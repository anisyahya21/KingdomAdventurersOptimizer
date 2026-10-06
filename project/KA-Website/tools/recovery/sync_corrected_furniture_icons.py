"""Replace legacy furniture PNGs in place with recovered catalog sprites."""
import json
import shutil
import zipfile
from export_assembled_facility_icons import ROOT, APP, OUT, TABLES, Icon, table, binding, seb

manifest_path = ROOT / 'website_icons/manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
recovered = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))['icons']
by_name = {name: r for r in recovered if any(s.startswith('furniture/') for s in r['sources'])
           for name in [r['name'], r['sourceName']]}
chips = {int(c[16]): c for c in table('MapChip').values() if c[9] == '10' and c[15] == '1'}
backup = ROOT.parent / 'RE-evidence/20260911-building/placement/furniture-icons-before-replacement.zip'
if not backup.exists():
    with zipfile.ZipFile(backup, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, 'manifest.json')
        for p in (ROOT / 'website_icons/furniture').glob('*.png'):
            archive.write(p, 'furniture/' + p.name)

count = 0
for entry in manifest['furniture']:
    record = by_name.get(entry['name'])
    if not record:
        continue
    chip = chips[record['id']]
    variants = entry.get('variants') or [dict(index=1, filename=entry['filename'])]
    for variant in variants:
        destination = ROOT / 'website_icons/furniture' / variant['filename']
        if variant['index'] == 1:
            shutil.copyfile(OUT / record['filename'], destination)
        else:
            # Retain an alternate direction, reconstructed from its complete SEB
            # rectangle rather than a guessed packed-image crop.
            layers = seb('furniture', binding('furniture', 'seb', int(chip[11])))
            layer = 2 if len(layers) == 4 else min(1, len(layers)-1)
            icon = Icon()
            icon.chip(chip, layer=layer)
            icon.cropped().save(destination)
        public = APP / 'public/website_icons/furniture' / variant['filename']
        shutil.copyfile(destination, public)
        assert destination.read_bytes() == public.read_bytes()
        for key in ['u', 'v', 'w', 'h']:
            variant.pop(key, None)
        variant['crop'] = 'Complete reconstructed OPT/SEB view, transparent bounds plus 3px padding'
        count += 1
    entry['source'] = 'tools/recovery/export_assembled_facility_icons.py + sync_corrected_furniture_icons.py'

manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
shutil.copyfile(manifest_path, APP / 'public/website_icons/manifest.json')
print(f'Replaced {count} furniture PNGs in both icon directories; original filenames retained.')
