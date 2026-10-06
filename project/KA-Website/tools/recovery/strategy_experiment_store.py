"""Additive, versioned persistent ledger for encounter-aware strategy experiments.

This module owns the NEW ``ea_*`` tables only. ``initialize`` must be called explicitly on a temporary
or deliberately activated database - never on the production library. Nothing here reads or mutates
the historic game tables (``candidate``/``run``/``archive``) or the earlier unversioned scaffold
tables; those are left untouched.

FROZEN public API (one ordered pair per run):

  * ``reserve(db, experiment_id, candidate_id, seed_pair)``
  * ``complete(db, experiment_id, candidate_id, seed_pair, result)``

``seed_pair`` is EXACTLY ONE ordered pair of two non-bool integers in ``0..2**31-1``, for example
``[123, 456]`` or ``(123, 456)``. A scalar seed, a one/three element sequence, a nested batch such as
``[[123, 456]]`` and any non-integer element are refused with ``ValueError``. ``(123, 456)`` differs
from ``(123, 457)`` and from ``(456, 123)``; ``(123, 123)`` is a valid pair. One pair consumes ONE
run, and a duplicate reserve/complete of the same pair is idempotent.

Guarantees the coordinator relies on:

  * An experiment has an IMMUTABLE intent (scope, parent/reference ids, policy, revisions, purpose,
    fixed/changed fields, planned budget and stopping rule). A content-derived ``request_key``
    coalesces a duplicate request onto the existing row instead of creating a second one.
  * Budget is conserved: reservations and completions are counted, ``reserved <= total``, and a
    finite, exhausted budget cannot revive. A completion is charged once even if it is replayed.
  * One persistent work sample is keyed by candidate + policy + mechanics/encounter revisions +
    ordered seed pair + measurement window. Identical work coalesces across experiments onto ONE
    charged sample; the ``ea_sample_link`` rows record the requesting experiments, the coalescing
    (``reused``) flag and the charged owner. Reusing an already-completed sample immediately exposes
    its result to the requester with no new pending job.
  * Every budget debit + sample write + link write happens in ONE atomic transaction (or savepoint
    when a transaction is already open), so a crash cannot split a session debit from the sample it
    paid for. ``recover`` returns ONE dispatch task per pending sample, never one per requester.
  * A zero-share owner is denied BEFORE any reservation is written, re-checked against the live
    session config so a stale stored share cannot resurrect a disabled owner.
  * Confirmation freezes the nomination/reference/policy/metric/sample plan. A frozen holdout pair
    must be globally fresh - against every development sample and every other confirmation, not just
    the same candidate. Common pairs between the nominee and the comparator inside the SAME frozen
    plan are allowed. Re-freezing the identical plan is idempotent; a different plan for the same
    experiment is refused; a completed holdout run is never overwritten. The comparison budget is
    charged at freeze from the real session (owner, purpose) row, and ``confirmation_report`` exposes
    only counts until every planned pair is complete (no optional-stopping peek).
  * A completion derives its canonical outcome from the STORED intent (policy, mechanics/encounter
    revision, measurement window, candidate), not just ``result['finishPolicy']``, and carries the
    full declared policy in the outcome row for boundary checks. The result seeds must equal the
    reserved ordered pair exactly, so a wrong seed result is refused instead of silently assigned.

Schema 4 keeps ``ea_sample.result/outcome`` and ``ea_holdout.result/outcome`` in their existing
TEXT-affinity columns: legacy values remain readable; new canonical results/outcomes use packed
BLOBs with deployed immutable definitions. Out-of-domain values retain the exact-text codec.
Schema 5 adds an exact compressed full experiment intent beside its thin SQL metadata projection.
Completion predicates and partial-index definitions remain based on SQL NULL exactly as before.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
import hashlib
import itertools
import json
import sqlite3
import time

import strategy_outcomes
import strategy_intent_codec as intent_codec
import strategy_payload_codec as payload_codec
import strategy_battle_binary_shadow as battle_binary_shadow

LEGACY_SCHEMA_VERSION = 3
SCHEMA_V4_VERSION = 4
SCHEMA_VERSION = 5
KINDS = ('fixed', 'compensated', 'support')
#: A seed pair element is a non-bool integer in ``0..MAX_SEED`` (int32 non-negative).
MAX_SEED = 2 ** 31 - 1
#: The session-wide owner key used when a purpose has a single shared budget row.
ANY_OWNER = '*'

# `ea_experiment` intent and scope columns are immutable after creation. Harvest persists several
# completed samples for the same experiment in one batch, so cache those immutable fields only for
# that batch. ContextVar keeps parallel evaluators isolated, and the context manager drops both the
# values and its strong reference to the SQLite connection immediately on exit.
_EXPERIMENT_READ_CACHE = ContextVar('strategy_experiment_read_cache', default=None)

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS ea_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_experiment(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_key TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL,
        parent_id INTEGER,
        reference_id TEXT,
        policy TEXT,
        mechanics_revision TEXT,
        encounter_revision TEXT,
        purpose TEXT,
        owner TEXT,
        owner_share REAL NOT NULL DEFAULT 1.0,
        fixed_fields TEXT NOT NULL,
        changed_fields TEXT NOT NULL,
        planned_budget INTEGER NOT NULL,
        stopping TEXT NOT NULL,
        session_id INTEGER,
        measurement_window TEXT,
        created_at REAL NOT NULL,
        intent_json TEXT,
        full_intent BLOB)''',
    '''CREATE TABLE IF NOT EXISTS ea_experiment_budget(
        experiment_id INTEGER PRIMARY KEY,
        total INTEGER NOT NULL,
        reserved INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS ea_sample(
        sample_key TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        policy TEXT,
        mechanics_revision TEXT,
        encounter_revision TEXT,
        seed_a INTEGER NOT NULL,
        seed_b INTEGER NOT NULL,
        measurement_window TEXT,
        result TEXT,
        outcome TEXT,
        timing TEXT,
        created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_sample_link(
        experiment_id INTEGER NOT NULL,
        candidate_id TEXT NOT NULL,
        seed_a INTEGER NOT NULL,
        seed_b INTEGER NOT NULL,
        sample_key TEXT NOT NULL,
        reused INTEGER NOT NULL DEFAULT 0,
        charged_experiment_id INTEGER,
        created_at REAL NOT NULL,
        PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b))''',
    '''CREATE TABLE IF NOT EXISTS ea_earning_portfolio(
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER, candidate_id TEXT,
        payload TEXT NOT NULL, created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_mechanism_archive(
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER, candidate_id TEXT,
        payload TEXT NOT NULL, created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_boundary(
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER,
        kind TEXT NOT NULL, frozen_reference TEXT, payload TEXT NOT NULL, created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_confirmation(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id INTEGER NOT NULL UNIQUE,
        plan_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_holdout(
        experiment_id INTEGER NOT NULL,
        candidate_id TEXT NOT NULL,
        seed_a INTEGER NOT NULL,
        seed_b INTEGER NOT NULL,
        result TEXT,
        outcome TEXT,
        timing TEXT,
        completed_at REAL,
        PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b))''',
    '''CREATE TABLE IF NOT EXISTS ea_session(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_key TEXT NOT NULL UNIQUE,
        config TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ea_session_budget(
        session_id INTEGER NOT NULL,
        owner TEXT NOT NULL DEFAULT '*',
        purpose TEXT NOT NULL,
        total INTEGER NOT NULL,
        reserved INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(session_id, owner, purpose))''',
    '''CREATE TABLE IF NOT EXISTS ea_session_experiment(
        experiment_id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL,
        owner TEXT,
        purpose TEXT)''',
    # Recovery runs at every fleet boundary. Without these indexes SQLite scans every
    # completed link, then looks up its sample, even when there are no unfinished jobs.
    '''CREATE INDEX IF NOT EXISTS ea_sample_unfinished
        ON ea_sample(sample_key) WHERE result IS NULL''',
    '''CREATE INDEX IF NOT EXISTS ea_sample_link_by_sample
        ON ea_sample_link(sample_key, experiment_id)''',
    # Admission and support diagnosis select a handful of resident candidates. The
    # experiment-first primary key otherwise makes every admission scan all history.
    '''CREATE INDEX IF NOT EXISTS ea_sample_link_by_candidate
        ON ea_sample_link(candidate_id, experiment_id, sample_key)''',
    # Global seed-pair collision guards in reserve/freeze_confirmation must use direct seeks.
    # The primary keys begin with experiment/candidate and otherwise scan every historical row.
    '''CREATE INDEX IF NOT EXISTS ea_sample_link_by_seed_pair
        ON ea_sample_link(seed_a, seed_b)''',
    '''CREATE INDEX IF NOT EXISTS ea_holdout_by_seed_pair
        ON ea_holdout(seed_a, seed_b)''',
)


def _now():
    return time.time()


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)


def _json_or_none(text):
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


_SAVEPOINTS = itertools.count(1)


@contextlib.contextmanager
def _atomic(db):
    """One atomic unit for a multi-write operation.

    Uses ``with db`` (a single transaction) when none is open, or a SAVEPOINT when the caller already
    has a transaction open, so a failure anywhere rolls the whole unit back - a budget debit can never
    be split from the sample it paid for.
    """
    if getattr(db, 'in_transaction', False):
        name = 'ea_sp_%d' % next(_SAVEPOINTS)
        db.execute('SAVEPOINT %s' % name)
        try:
            yield
        except BaseException as original:
            try:
                db.execute('ROLLBACK TO %s' % name)
                db.execute('RELEASE %s' % name)
            except BaseException as cleanup:
                # If code inside the atomic block committed/rolled back the connection, SQLite has
                # already discarded this savepoint. Preserve the operation's real failure instead of
                # replacing it with the cleanup error (which otherwise hides the caller to fix).
                add_note = getattr(original, 'add_note', None)
                if callable(add_note):
                    add_note('savepoint %s rollback cleanup also failed: %s' % (name, cleanup))
                raise original from cleanup
            raise
        else:
            db.execute('RELEASE %s' % name)
    else:
        with db:
            yield


def _pair(seed_pair):
    """Validate and return EXACTLY ONE ordered pair of two non-bool ints in ``0..MAX_SEED``.

    A scalar seed, a sequence whose length is not two, a nested batch and any non-integer/bool or
    out-of-range element are all refused. Equal components are a valid pair.
    """
    if seed_pair is None:
        raise ValueError('a single ordered seed pair is required')
    if isinstance(seed_pair, bool) or isinstance(seed_pair, int):
        raise ValueError('seed_pair must be ONE ordered pair of two integers, not the scalar %r'
                         % (seed_pair,))
    if isinstance(seed_pair, (str, bytes, bytearray)):
        raise ValueError('seed_pair must be ONE ordered pair of two integers, not %r' % (seed_pair,))
    if not isinstance(seed_pair, (list, tuple)):
        raise ValueError('seed_pair must be a list/tuple of exactly two integers, got %r'
                         % (seed_pair,))
    if len(seed_pair) != 2:
        raise ValueError('seed_pair must have exactly two integers (one ordered pair per run); '
                         'got %d element(s): %r (a batch must be split into separate calls)'
                         % (len(seed_pair), seed_pair))
    cleaned = []
    for value in seed_pair:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('seed_pair element %r is not a non-bool integer' % (value,))
        if value < 0 or value > MAX_SEED:
            raise ValueError('seed_pair element %r is outside 0..%d' % (value, MAX_SEED))
        cleaned.append(int(value))
    return (cleaned[0], cleaned[1])


def _pair_list(values):
    """A list of ordered pairs (for one confirmation sample-plan entry)."""
    if values is None or isinstance(values, (str, bytes, bytearray)):
        raise ValueError('seeds must be a list of ordered seed pairs, got %r' % (values,))
    if not isinstance(values, (list, tuple)):
        raise ValueError('seeds must be a list of ordered seed pairs, got %r' % (values,))
    return [_pair(item) for item in values]


def _schema_version(db):
    try:
        row = db.execute("SELECT value FROM ea_meta WHERE key='schema_version'").fetchone()
    except Exception:
        return None
    return row[0] if row is not None else None


def initialize(db):
    """Create the additive ``ea_*`` schema, idempotently. Explicit activation/test databases only."""
    existing = _schema_version(db)
    try:
        parsed_existing = None if existing is None else int(existing)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('unrecognized encounter ledger schema version %r' % (existing,)) from exc
    if parsed_existing is not None and parsed_existing not in (
            LEGACY_SCHEMA_VERSION, SCHEMA_V4_VERSION, SCHEMA_VERSION):
        raise RuntimeError('encounter ledger schema v%s is incompatible with this build (v%d); '
                           'use the explicit schema activation/migration path'
                           % (existing, SCHEMA_VERSION))
    had_ea_schema = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN "
        "('ea_meta','ea_experiment','ea_sample','ea_holdout') LIMIT 1").fetchone() is not None
    for statement in SCHEMA:
        db.execute(statement)
    if 'intent_json' not in {row[1] for row in db.execute('PRAGMA table_info(ea_experiment)')}:
        db.execute("ALTER TABLE ea_experiment ADD COLUMN intent_json TEXT")
    if 'full_intent' not in {row[1] for row in db.execute('PRAGMA table_info(ea_experiment)')}:
        db.execute("ALTER TABLE ea_experiment ADD COLUMN full_intent BLOB")
    # Portfolio refresh filters immutable intents by this exact compatibility field. Without
    # the expression index, every encounter re-parses every historical intent on each new plan.
    db.execute("CREATE INDEX IF NOT EXISTS ea_experiment_compatibility "
               "ON ea_experiment(mechanics_revision, "
               "json_extract(intent_json, '$.compatibility'))")
    if parsed_existing is None:
        # A database with prior ea_* tables but no version marker is treated as legacy TEXT.
        # Only a genuinely new ledger starts at v5; old rows are never rewritten here.
        version = LEGACY_SCHEMA_VERSION if had_ea_schema else SCHEMA_VERSION
        db.execute('INSERT INTO ea_meta(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('schema_version', str(version)))
        return version
    return parsed_existing


def activate_schema_v4(db):
    """Explicit metadata-only opt-in to compressed payload writes for an existing v3 ledger.

    This never rewrites historical rows. Until called, a v3 ledger remains readable and all writes
    remain plain TEXT. An old v3 binary will refuse the v4 marker rather than misread codec BLOBs.
    """
    existing = _schema_version(db)
    if existing is None:
        raise RuntimeError('initialize the encounter ledger before activating schema v4')
    try:
        version = int(existing)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('unrecognized encounter ledger schema version %r' % (existing,)) from exc
    if version in (SCHEMA_V4_VERSION, SCHEMA_VERSION):
        return version
    if version != LEGACY_SCHEMA_VERSION:
        raise RuntimeError('cannot activate schema v4 from encounter ledger schema v%s' % existing)
    with _atomic(db):
        cursor = db.execute("UPDATE ea_meta SET value=? WHERE key='schema_version' AND value=?",
                            (str(SCHEMA_V4_VERSION), str(LEGACY_SCHEMA_VERSION)))
        if cursor.rowcount != 1 and _schema_version(db) != str(SCHEMA_V4_VERSION):
            raise RuntimeError('encounter ledger schema changed during v4 activation')
    return SCHEMA_V4_VERSION


def activate_schema_v5(db):
    """Explicitly enable thin metadata plus exact compressed full-intent writes.

    Initialization adds the nullable column without changing old writers. Existing schema 3/4
    rows stay untouched and continue to use ``intent_json`` as their full-text fallback.
    """
    existing = _schema_version(db)
    if existing is None:
        raise RuntimeError('initialize the encounter ledger before activating schema v5')
    columns = {row[1] for row in db.execute('PRAGMA table_info(ea_experiment)')}
    if not {'intent_json', 'full_intent'} <= columns:
        raise RuntimeError('initialize the encounter ledger before activating schema v5')
    try:
        version = int(existing)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('unrecognized encounter ledger schema version %r' % (existing,)) from exc
    if version == SCHEMA_VERSION:
        return SCHEMA_VERSION
    if version not in (LEGACY_SCHEMA_VERSION, SCHEMA_V4_VERSION):
        raise RuntimeError('cannot activate schema v5 from encounter ledger schema v%s' % existing)
    with _atomic(db):
        cursor = db.execute("UPDATE ea_meta SET value=? WHERE key='schema_version' AND value=?",
                            (str(SCHEMA_VERSION), str(version)))
        if cursor.rowcount != 1 and _schema_version(db) != str(SCHEMA_VERSION):
            raise RuntimeError('encounter ledger schema changed during v5 activation')
    return SCHEMA_VERSION


def _payload_for_storage(db, text, kind='result'):
    """Choose TEXT/BLOB by the activated schema marker; never emit BLOB under v3."""
    version = _schema_version(db)
    if version is None or str(version) == str(LEGACY_SCHEMA_VERSION):
        return text
    if str(version) not in (str(SCHEMA_V4_VERSION), str(SCHEMA_VERSION)):
        raise RuntimeError('cannot write payload with encounter ledger schema v%s' % version)
    return payload_codec.encode_packed_text(text, kind)


def _json_from_stored_payload(value):
    """Decode one result/outcome/timing cell to the JSON value visible to store readers."""
    if payload_codec.has_codec_magic(value):
        value = payload_codec.decode_text(value)
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    return json.loads(value)


def _shadow_context(experiment, experiment_id, candidate_id, pair, *, kind, record_key):
    """Stable sample/holdout identity and intent context for the opt-in sidecar."""
    context = dict(kind=kind, recordKey=record_key, candidateId=candidate_id,
                   seedPair=[pair[0], pair[1]], policy=_json_or_none(experiment.get('policy')),
                   mechanicsRevision=experiment.get('mechanics_revision'),
                   encounterRevision=experiment.get('encounter_revision'),
                   measurementWindow=_json_or_none(experiment.get('measurement_window')),
                   engineRevision=experiment.get('engine_revision'))
    if kind == 'holdout':
        context['experimentId'] = experiment_id
    return context


def _shadow_completed_payload(db, kind, record_key, context, value, primary_where):
    """Best-effort copy; all failures are isolated inside the shadow module."""
    try:
        battle_binary_shadow.record_primary_write(
            db, kind=kind, record_key=record_key, context=context, value=value,
            primary_where=primary_where)
    except Exception as exc:
        battle_binary_shadow.report_failure(kind, record_key, exc)


def _intent_for_storage(db, text):
    """Return ``(intent_json, full_intent)`` for the currently activated schema."""
    version = _schema_version(db)
    if version is None or str(version) in (str(LEGACY_SCHEMA_VERSION), str(SCHEMA_V4_VERSION)):
        return text, None
    if str(version) != str(SCHEMA_VERSION):
        raise RuntimeError('cannot write intent with encounter ledger schema v%s' % version)
    return intent_codec.encode_intent(text)


def _intent_fields(intent):
    required = ('scope', 'planned_budget', 'stopping')
    missing = [name for name in required if intent.get(name) in (None, '')]
    if missing:
        raise ValueError('experiment intent is missing %s' % ', '.join(missing))
    budget = intent['planned_budget']
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError('planned_budget must be a positive integer, got %r' % (budget,))
    share = intent.get('ownerShare', intent.get('owner_share', 1.0))
    if isinstance(share, bool) or not isinstance(share, (int, float)) or share < 0:
        raise ValueError('ownerShare must be a non-negative number, got %r' % (share,))
    return dict(
        scope=str(intent['scope']),
        parent_id=intent.get('parentId', intent.get('parent_id')),
        reference_id=intent.get('referenceId', intent.get('reference_id')),
        policy=_canonical(intent.get('policy')) if intent.get('policy') is not None else None,
        mechanics_revision=intent.get('mechanicsRevision', intent.get('mechanics_revision')),
        encounter_revision=intent.get('encounterRevision', intent.get('encounter_revision')),
        purpose=intent.get('purpose'),
        owner=intent.get('owner', intent.get('ownerStream', intent.get('owner_stream'))),
        owner_share=float(share),
        session_id=intent.get('sessionId', intent.get('session_id')),
        measurement_window=(None if intent.get('measurementWindow', intent.get('measurement_window'))
                            is None else
                            _canonical(intent.get('measurementWindow', intent.get('measurement_window')))),
        fixed_fields=_canonical(intent.get('fixedFields', intent.get('fixed_fields', {}))),
        changed_fields=_canonical(intent.get('changedFields', intent.get('changed_fields', {}))),
        planned_budget=int(budget),
        stopping=_canonical(intent['stopping']),
    )


def _request_key(fields):
    content = {key: value for key, value in fields.items() if key != 'owner_share'}
    return hashlib.sha256(_canonical(content).encode('utf-8')).hexdigest()


def _session_row(db, session_id):
    row = db.execute('SELECT id, config, active FROM ea_session WHERE id=?', (session_id,)).fetchone()
    if row is None:
        raise ValueError('unknown session %r' % (session_id,))
    return dict(session_id=int(row[0]), config=json.loads(row[1]), active=bool(row[2]))


def _budget_rows(config):
    """Explicit ``(owner, purpose, total)`` rows for a validated config.

    A config may carry a full ``budgets`` matrix (``owner -> {purpose: runs}``); otherwise each explicit
    purpose budget becomes a session-wide (``owner='*'``) finite run budget.
    """
    import strategy_search_mode
    rows = {}
    explicit = config.get('budgets')
    if isinstance(explicit, dict) and explicit:
        for owner, purposes in explicit.items():
            if not isinstance(purposes, dict):
                raise ValueError('budgets[%r] must be a purpose -> runs mapping' % (owner,))
            for purpose, total in purposes.items():
                if purpose not in strategy_search_mode.PURPOSES:
                    raise ValueError('unknown purpose %r in budgets' % (purpose,))
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise ValueError('budget %s/%s must be a non-negative integer, got %r'
                                     % (owner, purpose, total))
                rows[(str(owner), purpose)] = int(total)
    else:
        for purpose, total in (config.get('purposes') or {}).items():
            rows[(ANY_OWNER, purpose)] = int(total)
    return rows


def configure_session(db, config, session_id=None):
    """Persist a search-mode config as a session and bind its (owner, purpose) run budgets.

    With ``session_id=None`` a new session is created (an identical ACTIVE config coalesces onto the
    existing session). An existing ``session_id`` may only be re-configured while it is NOT active, so a
    running session's config is never silently mutated. Budget rows UPSERT the total while preserving
    any already-reserved/completed counts, so a resize cannot erase charged work.
    """
    import strategy_search_mode
    strategy_search_mode.validate_config(config)
    request_key = hashlib.sha256(_canonical(config).encode('utf-8')).hexdigest()
    if session_id is None:
        row = db.execute('SELECT id FROM ea_session WHERE request_key=?',
                         (request_key,)).fetchone()
        if row is not None:
            # Explicit activation of an identical paused/rolled-back session retains
            # every prior charge. The UNIQUE request key is not a fresh budget grant.
            with _atomic(db):
                db.execute('UPDATE ea_session SET active=1 WHERE id=?', (row[0],))
            return int(row[0])
        with _atomic(db):
            cursor = db.execute('INSERT INTO ea_session(request_key,config,active,created_at) '
                                'VALUES(?,?,?,?)', (request_key, _canonical(config), 1, _now()))
            session_id = int(cursor.lastrowid)
    else:
        row = db.execute('SELECT active FROM ea_session WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ValueError('unknown session %r' % (session_id,))
        if row[0]:
            raise ValueError('refusing to mutate the config of active session %r; deactivate it or '
                             'open a new session' % (session_id,))
        requested_rows = _budget_rows(config)
        existing_rows = list(db.execute('SELECT owner,purpose,reserved FROM ea_session_budget WHERE session_id=?',
                                        (session_id,)))
        for owner, purpose, reserved in existing_rows:
            if requested_rows.get((owner, purpose), 0) < reserved:
                raise ValueError('New budget cannot erase already reserved work')
        with _atomic(db):
            db.execute('UPDATE ea_session SET request_key=?, config=? WHERE id=?',
                       (request_key, _canonical(config), session_id))
    rows = _budget_rows(config)
    with _atomic(db):
        db.execute('UPDATE ea_session_budget SET total=0 WHERE session_id=? AND reserved=0', (session_id,))
        for (owner, purpose), total in rows.items():
            db.execute('INSERT INTO ea_session_budget(session_id,owner,purpose,total) '
                       'VALUES(?,?,?,?) ON CONFLICT(session_id,owner,purpose) '
                       'DO UPDATE SET total=excluded.total',
                       (session_id, owner, purpose, total))
    return int(session_id)


def deactivate_session(db, session_id):
    """Mark a session inactive so its config may be re-configured; its work stops dispatching."""
    with _atomic(db):
        db.execute('UPDATE ea_session SET active=0 WHERE id=?', (session_id,))


@contextlib.contextmanager
def experiment_read_cache(db):
    """Reuse immutable experiment metadata within one harvest batch, then discard it.

    This is deliberately scoped rather than a process-wide cache: an external library edit, a
    different SQLite connection, or a later harvest always performs a fresh read. Completion may
    update budget counters during the scope, but those are not part of `_experiment`'s cached row.
    """
    token = _EXPERIMENT_READ_CACHE.set((db, {}))
    try:
        yield
    finally:
        _EXPERIMENT_READ_CACHE.reset(token)


def _experiment(db, experiment_id):
    active = _EXPERIMENT_READ_CACHE.get()
    cache = active[1] if active is not None and active[0] is db else None
    if cache is not None and experiment_id in cache:
        # Do not let a caller mutate the batch's shared immutable metadata view.
        return dict(cache[experiment_id])
    row = db.execute('SELECT owner_share, planned_budget, session_id, owner, purpose, policy, '
                     'mechanics_revision, encounter_revision, measurement_window, intent_json '
                     'FROM ea_experiment WHERE id=?', (experiment_id,)).fetchone()
    if row is None:
        raise ValueError('unknown experiment %r' % (experiment_id,))
    experiment = dict(owner_share=float(row[0]), planned_budget=int(row[1]), session_id=row[2],
                      owner=row[3], purpose=row[4], policy=row[5], mechanics_revision=row[6],
                      encounter_revision=row[7], measurement_window=row[8],
                      engine_revision=(json.loads(row[9]).get('engineRevision') if row[9] else None))
    if cache is not None:
        cache[experiment_id] = experiment
        return dict(experiment)
    return experiment


def create_experiment(db, intent):
    """Create (or coalesce onto) one experiment and return its id. The intent is immutable."""
    if not isinstance(intent, dict):
        raise ValueError('intent must be a mapping')
    fields = _intent_fields(intent)
    intent_text = _canonical(intent)
    fields['intent_json'] = intent_text
    if fields['session_id'] is not None:
        _session_row(db, fields['session_id'])
    key = _request_key(fields)
    row = db.execute('SELECT id FROM ea_experiment WHERE request_key=?', (key,)).fetchone()
    if row is not None:
        return int(row[0])
    stored_intent, stored_full_intent = _intent_for_storage(db, intent_text)
    with _atomic(db):
        cursor = db.execute(
            'INSERT INTO ea_experiment(request_key,scope,parent_id,reference_id,policy,'
            'mechanics_revision,encounter_revision,purpose,owner,owner_share,fixed_fields,'
            'changed_fields,planned_budget,stopping,session_id,measurement_window,created_at,'
            'intent_json,full_intent) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, fields['scope'], fields['parent_id'], fields['reference_id'], fields['policy'],
             fields['mechanics_revision'], fields['encounter_revision'], fields['purpose'],
             fields['owner'], fields['owner_share'], fields['fixed_fields'], fields['changed_fields'],
             fields['planned_budget'], fields['stopping'], fields['session_id'],
             fields['measurement_window'], _now(), stored_intent, stored_full_intent))
        experiment_id = int(cursor.lastrowid)
        db.execute('INSERT INTO ea_experiment_budget(experiment_id,total) VALUES(?,?)',
                   (experiment_id, fields['planned_budget']))
        if fields['session_id'] is not None:
            db.execute('INSERT INTO ea_session_experiment(experiment_id,session_id,owner,purpose) '
                       'VALUES(?,?,?,?)',
                       (experiment_id, fields['session_id'], fields['owner'], fields['purpose']))
    return experiment_id


def _effective_share(db, experiment):
    """The owner's CURRENT explicit share: the session config allocation, else the stored share.

    A session-bound experiment is re-checked against the live session config, so a later zero allocation
    disables it and a stale ``owner_share`` cannot resurrect it.
    """
    if experiment.get('session_id') is not None and experiment.get('owner'):
        session = _session_row(db, experiment['session_id'])
        return float((session['config'].get('allocations') or {}).get(experiment['owner'], 0.0))
    return experiment['owner_share']


def _sample_key(experiment, candidate_id, pair):
    """The content identity of one sample. Candidate identity is included, never merged."""
    content = dict(
        candidate=candidate_id, policy=experiment.get('policy'),
        mechanicsRevision=experiment.get('mechanics_revision'),
        encounterRevision=experiment.get('encounter_revision'),
        seedPair=[pair[0], pair[1]],
        measurementWindow=experiment.get('measurement_window'),
    )
    if experiment.get('engine_revision') is not None:
        content['engineRevision'] = experiment['engine_revision']
    return hashlib.sha256(_canonical(content).encode('utf-8')).hexdigest()


def _reserved_count(db, experiment_id):
    links = int(db.execute('SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                           (experiment_id,)).fetchone()[0])
    holdouts = int(db.execute('SELECT COUNT(*) FROM ea_holdout WHERE experiment_id=?',
                              (experiment_id,)).fetchone()[0])
    return links + holdouts


def _recount(db, experiment_id):
    reserved = _reserved_count(db, experiment_id)
    completed = int(db.execute(
        'SELECT COUNT(*) FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key '
        'WHERE l.experiment_id=? AND s.result IS NOT NULL', (experiment_id,)).fetchone()[0])
    completed += int(db.execute('SELECT COUNT(*) FROM ea_holdout WHERE experiment_id=? '
                                'AND result IS NOT NULL', (experiment_id,)).fetchone()[0])
    db.execute('UPDATE ea_experiment_budget SET reserved=?, completed=? WHERE experiment_id=?',
               (reserved, completed, experiment_id))


def _session_owner(db, experiment):
    return experiment['owner'] or ANY_OWNER


def _charge_session_reserve(db, experiment, runs):
    """Debit ``runs`` to the matching (owner, purpose) session budget row, or deny.

    An owner-specific row with total 0 disables that owner and is NOT bypassed by the session-wide row.
    """
    session_id, purpose = experiment['session_id'], experiment['purpose']
    owner = _session_owner(db, experiment)
    row = db.execute('SELECT total, reserved FROM ea_session_budget WHERE session_id=? AND owner=? '
                     'AND purpose=?', (session_id, owner, purpose)).fetchone()
    if row is None and owner != ANY_OWNER:
        owner = ANY_OWNER
        row = db.execute('SELECT total, reserved FROM ea_session_budget WHERE session_id=? AND '
                         'owner=? AND purpose=?', (session_id, owner, purpose)).fetchone()
    if row is None:
        return False
    total, reserved = int(row[0]), int(row[1])
    if total <= 0 or reserved + runs > total:
        return False
    db.execute('UPDATE ea_session_budget SET reserved=reserved+? WHERE session_id=? AND owner=? '
               'AND purpose=?', (runs, session_id, owner, purpose))
    return True


def _charge_session_complete(db, experiment, runs):
    """Credit ``runs`` completions to the matching (owner, purpose) session budget row once."""
    session_id, purpose = experiment['session_id'], experiment['purpose']
    owner = _session_owner(db, experiment)
    found = db.execute('SELECT 1 FROM ea_session_budget WHERE session_id=? AND owner=? AND purpose=?',
                       (session_id, owner, purpose)).fetchone()
    if found is None and owner != ANY_OWNER:
        owner = ANY_OWNER
        found = db.execute('SELECT 1 FROM ea_session_budget WHERE session_id=? AND owner=? AND '
                           'purpose=?', (session_id, owner, purpose)).fetchone()
    if found is None:
        return
    db.execute('UPDATE ea_session_budget SET completed=completed+? WHERE session_id=? AND owner=? '
               'AND purpose=?', (runs, session_id, owner, purpose))


def reserve(db, experiment_id, candidate_id, seed_pair):
    """Reserve EXACTLY ONE ordered seed pair for one battle. False when denied; no partial write.

    Denied before any write when the pair/scalar is invalid, when the owner's CURRENT share is zero,
    when the experiment's session is inactive, when the pair is a frozen holdout pair (development
    never takes holdout pairs), or when the reservation would exceed the finite experiment budget (plus
    the exact (owner, purpose) session run budget when bound). Re-reserving an existing pair is an
    idempotent no-op. Identical cross-experiment work coalesces onto one already-charged sample and
    immediately exposes any completed result to the requester.
    """
    pair = _pair(seed_pair)
    if candidate_id is None or candidate_id == '':
        raise ValueError('candidate_id is required')
    experiment = _experiment(db, experiment_id)
    if _effective_share(db, experiment) <= 0:
        return False
    if experiment['session_id'] is not None and not _session_row(db, experiment['session_id'])['active']:
        return False
    if db.execute('SELECT 1 FROM ea_holdout WHERE seed_a=? AND seed_b=? LIMIT 1', pair).fetchone():
        return False
    sample_key = _sample_key(experiment, candidate_id, pair)
    shadow_sample_key = sample_key
    now = _now()
    shadow_enabled = battle_binary_shadow.enabled()
    shadow_cells = None
    with _atomic(db):
        existing = db.execute('SELECT sample_key FROM ea_sample_link WHERE experiment_id=? AND candidate_id=? '
                              'AND seed_a=? AND seed_b=?',
                              (experiment_id, candidate_id, pair[0], pair[1])).fetchone()
        if existing is not None:
            if shadow_enabled:
                shadow_sample_key = existing[0]
                shadow_cells = db.execute(
                    'SELECT result,outcome,timing FROM ea_sample WHERE sample_key=?',
                    (shadow_sample_key,)).fetchone()
        else:
            if _reserved_count(db, experiment_id) + 1 > experiment['planned_budget']:
                return False
            sample_columns = 'result,outcome,timing' if shadow_enabled else 'result'
            sample = db.execute('SELECT %s FROM ea_sample WHERE sample_key=?' % sample_columns,
                                (sample_key,)).fetchone()
            reused = sample is not None
            if reused:
                if shadow_enabled and sample[0] is not None:
                    shadow_cells = sample
                charge_row = db.execute('SELECT charged_experiment_id FROM ea_sample_link WHERE '
                                        'sample_key=? AND charged_experiment_id IS NOT NULL '
                                        'ORDER BY experiment_id LIMIT 1', (sample_key,)).fetchone()
                charged_experiment_id = int(charge_row[0]) if charge_row else experiment_id
            else:
                charged_experiment_id = experiment_id
            if not reused:
                if experiment['session_id'] is not None and not _charge_session_reserve(db, experiment, 1):
                    return False
                db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
                           'encounter_revision,seed_a,seed_b,measurement_window,created_at) '
                           'VALUES(?,?,?,?,?,?,?,?,?)',
                           (sample_key, candidate_id, experiment['policy'],
                            experiment['mechanics_revision'], experiment['encounter_revision'],
                            pair[0], pair[1], experiment['measurement_window'], now))
            db.execute('INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,'
                       'reused,charged_experiment_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (experiment_id, candidate_id, pair[0], pair[1], sample_key,
                        1 if reused else 0, charged_experiment_id, now))
            _recount(db, experiment_id)
    if shadow_enabled and shadow_cells is not None and shadow_cells[0] is not None:
        try:
            shadow_value = dict(result=_json_from_stored_payload(shadow_cells[0]),
                                outcome=_json_from_stored_payload(shadow_cells[1]),
                                timing=_json_from_stored_payload(shadow_cells[2]))
            shadow_context = _shadow_context(experiment, experiment_id, candidate_id, pair,
                                             kind='sample', record_key=shadow_sample_key)
            _shadow_completed_payload(db, 'sample', shadow_sample_key, shadow_context, shadow_value,
                                      {'sample_key': shadow_sample_key})
        except Exception as exc:
            battle_binary_shadow.report_failure('sample', shadow_sample_key, exc)
    return True


def _outcome_for(experiment, candidate_id, experiment_id, payload):
    """Canonical outcome derived from the STORED intent, not just ``result['finishPolicy']``."""
    outcome = strategy_outcomes.outcome(
        payload, candidate_id=candidate_id,
        encounter_revision=experiment['encounter_revision'],
        mechanics_revision=experiment['mechanics_revision'],
        experiment_id=experiment_id,
        measurement_window=_json_or_none(experiment['measurement_window']),
        policy=_json_or_none(experiment['policy']))
    outcome['engineRevision'] = experiment.get('engine_revision')
    return outcome


def complete(db, experiment_id, candidate_id, seed_pair, result):
    """Complete EXACTLY ONE reserved pair with ONE battle result. False for a pure duplicate replay.

    The result seeds must equal the reserved ordered pair exactly; a wrong/missing seed result is
    refused rather than assigned. A canonical outcome is derived from the experiment's stored policy,
    revisions and measurement window and persisted with the raw result and timing. A completed sample
    is never overwritten: an identical replay is a no-op, a different result is refused.
    """
    pair = _pair(seed_pair)
    experiment = _experiment(db, experiment_id)
    payload = result if isinstance(result, dict) else {}
    try:
        declared = _pair(payload.get('seeds'))
    except ValueError as exc:
        raise ValueError('result must carry the reserved ordered seed pair: %s' % exc)
    if declared != pair:
        raise ValueError('result seeds %r do not match the reserved pair %r'
                         % (list(declared), list(pair)))
    link = db.execute('SELECT sample_key, charged_experiment_id FROM ea_sample_link WHERE '
                      'experiment_id=? AND candidate_id=? AND seed_a=? AND seed_b=?',
                      (experiment_id, candidate_id, pair[0], pair[1])).fetchone()
    if link is None:
        return False
    sample_key, charged_experiment_id = link[0], link[1]
    raw = _canonical(payload)
    shadow_enabled = battle_binary_shadow.enabled()
    duplicate = False
    shadow_cells = None
    with _atomic(db):
        columns = 'result,outcome,timing' if shadow_enabled else 'result'
        row = db.execute('SELECT %s FROM ea_sample WHERE sample_key=?' % columns,
                         (sample_key,)).fetchone()
        if row is None:
            return False
        stored_result = row[0]
        if payload_codec.has_codec_magic(stored_result):
            stored_result = payload_codec.decode_text(stored_result)
        if stored_result is not None:
            if stored_result == raw:
                duplicate = True
                if shadow_enabled:
                    shadow_cells = row
            else:
                raise ValueError('sample %s is already complete; refusing to overwrite with a different '
                                 'result' % sample_key)
        else:
            derived = _outcome_for(experiment, candidate_id, experiment_id, payload)
            timing = payload.get('timing')
            timing = timing if timing is not None else {'completedAt': _now()}
            outcome_text = _canonical(derived)
            timing_text = _canonical(timing)
            db.execute('UPDATE ea_sample SET result=?, outcome=?, timing=? WHERE sample_key=?',
                       (_payload_for_storage(db, raw),
                        _payload_for_storage(db, outcome_text, 'outcome'), timing_text, sample_key))
            if shadow_enabled:
                shadow_cells = (raw, outcome_text, timing_text)
            if charged_experiment_id is not None:
                # Ordinary work is charged to the experiment being completed. Reuse its already
                # validated immutable row instead of querying and parsing the same intent twice.
                charger = (experiment if charged_experiment_id == experiment_id else
                           _experiment(db, charged_experiment_id))
                if charger['session_id'] is not None:
                    _charge_session_complete(db, charger, 1)
            _recount(db, experiment_id)
    # This hook runs only after the primary atomic operation succeeds. The sidecar catches
    # and reports its own errors, so it cannot affect the primary return value or transaction.
    shadow_value = None
    if shadow_cells is not None and shadow_enabled:
        try:
            shadow_value = dict(result=_json_from_stored_payload(shadow_cells[0]),
                                outcome=_json_from_stored_payload(shadow_cells[1]),
                                timing=_json_from_stored_payload(shadow_cells[2]))
        except (TypeError, ValueError, UnicodeError):
            # Preserve primary success even if an old completed row has a partial payload.
            shadow_value = None
    if shadow_value is not None:
        try:
            shadow_context = _shadow_context(experiment, experiment_id, candidate_id, pair,
                                             kind='sample', record_key=sample_key)
            _shadow_completed_payload(db, 'sample', sample_key, shadow_context, shadow_value,
                                      {'sample_key': sample_key})
        except Exception as exc:
            battle_binary_shadow.report_failure('sample', sample_key, exc)
    return not duplicate


class RecoverySnapshot:
    """One campaign-wide view of unfinished samples and their charge-owner eligibility.

    The link table has no index beginning with `sample_key`. Recovery therefore joins its rows once,
    orders by the same sample identity as the reference path, then bulk-loads the small set of related
    experiments and sessions. Coordinators share these ordered rows and per-session views; they never
    issue per-sample link lookups or copy every other session's blocked list.
    """

    _RELATED_BATCH = 800  # below SQLite's historical 999-variable limit

    def __init__(self, db):
        self._db = db
        self._completed_count = None
        self._global_outstanding = []
        link_rows = db.execute(
            'SELECT s.sample_key, s.candidate_id, s.seed_a, s.seed_b, '
            'l.experiment_id, l.charged_experiment_id '
            # Force unfinished samples first. SQLite otherwise starts at the larger link table
            # and probes every completed sample even with both indexes present.
            'FROM ea_sample AS s INDEXED BY ea_sample_unfinished '
            'CROSS JOIN ea_sample_link AS l INDEXED BY ea_sample_link_by_sample '
            'ON l.sample_key=s.sample_key '
            'WHERE s.result IS NULL '
            'ORDER BY s.candidate_id, s.seed_a, s.seed_b, s.sample_key, l.experiment_id').fetchall()

        grouped = []
        experiment_ids = set()
        previous_key = None
        for row in link_rows:
            sample_key, candidate_id, seed_a, seed_b, experiment_id, charged_id = row
            key = (sample_key, candidate_id, int(seed_a), int(seed_b))
            if key != previous_key:
                grouped.append([key, []])
                previous_key = key
            links = grouped[-1][1]
            links.append((int(experiment_id), None if charged_id is None else int(charged_id)))
            experiment_ids.add(int(experiment_id))
            if charged_id is not None:
                experiment_ids.add(int(charged_id))

        self._entries = []
        self._by_session = {}
        if not grouped:
            self.blocked_count = 0
            self.outstanding_count = 0
            return

        experiments = {}
        ids = sorted(experiment_ids)
        for offset in range(0, len(ids), self._RELATED_BATCH):
            batch = ids[offset:offset + self._RELATED_BATCH]
            marks = ','.join('?' for _ in batch)
            for row in db.execute(
                    'SELECT id, owner_share, session_id, owner, purpose '
                    'FROM ea_experiment WHERE id IN (%s)' % marks, batch):
                experiments[int(row[0])] = dict(
                    ownerShare=float(row[1]),
                    sessionId=None if row[2] is None else int(row[2]),
                    owner=row[3], purpose=row[4])

        session_ids = sorted({row['sessionId'] for row in experiments.values()
                              if row['sessionId'] is not None})
        sessions = {}
        for offset in range(0, len(session_ids), self._RELATED_BATCH):
            batch = session_ids[offset:offset + self._RELATED_BATCH]
            marks = ','.join('?' for _ in batch)
            for row in db.execute(
                    'SELECT id, config, active FROM ea_session WHERE id IN (%s)' % marks, batch):
                sessions[int(row[0])] = dict(config=json.loads(row[1]), active=bool(row[2]))

        for (sample_key, candidate_id, seed_a, seed_b), links in grouped:
            experiment_row_ids = [experiment_id for experiment_id, _charged in links]
            chargers = {charged for _experiment_id, charged in links if charged is not None}
            charged = min(chargers) if chargers else min(experiment_row_ids)
            experiment = experiments[charged]
            session_id = experiment['sessionId']
            active = True
            share = experiment['ownerShare']
            if session_id is not None:
                session = sessions[session_id]
                active = session['active']
                if experiment['owner']:
                    share = float((session['config'].get('allocations') or {}).get(
                        experiment['owner'], 0.0))
            entry = dict(sampleKey=sample_key, candidateId=candidate_id,
                         seedPair=[seed_a, seed_b], experimentIds=experiment_row_ids,
                         chargedExperimentId=charged, owner=experiment['owner'],
                         purpose=experiment['purpose'], sessionId=session_id)
            eligible = share > 0 and active
            self._entries.append((entry, eligible))
            if eligible:
                self._global_outstanding.append(entry)
                self._by_session.setdefault(session_id, []).append(entry)
        self.outstanding_count = sum(1 for _entry, eligible in self._entries if eligible)
        self.blocked_count = len(self._entries) - self.outstanding_count

    def coordinator_view(self, session_id):
        """Minimal zero-copy view consumed by one coordinator's recovery dispatch."""
        outstanding = (self._global_outstanding if session_id is None
                       else self._by_session.get(session_id, []))
        return dict(outstanding=outstanding,
                    outstandingCount=len(outstanding),
                    blockedCount=len(self._entries) - len(outstanding))

    def report(self, *, session_id=None, count_completed=True):
        """Materialize the full historical `recover` result shape for API callers and tests."""
        if count_completed and self._completed_count is None:
            self.completed_count(self._db)
        outstanding, blocked = [], []
        for entry, eligible in self._entries:
            if eligible and (session_id is None or entry['sessionId'] == session_id):
                outstanding.append(entry)
            else:
                blocked.append(entry)
        return dict(outstanding=outstanding, outstandingCount=len(outstanding),
                    blocked=blocked, blockedCount=len(blocked),
                    bySample={entry['sampleKey']: entry for entry in outstanding},
                    completedCount=(int(self._completed_count)
                                    if count_completed and self._completed_count is not None else None))

    def completed_count(self, db):
        self._completed_count = int(db.execute(
            'SELECT COUNT(*) FROM ea_sample WHERE result IS NOT NULL').fetchone()[0])
        return self._completed_count


