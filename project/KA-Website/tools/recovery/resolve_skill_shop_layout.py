"""Evaluate table joins and SetupHouse coordinates; no website/runtime claim."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / 'KA-Website/artifacts/kingdom-adventures/tmp/KA_assets/xls/English.lproj'
OUT = ROOT / 'RE-evidence/20260911-building/placement'

def table(name):
    return {int(a[0]): a for line in (TABLES / name).read_text(encoding='utf-8-sig').splitlines()
            if (a := line.split('\t'))[0].isdigit()}

chips, houses, walls = table('MapChip.txt'), table('House.txt'), table('Wall.txt')
row = houses[6]
# HouseData.Load 0x1629e90: four scalars, costs array, two scalars, four arrays.
k = 4
k += 1 + int(row[k])
k += 2
for _ in range(4):
    k += 1 + int(row[k])
fields = dict(zip(['res', 'img', 'bed', 'workbench', 'register', 'shelves', 'storage', 'floor', 'fence'],
                  map(int, row[k:k+9])))
assert [fields[x] for x in ['bed', 'workbench', 'register', 'shelves', 'storage']] == [132,156,143,145,142]

layouts = []
for size, plot in zip(['S','M','L','XL'], [29,30,31,32]):
    width, height = map(int, chips[plot][22:24])
    pieces = []
    for role in ['bed','workbench','shelves','storage','register']:
        chip = chips[fields[role]]
        w, h = map(int, chip[22:24])
        # SetupHouse 0x15a683c. Coordinates relative to LandComponent.xi,yi.
        xy = {'bed': (1,1), 'workbench': (width-1-w,1),
              'shelves': (2-w,height-1-h), 'storage': (width-1-w,height-1-h),
              'register': (1+(width-2)//2-(w+1)//2,1+(height-2)//2-(h+1)//2)}[role]
        x,y = xy
        assert 1 <= x and x+w <= width-1 and 1 <= y and y+h <= height-1
        occupied = {(xx,yy) for yy in range(y,y+h) for xx in range(x,x+w)}
        for previous in pieces:
            assert not occupied.intersection(map(tuple, previous['cells']))
        pieces.append(dict(role=role,chipId=int(chip[0]),x=x,y=y,width=w,height=h,
                           cells=sorted(occupied)))
    layouts.append(dict(size=size,plotChipId=plot,width=width,height=height,pieces=pieces))

result = dict(houseId=6,fields=fields,layouts=layouts,
              fence=dict(wallId=fields['fence'],row=list(map(int,walls[fields['fence']]))),
              native=dict(layout='SetupHouse 0x15a683c, branches 0x15a6978..0x15a6fc0',
                          fence='PlaceLandFence 0x157f4dc; GetFenceImgId 0x157fa74',
                          setupTrigger='OnPlaceEntranceChip 0x15a5d54'),
              scope='Native-derived placement coordinates evaluated against four real plot rows; geometric checks only, not original-game render comparison.',
              unresolved=['Floor placement lifecycle and final ground heights',
                          'Sprite crops and per-component SEB layer selection',
                          'Door replacement lifecycle and image selection at each entrance'])
(OUT / 'skill-shop-layout.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'sizes':[(a['size'],a['width'],a['height']) for a in layouts],
                  'piecesChecked':sum(len(a['pieces']) for a in layouts),'checks':'pass'}))
