"""Builder sprite parts with preserved native anchors; reuses recovered OPT/SEB.
Run from workspace root. Does not modify original images or catalog exports.
"""
import json, sys, hashlib, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import export_assembled_facility_icons as source
from PIL import Image, ImageOps

APP = source.APP
OUT = APP / 'public/world-assets/builder'
OUT.mkdir(parents=True, exist_ok=True)
chips = source.table('MapChip')
facilities = {int(r[0]): r for r in csv.reader((source.ROOT / 'data/sheet-research/raw-copies/KA GameData - Facility_lookup.csv').open(encoding='utf-8-sig')) if r[0].isdigit()}
sprites = {}

def part(folder, name, rec):
    key = hashlib.sha1(json.dumps([folder,name,rec]).encode()).hexdigest()[:16]
    if key not in sprites:
        _, _, u,v,w,h,dx,dy,fx,fy = rec
        original=source.logical(folder,name)
        assert 0<=u<u+w<=original.width and 0<=v<v+h<=original.height,(folder,name,rec,original.size)
        im = original.crop((u,v,u+w,v+h))
        if fx: im = ImageOps.mirror(im)
        if fy: im = ImageOps.flip(im)
        im.save(OUT / (key+'.png'))
        sprites[key] = dict(url='/world-assets/builder/'+key+'.png',width=w,height=h,dx=dx,dy=dy)
    return key

def chip_sprite(chip, direction=0):
    folder = {9:'chip',10:'furniture',23:'building'}[int(chip[9])]
    layers = source.seb(folder,source.binding(folder,'seb',int(chip[11])),int(chip[12]))
    rec=layers[direction % len(layers)]
    if rec[4]<=0 or rec[5]<=0:rec=next(r for r in layers if r[4]>0 and r[5]>0)
    return part(folder,source.binding(folder,'img',int(chip[10])),rec)

# Capture complete established compound illustrations and their original origin.
# Temporary output is kept under the builder export; catalog owners stay untouched.
captured = []
OriginalIcon = source.Icon
class CaptureIcon(OriginalIcon):
    def cropped(self):
        box = self.image.getbbox()
        captured.append(dict(dx=box[0]-3-320,dy=box[1]-3-360))
        return super().cropped()
source.Icon = CaptureIcon
source.OUT = OUT / 'assemblies'
source.PUBLIC = OUT / 'assemblies-copy'
source.main()
assemblies = json.loads((source.OUT/'manifest.json').read_text())['icons']
assembled = {}
for entry, anchor in zip(assemblies,captured):
    key = 'assembly-'+str(entry['id'])
    sprites[key] = dict(url='/world-assets/builder/assemblies/'+entry['filename'],width=entry['width'],height=entry['height'],**anchor)
    assembled[entry['id']] = key

