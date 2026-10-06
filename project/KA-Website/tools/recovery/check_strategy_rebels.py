"""Student 2, the rebellious cheater: the tier table and the rule-break ledger.

Student 2 starts from a current community best and breaks a declared subset of the numbered rules. What
this check proves, and what it does not:

  * the tier table is the intended one - T1 breaks one rule, T2 two, T3 three, T4 all but one, which
    covers four and five as well;
  * **a Tier 1 break is admitted by T1 while a two-rule break is refused**, and T2 takes exactly the
    two-rule break T1 refused. That is the property that makes a tier a *distance* rather than a label:
    without it a T1 result would be evidence about an unknown combination;
  * each of the named moves breaks the rule it is supposed to break, measured relative to the build it
    came from rather than assumed;
  * C6, the herb rule, has no move at all today, so a tier that permits it has nothing to draw on
    rather than a broken menu;
  * every child a tier produces breaks exactly the rules the ledger records for it;
  * the ledger round-trips through a library and groups by the **exact** break set, not by rule.

It does not prove that the rebel share reaches a worker end to end: that needs a running optimizer and a
library, and is checked separately, the same way the community share is.

Usage: python check_strategy_rebels.py [--library PATH] [--json OUT]
"""
import argparse
import json
import pathlib
import random
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract as contract  # noqa: E402
import strategy_students as students  # noqa: E402

LIVE = pathlib.Path(r'A:/KingdomAdventurersOptimizer/strategiesv18.sqlite')
ENCOUNTER = 11


def supplied_scenario(db, encounter):
    row = db.execute(
        "SELECT c.scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id "
        "WHERE m.encounter=? AND c.source='supplied' LIMIT 1", (encounter,)).fetchone()
    return json.loads(row[0]) if row else None


def unit_with_role(scenario, wanted):
    return next(u for u in scenario['ownUnits'] if students.role(u) == wanted)


