"""Resolve preserved native resume state to A without changing historical evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
from functools import lru_cache
from pathlib import Path


class ResumeRelocationError(RuntimeError):
    """A stored state cannot be safely resolved within the active A allocation."""


DEFAULT_MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_CONFIGURABLE_STATE_BYTES = 512 * 1024 * 1024


def _max_state_bytes():
    raw = os.environ.get('KA_NATIVE_RESUME_MAX_STATE_BYTES')
    if raw is None:
        return DEFAULT_MAX_STATE_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ResumeRelocationError('KA_NATIVE_RESUME_MAX_STATE_BYTES must be an integer') from exc
    if value <= 0 or value > MAX_CONFIGURABLE_STATE_BYTES:
        raise ResumeRelocationError(
            f'KA_NATIVE_RESUME_MAX_STATE_BYTES must be in 1..{MAX_CONFIGURABLE_STATE_BYTES}')
    return value


def _norm(path):
    return ntpath.normcase(ntpath.normpath(str(path)))


def _relative_under(path, root):
    value = _norm(path)
    base = _norm(root)
    if value == base:
        return ''
    prefix = base.rstrip('\\') + '\\'
    if not value.startswith(prefix):
        return None
    try:
        relative = ntpath.relpath(ntpath.normpath(str(path)), ntpath.normpath(str(root)))
    except ValueError:
        return None
    if relative == '..' or relative.startswith('..\\') or ntpath.isabs(relative):
        return None
    return relative


def rebase_exact_root(path, old_root, new_root):
    """Map a path only when it is inside the exact old root; otherwise return None."""
    relative = _relative_under(path, old_root)
    if relative is None:
        return None
    return str(new_root) if not relative else ntpath.join(str(new_root), relative)


def _roots(relocation_map_path, project_root, local_app_data):
    try:
        relocation = json.loads(Path(relocation_map_path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ResumeRelocationError(f'Cannot read A relocation map: {exc}') from exc
    if relocation.get('schema') != 'ka-optimizer-relocation-map/1':
        raise ResumeRelocationError('A relocation map schema is unsupported')
    root = relocation.get('root')
    if not isinstance(root, str) or not ntpath.isabs(root):
        raise ResumeRelocationError('A relocation map has no absolute A root')
    mappings = relocation.get('sourceMappings')
    if not isinstance(mappings, list):
        raise ResumeRelocationError('A relocation map has no source mappings')
    project = Path(project_root).resolve(strict=True)
    local = Path(local_app_data).resolve(strict=True)
    product_data = local / 'KingdomAdventurersOptimizer'
    old_project = old_data = None
    new_project = new_data = None
    for row in mappings:
        if not isinstance(row, dict):
            continue
        old, active = row.get('old'), row.get('active')
        if not isinstance(old, str) or not isinstance(active, str):
            continue
        if _norm(active) == _norm(project):
            old_project, new_project = old, active
        if _norm(active) == _norm(product_data):
            old_data, new_data = old, active
    if not old_project or not old_data:
        raise ResumeRelocationError('Relocation map lacks exact project or AppData product roots')
    if _norm(new_project) != _norm(project) or _norm(new_data) != _norm(product_data):
        raise ResumeRelocationError('Relocation map A roots do not match the running installation')
    return {
        'optimizer_root': Path(root).resolve(strict=True),
        'old_project': old_project,
        'new_project': str(project),
        'old_data': old_data,
        'new_data': str(product_data),
    }


@lru_cache(maxsize=2)
def _archive_index(manifest_path):
    index = {}
    try:
        with Path(manifest_path).open('r', encoding='utf-8-sig') as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get('path'), str):
                    raise ResumeRelocationError(f'Invalid preservation manifest row {line_number}')
                index[_norm(row['path'])] = row
    except (OSError, ValueError) as exc:
        raise ResumeRelocationError(f'Cannot read A AppData preservation ledger: {exc}') from exc
    return index


def _archive_row(relative, manifest_path):
    row = _archive_index(str(manifest_path)).get(_norm(relative))
    if not row or row.get('result') != 'ok' or row.get('kind') != 'file':
        raise ResumeRelocationError(f'No verified A preservation row for {relative}')
    source_hash = str(row.get('source', '')).lower()
    destination_hash = str(row.get('destination', '')).lower()
    if len(source_hash) != 64 or source_hash != destination_hash:
        raise ResumeRelocationError(f'Preservation ledger is not a matching SHA copy for {relative}')
    return row, destination_hash


def _hash_and_newlines(path):
    digest = hashlib.sha256()
    newlines = size = 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            newlines += block.count(b'\n')
            size += len(block)
    return digest.hexdigest(), newlines, size


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _inside_existing(path, root, description):
    try:
        resolved_root = Path(root).resolve(strict=True)
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ResumeRelocationError(f'{description} is missing or unreadable on A: {exc}') from exc
    if _relative_under(resolved, resolved_root) is None:
        raise ResumeRelocationError(f'{description} resolves outside its allowed A root')
    return resolved


def _check_archive_digest(path, relative, manifest_path, actual_hash, actual_size,
                          expected_hash=None):
    row, ledger_hash = _archive_row(relative, manifest_path)
    if actual_size != int(row.get('size', -1)):
        raise ResumeRelocationError(f'A preserved size differs from the ledger for {relative}')
    if str(actual_hash).lower() != ledger_hash:
        raise ResumeRelocationError(f'A preserved SHA-256 differs from the ledger for {relative}')
    if expected_hash is not None and str(actual_hash).lower() != str(expected_hash).lower():
        raise ResumeRelocationError(f'Checkpoint-declared SHA-256 differs for {relative}')
    return ledger_hash


def _map_state_pointer(stored_path, roots, appdata_manifest, max_state_bytes):
    if not isinstance(stored_path, str) or not stored_path or not ntpath.isabs(stored_path):
        raise ResumeRelocationError('Stored native checkpoint path is not an absolute path')
    legacy_relative = _relative_under(stored_path, roots['old_data'])
    if legacy_relative is not None:
        mapped = Path(roots['new_data']) / legacy_relative
        mapped = _inside_existing(mapped, roots['new_data'], 'Mapped native checkpoint')
        relative = legacy_relative or '.'
        row, expected_hash = _archive_row(relative, appdata_manifest)
        if mapped.stat().st_size != int(row.get('size', -1)):
            raise ResumeRelocationError(f'A preserved size differs from the ledger for {relative}')
        legacy = True
    elif _relative_under(stored_path, roots['new_data']) is not None:
        mapped = _inside_existing(stored_path, roots['new_data'], 'A native checkpoint')
        relative = None
        expected_hash = None
        legacy = False
    else:
        raise ResumeRelocationError(
            'Stored native checkpoint is outside the exact preserved C AppData root and A runtime root; refusing fallback')
    size = mapped.stat().st_size
    if size <= 0 or size > max_state_bytes:
        raise ResumeRelocationError(
            f'Mapped native checkpoint size {size} exceeds configured limit {max_state_bytes} bytes; '
            'set KA_NATIVE_RESUME_MAX_STATE_BYTES to a reviewed value if a larger preserved checkpoint is required')
    if not mapped.is_file() or mapped.name.lower() not in ('search-state.json', 'checkpoint.json'):
        raise ResumeRelocationError('Mapped native checkpoint is not a supported state file')
    return mapped, legacy, relative, expected_hash


def _map_appdata_reference(value, expected_hash, record_count, roots, appdata_manifest, label):
    if not isinstance(value, str) or not value or not ntpath.isabs(value):
        raise ResumeRelocationError(f'{label} path is not absolute')
    old_relative = _relative_under(value, roots['old_data'])
    if old_relative is not None:
        mapped = Path(roots['new_data']) / old_relative
        mapped = _inside_existing(mapped, roots['new_data'], label)
        actual_hash, actual_rows, actual_size = _hash_and_newlines(mapped)
        _check_archive_digest(mapped, old_relative or '.', appdata_manifest,
                              actual_hash, actual_size, expected_hash)
        if record_count is not None and actual_rows != record_count:
            raise ResumeRelocationError(f'{label} record count differs from the checkpoint')
        return mapped, actual_hash, actual_rows, True
    if _relative_under(value, roots['new_data']) is not None:
        mapped = _inside_existing(value, roots['new_data'], label)
        actual_hash, actual_rows, _actual_size = _hash_and_newlines(mapped)
        if actual_hash.lower() != str(expected_hash).lower():
            raise ResumeRelocationError(f'{label} SHA-256 differs from the checkpoint')
        if record_count is not None and actual_rows != record_count:
            raise ResumeRelocationError(f'{label} record count differs from the checkpoint')
        return mapped, actual_hash, actual_rows, False
    raise ResumeRelocationError(f'{label} is outside the exact preserved C AppData root and A runtime root')


def _map_project_file(value, expected_hash, roots, label):
    if not isinstance(value, str) or not value or not ntpath.isabs(value):
        raise ResumeRelocationError(f'{label} path is not absolute')
    old_relative = _relative_under(value, roots['old_project'])
    if old_relative is not None:
        mapped = Path(roots['new_project']) / old_relative
    elif _relative_under(value, roots['new_project']) is not None:
        mapped = Path(value)
    else:
        raise ResumeRelocationError(f'{label} is outside the exact preserved C project root and A project root')
    mapped = _inside_existing(mapped, roots['new_project'], label)
    actual_hash, _, _ = _hash_and_newlines(mapped)
    if not expected_hash or actual_hash.lower() != str(expected_hash).lower():
        raise ResumeRelocationError(f'{label} SHA-256 differs from the checkpoint policy')
    return mapped, actual_hash


def _policy_without_operational_paths(policy):
    value = copy.deepcopy(policy)
    value.pop('statBoundsSource', None)
    bounds = value.get('statBounds')
    if isinstance(bounds, dict):
        bounds.pop('source', None)
    mutations = value.get('mutations')
    if isinstance(mutations, list):
        for row in mutations:
            if isinstance(row, dict):
                row.pop('boundsSource', None)
    return value


def _state_without_operational_paths(state):
    value = copy.deepcopy(state)
    policy = value.get('learningPolicy')
    if isinstance(policy, dict):
        value['learningPolicy'] = _policy_without_operational_paths(policy)
    journals = value.get('sourceJournals')
    if isinstance(journals, list):
        for row in journals:
            if isinstance(row, dict):
                row.pop('path', None)
    return value


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def prepare_resume_state(stored_path, language, run_folder, project_root,
                         local_app_data, relocation_map_path, appdata_manifest_path):
    """Return an A-only state path and data, with narrowly rebased operational fields."""
    if stored_path is None or stored_path == '':
        return None, None, None
    roots = _roots(relocation_map_path, project_root, local_app_data)
    max_state_bytes = _max_state_bytes()
    source_path, pointer_was_legacy, pointer_relative, baseline_hash = _map_state_pointer(
        stored_path, roots, appdata_manifest_path, max_state_bytes)
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if baseline_hash is not None and source_hash.lower() != baseline_hash.lower():
        raise ResumeRelocationError('Mapped checkpoint SHA-256 differs from the preserved A baseline')
    original = json.loads(source_bytes.decode('utf-8-sig'))
    if not isinstance(original, dict):
        raise ResumeRelocationError('Mapped native checkpoint is not a JSON object')
    state = copy.deepcopy(original)
    policy_rewrites = []
    journal_rewrites = []
    legacy_paths_verified = []

    def rewrite_policy(container, key, pointer, expected_hash):
        value = container.get(key)
        if value is None:
            return
        mapped, actual_hash = _map_project_file(
            value, expected_hash, roots, f'learningPolicy{pointer}')
        new_value = str(mapped)
        if new_value != value:
            container[key] = new_value
            policy_rewrites.append({
                'jsonPointer': f'/learningPolicy{pointer}',
                'oldPath': value,
                'newPath': new_value,
                'sha256': actual_hash,
            })
        if _relative_under(value, roots['old_project']) is not None:
            legacy_paths_verified.append(f'/learningPolicy{pointer}')

    if language == 'cpp':
        policy = state.get('learningPolicy')
        if isinstance(policy, dict):
            expected_bounds_hash = policy.get('statBoundsSha256')
            rewrite_policy(policy, 'statBoundsSource', '/statBoundsSource', expected_bounds_hash)
            bounds = policy.get('statBounds')
            if isinstance(bounds, dict):
                rewrite_policy(bounds, 'source', '/statBounds/source',
                               bounds.get('sha256', expected_bounds_hash))
            mutations = policy.get('mutations')
            if isinstance(mutations, list):
                for index, mutation in enumerate(mutations):
                    if isinstance(mutation, dict):
                        rewrite_policy(mutation, 'boundsSource',
                                       f'/mutations/{index}/boundsSource',
                                       mutation.get('boundsSha256', expected_bounds_hash))

    journals = state.get('sourceJournals')
    if isinstance(journals, list):
        for index, journal in enumerate(journals):
            if not isinstance(journal, dict):
                raise ResumeRelocationError(f'sourceJournals[{index}] is malformed')
            old_path = journal.get('path')
            mapped, actual_hash, rows, was_legacy = _map_appdata_reference(
                old_path, journal.get('sha256'), journal.get('recordCount'),
                roots, appdata_manifest_path, f'sourceJournals[{index}]')
            new_path = str(mapped)
            if new_path != old_path:
                journal['path'] = new_path
                journal_rewrites.append({
                    'index': index,
                    'oldPath': old_path,
                    'newPath': new_path,
                    'sha256': actual_hash,
                    'recordCount': rows,
                })
            if was_legacy:
                legacy_paths_verified.append(f'/sourceJournals/{index}/path')

    translated = bool(policy_rewrites or journal_rewrites)
    if translated:
        before_projection = _state_without_operational_paths(original)
        after_projection = _state_without_operational_paths(state)
        if before_projection != after_projection:
            raise ResumeRelocationError('Resume relocation changed a non-path checkpoint field')
        if state.get('provenance') != original.get('provenance'):
            raise ResumeRelocationError('Resume relocation changed immutable provenance')
        run_folder = Path(run_folder)
        run_parent = _inside_existing(
            run_folder.parent, roots['optimizer_root'], 'Run folder parent')
        if run_folder.exists() or run_folder.is_symlink():
            raise ResumeRelocationError(
                'Native output folder already exists before resume preparation')

        staging_root = run_parent / 'resume-staging'
        if staging_root.exists() or staging_root.is_symlink():
            staging_root = _inside_existing(
                staging_root, roots['optimizer_root'], 'Resume staging root')
        else:
            staging_root.mkdir()
            staging_root = _inside_existing(
                staging_root, roots['optimizer_root'], 'Resume staging root')
        staging_folder = staging_root / run_folder.name
        if staging_folder.exists() or staging_folder.is_symlink():
            raise ResumeRelocationError(
                'Refusing to overwrite an existing resume staging folder')
        staging_folder.mkdir()
        staging_folder = _inside_existing(
            staging_folder, roots['optimizer_root'], 'Resume staging folder')
        derived_path = staging_folder / 'operational-resume-state.json'
        if derived_path.exists():
            raise ResumeRelocationError('Refusing to overwrite an existing derived checkpoint')
        _write_json_atomic(derived_path, state)
        derived_hash = _sha256_file(derived_path)
    else:
        derived_path = None
        derived_hash = source_hash

    report = {
        'schema': 'ka-native-resume-relocation/1',
        'language': language,
        'sourcePointer': stored_path,
        'mappedSourcePath': str(source_path),
        'sourceWasLegacyCPath': pointer_was_legacy,
        'sourceRelativePath': pointer_relative,
        'sourceCheckpointSha256': source_hash,
        'maxCheckpointBytes': max_state_bytes,
        'derivedCheckpointPath': str(derived_path) if derived_path else None,
        'derivedCheckpointSha256': derived_hash,
        'pathRewrites': len(policy_rewrites) + len(journal_rewrites),
        'policyPathRewrites': policy_rewrites,
        'sourceJournalPathRewrites': journal_rewrites,
        'legacyPathIntegrityChecks': legacy_paths_verified,
        'historicalProvenanceUnchanged': state.get('provenance') == original.get('provenance'),
        'nonPathCheckpointFieldsUnchanged':
            _state_without_operational_paths(state) == _state_without_operational_paths(original),
        'nativeCompatibilityGuards': 'unchanged; native checkpoint and source-journal integrity checks remain enabled',
        'cPathFallbackUsed': False,
    }
    if translated:
        report_path = staging_folder / 'resume-relocation.json'
        _write_json_atomic(report_path, report)
        report['reportPath'] = str(report_path)
    return derived_path or source_path, state, report
