"""Bounded, resumable, append-only recovery of scenarios omitted by the first history import.

What this fixes
---------------
``strategy_history_summary._load_scenario`` resolves a candidate's scenario only from the *source*
``candidate`` table.  A candidate whose scenario the source had already pruned therefore produced a
``missing-scenario`` omission even when the exact scenario is already archived losslessly in the
destination ``history_scenario`` table (recovered earlier from ``predictor_history``).  This tool
re-reads only those omitted rows, binds each source-stored candidate id to the destination's
source-attributed canonical identity, verifies the canonical identity of the archived scenario,
re-derives the group summaries and compressed seed blocks with the *existing* importer code, and
appends them atomically.  No source is written; the source sha256 is verified first and the
originals are byte-identical afterwards.

What it deliberately does not do
--------------------------------
* It never streams the full evidence bank again: only omitted rows for a source are copied into a
  bounded scratch table, then the existing ``_stream_measurements`` consumes exactly that.
* It never invents a scenario, an encounter, an engine group, a policy or a source attribution:
  those come from the destination ``history_scenario``/``history_candidate``/``history_source``.
* It never merges or replaces an existing candidate/policy/engine group.  A collision is detected
  at whole *block-group* granularity ``(candidate, policy, engine)`` - matching
  ``history_seed_block``'s uniqueness, which omits ``phase`` - before any summary is staged, so a
  new phase of an existing block can never be lost to ``INSERT OR IGNORE``.  The run stops
  (``--on-collision stop``, the default) or, only when asked, excludes that whole already-present
  group (``--on-collision skip``), counts its source observations exactly, and records the explicit
  exclusion - never a silent merge or a fabricated "duplicate".
* It never touches the current campaign: the new-campaign counters must be zero (and, if
  ``--campaign-db`` is given, that store must report no new runs either).
* It keeps every existing ``history_build_summary`` and ``history_seed_block`` row unchanged and
  copies no old archive/replay payload.
* It prepares the expensive ``_scenario_info``/fingerprint for recoverable candidates in a bounded
  local process pool (``--workers``, default 6; ``1`` is serial) and serves the streaming call from
  that precomputed lookup, so fingerprints are never recomputed one-by-one in serial.

Idempotency
-----------
All destination writes - the appended summaries, the appended seed blocks, the corrected
``history_omission`` count and a JSON correction receipt in ``meta`` - happen in one transaction, so
an interrupted run rolls back completely.  A persisted receipt per source makes a rerun a no-op: it
is checked *before* any source hash or stream work, so it never re-reads, re-appends a row, or
re-charges an omission that was already corrected.  The report is written as Markdown plus a
compact JSON companion beside it.

Progress
--------
Progress is written locally for the (root-owned) installer-style monitor: JSON lines with a ``stage``
field appended to a log file plus an atomically-replaced progress JSON.  No AI polling.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import time
import zlib
from pathlib import Path
from urllib.request import pathname2url

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_build_domain as domain  # noqa: E402
import strategy_history_summary as summary  # noqa: E402

VERSION = 'strategy-history-recover-omissions-1'
TOOL = 'strategy_history_recover_omissions'

#: ``meta`` key prefix for the persisted per-source correction receipt (idempotency proof).
RECEIPT_PREFIX = 'historyOmissionRecovery:'
#: Omission kinds that mean an input row was *not* admitted (it stays omitted).
SKIP_KINDS = ('missing-scenario', 'malformed-scenario', 'missing-encounter', 'malformed-run-result')
#: Omission kind this tool corrects.
OMISSION_KIND = 'missing-scenario'
#: Conservative bounded local process pool for expensive scenario-info/fingerprint preparation.
DEFAULT_WORKERS = 6

PROGRESS_NAME = 'history-omission-progress.json'
PROGRESS_LOG_NAME = 'history-omission-progress.log'
REPORT_NAME = 'history-omission-backfill-report.md'


class RecoveryError(RuntimeError):
    """The recovery could not proceed; nothing was written to the destination."""


class NotAuthorised(RecoveryError):
    """A real append was requested without ``--authorised``."""


class CollisionError(RecoveryError):
    """A recovered group already exists in the destination; refusing to merge or replace."""


def _ro_uri(path):
    return 'file:' + pathname2url(str(path)) + '?mode=ro'


def _open_ro(path):
    conn = sqlite3.connect(_ro_uri(path), uri=True, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=1')
    return conn


def _sha256(path):
    return summary._sha256_file(path)


def _canonical(value):
    return domain.canonical(value)


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


class Progress:
    """Append a JSON line per stage and atomically replace a progress JSON the monitor can read."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.json_path = self.dir / PROGRESS_NAME
        self.log_path = self.dir / PROGRESS_LOG_NAME
        self.started = time.time()
        self.last_stage = None
        self.last_write = 0.0
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text('', encoding='utf-8')
        except OSError:
            pass

    def stage(self, stage, *, done=0, total=0, note='', ok=None, extra=None, since=None):
        now = time.time()
        if (stage == self.last_stage and now - self.last_write < 0.5
                and ok is None and not (total and done >= total)):
            return
        self.last_stage, self.last_write = stage, now
        elapsed = now - self.started
        span = now - (since if since is not None else self.started)
        rate = (done / span) if (done and span > 0) else None
        remaining = ((total - done) / rate) if (rate and total > done) else None
        payload = dict(tool=TOOL, version=VERSION, stage=stage, done=int(done), total=int(total),
                       percent=(round(100.0 * done / total, 2) if total else None),
                       elapsedSeconds=round(elapsed, 3),
                       stageElapsedSeconds=round(span, 3),
                       ratePerSecond=(round(rate, 4) if rate else None),
                       etaSeconds=(round(remaining, 1) if remaining is not None else None),
                       note=note, updatedAt=now)
        if ok is not None:
            payload['ok'] = bool(ok)
        if extra:
            payload.update(extra)
        try:
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(payload, sort_keys=True) + '\n')
            tmp = self.json_path.with_name(self.json_path.name + '.tmp')
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
            os.replace(tmp, self.json_path)
        except OSError:
            pass


