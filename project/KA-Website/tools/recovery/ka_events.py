"""Normalize canonical Python trace events and native events into one comparable form.

Rust does not copy Python's JSON event dicts; both sides are projected onto the same semantic
record so ordering and content can be compared exactly:

    (kind, actor_identity, target_identity, payload_tuple)

Actor/target are canonical entity identities, never roster slots. The comparison set is the event
kinds the ported fighter/action mechanics produce. Native records with no canonical observable
(KA_EVENT_HP 10, KA_EVENT_DESTROY 9) and Python's rng draw trace are excluded on both sides: they are
diagnostics, not observables, and RNG parity is covered by the full-state comparison.

Native code -> canonical kind:
    1  state             -> state
    2  enqueue           -> enqueue
    3  invocation        -> invocation
    4  animation         -> animation_request
    6  attack            -> attack
    7  mp                -> mp
    8  effect_birth      -> effect_birth
    9  destroy           -> excluded (no canonical observable)
    10 hp                -> excluded (no canonical observable)
    11 heal              -> heal
    12 status_apply      -> status_apply
    13 prize             -> prize
    14 invoking          -> invoking (projected to counts plus the appended skill)
    15 area_cell         -> area_cell
    16 state_sound       -> state_sound (leaving and knockdown share this kind)
    18 body_flight       -> body_flight
    19 release           -> release
    20 projectile_launch -> projectile_launch
    21 revive            -> revive
    22 status_text       -> status_text
    23 attack_batch      -> attack_batch
    30 resource_change   -> resource_change
    31 battle_item       -> battle_item
"""

NO_ACTOR = -1

#: Native codes with no canonical observable, outside the comparison on both sides.
EXCLUDED_NATIVE = frozenset((9, 10))
#: Python kinds outside the comparison (draw diagnostics and unported global-phase kinds).
EXCLUDED_PYTHON = frozenset(('rng', 'human_images'))

STATUS_TEXT_CODES = {'miss': 0, 'defense_down': 1, 'sleep': 2}


def normalize_native(events):
    """Project native KaEvent tuples (kind, unit, a, b, c, d, e)."""
    out = []
    for kind, unit, a, b, c, d, e in events:
        if kind in EXCLUDED_NATIVE:
            continue
        if kind == 1:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 2:
            out.append((kind, unit, b, (a,)))
        elif kind == 3:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 4:
            out.append((kind, unit, NO_ACTOR, (a, b)))
        elif kind == 6:
            out.append((kind, unit, a, (b, c, d, e)))
        elif kind == 7:
            out.append((kind, unit, NO_ACTOR, (a, c)))
        elif kind == 8:
            out.append((kind, unit, NO_ACTOR, (a, b)))
        elif kind == 11:
            out.append((kind, unit, a, (b, c, d, e)))
        elif kind == 12:
            out.append((kind, unit, a, (b, c, d)))
        elif kind == 13:
            out.append((kind, unit, NO_ACTOR, (a,)))
        elif kind == 14:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 15:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 16:
            out.append((kind, unit, NO_ACTOR, (a,)))
        elif kind == 18:
            out.append((kind, unit, NO_ACTOR, (a, b)))
        elif kind == 19:
            # emitted as (caster, target, skill, index, used, row id); canonical order is
            # (skill, index, used).
            out.append((kind, unit, a, (b, c, d)))
        elif kind == 20:
            out.append((kind, unit, a, (c,)))
        elif kind == 21:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 22:
            out.append((kind, unit, NO_ACTOR, (a, b)))
        elif kind == 23:
            out.append((kind, unit, NO_ACTOR, ()))
        elif kind == 24:
            out.append((kind, unit, NO_ACTOR, (a, b, c)))
        elif kind == 25:
            # status_tick: (before63, before64, after63, after64, status-present-after)
            out.append((kind, unit, NO_ACTOR, (a, b, c, d, e)))
        elif kind == 26:
            out.append((kind, unit, NO_ACTOR, ()))
        elif kind == 27:
            out.append((kind, unit, a, (b, c)))
        elif kind == 28:
            out.append((kind, unit, NO_ACTOR, (a, b)))
        elif kind == 30:
            # (parameter, after, before, source, maximum) -> (parameter, before, after, max, source)
            out.append((kind, unit, NO_ACTOR, (a, c, b, e, d)))
        elif kind == 31:
            # (used, percent, remaining, blocked, target count) - the dispatch record itself.
            out.append((kind, unit, NO_ACTOR, (a, b, c, d, e)))
        else:
            raise AssertionError(f'unnormalized native event kind {kind}')
    return out


