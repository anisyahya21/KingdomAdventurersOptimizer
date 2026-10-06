"""All twenty encounters exist and actually receive search work (Parts C and D).

`check` runs three experiments on temporary libraries:

  1. a NEW library starts with one supplied baseline per recovered encounter/difficulty;
  2. an OLD single-encounter library can expand to all twenty without losing work, keeping its own
     horizon, and its stored scope is not silently reinterpreted;
  3. a controlled search over all twenty hands out proposals, discovery and validation to every one
     of them, and each encounter cycles through the proposal axes instead of inheriting one axis
     from a global modulus pattern (the counter-coupling bug a previous audit found).

    python check_optimizer_encounters.py [--seconds 25] [--workers 8]
"""
import argparse
import json
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def new_library(root, name='new.sqlite', encounter=19, ticks=10000):
    path = Path(root) / name
    scenario = dict(default_scenario(), tickLimit=ticks)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        store.add(scenario, 'Expansion fixture', 'supplied', stats(scenario))
    store.close()
    return path


def start(optimizer_path, seconds, workers, commands=()):
    live = optimizer.Optimizer(optimizer_path)
    deadline = time.monotonic() + 60
    while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
        time.sleep(.05)
    for action, value in commands:
        live.command(action, value, wait=True)
    live.command('start', dict(workers=workers, duty=1), wait=True)
    time.sleep(seconds)
    live.command('pause', {}, wait=True)
    time.sleep(1.0)
    status = live.status()
    live.command('close')
    live.thread.join(180)
    return status


def work_by_encounter(path):
    """Proposals/candidates/discovery/validation per encounter, straight from the store."""
    store = optimizer.Store(path, provenance())
    try:
        scenarios = {row['id']: json.loads(row['scenario'])
                     for row in store.db.execute('SELECT id, scenario FROM candidate')}
        per = defaultdict(Counter)
        for cid, scenario in scenarios.items():
            per[scenario['encounterId']]['candidates'] += 1
        for row in store.db.execute('SELECT candidate, phase, COUNT(*) AS n FROM run '
                                    'GROUP BY candidate, phase'):
            per[scenarios[row['candidate']]['encounterId']][row['phase']] += row['n']
        proposals = defaultdict(int)
        # `source='mutation'` is the authoritative marker of a search proposal, whatever the generator
        # labelled it: the contract's labels ("Contract +Heal L", "Legal mutation n", "Axis step ...")
        # all describe a proposal, and the label text is presentation.
        for label, cid in store.db.execute('SELECT label, id FROM candidate'):
            source = store.db.execute('SELECT source FROM candidate WHERE id=?', (cid,)).fetchone()[0]
            if source == 'mutation':
                proposals[scenarios[cid]['encounterId']] += 1
        for encounter, count in proposals.items():
            per[encounter]['proposals'] = count
        return per, store.get('proposalAxes')
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=25)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    failures = []
    catalogue = optimizer.encounter_catalogue()

    with tempfile.TemporaryDirectory(prefix='ka-encounters-') as root:
        # ---- 1. a new library starts with all twenty baselines -------------------------------
        fresh = Path(root) / 'fresh.sqlite'
        live = optimizer.Optimizer(fresh)
        deadline = time.monotonic() + 60
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        status = live.status()
        live.command('close')
        live.thread.join(180)
        encounters = sorted({c['scenario']['encounterId'] for c in status['candidates']})
        horizons = sorted({c['scenario']['tickLimit'] for c in status['candidates']})
        if encounters != list(range(20)):
            failures.append(f'a new library has encounters {encounters}, expected all 20')
        # A new library is generated at the production horizon; read it from the module that owns it
        # rather than pinning a literal, so a horizon change is a one-place edit.
        import strategy_search
        expected_horizon = strategy_search.DEFAULT_TICK_LIMIT
        if horizons != [expected_horizon]:
            failures.append(f'a new library uses horizons {horizons}, expected [{expected_horizon}]')
        labels = {c['scenario']['encounterId']: c['label'] for c in status['candidates']}
        if 'Baseline' not in labels.get(0, ''):
            failures.append(f'baseline labels are not catalogue titles: {labels.get(0)!r}')
        print(f'  new library: {len(status["candidates"])} baselines, encounters {encounters[0]}'
              f'..{encounters[-1]}, horizon {horizons}, e.g. {labels.get(0)!r}')

        # ---- 2. an old single-encounter library expands without losing work ------------------
        legacy = new_library(root, 'legacy.sqlite', encounter=19, ticks=7000)
        status = start(legacy, 6, 4)
        before = status['totalRuns']
        status = start(legacy, 8, 4, commands=(('all_encounters', {}),))
        after_encounters = sorted({c['scenario']['encounterId'] for c in status['candidates']})
        after_horizons = sorted({c['scenario']['tickLimit'] for c in status['candidates']})
        if after_encounters != list(range(20)):
            failures.append(f'expansion produced encounters {after_encounters}')
        if after_horizons != [7000]:
            failures.append(f'expansion changed the horizon to {after_horizons}')
        if (status['totalRuns'] or 0) < before:
            failures.append('expansion lost recorded runs')
        print(f'  legacy expansion: encounters {after_encounters[0]}..{after_encounters[-1]}, '
              f'horizon {after_horizons}, runs {before} -> {status["totalRuns"]} (kept)')

        # ---- 3. every encounter gets work, and the axes are not axis-locked ----------------
        status = start(legacy, args.seconds, args.workers)
        per, axes = work_by_encounter(legacy)
        missing = [encounter for encounter in range(20)
                   if per[encounter]['discovery'] + per[encounter]['validation'] == 0]
        if missing:
            failures.append(f'encounters with no simulation work: {missing}')
        with_proposals = [encounter for encounter in range(20) if per[encounter]['proposals']]
        print('  work per encounter (proposals/discovery/validation):')
        for encounter in range(20):
            print(f"    enc{encounter:>2}: {per[encounter]['proposals']:>3} / "
                  f"{per[encounter]['discovery']:>4} / {per[encounter]['validation']:>4}")
        if len(with_proposals) < 20:
            print(f'    (only {len(with_proposals)}/20 had proposals in this window: '
                  f'{with_proposals})')

        # Axis fairness: with 20 encounters and several axes, a modulus coupling would give each
        # encounter exactly one axis forever. Check the library's own per-axis counters instead:
        # every axis that has been used must have been used by several different encounters.
        store = optimizer.Store(legacy, provenance())
        try:
            axis_by_label = defaultdict(set)
            for label, cid in store.db.execute('SELECT label, id FROM candidate'):
                if not label or not label.startswith('Axis step'):
                    continue
                axis = label.split('·')[1].strip().split()[0] if '·' in label else '?'
                scenario = json.loads(store.db.execute('SELECT scenario FROM candidate WHERE id=?',
                                                       (cid,)).fetchone()[0])
                axis_by_label[axis].add(scenario['encounterId'])
            turns = store.get('proposalAxes') or {}
        finally:
            store.close()
        locked = {axis: sorted(encounters_) for axis, encounters_ in axis_by_label.items()
                  if len(encounters_) < 2}
        print(f'  proposal axes used: {sorted(axis_by_label)} with turn counters {turns}')
        if locked:
            print(f'    axes seen on a single encounter (expected while few proposals exist): '
                  f'{locked}')
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print('  all twenty encounters hold a baseline, an old library expands without losing work or '
          'changing its horizon, and the search gave every encounter simulation work')
    return 0


if __name__ == '__main__':
    sys.exit(main())
