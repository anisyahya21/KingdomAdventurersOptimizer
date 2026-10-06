"""Compile only: no tests, diagnostic battles or benchmark execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import argparse

HERE = Path(__file__).resolve().parent
SDK = HERE.parent / 'toolchain/go'
parser = argparse.ArgumentParser(description='Compile immutable versioned Go artifacts; no tests.')
parser.add_argument('--label', default='finish')
args = parser.parse_args()
if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.label):
    parser.error('label must contain only letters, digits, hyphen or underscore')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
sources = {p.name: sha(p) for p in sorted(HERE.glob('*.go'))}
sources['go.mod'] = sha(HERE / 'go.mod')
if (HERE / 'native_search_bounds.json').exists():
    sources['native_search_bounds.json'] = sha(HERE / 'native_search_bounds.json')
revision = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
env = dict(os.environ, GOROOT=str(SDK), GOCACHE=str(HERE / '.build-cache'), GOMAXPROCS='6')
destination = HERE / 'revisions' / (args.label+'-'+revision[:12])
destination.mkdir(parents=True, exist_ok=True)
executable = destination / 'go-optimizer.exe'
if executable.exists():
    raise SystemExit('Existing version is immutable; choose a new label or reuse its manifest.')
for name in sources:
    (destination / name).write_bytes((HERE / name).read_bytes())
command = [str(SDK / 'bin/go.exe'), 'build', '-p', '6', '-ldflags', '-X main.buildRevision='+revision, '-o', str(executable), '.']
completed = subprocess.run(command, cwd=HERE, env=env, capture_output=True, text=True)
after = {p.name: sha(p) for p in sorted(HERE.glob('*.go'))}
after['go.mod'] = sha(HERE / 'go.mod')
if (HERE / 'native_search_bounds.json').exists():
    after['native_search_bounds.json'] = sha(HERE / 'native_search_bounds.json')
changed_during_build = after != sources
manifest = {'schema':'ka-go-build-1', 'sourceRevision':revision, 'sources':sources, 'command':command, 'exitCode':completed.returncode,
            'stdout':completed.stdout, 'stderr':completed.stderr, 'testsExecuted':False, 'sourceChangedDuringBuild':changed_during_build}
if completed.returncode == 0 and not changed_during_build:
    manifest['executableSHA256'] = sha(executable)
    manifest['executablePath'] = str(executable)
(destination / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
(HERE / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in manifest.items() if k not in ('sources','command')}, indent=2))
raise SystemExit(completed.returncode or int(changed_during_build))
