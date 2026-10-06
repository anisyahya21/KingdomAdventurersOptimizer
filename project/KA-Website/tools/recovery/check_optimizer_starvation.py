"""Anti-starvation: the control law, the candidates it creates, and the bounds that stop it.

Part 2 of the follow-up pass. The real 24-worker run fell from 37% to 20% useful utilisation while the
search was still finding improvements, so normal branching cannot always replace the work the pool
consumes. This checker covers the whole mechanism except the wall-clock measurement of a live pool
(that is `check_optimizer_utilisation.py`):

  * the control law: sustained low utilisation *with free workers and nothing runnable* raises the
    pressure, a single dip does not, and a healthy window drops it back to zero;
  * the gate: while the reservoir still holds enough runnable work - or the interval has not passed -
    nothing is created, at any pressure (the mechanism can never become a second unbounded generator);
  * the candidates: every starvation reseed is a legal Search Space v3 build, is genuinely new, is
    labelled in the lineage as a reseed with its kind, and deliberately includes stat-space movement;
  * the bounds: the per-encounter and global in-population reseed caps and the reservoir target are
    respected, and the encounter choice prefers the encounter with the least runnable work.

    python check_optimizer_starvation.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)


def stat_vector(scenario):
    """Every human's searchable stats as one tuple, so "the stats moved" is comparable.

    A stored scenario is the JSON round trip of a validated one, so its parameter keys are strings;
    a freshly generated one still has the engine's integer keys. Both are read here.
    """
    from search_contract import CORE_STATS, INT_STAT
    identifiers = sorted({**CORE_STATS, **INT_STAT}.values())

    def value(unit, pid):
        parameters = unit.get('parameters') or {}
        entry = parameters.get(pid) or parameters.get(str(pid)) or {}
        return int(entry.get('rawValue') or 0)

    return tuple(tuple(value(unit, pid) for pid in identifiers)
                 for unit in scenario['ownUnits'] if unit.get('human'))


def check_control_law(failures):
    """`starvation_level` and `window_utilisation` on samples with known answers."""
    checks = 0
    for utilisation, expected in ((0.95, 0), (0.80, 0), (0.75, 0), (0.69, 1), (0.55, 1),
                                  (0.45, 2), (0.35, 2), (0.25, 3), (0.0, 3), (None, 0)):
        got = learner.starvation_level(utilisation)
        if got != expected:
            failures.append(f'starvation_level({utilisation}) = {got}, expected {expected}')
        checks += 1

    def samples(total_idle, seconds, workers=8, step=.25, starved=False):
        steps = int(seconds/step)
        return [(index*step, total_idle*index/max(1, steps), workers,
                 starved and index == steps//2) for index in range(steps + 1)]

    # A fully busy pool: no idle seconds accumulate, so nothing is starved even if a dip once happened.
    utilisation, starved = learner.window_utilisation(samples(0., 12., starved=True))
    if utilisation != 1.0 or not starved:
        failures.append(f'a busy window read utilisation={utilisation!r} starved={starved!r}')
    if learner.starvation_level(utilisation) != 0:
        failures.append('a busy window raised the pressure')
    checks += 2
    # A starved pool: three quarters of the worker-seconds in the window are idle.
    utilisation, starved = learner.window_utilisation(samples(.75*8.*12., 12., starved=True))
    if utilisation is None or not (0.2 <= utilisation <= 0.3) or not starved:
        failures.append(f'a starved window read utilisation={utilisation!r} starved={starved!r}')
    if learner.starvation_level(utilisation) != 3:
        failures.append(f'a starved window asked for level {learner.starvation_level(utilisation)}')
    checks += 2
    # One dip inside an otherwise healthy window: every worker idle for a single 0.25 s sample.
    dip = samples(8.*.25, 12., starved=True)
    utilisation, _ = learner.window_utilisation(dip)
    if utilisation is None or utilisation < .9:
        failures.append(f'a single dip moved the reading to {utilisation!r}')
    if learner.starvation_level(utilisation) != 0:
        failures.append('a single dip raised the pressure')
    checks += 2
    # A window that is too short to judge: a fresh Start must not be reseeded on one idle moment.
    utilisation, starved = learner.window_utilisation(samples(.75*8.*3., 3., starved=True))
    if utilisation is not None:
        failures.append(f'a {3}s window produced a decision ({utilisation!r})')
    if learner.starvation_level(utilisation) != 0:
        failures.append('a short window raised the pressure')
    checks += 2
    # Low utilisation with no starved sample is coordinator-busy or duty-cycle idle: the caller's own
    # gate keeps the pressure at zero, and the gate is asserted directly here.
    utilisation, starved = learner.window_utilisation(samples(.75*8.*12., 12., starved=False))
    level = learner.starvation_level(utilisation) if starved else 0
    if starved or level != 0:
        failures.append('an idle-but-not-starved window was treated as starvation')
    checks += 2
    # A dry planner with a saturated coordinator must NOT count as starvation: the same idle workers,
    # but the shortfall is the single coordinator thread, and more candidates would feed the bottleneck.
    trace = [(index*.25, .75*8*12*index/48, 8, True, 0.9*12*index/48) for index in range(49)]
    utilisation, starved = learner.window_utilisation(trace)
    coordinator = learner.window_coordinator_fraction(trace)
    if coordinator is None or not (.85 <= coordinator <= .95):
        failures.append(f'the coordinator fraction read {coordinator!r}')
    if not (starved and utilisation is not None and utilisation < learner.STARVATION_HEALTHY):
        failures.append('the veto fixture did not reproduce a starved window')
    if not (coordinator is not None and coordinator >= learner.COORDINATOR_SATURATED):
        failures.append('a saturated coordinator was not detected')
    checks += 3
    # ...and the same window with a slack coordinator still raises the pressure.
    slack = [(index*.25, .75*8*12*index/48, 8, True, 0.2*12*index/48) for index in range(49)]
    coordinator = learner.window_coordinator_fraction(slack)
    if coordinator is None or coordinator >= learner.COORDINATOR_SATURATED:
        failures.append(f'a slack coordinator read {coordinator!r}')
    checks += 1
    print(f'  control law: {checks} unit checks passed', flush=True)
    return checks


def check_gate(failures):
    """The gate that keeps starvation reseeding bounded, at every pressure."""
    checks = 0
    cases = [
        # (level, seconds since last, open seeds, target) -> (create, count)
        ((0, 999., 0, 72), (False, 0)),          # healthy: never
        ((3, 0.5, 0, 72), (False, 0)),           # too soon
        ((3, 999., 72, 72), (False, 0)),         # reservoir still full
        ((3, 999., 100, 72), (False, 0)),        # reservoir over target
        ((1, 999., 10, 72), (True, 2)),
        ((2, 999., 10, 72), (True, 4)),
        ((3, 999., 10, 72), (True, 6)),
    ]
    for (level, since, open_seeds, target), expected in cases:
        got = optimizer.starvation_action(level, since, open_seeds, target)
        if got != expected:
            failures.append(f'starvation_action{level, since, open_seeds, target} = {got}, '
                            f'expected {expected}')
        checks += 1
    if learner.RESEED_BATCH[3] > learner.BRANCH_FACTOR + 2:
        failures.append('the highest-pressure batch is larger than the branching factor allows')
    checks += 1
    print(f'  gate: {checks} unit checks passed (no creation while healthy, too soon or full)',
          flush=True)
    return checks


def fixture_store(root, encounters=(19, 10), name='starvation.sqlite'):
    """A real store with a few candidates per encounter, so lanes and regions exist to draw from."""
    path = Path(root) / name
    store = optimizer.Store(path, provenance())
    import copy
    seeds = []
    with store.db:
        store.set('searchSpaceVersion', 3)
        for encounter in encounters:
            base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=encounter))
            store.set('scope', optimizer.scope(dict(default_scenario(), tickLimit=40)))
            seeds.append(store.add(base, f'Baseline enc{encounter}', 'supplied', stats(base)))
            for index in range(6):
                variant = copy.deepcopy(base)
                variant['ownUnits'][0]['parameters'][13]['rawValue'] += 10 + index
                variant = validate_scenario(variant)
                cid = store.add(variant, f'Branch enc{encounter} {index}', 'mutation', stats(variant))
                for ordinal in range(learner.DISCOVERY_RUNS):
                    store.record(cid, 'discovery', ordinal, dict(
                        verdict=1 if index % 2 else 2, censored=False, ticks=40,
                        prizeCallbacks=index + ordinal, survivors=1, resourceUses=0,
                        behavior=dict(attacks=1, heals=0, prizes=index + ordinal),
                        seeds=[ordinal, 100 + ordinal], digest=f'd{encounter}-{index}-{ordinal}',
                        elapsedSeconds=.01,
                        rewardOutcome=dict(awardedChests=index, pendingChests=index,
                                           awardedBasis='test', inventoryVerified=False)))
    return store


def check_reseed_candidates(failures, root):
    """Real reseeds: legal, new, labelled, stat-exploring, and bounded."""
    store = fixture_store(root)
    try:
        created = []
        diagnostics = {}
        # Eight decisions at the highest pressure, one proposal number each, exactly as the loop asks.
        for proposal in range(8):
            ids = optimizer.spawn_children(store, 19, 2, proposal, diagnostics,
                                           sources=learner.RESEED_SOURCES, reseed=True)
            created.extend(ids)
        if len(created) < 8:
            failures.append(f'only {len(created)} starvation candidates were created in 8 decisions')
        lineages = {row['candidate']: row for row in learner.lineage_rows(store.db)}
        rows = {row['id']: json.loads(row['scenario'])
                for row in store.db.execute('SELECT id, scenario FROM candidate')}
        kinds = {}
        stat_vectors = set()
        stat_moves = 0
        for cid in created:
            row = lineages.get(cid) or {}
            source = str(row.get('source') or '')
            if not source.startswith('reseed:'):
                failures.append(f'{cid[:10]} was created with lineage source {source!r}, not a reseed')
            else:
                kinds[source.split(':', 1)[1]] = kinds.get(source.split(':', 1)[1], 0) + 1
            scenario = rows.get(cid)
            if scenario is None:
                failures.append(f'{cid[:10]} is not in the candidate table')
                continue
            try:
                validate_scenario(scenario)
            except Exception as error:                                   # noqa: BLE001
                failures.append(f'{cid[:10]} is not a legal Search Space v3 build: {error}')
            stat_vectors.add(stat_vector(scenario))
            parent_id = row.get('parent')
            if parent_id and parent_id in rows and stat_vector(rows[parent_id]) != stat_vector(scenario):
                stat_moves += 1
        if len(kinds) < 2:
            failures.append(f'the reseeds only used source kinds {sorted(kinds)}; the mixture is meant '
                            'to draw on elites, older families, regions and restarts')
        if stat_moves < 2:
            failures.append(f'only {stat_moves} of {len(created)} reseeds moved a stat away from its '
                            'parent; starvation exploration must deliberately probe stat space')
        if len(stat_vectors) < 3:
            failures.append(f'the reseeds produced {len(stat_vectors)} distinct stat vector(s); '
                            'starvation exploration must move stats, not only structure')
        # Duplicates are never created: every returned id is a live candidate with its own identity.
        if len(set(created)) != len(created):
            failures.append('a starvation decision returned the same candidate twice')
        print(f'  reseeds: {len(created)} candidates over 8 decisions, kinds '
              f'{dict(sorted(kinds.items()))}, stat moves {stat_moves}, '
              f'distinct stat vectors {len(stat_vectors)}')

        # The bounds: the per-encounter cap decides the target, and the global cap stops everything.
        limits = {cid: (learner.DISCOVERY_RUNS, 0) for cid in rows}
        ordinals = {}
        chosen, by_encounter = optimizer.reseed_target(store, limits, ordinals)
        if chosen != 10:
            failures.append(f'the encounter chosen for a reseed was {chosen}, expected 10: encounter '
                            '19 is at its per-encounter reseed cap and must not be chosen again')
        if by_encounter.get('19') != len(created):
            failures.append(f'by_encounter reports {by_encounter} for {len(created)} reseeds')
        # Fill the second encounter to its own cap; only then must the controller stop choosing.
        for proposal in range(40):
            if not optimizer.spawn_children(store, 10, 2, 100 + proposal, {},
                                            sources=learner.RESEED_SOURCES, reseed=True):
                break
        chosen, by_encounter = optimizer.reseed_target(store, limits, ordinals)
        if chosen is not None:
            failures.append(f'the encounter chosen for a reseed was {chosen}, expected None once '
                            'every encounter holds the per-encounter reseed cap')
        print(f'  bounds: {sum(by_encounter.values())} reseeds in the population, chosen={chosen}')
        ok, count = optimizer.starvation_action(3, 999., 0, 72)
        if not ok or count != learner.RESEED_BATCH[3]:
            failures.append('the pressure gate stopped creating while the encounter was unsaturated')
    finally:
        store.close()


def check_fairness(failures, root):
    """A starved encounter with less runnable work is chosen ahead of a busier one."""
    store = fixture_store(root, encounters=(19, 10, 12), name='fairness.sqlite')
    try:
        limits = {}
        for row in store.db.execute('SELECT id FROM candidate'):
            limits[row[0]] = (learner.DISCOVERY_RUNS, 0)
        index = store.candidate_encounters()
        # Encounter 10 is given a large runnable bank; 12 has none left; 19 keeps its first bank.
        ordinals = {}
        for cid, encounter in index.items():
            if encounter == 10:
                ordinals[(cid, 'discovery')] = 0
            elif encounter == 12:
                ordinals[(cid, 'discovery')] = learner.DISCOVERY_RUNS
            else:
                ordinals[(cid, 'discovery')] = learner.DISCOVERY_RUNS // 2
        chosen, by_encounter = optimizer.reseed_target(store, limits, ordinals)
        if chosen != 12:
            failures.append(f'the least-runnable encounter (12) was not chosen: got {chosen}')
        print(f'  fairness: runnable work '
              f'{[{k: v for k, v in sorted(optimizer.reseed_load(store, limits, ordinals)[0].items())}]} '
              f'-> encounter {chosen}', flush=True)
        if by_encounter:
            failures.append(f'by_encounter was {by_encounter} before any reseed existed')
    finally:
        store.close()


def main():
    failures = []
    checks = 0
    checks += check_control_law(failures)
    checks += check_gate(failures)
    with tempfile.TemporaryDirectory(prefix='ka-starvation-') as root:
        check_reseed_candidates(failures, root)
        check_fairness(failures, root)
    if failures:
        print('FAILURES:')
        for row in failures[:12]:
            print('  ' + row)
        return 1
    print(f'  {checks} control-law/gate checks plus the candidate, bound and fairness checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
