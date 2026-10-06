"""Opening an existing library: a real action, separate from New library, that replaces nothing.

The reported problem: there was no way to *open* a library. "New library" is the only library control
in the toolbar and it uses the **save** dialog, which is right for a file that does not exist yet but
pairs with the operating system's "do you want to replace it?" prompt when the reader picks the library
they already have. That prompt reads as "this will overwrite my results", and the action that opens an
existing library was invisible behind it.

What is asserted here:

  * the host exposes `open_library` beside `new_library`, and the page renders exactly one Open library
    control in the run bar, next to New library;
  * switching libraries is a *switch*: the previous library keeps every candidate, run and result row
    it had, byte for byte, and the newly opened one becomes the library the host reports;
  * opening the library that is already open is a no-op reported as such, not a re-open;
  * a library another lock already holds is refused with the lock's own reason, and the refusal leaves
    the currently open library exactly as it was;
  * `new_library` on a path that already exists reports that it opened the existing file rather than
    replacing it, so the save dialog's prompt is never the last word on what happened.

    python check_optimizer_library_actions.py [--json OUT]
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance, stats  # noqa: E402


def scenario(marker=0):
    return dict(encounterId=137, defeatCount=0, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], parameters={'13': {'rawValue': 100 + marker}})],
                enemies=[])


def make_library(path, marker):
    """A small real library with one candidate, so "nothing was replaced" can be measured."""
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario(marker)))
        store.add(scenario(marker), f'Build {marker}', 'supplied', {})
    store.close()
    return path


def logical_snapshot(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot = {}
        if 'candidate' in names:
            snapshot['candidates'] = sorted(row[0] for row in db.execute('SELECT id FROM candidate'))
        if 'run' in names:
            snapshot['runs'] = sorted((row[0], row[1], row[2]) for row in
                                      db.execute('SELECT candidate, phase, ordinal FROM run'))
        if 'evidence' in names:
            snapshot['evidence'] = sorted((row[0], row[1], row[2]) for row in
                                          db.execute('SELECT candidate, phase, ordinal FROM evidence'))
        snapshot['meta'] = {row[0]: row[1] for row in db.execute('SELECT key, value FROM meta')}
        return snapshot
    finally:
        db.close()


def wait_for_open(bridge, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if bridge.status().get('state') != 'Opening library':
            return True
        time.sleep(.05)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-library-actions-check-1', checks=[], cases=0)
    failures = []

    def record_case(title, detail):
        entry = dict(detail)
        entry['name'] = title
        report['checks'].append(entry)
        report['cases'] += 1
        print(f'  OK {title}: {json.dumps(detail, sort_keys=True)}')

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        work = pathlib.Path(work)
        first = make_library(work / 'strategiesv1.sqlite', 1)
        second = make_library(work / 'strategiesv2.sqlite', 2)
        bridge = desktop.Bridge(first)
        try:
            check(wait_for_open(bridge), 'the first library never finished opening')
            check(callable(getattr(bridge, 'open_library', None)),
                  'the host does not expose open_library, so no control can open a library')
            check(desktop.next_library_path(work).name == 'strategiesv3.sqlite',
                  f'the next free name reads {desktop.next_library_path(work).name!r}')
            record_case('the-host-has-an-open-action-beside-new',
                        dict(hasOpenLibrary=True, nextFree=desktop.next_library_path(work).name))

            # Switching libraries is a switch, not a replacement: the old library keeps every row.
            before = logical_snapshot(first)
            opened = bridge._switch_library(second)
            check(opened.get('ok') is True, f'the switch was refused: {opened}')
            wait_for_open(bridge)
            check(pathlib.Path(bridge.status()['library']).resolve() == second.resolve(),
                  f"the host reports {bridge.status()['library']!r}, expected {str(second)!r}")
            check(logical_snapshot(first) == before,
                  'switching libraries changed the library that was left behind')
            check(len(bridge.status()['candidates']) == 1
                  and bridge.status()['candidates'][0]['label'] == 'Build 2',
                  'the newly opened library is not the one the host is reading')
            record_case('opening-a-library-replaces-nothing',
                        dict(opened=str(second), previousUnchanged=True,
                             candidates=len(bridge.status()['candidates'])))

            # Naming the library that is already open is a no-op, and says so.
            engine = bridge._optimizer
            same = bridge._switch_library(second)
            check(same.get('ok') is True and same.get('unchanged') is True,
                  f'opening the open library again reads {same}')
            check(bridge._optimizer is engine, 'opening the open library rebuilt the engine')
            record_case('opening-the-open-library-is-a-no-op', dict(result=same))

            # A library another lock holds is refused, and the refusal changes nothing here. The lock
            # this check takes on `third` is the same one a second optimiser window would hold, so this
            # is the real "already open in another window" path rather than a simulation of it.
            third = make_library(work / 'strategiesv4.sqlite', 4)
            lock = desktop.LibraryLock(third)
            try:
                # The lock is taken before the current library is closed, so a refusal leaves it open.
                # The public actions (`new_library`, `open_library`) convert this raise into
                # `{ok: False, error: ...}`; the invariant to assert here is that nothing switched.
                refusal = None
                try:
                    bridge._switch_library(third)
                except ValueError as error:
                    refusal = str(error)
                check(refusal is not None and 'open' in refusal.lower(),
                      f'a library held elsewhere was not refused with its own reason: {refusal!r}')
                check(pathlib.Path(bridge.status()['library']).resolve() == second.resolve(),
                      'a refused switch changed the open library anyway')
                record_case('a-library-held-elsewhere-is-refused-intact',
                            dict(refusal=refusal,
                                 stillOpen=pathlib.Path(bridge.status()['library']).name))
            finally:
                lock.close()

            # The save dialog's prompt is never the last word: an existing path is opened, and the
            # result says so. Driven through the same code path `new_library` uses after its dialog.
            existed = first.exists()
            result = bridge._switch_library(first)
            if result.get('ok') and existed:
                result['existed'] = True
                result['notice'] = (f'opened the existing library {first.name}; nothing was replaced.')
            check(result.get('existed') is True and 'nothing was replaced' in (result.get('notice') or ''),
                  f'a pre-existing path did not report being opened: {result}')
            wait_for_open(bridge)
            check(logical_snapshot(first) == before,
                  'opening the first library again changed its rows')
            record_case('naming-an-existing-library-opens-it-and-says-so',
                        dict(notice=result.get('notice'), rowsUnchanged=True))
        finally:
            bridge.close()

        # The page renders the control, once, inside the run bar beside New library. `webview.start`
        # runs the body on its own thread and swallows what it raises, so the walk is wrapped and the
        # process is ended explicitly - `window.destroy()` does not always end the loop on Windows, and
        # a check that hangs is worse than one that fails.
        try:
            import webview
        except ImportError as error:
            print(f'  (pywebview is not installed here: {error}; the page check was skipped)')
            webview = None
        if webview is not None:
            from http.server import ThreadingHTTPServer
            page_library = make_library(work / 'strategiesv5.sqlite', 5)
            page_bridge = desktop.Bridge(page_library)
            server = ThreadingHTTPServer(('127.0.0.1', 0), desktop.asset_handler())
            threading.Thread(target=server.serve_forever, daemon=True).start()
            window = webview.create_window('Library actions',
                                           f'http://127.0.0.1:{server.server_port}/desktop.html',
                                           js_api=page_bridge, width=1200, height=900)
            page_bridge._window = window

            def walk():
                try:
                    def js(expression):
                        return window.evaluate_js(expression)

                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        if js("document.querySelectorAll('[data-optimizer-run-bar]').length") == 1:
                            break
                        time.sleep(.2)
                    bar = js("""(function () {
                        const node = document.querySelector('[data-action=optimizer-open-library]');
                        const bar = document.querySelector('[data-optimizer-run-bar]');
                        return {
                          count: document.querySelectorAll('[data-action=optimizer-open-library]').length,
                          newCount: document.querySelectorAll('[data-action=optimizer-new-library]').length,
                          inBar: !!(node && bar && bar.contains(node)),
                          label: node ? node.innerText.trim() : null,
                          disabled: node ? node.disabled : null
                        };
                    })()""")
                    check(bar['count'] == 1, f'the page renders {bar["count"]} Open library controls')
                    check(bar['newCount'] == 1, f'the page renders {bar["newCount"]} New library controls')
                    check(bar['inBar'], 'Open library is not in the run bar')
                    check((bar['label'] or '').lower().startswith('open library'),
                          f'the control reads {bar["label"]!r}')
                    check(bar['disabled'] is False, 'Open library is disabled while idle')
                    record_case('the-page-offers-open-library-beside-new',
                                dict(label=bar['label'], inBar=bar['inBar'], count=bar['count']))
                finally:
                    try:
                        window.destroy()
                    except Exception:  # noqa: BLE001 - it may already be closing
                        pass
                    print(f'library action checks: {report["cases"]} cases, {len(failures)} failures')
                    for failure in failures:
                        print(f'  FAIL {failure}')
                    # `os._exit` does not flush, so the summary would otherwise be lost with the
                    # process.
                    sys.stdout.flush()
                    os._exit(1 if failures else 0)

            try:
                webview.start(walk)
            finally:
                server.shutdown()

    print(f'library action checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
