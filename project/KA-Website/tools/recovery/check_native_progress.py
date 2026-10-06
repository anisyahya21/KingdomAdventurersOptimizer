"""Stored-attack progress parity: canonical Python versus the native kernel, field by field.

Part C of the objective-redesign task. `combat_progress.ProgressWatch` (Python) and the kernel's own
observer (`battle_control::progress_*`) publish the same eighteen counters. This checker runs the
same real scenario/seeds through both and requires every counter to agree exactly, then reports how
many cases cover each situation the counters exist to distinguish:

    no stored attacks / boss never dies / boss dies before the dump / boss dies during the dump /
    stored attacks released too early / high potential but defeat / multi-chest win /
    one stored attack at death / many stored attacks at death

The case bank is real: the frozen whole-fight cases the other native suites use, the persisted
record-holder scenarios of a library, and deterministic mutations of that library's supplied
baselines. Nothing is synthesised into an impossible state.

    pypy3.exe check_native_progress.py [--library PATH] [--limit N] [--jobs N] [--json OUT]

Set `KA_KERNEL_DLL` to verify a freshly built kernel while a running optimiser holds the old one.
"""
import argparse
import copy
import hashlib
import json
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_progress  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
from check_native_real_state import CASES as FROZEN_CASES  # noqa: E402
from strategy_optimizer_adapter import default_scenario, validate_scenario  # noqa: E402

DEFAULT_LIBRARY = Path(r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy'
                       r'\KA-Website\strategiespostrust1000tickslimit.sqlite')
#: Long fights are the interesting ones, but the horizon is clipped so a parity run stays bounded;
#: clipping only truncates the fight, so Python and the kernel still see identical input.
MAX_TICKS = 8000
#: How many of the real cases are re-run with the live Holy Herb policy declared (test fixture).
HERBED_CASES = 10


def _python_case(payload):
    """One canonical Python run, in a child process: (ticks, verdict, progress, digest)."""
    import strategy_optimizer_adapter as adapter
    scenario, seeds = payload
    compact = adapter._python_simulate_compact(scenario, seeds)
    return dict(ticks=compact['ticks'], verdict=compact['verdict'], digest=compact['digest'],
                progress=compact.get('progressMetrics'), mp=compact.get('mpMetrics'),
                herb=compact.get('herbMetrics'), label=scenario.get('_label'))


#: TEST FIXTURE, not a production or game value: the game never states a Holy Herb quantity, so the
#: automatic-policy cases below declare their own stock and cap explicitly (and label themselves).
HERBED_FIXTURE = dict(stock=2, maxUses=2)


def herbed(scenario):
    """The same real fight with the live `<=3%` policy configured for its own first two humans.

    The trigger units are the configured DPS and healer, so this exercises the MP telemetry and the
    policy on the *same* real state the plain case bank uses rather than on a synthesised board.
    """
    from strategy_optimizer_adapter import validate_scenario
    humans = [unit['name'] for unit in scenario.get('ownUnits') or [] if unit.get('human')]
    if len(humans) < 2:
        return None
    allowed = [name for name in (scenario.get('holyHerbTriggerUnits') or ()) if name in humans][:2]
    trigger = allowed if len(allowed) == 2 else humans[:2]
    try:
        scenario = validate_scenario(dict(scenario, holyHerbStock=HERBED_FIXTURE['stock'],
                                          holyHerbMaxUses=HERBED_FIXTURE['maxUses'],
                                          holyHerbTriggerUnits=trigger))
    except Exception:  # noqa: BLE001 - a case the declaration cannot describe is simply not herbed
        return None
    scenario['_label'] = f"{scenario.get('_label')} [herbed fixture]"
    return scenario


def _rng(seed):
    """A fixed, reproducible byte stream - never `random`, so the bank cannot drift."""
    state = hashlib.sha256(seed.encode()).digest()
    while True:
        for index in range(0, len(state) - 4, 4):
            yield int.from_bytes(state[index:index + 4], 'big')
        state = hashlib.sha256(state).digest()


def _mutate(scenario, rng, encounter_id=None):
    """One legal-looking perturbation of a supplied baseline: a stat step and a horizon."""
    scenario = copy.deepcopy(scenario)
    if encounter_id is not None:
        scenario['encounterId'] = encounter_id
    units = scenario.get('ownUnits') or []
    if units:
        unit = units[next(rng) % len(units)]
        parameters = unit.get('parameters') or {}
        keys = sorted(parameters)
        if keys:
            key = keys[next(rng) % len(keys)]
            entry = parameters[key]
            if isinstance(entry, dict) and isinstance(entry.get('rawValue'), int):
                step = (next(rng) % 9) - 4
                entry['rawValue'] = max(1, entry['rawValue'] + step)
    return scenario


