"""Predeclared, budget-bounded fair comparison of the legacy and encounter-aware search.

This harness is a mechanical comparison driver, not a research result. It answers one question for
section 16 of the implementation command: does the Community-first Coordinator, driving the real
Optimizer loop, reach the same or better independently measured earned reward as the untouched legacy
planner, under an equal and predeclared budget, equal mature starting evidence, the same allowed
search template/domain/resource/Finish policy, and fresh common holdouts?

It is deliberately split in two so nothing can be decided after the fact:

* write-plan writes an immutable JSON plan: every stage, arm, encounter, seed and dispatch (including
  calibration, controls, replays and confirmation) and the exact total. It runs no battles and never
  opens the live library.
* run-plan re-derives the plan digest, refuses a changed plan, runs the preflight gates, and only
  then executes the predeclared stages. Each arm runs in its own subprocess with PYTHONPATH set to
  that arm source root, so a legacy and a current runtime can never share a module cache.

Fairness and honesty rules encoded here:

* Both arms start from an SQLite online-backup copy of the same frozen mature baseline (1595
  candidates / 10.9M lifetime runs / all 20 encounters). The baseline is opened read-only and never
  written; copies are made serially and deleted afterwards so peak disk stays minimal.
* The legacy arm is the frozen legacy source exactly as it generated the baseline; nothing in it is
  handicapped, capped or replaced. Both arms drive the real public runtime (Optimizer); the new arm
  additionally enables the persisted Community-first mode so the Coordinator owns the loop.
* The canonical terminal earned contract (strategy_outcomes.outcome / summarize) is applied to both
  arms raw results. Errors, censoring, unresolved and pending counts are counted, never dropped and
  never scored as zero.
* Final nominees are frozen BEFORE any holdout battle. Holdout pairs are fresh globally and common
  within a comparison, drawn from the plan digest so both arms see the same pairs.
* Provenance is a gate, not an assumption. New instrumentation changes combat-module digests, so the
  new arm is admitted only through the explicitly reviewed observer migration
  (strategy_encounter_migration.preview against the checked-in pinned certificate, which the runtime
  then applies to the paused copy and whose applied proof the Store honours). same_simulator alone
  never authorises the transition, and no arbitrary manifest verdict string is accepted. Without the
  reviewed transition the comparison is reported inconclusive/blocked, never silently patched.
* Throughput measures infrastructure, never an algorithm. The telemetry modes toggle the current
  runtime pool on identical work; the matched modes run one identical finite fixed workload
  (same build, same ``seed_pair`` sequence, same count, same worker concurrency) through the frozen
  legacy HeadlessPool+Store.record path and the current Evaluator+ea ledger, reporting
  preparation/dispatch/persistence/analysis wall and parent CPU separately. The real end-to-end
  search timing for the algorithm comparison lives in the search stage.

Known adapter gaps are reported, never hidden. Run selftest for the no-battle mechanical checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import signal
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

PLAN_SCHEMA = 'encounter-redesign-benchmark-plan-1'
ARM_LEGACY = 'legacy'
ARM_NEW = 'new'
ARMS = (ARM_LEGACY, ARM_NEW)
SEED_MAX = (1 << 31) - 1
THROUGHPUT_MODES = ('telemetry-off', 'telemetry-on', 'legacy-dispatch', 'new-dispatch')
TELEMETRY_MODES = ('telemetry-off', 'telemetry-on')
MATCHED_MODES = ('legacy-dispatch', 'new-dispatch')
#: Stop an encounter driver that is Running but has made no transport progress for this many
#: consecutive polls; the phase is reported INCOMPLETE instead of idling to the real deadline.
#: This is only ever applied once the coordinator publishes an EXPLICIT idle/unspendable/no-useful-work
#: signal (see ``_coordinator_stall``) and after ``IDLE_GRACE_SECONDS`` of startup/analysis allowance,
#: so a real 20 s+ shared-analysis/fresh-history pass is never mistaken for a stalled search.
IDLE_POLL_LIMIT = 20
#: A new-arm search may spend this bounded window on its initial shared analysis/fresh global-history
#: read before an unspendable budget may be declared idle; the absolute deadline still bounds the run.
IDLE_GRACE_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 0.5
#: Bounded wait for the raw Optimizer's own worker thread to exit after close before its copy is released.
SHUTDOWN_JOIN_SECONDS = 60
#: Bounded, read-only per-encounter measured snapshot caps (never a whole-population scan).
MEASURED_CANDIDATE_LIMIT = 256
MEASURED_ROWS_PER_CANDIDATE = 4096

#: Copy-only SQLite ceiling, in MiB, applied IDENTICALLY to both arms inside the benchmark child
#: immediately before any Store is constructed. The frozen baseline is opened read-only and is never
#: raised; only the temporary unit copies carry this limit. This is an explicit, declared test-scope
#: deviation from the optimiser's own 4096 MiB default (a legacy copy.sqlite overflowed at exactly
#: 4294967296 bytes on a host with ample free disk) and changes no search/policy/selection semantics.
COPY_STORAGE_MIB = 8192
#: The optimiser module default, recorded so the plan states the deviation explicitly.
OPTIMIZER_DEFAULT_DB_MIB = 4096
#: Declared WAL headroom for the peak-copy bound, in MiB; part of the copy filesystem headroom check.
COPY_WAL_MIB = 256
#: At most two unit copies are ever live at once (both arms of one encounter/replicate unit).
COPY_PEAK_COPIES = 2
#: The declared copy filesystem headroom: peak copies plus a bounded WAL, in MiB.
COPY_REQUIRED_FREE_MIB = COPY_PEAK_COPIES * COPY_STORAGE_MIB + COPY_WAL_MIB

#: Lazily loaded, by explicit path, so both arms share exactly one current canonical observer.
_CANONICAL_OUTCOMES = None


class HarnessError(RuntimeError):
    """A malformed request or an unsafe operation; never a made-up result."""


class Blocked(HarnessError):
    """A preflight gate refused to let the comparison proceed (reported, not worked around)."""


class BudgetExhausted(HarnessError):
    """The finite execution meter has no remaining authorised battles for this phase."""


class Incomplete(HarnessError):
    """A real deadline ended a stage before its declared budget completed."""


# --------------------------------------------------------------------------------------------------
# small generic helpers
# --------------------------------------------------------------------------------------------------
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value, *, immutable=False):
    path = Path(path)
    if immutable and path.exists():
        raise HarnessError(f'refusing to overwrite an existing file: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
                    encoding='utf-8')
    return path


def append_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        for row in rows:
            handle.write(canonical(row) + '\n')


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def default_python():
    candidate = REPO.parent / '.venv' / 'Scripts' / 'python.exe'
    return str(candidate if candidate.is_file() else Path(sys.executable))


BASELINE_META_KEYS = ('schema', 'objectiveVersion', 'searchSpaceVersion', 'totalRuns', 'provenance',
                      'mpRecoverySetting', 'freshRootIndex', 'averagePlan',
                      'tickLimit', 'finishPolicy', 'holyHerbStock', 'defeatCount', 'inputs', 'scope')


def read_baseline_binding(path, *, counts=True):
    """Bind the plan to the exact frozen snapshot without ever writing to it."""
    path = Path(path)
    if not path.is_file():
        raise Blocked(f'baseline snapshot not found: {path}')
    stat = path.stat()
    binding = dict(path=str(path), bytes=stat.st_size, mtime=round(stat.st_mtime, 3), sha256=None,
                   sha256Note='not computed (4.3GB snapshot; pass --hash-baseline to bind content)')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    try:
        connection.execute('PRAGMA query_only=ON')
        meta = {}
        for key in BASELINE_META_KEYS:
            row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            if row is None or row[0] in (None, ''):
                continue
            try:
                meta[key] = json.loads(row[0])
            except ValueError:
                meta[key] = row[0]
        binding['meta'] = meta
        binding['counts'] = {}
        if counts:
            for table in ('candidate', 'run', 'evidence'):
                try:
                    binding['counts'][table] = int(
                        connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
                except sqlite3.Error:
                    binding['counts'][table] = None
    finally:
        connection.close()
    provenance = meta.get('provenance') if isinstance(meta.get('provenance'), dict) else {}
    binding['storedProvenanceDigest'] = provenance.get('digest')
    binding['candidateCount'] = binding['counts'].get('candidate')
    binding['lifetimeRuns'] = meta.get('totalRuns')
    return binding


SCOPE_DEFAULTS = dict(tickLimit=30000, finishPolicy='on-verdict', holyHerbStock=0, defeatCount=0,
                      inputs=[])


def _coerce_mapping(value):
    """Return a dict from a value that may be a JSON object or a (double-)encoded JSON string."""
    for _ in range(4):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return None


def read_scope_record(binding):
    """Record the exact scope policy once, applied identically to both arms.

    The frozen snapshot stores the real horizon/policy in a ``scope`` meta value that may itself be a
    JSON-encoded string; that is read first, then any flat ``meta`` keys, and only then the declared
    default. Each entry records its value and its source, so a default is never passed off as a
    recovered game fact and the 30000 horizon is never silently replaced by a censored default. Both
    arms read the same record from the same shared baseline copy.
    """
    meta = binding.get('meta') or {}
    nested = _coerce_mapping(meta.get('scope')) or {}
    record = {}
    for key, default in SCOPE_DEFAULTS.items():
        if key in meta and meta.get(key) is not None:
            value, source = meta.get(key), 'meta'
        elif nested.get(key) is not None:
            value, source = nested.get(key), 'meta:scope'
        else:
            value, source = default, 'default'
        record[key] = dict(value=value, default=default, source=source)
    return record


def resolve_actual_scope(meta_scope, arm_observed):
    """Resolve the policy the arms ACTUALLY dispatch, reconciling the frozen meta and arm defaults.

    ``meta_scope`` is the frozen snapshot's recorded scope (``meta``/``meta:scope``, else the declared
    default); ``arm_observed`` maps each arm to its own adapter default policy fields. Both arms must
    read the SAME actual policy. Where the arm default disagrees with the frozen meta the ACTUAL arm
    value is used AND the disagreement is recorded, so a 30000 horizon is never claimed as the policy
    when the arms actually read less. A key on which the two arms disagree is recorded as an
    ``arms-differ`` conflict and rejected by validate_plan rather than silently drifting.
    """
    conflicts = []
    record = {}
    for key, default in SCOPE_DEFAULTS.items():
        entry = meta_scope.get(key)
        meta_value = entry.get('value') if isinstance(entry, dict) else entry
        meta_source = entry.get('source') if isinstance(entry, dict) else None
        per_arm = {arm: (arm_observed.get(arm) or {}).get(key) for arm in ARMS}
        if len({canonical(value) for value in per_arm.values()}) > 1:
            conflicts.append(dict(key=key, kind='arms-differ', values=per_arm))
        actual = next((per_arm[arm] for arm in ARMS if per_arm[arm] is not None), meta_value)
        if canonical(actual) != canonical(meta_value):
            conflicts.append(dict(key=key, kind='meta-vs-observed', meta=meta_value, observed=actual,
                                  metaSource=meta_source))
        record[key] = dict(value=actual, default=default, source='arm-observed-default',
                           metaValue=meta_value, metaSource=meta_source)
    return record, conflicts


PROVENANCE_PROBE = (
    "import json,sys;"
    "sys.path.insert(0,sys.argv[1]);"
    "import strategy_optimizer_adapter as a;"
    "p=a.provenance();"
    "print(json.dumps({k:p.get(k) for k in ('digest','mode','count','missing','files')}))"
)

INTERFACE_PROBE = (
    "import importlib,importlib.util,json,sys;"
    "sys.path.insert(0,sys.argv[1]);"
    "out={};"
    "opt=importlib.import_module('strategy_optimizer');"
    "out['optimizer']=hasattr(opt,'Optimizer') and hasattr(opt,'baseline_scenarios')"
    " and hasattr(opt,'same_simulator');"
    "out['coordinatorWired']=hasattr(opt,'encounter_search');"
    "mods=('strategy_encounter_search','strategy_experiment_store','strategy_outcomes',"
    "'strategy_joint_proposals','strategy_search_mode','strategy_encounter_evaluation');"
    "out['modules']={m:bool(importlib.util.find_spec(m)) for m in mods};"
    "print(json.dumps(out))"
)


def _probe(python, source_root, code, timeout, extra_args=()):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(source_root)
    command = [python, '-B', '-X', 'utf8', '-c', code, str(source_root)]
    command.extend(str(argument) for argument in extra_args)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(ok=False, error=f'probe timed out after {timeout}s')
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or '').strip().splitlines()[-3:]
        return dict(ok=False, error=' | '.join(tail)[-600:])
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return dict(ok=False, error=f'unreadable probe output: {exc}')
    payload['ok'] = True
    return payload


def probe_provenance(python, source_root, timeout=600):
    return _probe(python, source_root, PROVENANCE_PROBE, timeout)


def probe_interface(python, source_root, timeout=600):
    return _probe(python, source_root, INTERFACE_PROBE, timeout)


RUNTIME_GATE_PROBE = (
    "import json,sys;"
    "sys.path.insert(0,sys.argv[1]);"
    "import strategy_optimizer as o;"
    "import strategy_optimizer_adapter as a;"
    "stored=json.loads(sys.argv[2]);"
    "print(json.dumps({'sameSimulator':bool(o.same_simulator(a.provenance(),stored))}))"
)


def probe_runtime_gate(python, source_root, stored_provenance, timeout=600):
    """The runtime's own fail-closed gate: does it accept this arm on the baseline library?"""
    env = dict(os.environ)
    env['PYTHONPATH'] = str(source_root)
    try:
        proc = subprocess.run([python, '-B', '-X', 'utf8', '-c', RUNTIME_GATE_PROBE,
                               str(source_root), canonical(stored_provenance)],
                              capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(ok=False, error='runtime-gate probe timed out')
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or '').strip().splitlines()[-3:]
        return dict(ok=False, error=' | '.join(tail)[-400:])
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return dict(ok=False, error=f'unreadable runtime-gate output: {exc}')
    payload['ok'] = True
    return payload


#: The stored-reference policy probe: read the frozen baseline's supplied scenarios READ-ONLY, run each
#: through the arm's OWN normalization path, and record the policy the arm actually dispatches. It also
#: reads the arm's runtime search horizon (``strategy_search.DEFAULT_TICK_LIMIT``, the value the
#: Optimizer overrides into the stored scope) and fails closed if the stored references disagree with
#: each other or with that horizon. The adapter's ``default_scenario()`` is deliberately NOT consulted:
#: it carries the stale 3000 pause horizon, not the 30000 policy the mature baseline stores and search
#: dispatches, so it is never reported as the runtime policy.
POLICY_PROBE = textwrap.dedent('''
    import json,sys,sqlite3
    root=sys.argv[1]; base=sys.argv[2]; encounters=json.loads(sys.argv[3]); limit=int(sys.argv[4])
    sys.path.insert(0,root)
    import strategy_optimizer_adapter as adapter
    import strategy_search as search
    policy_keys=('finishPolicy','tickLimit','holyHerbStock','defeatCount','inputs','startProfile')
    provenance=adapter.provenance()
    uri='file:'+base.replace(chr(92),'/')+'?mode=ro'
    connection=sqlite3.connect(uri,uri=True,timeout=30)
    connection.execute('PRAGMA query_only=ON')
    references=[]; by_encounter={}; missing=[]
    try:
        for encounter in encounters:
            rows=connection.execute(
                "SELECT c.id,c.scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id "
                "WHERE m.encounter=? AND c.source='supplied' ORDER BY c.created,c.id LIMIT ?",
                (int(encounter),limit)).fetchall()
            if not rows:
                missing.append(int(encounter))
                continue
            for candidate_id,scenario_json in rows:
                stored=json.loads(scenario_json)
                normalized=adapter.validate_scenario(stored)
                references.append(dict(encounterId=int(encounter),candidateId=candidate_id,
                    stored={k:stored.get(k) for k in policy_keys},
                    observed={k:normalized.get(k) for k in policy_keys}))
                # The encounter's OWN supplied reference is pinned per encounter; one first-reference
                # is never reused across encounters.
                if int(encounter) not in by_encounter:
                    by_encounter[int(encounter)]=dict(encounterId=int(encounter),candidateId=candidate_id,
                        stored=stored)
    finally:
        connection.close()
    conflicts=[]; observed=None
    for reference in references:
        if observed is None:
            observed=reference['observed']
        elif json.dumps(observed,sort_keys=True)!=json.dumps(reference['observed'],sort_keys=True):
            conflicts.append(dict(kind='reference-differs',encounterId=reference['encounterId'],
                candidateId=reference['candidateId'],expected=observed,observed=reference['observed']))
    search_horizon=getattr(search,'DEFAULT_TICK_LIMIT',None)
    if observed is not None and search_horizon is not None and observed.get('tickLimit')!=search_horizon:
        conflicts.append(dict(kind='runtime-horizon-differs',runtime=search_horizon,
            stored=observed.get('tickLimit')))
    payload=dict(finishPolicy=(observed or {}).get('finishPolicy'),
        scenarioScope={k:(observed or {}).get(k) for k in
            ('defeatCount','tickLimit','holyHerbStock','inputs','startProfile')},
        observed=observed,searchHorizon=search_horizon,references=references,
        referencesByEncounter={str(k):v for k,v in by_encounter.items()},
        missingEncounters=missing,conflicts=conflicts,
        revision=provenance.get('digest'),files=provenance.get('files'),
        count=provenance.get('count'),missingFiles=(provenance.get('missing') or []))
    print(json.dumps(payload))
''')


def probe_arm_policy(python, source_root, baseline, encounters, timeout=300):
    """That arm's OWN observed stored-reference policy and bounded source inventory.

    Run with the arm's own ``PYTHONPATH`` in a subprocess, so the legacy arm's policy and inventory
    come from the frozen legacy tree and the new arm's from the current tree. The frozen baseline is
    opened READ-ONLY (no mutating Store, no live library) and each supplied scenario is normalized by
    the arm's own ``validate_scenario``, so the observed policy is the stored 30000 horizon the search
    dispatches, never the adapter's stale 3000 ``default_scenario`` pause horizon. No value is assumed
    from an older revision. At most 20 supplied scenarios are read in total.
    """
    encounters = [int(value) for value in encounters]
    limit = max(1, 20 // max(1, len(encounters)))
    return _probe(python, source_root, POLICY_PROBE, timeout,
                  extra_args=(str(baseline), canonical(encounters), str(limit)))


def _source_inventory(probe):
    """Freeze the bounded arm implementation inventory (digest + per-file hashes) from a probe."""
    probe = probe or {}
    return dict(ok=bool(probe.get('ok')), digest=probe.get('revision'),
                files=probe.get('files') or {}, count=probe.get('count'),
                missing=list(probe.get('missing') or []), error=probe.get('error'),
                note='canonical combat/runtime-data provenance only (adapter.provenance: combat*.py, '
                     'strategy_search, adapter, main optimizer, limits and runtime data/tables); the '
                     'independent strategy/Coordinator/proposals/adviser + native DLL/ABI + observer '
                     'inventory is the separate arms.<arm>.implementationInventory, never the whole '
                     'repository')


def _arm_policy_record(probe):
    """The exact stored-reference policy that arm resolves, recorded with its source, never assumed."""
    probe = probe or {}
    return dict(ok=bool(probe.get('ok')), finishPolicy=probe.get('finishPolicy'),
                scenarioScope=probe.get('scenarioScope') or {}, revision=probe.get('revision'),
                source='per-arm-subprocess-probe(stored supplied reference + runtime horizon)',
                error=probe.get('error'))


def _observed_policy_record(probe):
    """The arm's own observed policy (the frozen stored supplied scenario normalized by the arm).

    Includes the exact stored startProfile plus finish/horizon/stock/inputs/defeatCount and carries the
    full representative stored scenario as ``reference``, so the guard re-normalizes the real stored
    input through the arm's own path and compares dispatched scenarios against a recorded reading,
    never the adapter default.
    """
    probe = probe or {}
    fields = dict(probe.get('observed') or {})
    references = probe.get('referencesByEncounter') or {}
    return dict(ok=bool(probe.get('ok')),
                source='per-arm-subprocess-probe(frozen stored supplied scenario, arm-normalized)',
                error=probe.get('error'), searchHorizon=probe.get('searchHorizon'),
                referenceCount=len(probe.get('references') or []),
                referenceEncounters=sorted(references),
                conflicts=list(probe.get('conflicts') or []),
                missingEncounters=list(probe.get('missingEncounters') or []),
                referencesByEncounter=dict(references),
                fields=dict(defeatCount=fields.get('defeatCount'),
                            finishPolicy=fields.get('finishPolicy'),
                            tickLimit=fields.get('tickLimit'),
                            holyHerbStock=fields.get('holyHerbStock'),
                            inputs=fields.get('inputs'),
                            startProfile=fields.get('startProfile')))


def validate_source_inventory(frozen, live, *, arm):
    """Refuse a run whose arm tree no longer matches the inventory frozen in the plan."""
    frozen = frozen or {}
    live = live or {}
    if not frozen.get('digest') or not frozen.get('files'):
        return [f'arm {arm} plan carries no frozen source inventory']
    if not live.get('ok'):
        return [f'arm {arm} live source probe failed: {live.get("error")}']
    problems = []
    live_digest = live.get('revision') or live.get('digest')
    if live_digest != frozen.get('digest'):
        problems.append(f'arm {arm} source digest changed after the plan: '
                        f'{live_digest} != {frozen.get("digest")}')
    frozen_files, live_files = frozen.get('files') or {}, live.get('files') or {}
    for name in sorted(set(frozen_files) | set(live_files)):
        if frozen_files.get(name) != live_files.get(name):
            problems.append(f'arm {arm} source file changed after the plan: {name}')
    return problems


#: Bounded INDEPENDENT implementation surface: the modules the arms' bounded search/dispatch path is
#: built from, including the Coordinator, the joint proposals and the adviser that the adapter's own
#: ``provenance`` never hashed. Deliberately an explicit bounded list, never a whole-repo glob.
IMPLEMENTATION_SOURCES = (
    'strategy_optimizer.py',
    'strategy_optimizer_fast.py',
    'strategy_optimizer_limits.py',
    'strategy_optimizer_adapter.py',
    'strategy_optimizer_native.py',
    'strategy_optimizer_portfolio.py',
    'strategy_dispatch.py',
    'strategy_encounter_search.py',
    'strategy_encounter_adviser.py',
    'strategy_encounter_decisions.py',
    'strategy_encounter_evaluation.py',
    'strategy_encounter_confirmation.py',
    'strategy_joint_proposals.py',
    'strategy_stream_proposals.py',
    'strategy_experiment_store.py',
    'strategy_outcomes.py',
    'strategy_search_mode.py',
    'strategy_students.py',
    'strategy_mechanics.py',
    'strategy_operating_regions.py',
    'strategy_encounter_compiler.py',
    'strategy_build_domain.py',
    'strategy_legacy_observations.py',
    'strategy_seed_freshness.py',
    'strategy_encounter_presentation.py',
    'strategy_support_evidence.py',
    'search_contract.py',
    'ka_abi.py',
    'ka_encounter_abi.py',
)
#: Implementation sources that are inventoried ONLY when the arm tree actually ships the file. The
#: bounded inventory must start covering a module once a tree adds it, yet a tree that predates it
#: (the frozen legacy arm, or any plan authored before the module existed) must keep its exact frozen
#: inventory and never gain a spurious ``None``/missing entry from a file it never had. Explicit name,
#: never a whole-tree glob, and deliberately separate from ``IMPLEMENTATION_SOURCES`` so that list's
#: unconditional (present-or-missing) semantics are unchanged.
CONDITIONAL_IMPLEMENTATION_SOURCES = ('strategy_history_summary.py',)
#: The harness' own canonical observer modules, loaded by explicit path from HERE (the harness tree),
#: so the observer revision is frozen independently of either arm tree.
OBSERVER_SOURCES = ('strategy_outcomes.py', 'strategy_encounter_migration.py',
                    'benchmark_encounter_redesign.py')

IMPLEMENTATION_INVENTORY_PROBE = '''
import importlib.util
import json
import sys
from pathlib import Path

harness_dir, root, names = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
spec = importlib.util.spec_from_file_location(
    '_benchmark_implementation_inventory',
    str(Path(harness_dir) / 'benchmark_encounter_redesign.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps(module.implementation_inventory_files(root, names)))
'''


def probe_implementation_inventory(python, source_root, timeout=600):
    """Independent per-arm implementation inventory in a subprocess of THAT arm's tree.

    Hashes the bounded strategy/search/ABI sources under ``source_root`` and the native DLLs the
    arm's own ``ka_abi`` / ``ka_encounter_abi`` actually resolve to. It never calls the adapter's
    ``provenance`` (so it is independent of it) and never assumes a missing asset is unchanged: a
    missing file is recorded as ``None`` and compared as such.
    """
    try:
        proc = subprocess.run([python, '-B', '-X', 'utf8', '-c', IMPLEMENTATION_INVENTORY_PROBE,
                               str(HERE), str(source_root), canonical(list(IMPLEMENTATION_SOURCES))],
                              capture_output=True, text=True, env=dict(os.environ), timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(ok=False, error=f'implementation-inventory probe timed out after {timeout}s')
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or '').strip().splitlines()[-3:]
        return dict(ok=False, error=' | '.join(tail)[-600:])
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return dict(ok=False, error=f'unreadable implementation-inventory output: {exc}')
    payload['ok'] = True
    return payload


def _file_sha256(path):
    try:
        hasher = hashlib.sha256()
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                hasher.update(block)
        return hasher.hexdigest()
    except OSError:
        return None


def _arm_native_paths(root):
    """The native DLL paths the arm's OWN ka_abi / ka_encounter_abi resolve to, by explicit path.

    Both ABI mirrors are loaded by absolute file path, and the sibling ``ka_abi`` is bound in
    ``sys.modules`` only for the duration of the load, so a same-named module elsewhere on the
    interpreter path can never be mistaken for the arm's own ABI or its DLLs. Any previous binding
    is restored afterwards.
    """
    import importlib.util
    root = Path(root)
    native, error = {}, None
    abi_path = root / 'ka_abi.py'
    if not abi_path.is_file():
        return native, 'ka_abi: not in arm tree'
    previous_abi = sys.modules.get('ka_abi')
    previous_encounter = sys.modules.get('ka_encounter_abi')
    try:
        spec = importlib.util.spec_from_file_location('_arm_ka_abi', abi_path)
        abi = importlib.util.module_from_spec(spec)
        sys.modules['ka_abi'] = abi
        spec.loader.exec_module(abi)
        native['kernel'] = str(abi.DLL)
        encounter_path = root / 'ka_encounter_abi.py'
        if not encounter_path.is_file():
            error = 'ka_encounter_abi: not in arm tree'
        else:
            try:
                spec2 = importlib.util.spec_from_file_location('_arm_ka_encounter_abi',
                                                               encounter_path)
                encounter = importlib.util.module_from_spec(spec2)
                sys.modules['ka_encounter_abi'] = encounter
                spec2.loader.exec_module(encounter)
                native['encounter'] = str(encounter.encounter_dll_path(False))
                native['encounter_large'] = str(encounter.encounter_dll_path(True))
            except Exception as exc:  # noqa: BLE001
                error = 'ka_encounter_abi: %s: %s' % (type(exc).__name__, exc)
    except Exception as exc:  # noqa: BLE001
        error = 'ka_abi: %s: %s' % (type(exc).__name__, exc)
    finally:
        if previous_abi is None:
            sys.modules.pop('ka_abi', None)
        else:
            sys.modules['ka_abi'] = previous_abi
        if previous_encounter is None:
            sys.modules.pop('ka_encounter_abi', None)
        else:
            sys.modules['ka_encounter_abi'] = previous_encounter
    return native, error


def implementation_inventory_files(root, names=None):
    """The independent inventory: bounded sources under ``root`` + the arm's actual native DLLs.

    Resolved by explicit file path (never by ambient ``sys.path``), so the in-process arm record and
    the subprocess probe freeze identical labels and hashes for the same tree.
    """
    root = Path(root)
    names = IMPLEMENTATION_SOURCES if names is None else names
    files, missing = {}, []
    for name in names:
        path = root / name
        label = 'impl/' + name
        files[label] = _file_sha256(path) if path.is_file() else None
        if files[label] is None:
            missing.append(label)
    # Conditional sources are hashed only when the arm tree actually ships them, so a tree without the
    # file records neither a label nor a missing entry and its frozen inventory is byte-identical to
    # before the file existed. A tree that adds the file is covered (and a later removal is still
    # caught as a frozen-vs-live hash change by validate_implementation_inventory).
    for name in CONDITIONAL_IMPLEMENTATION_SOURCES:
        path = root / name
        label = 'impl/' + name
        if label in files or not path.is_file():
            continue
        files[label] = _file_sha256(path)
    native, native_error = _arm_native_paths(root)
    for key in sorted(native):
        path = Path(native[key])
        label = 'native/' + key
        files[label] = _file_sha256(path) if path.is_file() else None
        if files[label] is None:
            missing.append(label)
    return dict(ok=True, files=dict(sorted(files.items())), missing=sorted(missing),
                nativePaths=native, nativeError=native_error)


def observer_inventory(root=None):
    """The harness observer revision (canonical outcome + pinned migration modules), from HERE."""
    root = Path(root or HERE)
    files, missing = {}, []
    for name in OBSERVER_SOURCES:
        path = root / name
        files[name] = _file_sha256(path) if path.is_file() else None
        if files[name] is None:
            missing.append(name)
    body = dict(files=dict(sorted(files.items())), missing=sorted(missing))
    body['digest'] = digest(body)
    return body


def _implementation_inventory_record(probe, *, canonical_files=None, observer=None):
    """Combine the independent implementation files + the canonical runtime/data files + observer."""
    probe = probe or {}
    observer = observer or {}
    files = dict(probe.get('files') or {})
    for label, value in (canonical_files or {}).items():
        files[f'canonical/{label}'] = value
    body = dict(files=dict(sorted(files.items())),
                observerFiles=dict(sorted((observer.get('files') or {}).items())),
                observerRevision=observer.get('digest'),
                missing=sorted(probe.get('missing') or []),
                nativePaths=dict(probe.get('nativePaths') or {}),
                nativeError=probe.get('nativeError'))
    body['digest'] = digest(body)
    body['ok'] = bool(probe.get('ok'))
    return body


#: Active ONLY during a same-plan current resume. A resume moves the harness observer revision (this
#: very file was repaired) while every strategy/combat/native/source file must still match the frozen
#: plan exactly; the rebind is recorded as an explicit deviation, never silent.
_CURRENT_RESUME_OBSERVER_REBIND = None


def _set_current_resume_observer(rebind):
    """Set (or clear) the same-plan observer rebind for this process."""
    global _CURRENT_RESUME_OBSERVER_REBIND
    _CURRENT_RESUME_OBSERVER_REBIND = rebind


def validate_implementation_inventory(frozen, live, *, arm):
    """Refuse a run whose independent implementation/native inventory changed after the plan."""
    frozen = frozen or {}
    live = live or {}
    if not frozen.get('digest') or not frozen.get('files'):
        return [f'arm {arm} plan carries no frozen implementation inventory']
    if not frozen.get('observerRevision'):
        return [f'arm {arm} plan carries no frozen harness observer revision']
    if not live.get('ok'):
        return [f'arm {arm} live implementation probe failed: {live.get("error")}']
    if _CURRENT_RESUME_OBSERVER_REBIND is not None:
        # Re-bind ONLY the harness observer fields before comparing: the repaired harness file moved
        # the observer revision, and the resumed run explicitly acknowledges that. Every other field
        # (arm implementation/native files, canonical provenance files) is still compared exactly.
        expected = dict(frozen)
        expected['observerRevision'] = live.get('observerRevision')
        expected['observerFiles'] = dict(live.get('observerFiles') or {})
        expected['digest'] = digest({key: value for key, value in expected.items()
                                     if key not in ('digest', 'ok')})
        frozen = expected
    problems = []
    if live.get('digest') != frozen.get('digest'):
        problems.append(f'arm {arm} implementation digest changed after the plan: '
                        f'{live.get("digest")} != {frozen.get("digest")}')
    frozen_files, live_files = frozen.get('files') or {}, live.get('files') or {}
    for name in sorted(set(frozen_files) | set(live_files)):
        if frozen_files.get(name) != live_files.get(name):
            problems.append(f'arm {arm} implementation/native file changed after the plan: {name}')
    if live.get('observerRevision') != frozen.get('observerRevision'):
        problems.append(f'arm {arm} harness observer revision changed after the plan: '
                        f'{live.get("observerRevision")} != {frozen.get("observerRevision")}')
    return problems


def load_explicit_module(name, path):
    """Load one module by explicit absolute path, never by import shadowing.

    The parent observer must run the *current* canonical outcome module even when the legacy arm's
    source root is a different tree; loading by path removes any dependence on sys.path order. The
    child arm processes keep importing only their own source.
    """
    import importlib.util
    path = Path(path)
    if not path.is_file():
        raise HarnessError(f'required module not found at {path}')
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HarnessError(f'cannot load {name} from {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_canonical_outcomes():
    """The current canonical terminal earned contract, loaded by explicit path."""
    return load_explicit_module('benchmark_canonical_outcomes', HERE / 'strategy_outcomes.py')


def load_migration_module():
    """The root-reviewed observer migration module (pinned certificate), by explicit path."""
    return load_explicit_module('benchmark_encounter_migration',
                                HERE / 'strategy_encounter_migration.py')


def migration_gate(legacy_provenance, new_provenance):
    """The required reviewed transition, not same_simulator and not a manifest verdict.

    ``strategy_encounter_migration.preview`` returns eligible only for the exact source/data
    inventories that were independently reviewed in the checked-in certificate, verifies the pinned
    native binaries and parity evidence, and returns the reproducible ``planId`` the runtime must be
    given so that ``apply`` continues the same transition. An absent certificate is reported
    ineligible, never fabricated.
    """
    try:
        module = load_migration_module()
        plan = module.preview(legacy_provenance, new_provenance)
    except Exception as exc:  # noqa: BLE001
        return dict(eligible=False, certificate=str(HERE / 'encounter_migration_certificate.json'),
                    reason=f'migration module unavailable: {type(exc).__name__}: {exc}')
    plan['certificate'] = str(getattr(module, 'CERTIFICATE', ''))
    plan['rule'] = ('preview(old,current) must be eligible and the runtime applies planId to the '
                    'paused copy; the Store honours the applied proof')
    return plan


def assert_native_backend(rows, *, stage):
    """Fail closed if any raw row was not produced by the native backend.

    The adapter's own `auto`/fallback path silently returns a Python result with
    ``resultBackend='python'`` (and a recorded ``nativeFallbackReason``); a benchmark that claims
    native parity may not score such rows. Missing or non-native backend labels are refused.
    """
    summary = summarise_backend(rows)
    if summary['fallback'] or summary['unknown']:
        raise Blocked(f'{stage}: refusing to compare on a non-native backend '
                      f'(native={summary["native"]}, fallback={summary["fallback"]}, '
                      f'unknown={summary["unknown"]})')
    return summary


def _ea_ledger_counts(path):
    """Read the new arm's ea_* ledger read-only, so its accounting never falls back to totalRuns."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
        try:
            connection.execute('PRAGMA query_only=ON')
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            out = {}
            for table in ('ea_experiment_budget', 'ea_session_budget'):
                if table not in tables:
                    continue
                row = connection.execute(
                    f'SELECT COALESCE(SUM(total),0), COALESCE(SUM(reserved),0), '
                    f'COALESCE(SUM(completed),0) FROM {table}').fetchone()
                out[table] = dict(total=int(row[0]), reserved=int(row[1]), completed=int(row[2]))
            for table in ('ea_sample_link', 'ea_holdout'):
                if table in tables:
                    out[table] = int(connection.execute(
                        f'SELECT COUNT(*) FROM {table}').fetchone()[0])
            return out or None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def _admitted_from_file(path):
    """The ``admitted`` field of a persisted JSON artifact, or None when it is absent/unreadable."""
    if not path:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    try:
        value = read_json(path).get('admitted')
    except (ValueError, OSError):
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recovered_admitted(probe_path, payload, cap, *, report_path=None, extra_counts=()):
    """The ACTUAL admitted count for a stage, even when the child failed.

    The child writes its execution meter snapshot (admitted/denied) durably in a finally block, its
    full stage report (whose ``admitted`` is the last word) and, for the new arm, its persisted
    ``ea_*`` session/experiment ledger counts the same work. Every compatible source is read and the
    CONSERVATIVE MAXIMUM is charged: an early/stale payload or interim meter snapshot of 0 must never
    hide a larger durable cleanup cost the report or the persisted ledger already recorded. Nothing is
    silently clamped - a source that reports more than the declared cap is a validation failure that
    fails closed - and only when no source is recoverable does it fall back to the full reserved cap
    (still fail closed, never a silent zero).
    """
    cap = int(cap)
    observed = []
    if isinstance(payload, dict) and payload.get('admitted') is not None:
        try:
            observed.append(int(payload['admitted']))
        except (TypeError, ValueError):
            pass
    for value in list(extra_counts):
        try:
            observed.append(int(value))
        except (TypeError, ValueError):
            continue
    for path in (probe_path, report_path):
        value = _admitted_from_file(path)
        if value is not None:
            observed.append(value)
    if not observed:
        return cap
    high = max(observed)
    if high > cap:
        raise Blocked(f'recovered admitted count {high} exceeds the declared cap {cap}; refusing to '
                      'charge an out-of-contract count')
    return max(0, high)


def _write_meter_snapshot(out_dir, stage, arm, meter):
    """Durably persist a stage's exact transport count so a failed child is still charged."""
    try:
        write_json(Path(out_dir) / f'meter-{stage}-{arm}.json',
                   dict(stage=stage, arm=arm, admitted=meter.charged, denied=meter.denied,
                        cap=meter.cap, usedPairs=len(meter.usedPairs), at=now()))
    except OSError:
        pass


def _write_transport_snapshot(out_dir, stage, arm, summary):
    """Durably persist the actual transport backend mix so a failed search is still accountable."""
    try:
        write_json(Path(out_dir) / f'transport-{stage}-{arm}.json', dict(summary, at=now()))
    except OSError:
        pass


# --------------------------------------------------------------------------------------------------
# budget model
# --------------------------------------------------------------------------------------------------
def build_budget(encounter_count, replicates, *, arms=2, smoke_encounters=20, smoke_pairs=2,
                 search_battles=128, holdout_pairs=64, throughput_battles=64, setup_cap=32):
    """The exact, itemised battle budget. Every dispatch is a line here, not an implicit cost."""
    smoke = arms * smoke_encounters * smoke_pairs
    search = arms * encounter_count * replicates * search_battles
    holdout = arms * encounter_count * replicates * holdout_pairs
    throughput = len(THROUGHPUT_MODES) * throughput_battles
    planned = smoke + search + holdout + throughput
    stages = {
        'smoke': dict(battles=smoke, arms=arms, encounters=smoke_encounters,
                      pairsPerEncounter=smoke_pairs,
                      note='all-20 native smoke, fixed seed pairs, no library copy'),
        'search': dict(battles=search, arms=arms, encountersPerReplicate=encounter_count,
                       replicates=replicates, battlesPerArmPerEncounterPerReplicate=search_battles,
                       note='equal mature starting evidence; equal allowed domain/resource/Finish policy'),
        'holdout': dict(battles=holdout, arms=arms, encountersPerReplicate=encounter_count,
                        replicates=replicates,
                        battlesPerArmPerEncounterPerReplicate=holdout_pairs, nomineeOnly=True,
                        note='each arm evaluates ONLY its own frozen nominee on the fresh common '
                             'pairs; no separate common reference battle is run inside an arm, so '
                             'every pairs count is a real paired comparison observation'),
        'throughput': dict(battles=throughput, modes=list(THROUGHPUT_MODES),
                           battlesPerMode=throughput_battles, matchedModes=list(MATCHED_MODES),
                           note='telemetry-off/on toggle the current runtime pool on identical '
                                'work; legacy-dispatch/new-dispatch run the same finite fixed '
                                'workload through the frozen legacy HeadlessPool+Store.record path '
                                'versus the current Evaluator+ea ledger, with matched candidate, '
                                'seed pairs, count and worker concurrency'),
    }
    setup_items = {'calibration': arms * encounter_count, 'controls': len(THROUGHPUT_MODES),
                   'replay': arms, 'confirmation': arms}
    setup_total = sum(setup_items.values())
    if setup_total > setup_cap:
        raise HarnessError(f'setup items {setup_total} exceed the declared setup cap {setup_cap}')
    return dict(stages=stages, plannedBattles=planned, setupCap=setup_cap, setup=setup_items,
                setupBattles=setup_total, setupOnlyIfUsed=True, globalCap=planned + setup_cap,
                hardCeiling=planned + setup_cap,
                accounting='one global cap = plannedBattles + setupCap, persisted by BudgetLedger '
                           'across stages, subprocesses and resume; setup is charged only when used '
                           'and is never re-granted per replicate; every native pair request is '
                           'admitted by a finite execution meter before submission, so there is no '
                           'headroom, slack or overshoot to reclassify')


class BudgetGuard:
    """Fail-closed battle accounting for one arm subprocess."""

    def __init__(self, planned, setup_cap, slack=0):
        self.planned = int(planned)
        self.setup_cap = int(setup_cap)
        self.slack = int(slack)
        self.spent = 0
        self.claims = []

    @property
    def ceiling(self):
        return self.planned + self.setup_cap

    def remaining(self):
        return self.ceiling - self.spent

    def claim(self, count, *, stage):
        count = int(count)
        if count < 0:
            raise HarnessError('budget claim must be non-negative')
        if self.spent + count > self.ceiling:
            raise Blocked(f'budget refused: claiming {count} for {stage} would take {self.spent + count} '
                          f'above the hard ceiling {self.ceiling}')
        self.spent += count
        self.claims.append(dict(stage=stage, count=count, spent=self.spent))
        return True

    def release(self, count):
        count = int(count)
        if count:
            self.spent -= count
            self.claims.append(dict(stage='reconcile', count=-count, spent=self.spent))


LEDGER_SCHEMA = 'encounter-redesign-battle-ledger-1'


class BudgetLedger:
    """The single global battle ceiling for one plan, persisted across stages and resume.

    Only the orchestrating parent writes this file. Before a stage child runs the parent reserves
    that stage exact cap; after the child reports its actual admitted count the parent settles the
    reservation against the actual. A stage can therefore never run with a fresh per-replicate
    allowance, and the global cap can never be exceeded even when a plan is resumed.
    """

    def __init__(self, path, ceiling, *, setup_cap):
        self.path = Path(path)
        self.ceiling = int(ceiling)
        self.setup_cap = int(setup_cap)
        self.spent = 0
        self.setup_spent = 0
        self.reserved = {}
        self.claims = []
        self._next = 1
        self._load()

    def _load(self):
        if not self.path.is_file():
            return
        doc = read_json(self.path)
        if doc.get('schema') != LEDGER_SCHEMA:
            raise Blocked('battle ledger schema mismatch; refusing to resume')
        if int(doc.get('ceiling') or 0) != self.ceiling:
            raise Blocked('battle ledger ceiling changed; refusing to resume a different plan')
        self.spent = int(doc.get('spent') or 0)
        self.setup_spent = int(doc.get('setupSpent') or 0)
        self.reserved = {int(key): dict(value) for key, value in (doc.get('reserved') or {}).items()}
        self.claims = list(doc.get('claims') or [])
        self._next = int(doc.get('nextReservation') or 1)
        if self.spent + self.setup_spent > self.ceiling:
            raise Blocked('battle ledger already exceeds its ceiling; refusing to resume')

    def _persist(self):
        write_json(self.path, dict(schema=LEDGER_SCHEMA, ceiling=self.ceiling, setupCap=self.setup_cap,
                                   spent=self.spent, setupSpent=self.setup_spent,
                                   reserved={str(key): value for key, value in self.reserved.items()},
                                   claims=self.claims[-500:], nextReservation=self._next))

    @property
    def committed(self):
        return self.spent + self.setup_spent

    @property
    def remaining(self):
        return self.ceiling - self.committed

    def reserve(self, stage, cap):
        cap = int(cap)
        if cap < 0:
            raise HarnessError('reservation must be non-negative')
        if self.committed + cap > self.ceiling:
            raise Blocked(f'global battle cap refused: reserving {cap} for {stage} would take '
                          f'{self.committed + cap} above the ceiling {self.ceiling}')
        reservation = self._next
        self._next += 1
        self.reserved[reservation] = dict(stage=stage, cap=cap, actual=None)
        self.spent += cap
        self.claims.append(dict(op='reserve', id=reservation, stage=stage, cap=cap, spent=self.spent))
        self._persist()
        return reservation

    def settle(self, reservation, actual):
        if reservation not in self.reserved:
            raise HarnessError(f'unknown reservation {reservation}')
        entry = self.reserved.pop(reservation)
        actual = int(actual)
        if actual < 0 or actual > entry['cap']:
            raise Blocked(f'settle refused: stage {entry["stage"]} admitted {actual} outside its '
                          f'reserved cap {entry["cap"]}')
        self.spent -= (entry['cap'] - actual)
        self.claims.append(dict(op='settle', id=reservation, stage=entry['stage'], cap=entry['cap'],
                                actual=actual, spent=self.spent))
        self._persist()
        return actual

    def abandon_unknown(self, reservation, *, reason):
        """Abandon ONE interrupted reservation whose admission is UNKNOWN, at its reserved cap.

        A missing meter after a lost process is not proof of zero admitted battles, so the
        reservation is REMOVED (the stage will never settle) but ``spent`` is left unchanged: the
        reserved cap stays charged as a conservative upper bound. ``actual`` is ``None`` and
        ``unknownAdmission`` is ``True`` - no actual is fabricated, no refund is given and no counter
        or budget is granted.
        """
        if reservation not in self.reserved:
            raise HarnessError(f'unknown reservation {reservation}')
        entry = self.reserved.pop(reservation)
        cap = int(entry['cap'])
        self.claims.append(dict(op='abandon-unknown', id=reservation, stage=entry['stage'], cap=cap,
                                actual=None, chargedUpperBound=cap, unknownAdmission=True,
                                spent=self.spent, reason=str(reason)))
        self._persist()
        return cap

    def charge_setup(self, stage, count):
        count = int(count)
        if count < 0:
            raise HarnessError('setup charge must be non-negative')
        if self.setup_spent + count > self.setup_cap:
            raise Blocked(f'setup cap refused: {stage} charge {count} would take setup to '
                          f'{self.setup_spent + count} above {self.setup_cap}')
        if self.committed + count > self.ceiling:
            raise Blocked(f'global cap refused: setup {stage} would exceed the ceiling {self.ceiling}')
        self.setup_spent += count
        self.claims.append(dict(op='setup', stage=stage, count=count, setupSpent=self.setup_spent))
        self._persist()

    def settled_stages(self):
        """The exact stage labels with a settled (actual-recorded) reservation, for a fail-closed
        resume: a stage whose search already settled must never be reserved or re-run again."""
        return {claim.get('stage') for claim in self.claims if claim.get('op') == 'settle'}

    def snapshot(self):
        return dict(ceiling=self.ceiling, spent=self.spent, setupSpent=self.setup_spent,
                    committed=self.committed, remaining=self.remaining, setupCap=self.setup_cap,
                    outstanding=len(self.reserved), claims=self.claims[-40:])


class ExecutionMeter:
    """A finite, exact execution meter for one authorised phase of one arm subprocess.

    The meter is the authoritative count of native pair requests. Every request is admitted before
    it is submitted, a batch is clamped to the remaining budget before submission, and once nothing
    remains every further call is denied. The cap is never raised; results already in flight when the
    last grant is admitted are still harvested, never dropped. ``on_last`` fires exactly once at the
    final grant so the driver can pause the engine.
    """

    def __init__(self, cap, *, stage, on_last=None):
        self.cap = int(cap)
        self.stage = stage
        self.charged = 0
        self.denied = 0
        self.usedPairs = []
        self._on_last = on_last
        self._last_fired = False
        self._lock = threading.Lock()

    @property
    def remaining(self):
        return max(0, self.cap - self.charged)

    def admit(self, requested):
        requested = int(requested)
        with self._lock:
            remaining = self.remaining
            if remaining <= 0 or requested <= 0:
                self.denied += 1
                raise BudgetExhausted(f'{self.stage}: execution meter exhausted at '
                                      f'{self.charged}/{self.cap}')
            grant = min(requested, remaining)
            self.charged += grant
            if self.charged >= self.cap and not self._last_fired:
                self._last_fired = True
                if self._on_last is not None:
                    self._on_last()
        return grant

    def record(self, pairs):
        for pair in pairs:
            self.usedPairs.append([int(pair[0]), int(pair[1])])

    def snapshot(self):
        return dict(stage=self.stage, cap=self.cap, charged=self.charged, denied=self.denied,
                    usedPairs=len(self.usedPairs))


class TransportCompletions:
    """Thread-safe record of the actual backend of every completed transport dispatch.

    A bounded pool returns futures that resolve to the real compact result(s); the harness records
    the backend of each completion here (single ``submit`` or batched ``submit_batch``), not the
    configured backend. ``native``/``fallback``/``unknown`` and the fallback reasons are reported, so
    a search scored on python-fallback rows can never be presented as a native comparison.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.rows = []

    def observe(self, value):
        """Attach to a returned future, or record a value a plain transport returned directly."""
        attach = getattr(value, 'add_done_callback', None)
        if callable(attach):
            attach(self._resolve)
        else:
            self._resolve_value(value)

    def _resolve(self, future):
        try:
            self._resolve_value(future.result())
        except BaseException as exc:  # noqa: BLE001 - a failed dispatch is an unknown completion
            with self._lock:
                self.rows.append(dict(backend=None, reason=f'dispatch-failed:{type(exc).__name__}'))

    def _resolve_value(self, value):
        entries = value if isinstance(value, (list, tuple)) else [value]
        with self._lock:
            for entry in entries:
                backend = reason = None
                if isinstance(entry, dict):
                    backend = entry.get('resultBackend') or entry.get('executionBackend')
                    reason = entry.get('nativeFallbackReason')
                self.rows.append(dict(backend=backend, reason=reason))

    def summary(self):
        with self._lock:
            rows = list(self.rows)
        native = fallback = unknown = 0
        reasons = {}
        for row in rows:
            backend = row.get('backend')
            if backend == 'native':
                native += 1
            elif backend:
                fallback += 1
            else:
                unknown += 1
            if row.get('reason'):
                reasons[row['reason']] = reasons.get(row['reason'], 0) + 1
        return dict(completed=len(rows), native=native, fallback=fallback, unknown=unknown,
                    reasons=reasons)


def assert_native_completions(summary, *, stage):
    """Fail closed unless every actual transport completion was native (search transport gate)."""
    if not summary.get('completed'):
        raise Blocked(f'{stage}: no transport completion was recorded; refusing to present an '
                      'unobserved search as a native comparison')
    if summary.get('fallback') or summary.get('unknown'):
        raise Blocked(f'{stage}: refusing to compare on a non-native transport completion '
                      f'(native={summary.get("native")}, fallback={summary.get("fallback")}, '
                      f'unknown={summary.get("unknown")}, reasons={summary.get("reasons")})')
    return summary


def install_bounded_dispatch(meter, *, pool_module, dispatch_module=None, optimizer_module=None,
                             pause_enqueue=None, scenario_validator=None, completions=None,
                             pool_registry=None):
    """Wrap the real transport so the meter is charged before any native request is submitted.

    ``submit`` charges exactly one pair; ``submit_batch`` clamps the requested batch to the remaining
    budget *before* submitting; ``batch_size`` is re-published as ``min(recommended, remaining)`` and
    ``duty_ready_slot`` returns None once nothing remains, so no further dispatch can be planned.
    ``pause_enqueue`` is wired as the meter ``on_last`` callback so the final grant pauses the engine.
    ``scenario_validator``, when given, is called with the *actual* dispatched scenario BEFORE the
    meter admits anything: a scenario/scope/owner it refuses can never consume a battle, and it is
    never tested against the declared plan fields in place of the real transport input.
    ``pool_registry``, when given, receives each real pool instance that dispatches, so the observer
    can read the owned worker processes' CPU while they are still alive; it never changes dispatch.
    Returns an uninstall callable.
    """
    if pause_enqueue is not None and meter._on_last is None:
        meter._on_last = pause_enqueue
    restored = []

    def _register_pool(pool):
        if pool_registry is None:
            return
        if not any(existing is pool for existing in pool_registry):
            pool_registry.append(pool)

    if dispatch_module is not None and hasattr(dispatch_module, 'batch_size'):
        original = dispatch_module.batch_size

        def bounded_batch_size(seconds_per_run=None, _original=original):
            value = _original(seconds_per_run)
            remaining = meter.remaining
            if remaining <= 0:
                return 0
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 1
            return max(0, min(value, remaining))

        dispatch_module.batch_size = bounded_batch_size
        restored.append(lambda module=dispatch_module, name='batch_size', value=original:
                        setattr(module, name, value))

    if optimizer_module is not None and hasattr(optimizer_module, 'duty_ready_slot'):
        original_slot = optimizer_module.duty_ready_slot

        def bounded_slot(pending, slot_ready_at, now, _original=original_slot):
            if meter.remaining <= 0:
                return None
            return _original(pending, slot_ready_at, now)

        optimizer_module.duty_ready_slot = bounded_slot
        restored.append(lambda module=optimizer_module, name='duty_ready_slot', value=original_slot:
                        setattr(module, name, value))

    pool_class = pool_module.HeadlessPool
    original_submit = pool_class.submit
    original_submit_batch = getattr(pool_class, 'submit_batch', None)

    def submit(self, function, scenario, seeds):
        _register_pool(self)
        if scenario_validator is not None:
            scenario_validator(scenario)
        meter.admit(1)
        meter.record([seeds])
        result = original_submit(self, function, scenario, seeds)
        if completions is not None:
            completions.observe(result)
        return result

    def submit_batch(self, function, scenario, seed_pairs):
        _register_pool(self)
        if scenario_validator is not None:
            scenario_validator(scenario)
        pairs = [list(pair) for pair in seed_pairs]
        grant = meter.admit(len(pairs))
        if grant < len(pairs):
            pairs = pairs[:grant]
        meter.record(pairs)
        result = original_submit_batch(self, function, scenario, pairs)
        if completions is not None:
            completions.observe(result)
        return result

    pool_class.submit = submit
    if original_submit_batch is not None:
        pool_class.submit_batch = submit_batch
        restored.append(lambda cls=pool_class, value=original_submit_batch:
                        setattr(cls, 'submit_batch', value))
    restored.append(lambda cls=pool_class, value=original_submit: setattr(cls, 'submit', value))

    def uninstall():
        for restore in reversed(restored):
            restore()
    return uninstall


#: The observed-policy dimensions the transport guard freezes and compares. `encounterId` is
#: compared separately (it varies per encounter); these five are the resource/horizon/profile policy.
GUARD_POLICY_KEYS = ('finishPolicy', 'tickLimit', 'holyHerbStock', 'inputs', 'startProfile',
                     'defeatCount')


def observed_policy_fields(scenario):
    """The actual observed policy of a concrete scenario (never a declared plan default)."""
    scenario = scenario or {}
    return dict(encounterId=scenario.get('encounterId'), defeatCount=scenario.get('defeatCount'),
                finishPolicy=scenario.get('finishPolicy'), tickLimit=scenario.get('tickLimit'),
                holyHerbStock=scenario.get('holyHerbStock'), inputs=scenario.get('inputs'),
                startProfile=scenario.get('startProfile'))


#: Dependent observation fields an arm's runtime aligns WITH the frozen policy when it dispatches a
#: stored scenario. They are not separate policy dimensions: when the frozen policy declares the herb
#: unavailable (``holyHerbStock`` 0) while a stored nominee still carries an old
#: ``holyHerbMaxUses``/trigger list, the runtime drops them rather than dispatching a stock-less
#: candidate that still declares uses. The holdout applies exactly this alignment to both arms.
ALIGN_POLICY_KEYS = ('holyHerbMaxUses', 'holyHerbTriggerUnits', 'mpWatchUnits')


def frozen_alignment_fields(policy_fields, reference):
    """The full field set the arm runtime aligns: the declared policy plus any dependent field the
    frozen, arm-normalized reference itself declares. An undeclared dependent field is dropped, never
    filled with an assumed default."""
    fields = dict(policy_fields or {})
    reference = reference or {}
    for key in ALIGN_POLICY_KEYS:
        if key in reference:
            fields[key] = reference[key]
    return fields


def align_scenario_policy(scenario, fields):
    """Apply a frozen policy the way the arm runtime does: copy each declared field and DROP each
    undeclared dependent field, so a stale nominee value can never contradict the frozen policy."""
    updated = dict(scenario or {})
    for key in tuple(GUARD_POLICY_KEYS) + tuple(ALIGN_POLICY_KEYS):
        if key in fields:
            updated[key] = deepcopy(fields[key])
        else:
            updated.pop(key, None)
    return updated


def pinned_reference(plan, arm, encounter_id):
    """The frozen baseline's OWN supplied reference for THIS encounter, pinned in observedPolicy.

    The reference is resolved per encounter from the frozen baseline; a single first-reference is
    never reused across encounters. It is the community template/fodder parent C7/C8 are measured
    from, so both selection and the pre-charge dispatch guard use the same pinned scenario.
    """
    record = (((plan.get('arms') or {}).get(arm) or {}).get('observedPolicy') or {})
    references = record.get('referencesByEncounter') if isinstance(record, dict) else None
    entry = None
    if isinstance(references, dict):
        entry = references.get(str(int(encounter_id)))
    if not isinstance(entry, dict) or not isinstance(entry.get('stored'), dict):
        raise Blocked(f'arm {arm} has no frozen supplied reference pinned for encounter '
                      f'{encounter_id}; refusing to select or guard against an assumption')
    return entry


def build_scenario_guard(plan, arm, encounter_id, *, adapter, students, role='search'):
    """A pre-charge transport guard built from the arm's OWN frozen STORED reference policy.

    It runs inside the arm process and refuses, BEFORE any meter charge, a scenario that is not
    community-admitted, is not the frozen encounter, or whose observed finish/horizon/stock/inputs/
    startProfile do not match the frozen study. The expected policy is the frozen stored-reference
    policy (30000 horizon), and the arm's own normalization path is re-verified against the frozen
    representative stored scenario embedded in the plan -- the adapter's ``default_scenario`` (the
    stale 3000 pause horizon) is never consulted. If the frozen study is missing a dimension, carries
    no stored reference, or the arm now normalizes that reference to a different policy, it fails
    closed instead of assuming a default. Every checked scenario's actual policy hash is recorded on
    the returned callable (``frozenHash``/``hashes``).
    """
    record = (((plan.get('arms') or {}).get(arm) or {}).get('observedPolicy') or {})
    expected = record.get('fields') if isinstance(record, dict) else None
    if not isinstance(expected, dict):
        raise Blocked(f'{role} guard: arm {arm} has no frozen observed policy in the plan')
    for key in GUARD_POLICY_KEYS:
        if key not in expected:
            raise Blocked(f'{role} guard: frozen observed policy for arm {arm} lacks {key!r}; '
                          'refusing to guard against an assumption')
    if expected.get('finishPolicy') is None or expected.get('tickLimit') is None:
        raise Blocked(f'{role} guard: arm {arm} frozen observed policy has no resolved '
                      'finish policy / horizon')
    # Re-verify the arm's REAL normalization path on the encounter's own pinned STORED scenario. The
    # input is a stored reference, so the adapter default (3000) is wrong by construction and is not
    # compared; only the normalized stored policy may decide.
    reference = pinned_reference(plan, arm, encounter_id)
    if adapter is None or not hasattr(adapter, 'validate_scenario'):
        raise Blocked(f'{role} guard: arm {arm} exposes no normalization path for the frozen stored '
                      'reference')
    try:
        normalized_reference = adapter.validate_scenario(deepcopy(reference['stored']))
    except Exception as exc:  # noqa: BLE001 - a normalization failure must fail closed, not pass
        raise Blocked(f'{role} guard: arm {arm} could not normalize the frozen stored reference: '
                      f'{exc}')
    own = {key: (normalized_reference or {}).get(key) for key in GUARD_POLICY_KEYS}
    for key in GUARD_POLICY_KEYS:
        if canonical(own.get(key)) != canonical(expected.get(key)):
            raise Blocked(f'{role} guard: arm {arm} normalizes the frozen stored reference {key} to '
                          f'{own.get(key)!r}, not the frozen actual policy {expected.get(key)!r}; '
                          'failing closed')
    # The community template C7 (skill-set held) and fodder contract C8 (fodder no tougher than the
    # community fodder) are measured from the encounter's own pinned supplied reference, not from an
    # absolute default.
    reference_scenario = (normalized_reference if isinstance(normalized_reference, dict)
                          else reference['stored'])
    expected_encounter = int(encounter_id)
    policy_fields = {key: expected.get(key) for key in GUARD_POLICY_KEYS}
    alignment_fields = frozen_alignment_fields(policy_fields, reference_scenario)

    def validate(scenario):
        if not isinstance(scenario, dict):
            raise Blocked(f'{role} guard: dispatched scenario is not a mapping')
        actual_encounter = scenario.get('encounterId')
        if actual_encounter is None or int(actual_encounter) != expected_encounter:
            raise Blocked(f'{role} guard: scenario encounter {actual_encounter!r} is not the frozen '
                          f'encounter {expected_encounter}')
        admitted, failures = students.community_admits(scenario, parent=reference_scenario)
        if not admitted:
            raise Blocked(f'{role} guard: scenario is not community-admitted: {failures}')
        actual = observed_policy_fields(scenario)
        for key in GUARD_POLICY_KEYS:
            if canonical(actual.get(key)) != canonical(expected.get(key)):
                raise Blocked(f'{role} guard: scenario {key} {actual.get(key)!r} does not match the '
                              f'frozen observed policy {expected.get(key)!r}')
        validate.hashes.add(digest({key: actual.get(key) for key in GUARD_POLICY_KEYS}))
        return actual

    validate.hashes = set()
    validate.frozenHash = digest(policy_fields)
    validate.policyFields = policy_fields
    validate.alignmentFields = alignment_fields
    validate.expectedEncounter = expected_encounter
    return validate


def apply_frozen_policy(scenario, guard):
    """Explicitly apply the frozen observed policy to a stored scenario before dispatch (holdout).

    A raw stored scenario may not carry the policy the study was frozen with; the policy is applied
    here, explicitly and reversibly, so the dispatched input is the study's policy and the guard then
    verifies the actual submitted scenario. It never invents values: every field comes from the guard.

    The alignment mirrors the arm runtime's own normalization: a dependent field the frozen policy
    does not declare (an old ``holyHerbMaxUses``/trigger list from a stock-10 nominee when the study
    is stock 0) is DROPPED rather than left to contradict the declared policy. This is the exact bug
    the stopped holdout hit; the same alignment is applied to both arms.
    """
    return align_scenario_policy(scenario, getattr(guard, 'alignmentFields', None)
                                 or guard.policyFields)


# --------------------------------------------------------------------------------------------------
# seed derivation (fresh, global, common within a comparison)
# --------------------------------------------------------------------------------------------------
def _randbits_stream(seed_bytes):
    state = int.from_bytes(seed_bytes[:16], 'big') or 1

    def randbits(bits):
        nonlocal state
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return state >> (64 - bits)

    return randbits


def derive_pairs(plan_id, label, count):
    """Deterministic int31 pairs: the same plan yields the same pairs anywhere."""
    randbits = _randbits_stream(hashlib.sha256(f'{plan_id}|{label}'.encode('utf-8')).digest())
    pairs, seen = [], set()
    while len(pairs) < count:
        pair = (randbits(31), randbits(31))
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs


def parse_seed_pairs(values):
    out = []
    for raw in values or ():
        if isinstance(raw, str):
            raw = json.loads(raw)
        if (not isinstance(raw, (list, tuple)) or len(raw) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= SEED_MAX
                           for v in raw)):
            raise HarnessError(f'seed pair must be two int31 values: {raw!r}')
        out.append((int(raw[0]), int(raw[1])))
    return out


REGISTRY_SCHEMA = 'encounter-redesign-holdout-registry-1'


class HoldoutRegistry:
    """A global registry of freshly issued holdout pairs for one plan, across all replicates.

    Pairs are derived deterministically from the plan id and a reproducible global nonce label. Any
    candidate that collides with a forbidden reserved seed or with a pair already issued anywhere in
    the plan is rejected and the deterministic stream advanced to the next candidate, so a resumed
    run cannot reissue a pair an earlier replicate already consumed.
    """

    def __init__(self, path, plan_id, nonce):
        self.path = Path(path)
        self.plan_id = plan_id
        self.nonce = nonce
        self.issued = {}
        self._load()

    def _load(self):
        if not self.path.is_file():
            return
        doc = read_json(self.path)
        if doc.get('schema') != REGISTRY_SCHEMA:
            raise Blocked('holdout registry schema mismatch; refusing to resume')
        if doc.get('planId') != self.plan_id or doc.get('nonce') != self.nonce:
            raise Blocked('holdout registry belongs to a different plan or nonce; refusing to resume')
        self.issued = {str(label): [(int(a), int(b)) for a, b in pairs]
                       for label, pairs in (doc.get('issued') or {}).items()}

    def _persist(self):
        write_json(self.path, dict(schema=REGISTRY_SCHEMA, planId=self.plan_id, nonce=self.nonce,
                                   issued={label: [list(pair) for pair in pairs]
                                           for label, pairs in self.issued.items()}))

    def _used(self):
        used = set()
        for pairs in self.issued.values():
            used.update(pairs)
        return used

    def issue(self, label, count, *, forbidden):
        if label in self.issued:
            raise Blocked(f'holdout label {label} was already issued; refusing to reuse it')
        blocked = {tuple(int(v) for v in pair) for pair in forbidden} | self._used()
        stream = _randbits_stream(hashlib.sha256(
            f'{self.plan_id}|{self.nonce}|{label}'.encode('utf-8')).digest())
        pairs, seen, attempts = [], set(), 0
        while len(pairs) < int(count):
            attempts += 1
            if attempts > 500000:
                raise Blocked(f'could not derive {count} collision-free holdout pairs for {label}')
            pair = (stream(31), stream(31))
            if pair in seen or pair in blocked:
                continue
            seen.add(pair)
            pairs.append(pair)
        self.issued[label] = pairs
        self._persist()
        return pairs

    def issue_or_adopt(self, label, count, *, forbidden, used_seeds=()):
        """Return a label's EXISTING pairs when a same-plan resume has already issued them.

        A resumed current run must never reissue a holdout label. If the label is present its exact
        persisted pairs are adopted, but only after re-checking that they were not consumed: every
        adopted pair must be absent from the search seeds already spent on this unit (``used_seeds``)
        and from the forbidden bank outside the label itself. A count mismatch, a missing pair, a
        used search seed or a collision with the library bank fails closed instead of silently
        deriving a fresh set (which would hide a repetition).
        """
        if label not in self.issued:
            return self.issue(label, count, forbidden=forbidden)
        pairs = [tuple(int(v) for v in pair) for pair in self.issued[label]]
        if len(pairs) != int(count):
            raise Blocked(f'holdout label {label} was issued with {len(pairs)} pairs, not '
                          f'{int(count)}; refusing a partial resume')
        own = set(pairs)
        spent = {tuple(int(v) for v in pair) for pair in used_seeds}
        clash = own & spent
        if clash:
            raise Blocked(f'holdout label {label} reuses {len(clash)} search seed pair(s); refusing '
                          'to hide a repetition')
        # The caller excludes this label from registry reservations BEFORE combining them with
        # library history. Subtracting own here would also erase a real history collision.
        external = {tuple(int(v) for v in pair) for pair in forbidden}
        clash = own & external
        if clash:
            raise Blocked(f'holdout label {label} collides with the reserved bank on {len(clash)} '
                          'pair(s); refusing to reuse it')
        return pairs


# --------------------------------------------------------------------------------------------------
# representative encounters (data-derived proposal, never asserted game truth)
# --------------------------------------------------------------------------------------------------
def resolve_representatives(evidence_dir, explicit):
    if explicit:
        ids = [int(part) for part in str(explicit).replace(';', ',').split(',') if part.strip()]
        roles = {0: 'short-cure', 15: 'many-healers', 18: 'long-community', 19: 'long-cure'}
        return [dict(id=i, role=roles.get(i, 'explicit'), basis='declared on the command line')
                for i in ids], []
    roles = json.loads((Path(evidence_dir) / 'enemy-combat-roles.json').read_text(encoding='utf-8'))
    rows = roles['encounters']
    healers = [e for e in rows if e['healerCount'] > 0]
    blocks, proposal = [], []
    if healers:
        fewest = min(len(e['fighters']) for e in healers)
        short = max((e for e in healers if len(e['fighters']) == fewest),
                    key=lambda e: (e['healerCount'], -e['encounterId']))
        proposal.append(dict(id=short['encounterId'], role='short-cure-heavy',
                             basis=f"data-derived proposal: fewest fighters ({fewest}) among "
                                   f"healer-bearing encounters, most healers ({short['healerCount']}); "
                                   f"{short['title']}"))
    else:
        blocks.append('no healer-bearing encounter in the frozen roles evidence')
    proposal.append(dict(id=None, role='dex-sensitive',
                         basis='unresolved: the frozen roles evidence carries no Speed/accuracy data, '
                               'so a DEX-sensitive encounter is not derived here (do not infer game '
                               'facts). Pass --encounters with a reviewed id.'))
    for encounter_id, role in ((18, 'long-community'), (19, 'long-cure')):
        proposal.append(dict(id=encounter_id, role=role, basis='fixed by the implementation command'))
    if any(row['id'] is None for row in proposal):
        blocks.append('at least one representative encounter is unresolved; pass --encounters <ids>')
    return proposal, blocks


# --------------------------------------------------------------------------------------------------
# canonical outcome + statistics
# --------------------------------------------------------------------------------------------------
def apply_canonical(rows, policy):
    """Apply the current canonical terminal earned contract and keep every status count.

    The canonical observer is loaded from the current tree by explicit absolute path, so a legacy
    arm subprocess never imports the altered modules and the parent never resolves the contract
    through sys.path order.
    """
    global _CANONICAL_OUTCOMES
    if _CANONICAL_OUTCOMES is None:
        _CANONICAL_OUTCOMES = load_canonical_outcomes()
    outcomes_service = _CANONICAL_OUTCOMES
    applied = []
    counts = dict(total=0, resolved=0, unresolved=0, censored=0, error=0, loss=0, certified=0,
                  terminalPolicy=0, winUnproven=0)
    for row in rows:
        outcome = outcomes_service.outcome(row['raw'], candidate_id=row.get('candidateId'),
                                           encounter_revision=row.get('encounterId'),
                                           experiment_id=row.get('experimentId'), policy=policy)
        applied.append(dict(row, outcome=outcome))
        counts['total'] += 1
        counts['error'] += int(bool(outcome.get('error')))
        counts['censored'] += int(bool(outcome.get('censored')))
        counts['resolved'] += int(bool(outcome.get('resolved')))
        counts['unresolved'] += int(not outcome.get('resolved'))
        status = outcome.get('status')
        if status == outcomes_service.LOSS:
            counts['loss'] += 1
        elif status == outcomes_service.CERTIFIED:
            counts['certified'] += 1
        elif status == outcomes_service.TERMINAL_POLICY:
            counts['terminalPolicy'] += 1
        elif status == outcomes_service.WIN_UNPROVEN:
            counts['winUnproven'] += 1
    return applied, counts


def earned_cells(rows):
    """{(candidateId, pair): finalEarned or None}; unresolved stays None, never zero."""
    cells = {}
    for row in rows:
        outcome = row['outcome']
        key = (row.get('candidateId'), tuple(row.get('seeds') or ()))
        cells[key] = outcome.get('finalEarned') if outcome.get('resolved') else None
    return cells


def select_nominee(measured):
    """Best eligible mean earned, never a jackpot/best field. Returns (candidateId, detail).

    A row that explicitly declares itself ineligible (``eligible``/``eligibleForRecommendation`` is
    False) is excluded even when it carries the highest mean, and an empty or fully-ineligible view
    returns an explicit no-eligible record rather than a fallback nominee.
    """
    eligible = [row for row in measured
                if row.get('meanEarned') is not None and str(row.get('candidateId') or '')
                and row.get('eligible') is not False
                and row.get('eligibleForRecommendation') is not False]
    if not eligible:
        return None, dict(noEligible=True,
                          reason='no candidate has a resolved meanEarned; selection never falls back '
                                 'to a jackpot or best field')
    best = max(eligible, key=lambda row: (float(row['meanEarned']), str(row['candidateId'])))
    return best['candidateId'], dict(noEligible=False, eligibleCount=len(eligible),
                                     consideredCount=len(measured), meanEarned=best['meanEarned'],
                                     reliability=best.get('standardError', best.get('reliability')),
                                     key='meanEarned')


def measured_view(status, arm):
    """The arm's own measured candidate view: the new portfolio or the legacy mean view.

    This is the raw view; scope filtering to the frozen Community/template/constraints happens in
    ``freeze_nomination`` against the actual stored candidate scenarios before any selection.
    """
    if arm == ARM_NEW:
        aware = status.get('encounterAware') or {}
        return [row for row in aware.get('portfolio') or [] if isinstance(row, dict)]
    return _collect_measured(status.get('focusPortfolio'), [])


def _normalise_measured_row(candidate_id, encounter_id, record, *, source, evidence_class=None):
    """One measured row in the canonical portfolio shape shared by both arms' durable stores."""
    eligible = record.get('eligible')
    if eligible is None:
        eligible = record.get('eligibleForRecommendation')
    return dict(candidateId=str(candidate_id), encounterId=int(encounter_id),
                meanEarned=float(record['meanEarned']),
                resolved=record.get('resolved'), total=record.get('total'),
                standardError=record.get('standardError', record.get('standard_error')),
                eligible=eligible, measuredSource=source, evidenceClass=evidence_class or source)


def _finite_number(value):
    """True only for a real, finite int/float (never a bool, None, NaN or infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


# NOTE: there is deliberately NO new-arm durable ``ea_earning_portfolio`` reader. Selecting the
# most-sampled/highest-mean row across every past window/experiment/revision is a confirmation leak,
# so the new arm nominates ONLY from its authoritative public portfolio (the live
# ``status.encounterAware.portfolio`` captured after the shutdown refresh). An empty authoritative
# view stays empty and selects no nominee, rather than guessing from an unrelated durable row.


def _legacy_aggregate_row(connection, candidate_id, encounter_id):
    """The legacy arm's own ORIGINAL arithmetic chest aggregate for one candidate, or None.

    The frozen legacy Store maintains the per-candidate ``aggregate:<id>:validation`` counter the
    planner, the published overview and ``lane_record`` all read; its chest mean is the same
    arithmetic ``chestSum / chestCount`` average the overview's own average column shows. This is the
    baseline's OWN original policy aggregate (the ``community_parent`` path) - NOT a canonical
    confirmed or full-resolved reward and NOT a certainty claim. It is labelled ``measuredSource`` and
    ``evidenceClass`` = 'legacy-validation-aggregate'; an actual comparative claim is only ever made
    from the fresh canonical common holdout, never from this aggregate. Read directly (bounded, one
    indexed meta row per candidate), never by decoding the retained run window. A missing,
    non-numeric or non-finite counter yields NO row (no mean), never a fabricated zero.
    """
    try:
        row = connection.execute('SELECT value FROM meta WHERE key=?',
                                 (f'aggregate:{candidate_id}:validation',)).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    try:
        total = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(total, dict):
        return None
    chest_count_raw, chest_sum_raw = total.get('chestCount'), total.get('chestSum')
    if not _finite_number(chest_count_raw) or float(chest_count_raw) <= 0:
        return None
    if not _finite_number(chest_sum_raw):
        return None
    chest_count = int(chest_count_raw)
    chest_sum = float(chest_sum_raw)
    standard_error = None
    if chest_count > 1 and _finite_number(total.get('chestSum2')):
        variance = max(0.0, (float(total['chestSum2']) - chest_sum * chest_sum / chest_count)
                       / (chest_count - 1))
        standard_error = math.sqrt(variance) / math.sqrt(chest_count)
    runs_raw = total.get('n')
    total_runs = int(runs_raw) if _finite_number(runs_raw) else None
    return _normalise_measured_row(
        candidate_id, encounter_id,
        dict(meanEarned=chest_sum / chest_count, resolved=chest_count,
             total=total_runs, standardError=standard_error),
        source='legacy-validation-aggregate', evidence_class='legacy-validation-aggregate')


def stored_measured_snapshot(copy_path, encounter_id, *, arm=None):
    """A bounded, read-only LEGACY measured-candidate snapshot read from the arm's own durable store.

    Only the frozen legacy arm may fall back to its durable store: the legacy search's
    ``focusPortfolio`` can legitimately be empty, and its own original arithmetic validation
    aggregate is the baseline policy (labelled 'legacy-validation-aggregate'). The NEW arm must
    nominate from its authoritative public portfolio - the live ``status.encounterAware.portfolio``
    captured after the shutdown refresh - so it has NO durable-EA fallback here and simply returns an
    empty view (an empty authoritative view selects no nominee rather than guessing from an aggregate
    over every past window/experiment/revision).

    It reads ONLY this encounter's candidates (indexed by ``candidate_meta``, capped) and only each
    candidate's own persisted ``aggregate:<id>:validation`` counter - never a whole-library scan and
    never the retained run window. It fabricates nothing: a candidate with no resolved measurement
    produces no row, and an empty result stays empty.
    """
    if arm == ARM_NEW:
        return []
    connection = sqlite3.connect(Path(copy_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    try:
        connection.execute('PRAGMA query_only=ON')
        try:
            candidate_ids = [row[0] for row in connection.execute(
                'SELECT c.id FROM candidate c JOIN candidate_meta m ON m.id=c.id '
                'WHERE m.encounter=? ORDER BY c.created, c.id LIMIT ?',
                (int(encounter_id), MEASURED_CANDIDATE_LIMIT)).fetchall()]
        except sqlite3.Error:
            return []
        rows = []
        for candidate_id in candidate_ids:
            row = _legacy_aggregate_row(connection, candidate_id, encounter_id)
            if row is not None:
                rows.append(row)
        return rows
    finally:
        connection.close()


def _scope_measured(rows, eligible_ids, encounter_id):
    """Keep only rows whose candidateId is in the frozen eligible scope (and matches the encounter)."""
    out = []
    for row in rows:
        candidate_id = row.get('candidateId')
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if encounter_id is not None and row.get('encounterId') is not None:
            try:
                if int(row['encounterId']) != int(encounter_id):
                    continue
            except (TypeError, ValueError):
                continue
        if eligible_ids is not None and candidate_id not in eligible_ids:
            continue
        out.append(row)
    return out


def admitted_candidate_ids(copy_path, candidate_ids, encounter_id, reference_scenario, students):
    """The frozen scope: ACTUAL stored candidate scenarios admitted to the declared Community.

    Each measured candidate id is read from the arm's own copy and admitted only when its stored
    scenario is the frozen encounter and ``students.community_admits`` holds RELATIVE to the
    encounter's own pinned supplied reference (so the community template C7 and the fodder contract
    C8 are enforced, not assumed). Applies identically to both arms; a foreign past-stream family is
    never a nominee.
    """
    ids = [cid for cid in candidate_ids if isinstance(cid, str) and cid]
    scenarios = _read_scenarios(copy_path, ids)
    admitted = set()
    for candidate_id, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            continue
        if scenario.get('encounterId') is not None:
            try:
                if int(scenario['encounterId']) != int(encounter_id):
                    continue
            except (TypeError, ValueError):
                continue
        try:
            ok, _failures = students.community_admits(scenario, parent=reference_scenario)
        except TypeError:
            raise Blocked('community_admits does not accept the pinned reference parent; refusing '
                          'to select or guard against an assumed community contract')
        if ok:
            admitted.add(candidate_id)
    return admitted


def paired_bootstrap(deltas, *, resamples=10000, seed=0):
    n = len(deltas)
    if n == 0:
        return dict(pairs=0, mean=None, fractionPositive=None, ci95=None, resamples=resamples, seed=seed)
    rng = random.Random(seed)
    means = [sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples)]
    means.sort()
    low = means[max(0, int(0.025 * resamples) - 1)]
    high = means[min(resamples - 1, int(0.975 * resamples))]
    return dict(pairs=n, mean=sum(deltas) / n,
                fractionPositive=sum(1 for m in means if m > 0) / resamples,
                ci95=[low, high], resamples=resamples, seed=seed)


# --------------------------------------------------------------------------------------------------
# plan construction / validation
# --------------------------------------------------------------------------------------------------
def copy_storage_config():
    """The declared copy-only storage policy, stated once and applied identically to both arms.

    Only the temporary unit copies (peak two: both arms of one unit) carry this ceiling; the frozen
    baseline is opened read-only and never raised. ``requiredFreeMiB`` is the bounded headroom the
    copy filesystem must have before any copy is made: peak copies plus a bounded WAL.
    """
    return dict(
        copyMiB=COPY_STORAGE_MIB, baselineMiB=OPTIMIZER_DEFAULT_DB_MIB,
        optimizerDefaultMiB=OPTIMIZER_DEFAULT_DB_MIB,
        appliesTo='temporary unit copies only; the frozen baseline is opened read-only and never raised',
        scope='both arms identically, applied process-local in the arm child immediately before any '
              'Store is constructed; never written to persisted live config and never source-altered',
        peakCopies=COPY_PEAK_COPIES, walMiB=COPY_WAL_MIB, requiredFreeMiB=COPY_REQUIRED_FREE_MIB,
        deviation='explicit test-scope deviation from the optimiser 4096 MiB default; identical for '
                  'both arms; no policy/search/selection/migration semantics change')


def declared_copy_storage_mib(plan):
    """The MiB copy ceiling the plan declares, defaulting to the declared copy limit."""
    storage = plan.get('storage') or {}
    value = storage.get('copyMiB')
    return int(value) if value is not None else COPY_STORAGE_MIB


def apply_copy_storage_limit(plan, optimizer_module):
    """Set the process-local copy Store ceiling before any Store is constructed.

    Called in the arm child for both arms with the same plan value, so a legacy and a current runtime
    receive the identical copy-only ceiling. It never lowers a module below its own default (a plan
    cannot silently shrink the guard) and never persists anything.
    """
    value = declared_copy_storage_mib(plan)
    current = int(getattr(optimizer_module, 'MAX_DB_MB', value))
    if value < current:
        raise Blocked(f'refusing to lower the copy Store ceiling below the module default '
                      f'({value} < {current})')
    optimizer_module.MAX_DB_MB = value
    return value


def build_plan(args, *, python):
    baseline = read_baseline_binding(args.baseline)
    if args.hash_baseline:
        hasher = hashlib.sha256()
        with open(args.baseline, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 22), b''):
                hasher.update(chunk)
        baseline['sha256'] = hasher.hexdigest()
        baseline['sha256Note'] = 'sha256 of the whole snapshot'
    representatives, blocks = resolve_representatives(args.evidence, args.encounters)
    encounter_ids = [row['id'] for row in representatives if row['id'] is not None]
    encounter_count = len(encounter_ids)
    if encounter_count == 0:
        blocks.append('no representative encounter is resolved')
    budget = build_budget(max(encounter_count, 1), args.replicates,
                          search_battles=args.search_battles, holdout_pairs=args.holdout_pairs,
                          throughput_battles=args.throughput_battles)
    meta_scope = read_scope_record(baseline)
    arm_sources = {ARM_LEGACY: str(args.legacy_source), ARM_NEW: str(args.current_source)}
    # The independent implementation inventory now covers strategy_support_evidence.py. The final plan
    # is deferred until that module actually exists in the current arm source (the frozen legacy tree
    # predates it), so the frozen inventory is never a half-created file.
    if not (Path(args.current_source) / 'strategy_support_evidence.py').is_file():
        blocks.append('strategy_support_evidence.py is not present in the current arm source; the '
                      'final implementation inventory and plan are deferred until it exists')
    arm_probes = {arm: probe_arm_policy(python, root, args.baseline, encounter_ids)
                  for arm, root in arm_sources.items()}
    # A stored-reference probe that failed, disagreed between references, or could not find a supplied
    # reference for a declared encounter is an open item, never silently replaced by a default policy.
    for arm in ARMS:
        probe = arm_probes[arm] or {}
        if not probe.get('ok'):
            blocks.append(f'arm {arm} stored-reference policy probe failed: {probe.get("error")}')
        elif not probe.get('observed'):
            blocks.append(f'arm {arm} stored-reference policy probe observed no supplied scenario')
        for conflict in probe.get('conflicts') or []:
            blocks.append(f'arm {arm} stored-reference policy conflict: {canonical(conflict)}')
        if probe.get('missingEncounters'):
            blocks.append(f'arm {arm} has no supplied stored reference for encounters '
                          f'{probe["missingEncounters"]}')
    observed_scopes = {arm: _observed_policy_record(arm_probes[arm])['fields'] for arm in ARMS}
    # The declared plan scope is the policy the arms ACTUALLY dispatch (their own observed stored
    # supplied referenced policy), reconciled against the frozen meta reading; a meta/arm disagreement
    # is recorded, never silently reported as the meta value.
    scope, scope_conflicts = resolve_actual_scope(meta_scope, observed_scopes)
    implementation_probes = {arm: probe_implementation_inventory(python, root)
                             for arm, root in arm_sources.items()}
    observer = observer_inventory()
    arm_inventories = {
        arm: _implementation_inventory_record(implementation_probes[arm],
                                              canonical_files=(arm_probes[arm].get('files') or {}),
                                              observer=observer)
        for arm in ARMS}
    plan_id = digest(dict(schema=PLAN_SCHEMA, baseline=baseline.get('storedProvenanceDigest'),
                          candidateCount=baseline.get('candidateCount'),
                          lifetimeRuns=baseline.get('lifetimeRuns'), encounters=representatives,
                          replicates=args.replicates, budget=budget, seedRoot=args.seed_root,
                          created=now()))[:32]
    smoke_pairs = derive_pairs(plan_id, 'smoke-pairs', 2)
    body = dict(
        schema=PLAN_SCHEMA, planId=plan_id, createdAt=now(), status='proposed',
        purpose='fair real legacy-vs-new encounter search comparison (implementation command 15/16)',
        baseline=baseline,
        scopeDeclaration=meta_scope, scopeConflicts=scope_conflicts,
        arms={
            ARM_LEGACY: dict(label='frozen legacy public/runtime planner',
                             source=str(args.legacy_source), pythonPath=str(args.legacy_source),
                             sourceInventory=_source_inventory(arm_probes[ARM_LEGACY]),
                             policy=_arm_policy_record(arm_probes[ARM_LEGACY]),
                             observedPolicy=_observed_policy_record(arm_probes[ARM_LEGACY]),
                             implementationInventory=arm_inventories[ARM_LEGACY]),
            ARM_NEW: dict(label='Community-first Coordinator through the Optimizer loop',
                          source=str(args.current_source), pythonPath=str(args.current_source),
                          sourceInventory=_source_inventory(arm_probes[ARM_NEW]),
                          policy=_arm_policy_record(arm_probes[ARM_NEW]),
                          observedPolicy=_observed_policy_record(arm_probes[ARM_NEW]),
                          implementationInventory=arm_inventories[ARM_NEW]),
        },
        parity=dict(required=True, expectedStoredDigest=baseline.get('storedProvenanceDigest'),
                    gate='unknown', module='strategy_encounter_migration',
                    rule='the new arm is admitted only through the explicitly reviewed observer '
                         'migration: strategy_encounter_migration.preview(legacyInventory, '
                         'newInventory) must be eligible against the checked-in pinned certificate, '
                         'and the runtime applies it to the paused copy (the Store honours the applied '
                         'proof) on an explicit activation. same_simulator alone never authorises the '
                         'transition and no arbitrary manifest verdict string is accepted.'),
        evidenceMode=args.evidence_mode, encounters=representatives, replicates=args.replicates,
        budget=budget,
        smoke=dict(encounters=list(range(20)), fixedPairs=[list(p) for p in smoke_pairs],
                   pairsPerEncounter=2),
        holdout=dict(battlesPerArmPerEncounterPerReplicate=args.holdout_pairs, nomineeOnly=True,
                     commonWithinComparison=True, freezeBothArmsBeforeHoldout=True, globalFresh=True,
                     unionAcrossWholeLibrary=True, registry='holdout-registry.json',
                     globalNonce=args.holdout_nonce,
                     seedDerivation='sha256(planId|nonce|holdout|encounter|replicate|index) -> two '
                                    'int31 values, identical for both arms, collision-rejected against '
                                    'the WHOLE library on both arm copies (every candidate, every '
                                    'ea_* reservation and frozen holdout, the global deterministic '
                                    'seed_pair banks plus the aggregate/probe/fine-tune ceilings and '
                                    'every explicit compact-evidence pair) and every issued pair'),
        scope=scope,
        throughput=dict(modes=list(THROUGHPUT_MODES), battlesPerMode=args.throughput_battles,
                        matchedModes=list(MATCHED_MODES), fixedCandidatesAndPairs=True,
                        note='telemetry-off/on run the current runtime pool on identical work with '
                             'telemetry off/on; legacy-dispatch/new-dispatch run one identical '
                             'finite fixed workload (same candidate, seed_pair sequence, count and '
                             'worker concurrency) persisted through the frozen legacy '
                             'HeadlessPool+Store.record path versus the current Evaluator+ea '
                             'ledger; preparation/dispatch/persistence/analysis wall and parent CPU '
                             'are recorded separately and one-time per-build work is reported apart '
                             'from the per-battle infrastructure comparison'),
        policy=dict(scope=scope,
                    allowedDomain='search_contract current shared template/domain',
                    note='the exact scope record (tickLimit, finishPolicy, holyHerbStock, inputs, '
                         'defeatCount) is read once from the frozen meta; the Finish policy each arm '
                         'is scored with is resolved per arm (see arms.<arm>.policy) from that arm '
                         'own tree and must agree with this declared scope, so neither arm is '
                         'rescored with the other tree default'),
        statistics=dict(metric='finalEarned', reliabilityThreshold=args.reliability,
                        bootstrap=dict(resamples=args.bootstrap, seed=int(plan_id[:8], 16)),
                        unknownPolicy='inconclusive', minimumResolvedFraction=1.0,
                        note='candidate-level final mean + paired bootstrap diagnostic; any unresolved '
                             'reading makes the comparison inconclusive'),
        schedule=dict(order='both arms search and freeze for each encounter/replicate before any '
                            'holdout in that comparison',
                      replicates=args.replicates),
        copyPolicy=dict(mode='sqlite-online-backup', serial=True, peakCopies=2,
                        reuse='every (arm, encounter, replicate) gets its own fresh baseline copy so '
                              'no encounter reuses a spent coordinator config/session; both copies '
                              'of a unit are deleted only after that unit nomination and holdout '
                              'rows are persisted',
                        note='the baseline snapshot is never written and is never deleted; only '
                             'registered unit copies under the output dir are removed'),
        storage=copy_storage_config(),
        openItems=blocks, authorisation=None,
    )
    body['schedule']['steps'] = [f"{row['op']}:{row['arm']}:e{row['encounterId']}:r{row['replicate']}"
                                 for row in plan_steps(body)]
    body['digest'] = digest({k: v for k, v in body.items() if k != 'digest'})
    return body


def validate_plan(plan, *, check_baseline=False):
    errors = []
    if plan.get('schema') != PLAN_SCHEMA:
        errors.append(f"plan schema {plan.get('schema')!r} is not {PLAN_SCHEMA!r}")
    if plan.get('digest') != digest({k: v for k, v in plan.items() if k != 'digest'}):
        errors.append('plan digest does not match its body (the plan was edited after it was written)')
    budget = plan.get('budget') or {}
    stages = budget.get('stages') or {}
    total = sum(int((stages.get(name) or {}).get('battles') or 0)
                for name in ('smoke', 'search', 'holdout', 'throughput'))
    if total != budget.get('plannedBattles'):
        errors.append(f'stage battles {total} do not sum to plannedBattles '
                      f'{budget.get("plannedBattles")}')
    if budget.get('hardCeiling') != (budget.get('plannedBattles') or 0) + (budget.get('setupCap') or 0):
        errors.append('hardCeiling is not plannedBattles + setupCap')
    setup_total = sum((budget.get('setup') or {}).values())
    if setup_total != budget.get('setupBattles'):
        errors.append('setup itemisation does not match setupBattles')
    if setup_total > (budget.get('setupCap') or 0):
        errors.append('setup itemisation exceeds setupCap')
    # The copy-only storage declaration is validated WHEN PRESENT, so a plan written before the
    # declaration existed (the stopped run) can still be re-derived for a carryforward comparison,
    # while any plan that declares it must declare the exact reviewed, both-arms-identical limit.
    storage = plan.get('storage')
    if storage is not None:
        if int(storage.get('copyMiB') or 0) != COPY_STORAGE_MIB:
            errors.append(f"copy storage limit {storage.get('copyMiB')!r} MiB is not the declared "
                          f"{COPY_STORAGE_MIB} MiB copy-only limit")
        if int(storage.get('baselineMiB') or 0) != OPTIMIZER_DEFAULT_DB_MIB:
            errors.append('the copy storage declaration must leave the frozen baseline limit '
                          f'unchanged at {OPTIMIZER_DEFAULT_DB_MIB} MiB')
        if int(storage.get('peakCopies') or 0) != COPY_PEAK_COPIES:
            errors.append(f'copy storage declaration must bound peak copies to {COPY_PEAK_COPIES}')
        if int(storage.get('requiredFreeMiB') or 0) != COPY_REQUIRED_FREE_MIB:
            errors.append(f'copy storage declaration must declare {COPY_REQUIRED_FREE_MIB} MiB of '
                          'bounded copy filesystem headroom (peak copies + WAL)')
        if 'copies only' not in str(storage.get('appliesTo') or ''):
            errors.append('copy storage declaration must state it applies to copies only, never the '
                          'frozen baseline')
    for arm in ARMS:
        arm_record = (plan.get('arms') or {}).get(arm) or {}
        if not arm_record.get('source'):
            errors.append(f'arm {arm} has no source root')
        inventory = arm_record.get('sourceInventory') or {}
        if not inventory.get('digest') or not inventory.get('files'):
            errors.append(f'arm {arm} has no frozen source inventory (digest + bounded file set)')
        implementation = arm_record.get('implementationInventory') or {}
        if not implementation.get('digest') or not implementation.get('files'):
            errors.append(f'arm {arm} has no frozen independent implementation inventory '
                          '(bounded strategy/search/ABI + native DLLs + canonical runtime/data)')
        if not implementation.get('observerRevision'):
            errors.append(f'arm {arm} implementation inventory carries no frozen harness observer '
                          'revision')
        arm_policy_record = arm_record.get('policy') or {}
        if arm_policy_record.get('finishPolicy') is None:
            errors.append(f'arm {arm} has no resolved per-arm Finish policy')
        observed = arm_record.get('observedPolicy') or {}
        observed_fields = observed.get('fields') if isinstance(observed, dict) else None
        if not isinstance(observed_fields, dict) or observed_fields.get('finishPolicy') is None \
                or observed_fields.get('tickLimit') is None:
            errors.append(f'arm {arm} has no frozen observed policy (finish/horizon/stock/inputs/'
                          'startProfile)')
    declared_finish = _declared_finish_policy(plan)
    for arm in ARMS:
        resolved = (((plan.get('arms') or {}).get(arm) or {}).get('policy') or {}).get('finishPolicy')
        if resolved is not None and declared_finish is not None and resolved != declared_finish:
            errors.append(f'arm {arm} Finish policy {resolved!r} disagrees with the declared scope '
                          f'policy {declared_finish!r}')
    # The resource/horizon policy is not only the Finish dimension: the two arms' own recorded
    # scenario fields must agree on horizon, stock and inputs too (the adapter default horizon may
    # legitimately differ from the library scope, so this compares the arms to each other, never the
    # adapter default to the library scope).
    legacy_scope = (((plan.get('arms') or {}).get(ARM_LEGACY) or {}).get('policy') or {}).get(
        'scenarioScope') or {}
    new_scope = (((plan.get('arms') or {}).get(ARM_NEW) or {}).get('policy') or {}).get(
        'scenarioScope') or {}
    for key in ('tickLimit', 'holyHerbStock', 'defeatCount', 'inputs'):
        legacy_value, new_value = legacy_scope.get(key), new_scope.get(key)
        if legacy_value is not None and new_value is not None and legacy_value != new_value:
            errors.append(f'arms differ on resource/horizon policy {key}: legacy {legacy_value!r} '
                          f'vs new {new_value!r}')
    # The frozen observed policies (what each arm's own default scenario actually reads) must put
    # both arms in the same allowed space, otherwise the search spaces are not comparable.
    legacy_observed = (((plan.get('arms') or {}).get(ARM_LEGACY) or {}).get('observedPolicy')
                       or {}).get('fields') or {}
    new_observed = (((plan.get('arms') or {}).get(ARM_NEW) or {}).get('observedPolicy')
                    or {}).get('fields') or {}
    for key in GUARD_POLICY_KEYS:
        legacy_value, new_value = legacy_observed.get(key), new_observed.get(key)
        if legacy_value is not None and new_value is not None and legacy_value != new_value:
            errors.append(f'arms differ on observed policy {key}: legacy {legacy_value!r} '
                          f'vs new {new_value!r}; the arms would not search the same allowed space')
    # The declared scope IS the actual policy the arms dispatch; it must be exactly what each arm''s
    # own default scenario reads, otherwise the plan reports a policy (e.g. a 30000 horizon) the
    # search never ran. The plan records where the frozen meta disagreed with the actual arm default;
    # this comparison rejects a mismatch rather than letting it drift.
    declared_scope = plan.get('scope') or {}
    for arm in ARMS:
        observed_fields = (((plan.get('arms') or {}).get(arm) or {}).get('observedPolicy')
                           or {}).get('fields') or {}
        for key in ('finishPolicy', 'tickLimit', 'holyHerbStock', 'defeatCount', 'inputs'):
            entry = declared_scope.get(key)
            declared = entry.get('value') if isinstance(entry, dict) else entry
            actual = observed_fields.get(key)
            if declared is not None and actual is not None and canonical(declared) != canonical(actual):
                errors.append(f'arm {arm} observed {key} {actual!r} disagrees with the declared scope '
                              f'policy {declared!r}; refusing to report a policy the search did not '
                              'dispatch')
    if plan.get('openItems'):
        errors.append('plan has open items: ' + '; '.join(plan['openItems']))
    holdout = plan.get('holdout') or {}
    if not holdout.get('freezeBothArmsBeforeHoldout'):
        errors.append('plan does not require both nominees frozen before any holdout')
    if not holdout.get('unionAcrossWholeLibrary'):
        errors.append('plan does not require holdout freshness against the whole library on both arm '
                      'copies (every candidate, not just the nominees)')
    if not holdout.get('globalNonce'):
        errors.append('plan has no reproducible global holdout nonce label')
    if not isinstance(plan.get('scope'), dict) or not plan.get('scope'):
        errors.append('plan has no exact scope record')
    errors.extend(holdout_ordering_errors(plan_steps(plan))[:1])
    if check_baseline:
        bound = (plan.get('baseline') or {}).get('path')
        if not bound or not Path(bound).is_file():
            errors.append(f'baseline snapshot missing: {bound}')
    return errors


def plan_steps(plan):
    """The exact ordered steps of a run: both arms search and freeze before any holdout.

    For every encounter and replicate the legacy search runs and its nominee is frozen, then the new
    search runs and its nominee is frozen, and only then are the common fresh holdout pairs issued
    and both nominees evaluated.
    """
    encounters = [row['id'] for row in plan.get('encounters') or [] if row.get('id') is not None]
    steps = []
    for replicate in range(1, int(plan.get('replicates') or 0) + 1):
        for encounter_id in encounters:
            for arm in ARMS:
                steps.append(dict(op='search', arm=arm, encounterId=int(encounter_id),
                                  replicate=replicate))
                steps.append(dict(op='freeze', arm=arm, encounterId=int(encounter_id),
                                  replicate=replicate))
            for arm in ARMS:
                steps.append(dict(op='holdout', arm=arm, encounterId=int(encounter_id),
                                  replicate=replicate))
    return steps


def holdout_ordering_errors(steps):
    """Every holdout step must follow both arms' nominee freezes in the same comparison."""
    errors = []
    for index, step in enumerate(steps):
        if step.get('op') != 'holdout':
            continue
        frozen = {row['arm'] for row in steps[:index]
                  if row.get('op') == 'freeze' and row.get('encounterId') == step.get('encounterId')
                  and row.get('replicate') == step.get('replicate')}
        if frozen != set(ARMS):
            errors.append(f'holdout for {step.get("arm")} e{step.get("encounterId")} '
                          f'r{step.get("replicate")} precedes a nominee freeze')
    return errors


def _declared_finish_policy(plan):
    """The Finish policy the plan declares in the shared scope record, or None."""
    finish = (plan.get('scope') or {}).get('finishPolicy')
    return finish.get('value') if isinstance(finish, dict) else finish


def arm_policy(plan, arm=None, *, source_root=None, probe=None):
    """The exact policy one arm's rows are scored with, sourced per arm.

    The declared scope policy (tickLimit, finishPolicy, stock, horizon, inputs) comes from the plan;
    the Finish policy is resolved from THAT arm's own tree (its recorded plan probe, a live per-arm
    subprocess probe, or the arm's persisted search report), never from one shared parent-process
    import applied to both arms. A resolved Finish policy that disagrees with the declared scope is
    rejected rather than silently rescoring either arm.
    """
    policy = dict(plan.get('policy') or {})
    declared = _declared_finish_policy(plan)
    resolved, source = None, 'unresolved'
    recorded = (((plan.get('arms') or {}).get(arm) or {}).get('policy') if arm else None)
    if isinstance(recorded, dict) and recorded.get('finishPolicy') is not None:
        resolved, source = recorded['finishPolicy'], f'plan:arms.{arm}.policy'
    if resolved is None and isinstance(probe, dict) and probe.get('finishPolicy') is not None:
        resolved, source = probe['finishPolicy'], f'arm-probe:{source_root}'
    if resolved is None:
        resolved, source = declared, 'declared-scope'
    if resolved is not None and declared is not None and resolved != declared:
        raise Blocked(f'arm {arm} Finish policy {resolved!r} disagrees with the declared scope '
                      f'policy {declared!r}; refusing to score a mismatched run')
    policy['finishPolicy'] = resolved
    policy['finishPolicySource'] = source
    policy['declaredFinishPolicy'] = declared
    policy['arm'] = arm
    policy['scopeSource'] = ("frozen-copy-scope-record resolved to the arms' actual observed default "
                             'policy (the same record both arms read)')
    policy['resourcePolicyNote'] = ('stock/horizon/inputs are the ACTUAL policy both arms dispatch '
                                    '(their observed adapter default), reconciled against the frozen '
                                    'meta scope in the plan; any meta/arm disagreement is recorded '
                                    'as plan.scopeConflicts and is never silently claimed')
    return policy


# --------------------------------------------------------------------------------------------------
# arm drivers (real runtimes; nothing is simulated or invented)
# --------------------------------------------------------------------------------------------------
def enter_source(source_root):
    source_root = str(source_root)
    while source_root in sys.path:
        sys.path.remove(source_root)
    sys.path.insert(0, source_root)


def summarise_backend(rows):
    native = fallback = unknown = 0
    for row in rows:
        backend = (row.get('raw') or {}).get('resultBackend')
        if backend == 'native':
            native += 1
        elif backend:
            fallback += 1
        else:
            unknown += 1
    return dict(native=native, fallback=fallback, unknown=unknown)


def _purposes_for(per_encounter):
    return dict(improvement=int(per_encounter), boundary=0, support=0, comparison=0, exploration=0)


def arm_smoke(plan, arm, source_root, out_dir, meter, workers):
    """Run the fixed all-20 native smoke in a child process and write raw results only.

    The parent applies the canonical contract, so a legacy child imports no current combat module.
    The meter charges every fixed smoke pair before it is simulated.
    """
    enter_source(source_root)
    import strategy_optimizer as optimizer
    import strategy_optimizer_adapter as adapter

    base = adapter.default_scenario()
    scenarios = optimizer.baseline_scenarios(base)
    pairs = parse_seed_pairs(plan['smoke']['fixedPairs'])
    rows = []
    try:
        for scenario, label in scenarios:
            encounter_id = int(scenario['encounterId'])
            for pair in pairs:
                meter.admit(1)
                started = time.perf_counter()
                raw = adapter.simulate(scenario, list(pair), backend='native')
                rows.append(dict(arm=arm, encounterId=encounter_id, label=label, seeds=list(pair),
                                 raw=raw, wallSeconds=round(time.perf_counter() - started, 4)))
    finally:
        _write_meter_snapshot(out_dir, 'smoke', arm, meter)
    assert_native_backend(rows, stage=f'smoke:{arm}')
    payload = dict(arm=arm, stage='smoke', sourceRoot=str(source_root), battles=len(rows),
                   admitted=meter.charged, rows=rows)
    write_json(Path(out_dir) / f'smoke-{arm}.json', payload)
    append_jsonl(Path(out_dir) / f'raw-smoke-{arm}.jsonl', rows)
    return payload


def _shutdown_search(engine, meter, completions, out_dir, stage_label, arm, report, uninstall,
                     *, finalise=None, pool_registry=None, cost=None):
    """Ordered search shutdown: stop/close/join -> final snapshot -> finalise -> uninstall.

    The bounded transport AND the pre-charge scenario guard stay INSTALLED for the whole sequence,
    including on the exception path: a cleanup flush or a late coordinator dispatch can never escape
    the meter/guard, and the baseline copy is never released while the worker thread is still alive.
    The FINAL meter/transport snapshots and the final status/nomination capture happen only AFTER the
    owned worker thread has finished, so a dispatch the coordinator accepts while it drains is
    charged and recorded instead of being lost to a snapshot taken too early. ``uninstall`` is last.

    If the owned worker thread is STILL ALIVE after the bounded join, the wrapper is deliberately NOT
    uninstalled: the live thread must never be able to send an unmetered dispatch. The exact owned
    worker processes are terminated instead so the thread drains, and the caller reports BLOCKED
    rather than proceeding to release a copy that is still in use. ``cost`` (a mutable holder) is
    given the observer-only owned-process CPU reading taken while the workers are still alive.
    Returns ``(cleanup_error, finalise_error)``; neither is raised here so the caller can preserve
    the ORIGINAL failure and still complete cleanup.
    """
    cleanup_error = None
    finalise_error = None
    if cost is not None:
        owned = []
        for pool in list(pool_registry or ()):
            owned.extend(list(getattr(pool, 'processes', ()) or ()))
        cost['processCpu'] = _owned_process_cpu_seconds(owned)
    for name in ('stop', 'close'):
        try:
            engine.command(name, {}, wait=True)
        except Exception as exc:  # noqa: BLE001
            report['errors'].append(f'{name}: {type(exc).__name__}: {exc}')
    thread = getattr(engine, 'thread', None)
    thread_alive = False
    if thread is not None:
        if thread.is_alive():
            thread.join(timeout=SHUTDOWN_JOIN_SECONDS)
        thread_alive = bool(thread.is_alive())
        if thread_alive:
            cleanup_error = ('the Optimizer worker thread did not exit after close; keeping the '
                             'transport guard installed, terminating the owned workers and refusing '
                             'to release its baseline copy')
    if cleanup_error is None and finalise is not None:
        try:
            finalise(engine.status())
        except BaseException as exc:  # noqa: BLE001 - preserved, never masked by cleanup
            finalise_error = exc
    try:
        _write_meter_snapshot(out_dir, stage_label, arm, meter)
    except OSError:
        pass
    transport_summary = completions.summary()
    report['transportBackends'] = transport_summary
    try:
        _write_transport_snapshot(out_dir, stage_label, arm, transport_summary)
    except OSError:
        pass
    if thread_alive:
        # The guard stays INSTALLED: never uninstall the wrapper while the owned thread could still
        # send unmetered work. Terminate ONLY the exact owned worker processes so it drains.
        terminated = 0
        for pool in list(pool_registry or ()):
            for process in list(getattr(pool, 'processes', ()) or ()):
                try:
                    _terminate_owned_process_tree(process)
                    terminated += 1
                except Exception:  # noqa: BLE001
                    pass
        report['guardRetained'] = True
        report['ownedTerminations'] = terminated
    else:
        uninstall()
        report['guardRetained'] = False
    return cleanup_error, finalise_error


def search_stage(plan, arm, source_root, copy_path, out_dir, *, encounter_id, replicate, seconds,
                 workers=4, cap=None, migration_plan_id=None):
    """Run one arm's search for one encounter with a finite meter and freeze its nominee.

    The meter caps the real transport at exactly the declared per-encounter budget, so both arms do
    equal work; the legacy totalRuns counter is never used to decide when to stop. The new arm's
    count is its own exact transport meter plus the coordinator ea_* ledger, never totalRuns.
    """
    stage_started, stage_cpu = time.perf_counter(), time.process_time()
    enter_source(source_root)
    import strategy_optimizer as optimizer
    import strategy_optimizer_fast as fast
    import strategy_dispatch as dispatch
    import strategy_optimizer_adapter as adapter
    import strategy_students as students

    # Apply the identical copy-only ceiling before the Optimizer (and therefore its Store) exists.
    apply_copy_storage_limit(plan, optimizer)
    budget = plan['budget']['stages']['search']
    target = int(cap if cap is not None else budget['battlesPerArmPerEncounterPerReplicate'])
    workers = max(1, int(workers or 4))
    # Validate this arm's own ACTUAL running implementation/native revision before any battle, and
    # record it: a Coordinator/native/observer change after the plan fails closed here, in the arm.
    live_implementation = _implementation_inventory_record(
        implementation_inventory_files(source_root),
        canonical_files=(adapter.provenance().get('files') or {}), observer=observer_inventory())
    implementation_problems = validate_implementation_inventory(
        (((plan.get('arms') or {}).get(arm) or {}).get('implementationInventory') or {}),
        live_implementation, arm=arm)
    if implementation_problems:
        raise Blocked('arm implementation changed after the plan: '
                      + '; '.join(implementation_problems))
    guard = build_scenario_guard(plan, arm, encounter_id, adapter=adapter, students=students,
                                 role=f'search:{arm}')
    pause_queue = []
    meter = ExecutionMeter(target, stage=f'search:{arm}:{encounter_id}',
                           on_last=lambda: pause_queue.append('pause'))
    completions = TransportCompletions()
    pool_registry = []
    uninstall = install_bounded_dispatch(meter, pool_module=fast, dispatch_module=dispatch,
                                         optimizer_module=optimizer, scenario_validator=guard,
                                         completions=completions, pool_registry=pool_registry)
    engine = optimizer.Optimizer(Path(copy_path))
    prepare_wall = time.perf_counter() - stage_started
    prepare_cpu = time.process_time() - stage_cpu
    policy = arm_policy(plan, arm)
    report = dict(arm=arm, stage='search', encounterId=int(encounter_id), replicate=replicate,
                  sourceRoot=str(source_root), copy=str(copy_path), declared=target,
                  admitted=None, denied=None, observedRuns=None, incomplete=False, encounters=[],
                  errors=[], schedulerStages={}, incumbentTimeline=None, nomination=None,
                  usedPairs=[], status=None, finishPolicy=policy.get('finishPolicy'),
                  armPolicy=policy, loadedRevision=None, failed=False, eaLedger=None,
                  countSource='execution-meter', implementationRevision=live_implementation['digest'],
                  observerRevision=live_implementation.get('observerRevision'), scenarioGuard=None)
    deadline = time.monotonic() + seconds
    failure = None
    dispatch_started, dispatch_cpu0 = time.perf_counter(), time.process_time()
    try:
        _wait_open(engine, deadline)
        _run_encounter(engine, arm, encounter_id, meter, workers, report, deadline,
                       pause_queue, migration_plan_id=migration_plan_id)
    except BaseException as exc:  # noqa: BLE001 - cleanup must still run, then re-raise
        failure = exc
    finally:
        dispatch_wall = time.perf_counter() - dispatch_started
        dispatch_cpu = time.process_time() - dispatch_cpu0
        shutdown_started, shutdown_cpu0 = time.perf_counter(), time.process_time()
        report['scenarioGuard'] = dict(role=f'search:{arm}', frozenPolicyHash=guard.frozenHash,
                                       actualPolicyHashes=sorted(guard.hashes),
                                       policyFields=guard.policyFields,
                                       expectedEncounter=guard.expectedEncounter)

        def _finalise(status):
            # Read the settled status and freeze the nominee only AFTER the owned thread has
            # stopped, so the coordinator's final analysis/portfolio is what is scored.
            report['status'] = _status_view(status)
            report['schedulerStages'] = status.get('scheduler') or {}
            report['incumbentTimeline'] = _incumbent_timeline(status, arm)
            report['loadedRevision'] = _loaded_revision_from_status(status)
            report['nomination'] = freeze_nomination(status, arm, out_dir, plan, encounter_id,
                                                     replicate, copy_path=copy_path,
                                                     students=students)

        cost_holder = {}
        cleanup_error, finalise_error = _shutdown_search(
            engine, meter, completions, out_dir, f'search-e{encounter_id}', arm, report, uninstall,
            finalise=(_finalise if failure is None else None), pool_registry=pool_registry,
            cost=cost_holder)
        if finalise_error is not None:
            if failure is None:
                failure = finalise_error
            else:
                report['errors'].append(
                    f'finalise: {type(finalise_error).__name__}: {finalise_error}')
        if cleanup_error is not None:
            if failure is None:
                failure = Blocked(cleanup_error)
            else:
                report['errors'].append(cleanup_error)
        report['observerCost'] = _observer_cost(
            wall_seconds=time.perf_counter() - stage_started,
            parent_cpu_seconds=time.process_time() - stage_cpu, raw_rows=None,
            process_cpu=cost_holder.get('processCpu'),
            stages=dict(
                preparation=dict(wall=round(prepare_wall, 4), parentCpu=round(prepare_cpu, 4),
                                 source='engine/guard/meter setup (one-time per unit)'),
                dispatch=dict(wall=round(max(0.0, dispatch_wall), 4),
                              parentCpu=round(max(0.0, dispatch_cpu), 4),
                              source='run encounter through the bounded transport meter'),
                shutdown=dict(wall=round(max(0.0, time.perf_counter() - shutdown_started), 4),
                              parentCpu=round(max(0.0, time.process_time() - shutdown_cpu0), 4),
                              source='stop/close/join + finalise')),
            runtime_counters=_runtime_phase_counters(report.get('status')))
    if failure is not None:
        raise failure
    report['admitted'] = meter.charged
    report['denied'] = meter.denied
    report['usedPairs'] = meter.usedPairs
    report['eaLedger'] = _ea_ledger_counts(copy_path)
    report['failed'] = any(row.get('failed') for row in report['encounters'])
    report['incomplete'] = (any(row.get('incomplete') for row in report['encounters'])
                            or report['failed'] or meter.charged < target)
    if meter.charged > target:
        raise Blocked(f'search admitted {meter.charged} above its exact cap {target}')
    write_json(Path(out_dir) / f'search-{arm}-e{encounter_id}-r{replicate}.json', report)
    # The transport ran real dispatches; every one of them must have completed on the native backend.
    # A python-fallback or unlabelled completion fails the search closed BEFORE any comparison.
    assert_native_completions(report['transportBackends'], stage=f'search:{arm}:e{encounter_id}')
    return report


def _wait_open(engine, deadline, timeout=1800):
    limit = min(deadline, time.monotonic() + timeout)
    while time.monotonic() < limit:
        if engine.status().get('state') != 'Opening library':
            return True
        time.sleep(0.25)
    raise Blocked('library copy did not finish opening')


def _pending_count(status):
    """In-flight native tasks: the top-level field if a mock publishes it, else the scheduler view.

    The live runtime publishes the count inside ``status['scheduler']['pending']`` and has no
    top-level ``pending`` key, so reading only the top level would treat a busy pool as idle.
    """
    if status.get('pending') is not None:
        return status.get('pending')
    return (status.get('scheduler') or {}).get('pending')


def _coordinator_stall(status, *, meter_remaining):
    """An EXPLICIT coordinator no-useful-work signal, or None.

    A Running search with an unspendable finite budget is only ever reported idle when the coordinator
    itself says so. During the new arm's initial shared analysis / fresh global-history read the
    published progress is ``idle=false`` with ``current=null`` and no pending work for 20 s+, so an
    absence of transport progress alone is NOT a stall and must never stop the run. The signals read
    here are the runtime's own: the encounter-aware ``progress.idle`` flag, an ``unspendable`` purpose
    or ``unspendableTasks`` entry that covers the remaining budget, or the coordinator's published
    ``scheduler.idleReason`` ("Running, free workers, nothing dispatchable").
    """
    if meter_remaining <= 0:
        return None
    progress = (status.get('encounterAware') or {}).get('progress')
    if isinstance(progress, dict):
        if progress.get('idle') is True:
            return 'the coordinator reports the focused search idle with an unspendable budget'
        unspendable = progress.get('unspendable') or []
        tasks = progress.get('unspendableTasks') or []
        if unspendable and (tasks or progress.get('queueLength') in (0, None)) \
                and progress.get('current') is None:
            return ('the coordinator marks every remaining purpose unspendable '
                    f'({sorted(str(item) for item in unspendable)}) with no current experiment')
    reason = (status.get('scheduler') or {}).get('idleReason')
    if reason:
        return f'the coordinator reports no dispatchable work: {reason}'
    return None


def _drain(engine, deadline):
    """Let already-admitted results finish and be harvested; admitted work is never dropped."""
    while time.monotonic() < deadline:
        try:
            status = engine.status()
        except Exception:  # noqa: BLE001
            return False
        if status.get('state') not in ('Running', 'Saving', 'Paused'):
            return True
        if _pending_count(status) in (0, None):
            return True
        time.sleep(0.5)
    return False


def _status_view(status):
    aware = status.get('encounterAware') or {}
    return dict(state=status.get('state'), error=status.get('error'),
                totalRuns=status.get('totalRuns'), scheduler=status.get('scheduler'),
                focusPortfolio=status.get('focusPortfolio'), rankOne=status.get('rankOne'),
                focusExploit=status.get('focusExploit'), focusRecord=status.get('focusRecord'),
                encounterAware=aware)


def _preview_encounter(engine, draft):
    """Preview the exact draft and return the runtime's echoed plan, or refuse.

    The backend now validates activation against an exact preview, so the harness must ask for one
    first and then activate the previewed config. If the runtime does not publish a preview (an older
    bridge, or a refused preview) this fails closed rather than pretending a plan was reviewed.
    """
    engine.command('encounter_preview', draft, wait=True)
    state = engine.status()
    if state.get('error'):
        raise Blocked('encounter preview failed: ' + str(state.get('error')))
    preview = (state.get('encounterAware') or {}).get('migrationPreview')
    if not isinstance(preview, dict):
        raise Blocked('the runtime published no migration preview for the requested draft')
    return preview


def _loaded_revision_from_status(status):
    """The revision the running arm actually loaded (its own status), or None. Evidence only."""
    status = status or {}
    aware = status.get('encounterAware') or {}
    for value in (aware.get('runtimeRevision'), status.get('runtimeRevision')):
        if value:
            return value
    return None


def _community_only_students(engine):
    """Pin the frozen legacy search to the SAME allowed space as community-first (Community 1).

    The raw legacy Optimizer constructor defaults to Community .3 / Rebel .25 / Stumble .2 /
    Discovery .25; the new arm activates community-first (Community 1, every other stream 0). Left
    alone the two arms would search different spaces. The registered stream names are read from the
    runtime's own published ``students.names`` (never hard-coded), the split is applied with the
    supported ``students`` command, and it is verified from the status before any battle. Only the
    student share is set: the legacy Community tracks and the default mature evidence are untouched,
    so the baseline is not handicapped.
    """
    names = ((engine.status().get('students') or {}).get('names') or {})
    registered = {str(key): str(value) for key, value in names.items()}
    community = registered.get('community')
    if not registered or not community:
        raise Blocked('the legacy runtime published no registered community stream name; refusing '
                      'to guess the community-only configuration')
    shares = {name: (1.0 if key == 'community' else 0.0) for key, name in registered.items()}
    engine.command('students', dict(shares=shares), wait=True)
    verified = dict(((engine.status().get('students') or {}).get('shares') or {}))
    if not verified:
        raise Blocked('the legacy runtime published no student shares after configuration')
    if float(verified.get(community, 0.0) or 0.0) < 0.999:
        raise Blocked(f'the legacy community share did not take: {verified.get(community)!r}')
    for name in registered.values():
        if name != community and float(verified.get(name, 0.0) or 0.0) > 1e-9:
            raise Blocked(f'legacy non-community stream {name!r} is still active '
                          f'({verified.get(name)!r}); the arms would not search the same space')
    return dict(registeredNames=registered, requested=shares, applied=verified, communityOnly=True)


def _run_encounter(engine, arm, encounter_id, meter, workers, report, deadline, pause_queue,
                   *, migration_plan_id=None, poll_interval=POLL_INTERVAL_SECONDS,
                   idle_polls=IDLE_POLL_LIMIT, idle_grace_seconds=IDLE_GRACE_SECONDS):
    """Drive one encounter until the finite meter is exhausted; count the meter, not totalRuns.

    No headroom and no slack: the transport itself is bounded, so the phase admits exactly its cap.
    The legacy totalRuns delta is recorded only as an independent cross-check.
    """
    if arm == ARM_NEW:
        reference = _reference_candidate(engine.path, encounter_id)
        purposes = _purposes_for(meter.cap)
        draft = dict(mode='community-first', encounter=int(encounter_id), reference=reference,
                     purposes=purposes)
        preview = _preview_encounter(engine, draft)
        config = preview.get('config')
        if not isinstance(config, dict):
            raise Blocked('the encounter preview did not echo an explicit config')
        migration = preview.get('simulatorMigration') or {}
        preview_plan_id = migration.get('planId') if migration.get('required') else None
        if migration.get('required') and not preview_plan_id:
            raise Blocked('the encounter preview requires a simulator migration but published no '
                          'plan id')
        if (migration_plan_id is not None and preview_plan_id is not None
                and str(migration_plan_id) != str(preview_plan_id)):
            raise Blocked(f'the preflight migration plan {migration_plan_id!r} does not match the '
                          f'runtime preview plan {preview_plan_id!r}')
        # Activate exactly what was previewed; keep the scope (encounter/reference) on the request
        # even when the preview plan itself does not echo it.
        activation = dict(mode='community-first', encounter=int(encounter_id), reference=reference,
                          purposes=purposes, config=config,
                          previousShares={}, previousTracks={}, previousMapping={})
        if preview_plan_id is not None:
            activation['migrationPlanId'] = preview_plan_id
        result = engine.command('encounter_activate', activation, wait=True) or {}
        state = engine.status()
        activation_error = result.get('error') if isinstance(result, dict) else None
        if activation_error or state.get('error'):
            # A refused activation is fatal even when the mode was already enabled from an earlier
            # activation; a stale 'enabled' flag must never hide the failure.
            raise Blocked('community-first activation failed: '
                          + str(activation_error or state.get('error')))
        if not (state.get('encounterAware') or {}).get('enabled'):
            raise Blocked('community-first activation did not enable the coordinator: '
                          + str(state.get('error') or 'no error published'))
    else:
        engine.command('focus_encounter', dict(encounterId=int(encounter_id)), wait=True)
        report['legacyStudents'] = _community_only_students(engine)
    baseline = int(engine.status().get('totalRuns') or 0)
    engine.command('start', dict(workers=workers, duty=1.0), wait=True)
    started = time.monotonic()
    # The coordinator's first shared analysis / fresh global-history read can take well over 20 s and
    # publishes no pending work while it runs, so an unspendable budget may only be declared idle
    # AFTER this bounded startup/analysis allowance and only on an EXPLICIT coordinator signal. The
    # absolute deadline above still bounds the run, so a genuinely wedged search cannot spin forever.
    idle_after = started + max(0.0, float(idle_grace_seconds))
    first_error = None
    incomplete = False
    failed = False
    incomplete_reason = None
    paused = False
    last_charge = meter.charged
    last_total = baseline
    idle_seen = 0
    while True:
        if time.monotonic() >= deadline:
            incomplete = True
            incomplete_reason = (f'real deadline reached with {meter.remaining} battles '
                                 'unadmitted')
            break
        if meter.remaining <= 0:
            break
        if pause_queue and not paused:
            try:
                engine.command('pause', {}, wait=True)
            except Exception as exc:  # noqa: BLE001
                report['errors'].append(f'pause: {type(exc).__name__}: {exc}')
            paused = True
        status = engine.status()
        if first_error is None and status.get('error'):
            first_error = str(status['error'])
            failed = True
            incomplete = True
            incomplete_reason = f'engine published an error: {first_error}'
            break
        if status.get('state') not in ('Running', 'Saving', 'Paused'):
            if meter.remaining > 0:
                incomplete = True
                incomplete_reason = (f'engine left {status.get("state")!r} with {meter.remaining} '
                                     'battles unadmitted (unresolved execution)')
            break
        total = int(status.get('totalRuns') or 0)
        pending = _pending_count(status)
        if meter.charged != last_charge or total != last_total:
            last_charge, last_total, idle_seen = meter.charged, total, 0
        else:
            idle_seen += 1
            stall = (_coordinator_stall(status, meter_remaining=meter.remaining)
                     if time.monotonic() >= idle_after else None)
            if (stall is not None and meter.remaining > 0 and pending in (0, None)
                    and idle_seen >= idle_polls):
                # Running with an unspendable budget, nothing outstanding and an explicit coordinator
                # signal: report INCOMPLETE instead of idling to the real deadline. No filler battle
                # is added and no selective extension is attempted.
                incomplete = True
                incomplete_reason = (f'idle budget: {stall}; no transport progress for {idle_polls} '
                                     f'polls with {meter.remaining} battles unspent')
                report['errors'].append(
                    f'idle budget: {stall}; no transport progress for {idle_polls} polls with '
                    f'{meter.remaining} battles unspent')
                break
        time.sleep(poll_interval)
    if not _drain(engine, deadline):
        incomplete = True
        incomplete_reason = incomplete_reason or 'admitted results did not finish draining'
    if meter.charged < meter.cap:
        incomplete = True
        incomplete_reason = incomplete_reason or (f'underexecution: admitted {meter.charged} '
                                                  f'of {meter.cap} declared battles')
    status = engine.status()
    observed = int(status.get('totalRuns') or 0) - baseline
    report['encounters'].append(dict(
        encounterId=int(encounter_id), declared=meter.cap, admitted=meter.charged,
        denied=meter.denied, observedRuns=observed, seconds=round(time.monotonic() - started, 2),
        incomplete=bool(incomplete), failed=bool(failed), partial=bool(incomplete),
        underexecuted=bool(meter.charged < meter.cap), reason=incomplete_reason,
        error=first_error, state=status.get('state'),
        scheduler=status.get('scheduler'),
        awareProgress=(status.get('encounterAware') or {}).get('progress')))
    return status


def _collect_measured(value, out):
    if isinstance(value, dict):
        if value.get('candidateId') and isinstance(value.get('meanEarned'), (int, float)):
            out.append(value)
        for item in value.values():
            _collect_measured(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_measured(item, out)
    return out


def freeze_nomination(status, arm, out_dir, plan, encounter_id, replicate, *, copy_path=None,
                      students=None):
    """Freeze the arm's final nominee from its own measured view, before any holdout battle.

    The measured view is first filtered to the ACTUAL stored candidates admitted to the declared
    Community relative to the encounter's own pinned supplied reference (template C7 / fodder C8),
    identically for both arms; only then is the best eligible meanEarned chosen (never a jackpot/best
    field). An arm with no eligible candidate freezes an explicit no-eligible record instead of a
    foreign-family fallback, so the holdout is never aborted by a nominee that was never admissible.

    Only the frozen legacy arm may fall back to its DURABLE validation aggregate (labelled
    'legacy-validation-aggregate') when the live ``focusPortfolio`` is empty; that fallback uses the
    same stored candidates, the same Community/policy scope filter and the same best-mean selection.
    The NEW arm uses ONLY its authoritative public portfolio (the live
    ``status.encounterAware.portfolio`` captured after the shutdown refresh); an empty authoritative
    view stays explicitly no-eligible and is never replaced by a durable aggregate over unrelated
    past windows/experiments/revisions.
    """
    raw = measured_view(status, arm)
    source = 'status-view'
    if not raw and copy_path is not None and arm == ARM_LEGACY:
        raw = stored_measured_snapshot(copy_path, encounter_id, arm=arm)
        source = 'stored-measurement'
    candidate_scope = dict(mode='unfiltered', reason='no stored-candidate scope was supplied')
    measured = raw
    if students is not None and copy_path is not None:
        reference = pinned_reference(plan, arm, encounter_id)
        ids = [row.get('candidateId') for row in raw]
        admitted = admitted_candidate_ids(copy_path, ids, encounter_id, reference['stored'], students)
        measured = _scope_measured(raw, admitted, encounter_id)
        candidate_scope = dict(mode='community-relative-to-frozen-reference',
                               encounterId=int(encounter_id),
                               referenceCandidateId=reference.get('candidateId'),
                               considered=len([cid for cid in ids if isinstance(cid, str) and cid]),
                               measuredSource=source,
                               admitted=len(admitted))
    else:
        candidate_scope = dict(candidate_scope, measuredSource=source)
    nominee, selection = select_nominee(measured)
    aware = status.get('encounterAware') or {}
    scope = plan.get('scope') or {}
    finish = scope.get('finishPolicy')
    finish_value = finish.get('value') if isinstance(finish, dict) else finish
    nomination = dict(arm=arm, replicate=replicate, encounterId=int(encounter_id), frozenAt=now(),
                      nominee=nominee, selection=selection,
                      candidateScope=candidate_scope,
                      reference=(aware.get('progress') or {}).get('reference'),
                      measured=measured, scope=scope, finishPolicy=finish_value)
    path = Path(out_dir) / f'nomination-{arm}-e{encounter_id}-r{replicate}.json'
    if path.exists():
        existing = read_json(path)
        if existing != nomination:
            raise Blocked(f'nomination {path.name} already exists with different content; refusing '
                          'to overwrite a frozen nominee')
        return existing
    write_json(path, nomination, immutable=True)
    return nomination


def _incumbent_timeline(status, arm):
    aware = status.get('encounterAware') or {}
    return dict(arm=arm, rankOne=status.get('rankOne'), focusPortfolio=status.get('focusPortfolio'),
                progress=aware.get('progress'), portfolio=aware.get('portfolio'),
                boundaries=aware.get('boundaries'),
                note='read from the live runtime report when available; None means not published')


def _reference_candidate(copy_path, encounter_id):
    """The encounter's own supplied community cast: identical in both baseline copies."""
    connection = sqlite3.connect(Path(copy_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    try:
        connection.execute('PRAGMA query_only=ON')
        row = connection.execute(
            'SELECT c.id FROM candidate c JOIN candidate_meta m ON m.id=c.id '
            "WHERE m.encounter=? AND c.source='supplied' ORDER BY c.created, c.id LIMIT 1",
            (int(encounter_id),)).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def holdout_stage(plan, arm, source_root, copy_path, out_dir, *, encounter_id, replicate, pairs,
                  nominee, seconds, workers=4):
    """Evaluate ONLY one arm's frozen nominee on the common pairs. Raw results only.

    The comparison is the new arm's nominee against the legacy arm's nominee on the identical fresh
    pairs; no common reference is re-simulated inside an arm, so every battle here is one real paired
    comparison observation. The parent applies the canonical contract, so a legacy child imports no
    current combat module. The meter charges every submitted pair before it is sent to the transport.
    """
    stage_started, stage_cpu0 = time.perf_counter(), time.process_time()
    enter_source(source_root)
    import strategy_optimizer_fast as fast
    import strategy_optimizer_adapter as adapter
    import strategy_students as students

    scenarios = _read_scenarios(copy_path, [nominee])
    if nominee not in scenarios:
        raise Blocked(f'holdout nominee missing from the arm copy: {nominee}')
    # The raw stored scenario may not carry the study policy: apply the SAME frozen observed policy
    # the search was guarded with, explicitly, before dispatch; the guard then verifies the actual
    # submitted scenario and the actual policy hash is recorded.
    guard = build_scenario_guard(plan, arm, encounter_id, adapter=adapter, students=students,
                                 role=f'holdout:{arm}')
    stored_scenario = scenarios[nominee]
    stored = apply_frozen_policy(stored_scenario, guard)
    prepare_wall = time.perf_counter() - stage_started
    prepare_cpu = time.process_time() - stage_cpu0
    target = len(pairs)
    meter = ExecutionMeter(target, stage=f'holdout:{arm}:{encounter_id}',
                           on_last=lambda: None)
    uninstall = install_bounded_dispatch(meter, pool_module=fast, scenario_validator=guard)
    rows = []
    incomplete = False
    deadline = time.monotonic() + seconds
    # The frozen legacy constructor takes only max_workers; the current one accepts telemetry=False.
    pool = (fast.HeadlessPool(workers) if arm == ARM_LEGACY
            else fast.HeadlessPool(workers, telemetry=False))
    dispatch_started, dispatch_cpu0 = time.perf_counter(), time.process_time()
    try:
        scenario = stored
        for pair in pairs:
            if time.monotonic() >= deadline:
                incomplete = True
                break
            started = time.perf_counter()
            raw = pool.submit(None, scenario, list(pair)).result()
            rows.append(dict(arm=arm, replicate=replicate, candidateId=nominee,
                             encounterId=int(encounter_id), seeds=list(pair), raw=raw,
                             wallSeconds=round(time.perf_counter() - started, 4)))
    finally:
        dispatch_wall = time.perf_counter() - dispatch_started
        dispatch_cpu = time.process_time() - dispatch_cpu0
        # Observer-only: read the owned workers' real process CPU BEFORE the pool shuts them down.
        process_cpu = _owned_process_cpu_seconds(getattr(pool, 'processes', ()))
        shutdown_started, shutdown_cpu0 = time.perf_counter(), time.process_time()
        try:
            pool.shutdown(wait=True)
        except Exception:  # noqa: BLE001
            pass
        uninstall()
        _write_meter_snapshot(out_dir, f'holdout-e{encounter_id}', arm, meter)
        shutdown_wall = time.perf_counter() - shutdown_started
        shutdown_cpu = time.process_time() - shutdown_cpu0
    assert_native_backend(rows, stage=f'holdout:{arm}:e{encounter_id}')
    if meter.charged > target:
        raise Blocked(f'holdout admitted {meter.charged} above its exact cap {target}')
    report = dict(arm=arm, stage='holdout', encounterId=int(encounter_id), replicate=replicate,
                  nominee=nominee, nomineeOnly=True, pairs=len(pairs), declared=target,
                  admitted=meter.charged, incomplete=incomplete, rawRows=len(rows),
                  scenarioTransform=dict(
                      source='stored-nominee-scenario', nominee=nominee,
                      storedScenarioDigest=digest(stored_scenario),
                      dispatchedScenarioDigest=digest(stored),
                      overlaidPolicyFields=sorted(guard.policyFields),
                      alignedPolicyFields=sorted(getattr(guard, 'alignmentFields',
                                                         guard.policyFields)),
                      droppedDependentFields=sorted(
                          key for key in ALIGN_POLICY_KEYS
                          if key in stored_scenario
                          and key not in (getattr(guard, 'alignmentFields', None)
                                          or guard.policyFields)),
                      policyHash=guard.frozenHash,
                      note='the stored nominee scenario is overlaid with the SAME frozen observed '
                           'policy the search was guarded with, so holdout is scored at the policy '
                           'the search actually scored; dependent fields the frozen policy does not '
                           'declare (a stale holyHerbMaxUses/trigger list) are DROPPED exactly as '
                           'the arm runtime normalizes them; canonical nominee identity is preserved '
                           '(candidateId=nominee)'),
                  scenarioGuard=dict(role=f'holdout:{arm}', frozenPolicyHash=guard.frozenHash,
                                     actualPolicyHashes=sorted(guard.hashes),
                                     policyFields=guard.policyFields,
                                     alignmentFields=getattr(guard, 'alignmentFields',
                                                             guard.policyFields),
                                     expectedEncounter=guard.expectedEncounter))
    report['observerCost'] = _observer_cost(
        wall_seconds=time.perf_counter() - stage_started,
        parent_cpu_seconds=time.process_time() - stage_cpu0, raw_rows=rows, process_cpu=process_cpu,
        stages=dict(
            preparation=dict(wall=round(prepare_wall, 4), parentCpu=round(prepare_cpu, 4),
                             source='nominee scenario read + frozen policy overlay'),
            dispatch=dict(wall=round(max(0.0, dispatch_wall), 4),
                          parentCpu=round(max(0.0, dispatch_cpu), 4),
                          source='canonical per-pair dispatch on the arm pool'),
            shutdown=dict(wall=round(max(0.0, shutdown_wall), 4),
                          parentCpu=round(max(0.0, shutdown_cpu), 4),
                          source='pool shutdown + meter snapshot')),
        runtime_counters=None)
    write_json(Path(out_dir) / f'holdout-{arm}-e{encounter_id}-r{replicate}.json', report)
    append_jsonl(Path(out_dir) / f'raw-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl', rows)
    report['rows'] = rows
    return report


def evaluate_holdout(plan, arm, out_dir, encounter_id, replicate, pairs, nominee, raw_rows):
    """Apply the canonical terminal earned contract to ONE arm's raw nominee rows.

    This records only the arm's own nominee outcomes; the paired comparison against the other arm's
    nominee happens once, in ``cross_arm_summary``, so a reference is never claimed equivalent here.
    """
    assert_native_backend(raw_rows, stage=f'holdout-eval:{arm}:e{encounter_id}')
    policy = arm_policy(plan, arm)
    applied, counts = apply_canonical(raw_rows, policy)
    cells = earned_cells(applied)
    unresolved = sum(1 for pair in pairs if cells.get((nominee, tuple(pair))) is None)
    result = dict(arm=arm, stage='holdout', replicate=replicate, encounterId=int(encounter_id),
                  nominee=nominee, nomineeOnly=True, pairs=len(pairs), unresolvedPairs=unresolved,
                  counts=counts, finishPolicy=policy.get('finishPolicy'), rows=applied)
    append_jsonl(Path(out_dir) / f'outcome-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl', applied)
    return result


def _read_scenarios(copy_path, ids):
    connection = sqlite3.connect(Path(copy_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    try:
        connection.execute('PRAGMA query_only=ON')
        out = {}
        for candidate_id in ids:
            row = connection.execute('SELECT scenario FROM candidate WHERE id=?',
                                     (candidate_id,)).fetchone()
            if row:
                out[candidate_id] = json.loads(row[0])
    finally:
        connection.close()
    return out


# --------------------------------------------------------------------------------------------------
# throughput: matched infrastructure measurement (never an algorithm comparison)
# --------------------------------------------------------------------------------------------------
def _worker_cpu_reading(rows):
    """Actual worker ``cpuSeconds`` only: any missing reading makes the total null, never elapsed."""
    values, missing = [], 0
    for row in rows:
        value = (row.get('raw') or {}).get('cpuSeconds')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
        else:
            missing += 1
    return dict(seconds=(None if (missing or not values) else sum(values)),
                readings=len(values), missing=missing)


def _owned_process_cpu_seconds(processes):
    """Actual Windows process CPU for the owned worker processes, or None when unavailable.

    Uses ``GetProcessTimes`` on the pool's own Popen handles before shutdown (no psutil, no install).
    Kernel+user time is real process CPU and is reported separately from any worker-published
    ``cpuSeconds``; a missing handle contributes nothing rather than a fabricated zero.
    """
    if os.name != 'nt' or not processes:
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    total, read = 0.0, 0
    for process in processes:
        pid = getattr(process, 'pid', None)
        if not pid:
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            continue
        try:
            creation, exit_time = wintypes.FILETIME(), wintypes.FILETIME()
            kernel, user = wintypes.FILETIME(), wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                          ctypes.byref(kernel), ctypes.byref(user))
            if ok:
                def _to_seconds(part):
                    return ((part.dwHighDateTime << 32) | part.dwLowDateTime) / 1e7
                total += _to_seconds(kernel) + _to_seconds(user)
                read += 1
        finally:
            kernel32.CloseHandle(handle)
    if not read:
        return None
    return dict(seconds=round(total, 4), readings=read, processes=len(processes))


#: The runtime scheduler phase counters retained verbatim when the status actually publishes them.
RUNTIME_PHASE_COUNTERS = ('proposalSeconds', 'publishSeconds', 'recordSeconds', 'flushSeconds',
                          'evidenceSeconds', 'planSeconds', 'reseedSeconds', 'coordinatorBusySeconds',
                          'idleWorkerSeconds', 'idleCoordinatorSeconds', 'idleNoWorkSeconds')

OBSERVER_COST_SCHEMA = 'encounter-redesign-observer-cost-1'


def _runtime_phase_counters(status):
    """The runtime scheduler's own phase seconds verbatim, or None when nothing was published.

    Only keys the runtime actually published are returned; an unavailable counter is absent (never a
    fabricated 0). A published 0.0 is an actual reading and is retained as such.
    """
    scheduler = (status or {}).get('scheduler')
    if not isinstance(scheduler, dict):
        return None
    return {name: scheduler.get(name) for name in RUNTIME_PHASE_COUNTERS if name in scheduler}


def _observer_cost(*, wall_seconds, parent_cpu_seconds, raw_rows=None, process_cpu=None, stages=None,
                   runtime_counters=None):
    """Observer-only unit cost block: whole-unit wall + parent CPU, worker CPU when actually known.

    Pure instrumentation. These numbers are NEVER a learning feature and are never fed to selection,
    the throughput gate or any comparative claim. Worker CPU comes from the raw rows' own
    ``cpuSeconds`` when published, otherwise from an owned-process ``GetProcessTimes`` reading taken
    before the workers die; when neither is available the value stays None and the missing count is
    recorded explicitly - a missing reading is never substituted with zero. Only actually-measured
    phases are included under ``stages``/``runtimeCounters``.
    """
    reading = _worker_cpu_reading(raw_rows or [])
    seconds, source = reading['seconds'], 'raw-cpuSeconds'
    if seconds is None and process_cpu is not None:
        seconds, source = process_cpu['seconds'], 'owned-process-GetProcessTimes'
    if seconds is None:
        source = 'unavailable'
    return dict(
        schema=OBSERVER_COST_SCHEMA,
        scope='OBSERVER-ONLY whole-unit wall + parent CPU; never a learning feature, never used for '
              'selection, gating or any comparative claim',
        wallSeconds=round(float(wall_seconds), 4),
        parentCpuSeconds=round(float(parent_cpu_seconds), 4),
        workerCpuSeconds=(None if seconds is None else round(float(seconds), 4)),
        workerCpuSource=source,
        workerCpuReadings=reading['readings'], workerCpuMissing=reading['missing'],
        workerProcessCpu=process_cpu,
        workerCpuNote='each missing worker CPU reading is counted, never replaced with zero; the '
                      'legacy-throughput worker CPU stays unknown',
        stages=dict(stages or {}),
        runtimeCounters=(None if runtime_counters is None else dict(runtime_counters)))


def _workload_candidate(copy_path, encounter_id):
    """The canonical raw build and next validation ordinal both fixed workloads share.

    Read from the arm's own copy of the same frozen baseline, so the legacy and current arms see the
    identical stored candidate, scenario and ordinal base without a second page-local mapping.
    """
    connection = sqlite3.connect(Path(copy_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    try:
        connection.execute('PRAGMA query_only=ON')
        row = connection.execute(
            'SELECT c.id, c.scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id '
            'WHERE m.encounter=? ORDER BY c.created, c.id LIMIT 1', (int(encounter_id),)).fetchone()
        if row is None:
            raise Blocked(f'no candidate for encounter {encounter_id} in {copy_path}')
        candidate_id, scenario = row[0], json.loads(row[1])
        base = connection.execute(
            "SELECT MAX(ordinal) FROM run WHERE candidate=? AND phase='validation'",
            (candidate_id,)).fetchone()[0]
    finally:
        connection.close()
    return dict(candidateId=candidate_id, scenario=scenario,
                nextOrdinal=0 if base is None else int(base) + 1, encounterId=int(encounter_id))


def _workload_pairs(seed_pair, base, count):
    """The actual legacy ``seed_pair('validation', ordinal)`` sequence both workloads dispatch."""
    return [tuple(int(value) for value in seed_pair('validation', int(base) + index))
            for index in range(int(count))]


def _close_store_safely(store):
    """Close a Store, guaranteeing its SQLite handle is released even when its flush fails.

    ``Store.close`` flushes first; a failed flush (for example a copy that reached a size ceiling)
    raises before ``self.db.close()`` runs, leaking the connection and later making the copy's
    ownership-scoped unlink fail with PermissionError [WinError 32]. Close the handle in that case and
    re-raise the ORIGINAL exception so a cleanup error never masks the real failure.
    """
    if store is None:
        return
    try:
        store.close()
    except BaseException:
        db = getattr(store, 'db', None)
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001 - never mask the original failure with a close error
                pass
        raise


def _matched_dispatch(plan, arm, source_root, copy_path, out_dir, meter, workers, battles, *,
                      encounter_id, mode, runtime=None):
    """One finite fixed workload persisted through the arm's own real path.

    ``legacy-dispatch`` dispatches the frozen legacy ``HeadlessPool`` and records every accepted
    result through the frozen legacy ``Store.record`` in ordinal order; ``new-dispatch`` runs the
    current ``Evaluator`` so the ``ea_*`` ledger is the durable store. Both use the same canonical
    build, the same ``seed_pair('validation', ordinal)`` sequence, the same count and the same worker
    concurrency. Nothing here mutates a runtime module or fabricates a future.
    """
    persistence = 'store' if mode == 'legacy-dispatch' else 'ledger'
    if runtime is None:
        enter_source(source_root)
        import strategy_optimizer as optimizer
        import strategy_optimizer_adapter as adapter
        import strategy_optimizer_fast as fast
        runtime = dict(optimizer=optimizer, adapter=adapter, fast=fast)
        if persistence == 'ledger':
            import strategy_experiment_store as ledger
            from strategy_encounter_evaluation import Evaluator
            runtime.update(ledger=ledger, evaluator=Evaluator)
    optimizer, adapter, fast = runtime['optimizer'], runtime['adapter'], runtime['fast']
    # Apply the copy-only ceiling identically for both arms, before any Store is constructed.
    apply_copy_storage_limit(plan, optimizer)
    battles = int(battles)
    workers = max(1, int(workers or 4))

    stages = {name: None for name in ('preparation', 'dispatch', 'persistence', 'analysis')}
    parent_cpu0 = time.process_time()
    started = time.perf_counter()
    process_cpu = None
    counts = {}
    rows = []
    pool = None
    store = None
    connection = None
    try:
        prep_wall, prep_cpu = time.perf_counter(), time.process_time()
        workload = _workload_candidate(copy_path, encounter_id)
        pairs = _workload_pairs(optimizer.seed_pair, workload['nextOrdinal'], battles)
        if len({tuple(pair) for pair in pairs}) != len(pairs):
            raise Blocked('the fixed workload derived a repeated seed pair')
        scenario, candidate_id = workload['scenario'], workload['candidateId']
        if persistence == 'ledger':
            connection = sqlite3.connect(str(copy_path), timeout=20)
            runtime['ledger'].initialize(connection)
            experiment_id = runtime['ledger'].create_experiment(connection, dict(
                scope=f'throughput-workload:{int(encounter_id)}', planned_budget=battles,
                stopping=dict(rule='fixed-workload', battles=battles), referenceId=candidate_id,
                purpose='comparison', owner='benchmark-throughput'))
        stages['preparation'] = dict(wall=round(time.perf_counter() - prep_wall, 4),
                                     parentCpu=round(time.process_time() - prep_cpu, 4),
                                     source='per-build preparation (reported apart from per battle)')

        loop_wall = time.perf_counter()
        loop_cpu = time.process_time()
        persistence_seconds = 0.0
        persistence_cpu = 0.0
        if persistence == 'store':
            store = optimizer.Store(Path(copy_path), adapter.provenance())
            pool = fast.HeadlessPool(workers)
            outstanding = deque()
            try:
                for index, pair in enumerate(pairs):
                    while len(outstanding) >= workers:
                        ordinal_done, done_pair, future = outstanding.popleft()
                        result = future.result()
                        began_wall, began_cpu = time.perf_counter(), time.process_time()
                        store.record(candidate_id, 'validation', ordinal_done, result)
                        persistence_seconds += time.perf_counter() - began_wall
                        persistence_cpu += time.process_time() - began_cpu
                        rows.append(dict(arm=arm, mode=mode, candidateId=candidate_id,
                                         encounterId=int(encounter_id), seeds=list(done_pair),
                                         raw=result))
                    meter.admit(1)
                    outstanding.append((workload['nextOrdinal'] + index, pair,
                                        pool.submit(None, scenario, list(pair))))
                while outstanding:
                    ordinal_done, done_pair, future = outstanding.popleft()
                    result = future.result()
                    began_wall, began_cpu = time.perf_counter(), time.process_time()
                    store.record(candidate_id, 'validation', ordinal_done, result)
                    persistence_seconds += time.perf_counter() - began_wall
                    persistence_cpu += time.process_time() - began_cpu
                    rows.append(dict(arm=arm, mode=mode, candidateId=candidate_id,
                                     encounterId=int(encounter_id), seeds=list(done_pair),
                                     raw=result))
                began_wall, began_cpu = time.perf_counter(), time.process_time()
                store.flush()
                persistence_seconds += time.perf_counter() - began_wall
                persistence_cpu += time.process_time() - began_cpu
            finally:
                process_cpu = _owned_process_cpu_seconds(getattr(pool, 'processes', ()))
                # Close the connection even if the flush inside close() fails; the pool is already
                # drained, so the copy can be released without a leaked handle.
                try:
                    pool.shutdown(wait=True)
                finally:
                    _close_store_safely(store)
                store = None
        else:
            ledger = runtime['ledger']
            pool = fast.HeadlessPool(workers, telemetry=False)
            evaluator = runtime['evaluator'](workers=workers, telemetry=False, pool=pool)
            try:
                submitted = 0
                while submitted < len(pairs) or evaluator.pending:
                    if submitted < len(pairs):
                        pair = pairs[submitted]
                        if evaluator.submit(connection, experiment_id, candidate_id, scenario,
                                            list(pair)):
                            meter.admit(1)
                            submitted += 1
                    completed = evaluator.harvest(connection)
                    for entry in completed:
                        rows.append(dict(arm=arm, mode=mode, candidateId=entry['candidateId'],
                                         encounterId=int(encounter_id), seeds=list(entry['seeds']),
                                         raw=entry['result']))
                    if submitted < len(pairs) and not evaluator.pending and not completed:
                        raise Blocked('ea ledger denied the next fixed-workload pair before the '
                                      'declared count completed')
                    if not completed:
                        time.sleep(0.001)  # workers busy; never a hot spin while they finish
            finally:
                persistence_seconds = float(evaluator.metrics.get('persistenceSeconds') or 0.0)
                process_cpu = _owned_process_cpu_seconds(getattr(pool, 'processes', ()))
                evaluator.close()
                pool = None
            counts['eaLedger'] = _ea_ledger_counts(copy_path)
        loop_wall = time.perf_counter() - loop_wall
        stages['dispatch'] = dict(wall=round(max(0.0, loop_wall - persistence_seconds), 4),
                                  parentCpu=round(max(0.0, (time.process_time() - loop_cpu)
                                                       - persistence_cpu), 4))
        stages['persistence'] = dict(wall=round(persistence_seconds, 4),
                                     parentCpu=round(persistence_cpu, 4),
                                     source=('legacy Store.record' if persistence == 'store'
                                             else 'current Evaluator + ea ledger'))
        assert_native_backend(rows, stage=f'throughput:{mode}')
        analysis_wall, analysis_cpu = time.perf_counter(), time.process_time()
        _applied, counts['outcomes'] = apply_canonical(rows, arm_policy(plan, arm))
        append_jsonl(Path(out_dir) / f'raw-throughput-{arm}-{mode}.jsonl', rows)
        stages['analysis'] = dict(wall=round(time.perf_counter() - analysis_wall, 4),
                                  parentCpu=round(time.process_time() - analysis_cpu, 4),
                                  source='canonical outcomes')
        counts['battles'] = len(rows)
    finally:
        if connection is not None:
            connection.close()
        _write_meter_snapshot(out_dir, f'throughput-{mode}', arm, meter)
    if meter.charged > battles:
        raise Blocked(f'throughput workload admitted {meter.charged} above {battles}')
    cpu_reading = _worker_cpu_reading(rows)
    payload = dict(arm=arm, stage='throughput', mode=mode, battles=battles, admitted=meter.charged,
                   workerConcurrency=workers, candidateId=workload['candidateId'],
                   nextOrdinal=workload['nextOrdinal'], scenarioDigest=digest(workload['scenario']),
                   pairsDigest=digest([list(pair) for pair in pairs]), persistence=persistence,
                   wallSeconds=round(time.perf_counter() - started, 3),
                   parentCpuSeconds=round(time.process_time() - parent_cpu0, 3),
                   workerCpuSeconds=(None if cpu_reading['seconds'] is None
                                     else round(cpu_reading['seconds'], 4)),
                   workerCpuReadings=cpu_reading['readings'], workerCpuMissing=cpu_reading['missing'],
                   workerProcessCpuSeconds=process_cpu,
                   oneTimeBuildSeconds=stages['preparation']['wall'],
                   stages=stages, counts=counts)
    write_json(Path(out_dir) / f'throughput-{arm}-{mode}.json', payload)
    return payload


def _telemetry_throughput(plan, arm, source_root, out_dir, meter, workers, mode, battles, *,
                         runtime=None):
    """The current runtime pool on identical work with telemetry off/on, isolating the toggle.

    Both ``telemetry-off`` and ``telemetry-on`` run from the current source tree with the same fixed
    candidates, the same fixed pairs and the same count; the only difference is the pool's telemetry
    flag, so a difference is the instrumented path, not a different search.
    """
    if runtime is None:
        enter_source(source_root)
        import strategy_optimizer as optimizer
        import strategy_optimizer_adapter as adapter
        import strategy_optimizer_fast as fast
        runtime = dict(optimizer=optimizer, adapter=adapter, fast=fast)
    optimizer, adapter, fast = runtime['optimizer'], runtime['adapter'], runtime['fast']
    # Identical copy-only ceiling for both arms (this mode builds no Store, but keeps the policy
    # identical across every throughput mode so no mode can differ in its storage guard).
    apply_copy_storage_limit(plan, optimizer)
    battles = int(battles)
    workers = max(1, int(workers or 4))
    telemetry = mode == 'telemetry-on'
    stages = {name: None for name in ('preparation', 'dispatch', 'persistence', 'analysis')}
    parent_cpu0 = time.process_time()
    started = time.perf_counter()
    process_cpu = None
    counts = {}
    rows = []
    pool = None
    try:
        prep_wall, prep_cpu = time.perf_counter(), time.process_time()
        scenarios = [row[0] for row in optimizer.baseline_scenarios(adapter.default_scenario())]
        pairs = derive_pairs(plan['planId'], 'throughput-fixed', battles)
        pool = fast.HeadlessPool(workers, telemetry=telemetry)
        stages['preparation'] = dict(wall=round(time.perf_counter() - prep_wall, 4),
                                     parentCpu=round(time.process_time() - prep_cpu, 4),
                                     source='current runtime pool with telemetry flag')
        dispatch_wall, dispatch_cpu = time.perf_counter(), time.process_time()
        for index, pair in enumerate(pairs):
            scenario = scenarios[index % len(scenarios)]
            meter.admit(1)
            rows.append(dict(arm=arm, mode=mode, encounterId=int(scenario['encounterId']),
                             seeds=list(pair), raw=pool.submit(None, scenario, list(pair)).result()))
        stages['dispatch'] = dict(wall=round(time.perf_counter() - dispatch_wall, 4),
                                  parentCpu=round(time.process_time() - dispatch_cpu, 4))
        persist_wall, persist_cpu = time.perf_counter(), time.process_time()
        append_jsonl(Path(out_dir) / f'raw-throughput-{arm}-{mode}.jsonl', rows)
        stages['persistence'] = dict(wall=round(time.perf_counter() - persist_wall, 4),
                                     parentCpu=round(time.process_time() - persist_cpu, 4),
                                     source='jsonl append')
        process_cpu = _owned_process_cpu_seconds(getattr(pool, 'processes', ()))
        assert_native_backend(rows, stage=f'throughput:{mode}')
        analysis_wall, analysis_cpu = time.perf_counter(), time.process_time()
        _applied, counts['outcomes'] = apply_canonical(rows, arm_policy(plan, arm))
        stages['analysis'] = dict(wall=round(time.perf_counter() - analysis_wall, 4),
                                  parentCpu=round(time.process_time() - analysis_cpu, 4),
                                  source='canonical outcomes')
        counts['battles'] = len(rows)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
        _write_meter_snapshot(out_dir, f'throughput-{mode}', arm, meter)
    if meter.charged > battles:
        raise Blocked(f'throughput telemetry workload admitted {meter.charged} above {battles}')
    cpu_reading = _worker_cpu_reading(rows)
    payload = dict(arm=arm, stage='throughput', mode=mode, battles=battles, admitted=meter.charged,
                   workerConcurrency=workers, telemetry=telemetry, persistence='current-pool',
                   wallSeconds=round(time.perf_counter() - started, 3),
                   parentCpuSeconds=round(time.process_time() - parent_cpu0, 3),
                   workerCpuSeconds=(None if cpu_reading['seconds'] is None
                                     else round(cpu_reading['seconds'], 4)),
                   workerCpuReadings=cpu_reading['readings'], workerCpuMissing=cpu_reading['missing'],
                   workerProcessCpuSeconds=process_cpu, stages=stages, counts=counts)
    write_json(Path(out_dir) / f'throughput-{arm}-{mode}.json', payload)
    return payload


def throughput_stage(plan, arm, source_root, out_dir, meter, workers, mode, battles, *,
                     copy_path=None, runtime=None):
    """End-to-end infrastructure measurement, never an algorithm comparison.

    The two telemetry modes toggle the current runtime pool on identical work. The two matched modes
    run the same finite fixed workload through the frozen legacy HeadlessPool+Store.record path
    (``legacy-dispatch``) versus the current Evaluator+ea ledger (``new-dispatch``). The real
    end-to-end search timing for the algorithm comparison lives in the search stage.
    """
    if mode in TELEMETRY_MODES:
        return _telemetry_throughput(plan, arm, source_root, out_dir, meter, workers, mode, battles,
                                     runtime=runtime)
    if mode in MATCHED_MODES:
        if runtime is None and copy_path is None:
            raise Blocked(f'{mode} requires an arm copy of the frozen baseline')
        encounter_id = int((plan.get('encounters') or [{}])[0].get('id'))
        return _matched_dispatch(plan, arm, source_root, copy_path, out_dir, meter, workers,
                                 battles, encounter_id=encounter_id, mode=mode, runtime=runtime)
    raise HarnessError(f'unknown throughput mode {mode!r}')


def _throughput_arm(mode):
    """Which arm source runs a throughput mode: only legacy-dispatch is the frozen legacy tree."""
    return ARM_LEGACY if mode == 'legacy-dispatch' else ARM_NEW


def matched_infrastructure_comparison(units, *, threshold=0.10):
    """Compare the two matched throughput modes on identical work only, excluding one-time build.

    Returns ``compared=False`` (with a reason, never a fabricated ratio) unless both matched modes
    ran the same build, seed sequence, count and worker concurrency. The per-battle infrastructure
    ratio is dispatch+persistence+analysis; the one-time per-build preparation is reported apart so
    the <=10% check never absorbs an algorithm or compile difference.
    """
    modes = {}
    for unit in units:
        payload = unit.get('throughput') or {}
        if payload.get('mode') in MATCHED_MODES:
            modes[payload['mode']] = payload
    legacy, new = modes.get('legacy-dispatch'), modes.get('new-dispatch')
    if not legacy or not new:
        return dict(compared=False, reason='both matched throughput modes must run')
    matched = (legacy.get('pairsDigest') == new.get('pairsDigest')
               and legacy.get('scenarioDigest') == new.get('scenarioDigest')
               and legacy.get('battles') == new.get('battles')
               and legacy.get('workerConcurrency') == new.get('workerConcurrency'))
    if not matched:
        return dict(compared=False, reason='the two fixed workloads are not the same work; refusing '
                                           'a ratio that would compare different work')

    def infrastructure(entry):
        stages = entry.get('stages') or {}
        total = 0.0
        for name in ('dispatch', 'persistence', 'analysis'):
            stage = stages.get(name) or {}
            if stage.get('wall') is None:
                return None
            total += float(stage['wall'])
        return total

    legacy_infra, new_infra = infrastructure(legacy), infrastructure(new)
    if legacy_infra is None or new_infra is None or legacy_infra <= 0:
        return dict(compared=False, reason='a matched infrastructure stage is missing')
    overhead = (new_infra - legacy_infra) / legacy_infra
    return dict(compared=True, matched=True, battles=legacy['battles'], threshold=threshold,
                metric='wall', wallRatio=round(overhead, 4),
                legacyInfrastructureSeconds=round(legacy_infra, 4),
                newInfrastructureSeconds=round(new_infra, 4),
                relativeOverhead=round(overhead, 4), withinThreshold=(overhead <= threshold),
                # The <=10% check is an end-to-end WALL metric; actual CPU is reported separately and
                # is NEVER substituted for the wall number. Missing CPU stays None, never a zero.
                legacyParentCpuSeconds=legacy.get('parentCpuSeconds'),
                newParentCpuSeconds=new.get('parentCpuSeconds'),
                legacyWorkerCpuSeconds=legacy.get('workerCpuSeconds'),
                newWorkerCpuSeconds=new.get('workerCpuSeconds'),
                cpuOverhead=None,
                metricNote='wall-based end-to-end infrastructure ratio; parent CPU retained '
                           'separately and never used as the throughput metric',
                excluded='one-time per-build preparation is reported separately, never in the ratio')


# --------------------------------------------------------------------------------------------------
# preflight gates and orchestration
# --------------------------------------------------------------------------------------------------
def copy_filesystem_headroom(plan, out_root=None):
    """The declared copy-only limit plus the measured free space on the copy filesystem.

    ``ok`` is None when no filesystem was checked (e.g. a plan-only validation), True when the free
    space covers the declared bound (peak copies + WAL), and False otherwise - a fail-closed signal,
    never a silent pass.
    """
    storage = plan.get('storage') or {}
    required = int(storage.get('requiredFreeMiB') or COPY_REQUIRED_FREE_MIB)
    headroom = dict(copyMiB=int(storage.get('copyMiB') or COPY_STORAGE_MIB),
                    peakCopies=int(storage.get('peakCopies') or COPY_PEAK_COPIES),
                    walMiB=int(storage.get('walMiB') or COPY_WAL_MIB), requiredFreeMiB=required,
                    checked=None, freeMiB=None, ok=None)
    if out_root is not None:
        checked = str(Path(out_root).resolve())
        headroom['checked'] = checked
        try:
            free_mib = shutil.disk_usage(checked).free // (1024 * 1024)
            headroom.update(freeMiB=free_mib, ok=(free_mib >= required))
        except OSError as exc:  # noqa: BLE001 - an unreadable filesystem fails closed
            headroom.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    return headroom


def preflight(plan, *, python, timeout=900, out_root=None):
    """Gate checks. No battles. Reports every gap instead of working around it."""
    report = dict(gates={}, blocked=[], notes=[])
    baseline = plan['baseline']
    if not Path(baseline['path']).is_file():
        report['blocked'].append(f"baseline missing: {baseline['path']}")
        return report
    bound = read_baseline_binding(baseline['path'])
    for key, expected in (('storedProvenanceDigest', baseline.get('storedProvenanceDigest')),
                          ('candidateCount', baseline.get('candidateCount')),
                          ('lifetimeRuns', baseline.get('lifetimeRuns'))):
        actual = bound.get(key)
        report['gates'][key] = dict(expected=expected, actual=actual, ok=(actual == expected))
        if actual != expected:
            report['blocked'].append(f'baseline binding changed: {key} {actual} != {expected}')
    probes = {}
    for arm in ARMS:
        source = plan['arms'][arm]['source']
        probes[arm] = dict(provenance=probe_provenance(python, source, timeout=timeout),
                           interface=probe_interface(python, source, timeout=timeout),
                           implementation=probe_implementation_inventory(python, source,
                                                                          timeout=timeout))
    report['arms'] = probes
    source_inventory = {}
    for arm in ARMS:
        frozen = ((plan.get('arms') or {}).get(arm) or {}).get('sourceInventory') or {}
        live = probes[arm]['provenance'] or {}
        problems = validate_source_inventory(frozen, live, arm=arm)
        source_inventory[arm] = dict(frozenDigest=frozen.get('digest'), liveDigest=live.get('digest'),
                                     problems=problems)
        report['blocked'].extend(problems)
    report['gates']['sourceInventory'] = source_inventory
    observer = observer_inventory()
    implementation_inventory = {}
    for arm in ARMS:
        frozen = ((plan.get('arms') or {}).get(arm) or {}).get('implementationInventory') or {}
        live = _implementation_inventory_record(
            probes[arm]['implementation'],
            canonical_files=(probes[arm]['provenance'] or {}).get('files') or {}, observer=observer)
        problems = validate_implementation_inventory(frozen, live, arm=arm)
        implementation_inventory[arm] = dict(frozenDigest=frozen.get('digest'),
                                             liveDigest=live.get('digest'),
                                             observerRevision=live.get('observerRevision'),
                                             problems=problems)
        report['blocked'].extend(problems)
    report['gates']['implementationInventory'] = implementation_inventory
    stored_provenance = bound['meta'].get('provenance') if isinstance(bound['meta'].get('provenance'),
                                                                      dict) else {}
    runtime_gate = {}
    for arm in ARMS:
        runtime_gate[arm] = probe_runtime_gate(python, plan['arms'][arm]['source'],
                                               stored_provenance, timeout=timeout)
    report['gates']['runtimeGate'] = runtime_gate
    stored = plan['parity'].get('expectedStoredDigest')
    legacy_digest = (probes[ARM_LEGACY]['provenance'] or {}).get('digest')
    new_digest = (probes[ARM_NEW]['provenance'] or {}).get('digest')
    gate = dict(storedDigest=stored, legacyDigest=legacy_digest, newDigest=new_digest,
                legacyMatchesStored=(legacy_digest == stored), changedFiles={})
    legacy_files = (probes[ARM_LEGACY]['provenance'] or {}).get('files') or {}
    new_files = (probes[ARM_NEW]['provenance'] or {}).get('files') or {}
    for name in sorted(set(legacy_files) | set(new_files)):
        if legacy_files.get(name) != new_files.get(name):
            gate['changedFiles'][name] = dict(legacy=legacy_files.get(name), new=new_files.get(name))
    new_ok = (new_digest == stored)
    legacy_provenance = probes[ARM_LEGACY]['provenance'] if isinstance(
        probes[ARM_LEGACY].get('provenance'), dict) else {}
    new_provenance = probes[ARM_NEW]['provenance'] if isinstance(
        probes[ARM_NEW].get('provenance'), dict) else {}
    migration = dict(eligible=False, reason='not evaluated') if new_ok else migration_gate(
        legacy_provenance, new_provenance)
    gate['migration'] = migration
    migration_ok = bool(new_ok or migration.get('eligible'))
    gate['verdict'] = ('identical' if new_ok else
                       'migration-verified' if migration.get('eligible') else 'inconclusive')
    report['gates']['parity'] = gate
    if not (probes[ARM_LEGACY]['provenance'].get('ok') and legacy_digest == stored):
        report['blocked'].append('the legacy arm does not reproduce the stored baseline provenance')
    if probes[ARM_LEGACY]['interface'].get('coordinatorWired'):
        report['notes'].append('legacy arm unexpectedly reports a wired coordinator')
    if not probes[ARM_NEW]['interface'].get('coordinatorWired'):
        report['blocked'].append('the new arm runtime does not wire the Coordinator into the loop')
    if not migration_ok:
        report['blocked'].append('the new arm provenance differs from the stored baseline and the '
                                 'reviewed observer migration is not eligible: '
                                 + str(migration.get('reason') or 'no reason published'))
    for arm in ARMS:
        if not probes[arm]['interface'].get('ok'):
            report['blocked'].append(f'arm {arm} interface probe failed')
    report['notes'].append('the reviewed observer migration is the gate; same_simulator and the '
                           'runtime probe are reported for information only and never flip the gate')
    # Copy filesystem headroom: at most two unit copies are live at once, plus a bounded WAL. The
    # limit is declared in the plan; here we check the actual free space on the copy filesystem and
    # fail closed rather than start a copy that cannot fit.
    headroom = copy_filesystem_headroom(plan, out_root)
    report['gates']['copyStorage'] = headroom
    if headroom.get('ok') is False:
        report['blocked'].append(
            f"copy filesystem headroom {headroom.get('freeMiB')} MiB is below the declared "
            f"{headroom['requiredFreeMiB']} MiB bound ({headroom['peakCopies']} x "
            f"{headroom['copyMiB']} MiB copies + {headroom['walMiB']} MiB WAL)")
    return report


def copy_baseline(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    started = time.time()
    # sqlite3's connection context manager commits or rolls back but NEVER closes the handle. A
    # destination handle left open here stayed attached to the copy and made the later
    # ownership-scoped unlink fail with PermissionError [WinError 32]. Close BOTH handles explicitly
    # and never mask the original exception with a cleanup error.
    origin = copy = None
    try:
        origin = sqlite3.connect(f'file:{Path(source)}?mode=ro', uri=True)
        origin.execute('PRAGMA query_only=ON')
        copy = sqlite3.connect(str(target))
        origin.backup(copy)
    finally:
        for handle in (copy, origin):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - a failed close must not mask the original error
                pass
    return round(time.time() - started, 2)


def delete_copy(path):
    path = Path(path)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        for suffix in ('-wal', '-shm'):
            side = Path(str(path) + suffix)
            if side.exists():
                side.unlink()
        path.unlink(missing_ok=True)


#: Every temporary library copy this harness makes is named exactly this inside its own unit dir.
UNIT_COPY_NAME = 'copy.sqlite'


def unit_copy_dir(out_root, arm, encounter_id, replicate):
    """A per-(arm, encounter, replicate) unit dir, so no two encounters ever share one library copy."""
    return Path(out_root) / f'{arm}-e{int(encounter_id)}-r{int(replicate)}'


def register_copy(created, path, *, root):
    """Record a copy this run is about to create, refusing any path outside the run root.

    Only registered paths are ever deleted, so the frozen baseline and anything the harness did not
    itself create can never be removed by cleanup.
    """
    resolved = Path(path).resolve()
    root = Path(root).resolve()
    if resolved.name != UNIT_COPY_NAME:
        raise HarnessError(f'refusing to own a non-unit copy name: {resolved}')
    if resolved != root and root not in resolved.parents:
        raise HarnessError(f'refusing to own a copy outside the run root: {resolved}')
    if resolved in created:
        raise HarnessError(f'copy already owned by this run: {resolved}')
    created.append(resolved)
    return resolved


def cleanup_copies(created, paths, *, root):
    """Delete ONLY the unit copies this run created and still owns; never the baseline.

    A path that is not owned by this run is left untouched (it is simply absent from ``created``), so
    a stale or foreign file can never be deleted by mistake. Ownership is by exact resolved path that
    is inside the run root and carries the unit copy name.
    """
    root = Path(root).resolve()
    for path in paths:
        if path is None:
            continue
        resolved = Path(path).resolve()
        if resolved not in created:
            continue
        if resolved.name != UNIT_COPY_NAME:
            raise HarnessError(f'refusing to delete a non-unit copy: {resolved}')
        if resolved != root and root not in resolved.parents:
            raise HarnessError(f'refusing to delete a copy outside the run root: {resolved}')
        delete_copy(resolved)
        created.remove(resolved)


def cross_arm_summary(units, plan, *, incomplete=False, incomplete_units=None):
    """Candidate-level final means on the common holdout pairs plus a paired bootstrap diagnostic.

    The paired delta is new minus legacy, so a positive mean and high positive fraction mean the new
    arm's frozen nominee earned more than the legacy arm's on the identical fresh pairs.
    """
    diagnostics = []
    by_encounter = {}
    incomplete_units = {tuple(item) for item in (incomplete_units or [])}
    for unit in units:
        wrapper = unit.get('holdout')
        if wrapper is not None:
            entries = wrapper.get('encounters') or []
        elif unit.get('stage') == 'holdout' and unit.get('nominee') and 'rows' in unit:
            entries = [unit]
        else:
            continue
        for entry in entries:
            key = (entry['replicate'], entry['encounterId'])
            slot = by_encounter.setdefault(key, {})
            slot[unit['arm']] = entry
    for (replicate, encounter_id), slot in sorted(by_encounter.items(), key=lambda item: str(item[0])):
        if (replicate, encounter_id) in incomplete_units:
            diagnostics.append(dict(replicate=replicate, encounterId=encounter_id,
                                    verdict='inconclusive',
                                    reason='incomplete unit: unequal or unfinished search; excluded '
                                           'from the comparative verdict'))
            continue
        legacy, new = slot.get(ARM_LEGACY), slot.get(ARM_NEW)
        if not legacy or not new:
            diagnostics.append(dict(replicate=replicate, encounterId=encounter_id,
                                    verdict='inconclusive', reason='missing arm'))
            continue
        legacy_cells = {}
        for row in legacy.get('rows') or []:
            if row.get('candidateId') == legacy.get('nominee'):
                legacy_cells[tuple(row.get('seeds') or ())] = row['outcome'].get('finalEarned') \
                    if row['outcome'].get('resolved') else None
        new_cells = {}
        for row in new.get('rows') or []:
            if row.get('candidateId') == new.get('nominee'):
                new_cells[tuple(row.get('seeds') or ())] = row['outcome'].get('finalEarned') \
                    if row['outcome'].get('resolved') else None
        deltas, missing = [], 0
        for pair in sorted(set(legacy_cells) | set(new_cells)):
            legacy_value, new_value = legacy_cells.get(pair), new_cells.get(pair)
            if legacy_value is None or new_value is None:
                missing += 1
                continue
            # Positive means the new nominee earned more than the legacy nominee on this pair.
            deltas.append(new_value - legacy_value)
        stats = paired_bootstrap(deltas, resamples=int(plan['statistics']['bootstrap']['resamples']),
                                 seed=int(plan['statistics']['bootstrap']['seed']) ^ int(encounter_id))
        threshold = float(plan['statistics']['reliabilityThreshold'])
        unresolved_ok = missing == 0
        verdict = 'inconclusive'
        if unresolved_ok and stats['mean'] is not None:
            if stats['mean'] > 0 and stats['fractionPositive'] >= threshold:
                verdict = 'new-favoured'
            elif stats['mean'] < 0 and (1.0 - stats['fractionPositive']) >= threshold:
                verdict = 'legacy-favoured'
        diagnostics.append(dict(replicate=replicate, encounterId=encounter_id, verdict=verdict,
                                legacyNominee=legacy.get('nominee'), newNominee=new.get('nominee'),
                                identicalNominee=(legacy.get('nominee') == new.get('nominee')),
                                paired=stats, unresolvedPairs=missing,
                                legacyCounts=legacy.get('counts'), newCounts=new.get('counts'),
                                reliabilityThreshold=threshold))
    valid = [row for row in diagnostics if row.get('verdict') in ('new-favoured', 'legacy-favoured')]
    summary = dict(comparisons=len(diagnostics),
                   unresolved=sum(1 for row in diagnostics if row.get('verdict') == 'inconclusive'),
                   newFavoured=sum(1 for row in valid if row['verdict'] == 'new-favoured'),
                   legacyFavoured=sum(1 for row in valid if row['verdict'] == 'legacy-favoured'),
                   identicalNomineeBuilds=sum(1 for row in diagnostics
                                              if row.get('identicalNominee')),
                   incomplete=bool(incomplete), boundaries=_boundaries(units),
                   diagnostics=diagnostics)
    summary['decisive'] = len(valid)
    summary['incompleteUnits'] = len(incomplete_units)
    if incomplete and not incomplete_units:
        # No unit-level detail was supplied: fail closed and claim no verdict at all.
        summary['decisive'] = 0
        summary['note'] = ('a real deadline ended a stage early; the comparison is incomplete and no '
                           'verdict is claimed')
    elif incomplete:
        summary['note'] = ('incomplete units are excluded from the comparative verdict; complete '
                           'units still count')
    return summary


def _boundaries(units):
    findings, gaps = [], []
    for unit in units:
        search = unit.get('search') or {}
        timeline = search.get('incumbentTimeline') or {}
        rows = timeline.get('boundaries') if isinstance(timeline, dict) else None
        for row in rows or []:
            findings.append(dict(arm=unit['arm'], testedPoints=row.get('testedPoints'),
                                 unresolvedGaps=row.get('unresolvedGaps')))
    return dict(findings=findings, gaps=gaps,
                note='mechanical boundary findings only; gaps stay unknown and non-monotone regions '
                     'are not smoothed into a safe box')


#: Fingerprint-keyed cache of the deterministic seed_pair ranges; keyed only by (phase, limit) so the
#: immutable baseline's bank is computed once and reused between units.
_DETERMINISTIC_BANK_CACHE = {}

#: Fingerprint-keyed cache of a whole copy's forbidden bank. A mutated copy has a new fingerprint, so
#: a cached entry is never stale; the immutable baseline/unchanged copies reuse one entry.
_FORBIDDEN_BANK_CACHE = {}


def _library_fingerprint(path):
    """A cheap identity for a SQLite library: main plus WAL/SHM size and mtime."""
    stat = Path(path).stat()
    parts = [int(stat.st_size), int(stat.st_mtime_ns)]
    for suffix in ('-wal', '-shm'):
        side = Path(str(path) + suffix)
        if side.exists():
            side_stat = side.stat()
            parts += [suffix, int(side_stat.st_size), int(side_stat.st_mtime_ns)]
        else:
            parts += [suffix, None, None]
    return tuple(parts)


def _deterministic_bank(phase, limit):
    """``seed_pair(phase, i)`` for ``i`` in ``range(limit)``, cached by (phase, limit)."""
    key = (str(phase), int(limit))
    cached = _DETERMINISTIC_BANK_CACHE.get(key)
    if cached is None:
        from strategy_optimizer import seed_pair
        cached = frozenset(tuple(int(value) for value in seed_pair(phase, index))
                           for index in range(int(limit)))
        _DETERMINISTIC_BANK_CACHE[key] = cached
    return cached


def _parse_seed_pair(raw, *, source):
    """One explicit stored seed pair, or fail closed when the compact evidence is malformed."""
    try:
        values = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise Blocked(f'freshness: malformed seed evidence in {source}: {raw!r}') from exc
    if (not isinstance(values, list) or len(values) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values)):
        raise Blocked(f'freshness: malformed seed evidence in {source}: {raw!r}')
    return (int(values[0]), int(values[1]))


def _parse_declared_bank(value, *, source):
    """A declared bank/counter JSON object, or fail closed when it cannot be read."""
    try:
        declared = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise Blocked(f'freshness: malformed declared bank in {source}: {value!r}') from exc
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise Blocked(f'freshness: declared bank in {source} is not a mapping')
    return declared


def _library_forbidden_pairs(copy_path, *, cache=None):
    """Every seed pair ONE arm copy is authorised to reuse, across ALL of its candidates.

    Freshness must exclude tuning/development used on ANY candidate, including eliminated candidates,
    not merely the two nominees. Collected sources: every ``ea_*`` reservation/frozen holdout, the
    global deterministic ``seed_pair(phase, ordinal)`` banks (the max ordinal per phase over both the
    ``run`` and the compact ``evidence`` tables, plus the aggregate/probe/fine-tune bank ceilings),
    and every explicit compact-evidence ``seeds`` pair. Only SQL aggregates and DISTINCT reads are
    used, so the multi-million-row ``run``/``evidence`` tables are never materialised in Python. A
    missing copy, a failed required read or malformed seed evidence fails closed; a bank table a
    fixture legitimately lacks contributes nothing.
    """
    path = Path(copy_path)
    if not path.is_file():
        raise Blocked(f'freshness: library copy missing: {path}')
    cache_store = _FORBIDDEN_BANK_CACHE if cache is None else cache
    key = (str(path.resolve()), _library_fingerprint(path))
    cached = cache_store.get(key)
    if cached is not None:
        return set(cached)
    from strategy_optimizer import DISCOVERY_RUNS, VALIDATION_RUNS
    try:
        from strategy_sampling import MAX_MEASUREMENT_RUNS
    except Exception:  # strategy_sampling is an optional planning companion at this layer
        MAX_MEASUREMENT_RUNS = 65536
    limits = {phase: max(int(DISCOVERY_RUNS if phase == 'discovery' else VALIDATION_RUNS),
                         int(MAX_MEASUREMENT_RUNS))
              for phase in ('discovery', 'validation')}
    pairs = set()
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=60)
    try:
        connection.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ('ea_sample', 'ea_sample_link', 'ea_holdout'):
            if table not in tables:
                continue
            try:
                rows = connection.execute(f'SELECT seed_a, seed_b FROM {table}').fetchall()
            except sqlite3.Error as exc:
                raise Blocked(f'freshness: cannot read {table} in {path}: {exc}') from exc
            for seed_a, seed_b in rows:
                pairs.add((int(seed_a), int(seed_b)))
        for table in ('run', 'evidence'):
            if table not in tables:
                continue
            for phase in limits:
                try:
                    row = connection.execute(
                        f'SELECT MAX(ordinal) FROM {table} WHERE phase=?', (phase,)).fetchone()
                except sqlite3.Error as exc:
                    raise Blocked(f'freshness: cannot read {table} in {path}: {exc}') from exc
                if row and row[0] is not None:
                    limits[phase] = max(limits[phase], int(row[0]) + 1)
        if 'evidence' in tables:
            try:
                seed_rows = connection.execute(
                    'SELECT DISTINCT seeds FROM evidence WHERE seeds IS NOT NULL').fetchall()
            except sqlite3.Error as exc:
                raise Blocked(f'freshness: cannot read compact evidence in {path}: {exc}') from exc
            for raw, in seed_rows:
                pairs.add(_parse_seed_pair(raw, source=f'{path}:evidence'))
        if 'meta' in tables:
            try:
                declarations = connection.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%' "
                    "OR key IN ('probeBanks','fineTunePrograms')").fetchall()
            except sqlite3.Error as exc:
                raise Blocked(f'freshness: cannot read meta banks in {path}: {exc}') from exc
            for meta_key, meta_value in declarations:
                if meta_key.startswith('aggregate:'):
                    phase = meta_key.rsplit(':', 1)[-1]
                    if phase not in limits:
                        continue
                    declared = _parse_declared_bank(meta_value, source=f'{path}:{meta_key}')
                    count = declared.get('n')
                    if count is None:
                        continue
                    if isinstance(count, bool) or not isinstance(count, (int, float)):
                        raise Blocked(f'freshness: malformed aggregate counter {meta_key} in {path}')
                    limits[phase] = max(limits[phase], int(count))
                elif meta_key == 'probeBanks':
                    for declared in _parse_declared_bank(
                            meta_value, source=f'{path}:probeBanks').values():
                        if declared is not None:
                            limits['validation'] = max(limits['validation'], int(declared))
                else:  # fineTunePrograms
                    for program in _parse_declared_bank(
                            meta_value, source=f'{path}:fineTunePrograms').values():
                        if not isinstance(program, dict):
                            continue
                        budget = program.get('budget') or {}
                        limits['validation'] = max(limits['validation'],
                                                   int(budget.get('maxBank') or 0))
                        for point in (program.get('points') or {}).values():
                            if not isinstance(point, dict):
                                continue
                            for value in (point.get('bank'), point.get('runs'),
                                          point.get('completedBank')):
                                if value:
                                    limits['validation'] = max(limits['validation'], int(value))
    finally:
        connection.close()
    for phase, limit in limits.items():
        pairs |= _deterministic_bank(phase, limit)
    cache_store[key] = frozenset(pairs)
    return pairs


def _union_reserved(copies, new_pairs, *, issued_pairs=(), cache=None):
    """Every pair that must never be issued as a fresh holdout, or the call fails closed.

    Freshness is checked against the WHOLE library on BOTH arm copies: every ``ea_*`` reservation and
    frozen holdout, every deterministic/authorised/evidence bank (so tuning used on an eliminated
    candidate still reserves its pairs), every new-arm transport pair and every pair the registry has
    already issued. A failed read is never swallowed into a partial set.
    """
    reserved = {tuple(int(value) for value in pair) for pair in new_pairs}
    reserved.update(tuple(int(value) for value in pair) for pair in issued_pairs)
    for arm in ARMS:
        copy_path = copies.get(arm)
        if not copy_path:
            raise Blocked(f'freshness: no arm copy for {arm}; refusing a partial reserved set')
        reserved |= _library_forbidden_pairs(copy_path, cache=cache)
    return reserved


def _terminate_owned_process_tree(process):
    """Terminate ONLY the child process tree this harness spawned; never an unrelated process.

    Windows: ``taskkill`` on the exact owned PID with ``/T`` (that process and its own descendants)
    and ``/F``; the pool's HeadlessPool worker subprocesses are descendants of this PID, so they die
    with it. POSIX: the child is its own session/process-group leader, so exactly that group is
    signalled. A broad name- or pattern-based kill is never used.
    """
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                    capture_output=True)
            if getattr(result, 'returncode', 1) != 0:
                # taskkill was refused; still reap the exact owned process itself, never a sweep.
                process.kill()
        except OSError:
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()


def spawn_arm(python, plan_path, arm, stage, source_root, copy_path, out_dir, *, cap, extra=(),
              seconds, workers):
    """Run one arm stage in its own subprocess with PYTHONPATH pinned to that arm's source root.

    The parent environment is never mutated; only the child receives the arm PYTHONPATH.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [str(python), '-B', '-X', 'utf8', str(Path(__file__).resolve()), 'arm',
               '--authorised', '--plan', str(plan_path), '--arm', arm, '--stage', stage,
               '--source-root', str(source_root), '--out', str(out_dir), '--cap', str(int(cap)),
               '--seconds', str(seconds), '--workers', str(workers)]
    if copy_path:
        command += ['--copy', str(copy_path)]
    command += [str(item) for item in extra]
    env = dict(os.environ)
    env['PYTHONPATH'] = str(source_root)
    started = time.perf_counter()
    creationflags = (getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env=env, creationflags=creationflags,
                               start_new_session=(os.name != 'nt'))
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=float(seconds) + 900)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill ONLY the exact child process tree this harness spawned, before reaping it: on Windows
        # the pool's worker grandchildren would otherwise outlive the direct child.
        _terminate_owned_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            stdout, stderr = '', ''
    if timed_out:
        write_json(out_dir / f'child-{stage}-{arm}.json',
                   dict(returncode=process.returncode, seconds=round(time.perf_counter() - started, 2),
                        timedOut=True, stderr=(stderr or '')[-2000:]))
        raise Incomplete(f'{stage}:{arm} exceeded its real deadline; its owned process tree was '
                         'terminated')
    try:
        payload = json.loads((stdout or '').strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = dict(ok=False, error=(stderr or stdout or '')[-800:])
    write_json(out_dir / f'child-{stage}-{arm}.json',
               dict(returncode=process.returncode, seconds=round(time.perf_counter() - started, 2),
                    timedOut=False, stderr=(stderr or '')[-2000:], payload=payload))
    if process.returncode != 0 or not payload.get('ok'):
        raise Blocked(f'{stage}:{arm} child failed: {payload.get("error") or process.returncode}')
    return payload


def _read_raw_rows(path):
    rows = []
    path = Path(path)
    if not path.is_file():
        return rows
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _persisted_search(unit_dir, arm, encounter_id, replicate, *, copy_path=None):
    """Resume a genuine persisted search nomination for a separate holdout-only stage, or refuse.

    A holdout-only run must never silently produce an empty comparison: it either finds the full
    nomination manifest the search stage wrote (and the arm copy it froze the nominee against), or it
    raises. Nothing is invented and no stage is quietly skipped.
    """
    path = Path(unit_dir) / f'search-{arm}-e{int(encounter_id)}-r{int(replicate)}.json'
    if not path.is_file():
        raise Blocked(
            f'holdout-only: no persisted search nomination for {arm} e{encounter_id} '
            f'r{replicate} at {path}; run the search stage first, or run search and holdout together')
    report = read_json(path)
    if not isinstance(report, dict):
        raise Blocked(f'holdout-only: malformed persisted nomination at {path}')
    if copy_path is not None and not Path(copy_path).is_file():
        raise Blocked(
            f'holdout-only: the frozen arm copy for {arm} e{encounter_id} r{replicate} is not '
            f'present at {copy_path}; a separate holdout stage cannot re-create the nominee evidence')
    return report


def _search_unit_problem(reports, cap=None):
    """Why a (encounter, replicate) search pair cannot be compared, or None when both arms are equal.

    Any underexecution (admitted != declared), a failed/unresolved execution, a non-drained queue
    (the child flags it incomplete) or unequal admitted work between the arms makes the unit
    INCOMPLETE. Such a unit is excluded from the comparative verdict and its holdout is blocked to
    conserve budget; nothing is silently extended, rerun or given a new budget.
    """
    counts = {}
    for arm in ARMS:
        report = reports.get(arm)
        if not isinstance(report, dict) or not report:
            return f'{arm} search result missing'
        if report.get('failed'):
            return f'{arm} search failed'
        if report.get('incomplete'):
            return f'{arm} search incomplete (underexecuted/failed/non-drained)'
        declared = report.get('declared', cap)
        admitted = report.get('admitted')
        if declared is None or admitted is None or int(admitted) != int(declared):
            return f'{arm} underexecution: admitted {admitted} of {declared} declared battles'
        counts[arm] = int(admitted)
    if len(set(counts.values())) != 1:
        return f'unequal admitted work across arms: {counts}'
    return None


def _authorise_plan(plan, out_root, *, authorise):
    """Accept the explicit root ``--authorised`` flag and persist a receipt bound to the plan.

    The plan itself is never edited: it stays the immutable hashed ``proposed`` plan. The receipt is
    a separate artefact that binds the root authorisation to this exact planId+digest before any
    battle; a stale receipt for a different plan is refused, and there is no silent
    auto-authorisation (no flag means no run).
    """
    if not authorise:
        raise Blocked('run-plan requires --authorised (root authorises the run, not the harness)')
    if plan.get('digest') != digest({k: v for k, v in plan.items() if k != 'digest'}):
        raise Blocked('refusing to authorise a plan whose digest does not match its body')
    plan_id = plan.get('planId')
    if not isinstance(plan_id, str) or not plan_id:
        raise Blocked('the plan has no planId to bind an authorisation receipt to')
    receipt_path = Path(out_root) / 'authorisation-receipt.json'
    receipt = dict(kind='encounter-redesign-benchmark-authorisation-1', planId=plan_id,
                   planDigest=plan.get('digest'), authorisedBy='root:--authorised',
                   scope='run-plan', at=now())
    if receipt_path.is_file():
        existing = read_json(receipt_path)
        if existing.get('planId') != plan_id or existing.get('planDigest') != plan.get('digest'):
            raise Blocked('an authorisation receipt already exists for a different plan/digest; '
                          'refusing to run this plan against a stale receipt')
        return existing
    write_json(receipt_path, receipt, immutable=True)
    return receipt


def _persisted_throughput_gate(out_root, plan):
    """A previously persisted matched-infrastructure gate, but only if bound to THIS exact plan."""
    path = Path(out_root) / 'throughput-gate.json'
    if not path.is_file():
        return None
    gate = read_json(path)
    if gate.get('planId') != plan.get('planId') or gate.get('planDigest') != plan.get('digest'):
        raise Blocked('the persisted throughput gate is bound to a different plan/digest; refusing '
                      'to reuse an unbound infrastructure result')
    return gate


def _validated_throughput_report(unit_dir, arm, mode, cap):
    """Load the arm child's OWN persisted full throughput report and validate it, or fail closed.

    ``cmd_arm`` prints only a compact stdout summary (ok/arm/stage/mode/admitted) with NO timing
    stages, so the parent gate must read the complete ``throughput-<arm>-<mode>.json`` the child
    writes. The child asserts the native backend before writing it, so a present report is the
    validated full measurement; the parent still refuses a report that is missing, truncated, for a
    different workload, or that did not admit its declared battles.
    """
    path = Path(unit_dir) / f'throughput-{arm}-{mode}.json'
    if not path.is_file():
        raise Blocked(f'throughput:{mode}: the full validated report {path.name} was not persisted; '
                      'refusing to gate on the child compact summary')
    report = read_json(path)
    if not isinstance(report, dict):
        raise Blocked(f'throughput:{mode}: malformed persisted report at {path}')
    if report.get('arm') != arm or report.get('mode') != mode:
        raise Blocked(f'throughput:{mode}: persisted report is for {report.get("arm")!r}/'
                      f'{report.get("mode")!r}, not this unit')
    if int(report.get('battles') or 0) != int(cap):
        raise Blocked(f'throughput:{mode}: persisted report ran {report.get("battles")} of the '
                      f'declared {cap} battles')
    if int(report.get('admitted') or 0) != int(cap):
        raise Blocked(f'throughput:{mode}: persisted report admitted {report.get("admitted")} of '
                      f'the declared {cap} battles; the workload did not complete')
    stages = report.get('stages')
    if not isinstance(stages, dict) or any(
            not isinstance(stages.get(name), dict) for name in ('dispatch', 'persistence', 'analysis')):
        raise Blocked(f'throughput:{mode}: persisted report has no complete timing stages; refusing '
                      'to gate on an unvalidated infrastructure measurement')
    return report


def _throughput_gate(units, *, out_root, plan):
    """The fail-closed matched-infrastructure gate, persisted and bound to this exact plan.

    Raises unless the two matched modes actually ran the SAME work and the current wall overhead is
    within the predeclared threshold; an unavailable comparison is refused, never skipped, so a full
    search can never run on an unvalidated infrastructure budget.
    """
    comparison = matched_infrastructure_comparison(units)
    comparison['planId'] = plan.get('planId')
    comparison['planDigest'] = plan.get('digest')
    write_json(Path(out_root) / 'throughput-gate.json', comparison)
    if not comparison.get('compared'):
        raise Blocked('matched infrastructure throughput gate is unavailable ('
                      f'{comparison.get("reason")}); refusing to run the comparison on an '
                      'unvalidated infrastructure budget')
    if not comparison.get('withinThreshold'):
        raise Blocked('matched infrastructure wall overhead {} exceeds {}; stopping before the full '
                      'search comparison for root diagnosis'.format(
                          comparison.get('wallRatio'), comparison.get('threshold')))
    return comparison


# --------------------------------------------------------------------------------------------------
# early-recovery carryforward (--resume-from): a narrow, fail-closed continuation of ONE stopped run.
# It never starts a battle here; it validates the prior plan/ledger/artifacts, adopts ONLY the
# completed smoke and telemetry-off/on results (hash-bound and charged once), and lets the normal
# stages re-run the failed matched dispatch in full and run the untouched search/holdout. This is not
# a generic resume engine.
# --------------------------------------------------------------------------------------------------
CARRYFORWARD_SCHEMA = 'encounter-redesign-carryforward-1'

#: The exact stopped-run artifacts an early recovery may adopt, with their declared complete counts.
CARRYFORWARD_ADOPT = (
    dict(stage='smoke', arm=ARM_LEGACY, cap=40, unit='smoke-legacy', raw='raw-smoke-legacy.jsonl'),
    dict(stage='smoke', arm=ARM_NEW, cap=40, unit='smoke-new', raw='raw-smoke-new.jsonl'),
    dict(stage='throughput', arm=ARM_NEW, mode='telemetry-off', cap=64,
         unit='throughput-telemetry-off', raw='raw-throughput-new-telemetry-off.jsonl',
         payload='throughput-new-telemetry-off.json'),
    dict(stage='throughput', arm=ARM_NEW, mode='telemetry-on', cap=64,
         unit='throughput-telemetry-on', raw='raw-throughput-new-telemetry-on.jsonl',
         payload='throughput-new-telemetry-on.json'),
)
#: The failed legacy-dispatch admitted 13 of 64 before the 4 GiB copy overflow; the whole mode is
#: re-run once and the 13 are charged against the setup allowance so no cost is lost or double-counted.
CARRYFORWARD_SETUP_CHARGE = 13
CARRYFORWARD_ADOPTED_BATTLES = sum(int(entry['cap']) for entry in CARRYFORWARD_ADOPT)
CARRYFORWARD_PRIOR_TOTAL = CARRYFORWARD_ADOPTED_BATTLES + CARRYFORWARD_SETUP_CHARGE

#: Fields that may legitimately differ between the stopped plan and the new plan. planId/digest/
#: createdAt change because a plan is written again; smoke.fixedPairs and the bootstrap seed are
#: DERIVED from the planId; storage is the new declared copy limit; the observer fields are the
#: harness revision that changed with this repair. EVERY other field must be byte-identical.
CARRYFORWARD_ALLOWED_DIFFERENCES = ('planId', 'digest', 'createdAt', 'status', 'authorisation',
                                    'smoke.fixedPairs', 'statistics.bootstrap.seed')
CARRYFORWARD_ALLOWED_PREFIXES = ('storage',) + tuple(
    f'arms.{arm}.implementationInventory.{suffix}' for arm in ARMS
    for suffix in ('digest', 'observerRevision', 'observerFiles'))


def carryforward_allowed_difference(path):
    if path in CARRYFORWARD_ALLOWED_DIFFERENCES:
        return True
    return any(path == prefix or path.startswith(prefix + '.')
               for prefix in CARRYFORWARD_ALLOWED_PREFIXES)


def _flatten_paths(prefix, value, out):
    if isinstance(value, dict):
        for key in value:
            _flatten_paths(f'{prefix}.{key}' if prefix else str(key), value[key], out)
    else:
        out[prefix] = canonical(value)


def carryforward_plan_differences(old_plan, new_plan):
    """Every dotted-path difference between two plans, for the carryforward receipt."""
    old, new = {}, {}
    _flatten_paths('', old_plan, old)
    _flatten_paths('', new_plan, new)
    return {path: dict(old=old.get(path), new=new.get(path))
            for path in sorted(set(old) | set(new)) if old.get(path) != new.get(path)}


def disallowed_plan_differences(old_plan, new_plan):
    """Only the declared harness-observer and copy-limit (plus planId-derived) differences are legal."""
    return {path: value for path, value in carryforward_plan_differences(old_plan, new_plan).items()
            if not carryforward_allowed_difference(path)}


def check_carryforward_ledger(new_plan, old_ledger):
    """Fail-closed checks on the stopped run's ledger; returns the list of problems (empty is OK).

    Enforces early-recovery-only (no search/holdout charged) and the exact prior accounting: 221 spent,
    zero outstanding reservations, zero setup, and the exact settlement breakdown (smoke 40+40,
    telemetry 64+64, and the 13-admitted failed legacy-dispatch).
    """
    problems = []
    claim_stages = [str(claim.get('stage') or '') for claim in old_ledger.get('claims') or []]
    if any(stage.startswith(('search', 'holdout')) for stage in claim_stages):
        problems.append('prior ledger already charged search/holdout; refusing an early recovery')
    budget = new_plan.get('budget') or {}
    if old_ledger.get('schema') != LEDGER_SCHEMA:
        problems.append('prior ledger schema mismatch')
    if int(old_ledger.get('spent') or 0) != CARRYFORWARD_PRIOR_TOTAL:
        problems.append(f'prior ledger spent {old_ledger.get("spent")} is not '
                        f'{CARRYFORWARD_PRIOR_TOTAL}')
    if int(old_ledger.get('setupSpent') or 0) != 0:
        problems.append('prior ledger already used setup allowance')
    if old_ledger.get('reserved'):
        problems.append('prior ledger still has outstanding reservations')
    if int(old_ledger.get('ceiling') or 0) != int(budget.get('hardCeiling') or 0):
        problems.append('prior ledger ceiling does not match the new plan hard ceiling')
    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    expected_settles = {f'smoke:{ARM_LEGACY}': [40], f'smoke:{ARM_NEW}': [40],
                        'throughput:telemetry-off': [64], 'throughput:telemetry-on': [64],
                        'throughput:legacy-dispatch': [CARRYFORWARD_SETUP_CHARGE]}
    if settled != expected_settles:
        problems.append(f'prior ledger settlement breakdown {settled} is not the expected '
                        f'{expected_settles}')
    return problems


def _prepare_carryforward_early(new_plan, old_dir, out_root):
    """Validate one stopped early run and return ``(receipt, adopted_index)``.

    Fails closed (``Blocked``) on any mismatch, never repairs or deletes the original run, and never
    starts a battle. An ``adopted_index`` key is ``('smoke', arm)`` or ``('throughput', mode)``.
    """
    old_dir, out_root = Path(old_dir), Path(out_root)
    problems = []
    for name in ('plan.json', 'battle-ledger.json', 'authorisation-receipt.json'):
        if not (old_dir / name).is_file():
            problems.append(f'prior run is missing {name}')
    if problems:
        raise Blocked('carryforward prerequisites failed: ' + '; '.join(problems))
    old_plan = read_json(old_dir / 'plan.json')
    old_ledger = read_json(old_dir / 'battle-ledger.json')
    old_receipt = read_json(old_dir / 'authorisation-receipt.json')

    # 1. The prior plan is a valid, digest-bound plan authored the same way as this one, and the
    #    root authorisation receipt is bound to exactly it.
    # `open items` are a plan-writing adjudication, not a carryforward semantic; every other error
    # (digest, budget, inventory, scope, ordering) fails the prior plan closed.
    problems.extend(f'prior plan: {error}' for error in validate_plan(old_plan, check_baseline=False)
                    if 'open items' not in error)
    if (old_receipt.get('kind') != 'encounter-redesign-benchmark-authorisation-1'
            or old_receipt.get('planId') != old_plan.get('planId')
            or old_receipt.get('planDigest') != old_plan.get('digest')
            or old_receipt.get('authorisedBy') != 'root:--authorised'
            or old_receipt.get('scope') != 'run-plan'):
        problems.append('prior authorisation receipt is not bound to the prior plan (root:--authorised)')

    # 2. Semantic equality with the new plan: only the declared differences may differ.
    disallowed = disallowed_plan_differences(old_plan, new_plan)
    if disallowed:
        problems.append('the new plan changes non-declared fields: ' + ', '.join(sorted(disallowed)))

    # 3/4. Early recovery only (no prior search/holdout), and the exact prior ledger accounting.
    if (old_dir / 'results.json').is_file():
        problems.append('prior run already produced results.json; this is not an early recovery')
    problems.extend(check_carryforward_ledger(new_plan, old_ledger))

    # 5. Adopt the completed smoke/telemetry artifacts, copy them into the new run and hash-bind them.
    adopted, copied = [], []
    for entry in CARRYFORWARD_ADOPT:
        unit_dir = old_dir / entry['unit']
        raw_path = unit_dir / entry['raw']
        rows = _read_raw_rows(raw_path)
        if len(rows) != int(entry['cap']):
            problems.append(f'{entry["unit"]}: {len(rows)} raw rows is not {entry["cap"]}')
            continue
        backends = summarise_backend(rows)
        if backends['native'] != len(rows) or backends['fallback'] or backends['unknown']:
            problems.append(f'{entry["unit"]}: not every adopted row is native: {backends}')
            continue
        payload_path = unit_dir / entry['payload'] if entry.get('payload') else None
        if payload_path is not None:
            payload = read_json(payload_path)
            if int(payload.get('admitted') or 0) != int(entry['cap']):
                problems.append(f'{entry["unit"]}: payload admitted {payload.get("admitted")} is not '
                                f'{entry["cap"]}')
                continue
            if payload.get('mode') != entry.get('mode'):
                problems.append(f'{entry["unit"]}: payload mode {payload.get("mode")!r} is not '
                                f'{entry.get("mode")!r}')
                continue
        new_unit = out_root / entry['unit']
        new_unit.mkdir(parents=True, exist_ok=True)
        new_raw = new_unit / entry['raw']
        shutil.copyfile(raw_path, new_raw)
        copied.append(new_raw)
        source_hash, adopted_hash = _file_sha256(raw_path), _file_sha256(new_raw)
        if source_hash != adopted_hash or not source_hash:
            problems.append(f'{entry["unit"]}: adopted copy hash does not match the original')
            continue
        new_payload = None
        if payload_path is not None:
            new_payload = new_unit / entry['payload']
            shutil.copyfile(payload_path, new_payload)
            copied.append(new_payload)
            if _file_sha256(new_payload) != _file_sha256(payload_path):
                problems.append(f'{entry["unit"]}: adopted payload hash does not match the original')
                continue
        adopted.append(dict(kind='adopted-artifact', stage=entry['stage'], arm=entry['arm'],
                            mode=entry.get('mode'), unit=entry['unit'], cap=int(entry['cap']),
                            admitted=int(entry['cap']), sourcePath=str(raw_path),
                            adoptedPath=str(new_raw), sourceSha256=source_hash,
                            payloadPath=(str(payload_path) if payload_path else None),
                            adoptedPayloadPath=(str(new_payload) if new_payload else None),
                            payloadSha256=(_file_sha256(payload_path) if payload_path else None),
                            backends=backends,
                            note='completed in the stopped run; reused unchanged, charged once'))
    if problems:
        for path in copied:
            try:
                path.unlink()
            except OSError:
                pass
        raise Blocked('carryforward prerequisites failed: ' + '; '.join(problems))

    budget = new_plan.get('budget') or {}
    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    rerun_battles = len(MATCHED_MODES) * int(new_plan['throughput']['battlesPerMode'])
    search_battles = int((budget.get('stages') or {}).get('search', {}).get('battles') or 0)
    holdout_battles = int((budget.get('stages') or {}).get('holdout', {}).get('battles') or 0)
    expected_total = CARRYFORWARD_PRIOR_TOTAL + rerun_battles + search_battles + holdout_battles
    ceiling = int(budget.get('hardCeiling') or 0)
    if expected_total > ceiling:
        raise Blocked(f'carryforward total {expected_total} exceeds the hard ceiling {ceiling}')
    receipt = dict(
        schema=CARRYFORWARD_SCHEMA, createdAt=now(),
        priorPlan=dict(path=str(old_dir / 'plan.json'), planId=old_plan.get('planId'),
                       digest=old_plan.get('digest'), status=old_plan.get('status'),
                       authorisedBy=old_receipt.get('authorisedBy')),
        newPlan=dict(planId=new_plan.get('planId'), digest=new_plan.get('digest'),
                     status=new_plan.get('status')),
        priorAuthorisation=old_receipt,
        priorLedger=dict(path=str(old_dir / 'battle-ledger.json'),
                         ceiling=int(old_ledger.get('ceiling') or 0),
                         spent=int(old_ledger.get('spent') or 0),
                         setupSpent=int(old_ledger.get('setupSpent') or 0),
                         reserved=old_ledger.get('reserved') or {}, settled=settled,
                         nextReservation=old_ledger.get('nextReservation')),
        adopted=adopted, adoptedBattles=CARRYFORWARD_ADOPTED_BATTLES,
        setupCharge=dict(stage='throughput:legacy-dispatch', battles=CARRYFORWARD_SETUP_CHARGE,
                         reason='the stopped legacy-dispatch admitted 13 of 64 before the 4 GiB copy '
                                'overflow; charged against the 32 setup allowance so the whole mode is '
                                're-run once with no lost cost and no double count'),
        priorTotal=CARRYFORWARD_PRIOR_TOTAL,
        rerun=dict(matchedDispatchBattles=rerun_battles, searchBattles=search_battles,
                   holdoutBattles=holdout_battles,
                   note='the entire failed legacy-dispatch and the never-run new-dispatch are re-run '
                        'once; the fixed search/holdout budgets are unchanged'),
        expectedTotal=expected_total, hardCeiling=ceiling,
        deviations=[
            dict(kind='harness-observer',
                 note='the harness observer revision changed with benchmark_encounter_redesign.py; '
                      'the prior and new plans and every adopted artifact are hash-bound here'),
            dict(kind='copy-storage-limit', priorMiB=OPTIMIZER_DEFAULT_DB_MIB, copyMiB=COPY_STORAGE_MIB,
                 note='copy-only limit, applied identically to both arms in the arm child before any '
                      'Store is constructed; process-local, never persisted to live config')],
        planDifferences=carryforward_plan_differences(old_plan, new_plan),
        allowedDifferences=list(CARRYFORWARD_ALLOWED_DIFFERENCES),
        allowedPrefixes=list(CARRYFORWARD_ALLOWED_PREFIXES))
    write_json(out_root / 'carryforward-receipt.json', receipt)
    index = {('smoke', entry['arm']): entry for entry in adopted if entry['stage'] == 'smoke'}
    index.update({('throughput', entry['mode']): entry for entry in adopted
                  if entry['stage'] == 'throughput'})
    return receipt, index


# --------------------------------------------------------------------------------------------------
# v4 continuation (--resume-from a stopped run that already charged the e0r1 search pair).
#
# Detected by CONTENT (a prior search/holdout claim), never by directory name. This is NOT a generic
# resume engine: the one failed (e0r1) predeclared pair is permanently inconclusive and is skipped -
# never re-run, never re-nominated, never replaced - the fully completed v4 stages are adopted
# hash-bound and charged once (no lost cost, no double count), and only the untouched 11 pairs run.
# Every original artifact stays read-only.
# --------------------------------------------------------------------------------------------------
CARRYFORWARD_V4_SCHEMA = 'encounter-redesign-carryforward-v4-1'

#: The exact completed v4 artifacts adopted hash-bound (their own v4 raw + payload/§report).
CARRYFORWARD_V4_ADOPT = (
    dict(stage='smoke', arm=ARM_LEGACY, cap=40, unit='smoke-legacy', raw='raw-smoke-legacy.jsonl'),
    dict(stage='smoke', arm=ARM_NEW, cap=40, unit='smoke-new', raw='raw-smoke-new.jsonl'),
    dict(stage='throughput', arm=ARM_NEW, mode='telemetry-off', cap=64,
         unit='throughput-telemetry-off', raw='raw-throughput-new-telemetry-off.jsonl',
         payload='throughput-new-telemetry-off.json', persistence='current-pool'),
    dict(stage='throughput', arm=ARM_NEW, mode='telemetry-on', cap=64,
         unit='throughput-telemetry-on', raw='raw-throughput-new-telemetry-on.jsonl',
         payload='throughput-new-telemetry-on.json', persistence='current-pool'),
    dict(stage='throughput', arm=ARM_LEGACY, mode='legacy-dispatch', cap=64,
         unit='throughput-legacy-dispatch', raw='raw-throughput-legacy-legacy-dispatch.jsonl',
         payload='throughput-legacy-legacy-dispatch.json', persistence='store'),
    dict(stage='throughput', arm=ARM_NEW, mode='new-dispatch', cap=64,
         unit='throughput-new-dispatch', raw='raw-throughput-new-new-dispatch.jsonl',
         payload='throughput-new-new-dispatch.json', persistence='ledger'),
)
CARRYFORWARD_V4_SETUP_CHARGE = 13
CARRYFORWARD_V4_ADOPTED_BATTLES = sum(int(entry['cap']) for entry in CARRYFORWARD_V4_ADOPT)
#: (ledger stage, arm, exact admitted) for the one permanently failed predeclared pair.
CARRYFORWARD_V4_FAILED_SEARCHES = (('search:legacy:0:r1', ARM_LEGACY, 128),
                                   ('search:new:0:r1', ARM_NEW, 4))
CARRYFORWARD_V4_FAILED_PAIR = (0, 1)
CARRYFORWARD_V4_PRIOR_REGULAR = (CARRYFORWARD_V4_ADOPTED_BATTLES
                                 + sum(int(entry[2]) for entry in CARRYFORWARD_V4_FAILED_SEARCHES))
CARRYFORWARD_V4_PRIOR_TOTAL = CARRYFORWARD_V4_PRIOR_REGULAR + CARRYFORWARD_V4_SETUP_CHARGE
CARRYFORWARD_V4_EXPECTED_PAIRS = 12

#: Fields that may legitimately differ between the stopped v4 plan and the new plan: the harness
#: observer metadata and the fields DERIVED from the (re-written) planId. ``storage`` is deliberately
#: absent: the 8 GiB copy limit and every runtime/combat/native/domain/policy/per-unit budget must be
#: byte-identical for a v4 continuation.
CARRYFORWARD_V4_ALLOWED_DIFFERENCES = ('planId', 'digest', 'createdAt', 'status', 'authorisation',
                                       'smoke.fixedPairs', 'statistics.bootstrap.seed')
CARRYFORWARD_V4_ALLOWED_PREFIXES = tuple(
    f'arms.{arm}.implementationInventory.{suffix}' for arm in ARMS
    for suffix in ('digest', 'observerRevision', 'observerFiles'))


def _prior_has_search_claims(ledger):
    """True when the prior ledger already charged a search/holdout stage (i.e. it is a v4 run)."""
    return any(str(claim.get('stage') or '').startswith(('search', 'holdout'))
               for claim in (ledger or {}).get('claims') or [])


def carryforward_allowed_difference_v4(path):
    if path in CARRYFORWARD_V4_ALLOWED_DIFFERENCES:
        return True
    return any(path == prefix or path.startswith(prefix + '.')
               for prefix in CARRYFORWARD_V4_ALLOWED_PREFIXES)


def disallowed_plan_differences_v4(old_plan, new_plan):
    """Only the declared observer + planId-derived differences are legal for a v4 continuation."""
    return {path: value for path, value in carryforward_plan_differences(old_plan, new_plan).items()
            if not carryforward_allowed_difference_v4(path)}


def check_carryforward_v4_ledger(new_plan, old_ledger):
    """Fail-closed checks on the stopped v4 ledger; returns the list of problems (empty is OK).

    Requires the EXACT persisted v4 shape and nothing more: the six completed stages settled
    40/40/64/64/64/64, the legacy e0r1 search settled 128, the new e0r1 search reserved 128 but
    settled 0 (its 4 cleanup dispatches were written after the interim meter snapshot), the 13
    carried setup charge, no outstanding reservation, and no claim beyond that set. A prior extra
    claim is an unexpected cost and is refused rather than re-charged.
    """
    problems = []
    if old_ledger.get('schema') != LEDGER_SCHEMA:
        problems.append('prior ledger schema mismatch')
    budget = new_plan.get('budget') or {}
    if int(old_ledger.get('ceiling') or 0) != int(budget.get('hardCeiling') or 0):
        problems.append('prior ledger ceiling does not match the new plan hard ceiling')
    if old_ledger.get('reserved'):
        problems.append('prior ledger still has outstanding reservations')
    if int(old_ledger.get('setupSpent') or 0) != CARRYFORWARD_V4_SETUP_CHARGE:
        problems.append(f'prior ledger setup charge {old_ledger.get("setupSpent")} is not '
                        f'{CARRYFORWARD_V4_SETUP_CHARGE}')
    expected_settles = {
        'carryforward:smoke:legacy': [40], 'carryforward:smoke:new': [40],
        'carryforward:throughput:telemetry-off': [64],
        'carryforward:throughput:telemetry-on': [64],
        'throughput:legacy-dispatch': [64], 'throughput:new-dispatch': [64],
        'search:legacy:0:r1': [128], 'search:new:0:r1': [0]}
    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    if settled != expected_settles:
        problems.append(f'prior ledger settlement breakdown {settled} is not the expected v4 shape '
                        f'{expected_settles}')
    else:
        expected_spent = sum(values[0] for values in expected_settles.values())
        if int(old_ledger.get('spent') or 0) != expected_spent:
            problems.append(f'prior ledger spent {old_ledger.get("spent")} is not the expected v4 '
                            f'total {expected_spent}')
    setups = [int(claim.get('count') or 0) for claim in old_ledger.get('claims') or []
              if claim.get('op') == 'setup']
    if setups != [CARRYFORWARD_V4_SETUP_CHARGE]:
        problems.append(f'prior ledger setup claims {setups} are not one '
                        f'{CARRYFORWARD_V4_SETUP_CHARGE}-battle charge')
    reserves = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'reserve':
            reserves.setdefault(str(claim.get('stage')), []).append(int(claim.get('cap') or 0))
    expected_reserves = {stage: [cap] for stage, cap in (
        ('carryforward:smoke:legacy', 40), ('carryforward:smoke:new', 40),
        ('carryforward:throughput:telemetry-off', 64),
        ('carryforward:throughput:telemetry-on', 64), ('throughput:legacy-dispatch', 64),
        ('throughput:new-dispatch', 64), ('search:legacy:0:r1', 128), ('search:new:0:r1', 128))}
    if reserves != expected_reserves:
        problems.append(f'prior ledger reserved stages {sorted(reserves)} are not the expected v4 '
                        f'reservation set')
    return problems


def _v4_failed_pair(old_dir, old_plan):
    """The one permanently failed e0r1 pair: exact conservative counts, hashes, no native claim."""
    encounter, replicate = CARRYFORWARD_V4_FAILED_PAIR
    problems = []
    units = {ARM_LEGACY: old_dir / 'legacy-e0-r1', ARM_NEW: old_dir / 'new-e0-r1'}
    base = f'search-{{arm}}-e{encounter}-r{replicate}.json'
    meters = {ARM_LEGACY: units[ARM_LEGACY] / f'meter-search-e{encounter}-legacy.json',
              ARM_NEW: units[ARM_NEW] / f'meter-search-e{encounter}-new.json'}
    reports = {arm: units[arm] / base.format(arm=arm) for arm in ARMS}
    payloads = {}
    for arm in ARMS:
        if not reports[arm].is_file():
            problems.append(f'the failed pair is missing the {arm} search report {reports[arm].name}')
            continue
        payloads[arm] = read_json(reports[arm])
        if not isinstance(payloads[arm], dict):
            problems.append(f'the failed pair {arm} search report is malformed')
        for leftover in units[arm].glob('holdout-*'):
            problems.append(f'the failed pair already has a holdout artifact {leftover.name}; refusing '
                            'to re-run or reconstruct a nomination')
    if problems:
        return problems, None
    extra = ()
    ledger = payloads[ARM_NEW].get('eaLedger') or {}
    session = (ledger.get('ea_session_budget') or {}).get('completed')
    experiment = (ledger.get('ea_experiment_budget') or {}).get('completed')
    extra = tuple(value for value in (session, experiment)
                  if isinstance(value, int) and not isinstance(value, bool))
    legacy_admitted = _recovered_admitted(meters[ARM_LEGACY], None, 128,
                                          report_path=reports[ARM_LEGACY])
    new_admitted = _recovered_admitted(meters[ARM_NEW], None, 128, report_path=reports[ARM_NEW],
                                       extra_counts=extra)
    expected = {arm: int(value) for _stage, arm, value in CARRYFORWARD_V4_FAILED_SEARCHES}
    if legacy_admitted != expected[ARM_LEGACY]:
        problems.append(f'the failed legacy e{encounter}r{replicate} search admitted '
                        f'{legacy_admitted}, not {expected[ARM_LEGACY]}')
    if new_admitted != expected[ARM_NEW]:
        problems.append(f'the failed new e{encounter}r{replicate} cleanup search admitted '
                        f'{new_admitted}, not {expected[ARM_NEW]}')
    if not payloads[ARM_NEW].get('incomplete'):
        problems.append('the failed new search is not marked incomplete; refusing to call it failed')
    if (payloads[ARM_NEW].get('nomination') or {}).get('nominee'):
        problems.append('the failed new search produced a nominee; refusing to reconstruct/replace it')
    if int(payloads[ARM_LEGACY].get('declared') or 0) != 128:
        problems.append('the failed legacy search did not declare the full 128-battle budget')
    if problems:
        return problems, None
    record = dict(
        encounter=int(encounter), replicate=int(replicate),
        status='permanently-failed-inconclusive',
        legacyAdmitted=legacy_admitted, newAdmitted=new_admitted, holdoutBattles=0,
        replacement=False, nominationReconstructed=False,
        nativeStatus='unavailable: the 4 new cleanup dispatches were persisted after the transport '
                     'snapshot, so no native-backend claim is made for them',
        legacyReport=dict(path=str(reports[ARM_LEGACY]),
                          sha256=_file_sha256(reports[ARM_LEGACY])),
        newReport=dict(path=str(reports[ARM_NEW]), sha256=_file_sha256(reports[ARM_NEW]),
                       eaSessionCompleted=session, eaExperimentCompleted=experiment),
        note='charged exactly once as prior compute; the pair is never re-run or re-nominated')
    return [], record


def _adopt_completed_units(old_dir, out_root, adopt, note):
    """Validate + copy the completed adopt specs into ``out_root``, hash-bound.

    Shared by every continuation branch so there is exactly one artifact verifier: the original run
    is never repaired, deleted or re-run, a short/mis-labelled/non-native artifact fails closed, and
    the adopted copy is hash-bound to the original. Returns ``(adopted, copied, problems)``.
    """
    old_dir, out_root = Path(old_dir), Path(out_root)
    adopted, copied, problems = [], [], []
    for entry in adopt:
        unit_dir = old_dir / entry['unit']
        raw_path = unit_dir / entry['raw']
        bad = False
        rows = _read_raw_rows(raw_path)
        if len(rows) != int(entry['cap']):
            problems.append(f'{entry["unit"]}: {len(rows)} raw rows is not {entry["cap"]}')
            bad = True
        backends = summarise_backend(rows)
        if backends['native'] != len(rows) or backends['fallback'] or backends['unknown']:
            problems.append(f'{entry["unit"]}: not every adopted row is native: {backends}')
            bad = True
        row_arms = {row.get('arm') for row in rows if row.get('arm') is not None}
        if row_arms and row_arms != {entry['arm']}:
            problems.append(f'{entry["unit"]}: raw rows are labelled {sorted(row_arms)}, not '
                            f'{entry["arm"]!r}')
            bad = True
        payload_path = unit_dir / entry['payload'] if entry.get('payload') else None
        payload = read_json(payload_path) if payload_path is not None else None
        if payload is not None:
            bindings = [('admitted', int(payload.get('admitted') or 0), int(entry['cap'])),
                        ('battles', int(payload.get('battles') or 0), int(entry['cap']))]
            for field, actual, want in bindings:
                if actual != want:
                    problems.append(f'{entry["unit"]}: payload {field} {actual} is not {want}')
                    bad = True
            if payload.get('mode') != entry.get('mode'):
                problems.append(f'{entry["unit"]}: payload mode {payload.get("mode")!r} is not '
                                f'{entry.get("mode")!r}')
                bad = True
            if payload.get('arm') != entry['arm']:
                problems.append(f'{entry["unit"]}: payload arm {payload.get("arm")!r} is not '
                                f'{entry["arm"]!r}')
                bad = True
            if entry.get('persistence') and payload.get('persistence') != entry['persistence']:
                problems.append(f'{entry["unit"]}: payload persistence '
                                f'{payload.get("persistence")!r} is not {entry["persistence"]!r}')
                bad = True
        if bad:
            continue
        new_unit = out_root / entry['unit']
        new_unit.mkdir(parents=True, exist_ok=True)
        new_raw = new_unit / entry['raw']
        shutil.copyfile(raw_path, new_raw)
        copied.append(new_raw)
        source_hash, adopted_hash = _file_sha256(raw_path), _file_sha256(new_raw)
        if source_hash != adopted_hash or not source_hash:
            problems.append(f'{entry["unit"]}: adopted copy hash does not match the original')
            continue
        new_payload = None
        if payload_path is not None:
            new_payload = new_unit / entry['payload']
            shutil.copyfile(payload_path, new_payload)
            copied.append(new_payload)
            if _file_sha256(new_payload) != _file_sha256(payload_path):
                problems.append(f'{entry["unit"]}: adopted payload hash does not match the original')
                continue
        adopted.append(dict(kind='adopted-artifact', stage=entry['stage'], arm=entry['arm'],
                            mode=entry.get('mode'), unit=entry['unit'], cap=int(entry['cap']),
                            admitted=int(entry['cap']), sourcePath=str(raw_path),
                            adoptedPath=str(new_raw), sourceSha256=source_hash,
                            payloadPath=(str(payload_path) if payload_path else None),
                            adoptedPayloadPath=(str(new_payload) if new_payload else None),
                            payloadSha256=(_file_sha256(payload_path) if payload_path else None),
                            timings=((payload or {}).get('stages') if payload else None),
                            backends=backends, compute='prior', note=note))
    return adopted, copied, problems


def _matched_gate_from_adopted(old_dir, adopt):
    """The matched-infrastructure gate from the adopted dispatch payloads (no battles)."""
    units = []
    for spec in adopt:
        if spec['stage'] == 'throughput' and spec.get('mode') in MATCHED_MODES:
            units.append(dict(arm=spec['arm'], mode=spec['mode'],
                              throughput=read_json(Path(old_dir) / spec['unit'] / spec['payload'])))
    return matched_infrastructure_comparison(units)


def prepare_carryforward_v4(new_plan, old_dir, out_root):
    """Validate the stopped v4 run and return ``(receipt, adopted_index)``.

    Content-detected by the caller (a prior search/holdout claim), never by directory name. Fails
    closed on any mismatch, never repairs or deletes the original run, never starts a battle, and
    never re-runs or re-nominates the permanently failed e0r1 pair. Adopted artifacts are copied into
    ``out_root`` and hash-bound; the original run is left byte-identical.
    """
    old_dir, out_root = Path(old_dir), Path(out_root)
    for name in ('plan.json', 'battle-ledger.json', 'authorisation-receipt.json'):
        if not (old_dir / name).is_file():
            raise Blocked(f'carryforward-v4 prerequisites failed: prior run is missing {name}')
    if (old_dir / 'results.json').is_file():
        raise Blocked('carryforward-v4 prerequisites failed: prior run already produced results.json; '
                      'this is not an unfinished continuation')
    old_plan = read_json(old_dir / 'plan.json')
    old_ledger = read_json(old_dir / 'battle-ledger.json')
    old_receipt = read_json(old_dir / 'authorisation-receipt.json')

    problems = []
    problems.extend(f'prior plan: {error}' for error in validate_plan(old_plan, check_baseline=False)
                    if 'open items' not in error)
    if (old_receipt.get('kind') != 'encounter-redesign-benchmark-authorisation-1'
            or old_receipt.get('planId') != old_plan.get('planId')
            or old_receipt.get('planDigest') != old_plan.get('digest')
            or old_receipt.get('authorisedBy') != 'root:--authorised'
            or old_receipt.get('scope') != 'run-plan'):
        problems.append('prior authorisation receipt is not bound to the prior plan (root:--authorised)')

    # Only harness-observer + planId-derived fields may differ; the 8 GiB copy limit and every
    # runtime/combat/native/domain/policy/per-unit budget must be byte-identical.
    disallowed = disallowed_plan_differences_v4(old_plan, new_plan)
    if disallowed:
        problems.append('the new plan changes non-declared fields: ' + ', '.join(sorted(disallowed)))
    problems.extend(check_carryforward_v4_ledger(new_plan, old_ledger))

    pairs = [(int(row['id']), replicate) for replicate in range(1, int(new_plan['replicates']) + 1)
             for row in new_plan['encounters'] if row.get('id') is not None]
    failed = tuple(CARRYFORWARD_V4_FAILED_PAIR)
    if len(pairs) != CARRYFORWARD_V4_EXPECTED_PAIRS:
        problems.append(f'the plan declares {len(pairs)} (encounter, replicate) pairs, not the '
                        f'predeclared {CARRYFORWARD_V4_EXPECTED_PAIRS}')
    if failed not in pairs:
        problems.append(f'the predeclared pairs do not contain the failed pair e{failed[0]}r{failed[1]}')
    untouched = [pair for pair in pairs if pair != failed]

    adopted, copied = [], []
    for entry in CARRYFORWARD_V4_ADOPT:
        unit_dir = old_dir / entry['unit']
        raw_path = unit_dir / entry['raw']
        bad = False
        rows = _read_raw_rows(raw_path)
        if len(rows) != int(entry['cap']):
            problems.append(f'{entry["unit"]}: {len(rows)} raw rows is not {entry["cap"]}')
            bad = True
        backends = summarise_backend(rows)
        if backends['native'] != len(rows) or backends['fallback'] or backends['unknown']:
            problems.append(f'{entry["unit"]}: not every adopted row is native: {backends}')
            bad = True
        row_arms = {row.get('arm') for row in rows if row.get('arm') is not None}
        if row_arms and row_arms != {entry['arm']}:
            problems.append(f'{entry["unit"]}: raw rows are labelled {sorted(row_arms)}, not '
                            f'{entry["arm"]!r}')
            bad = True
        payload_path = unit_dir / entry['payload'] if entry.get('payload') else None
        payload = read_json(payload_path) if payload_path is not None else None
        if payload is not None:
            bindings = [('admitted', int(payload.get('admitted') or 0), int(entry['cap'])),
                        ('battles', int(payload.get('battles') or 0), int(entry['cap']))]
            for field, actual, want in bindings:
                if actual != want:
                    problems.append(f'{entry["unit"]}: payload {field} {actual} is not {want}')
                    bad = True
            if payload.get('mode') != entry.get('mode'):
                problems.append(f'{entry["unit"]}: payload mode {payload.get("mode")!r} is not '
                                f'{entry.get("mode")!r}')
                bad = True
            if payload.get('arm') != entry['arm']:
                problems.append(f'{entry["unit"]}: payload arm {payload.get("arm")!r} is not '
                                f'{entry["arm"]!r}')
                bad = True
            if entry.get('persistence') and payload.get('persistence') != entry['persistence']:
                problems.append(f'{entry["unit"]}: payload persistence '
                                f'{payload.get("persistence")!r} is not {entry["persistence"]!r}')
                bad = True
        if bad:
            continue
        new_unit = out_root / entry['unit']
        new_unit.mkdir(parents=True, exist_ok=True)
        new_raw = new_unit / entry['raw']
        shutil.copyfile(raw_path, new_raw)
        copied.append(new_raw)
        source_hash, adopted_hash = _file_sha256(raw_path), _file_sha256(new_raw)
        if source_hash != adopted_hash or not source_hash:
            problems.append(f'{entry["unit"]}: adopted copy hash does not match the original')
            continue
        new_payload = None
        if payload_path is not None:
            new_payload = new_unit / entry['payload']
            shutil.copyfile(payload_path, new_payload)
            copied.append(new_payload)
            if _file_sha256(new_payload) != _file_sha256(payload_path):
                problems.append(f'{entry["unit"]}: adopted payload hash does not match the original')
                continue
        adopted.append(dict(kind='adopted-artifact', stage=entry['stage'], arm=entry['arm'],
                            mode=entry.get('mode'), unit=entry['unit'], cap=int(entry['cap']),
                            admitted=int(entry['cap']), sourcePath=str(raw_path),
                            adoptedPath=str(new_raw), sourceSha256=source_hash,
                            payloadPath=(str(payload_path) if payload_path else None),
                            adoptedPayloadPath=(str(new_payload) if new_payload else None),
                            payloadSha256=(_file_sha256(payload_path) if payload_path else None),
                            timings=((payload or {}).get('stages') if payload else None),
                            backends=backends, compute='prior',
                            note='completed in the stopped v4 run; reused unchanged, charged once'))

    failed_problems, failed_record = _v4_failed_pair(old_dir, old_plan)
    problems.extend(failed_problems)

    dispatch_units = []
    for spec in CARRYFORWARD_V4_ADOPT:
        if spec['stage'] == 'throughput' and spec.get('mode') in MATCHED_MODES:
            dispatch_units.append(dict(arm=spec['arm'], mode=spec['mode'],
                                       throughput=read_json(old_dir / spec['unit'] / spec['payload'])))
    gate = matched_infrastructure_comparison(dispatch_units)
    if not (gate.get('compared') and gate.get('matched') and gate.get('withinThreshold')):
        problems.append(f'the adopted v4 matched throughput does not pass the <=10% same-work gate: '
                        f'{gate}')

    budget = new_plan['budget']['stages']
    search_per_pair = (int(budget['search']['battlesPerArmPerEncounterPerReplicate'])
                       * len(ARMS))
    holdout_per_pair = (int(budget['holdout']['battlesPerArmPerEncounterPerReplicate'])
                        * len(ARMS))
    rerun_search = len(untouched) * search_per_pair
    rerun_holdout = len(untouched) * holdout_per_pair
    ceiling = int(new_plan['budget']['hardCeiling'])
    expected_total = CARRYFORWARD_V4_PRIOR_TOTAL + rerun_search + rerun_holdout
    if expected_total > ceiling:
        problems.append(f'carryforward-v4 total {expected_total} exceeds the hard ceiling {ceiling}')
    if problems:
        for path in copied:
            try:
                path.unlink()
            except OSError:
                pass
        raise Blocked('carryforward-v4 prerequisites failed: ' + '; '.join(problems))

    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    receipt = dict(
        schema=CARRYFORWARD_V4_SCHEMA, createdAt=now(),
        detectedBy='prior ledger already charged a search/holdout stage (content, never a directory '
                   'name)',
        priorPlan=dict(path=str(old_dir / 'plan.json'), planId=old_plan.get('planId'),
                       digest=old_plan.get('digest'), status=old_plan.get('status'),
                       authorisedBy=old_receipt.get('authorisedBy')),
        newPlan=dict(planId=new_plan.get('planId'), digest=new_plan.get('digest'),
                     status=new_plan.get('status')),
        priorAuthorisation=old_receipt,
        priorLedger=dict(path=str(old_dir / 'battle-ledger.json'),
                         ceiling=int(old_ledger.get('ceiling') or 0),
                         spent=int(old_ledger.get('spent') or 0),
                         setupSpent=int(old_ledger.get('setupSpent') or 0),
                         reserved=old_ledger.get('reserved') or {}, settled=settled,
                         nextReservation=old_ledger.get('nextReservation'),
                         reconciledRegular=CARRYFORWARD_V4_PRIOR_REGULAR,
                         reconciledTotal=CARRYFORWARD_V4_PRIOR_TOTAL),
        adopted=adopted, adoptedBattles=CARRYFORWARD_V4_ADOPTED_BATTLES,
        failedPair=failed_record,
        failedSearches=[dict(stage=stage, arm=arm, admitted=int(admitted), compute='prior')
                        for stage, arm, admitted in CARRYFORWARD_V4_FAILED_SEARCHES],
        untouchedPairs=[dict(encounter=pair[0], replicate=pair[1]) for pair in untouched],
        setupCharge=dict(stage='carryforward:throughput:legacy-dispatch',
                         battles=CARRYFORWARD_V4_SETUP_CHARGE, compute='prior',
                         reason='the v3 legacy-dispatch admitted 13 of 64 before the 4 GiB copy '
                                'overflow; carried once as setup so no cost is lost or double-counted'),
        priorRegular=CARRYFORWARD_V4_PRIOR_REGULAR, priorTotal=CARRYFORWARD_V4_PRIOR_TOTAL,
        rerun=dict(pairs=len(untouched), searchBattles=rerun_search, holdoutBattles=rerun_holdout,
                   failedPairsSkipped=1,
                   note='the untouched predeclared pairs run in full; the failed e0r1 pair is skipped '
                        'with no replacement, no re-run and no nomination reconstruction'),
        expectedTotal=expected_total, hardCeiling=ceiling,
        unusedBudget=ceiling - expected_total,
        throughputGate=gate,
        nativeClaim=('the 6 adopted stages are native (validated from their raw rows); the 4 new '
                     'cleanup dispatches of the failed pair have unknown transport backend and are '
                     'NOT claimed native'),
        planDifferences=carryforward_plan_differences(old_plan, new_plan),
        allowedDifferences=list(CARRYFORWARD_V4_ALLOWED_DIFFERENCES),
        allowedPrefixes=list(CARRYFORWARD_V4_ALLOWED_PREFIXES),
        deviations=[
            dict(kind='harness-observer',
                 note='the harness observer revision changed with benchmark_encounter_redesign.py; '
                      'the prior and new plans and every adopted artifact are hash-bound here'),
            dict(kind='plan-id-derived',
                 note='planId/digest/createdAt/status/authorisation and the planId-derived smoke pairs '
                      'and bootstrap seed differ by design; every other field is byte-identical')])
    write_json(out_root / 'carryforward-receipt.json', receipt)
    index = {('smoke', entry['arm']): entry for entry in adopted if entry['stage'] == 'smoke'}
    index.update({('throughput', entry['mode']): entry for entry in adopted
                  if entry['stage'] == 'throughput'})
    return receipt, index


# --------------------------------------------------------------------------------------------------
# v5 continuation (--resume-from a stopped run whose new-arm e15 search was refused unobserved).
#
# Detected by CONTENT (a carried ``carryforward-failed:`` claim in the prior ledger), never by
# directory name. This is the same narrow continuation as v4, extended to TWO permanently failed
# predeclared pairs: the carried e0r1 pair and the newly refused e15r1 pair. Neither is ever
# re-run, re-nominated or replaced; the six completed stages are adopted hash-bound and charged
# once; and only the ten untouched predeclared pairs run. The runtime production change that
# follows is admitted ONLY through an explicit, root-authored reviewed-source-change manifest that
# binds the exact old/new SHA256 of each changed NEW-arm history/seed file; every combat, legacy,
# native, space, threshold, policy, per-unit-budget, baseline and holdout rule must stay identical.
# --------------------------------------------------------------------------------------------------
CARRYFORWARD_V5_SCHEMA = 'encounter-redesign-carryforward-v5-1'
REVIEWED_SOURCE_MANIFEST_SCHEMA = 'encounter-redesign-reviewed-source-change-1'

#: The exact completed v4 artifacts adopted hash-bound (physically re-copied into the v5 run).
CARRYFORWARD_V5_ADOPT = CARRYFORWARD_V4_ADOPT
CARRYFORWARD_V5_SETUP_CHARGE = CARRYFORWARD_V4_SETUP_CHARGE
CARRYFORWARD_V5_ADOPTED_BATTLES = CARRYFORWARD_V4_ADOPTED_BATTLES
#: (ledger stage, arm, exact conservative admitted) for BOTH permanently failed pairs searches.
CARRYFORWARD_V5_FAILED_SEARCHES = (
    ('search:legacy:0:r1', ARM_LEGACY, 128), ('search:new:0:r1', ARM_NEW, 4),
    ('search:legacy:15:r1', ARM_LEGACY, 128), ('search:new:15:r1', ARM_NEW, 0))
CARRYFORWARD_V5_FAILED_PAIRS = ((0, 1), (15, 1))
CARRYFORWARD_V5_NEW_PAIR = (15, 1)
CARRYFORWARD_V5_NEW_PAIR_COUNTS = {ARM_LEGACY: 128, ARM_NEW: 0}
CARRYFORWARD_V5_PRIOR_REGULAR = (CARRYFORWARD_V5_ADOPTED_BATTLES
                                 + sum(int(entry[2]) for entry in CARRYFORWARD_V5_FAILED_SEARCHES))
CARRYFORWARD_V5_PRIOR_TOTAL = CARRYFORWARD_V5_PRIOR_REGULAR + CARRYFORWARD_V5_SETUP_CHARGE
CARRYFORWARD_V5_EXPECTED_PAIRS = 12
CARRYFORWARD_V5_EXPECTED_SETTLES = {
    'carryforward:smoke:legacy': [40], 'carryforward:smoke:new': [40],
    'carryforward:throughput:telemetry-off': [64], 'carryforward:throughput:telemetry-on': [64],
    'carryforward:throughput:legacy-dispatch': [64],
    'carryforward:throughput:new-dispatch': [64],
    'carryforward-failed:search:legacy:0:r1': [128],
    'carryforward-failed:search:new:0:r1': [4],
    'search:legacy:15:r1': [128], 'search:new:15:r1': [0]}
CARRYFORWARD_V5_EXPECTED_RESERVES = {
    stage: [cap] for stage, cap in (
        ('carryforward:smoke:legacy', 40), ('carryforward:smoke:new', 40),
        ('carryforward:throughput:telemetry-off', 64),
        ('carryforward:throughput:telemetry-on', 64),
        ('carryforward:throughput:legacy-dispatch', 64),
        ('carryforward:throughput:new-dispatch', 64),
        ('carryforward-failed:search:legacy:0:r1', 128),
        ('carryforward-failed:search:new:0:r1', 4),
        ('search:legacy:15:r1', 128), ('search:new:15:r1', 128))}
#: The ONLY new-arm source files a v5 continuation may admit as reviewed changes: non-combat
#: history/seed plumbing. Everything else (combat, legacy, native, space, thresholds, policy,
#: per-unit budget, baseline, holdout) is byte-identical or the continuation refuses.
CARRYFORWARD_V5_ALLOWED_SOURCE_FILES = (
    'strategy_seed_freshness.py', 'strategy_encounter_search.py',
    'strategy_legacy_observations.py', 'strategy_history_summary.py')
#: Runtime revision inventory glue: a plan-level file that is BOTH an independent implementation source
#: AND one of the adapter's own canonical provenance sources, so its content hash is mirrored into the
#: plan in several derived places. Root extends this explicitly (currently ``strategy_optimizer.py``,
#: whose only reviewed change is listing the newly shipped ``strategy_history_summary.py`` in its
#: ``_loaded_revision`` inventory); an arbitrary new file can never be smuggled in, and a glue entry is
#: admitted only through the strict mirror/derived-digest binding in ``_reviewed_source_glue_problems``.
CARRYFORWARD_V5_ALLOWED_SOURCE_GLUE = ('strategy_optimizer.py',)
#: The provenance digests the adapter's own ``provenance`` can emit, one per real runtime data mode.
#: A reviewed glue change must reproduce the stored digest under one of these; an unreproduced digest
#: is refused rather than accepted as a mirror.
PROVENANCE_DIGEST_MODES = ('package', 'recovery-workspace')


def _provenance_digest_candidates(files):
    """Every digest the adapter's ``provenance`` algorithm can emit for one frozen file reading.

    Mirrors ``strategy_optimizer_adapter.provenance``: the digest is ``sha256`` over the canonical
    JSON of ``{files: {label: sha256}, count: len(files), missing: sorted(missing), mode: <mode>}`` for
    the runtime data mode the arm actually ran in.
    """
    body = dict(files=dict(files or {}), count=len(files or {}), missing=[])
    return {digest(dict(body, mode=mode)) for mode in PROVENANCE_DIGEST_MODES}


def _reviewed_source_glue_problems(old_plan, new_plan, rel, old_hash, new_hash):
    """Verify one signed runtime-inventory glue change; return ``(problems, covered_paths)``.

    ``strategy_optimizer.py`` is special: it is an independent implementation source AND one of the
    adapter's canonical provenance sources, so its hash is mirrored into the new arm as
    ``implementationInventory.files.impl/<rel>``, ``implementationInventory.files.canonical/tools/
    recovery/<rel>`` AND ``sourceInventory.files.tools/recovery/<rel>``, and the whole ``sourceInventory``
    digest (also recorded as ``policy.revision``/``observedPolicy.revision``) is DERIVED from it. A glue
    edit is admitted only when every mirror equals the signed impl hash on BOTH plans, no other
    canonical provenance file changed, the old/new digests reproduce the adapter's own algorithm, and
    the policy records differ only in that derived revision. The returned ``covered_paths`` are exactly
    those mathematically bound mirrors; the manifest never gains authority over combat behaviour.
    """
    problems, covered = [], set()

    def _impl_files(plan):
        return (((plan.get('arms') or {}).get(ARM_NEW) or {}).get('implementationInventory')
                or {}).get('files') or {}

    def _source(plan):
        return ((plan.get('arms') or {}).get(ARM_NEW) or {}).get('sourceInventory') or {}

    impl_label = 'impl/' + rel
    canonical_label = 'canonical/tools/recovery/' + rel
    provenance_label = 'tools/recovery/' + rel
    old_impl, new_impl = _impl_files(old_plan), _impl_files(new_plan)
    old_src, new_src = _source(old_plan), _source(new_plan)
    old_src_files, new_src_files = old_src.get('files') or {}, new_src.get('files') or {}

    for label, old_mirror, new_mirror in ((impl_label, old_impl.get(impl_label),
                                           new_impl.get(impl_label)),
                                          (canonical_label, old_impl.get(canonical_label),
                                           new_impl.get(canonical_label))):
        if old_mirror != old_hash or new_mirror != new_hash:
            problems.append(f'reviewed-source glue {rel!r} is not bound to its {label} mirror on both '
                            'plans')
    if (old_src_files.get(provenance_label) != old_hash
            or new_src_files.get(provenance_label) != new_hash):
        problems.append(f'reviewed-source glue {rel!r} is not bound to its sourceInventory file on '
                        'both plans')
    changed = {name for name in set(old_src_files) | set(new_src_files)
               if old_src_files.get(name) != new_src_files.get(name)}
    if changed != {provenance_label}:
        problems.append(f'reviewed-source glue {rel!r} changed canonical provenance files '
                        f'{sorted(changed)}; only the signed glue file may change')
    for side, src in (('old', old_src), ('new', new_src)):
        files = src.get('files') or {}
        if src.get('count') != len(files):
            problems.append(f'the {side} provenance file count is not the adapter reading')
        if src.get('digest') not in _provenance_digest_candidates(files):
            problems.append(f'the {side} provenance digest does not reproduce the adapter algorithm')
    derived = {f'arms.{ARM_NEW}.implementationInventory.files.{impl_label}',
               f'arms.{ARM_NEW}.implementationInventory.files.{canonical_label}',
               f'arms.{ARM_NEW}.sourceInventory.files.{provenance_label}',
               f'arms.{ARM_NEW}.sourceInventory.digest'}
    for section in ('policy', 'observedPolicy'):
        old_record = ((old_plan.get('arms') or {}).get(ARM_NEW) or {}).get(section) or {}
        new_record = ((new_plan.get('arms') or {}).get(ARM_NEW) or {}).get(section) or {}
        if ({k: v for k, v in old_record.items() if k != 'revision'}
                != {k: v for k, v in new_record.items() if k != 'revision'}):
            problems.append(f'reviewed-source glue {rel!r} changed {section} fields other than the '
                            'derived revision')
        if 'revision' in old_record or 'revision' in new_record:
            if (old_record.get('revision') != old_src.get('digest')
                    or new_record.get('revision') != new_src.get('digest')):
                problems.append(f'the {section} revision is not the derived provenance digest')
            derived.add(f'arms.{ARM_NEW}.{section}.revision')
    if not problems:
        covered |= derived
    return problems, covered


def _carryforward_failed_records(carryforward):
    """Both the historical singular ``failedPair`` and the plural ``failedPairs``, in order."""
    records = list((carryforward or {}).get('failedPairs') or [])
    single = (carryforward or {}).get('failedPair')
    if single:
        records.append(single)
    return records


def _prior_is_v5_continuation(ledger):
    """True when the prior ledger was produced by a v4 continuation (it carries failed-pair costs)."""
    return any(str(claim.get('stage') or '').startswith('carryforward-failed:')
               for claim in (ledger or {}).get('claims') or [])


def _resolve_referenced_path(path):
    """Resolve a stored artifact path: absolute, repo-relative, or cwd-relative; None if absent."""
    path = Path(path)
    candidates = [path] if path.is_absolute() else [REPO / path, Path.cwd() / path, path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _conservative_admitted(counts, cap):
    """The conservative MAX over every durable source; None when nothing is recoverable."""
    observed = [int(value) for value in counts
                if isinstance(value, int) and not isinstance(value, bool)]
    if not observed:
        return None
    high = max(observed)
    if high > int(cap):
        raise Blocked(f'recovered admitted count {high} exceeds the declared cap {cap}; refusing to '
                      'charge an out-of-contract count')
    return max(0, high)


def check_reviewed_source_changes(old_plan, new_plan, manifest):
    """Fail-closed reconciliation of the ONLY allowed new-arm source changes.

    Returns ``(problems, changes)``. The manifest must be bound to the prior plan digest, must name
    only NEW-arm non-combat history/seed files, and must EXACTLY match the changed/added/removed
    ``impl/*`` files of the new arm implementation inventory with the exact old/new SHA256 the two
    plans carry. A changed file that is not declared, a declared file that did not change, a hash
    that does not match, or any non-inventory plan difference all fail closed.
    """
    problems = []
    manifest = manifest if isinstance(manifest, dict) else {}
    if manifest.get('schema') != REVIEWED_SOURCE_MANIFEST_SCHEMA:
        problems.append('reviewed-source manifest schema mismatch')
    if manifest.get('arm') != ARM_NEW:
        problems.append('reviewed-source manifest must describe the new arm only')
    if manifest.get('priorPlanDigest') != old_plan.get('digest'):
        problems.append('reviewed-source manifest is not bound to the prior plan digest')
    entries = manifest.get('changes')
    if not isinstance(entries, list):
        problems.append('reviewed-source manifest has no changes list')
        return problems, {}
    allowed = set(CARRYFORWARD_V5_ALLOWED_SOURCE_FILES) | set(CARRYFORWARD_V5_ALLOWED_SOURCE_GLUE)
    declared = {}
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append('reviewed-source manifest contains a non-object change')
            continue
        rel = str(entry.get('path') or '')
        if not rel or not str(entry.get('rationale') or '').strip():
            problems.append(f'reviewed-source change {rel!r} needs a path and a rationale')
        if rel not in allowed:
            problems.append(f'reviewed-source change {rel!r} is not allowed non-combat history/seed '
                            'plumbing')
        if rel in declared:
            problems.append(f'reviewed-source change {rel!r} is declared twice')
        declared[rel] = dict(oldSha256=entry.get('oldSha256'), newSha256=entry.get('newSha256'),
                             rationale=entry.get('rationale'))
    old_files = ((old_plan.get('arms') or {}).get(ARM_NEW) or {}).get(
        'implementationInventory', {}).get('files') or {}
    new_files = ((new_plan.get('arms') or {}).get(ARM_NEW) or {}).get(
        'implementationInventory', {}).get('files') or {}
    actual = {}
    for label in sorted(set(old_files) | set(new_files)):
        if not label.startswith('impl/'):
            continue
        if old_files.get(label) != new_files.get(label):
            actual[label[len('impl/'):]] = dict(oldSha256=old_files.get(label),
                                                newSha256=new_files.get(label))
    if set(actual) != set(declared):
        problems.append('the reviewed-source manifest does not exactly match the new-arm source '
                        f'changes: declared {sorted(declared)} vs changed {sorted(actual)}')
    for rel in sorted(set(actual) & set(declared)):
        if (declared[rel]['oldSha256'] != actual[rel]['oldSha256']
                or declared[rel]['newSha256'] != actual[rel]['newSha256']):
            problems.append(f'reviewed-source change {rel!r} hashes do not match the two plans')
    covered = {f'arms.{ARM_NEW}.implementationInventory.files.impl/{rel}' for rel in declared}
    # A signed runtime-inventory glue entry covers a wider set of mathematically bound mirrors (its
    # canonical provenance file, the sourceInventory file + derived digest, and the derived policy
    # revision). Only add those mirrors to ``covered`` once the strict binding check passes, so a bad
    # mirror or a smuggled extra change still surfaces as a non-declared field.
    for rel in sorted(declared):
        if rel in CARRYFORWARD_V5_ALLOWED_SOURCE_GLUE:
            glue_problems, glue_paths = _reviewed_source_glue_problems(
                old_plan, new_plan, rel, declared[rel]['oldSha256'], declared[rel]['newSha256'])
            problems.extend(glue_problems)
            covered |= glue_paths
    remaining = {path: value for path, value in disallowed_plan_differences_v4(old_plan, new_plan).items()
                 if path not in covered}
    if remaining:
        problems.append('the new plan changes non-declared fields: ' + ', '.join(sorted(remaining)))
    changes = [dict(path=rel, oldSha256=declared[rel]['oldSha256'],
                    newSha256=declared[rel]['newSha256'], rationale=declared[rel]['rationale'])
               for rel in sorted(declared)]
    return problems, changes


def check_carryforward_v5_ledger(new_plan, old_ledger):
    """Fail-closed checks on the stopped v5 ledger; returns the list of problems (empty is OK).

    Requires the EXACT persisted shape and nothing more: the six adopted-v4 stages settled
    40/40/64/64/64/64, the two carried e0r1 failed searches settled 128/4, the completed legacy
    e15r1 search settled 128, the refused new e15r1 search settled 0, the 13 carried setup charge,
    no outstanding reservation, no under-budget and no claim beyond that set. A prior extra claim is
    an unexpected cost and is refused rather than re-charged.
    """
    problems = []
    if old_ledger.get('schema') != LEDGER_SCHEMA:
        problems.append('prior ledger schema mismatch')
    budget = new_plan.get('budget') or {}
    if int(old_ledger.get('ceiling') or 0) != int(budget.get('hardCeiling') or 0):
        problems.append('prior ledger ceiling does not match the new plan hard ceiling')
    if old_ledger.get('reserved'):
        problems.append('prior ledger still has outstanding reservations')
    if int(old_ledger.get('setupSpent') or 0) != CARRYFORWARD_V5_SETUP_CHARGE:
        problems.append(f'prior ledger setup charge {old_ledger.get("setupSpent")} is not '
                        f'{CARRYFORWARD_V5_SETUP_CHARGE}')
    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    expected_settles = CARRYFORWARD_V5_EXPECTED_SETTLES
    if settled != expected_settles:
        problems.append(f'prior ledger settlement breakdown {settled} is not the expected v5 shape '
                        f'{expected_settles}')
    else:
        expected_spent = sum(values[0] for values in expected_settles.values())
        if int(old_ledger.get('spent') or 0) != expected_spent:
            problems.append(f'prior ledger spent {old_ledger.get("spent")} is not the expected v5 '
                            f'total {expected_spent}')
    setups = [int(claim.get('count') or 0) for claim in old_ledger.get('claims') or []
              if claim.get('op') == 'setup']
    if setups != [CARRYFORWARD_V5_SETUP_CHARGE]:
        problems.append(f'prior ledger setup claims {setups} are not one '
                        f'{CARRYFORWARD_V5_SETUP_CHARGE}-battle charge')
    reserves = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'reserve':
            reserves.setdefault(str(claim.get('stage')), []).append(int(claim.get('cap') or 0))
    if reserves != CARRYFORWARD_V5_EXPECTED_RESERVES:
        problems.append(f'prior ledger reserved stages {sorted(reserves)} are not the expected v5 '
                        'reservation set')
    if int(old_ledger.get('spent') or 0) < CARRYFORWARD_V5_PRIOR_REGULAR:
        problems.append('the prior v5 ledger is under-budget; refusing to continue')
    return problems


def _verify_carried_failed_pair(record):
    """Re-verify a carried failed-pair record; the prior continuation receipt is hash-bound."""
    if not isinstance(record, dict) or record.get('status') != 'permanently-failed-inconclusive':
        return ['the carried failed-pair record is missing or not permanently-failed-inconclusive']
    problems = []
    for arm, key in ((ARM_LEGACY, 'legacyReport'), (ARM_NEW, 'newReport')):
        ref = record.get(key) or {}
        stored = str(ref.get('path') or '')
        if not stored or not ref.get('sha256'):
            problems.append(f'the carried failed pair has no {arm} report binding')
            continue
        resolved = _resolve_referenced_path(stored)
        if resolved is None:
            problems.append(f'the carried failed pair {arm} report {stored} is not resolvable')
            continue
        if _file_sha256(resolved) != ref.get('sha256'):
            problems.append(f'the carried failed pair {arm} report hash no longer matches')
    return problems


def _v5_carried_failed_pair(prior_receipt):
    record = (prior_receipt or {}).get('failedPair')
    problems = _verify_carried_failed_pair(record)
    return problems, (record if not problems else None)


def _v5_failed_pair(old_dir):
    """The newly failed e15r1 pair derived from the stopped v5 artifacts (conservative MAX).

    The legacy arm completed its full 128-battle native search; the new arm recorded ZERO transport
    completions (the coordinator reported the focused search idle with an unspendable budget) and is
    therefore INCONCLUSIVE. The pair is never re-run, re-nominated or replaced, and its real cost is
    charged exactly once.
    """
    encounter, replicate = CARRYFORWARD_V5_NEW_PAIR
    old_dir = Path(old_dir)
    problems = []
    units = {arm: old_dir / f'{arm}-e{encounter}-r{replicate}' for arm in ARMS}
    reports = {arm: units[arm] / f'search-{arm}-e{encounter}-r{replicate}.json' for arm in ARMS}
    meters = {arm: units[arm] / f'meter-search-e{encounter}-{arm}.json' for arm in ARMS}
    transports = {arm: units[arm] / f'transport-search-e{encounter}-{arm}.json' for arm in ARMS}
    payloads, transport = {}, {}
    for arm in ARMS:
        if not reports[arm].is_file():
            problems.append(f'the failed pair is missing the {arm} search report {reports[arm].name}')
            continue
        payloads[arm] = read_json(reports[arm])
        if not isinstance(payloads[arm], dict):
            problems.append(f'the failed pair {arm} search report is malformed')
        if transports[arm].is_file():
            transport[arm] = read_json(transports[arm]) or {}
        for leftover in units[arm].glob('holdout-*'):
            problems.append(f'the failed pair already has a holdout artifact {leftover.name}; '
                            'refusing to re-run or reconstruct a nomination')
    if problems:
        return problems, None
    counts = {}
    for arm in ARMS:
        observed = [_admitted_from_file(meters[arm]), _admitted_from_file(reports[arm])]
        for key in ('completed', 'native', 'fallback', 'unknown'):
            value = (transport.get(arm) or {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                observed.append(value)
        counts[arm] = _conservative_admitted(observed, 128)
    for arm in ARMS:
        if counts[arm] != CARRYFORWARD_V5_NEW_PAIR_COUNTS[arm]:
            problems.append(f'the failed {arm} e{encounter}r{replicate} conservative MAX admitted '
                            f'{counts[arm]}, not {CARRYFORWARD_V5_NEW_PAIR_COUNTS[arm]}')
    if int(payloads[ARM_LEGACY].get('declared') or 0) != 128:
        problems.append('the failed legacy search did not declare the full 128-battle budget')
    if not payloads[ARM_NEW].get('incomplete'):
        problems.append('the refused new search is not marked incomplete; refusing to call it'
                        ' inconclusive')
    new_transport = transport.get(ARM_NEW) or {}
    if int(new_transport.get('completed') or 0) or int(new_transport.get('native') or 0):
        problems.append('the refused new e15 search recorded transport completions; refusing to '
                        'treat it as unobserved')
    legacy_transport = transport.get(ARM_LEGACY) or {}
    if int(legacy_transport.get('native') or 0) != 128 or int(legacy_transport.get('fallback') or 0):
        problems.append(f'the completed legacy e15 search is not 128 native: {legacy_transport}')
    if problems:
        return problems, None

    def _cost(arm):
        cost = payloads[arm].get('observerCost') or {}
        worker = cost.get('workerCpuSeconds')
        return dict(parentCpuSeconds=cost.get('parentCpuSeconds'),
                    wallSeconds=cost.get('wallSeconds'),
                    stages=cost.get('stages'),
                    workerCpuSeconds=(worker if isinstance(worker, (int, float))
                                      and not isinstance(worker, bool) else None),
                    workerCpuNote=cost.get('workerCpuNote'))

    child_path = units[ARM_NEW] / 'child-search-new.json'
    nomination = payloads[ARM_NEW].get('nomination') or {}
    record = dict(
        encounter=int(encounter), replicate=int(replicate),
        status='permanently-failed-inconclusive',
        legacyAdmitted=counts[ARM_LEGACY], newAdmitted=counts[ARM_NEW], holdoutBattles=0,
        replacement=False, nominationReconstructed=False, newSearchEvidence=0,
        transport={arm: transport.get(arm) for arm in ARMS},
        child=read_json(child_path) if child_path.is_file() else None,
        observerCost={arm: _cost(arm) for arm in ARMS},
        nominee=nomination.get('nominee'),
        nomineeNote='a nominee value is present from the historical portfolio, but it was produced '
                    'with ZERO new search evidence and NO holdout, so the pair stays inconclusive '
                    'and is never re-nominated, re-run or replaced',
        nativeStatus='the completed legacy arm is native (128/128); the refused new arm recorded '
                     'zero transport completions, so no new-arm native claim is made',
        legacyReport=dict(path=str(reports[ARM_LEGACY]), sha256=_file_sha256(reports[ARM_LEGACY])),
        newReport=dict(path=str(reports[ARM_NEW]), sha256=_file_sha256(reports[ARM_NEW])),
        note='charged exactly once as prior compute; the pair is never re-run or re-nominated')
    return [], record


def prepare_carryforward_v5(new_plan, old_dir, out_root, *, reviewed_source_manifest=None):
    """Validate the stopped v5 run and return ``(receipt, adopted_index)``.

    Content-detected by the caller (a carried ``carryforward-failed:`` claim), never by directory
    name. Fails closed on any mismatch, never repairs or deletes the original run, never starts a
    battle, and never re-runs or re-nominates the two permanently failed (e0r1, e15r1) pairs.
    """
    old_dir, out_root = Path(old_dir), Path(out_root)
    for name in ('plan.json', 'battle-ledger.json', 'authorisation-receipt.json',
                 'carryforward-receipt.json'):
        if not (old_dir / name).is_file():
            raise Blocked(f'carryforward-v5 prerequisites failed: prior run is missing {name}')
    if (old_dir / 'results.json').is_file():
        raise Blocked('carryforward-v5 prerequisites failed: prior run already produced results.json; '
                      'this is not an unfinished continuation')
    old_plan = read_json(old_dir / 'plan.json')
    old_ledger = read_json(old_dir / 'battle-ledger.json')
    old_receipt = read_json(old_dir / 'authorisation-receipt.json')
    prior_receipt = read_json(old_dir / 'carryforward-receipt.json')

    problems = []
    problems.extend(f'prior plan: {error}' for error in validate_plan(old_plan, check_baseline=False)
                    if 'open items' not in error)
    if (old_receipt.get('kind') != 'encounter-redesign-benchmark-authorisation-1'
            or old_receipt.get('planId') != old_plan.get('planId')
            or old_receipt.get('planDigest') != old_plan.get('digest')
            or old_receipt.get('authorisedBy') != 'root:--authorised'
            or old_receipt.get('scope') != 'run-plan'):
        problems.append('prior authorisation receipt is not bound to the prior plan (root:--authorised)')
    if prior_receipt.get('schema') not in (CARRYFORWARD_V4_SCHEMA, CARRYFORWARD_V5_SCHEMA):
        problems.append('prior carryforward receipt is not a v4/v5 continuation receipt')

    if reviewed_source_manifest is None:
        problems.append('a v5 continuation requires the reviewed-source-change manifest')
    manifest_problems, manifest_changes = check_reviewed_source_changes(
        old_plan, new_plan, reviewed_source_manifest)
    problems.extend(manifest_problems)
    problems.extend(check_carryforward_v5_ledger(new_plan, old_ledger))

    pairs = [(int(row['id']), replicate) for replicate in range(1, int(new_plan['replicates']) + 1)
             for row in new_plan['encounters'] if row.get('id') is not None]
    failed = tuple(CARRYFORWARD_V5_FAILED_PAIRS)
    if len(pairs) != CARRYFORWARD_V5_EXPECTED_PAIRS:
        problems.append(f'the plan declares {len(pairs)} (encounter, replicate) pairs, not the '
                        f'predeclared {CARRYFORWARD_V5_EXPECTED_PAIRS}')
    for pair in failed:
        if pair not in pairs:
            problems.append(f'the predeclared pairs do not contain the failed pair e{pair[0]}r{pair[1]}')
    untouched = [pair for pair in pairs if pair not in failed]

    adopted, copied, adopt_problems = _adopt_completed_units(
        old_dir, out_root, CARRYFORWARD_V5_ADOPT,
        note='completed in the stopped v5 run; reused unchanged, charged once')
    problems.extend(adopt_problems)

    carried_problems, carried_record = _v5_carried_failed_pair(prior_receipt)
    problems.extend(carried_problems)
    new_problems, new_record = _v5_failed_pair(old_dir)
    problems.extend(new_problems)

    gate = _matched_gate_from_adopted(old_dir, CARRYFORWARD_V5_ADOPT)
    if not (gate.get('compared') and gate.get('matched') and gate.get('withinThreshold')):
        problems.append(f'the adopted matched throughput does not pass the <=10% same-work gate: '
                        f'{gate}')

    budget = new_plan['budget']['stages']
    search_per_pair = int(budget['search']['battlesPerArmPerEncounterPerReplicate']) * len(ARMS)
    holdout_per_pair = int(budget['holdout']['battlesPerArmPerEncounterPerReplicate']) * len(ARMS)
    rerun_search = len(untouched) * search_per_pair
    rerun_holdout = len(untouched) * holdout_per_pair
    ceiling = int(new_plan['budget']['hardCeiling'])
    expected_total = CARRYFORWARD_V5_PRIOR_TOTAL + rerun_search + rerun_holdout
    if expected_total > ceiling:
        problems.append(f'carryforward-v5 total {expected_total} exceeds the hard ceiling {ceiling}')
    if expected_total < CARRYFORWARD_V5_PRIOR_TOTAL:
        problems.append('carryforward-v5 total is below the carried prior cost; refusing to under-run')
    if problems:
        for path in copied:
            try:
                path.unlink()
            except OSError:
                pass
        raise Blocked('carryforward-v5 prerequisites failed: ' + '; '.join(problems))

    settled = {}
    for claim in old_ledger.get('claims') or []:
        if claim.get('op') == 'settle':
            settled.setdefault(str(claim.get('stage')), []).append(int(claim.get('actual') or 0))
    receipt = dict(
        schema=CARRYFORWARD_V5_SCHEMA, createdAt=now(),
        detectedBy='prior ledger already carried a failed-pair charge (content, never a directory name)',
        priorPlan=dict(path=str(old_dir / 'plan.json'), planId=old_plan.get('planId'),
                       digest=old_plan.get('digest'), status=old_plan.get('status'),
                       authorisedBy=old_receipt.get('authorisedBy')),
        newPlan=dict(planId=new_plan.get('planId'), digest=new_plan.get('digest'),
                     status=new_plan.get('status')),
        priorAuthorisation=old_receipt,
        priorCarryforward=dict(path=str(old_dir / 'carryforward-receipt.json'),
                               schema=prior_receipt.get('schema'),
                               priorTotal=prior_receipt.get('priorTotal')),
        priorLedger=dict(path=str(old_dir / 'battle-ledger.json'),
                         ceiling=int(old_ledger.get('ceiling') or 0),
                         spent=int(old_ledger.get('spent') or 0),
                         setupSpent=int(old_ledger.get('setupSpent') or 0),
                         reserved=old_ledger.get('reserved') or {}, settled=settled,
                         nextReservation=old_ledger.get('nextReservation'),
                         reconciledRegular=CARRYFORWARD_V5_PRIOR_REGULAR,
                         reconciledTotal=CARRYFORWARD_V5_PRIOR_TOTAL),
        adopted=adopted, adoptedBattles=CARRYFORWARD_V5_ADOPTED_BATTLES,
        failedPairs=[entry for entry in (carried_record, new_record) if entry],
        failedSearches=[dict(stage=stage, arm=arm, admitted=int(admitted), compute='prior')
                        for stage, arm, admitted in CARRYFORWARD_V5_FAILED_SEARCHES],
        untouchedPairs=[dict(encounter=pair[0], replicate=pair[1]) for pair in untouched],
        reviewedSourceChanges=manifest_changes,
        reviewedSourceManifestDigest=(digest(reviewed_source_manifest)
                                      if reviewed_source_manifest else None),
        setupCharge=dict(stage='carryforward:throughput:legacy-dispatch',
                         battles=CARRYFORWARD_V5_SETUP_CHARGE, compute='prior',
                         reason='the v3 legacy-dispatch 13-battle setup charge is carried once, '
                                'unchanged from the v4 continuation'),
        priorRegular=CARRYFORWARD_V5_PRIOR_REGULAR, priorTotal=CARRYFORWARD_V5_PRIOR_TOTAL,
        rerun=dict(pairs=len(untouched), searchBattles=rerun_search, holdoutBattles=rerun_holdout,
                   failedPairsSkipped=len(failed),
                   note='the ten untouched predeclared pairs run in full; the failed e0r1 and e15r1 '
                        'pairs are skipped with no replacement, no re-run and no nomination '
                        'reconstruction'),
        expectedTotal=expected_total, hardCeiling=ceiling,
        unusedBudget=ceiling - expected_total,
        throughputGate=gate,
        nativeClaim=('the 6 adopted stages are native (validated from their raw rows); the completed '
                     'legacy e15r1 search is native (128/128); the refused new e15r1 search recorded '
                     'ZERO transport completions, so no new-arm native claim is made and the pair '
                     'stays inconclusive'),
        planDifferences=carryforward_plan_differences(old_plan, new_plan),
        allowedDifferences=list(CARRYFORWARD_V4_ALLOWED_DIFFERENCES),
        allowedPrefixes=list(CARRYFORWARD_V4_ALLOWED_PREFIXES),
        deviations=[
            dict(kind='harness-observer',
                 note='the harness observer revision changed with benchmark_encounter_redesign.py; '
                      'the prior and new plans and every adopted artifact are hash-bound here'),
            dict(kind='plan-id-derived',
                 note='planId/digest/createdAt/status/authorisation and the planId-derived smoke '
                      'pairs and bootstrap seed differ by design; every other field is byte-identical '
                      'except the reviewed-source-change manifest entries'),
            dict(kind='reviewed-source-change',
                 note='only NEW-arm non-combat history/seed plumbing may differ, and only through the '
                      'root-authored manifest whose exact old/new SHA256 are recorded here')])
    write_json(out_root / 'carryforward-receipt.json', receipt)
    index = {('smoke', entry['arm']): entry for entry in adopted if entry['stage'] == 'smoke'}
    index.update({('throughput', entry['mode']): entry for entry in adopted
                  if entry['stage'] == 'throughput'})
    return receipt, index


def prepare_carryforward(new_plan, old_dir, out_root, *, reviewed_source_manifest=None):
    """Content-detected continuation dispatcher: early-recovery, v4, or the v5 continuation.

    The branch is chosen from the prior LEDGER (a search/holdout claim means a continuation; a
    carried ``carryforward-failed:`` claim additionally means v5), never from the directory name.
    All branches fail closed, never re-run or delete the original run, and never start a battle.
    """
    old_dir = Path(old_dir)
    for name in ('plan.json', 'battle-ledger.json', 'authorisation-receipt.json'):
        if not (old_dir / name).is_file():
            raise Blocked(f'carryforward prerequisites failed: prior run is missing {name}')
    old_ledger = read_json(old_dir / 'battle-ledger.json')
    if _prior_has_search_claims(old_ledger):
        if _prior_is_v5_continuation(old_ledger):
            return prepare_carryforward_v5(new_plan, old_dir, out_root,
                                           reviewed_source_manifest=reviewed_source_manifest)
        return prepare_carryforward_v4(new_plan, old_dir, out_root)
    return _prepare_carryforward_early(new_plan, old_dir, out_root)


#: The stopped current-run resume (``comparison-v6``): both e18r1 searches completed and were charged
#: once, exactly zero holdout battles were admitted, and the nine untouched predeclared pairs remain.
#: These constants are the fail-closed prior accounting; they are verified against the ledger, never
#: assumed, and the resulting final total must be 4449 (<= the 4976 ceiling).
CURRENT_RESUME_SCHEMAS = (CARRYFORWARD_V4_SCHEMA, CARRYFORWARD_V5_SCHEMA)
CURRENT_RESUME_FAILED_PAIRS = ((0, 1), (15, 1))
CURRENT_RESUME_PRIOR_REGULAR = 852
CURRENT_RESUME_PRIOR_SETUP = 13
CURRENT_RESUME_PRIOR_TOTAL = CURRENT_RESUME_PRIOR_REGULAR + CURRENT_RESUME_PRIOR_SETUP
CURRENT_RESUME_EXPECTED_FINAL = 4449


def _current_resume_failed_pairs(receipt):
    """The permanently failed pairs the stopped run already excluded and charged (never re-run)."""
    return tuple(sorted((int(record['encounter']), int(record['replicate']))
                        for record in (receipt.get('failedPairs') or [])))


def check_current_resume_state(plan, out_root, ledger, receipt):
    """Fail-closed validation of a stopped SAME-PLAN run before a resume; returns a problem list.

    Verifies the SAME plan is on disk, no results.json exists, the authorisation and carryforward
    receipts are bound to this exact planId+digest, the prior ledger is exactly 852 regular + 13
    setup with nothing outstanding, and the permanently failed pairs are exactly the predeclared two.
    It then aborts on any holdout outcome or admission already on disk (an unknown/admitted partial
    holdout must never be repeated), while accepting the failed-holdout child payload of an attempt
    that admitted zero pairs.
    """
    out_root = Path(out_root)
    problems = []
    plan_disk = read_json(out_root / 'plan.json') if (out_root / 'plan.json').is_file() else {}
    if plan_disk.get('planId') != plan.get('planId') or plan_disk.get('digest') != plan.get('digest'):
        problems.append('the run directory plan.json is not the plan being resumed')
    if (out_root / 'results.json').is_file():
        problems.append('the run already produced results.json; it is not an unfinished run')
    auth_path = out_root / 'authorisation-receipt.json'
    auth = read_json(auth_path) if auth_path.is_file() else {}
    if (auth.get('kind') != 'encounter-redesign-benchmark-authorisation-1'
            or auth.get('planId') != plan.get('planId')
            or auth.get('planDigest') != plan.get('digest')
            or auth.get('authorisedBy') != 'root:--authorised'):
        problems.append('the authorisation receipt is not bound to this plan (root:--authorised)')
    if receipt.get('schema') not in CURRENT_RESUME_SCHEMAS:
        problems.append('the prior carryforward receipt is not a v4/v5 continuation receipt')
    new_plan = receipt.get('newPlan') or {}
    if new_plan.get('planId') != plan.get('planId') or new_plan.get('digest') != plan.get('digest'):
        problems.append('the prior carryforward receipt is not bound to this exact plan')
    if int(ledger.spent) != CURRENT_RESUME_PRIOR_REGULAR \
            or int(ledger.setup_spent) != CURRENT_RESUME_PRIOR_SETUP:
        problems.append(f'the prior ledger is {ledger.spent} regular + {ledger.setup_spent} setup, not '
                        f'{CURRENT_RESUME_PRIOR_REGULAR}+{CURRENT_RESUME_PRIOR_SETUP}')
    if ledger.reserved:
        problems.append(f'the prior ledger has {len(ledger.reserved)} outstanding reservation(s); '
                        'refusing to resume a partially settled stage')
    failed = _current_resume_failed_pairs(receipt)
    if failed != CURRENT_RESUME_FAILED_PAIRS:
        problems.append(f'the prior receipt excludes {failed}, not the predeclared '
                        f'{CURRENT_RESUME_FAILED_PAIRS}')
    failed_set = set(failed)
    pairs = [(int(row['id']), replicate)
             for replicate in range(1, int(plan['replicates']) + 1)
             for row in plan['encounters'] if row.get('id') is not None]
    for encounter_id, replicate in pairs:
        if (encounter_id, replicate) in failed_set:
            continue
        for arm in ARMS:
            unit_dir = out_root / f'{arm}-e{encounter_id}-r{replicate}'
            if not unit_dir.is_dir():
                continue
            for pattern in ('holdout-*.json', 'outcome-holdout-*.json', 'raw-holdout-*.jsonl'):
                for path in sorted(unit_dir.glob(pattern)):
                    problems.append(f'a holdout artifact already exists ({path.name}); refusing a '
                                    'resume that could repeat an admitted holdout')
            meter_path = unit_dir / f'meter-holdout-e{encounter_id}-{arm}.json'
            if meter_path.is_file():
                meter = read_json(meter_path) or {}
                if int(meter.get('admitted') or 0) or int(meter.get('usedPairs') or 0):
                    problems.append(f'{meter_path.name} records an admitted holdout '
                                    f'({meter.get("admitted")} admitted / {meter.get("usedPairs")} '
                                    'used); refusing a hidden repetition')
    return problems


def _adopt_current_search(unit_dir, arm, encounter_id, replicate, ledger):
    """Adopt a persisted, complete search report for a same-plan resume, or return None.

    Adoption requires BOTH that the ledger already settled this exact search stage (so the prior
    compute is charged once and never re-granted) and that the persisted report is the full completed
    search payload. A settled stage with a missing, failed, underexecuted or nominee-less report
    fails closed instead of silently re-running a completed search.
    """
    stage = f'search:{arm}:{encounter_id}:r{replicate}'
    if stage not in ledger.settled_stages():
        return None
    path = Path(unit_dir) / f'search-{arm}-e{encounter_id}-r{replicate}.json'
    if not path.is_file():
        raise Blocked(f'resume-current: {stage} is charged in the ledger but its report {path.name} is '
                      'missing; refusing to re-run a completed search')
    report = read_json(path)
    if not isinstance(report, dict):
        raise Blocked(f'resume-current: malformed persisted search report {path.name}')
    if report.get('failed') or report.get('incomplete'):
        raise Blocked(f'resume-current: {stage} report is failed/incomplete; refusing to re-run or '
                      'reuse it')
    if report.get('admitted') is None or report.get('declared') is None \
            or int(report['admitted']) != int(report['declared']):
        raise Blocked(f'resume-current: {stage} report admitted {report.get("admitted")} of '
                      f'{report.get("declared")}; refusing an underexecuted reuse')
    if not (report.get('nomination') or {}).get('nominee'):
        raise Blocked(f'resume-current: {stage} report carries no frozen nominee; refusing')
    return report


def prepare_current_resume_observer(plan, out_root):
    """Re-bind the harness observer revision for a SAME-plan resume, fail-closed and recorded.

    The only frozen inventory field a same-plan resume may move is the harness observer revision
    (``benchmark_encounter_redesign.py`` itself, the allowed repair target); every strategy, combat,
    native and canonical provenance file must still match the frozen plan byte-for-byte. Writes a
    rebound plan used ONLY by the arm children (the original ``plan.json`` is never edited), sets the
    in-process rebind for the parent preflight, and returns ``(child_plan_path, deviation)``.
    """
    live = observer_inventory()
    live_files = dict(sorted((live.get('files') or {}).items()))
    child_plan = json.loads(json.dumps(plan))
    arms = {}
    for arm in ARMS:
        impl = ((child_plan.get('arms') or {}).get(arm) or {}).get('implementationInventory') or {}
        prior_files = dict(impl.get('observerFiles') or {})
        changed = sorted(name for name in set(prior_files) | set(live_files)
                         if prior_files.get(name) != live_files.get(name))
        if changed != ['benchmark_encounter_redesign.py']:
            raise Blocked(f'current-resume observer rebind refused for {arm}: changed observer files '
                          f'{changed}, not exactly the repaired harness file')
        arms[arm] = dict(priorObserverRevision=impl.get('observerRevision'),
                         liveObserverRevision=live.get('digest'), changedObserverFiles=changed)
        impl['observerFiles'] = dict(live_files)
        impl['observerRevision'] = live.get('digest')
        impl['digest'] = digest({key: value for key, value in impl.items()
                                 if key not in ('digest', 'ok')})
    child_plan['digest'] = digest({key: value for key, value in child_plan.items()
                                   if key != 'digest'})
    path = Path(out_root) / 'plan-current-resume-observer.json'
    write_json(path, child_plan)
    deviation = dict(liveObserverRevision=live.get('digest'),
                     changedObserverFiles=['benchmark_encounter_redesign.py'], arms=arms,
                     childPlan=str(path),
                     note='the harness observer revision moved with the repaired runner file; every '
                          'arm implementation/native/canonical provenance file is byte-identical to '
                          'the frozen v6 plan and the original plan.json is never edited')
    _set_current_resume_observer(dict(liveObserverRevision=live.get('digest'), arms=arms))
    return path, deviation

# --------------------------------------------------------------------------------------------------
# detached checkpoint resume (``resume-detached``): a bounded, explicit continuation of ONE stopped
# run whose interrupted legacy search admission is UNKNOWN. It adopts BOTH fully completed pairs
# (search AND holdout) verbatim, abandons the interrupted pair permanently at its already-reserved
# upper bound (no refund, no fabricated actual, no re-run), and runs only the seven untouched pairs.
# --------------------------------------------------------------------------------------------------
DETACHED_RECOVERY_SCHEMA = 'encounter-redesign-detached-recovery-1'
DETACHED_COMPLETED_PAIRS = ((18, 1), (19, 1))
DETACHED_FAILED_PAIRS = ((0, 1), (15, 1))
DETACHED_ABANDONED_PAIR = (0, 2)
DETACHED_ABANDON_STAGE = 'search:legacy:0:r2'
DETACHED_ABANDON_RESERVATION = 20
DETACHED_ABANDON_CAP = 128
DETACHED_STOPPED_SPENT = 1492
DETACHED_STOPPED_SETUP = 13
DETACHED_SEARCH_CAP = 128
DETACHED_HOLDOUT_PAIRS = 64
DETACHED_ADOPTED_BATTLES = 768
DETACHED_UNKNOWN_CHARGE = 128
DETACHED_FINAL_CHARGED = 4193
DETACHED_UNTOUCHED_EXPECTED = 7
DETACHED_COMPLETED_ARTIFACTS = (
    'search-{arm}-e{enc}-r{rep}.json',
    'nomination-{arm}-e{enc}-r{rep}.json',
    'holdout-{arm}-e{enc}-r{rep}.json',
    'outcome-holdout-{arm}-e{enc}-r{rep}.jsonl',
    'raw-holdout-{arm}-e{enc}-r{rep}.jsonl',
    'meter-search-e{enc}-{arm}.json',
    'meter-holdout-e{enc}-{arm}.json',
)


def _detached_rel(out_root, path):
    path = Path(path)
    try:
        return str(path.relative_to(Path(out_root)))
    except ValueError:
        return str(path)


def _detached_artifact(out_root, path):
    path = Path(path)
    if not path.is_file():
        return None
    return dict(path=_detached_rel(out_root, path), sha256=_file_sha256(path))


def _detached_completed_entry(plan, out_root, arm, encounter_id, replicate, registry):
    """Validate ONE arm's completed (search AND holdout) artifact set, or raise Blocked.

    This never re-nominates and never reissues a holdout pair: it adopts the persisted, fully
    completed 128-battle search and 64-pair holdout whose nominees agree and whose outcome seed pairs
    are exactly the pairs already issued in the registry for this label.
    """
    unit_dir = Path(out_root) / f'{arm}-e{encounter_id}-r{replicate}'
    stage = f'{arm} e{encounter_id} r{replicate}'
    budget = plan.get('budget', {}).get('stages', {})
    search_cap = int((budget.get('search') or {}).get('battlesPerArmPerEncounterPerReplicate')
                     or DETACHED_SEARCH_CAP)
    holdout_cap = int((budget.get('holdout') or {}).get('battlesPerArmPerEncounterPerReplicate')
                      or DETACHED_HOLDOUT_PAIRS)
    if search_cap != DETACHED_SEARCH_CAP or holdout_cap != DETACHED_HOLDOUT_PAIRS:
        raise Blocked(f'detached-checkpoint: the plan declares {search_cap} search / {holdout_cap} '
                      f'holdout per arm, not the predeclared {DETACHED_SEARCH_CAP}/'
                      f'{DETACHED_HOLDOUT_PAIRS}')
    search_path = unit_dir / f'search-{arm}-e{encounter_id}-r{replicate}.json'
    report = read_json(search_path) if search_path.is_file() else None
    if not isinstance(report, dict):
        raise Blocked(f'detached-checkpoint: completed {stage} is missing its search report')
    if report.get('failed') or report.get('incomplete'):
        raise Blocked(f'detached-checkpoint: completed {stage} search report is failed/incomplete')
    if report.get('admitted') is None or report.get('declared') is None \
            or int(report['admitted']) != int(report['declared']) \
            or int(report['admitted']) != search_cap:
        raise Blocked(f'detached-checkpoint: completed {stage} search admitted '
                      f'{report.get("admitted")}/{report.get("declared")}, not '
                      f'{search_cap}/{search_cap}')
    nominee = (report.get('nomination') or {}).get('nominee')
    if not nominee:
        raise Blocked(f'detached-checkpoint: completed {stage} search carries no frozen nominee')
    nomination_path = unit_dir / f'nomination-{arm}-e{encounter_id}-r{replicate}.json'
    nomination = read_json(nomination_path) if nomination_path.is_file() else None
    if not isinstance(nomination, dict) or nomination.get('nominee') != nominee:
        raise Blocked(f'detached-checkpoint: completed {stage} nomination manifest does not match '
                      'its frozen search nominee')
    holdout_path = unit_dir / f'holdout-{arm}-e{encounter_id}-r{replicate}.json'
    holdout = read_json(holdout_path) if holdout_path.is_file() else None
    if not isinstance(holdout, dict):
        raise Blocked(f'detached-checkpoint: completed {stage} is missing its holdout report')
    if holdout.get('failed') or holdout.get('incomplete'):
        raise Blocked(f'detached-checkpoint: completed {stage} holdout report is failed/incomplete')
    if holdout.get('nominee') != nominee:
        raise Blocked(f'detached-checkpoint: completed {stage} holdout nominee differs from the '
                      'frozen search nominee')
    if holdout.get('admitted') is None or holdout.get('declared') is None \
            or int(holdout['admitted']) != int(holdout['declared']) \
            or int(holdout['admitted']) != holdout_cap:
        raise Blocked(f'detached-checkpoint: completed {stage} holdout admitted '
                      f'{holdout.get("admitted")}/{holdout.get("declared")}, not '
                      f'{holdout_cap}/{holdout_cap}')
    label = f'holdout|{encounter_id}|{replicate}'
    if label not in registry.issued:
        raise Blocked(f'detached-checkpoint: completed {stage} has no issued registry label {label}')
    pairs = [tuple(int(v) for v in pair) for pair in registry.issued[label]]
    if len(pairs) != holdout_cap:
        raise Blocked(f'detached-checkpoint: {label} has {len(pairs)} issued pairs, not {holdout_cap}')
    other = [p for lab, plist in registry.issued.items() if lab != label for p in plist]
    used_seeds = [tuple(int(v) for v in pair) for pair in (report.get('usedPairs') or [])]
    registry.issue_or_adopt(label, holdout_cap, forbidden=other, used_seeds=used_seeds)
    outcome_path = unit_dir / f'outcome-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl'
    raw_path = unit_dir / f'raw-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl'
    outcome_rows = _read_raw_rows(outcome_path)
    raw_rows = _read_raw_rows(raw_path)
    if len(outcome_rows) != len(pairs) or len(raw_rows) != len(pairs):
        raise Blocked(f'detached-checkpoint: completed {stage} has {len(outcome_rows)} outcome / '
                      f'{len(raw_rows)} raw rows, not {len(pairs)}')
    assert_native_backend(raw_rows, stage=f'detached-checkpoint:{stage}')
    if {tuple(row.get('seeds') or ()) for row in outcome_rows} != set(pairs):
        raise Blocked(f'detached-checkpoint: completed {stage} outcome seeds are not the issued '
                      'registry pairs')
    if any(row.get('candidateId') != nominee for row in outcome_rows):
        raise Blocked(f'detached-checkpoint: completed {stage} outcome rows are not all the frozen '
                      'nominee')
    meter_search = read_json(unit_dir / f'meter-search-e{encounter_id}-{arm}.json')
    meter_holdout = read_json(unit_dir / f'meter-holdout-e{encounter_id}-{arm}.json')
    if int((meter_search or {}).get('admitted') or 0) != search_cap:
        raise Blocked(f'detached-checkpoint: {stage} search meter admitted '
                      f'{(meter_search or {}).get("admitted")}, not {search_cap}')
    if int((meter_holdout or {}).get('admitted') or 0) != holdout_cap:
        raise Blocked(f'detached-checkpoint: {stage} holdout meter admitted '
                      f'{(meter_holdout or {}).get("admitted")}, not {holdout_cap}')
    record = dict(arm=arm, encounter=int(encounter_id), replicate=int(replicate), nominee=nominee,
                  admitted=dict(search=search_cap, holdout=holdout_cap), registryLabel=label,
                  registryPairs=[list(pair) for pair in pairs],
                  registryPairsDigest=digest([list(pair) for pair in pairs]))
    for name in DETACHED_COMPLETED_ARTIFACTS:
        path = unit_dir / name.format(arm=arm, enc=encounter_id, rep=replicate)
        artifact = _detached_artifact(out_root, path)
        if artifact is None:
            raise Blocked(f'detached-checkpoint: completed {stage} is missing {path.name}')
        record[path.name] = artifact
    return record


def prepare_detached_checkpoint(plan, out_root, ledger, registry, carry_receipt, auth_receipt):
    """Validate the stopped comparison-v6 run and return the bounded detached-checkpoint context.

    Fail-closed: the exact plan/authorisation/carryforward receipts are re-verified against the disk,
    the STOPPED ledger must be exactly 1492 regular + 13 setup with the single interrupted legacy
    e0r2 search reservation outstanding, BOTH completed pairs (e18r1, e19r1) must carry complete,
    matching 128-battle search and 64-pair holdout payloads whose seeds are the already-issued
    registry pairs, and a separate recovery receipt snapshots deterministic hashes of every adopted
    artifact. Only then is the interrupted reservation removed, with ``spent`` left untouched as a
    conservative upper bound (no refund, no fabricated actual, no counter grant). Re-running over an
    unchanged stopped run validates the existing snapshot and never double charges; any unexplained
    change is refused.
    """
    out_root = Path(out_root)
    problems = []
    plan_disk = read_json(out_root / 'plan.json') if (out_root / 'plan.json').is_file() else {}
    if plan_disk.get('planId') != plan.get('planId') or plan_disk.get('digest') != plan.get('digest'):
        problems.append('the run directory plan.json is not the plan being resumed')
    if (out_root / 'results.json').is_file():
        problems.append('the run already produced results.json; it is not an unfinished run')
    if (auth_receipt.get('kind') != 'encounter-redesign-benchmark-authorisation-1'
            or auth_receipt.get('planId') != plan.get('planId')
            or auth_receipt.get('planDigest') != plan.get('digest')
            or auth_receipt.get('authorisedBy') != 'root:--authorised'):
        problems.append('the authorisation receipt is not bound to this plan (root:--authorised)')
    if carry_receipt.get('schema') not in (CARRYFORWARD_V4_SCHEMA, CARRYFORWARD_V5_SCHEMA):
        problems.append('the prior carryforward receipt is not a v4/v5 continuation receipt')
    new_plan = carry_receipt.get('newPlan') or {}
    if new_plan.get('planId') != plan.get('planId') or new_plan.get('digest') != plan.get('digest'):
        problems.append('the prior carryforward receipt is not bound to this exact plan')
    failed = _current_resume_failed_pairs(carry_receipt)
    if failed != DETACHED_FAILED_PAIRS:
        problems.append(f'the prior receipt excludes {failed}, not the predeclared '
                        f'{DETACHED_FAILED_PAIRS}')
    if int(ledger.setup_spent) != DETACHED_STOPPED_SETUP:
        problems.append(f'the prior ledger carries {ledger.setup_spent} setup, not '
                        f'{DETACHED_STOPPED_SETUP}')
    if int(ledger.spent) != DETACHED_STOPPED_SPENT:
        problems.append(f'the prior ledger carries {ledger.spent} regular, not '
                        f'{DETACHED_STOPPED_SPENT}')
    reserved = ledger.reserved
    has_reservation = DETACHED_ABANDON_RESERVATION in reserved
    abandon_claims = [claim for claim in ledger.claims
                      if claim.get('op') == 'abandon-unknown'
                      and claim.get('stage') == DETACHED_ABANDON_STAGE]
    if has_reservation:
        entry = reserved[DETACHED_ABANDON_RESERVATION]
        if entry.get('stage') != DETACHED_ABANDON_STAGE \
                or int(entry.get('cap') or -1) != DETACHED_ABANDON_CAP:
            problems.append('the interrupted reservation is not the expected legacy e0r2 search')
        if {int(key) for key in reserved} != {DETACHED_ABANDON_RESERVATION}:
            problems.append('the prior ledger carries reservations beyond the interrupted legacy '
                            'e0r2 search')
    if not has_reservation and not abandon_claims:
        problems.append('the interrupted legacy e0r2 reservation is neither present nor recorded as '
                        'abandoned; refusing an unexplained ledger')
    if has_reservation and abandon_claims:
        problems.append('the interrupted legacy e0r2 reservation is both present and marked '
                        'abandoned; refusing an ambiguous ledger')
    completed = []
    if not problems:
        try:
            for arm in ARMS:
                for encounter_id, replicate in DETACHED_COMPLETED_PAIRS:
                    completed.append(_detached_completed_entry(
                        plan, out_root, arm, encounter_id, replicate, registry))
        except Blocked as exc:
            problems.append(str(exc))
    if problems:
        raise Blocked('resume-detached refused: ' + '; '.join(problems))
    pairs = [(int(row['id']), replicate) for replicate in range(1, int(plan['replicates']) + 1)
             for row in plan['encounters'] if row.get('id') is not None]
    done = {(int(enc), int(rep)) for enc, rep in DETACHED_COMPLETED_PAIRS}
    abandoned = {(int(enc), int(rep)) for enc, rep in (DETACHED_ABANDONED_PAIR,)}
    excluded = set(DETACHED_FAILED_PAIRS) | done | abandoned
    untouched = [pair for pair in pairs if pair not in excluded]
    if len(untouched) != DETACHED_UNTOUCHED_EXPECTED:
        raise Blocked(f'resume-detached refused: {len(untouched)} untouched pairs remain, not the '
                      f'predeclared {DETACHED_UNTOUCHED_EXPECTED}')
    registry_path = out_root / plan['holdout']['registry']
    if not registry_path.is_file():
        raise Blocked('resume-detached refused: the holdout registry is missing')
    snapshot = dict(
        kind='encounter-redesign-detached-recovery-core-1',
        planId=plan['planId'], planDigest=plan['digest'],
        authorisation=dict(path='authorisation-receipt.json', digest=digest(auth_receipt)),
        carryforward=dict(path='carryforward-receipt.json', schema=carry_receipt.get('schema'),
                          digest=digest(carry_receipt)),
        priorLedger=dict(spent=int(ledger.spent), setupSpent=int(ledger.setup_spent),
                         ceiling=int(ledger.ceiling),
                         reservationId=DETACHED_ABANDON_RESERVATION,
                         reservationStage=DETACHED_ABANDON_STAGE,
                         reservationCap=DETACHED_ABANDON_CAP),
        completed=completed,
        registry=dict(path=plan['holdout']['registry'],
                      sha256=_file_sha256(registry_path),
                      labels={label: len(plist) for label, plist in sorted(registry.issued.items())}),
        abandon=dict(stage=DETACHED_ABANDON_STAGE, reservationId=DETACHED_ABANDON_RESERVATION,
                     cap=DETACHED_ABANDON_CAP, actual=None,
                     chargedUpperBound=DETACHED_ABANDON_CAP, unknownAdmission=True,
                     note='the legacy e0r2 search admission is UNKNOWN: a missing meter after the '
                          'lost process is not proof of zero. The case is abandoned permanently '
                          'and its already-reserved 128 battles stay charged as a conservative '
                          'upper bound, with no refund, no fabricated actual and no counter grant'),
        failedPairs=[list(pair) for pair in DETACHED_FAILED_PAIRS],
        completedPairs=[list(pair) for pair in DETACHED_COMPLETED_PAIRS],
        abandonedPairs=[list(DETACHED_ABANDONED_PAIR)],
        untouchedPairs=[dict(encounter=pair[0], replicate=pair[1]) for pair in untouched],
        adoptedBattles=DETACHED_ADOPTED_BATTLES, unknownCharge=DETACHED_UNKNOWN_CHARGE,
        expectedFinal=DETACHED_FINAL_CHARGED, hardCeiling=int(plan['budget']['hardCeiling']))
    receipt_path = out_root / 'detached-recovery-receipt.json'
    stored = read_json(receipt_path) if receipt_path.is_file() else None
    if stored is not None:
        if stored.get('schema') != DETACHED_RECOVERY_SCHEMA:
            raise Blocked('resume-detached refused: an existing detached-recovery receipt has the '
                          'wrong schema')
        stored_core = {key: value for key, value in stored.items() if key != 'createdAt'}
        if digest(stored_core) != digest(dict(snapshot, schema=DETACHED_RECOVERY_SCHEMA)):
            raise Blocked('resume-detached refused: the stopped run changed since the validated '
                          'detached-recovery snapshot; refusing an unexplained state change')
    else:
        write_json(receipt_path, dict(snapshot, schema=DETACHED_RECOVERY_SCHEMA, createdAt=now()))
    applied_now = False
    if has_reservation and not abandon_claims:
        ledger.abandon_unknown(DETACHED_ABANDON_RESERVATION, reason=snapshot['abandon']['note'])
        applied_now = True
    context = dict(snapshot, recoveryPath=str(receipt_path), abandonAppliedNow=applied_now,
                   abandonAlreadyApplied=bool(abandon_claims))
    return context


def reconcile_detached_final(ledger, search_reports, detached, plan):
    """Reconcile actual settled work against the ceiling, allowing evidenced unused budget.

    An incomplete search deliberately withholds its holdouts. Neither unused allocation nor
    withheld confirmation is a battle. Missing reports, open reservations and unexplained ledger
    differences still fail closed. This function is read-only and can finalize saved evidence.
    """
    if ledger.get('reserved'):
        raise Blocked('final accounting has outstanding reservations')
    settled = {}
    for entry in ledger.get('claims', []):
        if entry.get('op') == 'settle':
            settled.setdefault(entry['stage'], []).append(entry)
    search_cap = int(plan['budget']['stages']['search']['battlesPerArmPerEncounterPerReplicate'])
    holdout_cap = int(plan['budget']['stages']['holdout']['battlesPerArmPerEncounterPerReplicate'])
    used = unused = withheld = 0
    incomplete = []
    for pair in detached['untouchedPairs']:
        encounter, replicate = pair['encounter'], pair['replicate']
        reports = {arm: search_reports.get((arm, encounter, replicate)) for arm in ARMS}
        for arm, report in reports.items():
            stage = f'search:{arm}:{encounter}:r{replicate}'
            entries = settled.get(stage, [])
            if not report or len(entries) != 1:
                raise Blocked(f'final accounting missing/duplicate search evidence: {stage}')
            actual = entries[0].get('actual')
            if (actual is None or not 0 <= actual <= search_cap
                    or actual != report.get('admitted') or entries[0]['cap'] != search_cap):
                raise Blocked(f'final accounting search mismatch: {stage}')
            used += actual
            unused += search_cap - actual
        problem = _search_unit_problem(reports, search_cap)
        if problem:
            incomplete.append(dict(encounterId=encounter, replicate=replicate, reason=problem))
        for arm in ARMS:
            stage = f'holdout:{arm}:{encounter}:r{replicate}'
            entries = settled.get(stage, [])
            if problem:
                if entries:
                    raise Blocked(f'holdout was admitted after incomplete search: {stage}')
                withheld += holdout_cap
            else:
                if (len(entries) != 1 or entries[0].get('actual') != holdout_cap
                        or entries[0].get('cap') != holdout_cap):
                    raise Blocked(f'final accounting incomplete holdout: {stage}')
                used += holdout_cap
    prior = int(detached['priorLedger']['spent']) + int(detached['priorLedger']['setupSpent'])
    committed = int(ledger['spent']) + int(ledger['setupSpent'])
    maximum = prior + len(detached['untouchedPairs']) * 2 * (search_cap + holdout_cap)
    if (committed != prior + used or committed + unused + withheld != maximum
            or maximum != DETACHED_FINAL_CHARGED or committed > plan['budget']['hardCeiling']):
        raise Blocked('final accounting has an unexplained charged/unused/withheld difference')
    return dict(chargedUpperBound=committed, priorCharged=prior, newObservedBattles=used,
                unusedSearch=unused, withheldHoldout=withheld, maximum=maximum,
                incompleteUnits=incomplete, outstandingReservations=0)


def _adopt_detached_holdout(plan, arm, unit_dir, encounter_id, replicate, pairs, nominee):
    """Adopt a completed holdout unit verbatim: recompute the canonical rows and require them to
    reproduce the persisted outcome rows exactly (never appending, never re-running)."""
    unit_dir = Path(unit_dir)
    raw_rows = _read_raw_rows(unit_dir / f'raw-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl')
    assert_native_backend(raw_rows, stage=f'holdout-eval:{arm}:e{encounter_id}')
    policy = arm_policy(plan, arm)
    applied, counts = apply_canonical(raw_rows, policy)
    persisted = _read_raw_rows(unit_dir / f'outcome-holdout-{arm}-e{encounter_id}-r{replicate}.jsonl')
    if digest(applied) != digest(persisted):
        raise Blocked(f'detached-checkpoint: adopted holdout {arm} e{encounter_id} r{replicate} '
                      'does not reproduce its persisted outcome rows; refusing a tampered artifact')
    cells = earned_cells(applied)
    unresolved = sum(1 for pair in pairs if cells.get((nominee, tuple(pair))) is None)
    return dict(arm=arm, stage='holdout', replicate=int(replicate), encounterId=int(encounter_id),
                nominee=nominee, nomineeOnly=True, pairs=len(pairs), unresolvedPairs=unresolved,
                counts=counts, finishPolicy=policy.get('finishPolicy'), rows=applied,
                compute='prior')


def adopt_detached_completed(plan, out_root, results, search_reports, context, ledger):
    """Adopt every completed pair into the result units BEFORE the copy/work loop, and return the
    (completed, abandoned) pair sets so the loop skips them entirely. No copy is created, no battle
    is started and no nominee or holdout pair is re-derived."""
    out_root = Path(out_root)
    for entry in context['completed']:
        arm, encounter_id, replicate = entry['arm'], int(entry['encounter']), int(entry['replicate'])
        unit_dir = out_root / f'{arm}-e{encounter_id}-r{replicate}'
        report = _adopt_current_search(unit_dir, arm, encounter_id, replicate, ledger)
        search_reports[(arm, encounter_id, replicate)] = report
        results['units'].append(dict(arm=arm, stage='search', search=report, compute='prior'))
        results['steps'].append(dict(op='search-reused', arm=arm, encounterId=encounter_id,
                                     replicate=replicate, admitted=report.get('admitted')))
        results['steps'].append(dict(op='freeze-reused', arm=arm, encounterId=encounter_id,
                                     replicate=replicate,
                                     nominee=(report.get('nomination') or {}).get('nominee')))
    for entry in context['completed']:
        arm, encounter_id, replicate = entry['arm'], int(entry['encounter']), int(entry['replicate'])
        unit_dir = out_root / f'{arm}-e{encounter_id}-r{replicate}'
        pairs = [tuple(int(v) for v in pair) for pair in entry['registryPairs']]
        unit = _adopt_detached_holdout(plan, arm, unit_dir, encounter_id, replicate, pairs,
                                       entry['nominee'])
        results['units'].append(unit)
        results['steps'].append(dict(op='holdout-reused', arm=arm, encounterId=encounter_id,
                                     replicate=replicate, admitted=entry['admitted']['holdout'],
                                     pairs=len(pairs)))
    completed_pairs = {(int(item['encounter']), int(item['replicate']))
                       for item in context['completed']}
    abandoned_pairs = {(int(pair[0]), int(pair[1])) for pair in context['abandonedPairs']}
    return completed_pairs, abandoned_pairs


def run_units(plan, python, out_root, *, stages, authorise, workers, seconds=10800, plan_path=None,
              resume_from=None, reviewed_source_manifest=None, current_resume=False,
              checkpoint_resume=False):
    # Clear any same-plan observer rebind from an earlier call in this process; only a current resume
    # below may set it, and only after verifying exactly the repaired harness file moved.
    _set_current_resume_observer(None)
    errors = validate_plan(plan, check_baseline=True)
    if errors:
        raise Blocked('plan is not runnable: ' + '; '.join(errors))
    if not (plan.get('storage') or {}).get('copyMiB'):
        raise Blocked('the plan does not declare the copy-only storage limit; refusing to run an '
                      'unbounded copy Store')
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    # A content-detected continuation validates the stopped run and adopts ONLY its completed stages;
    # it never starts a battle here and fails closed on any mismatch.
    carryforward, carry_index = (None, {})
    current_receipt = None
    observer_rebind = None
    failed_pairs = set()
    detached, completed_pairs, abandoned_pairs = None, set(), set()
    if current_resume:
        # SAME-plan in-place resume: the run dir already holds the plan, ledger and registry; there is
        # no fresh carryforward and no re-charging. Refuse an ambiguous double resume.
        if resume_from is not None:
            raise Blocked('resume-current continues the SAME run directory; --resume-from is refused')
        receipt_path = out_root / 'carryforward-receipt.json'
        if not receipt_path.is_file():
            raise Blocked('resume-current: the run directory has no carryforward receipt to bind to')
        current_receipt = read_json(receipt_path)
        if not isinstance(current_receipt, dict):
            raise Blocked('resume-current: malformed carryforward receipt')
    elif checkpoint_resume:
        # SAME-plan detached checkpoint: the run dir already holds the plan, ledger and registry, and
        # the exact stopped state is re-verified before any battle. Refuse an ambiguous double resume.
        if resume_from is not None:
            raise Blocked('resume-detached continues the SAME run directory; --resume-from is refused')
        receipt_path = out_root / 'carryforward-receipt.json'
        if not receipt_path.is_file():
            raise Blocked('resume-detached: the run directory has no carryforward receipt to bind to')
        current_receipt = read_json(receipt_path)
        if not isinstance(current_receipt, dict):
            raise Blocked('resume-detached: malformed carryforward receipt')
    elif resume_from is not None:
        carryforward, carry_index = prepare_carryforward(
            plan, resume_from, out_root, reviewed_source_manifest=reviewed_source_manifest)
    # The immutable hashed plan stays `proposed`; the explicit root --authorised flag is accepted and
    # recorded in a SEPARATE receipt bound to planId+digest before any battle. No manual plan edit is
    # needed and there is no silent auto-authorisation: without the flag nothing runs.
    receipt = _authorise_plan(plan, out_root, authorise=authorise)
    plan_path = Path(plan_path) if plan_path else (out_root / 'plan.json')
    if not plan_path.is_file():
        write_json(plan_path, plan)
    budget = plan['budget']
    ledger = BudgetLedger(out_root / 'battle-ledger.json', budget['hardCeiling'],
                          setup_cap=budget['setupCap'])
    registry = HoldoutRegistry(out_root / 'holdout-registry.json', plan['planId'],
                               plan['holdout']['globalNonce'])
    if current_resume:
        problems = check_current_resume_state(plan, out_root, ledger, current_receipt)
        if problems:
            raise Blocked('resume-current refused: ' + '; '.join(problems))
        carryforward = current_receipt
        failed_pairs = set(_current_resume_failed_pairs(current_receipt))
        # The repaired runner moved the harness observer revision; re-bind it explicitly (recorded
        # below) and hand the arm children a rebound plan whose frozen inventory matches live. The
        # original plan.json and every arm source/native file are untouched.
        child_plan_path, observer_rebind = prepare_current_resume_observer(plan, out_root)
        plan_path = child_plan_path
    elif checkpoint_resume:
        # Bounded explicit checkpoint: validate the exact stopped run, snapshot the completed
        # artifacts, then abandon the interrupted legacy e0r2 search at its reserved upper bound.
        detached = prepare_detached_checkpoint(plan, out_root, ledger, registry, current_receipt,
                                               receipt)
        carryforward = current_receipt
        failed_pairs = set(_current_resume_failed_pairs(current_receipt))
        child_plan_path, observer_rebind = prepare_current_resume_observer(plan, out_root)
        plan_path = child_plan_path
    gates = preflight(plan, python=python, out_root=out_root)
    write_json(out_root / 'preflight.json', gates)
    if gates['blocked']:
        raise Blocked('preflight blocked: ' + '; '.join(gates['blocked']))
    if carryforward is not None and not current_resume and not checkpoint_resume:
        # Charge the adopted completed results once (the early-recovery 208, or the v4 336), then the
        # whole failed legacy-dispatch 13 against the setup allowance, so the prior cost is carried
        # with no lost cost and no double count.
        for entry in carryforward['adopted']:
            label = entry['mode'] or entry['arm']
            reservation = ledger.reserve(f"carryforward:{entry['stage']}:{label}", entry['cap'])
            ledger.settle(reservation, entry['admitted'])
        # The v4 continuation also charges the one permanently failed pair's real searches (128
        # legacy + 4 new cleanup dispatches) once, and never re-runs that pair.
        for entry in carryforward.get('failedSearches') or []:
            reservation = ledger.reserve(f"carryforward-failed:{entry['stage']}",
                                         int(entry['admitted']))
            ledger.settle(reservation, int(entry['admitted']))
        for failed_record in _carryforward_failed_records(carryforward):
            failed_pairs.add((int(failed_record['encounter']), int(failed_record['replicate'])))
        ledger.charge_setup('carryforward:throughput:legacy-dispatch',
                            carryforward['setupCharge']['battles'])
    parity_gate = (gates.get('gates') or {}).get('parity') or {}
    migration_plan_id = (parity_gate.get('migration') or {}).get('planId') \
        if (parity_gate.get('migration') or {}).get('eligible') else None
    results = dict(planId=plan['planId'], startedAt=now(), gates=gates, units=[], steps=[],
                   ledger=ledger.snapshot(), authorisation=receipt, incomplete=False,
                   carryforward=carryforward)
    baseline = plan['baseline']['path']
    encounters = [row['id'] for row in plan['encounters'] if row['id'] is not None]
    workers = max(1, int(workers or 4))
    search_reports, created, incomplete_units = {}, [], set()
    if checkpoint_resume:
        # Adopt every completed pair into the result units BEFORE the copy/work loop and skip it
        # entirely: no copy is created, no battle is started and no nominee or pair is re-derived.
        completed_pairs, abandoned_pairs = adopt_detached_completed(
            plan, out_root, results, search_reports, detached, ledger)
        incomplete_units.update(abandoned_pairs)
        results['incomplete'] = True
    try:
        if 'smoke' in stages:
            cap = int(budget['stages']['smoke']['battles']) // len(ARMS)
            for arm in ARMS:
                unit_dir = out_root / f'smoke-{arm}'
                adopted = carry_index.get(('smoke', arm))
                if adopted is not None:
                    # Adopted from the stopped run; already charged by the carryforward block above.
                    rows = _read_raw_rows(adopted['adoptedPath'])
                    applied, counts = apply_canonical(rows, arm_policy(plan, arm))
                    results['units'].append(dict(
                        arm=arm, stage='smoke', admitted=adopted['admitted'], counts=counts,
                        battles=len(applied), compute='prior', timings=adopted.get('timings'),
                        carryforward=dict(unit=adopted['unit'], sourceSha256=adopted['sourceSha256'],
                                          adoptedPath=adopted['adoptedPath'])))
                    results['steps'].append(dict(op='smoke-reused', arm=arm,
                                                 admitted=adopted['admitted']))
                    continue
                reservation = ledger.reserve(f'smoke:{arm}', cap)
                payload = None
                try:
                    payload = spawn_arm(python, plan_path, arm, 'smoke',
                                        plan['arms'][arm]['source'], None, unit_dir, cap=cap,
                                        seconds=seconds, workers=workers)
                finally:
                    ledger.settle(reservation, _recovered_admitted(
                        unit_dir / f'meter-smoke-{arm}.json', payload, cap,
                        report_path=unit_dir / f'smoke-{arm}.json'))
                rows = _read_raw_rows(payload.get('rowsFile') or '')
                applied, counts = apply_canonical(rows, arm_policy(plan, arm))
                results['units'].append(dict(arm=arm, stage='smoke', admitted=payload.get('admitted'),
                                             counts=counts, battles=len(applied)))
                results['steps'].append(dict(op='smoke', arm=arm, admitted=payload.get('admitted')))
        # Throughput runs BEFORE the search: the <=10% matched-infrastructure WALL check is a cheap
        # gate, and a run that exceeds it stops here for root diagnosis instead of spending the full
        # search budget on a comparison whose infrastructure is not matched.
        if 'throughput' in stages:
            for mode in THROUGHPUT_MODES:
                # telemetry-off/on isolate the current runtime pool toggle; the two dispatch modes
                # are the frozen legacy path versus the current Evaluator+ea ledger.
                arm = _throughput_arm(mode)
                unit_dir = out_root / f'throughput-{mode}'
                unit_dir.mkdir(parents=True, exist_ok=True)
                adopted = carry_index.get(('throughput', mode))
                if adopted is not None:
                    # Adopted from the stopped run; already charged by the carryforward block above.
                    payload = read_json(adopted['adoptedPayloadPath'] or adopted['payloadPath'])
                    payload['carryforward'] = dict(
                        unit=adopted['unit'], sourceSha256=adopted['sourceSha256'],
                        payloadSha256=adopted['payloadSha256'], adoptedPath=adopted['adoptedPath'])
                    results['units'].append(dict(arm=arm, mode=mode, throughput=payload,
                                                 carryforward=payload['carryforward'],
                                                 compute='prior', timings=adopted.get('timings')))
                    results['steps'].append(dict(op='throughput-reused', arm=arm, mode=mode,
                                                 admitted=payload.get('admitted')))
                    continue
                copy_path = None
                if mode in MATCHED_MODES:
                    copy_path = unit_dir / UNIT_COPY_NAME
                    register_copy(created, copy_path, root=out_root)
                    copy_baseline(baseline, copy_path)
                cap = int(plan['throughput']['battlesPerMode'])
                reservation = ledger.reserve(f'throughput:{mode}', cap)
                payload = None
                report = None
                try:
                    payload = spawn_arm(python, plan_path, arm, 'throughput',
                                        plan['arms'][arm]['source'], copy_path, unit_dir, cap=cap,
                                        extra=['--mode', mode, '--battles', str(cap)],
                                        seconds=seconds, workers=workers)
                    report = _validated_throughput_report(unit_dir, arm, mode, cap)
                finally:
                    ledger.settle(reservation, _recovered_admitted(
                        unit_dir / f'meter-throughput-{mode}-{arm}.json', payload, cap,
                        report_path=unit_dir / f'throughput-{arm}-{mode}.json'))
                    if copy_path is not None:
                        cleanup_copies(created, [copy_path], root=out_root)
                # The gate consumes the child's VALIDATED full report (its timing stages), never the
                # compact stdout summary that carries no stages.
                results['units'].append(dict(arm=arm, mode=mode, throughput=report,
                                             admitted=payload.get('admitted')))
            if 'search' in stages:
                _throughput_gate(results['units'], out_root=out_root, plan=plan)
        if ('search' in stages or 'holdout' in stages) and 'throughput' not in stages:
            # A search/holdout invocation may NOT bypass the <=10% matched-infrastructure gate: it
            # must carry the gate in the SAME invocation, or reuse a persisted gate bound to THIS
            # exact planId+digest. An unavailable or unbound/over-threshold gate fails closed.
            persisted = _persisted_throughput_gate(out_root, plan)
            if persisted is None:
                raise Blocked('search/holdout requires the matched-infrastructure throughput gate: '
                              'run it in the same invocation (--stage throughput) or provide a '
                              'persisted gate bound to this plan')
            if not persisted.get('compared'):
                raise Blocked('the persisted throughput gate is unavailable ('
                              f'{persisted.get("reason")}); refusing to bypass the gate')
            if not persisted.get('withinThreshold'):
                raise Blocked('the persisted throughput gate exceeded the threshold; refusing to run '
                              'the search on unmatched infrastructure')
        if 'search' in stages or 'holdout' in stages:
            search_cap = int(budget['stages']['search']['battlesPerArmPerEncounterPerReplicate'])
            pairs_per_arm = \
                int(budget['stages']['holdout']['battlesPerArmPerEncounterPerReplicate'])
            forbidden_cache = {}
            for replicate in range(1, int(plan['replicates']) + 1):
                for encounter_id in encounters:
                    if (int(encounter_id), replicate) in failed_pairs:
                        # Verified permanently failed/inconclusive: never re-run, re-nominated or
                        # replaced. The prior cost was charged once above; the pair is excluded.
                        results['steps'].append(dict(
                            op='failed-unit', encounterId=int(encounter_id), replicate=replicate,
                            status='permanently-failed-inconclusive', compute='prior',
                            reason='the failed pair is skipped with no replacement, no re-run and no '
                                   'nomination reconstruction'))
                        continue
                    if checkpoint_resume and (int(encounter_id), replicate) in abandoned_pairs:
                        # The interrupted legacy e0r2 admission is UNKNOWN: the case is abandoned
                        # permanently (both arms AND its holdout are skipped) and its reserved 128
                        # stays charged as a conservative upper bound (no refund, no fabricated
                        # actual, no counter grant). It is never re-run or re-nominated.
                        results['steps'].append(dict(
                            op='abandoned-unknown', encounterId=int(encounter_id),
                            replicate=replicate, compute='unknown',
                            chargedUpperBound=DETACHED_ABANDON_CAP, actual=None,
                            unknownAdmission=True,
                            reason='the interrupted search admission is unknown (a missing meter is '
                                   'not proof of zero); the case is abandoned permanently'))
                        continue
                    if checkpoint_resume and (int(encounter_id), replicate) in completed_pairs:
                        # Adopted verbatim above (search AND holdout) before the copy/work loop.
                        continue
                    # Every (arm, encounter, replicate) starts from an IDENTICAL frozen baseline
                    # copy. No two encounters may share a copy: the coordinator's finite config and
                    # session would be spent after the first activation, so the next encounter would
                    # be refused or reuse a spent budget.
                    unit_dirs = {arm: unit_copy_dir(out_root, arm, encounter_id, replicate)
                                 for arm in ARMS}
                    unit_copies = {arm: unit_dirs[arm] / UNIT_COPY_NAME for arm in ARMS}
                    try:
                        if 'search' in stages:
                            for arm in ARMS:
                                unit_dirs[arm].mkdir(parents=True, exist_ok=True)
                                register_copy(created, unit_copies[arm], root=out_root)
                                copy_baseline(baseline, unit_copies[arm])
                            for arm in ARMS:
                                unit_dir, copy_path = unit_dirs[arm], unit_copies[arm]
                                if current_resume:
                                    adopted_report = _adopt_current_search(
                                        unit_dir, arm, encounter_id, replicate, ledger)
                                    if adopted_report is not None:
                                        # The search is already charged once and its frozen nomination
                                        # is reused verbatim; the recreated copy only serves the
                                        # holdout nominee read. No reservation, no new battles.
                                        search_reports[(arm, encounter_id, replicate)] = adopted_report
                                        results['steps'].append(dict(
                                            op='search-reused', arm=arm, encounterId=encounter_id,
                                            replicate=replicate,
                                            admitted=adopted_report.get('admitted')))
                                        results['steps'].append(dict(
                                            op='freeze-reused', arm=arm, encounterId=encounter_id,
                                            replicate=replicate,
                                            nominee=(adopted_report.get('nomination') or {}).get(
                                                'nominee')))
                                        results['units'].append(dict(arm=arm, stage='search',
                                                                     search=adopted_report,
                                                                     compute='prior'))
                                        continue
                                reservation = ledger.reserve(
                                    f'search:{arm}:{encounter_id}:r{replicate}', search_cap)
                                extra = ['--encounter', str(encounter_id),
                                         '--replicate', str(replicate)]
                                if arm == ARM_NEW and migration_plan_id:
                                    extra += ['--migration-plan-id', str(migration_plan_id)]
                                payload = None
                                try:
                                    payload = spawn_arm(
                                        python, plan_path, arm, 'search',
                                        plan['arms'][arm]['source'], copy_path, unit_dir,
                                        cap=search_cap, extra=extra, seconds=seconds,
                                        workers=workers)
                                finally:
                                    ledger.settle(reservation, _recovered_admitted(
                                        unit_dir / f'meter-search-e{encounter_id}-{arm}.json',
                                        payload, search_cap,
                                        report_path=unit_dir
                                        / f'search-{arm}-e{encounter_id}-r{replicate}.json'))
                                search_reports[(arm, encounter_id, replicate)] = payload
                                if payload.get('incomplete'):
                                    results['incomplete'] = True
                                results['steps'].append(dict(
                                    op='search', arm=arm, encounterId=encounter_id,
                                    replicate=replicate, admitted=payload.get('admitted'),
                                    observedRuns=payload.get('observedRuns')))
                                results['steps'].append(dict(
                                    op='freeze', arm=arm, encounterId=encounter_id,
                                    replicate=replicate,
                                    nominee=(payload.get('nomination') or {}).get('nominee')))
                                results['units'].append(dict(arm=arm, stage='search', search=payload,
                                                             compute='new'))
                        else:
                            # A separate holdout-only run must resume genuine persisted nomination
                            # manifests (with their frozen arm copies) or refuse loudly; it is never
                            # allowed to produce a silently empty comparison.
                            for arm in ARMS:
                                search_reports[(arm, encounter_id, replicate)] = _persisted_search(
                                    unit_dirs[arm], arm, encounter_id, replicate,
                                    copy_path=unit_copies[arm])
                        # Both arms' results are checked as a matched pair BEFORE any holdout battle:
                        # an underexecuted or unequal search makes the unit incomplete, blocks its
                        # holdout (conserving budget) and is excluded from the comparative verdict.
                        unit_problem = _search_unit_problem(
                            {arm: search_reports.get((arm, encounter_id, replicate)) for arm in ARMS},
                            search_cap if 'search' in stages else None)
                        if unit_problem:
                            results['incomplete'] = True
                            incomplete_units.add((replicate, encounter_id))
                            results['steps'].append(dict(
                                op='holdout-blocked', encounterId=encounter_id,
                                replicate=replicate, reason=unit_problem))
                            continue
                        if 'holdout' not in stages:
                            continue
                        nominations = {
                            arm: (search_reports.get((arm, encounter_id, replicate)) or {})
                            .get('nomination') or {} for arm in ARMS}
                        if any(not nominations[arm].get('nominee') for arm in ARMS):
                            results['incomplete'] = True
                            results['steps'].append(dict(
                                op='holdout-skipped', encounterId=encounter_id,
                                replicate=replicate, reason='an arm has no eligible nominee'))
                            continue
                        new_pairs = []
                        for arm in ARMS:
                            report = search_reports.get((arm, encounter_id, replicate)) or {}
                            new_pairs.extend(report.get('usedPairs') or [])
                        nominees = {arm: nominations[arm]['nominee'] for arm in ARMS}
                        label = f'holdout|{encounter_id}|{replicate}'
                        issued = [pair for prior_label, prior_pairs in registry.issued.items()
                                  if not current_resume or prior_label != label
                                  for pair in prior_pairs]
                        forbidden = _union_reserved(unit_copies, new_pairs, issued_pairs=issued,
                                                    cache=forbidden_cache)
                        if current_resume:
                            # A same-plan resume adopts the pairs it already issued (strictly only
                            # when no holdout battle was admitted) instead of reissuing a fresh set.
                            was_issued = label in registry.issued
                            pairs = registry.issue_or_adopt(label, pairs_per_arm,
                                                            forbidden=forbidden,
                                                            used_seeds=new_pairs)
                            if was_issued:
                                results['steps'].append(dict(
                                    op='holdout-reused', encounterId=encounter_id,
                                    replicate=replicate, label=label, pairs=len(pairs)))
                        else:
                            pairs = registry.issue(label, pairs_per_arm, forbidden=forbidden)
                        for arm in ARMS:
                            cap = len(pairs)
                            reservation = ledger.reserve(
                                f'holdout:{arm}:{encounter_id}:r{replicate}', cap)
                            payload = None
                            try:
                                payload = spawn_arm(
                                    python, plan_path, arm, 'holdout',
                                    plan['arms'][arm]['source'], unit_copies[arm],
                                    unit_dirs[arm], cap=cap,
                                    extra=['--encounter', str(encounter_id),
                                           '--replicate', str(replicate),
                                           '--pairs', canonical([list(pair) for pair in pairs]),
                                           '--nominee', nominees[arm]],
                                    seconds=seconds, workers=workers)
                            finally:
                                ledger.settle(reservation, _recovered_admitted(
                                    unit_dirs[arm]
                                    / f'meter-holdout-e{encounter_id}-{arm}.json', payload, cap,
                                    report_path=unit_dirs[arm]
                                    / f'holdout-{arm}-e{encounter_id}-r{replicate}.json'))
                            if payload.get('incomplete'):
                                results['incomplete'] = True
                            raw_rows = _read_raw_rows(payload.get('rowsFile') or '')
                            holdout_unit = evaluate_holdout(
                                plan, arm, unit_dirs[arm], encounter_id, replicate, pairs,
                                nominees[arm], raw_rows)
                            holdout_unit['compute'] = 'new'
                            results['units'].append(holdout_unit)
                            results['steps'].append(dict(
                                op='holdout', arm=arm, encounterId=encounter_id,
                                replicate=replicate, admitted=payload.get('admitted')))
                    finally:
                        # Nominations, scenario and holdout rows are already persisted above; only then
                        # is this unit's pair of copies released, so at most two 4GB copies are live.
                        cleanup_copies(created, [unit_copies[arm] for arm in ARMS], root=out_root)
    finally:
        # Ownership-scoped: delete only copies this run created and still owns, never the baseline.
        cleanup_copies(created, list(created), root=out_root)
    results['ledger'] = ledger.snapshot()
    if current_resume:
        # Fail closed on the known-good accounting: 865 charged before the resume (852 regular + 13
        # setup), 256 searches retained, 0 holdouts admitted, and a resulting 4449 (<= 4976). Any
        # deviation means the run is not the predeclared same-plan continuation.
        committed = int(ledger.spent) + int(ledger.setup_spent)
        if committed != CURRENT_RESUME_EXPECTED_FINAL:
            raise Blocked(f'resume-current final charged total {committed} is not the predeclared '
                          f'{CURRENT_RESUME_EXPECTED_FINAL}; refusing to report fabricated accounting')
        results['currentResume'] = dict(
            priorRegular=CURRENT_RESUME_PRIOR_REGULAR, priorSetup=CURRENT_RESUME_PRIOR_SETUP,
            priorTotal=CURRENT_RESUME_PRIOR_TOTAL,
            observerRebind=observer_rebind,
            searchesRetained=sum(int((search_reports.get((arm, 18, 1)) or {}).get('admitted') or 0)
                                 for arm in ARMS),
            holdoutsAdmittedBeforeResume=0,
            expectedFinal=CURRENT_RESUME_EXPECTED_FINAL, hardCeiling=plan['budget']['hardCeiling'],
            note='same comparison-v6 plan/ledger/output: the e18r1 searches are reused from the saved '
                 'artifacts, their already-issued holdout pairs are adopted (never reissued), and the '
                 'nine untouched pairs run normally; no failed pair is re-run and no budget is granted')
    elif checkpoint_resume:
        # Fail closed on the bounded checkpoint accounting: 1492 regular + 13 setup carried, the
        # interrupted legacy e0r2 search kept as a 128 upper-bound charge, 768 adopted battles and
        # 7 untouched pairs x 384 gives a 4193 maximum (<= 4976). Underexecution and
        # withheld holdouts must reconcile exactly; they must never be invented as spent work.
        committed = int(ledger.spent) + int(ledger.setup_spent)
        results['finalAccounting'] = reconcile_detached_final(
            results['ledger'], search_reports, detached, plan)
        results['detachedCheckpoint'] = dict(
            priorSpent=detached['priorLedger']['spent'],
            priorSetup=detached['priorLedger']['setupSpent'],
            abandoned=detached['abandon'], completedPairs=detached['completedPairs'],
            adoptedBattles=detached['adoptedBattles'], unknownCharge=detached['unknownCharge'],
            untouchedPairs=detached['untouchedPairs'], failedPairs=detached['failedPairs'],
            observerRebind=observer_rebind, recoveryPath=detached['recoveryPath'],
            abandonAppliedNow=detached['abandonAppliedNow'],
            abandonAlreadyApplied=detached['abandonAlreadyApplied'],
            expectedFinal=DETACHED_FINAL_CHARGED, hardCeiling=plan['budget']['hardCeiling'],
            conclusive=False, complete=False,
            note='bounded detached checkpoint of the stopped comparison-v6 run: the two fully '
                 'completed pairs (e18r1, e19r1) are adopted verbatim (search AND holdout, never '
                 're-nominated or reissued) and the seven untouched pairs run normally; the '
                 'interrupted legacy e0r2 search is abandoned with its reserved 128 kept as a '
                 'conservative upper bound, so this is NOT a full conclusive benchmark')
    results['incompleteUnits'] = sorted([list(item) for item in incomplete_units])
    failed_records = _carryforward_failed_records(carryforward)
    results['failedUnits'] = [dict(record, compute='prior') for record in failed_records]
    new_battles = sum(int(step.get('admitted') or 0) for step in results['steps']
                      if step.get('op') in ('search', 'holdout'))
    results['computeAccounting'] = dict(
        note='prior = completed in the stopped run and charged once (never re-run); new = compute '
             'performed in this continuation; adopted stage/timing costs are retained on each prior '
             'unit and worker/parent CPU is observer-only, never a learning feature',
        prior=dict(adoptedBattles=(carryforward or {}).get('adoptedBattles'),
                   failedPairBattles=(None if not failed_records else
                                      sum(int(record['legacyAdmitted'])
                                          + int(record['newAdmitted'])
                                          for record in failed_records)),
                   chargedTotal=(carryforward or {}).get('priorTotal')),
        new=dict(battles=new_battles))
    if checkpoint_resume:
        # The unknown charge is NOT an observed run: it is reported separately as an upper bound so
        # the actual new-battle count is never conflated with the abandoned reservation.
        results['computeAccounting']['unknownCharge'] = dict(
            stage=DETACHED_ABANDON_STAGE, reservationId=DETACHED_ABANDON_RESERVATION,
            chargedUpperBound=DETACHED_ABANDON_CAP, actual=None, unknownAdmission=True,
            note='the interrupted case is a conservative upper-bound charge, not an observed run; '
                 'it is excluded from the actual new-battle count')
        # The prior block describes THIS checkpoint's carried state, not the earlier v5 continuation.
        results['computeAccounting']['prior'] = dict(
            adoptedBattles=detached['adoptedBattles'],
            adoptedNote='the two fully completed pairs (e18r1, e19r1), search AND holdout, are '
                        'adopted verbatim and charged once; never re-run, re-nominated or reissued',
            carriedRegular=detached['priorLedger']['spent'],
            carriedSetup=detached['priorLedger']['setupSpent'],
            chargedTotal=detached['priorLedger']['spent'] + detached['priorLedger']['setupSpent'],
            failedPairBattles=(None if not failed_records else
                               sum(int(record['legacyAdmitted']) + int(record['newAdmitted'])
                                   for record in failed_records)))
    results['summary'] = cross_arm_summary(results['units'], plan, incomplete=results['incomplete'],
                                           incomplete_units=incomplete_units)
    results['throughputComparison'] = matched_infrastructure_comparison(results['units'])
    results['finishedAt'] = now()
    write_json(out_root / 'results.json', results)
    return results


# --------------------------------------------------------------------------------------------------
# selftest (no battles, no live library, synthetic baseline only)
# --------------------------------------------------------------------------------------------------
def _fixture_scenario(encounter_id, tick_limit=30000):
    """A minimal but legal stored supplied scenario (labelled fixture values, not game facts)."""
    return dict(schema='ka-special-combat-research-1', encounterId=int(encounter_id), defeatCount=0,
                mathSeed=7, libSeed=8, tickLimit=tick_limit, finishPolicy='on-verdict',
                holyHerbStock=0, inputs=[],
                startProfile=dict(kind='isolated-scene0', enemySpawnCell=[0, 0], bossCell=None,
                                  startingStatus={}),
                ownUnits=[dict(
                    name='fixture fighter', human=True, monsterId=None, weaponId=0, equipment=[],
                    visitor=False, leaderIdentity=False, skills=[26, 25], invocationLevels=[1, 1],
                    parameters={p: dict(rawValue=1, rawMax=1 if p in (10, 11) else 2147483647,
                                        extraValue=0, extraMax=0, trainingLevel=123)
                                for p in range(10, 41)})],
                note='Synthetic benchmark fixture; test values, not game facts.')


def _synthetic_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    baseline = root / 'baseline.sqlite'
    if baseline.exists():
        baseline.unlink()
    db = sqlite3.connect(str(baseline))
    db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
            source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
            defeat INTEGER NOT NULL, region TEXT NOT NULL);
        CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
            result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal));
        CREATE TABLE evidence(candidate TEXT, phase TEXT, ordinal INTEGER, seeds TEXT,
            verdict INTEGER, censored INTEGER, prizeCallbacks INTEGER, awardedChests INTEGER,
            awardedBasis TEXT, pendingChests INTEGER);''')
    meta = dict(schema=1, objectiveVersion=3, searchSpaceVersion=4, totalRuns=1000,
                provenance=dict(digest='fixture-digest', files={}, count=0, missing=[], mode='fixture'),
                mpRecoverySetting=dict(enabled=False, stock=0), freshRootIndex={})
    for key, value in meta.items():
        db.execute('INSERT INTO meta VALUES (?,?)', (key, canonical(value)))
    for candidate_id, encounter, created in (('c0', 19, 0), ('c1', 19, 1), ('e0', 0, 2),
                                             ('e15', 15, 3), ('e18', 18, 4)):
        db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                   (candidate_id, canonical(_fixture_scenario(encounter)), 'fixture', 'supplied',
                    '{}', created))
        db.execute('INSERT INTO candidate_meta VALUES (?,?,?,?)', (candidate_id, encounter, 0, 'r'))
    db.commit()
    db.close()
    evidence = root / 'enemy-combat-roles.json'
    evidence.write_text(json.dumps({'encounters': [
        dict(encounterId=0, title='fixture short cure', fighters=[{}] * 6, healerCount=4),
        dict(encounterId=18, title='fixture eighteen', fighters=[{}] * 16, healerCount=0),
        dict(encounterId=19, title='fixture nineteen', fighters=[{}] * 21, healerCount=0)]}),
        encoding='utf-8')
    return baseline, evidence


def selftest(work=None):
    checks = []

    def check(name, condition, detail=''):
        checks.append(dict(check=name, ok=bool(condition), detail=str(detail)))

    def refuse(call):
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    def make_pool_module():
        class Pool:
            def __init__(self):
                self.calls = []

            def submit(self, function, scenario, seeds):
                self.calls.append(('submit', [list(seeds)]))
                return dict(seeds=list(seeds))

            def submit_batch(self, function, scenario, seed_pairs):
                self.calls.append(('batch', [list(pair) for pair in seed_pairs]))
                return [dict(seeds=list(pair)) for pair in seed_pairs]

        class Module:
            HeadlessPool = Pool
        return Module

    work = Path(work or (REPO / 'tmp' / 'encounter-redesign-20260928' / 'selftest'))
    baseline, evidence = _synthetic_fixture(work)
    budget = build_budget(4, 3)
    check('budget-matches-command-skeleton', budget['plannedBattles'] == 4944,
          f"planned={budget['plannedBattles']}")
    check('budget-setup-capped', budget['setupBattles'] <= budget['setupCap'])
    check('budget-hard-ceiling', budget['hardCeiling'] == 4944 + budget['setupCap'])
    check('budget-global-cap', budget['globalCap'] == budget['hardCeiling'])
    check('budget-setup-only-if-used', budget.get('setupOnlyIfUsed') is True)

    ledger_path = work / 'ledger.json'
    if ledger_path.exists():
        ledger_path.unlink()
    ledger = BudgetLedger(ledger_path, 20, setup_cap=5)
    reservation = ledger.reserve('search', 12)
    check('ledger-reserve', ledger.spent == 12 and ledger.remaining == 8)
    check('ledger-refuses-global-overrun',
          isinstance(refuse(lambda: ledger.reserve('other', 9)), Blocked))
    ledger.settle(reservation, 7)
    check('ledger-settles-actual', ledger.spent == 7 and ledger.remaining == 13)
    ledger.charge_setup('calibration', 5)
    check('ledger-setup-cap', ledger.setup_spent == 5 and ledger.remaining == 8)
    check('ledger-refuses-setup-overrun',
          isinstance(refuse(lambda: ledger.charge_setup('controls', 1)), Blocked))
    resumed = BudgetLedger(ledger_path, 20, setup_cap=5)
    check('ledger-persists-across-resume', resumed.spent == 7 and resumed.setup_spent == 5)

    meter = ExecutionMeter(128, stage='transport')
    pause = []
    module = make_pool_module()
    uninstall = install_bounded_dispatch(meter, pool_module=module,
                                         pause_enqueue=lambda: pause.append('pause'))
    pool = module.HeadlessPool()
    submitted = 0
    denied = False
    try:
        while True:
            pool.submit_batch(None, {}, [[1, index] for index in range(8)])
            submitted += 8
    except BudgetExhausted:
        denied = True
    check('transport-exact-cap', meter.charged == 128 and submitted == 128, meter.snapshot())
    check('transport-denies-beyond', denied and meter.denied >= 1)
    check('transport-pause-fired-once', len(pause) == 1)
    uninstall()
    module2 = make_pool_module()
    meter2 = ExecutionMeter(5, stage='clamp')
    install_bounded_dispatch(meter2, pool_module=module2)
    pool2 = module2.HeadlessPool()
    pool2.submit_batch(None, {}, [[1, index] for index in range(8)])
    check('transport-clamps-batch', meter2.charged == 5 and len(pool2.calls[0][1]) == 5)

    pairs = derive_pairs('abc123', 'holdout', 5)
    check('pairs-deterministic', pairs == derive_pairs('abc123', 'holdout', 5))
    check('pairs-distinct', len(set(pairs)) == 5)
    check('pairs-int31', all(0 <= a <= SEED_MAX and 0 <= b <= SEED_MAX for a, b in pairs))

    registry_path = work / 'registry.json'
    if registry_path.exists():
        registry_path.unlink()
    registry = HoldoutRegistry(registry_path, 'pid', 'nonce')
    first = registry.issue('holdout|0|1', 6, forbidden={(1, 2)})
    check('registry-rejects-forbidden', (1, 2) not in first)
    check('registry-int31', all(0 <= a <= SEED_MAX and 0 <= b <= SEED_MAX for a, b in first))
    check('registry-refuses-reissue',
          isinstance(refuse(lambda: HoldoutRegistry(registry_path, 'pid', 'nonce')
                            .issue('holdout|0|1', 6, forbidden=set())), Blocked))
    second = registry.issue('holdout|0|2', 6, forbidden={(1, 2)})
    check('registry-global-distinct', not (set(first) & set(second)))
    fresh_path = work / 'registry-fresh.json'
    if fresh_path.exists():
        fresh_path.unlink()
    fresh = HoldoutRegistry(fresh_path, 'pid', 'nonce')
    check('registry-deterministic', fresh.issue('holdout|0|1', 6, forbidden={(1, 2)}) == first)

    boot = paired_bootstrap([1, 2, 3, -1, 0], resamples=500, seed=7)
    check('bootstrap-mean', abs(boot['mean'] - 1.0) < 1e-9)
    check('bootstrap-ci-ordered', boot['ci95'][0] <= boot['ci95'][1])
    check('bootstrap-empty', paired_bootstrap([], resamples=10)['mean'] is None)

    nominee, detail = select_nominee([dict(candidateId='a', meanEarned=None, bestEarned=999),
                                      dict(candidateId='b', meanEarned=2.0)])
    check('nominee-best-mean-not-jackpot', nominee == 'b' and not detail['noEligible'])
    none_nominee, none_detail = select_nominee([dict(candidateId='a', meanEarned=None)])
    check('nominee-explicit-no-eligible', none_nominee is None and none_detail['noEligible'])

    proposal, blocks = resolve_representatives(work, explicit=None)
    check('representatives-block-when-unresolved', bool(blocks))
    short = next(row for row in proposal if row['role'] == 'short-cure-heavy')
    check('representatives-derive-short-cure', short['id'] == 0, short['basis'])
    resolved, resolved_blocks = resolve_representatives(work, explicit='0,15,18,19')
    check('representatives-explicit-clean', not resolved_blocks and len(resolved) == 4)

    plan = build_plan(_Args(baseline=str(baseline), evidence=str(work), encounters='0,15,18,19'),
                      python=default_python())
    errors = [e for e in validate_plan(plan) if 'open items' not in e]
    check('plan-validates', not errors, '; '.join(errors))
    check('plan-total-4944', plan['budget']['plannedBattles'] == 4944)
    check('plan-scope-record', isinstance(plan.get('scope'), dict) and 'tickLimit' in plan['scope'])
    tampered = json.loads(json.dumps(plan))
    tampered['replicates'] = 99
    check('plan-detects-tampering', bool(validate_plan(tampered)))
    check('plan-baseline-binding',
          plan['baseline']['storedProvenanceDigest'] == 'fixture-digest'
          and plan['baseline']['candidateCount'] == 5)
    steps = plan_steps(plan)
    ordered = True
    for index, step in enumerate(steps):
        if step['op'] != 'holdout':
            continue
        frozen = {row['arm'] for row in steps[:index] if row['op'] == 'freeze'
                  and row['encounterId'] == step['encounterId']
                  and row['replicate'] == step['replicate']}
        ordered = ordered and frozen == set(ARMS)
    check('plan-holdout-after-both-freezes', ordered)

    samples = [
        dict(seeds=[1, 2], verdict=2, censored=False,
             rewardOutcome=dict(pendingChests=0, awardedChests=0, awardedBasis='native-win-loss-gate')),
        dict(seeds=[3, 4], verdict=1, censored=False,
             rewardOutcome=dict(pendingChests=3, awardedChests=3,
                                awardedBasis='reward-entitlement-certificate')),
        dict(seeds=[5, 6], verdict=1, censored=False,
             rewardOutcome=dict(pendingChests=1, awardedChests=None,
                                awardedBasis='unknown-win-without-certificate')),
        dict(seeds=[7, 8], verdict=1, error='boom', censored=False,
             rewardOutcome=dict(pendingChests=2, awardedChests=2,
                                awardedBasis='reward-entitlement-certificate')),
    ]
    rows = [dict(candidateId='c0', encounterId=19, seeds=s['seeds'], raw=s) for s in samples]
    applied, counts = apply_canonical(rows, dict(finishPolicy=None))
    check('canonical-total-4', counts['total'] == 4)
    check('canonical-loss-zero', applied[0]['outcome']['finalEarned'] == 0 and
          applied[0]['outcome']['status'] == 'loss')
    check('canonical-certified', applied[1]['outcome']['finalEarned'] == 3 and
          applied[1]['outcome']['status'] == 'certified')
    check('canonical-unknown-not-zero', applied[2]['outcome']['finalEarned'] is None)
    check('canonical-error-kept', counts['error'] == 1 and counts['unresolved'] >= 2)
    cells = earned_cells(applied)
    check('canonical-cells-none-for-unknown',
          cells[('c0', (5, 6))] is None and cells[('c0', (1, 2))] == 0)

    probe = probe_interface(default_python(), str(HERE))
    check('interface-probe-runs', probe.get('ok') is True or 'error' in probe, probe.get('error', ''))
    check('interface-probe-reports-coordinator',
          bool(probe.get('coordinatorWired')) if probe.get('ok') else True)

    ok = all(row['ok'] for row in checks)
    return dict(ok=ok, checks=checks,
                note='mechanical checks only; no battle is run and no live library is opened')


class _Args:
    def __init__(self, **overrides):
        frozen = (r'C:\Users\anisb\Documents\Codex\2026-09-28'
                  r'\deepastra-handoff-community-first-optimiser-implementation'
                  r'\work\encounter-redesign')
        defaults = dict(
            baseline=(os.environ.get('KA_BENCH_BASELINE') or (frozen + r'\baseline.sqlite')),
            evidence=(os.environ.get('KA_BENCH_EVIDENCE')
                      or r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy'
                         r'\RE-evidence\20260912-combat'),
            legacy_source=(os.environ.get('KA_BENCH_LEGACY')
                           or (frozen + r'\legacy-source\tools\recovery')),
            current_source=str(HERE), encounters='0,15,18,19', replicates=3, search_battles=128,
            holdout_pairs=64, throughput_battles=64, seed_root='encounter-redesign-20260928',
            holdout_nonce='encounter-redesign-holdout-v1',
            evidence_mode='reuse', reliability=0.9, bootstrap=10000,
            hash_baseline=False)
        defaults.update(overrides)
        self.__dict__.update(defaults)


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def cmd_write_plan(args):
    plan = build_plan(args, python=args.python)
    path = write_json(args.out, plan, immutable=not args.force)
    print(json.dumps(dict(ok=True, plan=str(path), digest=plan['digest'],
                          plannedBattles=plan['budget']['plannedBattles'],
                          hardCeiling=plan['budget']['hardCeiling'], openItems=plan['openItems']),
                     indent=2))
    return 0


def cmd_validate_plan(args):
    plan = read_json(args.plan)
    errors = validate_plan(plan, check_baseline=args.check_baseline)
    payload = dict(ok=not errors, errors=errors, digest=plan.get('digest'))
    if args.preflight:
        payload['preflight'] = preflight(plan, python=args.python)
        payload['ok'] = payload['ok'] and not payload['preflight']['blocked']
    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


def cmd_run_plan(args):
    if not args.authorised:
        raise Blocked('run-plan requires --authorised (Astra authorises the run, not the harness)')
    plan = read_json(args.plan)
    stages = tuple(args.stage) if args.stage else ('smoke', 'search', 'holdout', 'throughput')
    # ``run_units`` -> ``prepare_carryforward_v5`` -> ``check_reviewed_source_changes`` expects the
    # manifest DICT, not the CLI path string (``cmd_prepare_continuation`` parses it the same way). Bind
    # it here so a v5 continuation fails on its real contents, never on a wrong argument type before
    # dispatch.
    manifest_path = getattr(args, 'reviewed_source_manifest', None)
    reviewed_source_manifest = read_json(manifest_path) if manifest_path else None
    results = run_units(plan, args.python, args.out, stages=stages, authorise=args.authorised,
                        workers=args.workers, plan_path=args.plan,
                        resume_from=getattr(args, 'resume_from', None),
                        reviewed_source_manifest=reviewed_source_manifest)
    summary = results['summary']
    print(json.dumps(dict(ok=True, out=str(args.out), units=len(results['units']),
                          decisive=summary['decisive'], unresolved=summary['unresolved'],
                          incomplete=summary.get('incomplete', False))))
    return 0


def cmd_resume_current(args):
    """Continue the SAME stopped plan/output offline: adopt saved searches, run only what remains.

    Requires the exact same plan.json and output directory of an unfinished run whose prior ledger is
    already charged. It never re-runs a saved search, never reissues an already-issued holdout pair,
    never re-runs the permanently failed pairs and never grants budget; it aborts on any unknown or
    partially admitted holdout. No battle starts until the fail-closed state check passes.
    """
    if not args.authorised:
        raise Blocked('resume-current requires --authorised (root authorises the run, not the harness)')
    plan = read_json(args.plan)
    results = run_units(plan, args.python, args.out, stages=('search', 'holdout'),
                        authorise=True, workers=args.workers, plan_path=args.plan,
                        current_resume=True)
    summary, ledger = results['summary'], results['ledger']
    print(json.dumps(dict(ok=True, out=str(args.out), units=len(results['units']),
                          decisive=summary['decisive'], unresolved=summary['unresolved'],
                          incomplete=summary.get('incomplete', False),
                          currentResume=results.get('currentResume'),
                          ledger=dict(spent=ledger['spent'], setupSpent=ledger['setupSpent'],
                                      committed=ledger['committed'], remaining=ledger['remaining']))))
    return 0


def cmd_resume_detached(args):
    """Bounded, exact-validated checkpoint continuation of the stopped comparison-v6 run.

    It adopts the two fully completed pairs (search AND holdout) verbatim, abandons the interrupted
    legacy e0r2 search permanently at its already-reserved 128 upper bound (no refund, no fabricated
    actual), and runs ONLY the seven untouched pairs. Requires --authorised; it is a separate,
    explicit checkpoint mode - never a generic resume - and it never claims a full conclusive result.
    """
    if not args.authorised:
        raise Blocked('resume-detached requires --authorised (root authorises the run, not the harness)')
    plan = read_json(args.plan)
    results = run_units(plan, args.python, args.out, stages=('search', 'holdout'),
                        authorise=True, workers=args.workers, plan_path=args.plan,
                        checkpoint_resume=True)
    summary, ledger = results['summary'], results['ledger']
    print(json.dumps(dict(ok=True, out=str(args.out), units=len(results['units']),
                          decisive=summary['decisive'], unresolved=summary['unresolved'],
                          incomplete=summary.get('incomplete', False),
                          detachedCheckpoint=results.get('detachedCheckpoint'),
                          ledger=dict(spent=ledger['spent'], setupSpent=ledger['setupSpent'],
                                      committed=ledger['committed'], remaining=ledger['remaining']))))
    return 0


def cmd_prepare_continuation(args):
    """Validate a continuation against the stopped run and preflight it. No battles, no run.

    This is the fail-closed preparation step root runs once the production source has settled and
    the reviewed-source-change manifest has been authored: it validates the prior plan/ledger/
    artifacts, binds the manifest, adopts the completed stages into the output dir (hash-bound),
    writes the continuation receipt and runs the no-battle preflight gates. It never authorises a
    run and never starts a battle.
    """
    plan = read_json(args.plan)
    manifest = read_json(args.reviewed_source_manifest) if args.reviewed_source_manifest else None
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    receipt, _index = prepare_carryforward(plan, args.resume_from, out_root,
                                           reviewed_source_manifest=manifest)
    gates = preflight(plan, python=args.python, out_root=out_root)
    write_json(out_root / 'preflight.json', gates)
    payload = dict(ok=not gates['blocked'], out=str(out_root), schema=receipt.get('schema'),
                   priorTotal=receipt.get('priorTotal'), expectedTotal=receipt.get('expectedTotal'),
                   hardCeiling=receipt.get('hardCeiling'),
                   untouchedPairs=len(receipt.get('untouchedPairs') or []),
                   failedPairs=len(_carryforward_failed_records(receipt)),
                   reviewedSourceChanges=receipt.get('reviewedSourceChanges'),
                   blocked=gates['blocked'])
    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


def cmd_arm(args):
    if not args.authorised:
        raise Blocked('arm requires --authorised; the harness never starts a battle by itself')
    plan = read_json(args.plan)
    # Validate this arm's frozen source inventory against its live tree BEFORE any battle: a source
    # change after the plan fails closed here, per arm, not only in the parent preflight.
    frozen = ((plan.get('arms') or {}).get(args.arm) or {}).get('sourceInventory') or {}
    live_policy = probe_arm_policy(sys.executable, args.source_root, plan['baseline']['path'],
                                   [row['id'] for row in plan['encounters'] if row['id'] is not None])
    problems = validate_source_inventory(frozen, live_policy, arm=args.arm)
    frozen_impl = ((plan.get('arms') or {}).get(args.arm) or {}).get('implementationInventory') or {}
    live_impl = _implementation_inventory_record(
        implementation_inventory_files(args.source_root),
        canonical_files=(live_policy.get('files') or {}), observer=observer_inventory())
    problems = problems + validate_implementation_inventory(frozen_impl, live_impl, arm=args.arm)
    if problems:
        raise Blocked('arm source changed after the plan: ' + '; '.join(problems))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(args.workers or 4))
    cap = int(args.cap or 0)
    if args.stage == 'smoke':
        meter = ExecutionMeter(cap or int(plan['budget']['stages']['smoke']['battles']) // len(ARMS),
                               stage='smoke')
        payload = arm_smoke(plan, args.arm, args.source_root, out_dir, meter, workers)
        summary = dict(ok=True, arm=args.arm, stage='smoke', cap=meter.cap, admitted=meter.charged,
                       rowsFile=str(out_dir / f'raw-smoke-{args.arm}.jsonl'))
    elif args.stage == 'search':
        payload = search_stage(plan, args.arm, args.source_root, args.copy, out_dir,
                               encounter_id=args.encounter, replicate=args.replicate,
                               seconds=args.seconds, workers=workers, cap=(cap or None),
                               migration_plan_id=args.migration_plan_id)
        first = (payload.get('encounters') or [{}])[0]
        summary = dict(ok=True, arm=args.arm, stage='search', cap=payload['declared'],
                       admitted=payload['admitted'], denied=payload['denied'],
                       incomplete=payload['incomplete'], observedRuns=first.get('observedRuns'),
                       nomination=payload['nomination'], usedPairs=payload['usedPairs'],
                       out=str(out_dir))
    elif args.stage == 'holdout':
        pairs = parse_seed_pairs(json.loads(args.pairs))
        payload = holdout_stage(plan, args.arm, args.source_root, args.copy, out_dir,
                                encounter_id=args.encounter, replicate=args.replicate, pairs=pairs,
                                nominee=args.nominee, seconds=args.seconds, workers=workers)
        summary = dict(ok=True, arm=args.arm, stage='holdout', cap=payload['declared'],
                       admitted=payload['admitted'], incomplete=payload['incomplete'],
                       rowsFile=str(out_dir / f'raw-holdout-{args.arm}-e{args.encounter}'
                                             f'-r{args.replicate}.jsonl'))
    elif args.stage == 'throughput':
        meter = ExecutionMeter(cap or int(args.battles or 0), stage=f'throughput:{args.mode}')
        payload = throughput_stage(plan, args.arm, args.source_root, out_dir, meter, workers,
                                   args.mode, args.battles, copy_path=args.copy)
        summary = dict(ok=True, arm=args.arm, stage='throughput', mode=args.mode,
                       admitted=payload['admitted'])
    else:
        raise HarnessError(f'unknown stage {args.stage!r}')
    summary['detail'] = payload.get('stage')
    print(json.dumps(summary))
    return 0


def cmd_selftest(args):
    payload = selftest()
    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


def _common_plan_args(parser):
    parser.add_argument('--baseline', default=_Args().baseline)
    parser.add_argument('--evidence', default=_Args().evidence)
    parser.add_argument('--legacy-source', default=_Args().legacy_source)
    parser.add_argument('--current-source', default=_Args().current_source)
    parser.add_argument('--encounters', default=None,
                        help='comma-separated representative encounter ids; required for a runnable plan')
    parser.add_argument('--replicates', type=int, default=3)
    parser.add_argument('--search-battles', type=int, default=128)
    parser.add_argument('--holdout-pairs', type=int, default=64)
    parser.add_argument('--holdout-nonce', default='encounter-redesign-holdout-v1',
                        help='reproducible global nonce label for holdout pair derivation')
    parser.add_argument('--throughput-battles', type=int, default=64)
    parser.add_argument('--seed-root', default='encounter-redesign-20260928')
    parser.add_argument('--evidence-mode', choices=('reuse', 'priors-only'), default='reuse')
    parser.add_argument('--reliability', type=float, default=0.9)
    parser.add_argument('--bootstrap', type=int, default=10000)
    parser.add_argument('--hash-baseline', action='store_true')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--python', default=default_python())
    sub = parser.add_subparsers(dest='command', required=True)

    writer = sub.add_parser('write-plan', help='write the immutable predeclared plan (no battles)')
    _common_plan_args(writer)
    writer.add_argument('--out', required=True)
    writer.add_argument('--force', action='store_true')
    writer.set_defaults(func=cmd_write_plan)

    validator = sub.add_parser('validate-plan', help='re-derive the digest and check the budget math')
    validator.add_argument('--plan', required=True)
    validator.add_argument('--check-baseline', action='store_true')
    validator.add_argument('--preflight', action='store_true')
    validator.set_defaults(func=cmd_validate_plan)

    runner = sub.add_parser('run-plan', help='preflight gates then run the predeclared stages')
    runner.add_argument('--plan', required=True)
    runner.add_argument('--out', required=True)
    runner.add_argument('--stage', action='append',
                        choices=('smoke', 'search', 'holdout', 'throughput'), default=None)
    runner.add_argument('--authorised', action='store_true')
    runner.add_argument('--workers', type=int, default=4)
    runner.add_argument('--resume-from', default=None,
                        help='content-detected continuation of ONE stopped run: either the early '
                             'recovery (no prior search/holdout; adopts the completed smoke/telemetry) '
                             'the v4 continuation (a prior e0r1 search pair; adopts the completed '
                             'stages, skips the permanently failed pair and runs the 11 untouched '
                             'pairs) or the v5 continuation (carried failed-pair charges; skips the '
                             'permanently failed e0r1 and e15r1 pairs and runs the 10 untouched pairs '
                             'under a reviewed-source-change manifest). Validated fail-closed from '
                             'the prior plan/ledger/artifacts, never from the directory name')
    runner.add_argument('--reviewed-source-manifest', default=None,
                        help='root-authored manifest binding the exact old/new SHA256 of each '
                             'allowed NEW-arm non-combat history/seed source change; required for '
                             'a v5 continuation')
    runner.set_defaults(func=cmd_run_plan)

    resumer = sub.add_parser('resume-current',
                             help='continue the SAME stopped plan/output in place (no re-run of '
                                  'saved searches or completed battles)')
    resumer.add_argument('--plan', required=True)
    resumer.add_argument('--out', required=True)
    resumer.add_argument('--authorised', action='store_true')
    resumer.add_argument('--workers', type=int, default=4)
    resumer.set_defaults(func=cmd_resume_current)

    detached = sub.add_parser('resume-detached',
                              help='bounded exact-validated checkpoint of the stopped comparison-v6 '
                                   'run: adopt the two completed pairs, abandon the interrupted e0r2 '
                                   'search at its reserved upper bound, run only the seven untouched '
                                   'pairs (never a full conclusive benchmark)')
    detached.add_argument('--plan', required=True)
    detached.add_argument('--out', required=True)
    detached.add_argument('--authorised', action='store_true')
    detached.add_argument('--workers', type=int, default=4)
    detached.set_defaults(func=cmd_resume_detached)

    preparer = sub.add_parser('prepare-continuation',
                              help='no-battle validation + preflight of a stopped-run continuation')
    preparer.add_argument('--plan', required=True)
    preparer.add_argument('--resume-from', required=True)
    preparer.add_argument('--out', required=True)
    preparer.add_argument('--reviewed-source-manifest', default=None)
    preparer.set_defaults(func=cmd_prepare_continuation)

    arm = sub.add_parser('arm', help='internal: run one arm stage in its own subprocess')
    arm.add_argument('--plan', required=True)
    arm.add_argument('--arm', choices=ARMS, required=True)
    arm.add_argument('--stage', choices=('smoke', 'search', 'holdout', 'throughput'), required=True)
    arm.add_argument('--source-root', required=True)
    arm.add_argument('--copy')
    arm.add_argument('--out', required=True)
    arm.add_argument('--workers', type=int, default=4)
    arm.add_argument('--replicate', type=int, default=1)
    arm.add_argument('--cap', type=int, default=0,
                     help='exact finite execution budget for this child phase')
    arm.add_argument('--encounter', type=int, default=None)
    arm.add_argument('--pairs', default=None, help='JSON list of [a,b] seed pairs for holdout')
    arm.add_argument('--nominee')
    arm.add_argument('--seconds', type=float, default=10800)
    arm.add_argument('--mode', default='telemetry-off')
    arm.add_argument('--battles', type=int, default=64)
    arm.add_argument('--migration-plan-id', default=None,
                     help='the reviewed observer migration planId; applied by the runtime on a '
                          'paused new-arm activation')
    arm.add_argument('--authorised', action='store_true')
    arm.set_defaults(func=cmd_arm)

    test = sub.add_parser('selftest', help='no-battle mechanical checks')
    test.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except HarnessError as exc:
        print(json.dumps(dict(ok=False, blocked=isinstance(exc, Blocked), error=str(exc))),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
