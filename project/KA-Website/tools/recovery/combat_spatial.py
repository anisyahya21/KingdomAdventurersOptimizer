"""Recovered movement and cell phases over shared component storage.

Callers supply the native subset order. Cell notifications run synchronously
after all cell writes; occupancy subscribers remain separate.
"""
from combat_initial_state import i32, trunc_div
from combat_resolution import f32, native_float_to_int


def can_move_in_air(entities, identity, vehicles):
    c = entities.objects[identity]['components']
    human = c[18]
    if human is None or not (human['flag'] & 4):
        return False
    vehicle_id = c[28]['board'].get(55, -1)
    if vehicle_id == -1:
        return False
    if not 0 <= vehicle_id < len(vehicles):
        raise IndexError('Native vehicle array index is out of bounds')
    return bool(vehicles[vehicle_id]['flags'] & 1)


def update_positions(members, entities):
    # MoveSystem requires Position and Speed. It does not inspect destroyed/HP.
    for identity in members:
        components = entities.objects[identity]['components']
        position, speed = components[0], components[1]
        for axis in range(3):
            position[axis] = f32(position[axis] + speed[axis])
        parent = position[6]
        if parent is not None:
            parent_position = entities.objects[parent]['components'][0]
            if parent_position is not None:
                for axis in range(3):
                    position[axis] = f32(parent_position[axis] + position[axis+3])


def battle_cell(x, z, width, height):
    if width <= 0 or height <= 0:
        raise ValueError('Battle cell dimensions must be positive')
    return i32(trunc_div(x, width)), i32(trunc_div(z, height))


def cell_key(x, y, map_width):
    return i32(x+i32(i32(y*map_width)*2))


class CellOccupancy:
    """CellCulling's retained buckets and ordered entity-reference lists."""
    def __init__(self, entities, map_width):
        self.entities, self.map_width = entities, map_width
        self.buckets = {}

    def added(self, unit, component_type, component):
        if component_type == 5:
            key = cell_key(*component, self.map_width)
            self.buckets.setdefault(key, []).append(unit['id'])

    def removed(self, unit, component_type, component):
        if component_type == 5:
            self._remove(cell_key(*component, self.map_width), unit['id'])

    def _remove(self, key, identity):
        bucket = self.buckets.get(key)
        if bucket is not None and identity in bucket:
            bucket.remove(identity)  # Native List.Remove removes only the first.

    def changed(self, identity, old_key):
        self._remove(old_key, identity)
        cell = self.entities.objects[identity]['components'][5]
        key = cell_key(*cell, self.map_width)
        self.buckets.setdefault(key, []).append(identity)


def update_cells(members, entities, map_width, cell_width, cell_height,
                 send_change, calculate=battle_cell):
    pending = []
    for identity in members:
        components = entities.objects[identity]['components']
        position = components[0]
        x, z = calculate(native_float_to_int(position[0]),
                         native_float_to_int(position[2]), cell_width, cell_height)
        cell = components[5]
        if cell[0] != x or cell[1] != z:
            old_key = cell_key(*cell, map_width)
            cell[0], cell[1] = x, z
            pending.append((identity, old_key))
    for identity, old_key in pending:
        send_change(identity, old_key)


def update_heights(members, entities, access):
    """HeightSystem's terrain support rules, after Cell and before animation.

    Map/entity data access is explicit. Required/excluded component membership
    is configured by the caller from HeightSystem.Init.
    """
    for identity in members:
        if access['has_product'](identity) or access['has_treasure'](identity):
            continue
        if access['has_human'](identity) and access['human_flag32'](identity):
            continue
        if access['can_move_in_air'](identity):
            continue
        cell = entities.objects[identity]['components'][5]
        tile = access['map_chip'](cell[0], cell[1])
        if access['null_or_destroyed'](tile):
            entities.objects[identity]['components'][0][1] = 0.
            continue
        if access['data'](tile)['flags'] & 0x4000000:
            continue
        position = entities.objects[identity]['components'][0]
        tile_y = access['position'](tile)[1]
        data = access['data'](tile)
        position[1] = f32(tile_y+f32(data['height']))
        index = 0
        # The native loop reads the live tile Cell list length on each iteration.
        while index < len(access['overlays'](tile)):
            overlay = access['overlays'](tile)[index]
            data = access['data'](overlay)
            if data is not None:
                if not (access['has_human'](identity) and data['category'] == 7):
                    if not (data['flags'] & 0x4000000):
                        position = entities.objects[identity]['components'][0]
                        position[1] = f32(position[1]+f32(data['height']))
            index += 1


def update_facing(members, entities, render_mode, can_move_in_air, has_human,
                  human_images, atan2, remainder):
    """RotateSystem direction, SEB selection and image-array reference swaps.

    Double atan2/remainder providers keep the platform-libm boundary explicit.
    Ground movement has no trigonometric call and preserves direction on ties.
    """
    for identity in members:
        c = entities.objects[identity]['components']
        velocity, direction = c[1], c[14]
        x, z = velocity[0], velocity[2]
        if x != 0. or z != 0.:
            if can_move_in_air(identity):
                angle = atan2(-float(z), float(x))
                if angle < 0.:
                    angle += 6.283185307179586
                angle = remainder(angle*360./6.283185307179586-45.+360., 360.)
                if 0. <= angle < 90.:
                    direction[0] = 0
                elif 270. <= angle < 360.:
                    direction[0] = 1
                elif 180. <= angle < 270.:
                    direction[0] = 2
                elif 90. <= angle < 180.:
                    direction[0] = 3
            elif z < 0. and abs(z) > abs(x):
                direction[0] = 0 if render_mode == 0 else 2
            elif x > 0. and abs(x) > abs(z):
                direction[0] = 1
            elif z > 0. and abs(z) > abs(x):
                direction[0] = 2 if render_mode == 0 else 1
            elif x < 0. and abs(x) > abs(z):
                direction[0] = 3
        c[2][1] = i32(trunc_div(c[2][1],4)*4+direction[0])
        if has_human(identity):
            res_ids, tex_ids = human_images(identity, direction[0] in (1,2))
            c[7]['res_ids'], c[7]['tex_ids'] = res_ids, tex_ids
