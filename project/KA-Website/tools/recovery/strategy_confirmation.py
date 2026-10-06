"""Independent frozen-strategy confirmation against an existing optimiser library.

This is a separate confirmation work class. It re-measures already-frozen strategies on a fresh,
common, cryptographically drawn seed bank; it is not a search algorithm, it never writes to the
library it reads, and it is deliberately never called statistical certification.

    python strategy_confirmation.py --library LIB.sqlite --candidate ID [--candidate ID ...] \
        --runs N --workers W --output DIR

Library access is read-only (`mode=ro` + `PRAGMA query_only=ON`); a missing library is never
created. Before any evaluation the requested scenarios, the adapter provenance and the drawn seed
pairs are frozen into `DIR/manifest.json`, written once and never rewritten: a changed request
(`--runs`, candidates, library or provenance digest) is rejected and a resume must match it exactly.
The seed pairs are common across every candidate (so paired readings are matched) and are rejected
if they collide with any deterministic discovery/validation bank pair the library is authorised to
use for the requested candidates. Results stream into `DIR/confirmation.sqlite`; unresolved or
failed runs are stored as such and are never scored zero.

Summary statistics cover fully resolved earned readings only; missing/censored readings mark the
candidate incomplete and withhold a claimed confirmation. Any interval is an approximate
fixed-sample normal interval for one frozen sample, not a guarantee: repeated peeking is not a
certificate and comparing several candidates is not a simultaneous guarantee.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import secrets
import sqlite3
import sys
import time
from functools import lru_cache
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIRMATION_SCHEMA = 1
DEFAULT_SIMULATOR = 'strategy_optimizer_adapter:simulate'
RESOLVED, UNRESOLVED, FAILED = 'resolved', 'unresolved', 'failed'
SEED_BANK = 'confirmation-common-random-v1'
Z_95 = 1.96
INT31 = (1 << 31) - 1
CHUNK_ROWS = 256
POOL_AHEAD = 2


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


# --- library (read-only) -----------------------------------------------------------------------

def open_library_readonly(path):
    """A read-only SQLite connection; a missing library is an error, never a new file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'library not found: {path}')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def read_candidates(connection, ids):
    """The frozen scenarios for the requested ids; a missing or duplicated id is an error."""
    scenarios = {}
    for cid in ids:
        row = connection.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
        if row is None:
            raise ValueError(f'candidate not in library: {cid}')
        scenarios[cid] = json.loads(row['scenario'])
    return scenarios


PHASES = ('discovery', 'validation')


def _optional_query(connection, sql, params=()):
    """Rows from an optional library table; a fixture without it simply contributes nothing."""
    try:
        return list(connection.execute(sql, params))
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        return []


def evidence_seed_pairs(connection, ids):
    """The exact seed pairs the compact evidence table recorded for the requested candidates.

    The evidence rows survive the rolling replay-window prune that deletes the heavier `run` rows,
    so they are the strongest record of a pair a requested candidate actually used - including
    evicted ordinals the run table no longer names.
    """
    pairs = set()
    for cid in ids:
        for row in _optional_query(connection, 'SELECT seeds FROM evidence WHERE candidate=?', (cid,)):
            raw = row['seeds']
            if not raw:
                continue
            try:
                values = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if (isinstance(values, list) and len(values) == 2
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in values)):
                pairs.add((int(values[0]), int(values[1])))
    return pairs


