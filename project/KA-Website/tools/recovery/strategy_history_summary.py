"""Standalone compact *history summary* library builder for the strategy optimiser.

This is an additive migration tool, not a new optimiser. It reads one or more existing libraries
read-only and writes a fresh, *small* working library that remembers the useful measured
conclusions (per exact build + encounter + full resource policy + engine-compat group + evidence
window) plus a compact, versioned record of the global past-seed exclusions. It deliberately does
not copy millions of raw runs, replay blobs, old archive rankings or the old active campaign
configuration.

Enforced rules:

* Sources are opened ``mode=ro`` with ``PRAGMA query_only`` inside one consistent read transaction
  per source. The tool never writes, VACUUMs, migrates or reconfigures a source, and never guesses
  a default/live path: every ``--source`` is caller-supplied.
* The destination is a NEW path (exclusive). A failure writes only to a clearly named
  ``<out>.partial`` staging file that is never a valid library, and never overwrites the
  destination. ``--authorised`` is required for a real import.
* The destination schema is created through the canonical ``strategy_optimizer.Store`` initializer
  (a valid, openable optimiser library with ``totalRuns`` 0 and a *current* provenance), then this
  module adds its own versioned ``history_*`` tables. No optimiser loop, coordinator or battle runs.
* One measurement per retained candidate/phase is decoded with the canonical
  ``strategy_evidence.decode`` and turned into an outcome with the canonical
  ``strategy_outcomes.outcome``. Unknown/unresolved/censored outcomes stay unknown; a full mean is
  only reported when every considered reading resolved. Lower-reward and failed histories are kept.
* Overlapping measurements across sources are deduplicated on (immutable candidate identity,
  policy, engine-compat group, phase, ordered seed pair). The same identity with a different earned
  number becomes ``conflict``/unknown: no winner is picked.
* Old aggregate scores and archive rankings are never treated as evidence. Lifetime counters are
  reported next to - never expanded into - the actual retained measurements.
* Seed identity is preserved compactly: per-phase deterministic bank ceilings plus declared
  probe/fineTune/average/breakthrough/mpRecovery ceilings and every proven non-bank ("exception")
  pair. Off-bank pairs are safely excludable, so they never block a claim by themselves; only
  malformed/unreadable seed identity sets ``holdout_blocked``. Coverage is reported as
  established/unknown counts and the strongest honest statement is "no collision with established
  historical coverage", never universal freshness.
* Each compatible group keeps the exact reward histogram *plus* a versioned, zlib-compressed
  canonical seed-outcome block (ordered seed pair or a known-missing marker, phase, canonical
  earned/status/loss/censor/error/pending/basis and per-observation source attribution) so the
  dedup can be audited later. The blocks are a bounded streaming JSON-lines codec with count and
  sha256 integrity checks; a corrupt block fails closed. No full replay is stored.
* Every historical candidate scenario (also from ``predictor_history`` when a candidate was pruned)
  is kept losslessly compressed in ``history_scenario`` and its attribution/lineage in
  ``history_candidate``. Only a bounded, diverse slice becomes an active resident in the canonical
  ``candidate``/``candidate_meta``/``lineage`` tables (a supplied Community reference per encounter,
  then a round-robin of engine/stat strata, <= 24/encounter and <= 640 total), so a fresh import is a
  small working library rather than every historic build. The new-campaign counters stay at zero, no
  old ranking/score is imported, and an active build's ``stats`` are recomputed in the current
  canonical runtime. A candidate id is validated against the canonical identity of its scenario; an
  alias dedups, an inconsistent id is never merged silently.

    python strategy_history_summary.py --source <a.sqlite> --source <b.sqlite> \
        --out <fresh.sqlite> --authorised

    from strategy_history_summary import build_library, load_observations, forbidden_pairs

Deterministic checks live in ``check_history_summary.py`` (tiny fixtures only; no simulator, engine,
live library, real import or battle). Integration into the optimiser is deliberately not done here.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import hashlib
import json
import math
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
import zlib
from pathlib import Path
from urllib.request import pathname2url

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # import the recovery siblings when run from anywhere
    sys.path.insert(0, str(HERE))

import strategy_build_domain as domain
import strategy_evidence as evidence_service
import strategy_outcomes as outcomes
import strategy_legacy_observations as legacy
import strategy_seed_freshness as freshness

VERSION = 'strategy-history-summary-2'
TOOL_VERSION = VERSION
#: Version of the ``history_*`` table layout and its meaning.
SUMMARY_VERSION = 2
#: Version of the compressed seed-outcome block layout and its codec.
BLOCK_VERSION = 1
BLOCK_CODEC = 'zlib-jsonl-1'
#: Bounded decode guards: a block must never expand without limit.
BLOCK_MAX_RECORDS = 5_000_000
BLOCK_MAX_BYTES = 1 << 31

PHASES = tuple(freshness.PHASES)
VALIDATION_PHASE = legacy.VALIDATION_PHASE
DISCOVERY_PHASE = 'discovery'
DEFAULT_WINDOW = legacy.MEASUREMENT_WINDOW  # 'legacy:validation'
MEASUREMENT_WINDOW = DEFAULT_WINDOW
DEFAULT_BATCH = 2000
SCRATCH_CHUNK = 4096
REWARD_THRESHOLDS = (1, 2, 3, 5, 10, 25, 50, 100)

EVIDENCE_COLUMNS = legacy.EVIDENCE_COLUMNS
POLICY_KEYS = legacy.POLICY_KEYS

STATUS_MEASURED = 'measured'
STATUS_PARTIAL = 'measured-partial'
STATUS_UNCONFIRMED = 'historical-unconfirmed'
STATUS_CONFLICT = 'conflict'

PROVENANCE_CLASS = 'history-summary-prior'
EVIDENCE_CLASS = 'historical-prior'

#: The real optimiser store's active-population ceilings, mirrored here so the migration can bound
#: the recovered working set itself. Every historic build is kept losslessly compressed in
#: `history_scenario` (and its attribution in `history_candidate`), but only a small, diverse
#: slice becomes an active resident starting build: a supplied Community reference per encounter,
#: then a round-robin draw across engine/stat strata ordered by retained sample count. This is what
#: keeps a fresh import a small working library instead of resurrecting every pruned build.
MAX_CANDIDATES_TOTAL = 640
MAX_CANDIDATES_PER_ENCOUNTER = 24
#: Codec tag for the lossless compressed `history_scenario` payloads.
SCENARIO_CODEC = 'zlib-json-1'

_HISTORY_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS history_source(
        source_id INTEGER PRIMARY KEY,
        source_path TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        ownership TEXT NOT NULL,
        source_sha256 TEXT,
        page_count INTEGER,
        library_schema INTEGER,
        objective_version INTEGER,
        search_space_version INTEGER,
        provenance_digest TEXT,
        provenance_json TEXT,
        lifetime_total_runs INTEGER,
        lifetime_timed_runs INTEGER,
        retained_runs INTEGER,
        evidence_rows INTEGER,
        pruned_ordinals TEXT,
        custom_seed_identity INTEGER NOT NULL DEFAULT 0,
        holdout_blocked INTEGER NOT NULL DEFAULT 0,
        migration_grant INTEGER NOT NULL DEFAULT 0,
        migration_grant_json TEXT NOT NULL DEFAULT '{}',
        snapshot_note TEXT NOT NULL DEFAULT '',
        imported_at REAL NOT NULL,
        tool_version TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS history_build_summary(
        summary_id INTEGER PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        scenario_digest TEXT NOT NULL,
        identity_json TEXT NOT NULL,
        encounter_id INTEGER NOT NULL,
        defeat_count INTEGER NOT NULL,
        policy_key TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        engine_compat_group TEXT NOT NULL,
        engine_digest TEXT,
        engine_provenance_json TEXT NOT NULL,
        phase TEXT NOT NULL,
        evidence_window TEXT NOT NULL,
        status TEXT NOT NULL,
        mean_earned REAL,
        partial_mean_earned REAL,
        resolved_count INTEGER NOT NULL,
        unknown_count INTEGER NOT NULL,
        censored_count INTEGER NOT NULL,
        error_count INTEGER NOT NULL,
        loss_count INTEGER NOT NULL,
        sample_count INTEGER NOT NULL,
        retained_measurements INTEGER NOT NULL,
        reward_histogram_json TEXT NOT NULL,
        moments_json TEXT NOT NULL,
        duplicate_count INTEGER NOT NULL DEFAULT 0,
        conflicting_rewards INTEGER NOT NULL DEFAULT 0,
        historical_confirmed INTEGER NOT NULL DEFAULT 0,
        provenance_json TEXT NOT NULL,
        UNIQUE(candidate_id,policy_key,engine_compat_group,phase,evidence_window))''',
    '''CREATE TABLE IF NOT EXISTS history_seed_exclusion(
        exclusion_id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL,
        phase TEXT NOT NULL,
        bank_ceiling INTEGER NOT NULL,
        aggregate_ceiling INTEGER,
        declared_json TEXT NOT NULL,
        observed_pairs INTEGER NOT NULL,
        bank_pairs INTEGER NOT NULL,
        exception_pairs INTEGER NOT NULL,
        custom_seed_identity INTEGER NOT NULL DEFAULT 0,
        holdout_blocked INTEGER NOT NULL DEFAULT 0,
        malformed_pairs INTEGER NOT NULL DEFAULT 0,
        missing_seed_rows INTEGER NOT NULL DEFAULT 0,
        coverage_json TEXT NOT NULL DEFAULT '{}',
        provenance_json TEXT NOT NULL,
        UNIQUE(source_id,phase))''',
    '''CREATE TABLE IF NOT EXISTS history_seed_exception(
        source_id INTEGER NOT NULL,
        seed_a INTEGER NOT NULL,
        seed_b INTEGER NOT NULL,
        PRIMARY KEY(source_id,seed_a,seed_b)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS history_omission(
        omission_id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        count INTEGER NOT NULL,
        detail TEXT NOT NULL DEFAULT '')''',
    '''CREATE TABLE IF NOT EXISTS history_aggregate(
        aggregate_id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL,
        candidate_id TEXT NOT NULL,
        scenario_digest TEXT NOT NULL,
        encounter_id INTEGER,
        defeat_count INTEGER,
        phase TEXT NOT NULL,
        raw_json TEXT NOT NULL,
        fields_json TEXT NOT NULL,
        measurement_count INTEGER,
        has_seed_identity INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '')''',
    '''CREATE TABLE IF NOT EXISTS history_candidate(
        candidate_id TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        scenario_digest TEXT NOT NULL,
        stored_id TEXT NOT NULL,
        identity_verified INTEGER NOT NULL,
        alias_of TEXT,
        recovered_from TEXT NOT NULL,
        encounter_id INTEGER,
        defeat_count INTEGER,
        policy_key TEXT,
        policy_json TEXT,
        label TEXT,
        origin_source TEXT,
        source_label TEXT,
        source_ownership TEXT NOT NULL,
        source_path TEXT,
        root TEXT,
        parent TEXT,
        depth INTEGER,
        depth_known INTEGER NOT NULL DEFAULT 0,
        operation TEXT,
        target TEXT,
        change TEXT,
        proposal INTEGER,
        search_space_version INTEGER,
        created INTEGER,
        PRIMARY KEY(candidate_id,source_id,stored_id)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS history_scenario(
        candidate_id TEXT PRIMARY KEY,
        scenario_digest TEXT NOT NULL,
        encounter_id INTEGER,
        defeat_count INTEGER,
        codec TEXT NOT NULL,
        plain_sha256 TEXT NOT NULL,
        compressed_bytes INTEGER NOT NULL,
        uncompressed_bytes INTEGER NOT NULL,
        scenario_zlib BLOB NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS history_seed_block(
        block_id INTEGER PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        scenario_digest TEXT NOT NULL,
        policy_key TEXT NOT NULL,
        engine_compat_group TEXT NOT NULL,
        block_version INTEGER NOT NULL,
        codec TEXT NOT NULL,
        phase_list_json TEXT NOT NULL,
        record_count INTEGER NOT NULL,
        observation_count INTEGER NOT NULL,
        plain_sha256 TEXT NOT NULL,
        compressed_bytes INTEGER NOT NULL,
        uncompressed_bytes INTEGER NOT NULL,
        histogram_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        block_zlib BLOB NOT NULL,
        UNIQUE(candidate_id,policy_key,engine_compat_group))''',
)

_SCRATCH_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS stage(
        ident TEXT NOT NULL,
        policy TEXT NOT NULL,
        engine TEXT NOT NULL,
        phase TEXT NOT NULL,
        k_a INTEGER NOT NULL,
        k_b INTEGER NOT NULL,
        seed_a INTEGER,
        seed_b INTEGER,
        has_seed INTEGER NOT NULL,
        earned REAL,
        status TEXT NOT NULL,
        basis TEXT,
        pending REAL,
        loss INTEGER NOT NULL DEFAULT 0,
        censored INTEGER NOT NULL DEFAULT 0,
        error INTEGER NOT NULL DEFAULT 0,
        duplicates INTEGER NOT NULL DEFAULT 0,
        conflicts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(ident,policy,engine,phase,k_a,k_b)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS stage_source(
        ident TEXT NOT NULL,
        policy TEXT NOT NULL,
        engine TEXT NOT NULL,
        phase TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        candidate_id TEXT NOT NULL,
        PRIMARY KEY(ident,policy,engine,phase,source_id)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS stage_scenario(
        ident TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        encounter_id INTEGER NOT NULL,
        defeat_count INTEGER NOT NULL,
        policy_key TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        identity_json TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS stage_obs(
        ident TEXT NOT NULL,
        policy TEXT NOT NULL,
        engine TEXT NOT NULL,
        phase TEXT NOT NULL,
        k_a INTEGER NOT NULL,
        k_b INTEGER NOT NULL,
        has_seed INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        candidate_id TEXT NOT NULL,
        ordinal INTEGER,
        earned REAL,
        status TEXT NOT NULL,
        basis TEXT,
        pending REAL,
        loss INTEGER NOT NULL DEFAULT 0,
        censored INTEGER NOT NULL DEFAULT 0,
        error INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(ident,policy,engine,phase,k_a,k_b,source_id,ordinal)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS evidence_row(
        candidate TEXT NOT NULL,
        phase TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        PRIMARY KEY(candidate,phase,ordinal)) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS seed_obs(
        seed_a INTEGER NOT NULL,
        seed_b INTEGER NOT NULL,
        PRIMARY KEY(seed_a,seed_b)) WITHOUT ROWID''',
)

_STAGE_UPSERT = '''INSERT INTO stage(ident,policy,engine,phase,k_a,k_b,seed_a,seed_b,has_seed,
        earned,status,basis,pending,loss,censored,error,duplicates,conflicts)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
    ON CONFLICT(ident,policy,engine,phase,k_a,k_b) DO UPDATE SET
      duplicates = stage.duplicates + (CASE WHEN stage.earned IS excluded.earned
                                            AND stage.status = excluded.status THEN 1 ELSE 0 END),
      conflicts = stage.conflicts + (CASE WHEN stage.earned IS NOT excluded.earned
                                          OR stage.status IS NOT excluded.status THEN 1 ELSE 0 END),
      earned = CASE WHEN stage.earned IS excluded.earned AND stage.status = excluded.status
                    THEN stage.earned ELSE NULL END,
      status = CASE WHEN stage.earned IS excluded.earned AND stage.status = excluded.status
                    THEN stage.status ELSE 'conflict' END,
      basis = CASE WHEN stage.earned IS excluded.earned AND stage.status = excluded.status
                    THEN stage.basis ELSE NULL END,
      pending = CASE WHEN stage.earned IS excluded.earned AND stage.status = excluded.status
                    THEN stage.pending ELSE NULL END'''

_STAGE_OBS_INSERT = '''INSERT OR IGNORE INTO stage_obs(ident,policy,engine,phase,k_a,k_b,has_seed,
        source_id,candidate_id,ordinal,earned,status,basis,pending,loss,censored,error)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''


class HistorySummaryError(RuntimeError):
    """The import could not be completed; any staging left behind is clearly partial."""


class NotAuthorised(HistorySummaryError):
    """A real import was requested without ``--authorised``."""


# --- small helpers ------------------------------------------------------------------------------
def _canonical(value):
    return domain.canonical(value)


def _sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _window(phase):
    return 'legacy:' + str(phase)


def _rows_as_dicts(cursor):
    """Yield rows as dicts regardless of the connection's row_factory (loader safety)."""
    names = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(names, row))


