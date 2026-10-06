"""Joined combat phase fixture; not a complete fight or original-world replay."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities,ComponentMap
from combat_collections import ComponentSubset
from combat_spatial import CellOccupancy,update_positions,update_cells,cell_key,update_heights
from combat_animation import update_animations
from combat_projectiles import fire_shared_projectile,tick_projectiles,create_projectile_trail
from combat_effects import create_combat_effect,update_effect_phase,tick_modifiers
from combat_lifecycle import update_garbage
from combat_states import enter_moving,update_moving,move_fighter,exit_moving,change_fighter_state
from combat_tick import update_fighters

checks=0;examples=[]
for speed,holes in product((5,10,100),(False,True)):
    move_set=ComponentSubset((0,1));cell_set=ComponentSubset((5,0,1));animation_set=ComponentSubset((2,12))
    projectile_set=ComponentSubset((39,0,1));effect_set=ComponentSubset((38,),(4,));garbage_set=ComponentSubset((32,))
    height_set=ComponentSubset((0,5,1),(11,39,38));modifier_set=ComponentSubset((19,))
    occupancy_set=ComponentSubset((5,));world=CombatEntities(100,[projectile_set,move_set,cell_set,height_set,modifier_set,animation_set,occupancy_set,effect_set,garbage_set])
    occupancy=CellOccupancy(world,64);occupancy_set.on_added=occupancy.added;occupancy_set.on_removed=occupancy.removed
    def create_positioned(position,cell):
        identity=world.allocate();world.add_component(identity,0,list(position)+[0.,0.,0.,None]);world.add_component(identity,1,[0.,0.,0.]);world.add_component(identity,5,list(cell));return identity
    if holes:
        temporary=create_positioned((0.,0.,0.),(0,0));world.destroy(temporary)
    fighter=create_positioned((0.,0.,96.),(0,4))
    world.add_component(fighter,2,[0,0,0,-1]);world.add_component(fighter,12,[1,-1]);world.add_component(fighter,14,[0])
    world.add_component(fighter,28,dict(board={4:0,5:2,6:0,7:0,8:0,13:24,14:96},long_board={},commands=[],path=[]))
    unit=world.fighter(fighter);events=[]
    def animate(u,m):u['components'][2][2]=0;events.append(('animation',tick,u['id'],m))
    tick=0;enter_moving(unit,animate)
    projectile=create_positioned((0.,0.,0.),(0,0));world.add_component(projectile,2,[0,0,0,-1])
    fire_shared_projectile(world,projectile,(0.,0.,0.),(0.,0.,48.),speed,fighter,True,1)
    resources=[[dict(frame=0,max_frame=10)]];impact_effects=[];impact_tick=None;trails=[]
    effects=ComponentMap(world,38,fields=('type','value1','value2','depth','frame','max_frame','parent','scale'))
    positions=ComponentMap(world,0,window=(0,3));lifetimes=ComponentMap(world,32,scalar=0)
    def state_change(u,n):
        change_fighter_state(u,n,{2:lambda f:exit_moving(f,lambda:3)},{3:lambda f:animate(f,3)})
    def state_update(u,state):
        if state==2:update_moving(u,move_fighter,lambda:3,lambda f:3,state_change)
    def rotate(identity,dy):
        # Rotation math is an explicit fixture stub. It does not touch movement.
        world.objects[identity]['components'][19]['angle']=0
    def trail(identity,previous):
        trails.append((tick,create_projectile_trail(world,identity,previous,lambda p:(123,678))))
    def impact(identity):
        global impact_tick
        impact_tick=tick;c=world.objects[identity]['components']
        events.append(('impact',tick,tuple(c[0][:3]),tuple(c[5])))
        spec=dict(type=0,value1=0,value2=0,depth=True,frame=0,max_frame=0,loop=False,parent=None,scale=100,res=0,seb=0,image=1,animate=True,position=(c[0][0],c[0][1]+10.,c[0][2]))
        effect=create_combat_effect(spec,lambda r,s:10,world.allocate,world.add_component);impact_effects.append(effect)
        world.add_component(identity,32,(1,))
    for tick in range(1,31):
        update_fighters([[unit],[]],2,state_update,lambda f:None)
        tick_projectiles(projectile_set.members,world,rotate,trail,impact)
        update_positions(move_set.members,world)
        update_cells(cell_set.members,world,64,24,24,occupancy.changed)
        height_access=dict(has_product=lambda i:False,has_treasure=lambda i:False,has_human=lambda i:False,
            human_flag32=lambda i:False,can_move_in_air=lambda i:False,map_chip=lambda x,y:None,null_or_destroyed=lambda t:True)
        update_heights(height_set.members,world,height_access)
        tick_modifiers(modifier_set.members,world,lambda a:0.,lambda:None)
        update_animations(animation_set.members,world,resources,dict(enabled=True,auto_animation=False,common_frames_update=True),lambda i,m:None)
        update_effect_phase(effect_set.members,effects,positions,world.is_destroyed,world.destroy)
        update_garbage(garbage_set.members,lifetimes,world.destroy)
        if tick==7:
            assert tuple(unit['position'])==(24.,0.,96.) and tuple(unit['cell'])==(1,4)
            assert unit['board'][7]==0 and unit['board'][5]==3
        if impact_tick==tick:
            assert world.is_destroyed(projectile)
            effect=impact_effects[0];assert world.objects[effect]['components'][32][0]==8
            assert world.objects[effect]['components'][2][2]==1
            assert effect in occupancy.buckets[cell_key(0,2,64)]
            assert projectile not in occupancy.buckets[cell_key(0,2,64)]
        for born,identity in trails:
            if born==tick:assert world.objects[identity]['components'][32][0]==9
    assert impact_tick is not None and all(world.is_destroyed(i) for i in impact_effects)
    assert all(world.is_destroyed(i) for t,i in trails)
    assert list(move_set.members)==[fighter] and list(cell_set.members)==[fighter]
    assert occupancy.buckets[cell_key(1,4,64)]==[fighter]
    assert all(not bucket for key,bucket in occupancy.buckets.items() if key!=cell_key(1,4,64))
    event=next(e for e in events if e[0]=='impact')
    if speed==5:assert event==('impact',10,(0.,0.,48.),(0,1))
    examples.append(dict(speed=speed,reusedSlots=holes,impactTick=impact_tick,position=list(event[2]),storedCell=list(event[3]),trails=len(trails)))
    checks+=1
report=dict(composedSharedWorldCases=checks,logicalTicks=checks*30,examples=examples,
    scope='Shared fighter movement, self/entity projectile fields, deferred impact, Move/Cell/occupancy/Height/Modifier/Animation/Effect/Garbage phases with real component membership and effect births',
    limitations=['Composition of native-checked routines, not execution of a complete original world','Next-state decision fixed to Charging; commands, damage, rotation math and rendering omitted explicitly','Flat absent-terrain fixture; original encounter terrain/source flags not supplied'])
(EVIDENCE/'shared-world-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
