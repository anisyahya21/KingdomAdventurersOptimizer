"""Focused exploitation depth: the bounded second stage is judged on the validation bank's mean.

While a fight is focused the 8/24/64 screening ceiling is the whole search, so a convincing build can
never be measured past its first bank. The lane in :mod:`strategy_optimizer` raises those builds with a
bounded, paired-seed target. This checker pins the correction the review asked for:

  * the competitive band is the *validation bank's mean* earned (`chestSum/chestCount`, a loss
    counting the zero its native gate released), never the lifetime maximum - one lucky seed must not
    select a build;
  * discovery and validation counts are never mixed: only a complete, comparable validation bank
    carries a mean, and only its own run count is reported as the candidate's depth;
  * a saved 512-run winner and a fresh 64-run mutation both gain real validation work, checked through
    the ready ordinals the planner would hand to workers, not only the returned target number;
  * a weak candidate stays on the staged policy and never sees the 1024 floor;
  * a fine-tune point and its frozen parent are banked and granted together, while an ineligible
    partner is not dragged in;
  * the per-pass budget is a minority of the reservoir, so novel mutations keep their share;
  * every target is capped, and a build at its target stops gaining work;
  * the resident Highest Earned record holder keeps a repeat bank even when the mean lane refuses it
    (a low mean with no converted win), so a rare exceptional outcome is repeated rather than
    abandoned, and the record build and its fine-tune variants are banked past 512 together;
  * the record lane is inert outside the focused encounter and for a build that is not the holder,
    its budget is a ring-fenced minority, and a pruned holder is reported with its frozen replay kept;
  * the whole path is repeated from a synthetic store through `focused_exploit_inputs`,
    `staged_limits`, `focused_exploitation` and `interleave_ready`.

    python check_optimizer_focused_depth.py
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract                                                       # noqa: E402
import strategy_learner as learner                                           # noqa: E402
import strategy_optimizer as optimizer                                       # noqa: E402
from strategy_optimizer import (FOCUS_EXPLOIT_CAP, FOCUS_EXPLOIT_COMPETITIVE,  # noqa: E402
                                FOCUS_EXPLOIT_FLOOR, FOCUS_EXPLOIT_SHARE,
                                FOCUS_EXPLOIT_SLOTS, FOCUS_EXPLOIT_STEP,
                                FOCUS_RECORD_CAP, FOCUS_RECORD_FLOOR, FOCUS_RECORD_SHARE,
                                VALIDATION_RUNS, focused_exploit_target,
                                focused_exploitation, focused_exploit_inputs,
                                focused_record_holder, focused_record_target,
                                interleave_ready, population_records, scope)
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)


def evidence(cid, encounter=19, runs=0, wins=0, mean=None, comparable=True, earned_max=None):
    """One allocator evidence record, exactly the shape `focused_exploit_inputs` produces."""
    record = dict(candidate=cid, encounterId=encounter, validationRuns=runs, wins=wins,
                  earnedMean=mean, winInterval=optimizer.wilson(wins, runs),
                  comparable=comparable)
    if earned_max is not None:
        record['earnedMax'] = earned_max
    return record


def outcome(ordinal, chests=8, verdict=1):
    """One accepted result, shaped like the runner's own record."""
    return dict(verdict=verdict, censored=False, ticks=40, prizeCallbacks=chests+ordinal,
                survivors=1, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=chests+ordinal),
                seeds=[ordinal, 100+ordinal], digest=f'run-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=chests, pendingChests=chests,
                                   awardedBasis='test', inventoryVerified=False))


def variant(base, unit, delta):
    scenario = copy.deepcopy(base)
    scenario['ownUnits'][unit]['parameters'][13]['rawValue'] += delta
    return validate_scenario(scenario)


def read_aggregates(store):
    return {row[0]: json.loads(row[1]) for row in store.db.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}


def open_store():
    """A bare temporary sqlite store (a sandbox may refuse a fresh subdirectory)."""
    handle = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    handle.close()
    return optimizer.Store(Path(handle.name), provenance())


def close_store(store):
    path = store.path
    store.close()
    for suffix in ('', '-wal', '-shm'):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()


