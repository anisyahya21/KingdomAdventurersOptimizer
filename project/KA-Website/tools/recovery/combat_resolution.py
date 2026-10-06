"""Recovered combat primitives only; not a complete attack/action resolver."""
from combat_initial_state import i32, trunc_div
import struct
from math import isinf, isnan


_F32 = struct.Struct('<f')


def f32(value):
    # Same rounding as struct.pack/unpack with a precompiled Struct; the module-level calls only
    # look up a cached Struct first.
    return _F32.unpack(_F32.pack(value))[0]


def native_float_to_int(value):
    """Common recovered FCVTZS sequence with explicit +infinity override."""
    value = f32(value)
    return 0 if isnan(value) else -2147483648 if isinf(value) else max(-2147483648, min(2147483647, int(value)))


def battle_terrain_match(has_equipment, terrain, weapon_attribute):
    return bool(has_equipment and terrain != -1 and terrain == weapon_attribute)


def consume_invoking_attack(skills):
    """Attacker-side Attack tail, after current rolls; applies even on misses."""
    remaining=[]
    for skill_id, count in skills:
        count=i32(count-1)
        if count>0: remaining.append((skill_id,count))
    return remaining


def defender_attack_phase(hit, critical, damage, board, candidates, eligible,
                          invokes, reflect, enqueue_counter, pay_mp, balloon,
                          sound_roll):
    """Attack's defender-SkillComponent branch, after base damage calculation.

    Candidates are possessed skills filtered by flag32 in original slot order.
    Call only when the defender has SkillComponent. Eligibility and invocation
    callbacks execute separately and in order; reflection is synchronous.
    """
    if hit and 62 in board and 63 in board:
        board[63] = i32(board[63] - 1)
        if board[63] <= 0:
            for key in (62, 63, 64):
                board.pop(key, None)
    for skill in candidates:
        if not eligible(skill) or not invokes(skill):
            continue
        typ = skill['type']
        if typ == 18:
            reflect(skill, damage)
        elif typ == 20:
            enqueue_counter(skill)
        elif typ in (21, 24):
            if typ == 21:
                damage = max(1, trunc_div(i32(skill['value'] * damage), 100))
            pay_mp(skill)
            balloon(skill)
            if typ == 24:
                hit, critical, damage = False, False, 0
        sound_roll()
        break
    return hit, critical, damage


def attacker_invocation_phase(invoking, candidates, eligible, invokes, pay_mp,
                              balloon):
    """Attack tail for an attacker with SkillComponent, including initial misses.

    `invoking` is mutated before candidate eligibility; candidates are flag64
    possessed skills in original order. Newly activated effects have ten uses.
    """
    invoking[:] = consume_invoking_attack(invoking)
    for skill in candidates:
        if not eligible(skill) or not invokes(skill):
            continue
        pay_mp(skill)
        balloon(skill)
        if not any(sid == skill['id'] for sid, count in invoking):
            invoking.append((skill['id'], 10))
        break


def resolve_attack(attacker, target, skill, target_exists, lot_critical, lot_hit,
                   calculate_damage, terrain_match, defender_phase=None,
                   attacker_phase=None):
    """Recovered Attack orchestration with explicit stateful dependencies.

    This returns a result; HP application is the later synchronous event step.
    Defender/attacker callbacks are supplied only when SkillComponent exists.
    No range, HP or fighter-state recheck is added at release time.
    """
    if not target_exists(target):
        return dict(attacker=attacker, target=target, hit=False, critical=False,
                    damage=0, skill=skill)
    critical = bool(lot_critical(attacker))
    hit = critical or bool(lot_hit(attacker, target))
    damage = calculate_damage(critical, attacker, target,
                              skill is not None and skill['type'] == 1) if hit else 0
    if terrain_match(attacker):
        damage = trunc_div(i32(damage * 150), 100)
    if defender_phase is not None:
        hit, critical, damage = defender_phase(hit, critical, damage)
    if attacker_phase is not None:
        attacker_phase()
    return dict(attacker=attacker, target=target, hit=hit, critical=critical,
                damage=damage, skill=skill)


