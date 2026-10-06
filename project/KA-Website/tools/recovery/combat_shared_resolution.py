"""Bind checked attack subscribers and skill commands to shared components.

Inputs use entity IDs. Skill use, state handlers and attack effects remain
explicit dependencies; this module does not claim complete battle execution.
ParameterComponent.parameters represents its ParamSet dictionary, not a new
native field. Skill arrays retain their native field names and order.
"""
from combat_resolution import apply_fighter_attack_results, subtract_raw_parameter
from combat_commands import execute_skill_queue
from combat_events import dispatch_attack_event
from combat_resolution import critical_rate, hit_rate, modified_rate, random_below, damage_from_parameters
from combat_resolution import skill_mp_cost
from combat_parameters import average_training_level
from combat_initial_state import i32


def shared_parameter_value(entities, identity, parameter_id):
    """ParameterComponent.GetValue, distinct from effective Param.GetValue.

    Requires an explicit extras list. Reads linked ParamSets directly without
    recursively following their extras; duplicate and self links contribute.
    """
    component = entities.objects[identity]['components'][33]
    parameter = component['parameters'][parameter_id]
    value = i32(parameter['rawValue'] + parameter['extraValue'])
    index = 0
    while index < len(component['extras']):
        linked = component['extras'][index]
        if target_exists(entities, linked):
            params = entities.objects[linked]['components'][33]['parameters']
            if parameter_id in params:
                parameter = params[parameter_id]
                value = i32(value + i32(parameter['rawValue'] + parameter['extraValue']))
        index += 1
    return value


def shared_skill_cost(entities, identity, skill, has_monster, has_human):
    if skill['minMp'] == 0 and skill['maxMp'] == 0:
        return 0
    if has_monster(identity):
        return skill['minMp']
    human = has_human(identity)
    parameters = entities.fighter(identity)['parameters'] if human else None
    return skill_mp_cost(skill['minMp'], skill['maxMp'], average_training_level(parameters, human))


class SharedAttackMath:
    """Live component inputs with an explicitly selected math RNG stream.

    get_value(identity, parameter_id, default) must implement effective stats,
    including equipment and extras. Raw parameter values are not a substitute.
    """
    def __init__(self, entities, skill_rows, get_value, has_monster, next_int, labelled_next=None):
        self.entities, self.skill_rows = entities, skill_rows
        self.get_value, self.has_monster, self.next_int = get_value, has_monster, next_int
        self.labelled_next=labelled_next

    def draw(self,purpose,bound=None):
        return self.labelled_next(purpose,bound) if self.labelled_next is not None else self.next_int()

    def first_invoking(self, identity, skill_type):
        component = self.entities.objects[identity]['components'][51]
        if component is not None:
            for skill_id, remaining in component['invokingSkills']:
                skill = self.skill_rows[skill_id]
                if skill['type'] == skill_type:
                    return skill
        return None

    def critical(self, attacker):
        rate = critical_rate(self.get_value(attacker, 16, 0))
        skill = self.first_invoking(attacker, 23)
        if skill is not None:
            rate = modified_rate(rate, skill['value'])
        # Read-only roll record: `attack_result` reads `last_critical`/`last_hit` to publish the
        # pre-reaction roll without drawing a second time. Never changes the decision or the draws.
        self.last_critical = random_below(self.draw('critical',100), 100) < rate
        return self.last_critical

    def hit(self, attacker, target):
        rate = hit_rate(self.get_value(attacker, 19, 0), self.get_value(target, 15, 0),
                        self.get_value(target, 16, 0))
        skill = self.first_invoking(target, 22)
        if skill is not None:
            rate = modified_rate(rate, skill['value'], evasion=True)
        self.last_hit = random_below(self.draw('accuracy',100), 100) < rate
        return self.last_hit

    def damage(self, critical, attacker, target, magical):
        board = self.entities.fighter(target)['board']
        status = self.skill_rows[board[62]] if 62 in board else None
        return damage_from_parameters(critical, magical, self.has_monster(attacker),
            lambda key, default: self.get_value(attacker, key, default),
            lambda key, default: self.get_value(target, key, default), status, lambda:self.draw('damage_variation'))


def target_exists(entities, identity):
    return identity is not None and identity in entities.objects and not entities.is_destroyed(identity)


def apply_shared_attack_results(entities, results, change_state):
    def subtract_hp(identity, amount):
        parameter = entities.fighter(identity)['parameters'][10]
        parameter['rawValue'] = subtract_raw_parameter(
            parameter['rawValue'], parameter['rawMax'], amount)[0]

    apply_fighter_attack_results(results, lambda identity: target_exists(entities, identity),
                                 subtract_hp,
                                 lambda identity, state: change_state(entities.fighter(identity), state))


def dispatch_shared_attack(entities, results, process_effect, change_state):
    dispatch_attack_event(results, process_effect,
                          lambda batch: apply_shared_attack_results(entities, batch, change_state))


def execute_shared_skill_commands(entities, identity, resolve_skill, change_animation, use_skill,
                                  on_release=None):
    fighter = entities.fighter(identity)
    execute_skill_queue(fighter['commands'], resolve_skill, fighter.command_animation(),
                        lambda behavior: change_animation(fighter, behavior),
                        lambda command, index: use_skill(fighter, command, index),
                        on_release)
