"""Research-only combat cell order; no target eligibility or movement engine."""


def line_cells(x, y, dx, dy, shooting_range):
    return [(x + dx * i, y + dy * i) for i in range(1, shooting_range + 1)]


def battle_band(y, direction, depth):
    start_y = y - depth if direction == 0 else y + 1
    return [(x, row) for row in range(start_y, start_y + depth) for x in range(5)]


def target_area(x, y, size, include_center=True):
    """Size1 is the center only; outer Manhattan radius is size-1."""
    if size <= 0:
        return []
    cells = [(x, y)] if include_center else []
    for radius in range(1, size):
        ox, oy = 0, -radius
        for dx, dy in [(1, 1), (-1, 1), (-1, -1), (1, -1)]:
            for _ in range(radius):
                cells.append((x + ox, y + oy))
                ox += dx; oy += dy
    return cells


def battle_move_base(position, target, speed, previous_velocity=(0.0, 0.0)):
    """AISystem.MoveBase for finite nonnegative speed >=0.1; movement integration is separate."""
    from math import sqrt
    from combat_resolution import f32
    px, pz = map(f32, position)
    tx, tz = map(f32, target)
    speed = f32(speed)
    if not speed >= f32(0.1):
        raise ValueError('This port requires explicit speed >=0.1')
    dx, dz = f32(tx-px), f32(tz-pz)
    distance2 = f32(f32(dx*dx)+f32(dz*dz))
    if distance2 <= f32(speed*speed):
        return True, (tx,tz), tuple(map(f32,previous_velocity))
    distance = f32(sqrt(distance2))
    if distance < speed: speed = distance
    return False, (px,pz), (f32(f32(dx*speed)/distance),f32(f32(dz*speed)/distance))


def fighter_path(start, target):
    """Native GetPath: intersect vertical/horizontal lines, truncate two points.

    Keep float32 arithmetic even when the ordinary result is (startX,targetZ).
    Large coordinates can collapse the unit-length construction lines.
    """
    from math import copysign, isnan
    from combat_resolution import f32, native_float_to_int
    sx, sz = map(f32, start)
    tx, tz = map(f32, target)
    a0, a1 = f32(sx-sx), f32(tx-f32(tx+1.))
    b0, b1 = f32(f32(sz+1.)-sz), f32(tz-tz)
    det = f32(f32(a0*b1)-f32(b0*a1))
    c0 = f32(f32(sx*(-b0))-f32(a0*sz))
    c1 = f32(f32(tx*(-b1))-f32(a1*tz))
    def divide(n):
        if det == 0:
            if n == 0 or isnan(n):
                return float('nan')
            return copysign(float('inf'), copysign(1., n)*copysign(1., det))
        return f32(n/det)
    cross = (divide(f32(f32(a1*c0)-f32(a0*c1))),
             divide(f32(f32(b0*c1)-f32(c0*b1))))
    return [tuple(native_float_to_int(v) for v in cross),
            (native_float_to_int(tx), native_float_to_int(tz))]