def apply_fighter_attack_results(results, target_exists, subtract_hp, change_state):
    """FighterSystem.OnAttack event subscriber, in result-array order.

    Each state change executes synchronously before the next result is read.
    Existence is rechecked here; range, current HP and damage positivity are not.
    Other event subscribers (including attack effects) are separate dependencies.
    """
    for result in results:
        target = result['target']
        if not result['hit'] or not target_exists(target):
            continue
        subtract_hp(target, result['damage'])
        change_state(target, 6)


def reflected_attack_result(attacker, target, skill, incoming_damage):
    """Type18 UseSkill payload AFTER eligibility, MP payment and balloon.

    Send event26 synchronously; do not feed this back through resolve_attack.
    """
    return dict(attacker=attacker, target=target, skill=skill, hit=True,
                critical=False,
                damage=max(1, trunc_div(i32(skill['value'] * incoming_damage), 100)))


def cure_result(caster, target, skill, effective_hp_maximum):
    return dict(caster=caster, target=target, skill=skill,
                amount=trunc_div(i32(skill['value'] * effective_hp_maximum), 100))


def add_raw_parameter(raw_value, amount, effective_maximum):
    """Parameter.Add; nonpositive maximum bypasses clamping entirely."""
    total = i32(raw_value + amount)
    value = max(0, min(effective_maximum, total)) if effective_maximum > 0 else total
    return value, i32(total - value)


def apply_fighter_cure_results(results, target_exists, add_hp, queue_position,
                              set_destination, change_state):
    for result in results:
        target = result['target']
        if not target_exists(target):
            continue
        add_hp(target, result['amount'])
        if result['skill']['type'] == 15:
            position = queue_position(target)
            if position is not None:
                set_destination(target, tuple(native_float_to_int(v) for v in position))
                change_state(target, 2)


def skill_invocation_rate(skill_type, invocation_level):
    rates=(100,60,30) if skill_type==15 else (80,50,30) if skill_type==2 else (36,18,9)
    if not 0 <= invocation_level < len(rates):
        raise ValueError('Native invocation index is out of bounds')
    return rates[invocation_level]


def decide_skill_invocation(skill_type, invocation_level, flags, math_rand_below):
    if flags & 8192: return True
    return math_rand_below(100) < skill_invocation_rate(skill_type,invocation_level)


def critical_rate(luck):
    """Graph.Easing with amplitude zero, preserving float32 operations."""
    if luck < 0:
        return 10
    if luck >= 100000:
        return 75
    if luck < 100:
        low, high, steps, progress = 10, 20, 99, luck
    elif luck < 1000:
        low, high, steps, progress = 21, 35, 899, luck - 100
    else:
        low, high, steps, progress = 36, 75, 98999, luck - 1000
    if progress >= steps:
        return high
    fraction = f32(f32(progress) / f32(steps))
    return max(low, min(high, int(f32(f32(fraction * (high - low)) + low))))


def modified_rate(rate, skill_value, *, evasion=False):
    multiplier = i32(100 - skill_value if evasion else 100 + skill_value)
    return trunc_div(i32(rate * multiplier), 100)


def random_below(raw_next_int, limit):
    if limit == 0:
        return 0
    raw = i32(raw_next_int)
    magnitude = i32(-raw) if raw < 0 else raw
    return magnitude - trunc_div(magnitude, limit) * limit


def random_range(raw_next_int, low, high):
    span = i32(high - low + 1)
    assert span != 0
    raw = i32(raw_next_int)
    return i32(raw - trunc_div(raw, span) * span + low)


def base_damage(attack, defense, next_int):
    attack_roll = random_range(next_int(), 80, 120)
    scaled_attack = max(1, trunc_div(i32(attack * attack_roll), 100))
    defense_roll = random_range(next_int(), 70, 90)
    damage = i32(scaled_attack - trunc_div(i32(defense * defense_roll), 100))
    if damage <= 5:
        return random_range(next_int(), 1, 10)
    return damage