def recover(db, *, session_id=None, count_completed=True):
    """Resume interrupted work from one global joined scan and bulk related-row reads."""
    snapshot = RecoverySnapshot(db)
    return snapshot.report(session_id=session_id, count_completed=count_completed)


def activate_exclusive_session(db, session_id):
    """A campaign has one active allocation policy; preserve all old charges and evidence."""
    _session_row(db, session_id)  # reject nonexistent sessions before changing any state
    with _atomic(db):
        db.execute('UPDATE ea_session SET active=CASE WHEN id=? THEN 1 ELSE 0 END',
                   (session_id,))


def activate_session(db, session_id):
    """Activate one independent campaign session without disabling peer encounters."""
    _session_row(db, session_id)
    with _atomic(db):
        db.execute('UPDATE ea_session SET active=1 WHERE id=?', (session_id,))


def development_reservations(db, experiment_id=None):
    """Development links that are NOT frozen holdout pairs (development queries exclude holdout)."""
    sql = ('SELECT l.experiment_id, l.candidate_id, l.seed_a, l.seed_b, (s.result IS NOT NULL) '
           'FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key '
           'WHERE NOT EXISTS (SELECT 1 FROM ea_holdout h WHERE h.experiment_id=l.experiment_id '
           'AND h.candidate_id=l.candidate_id AND h.seed_a=l.seed_a AND h.seed_b=l.seed_b)')
    args = []
    if experiment_id is not None:
        sql += ' AND l.experiment_id=?'
        args.append(experiment_id)
    sql += ' ORDER BY l.experiment_id, l.candidate_id, l.seed_a, l.seed_b'
    return [dict(experimentId=row[0], candidateId=row[1], seedPair=[int(row[2]), int(row[3])],
                 state=('completed' if row[4] else 'reserved'))
            for row in db.execute(sql, args)]


