"""Cached, incremental read model over the Community encounter (ea_*) ledger.

The Community-first runtime records every battle in the ``ea_*`` tables instead of the legacy
``run``/``totalRuns`` counters (historical separation). The overview therefore has to read the two
persisted result tables directly to answer three questions the legacy view cannot:

  * how many attempts each ``<encounterId>:<defeatCount>`` row really has, split by outcome and by
    evidence backend (native vs fallback), so an errored or excluded battle is labelled rather than
    silently dropped or invented as a zero;
  * which build actually set a fight's lifetime maxima, by candidate id, so a "max holder" points at
    a real, replayable candidate instead of a display name;
  * which build led each fight's MEASURED MEAN (earned and potential), grouped by the exact
    table/experiment/candidate/policy/window/revision identity, so a confirmation or an older window
    is never averaged into fresh development evidence as if it were the same sample.

Everything here is READ-ONLY and INCREMENTAL. Only the small ``ea_*`` tables are touched - never the
legacy 20M-row ``run`` table - and the scan is keyed by the implicit per-table ``rowid``. A cheap
change token (``PRAGMA data_version`` plus the connection's own ``total_changes``) short-circuits an
unchanged poll without counting rows; when it moves, only the rows above the per-table high-water
mark are read and decoded. A row first seen while still pending (``result IS NULL``) is remembered by
rowid alone and re-checked in bounded batches on later polls, so a completion that lands on an older
row can never be missed. A library whose rowids regress (a swapped/truncated file) is detected and
rescanned once.

No per-sample record is retained: counts and maxima are accumulated incrementally per encounter, and
the mean-leader evidence is kept as per-exact-group sums/counts. The published payload is rebuilt
from those build groups, never from the individual runs.
"""
from __future__ import annotations

import json
import hashlib
import io
import os
import pickle
import statistics
import time

import strategy_experiment_store as ledger
import strategy_payload_codec as payload_codec

#: Minimum qualifying observations before a build may lead a fight's measured mean. The desktop host
#: owns this floor for its own ledger; the value is duplicated here on purpose, because importing the
#: desktop bridge into this small backend read model would be a circular import.
AVERAGE_MIN_SAMPLES = 10

#: Encounter verdicts as recovered and persisted by ``strategy_outcomes``.
WIN_VERDICT, LOSS_VERDICT = 1, 2

_TABLES = (('ea_sample', 'development'), ('ea_holdout', 'confirmation'))

#: Rowid pagination page size for the one-time catch-up scan, and the pending re-check batch size.
_PAGE = 512
_RECHECK = 256

#: Sentinel row key for samples whose candidate row cannot be resolved to an encounter yet.
_UNMAPPED = None

_LEDGER_CACHE_SEED_SCHEMA = 'ka-encounter-ledger-cache-seed-1'
_LEDGER_CACHE_SEED_FIELDS = (
    '_view', '_candidate_cache', '_missing_candidates', '_labels', '_experiments', '_intents',
    '_scope', '_scope_resolved', 'stats', '_counts', '_rows', '_groups', '_residents',
    '_mapped_encounters', '_unmapped_entries', '_first_at', '_last_at', '_tables')