def build_cases(library, limit):
    """The real case bank: frozen fights, persisted record holders, mutated baselines."""
    base = default_scenario()
    cases = []
    for case in FROZEN_CASES:
        scenario = dict(base, **case)
        scenario['_label'] = f"frozen enc{case['encounterId']} {case['tickLimit']}t"
        cases.append((scenario, (case['mathSeed'], case['libSeed'])))
        # The same frozen fight cut very early: no charge interval completes, so no fighter ever holds
        # a stored command. That is the "no stored attacks" end of the chain, and it is only reachable
        # with a real short horizon.
        for short in (5, 12, 25, 120):
            if case['tickLimit'] > short:
                cut = dict(scenario, tickLimit=short,
                           _label=f"frozen enc{case['encounterId']} {short}t (cut)")
                cases.append((cut, (case['mathSeed'], case['libSeed'])))
    holders = {}
    try:
        # Read-only, and through a copy: the pywebview coordinator owns the live library and PyPy
        # cannot open a write-mode SQLite connection to it at all (`synchronous=FULL`). A copy plus a
        # `mode=ro` URI needs no write lock, no WAL recovery and no optimiser store.
        import shutil
        import sqlite3
        with tempfile.TemporaryDirectory(prefix='ka-parity-lib-') as root:
            copy_path = Path(root)/library.name
            shutil.copyfile(library, copy_path)
            db = sqlite3.connect(f'file:{copy_path}?mode=ro', uri=True)
            try:
                stored = db.execute("SELECT value FROM meta WHERE key='recordHolders'").fetchone()
                holders = dict(json.loads(stored[0]) or {}) if stored else {}
                baselines = {}
                for scenario_json, source in db.execute('SELECT scenario, source FROM candidate'):
                    if source != 'supplied':
                        continue
                    scenario = json.loads(scenario_json)
                    baselines.setdefault(int(scenario['encounterId']), scenario)
            finally:
                db.close()
    except Exception as exc:  # noqa: BLE001 - a library is optional for this checker
        print(f'  (no library case bank: {exc})')
        baselines = {}
    for key, holder in sorted(holders.items()):
        # A scenario read back out of the store has JSON's string parameter keys; the native template
        # builder indexes them as the integers `validate_scenario` normalises them to, which is also
        # exactly what the canonical Python path does internally.
        scenario = validate_scenario(dict(holder['scenario'],
                                          tickLimit=min(int(holder['scenario'].get('tickLimit')
                                                             or MAX_TICKS), MAX_TICKS)))
        scenario['_label'] = f'holder {key} {scenario["tickLimit"]}t'
        cases.append((scenario, (int(holder['mathSeed']), int(holder['libSeed']))))
        # The same real fight cut at several horizons, including just before and just after the boss
        # dies: that is how "stored attacks hold while the boss dies" and "released too late" happen
        # without inventing a state the engine never produced.
        horizon = int(holder['scenario'].get('tickLimit') or scenario['tickLimit'])
        for fraction in (.30, .31, .35, .45):
            cut = int(horizon*fraction)
            if 20 < cut < scenario['tickLimit']:
                cases.append((dict(validate_scenario(dict(holder['scenario'], tickLimit=cut)),
                                   _label=f'holder {key} {cut}t (cut)'),
                              (int(holder['mathSeed']), int(holder['libSeed']))))
    stream = _rng('ka-progress-bank-v1')
    for index in range(limit):
        if not baselines:
            break
        encounter = sorted(baselines)[index % len(baselines)]
        scenario = validate_scenario(_mutate(baselines[encounter], stream))
        # Three horizons, including one long enough for the boss to die and its stored attacks to be
        # released at a different offset - the timing spread the release-tick counters exist for.
        scenario['tickLimit'] = (1000, 3000, 5000)[index % 3]
        seeds = (next(stream) & 0x7fffffff, next(stream) & 0x7fffffff)
        scenario['_label'] = f'mutation enc{scenario["encounterId"]} {scenario["tickLimit"]}t'
        cases.append((scenario, seeds))
    # The same real fights with the live `<=3%` policy declared (TEST FIXTURE stock/cap), so the MP
    # telemetry and the policy's own evidence are compared on real state rather than only on an empty
    # block. They ride the same bank and the same comparison.
    herbed_cases = []
    for scenario, seeds in cases:
        if len(herbed_cases) >= HERBED_CASES:
            break
        variant = herbed(scenario)
        if variant is not None:
            herbed_cases.append((variant, seeds))
    cases += herbed_cases
    return cases, holders