def outcomes(db, experiment_id=None):
    """Every stored outcome available to an experiment, including reused cross-experiment samples."""
    sql = ('SELECT l.experiment_id, l.candidate_id, l.seed_a, l.seed_b, s.outcome, '
           'l.charged_experiment_id, l.sample_key, (s.result IS NOT NULL) '
           'FROM ea_sample_link l JOIN ea_sample s ON s.sample_key = l.sample_key '
           'WHERE s.outcome IS NOT NULL')
    args = []
    if experiment_id is not None:
        sql += ' AND l.experiment_id=?'
        args.append(experiment_id)
    sql += ' ORDER BY l.experiment_id, l.candidate_id, l.seed_a, l.seed_b'
    return [dict(experimentId=row[0], candidateId=row[1], seedPair=[int(row[2]), int(row[3])],
                 outcome=json.loads(payload_codec.decode_text(row[4])),
                 chargedExperimentId=row[5], sampleKey=row[6],
                 state=('completed' if row[7] else 'reserved'))
            for row in db.execute(sql, args)]


def summary(db):
    """Budget conservation and completion state for every experiment."""
    experiments = []
    for row in db.execute('SELECT id, scope, purpose, planned_budget FROM ea_experiment ORDER BY id'):
        experiment_id, scope, purpose, planned = row
        budget = db.execute('SELECT total, reserved, completed FROM ea_experiment_budget '
                            'WHERE experiment_id=?', (experiment_id,)).fetchone()
        total, reserved, completed = ((int(budget[0]), int(budget[1]), int(budget[2])) if budget
                                      else (planned, 0, 0))
        experiments.append(dict(experimentId=experiment_id, scope=scope, purpose=purpose,
                                total=total, reserved=reserved, completed=completed,
                                outstanding=reserved - completed, remaining=total - reserved,
                                conserved=(reserved <= total and completed <= reserved)))
    return dict(experimentCount=len(experiments),
                totalBudget=sum(entry['total'] for entry in experiments),
                totalReserved=sum(entry['reserved'] for entry in experiments),
                totalCompleted=sum(entry['completed'] for entry in experiments),
                conserved=all(entry['conserved'] for entry in experiments),
                experiments=experiments)


