"""Focused checks for the bounded legacy-observation reader.

Run:  <venv>/python -B -X utf8 tools/recovery/check_legacy_observations.py

Deterministic, read-only, no battles, no live library. Builds small synthetic SQLite fixtures that
match the real optimiser schema (meta / candidate / candidate_meta / run / evidence) and asserts
the reader's compatibility, dedupe, bound, fail-closed and non-mutation behaviour. If
``KA_LEGACY_OBSERVATIONS_BASELINE`` points at a frozen ``baseline.sqlite`` it additionally runs one
bounded read-only probe; it refuses to open any live library. Prints PASS/FAIL per check.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_build_domain as domain            # noqa: E402
import strategy_encounter_adviser as adviser      # noqa: E402
import strategy_legacy_observations as legacy      # noqa: E402

ENGINE = 'a' * 64
OTHER_ENGINE = 'b' * 64
MECHANICS = 'strategy-mechanics-3'

SCHEMA = (
    'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    '''CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL,
        label TEXT NOT NULL, source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL)''',
    '''CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
        defeat INTEGER NOT NULL, region TEXT NOT NULL)''',
    '''CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal))''',
    '''CREATE TABLE evidence(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
        seeds TEXT, verdict INTEGER, censored INTEGER NOT NULL DEFAULT 0, prizeCallbacks INTEGER,
        awardedChests INTEGER, awardedBasis TEXT, pendingChests INTEGER,
        PRIMARY KEY(candidate,phase,ordinal)) WITHOUT ROWID''',
    '''CREATE TABLE ea_holdout(experiment_id INTEGER NOT NULL, candidate_id TEXT NOT NULL,
        seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, result TEXT, outcome TEXT,
        PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b))''',
)

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(bool(condition))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _scratch_base():
    """A writable directory for fixtures. The sandbox may block %TEMP%, so prefer a repo scratch."""
    for candidate in (os.environ.get('KA_LEGACY_OBSERVATIONS_TMP'), HERE.parents[1] / 'tmp',
                      Path.cwd()):
        if not candidate:
            continue
        base = Path(candidate)
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ('.ka-legacy-probe-%d' % os.getpid())
            probe.write_text('x')
            probe.unlink()
            return base
        except OSError:
            continue
    raise RuntimeError('no writable directory available for fixtures')


def _file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


class Fixture:
    """A temporary directory of synthetic libraries; removed on close."""

    def __init__(self):
        # A unique, plain-permission directory: tempfile.mkdtemp's 0o700 mode is not writable
        # under the managed sandbox, while os.makedirs' default mode is.
        base = _scratch_base()
        self.dir = base / ('ka-legacy-obs-%d-0' % os.getpid())
        suffix = 0
        while self.dir.exists():
            suffix += 1
            self.dir = base / ('ka-legacy-obs-%d-%d' % (os.getpid(), suffix))
        os.makedirs(str(self.dir))
        self.libs = []

    def make(self, name, provenance=ENGINE, with_holdout=False):
        path = self.dir / name
        db = sqlite3.connect(str(path))
        for statement in SCHEMA:
            if 'ea_holdout' in statement and not with_holdout:
                continue
            db.execute(statement)
        if provenance is not None:
            db.execute('INSERT INTO meta VALUES (?,?)',
                       ('provenance', canonical(dict(
                           digest=provenance, count=1, missing=[],
                           files={'tools/recovery/combat_x.py': 'c' * 64}, mode='test'))))
        db.execute('INSERT INTO meta VALUES (?,?)', ('schema', '1'))
        db.execute('INSERT INTO meta VALUES (?,?)', ('totalRuns', '0'))
        db.commit()
        lib = _Lib(db, path)
        self.libs.append(lib)
        return lib

    def close(self):
        for lib in self.libs:
            try:
                lib.close()
            except Exception:  # noqa: BLE001 - best-effort teardown of a fixture
                pass
        shutil.rmtree(self.dir, ignore_errors=True)


class _Lib:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def close(self):
        self.db.commit()
        self.db.close()

    def add_candidate(self, scenario, encounter, defeat=0, label='fixture', region='r', cid=None):
        cid = cid or domain.identity(scenario)
        self.db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                        (cid, canonical(scenario), label, 'supplied', '{}', 1))
        self.db.execute('INSERT INTO candidate_meta VALUES (?,?,?,?)', (cid, encounter, defeat, region))
        return cid

    def add_evidence(self, cid, rows, phase=legacy.VALIDATION_PHASE):
        for row in rows:
            self.db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (cid, phase, row['ordinal'],
                             json.dumps(row['seeds']) if row.get('seeds') is not None else None,
                             row.get('verdict'), 1 if row.get('censored') else 0,
                             row.get('prize', 0), row.get('awarded'), row.get('basis'),
                             row.get('pending')))

    def add_run(self, cid, ordinal, result, phase=legacy.VALIDATION_PHASE):
        self.db.execute('INSERT INTO run VALUES (?,?,?,?)', (cid, phase, ordinal, canonical(result)))

    def add_holdout(self, cid, seed_a, seed_b, result):
        self.db.execute('INSERT INTO ea_holdout VALUES (?,?,?,?,?,?)',
                        (1, cid, seed_a, seed_b, canonical(result), None))

    def commit(self):
        self.db.commit()


def _unit(name, atk=50):
    return dict(name=name, human=True, monsterId=None, weaponId=35, skills=[26, 110, 25, 24, 23, 22],
                invocationLevels=[0, 0, 0, 0, 0, 0], equipment=[dict(id=35, level=1, affinity=1)],
                leaderIdentity=False, visitor=False,
                parameters={str(pid): dict(rawValue=(atk if pid == 13 else 50), rawMax=50,
                                          extraValue=0, extraMax=0, trainingLevel=1)
                            for pid in (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)})


def scenario(encounter, defeat=0, finish='on-verdict', atk=50):
    return dict(encounterId=encounter, defeatCount=defeat, finishPolicy=finish, holyHerbStock=0,
                inputs=[], schema='ka-special-combat-research-1', tickLimit=30000,
                startProfile=dict(kind='isolated-scene0', bossCell=None, enemySpawnCell=[0, 0],
                                  startingStatus={}),
                mathSeed=7, libSeed=8, ownUnits=[_unit('A', atk), _unit('B', atk)])


def cert_win(ordinal, seeds, chests):
    return dict(ordinal=ordinal, seeds=seeds, verdict=1, awarded=chests, pending=chests,
                basis='reward-entitlement-certificate', prize=chests)


def unproven(ordinal, seeds, pending):
    return dict(ordinal=ordinal, seeds=seeds, verdict=1, awarded=None, pending=pending,
                basis='unknown-win-without-certificate', prize=pending)


def loss(ordinal, seeds):
    return dict(ordinal=ordinal, seeds=seeds, verdict=2, awarded=0, pending=0,
                basis='native-win-loss-gate', prize=0)


def censored(ordinal, seeds):
    return dict(ordinal=ordinal, seeds=seeds, verdict=None, censored=1, awarded=None,
                pending=None, basis='unknown-unresolved-battle', prize=None)


def unresolved(ordinal, seeds):
    return dict(ordinal=ordinal, seeds=seeds, verdict=None, awarded=None, pending=None,
                basis='unknown-unresolved-battle', prize=None)


def load(lib, cids, *, encounter=None, policy=None, engine=ENGINE, rows=256, maxc=24, cache=None):
    return legacy.load_observations(
        str(lib.path), cids,
        encounter=encounter if encounter is not None else {'encounterId': 19, 'defeatCount': 0},
        policy=policy if policy is not None else {'finishPolicy': 'on-verdict'},
        mechanics_revision=MECHANICS, engine_revision=engine,
        maximum_candidates=maxc, rows_per_candidate=rows, cache=cache)


def seed_pair(index):
    return [1000 + index, 2000 + index]


def main():
    fixtures = Fixture()
    try:
        # --- A. correct win / loss / unknown classification -----------------------------------
        lib = fixtures.make('a.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 5), loss(1, seed_pair(1)),
                               censored(2, seed_pair(2)), unresolved(3, seed_pair(3))])
        lib.commit()
        result = load(lib, [cid])
        obs = result['observations'][0] if result['observations'] else {}
        check('win/loss/unknown: all four classes classified',
              obs.get('byStatus', {}).get('certified') == 1 and obs.get('byStatus', {}).get('loss') == 1
              and obs.get('byStatus', {}).get('censored') == 1
              and obs.get('byStatus', {}).get('unresolved') == 1, obs.get('byStatus'))
        check('win/loss/unknown: loss is a safe zero, win is its certified count',
              obs.get('counts', {}).get('zero') == 1 and obs['summary'].get('max') == 5
              and obs['summary'].get('min') == 0, obs.get('counts'))
        check('win/loss/unknown: unknown rows are neither dropped nor zeroed',
              obs.get('counts', {}).get('unknown') == 2 and obs.get('counts', {}).get('resolved') == 2)
        check('win/loss/unknown: incomplete reading keeps meanEarned None and reports partialMean',
              obs.get('meanEarned') is None and obs.get('partialMean') == 2.5, obs.get('partialMean'))
        check('win/loss/unknown: quantiles and uncertainty retained',
              obs.get('quantiles', {}).get('p100') == 5 and obs.get('stdev') is not None)

        # --- B. an all-unknown candidate yields no mean at all --------------------------------
        lib = fixtures.make('b.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [unresolved(0, seed_pair(0)), unresolved(1, seed_pair(1))])
        lib.commit()
        obs = load(lib, [cid])['observations'][0]
        check('unknown-only: no fabricated mean', obs['meanEarned'] is None and obs['resolved'] == 0
              and obs['unknownCount'] == 2 and obs['partialMean'] is None, obs.get('counts'))
        model = adviser.fit([obs], compatibility=obs['compatibility'])
        check('unknown-only: adviser refuses (coverage), never a fabricated prior',
              model['status'] == 'coverage', model.get('status'))

        # --- C. the declared on-verdict policy resolves a win's pending (canonical policy) -----
        lib = fixtures.make('c.sqlite')
        cid = lib.add_candidate(scenario(19, finish='on-verdict'), 19)
        lib.add_evidence(cid, [unproven(0, seed_pair(0), 7)])
        lib.commit()
        obs = load(lib, [cid], policy={'finishPolicy': 'on-verdict'})['observations'][0]
        check('declared on-verdict: pending is the dispatched quantity, not a fabricated zero',
              obs['byStatus'].get('terminal-policy-dispatch') == 1 and obs['meanEarned'] == 7)

        # --- D. no cross-candidate pooling ----------------------------------------------------
        lib = fixtures.make('d.sqlite')
        win_id = lib.add_candidate(scenario(19, atk=50), 19)
        loss_id = lib.add_candidate(scenario(19, atk=51), 19)
        lib.add_evidence(win_id, [cert_win(0, seed_pair(0), 10), cert_win(1, seed_pair(1), 10)])
        lib.add_evidence(loss_id, [loss(0, seed_pair(0)), loss(1, seed_pair(1))])
        lib.commit()
        by_id = {o['candidateId']: o for o in load(lib, [win_id, loss_id])['observations']}
        check('no pooling: each candidate keeps its own rows',
              by_id[win_id]['meanEarned'] == 10 and by_id[win_id]['counts']['loss'] == 0
              and by_id[loss_id]['meanEarned'] == 0 and by_id[loss_id]['counts']['loss'] == 2)

        # --- E/F/G. engine / policy / encounter mismatch --------------------------------------
        lib = fixtures.make('e.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 3)])
        lib.commit()
        mismatch = load(lib, [cid], engine=OTHER_ENGINE)
        check('engine mismatch: fail closed with a specific reason',
              not mismatch['observations'] and mismatch['counts']['byReason'] == {'engine-revision-mismatch': 1})
        rejected = load(lib, [cid], policy={'finishPolicy': 'none'})
        check('policy mismatch: excluded with reason',
              not rejected['observations'] and rejected['counts']['byReason'] == {'policy-mismatch': 1})
        rejected = load(lib, [cid], encounter={'encounterId': 20, 'defeatCount': 0})
        check('encounter mismatch: excluded with reason',
              not rejected['observations'] and rejected['counts']['byReason'] == {'encounter-mismatch': 1})

        mapping = dict(digest=ENGINE, count=1, missing=[], mode='test',
                       files={'tools/recovery/combat_x.py': 'c' * 64})
        accepted = load(lib, [cid], engine=mapping)
        check('engine mapping: an exact digest inside a full provenance mapping is accepted',
              accepted['compatibility']['basis'] == 'exact'
              and accepted['counts']['includedCandidates'] == 1, accepted['compatibility'])

        # --- H. missing library provenance ----------------------------------------------------
        lib = fixtures.make('h.sqlite', provenance=None)
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 3)])
        lib.commit()
        missing = load(lib, [cid])
        check('missing metadata: excluded with missing-stored-provenance',
              not missing['observations']
              and missing['counts']['byReason'] == {'missing-stored-provenance': 1})

        # --- I. candidate identity mismatch ---------------------------------------------------
        lib = fixtures.make('i.sqlite')
        cid = lib.add_candidate(scenario(19), 19, cid='deadbeef')
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 3)])
        lib.commit()
        wrong_id = load(lib, [cid])
        check('identity mismatch: excluded with reason',
              not wrong_id['observations']
              and wrong_id['counts']['byReason'] == {'identity-mismatch': 1})

        # --- J/J2. cap and deterministic order -------------------------------------------------
        lib = fixtures.make('j.sqlite')
        ids = []
        for index in range(30):
            candidate = lib.add_candidate(scenario(19, atk=40 + index), 19)
            lib.add_evidence(candidate, [cert_win(0, seed_pair(0), index)])
            ids.append(candidate)
        lib.commit()
        capped = load(lib, ids, maxc=24)
        check('cap: maximum_candidates bounds the read',
              capped['counts']['includedCandidates'] == 24
              and capped['counts']['byReason'].get('above-candidate-cap') == 6)
        check("cap: selection preserves the caller order, not reward-ranked",
              [o['candidateId'] for o in capped['observations']] == ids[:24])

        # --- K. duplicate seed pairs dedupe ---------------------------------------------------
        lib = fixtures.make('k.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 5), cert_win(1, seed_pair(1), 5),
                               cert_win(2, seed_pair(0), 999)])
        lib.commit()
        obs = load(lib, [cid])['observations'][0]
        check('dedupe: a repeated seed pair counts once and the first wins',
              obs['ordinals']['filtered'] == 1 and obs['total'] == 2 and obs['meanEarned'] == 5,
              obs['ordinals'])

        # --- L. malformed rows fail closed with a count ---------------------------------------
        lib = fixtures.make('l.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 5), dict(ordinal=1, seeds=None, verdict=1,
                                                                  awarded=None, pending=1,
                                                                  basis='unknown-win-without-certificate')])
        lib.commit()
        bad = load(lib, [cid])
        check('malformed seeds: candidate excluded with a count, never silently zeroed',
              not bad['observations'] and bad['counts']['byReason'] == {'malformed-evidence': 1}
              and bad['excluded'][0]['counts']['malformed'] == 1, bad['excluded'])

        # --- M. rows_per_candidate bound ------------------------------------------------------
        lib = fixtures.make('m.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(i, seed_pair(i), 2) for i in range(10)])
        lib.commit()
        bounded = load(lib, [cid], rows=3)['observations'][0]
        check('rows_per_candidate: bounded deterministic ordinal prefix',
              bounded['ordinals']['considered'] == 3 and bounded['ordinals']['range'] == [0, 2]
              and bounded['ordinals']['bankSize'] == 10, bounded['ordinals'])

        # --- N. holdout / confirmation data never leaks ---------------------------------------
        lib = fixtures.make('n.sqlite', with_holdout=True)
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 4), cert_win(1, seed_pair(1), 4)])
        lib.add_evidence(cid, [cert_win(0, [9, 9], 1000)], phase='holdout')
        lib.add_run(cid, 0, run_win([9, 9], 1000), phase='holdout')
        lib.add_holdout(cid, 9, 9, run_win([9, 9], 1000))
        lib.commit()
        obs = load(lib, [cid])['observations'][0]
        check('no holdout leak: only the validation bank is read',
              obs['total'] == 2 and 'holdout' not in json.dumps(obs), obs['ordinals'])

        # --- O. retained run payload used when the compact table has no rows -------------------
        lib = fixtures.make('o.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_run(cid, 0, run_win([1, 2], 6))
        lib.add_run(cid, 1, run_loss([3, 4]))
        lib.add_run(cid, 2, run_win([8, 8], 50), phase='holdout')
        lib.commit()
        obs = load(lib, [cid])['observations'][0]
        check('run fallback: retained replay read when compact rows absent, holdout ignored',
              obs['ordinals']['source'] == 'run-replay' and obs['total'] == 2, obs['ordinals'])

        # --- P. non-mutation ------------------------------------------------------------------
        lib = fixtures.make('p.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 5)])
        lib.commit()
        lib.close()
        digest_before = hashlib.sha256(Path(lib.path).read_bytes()).hexdigest()
        load(lib, [cid])
        digest_after = hashlib.sha256(Path(lib.path).read_bytes()).hexdigest()
        check('non-mutation: the library file is byte-identical after a read',
              digest_before == digest_after)
        store = legacy.open_readonly(str(lib.path))
        try:
            store.db.execute('INSERT INTO meta VALUES (?,?)', ('x', '1'))
            refused = False
        except sqlite3.OperationalError:
            refused = True
        store.close()
        check('non-mutation: a read-only store refuses a write', refused)

        # --- Q. cache reuse without aliasing --------------------------------------------------
        lib = fixtures.make('q.sqlite')
        cid = lib.add_candidate(scenario(19), 19)
        lib.add_evidence(cid, [cert_win(0, seed_pair(0), 5)])
        lib.commit()
        cache = {}
        first = load(lib, [cid], cache=cache)
        second = load(lib, [cid], cache=cache)
        check('cache: a caller cache is populated and reused', bool(cache)
              and first['observations'] == second['observations'])
        second['observations'][0]['meanEarned'] = 999
        third = load(lib, [cid], cache=cache)
        check('cache: returned observations are copies, not the cached object',
              third['observations'][0]['meanEarned'] == 5)

        # --- R. adviser integration -----------------------------------------------------------
        lib = fixtures.make('r.sqlite')
        ids = []
        for index in range(14):
            candidate = lib.add_candidate(scenario(19, atk=40 + index), 19)
            lib.add_evidence(candidate, [cert_win(0, seed_pair(0), 3 + index),
                                         cert_win(1, seed_pair(1), 5 + index)])
            ids.append(candidate)
        lib.commit()
        result = load(lib, ids)
        check('adviser: complete compatible readings are adviser-eligible',
              result['counts']['adviserEligible'] == 14
              and all(o['features'] and o['window'] == 'development' for o in result['observations']))
        model = adviser.fit(result['observations'], compatibility=result['compatibility']['compatibilityKey'])
        predicted = adviser.predict(model, result['observations'][0]['features']) if model.get('trees') else {}
        check('adviser: prior fits and predicts', model['status'] == 'fitted'
              and predicted.get('status') == 'predicted', model.get('status'))

        # --- S. source scope separated from learner compatibility -----------------------------
        obs = result['observations'][0]
        check('provenance: source scope separated from current learner compatibility',
              result['provenance']['source']['measurementWindow'] == 'legacy:validation'
              and result['provenance']['adviserWindow'] == 'development'
              and obs['independentConfirmation'] is False and obs['confirmationEligible'] is False
              and obs['evidenceClass'] == 'historical-prior'
              and obs['sourcePhase'] == 'validation')
        check('result: JSON-serializable with strict (no-NaN) encoding',
              bool(json.dumps(result, allow_nan=False)))
    finally:
        fixtures.close()

    probe_baseline()
    passed = sum(1 for ok in RESULTS if ok)
    print('TOTAL %d/%d' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


def run_win(seeds, chests):
    return dict(seeds=seeds, verdict=1, censored=False, prizeCallbacks=chests,
                rewardOutcome=dict(awardedChests=chests, pendingChests=chests,
                                   awardedBasis='reward-entitlement-certificate'))


def run_loss(seeds):
    return dict(seeds=seeds, verdict=2, censored=False, prizeCallbacks=0,
                rewardOutcome=dict(awardedChests=0, pendingChests=0,
                                   awardedBasis='native-win-loss-gate'))


def _refuse_live(path):
    text = str(path).lower()
    return 'live' in text or Path(path).name.lower() in ('strategiesv18.sqlite', 'strategiesv18.sqlite-wal')


def probe_baseline():
    """One bounded read-only probe of a frozen baseline, if the caller points at one."""
    configured = os.environ.get('KA_LEGACY_OBSERVATIONS_BASELINE')
    if not configured:
        print('PROBE skipped: set KA_LEGACY_OBSERVATIONS_BASELINE to a frozen baseline.sqlite')
        return
    path = Path(configured)
    if _refuse_live(path):
        check('baseline probe refused a live library', False, str(path))
        return
    if not path.is_file():
        print('PROBE skipped: %s does not exist' % path)
        return
    store = legacy.open_readonly(str(path), immutable=True)
    try:
        provenance = store.get('provenance') or {}
        scope = store.get('scope')
        scope = json.loads(scope) if isinstance(scope, str) else scope
        encounter = (scope or {}).get('encounterId', 0)
        ids = [row[0] for row in store.db.execute(
            'SELECT id FROM candidate_meta WHERE encounter=? ORDER BY id LIMIT 1', (encounter,))]
        before = _file_sha256(path)
        if not ids:
            print('PROBE: no candidate for encounter %r in %s' % (encounter, path.name))
            return
        policy = {key: (scope or {})[key] for key in legacy.POLICY_KEYS if isinstance(scope, dict)
                  and key in scope}
        compatible = legacy.load_observations(
            store, ids, encounter=dict(encounterId=encounter, defeatCount=0), policy=policy,
            mechanics_revision='strategy-mechanics-3', engine_revision=provenance.get('digest'),
            maximum_candidates=1, rows_per_candidate=64)
        closed = legacy.load_observations(
            store, ids, encounter=dict(encounterId=encounter, defeatCount=0), policy=policy,
            mechanics_revision='strategy-mechanics-3', engine_revision='0' * 64,
            maximum_candidates=1, rows_per_candidate=64)
        after = _file_sha256(path)
        print('PROBE baseline=%s encounter=%r candidates=%d' % (path.name, encounter, len(ids)))
        print('PROBE stored-digest=%s exact-basis=%s included=%d rows=%d'
              % (str(provenance.get('digest'))[:12], compatible['compatibility']['basis'],
                 compatible['counts']['includedCandidates'],
                 compatible['counts']['rowsConsidered']))
        print('PROBE wrong-engine reason=%s (fail closed)'
              % (closed['compatibility']['reason'],))
        check('baseline probe: frozen file byte-identical after the read', before == after)
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())


