"""Export town-placement rules from original MapChip flags, not facility names."""
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "artifacts/kingdom-adventures"
def table(name):
    rows = (APP / "tmp/KA_assets/xls/English.lproj" / (name + ".txt")).read_text(encoding="utf-8-sig").splitlines()
    return {int(row[0]): row for line in rows if (row := line.split("\t"))[0].isdigit()}

chips = table('MapChip')
assets = json.loads((APP/'src/game-data/builder-assets.json').read_text())
records = {}
for fid, asset in assets['facilities'].items():
    if not fid.isdigit(): continue
    row = chips[asset['chipId']]
    assert row[15] == '1' and int(row[16]) == int(fid)
    flags, category = int(row[-1]), int(row[2])
    # CheckPlace 0x1508744..8780; category exceptions 0x1508818..8860.
    rule = 'outside' if category in (35, 52) else 'anywhere' if flags & 65536 else 'inside'
    records[fid] = dict(chipId=int(row[0]), flags=flags, category=category, townCoverage=rule)
output = dict(source='Original MapChip.txt; ChipPlaceSystem.CheckPlace 0x1508238', facilities=records)
(APP/'src/game-data/builder-placement-rules.json').write_text(json.dumps(output, indent=2)+'\n')
print(f'Exported {len(records)} original town-placement rules')
for rule in ['anywhere','outside']:
    print(rule, [(fid, chips[r['chipId']][8]) for fid,r in records.items() if r['townCoverage']==rule])