def check_target_choice(failures):
    """Best/competitive is the validation bank's mean, never the lifetime maximum."""
    checks = 0
    limits = {'steady': (0, 128), 'lucky': (0, 128), 'spiky': (0, 128)}
    steady = evidence('steady', runs=128, wins=120, mean=5.0, earned_max=5)
    lucky = evidence('lucky', runs=128, wins=120, mean=5.0, earned_max=800)
    spiky = evidence('spiky', runs=128, wins=120, mean=2.0, earned_max=1000)
    _out, assignments = focused_exploitation([steady, lucky, spiky], limits, 19, 96)
    if assignments['steady']['tier'] != 'competitive' or not assignments['lucky']['competitive']:
        failures.append('equal validation means were not both admitted to the competitive band')
    checks += 1
    for name in ('steady', 'lucky'):
        if assignments[name]['target'] != FOCUS_EXPLOIT_COMPETITIVE:
            failures.append(f'{name} did not earn the competitive target')
        checks += 1
    if assignments['spiky']['competitive']:
        failures.append('a single lucky earnedMax selected a build whose validation mean was low')
    checks += 1
    if assignments['spiky']['target'] != FOCUS_EXPLOIT_FLOOR:
        failures.append('a certified, non-competitive build did not fall back to the floor')
    checks += 1
    steady_decision = focused_exploit_target(steady, 19, best_mean=5.0, band_size=2)
    lucky_decision = focused_exploit_target(lucky, 19, best_mean=5.0, band_size=2)
    if {k: v for k, v in steady_decision.items() if k != 'candidate'} != \
            {k: v for k, v in lucky_decision.items() if k != 'candidate'}:
        failures.append('the lifetime maximum changed the target decision')
    checks += 1
    # An incomplete bank cannot define or join the band, however huge its reported mean.
    other = evidence('other', runs=128, wins=120, mean=1000.0, comparable=False)
    _out2, assignments2 = focused_exploitation([steady, lucky, other], limits, 19, 96)
    if 'other' in assignments2:
        failures.append('a non-comparable bank joined the exploitation decisions')
    checks += 1
    if assignments2['steady']['target'] != FOCUS_EXPLOIT_COMPETITIVE:
        failures.append('an incomparable build changed the band defined by comparable banks')
    checks += 1
    print(f'  target choice from validation mean: {checks} checks passed', flush=True)
    return checks


def check_saved_512(failures):
    """A saved 512-run winner earns real work past 512, staged at 512 or at the 64 screen."""
    checks = 0
    winner = evidence('winner', runs=512, wins=440, mean=2200/512, earned_max=40)
    for staged in ({'winner': (0, 512)}, {'winner': (0, VALIDATION_RUNS)}):
        out, assignments = focused_exploitation([winner], staged, 19, 48)
        if assignments['winner']['granted'] <= 0:
            failures.append('a saved 512-run winner was not granted new focused work')
        checks += 1
        if out['winner'][1] <= 512 or out['winner'][1] <= staged['winner'][1]:
            failures.append('the saved winner validation limit did not rise past its 512-run bank')
        checks += 1
        if out['winner'][0] != staged['winner'][0]:
            failures.append('the exploitation lane changed the winner discovery limit')
        checks += 1
        if assignments['winner']['current'] != 512 \
                or assignments['winner']['target'] < FOCUS_EXPLOIT_FLOOR:
            failures.append('the winner target did not report its validation depth')
        checks += 1
        ready = interleave_ready(out, {('winner', 'validation'): 512}, set(),
                                 focus_ids={'winner'})
        ordinals = [seed[2] for seed in ready if seed[0] == 'winner' and seed[1] == 'validation']
        if not ordinals or min(ordinals) < 512 or max(ordinals) <= 512:
            failures.append('the ready list did not offer validation ordinals past 512')
        checks += 1
        if max(ordinals) != out['winner'][1]-1:
            failures.append('the ready ordinals did not reach the raised limit')
        checks += 1
    print(f'  saved 512-run winner: {checks} checks passed', flush=True)
    return checks


