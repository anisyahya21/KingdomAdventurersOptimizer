"""Item input-callback timeline fidelity (combat_sandbox input emission block).

The contract under test is only the input-callback emission in ``combat_sandbox.run_scenario``:

  * every dispatched consumable - Holy Herb (item 31) and the generic recovery rows - emits one
    ``battle_item`` attempt/use record carrying the item, parameter/scope, the used flag and the
    remaining finite stock (a blocked attempt is recorded too, with ``blocked="no stock"``);
  * every unit/parameter the native ``Parameter.Add`` actually changed emits one ``resource_change``
    carrying ``target``/``parameter``/``before``/``after`` (the engine's own effective value read
    immediately before and after that Add), ``max`` (its effective maximum) and ``sourceItem``.

A clamped (unchanged) parameter emits nothing, so an item restoration is never applied twice and
the mid-fight fold sees exactly the engine's value. Nothing here recomputes a healing amount; the
numbers are read back from the runner's own events.

The real export is folded by the TypeScript side (tools/generated-replay-check/pure_check.mjs); this
check asserts the exported payload carries the per-unit/parameter identity the fold consumes.
"""
import json
from combat_sandbox import run_scenario
from combat_replay_export import export_replay
from combat_initial_state import EVIDENCE
from combat_parameters import HUMAN_TRAINING_PARAMETERS

SKILLS=[26,110,25,24,23,22]  # a farmer skill set that spends MP on its own within a few ticks


def human(name,skills,stats):
    return dict(name=name,human=True,monsterId=None,weaponId=0,equipment=[],visitor=False,leaderIdentity=False,
        skills=skills,invocationLevels=[1]*len(skills),
        parameters={p:dict(rawValue=stats.get(p,1),rawMax=stats.get(p,1) if p in (10,11) else 2147483647,
                           extraValue=0,extraMax=0,trainingLevel=123) for p in HUMAN_TRAINING_PARAMETERS})


def scenario(inputs,stock=1,items=None,item_stock=None,tick_limit=80):
    own=[human(f'f{i}',SKILLS,{10:5000,11:1000,13:300,14:3000,15:220,16:110,19:0}) for i in range(3)]
    return dict(schema='ka-special-combat-research-1',encounterId=19,defeatCount=0,mathSeed=7,libSeed=8,
        tickLimit=tick_limit,ownUnits=own,holyHerbStock=stock,items=items or {},itemStock=item_stock or {},
        inputs=inputs,note='Synthetic item-timeline probe; not a player build or a recommendation.')


HERB=lambda tick:dict(tick=tick,type='holy_herb',phase='before_fighters')
SALVE={'salve':dict(bonusCategory=3,bonusType=0,bonusMinValue=50,bonusMaxValue=50)}
SALVE_INPUT=lambda tick:dict(tick=tick,type='item',item='salve',phase='before_fighters',target='all')


def of(report,kind):return [e for e in report['trace'] if e['kind']==kind]
def own_ids(report):return {report['result']['names'][f'f{i}'] for i in range(3)}
def prior_value(events,unit,tick,key,field):
    """The engine's own value carried by the last prior event naming `unit` in `key`."""
    prior=[e for e in events if e.get(key)==unit and e['tick']<tick]
    return prior[-1][field] if prior else None


def check_holy_herb_use():
    report=run_scenario(scenario([HERB(54)]),True)
    own=own_ids(report);resources=of(report,'resource_change');items=of(report,'battle_item')
    assert len(items)==1,items
    assert items[0]['item']=='holy_herb' and items[0]['used'] is True and items[0]['remaining']==0,items
    assert items[0]['parameter']==11 and items[0]['scope']=='all' and items[0]['percent']==100,items
    assert sorted(items[0]['targets'])==sorted(own),items[0]['targets']
    changed={e['target'] for e in resources}
    assert len(resources)==len(changed)==2,resources
    spends=of(report,'mp')
    for e in resources:
        assert e['tick']==54 and e['parameter']==11 and e['sourceItem']=='holy_herb',e
        assert e['max']==1000 and e['after']==1000 and e['before']<e['after'],e
        # The item's `before` is the engine's own live MP (the previous mp event's after), never a
        # value recomputed from the start snapshot.
        assert e['before']==prior_value(spends,e['target'],54,'caster','after'),(e,spends)


