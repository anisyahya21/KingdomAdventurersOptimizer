"""Deterministic encounter compiler: roster revision + enemy demands.

Read-only. Wraps `combat_encounters.special_enemy_baseline` (through
`combat_setup.prepare_setup`) and the recovered formulas in `combat_resolution`
and `strategy_mechanics`. It returns the actual compiled enemy roster, a
revision digest and the encounter-side demands of MECHANICS_GUIDED_SEARCH §25.
It consumes no simulator run and cannot produce a reward number.

ATT search landmarks use the search contract's permitted synthetic interval; the
compiled result is cached by the *encounter revision* plus the dependency digest,
so two scenarios that differ only in own-unit stats share one compiled roster.
"""
from __future__ import annotations

import strategy_mechanics as mechanics
from combat_initial_state import i32, trunc_div
from combat_resolution import attack_interval, skill_mp_cost

COMPILER_REVISION = 'strategy-encounter-compiler-3'
_ATTACK_DOMAIN = mechanics.attack_search_bounds('synthetic')
ATK_LO, ATK_HI = _ATTACK_DOMAIN['low'], _ATTACK_DOMAIN['high']

SKILLS = mechanics._SKILL_ROWS

_COMPILE_CACHE = {}


def _fallback_fraction(attack, defense, critical=False):
    """Exact fraction of the (attack_roll, defense_roll) grid in the fallback branch."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    count = 0
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            if i32(scaled - trunc_div(i32(armor * defense_roll), 100)) <= 5:
                count += 1
    return count / (41 * 21)


def _max_single_hit(attack, defense, critical=False):
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    scaled = max(1, trunc_div(i32(i32(attack) * 120), 100))
    return i32(scaled - trunc_div(i32(armor * 70), 100))


def _first_attack(predicate, lo=ATK_LO, hi=ATK_HI):
    """Smallest ATK with a monotone predicate True; None if never true in range."""
    if not predicate(hi):
        return None
    left, right = lo, hi
    while left < right:
        mid = (left + right) // 2
        if predicate(mid):
            right = mid
        else:
            left = mid + 1
    return left


def _progression_for(defense):
    crit_defense = trunc_div(i32(defense), 8)
    def relief(atk):
        return trunc_div(i32(70 * defense), 100) - trunc_div(i32(70 * crit_defense), 100)
    return dict(
        defense=defense, criticalDefense=crit_defense,
        fallbackExitAtk=_first_attack(lambda a: _fallback_fraction(a, defense) < 1.0),
        halfFallbackAtk=_first_attack(lambda a: _fallback_fraction(a, defense) < 0.5),
        allRealAtk=_first_attack(lambda a: _fallback_fraction(a, defense) == 0.0),
        maxSingleHitAtCeiling=_max_single_hit(ATK_HI, defense),
        criticalDefenseRelief=relief(defense),
    )


def _defense_forcing_fallback(effective_attack):
    """DEF at which even the maximum enemy roll is forced into the 1..10 fallback."""
    scaled = max(1, trunc_div(i32(i32(effective_attack) * 120), 100))
    need = scaled - 5
    if need <= 0:
        return 0
    return (need * 100 + 69) // 70


def _enemy_rows(prepared):
    encounter = prepared['encounter']
    rows = []
    for order, fighter in enumerate(encounter['fighters']):
        params = fighter['parameters']
        skill_ids = list(fighter.get('skills', {}).get('dataIds', []))
        first_skill = SKILLS.get(skill_ids[0]) if skill_ids else None
        speed = mechanics._param(params, 15)
        defense = mechanics._param(params, 14, fighter.get('effectiveDefense'))
        dexterity = mechanics._param(params, 19)
        luck = mechanics._param(params, 16)
        atk = mechanics._param(params, 13)
        magic = first_skill is not None and first_skill['type'] == 1
        effective_attack = trunc_div(i32(dexterity), 2) if magic else atk
        role = mechanics.classify_role(skill_ids)
        interval = attack_interval(speed)
        row = dict(
            order=order, name=fighter['name'], boss=bool(fighter.get('leaderIdentity')),
            role=role, skillIds=skill_ids,
            parameters=dict(hp=mechanics._param(params, 10), mp=mechanics._param(params, 11),
                            attack=atk, defense=defense, speed=speed, luck=luck,
                            dexterity=dexterity, magic=mechanics._param(params, 18)),
            interval=interval, period=interval + 21,
            periodSemantics='uninterrupted normal-attack reduced path',
            dexCapThreshold=mechanics.hit_cap_dexterity(speed, luck),
            effectiveAttack=effective_attack, magicAttack=magic,
        )
        if role == 'healer' and first_skill is not None:
            cost = skill_mp_cost(first_skill['minMp'], first_skill['maxMp'], 1, monster=True)
            mp = mechanics._param(params, 11) or 0
            row['healing'] = dict(skillId=first_skill['id'], healPercent=first_skill['value'],
                                  mpPerCast=cost,
                                  castsAffordable=(mp // cost) if cost > 0 else None,
                                  castsSemantics='static affordable cast count, not a simulated heal cadence')
        rows.append(row)
    return rows


def _segments(rows):
    segments = []
    for row in rows:
        if segments and segments[-1]['role'] == row['role']:
            segments[-1]['enemies'].append(row['name'])
            segments[-1]['indices'].append(row['order'])
        else:
            segments.append(dict(role=row['role'], indices=[row['order']], enemies=[row['name']]))
    return segments


def _build(prepared):
    rows = _enemy_rows(prepared)
    progression = {row['name']: _progression_for(row['parameters']['defense']) for row in rows}
    accuracy = []
    survival = []
    for row in rows:
        accuracy.append(dict(name=row['name'], boss=row['boss'],
                             dexCapThreshold=row['dexCapThreshold'],
                             cappedAtZero=row['dexCapThreshold'] <= 0))
        survival.append(dict(name=row['name'], effectiveAttack=row['effectiveAttack'],
                             magicAttack=row['magicAttack']))
    intervals = sorted({row['interval'] for row in rows})
    dex_caps = [row['dexCapThreshold'] for row in rows]
    fallback_defense = max((_defense_forcing_fallback(row['effectiveAttack'])
                            for row in rows), default=0)
    healing = [dict(order=row['order'], name=row['name'], **row['healing'])
               for row in rows if 'healing' in row]
    boss_rows = [row for row in rows if row['boss']]
    revision = mechanics.encounter_revision(prepared)
    return dict(
        schema='ka-encounter-compilation-1',
        compilerRevision=COMPILER_REVISION, mechanicsRevision=mechanics.MECHANICS_REVISION,
        dependencyDigest=mechanics.dependency_digest(),
        revision=revision,
        attackDomain=_ATTACK_DOMAIN,
        encounter=dict(encounterId=revision['encounterId'], title=prepared['encounter'].get('title'),
                       defeatCount=revision['defeatCount'], level=revision['level'],
                       terrain=prepared['encounter'].get('terrain'),
                       order=prepared['encounter']['formationOrder'], enemyCount=len(rows),
                       bossName=boss_rows[0]['name'] if boss_rows else None,
                       bossOrder=boss_rows[0]['order'] if boss_rows else None),
        roster=rows,
        demands=dict(
            progression=dict(perEnemy=[dict(name=row['name'], boss=row['boss'],
                                            **progression[row['name']]) for row in rows]),
            accuracy=dict(perEnemy=accuracy, appliesAtZeroDex=any(r['cappedAtZero'] for r in accuracy),
                          maxDexCapThreshold=max(dex_caps) if dex_caps else None),
            timing=dict(intervals=intervals, fastestInterval=min(intervals) if intervals else None,
                        slowestInterval=max(intervals) if intervals else None,
                        perEnemy=[dict(name=row['name'], interval=row['interval'],
                                       period=row['period'],
                                       periodSemantics=row['periodSemantics']) for row in rows]),
            critOpportunity=dict(perEnemy=[dict(name=row['name'], criticalDefenseRelief=
                                                progression[row['name']]['criticalDefenseRelief'])
                                           for row in rows]),
            healingPressure=dict(carriers=healing, healerCount=len(healing)),
            survivalPressure=dict(perEnemy=survival, fallbackDefenseForEncounter=fallback_defense),
            segments=_segments(rows),
        ),
        landmarks=dict(
            dexterityCapsAreLandmarks=True, luckCapsAreLandmarks=True,
            note='DEX/Luck caps are mechanical landmarks, not reward prohibitions; no universal '
                 'floor or optimal value is implied, and a synthetic point is not claimed reachable.'),
        limits=['No simulator run, seed or telemetry is consumed.',
                'Demands are encounter-side mechanics; they contain no reward estimate.',
                'ATT progression landmarks are bounded by the search-contract permitted interval.',
                'The period (interval+21) is an uninterrupted normal-attack reduced path, not actual cadence.'],
    )


def compile_encounter(scenario):
    """Compiled enemy roster revision + demands for one scenario (deterministic).

    Cached by the actual encounter revision digest plus the dependency digest, so a
    change to own-unit stats (which leaves the enemy roster unchanged) reuses the
    same compiled result.
    """
    prepared = mechanics.prepared_setup(scenario)
    revision = mechanics.encounter_revision(prepared)
    key = mechanics.canonical(dict(revision=revision['digest'], compilerRevision=COMPILER_REVISION,
                                   dependencyDigest=mechanics.dependency_digest()))
    cached = _COMPILE_CACHE.get(key)
    if cached is not None:
        return cached
    result = _build(prepared)
    _COMPILE_CACHE[key] = result
    return result


def cache_info():
    return dict(compiledEntries=len(_COMPILE_CACHE),
                dependencyDigest=mechanics.dependency_digest())


def invalidate_caches():
    _COMPILE_CACHE.clear()
    mechanics.invalidate_caches()


mechanics.register_invalidator(_COMPILE_CACHE.clear)


if __name__ == '__main__':  # pragma: no cover - manual inspection only
    import argparse, json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario')
    args = parser.parse_args()
    print(json.dumps(compile_encounter(json.loads(Path(args.scenario).read_text(encoding='utf-8'))),
                     indent=2, sort_keys=True))