def check_new_64(failures):
    """A fresh 64-run mutation keeps its discovery bank and earns validation depth past 64."""
    checks = 0
    mutant = evidence('mutant', runs=64, wins=40, mean=4.5)
    out, assignments = focused_exploitation([mutant], {'mutant': (24, 64)}, 19, 48)
    if assignments['mutant']['granted'] <= 0:
        failures.append('a strong 64-run mutation was not granted new focused work')
    checks += 1
    if out['mutant'][1] <= 64:
        failures.append('the mutation validation limit did not rise past the 64-run screen')
    checks += 1
    if out['mutant'][0] != 24:
        failures.append('the exploitation lane changed the mutation discovery limit')
    checks += 1
    if assignments['mutant']['current'] != 64 \
            or assignments['mutant']['target'] < FOCUS_EXPLOIT_FLOOR:
        failures.append('the mutation target did not report its validation depth')
    checks += 1
    ready = interleave_ready(out, {('mutant', 'discovery'): 24, ('mutant', 'validation'): 64},
                             set(), focus_ids={'mutant'})
    ordinals = [seed[2] for seed in ready if seed[1] == 'validation']
    if not ordinals or min(ordinals) != 64 or max(ordinals) <= 64:
        failures.append('the ready list did not offer validation ordinals past 64')
    checks += 1
    if any(seed[1] == 'discovery' for seed in ready):
        failures.append('the exploitation lane reopened the completed discovery bank')
    checks += 1
    print(f'  fresh 64-run mutation: {checks} checks passed', flush=True)
    return checks


def check_focus_only(failures):
    """Only the focused encounter is touched, and its ready list offers only that fight."""
    checks = 0
    here = evidence('here', encounter=19, runs=256, wins=220, mean=9.0)
    other = evidence('other', encounter=11, runs=512, wins=470, mean=40.0)
    limits = {'here': (0, 256), 'other': (0, 512)}
    out, assignments = focused_exploitation([here, other], limits, 19, 48)
    if 'other' in assignments or out['other'] != limits['other']:
        failures.append('the exploitation lane touched a non-focused encounter')
    checks += 1
    if out['here'][1] <= limits['here'][1]:
        failures.append('the focused encounter did not gain work')
    checks += 1
    ready = interleave_ready(out, {}, {('other', 'validation'): 512}, focus_ids={'here'})
    if not ready or any(seed[0] != 'here' for seed in ready):
        failures.append('the focused ready list offered work from another fight')
    checks += 1
    mixed = interleave_ready(out, {}, set())
    if not any(seed[0] == 'other' for seed in mixed):
        failures.append('clearing the focus did not restore the mixed population')
    checks += 1
    cleared, none = focused_exploitation([here, other], limits, None, 48)
    if none or cleared != limits:
        failures.append('the exploitation lane was not inert without a focus')
    checks += 1
    print(f'  focus isolation: {checks} checks passed', flush=True)
    return checks


def check_weak_candidate(failures):
    """A weak candidate never sees the exploitation targets (the floor alone is 1024)."""
    checks = 0
    cases = [
        evidence('no-bank', runs=8, wins=0, mean=None, comparable=False),
        evidence('partial', runs=16, wins=8, mean=2.0, comparable=False),
        evidence('no-win', runs=64, wins=0, mean=0.0, comparable=True, earned_max=1000),
        evidence('lucky-max', runs=64, wins=50, mean=1000.0, comparable=False, earned_max=1000),
    ]
    limits = {record['candidate']: (8, 0) for record in cases}
    for record in cases:
        if focused_exploit_target(record, 19) is not None:
            failures.append(f'{record["candidate"]} was handed an exploitation target')
        checks += 1
    out, assignments = focused_exploitation(cases, limits, 19, 48)
    if assignments:
        failures.append('weak candidates received exploitation grants')
    checks += 1
    for record in cases:
        if out[record['candidate']] != limits[record['candidate']]:
            failures.append(f'{record["candidate"]} had its staged limit changed')
        checks += 1
    print(f'  weak candidate screening: {checks} checks passed', flush=True)
    return checks


def check_paired_grants(failures):
    """A fine-tune point and its frozen parent are banked and granted together."""
    checks = 0
    child = evidence('child', runs=64, wins=54, mean=6.0)
    parent = evidence('parent', runs=64, wins=56, mean=6.0)
    out, assignments = focused_exploitation(
        [child, parent], {'child': (0, 64), 'parent': (0, 64)}, 19, 48,
        groups=[frozenset({'child', 'parent'})])
    if out['child'][1] != out['parent'][1] or out['child'][1] <= 64:
        failures.append('the tested point and its frozen parent were banked differently')
    checks += 1
    if not (assignments['child'].get('paired') and assignments['parent'].get('paired')):
        failures.append('the paired assignment was not marked as such')
    checks += 1
    if assignments['child']['granted'] != assignments['parent']['granted'] \
            or assignments['child']['granted'] <= 0:
        failures.append('the pair was granted different amounts')
    checks += 1
    weak = evidence('weak', runs=8, wins=0, comparable=False)
    out2, assignments2 = focused_exploitation(
        [child, weak], {'child': (0, 64), 'weak': (8, 0)}, 19, 48,
        groups=[frozenset({'child', 'weak'})])
    if 'weak' in assignments2 or out2['weak'] != (8, 0):
        failures.append('an ineligible group partner was dragged into the pair')
    checks += 1
    if out2['child'][1] <= 64:
        failures.append('the eligible half of a mixed group lost its grant')
    checks += 1
    print(f'  paired point/parent grants: {checks} checks passed', flush=True)
    return checks


