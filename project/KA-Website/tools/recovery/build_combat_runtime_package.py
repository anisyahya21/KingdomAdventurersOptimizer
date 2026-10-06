"""Build the self-contained deployable combat-runner package (PASS 16 COMMAND 16.7).

The package contains only what a run needs:

    combat_runtime_data/runtime-manifest.json   attested source identities + package inventory
    combat_runtime_data/data/*.json             the recovered combat data the runner loads
    combat_runtime_data/tables/*.json           the original sheet rows the runner needs
    <python modules>                            the traced import closure of the runtime entry points
                                                (combat_replay_export, combat_evaluation, combat_search)
                                                plus the declared transport helpers the endpoint imports
                                                from the packaged directory (TRANSPORT_MODULES)

It never contains the game binary, the original sheet text files, APK assets, captures or evidence
trees. Provenance is attested from the original evidence at build time; the deployed runtime reports
that attestation instead of rehashing a 51.8 MB ELF.

Usage:
    python build_combat_runtime_package.py [--out DIR] [--scenario FILE] [--trace-report FILE]
"""
import argparse
import ast
import hashlib
import io
import json
import os
import pathlib
import shutil
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
WORKSPACE = TOOLS.parents[2]
EVIDENCE = WORKSPACE / 'RE-evidence/20260912-combat'
TABLES_DIR = WORKSPACE / 'RE-evidence/20260911-treasure/xls-original/English.lproj'
NATIVE_DIR = WORKSPACE / 'RE-evidence/G2.1/39257e72291d'
REGISTRY = WORKSPACE / 'RE-evidence/20260919-combat-registry'
INDEX_MANIFEST = WORKSPACE / 'RE-evidence/20260920-native-index/build/index-manifest.json'

PACKAGE_SCHEMA = 'ka-combat-runtime-1'
RUNTIME_DATA_FILES = ('weapon-skill-profiles.json', 'encounters.json', 'formation-rules.json',
                      'skill-combat-constants.json', 'animation-resources.json',
                      'effect-resource-checks.json')
RUNTIME_TABLES = ('Monster', 'Treasure')
DEFAULT_SCENARIO = (WORKSPACE / 'RE-evidence/20260919-configurable-battle-setup/'
                               'run-battle-16.5/scenarios/R.scenario.json')
DEFAULT_OUT = (WORKSPACE / 'RE-evidence/20260919-configurable-battle-setup/'
                          'deployment-16.7/bundle')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


RUNTIME_ENTRY_POINTS = ('combat_replay_export', 'combat_evaluation', 'combat_search')

# Transport helpers the endpoint imports from the packaged runtime directory but which are not reached
# by the runner's own import closure. They are shipped in the package, so they are inventoried and
# attested with it (	ransportModules in the manifest) instead of appearing as an unlisted extra.
TRANSPORT_MODULES = ('combat_interaction',)