def _consumable_label(event, key, item_index):
    """Project a Python consumable identity onto the native one: `-1` is the Holy Herb."""
    name = event.get(key)
    if name == 'holy_herb':
        return -1
    if item_index is None or name not in item_index:
        raise AssertionError(f'consumable event names an unmapped item {name!r}')
    return item_index[name]


def normalize_python(trace, item_index=None):
    """Project canonical trace dicts onto the same canonical record."""
    out = []
    for event in trace:
        kind = event['kind']
        if kind in EXCLUDED_PYTHON:
            continue
        if kind == 'state':
            out.append((1, event['target'], NO_ACTOR, (event['old'], event['new'], event['hp'])))
        elif kind == 'enqueue':
            # Python encodes "no target" as None; the native record uses -1.
            target = event['target']
            out.append((2, event['caster'], -1 if target is None else target, (event['skill'],)))
        elif kind == 'invocation':
            out.append((3, event['caster'], NO_ACTOR,
                        (event['skill'], event['level'], int(bool(event['passed'])))))
        elif kind == 'animation_request':
            out.append((4, event['target'], NO_ACTOR, (event['behavior'], event['clip'])))
        elif kind == 'attack':
            out.append((6, event['attacker'], event['target'],
                        (event['skill'] if event['skill'] is not None else -1,
                         int(bool(event['hit'])), int(bool(event['critical'])), event['damage'])))
        elif kind == 'mp':
            out.append((7, event['caster'], NO_ACTOR, (event['skill'], event['amount'])))
        elif kind == 'effect_birth':
            out.append((8, event['effect'], NO_ACTOR, (event['type'], event['lifetime'])))
        elif kind == 'heal':
            out.append((11, event['caster'], event['target'],
                        (event['skill'], event['amount'], event['before'], event['after'])))
        elif kind == 'status_apply':
            after = event['after']
            out.append((12, event['caster'], event['target'],
                        (event['skill'], int(bool(event['applied'])), after.get(63, 0))))
        elif kind == 'prize':
            out.append((13, event['target'], NO_ACTOR, (event['treasureId'],)))
        elif kind == 'invoking':
            before, after = event['before'], event['after']
            appended = after[-1][0] if len(after) > len(before) else -1
            out.append((14, event['caster'], NO_ACTOR, (len(before), len(after), appended)))
        elif kind == 'area_cell':
            cell = event['cell']
            out.append((15, event['caster'], NO_ACTOR, (event['skill'], cell[0], cell[1])))
        elif kind == 'state_sound':
            out.append((16, event['sound'], NO_ACTOR, (int(bool(event['passed'])),)))
        elif kind == 'body_flight':
            out.append((18, event['target'], NO_ACTOR,
                        (event['speed'], int(bool(event['alreadyInFlight'])))))
        elif kind == 'release':
            out.append((19, event['caster'], event['target'],
                        (event['skill'], event['index'], int(bool(event['used'])))))
        elif kind == 'projectile_launch':
            out.append((20, event['projectile'], event['owner'], (event['skill'],)))
        elif kind == 'revive':
            out.append((21, event['target'], NO_ACTOR,
                        (event['skill'], event['state'], event['hp'])))
        elif kind == 'status_text':
            code = STATUS_TEXT_CODES.get(event['text'], -1)
            out.append((22, event['target'], NO_ACTOR, (code, event['skill'])))
        elif kind == 'attack_batch':
            out.append((23, len(event['attacks']), NO_ACTOR, ()))
        elif kind == 'cell_change':
            cell = event['cell']
            out.append((24, event['target'], NO_ACTOR, (event['oldKey'], cell[0], cell[1])))
        elif kind == 'status_tick':
            before, after = event['before'], event['after']
            out.append((25, event['target'], NO_ACTOR,
                        (before.get(63, -1), before.get(64, -1), after.get(63, -1),
                         after.get(64, -1), int(62 in after))))
        elif kind == 'body_impact':
            out.append((26, event['target'], NO_ACTOR, ()))
        elif kind == 'projectile_impact':
            cell = event['cell']
            out.append((27, event['projectile'], event['owner'], (cell[0], cell[1])))
        elif kind == 'projectile_cleanup':
            route = 0 if event['route'] == 'fade' else 1
            out.append((28, event['projectile'], NO_ACTOR, (route, event['duration'])))
        elif kind == 'resource_change':
            out.append((30, event['target'], NO_ACTOR,
                        (event['parameter'], event['before'], event['after'], event['max'],
                         _consumable_label(event, 'sourceItem', item_index))))
        elif kind == 'battle_item':
            value = event.get('percent')
            out.append((31, _consumable_label(event, 'item', item_index), NO_ACTOR,
                        (int(bool(event['used'])), -1 if value is None else value,
                         event['remaining'], int('blocked' in event), len(event['targets']))))
        else:
            raise AssertionError(f'unnormalized python event kind {kind!r}')
    return out
