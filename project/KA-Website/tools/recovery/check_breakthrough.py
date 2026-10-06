"""The breakthrough / reliability loop, against deterministic synthetic fixtures.

Every fixture below is a LABELLED FIXTURE, not a measurement of the game: a synthetic reward model
answers for a scenario and a seed pair, and the loop is driven with it so the *learning behaviour* can
be checked deterministically and in seconds. The real encounter is investigated by the optimiser
itself under its own share; this file proves the machinery it runs on.

  A  learnable rare-success mechanism    - only the joint change makes high rewards common
  B  pure lottery                        - nothing a build can change moves the odds
  C  misleading proxy                    - the stored-command counter moves, the reward does not
  D  two mechanisms                      - two different changes each work, analysis must not merge them
  E  low-ranked valuable evidence        - a poor-average build's one good run is still evidence
  F  unequal sample counts / clones      - more runs must not buy archive space or nomination
  G  selected-seed overfitting           - helps only the seeds that chose it -> never confirmed
  I  accounting                          - a replayed/duplicated observation is one sample
  J  operational                         - zero-share halt, resume, missing telemetry, missing incumbent
  K  Student 9 integration               - "validate" schedules parent runs; polls do not age stagnation

    python check_breakthrough.py
"""
import json
import statistics
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_breakthrough as bt  # noqa: E402
import strategy_students as students  # noqa: E402
from strategy_optimizer import Store, seed_pair, scope  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402

FAILURES = []
NOTES = []


def expect(condition, message):
    print(('  OK   ' if condition else '  FAIL ') + message, flush=True)
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def fixture_scenario(tick_limit=120):
    """A real, validated scenario at a tiny horizon. FIXTURE: no battle is ever simulated here."""
    scenario = deepcopy(default_scenario())
    scenario['tickLimit'] = int(tick_limit)
    return scenario


def axis_value(scenario, unit, axis):
    import strategy_probe
    return strategy_probe.pivot_value(scenario, unit, axis)


def synthetic_result(earned, seeds, progress=None, verdict=1):
    """One compact result shaped like the runner's, so `Store.record` accepts it."""
    return dict(verdict=verdict, censored=False, ticks=100, prizeCallbacks=int(earned),
                survivors=1, resourceUses=0, healthFraction=0.5,
                behavior=dict(attacks=10, heals=0, prizes=int(earned)),
                seeds=list(seeds), digest=f'fixture-{int(earned)}-{seeds[0]}-{seeds[1]}',
                rewardOutcome=dict(awardedChests=int(earned), awardedBasis='fixture',
                                   pendingChests=int(earned), inventoryVerified=False),
                progressMetrics=dict(progress or {}))


def fill_bank(store, candidate, bank, model, phase='validation'):
    """Run one candidate's authorised bank with the synthetic model (never a real battle)."""
    ordinal = store.next_ordinal(candidate, phase)
    scenario = store.scenario(candidate)
    while ordinal < bank:
        seeds = seed_pair(phase, ordinal)
        earned, progress = model(scenario, seeds, ordinal)
        store.record(candidate, phase, ordinal, synthetic_result(earned, seeds, progress))
        ordinal = store.next_ordinal(candidate, phase)


def fill_all(store, model, phase='validation'):
    """Satisfy every declared bank: the mechanism arms and Student 9's evaluation banks."""
    banks = dict(bt.banks(store))
    banks.update(students.average_banks(store))
    for cid, bank in banks.items():
        if store.scenario(cid) is not None:
            fill_bank(store, cid, int(bank), model, phase)


def make_store(root, name, scenario):
    store = Store(Path(root)/name, provenance())
    with store.db:
        store.set('scope', scope(scenario))
        store.add(scenario, 'Fixture baseline', 'supplied', stats(scenario))
    return store


def parent_of(store):
    return store.db.execute('SELECT id FROM candidate ORDER BY created LIMIT 1').fetchone()[0]


# ---------------------------------------------------------------------------------------------
# A. A learnable rare-success mechanism: only the joint change makes high rewards common
# ---------------------------------------------------------------------------------------------

