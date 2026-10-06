"""Focused checks for `strategy_support_evidence.diagnose`.

    <venv>/python -B -X utf8 tools/recovery/check_support_evidence.py

Deterministic, read-only, no battles, no simulator import (the solver can never be reached from a
support diagnosis). The fixtures use the FROZEN one-ordered-pair ledger API and the REAL canonical
schemas: the compact development result (`mpMetrics`, `encounterTelemetry.ownDeaths/ownRoles`) and the
REAL operating-region boundary payload (`strategy_operating_regions.question` -> `study`, the paired
`assessment`, the per-experiment `testedValue`, `published`), written into temporary SQLite databases
only. Nothing under the repo is written.

Every fixture name below is a behavioural assertion, not a measurement of the game:

  * a genuine parent MP crossing (strictly before an observed run end) triggers MP repair; a crossing
    with no observed run end does not;
  * a different candidate does not borrow it; a foreign owner, a stale compatibility digest, a holdout
    pair and a non-development window are all rejected;
  * missing telemetry and a fodder crossing never trigger;
  * a death - before or after the boss/certificate, at the run end, DPS or healer - is only a
    hypothesis and NEVER triggers HP/DEF on its own;
  * a persisted, compatible, resolved, single-axis supported-degraded support-boundary point whose
    DEGRADED TESTED CHILD is THIS candidate triggers the tested HP or DEF axis (and names the tested
    role); its distinct HIGH reference stays unrepaired, a sibling stays unrepaired, and a merely
    held-constant support axis in fixedFields is not treated as a change; an actually multi-axis,
    compensated, unverifiable, unresolved, foreign, fodder, foreign-owner, incompatible or
    non-development boundary does not trigger;
  * an MP crossing is preferred when a degraded boundary point is also present;
  * a large reward with no telemetry trigger does not trigger (reward is not a predictor).
"""
from __future__ import annotations

import contextlib
from copy import deepcopy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_experiment_store as ledger          # noqa: E402
import strategy_mp_recovery as mp_recovery           # noqa: E402
import strategy_optimizer_adapter as adapter         # noqa: E402
import strategy_operating_regions as regions         # noqa: E402
import strategy_students as students                 # noqa: E402
import strategy_support_evidence as support          # noqa: E402
import search_contract as contract                   # noqa: E402

FAILURES = []
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))
    if not condition:
        FAILURES.append(name)


SCENARIO = adapter.default_scenario()
WATCHED, _WATCH_DETAIL = mp_recovery.watch_units(SCENARIO)
DPS_NAME, HEALER_NAME = WATCHED[0], WATCHED[1]
FODDER_NAME = next(unit['name'] for unit in SCENARIO['ownUnits']
                   if students.role(unit) == students.ROLE_FODDER)

#: The canonical source index of each role in the scenario's own units (== the field index).
DPS_INDEX = next(i for i, unit in enumerate(SCENARIO['ownUnits'])
                 if students.role(unit) == students.ROLE_DPS)
HEALER_INDEX = next(i for i, unit in enumerate(SCENARIO['ownUnits'])
                    if students.role(unit) == students.ROLE_HEALER)
FODDER_INDEX = next(i for i, unit in enumerate(SCENARIO['ownUnits'])
                    if students.role(unit) == students.ROLE_FODDER)
HP, MP, DEF = 10, 11, 14


def field(index, parameter_id):
    return 'ownUnits.%d.parameters.%d' % (index, parameter_id)


DPS_HP_FIELD, DPS_DEF_FIELD = field(DPS_INDEX, HP), field(DPS_INDEX, DEF)
HEALER_HP_FIELD = field(HEALER_INDEX, HP)
FODDER_HP_FIELD = field(FODDER_INDEX, HP)

DPS_ID, HEALER_ID, FODDER_ID = 9001, 9002, 9003
OWN_ROLES = {str(DPS_ID): students.ROLE_DPS, str(HEALER_ID): students.ROLE_HEALER,
             str(FODDER_ID): students.ROLE_FODDER}
COMPAT = 'compat-live-1'
WINDOW = 'development'
POLICY = {'finishPolicy': 'on-verdict', 'tag': 'A'}
ENC, MECH, ENGINE = 'enc-A', 'mech-A', 'rev-A'


def mp_entry(name, identity, *, low, tick, percent=2, minimum=7):
    return dict(name=name, identity=int(identity), minimumMp=int(minimum),
                minimumMpPercent=int(percent), reachedLowMp=bool(low), firstLowMpTick=int(tick),
                firstLowMpPhase='after-fighters', reachedZero=False)


