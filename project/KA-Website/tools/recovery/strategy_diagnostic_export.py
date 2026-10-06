"""On-demand, bounded diagnostic export for the strategy optimiser library.

This module answers one user request: *save a shareable detailed log of the last N battles*. It is a
read-only snapshot of an existing optimiser SQLite library - it never runs a battle, never writes to
the library, never restarts or mutates the live optimiser and never regenerates a replay. It streams a
ZIP that stays under a hard byte cap and states plainly what it does and does not contain.

What one export holds
---------------------

* ``battles.jsonl`` - one line per sampled battle, newest first. Each line carries the recorded result,
  the ordered seed pair where the row recorded one, the experiment(s) that requested the sample with
  their purpose/question, a field-level change diff against the reference/parent build with human
  labels where the recovery layer knows one, an uncertainty note, and provenance.
* ``builds.jsonl`` - one line per candidate referenced by an included battle (or by a change diff): the
  exact frozen scenario and settings, once per candidate, or an explicit ``available: false`` flag.
* ``diagnostics.json`` - worker/coordinator diagnostics and errors, read once from the host's own
  ``status()`` snapshot before the worker starts, plus the export's own counts.
* ``README.txt`` - what the archive is, and what is deliberately omitted.
* ``manifest.json`` - counts, cap, file inventory and omissions, written last.

Two sources, one honest ordering
--------------------------------

New Community runs live in the additive ``ea_sample`` / ``ea_sample_link`` / ``ea_experiment`` tables
(the legacy ``run`` table can be empty even after results). Legacy libraries keep results in ``run``.
The export prefers ``ea_sample`` when it holds completed samples and otherwise falls back to ``run``'s
newest rows. In both cases rows are ordered by the monotonic SQLite ``rowid`` (insertion order, which
follows the recorded creation timestamps) and a **cutoff is frozen at the start**, so rows written
after the export begins can never enter the window. Pagination is keyset-based over ``rowid`` with a
bounded batch size, so no full table is scanned and no read transaction is held between batches.

The hard cap
------------

The uncompressed budget (default 490 MB) is enforced as bytes are written: a battle line is only
emitted when it, every referenced build and a fixed trailer reserve still fit. Known replay/event
trace fields are dropped before a bounded result payload is decoded, so a giant legacy trace cannot be
transported; the archive says so explicitly rather than implying a full replay is present. After the
ZIP closes its real size is checked against the user cap (default 500,000,000 bytes) and the export
fails safe - the partial file is removed - if it is ever exceeded.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path

import strategy_intent_codec as intent_codec
import strategy_payload_codec as payload_codec

SCHEMA = 'ka-strategy-diagnostic-export-1'
README_NAME = 'README.txt'
BATTLES_NAME = 'battles.jsonl'
BUILDS_NAME = 'builds.jsonl'
DIAGNOSTICS_NAME = 'diagnostics.json'
MANIFEST_NAME = 'manifest.json'

#: The user-facing hard cap: 500 MB of a decimal megabyte, exactly as the control states it.
MAX_EXPORT_BYTES = 500_000_000
#: The conservative uncompressed budget. With STORE compression the archive is essentially its payload,
#: so keeping the payload under this leaves room for headers, the central directory and the trailer.
UNCOMPRESSED_CAP_BYTES = 490_000_000

DEFAULT_COUNT = 1000
MIN_COUNT = 1
MAX_COUNT = 1_000_000

#: Rows read per batch. Bounded so a very large library never materialises in one statement.
BATCH_SIZE = 250
#: Candidate ids per ``IN (...)`` list, safely under SQLite's bound-variable limit.
IN_CHUNK = 200

#: Per-row prefix bounds applied *before* decoding, so a huge legacy trace is never transported.
EA_RESULT_PREFIX = 4_000_000
EA_OUTCOME_PREFIX = 1_000_000
EA_TIMING_PREFIX = 200_000
LEGACY_RESULT_PREFIX = 1_500_000
#: A candidate scenario larger than this is reported as unavailable rather than transported.
MAX_BUILD_SCENARIO = 24_000_000

#: Bytes held back for the build log, diagnostics, manifest, README, zip headers and central directory.
TRAILER_RESERVE_BYTES = 8_000_000
DIAGNOSTICS_MAX_BYTES = 6_000_000

MAX_DIFF_ENTRIES = 200
MAX_DIFF_VALUE = 240
BUILD_OVERHEAD = 512
SCENARIO_CACHE_LIMIT = 128

#: Known replay/event-trace keys. They are dropped from a stored result with a note; their absence is
#: stated explicitly so a reader is never led to believe a full replay is attached.
TRACE_KEYS = frozenset((
    'replay', 'trace', 'eventTrace', 'rawTrace', 'replayExport', 'visualReplay', 'visual',
    'timeline', 'events', 'event_log', 'frames', 'replayFrames', 'checkpoint', 'checkpoints',
    'snapshots', 'recording', 'progressMetrics',
))

#: Status keys worth keeping as worker/coordinator diagnostics. Everything else in the (large) live
#: snapshot is data the battle log already covers, not diagnostics.
DIAGNOSTIC_KEYS = (
    'state', 'error', 'publicationError', 'startupDiagnostics', 'projectionCacheSeed', 'provenance', 'acceleration', 'scheduler', 'students',
    'breakthrough', 'mpRecovery', 'rankOne', 'encounterAware', 'campaign', 'focusEncounters',
    'focusEncounter', 'searchIdleReason', 'sessionRuns', 'sessionRunsBasis', 'totalRuns',
    'legacyTotalRuns', 'encounterRuns', 'sessionElapsedSeconds', 'currentRunElapsedSeconds',
    'compatible', 'objectiveVersion', 'searchSpaceVersion', 'libraryCapacityMiB',
    'throughput', 'encounterEvidence', 'encounterLedger', 'encounterCoverage',
)

TOP_LEVEL_LABELS = {
    'ownUnits': 'units', 'items': 'items', 'itemStock': 'item stock',
    'holyHerbStock': 'Holy Herb stock', 'holyHerbMaxUses': 'Holy Herb max uses',
    'holyHerbTriggerUnits': 'Holy Herb watch units', 'encounter': 'encounter', 'job': 'job',
    'skills': 'skills', 'invocationLevels': 'triggers', 'weaponId': 'weapon',
    'parameters': 'parameters', 'rawValue': 'value', 'name': 'unit name', 'level': 'level',
    'equipment': 'equipment', 'stats': 'stats', 'finishPolicy': 'finish policy',
    'mpWatchUnits': 'MP watch units',
}


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(',', ':'), default=str)


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def normalize_count(value):
    """The number of last battles to include: an integer 1..1,000,000 (default 1000).

    A blank or missing value is the documented default; anything else must be a whole number in range.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_COUNT
    if isinstance(value, bool):
        raise ValueError('number of battles must be a whole number, got %r' % (value,))
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError('number of battles must be a whole number, got %r' % (value,)) from None
    if not MIN_COUNT <= number <= MAX_COUNT:
        raise ValueError('number of battles must be between %d and %s, got %d'
                         % (MIN_COUNT, format(MAX_COUNT, ','), number))
    return number