def check_budget_and_fairness(failures):
    """One pass's exploitation is a minority of the reservoir, so novel mutations keep their share."""
    checks = 0
    reservoir = 24*learner.RESERVOIR_SEEDS_PER_WORKER
    budget = reservoir//FOCUS_EXPLOIT_SHARE
    summaries = [evidence(f'w{index}', runs=200, wins=190, mean=20.0) for index in range(10)]
    limits = {summary['candidate']: (0, 200) for summary in summaries}
    out, assignments = focused_exploitation(summaries, limits, 19, budget)
    granted = sum(entry['granted'] for entry in assignments.values())
    if granted > budget:
        failures.append(f'the lane granted {granted} seeds, past its {budget}-seed budget')
    checks += 1
    if budget >= reservoir:
        failures.append('the exploitation budget was not a minority of the reservoir')
    checks += 1
    extra = sum(out[summary['candidate']][1]-200 for summary in summaries)
    if extra != granted:
        failures.append('the raised limits did not equal the granted seeds')
    checks += 1
    if reservoir-extra <= 0:
        failures.append('exploitation could hold the whole reservoir and starve novel mutations')
    checks += 1
    advanced = sum(1 for entry in assignments.values() if entry['granted'] > 0)
    if advanced < 2 or advanced > FOCUS_EXPLOIT_SLOTS:
        failures.append(f'{advanced} builds advanced at once; expected 2..{FOCUS_EXPLOIT_SLOTS}')
    checks += 1
    untouched = [cid for cid in out if cid not in assignments]
    if not untouched or any(out[cid] != (0, 200) for cid in untouched):
        failures.append('builds outside the pass kept their staged limits')
    checks += 1
    if any(entry['granted'] > FOCUS_EXPLOIT_STEP for entry in assignments.values()):
        failures.append('a single pass granted more than the bounded step')
    checks += 1
    out0, assignments0 = focused_exploitation(summaries, limits, 19, 0)
    if assignments0 or out0 != limits:
        failures.append('a zero budget still authorised exploitation')
    checks += 1
    print(f'  budget/fairness: {checks} checks passed', flush=True)
    return checks


def check_cap_and_stopping(failures):
    """Every target is capped, and a build at its target stops gaining work."""
    checks = 0
    done = evidence('done', runs=FOCUS_EXPLOIT_FLOOR, wins=900, mean=0.0)
    decision = focused_exploit_target(done, 19, best_mean=0.0, band_size=0)
    if decision is None or decision['tier'] != 'capped' \
            or decision['target'] != FOCUS_EXPLOIT_FLOOR:
        failures.append('a certified contender at its floor was not stopped at the floor')
    checks += 1
    rare = evidence('rare', runs=FOCUS_EXPLOIT_CAP, wins=200, mean=3.0)
    decision = focused_exploit_target(rare, 19, best_mean=3.0, band_size=3)
    if decision is None or decision['tier'] != 'capped' or decision['target'] > FOCUS_EXPLOIT_CAP:
        failures.append('a competitive build past the cap was not stopped at the cap')
    checks += 1
    for runs in (FOCUS_EXPLOIT_FLOOR, FOCUS_EXPLOIT_COMPETITIVE, FOCUS_EXPLOIT_CAP,
                 FOCUS_EXPLOIT_CAP+1000):
        decision = focused_exploit_target(evidence('t', runs=runs, wins=int(runs*.9), mean=9.0),
                                          19, best_mean=9.0, band_size=2)
        if decision is None or decision['target'] > FOCUS_EXPLOIT_CAP:
            failures.append(f'a {runs}-run build got a target past the cap')
        checks += 1
    out, assignments = focused_exploitation([done], {'done': (0, FOCUS_EXPLOIT_FLOOR)}, 19, 48)
    if assignments['done']['granted'] != 0 or out['done'][1] != FOCUS_EXPLOIT_FLOOR:
        failures.append('a build at its target was still granted work')
    checks += 1
    built = evidence('built', runs=512, wins=440, mean=5.0)
    _out2, assignments2 = focused_exploitation([built], {'built': (0, 512)}, 19, 48)
    if assignments2['built']['granted'] > FOCUS_EXPLOIT_STEP \
            or assignments2['built']['target'] > FOCUS_EXPLOIT_CAP:
        failures.append('a single pass raised a build past its bounded step or cap')
    checks += 1
    print(f'  cap and stopping: {checks} checks passed', flush=True)
    return checks