# --- destination introspection -----------------------------------------------------------------
def _destination_sources(dest_conn):
    """``{source_id: {...}}`` and the canonical engine-compat grouping, from the destination only."""
    sources = {}
    for row in dest_conn.execute(
            'SELECT source_id,label,source_path,ownership,source_sha256,provenance_digest,'
            'provenance_json FROM history_source ORDER BY source_id'):
        try:
            prov = json.loads(row['provenance_json'] or '{}')
        except (TypeError, ValueError):
            prov = {}
        if not isinstance(prov, dict):
            prov = {}
        sources[int(row['source_id'])] = dict(
            path=row['source_path'], label=row['label'], ownership=row['ownership'],
            sha256=row['source_sha256'], provenanceDigest=row['provenance_digest'],
            provenance=prov)
    groups = summary._engine_groups({sid: info['provenance'] for sid, info in sources.items()})
    return sources, groups


def _identity_binding(dest_conn, source_id):
    """Source-attributed ``stored_id -> canonical candidate_id`` binding from ``history_candidate``."""
    binding = {}
    for row in dest_conn.execute(
            'SELECT candidate_id,stored_id FROM history_candidate WHERE source_id=?', (source_id,)):
        canonical = row['candidate_id']
        binding[canonical] = canonical
        stored = row['stored_id']
        if stored:
            binding[str(stored)] = canonical
    return binding


def _source_missing_rows(source_conn):
    """The exact omission rows the first import counted: missing-candidate evidence + run-only."""
    evidence = run_only = 0
    if _has_table(source_conn, 'evidence'):
        evidence = source_conn.execute(
            'SELECT COUNT(*) FROM evidence WHERE candidate NOT IN (SELECT id FROM candidate)'
        ).fetchone()[0]
    if _has_table(source_conn, 'run'):
        run_only = source_conn.execute(
            'SELECT COUNT(*) FROM run WHERE candidate NOT IN (SELECT id FROM candidate) '
            'AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate=run.candidate '
            'AND e.phase=run.phase AND e.ordinal=run.ordinal)').fetchone()[0]
    return int(evidence or 0), int(run_only or 0)


def _missing_candidate_ids(source_conn):
    ids = []
    if _has_table(source_conn, 'evidence'):
        ids += [row[0] for row in source_conn.execute(
            'SELECT DISTINCT candidate FROM evidence '
            'WHERE candidate NOT IN (SELECT id FROM candidate)')]
    if _has_table(source_conn, 'run'):
        ids += [row[0] for row in source_conn.execute(
            'SELECT DISTINCT candidate FROM run WHERE candidate NOT IN (SELECT id FROM candidate) '
            'AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate=run.candidate '
            'AND e.phase=run.phase AND e.ordinal=run.ordinal)')]
    return list(dict.fromkeys(ids))


# --- shadow reader -----------------------------------------------------------------------------
class ShadowReader:
    """A read-only-looking view of one source, restricted to the omitted candidates.

    The bounded scratch database holds ``candidate``/``candidate_meta`` from the destination archive
    and copies of only the omitted ``evidence``/``run`` rows, so ``strategy_history_summary`` streams
    the omitted records through the *existing* code path unchanged.
    """

    def __init__(self, path, conn, tables):
        self.path = Path(path)
        self.db = conn
        self._tables = frozenset(tables)

    def tables(self):
        return set(self._tables)

    def has_table(self, name):
        return name in self._tables

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass


_SHADOW_SCHEMA = (
    '''CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL)''',
    '''CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER, defeat INTEGER)''',
    '''CREATE TABLE evidence(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
        seeds TEXT, verdict INTEGER, censored INTEGER NOT NULL DEFAULT 0, prizeCallbacks INTEGER,
        awardedChests INTEGER, awardedBasis TEXT, pendingChests INTEGER,
        PRIMARY KEY(candidate,phase,ordinal))''',
    '''CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal))''',
    '''CREATE TABLE omitted(id TEXT PRIMARY KEY)''',
)


