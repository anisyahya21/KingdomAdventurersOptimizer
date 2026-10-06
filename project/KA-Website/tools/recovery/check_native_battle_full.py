"""Whole-battle native parity: `ka_run_battle` against the canonical engine loop.

Both sides start from the identical real captured tick-0 state of a frozen scenario, then run the
same number of ticks with the identical control shell:

    tick head (advance_battle_frame, is_annihilated, enter_ending, verdict)
    → fighters → observer → status → projectiles → move → rotate → cells → height
    → modifiers → animation → effects → garbage → finish policy

The Python side uses the canonical `combat_ending` functions, `combat_collapse`-free
`RewardEntitlementWatch`, and the real phase functions; the native side uses `ka_run_battle`. The
comparison covers the `SharedControllers.run` report fields the optimiser consumes, the certificate
observer state, the final snapshot and both RNG streams.

    pypy3.exe check_native_battle_full.py [ticks]
"""
import copy
import ctypes
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_real_state import (CASES, Checkpoints, global_phases,  # noqa: E402
                                     projectile_phase, status_phase, trailing_phases)
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_ending import (advance_battle_frame, after_ending_confirmation, enter_ending,  # noqa: E402
                           is_annihilated)
from combat_reward_entitlement import RewardEntitlementWatch  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_tick import update_fighters  # noqa: E402
from combat_shared_resolution import execute_shared_skill_commands  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402


def encounter_from_engine(engine):
    """The scope only reads each enemy fighter's `skills.dataIds`."""
    fighters = []
    for unit in engine.teams[1]:
        spec = engine.specs[unit['id']]
        fighters.append(dict(monsterId=None, name=spec['name'],
                             skills=dict(dataIds=list(spec['skills']))))
    return dict(encounterId=None, fighters=fighters)


def battle_loop(engine, steps, policy, watch):
    """The canonical `SharedControllers.run` control shell over the same phase set."""
    for _ in range(steps):
        engine.tick += 1
        engine.battle_frame = advance_battle_frame(engine.battle_frame)
        if engine.battle_state == 2 and any(is_annihilated(t) for t in engine.teams):
            engine.battle_frame = 0
            engine.verdict = enter_ending(engine.teams, engine.change)
            engine.battle_state = 3
            engine.emit('verdict', result=engine.verdict)
        update_fighters(engine.teams, engine.battle_state, engine.update,
                        lambda unit: execute_shared_skill_commands(
                            engine.world, unit['id'], lambda command: ROWS[command['skill']],
                            engine.animate, engine.use))
        watch.observe(engine)
        status_phase(engine)
        projectile_phase(engine)
        global_phases(engine)
        trailing_phases(engine)
        if engine.battle_state == 3:
            if policy == 2:
                engine.ending_confirmed = False
                break
            if policy == 1 and engine.battle_frame > 79:
                counter, transition = after_ending_confirmation(engine.battle_frame, engine.verdict)
                engine.ending_counter = counter
                if transition is not None:
                    engine.ending_gate_tick = engine.tick
                    engine.ending_confirmed = True
                    break
    if engine.battle_state == 3 and policy == 0:
        counter, transition = after_ending_confirmation(engine.battle_frame, engine.verdict)
        engine.ending_counter = counter
        engine.ending_confirmed = transition is not None
        if transition is not None:
            engine.ending_gate_tick = engine.tick
    return dict(
        ticks=engine.tick + 1, verdict=engine.verdict, battleState=engine.battle_state,
        battleFrame=engine.battle_frame, endingGateTick=engine.ending_gate_tick,
        endingConfirmed=engine.ending_confirmed, endingCounter=engine.ending_counter,
        prizeCallbacks=len(engine.prizes),
        unresolvedCommands=sum(1 for u in engine.units.values() if u['commands']),
        pendingProjectiles=len(engine.projectiles.members.indices),
        activeDamageOrLeaving=sum(1 for u in engine.units.values() if u['board'][5] in (6, 8)),
        mathDraws=engine.math_draws, libDraws=engine.lib_draws,
        verdictTick=next((event['tick'] for event in engine.trace if event['kind'] == 'verdict'),
                         None))