def result(seeds, *, ticks=100, mp=None, telemetry=True, deaths=None, certificate=None,
           boss_death=-1, awarded=0, prize=0):
    """One compact result shaped exactly like the persisted development payload."""
    payload = dict(verdict=1, censored=False, ticks=ticks, seeds=list(seeds), survivors=2,
                   resourceUses=0, healthFraction=0.5,
                   behavior=dict(attacks=10, heals=3, prizes=prize), prizeCallbacks=prize,
                   herbMetrics=dict(startingStock=0, remainingStock=0, maxUses=0, useCount=0,
                                    uses=[]),
                   rewardOutcome=dict(pendingChests=awarded, awardedChests=awarded,
                                      awardedBasis='fixture-no-entitlement', inventoryVerified=False),
                   progressMetrics={})
    if mp is not None:
        payload['mpMetrics'] = list(mp)
    if certificate is not None:
        payload['rewardOutcome']['certificate'] = dict(
            certificateId='ka-reward-entitlement-certificate-1', holds=True, frame=int(certificate),
            issuedBeforeVerdict=True, timingRule='pre-verdict')
    if telemetry:
        payload['encounterTelemetry'] = dict(
            version=3, ownIdentities=[DPS_ID, HEALER_ID, FODDER_ID], ownRoles=dict(OWN_ROLES),
            ownSurvivors=2, ownDeaths=dict(deaths or {}), ownLeavings={}, bossIdentity=-1,
            bossFirstDeathTick=int(boss_death), bossLeavingTick=-1, bossLifecycle=None,
            finalStatus=dict(verdict=1, censored=False), finalReward=dict(),
            unavailable=dict(), notObserved=[])
    return payload


def build_scenario(overrides=()):
    """The real default build with `(index, parameter_id, value)` written by the shared contract writer."""
    scenario = deepcopy(SCENARIO)
    for index, parameter_id, value in overrides:
        contract.set_effective_parameter(scenario, scenario['ownUnits'][index], parameter_id, int(value))
    return scenario


def support_question(reference, *, question_field, value, fixed_fields=None, adjustable=()):
    """One frozen support question, built by the REAL conditional-regions service."""
    fields = {question_field: int(value)}
    fields.update(fixed_fields or {})
    return regions.question(kind='support', reference_id=reference, encounter_revision=ENC,
                            mechanics_revision=MECH, policy=dict(POLICY), constraints={},
                            field=question_field, fixed_fields=fields,
                            adjustable_fields=list(adjustable), tolerance=0.9)


def paired_rows(candidate_id, earned, count, start):
    """One real ordered-pair arm as the coordinator stores it for a boundary assessment."""
    return [dict(seeds=[start + i, start + 5000 + i], finalEarned=float(earned), resolved=True,
                 policy=dict(POLICY), candidateId=candidate_id, encounterRevision=ENC,
                 mechanicsRevision=MECH) for i in range(count)]


def real_assessment(question, child_id, *, degraded=True, count=16):
    """A REAL paired classification: a reduced child measurably loses, or matches its reference."""
    value = dict(field=question['field'], kind='support',
                 value=question['fixedFields'][question['field']])
    return regions.assess(
        question, paired_rows(question['referenceId'], 100.0, count, 700),
        paired_rows(child_id, 40.0 if degraded else 100.0, count, 700), candidate_id=child_id,
        value=value, minimum_pairs=count)


def support_experiment(db, *, child_id, reference_id, child_scenario, reference_scenario,
                       changed_fields, owner='community', compat=COMPAT, window=WINDOW, tag='',
                       include_intent=True):
    """A real development experiment intent carrying the observed scenarios the coordinator emits."""
    intent = dict(scope=owner, owner=owner, ownerShare=1.0, parentId=reference_id,
                  referenceId=reference_id, purpose='support', planned_budget=64,
                  stopping={'maxRuns': 32, 'case': tag}, policy=dict(POLICY), mechanicsRevision=MECH,
                  encounterRevision=ENC, engineRevision=ENGINE, compatibility=compat,
                  measurementWindow=window)
    if include_intent:
        intent['changedFields'] = list(changed_fields)
        intent['fixedFields'] = {}
        intent['observedScenarios'] = {reference_id: reference_scenario, child_id: child_scenario}
    return ledger.create_experiment(db, intent)


def put_boundary(db, experiment_id, question, assessment, *, published=True):
    tested_value = dict(field=question['field'], kind=question['kind'],
                        value=question['fixedFields'][question['field']])
    tested_points = ([dict(candidateId=assessment['candidateId'], value=tested_value,
                           classification=assessment['classification'],
                           pairedCount=assessment['pairedCount'])] if published else [])
    gaps = ([] if published else [dict(candidateId=assessment['candidateId'], value=tested_value,
                                     classification=assessment['classification'],
                                     reason=assessment.get('reason'))])
    payload = dict(study=question, assessment=assessment, testedValue=tested_value, inference=None,
                   safeCartesianProductClaimed=False, published=bool(published))
    ledger.record_boundary(db, experiment_id, 'support', question['referenceId'],
                           tested_points, gaps, payload)


def connect(path):
    db = sqlite3.connect(path)
    ledger.initialize(db)
    db.commit()
    return db


