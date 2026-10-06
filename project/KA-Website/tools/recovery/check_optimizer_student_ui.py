"""The students' share controls, driven through the real desktop host.

Student 9 - the mean-earned student (`strategy_students.STUDENT_AVERAGE`) - is a real, dispatched
stream whose default share is 0.0 *on purpose*: switching it on is the user's decision. That only
works if the surface that makes the decision offers it. This check drives the actual page through the
actual bridge and asserts the control exists, carries the right key, and that using it reaches the
host - because "the student is implemented" and "the student can be switched on" are different claims,
and only the second one is testable this way.

It is deliberately narrow: it does not run battles, so it is seconds rather than minutes.

    python check_optimizer_student_ui.py

Needs pywebview (`pip install pywebview`), like `check_strategy_optimizer_desktop.py`. A missing
WebView2 or a missing module is reported as such and exits non-zero, so "no browser here" is never
mistaken for "the control is there".
"""
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_students as students  # noqa: E402
from strategy_optimizer import Store, scope  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402
from strategy_optimizer_desktop import Bridge, asset_handler  # noqa: E402

#: Every share stream the scheduler honours, and the ones the surface must therefore offer. The open
#: discovery stream is listed last on screen, which is the order the page uses.
#: Share streams the surface must offer, in the order it shows them. `mechanism` is the
#: breakthrough / reliability stream: a share like any other, 0% by default.
EXPECTED_KEYS = ('community', 'rebel', 'stumble', 'average', 'mechanism', 'discovery')
FAILURES = []


def expect(condition, message):
    print(('  OK   ' if condition else '  FAIL ') + message, flush=True)
    if not condition:
        FAILURES.append(message)


def report():
    """The summary, printed exactly once whichever way the page walk ended."""
    global _reported
    if _reported:
        return
    _reported = True
    print(f'failures: {len(FAILURES)}', flush=True)
    for detail in FAILURES:
        print(f'  {detail}', flush=True)


_reported = False