def _seed_path(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _seed_file_identity(path):
    stat = os.stat(path)
    return (int(stat.st_dev), int(stat.st_ino))


def _database_file_identity(db):
    row = db.execute('PRAGMA database_list').fetchone()
    if row is None or not row[2]:
        raise ValueError('database_file_unknown')
    return _seed_file_identity(row[2])


def _seed_provenance_digest(provenance):
    raw = json.dumps(provenance or {}, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

_SELECT_SAMPLE = ('SELECT rowid, sample_key, candidate_id, policy, mechanics_revision, '
                  'encounter_revision, measurement_window, result, outcome, created_at '
                  'FROM ea_sample')
_SELECT_HOLDOUT = ('SELECT rowid, experiment_id, candidate_id, seed_a, seed_b, result, outcome, '
                   'completed_at FROM ea_holdout')
_SELECT = {'ea_sample': _SELECT_SAMPLE, 'ea_holdout': _SELECT_HOLDOUT}

#: The per-encounter counters kept incrementally. ``excluded`` is a UNION flag count, never a sum of
#: the other counters.
_COUNTER_KEYS = ('attempts', 'wins', 'losses', 'noVerdict', 'censored', 'errors', 'unknownOutcome',
                 'native', 'fallback', 'unknownBackend', 'excluded')


def _has_table(db, name):
    try:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (name,)).fetchone() is not None
    except Exception:  # noqa: BLE001 - an unreadable library simply has no ledger
        return False


def signature(db):
    """A cheap change token for the ledger, safe to run on every status poll.

    ``PRAGMA data_version`` moves when ANOTHER connection commits; the connection's own
    ``total_changes`` moves when THIS connection writes. Together they cover both writers without a
    single ``COUNT(*)`` and without decoding any result/outcome JSON, so an unchanged poll costs one
    pragma read.
    """
    try:
        data_version = int(db.execute('PRAGMA data_version').fetchone()[0])
    except Exception:  # noqa: BLE001 - a missing pragma never blocks a read
        data_version = -1
    try:
        total_changes = int(db.total_changes)
    except Exception:  # noqa: BLE001
        total_changes = -1
    return (data_version, total_changes)


def blank_counts():
    return dict(total=0, development=0, confirmation=0, native=0, fallback=0, unknownBackend=0,
                wins=0, losses=0, noVerdict=0, censored=0, errors=0, unknownOutcome=0, unmapped=0,
                excluded=0, firstAt=None, lastAt=None)


def _json(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _chunks(values, size=400):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


class FrozenIntentIndex:
    """Candidate -> the exact scenario a pruned build was dispatched under, from frozen intents only.

    A candidate can be pruned out of ``candidate`` after its samples were recorded, and then its
    encounter can no longer be read from a resident scenario. The experiment that authorised the run
    did freeze the observed view, though: ``ea_experiment.intent_json.observedScenarios`` maps each
    candidate id to the exact scenario that was simulated, and ``ea_sample_link``/``ea_holdout`` say
    which experiments named a candidate. This index reads those two tables at most once and decodes
    each intent at most once into compact identities, so a pruned sample can be attributed without
    rescanning the ledger per query and without retaining the full intent in a long-lived publisher.
    The explicit `scenario()` UI API reloads only the selected full scenario to preserve its contract.

    Only an unambiguous recovery is accepted: when every frozen intent that names the candidate
    agrees on one encounter/difficulty, that is returned; a candidate whose intents disagree, or that
    has no frozen scenario at all, stays unresolved (unmapped) rather than being guessed.
    """

    def __init__(self):
        self._links = {}
        self._high = {}
        # Publication polls can touch tens of thousands of experiments. Retain only the two
        # integers needed to prove a candidate's encounter, never each complete (40KB+) intent.
        self._identities = {}

    def _link_index(self, db):
        links = self._links
        for table, kind in (('ea_sample_link', 'development'), ('ea_holdout', 'confirmation')):
            if not _has_table(db, table):
                continue
            try:
                rows = db.execute('SELECT rowid, candidate_id, experiment_id FROM ' + table
                                  + ' WHERE rowid > ? ORDER BY rowid',
                                  (self._high.get(table, 0),)).fetchall()
            except Exception:  # noqa: BLE001 - an unreadable link table is simply no evidence
                rows = ()
            for row in rows:
                self._high[table] = int(row[0])
                links.setdefault(str(row[1]), set()).add((kind, int(row[2])))
        return links

    def _experiment_identities(self, db, experiment_id):
        if experiment_id in self._identities:
            return self._identities[experiment_id]
        identities = {}
        try:
            raw = ledger.experiment_intent_raw(db, experiment_id)
            decoded = _json(raw)
            observed_scenarios = (decoded.get('observedScenarios')
                                  if isinstance(decoded, dict) else None)
            if isinstance(observed_scenarios, dict):
                for cid, observed in observed_scenarios.items():
                    if not isinstance(observed, dict) or 'encounterId' not in observed:
                        continue
                    try:
                        identities[str(cid)] = (int(observed['encounterId']),
                                                 int(observed.get('defeatCount') or 0))
                    except (TypeError, ValueError):
                        continue
        except Exception:  # noqa: BLE001 - a missing intent is no recovery
            pass
        self._identities[experiment_id] = identities
        return identities

    @staticmethod
    def _observed_scenario(db, experiment_id, candidate_id):
        """Load one full scenario only for explicit public/UI retrieval."""
        try:
            raw = ledger.experiment_intent_raw(db, experiment_id)
            decoded = _json(raw)
            observed = ((decoded.get('observedScenarios') or {}).get(str(candidate_id))
                        if isinstance(decoded, dict) else None)
            return observed if isinstance(observed, dict) else None
        except Exception:  # noqa: BLE001 - a missing intent is no recovery
            return None

    def identity(self, db, candidate_id):
        """Compact proof `{basis, experimentId, encounterId, defeatCount}` or None."""
        cid = str(candidate_id)
        entries = sorted(self._link_index(db).get(cid) or ())
        chosen = None
        encounters = set()
        for kind, experiment_id in entries:
            key = self._experiment_identities(db, experiment_id).get(cid)
            if key is None:
                continue
            encounters.add(key)
            if chosen is None:
                chosen = (kind, experiment_id, key)
        if chosen is None or len(encounters) != 1:
            return None
        kind, experiment_id, key = chosen
        return dict(basis='ea-%s-intent' % kind, experimentId=experiment_id,
                    encounterId=key[0], defeatCount=key[1])

    def scenario(self, db, candidate_id):
        """`{scenario, basis, experimentId, encounterId, defeatCount}` or None when unprovable."""
        cid = str(candidate_id)
        proof = self.identity(db, cid)
        if proof is None:
            return None
        observed = self._observed_scenario(db, proof['experimentId'], cid)
        if observed is None:
            return None
        return dict(scenario=observed, basis=proof['basis'],
                    experimentId=proof['experimentId'], encounterId=proof['encounterId'],
                    defeatCount=proof['defeatCount'])


def frozen_scenario_for(db, candidate_id, index=None):
    """The exact frozen observed scenario naming `candidate_id`, or None. Never a current guess."""
    index = index if index is not None else FrozenIntentIndex()
    return index.scenario(db, candidate_id)


def _empty_row(encounter_id=None, defeat_count=None):
    row = dict(encounterId=encounter_id, defeatCount=defeat_count)
    for name in _COUNTER_KEYS:
        row[name] = 0
    row.update(highestPotentialChests=None, potentialCandidateId=None, potentialSeeds=None,
               potentialSampleKey=None, potentialBasis=None, highestChestsEarned=None,
               earnedCandidateId=None, earnedSeeds=None, earnedSampleKey=None, earnedBasis=None)
    return row


def _candidate_tuple(scenario, label, source):
    """`(encounterId, defeatCount, label, source)` from a resident scenario, or None."""
    if isinstance(scenario, dict) and 'encounterId' in scenario:
        try:
            return (int(scenario['encounterId']), int(scenario.get('defeatCount') or 0),
                    label, source)
        except (TypeError, ValueError):
            return None
    return None


def _empty_group():
    return dict(attempts=0, wins=0, losses=0, native=0, fallback=0, unknownBackend=0, errors=0,
                censored=0, unknownOutcome=0, noVerdict=0, earnedSum=0, earnedCount=0,
                earnedMax=None, potentialSum=0, potentialCount=0)


def _merge_row(target, source):
    """Fold one accumulated encounter bucket into another (unattributable samples -> declared scope)."""
    if source is None:
        return target
    for name in _COUNTER_KEYS:
        target[name] += source[name]
    for field, prefix in (('highestPotentialChests', 'potential'),
                          ('highestChestsEarned', 'earned')):
        value = source[field]
        if value is not None and (target[field] is None or value > target[field]):
            target[field] = value
            for suffix in ('CandidateId', 'Seeds', 'SampleKey', 'Basis'):
                target[prefix + suffix] = source[prefix + suffix]
    return target


def _group_comparable(group):
    """True only for a group whose observations are complete and purely native.

    A fallback/python run, an unknown backend, a censored or errored reading, a sample without a
    canonical outcome, or one whose verdict never landed can never make a group comparable - and
    never contributes a mean observation either, so it cannot become a "native mean leader".
    """
    clean = (group['fallback'] == 0 and group['unknownBackend'] == 0 and group['errors'] == 0
             and group['censored'] == 0 and group['unknownOutcome'] == 0 and group['noVerdict'] == 0)
    return bool(clean and group['earnedCount'] == group['attempts'])


def _key_parts(key):
    encounter, _sep, defeats = str(key).partition(':')
    return int(encounter), int(defeats)


def _encounter_sort_key(key):
    return _key_parts(key)


class LedgerCache:
    """One renderer's persistent incremental view of the encounter ledger.

    The incremental state is keyed to the exact connection it was built from: a different connection
    - or a library whose rowids regressed, e.g. a swapped file - resets the cache and rescans once.
    Each table keeps a rowid high-water mark plus the rowids of samples first seen while pending; a
    sample is decoded exactly once and only its incremental counters survive.
    """

    def __init__(self):
        self._conn = None
        self._token = None
        self._view = None
        self._candidate_cache = {}
        self._missing_candidates = set()
        self._labels = {}
        self._experiments = {}
        self._intents = FrozenIntentIndex()
        self._scope = None
        self._scope_resolved = False
        #: Observable counters for the incremental contract (and for deterministic tests).
        self.stats = dict(absorbs=0, rowsRead=0, completionRechecks=0, resets=0)
        self._reset_accumulators()

    def export_seed_packet(self, path, provenance, max_bytes):
        """Serialize detached aggregate state for one private projection-process handoff.

        The source connection/token are deliberately excluded. The receiving read transaction
        validates identity and rowid high-waters, binds its own connection, and forces one cheap
        incremental refresh before publishing.
        """
        diag = dict(state='fallback_full_fold', reason='cache_not_ready', packetBytes=0)
        if self._view is None or self._view.get('error'):
            return None, diag
        class BoundedBuffer(io.BytesIO):
            def write(self, value):
                if self.tell() + len(value) > max(0, int(max_bytes)):
                    raise OverflowError('packet_limit')
                return super().write(value)

        started = time.perf_counter()
        try:
            if self._conn is None or _database_file_identity(self._conn) != _seed_file_identity(path):
                raise ValueError('source_file_mismatch')
            state = {name: getattr(self, name) for name in _LEDGER_CACHE_SEED_FIELDS}
            envelope = dict(schema=_LEDGER_CACHE_SEED_SCHEMA, path=_seed_path(path),
                            fileIdentity=_seed_file_identity(path),
                            schemaVersion=int(self._conn.execute('PRAGMA schema_version').fetchone()[0]),
                            provenance=_seed_provenance_digest(provenance), state=state)
            buffer = BoundedBuffer()
            pickle.Pickler(buffer, protocol=5).dump(envelope)
            packet = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - seed failure falls back to a correct full fold
            diag['reason'] = ('packet_limit' if isinstance(exc, OverflowError)
                              else 'serialize_%s' % type(exc).__name__)
            diag['exportSeconds'] = time.perf_counter() - started
            return None, diag
        diag['packetBytes'] = len(packet)
        diag['exportSeconds'] = time.perf_counter() - started
        if len(packet) > max(0, int(max_bytes)):
            diag['reason'] = 'packet_limit'
            return None, diag
        diag.update(state='offered', reason=None)
        return packet, diag

    @classmethod
    def from_seed_packet(cls, packet, db, path, provenance, max_bytes):
        """Validate/import a private seed against the projection's pinned SQLite snapshot."""
        started = time.perf_counter()
        diag = dict(state='fallback_full_fold', reason='missing_packet', packetBytes=0)
        if not isinstance(packet, bytes):
            diag['importSeconds'] = time.perf_counter() - started
            return None, diag
        diag['packetBytes'] = len(packet)
        if len(packet) > max(0, int(max_bytes)):
            diag['reason'] = 'packet_limit'
            diag['importSeconds'] = time.perf_counter() - started
            return None, diag
        try:
            envelope = pickle.loads(packet)  # private local pipe only; never caller-controlled
            if not isinstance(envelope, dict) or envelope.get('schema') != _LEDGER_CACHE_SEED_SCHEMA:
                raise ValueError('schema')
            if envelope.get('path') != _seed_path(path):
                raise ValueError('path_mismatch')
            if tuple(envelope.get('fileIdentity') or ()) != _database_file_identity(db):
                raise ValueError('file_identity_mismatch')
            if int(envelope.get('schemaVersion', -1)) != int(
                    db.execute('PRAGMA schema_version').fetchone()[0]):
                raise ValueError('schema_version_mismatch')
            if envelope.get('provenance') != _seed_provenance_digest(provenance):
                raise ValueError('provenance_mismatch')
            state = envelope.get('state')
            if not isinstance(state, dict) or set(state) != set(_LEDGER_CACHE_SEED_FIELDS):
                raise ValueError('state_shape')
            if not isinstance(state['_view'], dict) or state['_view'].get('error'):
                raise ValueError('view_invalid')
            tables = state['_tables']
            if not isinstance(tables, dict) or set(tables) != {name for name, _kind in _TABLES}:
                raise ValueError('table_shape')
            for table, table_state in tables.items():
                if not isinstance(table_state, dict) or set(table_state) != {'exists', 'high', 'pending'}:
                    raise ValueError('table_state')
                exists = _has_table(db, table)
                current = (int(db.execute('SELECT COALESCE(MAX(rowid),0) FROM ' + table).fetchone()[0])
                           if exists else 0)
                high = int(table_state['high'])
                if bool(table_state['exists']) != exists or high < 0 or current < high:
                    raise ValueError('highwater_regressed')
                if not isinstance(table_state['pending'], set) or any(
                        int(rowid) <= 0 or int(rowid) > high for rowid in table_state['pending']):
                    raise ValueError('pending_rows_invalid')
            cache = cls()
            for name, value in state.items():
                setattr(cache, name, value)
            cache._conn = db
            cache._token = None  # force signature and incremental append/pending refresh
            diag.update(state='seeded', reason=None)
            diag['importSeconds'] = time.perf_counter() - started
            return cache, diag
        except Exception as exc:  # noqa: BLE001 - all invalid seeds safely fall back to full fold
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            diag['reason'] = reason or 'invalid_seed'
            diag['importSeconds'] = time.perf_counter() - started
            return None, diag

    # -- state ------------------------------------------------------------------------------------

    def _reset_accumulators(self):
        self._counts = blank_counts()
        self._rows = {}
        self._groups = {}
        self._residents = {}
        self._mapped_encounters = set()
        self._unmapped_entries = 0
        self._first_at = None
        self._last_at = None
        self._tables = {name: dict(exists=False, high=0, pending=set()) for name, _kind in _TABLES}

    def _reset(self, db):
        self._conn = db
        self._token = None
        self._view = None
        self._candidate_cache = {}
        self._missing_candidates = set()
        self._labels = {}
        self._experiments = {}
        self._intents = FrozenIntentIndex()
        self._scope = None
        self._scope_resolved = False
        self._reset_accumulators()

    # -- immutable lookups ------------------------------------------------------------------------

    def _candidate(self, db, cid):
        """The encounter a candidate row names, or None.

        A positive lookup is cached (the candidate id is an immutable content hash); a MISSING row is
        resolved from the experiment's own frozen observed scenario when one names it, so a pruned
        build's already-recorded samples are attributed to the exact fight they were run under - never
        to the currently selected fight. A candidate with no resident row and no consistent frozen
        scenario stays unmapped rather than being guessed.
        """
        entry = self._candidate_cache.get(cid)
        if entry is not None:
            return entry
        entry = None
        row = None
        try:
            row = db.execute('SELECT scenario, label, source FROM candidate WHERE id=?',
                             (cid,)).fetchone()
        except Exception:  # noqa: BLE001 - a missing candidate is reported as unmapped
            row = None
        if row is not None:
            entry = _candidate_tuple(_json(row[0]), row[1], row[2])
        if entry is None:
            try:
                found = self._intents.identity(db, cid)
            except Exception:  # noqa: BLE001 - an unreadable intent is simply no recovery
                found = None
            if found is not None:
                entry = (found['encounterId'], found['defeatCount'], None, found['basis'])
        if entry is not None:
            self._candidate_cache[cid] = entry
            self._labels[cid] = (entry[2], entry[3])
            self._missing_candidates.discard(cid)
        else:
            self._missing_candidates.add(cid)
        return entry

    def _experiment(self, db, experiment_id):
        """The stored engine/policy/window conditions of one experiment, cached when it exists."""
        conditions = self._experiments.get(experiment_id)
        if conditions is not None:
            return conditions
        conditions = (None, None, None, None)
        try:
            row = db.execute('SELECT policy, mechanics_revision, encounter_revision, '
                             'measurement_window FROM ea_experiment WHERE id=?',
                             (experiment_id,)).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            conditions = (row[0], row[1], row[2], row[3])
            self._experiments[experiment_id] = conditions
        return conditions

    # -- decoding ---------------------------------------------------------------------------------

    def _decode(self, db, *, table, kind, ident, candidate_id, policy, window, mechanics,
                encounter_revision, result_raw, outcome_raw, stamp, experiment):
        entry = dict(table=table, kind=kind, ident=ident, candidate=candidate_id,
                     experiment=experiment, policy=policy, window=window, mechanics=mechanics,
                     encounterRevision=encounter_revision, engine=None, encounterId=None,
                     defeatCount=None, label=None, source=None, backend='unknown', verdict=None,
                     censored=False, error=False, unknownOutcome=False, resolved=False,
                     finalEarned=None, pending=None, seeds=None, stamp=None, unmapped=False)
        resolved_candidate = self._candidate(db, candidate_id)
        if resolved_candidate is None:
            entry['unmapped'] = True
        else:
            (entry['encounterId'], entry['defeatCount'], entry['label'],
             entry['source']) = resolved_candidate
        # Retain the existing malformed/legacy BLOB fallback behavior. Only the reserved v4
        # BLOB representation needs to be decoded before _json handles the stored value.
        result = payload_codec.decode_json_codec_if_magic(result_raw)
        if isinstance(result, dict):
            backend = result.get('resultBackend')
            if isinstance(backend, str) and backend:
                entry['backend'] = backend
            seeds = result.get('seeds')
            if isinstance(seeds, (list, tuple)) and len(seeds) >= 2:
                try:
                    entry['seeds'] = [int(seeds[0]), int(seeds[1])]
                except (TypeError, ValueError):
                    entry['seeds'] = None
        outcome = payload_codec.decode_json_codec_if_magic(outcome_raw)
        if not isinstance(outcome, dict):
            entry['unknownOutcome'] = True
        else:
            entry['verdict'] = _whole(outcome.get('verdict'))
            entry['censored'] = bool(outcome.get('censored'))
            entry['error'] = bool(outcome.get('error'))
            entry['resolved'] = bool(outcome.get('resolved'))
            entry['finalEarned'] = _whole(outcome.get('finalEarned'))
            entry['pending'] = _whole(outcome.get('pending'))
            engine = outcome.get('engineRevision')
            if isinstance(engine, str) and engine:
                entry['engine'] = engine
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            entry['stamp'] = stamp
        return entry

    def _apply(self, entry):
        """Fold one completed sample into the incremental counters (no per-sample record kept)."""
        counts = self._counts
        counts['total'] += 1
        kind = entry['kind']
        counts[kind] = counts.get(kind, 0) + 1
        if entry['unmapped']:
            self._unmapped_entries += 1
            rowkey = _UNMAPPED
        else:
            rowkey = '%d:%d' % (entry['encounterId'], entry['defeatCount'])
            self._mapped_encounters.add(entry['encounterId'])
        self._residents.setdefault(rowkey, set()).add(entry['candidate'])
        backend = entry['backend']
        fallback = backend in ('fallback', 'python')
        if backend == 'native':
            counts['native'] += 1
        elif fallback:
            counts['fallback'] += 1
        else:
            counts['unknownBackend'] += 1
        error = bool(entry['error'])
        censored = bool(entry['censored'])
        unknown_outcome = bool(entry['unknownOutcome'])
        if unknown_outcome:
            counts['unknownOutcome'] += 1
        if censored:
            counts['censored'] += 1
        if error:
            counts['errors'] += 1
        verdict = entry['verdict']
        if verdict == WIN_VERDICT:
            counts['wins'] += 1
        elif verdict == LOSS_VERDICT:
            counts['losses'] += 1
        else:
            counts['noVerdict'] += 1
        excluded = bool(fallback or error or censored)
        if excluded:
            counts['excluded'] += 1
        row = self._rows.get(rowkey)
        if row is None:
            row = self._rows[rowkey] = _empty_row(entry['encounterId'], entry['defeatCount'])
        row['attempts'] += 1
        if backend == 'native':
            row['native'] += 1
        elif fallback:
            row['fallback'] += 1
        else:
            row['unknownBackend'] += 1
        if unknown_outcome:
            row['unknownOutcome'] += 1
        if censored:
            row['censored'] += 1
        if error:
            row['errors'] += 1
        if excluded:
            row['excluded'] += 1
        if verdict == WIN_VERDICT:
            row['wins'] += 1
            if backend == 'native' and entry['resolved'] and not error and not censored:
                value = entry['finalEarned']
                if value is not None and (row['highestChestsEarned'] is None
                                          or value > row['highestChestsEarned']):
                    row['highestChestsEarned'] = value
                    row['earnedCandidateId'] = entry['candidate']
                    row['earnedSeeds'] = _seeds(entry)
                    row['earnedSampleKey'] = entry['ident']
                    row['earnedBasis'] = 'ea-sample-final-earned'
        elif verdict == LOSS_VERDICT:
            row['losses'] += 1
            if backend == 'native' and entry['resolved'] and not error and not censored:
                value = entry['pending']
                if value is not None and (row['highestPotentialChests'] is None
                                          or value > row['highestPotentialChests']):
                    row['highestPotentialChests'] = value
                    row['potentialCandidateId'] = entry['candidate']
                    row['potentialSeeds'] = _seeds(entry)
                    row['potentialSampleKey'] = entry['ident']
                    row['potentialBasis'] = 'ea-sample-terminal-pending'
        else:
            row['noVerdict'] += 1
        # The exact observation identity: table (development vs confirmation), experiment, candidate,
        # policy, measurement window and the recovered revision/engine conditions. ANY difference
        # keeps incompatible observations in separate builds, so they are never merged into a mean.
        gkey = (entry['kind'], entry['experiment'], entry['candidate'], entry['policy'],
                entry['window'], entry['mechanics'], entry['encounterRevision'], entry['engine'])
        group = self._groups.get((rowkey, gkey))
        if group is None:
            group = self._groups[(rowkey, gkey)] = _empty_group()
        group['attempts'] += 1
        if backend == 'native':
            group['native'] += 1
        elif fallback:
            group['fallback'] += 1
        else:
            group['unknownBackend'] += 1
        if unknown_outcome:
            group['unknownOutcome'] += 1
        if censored:
            group['censored'] += 1
        if error:
            group['errors'] += 1
        if verdict == WIN_VERDICT:
            group['wins'] += 1
        elif verdict == LOSS_VERDICT:
            group['losses'] += 1
        if verdict is None:
            group['noVerdict'] += 1
        # Only a NATIVE, canonical, uncensored, errorless reading may contribute a mean observation.
        if backend == 'native' and not error and not censored and not unknown_outcome:
            if verdict == WIN_VERDICT and entry['resolved'] and entry['finalEarned'] is not None:
                amount = int(entry['finalEarned'])
                group['earnedSum'] += amount
                group['earnedCount'] += 1
                group['earnedMax'] = (amount if group['earnedMax'] is None
                                      else max(group['earnedMax'], amount))
            elif verdict == LOSS_VERDICT and entry['resolved']:
                # The recovered loss gate is a proven zero. A resolved loss may carry it explicitly;
                # an UNRESOLVED or unknown reading contributes nothing rather than a fabricated zero.
                earned = entry['finalEarned']
                amount = 0 if earned is None else int(earned)
                group['earnedSum'] += amount
                group['earnedCount'] += 1
                group['earnedMax'] = (amount if group['earnedMax'] is None
                                      else max(group['earnedMax'], amount))
                if entry['pending'] is not None:
                    group['potentialSum'] += int(entry['pending'])
                    group['potentialCount'] += 1
        stamp = entry['stamp']
        if stamp is not None:
            self._first_at = stamp if self._first_at is None else min(self._first_at, stamp)
            self._last_at = stamp if self._last_at is None else max(self._last_at, stamp)

    # -- incremental reads ------------------------------------------------------------------------

    def _absorb(self, db):
        self.stats['absorbs'] += 1
        for table, kind in _TABLES:
            self._absorb_table(db, table, kind)

    def _absorb_table(self, db, table, kind):
        state = self._tables[table]
        if not _has_table(db, table):
            state['exists'] = False
            return
        if not state['exists']:
            state['exists'] = True
            state['high'] = 0
            state['pending'] = set()
        elif state['high']:
            # A swapped/truncated library regresses the rowids; rescan rather than silently miss.
            try:
                current = int(db.execute('SELECT COALESCE(MAX(rowid), 0) FROM ' + table).fetchone()[0])
            except Exception:  # noqa: BLE001
                current = state['high']
            if current < state['high']:
                state['high'] = 0
                state['pending'] = set()
                self.stats['resets'] += 1
        self._recheck_pending(db, table, kind, state)
        select = _SELECT[table]
        while True:
            rows = db.execute(select + ' WHERE rowid > ? ORDER BY rowid LIMIT ?',
                              (state['high'], _PAGE)).fetchall()
            if not rows:
                return
            for row in rows:
                state['high'] = int(row[0])
                self._decode_row(db, table, kind, state, row)
            if len(rows) < _PAGE:
                return

    def _recheck_pending(self, db, table, kind, state):
        """Re-read only the rowids first seen with ``result IS NULL``, in bounded batches.

        A completion lands on the row's EXISTING rowid, so forward pagination alone can never see it;
        these rowids are the complete set of in-flight samples and are re-checked as one indexed
        ``rowid IN`` statement per batch instead of rescanning the ledger.
        """
        pending = state['pending']
        if not pending:
            return
        select = _SELECT[table]
        rowids = sorted(pending)
        for start in range(0, len(rowids), _RECHECK):
            chunk = rowids[start:start + _RECHECK]
            marks = ','.join('?' * len(chunk))
            rows = db.execute(select + ' WHERE rowid IN (%s)' % marks, chunk).fetchall()
            self.stats['completionRechecks'] += len(chunk)
            for row in rows:
                self._decode_row(db, table, kind, state, row)

    def _decode_row(self, db, table, kind, state, row):
        rowid = int(row[0])
        if table == 'ea_sample':
            ident = 'ea_sample:' + str(row[1])
            candidate_id, policy, mechanics, encounter_revision, window = row[2], row[3], row[4], row[5], row[6]
            result_raw, outcome_raw, stamp = row[7], row[8], row[9]
            experiment = None
        else:
            experiment_id, candidate_id, seed_a, seed_b = row[1], row[2], row[3], row[4]
            experiment = int(experiment_id)
            ident = 'ea_holdout:%s:%s:%s:%s' % (experiment_id, candidate_id, seed_a, seed_b)
            result_raw, outcome_raw, stamp = row[5], row[6], row[7]
            policy, mechanics, encounter_revision, window = self._experiment(db, experiment)
        if result_raw is None:
            state['pending'].add(rowid)
            return
        state['pending'].discard(rowid)
        self.stats['rowsRead'] += 1
        entry = self._decode(db, table=table, kind=kind, ident=ident, candidate_id=candidate_id,
                             policy=policy, window=window, mechanics=mechanics,
                             encounter_revision=encounter_revision, result_raw=result_raw,
                             outcome_raw=outcome_raw, stamp=stamp, experiment=experiment)
        self._apply(entry)

    # -- exact per-candidate evidence (reused, never rescanned) -----------------------------------

    def _evidence_seed(self, candidate_id):
        label, source = self._labels.get(candidate_id, (None, None))
        return dict(candidateId=candidate_id, label=label, source=source, attempts=0, wins=0,
                    losses=0, noVerdict=0, errors=0, censored=0, unknownOutcome=0, fallback=0,
                    unknownBackend=0, native=0, earnedSum=0, earnedCount=0, potentialSum=0,
                    potentialCount=0, meanEarned=None, meanPotential=None, comparable=False,
                    earnedMax=None, windows=[])

    @staticmethod
    def _window_view(encounter_id, defeat_count, gkey, group):
        """One exact observation identity's own sums/counts (table, experiment, policy, window...)."""
        return dict(encounterId=encounter_id, defeatCount=defeat_count, kind=gkey[0],
                    experiment=gkey[1], policy=gkey[3], measurementWindow=gkey[4],
                    mechanics=gkey[5], encounterRevision=gkey[6], engine=gkey[7],
                    attempts=group['attempts'], wins=group['wins'], losses=group['losses'],
                    noVerdict=group['noVerdict'], errors=group['errors'],
                    censored=group['censored'], unknownOutcome=group['unknownOutcome'],
                    fallback=group['fallback'], unknownBackend=group['unknownBackend'],
                    native=group['native'], earnedSum=group['earnedSum'],
                    earnedCount=group['earnedCount'], potentialSum=group['potentialSum'],
                    potentialCount=group['potentialCount'], earnedMax=group['earnedMax'],
                    meanEarned=(group['earnedSum'] / group['earnedCount']
                                if group['earnedCount'] else None),
                    meanPotential=(group['potentialSum'] / group['potentialCount']
                                   if group['potentialCount'] else None),
                    comparable=_group_comparable(group))

    @staticmethod
    def _fold_evidence(bucket, encounter_id, defeat_count, gkey, group):
        bucket['attempts'] += group['attempts']
        for name in ('wins', 'losses', 'noVerdict', 'errors', 'censored', 'unknownOutcome',
                     'fallback', 'unknownBackend', 'native', 'earnedSum', 'earnedCount',
                     'potentialSum', 'potentialCount'):
            bucket[name] += group[name]
        if group['earnedMax'] is not None:
            bucket['earnedMax'] = (group['earnedMax'] if bucket['earnedMax'] is None
                                   else max(bucket['earnedMax'], group['earnedMax']))
        bucket['windows'].append(LedgerCache._window_view(encounter_id, defeat_count, gkey, group))

    @staticmethod
    def _finalize_evidence(bucket):
        """A top-level mean only when there is ONE exact identity; otherwise windows stay separate."""
        if len(bucket['windows']) == 1:
            window = bucket['windows'][0]
            bucket['meanEarned'] = window['meanEarned']
            bucket['meanPotential'] = window['meanPotential']
            bucket['comparable'] = window['comparable']
        else:
            # Two or more incompatible identities (different table/experiment/policy/window/engine):
            # a combined mean would be fabricated, so it is left None and each window is published.
            bucket['meanEarned'] = None
            bucket['meanPotential'] = None
            bucket['comparable'] = False
        return bucket

    def fight_evidence(self, encounter_id, defeat_count=0, db=None, refresh=True):
        """Every build with recorded ea_* evidence for one fight, folded from the cached groups.

        Uses the incremental counters the view already absorbed - no whole-ledger rescan. Samples that
        could not be attributed to a resident candidate are included only when the library's declared
        scope is exactly this fight, so a recovered-but-unattributable sample is never silently
        assigned to whatever fight happens to be selected.
        """
        conn = db if db is not None else self._conn
        if conn is None:
            return None
        if refresh:
            self.view(conn)
        try:
            encounter_id, defeat_count = int(encounter_id), int(defeat_count)
        except (TypeError, ValueError):
            return None
        key = '%d:%d' % (encounter_id, defeat_count)
        attributed = False  # Current library scope never attributes an unknown historical sample.
        buckets = {}
        for (rowkey, gkey), group in self._groups.items():
            if rowkey is _UNMAPPED:
                if not attributed:
                    continue
                rowkey = key
            if rowkey != key:
                continue
            cid = gkey[2]
            bucket = buckets.get(cid)
            if bucket is None:
                bucket = buckets[cid] = self._evidence_seed(cid)
            self._fold_evidence(bucket, encounter_id, defeat_count, gkey, group)
        candidates = [self._finalize_evidence(bucket) for bucket in buckets.values()]
        candidates.sort(key=lambda entry: (-(entry['earnedSum'] or 0), entry['candidateId']))
        row = self._rows.get(key)
        return dict(ok=True, encounterId=encounter_id, defeatCount=defeat_count,
                    candidates=candidates, attributed=bool(attributed),
                    row=dict(row) if row is not None else None, source='ea-ledger')

    def candidate_evidence(self, candidate_id, db=None, refresh=True):
        """One candidate's exact per-window ea_* readings, folded from the cached groups."""
        conn = db if db is not None else self._conn
        if conn is None:
            return None
        if refresh:
            self.view(conn)
        cid = str(candidate_id)
        bucket = self._evidence_seed(cid)
        for (rowkey, gkey), group in self._groups.items():
            if gkey[2] != cid:
                continue
            if rowkey is _UNMAPPED:
                base = None
            else:
                base = _key_parts(rowkey)
            if base is None:
                continue
            self._fold_evidence(bucket, base[0], base[1], gkey, group)
        return self._finalize_evidence(bucket)

    # -- published view ---------------------------------------------------------------------------

    def view(self, db):
        if db is not self._conn:
            self._reset(db)
        token = signature(db)
        if token == self._token and self._view is not None:
            return self._view
        # Restoration or truncation is exceptional; rebuild once when it actually occurs. The
        # unresolvable candidates are checked in a few IN (...) batches rather than one query each, so
        # a library with many pruned-but-unprovable candidates does not pay a query per candidate on
        # every ledger change.
        restored = False
        for chunk in _chunks(self._missing_candidates):
            marks = ','.join('?' * len(chunk))
            if db.execute('SELECT 1 FROM candidate WHERE id IN (%s) LIMIT 1' % marks,
                          chunk).fetchone():
                restored = True
                break
        regressed = any(state['high'] and _has_table(db, table)
                        and int(db.execute('SELECT COALESCE(MAX(rowid),0) FROM '+table).fetchone()[0]) < state['high']
                        for table, state in self._tables.items())
        if restored or regressed:
            self._reset(db)
            self.stats['resets'] += 1
        self._token = token
        try:
            self._absorb(db)
            available = any(state['exists'] for state in self._tables.values())
            if not self._scope_resolved:
                self._scope = _declared_scope(db)
                self._scope_resolved = True
            self._view = self._build_view(available)
        except Exception as exc:  # noqa: BLE001 - report, never fabricate
            self._reset_accumulators()
            message = '%s: %s' % (type(exc).__name__, exc)
            self._view = dict(available=False, error=message, counts=blank_counts(), rows={},
                              leaders=[], leaders_payload=None,
                              coverage=dict(available=False, complete=False, error=message))
        return self._view

    def _build_view(self, available):
        scope = self._scope
        # A currently selected encounter cannot establish where an unidentified old result belongs.
        attribute = None
        counts = dict(self._counts)
        counts['available'] = bool(available)
        counts['unmapped'] = 0 if attribute is not None else self._unmapped_entries
        counts['firstAt'] = self._first_at
        counts['lastAt'] = self._last_at
        rows = {}
        for key, row in self._rows.items():
            if key is _UNMAPPED:
                continue
            rows[key] = dict(row)
        scope_key = None
        if attribute is not None:
            scope_key = '%d:%d' % attribute
            target = rows.get(scope_key)
            if target is None:
                target = rows[scope_key] = _empty_row(attribute[0], attribute[1])
            _merge_row(target, self._rows.get(_UNMAPPED))
        # Groups are folded per exact final encounter key first, so a candidate whose samples were
        # split between attributed and unattributable rows still competes as ONE build, never with
        # itself.
        folded = {}
        for (rowkey, gkey), group in self._groups.items():
            if rowkey is _UNMAPPED:
                if attribute is None:
                    continue
                rowkey = scope_key
            bucket = folded.setdefault(rowkey, {})
            present = bucket.get(gkey)
            if present is None:
                bucket[gkey] = dict(group)
            else:
                for name, value in group.items():
                    present[name] += value
        leaders = {}
        for rowkey, bucket in folded.items():
            encounter_id, defeat_count = _key_parts(rowkey)
            slot = leaders.get(rowkey)
            if slot is None:
                slot = leaders[rowkey] = dict(encounterId=encounter_id, defeatCount=defeat_count,
                                              residentCount=0, highestAvgEarned=None,
                                              highestAvgPotential=None)
            for gkey, group in bucket.items():
                candidate_id = gkey[2]
                label, source = self._labels.get(candidate_id, (None, None))
                for metric, total, samples in (('earned', group['earnedSum'], group['earnedCount']),
                                               ('potential', group['potentialSum'],
                                                group['potentialCount'])):
                    if samples < AVERAGE_MIN_SAMPLES:
                        continue
                    proposed = dict(candidateId=candidate_id, label=label, source=source,
                                    mean=float(total) / samples, samples=samples,
                                    attempts=group['attempts'],
                                    comparable=_group_comparable(group),
                                    noVerdict=group['noVerdict'], retainedSampleCount=samples)
                    field = 'highestAvgEarned' if metric == 'earned' else 'highestAvgPotential'
                    if _outranks_mean(slot.get(field), proposed):
                        slot[field] = proposed
        for rowkey, slot in leaders.items():
            residents = set(self._residents.get(rowkey) or ())
            if attribute is not None and rowkey == scope_key:
                residents |= set(self._residents.get(_UNMAPPED) or ())
            slot['residentCount'] = len(residents)
        ordered = [leaders[key] for key in sorted(leaders, key=_encounter_sort_key)]
        payload = dict(ok=True, leaders=ordered, encounters=len(ordered),
                       minSamples=AVERAGE_MIN_SAMPLES, source='encounter-ledger')
        coverage = dict(available=bool(available),
                        complete=bool(available and counts['unmapped'] == 0
                                      and counts['unknownOutcome'] == 0
                                      and counts['errors'] == 0),
                        samples=counts['total'], mapped=counts['total'] - counts['unmapped'],
                        unmapped=counts['unmapped'], native=counts['native'],
                        excluded=counts['excluded'], unknownOutcome=counts['unknownOutcome'],
                        errors=counts['errors'], note=None)
        if not available:
            coverage['note'] = 'this library has no ea_* encounter ledger'
        elif not coverage['complete']:
            coverage['note'] = ('counts are partial: %d unmapped, %d without a canonical outcome, '
                                '%d errored' % (counts['unmapped'], counts['unknownOutcome'],
                                                counts['errors']))
        return dict(available=bool(available), error=None, counts=counts, rows=rows,
                    leaders=ordered, leaders_payload=payload, coverage=coverage)


def _meta_json(db, key):
    try:
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    except Exception:  # noqa: BLE001 - no meta table means no declaration
        return None
    if not row or row[0] is None:
        return None
    value = _json(row[0])
    return _json(value) if isinstance(value, str) else value


def _declared_scope(db):
    """The library's single declared encounter, for attributing pruned-candidate samples.

    A candidate can be pruned out of the ``candidate`` table after its samples were recorded, so the
    encounter id cannot always be read from the scenario. When the library's own scope names exactly
    one encounter AND nothing in the ledger contradicts it, that declaration is the only encounter the
    samples can belong to - the same attribution rule the legacy lifetime reconstruction uses. When a
    second encounter is present the samples stay unattributed rather than being assigned to a guess.
    """
    scope = _meta_json(db, 'scope')
    if isinstance(scope, dict) and 'encounterId' in scope:
        try:
            return (int(scope['encounterId']), int(scope.get('defeatCount') or 0))
        except (TypeError, ValueError):
            return None
    return None


def _seeds(entry):
    seeds = entry.get('seeds')
    return list(seeds) if isinstance(seeds, (list, tuple)) else None


def _outranks_mean(leader, entry):
    """Higher mean, then larger n, then lower candidate id (the desktop's own tie-break)."""
    if leader is None:
        return True
    if entry['mean'] != leader['mean']:
        return entry['mean'] > leader['mean']
    if entry['samples'] != leader['samples']:
        return entry['samples'] > leader['samples']
    return entry['candidateId'] < leader['candidateId']


def blank_lifetime():
    return dict(lifetimeAttempts=0, lifetimeWins=0, lifetimeLosses=0, lifetimeNoVerdict=0,
                highestPotentialChests=None, highestChestsEarned=None,
                potentialBasis=None, earnedBasis=None)


_EA_KEYS = _COUNTER_KEYS + ('highestPotentialChests', 'potentialCandidateId', 'potentialSeeds',
                            'potentialSampleKey', 'potentialBasis', 'highestChestsEarned',
                            'earnedCandidateId', 'earnedSeeds', 'earnedSampleKey', 'earnedBasis')


def _empty_ea():
    entry = {name: 0 for name in _COUNTER_KEYS}
    entry.update(highestPotentialChests=None, potentialCandidateId=None, potentialSeeds=None,
                 potentialSampleKey=None, potentialBasis=None, highestChestsEarned=None,
                 earnedCandidateId=None, earnedSeeds=None, earnedSampleKey=None, earnedBasis=None)
    return entry


def merge_stats(legacy, view):
    """Fold the ea_* ledger into the legacy encounter rows, additively and without double counting.

    Legacy fields are preserved verbatim under ``legacy``; the top-level fields the overview already
    reads become the union of the two disjoint ledgers (a community run never touches the legacy
    ``run`` table, so the two sets cannot overlap). Each row also carries its own ``ea`` breakdown,
    including the wins/losses/no-verdict split, the native/fallback/error counts and the candidate
    that set each maximum. Rows that exist only in the ea ledger are added rather than dropped; rows
    that exist only in the legacy ledger keep their counts.
    """
    merged = {}
    legacy = legacy if isinstance(legacy, dict) else {}
    for key, legacy_row in legacy.items():
        row = blank_lifetime()
        row.update(legacy_row if isinstance(legacy_row, dict) else {})
        row = dict(row)
        row['legacy'] = {name: value for name, value in row.items()}
        row['ea'] = _empty_ea()
        row['native'] = 0
        row['excluded'] = 0
        row['complete'] = True
        merged[key] = row
    ea_rows = (view or {}).get('rows') or {}
    for key, ea in ea_rows.items():
        row = merged.get(key)
        if row is None:
            row = dict(blank_lifetime())
            row['legacy'] = {name: value for name, value in row.items()}
            row['ea'] = _empty_ea()
            row['native'] = 0
            row['excluded'] = 0
            row['complete'] = True
            merged[key] = row
        preserved = dict(row['legacy'])
        row['lifetimeAttempts'] = int(preserved.get('lifetimeAttempts') or 0) + ea['attempts']
        row['lifetimeWins'] = int(preserved.get('lifetimeWins') or 0) + ea['wins']
        row['lifetimeLosses'] = int(preserved.get('lifetimeLosses') or 0) + ea['losses']
        row['lifetimeNoVerdict'] = int(preserved.get('lifetimeNoVerdict') or 0) + ea['noVerdict']
        if ea['highestPotentialChests'] is not None and (
                row.get('highestPotentialChests') is None
                or ea['highestPotentialChests'] > row['highestPotentialChests']):
            row['highestPotentialChests'] = ea['highestPotentialChests']
            row['potentialBasis'] = ea['potentialBasis']
        if ea['highestChestsEarned'] is not None and (
                row.get('highestChestsEarned') is None
                or ea['highestChestsEarned'] > row['highestChestsEarned']):
            row['highestChestsEarned'] = ea['highestChestsEarned']
            row['earnedBasis'] = ea['earnedBasis']
        row['ea'] = {name: ea.get(name) for name in _EA_KEYS}
        row['native'] = ea['native']
        # ``excluded`` is the UNION of the samples that are fallback/python, errored or censored -
        # never the sum of the three counters, which would count an overlapping reading twice.
        row['excluded'] = int(ea.get('excluded') or 0)
        row['complete'] = bool(ea['unknownOutcome'] == 0 and ea['errors'] == 0
                               and ea['fallback'] == 0)
    return merged
