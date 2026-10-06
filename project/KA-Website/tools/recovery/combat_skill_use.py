"""UseSkill orchestration for fighter targets (the MapChip branch is excluded).

Callbacks are synchronous and bound to the current caster, target and skill.
They must implement the separately recovered effects and RNG behavior. This
module is an integration layer, not a standalone battle simulator.
"""


def use_fighter_skill(skill, use_index, effects):
    if not effects['can_use'](use_index == 0):
        return False
    if use_index == 0:
        effects['pay_mp']()
        effects['balloon']()
    if skill['type'] in (1, 11):
        # Includes a per-projectile PlaySkillSe call in native FireProjectile.
        effects['fire_projectiles']()
    elif skill['category'] == 1:
        result = effects['cure']()
        effects['send'](27, [result])
        effects['sound']()
    elif skill['category'] != 0:
        effects['unsupported_category']()
    elif skill['type'] in (26, 27):
        for cell in effects['cells']('line' if skill['type'] == 26 else 'band'):
            effects['damage_cell'](cell)
            effects['cell_effect'](cell)
            effects['sound']()
    elif skill['type'] == 18:
        result = effects['reflect']()
        effects['send'](26, [result])
        effects['sound']()
    elif skill['flags'] & 0x40000:
        for cell in effects['cells']('band'):
            effects['buff_cell'](cell)
            effects['cell_effect'](cell)
            effects['sound']()
    else:
        result = effects['attack']()
        effects['send'](26, [result])
        effects['sound']()
    return True
