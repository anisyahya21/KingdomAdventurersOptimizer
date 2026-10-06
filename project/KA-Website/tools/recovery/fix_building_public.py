"""Repair known four-byte truncations in served building assets; compare real consumer."""
import json,subprocess,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; WEB=ROOT/'KA-Website'
OUT=ROOT/'RE-evidence/20260911-building';OUT.mkdir(exist_ok=True)
TARGET=WEB/'artifacts/kingdom-adventures/public/world-assets/building'
def sha(b):return hashlib.sha256(b).hexdigest()
def probe():
    p=subprocess.run(['node',str(Path(__file__).with_name('probe_building_consumer.cjs')),str(TARGET)],capture_output=True,text=True,check=True)
    return json.loads(p.stdout)
rows=json.loads((ROOT/'RE-evidence/G4.1b/20260910/result.json').read_text())['changes']
changes=[]
for row in rows:
    source=Path(row['path']);new=source.read_bytes();assert sha(new)==row['after_sha256']
    p=TARGET/source.name;old=p.read_bytes();assert old in (new,new[:-4]),'Unexpected local edit: '+str(p)
    if old!=new:changes.append((p,old,new))
before=probe()
if changes:
    backup=OUT/'public-before.zip';assert not backup.exists(),'Backup exists; inspect instead of overwriting'
    with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
        for p,old,new in changes:z.writestr(p.name,old)
    for p,old,new in changes:p.write_bytes(new)
after=probe()
assert not after['errors']
for row in rows:assert sha((TARGET/Path(row['path']).name).read_bytes())==row['after_sha256']
result={'repaired_files':len(changes),'actual_preview_parser_before':before,'actual_preview_parser_after':after}
(OUT/'consumer-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
