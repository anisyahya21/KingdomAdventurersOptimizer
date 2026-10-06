"""Shared, fail-closed global forbidden-seed-pair collector for the optimiser.

This is the one shared service the encounter coordinator should call exactly once before freezing a
confirmation, replacing the nominee/reference-only, fail-open historic read inside
``Coordinator._fresh_pairs``. That read looked only at the two compared candidates, silently
swallowed a failed or malformed library read, and could therefore hand a held-out pair that an
*eliminated* candidate had already used. This module instead collects the forbidden set globally and
refuses to return a partial answer.

    from strategy_seed_freshness import collect
    report = collect(connection, cache=None, revision='<engine revision>', timeout_seconds=30)
    report['pairs']        # read-only Set of ordered (a, b) tuples that must never be issued as fresh
    report['counts']       # truthful per-source history counts for the freshness report
    report['cacheKey']     # token identifying (revision, connection, data_version, total_changes)
    report['provenance']   # database identity + guard values actually used

Coverage (all ordered, ``(a, b)`` is never collapsed with ``(b, a)``):

  * every explicit compact ``evidence.seeds`` pair - the table that survives the rolling replay
    window, so a pair whose ``run`` ordinal was pruned is still forbidden;
  * the global deterministic ``seed_pair(phase, ordinal)`` banks, extended to the largest ordinal
    named by ANY candidate: ``MAX(ordinal)`` over both ``run`` and ``evidence`` per phase, plus the
    ``aggregate:<id>:<phase>`` counters, ``probeBanks``, ``fineTunePrograms``, ``averageBanks``, the
    breakthrough stream and the MP-recovery ledger (declared banks, expanded with the existing
    ``seed_pair`` law imported lazily from the current source);
  * every ``ea_sample`` / ``ea_sample_link`` / ``ea_holdout`` reservation or frozen holdout pair.

Fail-closed rules: the legacy tables ``candidate``/``run``/``evidence``/``meta`` are required (a
fixture without them raises ``Blocked``); optional ``ea_*`` tables may be legitimately missing on a
legacy library and then contribute nothing; a malformed pair, a bool or out-of-31-bit seed, a bad
declared bank, a read error or an exceeded deadline all raise instead of returning a smaller set.
Only ``SELECT``/aggregate/DISTINCT reads are issued - no write, no ``PRAGMA`` change - and the heavy
tables are streamed so no multi-million-row table is materialised in Python.

Claim scope: the returned set is every forbidden pair over *established* coverage in THIS library -
recorded evidence pairs, the enumerated deterministic banks up to the largest observed/declared
ordinal, and every ``ea_*`` row. It is not a proof that an older, pruned or never-recorded seed gap
is fresh: a caller may describe a drawn pair only as 'not colliding with established coverage',
never as universally fresh.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import OrderedDict
from collections.abc import MutableMapping, Set as AbstractSet

__all__ = ['collect', 'Blocked', 'FreshnessError']

INT31 = (1 << 31) - 1
PHASES = ('discovery', 'validation')
REQUIRED_TABLES = ('candidate', 'run', 'evidence', 'meta')
#: A compact summary library keeps candidate/meta plus these tables instead of run/evidence.
COMPACT_TABLE = 'history_seed_exclusion'
COMPACT_VERSION_KEY = 'historySummaryVersion'
SUPPORTED_SUMMARY_VERSIONS = (2,)
EA_PAIR_TABLES = ('ea_sample', 'ea_sample_link', 'ea_holdout')
DECLARED_BANK_KEYS = ('probeBanks', 'fineTunePrograms', 'averageBanks', 'breakthrough',
                      'mpRecoveryLedger')
#: A cold full scan of a multi-GiB library legitimately takes far longer than a warm one (a warm
#: ~4 GiB baseline completed once in ~21 s); the once-per-session initial scan therefore gets a
#: bounded but realistic budget. It stays finite and a read that cannot finish still fails closed.
DEFAULT_TIMEOUT_SECONDS = 120.0
_PROGRESS_INSTRUCTIONS = 200_000
_STREAM_CHUNK = 4096
_DEFAULT_CACHE_SIZE = 1


class FreshnessError(RuntimeError):
    """A freshness read that cannot be trusted; never a silently smaller forbidden set."""


class Blocked(FreshnessError):
    """The collector refused to answer (missing schema, malformed history, read error, deadline)."""


_clock = time.monotonic
_DEFAULT_CACHE: 'OrderedDict[str, dict]' = OrderedDict()
_BANK_CACHE: 'OrderedDict[tuple, SeedPairSet]' = OrderedDict()
_BANK_CACHE_SIZE = len(PHASES)


class SeedPairSet(AbstractSet):
    """Exact ordered seed-pair membership with one packed integer per pair.

    Both seeds are validated 31-bit non-negative integers, so ``(a, b)`` has a reversible 62-bit
    encoding. The public object is a read-only ``collections.abc.Set`` of ordinary tuple pairs;
    internally it avoids retaining a tuple and two Python integer objects for every bank entry.
    """

    __slots__ = ('_packed',)

    def __init__(self):
        self._packed = set()

    @staticmethod
    def _encode(a, b):
        return (int(a) << 31) | int(b)

    def _add(self, a, b):
        """Add a pair already validated by the collector."""
        self._packed.add(self._encode(a, b))

    def _update(self, pairs):
        """Add validated pairs, copying packed keys directly when possible."""
        if isinstance(pairs, SeedPairSet):
            self._packed.update(pairs._packed)
            return
        for a, b in pairs:
            self._add(a, b)

    def __contains__(self, pair):
        if not isinstance(pair, tuple) or len(pair) != 2:
            return False
        a, b = pair
        if (isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= INT31
                or isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= INT31):
            return False
        return self._encode(a, b) in self._packed

    def __iter__(self):
        return ((value >> 31, value & INT31) for value in self._packed)

    def __len__(self):
        return len(self._packed)


# --- validation ---------------------------------------------------------------------------------
def _fail(message):
    raise Blocked('freshness: ' + message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def _as_seed(value, *, source):
    """One ordered seed value: a real int in ``0..2**31-1``; a bool is not a seed."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f'non-integer seed {value!r} in {source}')
    if not 0 <= value <= INT31:
        _fail(f'seed {value!r} out of 31-bit range in {source}')
    return int(value)


