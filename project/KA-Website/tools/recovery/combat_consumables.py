"""Recovered battle-team recovery item path; effective parameter access supplied."""
from combat_initial_state import i32,trunc_div

RECOVERY_CATEGORY=3
# ExecuteItem 0x167b834 outer table 0x77256a (bonusCategory-3, 3..9) selects the recovery switch at
# 0x167bb84 for bonusCategory3; its inner table 0x772584 (bonusType0..5) maps type -> parameter and
# recipient scope: even types call RecoveryResidentsParameter 0x167aea0 with the supplied resident
# array, odd types call RecoveryResidentParameter 0x167acac with the single `resident` argument.
RECOVERY_PARAMETERS={0:10,1:10,2:11,3:11,4:12,5:12}
RECOVERY_ALL_RESIDENTS=(0,2,4)
RECOVERY_WITHDRAWN_TYPES=()


def bonus_value(item,draw):
    """ItemData.GetRandomBonusValue 0x162b344: `min` when min>=max, else math Random.Rand(min,max).

    `draw` returns the next raw kairo.unity.math.Random NextInt (0x145ff98 Rand is
    `raw % (high-low+1) + low` with truncating remainder). Native draws this value once per
    dispatched item, after the stock/CheckItem gate and before the recovery branch, so a failed
    recovery still consumes the draw when min<max. Holy Herb has min==max==100 and draws nothing.
    """
    low,high=item['bonusMinValue'],item['bonusMaxValue']
    if low>=high:return low
    raw=i32(draw());span=i32(high-low+1)
    if span==0:raise ValueError('Empty item bonus range')
    return i32(raw-trunc_div(raw,span)*span+low)


def recover_item_parameter(targets,parameter_id,percent,rate,maximum,
                           human_with_parameters,add_value):
    """RecoveryResidentsParameter 0x167aea0 / RecoveryResidentParameter 0x167acac.

    Each member is visited in order (no early exit): CanRecoveryResidentParameter 0x167ad84 is
    rate<100 and amount=trunc(i32(max*percent)/100); _Parameterup 0x167adf8 applies AddValue only
    to a Human that has the Parameter. Aggregate success is the OR of the member tests, so a
    non-Human can report success without gaining anything and an all-full group reports failure.
    """
    success=False
    for identity in targets:
        if rate(identity,parameter_id)<100:
            success=True
            amount=trunc_div(i32(maximum(identity,parameter_id)*percent),100)
            if human_with_parameters(identity):add_value(identity,parameter_id,amount)
    return success


def recovery_parameters(item):
    """ExecuteItem recovery classification, or raise: unsupported items fail closed."""
    category=item.get('bonusCategory');bonus_type=item.get('bonusType')
    if category!=RECOVERY_CATEGORY or bonus_type not in RECOVERY_PARAMETERS:
        raise ValueError('Unsupported item effect; only bonusCategory3 bonusType0..5 are recovered')
    return RECOVERY_PARAMETERS[bonus_type],bonus_type in RECOVERY_ALL_RESIDENTS


def use_battle_item(item,residents,has_stock,draw,rate,maximum,human_with_parameters,add_value,
                    spend_one,resident=None,state=None):
    """One battle item dispatch (ExecuteItem 0x167b834) with a finite stock gate (CheckItem).

    `residents` is the target array the caller supplies: the native battle touch path passes the
    own-team array already filtered by 0x14f34ac (fighter states 7/8 excluded) and a null single
    target, so only the all-residents types are reachable in battle. Returns a record for the
    trace; the caller owns the stock counter through `has_stock`/`spend_one`.
    """
    parameter_id,all_residents=recovery_parameters(item)
    if all_residents:
        # 0x14f34ac filtered the caller's own-team array before ExecuteItem.
        targets=[identity for identity in residents if state is None or state(identity) not in (7,8)]
    else:
        if resident is None:raise ValueError('Single-resident recovery item needs an explicit resident')
        targets=[resident]
    record=dict(parameter=parameter_id,scope='all' if all_residents else 'single',
                targets=list(targets),used=False,percent=None)
    if not has_stock():
        record['blocked']='no stock'
        return record
    percent=bonus_value(item,draw)
    record['percent']=percent
    record['used']=recover_item_parameter(targets,parameter_id,percent,rate,maximum,
                                          human_with_parameters,add_value)
    if record['used']:spend_one()
    return record


def use_battle_holy_herb(roster,state,has_stock,rate,maximum,
                        human_with_parameters,add_value,spend_one):
    """Item31 Holy Herb (bonusCategory3/bonusType2 -> parameter11 at percent100)."""
    targets=[identity for identity in roster if state(identity) not in (7,8)]
    if not has_stock():return False
    success=recover_item_parameter(targets,11,100,rate,maximum,human_with_parameters,add_value)
    if success:spend_one()
    return success

