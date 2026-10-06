"""Native compact-result parity: the adapter's `_compact` object and its digest.

For each frozen whole-battle case: Python produces the canonical report through `run_scenario` and
runs the real `strategy_optimizer_adapter._compact`; the native side clones a template, seeds it,
runs `ka_run_battle`, and fills the same compact fields from the native report. Every field is
compared individually, then the digests (computed by the existing adapter helper, unchanged).

    pypy3.exe check_native_compact.py
"""
import copy
import ctypes
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import combat_progress  # noqa: E402
from check_native_battle_full import encounter_from_engine  # noqa: E402
from check_native_real_state import CASES, Checkpoints  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_reward_entitlement import RewardEntitlementWatch  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from strategy_optimizer_adapter import _compact, default_scenario, validate_scenario  # noqa: E402


def native_compact(library, snapshot, seeds, ticks, watch_names=(), policy=0):
    """The same logical object `_compact` builds, filled from the native report."""
    template = ka_abi.load_snapshot(library, snapshot)
    try:
        clone = library.ka_battle_clone(template)
        library.ka_battle_seed_rng(clone, seeds[0], seeds[1])
        report = ka_abi.KaBattleReport()
        status = library.ka_run_battle(clone, ticks - 1, policy, ctypes.byref(report))
        library.ka_battle_free(clone)
    finally:
        library.ka_battle_free(template)
    if status != 0:
        return None, status
    resolved = report.verdict != 0
    maximum = report.own_hp_max
    payload = dict(
        verdict=report.verdict if resolved else None,
        censored=not resolved,
        ticks=report.ticks,
        prizeCallbacks=(report.pre_verdict_prize_callbacks if resolved else None),
        retained=None,
        survivors=report.survivors if report.own_present else None,
        # `_compact`'s own definition: successful Holy Herb uses + successful battle-item uses. The
        # frozen cases declare no consumables, so this was always zero until the herbed pass below
        # configured the live policy; reading the kernel's own counter keeps the two definitions one.
        resourceUses=report.resource_uses,
        healthFraction=(round(report.own_hp / maximum, 6) if maximum else None),
        behavior=dict(heals=report.heals, attacks=report.attack_attempts,
                      prizes=report.prize_callbacks),
        seeds=list(seeds),
        # The additive stored-attack block, built independently from the same report the way the
        # adapter's own native path builds it. It is outside the digest (like `rewardOutcome`), but it
        # is compared field by field here as well, which is a second check of the ABI mapping.
        progressMetrics=dict(
            bossIdentity=(report.progress_boss if report.progress_boss >= 0 else -1),
            bossDeathTick=report.progress_boss_death_tick,
            bossLeavingTick=report.progress_boss_leaving_tick,
            storedCommandsAtDeath=report.progress_stored_at_death,
            storedCommandsTargetingBossAtDeath=report.progress_stored_targeting_boss_at_death,
            storedTargetHoldersAtDeath=report.progress_stored_target_holders_at_death,
            commandsTargetingBoss=report.progress_commands_targeting_boss,
            commandsTargetingBossReleased=report.progress_commands_targeting_boss_released,
            maxSimultaneousStoredCommands=report.progress_max_stored,
            maxSimultaneousCommandsTargetingBoss=report.progress_max_targeting_boss,
            storedTargetHoldersPeak=report.progress_target_holders_peak,
            postDeathBossReentries=report.progress_post_death_reentries,
            postDeathBossLeavings=report.progress_post_death_leavings,
            postDeathPrizes=report.progress_post_death_prizes,
            commandsReleasedAfterDeath=report.progress_released_after_death,
            commandsReleasedAfterDeathTargetingBoss=report.progress_released_after_death_targeting_boss,
            firstPostDeathCommandReleaseTick=report.progress_first_release_after_death,
            lastPostDeathCommandReleaseTick=report.progress_last_release_after_death),
    )
    # The MP telemetry and the explicit Holy Herb evidence are built through the *same* builders the
    # reference sandbox uses, so the comparison below is one object checked twice rather than two
    # similar objects that happen to agree today.
    watch = max(0, min(report.mp_watch_count, ka_abi.KA_MAX_MP_WATCH))
    payload['mpMetrics'] = combat_progress.mp_metrics(
        list(watch_names), list(report.mp_identity)[:watch], list(report.mp_min)[:watch],
        list(report.mp_min_percent)[:watch], [bool(v) for v in list(report.mp_low)[:watch]],
        list(report.mp_first_low_tick)[:watch], list(report.mp_first_low_phase)[:watch],
        [bool(v) for v in list(report.mp_zero)[:watch]])
    logged = max(0, min(report.herb_log_count, ka_abi.KA_MAX_HERB_USES))
    payload['herbMetrics'] = combat_progress.herb_metrics(
        report.herb_stock_start, report.herb_stock_remaining, report.herb_max_uses,
        report.herb_use_count,
        [(list(report.herb_use_tick)[slot], list(report.herb_use_phase)[slot],
          list(report.herb_use_source)[slot], bool(list(report.herb_use_ok)[slot]))
         for slot in range(logged)])
    return payload, 0