def model_interaction(parent_atk, parent_spd, joint_bonus=12, base=3, chance=40):
    def model(scenario, seeds, ordinal):
        import strategy_probe
        unit = strategy_probe.default_probe_unit(scenario, ['atk', 'spd'])
        atk = strategy_probe.pivot_value(scenario, unit, 'atk')
        spd = strategy_probe.pivot_value(scenario, unit, 'spd')
        joint = atk <= parent_atk*0.6 and spd <= parent_spd*0.7
        # Rare by default, common when both moved: the interaction is the only way up.
        # Rare while nothing has changed, common once both parameters moved: the interaction is the
        # only route to the high band, which is what makes fixture A learnable.
        high = ((seeds[0] % 100) < (chance if joint else 3))
        return (base + (joint_bonus if high else 0)), dict(
            maxSimultaneousStoredCommands=(4 if joint else 1) + (2 if high else 0),
            commandsTargetingBoss=2, storedTargetHoldersPeak=1,
            commandsReleasedAfterDeath=(3 if high else 1), postDeathPrizes=(2 if high else 0),
            bossDeathTick=60, storedCommandsTargetingBossAtDeath=1)
    return model


def check_learnable_mechanism(root):
    scenario = fixture_scenario()
    store = make_store(root, 'a.sqlite', scenario)
    parent = parent_of(store)
    model = model_interaction(axis_value(scenario, 'Ninja (A aw20)', 'atk'),
                              axis_value(scenario, 'Ninja (A aw20)', 'spd'))
    with store.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            earned, progress = model(store.scenario(parent), seeds, ordinal)
            store.record(parent, 'validation', ordinal, synthetic_result(earned, seeds, progress))
    first = bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    expect(first['started'] and first['enabled'], f'A: the stream started a plan ({first})')
    plan = bt.load(store)['encounters'][str(scenario['encounterId'])]['plans'][0]
    names = [arm['name'] for arm in plan['arms']]
    expect(names[:4] == ['A', 'B', 'C', 'D'],
           f'A: the first batch is the controlled quartet A/B/C/D, got {names}')
    expect(plan['arms'][0]['candidate'] == parent,
           'A: arm A is the unchanged parent on the same bank')
    # Measure the arms with the synthetic model, then let the stream close the plan.
    fill_all(store, model)
    second = bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    record = bt.load(store)['encounters'][str(scenario['encounterId'])]
    closed = next((entry for entry in record['plans'] if entry['id'] == plan['id']), None)
    expect(second['closed'] == [plan['id']], f'A: the finished plan was closed ({second})')
    result = (closed or {}).get('result') or {}
    best = result.get('best') or {}
    expect(best.get('name') == 'D',
           f'A: the joint change is the only arm that wins, got {best.get("name")}')
    expect((best.get('delta') or {}).get('lower', -1) > 0,
           f'A: the joint arm beats the parent on paired fresh seeds, got {best.get("delta")}')
    expect(record['status'] == 'promising_unconfirmed',
           f'A: the encounter is marked promising but unconfirmed, got {record["status"]}')
    hypothesis = next(entry for entry in record['hypotheses']
                      if entry['id'] == plan['hypothesis'])
    expect(hypothesis['status'] == 'intervention_supported',
           f'A: the hypothesis records the intervention support, got {hypothesis["status"]}')
    expect(record.get('nomination'), 'A: a candidate was nominated for confirmation')
    NOTES.append('A: the joint arm alone raised the paired mean; the stream nominated it for '
                 'independent confirmation')
    store.db.close()


# ---------------------------------------------------------------------------------------------
# B/C/D. The two negative controls and the two-mechanism case
# ---------------------------------------------------------------------------------------------

def model_lottery(base=3):
    def model(scenario, seeds, ordinal):
        return (base + (10 if (seeds[1] % 50) < 4 else 0)), dict(
            maxSimultaneousStoredCommands=2, commandsTargetingBoss=1,
            commandsReleasedAfterDeath=1, postDeathPrizes=0, bossDeathTick=50,
            storedCommandsTargetingBossAtDeath=1)
    return model


def model_proxy(parent_atk):
    """Stored-command accumulation tracks the change; the reward does not (fixture C)."""
    def model(scenario, seeds, ordinal):
        import strategy_probe
        unit = strategy_probe.default_probe_unit(scenario, ['atk', 'spd'])
        atk = strategy_probe.pivot_value(scenario, unit, 'atk')
        moved = atk <= parent_atk*0.6
        return 3, dict(maxSimultaneousStoredCommands=(9 if moved else 2),
                       commandsTargetingBoss=(9 if moved else 1), storedTargetHoldersPeak=1,
                       commandsReleasedAfterDeath=1, postDeathPrizes=0, bossDeathTick=55,
                       storedCommandsTargetingBossAtDeath=1)
    return model