def record_portfolio(db, experiment_id, payload, candidate_id=None):
    with _atomic(db):
        db.execute('INSERT INTO ea_earning_portfolio(experiment_id,candidate_id,payload,created_at) '
                   'VALUES(?,?,?,?)', (experiment_id, candidate_id, _canonical(payload), _now()))


def record_mechanism(db, experiment_id, payload, candidate_id=None):
    with _atomic(db):
        db.execute('INSERT INTO ea_mechanism_archive(experiment_id,candidate_id,payload,created_at) '
                   'VALUES(?,?,?,?)', (experiment_id, candidate_id, _canonical(payload), _now()))


def record_boundary(db, experiment_id, kind, frozen_reference, tested_points, unresolved_gaps,
                    payload=None):
    """Store a boundary record: tested disconnected points, unresolved gaps, fixed/compensated kind.

    A fixed-sensitivity result is NEVER promoted to a safe box or a no-compensation claim; passing
    such an inference is refused rather than stored.
    """
    if kind not in KINDS:
        raise ValueError('boundary kind must be one of %s, got %r' % (KINDS, kind))
    record = dict(kind=kind, frozenReference=frozen_reference,
                  testedPoints=list(tested_points or []), unresolvedGaps=list(unresolved_gaps or []),
                  inference=None)
    merged = dict(payload or {})
    for forbidden in ('safeBox', 'noCompensation', 'no_compensation'):
        if merged.get(forbidden):
            raise ValueError('boundary records may not infer %r from a fixed sensitivity' % forbidden)
    merged.update(record)
    with _atomic(db):
        db.execute('INSERT INTO ea_boundary(experiment_id,kind,frozen_reference,payload,created_at) '
                   'VALUES(?,?,?,?,?)',
                   (experiment_id, kind,
                    None if frozen_reference is None else _canonical(frozen_reference),
                    _canonical(merged), _now()))


