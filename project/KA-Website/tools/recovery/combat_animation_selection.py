"""Combat clip subset of ChangeAnimation; Human vehicle/image composition remains outside scope.

PASS 16 COMMAND 16.5. This module selects an animation *identity* only: `combat_clip` returns the
SEB clip id (`base + direction`) that ChangeAnimation 0x165dea8 writes, and nothing here feeds
attack timing, reach, damage or projectile behaviour. Those come from their own recovered sources
(`UpdateAttacking` uses BATTLE_FRAME 11/20 regardless of the clip, and the ranged path reads
`EquipData.projectileFlag` / `.type`), so integrating a weapon motion cannot move combat mechanics.

`COMBAT_BEHAVIORS` is the set of behaviour ids this runner accepts. It contains

  * the non-attack behaviours the state entries request (0,1,2,3,5,7,15,28,30,32), and
  * every ordinary weapon behaviour the recovered equipment table actually uses
    (`WEAPON_MOTION_BEHAVIORS`): 4 sword/default, 9 spear, 10 gun, 11 bow, 12 torch/tool,
    16 scoop/shovel, 37 rake/hoe, plus 31 magic for skill-driven attacks.

EnterAttacking 0x15854c0 takes the behaviour straight from the equipped weapon
(`EquipData.motion`, +0x64), so a weapon motion outside `WEAPON_MOTION_BEHAVIORS` means a motion the
catalog does not contain; `check_combat_animation_selection.py` fails if that ever happens.
"""
import json
from combat_initial_state import i32
from combat_runtime_data import load_data

HUMAN_BASES=load_data('skill-combat-constants.json')['humanAnimationSebBases']['values']
# Recovered EquipData.motion values present in the equipment table (COMMAND 16.5 audit).
WEAPON_MOTION_BEHAVIORS={4,9,10,11,12,16,37}
# State-entry behaviours: waiting3, moving2, charging3, skill-exit3, damaging30, knockdown28,
# leaving has no ChangeAnimation call, and the low-HP/special remaps stay inside this set.
STATE_BEHAVIORS={0,1,2,3,4,5,7,10,11,15,28,30,31,32}
COMBAT_BEHAVIORS=STATE_BEHAVIORS|WEAPON_MOTION_BEHAVIORS

def combat_clip(behavior,human,direction,*,hp_rate=100,monster_size=0,special_human=False,on_vehicle=False):
    if on_vehicle:raise NotImplementedError('Vehicle animation requires source vehicle state')
    if behavior not in COMBAT_BEHAVIORS:raise NotImplementedError(f'Unintegrated animation behavior {behavior}')
    if human:
        if not 0<=behavior<len(HUMAN_BASES):
            raise NotImplementedError(f'No recovered human animation base for behavior {behavior}')
        base=HUMAN_BASES[behavior]
        if hp_rate<=20 and behavior in (2,3):base=104 if behavior==2 else 192
        elif behavior in (1,2) and special_human:base=196
    else:
        # AnimationSet.cctor allocates45 zeros, then sets element4 to16.
        base=(16 if behavior==4 else 0)+(4 if monster_size==3 else 0)
    return i32(base+direction)

def change_combat_animation(unit,behavior,**source):
    clip=combat_clip(behavior,source.pop('human'),unit['direction'],**source)
    unit['components'][2][1]=clip
    unit['components'][2][2]=0
    unit['components'][12][1]=-1 # All recovered combat callers pass restore=false.
    return clip
