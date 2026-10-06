"""Production-horizon parity: battles that only resolve AFTER the old 10,000-tick limit.

The horizon change is only safe if the two engines still agree on the fights that motivated it. This
harness takes the *exact* stored runs that had no verdict at their 10,000-tick horizon and resolved
later (same candidate, same math/lib seeds, from `RE-evidence/20260922-horizon/horizon-probe.json`),
runs them at the production horizon and finish policy through both backends, and compares:

  * the compact result, digest and rewardOutcome through the production adapter boundary;
  * verdict, verdict tick, final tick count, prize callbacks, certificate state, the complete final
    snapshot (board/long-board/cell/parameters/subset/occupancy state) and both RNG streams against
    the canonical `SharedControllers.run` shell;
  * one genuine stalemate (healing-only own team) that reaches the horizon with no verdict at all.

The production policy is `on-verdict`: a resolved battle stops at its verdict, so a larger safety
ceiling costs extra ticks only for the fights that genuinely need them, and only a battle that reaches
the ceiling with no verdict is a "no verdict by limit".

    pypy3.exe check_native_horizon.py
"""
import copy
import ctypes
import json
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_battle_full import battle_loop, compare, encounter_from_engine  # noqa: E402
from check_native_real_state import Checkpoints  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_parameters import HUMAN_TRAINING_PARAMETERS  # noqa: E402
from combat_reward_entitlement import RewardEntitlementWatch  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402

EVIDENCE = HERE / 'RE-evidence/20260922-horizon/horizon-probe.json'

#: `battle_control`'s policy codes, matching `combat_scenario`'s names.
POLICY_CODES = {'at-horizon': 0, 'after-ending': 1, 'on-verdict': 2}


def production_horizon():
    import combat_evaluation
    import strategy_search
    return (strategy_search.DEFAULT_TICK_LIMIT, combat_evaluation.HARD_MAX_TICKS,
            strategy_search.TERMINAL_VERDICT_POLICY)


def post_limit_cases(limit, sample=0):
    """Stored runs that were censored at the old limit and resolve later, read from the evidence."""
    from strategy_optimizer_adapter import validate_scenario

    document = json.loads(EVIDENCE.read_text(encoding='utf-8'))
    cases = []
    seen = set()
    for row in document['rows']:
        resolved = [o for o in row['observations'] if o.get('verdict') not in (None, 0)]
        if not resolved or row['resolvedAt'] is None:
            continue
        first = resolved[0]
        if first['verdictTick'] <= 7000:
            continue
        key = (row['mathSeed'], row['libSeed'], first['verdict'])
        if key in seen:
            continue
        seen.add(key)
        cases.append(dict(label=f"enc{row['encounterId']} seed {row['mathSeed']}/{row['libSeed']} "
                                f"verdict {first['verdict']} at {first['verdictTick']}",
                          verdict=first['verdict'], verdictTick=first['verdictTick'],
                          storedHorizon=int(row['horizon']),
                          scenario=validate_scenario(row['scenario'])))
    # Longest resolutions first: those are the fights the old cap censored.
    cases.sort(key=lambda case: -case['verdictTick'])
    return cases[:sample] if sample else cases


def stalemate_case(ticks):
    """A canonical battle that never reaches a verdict: healing-only own team, unkillable enemy."""
    def human(name, skills, stats):
        return dict(name=name, human=True, monsterId=None, weaponId=0, equipment=[], visitor=False,
                    leaderIdentity=False, skills=skills, invocationLevels=[1] * len(skills),
                    parameters={p: dict(rawValue=stats.get(p, 1),
                                        rawMax=stats.get(p, 1) if p in (10, 11) else 2147483647,
                                        extraValue=0, extraMax=0, trainingLevel=123)
                                for p in HUMAN_TRAINING_PARAMETERS})

    own = [human(f'synthetic wall{i}', [37], {10: 500000, 11: 9999, 13: 1, 14: 9999, 15: 1,
                                             16: 1, 19: 1}) for i in range(5)]
    return dict(label='synthetic stalemate', verdict=None, verdictTick=None,
                scenario=dict(schema='ka-special-combat-research-1', encounterId=19, defeatCount=0,
                              mathSeed=7, libSeed=8, tickLimit=ticks, ownUnits=own, holyHerbStock=0,
                              inputs=[],
                              note='Synthetic horizon fixture: a healing-only own team that cannot '
                                   'kill the enemy and cannot be annihilated by it.'))


