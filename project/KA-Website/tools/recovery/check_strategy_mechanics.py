"""Focused checks for the shared strategy mechanics/compiler/domain services.

Run:  <venv>/python -B -X utf8 tools/recovery/check_strategy_mechanics.py
Deterministic, read-only, no battles, no SQLite. Prints PASS/FAIL per check and
exits non-zero on any failure.
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_build_domain as domain        # noqa: E402
import strategy_encounter_compiler as compiler  # noqa: E402
import strategy_mechanics as mechanics        # noqa: E402
from combat_initial_state import i32, trunc_div  # noqa: E402
from combat_resolution import base_damage, critical_rate, hit_rate, random_range  # noqa: E402

WORKSPACE = HERE.parents[2]
SCENARIO_PATH = (WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json')
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))


def own_unit_view(normalized, prepared, index=0):
    """The `prepared_unit` shape `skill_selection_profile` consumes, with source skills."""
    member = prepared['ownUnits'][index]
    source = next(u for u in normalized['ownUnits'] if u['name'] == member['name'])
    view = dict(member)
    view['skills'] = list(source['skills'])
    view['invocationLevels'] = list(source['invocationLevels'])
    return view


def reference_pmf(attack, defense, critical=False):
    """Independent enumeration driving the authoritative base_damage."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    out = collections.defaultdict(float)
    for r1 in range(41):
        for r2 in range(21):
            for fb in range(10):
                seq = [r1, r2, fb]
                cursor = [0]

                def nxt():
                    value = seq[cursor[0]] if cursor[0] < len(seq) else 0
                    cursor[0] += 1
                    return value
                out[base_damage(attack, armor, nxt)] += 1.0 / (41 * 21 * 10)
    return out


def check_pmf_parity():
    for attack in (7, 50, 200):
        for defense in (5, 40, 120):
            for critical in (False, True):
                want = reference_pmf(attack, defense, critical)
                got = mechanics.base_damage_pmf(attack, defense, critical)
                keys = set(want) | set(got)
                worst = max(abs(want.get(k, 0.0) - got.get(k, 0.0)) for k in keys)
                check(f'pmf-parity atk={attack} def={defense} crit={critical}', worst < 1e-12,
                      f'maxDelta={worst:.3e} keys={len(keys)}')


def check_profile_and_cache(scenario, normalized, prepared):
    # 1. Deterministic repeat + dependency-named caching.
    first = mechanics.profile(scenario)
    second = mechanics.profile(scenario)
    check('profile-deterministic', mechanics.canonical(first) == mechanics.canonical(second))
    info = mechanics.cache_info()['profile']
    before_hits = info['hits']
    mechanics.profile(scenario)
    check('profile-cache-hit', mechanics.cache_info()['profile']['hits'] == before_hits + 1,
          str(mechanics.cache_info()['profile']))
    check('no-battle-modules-imported',
          'combat_sandbox' not in sys.modules and 'strategy_optimizer_adapter' not in sys.modules)
    digest = mechanics.dependency_digest()
    manifest = mechanics.dependency_manifest()
    check('dependency-digest-exposed', len(digest) == 64
          and all(manifest['source'][name] for name in mechanics._SOURCE_FILES)
          and all(manifest['data'][name] for name in mechanics._DATA_FILES))
    check('cache-keys-name-dependency',
          mechanics.cache_info()['dependencyDigest'] == digest
          and first['dependencyDigest'] == digest)
    check('compiler-exposes-dependency', compiler.compile_encounter(scenario)['dependencyDigest'] == digest)

    # 2. Compiler caches by encounter revision, independent of own-stat changes.
    compiler.compile_encounter(scenario)
    entries_before = compiler.cache_info()['compiledEntries']
    tuned = deepcopy(scenario)
    tuned['ownUnits'][0]['parameters']['13']['rawValue'] += 50
    same = compiler.compile_encounter(scenario) is compiler.compile_encounter(tuned)
    check('compiler-cache-independent-of-own-stats',
          same and compiler.cache_info()['compiledEntries'] == entries_before,
          f'entries={entries_before}->{compiler.cache_info()["compiledEntries"]}')