def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=1')
    except sqlite3.DatabaseError:
        # A newer/older build may not accept the pragma; the read-only URI already forbids writes.
        pass
    return db


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _resolve_source(db):
    """``(kind, cutoff)`` where kind is 'ea_sample', 'legacy_run' or 'none' and cutoff is a rowid."""
    has_ea = all(_table_exists(db, name) for name in ('ea_sample', 'ea_sample_link', 'ea_experiment'))
    if has_ea:
        row = db.execute('SELECT MAX(rowid) FROM ea_sample WHERE result IS NOT NULL').fetchone()
        if row and row[0] is not None:
            return 'ea_sample', int(row[0])
    if _table_exists(db, 'run'):
        row = db.execute('SELECT MAX(rowid) FROM run').fetchone()
        if row and row[0] is not None:
            return 'legacy_run', int(row[0])
        return 'legacy_run', None
    if has_ea:
        return 'ea_sample', None
    return 'none', None


EA_BATCH_SQL = (
    'SELECT rowid AS rid, sample_key, candidate_id, seed_a, seed_b, created_at, '
    "CASE WHEN typeof(result)='blob' AND substr(result,1,3)=X'4B4150' "
    'THEN substr(result,1,?) WHEN typeof(result)=\'blob\' THEN substr(result,1,?) '
    'ELSE substr(result,1,?) END AS result_text, '
    'typeof(result) AS result_storage_type, length(result) AS result_storage_length, '
    "CASE WHEN typeof(outcome)='blob' AND substr(outcome,1,3)=X'4B4150' "
    'THEN substr(outcome,1,?) WHEN typeof(outcome)=\'blob\' THEN substr(outcome,1,?) '
    'ELSE substr(outcome,1,?) END AS outcome_text, '
    'typeof(outcome) AS outcome_storage_type, length(outcome) AS outcome_storage_length, '
    'substr(timing,1,?) AS timing_text '
    'FROM ea_sample WHERE result IS NOT NULL AND rowid < ? '
    'ORDER BY rowid DESC LIMIT ?'
)
LEGACY_BATCH_SQL = (
    'SELECT rowid AS rid, candidate, phase, ordinal, '
    'substr(result,1,?) AS result_text '
    'FROM run WHERE rowid < ? ORDER BY rowid DESC LIMIT ?'
)


def _ea_batches(db, cutoff, limit):
    after = cutoff + 1
    produced = 0
    while produced < limit:
        size = min(BATCH_SIZE, limit - produced)
        cursor = db.execute(EA_BATCH_SQL,
                            (payload_codec.MAX_ENCODED_BYTES + 1,
                             4 * (EA_RESULT_PREFIX + 1), EA_RESULT_PREFIX,
                             payload_codec.MAX_ENCODED_BYTES + 1,
                             4 * (EA_OUTCOME_PREFIX + 1), EA_OUTCOME_PREFIX,
                             EA_TIMING_PREFIX, after, size))
        rows = cursor.fetchall()
        cursor.close()
        if not rows:
            return
        after = int(rows[-1]['rid'])
        produced += len(rows)
        yield rows


def _legacy_batches(db, cutoff, limit):
    after = cutoff + 1
    produced = 0
    while produced < limit:
        size = min(BATCH_SIZE, limit - produced)
        cursor = db.execute(LEGACY_BATCH_SQL, (LEGACY_RESULT_PREFIX, after, size))
        rows = cursor.fetchall()
        cursor.close()
        if not rows:
            return
        after = int(rows[-1]['rid'])
        produced += len(rows)
        yield rows