def compare_case(library, case, scenario, ticks):
    """`(label, [failures])` for one scenario on fixed seeds, both backends."""
    seeds = (case['mathSeed'], case['libSeed'])
    watch_names = [str(name) for name in scenario.get('holyHerbTriggerUnits') or ()]
    failures = []
    expected = _compact(run_scenario(scenario, include_trace=False), seeds)
    expected.pop('rewardOutcome', None)
    checkpoints = Checkpoints([0])
    run_scenario(scenario, checkpoints=checkpoints, stop_tick=0)
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    # The declared trigger units travel with the template exactly as the production path sends them:
    # *names* resolved here to the roster identities both engines address a fighter by.
    consumables, _item_slot, _reason = ka_abi.consumables_from_scenario(scenario)
    consumables['mp_watch'] = [engine.names[name] for name in watch_names]
    snapshot = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES,
                                      consumables)
    # The *declared* finish policy travels to the kernel exactly as the production template builder
    # sends it. Hardcoding `at-horizon` here (which this checker used to do) made the native run the
    # whole horizon while the canonical Python run stopped at the declared verdict, so the
    # comparison measured a policy difference rather than a parity difference.
    policy = 2 if scenario.get('finishPolicy') == 'on-verdict' else 0
    got, status = native_compact(library, snapshot, seeds, ticks, watch_names, policy)
    if got is None:
        return f'enc{case["encounterId"]}', [f'native refused with {status}']
    # digest uses the existing adapter helper, unchanged
    expected_digest = expected['digest']
    expected_fields = {key: value for key, value in expected.items() if key != 'digest'}
    import strategy_optimizer_adapter as adapter
    # The digest covers exactly the fields `_compact` covered: the additive stored-attack, MP and herb
    # blocks are attached after it, so they must not enter the hash here either.
    got['digest'] = adapter._digest({key: value for key, value in got.items()
                                     if key not in ('progressMetrics', 'mpMetrics', 'herbMetrics')})
    got_fields = {key: value for key, value in got.items() if key != 'digest'}
    for key in sorted(set(expected_fields) | set(got_fields)):
        if expected_fields.get(key) != got_fields.get(key):
            failures.append(f'{key}: python={expected_fields.get(key)!r} '
                            f'native={got_fields.get(key)!r}')
    if expected_digest != got['digest']:
        failures.append(f'digest: python={expected_digest[:16]} native={got["digest"][:16]}')
    return f'enc{case["encounterId"]}', failures


def main():
    library = ka_abi.load()
    base = default_scenario()
    failures = []
    checked = 0
    for case in CASES:
        label, case_failures = compare_case(library, case, dict(base, **case), case['tickLimit'])
        failures += [(case, detail) for detail in case_failures]
        checked += 1
    print(f'native compact-result parity: {checked} cases compared')
    print(f'  (no declared trigger units: an empty MP block and a zeroed herb block, '
          f'{len(ka_abi.REPORT_FIELDS)} report fields per case)')
    # ---- the same fixed seeds with the live policy configured -------------------------------------
    # TEST CONFIGURATION, not a production value: the game never states a herb quantity, so these are
    # the fixture numbers the policy tests use (`holyHerbStock = 2`, `holyHerbMaxUses = 2`) with the
    # roster's own first two human residents as the declared DPS and healer.
    herbed = 0
    fired = 0
    for case in CASES:
        scenario = dict(base, **case)
        humans = [unit['name'] for unit in scenario['ownUnits'] if unit['human']]
        if len(humans) < 2:
            continue
        scenario = validate_scenario(dict(scenario, holyHerbStock=2, holyHerbMaxUses=2,
                                          holyHerbTriggerUnits=humans[:2]))
        label, case_failures = compare_case(library, case, scenario, case['tickLimit'])
        failures += [(case, 'herbed ' + detail) for detail in case_failures]
        herbed += 1
        probe = _compact(run_scenario(scenario, include_trace=False),
                         (case['mathSeed'], case['libSeed']))
        fired += 1 if probe['herbMetrics']['useCount'] else 0
    print(f'native compact-result parity: {herbed} herbed cases compared '
          f'({fired} of them dispatched at least one automatic herb)')
    if failures:
        print('FAILURES:')
        for case, detail in failures[:10]:
            print(f"  enc{case['encounterId']} seeds {case['mathSeed']}/{case['libSeed']}: {detail}")
        return 1
    print('  every compact field and the compact digest matched exactly')
    return 0


if __name__ == '__main__':
    sys.exit(main())
