"""Synthetic runtime integration checks for the compact history module.

Real `Coordinator` + `Store` on hand-built tiny compact libraries, with the battle pool mocked ONLY
for deterministic synthetic rewards (no real battles, no real import, no live library). The checks
pin the integration contract the root owns:

* a compact prior is consumed by the adviser and stays historical/unconfirmed;
* build constraints are validated against the exact stored scenario (never refused wholesale);
* the original candidate family/lineage is preserved;
* an old source is admitted only through a recorded, read-time-verified observer-migration grant;
  an unknown/grant-less source is refused; a changed certificate revokes the grant;
* the seed collector unions compact known exclusions, keeps a partial-coverage claim honest and
  still refuses a fresh draw that collides with a known custom seed;
* a real bounded cycle charges only its own experiments (conserved), a restart does not
  double-charge, and fresh development evidence for a candidate wins over its own prior;
* an aggregate-only pruned build is preserved but never invented into a full mean/histogram.

    python check_history_runtime.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = HERE / '.tmp-history-runtime'
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)
for _env in ('TMPDIR', 'TEMP', 'TMP'):
    os.environ[_env] = str(TMP)

import strategy_build_domain as domain                # noqa: E402
import strategy_encounter_adviser as adviser           # noqa: E402
import strategy_encounter_evaluation as evaluation     # noqa: E402
import strategy_encounter_migration as migration       # noqa: E402
import strategy_encounter_search as search             # noqa: E402
import strategy_experiment_store as ledger             # noqa: E402
import strategy_history_summary as summary             # noqa: E402
import strategy_legacy_observations as legacy          # noqa: E402
from strategy_optimizer import Store, seed_pair        # noqa: E402
from strategy_optimizer_adapter import default_scenario, propose, provenance  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


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
CREATE TABLE rebel_break(ledger_id INTEGER PRIMARY KEY, detail TEXT);
"""


def new_dir(tag):
    path = TMP / ('ka-runtime-%s-%s' % (tag, os.urandom(4).hex()))
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_source(path, *, prov, builds, evidence=(), lineage_rows=(), predictor=()):
    db = sqlite3.connect(str(path))
    try:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO meta VALUES(?,?)', ('schema', json.dumps(1)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('objectiveVersion', json.dumps(3)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('searchSpaceVersion', json.dumps(4)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('totalRuns', json.dumps(len(evidence))))
        db.execute('INSERT INTO meta VALUES(?,?)', ('timedRuns', json.dumps(len(evidence))))
        db.execute('INSERT INTO meta VALUES(?,?)', ('provenance', json.dumps(prov)))
        for build in builds:
            db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                       (build['id'], json.dumps(build['scenario']), build.get('label', build['id']),
                        build.get('origin', 'fixture'), '{}', build.get('created', 0)))
            db.execute('INSERT INTO candidate_meta VALUES(?,?,?,?)',
                       (build['id'], build['encounter'], build['defeat'], build.get('region', '')))
        db.executemany('INSERT INTO lineage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', list(lineage_rows))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)', list(evidence))
        db.commit()
    finally:
        db.close()


def ev(candidate, ordinal, pair, verdict, awarded=None, basis=None, phase='validation'):
    return (candidate, phase, ordinal, None if pair is None else json.dumps(list(pair)),
            verdict, 0, 0, awarded, basis, None)


def build_compact(folder, *, tag, source_prov, builds, evidence=(), lineage_rows=(), grants=(),
                  predictor=()):
    source = folder / ('source-%s.sqlite' % tag)
    dest = folder / ('summary-%s.sqlite' % tag)
    write_source(source, prov=source_prov, builds=builds, evidence=evidence,
                 lineage_rows=lineage_rows, predictor=predictor)
    summary.build_library([dict(path=str(source), label=tag, ownership='community')], dest,
                          authorised=True, batch_size=16,
                          grants=[str(source)] if grants else ())
    return dest