def authorized_bank_seeds(connection, ids):
    """A bounded conservative superset of every deterministic bank pair the requested candidates use.

    The deterministic banks are the sha256 `seed_pair(phase, ordinal)` values. They run at least to
    the measurement ceiling and, in a live library, to every ordinal a requested candidate actually
    reached. That ordinal is the largest of: a retained `run` ordinal, the lifetime count in the
    per-candidate aggregate counters, the authorised probe / fine-tune bank, and any explicit seed
    the compact evidence table still holds. Reserving the whole range up to the largest of those is
    a conservative superset of both deterministic banks - never a smaller set.
    """
    from strategy_optimizer import DISCOVERY_RUNS, VALIDATION_RUNS, seed_pair
    try:
        from strategy_sampling import MAX_MEASUREMENT_RUNS
    except Exception:  # strategy_sampling is an optional planning companion at this layer
        MAX_MEASUREMENT_RUNS = 65536
    wanted = set(ids)
    limits = {phase: max(int(DISCOVERY_RUNS if phase == 'discovery' else VALIDATION_RUNS),
                         int(MAX_MEASUREMENT_RUNS)) for phase in PHASES}
    reserved = set(evidence_seed_pairs(connection, ids))
    for cid in ids:
        for phase, ordinal in _optional_query(
                connection, 'SELECT phase,ordinal FROM run WHERE candidate=?', (cid,)):
            phase = str(phase)
            if phase in limits:
                limits[phase] = max(limits[phase], int(ordinal) + 1)
        for phase in PHASES:
            for row in _optional_query(connection, 'SELECT value FROM meta WHERE key=?',
                                       (f'aggregate:{cid}:{phase}',)):
                try:
                    count = int((json.loads(row['value']) or {}).get('n') or 0)
                except (TypeError, ValueError):
                    count = 0
                limits[phase] = max(limits[phase], count)
    for row in _optional_query(connection, "SELECT value FROM meta WHERE key='probeBanks'"):
        try:
            declared = json.loads(row['value']) or {}
        except (TypeError, ValueError):
            declared = {}
        for cid in wanted:
            limits['validation'] = max(limits['validation'], int(declared.get(cid) or 0))
    for row in _optional_query(connection, "SELECT value FROM meta WHERE key='fineTunePrograms'"):
        try:
            programs = json.loads(row['value']) or {}
        except (TypeError, ValueError):
            programs = {}
        for program in programs.values():
            if not isinstance(program, dict):
                continue
            ceiling = int((program.get('budget') or {}).get('maxBank') or 0)
            for point in (program.get('points') or {}).values():
                if not isinstance(point, dict) or point.get('candidateId') not in wanted:
                    continue
                for value in (ceiling, point.get('bank'), point.get('runs'), point.get('completedBank')):
                    if value:
                        limits['validation'] = max(limits['validation'], int(value))
    for phase in PHASES:
        for ordinal in range(limits[phase]):
            reserved.add(tuple(seed_pair(phase, ordinal)))
    return reserved


def draw_seed_pairs(runs, reserved, randbits=secrets.randbits):
    """`runs` distinct common random seed pairs, rejecting reserved collisions and repeats."""
    pairs, seen, rejected = [], set(), 0
    while len(pairs) < runs:
        pair = (randbits(31), randbits(31))
        if pair in reserved or pair in seen:
            rejected += 1
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs, rejected


# --- manifest (immutable, written before evaluation) -------------------------------------------

@lru_cache(maxsize=8)
def simulator_provenance(simulator):
    """The identity and provenance of the simulator module behind `module:entry`.

    The production adapter exposes `provenance()`; a module without it (a test mock) is recorded by
    module/entry with no digest. The simulator string is frozen into the fingerprint, so a mock
    request can never be resumed as a real one, and a changed engine digest is rejected outright.
    """
    module_name, _, entry = simulator.partition(':')
    module = importlib.import_module(module_name)
    if not callable(getattr(module, entry, None)):
        raise ValueError('Simulator entry point is not callable')
    provider = getattr(module, 'provenance', None)
    data = dict(provider()) if callable(provider) else {}
    data.pop('digest', None)
    paths = [Path(module.__file__)]
    if simulator == DEFAULT_SIMULATOR:
        import ka_abi
        import strategy_optimizer_native
        paths.extend([HERE / 'strategy_optimizer_native.py', HERE / 'ka_abi.py', ka_abi.DLL,
                      HERE / 'native/ka_kernel/target/release/ka_kernel_v9_large.dll'])
        data['backend'] = strategy_optimizer_native.DEFAULT_BACKEND
    data.update(module=module_name, entry=entry,
                executionFiles={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                                if path.is_file() else None for path in paths})
    data['digest'] = _digest(data)
    return data


def request_fingerprint(library, runs, scenarios, provenance_digest, simulator):
    return _digest(dict(schema=CONFIRMATION_SCHEMA, library=str(Path(library).resolve()),
                        runs=runs, provenance=provenance_digest, simulator=simulator,
                        candidates=[dict(id=cid, scenarioDigest=_digest(scenarios[cid]))
                                    for cid in sorted(scenarios)]))


