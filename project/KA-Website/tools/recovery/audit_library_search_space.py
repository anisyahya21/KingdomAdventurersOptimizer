"""What has the current library actually been exploring? (read-only, on a copy)

Diagnostic evidence for the search-space contract: counts, the improvement timeline, and - most
importantly - how much of the population differs only by the *order* of the same skills, which is the
dimension the old generator could reach and little else.

    python audit_library_search_space.py [--library PATH] [--json OUT]
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract  # noqa: E402

LIVE = Path(r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy\KA-Website'
            r'\strategies.FINISHED.REWARD.SYSTEM.V1.sqlite')


def load(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    meta = {row['key']: row['value'] for row in db.execute('SELECT key, value FROM meta')}
    candidates = [dict(row) for row in db.execute('SELECT * FROM candidate')]
    runs = [dict(row) for row in db.execute('SELECT * FROM run ORDER BY rowid')]
    archive = [dict(row) for row in db.execute('SELECT * FROM archive')]
    db.close()
    return meta, candidates, runs, archive


def shape(scenario):
    return dict(
        sets=tuple(frozenset(unit['skills']) for unit in scenario['ownUnits']),
        orders=tuple(tuple(unit['skills']) for unit in scenario['ownUnits']),
        triggers=tuple(tuple(unit['invocationLevels']) for unit in scenario['ownUnits']),
        parameters=tuple(tuple(sorted((int(k), int((v or {}).get('rawValue') or 0))
                                      for k, v in unit['parameters'].items()))
                         for unit in scenario['ownUnits']),
        placement=tuple((unit['name'], unit.get('grid'), tuple(unit.get('cell') or ()))
                        for unit in scenario['ownUnits']),
        weapons=tuple(search_contract.weapon_group_id(unit) for unit in scenario['ownUnits']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=LIVE)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    root = tempfile.mkdtemp(prefix='ka-audit-')
    copy = Path(root)/args.library.name
    shutil.copyfile(args.library, copy)
    meta, candidates, runs, archive = load(copy)

    scenarios = {}
    for candidate in candidates:
        try:
            scenarios[candidate['id']] = json.loads(candidate['scenario'])
        except ValueError:
            continue
    shapes = {cid: shape(scenario) for cid, scenario in scenarios.items()}
    baseline = next((shapes[c['id']] for c in candidates if c['source'] == 'supplied'
                     and c['id'] in shapes), None)

    def counts(field):
        return len({shape[field] for shape in shapes.values()})

    order_only = [cid for cid, value in shapes.items()
                  if baseline and value['sets'] == baseline['sets']
                  and value['orders'] != baseline['orders']
                  and value['parameters'] == baseline['parameters']
                  and value['weapons'] == baseline['weapons']
                  and value['placement'] == baseline['placement']]
    run_counts = Counter(row['candidate'] for row in runs)
    total_runs = sum(run_counts.values())
    order_only_runs = sum(run_counts[cid] for cid in order_only)

    stat_ranges = {}
    for cid, scenario in scenarios.items():
        for unit in scenario['ownUnits']:
            for key, entry in (unit.get('parameters') or {}).items():
                value = int((entry or {}).get('rawValue') or 0)
                low, high = stat_ranges.get(str(key), (value, value))
                stat_ranges[str(key)] = (min(low, value), max(high, value))

    # Improvement timeline: the running best of each metric over the stored runs, in insertion order.
    best = dict(earned=0, potential=0, setup=0)
    first_best = {}
    for index, row in enumerate(runs, start=1):
        try:
            result = json.loads(row['result'])
        except ValueError:
            continue
        reward = result.get('rewardOutcome') or {}
        verdict = result.get('verdict') if not result.get('censored') else None
        earned = reward.get('awardedChests') if verdict == 1 else 0
        potential = reward.get('pendingChests') or 0
        progress = result.get('progressMetrics') or {}
        setup = progress.get('postDeathPrizes') or 0
        for name, value in (('earned', earned), ('potential', potential), ('setup', setup)):
            if isinstance(value, (int, float)) and value > best[name]:
                best[name] = int(value)
                first_best[name] = index

    report = dict(
        library=args.library.name,
        scope=json.loads(meta.get('scope') or '{}'),
        totalRuns=json.loads(meta.get('totalRuns') or '0'),
        proposals=json.loads(meta.get('proposals') or '0'),
        improvements=json.loads(meta.get('improvements') or '0'),
        lastImprovementRun=json.loads(meta.get('lastImprovementRun') or 'null'),
        candidates=len(candidates), survivingRows=len(runs), archiveCells=len(archive),
        sources=dict(Counter(c['source'] for c in candidates)),
        distinctSkillSets=counts('sets'), distinctSkillOrders=counts('orders'),
        distinctTriggerConfigs=counts('triggers'), distinctStatVectors=counts('parameters'),
        distinctPlacements=counts('placement'), distinctWeaponGroups=counts('weapons'),
        candidatesThatAreOrderOnlyOfTheBaseline=len(order_only),
        runsOnOrderOnlyCandidates=order_only_runs,
        orderOnlyRunFraction=(round(order_only_runs/total_runs, 4) if total_runs else None),
        statRangesReached=stat_ranges,
        bestOverRunNumber=best, runNumberOfFirstBest=first_best,
        proposalAxes=json.loads(meta.get('proposalAxes') or 'null'),
        proposalLanes=json.loads(meta.get('proposalLanes') or 'null'),
        objectiveVersion=json.loads(meta.get('objectiveVersion') or 'null'),
        searchSpaceVersion=json.loads(meta.get('searchSpaceVersion') or 'null'),
    )
    for key, value in report.items():
        print(f'  {key}: {value}')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
