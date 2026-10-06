"""Export static catalog icons from recovered OPT/SEB assets.

Run from any directory with Pillow installed. Geometry follows the focused
20260911-building/placement renderers. These are isolated, flat-ground catalog
illustrations, not saved-game states or full native runtime validation.
"""
from pathlib import Path
import csv
import json
import re
import shutil
import struct
from functools import lru_cache
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'artifacts/kingdom-adventures'
EVIDENCE = ROOT.parent / 'RE-evidence/20260911-building/placement'
TABLES = APP / 'tmp/KA_assets/xls/English.lproj'
OUT = ROOT / 'website_icons/facilities_assembled'
PUBLIC = APP / 'public/website_icons/facilities_assembled'


def table(name):
    return {int(a[0]): a for line in (TABLES / (name + '.txt')).read_text(encoding='utf-8-sig').splitlines()
            if (a := line.split('\t'))[0].isdigit()}


def folder_path(folder):
    return EVIDENCE / (folder + '-original')


@lru_cache(None)
def binding(folder, kind, index):
    rows = (folder_path(folder) / (kind + '.inf')).read_text(encoding='utf-8-sig').splitlines()
    name = dict(line.split('\t', 1) for line in rows)[str(index)].split(',')[0]
    return name + '.seb' if kind == 'seb' and not name.endswith('.seb') else name