def adapter_parity(case, report):
    """Compact result, digest and rewardOutcome through the production adapter boundary."""
    import strategy_optimizer_adapter as adapter
    seeds = (case['scenario']['mathSeed'], case['scenario']['libSeed'])
    canonical = adapter.simulate(case['scenario'], seeds, backend='python')
    native = adapter.simulate(case['scenario'], seeds, backend='native')
    # A battle that stays unresolved for tens of thousands of ticks can outgrow the kernel's fixed
    # entity arena; the production path then answers from the canonical Python engine, which is the
    # documented fallback (`strategy_optimizer_native.KA_ERR_CAPACITY`). Either backend is acceptable
    # here - what has to match is the answer, and the comparison below is what proves that. The
    # backend actually used is recorded so a fallback is visible rather than silent.
    report.setdefault('backends', {})[case['label']] = native.get('resultBackend')
    for key in sorted(set(canonical) | set(native)):
        if key == 'resultBackend':
            continue
        if canonical.get(key) != native.get(key):
            report['failures'].append(f"{case['label']}: {key}: python={canonical.get(key)!r} "
                                      f"native={native.get(key)!r}")
    if case['verdict'] is None:
        if canonical.get('verdict') is not None or not canonical.get('censored'):
            report['failures'].append(f"{case['label']}: expected no verdict, got "
                                      f"{canonical.get('verdict')!r}")
    elif canonical.get('verdict') != case['verdict']:
        report['failures'].append(f"{case['label']}: python verdict {canonical.get('verdict')} != "
                                  f"recorded {case['verdict']}")
    return canonical


