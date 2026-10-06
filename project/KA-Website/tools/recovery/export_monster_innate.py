"""Export the canonical per-species first (innate) monster skill.

Reads the ORIGINAL Monster table through the canonical runtime reader
(`combat_runtime_data.table('Monster')`, packaged `tables/monster.json` first, otherwise
`RE-evidence/20260911-treasure/xls-original/English.lproj/Monster.txt`) and copies the
`skillId` field exactly as the existing recovery reader `recover_special_combat.py` does:

    parameters = array(row, 7, depth=2)   # 7 parameter curve blocks
    exps       = array(row, pos, depth=1)
    sale       = array(row, pos, depth=2) # sale-condition block
    skillId    = int(row[pos + 14])       # offset inside the trailing 19-field tail

The derivation is deterministic and cross-checked against every monster the canonical
combat evidence already pins: `encounters.json` monsters[].skillId (27 rows).  Nothing is
inferred and nothing is guessed: a row with `-1` declares no innate skill.

Run from the workspace root with .venv/Scripts/python.exe.  Writes
`KA-Website/artifacts/kingdom-adventures/src/game-data/native-monster-innate.json`.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'KA-Website/artifacts/kingdom-adventures'
RUNTIME = APP / 'api/_battle_runtime'
OUT = APP / 'src/game-data/native-monster-innate.json'
SOURCE_ROOT = ROOT / 'RE-evidence/20260911-treasure/xls-original/English.lproj'

sys.path.insert(0, str(RUNTIME))
from combat_runtime_data import load_data, table  # noqa: E402


def read_array(row, pos, depth):
    """The exact reader `recover_special_combat.py` uses: a count field, then that many entries."""
    count = int(row[pos])
    pos += 1
    assert count >= 0
    values = []
    for _ in range(count):
        if depth == 1:
            values.append(int(row[pos]))
            pos += 1
        else:
            value, pos = read_array(row, pos, depth - 1)
            values.append(value)
    return values, pos


def skill_id(row):
    """Monster.skillId, the skill loaded into the entity's initial skill list."""
    parameters, pos = read_array(row, 7, 2)
    exps, pos = read_array(row, pos, 1)
    sale, pos = read_array(row, pos, 2)
    assert pos + 19 == len(row), (row[0], pos, len(row))
    assert len(parameters) == 7, (row[0], len(parameters))
    return int(row[pos + 14])


def main():
    monsters = table('Monster')
    profiles = load_data('weapon-skill-profiles.json')
    skills = {int(entry['id']) for entry in profiles['skills']}
    encounters = load_data('encounters.json')
    known = {int(entry['id']): int(entry['skillId']) for entry in encounters['monsters']}

    rows = []
    mismatches = []
    for monster_id in sorted(monsters):
        row = monsters[monster_id]
        sid = skill_id(row)
        if monster_id in known and known[monster_id] != sid:
            mismatches.append(dict(id=monster_id, derived=sid, evidence=known[monster_id]))
        rows.append(dict(id=monster_id, name=row[1], skillId=sid if sid >= 0 else None))
    assert not mismatches, mismatches

    unknown = sorted({entry['skillId'] for entry in rows if entry['skillId'] is not None} - skills)
    assert not unknown, unknown
    assert all(entry['skillId'] == known[entry['id']] for entry in rows if entry['id'] in known)

    source_file = SOURCE_ROOT / 'Monster.txt'
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    payload = {
        '$comment': (
            'Canonical per-species first (innate) monster skill copied from the original Monster '
            'table column read by tools/recovery/recover_special_combat.py (skillId = row[pos+14] '
            'after the parameters/exps/sale blocks); not inferred. skillId null means the row '
            'declares no innate skill (-1). This is the skill native CreateMonster 0x147780c loads '
            'into the entity initial skill list, so it must stay FIRST in the exported pet skills.'
        ),
        'source': {
            'table': 'Monster',
            'route': 'RE-evidence/20260911-treasure/xls-original/English.lproj/Monster.txt',
            'sha256': digest,
            'rowCount': len(rows),
            'reader': 'KA-Website/tools/recovery/export_monster_innate.py',
        },
        'crossCheck': {
            'against': 'RE-evidence/20260912-combat/encounters.json monsters[].skillId',
            'rows': len(known),
            'mismatches': mismatches,
        },
        'monsters': rows,
        'limits': [
            'Only the single first skill column is recovered; monster additional skill slots and '
            'their activation rules stay unmodelled.',
            'skillId null is a table value (-1: no innate skill), never a missing lookup.',
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(rows=len(rows), withInnate=sum(1 for r in rows if r['skillId'] is not None),
                          crossChecked=len(known), distinctInnateSkills=len(
                              {r['skillId'] for r in rows if r['skillId'] is not None}))))
    print(OUT)


if __name__ == '__main__':
    main()