# --- compact seed-outcome block codec -------------------------------------------------------------
#: Field order of one record in the compressed block (identity/policy/engine are block-level).
_BLOCK_FIELDS = ('phase', 'seedA', 'seedB', 'earned', 'status', 'basis', 'pending', 'loss',
                 'censored', 'error', 'duplicates', 'conflicts', 'observations')
#: Field order of one per-observation audit record.
_OBS_FIELDS = ('sourceId', 'ordinal', 'earned', 'status', 'basis', 'pending')


def _block_record_list(record):
    """JSON-ready compact array for one deduplicated seed-outcome record."""
    observations = []
    for obs in record.get('observations') or ():
        if isinstance(obs, (list, tuple)):
            observations.append(list(obs))
        else:
            observations.append([obs.get('sourceId'), obs.get('ordinal'), obs.get('earned'),
                                 obs.get('status'), obs.get('basis'), obs.get('pending')])
    return [record.get('phase'), record.get('seedA'), record.get('seedB'), record.get('earned'),
            record.get('status'), record.get('basis'), record.get('pending'),
            int(record.get('loss') or 0), int(record.get('censored') or 0),
            int(record.get('error') or 0), int(record.get('duplicates') or 0),
            int(record.get('conflicts') or 0), observations]


def _encode_block(records):
    """Compress records to a versioned zlib JSON-lines blob with count/sha256 integrity.

    Returns ``(blob, plain_sha256, record_count, uncompressed_bytes)``. The identity, policy and
    engine-compatible group live in the destination row, not on every record, so a retained run is
    one short line rather than a verbose identity object.
    """
    digest = hashlib.sha256()
    plain = []
    count = 0
    for record in records:
        line = json.dumps(_block_record_list(record), separators=(',', ':'),
                          sort_keys=False).encode('utf-8') + b'\n'
        plain.append(line)
        digest.update(line)
        count += 1
    raw = b''.join(plain)
    return zlib.compress(raw, 6), digest.hexdigest(), count, len(raw)


def _decode_block(blob, *, expected_count=None, expected_sha256=None):
    """Bounded streaming decode with fail-closed integrity and count checks.

    A corrupt blob, a sha256 mismatch, a record-count mismatch or an expansion beyond the guards
    raises :class:`HistorySummaryError`; a caller must never read a partially valid block as truth.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise HistorySummaryError('seed block is not a byte string')
    try:
        decompressor = zlib.decompressobj()
    except zlib.error as exc:  # pragma: no cover - construction never fails in practice
        raise HistorySummaryError('seed block codec unavailable: %s' % exc)
    digest = hashlib.sha256()
    buffer = b''
    records = []
    produced = 0
    view = memoryview(blob)
    try:
        for start in range(0, len(view), 1 << 16):
            piece_in = bytes(view[start:start + (1 << 16)])
            while piece_in:
                piece = decompressor.decompress(piece_in, 1 << 16)
                piece_in = decompressor.unconsumed_tail
                if not piece:
                    break
                produced += len(piece)
                if produced > BLOCK_MAX_BYTES:
                    raise HistorySummaryError('seed block expands beyond the bounded guard')
                digest.update(piece)
                buffer += piece
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if line.strip():
                        records.append(_block_record_dict(json.loads(line.decode('utf-8'))))
                        if len(records) > BLOCK_MAX_RECORDS:
                            raise HistorySummaryError('seed block has too many records')
        tail = decompressor.flush()
        if tail:
            produced += len(tail)
            digest.update(tail)
            buffer += tail
        if buffer.strip():
            records.append(_block_record_dict(json.loads(buffer.decode('utf-8'))))
    except zlib.error as exc:
        raise HistorySummaryError('seed block is corrupt: %s' % exc) from exc
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise HistorySummaryError('seed block is not decodable JSON: %s' % exc) from exc
    if expected_count is not None and len(records) != int(expected_count):
        raise HistorySummaryError('seed block record count %d != recorded %s'
                                  % (len(records), expected_count))
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        raise HistorySummaryError('seed block sha256 does not match the recorded integrity digest')
    return records


def _block_record_dict(values):
    if not isinstance(values, list) or len(values) != len(_BLOCK_FIELDS):
        raise HistorySummaryError('seed block record has the wrong shape')
    record = dict(zip(_BLOCK_FIELDS, values))
    observations = record.get('observations')
    if not isinstance(observations, list):
        raise HistorySummaryError('seed block observations are not a list')
    decoded = []
    for obs in observations:
        if not isinstance(obs, list) or len(obs) != len(_OBS_FIELDS):
            raise HistorySummaryError('seed block observation has the wrong shape')
        decoded.append(dict(zip(_OBS_FIELDS, obs)))
    record['observations'] = decoded
    return record


def _histogram_from_records(records):
    """The exact reward histogram a block's records imply (roundtrip-checked against storage)."""
    histogram = {}
    for record in records:
        earned = record.get('earned')
        if earned is None:
            continue
        label = str(int(earned)) if float(earned).is_integer() else str(earned)
        histogram[label] = histogram.get(label, 0) + 1
    return histogram


def _readonly_uri(path, *, immutable):
    uri = 'file:' + pathname2url(str(path)) + '?mode=ro'
    if immutable:
        uri += '&immutable=1'
    return uri


