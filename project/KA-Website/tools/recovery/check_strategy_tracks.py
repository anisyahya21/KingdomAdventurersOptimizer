"""Student 1's tracks: the named sub-divisions of its share, and what each one is allowed to move.

Student 1 is not one sweep. Its share divides across tracks - one per stat, one for every pair of values,
one for the dial - and each is a permanent line with its own budget. The reason that structure matters
rather than being bookkeeping: a single-dimensional ladder cannot reach a compensating pair at all, so
"the relationship between two values" is not something a faster or longer sweep finds. It needs a line
that exists to look for it.

What this proves:

  * the table has one track per stat, a pair track and a dial track, and each axis track names exactly
    one stat;
  * **a track cannot move outside itself** - the Attack track's children change Attack and nothing else.
    That is a property of the generator, not a hope: the axes are filtered before the move is chosen, so
    "the Attack track moved Luck" is unrepresentable rather than merely unlikely;
  * the pair track walks **every** pair: over one cycle of its cursor, all of the build's axis pairs are
    tested, and every child moves exactly two axes;
  * the dial moves several axes at once and every one of them lands **inside the span this fight has
    converted in**, which is what makes it the dial rather than another ladder;
  * every child of every track still satisfies the C1-C8 contract, so Student 1 remains incapable of
    emitting a build that is not a community build;
  * the shares divide Student 1's own share: they normalise, a zero is honoured as off, "only Attack" is
    selectable, an unknown track is refused, and no track is starved of turns;
  * every track answers four elements on both outcomes - the failure path that once returned two into a
    four-way unpack, which stopped the community track replenishing the first time a fight was exhausted.

It does not measure which track is *paying*. That needs the tracks to have run and their chest numbers to
be compared, which is measurement, not proof.

Usage: python check_strategy_tracks.py [--library PATH] [--json OUT]
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
    """`[(unit, stat, before, after)]` for every axis Student 1 may vary whose value changed."""
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
    report = dict(schema='ka-strategy-tracks-check-1', checks=[], cases=0)

    def record(name, detail):
        report['checks'].append(dict(name=name, **detail))
        report['cases'] += 1
        print(f'  OK {name}: {json.dumps(detail, sort_keys=True)}')

    # (1) The table itself: one line per stat, one for the pairs, one for the dial.
    table = {track.name: track for track in students.tracks()}
    axis_tracks = [track for track in students.tracks() if track.kind == 'axis']
    assert len(axis_tracks) == len(students.COMMUNITY_VALUE_STATS), \
        f'{len(axis_tracks)} axis tracks for {len(students.COMMUNITY_VALUE_STATS)} stats'
    for track in axis_tracks:
        assert len(track.stats) == 1, (track.name, track.stats)
    assert [t.name for t in axis_tracks] == list(students.COMMUNITY_VALUE_STATS), \
        'the axis tracks must be one per stat, in table order'
    assert table['pair'].kind == 'pair' and table['dial'].kind == 'dial'
    record('the-track-table',
           dict(axis=[t.name for t in axis_tracks], pair=table['pair'].kind,
                dial=table['dial'].kind, total=len(students.tracks())))

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    try:
        base = supplied_scenario(db, ENCOUNTER)
    finally:
        db.close()
    assert base is not None, 'the encounter has no supplied baseline'
    assert students.community_admits(base)[0], 'the baseline must be a community build'

    # (2) A track cannot move outside itself. This is the whole reason the axes are filtered inside the
    #     generator: a track that *usually* stays inside its stat is a hope, and one that cannot leave is
    #     a line whose numbers mean what their label says.
    for track in axis_tracks:
        seen = collections.Counter()
        for seed in range(16):
            child, operation, _target, _change = students.track_child(
                base, track, random.Random(seed), offset=seed)
            assert child is not None, (track.name, seed)
            moved = moved_axes(base, child)
            assert moved, (track.name, 'moved nothing')
            assert {stat for _n, stat, _c, _v in moved} == set(track.stats), \
                (track.name, moved)
            assert students.community_admits(child, base)[0], (track.name, moved)
            assert operation == 'ladder', (track.name, operation)
            seen[len(moved)] += 1
        assert seen[1] == 16, (track.name, dict(seen))
        record(f'{track.name}-track-moves-only-{track.stats[0]}',
               dict(draws=16, axes_changed=dict(seen), stats_moved=sorted(track.stats)))

    # (3) The pair track: every child moves exactly two axes, and one cycle of its cursor tests **every**
    #     pair the build has. "It has to know the relationship of every pair" is only true when every pair
    #     appears, so the track walks an enumerated list and this is where that is checked.
    keys = students.pair_keys(base)
    assert len(keys) >= 2, f'only {len(keys)} pairs on this build'
    tested, widths = set(), collections.Counter()
    for offset in range(len(keys)):
        child, operation, target, _change = students.track_child(
            base, table['pair'], random.Random(offset), offset=offset)
        assert child is not None, (offset, target)
        moved = moved_axes(base, child)
        assert len(moved) == 2, (offset, moved)
        assert operation == 'pair', operation
        assert students.community_admits(child, base)[0], (offset, moved)
        tested.add(target)
        widths[len(moved)] += 1
    assert tested == {students.pair_label(pair) for pair in keys}, \
        f'one cycle missed {sorted({students.pair_label(p) for p in keys} - tested)}'
    record('the-pair-track-tests-every-pair',
           dict(pairs=len(keys), tested_in_one_cycle=len(tested), axes_changed=dict(widths)))

    # (4) The dial: several axes at once, and every one inside the span that fight has converted in. The
    #     spans are supplied here rather than measured, so the check tests the rule and not the library.
    axes = students._value_axes(base)
    spans = {}
    for name, stat, current, low, high in axes:
        if stat in ('atk', 'spd', 'lck'):
            spans[(name, stat)] = (max(int(low), current // 2), min(int(high), current * 2))
    moved_inside, moved_total, widths = 0, 0, collections.Counter()
    for seed in range(16):
        child, operation, _target, _change = students.dial_child(
            base, random.Random(seed), spans=spans)
        assert child is not None, seed
        assert operation == 'dial', operation
        assert students.community_admits(child, base)[0], seed
        moved = moved_axes(base, child)
        assert len(moved) >= 2, moved
        widths[len(moved)] += 1
        for name, stat, _before, value in moved:
            span = spans.get((name, stat))
            if not span:
                # No measurement for this axis, so it falls back to the local step and there is no span
                # for it to land inside. Only the constrained axes are counted, otherwise the check
                # would be asserting something the rule never claimed.
                continue
            moved_total += 1
            if span[0] <= value <= span[1]:
                moved_inside += 1
    assert moved_total > 0, 'no dial move touched an axis with a measured span'
    assert moved_inside == moved_total, \
        f'{moved_total-moved_inside} of {moved_total} dial moves landed outside their working span'
    record('the-dial-moves-inside-the-working-span',
           dict(draws=16, axes_changed=dict(sorted(widths.items())),
                moves_inside_span=moved_inside, moves_total=moved_total))

    # (5) The shares divide Student 1's own share. A zero is honoured as "off", "only Attack" is
    #     selectable, and the leftover child rotates so a small share still takes turns.
    even = students.track_shares()
    assert abs(sum(even.values()) - 1.0) < 1e-9
    assert len(set(round(v, 9) for v in even.values())) == 1, 'the default must be even'
    only_attack = students.track_shares({**{t.name: 0.0 for t in students.tracks()}, 'atk': 1.0})
    assert only_attack['atk'] == 1.0 and sum(only_attack.values()) == 1.0, only_attack
    plan = {track.name: n for track, n in students.track_plan(6, only_attack, 0)}
    assert plan['atk'] == 6 and sum(plan.values()) == 6, plan
    assert all(plan[name] == 0 for name in plan if name != 'atk'), plan
    for bad, why in (({'nobody': 0.5}, 'an unknown track'),
                     ({'atk': 'lots'}, 'a non-numeric share'),
                     ({'atk': -1.0}, 'a negative share')):
        try:
            students.track_shares(bad)
        except ValueError:
            continue
        raise AssertionError(f'{why} was accepted')
    record('only-attack-is-selectable-and-nonsense-is-refused',
           dict(tracks=len(students.tracks()), only_attack=plan['atk'],
                refused=3, total=sum(plan.values())))

    # (6) No track is starved. With even shares and one child per track the plan is one each; over a run
    #     of proposals every track takes its turn even when the count is smaller than the table.
    full = {track.name: n for track, n in students.track_plan(len(students.tracks()), even, 0)}
    assert set(full.values()) == {1}, full
    turns = collections.Counter()
    for proposal in range(len(students.tracks())*3):
        for track, n in students.track_plan(len(students.tracks())-3, even, proposal):
            turns[track.name] += n
    assert set(turns) == {track.name for track in students.tracks()}, sorted(turns)
    assert min(turns.values()) > 0, dict(turns)
    record('no-track-is-starved',
           dict(one_each_when_full=len(full), turns_over_time=dict(sorted(turns.items()))))

    # (7) The regression that was fixed: every track must answer four elements whether or not it found a
    #     child, because the caller unpacks four.
    exhausted = lambda scenario: True  # noqa: E731 - every rung already measured
    shapes = {}
    for track in students.tracks():
        answer = students.track_child(base, track, random.Random(0), taken=exhausted, offset=0)
        assert isinstance(answer, tuple) and len(answer) == 4, (track.name, len(answer))
        shapes[track.name] = len(answer)
    record('every-track-answers-four-elements-when-exhausted', shapes)

    print(f"track checks passed: {report['cases']}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
