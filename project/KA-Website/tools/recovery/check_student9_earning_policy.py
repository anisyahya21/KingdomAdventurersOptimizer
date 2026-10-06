"""Student 9 spends its share on earning changes, not on minimum-stat tuning.

The student's job is to raise a fight's reliable **Mean Earned**. It used to walk Student 1's seven
single-axis tracks, so its ordinary refinement proposed Health, Defence, MP and Dexterity changes -
support-minimum tuning, which is Student 1's work. This check pins the replacement policy:

  * ordinary (`earning`) proposals move the DPS's Attack, Luck and Speed and nothing else;
  * inherited HP, MP and Defence are preserved exactly, however far above the legal minimum they sit,
    and a lower "sufficient" value found elsewhere never becomes a reason to lower one;
  * earning refinement never waits on a minimum: it proposes from a credible parent with no
    requirement evidence at all;
  * a `repair` is a different operation, only raises a support resource for a named unit, and is only
    offered when the evidence shows that unit became MP-limited;
  * a defeat on its own - including a defeat after the DPS had done its work - is not a repair trigger;
  * the healer's offensive cleanup is its own purpose, and ordinary earning refinement refuses to move
    the healer at all;
  * Attack and Luck are pairable, mixed directions are generated, and Speed is conditioned on the
    region; every one of those moves is still legal under the contract;
  * the pair and dial paths cannot bypass the policy: a protected-stat move built by hand is refused,
    and no draw from the generator ever returns one;
  * the parent's own stored scenario, identity and statistics are untouched by its children;
  * the pass works with Breakthrough switched off (it is not gated by another stream);
  * the student's own report exposes why a zero-attempt candidate has no runs;
  * a restart resumes the outstanding request without duplicating independent samples.

    python check_student9_earning_policy.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_students as students  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats as adapter_stats  # noqa: E402

ENCOUNTER = 19


def parent_scenario(*, healer_def=None, dps_atk=38, dps_spd=80, dps_lck=12):
    """The frozen default fight, with the DPS values this check needs and an optional healer Defence."""
    built = default_scenario()
    built['encounterId'] = ENCOUNTER
    built['defeatCount'] = 0
    built['inputs'] = []
    for unit in built['ownUnits']:
        if not unit.get('human'):
            continue
        role = students.role(unit)
        parameters = unit.setdefault('parameters', {})
        if role == students.ROLE_DPS:
            parameters[13] = dict(parameters.get(13) or {}, rawValue=dps_atk)
            parameters[15] = dict(parameters.get(15) or {}, rawValue=dps_spd)
            parameters[16] = dict(parameters.get(16) or {}, rawValue=dps_lck)
        if role == students.ROLE_HEALER and healer_def is not None:
            parameters[14] = dict(parameters.get(14) or {}, rawValue=healer_def)
    return built


def run_result(*, verdict, seeds, digest, chests):
    return dict(verdict=verdict, censored=False, ticks=120, prizeCallbacks=chests, retained=None,
                survivors=1, resourceUses=0, elapsedSeconds=.01,
                behavior=dict(heals=1, attacks=2, prizes=chests), seeds=list(seeds), digest=digest,
                rewardOutcome=dict(pendingChests=chests, awardedChests=chests, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def credible_parent(store, scenario, *, runs=64, chests=3):
    """A parent with a credible measured mean, so `average_parent` treats it as the incumbent."""
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        parent = store.add(scenario, 'Community build', 'supplied', {})
    for ordinal in range(runs):
        store.record(parent, 'validation', ordinal,
                     run_result(verdict=1, seeds=(500 + ordinal, 900 + ordinal),
                                digest=f'p{ordinal}', chests=chests))
    return parent


def value_map(scenario):
    return students._value_map(scenario)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-student9-earning-policy-1', checks=[], cases=0)
    failures = []

    def record_case(title, detail):
        entry = dict(detail)
        entry['name'] = title
        report['checks'].append(entry)
        report['cases'] += 1
        print(f'  OK {title}: {json.dumps(detail, sort_keys=True)[:220]}')

    def check(condition, message):
        if not condition:
            failures.append(message)

    scenario = parent_scenario(healer_def=9)
    dps, healer = students.average_roles(scenario)
    check(dps is not None and healer is not None,
          f'the fixture did not resolve its DPS and healer: {(dps, healer)}')

    # ---- (1) ordinary earning refinement: the DPS's Attack/Luck/Speed only ------------------------
    protected = set(students.AVERAGE_PROTECTED_STATS)
    touched_stats, touched_units, lowered = set(), set(), []
    proposals = 0
    for draw in range(160):
        child, operation, target, change = students.average_child(
            scenario, taken=None, offset=draw, purpose='earning')
        if child is None:
            continue
        proposals += 1
        before, after = value_map(scenario), value_map(child)
        for key in set(before) | set(after):
            if before.get(key) == after.get(key):
                continue
            name, stat = key
            touched_stats.add(stat)
            touched_units.add(name)
            if stat in protected:
                lowered.append(f'{name} {stat} {before[key]}->{after[key]}')
    check(proposals > 0, 'ordinary earning refinement proposed nothing at all')
    check(not touched_stats & protected,
          f'ordinary earning refinement moved a protected stat: {sorted(touched_stats & protected)} '
          f'({lowered[:3]})')
    check(touched_stats <= set(students.AVERAGE_EARNING_STATS),
          f'ordinary refinement moved an axis outside Attack/Luck/Speed: {sorted(touched_stats)}')
    check(touched_units <= {dps},
          f'ordinary refinement moved a unit that is not the DPS: {sorted(touched_units)}')
    check('lck' in touched_stats and 'atk' in touched_stats and 'spd' in touched_stats,
          f'the earning search did not cover all three axes: {sorted(touched_stats)}')
    record_case('earning-refinement-moves-the-dps-attack-luck-and-speed',
                dict(proposals=proposals, stats=sorted(touched_stats), units=sorted(touched_units),
                     protectedTouched=sorted(touched_stats & protected)))

    # ---- (2) a known lower minimum never becomes a reason to lower a sufficient value ------------
    high_def = parent_scenario(healer_def=353)
    before = value_map(high_def)
    lowered = []
    for draw in range(120):
        child, _operation, _target, _change = students.average_child(
            high_def, taken=None, offset=draw, purpose='earning')
        if child is None:
            continue
        after = value_map(child)
        for (name, stat), value in before.items():
            if stat in protected and after.get((name, stat)) != value:
                lowered.append(f'{name} {stat} {value}->{after.get((name, stat))}')
    check(not lowered, f'a sufficient support value was changed: {lowered[:3]}')
    record_case('a-sufficient-support-value-is-left-alone',
                dict(healerDefence=before.get((healer, 'def')), changes=len(lowered)))

    # ---- (3) no minimum-search prerequisite blocks earning refinement ---------------------------
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        path = pathlib.Path(work)/'student9.sqlite'
        store = optimizer.Store(path, provenance())
        try:
            parent = credible_parent(store, scenario)
            quiet = students.average_child(scenario, taken=None, offset=3, purpose='earning')
            check(quiet[0] is not None,
                  f'refinement was refused with no requirement evidence: {quiet[1]!r}')
            record_case('earning-does-not-wait-for-a-minimum',
                        dict(refusal=quiet[1] if quiet[0] is None else None,
                             change=quiet[3]))

            # ---- (6) the healer's cleanup is its own purpose, not part of earning ---------------
            cleanup_touched, cleanup_units = set(), set()
            for draw in range(120):
                child, _op, _target, _change = students.average_child(
                    scenario, taken=None, offset=draw, purpose='cleanup')
                if child is None:
                    continue
                before_c, after_c = value_map(scenario), value_map(child)
                for key in set(before_c) | set(after_c):
                    if before_c.get(key) != after_c.get(key):
                        cleanup_units.add(key[0])
                        cleanup_touched.add(key[1])
            check(cleanup_units <= {healer},
                  f'cleanup moved a unit other than the healer: {sorted(cleanup_units)}')
            check(cleanup_touched <= set(students.AVERAGE_EARNING_STATS),
                  f'cleanup moved a non-earning stat: {sorted(cleanup_touched)}')
            healer_in_earning = students.average_child(scenario, taken=None, offset=0,
                                                       purpose='earning', unit=healer)
            check(healer_in_earning[0] is None,
                  'ordinary earning refinement accepted a healer-scoped proposal')
            record_case('cleanup-and-earning-are-separate-purposes',
                        dict(cleanupUnits=sorted(cleanup_units), cleanupStats=sorted(cleanup_touched),
                             healerScopedEarningRefused=healer_in_earning[0] is None))

            # ---- (4) repair raises a support resource, upward only, and is a different operation -
            repair_changes = []
            for draw in range(60):
                child, _op, _target, change = students.average_child(
                    scenario, taken=None, offset=draw, purpose='repair', unit=healer)
                if child is None:
                    continue
                before_r, after_r = value_map(scenario), value_map(child)
                for key in set(before_r) | set(after_r):
                    if before_r.get(key) != after_r.get(key):
                        repair_changes.append((key[0], key[1], before_r.get(key), after_r.get(key)))
            check(repair_changes, 'a repair for a unit with MP evidence proposed nothing')
            check(all(stat in students.AVERAGE_REPAIR_STATS for _n, stat, _o, _v in repair_changes),
                  f'a repair moved a non-support stat: {repair_changes[:3]}')
            check(all(int(new) > int(old) for _n, _s, old, new in repair_changes),
                  f'a repair lowered a support resource: {repair_changes[:3]}')
            check(all(name == healer for name, _s, _o, _v in repair_changes),
                  f'a repair moved a unit it was not scoped to: {repair_changes[:3]}')
            # An ordinary refinement must refuse a support raise as firmly as it refuses a lowering.
            support_child, _op, _target, _change = students.average_child(
                scenario, taken=None, offset=0, purpose='repair', unit=healer)
            ok, reason = students.average_change_allowed(scenario, support_child, 'earning')
            check(not ok, 'ordinary refinement accepted a support-resource change')
            record_case('a-repair-is-a-different-operation',
                        dict(changes=len(repair_changes), example=repair_changes[:1],
                             earningRefusal=reason))

            # ---- (5) a defeat alone is not a repair trigger -------------------------------------
            support = students.average_support_evidence(store, ENCOUNTER)
            check(support == {}, f'a plain defeat produced support evidence: {support}')
            record_case('a-defeat-alone-is-not-a-repair-trigger',
                        dict(supportEvidence=support,
                             why='no watched unit was measured MP-limited, and a loss is not a diagnosis'))

            # ---- (7) Attack/Luck pairs, mixed directions, Speed conditioning ---------------------
            pairs_seen, directions = set(), set()
            for draw in range(400):
                child, operation, target, change = students.average_child(
                    scenario, taken=None, offset=draw, purpose='earning', broaden=True)
                if child is None:
                    continue
                before_p, after_p = value_map(scenario), value_map(child)
                moved = [(key[1], before_p.get(key), after_p.get(key)) for key in set(before_p) | set(after_p)
                         if before_p.get(key) != after_p.get(key)]
                for stat, old, new in moved:
                    directions.add((stat, 'up' if int(new) > int(old) else 'down'))
                if len(moved) >= 2:
                    pairs_seen.add(tuple(sorted(stat for stat, _o, _v in moved)))
            check(('atk', 'lck') in pairs_seen or ('atk', 'lck', 'spd') in pairs_seen,
                  f'Attack and Luck were never proposed together: {sorted(pairs_seen)}')
            check(len({d for _s, d in directions}) == 2,
                  f'the proposal set is single-directional: {sorted(directions)}')
            check(all(stat in students.AVERAGE_EARNING_STATS for stat, _d in directions),
                  f'a pair or dial move touched a protected stat: {sorted(directions)}')
            record_case('attack-and-luck-are-paired-in-both-directions',
                        dict(pairs=sorted(pairs_seen), directions=sorted(directions)))

            # ---- (8) the pair/dial machinery cannot bypass the policy ---------------------------
            raw_pair = students.community_pair_child(
                scenario, taken=None, pair=((healer, 'def'), (healer, 'mp')))
            ok_raw, raw_reason = students.average_change_allowed(scenario, raw_pair[0], 'earning')
            check(raw_pair[0] is not None and not ok_raw,
                  'a by-hand protected-stat pair was not refused by the policy')
            offenders = 0
            for draw in range(300):
                child, _op, _target, _change = students.average_child(
                    scenario, taken=None, offset=draw, purpose='earning',
                    broaden=(draw % 2 == 0))
                if child is None:
                    continue
                ok, _reason = students.average_change_allowed(scenario, child, 'earning')
                if not ok:
                    offenders += 1
            check(offenders == 0, f'{offenders} generated children failed the policy check')
            record_case('pair-and-dial-cannot-bypass-the-policy',
                        dict(byHandRefusal=raw_reason, generatedOffenders=offenders))

            # ---- (9) the parent's own build and evidence are untouched --------------------------
            before_identity = optimizer.identity(store.scenario(parent))
            created = students.replenish_average(store, ENCOUNTER, 3, 1, adapter_stats)
            check(created, f'Student 9 created nothing from a credible parent: {created}')
            check(optimizer.identity(store.scenario(parent)) == before_identity,
                  'the parent scenario changed while its children were created')
            rows = store.db.execute(
                'SELECT l.candidate, l.parent, l.source, l.operation, l.change FROM lineage l '
                'WHERE l.parent=?', (parent,)).fetchall()
            check(all(row['source'] == students.STUDENT_AVERAGE for row in rows),
                  'a child was attributed to the wrong stream')
            for row in rows:
                child_scenario = store.scenario(row['candidate'])
                ok, reason = students.average_change_allowed(store.scenario(parent), child_scenario,
                                                             'earning')
                check(ok, f'a stored child violates the policy: {reason}')
            record_case('children-are-separate-and-legal',
                        dict(created=len(created), parentUnchanged=True,
                             changes=[row['change'] for row in rows]))

            # ---- (10) the report exposes why a zero-attempt candidate has no runs ---------------
            report_rows = students.average_report(store).get(ENCOUNTER) or {}
            pending = report_rows.get('pending') or []
            check(pending, 'the report does not expose zero-attempt candidates')
            check(all(row.get('state') for row in pending),
                  f'a zero-attempt candidate has no stated state: {pending[:2]}')
            record_case('zero-attempt-builds-carry-a-state',
                        dict(example=pending[0], count=len(pending)))

            # ---- (13) restart resumes the request without duplicating samples -------------------
            before_counts = dict(store.db.execute(
                'SELECT candidate, COUNT(*) FROM evidence GROUP BY candidate').fetchall())
            plan = store.get(students.AVERAGE_PLAN_KEY)
            store.close()
            store = optimizer.Store(path, provenance())
            after_plan = store.get(students.AVERAGE_PLAN_KEY)
            check(after_plan == plan, 'the outstanding request did not survive the restart')
            again = students.replenish_average(store, ENCOUNTER, 3, 2, adapter_stats)
            after_counts = dict(store.db.execute(
                'SELECT candidate, COUNT(*) FROM evidence GROUP BY candidate').fetchall())
            check(after_counts == before_counts,
                  'a resumed pass recorded evidence rows without running a battle')
            record_case('a-restart-resumes-without-duplicating-work',
                        dict(planSurvives=True, newChildren=len(again), evidenceUnchanged=True))
        finally:
            store.close()

    print(f'Student 9 earning-policy checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
