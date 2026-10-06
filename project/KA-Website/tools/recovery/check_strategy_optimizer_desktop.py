"""Run real WebView2 integration without taking over the user's keyboard or mouse."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from strategy_optimizer import OBJECTIVE_VERSION, Store, scope
from strategy_optimizer_adapter import default_scenario, provenance, stats
from strategy_optimizer_desktop import Bridge, asset_handler, next_library_path

ELAPSED_RE = re.compile(r'(\d+):(\d{2}):(\d{2})')


def library_naming_checks():
    """The next-free numbered library filename, as a plain unit test (no WebView).

    The default name in the New library save dialog is the next free `strategiesv<N>.sqlite`. Only
    the exact numbered convention counts, and a collision on the computed number advances rather
    than offering an overwrite. This is a user-facing filename sequence, never the library's own
    search-space/objective version, so it is tested on its own.
    """
    checks = 0
    with tempfile.TemporaryDirectory(prefix='ka-library-name-') as root:
        directory = Path(root)
        assert next_library_path(directory).name == 'strategiesv1.sqlite', 'empty directory'
        checks += 1
        for name in ('strategiesv3.sqlite', 'strategiesv4.sqlite', 'strategiesv5.sqlite'):
            (directory/name).write_text('', encoding='utf-8')
        assert next_library_path(directory).name == 'strategiesv6.sqlite', 'highest number + 1'
        checks += 1
        for name in ('strategies.sqlite', 'strategiesv5.notes.sqlite', 'strategiesv.sqlite',
                     'strategiesv2.sqlite.bak', 'other.sqlite'):
            (directory/name).write_text('', encoding='utf-8')
        assert next_library_path(directory).name == 'strategiesv6.sqlite', 'non-matching ignored'
        checks += 1
        (directory/'StrategiesV6.SQLITE').write_text('', encoding='utf-8')
        assert next_library_path(directory).name == 'strategiesv7.sqlite', 'case-insensitive match'
        checks += 1
        (directory/'strategiesv7.sqlite').mkdir()
        assert next_library_path(directory).name == 'strategiesv8.sqlite', 'collision advances'
        checks += 1
    print(f'  library naming: {checks} unit checks passed', flush=True)
    return checks


def elapsed_seconds(text):
    """Read the top-line `Elapsed HH:MM:SS` reading, so pause/resume can be asserted on screen."""
    match = ELAPSED_RE.search(text or '')
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


WORKERS_JS = """
window.__kaFire = (node, type, buttons, id) => node.dispatchEvent(new PointerEvent(type, {
  bubbles: true, cancelable: true, button: 0, buttons, pointerId: id, pointerType: 'mouse',
  isPrimary: true }));
window.__kaOpenWorkers = () => {
  if (document.querySelectorAll('[role=option]').length) return true;
  const trigger = document.querySelector('[data-optimizer-workers]');
  if (!trigger) return false;
  window.__kaFire(trigger, 'pointerdown', 1, 7);
  window.__kaFire(trigger, 'pointerup', 0, 7);
  trigger.click();
  return true;
};
window.__kaDismiss = () => {
  document.body.dispatchEvent(new PointerEvent('pointerdown', {
    bubbles: true, cancelable: true, button: 0, buttons: 1, pointerId: 9, pointerType: 'mouse',
    isPrimary: true }));
  return true;
};
window.__kaWorkerOptions = () =>
  Array.from(document.querySelectorAll('[role=option]')).map((node) => node.textContent.trim());