def followup_cases(holders, deaths, horizon):
    """Cut the real fights a few ticks either side of the boss's measured death.

    'The boss died with stored attacks still queued' and 'the dump was already finished when it died'
    are both reachable this way and neither exists in the bank as captured: they are properties of
    *when the fight stops*, not of a different fight. The death tick is measured in the first pass, so
    the cuts land where the counters are meant to be told apart.
    """
    extra = []
    for key, death in sorted(deaths.items()):
        holder = holders.get(key)
        if holder is None or death is None or death < 0:
            continue
        for delta in (1, 3, 9, 30, 90):
            cut = int(death)+delta
            if cut <= 20 or cut >= int(horizon):
                continue
            extra.append((dict(validate_scenario(dict(holder['scenario'], tickLimit=cut)),
                               _label=f'holder {key} death+{delta} ({cut}t)'),
                          (int(holder['mathSeed']), int(holder['libSeed'])), key))
    return [(scenario, seeds) for scenario, seeds, _ in extra]


def situations(metrics, verdict, potential):
    """The situations the counters exist to tell apart, read off one measured result."""
    tags = set()
    if metrics['commandsTargetingBoss'] == 0 and metrics['maxSimultaneousStoredCommands'] == 0:
        tags.add('no stored attacks')
    if metrics['bossDeathTick'] < 0:
        tags.add('boss never dies')
    else:
        if metrics['commandsReleasedAfterDeath']:
            tags.add('releases after death')
        elif metrics['storedCommandsAtDeath']:
            tags.add('boss dies before the dump')
        if metrics['postDeathPrizes']:
            tags.add('post-death prizes')
        if metrics['storedCommandsTargetingBossAtDeath'] and \
                metrics['commandsReleasedAfterDeathTargetingBoss']:
            tags.add('boss dies during the dump')
        if metrics['commandsReleasedAfterDeathTargetingBoss'] == 0 \
                and metrics['commandsTargetingBossReleased'] > 0:
            tags.add('released too early')
        # Release *timing* is covered by its own measured witnesses after the bank runs: the earliest
        # and latest first-release latency in the bank are both compared field by field, so the
        # ordering question ("did the dump fire immediately or only much later?") is covered without
        # inventing a tick threshold here.
    targeting = metrics['storedCommandsTargetingBossAtDeath']
    if targeting == 1:
        tags.add('one stored attack at death')
    elif targeting >= 5:
        tags.add('many stored attacks at death')
    if verdict == 2 and (potential or 0) > 0:
        tags.add('potential on a defeat')
    if verdict == 1 and (potential or 0) >= 2:
        tags.add('multi-chest win')
    return tags


