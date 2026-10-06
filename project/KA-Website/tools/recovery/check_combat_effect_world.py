"""Composition checks for effect allocation, mutable subsets and destruction.

These are integration checks of individually native-checked helpers, not direct
execution of a complete original world. Phase membership is from native Init.
"""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_collections import ComponentSubset
from combat_entities import CombatEntities,ComponentMap
from combat_effects import create_combat_effect, update_effect_phase
from combat_lifecycle import update_garbage

resources = json.loads((EVIDENCE / 'effect-resource-checks.json').read_text(encoding='utf-8'))
lengths = {r['id']: r['maxFrame'] for r in resources['resources']}
checks = 0
for depth, parent_dies, with_hole, birth_on_destroy in product((False, True), repeat=4):
    membership = []
    effect_set = ComponentSubset((38,), (4,),
        on_added=lambda u,t,c: membership.append(('added', u['id'], t)),
        on_removed=lambda u,t,c: membership.append(('removed', u['id'], t)))
    garbage_set = ComponentSubset((32,))
    destroyed = []
    world = CombatEntities(100, [effect_set, garbage_set])
    parent = world.allocate()
    spec = dict(type=0, value1=0, value2=0, depth=depth, frame=0, max_frame=0,
                loop=False, parent=parent, scale=100, res=28, seb=0, image=1,
                animate=True, position=(24., 10., 24.))
    def spawn(overrides=None):
        settings = dict(spec, **(overrides or {}))
        return create_combat_effect(settings, lambda r,s: lengths[s], world.allocate, world.add_component)
    if with_hole:
        temporary = spawn({'depth': False})
        world.destroy(temporary)
    subject = spawn()
    assert subject in garbage_set.members.indices
    assert (subject in effect_set.members.indices) == (not depth)
    assert world.objects[subject]['components'][32] == [9]
    born = []
    def on_destroyed(unit):
        destroyed.append((unit['id'], unit['flags'], None if unit['components'][38] is None else list(unit['components'][38])))
        assert unit['id'] in world.index.indices and not (unit['flags'] & 2)
        if birth_on_destroy and unit['id'] == subject:
            born.append(spawn({'parent': None, 'max_frame': 3}))
    world.on_destroyed = on_destroyed
    if parent_dies:
        world.destroy(parent)

    subject_death_tick = None
    born_lifetimes = []
    for tick in range(1, 13):
        effects=ComponentMap(world,38,fields=('type','value1','value2','depth','frame','max_frame','parent','scale'))
        positions=ComponentMap(world,0,window=(0,3))
        update_effect_phase(effect_set.members,effects,positions,world.is_destroyed,world.destroy)
        lifetimes=ComponentMap(world,32,scalar=0)
        update_garbage(garbage_set.members,lifetimes,world.destroy)
        if subject_death_tick is None and world.is_destroyed(subject):
            subject_death_tick = tick
        if born and not world.is_destroyed(born[0]):
            born_lifetimes.append((tick, world.objects[born[0]]['components'][32][0]))

    expected_death = 1 if parent_dies and not depth else 9
    assert subject_death_tick == expected_death
    removal = next(x for x in destroyed if x[0] == subject)
    assert removal[2][4] == (1 if expected_death == 1 else 0 if depth else 0)
    # Depth is removed before Effect during Destroy: transient subset re-entry.
    if depth:
        assert ('added', subject, 4) in membership
        assert ('removed', subject, 38) in membership
    if birth_on_destroy:
        # A birth in Effect cleanup participates in the later Garbage phase;
        # a birth during Garbage cleanup waits until the following phase.
        first = 2 if expected_death == 1 else 3
        assert born_lifetimes[0] == (expected_death, first), born_lifetimes
        assert world.is_destroyed(born[0])
    assert not list(effect_set.members) and not list(garbage_set.members)
    checks += 1

report = dict(composedEffectWorldCases=checks,
              scope='Shared mutable component views and entity IDs, original effect durations, Effect-without-Depth subset, parent cleanup and deferred garbage destruction with callback births',
              limitations=['Composition of native-checked helpers; no complete original-world execution, Animation/Render phases omitted'])
(EVIDENCE / 'effect-world-checks.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report))