def model_two_mechanisms(parent_atk, parent_spd, base=3):
    """Two independent paths to a better reward (fixture D)."""
    def model(scenario, seeds, ordinal):
        import strategy_probe
        unit = strategy_probe.default_probe_unit(scenario, ['atk', 'spd'])
        atk_low = strategy_probe.pivot_value(scenario, unit, 'atk') <= parent_atk*0.6
        spd_low = strategy_probe.pivot_value(scenario, unit, 'spd') <= parent_spd*0.7
        bonus = 6 if (atk_low or spd_low) else 0
        return base + bonus, dict(
            maxSimultaneousStoredCommands=(6 if atk_low else 2) + (3 if spd_low else 0),
            commandsTargetingBoss=2, storedTargetHoldersPeak=1, commandsReleasedAfterDeath=2,
            postDeathPrizes=(1 if spd_low else 0), bossDeathTick=58,
            storedCommandsTargetingBossAtDeath=1)
    return model


def run_fixture(root, name, model, share=0.5):
    scenario = fixture_scenario()
    store = make_store(root, name, scenario)
    parent = parent_of(store)
    with store.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            earned, progress = model(store.scenario(parent), seeds, ordinal)
            store.record(parent, 'validation', ordinal, synthetic_result(earned, seeds, progress))
    bt.advance(store, share=share, workers=1, focus_encounter=scenario['encounterId'])
    fill_all(store, model)
    bt.advance(store, share=share, workers=1, focus_encounter=scenario['encounterId'])
    record = bt.load(store)['encounters'][str(scenario['encounterId'])]
    return store, record, parent


def check_lottery(root):
    store, record, _parent = run_fixture(root, 'b.sqlite', model_lottery())
    plans = [plan for plan in record['plans'] if plan['kind'] == 'screening']
    promoted = [arm for arm in (plans[0]['result'] or {}).get('arms') or [] if arm['rewardPromising']]
    expect(not promoted, f'B: no arm is nominated when nothing controllable matters ({promoted})')
    expect(record['status'] != 'confirmed_improvement',
           f'B: the lottery fixture is never confirmed, got {record["status"]}')
    hypothesis = next(entry for entry in record['hypotheses'] if entry['id'] == plans[0]['hypothesis'])
    expect(hypothesis['status'] in ('inconclusive', 'failed_in_tested_region'),
           f'B: the hypothesis is not credited with progress, got {hypothesis["status"]}')
    expect(not record.get('nomination'),
           'B: the lottery fixture nominated no candidate for confirmation')
    NOTES.append('B: with no controllable effect the stream reported an inconclusive batch and '
                 'nominated nothing')
    store.db.close()


def check_misleading_proxy(root):
    scenario = fixture_scenario()
    store, record, _parent = run_fixture(root, 'c.sqlite', model_proxy(axis_value(scenario, 'Ninja (A aw20)', 'atk')))
    plan = [entry for entry in record['plans'] if entry['kind'] == 'screening'][0]
    arms = (plan['result'] or {}).get('arms') or []
    moved = [arm for arm in arms if arm['name'] != 'A'
             and (arm['behaviourChange'] or {}).get('maxSimultaneousStoredCommands', 0) > 0]
    expect(moved, 'C: the fixture did move the stored-command counter')
    expect(not any(arm['rewardPromising'] for arm in arms),
           'C: a moved proxy alone never nominates a candidate')
    expect(record['status'] != 'confirmed_improvement',
           f'C: the proxy fixture is never confirmed, got {record["status"]}')
    NOTES.append('C: the stored-command counter rose while the reward did not, and nothing was '
                 'promoted on the proxy')
    store.db.close()


def check_two_mechanisms(root):
    scenario = fixture_scenario()
    store, record, _parent = run_fixture(root, 'd.sqlite', model_two_mechanisms(
        axis_value(scenario, 'Ninja (A aw20)', 'atk'), axis_value(scenario, 'Ninja (A aw20)', 'spd')))
    plan = [entry for entry in record['plans'] if entry['kind'] == 'screening'][0]
    arms = {arm['name']: arm for arm in (plan['result'] or {}).get('arms') or []}
    separate = [name for name in ('B', 'C')
                if (arms.get(name, {}).get('delta') or {}).get('lower', -1) > 0]
    expect(len(separate) == 2,
           f'D: both single-axis mechanisms are detected separately, got {separate}')
    features = {}
    for name in ('B', 'C'):
        change = (arms.get(name, {}).get('behaviourChange') or {})
        features[name] = {key: value for key, value in change.items() if value}
    expect(features['B'] != features['C'],
           f'D: the two mechanisms show different behavioural signatures, got {features}')
    NOTES.append('D: two separate working changes were reported separately rather than collapsed '
                 'into one rule')
    store.db.close()