def _links_for(db, sample_keys):
    found = {}
    keys = [key for key in dict.fromkeys(sample_keys) if key]
    for chunk in _chunks(keys, IN_CHUNK):
        sql = ('SELECT experiment_id, candidate_id, seed_a, seed_b, sample_key, reused, '
               'charged_experiment_id, created_at FROM ea_sample_link WHERE sample_key IN ('
               + ','.join('?' for _ in chunk) + ') ORDER BY experiment_id')
        cursor = db.execute(sql, chunk)
        rows = cursor.fetchall()
        cursor.close()
        for row in rows:
            found.setdefault(row['sample_key'], []).append(dict(row))
    return found


def _experiments_for(db, experiment_ids):
    found = {}
    ids = sorted({int(value) for value in experiment_ids if value is not None})
    for chunk in _chunks(ids, IN_CHUNK):
        sql = ('SELECT id, request_key, scope, parent_id, reference_id, policy, mechanics_revision, '
               'encounter_revision, purpose, owner, owner_share, fixed_fields, changed_fields, '
               'planned_budget, stopping, session_id, measurement_window, created_at, intent_json, '
               "CASE WHEN typeof(full_intent)='blob' "
               'THEN substr(full_intent,1,?) ELSE full_intent END AS full_intent '
               'FROM ea_experiment WHERE id IN (' + ','.join('?' for _ in chunk) + ')')
        try:
            cursor = db.execute(sql, (payload_codec.MAX_ENCODED_BYTES + 1, *chunk))
        except sqlite3.OperationalError as exc:
            if 'full_intent' not in str(exc):
                raise
            legacy_sql = ('SELECT id, request_key, scope, parent_id, reference_id, policy, '
                          'mechanics_revision, encounter_revision, purpose, owner, owner_share, '
                          'fixed_fields, changed_fields, planned_budget, stopping, session_id, '
                          'measurement_window, created_at, intent_json, NULL AS full_intent '
                          'FROM ea_experiment WHERE id IN (' + ','.join('?' for _ in chunk) + ')')
            cursor = db.execute(legacy_sql, chunk)
        rows = cursor.fetchall()
        cursor.close()
        for row in rows:
            found[int(row['id'])] = row
    return found


def _json_or_none(text):
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _experiment_view(row):
    try:
        full_intent = row['full_intent']
    except (IndexError, KeyError):
        full_intent = None
    try:
        raw_intent = intent_codec.decode_intent(row['intent_json'], full_intent)
    except (TypeError, ValueError):
        raw_intent = None
    intent = _json_or_none(raw_intent)
    question = None
    if isinstance(intent, dict):
        question = intent.get('question') or intent.get('purpose') or None
    return {
        'experimentId': int(row['id']),
        'scope': row['scope'],
        'purpose': row['purpose'],
        'question': question,
        'owner': row['owner'],
        'parentId': row['parent_id'],
        'referenceId': row['reference_id'],
        'policy': _json_or_none(row['policy']),
        'mechanicsRevision': row['mechanics_revision'],
        'encounterRevision': row['encounter_revision'],
        'measurementWindow': _json_or_none(row['measurement_window']),
        'fixedFields': _json_or_none(row['fixed_fields']),
        'changedFields': _json_or_none(row['changed_fields']),
        'stopping': _json_or_none(row['stopping']),
        'plannedBudget': row['planned_budget'],
        'createdAt': row['created_at'],
    }


def _strip_traces(value):
    """Drop known replay/event-trace keys from one decoded result. Returns ``(value, removed)``."""
    removed = []
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key in TRACE_KEYS:
                removed.append(key)
                del value[key]
    return value, removed


def _decode_bounded(text, label='result'):
    """Decode a per-row JSON payload that was already read as a bounded prefix.

    Returns ``(value, note)``. A payload that does not parse (typically because the prefix cut a huge
    trace) is replaced by an explicit unavailable marker - never silently emptied.
    """
    if text is None or text == '':
        return None, '%s was not recorded for this row' % label
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return ({'_unavailable': '%s payload exceeds the per-row bound or is not valid JSON '
                                 '(read as %d characters); it was not decoded' % (label, len(text))},
                '%s unreadable' % label)
    if not isinstance(value, dict):
        value = {'value': value}
    value, removed = _strip_traces(value)
    if removed:
        return value, 'omitted trace fields: ' + ', '.join(sorted(removed))
    return value, None


def _decode_ea_bounded(row, column, label, max_characters):
    """Decode one bounded ea_* cell while retaining SQLite TEXT/BLOB prefix behavior."""
    storage_type = row[column + '_storage_type']
    value = row[column + '_text']
    if value is None:
        return _decode_bounded(None, label)
    try:
        if storage_type == 'blob':
            total_length = int(row[column + '_storage_length'] or 0)
            if payload_codec.has_codec_magic(value):
                if total_length > payload_codec.MAX_ENCODED_BYTES or len(value) != total_length:
                    raise payload_codec.PayloadCodecError(
                        'compressed payload exceeds the bounded export read')
                text = payload_codec.decode_text(value)[:max_characters]
            else:
                # Legacy raw UTF-8 BLOBs retain the old JSON-bytes behavior. SQL reads at most four
                # bytes per requested Unicode character, then this helper applies the character cap.
                text = payload_codec.decode_prefix(value, max_characters)
        elif storage_type == 'text':
            # SQLite substr(TEXT,...) counts characters; preserve its existing output unchanged.
            text = value
        else:
            raise payload_codec.PayloadCodecError(
                'unexpected SQLite payload storage type %r' % storage_type)
    except payload_codec.PayloadCodecError as exc:
        return ({'_unavailable': '%s payload could not be decoded safely: %s'
                              % (label, exc)}, '%s unreadable' % label)
    return _decode_bounded(text, label)


