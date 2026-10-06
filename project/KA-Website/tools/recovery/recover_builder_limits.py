"""Recover supported finite Chaos Stone supply from original Survey/Area rewards.
The seven-copy planning limit is normal-source supply, not Facility.maxStock (999).
Unattributed delivery box 938 is not an additional acquisition route.
"""
import csv, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'artifacts/kingdom-adventures'
def table(name):
    return {int(r[0]):r for line in (APP/f'tmp/KA_assets/xls/English.lproj/{name}.txt').read_text(encoding='utf-8-sig').splitlines() if (r:=line.split('\t'))[0].isdigit()}
surveys=table('Survey');areas=table('Area');treasures=table('Treasure')
# Original Treasure reward furniture slot: mapChipId, percent, quantity.
assert treasures[765][31:34]==['221','100','1']
assert treasures[352][31:34]==['221','100','1']
rows=[r for r in surveys.values() if r[8]=='765']
assert [int(r[0]) for r in rows]==[10,21,32]
assert all(r[6]=='2' for r in rows)
# Cross-check original Area against the existing normalized lookup.
lookup=list(csv.DictReader((ROOT/'data/sheet-research/raw-copies/KA GameData - Area_lookup.csv').open(encoding='utf-8-sig')))
areaRows=[r for r in lookup if r['treasureId']=='352']
assert len(areaRows)==1 and areaRows[0]['id']=='94'
assert areas[94][12]=='352'
sources=[dict(kind='survey',id=int(r[0]),treasureId=765,quantity=int(r[6])) for r in rows]
sources.append(dict(kind='area',id=94,treasureId=352,quantity=1))
limit=sum(s['quantity'] for s in sources)
assert limit==7
output={'191':dict(maxPlaced=limit,basis='Supported normal acquisition sources: three surveys with two rewards each, plus one area-clear reward.',sources=sources)}
(APP/'src/game-data/builder-acquisition-limits.json').write_text(json.dumps(output,indent=2)+'\n')
print('Chaos Stone: 6 survey rewards + 1 area reward =',limit)