def check_identity(scenario):
    # identity must be exactly strategy_optimizer.identity, raw input minus seeds.
    body = deepcopy(scenario)
    body.pop('mathSeed', None)
    body.pop('libSeed', None)
    expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()
    check('identity-matches-canonical-expression', domain.identity(scenario) == expected)
    try:
        import strategy_optimizer
        check('identity-matches-strategy-optimizer', domain.identity(scenario) == strategy_optimizer.identity(scenario))
    except Exception as error:  # pragma: no cover - dependency-free environments
        check('identity-matches-strategy-optimizer', False, repr(error))
    seeded = deepcopy(scenario)
    seeded['mathSeed'] += 999
    seeded['libSeed'] += 1
    base_fp = domain.fingerprint(scenario, {'context': 'synthetic'})
    seed_fp = domain.fingerprint(seeded, {'context': 'synthetic'})
    check('seeds-do-not-change-identity', base_fp['identity'] == seed_fp['identity'])
    check('seeds-do-not-change-exactkey', base_fp['exactKey'] == seed_fp['exactKey'])
    check('seeds-do-not-change-normalized-fingerprint',
          base_fp['normalizedFingerprint'] == seed_fp['normalizedFingerprint'])


def check_constraints(scenario, normalized, prepared, eff):
    dex = eff(0, 19)
    atk = eff(0, 13)
    # A fixed value equal to the effective value is accepted and echoed unchanged.
    ok = domain.validate_constraints(scenario, {
        'context': 'synthetic',
        'fixed': {'ownUnits.0.parameters.13': atk},
        'bounds': {'ownUnits.0.parameters.16': [5, 5204]}})
    check('constraints-effective-fixed-and-bounds-valid', ok['valid'], str(ok['violations']))
    check('constraints-echo-supplied-fixed', ok['normalizedConstraints']['fixed'] == {'ownUnits.0.parameters.13': atk})
    check('reachability-never-claimed', ok['reachability'] == 'unknown' and not ok['reachableClaimed'])
    check('synthetic-bounds-exposed', ok['syntheticBounds'] and ok['syntheticBounds']['13'] == [6, 5766])

    # Fixed DEX mismatch on the real unit is rejected.
    mismatch = domain.validate_constraints(scenario, {'context': 'synthetic',
                                                      'fixed': {'ownUnits.0.parameters.19': dex + 1}})
    check('constraints-fixed-dex-mismatch-rejected', not mismatch['valid'],
          str(mismatch['violations'][:1]))
    # Bound violation against the effective value.
    outside = domain.validate_constraints(scenario, {'context': 'synthetic',
                                                     'bounds': {'ownUnits.0.parameters.15': [100, 150]}})
    check('constraints-effective-outside-bounds-rejected', not outside['valid'],
          str(outside['violations'][:1]))
    # Synthetic admissibility is the contract interval, not signed-32.
    too_wide = domain.validate_constraints(scenario, {'context': 'synthetic',
                                                      'bounds': {'ownUnits.0.parameters.16': [1, 6000]}})
    check('constraints-synthetic-interval-enforced', not too_wide['valid'],
          str(too_wide['violations'][:1]))
    # Non-searchable parameter under synthetic context is refused.
    energy = domain.validate_constraints(scenario, {'context': 'synthetic',
                                                    'fixed': {'ownUnits.0.parameters.12': 90}})
    check('constraints-synthetic-non-searchable-parameter', not energy['valid'],
          str(energy['violations'][:1]))
    # raw/extra dependency: adding extra changes the effective value and rejects the old fixed.
    extra = deepcopy(scenario)
    extra['ownUnits'][0]['parameters']['19']['extraValue'] = 7
    extra_eff = mechanics.prepared_setup(mechanics.normalize_scenario(extra))['ownUnits'][0][
        'effectiveParameters'][19]['value']
    check('effective-value-tracks-extra', extra_eff == dex + 7, f'{dex}->{extra_eff}')
    stale = domain.validate_constraints(extra, {'context': 'synthetic',
                                                'fixed': {'ownUnits.0.parameters.19': dex}})
    check('constraints-reject-raw-as-effective', not stale['valid'], str(stale['violations'][:1]))
    fresh = domain.validate_constraints(extra, {'context': 'synthetic',
                                                'fixed': {'ownUnits.0.parameters.19': extra_eff}})
    check('constraints-accept-effective-after-extra', fresh['valid'], str(fresh['violations']))

    # Player provenance stays unknown but an effective fixed value is still honoured.
    player = domain.validate_constraints(scenario, {'context': 'player',
                                                    'fixed': {'ownUnits.0.parameters.19': dex}})
    check('player-provenance-unknown', player['provenanceStatus'] == 'unknown' and player['valid'])
    player_bad = domain.validate_constraints(scenario, {'context': 'player',
                                                        'fixed': {'ownUnits.0.parameters.19': 2147483647}})
    check('player-fixed-mismatch-rejected', not player_bad['valid'])
    unknown = domain.validate_constraints(scenario, {'fixed': {'ownUnits.0.parameters.13': atk}})
    check('missing-context-provenance-unknown', unknown['provenanceStatus'] == 'unknown')
    check('invalid-context-fails', not domain.validate_constraints(scenario, {'context': 'bogus'})['valid'])
    schema = domain.constraint_schema({'context': 'synthetic', 'fixed': {'not.a.path': 5}})
    check('schema-validation-separate', not schema['valid'] and not schema['fixed'])