@contextlib.contextmanager
def _temp_dir(prefix):
    """A disposable temp directory created with default (accessible) permissions.

    ``tempfile.mkdtemp`` requests mode ``0o700``; some restricted Windows sandboxes deny access to
    the resulting directory. Creating it with ``os.makedirs`` keeps the tool usable there while
    behaving identically on a normal host.
    """
    base = tempfile.gettempdir()
    path = os.path.join(base, '%s%s' % (prefix, secrets.token_hex(6)))
    os.makedirs(path, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class SourceReader:
    """A strictly read-only, single-snapshot view of one existing library."""

    def __init__(self, path, *, immutable=False):
        self.path = Path(path).resolve()
        self.db = sqlite3.connect(_readonly_uri(self.path, immutable=immutable), uri=True,
                                  timeout=15.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=1')
        self._tables = None
        self._closed = False

    def tables(self):
        if self._tables is None:
            self._tables = {row[0] for row in
                            self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return self._tables

    def has_table(self, name):
        return name in self.tables()

    def begin(self):
        self.db.execute('BEGIN')

    def commit(self):
        self.db.execute('COMMIT')

    def meta(self, key, default=None):
        if not self.has_table('meta'):
            return default
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    def count(self, table):
        if not self.has_table(table):
            return None
        return self.db.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]

    def close(self):
        if not self._closed:
            self._closed = True
            self.db.close()


def open_source(path, *, immutable=False):
    """Explicit read-only open; the caller owns and closes the reader."""
    return SourceReader(path, immutable=immutable)


def _require_schema(reader):
    missing = [name for name in ('candidate', 'run', 'meta') if not reader.has_table(name)]
    if missing:
        raise HistorySummaryError('source %s is missing required tables: %s'
                                  % (reader.path, missing))


def _same_simulator(left, right):
    return legacy._same_simulator(left, right)


def _fresh_provenance():
    """The *current* canonical provenance for the new library (never a historical source's)."""
    import strategy_optimizer_adapter as adapter
    return adapter.provenance()


def _create_store(path):
    """Create a valid, empty optimiser library at ``path`` through the canonical initializer."""
    import strategy_optimizer
    return strategy_optimizer.Store(path, _fresh_provenance())


def _scenario_info(scenario, meta_row, stored_id=None):
    """Immutable identity + encounter + full resource policy, with no raw scenario retained.

    ``identity`` is the canonical scenario identity (``strategy_build_domain.identity`` is exactly
    the optimiser's candidate identity). A stored id that differs from it is an *alias*, reported
    separately rather than trusted as the identity. The encounter/defeat pair is required for a
    usable summary; without it the caller records an explicit omission.
    """
    try:
        fingerprint = domain.fingerprint(scenario)
    except Exception:  # noqa: BLE001 - a fingerprint that cannot be derived is simply absent
        fingerprint = {}
    try:
        identity = domain.identity(scenario)
    except Exception:  # noqa: BLE001 - an unhashable scenario is reported, not trusted
        identity = None
    policy = {key: scenario[key] for key in POLICY_KEYS if key in scenario}
    encounter_id = None
    defeat = None
    if meta_row is not None:
        try:
            encounter_id = int(meta_row['encounter'])
            defeat = int(meta_row['defeat'])
        except (TypeError, ValueError, KeyError, IndexError):
            encounter_id, defeat = None, None
    if encounter_id is None:
        raw = scenario.get('encounterId')
        encounter_id = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
    if defeat is None:
        raw = scenario.get('defeatCount')
        defeat = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
    alias = bool(identity and stored_id is not None and str(stored_id) != identity)
    return dict(
        identity=identity,
        stored_id=None if stored_id is None else str(stored_id),
        identity_verified=bool(identity and stored_id is not None
                               and str(stored_id) == identity),
        alias=alias,
        encounter_id=encounter_id, defeat_count=defeat, policy=policy,
        policy_key=_canonical(policy),
        identity_json=_canonical({
            'identity': identity,
            'storedId': None if stored_id is None else str(stored_id),
            'identityVerified': bool(identity and stored_id is not None
                                     and str(stored_id) == identity),
            'alias': alias,
            'exactKey': fingerprint.get('exactKey'),
            'normalizedFingerprint': fingerprint.get('normalizedFingerprint'),
            'preparedDigest': fingerprint.get('preparedDigest'),
            'mechanicsRevision': fingerprint.get('mechanicsRevision'),
            'compilerRevision': fingerprint.get('compilerRevision'),
            'note': 'computed now from the stored scenario; not a stored historical revision stamp',
        }),
    )


def _engine_groups(source_meta):
    """Assign each source's stored provenance to a compat group with the canonical equivalence."""
    groups = {}
    for source_id, prov in source_meta.items():
        digest = prov.get('digest') if isinstance(prov, dict) else None
        if not digest:
            groups[source_id] = 'unknown:%s' % source_id
            continue
        found = digest
        for other_id, other in groups.items():
            other_prov = source_meta.get(other_id)
            if isinstance(other_prov, dict) and _same_simulator(prov, other_prov):
                found = other
                break
        groups[source_id] = found
    return groups


# --- seed exclusion -----------------------------------------------------------------------------
def _seed_bounds(reader):
    """Compressed bank ceilings via the existing seed collector (never a materialised pair set)."""
    conn = reader.db
    run_max = freshness._phase_max_ordinal(conn, 'run') if reader.has_table('run') else {}
    ev_max = freshness._phase_max_ordinal(conn, 'evidence') if reader.has_table('evidence') else {}
    ceilings = freshness._declared_bank_ceilings(conn)
    limits = freshness._bank_limits(run_max, ev_max, ceilings)
    return dict(runMax=dict(run_max), evidenceMax=dict(ev_max),
                aggregate=dict(ceilings['aggregate']),
                declared={key: ceilings[key] for key in freshness.DECLARED_BANK_KEYS
                          if key != 'aggregate'},
                bankCeilings={phase: int(limits[phase]) for phase in freshness.PHASES})


def _stream_observed_pairs(scratch, reader):
    """Stream known observed seed pairs into disk scratch and count missing/malformed identity.

    Run rows already covered by an evidence row of the same ``(candidate,phase,ordinal)`` are
    skipped with an indexed ``NOT EXISTS``, so the retained replay JSON is parsed at most once and
    only where it adds coverage. Returns ``(distinct_pairs, missing, malformed)``.
    """
    scratch.execute('DELETE FROM seed_obs')
    missing = malformed = distinct = 0
    batch = []

    def flush():
        nonlocal batch
        if batch:
            scratch.execute('BEGIN')
            try:
                scratch.executemany('INSERT OR IGNORE INTO seed_obs(seed_a,seed_b) VALUES(?,?)',
                                    batch)
            finally:
                scratch.execute('COMMIT')
            batch = []

    def feed(raw, source):
        nonlocal missing, malformed, distinct
        if raw is None:
            missing += 1
            return
        try:
            pair = freshness._parse_seed_pair(raw, source=source)
        except freshness.FreshnessError:
            malformed += 1
            return
        batch.append(pair)
        distinct += 1
        if len(batch) >= SCRATCH_CHUNK:
            flush()

    if reader.has_table('evidence'):
        row = reader.db.execute('SELECT COUNT(*) FROM evidence WHERE seeds IS NULL').fetchone()
        missing += int(row[0] or 0)
        cursor = reader.db.execute('SELECT DISTINCT seeds FROM evidence WHERE seeds IS NOT NULL')
        while True:
            chunk = cursor.fetchmany(SCRATCH_CHUNK)
            if not chunk:
                break
            for (raw,) in chunk:
                feed(raw, 'evidence.seeds')
        flush()
    if reader.has_table('run'):
        cursor = reader.db.execute(_run_uncovered_sql(reader, 'result'))
        while True:
            chunk = cursor.fetchmany(SCRATCH_CHUNK)
            if not chunk:
                break
            for (raw,) in chunk:
                try:
                    result = json.loads(raw)
                except (TypeError, ValueError):
                    malformed += 1
                    continue
                seeds = result.get('seeds') if isinstance(result, dict) else None
                if seeds is None:
                    missing += 1
                    continue
                feed(_canonical(seeds), 'run.result.seeds')
    flush()
    return distinct, missing, malformed


def _exception_pairs(scratch, bounds):
    """Observed pairs outside every phase's deterministic bank; stored, never guessed.

    The deterministic bank is the canonical collector's cached set, so the whole bank is built once
    per (phase, ceiling) and reused instead of rescanning scratch for every bank ordinal.
    """
    deterministic = freshness._deterministic_pairs(bounds['bankCeilings'], lambda _where: None)
    return [(int(a), int(b)) for a, b in scratch.execute('SELECT seed_a,seed_b FROM seed_obs')
            if (int(a), int(b)) not in deterministic]


def seed_exclusion_report(reader):
    """The versioned, compressed past-seed exclusion for one source. Never claims freshness.

    Off-bank ("exception") pairs are safely excludable and are recorded, not treated as a block.
    ``holdoutBlocked`` is set only when seed identity cannot be established at all (a broken seed
    law or malformed retained seed data), which fails closed.
    """
    report = dict(bounds=None, exceptionPairs=[], observedPairs=0, bankPairs=0, missingSeedRows=0,
                  malformedPairs=0, customSeedIdentity=False, holdoutBlocked=False, reason=None,
                  coverage={}, provenance={})
    note = dict(collector='strategy_seed_freshness', method='bank-limits+streamed-exception-scan',
                declaredKeys=list(freshness.DECLARED_BANK_KEYS))
    with _temp_dir('ka-history-seed-') as folder:
        scratch = sqlite3.connect(str(Path(folder) / 'seed.sqlite'), isolation_level=None)
        try:
            scratch.execute('PRAGMA journal_mode=OFF')
            scratch.execute('PRAGMA synchronous=OFF')
            scratch.execute('CREATE TABLE seed_obs(seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL,'
                            ' PRIMARY KEY(seed_a,seed_b)) WITHOUT ROWID')
            try:
                bounds = _seed_bounds(reader)
                observed, missing, malformed = _stream_observed_pairs(scratch, reader)
                report['bounds'] = bounds
                report['bankPairs'] = sum(int(value) for value in bounds['bankCeilings'].values())
                report['observedPairs'] = int(observed)
                report['missingSeedRows'] = int(missing)
                report['malformedPairs'] = int(malformed)
                exceptions = _exception_pairs(scratch, bounds)
                report['exceptionPairs'] = exceptions
                report['customSeedIdentity'] = bool(exceptions)
                report['holdoutBlocked'] = bool(malformed)
                if malformed:
                    report['reason'] = ('malformed retained seed identity: coverage cannot be '
                                        'established, so the source fails closed')
                report['coverage'] = dict(
                    establishedPairs=int(observed) - len(exceptions),
                    exceptionPairs=len(exceptions),
                    unknownPairs=int(missing) + int(malformed),
                    note='every known pair is excludable; missing seeds are incomplete coverage and '
                         'never permission to claim freshness')
                note.update(bankCeilings=bounds['bankCeilings'], aggregate=bounds['aggregate'],
                            runMax=bounds['runMax'], evidenceMax=bounds['evidenceMax'])
            except freshness.FreshnessError as exc:
                report['holdoutBlocked'] = True
                report['customSeedIdentity'] = True
                report['reason'] = str(exc)
            report['provenance'] = note
        finally:
            scratch.close()
    return report


# --- staging and aggregation --------------------------------------------------------------------
def _stage_row_values(info, engine_group, phase, candidate, outcome_row, source_id, key):
    """One dedupe-keyed scratch row. A missing seed identity never collides with a real pair."""
    earned = outcome_row.get('finalEarned')
    earned = float(earned) if isinstance(earned, (int, float)) and not isinstance(earned, bool) \
        else None
    status = outcome_row.get('status') or outcomes.UNRESOLVED
    loss = 1 if status == outcomes.LOSS else 0
    censored = 1 if status == outcomes.CENSORED else 0
    error = 1 if status == outcomes.ERROR else 0
    basis = outcome_row.get('basis')
    basis = basis if isinstance(basis, str) else None
    pending = outcome_row.get('pending')
    pending = float(pending) if isinstance(pending, (int, float)) and not isinstance(pending, bool) \
        else None
    ordinal = int(outcome_row.get('_ordinal', 0))
    if key is not None:
        k_a, k_b, seed_a, seed_b = int(key[0]), int(key[1]), int(key[0]), int(key[1])
    else:
        # A negative, source-scoped key: an unseeded reading can never be confused with a real pair.
        k_a, k_b, seed_a, seed_b = -(int(source_id) + 1), ordinal, None, None
    return (candidate, info['policy_key'], engine_group, phase, k_a, k_b, seed_a, seed_b,
            1 if key is not None else 0, earned, status, basis, pending, loss, censored, error)


def _stage_obs_values(info, engine_group, phase, ident, candidate, ordinal, earned, status, basis,
                      pending, loss, censored, error, key, source_id):
    """One raw observation row for the compressed-block audit trail."""
    if key is not None:
        k_a, k_b, has_seed = int(key[0]), int(key[1]), 1
    else:
        k_a, k_b, has_seed = -(int(source_id) + 1), int(ordinal or 0), 0
    return (ident, info['policy_key'], engine_group, phase, k_a, k_b, has_seed, source_id,
            candidate, ordinal, earned, status, basis, pending, loss, censored, error)


def _load_scenario(scratch, reader, candidate):
    """Parse + fingerprint one stored scenario. Returns the info dict even when the encounter is
    missing, so the caller can count the omission instead of silently dropping the candidate."""
    row = reader.db.execute('SELECT scenario FROM candidate WHERE id=?', (candidate,)).fetchone()
    if row is None:
        return None, 'missing-scenario'
    try:
        scenario = json.loads(row[0])
    except (TypeError, ValueError):
        return None, 'malformed-scenario'
    if not isinstance(scenario, dict):
        return None, 'malformed-scenario'
    meta_row = None
    if reader.has_table('candidate_meta'):
        meta_row = reader.db.execute('SELECT encounter,defeat FROM candidate_meta WHERE id=?',
                                     (candidate,)).fetchone()
    info = _scenario_info(scenario, meta_row, stored_id=candidate)
    ident = info['identity'] or str(candidate)
    info['ident'] = ident
    if info['encounter_id'] is None or info['defeat_count'] is None:
        return info, 'missing-encounter'
    if info['identity'] is None:
        return info, 'identity-unverified'
    if info['alias']:
        # The stored id is not the canonical identity; the summary is keyed by the canonical identity
        # but the alias is recorded (never merged silently).
        scratch.execute('INSERT OR IGNORE INTO stage_scenario(ident,candidate_id,encounter_id,'
                        'defeat_count,policy_key,policy_json,identity_json) VALUES(?,?,?,?,?,?,?)',
                        (ident, str(candidate), info['encounter_id'], info['defeat_count'],
                         info['policy_key'], _canonical(info['policy']), info['identity_json']))
        return info, 'identity-alias'
    if not info['identity_verified']:
        scratch.execute('INSERT OR IGNORE INTO stage_scenario(ident,candidate_id,encounter_id,'
                        'defeat_count,policy_key,policy_json,identity_json) VALUES(?,?,?,?,?,?,?)',
                        (ident, str(candidate), info['encounter_id'], info['defeat_count'],
                         info['policy_key'], _canonical(info['policy']), info['identity_json']))
        return info, 'identity-unverified'
    scratch.execute('INSERT OR IGNORE INTO stage_scenario(ident,candidate_id,encounter_id,'
                    'defeat_count,policy_key,policy_json,identity_json) VALUES(?,?,?,?,?,?,?)',
                    (ident, str(candidate), info['encounter_id'], info['defeat_count'],
                     info['policy_key'], _canonical(info['policy']), info['identity_json']))
    return info, None


def _run_uncovered_sql(reader, select_columns):
    """Run rows with no compact evidence row of the same (candidate,phase,ordinal) identity."""
    base = 'SELECT %s FROM run' % select_columns
    if reader.has_table('evidence'):
        return (base + ' WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate=run.candidate'
                ' AND e.phase=run.phase AND e.ordinal=run.ordinal)'
                ' ORDER BY run.candidate,run.phase,run.ordinal')
    return base + ' ORDER BY candidate,phase,ordinal'


def _stream_measurements(scratch, reader, source_id, engine_group, batch_size):
    """Copy retained measurements into disk scratch in bounded batches.

    Evidence and retained runs are deduplicated by the *actual row identity*
    ``(candidate, phase, ordinal)`` (an indexed ``NOT EXISTS``), never by candidate alone: partial
    evidence coverage must not hide a run-only measurement. Overlapping measurements that share a
    seed pair but disagree on earned become ``conflict``/unknown in the ``stage`` upsert - no winner
    is chosen. Different conditions/engine groups never merge because the engine group is in the key.
    """
    custom = False
    omitted = {}
    group_seen = set()
    pending, pending_sources, pending_obs, pending_identity = [], [], [], []

    def flush():
        nonlocal pending, pending_sources, pending_obs, pending_identity
        if pending_obs or pending:
            scratch.execute('BEGIN')
            try:
                if pending:
                    scratch.executemany(_STAGE_UPSERT, pending)
                if pending_obs:
                    scratch.executemany(_STAGE_OBS_INSERT, pending_obs)
                if pending_sources:
                    scratch.executemany('INSERT OR IGNORE INTO stage_source(ident,policy,engine,'
                                        'phase,source_id,candidate_id) VALUES(?,?,?,?,?,?)',
                                        pending_sources)
                if pending_identity:
                    scratch.executemany('INSERT OR IGNORE INTO evidence_row(candidate,phase,ordinal)'
                                        ' VALUES(?,?,?)', pending_identity)
            finally:
                scratch.execute('COMMIT')
            pending, pending_sources, pending_obs, pending_identity = [], [], [], []

    def emit(ident, candidate, phase, outcome_row, info, key):
        nonlocal custom
        if key is None:
            custom = True
        earned = outcome_row.get('finalEarned')
        earned = float(earned) if isinstance(earned, (int, float)) and not isinstance(earned, bool) \
            else None
        status = outcome_row.get('status') or outcomes.UNRESOLVED
        basis = outcome_row.get('basis') if isinstance(outcome_row.get('basis'), str) else None
        pendingv = outcome_row.get('pending')
        pendingv = float(pendingv) if isinstance(pendingv, (int, float)) \
            and not isinstance(pendingv, bool) else None
        loss = 1 if status == outcomes.LOSS else 0
        censored = 1 if status == outcomes.CENSORED else 0
        error = 1 if status == outcomes.ERROR else 0
        ordinal = outcome_row.get('_ordinal')
        pending.append(_stage_row_values(info, engine_group, phase, ident, outcome_row, source_id,
                                         key))
        pending_obs.append(_stage_obs_values(info, engine_group, phase, ident, candidate, ordinal,
                                             earned, status, basis, pendingv, loss, censored, error,
                                             key, source_id))
        group = (ident, info['policy_key'], engine_group, phase)
        if group not in group_seen:
            group_seen.add(group)
            pending_sources.append((ident, info['policy_key'], engine_group, phase, source_id,
                                    str(candidate)))
        if len(pending) >= batch_size:
            flush()

    def note(kind, count=1):
        omitted[kind] = omitted.get(kind, 0) + int(count)

    if reader.has_table('evidence'):
        columns = ','.join(EVIDENCE_COLUMNS)
        cursor = reader.db.execute('SELECT candidate,phase,ordinal,' + columns + ' FROM evidence '
                                   'ORDER BY candidate,phase,ordinal')
        current, info, failure = None, None, None
        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            for row in chunk:
                candidate = row[0]
                if candidate != current:
                    current = candidate
                    info, failure = _load_scenario(scratch, reader, candidate)
                if info is None:
                    note(failure or 'missing-scenario')
                    continue
                if failure:
                    note(failure)
                if info['encounter_id'] is None or info['identity'] is None:
                    continue
                ident = info['ident']
                decoded = evidence_service.decode(*row[3:])
                key = legacy._seed_key(decoded.get('seeds'))
                if key is None:
                    note('unseeded-or-invalid-seed')
                outcome_row = outcomes.outcome(decoded, candidate_id=candidate,
                                               measurement_window=_window(row[1]),
                                               policy=info['policy'])
                outcome_row['_ordinal'] = row[2]
                pending_identity.append((candidate, row[1], int(row[2])))
                emit(ident, candidate, row[1], outcome_row, info, key)
    if reader.has_table('run'):
        cursor = reader.db.execute(_run_uncovered_sql(reader, 'candidate,phase,ordinal,result'))
        current, info, failure = None, None, None
        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            for row in chunk:
                candidate = row[0]
                if candidate != current:
                    current = candidate
                    info, failure = _load_scenario(scratch, reader, candidate)
                if info is None:
                    note(failure or 'missing-scenario')
                    continue
                if failure:
                    note(failure)
                if info['encounter_id'] is None or info['identity'] is None:
                    continue
                ident = info['ident']
                try:
                    result = json.loads(row[3])
                except (TypeError, ValueError):
                    note('malformed-run-result')
                    continue
                if not isinstance(result, dict):
                    note('malformed-run-result')
                    continue
                key = legacy._seed_key(result.get('seeds'))
                if key is None:
                    note('unseeded-or-invalid-seed')
                outcome_row = outcomes.outcome(result, candidate_id=candidate,
                                               measurement_window=_window(row[1]),
                                               policy=info['policy'])
                outcome_row['_ordinal'] = row[2]
                emit(ident, candidate, row[1], outcome_row, info, key)
    flush()
    return dict(custom=custom, omitted=omitted)

def _weighted_value_at(values, cumulative, index):
    """The ``index``-th (0-based) order statistic of the expanded numeric list, without expanding."""
    position = bisect.bisect_right(cumulative, index)
    return values[position]


def _weighted_quantile(values, cumulative, count, fraction):
    """Exactly ``strategy_outcomes._quantile`` but over a weighted distribution."""
    if not values:
        return None
    if count == 1:
        return values[0]
    position = (count - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return _weighted_value_at(values, cumulative, low)
    weight = position - low
    return (_weighted_value_at(values, cumulative, low) * (1.0 - weight)
            + _weighted_value_at(values, cumulative, high) * weight)


def _weighted_summary(distribution, basis_counts=None, thresholds=()):
    """Exactly ``strategy_outcomes.summarize`` over a weighted ``(earned,status)->count`` map.

    No per-sample list is ever built, so a build measured 500k times costs one pass over the
    distinct ``(earned,status)`` buckets. The field semantics are shared with the canonical
    summariser (including ``complete``/``meanEarned`` and the finite diagnostics), which the check
    verifies against ``strategy_outcomes.summarize`` on small fixtures.
    """
    total = 0
    by_status = {}
    numeric_counts = {}
    losses = zeros = unresolved = errors = censored = 0
    for (earned, status), count in distribution.items():
        count = int(count)
        if count <= 0:
            continue
        total += count
        by_status[status] = by_status.get(status, 0) + count
        if status == outcomes.ERROR:
            errors += count
        if status == outcomes.CENSORED:
            censored += count
        if status in outcomes.UNRESOLVED_STATUSES:
            unresolved += count
        if status == outcomes.LOSS:
            losses += count
        if earned is not None:
            value = float(earned)
            numeric_counts[value] = numeric_counts.get(value, 0) + count
            if value == 0:
                zeros += count
    values = sorted(numeric_counts)
    counts = [numeric_counts[value] for value in values]
    cumulative, running = [], 0
    for count in counts:
        running += count
        cumulative.append(running)
    numeric_total = running
    basis = dict(basis_counts or {})
    mean = (sum(value * numeric_counts[value] for value in values) / float(numeric_total)) \
        if numeric_total else None
    stdev = None
    if numeric_total >= 2 and mean is not None:
        variance = sum((value - mean) ** 2 * numeric_counts[value]
                       for value in values) / float(numeric_total)
        stdev = math.sqrt(variance)
    complete = bool(total) and unresolved == 0 and errors == 0 and numeric_total == total
    ordered_total = sum(value * numeric_counts[value] for value in values)
    median = _weighted_quantile(values, cumulative, numeric_total, 0.5)
    skewness = None
    if numeric_total >= 3 and stdev:
        m3 = sum((value - mean) ** 3 * numeric_counts[value]
                 for value in values) / float(numeric_total)
        skewness = m3 / (stdev ** 3)
    report = dict(
        total=total,
        numericCount=numeric_total,
        resolvedCount=numeric_total,
        byStatus=by_status,
        byBasis=basis,
        unresolvedCount=unresolved,
        errorCount=errors,
        censoredCount=censored,
        lossCount=losses,
        zeroCount=zeros,
        lossFrequency=(losses / float(total)) if total else None,
        zeroFrequency=(zeros / float(numeric_total)) if numeric_total else None,
        resourceCount=0,
        resourceMean=None,
        resourceFrequency=None,
        partialMean=mean,
        meanEarned=mean if complete else None,
        median=median,
        min=values[0] if values else None,
        max=values[-1] if values else None,
        stdev=stdev,
        quantiles={name: _weighted_quantile(values, cumulative, numeric_total, fraction)
                   for name, fraction in (('p0', 0.0), ('p25', 0.25), ('p50', 0.5),
                                          ('p75', 0.75), ('p100', 1.0))},
        metricSampleCounts=dict(total=total, numeric=numeric_total, resources=0),
        eligibleForRecommendation=complete and numeric_total > 0,
        fullClaim=dict(complete=complete, meanEarned=mean if complete else None,
                       note='a full claim needs every outcome resolved with no error; the partial '
                            'mean covers only the available readings'),
        diagnostics=dict(
            skewness=skewness,
            top1ShareOfTotal=((values[-1] / ordered_total)
                              if values and ordered_total > 0 else None),
            maxToMedianRatio=((values[-1] / median) if values and median else None),
            jackpotSensitive=bool(numeric_total >= 2 and ordered_total > 0
                                  and values[-1] / ordered_total > 0.5),
            sampleCount=numeric_total),
    )
    report['thresholds'] = {
        str(threshold): dict(
            count=sum(numeric_counts[value] for value in values if value >= threshold),
            frequency=((sum(numeric_counts[value] for value in values if value >= threshold)
                        / float(numeric_total)) if numeric_total else None))
        for threshold in (thresholds or ())}
    report['denominators'] = dict(attempted=total, resolved=numeric_total, lost=losses,
                                  unresolved=unresolved, errors=errors, censored=censored)
    return report


def _moments(scratch, ident, policy, engine, phase):
    """Exact weighted summary for one group. No per-sample expansion, however many measurements."""
    distribution = {}
    histogram = {}
    basis_counts = {}
    for earned, status, basis, count in scratch.execute(
            'SELECT earned,status,basis,COUNT(*) FROM stage WHERE ident=? AND policy=? AND engine=? '
            'AND phase=? GROUP BY earned,status,basis', (ident, policy, engine, phase)):
        count = int(count)
        distribution[(earned, status)] = distribution.get((earned, status), 0) + count
        basis_counts[basis or 'none'] = basis_counts.get(basis or 'none', 0) + count
        if earned is not None:
            label = str(int(earned)) if float(earned).is_integer() else str(earned)
            histogram[label] = histogram.get(label, 0) + count
    summary = _weighted_summary(distribution, basis_counts, thresholds=REWARD_THRESHOLDS)
    moments = dict(
        partialMean=summary['partialMean'], meanEarned=summary['meanEarned'],
        median=summary['median'], min=summary['min'], max=summary['max'], stdev=summary['stdev'],
        quantiles=summary['quantiles'], byStatus=summary['byStatus'], byBasis=summary['byBasis'],
        lossFrequency=summary['lossFrequency'], zeroFrequency=summary['zeroFrequency'],
        denominators=summary['denominators'], thresholds=summary['thresholds'],
        diagnostics=summary['diagnostics'],
    )
    return summary, moments, histogram


def _group_provenance(scratch, ident, policy, engine, phase, sources):
    contributors, digest, engine_provenance = [], None, None
    for source_id, candidate_id in scratch.execute(
            'SELECT source_id,candidate_id FROM stage_source WHERE ident=? AND policy=? '
            'AND engine=? AND phase=? ORDER BY source_id', (ident, policy, engine, phase)):
        source = sources.get(source_id, {})
        contributors.append(dict(sourceId=source_id, candidateId=candidate_id,
                                 label=source.get('label'), ownership=source.get('ownership'),
                                 sourcePath=source.get('path'),
                                 provenanceDigest=source.get('provenanceDigest')))
        if digest is None:
            digest = source.get('provenanceDigest')
            full = source.get('provenance') or {}
            engine_provenance = dict(digest=full.get('digest'), files=full.get('files')) \
                if full else None
    return dict(engineCompatibilityGroup=engine, engineDigest=digest,
                engineProvenance=engine_provenance, sources=contributors,
                historicalConfirmed=False,
                note='retained historical measurements only; never a fresh confirmation')


def _aggregate(scratch, dest, sources):
    groups = list(scratch.execute(
        'SELECT ident,policy,engine,phase,COUNT(*) samples,'
        ' SUM(CASE WHEN earned IS NOT NULL THEN 1 ELSE 0 END) resolved,'
        ' SUM(CASE WHEN status=? THEN 1 ELSE 0 END) losses,'
        ' SUM(censored) censored, SUM(error) errors,'
        ' SUM(duplicates) duplicates, SUM(conflicts) conflicts '
        'FROM stage GROUP BY ident,policy,engine,phase', (outcomes.LOSS,)))
    inserted = 0
    for group in groups:
        (ident, policy, engine, phase, samples, resolved, losses, censored, errors,
         duplicates, conflicts) = group
        scenario = scratch.execute('SELECT encounter_id,defeat_count,policy_json,identity_json '
                                   'FROM stage_scenario WHERE ident=?', (ident,)).fetchone()
        if scenario is None:
            continue
        summary, moments, histogram = _moments(scratch, ident, policy, engine, phase)
        status = STATUS_MEASURED
        if conflicts:
            status = STATUS_CONFLICT
        elif str(engine).startswith('unknown:'):
            status = STATUS_UNCONFIRMED
        elif summary['meanEarned'] is None:
            status = STATUS_PARTIAL
        provenance = _group_provenance(scratch, ident, policy, engine, phase, sources)
        dest.execute(
            'INSERT OR IGNORE INTO history_build_summary(candidate_id,scenario_digest,'
            'identity_json,encounter_id,defeat_count,policy_key,policy_json,engine_compat_group,'
            'engine_digest,engine_provenance_json,phase,evidence_window,status,mean_earned,'
            'partial_mean_earned,resolved_count,unknown_count,censored_count,error_count,'
            'loss_count,sample_count,retained_measurements,reward_histogram_json,moments_json,'
            'duplicate_count,conflicting_rewards,historical_confirmed,provenance_json) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, ident, scenario[3], scenario[0], scenario[1], policy, scenario[2], engine,
             provenance.get('engineDigest'), _canonical(provenance.get('engineProvenance')),
             phase, _window(phase), status, summary['meanEarned'], summary['partialMean'],
             int(resolved), int(samples) - int(resolved), int(censored or 0), int(errors or 0),
             int(losses or 0), int(samples), int(samples), _canonical(histogram),
             _canonical(moments), int(duplicates or 0), int(conflicts or 0), 0,
             _canonical(provenance)))
        inserted += 1
    return inserted


def _block_provenance(scratch, ident, policy, engine, sources):
    """Merged source attribution for the whole compatible group (all phases)."""
    contributors, seen = [], set()
    for source_id, candidate_id, phase in scratch.execute(
            'SELECT source_id,candidate_id,phase FROM stage_source WHERE ident=? AND policy=? '
            'AND engine=? ORDER BY source_id,phase,candidate_id', (ident, policy, engine)):
        key = (source_id, candidate_id, phase)
        if key in seen:
            continue
        seen.add(key)
        source = sources.get(source_id, {})
        contributors.append(dict(sourceId=source_id, candidateId=candidate_id, phase=phase,
                                 label=source.get('label'), ownership=source.get('ownership'),
                                 sourcePath=source.get('path'),
                                 provenanceDigest=source.get('provenanceDigest')))
    return dict(engineCompatibilityGroup=engine, sources=contributors,
                historicalConfirmed=False,
                note='compressed canonical seed-outcome block; audit trail for dedup, not a replay')


def _iter_group_records(scratch, ident, policy, engine):
    """Stream one compatible group's deduplicated records with their observation audit trail.

    Bounded: stage rows and observations are merged from two ordered cursors in fixed-size chunks,
    so a group measured hundreds of thousands of times is never materialised as Python objects.
    """
    stage_cursor = scratch.execute(
        'SELECT phase,k_a,k_b,seed_a,seed_b,earned,status,basis,pending,loss,censored,error,'
        'duplicates,conflicts FROM stage WHERE ident=? AND policy=? AND engine=? '
        'ORDER BY phase,k_a,k_b', (ident, policy, engine))
    obs_cursor = scratch.execute(
        'SELECT phase,k_a,k_b,source_id,ordinal,earned,status,basis,pending FROM stage_obs '
        'WHERE ident=? AND policy=? AND engine=? ORDER BY phase,k_a,k_b,source_id,ordinal',
        (ident, policy, engine))
    obs_buffer = list(obs_cursor.fetchmany(SCRATCH_CHUNK))
    position = 0

    def take(phase, k_a, k_b):
        nonlocal obs_buffer, position
        key = (phase, k_a, k_b)
        found = []
        while True:
            if position >= len(obs_buffer):
                obs_buffer = list(obs_cursor.fetchmany(SCRATCH_CHUNK))
                position = 0
                if not obs_buffer:
                    break
            row = obs_buffer[position]
            row_key = (row[0], row[1], row[2])
            if row_key == key:
                found.append([row[3], row[4], row[5], row[6], row[7], row[8]])
                position += 1
            elif row_key < key:
                position += 1
            else:
                break
        return found

    for row in stage_cursor:
        phase, k_a, k_b = row[0], row[1], row[2]
        yield dict(phase=phase, seedA=row[3], seedB=row[4], earned=row[5], status=row[6],
                   basis=row[7], pending=row[8], loss=row[9], censored=row[10], error=row[11],
                   duplicates=row[12], conflicts=row[13], observations=take(phase, k_a, k_b))


def _mine_blocks(scratch, dest, sources, created_at):
    """Write one versioned compressed seed-outcome block per compatible group (bounded streaming)."""
    inserted = 0
    groups = scratch.execute('SELECT DISTINCT ident,policy,engine FROM stage').fetchall()
    for ident, policy, engine in groups:
        if scratch.execute('SELECT 1 FROM stage_scenario WHERE ident=?', (ident,)).fetchone() is None:
            continue
        compressor = zlib.compressobj(6)
        digest = hashlib.sha256()
        chunks, count, observations, raw_bytes = [], 0, 0, 0
        histogram, phases = {}, set()
        for record in _iter_group_records(scratch, ident, policy, engine):
            line = json.dumps(_block_record_list(record), separators=(',', ':')).encode('utf-8') \
                + b'\n'
            digest.update(line)
            raw_bytes += len(line)
            count += 1
            observations += len(record['observations'] or ())
            phases.add(record['phase'])
            earned = record.get('earned')
            if earned is not None:
                label = str(int(earned)) if float(earned).is_integer() else str(earned)
                histogram[label] = histogram.get(label, 0) + 1
            chunks.append(compressor.compress(line))
        if not count:
            continue
        chunks.append(compressor.flush())
        blob = b''.join(chunks)
        provenance = _block_provenance(scratch, ident, policy, engine, sources)
        dest.execute(
            'INSERT OR IGNORE INTO history_seed_block(candidate_id,scenario_digest,policy_key,'
            'engine_compat_group,block_version,codec,phase_list_json,record_count,'
            'observation_count,plain_sha256,compressed_bytes,uncompressed_bytes,histogram_json,'
            'provenance_json,created_at,block_zlib) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, ident, policy, engine, BLOCK_VERSION, BLOCK_CODEC,
             _canonical(sorted(phases)), count, observations, digest.hexdigest(), len(blob),
             raw_bytes, _canonical(histogram), _canonical(provenance), created_at, blob))
        inserted += 1
    return inserted


# --- candidate + lineage recovery ----------------------------------------------------------------
def _candidate_origin(origin_source, label, cid):
    """The original per-candidate source, never the source's ownership label, never guessed."""
    if isinstance(origin_source, str) and origin_source:
        return origin_source
    return 'history-summary-prior'


def _profile_rank(profile):
    """Deterministic strength order for a recovered build (retained evidence, never old ranking)."""
    return (int(profile.get('samples') or 0), int(profile.get('resolved') or 0),
            int(profile.get('created') or 0), str(profile['identity']))


def _summary_profiles(dest):
    """Per-candidate retained evidence profile (max sample count) from ``history_build_summary``."""
    profiles = {}
    try:
        rows = dest.execute(
            'SELECT candidate_id,engine_compat_group,sample_count,resolved_count,mean_earned '
            'FROM history_build_summary').fetchall()
    except sqlite3.OperationalError:
        return profiles
    for cid, engine, samples, resolved, mean in rows:
        samples = int(samples or 0)
        resolved = int(resolved or 0)
        current = profiles.get(cid)
        if current is None or (samples, resolved) > (current['samples'], current['resolved']):
            profiles[cid] = dict(engine=engine, samples=samples, resolved=resolved, mean=mean)
        elif current.get('mean') is None and mean is not None:
            current['mean'] = mean
    return profiles


def _is_supplied_reference(profile):
    """A supplied Community reference build, never inferred from a label."""
    return (str(profile.get('recovered_from')) == 'candidate'
            and str(profile.get('origin_source') or '') == 'supplied')


def _stat_bucket(profile):
    """A coarse reward/evidence stratum so alternatives below the top mean still survive."""
    mean = profile.get('mean')
    if mean is None:
        return 'unmeasured'
    try:
        return 'mean:%d' % int(round(float(mean)))
    except (TypeError, ValueError):
        return 'unmeasured'


def _select_active(candidates):
    """Bounded, diverse active set: a supplied reference per encounter, then round-robin strata.

    Strata are ``(engine-compat group, coarse reward bucket)`` so a genuinely different engine group
    or reward/stat alternative stays represented instead of a single top-mean winner. Within a
    stratum the strongest *retained evidence* (sample count, then resolved count) is drawn first.
    Engine groups are never merged, and an incompatible group is only ever a hypothesis/starting
    build - the loader still refuses it as a reward prior.
    """
    by_encounter = {}
    for profile in candidates:
        by_encounter.setdefault(profile.get('encounter_id'), []).append(profile)
    order = sorted(by_encounter, key=lambda value: (value is None, value))
    chosen, counts, seen = [], {}, set()

    def take(profile):
        identity = profile['identity']
        if identity in seen:
            return False
        encounter = profile.get('encounter_id')
        if counts.get(encounter, 0) >= MAX_CANDIDATES_PER_ENCOUNTER:
            return False
        if len(chosen) >= MAX_CANDIDATES_TOTAL:
            return False
        seen.add(identity)
        counts[encounter] = counts.get(encounter, 0) + 1
        chosen.append(profile)
        return True

    for encounter in order:  # one supplied Community reference per encounter first
        references = [p for p in by_encounter[encounter] if _is_supplied_reference(p)]
        references.sort(key=_profile_rank, reverse=True)
        for profile in references:
            if take(profile):
                break

    for encounter in order:  # then round-robin across engine/reward strata
        remaining = [p for p in by_encounter[encounter] if p['identity'] not in seen]
        strata = {}
        for profile in remaining:
            key = (str(profile.get('engine') or 'unknown'), _stat_bucket(profile))
            strata.setdefault(key, []).append(profile)
        for members in strata.values():
            members.sort(key=_profile_rank, reverse=True)
        keys = sorted(strata)
        index = {key: 0 for key in keys}
        progress = True
        while progress:
            progress = False
            for key in keys:
                if (counts.get(encounter, 0) >= MAX_CANDIDATES_PER_ENCOUNTER
                        or len(chosen) >= MAX_CANDIDATES_TOTAL):
                    break
                members = strata[key]
                if index[key] < len(members):
                    take(members[index[key]])
                    index[key] += 1
                    progress = True
    return chosen


def _canonical_stats(scenario):
    """The build's *current* canonical per-fighter stats, recomputed from its own scenario.

    ``{}`` when the canonical runtime cannot be loaded: no old ranking/score is ever substituted.
    """
    try:
        from strategy_optimizer_adapter import stats as adapter_stats
        value = adapter_stats(scenario)
    except Exception:  # noqa: BLE001 - a missing runtime never fabricates or copies a stat blob
        return {}
    return value if isinstance(value, dict) else {}


def _materialise_active(dest, profile):
    """Insert one selected build as an active resident with recomputed current stats."""
    identity = profile['identity']
    row = dest.execute('SELECT scenario_zlib FROM history_scenario WHERE candidate_id=?',
                       (identity,)).fetchone()
    if row is None:
        return
    try:
        scenario = json.loads(zlib.decompress(row[0]).decode('utf-8'))
    except (zlib.error, UnicodeDecodeError, TypeError, ValueError):
        return
    if not isinstance(scenario, dict):
        return
    label = (str(profile['label']) if profile.get('label') else identity)[:160]
    dest.execute('INSERT OR IGNORE INTO candidate(id,scenario,label,source,stats,created) '
                 'VALUES(?,?,?,?,?,?)',
                 (identity, _canonical(scenario), label,
                  _candidate_origin(profile.get('origin_source'), profile.get('label'), identity),
                  _canonical(_canonical_stats(scenario)), int(profile.get('created') or 0)))
    if profile.get('encounter_id') is not None and profile.get('defeat_count') is not None:
        dest.execute('INSERT OR IGNORE INTO candidate_meta(id,encounter,defeat,region) '
                     'VALUES(?,?,?,?)',
                     (identity, profile['encounter_id'], profile['defeat_count'],
                      profile.get('region') or ''))
    parent, root = profile.get('parent'), profile.get('root')
    if parent is not None or root is not None:
        dest.execute('INSERT OR IGNORE INTO lineage(candidate,parent,root,depth,operation,'
                     'target,change,source,encounterId,proposal,searchSpaceVersion,created) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identity, parent, root if root is not None else identity,
                      int(profile.get('depth') or 0), profile.get('operation'),
                      profile.get('target'), profile.get('change'),
                      profile.get('lineage_source')
                      if profile.get('lineage_source') is not None
                      else profile.get('origin_source'),
                      profile.get('encounter_id'), int(profile.get('proposal') or 0),
                      int(profile['search_space_version'])
                      if profile.get('search_space_version') is not None else 0,
                      int(profile.get('created') or 0)))