def freeze_confirmation(db, experiment_id, nomination, reference, policy, metric, sample_plan):
    """Freeze a confirmation plan and its holdout pairs. Refuses any global pair collision.

    ``sample_plan`` is an iterable of ``{'candidateId': id, 'seeds': [[a, b], ...]}`` entries; ONE
    ordered pair is ONE run. Every proposed pair must be globally fresh: it may not equal any
    development sample pair or any pair frozen by another confirmation, for ANY candidate. Common
    pairs between the nominee and the comparator inside the SAME plan are allowed. Re-freezing the
    identical plan is idempotent; a different plan for the same experiment is refused. The comparison
    budget is charged from the real session (owner, purpose) row at freeze time.
    """
    experiment = _experiment(db, experiment_id)
    if _effective_share(db, experiment) <= 0:
        raise ValueError('experiment %r owner share is zero; cannot freeze a comparison plan'
                         % (experiment_id,))
    if experiment['session_id'] is not None and not _session_row(db, experiment['session_id'])['active']:
        raise ValueError('experiment %r session is inactive; cannot freeze a comparison plan'
                         % (experiment_id,))
    plan = []
    for entry in sample_plan or []:
        candidate = entry.get('candidateId', entry.get('candidate_id'))
        if candidate is None:
            raise ValueError('sample plan entry is missing a candidate id')
        pairs = []
        for item in _pair_list(entry.get('seeds')):
            if item not in pairs:
                pairs.append(item)
        plan.append(dict(candidateId=candidate, pairs=pairs))
    plan_json = [dict(candidateId=entry['candidateId'],
                      seeds=[[p[0], p[1]] for p in entry['pairs']]) for entry in plan]
    runs_planned = sum(len(entry['pairs']) for entry in plan)
    if runs_planned <= 0:
        raise ValueError('a confirmation plan must predeclare at least one run')
    plan_key = hashlib.sha256(_canonical(dict(experimentId=experiment_id, nomination=nomination,
                                              reference=reference, policy=policy, metric=metric,
                                              samplePlan=plan_json)).encode('utf-8')).hexdigest()
    existing = db.execute('SELECT id, plan_key FROM ea_confirmation WHERE experiment_id=?',
                          (experiment_id,)).fetchone()
    if existing is not None:
        if existing[1] == plan_key:
            return int(existing[0])
        raise ValueError('experiment %r already froze a DIFFERENT confirmation plan; a plan is '
                         'immutable' % (experiment_id,))
    for entry in plan:
        for pair in entry['pairs']:
            if db.execute('SELECT 1 FROM ea_sample_link WHERE seed_a=? AND seed_b=? LIMIT 1',
                          pair).fetchone():
                raise ValueError('holdout pair %r collides with a development sample'
                                 % (list(pair),))
            if db.execute('SELECT 1 FROM ea_holdout WHERE seed_a=? AND seed_b=? LIMIT 1',
                          pair).fetchone():
                raise ValueError('holdout pair %r is already frozen in another confirmation'
                                 % (list(pair),))
    if _reserved_count(db, experiment_id) + runs_planned > experiment['planned_budget']:
        raise ValueError('comparison plan needs %d runs but experiment %r has only %d remaining'
                         % (runs_planned, experiment_id,
                            experiment['planned_budget'] - _reserved_count(db, experiment_id)))
    payload = dict(nomination=nomination, reference=reference, policy=policy, metric=metric,
                   samplePlan=plan_json, runsPlanned=runs_planned)
    now = _now()
    with _atomic(db):
        if experiment['session_id'] is not None and not _charge_session_reserve(db, experiment,
                                                                                runs_planned):
            raise ValueError('comparison budget denied for experiment %r' % (experiment_id,))
        cursor = db.execute('INSERT INTO ea_confirmation(experiment_id,plan_key,payload,created_at) '
                            'VALUES(?,?,?,?)', (experiment_id, plan_key, _canonical(payload), now))
        confirmation_id = int(cursor.lastrowid)
        for entry in plan:
            for pair in entry['pairs']:
                db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                           'VALUES(?,?,?,?)',
                           (experiment_id, entry['candidateId'], pair[0], pair[1]))
        _recount(db, experiment_id)
    return confirmation_id


