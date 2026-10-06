"""Short v3 generation verification: a fresh synthetic-human search, no learning judgement.

Runs one short search on a brand-new version-3 library and reports what the *generator* reached:
distinct skill sets, orders, trigger configurations, stat vectors, weapon behaviour groups and legal
placements - plus two negative proofs: no job/rank axis exists, and the legacy permutation generator
was never invoked in production.

    python check_v3_generation.py [--seconds 45] [--workers 8] [--ticks 300] [--json OUT]
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def shape(scenario):
    return dict(
        sets=tuple(frozenset(unit['skills']) for unit in scenario['ownUnits']),
        orders=tuple(tuple(unit['skills']) for unit in scenario['ownUnits']),
        triggers=tuple(tuple(unit['invocationLevels']) for unit in scenario['ownUnits']),
        stats=tuple(tuple(sorted((int(k), int((v or {}).get('rawValue') or 0))
                                 for k, v in unit['parameters'].items()))
                    for unit in scenario['ownUnits']),
        placement=tuple((unit['name'], unit.get('grid'), tuple(unit.get('cell') or ()))
                        for unit in scenario['ownUnits']),
        weapons=tuple(search_contract.weapon_group_id(unit) for unit in scenario['ownUnits']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=45)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--ticks', type=int, default=300)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    failures = []
    report = dict(seconds=args.seconds, workers=args.workers, ticks=args.ticks)

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    legacy_calls = []
    original_legacy = optimizer.legacy_proposal
    optimizer.legacy_proposal = lambda *a, **k: legacy_calls.append(a) or original_legacy(*a, **k)
    # This check is about what the *generator* reaches, so it must not be judged through the active
    # population's pruning policy: with the production per-encounter bound a dimension that the search
    # did reach can be evicted again before the run ends (a placement variant that wins no lane is
    # replaced by the next sibling), which made "every dimension was reached" depend on the survival
    # mix rather than on the generator. The bound is raised here only.
    optimizer.MAX_CANDIDATES_PER_ENCOUNTER = max(optimizer.MAX_CANDIDATES_PER_ENCOUNTER, 64)
    with tempfile.TemporaryDirectory(prefix='ka-v3-') as root:
        path = Path(root)/'v3.sqlite'
        scenario = dict(default_scenario(), tickLimit=args.ticks, encounterId=19)
        store = optimizer.Store(path, provenance())
        with store.db:
            store.set('scope', optimizer.scope(scenario))
            store.add(scenario, 'v3 fixture', 'supplied', stats(scenario))
        store.close()
        store = optimizer.Store(path, provenance())
        expect('a fresh library records search-space version 3',
               int(store.get('searchSpaceVersion', 1)) == search_contract.SEARCH_SPACE_VERSION,
               str(store.get('searchSpaceVersion')))
        store.close()

        live = optimizer.Optimizer(path)
        try:
            deadline = time.monotonic()+60
            while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
                time.sleep(.05)
            live.command('start', dict(workers=args.workers, duty=1), wait=True)
            started = time.monotonic()
            while time.monotonic()-started < args.seconds:
                time.sleep(.5)
            live.command('pause', {}, wait=True)
            time.sleep(1.0)
            status = live.status()
            wall = time.monotonic()-started
        finally:
            live.command('close')
            live.thread.join(180)
        store = optimizer.Store(path, provenance())
        try:
            rows = [json.loads(row[0]) for row in store.db.execute('SELECT scenario FROM candidate')]
            axes = store.get('proposalAxes') or {}
            report.update(
                runs=status.get('totalRuns'), proposals=store.get('proposals'),
                rejected=(status.get('scheduler') or {}).get('rejectedProposals', 0),
                candidates=len(rows), axes=axes,
                distinctSkillSets=len({shape(s)['sets'] for s in rows}),
                distinctOrders=len({shape(s)['orders'] for s in rows}),
                distinctTriggers=len({shape(s)['triggers'] for s in rows}),
                distinctStatVectors=len({shape(s)['stats'] for s in rows}),
                distinctPlacements=len({shape(s)['placement'] for s in rows}),
                distinctWeaponGroups=len({shape(s)['weapons'] for s in rows}),
                legacyInvocations=len(legacy_calls))
        finally:
            store.close()
    optimizer.legacy_proposal = original_legacy

    expect('the search produced proposals', report['proposals'] > 0, str(report))
    expect('different skill sets were reached', report['distinctSkillSets'] > 1,
           f"{report['distinctSkillSets']} set(s)")
    expect('different skill orders were reached', report['distinctOrders'] > 1,
           f"{report['distinctOrders']} order(s)")
    expect('different trigger configurations were reached', report['distinctTriggers'] > 1,
           f"{report['distinctTriggers']} trigger config(s)")
    expect('different stat vectors were reached', report['distinctStatVectors'] > 1,
           f"{report['distinctStatVectors']} stat vector(s)")
    expect('different weapon behaviour groups were reached', report['distinctWeaponGroups'] > 1,
           f"{report['distinctWeaponGroups']} group(s)")
    expect('different legal placements were reached', report['distinctPlacements'] > 1,
           f"{report['distinctPlacements']} placement(s)")
    expect('no job/rank/level axis exists in the production rotation',
           not any(axis in ('job', 'rank', 'level', 'skills', 'formation') for axis in axes),
           str(sorted(axes)))
    expect('production never invoked the legacy permutation generator',
           report['legacyInvocations'] == 0, f"{report['legacyInvocations']} call(s)")
    print(f"  runs {report['runs']} · proposals {report['proposals']} · "
          f"rejected {report['rejected']} · sets {report['distinctSkillSets']} · "
          f"orders {report['distinctOrders']} · triggers {report['distinctTriggers']} · "
          f"stats {report['distinctStatVectors']} · weapons {report['distinctWeaponGroups']} · "
          f"placements {report['distinctPlacements']}")
    print(f"  axes used: { {k: v for k, v in sorted(axes.items())} }")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  production generation reaches every synthetic dimension and never falls back to the '
          'legacy permutation generator')
    return 0


if __name__ == '__main__':
    sys.exit(main())
