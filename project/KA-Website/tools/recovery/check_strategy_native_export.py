"""Read-only integration checks for the native library's streamed JSON export.

The checker requires an explicit path to a saved full-UI library. It takes a SQLite online backup
into a disposable directory, opens only that copy with the current C++ finishing manifest, and runs
exports against the copy. It never imports observations, starts a native runner, or writes the source
library. On failure it preserves the scratch copy and diagnostics for inspection.

    python check_strategy_native_export.py --library PATH_TO_FULL_UI_LIBRARY
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import sys
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT_ROOT = ROOT / 'coordination' / 'native-finish' / 'export-check-v2'
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_diagnostic_export as export_contract
import strategy_native_assets
from strategy_native_library import _unpack
from strategy_native_controller import NativeOptimizer

CHECKS = 0
CHECK_LABELS = []
EXPORT_COUNT = 100
WAIT_SECONDS = 180


def check(condition, label):
    global CHECKS
    if not condition:
        raise AssertionError(label)
    CHECKS += 1
    CHECK_LABELS.append(label)


def canonical_sha(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save_report(directory, report):
    data = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    path = directory / 'report.json'
    temporary = directory / 'report.json.tmp'
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    latest_temp = REPORT_ROOT / f'report-latest-{os.getpid()}-{uuid.uuid4().hex}.tmp'
    with latest_temp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(latest_temp, REPORT_ROOT / 'report-latest.json')
    return path


def open_readonly(path):
    resolved = Path(path).resolve(strict=True)
    db = sqlite3.connect(resolved.as_uri() + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def make_backup(source, destination):
    """Use SQLite's online backup API so committed WAL data is included without source writes."""
    src = open_readonly(source)
    dst = sqlite3.connect(str(destination), timeout=30)
    try:
        src.backup(dst, pages=512, sleep=0.05)
        check(dst.execute('PRAGMA quick_check').fetchone()[0] == 'ok',
              'the disposable SQLite backup passes quick_check')
    finally:
        dst.close()
        src.close()


def evidence_fingerprint(db, scope):
    """Hash strategy evidence keys and raw-hash indexes, without decompressing battle payloads."""
    digest = hashlib.sha256()
    for table, columns, order in (
        ('nui_observation', 'observation_id,candidate_id,dedup_key,raw_sha256,semantic_sha256',
         'observation_id'),
        ('nui_runs', 'journal_id,observation_id,raw_sha256', 'journal_id'),
    ):
        digest.update(table.encode('ascii') + b'\0')
        query = f'SELECT {columns} FROM {table} WHERE scope=? ORDER BY {order}'
        count = 0
        for row in db.execute(query, (scope,)):
            digest.update(json.dumps(tuple(row), separators=(',', ':'), allow_nan=False).encode('utf-8'))
            digest.update(b'\n')
            count += 1
        digest.update(str(count).encode('ascii') + b'\0')
    return digest.hexdigest()


def latest_observations(db, scope, count):
    rows = list(db.execute(
        'SELECT * FROM nui_observation WHERE scope=? '
        'ORDER BY created_at DESC, observation_id DESC LIMIT ?', (scope, count)))
    rows.sort(key=lambda row: (row['created_at'], row['observation_id']))
    return rows


