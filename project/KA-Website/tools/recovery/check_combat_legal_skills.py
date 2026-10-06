"""Legal saved-team skills: Critical UP (type23) and Revive (type15) run end to end.

Recovered routes exercised:
  * type23 Critical UP is an invoking-skill crit modifier reached through
    BattleHelper.LotCritical 0x168cc54 -> SkillComponent.EnumerateInvokingSkills 0x14d1880
    (differential in check_combat_shared_lottery), and its append producer is the Attack tail
    BattleHelper.Attack 0x168d1e8: List<InvokingSkill>$$AddWithResize 0x1dd0928 at 0x168dea4 with
    the (dataId, count10) pair packed at 0x168de5c..0x168de60 (InvokingSkill..ctor 0x1686204 is
    inlined; it has no recorded direct caller in the index).
  * type15 Revive is the category-1 Cure branch: UseSkill 0x15e0c2c -> Cure -> OnCure 0x1587e64 ->
    Param.Add + CureResult type15 queue-position/state2 (differential in check_combat_cure_event).

The whitelist enumeration, the run_scenario fixtures and the export regression below are portable
checks over the recovered data; no new native execution is claimed here.
"""
import json
from copy import deepcopy

from combat_initial_state import EVIDENCE
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_resolution import SystemRandomState
from combat_shared_controllers import (SharedControllers, ROWS, skill_unsupported_reason,
    SUPPORTED_SKILL_TYPES, SUPPORTED_GENERATED_STATUS_TYPES)
from combat_runtime_data import load_data
from combat_sandbox import run_scenario

LEGAL = (EVIDENCE.parent / '20260919-configurable-battle-setup/legal-integration-16.14/'
                          'legality-valid-scenario.json')


def ally(name, skills, stats):
    parameters = {str(p): dict(rawValue=stats.get(p, 1), rawMax=stats.get(p, 1) if p in (10, 11) else 2147483647,
                               extraValue=0, extraMax=0, trainingLevel=123) for p in HUMAN_TRAINING_PARAMETERS}
    return dict(name=name, human=True, monsterId=None, weaponId=0, equipment=[], visitor=False,
                leaderIdentity=False, skills=list(skills), invocationLevels=[1]*len(skills), parameters=parameters)


def legal_scenario():
    return json.loads(LEGAL.read_text(encoding='utf-8'))


def boosted_knight(scenario):
    knight = deepcopy(scenario['ownUnits'][0])
    for pid, value in (('10', 100000), ('11', 100000), ('13', 80000), ('15', 99999), ('16', 100000)):
        knight['parameters'][pid].update(rawValue=value, rawMax=value)
    return knight


def winning_scenario():
    """The legal Knight (skills 33 Area Attack + 30 Critical UP) made able to annihilate encounter 19."""
    scenario = legal_scenario()
    scenario['ownUnits'] = [boosted_knight(scenario)] + [ally(f'support{i}', [], {}) for i in range(4)]
    scenario['tickLimit'] = 4000
    scenario['inputs'] = []
    return scenario


def revive_scenario():
    """The legal Knight plus a reviving ally (skill 41 Revive 100%), runnable without a rejection."""
    scenario = legal_scenario()
    scenario['ownUnits'] = scenario['ownUnits'] + [ally('legal reviver', [41], {10: 900, 11: 900})]
    scenario['tickLimit'] = 300
    return scenario


def whitelist_enumeration():
    rows = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}
    supported = {sid for sid, row in rows.items() if skill_unsupported_reason(row) is None}
    # Critical UP and Revive are exactly the rows the saved legal team needs and the old whitelist rejected.
    assert {30, 108, 40, 41}.issubset(supported), sorted({30, 108, 40, 41} - supported)
    # The already-implemented category-0 routes that were missing from the old whitelist.
    assert {row['id'] for row in rows.values()
            if row['type'] in (0, 15, 21, 23, 24)}.issubset(supported)
    # The flags&0x40000 generated-status route: only types 66/67.
    assert {113, 114, 115, 116, 117, 118, 119}.issubset(supported)
    assert not {sid for sid in (113, 117)} - supported
    # Leader passives (category-2 type60) stay supported; other category-2 noncombat/vehicle rows do not.
    assert {105, 106, 107}.issubset(supported)
    category_two = {sid for sid, row in rows.items() if row['category'] == 2}
    # The ten always-on type-48 EQUIP_MASTER affinity rows are supported too; every other
    # category-2 noncombat/vehicle/passive row still fails closed.
    assert category_two & supported == set(range(82, 92)) | {105, 106, 107}, sorted(category_two & supported)
    for sid in (44, 45, 52, 60, 64):
        assert skill_unsupported_reason(rows[sid]) is not None, sid
    return supported


