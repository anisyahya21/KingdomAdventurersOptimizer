"""Recovered `BattleSystem.Finish` 0x14ed864 special branch, separate from world collection.

Static decode (frozen `fb834373...30208`) plus the native fixture
`check_combat_special_settlement.py` (`special-settlement-checks.json`, winner x count 0..256):

    battle+0x65 (special) != 0 and winner (battle+0x50) == 1
        SpecialBossData.AddDefeatCount(1)        0x1632ab0, once per Finish call
        filter prizes_ (battle+0x58) by type == 0  0x14f3160
        for each surviving entry, in list order:
            FireTreasure(sourceBoss, treasureId)    0x14f0118
    then: request battle form Pop 0x237d768  (deferred finish request)
    then: destroy the source boss           0x1473e30

`AddGuerrillaPrize` 0x14ed618 appends one entry per eligible Leaving; nothing in this chain
deduplicates or clamps the count (verified through 256 entries). A non-special battle or a
winner other than 1 dispatches nothing and does not increment the defeat count, but still Pops
the form and destroys the source boss.

Boundary: this models the *dispatch* of the chest entity. It is not an inventory receipt and not
EXP: `FireTreasure` -> `CreateTreasure` spawns a treasure; opening it later runs the
TreasureBoxResult arms in `combat_receipt.py`. Confirmation/EXP/teardown stay outside.
"""

SPECIAL_FLAG_OFFSET = 0x65
WINNER_OFFSET = 0x50
PRIZES_OFFSET = 0x58
TYPE_ZERO_FILTER = '0x14f3160'
FIRE_TREASURE = '0x14f0118'
ADD_DEFEAT_COUNT = '0x1632ab0'
POP_BATTLE_FORM = '0x237d768'
DESTROY_SOURCE_BOSS = '0x1473e30'


def is_dispatched_type(prize):
    """`0x14f3160` == 0 exactly: only `Prize.type == 0` entries reach FireTreasure."""
    return (prize.get('type', 0) if isinstance(prize, dict) else 0) == 0


def special_finish(winner, prizes, *, special=True, defeat_count, fire_treasure,
                   pop_battle_form, destroy_source_boss):
    """Execute the recovered Finish order; returns the dispatched prize entries in order.

    Callbacks are synchronous and own their side effects (RNG draws happen inside
    `fire_treasure`, exactly as native CreateTreasure draws at dispatch time).
    """
    dispatched = []
    if special and winner == 1:
        defeat_count()
        for prize in prizes:
            if is_dispatched_type(prize):
                fire_treasure(prize)
                dispatched.append(prize)
    pop_battle_form()
    destroy_source_boss()
    return dispatched


def finish_report(winner, prizes, dispatched):
    """Split the lifecycle: outcome, queued awards, dispatched awards, collection.

    Nothing here may be read as inventory: only the receipt ledger's confirmed modelled
    storage write does that, and the chest has not been opened at Finish time.
    """
    return dict(
        fightOutcome=dict(winner=winner, verdict=('own team annihilated' if winner == 2 else
                                                  'opponent annihilated' if winner == 1 else 'unresolved'),
                          completionCondition='BattleSystem.IsAnnihilated: every fighter of one team is in Leaving state 8',
                          dispatchGate='special battle flag and winner==1'),
        queuedChestAwards=dict(count=len(prizes), entries=[dict(prize) for prize in prizes]),
        dispatchedChestAwards=dict(count=len(dispatched), entries=[dict(entry) for entry in dispatched],
                                   note='FireTreasure created a chest entity per type0 entry, in queue order; '
                                        'no deduplication or count clamp through 256 entries'),
        inventoryCollection=dict(state='not modelled', count=None,
                                 note='Opening the dispatched chest runs the TreasureBoxResult arms; storage/'
                                      'inventory and world collection of ground spawns stay outside this report'),
        exp='not modelled')