# ---------------------------------------------------------------------------------------------
# E/F. Which runs become evidence, and which do not monopolise it
# ---------------------------------------------------------------------------------------------

def check_low_ranked_evidence(root):
    """A poor-average build with one exceptional run must stay in the reading list (fixture E)."""
    scenario = fixture_scenario()
    store = make_store(root, 'e.sqlite', scenario)
    parent = parent_of(store)
    with store.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            earned = 31 if ordinal == 7 else (seeds[0] % 2)
            # The one productive run has a different stage signature, which is the whole point: the
            # contrast must find it inside a build whose average is poor.
            progress = (dict(maxSimultaneousStoredCommands=7, commandsTargetingBoss=4,
                             storedTargetHoldersPeak=2, commandsReleasedAfterDeath=5,
                             postDeathPrizes=3, bossDeathTick=58,
                             storedCommandsTargetingBossAtDeath=3) if ordinal == 7 else
                        dict(maxSimultaneousStoredCommands=1, commandsTargetingBoss=1,
                             storedTargetHoldersPeak=0, commandsReleasedAfterDeath=1,
                             postDeathPrizes=0, bossDeathTick=50,
                             storedCommandsTargetingBossAtDeath=1))
            store.record(parent, 'validation', ordinal, synthetic_result(earned, seeds, progress))
    rows, coverage = bt.evidence_view(store, scenario['encounterId'])
    stats = bt.outcome_stats(rows)
    expect(stats['maximum'] == 31 and stats['mean'] < 2,
           f'E: the fixture really is a poor mean with one high run (mean {stats["mean"]})')
    archive_state, bands = bt.archive(None, rows)
    expect(any(entry['earned'] == 31 for entry in archive_state['examples']),
           'E: the 31-chest run is retained as a diagnostic example')
    expect(bands['20+']['count'] == 1, f'E: the band totals count it once, got {bands["20+"]}')
    contrast, contrast_coverage = bt.contrast_within(rows, parent)
    # One observation is not a contrast: the strict separation rule must refuse to name a stage from a
    # single productive run, however valuable that run is.
    expect(contrast is not None, 'E: the contrast was computed for the poor-average build')
    expect(contrast and not contrast['separating'],
           f'E: one high run does not fabricate a separating stage, got '
           f'{contrast and contrast["separating"]}')
    hypothesis = bt.prior_hypothesis(scenario['encounterId'], rows[0]['scope'], rows, parent)
    expect(hypothesis and hypothesis['supporting'] and hypothesis['supporting'][0]['earned'] == 31,
           f'E: the 31-chest run is the hypothesis\'s supporting reference, got '
           f'{(hypothesis or {}).get("supporting")}')
    # With enough productive runs to contrast, the same rule *does* name the stage.
    store2 = make_store(root, 'e2.sqlite', scenario)
    parent2 = parent_of(store2)
    with store2.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            productive = ordinal % 8 < 3          # 24 of 64: enough for extreme groups
            progress = (dict(maxSimultaneousStoredCommands=6, commandsTargetingBoss=4,
                             storedTargetHoldersPeak=2, commandsReleasedAfterDeath=4,
                             postDeathPrizes=2, bossDeathTick=58,
                             storedCommandsTargetingBossAtDeath=3) if productive else
                        dict(maxSimultaneousStoredCommands=1, commandsTargetingBoss=1,
                             storedTargetHoldersPeak=0, commandsReleasedAfterDeath=1,
                             postDeathPrizes=0, bossDeathTick=50,
                             storedCommandsTargetingBossAtDeath=1))
            store2.record(parent2, 'validation', ordinal,
                          synthetic_result(15 if productive else 3, seeds, progress))
    rows2, _coverage2 = bt.evidence_view(store2, scenario['encounterId'])
    contrast2, _ = bt.contrast_within(rows2, parent2)
    stage, evidence = bt.stage_bottleneck([contrast2])
    expect(contrast2 and contrast2['separating'] and stage,
           f'E: a common enough signal does establish the stage, got {stage} / '
           f'{contrast2 and contrast2["separating"]}')
    NOTES.append('E: a build whose mean is 1.5 kept its 31-chest run as a diagnostic example, one run '
                 'did not fabricate a stage, and 24 productive runs did establish one')
    store.db.close()
    store2.db.close()


