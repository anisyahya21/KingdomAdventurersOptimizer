"""Default search is restricted to current/supporting research. History is opt-in."""
import argparse
import json
from pathlib import Path
from inventory import DOCS

ap=argparse.ArgumentParser()
ap.add_argument('query'); ap.add_argument('--historical',action='store_true')
args=ap.parse_args()
inv=json.loads((DOCS/'documents.json').read_text(encoding='utf-8'))
for item in inv['documents']:
    if item.get('containment_applied'):
        if not args.historical: continue
        p=Path(item['preserved_original'])
    elif item['disposition'] in ('CANONICAL','SUPPORTING'):
        p=Path(item['path'])
    else: continue
    for n,line in enumerate(p.read_text(encoding='utf-8',errors='replace').splitlines(),1):
        if args.query.lower() in line.lower():
            print(f'{p}:{n}: {line[:400]}')
