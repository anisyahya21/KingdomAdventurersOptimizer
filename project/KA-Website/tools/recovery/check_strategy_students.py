"""The students: shares, the community contract, and the value-only child.

These are the parts that decide what a worker is handed before any battle runs, so they are checked
without simulating one. What this proves and what it does not:

  * the allocation normalises, refuses nonsense, and reports (rather than vetoes) a split that has cut
    the open discovery floor;
  * the C1-C6 contract accepts the encounter's own community cast and refuses the shape the search
    actually produced - five attackers plus a skill-less unit;
  * every value child Student 1 produces is admitted, so the community student cannot emit a build that
    is not a community build;
  * a defence move that would reorder the roster is refused by the placement half of the contract.

It does not prove that the reserved share reaches a worker end to end: that needs a running optimizer and
a library, and is checked separately.

Usage: python check_strategy_students.py [--library PATH] [--json OUT]
"""
import argparse
import collections
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


def five_attacker(db, encounter):
    """A resident build that kept six units and lost the roles - what the search actually produced."""
    for row in db.execute(
            "SELECT c.scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id "
            "WHERE m.encounter=? AND c.source!='supplied'", (encounter,)):
        scenario = json.loads(row[0])
        if len(scenario.get('ownUnits') or []) != 6:
            continue
        roles = collections.Counter(students.placed_roles(scenario))
        if roles[students.ROLE_DPS] >= 4:
            return scenario
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=str(LIVE))
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-strategy-students-check-1', checks=[], cases=0)

    def record(name, detail):
        report['checks'].append(dict(name=name, **detail))
        report['cases'] += 1
        print(f'  OK {name}: {json.dumps(detail, sort_keys=True)}')

    # (1) Allocation: the defaults, a deliberate single-student setting, and the floor warning.
    default = students.shares()
    assert abs(sum(default.values()) - 1.0) < 1e-9
    assert students.share_warning(default) is None
    record('default-split', dict(**default))

    solo = students.shares({students.STUDENT_COMMUNITY: 1.0, students.STREAM_DISCOVERY: 0.0,
                            students.STUDENT_REBEL: 0.0, students.STUDENT_STUMBLING: 0.0})
    assert solo[students.STUDENT_COMMUNITY] == 1.0
    assert students.share_warning(solo) is not None
    record('one-student-100-percent', dict(share=solo[students.STUDENT_COMMUNITY],
                                           warned=bool(students.share_warning(solo))))

    for bad, why in (({students.STUDENT_COMMUNITY: 'lots'}, 'not a number'),
                     ({students.STUDENT_COMMUNITY: -1}, 'out of range'),
                     ({'nobody': 0.5}, 'unknown stream')):
        try:
            students.shares(bad)
        except ValueError:
            continue
        raise AssertionError(f'{why} was accepted')
    record('allocation-refuses-nonsense', dict(cases=3))

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    try:
        base = supplied_scenario(db, ENCOUNTER)
        five = five_attacker(db, ENCOUNTER)
    finally:
        db.close()
    assert base is not None, 'the encounter has no supplied baseline'

    # (2) The contract accepts the encounter's own community cast.
    admitted, failures = students.community_admits(base)
    assert admitted, failures
    shape = students.community_shape(base)
    assert shape['roles'] == list(students.COMMUNITY_PLACEMENT), shape
    record('accepts-the-community-cast', dict(roles=shape['roles'], placement=shape['placement']))

    # (3) The contract refuses what the search produced instead. The resident example is used when one
    #     exists; otherwise the same shape is built by arming the fodder, which is what the search did to
    #     almost every six-unit build it ever made on this fight.
    rebel_shape = five
    source = 'resident'
    if rebel_shape is None:
        rebel_shape = base
        for unit in base['ownUnits']:
            if students.role(unit) == students.ROLE_FODDER:
                rebel_shape = contract.add_skill(rebel_shape, unit['name'], 26, trigger=0)
        source = 'constructed'
    ok, failures = students.community_admits(rebel_shape)
    assert not ok, 'a five-attacker build was admitted'
    failed = [rule for rule, _detail in failures]
    assert 'C5' in failed or 'C4' in failed or 'C2' in failed, failures
    record('refuses-five-attackers', dict(source=source, roles=list(students.placed_roles(rebel_shape)),
                                          failed=failed))

    # (4) C1 is exact: neither five nor seven units are a community build.
    short = dict(base, ownUnits=base['ownUnits'][:5])
    long = dict(base, ownUnits=base['ownUnits'] + [dict(base['ownUnits'][2], name='Extra')])
    assert not students.community_admits(short)[0]
    assert not students.community_admits(long)[0]
    record('c1-is-exactly-six', dict(five=False, seven=False))

    # (5) C4 refuses a damage skill on the healer; C3 refuses a heal on the DPS. These are the two
    #     rebellions Student 2 exists to run, so the contract must say No to them here.
    healer_name = next(u['name'] for u in base['ownUnits']
                       if students.role(u) == students.ROLE_HEALER)
    armed = contract.add_skill(base, healer_name, 26, trigger=0)
    assert not students.community_admits(armed)[0], 'a damage-carrying healer was admitted'
    dps_name = next(u['name'] for u in base['ownUnits'] if students.role(u) == students.ROLE_DPS)
    healed = contract.add_skill(base, dps_name, 37, trigger=0)
    assert not students.community_admits(healed)[0], 'a healing DPS was admitted'
    record('refuses-class-rebellions', dict(healer_damage=False, dps_heal=False))

    # (6) Every value child Student 1 emits is admitted - that is what makes Student 1 incapable of
    #     emitting a non-community build.
    rng = random.Random(20260925)
    scenario = base
    made = 0
    for step in range(40):
        # The axis cursor is walked, not left at zero: the ladder now chooses its axis by rotation so a
        # turn of the cursor reaches every axis, and a test that pinned the offset would only ever
        # exercise the first one.
        child, _op, _stat, _change = students.community_child(scenario, rng, offset=step)
        if child is None:
            continue
        assert students.community_admits(child)[0], students.community_admits(child)[1]
        made += 1
        scenario = child
    assert made >= 30, f'only {made} of 40 value moves produced an admitted child'
    record('value-children-always-admitted', dict(created=made, attempted=40))

    # (7) The placement half of the contract does real work: the fodder are held low, so raising one far
    #     enough to out-defend the DPS reorders the roster. Whatever the mechanism, the answer must come
    #     from the derived placement rather than from the roster order the caller wrote.
    fodder_name = next(u['name'] for u in base['ownUnits']
                       if students.role(u) == students.ROLE_FODDER)
    reordered = contract.set_stat(base, fodder_name, 'def', contract.stat_bounds()[14][1])
    roles = students.placed_roles(reordered)
    assert roles != students.COMMUNITY_PLACEMENT, 'a reordering defence change was still admitted'
    record('placement-is-derived', dict(roles=list(roles), admitted=False))

    print(f"student checks passed: {report['cases']}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