@lru_cache(None)
def logical(folder, filename):
    path = folder_path(folder) / filename
    image = Image.open(path).convert('RGBA')
    opt = path.with_suffix('.opt')
    if not opt.exists():
        return image
    raw = opt.read_bytes()
    cw, ch, nx, ny = struct.unpack_from('>BBbb', raw)
    assert nx > 0 and ny > 0
    result = Image.new('RGBA', (cw * nx, ch * ny))
    cursor = 4
    for cell in range(nx * ny):
        count = struct.unpack_from('>b', raw, cursor)[0]
        cursor += 1
        assert count >= 0
        for _ in range(count):
            ref, dx, dy, x, y, w, h = struct.unpack_from('>7h', raw, cursor)
            cursor += 14
            assert ref == -1 and 0 <= x <= x+w <= image.width and 0 <= y <= y+h <= image.height
            result.paste(image.crop((x, y, x+w, y+h)), (cell % nx*cw+dx, cell // nx*ch+dy))
    assert cursor == len(raw)
    return result


@lru_cache(None)
def seb(folder, filename, frame=0):
    raw = (folder_path(folder) / filename).read_bytes()
    count, _ = struct.unpack_from('>HH', raw)
    cursor, layers = 4, []
    for _ in range(count):
        length, _ = struct.unpack_from('>HH', raw, cursor)
        cursor += 4
        frames = []
        for _ in range(length):
            frames.append(struct.unpack_from('>10h', raw, cursor))
            cursor += 20
        layers.append(next(r for r in frames if r[0] == frame))
    assert cursor == len(raw)
    return layers


class Icon:
    def __init__(self):
        self.image = Image.new('RGBA', (768, 768))
        self.sources = set()

    def add(self, folder, filename, record, x=0, z=0, height=0, opacity=1):
        if record[1] == -1:
            return
        _, _, u, v, w, h, dx, dy, flip_x, flip_y = record
        source = logical(folder, filename)
        assert 0 <= u < u+w <= source.width and 0 <= v < v+h <= source.height, (filename, record, source.size)
        part = source.crop((u, v, u+w, v+h))
        if flip_x:
            part = ImageOps.mirror(part)
        if flip_y:
            part = ImageOps.flip(part)
        if opacity != 1:
            part.putalpha(part.getchannel('A').point(lambda a: round(a * opacity)))
        px, py = 320+24*(x-z)+dx, 360+12*(x+z)-height+dy
        assert 0 <= px < px+w <= 768 and 0 <= py < py+h <= 768
        self.image.alpha_composite(part, (px, py))
        self.sources.add(folder + '/' + filename)

    def chip(self, chip, layer=0, x=0, z=0, height=0, frame=0):
        folder = {9: 'chip', 10: 'furniture', 23: 'building'}[int(chip[9])]
        self.add(folder, binding(folder, 'img', int(chip[10])),
                 seb(folder, binding(folder, 'seb', int(chip[11])), frame)[layer], x, z, height)

    def cropped(self):
        bounds = self.image.getbbox()
        assert bounds
        return ImageOps.expand(self.image.crop(bounds), border=3)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    chips, walls, warehouses, materials = table('MapChip'), table('Wall'), table('Warehouse'), table('Material')
    facilities = {int(r[0]): r for r in csv.reader((ROOT / 'data/sheet-research/raw-copies/KA GameData - Facility_lookup.csv').open(encoding='utf-8-sig')) if r[0].isdigit()}
    names = {int(i): n for i, n in re.findall(r'id:\s*(\d+)\s*,\s*name: "([^"]+)"', (APP / 'src/game-data/facilities.ts').read_text(encoding='utf-8'))}
    icons = {}
    for fid in [23, 24, 25, 26, 28, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43, 44, 194]:
        icon = Icon()
        facility = facilities[fid]
        wall = walls[int(facility[28])]
        wall_image = binding('wall', 'img', int(wall[3]))
        wall_layers = seb('wall', binding('wall', 'seb', int(wall[4])))
        if fid in [23, 24, 25, 26, 28]:
            if fid != 28:
                # Use the two connected sides instead of the isolated post.
                icon.add('wall', wall_image, seb('wall', 'barrier_00.seb', 6)[0])
            else:
                icon.add('wall', wall_image, wall_layers[0])
        else:
            chip = next(c for c in chips.values() if c[15] == '1' and int(c[16]) == fid)
            if fid in [42, 43, 44]:
                # Catalog sample of an expandable patch, not a required footprint.
                # Same-chip neighbors share a perimeter, like ordinary storehouses.
                cells = [(0, 0), (1, 0), (0, 1), (1, 1)]
                for x, z in cells:
                    icon.chip(chip, x=x, z=z)
                for x, z in cells:
                    for d, (dx, dz) in enumerate([(0, -1), (1, 0), (0, 1), (-1, 0)]):
                        if d in [0, 3] and (x+dx, z+dz) not in cells:
                            icon.add('wall', wall_image, wall_layers[d], x, z, int(chip[17]))
                if fid in [42, 43]:
                    for x, z in cells:
                        icon.chip(chip, x=x, z=z, frame=2)
                for x, z in cells:
                    for d, (dx, dz) in enumerate([(0, -1), (1, 0), (0, 1), (-1, 0)]):
                        if d in [1, 2] and (x+dx, z+dz) not in cells:
                            icon.add('wall', wall_image, wall_layers[d], x, z, int(chip[17]))
                icons[fid] = icon
                continue
            icon.chip(chip)
            for d in [0, 3]:
                icon.add('wall', wall_image, wall_layers[d], height=int(chip[17]))
            if fid in [33, 34, 35, 36, 37, 39]:
                # Representative full stock, using the recovered warehouse type join.
                material_type = int(warehouses[int(facility[21])][1])
                material = next(m for m in materials.values() if int(m[2]) == material_type)
                # GetWarehouseImgId 0x162e2a8 returns imgs[1], not material type.
                material_image = binding('material', 'img', int(material[6]))
                if fid == 39:
                    assert material_image == 'warehouse_07.png'
                for layer in seb('material', 'warehouse_obj.seb')[1:5]:
                    icon.add('material', material_image, layer, height=int(chip[17]))
            for d in [1, 2]:
                # Original Mystic Ore faces are opaque. User-requested catalog
                # treatment: southern walls at 30%, leaving source PNG untouched.
                icon.add('wall', wall_image, wall_layers[d], height=int(chip[17]), opacity=0.3 if fid == 37 else 1)
        icons[fid] = icon

    # User-accepted direction 0; recovered base/tower/flag heights and ground ring.
    icon = Icon()
    ring = json.loads((EVIDENCE / 'townhall-ground-fences.json').read_text())['groundRing']['offsetsFromHallFootprintTopLeft']
    occupied = {(0, 0), (1, 0), (0, 1), (1, 1)} | set(map(tuple, ring))
    floor = seb('chip', 'chip00.seb')[0]
    rails = seb('wall', 'fence_01.seb')
    front = []
    for x, z in ring:
        storage = (x, z) in [(0, 2), (1, 2)]
        icon.add('chip', 'souko_00.png' if storage else 'chip_94.png', floor, x, z)
        if storage:
            continue
        exposed = [d for d, (dx, dz) in enumerate([(0, -1), (1, 0), (0, 1), (-1, 0)]) if (x+dx, z+dz) not in occupied]
        if exposed:
            args = ('wall', 'fence_05.png', rails[exposed[0] if len(exposed) == 1 else 4], x, z, 6)
            if x == 2 or z == 2:
                front.append(args)
            else:
                icon.add(*args)
    icon.chip(chips[58], x=1, z=1)
    icon.chip(chips[59], x=1, z=1, height=53)
    icon.chip(chips[60], x=1, z=1, height=166)
    for args in front:
        icon.add(*args)
    icons[17] = icon

    # Both catalog Port records describe the same four-piece port structure.
    # Keep the optional dock out of this isolated building icon.
    icon = Icon()
    for cid, x, z in [(67, 1, 1), (70, 3, 1), (68, 1, 3), (69, 3, 3)]:
        icon.chip(chips[cid], layer=1, x=x, z=z, height=3)
    icons[7] = icons[10] = icon

    # Reconstruct complete furniture rectangles before trimming transparent
    # margins. Layer 0 of each original SEB selects the catalog-facing view;
    # furniture01 uses logical x=48 (not pixel x=1 in the packed PNG).
    # Matches the supplied Restaurant Shelves/Stove/Window screenshots.
    for chip in chips.values():
        fid = int(chip[16])
        if chip[9] != '10' or chip[15] != '1' or fid not in names:
            continue
        icon = Icon()
        icon.chip(chip)
        icon.source_name = chip[8]
        icons[fid] = icon

    records, tiles = [], []
    for fid, icon in sorted(icons.items()):
        filename = f'facility_{fid:03d}.png'
        result = icon.cropped()
        result.save(OUT / filename)
        shutil.copyfile(OUT / filename, PUBLIC / filename)
        assert Image.open(PUBLIC / filename).tobytes() == result.tobytes()
        records.append(dict(id=fid, name=names[fid], sourceName=getattr(icon, 'source_name', names[fid]), filename=filename, width=result.width, height=result.height, sources=sorted(icon.sources)))
        tile = Image.new('RGBA', (220, 260), '#eceff3')
        preview = result.copy()
        preview.thumbnail((208, 225), Image.Resampling.NEAREST)
        tile.alpha_composite(preview, ((220-preview.width)//2, (230-preview.height)//2))
        ImageDraw.Draw(tile).text((6, 238), names[fid], fill='black')
        tiles.append(tile)
    manifest = dict(version=2, source='Original OPT/SEB and native table bindings; 20260911 placement recovery',
                    scope='Furniture uses full OPT reconstruction and SEB layer 0/frame 0, then alpha-bounds trim; screenshot-matched facing for the supplied indoor examples. Static catalog illustrations; Town Hall direction 0, Port direction 1. Resource piles show full stock; item/treasure/egg storage is empty. Farms use a representative 2x2 patch with perimeter fences, mature crops and trees (frame 2); Ranch is an empty pasture. Mystic Ore southern wall alpha is multiplied by 0.3 for website visibility. Patch size and alpha adjustment are presentation choices, not recovered constraints. No game/runtime equivalence claim.', icons=records)
    for dest in [OUT, PUBLIC]:
        (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    sheet = Image.new('RGBA', (220*5, 260*((len(tiles)+4)//5)), 'white')
    for i, tile in enumerate(tiles):
        sheet.alpha_composite(tile, (i % 5*220, i // 5*260))
    sheet.save(OUT / 'contact-sheet.png')
    print(f'Exported and checked {len(records)} assembled facility icons.')


if __name__ == '__main__':
    main()
