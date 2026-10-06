"""Export decoded SEB frame records for the battle fighter animation path.

Read-only over the extracted APK assets: parses the original SEB headers, resolves
each frame's texture id through the group's img.inf, copies the referenced PNGs
into the website's public folder and writes a frame manifest the replay can play.

The monster clips (the ones the battle replay's monster body path uses) are read from the
*intact* originals in `RE-evidence/20260911-treasure/monster-original/`. The working copies
under `tmp/KA_assets/monster` are 4 bytes short (e.g. monster_s_wait_up.seb 168 vs 172), which
silently dropped the last record of the second SEB line - the frame-19 / frame-9 keyframe that
returns the body to cell u=0. The parse now asserts exact byte consumption, so a truncated
source fails loudly instead of exporting a shorter clip. Human clips still come from the
legacy tree unchanged (they show the same 4-byte shortfall; that is a separate task).

Each record also carries its `line` (the SEB layer index) because `Seb.GetSprite(frame, line)`
is a per-line lookup - the shadow line and the body line have their own key lists and crops.

    python KA-Website/tools/recovery/export_battle_animation.py
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "KA-Website/artifacts/kingdom-adventures/tmp/KA_assets"
PUBLIC = ROOT / "KA-Website/artifacts/kingdom-adventures/public/battle-assets/anim"
MANIFEST = ROOT / "KA-Website/artifacts/kingdom-adventures/src/game-data/battle-animation.json"
# Intact EFB/SEB originals (verified copies; the working tree's monster copies lose 4 bytes).
INTACT_MONSTER = ROOT / "RE-evidence/20260911-treasure/monster-original"

# Recovered clip list. Human base ids come from humanAnimationSebBases
# (skill-combat-constants.json); monster bases come from AnimationSet.cctor
# (0 = wait group, 16 = attack group, +4 for size 3).
CLIPS = {
    "monsterWait": ["monster", "monster_s_wait_up.seb", INTACT_MONSTER],
    "monsterWaitLeft": ["monster", "monster_s_wait_right.seb", INTACT_MONSTER],
    "monsterAttack": ["monster", "monster_s_attack_up.seb", INTACT_MONSTER],
    "monsterAttackLeft": ["monster", "monster_s_attack_right.seb", INTACT_MONSTER],
    "humanWait": ["chara", "equip_wait_up.seb", None],
    "humanWalk": ["chara", "equip_walk_up.seb", None],
    "humanHit": ["chara", "equip_damege2_up.seb", None],
    "humanKnockDown": ["chara", "equip_sit_up.seb", None],
}


def read_inf(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "\t" not in line:
            continue
        index, rest = line.split("\t", 1)
        out[int(index)] = rest.split(",")[0].strip()
    return out


def parse_seb(path: Path, strict: bool) -> dict:
    data = path.read_bytes()
    layers, maximum = struct.unpack_from(">Hh", data, 0)
    pos = 4
    frames = []
    dropped = 0
    for layer in range(layers):
        count, _reserved = struct.unpack_from(">hh", data, pos)
        pos += 4
        for n in range(count):
            if pos + n * 20 + 20 > len(data):
                if strict:
                    raise SystemExit(
                        f"{path.name}: record {n} of line {layer} runs past the end of the file"
                    )
                dropped += 1
                continue
            rec = struct.unpack_from(">10h", data, pos + n * 20)
            frames.append(
                {
                    "line": layer,
                    "frame": rec[0],
                    "tex": rec[1],
                    "u": rec[2],
                    "v": rec[3],
                    "w": rec[4],
                    "h": rec[5],
                    "transX": rec[6],
                    "transY": rec[7],
                    "reversU": rec[8],
                    "reversV": rec[9],
                }
            )
        pos += count * 20
    if pos != len(data) and strict:
        raise SystemExit(
            f"{path.name}: parsed {pos} of {len(data)} bytes - the SEB is truncated or uses an "
            "unsupported variant; refusing to export a silently shortened clip"
        )
    return {
        "layers": layers,
        "maxFrame": maximum,
        "frames": frames,
        "dropped": dropped,
        "consumed": pos,
        "bytes": len(data),
    }


def main() -> int:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    images: dict[str, dict[int, str]] = {group: read_inf(ASSETS / group / "img.inf") for group in ("monster", "chara")}
    previous: dict[str, object] = {}
    if MANIFEST.exists():
        try:
            previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    manifest: dict[str, object] = {
        "note": (
            "Decoded original SEB frame records for the battle fighter animation path. "
            "Frames are 1:1 with the original files; no timing is invented here. Each record "
            "carries its SEB line index (line 0 = shadow, line 1 = body) because "
            "Seb.GetSprite(frame, line) is a per-line lookup. Monster clips are decoded from the "
            "intact originals in RE-evidence/20260911-treasure/monster-original; human clips "
            "still come from the legacy extracted tree."
        ),
        "clips": {},
        "images": {},
        "copied": [],
        "sources": {},
    }
    copied: set[str] = set()
    for clip, (group, seb_file, intact_dir) in CLIPS.items():
        seb_path = (intact_dir if intact_dir else ASSETS / group) / seb_file
        if not seb_path.exists():
            print(f"missing {seb_path}")
            continue
        parsed = parse_seb(seb_path, strict=intact_dir is not None)
        tex_names = {}
        for frame in parsed["frames"]:
            tex_id = frame["tex"]
            if tex_id < 0:
                continue
            name = images[group].get(tex_id)
            if not name:
                continue
            tex_names[tex_id] = name
            if name not in copied:
                src = ASSETS / group / name
                if src.exists():
                    shutil.copyfile(src, PUBLIC / name)
                    copied.add(name)
        manifest["clips"][clip] = {
            "group": group,
            "seb": seb_file,
            "maxFrame": parsed["maxFrame"],
            "layers": parsed["layers"],
            "textures": {str(k): v for k, v in sorted(tex_names.items())},
            "frames": [f for f in parsed["frames"] if f["tex"] in tex_names],
        }
        line_counts = {
            str(line): sum(1 for f in parsed["frames"] if f["line"] == line)
            for line in range(parsed["layers"])
        }
        manifest["sources"][clip] = {
            "path": str(seb_path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": seb_path.stat().st_size,
            "intact": intact_dir is not None,
            "lines": line_counts,
            "droppedRecords": parsed["dropped"],
        }
        print(
            f"{clip}: {len(parsed['frames'])} records over {parsed['layers']} lines "
            f"{line_counts} from {seb_path.parent.name}/{seb_file} "
            f"({seb_path.stat().st_size} bytes, dropped {parsed['dropped']} trailing record(s)), "
            f"textures {sorted(set(tex_names.values()))}"
        )
    manifest["copied"] = sorted(copied)
    # Keys owned by other exporters must survive this regeneration.
    if previous.get("humanCompositeWait"):
        manifest["humanCompositeWait"] = previous["humanCompositeWait"]
        print("preserved humanCompositeWait from the existing manifest")
    if previous.get("images"):
        manifest["images"] = previous["images"]
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("wrote", MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
