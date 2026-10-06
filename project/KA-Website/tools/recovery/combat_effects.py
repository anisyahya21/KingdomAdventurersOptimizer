"""Combat-relevant lifecycle of native animation modifiers; no renderer."""
from combat_initial_state import i32, trunc_div
from combat_resolution import f32, native_float_to_int


def departure_effect_spec(position):
    """Knockdown/Leaving image6 through World.CreateEffect; original effectSebIds[6]=0."""
    return dict(type=0,value1=0,value2=0,res=28,seb=0,
                position=(position[0],f32(position[1]+10.),position[2]),scale=100,
                image=6,depth=True,max_frame=0,frame=0,loop=False,parent=None,animate=True)


def cell_skill_effect_spec(skill,cell):
    """SkillSystem.CreateSkillEffect 15e0a14; special map cell dimensions24x24."""
    return dict(type=0,value1=0,value2=0,res=28,seb=skill['seb'],
                position=(f32(i32(cell[0]*24)),30.,f32(i32(cell[1]*24))),scale=100,
                image=skill['img'],depth=True,max_frame=0,frame=0,loop=False,parent=None,animate=True)


def skill_balloon_spec(skill_id,position,ally):
    """SkillSystem.CreateSkillBalloon 0x15e0620; explicit 20-frame lifetime."""
    return dict(type=15,value1=skill_id,value2=int(not ally),res=6,seb=160,
                position=(position[0],f32(position[1]+50.),position[2]),scale=100,
                image=-1,depth=False,max_frame=20,frame=0,loop=False,parent=None,animate=True)


def healing_effect_spec(amount,position):
    """Special Battle.OnCure 0x14f28a4, before FighterSystem's HP subscriber."""
    return dict(type=1,value1=amount,value2=0,res=4,seb=279,
                position=(position[0],f32(position[1]+20.),position[2]),scale=100,
                image=-1,depth=False,max_frame=30,frame=0,loop=False,parent=None,animate=True)


def attack_effect_specs(result, position, attacker_ally):
    """ProcessAttackEffect's fighter paths; excludes MapChip recipients."""
    def spec(typ,value,res,seb,y,scale=100,image=-1,depth=False,duration=30):
        return dict(type=typ,value1=value,value2=0,res=res,seb=seb,position=(position[0],f32(position[1]+y),position[2]),
            scale=scale,image=image,depth=depth,max_frame=duration,frame=0,loop=False,parent=None,animate=True)
    if not result['hit']:return [spec(12,0,6,126,15.)]
    critical=result['critical'];scale=150 if critical else 100
    effects=[spec(0,0,28,0,10.,scale,1 if critical else 2,True,0)]
    if critical:effects.append(spec(12,9,6,126,15.))
    effects.append(spec(1,result['damage'],4,(237 if critical else 78) if attacker_ally else 112,20.,scale))
    return effects


def create_combat_effect(spec, resource_frames, allocate, add_component):
    """World.CreateEffect component insertion order, including optional cell.

    Resource frame counts must come from the original resource data. Allocation
    and each component add can synchronously update subset/cell membership.
    """
    duration = spec['max_frame']
    if duration == 0 and not spec['loop']:
        duration = i32(resource_frames(spec['res'], spec['seb']) - 1)
    entity = allocate()
    add_component(entity, 'Effect', (spec['type'], spec['value1'], spec['value2'],
                  spec['depth'], spec['frame'], duration, spec['parent'], spec['scale']))
    position = tuple(f32(v) for v in spec['position'])
    add_component(entity, 'Position', position + (0., 0., 0., None))
    add_component(entity, 'Speed', (0., 0., 0.))
    add_component(entity, 'Seb', (spec['res'], spec['seb'], spec['frame'], -1))
    if spec['depth']:
        add_component(entity, 'Cell', (trunc_div(native_float_to_int(position[0]), 24),
                                      trunc_div(native_float_to_int(position[2]), 24)))
        add_component(entity, 'Depth', (0, 40))
    if not spec['loop']:
        add_component(entity, 'Garbage', (duration,))
    if spec['image'] != -1:
        add_component(entity, 'Image', (spec['image'], spec['res'], -1, -1, -1, -1))
    if spec['animate']:
        add_component(entity, 'Animation', (1, -1))
    return entity