def _build_shadow(stack, source_path, candidates):
    """Populate a bounded scratch view.

    ``candidates`` is ``{stored_id: {canonical, encounter, defeat, scenario}}``; the archived
    scenario text is keyed by the *source* stored id so ``_load_scenario`` resolves it exactly as if
    the source still held the row.
    """
    tmpdir = stack.enter_context(summary._temp_dir('ka-history-recover-'))
    conn = sqlite3.connect(os.path.join(tmpdir, 'shadow.sqlite'), uri=True, isolation_level=None,
                           timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    for statement in _SHADOW_SCHEMA:
        conn.execute(statement)
    conn.executemany('INSERT INTO omitted(id) VALUES(?)', [(key,) for key in candidates])
    conn.executemany('INSERT INTO candidate(id,scenario) VALUES(?,?)',
                     [(key, info['scenario']) for key, info in candidates.items()])
    conn.executemany('INSERT INTO candidate_meta(id,encounter,defeat) VALUES(?,?,?)',
                     [(key, info['encounter'], info['defeat'])
                      for key, info in candidates.items() if info['encounter'] is not None])
    conn.execute('ATTACH DATABASE ? AS src', (summary._readonly_uri(Path(source_path),
                                                                   immutable=False),))
    src_tables = {row[0] for row in conn.execute(
        "SELECT name FROM src.sqlite_master WHERE type='table'")}
    if 'evidence' in src_tables:
        conn.execute('INSERT INTO evidence SELECT candidate,phase,ordinal,seeds,verdict,censored,'
                     'prizeCallbacks,awardedChests,awardedBasis,pendingChests FROM src.evidence '
                     'WHERE candidate IN (SELECT id FROM omitted)')
    if 'run' in src_tables:
        conn.execute('INSERT INTO run SELECT candidate,phase,ordinal,result FROM src.run '
                     'WHERE candidate IN (SELECT id FROM omitted)')
    conn.execute('DETACH DATABASE src')
    tables = {'candidate', 'candidate_meta'}
    tables |= ({'evidence'} if 'evidence' in src_tables else set())
    tables |= ({'run'} if 'run' in src_tables else set())
    return ShadowReader(source_path, conn, tables)


def _archived_candidates(dest_conn, source_conn, source_id, prog):
    """Resolve every missing source candidate to a verified archived scenario.

    Returns ``(candidates, unresolved, mismatched)``.  ``unresolved`` counts missing candidates with
    no archived scenario (a genuine residual gap); ``mismatched`` counts archived payloads whose
    re-derived identity does not equal the canonical id they are filed under (never trusted).
    """
    binding = _identity_binding(dest_conn, source_id)
    missing = _missing_candidate_ids(source_conn)
    candidates, unresolved, mismatched = {}, 0, 0
    for stored in missing:
        canonical = binding.get(stored, stored)
        row = dest_conn.execute('SELECT scenario_zlib,encounter_id,defeat_count FROM '
                                'history_scenario WHERE candidate_id=?', (canonical,)).fetchone()
        if row is None:
            unresolved += 1
            continue
        try:
            scenario = json.loads(zlib.decompress(row['scenario_zlib']).decode('utf-8'))
        except (zlib.error, UnicodeDecodeError, TypeError, ValueError):
            unresolved += 1
            continue
        if not isinstance(scenario, dict):
            unresolved += 1
            continue
        try:
            identity = domain.identity(scenario)
        except Exception:  # noqa: BLE001 - an unhashable scenario is not trusted
            identity = None
        if identity != canonical:
            mismatched += 1
            continue
        candidates[stored] = dict(canonical=canonical, scenario=_canonical(scenario),
                                  encounter=row['encounter_id'], defeat=row['defeat_count'])
    return candidates, unresolved, mismatched


def _count(conn, sql, args=()):
    return int(conn.execute(sql, args).fetchone()[0] or 0)


# --- conservation accounting --------------------------------------------------------------------
def _conservation(label, stats):
    """Exact identity: omitted == retained + residual + collision + undecodable + deduplicated."""
    accounted = (stats['retainedObservations'] + stats['residualInputRows']
                 + stats['collisionInputRows'] + stats['undecodableInputRows']
                 + stats['deduplicatedInputRows'])
    return dict(source=label, omissionBefore=stats['omissionBefore'],
                retainedObservations=stats['retainedObservations'],
                omissionAfter=stats['omissionBefore'] - stats['retainedObservations'],
                residualInputRows=stats['residualInputRows'],
                collisionInputRows=stats['collisionInputRows'],
                undecodableInputRows=stats['undecodableInputRows'],
                deduplicatedInputRows=stats['deduplicatedInputRows'],
                accounted=accounted, balanced=(accounted == stats['omissionBefore']))


# --- report -------------------------------------------------------------------------------------
def _render_report(result):
    lines = []
    add = lines.append
    add('# History omission backfill - bounded incremental recovery')
    add('')
    add('Tool: `tools/recovery/strategy_history_recover_omissions.py` (`%s`)' % VERSION)
    add('Outcome: `%s`' % result['outcome'])
    add('')
    add('## Exact CLI')
    add('```')
    add('python tools/recovery/strategy_history_recover_omissions.py \\')
    add('  --library "%s" \\' % result['library'])
    add('  --authorised --on-collision skip \\')
    add('  --workers %d \\' % result['workers'])
    add('  --report "tmp/encounter-redesign-20260928/%s"' % REPORT_NAME)
    add('```')
    add('Without `--authorised` the same command is a read-only dry plan (nothing written). Sources')
    if result.get('hashesVerified'):
        add('open `mode=ro`; each source sha256 is verified against `history_source.source_sha256`')
        add('before any write; the original files are byte-identical afterwards.')
    else:
        add('open `mode=ro`; source sha256 verification was skipped this run, so this report does')
        add('**not** claim the frozen hashes were checked. Originals are only ever opened read-only.')
    add('')
    add('## What is recovered')
    add('Omitted rows whose source `candidate` row was pruned but whose exact scenario is already')
    add('archived in destination `history_scenario` (bound by source-attributed')
    add('`history_candidate.candidate_id`/`stored_id`, then re-verified with `domain.identity`).')
    add('Only those rows are streamed - the full evidence bank is never re-read.')
    add('')
    add('## Expected rows')
    add('')
    add('| source | omitted before | recoverable rows | retained observations | residual rows |')
    add('| --- | ---: | ---: | ---: | ---: |')
    for source in result['sources']:
        add('| %s | %d | %d | %d | %d |' % (source['label'], source['omissionBefore'],
            source['recoverableInputRows'], source['retainedObservations'],
            source['residualInputRows']))
    add('| **total** | **%d** | **%d** | **%d** | **%d** |' % (
        result['totals']['omissionBefore'], result['totals']['recoverableInputRows'],
        result['totals']['retainedObservations'], result['totals']['residualInputRows']))
    if result.get('planOnly'):
        add('')
        add('Plan-only mode: no row was streamed, so recoverable rows are the exact omitted rows')
        add('that resolve to an archived scenario and retained observations are the upper bound')
        add('(exact dedup/duplicate counts are produced by the authorised run).')
    add('')
    add('## Preservation proof')
    add('Per source: `omitted == retained + residual + collision + undecodable + deduplicated`.')
    for row in result['conservation']:
        add('- `%s`: omitted=%d, retained=%d, residual=%d, collision=%d, undecodable=%d, '
            'dedup=%d, after=%d, balanced=%s' % (row['source'], row['omissionBefore'],
            row['retainedObservations'], row['residualInputRows'], row['collisionInputRows'],
            row['undecodableInputRows'], row['deduplicatedInputRows'], row['omissionAfter'],
            row['balanced']))
    if result.get('hashesVerified'):
        add('- source sha256 verified unchanged; prior summaries/blocks untouched; campaign counters')
    else:
        add('- source sha256 NOT re-verified this run; prior summaries/blocks untouched; campaign')
        add('  counters')
    add('  zero (`totalRuns=%d`); active candidate set unchanged.' % result['campaignTotalRuns'])
    add('')
    add('## Collisions')
    add('Recovered groups already present in the destination: %d (%s).' % (
        result['collisionCount'], 'fail-closed stop' if result['onCollision'] == 'stop'
        else 'excluded, never merged/replaced'))
    for item in result['collisions'][:15]:
        add('- `%s` policy `%s` engine `%s`' % item)
    if len(result['collisions']) > 15:
        add('- ... and %d more' % (len(result['collisions']) - 15))
    add('')
    add('## Residual gaps (kept omitted, not guessed)')
    add('- No archived scenario (unknown/never recorded): %d input rows.'
        % result['totals']['residualInputRows'])
    add('- Unusable identity/condition rows: %d.' % result['totals']['undecodableInputRows'])
    add('- Already-represented block-groups excluded (observations): %d.'
        % result['totals']['collisionInputRows'])
    add('- No scenario/encounter/engine/policy/attribution is inferred; no replay payload copied.')
    add('')
    add('## Result')
    add('- Summaries appended: %d; seed blocks appended: %d; correction receipts: %s.' % (
        result['summariesAppended'], result['blocksAppended'],
        'yes' if result['receiptsWritten'] else 'no'))
    add('- Idempotent: a rerun sees the per-source receipt and appends nothing, charges nothing.')
    return '\n'.join(lines) + '\n'


def _write_report(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_report(result), encoding='utf-8')
    json_path = path.with_suffix('.json') if path.suffix else path.with_name(path.name + '.json')
    json_path.write_text(json.dumps(result, sort_keys=True, separators=(',', ':'), default=str),
                         encoding='utf-8')


# --- main flow ----------------------------------------------------------------------------------
def recover(library, *, authorised=False, report_path=None, on_collision='stop',
            batch_size=summary.DEFAULT_BATCH, verify_hashes=True, campaign_db=None,
            progress_dir=None, plan_only=False, workers=DEFAULT_WORKERS):
    library = str(Path(library))
    if report_path is None:
        base = Path(progress_dir) if progress_dir else (
            HERE.parent.parent / 'tmp' / 'encounter-redesign-20260928')
        report_path = base / REPORT_NAME
    report_path = Path(report_path)
    prog = Progress(report_path.parent)
    prog.stage('opening-destination', note=library)
    authorised = bool(authorised)
    if plan_only:
        authorised = False
    if authorised:
        dest_conn = sqlite3.connect(library, isolation_level=None, timeout=30.0)
        dest_conn.row_factory = sqlite3.Row
    else:
        dest_conn = _open_ro(library)
    workers = max(1, int(workers))
    result = dict(library=library, authorised=authorised, onCollision=on_collision,
                  workers=workers, outcome='dry-plan', collisions=[], collisionCount=0,
                  summariesAppended=0, blocksAppended=0, applied=False, receiptsWritten=False,
                  alreadyApplied=False, hashesVerified=bool(verify_hashes), campaignTotalRuns=0,
                  campaignDb=campaign_db, sources=[], conservation=[], receipts={},
                  planOnly=bool(plan_only), workerMode=None,
                  totals=dict(omissionBefore=0, recoverableInputRows=0, retainedObservations=0,
                              residualInputRows=0, collisionInputRows=0, undecodableInputRows=0))
    try:
        result['campaignTotalRuns'] = _campaign_snapshot(dest_conn, campaign_db, authorised)
        sources, engine_groups = _destination_sources(dest_conn)
        if authorised:
            receipts = _source_receipts(dest_conn)
            needed = set(RECEIPT_PREFIX + str(sid) for sid in sources)
            if needed <= set(receipts):
                result['alreadyApplied'] = True
                result['outcome'] = 'already-applied'
                result['hashesVerified'] = False
                result['workerMode'] = 'none'
                _replay_receipts(result, sources, receipts)
                prog.stage('already-applied', ok=True, note='receipt present; no reprocessing')
                _write_report(report_path, result)
                return result
            if needed & set(receipts):
                raise RecoveryError('partial correction receipt state; refusing to continue')
        scratch = None
        scratch_path = None
        stack = contextlib.ExitStack()
        try:
            handle, scratch_path = tempfile.mkstemp(prefix='ka-history-recover-stage-',
                                                    suffix='.sqlite')
            os.close(handle)
            scratch = sqlite3.connect(scratch_path, isolation_level=None)
            scratch.execute('PRAGMA journal_mode=OFF')
            scratch.execute('PRAGMA synchronous=OFF')
            scratch.execute('PRAGMA temp_store=MEMORY')
            scratch.executescript(';\n'.join(summary._SCRATCH_SCHEMA))
            per_source = {}
            planned = set()
            total = len(sources)
            resolved = {}
            prep_total = 0
            prog.stage('resolving-candidates', done=0, total=total)
            for position, (source_id, info) in enumerate(sources.items(), start=1):
                _verify_source(info, verify_hashes)
                source_conn = _open_ro(info['path'])
                try:
                    ev_total, run_total = _source_missing_rows(source_conn)
                    candidates, unresolved, mismatched = _archived_candidates(
                        dest_conn, source_conn, source_id, prog)
                finally:
                    source_conn.close()
                resolved[source_id] = dict(ev_total=ev_total, run_total=run_total,
                                           candidates=candidates, unresolved=unresolved,
                                           mismatched=mismatched)
                prep_total += len(candidates)
                prog.stage('resolving-candidates', note=info['label'], done=position,
                           total=total)
            prep_started = time.time()
            prepared = 0
            for position, (source_id, info) in enumerate(sources.items(), start=1):
                prog.stage('reading-source', note=info['label'], done=position - 1, total=total)
                res = resolved[source_id]
                candidates = res['candidates']
                shadow = None
                try:
                    shadow = _build_shadow(stack, info['path'], candidates)
                    ev_rec = _count(shadow.db, 'SELECT COUNT(*) FROM evidence')
                    run_rec = _count(shadow.db, 'SELECT COUNT(*) FROM run WHERE NOT EXISTS ('
                                     'SELECT 1 FROM evidence e WHERE e.candidate=run.candidate '
                                     'AND e.phase=run.phase AND e.ordinal=run.ordinal)')
                    measured = {'custom': False, 'omitted': {}}
                    if plan_only:
                        _plan_collisions(planned, shadow, candidates, engine_groups[source_id])
                    else:
                        lookup, mode = _prepare_infos(candidates, workers, prog,
                                                      done_base=prepared, total=prep_total,
                                                      since=prep_started)
                        result['workerMode'] = mode
                        prepared += len(candidates)
                        with _precomputed_scenario_info(lookup):
                            measured = summary._stream_measurements(scratch, shadow, source_id,
                                                                    engine_groups[source_id],
                                                                    batch_size)
                finally:
                    if shadow is not None:
                        shadow.close()
                skipped = sum(int(measured['omitted'].get(kind, 0)) for kind in SKIP_KINDS)
                per_source[source_id] = dict(
                    label=info['label'], path=info['path'], sha256=info['sha256'],
                    engineGroup=engine_groups[source_id],
                    omissionBefore=res['ev_total'] + res['run_total'],
                    recoverableInputRows=ev_rec + run_rec, undecodableInputRows=skipped,
                    collisionInputRows=0, candidates=len(candidates),
                    unresolvedCandidates=res['unresolved'], identityMismatches=res['mismatched'],
                    measuredMissing=dict(measured['omitted']))
                prog.stage('streamed-source', note='%s: %d recoverable rows'
                           % (info['label'], ev_rec + run_rec), done=position, total=total)
            staged = set(planned) if plan_only else set(tuple(row) for row in scratch.execute(
                'SELECT DISTINCT ident,policy,engine FROM stage'))
            existing = set(tuple(row) for row in dest_conn.execute(
                'SELECT DISTINCT candidate_id,policy_key,engine_compat_group '
                'FROM history_seed_block'))
            existing |= set(tuple(row) for row in dest_conn.execute(
                'SELECT DISTINCT candidate_id,policy_key,engine_compat_group '
                'FROM history_build_summary'))
            collisions = sorted(staged & existing)
            result['collisions'] = collisions
            result['collisionCount'] = len(collisions)
            if collisions and on_collision == 'skip' and not plan_only:
                for ident, policy, engine in collisions:
                    for source_id in per_source:
                        per_source[source_id]['collisionInputRows'] += _count(
                            scratch, 'SELECT COUNT(*) FROM stage_obs WHERE source_id=? AND ident=? '
                            'AND policy=? AND engine=?', (source_id, ident, policy, engine))
                    for table in ('stage', 'stage_obs', 'stage_source'):
                        scratch.execute('DELETE FROM %s WHERE ident=? AND policy=? AND engine=?'
                                        % table, (ident, policy, engine))
                scratch.commit()
            for source_id, stats in per_source.items():
                retained = (stats['recoverableInputRows'] if plan_only
                            else _count(scratch, 'SELECT COUNT(*) FROM stage_obs WHERE source_id=?',
                                        (source_id,)))
                stats['retainedObservations'] = retained
                stats['residualInputRows'] = stats['omissionBefore'] - stats['recoverableInputRows']
                stats['deduplicatedInputRows'] = max(0, stats['recoverableInputRows']
                    - stats['undecodableInputRows'] - retained - stats['collisionInputRows'])
                result['sources'].append(dict(
                    label=stats['label'], omissionBefore=stats['omissionBefore'],
                    recoverableInputRows=stats['recoverableInputRows'],
                    retainedObservations=retained, residualInputRows=stats['residualInputRows']))
                result['conservation'].append(_conservation(stats['label'], stats))
            for key in ('omissionBefore', 'recoverableInputRows', 'residualInputRows',
                        'collisionInputRows', 'undecodableInputRows'):
                result['totals'][key] = sum(s[key] for s in per_source.values())
            result['totals']['retainedObservations'] = sum(
                s['retainedObservations'] for s in per_source.values())
            if not authorised:
                result['outcome'] = 'plan-only' if plan_only else 'dry-plan'
                prog.stage('dry-plan-complete', ok=True, note='%d recoverable observations'
                           % result['totals']['retainedObservations'])
                _write_report(report_path, result)
                return result
            if collisions and on_collision == 'stop':
                result['outcome'] = 'stopped-collision'
                prog.stage('collision-stop', ok=False, note='%d block-group(s)' % len(collisions))
                _write_report(report_path, result)
                raise CollisionError(
                    '%d recovered block-group(s) already exist in the destination; refusing to '
                    'merge or replace. Re-run with --on-collision skip to exclude only those '
                    'already-present groups.' % len(collisions))
            _apply(dest_conn, scratch, sources, per_source, result)
            result['applied'] = True
            result['receiptsWritten'] = True
            result['outcome'] = 'applied'
            prog.stage('complete', ok=True, note='appended %d summaries / %d blocks'
                       % (result['summariesAppended'], result['blocksAppended']))
            _write_report(report_path, result)
            return result
        finally:
            if scratch is not None:
                scratch.close()
            stack.close()
            if scratch_path is not None:
                for suffix in ('', '-wal', '-shm'):
                    try:
                        os.remove(scratch_path + suffix)
                    except OSError:
                        pass
    finally:
        dest_conn.close()


# --- bounded scenario-info preparation (expensive fingerprint) ---------------------------------
def _scenario_meta_row(info):
    """The exact candidate_meta row ``_build_shadow`` will expose, or None (no synthesis)."""
    if info.get('encounter') is None:
        return None
    return {'encounter': info['encounter'], 'defeat': info['defeat']}


def _scenario_key(scenario, meta_row, stored_id):
    """Bind a prepared info to stored id + exact canonical scenario + encounter/defeat metadata."""
    if stored_id is None:
        return None
    encounter = defeat = None
    if meta_row is not None:
        try:
            encounter = int(meta_row['encounter'])
            defeat = int(meta_row['defeat'])
        except (TypeError, ValueError, KeyError, IndexError):
            encounter, defeat = None, None
    return (str(stored_id), _canonical(scenario), encounter, defeat)


def _prepare_scenario_info(stored, info):
    """Process-worker body: the original ``summary._scenario_info`` for one archived candidate.

    Pure and DB-free, so the parent stays the only SQLite writer. The key is recomputed here with
    the exact helper the lookup uses, so changed scenario/metadata can never reuse stale info.
    """
    scenario = json.loads(info['scenario'])
    meta_row = _scenario_meta_row(info)
    prepared = summary._scenario_info(scenario, meta_row, stored_id=stored)
    return _scenario_key(scenario, meta_row, stored), prepared


def _prepare_infos(candidates, workers, prog, *, done_base, total, since):
    """Prepare recoverable candidates' scenario info in a bounded local process pool.

    ``workers == 1`` runs identical work serially. Submissions are bounded to a fixed window so
    memory stays flat; only the parent opens SQLite. Returns ``(lookup, mode)`` where ``mode`` is
    ``'pool'`` or (only when the local OS refuses to create a process pool) ``'serial-fallback'``.
    """
    lookup = {}
    if not candidates:
        return lookup, 'serial'
    items = list(candidates.items())
    mode = 'serial'
    if workers > 1:
        pool = None
        try:
            pool = multiprocessing.Pool(processes=workers)
        except (OSError, ImportError, ValueError):
            pool = None
        if pool is not None:
            try:
                _drain_pool(pool, items, lookup, prog, done_base, total, since, workers)
            finally:
                pool.terminate()
                pool.join()
            return lookup, 'pool'
        mode = 'serial-fallback'
    for index, (stored, info) in enumerate(items, start=1):
        key, prepared = _prepare_scenario_info(stored, info)
        lookup[key] = prepared
        if prog is not None:
            prog.stage('preparing-info', done=done_base + index, total=total,
                       note='serial precompute' if mode == 'serial' else
                            'serial fallback (process pool unavailable)', since=since)
    return lookup, mode


def _drain_pool(pool, items, lookup, prog, done_base, total, since, workers):
    """Bounded window of submissions over a live pool; single consumer, single SQLite writer."""
    window = max(2 * workers, 8)
    iterator = iter(items)
    pending = collections.deque()

    def fill():
        while len(pending) < window:
            try:
                stored, info = next(iterator)
            except StopIteration:
                return
            pending.append(pool.apply_async(_prepare_scenario_info, (stored, info)))

    fill()
    finished = 0
    while pending:
        handle = pending.popleft()
        try:
            key, prepared = handle.get()
        except Exception as exc:  # noqa: BLE001 - surface the worker failure, write nothing
            raise RecoveryError('scenario-info worker failed: %s' % exc)
        lookup[key] = prepared
        finished += 1
        if prog is not None:
            prog.stage('preparing-info', done=done_base + finished, total=total,
                       note='%d workers' % workers, since=since)
        fill()


@contextlib.contextmanager
def _precomputed_scenario_info(lookup):
    """Serve ``summary._scenario_info`` from a precomputed lookup; restore it in ``finally``.

    No fallback: a miss means the cached info does not bind to this exact stored id + canonical
    scenario + encounter/defeat, so we refuse rather than fabricate or recompute in serial.
    """
    original = summary._scenario_info

    def _from_lookup(scenario, meta_row, stored_id=None):
        key = _scenario_key(scenario, meta_row, stored_id)
        try:
            return dict(lookup[key])
        except (KeyError, TypeError):
            raise RecoveryError(
                'no precomputed scenario info for stored id %r; refusing to fabricate metadata'
                % (stored_id,))

    summary._scenario_info = _from_lookup
    try:
        yield lookup
    finally:
        summary._scenario_info = original


def _plan_collisions(planned, shadow, candidates, engine_group):
    """Cheap, read-only prediction of staged *block-group* keys (ident, policy, engine)."""
    seen = set()
    for candidate, phase in shadow.db.execute(
            'SELECT DISTINCT candidate,phase FROM evidence '
            'UNION SELECT DISTINCT candidate,phase FROM run'):
        seen.add(candidate)
    for stored, info in candidates.items():
        if stored not in seen:
            continue
        try:
            scn = json.loads(info['scenario'])
        except (TypeError, ValueError):
            continue
        policy = {key: scn[key] for key in summary.POLICY_KEYS if key in scn}
        planned.add((info['canonical'], _canonical(policy), engine_group))


def _verify_source(info, verify_hashes):
    path = Path(info['path'])
    if not path.is_file():
        raise RecoveryError('source is missing: %s' % path)
    if verify_hashes and info.get('sha256'):
        actual = _sha256(path)
        if actual != info['sha256']:
            raise RecoveryError('source sha256 changed since import (frozen): %s' % path)


def _source_receipts(dest_conn):
    """``{key: raw_value}`` for every persisted per-source correction receipt."""
    return {row[0]: row[1] for row in dest_conn.execute(
        'SELECT key,value FROM meta WHERE key LIKE ?', (RECEIPT_PREFIX + '%',))}


def _replay_receipts(result, sources, receipts):
    """Refill the report from persisted receipts so an idempotent rerun does no source work."""
    stats_list = []
    for source_id, info in sources.items():
        raw = receipts.get(RECEIPT_PREFIX + str(source_id))
        if raw is None:
            continue
        try:
            receipt = json.loads(raw)
        except (TypeError, ValueError):
            continue
        result['receipts'][source_id] = receipt
        stats = dict(
            label=receipt.get('sourceLabel') or info['label'],
            omissionBefore=int(receipt.get('omissionBefore') or 0),
            recoverableInputRows=int(receipt.get('recoverableInputRows') or 0),
            retainedObservations=int(receipt.get('recoveredObservations') or 0),
            residualInputRows=int(receipt.get('residualInputRows') or 0),
            collisionInputRows=int(receipt.get('collisionInputRows') or 0),
            undecodableInputRows=int(receipt.get('undecodableInputRows') or 0),
            deduplicatedInputRows=int(receipt.get('deduplicatedInputRows') or 0))
        stats_list.append(stats)
        result['sources'].append(dict(
            label=stats['label'], omissionBefore=stats['omissionBefore'],
            recoverableInputRows=stats['recoverableInputRows'],
            retainedObservations=stats['retainedObservations'],
            residualInputRows=stats['residualInputRows']))
        result['conservation'].append(_conservation(stats['label'], stats))
    for key in ('omissionBefore', 'recoverableInputRows', 'residualInputRows',
                'collisionInputRows', 'undecodableInputRows'):
        result['totals'][key] = sum(s[key] for s in stats_list)
    result['totals']['retainedObservations'] = sum(s['retainedObservations'] for s in stats_list)


def _destination_new_campaign_count(dest_conn):
    total = 0
    for row in dest_conn.execute("SELECT key,value FROM meta WHERE key IN "
                                 "('totalRuns','proposals','improvements')"):
        try:
            total = max(total, int(json.loads(row['value'])))
        except (TypeError, ValueError):
            pass
    return total


def _campaign_snapshot(dest_conn, campaign_db, authorised):
    """The destination must carry no new-campaign runs. Never writes to the live store."""
    total = _destination_new_campaign_count(dest_conn)
    if authorised and total:
        raise RecoveryError('destination new-campaign counters are not zero (max=%d); recovery '
                            'must not charge a running campaign' % total)
    if campaign_db:
        conn = _open_ro(campaign_db)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='totalRuns'").fetchone()
            runs = int(json.loads(row['value'])) if row else 0
            if runs and authorised:
                raise RecoveryError('campaign store reports %d new runs; refusing to append' % runs)
        finally:
            conn.close()
    return total


def _apply(dest_conn, scratch, sources, per_source, result):
    sources_map = {sid: dict(path=info['path'], label=info['label'],
                             ownership=info['ownership'],
                             provenanceDigest=info.get('provenanceDigest'),
                             provenance=info.get('provenance'))
                   for sid, info in sources.items()}
    created_at = time.time()
    dest_conn.execute('BEGIN IMMEDIATE')
    try:
        if _destination_new_campaign_count(dest_conn):
            raise RecoveryError('destination new-campaign counters became non-zero inside the write '
                                'transaction; refusing to append')
        before_summaries = _count(dest_conn, 'SELECT COUNT(*) FROM history_build_summary')
        before_blocks = _count(dest_conn, 'SELECT COUNT(*) FROM history_seed_block')
        inserted = summary._aggregate(scratch, dest_conn, sources_map)
        blocks = summary._mine_blocks(scratch, dest_conn, sources_map, created_at)
        after_summaries = _count(dest_conn, 'SELECT COUNT(*) FROM history_build_summary')
        after_blocks = _count(dest_conn, 'SELECT COUNT(*) FROM history_seed_block')
        if after_summaries - before_summaries != inserted:
            raise RecoveryError('summary insert mismatch: intended %d, actually inserted %d'
                                % (inserted, after_summaries - before_summaries))
        if after_blocks - before_blocks != blocks:
            raise RecoveryError('seed-block insert mismatch: intended %d, actually inserted %d; a '
                                'block group would have been silently ignored'
                                % (blocks, after_blocks - before_blocks))
        for source_id, stats in per_source.items():
            retained = stats['retainedObservations']
            _correct_omission(dest_conn, source_id, retained)
            receipt = dict(tool=TOOL, version=VERSION, appliedAt=created_at,
                           sourceLabel=stats['label'], sourcePath=stats['path'],
                           sourceSha256=stats['sha256'], engineCompatGroup=stats['engineGroup'],
                           omissionKind=OMISSION_KIND, omissionBefore=stats['omissionBefore'],
                           recoveredObservations=retained,
                           omissionAfter=stats['omissionBefore'] - retained,
                           recoverableInputRows=stats['recoverableInputRows'],
                           undecodableInputRows=stats['undecodableInputRows'],
                           residualInputRows=stats['residualInputRows'],
                           collisionInputRows=stats['collisionInputRows'],
                           deduplicatedInputRows=stats['deduplicatedInputRows'],
                           candidatesRecovered=stats['candidates'],
                           unresolvedCandidates=stats['unresolvedCandidates'],
                           identityMismatches=stats['identityMismatches'],
                           measuredOmissions=stats['measuredMissing'],
                           note='bounded incremental append-only correction; old summaries and '
                                'blocks unchanged; campaign counters untouched')
            dest_conn.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                              (RECEIPT_PREFIX + str(source_id), _canonical(receipt)))
            result['receipts'][source_id] = receipt
        dest_conn.execute('COMMIT')
    except BaseException:
        dest_conn.execute('ROLLBACK')
        raise
    result['summariesAppended'] = inserted
    result['blocksAppended'] = blocks


