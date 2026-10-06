"""Native animation phase with explicit resource and global timing state."""
from combat_initial_state import i32


def signed_remainder(value, divisor):
    quotient = 0 if divisor == 0 else (abs(value)//abs(divisor)) * (-1 if (value < 0) != (divisor < 0) else 1)
    return i32(value-i32(quotient)*divisor)


def frame_resource(resource, auto_animation, common_frames_update):
    if not auto_animation and common_frames_update:
        resource['frame'] = signed_remainder(i32(resource['frame']+1), resource['max_frame'])


def update_animations(members, entities, resources, state, change_animation):
    enabled=state['enabled']
    if enabled:
        # Hoist the two gates out of the per-clip frame_resource call and inline `frame_resource`'s
        # body (its `i32` and `signed_remainder` helpers written out with their exact formulas).
        # This is the same advance condition, the same values and the same order, with two function
        # calls removed per clip row.
        advance=state['common_frames_update'] and not state['auto_animation']
        for manager in resources:
            if manager is not None:
                for resource in manager:
                    if resource is not None and advance:
                        divisor=resource['max_frame']
                        value=resource['frame']+1
                        value=((value+0x80000000)&0xFFFFFFFF)-0x80000000
                        quotient=0 if divisor==0 else (abs(value)//abs(divisor))*(-1 if (value<0)!=(divisor<0) else 1)
                        quotient=((quotient+0x80000000)&0xFFFFFFFF)-0x80000000
                        resource['frame']=((value-quotient*divisor+0x80000000)&0xFFFFFFFF)-0x80000000
    for identity in members:
        components = entities.objects[identity]['components']
        if not enabled:
            modifier = components[19]
            if modifier is None or modifier['type'] != 10:
                continue
        res, seb_id = components[2][:2]
        if res < 0 or seb_id < 0 or res >= len(resources):
            continue
        # Native only guards the outer resource index; invalid inner IDs throw.
        if resources[res] is None or not 0 <= seb_id < len(resources[res]):
            raise ValueError('Invalid native animation resource reference')
        resource = resources[res][seb_id]
        seb, animation = components[2], components[12]
        seb[2] = i32(seb[2]+animation[0])
        if resource is None:
            raise ValueError('Missing native animation resource')
        if seb[2] >= resource['max_frame']:
            seb[2] = signed_remainder(seb[2], resource['max_frame'])
            if animation[1] != -1:
                change_animation(identity, animation[1])
                # Reacquire: the synchronous callback may replace the component.
                entities.objects[identity]['components'][12][1] = -1