def _as_count(value, *, source):
    """A non-negative integer ceiling/ordinal; a bool is not a count and out-of-range fails closed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f'non-numeric value {value!r} in {source}')
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f'non-finite value {value!r} in {source}')
    number = int(value)
    if not 0 <= number <= INT31:
        _fail(f'value {value!r} out of range in {source}')
    return number


def _parse_json(raw, *, source):
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise Blocked(f'freshness: malformed JSON in {source}: {raw!r}') from exc


def _as_mapping(raw, *, source):
    """A declared bank object: ``None`` reads as empty, malformed JSON or a non-mapping fails closed."""
    declared = _parse_json(raw, source=source)
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise Blocked(f'freshness: declared bank in {source} is not a mapping')
    return declared


def _parse_seed_pair(raw, *, source):
    """One stored ordered seed pair, or fail closed when the compact evidence is malformed."""
    values = _parse_json(raw, source=source)
    if not isinstance(values, list) or len(values) != 2:
        raise Blocked(f'freshness: malformed seed pair in {source}: {raw!r}')
    return (_as_seed(values[0], source=source), _as_seed(values[1], source=source))


# --- reads --------------------------------------------------------------------------------------
def _read_guard(connection, revision):
    """The connection identity/guard used to key the cache; a failed read fails closed."""
    try:
        databases = tuple((str(name), str(file))
                          for _seq, name, file in connection.execute('PRAGMA database_list'))
        data_version = int(connection.execute('PRAGMA data_version').fetchone()[0])
        total_changes = int(connection.total_changes)
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise Blocked(f'freshness: cannot read connection guard: {exc}') from exc
    return dict(revision=revision, databases=databases, dataVersion=data_version,
                totalChanges=total_changes, connection=id(connection))


def _table_names(connection):
    return {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _phase_max_ordinal(connection, table):
    """``{phase: max ordinal or None}`` in one aggregate read; the raw rows never enter Python."""
    out = {phase: None for phase in PHASES}
    for phase, value in connection.execute(
            f'SELECT phase, MAX(ordinal) FROM {table} GROUP BY phase'):
        phase = str(phase)
        if phase in out and value is not None:
            out[phase] = _as_count(value, source=f'{table}.ordinal')
    return out


def _declared_bank_ceilings(connection):
    """The declared-bank ceilings per source, read from ``meta`` in one bounded query."""
    placeholders = ','.join('?' * len(DECLARED_BANK_KEYS))
    rows = connection.execute(
        f"SELECT key, value FROM meta WHERE key LIKE ? OR key IN ({placeholders})",
        ('aggregate:%',) + DECLARED_BANK_KEYS).fetchall()
    ceilings = {'aggregate': {phase: 0 for phase in PHASES}, 'probeBanks': 0,
                'fineTunePrograms': 0, 'averageBanks': 0, 'breakthrough': 0,
                'mpRecoveryLedger': 0}
    for key, value in rows:
        key = str(key)
        if key.startswith('aggregate:'):
            phase = key.rsplit(':', 1)[-1]
            if phase not in PHASES:
                continue
            declared = _as_mapping(value, source=f'meta:{key}')
            count = declared.get('n')
            if count is None:
                continue
            ceilings['aggregate'][phase] = max(ceilings['aggregate'][phase],
                                               _as_count(count, source=f'meta:{key}'))
        elif key in ('probeBanks', 'averageBanks'):
            declared = _as_mapping(value, source=f'meta:{key}')
            for bank in declared.values():
                ceilings[key] = max(ceilings[key], _as_count(bank, source=f'meta:{key}'))
        elif key == 'fineTunePrograms':
            declared = _as_mapping(value, source='meta:fineTunePrograms')
            for program in declared.values():
                if not isinstance(program, dict):
                    continue
                budget = program.get('budget') or {}
                if isinstance(budget, dict):
                    ceilings[key] = max(ceilings[key], _as_count(budget.get('maxBank') or 0,
                                                                 source='meta:fineTunePrograms'))
                for point in (program.get('points') or {}).values():
                    if not isinstance(point, dict):
                        continue
                    for field in ('bank', 'runs', 'completedBank'):
                        ceilings[key] = max(ceilings[key], _as_count(point.get(field) or 0,
                                                                     source='meta:fineTunePrograms'))
        elif key == 'breakthrough':
            state = _as_mapping(value, source='meta:breakthrough')
            for record in (state.get('encounters') or {}).values():
                if not isinstance(record, dict):
                    continue
                for plan in record.get('plans') or []:
                    if not isinstance(plan, dict) or plan.get('status') != 'pending':
                        continue
                    bank = _as_count(plan.get('bank') or 0, source='meta:breakthrough')
                    candidates = [plan.get('parent')] + [arm.get('candidate')
                                                         for arm in plan.get('arms') or []
                                                         if isinstance(arm, dict)]
                    if bank and any(candidates):
                        ceilings[key] = max(ceilings[key], bank)
        elif key == 'mpRecoveryLedger':
            state = _as_mapping(value, source='meta:mpRecoveryLedger')
            for request in (state.get('requests') or {}).values():
                if not isinstance(request, dict) or request.get('status') != 'pending':
                    continue
                if request.get('child'):
                    ceilings[key] = max(ceilings[key], _as_count(request.get('bank') or 0,
                                                                 source='meta:mpRecoveryLedger'))
    return ceilings


def _seed_pair_law():
    """The current-source seed law ``(discovery_runs, validation_runs, measurement, seed_pair)``.

    Imported lazily so this module stays a light import; a missing law fails closed rather than
    silently contributing no deterministic banks. The native engine is irrelevant here.
    """
    try:
        from strategy_optimizer import DISCOVERY_RUNS, VALIDATION_RUNS, seed_pair
    except ImportError as exc:
        raise Blocked(f'freshness: seed_pair law unavailable: {exc}') from exc
    try:
        from strategy_sampling import MAX_MEASUREMENT_RUNS
    except Exception:  # strategy_sampling is an optional planning companion at this layer
        MAX_MEASUREMENT_RUNS = 65536
    return int(DISCOVERY_RUNS), int(VALIDATION_RUNS), int(MAX_MEASUREMENT_RUNS), seed_pair


def _bank_limits(run_max, evidence_max, ceilings):
    """The per-phase deterministic-bank ceilings: global ordinals, aggregate and declared banks."""
    discovery_runs, validation_runs, measurement, _seed_pair = _seed_pair_law()
    base = {'discovery': max(discovery_runs, measurement),
            'validation': max(validation_runs, measurement)}
    limits = {phase: max(base[phase], ceilings['aggregate'][phase]) for phase in PHASES}
    for phase in PHASES:
        for observed in (run_max.get(phase), evidence_max.get(phase)):
            if observed is not None:
                limits[phase] = max(limits[phase], observed + 1)
    limits['validation'] = max([limits['validation']]
                               + [ceilings[key] for key in DECLARED_BANK_KEYS if key != 'aggregate'])
    return limits


def _deterministic_pairs(limits, check):
    """``seed_pair(phase, i)`` for the whole per-phase range, cached by ``(phase, limit)``."""
    seed_pair = _seed_pair_law()[3]
    pairs = SeedPairSet()
    for phase in PHASES:
        limit = int(limits[phase])
        key = (phase, limit)
        cached = _BANK_CACHE.get(key)
        if cached is None:
            cached = SeedPairSet()
            for index in range(limit):
                a, b = seed_pair(phase, index)
                cached._add(_as_seed(a, source=f'seed_pair:{phase}'),
                            _as_seed(b, source=f'seed_pair:{phase}'))
                if (index & 0xFFF) == 0:
                    check(f'deterministic-bank:{phase}')
            _BANK_CACHE[key] = cached
            while len(_BANK_CACHE) > _BANK_CACHE_SIZE:
                _BANK_CACHE.popitem(last=False)
        else:
            _BANK_CACHE.move_to_end(key)
        pairs._update(cached)
        check(f'deterministic-bank:{phase}')
    return pairs


# --- cache --------------------------------------------------------------------------------------
def _cache_for(cache):
    if cache is None:
        return _DEFAULT_CACHE
    if not isinstance(cache, MutableMapping):
        raise Blocked('freshness: cache must be a mutable mapping or None')
    return cache


def _cache_lookup(cache, key, connection, guard):
    entry = cache.get(key)
    if entry is not None and entry.get('connection') is connection and entry.get('guard') == guard:
        if isinstance(cache, OrderedDict):
            cache.move_to_end(key)
        return entry
    return None


def _cache_store(cache, key, connection, guard, pairs, counts, provenance):
    # SeedPairSet has a read-only Set API, so the cache and report can safely share it without a
    # second hash table proportional to the full history.
    cache[key] = dict(connection=connection, guard=guard, pairs=pairs,
                      counts=counts, provenance=provenance)
    if cache is _DEFAULT_CACHE:
        while len(cache) > _DEFAULT_CACHE_SIZE:
            cache.popitem(last=False)


# --- compact summary library --------------------------------------------------------------------
def _compact_exclusions(connection, check):
    """Union an imported compact library's known exclusions and bank bounds, or fail closed.

    Only the version-stamped ``history_*`` seed tables are read: the exact stored exception pairs and
    one max bank ceiling per phase (never a sum of overlapping sources). Retained malformed seed data
    or an unknown/corrupt summary version raises ``Blocked`` - no smaller partial set is returned.
    Missing historical seed rows are *not* fatal: they are reported as partial coverage so the known
    exclusions stay usable without claiming global freshness. Returns exceptions and ceilings
    separately so the caller can combine all bank limits before one deterministic expansion.
    """
    row = connection.execute('SELECT value FROM meta WHERE key=?', (COMPACT_VERSION_KEY,)).fetchone()
    if row is None:
        raise Blocked('freshness: compact history library has no summary version stamp')
    try:
        version = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        raise Blocked(f'freshness: compact history summary version is unreadable: {exc}') from exc
    if version not in SUPPORTED_SUMMARY_VERSIONS:
        raise Blocked(f'freshness: unsupported compact history summary version {version!r}')

    pairs = SeedPairSet()
    ceiling_by_phase = {phase: 0 for phase in PHASES}
    missing = malformed = 0
    holdout = False
    for phase, ceiling, mal_c, miss, block in connection.execute(
            'SELECT phase,bank_ceiling,malformed_pairs,missing_seed_rows,holdout_blocked '
            'FROM history_seed_exclusion'):
        phase = str(phase)
        malformed += int(mal_c or 0)
        missing += int(miss or 0)
        holdout = holdout or bool(block)
        if phase in ceiling_by_phase and ceiling is not None:
            ceiling_by_phase[phase] = max(
                ceiling_by_phase[phase],
                _as_count(ceiling, source='history_seed_exclusion.bank_ceiling'))
        check('compact-exclusions')
    if malformed:
        raise Blocked('freshness: retained compact seed history is malformed; refusing a partial set')

    exceptions = 0
    for seed_a, seed_b in connection.execute('SELECT seed_a,seed_b FROM history_seed_exception'):
        pairs._add(_as_seed(seed_a, source='history_seed_exception'),
                   _as_seed(seed_b, source='history_seed_exception'))
        exceptions += 1
        check('compact-exceptions')

    return dict(pairs=pairs, version=version, ceilingByPhase=dict(ceiling_by_phase),
                deterministicPairs=0, exceptionPairs=exceptions,
                missingSeedRows=missing, holdoutBlocked=holdout)


# --- entry point --------------------------------------------------------------------------------
def collect(connection, *, cache=None, revision=None, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """Every seed pair forbidden as a fresh holdout, or ``Blocked`` - never a partial set.

    ``cache`` is an optional mutable mapping; a cache entry is reused only when the same connection
    object, the same supplied ``revision`` and the same ``data_version``/``total_changes`` guard are
    all seen again, so a foreign database, a mutated library or a different revision never reuses a
    stale set. ``timeout_seconds`` bounds the whole read.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or timeout_seconds <= 0:
        raise Blocked('freshness: timeout_seconds must be a positive number')
    if connection is None or not hasattr(connection, 'execute'):
        raise Blocked('freshness: a SQLite connection is required')
    deadline = _clock() + float(timeout_seconds)

    def check(where):
        if _clock() > deadline:
            raise Blocked(f'freshness: read deadline exceeded at {where}')

    guard = _read_guard(connection, revision)
    try:
        cache_key = hashlib.sha256(_canonical(guard).encode('utf-8')).hexdigest()
    except (TypeError, ValueError) as exc:
        raise Blocked(f'freshness: revision/guard is not serialisable: {exc}') from exc
    store = _cache_for(cache)
    entry = _cache_lookup(store, cache_key, connection, guard)
    if entry is not None:
        return dict(pairs=entry['pairs'], counts=dict(entry['counts']), cacheKey=cache_key,
                    provenance=dict(entry['provenance'], cached=True))

    aborted = {'deadline': False}

    def progress():
        if _clock() > deadline:
            aborted['deadline'] = True
            return 1
        return 0

    handler = getattr(connection, 'set_progress_handler', None)
    installed = False
    if callable(handler):
        try:
            connection.set_progress_handler(progress, _PROGRESS_INSTRUCTIONS)
            installed = True
        except Exception:  # noqa: BLE001 - a connection that refuses the bound keeps the checks
            installed = False
    try:
        check('tables')
        tables = _table_names(connection)
        base_missing = [name for name in ('candidate', 'meta') if name not in tables]
        if base_missing:
            raise Blocked(f'freshness: missing required schema: {base_missing}')
        live = all(name in tables for name in ('run', 'evidence'))
        compact = COMPACT_TABLE in tables
        if not live and not compact:
            raise Blocked('freshness: missing required schema: [\'run\', \'evidence\'] or '
                          f'[\'{COMPACT_TABLE}\']')

        pairs = SeedPairSet()
        evidence_pairs = 0
        run_max = {phase: None for phase in PHASES}
        evidence_max = {phase: None for phase in PHASES}
        ceilings = {'aggregate': {phase: 0 for phase in PHASES}, 'probeBanks': 0,
                    'fineTunePrograms': 0, 'averageBanks': 0, 'breakthrough': 0,
                    'mpRecoveryLedger': 0}
        limits = {phase: 0 for phase in PHASES}
        deterministic = SeedPairSet()
        compact_report = None
        if live:
            check('evidence')
            stream = connection.execute(
                'SELECT DISTINCT seeds FROM evidence WHERE seeds IS NOT NULL')
            while True:
                chunk = stream.fetchmany(_STREAM_CHUNK)
                if not chunk:
                    break
                for (raw,) in chunk:
                    a, b = _parse_seed_pair(raw, source='evidence.seeds')
                    pairs._add(a, b)
                    evidence_pairs += 1
                check('evidence-stream')

            check('ordinals')
            run_max = _phase_max_ordinal(connection, 'run')
            evidence_max = _phase_max_ordinal(connection, 'evidence')

            check('meta')
            ceilings = _declared_bank_ceilings(connection)
            limits = _bank_limits(run_max, evidence_max, ceilings)
        if compact:
            check('compact')
            compact_report = _compact_exclusions(connection, check)
            pairs._update(compact_report['pairs'])
            for phase, ceiling in compact_report['ceilingByPhase'].items():
                limits[phase] = max(limits.get(phase, 0), ceiling)

        # Combine the live and compact ceilings before expanding banks. This avoids building the
        # same deterministic prefix twice when both schemas are present in the current library.
        deterministic = SeedPairSet()
        if live or (compact and any(limits.values())):
            deterministic = _deterministic_pairs(limits, check)
            pairs._update(deterministic)

        check('ea')
        ea_counts = {}
        for table in EA_PAIR_TABLES:
            if table not in tables:
                ea_counts[table] = 0
                continue
            count = 0
            stream = connection.execute(f'SELECT seed_a, seed_b FROM {table}')
            while True:
                chunk = stream.fetchmany(_STREAM_CHUNK)
                if not chunk:
                    break
                for seed_a, seed_b in chunk:
                    pairs._add(_as_seed(seed_a, source=table), _as_seed(seed_b, source=table))
                    count += 1
                check(table)
            ea_counts[table] = count
    except sqlite3.OperationalError as exc:
        if aborted['deadline']:
            raise Blocked('freshness: read deadline exceeded') from exc
        raise Blocked(f'freshness: read failed: {exc}') from exc
    except sqlite3.Error as exc:
        raise Blocked(f'freshness: read failed: {exc}') from exc
    finally:
        if installed:
            try:
                connection.set_progress_handler(None, 0)
            except Exception:  # noqa: BLE001 - best-effort removal of our own bound
                pass

    compact_missing = compact_report['missingSeedRows'] if compact else 0
    compact_holdout = bool(compact_report['holdoutBlocked']) if compact else False
    partial = bool(compact and (compact_missing or compact_holdout))
    sources = []
    if live:
        sources += ['evidence.seeds', 'deterministic-seed_pair-banks']
    if compact:
        sources += ['history_seed_exclusion', 'history_seed_exception']
    sources += ['ea_sample/ea_sample_link/ea_holdout']
    coverage_note = ('forbidden pairs are those recorded in this library plus the deterministic '
                     'seed_pair banks up to the largest observed/declared ordinal; seed pairs from '
                     'ordinals that were pruned or never recorded cannot be enumerated and are NOT '
                     'claimed fresh')
    if partial:
        coverage_note += ('; the imported compact history covers only established/exception '
                          'coverage, so its seed claim is explicitly PARTIAL, never global '
                          'freshness')
    counts = dict(
        requiredTables=list(REQUIRED_TABLES),
        liveTables=bool(live),
        compactTables=bool(compact),
        summaryVersion=compact_report['version'] if compact else None,
        compactMissingSeedRows=compact_missing,
        compactHoldoutBlocked=compact_holdout,
        eaTablesPresent=[name for name in EA_PAIR_TABLES if name in tables],
        evidencePairs=evidence_pairs,
        runMaxOrdinal=run_max,
        evidenceMaxOrdinal=evidence_max,
        aggregateCeilings=dict(ceilings['aggregate']),
        declaredCeilings={key: ceilings[key] for key in DECLARED_BANK_KEYS if key != 'aggregate'},
        bankCeilings=dict(limits),
        deterministicPairs=len(deterministic),
        compactExceptionPairs=(compact_report['exceptionPairs'] if compact else 0),
        eaPairs=ea_counts,
        totalPairs=len(pairs),
        coverage=dict(
            scope='established-seed-history',
            sources=sources,
            partial=partial,
            missingSeedRows=compact_missing,
            holdoutBlocked=compact_holdout,
            note=coverage_note,
        ),
    )
    provenance = dict(
        collector='strategy_seed_freshness',
        database=guard['databases'][0][1] if guard['databases'] else None,
        databases=[list(item) for item in guard['databases']],
        revision=revision,
        dataVersion=guard['dataVersion'],
        totalChanges=guard['totalChanges'],
        eaTables=counts['eaTablesPresent'],
        compactTables=bool(compact),
        compactSummaryVersion=counts['summaryVersion'],
        partialCoverage=partial,
    )
    _cache_store(store, cache_key, connection, guard, pairs, counts, provenance)
    return dict(pairs=pairs, counts=counts, cacheKey=cache_key,
                provenance=dict(provenance, cached=False))