def _recover_candidates(dest, records, sources_map, imported_at):
    """Recover candidate scenarios + lineage into the destination, bounded as active residents.

    Every exact scenario (live from ``candidate`` or pruned from ``predictor_history``) is kept
    *losslessly compressed* in ``history_scenario`` keyed by canonical identity, and all of its
    source attribution/lineage is kept in ``history_candidate``. Only a small, diverse slice becomes
    an active resident build in ``candidate``/``candidate_meta``/``lineage``: a supplied Community
    reference per encounter, then a round-robin draw across engine/stat strata, bounded to
    ``MAX_CANDIDATES_PER_ENCOUNTER``/``MAX_CANDIDATES_TOTAL``. A fresh import is therefore a small
    working library, not a resurrection of every historic build. The stored id is validated against
    the canonical identity of the scenario: a mismatch is an alias that deduplicates by identity and
    is reported explicitly. Old rankings/scores are never imported; an active build's ``stats`` are
    recomputed from its own scenario in the current canonical runtime.
    """
    by_path = {info['path']: sid for sid, info in sources_map.items()}
    omissions = {}
    profiles = _summary_profiles(dest)
    pool = {}

    def note(source_id, kind, count=1, detail=''):
        key = (source_id, kind)
        entry = omissions.get(key)
        if entry is None:
            omissions[key] = [int(count), detail]
        else:
            entry[0] += int(count)

    def insert(source_id, stored_id, scenario, *, recovered_from, label, origin_source, root,
               parent, depth, depth_known, operation, target, change, proposal,
               search_space_version, created, region='', lineage_source=None, encounter=None,
               defeat=None):
        try:
            identity = domain.identity(scenario)
        except Exception:  # noqa: BLE001 - an unhashable scenario is reported, never trusted
            identity = None
        if identity is None:
            note(source_id, 'unhashable-scenario')
            return None
        alias = identity != str(stored_id)
        if alias:
            note(source_id, 'identity-alias', 1,
                 'stored id %r differs from the canonical identity; deduplicated by identity'
                 % str(stored_id))
        source = sources_map.get(source_id, {})
        scene_encounter, scene_defeat = _scenario_meta(scenario)
        encounter_id = encounter if encounter is not None else scene_encounter
        defeat_count = defeat if defeat is not None else scene_defeat
        policy = {key: scenario[key] for key in POLICY_KEYS if key in scenario}
        dest.execute(
            'INSERT OR IGNORE INTO history_candidate(candidate_id,source_id,scenario_digest,'
            'stored_id,identity_verified,alias_of,recovered_from,encounter_id,defeat_count,'
            'policy_key,policy_json,label,origin_source,source_label,source_ownership,source_path,'
            'root,parent,depth,depth_known,operation,target,change,proposal,search_space_version,'
            'created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identity, source_id, identity, str(stored_id), 0 if alias else 1,
             str(stored_id) if alias else None, recovered_from, encounter_id, defeat_count,
             _canonical(policy), _canonical(policy), label, origin_source, source.get('label'),
             source.get('ownership', 'unknown'), source.get('path'), root, parent, depth,
             depth_known, operation, target, change, proposal, search_space_version, created))
        scene_json = _canonical(scenario)
        raw = scene_json.encode('utf-8')
        blob = zlib.compress(raw, 6)
        dest.execute(
            'INSERT OR IGNORE INTO history_scenario(candidate_id,scenario_digest,encounter_id,'
            'defeat_count,codec,plain_sha256,compressed_bytes,uncompressed_bytes,scenario_zlib) '
            'VALUES(?,?,?,?,?,?,?,?,?)',
            (identity, identity, encounter_id, defeat_count, SCENARIO_CODEC,
             hashlib.sha256(raw).hexdigest(), len(blob), len(raw), blob))
        profile = dict(identity=identity, source_id=source_id, label=label,
                       origin_source=origin_source, recovered_from=recovered_from,
                       encounter_id=encounter_id, defeat_count=defeat_count, root=root,
                       parent=parent, depth=depth, depth_known=depth_known, operation=operation,
                       target=target, change=change, proposal=proposal,
                       search_space_version=search_space_version, created=int(created or 0),
                       region=region or '', lineage_source=lineage_source)
        profile.update(profiles.get(identity) or {})
        previous = pool.get(identity)
        if previous is None or _profile_rank(profile) > _profile_rank(previous):
            pool[identity] = profile
        return identity

    for record in records:
        reader = record['reader']
        source_id = by_path[str(Path(record['spec']['path']).resolve())]
        meta_rows = {}
        if reader.has_table('candidate_meta'):
            for cid, encounter, defeat, region in reader.db.execute(
                    'SELECT id,encounter,defeat,region FROM candidate_meta'):
                meta_rows[cid] = (encounter, defeat, region)
        lineage_rows = {}
        if reader.has_table('lineage'):
            for row in reader.db.execute(
                    'SELECT candidate,parent,root,depth,operation,target,change,source,encounterId,'
                    'proposal,searchSpaceVersion,created FROM lineage'):
                lineage_rows[row[0]] = row
        if reader.has_table('candidate'):
            for cid, scenario_json, label, origin_source, created in reader.db.execute(
                    'SELECT id,scenario,label,source,created FROM candidate'):
                try:
                    scenario = json.loads(scenario_json)
                except (TypeError, ValueError):
                    note(source_id, 'malformed-scenario')
                    continue
                if not isinstance(scenario, dict):
                    note(source_id, 'malformed-scenario')
                    continue
                meta = meta_rows.get(cid)
                lineage = lineage_rows.get(cid)
                insert(source_id, cid, scenario, recovered_from='candidate', label=label,
                       origin_source=origin_source,
                       root=lineage[2] if lineage else None, parent=lineage[1] if lineage else None,
                       depth=lineage[3] if lineage else 0, depth_known=1 if lineage else 0,
                       operation=lineage[4] if lineage else None,
                       target=lineage[5] if lineage else None,
                       change=lineage[6] if lineage else None,
                       proposal=lineage[9] if lineage else None,
                       search_space_version=lineage[10] if lineage else None,
                       created=created,
                       region=meta[2] if meta else '',
                       lineage_source=lineage[7] if lineage else None,
                       encounter=meta[0] if meta else None,
                       defeat=meta[1] if meta else None)
        if reader.has_table('predictor_history'):
            for (cid, scenario_zlib, root, encounter, created, discovery, validation,
                 pruned) in reader.db.execute(
                    'SELECT candidate,scenario_zlib,root,encounter,created,discovery,validation,'
                    'pruned FROM predictor_history'):
                try:
                    scenario = json.loads(zlib.decompress(scenario_zlib).decode('utf-8'))
                except (zlib.error, UnicodeDecodeError, TypeError, ValueError):
                    note(source_id, 'malformed-predictor-scenario')
                    continue
                if not isinstance(scenario, dict):
                    note(source_id, 'malformed-predictor-scenario')
                    continue
                identity = insert(source_id, cid, scenario, recovered_from='predictor_history',
                                  label=None, origin_source=None, root=root, parent=None, depth=0,
                                  depth_known=0, operation=None, target=None, change=None,
                                  proposal=None, search_space_version=None, created=created,
                                  encounter=encounter)
                if identity is None:
                    continue
                for phase, raw in (('discovery', discovery), ('validation', validation)):
                    fields = _aggregate_only(raw)
                    if fields is None:
                        continue
                    note(source_id, 'predictor-aggregate-unseeded', 1,
                         'pruned per-phase aggregate retained as aggregate-only evidence; no seed '
                         'identity, so it is never expanded into measurements or a distribution')
                    dest.execute(
                        'INSERT INTO history_aggregate(source_id,candidate_id,scenario_digest,'
                        'encounter_id,defeat_count,phase,raw_json,fields_json,measurement_count,'
                        'has_seed_identity,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (source_id, identity, identity, encounter, None, phase,
                         _canonical(raw), _canonical(fields),
                         fields.get('measurementCount'), 0,
                         'aggregate-only historical evidence; may seed a candidate proposal but '
                         'never claims a confirmed reward, mean or distribution'))
    for profile in _select_active(list(pool.values())):
        _materialise_active(dest, profile)
    return omissions


