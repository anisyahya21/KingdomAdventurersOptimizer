"""Opt-in, best-effort shadow copy of strategy experiment results.

Set ``KA_BATTLE_BINARY_SHADOW_DIR`` to enable this sidecar. It stores immutable
content-addressed codec definitions in ``definitions/`` and append-only records
in ``shadow.sqlite3``. The primary experiment database remains authoritative.

Only completed ``ea_sample`` and ``ea_holdout`` result/outcome/timing values are
copied when a completion or completed-sample reuse reaches the store hook. A
coalesced development sample is stored once under its existing ``sample_key``;
every reuse link resolves to that same identity. This does not scan or backfill
unrelated historical rows. The module never changes read routing, retention,
migrations, or the primary schema.

When a store operation runs inside a caller-owned SQLite transaction, the copy
is queued in memory. Call :func:`flush_pending_shadow` after commit or rollback;
it verifies the committed primary row before persisting and discards rolled
back/mismatched events. If the caller closes the connection without flushing,
call :func:`discard_pending_shadow` first. The queue has per-connection event
and byte limits. Standalone store operations flush immediately after their own
transaction commits.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import warnings
import zlib


ENV_NAME = 'KA_BATTLE_BINARY_SHADOW_DIR'
DB_NAME = 'shadow.sqlite3'
_PENDING = {}
_PENDING_LOCK = threading.RLock()
MAX_PENDING_EVENTS = 128
MAX_PENDING_BYTES = 32 * 1024 * 1024
_DEFINITION_CACHE = OrderedDict()
_READ_DEFINITION_CACHE = OrderedDict()
_VERIFIED_DEFINITIONS = OrderedDict()
_MAX_CACHED_SNAPSHOTS = 8
_MAX_CACHED_READERS = 8
_MAX_VERIFIED_PATHS = 64
_DEFINITION_LOCK = threading.RLock()
_PRIMARY_KEYS = {
    'sample': ('ea_sample', ('sample_key',)),
    'holdout': ('ea_holdout', ('experiment_id', 'candidate_id', 'seed_a', 'seed_b')),
}
_PAYLOAD_COLUMNS = ('result', 'outcome', 'timing')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def _warn(message, stacklevel=2):
    try:
        warnings.warn(message, RuntimeWarning, stacklevel=stacklevel)
    except Exception:
        # A warnings-as-errors policy must not turn an optional sidecar into a
        # primary-store failure either.
        pass


def _configured_root():
    raw = os.environ.get(ENV_NAME)
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def enabled():
    """Return whether the caller explicitly enabled a sidecar directory."""
    raw = os.environ.get(ENV_NAME)
    return bool(raw and raw.strip())


def report_failure(kind, record_key, error):
    """Expose an unexpected integration failure without raising into the primary store."""
    try:
        root = _configured_root()
        if root is None:
            return
        _status(root, 'error', kind=kind, recordKey=str(record_key),
                errorType=type(error).__name__, error=str(error))
        _warn('battle binary shadow integration failed for %s %s: %s' %
              (kind, record_key, error), stacklevel=2)
    except Exception as nested:
        _warn('battle binary shadow failure could not be reported: %s' % nested, stacklevel=2)


def _status(root, state, **details):
    """Best-effort durable status and diagnostic log; reporting cannot break callers."""
    if root is None:
        return
    now = time.time()
    item = {'updatedAt': now, 'state': state, **details}
    try:
        root.mkdir(parents=True, exist_ok=True)
        _atomic_bytes(root / 'status.json', (_canonical(item) + '\n').encode('utf-8'))
        if state in ('error', 'overflow', 'discarded'):
            log_path = root / 'shadow.log'
            if log_path.exists() and log_path.stat().st_size >= 1024 * 1024:
                os.replace(log_path, root / 'shadow.log.1')
            line = (_canonical(item) + '\n').encode('utf-8')
            with log_path.open('ab') as stream:
                stream.write(line)
                stream.flush()
    except Exception:
        # An invalid/unwritable target still needs to be visible to the caller.
        _warn('battle binary shadow status could not be persisted at %s' % root,
              stacklevel=3)


def _atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.%s-' % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _definition_bytes(definitions):
    if not isinstance(definitions, dict):
        raise TypeError('codec definitions must be a JSON object')
    # Definition key order participates in the codec's compact fingerprint.
    # Preserve the supplied mapping order while still writing deterministic JSON.
    return (json.dumps(definitions, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def _write_definition(root, definitions):
    root_key = str(root)
    identity = id(definitions)
    with _DEFINITION_LOCK:
        cached = _DEFINITION_CACHE.get(identity)
        if cached is None or cached[0] is not definitions:
            packed = zlib.compress(_definition_bytes(definitions), level=6)
            digest = hashlib.sha256(packed).hexdigest()
            # Keep the object itself strongly referenced so CPython cannot recycle its id.
            _DEFINITION_CACHE[identity] = (definitions, digest, packed)
        else:
            _, digest, packed = cached
            _DEFINITION_CACHE.move_to_end(identity)
        while len(_DEFINITION_CACHE) > _MAX_CACHED_SNAPSHOTS:
            _DEFINITION_CACHE.popitem(last=False)
        path = root / 'definitions' / (digest + '.json.zlib')
        verification_key = (root_key, digest)
        if verification_key not in _VERIFIED_DEFINITIONS:
            if path.exists():
                existing = path.read_bytes()
                if hashlib.sha256(existing).hexdigest() != digest:
                    raise RuntimeError('content-addressed definition mismatch for %s' % digest)
            else:
                _atomic_bytes(path, packed)
            _mark_verified(verification_key)
        return digest


def _mark_verified(key):
    _VERIFIED_DEFINITIONS[key] = None
    _VERIFIED_DEFINITIONS.move_to_end(key)
    while len(_VERIFIED_DEFINITIONS) > _MAX_VERIFIED_PATHS:
        _VERIFIED_DEFINITIONS.popitem(last=False)


def _connect(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / DB_NAME), timeout=20.0)
    db.execute('PRAGMA busy_timeout=20000')
    db.execute('''CREATE TABLE IF NOT EXISTS shadow_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        record_key TEXT NOT NULL,
        record_digest TEXT NOT NULL,
        context_json TEXT NOT NULL,
        packet BLOB NOT NULL,
        definition_digest TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(kind, record_key, record_digest)
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS shadow_record_by_identity '
               'ON shadow_record(kind, record_key, id)')
    return db


def _codec():
    # Import lazily so a disabled shadow has no codec/definition-file dependency.
    import strategy_battle_binary
    return strategy_battle_binary


def _decode_primary_value(value):
    if value is None:
        return None
    try:
        import strategy_payload_codec
        if strategy_payload_codec.has_codec_magic(value):
            value = strategy_payload_codec.decode_text(value)
    except (ImportError, AttributeError):
        pass
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    if not isinstance(value, str):
        raise TypeError('primary payload is neither JSON text nor a recognized BLOB')
    return json.loads(value)


def _exact_value(left, right):
    """Compare decoded JSON values without Python's bool/int or signed-zero shortcuts."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (left.keys() == right.keys() and
                all(_exact_value(left[key], right[key]) for key in left))
    if isinstance(left, list):
        return len(left) == len(right) and all(_exact_value(a, b) for a, b in zip(left, right))
    if type(left) is float:
        return struct.pack('<d', left) == struct.pack('<d', right)
    return left == right