def check_skill_selection(scenario, normalized, prepared):
    view = own_unit_view(normalized, prepared)
    full = mechanics.skill_selection_profile(view, 10 ** 6)
    check('skill-filter-uses-authoritative-cost',
          'expectedCommandHitsPerAction' in full and 'landedDamageModel' in full
          and full['expectedCommandHitsPerAction'] == full['expectedHitsPerAction'])
    # Command hit count, not landed damage.
    manual = sum(entry['selectProbability'] * entry['commandHitCount'] for entry in full['eligible']) \
        + full['normalAttackProbability']
    check('skill-count-is-command-hits',
          abs(manual - full['expectedCommandHitsPerAction']) < 1e-6
          and all(entry['commandHitCount'] == entry['count'] for entry in full['eligible']))
    # Low MP excludes expensive skills and names the unavailable routes.
    low = mechanics.skill_selection_profile(view, 15)
    cheap = [entry['skillId'] for entry in low['eligible'] if entry['skillId'] != 0]
    expensive = {route['skillId'] for route in low['unavailableRoutes']}
    check('skill-low-mp-excludes-expensive', expensive and not (set(cheap) & expensive)
          and all(route['reason'] == 'mp-insufficient' for route in low['unavailableRoutes']),
          f'eligible={cheap} unavailable={sorted(expensive)}')
    # Training level changes the recovered MP cost, which changes eligibility.
    trained = dict(view)
    trained['averageTrainingLevel'] = 1
    retrained = mechanics.skill_selection_profile(trained, 15)
    check('skill-training-level-alters-eligibility',
          {e['skillId'] for e in retrained['eligible']} != set(cheap),
          f'{cheap} vs {[e["skillId"] for e in retrained["eligible"]]}')
    # Filtered indexing: the invocation level follows the *filtered* ordinal.
    levels = [2, 2, 1, 0, 1, 0]
    indexed = dict(view)
    indexed['invocationLevels'] = levels
    filtered = mechanics.skill_selection_profile(indexed, 15)
    check('skill-filtered-invocation-indexing',
          filtered['eligible'][0]['invocationLevel'] == levels[0],
          str(filtered['eligible'][0]))
    # With every skill eligible the levels still follow the filtered ordinal, which
    # is not the original equipped index (skill 110 is source slot 1, level slot 0).
    indexed_full = mechanics.skill_selection_profile(indexed, 10 ** 6)
    check('skill-invocation-levels-follow-filtered-ordinal',
          [entry['invocationLevel'] for entry in indexed_full['eligible']]
          == levels[:len(indexed_full['eligible'])])