def coordinator_for(path, reference, *, constraints=None, workers=2):
    prov = provenance()
    coordinator = search.Coordinator(path=path, revision=prov['digest'], mode='community-first',
                                     encounter=19, reference=reference, constraints=constraints,
                                     workers=workers, telemetry=True, maximum=6)
    coordinator.state['policy'] = {'finishPolicy': 'on-verdict'}
    store = Store(path, prov)
    coordinator._sync_db(store.db)
    coordinator._ensure_schema(store.db)
    return store, coordinator, prov


class RewardPool:
    """Deterministic synthetic rewards; no battle is ever simulated."""

    def __init__(self):
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
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


def spy_on_fit():
    captured = {}
    real = adviser.fit

    def spy(observations, compatibility=None, previous=None):
        captured['observations'] = list(observations)
        return real(observations, compatibility=compatibility, previous=previous)
    adviser.fit = spy
    return captured, (lambda: setattr(adviser, 'fit', real))


def prior_and_contract(folder):
    """The compact prior is consumed by the adviser, stays unconfirmed, and honours constraints."""
    prov = provenance()
    base = default_scenario()
    cid = domain.identity(base)
    child = propose(base, 2)
    cid2 = domain.identity(child)
    evidence = [ev(cid, i, seed_pair('validation', i), 1, awarded=2,
                   basis='reward-entitlement-certificate') for i in range(3)]
    evidence += [ev(cid2, 10, seed_pair('validation', 10), 1, awarded=1,
                    basis='reward-entitlement-certificate')]
    lineage = [(cid2, None, cid2, 0, 'child', '', '', 'mutation', 19, 0, 4, 5)]
    dest = build_compact(folder, tag='prior', source_prov=prov,
                         builds=[dict(id=cid, encounter=19, defeat=0, scenario=base),
                                 dict(id=cid2, encounter=19, defeat=0, scenario=child,
                                      origin='reseed:herb')],
                         evidence=evidence, lineage_rows=lineage)
    store, coordinator, prov = coordinator_for(dest, cid)
    try:
        priors = coordinator._legacy_observations(store.db, None)
        check('compact-prior-returned', bool(priors), priors)
        main = {str(p['candidateId']): p for p in priors}
        entry = main.get(cid)
        check('compact-prior-is-historical', entry is not None
              and entry['evidenceClass'] == 'historical-prior'
              and entry['independentConfirmation'] is False, entry)
        check('compact-prior-unconfirmed', entry is not None
              and entry['historicalConfirmed'] is False
              and entry['confirmationEligible'] is False, entry)
        check('compact-prior-mean', entry is not None and entry['meanEarned'] == 2.0, entry)
        check('compact-prior-features', entry is not None
              and entry['featuresAvailable'] is True and bool(entry['features']), entry)
        child_entry = main.get(cid2)
        # The loader preserves the per-candidate origin/ownership; the coordinator separately labels
        # the budget/evidence *stream* from the lineage source, so the two are never conflated.
        check('original-lineage-preserved', child_entry is not None
              and child_entry['originSource'] == 'reseed:herb'
              and child_entry['sourceOwnership'] == 'community', child_entry)
        check('family-boundary-is-stream', child_entry is not None
              and child_entry['evidenceFamily'] == 'mutation', child_entry)

        captured, restore = spy_on_fit()
        try:
            model = coordinator._fit_adviser(store.db)
        finally:
            restore()
        used = {str(o['candidateId']) for o in captured.get('observations', [])}
        check('compact-prior-consumed-by-adviser', cid in used, used)
        check('adviser-input-includes-priors', (model or {}).get('candidateCount') == 2, model)

        mech = coordinator._mechanics_revision()
        encounter = {'encounterId': 19, 'defeatCount': 0}
        policy = {'finishPolicy': 'on-verdict'}
        allowed = legacy.load_observations(store.db, [cid], encounter=encounter, policy=policy,
                                           mechanics_revision=mech, engine_revision=prov,
                                           constraints={})
        check('constraints-validated-not-refused', len(allowed['observations']) == 1,
              allowed['excluded'])
        rejected = legacy.load_observations(
            store.db, [cid], encounter=encounter, policy=policy, mechanics_revision=mech,
            engine_revision=prov, constraints={'fixed': {'encounterId': 999}})
        check('constraints-mismatch-refused', not rejected['observations']
              and rejected['excluded'][0]['reason'] in ('constraints-mismatch', 'constraints-invalid'),
              rejected['excluded'])
        return dict(priors=len(priors), origin=child_entry['originSource'],
                    stream=child_entry['evidenceFamily'])
    finally:
        store.close()


