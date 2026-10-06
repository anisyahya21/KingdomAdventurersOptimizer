"""Restore only the reviewed building payloads; preflight all bytes before mutation."""
import hashlib,json,zipfile,subprocess,sys,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; WEB=ROOT/'KA-Website'
BASE=ROOT/'RE-evidence/G4.1a/20260910'; RUN=ROOT/'RE-evidence/G4.1b/20260910'
TARGET=WEB/'artifacts/kingdom-adventures/tmp/KA_assets/building'
LEGACY=WEB/'tools/asset_extractor/decrypt_assets.py'
def sha(b):return hashlib.sha256(b).hexdigest()
def save(n,v):(RUN/n).write_text(json.dumps(v,indent=2),encoding='utf-8')
def tree(p):return {str(f.relative_to(p)):sha(f.read_bytes()) for f in p.rglob('*') if f.is_file()} if p.exists() else {}
def main():
    data=(BASE/'building.archive').read_bytes()
    assert sha(data)=='d4ea91b74629367808c44fd3af00680099511218865ce16621384dfbe3c3c737'
    rows=json.loads((BASE/'archive-records.json').read_text());assert len(rows)==360
    payloads=[];seen=set()
    for r in rows:
        p=(TARGET/r['name']).resolve()
        assert p.is_relative_to(TARGET.resolve()) and p not in seen;seen.add(p)
        new=data[r['data_offset']:r['data_offset']+r['length']]
        assert len(new)==r['length']
        old=p.read_bytes()
        assert old in (new,new[:-4]),'Unexpected bytes: '+str(p)
        payloads.append((p,old,new))
    unrelated={str(p):sha(p.read_bytes()) for p in TARGET.rglob('*') if p.is_file() and p.resolve() not in seen}
    generated_roots=[WEB/'artifacts/kingdom-adventures/public/world-assets/building',WEB/'artifacts/api-server/data/sprites/building',WEB/'tools/asset_extractor/generated']
    generated={str(p):tree(p) for p in generated_roots}
    snapshot=RUN/'before-r2.zip'
    assert not snapshot.exists(),'Use a new run for a new migration'
    with zipfile.ZipFile(snapshot,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p,old,new in payloads:z.writestr(p.relative_to(ROOT).as_posix(),old)
        z.writestr(LEGACY.relative_to(ROOT).as_posix(),LEGACY.read_bytes())
    with zipfile.ZipFile(snapshot) as z:
        for p,old,new in payloads:assert z.read(p.relative_to(ROOT).as_posix())==old
        assert z.read(LEGACY.relative_to(ROOT).as_posix())==LEGACY.read_bytes()
    # Trace candidate consumers once; preserve full matches outside chat.
    probe=subprocess.run(['rg','-n','KA_assets|parse_img_inf|parse_scr_inf|world-assets|decrypt_assets',str(WEB/'tools/asset_extractor'),str(WEB/'artifacts/kingdom-adventures/scripts'),str(WEB/'artifacts/kingdom-adventures/src'),str(WEB/'artifacts/api-server/src'),'-g','*.py','-g','*.mjs','-g','*.ts','-g','*.tsx'],capture_output=True,text=True,encoding='utf-8')
    assert probe.returncode==0
    (RUN/'consumer-search.txt').write_text(probe.stdout,encoding='utf-8')
    save('consumers.json',{'search_exit_code':probe.returncode,'dispositions':[
        {'path':'tools/asset_extractor/config.py','role':'KA_ASSETS_DIR directs extractor consumers to repaired building source'},
        {'path':'tools/asset_extractor/extractors/asset_registry.py','role':'_build_refs_for_directory reads img.inf and related PNG/OPT; repaired raw input; inferred res seeds and parsed semantics remain unverified'},
        {'path':'tools/asset_extractor/main.py and extractors/discovery','role':'Direct INF/discovery consumers use same root; no outputs regenerated, inferred domain mappings not approved'},
        {'path':'tools/asset_extractor/decrypt_assets.py','role':'Obsolete truncating bulk entry replaced with fail-closed notice before imports/file writes'},
        {'path':'src/pages/runtime-world-render-test.tsx and runtime-world-render-lab.tsx','role':'Consume separate public/world-assets copies; already preview-gated, generated copies untouched and still unverified'},
        {'path':'src/pages/terrain-composition-lab.tsx and runtime-world-grid-test.tsx','role':'Other domains; excluded from building migration; existing preview gate retained'},
        {'path':'artifacts/api-server/src/routes/ka.ts','role':'Search hit is legacy Skill.txt fallback, not building input; excluded'},
        {'path':'artifacts/kingdom-adventures/scripts map tools','role':'Map/chip/xls inputs; not building migration targets'},
    ],'scope_limit':'Candidate search plus direct owner inspection, not whole-project semantic certification. Experimental scripts retain unverified algorithms; repaired bytes do not approve those algorithms.'})
    for p,old,new in payloads:
        if old!=new:p.write_bytes(new)
    LEGACY.write_text('''"""Retired bulk extractor: its payload sizing truncated four bytes per file.
The original implementation is preserved in RE-evidence/G4.1b/20260910/before-r2.zip.
Only the building archive has been reviewed. Use tools/recovery tooling and the
reverse-engineering state/review ledger; other archives require separate review.
"""
raise SystemExit("Retired unsafe bulk extractor. See docs/reverse-engineering/state.json; do not regenerate from this entry point.")
''',encoding='utf-8')
    for p,old,new in payloads:assert p.read_bytes()==new
    assert all(Path(p).read_bytes() and sha(Path(p).read_bytes())==h for p,h in unrelated.items())
    assert generated=={str(p):tree(p) for p in generated_roots}
    guard=subprocess.run([sys.executable,str(LEGACY)],capture_output=True,text=True)
    assert guard.returncode!=0 and 'Retired unsafe' in guard.stderr
    changes=[{'path':str(p),'before_sha256':sha(old),'after_sha256':sha(new),'restored_bytes':len(new)-len(old)} for p,old,new in payloads]
    save('result.json',{'step':'G4.1b','success':True,'changed':sum(old!=new for p,old,new in payloads),'checked_members':len(payloads),'changes':changes,
        'snapshot_sha256':sha(snapshot.read_bytes()),'snapshot_all_before_bytes_verified':True,'generated_unchanged':generated,'unrelated_unchanged':unrelated,
        'guard':{'path':str(LEGACY),'sha256':sha(LEGACY.read_bytes()),'exit_code':guard.returncode,'stderr':guard.stderr},
        'script_sha256':sha(Path(__file__).read_bytes()),'limits':'Raw building payload repair only; no OPT/SEB interpretation, resource joins, rendering algorithms or generated output certified.'})
    print(json.dumps({'repaired':sum(old!=new for p,old,new in payloads),'verified':len(payloads),'legacy_entry_blocked':True,'generated_unchanged':True}))
if __name__=='__main__':main()