def _scenario_meta(scenario):
    """``(encounterId, defeatCount)`` from a scenario, both optional."""
    encounter = scenario.get('encounterId')
    encounter = encounter if isinstance(encounter, int) and not isinstance(encounter, bool) else None
    defeat = scenario.get('defeatCount')
    defeat = defeat if isinstance(defeat, int) and not isinstance(defeat, bool) else None
    return encounter, defeat


#: Pruned predictor aggregates carry old rankings/scores that are not evidence.
_AGGREGATE_DROP = ('rank', 'score', 'quality')


def _aggregate_only(raw):
    """Raw statistical fields of a pruned aggregate, minus old rankings/scores; or ``None``.

    Kept as an *aggregate-only* record attributed to its source. No seed identity is present, so the
    caller must never expand it into a histogram, a mean or a count of independent measurements, and
    overlapping aggregates are never summed. A numeric size that the raw itself carries is exposed
    honestly as ``measurementCount`` (and labelled not-independent); nothing is synthesised.
    """
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or not value:
        return None
    fields = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(token in lowered for token in _AGGREGATE_DROP):
            continue
        fields[str(key)] = item
    if not fields:
        return None
    count = None
    for key in ('measurementCount', 'measurements', 'samples', 'runs', 'n', 'count'):
        candidate = value.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            count = candidate
            break
    fields['measurementCount'] = count
    return fields


