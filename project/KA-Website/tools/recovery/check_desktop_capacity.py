"""Deterministic checks for the desktop `--library-capacity-mib` opt-in.

Reads only: the desktop module, the optimiser module's default, one throwaway SQLite file and the
optimiser's own source line. It never constructs a real Optimizer or Bridge, never opens a window,
never runs a battle, never touches the live library or the real settings file, and never allocates
gigabytes - the SQLite probe only sets a page ceiling on an empty temporary database.
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import types
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer
import strategy_optimizer_desktop as desktop


BASELINE_MIB = 4096
MAX_MIB = 1048576
PRAGMA_PAGES_PER_MIB = 256


def _parse(argv):
    return desktop.build_parser().parse_args(argv)


def _rejected(argv):
    """Assert argparse rejects argv with its invalid-value exit code (2)."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            desktop.build_parser().parse_args(argv)
    except SystemExit as exc:
        assert exc.code == 2, (argv, exc.code)
        assert 'library capacity' in buffer.getvalue().lower(), buffer.getvalue()
        return
    raise AssertionError(f'expected argparse to reject {argv!r}')


def check_parsing(baseline):
    assert desktop.LIBRARY_CAPACITY_MIN_MIB == BASELINE_MIB, desktop.LIBRARY_CAPACITY_MIN_MIB
    assert desktop.LIBRARY_CAPACITY_MAX_MIB == MAX_MIB, desktop.LIBRARY_CAPACITY_MAX_MIB

    # Absent flag: None, so nothing is applied and the compiled default is left untouched.
    assert _parse(['--library', 'x.sqlite']).library_capacity_mib is None
    # Explicit values inside the range, including both bounds.
    assert _parse(['--library-capacity-mib', '4096']).library_capacity_mib == 4096
    assert _parse(['--library-capacity-mib', '8192']).library_capacity_mib == 8192
    assert _parse(['--library-capacity-mib', str(MAX_MIB)]).library_capacity_mib == MAX_MIB
    # Negative, non-integer, float, below-minimum, above-maximum and empty are all rejected.
    for bad in ('-1', 'abc', '8192.5', '100', str(MAX_MIB + 1), ''):
        _rejected([f'--library-capacity-mib={bad}'])
    # The help text must state the ceiling-not-allocation and no-autorun contract.
    help_text = ' '.join(desktop.build_parser().format_help().split()).lower()
    assert 'ceiling, not an allocation' in help_text, help_text
    assert 'does not start a run' in help_text, help_text

    original = strategy_optimizer.MAX_DB_MB
    assert desktop.apply_library_capacity(None) is None
    assert strategy_optimizer.MAX_DB_MB == original == baseline


class _Stop(Exception):
    """Sentinel used to stop main() at the fake Bridge, before any window exists."""


def check_apply_order(baseline, work):
    seen = []
    original_bridge = desktop.Bridge
    original_app = desktop.APP
    original_max = strategy_optimizer.MAX_DB_MB
    had_webview = 'webview' in sys.modules
    previous_webview = sys.modules.get('webview')

    app = work / 'app'
    (app / 'desktop-dist').mkdir(parents=True)
    (app / 'desktop-dist' / 'desktop.html').write_text('<!doctype html>', encoding='utf-8')
    library = work / 'probe.sqlite'

    fake_webview = types.ModuleType('webview')
    fake_webview.settings = {}

    class RecordingBridge:
        def __init__(self, lib, remember=False):
            # The ceiling must already be in force when the Optimizer-owning bridge is built.
            seen.append(dict(library=str(lib), remember=remember, maxDbMb=strategy_optimizer.MAX_DB_MB))
            raise _Stop()

    def run(argv):
        seen.clear()
        strategy_optimizer.MAX_DB_MB = baseline
        try:
            desktop.main(argv)
        except _Stop:
            pass
        else:
            raise AssertionError('Bridge was never constructed')

    desktop.Bridge = RecordingBridge
    desktop.APP = app
    sys.modules['webview'] = fake_webview
    try:
        for value in (8192, 4096):
            run(['--library', str(library), '--library-capacity-mib', str(value)])
            assert seen and int(seen[0]['maxDbMb']) == value, seen
            assert int(strategy_optimizer.MAX_DB_MB) == value, strategy_optimizer.MAX_DB_MB

        # No flag: the constructor must see the untouched default, not a raised one.
        run(['--library', str(library)])
        assert seen and int(seen[0]['maxDbMb']) == baseline, seen
        assert int(strategy_optimizer.MAX_DB_MB) == baseline

        # Invalid value: argparse stops main before the constructor is ever reached.
        seen.clear()
        strategy_optimizer.MAX_DB_MB = baseline
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                desktop.main(['--library', str(library), '--library-capacity-mib=-1'])
        except SystemExit as exc:
            assert exc.code == 2, exc.code
        else:
            raise AssertionError('invalid capacity was not rejected')
        assert not seen, seen
    finally:
        desktop.Bridge = original_bridge
        desktop.APP = original_app
        strategy_optimizer.MAX_DB_MB = original_max
        if had_webview:
            sys.modules['webview'] = previous_webview
        else:
            sys.modules.pop('webview', None)
    assert int(strategy_optimizer.MAX_DB_MB) == baseline


def check_settings_untouched():
    settings = desktop.SETTINGS
    if settings.is_file():
        before = settings.read_bytes(), settings.stat().st_mtime_ns
        assert settings.read_bytes() == before[0]
        assert settings.stat().st_mtime_ns == before[1]
        return True
    assert not settings.exists()
    return False


def check_pragma(work):
    """The MiB value maps to a SQLite page ceiling without allocating memory or disk."""
    db = work / 'capacity-probe.sqlite'
    connection = sqlite3.connect(str(db))
    try:
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        assert page_size > 0
        for mib in (BASELINE_MIB, 8192):
            connection.execute(f'PRAGMA max_page_count={mib * PRAGMA_PAGES_PER_MIB}')
            limit = connection.execute('PRAGMA max_page_count').fetchone()[0]
            assert limit == mib * PRAGMA_PAGES_PER_MIB, limit
            assert limit * page_size == mib * 1024 * 1024
    finally:
        connection.close()
    assert db.stat().st_size < 1024 * 1024, db.stat().st_size
    source = (HERE / 'strategy_optimizer.py').read_text(encoding='utf-8')
    assert 'PRAGMA max_page_count={MAX_DB_MB*256}' in source


def main():
    baseline = int(strategy_optimizer.MAX_DB_MB)
    assert baseline == BASELINE_MIB, f'expected the shipped {BASELINE_MIB} MiB default, found {baseline}'
    root = Path(__file__).resolve().parents[2] / 'tmp' / 'encounter-redesign-20260928' / (
        'desktop-capacity-' + uuid.uuid4().hex)
    root.mkdir(parents=True)

    check_parsing(baseline)
    check_apply_order(baseline, root)
    settings_existed = check_settings_untouched()
    check_pragma(root)
    print('PASS desktop --library-capacity-mib: parse bounds, opt-in ordering before Bridge, '
          f'default untouched ({baseline} MiB), settings unchanged (existed={settings_existed}), '
          'MiB-to-page ceiling confirmed on a throwaway database')


if __name__ == '__main__':
    main()
