"""Recovered projectile numerical stages; no renderer or full fight simulation."""
from math import sqrt
from combat_resolution import f32
from combat_initial_state import i32


def isometric_trail_screen(position):
    """RenderSystem.ToScreenPos mode0 with camera=false (the projectile caller)."""
    from combat_resolution import native_float_to_int
    x,y,z=map(f32,position)
    return (native_float_to_int(f32(x-z)),
            native_float_to_int(f32(f32(f32(x+z)*.5)-y)))


def skill_projectile_destinations(target_position, area_range, cell_width=24, cell_height=24):
    """UseSkill target-area offsets applied to the target's actual position."""
    from combat_geometry import target_area
    x,y,z=target_position
    return [(f32(f32(x)+f32(i32(dx*cell_width))),f32(y),
             f32(f32(z)+f32(i32(dy*cell_height)))) for dx,dy in target_area(0,0,area_range)]


def parabola(height, length, frame):
    if length == 0: return 0
    t=f32(f32(frame)/f32(length))
    four_t=f32(t*4.0)
    value=f32(f32(four_t-f32(t*four_t))*f32(height))
    return max(-2**31,min(2**31-1,int(value)))


def magnitude(vector):
    x,y,z=map(f32,vector)
    return f32(sqrt(f32(f32(f32(x*x)+f32(y*y))+f32(z*z))))


def launch(start, end, speed):
    start,end=tuple(map(f32,start)),tuple(map(f32,end))
    speed=max(1,i32(speed))
    delta=tuple(f32(b-a) for a,b in zip(start,end))
    distance=magnitude(delta)
    return dict(start=start,end=end,speed=speed,
                height=min(100,max(20,int(f32(f32(distance*distance)/100.0)))),
                length=int(f32(distance/f32(speed))),frame=0,
                position=start,velocity=(0.0,0.0,0.0),impacted=False)


def projectile_update(state):
    """ProjectileSystem phase, before MoveSystem integrates velocity.
    Omits screen-space trail/rotation only; does not apply impact damage.
    """
    s=dict(state)
    if s['impacted']: raise ValueError('Projectile component already removed')
    delta=tuple(f32(b-a) for a,b in zip(s['start'],s['end']))
    distance=magnitude(delta)
    normal=tuple(f32(x/distance) for x in delta) if distance else (0.,0.,0.)
    frame,length=s['frame'],s['length']
    y=f32(s['start'][1]+f32(parabola(s['height'],length,frame)))
    next_y=f32(s['start'][1]+f32(parabola(s['height'],length,i32(frame+1))))
    dy=f32(next_y-y)
    s['position']=(s['position'][0],y,s['position'][2])
    # Native updates only X/Z speed. Y is set directly from the parabola.
    s['velocity']=(f32(normal[0]*f32(s['speed'])),s['velocity'][1],f32(normal[2]*f32(s['speed'])))
    impact=length<1
    if not impact:
        s['frame']=i32(frame+1)
        impact=dy<=0 and frame>=1 and y<=s['end'][1]
    if impact:
        s['position']=s['end'];s['velocity']=(0.,0.,0.);s['impacted']=True
    return s


def integrate_position(state):
    s=dict(state)
    s['position']=tuple(f32(p+v) for p,v in zip(s['position'],s['velocity']))
    return s


def fire_shared_projectile(entities, identity, start, end, speed, owner,
                           animate=False, attack_skill=None):
    """Launch on an existing entity; Position/Speed are not initialized here."""
    state = launch(start, end, speed)
    entities.remove_component(identity, 19)
    entities.add_component(identity, 39, {k: state[k] for k in
        ('start', 'end', 'speed', 'height', 'frame', 'length')} | {'owner': owner})
    entities.add_component(identity, 19, dict(type=7, offset_x=0., offset_y=0., offset_z=0.,
        scale_x=1., scale_y=1., angle=0, anchor=0, frame=0, duration=0,
        destroy_on_finish=False, loop=True, alpha=255))
    if animate:
        entities.add_component(identity, 12, (1, -1))
    if attack_skill is not None:
        entities.add_component(identity, 46, (attack_skill,))
    return state['length']