def check_synthetic_store_planner(failures):
    """The whole path from a synthetic store: means from the validation bank, real ready ordinals."""
    checks = 0
    store = open_store()
    base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
    other_base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=11))
    try:
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', scope(base))
            winner = store.add(variant(base, 0, 7), 'Saved enc19 winner', 'supplied', stats(base))
            rival = store.add(variant(base, 0, 14), 'Rival enc19 probe', 'probe', stats(base))
            rival2 = store.add(variant(base, 0, 21), 'Second rival enc19 probe', 'probe',
                               stats(base))
            mutant = store.add(variant(base, 0, 28), 'New enc19 mutation', 'mutation', stats(base))
            weak = store.add(variant(base, 0, 35), 'Weak enc19 child', 'mutation', stats(base))
            outsider = store.add(variant(other_base, 0, 7), 'Other enc11 probe', 'probe',
                                 stats(other_base))
            # The winner's lifetime maximum comes from ONE lucky discovery win (40 chests); its
            # validation bank pays 5 chests per win and takes 72 losses, so its mean is 2200/512.
            for ordinal in range(24):
                store.record(winner, 'discovery', ordinal,
                             outcome(ordinal, chests=40, verdict=1 if ordinal == 0 else 2))
            for ordinal in range(512):
                store.record(winner, 'validation', ordinal,
                             outcome(ordinal, chests=5, verdict=1 if ordinal < 440 else 2))
            for cid in (rival, rival2):
                for ordinal in range(128):
                    store.record(cid, 'validation', ordinal,
                                 outcome(ordinal, chests=8, verdict=1 if ordinal < 120 else 2))
            for ordinal in range(24):
                store.record(mutant, 'discovery', ordinal,
                             outcome(ordinal, chests=6, verdict=1 if ordinal == 0 else 2))
            for ordinal in range(64):
                store.record(mutant, 'validation', ordinal,
                             outcome(ordinal, chests=6, verdict=1 if ordinal < 48 else 2))
            for ordinal in range(8):
                store.record(weak, 'discovery', ordinal, outcome(ordinal, chests=0, verdict=2))
            for ordinal in range(128):
                store.record(outsider, 'validation', ordinal,
                             outcome(ordinal, chests=7, verdict=1 if ordinal < 110 else 2))
        store.flush()
        aggregates = read_aggregates(store)
        keys = store.candidate_keys()
        records = population_records(store, keys, aggregates)
        by_id = {record['candidate']: record for record in records}
        summaries, _groups = focused_exploit_inputs(store, records, aggregates, 19)
        summary = {entry['candidate']: entry for entry in summaries}
        if not summary[winner]['comparable'] or summary[winner]['validationRuns'] != 512:
            failures.append('a 512-run saved winner was not read as a comparable validation bank')
        checks += 1
        if abs(summary[winner]['earnedMean'] - 2200/512) > 1e-9:
            failures.append('the winner mean was not chestSum/chestCount over resolved runs')
        checks += 1
        if 'earnedMax' in summary[winner]:
            failures.append('the lane summary still carries the lifetime maximum')
        checks += 1
        if by_id[winner]['earnedMax'] != 40:
            failures.append('the fixture did not give the winner a lucky lifetime maximum')
        checks += 1
        if not summary[rival]['comparable'] or summary[rival]['earnedMean'] != 7.5:
            failures.append('the rival validation mean was not read')
        checks += 1
        if summary[mutant]['validationRuns'] != 64 or summary[mutant]['earnedMean'] != 4.5:
            failures.append('the 64-run mutation bank was not summarised')
        checks += 1
        if summary[weak]['comparable'] or summary[weak]['earnedMean'] is not None:
            failures.append('a discovery-only build was read as comparable validation depth')
        checks += 1
        limits = {}
        for cid in keys:
            discovery = aggregates.get(f'aggregate:{cid}:discovery') or {}
            record = by_id[cid]
            is_probe = cid in (rival, rival2, outsider)
            has_parent = cid in (mutant, weak)
            limits[cid] = optimizer.staged_limits(
                int(discovery.get('n', 0) or 0), int(discovery.get('wins', 0) or 0),
                None, False, None, record['earnedMax'], record['potentialMax'],
                record['progress'], is_probe, 128, has_parent)
        out, assignments = focused_exploitation(summaries, limits, 19, 192)
        if assignments.get(winner, {}).get('granted', 0) <= 0:
            failures.append('the planner did not grant the saved winner new work')
        checks += 1
        if out[winner][1] <= max(512, limits[winner][1]):
            failures.append('the winner validation limit did not rise past its 512-run bank')
        checks += 1
        if assignments[winner]['current'] != 512 or out[winner][0] != limits[winner][0]:
            failures.append('the winner target mixed discovery depth into its validation report')
        checks += 1
        if out[mutant][1] <= max(64, limits[mutant][1]):
            failures.append('the fresh mutation validation limit did not rise past 64')
        checks += 1
        if weak in assignments or out[weak] != limits[weak]:
            failures.append('the weak child was handed exploitation depth')
        checks += 1
        if outsider in assignments or out[outsider] != limits[outsider]:
            failures.append('the non-focused encounter was changed')
        checks += 1
        if not assignments[rival]['competitive'] or assignments[winner]['competitive']:
            failures.append('the competitive band ignored the validation means')
        checks += 1
        ordinals = {(cid, phase): store.next_ordinal(cid, phase)
                    for cid in keys for phase in ('discovery', 'validation')}
        focus_ids = optimizer.encounter_scope(store.candidate_encounters(), 19)
        ready = interleave_ready(out, ordinals, set(), focus_ids=focus_ids)
        winner_ordinals = [seed[2] for seed in ready
                           if seed[0] == winner and seed[1] == 'validation']
        if not winner_ordinals or min(winner_ordinals) < 512 or max(winner_ordinals) <= 512:
            failures.append('the planner ready list did not offer ordinals past 512')
        checks += 1
        if any(seed[0] == outsider for seed in ready):
            failures.append('the planner ready list offered non-focused work')
        checks += 1
        if any(seed[0] == weak for seed in ready):
            failures.append('the planner ready list offered the weak child work')
        checks += 1
        print(f'  synthetic store/planner: {checks} checks passed', flush=True)
    finally:
        close_store(store)
    return checks