def _label_for(path):
    parts = path.split('.')
    for index, part in enumerate(parts):
        if part == 'parameters' and index + 1 < len(parts):
            return 'parameter %s' % parts[index + 1]
    leaf = parts[-1] if parts else path
    return TOP_LEVEL_LABELS.get(leaf, leaf)


def _scalar(value):
    if isinstance(value, str) and len(value) > MAX_DIFF_VALUE:
        return value[:MAX_DIFF_VALUE] + '...'
    if isinstance(value, (dict, list)):
        return '<%s of %d>' % (type(value).__name__, len(value))
    return value


def _field_diff(before, after, path='', depth=0, out=None):
    """Bounded field-level difference between two stored scenarios. Leaves carry a human label."""
    if out is None:
        out = []
    if len(out) >= MAX_DIFF_ENTRIES or depth > 4:
        return out
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            if len(out) >= MAX_DIFF_ENTRIES:
                break
            child = '%s.%s' % (path, key) if path else str(key)
            if key not in before:
                out.append({'path': child, 'label': _label_for(child),
                            'before': None, 'after': _scalar(after[key])})
            elif key not in after:
                out.append({'path': child, 'label': _label_for(child),
                            'before': _scalar(before[key]), 'after': None})
            else:
                _field_diff(before[key], after[key], child, depth + 1, out)
        return out
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            out.append({'path': path, 'label': _label_for(path),
                        'before': len(before), 'after': len(after)})
            return out
        for index, (left, right) in enumerate(zip(before, after)):
            if len(out) >= MAX_DIFF_ENTRIES:
                break
            _field_diff(left, right, '%s.%d' % (path, index), depth + 1, out)
        return out
    if before != after:
        out.append({'path': path, 'label': _label_for(path),
                    'before': _scalar(before), 'after': _scalar(after)})
    return out


class _ScenarioStore:
    """Bounded, cached access to the exact stored scenarios of the candidates we touch."""

    def __init__(self, db):
        self._db = db
        self.sizes = {}
        self._cache = {}
        self._order = []
        self.reserved = 0

    def _note(self, candidate_id, size):
        self.sizes[candidate_id] = size
        self.reserved += (size + BUILD_OVERHEAD) if isinstance(size, int) else BUILD_OVERHEAD

    def text(self, candidate_id):
        """The bounded stored scenario text, or None when the candidate row is absent/oversized."""
        if candidate_id in self._cache:
            return self._cache[candidate_id]['s']
        if not candidate_id or not _table_exists(self._db, 'candidate'):
            return None
        if candidate_id in self.sizes:
            size = self.sizes[candidate_id]
        else:
            row = self._db.execute(
                'SELECT length(substr(scenario,1,?)) AS n FROM candidate WHERE id=?',
                (MAX_BUILD_SCENARIO, candidate_id)).fetchone()
            if row is None:
                self._note(candidate_id, None)
                return None
            size = int(row['n'] or 0)
            self._note(candidate_id, size)
        if size is None or size >= MAX_BUILD_SCENARIO:
            return None
        row = self._db.execute('SELECT substr(scenario,1,?) AS s, label, source, stats, created '
                               'FROM candidate WHERE id=?',
                               (MAX_BUILD_SCENARIO, candidate_id)).fetchone()
        if row is None:
            return None
        self._remember(candidate_id, row)
        return row['s']

    def _remember(self, candidate_id, row):
        self._cache[candidate_id] = row
        self._order.append(candidate_id)
        while len(self._order) > SCENARIO_CACHE_LIMIT:
            self._cache.pop(self._order.pop(0), None)

    def parsed(self, candidate_id):
        """The parsed scenario, or None. Never raises on malformed stored text."""
        text = self.text(candidate_id)
        if text is None:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return None

    def build_entry(self, candidate_id):
        text = self.text(candidate_id)
        if text is None:
            return {'candidateId': candidate_id, 'available': False,
                    'reason': 'the candidate row is not resident in this library, or its scenario '
                              'exceeds the per-build export bound'}
        try:
            scenario = json.loads(text)
        except (TypeError, ValueError):
            return {'candidateId': candidate_id, 'available': False,
                    'reason': 'the stored scenario could not be decoded'}
        entry = {'candidateId': candidate_id, 'available': True, 'scenario': scenario}
        row = self._cache.get(candidate_id)
        if row is not None:
            entry['label'] = row['label']
            entry['source'] = row['source']
            entry['created'] = row['created']
            stats = _json_or_none(row['stats'])
            if stats is not None:
                entry['stats'] = stats
        return entry