def check_bounds_and_labels(default_agility):
    synthetic = mechanics.synthetic_stat_bounds(13)
    check('attack-bounds-from-search-contract',
          mechanics.attack_search_bounds('synthetic') == dict(low=synthetic[0], high=synthetic[1],
                                                              source='search-contract.stat_bounds',
                                                              context='synthetic'))
    tightened = mechanics.attack_search_bounds('synthetic', lo=100, hi=200)
    check('attack-bounds-caller-can-tighten', tightened['low'] == 100 and tightened['high'] == 200)
    try:
        mechanics.attack_search_bounds('player')
        raised = False
    except ValueError:
        raised = True
    check('player-domain-named-not-signed32', raised)
    check('speed-domain-from-search-contract',
          mechanics.speed_domain('synthetic')['high'] == mechanics.synthetic_stat_bounds(15)[1])
    timing = mechanics.action_timing(default_agility)
    check('period-labelled-reduced-path',
          timing['period'] == timing['interval'] + 21
          and timing['periodSemantics'] == 'uninterrupted normal-attack reduced path')
    distribution = mechanics.damage_distribution(300, 100, 120, 300, 200, 40)
    check('damage-model-labelled-reduced',
          distribution['modelKind'] == 'base-law-per-hit'
          and distribution['completeUnitCombatPmf'] is False
          and 'invoking skill type/value modifiers' in distribution['notModelled'])
    profile = mechanics.profile(load_scenario())
    check('profile-lists-reduced-assumptions',
          any('invoking skill' in line for line in profile['approximations'])
          and profile['reducedModel'] is True)
    contact = mechanics.interval_contact_boundaries(default_agility)
    check('interval-contact-landmarks-are-timing',
          contact['matchSemantics'] == 'timing'
          and contact['timingMatchLandmarkCount'] > 2
          and any(row['inCurrentBand'] for row in contact['timingMatchLandmarks'])
          and contact['matchToBeat'][contact['interval']] == contact['lowerAgilityEdge']
          and 'incomingContactLandmarks' not in contact)
    solve = mechanics.solve_attack_for_expected_damage(50.0, luck=120, defense=100)
    check('attack-compensation-bounded-inverse',
          solve['reached'] and solve['bounds']['source'] == 'search-contract.stat_bounds',
          str(solve))


def check_hit_rate_inverses():
    # hit_profile must derive the cap from hit_rate itself, not from a copied constant.
    agility, luck, domain = 200, 800, (2, 2000)
    ceiling_profile = mechanics.hit_profile(0, agility, luck, domain=domain)
    brute = next(dex for dex in range(domain[0], domain[1] + 1)
                 if hit_rate(dex, agility, luck) == hit_rate(domain[1], agility, luck))
    check('hit-profile-cap-from-bounded-inverse',
          ceiling_profile['firstDexterityAtCeiling'] == brute
          and ceiling_profile['rateCeiling'] == hit_rate(domain[1], agility, luck)
          and 'no clamp constant is copied' in ceiling_profile['derivation'],
          f'derived={ceiling_profile["firstDexterityAtCeiling"]} brute={brute}')
    inverse = mechanics.hit_rate_inverse(agility, luck, 70, domain=domain)
    check('hit-rate-inverse-lands-on-authoritative-class',
          inverse['reached'] and hit_rate(inverse['dexterity'], agility, luck) >= 70
          and (inverse['dexterity'] == domain[0]
               or hit_rate(inverse['dexterity'] - 1, agility, luck) < 70))
    crit_inverse = mechanics.crit_rate_inverse(70, domain=(1, 100000))
    check('crit-rate-inverse-from-authoritative-rate',
          crit_inverse['reached'] and critical_rate(crit_inverse['luck']) >= 70
          and (crit_inverse['luck'] == 1
               or critical_rate(crit_inverse['luck'] - 1) < 70))

    # Same timing interval, different incoming hit probability.
    low = mechanics.incoming_contact_boundaries(30, luck, agility)
    high = mechanics.incoming_contact_boundaries(300, luck, agility)
    check('same-interval-different-incoming-hit-rate',
          low['interval'] == high['interval'] and low['hitRateAtAgility'] != high['hitRateAtAgility']
          and low['hitRateAtAgility'] == hit_rate(30, agility, luck)
          and high['hitRateAtAgility'] == hit_rate(300, agility, luck),
          f'interval={low["interval"]} rates={low["hitRateAtAgility"]},{high["hitRateAtAgility"]}')
    middle = mechanics.incoming_contact_boundaries(120, luck, agility)
    check('incoming-contact-interior-landmarks-are-real-changes',
          middle['rateChangeCount'] > 0 and middle['interiorLandmarks']
          and all(row['hitRateBefore'] != row['hitRateAfter']
                  and middle['band']['low'] < row['agility'] <= middle['band']['high']
                  for row in middle['interiorLandmarks'])
          and len(middle['interiorLandmarks']) <= middle['hitRateAtBandLow'] - middle['hitRateAtBandHigh'] + 1,
          str(middle['interiorLandmarks'][:3]))
    check('incoming-contact-domain-named',
          middle['domain']['source'] == 'search-contract.stat_bounds')


