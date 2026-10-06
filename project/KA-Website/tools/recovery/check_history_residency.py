"""Residency checks: a scale-shaped history import keeps a SMALL working library.

One meaningful scale-shaped synthetic fixture (>640 recovered historical builds, every scenario kept
only zlib-compressed) is imported through the real ``strategy_history_summary.build_library``. The
checks pin the root review contract the residency fix must satisfy:

* every recovered scenario/attribution/summary/aggregate identity is retained losslessly;
* the *active* resident population is bounded (<= 24 per encounter, <= 640 total) and carries a
  supplied Community reference per encounter plus a diverse round-robin of engine/stat strata;
* an active build's ``stats`` are the current canonical runtime stats, never an old ranking;
* the real ``Coordinator`` can propose a new child without a population refusal;
* every current-campaign counter stays 0 and the source library is byte-untouched;
* the hot seed collector reads the indexed compact bounds/exceptions and live EA rows, and never
  decodes a full ``history_seed_block`` on the per-confirmation path (SQL-trace proof).

No real import, no live library, no battle: the fixture source is synthetic and the battle pool is a
deterministic stub.

    python check_history_residency.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import zlib
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = HERE / '.tmp-history-residency'
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)
for _env in ('TMPDIR', 'TEMP', 'TMP'):
    os.environ[_env] = str(TMP)

import strategy_build_domain as domain                 # noqa: E402
import strategy_encounter_evaluation as evaluation      # noqa: E402
import strategy_encounter_search as search              # noqa: E402
import strategy_history_summary as summary              # noqa: E402
import strategy_seed_freshness as freshness             # noqa: E402
import strategy_students as students                    # noqa: E402
from strategy_optimizer import Store, seed_pair         # noqa: E402
from strategy_optimizer_adapter import default_scenario, propose, stats, provenance  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)[:400]) if detail else ''))


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
    source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
    defeat INTEGER NOT NULL, region TEXT NOT NULL DEFAULT '');
CREATE TABLE lineage(candidate TEXT PRIMARY KEY, parent TEXT, root TEXT NOT NULL,
    depth INTEGER NOT NULL, operation TEXT, target TEXT, change TEXT, source TEXT,
    encounterId INTEGER, proposal INTEGER, searchSpaceVersion INTEGER, created INTEGER NOT NULL);
CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
    result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal));
CREATE TABLE evidence(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
    seeds TEXT, verdict INTEGER, censored INTEGER NOT NULL DEFAULT 0, prizeCallbacks INTEGER,
    awardedChests INTEGER, awardedBasis TEXT, pendingChests INTEGER,
    PRIMARY KEY(candidate,phase,ordinal));
CREATE TABLE archive(cell TEXT PRIMARY KEY, candidate TEXT NOT NULL, quality TEXT NOT NULL);
CREATE TABLE predictor_history(candidate TEXT PRIMARY KEY, scenario_zlib BLOB NOT NULL,
    root TEXT NOT NULL, encounter INTEGER NOT NULL, created INTEGER NOT NULL,
    discovery TEXT, validation TEXT, pruned INTEGER NOT NULL);
"""

ENCOUNTERS = tuple(range(20))
BASE = default_scenario()
_SCEN = {}


def unique_scenarios(encounter, count, offset):
    """``count`` distinct legal scenarios for ``encounter`` (deterministic, cached)."""
    key = (encounter, count, offset)
    if key in _SCEN:
        return _SCEN[key]
    found = {}
    seed = offset
    while len(found) < count and seed < offset + 4000:
        seed += 1
        scenario = dict(BASE)
        scenario['encounterId'] = encounter
        try:
            proposed = propose(scenario, seed)
        except Exception:  # noqa: BLE001 - a seed with no legal move is simply skipped
            continue
        identity = domain.identity(proposed)
        if identity not in found:
            found[identity] = proposed
    _SCEN[key] = list(found.items())
    return _SCEN[key]


def ev(candidate, ordinal, awarded, phase='validation'):
    return (candidate, phase, ordinal, json.dumps(list(seed_pair(phase, ordinal))), 1, 0, 0,
            awarded, 'reward-entitlement-certificate', None)


