"""Effective fighter parameters; excludes facility-only surrounding effects."""
from combat_initial_state import i32
from combat_resolution import equipment_parameter, affinity_contribution
from combat_initial_state import trunc_div


HUMAN_TRAINING_PARAMETERS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)


def average_training_level(parameters, human):
    """Param.AverageHumanParamLevels, using original human parameter group.

    Missing human parameters are an incomplete input, not level-one defaults.
    trainingLevel is the portable name for Parameter.level at native +0x24.
    """
    if not human:
        return 1
    total = 0
    for key in HUMAN_TRAINING_PARAMETERS:
        total = i32(total + parameters[key]['trainingLevel'])
    return max(1, trunc_div(total, len(HUMAN_TRAINING_PARAMETERS)))


def equipment_contribution(equipment, parameter_id, selected_level, *, affinity=1, human=False):
    index=i32(parameter_id-10)
    if not 0 <= index < len(equipment['parameters']):return 0
    level=selected_level if selected_level>0 else equipment['level']
    value=equipment_parameter(equipment['parameters'][index],level)
    return affinity_contribution(value,affinity,alive_human=human)


EQUIP_MASTER_SKILL_TYPE = 48


def equip_master_lifted_types(skill_rows):
    """Equipment types a possessed SkillData.TYPE_EQUIP_MASTER row lifts to affinity 1.

    Native JobData.GetAffinity 0x16208d0 indexes the job group affinity array by EquipData.type
    and, only when the indexed value is -1 or 0, searches the possessed skills for a
    TYPE_EQUIP_MASTER (48) row whose `value` equals that type (SkillComponent.SearchSkillsTypesOf
    with the literal 0x30, Enumerable.Any predicate 0x162cc34). A match returns 1; values already
    >= 1 are returned unchanged. The row is permanently active and never invoked.
    """
    return {row["value"] for row in skill_rows if row["type"] == EQUIP_MASTER_SKILL_TYPE}


def equipment_affinity(equipment_type, declared_affinity, lifted_types):
    """Declared job affinity after the native GetAffinity EQUIP_MASTER override (-1/0 -> 1)."""
    if declared_affinity in (-1, 0) and equipment_type in lifted_types:
        return 1
    return declared_affinity


def equipment_level(slot, equipment, owner_levels, human, ally):
    if owner_levels is not None: return owner_levels[slot]
    return equipment['pvpLevel'] if human and not ally else equipment['level']


def fighter_parameter(parameter_id, parameter, equipment, contribution,
                      *, owner_levels=None, human=False, ally=False,
                      exists=True, maximum=False, default=0):
    """contribution(row, parameter_id, selected_level) includes job affinity.

    Missing parameter returns the supplied default without adding equipment.
    Input raw/extra fields are those preserved by native clone serialization.
    """
    if not exists or parameter is None: return default
    bounded=parameter['rawMax'] != 2147483647
    if maximum and not bounded: return 2147483647
    value=i32(parameter['rawMax']+parameter['extraMax']) if maximum else i32(parameter['rawValue']+parameter['extraValue'])
    if maximum == bounded:
        for slot,row in enumerate(equipment):
            level=equipment_level(slot,row,owner_levels,human,ally)
            value=i32(value+contribution(row,parameter_id,level))
    if maximum or parameter_id==25:return value
    upper=fighter_parameter(parameter_id,parameter,equipment,contribution,owner_levels=owner_levels,
                            human=human,ally=ally,maximum=True)
    return max(0,min(upper,value))