def check_expected_damage_includes_miss():
    with_miss = mechanics.solve_attack_for_expected_damage(50.0, luck=120, defense=100,
                                                           dexterity=40, agility=400, defender_luck=300)
    without = mechanics.solve_attack_for_expected_damage(50.0, luck=120, defense=100)
    check('expected-damage-inverse-includes-miss',
          with_miss['includesMiss'] and with_miss['hitProbability'] is not None
          and not without['includesMiss']
          and with_miss['attack'] >= without['attack'],
          f'withMiss={with_miss["attack"]} noMiss={without["attack"]}')


def check_attack_profile_solver():
    roster = [dict(name='Boss', boss=True, hp=5000, defense=300, speed=120, luck=80, dexterity=200),
              dict(name='Follower', boss=False, hp=900, defense=90, speed=200, luck=40, dexterity=120)]
    solved = mechanics.solve_attack_profile(
        {'Boss': {'expectedHitsToKill': 8, 'damageMedian': 200}, 'Follower': {'expectedHitsToKill': 3}},
        luck=120, dex=300, enemy_roster=roster, bounds={'attack': [6, 5766]})
    check('attack-profile-solver-soft-quantiles',
          solved['soft'] and solved['wholeBattleCalls'] == 0 and len(solved['results']) == 2
          and all(result['best']['residual'] >= 0 for result in solved['results'])
          and all(result['best']['perEnemy']['Boss']['damageMedian'] is not None
                  and result['best']['perEnemy']['Boss']['hitProbability'] is not None
                  for result in solved['results']),
          str([result['best']['residual'] for result in solved['results']]))
    check('attack-profile-solver-mechanical-boundaries-not-percent-ladder',
          'ka-strategy-attack-profile-solve-2' == solved['schema']
          and solved['mechanicalBoundaries']
          and all(isinstance(value, int) for value in solved['mechanicalBoundaries'])
          and 'max(1, |wanted|)' in solved['residualNormalization']
          and all(any(abs(candidate['attack'] - result['seedAttack']) <= 1
                      for candidate in result['candidates']) for result in solved['results']),
          str(solved['mechanicalBoundaries'][:4]))


def check_encounter_scan(scenario):
    compiled = compiler.compile_encounter(scenario)
    check('compile-roster-count', len(compiled['roster']) == compiled['encounter']['enemyCount'])
    forbidden = {'chests', 'awardedchests', 'pendingchests', 'reward', 'earned', 'ticks',
                 'verdict', 'censored', 'survivors', 'seed', 'run'}
    found = []

    def scan(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in forbidden:
                    found.append(str(key))
                scan(value)
        elif isinstance(node, list):
            for item in node:
                scan(item)
    scan(compiled)
    check('compile-no-telemetry', not found, str(found))
    check('compile-period-labelled',
          all(row['periodSemantics'] == 'uninterrupted normal-attack reduced path'
              for row in compiled['demands']['timing']['perEnemy']))


def main():
    scenario = load_scenario()
    normalized = mechanics.normalize_scenario(scenario)
    prepared = mechanics.prepared_setup(normalized)

    def eff(index, pid):
        return prepared['ownUnits'][index]['effectiveParameters'][pid]['value']

    check_profile_and_cache(scenario, normalized, prepared)
    check_identity(scenario)
    check_constraints(scenario, normalized, prepared, eff)
    check_skill_selection(scenario, normalized, prepared)
    check_bounds_and_labels(scenario['ownUnits'][0]['parameters']['15']['rawValue'])
    check_attack_profile_solver()
    check_hit_rate_inverses()
    check_expected_damage_includes_miss()
    check_encounter_scan(scenario)
    check_pmf_parity()

    # Invalidation is exposed and clears this process's caches (a restart is still
    # required for edited source/data files, which the digest names).
    mechanics.invalidate_caches()
    check('invalidate-caches-clears', mechanics.cache_info()['profile']['currsize'] == 0
          and compiler.cache_info()['compiledEntries'] == 0)

    failed = [r['name'] for r in RESULTS if not r['ok']]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