def _primary_matches(db, event):
    table, key_columns = _PRIMARY_KEYS[event['kind']]
    where = event['primaryWhere']
    if set(where) != set(key_columns):
        raise ValueError('invalid primary identity for %s' % event['kind'])
    clause = ' AND '.join('%s=?' % key for key in key_columns)
    row = db.execute('SELECT result,outcome,timing FROM %s WHERE %s' % (table, clause),
                     tuple(where[key] for key in key_columns)).fetchone()
    if row is None:
        return False
    try:
        actual = {column: _decode_primary_value(value)
                  for column, value in zip(_PAYLOAD_COLUMNS, row)}
    except (TypeError, ValueError, UnicodeError):
        return False
    return _exact_value(actual, event['value'])


def _loaded_decoder(codec, root, definition_digest):
    """Load and verify one immutable definition snapshot once per sidecar root/digest."""
    root_key = str(root)
    verification_key = (root_key, definition_digest)
    with _DEFINITION_LOCK:
        if verification_key not in _VERIFIED_DEFINITIONS:
            path = root / 'definitions' / (definition_digest + '.json.zlib')
            packed = path.read_bytes()
            if hashlib.sha256(packed).hexdigest() != definition_digest:
                raise ValueError('definition checksum mismatch for %s' % definition_digest)
            _mark_verified(verification_key)
        cached = _READ_DEFINITION_CACHE.get(definition_digest)
        if cached is not None:
            _READ_DEFINITION_CACHE.move_to_end(definition_digest)
            return cached[0], cached[1]
        if 'packed' not in locals():
            path = root / 'definitions' / (definition_digest + '.json.zlib')
            packed = path.read_bytes()
        definitions = json.loads(zlib.decompress(packed).decode('utf-8'))
        decoder_type = getattr(codec, 'RecordDecoder', None)
        if decoder_type is not None:
            decoder = decoder_type(definitions)
        else:
            decoder = None
        _READ_DEFINITION_CACHE[definition_digest] = (definitions, decoder)
        _READ_DEFINITION_CACHE.move_to_end(definition_digest)
        while len(_READ_DEFINITION_CACHE) > _MAX_CACHED_READERS:
            _READ_DEFINITION_CACHE.popitem(last=False)
        return definitions, decoder