def certificate_grant(folder):
    """A recorded observer grant is re-verified at read time; an unknown source is refused."""
    prov = provenance()
    base = default_scenario()
    scenario = propose(base, 2)
    cid = domain.identity(scenario)
    old_prov = dict(digest='b' * 64,
                    files={'tools/recovery/combat_shared_controllers.py': 'old-marker'})
    unknown_prov = dict(digest='c' * 64,
                        files={'tools/recovery/combat_shared_controllers.py': 'other-marker'})
    evidence = [ev(cid, 0, seed_pair('validation', 0), 1, awarded=3,
                   basis='reward-entitlement-certificate')]
    fake_cert = folder / 'fake-certificate.json'
    fake_cert.write_text('{"schema": 1, "review": "accepted"}', encoding='utf-8')
    real_cert, real_preview = migration.CERTIFICATE, migration.preview
    migration.CERTIFICATE = fake_cert
    migration.preview = lambda old, current: dict(
        eligible=True, proof=dict(certificate='cafe', sourceFrom='from', sourceTo='to'),
        planId='plan-1', limitation='synthetic')
    try:
        granted = build_compact(
            folder, tag='granted', source_prov=old_prov,
            builds=[dict(id=cid, encounter=19, defeat=0, scenario=scenario)], evidence=evidence,
            grants=[str(folder / 'source-granted.sqlite')])
        ungranted = build_compact(
            folder, tag='ungranted', source_prov=unknown_prov,
            builds=[dict(id=cid, encounter=19, defeat=0, scenario=scenario)], evidence=evidence)
        mech = 'mechanics-synthetic'
        encounter = {'encounterId': 19, 'defeatCount': 0}
        policy = {'finishPolicy': 'on-verdict'}

        admitted = legacy.load_observations(granted, [cid], encounter=encounter, policy=policy,
                                            mechanics_revision=mech, engine_revision=prov)
        check('granted-source-admitted', len(admitted['observations']) == 1, admitted['excluded'])
        if admitted['observations']:
            check('grant-basis-is-migration-certificate',
                  admitted['observations'][0]['engine']['basis'] == 'migration-certificate',
                  admitted['observations'][0]['engine'])

        refused = legacy.load_observations(ungranted, [cid], encounter=encounter, policy=policy,
                                           mechanics_revision=mech, engine_revision=prov)
        check('unknown-source-refused', not refused['observations']
              and refused['excluded'][0]['reason'] == 'engine-revision-mismatch',
              refused['excluded'])

        # A changed pinned certificate revokes the recorded grant (no universal stamp).
        fake_cert.write_text('{"schema": 1, "review": "revised"}', encoding='utf-8')
        revoked = legacy.load_observations(granted, [cid], encounter=encounter, policy=policy,
                                           mechanics_revision=mech, engine_revision=prov)
        check('changed-certificate-revokes-grant', not revoked['observations']
              and revoked['excluded'][0]['reason'] == 'engine-revision-mismatch',
              revoked['excluded'])
        fake_cert.write_text('{"schema": 1, "review": "accepted"}', encoding='utf-8')

        # An ineligible/inconclusive transition is refused even with the grant recorded.
        migration.preview = lambda old, current: dict(eligible=False, reason='synthetic-unknown')
        inconclusive = legacy.load_observations(granted, [cid], encounter=encounter, policy=policy,
                                                mechanics_revision=mech, engine_revision=prov)
        check('ineligible-transition-refused', not inconclusive['observations']
              and inconclusive['excluded'][0]['reason'] == 'engine-revision-mismatch',
              inconclusive['excluded'])
        return dict(grantBasis='migration-certificate')
    finally:
        migration.CERTIFICATE, migration.preview = real_cert, real_preview