# --- build --------------------------------------------------------------------------------------
def _source_records(specs, *, immutable, hash_sources):
    records = []
    for spec in specs:
        reader = open_source(spec['path'], immutable=immutable)
        try:
            _require_schema(reader)
            reader.begin()
            provenance = reader.meta('provenance')
            digest = (provenance or {}).get('digest') if isinstance(provenance, dict) else None
            record = dict(
            spec=spec, reader=reader,
            sha256=_sha256_file(spec['path']) if hash_sources else None,
            pageCount=reader.db.execute('PRAGMA page_count').fetchone()[0],
            schema=reader.meta('schema'), objectiveVersion=reader.meta('objectiveVersion'),
            searchSpaceVersion=reader.meta('searchSpaceVersion'),
            provenance=provenance if isinstance(provenance, dict) else {},
            provenanceDigest=digest,
            totalRuns=reader.meta('totalRuns'), timedRuns=reader.meta('timedRuns'),
            prunedOrdinals=reader.meta('prunedOrdinals'),
            retainedRuns=reader.count('run'), evidenceRows=reader.count('evidence'),
            snapshotNote=('source sha256 read from the file at import time; the read-only '
                          'transaction is a consistent snapshot, but the file bytes are not '
                          're-verified against it'),
            )
        except Exception:
            reader.close()
            raise
        records.append(record)
    return records


def _grant_record(spec, provenance):
    """An explicitly imported grant, pinned to the reviewed certificate bytes (never a new stamp).

    The grant is recorded (not applied) here: it names the certificate and the source-inventory
    digest it was reviewed against. Whether an old source is compatible is decided again at *read*
    time by ``_engine_match`` re-running ``strategy_encounter_migration.preview`` against the current
    pinned certificate and the caller's provenance, so a later certificate or runtime change cannot
    silently inherit an old grant. Original source provenance is preserved alongside.
    """
    if not spec.get('migrationGrant'):
        return 0, '{}'
    try:
        import strategy_encounter_migration as migration
        certificate = hashlib.sha256(Path(migration.CERTIFICATE).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001 - a grant that cannot be pinned is not recorded
        return 0, _canonical(dict(error='observer-migration certificate unavailable'))
    try:
        digest = domain.identity(provenance) if isinstance(provenance, dict) else None
    except Exception:  # noqa: BLE001
        digest = None
    return 1, _canonical(dict(certificate=certificate, sourceFromDigest=digest,
                              note='observer-only migration grant; re-verified at read time'))


def _insert_sources(dest, records, imported_at):
    sources = {}
    for record in records:
        spec = record['spec']
        grant, grant_json = _grant_record(spec, record['provenance'])
        cursor = dest.execute(
            'INSERT INTO history_source(source_path,label,ownership,source_sha256,page_count,'
            'library_schema,objective_version,search_space_version,provenance_digest,'
            'provenance_json,lifetime_total_runs,lifetime_timed_runs,retained_runs,evidence_rows,'
            'pruned_ordinals,migration_grant,migration_grant_json,snapshot_note,imported_at,'
            'tool_version) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (str(Path(spec['path']).resolve()), spec['label'], spec['ownership'], record['sha256'],
             record['pageCount'], record['schema'], record['objectiveVersion'],
             record['searchSpaceVersion'], record['provenanceDigest'],
             _canonical(record['provenance']), record['totalRuns'], record['timedRuns'],
             record['retainedRuns'], record['evidenceRows'], _canonical(record['prunedOrdinals']),
             grant, grant_json, record['snapshotNote'], imported_at, TOOL_VERSION))
        source_id = cursor.lastrowid
        sources[source_id] = dict(path=str(Path(spec['path']).resolve()), label=spec['label'],
                                  ownership=spec['ownership'],
                                  provenanceDigest=record['provenanceDigest'],
                                  provenance=record['provenance'], migrationGrant=bool(grant))
    return sources


def _insert_omissions(dest, record, source_id, extra=None):
    lifetime = record['totalRuns'] if isinstance(record['totalRuns'], int) else None
    evidence = record['evidenceRows']
    for kind, count, detail in (
            ('run-replay', record['retainedRuns'],
             'retained replay rows are not copied; evidence is the compact survivor record'),
            ('archive-ranking', record['reader'].count('archive'),
             'old archive quality/rankings are never treated as evidence'),
            ('predictor-history', record['reader'].count('predictor_history'),
             'pruned-candidate scenarios/lineage recovered as compressed history + attribution; '
             'only a bounded slice is an active resident'),
            ('rebel-break', record['reader'].count('rebel_break'),
             'rebel ledger is an active-campaign record, not measured history')):
        if count is None:
            continue
        dest.execute('INSERT INTO history_omission(source_id,kind,count,detail) VALUES(?,?,?,?)',
                     (source_id, kind, int(count), detail))
    if lifetime is not None and evidence is not None and lifetime > evidence:
        dest.execute('INSERT INTO history_omission(source_id,kind,count,detail) VALUES(?,?,?,?)',
                     (source_id, 'lifetime-without-evidence', int(lifetime) - int(evidence),
                      'lifetime attempts with no retained evidence row; never extrapolated'))
    for kind, count in (extra or {}).items():
        dest.execute('INSERT INTO history_omission(source_id,kind,count,detail) VALUES(?,?,?,?)',
                     (source_id, kind, int(count),
                      'retained rows with unusable identity/condition; kept as an explicit '
                      'omission, never silently skipped'))


def _write_meta(dest, **values):
    for key, value in values.items():
        dest.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, _canonical(value)))