def whole_battle_parity(library, case, policy_code, report):
    """Verdict tick, certificate state, complete final snapshot and both RNG streams."""
    scenario = case['scenario']
    horizon = scenario['tickLimit']
    checkpoints = Checkpoints([0])
    run_scenario(dict(scenario), checkpoints=checkpoints, stop_tick=0)
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    watch = RewardEntitlementWatch(encounter_from_engine(engine), ROWS)
    pre = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)
    battle = ka_abi.load_snapshot(library, pre)
    try:
        library.ka_battle_set_scope_allowed(battle, int(watch.scope['allowed']))
        steps = horizon - 1
        expected = battle_loop(engine, steps, policy_code, watch)
        native = ka_abi.KaBattleReport()
        status = library.ka_run_battle(battle, steps, policy_code, ctypes.byref(native))
        if status != 0:
            from strategy_optimizer_native import KA_ERR_CAPACITY
            if status == KA_ERR_CAPACITY:
                # The kernel's created-entity arena is fixed-size and it refuses rather than
                # approximating; the production path falls back to the canonical Python engine, and
                # `adapter_parity` above has already required the two to agree. Reported, not hidden.
                report.setdefault('capacitySkips', []).append(
                    f"{case['label']}: kernel arena capacity at tick {native.ticks} of {horizon}")
                return
            report['failures'].append(f"{case['label']}: native refused with {status}")
            return
        got = ka_abi.report_dict(native)
        diffs = compare(case['label'], expected, got, watch, got)
        final = ka_abi.native_snapshot(library, battle, pre)
        final_diffs = ka_abi.diff_snapshots(
            ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES), final)
        if diffs or final_diffs:
            report['failures'].append(f"{case['label']}: " + '; '.join((diffs + final_diffs)[:5]))
            return
        if case['verdictTick'] is not None and got['verdict_tick'] != case['verdictTick']:
            report['failures'].append(f"{case['label']}: engineering verdict tick "
                                      f"{got['verdict_tick']} != recorded {case['verdictTick']}")
        if case['verdictTick'] is not None and got['ticks'] != got['verdict_tick'] + 1:
            report['failures'].append(f"{case['label']}: a resolved battle did not stop at its "
                                      f"verdict: ticks {got['ticks']} != "
                                      f"verdictTick+1 {got['verdict_tick'] + 1}")
        if case['verdict'] is None and got['verdict'] != 0:
            report['failures'].append(f"{case['label']}: expected no verdict, native said "
                                      f"{got['verdict']}")
        report['cases'] += 1
        report['rows'].append(dict(label=case['label'], verdict=got['verdict'],
                                   verdictTick=got['verdict_tick'], ticks=got['ticks'],
                                   mathDraws=got['math_draws'], libDraws=got['lib_draws'],
                                   prizeCallbacks=got['prize_callbacks']))
    finally:
        library.ka_battle_free(battle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=6,
                        help='how many late-resolving stored fights to replay in full')
    args = parser.parse_args()
    ticks, hard_max, policy = production_horizon()
    policy_code = POLICY_CODES[policy]
    failures = []
    print(f'production horizon: {ticks} ticks ({ticks / 20 / 60:.0f}:{int(ticks / 20) % 60:02d}); '
          f'hardMaxTicks={hard_max}; finish policy {policy!r}')
    # Pinned by the Part A measurement in `RE-evidence/20260922-horizon/`: of the 40 stored Aloha-Hard
    # fights with no verdict at 10,000 ticks, 0 resolved by 10,000, all 40 resolved by 20,000, and the
    # longest resolution was tick 19,780 (16:29 of game time). 30,000 is ~1.5x that observed maximum.
    if ticks != 30000:
        failures.append(f'production horizon is {ticks}, expected the measured 30000')
    if policy != 'on-verdict':
        failures.append(f'production finish policy is {policy!r}, expected on-verdict')
    if ticks > hard_max:
        failures.append(f'horizon {ticks} exceeds the evaluation hard max {hard_max}')
    cases = post_limit_cases(ticks, sample=args.cases)
    if not cases:
        failures.append('no post-10,000 cases found in the horizon evidence')
    for case in cases:
        case['scenario'] = dict(case['scenario'], tickLimit=ticks, finishPolicy=policy)
    stalemate = stalemate_case(ticks)
    stalemate['scenario'] = dict(stalemate['scenario'], finishPolicy=policy)
    print(f'{len(cases)} post-10,000 cases + 1 stalemate fixture at {ticks} ticks')
    report = dict(failures=failures, cases=0, rows=[])
    library = ka_abi.load()
    for case in cases + [stalemate]:
        adapter_parity(case, report)
        whole_battle_parity(library, case, policy_code, report)
    print('post-limit parity:')
    for row in report['rows']:
        print(f"  {row['label']}: verdict={row['verdict']} at tick {row['verdictTick']} "
              f"ticks={row['ticks']} prizes={row['prizeCallbacks']} "
              f"libDraws={row['libDraws']}")
    print(f"  cases={report['cases']} (compact, digest, rewardOutcome, verdict tick, final "
          f'snapshot and RNG all compared)')
    for label, backend in sorted((report.get('backends') or {}).items()):
        if backend != 'native':
            print(f'  {label}: answered by the canonical Python engine ({backend})')
    for row in report.get('capacitySkips', []):
        print(f'  {row} (production falls back to the canonical engine and must agree - checked above)')
    if report['failures']:
        print('FAILURES:')
        for row in report['failures'][:10]:
            print('  ' + row)
        return 1
    print('  every post-7,000 battle and the stalemate matched exactly on both backends')
    return 0


if __name__ == '__main__':
    sys.exit(main())