def revived_unit_timeline():
    """Real UseSkill cure route: a dead ally is revived, requeued to state2 and moved to QueuePosition."""
    from check_combat_sandbox import human
    def spec(name, team, skills, stats):
        return dict(human(name, skills, stats), team=team, grid=team, cell=[team, team],
                    levels=[1]*len(skills), human=team == 0)
    specs = [spec('reviver', 0, [40], {10: 1000, 11: 1000, 14: 10, 15: 10}),
             spec('fallen', 0, [], {10: 500, 11: 10, 14: 10, 15: 10}),
             spec('enemy', 1, [], {10: 100, 11: 10, 14: 10, 15: 10})]
    engine = SharedControllers(specs, 7, 8, movement=False)
    reviver, fallen = engine.teams[0][0], engine.teams[0][1]
    maximum = engine.maximum(fallen['id'], 10)
    engine.param(fallen['id'], 10)['rawValue'] = 0
    engine.change(fallen, 7)
    assert fallen['board'][5] == 7 and engine.value(fallen['id'], 10) == 0
    engine.use(reviver, dict(target=fallen['id'], skill=40, traceId=0), 0)
    heals = [e for e in engine.trace if e['kind'] == 'heal']
    revives = [e for e in engine.trace if e['kind'] == 'revive']
    assert len(heals) == 1 and len(revives) == 1, engine.trace
    # skill40 value50 -> exactly half the effective maximum; revive returns the fighter to state2.
    assert engine.value(fallen['id'], 10) == maximum // 2 == 250
    assert fallen['board'][5] == 2 and revives[0]['state'] == 2
    assert (fallen['board'][13], fallen['board'][14]) == (24, 120)
    # Native order: the healing effect is created before FighterSystem.OnCure applies HP.
    assert heals[0]['before'] == 0 and heals[0]['after'] == 250
    return dict(maximum=maximum, amount=heals[0]['amount'], destination=(fallen['board'][13], fallen['board'][14]))


def winning_export_regression():
    """Real winning nonempty-prize run through export_replay; Finish, chest_dispatch and RNG agree."""
    from combat_replay_export import export_replay
    scenario = winning_scenario()
    replay = export_replay(scenario)
    finish, post = replay['finish'], replay['postFinish']
    assert finish['fightOutcome']['winner'] == 1
    queued, dispatched = finish['queuedChestAwards']['count'], finish['dispatchedChestAwards']['count']
    assert queued >= 1 and dispatched == queued, (queued, dispatched)
    chest_events = [e for e in replay['events'] if e['kind'] == 'chest_dispatch']
    finish_events = [e for e in replay['events'] if e['kind'] == 'finish']
    assert len(chest_events) == dispatched == finish_events[0]['dispatchedChests']
    assert [e['treasureId'] for e in chest_events] == [p['treasureId'] for p in finish['dispatchedChestAwards']['entries']]
    assert post['chestDispatchMathDraws'] == 3 * dispatched  # duration, dx, dz per FireTreasure
    assert post['mathDrawsInTrace'] == post['mathDrawsAfterFinish'] == replay['finalState']['mathDraws']
    # The recorded final Math state must equal a fresh stream advanced by every recorded draw,
    # so the last draw (the last finish chest value) is inside the snapshot.
    recorded = replay['finalState']['rngFinalState']['math']
    state = SystemRandomState(scenario['mathSeed'])
    for _ in range(post['mathDrawsInTrace']):
        state.next_int()
    assert (state.index, state.partner, state.values) == (recorded['index'], recorded['partner'], recorded['values'])
    assert finish['inventoryCollection'] == {'state': 'not modelled', 'count': None,
        'note': 'Opening the dispatched chest runs the TreasureBoxResult arms; storage/'
                'inventory and world collection of ground spawns stay outside this report'}
    return dict(queued=queued, dispatched=dispatched, chestDispatchEvents=len(chest_events),
                finishMathDraws=post['chestDispatchMathDraws'], totalMathDraws=post['mathDrawsInTrace'])