def record_holdout_result(db, experiment_id, candidate_id, seed_pair, result):
    """Complete ONE frozen holdout pair. Only a predeclared pair may be recorded, exactly once.

    A duplicate identical result is an idempotent no-op; a different result for a completed pair is
    refused (no overwrite). A pair that was never frozen is refused.
    """
    pair = _pair(seed_pair)
    experiment = _experiment(db, experiment_id)
    payload = result if isinstance(result, dict) else {}
    try:
        declared = _pair(payload.get('seeds'))
    except ValueError as exc:
        raise ValueError('holdout result must carry the frozen ordered seed pair: %s' % exc)
    if declared != pair:
        raise ValueError('holdout result seeds %r do not match the frozen pair %r'
                         % (list(declared), list(pair)))
    raw = _canonical(payload)
    shadow_enabled = battle_binary_shadow.enabled()
    duplicate = False
    shadow_cells = None
    with _atomic(db):
        columns = 'result,outcome,timing' if shadow_enabled else 'result'
        row = db.execute('SELECT %s FROM ea_holdout WHERE experiment_id=? AND candidate_id=? '
                         'AND seed_a=? AND seed_b=?' % columns,
                         (experiment_id, candidate_id, pair[0], pair[1])).fetchone()
        if row is None:
            raise ValueError('holdout pair %r was not frozen for experiment %r'
                             % (list(pair), experiment_id))
        stored_result = row[0]
        if payload_codec.has_codec_magic(stored_result):
            stored_result = payload_codec.decode_text(stored_result)
        if stored_result is not None:
            if stored_result == raw:
                duplicate = True
                if shadow_enabled:
                    shadow_cells = row
            else:
                raise ValueError('holdout pair %r is already complete; refusing to overwrite'
                                 % (list(pair),))
        else:
            derived = _outcome_for(experiment, candidate_id, experiment_id, payload)
            timing = payload.get('timing')
            timing = timing if timing is not None else {'completedAt': _now()}
            outcome_text = _canonical(derived)
            timing_text = _canonical(timing)
            db.execute('UPDATE ea_holdout SET result=?, outcome=?, timing=?, completed_at=? '
                       'WHERE experiment_id=? AND candidate_id=? AND seed_a=? AND seed_b=?',
                       (_payload_for_storage(db, raw), _payload_for_storage(db, outcome_text, 'outcome'),
                        timing_text, _now(), experiment_id, candidate_id, pair[0], pair[1]))
            if shadow_enabled:
                shadow_cells = (raw, outcome_text, timing_text)
            if experiment['session_id'] is not None:
                _charge_session_complete(db, experiment, 1)
            _recount(db, experiment_id)
    shadow_value = None
    if shadow_cells is not None and shadow_enabled:
        try:
            shadow_value = dict(result=_json_from_stored_payload(shadow_cells[0]),
                                outcome=_json_from_stored_payload(shadow_cells[1]),
                                timing=_json_from_stored_payload(shadow_cells[2]))
        except (TypeError, ValueError, UnicodeError):
            # A legacy partial row is still a successful no-op for the primary store.
            shadow_value = None
    if shadow_value is not None:
        try:
            record_identity = hashlib.sha256(
                _canonical(dict(experimentId=experiment_id, candidateId=candidate_id,
                                seedPair=[pair[0], pair[1]])).encode('utf-8')).hexdigest()
            shadow_context = _shadow_context(experiment, experiment_id, candidate_id, pair,
                                             kind='holdout', record_key=record_identity)
            _shadow_completed_payload(db, 'holdout', record_identity, shadow_context, shadow_value,
                                      {'experiment_id': experiment_id, 'candidate_id': candidate_id,
                                       'seed_a': pair[0], 'seed_b': pair[1]})
        except Exception as exc:
            battle_binary_shadow.report_failure('holdout', 'pending-identity', exc)
    return not duplicate