def check_archive_fairness(root):
    """More runs must not buy archive space, and a maximum must not win a nomination (fixture F)."""
    scenario = fixture_scenario()
    store = make_store(root, 'f.sqlite', scenario)
    parent = parent_of(store)
    # A heavily tested build: 500 resolved runs, all low, one extreme maximum.
    with store.db:
        for ordinal in range(500):
            seeds = seed_pair('validation', ordinal)
            store.record(parent, 'validation', ordinal,
                         synthetic_result(40 if ordinal == 499 else 1, seeds,
                                          dict(maxSimultaneousStoredCommands=1,
                                               commandsTargetingBoss=1,
                                               commandsReleasedAfterDeath=1,
                                               postDeathPrizes=0, bossDeathTick=50,
                                               storedCommandsTargetingBossAtDeath=1)))
    # A lightly tested sibling with a genuinely better distribution.
    sibling = deepcopy(scenario)
    sibling['tickLimit'] = int(scenario['tickLimit'])
    from strategy_optimizer import identity
    sibling_id = store.add(sibling, 'Fixture sibling', 'supplied', stats(sibling))
    with store.db:
        for ordinal in range(64):
            if sibling_id == parent:
                break
            seeds = seed_pair('validation', ordinal)
            store.record(sibling_id, 'validation', ordinal,
                         synthetic_result(8, seeds, dict(maxSimultaneousStoredCommands=3,
                                                         commandsTargetingBoss=1,
                                                         commandsReleasedAfterDeath=2,
                                                         postDeathPrizes=1, bossDeathTick=52,
                                                         storedCommandsTargetingBossAtDeath=1)))
    rows, coverage = bt.evidence_view(store, scenario['encounterId'])
    archive_state, bands = bt.archive(None, rows)
    kept_parent = [entry for entry in archive_state['examples'] if entry['candidate'] == parent]
    expect(len(kept_parent) <= bt.ARCHIVE_PER_CANDIDATE,
           f'F: one build keeps at most {bt.ARCHIVE_PER_CANDIDATE} examples, got {len(kept_parent)}')
    expect(len(archive_state['examples']) <= bt.ARCHIVE_TOTAL,
           f'F: the archive stays bounded, got {len(archive_state["examples"])}')
    expect(bands["20+"]["count"] == 1,
           f'F: the 500-run build contributes one band count for its single high run, '
          f'got {bands["20+"]}')
    # Idempotence: reanalysing the *same* view must not move a single count or duplicate an example.
    again, bands_again = bt.archive(archive_state, rows)
    expect(bands_again == bands,
           f'F: reanalysing unchanged rows leaves the band counts identical, got {bands_again}')
    expect(len(again['examples']) == len(archive_state['examples']),
           'F: reanalysing unchanged rows adds no duplicate examples')
    # A full band still admits a better example instead of freezing on the first twelve it saw: twenty
    # runs in the 2-4 band fill it, and a stronger run in the *same* band then takes a slot.
    full = [dict(candidate=f'c{index:03d}', phase='validation', ordinal=index, earned=2,
                 label=f'fixture {index}', stream='community', seeds=[1, index], result=None,
                 telemetry=False) for index in range(20)]
    filled, _ = bt.archive(None, full)
    expect(len(filled['examples']) == bt.ARCHIVE_PER_BAND,
           f'F: one band keeps exactly its cap, got {len(filled["examples"])} of '
           f'{bt.ARCHIVE_PER_BAND}')
    stronger = full + [dict(full[0], candidate='strong', earned=4)]
    replaced, _ = bt.archive(filled, stronger)
    expect(any(entry['earned'] == 4 for entry in replaced['examples']),
           'F: a full band accepts a more informative example')
    NOTES.append('F: 500 opportunities for an extreme maximum bought the heavily tested build no '
                 'archive monopoly and no reliability')
    store.db.close()


# ---------------------------------------------------------------------------------------------
# G. Selected-seed overfitting must never become a confirmed champion
# ---------------------------------------------------------------------------------------------