def tick_projectiles(members, entities, rotate, trail, send_impact):
    """Shared numerical flight and ordered callbacks, then impact dispatch.

    Rotation and trail allocation are explicit dependencies: both run before
    deferred impacts and may read live components. They cannot be omitted by a
    complete world runner. Projectile component removal follows each Send41.
    """
    pending = []
    for identity in members:
        c = entities.objects[identity]['components']
        projectile, position, velocity = c[39], c[0], c[1]
        delta = tuple(f32(b-a) for a,b in zip(projectile['start'],projectile['end']))
        distance = magnitude(delta)
        normal = tuple(f32(v/distance) for v in delta) if distance else (0.,0.,0.)
        old, length = projectile['frame'], projectile['length']
        position[1] = f32(projectile['start'][1]+f32(parabola(projectile['height'],length,old)))
        next_y = f32(projectile['start'][1]+f32(parabola(projectile['height'],length,i32(old+1))))
        velocity[0], velocity[2] = f32(normal[0]*f32(projectile['speed'])), f32(normal[2]*f32(projectile['speed']))
        dy = f32(next_y-position[1])
        if c[19] is not None:
            rotate(identity, dy)
        impact = c[39]['length'] < 1
        if not impact:
            frame = c[39]['frame']
            c[39]['frame'] = i32(frame+1)
            impact = dy <= 0 and frame >= 1 and c[0][1] <= c[39]['end'][1]
        if impact:
            c[0][:3] = c[39]['end']
            c[1][:] = (0.,0.,0.)
            entities.remove_component(identity, 19)
            pending.append(identity)
        elif old >= 1 and c[46] is not None:
            previous = (f32(c[0][0]-c[1][0]),
                f32(c[39]['start'][1]+f32(parabola(c[39]['height'],length,i32(old-1)))),
                f32(c[0][2]-c[1][2]))
            trail(identity, previous)
    for identity in pending:
        send_impact(identity)
        entities.remove_component(identity, 39)
    return pending


def create_projectile_trail(entities, identity, previous, to_screen):
    from combat_effects import create_combat_effect
    screen_x, screen_y = to_screen(previous)
    position = entities.objects[identity]['components'][0][:3]
    spec = dict(type=17, value1=screen_x, value2=screen_y, res=0, seb=0,
        depth=False, loop=False, max_frame=10, frame=0, parent=None,
        image=-1, animate=False, scale=100, position=position)
    # Duration is explicit, so the native constructor does not read resources.
    def unused_resource(*args):
        raise AssertionError('Projectile trails do not look up a clip duration')
    return create_combat_effect(spec, unused_resource, entities.allocate, entities.add_component)


def update_projectile_phase(members, states, on_impact, on_motion_complete=None):
    """Ordered trajectory pass, then synchronous impacts and component removal.

    `members` must preserve native subset slot order. The callback may create
    projectiles; these join the subset but do not travel until the next pass.
    Position integration remains a later world-system phase.
    """
    pending = []
    for entity in members:
        states[entity] = projectile_update(states[entity])
        if states[entity]['impacted']:
            # Native removes ModifyAnimation now, before updating the next
            # projectile; Send41 and Projectile removal are deferred.
            if on_motion_complete is not None:
                on_motion_complete(entity, states)
            pending.append(entity)
    for entity in pending:
        on_impact(entity, states, members)
        members.remove(entity)
    return pending


def process_projectile_impact(unit, effects):
    """Native BattleHelper impact dispatch; effects bind component/world access.

    Damage uses actual position. Terrain effects use the stored Cell, which may
    still describe the previous update. Damage eligibility does not gate cleanup.
    """
    if effects['destroyed'](unit) or not effects['has_cell'](unit):
        return
    owner = effects['owner'](unit)
    if effects['alive'](owner) and effects['fighter'](owner) and effects['has_attack'](unit):
        effects['damage_at_position'](unit, owner, effects['skill'](unit))
    category = effects['terrain_at_cell'](unit)
    if category is not None:
        effects['effect'](unit, 23 if category in (0, 9, 49) else 25, 10.)
    if not effects['has_attack'](unit):
        return
    skill = effects['skill'](unit)
    if skill is not None and skill['impactImg'] != -1:
        effects['effect'](unit, skill['impactImg'], 15.)
    if effects['texture'](unit) == 27 and effects['seb'](unit) == 27:
        effects['fade'](unit, dict(type=5, angle=180, frame=0, duration=20,
                                 destroy_on_finish=True, loop=False, alpha=255))
    else:
        effects['garbage'](unit, 1)
