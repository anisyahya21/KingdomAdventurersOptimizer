"""Part L: the lane objective's intended behaviour, proven by deterministic counterexamples.

Six cases, each one a strategy the old single score would have thrown away:

  CASE 1  99 potential / loss / 0 earned   vs  20 potential / win / 20 earned
  CASE 2  0 potential / 0 earned / strong stored-attack setup   vs  1 earned / weak setup
  CASE 3  40 earned with 2 consumables     vs  20 earned with 0
  CASE 4  40 earned with 0 consumables     vs  40 earned with 3
  CASE 5  rare high yield, low win rate    vs  reliable low yield
  CASE 6  same potential, one converted    vs  one not

plus the two guarantees the architecture has to make beyond single comparisons: the reliability gate
is gone from admission (a complete bank with a 20% win rate can still be elite) while a censored bank
is still refused, and no lane can starve the parent selection.

    python check_optimizer_lanes.py
"""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402


def record(candidate, earned=None, potential=None, resources=0., wins=0, n=0, progress=None,
           encounter=19, defeat=0):
    """One candidate's lane evidence, shaped exactly like `optimizer.lane_record`'s output."""
    return dict(candidate=candidate, label=candidate, source='mutation', encounterId=encounter,
                defeatCount=defeat, n=n, wins=wins, losses=max(0, n-wins), censored=0,
                # A win is the only thing that can count as an earned observation.
                earnedCount=(wins if earned is not None else 0),
                winInterval=optimizer.wilson(wins, n) if n else [0., 1.],
                winRate=(wins/n if n else None), earnedMax=earned, potentialMax=potential,
                meanResources=resources, progress=dict(progress or {}), progressRuns=(1 if progress else 0),
                earliestBossDeathTick=(progress or {}).get('bossDeathTick'),
                earliestPostDeathReleaseTick=(progress or {}).get('firstPostDeathCommandReleaseTick'))


def setup(**values):
    base = dict(postDeathPrizes=0, postDeathBossLeavings=0, postDeathBossReentries=0,
                commandsReleasedAfterDeath=0, commandsReleasedAfterDeathTargetingBoss=0,
                storedCommandsAtDeath=0, storedCommandsTargetingBossAtDeath=0,
                maxSimultaneousStoredCommands=0, maxSimultaneousCommandsTargetingBoss=0,
                storedTargetHoldersPeak=0, storedTargetHoldersAtDeath=0, commandsTargetingBoss=0,
                commandsTargetingBossReleased=0)
    base.update(values)
    return base


def leaders(records):
    lanes = optimizer.elite_lanes(records)
    return optimizer.lane_leaders(records, lanes), lanes


