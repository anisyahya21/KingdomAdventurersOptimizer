"""Reversible legacy-document containment and explicit claim/consumer registry."""
import json
import re
import uuid
import zipfile
from pathlib import Path
from inventory import ROOT, WEB, EXT, DOCS, digest, write_json

def main():
    inv = json.loads((DOCS/'documents.json').read_text(encoding='utf-8'))
    run = ROOT/'RE-evidence/G1.2'/uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    originals = Path(inv['archive']).parent/'documents'
    changes = []
    with zipfile.ZipFile(inv['archive']) as z:
        for r in inv['documents']:
            p = Path(r['path'])
            if p.name in ('replit.md', 'tmp-guide.md'):
                r['disposition'] = 'OUT_OF_SCOPE'
                r['reason'] = 'General website overview or playthrough guide, not native research.'
            if r['disposition'] not in ('QUARANTINED', 'HISTORICAL'):
                continue
            b = p.read_bytes()
            assert digest(b) == r['sha256'], f'Original changed since preservation: {p}'
            archived = (originals/r['archive_member']).resolve()
            assert archived.is_relative_to(originals.resolve())
            archived.parent.mkdir(parents=True, exist_ok=True)
            archived.write_bytes(z.read(r['archive_member']))
            assert digest(archived.read_bytes()) == r['sha256']
            content = ('# Historical research — not an approved source\n\n'
                       'Status: QUARANTINED pending claim-level revalidation. Original wording, including any '
                       '“confirmed”, “final” or “100%” claims, is not current guidance. This notice does not mean every observation was false.\n\n'
                       f'[Preserved original](<{archived.as_posix()}>) · '
                       f'[Current recovery plan](<{(DOCS/"PLAN.md").as_posix()}>) · '
                       f'[Claim and consumer status](<{(DOCS/"claims.json").as_posix()}>)\n\n'
                       f'Original SHA-256: `{r["sha256"]}`\n')
            p.write_text(content, encoding='utf-8')
            r.update(disposition='QUARANTINED', containment_applied=True, preserved_original=str(archived),
                     current_sha256=digest(p.read_bytes()), reason='Original archived; active path is a status/redirect only.')
            changes.append({'path': str(p), 'before_sha256': r['sha256'], 'after_sha256': r['current_sha256']})
    definitions = [
        ('RE-001', 'rejected', 'PASS-012 direct-address procedure identifies exact methods in the sampled relocated Ghidra exports.', 'Original VA and relocated Ghidra address differ in the audited export. The correction is project-specific.', ['address-resolution-audit.md']),
        ('RE-002', 'rejected', 'The old SEB report establishes 100% render semantic closure.', 'Its own central parameter meanings remain inferred or unanswered; old generic cache helpers are not verified draw semantics.', ['SEB_SEMANTICS_FINAL_REPORT.md','seb-draw-final-summary.md','seb-draw-transform-rule.md','reverse-engineering-discovery-index.md']),
        ('RE-003', 'rejected', 'PASS-043 offset writes alone establish warehouse/bridge MapChip objects.', 'The sampled wrong identity and integer-array allocator invalidate that interpretation; individual native bodies still need correct revalidation.', ['renderer-impact.md','field-offset-resolution.md','pass041-summary.md']),
        ('RE-004', 'rejected', 'Hexadecimal 0x28 matches decimal enum value 28.', '0x28 equals decimal 40. No enum semantics follow without the owning domain.', ['constant-resolution.md']),
        ('RE-005', 'rejected', 'A shared numeric relatedDataId across different relatedDataType domains proves facility support membership.', 'A typed relationship or runtime producer is required; the historical proposed links do not supply it.', ['facility-visual-reconstruction-tests.md']),
        ('RE-006', 'unknown', 'Legacy experimental renderer placement, nature and traversal behavior matches the game.', 'Existing fitted offsets and heuristics remain unverified. Preview consumers are gated until original-game validation.', ['runtime-world-render-lab-evidence.md','facility-world-placement.md'])
    ]
    claims = []
    for cid,status,statement,reason,filenames in definitions:
        sources = [dict(path=r['path'], original_sha256=r['sha256'], preserved_original=r.get('preserved_original'))
                   for r in inv['documents'] if Path(r['path']).name in filenames]
        claims.append({'id':cid,'status':status,'statement':statement,'disposition_reason':reason,
                       'scope':'Historical claims in the sampled source reports; no whole-library semantic certification.',
                       'sources':sources,'supporting_audit':str(ROOT/'RE-audit-2026-09-10/AUDIT.md'),
                       'review_status':'pending_independent_review','dependencies':[]})
    src = WEB/'artifacts/kingdom-adventures/src'
    patterns = {'wrong_native_addresses':r'(?i)(?:0x)?0?15c50d4|(?:0x)?0?15c48c4|(?:0x)?0?15b07b8|(?:0x)?0?15b2184|(?:0x)?0?13eb4c0',
                'semantic_labels':r'StagedRenderEntry|depth_or_scale_delta|stored_draw_value',
                'renderer_imports':r'(?:import|from).*runtime-world-(?:render-test|grid-test)|from.*runtime/world-builder',
                'relationship_joins':r'relatedData(?:Id|Type)',
                'manual_offsets':r'MANUAL TEMPORARY FIX|PORT_BRIDGE_PIECES|PORT_GATE_OFFSETS'}
    hits = []
    for p in src.rglob('*'):
        if p.suffix not in ('.ts','.tsx','.json'):
            continue
        for n,line in enumerate(p.read_text(encoding='utf-8',errors='replace').splitlines(),1):
            for name,regex in patterns.items():
                if re.search(regex,line):
                    hits.append({'path':str(p),'line':n,'pattern':name,'text':line[:400]})
    consumers = [
        {'path':'src/pages/runtime-world-render-test.tsx','claims':['RE-006'],'disposition':'default export gated; manual placement remains unverified preview'},
        {'path':'src/pages/runtime-world-grid-test.tsx','claims':['RE-006'],'disposition':'default export gated; interpreted field/placement semantics remain unverified preview'},
        {'path':'src/pages/terrain-composition-lab.tsx','claims':['RE-006'],'disposition':'default export gated; unverified preview'},
        {'path':'src/pages/chaos-setup-lab.tsx','claims':['RE-006'],'disposition':'uses gated renderer; copied port footprint planning also gated at page entry'},
        {'path':'src/pages/world-map-v2.tsx','claims':['RE-006'],'disposition':'imports gated render/grid components; no direct implementation bypass'},
        {'path':'src/pages/weekly-conquest.tsx','claims':['RE-006'],'disposition':'only embedded renderer gated; unrelated weekly-conquest data retained'},
        {'path':'src/pages/runtime-world-render-lab.tsx','claims':['RE-006'],'disposition':'unrouted legacy alternate; additionally gated to prevent future direct reuse'},
        {'path':'src/runtime/world-builder','claims':['RE-006'],'disposition':'data-driven heuristics retained solely as unverified research; page import graph is gated'},
        {'path':'src/pages/world-map.tsx','claims':[],'disposition':'inspected: independent terrain CSV/reference map path, no direct audited wrong-function or staging-label matches; not certified by this cleanup'},
        {'path':'src/pages/map-2-testing.tsx','claims':[],'disposition':'inspected: independent terrain CSV path, not audited native-helper consumer; not certified by this cleanup'},
        {'path':'tools/asset_extractor/ghidra_scripts/BulkExportSebRender.java','claims':['RE-001','RE-002'],'disposition':'legacy exporter disabled by default with explicit unverified-research opt-in'}
    ]
    registry = {'schema_version':1,'claims':claims,'consumers':consumers,'scan_patterns':patterns,
                'scan_limits':'Text/constant/import scan plus targeted manual review. Not proof that arbitrary semantic copies anywhere on disk do not exist.',
                'scope':'Website src plus named legacy exporter; all inventoried historical research documents quarantined.',
                'result_status':'pending_independent_review'}
    write_json(DOCS/'claims.json',registry)
    write_json(DOCS/'documents.json',inv)
    write_json(run/'document-changes.json',changes)
    write_json(run/'consumer-scan.json',hits)
    write_json(run/'claims.json',registry)
    (run/'containment.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'run':str(run),'redirected_documents':len(changes),'consumer_scan_hits':len(hits)}))

if __name__=='__main__':
    main()