def damage_from_parameters(critical, magical, monster, attacker_value,
                           defender_value, status, next_int):
    """Entity CalcDamage wrapper; getters take (parameter_id, missing_default)."""
    default = trunc_div(attacker_value(19, 0), 2) if magical and monster else 0
    attack = attacker_value(18 if magical else 13, default)
    defense = defender_value(14, 0)
    if status is not None and status['type'] == 66:
        defense = trunc_div(i32(i32(100 - status['value']) * defense), 100)
    if critical:
        defense = trunc_div(defense, 8)
    return base_damage(attack, defense, next_int)


def subtract_raw_parameter(raw_value, raw_maximum, amount):
    """Parameter.Sub: returns (new raw value, clamped-away underflow).

    Extra values/maxima are not subtracted or consulted by this operation.
    """
    difference = i32(raw_value - amount)
    value = difference if raw_maximum == 2147483647 else max(0, difference)
    return value, i32(value - difference)


def hit_rate(dexterity, agility, luck):
    value = i32(i32(i32(dexterity - trunc_div(agility, 5)) - trunc_div(luck, 5)) + 150)
    return max(5, min(97, value))


def attack_interval(agility):
    """Native double expression; host pow remains a documented libm dependency."""
    from math import pow
    agility = max(0, min(99999, i32(agility)))
    if agility == 0:
        # The original explicitly maps positive infinity to INT_MIN.
        return -2147483648
    return int((1.0 / (pow(agility / 25.0, 0.362) / 1.5)) * 20.0)


def equipment_parameter(pair, level):
    if pair is None:
        return 0
    base, growth = pair
    return i32(base + i32(growth * i32(level - 1)))


def affinity_contribution(value, affinity, *, alive_human=True):
    if not alive_human or affinity != 0:
        return value
    return max(1, value // 2) if value > 0 else 0


def xorshift_next(state):
    """One available JRandom backend, not a claim it is the active backend."""
    x, y, z, w = state
    t = (x ^ (x << 11)) & 0xffffffff
    n = (t ^ (t >> 8) ^ w ^ (w >> 19)) & 0xffffffff
    return (y, z, w, n), i32(n)


def linear_easing(low, high, steps, progress):
    """Native Graph.Easing amplitude zero; finite int32 inputs."""
    if progress < 0:
        return low
    if progress >= steps:
        return high
    fraction = f32(f32(progress) / f32(steps))
    value = int(f32(f32(fraction * f32(i32(high - low))) + f32(low)))
    return max(min(low, high), min(max(low, high), value))


def skill_mp_cost(minimum, maximum, average_level, *, monster=False):
    if monster or (minimum == 0 and maximum == 0):
        return minimum
    return linear_easing(minimum, maximum, 998, i32(average_level - 1))


def buff_accuracy(dexterity, target_luck, skill_type, skill_value):
    rate = linear_easing(25, 55, 1000, dexterity)
    if skill_type == 67:
        rate = i32(rate + skill_value)
    return i32(rate - linear_easing(5, 20, 1000, target_luck))


def buff_turns(intelligence):
    return linear_easing(2, 5, 1000, intelligence)


class SystemRandomState:
    """Recovered System.Random backend; caller must supply captured seed/state."""
    def __init__(self, seed):
        seed = i32(seed)
        magnitude = 2147483647 if seed == -2147483648 else abs(seed)
        previous = i32(161803398 - magnitude)
        self.values = [0] * 56
        self.values[55] = previous
        current = 1
        index = 0
        for _ in range(54):
            index = (index + 21) % 55
            self.values[index] = current
            difference = i32(previous - current)
            if difference < 0:
                difference = i32(difference + 2147483647)
            previous, current = current, difference
        for _ in range(4):
            for index in range(1, 56):
                value = i32(self.values[index] - self.values[1 + (index + 30) % 55])
                self.values[index] = i32(value + 2147483647) if value < 0 else value
        self.index, self.partner = 0, 21

    def next_int(self):
        self.index = self.index + 1 if self.index < 55 else 1
        self.partner = self.partner + 1 if self.partner < 55 else 1
        value = i32(self.values[self.index] - self.values[self.partner])
        if value == 2147483647:
            value -= 1
        elif value < 0:
            value = i32(value + 2147483647)
        self.values[self.index] = value
        return value
