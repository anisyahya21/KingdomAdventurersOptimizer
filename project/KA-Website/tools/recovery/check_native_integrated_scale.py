"""Integrated worker scaling through the production adapter path (`adapter.simulate`).

Each worker process imports the adapter, warms its own candidate-template cache, then runs its share
of the seed bank with `backend='native'`. Reports wall time, battles/sec, per-battle median, cache
hit rate and scaling efficiency. This is the production path, not the standalone Rust harness.

    pypy3.exe check_native_integrated_scale.py [workers...] [--battles N] [--ticks N] [--consumable]

`--consumable` adds a real consumable timeline (Holy Herb + two reachable all-resident battle items)
to the same candidate, so the two readings are directly comparable.
"""
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: A reachable consumable timeline: Holy Herb plus bonusType 2 (MP) and bonusType 0 (HP) recovery
#: items. Both items are all-resident, which is the only scope the canonical battle touch path supplies.
CONSUMABLES = dict(
    holyHerbStock=3,
    items={'salve': dict(bonusCategory=3, bonusType=2, bonusMinValue=100, bonusMaxValue=200),
           'tonic': dict(bonusCategory=3, bonusType=0, bonusMinValue=50, bonusMaxValue=150)},
    itemStock={'salve': 2, 'tonic': 2},
    inputs=[dict(tick=120, phase='before_fighters', type='holy_herb'),
            dict(tick=400, phase='before_fighters', type='item', item='salve', target='all'),
            dict(tick=1200, phase='before_fighters', type='holy_herb'),
            dict(tick=2400, phase='after_fighters', type='item', item='tonic', target='all'),
            dict(tick=4000, phase='before_fighters', type='holy_herb'),
            dict(tick=5000, phase='before_fighters', type='item', item='salve', target='all')])


def worker(index, battles, ticks, consumable, queue):
    import strategy_optimizer_adapter as adapter
    import strategy_optimizer_native as native
    scenario = dict(adapter.default_scenario(), encounterId=19, tickLimit=ticks,
                    mathSeed=7, libSeed=8)
    if consumable:
        scenario.update(CONSUMABLES)
    adapter.simulate(scenario, (7, 8), backend='native')      # build the template
    times = []
    started = time.perf_counter()
    for battle in range(battles):
        mark = time.perf_counter()
        adapter.simulate(scenario, (100 + index * 1000 + battle, 200 + index * 1000 + battle),
                         backend='native')
        times.append(time.perf_counter() - mark)
    queue.put(dict(index=index, wall=time.perf_counter() - started, battles=battles,
                   median=statistics.median(times) * 1e3, counters=native.counters()))


def main():
    # Parse the two valued options first: a bare `--ticks 10000` must not be read as 10,000 workers.
    argv = list(sys.argv[1:])
    battles = 12
    ticks = 7000
    for flag, name in (('--battles', 'battles'), ('--ticks', 'ticks')):
        if flag in argv:
            index = argv.index(flag)
            value = int(argv[index + 1])
            argv = argv[:index] + argv[index + 2:]
            if name == 'battles':
                battles = value
            else:
                ticks = value
    counts = [int(value) for value in argv if value.isdigit()] or [1, 4, 8]
    consumable = '--consumable' in sys.argv
    print(f'integrated adapter-path scaling, enc19 @{ticks} ticks, {battles} battles/worker, '
          f'consumables={"on" if consumable else "off"}')
    base = None
    for workers in counts:
        queue = mp.Queue()
        processes = [mp.Process(target=worker, args=(index, battles, ticks, consumable, queue))
                     for index in range(workers)]
        started = time.perf_counter()
        for process in processes:
            process.start()
        rows = [queue.get() for _ in processes]
        for process in processes:
            process.join()
        wall = time.perf_counter() - started
        total = sum(row['battles'] for row in rows)
        rate = total / wall
        if base is None:
            base = rate
        counters = rows[0]['counters']
        hits = counters.get('templateCacheHits', 0)
        misses = counters.get('templateCacheMisses', 0)
        print(f'  {workers:2d} workers: battles={total} wall={wall:.2f}s '
              f'rate={rate:.1f}/s eff={rate/(base*workers)*100 if workers > 1 else 100:.0f}% '
              f'median={statistics.median([r["median"] for r in rows]):.1f}ms '
              f'cache={hits}hit/{misses}miss fallbacks={counters.get("nativeFallbacks", 0)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