def store_cases(expect):
    """Real-store regressions: evidence identity, earned qualification and pruning protection.

    The pure-function cases above cannot see the two defects this section exists for - the planner
    ranking from one phase while the page ranked from both, and `Store.add` deleting a lane leader -
    because both are properties of the store and the planner, not of the ordering functions.
    """
    import tempfile
    from pathlib import Path

    from strategy_optimizer_adapter import default_scenario, provenance, stats, validate_scenario
    notes = []

    def result(verdict, awarded, pending, index, progress=None):
        row = dict(verdict=verdict, censored=False, ticks=40, prizeCallbacks=pending, survivors=1,
                   resourceUses=0, behavior=dict(attacks=1, heals=0, prizes=pending),
                   seeds=[index, index+1], digest=f'run-{index}', elapsedSeconds=.01,
                   rewardOutcome=dict(awardedChests=awarded, pendingChests=pending,
                                      awardedBasis='reward-entitlement-certificate'))
        if progress is not None:
            row['progressMetrics'] = progress
        return row

    def build(root, name):
        path = Path(root)/name
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        store = optimizer.Store(path, provenance())
        with store.db:
            store.set('scope', optimizer.scope(base))
        return store, base

    def fillers(store, base, count, start=0):
        """Mutations with no evidence at all: the only replaceable population."""
        import copy
        with store.db:
            for index in range(count):
                scenario = copy.deepcopy(base)
                scenario['ownUnits'][0]['parameters'][13]['rawValue'] += start+index+1
                store.add(validate_scenario(scenario), f'Filler {start+index}', 'mutation',
                          stats(scenario))

    # Windows keeps the sqlite handle briefly after close, so the fixture directory may not be
    # removable in-process; the files themselves are temporary either way.
    with tempfile.TemporaryDirectory(prefix='ka-lane-store-', ignore_cleanup_errors=True) as root:
        # ---- the planner and the page must rank from the same evidence ----------------------
        store, base = build(root, 'evidence.sqlite')
        weak = dict(base, note='weak discovery')
        strong = dict(base, note='strong validation')
        with store.db:
            weak_id = store.add(weak, 'Weak discovery', 'mutation', stats(weak))
            strong_id = store.add(strong, 'Strong validation', 'mutation', stats(strong))
            # `weak`: eight weak discovery runs and nothing else.
            for ordinal in range(8):
                store.record(weak_id, 'discovery', ordinal,
                             result(1, 1, 1, ordinal))
            # `strong`: two weak discovery runs, then a 64-run validation bank worth 40 chests.
            for ordinal in range(2):
                store.record(strong_id, 'discovery', ordinal, result(1, 1, 1, 100+ordinal))
            for ordinal in range(64):
                store.record(strong_id, 'validation', ordinal,
                             result(1 if ordinal < 40 else 2, 40 if ordinal == 0 else 0,
                                    40 if ordinal == 0 else 0, 200+ordinal))
        aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
        rows = list(store.db.execute('SELECT id, label, source, scenario FROM candidate'))
        merged = {row['id']: optimizer.lane_record(row, optimizer.merge_aggregates(
            aggregates.get(f'aggregate:{row["id"]}:discovery'),
            aggregates.get(f'aggregate:{row["id"]}:validation'))) for row in rows}
        published = {payload['id']: payload['evidence']
                     for payload in optimizer.full_candidate_payloads(store)}
        expect('the planner and the published view use identical lane evidence',
               merged == published,
               'the two evidence builders disagree, so the page can show a different leader')
        lanes = optimizer.elite_lanes(list(merged.values()))
        expect('a strong validation bank is not masked by weak discovery runs',
               lanes['19:0']['earned'][0] == strong_id,
               f"earned lane {[merged[cid]['label'] for cid in lanes['19:0']['earned']]}")
        notes.append('evidence: planner and page agree, and the earned leader is the build whose '
                     'strength is in its validation bank')
        store.close()

        # ---- a loss-gate zero is not a conversion -------------------------------------------
        store, base = build(root, 'earned.sqlite')
        lossy = dict(base, note='loss only')
        winner = dict(base, note='winner')
        with store.db:
            lossy_id = store.add(lossy, 'High-potential loser', 'mutation', stats(lossy))
            winner_id = store.add(winner, 'Small winner', 'mutation', stats(winner))
            for ordinal in range(8):
                # Queued plenty, converted nothing: this is the case the earned lane must refuse.
                store.record(lossy_id, 'discovery', ordinal, result(2, 0, 99, ordinal))
            store.record(winner_id, 'discovery', 0, result(1, 3, 3, 500))
        aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
        rows = list(store.db.execute('SELECT id, label, source, scenario FROM candidate'))
        merged = {row['id']: optimizer.lane_record(row, optimizer.merge_aggregates(
            aggregates.get(f'aggregate:{row["id"]}:discovery'),
            aggregates.get(f'aggregate:{row["id"]}:validation'))) for row in rows}
        lanes = optimizer.elite_lanes(list(merged.values()))
        expect('a loss-only build with 99 potential is NOT an earned candidate',
               lossy_id not in lanes['19:0']['earned']
               and merged[lossy_id]['earnedMax'] is None,
               f"earned lane {lanes['19:0']['earned']}, earnedMax {merged[lossy_id]['earnedMax']}")
        expect('the same build still leads the potential lane',
               lanes['19:0']['potential'][0] == lossy_id, f"potential {lanes['19:0']['potential']}")
        expect('the one win is enough to be an earned candidate',
               lanes['19:0']['earned'][0] == winner_id, f"earned {lanes['19:0']['earned']}")
        notes.append('earned qualification: a 99-potential defeat leads potential and is refused by '
                     'earned; one 3-chest win is enough for earned')
        store.close()

        # ---- a lane leader survives saturation ----------------------------------------------
        store, base = build(root, 'prune.sqlite')
        elite = dict(base, note='potential elite')
        replaceable = dict(base, note='replaceable')
        with store.db:
            elite_id = store.add(elite, 'Potential elite', 'mutation', stats(elite))
            replaceable_id = store.add(replaceable, 'Replaceable', 'mutation', stats(replaceable))
            for ordinal in range(4):
                store.record(elite_id, 'discovery', ordinal, result(2, 0, 99, ordinal))
        protected = store.elite_members()
        expect('the potential elite is protected before saturation', elite_id in protected,
               f'protected {len(protected)}')
        expect('a build with no evidence is not protected', replaceable_id not in protected,
               'a baseless mutation must stay replaceable')
        # The active population is bounded per encounter, so this fills the elite's own fight well
        # past that bound: every filler after the bound forces one replacement.
        fillers(store, base, optimizer.MAX_CANDIDATES_PER_ENCOUNTER*3)
        survivors = {row[0] for row in store.db.execute('SELECT id FROM candidate')}
        expect('saturating the population does not delete the lane leader', elite_id in survivors,
               'the potential elite was pruned')
        expect('a non-elite replaceable mutation is the one that goes', replaceable_id not in survivors,
               'the replaceable mutation survived, so pruning did not do its job')
        expect('the population stayed inside its per-encounter bound (plus protected builds)',
               len(survivors) <= (optimizer.MAX_CANDIDATES_PER_ENCOUNTER
                                  + len(optimizer.LANE_NAMES)*optimizer.LANE_POOL + 2),
               f'{len(survivors)} candidates')
        expect('saturation kept replacing builds instead of filling the library',
               len(survivors) < optimizer.MAX_CANDIDATES_PER_ENCOUNTER*3,
               f'{len(survivors)} candidates survived {optimizer.MAX_CANDIDATES_PER_ENCOUNTER*3} adds')
        expect('protection stays bounded to the lane pools',
               len(store.elite_members()) <= len(optimizer.LANE_NAMES)*optimizer.LANE_POOL,
               f'{len(store.elite_members())} protected')
        notes.append(f'pruning: the potential elite survived saturation with '
                     f'{len(store.elite_members())} protected ids '
                     f'(bound {len(optimizer.LANE_NAMES)*optimizer.LANE_POOL})')
        store.close()
    return notes