def _changes_for(db, scenarios, candidate_id, reference_id, parent_id):
    """A field-level change diff of the battle's build against its reference, else its parent."""
    reference = str(reference_id) if reference_id not in (None, '') else None
    parent = str(parent_id) if parent_id not in (None, '') else None
    child = scenarios.parsed(candidate_id)
    target_id, role = None, None
    for candidate, name in ((reference, 'reference'), (parent, 'parent')):
        if candidate and candidate != candidate_id:
            target_id, role = candidate, name
            break
    if child is None:
        return {'available': False, 'referenceId': reference, 'parentId': parent,
                'reason': 'the battle build scenario is unavailable, so no field diff was computed'}
    if target_id is None:
        return {'available': False, 'referenceId': reference, 'parentId': parent,
                'reason': 'no reference or parent build is recorded for this sample'}
    target = scenarios.parsed(target_id)
    if target is None:
        return {'available': False, 'referenceId': reference, 'parentId': parent,
                'reason': 'the %s build is not resident in this library, so no field diff was '
                          'computed' % role}
    fields = _field_diff(target, child)
    human = None
    try:
        import strategy_naming
        human = strategy_naming.describe(target, child) or None
    except Exception:  # noqa: BLE001 - a naming helper must never fail an export
        human = None
    return {'available': True, 'role': role, 'comparedTo': target_id,
            'referenceId': reference, 'parentId': parent, 'human': human, 'fields': fields}


def _lineage_parent(db, candidate_id):
    if not _table_exists(db, 'lineage'):
        return None, None
    row = db.execute('SELECT parent, source FROM lineage WHERE candidate=?',
                     (candidate_id,)).fetchone()
    if row is None:
        return None, None
    return row['parent'], row['source']


def diagnostic_view(status, budget=DIAGNOSTICS_MAX_BYTES):
    """A bounded, JSON-ready subset of the host's ``status()`` snapshot: diagnostics and errors only.

    The live snapshot also carries every candidate and example; that is data the battle log already
    covers, not diagnostics, so it is not copied here. If the retained blocks still exceed the budget
    the largest are dropped by name so the omission is visible rather than silent.
    """
    view = {}
    if isinstance(status, dict):
        for key in DIAGNOSTIC_KEYS:
            if key in status:
                view[key] = status[key]
    dropped = []
    while True:
        try:
            size = len(_dumps(view).encode('utf-8'))
        except (TypeError, ValueError):
            break
        if size <= budget:
            break
        sizes = [(key, len(_dumps(value).encode('utf-8'))) for key, value in view.items()]
        if len(sizes) <= 1:
            break
        key = max(sizes, key=lambda item: item[1])[0]
        dropped.append(key)
        del view[key]
    if dropped:
        view['_omittedDiagnosticKeys'] = sorted(dropped)
    return view


def _readme(kind, cutoff, cap, requested):
    ordering = {'ea_sample': 'ea_sample.rowid (insertion order, newest first)',
                'legacy_run': 'run.rowid (insertion order, newest first)'}.get(
        kind, 'none (no battle table is present in this library)')
    return '\n'.join([
        'Kingdom Adventurers - strategy optimiser diagnostic export',
        '================================================================',
        '',
        'This is a read-only snapshot of an existing optimiser library. It did not run a battle,',
        'change a setting, or write to the library it was taken from.',
        '',
        'Files',
        '  battles.jsonl     one JSON object per sampled battle, newest first.',
        '  builds.jsonl      the exact frozen scenario/settings, once per referenced candidate.',
        '  diagnostics.json  worker/coordinator diagnostics and errors, plus the export counts.',
        '  manifest.json     the counts, the byte cap, the file inventory and the omissions.',
        '',
        'What each battle carries: the recorded result, the ordered seed pair where the row recorded',
        'one, the experiment purpose/question that requested it, a field-level change diff against the',
        'reference or parent build (with human labels where the recovery layer knows them), an',
        'uncertainty note, and provenance (source table, monotonic row id, revisions).',
        '',
        'What is deliberately NOT included',
        '  * ea_holdout confirmation battles. This export currently selects development samples',
        '    (or legacy runs) only. Its time span must NOT be used as total optimiser throughput.',
        '  * replay/event traces - a known trace field is dropped from a stored result, and the export',
        '    never regenerates a battle. The absence of a replay is stated per row, not implied.',
        '  * the live optimiser snapshot in full - only diagnostics and errors are kept, bounded.',
        '  * uncommitted work - only results that were already saved to the library are read.',
        '',
        'Ordering: %s. The window cutoff is frozen when the export starts, so rows written while it '
        'runs cannot enter it.' % ordering,
        '',
        'Requested battles: %d. Byte cap: %s bytes.' % (requested, format(cap, ',')),
        '',
    ])


def _manifest_notes(counts):
    notes = ['Confirmation battles in ea_holdout are omitted. This is a development-only window '
             'when source=ea_sample, not the last N battles across all work. Use the diagnostic '
             'snapshot for campaign-wide counts; rowids from separate tables are not comparable.']
    if counts.get('libraryHeldFewer'):
        notes.append('The selected source holds only %d completed battle(s) at this cutoff, fewer than the '
                     '%d requested.' % (counts['examined'], counts['requested']))
    if counts.get('truncatedByCap'):
        notes.append('The window stopped at the byte cap after %d battle(s); older battles were '
                     'left out rather than writing an oversized archive.' % counts['included'])
    if counts.get('source') == 'legacy_run':
        notes.append('New Community runs live in the ea_sample tables; this library had no completed '
                     'ea_sample rows, so the newest legacy run rows were used instead.')
    if counts.get('source') == 'none':
        notes.append('No battle table (ea_sample or run) is present in this library.')
    return notes


