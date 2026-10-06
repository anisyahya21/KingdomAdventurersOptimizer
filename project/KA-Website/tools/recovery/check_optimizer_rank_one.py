"""The extended banks: first place is bombarded, the top ten earners get a smaller boost, and both stop
the moment the build stops qualifying.

The problem this answers: the staged policy stops a promising build at 88 runs (24 discovery + 64
validation) and that is all it ever receives. A build sitting first on a leaderboard was therefore
measured exactly as thinly as an unproven child, and once its 88 were spent it could gain no further
run - so it could neither defend its place nor lose it on evidence. The one number every other result
is read against was the one nobody was checking.

What this proves:

  * **who gets an extended bank** - the leader of every lane, plus the fight's highest potential and
    highest earned, which are the two records the overview shows and are not necessarily a lane leader
    (the potential lane ranks by the unearned prize, not by the number reached), plus the fight's **top
    ten by best earned**;
  * the rungs are ordered ordinary < top-earned < incumbent, and holding first place never pays less
    than merely being in the top ten;
  * an extended build is given comparable-bank runs, which is what the leaderboard readings are made of;
  * the bank is a **ceiling, not a renewal**: a build that has spent it receives nothing further,
    so repeatedly taking and losing the lead cannot grow it without bound;
  * the moment a build drops out of both groups it falls back to the ordinary policy, and since it has
    already outgrown that, it receives nothing further - out of the top positions, still in the library;
  * the ordinary rungs are untouched: a build in neither group is treated exactly as before, and
    `legacy` mode is left alone.

It does not measure whether the extra runs change any chest number. That is what the runs are for.

Usage: python check_optimizer_rank_one.py [--json OUT]
"""
import argparse
import inspect
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
import strategy_students as students  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-rank-one-check-1', checks=[], cases=0)

    def record(name, detail):
        report['checks'].append(dict(name=name, **detail))
        report['cases'] += 1
        print(f'  OK {name}: {json.dumps(detail, sort_keys=True)}')

    # (1) Who gets an extended bank. Lanes give their leaders; the two records are added from the
    #     records themselves, because a fight's highest potential is not the potential lane's leader;
    #     and the fight's top earners are added by rank.
    lanes = {
        '3:0': {'earned': ['E1', 'E2'], 'potential': ['P1'], 'setup': [], 'efficiency': ['F1']},
        '7:0': {'earned': [], 'potential': ['Q1'], 'setup': ['S1'], 'efficiency': []},
    }
    records = [
        dict(candidate='E1', encounterId=3, defeatCount=0, earnedMax=40, potentialMax=40),
        dict(candidate='P1', encounterId=3, defeatCount=0, earnedMax=0, potentialMax=99),
        dict(candidate='REC', encounterId=3, defeatCount=0, earnedMax=12, potentialMax=250),
        dict(candidate='Q1', encounterId=7, defeatCount=0, earnedMax=5, potentialMax=5),
        dict(candidate='NOBODY', encounterId=7, defeatCount=0, earnedMax=0, potentialMax=0),
    ] + [dict(candidate=f'earner{n:02d}', encounterId=3, defeatCount=0,
              # earner00 holds the highest earned on the fight - so it is a record holder and takes
              # first place rather than the top-ten bank - and the rest fill the top-ten ranks under it.
              earnedMax=(500 if n == 0 else 100-n), potentialMax=0) for n in range(0, 16)]
    banks = optimizer.incumbent_banks(lanes, records)
    first = {cid for cid, bank in banks.items() if bank >= optimizer.RANK_ONE_RUNS}
    # `earner00` holds the fight's highest earned, so it is a record holder and belongs in `first`
    # rather than in the top-ten group - which is exactly the "a record beats a rank" rule.
    assert {'E1', 'P1', 'F1', 'Q1', 'S1', 'REC', 'earner00'} == first, sorted(first)
    assert 'E2' not in first, 'second in a lane is not first at anything'
    assert 'NOBODY' not in banks, 'a build with no reading at all gets no bank'
    top = {cid for cid, bank in banks.items() if bank == optimizer.TOP_EARNED_RUNS}
    expected_top = {f'earner{n:02d}' for n in range(1, optimizer.TOP_EARNED_SLOTS)}
    assert top == expected_top, (sorted(top), sorted(expected_top))
    assert 'earner10' not in banks, 'the eleventh earner should not be extended'
    record('who-gets-an-extended-bank',
           dict(first=sorted(first), top_earned=sorted(top),
                why='lane leaders and the two records take first place; the fight\'s top ten by best '
                    'earned take the smaller bank'))

    # (2) The rung itself: rank one gets the incumbent bank, on top of the screening runs.
    ordinary = optimizer.staged_limits(24, 5, None, False, 0, 30, 0, {}, False, 0, True)
    incumbent = optimizer.staged_limits(24, 5, None, False, 0, 30, 0, {}, False, 0, True,
                                        incumbent=optimizer.RANK_ONE_RUNS)
    earner = optimizer.staged_limits(24, 5, None, False, 0, 30, 0, {}, False, 0, True,
                                     incumbent=optimizer.TOP_EARNED_RUNS)
    assert ordinary == (learner.EXTENDED_DISCOVERY_RUNS, learner.VALIDATION_RUNS), ordinary
    assert incumbent[0] == learner.EXTENDED_DISCOVERY_RUNS, incumbent
    assert incumbent[1] == optimizer.RANK_ONE_RUNS, incumbent
    assert earner[1] == optimizer.TOP_EARNED_RUNS, earner
    ordinary_total = sum(ordinary)
    incumbent_total = sum(incumbent)
    assert incumbent_total >= ordinary_total*5, (ordinary_total, incumbent_total)
    record('the-rungs-in-order',
           dict(ordinary=ordinary_total, topEarned=sum(earner), first=incumbent_total,
                multiple=round(incumbent_total/ordinary_total, 2),
                bank=optimizer.RANK_ONE_RUNS, topEarnedBank=optimizer.TOP_EARNED_RUNS))
    assert (learner.EXTENDED_DISCOVERY_RUNS + learner.VALIDATION_RUNS
            < optimizer.TOP_EARNED_RUNS < optimizer.RANK_ONE_RUNS), 'the rungs are out of order'

    # (3) A ceiling, not a renewal. Once the bank is spent there is nothing further, however long the
    #     build stays first, so taking and losing the lead cannot grow it without bound. The limit is
    #     allowed to track a spent ordinal - a limit that never falls below what has already run cannot
    #     revoke a seed - so the property to assert is *granted runs*, which must be zero at and past
    #     the bank however far past it the build has gone.
    spent = optimizer.staged_limits(optimizer.RANK_ONE_RUNS, 400, None, False, 0, 30, 0, {}, False, 0,
                                    True, incumbent=optimizer.RANK_ONE_RUNS)
    assert max(0, spent[1]-optimizer.RANK_ONE_RUNS) == 0, spent
    for nd in (optimizer.RANK_ONE_RUNS, optimizer.RANK_ONE_RUNS+64, optimizer.RANK_ONE_RUNS*3):
        for phase, index in (('discovery', 0), ('validation', 1)):
            limit = optimizer.staged_limits(nd, 400, None, False, 0, 30, 0, {}, False, 0, True,
                                            incumbent=optimizer.RANK_ONE_RUNS)[index]
            assert max(0, limit-nd) == 0, (nd, phase, limit)
    record('the-bank-is-a-ceiling',
           dict(at_bank=list(spent), further_runs_granted_at=0))

    # (4) Overtaken: the very next pass drops it back to the ordinary policy, which it has already
    #     outgrown - so it is handed nothing more. Out of the top position, still in the library.
    overtaken = optimizer.staged_limits(optimizer.RANK_ONE_RUNS, 400, None, False, 0, 30, 0, {},
                                        False, 0, True, incumbent=0)
    assert overtaken[1] == learner.VALIDATION_RUNS, overtaken
    assert overtaken[1] <= 400, 'an overtaken build was still handed comparable runs'
    record('overtaken-means-no-further-runs',
           dict(validation_limit=overtaken[1],
                validation_already_done=400, granted=max(0, overtaken[1]-400)))

    # (5) Nothing else moved. A build that is not rank one is treated exactly as the policy treated it
    #     before, and legacy mode ignores the rung entirely.
    for nd, wins, better, region, rank, earned, potential, progress, probe, bank, parent, mode in (
            (0, 0, None, False, None, None, 0, {}, False, 0, True, 'branching'),
            (8, 0, None, False, 10, None, 0, {}, False, 0, True, 'branching'),
            (8, 1, None, False, 2, 30, 0, {}, False, 0, True, 'branching'),
            (24, 3, ['earned'], False, 0, 30, 12, {}, False, 0, True, 'branching'),
            (0, 0, None, False, None, None, 0, {}, False, 0, True, 'legacy'),
            (24, 0, None, False, None, None, 0, {}, False, 0, True, 'legacy')):
        before = optimizer.staged_limits(nd, wins, better, region, rank, earned, potential, progress,
                                         probe, bank, parent, mode)
        after = optimizer.staged_limits(nd, wins, better, region, rank, earned, potential, progress,
                                        probe, bank, parent, mode, incumbent=0)
        assert before == after, (before, after)
        if mode == 'branching':
            assert after != optimizer.staged_limits(
                nd, wins, better, region, rank, earned, potential, progress, probe, bank, parent,
                mode, incumbent=optimizer.TOP_EARNED_RUNS), f'{nd} runs: the bank changed an ordinary build'
    record('the-ordinary-rungs-are-untouched', dict(cases=6))

    # (6) The bank is published, so the surface can show the bombardment firing rather than leaving it
    #     to be inferred from a run count.
    assert optimizer.RANK_ONE_RUNS == optimizer.MAX_SAMPLES, \
        'the incumbent bank must not outgrow a deliberately tested point'
    record('the-bank-is-the-hard-tested-ceiling',
           dict(bank=optimizer.RANK_ONE_RUNS, ceiling=optimizer.MAX_SAMPLES))

    # (8) A share of zero halts. Every candidate is owned by the stream on its lineage row, and a stream
    #     at zero must exclude its builds from dispatch entirely - not merely deprioritise them. The
    #     measured failure this answers: 38 Student 1 builds advanced in 45 seconds at a 0% share while
    #     only 11 Student 2 builds advanced at 100%, because the reserved streams had only first refusal
    #     and whatever they did not use fell through to the general pool.
    with_rebel = optimizer.dispatch_gate(
        {students.STUDENT_COMMUNITY: 0.0, students.STUDENT_REBEL: 1.0,
         students.STUDENT_STUMBLING: 0.0, students.STREAM_DISCOVERY: 0.0},
        {'c1'}, {'r1'})
    assert with_rebel('r1') is True, 'the student at 100% was not dispatched'
    assert with_rebel('c1') is False, 'Student 1 still ran at a 0% share'
    assert with_rebel('nobody') is False, 'the ordinary search still ran at a 0% discovery share'
    with_community = optimizer.dispatch_gate(
        {students.STUDENT_COMMUNITY: 1.0, students.STUDENT_REBEL: 0.0,
         students.STUDENT_STUMBLING: 0.0, students.STREAM_DISCOVERY: 0.0},
        {'c1'}, {'r1'})
    assert with_community('c1') is True and with_community('r1') is False
    assert with_community('nobody') is False
    # Student 9 - the mean-earned student - is a share stream like the other two, so the same rule has
    # to hold for it: at 0% its children receive nothing, and a positive share is what turns them on.
    # Its default *is* 0.0 on purpose (switching it on is the user's decision), which is exactly why the
    # control that makes the decision has to exist and why the gate is asserted here rather than assumed.
    off9 = optimizer.dispatch_gate(
        {students.STUDENT_COMMUNITY: 0.4, students.STUDENT_REBEL: 0.3,
         students.STUDENT_STUMBLING: 0.0, students.STREAM_DISCOVERY: 0.3,
         students.STUDENT_AVERAGE: 0.0},
        {'c1'}, {'r1'}, {'a1'})
    assert off9('a1') is False, 'Student 9 ran while its own share was 0%'
    on9 = optimizer.dispatch_gate(
        {students.STUDENT_COMMUNITY: 0.0, students.STUDENT_REBEL: 0.0,
         students.STUDENT_STUMBLING: 0.0, students.STREAM_DISCOVERY: 0.0,
         students.STUDENT_AVERAGE: 1.0},
        {'c1'}, {'r1'}, {'a1'})
    assert on9('a1') is True, 'Student 9 was not dispatched at a 100% share'
    assert on9('c1') is False and on9('r1') is False and on9('nobody') is False
    assert students.shares({'average': 0.2})[students.STUDENT_AVERAGE] > 0, \
        'a share for Student 9 must survive normalisation'
    record('a-zero-share-halts-its-stream',
           dict(at_zero='Student 1, Student 2, Student 9 and the general pool all receive nothing',
                at_one_hundred='only that student runs',
                student9='a positive share of Student 9 is accepted and dispatched',
                why='measured: 38 Student 1 builds advanced in 45s at a 0% share'))

    # (7) The rule the user added: first place on **highest earned** or on **highest average earned**
    #     buys attempts without limit, for as long as the stream that produced the build is switched on.
    #     The fixture is the case that prompted it - a build with the fight's best single run (167) and a
    #     mediocre average (41), beside one with the best average (55) and a lower best.
    # **Two fights, not one.** The first version of this fixture used a single fight, and passed while
    # the code keyed its per-flight champions by column name only - so every fight overwrote the
    # previous fight's winner and only the last fight ever took the limitless rung. A one-fight fixture
    # cannot see that, and the running app showed it as "2 retried without limit" across twenty fights.
    lanes2 = {'3:0': {'earned': ['E1'], 'potential': ['P1'], 'setup': [], 'efficiency': ['F1']},
              '7:0': {'earned': [], 'potential': ['Q1'], 'setup': [], 'efficiency': []}}
    records2 = [dict(candidate='BEST', encounterId=3, defeatCount=0, earnedMax=167, earnedMean=41.0),
                dict(candidate='AVG', encounterId=3, defeatCount=0, earnedMax=120, earnedMean=55.0),
                dict(candidate='E1', encounterId=3, defeatCount=0, earnedMax=90, earnedMean=20.0),
                dict(candidate='P1', encounterId=3, defeatCount=0, earnedMax=0, earnedMean=None,
                     potentialMax=99),
                dict(candidate='F1', encounterId=3, defeatCount=0, earnedMax=50, earnedMean=10.0),
                dict(candidate='BEST7', encounterId=7, defeatCount=0, earnedMax=70, earnedMean=12.0),
                dict(candidate='AVG7', encounterId=7, defeatCount=0, earnedMax=30, earnedMean=44.0),
                dict(candidate='Q1', encounterId=7, defeatCount=0, earnedMax=20, earnedMean=8.0)]
    live = optimizer.incumbent_banks(lanes2, records2, active=None)
    # First on Best Earned or Mean Earned buys the larger *finite* review, not an entitlement. Rank
    # alone must never authorise work without limit: the highest-earned record is monotonic, so an
    # unbounded grant fed one unchanged champion for the rest of the session.
    assert live['BEST'] == optimizer.RANK_REVIEW_RUNS, live
    assert live['AVG'] == optimizer.RANK_REVIEW_RUNS, live
    # **Both fights keep their own champions.** This is the assertion that would have caught the bug.
    assert live['BEST7'] == optimizer.RANK_REVIEW_RUNS, live
    assert live['AVG7'] == optimizer.RANK_REVIEW_RUNS, live
    assert sum(1 for bank in live.values() if bank >= optimizer.RANK_REVIEW_RUNS) == 4, live
    assert live['E1'] == optimizer.RANK_ONE_RUNS and live['P1'] == optimizer.RANK_ONE_RUNS, live
    # **Finite and cumulative.** The review is an endpoint, not a renewal: it authorises runs up to it
    # and nothing beyond, it is never reissued by holding or regaining the place, and the largest rung
    # is small enough that no unchanged champion can absorb a session's compute on rank alone.
    assert optimizer.RANK_REVIEW_RUNS < optimizer.MAX_SAMPLES * 4, optimizer.RANK_REVIEW_RUNS
    for nd in (64, optimizer.MAX_SAMPLES):
        limit = optimizer.staged_limits(nd, 400, None, False, 0, 167, 0, {}, False, 0, True,
                                        incumbent=optimizer.RANK_REVIEW_RUNS)[1]
        assert limit == optimizer.RANK_REVIEW_RUNS and limit > nd, (nd, limit)
    for nd in (optimizer.RANK_REVIEW_RUNS, optimizer.RANK_REVIEW_RUNS + 64,
               optimizer.RANK_REVIEW_RUNS * 3):
        limit = optimizer.staged_limits(nd, 400, None, False, 0, 167, 0, {}, False, 0, True,
                                        incumbent=optimizer.RANK_REVIEW_RUNS)[1]
        assert limit <= nd, f'an already-spent review authorised more work at {nd}: {limit}'
    # The gate the user set: the retry belongs to the stream that produced the build, so switching that
    # student off stops its leader being retried - and the build keeps whatever bounded rung it also
    # qualifies for, because that budget was already earned.
    off = optimizer.incumbent_banks(lanes2, records2, active={'E1', 'P1', 'F1', 'Q1'})
    assert off['BEST'] == optimizer.RANK_ONE_RUNS, off
    assert off.get('AVG', 0) <= optimizer.RANK_ONE_RUNS, off
    assert off.get('BEST7', 0) <= optimizer.RANK_ONE_RUNS, off
    record('first-on-either-column-buys-a-bounded-review',
           dict(bestEarned=f'BEST 167 best / 41 avg -> {optimizer.RANK_REVIEW_RUNS} cumulative runs',
                bestAverage=f'AVG 55 avg -> {optimizer.RANK_REVIEW_RUNS} cumulative runs',
                rankAloneAuthorisesNoMoreWork=('an already-spent review authorises nothing further at '
                                               'any ordinal'),
                whenStreamOff='falls back to the bounded rung it also qualifies for',
                ordinary=learner.EXTENDED_DISCOVERY_RUNS + learner.VALIDATION_RUNS,
                topEarnedBank=optimizer.TOP_EARNED_RUNS, firstBank=optimizer.RANK_ONE_RUNS))

    # (9) The two ways a rank rule could still leak past the caps, closed and asserted.
    #
    # (a) An explicit experiment request must not be *enlarged* by holding first place. A declared bank
    #     (a screening arm, a confirmation window, a probe, Student 9's evaluate bank) is the work the
    #     requester asked for; the rank rung must not be able to add to it. `banks` is the one map the
    #     scheduler feeds declared banks through, and a candidate in it is measured to its own bank
    #     whatever its rank is.
    for declared in (16, 64, 256, optimizer.RANK_REVIEW_RUNS):
        limit = optimizer.staged_limits(48, 30, None, False, 0, 167, 0, {}, True, declared, True)[1]
        assert limit == declared, (declared, limit)
    # ... and the ordinary rungs are not enlarged either: only a candidate whose *own* rank rung is
    # passed in as `incumbent` gets it, which is what `incumbent_banks` computes from live evidence.
    ordinary_with_experiment = optimizer.staged_limits(48, 30, None, False, 0, 167, 0, {}, True,
                                                       optimizer.RANK_REVIEW_RUNS, True)
    assert ordinary_with_experiment[1] == optimizer.RANK_REVIEW_RUNS
    record('a-declared-experiment-bank-is-never-enlarged-by-rank',
           dict(declaredBanks='returned exactly as requested, whatever the build\'s rank',
                why='a screening/confirmation/evaluate window is the work its request asked for'))

    # (b) A retired authorisation cannot survive a restart because there is nothing to survive: the
    #     rank map is a pure function of the current lanes, records and active streams, with no store
    #     to read a previously persisted grant out of and none to write one into. A future edit that
    #     gave it durable state would break this, which is the point of asserting it.
    parameters = list(inspect.signature(optimizer.incumbent_banks).parameters)
    assert parameters == ['lanes', 'records', 'active'], parameters
    again = optimizer.incumbent_banks(lanes2, records2, active=None)
    assert again == live, 'the rank map is not deterministic in its own evidence'
    record('rank-authorisations-are-recomputed-not-persisted',
           dict(signature=parameters,
                why='a stale million-run grant has nowhere to be stored or restored'))

    print(f"rank-one checks passed: {report['cases']}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