def seed_coverage(folder):
    """Compact known exclusions are usable; missing coverage is a limited claim, not a block."""
    prov = provenance()
    base = default_scenario()
    cid = domain.identity(base)
    off_bank = seed_pair('validation', 1_000_000)
    evidence = [ev(cid, 0, off_bank, 1, awarded=1, basis='reward-entitlement-certificate'),
                ev(cid, 1, seed_pair('validation', 1), 1, awarded=1,
                   basis='reward-entitlement-certificate'),
                ev(cid, 2, None, 1, awarded=1, basis='reward-entitlement-certificate')]
    dest = build_compact(folder, tag='seeds', source_prov=prov,
                         builds=[dict(id=cid, encounter=19, defeat=0, scenario=base)],
                         evidence=evidence)
    store, coordinator, _prov = coordinator_for(dest, cid)
    try:
        reserved = coordinator._seed_freshness(store)
        check('compact-seed-freshness-usable', reserved is not None, coordinator._freshness_blocked)
        check('known-custom-seed-excluded', reserved is not None and tuple(off_bank) in reserved)
        coverage = (coordinator._freshness_report or {}).get('coverage') or {}
        check('missing-coverage-limited-claim', coverage.get('partial') is True, coverage)
        check('coverage-limitation-surfaced',
              any('partial' in note for note in coordinator.limitations), coordinator.limitations)
        check('seed-history-ready-not-blocked', coordinator._seed_history_ready(store) is True)
        drawn = coordinator._fresh_pairs(store, [cid], 4)
        check('fresh-draw-avoids-known-seed',
              drawn and all(tuple(p) != tuple(off_bank) for p in drawn), drawn)
        check('report-exposes-history-coverage',
              (coordinator.report().get('historyCoverage') or {}).get('partial') is True)
        return dict(reserved=len(reserved), partial=coverage.get('partial'))
    finally:
        store.close()


def current_evidence_wins(folder):
    """A fresh development view of a candidate displaces that candidate's own historical prior."""
    prov = provenance()
    base = default_scenario()
    cid = domain.identity(base)
    dest = build_compact(folder, tag='wins', source_prov=prov,
                         builds=[dict(id=cid, encounter=19, defeat=0, scenario=base)],
                         evidence=[ev(cid, 0, seed_pair('validation', 0), 1, awarded=2,
                                      basis='reward-entitlement-certificate')])
    store, coordinator, _prov = coordinator_for(dest, cid)
    try:
        development = dict(candidateId=cid, window='development',
                           compatibility=coordinator._compatibility(), meanEarned=9.0,
                           resolved=4, samples=4, total=4, features={'synthetic': 1.0},
                           evidenceClass='development', independentConfirmation=False)
        coordinator._observations = lambda db, owner=None: [dict(development)]
        captured, restore = spy_on_fit()
        try:
            coordinator._fit_adviser(store.db)
        finally:
            restore()
        same = [o for o in captured.get('observations', []) if str(o['candidateId']) == cid]
        check('current-evidence-wins', len(same) == 1 and same[0].get('meanEarned') == 9.0
              and same[0].get('evidenceClass') == 'development', same)
        return dict(observations=len(captured.get('observations', [])))
    finally:
        store.close()


