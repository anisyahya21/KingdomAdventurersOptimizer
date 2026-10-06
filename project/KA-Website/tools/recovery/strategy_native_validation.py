"""Integrity checks for native strategy-library scopes and records.

This module validates local engine assets and the exact identity fields emitted by the
Rust, Go and C++ journals. It deliberately does not require a coordinator/test attestation:
ordinary real strategy runs are evidence when their run purpose and engine identity match.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')
_REAL_PURPOSES = {
    'strategy', 'search', 'evaluate',
    'production-strategy', 'production-search', 'production-evaluate',
    'strategy-search', 'strategy-evaluate', 'strategy-search-evaluate', 'production',
}


def canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _hash(value, label):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f'{label} must be a 64-character SHA-256 digest')
    return value.lower()


def _asset_pair(block, path_names, hash_names, label):
    if isinstance(block, dict):
        path = next((block.get(key) for key in path_names if block.get(key)), None)
        digest = next((block.get(key) for key in hash_names if block.get(key)), None)
    else:
        path, digest = block, None
    if not path or not digest:
        raise ValueError(f'{label} requires a path and SHA-256 digest')
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'{label} is not a file: {path}')
    expected = _hash(digest, f'{label} SHA-256')
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f'{label} changed: expected {expected}, found {actual}')
    return dict(path=str(path), sha256=actual)


def validate_engine_assets(language, assets):
    """Return verified content identities from validate_engine/build-manifest metadata.

    The canonical shape is strategy_native_assets.validate_engine(): language, revision,
    executable/kernel objects with Path + sha256, and named staticInputs. Manifest-style
    string paths are accepted for standalone callers as well.
    """
    if not isinstance(assets, dict):
        raise ValueError('production scope requires engineAssets metadata from validate_engine')
    manifest = assets.get('manifest') or assets.get('buildManifest')
    if isinstance(manifest, dict):
        assets = dict(manifest, **{k: v for k, v in assets.items()
                                  if k not in ('manifest', 'buildManifest')})
    found_language = str(assets.get('language', assets.get('engine', ''))).lower()
    if found_language != str(language).lower():
        raise ValueError('engineAssets language does not match the selected engine')
    revision = assets.get('revision')
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError('engineAssets requires a nonempty frozen revision')
    executable_block = assets.get('executable')
    executable = _asset_pair(
        executable_block if isinstance(executable_block, dict) else {
            'path': assets.get('executablePath'),
            'sha256': assets.get('executableSha256', assets.get('executableSHA256'))},
        ('path', 'executablePath'), ('sha256', 'executableSha256', 'executableSHA256'),
        'native executable')
    kernel_block = assets.get('kernel')
    kernel = _asset_pair(
        kernel_block if isinstance(kernel_block, dict) else {
            'path': assets.get('kernelPath'),
            'sha256': assets.get('kernelSha256', assets.get('currentKernelSha256'))},
        ('path', 'kernelPath'), ('sha256', 'kernelSha256', 'currentKernelSha256'),
        'battle kernel')
    static_raw = assets.get('staticInputs')
    if not isinstance(static_raw, dict) or not static_raw:
        raise ValueError('engineAssets requires named staticInputs')
    static = {}
    for name, value in sorted(static_raw.items()):
        if not isinstance(name, str) or not name:
            raise ValueError('staticInputs names must be nonempty strings')
        pair = _asset_pair(value, ('path',), ('sha256', 'sha'), f'static input {name}')
        static[name] = pair
    optional_hashes = {}
    for key in ('sourceSetSha256', 'sourceSha256', 'catalogSha256', 'factsSha256',
                'tablesSha256', 'policySemanticsVersion'):
        value = assets.get(key)
        if value is None:
            continue
        if key.endswith('Sha256') and isinstance(value, str):
            optional_hashes[key] = _hash(value, key)
        elif key == 'sourceSha256' and isinstance(value, dict):
            optional_hashes[key] = value
        elif key == 'policySemanticsVersion' and isinstance(value, str):
            optional_hashes[key] = value
    normalized = dict(language=found_language, revision=revision,
                       executable=executable, kernel=kernel, staticInputs=static,
                       metadata=optional_hashes)
    normalized['contentSha256'] = canonical_digest({
        'language': found_language, 'revision': revision,
        'executableSha256': executable['sha256'], 'kernelSha256': kernel['sha256'],
        'staticInputs': {name: value['sha256'] for name, value in static.items()},
        'metadata': optional_hashes,
    })
    return normalized


def normalize_real_purpose(value):
    """Accept only the declared strategy/search/evaluate purposes, never diagnostic labels."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace('_', '-').replace(' ', '-')
    return normalized if normalized in _REAL_PURPOSES else None


def purpose_operation(value):
    """Map an explicit strategy purpose to its operation family."""
    normalized = normalize_real_purpose(value)
    if normalized is None:
        return None
    if normalized == 'production':
        return 'production'
    if normalized in ('search', 'strategy-search', 'production-search'):
        return 'search'
    if normalized in ('evaluate', 'strategy-evaluate', 'production-evaluate'):
        return 'evaluate'
    return 'strategy'