def _persist(root, event):
    codec = _codec()
    packet, definitions = codec.encode_record(event['value'])
    if not isinstance(packet, (bytes, bytearray, memoryview)):
        raise TypeError('codec packet must be bytes-like')
    packet = bytes(packet)
    definition_digest = _write_definition(root, definitions)
    record_digest = hashlib.sha256(
        event['kind'].encode('utf-8') + b'\0' + event['recordKey'].encode('utf-8') + b'\0' +
        _canonical(event['context']).encode('utf-8') + b'\0' +
        definition_digest.encode('ascii') + b'\0' + packet).hexdigest()
    sidecar = _connect(root)
    try:
        with sidecar:
            cursor = sidecar.execute('INSERT OR IGNORE INTO shadow_record '
                                     '(kind,record_key,record_digest,context_json,packet,'
                                     'definition_digest,created_at) VALUES(?,?,?,?,?,?,?)',
                                     (event['kind'], event['recordKey'], record_digest,
                                      _canonical(event['context']), sqlite3.Binary(packet),
                                      definition_digest, time.time()))
            inserted = cursor.rowcount == 1
            if inserted:
                record_id = int(cursor.lastrowid)
            else:
                record_id = int(sidecar.execute(
                    'SELECT id FROM shadow_record WHERE kind=? AND record_key=? AND record_digest=?',
                    (event['kind'], event['recordKey'], record_digest)).fetchone()[0])
    finally:
        sidecar.close()
    _status(root, 'stored', kind=event['kind'], recordKey=event['recordKey'],
            recordId=record_id, inserted=inserted, definitionDigest=definition_digest)
    return True


def _record_after_commit(db, root, event):
    try:
        if not _primary_matches(db, event):
            _status(root, 'discarded', kind=event['kind'], recordKey=event['recordKey'],
                    reason='primary row missing or differs from staged values')
            return False
        return _persist(root, event)
    except Exception as exc:
        _status(root, 'error', kind=event['kind'], recordKey=event['recordKey'],
                errorType=type(exc).__name__, error=str(exc))
        _warn('battle binary shadow write failed for %s %s: %s' %
              (event['kind'], event['recordKey'], exc), stacklevel=3)
        return False


def _queue_pending(db, root, event, byte_count):
    with _PENDING_LOCK:
        entry = _PENDING.get(id(db))
        if entry is None or entry[0] is not db:
            entry = [db, [], 0]
            _PENDING[id(db)] = entry
        if len(entry[1]) >= MAX_PENDING_EVENTS or entry[2] + byte_count > MAX_PENDING_BYTES:
            return False, len(entry[1]), entry[2]
        entry[1].append((root, event, byte_count))
        entry[2] += byte_count
        return True, len(entry[1]), entry[2]