def budgets_conserved_and_restart(folder):
    """A real bounded cycle charges only its own paired experiments; a restart does not re-charge."""
    prov = provenance()
    base = default_scenario()
    cid = domain.identity(base)
    dest = build_compact(folder, tag='cycle', source_prov=prov,
                         builds=[dict(id=cid, encounter=19, defeat=0, scenario=base)],
                         evidence=[ev(cid, i, seed_pair('validation', i), 1, awarded=2,
                                      basis='reward-entitlement-certificate') for i in range(4)])
    search.EVALUATOR_FACTORY = (lambda *, workers, telemetry, timeout:
                                evaluation.Evaluator(workers=workers, telemetry=telemetry,
                                                     pool=RewardPool(), timeout=10.0))
    store = Store(dest, prov)
    try:
        coordinator = search.Coordinator(path=dest, revision=prov['digest'], mode='community-first',
                                         encounter=19, reference=cid, workers=2, telemetry=True,
                                         maximum=6)
        config = search.default_config('community-first', purposes={
            'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
        store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                        previous={}))
        coordinator.load(store)
        deadline = time.time() + 60
        while time.time() < deadline:
            coordinator.run_pass(store, running=True)
            if coordinator.state.get('publication'):
                break
            if (not coordinator.busy and coordinator.state.get('current') is None
                    and not coordinator.state.get('confirmation')
                    and coordinator._next_purpose(store.db) is None):
                break
            time.sleep(0.02)
        before = ledger.session_status(store.db, coordinator.session_id)
        check('budgets-conserved', before['conserved'] is True, before['budgets'])
        improvement = [row for row in before['budgets'] if row['purpose'] == 'improvement']
        check('budget-charges-are-paired',
              all(row['completed'] % 2 == 0 and row['completed'] <= row['total']
                  for row in improvement), improvement)
        check('no-budget-overdraw', all(row['reserved'] <= row['total'] for row in before['budgets']))

        restarted = search.Coordinator(path=dest, revision=prov['digest'], mode='community-first',
                                       encounter=19, reference=cid, workers=2, telemetry=True,
                                       maximum=6)
        restarted.load(store)
        restarted.run_pass(store, running=True)
        after = ledger.session_status(store.db, restarted.session_id)
        check('restart-does-not-double-charge', after['budgets'] == before['budgets'],
              (before['budgets'], after['budgets']))
        return dict(completed=sum(row['completed'] for row in before['budgets']))
    finally:
        search.EVALUATOR_FACTORY = None
        store.close()


def aggregate_only(folder):
    """A pruned aggregate is retained aggregate-only and never surfaced as a measurement prior."""
    prov = provenance()
    base = default_scenario()
    scenario = propose(base, 2)
    cid = domain.identity(scenario)
    raw = json.dumps({'n': 9, 'mean': 3.5, 'rank': 2, 'score': 88, 'histogram': {'3': 4}})
    source = folder / 'source-agg.sqlite'
    dest = folder / 'summary-agg.sqlite'
    import zlib
    write_source(source, prov=prov, builds=[], evidence=[])
    db = sqlite3.connect(str(source))
    try:
        db.execute('INSERT INTO predictor_history VALUES(?,?,?,?,?,?,?,?)',
                   (cid, zlib.compress(json.dumps(scenario).encode(), 1), cid, 19, 1, raw, None, 1))
        db.commit()
    finally:
        db.close()
    summary.build_library([dict(path=str(source), label='agg', ownership='community')], dest,
                          authorised=True, batch_size=4)
    view = summary.aggregate_evidence(dest)
    check('aggregate-preserved', view['count'] == 1, view)
    record = view['records'][0] if view['records'] else {}
    fields = record.get('fields') or {}
    check('aggregate-drops-rankings', 'mean' in fields and 'n' in fields
          and all(key not in fields for key in ('rank', 'score')), fields)
    check('aggregate-not-confirmed', record.get('confirmedReward') is False
          and record.get('independentConfirmation') is False and record.get('hasSeedIdentity') is False)
    store, coordinator, _prov = coordinator_for(dest, None)
    try:
        result = legacy.load_observations(dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
                                          policy={'finishPolicy': 'on-verdict'},
                                          mechanics_revision='m', engine_revision=prov)
        check('aggregate-only-not-a-prior', not result['observations'], result['observations'])
    finally:
        store.close()
    return dict(records=view['count'])


def main():
    folder = new_dir('all')
    report = dict(
        module='check_history_runtime',
        priorAndContract=prior_and_contract(folder),
        certificateGrant=certificate_grant(folder),
        seedCoverage=seed_coverage(folder),
        currentEvidenceWins=current_evidence_wins(folder),
        budgets=budgets_conserved_and_restart(folder),
        aggregateOnly=aggregate_only(folder),
    )
    passed = sum(1 for _name, ok in RESULTS if ok)
    report['checks'] = dict(passed=passed, total=len(RESULTS))
    print(json.dumps(report, sort_keys=True))
    print('%d/%d history-runtime checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