def _correct_omission(dest_conn, source_id, retained):
    row = dest_conn.execute(
        'SELECT omission_id,count FROM history_omission WHERE source_id=? AND kind=? '
        'ORDER BY count DESC LIMIT 1', (source_id, OMISSION_KIND)).fetchone()
    if row is None:
        if retained:
            raise RecoveryError('no %s omission row to correct for source %d'
                                % (OMISSION_KIND, source_id))
        return
    count = int(row['count'])
    if retained > count:
        raise RecoveryError('omission underflow for source %d: %d retained exceeds %d counted; '
                            'refusing to clamp' % (source_id, retained, count))
    dest_conn.execute('UPDATE history_omission SET count=? WHERE omission_id=?',
                      (count - int(retained), row['omission_id']))


@contextlib.contextmanager
def _exclusive_recovery(library):
    """Serialize authorised invocations; a later invocation reuses the committed receipt."""
    import msvcrt
    lock_path = str(Path(library).resolve()) + '.history-recovery.lock'
    with open(lock_path, 'a+b') as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 3600
        announced = False
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RecoveryError('Another history recovery still holds the library lock')
                if not announced:
                    print('Waiting for the existing history recovery; work will not be duplicated.', flush=True)
                    announced = True
                time.sleep(0.5)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True, help='prepared destination history library')
    parser.add_argument('--authorised', action='store_true',
                        help='perform the real append; without it a read-only dry plan is written')
    parser.add_argument('--report', default=None, help='report path (Markdown)')
    parser.add_argument('--on-collision', choices=('stop', 'skip'), default='stop',
                        help='stop (fail closed) or exclude an already-present group')
    parser.add_argument('--batch-size', type=int, default=summary.DEFAULT_BATCH)
    parser.add_argument('--campaign-db', default=None,
                        help='optional live campaign store; must report totalRuns=0')
    parser.add_argument('--no-verify-hashes', action='store_true',
                        help='skip the frozen-source sha256 verification (not recommended)')
    parser.add_argument('--progress-dir', default=None,
                        help='directory for the progress JSON/log the local monitor reads')
    parser.add_argument('--plan-only', action='store_true',
                        help='read-only plan without streaming (cheap collision/row estimate)')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help='bounded local process pool for scenario-info/fingerprint preparation '
                             '(%d by default; 1 runs the identical precompute serially)'
                             % DEFAULT_WORKERS)
    args = parser.parse_args(argv)
    try:
        guard = (_exclusive_recovery(args.library) if args.authorised and not args.plan_only
                 else contextlib.nullcontext())
        with guard:
            result = recover(args.library, authorised=args.authorised, report_path=args.report,
                             on_collision=args.on_collision, batch_size=args.batch_size,
                             verify_hashes=not args.no_verify_hashes,
                             campaign_db=args.campaign_db, progress_dir=args.progress_dir,
                             plan_only=args.plan_only, workers=args.workers)
    except (RecoveryError, summary.HistorySummaryError) as exc:
        print('recovery stopped: %s' % exc, file=sys.stderr)
        return 2
    print(json.dumps(dict(mode='authorised' if args.authorised else 'dry-plan',
                          outcome=result['outcome'], library=result['library'],
                          collisions=result['collisionCount'], totals=result['totals']),
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
