"""One runtime data root for the combat runner (PASS 16 COMMAND 16.7).

The runner reads its inputs from exactly one place, chosen by what is present next to this module:

  packaged (deployable)  `combat_runtime_data/`
                         runtime-manifest.json  - attested source identities
                         data/*.json            - the recovered combat data the runner loads
                         tables/*.json          - the original sheet rows the runner needs

  local recovery workspace
                         RE-evidence/20260912-combat, RE-evidence/20260911-treasure/.../English.lproj,
                         RE-evidence/G2.1/... - the original evidence, used by the recovery tooling

The packaged layout never contains the game binary or the original sheet text files. See
`build_combat_runtime_package.py` for the export rules and `check_combat_runtime_package.py` for the
isolated proof.
"""
import hashlib
import json
import pickle
import threading
from pathlib import Path

RUNNER_SCHEMA = 'ka-combat-runtime-1'
PACKAGE_DIR = Path(__file__).resolve().with_name('combat_runtime_data')
MANIFEST_NAME = 'runtime-manifest.json'

# Local recovery workspace layout (only used when the package is absent).
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = WORKSPACE_ROOT / 'RE-evidence/20260912-combat'
TABLES_DIR = WORKSPACE_ROOT / 'RE-evidence/20260911-treasure/xls-original/English.lproj'
NATIVE_DIR = WORKSPACE_ROOT / 'RE-evidence/G2.1/39257e72291d'

# Files the runner loads at run time (not provenance-only material).
RUNTIME_DATA_FILES = ('weapon-skill-profiles.json', 'encounters.json', 'formation-rules.json',
                      'skill-combat-constants.json')
RUNTIME_TABLES = ('Monster', 'Treasure')

_DATA_CACHE = {}
_DATA_CACHE_LOCK = threading.Lock()


def packaged() -> bool:
    """True when this module sits inside a built package."""
    return (PACKAGE_DIR / MANIFEST_NAME).is_file()


def mode() -> str:
    return 'package' if packaged() else 'recovery-workspace'


def manifest() -> dict:
    if not packaged():
        return dict(schema=RUNNER_SCHEMA, mode='recovery-workspace',
                    note='running against the original recovery workspace; no attested package manifest present')
    return json.loads((PACKAGE_DIR / MANIFEST_NAME).read_text(encoding='utf-8'))


def attested() -> dict:
    """Attested source identities (empty dict in the recovery workspace)."""
    return dict(manifest().get('attested', {}))


def data_path(name: str) -> Path:
    """Path of one runtime data file, package first."""
    if packaged():
        path = PACKAGE_DIR / 'data' / name
        if not path.is_file():
            raise FileNotFoundError(f'{name} is missing from the runtime package: {path}')
        return path
    return EVIDENCE_DIR / name


def load_data(name: str):
    """Load the current JSON file and return an independent mutable object graph."""
    path = data_path(name)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).digest()
    key = path.resolve()
    with _DATA_CACHE_LOCK:
        cached = _DATA_CACHE.get(key)
        if cached is None or cached[0] != digest:
            value = json.loads(raw.decode('utf-8'))
            cached = (digest, pickle.dumps(value, protocol=5))
            _DATA_CACHE[key] = cached
        snapshot = cached[1]
    # Unpickling returns a fresh mutable object graph. Callers may freely edit their
    # copy without changing the cached data or the value returned by another call.
    return pickle.loads(snapshot)


def clear_data_cache() -> None:
    """Discard parsed runtime data snapshots, for callers that manage data updates."""
    with _DATA_CACHE_LOCK:
        _DATA_CACHE.clear()


def package_files() -> dict:
    """sha256 of every file in the package (relative POSIX path -> digest)."""
    files = {}
    for path in sorted(p for p in PACKAGE_DIR.rglob('*') if p.is_file()):
        files[path.relative_to(PACKAGE_DIR).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def table(name: str) -> dict:
    """Original rows for one sheet table, exactly as the recovery reader returns them.

    Package: the exported rows (id -> positional row list) from `tables/<name>.json`.
    Workspace: the original `<name>.txt` under the English.lproj source directory.
    """
    if packaged():
        path = PACKAGE_DIR / 'tables' / f'{name.lower()}.json'
        if not path.is_file():
            raise FileNotFoundError(f'table {name} is missing from the runtime package: {path}')
        payload = json.loads(path.read_text(encoding='utf-8'))
        return {int(row['id']): list(row['row']) for row in payload['rows']}
    source = TABLES_DIR / f'{name}.txt'
    return {int(r[0]): r for line in source.read_text(encoding='utf-8-sig').splitlines()
            if (r := line.split('\t'))[0].isdigit()}


def table_path(name: str) -> Path:
    if packaged():
        return PACKAGE_DIR / 'tables' / f'{name.lower()}.json'
    return TABLES_DIR / f'{name}.txt'