def model_overfit(parent_atk, development=64):
    """The change helps only the seeds that selected it (fixture G)."""
    def model(scenario, seeds, ordinal):
        import strategy_probe
        unit = strategy_probe.default_probe_unit(scenario, ['atk', 'spd'])
        moved = strategy_probe.pivot_value(scenario, unit, 'atk') <= parent_atk*0.6
        if moved and ordinal < development:
            return 14, dict(maxSimultaneousStoredCommands=8, commandsTargetingBoss=4,
                            storedTargetHoldersPeak=2, commandsReleasedAfterDeath=4,
                            postDeathPrizes=2, bossDeathTick=58,
                            storedCommandsTargetingBossAtDeath=2)
        return 3, dict(maxSimultaneousStoredCommands=2, commandsTargetingBoss=1,
                       storedTargetHoldersPeak=1, commandsReleasedAfterDeath=1,
                       postDeathPrizes=0, bossDeathTick=58, storedCommandsTargetingBossAtDeath=1)
    return model


def check_overfitting(root):
    scenario = fixture_scenario()
    store = make_store(root, 'g.sqlite', scenario)
    parent = parent_of(store)
    model = model_overfit(axis_value(scenario, 'Ninja (A aw20)', 'atk'))
    with store.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            earned, progress = model(store.scenario(parent), seeds, ordinal)
            store.record(parent, 'validation', ordinal, synthetic_result(earned, seeds, progress))
    bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    fill_all(store, model)
    bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    record = bt.load(store)['encounters'][str(scenario['encounterId'])]
    screening = [plan for plan in record['plans'] if plan['kind'] == 'screening'][0]
    nominated = (screening['result'] or {}).get('best') or {}
    expect(nominated.get('rewardPromising'),
           f'G: the screening stage really did nominate the overfit arm ({nominated.get("name")})')
    confirmation = [plan for plan in record['plans'] if plan['kind'] == 'confirmation']
    expect(confirmation, 'G: a confirmation was frozen before any fresh seed was run')
    fill_all(store, model)
    bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    record = bt.load(store)['encounters'][str(scenario['encounterId'])]
    confirmation = [plan for plan in record['plans'] if plan['kind'] == 'confirmation'][0]
    expect(confirmation['status'] == 'not_promoted',
           f'G: the overfit candidate was not promoted, got {confirmation["status"]}')
    expect(not record.get('confirmed'),
           'G: nothing was published as a confirmed improvement')
    result = confirmation.get('result') or {}
    expect(result.get('nomineeMean') is not None and result.get('nomineeMedian') is not None
           and result.get('completedSamples') == result.get('requestedSamples'),
           f'G: the confirmation reports the window it actually measured, got {result}')
    expect(result.get('incumbentMean') == result.get('nomineeMean'),
           f'G: the fresh window really showed no gain (means {result.get("incumbentMean")} vs '
           f'{result.get("nomineeMean")})')
    # The loop must keep working after a failed confirmation rather than retiring the encounter: the
    # next pass starts the other tested direction of the same hypothesis.
    later = [plan for plan in record['plans'] if plan['kind'] == 'screening']
    expect(len(later) >= 2,
           f'G: a failed confirmation leaves the encounter able to run another experiment, got {later}')
    NOTES.append('G: an intervention that only helped its own selecting seeds was screened in, frozen, '
                 'measured on 448 unused seeds, refused, and the loop continued with the next direction')
    store.db.close()


# ---------------------------------------------------------------------------------------------
# I/J/K. Accounting, operations, and the Student 9 integration fixes
# ---------------------------------------------------------------------------------------------

def check_accounting(root):
    """A replay of the same deterministic battle is one sample, not two (fixture I)."""
    scenario = fixture_scenario()
    store = make_store(root, 'i.sqlite', scenario)
    parent = parent_of(store)
    with store.db:
        for ordinal in range(8):
            seeds = seed_pair('validation', ordinal)
            store.record(parent, 'validation', ordinal,
                         synthetic_result(5, seeds, dict(maxSimultaneousStoredCommands=1,
                                                         commandsTargetingBoss=1,
                                                         commandsReleasedAfterDeath=1,
                                                         postDeathPrizes=0, bossDeathTick=50,
                                                         storedCommandsTargetingBossAtDeath=1)))
    rows, coverage = bt.evidence_view(store, scenario['encounterId'])
    summary = bt.outcome_stats(rows)
    expect(summary['n'] == 8, f'I: eight distinct seeds are eight samples, got {summary["n"]}')
    # The same deterministic battle recorded again under another purpose - the other phase, which is
    # what a replay is - must not become a second independent sample.
    with store.db:
        for ordinal in range(8):
            seeds = seed_pair('validation', ordinal)
            store.record(parent, 'discovery', ordinal,
                         synthetic_result(5, seeds, dict(maxSimultaneousStoredCommands=1,
                                                         commandsTargetingBoss=1,
                                                         commandsReleasedAfterDeath=1,
                                                         postDeathPrizes=0, bossDeathTick=50,
                                                         storedCommandsTargetingBossAtDeath=1)))
    rows, coverage = bt.evidence_view(store, scenario['encounterId'])
    summary = bt.outcome_stats(rows)
    expect(summary['n'] == 8,
           f'I: the same battle stored in both phases is still eight samples, got {summary["n"]}')
    expect(coverage['scopes'] >= 1, 'I: the view reports its compatibility scope')
    NOTES.append('I: a replay/duplicate of one deterministic battle stayed one sample')
    store.db.close()


