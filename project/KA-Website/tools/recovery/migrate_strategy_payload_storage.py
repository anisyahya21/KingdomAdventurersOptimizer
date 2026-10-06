"""Stage and verify the v5 lossless encounter-ledger storage migration.

The source database is always opened read-only.  Conversion writes only to an explicitly named
stage copy, checkpoints every committed keyset batch in ``ea_meta`` in the same transaction as the
row changes, and writes a compact result to a separate, previously absent VACUUM INTO path.

Run with ``--help`` for the guarded CLI.  The Tk progress monitor is on by default; ``--headless``
is intended for the synthetic checker and supervised automation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_experiment_store as store
import strategy_payload_codec as payload_codec

CHECKPOINT_KEY = '__strategy_payload_storage_migration_v5__'
INPUT_SCHEMA_VERSIONS = (3, 4)
TARGET_SCHEMA_VERSION = 5
TABLE_PHASES = ('ea_sample', 'ea_holdout', 'ea_experiment')
PAYLOAD_COLUMNS = {'ea_sample': ('result', 'outcome'),
                   'ea_holdout': ('result', 'outcome')}
INTENT_PROJECTIONS = ('compatibility', 'engineRevision', 'mechanicsRevision', 'policy',
                      'measurementWindow', 'changedFields')
DEFAULT_BATCH_ROWS = 128
DEFAULT_BATCH_BYTES = 32 * 1024 * 1024
DEFAULT_ENCODER_THREADS = 2


class MigrationError(RuntimeError):
    """The migration cannot continue without risking an incorrect or ambiguous result."""


class MigrationInterrupted(RuntimeError):
    """Test-only checkpoint hook exception used by the focused synthetic checker."""


class Progress:
    """Durable status and append-only diagnostics shared by the worker and Tk monitor."""

    def __init__(self, status_path, log_path, report_path):
        self.status_path = Path(status_path)
        self.log_path = Path(log_path)
        self.report_path = Path(report_path)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.stage_started = self.started
        self.last_stage = None
        self.last_counter = None
        self.last_counter_time = self.started
        self.last_persist = 0.0
        self.data = {'state': 'starting', 'stage': 'preflight', 'completed': 0, 'total': None,
                     'percent': None, 'elapsedSeconds': 0.0, 'rowsPerSecond': None,
                     'processingRate': None, 'processingRateUnit': None,
                     'etaSeconds': None, 'warning': None, 'error': None,
                     'outputLocation': str(self.report_path), 'updatedAt': _utc_now()}

    def update(self, **fields):
        with self.lock:
            state_changed = fields.get('state', self.data.get('state')) != self.data.get('state')
            stage_changed = fields.get('stage') is not None and fields['stage'] != self.last_stage
            if stage_changed:
                self.last_stage = fields['stage']
                self.stage_started = time.monotonic()
                self.last_counter = fields.get('completed')
                self.last_counter_time = self.stage_started
            self.data.update(fields)
            self.data['elapsedSeconds'] = round(time.monotonic() - self.started, 3)
            self.data['updatedAt'] = _utc_now()
            if 'total' in fields and fields['total'] is None:
                self.data['percent'] = None
            elif fields.get('total') is not None:
                total = fields['total']
                completed = fields.get('completed', self.data.get('completed'))
                self.data['percent'] = (round(100 * completed / total, 2)
                                        if total and completed is not None else
                                        (100.0 if total == 0 else None))
            rate = fields.get('rowsPerSecond')
            if rate is not None:
                self.data['processingRate'] = rate
                stage = self.data.get('stage', '')
                self.data['processingRateUnit'] = ('pages/s' if stage == 'consistent backup'
                                                   else 'rows/s')
            elif stage_changed:
                self.data['processingRate'] = None
                self.data['processingRateUnit'] = None
            elif isinstance(fields.get('completed'), (int, float)):
                now = time.monotonic()
                count = fields['completed']
                if (isinstance(self.last_counter, (int, float)) and
                        count >= self.last_counter and now - self.last_counter_time >= 0.5):
                    delta = count - self.last_counter
                    self.data['processingRate'] = round(delta / (now - self.last_counter_time), 3)
                    stage = self.data.get('stage', '')
                    if stage == 'consistent backup':
                        unit = 'pages/s'
                    elif 'integrity check' in stage:
                        unit = 'SQLite callbacks/s'
                    elif 'VACUUM INTO' in stage:
                        unit = 'SQLite callbacks/s'
                    else:
                        unit = 'rows/s'
                    self.data['processingRateUnit'] = unit
                    self.last_counter = count
                    self.last_counter_time = now
                elif self.last_counter is None or count < self.last_counter:
                    self.last_counter = count
                    self.last_counter_time = now
            now = time.monotonic()
            finished_stage = fields.get('total') is not None and fields.get('completed') == fields['total']
            if stage_changed or state_changed or finished_stage or now - self.last_persist >= 1.0:
                _atomic_json(self.status_path, self.data)
                _append_jsonl(self.log_path, self.data)
                self.last_persist = now

    def snapshot(self):
        with self.lock:
            data = dict(self.data)
        data['elapsedSeconds'] = round(time.monotonic() - self.started, 3)
        return data


def _utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=False, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'), default=_json_default)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _append_jsonl(path, value):
    with open(path, 'a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(',', ':'), default=_json_default))
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _norm_path(path):
    return Path(path).expanduser().resolve(strict=False)


def _ro_uri(path):
    # SQLite URI paths use forward slashes; quote spaces, #, ? and non-ASCII without quoting drive
    # punctuation.  mode=ro still observes a committed WAL, unlike immutable=1.
    encoded = quote(str(Path(path).resolve()).replace('\\', '/'), safe='/:')
    return 'file:%s?mode=ro' % encoded


def _connect_readonly(path):
    db = sqlite3.connect(_ro_uri(path), uri=True, timeout=30.0, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def _open_stage(path):
    db = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA synchronous=FULL')
    return db


def _schema_version(db):
    try:
        row = db.execute("SELECT value FROM ea_meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error as exc:
        raise MigrationError('database has no readable ea_meta schema marker') from exc
    if row is None:
        raise MigrationError('database has no ea_meta schema_version marker')
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise MigrationError('unrecognized schema_version value %r' % (row[0],)) from exc


def _has_table(db, table):
    return db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',
                      ('table', table)).fetchone() is not None


def _columns(db, table):
    return [row['name'] for row in db.execute('PRAGMA table_info("%s")' % table)]


def _integrity_check(db, progress, label):
    started = time.monotonic()
    counter = [0]
    progress.update(stage=label, completed=0, total=None, etaSeconds=None,
                    detail='Checking SQLite integrity; total VM work is unknown.')

    def tick():
        counter[0] += 1
        now = time.monotonic()
        if counter[0] % 200 == 0:
            progress.update(stage=label, completed=counter[0], total=None, rowsPerSecond=None,
                            etaSeconds=None, detail='SQLite VM progress callbacks: %d' % counter[0])
        return 0

    db.set_progress_handler(tick, 100000)
    try:
        rows = db.execute('PRAGMA integrity_check').fetchall()
    finally:
        db.set_progress_handler(None, 0)
    elapsed = time.monotonic() - started
    messages = [row[0] for row in rows]
    if messages != ['ok']:
        raise MigrationError('%s failed: %s' % (label, '; '.join(str(v) for v in messages[:8])))
    return {'elapsedSeconds': round(elapsed, 3), 'progressCallbacks': counter[0],
            'result': 'ok'}


def _resource_snapshot(paths):
    result = {'capturedAt': _utc_now(), 'logicalCpuCount': os.cpu_count(),
              'physicalCpuCount': None, 'availableMemoryBytes': None, 'memorySource': None,
              'disks': {}}
    if os.name == 'nt':
        try:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            length = ctypes.c_ulong(0)
            kernel.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
            buffer = ctypes.create_string_buffer(length.value)
            if kernel.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(length)):
                offset, cores = 0, 0
                while offset + 8 <= length.value:
                    relationship, size = struct.unpack_from('<II', buffer.raw, offset)
                    if size < 8 or offset + size > length.value:
                        break
                    cores += relationship == 0
                    offset += size
                if offset == length.value:
                    result['physicalCpuCount'] = cores
        except (AttributeError, OSError, struct.error):
            pass
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        try:
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                result['availableMemoryBytes'] = int(status.ullAvailPhys)
                result['totalMemoryBytes'] = int(status.ullTotalPhys)
                result['memorySource'] = 'GlobalMemoryStatusEx'
        except (AttributeError, OSError):
            pass
    else:
        try:
            pages = os.sysconf('SC_AVPHYS_PAGES')
            page_size = os.sysconf('SC_PAGE_SIZE')
            result['availableMemoryBytes'] = int(pages * page_size)
            result['memorySource'] = 'sysconf'
        except (AttributeError, ValueError, OSError):
            pass
    try:
        import psutil  # Optional: physical core count only, never required for conversion.
        result['physicalCpuCount'] = psutil.cpu_count(logical=False)
        result['cpuUtilizationPercent'] = psutil.cpu_percent(interval=0.25)
        if result['availableMemoryBytes'] is None:
            result['availableMemoryBytes'] = int(psutil.virtual_memory().available)
            result['memorySource'] = 'psutil'
    except ImportError:
        pass
    for label, path in paths.items():
        if path is None:
            continue
        parent = Path(path).resolve(strict=False)
        if parent.suffix or (parent.exists() and parent.is_file()):
            parent = parent.parent
        try:
            usage = shutil.disk_usage(parent)
            result['disks'][label] = {'path': str(parent), 'freeBytes': usage.free,
                                      'totalBytes': usage.total}
        except OSError as exc:
            result['disks'][label] = {'path': str(parent), 'error': str(exc)}
    return result


def _set_background_priority():
    try:
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000):
                return 'could not set Windows below-normal priority'
        else:
            os.nice(5)
    except (AttributeError, OSError) as exc:
        return 'could not set below-normal priority: %s' % exc
    return None


def _typed_equal(left, right):
    # SQLite INTEGER values arrive as Python int and REAL values as float.  Preserve REAL bit
    # patterns and distinguish BLOB/TEXT even where Python might otherwise compare equal.
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        return struct.pack('>d', left) == struct.pack('>d', right)
    return left == right


def _value_size(value):
    if value is None:
        return 1
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        # A conservative upper bound for UTF-8 and Python's temporary encoded copy.
        return len(value) * 4
    return 16


def _row_size(row):
    return sum(_value_size(value) for value in row)


def _table_columns_for_compare(db, table, allowed_added_column):
    rows = db.execute('PRAGMA table_info("%s")' % table).fetchall()
    values = [tuple(row) for row in rows]
    if table == 'ea_experiment' and allowed_added_column:
        values = [row for row in values if row[1] != 'full_intent']
    return values


def _schema_fingerprint(db):
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') "
        'ORDER BY name')]
    objects = [tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND type IN ('index','trigger','view') "
        'ORDER BY type,name')]
    return tables, objects


def _assert_schema_compatible(left_db, right_db, allow_version_change=True):
    left_tables, left_objects = _schema_fingerprint(left_db)
    right_tables, right_objects = _schema_fingerprint(right_db)
    if left_tables != right_tables:
        raise MigrationError('table set changed: source=%r stage=%r' % (left_tables, right_tables))
    for table in left_tables:
        left_columns = _table_columns_for_compare(left_db, table, False)
        right_columns = _table_columns_for_compare(
            right_db, table, table == 'ea_experiment' and
            'full_intent' in _columns(right_db, 'ea_experiment') and
            'full_intent' not in _columns(left_db, 'ea_experiment'))
        if left_columns != right_columns:
            raise MigrationError('column definition changed in table %s' % table)
        if table == 'ea_experiment':
            source_names = [row[1] for row in _table_columns_for_compare(left_db, table, False)]
            target_names = _columns(right_db, table)
            extra = [name for name in target_names if name not in source_names]
            if extra not in ([], ['full_intent']):
                raise MigrationError('unexpected ea_experiment columns: %r' % extra)
            if 'full_intent' in extra:
                info = next(tuple(row) for row in right_db.execute(
                    'PRAGMA table_info("ea_experiment")') if row['name'] == 'full_intent')
                if info[2].upper() != 'BLOB' or info[3] or info[4] is not None or info[5]:
                    raise MigrationError('ea_experiment.full_intent has an unexpected definition')
            for database, names in ((left_db, source_names), (right_db, target_names)):
                if 'full_intent' in names:
                    info = next(tuple(row) for row in database.execute(
                        'PRAGMA table_info("ea_experiment")') if row['name'] == 'full_intent')
                    if info[2].upper() != 'BLOB' or info[3] or info[4] is not None or info[5]:
                        raise MigrationError('ea_experiment.full_intent is not a nullable BLOB')
        left_sql = left_db.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?',
                                   (table,)).fetchone()[0]
        right_sql = right_db.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?',
                                     (table,)).fetchone()[0]
        if table == 'ea_experiment' and 'full_intent' not in _columns(left_db, table) and \
                'full_intent' in _columns(right_db, table):
            right_sql = re.sub(r',\s*full_intent\s+BLOB(?=\s*\))', '', right_sql,
                               count=1, flags=re.IGNORECASE)
        if left_sql != right_sql:
            raise MigrationError('table definition changed beyond the v5 full_intent column: %s'
                                 % table)
    if left_objects != right_objects:
        raise MigrationError('index, trigger, or view definitions changed')
    if left_db.execute('PRAGMA user_version').fetchone()[0] != \
            right_db.execute('PRAGMA user_version').fetchone()[0]:
        raise MigrationError('PRAGMA user_version changed')
    if left_db.execute('PRAGMA application_id').fetchone()[0] != \
            right_db.execute('PRAGMA application_id').fetchone()[0]:
        raise MigrationError('PRAGMA application_id changed')
    if not allow_version_change and _schema_version(left_db) != _schema_version(right_db):
        raise MigrationError('schema version marker changed unexpectedly')


def _intent_codec():
    try:
        import strategy_intent_codec
    except ImportError as exc:
        raise MigrationError('strategy_intent_codec.py is required for v5 migration') from exc
    encode = getattr(strategy_intent_codec, 'encode_intent', None)
    decode = getattr(strategy_intent_codec, 'decode_intent', None)
    if not callable(encode) or not callable(decode):
        raise MigrationError('strategy_intent_codec must expose encode_intent/decode_intent')
    return strategy_intent_codec


def _decode_intent(db_codec, metadata, full_blob):
    if metadata is None and full_blob is None:
        return None
    if isinstance(metadata, bytes) and full_blob is None:
        # Preserve unusual legacy SQLite BLOB values exactly.  The v5 codec takes raw JSON TEXT.
        if payload_codec.has_codec_magic(metadata):
            payload_codec.decode_text(metadata)
        return metadata
    return db_codec.decode_intent(metadata, full_blob)


def _sqlite_projection(db, raw, key):
    try:
        return tuple(db.execute('SELECT typeof(json_extract(?, ?)), json_extract(?, ?)',
                                (raw, '$.' + key, raw, '$.' + key)).fetchone())
    except sqlite3.Error as exc:
        raise MigrationError('SQLite could not read intent projection %s: %s' % (key, exc)) from exc


def _assert_intent_projection(db, raw, metadata):
    if not isinstance(raw, str) or not isinstance(metadata, str):
        return
    expressions = ','.join("typeof(json_extract(?, '$.%s')),json_extract(?, '$.%s')" % (key, key)
                           for key in INTENT_PROJECTIONS)
    try:
        before = tuple(db.execute('SELECT ' + expressions, (raw,) * (2 * len(INTENT_PROJECTIONS))).fetchone())
        after = tuple(db.execute('SELECT ' + expressions, (metadata,) * (2 * len(INTENT_PROJECTIONS))).fetchone())
    except sqlite3.Error:
            # Invalid/unprojectable legacy JSON is an explicit full-TEXT fallback in v5.  Since
            # the exact raw string remains in intent_json, its pre-existing SQLite failure mode
            # is unchanged.  A changed metadata string must still pass every SQL projection.
        if raw == metadata:
            return
        raise
    if before != after:
        raise MigrationError('v5 intent metadata changed SQLite json_extract semantics')


def _cell_for_logical_compare(db, table, column, row, columns, intent_codec, is_target):
    value = row[column]
    if table in PAYLOAD_COLUMNS and column in PAYLOAD_COLUMNS[table]:
        if isinstance(value, bytes) and payload_codec.has_codec_magic(value):
            return payload_codec.decode_text(value)
        return value
    if table == 'ea_experiment' and column == 'intent_json':
        if is_target:
            full_blob = row['full_intent'] if 'full_intent' in columns else None
            exact = _decode_intent(intent_codec, value, full_blob)
            if isinstance(exact, str) and isinstance(value, str):
                _assert_intent_projection(db, exact, value)
            return exact
        return value
    return value


def _digest_value(digest, value):
    if value is None:
        digest.update(b'N')
    elif isinstance(value, bytes):
        digest.update(b'B')
        digest.update(len(value).to_bytes(8, 'big'))
        digest.update(value)
    elif isinstance(value, str):
        raw = value.encode('utf-8', 'surrogatepass')
        digest.update(b'T')
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)
    elif isinstance(value, int):
        raw = str(value).encode('ascii')
        digest.update(b'I')
        digest.update(len(raw).to_bytes(4, 'big'))
        digest.update(raw)
    elif isinstance(value, float):
        digest.update(b'F' + struct.pack('>d', value))
    else:
        raw = repr(value).encode('utf-8', 'backslashreplace')
        digest.update(b'R')
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)


def _filtered_rows(db, table, columns, is_target, intent_codec, normalize_schema):
    # VACUUM may renumber implicit rowids. Declared keys also cover WITHOUT ROWID evidence tables.
    info = db.execute('PRAGMA table_info("%s")' % table).fetchall()
    keys = [row['name'] for row in sorted(info, key=lambda row: row['pk']) if row['pk']]
    if table == 'sqlite_sequence':
        keys = ['name']
    if not keys:
        raise MigrationError('table %s has no declared stable verification key' % table)
    quoted_keys = ','.join('"' + key.replace('"', '""') + '"' for key in keys)
    try:
        query = 'SELECT * FROM "%s" ORDER BY %s' % (table, quoted_keys)
        cursor = db.execute(query)
    except sqlite3.Error as exc:
        raise MigrationError('table %s cannot be verified by declared key: %s' % (table, exc)) from exc
    visible_columns = [column for column in columns
                       if not (table == 'ea_experiment' and column == 'full_intent')]
    try:
        for row in cursor:
            if table == 'ea_meta' and row['key'] == CHECKPOINT_KEY:
                continue
            values = []
            for column in visible_columns:
                value = row[column]
                if table == 'ea_meta' and column == 'value' and row['key'] == 'schema_version' \
                        and normalize_schema:
                    value = '__schema_version_v5__'
                if table in PAYLOAD_COLUMNS and column in PAYLOAD_COLUMNS[table]:
                    if isinstance(value, bytes) and payload_codec.has_codec_magic(value):
                        value = payload_codec.decode_text(value)
                elif table == 'ea_experiment' and column == 'intent_json':
                    if is_target:
                        full_blob = row['full_intent'] if 'full_intent' in row.keys() else None
                        value = _decode_intent(intent_codec, value, full_blob)
                        if isinstance(value, str) and isinstance(row['intent_json'], str):
                            _assert_intent_projection(db, value, row['intent_json'])
                values.append(value)
            yield tuple(row[key] for key in keys), tuple(values)
    finally:
        try:
            cursor.close()
        except sqlite3.ProgrammingError:
            pass


def _compare_databases(left_db, right_db, progress, *, decode_storage, allow_version_change):
    _assert_schema_compatible(left_db, right_db, allow_version_change=allow_version_change)
    if not _has_table(left_db, 'ea_experiment') or not _has_table(right_db, 'ea_experiment'):
        raise MigrationError('both databases must contain ea_experiment')
    intent_codec = _intent_codec()
    tables = [row[0] for row in left_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') "
        'ORDER BY name')]
    digest = hashlib.sha256()
    table_counts = {}
    total_tables = len(tables)
    checked = 0
    progress.update(stage='logical verification', completed=0, total=None,
                    rowsPerSecond=None, etaSeconds=None,
                    detail='checking every table, row, column and preserved SQLite value; '
                    'full row total is learned from this streaming pass')
    for table_index, table in enumerate(tables):
        left_cols = _columns(left_db, table)
        right_cols = _columns(right_db, table)
        left_compare_cols = [name for name in left_cols
                             if not (table == 'ea_experiment' and name == 'full_intent')]
        right_compare_cols = [name for name in right_cols
                              if not (table == 'ea_experiment' and name == 'full_intent')]
        if left_compare_cols != right_compare_cols:
            raise MigrationError('logical columns differ in %s: source=%r target=%r'
                                 % (table, left_compare_cols, right_compare_cols))
        left_iter = iter(_filtered_rows(left_db, table, left_cols,
                                        'full_intent' in left_cols, intent_codec,
                                        normalize_schema=allow_version_change))
        right_iter = iter(_filtered_rows(right_db, table, right_cols,
                                         'full_intent' in right_cols, intent_codec,
                                         normalize_schema=allow_version_change))
        count = 0
        try:
            while True:
                left_row = next(left_iter, None)
                right_row = next(right_iter, None)
                if left_row is None or right_row is None:
                    if left_row != right_row:
                        raise MigrationError('row count differs in %s after %d matching rows'
                                             % (table, count))
                    break
                if left_row[0] != right_row[0]:
                    raise MigrationError('key differs in %s at source=%r target=%r'
                                         % (table, left_row[0], right_row[0]))
                if len(left_row[1]) != len(right_row[1]) or any(
                        not _typed_equal(a, b) for a, b in zip(left_row[1], right_row[1])):
                    raise MigrationError('logical row differs in %s rowid=%s' % (table, left_row[0]))
                digest.update(table.encode('utf-8') + b'\0')
                for key_value in left_row[0]:
                    _digest_value(digest, key_value)
                for value in left_row[1]:
                    _digest_value(digest, value)
                count += 1
                checked += 1
                if checked % 500 == 0:
                    progress.update(stage='logical verification', completed=checked, total=None,
                                    rowsPerSecond=None, etaSeconds=None,
                                    detail='table %d/%d: %s' % (table_index + 1, total_tables, table))
        finally:
            left_iter.close()
            right_iter.close()
        table_counts[table] = count
    progress.update(stage='logical verification', completed=checked, total=checked,
                    detail='all logical rows compared')
    return {'logicalSha256': digest.hexdigest(), 'tableRows': table_counts,
            'rowsCompared': checked, 'tableCount': len(tables)}


def _read_checkpoint(db):
    row = db.execute('SELECT value FROM ea_meta WHERE key=?', (CHECKPOINT_KEY,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        raise MigrationError('staging database has a malformed migration checkpoint') from exc
    if value.get('format') != 1 or value.get('targetSchema') != TARGET_SCHEMA_VERSION:
        raise MigrationError('staging database checkpoint belongs to an unsupported migration')
    return value


def _write_checkpoint(db, checkpoint):
    db.execute('INSERT INTO ea_meta(key,value) VALUES(?,?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (CHECKPOINT_KEY, json.dumps(checkpoint, sort_keys=True, separators=(',', ':'))))


def _validate_paths(source, stage, output, status, log, report, pilot_rows):
    raw_stage = Path(stage).expanduser()
    raw_output = Path(output).expanduser()
    raw_database_paths = [Path(source).expanduser(), Path(stage).expanduser(),
                          Path(output).expanduser(), Path(status).expanduser(),
                          Path(log).expanduser(), Path(report).expanduser(),
                          Path(str(raw_stage) + '.backup-in-progress'),
                          Path(str(raw_output) + '.vacuum-in-progress')]
    if any(path.is_symlink() for path in raw_database_paths):
        raise MigrationError('database, status, log and report paths may not be symbolic links')
    paths = {'source': _norm_path(source), 'stage': _norm_path(stage),
             'output': _norm_path(output), 'status': _norm_path(status),
             'log': _norm_path(log), 'report': _norm_path(report)}
    if len({str(paths[name]).casefold() for name in ('source', 'stage', 'output')}) != 3:
        raise MigrationError('source, stage and compact output must be three different paths')
    db_paths = {str(paths[name]).casefold() for name in ('source', 'stage', 'output')}
    if any(str(paths[name]).casefold() in db_paths for name in ('status', 'log', 'report')):
        raise MigrationError('status, log and report paths must be separate from database files')
    if len({str(paths[name]).casefold() for name in ('status', 'log', 'report')}) != 3:
        raise MigrationError('status, log and report paths must be distinct')
    reserved = {str(paths['source']).casefold(), str(paths['stage']).casefold(),
                str(paths['output']).casefold(), str(_norm_path(str(paths['stage']) +
                                                                  '.backup-in-progress')).casefold(),
                str(_norm_path(str(paths['output']) + '.vacuum-in-progress')).casefold()}
    if len(reserved) != 5:
        raise MigrationError('a database path collides with a backup/VACUUM recovery path')
    checked_paths = [paths[name] for name in ('source', 'stage', 'output', 'status', 'log', 'report')]
    checked_paths.extend((_norm_path(str(paths['stage']) + '.backup-in-progress'),
                          _norm_path(str(paths['output']) + '.vacuum-in-progress')))
    for index, left in enumerate(checked_paths):
        if not left.exists():
            continue
        for right in checked_paths[index + 1:]:
            if right.exists() and os.path.samefile(left, right):
                raise MigrationError('two migration paths refer to the same filesystem file')
    if any(str(paths[name]).casefold() in reserved for name in ('status', 'log', 'report')):
        raise MigrationError('status/log/report paths collide with a database or recovery file')
    if str(_norm_path(str(paths['stage']) + '.backup-in-progress')).casefold() in {
            str(paths['source']).casefold(), str(paths['stage']).casefold(),
            str(paths['output']).casefold()}:
        raise MigrationError('stage backup recovery path collides with a database path')
    if str(_norm_path(str(paths['output']) + '.vacuum-in-progress')).casefold() in {
            str(paths['source']).casefold(), str(paths['stage']).casefold(),
            str(paths['output']).casefold()}:
        raise MigrationError('VACUUM recovery path collides with a database path')
    if not paths['source'].is_file():
        raise MigrationError('source database does not exist: %s' % paths['source'])
    if not paths['stage'].parent.is_dir() or not paths['output'].parent.is_dir():
        raise MigrationError('stage/output parent directories must already exist')
    if paths['stage'].exists() and paths['stage'].is_dir():
        raise MigrationError('stage path is a directory')
    if paths['output'].exists() and paths['output'].is_dir():
        raise MigrationError('compact output path is a directory')
    if pilot_rows is not None and not paths['stage'].is_file():
        raise MigrationError('--pilot-rows requires an existing staged database copy')
    return paths


def _data_version_unchanged(source_db, initial):
    now = int(source_db.execute('PRAGMA data_version').fetchone()[0])
    if now != initial:
        raise MigrationError('source database changed during migration; stage/output are preserved')


def _backup_to_stage(source_db, source_path, stage_path, progress, source_data_version):
    stage = Path(stage_path)
    temporary = Path(str(stage) + '.backup-in-progress')
    if temporary.is_symlink():
        raise MigrationError('backup recovery path is a symbolic link; it was not followed')
    progress.update(stage='consistent backup', state='running', completed=0, total=None,
                    rowsPerSecond=None, etaSeconds=None,
                    detail='SQLite backup to a separate temporary stage file')
    if stage.exists():
        return None
    if temporary.exists():
        # A prior process may have completed SQLite backup and stopped before its atomic rename.
        # Reuse it only after a complete integrity and exact source comparison.
        candidate = sqlite3.connect(str(temporary), timeout=30.0, isolation_level=None)
        candidate.row_factory = sqlite3.Row
        try:
            _integrity_check(candidate, progress, 'backup-stage integrity check')
            baseline = _compare_databases(source_db, candidate, progress, decode_storage=False,
                                          allow_version_change=True)
            candidate.close()
            os.rename(temporary, stage)
            return baseline
        except BaseException:
            candidate.close()
            raise MigrationError('existing backup-in-progress file did not verify; it was preserved: '
                                 '%s' % temporary)
    destination = sqlite3.connect(str(temporary), timeout=30.0, isolation_level=None)
    total_pages = int(source_db.execute('PRAGMA page_count').fetchone()[0])
    copy_started = time.monotonic()

    def callback(status, remaining, total):
        completed = max(0, total - remaining)
        elapsed = time.monotonic() - copy_started
        rate = completed / elapsed if elapsed > 0 and completed > 0 else None
        eta = ((remaining / rate) if rate else None)
        progress.update(stage='consistent backup', state='running', completed=completed,
                        total=total or total_pages or None, rowsPerSecond=rate, etaSeconds=eta,
                        detail='SQLite pages copied: %d remaining' % remaining)

    try:
        source_db.backup(destination, pages=256, progress=callback, sleep=0.05)
        destination.commit()
        destination.close()
        candidate = sqlite3.connect(str(temporary), timeout=30.0, isolation_level=None)
        candidate.row_factory = sqlite3.Row
        try:
            _integrity_check(candidate, progress, 'backup-stage integrity check')
            baseline = _compare_databases(source_db, candidate, progress, decode_storage=False,
                                          allow_version_change=True)
        finally:
            candidate.close()
        _data_version_unchanged(source_db, source_data_version)
        if stage.exists():
            raise MigrationError('stage path appeared during backup; temporary backup preserved')
        os.rename(temporary, stage)
        return baseline
    except BaseException:
        try:
            destination.close()
        except sqlite3.Error:
            pass
        raise


def _payload_transform(value):
    if value is None:
        return value
    if isinstance(value, bytes):
        # Check any reserved KAP frame strictly; legacy direct-SQL BLOBs retain their exact bytes.
        if payload_codec.has_codec_magic(value):
            payload_codec.decode_text(value)
        return value
    if not isinstance(value, str):
        return value
    encoded = payload_codec.encode_text(value)
    decoded = payload_codec.decode_text(encoded)
    if decoded != value:
        raise MigrationError('result/outcome codec failed its exact raw-text round-trip')
    return encoded


def _intent_transform(db_codec, raw):
    if raw is None or not isinstance(raw, str):
        return raw, None
    metadata, full_blob = db_codec.encode_intent(raw)
    if not isinstance(metadata, str):
        raise MigrationError('intent encoder must return SQLite TEXT metadata')
    if full_blob is not None and not isinstance(full_blob, bytes):
        raise MigrationError('intent encoder full payload must be BLOB bytes or None')
    exact = db_codec.decode_intent(metadata, full_blob)
    if exact != raw:
        raise MigrationError('intent encoder failed its exact lexical round-trip')
    return metadata, full_blob


def _get_phase_rows(db, table, after_rowid, limit, byte_limit):
    predicate = '' if after_rowid is None else ' WHERE rowid>?'
    sql = 'SELECT rowid AS __migration_rowid__, * FROM "%s"%s ORDER BY rowid' % (table, predicate)
    cursor = db.execute(sql, () if after_rowid is None else (after_rowid,))
    rows = []
    byte_count = 0
    try:
        while len(rows) < limit:
            row = cursor.fetchone()
            if row is None:
                break
            size = _row_size(tuple(row))
            if rows and byte_count + size > byte_limit:
                break
            rows.append(row)
            byte_count += size
            if byte_count >= byte_limit:
                break
    finally:
        cursor.close()
    return rows, byte_count


def _transform_row(table, row, db_codec, executor):
    names = row.keys()
    original = dict(row)
    if table in PAYLOAD_COLUMNS:
        columns = PAYLOAD_COLUMNS[table]
        jobs = [(column, original[column]) for column in columns]
        results = [_payload_transform(value) for _, value in jobs]
        for (column, _), converted in zip(jobs, results):
            original[column] = converted
    else:
        original_intent = original['intent_json']
        existing_blob = original.get('full_intent')
        if existing_blob is not None:
            _decode_intent(db_codec, original_intent, existing_blob)
            metadata, blob = original_intent, existing_blob
        elif isinstance(original_intent, str):
            metadata, blob = _intent_transform(db_codec, original_intent)
        else:
            metadata, blob = original_intent, None
        original['intent_json'] = metadata
        original['full_intent'] = blob
    return original


def _raw_target_value(table, column, value, row, codec):
    if table in PAYLOAD_COLUMNS and column in PAYLOAD_COLUMNS[table]:
        if isinstance(value, bytes) and payload_codec.has_codec_magic(value):
            return payload_codec.decode_text(value)
        return value
    if table == 'ea_experiment' and column == 'intent_json':
        exact = _decode_intent(codec, value, row.get('full_intent'))
        if isinstance(exact, str) and isinstance(value, str):
            # The metadata column is deliberately small; every SQL-visible projection must remain
            # equivalent to SQLite's first-occurrence json_extract behavior.
            return exact
        return exact
    return value


def _process_batch(db, source_db, table, rows, progress, checkpoint, executor, db_codec,
                   batch_number, batch_hook=None, progress_base=0, progress_total=None):
    prepared = list(executor.map(lambda row: (row, _transform_row(table, row, db_codec, None)), rows))
    conn_cols = _columns(db, table)
    updates = []
    # Encoding/decoding runs outside the writer transaction.  The immediate transaction then
    # checks the exact original row before applying any cell updates.
    db.execute('BEGIN IMMEDIATE')
    try:
        for row, transformed in prepared:
            rowid = row['__migration_rowid__']
            current = db.execute('SELECT rowid AS __migration_rowid__, * FROM "%s" '
                                 'WHERE rowid=?' % table, (rowid,)).fetchone()
            if current is None or len(current) != len(row):
                raise MigrationError('stage row changed before its batch write: %s rowid=%s'
                                     % (table, rowid))
            for name in row.keys():
                if not _typed_equal(row[name], current[name]):
                    raise MigrationError('stage row changed before its batch write: %s.%s rowid=%s'
                                         % (table, name, rowid))
            expected = dict((name, row[name]) for name in row.keys())
            expected.update(transformed)
            if table == 'ea_experiment':
                changed = (not _typed_equal(expected['intent_json'], row['intent_json']) or
                           not _typed_equal(expected['full_intent'], row['full_intent']))
                if changed:
                    cursor = db.execute('UPDATE ea_experiment SET intent_json=?, full_intent=? '
                                        'WHERE rowid=?',
                                        (expected['intent_json'], expected['full_intent'], rowid))
                    if cursor.rowcount != 1:
                        raise MigrationError('intent update did not affect exactly one row')
            else:
                changed_columns = [name for name in PAYLOAD_COLUMNS[table]
                                   if not _typed_equal(expected[name], row[name])]
                if changed_columns:
                    set_clause = ','.join('"%s"=?' % name for name in changed_columns)
                    values = [expected[name] for name in changed_columns] + [rowid]
                    cursor = db.execute('UPDATE "%s" SET %s WHERE rowid=?' %
                                        (table, set_clause), values)
                    if cursor.rowcount != 1:
                        raise MigrationError('%s update did not affect exactly one row' % table)
            readback = db.execute('SELECT rowid AS __migration_rowid__, * FROM "%s" '
                                  'WHERE rowid=?' % table, (rowid,)).fetchone()
            if readback is None:
                raise MigrationError('stage row vanished during its batch: %s rowid=%s'
                                     % (table, rowid))
            for name in conn_cols:
                want = expected.get(name, row[name] if name in row.keys() else None)
                got = readback[name]
                if name == 'intent_json' and table == 'ea_experiment':
                    got_raw = _decode_intent(db_codec, got, readback['full_intent'])
                    want_raw = _decode_intent(db_codec, want,
                                              expected.get('full_intent', row['full_intent']))
                    if not _typed_equal(got_raw, want_raw):
                        raise MigrationError('intent exact-raw readback failed at rowid=%s' % rowid)
                    _assert_intent_projection(db, got_raw, got)
                elif name in PAYLOAD_COLUMNS.get(table, ()):
                    got_raw = _payload_transform_readback(got)
                    want_raw = _payload_transform_readback(want)
                    if not _typed_equal(got_raw, want_raw):
                        raise MigrationError('%s.%s exact-raw readback failed at rowid=%s'
                                             % (table, name, rowid))
                elif not _typed_equal(got, want):
                    raise MigrationError('%s.%s readback changed at rowid=%s'
                                         % (table, name, rowid))
        checkpoint['cursors'][table] = rows[-1]['__migration_rowid__']
        checkpoint['rowsVisited'] = int(checkpoint.get('rowsVisited', 0)) + len(rows)
        checkpoint['batchesCommitted'] = int(checkpoint.get('batchesCommitted', 0)) + 1
        _write_checkpoint(db, checkpoint)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if batch_hook is not None:
        batch_hook(batch_number, table, dict(checkpoint))
    done = int(checkpoint['rowsVisited'])
    rows_this_run = done - progress_base
    progress_done = rows_this_run if progress_total is not None else done
    total = progress_total if progress_total is not None else checkpoint.get('plannedRows')
    elapsed = max(0.001, time.monotonic() - progress.started)
    rate = rows_this_run / elapsed if rows_this_run else None
    eta = ((total - progress_done) / rate
           if rate and total is not None and total >= progress_done and elapsed >= 10
           and rows_this_run >= 10
           else None)
    progress.update(stage='convert ' + table, state='running', completed=progress_done,
                    total=total, rowsPerSecond=rate, etaSeconds=eta,
                    etaProvisional=eta is not None,
                    batchNumber=batch_number, batchRows=len(rows), batchBytes=sum(
                        _row_size(tuple(row)) for row in rows), lastRowid=rows[-1]['__migration_rowid__'])


def _payload_transform_readback(value):
    if value is None:
        return None
    if isinstance(value, bytes) and payload_codec.has_codec_magic(value):
        return payload_codec.decode_text(value)
    return value


def _prepare_checkpoint(source_db, stage_db, source_path, source_version, progress,
                        source_data_version, *, pilot_mode=False, baseline_hint=None):
    checkpoint = _read_checkpoint(stage_db)
    source_identity = str(Path(source_path).resolve())
    if checkpoint is not None:
        if checkpoint.get('sourcePath') != source_identity:
            raise MigrationError('staging checkpoint belongs to a different source path')
        if checkpoint.get('sourceSchemaVersion') != source_version:
            raise MigrationError('source schema version changed since this stage was created')
        if not pilot_mode:
            current = _compare_databases(source_db, stage_db, progress, decode_storage=True,
                                         allow_version_change=True)
            known_hash = checkpoint.get('baselineLogicalSha256')
            if known_hash is not None and current['logicalSha256'] != known_hash:
                raise MigrationError('resumable stage no longer matches its source snapshot')
            observed_rows = sum(current['tableRows'].get(table, 0) for table in TABLE_PHASES)
            if checkpoint.get('plannedRows') is not None and \
                    int(checkpoint['plannedRows']) != observed_rows:
                raise MigrationError('resumable stage row counts no longer match the source')
            checkpoint['baselineLogicalSha256'] = current['logicalSha256']
            checkpoint['plannedRows'] = observed_rows
            stage_db.execute('BEGIN IMMEDIATE')
            try:
                _write_checkpoint(stage_db, checkpoint)
                stage_db.commit()
            except BaseException:
                stage_db.rollback()
                raise
        return checkpoint
    stage_version = _schema_version(stage_db)
    if source_version not in INPUT_SCHEMA_VERSIONS:
        raise MigrationError('source schema v%d is not an approved v3/v4 input' % source_version)
    if stage_version not in INPUT_SCHEMA_VERSIONS + (TARGET_SCHEMA_VERSION,):
        raise MigrationError('stage schema v%d cannot be resumed by this tool' % stage_version)
    if source_version != stage_version and stage_version != TARGET_SCHEMA_VERSION:
        raise MigrationError('stage schema version differs from source before activation')
    _assert_schema_compatible(source_db, stage_db, allow_version_change=True)
    baseline = baseline_hint
    if baseline is None and not pilot_mode:
        baseline = _compare_databases(source_db, stage_db, progress, decode_storage=True,
                                      allow_version_change=True)
    if stage_version in INPUT_SCHEMA_VERSIONS:
        if stage_version != source_version:
            raise MigrationError('source and staged input schema markers differ')
        stage_db.execute('BEGIN IMMEDIATE')
        try:
            initialized = store.initialize(stage_db)
            if int(initialized) != source_version:
                raise MigrationError('staged initialize changed schema marker to v%d' % initialized)
            activated = store.activate_schema_v5(stage_db)
            if int(activated) != TARGET_SCHEMA_VERSION:
                raise MigrationError('store.activate_schema_v5 returned schema v%d' % activated)
            if 'full_intent' not in _columns(stage_db, 'ea_experiment'):
                raise MigrationError('schema v5 activation did not add ea_experiment.full_intent')
            stage_db.commit()
        except BaseException:
            stage_db.rollback()
            raise
    elif 'full_intent' not in _columns(stage_db, 'ea_experiment'):
        raise MigrationError('schema v5 stage is missing ea_experiment.full_intent')
    cursors = {table: None for table in TABLE_PHASES}
    checkpoint = {'format': 1, 'targetSchema': TARGET_SCHEMA_VERSION,
                  'sourcePath': source_identity, 'sourceSchemaVersion': source_version,
                  'sourceDataVersion': source_data_version,
                  'baselineLogicalSha256': baseline['logicalSha256'] if baseline else None,
                  'cursors': cursors,
                  'phaseIndex': 0, 'rowsVisited': 0, 'batchesCommitted': 0,
                  'plannedRows': (sum(baseline['tableRows'].get(table, 0)
                                      for table in TABLE_PHASES) if baseline else None),
                  'createdAt': _utc_now(), 'verified': False}
    stage_db.execute('BEGIN IMMEDIATE')
    try:
        _write_checkpoint(stage_db, checkpoint)
        stage_db.commit()
    except BaseException:
        stage_db.rollback()
        raise
    progress.update(stage='checkpoint ready', completed=0, total=checkpoint['plannedRows'],
                    detail=('stage/source logical baseline %s' % baseline['logicalSha256']
                            if baseline else 'bounded pilot skipped full-database baseline scan'))
    return checkpoint


def _convert_rows(stage_db, checkpoint, progress, batch_rows, batch_bytes, encoder_threads,
                  pilot_rows=None, batch_hook=None):
    db_codec = _intent_codec()
    phase = int(checkpoint.get('phaseIndex', 0))
    batch_number = int(checkpoint.get('batchesCommitted', 0))
    rows_seen_this_run = 0
    progress_base = int(checkpoint.get('rowsVisited', 0))
    run_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=encoder_threads, thread_name_prefix='intent-codec') as pool:
        while phase < len(TABLE_PHASES):
            table = TABLE_PHASES[phase]
            cursor = checkpoint['cursors'].get(table)
            room = batch_rows
            if pilot_rows is not None:
                remaining = pilot_rows - rows_seen_this_run
                if remaining <= 0:
                    break
                room = min(room, remaining)
            rows, bytes_read = _get_phase_rows(stage_db, table, cursor, room, batch_bytes)
            if not rows:
                phase += 1
                checkpoint['phaseIndex'] = phase
                stage_db.execute('BEGIN IMMEDIATE')
                try:
                    _write_checkpoint(stage_db, checkpoint)
                    stage_db.commit()
                except BaseException:
                    stage_db.rollback()
                    raise
                continue
            batch_number += 1
            _process_batch(stage_db, None, table, rows, progress, checkpoint, pool, db_codec,
                           batch_number, batch_hook=batch_hook, progress_base=progress_base,
                           progress_total=pilot_rows)
            rows_seen_this_run += len(rows)
        checkpoint['phaseIndex'] = phase
    checkpoint['conversionElapsedThisRunSeconds'] = round(time.monotonic() - run_started, 3)
    checkpoint['pilotStopped'] = pilot_rows is not None
    stage_db.execute('BEGIN IMMEDIATE')
    try:
        _write_checkpoint(stage_db, checkpoint)
        stage_db.commit()
    except BaseException:
        stage_db.rollback()
        raise
    return checkpoint


def _refresh_checkpoint_plan(checkpoint):
    # Full runs get exact totals from the mandatory streaming source/stage comparison.  Pilot runs
    # deliberately avoid an unbounded COUNT(*) scan and display the requested row budget instead.
    if checkpoint.get('plannedRows') is None:
        raise MigrationError('full migration has no verified source row-count plan')


def _vacuum_into(stage_db, vacuum_path, progress):
    vacuum_path = Path(vacuum_path)
    if vacuum_path.exists():
        raise MigrationError('VACUUM recovery path already exists; it must be validated before use')
    if vacuum_path.is_symlink():
        raise MigrationError('VACUUM recovery path is a symbolic link; it was not followed')
    operations = [0]
    last_update = [0.0]

    def tick():
        operations[0] += 1
        now = time.monotonic()
        if now - last_update[0] >= 2.0:
            last_update[0] = now
            progress.update(stage='compact output (VACUUM INTO)', completed=operations[0], total=None,
                            rowsPerSecond=None, etaSeconds=None,
                            detail='SQLite VM callbacks: %d; total work is not available from SQLite'
                            % operations[0])
        return 0

    progress.update(stage='compact output (VACUUM INTO)', completed=0, total=None,
                    rowsPerSecond=None, etaSeconds=None,
                    detail='SQLite does not expose total VACUUM work')
    stage_db.set_progress_handler(tick, 100000)
    try:
        stage_db.execute('VACUUM INTO ?', (str(vacuum_path),))
    finally:
        stage_db.set_progress_handler(None, 0)
    return True


def _verify_compact_candidate(path, source_db, stage_db, source_logical, progress):
    candidate = _connect_readonly(path)
    try:
        integrity = _integrity_check(candidate, progress, 'compact candidate integrity check')
        stage_logical = _compare_databases(stage_db, candidate, progress,
                                           decode_storage=True, allow_version_change=False)
        # The complete stage/compact comparison plus the source/stage proof is transitive.
        # Reuse the proven source digest instead of reading all history for a third time.
        source_output_logical = dict(source_logical, proof='source=stage and stage=compact')
        if source_output_logical['logicalSha256'] != source_logical['logicalSha256']:
            raise MigrationError('compact candidate logical hash differs from the verified source')
        return {'integrity': integrity, 'stageLogical': stage_logical,
                'sourceLogical': source_output_logical}
    finally:
        candidate.close()


def _database_bytes(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _wait_for_processes(pids, progress):
    if os.name != 'nt':
        raise MigrationError('--wait-for-pid currently requires Windows')
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('exe', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            parents[int(entry.pid)] = int(entry.parent)
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    targets = set(pids)
    while True:
        expanded = targets | {pid for pid, parent in parents.items() if parent in targets}
        if expanded == targets:
            break
        targets = expanded
    handles = {}
    try:
        for pid in targets:
            if pid not in parents:
                continue
            handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:  # Process exited between enumeration and OpenProcess.
                    continue
                raise ctypes.WinError(error)
            handles[pid] = handle
        while handles:
            progress.update(stage='waiting for optimizer to close normally', completed=0, total=None,
                            etaSeconds=None, detail='Waiting for %d captured processes; source untouched.' % len(handles))
            for pid, handle in list(handles.items()):
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if code.value != 259:
                    kernel.CloseHandle(handle)
                    del handles[pid]
            if handles:
                time.sleep(0.5)
    finally:
        for handle in handles.values():
            kernel.CloseHandle(handle)


def run_migration(source, stage, output, *, batch_rows=DEFAULT_BATCH_ROWS,
                  batch_bytes=DEFAULT_BATCH_BYTES, encoder_threads=DEFAULT_ENCODER_THREADS,
                  status_path=None, log_path=None, report_path=None, pilot_rows=None,
                  batch_hook=None, progress=None, wait_for_pids=()):
    """Run or safely resume conversion.  The source connection is opened read-only throughout."""
    stage_guess = _norm_path(stage)
    output_guess = _norm_path(output)
    status_path = status_path or Path(str(stage_guess) + '.migration-status.json')
    log_path = log_path or Path(str(stage_guess) + '.migration-log.jsonl')
    report_path = report_path or (Path(str(stage_guess) + '.pilot-report.json') if pilot_rows
                                  else Path(str(output_guess) + '.migration-report.json'))
    paths = _validate_paths(source, stage, output, status_path, log_path, report_path, pilot_rows)
    if batch_rows < 1 or batch_bytes < 1024 or encoder_threads < 1:
        raise MigrationError('batch rows, batch bytes and encoder threads must be positive')
    if pilot_rows is not None and pilot_rows < 1:
        raise MigrationError('--pilot-rows must be positive')
    progress = progress or Progress(paths['status'], paths['log'], paths['report'])
    started = time.monotonic()
    report = {'format': 1, 'tool': 'migrate_strategy_payload_storage.py',
              'source': str(paths['source']), 'stage': str(paths['stage']),
              'output': str(paths['output']), 'startedAt': _utc_now(),
              'pilotRowsLimit': pilot_rows, 'batchRows': batch_rows,
              'batchBytes': batch_bytes, 'encoderThreads': encoder_threads,
              'includesSourceBackup': pilot_rows is None,
              'timings': {}, 'warnings': []}
    resource = _resource_snapshot({'source': paths['source'], 'stage': paths['stage'],
                                   'output': paths['output']})
    report['resourcesBefore'] = resource
    warning = _set_background_priority()
    if warning:
        report['warnings'].append(warning)
    progress.update(stage='preflight', state='running', completed=0, total=None,
                    rowsPerSecond=None, etaSeconds=None, resources=resource,
                    warning=warning, detail='source is read-only; only stage is writable')
    if resource['availableMemoryBytes'] is not None and resource['availableMemoryBytes'] < 4 * 1024**3:
        report['warnings'].append('available RAM is below the 4 GiB foreground headroom target')
        progress.update(warning=report['warnings'][-1])
    source_db = None
    stage_db = None
    try:
        if wait_for_pids:
            # Capture process identities and descendants before the operator closes the app normally.
            # No signals, termination, or application controls are sent by the migration tool.
            _wait_for_processes(wait_for_pids, progress)
        source_db = _connect_readonly(paths['source'])
        source_version = _schema_version(source_db)
        if source_version not in INPUT_SCHEMA_VERSIONS:
            raise MigrationError('source schema v%d is unsupported; only v3/v4 inputs are accepted'
                                 % source_version)
        for table in TABLE_PHASES:
            if not _has_table(source_db, table):
                raise MigrationError('source database is missing required table %s' % table)
        source_data_version = int(source_db.execute('PRAGMA data_version').fetchone()[0])
        report['sourceSchemaVersion'] = source_version
        report['sourceBytes'] = _database_bytes(paths['source'])
        if pilot_rows is None:
            source_integrity_started = time.monotonic()
            report['sourceIntegrity'] = _integrity_check(source_db, progress,
                                                         'source integrity check')
            report['timings']['sourceIntegritySeconds'] = round(
                time.monotonic() - source_integrity_started, 3)
        else:
            report['sourceIntegrity'] = {'result': 'skipped_for_bounded_pilot',
                                         'reason': 'pilot avoids an unbounded full-database scan'}
        _data_version_unchanged(source_db, source_data_version)
        backup_baseline = None
        if not paths['stage'].exists():
            if pilot_rows is not None:
                raise MigrationError('--pilot-rows only operates on an existing staged copy')
            backup_baseline = _backup_to_stage(source_db, paths['source'], paths['stage'],
                                               progress, source_data_version)
        backup_done = time.monotonic()
        report['timings']['backupAndBaselineSeconds'] = round(backup_done - started, 3)
        stage_db = _open_stage(paths['stage'])
        stage_version = _schema_version(stage_db)
        if stage_version not in INPUT_SCHEMA_VERSIONS + (TARGET_SCHEMA_VERSION,):
            raise MigrationError('stage schema v%d is unsupported' % stage_version)
        if pilot_rows is None and backup_baseline is None:
            _integrity_check(stage_db, progress, 'stage integrity check')
        checkpoint = _prepare_checkpoint(source_db, stage_db, paths['source'], source_version,
                                         progress, source_data_version,
                                         pilot_mode=pilot_rows is not None,
                                         baseline_hint=backup_baseline)
        if pilot_rows is None:
            _refresh_checkpoint_plan(checkpoint)
        planned = (int(checkpoint['plannedRows']) if checkpoint.get('plannedRows') is not None
                   else None)
        if pilot_rows is not None:
            progress.update(stage='pilot conversion', state='running',
                            completed=0, total=pilot_rows,
                            rowsPerSecond=None, etaSeconds=None,
                            detail='bounded staged-copy pilot; full integrity and source equality scans skipped')
        else:
            progress.update(stage='conversion', state='running',
                            completed=int(checkpoint['rowsVisited']), total=planned,
                            rowsPerSecond=None, etaSeconds=None,
                            detail='single SQLite writer; %d encoder thread(s)' % encoder_threads)
        conversion_started = time.monotonic()
        rows_before_conversion = int(checkpoint['rowsVisited'])
        batches_before_conversion = int(checkpoint['batchesCommitted'])
        checkpoint = _convert_rows(stage_db, checkpoint, progress, batch_rows, batch_bytes,
                                   encoder_threads, pilot_rows=pilot_rows,
                                   batch_hook=batch_hook)
        report['timings']['conversionSecondsThisRun'] = round(
            time.monotonic() - conversion_started, 3)
        report['rowsVisitedTotal'] = int(checkpoint['rowsVisited'])
        report['batchesCommittedTotal'] = int(checkpoint['batchesCommitted'])
        report['rowsProcessedThisRun'] = int(checkpoint['rowsVisited']) - rows_before_conversion
        report['batchesCommittedThisRun'] = (int(checkpoint['batchesCommitted']) -
                                             batches_before_conversion)
        report['stageBytesBeforeVacuum'] = _database_bytes(paths['stage'])
        if pilot_rows is not None:
            checkpoint['pilotStopped'] = True
            stage_db.execute('BEGIN IMMEDIATE')
            try:
                _write_checkpoint(stage_db, checkpoint)
                stage_db.commit()
            except BaseException:
                stage_db.rollback()
                raise
            report['outcome'] = 'pilot_paused_with_transactional_checkpoint'
            report['stageSchemaVersion'] = _schema_version(stage_db)
            report['stageCheckpoint'] = {key: checkpoint.get(key) for key in
                                         ('phaseIndex', 'cursors', 'rowsVisited', 'plannedRows',
                                          'batchesCommitted')}
            report['endToEndElapsedSeconds'] = round(time.monotonic() - started, 3)
            report['rowsPerEndToEndSecond'] = (
                report['rowsProcessedThisRun'] / report['endToEndElapsedSeconds']
                if report['endToEndElapsedSeconds'] > 0 else None)
            _atomic_json(paths['report'], report)
            progress.update(stage='pilot complete; staged conversion is resumable', state='complete',
                            completed=min(pilot_rows, report['rowsProcessedThisRun']), total=pilot_rows,
                            reportPath=str(paths['report']), outputLocation=str(paths['stage']),
                            detail='pilot left the stage checkpoint in place; no compact output made')
            return report
        if int(checkpoint.get('phaseIndex', 0)) != len(TABLE_PHASES):
            raise MigrationError('conversion stopped before all staged table phases completed')
        _data_version_unchanged(source_db, source_data_version)
        verification_started = time.monotonic()
        progress.update(stage='full logical verification', completed=0, total=None,
                        rowsPerSecond=None, etaSeconds=None,
                        detail='checking every table, row, column and preserved SQLite value')
        logical = _compare_databases(source_db, stage_db, progress, decode_storage=True,
                                     allow_version_change=True)
        _data_version_unchanged(source_db, source_data_version)
        checkpoint['verified'] = True
        checkpoint['verifiedLogicalSha256'] = logical['logicalSha256']
        with stage_db:
            _write_checkpoint(stage_db, checkpoint)
        report['logicalVerification'] = logical
        report['timings']['logicalVerificationSeconds'] = round(
            time.monotonic() - verification_started, 3)
        if _schema_version(stage_db) != TARGET_SCHEMA_VERSION:
            raise MigrationError('verified stage schema marker is not v5')
        stage_integrity = _integrity_check(stage_db, progress, 'converted stage integrity check')
        report['stageIntegrity'] = stage_integrity
        # Retain the completed transactional cursor in the stage through VACUUM and output proof.
        vacuum_path = Path(str(paths['output']) + '.vacuum-in-progress')
        if vacuum_path.is_symlink():
            raise MigrationError('VACUUM recovery file is a symbolic link; it was not followed')
        candidate_path = paths['output'] if paths['output'].exists() else vacuum_path
        if paths['output'].exists():
            report['vacuumIntoReusedExisting'] = True
            report['timings']['vacuumIntoSeconds'] = 0.0
            if vacuum_path.exists():
                report['warnings'].append('an unused VACUUM recovery file remains beside the '
                                          'verified compact output')
        elif vacuum_path.exists():
            report['vacuumIntoReusedExisting'] = True
            report['vacuumRecoveryFileReused'] = str(vacuum_path)
            report['timings']['vacuumIntoSeconds'] = 0.0
        else:
            vacuum_started = time.monotonic()
            _vacuum_into(stage_db, vacuum_path, progress)
            report['timings']['vacuumIntoSeconds'] = round(time.monotonic() - vacuum_started, 3)
            report['vacuumIntoReusedExisting'] = False
        if candidate_path == vacuum_path:
            compact_write = _open_stage(candidate_path)
            try:
                with compact_write:
                    compact_write.execute('DELETE FROM ea_meta WHERE key=?', (CHECKPOINT_KEY,))
            finally:
                compact_write.close()
        compact_proof = _verify_compact_candidate(candidate_path, source_db, stage_db, logical,
                                                  progress)
        if candidate_path != paths['output']:
            if paths['output'].exists():
                raise MigrationError('final compact output appeared during verification; the '
                                     'verified VACUUM file remains preserved')
            os.rename(candidate_path, paths['output'])
        report['outputIntegrity'] = compact_proof['integrity']
        report['compactOutputLogicalVerification'] = compact_proof['stageLogical']
        report['sourceToOutputLogicalVerification'] = compact_proof['sourceLogical']
        _data_version_unchanged(source_db, source_data_version)
        report['stageSchemaVersion'] = _schema_version(stage_db)
        output_marker_db = _connect_readonly(paths['output'])
        try:
            report['outputSchemaVersion'] = _schema_version(output_marker_db)
        finally:
            output_marker_db.close()
        report['stageBytesAfterConversion'] = _database_bytes(paths['stage'])
        report['compactOutputBytes'] = _database_bytes(paths['output'])
        report['compactOutputSavingsBytes'] = (
            report['stageBytesBeforeVacuum'] - report['compactOutputBytes']
            if report.get('stageBytesBeforeVacuum') is not None and
            report.get('compactOutputBytes') is not None else None)
        report['outcome'] = 'verified_compact_v5_copy_ready'
        report['endToEndElapsedSeconds'] = round(time.monotonic() - started, 3)
        report['rowsPerEndToEndSecond'] = (
            report['rowsProcessedThisRun'] / report['endToEndElapsedSeconds']
            if report['endToEndElapsedSeconds'] > 0 else None)
        report['completedAt'] = _utc_now()
        report['resourcesAfter'] = _resource_snapshot({'source': paths['source'],
                                                       'stage': paths['stage'],
                                                       'output': paths['output']})
        _atomic_json(paths['report'], report)
        progress.update(stage='complete', state='complete', completed=planned, total=planned,
                        reportPath=str(paths['report']), outputLocation=str(paths['output']),
                        detail='source retained; verified compact copy is ready')
        return report
    except BaseException as exc:
        try:
            progress.update(stage='failed safely', state='error', error=str(exc),
                            traceback=traceback.format_exc(),
                            detail='source, stage and any existing output were preserved')
        except BaseException:
            pass
        raise
    finally:
        if stage_db is not None:
            stage_db.close()
        if source_db is not None:
            source_db.close()


def _human_time(seconds):
    if seconds is None:
        return 'unknown'
    seconds = max(0, int(seconds))
    if seconds < 60:
        return '%ds' % seconds
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return '%dm %02ds' % (minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    return '%dh %02dm' % (hours, minutes)


def _run_with_tk(worker, status_path):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:
        raise MigrationError('Tk is required for the default progress window; use --headless only '
                             'for supervised runs: %s' % exc) from exc

    state = {'finished': False, 'hidden': False, 'error': None, 'report': None}

    def work():
        try:
            state['report'] = worker()
        except BaseException as exc:
            state['error'] = exc
        finally:
            state['finished'] = True

    root = tk.Tk()
    root.title('Strategy storage migration')
    root.geometry('520x205')
    root.minsize(480, 190)
    container = ttk.Frame(root, padding=14)
    container.pack(fill='both', expand=True)
    title = ttk.Label(container, text='Preparing migration…', font=('Segoe UI', 11, 'bold'))
    title.pack(anchor='w')
    detail = ttk.Label(container, text='Status: %s' % status_path, wraplength=480)
    detail.pack(anchor='w', pady=(8, 5))
    bar = ttk.Progressbar(container, orient='horizontal', mode='determinate', maximum=100)
    bar.pack(fill='x', pady=5)
    stats = ttk.Label(container, text='Completed: 0 / unknown    Elapsed: 0s    ETA: unknown', wraplength=480)
    stats.pack(anchor='w')
    note = ttk.Label(container, text='Closing this window hides the monitor; the migration continues.',
                     wraplength=480)
    note.pack(anchor='w', pady=(8, 0))
    diagnostics = tk.Text(container, height=9, wrap='word')
    details_shown = [False]

    def toggle_details():
        details_shown[0] = not details_shown[0]
        if details_shown[0]:
            root.geometry('660x430')
            diagnostics.pack(fill='both', expand=True, pady=(8, 0))
        else:
            diagnostics.pack_forget()
            root.geometry('520x245')

    ttk.Button(container, text='Diagnostic details', command=toggle_details).pack(anchor='w')
    root.geometry('520x245')

    def close_window():
        if state['finished']:
            root.destroy()
        else:
            state['hidden'] = True
            root.withdraw()

    root.protocol('WM_DELETE_WINDOW', close_window)
    threading.Thread(target=work, name='migration-worker', daemon=False).start()

    def refresh():
        try:
            with open(status_path, 'r', encoding='utf-8') as stream:
                snapshot = json.load(stream)
        except (OSError, ValueError):
            snapshot = {'stage': 'starting', 'completed': 0, 'total': None,
                        'elapsedSeconds': 0, 'etaSeconds': None}
        stage_name = snapshot.get('stage', 'working')
        title.configure(text='Strategy storage migration: %s' % stage_name)
        completed, total = snapshot.get('completed'), snapshot.get('total')
        if total is None:
            bar.configure(mode='indeterminate')
            bar.start(12)
            count_text = '%s / unknown' % (completed if completed is not None else 'unknown')
        else:
            bar.stop()
            bar.configure(mode='determinate', maximum=max(1, total), value=min(completed or 0, total))
            count_text = '%s / %s (%s%%)' % (completed, total, snapshot.get('percent', 0))
        rate = snapshot.get('processingRate')
        unit = snapshot.get('processingRateUnit') or 'units/s'
        rate_text = ('%.2f %s' % (rate, unit)) if isinstance(rate, (int, float)) else 'rate unknown'
        stats.configure(text='Completed: %s    Elapsed: %s    Rate: %s    ETA: %s' %
                         (count_text, _human_time(snapshot.get('elapsedSeconds')),
                          rate_text, _human_time(snapshot.get('etaSeconds'))))
        detail_lines = [snapshot.get('detail') or ('Durable status: %s' % status_path)]
        if snapshot.get('warning'):
            detail_lines.append('Warning: ' + str(snapshot['warning']))
        if snapshot.get('error'):
            detail_lines.append('Error: ' + str(snapshot['error']))
        detail.configure(text='\n'.join(detail_lines))
        if details_shown[0]:
            diagnostics.configure(state='normal')
            diagnostics.delete('1.0', 'end')
            diagnostics.insert('end', json.dumps(snapshot, indent=2, default=_json_default))
            diagnostics.configure(state='disabled')
        if state['finished']:
            if not state['hidden']:
                note.configure(text=('Finished with error: %s' % state['error'])
                                if state['error'] else 'Saved: %s' % snapshot.get('outputLocation', status_path))
                title.configure(text='Strategy storage migration: ' +
                                 ('failed' if state['error'] else 'complete'))
            else:
                root.destroy()
                return
        root.after(700, refresh)

    root.after(200, refresh)
    root.mainloop()
    if state['error'] is not None:
        raise state['error']
    return state['report']


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='read-only source SQLite database (schema 3/4)')
    parser.add_argument('--stage', required=True, help='separate writable staging-copy path')
    parser.add_argument('--output', required=True, help='new compact VACUUM INTO output path')
    parser.add_argument('--status', help='durable atomic JSON status path')
    parser.add_argument('--log', help='durable append-only JSONL diagnostic path')
    parser.add_argument('--report', help='compact final JSON report path')
    parser.add_argument('--batch-rows', type=int, default=DEFAULT_BATCH_ROWS)
    parser.add_argument('--batch-bytes', type=int, default=DEFAULT_BATCH_BYTES)
    parser.add_argument('--encoder-threads', type=int, default=DEFAULT_ENCODER_THREADS)
    parser.add_argument('--pilot-rows', type=int,
                        help='process at most N rows on an existing staged copy; no compact output')
    parser.add_argument('--headless', action='store_true',
                        help='run without the default Tk progress monitor')
    parser.add_argument('--wait-for-pid', type=int, action='append', default=[],
                        help='wait for this optimizer process and its captured children to exit normally before opening source')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    kwargs = {'batch_rows': args.batch_rows, 'batch_bytes': args.batch_bytes,
              'encoder_threads': args.encoder_threads, 'status_path': args.status,
              'log_path': args.log, 'report_path': args.report, 'pilot_rows': args.pilot_rows,
              'wait_for_pids': args.wait_for_pid}
    worker = lambda: run_migration(args.source, args.stage, args.output, **kwargs)
    try:
        if args.headless:
            result = worker()
        else:
            result = _run_with_tk(worker, args.status or
                                  (str(_norm_path(args.stage)) + '.migration-status.json'))
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except (MigrationError, sqlite3.Error, OSError, ValueError) as exc:
        print('migration refused or failed safely: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