def equip_master_resistance():
    """Possessed type-48 EQUIP_MASTER rows are accepted passives that lift only a matching type.

    Recovered semantics (JobData.GetAffinity 0x16208d0, predicate 0x162cc34): the override
    candidate set is exactly {-1, 0}, the predicate compares the row's `value` with
    EquipData.type, and a match returns 1. The rows carry no FLAG_FOR_BATTLE 0x8, so they are
    never selected as active skills; they stay in the possessed skill list.
    """
    from combat_skill_selection import active_skill_infos
    from combat_replay_export import export_replay
    profiles = load_data('weapon-skill-profiles.json')
    rows = {r['id']: r for r in profiles['skills']}
    equipment = {r['id']: r for r in profiles['equipment']}
    equip_master = sorted(sid for sid, row in rows.items() if row['type'] == 48)
    assert equip_master == list(range(82, 92)), equip_master
    assert all(skill_unsupported_reason(rows[sid]) is None for sid in equip_master)
    assert all(not rows[sid]['flags'] & 8 for sid in equip_master)
    # Permanently active: even a row that carries the battle flag, or a non-category-2 variant,
    # has no active route here and fails closed instead of being silently accepted.
    assert active_skill_infos([rows[89]], [1], 1000, lambda skill: 1) == []
    assert skill_unsupported_reason(dict(rows[89], flags=rows[89]['flags'] | 8)) is not None
    assert skill_unsupported_reason(dict(rows[89], category=0)) is not None
    assert skill_unsupported_reason(rows[44]) is not None  # Thief, type3 category2, still rejected

    def weak_club(skill):
        scenario = legal_scenario()
        knight = scenario['ownUnits'][0]
        weapon = next(s for s in knight['equipment'] if s['id'] == knight['weaponId'])
        assert equipment[weapon['id']]['type'] == 6 and equipment[weapon['id']]['category'] == 0
        weapon['affinity'] = 0  # declared weak: native affinity 0 halves any positive contribution
        knight['skills'] = [skill]
        knight['invocationLevels'] = [1]
        return scenario

    def contribution(skill):
        replay = export_replay(weak_club(skill))
        unit = replay['units'][0]
        assert unit['skillIds'] == [skill], unit['skillIds']  # the possessed row is retained
        return unit['parameters']['13']

    matching, nonmatching = contribution(87), contribution(89)
    # Club (type 6) at level 12 is 4 + 1*11 = 15. Club Resistance (value 6) matches it ->
    # affinity 1 -> full 15; Bow Resistance (value 8) does not -> affinity 0 -> max(1, 15 // 2) = 7.
    def gain(entry):
        raw = entry['raw']
        return entry['effectiveValue'] - raw['rawValue'] - raw['extraValue']

    matching_gain, nonmatching_gain = gain(matching), gain(nonmatching)
    assert (matching_gain, nonmatching_gain) == (15, 7), (matching_gain, nonmatching_gain)
    return dict(equipMasterRows=equip_master, weaponType=6, weaponLevel=12,
                matchingSkill=87, nonmatchingSkill=89,
                matchingAttackGain=matching_gain, nonmatchingAttackGain=nonmatching_gain)


def main():
    supported = whitelist_enumeration()
    fixture = legal_scenario()
    legal = run_scenario(fixture)
    revive = run_scenario(revive_scenario())
    # The legality fixture is the adapter's own output: it declares isolated-scene0 source
    # conditions (startProfile), so the runner reports the supplied pre-placement mode. The
    # 'final-cell starting approximation' mode is only reached when no startProfile/prePlacement
    # is declared, which the adapter no longer produces for a normal setup.
    declared_source = 'startProfile' in fixture or 'prePlacement' in fixture
    assert legal['result']['initializationMode'] == (
        'supplied pre-placement state, sequential teams' if declared_source
        else 'final-cell starting approximation')
    # The revive skill is accepted and the fixture runs the real cure route to a native verdict;
    # reaching a result at all proves load_scenario no longer rejects type15. It is no longer
    # censored at the tick horizon - the recovered verdict path resolves it - so assert the native
    # resolution rather than the old short-horizon censoring.
    assert revive['result']['censored'] is False and revive['result']['verdict'] in (1, 2), revive['result']
    assert 'native verdict' in revive['result']['stopReason'], revive['result']['stopReason']
    revived = revived_unit_timeline()
    win = winning_export_regression()
    resistance = equip_master_resistance()
    report = dict(supportedSkillRows=len(supported), totalSkillRows=len(ROWS),
                  legalFixtureVerdict=legal['result']['verdict'], legalFixtureCensored=legal['result']['censored'],
                  reviveFixtureRunnable=True, revivedUnit=revived, winningExport=win,
                  equipMasterResistance=resistance,
                  findings=[
                      'Critical UP (type23) and Revive (type15) are accepted: both routes were already '
                      'integrated (invoking crit modifier; category-1 Cure/OnCure) and were only missing '
                      'from the whitelist.',
                      'The whitelist now names one integrated set, plus the flags&0x40000 generated-status '
                      'types, plus the ten always-on type-48 EQUIP_MASTER affinity rows; every other '
                      'category-2 noncombat/vehicle/passive row is rejected with its type. A type-48 row is '
                      'never invoked: it is retained in the possessed list and only lifts a matching '
                      'equipment type declared affinity -1/0 to 1 (JobData.GetAffinity 0x16208d0).',
                      'Revive runs through the real cure route: half-maximum HP for skill40, requeue to '
                      'state2 with the recovered QueuePosition in BB13/BB14.',
                      'A real winning nonempty-prize scenario exports with Finish queue == chest_dispatch '
                      'event count and a final Math state that equals the full recorded draw sequence.'],
                  limits=[
                      'Legal-build feasibility (job, ownership, slots) is not enforced; scenarios are explicit inputs.',
                      'Fight results remain model estimates from the recovered composition, not certified predictions.',
                      'inventoryCollection stays count=None (unknown): no chest was opened or stored.'])
    (EVIDENCE / 'legal-skills-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
