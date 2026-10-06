"""Check preservation, redirects, active claim references and preview isolation."""
import argparse
import json
import re
from pathlib import Path
from inventory import ROOT, WEB, DOCS, digest

def check(extra_text=''):
    inv=json.loads((DOCS/'documents.json').read_text(encoding='utf-8'))
    claims=json.loads((DOCS/'claims.json').read_text(encoding='utf-8'))
    rejected={c['id'] for c in claims['claims'] if c['status']=='rejected'}
    errors=[]
    texts=[extra_text]
    quarantined=0
    for r in inv['documents']:
        p=Path(r['path'])
        text=p.read_text(encoding='utf-8',errors='replace')
        if r['containment_applied']:
            quarantined+=1
            original=Path(r['preserved_original'])
            if digest(original.read_bytes())!=r['sha256']: errors.append('archive mismatch: '+str(p))
            if digest(p.read_bytes())!=r['current_sha256']: errors.append('redirect changed: '+str(p))
            if not text.startswith('# Historical research — not an approved source'): errors.append('missing notice: '+str(p))
            for link in re.findall(r'\]\(<([^>]+)>\)',text):
                if not Path(link).is_file(): errors.append('broken redirect: '+link)
        else:
            texts.append(text)
    for text in texts:
        for cid in rejected:
            if re.search(r'claim:'+re.escape(cid)+r'\s+status:verified',text):
                errors.append('rejected claim promoted: '+cid)
        if re.search(r'Semantic Precision:\*?\*?\s*100%',text,re.I):
            errors.append('uncontained legacy semantic precision claim')
    src=WEB/'artifacts/kingdom-adventures/src/pages'
    names=['runtime-world-render-test','runtime-world-render-lab','runtime-world-grid-test','terrain-composition-lab','chaos-setup-lab']
    for name in names:
        text=(src/(name+'.tsx')).read_text(encoding='utf-8')
        if not re.search(r'export default function[^\n]+\n\s*return <ResearchPreviewGate>',text):
            errors.append('ungated preview: '+name)
    legacy=(WEB/'tools/asset_extractor/ghidra_scripts/BulkExportSebRender.java').read_text()
    if 'if (!Boolean.getBoolean("ka.allowUnverifiedLegacyExport"))' not in legacy:
        errors.append('legacy exporter not disabled')
    return {'passed':not errors,'errors':errors,'quarantined_documents':quarantined}

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--inject-stale-reference',action='store_true'); args=ap.parse_args()
    result=check('<!-- claim:RE-002 status:verified -->' if args.inject_stale_reference else '')
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