def purposes_compatible(record_purpose_value, run_purpose_value, language):
    record_op = purpose_operation(record_purpose_value)
    run_op = purpose_operation(run_purpose_value)
    if run_op not in ('strategy', 'search', 'evaluate'):
        return False
    return (record_op == run_op or record_op == 'strategy' or run_op == 'strategy'
            or (str(language).lower() == 'go'
                and normalize_real_purpose(record_purpose_value) == 'production'))


def record_execution_mode(language, record):
    language = str(language).lower()
    if language == 'cpp':
        block = record.get('searchPolicy') if isinstance(record.get('searchPolicy'), dict) else {}
        return block.get('executionMode', record.get('executionMode'))
    return record.get('executionMode')


def record_purpose(language, record):
    language = str(language).lower()
    if language == 'rust':
        scope = record.get('scope') if isinstance(record.get('scope'), dict) else {}
        policy = scope.get('policy') if isinstance(scope.get('policy'), dict) else {}
        return policy.get('purpose', record.get('purpose'))
    if language == 'go':
        return record.get('testPurpose', record.get('purpose'))
    policy = record.get('searchPolicy') if isinstance(record.get('searchPolicy'), dict) else {}
    return policy.get('purpose', record.get('purpose'))


def verify_record_engine_identity(language, record, assets):
    """Bind a fresh journal record to verified executable, kernel and core-data hashes."""
    language = str(language).lower()
    if not isinstance(record, dict):
        raise ValueError('native record must be an object')
    if not isinstance(assets, dict) or 'contentSha256' not in assets:
        raise ValueError('verified engineAssets are required to validate a production record')
    if language == 'rust':
        scope = record.get('scope') if isinstance(record.get('scope'), dict) else {}
        identity = scope
    elif language == 'go':
        provenance = record.get('provenance') if isinstance(record.get('provenance'), dict) else {}
        identity = provenance.get('productionIdentity')
        if not isinstance(identity, dict):
            raise ValueError('Go record lacks provenance.productionIdentity')
    elif language == 'cpp':
        identity = record.get('provenance')
        if not isinstance(identity, dict):
            result = record.get('result') if isinstance(record.get('result'), dict) else {}
            identity = result.get('provenance')
        if not isinstance(identity, dict):
            raise ValueError('C++ record lacks native provenance')
    else:
        raise ValueError(f'unsupported native language: {language}')

    expected_exe = assets['executable']['sha256']
    expected_kernel = assets['kernel']['sha256']
    exe_key = 'executableSha256' if language != 'cpp' else 'actualExecutableSha256'
    kernel_key = 'kernelSha256' if language != 'cpp' else 'currentKernelSha256'
    if identity.get(exe_key) != expected_exe:
        raise ValueError(f'{language} record executable hash does not match verified engineAssets')
    if identity.get(kernel_key) != expected_kernel:
        raise ValueError(f'{language} record kernel hash does not match verified engineAssets')
    revision = identity.get('revision') or identity.get('sourceRevision')
    if revision is not None and revision != assets['revision']:
        raise ValueError(f'{language} record revision does not match verified engineAssets')

    static = assets['staticInputs']
    if language == 'rust':
        expected = next((v['sha256'] for n, v in static.items()
                         if any(tag in n.lower() for tag in ('catalog', 'table'))), None)
        actual = identity.get('catalogFileSha256', identity.get('catalogSha256'))
        if expected is None or actual != expected:
            raise ValueError('Rust record catalog hash does not match verified static input')
        src_expected = assets['metadata'].get('sourceSha256')
        if src_expected is not None and identity.get('sourceSha256') != src_expected:
            raise ValueError('Rust record source hashes do not match verified engineAssets')
    elif language == 'cpp':
        expected = next((v['sha256'] for n, v in static.items()
                         if any(tag in n.lower() for tag in ('table', 'catalog'))), None)
        if expected is None or identity.get('tablesSha256') != expected:
            raise ValueError('C++ record tables hash does not match verified static input')
    else:
        matched = False
        for record_key, tags in (('catalogSha256', ('catalog', 'table')),
                                 ('factsSha256', ('facts',))):
            expected = next((v['sha256'] for n, v in static.items()
                             if any(tag in n.lower() for tag in tags)), None)
            actual = identity.get(record_key)
            if expected is not None:
                if actual != expected:
                    raise ValueError(f'Go record {record_key} does not match verified static input')
                matched = True
        if not matched:
            raise ValueError('Go record lacks a matching verified catalog/facts hash')
        source_set = assets['metadata'].get('sourceSetSha256')
        if source_set is not None and identity.get('sourceSetSha256') != source_set:
            raise ValueError('Go record source-set hash does not match verified engineAssets')
    return dict(executableSha256=expected_exe, kernelSha256=expected_kernel,
                engineAssetsSha256=assets['contentSha256'], revision=assets['revision'])
