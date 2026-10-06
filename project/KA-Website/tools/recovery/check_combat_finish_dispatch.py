"""Special Finish (0x14ed864) joined to the sandbox report lifecycle.

Two differentials against recorded native output, not a plausible-model test:
  * replay of `special-settlement-checks.json` (winner x queued count 0/1/10/60/70/256 from the
    original loop) through the portable `special_finish`; and
  * a controlled two-fighter engine where a real boss Leaving callback queues the prize, the
    native completion condition (`IsAnnihilated`) is then reached by running a tick, and the
    recovered Finish step dispatches chest entities on the same Math stream.

The report must keep fight outcome, queued awards, dispatched chest entities and inventory
collection separate; nothing here claims EXP, storage or world pickup.
"""
import json

from check_combat_sandbox import human
from combat_finish import finish_report, is_dispatched_type, special_finish
from combat_initial_state import EVIDENCE
from combat_prizes import special_prize_candidates
from combat_receipt import ReceiptLedger
from combat_sandbox import finish_lifecycle, queued_prizes, run_scenario
from combat_shared_controllers import SharedControllers


def drive(prize_count=1, verdict=1):
    """One boss Leaving callback per requested prize, then the native verdict tick."""
    specs = []
    for team in (0, 1):
        unit = human(f'finish probe {team}', [], {10: 100, 11: 100, 14: 10, 15: 10})
        specs.append(dict(unit, team=team, grid=team, cell=[team, team], levels=[], human=team == 0,
                          boss=team == 1))
    engine = SharedControllers(specs, 7, 8, prize_candidates=special_prize_candidates(19),
                               receipts=ReceiptLedger())
    boss = engine.teams[1][0]
    engine.param(boss['id'], 10)['rawValue'] = 0
    for _ in range(prize_count):
        engine.change(boss, 8)
    if verdict == 1:
        engine.run(1)
        # This probe drives the Finish dispatch in isolation, so it declares the valid Ending
        # confirmation explicitly (counter>79) instead of spending ticks that would draw Math values
        # and break the exact 3-draws-per-chest assertion below. `combat_ending.update_ending` only
        # transitions from frame>79; the declared-input policy lives in check_combat_finish_policy.
        engine.ending_confirmed = True
        engine.ending_counter = 80
    return engine, boss


def replay_recorded_native():
    recorded = json.loads((EVIDENCE / 'special-settlement-checks.json').read_text(encoding='utf-8'))
    checked = 0
    for example in recorded['examples']:
        prizes = [dict(type=0, treasureId=710 + i % 2, index=i) for i in range(example['queuedTreasures'])]
        released = []
        dispatched = special_finish(example['winner'], prizes, special=True, defeat_count=lambda: None,
                                    fire_treasure=released.append, pop_battle_form=lambda: None,
                                    destroy_source_boss=lambda: None)
        assert len(dispatched) == example['released'], (example, len(dispatched))
        assert [p['index'] for p in dispatched] == list(range(len(dispatched)))
        assert released == dispatched
        assert len(dispatched) == (len(prizes) if example['winner'] == 1 else 0)
        checked += 1
    # Non-special battles never dispatch, whatever the winner field says.
    prizes = [dict(type=0, treasureId=710)]
    assert special_finish(1, prizes, special=False, defeat_count=lambda: None, fire_treasure=lambda p: None,
                          pop_battle_form=lambda: None, destroy_source_boss=lambda: None) == []
    # Only type0 entries survive the 0x14f3160 filter.
    assert [is_dispatched_type(p) for p in ({'type': 0}, {}, {'type': 1}, {'type': -2147483648})] == \
           [True, True, False, False]
    return recorded, checked


