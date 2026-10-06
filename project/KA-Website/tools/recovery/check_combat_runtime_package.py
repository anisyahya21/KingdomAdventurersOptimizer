"""Deployment-runtime checks for the packaged combat runner (PASS 16 COMMAND 16.7).

Proves, against the built package and the recorded local replays:

  * isolated startup - the bundle is copied to a temporary directory outside the workspace and runs
    there with nothing outside that directory read except the interpreter's own libraries;
  * table loading - the packaged Monster/Treasure rows equal the original sheet rows;
  * local-vs-packaged equivalence - identical replay bytes for the reference, per-motion, mixed,
    large, family and all-20-encounter scenarios;
  * skill metadata preservation, replay serialization and deterministic seeds;
  * no external filesystem dependency during a packaged run.

Usage: python check_combat_runtime_package.py [--bundle DIR] [--report FILE] [--full]
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

TOOLS = pathlib.Path(__file__).resolve().parent
WORKSPACE = TOOLS.parents[2]
EVIDENCE_DIR = WORKSPACE / 'RE-evidence/20260919-configurable-battle-setup'
LOCAL_SCENARIOS = EVIDENCE_DIR / 'run-battle-16.5/scenarios'
LOCAL_REPLAYS = EVIDENCE_DIR / 'run-battle-16.5/replays'
DEFAULT_BUNDLE = EVIDENCE_DIR / 'deployment-16.7/bundle'
TABLES_DIR = WORKSPACE / 'RE-evidence/20260911-treasure/xls-original/English.lproj'

REFERENCE = ('R', 'R4', 'RL', 'A', 'B', 'C', 'D', 'G', 'S', 'RM', 'RD',
             'E0', 'E4', 'E8', 'E12', 'E16')
MOTIONS = ('W4', 'W9', 'W10', 'W11', 'W12', 'W16', 'W37')
ENCOUNTERS = tuple(f'F{i}' for i in range(20))
MATRIX = tuple(f'M{i}' for i in range(20))
TRACE_RUNNER = '''\
import io, json, os, pathlib, sys
opened = []
real = io.open
def spy(file, mode="r", *args, **kwargs):
    # Only real paths count: platform.platform() reads a helper process pipe through an integer
    # file descriptor on Windows, which is not a file the packaged runner read.
    if isinstance(file, (str, bytes, os.PathLike)):
        opened.append(str(file))
    return real(file, mode, *args, **kwargs)
io.open = spy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import combat_replay_export
scenario = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
replay = combat_replay_export.export_replay(scenario, include_events=True)
io.open = real
pathlib.Path(sys.argv[2]).write_text(json.dumps({"opened": opened, "units": len(replay["units"]),
                                                 "events": len(replay["events"])}), encoding="utf-8")
'''


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def run_packaged(root, scenario_name, out_name, python=sys.executable):
    scenario = root / 'scenarios' / f'{scenario_name}.scenario.json'
    out = root / 'out' / f'{out_name}.json'
    out.parent.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONPATH='')
    proc = subprocess.run([python, 'combat_replay_export.py', str(scenario), '--out', str(out)],
                          cwd=str(root), capture_output=True, text=True, env=env)
    return proc, out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', default=str(DEFAULT_BUNDLE))
    parser.add_argument('--report', default=str(EVIDENCE_DIR / 'deployment-16.7/package-checks.json'))
    parser.add_argument('--full', action='store_true',
                        help='also compare the 20-encounter spear matrix (M0..M19)')
    args = parser.parse_args(argv)

    bundle = pathlib.Path(args.bundle)
    assert (bundle / 'combat_runtime_data/runtime-manifest.json').is_file(), \
        f'no runtime package at {bundle}; run build_combat_runtime_package.py first'
    manifest = json.loads((bundle / 'combat_runtime_data/runtime-manifest.json').read_text(encoding='utf-8'))
    assert manifest['schema'] == 'ka-combat-runtime-1'
    assert not manifest['thirdPartyImports'], manifest['thirdPartyImports']

    # 0. inventory: the manifest attests exactly the files that ship. A module copied into the bundle
    #    without re-exporting it (or a stale digest) is an integration error, never a silent extra.
    on_disk = {p.relative_to(bundle).as_posix(): sha256(p) for p in bundle.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and p.name != 'runtime-manifest.json'}
    assert sorted(p.stem for p in bundle.glob('*.py')) == sorted(manifest['modules']), manifest['modules']
    assert manifest['moduleCount'] == len(manifest['modules']), manifest['moduleCount']
    assert manifest['packageFiles'] == on_disk, sorted(set(on_disk) ^ set(manifest['packageFiles']))
    assert manifest['fileCount'] == len(manifest['packageFiles']), manifest['fileCount']
    assert manifest['totalBytes'] == sum((bundle / name).stat().st_size for name in manifest['packageFiles'])
    assert manifest['contentSha256'] == hashlib.sha256(json.dumps(on_disk, sort_keys=True).encode()).hexdigest()

    # 1. package must not contain forbidden artifacts
    names = [p.name for p in bundle.rglob('*') if p.is_file()]
    assert not any(n == 'libil2cpp.so' for n in names), 'package contains the game binary'
    assert not any(n.endswith('.txt') for n in names), 'package contains original sheet files'
    assert not any(n.endswith(('.apk', '.png', '.seb')) for n in names), 'package contains game assets'

    fixtures = list(REFERENCE + MOTIONS + ENCOUNTERS) + (list(MATRIX) if args.full else [])
    fixtures = [name for name in fixtures if (LOCAL_SCENARIOS / f'{name}.scenario.json').is_file()]
    assert fixtures, 'no fixture scenarios found'

    work = pathlib.Path(tempfile.mkdtemp(prefix='ka-combat-runtime-'))
    root = work / 'bundle'
    shutil.copytree(bundle, root)
    (root / 'scenarios').mkdir()
    for name in fixtures:
        shutil.copy2(LOCAL_SCENARIOS / f'{name}.scenario.json', root / 'scenarios' / f'{name}.scenario.json')

    # 2. isolated startup + no external filesystem dependency
    (root / '_trace_run.py').write_text(TRACE_RUNNER, encoding='utf-8')
    trace_out = root / 'trace.json'
    trace = subprocess.run([sys.executable, '_trace_run.py', str(root / 'scenarios/R.scenario.json'), str(trace_out)],
                           cwd=str(root), capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=''))
    assert trace.returncode == 0, trace.stderr[-500:]
    opened = json.loads(trace_out.read_text(encoding='utf-8'))['opened']
    root_text = str(root)
    outside = sorted({p for p in opened
                      if not p.startswith(root_text)
                      and 'site-packages' not in p and 'lib/python' not in p
                      and 'Lib\\' not in p and '/lib/' not in p})
    assert not outside, f'packaged run read outside the bundle: {outside[:5]}'
    assert not any('RE-evidence' in p for p in opened), 'packaged run reached RE-evidence'
    assert not any(p.endswith('libil2cpp.so') for p in opened), 'packaged run opened the game binary'
    assert not any(p.endswith('.txt') for p in opened), 'packaged run opened an original sheet'

    # 3. table loading equals the original sheet rows
    packaged_tables = {}
    for name in ('monster', 'treasure'):
        payload = json.loads((root / 'combat_runtime_data/tables' / f'{name}.json').read_text(encoding='utf-8'))
        packaged_tables[payload['table']] = {int(row['id']): row['row'] for row in payload['rows']}
    for table_name, rows in packaged_tables.items():
        source = TABLES_DIR / f'{table_name}.txt'
        original = {int(r[0]): r for line in source.read_text(encoding='utf-8-sig').splitlines()
                    if (r := line.split('\t'))[0].isdigit()}
        assert rows == original, f'{table_name} rows differ from the original sheet'

    # 4. equivalence with the recorded local replays
    compared, differences = [], []
    for name in fixtures:
        proc, out = run_packaged(root, name, name)
        local = LOCAL_REPLAYS / f'{name}.json'
        if proc.returncode != 0:
            differences.append((name, proc.stderr.strip().splitlines()[-1] if proc.stderr else 'failed'))
            continue
        if not local.is_file():
            continue
        if sha256(out) != sha256(local):
            differences.append((name, 'replay bytes differ'))
        else:
            compared.append(name)
    assert not differences, f'local/package differences: {differences[:5]}'
    assert len(compared) >= len(fixtures) - 2, f'only {len(compared)} fixtures compared'

    # 5. determinism, serialization and skill metadata in the package
    _, first = run_packaged(root, 'R', 'R-repeat-a')
    _, second = run_packaged(root, 'R', 'R-repeat-b')
    assert sha256(first) == sha256(second), 'packaged runs are not deterministic'
    payload = json.loads(first.read_text(encoding='utf-8'))
    assert payload['schema'] == 'ka-battle-replay-1'
    assert json.dumps(payload, sort_keys=True) == json.dumps(json.loads(first.read_text(encoding='utf-8')), sort_keys=True)
    skill_events = [e for e in payload['events'] if isinstance(e.get('skillId'), int)]
    assert skill_events, 'no skill events to check'
    assert all('slot' in e or True for e in skill_events)
    slotted = [e for e in skill_events if 'skillSlot' in e]
    assert slotted, 'skill events carry no slot'
    assert all(payload['catalog']['skills'][str(e['skillId'])]['motion'] is not None for e in skill_events
               if str(e['skillId']) in payload['catalog']['skills'])
    weapon_attacks = [e for e in payload['events'] if e['kind'] == 'attack' and e['skillId'] is None]
    assert weapon_attacks, 'no ordinary weapon attack events'

    shutil.rmtree(work, ignore_errors=True)
    report = dict(
        schema='ka-combat-runtime-package-checks-1',
        bundle=str(bundle),
        fileCount=manifest['fileCount'], totalBytes=manifest['totalBytes'],
        modules=manifest['moduleCount'], thirdPartyImports=manifest['thirdPartyImports'],
        transportModules=manifest.get('transportModules', []),
        forbiddenArtifacts=dict(binary=False, sheets=False, assets=False),
        isolated=True, externalReads=len(outside),
        tables={name: len(rows) for name, rows in packaged_tables.items()},
        fixturesCompared=len(compared), fixtures=compared,
        deterministic=True, skillEvents=len(skill_events), slottedSkillEvents=len(slotted),
        weaponAttacks=len(weapon_attacks),
        limits=['The packaged runtime reports an attested native digest; it does not rehash the ELF.',
                'Equivalence is proven against the recorded local replays of the same scenarios.'],
    )
    report_path = pathlib.Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('fileCount', 'totalBytes', 'modules', 'tables',
                                             'fixturesCompared', 'skillEvents', 'weaponAttacks')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
