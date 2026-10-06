"""Part A evidence: what a *stored attack* is, and how it becomes a chest.

Replays a persisted record-holder scenario from a real library through the canonical Python engine with
a read-only per-tick sampler (the engine's own `abort` seam, which never mutates state), and prints the
causal chain tick by tick:

    boss HP reaches 0 -> boss enters Leaving (state 8) -> prize queued
    -> a stored attack still pointed at the boss -> boss re-enters Damaging (state 6)
    -> Leaving again -> another prize

The two structures the native code calls a stored attack are both sampled every tick:

  * the per-fighter persistent command queue `unit['commands']` (opcode 29, each with its own target,
    clock and use index) - released by `execute_shared_skill_commands` every fighters phase;
  * the stored normal-attack target `long_board[16]` (native BKL:16) that `UpdateCharging` writes and
    only `ExitUsingSkill` clears, read by `UpdateAttacking`.

    python trace_stored_attacks.py [--library PATH] [--holder earned:19:0] [--json OUT]

The library is opened read-only; nothing here writes to it.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_sandbox  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402


def load_holder(library, key):
    """The frozen scenario + seeds of one persisted record holder, straight from the library."""
    store = optimizer.Store(library, provenance())
    try:
        holders = store.get('recordHolders') or {}
        if key not in holders:
            raise SystemExit(f'no record holder {key!r}; library has {sorted(holders)}')
        holder = holders[key]
    finally:
        store.close()
    return holder['scenario'], (holder['mathSeed'], holder['libSeed']), holder


def trace(library, key):
    scenario, (math_seed, lib_seed), holder = load_holder(library, key)
    samples = []

    def sample(world):
        # Read-only: the seam only observes. `value`/`unit` are pure lookups.
        units = {}
        for identity, unit in world.units.items():
            units[str(identity)] = dict(
                hp=world.value(identity, 10), state=unit['board'][5], team=unit['board'][6],
                boss=bool(world.specs[identity].get('boss', False)),
                monster=not world.specs[identity]['human'],
                storedTarget=unit['long_board'].get(16),
                commands=[dict(traceId=c.get('traceId'), target=c.get('target'), skill=c.get('skill'),
                               tick=c.get('tick'), duration=c.get('duration'),
                               useIndex=c.get('use_index')) for c in unit['commands']])
        samples.append(dict(tick=world.tick, battleState=world.battle_state, verdict=world.verdict,
                            prizes=len(world.prizes), units=units))
        return False

    report = combat_sandbox.run_scenario(dict(scenario, mathSeed=math_seed, libSeed=lib_seed),
                                         include_trace=True, abort=sample)
    return report, samples, holder


def summarize(report, samples):
    result = report['result']
    events = report['trace']
    prizes = [e for e in events if e['kind'] == 'prize']
    states = [e for e in events if e['kind'] == 'state']
    enqueues = [e for e in events if e['kind'] == 'enqueue']
    # The rival leader is the unit the prize events name; every prize needs one state-8 entry of it.
    boss = prizes[0]['target'] if prizes else next(
        (int(i) for i, u in samples[-1]['units'].items() if u['boss']), None)
    death = next((s['tick'] for s in samples
                  if boss is not None and s['units'][str(boss)]['hp'] == 0), None)
    boss_states = [e for e in states if e.get('target') == boss] if boss is not None else []

    def at(tick):
        return next((s for s in samples if s['tick'] == tick), None)

    death_sample = at(death) if death is not None else None
    queued_at_death = sum(len(u['commands']) for u in death_sample['units'].values()) \
        if death_sample else None
    queued_targeting_boss = sum(1 for u in death_sample['units'].values()
                                for c in u['commands'] if c['target'] == boss) if death_sample else None
    stored_at_death = sum(1 for u in death_sample['units'].values()
                          if u['storedTarget'] == boss) if death_sample else None
    post = [s for s in samples if death is not None and s['tick'] > death]
    post_prizes = [e for e in prizes if e['tick'] > death] if death is not None else []
    reentries = [e for e in boss_states if death is not None and e['tick'] > death and e['new'] == 6]
    leave_after = [e for e in boss_states if death is not None and e['tick'] > death and e['new'] == 8]
    # Every command that disappears between two samples was released (or cleared by a knockdown).
    released_after = []
    previous = death_sample
    for sample in post:
        if previous is None:
            previous = sample
            continue
        before = {c['traceId']: c for u in previous['units'].values() for c in u['commands']}
        after = {c['traceId'] for u in sample['units'].values() for c in u['commands']}
        for trace_id, command in before.items():
            if trace_id not in after:
                released_after.append(dict(traceId=trace_id, tick=sample['tick'],
                                           target=command['target'], skill=command['skill'],
                                           targetsBoss=command['target'] == boss,
                                           useCount=command['useIndex']))
        previous = sample
    return dict(
        boss=boss, verdict=result['verdict'], verdictTick=result.get('verdictTick'),
        ticks=result['ticks'], prizes=len(prizes), prizesAtVerdict=result.get('preVerdictPrizeCallbacks'),
        pendingChests=(result.get('rewardEntitlement') or {}).get('pendingChestCount'),
        awardedChests=(result.get('rewardEntitlement') or {}).get('awardedChestCount'),
        bossDeathTick=death, bossStateChanges=[(e['tick'], e['old'], e['new']) for e in boss_states],
        queuedCommandsAtBossDeath=queued_at_death, queuedTargetingBoss=queued_targeting_boss,
        storedTargetsAtBossDeath=stored_at_death,
        prizesAfterBossDeath=len(post_prizes),
        bossReentriesAfterDeath=len(reentries), bossLeavingsAfterDeath=len(leave_after),
        commandsReleasedAfterDeath=len(released_after),
        commandsReleasedAfterDeathTargetingBoss=sum(1 for c in released_after if c['targetsBoss']),
        firstReleaseAfterDeath=(released_after[0]['tick'] if released_after else None),
        lastReleaseAfterDeath=(released_after[-1]['tick'] if released_after else None),
        prizeTicks=[e['tick'] for e in prizes],
        enqueues=len(enqueues),
        enqueueSources=dict(Counter(e['source'] for e in enqueues)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path(
        r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy\KA-Website'
        r'\strategiespostrust1000tickslimit.sqlite'))
    parser.add_argument('--holder', default='earned:19:0')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    report, samples, holder = trace(args.library, args.holder)
    summary = summarize(report, samples)
    summary['holder'] = args.holder
    summary['library'] = args.library.name
    # The new run-level metrics, straight out of the canonical engine's own report (Part B).
    summary['progressMetrics'] = report['result']['progressMetrics']
    print(json.dumps(summary, indent=1, default=str))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=1, default=str), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
