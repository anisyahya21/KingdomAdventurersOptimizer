"""Pixel-compare TS draw commands with an independent logical-cell sampler."""
import sys,json,math
from pathlib import Path
sys.path.insert(0,r'C:\Users\anisb\unitypy_pkgs')
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'RE-evidence/20260911-building/placement'
ASSETS=ROOT/'KA-Website/artifacts/kingdom-adventures/public/world-assets/building'
cases=json.loads((OUT/'cases.json').read_text());tiles=[]
for case in cases:
    source=Image.open(ASSETS/(case['name']+'.png')).convert('RGBA')
    dst=case['destination'];req=case['request'];scale=case['scale']
    size=(dst['x']+dst['width']+20,dst['y']+dst['height']+18)
    actual=Image.new('RGBA',size)
    for command in case['commands']:
        s,d=command['source'],command['destination']
        part=source.crop((s['x'],s['y'],s['x']+s['width'],s['y']+s['height']))
        part=part.resize((d['width'],d['height']),Image.Resampling.NEAREST)
        actual.alpha_composite(part,(d['x'],d['y']))
    # Sample each logical cell pixel from its component, independently of TS intersections.
    expected=Image.new('RGBA',size)
    for c in case['components']:
        layer=Image.new('RGBA',size);pixels=layer.load();inp=source.load()
        for py in range(dst['y'],dst['y']+dst['height']):
            y=req['y']+(py-dst['y'])//scale
            for px in range(dst['x'],dst['x']+dst['width']):
                x=req['x']+(px-dst['x'])//scale
                if c['x']<=x<c['x']+c['width'] and c['y']<=y<c['y']+c['height']:
                    pixels[px,py]=inp[c['sourceX']+x-c['x'],c['sourceY']+y-c['y']]
        expected.alpha_composite(layer)
    assert actual.tobytes()==expected.tobytes(),case['name']
    if not case['clipped'] and scale==1:
        tile=Image.new('RGBA',(160,180),(235,238,242,255));draw=ImageDraw.Draw(tile)
        draw.rectangle((20,18,20+req['width']-1,18+req['height']-1),outline=(100,120,150))
        tile.alpha_composite(actual);draw.text((8,156),f"{case['name']} [{case['column']},{case['row']}]",fill=(20,30,40))
        tiles.append(tile)
sheet=Image.new('RGBA',(160*4,180*math.ceil(len(tiles)/4)),(255,255,255))
for i,tile in enumerate(tiles):sheet.alpha_composite(tile,((i%4)*160,(i//4)*180))
sheet.save(OUT/'building-cells.png')
result={'pixelComparisonsPassed':len(cases),'assetFrames':len(tiles),'scope':'Logical unrotated OPT placement; raw PNG source scale1; no original runtime image comparison'}
(OUT/'pixel-tests.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
