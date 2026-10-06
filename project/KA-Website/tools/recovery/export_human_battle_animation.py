"""Decode the human battle animation clips the Recovered Replay plays.

PASS 15 COMMAND 15.2. Reads the **intact** originals in
`RE-evidence/20260912-combat/chara-animation-original/` (the `tmp/KA_assets` copies of these SEBs
are 4 bytes short and lose the last record of the last line) and writes the per-line key records the
replay's `Seb.GetSprite` lookup needs.

Clips, all direction UP:

  equipWaitUp   chara/equip_wait_up.seb    behaviour 3  -> base 12  (FighterSystem.EnterWaiting)
  attackSwordUp chara/attack_sword_up.seb behaviour 4  -> base 16  (EquipData.motion 4: club/staff/
                                                                     sword/bare-handed)
  attackMagicUp chara/attack_magic_up.seb behaviour 31 -> base 48  (skill record +0x4C = 31)
  attackGunUp   chara/attack_gun_up.seb   behaviour 10 -> base 56  (EquipData.motion 10)
  attackSpearUp chara/attack_spear_up.seb  behaviour 9  -> base 72  (EquipData.motion 9 = equip 72)
  attackBowUp   chara/attack_bow_up.seb    behaviour 11 -> base 68  (EquipData.motion 11 = equip 162)

The behaviour -> base values come from `humanAnimationSebBases` (AnimationSet.cctor 0x165ED2C); the
weapon motions come from `EquipData.motion` (+0x64) in the intact `xls-original/.../Equip.txt`.

Two hard checks run before anything is written:

  * every SEB parses to exactly its file length (a truncated source fails loudly);
  * the frame-0 record of every `equip_wait_up.seb` line equals the already-shipped frozen frame-0
    record in `src/game-data/human-battle-idle.json`, so this exporter cannot silently redefine the
    frame the production renderer was verified against.

    python KA-Website/tools/recovery/export_human_battle_animation.py
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path


def find_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so").is_file():
            return parent
    raise SystemExit("could not locate repo root")


ROOT = find_root()
INTACT = ROOT / "RE-evidence/20260912-combat/chara-animation-original"
EQUIP_TABLE = ROOT / "RE-evidence/20260911-treasure/xls-original/English.lproj/Equip.txt"
RESOURCE_TABLES = (
    ROOT / "RE-evidence/20260918-battle-visual-replay/human-native-recovery/HUMAN-RESOURCE-TABLES.json"
)
FROZEN = ROOT / "KA-Website/artifacts/kingdom-adventures/src/game-data/human-battle-idle.json"
CHARACTER_RULES = (
    ROOT / "KA-Website/artifacts/kingdom-adventures/public/character_sprites/character-rules.json"
)
OUT = ROOT / "KA-Website/artifacts/kingdom-adventures/src/game-data/human-battle-animation.json"

# Line -> resource group for the 14 SEB lines, per clip. These are *not* the same row: the attack
# clips carry the weapon on line 2 and the face on line 7 (EQUIP_WAIT has the weapon on line 12 and
# the face on line 6), so the row has to follow the clip. Each value below is the unique
# `HumanResourceSet` row that is consistent with BOTH demo characters and with the clip's own crops
# - `resolve_resource_row` proves and enforces that uniqueness at export time.
RESOURCE_ROWS = {
    "equipWaitUp": [18, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 25, 16],
    # `attack_sword_up` and `attack_magic_up` keep the EQUIP_WAIT order (weapon on line 12, face
    # on line 6); only the spear/bow/gun group moves the weapon to line 2 and the face to line 7.
    # Both rows are resolved (and asserted unique) at export time from the clip's own crops.
    "attackSwordUp": [18, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 25, 16],
    "attackMagicUp": [18, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 25, 16],
    "attackGunUp": [18, 25, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 16],
    "attackSpearUp": [18, 25, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 16],
    "attackBowUp": [18, 25, 25, 12, 15, 19, 16, 14, 17, 13, -2, -2, -2, 16],
}

# Resource group -> character_sprites part directory (each fixed by the group's own img.inf).
GROUP_DIR = {18: "shadow", 25: "weapon", 12: "body", 15: "foot", 16: "hand", 14: "face",
             19: "shoes", 17: "hair"}

# PASS 15 COMMAND 15.5: the reaction clips, one per direction. `chara/seb.inf` lists four entries
# per human SEB base and the fourth/first of them re-use the same file with the `,u` asset flag, which
# `Seb.Flip` (+0xB0) turns into a horizontal mirror (`Seb.GetSprite` inverts the sprite's reversU).
# So direction 0 = the first file, 1 = the second, 2 = the second file mirrored, 3 = the first
# mirrored -- exactly the table below, read from the original `seb.inf` rather than assumed.
REACTIONS = {
    "damage": (30, 188, "non-lethal and lethal hit reaction (EnterDamaging 0x1585E34)"),
    "knockDownSit": (28, 44, "knock-down entry pose (EnterKnockingDown 0x1586444)"),
    "knockDownDown": (7, 28, "static settled pose (UpdateKnockingDown 0x1586954 at timer 21)"),
}
WALKS = {
    "walk": (2, 8, "revival walk-back (EnterMoving 0x1584194, rate 2)"),
}
DIRECTION_NAMES = ["UP", "RIGHT", "DOWN", "LEFT"]


def human_resource_set_types() -> dict[int, int]:
    """`AnimationSet.GetHumanResourceSetType 0x165EA64` read from its own jump table.

    The function is `type = table[behaviour - 5]` for behaviour 5..38 and the default (0) outside,
    with the table stored at the recovered VA `0x772314`. Reading the table keeps this mapping
    derived from the frozen binary instead of transcribed.
    """
    from elftools.elf.elffile import ELFFile

    binary = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
    with binary.open("rb") as handle:
        elf = ELFFile(handle)
        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue
            start, size = segment["p_vaddr"], segment["p_filesz"]
            if start <= 0x772314 < start + size:
                handle.seek(segment["p_offset"] + (0x772314 - start))
                raw = handle.read(34 * 4)
                break
        else:
            raise SystemExit("jump table 0x772314 is not in a loadable segment")
    values = struct.unpack("<34I", raw)
    return {behaviour: values[behaviour - 5] for behaviour in range(5, 39)}


RESOURCE_SET_TYPES = human_resource_set_types()

# clip id -> (seb file, behaviour, human SEB base, why the replay needs it)
CLIPS = {
    "equipWaitUp": ("equip_wait_up.seb", 3, 12, "idle loop (FighterSystem.EnterWaiting 0x15840F0)"),
    "attackSwordUp": (
        "attack_sword_up.seb",
        4,
        16,
        "sword/club/staff/bare-handed attack (behaviour 4 -> base 16 = EquipData.motion 4, the"
        " most common weapon motion: equip 0/35/37)",
    ),
    "attackMagicUp": (
        "attack_magic_up.seb",
        31,
        48,
        "magic attack / magic-looking skill (behaviour 31 -> base 48; skill records carry +0x4C = 31,"
        " e.g. the healer's Heal Maddy skill 37)",
    ),
    "attackGunUp": (
        "attack_gun_up.seb",
        10,
        56,
        "gun attack (behaviour 10 -> base 56 = EquipData.motion 10)",
    ),
    "attackSpearUp": (
        "attack_spear_up.seb",
        9,
        72,
        "Guard D attack (EquipData.motion of equip 72)",
    ),
    "attackBowUp": ("attack_bow_up.seb", 11, 68, "Archer C attack (EquipData.motion of equip 162)"),
}

# The two demo weapons COMMAND 15.2 resolves, and the demo characters that carry them.
WEAPONS = {
    72: ("guard-d", "E/ Fisherman's Pike"),
    162: ("archer-c", "D/ Bow"),
}


def parse_seb(path: Path) -> dict:
    data = path.read_bytes()
    layers, maximum = struct.unpack_from(">Hh", data, 0)
    pos = 4
    frames = []
    per_line: list[int] = []
    for layer in range(layers):
        count, _reserved = struct.unpack_from(">hh", data, pos)
        pos += 4
        per_line.append(count)
        for n in range(count):
            if pos + n * 20 + 20 > len(data):
                raise SystemExit(
                    f"{path.name}: record {n} of line {layer} runs past the end of the file"
                )
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
    if pos != len(data):
        raise SystemExit(
            f"{path.name}: parsed {pos} of {len(data)} bytes - the SEB is truncated or uses an "
            "unsupported variant; refusing to export a silently shortened clip"
        )
    return {
        "layers": layers,
        "maxFrame": maximum,
        "frames": frames,
        "recordsPerLine": per_line,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def equip_motion(equip_id: int) -> dict:
    """`EquipData.motion` (+0x64) for one equipment row, from the intact original master table."""
    lines = EQUIP_TABLE.read_text(encoding="utf-8-sig").splitlines()
    if equip_id >= len(lines):
        raise SystemExit(f"equip {equip_id} is past the end of {EQUIP_TABLE.name}")
    columns = lines[equip_id].split("\t")
    # Column layout is the EquipData field order: id, name, category, type, sortId, group, rank,
    # res, img, motion, iconU, iconV, unlockEquip, ...
    return {
        "id": int(columns[0]),
        "name": columns[1],
        "type": int(columns[3]),
        "res": int(columns[7]),
        "img": int(columns[8]),
        "motion": int(columns[9]),
    }


def frozen_frame_zero() -> dict[int, dict]:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    return {line["line"]: line for line in frozen["lines"]}


def seb_inf_entries() -> list[str]:
    """The original `chara/seb.inf`: 100 positional clip names, then (id, name) record pairs."""
    path = INTACT / "seb.inf"
    text = "".join(
        chr(byte) if 32 <= byte < 127 else "\n" for byte in path.read_bytes()
    )
    tokens = [token for token in text.split("\n") if len(token) >= 3]
    table = [""] * 400
    for index in range(100):
        table[index] = tokens[index]
    for index in range(100, len(tokens) - 1, 2):
        table[int(tokens[index])] = tokens[index + 1]
    return table


def direction_variants(base: int, table: list[str]) -> list[dict]:
    """The four `base + direction` entries as (file, flip), read from `seb.inf`."""
    variants = []
    for direction in range(4):
        entry = table[base + direction]
        flip = entry.endswith(",u")
        file = entry[:-2] if flip else entry
        variants.append({"direction": direction, "name": DIRECTION_NAMES[direction],
                         "file": file, "flip": flip})
    # The original table only ever pairs the same asset with and without the flag, and entry 0 reuses
    # the asset of entry 3 while entry 1 reuses entry 2 (or vice versa); assert that shape so a
    # different resource set can never be silently misread as a four-direction clip.
    if variants[2]["file"] != variants[1]["file"] or not variants[2]["flip"]:
        raise SystemExit(f"base {base}: direction 2 is not the mirrored form of direction 1")
    if variants[3]["file"] != variants[0]["file"] or not variants[3]["flip"]:
        raise SystemExit(f"base {base}: direction 3 is not the mirrored form of direction 0")
    return variants


def resolve_part_sheet(rules: dict, dir_name: str, image: int):
    """The baked OPT entry for `image` inside one `character_sprites` part directory."""
    part = rules["dirs"].get(dir_name)
    if not part:
        return None
    name = part["inf"]["img"].get(str(image))
    if not name:
        return None
    # `sheetFor` in src/lib/human-battle-idle.ts tries the img.inf name first and its stem second.
    for key in (name, name.rsplit(".", 1)[0]):
        entry = part["opts"].get(key)
        if entry:
            return entry
    return None


def resolve_resource_row(
    clip: dict, demo_img_ids: list[list[int]], strict: bool = True
) -> tuple[list[list[int]], list[str]]:
    """Find every recovered `HumanResourceSet` row consistent with this clip and both demo inputs.

    A row is consistent when, for both demonstration characters, every line the SEB actually draws
    (texId >= 0 and imgIds[texId] >= 0) lands on a group whose part directory really holds that image
    id *and* whose OPT cell size equals the record's own crop size. The crop-size test is what pins
    the 60x60 weapon line and the 24x24 face line, which is what separates the attack rows from the
    EQUIP_WAIT row; the cell-size test alone still leaves several candidates, so the two together are
    what make the answer unique.
    """
    tables = json.loads(RESOURCE_TABLES.read_text(encoding="utf-8"))["tables"]
    rules = json.loads(CHARACTER_RULES.read_text(encoding="utf-8"))
    frame_zero = {record["line"]: record for record in clip["frames"] if record["frame"] == 0}
    hits: list[tuple[str, int, list[int]]] = []
    for table_name, table in tables.items():
        for row in table["rows"]:
            values = row["values"]
            consistent = True
            for img_ids in demo_img_ids:
                for line in range(14):
                    record = frame_zero.get(line)
                    if record is None:
                        consistent = False
                        break
                    if record["tex"] < 0:
                        continue
                    image = img_ids[record["tex"]] if record["tex"] < len(img_ids) else -2
                    if image < 0:
                        continue
                    dir_name = GROUP_DIR.get(values[line])
                    entry = resolve_part_sheet(rules, dir_name, image) if dir_name else None
                    if entry is None or (entry["cellW"], entry["cellH"]) != (record["w"], record["h"]):
                        consistent = False
                        break
                if not consistent:
                    break
            if consistent:
                hits.append((table_name, row["row"], values))
    distinct = sorted({tuple(values) for _, _, values in hits})
    labels = [f"{name} row {row}" for name, row, _ in hits]
    if strict and len(distinct) != 1:
        raise SystemExit(
            f"{clip['id']}: {len(distinct)} distinct consistent resource rows "
            f"({labels}) - the line -> group row is not determined"
        )
    return [list(values) for values in distinct], labels


def main() -> int:
    clips: dict[str, dict] = {}
    for clip_id, (seb_file, behaviour, base, why) in CLIPS.items():
        path = INTACT / seb_file
        if not path.is_file():
            raise SystemExit(f"missing intact original {path}")
        parsed = parse_seb(path)
        clips[clip_id] = {
            "id": clip_id,
            "group": "chara",
            "seb": f"chara/{seb_file}",
            "behaviour": behaviour,
            "humanBase": base,
            "direction": "UP",
            "why": why,
            "source": str(path.relative_to(ROOT)).replace("\\", "/"),
            "maxFrame": parsed["maxFrame"],
            "layers": parsed["layers"],
            "recordsPerLine": parsed["recordsPerLine"],
            "bytes": parsed["bytes"],
            "sha256": parsed["sha256"],
            "frames": parsed["frames"],
        }
        print(f"{clip_id}: {parsed['layers']} lines, {len(parsed['frames'])} records, "
              f"maxFrame {parsed['maxFrame']}, {parsed['bytes']} bytes")

    demo_img_ids = [character["imgIds"] for character in json.loads(FROZEN.read_text(encoding="utf-8"))["characters"]]
    for clip_id, clip in clips.items():
        candidates, labels = resolve_resource_row(clip, demo_img_ids)
        row = candidates[0]
        if row != RESOURCE_ROWS[clip_id]:
            raise SystemExit(
                f"{clip_id}: resolved resource row {row} does not match the expected "
                f"{RESOURCE_ROWS[clip_id]}"
            )
        clip["lineResourceGroups"] = {
            "row": row,
            "evidence": "SUPPORTED",
            "derivedFrom": labels,
            "note": (
                "The unique HumanResourceSet row consistent with this clip and both demo characters "
                "(every drawn line resolves to a part directory that holds imgIds[texId] and whose "
                "OPT cell size equals the record's crop). The attack clips move the weapon to line 2 "
                "and the face to line 7, so they do not use the EQUIP_WAIT row."
            ),
        }
        print(f"{clip_id}: line -> group row {row} (unique; {', '.join(labels)})")

    # Hard check: frame 0 of equip_wait_up must equal the frozen frame-0 records already shipped.
    frozen = frozen_frame_zero()
    wait = clips["equipWaitUp"]
    mismatched = []
    for line in frozen:
        record = next(
            (f for f in wait["frames"] if f["line"] == line and f["frame"] == 0),
            None,
        )
        if record is None:
            mismatched.append(f"line {line}: no frame-0 record in the intact SEB")
            continue
        # `human-battle-idle.json` names the SEB record's texture slot `texId`; the SEB record itself
        # calls it `tex` (Seb.SP_TEX). Same field.
        for seb_key, frozen_key in (
            ("tex", "texId"),
            ("u", "u"),
            ("v", "v"),
            ("w", "w"),
            ("h", "h"),
            ("transX", "transX"),
            ("transY", "transY"),
            ("reversU", "reversU"),
            ("reversV", "reversV"),
        ):
            if record[seb_key] != frozen[line][frozen_key]:
                mismatched.append(
                    f"line {line} {seb_key}: seb {record[seb_key]} != frozen {frozen[line][frozen_key]}"
                )
    if mismatched:
        raise SystemExit("frame-0 mismatch against the frozen idle data:\n  " + "\n  ".join(mismatched))
    print(f"frame-0 cross-check: all {len(frozen)} lines match src/game-data/human-battle-idle.json")

    weapons = {str(equip_id): equip_motion(equip_id) for equip_id in WEAPONS}
    character_weapons: dict[str, dict] = {}
    for equip_id, (character, _) in WEAPONS.items():
        motion = weapons[str(equip_id)]["motion"]
        if motion not in {9, 11}:
            raise SystemExit(f"equip {equip_id} motion {motion} is outside the two resolved values")
        clip_id = "attackSpearUp" if motion == 9 else "attackBowUp"
        character_weapons[character] = {
            "equipId": equip_id,
            "equipName": weapons[str(equip_id)]["name"],
            "equipType": weapons[str(equip_id)]["type"],
            "motion": motion,
            "clipId": clip_id,
            "source": "EquipData.motion (+0x64) in RE-evidence/20260911-treasure/xls-original/"
                      "English.lproj/Equip.txt, read by FighterSystem.EnterAttacking 0x15854C0",
        }
        print(f"equip {equip_id} {weapons[str(equip_id)]['name']}: motion {motion} "
              f"-> {clip_id} ({character})")

    # PASS 15 COMMAND 15.5 reaction clips: four direction variants each, decoded from the intact SEBs.
    table = seb_inf_entries()
    reactions: dict[str, dict] = {}
    for reaction_id, (behaviour, base, why) in REACTIONS.items():
        variants = direction_variants(base, table)
        decoded: dict[str, dict] = {}
        for variant in variants:
            if variant["file"] not in decoded:
                path = INTACT / variant["file"]
                if not path.is_file():
                    raise SystemExit(f"missing intact original {path}")
                decoded[variant["file"]] = parse_seb(path)
        variants_out = []
        for variant in variants:
            parsed = decoded[variant["file"]]
            variants_out.append({
                "direction": variant["direction"],
                "directionName": variant["name"],
                "sebId": base + variant["direction"],
                "seb": f"chara/{variant['file']}",
                "source": f"RE-evidence/20260912-combat/chara-animation-original/{variant['file']}",
                "flip": variant["flip"],
                "maxFrame": parsed["maxFrame"],
                "layers": parsed["layers"],
                "bytes": parsed["bytes"],
                "sha256": parsed["sha256"],
                "frames": parsed["frames"],
            })
        reactions[reaction_id] = {
            "id": reaction_id,
            "behaviour": behaviour,
            "humanBase": base,
            "why": why,
            # The type index `AnimationSet.GetHumanResourceSetType 0x165EA64` returns for this
            # behaviour; `SetHumanImgs 0x165EA88` uses it as the row index into the resource-set
            # arrays. The row *contents* this project ships are the geometry-consistent ones the
            # exporter proves below, because the recovered literal -> row-index pairing is SUPPORTED.
            "resourceSetType": RESOURCE_SET_TYPES[behaviour],
            "lineResourceGroups": None,  # filled in below
            "directions": variants_out,
        }
        print(f"reaction {reaction_id:14} behaviour {behaviour:2} base {base:3} "
              f"type {RESOURCE_SET_TYPES[behaviour]:2} "
              + " | ".join(f"d{v['direction']}={v['file']}{',u' if v['flip'] else ''}"
                           for v in variants))

    # Line -> group row per reaction clip, by the same uniqueness proof the attack clips used. The
    # settled `down_right.seb` draws only a shadow and a face, so several recovered rows survive; the
    # row the other human clips use is taken when it is among them, and the candidate count is kept.
    for reaction in reactions.values():
        up = next(v for v in reaction["directions"] if v["direction"] == 0)
        candidates, labels = resolve_resource_row(
            {"id": reaction["id"], "frames": up["frames"]}, demo_img_ids, strict=False
        )
        chosen = RESOURCE_ROWS["equipWaitUp"]
        if chosen not in candidates:
            raise SystemExit(f"{reaction['id']}: the EQUIP_WAIT row {chosen} is not among the "
                             f"geometry-consistent rows {candidates}")
        reaction["lineResourceGroups"] = {
            "row": chosen,
            "evidence": "SUPPORTED",
            "derivedFrom": labels,
            "candidateCount": len(candidates),
            "note": ("unique HumanResourceSet row consistent with both demo characters"
                     if len(candidates) == 1 else
                     "several recovered rows are geometrically consistent for this clip; the row "
                     "the other human clips use is taken (it maps this clip's drawn lines to shadow "
                     "and face correctly)"),
        }
        print(f"  {reaction['id']}: row candidates={len(candidates)} -> {chosen}")

    # PASS 15 COMMAND 15.11 revival clips: the four behavior-2 direction variants. `EnterMoving`
    # requests behavior 2 and sets Animation.rate = 2; the original `seb.inf` gives the same
    # UP/RIGHT + `,u` shape as the reaction clips.
    walks: dict[str, dict] = {}
    for walk_id, (behaviour, base, why) in WALKS.items():
        variants = direction_variants(base, table)
        decoded: dict[str, dict] = {}
        for variant in variants:
            if variant["file"] not in decoded:
                path = INTACT / variant["file"]
                if not path.is_file():
                    raise SystemExit(f"missing intact original {path}")
                decoded[variant["file"]] = parse_seb(path)
        variants_out = []
        for variant in variants:
            parsed = decoded[variant["file"]]
            variants_out.append({
                "direction": variant["direction"],
                "directionName": variant["name"],
                "sebId": base + variant["direction"],
                "seb": f"chara/{variant['file']}",
                "source": f"RE-evidence/20260912-combat/chara-animation-original/{variant['file']}",
                "flip": variant["flip"],
                "maxFrame": parsed["maxFrame"],
                "layers": parsed["layers"],
                "bytes": parsed["bytes"],
                "sha256": parsed["sha256"],
                "frames": parsed["frames"],
            })
        up = next(v for v in variants_out if v["direction"] == 0)
        candidates, labels = resolve_resource_row(
            {"id": walk_id, "frames": up["frames"]}, demo_img_ids, strict=False
        )
        chosen = RESOURCE_ROWS["equipWaitUp"]
        if chosen not in candidates:
            raise SystemExit(
                f"{walk_id}: the EQUIP_WAIT row {chosen} is not among the geometry-consistent "
                f"rows {candidates}"
            )
        walks[walk_id] = {
            "id": walk_id,
            "behaviour": behaviour,
            "humanBase": base,
            "why": why,
            "animationRate": 2,
            "lineResourceGroups": {
                "row": chosen,
                "evidence": "SUPPORTED",
                "derivedFrom": labels,
                "candidateCount": len(candidates),
                "note": (
                    "unique HumanResourceSet row consistent with both demo characters"
                    if len(candidates) == 1 else
                    "several recovered rows are geometrically consistent for this clip; the row "
                    "the other human clips use is taken"
                ),
            },
            "directions": variants_out,
        }
        print(f"walk {walk_id}: behaviour {behaviour} base {base} rate 2 "
              + " | ".join(f"d{v['direction']}={v['seb'].split('/')[-1]}{',u' if v['flip'] else ''}"
                           for v in variants_out))
        print(f"  {walk_id}: row candidates={len(candidates)} -> {chosen}")

    document = {
        "note": (
            "Decoded original human battle clips for the Recovered Replay, all direction UP. Frames "
            "are 1:1 with the intact original SEBs; no timing is invented here. Each record carries "
            "its SEB line index because Seb.GetSprite(frame, line) 0x2351CA0 is a per-line lookup. "
            "The clip a fighter plays is chosen by behaviour (EnterWaiting 0x15840F0 = 3) or, for "
            "attacks, by the equipped weapon's EquipData.motion (+0x64) read by EnterAttacking "
            "0x15854C0; the behaviour indexes humanAnimationSebBases (AnimationSet.cctor 0x165ED2C)."
        ),
        "attackWindow": {
            "updates": 20,
            "hitUpdate": 11,
            "frameMs": 50,
            "evidence": "CONFIRMED",
            "source": (
                "FighterSystem.UpdateAttacking 0x1585568 compares the blackboard state timer with 11 "
                "(cmp w0,#0xb -> BattleHelper.Attack 0x168D1E8) and leaves the state at 20 "
                "(cmp w0,#0x14 -> DecideNextState 0x15835A8 + ChangeState 0x1583B48); "
                "FighterSystem.ChangeState 0x1583B48 resets that timer to 0 and "
                "FighterSystem.Update 0x1583CF8 increments it once per rendered battle update. At "
                "the recovered 20 updates/s the window is 20 x 50 ms = 1000 ms and the hit lands at "
                "update 11 = 550 ms."
            ),
        },
        "weapons": weapons,
        "characterWeapons": character_weapons,
        "reactions": reactions,
        "walks": walks,
        "clips": clips,
    }
    OUT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
