"""Read native nested map arrays; report truncation without inventing values."""
import json, struct
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
source = ROOT/'KA-Website/artifacts/kingdom-adventures/public/world-assets/map/map_160_160.bin'
data = source.read_bytes()
offset = 0
def integer():
    global offset
    if offset + 4 > len(data):
        raise EOFError(offset)
    value = struct.unpack_from('>i', data, offset)[0]
    offset += 4
    return value
def array(depth):
    count = integer()
    assert 0 <= count <= 1000, (offset, count)
    return [array(depth - 1) if depth > 1 else integer() for _ in range(count)]
# ReadMapData 0x15b3690/36a4: ReadTripleIntArray then ReadDoubleIntArray.
chips = array(3)
height_start = offset
rows = integer()
heights, missing = [], []
for y in range(rows):
    count = integer()
    assert count == 160
    row = []
    for x in range(count):
        try:
            row.append(integer())
        except EOFError:
            row.append(None)
            missing.append([x, y])
    heights.append(row)
result = {
    'chipArrayShape': [len(chips), len(chips[0]), len(chips[0][0])],
    'heightSectionOffset': height_start,
    'heightShape': [len(heights), len(heights[0])],
    'missingHeightCells': missing,
    'fileComplete': not missing and offset == len(data),
    'nativeBaseHeightFormula': 'level > 0 ? 20*level-10 : 0',
    'portOrigins': [{'x': x, 'y': y, 'rawChipArray': chips[y][x], 'heightLevel': heights[y][x]}
                    for y in [37,39,101,103] for x in [153,155]],
    'scope': 'Base height levels only; chip heights and initialization replacements still need tracing.'
}
(ROOT/'RE-evidence/20260911-building/placement/native-map-probe.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