def record_evidence(cid, runs=512, wins=0, mean=0.0, encounter=19, value=900, comparable=True):
    """A record-holder summary: a low mean under an exceptional lifetime record."""
    return dict(candidate=cid, encounterId=encounter, validationRuns=runs, wins=wins,
                earnedMean=mean, winInterval=optimizer.wilson(wins, runs),
                comparable=comparable, recordValue=value)


def check_record_repair(failures):
    """A low-mean Highest Earned holder stays eligible and gets real work past 512, bounded."""
    checks = 0
    host = record_evidence('rec')
    # The mean lane refuses this build: its validation bank holds no converted win, which is exactly
    # the "low current mean" case the record lane exists for.
    if focused_exploit_target(host, 19) is not None:
        failures.append('the fixture did not reproduce a record build the mean lane refuses')
    checks += 1
    out, assignments = focused_exploitation([host], {'rec': (0, 512)}, 19, 48, record=host,
                                            record_budget=48)
    entry = assignments.get('rec')
    if not entry or not entry.get('record') or entry['granted'] <= 0:
        failures.append('the Highest Earned holder was not granted a repeat bank')
    checks += 1
    if out['rec'][1] <= 512 or out['rec'][1] > FOCUS_RECORD_CAP:
        failures.append('the repeat bank did not rise past 512 within its cap')
    checks += 1
    if not FOCUS_RECORD_FLOOR <= entry['target'] <= FOCUS_RECORD_CAP:
        failures.append('the repeat target sat outside its floor/cap')
    checks += 1
    ready = interleave_ready(out, {('rec', 'validation'): 512}, set(), focus_ids={'rec'})
    ordinals = [seed[2] for seed in ready if seed[0] == 'rec' and seed[1] == 'validation']
    if not ordinals or min(ordinals) < 512 or max(ordinals) <= 512:
        failures.append('the record repair ready list offered no validation ordinals past 512')
    checks += 1
    if max(ordinals) != out['rec'][1]-1:
        failures.append('the record repair ready ordinals did not reach the raised limit')
    checks += 1
    # Evidence warrants the size: a certified holder needs only the floor, an uncertain one the
    # larger bank; a mean-lane grant is never lowered and the hard cap still applies.
    steady = record_evidence('steady', runs=512, wins=470, mean=3.0, value=300)
    if not float(steady['winInterval'][0]) >= optimizer.FOCUS_EXPLOIT_CERTIFIED_LOWER:
        failures.append('the certified record fixture is not certified')
    checks += 1
    if focused_record_target(steady, 19)['target'] != FOCUS_RECORD_FLOOR:
        failures.append('a certified record holder did not earn the floor')
    checks += 1
    if focused_record_target(host, 19)['target'] != FOCUS_EXPLOIT_COMPETITIVE:
        failures.append('an uncertain record holder did not earn the larger bank')
    checks += 1
    raised = focused_record_target(host, 19, dict(target=FOCUS_EXPLOIT_CAP))
    if raised['target'] != FOCUS_RECORD_CAP:
        failures.append('the record lane lowered or ignored a larger mean-lane target')
    checks += 1
    past = focused_record_target(record_evidence('past', runs=FOCUS_RECORD_CAP+1000), 19)
    if past['tier'] != 'record-capped' or past['target'] > FOCUS_RECORD_CAP:
        failures.append('a record holder past the cap was not clamped and stopped')
    checks += 1
    print(f'  record repair eligibility: {checks} checks passed', flush=True)
    return checks