def a_skill_that_fits(scenario, unit, *, healing=False):
    """One approved skill `unit` can legally receive, or None. Tries every candidate rather than the
    first, because which skills a unit may carry depends on the weapon it holds."""
    for skill_id in sorted(contract.whitelist()):
        if skill_id in (unit.get('skills') or ()) or contract.is_formation_skill(skill_id):
            continue
        if (skill_id in students.HEALING_SKILLS) != bool(healing):
            continue
        try:
            contract.add_skill(scenario, unit['name'], skill_id, trigger=1)
        except (contract.ContractError, ValueError):
            continue
        return skill_id
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=str(LIVE))
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-strategy-rebels-check-1', checks=[], cases=0)

    def record(name, detail):
        report['checks'].append(dict(name=name, **detail))
        report['cases'] += 1
        print(f'  OK {name}: {json.dumps(detail, sort_keys=True)}')

    table = {tier.name: tier for tier in students.tiers()}

    # (1) The tier table. T1-T3 are the distances one, two and three; T4 is all but one, which is what
    #     covers four and five as well. "All but one" is derived, so adding a rule widens T4 instead of
    #     leaving a distance with no tier to reach it.
    assert table['T1'].span == (1, 1), table['T1'].span
    assert table['T2'].span == (2, 2), table['T2'].span
    assert table['T3'].span == (3, 3), table['T3'].span
    assert table['T4'].minimum == 4, table['T4'].span
    assert table['T4'].maximum == len(students.RULE_IDS) - 1, table['T4'].span
    assert table['T4'].maximum >= 5, 'all but one must reach five, or four and five are unreachable'
    record('the-tier-table', dict(tiers={name: list(tier.span) for name, tier in table.items()},
                                  rules=list(students.RULE_IDS)))

    # A tier that may break nothing is Student 1, and an unknown rule is not a distance.
    for bad, why in (({'T1': []}, 'a tier that may break nothing'),
                     ({'T1': ['C99']}, 'an unknown rule'),
                     ({'T9': ['C1']}, 'an unknown tier')):
        try:
            students.tiers(bad)
        except ValueError:
            continue
        raise AssertionError(f'{why} was accepted')
    record('tier-editor-refuses-nonsense', dict(cases=3))

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    try:
        base = supplied_scenario(db, ENCOUNTER)
    finally:
        db.close()
    assert base is not None, 'the encounter has no supplied baseline'
    dps = unit_with_role(base, students.ROLE_DPS)
    healer = unit_with_role(base, students.ROLE_HEALER)
    fodder = unit_with_role(base, students.ROLE_FODDER)
    assert students.community_admits(base)[0], 'the baseline must be a community build'

    # (2) The single property that makes a tier a distance: T1 admits a one-rule break and refuses a
    #     two-rule break, and T2 takes exactly the break T1 refused. Both children are built here by
    #     hand rather than drawn, so the check does not depend on the sampler's luck.
    one_rule = contract.add_unit(base, dps['name'])
    one_breaks = students.rule_breaks(one_rule, base)
    assert one_breaks == {'C1'}, one_breaks
    damage_skill = a_skill_that_fits(base, healer)
    assert damage_skill is not None, 'the healer can receive no damage skill on this encounter'
    two_rules = contract.add_skill(base, healer['name'], damage_skill, trigger=1)
    two_breaks = students.rule_breaks(two_rules, base)
    assert len(two_breaks) == 2, two_breaks
    assert table['T1'].takes(one_breaks), 'T1 refused its own one-rule break'
    assert not table['T1'].takes(two_breaks), 'T1 admitted a two-rule break'
    assert table['T2'].takes(two_breaks), 'T2 refused a two-rule break'
    assert not table['T2'].takes(one_breaks), 'T2 admitted T1\'s one-rule break'
    record('t1-admits-one-and-refuses-two',
           dict(one=list(sorted(one_breaks)), two=list(sorted(two_breaks)),
                t1_takes_one=True, t1_takes_two=False, t2_takes_two=True, t2_takes_one=False))

    # The refusal is the same test the student itself runs, so what is proved here is the path a real
    # candidate takes rather than a parallel re-implementation of it.
    drawn, why = students.rebel_child(base, table['T1'], random.Random(7))
    assert drawn is None or table['T1'].takes(students.rule_breaks(drawn, base)), why
    assert table['T1'].takes(students.rule_breaks(drawn, base)) if drawn else True
    refused = [students.rebel_child(base, students.Tier('T1', ('C4',), 2, 2), random.Random(7))
               for _ in range(3)]
    assert all(child is None for child, _ in refused), 'a tier with an impossible window admitted a child'
    record('a-tier-outside-its-window-admits-nothing',
           dict(window=[2, 2], rules=['C4'], draws=len(refused), admitted=0))

    # (3) Each named move breaks the rule it is supposed to break. These are the moves the whole
    #     student exists to test, and the answer is measured against `base` rather than assumed.
    examples = {}
    examples['seventh unit (a second healer)'] = one_breaks
    dps_skill = a_skill_that_fits(base, dps)
    if dps_skill is not None:
        armed_dps = contract.add_skill(base, dps['name'], dps_skill, trigger=1)
        examples['a skill the DPS may not carry'] = students.rule_breaks(armed_dps, base)
    if len(dps['skills']) > 1:
        trimmed = contract.remove_skill(base, dps['name'], 0)
        examples['a crucial skill removed'] = students.rule_breaks(trimmed, base)
    fatter = contract.set_stat(base, fodder['name'], 'hp', students.FODDER_HP_CEILING + 1)
    examples['fodder made tougher'] = students.rule_breaks(fatter, base)
    fodder_heal = a_skill_that_fits(base, fodder, healing=True)
    if fodder_heal is not None:
        examples['a fodder turned into a healer'] = students.rule_breaks(
            contract.add_skill(base, fodder['name'], fodder_heal, trigger=1), base)
    examples['the healer given a damage skill'] = two_breaks
    assert examples['seventh unit (a second healer)'] == {'C1'}
    assert examples['fodder made tougher'] == {'C8'}, examples['fodder made tougher']
    assert 'C7' in examples.get('a skill the DPS may not carry', {'C7'}), examples
    record('the-named-moves-and-what-they-break',
           {name: list(sorted(rules)) for name, rules in examples.items()})

    # (4) C6 is the herb rule, and the herb branch does not exist in this library - so no move can break
    #     it. The menu is empty and the student says so instead of quietly substituting another rule.
    assert students._break_moves('C6', base, base, random.Random(0)) == [], 'C6 has a move'
    herb_tier = students.Tier('The herb rule alone', ('C6',), 1, 1)
    child, why = students.rebel_child(base, herb_tier, random.Random(0))
    assert child is None and 'C6' in why, why
    record('c6-cannot-be-broken-yet', dict(moves=0, reason=why))

    # (5) Every tier's children break exactly the rules the ledger would record for them, and none of
    #     them is a community build - a rebel is refused by Student 1's own contract by definition.
    for tier in students.tiers():
        made, seen = 0, set()
        for seed in range(12):
            candidate, breaks = students.rebel_child(base, tier, random.Random(seed))
            if candidate is None:
                continue
            made += 1
            assert breaks == sorted(students.rule_breaks(candidate, base)), (tier.name, breaks)
            assert tier.takes(breaks), (tier.name, breaks)
            assert students.community_admits(candidate, base)[0] is False, (tier.name, breaks)
            seen.add('+'.join(breaks))
        assert made >= 8, f'{tier.name} produced only {made} of 12 draws'
        record(f'{tier.name}-children-match-their-ledger',
               dict(made=made, attempted=12, break_sets=sorted(seen)[:6]))

    # (6) The ledger round-trips, and it groups by the exact break set. A synthetic library is used
    #     rather than the live one: this is a test of the ledger's own logic, and it must not write.
    scratch = sqlite3.connect(':memory:')
    scratch.row_factory = sqlite3.Row
    students.store_schema(scratch)
    scratch.executescript('''
        CREATE TABLE lineage(candidate TEXT PRIMARY KEY, source TEXT);
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    scratch.execute("INSERT INTO lineage VALUES ('a', 'rebel')")
    scratch.execute("INSERT INTO lineage VALUES ('b', 'rebel')")
    scratch.execute("INSERT INTO lineage VALUES ('c', 'community')")
    scratch.execute("INSERT INTO meta VALUES ('aggregate:a:validation', ?)",
                    (json.dumps(dict(n=10, chestCount=10, chestSum=40.0)),))
    scratch.execute("INSERT INTO meta VALUES ('aggregate:b:validation', ?)",
                    (json.dumps(dict(n=10, chestCount=10, chestSum=10.0)),))
    students.record_rebel(scratch, 'a', 'T1', 1, ['C4'], ENCOUNTER, 1, 0)
    students.record_rebel(scratch, 'b', 'T1', 2, ['C2', 'C5'], ENCOUNTER, 2, 0)

    class Shim:
        def __init__(self, connection):
            self.db = connection

    store = Shim(scratch)
    rows = students.rebel_rows(store)
    assert len(rows) == 2, rows
    assert students.rebel_rows(store, tier='T1') and not students.rebel_rows(store, tier='T9')
    ledger = students.rebel_report(store)
    assert set(ledger) == {'T1.v1', 'T1.v2'}, ledger
    assert ledger['T1.v1']['C4']['bestMeanChests'] == 4.0, ledger
    assert ledger['T1.v2']['C2+C5']['runs'] == 10, ledger
    # A candidate no student owns is not in the ledger, and a library without the table reads as empty
    # rather than raising - that is what lets an older library open unchanged.
    assert not students.rebel_rows(Shim(sqlite3.connect(':memory:'))), 'a library without the table raised'
    record('the-ledger-round-trips', dict(entries=sorted(ledger),
                                          t1v1=ledger['T1.v1']['C4'],
                                          t1v2=ledger['T1.v2']['C2+C5']))
    scratch.close()

    print(f"rebel checks passed: {report['cases']}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