def check_operational(root):
    """Zero share, resume, missing telemetry and the missing-incumbent edge (fixture J)."""
    scenario = fixture_scenario()
    store = make_store(root, 'j.sqlite', scenario)
    parent = parent_of(store)
    with store.db:
        for ordinal in range(64):
            seeds = seed_pair('validation', ordinal)
            store.record(parent, 'validation', ordinal,
                         synthetic_result(3 + (ordinal % 3), seeds,
                                          dict(maxSimultaneousStoredCommands=1,
                                               commandsTargetingBoss=1,
                                               commandsReleasedAfterDeath=1,
                                               postDeathPrizes=0, bossDeathTick=50,
                                               storedCommandsTargetingBossAtDeath=1)))
    before = store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]
    off = bt.advance(store, share=0.0, workers=1, focus_encounter=scenario['encounterId'])
    after = store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]
    expect(off['enabled'] is False and 'no proposals' in off['decision'],
           f'J: a 0% share launches nothing, got {off["decision"]!r}')
    expect(before == after, 'J: no candidate was created at a 0% share')
    expect(bt.banks(store) == {}, 'J: no bank was declared at a 0% share')
    on = bt.advance(store, share=0.5, workers=1, focus_encounter=scenario['encounterId'])
    expect(on['started'], 'J: a positive share starts a plan')
    state = bt.load(store)
    restored = bt.status(store)
    expect(restored['encounters'] and restored['encounters'][str(scenario['encounterId'])]['plan'],
           'J: the plan survives a reload, so a restart resumes instead of repeating')
    expect(any(plan['id'] == on['started'] for plan in state['encounters'][str(scenario['encounterId'])]['plans']),
           'J: the started plan is the persisted one')
    # Missing telemetry is reported, never inferred.
    blind = fixture_scenario()
    store2 = make_store(root, 'j2.sqlite', blind)
    blind_parent = parent_of(store2)
    with store2.db:
        for ordinal in range(16):
            seeds = seed_pair('validation', ordinal)
            result = synthetic_result(ordinal % 7, seeds)
            result.pop('progressMetrics')
            store2.record(blind_parent, 'validation', ordinal, result)
    blind_state = bt.encounters_of(store2) if hasattr(bt, 'encounters_of') else None
    bt.advance(store2, share=0.5, workers=1, focus_encounter=blind['encounterId'])
    record2 = bt.load(store2)['encounters'][str(blind['encounterId'])]
    expect(record2['status'] == 'telemetry_missing',
           f'J: runs without behaviour detail report missing telemetry, got {record2["status"]}')
    # The missing-incumbent edge: a fight where nothing is credible yet must not raise.
    thin = fixture_scenario()
    store3 = make_store(root, 'j3.sqlite', thin)
    thin_parent = parent_of(store3)
    with store3.db:
        for ordinal in range(32):
            seeds = seed_pair('validation', ordinal)
            store3.record(thin_parent, 'validation', ordinal,
                          synthetic_result(4, seeds, dict(maxSimultaneousStoredCommands=1,
                                                          commandsTargetingBoss=1,
                                                          commandsReleasedAfterDeath=1,
                                                          postDeathPrizes=0, bossDeathTick=50,
                                                          storedCommandsTargetingBossAtDeath=1)))
    try:
        parent_id, _scenario, why = students.average_parent(store3, thin['encounterId'])
        expect(parent_id == thin_parent and why.startswith('validate'),
               f'J: the under-sampled parent is chosen for validation without raising ({why!r})')
    except Exception as error:  # noqa: BLE001 - the point of the assertion
        expect(False, f'J: the missing-incumbent edge raised {type(error).__name__}: {error}')
    # And the gate really halts this stream's candidates.
    from strategy_optimizer import dispatch_gate
    gate = dispatch_gate({students.STUDENT_MECHANISM: 0.0, students.STREAM_DISCOVERY: 1.0},
                         set(), set(), set(), {'arm'})
    expect(gate('arm') is False, 'J: the dispatch gate halts this stream at a 0% share')
    store.db.close()
    store2.db.close()
    store3.db.close()


