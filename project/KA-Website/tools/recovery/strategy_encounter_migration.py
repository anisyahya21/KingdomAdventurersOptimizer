"""Explicit, pinned observer migration; historical provenance is never rewritten.

The checked-in certificate is an acceptance artifact produced after independent review.
Neither a successful test exit nor an arbitrary caller-provided manifest grants compatibility.
Only the exact source and data inventories reviewed in that certificate are eligible.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

KEY = 'encounterObserverMigration'
CERTIFICATE = Path(__file__).with_name('encounter_migration_certificate.json')
# Keep each accepted transition on an explicit code-level allow-list. The first certificate predates
# the current library migration; the second is restricted to the reviewed timing-instrumentation
# transition recorded for the 2026-09-29 library.
TIMING_CERTIFICATE = Path(__file__).with_name('encounter_migration_certificate_20260929_timing.json')
CERTIFICATES = (CERTIFICATE, TIMING_CERTIFICATE)
# Scheduler files cannot affect a battle, and routinely change during rollout.
SCHEDULER_FILES = frozenset({'tools/recovery/strategy_optimizer.py',
                           'tools/recovery/strategy_optimizer_limits.py'})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def inventory(provenance):
    if not isinstance(provenance, dict) or provenance.get('missing'):
        raise ValueError('Complete simulator provenance is required for migration.')
    files = provenance.get('files')
    if not isinstance(files, dict) or not files or any(not v for v in files.values()):
        raise ValueError('Simulator source/data inventory is missing.')
    return {k: v for k, v in files.items() if k not in SCHEDULER_FILES}


def preview(old, current):
    """Read-only eligibility check, including native binaries; no inferred equivalence."""
    try:
        before, after = inventory(old), inventory(current)
        root = Path(__file__).resolve().parents[2]
        matched = False
        for certificate_path in CERTIFICATES:
            raw = certificate_path.read_bytes()
            certificate = json.loads(raw)
            if (before != certificate.get('sourceFrom')
                    or after != certificate.get('sourceTo')):
                continue
            matched = True
            if certificate.get('schema') != 1 or certificate.get('review') != 'accepted':
                raise ValueError('Observer transition has not passed independent review.')
            data_keys = {k for k in before.keys() | after.keys() if k.startswith('runtime-')}
            if any(before.get(k) != after.get(k) for k in data_keys):
                raise ValueError('Runtime combat data changed; observer-only migration is inapplicable.')
            binaries = certificate.get('nativeBinaries')
            if not isinstance(binaries, dict) or not binaries:
                raise ValueError('Reviewed native binary inventory is missing.')
            evidence = certificate.get('evidence')
            if (not isinstance(evidence, dict) or not evidence.get('path')
                    or not evidence.get('sha256')):
                raise ValueError('Pinned parity evidence is missing.')
            assets = list(binaries.items())
            # The canonical Python provenance does not inventory the native adapter/ABI modules.
            # Pin those separately so a later adapter change cannot inherit an old parity grant.
            runtime_assets = certificate.get('reviewedRuntimeAssets', {})
            if not isinstance(runtime_assets, dict):
                raise ValueError('Reviewed runtime asset inventory is invalid.')
            assets.extend(runtime_assets.items())
            assets.append((evidence['path'], evidence['sha256']))
            for relative, expected in assets:
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError('Reviewed binary or parity evidence is missing.')
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError('Binary or parity evidence differs from the reviewed transition.')
            proof = dict(certificate=hashlib.sha256(raw).hexdigest(),
                         sourceFrom=_digest(before), sourceTo=_digest(after))
            return dict(eligible=True, proof=proof, planId=_digest(proof),
                        evidence=certificate.get('evidence'),
                        limitation='Finite parity fixtures plus source review; not exhaustive proof.',
                        preserves=['historical provenance', 'candidates', 'results', 'lineage'])
        if matched:
            raise ValueError('The matching reviewed certificate failed validation.')
        raise ValueError('This simulator transition does not match the reviewed inventories.')
    except (OSError, ValueError, TypeError) as exc:
        return dict(eligible=False, reason=str(exc))


def applied(store, current):
    record = store.get(KEY)
    if not record:
        return False
    plan = preview(store.get('provenance'), current)
    return bool(plan.get('eligible') and record.get('proof') == plan['proof'])


def apply(store, current, plan_id):
    """Caller must require paused/drained mode and an explicitly reviewed preview token."""
    plan = preview(store.get('provenance'), current)
    if not plan.get('eligible'):
        raise ValueError(plan.get('reason', 'Observer migration is not eligible.'))
    if plan_id != plan['planId']:
        raise ValueError('Preview this exact simulator migration before applying it.')
    record = dict(proof=plan['proof'], appliedAt=time.time(), originalProvenancePreserved=True)
    from strategy_experiment_store import _atomic
    with _atomic(store.db):
        store.set(KEY, record)
    store.compatible = True
    return record


def rollback(store, current):
    """Remove the compatibility grant without removing any measured evidence."""
    from strategy_experiment_store import _atomic
    with _atomic(store.db):
        store.db.execute('DELETE FROM meta WHERE key=?', (KEY,))
    from strategy_optimizer import same_simulator
    old = store.get('provenance')
    store.compatible = old['digest'] == current['digest'] or same_simulator(old, current)