def make_experiment(db, *, owner='community', compat=COMPAT, window=WINDOW, policy=POLICY):
    intent = dict(scope=owner, owner=owner, ownerShare=1.0, parentId='parent-1',
                  referenceId='ref-1', purpose='improvement', planned_budget=64,
                  stopping={'maxRuns': 1}, policy=dict(policy), mechanicsRevision='mech-A',
                  encounterRevision='enc-A', engineRevision='rev-A', compatibility=compat,
                  measurementWindow=window)
    return ledger.create_experiment(db, intent)


def put(db, experiment_id, candidate, seeds, payload):
    assert ledger.reserve(db, experiment_id, candidate, list(seeds)) is True
    assert ledger.complete(db, experiment_id, candidate, list(seeds), payload) is True


def diagnose(db, candidate, *, scenario=SCENARIO, owner='community', compat=COMPAT, maximum_rows=256):
    return support.diagnose(db, candidate_id=candidate, scenario=scenario, owner=owner,
                            compatibility=compat, maximum_rows=maximum_rows)


def boundary(db, experiment_id, child_id, *, reference_id, question_field, value, child_scenario,
             reference_scenario, changed_fields, fixed_fields=None, adjustable=(), degraded=True,
             published=True, assessment=None):
    """One REAL support boundary: the degraded TESTED CHILD is `child_id`, its reference is distinct."""
    question = support_question(reference_id, question_field=question_field, value=value,
                                fixed_fields=fixed_fields, adjustable=adjustable)
    if assessment is None:
        assessment = real_assessment(question, child_id, degraded=degraded)
    put_boundary(db, experiment_id, question, assessment, published=published)
    return question, assessment


@contextlib.contextmanager
def scratch():
    """A writable fixture directory under the repo scratch (the sandbox may block %TEMP%)."""
    base = HERE.parents[1] / 'tmp'
    base.mkdir(parents=True, exist_ok=True)
    directory, index = base / ('ka-support-evidence-%d' % os.getpid()), 0
    while directory.exists():
        index += 1
        directory = base / ('ka-support-evidence-%d-%d' % (os.getpid(), index))
    os.makedirs(str(directory))
    try:
        yield str(directory)
    finally:
        shutil.rmtree(str(directory), ignore_errors=True)