def confirmation_report(db, experiment_id):
    """Report a frozen confirmation. Until EVERY predeclared run is complete it returns no metric.

    This is the no-early-peek contract: a partially completed holdout exposes only ``ready=False``, the
    planned/completed counts and the frozen metric *name*, never a per-run score that could be used to
    peek at early completion.
    """
    return _confirmation_view(db, experiment_id, include_outcomes=True)


def confirmation_progress(db, experiment_id):
    """Exact frozen-plan readiness for scheduling, without reading any holdout outcome.

    This follows the same planned pair membership and duplicate/error behavior as the public
    report. The scheduler needs counts even when a plan is ready, but never needs its evidence
    payloads. Keep full outcome hydration at the explicit confirmation/publication boundary.
    """
    return _confirmation_view(db, experiment_id, include_outcomes=False)


def _confirmation_view(db, experiment_id, *, include_outcomes):
    row = db.execute('SELECT id, payload FROM ea_confirmation WHERE experiment_id=?',
                     (experiment_id,)).fetchone()
    if row is None:
        return dict(frozen=False, experimentId=experiment_id)
    payload = json.loads(row[1])
    planned = [(entry['candidateId'], (int(p[0]), int(p[1])))
               for entry in (payload.get('samplePlan') or []) for p in entry['seeds']]
    # Selecting NULL keeps identical tuple/membership handling without copying TEXT/BLOB evidence
    # out of SQLite. It also prevents a ready scheduling gate from decoding every outcome again.
    outcome_column = 'outcome' if include_outcomes else 'NULL'
    observed = {(candidate_id, int(seed_a), int(seed_b)): outcome
                for candidate_id, seed_a, seed_b, outcome in db.execute(
                    'SELECT candidate_id, seed_a, seed_b, ' + outcome_column + ' FROM ea_holdout '
                    'WHERE experiment_id=? AND result IS NOT NULL', (experiment_id,))}
    completed = []
    for candidate_id, pair in planned:
        key = (candidate_id, pair[0], pair[1])
        if key in observed:
            completed.append((candidate_id, pair, observed[key]))
    ready = bool(planned) and len(completed) == len(planned)
    report = dict(frozen=True, experimentId=experiment_id, confirmationId=int(row[0]),
                  metric=payload.get('metric'), runsPlanned=len(planned),
                  runsCompleted=len(completed), ready=ready, earlyPeek=False)
    if ready and include_outcomes:
        report['outcomes'] = [json.loads(payload_codec.decode_text(item[2]))
                              for item in sorted(completed)]
    return report