window.__kaPickWorker = (value) => {
  const options = Array.from(document.querySelectorAll('[role=option]'));
  const target = options.find((node) => node.textContent.trim() === String(value));
  if (!target) return false;
  /* Radix Select highlights the item under the pointer and only then commits it, so the sequence has
     to move over the item before pressing: a bare pointerdown/up pair selects whatever was still
     highlighted, which is why a synthetic click alone looked like it was ignored. */
  const move = (node, buttons) => node.dispatchEvent(new PointerEvent('pointermove', {
    bubbles: true, cancelable: true, button: 0, buttons, pointerId: 8, pointerType: 'mouse',
    isPrimary: true, clientX: node.getBoundingClientRect().x + 4,
    clientY: node.getBoundingClientRect().y + 4 }));
  move(target, 0);
  window.__kaFire(target, 'pointerover', 0, 8);
  window.__kaFire(target, 'pointerdown', 1, 8);
  window.__kaFire(target, 'pointerup', 0, 8);
  target.click();
  return true;
};
"""


def runtime_controls(js, bridge, wait):
    """The runtime controls this task adds, driven through the real page rather than the bridge.

    Elapsed time must hold while paused, continue on resume, freeze on stop and reset on the next
    Start; the session run count must be the delta against the library-wide count; and the worker
    dropdown must offer every count the host's ceiling allows - including 16 and 24 - and actually
    launch that many.
    """
    js(WORKERS_JS)
    for selector in ('[data-optimizer-elapsed]', '[data-optimizer-rate-session-runs]',
                     '[data-optimizer-rate-total-runs]', '[data-optimizer-workers-in-use]',
                     '[data-optimizer-rate-capacity]', '[data-optimizer-rate-runs-per-second]',
                     '[data-optimizer-perf-utilization]', '[data-optimizer-perf-boxes]',
                     '[data-optimizer-encounters]'):
        assert js(f"!!document.querySelector('{selector}')"), f'top line is missing {selector}'
    boxes = js("document.querySelector('[data-optimizer-perf-boxes]').innerText")
    for label in ('Actual speed', 'Capacity', 'Utilization', 'Workers', 'Elapsed',
                  'Runs this session', 'Total runs'):
        # The tile labels are uppercased by CSS, and innerText reports what is rendered.
        assert label.lower() in boxes.lower(), f'{label!r} metric box missing: {boxes!r}'
    # The horizon wording (item 14) and the encounter overview (Part C).
    page = js('document.body.innerText')
    assert js("!!document.querySelector('[data-optimizer-encounters] [data-optimizer-help][aria-label=\"How the encounter overview numbers are read\"]')"), 'the overview explanation is missing'
    stale = re.search(r'.{100}Unresolved at horizon.{100}', page, re.S)
    assert stale is None, f'the old horizon wording is still on the page near: {stale.group(0)!r}'
    # The focused overview explains no-verdict outcomes without filling the primary screen with
    # the tick/time diagnostic. Its numerical horizon is checked by the native/optimizer checks.
    cards = js("Array.from(document.querySelectorAll('[data-encounter-card]'))"
               ".map((node) => node.getAttribute('data-encounter-card'))")
    rows = js("document.querySelectorAll('[data-encounter-difficulty]').length")
    if len(cards) != 5 or rows != 20:
        raise AssertionError(f'encounter overview is {len(cards)} cards / {rows} difficulties')
    # The compact table keeps attempts in a column and the no-verdict count in its tooltip.
    if js("document.querySelectorAll('[data-encounter-attempts]').length") != 20:
        raise AssertionError('the attempts column is missing from the overview')
    print(f'  metric boxes: {", ".join(sorted(boxes.splitlines()))[:120]}')
    print(f'  encounter overview: {len(cards)} cards x {rows // len(cards)} difficulties '
          f'({cards})', flush=True)

    def elapsed():
        return elapsed_seconds(js("document.querySelector('[data-optimizer-elapsed]').innerText"))

    def line():
        return js("document.querySelector('[data-optimizer-run-bar]').innerText")

    def press_start():
        # The button is disabled while the page's own (2 s) poll still shows Running, so a click
        # issued before that poll lands would be ignored; wait for the page, not just the host.
        wait(lambda: js("!document.querySelector('[data-action=optimizer-start]').disabled"),
             'Start button enabled')
        js("document.querySelector('[data-action=optimizer-start]').click()")

    # ---- paused time does not accumulate (the page is already Paused from the first phase) ----
    wait(lambda: bridge.status()['state'] == 'Paused', 'paused before the controls phase')
    paused = elapsed()
    assert paused is not None, f'no HH:MM:SS reading in {line()!r}'
    time.sleep(2.5)
    held = elapsed()
    assert held <= paused + 1, f'paused elapsed advanced: {paused}s -> {held}s'

    # ---- resume continues from the held value, and the rate/achieving reading appears ----
    press_start()
    wait(lambda: bridge.status()['state'] == 'Running', 'resumed')
    time.sleep(7)
    running = elapsed()
    assert running >= held + 4, f'resume did not continue accumulating: {held}s -> {running}s'
    status = bridge.status()
    assert status['throughput']['battlesPerSecond'] is not None, 'no measured rate after 7 s'
    assert status['throughput']['achievedFraction'] is not None, 'no achieving fraction'
    text = line()
    for needle in ('Elapsed', 'Runs this session', 'Total runs', 'Workers', 'Actual speed',
                   'Capacity', 'Utilization', 'battles/hour measured', 'engine time'):
        assert needle.lower() in text.lower(), f'{needle!r} missing from the runtime line: {text!r}'
    print(f'  running: {text.strip()[:200]}', flush=True)

    # ---- pause again, then stop: both freeze the reading ----
    js("document.querySelector('[data-action=optimizer-pause]').click()")
    wait(lambda: bridge.status()['state'] == 'Paused', 'paused again')
    frozen = elapsed()
    time.sleep(2.5)
    assert elapsed() <= frozen + 1, 'paused elapsed advanced the second time'
    js("document.querySelector('[data-action=optimizer-stop]').click()")
    wait(lambda: bridge.status()['state'] not in ('Running', 'Saving'), 'stopped')
    stopped = elapsed()
    time.sleep(2.5)
    assert elapsed() <= stopped + 1, 'stopped elapsed advanced'
    library_runs = bridge.status()['totalRuns']

    # ---- the dropdown offers every count this machine allows, and Start uses the chosen one ----
    ceiling = bridge.status()['throughput']['ceiling']
    def open_workers():
        js('window.__kaOpenWorkers()')
        return js('window.__kaWorkerOptions()')

    offered = wait(open_workers, 'worker dropdown opened')
    expected = [count for count in (1, 2, 4, 8, 12, 16, 24) if count <= ceiling]
    assert [int(value) for value in offered] == expected, (
        f'offered {offered}, expected {expected} for ceiling {ceiling}')
    print(f'  worker ceiling {ceiling}; dropdown offers {offered}', flush=True)
    js('window.__kaDismiss()')
    wait(lambda: not js('window.__kaWorkerOptions()'), 'worker dropdown dismissed')

    started = {}
    for count in (16, 24):
        if count > ceiling:
            print(f'  {count} workers is above this ceiling; not offered, so not started', flush=True)
            continue
        options = wait(open_workers, f'{count} dropdown open')
        assert str(count) in options, f'{count} missing from {options}'
        assert js(f'window.__kaPickWorker({count})'), f'{count} was not offered in the dropdown'
        wait(lambda: js("document.querySelector('[data-optimizer-workers]').innerText.trim()")
             == str(count), f'selected {count} workers')
        press_start()
        wait(lambda: bridge.status()['state'] == 'Running', f'started with {count} workers', 240)
        wait(lambda: bridge.status()['throughput']['workers'] == count,
             f'host reports {count} workers', 60)
        wait(lambda: (bridge.status()['totalRuns'] or 0) > library_runs,
             f'a run completed with {count} workers', 240)
        # The page renders the host's last poll (every 2 s), so wait for the line to catch up with
        # the host before asserting what the status line reports.
        def workers_box():
            return js("document.querySelector('[data-optimizer-workers-in-use]').innerText")

        wait(lambda: re.search(rf'(?<!\\d){count}(?!\\d)', workers_box()) is not None,
             f'the Workers box reports {count}', 30)
        running_status = bridge.status()
        text = line()
        assert re.search(rf'(?<!\\d){count}(?!\\d)', workers_box()), \
            f'{count} not shown in the Workers box: {workers_box()!r}'
        assert 'runs this session' in text.lower() and 'total runs' in text.lower(), \
            f'run counts missing: {text!r}'
        session_runs = running_status.get('sessionRuns')
        total_runs = running_status['totalRuns']
        assert session_runs is not None, 'session run count missing after a new Start'
        assert session_runs < total_runs, (
            f'the session count {session_runs} is not distinct from the library count {total_runs}')
        assert elapsed() <= 8, f'elapsed did not reset on the new Start: {elapsed()}s'
        started[count] = dict(workers=running_status['throughput']['workers'],
                              sessionRuns=session_runs, libraryRuns=total_runs)
        print(f'  Start at {count}: host workers={running_status["throughput"]["workers"]} '
              f'sessionRuns={session_runs} libraryRuns={total_runs} elapsed={elapsed()}s', flush=True)
        library_runs = total_runs
        js("document.querySelector('[data-action=optimizer-stop]').click()")
        wait(lambda: bridge.status()['state'] not in ('Running', 'Saving'),
             f'stopped after {count} workers')

    # ---- the encounter overview scopes the family and run cards (Parts J/K/L) ----
    js("document.querySelector('[data-encounter-difficulty=\"19\"]').click()")
    wait(lambda: js("document.querySelector('[data-encounter-difficulty=\"19\"]')"
                    ".getAttribute('data-encounter-focused')") == 'true',
         'the overview focused encounter 19')
    # Strategy families now live in optional exploration; open it before testing its legacy flow.
    js("document.querySelector('[data-optimizer-explore]').open = true")
    scope = js("document.querySelector('[data-family-fight-scope]').innerText")
    if 'Wairo' not in scope:
        raise AssertionError(f'the family section does not name the focused fight: {scope!r}')
    print(f'  family scope: {scope}', flush=True)
    families = js("Array.from(document.querySelectorAll('[data-strategy-family]'))")
    if not families:
        raise AssertionError('no strategy family cards rendered after focusing a fight')
    metrics = js("document.querySelector('[data-family-metrics]').innerText")
    for needle in ('best chests earned', 'highest potential', 'attempts', 'win rate',
                   'no verdict by limit', 'consumables'):
        if needle not in metrics.lower():
            raise AssertionError(f'family card is missing {needle!r}: {metrics!r}')
    # Selecting a family must relabel the run cards as family-scoped, and the encounter-wide
    # records must not move.
    before = js("document.querySelector('[data-encounter-difficulty=\"19\"]').innerText")
    js("document.querySelector('[data-action=select-family-representative]').click()")
    wait(lambda: js("!!document.querySelector('[data-family-selected=\"true\"]')"),
         'a family is selected')
    runs_title = js("document.querySelector('[data-optimizer-runs]').innerText")
    if 'Selected Strategy Family' not in runs_title:
        raise AssertionError(f'run cards are not labelled as family-scoped: {runs_title[:200]!r}')
    if 'This Family' not in runs_title:
        raise AssertionError(f'run card labels do not name their scope: {runs_title[:200]!r}')
    after = js("document.querySelector('[data-encounter-difficulty=\"19\"]').innerText")
    if before != after:
        raise AssertionError('selecting a family changed the encounter-wide record row')
    print('  family selection relabelled the run cards and left the encounter row untouched',
          flush=True)
    # The overview is concise now: the separate record-holder replay/investigate buttons are gone,
    # and each of the four metrics is itself the click-through onto the focused strategy workspace.
    for stale in ('replay-record-earned', 'replay-record-potential', 'investigate-record-earned'):
        if js(f"!!document.querySelector('[data-action={stale}]')"):
            raise AssertionError(f'the removed overview control {stale!r} is still present')
    if js("!!document.querySelector('[data-encounter-records]')"):
        raise AssertionError('the removed lifetime-record-holder button row is still present')

    def host_metric(name):
        stats = (bridge.status().get('encounterStats') or {}).get('19:0') or {}
        if name == 'highest-potential':
            return stats.get('highestPotentialChests')
        if name == 'highest-earned':
            return stats.get('highestChestsEarned')
        row = next((entry for entry in (bridge.overview_average_leaders().get('leaders') or [])
                    if entry.get('encounterId') == 19 and entry.get('defeatCount') == 0), None)
        leader = (row or {}).get(
            'highestAvgPotential' if name == 'avg-potential' else 'highestAvgEarned')
        return leader['mean'] if leader else None

    def metric_selector(name):
        return f'{ROW_SELECTOR} [data-encounter-metric="{name}"]'

    metric_names = ('highest-potential', 'highest-earned', 'avg-potential', 'avg-earned')
    for name in metric_names:
        if not js(f"!!document.querySelector('{metric_selector(name)}')"):
            raise AssertionError(f'the {name} metric is missing from the overview row')

    def expected_value(name):
        # The page formats a maximum as an integer and an average to two decimals; a null metric is
        # the em dash, so the disabled state and the text are compared together.
        value = host_metric(name)
        if not isinstance(value, (int, float)):
            return '\u2014'
        if name in ('highest-potential', 'highest-earned'):
            return str(int(value))
        number = float(value)
        return str(int(number)) if number.is_integer() else f'{number:.2f}'

    for name in metric_names:
        wait(lambda name=name: js(f"document.querySelector('{metric_selector(name)} "
                                  "[data-encounter-metric-value]').innerText.trim()")
             == expected_value(name), f'{name} shows the host value')
        disabled = js(f"document.querySelector('{metric_selector(name)}').disabled")
        if disabled != (expected_value(name) == '\u2014'):
            raise AssertionError(f'{name} disabled-state does not match its value')
    started_holders = bridge.status().get('recordHolders') or {}
    print(f'  overview metrics: {", ".join(metric_names)} · record holders '
          f'{sorted(started_holders)[:4]}', flush=True)
    return started


ROW_SELECTOR = '[data-encounter-difficulty="19"]'
EXPAND_SELECTOR = '[data-action=expand-encounters]'


def encounter_overview(js, bridge, wait):
    """The overview row must show the persisted lifetime counters, not a recount of surviving rows.

    The page used to read `stat.attempts`/`wins`/`losses`/`unresolved`, four fields the coordinator
    stopped publishing, so Attempts rendered 0 on any library whose rows had been pruned. This drives
    the real page, compares what is on screen with what the host published, and then exercises the
    expansion action on the one-encounter harness library.
    """
    def cell(metric):
        # A chest maximum lives on its click-through metric cell; Attempts keeps its own hook.
        if metric in ('potential', 'earned'):
            selector = (f'[data-encounter-metric="highest-{metric}"] '
                        '[data-encounter-metric-value]')
        else:
            selector = f'[data-encounter-{metric}]'
        return js(f"document.querySelector('{ROW_SELECTOR} {selector}')"
                  ".innerText.trim()")

    def published():
        return (bridge.status().get('encounterStats') or {}).get('19:0') or {}

    # ---- the expansion action is offered, and says exactly what it will do -------------------
    if not js(f"!!document.querySelector('{EXPAND_SELECTOR}')"):
        raise AssertionError('a library holding one encounter was not offered the expansion action')
    text = js("document.querySelector('[data-optimizer-expand-encounters]').innerText")
    for needle in ('Adds supplied baselines for missing encounter/difficulty combinations',
                   'Existing runs and strategies are preserved',
                   'The library keeps its current simulation horizon'):
        if needle not in text:
            raise AssertionError(f'the expansion action is missing {needle!r}: {text!r}')
    if bridge.status().get('libraryEncounters') != [19]:
        raise AssertionError('the harness library does not report itself as encounter-19 only')
    print(f'  expansion action offered on a {len(text)}-char explanation', flush=True)

    # ---- Attempts is the persisted lifetime count, and it is not 0 --------------------------
    stat = published()
    if not stat.get('lifetimeAttempts'):
        raise AssertionError(f'the harness library recorded no attempts to check: {stat}')
    wait(lambda: cell('attempts') == str(int(published()['lifetimeAttempts'])),
         'Attempts shows the persisted lifetime count')
    if cell('attempts') == '0':
        raise AssertionError('Attempts rendered 0 while lifetimeAttempts is non-zero')

    # ---- the attempts tooltip retains the lifetime no-verdict count -------------------------
    shown = js(f"document.querySelector('{ROW_SELECTOR} [data-encounter-attempts]')"
               ".parentElement.getAttribute('title')")
    want = int(published()['lifetimeNoVerdict'])
    if f'{want} with no verdict by limit' not in shown:
        raise AssertionError(f'attempts tooltip lacks lifetimeNoVerdict {want}: {shown!r}')

    # ---- Potential/Earned are the lifetime maxima, unchanged by this fix --------------------
    # A library with no resolved run yet legitimately shows the em dash, so the cell is compared with
    # whatever the host published - count or absence - rather than skipped when the value is null.
    for metric, field in (('potential', 'highestPotentialChests'), ('earned', 'highestChestsEarned')):
        want = published().get(field)
        expect = str(int(want)) if isinstance(want, (int, float)) else '\u2014'
        if cell(metric) != expect:
            raise AssertionError(f'{metric} shows {cell(metric)!r}, host published {expect!r}')
    print(f'  encounter 19 row: attempts={cell("attempts")} no-verdict={shown!r} '
          f'potential={cell("potential")!r} earned={cell("earned")!r}', flush=True)

    # ---- the action expands without touching recorded work or the horizon -------------------
    before = bridge.status()
    js(f"document.querySelector('{EXPAND_SELECTOR}').click()")
    wait(lambda: bridge.status().get('libraryEncounters') == list(range(20)),
         'the library expanded to all twenty encounters')
    wait(lambda: not js(f"!!document.querySelector('{EXPAND_SELECTOR}')"),
         'the expansion action is gone once every encounter is present')
    after = bridge.status()
    if (after.get('totalRuns') or 0) != (before.get('totalRuns') or 0):
        raise AssertionError('expansion recorded simulations of its own')
    if (after.get('horizonTicks') or 0) != (before.get('horizonTicks') or 0):
        raise AssertionError('expansion changed the library horizon')
    kept = (after.get('encounterStats') or {}).get('19:0') or {}
    if kept.get('lifetimeAttempts') != stat.get('lifetimeAttempts'):
        raise AssertionError('expansion reset the encounter-19 lifetime counters')
    rows = js("document.querySelectorAll('[data-encounter-difficulty]').length")
    if rows != 20:
        raise AssertionError(f'after expansion the overview shows {rows} difficulty rows')
    print('  expansion: 20 encounters, horizon and encounter-19 lifetime counters unchanged '
          f'(runs {before.get("totalRuns")}, horizon {after.get("horizonTicks")})', flush=True)

    # ---- the lane objective is visible, and the upgrade is explicit -------------------------
    if not js("!!document.querySelector('[data-optimizer-lanes]')"):
        raise AssertionError('the discovery-lane panel is missing for the focused fight')
    lanes = js("Array.from(document.querySelectorAll('[data-lane]'))"
               ".map((node) => node.getAttribute('data-lane'))")
    if not lanes:
        raise AssertionError('no lane leader rows rendered')
    panel = js("document.querySelector('[data-optimizer-lanes]').innerText")
    for needle in ('discovery parent', 'reliable strategy'):
        if needle not in panel.lower():
            raise AssertionError(f'the lane panel does not distinguish {needle!r}: {panel!r}')
    for lane in lanes:
        evidence = js(f"document.querySelector('[data-lane={lane}] [data-lane-evidence]').innerText")
        for needle in ('runs', 'win', 'consumables'):
            if needle not in evidence.lower():
                raise AssertionError(f'lane {lane} evidence is missing {needle!r}: {evidence!r}')
    print(f'  discovery lanes: {lanes}', flush=True)

    if not js("!!document.querySelector('[data-action=migrate-objective]')"):
        raise AssertionError('a generation-1 library was not offered the objective upgrade')
    upgrade = js("document.querySelector('[data-optimizer-objective-upgrade]').innerText")
    for needle in ('never recorded', 'stored-attack lane stays empty'):
        if needle not in upgrade:
            raise AssertionError(f'the upgrade text does not say what cannot be reconstructed: {upgrade!r}')
    # The encounter expansion immediately before this is one host command on the page's single
    # command channel, so its promise can still be settling - the busy label set and this button
    # disabled - when the test reaches here. A click on a disabled button is silently dropped, which
    # is what made the objective look like it never migrated. Read the busy/disabled state, wait for
    # the channel to be idle, then click, and surface the host's own refusal if it still refuses.
    held = js("(() => { const b = document.querySelector('[data-action=migrate-objective]');"
              " const busy = document.querySelector('[data-optimizer-busy]')"
              " || document.querySelector('[data-optimizer-run-bar]');"
              " return { disabled: !!(b && b.disabled), label: busy ? busy.innerText.trim() : '' }; })()")
    wait(lambda: js("(() => { const b = document.querySelector('[data-action=migrate-objective]');"
                    " return !!b && !b.disabled; })()"),
         'the objective upgrade button is enabled')
    if held.get('disabled'):
        print(f'  objective upgrade deferred until the command channel was idle ({held["label"]!r})',
              flush=True)
    js("document.querySelector('[data-action=migrate-objective]').click()")
    wait(lambda: bridge.status().get('objectiveVersion') == OBJECTIVE_VERSION
         or js("!!document.querySelector('[data-optimizer-action-error]')"),
         'the objective upgraded or the host refused')
    if bridge.status().get('objectiveVersion') != OBJECTIVE_VERSION:
        raise AssertionError('the objective upgrade was refused: '
                             + js("(document.querySelector('[data-optimizer-action-error]')"
                                  " || {}).innerText || 'no reason shown'"))
    wait(lambda: not js("!!document.querySelector('[data-action=migrate-objective]')"),
         'the upgrade action is gone once the library is current')
    migration = bridge.status().get('objectiveMigration') or {}
    if not migration:
        raise AssertionError('the migration was not reported')
    print(f"  objective upgrade: version {bridge.status()['objectiveVersion']}, "
          f"{migration.get('runsReused')} runs reused, "
          f"{migration.get('runsWithProgressMetrics')} carried stored-attack metrics", flush=True)
    return dict(libraryEncounters=sorted(after['libraryEncounters']),
                attempts=cell('attempts'), noVerdict=shown, lanes=lanes,
                objectiveVersion=bridge.status()['objectiveVersion'])


def strategy_investigation(js, bridge, wait, library):
    """The ranked-list -> investigation flow, driven through the real page on the tiny fixture.

    Clicking the focused difficulty row must load the host's ranked strategies; picking one must open
    its investigation panel with that build's own label, its stored run and the stored bank summary;
    and one backend chunk of eight fresh trials must persist eight seeds to the sidecar and report
    the trial bank's own sample count. The `Simulate # fights` count box must run an arbitrary total -
    here two, a number no preset offers - and the `Simulate 1 visually` action must run one brand-new
    fight and open its own fresh trace labelled a new simulation, adding exactly one trial. Nothing
    here runs a production batch: the fixture is a 40-tick scenario and only these small runs are
    exercised, and the fixture has no earned record, so no fabricated 125 result is involved.
    """
    # ---- the ranked list is the host's, not the page's own idea of the order ------------------
    wait(lambda: js(f"document.querySelector('{ROW_SELECTOR}')"
                    ".getAttribute('data-encounter-focused')") == 'true',
         'encounter 19 is focused for ranking')
    js(f"document.querySelector('{ROW_SELECTOR}').click()")
    wait(lambda: js("!!document.querySelector('[data-strategy-list]')"),
         "the host's ranked strategies loaded on screen")
    host = bridge.encounter_strategies(19, 0)
    assert host.get('ok'), f'encounter_strategies refused: {host.get("error")}'
    if not host['strategies']:
        raise AssertionError('the host ranks no strategy for the fixture fight')
    shown = js("Array.from(document.querySelectorAll('[data-strategy-row]'))"
               ".map((node) => node.getAttribute('data-strategy-row'))")
    want = [row['candidateId'] for row in host['strategies']]
    # The page may re-sort the host's rows by the reader's chosen sort mode, so the check is that the
    # same builds are on screen, not that their order matches the host's own ranking.
    if sorted(shown) != sorted(want):
        raise AssertionError(f'on-screen ranked rows {shown} != the host builds {want}')
    target = host['strategies'][0]
    cid, label = target['candidateId'], target['label']
    print(f'  ranked list: {len(want)} row(s) from the host, top build={label!r}', flush=True)

    # ---- selecting that row opens its investigation, owned by the host -----------------------
    js(f"document.querySelector('[data-strategy-row=\"{cid}\"] "
       "[data-action=select-encounter-strategy]').click()")
    wait(lambda: js("!!document.querySelector('[data-optimizer-investigation]')"),
         'the investigation panel opened')
    wait(lambda: js("(document.querySelector('[data-investigation-owner]') || {}).innerText || ''")
         and not js("!!document.querySelector('[data-investigation-busy]')"),
         'the investigation detail finished loading')
    owner = js("document.querySelector('[data-investigation-owner]').innerText")
    if label not in owner:
        raise AssertionError(f'the panel is not describing {label!r}: {owner!r}')
    detail = bridge.strategy_detail(candidate_id=cid)
    assert detail.get('ok'), f'strategy_detail refused: {detail.get("error")}'
    if detail.get('label') != label:
        raise AssertionError(f'the host detail labels {detail.get("label")!r}, ranked {label!r}')

    # ---- its stored run and the stored bank summary are the host's numbers -------------------
    if js("document.querySelector('[data-investigation-history]').open"):
        raise AssertionError('the full run ledger should be collapsed in the focused workspace')
    js("document.querySelector('[data-investigation-history]').open = true")
    stored_attempts = js("document.querySelector('[data-investigation-bank=stored]"
                         " [data-bank-attempts]').innerText.trim()")
    want_attempts = int(detail['storedSummary']['attempts'])
    if stored_attempts != str(want_attempts):
        raise AssertionError(f'the stored bank shows {stored_attempts!r} attempts, host {want_attempts}')
    want_rows = len(detail['storedRuns'])
    stored_rows = js("document.querySelectorAll('[data-investigation-runs]')[0]"
                     ".querySelectorAll('[data-investigation-run]').length")
    if want_rows < 1:
        raise AssertionError('the fixture stored no run for the investigation to show')
    if stored_rows != min(want_rows, 25):
        raise AssertionError(f'the stored run table initially shows {stored_rows} row(s), '
                             f'expected {min(want_rows, 25)} of {want_rows}')
    if want_rows > 25:
        js("document.querySelector('[data-investigation-runs-more] "
           "[data-action=investigation-show-all]').click()")
        wait(lambda: js("document.querySelectorAll('[data-investigation-runs]')[0]"
                        ".querySelectorAll('[data-investigation-run]').length") == want_rows,
             'every retained stored run is reachable')

    # ---- every own-unit slot is shown separately, with its own build -------------------------
    # The frozen scenario the host returned for this exact build is the source of truth: the panel
    # must render one card per slot in the scenario's recorded order, each with its own weapon,
    # equipment, parameters and aligned invocation level, and it must not collapse the allies into
    # one summary line. This reads the same scenario the host gave the page and compares the rendered
    # numbers to it; it never re-implements the page's mapping.
    own_units = (detail.get('scenario') or {}).get('ownUnits') or []
    if len(own_units) < 2:
        raise AssertionError('the fixture scenario carries fewer than two own units to separate')
    js("document.querySelectorAll('[data-team-build-advanced]').forEach(function (node) { node.open = true })")
    build = js("""
      Array.from(document.querySelectorAll('[data-team-build-slot]')).map(function (card) {
        return {
          slot: card.getAttribute('data-team-build-slot'),
          name: (card.querySelector('[data-team-build-name]') || {}).innerText || '',
          equipment: Array.from(card.querySelectorAll('[data-team-build-equipment-id]')).map(
            function (li) { return li.getAttribute('data-team-build-equipment-id'); }),
          parameters: Array.from(card.querySelectorAll('[data-team-build-parameter]')).map(
            function (row) { return {
              id: row.getAttribute('data-team-build-parameter'),
              raw: (row.querySelector('[data-team-build-parameter-raw]') || {}).innerText || '' }; }),
          skills: Array.from(card.querySelectorAll('[data-team-build-skill-slot]')).map(
            function (li) { return {
              id: li.getAttribute('data-team-build-skill-id'),
              invocation: li.getAttribute('data-team-build-invocation'),
              text: li.innerText }; })
        };
      })
    """)
    ordered = [str(index + 1) for index in range(len(own_units))]
    if [entry['slot'] for entry in build] != ordered:
        raise AssertionError(f'the team-build slots are not in scenario order: '
                             f'{[entry["slot"] for entry in build]}')

    def raw_text(value):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)

    for index, unit in enumerate(own_units):
        shown = build[index]
        if (unit.get('name') or '') and unit['name'] not in shown['name']:
            raise AssertionError(f'slot {index + 1} is named {shown["name"]!r}, host '
                                 f'{unit["name"]!r}')
        expected_equipment = [str(piece.get('id')) for piece in (unit.get('equipment') or [])]
        if shown['equipment'] != expected_equipment:
            raise AssertionError(f'slot {index + 1} equipment {shown["equipment"]} != host '
                                 f'{expected_equipment}')
        expected_parameters = sorted((unit.get('parameters') or {}), key=int)
        if [entry['id'] for entry in shown['parameters']] != expected_parameters:
            raise AssertionError(f'slot {index + 1} parameter ids '
                                 f'{[entry["id"] for entry in shown["parameters"]]} != host '
                                 f'{expected_parameters}')
        by_id = {entry['id']: entry['raw'] for entry in shown['parameters']}
        for pid in expected_parameters:
            recorded = (unit.get('parameters') or {}).get(pid) or {}
            if recorded.get('rawValue') is None:
                continue
            want = raw_text(recorded['rawValue'])
            if want not in by_id[pid]:
                raise AssertionError(f'slot {index + 1} parameter {pid} raw shows {by_id[pid]!r}, '
                                     f'host raw {want!r}')
        recorded_skills = [str(int(skill)) for skill in (unit.get('skills') or [])]
        if [entry['id'] for entry in shown['skills']] != recorded_skills:
            raise AssertionError(f'slot {index + 1} skills {[entry["id"] for entry in shown["skills"]]} '
                                 f'!= host {recorded_skills}')
        aligned_levels = [str(int(level)) for level in (unit.get('invocationLevels') or [])]
        if [entry['invocation'] for entry in shown['skills']] != aligned_levels:
            raise AssertionError(f'slot {index + 1} invocation levels '
                                 f'{[entry["invocation"] for entry in shown["skills"]]} != recorded '
                                 f'{aligned_levels}')

    # The top two slots must differ on every axis, so they cannot be one comma-joined summary.
    if build[0]['equipment'] == build[1]['equipment']:
        raise AssertionError('slots 1 and 2 render identical equipment, so the build was collapsed')
    if [entry['id'] for entry in build[0]['skills']] == [entry['id'] for entry in build[1]['skills']]:
        raise AssertionError('slots 1 and 2 render identical skills, so the build was collapsed')
    if build[0]['parameters'] == build[1]['parameters']:
        raise AssertionError('slots 1 and 2 render identical parameters, so the build was collapsed')
    # The numeric setting is preserved and a canonical trigger meaning is shown next to it.
    skill_lines = ' '.join(entry['text'] for entry in build[0]['skills'])
    if not re.search(r'\b(High|Normal|Low|Always active|activation not recovered|not recorded)\b',
                     skill_lines):
        raise AssertionError(f'the skill lines show no trigger meaning: {skill_lines[:200]!r}')
    print(f'  team build: {len(build)} slot(s) in order, '
          f'slot 1 equipment={build[0]["equipment"]}, '
          f'slot 1 skills={[entry["id"] for entry in build[0]["skills"]]}', flush=True)

    # ---- one backend chunk of eight fresh trials persists through the sidecar ----------------
    sidecar = library.with_suffix('.investigation.json')
    if sidecar.exists():
        raise AssertionError('the fixture sidecar already existed before any trial was run')
    button = '[data-action=run-fresh-trials-8]'
    if not js(f"!!document.querySelector('{button}')"):
        raise AssertionError('the eight-trial control is missing from the investigation panel')
    if js(f"document.querySelector('{button}').disabled"):
        raise AssertionError('the eight-trial control was disabled for a live build')
    # ---- the count box offers a number, and the one-fight visual action sits beside it ---------
    count_box = '[data-trial-count-input]'
    if not js(f"!!document.querySelector('{count_box}')"):
        raise AssertionError('the Simulate # fights count box is missing from the panel')
    if js(f"document.querySelector('{count_box}').disabled"):
        raise AssertionError('the count box was disabled for a live build')
    default_count = (js(f"document.querySelector('{count_box}').value") or '').strip()
    if not default_count.isdigit() or int(default_count) < 1:
        raise AssertionError(f'the count box shows no numeric default: {default_count!r}')
    count_submit = '[data-action=run-fresh-trials-count]'
    if not js(f"!!document.querySelector('{count_submit}')"):
        raise AssertionError('the count box has no submit action')
    if js(f"document.querySelector('{count_submit}').disabled"):
        raise AssertionError('the count submit was disabled for a live build')
    if not js("!!document.querySelector('[data-trial-count-hint]')"):
        raise AssertionError('the count box does not state its range')
    visual_button = '[data-action=simulate-visually]'
    if not js(f"!!document.querySelector('{visual_button}')"):
        raise AssertionError('the Simulate 1 visually action is missing from the panel')
    if js(f"document.querySelector('{visual_button}').disabled"):
        raise AssertionError('the visual simulation action was disabled for a live build')
    if not js("!!document.querySelector('[data-investigation-actions]')"):
        raise AssertionError('the simulation controls are not grouped on the panel')
    print(f'  simulate controls: count box default={default_count!r}, submit and visual action '
          f'present and enabled', flush=True)
    js(f"document.querySelector('{button}').click()")
    wait(lambda: js("!!document.querySelector('[data-trial-error]')")
         or js("!!document.querySelector('[data-latest-test=\"8\"]')"),
         'the fresh-trial chunk finished')
    if js("!!document.querySelector('[data-trial-error]')"):
        raise AssertionError('the fresh trials failed: '
                             + js("document.querySelector('[data-trial-error]').innerText"))
    message = js("document.querySelector('[data-latest-test]').innerText")
    if 'Latest test: 8 fights' not in message:
        raise AssertionError(f'the latest result card does not report eight fights: {message!r}')
    after = bridge.strategy_detail(candidate_id=cid)
    assert after.get('ok'), after.get('error')
    want_trials = int(after['trialSummary']['attempts'])
    if want_trials != 8:
        raise AssertionError(f'the host recorded {want_trials} trials, expected one eight-chunk')
    trial_attempts = js("document.querySelector('[data-investigation-bank=trial]"
                        " [data-bank-attempts]').innerText.trim()")
    if trial_attempts != str(want_trials):
        raise AssertionError(f'the trial bank shows {trial_attempts!r} attempts, host {want_trials}')
    trial_retained = js("document.querySelector('[data-investigation-bank=trial]"
                        " [data-bank-retained]').innerText.trim().split('(')[0].trim()")
    if trial_retained != str(int(after['trialSummary']['samples'])):
        raise AssertionError(f'the trial bank retained {trial_retained!r}, host '
                             f'{after["trialSummary"]["samples"]}')
    mean_cell = js("document.querySelector('[data-investigation-bank=trial]"
                   " td:nth-child(8)').innerText")
    match = re.search(r'n=([\d,]+)', mean_cell or '')
    if not match or int(match.group(1).replace(',', '')) != int(after['trialSummary']['earnedSamples']):
        raise AssertionError(f"the trial mean's sample count is not the host's: {mean_cell!r}")
    trial_rows = js("document.querySelectorAll('[data-investigation-runs]')[1]"
                    ".querySelectorAll('[data-investigation-run]').length")
    if trial_rows != want_trials:
        raise AssertionError(f'the fresh-trial table shows {trial_rows} row(s), host {want_trials}')

    # ---- the count box runs an arbitrary number, not just the presets -------------------------
    # A number the presets never offer proves the input drives the total; the host still chunks it.
    want_count = 2
    js("(() => { const box = document.querySelector('%s');"
       "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"
       " 'value').set; setter.call(box, '%d');"
       "box.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
       % (count_box, want_count))
    js(f"document.querySelector('{count_submit}').click()")
    wait(lambda: js("!!document.querySelector('[data-trial-error]')")
         or js(f"!!document.querySelector('[data-latest-test=\"{want_count}\"]')"),
         'the counted fresh-trial run finished')
    if js("!!document.querySelector('[data-trial-error]')"):
        raise AssertionError('the counted fresh trials failed: '
                             + js("document.querySelector('[data-trial-error]').innerText"))
    counted_message = js("document.querySelector('[data-latest-test]').innerText")
    if f'Latest test: {want_count} fights' not in counted_message:
        raise AssertionError(f'the counted run does not report {want_count}: {counted_message!r}')
    after_count = bridge.strategy_detail(candidate_id=cid)
    assert after_count.get('ok'), after_count.get('error')
    want_after_count = want_trials + want_count
    if int(after_count['trialSummary']['attempts']) != want_after_count:
        raise AssertionError(f'the counted run left {after_count["trialSummary"]["attempts"]} '
                             f'trial(s), expected {want_after_count}')
    counted_rows = js("document.querySelectorAll('[data-investigation-runs]')[1]"
                      ".querySelectorAll('[data-investigation-run]').length")
    if counted_rows != want_after_count:
        raise AssertionError(f'the cumulative trial table shows {counted_rows} row(s), '
                             f'host {want_after_count}')
    print(f'  count box: ran {want_count} fresh fight(s) from the input, '
          f'{counted_rows} cumulative trial row(s) on screen', flush=True)

    # ---- one fresh visual fight: a new simulation, rendered from its own trace -----------------
    # This is the same exact build on a brand-new seed pair, not a replay of the stored record, so
    # the renderer must open on a fresh trace and the panel must say so.
    js(f"document.querySelector('{visual_button}').click()")
    wait(lambda: js("!!document.querySelector('[data-optimizer-replay]')"),
         'the visual simulation opened the battle renderer', 120)
    wait(lambda: js("!!document.querySelector('[data-generated-replay=loaded]')"),
         'the fresh trace rendered in the shared renderer', 120)
    if not js("document.querySelectorAll('[data-generated-unit]').length"):
        raise AssertionError('the fresh visual trace drew no units')
    visual_title = js("document.querySelector('[data-optimizer-replay-title]').innerText")
    if 'new simulation' not in visual_title:
        raise AssertionError(f'the visual is not labelled a new simulation: {visual_title!r}')
    visual_note = js("(document.querySelector('[data-visual-trace-note]') || {}).innerText || ''")
    if 'not a replay' not in visual_note:
        raise AssertionError(f'the visual trace is not labelled a fresh simulation: {visual_note!r}')
    js("document.querySelector('[data-action=back-to-optimizer]').click()")
    wait(lambda: js("!!document.querySelector('[data-investigation-actions]')"),
         'the investigation panel came back after the visual')
    js("document.querySelector('[data-investigation-history]').open = true")
    after_visual = bridge.strategy_detail(candidate_id=cid)
    assert after_visual.get('ok'), after_visual.get('error')
    want_after_visual = want_after_count + 1
    if int(after_visual['trialSummary']['attempts']) != want_after_visual:
        raise AssertionError(f'the visual fight left {after_visual["trialSummary"]["attempts"]} '
                             f'trial(s), expected {want_after_visual}')
    bank_after_visual = js("document.querySelector('[data-investigation-bank=trial]"
                           " [data-bank-attempts]').innerText.trim()")
    if bank_after_visual != str(want_after_visual):
        raise AssertionError(f'the trial bank shows {bank_after_visual!r} attempts after the '
                             f'visual, host {want_after_visual}')
    panel_note = js("(document.querySelector('[data-visual-note]') || {}).innerText || ''")
    if 'not a replay' not in panel_note:
        raise AssertionError(f'the panel does not label the visual a fresh simulation: {panel_note!r}')
    # The visual's own fresh seed pair is the one the panel reports and the one on the sidecar.
    before_pairs = {tuple(run['seeds']) for run in after_count['trialRuns'] if run.get('seeds')}
    fresh_pairs = [tuple(run['seeds']) for run in after_visual['trialRuns']
                   if run.get('seeds') and tuple(run['seeds']) not in before_pairs]
    if len(fresh_pairs) != 1:
        raise AssertionError(f'the visual recorded {len(fresh_pairs)} new seed pair(s), expected one')
    fresh_seed_text = f'[{fresh_pairs[0][0]}, {fresh_pairs[0][1]}]'
    if fresh_seed_text not in panel_note:
        raise AssertionError(f'the panel note omits the new seed pair {fresh_seed_text}: {panel_note!r}')
    print(f'  visual simulation: labelled a new simulation, rendered from its own trace, '
          f'new seeds {fresh_seed_text}', flush=True)

    # ---- the trials really reached the sidecar, with their seeds and digest --------------------
    if not sidecar.is_file():
        raise AssertionError('the fresh trials were not persisted to the investigation sidecar')
    payload = json.loads(sidecar.read_text(encoding='utf-8'))
    trials = [trial for ledger in (payload.get('scenarios') or {}).values()
              for trial in (ledger.get('trials') or [])]
    if len(trials) != want_after_visual:
        raise AssertionError(f'the sidecar holds {len(trials)} trial(s), expected {want_after_visual}')
    for trial in trials:
        if not trial.get('seeds') or not (trial.get('result') or {}).get('digest'):
            raise AssertionError(f'a persisted trial lost its seeds or digest: {sorted(trial)}')
    print(f'  investigation: stored attempts={want_attempts}, {want_after_visual} fresh trial(s) '
          f'persisted to {sidecar.name}, trial mean samples={match.group(1)}', flush=True)
    js("document.querySelector('[data-action=close-investigation]').click()")
    wait(lambda: not js("!!document.querySelector('[data-optimizer-investigation]')"),
         'the investigation panel closed again')
    return dict(rankedRows=len(want), label=label, storedRuns=stored_rows,
                storedAttempts=want_attempts, trials=len(trials), sidecar=sidecar.name,
                countedTrials=want_after_count, visualSeeds=list(fresh_pairs[0]))


def control_bar(js, bridge, wait, window):
    """The pinned toolbar: one primary location per control, genuinely sticky, usable when narrow.

    Driven against the real page. The Workers selector and the New library action must exist exactly
    once on the page (the duplicates in the lower card are gone), the bar must hold at the viewport
    top while the long results area scrolls under it, the Workers value chosen *in the bar* must be
    what the next Start asks the host for, and New library while Running must be refused with a
    visible reason - the page keeps the control enabled and surfaces the host's `_paused` refusal.
    """
    js(WORKERS_JS)
    state = bridge.status()['state']
    info = js("""
    (() => {
      const bar = document.querySelector('[data-optimizer-run-bar]');
      if (!bar) return null;
      const node = (sel) => bar.querySelector(sel);
      const shown = (sel) => { const n = node(sel); if (!n) return false;
        const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const name = node('[data-optimizer-library-name]');
      return {
        text: bar.innerText,
        libraryName: name ? name.innerText.trim() : null,
        workers: !!node('[data-optimizer-workers]'),
        start: !!node('[data-action=optimizer-start]'),
        pause: !!node('[data-action=optimizer-pause]'),
        stop: !!node('[data-action=optimizer-stop]'),
        newLibrary: !!node('[data-action=optimizer-new-library]'),
        newLibraryVisible: shown('[data-action=optimizer-new-library]')
      };
    })()
    """)
    assert info, 'the sticky control bar did not render'
    for key in ('workers', 'start', 'pause', 'stop', 'newLibrary'):
        assert info[key], f'the control bar is missing {key}'
    assert info['newLibraryVisible'], 'New library is not visible in the bar'
    assert state in info['text'], f'the bar does not show the state {state!r}: {info["text"]!r}'
    expected_name = Path(bridge.status()['library']).name
    assert info['libraryName'] == expected_name, (
        f'the bar shows library {info["libraryName"]!r}, the bridge opened {expected_name!r}')
    assert js("document.querySelectorAll('[data-optimizer-workers]').length") == 1, \
        'there is more than one Workers selector on the page'
    assert js("document.querySelectorAll('[data-action=\"optimizer-new-library\"]').length") == 1, \
        'there is more than one New library action on the page'
    print(f'  control bar: state={state} library={expected_name} '
          f'workers/start/pause/stop/new-library present, hooks unique', flush=True)

    # ---- genuinely sticky: open the long technical section, scroll a long way down, stay on top ---
    js("document.querySelector('[data-optimizer-technical]').open = true")
    js("window.scrollTo(0, document.documentElement.scrollHeight)")
    wait(lambda: js('window.scrollY') > 200, 'the results area scrolled')
    sticky = js("""
    (() => {
      const bar = document.querySelector('[data-optimizer-run-bar]');
      const r = bar.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + 8, r.top + 8);
      return { top: r.top, bottom: r.bottom, height: r.height, scrollY: window.scrollY,
               uncovered: !!(hit && hit.closest('[data-optimizer-run-bar]')) };
    })()
    """)
    assert sticky['scrollY'] > 200, f'the page did not scroll far enough to test sticky: {sticky}'
    assert sticky['height'] > 0 and sticky['bottom'] > 0, f'the bar left the viewport: {sticky}'
    assert abs(sticky['top']) <= 2, f'the bar is not pinned to the viewport top: {sticky}'
    assert sticky['uncovered'], f'another element covers the sticky bar: {sticky}'
    print(f'  sticky bar held at top={sticky["top"]:.0f}px after scrolling '
          f'{int(sticky["scrollY"])}px down', flush=True)
    js("window.scrollTo(0, 0)")
    time.sleep(0.2)

    # ---- the Workers value chosen in the bar is what the next Start asks the host for ----------
    js('window.__kaOpenWorkers()')
    offered = wait(lambda: js('window.__kaWorkerOptions()'), 'the bar worker selector opened')
    chosen = offered[0]
    assert js(f'window.__kaPickWorker({json.dumps(chosen)})'), f'could not pick {chosen!r}'
    wait(lambda: not js('window.__kaWorkerOptions()'), 'the worker choice closed the list')
    wait(lambda: js("!document.querySelector('[data-action=optimizer-start]').disabled"),
         'Start enabled again')
    js("document.querySelector('[data-action=optimizer-start]').click()")
    wait(lambda: bridge.status()['state'] == 'Running', 'started from the bar')
    wait(lambda: bridge.status()['throughput']['workers'] == int(chosen),
         f'the bar choice {chosen} was not used')
    print(f'  bar Workers: chose {chosen}, host started '
          f'{bridge.status()["throughput"]["workers"]}', flush=True)

    # ---- New library while Running is refused, with the host's own reason on screen -----------
    was_disabled = js("(document.querySelector('[data-action=optimizer-new-library]') || {}).disabled")
    previous = js("(document.querySelector('[data-optimizer-action-error]') || {}).innerText || ''")

    def refusal():
        text = js("(document.querySelector('[data-optimizer-action-error]') || {}).innerText || ''")
        return text if text and text != previous else None

    js("document.querySelector('[data-action=optimizer-new-library]').click()")
    reason = wait(refusal, 'New library refused while Running')
    lowered = reason.lower()
    assert 'paus' in lowered or 'wait' in lowered or 'outstanding' in lowered, reason
    print(f'  New library blocked while Running (button disabled={was_disabled!r}): '
          f'{reason.strip()!r}', flush=True)
    js("document.querySelector('[data-action=optimizer-stop]').click()")
    wait(lambda: bridge.status()['state'] not in ('Running', 'Saving'),
         'stopped after the bar checks')

    # ---- a narrow window: the bar keeps its controls and the body does not overflow ------------
    window.resize(560, 720)
    time.sleep(0.8)
    narrow = js("""
    (() => {
      const root = document.documentElement;
      return { width: root.clientWidth, overflow: root.scrollWidth - root.clientWidth,
               bar: !!document.querySelector('[data-optimizer-run-bar]'),
               workers: !!document.querySelector('[data-optimizer-workers]'),
               start: !!document.querySelector('[data-action=optimizer-start]'),
               newLibrary: !!document.querySelector('[data-action=optimizer-new-library]') };
    })()
    """)
    assert narrow['bar'] and narrow['workers'] and narrow['start'] and narrow['newLibrary'], \
        f'the bar lost a control at {narrow["width"]}px: {narrow}'
    assert narrow['overflow'] <= 1, (
        f'the body overflows horizontally by {narrow["overflow"]}px at width {narrow["width"]}')
    print(f'  narrow window {narrow["width"]}px: bar intact, no horizontal overflow', flush=True)
    window.resize(1100, 780)
    time.sleep(0.4)
    return info['libraryName']


def main():
    import webview
    library_naming_checks()
    with tempfile.TemporaryDirectory(prefix='ka-desktop-check-') as root:
        path = Path(root)/'test.sqlite'
        scenario = default_scenario()
        scenario['tickLimit'] = 40
        store = Store(path, provenance())
        with store.db:
            store.set('scope', scope(scenario))
            candidate = store.add(scenario, 'Desktop integration run', 'supplied', stats(scenario))
            # One real run carrying the stored-attack metrics, so the setup lane has evidence to
            # render in this tiny fixture. It is a genuine simulation - the page refuses to replay a
            # run whose metrics do not reproduce - and it stays censored because every run of this
            # 40-tick fixture reaches no verdict. Only the additive progress block is attached.
            from strategy_optimizer_adapter import simulate
            seeded = simulate(scenario, (101, 202), backend='python')
            seeded['progressMetrics'] = dict(
                bossIdentity=1, bossDeathTick=30, bossLeavingTick=31, storedCommandsAtDeath=4,
                storedCommandsTargetingBossAtDeath=3, storedTargetHoldersAtDeath=0,
                commandsTargetingBoss=3, commandsTargetingBossReleased=3,
                maxSimultaneousStoredCommands=6, maxSimultaneousCommandsTargetingBoss=4,
                storedTargetHoldersPeak=1, postDeathBossReentries=2, postDeathBossLeavings=3,
                postDeathPrizes=3, commandsReleasedAfterDeath=3,
                commandsReleasedAfterDeathTargetingBoss=3, firstPostDeathCommandReleaseTick=44,
                lastPostDeathCommandReleaseTick=60)
            store.record(candidate, 'validation', 0, seeded)
            # Present the fixture as a library written before the lane objective (generation 1), so the
            # page's explicit upgrade path is exercised through the real bridge. A library created now
            # is created at generation 2, which is why this is a deliberate edit rather than the default.
            store.db.execute("DELETE FROM meta WHERE key='objectiveVersion'")
        store.close()
        bridge = Bridge(path)
        server = ThreadingHTTPServer(('127.0.0.1', 0), asset_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = webview.create_window('Strategy Optimiser integration check',
            f'http://127.0.0.1:{server.server_port}/desktop.html', js_api=bridge,
            width=1100, height=780)
        bridge._window = window
        output = {}

        def js(expression):
            return window.evaluate_js(expression)

        def wait(check, label, seconds=60):
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                try:
                    value = check()
                    if value:
                        print(label, flush=True)
                        return value
                except Exception:
                    pass
                time.sleep(.2)
            raise AssertionError(f'Timed out: {label}; state={bridge.status().get("state")}')

        def exercise():
            print('Exercise started', flush=True)
            try:
                wait(lambda: js("!!document.querySelector('[data-action=optimizer-start]')"), 'React mounted')
                wait(lambda: js("!!window.pywebview?.api?.status"), 'native bridge mounted')
                wait(lambda: js("!document.querySelector('[data-action=optimizer-start]').disabled"), 'Start enabled')
                js("document.querySelector('[data-action=optimizer-start]').click()")
                wait(lambda: (bridge.status().get('totalRuns') or 0) >= 1, 'real headless run')
                wait(lambda: js("!document.querySelector('[data-action=optimizer-pause]').disabled"), 'Pause enabled')
                js("document.querySelector('[data-action=optimizer-pause]').click()")
                wait(lambda: bridge.status()['state'] == 'Paused', 'pause drained batch')
                saved = bridge.status()['totalRuns']
                wait(lambda: js("!document.querySelector('[data-action=optimizer-replay]').disabled"), 'Replay enabled')
                js("document.querySelector('[data-action=optimizer-replay]').click()")
                try:
                    wait(lambda: js("!!document.querySelector('[data-optimizer-replay]')"),
                         'same renderer embedded', 30)
                except AssertionError:
                    # Surface the page's own reason instead of a bare timeout: the replay control
                    # reports a refused payload or a missing seed pair in its own element.
                    detail = js("(document.querySelector('[data-optimizer-replay-error]') || {}).innerText"
                                " || (document.querySelector('[data-optimizer-replay-busy]') || {}).innerText"
                                " || 'no error text'")
                    raise AssertionError(f'the replay did not open: {detail}')
                text = js('document.body.innerText')
                assert 'No generated battle' not in text, text[:1000]
                wait(lambda: js("!!document.querySelector('[data-generated-replay=loaded]')"), 'Existing battle renderer loaded')
                assert js("document.querySelectorAll('[data-generated-unit]').length") > 0, 'Actual replay unit rows missing'
                assert bridge.status()['totalRuns'] == saved, 'Replay must not be counted as search samples'
                js("document.querySelector('[data-action=back-to-optimizer]').click()")
                wait(lambda: js("!!document.querySelector('[data-action=optimizer-start]')"), 'Back preserved library')
                controls = runtime_controls(js, bridge, wait)
                overview = encounter_overview(js, bridge, wait)
                investigation = strategy_investigation(js, bridge, wait, path)
                bar = control_bar(js, bridge, wait, window)
                output.update(ok=True, simulations=saved, embeddedRenderer=True,
                              traceCache=path.with_suffix('.replay.json').is_file(),
                              controls=controls, overview=overview, investigation=investigation,
                              controlBar=bar)
            except Exception as exc:
                output.update(ok=False, error=str(exc))
                try:
                    output['uiText'] = js('document.body.innerText')[:2500]
                except Exception:
                    pass
            finally:
                bridge.close()
                bridge._optimizer.thread.join(190)
                window.destroy()

        try:
            webview.start(exercise, gui='edgechromium', private_mode=True)
        finally:
            bridge._guard.close()
            server.shutdown()
            server.server_close()
        print(json.dumps(output))
        if not output.get('ok'):
            raise SystemExit(1)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