def build_library(sources, out, *, authorised=False, batch_size=DEFAULT_BATCH, hash_sources=True,
                  immutable=False, grants=()):
    """Build a fresh compact history-summary library. Returns a report dict.

    ``sources`` is a list of ``{'path': ..., 'label': ..., 'ownership': ...}``. The destination must
    not exist. On success the staging file is renamed to ``out``; on failure it is left as
    ``<out>.partial`` and the exception propagates.
    """
    if not authorised:
        raise NotAuthorised('--authorised is required for a real import; no file was written')
    grant_paths = {str(Path(g).resolve()) for g in (grants or ())}
    for spec in sources:
        if str(Path(spec['path']).resolve()) in grant_paths:
            spec['migrationGrant'] = True
    out = Path(out)
    if out.exists():
        raise FileExistsError('destination already exists (no overwrite): %s' % out)
    resolved_out = out.resolve()
    for spec in sources:
        source = Path(spec['path']).resolve()
        if source == resolved_out:
            raise HistorySummaryError('destination must not be a source: %s' % source)
        if not source.is_file():
            raise FileNotFoundError('source does not exist: %s' % source)
        if str(source).startswith(str(resolved_out) + os.sep):
            raise HistorySummaryError('destination must not be an ancestor directory of a source')
    staging = out.with_name(out.name + '.partial')
    if staging.exists():
        raise FileExistsError('partial staging already exists; remove it first: %s' % staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    imported_at = time.time()
    readers, store, scratch, scratch_path, inserted = [], None, None, None, 0
    try:
        records = _source_records(sources, immutable=immutable, hash_sources=hash_sources)
        readers = [record['reader'] for record in records]
        store = _create_store(staging)
        dest = store.db
        dest.executescript(';\n'.join(_HISTORY_SCHEMA))
        with dest:
            sources_map = _insert_sources(dest, records, imported_at)
            engine_groups = _engine_groups({sid: info['provenance']
                                            for sid, info in sources_map.items()})
            handle, scratch_path = tempfile.mkstemp(prefix='ka-history-stage-', suffix='.sqlite')
            os.close(handle)
            scratch = sqlite3.connect(scratch_path, isolation_level=None)
            scratch.execute('PRAGMA journal_mode=OFF')
            scratch.execute('PRAGMA synchronous=OFF')
            scratch.execute('PRAGMA temp_store=MEMORY')
            scratch.executescript(';\n'.join(_SCRATCH_SCHEMA))
            custom_sources = {}
            holdout_sources = {}
            by_path = {info['path']: sid for sid, info in sources_map.items()}
            for record in records:
                source_id = by_path[str(Path(record['spec']['path']).resolve())]
                reader = record['reader']
                seed = seed_exclusion_report(reader)
                measured = _stream_measurements(scratch, reader, source_id,
                                                engine_groups[source_id], batch_size)
                custom_sources[source_id] = bool(measured['custom']) \
                    or seed['customSeedIdentity']
                holdout_sources[source_id] = bool(seed['holdoutBlocked'])
                seed_bounds = seed.get('bounds') or {}
                bank_ceilings = seed_bounds.get('bankCeilings') or {}
                for phase, ceiling in bank_ceilings.items():
                    dest.execute(
                        'INSERT INTO history_seed_exclusion(source_id,phase,bank_ceiling,'
                        'aggregate_ceiling,declared_json,observed_pairs,bank_pairs,'
                        'exception_pairs,custom_seed_identity,holdout_blocked,malformed_pairs,'
                        'missing_seed_rows,coverage_json,provenance_json)'
                        ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (source_id, phase, int(ceiling),
                         int((seed_bounds.get('aggregate') or {}).get(phase, 0)),
                         _canonical(seed_bounds.get('declared') or {}), int(seed['observedPairs']),
                         int(ceiling),
                         int(len(seed['exceptionPairs'])),
                         1 if custom_sources[source_id] else 0,
                         1 if seed['holdoutBlocked'] else 0,
                         int(seed.get('malformedPairs') or 0),
                         int(seed.get('missingSeedRows') or 0),
                         _canonical(seed.get('coverage') or {}),
                         _canonical(seed['provenance'])))
                if not bank_ceilings:
                    dest.execute('INSERT INTO history_omission(source_id,kind,count,detail) '
                                 'VALUES(?,?,?,?)',
                                 (source_id, 'seed-coverage-unavailable', 0,
                                  seed.get('reason') or 'no deterministic bank ceiling recorded')) 
                for seed_a, seed_b in seed['exceptionPairs']:
                    dest.execute('INSERT OR IGNORE INTO history_seed_exception'
                                 '(source_id,seed_a,seed_b) VALUES(?,?,?)',
                                 (source_id, seed_a, seed_b))
                dest.execute('UPDATE history_source SET custom_seed_identity=?,holdout_blocked=? '
                             'WHERE source_id=?',
                             (1 if custom_sources[source_id] else 0,
                              1 if seed['holdoutBlocked'] else 0, source_id))
                _insert_omissions(dest, record, source_id, extra=measured['omitted'])
            inserted = _aggregate(scratch, dest, sources_map)
            block_count = _mine_blocks(scratch, dest, sources_map, imported_at)
            candidate_omissions = _recover_candidates(dest, records, sources_map, imported_at)
            for (source_id, kind), (count, detail) in candidate_omissions.items():
                dest.execute('INSERT INTO history_omission(source_id,kind,count,detail) '
                             'VALUES(?,?,?,?)', (source_id, kind, int(count), detail))
            holdout = any(holdout_sources.values())
            _write_meta(dest, historySummaryVersion=SUMMARY_VERSION, historyToolVersion=TOOL_VERSION,
                        historyBlockVersion=BLOCK_VERSION, historyBlockCodec=BLOCK_CODEC,
                        historyImportedAt=imported_at, historySourceCount=len(records),
                        historyImportedRuns=sum(r['retainedRuns'] or 0 for r in records),
                        historyImportedEvidence=sum(r['evidenceRows'] or 0 for r in records),
                        historyImportedMeasurements=inserted, historySeedBlocks=block_count,
                        historyHoldoutBlocked=holdout,
                        historyProvenanceClass=PROVENANCE_CLASS)
        dest.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        dest.execute('PRAGMA journal_mode=DELETE')
        dest.close()
        store = None
        if out.exists():
            raise FileExistsError('destination appeared during build; refusing to overwrite: %s'
                                  % out)
        os.rename(staging, out)
        return dict(version=VERSION, out=str(resolved_out), sources=len(records),
                    summaries=inserted, seedBlocks=block_count, holdoutBlocked=holdout,
                    customSeedSources=sum(1 for value in custom_sources.values() if value))
    finally:
        if store is not None:
            try:
                store.db.close()
            except Exception:  # noqa: BLE001 - best-effort close of a failed staging connection
                pass
        if scratch is not None:
            try:
                scratch.close()
            except Exception:  # noqa: BLE001
                pass
        for reader in readers:
            try:
                reader.close()
            except Exception:  # noqa: BLE001 - best-effort close of read-only handles
                pass
        if scratch_path is not None:
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.remove(scratch_path + suffix)
                except OSError:
                    pass


# --- pure loader --------------------------------------------------------------------------------
def _resolve_db(db):
    if isinstance(db, (str, Path)):
        reader = SourceReader(db, immutable=False)
        return reader.db, reader
    if isinstance(db, sqlite3.Connection):
        return db, None
    if hasattr(db, 'db'):
        return db.db, None
    raise HistorySummaryError('db must be a path, a sqlite3 connection, or an object exposing .db')


def _summary_version(connection):
    """``(version, reason, detail)`` for the validated summary-library version stamp."""
    try:
        row = connection.execute(
            "SELECT value FROM meta WHERE key='historySummaryVersion'").fetchone()
    except sqlite3.OperationalError as exc:
        return None, 'unreadable-summary-library', str(exc)
    if row is None:
        return None, 'not-a-summary-library', 'no historySummaryVersion stamp'
    try:
        return int(json.loads(row[0])), None, ''
    except (TypeError, ValueError):
        return None, 'corrupt-summary-version', 'historySummaryVersion is not an integer'


def _candidate_scenario(connection, cid):
    """The exact stored scenario for a candidate, or a fail-closed reason.

    An active candidate is read from ``candidate``; a build that is retained only in the compressed
    history (never a resident) falls back to ``history_scenario`` losslessly, so a summary prior is
    still provable for a historical candidate that was pruned from the active population.
    """
    try:
        row = connection.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
    except sqlite3.OperationalError as exc:
        return None, 'unreadable-scenario', str(exc)
    if row is None:
        return _history_scenario(connection, cid)
    try:
        scenario = json.loads(row[0])
    except (TypeError, ValueError):
        return None, 'unreadable-scenario', 'stored scenario is not valid JSON'
    if not isinstance(scenario, dict):
        return None, 'unreadable-scenario', 'stored scenario is not a mapping'
    return scenario, None, ''


def _history_scenario(connection, cid):
    """The exact compressed-history scenario for a non-resident candidate, or a fail-closed reason."""
    try:
        row = connection.execute(
            'SELECT codec,scenario_zlib FROM history_scenario WHERE candidate_id=?', (cid,)).fetchone()
    except sqlite3.OperationalError:
        return None, 'missing-scenario', 'no candidate row for this id'
    if row is None:
        return None, 'missing-scenario', 'no candidate row for this id'
    if row[0] != SCENARIO_CODEC:
        return None, 'unreadable-scenario', 'stored history scenario has an unknown codec'
    try:
        scenario = json.loads(zlib.decompress(row[1]).decode('utf-8'))
    except (zlib.error, UnicodeDecodeError, TypeError, ValueError):
        return None, 'unreadable-scenario', 'stored history scenario is not valid zlib JSON'
    if not isinstance(scenario, dict):
        return None, 'unreadable-scenario', 'stored history scenario is not a mapping'
    return scenario, None, ''


def _candidate_family(connection, cid):
    """Honest attribution: the per-candidate source, else the source label/ownership.

    The source's ownership label is never applied *as* the candidate's own origin; the stored
    per-candidate source wins and the ownership is kept only as a fallback attribution.
    """
    record = dict(candidateId=cid, originSource=None, sourceLabel=None, sourceOwnership=None,
                  sourceId=None, sourcePath=None, recoveredFrom=None, aliasOf=None,
                  evidenceFamily=None)
    try:
        row = connection.execute(
            'SELECT source_id,origin_source,source_label,source_ownership,source_path,'
            'recovered_from,alias_of FROM history_candidate WHERE candidate_id=? '
            'ORDER BY source_id LIMIT 1', (cid,)).fetchone()
    except sqlite3.OperationalError:
        return record
    if row is None:
        return record
    record.update(sourceId=row[0], originSource=row[1], sourceLabel=row[2],
                  sourceOwnership=row[3], sourcePath=row[4], recoveredFrom=row[5], aliasOf=row[6])
    record['evidenceFamily'] = (record['originSource'] or record['sourceLabel']
                                or record['sourceOwnership'])
    return record


def _grant_basis(connection, group, stored, engine_revision):
    """Read-time verification of a recorded observer-migration grant (never a universal stamp).

    The recorded grant names the reviewed certificate bytes and the source inventory it was
    imported against. It is honoured only when the pinned certificate is byte-identical now AND the
    existing ``strategy_encounter_migration.preview`` proves the stored source provenance is the
    reviewed ``sourceFrom`` for the caller's current provenance (``sourceTo``), re-checking the
    native assets. A changed certificate or runtime therefore cannot inherit an old grant, and the
    grant is recorded (never applied) to the destination only.
    """
    try:
        row = connection.execute(
            'SELECT migration_grant,migration_grant_json FROM history_source '
            'WHERE provenance_digest=?', (group,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or not row[0]:
        return None
    try:
        recorded = json.loads(row[1]) if row[1] else {}
    except (TypeError, ValueError):
        return None
    certificate = recorded.get('certificate') if isinstance(recorded, dict) else None
    if not certificate:
        return None
    try:
        import strategy_encounter_migration as migration
        current = hashlib.sha256(Path(migration.CERTIFICATE).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001 - a grant that cannot be re-verified is not applied
        return None
    if current != certificate:
        return None
    if not isinstance(stored, dict) or not stored.get('files'):
        return None
    plan = migration.preview(stored, engine_revision)
    if not plan.get('eligible'):
        return None
    return dict(basis='migration-certificate', proof=plan.get('proof'),
                planId=plan.get('planId'), limitation=plan.get('limitation'),
                sourceFrom=recorded.get('sourceFromDigest'), certificate=certificate)


def _engine_match(row, engine_revision, connection=None):
    """Prove the summary's engine group matches the caller, or a specific refusal reason.

    Exact digest and canonical ``same_simulator`` equivalence are accepted as before. A genuinely
    different stored provenance is NOT bypassed: it is accepted only through a recorded,
    read-time-verified observer-migration grant for that exact source group.
    """
    group = row['engine_compat_group']
    if not engine_revision:
        return False, 'engine-revision-missing', 'no engine revision was supplied', None
    try:
        stored = json.loads(row['engine_provenance_json']) if row['engine_provenance_json'] else None
    except (TypeError, ValueError):
        stored = None
    if isinstance(engine_revision, str):
        if engine_revision == group or engine_revision == row['engine_digest']:
            return True, 'exact', '', 'exact'
        return False, 'engine-revision-mismatch', 'summary engine group %s != %s' % (
            str(group)[:12], engine_revision[:12]), None
    if isinstance(engine_revision, dict):
        digest = engine_revision.get('digest')
        if digest and (digest == group or digest == row['engine_digest']):
            return True, 'exact', '', 'exact'
        if isinstance(stored, dict) and _same_simulator(stored, engine_revision):
            return True, 'exact-simulator-equivalence', '', 'exact-simulator-equivalence'
        grant = _grant_basis(connection, group, stored, engine_revision) if connection is not None \
            else None
        if grant:
            return True, 'migration-certificate', '', grant['basis']
        return False, 'engine-revision-mismatch', 'summary engine group is not equivalent', None
    return False, 'engine-revision-missing', 'engine_revision must be a digest or mapping', None


def _find_summary(connection, cid, encounter_key, policy_declared, window, engine_revision):
    try:
        cursor = connection.execute(
            'SELECT * FROM history_build_summary WHERE candidate_id=? AND encounter_id=? '
            'AND defeat_count=? AND evidence_window=?',
            (cid, encounter_key['encounterId'], encounter_key['defeatCount'], window))
        rows = list(_rows_as_dicts(cursor))
    except sqlite3.OperationalError as exc:
        return None, 'unreadable-summary-library', str(exc), None
    if not rows:
        return None, 'no-history-summary', 'no retained summary for this candidate/window', None
    last = ('engine-revision-missing', 'no engine revision was supplied', None)
    for row in rows:
        try:
            stored_policy = json.loads(row['policy_json'])
        except (TypeError, ValueError):
            stored_policy = {}
        matched, policy_detail = legacy._policy_matches(policy_declared, stored_policy)
        if not matched:
            last = ('policy-mismatch', policy_detail, None)
            continue
        ok, reason, detail, basis = _engine_match(row, engine_revision, connection)
        if ok:
            return row, None, '', basis
        last = (reason, detail, None)
    return None, last[0], last[1], None


def _validate_scenario(cid, scenario, encounter_key, constraints):
    """Per-candidate exact stored-scenario validation; ``None`` means admitted."""
    try:
        identity = domain.identity(scenario)
    except Exception as exc:  # noqa: BLE001 - an unhashable scenario is never trusted
        return dict(candidateId=cid, reason='identity-mismatch',
                    detail='stored scenario does not hash: %s' % (exc,), counts={})
    if identity != cid:
        return dict(candidateId=cid, reason='identity-mismatch',
                    detail='stored scenario does not hash to the requested candidate id', counts={})
    if scenario.get('encounterId') != encounter_key['encounterId']:
        return dict(candidateId=cid, reason='encounter-mismatch',
                    detail='stored encounterId %r != requested %r'
                    % (scenario.get('encounterId'), encounter_key['encounterId']), counts={})
    stored_defeat = scenario.get('defeatCount')
    try:
        stored_defeat = int(stored_defeat if stored_defeat is not None else 0)
    except (TypeError, ValueError):
        return dict(candidateId=cid, reason='unreadable-scenario',
                    detail='stored defeatCount is not an integer', counts={})
    if stored_defeat != encounter_key['defeatCount']:
        return dict(candidateId=cid, reason='encounter-mismatch',
                    detail='stored defeatCount %r != requested %r'
                    % (scenario.get('defeatCount'), encounter_key['defeatCount']), counts={})
    if constraints is not None:
        try:
            validated = domain.validate_constraints(scenario, constraints)
        except Exception as exc:  # noqa: BLE001
            return dict(candidateId=cid, reason='constraints-invalid',
                        detail='constraint validation failed: %s' % (exc,), counts={})
        if not validated.get('valid'):
            return dict(candidateId=cid, reason='constraints-mismatch',
                        detail='stored scenario violates the supplied constraints', counts={})
    return None


def _observation(row, encounter_key, mechanics_revision, scenario, family, basis, grant):
    try:
        moments = json.loads(row['moments_json'])
    except (TypeError, ValueError):
        moments = {}
    try:
        histogram = json.loads(row['reward_histogram_json'])
    except (TypeError, ValueError):
        histogram = {}
    resolved = int(row['resolved_count'])
    samples = int(row['sample_count'])
    stdev = moments.get('stdev')
    standard_error = stdev / (resolved ** 0.5) if (resolved >= 2 and stdev is not None) else None
    complete = (row['mean_earned'] is not None and int(row['conflicting_rewards']) == 0)
    features = legacy._joint_features(scenario)
    summary = dict(moments)
    summary.setdefault('resolvedCount', resolved)
    summary.setdefault('total', samples)
    summary['meanEarned'] = row['mean_earned']
    summary['partialMean'] = row['partial_mean_earned']
    engine = dict(storedDigest=row['engine_digest'], basis=basis,
                  compatibilityGroup=row['engine_compat_group'])
    if grant:
        engine.update(proof=grant.get('proof'), planId=grant.get('planId'),
                      limitation=grant.get('limitation'))
    return dict(
        candidateId=row['candidate_id'],
        window=legacy.ADVISER_WINDOW,
        compatibility=dict(engineCompatibilityGroup=row['engine_compat_group'],
                           historicalStatus=row['status'], engineBasis=basis),
        meanEarned=row['mean_earned'],
        arithmeticMeanEarned=row['partial_mean_earned'],
        partialMean=row['partial_mean_earned'],
        resolved=resolved,
        samples=resolved,
        total=samples,
        unknownCount=int(row['unknown_count']),
        standardError=standard_error,
        uncertainty=dict(kind='sample-standard-error', value=standard_error,
                         note='over the available readings; heuristic, not a confidence interval'),
        reliability=(resolved / float(samples)) if samples else None,
        lossFrequency=moments.get('lossFrequency'),
        median=moments.get('median'),
        features=features,
        featuresAvailable=features is not None,
        eligibleForRecommendation=bool(complete and features is not None),
        adviserEligible=bool(complete and features is not None
                             and row['status'] in (STATUS_MEASURED, STATUS_PARTIAL)),
        evidenceClass=EVIDENCE_CLASS,
        provenanceClass=PROVENANCE_CLASS,
        independentConfirmation=False,
        confirmationEligible=False,
        historicalConfirmed=bool(row['historical_confirmed']),
        historicalStatus=row['status'],
        evidenceFamily=family.get('evidenceFamily'),
        originSource=family.get('originSource'),
        sourceOwnership=family.get('sourceOwnership'),
        sourceLabel=family.get('sourceLabel'),
        sourceId=family.get('sourceId'),
        sourcePath=family.get('sourcePath'),
        aliasOf=family.get('aliasOf'),
        sourcePhase=row['phase'],
        measurementWindow=row['evidence_window'],
        sourcePolicy=json.loads(row['policy_json']) if row['policy_json'] else {},
        sourceEncounter=dict(encounterId=encounter_key['encounterId'],
                             defeatCount=encounter_key['defeatCount']),
        mechanicsRevision=mechanics_revision,
        counts=dict(resolved=resolved, unknown=int(row['unknown_count']),
                    censored=int(row['censored_count']), errors=int(row['error_count']),
                    loss=int(row['loss_count']), samples=samples),
        ordinals=dict(considered=samples, deduplicated=int(row['duplicate_count']),
                      conflicts=int(row['conflicting_rewards']), malformed=0),
        rewardHistogram=histogram,
        moments=moments,
        summary=summary,
        quantiles=moments.get('quantiles'),
        stdev=stdev,
        byStatus=moments.get('byStatus'),
        byBasis=moments.get('byBasis'),
        domain=legacy._domain_block(scenario),
        engine=engine,
        provenance=json.loads(row['provenance_json']) if row['provenance_json'] else {},
    )


def _loader_result(observations, excluded, wanted, duplicates, invalid, reason, detail, window):
    by_reason = {}
    for entry in excluded:
        by_reason[entry['reason']] = by_reason.get(entry['reason'], 0) + 1
    return dict(
        version=VERSION,
        observations=observations,
        excluded=excluded,
        provenance=dict(source='history-summary', measurementWindow=window,
                        summaryVersion=SUMMARY_VERSION, independentConfirmation=False),
        counts=dict(requestedCandidates=len(wanted) + len(invalid),
                    uniqueCandidates=len(wanted), duplicateCandidateIds=duplicates,
                    invalidCandidateIds=len(invalid), includedCandidates=len(observations),
                    excludedCandidates=len(excluded),
                    observationsWithMean=sum(1 for o in observations if o['meanEarned'] is not None),
                    adviserEligible=sum(1 for o in observations if o.get('adviserEligible')),
                    byReason=by_reason),
        compatibility=dict(compatible=reason is None, reason=reason, detail=detail,
                           summaryVersion=SUMMARY_VERSION),
        limits=['Historical measurements only; never a fresh confirmation.',
                'Adviser features are derived from the exact stored scenario at read time.',
                'Constraints are validated against the exact stored scenario; a scenario that cannot '
                'be proven in scope is excluded, never admitted.',
                'A stored engine group is accepted only when exact, canonically equivalent, or '
                'covered by a recorded observer-migration grant re-verified at read time.'],
    )


def load_observations(db, candidate_ids, *, encounter, policy, mechanics_revision=None,
                      engine_revision=None, constraints=None,
                      maximum_candidates=legacy.DEFAULT_MAXIMUM_CANDIDATES,
                      window=DEFAULT_WINDOW, cache=None, rows_per_candidate=None, immutable=True):
    """Read historical priors from a summary library, in the legacy reader's output shape.

    A summary is only returned when the library version is recognised, the candidate's exact stored
    scenario proves identity/encounter/defeat/constraints/policy, and its engine-compat group proves
    the requested scope (exact, canonically equivalent, or a read-time-verified observer-migration
    grant). Adviser features are derived from the stored scenario with the shared legacy helpers, so
    a summary library is feature-bearing. ``rows_per_candidate``/``immutable`` are accepted for the
    legacy call shape and ignored (a summary has no replay prefix to bound).
    """
    connection, owned = _resolve_db(db)
    try:
        wanted, duplicates, invalid = legacy._unique_ids(candidate_ids)
        maximum = legacy._clamp_int(maximum_candidates, 1, legacy.MAXIMUM_CANDIDATES_CAP,
                                    legacy.DEFAULT_MAXIMUM_CANDIDATES)
        encounter_key, encounter_reason = legacy._normalize_encounter(encounter)
        policy_declared, policy_reason = legacy._normalize_policy(policy)
        excluded, observations = [], []
        for cid in invalid:
            excluded.append(dict(candidateId=repr(cid), reason='invalid-candidate-id',
                                 detail='candidate ids must be non-empty strings', counts={}))
        for cid in wanted[maximum:]:
            excluded.append(dict(candidateId=cid, reason='above-candidate-cap',
                                 detail='requested beyond maximum_candidates=%d' % maximum,
                                 counts={}))
        version, version_reason, version_detail = _summary_version(connection)
        if version != SUMMARY_VERSION:
            reason = version_reason or 'history-summary-version-unrecognised'
            detail = version_detail or ('summary version %r != supported %r'
                                        % (version, SUMMARY_VERSION))
            for cid in wanted[:maximum]:
                excluded.append(dict(candidateId=cid, reason=reason, detail=detail, counts={}))
            return _loader_result(observations, excluded, wanted, duplicates, invalid, reason,
                                  detail, window)
        reason = encounter_reason or policy_reason
        detail = encounter_reason or policy_reason
        if reason:
            for cid in wanted[:maximum]:
                excluded.append(dict(candidateId=cid, reason=reason, detail=detail, counts={}))
            return _loader_result(observations, excluded, wanted, duplicates, invalid, reason,
                                  detail, window)
        for cid in wanted[:maximum]:
            scenario, scen_reason, scen_detail = _candidate_scenario(connection, cid)
            if scenario is None:
                excluded.append(dict(candidateId=cid, reason=scen_reason, detail=scen_detail,
                                     counts={}))
                continue
            verdict = _validate_scenario(cid, scenario, encounter_key, constraints)
            if verdict is not None:
                excluded.append(verdict)
                continue
            row, fail_reason, fail_detail, basis = _find_summary(
                connection, cid, encounter_key, policy_declared, window, engine_revision)
            if row is None:
                excluded.append(dict(candidateId=cid, reason=fail_reason, detail=fail_detail,
                                     counts={}))
                continue
            grant = None
            if basis == 'migration-certificate':
                try:
                    stored = json.loads(row['engine_provenance_json']) \
                        if row['engine_provenance_json'] else None
                except (TypeError, ValueError):
                    stored = None
                grant = _grant_basis(connection, row['engine_compat_group'], stored, engine_revision)
            observations.append(_observation(row, encounter_key, mechanics_revision, scenario,
                                             _candidate_family(connection, cid), basis, grant))
        return _loader_result(observations, excluded, wanted, duplicates, invalid, None, '',
                              window)
    finally:
        if owned is not None:
            owned.close()


def aggregate_evidence(db):
    """Source-attributed aggregate-only records from pruned ``predictor_history`` blobs.

    These may seed a candidate *proposal* only. They carry no seed identity and are never a
    confirmed reward, mean or distribution, and overlapping aggregates are never summed.
    """
    connection, owned = _resolve_db(db)
    try:
        rows = []
        for row in _rows_as_dicts(connection.execute(
                'SELECT source_id,candidate_id,encounter_id,defeat_count,phase,fields_json,'
                'measurement_count,has_seed_identity,note FROM history_aggregate '
                'ORDER BY source_id,candidate_id,phase')):
            try:
                fields = json.loads(row['fields_json']) if row['fields_json'] else {}
            except (TypeError, ValueError):
                fields = {}
            rows.append(dict(sourceId=row['source_id'], candidateId=row['candidate_id'],
                             encounterId=row['encounter_id'], defeatCount=row['defeat_count'],
                             phase=row['phase'], fields=fields,
                             measurementCount=row['measurement_count'],
                             hasSeedIdentity=bool(row['has_seed_identity']),
                             independentConfirmation=False, confirmedReward=False,
                             note=row['note']))
        return dict(records=rows, count=len(rows), independentConfirmation=False,
                    confirmedReward=False)
    finally:
        if owned is not None:
            owned.close()

def _assert_version(connection):
    # Raise unless the library carries the exact supported summary version.
    version, reason, detail = _summary_version(connection)
    if version != SUMMARY_VERSION:
        raise HistorySummaryError(
            'history summary library is not usable: %s (%s)'
            % (reason or 'history-summary-version-unrecognised', detail))


def verify_blocks(db):
    """Decode every compressed seed block and fail closed on any integrity/count/codec problem."""
    connection, owned = _resolve_db(db)
    try:
        verified = 0
        for row in _rows_as_dicts(connection.execute(
                'SELECT candidate_id,engine_compat_group,block_version,codec,record_count,'
                'plain_sha256,block_zlib FROM history_seed_block ORDER BY block_id')):
            if row['codec'] != BLOCK_CODEC or int(row['block_version']) != BLOCK_VERSION:
                raise HistorySummaryError('seed block has an unknown codec/version: %s v%s'
                                          % (row['codec'], row['block_version']))
            _decode_block(row['block_zlib'], expected_count=row['record_count'],
                          expected_sha256=row['plain_sha256'])
            verified += 1
        return verified
    finally:
        if owned is not None:
            owned.close()


def forbidden_pairs(db, *, expand=True, verify=True):
    """The forbidden seed-pair view for later ``strategy_seed_freshness.collect`` integration.

    ``expand=True`` returns the conservative deterministic bank pairs (from the recorded ceilings)
    plus the exact stored exception pairs. ``expand=False`` returns only the compressed bounds,
    coverage and provenance. Off-bank exception pairs are exclusions, never a reason to block.
    With ``verify=True`` every compressed block is decoded and checked, so malformed retained seed
    data fails closed instead of being silently accepted.
    """
    connection, owned = _resolve_db(db)
    try:
        if verify:
            verify_blocks(connection)
        bounds, pairs, exceptions, holdout = [], set(), [], False
        coverage = dict(establishedPairs=0, exceptionPairs=0, unknownPairs=0)
        per_source = {}
        bank_ceilings = {}
        for row in _rows_as_dicts(connection.execute(
                'SELECT source_id,phase,bank_ceiling,aggregate_ceiling,declared_json,'
                'observed_pairs,exception_pairs,custom_seed_identity,holdout_blocked,'
                'malformed_pairs,missing_seed_rows,coverage_json FROM history_seed_exclusion '
                'ORDER BY source_id,phase')):
            try:
                entry_coverage = json.loads(row['coverage_json']) if row['coverage_json'] else {}
            except (TypeError, ValueError):
                entry_coverage = {}
            if not isinstance(entry_coverage, dict):
                entry_coverage = {}
            bounds.append(dict(sourceId=row['source_id'], phase=row['phase'],
                               bankCeiling=row['bank_ceiling'],
                               aggregateCeiling=row['aggregate_ceiling'],
                               declared=json.loads(row['declared_json']),
                               observedPairs=row['observed_pairs'],
                               exceptionPairs=row['exception_pairs'],
                               customSeedIdentity=bool(row['custom_seed_identity']),
                               malformedPairs=row['malformed_pairs'],
                               missingSeedRows=row['missing_seed_rows'],
                               coverage=entry_coverage))
            holdout = holdout or bool(row['holdout_blocked'])
            # The seed coverage is one source-level fact stored on every phase row; keep the first.
            per_source.setdefault(row['source_id'], dict(
                observed=row['observed_pairs'], exceptions=row['exception_pairs'],
                holdout=bool(row['holdout_blocked']), malformed=row['malformed_pairs'],
                missing=row['missing_seed_rows']))
            bank_ceilings[row['phase']] = max(bank_ceilings.get(row['phase'], 0),
                                              int(row['bank_ceiling']))
        for info in per_source.values():
            if info['holdout']:
                coverage['unknownPairs'] += int(info['missing'] or 0) + int(info['malformed'] or 0)
                continue
            observed = int(info['observed'] or 0)
            source_exceptions = int(info['exceptions'] or 0)
            coverage['establishedPairs'] += max(0, observed - source_exceptions)
            coverage['exceptionPairs'] += source_exceptions
            coverage['unknownPairs'] += int(info['missing'] or 0) + int(info['malformed'] or 0)
        for row in _rows_as_dicts(connection.execute(
                'SELECT seed_a,seed_b FROM history_seed_exception')):
            try:
                pair = (int(row['seed_a']), int(row['seed_b']))
            except (TypeError, ValueError) as exc:
                raise HistorySummaryError('stored seed exception is malformed: %s' % exc) from exc
            exceptions.append(pair)
            pairs.add(pair)
        if expand:
            limits = {phase: int(bank_ceilings.get(phase, 0)) for phase in freshness.PHASES}
            pairs |= freshness._deterministic_pairs(limits, lambda _where: None)
        return dict(pairs=pairs, bounds=bounds, exceptionPairs=exceptions, holdoutBlocked=holdout,
                    bankCeilings=bank_ceilings, coverage=coverage,
                    notes=[
                        'off-bank observed pairs are exact exclusions, not a block',
                        'missing historical seeds are incomplete coverage, never freshness',
                        'the strongest honest seed claim is: no collision with established '
                        'historical coverage',
                        'source lifetime counters are not unique runs across sources',
                        'a histogram alone never asserts independence',
                    ],
                    note='historical exclusions are preserved, never claimed fresh')
    finally:
        if owned is not None:
            owned.close()


def history_status(db):
    """Truthful, separate counters for the summary library (the new campaign stays at zero)."""
    connection, owned = _resolve_db(db)
    try:
        def scalar(sql, args=()):
            try:
                row = connection.execute(sql, args).fetchone()
            except sqlite3.OperationalError:
                return None
            return row[0] if row else None

        def meta(key):
            raw = scalar('SELECT value FROM meta WHERE key=?', (key,))
            try:
                return json.loads(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None
        version, version_reason, version_detail = _summary_version(connection)
        return dict(
            summaryVersion=version if version is not None else meta('historySummaryVersion'),
            summaryVersionRecognised=(version == SUMMARY_VERSION),
            summaryVersionReason=version_reason,
            aggregateRecords=scalar('SELECT COUNT(*) FROM history_aggregate'),
            blockVersion=meta('historyBlockVersion'),
            currentTotalRuns=meta('totalRuns'),
            importedSources=scalar('SELECT COUNT(*) FROM history_source'),
            importedRuns=meta('historyImportedRuns'),
            importedEvidence=meta('historyImportedEvidence'),
            importedMeasurements=meta('historyImportedMeasurements'),
            summaries=scalar('SELECT COUNT(*) FROM history_build_summary'),
            candidates=scalar('SELECT COUNT(DISTINCT candidate_id) FROM history_candidate'),
            scenarios=scalar('SELECT COUNT(*) FROM history_scenario'),
            activeCandidates=scalar('SELECT COUNT(*) FROM candidate'),
            seedBlocks=scalar('SELECT COUNT(*) FROM history_seed_block'),
            aliases=scalar('SELECT COUNT(*) FROM history_candidate WHERE alias_of IS NOT NULL'),
            omissions=scalar('SELECT COALESCE(SUM(count),0) FROM history_omission'),
            conflicts=scalar('SELECT COUNT(*) FROM history_build_summary WHERE status=?',
                             (STATUS_CONFLICT,)),
            unconfirmed=scalar('SELECT COUNT(*) FROM history_build_summary WHERE status=?',
                               (STATUS_UNCONFIRMED,)),
            historicalConfirmed=scalar(
                'SELECT COUNT(*) FROM history_build_summary WHERE historical_confirmed=1'),
            buildHoldoutBlocked=scalar(
                'SELECT COUNT(*) FROM history_source WHERE holdout_blocked=1'),
            holdoutBlocked=bool(meta('historyHoldoutBlocked')),
            provenanceClass=meta('historyProvenanceClass'),
            lifetimeCountersAreNotUniqueRuns=True,
            independentConfirmation=False,
        )
    finally:
        if owned is not None:
            owned.close()


# --- root integration adapter -------------------------------------------------------------------
def adviser_priors(db, candidate_ids, *, encounter, policy, mechanics_revision=None,
                   engine_revision=None, constraints=None,
                   maximum_candidates=legacy.DEFAULT_MAXIMUM_CANDIDATES,
                   window=DEFAULT_WINDOW, cache=None):
    """Adapter for later root integration: the observation list the adviser consumes.

    Kept separate from :func:`load_observations` so the existing consumer can be pointed at the
    summary library without changing its call shape.
    """
    return load_observations(db, candidate_ids, encounter=encounter, policy=policy,
                             mechanics_revision=mechanics_revision,
                             engine_revision=engine_revision, constraints=constraints,
                             maximum_candidates=maximum_candidates, window=window,
                             cache=cache)['observations']


def stored_scenario(db, candidate_id):
    """Pure on-demand lookup: the exact scenario for any recovered candidate, or ``None``.

    Reads the active ``candidate`` row when the build is resident, else decompresses the lossless
    ``history_scenario`` copy. This is the seam for later archived-build reuse: it hydrates one
    candidate on request and never restores the whole archived population.
    """
    connection, owned = _resolve_db(db)
    try:
        scenario, _reason, _detail = _candidate_scenario(connection, candidate_id)
        return scenario
    finally:
        if owned is not None:
            owned.close()


# --- CLI ----------------------------------------------------------------------------------------
def _spec_maps(values):
    mapping = {}
    for item in values or ():
        path, _, value = item.rpartition('=')
        if not path:
            raise HistorySummaryError('expected PATH=VALUE, got %r' % item)
        mapping[str(Path(path).resolve())] = value
    return mapping


def _build_specs(paths, labels, ownership):
    specs = []
    for raw in paths:
        path = Path(raw)
        resolved = str(path.resolve())
        specs.append(dict(path=resolved, label=labels.get(resolved, path.stem),
                          ownership=ownership.get(resolved, 'unknown')))
    return specs


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build a compact strategy history-summary library.')
    parser.add_argument('--source', action='append', required=True,
                        help='existing library to read (repeatable, read-only)')
    parser.add_argument('--out', required=True, help='NEW summary library path (must not exist)')
    parser.add_argument('--authorised', action='store_true', help='required for a real import')
    parser.add_argument('--source-label', action='append', default=[],
                        help='PATH=LABEL for one source (optional)')
    parser.add_argument('--source-ownership', action='append', default=[],
                        help='PATH=OWNERSHIP for one source, e.g. student9/community (optional)')
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    parser.add_argument('--no-hash', action='store_true', help='skip the source sha256 digest')
    parser.add_argument('--immutable', action='store_true',
                        help='open sources as immutable snapshots instead of live ro transactions')
    parser.add_argument('--grant-source', action='append', default=[],
                        help='PATH whose reviewed observer-migration certificate may be applied; '
                             'recorded (not applied) and re-verified at read time (repeatable)')
    args = parser.parse_args(argv)
    try:
        specs = _build_specs(args.source, _spec_maps(args.source_label),
                             _spec_maps(args.source_ownership))
        report = build_library(specs, args.out, authorised=args.authorised, batch_size=args.batch,
                               hash_sources=not args.no_hash, immutable=args.immutable,
                               grants=args.grant_source)
    except NotAuthorised as exc:
        print('refused: %s' % exc, file=sys.stderr)
        return 3
    except (HistorySummaryError, freshness.FreshnessError, sqlite3.Error, OSError) as exc:
        print('failed: %s' % exc, file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


__all__ = ['VERSION', 'SUMMARY_VERSION', 'TOOL_VERSION', 'BLOCK_VERSION', 'BLOCK_CODEC',
           'MAX_CANDIDATES_TOTAL', 'MAX_CANDIDATES_PER_ENCOUNTER', 'SCENARIO_CODEC',
           'DEFAULT_WINDOW', 'STATUS_MEASURED', 'STATUS_PARTIAL', 'STATUS_UNCONFIRMED',
           'STATUS_CONFLICT', 'HistorySummaryError', 'NotAuthorised', 'SourceReader', 'open_source',
           'build_library', 'load_observations', 'adviser_priors', 'forbidden_pairs',
           'history_status', 'stored_scenario', 'verify_blocks', 'seed_exclusion_report',
           'aggregate_evidence', 'main']