def update_combat_effect(effect, position):
    """Reachable impact/text/balloon/recovery/trail updates; separate from Seb."""
    typ = effect['type']
    if typ == 0:
        if not effect['depth']:
            old = effect['frame']
            frame = i32(old + 1)
            if old >= 0:
                duration = effect['max_frame']
                quotient = ((abs(frame) // abs(duration)) *
                            (-1 if (frame < 0) != (duration < 0) else 1)) if duration else 0
                frame = i32(frame - quotient * duration)
            effect['frame'] = frame
    elif typ in (1, 12, 15, 17, 19):
        effect['frame'] = i32(effect['frame'] + 1)
        if typ in (1, 19):
            position[1] = f32(f32(position[1]) + 1.)
    else:
        raise NotImplementedError(f'Effect type {typ} has not been composed')


def update_effect_phase(members, effects, positions, parent_destroyed, destroy):
    pending = []
    for entity in members:
        effect = effects[entity]
        if effect['parent'] is not None and parent_destroyed(effect['parent']):
            pending.append(entity)
        # Native still updates an effect queued for destruction by its parent.
        update_combat_effect(effect, positions[entity])
    for entity in pending:
        destroy(entity)


def update_hit_modifier(modifier):
    """Type4 hit shake; returns whether it enters the deferred completion list.

    Completion subsequently destroys its entity if destroy_on_finish is set;
    otherwise it removes the component and requests a static-render update.
    Normal combat hits create this with duration6, frame0 and both flags false.
    """
    if modifier['type'] != 4:
        raise NotImplementedError('Only the recovered hit-shake modifier is modeled')
    if not modifier['loop'] and modifier['duration'] == 0:
        return True
    frame = modifier['frame']
    if frame < 0:
        modifier['frame'] = i32(frame + 1)
        return False
    offset = f32(trunc_div(i32(modifier['duration'] - frame), 2))
    modifier['offset_x'] = offset if frame & 1 else -offset
    modifier['frame'] = i32(frame + 1)
    if frame >= modifier['duration']:
        if modifier['loop']:
            modifier['frame'] = 0
        else:
            return True
    return False


def update_projectile_modifier(modifier):
    """Type7 has no switch-body transform; generic frame/lifetime still runs."""
    if modifier['type'] != 7:
        raise NotImplementedError('Expected projectile modifier type7')
    if not modifier['loop'] and modifier['duration'] == 0:
        return True
    old = modifier['frame']
    modifier['frame'] = i32(old + 1)
    if old >= 0 and old >= modifier['duration']:
        if modifier['loop']:
            modifier['frame'] = 0
        else:
            return True
    return False


def graph_easing_int(start, end, duration, frame, curve, sin_degrees):
    """Graph.Easing integer overload; sin provider keeps libm provenance explicit."""
    if frame < 0:
        return start
    if frame >= duration:
        return end
    t = f32(f32(frame)/f32(duration))
    pi = f32(3.1415927410125732)
    angle = f32(f32(f32(t*pi)*180.)/pi)
    eased = f32(t + f32(f32(f32(curve)/f32(314.1592712402344))*f32(sin_degrees(angle))))
    value = native_float_to_int(f32(f32(eased*f32(i32(end-start)))+f32(start)))
    return min(max(start, end), max(min(start, end), value))


def update_projectile_fade(modifier, sin_degrees):
    if modifier['type'] != 5:
        raise NotImplementedError('Expected projectile fade type5')
    if not modifier['loop'] and modifier['duration'] == 0:
        return True
    old = modifier['frame']
    if old >= 0:
        modifier['alpha'] = graph_easing_int(255, 0, modifier['duration'], old, -100, sin_degrees)
    modifier['frame'] = i32(old + 1)
    if old >= 0 and old >= modifier['duration']:
        if modifier['loop']:
            modifier['frame'] = 0
        else:
            return True
    return False


def tick_modifiers(members, entities, sin_degrees, static_render_dirty):
    """Checked combat modifier types; complete all updates before cleanup."""
    pending = []
    for identity in members:
        modifier = entities.objects[identity]['components'][19]
        typ = modifier['type']
        if typ == 4:
            complete = update_hit_modifier(modifier)
        elif typ == 5:
            complete = update_projectile_fade(modifier, sin_degrees)
        elif typ == 7:
            complete = update_projectile_modifier(modifier)
        else:
            raise NotImplementedError(f'Modifier type {typ} has not been composed')
        if complete:
            pending.append(identity)
    for identity in pending:
        # Native reacquires the component after preceding synchronous callbacks.
        modifier = entities.objects[identity]['components'][19]
        if modifier['destroy_on_finish']:
            entities.destroy(identity)
        else:
            if modifier['type'] == 8:
                # A preceding cleanup callback can replace the pending modifier.
                entities.add_component(identity, 30, ())
            entities.remove_component(identity, 19)
            static_render_dirty()
