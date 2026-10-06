"""Project native workflow status from an existing SQLite library without opening a controller.

This is an offline diagnostic helper: it opens SQLite in read-only mode, starts no writer or
native process, and never changes workflow state or observations.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from strategy_native_library import NativeLibrary
from strategy_native_workflows import NativeWorkflowMixin


def _meta(db, scope):
    return {row['key']: json.loads(row['value']) for row in db.execute(
        'SELECT key,value FROM nui_meta WHERE scope=?', (scope,))}


class _ReadonlyWorkflow(NativeWorkflowMixin):
    def __init__(self, db, scope, meta):
        self._db = db
        self._scope = scope
        self._metadata = meta
        self.language = str(meta.get('engine') or '')
        self._focus = (meta.get('nativeController') or {}).get('focusEncounters') or list(range(20))
        workflow = meta.get('nativeWorkflowV1') or {}
        self._native_workflow_chunk_done = threading.Event()
        if not workflow.get('activeExperimentId'):
            self._native_workflow_chunk_done.set()
        self._native_library_import_epoch = int(meta.get('totalRuns') or 0)

        # Bind only the read methods required by evidence_runs. No __init__, schema migration,
        # writer connection, production asset validation, or library mutation is performed.
        self._library = object.__new__(NativeLibrary)
        self._library.db = db
        self._library.scope = scope
        self._library.engine = self.language
        provenance = meta.get('provenance') or {}
        self._library.provenance = provenance
        self._library.execution_mode = provenance.get('executionMode', 'diagnostic')

    def _libcall(self, method, *args):
        if method == 'get':
            return self._metadata.get(args[0], args[1] if len(args) > 1 else None)
        if method == '_native_evidence_runs':
            return NativeLibrary.evidence_runs(self._library, args[0], args[1])
        if method == '_native_seed_pairs':
            return NativeLibrary.seed_pairs(self._library, args[0])
        raise RuntimeError(f'read-only projection rejected library operation: {method}')

    def _provenance(self):
        return self._metadata.get('provenance') or {}

    def status(self):
        active = (self._metadata.get('nativeWorkflowV1') or {}).get('activeExperimentId')
        return {'state': 'Running' if active else 'Stopped'}


def _scope_report(db, scope, meta):
    state = meta.get('nativeWorkflowV1')
    if not isinstance(state, dict):
        return {'scope': scope, 'error': 'nativeWorkflowV1 is not an object'}
    view = _ReadonlyWorkflow(db, scope, meta)
    started = time.perf_counter()
    try:
        projected = view._workflow_status()
        strict = json.dumps(projected, allow_nan=False, separators=(',', ':'))
        focused = projected.get('focusedExperiment') or {}
        experiments = state.get('experiments') or []
        return {
            'scope': scope,
            'engine': view.language,
            'totalRuns': meta.get('totalRuns', 0),
            'projectionRevision': state.get('_projectionRevision', 0),
            'activeExperimentId': state.get('activeExperimentId'),
            'experimentCount': len(experiments),
            'probeCount': len(projected.get('probes') or []),
            'fineTuneCount': len(projected.get('fineTune') or []),
            'focusedExperiment': {
                'id': focused.get('id'), 'status': focused.get('status'),
                'completed': focused.get('completed'), 'total': focused.get('total'),
                'comparisonCount': len(focused.get('comparisons') or []),
                'pairedEvidenceCounts': [item.get('pairedSeeds') for item in
                                         (focused.get('comparisons') or [])],
                'pairedIntervals': [item.get('paired') for item in
                                    (focused.get('comparisons') or [])],
            } if focused else None,
            'projectionSeconds': round(time.perf_counter() - started, 4),
            'strictJsonBytes': len(strict.encode('utf-8')),
            'strictJson': 'pass',
            'workflowError': projected.get('workflowError'),
        }
    except (TypeError, ValueError) as exc:
        return {'scope': scope, 'engine': view.language, 'strictJson': 'fail',
                'serializationError': str(exc)}
    except Exception as exc:  # preserve per-scope diagnostics and continue other scopes
        return {'scope': scope, 'engine': view.language, 'projectionError': str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, help='Existing native SQLite library path')
    parser.add_argument('--language', choices=('rust', 'go', 'cpp'),
                        help='Optional engine scope filter')
    args = parser.parse_args()
    path = Path(args.library).resolve()
    uri = path.as_uri() + '?mode=ro'
    try:
        db = sqlite3.connect(uri, uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        scopes = [row['scope'] for row in db.execute(
            "SELECT scope FROM nui_meta WHERE key='nativeWorkflowV1' ORDER BY scope")]
        reports = []
        for scope in scopes:
            meta = _meta(db, scope)
            if args.language and meta.get('engine') != args.language:
                continue
            reports.append(_scope_report(db, scope, meta))
        print(json.dumps({'schema': 'native-status-readonly-check-v1',
            'library': str(path), 'readOnly': True, 'nativeProcessesStarted': False,
            'scopeCount': len(reports), 'scopes': reports}, indent=2, allow_nan=False))
        return 1 if any(row.get('strictJson') == 'fail' or row.get('projectionError') or
                        row.get('workflowError')
                        for row in reports) else 0
    finally:
        if 'db' in locals():
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