def assert_export_matches_db(db, library, destination, requested):
    scope = library.scope
    rows = latest_observations(db, scope, requested)
    check(len(rows) == requested, 'the disposable current-manifest C++ scope contains 100 observations')

    payload = json.loads(destination.read_text(encoding='utf-8'))
    check(payload.get('schema') == 'ka-native-library-export-1', 'the export uses the native JSON schema')
    check(payload.get('engine') == 'cpp', 'the export identifies the C++ engine')
    check(payload.get('scope') == scope, 'the export identifies the current-manifest library scope')
    check(payload.get('compatibility') == library.scope_identity,
          'the export retains the full compatibility identity')
    check(payload.get('provenance') == library.provenance,
          'the export preserves controller and engine provenance')
    check(payload.get('verificationOnly') is False, 'current-manifest production scope is not downgraded')
    check(payload.get('requestedCount') == requested and payload.get('includedCount') == requested,
          'the JSON header reports the requested and included latest-100 window')
    check(payload.get('totalObservations') == int(db.execute(
        'SELECT COUNT(*) FROM nui_observation WHERE scope=?', (scope,)).fetchone()[0]),
        'the header reports the exact durable scope total')

    observations = payload.get('observations')
    check(isinstance(observations, list) and len(observations) == requested,
          'the output has exactly 100 observation rows')
    expected_ids = [row['observation_id'] for row in rows]
    check([row.get('observationId') for row in observations] == expected_ids,
          'the exported IDs match the newest-100 window in its documented output order')
    for source, exported in zip(rows, observations):
        check(exported.get('candidateId') == source['candidate_id'],
              'each observation keeps its exact candidate reference')
        check(exported.get('rawSha256') == source['raw_sha256'],
              'each observation keeps its durable raw-record hash')
        check(exported.get('nativeFlags') == json.loads(source['native_flags']),
              'each observation keeps its original native flags')
        check(exported.get('earned') == source['earned']
              and exported.get('earnedBasis') == source['earned_basis']
              and exported.get('sourceEarnedBasis') == source['source_earned_basis'],
              'each observation keeps its recorded Earned value and basis')
        check(exported.get('purpose') == source['purpose']
              and exported.get('executionMode') == source['execution_mode'],
              'each observation keeps its execution mode and purpose')
        check(exported.get('finishDiagnostic') is bool(source['finish_diagnostic'])
              and exported.get('dedupConflict') is bool(source['dedup_conflict']),
              'finish diagnostics and dedup conflicts remain distinct fields')

    expected_candidate_ids = sorted({row['candidate_id'] for row in rows})
    candidates = payload.get('candidates')
    check(isinstance(candidates, list)
          and [row.get('candidateId') for row in candidates] == expected_candidate_ids,
          'candidate metadata includes exactly the candidates referenced by the selected observations')
    for candidate in candidates:
        stored = db.execute('SELECT * FROM nui_candidate WHERE scope=? AND candidate_id=?',
                            (scope, candidate['candidateId'])).fetchone()
        check(stored is not None, 'every exported candidate exists in the copied native library')
        intent = _unpack(stored['intent_zlib'])
        check(candidate.get('intent') == intent
              and candidate.get('intentSha256') == stored['intent_sha256'] == canonical_sha(intent),
              'candidate intent and checksum match the frozen library record')
        check(candidate.get('label') == stored['label']
              and candidate.get('parentId') == stored['parent_id']
              and candidate.get('language') == stored['language']
              and candidate.get('sourceIdentity') == stored['source_identity'],
              'candidate lineage and source identity are preserved')

    selected_ids = set(expected_ids)
    expected_journals = []
    for observation in rows:
        logs = db.execute(
            'SELECT journal_id,raw_sha256,raw_zlib,provenance_zlib FROM nui_runs '
            'WHERE scope=? AND observation_id=? ORDER BY created_at,journal_id',
            (scope, observation['observation_id']))
        for log in logs:
            expected_journals.append((observation['observation_id'], log))
    journals = payload.get('journals')
    check(isinstance(journals, list) and len(journals) == len(expected_journals),
          'the export includes every source journal row for the selected observations')
    exported_hashes = []
    for (observation_id, source), exported in zip(expected_journals, journals):
        envelope = _unpack(source['raw_zlib'])
        expected_record = envelope.get('nativeRecord', envelope)
        check(exported.get('observationId') == observation_id
              and exported.get('journalId') == source['journal_id'],
              'journal entries retain their observation and journal IDs')
        check(exported.get('rawSha256') == source['raw_sha256'],
              'journal raw hashes match the durable compressed records')
        check(exported.get('rawRecord') == expected_record,
              'journal raw records are preserved exactly')
        check(exported.get('provenance') == _unpack(source['provenance_zlib']),
              'per-journal provenance is preserved exactly')
        exported_hashes.append(exported['rawSha256'])
    check(len(exported_hashes) == len(set(exported_hashes)),
          'deduplicated raw hashes occur once across exported journal rows')
    check(all(row['observationId'] in selected_ids for row in journals),
          'no unrelated journal entered the requested latest-100 window')

    return payload