def write_source(path, *, digest, live, pruned, archive_rows=0):
    """A synthetic optimiser source: ``live`` (candidate) + ``pruned`` (predictor_history) builds."""
    db = sqlite3.connect(str(path))
    try:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO meta VALUES(?,?)', ('schema', json.dumps(1)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('objectiveVersion', json.dumps(3)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('searchSpaceVersion', json.dumps(4)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('totalRuns', json.dumps(len(live))))
        db.execute('INSERT INTO meta VALUES(?,?)', ('timedRuns', json.dumps(len(live))))
        db.execute('INSERT INTO meta VALUES(?,?)', (
            'provenance', json.dumps(dict(digest=digest, count=1,
                                          files={'tools/recovery/combat_shared_controllers.py': digest[:8]}))))
        evidence = []
        for build in live:
            cid, scenario = build['identity'], build['scenario']
            db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                       (cid, json.dumps(scenario), build['label'], build['source'], '{}', build['created']))
            db.execute('INSERT INTO candidate_meta VALUES(?,?,?,?)',
                       (cid, build['encounter'], build['defeat'], ''))
            db.execute('INSERT INTO lineage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (cid, None, cid, 0, 'origin', '', '', build['lineage_source'], build['encounter'],
                        0, 4, build['created']))
            for ordinal in range(2):
                evidence.append(ev(cid, ordinal, build['awarded']))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)', evidence)
        for entry in pruned:
            cid, scenario = entry['identity'], entry['scenario']
            raw = json.dumps({'n': 7, 'mean': entry['awarded'], 'rank': 2, 'score': 999})
            db.execute('INSERT INTO predictor_history VALUES(?,?,?,?,?,?,?,?)',
                       (cid, zlib.compress(json.dumps(scenario).encode('utf-8'), 6), cid,
                        entry['encounter'], entry['created'], raw, raw, 1))
        for index in range(archive_rows):
            db.execute('INSERT INTO archive VALUES(?,?,?)', ('cell-%d' % index, live[0]['identity'], '1'))
        db.commit()
    finally:
        db.close()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build_fixture(folder):
    """The two-source scale fixture: ~740 recovered builds, all scenarios compressed-only."""
    seed_a = folder / 'source-a.sqlite'
    seed_b = folder / 'source-b.sqlite'
    digest_a = hashlib.sha256(b'engine-a').hexdigest()
    digest_b = hashlib.sha256(b'engine-b').hexdigest()

    live_a, pruned_a = [], []
    live_b, pruned_b = [], []
    for encounter in ENCOUNTERS:
        ref_a = unique_scenarios(encounter, 1, 100)[0]
        mutants_a = unique_scenarios(encounter, 7, 100)[1:]
        if encounter == 19:
            known = {cid for cid, _scenario in mutants_a}
            extra = [item for item in unique_scenarios(encounter, 46, 5000)[6:]
                     if item[0] not in known]
            mutants_a = mutants_a + extra
        pruned_scen_a = unique_scenarios(encounter, 27, 100)[7:]
        live_a.append(dict(identity=ref_a[0], scenario=ref_a[1], encounter=encounter, defeat=0,
                           label='Community reference', source='supplied', lineage_source='supplied',
                           awarded=2, created=encounter + 1))
        for index, (cid, scenario) in enumerate(mutants_a):
            live_a.append(dict(identity=cid, scenario=scenario, encounter=encounter, defeat=0,
                               label='mutant %d' % index, source='community',
                               lineage_source='mutation', awarded=index + 1, created=encounter + 1))
        for cid, scenario in pruned_scen_a:
            pruned_a.append(dict(identity=cid, scenario=scenario, encounter=encounter,
                                 awarded=1, created=encounter + 1))

        ref_b = unique_scenarios(encounter, 1, 3000)[0]
        mutants_b = unique_scenarios(encounter, 4, 3000)[1:]
        pruned_scen_b = unique_scenarios(encounter, 16, 3000)[4:]
        live_b.append(dict(identity=ref_b[0], scenario=ref_b[1], encounter=encounter, defeat=0,
                           label='Community reference', source='supplied', lineage_source='supplied',
                           awarded=3, created=encounter + 1))
        for index, (cid, scenario) in enumerate(mutants_b):
            live_b.append(dict(identity=cid, scenario=scenario, encounter=encounter, defeat=0,
                               label='b-mutant %d' % index, source='community',
                               lineage_source='mutation', awarded=index + 1, created=encounter + 1))
        for cid, scenario in pruned_scen_b:
            pruned_b.append(dict(identity=cid, scenario=scenario, encounter=encounter,
                                 awarded=1, created=encounter + 1))

    write_source(seed_a, digest=digest_a, live=live_a, pruned=pruned_a, archive_rows=2)
    write_source(seed_b, digest=digest_b, live=live_b, pruned=pruned_b)
    identities = {build['identity'] for build in live_a + pruned_a + live_b + pruned_b}
    dest = folder / 'residency-summary.sqlite'
    before = (sha256_file(seed_a), sha256_file(seed_b))
    summary.build_library(
        [dict(path=str(seed_a), label='engine-a', ownership='community'),
         dict(path=str(seed_b), label='engine-b', ownership='student9')],
        dest, authorised=True, batch_size=256)
    after = (sha256_file(seed_a), sha256_file(seed_b))
    return dict(source=seed_a, source_b=seed_b, dest=dest, before=before, after=after,
                live=len(live_a) + len(live_b), pruned=len(pruned_a) + len(pruned_b),
                recovered=len(identities),
                expectedAggregates=(len(pruned_a) + len(pruned_b)) * 2)