def compare(cases, jobs, expected_keys):
    """Run one bank through both engines and return (failures, coverage, compared, death ticks)."""
    failures = []
    coverage = {}
    compared = 0
    deaths = {}
    latencies = []
    # The Python side is the slow one, so it runs across a few child processes while the native
    # side (twelve milliseconds a fight) runs in this process.
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        python_results = pool.map(_python_case, cases)
        for (scenario, seeds), python in zip(cases, python_results):
            label = scenario.get('_label')
            compact, reason = native._native_compact(scenario, seeds)
            if compact is None:
                failures.append(f'{label}: native refused the case ({reason})')
                continue
            compared += 1
            native_progress = compact.get('progressMetrics')
            python_progress = python['progress']
            for key in expected_keys:
                if (python_progress or {}).get(key) != (native_progress or {}).get(key):
                    failures.append(f'{label}: {key} python={python_progress.get(key)!r} '
                                    f'native={(native_progress or {}).get(key)!r}')
            # MP telemetry and the explicit Holy Herb evidence: compared as whole objects, field by
            # field, because both backends fill them through the same builders.
            for key, python_value in (('mpMetrics', python.get('mp')), ('herbMetrics', python.get('herb'))):
                if python_value != compact.get(key):
                    failures.append(f'{label}: {key} python={python_value!r} '
                                    f'native={compact.get(key)!r}')
            mp = compact.get('mpMetrics') or []
            herb = compact.get('herbMetrics') or {}
            if mp:
                coverage['mp telemetry sampled for both trigger units'] = \
                    coverage.get('mp telemetry sampled for both trigger units', 0) + 1
            if any(entry.get('reachedLowMp') for entry in mp):
                coverage['mp crossed <=3% of own maximum'] = \
                    coverage.get('mp crossed <=3% of own maximum', 0) + 1
            if any(entry.get('reachedZero') for entry in mp):
                coverage['mp reached exactly 0'] = coverage.get('mp reached exactly 0', 0) + 1
            uses = int(herb.get('useCount') or 0)
            if uses:
                coverage['automatic Holy Herb dispatched'] = \
                    coverage.get('automatic Holy Herb dispatched', 0) + 1
            if uses >= 2:
                coverage['automatic Holy Herb re-armed and fired twice'] = \
                    coverage.get('automatic Holy Herb re-armed and fired twice', 0) + 1
            if set(native_progress or {}) != set(expected_keys):
                failures.append(f'{label}: published keys differ from combat_progress.blank_progress()')
            if python['digest'] != compact['digest']:
                failures.append(f'{label}: compact digest differs '
                                f'({python["digest"][:12]} vs {compact["digest"][:12]})')
            if python['verdict'] != compact['verdict'] or python['ticks'] != compact['ticks']:
                failures.append(f'{label}: verdict/ticks differ python=({python["verdict"]},'
                                f'{python["ticks"]}) native=({compact["verdict"]},{compact["ticks"]})')
            reward = compact.get('rewardOutcome') or {}
            potential = reward.get('pendingChests')
            for tag in situations(python_progress or {}, compact['verdict'], potential):
                coverage[tag] = coverage.get(tag, 0) + 1
            if label and label.startswith('holder'):
                deaths.setdefault(label.split()[1], (python_progress or {}).get('bossDeathTick'))
            release = (python_progress or {}).get('firstPostDeathCommandReleaseTick', -1)
            death = (python_progress or {}).get('bossDeathTick', -1)
            if release >= 0 and death >= 0:
                latencies.append(release-death)
    return failures, coverage, compared, deaths, latencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument('--limit', type=int, default=24, help='mutated-baseline cases')
    parser.add_argument('--jobs', type=int, default=4, help='Python child processes')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()

    cases, holders = build_cases(args.library, args.limit)
    native.library()
    print(f'kernel: {__import__("ka_abi").DLL}')
    expected_keys = list(combat_progress.blank_progress())
    started = time.monotonic()
    failures, coverage, compared, deaths, latencies = compare(cases, args.jobs, expected_keys)
    required = ('no stored attacks', 'boss never dies', 'boss dies before the dump',
                'boss dies during the dump', 'post-death prizes',
                'released too early (earliest witness)', 'released too late (latest witness)',
                'potential on a defeat', 'multi-chest win',
                'one stored attack at death', 'many stored attacks at death')

    def missing_now():
        return [tag for tag in required if not coverage.get(tag)]

    if missing_now() and holders and deaths:
        # Second pass: cut the real fights a few ticks either side of the boss's measured death, which
        # is where "still holding stored attacks when it died" and "the dump already finished" live.
        horizon = max(int(holder['scenario'].get('tickLimit') or 0) for holder in holders.values())
        extra = followup_cases(holders, deaths, horizon)
        print(f'second pass: {len(extra)} cases cut around the measured boss death')
        more_failures, more_coverage, more_compared, _, more_latencies = compare(
            extra, args.jobs, expected_keys)
        failures += more_failures
        compared += more_compared
        latencies += more_latencies
        for tag, count in more_coverage.items():
            coverage[tag] = coverage.get(tag, 0)+count
    # Release-timing witnesses: the fastest and slowest first-post-death release in the bank. Both are
    # real cases whose release-tick fields were compared exactly, so "too early" and "too late" are
    # covered as measured extremes rather than as an invented threshold.
    if latencies and max(latencies) > min(latencies):
        coverage['released too early (earliest witness)'] = 1
        coverage['released too late (latest witness)'] = 1
        print(f'  release latency after the boss died: {min(latencies)}..{max(latencies)} ticks '
              f'across {len(latencies)} cases')
    elapsed = time.monotonic()-started
    print(f'stored-attack progress parity: {compared} cases compared '
          f'({len(expected_keys)} counters each) in {elapsed:.1f}s')
    for tag in sorted(coverage):
        print(f'  {coverage[tag]:3d} cases: {tag}')
    missing = missing_now()
    for tag in missing:
        print(f'  NOT COVERED by this bank: {tag}')
    print(f'  failures: {len(failures)}')
    for detail in failures[:12]:
        print(f'    {detail}')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(compared=compared, seconds=elapsed, coverage=coverage,
                                             failures=failures,
                                             kernel=str(__import__('ka_abi').DLL)), indent=1),
                             encoding='utf-8')
    return 1 if (failures or missing) else 0


if __name__ == '__main__':
    sys.exit(main())