def assert_bad_counts(controller, root):
    for index, bad in enumerate((0, -1, 1_000_001, 'not-a-count', '1.5', True)):
        destination = root / f'invalid-count-{index}.json'
        result = controller.request('export_diagnostics', {'path': str(destination), 'count': bad})
        error = str(result.get('error', '')).lower()
        check(result.get('ok') is False and ('number of battles' in error or 'between 1 and' in error),
              f'malformed count {bad!r} is rejected with an actionable range/integer message')
        check(not destination.exists() and not result.get('jobId'),
              f'malformed count {bad!r} does not create a file or background job')


def assert_cap_failure_is_atomic(controller, root):
    target = root / 'protected.json'
    sentinel = b'keep-this-existing-export-intact\n'
    target.write_bytes(sentinel)
    before_temps = set(root.glob(target.name + '.*.tmp'))
    original_cap = export_contract.MAX_EXPORT_BYTES
    try:
        # The checker runs as its own process and exports only to a disposable SQLite clone.
        export_contract.MAX_EXPORT_BYTES = 256
        try:
            controller._libcall('export_diagnostics', str(target), EXPORT_COUNT)
            raise AssertionError('an export exceeding the injected cap should fail')
        except ValueError as exc:
            check('cap' in str(exc).lower(), 'the small-cap failure reports the byte cap')
    finally:
        export_contract.MAX_EXPORT_BYTES = original_cap
    check(target.read_bytes() == sentinel, 'a failed export preserves the destination sentinel byte-for-byte')
    check(set(root.glob(target.name + '.*.tmp')) == before_temps,
          'a failed export removes its temporary file and leaves no stray partial output')


