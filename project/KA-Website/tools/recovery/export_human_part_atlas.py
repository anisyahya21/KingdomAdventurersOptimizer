"""Record the pixel size of every human part sheet that has no baked OPT entry.

Why this file exists: the baked `character_sprites/character-rules.json` OPT table is built from the
original `KA_assets/<dir>/<file>.opt` files, and a handful of part images ship **without** an `.opt`
in the original assets (the Beast Tamer rank-D `m_face_32_0.png`, the `weapon_10/20/28_2..4/29`
families, ...). Those files are not packed sprites at all: they are plain, untrimmed cell atlases
(`m_face_32_0.png` = 24x96 = four 24x24 direction cells stacked; `weapon_28_3.png` = 240x60 = four
60x60 cells side by side), so their OPT is the identity grid and the only unknown is the PNG size.

This exporter records exactly that: the pixel size of every part PNG whose basename has no OPT entry.
`humanIdleResolution` then derives the identity cell grid from the SEB record's own crop
(cellW = crop.w, cellH = crop.h, cols = pngW / crop.w, rows = pngH / crop.h, slot (row, col) =
src (col * cellW, row * cellH, cellW, cellH) + dest (0, 0)) - and only when the PNG size really is an
exact multiple of the crop, so a tightly packed sheet can never be misread as an atlas.

    python KA-Website/tools/recovery/export_human_part_atlas.py
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


def find_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so").is_file():
            return parent
    raise SystemExit("could not locate repo root")


ROOT = find_root()
SPRITES = ROOT / "KA-Website/artifacts/kingdom-adventures/public/character_sprites"
RULES = SPRITES / "character-rules.json"
OUT = ROOT / "KA-Website/artifacts/kingdom-adventures/src/game-data/human-part-atlas.json"


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path.name}: not a PNG")
    return struct.unpack(">II", header[16:24])


def main() -> int:
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    sheets: dict[str, dict[str, list[int]]] = {}
    for dir_name, part in sorted(rules["dirs"].items()):
        baked = set(part.get("opts", {}).keys())
        missing = {}
        for png in sorted((SPRITES / dir_name).glob("*.png")):
            if png.stem in baked:
                continue
            missing[png.name] = list(png_size(png))
        if missing:
            sheets[dir_name] = missing
    document = {
        "note": (
            "Pixel size of every human part sheet that has no baked OPT entry in "
            "character-rules.json. These originals ship without an .opt because they are untrimmed "
            "cell atlases (one SEB cell per direction), so their OPT is the identity grid; the "
            "resolution rule that turns this size into cells lives in humanIdleResolution."
        ),
        "source": "public/character_sprites/<dir>/<file>.png IHDR, compared against the baked "
                  "character-rules.json opts keys",
        "rule": "cellW/cellH = the SEB record's crop (w,h); the sheet is a cell grid iff "
                "pngW % cellW == 0 and pngH % cellH == 0; slot(row,col) = "
                "src(col*cellW, row*cellH, cellW, cellH), dest(0,0)",
        "sheets": sheets,
    }
    OUT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for dir_name, missing in sheets.items():
        print(f"{dir_name}: {len(missing)} unbaked sheet(s)")
        for name, size in missing.items():
            print(f"   {name} {size[0]}x{size[1]}")
    print("wrote", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