def record_primary_write(db, *, kind, record_key, context, value, primary_where):
    """Record one completed primary result, swallowing every shadow-only failure.

    ``value`` must contain the decoded primary ``result``, ``outcome`` and ``timing``
    objects. The primary row is re-read and compared before the sidecar accepts it.
    """
    try:
        root = _configured_root()
    except Exception as exc:
        _warn('battle binary shadow could not resolve its configured directory: %s' % exc,
              stacklevel=2)
        return False
    if root is None:
        return False
    if kind not in _PRIMARY_KEYS:
        _status(root, 'error', errorType='ValueError', error='unknown shadow record kind')
        return False
    event = dict(kind=kind, recordKey=str(record_key), context=context,
                 value=value, primaryWhere=dict(primary_where))
    try:
        encoded_size = len(_canonical(event).encode('utf-8'))
        if encoded_size > 16 * 1024 * 1024:
            raise ValueError('shadow event exceeds 16 MiB bound')
        # Finish a previous caller-owned transaction if a later write reaches this hook.
        if not getattr(db, 'in_transaction', False):
            flush_pending_shadow(db)
        if getattr(db, 'in_transaction', False):
            queued, pending_count, pending_bytes = _queue_pending(db, root, event, encoded_size)
            if not queued:
                _status(root, 'overflow', kind=kind, recordKey=str(record_key),
                        reason='caller-owned transaction shadow queue limit reached',
                        pendingCount=pending_count, pendingBytes=pending_bytes,
                        eventLimit=MAX_PENDING_EVENTS, byteLimit=MAX_PENDING_BYTES,
                        skippedBytes=encoded_size)
                _warn('battle binary shadow queue limit reached; skipped %s %s' %
                      (kind, record_key), stacklevel=2)
                return False
            _status(root, 'deferred', kind=kind, recordKey=str(record_key),
                    reason='caller-owned primary transaction is still open')
            return False
        return _record_after_commit(db, root, event)
    except Exception as exc:
        _status(root, 'error', kind=kind, recordKey=str(record_key),
                errorType=type(exc).__name__, error=str(exc))
        _warn('battle binary shadow staging failed for %s %s: %s' %
              (kind, record_key, exc), stacklevel=2)
        return False


def flush_pending_shadow(db):
    """Flush staged events after commit, or discard them after rollback.

    Returns counts. If the caller transaction is still open, the queue is left intact.
    """
    if getattr(db, 'in_transaction', False):
        return {'persisted': 0, 'discarded': 0, 'deferred': True}
    with _PENDING_LOCK:
        entry = _PENDING.get(id(db))
        if entry is None or entry[0] is not db:
            return {'persisted': 0, 'discarded': 0, 'deferred': False}
        events = entry[1]
        _PENDING.pop(id(db), None)
    persisted = discarded = 0
    for root, event, _byte_count in events:
        if _record_after_commit(db, root, event):
            persisted += 1
        else:
            discarded += 1
    return {'persisted': persisted, 'discarded': discarded, 'deferred': False}


def discard_pending_shadow(db):
    """Drop queued events and release the strong connection reference before close."""
    with _PENDING_LOCK:
        entry = _PENDING.get(id(db))
        if entry is None or entry[0] is not db:
            return {'discarded': 0}
        events = entry[1]
        _PENDING.pop(id(db), None)
    count = len(events)
    for root, event, _byte_count in events:
        _status(root, 'discarded', kind=event['kind'], recordKey=event['recordKey'],
                reason='caller explicitly discarded pending shadow before connection close')
    return {'discarded': count}


def read_records(root=None, *, kind=None):
    """Read and decode sidecar records using their persisted definition snapshots."""
    root = Path(root).expanduser().resolve() if root is not None else _configured_root()
    if root is None or not (root / DB_NAME).exists():
        return []
    codec = _codec()
    sidecar = _connect(root)
    try:
        if kind is None:
            rows = sidecar.execute('SELECT kind,record_key,record_digest,context_json,packet,'
                                   'definition_digest,created_at FROM shadow_record ORDER BY id').fetchall()
        else:
            rows = sidecar.execute('SELECT kind,record_key,record_digest,context_json,packet,'
                                   'definition_digest,created_at FROM shadow_record WHERE kind=? '
                                   'ORDER BY id', (kind,)).fetchall()
    finally:
        sidecar.close()
    result = []
    for row in rows:
        record_kind, key, digest, context_json, packet, definition_digest, created = row
        definitions, decoder = _loaded_decoder(codec, root, definition_digest)
        value = (decoder.decode(bytes(packet)) if decoder is not None else
                 codec.decode_record(bytes(packet), definitions))
        result.append(dict(kind=record_kind, recordKey=key, recordDigest=digest,
                           context=json.loads(context_json), value=value,
                           definitionDigest=definition_digest, createdAt=created))
    return result


def count_records(root=None):
    """Return the number of append-only sidecar records, or zero when absent/disabled."""
    root = Path(root).expanduser().resolve() if root is not None else _configured_root()
    if root is None or not (root / DB_NAME).exists():
        return 0
    sidecar = sqlite3.connect(str(root / DB_NAME))
    try:
        return int(sidecar.execute('SELECT COUNT(*) FROM shadow_record').fetchone()[0])
    finally:
        sidecar.close()