def check_record_paired_and_budget(failures):
    """A record parent and its variant share one bank, and the lane is a ring-fenced minority."""
    checks = 0
    host = record_evidence('rec')
    child = record_evidence('child', runs=512, wins=200, mean=3.0, value=0)
    out, assignments = focused_exploitation(
        [host, child], {'rec': (0, 512), 'child': (0, 512)}, 19, 48,
        groups=[frozenset({'rec', 'child'})], record=host, record_budget=48)
    if out['rec'][1] != out['child'][1] or out['rec'][1] <= 512:
        failures.append('the record parent and its variant were not banked to one raised target')
    checks += 1
    if not (assignments['rec'].get('paired') and assignments['rec'].get('record')):
        failures.append('the record parent/variant bank was not reported as paired')
    checks += 1
    reservoir = 24*learner.RESERVOIR_SEEDS_PER_WORKER
    record_budget = reservoir//FOCUS_RECORD_SHARE
    if record_budget >= reservoir:
        failures.append('the record repair budget was not a minority of the reservoir')
    checks += 1
    leader = evidence('leader', runs=512, wins=470, mean=20.0)
    limits = {'leader': (0, 512), 'rec': (0, 512)}
    without, _none = focused_exploitation([leader, host], dict(limits), 19, record_budget)
    with_record, assignments2 = focused_exploitation(
        [leader, host], dict(limits), 19, record_budget, record=host, record_budget=record_budget)
    if with_record['leader'][1] != without['leader'][1]:
        failures.append('the record lane changed the mean-leader lane grant')
    checks += 1
    if without['rec'][1] != 512:
        failures.append('the record build was banked without a record')
    checks += 1
    record_share = with_record['rec'][1]-512
    mean_share = with_record['leader'][1]-512
    if record_share <= 0 or record_share > record_budget:
        failures.append('the record lane granted outside its ring-fenced budget')
    checks += 1
    if mean_share + record_share >= reservoir:
        failures.append('the record lane could starve novel exploration')
    checks += 1
    print(f'  record paired bank and budget: {checks} checks passed', flush=True)
    return checks


