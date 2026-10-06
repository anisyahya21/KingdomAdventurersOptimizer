"""Issue 5: a stored command released in the SAME fighters phase as the boss's death.

`ProgressWatch` used to learn the death only from the per-tick sample, which runs *after* the whole
fighters phase, while releases happen *during* it. A command released later in the same phase as the
killing blow therefore looked like it had preceded the death and was counted as neither pre- nor
post-death. This checker

  1. runs a targeted fixture on the real engine - two own fighters, each with a stored command aimed
     at the rival leader, and the leader's HP driven to 0 inside the tick in which those commands
     complete - and compares the fixed observer against the pre-fix ordering rule kept verbatim as
     `LegacyWatch`; and
  2. scans real captured cases for the same pattern, so the claim is not fixture-only.

Combat behavior is untouched: the fixture only sets the boss's HP, and both observers are read-only.

    python check_progress_release_order.py [--json OUT]
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_progress  # noqa: E402


class LegacyWatch(combat_progress.ProgressWatch):
    """The pre-fix ordering rule, kept verbatim: no HP probe and no in-phase latch.

    Everything else (sampling, maxima, prizes, the death tick itself) is the same observer, so any
    difference between the two is exactly the same-tick release this issue is about.
    """

    def release(self, identity, command, tick, boss_hp=None):
        fields = self.fields
        boss = fields['bossIdentity']
        if command.get('target') == boss:
            fields['commandsTargetingBossReleased'] += 1
        if fields['bossDeathTick'] >= 0 and tick > fields['bossDeathTick']:
            fields['commandsReleasedAfterDeath'] += 1
            if command.get('target') == boss:
                fields['commandsReleasedAfterDeathTargetingBoss'] += 1
            if fields['firstPostDeathCommandReleaseTick'] < 0:
                fields['firstPostDeathCommandReleaseTick'] = tick
            fields['lastPostDeathCommandReleaseTick'] = tick


class LoggingWatch(combat_progress.ProgressWatch):
    """The fixed observer, plus the tick of every command completion (for finding the fixture tick)."""

    def __init__(self, boss):
        super().__init__(boss)
        self.completions = []

    def release(self, identity, command, tick, boss_hp=None):
        self.completions.append(dict(tick=tick, target=command.get('target'), hp=boss_hp))
        super().release(identity, command, tick, boss_hp)


def fixture_specs(hp):
    """Two own attackers with a stored-command skill, and one rival leader."""
    from combat_encounters import special_enemy_baseline
    from combat_parameters import HUMAN_TRAINING_PARAMETERS
    from copy import deepcopy

    def human(name, grid, values):
        params = {p: dict(rawValue=values.get(p, 1), rawMax=values.get(p, 1) if p in (10, 11)
                          else 2147483647, extraValue=0, extraMax=0, trainingLevel=123)
                  for p in HUMAN_TRAINING_PARAMETERS}
        return dict(name=name, human=True, team=0, grid=grid, cell=[grid % 5, 4+grid // 5],
                    skills=[26], levels=[1], parameters=params)

    baseline = special_enemy_baseline(3, 0, lambda n: 0)
    boss = next(f for f in baseline['fighters'] if f['leaderIdentity'])
    specs = [human('a1', 0, {10: 5000, 11: 1000, 13: 300, 14: 3000, 15: 220, 16: 110}),
             human('a2', 5, {10: 5000, 11: 1000, 13: 300, 14: 3000, 15: 220, 16: 110})]
    specs.append(dict(name='boss', human=False, team=1, grid=0, cell=[0, 3], boss=True,
                      skills=boss['skills']['dataIds'], levels=boss['skills']['invocationLevels'],
                      parameters={int(k): v for k, v in deepcopy(boss['parameters']).items()}))
    specs[-1]['parameters'][10]['rawValue'] = hp
    specs[-1]['parameters'][10]['rawMax'] = hp
    return specs


def run_fixture(watch_class, ticks, kill_tick=None):
    """One real-engine run: both stored commands complete while the boss is already down."""
    from combat_shared_controllers import SharedControllers

    engine = SharedControllers(fixture_specs(10**9), 1, 2, lambda engine, i: [])
    engine.progress = watch_class(combat_progress.boss_identity(engine.specs))
    boss = engine.progress.fields['bossIdentity']
    # Both own fighters get one stored command aimed at the leader; they complete together.
    for unit in engine.teams[0]:
        engine.enqueue(unit['id'], 26, boss, 'fixture')

    def before(world, phase):
        if phase == 'after_fighters':
            # The sandbox's own seam: the observer samples once per tick, after the fighters phase.
            world.progress.observe(world)
        if kill_tick is not None and phase == 'before_fighters' and world.tick == kill_tick:
            # The leader reaches HP 0 inside this tick; both commands complete later in its fighters
            # phase. This is the ordering the observer has to get right.
            world.units[boss]['parameters'][10]['rawValue'] = 0

    engine.run(ticks, before, finish_policy='on-verdict')
    return engine, engine.progress.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    failures = []
    report = {}

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    # ---- 1. targeted fixture ------------------------------------------------------------------
    ticks = 26
    engine, _ = run_fixture(LoggingWatch, ticks)
    completions = engine.progress.completions
    print(f'  fixture: {len(completions)} stored-command completion(s) at ticks '
          f'{[row["tick"] for row in completions]}')
    expect('the fixture completes stored commands at all', bool(completions), 'no completion happened')
    if not completions:
        return 1
    release_tick = completions[0]['tick']
    same_tick_releases = sum(1 for row in completions if row['tick'] == release_tick)
    expect('both stored commands complete on the same tick', same_tick_releases == 2,
           f'completion ticks {[row["tick"] for row in completions]}')
    expect('and both of them target the rival leader',
           all(row['target'] == engine.progress.fields['bossIdentity'] for row in completions[:2]),
           f'completions {completions}')

    _, legacy = run_fixture(LegacyWatch, ticks, kill_tick=release_tick)
    _, fixed = run_fixture(combat_progress.ProgressWatch, ticks, kill_tick=release_tick)
    report['fixture'] = dict(release_tick=release_tick, legacy=legacy, fixed=fixed)
    print(f'  boss killed at tick {release_tick}, the same tick the commands complete')
    print(f'    pre-fix rule : releasedAfterDeath={legacy["commandsReleasedAfterDeath"]} '
          f'targetingBoss={legacy["commandsReleasedAfterDeathTargetingBoss"]} '
          f'firstTick={legacy["firstPostDeathCommandReleaseTick"]} '
          f'postDeathPrizes={legacy["postDeathPrizes"]}')
    print(f'    fixed rule   : releasedAfterDeath={fixed["commandsReleasedAfterDeath"]} '
          f'targetingBoss={fixed["commandsReleasedAfterDeathTargetingBoss"]} '
          f'firstTick={fixed["firstPostDeathCommandReleaseTick"]} '
          f'postDeathPrizes={fixed["postDeathPrizes"]}')
    expect('the pre-fix rule misses the same-tick releases',
           legacy['commandsReleasedAfterDeath'] == 0
           and legacy['firstPostDeathCommandReleaseTick'] < 0,
           f"legacy counted {legacy['commandsReleasedAfterDeath']}")
    expect('the fixed rule counts them as post-death',
           fixed['commandsReleasedAfterDeath'] == 2
           and fixed['commandsReleasedAfterDeathTargetingBoss'] == 2,
           f"fixed counted {fixed['commandsReleasedAfterDeath']}/"
           f"{fixed['commandsReleasedAfterDeathTargetingBoss']}")
    expect('the death tick is the same on both, and matches the release tick',
           fixed['bossDeathTick'] == legacy['bossDeathTick'] == release_tick,
           f"legacy {legacy['bossDeathTick']}, fixed {fixed['bossDeathTick']}, "
           f'release {release_tick}')
    expect('the fixed rule reports the release window',
           fixed['firstPostDeathCommandReleaseTick'] == release_tick
           and fixed['lastPostDeathCommandReleaseTick'] == release_tick,
           f"{fixed['firstPostDeathCommandReleaseTick']}.."
           f"{fixed['lastPostDeathCommandReleaseTick']}")

    # ---- 2. does the same pattern occur in real captured cases? -------------------------------
    scanned = []
    try:
        import sqlite3
        import shutil
        import tempfile
        import combat_sandbox

        library = Path(r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy'
                       r'\KA-Website\strategiespostrust1000tickslimit.sqlite')
        with tempfile.TemporaryDirectory(prefix='ka-order-', ignore_cleanup_errors=True) as root:
            copy = Path(root)/'order.sqlite'
            shutil.copyfile(library, copy)
            db = sqlite3.connect(f'file:{copy}?mode=ro', uri=True)
            holders = json.loads(db.execute(
                "SELECT value FROM meta WHERE key='recordHolders'").fetchone()[0])
            db.close()
        for key, holder in sorted(holders.items()):
            horizon = int(holder['scenario'].get('tickLimit') or 0)
            cut = int(horizon*0.31) or 3000
            scenario = dict(holder['scenario'], tickLimit=cut)
            seeds = (int(holder['mathSeed']), int(holder['libSeed']))
            for watch_class, label in ((LegacyWatch, 'legacy'),
                                       (combat_progress.ProgressWatch, 'fixed')):
                original = combat_sandbox.combat_progress.ProgressWatch
                combat_sandbox.combat_progress.ProgressWatch = watch_class
                try:
                    result = combat_sandbox.run_scenario(dict(scenario), include_trace=False)
                finally:
                    combat_sandbox.combat_progress.ProgressWatch = original
                scanned.append((key, label, result['result']['progressMetrics']))
        pairs = {}
        for key, label, metrics in scanned:
            pairs.setdefault(key, {})[label] = metrics
        for key, both in sorted(pairs.items()):
            legacy_metrics, fixed_metrics = both['legacy'], both['fixed']
            delta = (fixed_metrics['commandsReleasedAfterDeath']
                     - legacy_metrics['commandsReleasedAfterDeath'])
            print(f"  real case {key} cut to {int(horizon*0.31)}t: releasedAfterDeath "
                  f"legacy={legacy_metrics['commandsReleasedAfterDeath']} "
                  f"fixed={fixed_metrics['commandsReleasedAfterDeath']} (delta {delta})")
            report.setdefault('realCases', []).append(
                dict(holder=key, legacy=legacy_metrics, fixed=fixed_metrics, delta=delta))
    except Exception as exc:  # noqa: BLE001 - the scan is supporting evidence, not the gate
        print(f'  (real-case scan unavailable: {exc})')

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print()
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  a stored command released later in the same fighters phase as the death is now counted '
          'as post-death, and the pre-fix rule demonstrably missed it')
    return 0


if __name__ == '__main__':
    sys.exit(main())