def build_manifest(library, runs, scenarios, provenance, reserved, simulator,
                   randbits=secrets.randbits):
    pairs, rejected = draw_seed_pairs(runs, reserved, randbits)
    digest = provenance.get('digest')
    manifest = dict(
        schema=CONFIRMATION_SCHEMA, kind='independent frozen-strategy confirmation',
        note=('Approximate fixed-sample intervals; not statistical certification. Repeated '
              'peeking is not a certificate; multiple candidates are not a simultaneous guarantee.'),
        created=time.time(), library=str(Path(library).resolve()), runs=runs,
        seedBank=SEED_BANK, simulator=simulator, provenanceDigest=digest,
        provenance={k: v for k, v in provenance.items() if k != 'digest'},
        candidates=[dict(id=cid, scenario=scenarios[cid], scenarioDigest=_digest(scenarios[cid]))
                    for cid in sorted(scenarios)],
        seeds=[list(pair) for pair in pairs], seedRejections=rejected,
        fingerprint=request_fingerprint(library, runs, scenarios, digest, simulator))
    manifest['digest'] = _digest({k: v for k, v in manifest.items() if k != 'digest'})
    return manifest


def verify_manifest(manifest, fingerprint, reserved, scenarios, simulator):
    """Reject a stored manifest whose body, schema, candidates, seeds or request do not match.

    The body digest is re-derived before anything is trusted, so a tampered scenario, seed, count or
    provenance is caught even when the separately stored request fingerprint is left untouched. The
    fingerprint itself is then re-derived from the frozen contents and must equal the live request.
    """
    if not isinstance(manifest, dict):
        raise ValueError('manifest is not an object')
    if manifest.get('schema') != CONFIRMATION_SCHEMA:
        raise ValueError('manifest schema mismatch')
    if manifest.get('digest') != _digest({k: v for k, v in manifest.items() if k != 'digest'}):
        raise ValueError('manifest body digest mismatch')
    if manifest.get('simulator') != simulator:
        raise ValueError('changed simulator: manifest does not match this confirmation request')
    rows = manifest.get('candidates')
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError('manifest has no candidate list')
    ids = [row.get('id') for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('manifest candidates are not a unique set')
    if scenarios is not None and set(ids) != set(scenarios):
        raise ValueError('manifest candidates differ from the requested library candidates')
    frozen = {}
    for row in rows:
        scenario = row.get('scenario')
        if row.get('scenarioDigest') != _digest(scenario):
            raise ValueError('manifest scenarioDigest does not match its scenario')
        frozen[row['id']] = scenario
    if scenarios is not None and frozen != scenarios:
        raise ValueError('manifest scenarios differ from the requested library scenarios')
    runs = manifest.get('runs')
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise ValueError('manifest run count is invalid')
    seeds = manifest.get('seeds')
    if not isinstance(seeds, list) or len(seeds) != runs:
        raise ValueError('manifest seed count does not match its run count')
    seen = set()
    for pair in seeds:
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= INT31
                           for v in pair)):
            raise ValueError('manifest seed is not two valid int31 values')
        key = (pair[0], pair[1])
        if key in reserved:
            raise ValueError('stored confirmation seeds collide with the library bank')
        if key in seen:
            raise ValueError('manifest contains duplicate seed pairs')
        seen.add(key)
    expected = request_fingerprint(manifest.get('library'), runs, frozen,
                                   manifest.get('provenanceDigest'), manifest.get('simulator'))
    if manifest.get('fingerprint') != fingerprint or expected != fingerprint:
        raise ValueError('changed request: manifest does not match this library/candidates/runs')
    return manifest


def load_or_create_manifest(output, fingerprint, reserved, scenarios, simulator, builder):
    """Return the frozen manifest; create it once, or reject a changed request or a bad resume."""
    path = Path(output) / 'manifest.json'
    if path.exists():
        manifest = json.loads(path.read_text(encoding='utf-8'))
        return verify_manifest(manifest, fingerprint, reserved, scenarios, simulator)
    if (Path(output) / 'confirmation.sqlite').exists():
        raise ValueError('output has results but no manifest; refusing to adopt it')
    Path(output).mkdir(parents=True, exist_ok=True)
    manifest = builder()
    with open(path, 'x', encoding='utf-8') as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


# --- durable results ---------------------------------------------------------------------------