def main():
    failures = []
    notes = []

    def expect(label, condition, detail=''):
        if condition:
            print(f'  ok    {label}')
        else:
            failures.append(f'{label}: {detail}')
            print(f'  FAIL  {label}: {detail}')

    # ---- CASE 1 ------------------------------------------------------------------------------
    a = record('A', earned=0, potential=99, n=64, wins=0)
    b = record('B', earned=20, potential=20, n=64, wins=64)
    table, lanes = leaders([a, b])
    expect('CASE 1 A is Potential Elite', lanes['19:0']['potential'][0] == 'A',
           f"potential lane {lanes['19:0']['potential']}")
    expect('CASE 1 B is Earned Elite', lanes['19:0']['earned'][0] == 'B',
           f"earned lane {lanes['19:0']['earned']}")
    expect('CASE 1 neither deletes the other',
           'A' in lanes['19:0']['potential'] and 'B' in lanes['19:0']['earned'],
           f"earned={lanes['19:0']['earned']} potential={lanes['19:0']['potential']}")
    # A has never won, so its loss-gate zero is not a conversion and it is not an earned candidate.
    expect('CASE 1 a loss-only build is not listed as earned', 'A' not in lanes['19:0']['earned'],
           f"earned lane {lanes['19:0']['earned']}")
    notes.append(f"CASE 1 potential lane {lanes['19:0']['potential']}, earned lane {lanes['19:0']['earned']}")

    # ---- CASE 2 ------------------------------------------------------------------------------
    a = record('A', earned=0, potential=0, n=8, wins=0,
               progress=setup(storedCommandsAtDeath=12, storedCommandsTargetingBossAtDeath=7,
                              maxSimultaneousCommandsTargetingBoss=9, commandsTargetingBoss=11,
                              commandsTargetingBossReleased=11, storedTargetHoldersPeak=2))
    b = record('B', earned=1, potential=1, n=8, wins=8)
    table, lanes = leaders([a, b])
    expect('CASE 2 A is Setup Elite', lanes['19:0']['setup'][0] == 'A',
           f"setup lane {lanes['19:0']['setup']}")
    expect('CASE 2 B is Earned Elite', lanes['19:0']['earned'][0] == 'B',
           f"earned lane {lanes['19:0']['earned']}")
    expect('CASE 2 the setup leader carries its evidence',
           table['19:0']['setup']['metrics']['progress']['storedCommandsTargetingBossAtDeath'] == 7,
           f"leader metrics {table['19:0']['setup']['metrics']}")
    notes.append(f"CASE 2 setup lane {lanes['19:0']['setup']}, earned lane {lanes['19:0']['earned']}")

    # ---- CASE 3 ------------------------------------------------------------------------------
    a = record('A', earned=40, potential=40, resources=2., n=64, wins=40)
    b = record('B', earned=20, potential=20, resources=0., n=64, wins=40)
    table, lanes = leaders([a, b])
    expect('CASE 3 the cheaper build does NOT replace the better one',
           lanes['19:0']['efficiency'][0] == 'A', f"efficiency lane {lanes['19:0']['efficiency']}")
    expect('CASE 3 the cheaper build is still preserved', 'B' in lanes['19:0']['efficiency'],
           f"efficiency lane {lanes['19:0']['efficiency']}")
    notes.append(f"CASE 3 efficiency lane {lanes['19:0']['efficiency']}")

    # ---- CASE 4 ------------------------------------------------------------------------------
    a = record('A', earned=40, potential=40, resources=0., n=64, wins=40)
    b = record('B', earned=40, potential=40, resources=3., n=64, wins=40)
    table, lanes = leaders([a, b])
    expect('CASE 4 equal chests: fewer consumables wins',
           lanes['19:0']['efficiency'][0] == 'A' and 'B' not in lanes['19:0']['efficiency'],
           f"efficiency lane {lanes['19:0']['efficiency']} (dominated members must not survive)")
    notes.append(f"CASE 4 efficiency lane {lanes['19:0']['efficiency']}")

    # ---- CASE 5 ------------------------------------------------------------------------------
    rare = record('RARE', earned=60, potential=60, resources=1., n=64, wins=13,
                  progress=setup(postDeathPrizes=44, commandsReleasedAfterDeathTargetingBoss=40,
                                 postDeathBossLeavings=45))
    steady = record('STEADY', earned=12, potential=14, resources=0., n=64, wins=64)
    table, lanes = leaders([rare, steady])
    expect('CASE 5 the rare mechanism leads the earned lane',
           lanes['19:0']['earned'][0] == 'RARE',
           f"earned={lanes['19:0']['earned']}")
    # Neither build has a prize worth chasing here: RARE cashed all 60 it reached, and STEADY's
    # 14-reached / 12-earned is a 2-chest gap, below the follow-up floor. So the pool is empty rather
    # than leading with a number too small to re-test, and the planner falls through to a lane that
    # does have something. RARE is not lost either way - it leads the earned lane just asserted.
    expect('CASE 5 with no worthwhile prize the potential pool is empty',
           lanes['19:0']['potential'] == [], f"potential={lanes['19:0']['potential']}")
    expect('CASE 5 the reliable low-yield build is preserved too',
           'STEADY' in lanes['19:0']['earned'] and 'STEADY' in lanes['19:0']['efficiency'],
           f"earned={lanes['19:0']['earned']} efficiency={lanes['19:0']['efficiency']}")
    expect('CASE 5 low reliability does not erase discovery value',
           table['19:0']['earned']['metrics']['winLower'] is not None
           and table['19:0']['earned']['metrics']['winLower'] < .80,
           f"reliability is reported, not gating: {table['19:0']['earned']['metrics']}")
    notes.append(f"CASE 5 earned lane {lanes['19:0']['earned']} at winLower "
                 f"{table['19:0']['earned']['metrics']['winLower']:.3f}")

    # ---- CASE 6 ------------------------------------------------------------------------------
    converted = record('CONVERTED', earned=30, potential=30, resources=0., n=64, wins=30)
    defeated = record('DEFEATED', earned=0, potential=30, resources=0., n=64, wins=0)
    table, lanes = leaders([converted, defeated])
    # **Inverted on purpose.** At equal potential the tie-break is the whole question, and it used to
    # favour the build that had converted. It now favours the build that still has a prize to cash:
    # CONVERTED has nothing unrealised (30-30), DEFEATED has all 30.
    expect('CASE 6 equal potential: the UNREALISED prize leads, not the converted build',
           lanes['19:0']['potential'][0] == 'DEFEATED',
           f"potential lane {lanes['19:0']['potential']}")
    expect('CASE 6 the cashed build leaves this pool but keeps the earned lane',
           lanes['19:0']['potential'] == ['DEFEATED'] and lanes['19:0']['earned'][0] == 'CONVERTED',
           f"potential={lanes['19:0']['potential']} earned={lanes['19:0']['earned']}")
    notes.append(f"CASE 6 potential lane {lanes['19:0']['potential']}")

    # ---- CASE 7 ------------------------------------------------------------------------------
    # The property the change exists for: a pool of unrealised prizes empties itself. OPEN leads while
    # it has 50 unrealised; once that is cashed, any other build with a prize left overtakes it here -
    # and the cashed build keeps the earned lane, so nothing is thrown away by moving on.
    open_prize = record('OPEN', earned=0, potential=50, n=64, wins=0)
    cashed = record('CASHED', earned=55, potential=55, n=64, wins=55)
    waiting = record('WAITING', earned=10, potential=45, n=64, wins=10)
    table, lanes = leaders([open_prize, cashed, waiting])
    expect('CASE 7 the unrealised prize leads the potential pool',
           lanes['19:0']['potential'][0] == 'OPEN', f"potential lane {lanes['19:0']['potential']}")
    expect('CASE 7 the fully-converted build still leads the earned lane',
           lanes['19:0']['earned'][0] == 'CASHED', f"earned lane {lanes['19:0']['earned']}")
    table, lanes = leaders([cashed, waiting])
    expect('CASE 7 once cashed, the pool moves on to the next unrealised prize',
           lanes['19:0']['potential'] == ['WAITING'],
           f"potential lane {lanes['19:0']['potential']}")
    notes.append(f"CASE 7 potential lane {lanes['19:0']['potential']}")

    # ---- CASE 8 ------------------------------------------------------------------------------
    # The measured failure the gate exists to stop. Ranking by the unrealised prize *alone* let a build
    # that reached 5 and earned 2 displace a build that reached and cashed 46, because a fully cashed
    # build has a gap of zero and therefore any gap at all beat it. A 3-chest prize is within the noise
    # of one or two prize callbacks, so it buys no place in this pool.
    tiny = record('TINY', earned=2, potential=5, n=64, wins=10)
    cashed46 = record('CASHED46', earned=46, potential=46, n=64, wins=64)
    table, lanes = leaders([tiny, cashed46])
    expect('CASE 8 a prize below the follow-up floor does not enter the potential pool',
           lanes['19:0']['potential'] == [], f"potential lane {lanes['19:0']['potential']}")
    expect('CASE 8 the cashed build is still the earned leader',
           lanes['19:0']['earned'][0] == 'CASHED46', f"earned lane {lanes['19:0']['earned']}")
    notes.append('CASE 8 a 3-chest gap is noise, not a prize')

    # ---- admission: the reliability gate is gone, censoring is still refused -----------------
    lucky = optimizer.summarize([
        dict(verdict=1 if i < 13 else 2, censored=False, prizeCallbacks=3 + (57 if i == 0 else 0),
             resourceUses=0, survivors=1, ticks=100, behavior=dict(prizes=3),
             rewardOutcome=dict(awardedChests=3 + (57 if i == 0 else 0)))
        for i in range(64)])
    expect('a complete 20%-win bank can still be elite', optimizer.quality(lucky) is not None,
           f'quality refused {lucky["winInterval"]}')
    censored = [dict(verdict=1, censored=False, prizeCallbacks=4, resourceUses=0, survivors=1,
                     ticks=100, behavior=dict(prizes=4), rewardOutcome=dict(awardedChests=4))
                for _ in range(64)]
    censored[-1] = dict(verdict=None, censored=True, prizeCallbacks=None, resourceUses=0, survivors=0,
                        ticks=100, behavior=dict(prizes=0), rewardOutcome=dict(awardedChests=None))
    expect('a bank with a censored run is still refused',
           optimizer.quality(optimizer.summarize(censored)) is None,
           'a half-run bank must not take an archive cell')

    # ---- no lane can starve the parent selection ---------------------------------------------
    pools = {lane: [f'{lane}-1', f'{lane}-2'] for lane in optimizer.LANE_NAMES}
    population = [cid for pool in pools.values() for cid in pool]
    used = {lane: 0 for lane in optimizer.LANE_NAMES}
    used['exploration'] = 0
    rng = random.Random(7)
    for proposal in range(400):
        _, lane = optimizer.choose_parent(pools, population, proposal, rng)
        used[lane] += 1
    per_lane = 400 // 5 * 4 // len(optimizer.LANE_NAMES)
    expect('no lane starves across 400 proposals',
           all(used[lane] >= per_lane for lane in optimizer.LANE_NAMES),
           f'parent sources {used}')
    expect('exploration still draws uniformly',
           used['exploration'] == 400 // 5, f'parent sources {used}')
    notes.append(f'parent sources over 400 proposals: {used}')

    # ---- store-level: identical evidence, earned qualification, pruning protection ----------
    notes.extend(store_cases(expect))

    print()
    for note in notes:
        print('  ' + note)
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  the lane objective keeps every counterexample the single score discarded, and no lane '
          'can starve the parent selection')
    return 0


if __name__ == '__main__':
    sys.exit(main())