def assert_async_controller_export(controller, root):
    target = root / 'latest-100.json'
    check(controller._state == 'Stopped' and controller._active is None,
          'the checker begins with no optimizer wave or active battle task')
    progress_events = []
    original_export = controller._library.export_diagnostics

    def capture_progress(path, count=None, progress=None):
        def capture(update):
            progress_events.append(dict(update))
            if callable(progress):
                progress(update)
        return original_export(path, count, capture)

    # Observe the controller's real callback without changing the library or invoking its writer
    # from this thread. The export method still runs only on its owning SQLite writer thread.
    controller._library.export_diagnostics = capture_progress
    try:
        started = controller.request('export_diagnostics', {'path': str(target), 'count': EXPORT_COUNT})
        check(started.get('ok') is True and started.get('jobId'),
              'the controller acknowledges the export with a background job ID')
        check(started.get('path') == str(target) and started.get('count') == EXPORT_COUNT,
              'the immediate job response keeps the selected destination and count')

        deadline = time.monotonic() + WAIT_SECONDS
        status = {}
        while time.monotonic() < deadline:
            status = controller.read('export_diagnostics_status', started['jobId'])
            if status.get('state') != 'running':
                break
            time.sleep(0.1)
    finally:
        controller._library.export_diagnostics = original_export
    check(status.get('state') == 'done' and status.get('stage') == 'complete',
          f'the asynchronous controller export reaches a successful terminal state: {status}')
    check(status.get('path') == str(target) and status.get('requested') == EXPORT_COUNT
          and status.get('included') == EXPORT_COUNT,
          'the completed job reports the selected path and exact requested/included counts')
    result = status.get('result') or {}
    check(result.get('ok') is True and result.get('format') == 'json'
          and result.get('observations') == EXPORT_COUNT and result.get('path') == str(target),
          'the controller result reports the JSON format, count, and destination')
    check(status.get('requestedCount') == EXPORT_COUNT and status.get('examined') == EXPORT_COUNT
          and status.get('totalObservations') == EXPORT_COUNT,
          'the callback reports the selected-window request and observation counts')
    check(status.get('journalDone') == status.get('journalTotal') == result.get('journals'),
          'the callback reports completed journal work against its real total')
    check(status.get('candidateCount') == result.get('candidates')
          and isinstance(status.get('candidateDone'), int)
          and 0 <= status['candidateDone'] <= status['candidateCount'],
          'the callback reports bounded candidate progress and the exact candidate total')
    check(status.get('bytesWritten') == result.get('bytes') and result.get('bytes', 0) > 0,
          'the completed callback and result agree on bytes written')
    elapsed = status.get('elapsedSeconds')
    check(isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
          and elapsed >= 0 and status.get('stageLabel'),
          'the callback reports a labeled stage and nonnegative elapsed time')
    stages = {update.get('stage') for update in progress_events}
    check({'prepare', 'candidates', 'observations', 'journals', 'finalize'} <= stages,
          'the library progress callback emits every actual export stage')
    candidate_updates = [update for update in progress_events if update.get('stage') == 'candidates']
    check(bool(candidate_updates)
          and candidate_updates[-1].get('candidateDone') == result.get('candidates'),
          'candidate progress reaches its exact exported candidate count')
    check(all(update.get('requestedCount') == EXPORT_COUNT
              and 0 <= update.get('included', -1) <= EXPORT_COUNT
              and update.get('totalLibraryObservations') == status.get('totalLibraryObservations')
              and 0 <= update.get('candidateDone', -1) <= update.get('candidateCount', -1)
              and 0 <= update.get('journalDone', -1) <= update.get('journalTotal', -1)
              and 0 <= update.get('bytesWritten', -1) <= result.get('bytes', -1)
              for update in progress_events),
          'progress callback counts stay within the requested window and library total')
    check(all(status.get(field) is None or
              (isinstance(status.get(field), (int, float)) and not isinstance(status.get(field), bool)
               and status[field] >= 0) for field in ('ratePerSecond', 'etaSeconds')),
          'rate and ETA are either explicitly unknown or nonnegative finite-work values')
    check(target.is_file(), 'the completed asynchronous export exists at its reported path')
    return target, result, status


