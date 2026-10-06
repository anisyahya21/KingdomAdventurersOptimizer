"""DIAGNOSTIC ONLY - real A/S job DEX provenance + a fast native DEX screen over all encounters.

Two halves, both read-only:

1. **Job provenance.** Every combat A- and S-rank row in `KA GameData - Job.csv`, its natural DEX
   curve (`initValue + raiseValue * (level - 1)` up to the row's own `maxLevel`), the reachable DEX
   value set those curves can actually occupy, and whether equipment can *lower* DEX.
2. **Fast screen.** For each encounter: the boss/follower DEX caps from the stored roster, a small
   ladder of mechanically distinct DEX classes snapped to values real A/S curves can reach, and 16
   fresh seeds per class run through the **native compact path** (no per-tick tracing).

    python screen_dex_native.py --pairs 16 --json out.json
    python screen_dex_native.py --encounters 18 19 --pairs 16
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_setup                                                # noqa: E402
import strategy_optimizer                                          # noqa: E402
import strategy_optimizer_adapter as adapter                       # noqa: E402
from combat_resolution import critical_rate, hit_rate              # noqa: E402
from derive_search_bounds import PARAM_ORDER, combat_jobs, equipment_rows, equipment_parameter
from strategy_optimizer_adapter import stats                       # noqa: E402
from test_counter_level_arms import FAMILY                         # noqa: E402

DEX_PID = 19
DEX_INDEX = PARAM_ORDER.index(DEX_PID)
DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
THRESHOLDS = (10, 25, 50)


def job_rows():
    """Every combat A/S row with its natural DEX curve and the DEX values it can occupy."""
    out = []
    for job in combat_jobs():
        if not job['combat'] or job['rank'] not in ('A', 'S'):
            continue
        max_level, _need, init, raise_value = job['blocks'][DEX_INDEX]
        values = [init + raise_value * (level - 1) for level in range(1, max_level + 1)]
        out.append(dict(rank=job['rank'], job=job['sheetName'], init=init, raiseValue=raise_value,
                        maxLevel=max_level, dexAtMax=values[-1], values=values))
    return out


def reachable_values(jobs):
    return sorted({value for job in jobs for value in job['values']})


def snap(value, reachable):
    return min(reachable, key=lambda candidate: (abs(candidate - value), candidate))


def equipment_dex_range():
    rows = [row for row in equipment_rows() if len(row.get('parameters') or []) > DEX_INDEX]
    values = [equipment_parameter(row['parameters'][DEX_INDEX], 99) for row in rows]
    return dict(rows=len(values), minimum=min(values), maximum=max(values),
                negatives=sum(1 for value in values if value < 0))


def unit_key(scenario):
    """The damage dealer: the multi-hit family carrier, else the human with the highest ATK."""
    for unit in scenario['ownUnits']:
        if set(unit['skills']) >= set(FAMILY):
            return unit['name']
    prepared = stats(scenario)
    humans = [name for name, table in prepared.items() if not table['monster']]
    return max(humans, key=lambda name: prepared[name]['parameters'][13]['value'])


def roster(scenario):
    prepared = combat_setup.prepare_setup(json.loads(json.dumps(scenario)))
    out = []
    for fighter in prepared['encounter']['fighters']:
        out.append(dict(name=fighter['name'], boss=bool(fighter['leaderIdentity']),
                        hp=fighter['parameters']['10']['rawValue'],
                        defense=fighter['effectiveDefense'],
                        agility=fighter['parameters']['15']['rawValue'],
                        luck=fighter['parameters']['16']['rawValue']))
    return out


def dex_cap(agility, luck):
    """The DEX at which `hit_rate` reaches its 97 clamp against this target."""
    return max(0, 97 - 150 + agility // 5 + luck // 5)


def set_dex(scenario, name, dex):
    out = json.loads(json.dumps(scenario))
    unit = next(u for u in out['ownUnits'] if u['name'] == name)
    key = str(DEX_PID) if str(DEX_PID) in unit['parameters'] else DEX_PID
    current = stats(scenario)[name]['parameters'][DEX_PID]['value']
    unit['parameters'][key]['rawValue'] = int(unit['parameters'][key]['rawValue']) + (dex - current)
    return out


def pick_candidate(db, encounter):
    rows = db.execute('SELECT c.id AS id, c.scenario AS scenario, c.label AS label, m.defeat AS defeat'
                      ' FROM candidate c JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=?',
                      (encounter,)).fetchall()
    best = None
    for row in rows:
        scenario = json.loads(row['scenario'])
        total = db.execute('SELECT value FROM meta WHERE key=?',
                           ('aggregate:%s:validation' % row['id'],)).fetchone()
        total = json.loads(total[0]) if total else {}
        count = int(total.get('chestCount') or 0)
        if count < 32:
            continue
        mean = float(total.get('chestSum') or 0) / count
        family = bool(scenario['ownUnits'] and set(scenario['ownUnits'][0]['skills']) >= set(FAMILY))
        score = (mean, family)          # the best-measured productive resident wins; family is a tie-break
        if best is None or score > best[0]:
            best = (score, row['id'], scenario, row['label'], mean, count)
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounters', type=int, nargs='+', default=list(range(20)))
    parser.add_argument('--candidate', nargs='+', metavar='ENC:ID',
                        help='force a specific resident for an encounter, e.g. 19:f7e6911be2a49c')
    parser.add_argument('--tag', default='', help='label stored with each run in the JSON')
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=2600000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    forced = {}
    for entry in args.candidate or []:
        encounter_text, candidate_id = entry.split(':', 1)
        forced[int(encounter_text)] = candidate_id

    jobs = job_rows()
    reachable = reachable_values(jobs)
    equipment = equipment_dex_range()
    print('=== real A/S job DEX provenance (%d combat rows) ===' % len(jobs))
    print('  reachable natural DEX values (%d distinct): %s' % (len(reachable), reachable[:30]))
    print('  A/S jobs whose level-1 DEX is the synthetic floor (2): %s'
          % [job['job'] for job in jobs if job['init'] == 2])
    print('  A/S jobs whose whole curve can sit at DEX <= 10 (any level): %s'
          % [job['job'] for job in jobs if min(job['values']) <= 10])
    print('  equipment DEX contribution at level 99: min %d max %d, negative rows %d'
          % (equipment['minimum'], equipment['maximum'], equipment['negatives']))
    print('  lowest DEX reachable: natural %d (level-1 of an init-2 job); equipment can only add'
          % min(job['init'] for job in jobs))

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    output, report = [], []
    started = time.perf_counter()
    battles = native = 0
    for encounter in args.encounters:
        if encounter in forced:
            row = db.execute('SELECT id, scenario, label FROM candidate WHERE id LIKE ?',
                             (forced[encounter] + '%',)).fetchone()
            if row is None:
                print('encounter %-2d: forced candidate %s not found' % (encounter, forced[encounter]))
                continue
            entry = db.execute('SELECT value FROM meta WHERE key=?',
                               ('aggregate:%s:validation' % row['id'],)).fetchone()
            entry = json.loads(entry[0]) if entry else {}
            count = int(entry.get('chestCount') or 0)
            chosen = (None, row['id'], json.loads(row['scenario']), row['label'],
                      (float(entry.get('chestSum') or 0) / count) if count else float('nan'), count)
        else:
            chosen = pick_candidate(db, encounter)
        if chosen is None:
            print('encounter %-2d: no measured resident - skipped' % encounter)
            continue
        _score, candidate, scenario, label, mean, count = chosen
        name = unit_key(scenario)
        enemy_roster = roster(scenario)
        boss = next(e for e in enemy_roster if e['boss'])
        followers = [e for e in enemy_roster if not e['boss']]
        stored_dex = stats(scenario)[name]['parameters'][DEX_PID]['value']
        follower_cap = max(dex_cap(e['agility'], e['luck']) for e in followers)
        boss_cap = dex_cap(boss['agility'], boss['luck'])
        targets = sorted({2, follower_cap, (follower_cap + boss_cap) // 2, boss_cap, boss_cap + 20})
        classes = []
        for target in targets:
            value = snap(max(2, target), reachable)
            if value not in [c for c in classes]:
                classes.append(value)
        print()
        print('encounter %-2d  candidate %s  %s  (measured mean %.2f over %d)'
              % (encounter, candidate[:12], label[:40], mean, count))
        print('  DPS %s  stored DEX %d | boss %s agi %d luck %d -> cap %d | follower cap %d'
              % (name, stored_dex, boss['name'], boss['agility'], boss['luck'], boss_cap,
                 follower_cap))
        print('  DEX classes (snapped to real A/S values): %s' % classes)
        per_class = {}
        for dex in classes:
            chests, pending, ticks, death, bossq, reentry, prizes = [], [], [], [], [], [], []
            for index in range(args.pairs):
                seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
                metrics = adapter.simulate(set_dex(scenario, name, dex), seeds)
                battles += 1
                native += 1 if metrics.get('resultBackend') == 'native' else 0
                progress = metrics.get('progressMetrics') or {}
                reward_block = metrics.get('rewardOutcome') or {}
                reward = reward_block.get('awardedChests')
                pending_value = reward_block.get('pendingChests')
                if isinstance(reward, (int, float)):
                    chests.append(reward)
                if isinstance(pending_value, (int, float)):
                    pending.append(pending_value)
                if isinstance(metrics.get('ticks'), (int, float)):
                    ticks.append(metrics['ticks'])
                if isinstance(progress.get('bossDeathTick'), (int, float)) and progress['bossDeathTick'] >= 0:
                    death.append(progress['bossDeathTick'])
                if isinstance(progress.get('storedCommandsTargetingBossAtDeath'), (int, float)):
                    bossq.append(progress['storedCommandsTargetingBossAtDeath'])
                if isinstance(progress.get('postDeathBossReentries'), (int, float)):
                    reentry.append(progress['postDeathBossReentries'])
                if isinstance(progress.get('postDeathPrizes'), (int, float)):
                    prizes.append(progress['postDeathPrizes'])
                output.append(dict(encounter=encounter, candidate=candidate, dex=dex, seed=list(seeds),
                                   backend=metrics.get('resultBackend'), chests=reward,
                                   pendingChests=pending_value,
                                   ticks=metrics.get('ticks'), tag=args.tag, **progress))
            summary = dict(
                dex=dex, hitBoss=hit_rate(dex, boss['agility'], boss['luck']),
                hitFollowerWorst=min(hit_rate(dex, e['agility'], e['luck']) for e in followers),
                mean=round(statistics.fmean(chests), 2) if chests else None,
                median=round(statistics.median(chests), 1) if chests else None,
                se=round(statistics.pstdev(chests) / (len(chests) ** 0.5), 2) if len(chests) > 1 else None,
                **{'ge%d' % t: (round(sum(1 for v in chests if v >= t) / len(chests), 3)
                                if chests else None) for t in THRESHOLDS},
                death=round(statistics.median(death), 0) if death else None,
                ticks=round(statistics.median(ticks), 0) if ticks else None,
                bossQueueAtDeath=round(statistics.fmean(bossq), 1) if bossq else None,
                reentries=round(statistics.fmean(reentry), 1) if reentry else None,
                prizes=round(statistics.fmean(prizes), 1) if prizes else None,
                pendingMean=round(statistics.fmean(pending), 2) if pending else None,
                pendingMedian=round(statistics.median(pending), 1) if pending else None,
                chestsCount=len(chests))
            per_class[dex] = summary
            print('    DEX %-4d bossHit %3d%% worstFoll %3d%% | certified mean %s median %s se %s '
                  'P>=10 %s P>=25 %s | pending mean %s median %s | death %s ticks %s '
                  'bossQ@death %s reentries %s prizes %s (n=%d)'
                  % (dex, summary['hitBoss'], summary['hitFollowerWorst'], summary['mean'],
                     summary['median'], summary['se'], summary['ge10'], summary['ge25'],
                     summary['pendingMean'], summary['pendingMedian'], summary['death'],
                     summary['ticks'], summary['bossQueueAtDeath'], summary['reentries'],
                     summary['prizes'], summary['chestsCount']))
        report.append(dict(encounter=encounter, candidate=candidate, label=label, dps=name,
                           measuredMean=mean, measuredCount=count, boss=boss,
                           followerCap=follower_cap, bossCap=boss_cap, storedDex=stored_dex,
                           classes=per_class))
    elapsed = time.perf_counter() - started
    print()
    print('=== performance ===')
    print('  battles %d (native %d, python %d) in %.1fs -> %.1f battles/s'
          % (battles, native, battles - native, elapsed, battles / elapsed if elapsed else 0))
    if args.json:
        Path(args.json).write_text(json.dumps(dict(report=report, runs=output, jobs=jobs,
                                                   reachable=reachable, equipment=equipment),
                                              indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