def main():
    with scratch() as tmp:
        db = connect(str(Path(tmp) / 'library.sqlite'))
        dev = make_experiment(db)

        # 1. A genuine parent MP crossing (strictly before an observed run end) triggers MP repair.
        crossing = result([101, 102], ticks=100, telemetry=True,
                          mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=10, percent=2)])
        put(db, dev, 'cand-genuine', [101, 102], crossing)
        hit = diagnose(db, 'cand-genuine')
        check('genuine-parent-mp-crossing-triggers',
              hit['supported'] is True and hit['limitingRole'] == students.ROLE_DPS
              and list(hit['limitingStats']) == ['mp']
              and hit['parentTelemetry']['kind'] == 'mp-crossing'
              and hit['counts']['mpCrossings'] == 1,
              'role=%r stats=%r kind=%r' % (hit['limitingRole'], hit['limitingStats'],
                                            (hit['parentTelemetry'] or {}).get('kind')))

        # 1b. A crossing with no OBSERVED run end cannot be established (the rule is tick < ticks).
        put(db, dev, 'cand-no-end', [111, 112], result(
            [111, 112], ticks=None, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=10)]))
        no_end = diagnose(db, 'cand-no-end')
        check('missing-run-end-does-not-trigger-mp-crossing',
              no_end['supported'] is False and no_end['counts']['mpCrossings'] == 0
              and any('run end' in note for note in no_end['unavailable']),
              no_end['reasons'][0] if no_end['reasons'] else '')

        # 2. A different candidate never borrows another candidate's crossing.
        other = diagnose(db, 'cand-someone-else')
        check('wrong-candidate-does-not-trigger',
              other['supported'] is False and other['limitingRole'] is None
              and other['counts']['examined'] == 0 and other['counts']['boundaryExamined'] == 0)

        # 3. A foreign owner is rejected; the owner that recorded it still triggers.
        rebel = make_experiment(db, owner='rebel')
        put(db, rebel, 'cand-rebel', [201, 202], result(
            [201, 202], ticks=100, mp=[mp_entry(HEALER_NAME, HEALER_ID, low=True, tick=12)]))
        foreign = diagnose(db, 'cand-rebel', owner='community')
        own = diagnose(db, 'cand-rebel', owner='rebel')
        check('foreign-owner-does-not-trigger',
              foreign['supported'] is False and foreign['counts']['rejections']['foreignOwner'] == 1
              and own['supported'] is True and own['limitingRole'] == students.ROLE_HEALER)

        # 4. A stale compatibility digest (different policy/mechanics) is rejected.
        put(db, dev, 'cand-compat', [301, 302], result(
            [301, 302], ticks=100, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=9)]))
        stale = diagnose(db, 'cand-compat', compat='compat-OLD')
        live = diagnose(db, 'cand-compat')
        check('policy-or-mechanics-mismatch-does-not-trigger',
              stale['supported'] is False and stale['counts']['rejections']['incompatible'] == 1
              and live['supported'] is True)

        # 5. A frozen holdout pair is excluded even when it is otherwise a matching crossing.
        put(db, dev, 'cand-holdout', [401, 402], result(
            [401, 402], ticks=100, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=11)]))
        db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                   'VALUES(?,?,?,?)', (dev, 'cand-holdout', 401, 402))
        db.commit()
        holdout = diagnose(db, 'cand-holdout')
        check('holdout-pair-does-not-trigger',
              holdout['supported'] is False and holdout['counts']['rejections']['holdout'] == 1)

        # 5b. A non-development (future/holdout) window is rejected.
        future = make_experiment(db, window='holdout')
        put(db, future, 'cand-future', [501, 502], result(
            [501, 502], ticks=100, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=11)]))
        fut = diagnose(db, 'cand-future')
        check('non-development-window-does-not-trigger',
              fut['supported'] is False and fut['counts']['rejections']['notDevelopment'] == 1)

        # 6. Missing telemetry is unknown, not zero: it never triggers.
        put(db, dev, 'cand-missing', [601, 602], result([601, 602], telemetry=False))
        missing = diagnose(db, 'cand-missing')
        check('missing-telemetry-does-not-trigger',
              missing['supported'] is False and missing['counts']['admitted'] == 1
              and len(missing['unavailable']) >= 1
              and any('mpMetrics' in note for note in missing['unavailable']))

        # 7. A fodder crossing is never a trigger (never fodder).
        put(db, dev, 'cand-fodder', [701, 702], result(
            [701, 702], ticks=100, deaths={str(FODDER_ID): 5},
            mp=[mp_entry(FODDER_NAME, FODDER_ID, low=True, tick=5)]))
        fodder = diagnose(db, 'cand-fodder')
        check('fodder-does-not-trigger',
              fodder['supported'] is False and fodder['counts']['examined'] == 1)

        # 8. A death alone is a hypothesis, never an HP/DEF trigger: the persisted telemetry cannot
        #    prove that this candidate's own HP/DEF was the limiting thing.
        put(db, dev, 'cand-death', [801, 802], result(
            [801, 802], ticks=100, deaths={str(DPS_ID): 5}, boss_death=-1,
            mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=80, minimum=5000)]))
        death = diagnose(db, 'cand-death')
        check('early-death-alone-does-not-trigger-hp-def',
              death['supported'] is False and death['limitingRole'] is None
              and death['counts']['deathsObserved'] == 1 and len(death['hypotheses']) == 1
              and list(death['limitingStats']) == [])

        # 8b. A healer death alone does NOT name the healer for HP/DEF.
        put(db, dev, 'cand-healer-death', [811, 812], result(
            [811, 812], ticks=100, deaths={str(HEALER_ID): 8},
            mp=[mp_entry(HEALER_NAME, HEALER_ID, low=False, tick=-1, percent=70, minimum=4000)]))
        healer_death = diagnose(db, 'cand-healer-death')
        check('healer-death-alone-does-not-trigger-hp-def',
              healer_death['supported'] is False and healer_death['limitingRole'] is None
              and healer_death['counts']['deathsObserved'] == 1)

        # 8c. A death BEFORE the boss death and before the certificate is still only a hypothesis.
        put(db, dev, 'cand-early-death', [821, 822], result(
            [821, 822], ticks=100, deaths={str(DPS_ID): 5}, certificate=40, boss_death=3,
            mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=80, minimum=5000)]))
        early = diagnose(db, 'cand-early-death')
        check('death-before-boss-and-certificate-does-not-trigger',
              early['supported'] is False and early['counts']['deathsObserved'] == 1)

        # 9. A death AFTER productive completion is not a failure - and neither triggers HP/DEF.
        put(db, dev, 'cand-late-death', [901, 902], result(
            [901, 902], ticks=100, deaths={str(DPS_ID): 60}, certificate=40, boss_death=-1,
            mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=60, minimum=3000)]))
        late = diagnose(db, 'cand-late-death')
        check('death-after-certificate-does-not-trigger',
              late['supported'] is False and late['counts']['deathsObserved'] == 1)

        # 9b. A death at the run's own end is reported, still without an HP/DEF trigger.
        put(db, dev, 'cand-end-death', [911, 912], result(
            [911, 912], ticks=100, deaths={str(DPS_ID): 100}, boss_death=-1,
            mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=60, minimum=3000)]))
        end_death = diagnose(db, 'cand-end-death')
        check('death-at-run-end-does-not-trigger', end_death['supported'] is False)

        # 10. Reward is not a predictor: a huge award with no telemetry trigger stays unsupported.
        rich = result([1001, 1002], ticks=100, deaths={}, awarded=999999, prize=999999,
                      mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=95, minimum=6000)])
        put(db, dev, 'cand-reward', [1001, 1002], rich)
        rewarded = diagnose(db, 'cand-reward')
        put(db, dev, 'cand-reward-lean', [1011, 1012], result(
            [1011, 1012], ticks=100, deaths={}, awarded=0, prize=0,
            mp=[mp_entry(DPS_NAME, DPS_ID, low=False, tick=-1, percent=95, minimum=6000)]))
        lean_out = diagnose(db, 'cand-reward-lean')
        check('reward-is-not-a-predictor',
              rewarded['supported'] is False and lean_out['supported'] is False
              and 'awardedChests' not in json.dumps(hit['parentTelemetry']))

        # 11. Bounded: an explicit row cap limits how many samples are examined.
        for ordinal in range(5):
            put(db, dev, 'cand-bounded', [1100 + ordinal, 1200 + ordinal], result(
                [1100 + ordinal, 1200 + ordinal], ticks=100,
                mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=10 + ordinal)]))
        bounded = diagnose(db, 'cand-bounded', maximum_rows=2)
        check('row-cap-is-applied',
              bounded['counts']['examined'] == 2 and bounded['counts']['mpCrossings'] == 2)

        # 12. Read-only: a diagnosis writes no row.
        before = db.total_changes
        diagnose(db, 'cand-genuine')
        diagnose(db, 'cand-boundary-hp')
        check('diagnosis-writes-nothing', db.total_changes == before)

        # 13. A library with no encounter ledger degrades to an explicit no-trigger, not a crash.
        bare = sqlite3.connect(str(Path(tmp) / 'bare.sqlite'))
        try:
            blank = support.diagnose(bare, candidate_id='cand-bare', scenario=SCENARIO,
                                     owner='community', compatibility=COMPAT)
            check('missing-ledger-does-not-trigger',
                  blank['supported'] is False and 'not readable' in blank['reasons'][0],
                  blank['reasons'][0] if blank['reasons'] else '')
        finally:
            bare.close()

        # ------------------------------------------------------------------------------------------
        # Controlled support-boundary evidence: the ONLY sound HP/DEF source.
        # ------------------------------------------------------------------------------------------

        # 14. The DEGRADED TESTED CHILD of a real paired support point triggers its tested HP axis and
        #     names the tested role. The distinct HIGH reference it was cut from is never raised by it.
        REF_HP, CHILD_HP = 'ref-high-hp', 'child-low-hp'
        child_hp = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_hp = support_experiment(db, child_id=CHILD_HP, reference_id=REF_HP,
                                    child_scenario=child_hp, reference_scenario=SCENARIO,
                                    changed_fields=[DPS_HP_FIELD], tag='hp')
        boundary(db, exp_hp, CHILD_HP, reference_id=REF_HP, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_hp, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        hp = diagnose(db, CHILD_HP, scenario=child_hp)
        ref = (hp['parentTelemetry'] or {}).get('evidenceRef') or {}
        check('degraded-child-support-hp-point-triggers-hp-axis',
              hp['supported'] is True and hp['limitingRole'] == students.ROLE_DPS
              and list(hp['limitingStats']) == ['hp']
              and hp['parentTelemetry']['kind'] == 'support-boundary-degraded'
              and ref.get('classification') == 'supported-degraded'
              and ref.get('testedField') == DPS_HP_FIELD and ref.get('pairedCount') == 16
              and ref.get('referenceId') == REF_HP and ref.get('testedCandidateId') == CHILD_HP
              and 'certificate' not in json.dumps(ref) and 'awarded' not in json.dumps(ref)
              and hp['counts']['boundaryAdmitted'] == 1,
              'role=%r stats=%r ref=%r' % (hp['limitingRole'], hp['limitingStats'],
                                           json.dumps(ref)[:200]))

        # 14b. The sufficient HIGH reference must stay unrepaired; a LOWER child losing is not evidence
        #      that an already-sufficient reference needs raising.
        high = diagnose(db, REF_HP, scenario=SCENARIO)
        check('sufficient-high-reference-is-not-raised-by-child-loss',
              high['supported'] is False and high['counts']['boundaryExamined'] == 0)

        # 14c. A degraded sibling must not raise an unrelated candidate either.
        sibling = diagnose(db, 'unrelated-build', scenario=child_hp)
        check('degraded-child-does-not-raise-unrelated-candidate',
              sibling['supported'] is False and sibling['counts']['boundaryExamined'] == 0)

        # 15. A reduced DPS DEF child triggers the DEF axis.
        REF_DEF, CHILD_DEF = 'ref-high-def', 'child-low-def'
        child_def = build_scenario([(DPS_INDEX, DEF, 100)])
        exp_def = support_experiment(db, child_id=CHILD_DEF, reference_id=REF_DEF,
                                     child_scenario=child_def, reference_scenario=SCENARIO,
                                     changed_fields=[DPS_DEF_FIELD], tag='def')
        boundary(db, exp_def, CHILD_DEF, reference_id=REF_DEF, question_field=DPS_DEF_FIELD, value=100,
                 child_scenario=child_def, reference_scenario=SCENARIO,
                 changed_fields=[DPS_DEF_FIELD])
        de = diagnose(db, CHILD_DEF, scenario=child_def)
        check('degraded-child-support-def-point-triggers-def-axis',
              de['supported'] is True and list(de['limitingStats']) == ['def']
              and de['parentTelemetry']['kind'] == 'support-boundary-degraded')

        # 16. A reduced healer HP child names the healer.
        REF_HEAL, CHILD_HEAL = 'ref-high-healer', 'child-low-healer'
        child_heal = build_scenario([(HEALER_INDEX, HP, 800)])
        exp_heal = support_experiment(db, child_id=CHILD_HEAL, reference_id=REF_HEAL,
                                      child_scenario=child_heal, reference_scenario=SCENARIO,
                                      changed_fields=[HEALER_HP_FIELD], tag='healer')
        boundary(db, exp_heal, CHILD_HEAL, reference_id=REF_HEAL, question_field=HEALER_HP_FIELD,
                 value=800, child_scenario=child_heal, reference_scenario=SCENARIO,
                 changed_fields=[HEALER_HP_FIELD])
        healer = diagnose(db, CHILD_HEAL, scenario=child_heal)
        check('degraded-child-healer-hp-point-names-healer',
              healer['supported'] is True and healer['limitingRole'] == students.ROLE_HEALER
              and list(healer['limitingStats']) == ['hp'])

        # 17. A held-constant support axis in fixedFields is NOT proof that axis changed: only the
        #     tested HP axis actually differs between the two OBSERVED builds, so the point stands.
        REF_KC, CHILD_KC = 'ref-held-def', 'child-held-def'
        ref_kc = build_scenario([(DPS_INDEX, DEF, 100)])
        child_kc = build_scenario([(DPS_INDEX, DEF, 100), (DPS_INDEX, HP, 1200)])
        exp_kc = support_experiment(db, child_id=CHILD_KC, reference_id=REF_KC,
                                    child_scenario=child_kc, reference_scenario=ref_kc,
                                    changed_fields=[DPS_HP_FIELD], tag='held')
        boundary(db, exp_kc, CHILD_KC, reference_id=REF_KC, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_kc, reference_scenario=ref_kc,
                 changed_fields=[DPS_HP_FIELD], fixed_fields={DPS_DEF_FIELD: 100})
        held = diagnose(db, CHILD_KC, scenario=child_kc)
        check('held-constant-fixed-support-axis-is-not-a-change',
              held['supported'] is True and list(held['limitingStats']) == ['hp'],
              json.dumps(held['counts']['boundaryRejections']))

        # 17b. The ACTUAL observed intervention moves HP AND DEF (compensated/multi-axis): ambiguous.
        REF_MA, CHILD_MA = 'ref-multi', 'child-multi'
        child_ma = build_scenario([(DPS_INDEX, HP, 1200), (DPS_INDEX, DEF, 100)])
        exp_ma = support_experiment(db, child_id=CHILD_MA, reference_id=REF_MA,
                                    child_scenario=child_ma, reference_scenario=SCENARIO,
                                    changed_fields=[DPS_HP_FIELD, DPS_DEF_FIELD], tag='multi')
        boundary(db, exp_ma, CHILD_MA, reference_id=REF_MA, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_ma, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD, DPS_DEF_FIELD], adjustable=[DPS_DEF_FIELD])
        multi = diagnose(db, CHILD_MA, scenario=child_ma)
        check('compensated-multi-axis-support-point-does-not-trigger',
              multi['supported'] is False
              and multi['counts']['boundaryRejections']['multiAxis'] == 1,
              json.dumps(multi['counts']['boundaryRejections']))

        # 17c. An intervention that cannot be verified (no observedScenarios, no changedFields) is
        #      UNKNOWN, and an unknown single-axis claim never triggers.
        REF_UK, CHILD_UK = 'ref-unverified', 'child-unverified'
        child_uk = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_uk = support_experiment(db, child_id=CHILD_UK, reference_id=REF_UK,
                                    child_scenario=child_uk, reference_scenario=SCENARIO,
                                    changed_fields=[], tag='unverified', include_intent=False)
        boundary(db, exp_uk, CHILD_UK, reference_id=REF_UK, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_uk, reference_scenario=SCENARIO, changed_fields=[])
        unverified = diagnose(db, CHILD_UK, scenario=child_uk)
        check('unverifiable-single-axis-support-point-stays-unknown',
              unverified['supported'] is False
              and unverified['counts']['boundaryRejections']['unresolved'] == 1,
              json.dumps(unverified['counts']['boundaryRejections']))

        # 18. A real supported-acceptable classification (child matches its reference) is not degraded.
        REF_ND, CHILD_ND = 'ref-equal', 'child-equal'
        child_nd = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_nd = support_experiment(db, child_id=CHILD_ND, reference_id=REF_ND,
                                    child_scenario=child_nd, reference_scenario=SCENARIO,
                                    changed_fields=[DPS_HP_FIELD], tag='equal')
        boundary(db, exp_nd, CHILD_ND, reference_id=REF_ND, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_nd, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD], degraded=False)
        nd = diagnose(db, CHILD_ND, scenario=child_nd)
        check('non-degraded-support-point-does-not-trigger',
              nd['supported'] is False
              and nd['counts']['boundaryRejections']['notDegraded'] == 1,
              json.dumps(nd['counts']['boundaryRejections']))

        # 18b. A degraded-but-incomplete pair comparison is not resolved.
        REF_INC, CHILD_INC = 'ref-incomplete', 'child-incomplete'
        child_inc = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_inc = support_experiment(db, child_id=CHILD_INC, reference_id=REF_INC,
                                     child_scenario=child_inc, reference_scenario=SCENARIO,
                                     changed_fields=[DPS_HP_FIELD], tag='incomplete')
        q_inc = support_question(REF_INC, question_field=DPS_HP_FIELD, value=1200)
        put_boundary(db, exp_inc, q_inc, dict(real_assessment(q_inc, CHILD_INC),
                                              totalReference=20, totalCandidate=20,
                                              unresolvedReference=4, unresolvedCandidate=4))
        incomplete = diagnose(db, CHILD_INC, scenario=child_inc)
        check('incomplete-pair-support-point-does-not-trigger',
              incomplete['supported'] is False
              and incomplete['counts']['boundaryRejections']['unresolved'] == 1)

        # 19. A boundary about a different child never contributes to this candidate.
        REF_F, CHILD_F = 'ref-foreign', 'child-foreign'
        child_f = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_f = support_experiment(db, child_id=CHILD_F, reference_id=REF_F,
                                   child_scenario=child_f, reference_scenario=SCENARIO,
                                   changed_fields=[DPS_HP_FIELD], tag='foreign')
        boundary(db, exp_f, CHILD_F, reference_id=REF_F, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_f, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        foreign_b = diagnose(db, 'cand-boundary-foreign')
        check('foreign-candidate-support-boundary-does-not-trigger',
              foreign_b['supported'] is False and foreign_b['counts']['boundaryExamined'] == 0)

        # 20. A point on a fodder is never named.
        REF_FOD, CHILD_FOD = 'ref-fodder', 'child-fodder'
        child_fod = build_scenario([(FODDER_INDEX, HP, 100)])
        exp_fod = support_experiment(db, child_id=CHILD_FOD, reference_id=REF_FOD,
                                     child_scenario=child_fod, reference_scenario=SCENARIO,
                                     changed_fields=[FODDER_HP_FIELD], tag='fodder')
        boundary(db, exp_fod, CHILD_FOD, reference_id=REF_FOD, question_field=FODDER_HP_FIELD,
                 value=100, child_scenario=child_fod, reference_scenario=SCENARIO,
                 changed_fields=[FODDER_HP_FIELD])
        fod = diagnose(db, CHILD_FOD, scenario=child_fod)
        check('fodder-support-point-does-not-trigger',
              fod['supported'] is False and fod['counts']['boundaryRejections']['fodder'] == 1)

        # 21. A boundary recorded by a foreign owner is rejected for the community-first view.
        REF_OWN, CHILD_OWN = 'ref-owner', 'child-owner'
        child_own = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_own = support_experiment(db, owner='rebel', child_id=CHILD_OWN, reference_id=REF_OWN,
                                     child_scenario=child_own, reference_scenario=SCENARIO,
                                     changed_fields=[DPS_HP_FIELD], tag='owner')
        boundary(db, exp_own, CHILD_OWN, reference_id=REF_OWN, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_own, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        owner_b = diagnose(db, CHILD_OWN, scenario=child_own, owner='community')
        check('foreign-owner-support-boundary-does-not-trigger',
              owner_b['supported'] is False
              and owner_b['counts']['boundaryRejections']['foreignOwner'] == 1)

        # 22. A boundary stored under a stale compatibility digest is rejected.
        REF_CI, CHILD_CI = 'ref-compat-b', 'child-compat-b'
        child_ci = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_ci = support_experiment(db, child_id=CHILD_CI, reference_id=REF_CI,
                                    child_scenario=child_ci, reference_scenario=SCENARIO,
                                    changed_fields=[DPS_HP_FIELD], tag='compat')
        boundary(db, exp_ci, CHILD_CI, reference_id=REF_CI, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_ci, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        compat_b = diagnose(db, CHILD_CI, scenario=child_ci, compat='compat-OLD')
        check('incompatible-support-boundary-does-not-trigger',
              compat_b['supported'] is False
              and compat_b['counts']['boundaryRejections']['incompatible'] == 1)

        # 23. A boundary from a non-development window is rejected.
        REF_W, CHILD_W = 'ref-window', 'child-window'
        child_w = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_w = support_experiment(db, child_id=CHILD_W, reference_id=REF_W,
                                   child_scenario=child_w, reference_scenario=SCENARIO,
                                   changed_fields=[DPS_HP_FIELD], tag='window', window='holdout')
        boundary(db, exp_w, CHILD_W, reference_id=REF_W, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_w, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        window_b = diagnose(db, CHILD_W, scenario=child_w)
        check('non-development-support-boundary-does-not-trigger',
              window_b['supported'] is False
              and window_b['counts']['boundaryRejections']['notDevelopment'] == 1)

        # 23b. A frozen holdout pair for the tested child taints its boundary record too.
        REF_BH, CHILD_BH = 'ref-bholdout', 'child-bholdout'
        child_bh = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_bh = support_experiment(db, child_id=CHILD_BH, reference_id=REF_BH,
                                    child_scenario=child_bh, reference_scenario=SCENARIO,
                                    changed_fields=[DPS_HP_FIELD], tag='bholdout')
        boundary(db, exp_bh, CHILD_BH, reference_id=REF_BH, question_field=DPS_HP_FIELD, value=1200,
                 child_scenario=child_bh, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                   'VALUES(?,?,?,?)', (exp_bh, CHILD_BH, 1301, 1302))
        db.commit()
        bhold = diagnose(db, CHILD_BH, scenario=child_bh)
        check('holdout-support-boundary-does-not-trigger',
              bhold['supported'] is False
              and bhold['counts']['boundaryRejections']['holdout'] == 1)

        # 24. A genuine MP crossing is preferred over an available evaluated boundary point.
        REF_BOTH, CHILD_BOTH = 'ref-both', 'child-both'
        child_both = build_scenario([(DPS_INDEX, HP, 1200)])
        exp_both = support_experiment(db, child_id=CHILD_BOTH, reference_id=REF_BOTH,
                                      child_scenario=child_both, reference_scenario=SCENARIO,
                                      changed_fields=[DPS_HP_FIELD], tag='both')
        put(db, exp_both, CHILD_BOTH, [1201, 1202], result(
            [1201, 1202], ticks=100, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=10)]))
        boundary(db, exp_both, CHILD_BOTH, reference_id=REF_BOTH, question_field=DPS_HP_FIELD,
                 value=1200, child_scenario=child_both, reference_scenario=SCENARIO,
                 changed_fields=[DPS_HP_FIELD])
        both = diagnose(db, CHILD_BOTH, scenario=child_both)
        check('mp-crossing-preferred-over-support-boundary',
              both['supported'] is True and both['parentTelemetry']['kind'] == 'mp-crossing'
              and list(both['limitingStats']) == ['mp']
              and both['counts']['boundaryAdmitted'] == 1,
              json.dumps(both['parentTelemetry'].get('evidenceRef'))[:160])

        # 25. A missing operating-region ledger must not discard a genuine MP crossing.
        mined = connect(str(Path(tmp) / 'no-boundary.sqlite'))
        try:
            mined_dev = make_experiment(mined)
            put(mined, mined_dev, 'cand-mp-only', [1401, 1402], result(
                [1401, 1402], ticks=100, mp=[mp_entry(DPS_NAME, DPS_ID, low=True, tick=10)]))
            mined.execute('DROP TABLE ea_boundary')
            mined.commit()
            mp_only = support.diagnose(mined, candidate_id='cand-mp-only', scenario=SCENARIO,
                                       owner='community', compatibility=COMPAT)
            check('mp-crossing-survives-unreadable-boundary-ledger',
                  mp_only['supported'] is True and mp_only['parentTelemetry']['kind'] == 'mp-crossing'
                  and any('operating-region ledger' in note for note in mp_only['unavailable']),
                  json.dumps(mp_only['unavailable'])[:200])
        finally:
            mined.close()

        db.close()

    # The support reader must not drag in the simulator or the optimiser: check in a clean process,
    # because this check itself imports the adapter to obtain the real scenario.
    probe = subprocess.run(
        [sys.executable, '-B', '-c',
         'import sys; sys.path.insert(0, %r); import strategy_support_evidence; '
         "bad=[n for n in sys.modules if n == 'combat_sandbox' "
         "or n.startswith('strategy_optimizer')]; print('|'.join(sorted(bad)))" % str(HERE)],
        capture_output=True, text=True)
    check('no-simulator-or-optimizer-import',
          probe.returncode == 0 and not probe.stdout.strip(),
          (probe.stdout.strip() or probe.stderr.strip())[:200])

    print('\n%d/%d PASS' % (sum(1 for row in RESULTS if row['ok']), len(RESULTS)))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