class RunStore:
    """Durable confirmation results in a new SQLite file inside the output directory."""

    def __init__(self, path):
        self.db = sqlite3.connect(Path(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seed(ordinal INTEGER PRIMARY KEY, math INTEGER NOT NULL,
                lib INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS run(candidate TEXT NOT NULL, ordinal INTEGER NOT NULL,
                status TEXT NOT NULL, earned REAL, basis TEXT, verdict INTEGER, censored INTEGER,
                ticks REAL, error TEXT, PRIMARY KEY(candidate,ordinal));''')
        self.db.commit()

    def _meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _verify_existing(self, manifest):
        """Reject a conflicting or foreign results database before a single row is written.

        `INSERT OR IGNORE` silently accepted a database whose meta manifest, seed rows or run rows
        belonged to a different request. Here the existing contents are read first and must either
        be empty or match the frozen manifest exactly.
        """
        raw = self._meta('manifest')
        stored = {int(row['ordinal']): (int(row['math']), int(row['lib']))
                  for row in self.db.execute('SELECT ordinal,math,lib FROM seed')}
        rows = [(row['candidate'], int(row['ordinal']))
                for row in self.db.execute('SELECT candidate,ordinal FROM run')]
        if raw is None:
            if stored or rows or self._meta('digest') is not None:
                raise ValueError('results database holds rows without a matching manifest')
            return
        try:
            existing = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError('results database manifest is unreadable')
        if _canonical(existing) != _canonical(manifest):
            raise ValueError('results database manifest does not match the frozen manifest')
        if self._meta('digest') != manifest['digest']:
            raise ValueError('results database digest does not match the frozen manifest')
        expected = {i: (int(pair[0]), int(pair[1])) for i, pair in enumerate(manifest['seeds'])}
        if stored != expected:
            raise ValueError('results database seeds do not match the frozen manifest')
        ids = {row['id'] for row in manifest['candidates']}
        for candidate, ordinal in rows:
            if candidate not in ids:
                raise ValueError(f'unexpected candidate in results database: {candidate}')
            if not 0 <= ordinal < manifest['runs']:
                raise ValueError(f'run ordinal out of range in results database: {candidate}/{ordinal}')

    def save_manifest(self, manifest):
        self._verify_existing(manifest)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',
                            ('manifest', _canonical(manifest)))
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('digest', manifest['digest']))
            self.db.executemany('INSERT OR IGNORE INTO seed VALUES (?,?,?)',
                                [(i, pair[0], pair[1]) for i, pair in enumerate(manifest['seeds'])])

    def completed(self):
        return {(row['candidate'], row['ordinal']) for row in
                self.db.execute('SELECT candidate,ordinal FROM run')}

    def record(self, candidate, ordinal, reading):
        self.db.execute(
            'INSERT OR IGNORE INTO run VALUES (?,?,?,?,?,?,?,?,?)',
            (candidate, ordinal, reading['status'], reading.get('earned'), reading.get('basis'),
             reading.get('verdict'), int(bool(reading.get('censored'))), reading.get('ticks'),
             reading.get('error')))

    def rows(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM run')]

    def close(self):
        self.db.close()


# --- evaluation (bounded pool, chunked streaming) ----------------------------------------------

def classify_row(row):
    """Derive one reading from a simulator row; unresolved is unknown, never zero."""
    from strategy_optimizer import chest_count
    if not isinstance(row, dict):
        return dict(status=FAILED, error='empty result')
    verdict, censored = row.get('verdict'), bool(row.get('censored'))
    earned, basis = chest_count(row)
    common = dict(verdict=verdict, censored=censored, ticks=row.get('ticks'))
    if earned is None:
        return dict(status=UNRESOLVED, earned=None, basis=basis, **common)
    return dict(status=RESOLVED, earned=float(earned), basis=basis, **common)


def _init(recovery):
    if recovery not in sys.path:
        sys.path.insert(0, recovery)


def _worker(job):
    module, _, name = job['simulator'].partition(':')
    seeds = [int(value) for value in job['seeds']]
    engine = simulator_provenance(job['simulator']).get('digest')
    try:
        outcome = getattr(importlib.import_module(module), name)(job['scenario'], list(seeds))
        reading = classify_row(outcome)
        if isinstance(outcome, dict) and outcome.get('seeds') is not None:
            if [int(value) for value in outcome['seeds']] != seeds:
                reading = dict(status=FAILED, error='simulator returned different seeds')
    except Exception as exc:  # a failed run is stored as failed, never scored zero
        reading = dict(status=FAILED, error=f'{type(exc).__name__}: {exc}')
    return dict(candidate=job['candidate'], ordinal=job['ordinal'], reading=reading,
                seeds=seeds, engineProvenance=engine)


def bounded_submit(tasks, submit, on_result, bound, observer=None, waiter=wait):
    """Keep at most `bound` jobs in flight; stream each completion to `on_result`.

    `tasks` is consumed lazily, so a large pending set is never materialised up front.
    """
    bound = max(1, int(bound))
    active, iterator, exhausted = {}, iter(tasks), False
    while active or not exhausted:
        while not exhausted and len(active) < bound:
            try:
                job = next(iterator)
            except StopIteration:
                exhausted = True
                break
            active[submit(job)] = job
        if observer:
            observer(len(active))
        if not active:
            continue
        done, _ = waiter(list(active), return_when=FIRST_COMPLETED)
        for future in done:
            job = active.pop(future)
            on_result(job, future.result())


def pending_jobs(manifest, done, simulator):
    """Yield every (candidate, ordinal) pair not already durably recorded, in a stable order."""
    scenarios = {row['id']: row['scenario'] for row in manifest['candidates']}
    seeds = [tuple(pair) for pair in manifest['seeds']]
    for row in manifest['candidates']:
        for ordinal in range(manifest['runs']):
            if (row['id'], ordinal) in done:
                continue
            yield dict(candidate=row['id'], ordinal=ordinal, scenario=scenarios[row['id']],
                       seeds=seeds[ordinal], simulator=simulator)


def process_pool(workers):
    """The production executor: a bounded spawn `ProcessPoolExecutor`."""
    import multiprocessing
    return ProcessPoolExecutor(max_workers=max(1, workers),
                               mp_context=multiprocessing.get_context('spawn'),
                               initializer=_init, initargs=(str(HERE),))


def evaluate(store, manifest, simulator, workers, observer=None, executor_factory=None):
    """Evaluate every unfinished (candidate, ordinal) pair; commit results in chunks."""
    jobs = pending_jobs(manifest, store.completed(), simulator)
    counter = [0]

    def on_result(job, result):
        candidate, ordinal = result['candidate'], result['ordinal']
        if (candidate, ordinal) != (job['candidate'], job['ordinal']):
            raise ValueError('worker result does not match the submitted job')
        if result['seeds'] != [int(value) for value in job['seeds']]:
            raise ValueError('worker result seeds do not match the submitted job')
        expected = manifest['provenanceDigest']
        if expected is not None and result.get('engineProvenance') != expected:
            raise ValueError('worker engine provenance does not match the frozen provenance')
        store.record(candidate, ordinal, result['reading'])
        counter[0] += 1
        if counter[0] % CHUNK_ROWS == 0:
            store.db.commit()

    with (executor_factory or process_pool)(workers) as pool:
        bounded_submit(jobs, lambda job: pool.submit(_worker, job), on_result,
                       max(1, workers * POOL_AHEAD), observer=observer)
    store.db.commit()
    return counter[0]


# --- summary -----------------------------------------------------------------------------------

def _sample_sd(values):
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (n - 1))


def summarize_candidate(runs, readings):
    by_ordinal = {row['ordinal']: row for row in readings}
    resolved = [row['earned'] for row in readings if row['status'] == RESOLVED]
    missing = [ordinal for ordinal in range(runs) if ordinal not in by_ordinal]
    unresolved = sum(1 for row in readings if row['status'] == UNRESOLVED)
    failed = sum(1 for row in readings if row['status'] == FAILED)
    n = len(resolved)
    mean = sum(resolved) / n if n else None
    sd = _sample_sd(resolved)
    incomplete = len(resolved) != runs
    # An interval over the resolved subset alone is not an interval for the frozen sample while any
    # reading is censored or unresolved, so it is withheld rather than faked. The resolved-subset
    # mean stays, labelled as the descriptive number it is.
    halfwidth = Z_95 * sd / math.sqrt(n) if sd is not None and n > 1 and not incomplete else None
    return dict(runs=runs, resolved=n, unresolved=unresolved, failed=failed,
                missingOrUnrecorded=len(missing), incomplete=incomplete,
                mean=mean, meanBasis='resolved-subset descriptive mean (censored readings excluded)',
                sampleSD=sd, approximateFixedSample95Halfwidth=halfwidth,
                wins=sum(1 for row in readings if row['status'] == RESOLVED and row['verdict'] == 1),
                max=max(resolved) if resolved else None, readings=by_ordinal)


def paired_difference(baseline, other, runs, by_candidate):
    deltas, unresolved = [], 0
    for ordinal in range(runs):
        left = by_candidate[baseline]['readings'].get(ordinal)
        right = by_candidate[other]['readings'].get(ordinal)
        if (left is None or left['status'] != RESOLVED or right is None
                or right['status'] != RESOLVED):
            unresolved += 1
            continue
        deltas.append(left['earned'] - right['earned'])
    n = len(deltas)
    mean = sum(deltas) / n if n else None
    sd = _sample_sd(deltas)
    incomplete = bool(unresolved)
    return dict(baseline=baseline, candidate=other, matched=n, unresolved=unresolved,
                incomplete=incomplete, meanDelta=mean,
                meanDeltaBasis='matched-resolved-pairs descriptive mean',
                approximateFixedSample95Halfwidth=Z_95 * sd / math.sqrt(n)
                if sd is not None and n > 1 and not incomplete else None)


def build_report(manifest, store):
    runs = manifest['runs']
    ids = [row['id'] for row in manifest['candidates']]
    rows = store.rows()
    by_candidate = {cid: summarize_candidate(runs, [row for row in rows if row['candidate'] == cid])
                    for cid in ids}
    paired = [paired_difference(ids[0], other, runs, by_candidate) for other in ids[1:]]
    for cid in ids:
        by_candidate[cid].pop('readings')
    report = dict(kind='independent frozen-strategy confirmation', library=manifest['library'],
                  manifestDigest=manifest['digest'], seedBank=manifest['seedBank'],
                  provenanceDigest=manifest['provenanceDigest'], runs=runs,
                  seedPairs=manifest['seeds'], candidates=by_candidate,
                  paired=paired,
                  notes=[manifest['note'],
                         'Unresolved or failed readings are never scored zero; a candidate with any '
                         'unresolved reading is incomplete and confirmation is withheld.'])
    return report


# --- entry point -------------------------------------------------------------------------------

def run_confirmation(library, candidates, runs, workers, output, simulator=DEFAULT_SIMULATOR,
                     randbits=secrets.randbits, observer=None, executor_factory=None):
    library, output = Path(library), Path(output)
    if runs < 1:
        raise ValueError('--runs must be >= 1')
    if workers < 1:
        raise ValueError('--workers must be >= 1')
    if not candidates:
        raise ValueError('at least one --candidate is required')
    if len(set(candidates)) != len(candidates):
        raise ValueError('duplicate --candidate values')
    if output.exists() and not output.is_dir():
        raise ValueError(f'--output is not a directory: {output}')
    connection = open_library_readonly(library)
    try:
        connection.execute('BEGIN')  # pin one consistent read snapshot for the freeze
        scenarios = read_candidates(connection, candidates)
        reserved = authorized_bank_seeds(connection, candidates)
    finally:
        connection.close()
    provenance = simulator_provenance(simulator)
    fingerprint = request_fingerprint(library, runs, scenarios, provenance.get('digest'), simulator)
    manifest = load_or_create_manifest(
        output, fingerprint, reserved, scenarios, simulator,
        lambda: build_manifest(library, runs, scenarios, provenance, reserved, simulator, randbits))
    store = RunStore(output / 'confirmation.sqlite')
    try:
        store.save_manifest(manifest)
        evaluate(store, manifest, simulator, workers, observer=observer,
                 executor_factory=executor_factory)
        report = build_report(manifest, store)
    finally:
        store.close()
    (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--candidate', action='append', default=[], required=True)
    parser.add_argument('--runs', type=int, required=True)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_confirmation(args.library, args.candidate, args.runs, args.workers, args.output)
    print(json.dumps(dict(manifestDigest=report['manifestDigest'],
                          candidates=report['candidates'], paired=report['paired']), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