class RewardPool:
    """Deterministic synthetic rewards; no battle is ever simulated."""

    def submit(self, fn, scenario, seeds):
        future = Future()
        digest = hashlib.sha256(json.dumps(scenario, sort_keys=True, default=str).encode()).hexdigest()
        reward = 1 + (int(digest[:6], 16) % 5)
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=reward),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def rows(db, sql, args=()):
    connection = sqlite3.connect(str(db))
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def scalar(db, sql, args=()):
    result = rows(db, sql, args)
    return result[0][0] if result else None


def residency(folder):
    fixture = build_fixture(folder)
    dest = fixture['dest']
    total_recovered = fixture['recovered']
    check('fixture-exceeds-scale', total_recovered > 640, total_recovered)

    status = summary.history_status(dest)
    check('active-bounded-total', status['activeCandidates'] <= summary.MAX_CANDIDATES_TOTAL,
          status['activeCandidates'])
    active_per_encounter = rows(
        dest, 'SELECT m.encounter, COUNT(*) FROM candidate c JOIN candidate_meta m ON m.id=c.id '
              'GROUP BY m.encounter')
    check('active-bounded-per-encounter',
          active_per_encounter and all(count <= summary.MAX_CANDIDATES_PER_ENCOUNTER
                                       for _enc, count in active_per_encounter),
          active_per_encounter)
    check('recovered-scenarios-lossless', status['scenarios'] == total_recovered,
          (status['scenarios'], total_recovered))
    check('all-attribution-retained',
          status['candidates'] == total_recovered
          and scalar(dest, 'SELECT COUNT(DISTINCT candidate_id) FROM history_scenario') == total_recovered,
          (status['candidates'], status['scenarios']))
    check('summaries-retained', status['summaries'] >= fixture['live'],
          (status['summaries'], fixture['live']))
    check('aggregates-retained', status['aggregateRecords'] == fixture['expectedAggregates'],
          (status['aggregateRecords'], fixture['expectedAggregates']))
    check('archive-omission-recorded',
          scalar(dest, "SELECT COALESCE(SUM(count),0) FROM history_omission "
                       "WHERE kind='archive-ranking'") == 2,
          scalar(dest, "SELECT COALESCE(SUM(count),0) FROM history_omission WHERE kind='archive-ranking'"))
    missing_summary = scalar(
        dest, 'SELECT COUNT(*) FROM history_build_summary s WHERE NOT EXISTS '
              '(SELECT 1 FROM history_candidate c WHERE c.candidate_id=s.candidate_id)')
    check('summary-identities-attributed', missing_summary == 0, missing_summary)

    archived = rows(dest, 'SELECT candidate_id,engine_compat_group,encounter_id,defeat_count '
                          'FROM history_build_summary WHERE candidate_id NOT IN '
                          '(SELECT id FROM candidate) ORDER BY candidate_id LIMIT 1')
    check('measured-builds-archived-not-resident', bool(archived), len(archived))
    if archived:
        pcid, group, enc, defeat = archived[0]
        hydrated = summary.stored_scenario(dest, pcid)
        check('archived-scenario-hydratable',
              isinstance(hydrated, dict) and hydrated.get('encounterId') == enc, pcid)
        loaded = summary.load_observations(
            dest, [pcid], encounter={'encounterId': enc, 'defeatCount': defeat},
            policy={'finishPolicy': 'on-verdict'}, engine_revision=group)
        check('loader-reads-archived-scenario',
              len(loaded['observations']) == 1 and loaded['excluded'] == [], loaded['excluded'])

    references = rows(dest, "SELECT c.id FROM candidate c JOIN candidate_meta m ON m.id=c.id "
                            "WHERE c.source='supplied' AND m.encounter=19")
    check('supplied-reference-per-encounter', bool(references), len(references))
    enc_with_reference = scalar(
        dest, "SELECT COUNT(DISTINCT m.encounter) FROM candidate c JOIN candidate_meta m ON m.id=c.id "
              "WHERE c.source='supplied'")
    check('supplied-reference-every-encounter', enc_with_reference == len(ENCOUNTERS),
          enc_with_reference)

    active_stats = rows(dest, 'SELECT COUNT(*) FROM candidate WHERE stats IS NOT NULL AND stats != \'{}\'')
    check('active-stats-are-current', active_stats[0][0] == status['activeCandidates'],
          (active_stats[0][0], status['activeCandidates']))
    stored_stats = json.loads(scalar(dest, 'SELECT stats FROM candidate WHERE id=?',
                                     (references[0][0],)))
    check('active-stats-canonical',
          stored_stats == json.loads(json.dumps(
              stats(json.loads(scalar(dest, 'SELECT scenario FROM candidate WHERE id=?',
                                      (references[0][0],)))),
              sort_keys=True)),
          list(stored_stats))

    check('campaign-totalRuns-zero', scalar(dest, "SELECT value FROM meta WHERE key='totalRuns'") == '0')
    check('campaign-proposals-zero',
          scalar(dest, "SELECT value FROM meta WHERE key='proposals'") == '0')
    check('campaign-improvements-zero',
          scalar(dest, "SELECT value FROM meta WHERE key='improvements'") == '0')
    check('current-campaign-counters-zero', status['currentTotalRuns'] == 0)
    check('source-untouched', fixture['before'] == fixture['after'],
          (fixture['before'], fixture['after']))

    return dict(fixture=fixture, status=status, active=status['activeCandidates'],
                per_encounter=dict(active_per_encounter), reference=references[0][0])