def wait_for(check, label, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except Exception:  # noqa: BLE001 - the page is still mounting
            pass
        time.sleep(.2)
    raise AssertionError(f'timed out waiting for {label}')


def main():
    try:
        import webview
    except ImportError as error:
        print(f'pywebview is not installed here ({error}); the UI check cannot run in this interpreter.')
        return 2
    # The bridge holds the library open, so the directory cannot always be removed on the way out
    # (Windows keeps the handle until the host is fully gone); a leftover temp dir is not a failure.
    with tempfile.TemporaryDirectory(prefix='ka-student-ui-',
                                     ignore_cleanup_errors=True) as root:
        path = Path(root)/'students.sqlite'
        scenario = default_scenario()
        scenario['tickLimit'] = 40
        store = Store(path, provenance())
        with store.db:
            store.set('scope', scope(scenario))
            store.add(scenario, 'Student share fixture', 'supplied', stats(scenario))
        store.close()
        bridge = Bridge(path)
        server = ThreadingHTTPServer(('127.0.0.1', 0), asset_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = webview.create_window('Student share controls',
                                       f'http://127.0.0.1:{server.server_port}/desktop.html',
                                       js_api=bridge, width=1200, height=900)
        bridge._window = window

        def js(expression):
            return window.evaluate_js(expression)

        def published():
            return (bridge.status().get('students') or {}).get('shares') or {}

        def rows():
            return js("Array.from(document.querySelectorAll('[data-student-share]'))"
                      ".map(function (el) { return el.getAttribute('data-student-share'); })")

        def set_share(key, percent):
            """Type a value into one row and commit it the way the page does (on blur)."""
            return js(f"""
                (function () {{
                  const box = document.querySelector('[data-student-share="{key}"]');
                  if (!box) return 'missing';
                  const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                  setter.call(box, '{percent}');
                  box.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  box.dispatchEvent(new FocusEvent('focusout', {{ bubbles: true }}));
                  box.blur();
                  return 'typed';
                }})()
            """)

        def exercise():
            wait_for(lambda: js("!!document.querySelector('[data-optimizer-students]')"),
                     'the students card')
            wait_for(lambda: js("!!window.pywebview?.api?.status"), 'the native bridge')
            wait_for(lambda: js("!!document.querySelector('[data-student-share]')"),
                     'the share controls')

            expect(tuple(rows()) == EXPECTED_KEYS,
                   f"the card offers every share stream in order: {rows()} (expected {list(EXPECTED_KEYS)})")
            label = js("(function(){const b=document.querySelector('[data-student-share=\"average\"]');"
                       "return b ? (b.closest('div').textContent || '') : '';})()") or ''
            expect('Student 9' in label and 'Mean earned' in label,
                   f"Student 9's row names the student: {label.strip()[:80]!r}")
            mechanism = js("(function(){const b=document.querySelector('[data-student-share=\"mechanism\"]');"
                           "return b ? (b.closest('div').textContent || '') : '';})()") or ''
            expect('Breakthrough' in mechanism and 'reliability' in mechanism,
                   f'the mechanism-guided stream has its own share control: {mechanism.strip()[:80]!r}')
            expect(published().get('mechanism') == 0.0,
                   f'the mechanism-guided stream starts switched off, by design: '
                   f'{published().get("mechanism")!r}')
            default_off = published().get('average')
            expect(default_off == 0.0,
                   f'Student 9 starts switched off, by design: {default_off!r}')

            expect(set_share('average', 20) == 'typed', "Student 9's share box accepts a value")
            wait_for(lambda: (published().get('average') or 0) > 0,
                     "the host received Student 9's share")
            after = published()
            # The typed numbers are a split, not fractions that must already total 1: 20% added to a
            # configured 100% is a 120% declaration and comes back as 20/120. What matters is that the
            # share the user typed is the share that governs.
            expect(abs(after.get('average', 0) - 0.2/1.2) < 1e-6,
                   f'Student 9 holds the share that was typed, normalised: {after!r}')
            expect(abs(sum(after.values()) - 1.0) < 1e-6,
                   f'the published split still totals 1: {after!r}')

            # The failure mode this guards: editing a *neighbour* must not silently reset Student 9,
            # which is what a row that is rendered but missing from the submitted map does.
            expect(set_share('community', 20) == 'typed', 'the community row still submits')
            wait_for(lambda: (published().get('community') or 0) > 0.15, 'the community share landed')
            expect((published().get('average') or 0) > 0,
                   f'editing another student keeps Student 9 switched on: {published()!r}')

            expect(set_share('average', 0) == 'typed', 'Student 9 can be switched off again')
            wait_for(lambda: (published().get('average') or 0) == 0.0,
                     'Student 9 switched off')
            expect(published().get('average') == 0.0,
                   f'Student 9 is off again: {published()!r}')

        result = {}

        def guarded():
            # `webview.start` runs this on its own thread and *swallows* whatever it raises: an
            # assertion inside the callback would leave the window open forever instead of failing the
            # check. Every outcome is therefore recorded and the window is always destroyed, so this
            # script always reaches its own exit code and never leaves a host running.
            try:
                exercise()
                result['ok'] = True
            except BaseException as error:  # noqa: BLE001 - reported below, never swallowed
                result['error'] = f'{type(error).__name__}: {error}'
            finally:
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001 - it may already be closing
                    pass
                report()
                # `window.destroy()` does not always end `webview.start` on Windows, and a check that
                # hangs is worse than one that fails: the summary is already printed, so the exit code
                # is all that is left to deliver.
                os._exit(1 if FAILURES else 0)

        try:
            webview.start(guarded)
        finally:
            server.shutdown()
            try:
                bridge.shutdown.set()
            except Exception:  # noqa: BLE001 - the bridge may already have stopped
                pass
        if not result.get('ok'):
            print(f'  the page walk did not finish: {result.get("error", "no result")}')
            FAILURES.append('the page walk did not finish')
    report()
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