def _uncertainty(result, stopping):
    censored = None
    verdict = None
    pending = None
    if isinstance(result, dict):
        censored = bool(result.get('censored'))
        value = result.get('verdict')
        verdict = int(value) if isinstance(value, int) and not isinstance(value, bool) else None
        reward = result.get('rewardOutcome')
        if isinstance(reward, dict):
            pending = reward.get('pendingChests')
    return {
        'singleSample': True,
        'censored': censored,
        'verdict': verdict,
        'pendingChests': pending,
        'stopping': stopping,
        'note': 'One battle is one sample, not an estimate; the experiment stopping rule and the '
                'reported reward reading bound what this single row can support.',
    }


def _battle_payload(db, kind, row, scenarios, links, experiments):
    """One battle JSON object plus the candidate ids it references."""
    if kind == 'ea_sample':
        result, result_note = _decode_ea_bounded(row, 'result', 'result', EA_RESULT_PREFIX)
        outcome, outcome_note = _decode_ea_bounded(row, 'outcome', 'outcome',
                                                   EA_OUTCOME_PREFIX)
        timing, _ = _decode_bounded(row['timing_text'], 'timing')
        candidate_id = row['candidate_id']
        seed_pair = None
        if row['seed_a'] is not None and row['seed_b'] is not None:
            seed_pair = [int(row['seed_a']), int(row['seed_b'])]
        link_rows = links.get(row['sample_key']) or []
        link_views = []
        reference_id = parent_id = None
        stopping = None
        declared = []
        for entry in link_rows:
            experiment = experiments.get(int(entry['experiment_id']))
            view = {'experimentId': int(entry['experiment_id']),
                    'reused': bool(entry['reused']),
                    'chargedExperimentId': entry['charged_experiment_id'],
                    'createdAt': entry['created_at']}
            if experiment is not None:
                experiment_view = _experiment_view(experiment)
                view['experiment'] = experiment_view
                if reference_id is None and experiment_view['referenceId']:
                    reference_id = experiment_view['referenceId']
                if parent_id is None and experiment_view['parentId'] is not None:
                    parent_id = experiment_view['parentId']
                changed = experiment_view['changedFields']
                if isinstance(changed, (list, tuple)):
                    declared.extend(str(item) for item in changed)
                if stopping is None:
                    stopping = experiment_view['stopping']
            link_views.append(view)
        changes = _changes_for(db, scenarios, candidate_id, reference_id, parent_id)
        if declared:
            changes['declaredChangedFields'] = sorted(set(declared))
        rid = int(row['rid'])
        provenance = {'source': 'ea_sample', 'sampleKey': row['sample_key'],
                      'rowid': rid, 'createdAt': row['created_at'],
                      'links': [{'experimentId': view['experimentId'],
                                 'chargedExperimentId': view['chargedExperimentId'],
                                 'reused': view['reused']} for view in link_views]}
        payload = {
            'source': 'ea_sample',
            'sampleKey': row['sample_key'],
            'rowid': rid,
            'candidateId': candidate_id,
            'seedPair': seed_pair,
            'result': result,
            'outcome': outcome,
            'timing': timing,
            'createdAt': row['created_at'],
            'changes': changes,
            'experiments': link_views,
            'provenance': provenance,
            'replayTracesIncluded': False,
            'traceOmitted': bool(result_note or outcome_note),
            'notes': [note for note in (result_note, outcome_note) if note],
            'uncertainty': _uncertainty(result, stopping),
        }
        references = {candidate_id, changes.get('comparedTo')}
        for view in link_views:
            experiment_view = view.get('experiment') or {}
            references.add(experiment_view.get('referenceId'))
            references.add(experiment_view.get('parentId'))
        return payload, {ref for ref in references if ref}

    result, result_note = _decode_bounded(row['result_text'], 'result')
    candidate_id = row['candidate']
    seed_pair = None
    if isinstance(result, dict):
        seeds = result.get('seeds')
        if isinstance(seeds, (list, tuple)) and len(seeds) == 2:
            try:
                seed_pair = [int(seeds[0]), int(seeds[1])]
            except (TypeError, ValueError):
                seed_pair = None
    parent_id, lineage_source = _lineage_parent(db, candidate_id)
    changes = _changes_for(db, scenarios, candidate_id, None, parent_id)
    rid = int(row['rid'])
    provenance = {'source': 'legacy_run', 'rowid': rid, 'phase': row['phase'],
                  'ordinal': int(row['ordinal']), 'lineageSource': lineage_source}
    payload = {
        'source': 'legacy_run',
        'rowid': rid,
        'candidateId': candidate_id,
        'phase': row['phase'],
        'ordinal': int(row['ordinal']),
        'seedPair': seed_pair,
        'result': result,
        'outcome': None,
        'timing': None,
        'createdAt': None,
        'changes': changes,
        'experiments': [],
        'provenance': provenance,
        'replayTracesIncluded': False,
        'traceOmitted': bool(result_note),
        'notes': [note for note in (result_note,) if note] + [
            'This legacy run row carries no canonical outcome row; the recorded result is retained '
            'verbatim (minus known trace fields).'],
        'uncertainty': _uncertainty(result, None),
    }
    references = {candidate_id, changes.get('comparedTo')}
    return payload, {ref for ref in references if ref}