catalog = {}
for fid, f in facilities.items():
    candidates = [c for c in chips.values() if c[15]=='1' and int(c[16])==fid and int(c[9]) in [9,10,23]]
    if not candidates: continue
    c = candidates[0]
    variants = []
    try:
        variants = [chip_sprite(c,d) for d in range(4)]
    except (KeyError,IndexError,StopIteration,FileNotFoundError):
        pass
    if fid in assembled and int(c[9]) != 10:
        variants = [assembled[fid]]*4
    if not variants: continue
    catalog[str(fid)] = dict(chipId=int(c[0]),width=int(c[22])*int(c[24]),height=int(c[23])*int(c[25]),componentWidth=int(c[22]),componentHeight=int(c[23]),unitWidth=int(c[24]),unitHeight=int(c[25]),variants=variants,states=[],builtInRoomFixture=int(c[9])==10 and int(c[2]) in [55,56,57],expandsTown=bool(int(f[-2])&2))
    if fid==17:
        catalog[str(fid)].update(width=4,height=4,anchorX=1,anchorY=1)
    elif fid in [7,10]:
        catalog[str(fid)].update(width=4,height=4,anchorX=0,anchorY=0)
    elif fid in assembled and int(c[9])!=10:
        catalog[str(fid)].update(anchorX=0,anchorY=0)
    else:
        catalog[str(fid)].update(anchorX=int(c[22])//2,anchorY=int(c[23])//2)

# FencePlaceSystem 0x1580618: every MapChip type 22 joins every other type 22.
# The four-direction neighbor mask is the SEB frame, not a rotation index.
walls = source.table('Wall')
for fid, asset in catalog.items():
    c = chips[asset['chipId']]
    if int(c[1]) != 22: continue
    wall = walls[int(facilities[int(fid)][28])]
    image_name = source.binding('wall', 'img', int(wall[3]))
    asset['barrierFrames'] = [part('wall', image_name, source.seb('wall', 'barrier_00.seb', mask)[0]) for mask in range(16)]
    # Menu illustration shows both rails; map rendering always selects by neighbors.
    asset['variants'] = [asset['barrierFrames'][6]] * 4
    asset['anchorX'] = asset['anchorY'] = 0

# Original surround effects; range traced in AroundEffectSystem 0x14e3c14.
native_facilities = source.table('Facility')
for fid, asset in catalog.items():
    raw = native_facilities[int(fid)]
    effects = [list(map(int,raw[-57+i*4:-53+i*4])) for i in range(3)]
    effects = [e for e in effects if e[0] != -1]
    if effects: asset['surroundEffects'] = effects

# Town Hall facings use each original layer, including the rotated ground ring.
ring=json.loads((source.EVIDENCE/'townhall-ground-fences.json').read_text())['groundRing']['offsetsFromHallFootprintTopLeft']
hall_variants=[]
for direction in range(4):
    icon=OriginalIcon()
    def turn(x,y):return [(x,y),(1-y,x),(1-x,1-y),(y,1-x)][direction]
    occupied={(0,0),(1,0),(0,1),(1,1)}|{turn(x,y) for x,y in ring}
    rails=source.seb('wall','fence_01.seb');front=[]
    for ox,oy in ring:
        x,y=turn(ox,oy);storage=(ox,oy) in [(0,2),(1,2)]
        icon.add('chip','souko_00.png' if storage else 'chip_94.png',source.seb('chip','chip00.seb')[0],x,y)
        if storage:continue
        exposed=[d for d,(dx,dy) in enumerate([(0,-1),(1,0),(0,1),(-1,0)]) if (x+dx,y+dy) not in occupied]
        if exposed:
            args=('wall','fence_05.png',rails[exposed[0] if len(exposed)==1 else 4],x,y,6)
            if x==2 or y==2:front.append(args)
            else:icon.add(*args)
    for cid,elevation in [(58,0),(59,53),(60,166)]:
        c=chips[cid];count=len(source.seb('building',source.binding('building','seb',int(c[11]))))
        icon.chip(c,layer=direction%count,x=1,z=1,height=elevation)
    for args in front:icon.add(*args)
    box=icon.image.getbbox();im=icon.cropped();key=f'townhall-{direction}';im.save(OUT/(key+'.png'))
    sprites[key]=dict(url='/world-assets/builder/'+key+'.png',width=im.width,height=im.height,dx=box[0]-3-320,dy=box[1]-3-360)
    hall_variants.append(key)
catalog['17']['variants']=hall_variants

# The build command uses MapChip size * unit dimensions (PlaceChip
# 0x15045f0..4630 and 0x15048b0..48cc). Farms are four component cells.
# The established farm assembly already has exactly that footprint.
for fid in [42,43,44]:
    assert catalog[str(fid)]['width']==catalog[str(fid)]['height']==2

# Two-cell entrances use the paired door frames, as in the recovered plot
# entrance assembly. Frame selection follows the adjoining cell, not scaling.
for fid in [28]:
    c=chips[catalog[str(fid)]['chipId']]
    wall=source.table('Wall')[int(facilities[fid][28])]
    variants=[]; gate_draws=[]
    for direction in range(4):
        icon=OriginalIcon();w,h=(1,2) if direction%2 else (2,1)
        footprint={(x,y) for y in range(h) for x in range(w)}; commands=[]
        for x,y in sorted(footprint,key=lambda p:sum(p)):
            icon.chip(c,x=x,z=y)
            commands.append(dict(sprite=chip_sprite(c),x=x,y=y,elevation=0,depth=-100000+x+y))
            delta=(0,-1) if direction%2 else (1,0)
            frame=int((x+delta[0],y+delta[1]) not in footprint)
            rec=source.seb('wall','entrance_door_00_'+['up','right','down','left'][direction]+'.seb',frame)[0]
            icon.add('wall',source.binding('wall','img',int(wall[3])),rec,x,y,height=int(c[17]))
            commands.append(dict(sprite=part('wall',source.binding('wall','img',int(wall[3])),rec),x=x,y=y,elevation=int(c[17]),depth=100*(x+y)+(26 if direction in [1,2] else 5)))
        box=icon.image.getbbox();im=icon.cropped();key=f'entrance-{fid}-{direction}';im.save(OUT/(key+'.png'))
        sprites[key]=dict(url='/world-assets/builder/'+key+'.png',width=im.width,height=im.height,dx=box[0]-3-320,dy=box[1]-3-360)
        variants.append(key)
        gate_draws.append(commands)
    catalog[str(fid)].update(variants=variants,anchorX=0,anchorY=0,rotationDraws=gate_draws)

# Native warehouse objects: four fullness layers, no arbitrary sprite scaling.
walls, warehouses, materials = source.table('Wall'),source.table('Warehouse'),source.table('Material')
for fid in [33,34,35,36,37,38,39,40,194]:
    f=facilities[fid];c=chips[catalog[str(fid)]['chipId']]
    wall=walls[int(f[28])];layers=source.seb('wall',source.binding('wall','seb',int(wall[4])))
    material_type=int(warehouses[int(f[21])][1])
    material=next(m for m in materials.values() if int(m[2])==material_type)
    states=[]
    for fullness in range(5):
        icon=OriginalIcon()
        footprint={(x,y) for y in range(int(c[25])) for x in range(int(c[24]))}
        for x,y in sorted(footprint,key=lambda p:sum(p)):
            icon.chip(c,x=x,z=y)
        for x,y in sorted(footprint,key=lambda p:sum(p)):
            exposed=[d for d,(dx,dy) in enumerate([(0,-1),(1,0),(0,1),(-1,0)]) if (x+dx,y+dy) not in footprint]
            for d in exposed:
                if d in [0,3]:icon.add('wall',source.binding('wall','img',int(wall[3])),layers[d],x,y,height=int(c[17]))
            # Scalar warehouses use pile layers; list warehouses use separate
            # gatherables and must not pretend these material piles are stock.
            if int(warehouses[int(f[21])][2])==0:
                for layer in source.seb('material','warehouse_obj.seb')[1:1+fullness]:icon.add('material',source.binding('material','img',int(material[6])),layer,x,y,height=int(c[17]))
            for d in exposed:
                if d in [1,2]:icon.add('wall',source.binding('wall','img',int(wall[3])),layers[d],x,y,height=int(c[17]))
        box=icon.image.getbbox();im=icon.cropped();key=f'storage-{fid}-{fullness}';im.save(OUT/(key+'.png'))
        sprites[key]=dict(url='/world-assets/builder/'+key+'.png',width=im.width,height=im.height,dx=box[0]-3-320,dy=box[1]-3-360)
        states.append(key)
    catalog[str(fid)]['variants']=[states[0]]*4
    catalog[str(fid)]['states']=states if int(warehouses[int(f[21])][2])==0 else []
    catalog[str(fid)]['contentsAppearancePending']=int(warehouses[int(f[21])][2])!=0

plots={}
house_materials={h['id']:h for h in json.loads((source.EVIDENCE/'plot-materials.json').read_text())['houses']}
for path in sorted((source.EVIDENCE/'plot-previews').glob('house-*-stage-1.json')):
    p=json.loads(path.read_text());draws=[]
    for draw in p['draws']:
        if draw['role'] in ['bed','workbench','shelves','storage','register']:continue
        folder='chip' if draw['role'] in ['floor','dirt','entranceGround'] else 'wall'
        x,y=draw['cell'];role=draw['role']
        depth=-10000+x+y if folder=='chip' else 100*(x+y)+(26 if role=='entranceDoor' else 0)
        draws.append(dict(sprite=part(folder,draw['asset'],draw['record']),x=x,y=y,elevation=draw['height'],depth=depth))
    fixed=[]
    for piece in p['fixedPieces']:
        c=chips[piece['chipId']]
        fid=int(c[16]);key=str(fid)
        if key not in catalog:
            catalog[key]=dict(chipId=int(c[0]),width=int(c[22]),height=int(c[23]),variants=[chip_sprite(c,d) for d in range(4)],states=[],anchorX=int(c[22])//2,anchorY=int(c[23])//2)
        fixed.append(dict(facilityId=fid,role=piece['role'],x=piece['x'],y=piece['y'],direction=piece['direction']))
    width,height={'S':(6,6),'M':(6,8),'L':(8,8),'XL':(8,10)}[p['size']]
    house=house_materials[p['houseId']]
    rotations=[]
    for turn in range(4):
        def rotate(x,y):
            return [(x,y),(height-1-y,x),(width-1-x,height-1-y),(y,width-1-x)][turn]
        n,m=(height,width) if turn%2 else (width,height)
        doors={rotate(width-1,2),rotate(width-1,3)}
        cmds=[]
        floor_height=8 if p['houseId']==16 else 10
        def add(folder,name,rec,x,y,elevation,depth):cmds.append(dict(sprite=part(folder,name,rec),x=x,y=y,elevation=elevation,depth=depth))
        for y in range(m):
            for x in range(n):
                inside=0<x<n-1 and 0<y<m-1
                add('chip',house['floor'] if inside else house['entranceGround'] if (x,y) in doors else house['perimeterGround'],source.seb('chip','chip00.seb')[0],x,y,0,-10000+x+y)
                if not inside and (x,y) not in doors:
                    layer=3 if x==0 and 0<y<m-1 else 0 if y==0 and 0<x<n-1 else 1 if x==n-1 and 0<y<m-1 else 2 if y==m-1 and 0<x<n-1 else 4
                    if any((x+dx,y+dy) in doors for dx,dy in [(0,-1),(1,0),(0,1),(-1,0)]):layer=4
                    add('wall',house['fence'],source.seb('wall',house['fenceTemplate'])[layer],x,y,0,100*(x+y))
                if inside:
                    for d,(dx,dy) in enumerate([(0,-1),(1,0),(0,1),(-1,0)]):
                        nx,ny=x+dx,y+dy
                        if (0<nx<n-1 and 0<ny<m-1) or (nx,ny) in doors:continue
                        add('wall',house['wall'],source.seb('wall',house['wallTemplate'])[d],x,y,floor_height,100*(x+y)+(20 if d in [1,2] else -20))
        for x,y in doors:
            d=1 if x==n-1 else 3 if x==0 else 0 if y==0 else 2
            delta=(0,-1) if d%2 else (1,0)
            frame=int((x+delta[0],y+delta[1]) not in doors)
            add('wall',house['entrance'],source.seb('wall','entrance_door_00_'+['up','right','down','left'][d]+'.seb',frame)[0],x,y,6,100*(x+y)+(26 if d in [1,2] else 5))
        rotations.append(cmds)
    plots[f"{p['houseId']}-{p['size']}"]=dict(draws=draws,rotations=rotations,fixed=fixed,supportHeight=int(chips[house['fields']['floor']][17]))

# Unassigned parcels have a neutral dirt floor and the recovered basic fence.
# This editor state is a visual placeholder until the user assigns HouseData.
empty_plots={}
for size,(w,h) in {'S':(6,6),'M':(6,8),'L':(8,8),'XL':(8,10)}.items():
    variants=[]
    for turn in range(4):
        n,m=(h,w) if turn%2 else (w,h);cmds=[];icon=OriginalIcon()
        for y in range(m):
            for x in range(n):
                rec=source.seb('chip','chip00.seb')[0]
                cmds.append(dict(sprite=part('chip','tuchi00.png',rec),x=x,y=y,elevation=0,depth=-10000+x+y))
                if turn==0:icon.add('chip','tuchi00.png',rec,x,y)
        for y in range(m):
            for x in range(n):
                if 0<x<n-1 and 0<y<m-1:continue
                layer=3 if x==0 and 0<y<m-1 else 0 if y==0 and 0<x<n-1 else 1 if x==n-1 and 0<y<m-1 else 2 if y==m-1 and 0<x<n-1 else 4
                rec=source.seb('wall','fence_01.seb')[layer]
                cmds.append(dict(sprite=part('wall','fence_01.png',rec),x=x,y=y,elevation=0,depth=100*(x+y)))
                if turn==0:icon.add('wall','fence_01.png',rec,x,y)
        variants.append(cmds)
        if turn==0:icon.cropped().save(OUT/f'land-{size}.png')
    empty_plots[size]=variants

for name,cid in [('road',5),('reclaim',0)]:
    # Road selection comes from the original named table row.
    if name=='road':cid=int(next(c for c in chips.values() if c[8]=='Road')[0])
    catalog[name]=dict(width=1,height=1,variants=[chip_sprite(chips[cid])]*4,states=[],anchorX=0,anchorY=0,chipId=cid)

dungeons = {}
for cid,c in chips.items():
    if int(c[2]) not in [34,89,90] or int(c[9]) != 23: continue
    sprite=chip_sprite(c)
    dungeons[str(cid)]=dict(chipId=cid,name=c[8]+(f' {cid-70}' if int(c[2])==34 else ''),width=int(c[22])*int(c[24]),height=int(c[23])*int(c[25]),variants=[sprite]*4,states=[],anchorX=int(c[22])//2,anchorY=int(c[23])//2)

# Menu previews use alpha-trimmed artwork; world sprites retain native anchors.
for fid,asset in list(catalog.items())+[(f'dungeon-{cid}',a) for cid,a in dungeons.items()]:
    sprite=sprites[(asset['states'] or asset['variants'])[-1 if asset['states'] else 0]]
    im=Image.open(APP/'public'/sprite['url'].lstrip('/')).convert('RGBA')
    box=im.getbbox()
    if box:
        preview=ImageOps.expand(im.crop(box),border=3)
        preview.save(OUT/f'menu-{fid}.png')
        asset['menuIcon']=f'/world-assets/builder/menu-{fid}.png'

# Occupant markers reuse original body poses. Bottom-center anchoring is editor
# presentation of an assignment, not a reconstruction of moving pet behavior.
for pet in json.loads((APP/'src/game-data/monster-sprites.json').read_text()):
    sprites[f"pet-{pet['id']}"]=dict(url=pet['src'],width=pet['width'],height=pet['height'],dx=-pet['width']/2,dy=-pet['height'])
output=dict(source='Original MapChip, House, OPT and SEB; recovered plot layout and territory formula. Static rendering, not game runtime validation.',sprites=sprites,facilities=catalog,dungeons=dungeons,plots=plots,emptyPlots=empty_plots)
(APP/'src/game-data/builder-assets.json').write_text(json.dumps(output,separators=(',',':'))+'\n')
print(f'Builder: {len(sprites)} anchored sprites, {len(catalog)} facilities, {len(plots)} plot shells')

# Original game UI pixels, no substitute vector icons. Build comes from the
# extracted native menu; remove is com/img.inf 167 (destruct.png).
build_icon=Image.open(APP/'public/website_icons/menu/menu_build_cropped.png')
build_icon.crop((0,28,build_icon.width,build_icon.height)).save(OUT/'build-icon.png')
Image.open(APP/'tmp/KA_assets/com/destruct.png').save(OUT/'remove-icon.png')