def import_closure(entries=RUNTIME_ENTRY_POINTS) -> list:
    """Every recovery module the entry points can import (transitively)."""
    seen, stack = set(), list([entries] if isinstance(entries, str) else entries)
    while stack:
        name = stack.pop()
        path = TOOLS / f'{name}.py'
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stack.extend(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                stack.append(node.module.split('.')[0])
    return sorted(seen)


def third_party_imports(modules) -> list:
    """Imports in the closure that are neither stdlib nor recovery modules."""
    stdlib = set(sys.stdlib_module_names)
    external = set()
    for name in modules:
        tree = ast.parse((TOOLS / f'{name}.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            roots = [alias.name.split('.')[0] for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else [])
            for root in roots:
                if root in stdlib or (TOOLS / f'{root}.py').is_file():
                    continue
                external.add(root)
    return sorted(external)


def trace_runtime(modules, scenario_path, trace_report=None):
    """Hook every real file open during one run and classify the accesses."""
    opened = []
    real_io_open = io.open

    def spy(file, mode='r', *args, **kwargs):
        # Only real paths are interesting. `platform.platform()` spawns a helper process on Windows
        # and reads its pipe through an integer file descriptor, which is not a file the runner read.
        if isinstance(file, (str, bytes, os.PathLike)):
            opened.append((str(file), mode))
        return real_io_open(file, mode, *args, **kwargs)

    io.open = spy
    try:
        sys.path.insert(0, str(TOOLS))
        import combat_replay_export  # noqa: F401  (imported for its side effects on the closure)
        scenario = json.loads(pathlib.Path(scenario_path).read_text(encoding='utf-8'))
        replay = combat_replay_export.export_replay(scenario, include_events=True)
    finally:
        io.open = real_io_open

    workspace = str(WORKSPACE)
    classes = dict(code=[], runtime_data=[], runtime_table=[], provenance=[], scenario=[], write=[], outside=[])
    for path, mode in opened:
        if any(ch in mode for ch in 'wax+'):
            classes['write'].append(path)
            continue
        stem = pathlib.Path(path).stem
        if not path.startswith(workspace):
            classes['outside'].append(path)
        elif stem in modules and path.endswith('.py'):
            classes['code'].append(path)
        elif path.endswith('.py'):
            # Not imported at run time: run_manifest hashes every combat_*.py plus the two recovery
            # modules for provenance. The packaged runtime hashes its own files instead.
            classes['provenance'].append(path)
        elif any(path.endswith(name) for name in RUNTIME_DATA_FILES):
            classes['runtime_data'].append(path)
        elif any(path.endswith(f'{name}.txt') for name in RUNTIME_TABLES):
            classes['runtime_table'].append(path)
        elif path.endswith('.scenario.json'):
            classes['scenario'].append(path)
        else:
            classes['provenance'].append(path)
    report = dict(
        units=len(replay['units']), events=len(replay['events']),
        classes={key: sorted({p[len(workspace) + 1:].replace('\\', '/') if p.startswith(workspace) else p
                              for p in value}) for key, value in classes.items()},
    )
    report['counts'] = {key: len(value) for key, value in report['classes'].items()}
    if trace_report:
        pathlib.Path(trace_report).write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


def export_table(name: str) -> dict:
    source = TABLES_DIR / f'{name}.txt'
    rows = {int(r[0]): r for line in source.read_text(encoding='utf-8-sig').splitlines()
            if (r := line.split('\t'))[0].isdigit()}
    return dict(
        schema='ka-combat-runtime-table-1',
        table=name,
        sourceFile=f'RE-evidence/20260911-treasure/xls-original/English.lproj/{name}.txt',
        sourceSha256=sha256_file(source),
        rowFields='positional columns of the original sheet row, verbatim strings',
        rowCount=len(rows),
        rows=[dict(id=row_id, row=list(row)) for row_id, row in sorted(rows.items())],
    )


def attested_provenance(package_files_hint=None) -> dict:
    def digest_of(path, key=None):
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload.get(key) if key else None

    def nested(path, *keys):
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding='utf-8'))
        for key in keys:
            if not isinstance(payload, dict):
                return None
            payload = payload.get(key)
        return payload

    sheets = {p.name: sha256_file(p) for p in sorted(TABLES_DIR.glob('*.txt'))}
    return dict(
        nativeSha256=sha256_file(NATIVE_DIR / 'inputs/libil2cpp.so'),
        nativeSource='RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so',
        sheetDigests=sheets,
        sheetBundleSha256=sha256_bytes(json.dumps(sheets, sort_keys=True).encode()),
        sheetCount=len(sheets),
        evidenceDigests={name: sha256_file(EVIDENCE / name) for name in RUNTIME_DATA_FILES},
        combatRegistryStableDigest=nested(REGISTRY / 'registry-build-report.json', 'stableDigest'),
        combatRegistryNativeIndex=nested(REGISTRY / 'combat-registry.json', 'nativeIndex', 'contentDigest'),
        combatKeysStableDigest=nested(REGISTRY / 'combat-keys.json', 'stableDigest'),
        nativeIndexContentDigest=digest_of(INDEX_MANIFEST, 'contentDigest'),
        runnerSchemaVersion='ka-battle-replay-1',
        packageSchema=PACKAGE_SCHEMA,
        note=('SOURCE VERIFIED AT PACKAGE BUILD: these digests were measured against the original '
              'evidence while this package was exported. SOURCE PRESENT AND REHASHED AT EXECUTION is '
              'the local recovery-workspace behaviour, which reopens the 51.8 MB ELF every run; the '
              'packaged runtime reports the attestation instead and never needs the binary.'),
    )


def build(out_dir, scenario_path=DEFAULT_SCENARIO, trace_report=None) -> dict:
    out = pathlib.Path(out_dir)
    package = out / 'combat_runtime_data'
    if out.exists():
        shutil.rmtree(out)
    (package / 'data').mkdir(parents=True)
    (package / 'tables').mkdir(parents=True)

    closure = import_closure(RUNTIME_ENTRY_POINTS)
    modules = sorted(set(closure) | set(TRANSPORT_MODULES))
    external = third_party_imports(modules)
    if external:
        raise SystemExit(f'runtime closure still needs third-party packages: {external}')
    # Classify opened files against the real import closure: a shipped transport helper is inventoried
    # in modules but is not imported by the runner, so its read belongs to provenance, not code.
    trace = trace_runtime(closure, scenario_path, trace_report)
    unclassified = [p for p in trace['classes']['provenance']
                    if not (p.endswith('libil2cpp.so') or p.endswith('.txt') or p.endswith('.py')
                            or p.endswith('.json'))]
    if unclassified:
        raise SystemExit(f'trace found runtime reads that are neither data nor known provenance: {unclassified}')
    if trace['classes']['outside'] or trace['classes']['write']:
        raise SystemExit(f'trace found writes/outside reads: {trace["classes"]}')

    for name in modules:
        shutil.copy2(TOOLS / f'{name}.py', out / f'{name}.py')
    for name in RUNTIME_DATA_FILES:
        shutil.copy2(EVIDENCE / name, package / 'data' / name)
    tables = {name: export_table(name) for name in RUNTIME_TABLES}
    for name, payload in tables.items():
        (package / 'tables' / f'{name.lower()}.json').write_text(json.dumps(payload, indent=1, sort_keys=True) + '\n',
                                                                encoding='utf-8')

    files = {p.relative_to(out).as_posix(): sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file()}
    manifest = dict(
        schema=PACKAGE_SCHEMA,
        runnerSchemaVersion='ka-battle-replay-1',
        entryPoint='combat_replay_export.py',
        entryPoints=[f'{name}.py' for name in RUNTIME_ENTRY_POINTS],
        transportModules=[f'{name}.py' for name in TRANSPORT_MODULES],
        attested=attested_provenance(),
        runtimeDataFiles={name: sha256_file(package / 'data' / name) for name in RUNTIME_DATA_FILES},
        runtimeTables={name: dict(file=f'tables/{name.lower()}.json', rows=payload['rowCount'],
                                  sourceSha256=payload['sourceSha256'])
                       for name, payload in sorted(tables.items())},
        modules=modules,
        moduleCount=len(modules),
        thirdPartyImports=external,
        dependencyTrace=trace,
        packageFiles=files,
        fileCount=len(files),
        totalBytes=sum((out / p).stat().st_size for p in files),
        contentSha256=sha256_bytes(json.dumps(files, sort_keys=True).encode()),
    )
    (package / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n',
                                                   encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    parser.add_argument('--scenario', default=str(DEFAULT_SCENARIO))
    parser.add_argument('--trace-report')
    args = parser.parse_args(argv)
    manifest = build(args.out, args.scenario, args.trace_report)
    print(json.dumps(dict(out=str(pathlib.Path(args.out).resolve()), fileCount=manifest['fileCount'],
                          totalBytes=manifest['totalBytes'], moduleCount=manifest['moduleCount'],
                          thirdPartyImports=manifest['thirdPartyImports'],
                          runtimeTables={k: v['rows'] for k, v in manifest['runtimeTables'].items()},
                          traceCounts=manifest['dependencyTrace']['counts']), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