def session_status(db, session_id):
    """Active flag plus the exact (owner, purpose) budget rows and their conservation state."""
    session = _session_row(db, session_id)
    budgets = [dict(owner=row[0], purpose=row[1], total=row[2], reserved=row[3], completed=row[4],
                    conserved=(row[3] <= row[2] and row[4] <= row[3]))
               for row in db.execute('SELECT owner, purpose, total, reserved, completed '
                                     'FROM ea_session_budget WHERE session_id=? '
                                     'ORDER BY owner, purpose', (session_id,))]
    return dict(sessionId=session_id, active=session['active'], budgets=budgets,
                conserved=all(entry['conserved'] for entry in budgets) if budgets else True)


def experiment_intent_raw(db, experiment_id):
    """The exact immutable intent text, decoding a v5 BLOB or using the legacy TEXT fallback."""
    try:
        row = db.execute('SELECT intent_json, full_intent FROM ea_experiment WHERE id=?',
                         (experiment_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        # Read-only viewers may open an older library before initialize() has added the nullable v5
        # column. Keep those files readable without changing their schema or activation marker.
        if 'full_intent' not in str(exc):
            raise
        row = db.execute('SELECT intent_json FROM ea_experiment WHERE id=?',
                         (experiment_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown experiment')
    if len(row) < 2:
        return row[0]
    return intent_codec.decode_intent(row[0], row[1])


def experiment_intent(db, experiment_id):
    """Full immutable proposal/question intent, including explanation and stopping plan."""
    raw = experiment_intent_raw(db, experiment_id)
    return json.loads(raw) if raw else None


def experiment_intent_json_extract(db, experiment_id, path):
    """Apply SQLite JSON1 ``json_extract`` semantics to the exact full intent at one path.

    Boundary readers use this for fields whose first-duplicate behavior is part of the old SQL
    gate. Python's regular ``json.loads`` intentionally retains its separate last-duplicate result.
    """
    raw = experiment_intent_raw(db, experiment_id)
    if raw is None:
        return None
    row = db.execute('SELECT json_extract(?, ?)', (raw, path)).fetchone()
    return row[0] if row is not None else None


def pending_sample(db, experiment_id, candidate_id, seed_pair):
    """Indexed dispatch eligibility for ONE existing sample; no whole-ledger recovery scan."""
    pair = _pair(seed_pair)
    row = db.execute('SELECT l.sample_key, l.charged_experiment_id, s.result '
                     'FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key '
                     'WHERE l.experiment_id=? AND l.candidate_id=? AND l.seed_a=? AND l.seed_b=?',
                     (experiment_id, candidate_id, pair[0], pair[1])).fetchone()
    if row is None or row[2] is not None or row[1] != experiment_id: return None
    experiment = _experiment(db, experiment_id)
    if _effective_share(db, experiment) <= 0: return None
    if experiment['session_id'] is not None and not _session_row(db, experiment['session_id'])['active']: return None
    return dict(sampleKey=row[0], chargedExperimentId=experiment_id, candidateId=candidate_id, seedPair=list(pair))
