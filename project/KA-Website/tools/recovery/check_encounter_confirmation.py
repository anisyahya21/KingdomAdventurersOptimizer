"""Independent fixed-sample decision contracts, without battles or live libraries."""
from copy import deepcopy
import strategy_encounter_confirmation as service


def fixture(a, b, *, nominations=1):
    pairs = [[i + 1, 10000 - i] for i in range(len(a))]
    plan = service.freeze(nominee='new', reference='old', pairs=pairs,
                          policy={'finishPolicy': 'on-verdict', 'tickLimit': 30000},
                          encounter_revision='enc', mechanics_revision='mechanics',
                          engine_revision='engine', maximum_nominations=nominations)
    rows = []
    for cid, values in [('old', a), ('new', b)]:
        for pair, value in zip(pairs, values):
            rows.append(dict(candidateId=cid, seedPair=pair,
                             outcome={'resolved': True, 'finalEarned': value,
                                      'verdict': 1 if value else 2},
                             **{key: plan[key] for key in
                                ('policy', 'encounterRevision', 'mechanicsRevision', 'engineRevision')}))
    return plan, rows


def run():
    checks = 0
    def check(value, name):
        nonlocal checks
        assert value, name
        checks += 1
    plan, rows = fixture([100] * 64, [103] * 64)
    result = service.decide(plan, rows)
    check(result['confirmed'] and result['diagnosticInterval'] == [3.0, 3.0],
          'clear replicated gain supports improvement')
    check(service.decide(plan, rows) == result, 'deterministic fixed-plan decision')
    partial = service.decide(plan, rows[:-1])
    check(not partial['confirmed'] and 'pairedDifference' not in partial
          and 'nominee' not in partial, 'partial holdout leaks no ranking or means')
    unresolved = deepcopy(rows)
    unresolved[90]['outcome'] = {'resolved': False, 'finalEarned': None}
    check('pairedDifference' not in service.decide(plan, unresolved), 'unresolved is not dropped')
    check(not service.decide(plan, rows + rows[:1])['confirmed'], 'duplicate cannot inflate evidence')
    for key in ('policy', 'encounterRevision', 'mechanicsRevision', 'engineRevision'):
        bad = deepcopy(rows)
        bad[1][key] = 'wrong'
        check(not service.decide(plan, bad)['confirmed'], key + ' compatibility is required')
    small, small_rows = fixture([100] * 8, [1000] * 8)
    check(not service.decide(small, small_rows)['confirmed'], 'large gain does not waive sample minimum')
    jackpot, jackpot_rows = fixture([10] * 64, [9] * 63 + [10000])
    test = service.decide(jackpot, jackpot_rows)
    check(not test['confirmed'] and test['singleWindfallSensitive'],
          'single jackpot cannot carry improvement')
    bad_plan = deepcopy(plan)
    bad_plan['pairs'].pop()
    try:
        service.decide(bad_plan, rows)
    except ValueError:
        checks += 1
    else:
        raise AssertionError('plan was mutable after nomination')
    zero, zero_rows = fixture([0] * 64, [2] * 64)
    check(service.decide(zero, zero_rows)['confirmed'], 'absolute gain can improve a zero reference')
    regression, regression_rows = fixture([10] * 64, [9] * 64)
    check(service.decide(regression, regression_rows)['status'] == 'supported-regression',
          'clear regression is reported')
    multiple, multiple_rows = fixture([100] * 64, [103] * 64, nominations=4)
    check(service.decide(multiple, multiple_rows)['alpha'] == 0.0125,
          'predeclared nomination multiplicity changes uncertainty level')
    nonfinite = deepcopy(rows)
    nonfinite[0]['outcome']['finalEarned'] = float('nan')
    check(not service.decide(plan, nonfinite)['confirmed'], 'nonfinite observations block decision')
    bool_pair = deepcopy(rows)
    bool_pair[0]['seedPair'][0] = True
    check(not service.decide(plan, bool_pair)['confirmed'], 'bool is not a seed integer')
    print(f'PASS {checks} fixed-sample confirmation checks; no battles')


if __name__ == '__main__':
    run()