def check_record_isolation_and_caps(failures):
    """Weak non-records, other encounters no focus: untouched. The bank climbs to its target and stops."""
    checks = 0
    host = record_evidence('rec')
    weak = evidence('weak', runs=8, wins=0, mean=None, comparable=False)
    out, assignments = focused_exploitation(
        [host, weak], {'rec': (0, 512), 'weak': (8, 0)}, 19, 48, record=host, record_budget=48)
    if 'weak' in assignments or out['weak'] != (8, 0):
        failures.append('a weak non-record build was handed the record lane')
    checks += 1
    other = record_evidence('other', runs=512, wins=0, encounter=11)
    out2, assignments2 = focused_exploitation(
        [host, other], {'rec': (0, 512), 'other': (0, 512)}, 19, 48, record=other,
        record_budget=48)
    if assignments2 or out2 != {'rec': (0, 512), 'other': (0, 512)}:
        failures.append('the record lane touched a build outside the focused encounter')
    checks += 1
    out3, assignments3 = focused_exploitation([host], {'rec': (0, 512)}, None, 48, record=host,
                                              record_budget=48)
    if assignments3 or out3 != {'rec': (0, 512)}:
        failures.append('the record lane was not inert without a focus')
    checks += 1
    target = focused_record_target(host, 19)['target']
    limit, last = 512, None
    for _ in range(400):
        out4, last = focused_exploitation([host], {'rec': (0, limit)}, 19, 4096, record=host,
                                          record_budget=4096)
        limit = out4['rec'][1]
        if limit > FOCUS_RECORD_CAP:
            failures.append('the repeat bank grew past the cap')
            break
        if last['rec']['tier'] == 'record-capped':
            break
    if last is None or limit != target or target > FOCUS_RECORD_CAP:
        failures.append('the repeat bank did not stop at its capped target')
    checks += 1
    if last['rec']['tier'] != 'record-capped' or last['rec']['granted'] != 0:
        failures.append('a record holder at its target kept gaining work')
    checks += 1
    print(f'  record isolation and caps: {checks} checks passed', flush=True)
    return checks


def check_record_repair_store(failures):
    """The holder is read from canonical storage; a pruned holder is reported and its replay kept."""
    checks = 0
    store = open_store()
    base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
    try:
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', scope(base))
            record = store.add(variant(base, 0, 7), 'Record holder', 'mutation', stats(base))
            weak = store.add(variant(base, 0, 14), 'Weak child', 'mutation', stats(base))
            # One lucky discovery win (99 chests) sets the record; the 64-run validation bank then
            # loses every seed, so the mean lane refuses the build but the record lane must not.
            store.record(record, 'discovery', 0, outcome(0, chests=99, verdict=1))
            store.record(record, 'discovery', 1, outcome(1, chests=0, verdict=2))
            for ordinal in range(VALIDATION_RUNS):
                store.record(record, 'validation', ordinal, outcome(ordinal, chests=0, verdict=2))
            for ordinal in range(8):
                store.record(weak, 'discovery', ordinal, outcome(ordinal, chests=0, verdict=2))
        store.flush()
        aggregates = read_aggregates(store)
        holder, report = focused_record_holder(store, 19, aggregates)
        if report is not None or not holder or holder['candidate'] != record:
            failures.append('the canonical Highest Earned holder was not identified')
        checks += 1
        if holder['recordValue'] != 99 or holder['source'] != 'mutation':
            failures.append('the holder report carried the wrong record identity')
        checks += 1
        summary = dict(holder, comparable=True)
        if focused_exploit_target(summary, 19) is not None:
            failures.append('the store fixture did not reproduce a build the mean lane refuses')
        checks += 1
        frozen = json.loads(json.dumps(store.get('recordHolders')['earned:19:0']))
        with store.db:
            store.db.execute('DELETE FROM candidate WHERE id=?', (record,))
            store.db.execute('DELETE FROM candidate_meta WHERE id=?', (record,))
        pruned, pruned_report = focused_record_holder(store, 19, aggregates)
        if pruned is not None or (pruned_report or {}).get('status') != 'pruned':
            failures.append('a pruned record holder was not reported as pruned')
        checks += 1
        if 'frozen replay' not in (pruned_report or {}).get('reason', ''):
            failures.append('the pruned report did not state the frozen replay is kept')
        checks += 1
        if store.get('recordHolders')['earned:19:0'] != frozen:
            failures.append('pruning the candidate mutated the frozen record holder')
        checks += 1
    finally:
        close_store(store)
    print(f'  record holder from canonical store: {checks} checks passed', flush=True)
    return checks


def main():
    failures = []
    checks = 0
    for checker in (check_target_choice, check_saved_512, check_new_64, check_focus_only,
                    check_weak_candidate, check_paired_grants, check_budget_and_fairness,
                    check_cap_and_stopping, check_record_repair, check_record_paired_and_budget,
                    check_record_isolation_and_caps):
        checks += checker(failures)
    checks += check_synthetic_store_planner(failures)
    checks += check_record_repair_store(failures)
    if failures:
        print(f'\nfocused exploitation depth: {len(failures)} FAILURE(S)')
        for failure in failures:
            print(f'  - {failure}')
        raise SystemExit(1)
    print(f'\nfocused exploitation depth: {checks} checks passed')


if __name__ == '__main__':
    main()
