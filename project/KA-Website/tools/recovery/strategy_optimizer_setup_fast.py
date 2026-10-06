"""Exact headless SETUP acceleration for the strategy optimiser (repeated-battle runs).

This module is a *hanger*, not a combat implementation. It monkeypatches canonical setup-only
objects **in the calling headless worker process** and restores them exactly on `uninstall()`. No
canonical source file is edited, so `strategy_optimizer_adapter.provenance()` is byte-identical
with and without the accelerator, and the reference engine is never silently patched (nothing
imports this module).

Motivation (measured, see optimizer-setup-findings.md): a repeated battle re-pays a fixed setup
floor before the first tick. The dominant term is source attestation - the frozen 51.8 MB
`libil2cpp.so` is re-hashed every run by `combat_run_manifest.native_identity`, and
`run_manifest` re-hashes ~100 source/table/evidence files. Both are pure functions of immutable
bytes, so they are cached here; nothing gameplay- or seed-dependent is.

Cached (bounded, per-process, cleared on uninstall, no result memoisation):
  * `combat_run_manifest.native_identity` - the (digest, source) pair keyed by the resolved ELF's
    stat signature (size, mtime_ns, st_ino, st_dev). A drifted signature recomputes and
    re-validates, so source drift is still rejected by the canonical `NATIVE_SHA256` check and a
    mismatch still raises `ScenarioError`. The packaged attestation branch is delegated to the
    original function unchanged.
  * `combat_run_manifest.run_manifest` - the identical payload, with each source/table/evidence
    file digest read from a stat-keyed per-file cache instead of re-reading ~100 files. The
    returned `files` dict is rebuilt fresh every call, so callers never share mutable state;
    `normalizedScenarioSha256`, `contentSha256`, `support` and the seeds stay per-call.

Deliberately NOT cached, and why:
  * `combat_runtime_data.load_data` / `table` (and their module-bound copies): caching would
    require a `deepcopy` on every return (their rows are mutable), and the measured deepcopy cost
    exceeds the re-parse it replaces (weapon-skill-profiles 3.39 ms vs 2.54 ms parse; Treasure
    3.70 ms vs 1.08 ms). The cache was measured and rejected, not forgotten.
  * `combat_setup.prepare_setup`: depends on `libSeed` (follower selection), on the caller-supplied
    roster and on the native raw-HP refill that mutates the scenario; it is called twice per run
    on purpose (pre- and post-refill) and is never memoised.
  * Any gameplay report, trace, RNG state or metric: no result memoisation of any kind.

Timing must be warm and unprofiled; the parity check over the full canonical report (trace, RNG
final state, final state, receipts, metrics, setup and the manifest payload itself) is the
equivalence proof.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_PATCHES = []  # (owner_object, attribute, original_value)
_STATE = {}    # bounded caches, cleared on install/uninstall

_NATIVE_CAPACITY = 4
_FILE_CAPACITY = 512

# Copied verbatim from combat_run_manifest.run_manifest; the checker asserts the whole manifest
# dict (this string included) equals the canonical function's output on every fixture case.
_RNG_BACKEND = ('System.Random subtractive primitive (JRandom.Logic defaults to 0 => System.Random); '
                'seeds are explicit scenario injections or live per-session wall-clock')


def _setattr_once(owner, name, value):
    _PATCHES.append((owner, name, getattr(owner, name)))
    setattr(owner, name, value)


def _remember(store, key, value, capacity):
    store[key] = value
    while len(store) > capacity:
        store.pop(next(iter(store)))


def _stat_signature(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns, getattr(st, 'st_ino', 0), getattr(st, 'st_dev', 0))


def _file_digest(path):
    """sha256 of one file, cached by stat signature; recomputed when the file moves."""
    store = _STATE.setdefault('fileDigests', {})
    key = str(path)
    try:
        signature = _stat_signature(path)
    except OSError:
        signature = None
    if signature is not None:
        hit = store.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if signature is not None:
        _remember(store, key, (signature, digest), _FILE_CAPACITY)
    return digest


def _native_identity(module):
    """Cached equivalent of `combat_run_manifest.native_identity`; packaged branch delegated."""
    native = module.NATIVE / 'inputs' / 'libil2cpp.so'
    if native.is_file():
        store = _STATE.setdefault('native', {})
        key = str(native)
        signature = _stat_signature(native)
        hit = store.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]
        with native.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != module.NATIVE_SHA256:
            raise module.ScenarioError('Native evidence build hash mismatch')
        result = (digest, 'rehashed-local-binary')
        _remember(store, key, (signature, result), _NATIVE_CAPACITY)
        return result
    return _ORIGINALS['native_identity']()


def _run_manifest(module, scenario, contract):
    """Cached equivalent of `combat_run_manifest.run_manifest` (same payload, cached file digests)."""
    digest, digest_source = _native_identity(module)
    if module.packaged():
        files = module.package_files()
        attested = module.manifest().get('attested', {})
        return dict(schema='ka-combat-run-manifest-1', nativeSha256=digest,
                    nativeSha256Source=digest_source, runtimeMode=module.mode(), runtimeFiles=files,
                    attestedSources=attested, normalizedScenarioSha256=module.canonical_hash(scenario),
                    files=files, contentSha256=module.canonical_hash(files),
                    python=module.platform.python_version(), platform=module.platform.platform(),
                    support=contract, rngBackend=_RNG_BACKEND,
                    initialSeeds=dict(math=scenario['mathSeed'], lib=scenario['libSeed']))
    # Hash contents, including dirty edits; a Git commit alone cannot reproduce this workspace.
    paths = set(Path(module.__file__).parent.glob('combat_*.py'))
    paths.add(Path(module.__file__).with_name('recover_special_combat.py'))
    paths.add(Path(module.__file__).with_name('recover_combat_animation_resources.py'))
    paths.update(module.TABLES.glob('*.txt'))
    paths.update(module.OUT / name for name in ('weapon-skill-profiles.json', 'encounters.json',
                                                'formation-rules.json', 'effect-resource-checks.json',
                                                'animation-resources.json', 'skill-combat-constants.json'))
    files = {p.relative_to(module.ROOT).as_posix(): _file_digest(p) for p in sorted(paths)}
    return dict(schema='ka-combat-run-manifest-1', nativeSha256=digest,
                nativeSha256Source=digest_source, runtimeMode=module.mode(),
                normalizedScenarioSha256=module.canonical_hash(scenario), files=files,
                contentSha256=module.canonical_hash(files), python=module.platform.python_version(),
                platform=module.platform.platform(), support=contract, rngBackend=_RNG_BACKEND,
                initialSeeds=dict(math=scenario['mathSeed'], lib=scenario['libSeed']))


_ORIGINALS = {}


def reference_native_identity():
    """The unpatched canonical `native_identity` (requires `installed()`)."""
    return _ORIGINALS['native_identity']()


def reference_run_manifest(scenario, contract):
    """The unpatched canonical `run_manifest` payload (requires `installed()`)."""
    return _ORIGINALS['run_manifest'](scenario, contract)


def install():
    """Patch the canonical setup objects in this process; returns the number of patches applied."""
    if _PATCHES:
        raise RuntimeError('strategy_optimizer_setup_fast is already installed')
    if 'strategy_optimizer_desktop' in sys.modules:
        raise RuntimeError('strategy_optimizer_setup_fast is headless-only; a desktop module is loaded')
    import combat_run_manifest
    import combat_sandbox

    _STATE.clear()
    _ORIGINALS.clear()
    _ORIGINALS['native_identity'] = combat_run_manifest.native_identity
    _ORIGINALS['run_manifest'] = combat_run_manifest.run_manifest

    _setattr_once(combat_run_manifest, 'native_identity',
                  lambda: _native_identity(combat_run_manifest))
    _setattr_once(combat_run_manifest, 'run_manifest',
                  lambda scenario, contract: _run_manifest(combat_run_manifest, scenario, contract))
    # combat_sandbox bound both names into its own namespace at import time.
    _setattr_once(combat_sandbox, 'run_manifest',
                  lambda scenario, contract: _run_manifest(combat_run_manifest, scenario, contract))
    return len(_PATCHES)


def uninstall():
    """Restore every patched attribute to its original object; returns the number restored."""
    restored = 0
    while _PATCHES:
        owner, name, original = _PATCHES.pop()
        setattr(owner, name, original)
        restored += 1
    _STATE.clear()
    _ORIGINALS.clear()
    return restored


def installed():
    return bool(_PATCHES)


def cache_stats():
    """Bounded-cache occupancy for the checker; never used to make a correctness decision."""
    return {name: len(store) for name, store in _STATE.items()}


class accelerated:
    """Context manager: install on entry, always uninstall on exit."""

    def __enter__(self):
        install()
        return self

    def __exit__(self, *exc):
        uninstall()
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Install/uninstall the exact headless setup accelerator.')
    parser.add_argument('action', choices=('install', 'check'))
    args = parser.parse_args()
    if args.action == 'install':
        print(f'patches={install()}')
    else:
        import combat_run_manifest
        before = (combat_run_manifest.native_identity, combat_run_manifest.run_manifest)
        install()
        during = (combat_run_manifest.native_identity, combat_run_manifest.run_manifest)
        assert during != before
        uninstall()
        after = (combat_run_manifest.native_identity, combat_run_manifest.run_manifest)
        assert after == before, 'uninstall did not restore the canonical objects'
        print('install/uninstall restores the canonical setup objects')


if __name__ == '__main__':
    main()