def run(source):
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    scratch = (REPORT_ROOT / run_id).resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    scratch.relative_to(REPORT_ROOT.resolve())  # fail closed if the configured report root is redirected
    copied = scratch / 'full-ui-library-copy.sqlite'
    destination = scratch / 'latest-100.json'
    controller = None
    check_db = None
    failure = None
    report = dict(schema='native-library-export-check-2', runId=run_id,
        sourceLibrary=str(Path(source).resolve()), scratchDirectory=str(scratch),
        startedUtc=datetime.now(timezone.utc).isoformat(), outcome='running')
    global CHECKS, CHECK_LABELS
    CHECKS = 0
    CHECK_LABELS = []
    try:
        make_backup(source, copied)
        assets = strategy_native_assets.validate_engine('cpp')
        check(assets.get('source') == 'finishing-manifest',
              'the check is pinned to the currently selected C++ finishing manifest')
        check(bool(assets.get('revision')) and assets.get('language') == 'cpp',
              'the selected C++ manifest has an explicit revision')

        controller = NativeOptimizer(copied, 'cpp')
        check(controller._execution_mode == 'production',
              'the copied full-UI library opens under an explicitly validated production scope')
        check(controller._engine_assets.get('revision') == assets.get('revision'),
              'the controller and fresh manifest agree on the C++ revision')
        library = controller._library
        check_db = open_readonly(copied)
        count = int(check_db.execute(
            'SELECT COUNT(*) FROM nui_observation WHERE scope=?', (library.scope,)).fetchone()[0])
        check(count >= EXPORT_COUNT,
              f'the current C++ manifest scope has at least {EXPORT_COUNT} durable observations')
        compatibility = check_db.execute(
            "SELECT value FROM nui_meta WHERE scope=? AND key='compatibility'", (library.scope,)).fetchone()
        check(compatibility is not None and json.loads(compatibility['value']) == library.scope_identity,
              'the copied scope is the exact current-manifest compatibility identity')
        before_evidence = evidence_fingerprint(check_db, library.scope)

        assert_bad_counts(controller, scratch)
        destination, export_result, progress_status = assert_async_controller_export(controller, scratch)
        payload = assert_export_matches_db(check_db, library, destination, EXPORT_COUNT)
        check(export_result.get('candidates') == len(payload['candidates'])
              and export_result.get('journals') == len(payload['journals']),
              'the controller result reports the exact candidate and journal counts in the JSON')
        assert_cap_failure_is_atomic(controller, scratch)
        check_db.close()
        check_db = open_readonly(copied)
        after_evidence = evidence_fingerprint(check_db, library.scope)
        check(after_evidence == before_evidence,
              'export, malformed-count, and cap checks add or alter no strategy evidence or raw hashes')
        report.update(outcome='passed', engine='cpp', revision=assets['revision'], scope=library.scope,
            observationTotal=count, requestedCount=EXPORT_COUNT, includedCount=len(payload['observations']),
            candidates=len(payload['candidates']), journals=len(payload['journals']),
            progressStatus=progress_status, exportPath=str(destination), exportBytes=destination.stat().st_size,
            exportSha256=sha256_file(destination), evidenceFingerprint=after_evidence,
            battlesRun=0, strategyEvidenceMutated=False)
    except BaseException as exc:
        failure = exc
        report.update(outcome='failed', error=f'{type(exc).__name__}: {exc}',
                      traceback=traceback.format_exc())
    finally:
        if check_db is not None:
            try:
                check_db.close()
            except Exception as exc:
                if failure is None:
                    failure = exc
                    report.update(outcome='failed', error=f'{type(exc).__name__}: {exc}')
        if controller is not None:
            try:
                controller.close()
            except Exception as exc:
                if failure is None:
                    failure = exc
                    report.update(outcome='failed', error=f'{type(exc).__name__}: {exc}')
                print(f'Could not close disposable native controller cleanly: {exc}',
                      file=sys.stderr, flush=True)
        report['outcome'] = 'passed' if failure is None else 'failed'
        report['checkCount'] = CHECKS
        report['checks'] = [{'label': label, 'passed': True} for label in CHECK_LABELS]
        report['endedUtc'] = datetime.now(timezone.utc).isoformat()
        report['reportPath'] = str(scratch / 'report.json')
        report['scratchPreserved'] = True
        report['successExportPreserved'] = failure is None and destination.is_file()
        try:
            save_report(scratch, report)
        except Exception as exc:
            if failure is None:
                failure = exc
                report['outcome'] = 'failed'
                report['error'] = f'{type(exc).__name__}: {exc}'
            print(f'Could not persist checker report: {exc}', file=sys.stderr, flush=True)

    if failure is not None:
        print(f'FAILED; diagnostics preserved: {scratch}', file=sys.stderr, flush=True)
        raise failure

    # Keep the copied library and successful export beside the durable per-run report. A later
    # coordinator can inspect this exact fixture without touching the source library again.
    report_path = scratch / 'report.json'
    latest_pointer = REPORT_ROOT / 'report-latest.json'
    print(json.dumps(dict(ok=True, report=str(report_path), latestReport=str(latest_pointer),
        revision=report.get('revision'), observations=report.get('includedCount'),
        candidates=report.get('candidates'), journals=report.get('journals'),
        exportPath=report.get('exportPath'), battlesRun=0), ensure_ascii=False), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, type=Path,
                        help='path to the real full-UI SQLite library; opened read-only for backup')
    args = parser.parse_args()
    return run(args.library)


if __name__ == '__main__':
    raise SystemExit(main())