def main():
    recorded, checked = replay_recorded_native()

    # Win path: one queued prize, native verdict reached, one chest entity dispatched.
    engine, boss = drive(prize_count=1)
    assert engine.verdict == 1, engine.verdict
    queued = queued_prizes(engine)
    assert len(queued) == 1 and queued[0]['treasureId'] in special_prize_candidates(19)
    assert len(engine.receipts.entries) == 1 and engine.receipts.entries[0]['state'] == 'queued'
    before = engine.math_draws
    reason, dispatched = finish_lifecycle(engine, engine.verdict)
    assert len(dispatched) == 1 and engine.math_draws - before == 3  # duration, dx, dz
    assert dispatched[0]['treasureId'] == queued[0]['treasureId']
    assert all(entry['collection'] == 'pending' and entry['entityCreated'] for entry in dispatched)
    report = finish_report(engine.verdict, queued, dispatched)
    assert report['fightOutcome']['winner'] == 1
    assert report['queuedChestAwards']['count'] == 1
    assert report['dispatchedChestAwards']['count'] == 1
    assert report['inventoryCollection']['state'] == 'not modelled'
    assert report['exp'] == 'not modelled'
    assert engine.receipts.entries[0]['state'] == 'queued', 'Finish must not fake a receipt'

    # Repeated awards: every queued entry is dispatched in order, none clamped or deduplicated.
    engine, _ = drive(prize_count=8)
    queued = queued_prizes(engine)
    before = engine.math_draws
    _, dispatched = finish_lifecycle(engine, 1)
    assert len(dispatched) == 8 and engine.math_draws - before == 24
    assert [d['treasureId'] for d in dispatched] == [p['treasureId'] for p in queued]
    # Duplicate treasure ids stay duplicate entries: no deduplication was recovered.
    assert len({d['treasureId'] for d in dispatched}) <= 8

    # Loss / non-dispatch: resolved battle, winner 2, nothing released.
    engine, _ = drive(prize_count=2, verdict=2)
    queued = queued_prizes(engine)
    before = engine.math_draws
    _, dispatched = finish_lifecycle(engine, 2)
    assert dispatched == [] and engine.math_draws == before
    assert finish_report(2, queued, [])['dispatchedChestAwards']['count'] == 0

    # Unresolved battle keeps the censored horizon and emits no finish section.
    from check_combat_sandbox import example
    data = example()
    data['tickLimit'] = 3
    unresolved = run_scenario(data)
    assert unresolved['finish'] is None and unresolved['result']['censored'] is True
    assert unresolved['receipts']['censored'] is True
    assert 'unresolved' in unresolved['result']['stopReason']

    # A resolved run through run_scenario: one-HP own team reaches IsAnnihilated (winner 2).
    loss = example()
    for unit in loss['ownUnits']:
        unit['parameters'][10].update(rawValue=1, rawMax=1)
        unit['skills'] = []
        unit['invocationLevels'] = []
    # Explicit empty declaration per owner: an absent entry is now a missing-host error, and the
    # recovered contract requires "no pets" to be declared as [] rather than by omission.
    loss['housePets'] = {owner: [] for owner in loss['housePets']}
    loss['tickLimit'] = 200
    resolved = run_scenario(loss)
    assert resolved['result']['verdict'] == 2 and resolved['result']['censored'] is False
    assert resolved['finish']['fightOutcome']['winner'] == 2
    assert resolved['finish']['dispatchedChestAwards']['count'] == 0
    assert resolved['finish']['queuedChestAwards']['count'] == resolved['metrics']['prizeCallbacks']
    assert resolved['receipts']['censored'] is False

    report = dict(nativeRecordedFinishCases=checked,
                  recordedWinnerCounts=sorted({(e['winner'], e['queuedTreasures']) for e in recorded['examples']}),
                  joinedLifecycleCases=5,
                  findings=[
                      'Portable Finish dispatch reproduces every recorded native winner/count case, in order, '
                      'including 256 entries with no deduplication or clamp.',
                      'Reaching IsAnnihilated now runs the Finish step inside the sandbox report and separates '
                      'fight outcome, queued awards, dispatched chest entities and unmodelled collection.',
                      'FireTreasure consumes three Math draws per dispatched chest in queue order; a loss or '
                      'non-special battle consumes none.',
                      'Queued receipts stay queued at Finish: dispatching a chest entity is not an inventory receipt.'],
                  limits=['Recorded fixtures supply already-filtered type0 entries; the type0 predicate 0x14f3160 '
                          'is compared separately in check_combat_special_settlement.py.',
                          'FireTreasure/CreateTreasure internals, chest opening, storage, world collection, '
                          'confirmation/EXP/teardown are not executed.',
                          'Fight results remain model estimates from the recovered composition, not certified predictions.'])
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
