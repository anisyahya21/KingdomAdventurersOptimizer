"""Lifetime encounter records must never decrease, and record holders must stay replayable.

Runs the whole Part F/G/N contract against a real store:

  * the lifetime aggregate is written in the same transaction as the run and matches an independent
    recount of the surviving rows;
  * filling the library until a candidate is pruned cannot lower attempts, wins, losses, the
    no-verdict count or either chest maximum (`Store.add` is what prunes);
  * `sum(lifetimeAttempts) == totalRuns` for a library that has never lost rows;
  * a record holder still replays to its stored digest AFTER its candidate has been pruned.

    python check_optimizer_lifetime.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats, validate_scenario  # noqa: E402


def fake_result(verdict, prize_callbacks, pending, awarded, seeds, digest):
    """One compact result shaped like the runner's, so the store's own gates are exercised."""
    return dict(verdict=verdict, censored=verdict is None, ticks=40,
               prizeCallbacks=prize_callbacks, survivors=1, resourceUses=0,
               behavior=dict(attacks=1, heals=0, prizes=prize_callbacks),
               seeds=list(seeds), digest=digest, elapsedSeconds=.01,
               rewardOutcome=dict(awardedChests=awarded, pendingChests=pending,
                                  awardedBasis='reward-entitlement-certificate'))


def add_lane_rivals(store, base, awarded, pending, label='Lane rival', runs=4):
    """Four candidates that TIE the victim's maxima but outrank it inside the elite lanes.

    Objective v2 protects a build while it holds a lane-pool place, so a fixture that wants the prune
    path to stay reachable has to fill the pools with rivals first. They deliberately tie the victim's
    numbers: a strictly greater value would take the lifetime record as well, and then the counters
    under test would be the rivals' rather than the pruned candidate's.

    Each rival repeats the tying run `runs` times, which is what breaks the remaining tie: equal
    maxima, but a far higher win interval, so the lane ordering puts every rival ahead.
    """
    import copy
    for index in range(4):
        rival = copy.deepcopy(base)
        rival['ownUnits'][0]['parameters'][13]['rawValue'] += 1000+index
        rival = validate_scenario(rival)
        with store.db:
            rival_id = store.add(rival, f'{label} {index}', 'mutation', stats(rival))
            for ordinal in range(runs):
                store.record(rival_id, 'discovery', ordinal,
                             # `fake_result(verdict, prize_callbacks, pending, awarded, seeds,
                             # digest)`: the callbacks match the opportunity so maxima tie exactly.
                             dict(fake_result(1, pending, pending, awarded,
                                              (900+index*10+ordinal, 950+index*10+ordinal),
                                              f'rival-{index}-{ordinal}'),
                                  # A stronger stored-attack setup, so a victim whose own run carries
                                  # progress metrics cannot hold the setup lane either. These counters
                                  # never touch a lifetime record.
                                  progressMetrics=dict(
                                      bossIdentity=1, bossDeathTick=1, bossLeavingTick=2,
                                      storedCommandsAtDeath=99, storedCommandsTargetingBossAtDeath=99,
                                      storedTargetHoldersAtDeath=9, commandsTargetingBoss=99,
                                      commandsTargetingBossReleased=99,
                                      maxSimultaneousStoredCommands=99,
                                      maxSimultaneousCommandsTargetingBoss=99,
                                      storedTargetHoldersPeak=9, postDeathBossReentries=99,
                                      postDeathBossLeavings=99, postDeathPrizes=99,
                                      commandsReleasedAfterDeath=99,
                                      commandsReleasedAfterDeathTargetingBoss=99,
                                      firstPostDeathCommandReleaseTick=3,
                                      lastPostDeathCommandReleaseTick=4)))


def prune_candidate(path, victim):
    """Fill the victim's own encounter until `Store.add` drops that candidate, and report it.

    The active population is bounded *per encounter* (plus a global ceiling), so a fixture that wants
    the prune path exercised has to fill the victim's own fight: filling a different one only grows
    that fight's population and leaves the victim untouched.
    """
    import copy
    store = optimizer.Store(path, provenance())
    try:
        # The filler has to land in the victim's own fight, because that is the population the bound
        # is stated in.
        fight = store.candidate_encounters().get(victim, 19)
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=fight))
        for index in range(optimizer.MAX_CANDIDATES_PER_ENCOUNTER + 2):
            # `identity()` is deliberately seed-independent, so a filler has to differ in the
            # scenario itself; a real parameter step is exactly that.
            filler = copy.deepcopy(base)
            filler['ownUnits'][0]['parameters'][13]['rawValue'] += index + 1
            filler = validate_scenario(filler)
            with store.db:
                store.add(filler, f'Filler mutation {index}', 'mutation', stats(filler))
            if not any(row[0] == victim for row in store.db.execute('SELECT id FROM candidate')):
                return True
        print(f'    diagnostic: candidates={len(store.candidates())} '
              f'per-encounter max={optimizer.MAX_CANDIDATES_PER_ENCOUNTER}')
        return False
    finally:
        store.close()


def check_monotonic(failures, root):
    """Counts and maxima from accepted runs survive candidate pruning unchanged."""
    path = Path(root) / 'lifetime.sqlite'
    scenario = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        # A mutation, because pruning only ever drops the oldest unelected mutation.
        cid = store.add(scenario, 'Lifetime fixture', 'mutation', stats(scenario))
        store.record(cid, 'discovery', 0, fake_result(2, 12, 12, 0, (1, 2), 'loss-small'))
        store.record(cid, 'discovery', 1, fake_result(2, 31, 31, 0, (3, 4), 'loss-big'))
        store.record(cid, 'discovery', 2, fake_result(1, 3, 3, 9, (5, 6), 'win-small'))
        store.record(cid, 'discovery', 3, fake_result(None, None, None, None, (9, 10), 'censored'))
        # Four rivals that tie the two maxima and outrank the fixture inside the lanes, so the
        # objective's pruning protection (a lane place is protected) leaves it replaceable.
        add_lane_rivals(store, scenario, 9, 31)
    key = '19:0'
    expected = dict(lifetimeAttempts=20, lifetimeWins=17, lifetimeLosses=2, lifetimeNoVerdict=1,
                    highestPotentialChests=31, highestChestsEarned=9)
    first = dict(store.lifetime()[key])
    for field, value in expected.items():
        if first.get(field) != value:
            failures.append(f'{field}: stored {first.get(field)!r}, expected {value!r}')
    total = store.get('totalRuns')
    recorded = sum(entry['lifetimeAttempts'] for entry in store.lifetime().values())
    if recorded != total:
        failures.append(f'sum(lifetimeAttempts) {recorded} != totalRuns {total}')
    if cid in store.elite_members():
        failures.append('the fixture is still a lane member, so the prune path is not reachable')
    print(f'  after the fixture runs: {json.dumps({k: first.get(k) for k in expected})}')
    print(f'  invariant: sum(attempts)={recorded} == totalRuns={total}')
    print('  the fixture holds no lane place (four tied rivals outrank it), so it is replaceable')
    store.close()

    if not prune_candidate(path, cid):
        failures.append('the fixture never pruned its candidate, so pruning was not exercised')
        return
    store = optimizer.Store(path, provenance())
    after = dict(store.lifetime()[key])
    if store.get('totalRuns') != total:
        failures.append('pruning changed totalRuns')
    store.close()
    for field, value in expected.items():
        if after.get(field) != value:
            failures.append(f'pruning lowered {field}: {after.get(field)!r} != {value!r}')
    # And the independent recount of what survives must NOT be used as the lifetime truth.
    print(f'  after pruning the candidate: {json.dumps({k: after.get(k) for k in expected})}')


def check_holder_replay(failures, root):
    """A record holder replays to its stored digest after its candidate has been pruned."""
    import strategy_optimizer_adapter as adapter
    path = Path(root) / 'holder.sqlite'
    scenario = validate_scenario(dict(default_scenario(), tickLimit=1200, encounterId=0))
    real = adapter.simulate(scenario, (7, 8), backend='python')
    if real.get('verdict') is None:
        failures.append('the holder fixture did not resolve; pick another encounter/horizon')
        return
    scenario = dict(scenario, mathSeed=7, libSeed=8)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        cid = store.add(scenario, 'Holder fixture', 'mutation', stats(scenario))
        store.record(cid, 'discovery', 0, real)
    key = '0:0'
    holders = store.holders()
    holder_key = next((name for name in (f'earned:{key}', f'potential:{key}')
                       if holders.get(name, {}).get('digest') == real.get('digest')), None)
    if holder_key is not None:
        # Four rivals that tie this run's maxima (so the record stays with the fixture) but outrank it
        # inside the lanes, which is what makes it replaceable under the objective's pruning
        # protection. `chest_count`/`potential_chests` give the exact values to tie.
        with store.db:
            add_lane_rivals(store, scenario, optimizer.chest_count(real)[0] or 0,
                            optimizer.potential_chests(real) or 0, 'Holder rival')
    store.close()
    if holder_key is None:
        failures.append('the real run did not become a record holder')
        return
    if not prune_candidate(path, cid):
        failures.append('the holder fixture never pruned its candidate')
        return
    holder = optimizer.load_holder(path, holder_key)
    if holder is None:
        failures.append('the record holder was lost with its candidate')
        return
    compact = adapter.simulate(holder['scenario'], (holder['mathSeed'], holder['libSeed']),
                               backend='python')
    if compact.get('digest') != holder.get('digest'):
        failures.append('the pruned record holder no longer replays to its stored digest')
        return
    print(f'  {holder_key}: replayed after pruning, verdict {compact["verdict"]} '
          f'value {holder["value"]}, digest matches')


def check_holder_bridge_replay(failures, root):
    """The Encounter Overview's replay buttons, driven through the host object the page actually calls.

    "Replay highest earned" / "Replay highest potential" are the primary replay controls, and they call
    `replay_holder(key)` over the JS host surface. That method used to be defined on the library-lock
    object instead of on the bridge, so the page's call could never resolve and the buttons silently did
    nothing. This builds a real library with a real record holder, prunes the candidate that set it, and
    then drives the bridge itself:

      * the frozen scenario + seed pair come back, so the replay is the exact battle the displayed
        number came from, not a re-simulation of whatever is currently selected;
      * the call takes the record key and nothing else, so it cannot depend on the selected Strategy
        Family;
      * a holder with no frozen replay data is refused with a reason instead of failing after a click.
    """
    import inspect
    import time
    from strategy_optimizer_desktop import Bridge, LibraryLock

    failures_before = len(failures)
    if not hasattr(Bridge, 'replay_holder'):
        failures.append('the desktop Bridge does not expose replay_holder, so the page\'s record '
                        'replay buttons cannot resolve')
        return
    if hasattr(LibraryLock, 'replay_holder'):
        failures.append('replay_holder is still defined on LibraryLock; it belongs to the Bridge')
    parameters = list(inspect.signature(Bridge.replay_holder).parameters)
    if parameters != ['self', 'key']:
        failures.append(f'Bridge.replay_holder takes {parameters}; the record buttons must need only '
                        'the record key')

    import strategy_optimizer_adapter as adapter
    path = Path(root) / 'bridge-holder.sqlite'
    scenario = validate_scenario(dict(default_scenario(), tickLimit=1200, encounterId=0))
    real = adapter.simulate(scenario, (7, 8), backend='python')
    if real.get('verdict') is None:
        failures.append('the bridge holder fixture did not resolve; pick another encounter/horizon')
        return
    scenario = dict(scenario, mathSeed=7, libSeed=8)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        cid = store.add(scenario, 'Bridge holder fixture', 'mutation', stats(scenario))
        store.record(cid, 'discovery', 0, real)
    key = '0:0'
    holders = store.holders()
    holder_key = next((name for name in (f'earned:{key}', f'potential:{key}')
                       if holders.get(name, {}).get('digest') == real.get('digest')), None)
    if holder_key is None:
        failures.append('the real run did not become a record holder')
        store.close()
        return
    with store.db:
        add_lane_rivals(store, scenario, optimizer.chest_count(real)[0] or 0,
                        optimizer.potential_chests(real) or 0, 'Bridge rival')
    store.close()
    if not prune_candidate(path, cid):
        failures.append('the bridge holder fixture never pruned its candidate')
        return

    bridge = Bridge(path)
    try:
        deadline = time.monotonic() + 60
        while bridge.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        holder = optimizer.load_holder(path, holder_key)
        result = bridge.replay_holder(holder_key)
        if not result.get('ok'):
            failures.append(f'replay_holder refused a live record holder: {result.get("error")!r}')
        else:
            if result.get('holder') != holder_key:
                failures.append(f'replay_holder replayed {result.get("holder")!r}, asked for '
                                f'{holder_key!r}')
            if (result.get('mathSeed'), result.get('libSeed')) != (holder['mathSeed'],
                                                                   holder['libSeed']):
                failures.append('replay_holder did not use the frozen seed pair')
            if result.get('value') != holder.get('value') or \
                    result.get('verdict') != holder.get('verdict'):
                failures.append('replay_holder did not report the frozen record result')
            replayed = result.get('scenario') or {}
            if (replayed.get('encounterId'), replayed.get('tickLimit')) != \
                    (holder['scenario']['encounterId'], holder['scenario']['tickLimit']):
                failures.append('replay_holder did not use the frozen scenario')
            payload = result.get('replay') or {}
            final = payload.get('finalState') or {}
            if final.get('verdict') != holder.get('verdict'):
                failures.append(f'replayed trace verdict {final.get("verdict")!r} != record '
                                f'{holder.get("verdict")!r}')
            print(f'  {holder_key}: bridge replay ok after pruning, verdict '
                  f'{final.get("verdict")} value {result.get("value")}, seeds '
                  f'{result.get("mathSeed")}/{result.get("libSeed")}')

        # A holder with no frozen data must be refused with a reason, not crash.
        import strategy_optimizer_desktop as desktop
        original = desktop.load_holder
        desktop.load_holder = lambda library, name: dict(original(library, name) or {}, scenario=None)
        try:
            refused = bridge.replay_holder(holder_key)
        finally:
            desktop.load_holder = original
        if refused.get('ok'):
            failures.append('a holder with no frozen scenario was replayed instead of refused')
        elif 'frozen replay data' not in str(refused.get('error')):
            failures.append(f'the refusal does not say why: {refused.get("error")!r}')
        else:
            print(f'  a holder without frozen data is refused: {refused["error"]!r}')
    finally:
        bridge.close()
        bridge._optimizer.thread.join(180)
        bridge._guard.close()
    if len(failures) == failures_before:
        print('  the encounter-level replay controls work through the real host object')


def main():
    failures = []
    with tempfile.TemporaryDirectory(prefix='ka-lifetime-') as root:
        check_monotonic(failures, root)
        check_holder_replay(failures, root)
        check_holder_bridge_replay(failures, root)
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print('  lifetimes are monotonic across pruning, the attempt invariant holds, and a pruned '
          'record holder still replays')
    return 0


if __name__ == '__main__':
    sys.exit(main())