def check_two_uses_chain():
    report=run_scenario(scenario([HERB(54),HERB(64)],stock=2),True)
    items=of(report,'battle_item');resources=of(report,'resource_change')
    assert [i['remaining'] for i in items]==[1,0],items
    assert all(i['used'] is True for i in items),items
    assert sorted(e['tick'] for e in resources)==[54,54,64,64],resources
    spends=of(report,'mp')
    for e in resources:
        assert e['before']==prior_value(spends,e['target'],e['tick'],'caster','after'),(e,spends)
        assert e['after']==e['max']==1000,e


def check_full_stock_no_use():
    report=run_scenario(scenario([]),True)
    assert report['holyHerbRemaining']==1 and not of(report,'battle_item') and not of(report,'resource_change')
    assert report['holyHerbUses']==[]


def check_failed_stock():
    report=run_scenario(scenario([HERB(54)],stock=0),True)
    items=of(report,'battle_item')
    assert len(items)==1 and items[0]['used'] is False and items[0]['blocked']=='no stock',items
    assert items[0]['remaining']==0 and items[0]['item']=='holy_herb',items
    assert not of(report,'resource_change') and report['holyHerbUses'][0]['used'] is False


def check_clamped_all_full():
    report=run_scenario(scenario([HERB(5)]),True)
    items=of(report,'battle_item')
    assert len(items)==1 and items[0]['used'] is False,items
    # Every eligible target is already at its maximum, so no unit/parameter changed.
    assert not of(report,'resource_change'),of(report,'resource_change')


def check_generic_item_multi_unit():
    report=run_scenario(scenario([SALVE_INPUT(40)],items=SALVE,item_stock={'salve':2},stock=1),True)
    own=own_ids(report);resources=of(report,'resource_change');items=of(report,'battle_item')
    assert len(items)==1 and items[0]['item']=='salve' and items[0]['used'] is True,items
    assert items[0]['parameter']==10 and items[0]['scope']=='all' and items[0]['percent']==50,items
    assert items[0]['remaining']==1 and report['itemRemaining']=={'salve':1},items
    assert len(resources)==3 and {e['target'] for e in resources}==own,resources
    hits=[e for e in report['trace'] if e['kind']=='attack']
    for e in resources:
        assert e['parameter']==10 and e['sourceItem']=='salve' and e['after']==e['max']==5000,e
        assert e['before']==prior_value(hits,e['target'],40,'target','hpAfter'),(e,hits)
    # The HP item touches HP only: no MP parameter change is attributed to it.
    assert not any(x['parameter']==11 for x in resources),resources


def check_generic_failed_stock():
    report=run_scenario(scenario([SALVE_INPUT(40)],items=SALVE,item_stock={'salve':0}),True)
    items=of(report,'battle_item')
    assert len(items)==1 and items[0]['used'] is False and items[0]['blocked']=='no stock',items
    assert items[0]['parameter']==10 and not of(report,'resource_change')
    assert report['itemRemaining']=={'salve':0}


def check_exported_identity():
    report=export_replay(scenario([HERB(54)]))
    unit_ids={u['unitId'] for u in report['units']}
    items=[e for e in report['events'] if e['kind']=='battle_item']
    resources=[e for e in report['events'] if e['kind']=='resource_change']
    assert items and items[0]['item']=='holy_herb' and items[0]['parameter']==11,items
    assert items[0]['targetUnitIds'] and all(u in unit_ids for u in items[0]['targetUnitIds']),items[0]
    assert resources and all(e['targetUnitId'] in unit_ids for e in resources),resources
    assert all(e['after']==e['max']==1000 and e['sourceItem']=='holy_herb' for e in resources),resources
    # The change lands mid-fight, strictly before the closing final snapshot the fold appends.
    assert max(e['tick'] for e in resources)==54<report['ticks'],report['ticks']


if __name__=='__main__':
    check_holy_herb_use();check_two_uses_chain();check_full_stock_no_use();check_failed_stock()
    check_clamped_all_full();check_generic_item_multi_unit();check_generic_failed_stock()
    check_exported_identity()
    report=dict(checks=['holy_herb multi-unit restore with exact before/after',
                        'two holy herb uses chain the engine value',
                        'full stock and no input emits nothing',
                        'failed stock records a blocked attempt only',
                        'all-full clamp emits no resource_change',
                        'generic item restores several units from their own live values',
                        'generic failed stock records a blocked attempt only',
                        'exported payload maps targetUnitId/targetUnitIds for the mid-fight fold'],
        midFightTick=54,issues=[])
    (EVIDENCE/'item-timeline-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
