"""Evaluate the traced fresh-map branches; no website mutation or runtime claim."""
import hashlib, json, struct
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'RE-evidence/20260911-building/placement'
ASSETS = ROOT/'KA-Website/artifacts/kingdom-adventures/tmp/KA_assets'
layout = json.loads((OUT/'native-area-layout.json').read_text())
metadata = (ROOT/'RE-evidence/G2.1/39257e72291d/inputs'/layout['source']).read_bytes()
payload = metadata[layout['fileOffset']:layout['fileOffset']+400]
assert hashlib.sha256(payload).hexdigest() == layout['sha256']
assert list(struct.unpack('<100i', payload)) == layout['areaIds']
def table(name):
    return {int(a[0]): a for l in (ASSETS/'xls/English.lproj'/name).read_text(encoding='utf-8-sig').splitlines()
            if (a := l.split('\t')) and a[0].isdigit()}
chips, areas = table('MapChip.txt'), table('Area.txt')
ground = [a for a in chips.values() if int(a[1]) == 10 and int(a[2]) == 0]
assert {int(a[17]) for a in ground} == {3}
b = (ASSETS/'map/map_160_160.map').read_bytes(); offset = 0
def integer():
    global offset
    n = struct.unpack_from('>i', b, offset)[0]; offset += 4; return n
def array(depth):
    return [array(depth-1) if depth > 1 else integer() for _ in range(integer())]
cells, heights = array(3), array(2)
assert offset == len(b)
results = []
for base_y in [36,100]:
    for chip_id, dx, dy in [(67,0,0),(70,2,0),(68,0,2),(69,2,2)]:
        x, y = 153+dx, base_y+1+dy
        area_id = layout['areaIds'][(y//16)*10+x//16]
        state = int(areas[area_id][1]); assert state == 2
        # CreateMapChips 0x15b1a64..1c34 passes cell[4] as dataId.
        terrain_id = cells[y][x][4]
        is_water = int(chips[terrain_id][2]) == 7
        assert heights[y][x] == 0
        # Nonwater cells do not enter the water-child creation branch.
        # FillWater changes water to ground with destroyChildren=true.
        height = 3 if is_water else int(chips[terrain_id][17])
        results.append(dict(portY=base_y, chipId=chip_id, x=x, y=y, areaId=area_id,
                            initialAreaState=state, initialTerrainId=terrain_id,
                            waterReplaced=is_water, childHeightContribution=0, placementY=height))
assert [r['placementY'] for r in results] == [2,3,3,3,3,3,3,3]
result = {'scope':'Static reconstruction of normal fresh initialization, before area opening or later changes',
          'results':results, 'notClaimed':'Saved worlds, later area transitions, or observed original-game pixels'}
(OUT/'initial-port-heights.json').write_text(json.dumps(result, indent=2))
print(json.dumps({'placementHeights':[r['placementY'] for r in results], 'areaIds':sorted({r['areaId'] for r in results}),
                  'nativeAreaLayoutHashVerified':True}))
