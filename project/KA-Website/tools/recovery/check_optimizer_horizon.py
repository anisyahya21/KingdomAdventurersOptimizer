"""The optimiser's battle horizon: 30,000 ticks, and a verdict is terminal.

Part 1 of the follow-up pass. Two things have to hold together, and both are measured on the *real*
Aloha-Hard runs that motivated the change rather than on a fixture:

  * the fights the old 10,000-tick limit censored still resolve, and they resolve at the same tick on
    both backends with byte-identical compact results (Python canonical engine vs the production
    native kernel through the adapter boundary the bulk workers use);
  * a resolved battle stops at its verdict (`ticks == verdictTick + 1`), so the much larger cap costs
    extra ticks only for the fights that genuinely need them, and a battle that reaches the cap with
    no verdict - and only such a battle - is reported as censored, which is what the lifetime
    "no verdict" counter counts.

Evidence: `RE-evidence/20260922-horizon/horizon-probe.json` (40 stored Aloha-Hard scenario/seed pairs
that were unresolved at 10,000 ticks and were re-run at 20,000/30,000/40,000/60,000).

    pypy3.exe check_optimizer_horizon.py [--cases 4]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EVIDENCE = HERE / 'RE-evidence/20260922-horizon/horizon-probe.json'


def production_policy():
    import strategy_search
    return (int(strategy_search.DEFAULT_TICK_LIMIT), str(strategy_search.TERMINAL_VERDICT_POLICY))


def resolved_late_cases(cases, limit):
    """Stored scenario/seed pairs that had no verdict at their stored horizon and later got one."""
    import strategy_optimizer_adapter as adapter

    document = json.loads(EVIDENCE.read_text(encoding='utf-8'))
    rows = []
    for row in document['rows']:
        resolved = next((o for o in row['observations'] if o.get('verdict') not in (None, 0)), None)
        if resolved is None or resolved['verdictTick'] <= int(row['horizon']):
            continue
        rows.append(dict(label=f"enc{row['encounterId']} seed {row['mathSeed']}/{row['libSeed']} "
                               f"verdict {resolved['verdict']} at {resolved['verdictTick']}",
                         verdict=resolved['verdict'], verdictTick=int(resolved['verdictTick']),
                         scenario=adapter.validate_scenario(row['scenario'])))
    # The longest resolutions are the interesting ones: they are what the old cap censored.
    rows.sort(key=lambda row: -row['verdictTick'])
    return rows[:cases]


def compare_backends(case, limit, policy, report):
    """Both backends at the production horizon/policy; returns the compact result on success."""
    import strategy_optimizer_adapter as adapter

    scenario = dict(case['scenario'], tickLimit=limit, finishPolicy=policy)
    seeds = (scenario['mathSeed'], scenario['libSeed'])
    canonical = adapter.simulate(scenario, seeds, backend='python')
    native = adapter.simulate(scenario, seeds, backend='native')
    if native.get('resultBackend') != 'native':
        report['failures'].append(f"{case['label']}: adapter did not run natively")
        return None
    for key in sorted(set(canonical) | set(native)):
        if key == 'resultBackend':
            continue
        if canonical.get(key) != native.get(key):
            report['failures'].append(f"{case['label']}: {key}: python={canonical.get(key)!r} "
                                      f"native={native.get(key)!r}")
    if canonical.get('verdict') != case['verdict']:
        report['failures'].append(f"{case['label']}: verdict {canonical.get('verdict')} != recorded "
                                  f"{case['verdict']}")
    if canonical.get('ticks') != case['verdictTick'] + 1:
        report['failures'].append(f"{case['label']}: the run did not stop at its verdict: ticks "
                                  f"{canonical.get('ticks')} != verdictTick+1 "
                                  f"{case['verdictTick'] + 1}")
    if canonical.get('censored'):
        report['failures'].append(f"{case['label']}: a resolved battle was reported censored")
    return canonical


def cap_cases(limit):
    """A battle that cannot end: the healing-only fixture from `check_native_horizon`."""
    from check_native_horizon import stalemate_case
    return [stalemate_case(limit)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=4,
                        help='how many late-resolving stored fights to compare on both backends')
    args = parser.parse_args()
    limit, policy = production_policy()
    failures = []
    print(f'production horizon {limit} ticks ({limit / 20 / 60:.0f}:{int(limit / 20) % 60:02d}), '
          f'finish policy {policy!r}')
    # The cap is evidence, not taste: 40 stored Aloha-Hard fights that had no verdict at 10,000 all
    # resolved by 20,000, the longest at tick 19,780, so 30,000 leaves ~1.5x the observed maximum.
    if limit != 30000:
        failures.append(f'production horizon is {limit}, expected the measured 30000')
    if policy != 'on-verdict':
        failures.append(f'production finish policy is {policy!r}, expected on-verdict')
    cases = resolved_late_cases(args.cases, limit)
    if not cases:
        failures.append('no stored fight that was unresolved at its horizon resolved later')
    report = dict(failures=failures, rows=[])
    for case in cases:
        compact = compare_backends(case, limit, policy, report)
        if compact is None:
            continue
        report['rows'].append(dict(label=case['label'], verdict=compact['verdict'],
                                   verdictTick=case['verdictTick'], ticks=compact['ticks'],
                                   prizeCallbacks=compact['prizeCallbacks'],
                                   digest=compact['digest'][:12]))
    print('late-resolving stored fights at the production horizon (both backends, identical):')
    for row in report['rows']:
        print(f"  {row['label']}: verdict={row['verdict']} at tick {row['verdictTick']} "
              f"ticks={row['ticks']} prizes={row['prizeCallbacks']} digest {row['digest']}")
    # ...and the opposite case: a battle that cannot end must reach the cap and be the *only* source
    # of a censored result. A genuine stalemate runs the whole ceiling, which is exactly the case the
    # kernel's fixed entity arena can outgrow: when it refuses on capacity the production path falls
    # back to the canonical Python engine (correct, just slower), so the assertion is made on the
    # adapter's answer - the surface the optimiser actually records - with the kernel's own status
    # reported beside it.
    import ctypes
    import ka_abi
    import strategy_optimizer_native as native
    import strategy_optimizer_adapter as adapter

    library = native.library()
    for case in cap_cases(limit):
        entry = native._template(case['scenario'], native.scenario_key(case['scenario']))
        clone = library.ka_battle_clone(entry['handle'])
        try:
            library.ka_battle_seed_rng(clone, case['scenario']['mathSeed'],
                                       case['scenario']['libSeed'])
            result = ka_abi.KaBattleReport()
            status = library.ka_run_battle(clone, entry['tick_limit'], entry['policy'],
                                           ctypes.byref(result))
        finally:
            library.ka_battle_free(clone)
        seeds = (case['scenario']['mathSeed'], case['scenario']['libSeed'])
        compact = adapter.simulate(case['scenario'], seeds, backend='auto')
        if compact['verdict'] is not None or not compact['censored'] or compact['ticks'] != limit:
            failures.append(f"{case['label']}: expected no verdict at the {limit}-tick cap, got "
                            f"verdict={compact['verdict']!r} ticks={compact['ticks']} "
                            f"censored={compact['censored']!r}")
        else:
            print(f"  {case['label']}: no verdict at {compact['ticks']} ticks (censored, as counted; "
                  f'kernel status={status} at tick {result.ticks}, backend='
                  f"{compact.get('resultBackend')})")
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print(f'  {len(report["rows"])}/{len(cases)} late-resolving fights matched on both backends and '
          f'stopped at their verdict; the cap is still the only source of a no-verdict result')
    return 0


if __name__ == '__main__':
    sys.exit(main())