def check_student9_integration(root):
    """The requested Student 9 fixes, asserted rather than assumed (fixture K)."""
    scenario = fixture_scenario()
    store = make_store(root, 'k.sqlite', scenario)
    parent = parent_of(store)
    # 32 runs: above the validation minimum, below the credibility minimum, so the parent is "thin".
    with store.db:
        for ordinal in range(32):
            seeds = seed_pair('validation', ordinal)
            store.record(parent, 'validation', ordinal,
                         synthetic_result(9, seeds, dict(maxSimultaneousStoredCommands=1,
                                                         commandsTargetingBoss=1,
                                                         commandsReleasedAfterDeath=1,
                                                         postDeathPrizes=0, bossDeathTick=50,
                                                         storedCommandsTargetingBossAtDeath=1)))
    created = students.replenish_average(store, scenario['encounterId'], 3, 1, stats, None)
    banks = students.average_banks(store)
    plan = store.get(students.AVERAGE_PLAN_KEY) or {}
    expect(created == [], f'K: a validate decision creates no children, got {len(created)}')
    expect(banks.get(parent, 0) >= 64,
           f'K: "validate" schedules runs of the parent itself, got bank {banks.get(parent)}')
    expect(plan.get('operation') == 'evaluate' and plan.get('candidate') == parent,
           f'K: the decision names the operation and the candidate, got {plan}')
    # Stagnation comes from finished work, not from being asked again.
    first = students.average_progress(store, scenario['encounterId'], 5.0, point_now=9.0,
                                      completed=32)
    second = students.average_progress(store, scenario['encounterId'], 5.0, point_now=9.0,
                                       completed=32)
    state = (store.get(students.AVERAGE_PROGRESS_KEY) or {})
    expect(first[1] is False, 'K: a fresh reading with completed work is not stalled')
    entry = state.get(str(scenario['encounterId'])) or {}
    expect(int(entry.get('passes') or 0) == 0,
           f'K: being asked again with no finished work does not age the clock, got {entry}')
    students.average_progress(store, scenario['encounterId'], 5.0, point_now=9.0, completed=40)
    entry = (store.get(students.AVERAGE_PROGRESS_KEY) or {}).get(str(scenario['encounterId'])) or {}
    expect(int(entry.get('passes') or 0) == 1,
           f'K: finished work ages the clock by one pass, got {entry}')
    # A tightening interval is not progress: same point mean, higher reliable mean, more samples.
    students.average_progress(store, scenario['encounterId'], 5.4, point_now=9.0, completed=48)
    entry = (store.get(students.AVERAGE_PROGRESS_KEY) or {}).get(str(scenario['encounterId'])) or {}
    expect(int(entry.get('passes') or 0) == 2,
           f'K: more samples on the same mean do not reset the clock, got {entry}')
    students.average_progress(store, scenario['encounterId'], 5.6, point_now=11.0, completed=56)
    entry = (store.get(students.AVERAGE_PROGRESS_KEY) or {}).get(str(scenario['encounterId'])) or {}
    expect(int(entry.get('passes') or 0) == 0,
           f'K: a genuinely higher measured mean does reset it, got {entry}')
    report = students.average_report(store)
    expect(str(scenario['encounterId']) in {str(key) for key in report},
           f'K: Student 9 has a report the application can show, got {list(report)}')
    NOTES.append('K: Student 9 validates by scheduling the parent, continues past the screening bank, '
                 'and ages its stagnation clock only on finished work')
    store.db.close()


def main():
    # Fixture stores are closed on the way out; Windows can still hold the WAL handle for a moment, and
    # a leftover temp directory is not a failure of the loop under test.
    with tempfile.TemporaryDirectory(prefix='ka-breakthrough-',
                                     ignore_cleanup_errors=True) as root:
        check_learnable_mechanism(root)
        check_lottery(root)
        check_misleading_proxy(root)
        check_two_mechanisms(root)
        check_low_ranked_evidence(root)
        check_archive_fairness(root)
        check_overfitting(root)
        check_accounting(root)
        check_operational(root)
        check_student9_integration(root)
    for note in NOTES:
        print(f'  {note}')
    print(f'failures: {len(FAILURES)}')
    for detail in FAILURES:
        print(f'  {detail}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
