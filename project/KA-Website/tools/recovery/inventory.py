"""Scoped research inventory and non-destructive snapshot/restore verification."""
import hashlib
import json
import re
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / 'KA-Website'
EXT = Path('C:/APK-RE/kingdom-adventurers')
DOCS = WEB / 'docs/reverse-engineering'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')

def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    run = ROOT / 'RE-evidence/G1.1' / stamp
    archive = ROOT / 'RE-archive' / stamp
    run.mkdir(parents=True)
    archive.mkdir(parents=True)
    scopes = [
        (ROOT / 'Handoff and start prompts', 'research'),
        (ROOT / 'KA-Legacy-Archive', 'historical'),
        (ROOT / '.github/agents', 'agent'),
        (WEB / '.github/agents', 'agent'),
        (WEB / 'docs', 'website-doc'),
        (WEB / 'artifacts/kingdom-adventures/docs', 'lab'),
        (EXT / 'docs', 'research'),
        (EXT / 'Reverse engineering', 'historical'),
        (ROOT / 'RE-audit-2026-09-10', 'audit'),
    ]
    files = {}
    excluded = {'node_modules', '.git', '.pnpm-store', '.venv', '__pycache__'}
    for folder, kind in scopes:
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for p in folder.rglob('*.md'):
            if not (set(p.parts) & excluded):
                files[p.resolve()] = kind
    for folder in (ROOT, WEB):
        for p in folder.glob('*.md'):
            files[p.resolve()] = 'root-note'
    records = []
    code_records = []
    zip_path = archive / 'originals.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for p, kind in sorted(files.items(), key=lambda kv: str(kv[0])):
            b = p.read_bytes()
            text = b.decode('utf-8-sig', errors='replace')
            base, prefix = (ROOT, 'workspace') if p.is_relative_to(ROOT) else (EXT, 'external')
            name = prefix + '/' + p.relative_to(base).as_posix()
            z.writestr(name, b)
            governance = p.name == 'AGENTS.md' or kind == 'agent' or p.is_relative_to(DOCS)
            unrelated = (kind == 'website-doc' and any(part in p.parts for part in ('operations', 'architecture'))
                         and p.name not in ('facility-world-placement.md', 'character-asset-composition.md'))
            unrelated |= p.name in ('website-workflow.md', 'backup-task-scheduler.md', 'pwa-event-reminders.md')
            if governance:
                disposition, reason = 'CANONICAL', 'Current operating instructions; version-bound review applies.'
            elif unrelated:
                disposition, reason = 'OUT_OF_SCOPE', 'Product/operations documentation outside research cleanup.'
            elif kind == 'audit':
                disposition, reason = 'SUPPORTING', 'Sampled September audit; not full-library certification.'
            elif kind == 'historical':
                disposition, reason = 'HISTORICAL', 'Historical research; no blanket verification.'
            else:
                disposition, reason = 'QUARANTINED', 'Legacy research requires claim-level revalidation.'
            links = []
            for target in re.findall(r'\]\(([^)]+)\)', text):
                target = target.strip('<>')
                if re.match(r'^[a-z]+://', target) or target.startswith('#'):
                    continue
                target_path = target.split('#')[0]
                resolved = Path(target_path) if Path(target_path).is_absolute() else p.parent / target_path
                links.append({'target': target, 'exists': resolved.exists()})
            records.append({'path': str(p), 'sha256': digest(b), 'size': len(b), 'kind': kind,
                            'disposition': disposition, 'reason': reason, 'containment_applied': False,
                            'archive_member': name, 'links': links})
        # Preserve the source tree potentially affected by claim cleanup, including uncommitted bytes.
        for p in sorted((WEB / 'artifacts/kingdom-adventures/src').rglob('*')):
            if p.is_file() and p.suffix in ('.ts', '.tsx', '.json', '.css'):
                b = p.read_bytes()
                name = 'workspace/' + p.relative_to(ROOT).as_posix()
                z.writestr(name, b)
                code_records.append({'path': str(p), 'sha256': digest(b), 'archive_member': name})
        z.writestr('restore-fixtures/nested space/évidence.txt', 'Restoration fixture — épreuve.\n'.encode())
    restored = run / 'restore-check'
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            dest = (restored / member.filename).resolve()
            if not dest.is_relative_to(restored.resolve()):
                raise ValueError('Unsafe archive member')
            dest.parent.mkdir(parents=True, exist_ok=True)
            b = z.read(member)
            dest.write_bytes(b)
            assert digest(dest.read_bytes()) == digest(b)
    for item in records + code_records:
        assert digest(Path(item['path']).read_bytes()) == item['sha256'], item['path']
        assert digest((restored / item['archive_member']).read_bytes()) == item['sha256']
    binary_paths = [EXT/'lib/arm64-v8a/libil2cpp.so', EXT/'lib/armeabi-v7a/libil2cpp.so',
                    EXT/'assets/bin/Data/Managed/Metadata/global-metadata.dat', EXT/'kingdom-adventurers.apk']
    binaries = [{'path': str(p), 'sha256': digest(p.read_bytes()), 'size': p.stat().st_size} for p in binary_paths]
    projects = []
    for folder in ('C:/GhirdaProjects', 'C:/ghiidra 11.4.2 projects', 'C:/ghiidra 11.4.2 projects - Copy'):
        for p in Path(folder).glob('*.gpr'):
            projects.append({'path': str(p), 'sha256': digest(p.read_bytes()), 'working_copy_required_before_mutation': True})
    git = subprocess.run(['git', '-C', str(WEB), 'status', '--porcelain'], capture_output=True, text=True, check=True)
    (run/'git-status-before.txt').write_text(git.stdout, encoding='utf-8')
    groups = {}
    for r in records:
        groups.setdefault(r['sha256'], []).append(r['path'])
    manifest = {'schema_version': 1, 'run_id': stamp, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'scope': [{'path': str(p), 'kind': k} for p,k in scopes], 'extra_scope': ['workspace/*.md', 'KA-Website/*.md'],
                'excluded_directory_names': sorted(excluded), 'archive': str(zip_path), 'archive_sha256': digest(zip_path.read_bytes()),
                'documents': records, 'source_files': code_records, 'binaries': binaries, 'project_handles': projects,
                'duplicate_groups': [v for v in groups.values() if len(v)>1],
                'validation': {'all_documents_restored': len(records), 'all_source_files_restored': len(code_records),
                               'original_bytes_unchanged': True, 'nested_space_unicode_fixture_pass': True},
                'result_status': 'pending_independent_review'}
    write_json(run/'inventory-result.json', manifest)
    write_json(DOCS/'documents.json', manifest)
    (run/'inventory.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'run': str(run), 'archive': str(zip_path), 'documents': len(records), 'source_files': len(code_records),
                      'restored': True, 'duplicates': len(manifest['duplicate_groups'])}))

if __name__ == '__main__':
    main()