def coordinator_proposes(folder, fixture, reference):
    """The real Coordinator can propose a new child; the recovered population never refuses it."""
    prov = provenance()
    search.EVALUATOR_FACTORY = (lambda *, workers, telemetry, timeout:
                                evaluation.Evaluator(workers=workers, telemetry=telemetry,
                                                     pool=RewardPool(), timeout=10.0))
    store = Store(fixture['dest'], prov)
    try:
        baseline = {row[0] for row in store.db.execute('SELECT id FROM candidate')}
        check('community-parent-available', students.community_parent(store, 19)[0] is not None)
        coordinator = search.Coordinator(path=fixture['dest'], revision=prov['digest'],
                                         mode='community-first', encounter=19, reference=reference,
                                         workers=2, telemetry=True, maximum=6)
        config = search.default_config('community-first', purposes={
            'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
        store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                        previous={}))
        coordinator.load(store)
        failure = None
        deadline = time.time() + 60
        try:
            while time.time() < deadline:
                coordinator.run_pass(store, running=True)
                if coordinator.state.get('publication'):
                    break
                if (not coordinator.busy and coordinator.state.get('current') is None
                        and not coordinator.state.get('confirmation')
                        and coordinator._next_purpose(store.db) is None):
                    break
                time.sleep(0.02)
        except ValueError as exc:
            failure = exc
        mutations = [row[0] for row in store.db.execute(
            "SELECT id FROM candidate WHERE source='mutation'")]
        check('coordinator-proposes-child', failure is None and len(mutations) >= 1,
              (failure, len(mutations)))
        check('active-still-bounded-after-pass',
              scalar(fixture['dest'], 'SELECT COUNT(*) FROM candidate') <= summary.MAX_CANDIDATES_TOTAL,
              scalar(fixture['dest'], 'SELECT COUNT(*) FROM candidate'))
        return dict(newMutations=len(mutations), baseline=len(baseline))
    finally:
        search.EVALUATOR_FACTORY = None
        store.close()


def seed_collector_trace(dest):
    """The hot seed collector reads indexed compact bounds/exceptions, never a full seed block."""
    connection = sqlite3.connect(str(dest))
    seen = []
    connection.set_trace_callback(lambda sql: seen.append(sql))
    try:
        result = freshness.collect(connection)
    finally:
        connection.set_trace_callback(None)
        connection.close()
    decoded = [sql for sql in seen if 'history_seed_block' in sql]
    indexed = [sql for sql in seen
               if 'history_seed_exclusion' in sql or 'history_seed_exception' in sql]
    check('seed-collector-no-block-decode', not decoded, decoded[:2])
    check('seed-collector-reads-indexed-bounds', bool(indexed), len(indexed))
    check('seed-collector-answers', isinstance(result, dict) and 'pairs' in result,
          result.get('counts', {}).get('coverage'))
    return dict(blockReads=len(decoded), indexedReads=len(indexed),
                pairs=len(result.get('pairs') or ()))


def main():
    folder = TMP / ('ka-residency-%s' % os.urandom(4).hex())
    folder.mkdir(parents=True, exist_ok=True)
    results = residency(folder)
    report = dict(
        module='check_history_residency',
        residency={key: value for key, value in results.items() if key != 'fixture'},
        coordinator=coordinator_proposes(folder, results['fixture'], results['reference']),
        seedCollector=seed_collector_trace(results['fixture']['dest']),
        limits=['Synthetic fixture only; no real source library, import, live DB or battle.',
                'The battle pool is a deterministic stub so a new child can be proposed without '
                'simulating a battle.'],
    )
    passed = sum(1 for _name, ok in RESULTS if ok)
    report['checks'] = dict(passed=passed, total=len(RESULTS))
    print(json.dumps(report, sort_keys=True, default=str))
    print('%d/%d history-residency checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