def compare(label, expected, got, watch, report):
    diffs = []
    fields = (('ticks', expected['ticks'], report['ticks']),
              ('verdict', expected['verdict'] or 0, report['verdict']),
              ('battleState', expected['battleState'], report['battle_state']),
              ('battleFrame', expected['battleFrame'], report['battle_frame']),
              ('endingGateTick', -1 if expected['endingGateTick'] is None
               else expected['endingGateTick'], report['ending_gate_tick']),
              ('endingConfirmed', int(bool(expected['endingConfirmed'])),
               report['ending_confirmed']),
              ('endingCounter', -1 if expected['endingCounter'] is None
               else expected['endingCounter'], report['ending_counter']),
              ('prizeCallbacks', expected['prizeCallbacks'], report['prize_callbacks']),
              ('unresolvedCommands', expected['unresolvedCommands'],
               report['unresolved_commands']),
              ('pendingProjectiles', expected['pendingProjectiles'],
               report['pending_projectiles']),
              ('activeDamageOrLeaving', expected['activeDamageOrLeaving'],
               report['active_damage_or_leaving']),
              ('mathDraws', expected['mathDraws'], report['math_draws']),
              ('libDraws', expected['libDraws'], report['lib_draws']),
              ('verdictTick', -1 if expected['verdictTick'] is None
               else expected['verdictTick'], report['verdict_tick']),
              ('certificate.holds', int(watch.certificate is not None),
               report['certificate_held']),
              ('certificate.frame', -1 if watch.certificate is None
               else watch.certificate['frame'], report['certificate_frame']),
              ('certificate.pending', -1 if watch.certificate is None
               else watch.certificate['pendingChestCount'], report['certificate_pending']),
              ('lateHoldFrame', -1 if watch.late_hold is None else watch.late_hold['frame'],
               report['late_hold_frame']),
              ('pendingFinal', watch.pending_final, report['pending_final']),
              ('pendingAtVerdict', -1 if watch.pending_at_verdict is None
               else watch.pending_at_verdict, report['pending_at_verdict']),
              ('postCertificateDelta', watch.post_certificate_delta,
               report['post_certificate_delta']),
              ('observations', watch.observations, report['observations']),
              ('verdictObservations', watch.verdict_observations,
               report['verdict_observations']),
              ('clauses', [int(v) for v in (watch.latest or {}).get('clauses', {}).values()],
               list(report['clauses'])),
              ('scopeAllowed', int(watch.scope['allowed']), report['scope_allowed']))
    for name, want, have in fields:
        if want != have:
            diffs.append(f'{name}: python={want} native={have}')
    return diffs


def main():
    library = ka_abi.load()
    base = default_scenario()
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    policy = 0
    rows = []
    for case in CASES:
        horizon = min(case['tickLimit'], limit)
        if horizon < 2:
            continue
        checkpoints = Checkpoints([0])
        started = time.perf_counter()
        run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=0)
        setup_seconds = time.perf_counter() - started
        stored = checkpoints.stored.get(0)
        if stored is None:
            continue
        engine = copy.deepcopy(stored['engine'])
        watch = RewardEntitlementWatch(encounter_from_engine(engine), ROWS)
        steps = horizon - 1
        pre = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)
        battle = ka_abi.load_snapshot(library, pre)
        try:
            library.ka_battle_set_scope_allowed(battle, int(watch.scope['allowed']))
            started = time.perf_counter()
            expected = battle_loop(engine, steps, policy, watch)
            python_seconds = time.perf_counter() - started
            report = ka_abi.KaBattleReport()
            started = time.perf_counter()
            status = library.ka_run_battle(battle, steps, policy, ctypes.byref(report))
            native_seconds = time.perf_counter() - started
            if status != 0:
                print(f'{case}: native refused with {status}')
                return 1
            got = ka_abi.report_dict(report)
            diffs = compare(case, expected, got, watch, got)
            final = ka_abi.native_snapshot(library, battle, pre)
            final_diffs = ka_abi.diff_snapshots(
                ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES), final)
            rows.append(dict(case=case, horizon=horizon, diffs=diffs, final=final_diffs[:6],
                             python=python_seconds, native=native_seconds,
                             setup=setup_seconds))
        finally:
            library.ka_battle_free(battle)
    print(f'whole-battle parity ({len(rows)} cases, policy at-horizon):')
    failures = 0
    for row in rows:
        case = row['case']
        verdict = row['diffs']
        tag = 'OK' if not verdict and not row['final'] else 'FAIL'
        if tag == 'FAIL':
            failures += 1
        print(f"  {tag} enc{case['encounterId']} seed{case['mathSeed']}/{case['libSeed']} "
              f"horizon={row['horizon']} python={row['python']:.2f}s native={row['native']:.2f}s "
              f"import={row['setup']:.2f}s")
        for diff in verdict[:6]:
            print(f'      {diff}')
        for diff in row['final']:
            print(f'      snapshot {diff}')
    if failures:
        return 1
    print(f'  all {len(rows)} whole battles matched on report fields, certificate state, '
          f'final snapshot and RNG')
    return 0


if __name__ == '__main__':
    sys.exit(main())