class _Counter:
    """A write-through byte counter. ``total`` tracks the real archive size exactly, because
    ``zipfile`` writes every byte - including headers and the central directory - through it."""

    def __init__(self, raw):
        self.raw = raw
        self.total = 0

    def write(self, data):
        self.total += len(data)
        return self.raw.write(data)

    def flush(self):
        return self.raw.flush()

    def close(self):
        pass


def _write_bytes(zip_file, counter, entry_bytes, name, payload):
    before = counter.total
    with zip_file.open(name, 'w') as entry:
        entry.write(payload)
    entry_bytes[name] = counter.total - before


def _iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def export(source, destination, count, *, max_bytes=MAX_EXPORT_BYTES,
           uncompressed_cap=UNCOMPRESSED_CAP_BYTES, diagnostics=None, progress=None,
           monotonic=time.monotonic):
    """Write the diagnostic archive and return a result dict. Read-only, bounded, atomic.

    ``destination`` is written to a ``.partial`` sibling and renamed into place only after the archive
    closes and its real size is verified against ``max_bytes``.
    """
    source = Path(source)
    destination = Path(destination)
    requested = normalize_count(count)
    cap = max(0, min(int(max_bytes), int(uncompressed_cap)))
    started = monotonic()

    def report(stage, **fields):
        if progress is not None:
            fields.setdefault('requested', requested)
            fields.setdefault('capBytes', cap)
            fields.setdefault('elapsedSeconds', round(monotonic() - started, 1))
            progress(stage, **fields)

    if not source.is_file():
        raise ValueError('there is no library file at %s' % source)
    temp = destination.with_name(destination.name + '.partial')
    protected = {Path(str(source.resolve()) + suffix) for suffix in ('', '-wal', '-shm', '-journal')}
    if destination.resolve() in protected or temp.resolve() in protected:
        raise ValueError('the export destination must not be the library or its sidecar files; pick another file')

    db = _readonly(source)
    entry_bytes = {}
    counts = {'requested': requested, 'included': 0, 'truncated': 0, 'examined': 0,
              'truncatedByCap': False, 'source': 'none', 'cutoffRowid': None,
              'libraryHeldFewer': False}
    file_bytes = None
    try:
        kind, cutoff = _resolve_source(db)
        counts['source'] = kind
        counts['cutoffRowid'] = cutoff
        scenarios = _ScenarioStore(db)
        trace_rows = 0

        destination.parent.mkdir(parents=True, exist_ok=True)
        if temp.exists():
            temp.unlink()

        report('starting', stageLabel='Opening the library')
        with open(temp, 'wb') as raw:
            counter = _Counter(raw)
            zip_file = zipfile.ZipFile(counter, 'w', compression=zipfile.ZIP_STORED)
            try:
                _write_bytes(zip_file, counter, entry_bytes, README_NAME,
                             _readme(kind, cutoff, cap, requested).encode('utf-8'))

                included = 0
                examined = 0
                truncated_by_cap = False
                if kind == 'ea_sample':
                    batches = _ea_batches(db, cutoff, requested)
                elif kind == 'legacy_run':
                    batches = _legacy_batches(db, cutoff, requested)
                else:
                    batches = ()
                before = counter.total
                with zip_file.open(BATTLES_NAME, 'w') as out:
                    for batch in batches:
                        if truncated_by_cap or included >= requested:
                            break
                        links = (_links_for(db, [row['sample_key'] for row in batch])
                                 if kind == 'ea_sample' else {})
                        experiments = {}
                        if kind == 'ea_sample':
                            experiments = _experiments_for(
                                db, [entry['experiment_id'] for rows in links.values()
                                     for entry in rows])
                        for row in batch:
                            examined += 1
                            if included >= requested:
                                break
                            payload, _references = _battle_payload(
                                db, kind, row, scenarios, links, experiments)
                            line = _dumps(payload).encode('utf-8') + b'\n'
                            projected = (counter.total + len(line) + scenarios.reserved
                                         + TRAILER_RESERVE_BYTES)
                            if projected > cap:
                                truncated_by_cap = True
                                break
                            out.write(line)
                            included += 1
                            if payload.get('traceOmitted'):
                                trace_rows += 1
                            report('writing', stageLabel='Writing battles', examined=examined,
                                   included=included, bytesWritten=counter.total)
                entry_bytes[BATTLES_NAME] = counter.total - before
                counts.update(included=included, examined=examined, truncatedByCap=truncated_by_cap)
                counts['truncated'] = max(0, requested - included)
                counts['libraryHeldFewer'] = (not truncated_by_cap) and examined < requested

                before = counter.total
                with zip_file.open(BUILDS_NAME, 'w') as out:
                    for candidate_id in scenarios.sizes:
                        out.write(_dumps(scenarios.build_entry(candidate_id)).encode('utf-8') + b'\n')
                entry_bytes[BUILDS_NAME] = counter.total - before

                omissions = {
                    'confirmationBattles': 'ea_holdout is not included; do not infer total '
                                           'optimiser throughput from this development-only export',
                    'replayTraces': 'not included: replay/event traces are never attached, moved or '
                                    'regenerated by this export',
                    'replayRowsMarked': trace_rows,
                    'diagnostics': 'worker/coordinator diagnostics only; the full live snapshot and '
                                   'uncommitted work are not included',
                    'fullCompactOutcomes': 'results and full compact canonical outcomes are retained '
                                           'per included battle',
                }
                generated_at = _iso_now()
                diagnostics_payload = {
                    'schema': SCHEMA,
                    'generatedAt': generated_at,
                    'library': {'path': str(source), 'name': source.name,
                                'bytes': source.stat().st_size},
                    'counts': counts,
                    'capBytes': cap,
                    'maxExportBytes': int(max_bytes),
                    'uncompressedCapBytes': int(uncompressed_cap),
                    'optimizer': diagnostic_view(diagnostics),
                    'omissions': omissions,
                }
                _write_bytes(zip_file, counter, entry_bytes, DIAGNOSTICS_NAME,
                             _dumps(diagnostics_payload).encode('utf-8'))

                manifest = {
                    'schema': SCHEMA,
                    'generatedAt': generated_at,
                    'counts': counts,
                    'capBytes': cap,
                    'maxExportBytes': int(max_bytes),
                    'uncompressedCapBytes': int(uncompressed_cap),
                    'payloadBytes': counter.total,
                    'files': dict(entry_bytes),
                    'omissions': omissions,
                    'notes': _manifest_notes(counts),
                }
                _write_bytes(zip_file, counter, entry_bytes, MANIFEST_NAME,
                             json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
            finally:
                zip_file.close()
            file_bytes = counter.total

        if file_bytes > int(max_bytes):
            raise ValueError('the archive would be %s bytes, over the %s-byte cap; it was removed and '
                             'nothing was written'
                             % (format(file_bytes, ','), format(int(max_bytes), ',')))
        report('finalizing', stageLabel='Verifying the archive', bytesWritten=file_bytes,
               included=counts['included'])
        os.replace(temp, destination)
        counts['fileBytes'] = file_bytes
        report('done', stageLabel='Finished', bytesWritten=file_bytes,
               included=counts['included'], path=str(destination))
        return dict(ok=True, path=str(destination), source=str(source), requested=requested,
                    included=counts['included'], truncated=counts['truncated'],
                    truncatedByCap=counts['truncatedByCap'], examined=counts['examined'],
                    libraryHeldFewer=counts['libraryHeldFewer'], sourceKind=kind,
                    cutoffRowid=cutoff, capBytes=cap, fileBytes=file_bytes, counts=counts,
                    files=dict(entry_bytes))
    except BaseException:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        raise
    finally:
        db.close()


class DiagnosticExportJob:
    """One background export. ``status()`` is a cheap cached read for the UI poll."""

    def __init__(self, job_id, source, destination, count):
        self.job_id = job_id
        self.source = Path(source)
        self.destination = Path(destination)
        self.count = count
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._state = {
            'jobId': job_id, 'state': 'running', 'stage': 'starting', 'stageLabel': 'Starting',
            'path': str(destination), 'source': str(source), 'requested': count,
            'included': 0, 'truncated': 0, 'examined': 0, 'bytesWritten': 0,
            'capBytes': None, 'elapsedSeconds': 0.0, 'ratePerSecond': None, 'etaSeconds': None,
            'error': None, 'message': None,
        }

    def status(self):
        with self._lock:
            return dict(self._state)

    def running(self):
        with self._lock:
            return self._state['state'] == 'running'

    def _progress(self, stage, **fields):
        elapsed = time.monotonic() - self._started
        with self._lock:
            current = self._state['included']
        included = int(fields.get('included') or current or 0)
        requested = int(fields.get('requested') or self.count or 0)
        rate = (included / elapsed) if elapsed > 0 and included else None
        eta = None
        if rate and requested > included:
            eta = round((requested - included) / rate, 1)
        fields.update(elapsedSeconds=round(elapsed, 1),
                      ratePerSecond=(round(rate, 2) if rate else None),
                      etaSeconds=eta)
        with self._lock:
            self._state.update(fields)

    def run(self, diagnostics, max_bytes, uncompressed_cap):
        try:
            result = export(self.source, self.destination, self.count, max_bytes=max_bytes,
                            uncompressed_cap=uncompressed_cap, diagnostics=diagnostics,
                            progress=self._progress)
            with self._lock:
                self._state.update(
                    state='done', stage='done', stageLabel='Finished',
                    included=result['included'], truncated=result['truncated'],
                    examined=result['examined'], bytesWritten=result['fileBytes'],
                    path=result['path'],
                    message='Exported %d battle(s); %d truncated.'
                            % (result['included'], result['truncated']))
        except BaseException as exc:  # noqa: BLE001 - surfaced verbatim to the UI
            with self._lock:
                self._state.update(state='error', stage='error', stageLabel='Failed',
                                   error=str(exc), message=None)


def start_export(source, destination, count, *, diagnostics=None, max_bytes=MAX_EXPORT_BYTES,
                 uncompressed_cap=UNCOMPRESSED_CAP_BYTES):
    """Validate, create the job and start its thread. Returns the job immediately."""
    requested = normalize_count(count)
    job = DiagnosticExportJob(uuid.uuid4().hex, source, destination, requested)
    thread = threading.Thread(target=job.run, args=(diagnostics, max_bytes, uncompressed_cap),
                              name='strategy-diagnostic-export', daemon=True)
    thread.start()
    return job


__all__ = ('SCHEMA', 'MAX_EXPORT_BYTES', 'UNCOMPRESSED_CAP_BYTES', 'DEFAULT_COUNT', 'MIN_COUNT',
           'MAX_COUNT', 'normalize_count', 'diagnostic_view', 'export', 'start_export',
           'DiagnosticExportJob')
