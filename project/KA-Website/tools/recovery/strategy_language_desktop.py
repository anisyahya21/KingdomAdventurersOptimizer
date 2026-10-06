"""Original full optimizer desktop shell with selectable native engine adapters.

The original React page and library controls remain the UI source of truth.
Native observations have an isolated namespace and retain their original flags.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[2] / 'coordination/native-preparation/shared'
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(HERE.parents[2])
BASE = Path(os.environ['LOCALAPPDATA']) / 'KingdomAdventurersOptimizer'
sys.path.insert(0, str(HERE))
import strategy_optimizer_desktop as original

ORIGINAL_OPTIMIZER = original.Optimizer
SELECTED_ENGINE = 'original'


def optimizer_factory(path):
    if SELECTED_ENGINE == 'original':
        return ORIGINAL_OPTIMIZER(path)
    from strategy_native_controller import NativeOptimizer
    return NativeOptimizer(path, SELECTED_ENGINE)


class LanguageBridge(original.Bridge):
    """Same public API as the original bridge; reads remain engine scoped."""
    def __init__(self, library, remember=False):
        self._engine = SELECTED_ENGINE
        super().__init__(library, remember)

    def optimizer_engines(self):
        state = self.status().get('state')
        options = {
            'original': ('Original Python + Rust',
                         'Original Python optimizer and Rust battle adapter; original-library results remain in the original engine scope.'),
            'rust': ('Rust native engine',
                     'Native summaries use measured records compatible with the Rust engine and library; original-engine records remain separate.'),
            'go': ('Go native engine',
                   'Native summaries use measured records compatible with the Go engine and library; original-engine records remain separate.'),
            'cpp': ('C++ native engine',
                    'Native summaries use measured records compatible with the C++ engine and library; original-engine records remain separate.'),
        }
        return {
            'selected': self._engine,
            'busy': state in ('Running', 'Saving', 'Opening library'),
            'options': [
                {'id': engine, 'label': label, 'description': description}
                for engine, (label, description) in options.items()
            ],
        }

    def optimizer_select_engine(self, engine):
        global SELECTED_ENGINE
        if engine not in ('original', 'rust', 'go', 'cpp'):
            return {'ok': False, 'error': 'Unknown engine'}
        if not self._operation.acquire(blocking=False):
            return {'ok': False, 'error': 'Wait for the active library or replay operation.'}
        try:
            self._paused()
            if engine == self._engine:
                return {'ok': True, 'unchanged': True}
            previous_engine = self._engine
            self._optimizer.command('close')
            self._optimizer.thread.join()
            self._close_encounter_ledger()
            SELECTED_ENGINE = engine
            try:
                replacement = optimizer_factory(self._library)
            except Exception:
                SELECTED_ENGINE = previous_engine
                self._optimizer = optimizer_factory(self._library)
                raise
            self._engine = engine
            self._optimizer = replacement
            self._optimizer.prepare_status_transport()
            self._save_settings()
            return {'ok': True}
        except Exception as error:
            return {'ok': False, 'error': str(error)}
        finally:
            self._operation.release()

    def _switch_library(self, path):
        return super()._switch_library(path)

    def status(self):
        value = self._optimizer.status()
        value['library'] = str(self._library)
        value['selectedEngine'] = self._engine
        value['libraryCapacityMiB'] = original.strategy_optimizer.MAX_DB_MB
        return value

    def command(self, action, value=None):
        if self._engine == 'original':
            return super().command(action, value)
        allowed = {'start', 'community_start', 'pause', 'stop', 'keep', 'all_encounters',
            'probe', 'fine_tune', 'migrate_objective', 'focus_encounter', 'focused_experiment',
            'end_experiment', 'students', 'student_report', 'breakthrough_budget', 'mp_recovery',
            'encounter_preview', 'encounter_activate', 'encounter_question', 'encounter_rollback',
            'encounter_budget'}
        if action not in allowed:
            return {'ok': False, 'error': 'Unknown command'}
        if not self._operation.acquire(blocking=False):
            return {'ok': False, 'error': 'Please wait for the selected replay or library operation.'}
        try:
            self._start_requested = action in ('start', 'community_start')
            return self._optimizer.command(action, value or {}, wait=True)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        finally:
            self._start_requested = False
            self._operation.release()

    def status_transport(self):
        self._optimizer.prepare_status_transport()
        return {'path': self._status_path}

    def _status_transport_body(self):
        if self._engine == 'original':
            return super()._status_transport_body()
        # Native projections may take longer than the browser's request timeout.  The controller
        # publishes one complete cached snapshot in the background; keep this HTTP handler cheap.
        base_json, live = self._optimizer.status_transport_data()
        live['library'] = str(self._library)
        live['selectedEngine'] = self._engine
        live['libraryCapacityMiB'] = original.strategy_optimizer.MAX_DB_MB
        live_json = json.dumps(live, separators=(',', ':'), ensure_ascii=False,
                               allow_nan=False).encode('utf-8')
        return b'{"base":' + base_json.encode('utf-8') + b',"live":' + live_json + b'}'

    def _read_native(self, method, *args, **kwargs):
        if self._engine == 'original':
            return getattr(super(), method)(*args, **kwargs)
        return self._optimizer.read(method, *args, **kwargs)

    def encounter_strategies(self, encounter_id, defeat_count=0):
        return self._read_native('encounter_strategies', encounter_id, defeat_count)

    def overview_average_leaders(self):
        return self._read_native('overview_average_leaders')

    def strategy_detail(self, candidate_id=None, holder_key=None):
        return self._read_native('strategy_detail', candidate_id, holder_key)

    def fine_tune_programs(self, candidate_id=None):
        return self._read_native('fine_tune_programs', candidate_id)

    def experiment_report(self, candidate_id=None):
        return self._read_native('experiment_report', candidate_id)

    def encounter_player_regions(self, candidate_limit=None, encounter_id=None):
        return self._read_native('encounter_player_regions', candidate_limit, encounter_id)

    def restore_strategy(self, holder_key):
        if self._engine == 'original':
            return super().restore_strategy(holder_key)
        return self._native_operation('restore_strategy', {'holderKey': holder_key})

    def _native_operation(self, action, value):
        if not self._operation.acquire(blocking=False):
            return {'ok': False, 'error': 'Wait for the active replay or library operation.'}
        try:
            self._paused()
            return self._optimizer.request(action, value)
        except Exception as error:
            return {'ok': False, 'error': str(error)}
        finally:
            self._operation.release()

    def run_strategy(self, candidate_id=None, holder_key=None, count=8):
        if self._engine == 'original':
            return super().run_strategy(candidate_id, holder_key, count)
        return self._native_operation('run_strategy', {'candidateId': candidate_id, 'holderKey': holder_key, 'count': count})

    def start_strategy_experiment(self, candidate_id=None, holder_key=None, count=4096, mode='evaluate'):
        if self._engine == 'original':
            return super().start_strategy_experiment(candidate_id, holder_key, count, mode)
        return self._native_operation('start_strategy_experiment', {'candidateId': candidate_id, 'holderKey': holder_key, 'count': count, 'mode': mode})

    def export_build(self, candidate_id):
        if self._engine == 'original':
            return super().export_build(candidate_id)
        import webview
        detail = self.strategy_detail(candidate_id)
        if not detail.get('ok'):
            return detail
        paths = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='strategy.scenario.json')
        if paths:
            path = Path(paths[0] if isinstance(paths, (tuple, list)) else paths)
            path.write_text(json.dumps(detail['scenario'], indent=2), encoding='utf-8')
        return {'ok': True}

    def export_diagnostics(self, count=None):
        if self._engine == 'original':
            return super().export_diagnostics(count)
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=f'{self._engine}-diagnostics.json')
        if not paths:
            return {'ok': True, 'cancelled': True}
        path = Path(paths[0] if isinstance(paths, (tuple, list)) else paths)
        return self._optimizer.request('export_diagnostics', {'path': str(path), 'count': count})

    def export_diagnostics_status(self, job_id=None):
        return self._read_native('export_diagnostics_status', job_id)

    def _native_replay(self, action, candidate_id=None, holder_key=None, seeds=None):
        return self._native_operation(action, {'candidateId': candidate_id, 'holderKey': holder_key, 'seeds': seeds})

    def replay(self, candidate_id, seeds):
        if self._engine == 'original':
            return super().replay(candidate_id, seeds)
        return self._native_replay('replay', candidate_id, seeds=seeds)

    def replay_holder(self, key):
        if self._engine == 'original':
            return super().replay_holder(key)
        return self._native_replay('replay_holder', holder_key=key)

    def replay_strategy_run(self, candidate_id=None, holder_key=None, seeds=None):
        if self._engine == 'original':
            return super().replay_strategy_run(candidate_id, holder_key, seeds)
        return self._native_replay('replay_strategy_run', candidate_id, holder_key, seeds)

    def simulate_strategy_visual(self, candidate_id=None, holder_key=None):
        if self._engine == 'original':
            return super().simulate_strategy_visual(candidate_id, holder_key)
        return self._native_replay('simulate_strategy_visual', candidate_id, holder_key)


def main():
    global SELECTED_ENGINE
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', choices=['original', 'rust', 'go', 'cpp'], default='original')
    parser.add_argument('--library', type=Path)
    args = parser.parse_args()
    SELECTED_ENGINE = args.engine
    original.Optimizer = optimizer_factory
    original.Bridge = LanguageBridge
    base_asset_handler = original.asset_handler
    def language_assets(*args, **kwargs):
        base = base_asset_handler(*args, **kwargs)
        class Assets(base):
            def do_GET(self):
                from urllib.parse import urlsplit, parse_qs
                parsed = urlsplit(self.path)
                if parsed.path == '/__language_frontend_error':
                    message = parse_qs(parsed.query).get('message', ['unknown'])[0][:12000]
                    BASE.mkdir(parents=True, exist_ok=True)
                    with (BASE / 'language-ui-frontend-errors.log').open('a', encoding='utf-8') as out:
                        out.write(message + '\n')
                    self.send_response(204)
                    self.end_headers()
                    return
                if parsed.path == '/desktop.html':
                    path = Path(self.translate_path(self.path))
                    text = path.read_text(encoding='utf-8')
                    logger = '''<script>function reportLanguageError(e){fetch('/__language_frontend_error?message='+encodeURIComponent(String(e.error&&e.error.stack||e.reason&&e.reason.stack||e.message||e.reason||e))).catch(function(){});}window.addEventListener('error',reportLanguageError);window.addEventListener('unhandledrejection',reportLanguageError);</script>'''
                    body = text.replace('<head>', '<head>' + logger, 1).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()
        return Assets
    original.asset_handler = language_assets
    original_settings = original.SETTINGS
    original.SETTINGS = BASE / 'language-ui-settings.json'
    original.DEFAULT_LIBRARY = BASE / 'languages' / 'full-ui-strategies.sqlite'
    original.DEFAULT_LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    if args.library is None and not original.DEFAULT_LIBRARY.exists() and original_settings.is_file():
        # Preserve the original file and take a consistent SQLite snapshot, including its WAL.
        import sqlite3
        source = Path(json.loads(original_settings.read_text(encoding='utf-8'))['library'])
        if source.is_file():
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
                with sqlite3.connect(original.DEFAULT_LIBRARY) as dst:
                    src.backup(dst)
    import webview
    create_window = webview.create_window
    def language_window(title, url, *positional, **kwargs):
        if isinstance(url, str) and '/desktop.html' in url:
            url += ('&' if '?' in url else '?') + 'languages=1'
        return create_window('Kingdom Adventurers · Optimizer Engines', url, *positional, **kwargs)
    webview.create_window = language_window
    original.main(['--library', str(args.library)] if args.library else [])


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as error:
        import traceback
        BASE.mkdir(parents=True, exist_ok=True)
        (BASE / 'language-ui-startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Full optimizer could not start', 0x10)
        else:
            raise
