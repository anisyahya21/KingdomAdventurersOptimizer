"""Student 1's value search: one axis, two axes, and a local neighbourhood.

The problem this checks against: Student 1's generator could only ever move **one** axis at a time, so
the space it could propose did not contain a compensating pair at all - if Attack up is worse alone and
Speed down is worse alone, no sequence of one-axis steps contains the combination that beat both. That is
a reachability defect, not a tuning one: it holds however long the search runs and however good its
parent rule is.

What this proves, and what it does not:

  * three generators exist and each moves the number of axes it is supposed to - the ladder one, the
    interaction probe two, the local move between two and the local cap;
  * every child of every stage still satisfies the C1-C8 contract, so Student 1 remains incapable of
    emitting a build that is not a community build;
  * the pair generator really does put a combination in the space: for a pair child, both single-axis
    intermediates exist as separate builds *and* the pair is a third, distinct build. **This proves
    reachability only.** Whether the pair is *better* than either half is an empirical question that only
    runs can answer, and this check does not claim it;
  * a local move is genuinely local - every step is within the configured fraction of the axis it moved;
  * the ladder's axis cursor sweeps: successive offsets visit every axis instead of sampling one, which
    is what makes the stage a coordinate search rather than a random walk;
  * every value generator returns a four-element answer on **both** outcomes. That last one is a fixed
    regression, not a style point: the failure path used to return two elements into a four-way unpack,
    so the first fight whose value neighbourhood was exhausted raised inside the coordinator and the
    community track stopped replenishing until the next pass.

It does not measure whether the objective is non-separable. That needs the pair probes to have run and
their chest numbers to be compared against their own halves, which is measurement, not proof.

Usage: python check_strategy_axes.py [--library PATH] [--json OUT]
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


def moved_axes(before, after):
    """`[(unit name, stat)]` - every axis Student 1 may vary whose value differs between the two builds."""
    by_name = {unit['name']: unit for unit in after['ownUnits']}
    moved = []
    for name, stat, current, _low, _high in students._value_axes(before):
        unit = by_name.get(name)
        if unit is None:
            continue
        try:
            value = int(contract.battle_value(after, unit, contract.stat_parameter(stat)) or 0)
        except (KeyError, TypeError, ValueError, contract.ContractError):
            continue
        if value != current:
            moved.append((name, stat, current, value))
    return moved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=str(LIVE))
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-strategy-axes-check-1', checks=[], cases=0)

    def record(name, detail):
        report['checks'].append(dict(name=name, **detail))
        report['cases'] += 1
        print(f'  OK {name}: {json.dumps(detail, sort_keys=True)}')

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    try:
        base = supplied_scenario(db, ENCOUNTER)
    finally:
        db.close()
    assert base is not None, 'the encounter has no supplied baseline'
    assert students.community_admits(base)[0], 'the baseline must be a community build'
    axes = students._value_axes(base)
    assert len(axes) >= 2, f'only {len(axes)} value axes; the interaction stages need at least two'
    record('the-value-axes', dict(count=len(axes),
                                  axes=[f'{name}:{stat}' for name, stat, *_ in axes]))

    # (1) Each stage moves the number of axes it is named for. This is the whole distinction between the
    #     stages: the ladder is the range sweep, the pair is the interaction probe, the local move is the
    #     neighbourhood sample that can discover a compensating pair.
    stages = (('ladder', students.community_child, 1, 1),
              ('pair', students.community_pair_child, 2, 2),
              ('local', students.community_local_child, 2, len(axes)))
    for label, generate, fewest, most in stages:
        made, seen = 0, collections.Counter()
        for seed in range(24):
            child, operation, target, change = generate(base, random.Random(seed), offset=seed) \
                if label == 'ladder' else generate(base, random.Random(seed))
            assert child is not None, (label, target, change)
            made += 1
            moved = moved_axes(base, child)
            assert fewest <= len(moved) <= most, (label, len(moved), moved)
            assert students.community_admits(child, base)[0], (label, moved)
            assert operation == label, (operation, label)
            seen[len(moved)] += 1
        assert made == 24, f'{label} produced only {made} of 24 draws'
        record(f'{label}-moves-the-right-number-of-axes',
               dict(made=made, attempted=24, axes_changed=dict(sorted(seen.items()))))

    # (2) The reachability property, which is the reason the pair stage exists at all. Take a real pair
    #     child, rebuild each half on its own, and show all three builds are distinct: the single-axis
    #     intermediates are in the space *and* so is the combination. A ladder-only generator can produce
    #     the two halves and never the third.
    reachable, example = 0, None
    for seed in range(24):
        child, _operation, _target, _change = students.community_pair_child(base, random.Random(seed))
        moved = moved_axes(base, child)
        if len(moved) != 2:
            continue
        halves = []
        for name, stat, current, value in moved:
            try:
                halves.append(contract.set_stat(base, name, stat, value))
            except (contract.ContractError, ValueError):
                halves = []
                break
        if len(halves) != 2:
            continue
        if halves[0] == child or halves[1] == child or halves[0] == halves[1]:
            continue
        if not all(students.community_admits(half, base)[0] for half in halves):
            continue
        reachable += 1
        if example is None:
            example = dict(
                first=f'{moved[0][0]}:{moved[0][1]} {moved[0][2]}->{moved[0][3]}',
                second=f'{moved[1][0]}:{moved[1][1]} {moved[1][2]}->{moved[1][3]}',
                all_three_distinct=True, both_halves_admitted=True)
    assert reachable > 0, 'no pair child had two admitted, distinct single-axis halves'
    record('a-pair-is-reachable-not-just-each-half',
           dict(pairs_with_both_halves=reachable, of=24, example=example))

    # (3) A local move is local: no step may exceed the configured fraction of the axis it moved. If it
    #     did, "local" would just be a second ladder under a different name.
    worst = 0.0
    for seed in range(24):
        child, _operation, _target, _change = students.community_local_child(base, random.Random(seed))
        for _name, _stat, current, value in moved_axes(base, child):
            if current:
                worst = max(worst, abs(value-current)/abs(current))
    assert worst <= students.LOCAL_STEP + 0.02, f'a local step moved {worst:.1%} of an axis'
    record('a-local-move-is-local', dict(largest_step=f'{worst:.1%}',
                                        configured=f'{students.LOCAL_STEP:.0%}'))

    # (4) The ladder's axis cursor sweeps rather than samples. With nothing taken, offset n starts on axis
    #     n, so one full turn of the cursor reaches every axis - which is what makes the stage coordinate
    #     descent instead of a random walk with a ladder attached.
    #     The comparison is on the *axis* (unit and stat), not on the stat name: this fight has two HP
    #     axes and two Defence axes, one on each of two units, and a stat-name comparison would call
    #     those one axis and pass a cursor that never reached the second unit.
    visited = set()
    for offset in range(len(axes)):
        child, _operation, _target, _change = students.community_child(
            base, random.Random(offset), offset=offset)
        visited |= {(name, stat) for name, stat, _c, _v in moved_axes(base, child)}
    assert visited == {(name, stat) for name, stat, *_ in axes}, \
        f'one turn of the cursor missed {sorted({(n, s) for n, s, *_ in axes} - visited)}'
    record('the-ladder-sweeps-every-axis',
           dict(axes=len(axes), reached_by_one_turn=len(visited)))

    # (5) The regression that was fixed: the failure path returned two elements into a four-way unpack, so
    #     the first exhausted neighbourhood raised inside the coordinator and stopped the community track.
    #     Every generator must answer four elements whether it found a child or not.
    exhausted = lambda scenario: True  # noqa: E731 - every rung already measured
    shapes = {}
    for label, generate in (('ladder', students.community_child),
                            ('pair', students.community_pair_child),
                            ('local', students.community_local_child)):
        answer = generate(base, random.Random(0), taken=exhausted)
        assert isinstance(answer, tuple) and len(answer) == 4, (label, len(answer), answer)
        assert answer[0] is None, (label, answer)
        shapes[label] = dict(length=len(answer), refused_because=answer[1][:60])
    record('every-stage-answers-four-elements-when-exhausted', shapes)

    print(f"axis checks passed: {report['cases']}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
