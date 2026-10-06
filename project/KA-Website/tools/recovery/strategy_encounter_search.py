"""Encounter-aware, Community-first search coordinator.

This is the real runtime the optimiser drives while `community-first` mode is enabled. It is not a
helper library: `Optimizer._loop` hands it the worker budget every pass and skips every legacy
proposal, planner, average, track, MP-recovery, reservation and fallback path while it is enabled.

The bounded cycle is:

  admitted Community reference
    -> shared joint proposals (`strategy_joint_proposals.pool`)
    -> a finite paired development sample (8 runs, extended to 16 for a higher-mean uncertain
       challenger)
    -> canonical outcomes in the `ea_*` ledger (`strategy_experiment_store`)
    -> refreshed earning portfolio / boundary record / adviser fit
    -> the next deliberate proposal
    -> a frozen, fresh, collision-checked confirmation against the reference
    -> publication only when the FULL predeclared holdout is complete and resolved.

Everything the coordinator decides survives a restart: the session, experiments, reservations,
outcomes and stop plans live in the ledger, and the round cursor / questions live in the library's
`encounterRuntime` meta. A restart therefore never re-selects work that would change an outstanding
plan and never double-charges a seed pair.

Ownership: the whole community-first stream is one generator (Community). Shared refinement,
comparison, support, boundary and exploration work is charged to Community's finite purpose budgets.
No other stream is admitted and no legacy loop competes.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from copy import deepcopy
from collections.abc import Set as AbstractSet

import strategy_encounter_adviser as adviser
import strategy_encounter_decisions as decisions
import strategy_experiment_store as ledger
import strategy_payload_codec as payload_codec
import strategy_intent_codec as intent_codec
import strategy_joint_proposals as proposals_service
import strategy_mechanics as mechanics
import strategy_operating_regions as regions
import strategy_outcomes as outcomes_service
import strategy_parallel_proposals as parallel_proposals
import strategy_search_mode as modes
import strategy_revision
import strategy_students as students
import strategy_stream_proposals as stream_proposals
import strategy_encounter_confirmation as confirmation_service
import strategy_encounter_presentation as presentation_service

# Confirmation history only needs a pair of frozen scenario values. Keep this cache bounded and
# separate from the canonical full-intent cache; schema 5 stores those values in the compressed
# full_intent BLOB, so JSON1 cannot project them from the thin intent_json metadata column.
CONFIRMATION_PROJECTION_BATCH_MAX = 300
CONFIRMATION_PROJECTION_ROW_LIMIT = 10000
CONFIRMATION_PROJECTION_BYTE_LIMIT = 16 * 1024 * 1024


class _ConfirmationProjectionUnsupported(ValueError):
    """Use the canonical full reader when a legacy intent shape cannot be projected safely."""


def _confirmation_projection_view(raw, candidate_ids):
    if raw is None or raw == '':
        return None, 0
    if not isinstance(raw, str):
        raise _ConfirmationProjectionUnsupported('full intent is not SQLite text')
    # Canonical C-backed JSON decoding preserves Python's last-duplicate and numeric semantics.
    # The batch avoids N SQLite point reads; only the selected dynamic pair survives in the partial
    # cache, while the full object remains transient and never enters _intent_cache.
    intent = json.loads(raw)
    if not isinstance(intent, dict):
        raise _ConfirmationProjectionUnsupported('full intent root is not an object')
    observed = intent.get('observedScenarios') or {}
    if not isinstance(observed, dict):
        raise _ConfirmationProjectionUnsupported('observedScenarios has a truthy non-object value')
    view = {name: intent[name] for name in ('measurementWindow', 'encounterRevision')
            if name in intent}
    view['observedScenarios'] = {
        key: observed[key] for key in candidate_ids if key in observed}
    # The raw decoded source size is a conservative byte budget for the smaller retained view.
    return view, len(raw.encode('utf-8'))

VERSION = 'encounter-search-1'
_RECOVERY_NOT_PROVIDED = object()
#: Item 9 fail-closed: all-strategy needs every registered stream generator wired through the shared
#: evaluator. Until that exists, REFUSE rather than run Community and claim all-strategy.
ALL_STRATEGY_ERROR = ('all-strategy stream generators are not wired in this coordinator revision; '
                      'refusing to run the Community stream under an all-strategy label')
#: The library meta key holding the coordinator's persisted cursor/state.
RUNTIME_KEY = 'encounterRuntime'
#: The library meta key holding the explicit, rollback-able mode mapping.
MODE_KEY = 'encounterAware'

#: Explicit finite default budgets for a fresh Community-first library. A placeholder of one run per
#: purpose is deliberately replaced by useful whole-run budgets; every one stays finite.
DEFAULT_PURPOSES = {'improvement': 128, 'boundary': 64, 'support': 32,
                    'comparison': 128, 'exploration': 32}

IMPROVEMENT_RUNS = 8
CHALLENGER_RUNS = 16
#: One durable admission transaction may cover at most this many ordered seed-pair jobs. The actual
#: batch is also bounded by currently free evaluator capacity and this pass's frozen eligible jobs.
RESERVATION_BATCH_MAX = 64
#: The declared finite knowledge-extension ladder. ``max_pairs`` is a TOTAL target cap: a
#: challenger climbs one rung at a time and the last rung is 64 TOTAL pairs, never 64 ADDITIONAL
#: pairs. The owner's improvement budget must fund the COMPLETE paired stage before it is bought.
EXTENSION_MAX_PAIRS = 64
#: Distinct development experiments that must complete (or rounds that must pass) between two
#: knowledge extensions, so a large open proposal pool can never starve uncertain challengers
#: and a challenger extension can never starve diverse coverage.
EXTENSION_INTERLEAVE_ROUNDS = 3
#: Bounded selection of the REAL legacy library for adviser priors: at most 24 candidates,
#: <=256 rows each, scanned newest-first over a bounded candidate window. Candidate identities
#: come from the legacy candidate/meta/lineage tables (NOT the new EA history), so a migrated
#: mature library with no EA history still yields priors.
LEGACY_MAXIMUM_CANDIDATES = 24
LEGACY_ROWS_PER_CANDIDATE = 256
LEGACY_CANDIDATE_SCAN = 256
#: Lineage/candidate sources that name no student: the supplied baseline and inert origin rows
#: belong to the community-first lane rather than to a stream.
OWNERLESS_SOURCES = (None, 'origin', 'supplied', 'import')
CONFIRM_PAIRS = confirmation_service.MINIMUM_PAIRS
#: A paired development experiment charges BOTH arms, so a full 8-battle improvement budget buys 4
#: reference-matched pairs. The nomination threshold is one full paired sample of that size.
CONFIRM_MIN_DEVELOPMENT = 4
MAX_SUBMISSIONS_PER_PASS = 4

#: Purposes whose children are INDEPENDENT development challengers measured against one shared
#: reference from one ranked proposal pool. Only these may be frozen as a bounded prospective cohort:
#: a boundary/support question, a knowledge extension and the frozen confirmation each change the
#: STUDY rather than just the challenger, so they stay single-experiment studies.
COHORT_PURPOSES = ('improvement', 'exploration')
#: Larger pools retain up to three useful dispatch waves (at most twelve challengers) from one
#: ranked pool. The queue amortises preparation and refills slots as workers finish; sample sizes,
#: finite purpose budgets and the shared frozen reference do not change. Selection adapts again only
#: after this explicitly recorded prospective cohort drains.
def cohort_bound(workers):
    """One challenger for small pools; up to three dispatch waves for larger pools."""
    waves = max(1, -(-max(1, int(workers)) // IMPROVEMENT_RUNS))
    return 1 if waves == 1 else min(12, waves * 3)

#: Observation/resource keys that must match across the two arms of one matched comparison. They are
#: copied from the single frozen policy onto both the candidate and the reference worker scenario, so
#: the only difference between the arms is the intended build field(s).
POLICY_KEYS = ('finishPolicy', 'tickLimit', 'holyHerbStock', 'startProfile', 'inputs',
               'holyHerbMaxUses', 'holyHerbTriggerUnits', 'mpWatchUnits')
#: The DPS ATK path is the joint compensation target the shared proposal generator moves, and the
#: canonical parameter ids a role/stat language is translated into.
ATK_PARAMETER_ID = 13
STAT_PARAMETER_IDS = {'atk': 13, 'lck': 16, 'spd': 15, 'dex': 19, 'hp': 10, 'mp': 11, 'def': 14}
#: Boundary question kinds mapped to the ledger's two boundary record kinds.
BOUNDARY_KINDS = {'fixed-build': 'fixed', 'compensated': 'compensated', 'support': 'support'}
#: Frozen question kind -> the shared generator purpose that builds its candidate point. A support
#: question must use the support generator (it lowers HP/MP/DEF); routing it through the fixed-build
#: generator produced an empty pool, so the support budget could never buy an assessment.
BOUNDARY_POOL_PURPOSES = {'fixed-build': 'boundary', 'compensated': 'compensated',
                        'support': 'support'}

#: Test/embedding seam: `factory(workers, telemetry, timeout)` -> an evaluator-like object.
EVALUATOR_FACTORY = None

#: Lazy, string-based resolution of the OPTIONAL asynchronous planning host services. The joint
#: worker API (`strategy_parallel_proposals.PlanningPool` plus
#: `strategy_joint_proposals.compute_planning_task` and its request factory) is an external service
#: this coordinator consumes. It is resolved by NAME at call time so this module never imports or
#: edits the joint service, and an older build without the API keeps the unchanged synchronous
#: planning path rather than failing.
_PLANNING_REQUEST_FACTORY_NAMES = (
    'planning_request', 'make_planning_request', 'create_planning_request', 'build_planning_request',
    'planning_task_request', 'request_for_planning', 'request_for', 'planning_request_for',
    'make_request', 'create_request', 'build_request', 'request')


def planning_pool_class():
    """`strategy_parallel_proposals.PlanningPool`, or None when that API has not landed."""
    return getattr(parallel_proposals, 'PlanningPool', None)


def planning_task_function():
    """`strategy_joint_proposals.compute_planning_task`, or None when it has not landed."""
    return getattr(proposals_service, 'compute_planning_task', None)


def planning_request_class():
    """`strategy_joint_proposals.EncounterPlanningRequest`, or None when that API has not landed."""
    return getattr(proposals_service, 'EncounterPlanningRequest', None)


def planning_request_factory():
    """The joint service's request factory, discovered by string; None when it has not landed.

    The exact factory name is owned by the joint service, so it is discovered rather than imported.
    Only callables that are NOT the worker entry point are accepted: the explicit candidate names are
    tried first, then any remaining public name mentioning 'request'.
    """
    task = planning_task_function()
    for name in _PLANNING_REQUEST_FACTORY_NAMES:
        factory = getattr(proposals_service, name, None)
        if callable(factory) and factory is not task:
            return factory
    for name in dir(proposals_service):
        if name.startswith('_') or 'request' not in name.lower():
            continue
        factory = getattr(proposals_service, name)
        if callable(factory) and factory is not task:
            return factory
    return None


def planning_default_workers():
    """The joint service's measured preparation-worker default, or 0 when unavailable."""
    try:
        return max(0, int(getattr(parallel_proposals, 'DEFAULT_PREP_WORKERS', 0) or 0))
    except (TypeError, ValueError):
        return 0


def default_config(mode='community-first', purposes=None):
    """A validated version-1 config with explicit finite purpose budgets."""
    config = modes.default_config(mode)
    config['purposes'] = dict(purposes or DEFAULT_PURPOSES)
    if mode == 'all-strategy':
        apply_derived_budgets(config)
    modes.validate_config(config)
    return config


def apply_derived_budgets(config):
    """Attach the deterministic per-owner budget matrix to an all-strategy config in place.

    An explicit matrix is left untouched. Otherwise `modes.derive_budget_matrix` apportions the
    declared purpose totals across the allocated owners, so every owner's finite budget is decided
    by its allocation rather than by a boolean admission. The derived matrix is stored on the config
    itself, which makes it visible in `preview`/`report` BEFORE any activation.
    """
    if config.get('mode') != 'all-strategy' or config.get('budgets'):
        return config
    config['budgets'] = {owner: dict(row)
                         for owner, row in modes.derive_budget_matrix(config)['matrix'].items()}
    return config


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _read_json(db, key):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    if row is None or row[0] in (None, ''):
        return None
    try:
        return json.loads(row[0])
    except RecursionError:
        # Old mode mappings could contain thousands of nested rollback snapshots. Status is a
        # read-only path, so project away the second (recursive) previous value in memory only.
        if key != MODE_KEY:
            return None
        projected = _discard_recursive_previous(row[0])
        if projected is None:
            return None
        try:
            return json.loads(projected)
        except (ValueError, RecursionError):
            return None
    except ValueError:
        return None


def _discard_recursive_previous(raw):
    """Replace the nested rollback chain with `{}` without decoding the deep JSON value."""
    previous_fields = 0
    index = 0
    length = len(raw)
    while index < length:
        if raw[index] != '"':
            index += 1
            continue
        start = index
        index += 1
        escaped = False
        while index < length:
            char = raw[index]
            index += 1
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                break
        else:
            return None
        token = raw[start:index]
        colon = index
        while colon < length and raw[colon].isspace():
            colon += 1
        if token != '"previous"' or colon >= length or raw[colon] != ':':
            continue
        previous_fields += 1
        if previous_fields < 2:
            continue
        value = colon + 1
        while value < length and raw[value].isspace():
            value += 1
        if value >= length or raw[value] != '{':
            return None
        stack = []
        in_string = False
        escaped = False
        for end in range(value, length):
            char = raw[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in '{[':
                stack.append(char)
            elif char in '}]':
                if not stack:
                    return None
                opener = stack.pop()
                if (opener, char) not in (('{', '}'), ('[', ']')):
                    return None
                if not stack:
                    return raw[:value] + '{}' + raw[end + 1:]
        return None
    return None


def _blank_report(fresh=False):
    return dict(version=VERSION, enabled=False, mode='community-first',
                config=default_config('community-first'), runtimeRevision=None, sessionId=None,
                question=None, encounter=None,
                mechanicalRegions={'points': [], 'gaps': [], 'note': 'no measured regions yet'},
                budgets={}, purposeSpending={}, portfolio=[], boundaries=[],
                progress={'roundIndex': 0, 'experiments': 0, 'reserved': 0, 'completed': 0,
                          'confirmation': None, 'publication': None, 'freshLibrary': bool(fresh)},
                timings={}, limitations=[], migrationPreview=None)


def bootstrap_report(path):
    """Read-only status for a library the loop has not opened yet.

    A missing `encounterRuntime` meta means the mode is not active: an existing library is disabled
    and never auto-migrated. A truly new library (no meta at all) reports the Community-first
    default, but it is still only *enabled* once the user starts a session.
    """
    import sqlite3
    from pathlib import Path
    path = Path(path)
    if not path.is_file():
        return _blank_report(fresh=True)
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    except sqlite3.Error:
        return _blank_report(fresh=True)
    try:
        mapping = _read_json(connection, MODE_KEY)
        runtime = _read_json(connection, RUNTIME_KEY)
        schema = connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    except sqlite3.Error:
        return _blank_report(fresh=True)
    finally:
        connection.close()
    fresh = schema is None and runtime is None and mapping is None
    report = _blank_report(fresh=fresh)
    if isinstance(mapping, dict):
        report['enabled'] = bool(mapping.get('enabled'))
        report['mode'] = mapping.get('mode') or report['mode']
        report['config'] = mapping.get('config') or report['config']
    if isinstance(runtime, dict):
        view = runtime.get('report') if isinstance(runtime.get('report'), dict) else runtime
        report['sessionId'] = view.get('sessionId', runtime.get('sessionId'))
        question = view.get('question')
        if isinstance(question, dict) and 'question' in question and 'budget' in question:
            # The persisted state stores {question, budget, spent}; expose the frozen question.
            question = question.get('question')
        report['question'] = question
        report['encounter'] = view.get('encounter', runtime.get('encounter'))
        report['progress'] = view.get('progress') or report['progress']
    return report


def _activate_session(ledger, db, session_id, config):
    """Campaign encounter sessions coexist; manual encounter search remains exclusive."""
    study_scope = (config or {}).get('studyScope') or {}
    if isinstance(study_scope, dict) and study_scope.get('campaignScope'):
        return ledger.activate_session(db, session_id)
    return ledger.activate_exclusive_session(db, session_id)


class Coordinator:
    """The Community-first runtime. One instance per open library, owned by `Optimizer._loop`."""

    def __init__(self, *, path, revision=None, scheduler_revision=None,
                 mode='community-first', config=None,
                 constraints=None, encounter=None, reference=None, workers=1, telemetry=True,
                 maximum=12, timeout=None, runtime_key=None):
        self.path = path
        # `revision` is the battle/measurement compatibility digest. Runtime source diagnostics
        # remain separate so scheduling and recovery edits do not invalidate frozen work.
        self.revision = revision
        self.scheduler_revision = (scheduler_revision if scheduler_revision is not None
                                   else revision)
        self.mode = mode
        self.constraints = constraints
        self.encounter = encounter
        self.reference = reference
        self.workers = max(1, int(workers))
        self.telemetry = bool(telemetry)
        self.maximum = max(1, min(int(maximum), 12))
        self.timeout = timeout
        self.runtime_key = runtime_key or RUNTIME_KEY
        self._submission_quota = None
        self._submission_started = 0
        self.duty = 1.0
        self.enabled = False
        self.session_id = None
        self.thresholds = None
        self.previous = {}
        self.state = self._fresh_state()
        self.evaluator = None
        #: The shared asynchronous planning host, OWNED by the Optimizer. The coordinator never
        #: constructs or closes a pool; it submits one request and (on the optimizer thread) accepts
        #: the harvested result. `None` keeps the unchanged synchronous planning path.
        self.planning_host = None
        #: At most ONE pending proposal group per encounter. A second round never starts while a
        #: result is outstanding, so a slow encounter can never accumulate duplicate planning work.
        self._planner_pending = None
        #: Observer-only planner accounting: never a selection input.
        self._planner_metrics = {'submitted': 0, 'accepted': 0, 'rejected': 0,
                                 'latencySeconds': None}
        self._planner_latency = []
        self._planner_allocation = {'requested': 0, 'effective': 0}
        self.migration_preview = None
        self.activated = False
        self.limitations = []
        self._last_budget_rows = {}
        #: Immutable experiment intents by id (an intent never changes once created).
        self._intent_cache = {}
        #: Bounded partial confirmation rows are scoped to a connection, compatible-id token and
        #: the exact dynamic nominee/reference pair. This never stores full intents.
        self._confirmation_projection_cache = None
        #: Cached revision digests. They are invalidated on an explicit activation/config change, on a
        #: database-object replacement, and at the start of an ACTIVE pass (so a changed mechanics file
        #: fails closed at the next dispatch boundary rather than being ignored for the process).
        self._compat = None
        self._mech = None
        #: The database object the per-db caches belong to. A replacement resets them.
        self._db_ref = None
        #: The database object whose `ea_*` schema was already ensured.
        self._initialized_db = None
        #: Read-only historical-prior cache, keyed by (owner context, compatibility). A prior is
        #: adviser guidance only; it is never pooled with a fresh development view of the same id.
        self._legacy_cache = {}
        self._legacy_read_cache = {}
        #: The global legacy+EA forbidden seed set, read once per engine revision through the shared
        #: collector and reused; a failed read is surfaced and refuses to draw rather than guessing.
        self._freshness = None
        self._freshness_key = None
        self._freshness_report = None
        #: The reason a blocked seed-history read refused to draw (None once a read succeeds).
        self._freshness_blocked = None
        #: The last readiness block, so a blocked history read is reported as readiness, not
        #: as spent or unspendable work. It is a session fact, never restored on restart.
        self._readiness_blocked = None
        #: Bounded memo keyed by loaded engine, mechanics dependencies and exact scenario.
        #: An unchanged frozen build can reuse its compilation across fleet passes.
        self._encounter_revision_cache = {}
        # Exact compiled enemy revision selected by the current encounter-local reference. This is
        # distinct from encounter id: defeatCount and the enemy payload can change the compilation.
        self._encounter_revision_scope = None
        #: Set when planning inputs changed (new evidence, config/policy, activation, db swap). It lets
        #: an unchanged idle pass skip planning without a database round-trip.
        self._planning_dirty = True
        #: Set when a reservation/completion/session change means the cached budget rows are stale.
        self._budget_dirty = True
        #: Signature/identity of the last persisted runtime payload, so an unchanged idle pass does not
        #: rewrite the whole state.
        self._persisted_signature = None
        self._persisted_store = None
        self._compatible_ids_cache = None
        self._outcomes_cache = {}
        #: Observer-only record of the last dispatch capacity (workers vs finite frozen jobs).
        self.last_submission_limit = None
        if config is not None:
            modes.validate_config(config)
            self.config = config
            self.mode = config['mode']
        else:
            self.config = default_config(mode)

    def _fresh_state(self):
        return {'roundIndex': 0, 'queue': [], 'current': None, 'cohort': [],
                'completedExperiments': [],
                'history': [], 'portfolio': [], 'boundaries': [], 'features': {},
                'advisers': {}, 'adviserSignatures': {},
                'question': None, 'confirmation': None, 'publication': None,
                'purposeRotation': 0, 'ownerRotation': 0, 'adviserSignature': None, 'timings': {},
                'unspendable': [], 'unspendableTasks': [], 'domains': {}, 'idle': False,
                'persistError': None, 'migrationPreview': None, 'policy': None,
                'presentation': None, 'presentationCache': None, 'thresholds': None,
                'mechanismArchive': [], 'holdoutRecord': None}

    # -- lifecycle --------------------------------------------------------------------------------
    def load(self, store, *, mapping_override=None):
        """Read this study's persisted mapping/cursor and decide whether it is active."""
        mapping = mapping_override if mapping_override is not None else (
            store.get(MODE_KEY) if store is not None else None)
        runtime = store.get(self.runtime_key) if store is not None else None
        if isinstance(mapping, dict):
            self.enabled = bool(mapping.get('enabled'))
            self.mode = mapping.get('mode') or self.mode
            self.config = mapping.get('config') or self.config
            self.constraints = mapping.get('constraints', self.constraints)
            self.encounter = mapping.get('encounter', self.encounter)
            self.reference = mapping.get('reference', self.reference)
            self.thresholds = mapping.get('thresholds', self.thresholds)
            self.previous = mapping.get('previous') or {}
            try:
                modes.validate_config(self.config)
            except ValueError as exc:
                self.enabled = False
                self.limitations.append(f'persisted config rejected: {exc}')
        # A persisted cursor is a PER-STUDY cursor. If the runtime was written for a different
        # encounter, adopting its portfolio/history/question would let a new study reuse another
        # encounter's charged work, so the cursor is left fresh and only the session id is dropped.
        runtime_encounter = runtime.get('encounter') if isinstance(runtime, dict) else None
        same_study = (not isinstance(runtime, dict) or runtime_encounter is None
                      or self.encounter is None or runtime_encounter == self.encounter)
        if isinstance(runtime, dict) and same_study:
            for key in ('roundIndex', 'queue', 'current', 'cohort', 'completedExperiments', 'history',
                        'portfolio', 'boundaries', 'features', 'question', 'confirmation',
                        'publication', 'purposeRotation', 'ownerRotation', 'adviserSignature',
                        'timings', 'unspendable', 'unspendableTasks', 'domains', 'idle',
                        'persistError', 'policy', 'paired', 'presentation', 'presentationCache',
                        'thresholds', 'mechanismArchive', 'holdoutRecord'):
                if key in runtime:
                    self.state[key] = runtime[key]
            # Legacy single-``current`` state resumes unchanged: adopt it as a one-member cohort so
            # the batch machinery never duplicates or orphans the outstanding experiment.
            if not self._cohort() and self.state.get('current') is not None:
                self._set_cohort([self.state['current']])
            self.session_id = runtime.get('sessionId')
        elif isinstance(runtime, dict):
            self._note('persisted runtime belongs to another encounter study; this study starts '
                       'with a fresh cursor')
        # A freshly loaded coordinator has no per-database or persisted-payload caches; the next pass
        # re-derives them once for the database object it is actually handed.
        self._db_ref = None
        self._initialized_db = None
        self._intent_cache = {}
        self._confirmation_projection_cache = None
        self._compatible_ids_cache = None
        self._outcomes_cache = {}
        self._compat = None
        self._mech = None
        self._encounter_revision_scope = None
        self._planning_dirty = True
        self._budget_dirty = True
        self._persisted_signature = None
        self._persisted_store = None
        self._freshness = None
        self._freshness_key = None
        self._freshness_report = None
        self._freshness_blocked = None
        self._readiness_blocked = None
        self._legacy_cache = {}
        self._legacy_read_cache = {}
        self._encounter_revision_scope = self._encounter_scope_anchor(store)[1]
        return self

    def close(self):
        if self.evaluator is not None:
            try:
                self.evaluator.close()
            finally:
                self.evaluator = None
        # Cancel this encounter's outstanding UNSTARTED planning request through the shared host. The
        # host owns the one shared planning pool, so the coordinator never constructs, closes or
        # shuts it down: doing so would kill unrelated encounters' queued and running work.
        host = self.planning_host
        cancel = getattr(host, 'cancel_planning', None) if host is not None else None
        if callable(cancel):
            try:
                cancel(self)
            except Exception:  # noqa: BLE001 - cancellation must never mask the real result
                pass
        self._planner_pending = None
        self.planning_host = None

    @property
    def inflight(self):
        """Actual submitted simulations; frozen queued plans are safe to pause and resume."""
        return bool(self.evaluator is not None and self.evaluator.busy())

    @property
    def busy(self):
        # Existing desktop start/pause guards use busy to mean simulations still saving. A queued
        # frozen plan is resumable work, not an active worker. _has_work and report.progress.current
        # retain the entire cohort and keep the campaign's separate drained-boundary guard closed.
        return self.inflight

    def configure_workers(self, workers):
        """Follow the pool size the user started: rebuild the evaluator when it changes."""
        workers = max(1, int(workers))
        if workers != self.workers:
            self.workers = workers
            if self.evaluator is not None:
                self.evaluator.close()
                self.evaluator = None

    def configure_duty(self, duty):
        """Follow the duty cycle the user started, on the same per-worker semantics as the pool.

        ``duty`` is the requested busy fraction (0.10-1.00). It is preserved across an evaluator
        rebuild, and applied to the evaluator when one exists.
        """
        try:
            value = float(duty)
        except (TypeError, ValueError):
            value = 1.0
        self.duty = max(0.05, min(1.0, value))
        if self.evaluator is not None:
            configure = getattr(self.evaluator, 'configure_duty', None)
            if callable(configure):
                configure(self.duty)
        return self.duty

    def _make_evaluator(self):
        if self.evaluator is None:
            factory = EVALUATOR_FACTORY
            if factory is not None:
                self.evaluator = factory(workers=self.workers, telemetry=self.telemetry,
                                         timeout=self.timeout)
            else:
                # Queue scheduling is separate from the canonical measurement module so a
                # scheduling improvement does not change frozen battle compatibility.
                import strategy_encounter_ready_queue as evaluation
                self.evaluator = evaluation.ReadyQueueEvaluator(
                    workers=self.workers, telemetry=self.telemetry,
                    timeout=(self.timeout or evaluation.DEFAULT_TIMEOUT_SECONDS))
            # Opt-in trace metadata only: the focused encounter id. Harmless on a stub evaluator
            # without a trace sink, so it can never affect scheduling or dispatch.
            try:
                self.evaluator.encounter_id = self.encounter
            except Exception:  # noqa: BLE001 - a diagnostic attribute must never break dispatch
                pass
            configure = getattr(self.evaluator, 'configure_duty', None)
            if callable(configure):
                configure(self.duty)
        return self.evaluator

    # -- explicit commands ------------------------------------------------------------------------
    def preview(self, value=None):
        """PURE migration plan; never writes and never enables anything."""
        value = value or {}
        mode = value.get('mode') or self.mode
        old_allocations = value.get('allocations')
        if old_allocations is None:
            old_allocations = self.config.get('allocations') if self.config else None
        old_purposes = value.get('oldPurposes', (self.config or {}).get('purposes'))
        old_budgets = value.get('oldBudgets', (self.config or {}).get('budgets'))
        plan = modes.plan_migration(old_allocations, mode, old_purposes=old_purposes,
                                    old_budgets=old_budgets)
        if value.get('purposes'):
            plan['config']['purposes'] = dict(value['purposes'])
        plan['purposes'] = dict(plan['config'].get('purposes') or {})
        apply_derived_budgets(plan['config'])
        modes.validate_config(plan['config'])
        derived = modes.derive_budget_matrix(plan['config'])
        plan['budgetMatrix'] = {owner: dict(row)
                                for owner, row in (plan['config'].get('budgets') or {}).items()}
        plan['budgetOwners'] = list(derived['owners'])
        plan['budgetRemainders'] = list(derived['remainders'])
        plan['budgetSource'] = derived['source']
        self.migration_preview = plan
        return plan

    def activate(self, store, value):
        """Enable the mode with an EXPLICIT config. Caller guarantees paused / no pending."""
        value = value or {}
        config = value.get('config')
        if config is None:
            purposes = value.get('purposes') or DEFAULT_PURPOSES
            config = default_config(value.get('mode') or self.mode, purposes=purposes)
        config = dict(config)
        apply_derived_budgets(config)
        modes.validate_config(config)
        self.config = config
        self.mode = config['mode']
        if 'constraints' in value:
            self.constraints = value.get('constraints')
        if 'encounter' in value:
            self.encounter = value.get('encounter')
        if 'reference' in value:
            self.reference = value.get('reference')
        self._encounter_revision_scope = self._encounter_scope_anchor(store)[1]
        if 'thresholds' in value:
            self.thresholds = value.get('thresholds')
            self.state['thresholds'] = self.thresholds
        ledger.initialize(store.db)
        session_id = ledger.configure_session(store.db, config)
        previous_mapping = value.get('previousMapping')
        previous_mapping = dict(previous_mapping) if isinstance(previous_mapping, dict) else {}
        # Store callers may pass the entire former mode mapping. Keeping its own rollback pointer
        # nests the complete mapping again on every activation; preserve one snapshot only.
        previous_mapping.pop('previous', None)
        self.previous = dict(shares=dict(value.get('previousShares') or {}),
                             tracks=dict(value.get('previousTracks') or {}),
                             mapping=previous_mapping)
        self.session_id = session_id
        _activate_session(ledger, store.db, session_id, config)
        self.enabled = True
        self.activated = True
        self.state['sessionId'] = session_id
        # A new session is a fresh scope: forget cached digests and let the next pass plan from scratch.
        self._initialized_db = store.db
        self._invalidate_revisions()
        self._planning_dirty = True
        self._budget_dirty = True
        self.state['idle'] = False
        # Persist the explicit, reversible mapping. `previous` is the exact in-memory share/track
        # state activation replaced, so a later restart or `encounter_rollback` can restore it.
        store.set(MODE_KEY, dict(enabled=True, mode=self.mode, config=config,
                                 constraints=self.constraints, encounter=self.encounter,
                                 reference=self.reference, thresholds=self.thresholds,
                                 previous=self.previous))
        self._persist(store)
        return dict(ok=True, sessionId=session_id, mode=self.mode)

    def rollback(self, store):
        """Disable the mode. Evidence and history are preserved; the previous config is restored."""
        if store is not None:
            if self.session_id is not None:
                try:
                    ledger.deactivate_session(store.db, self.session_id)
                except ValueError:
                    pass
            mapping = store.get(MODE_KEY) or {}
            if not isinstance(mapping, dict):
                mapping = {}
            mapping['enabled'] = False
            store.set(MODE_KEY, mapping)
        self.enabled = False
        self.activated = False
        self._planning_dirty = True
        self._budget_dirty = True
        self._persist(store)
        return dict(ok=True, restored=self.previous.get('mapping') or {})

    def set_question(self, store, value):
        """Freeze one explicit, finite boundary/operating-region question.

        The caller may name the axis by role+stat ("dps"/"dex"), which is translated into the
        canonical ``ownUnits.<index>.parameters.<id>`` path using the ACTUAL reference build's roles.
        The supplied tested value, minimum complete-pair sample and tolerance are honoured; the named
        reference, fixed and adjustable domains are frozen. A support question is frozen too, but it
        is never published as a filtered/compensated boundary claim.
        """
        value = value or {}
        kind = value.get('kind')
        if kind not in regions.KINDS:
            return dict(ok=False, error='question kind must be fixed-build, compensated or support')
        budget = value.get('budget')
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            return dict(ok=False, error='question budget must be a positive whole number of runs')
        reference_id, reference_scenario = self._question_reference(store, value)
        if reference_scenario is None:
            return dict(ok=False, error='question reference could not be resolved in this library')
        observed_reference = store.observed_scenario(reference_scenario, candidate=reference_id)
        field = value.get('field')
        if isinstance(field, str) and not field.startswith('ownUnits.') and '.' in field:
            role, stat = field.split('.', 1)
            field = self._canonical_field(reference_scenario, role, stat)
        if not field:
            role = value.get('role') or 'dps'
            stat = value.get('stat') or 'atk'
            field = self._canonical_field(reference_scenario, role, stat)
            if field is None:
                return dict(ok=False, error='could not translate role/stat into a canonical field')
        fixed_fields = dict(value.get('fixedFields') or {})
        tested = value.get('value')
        if tested is None:
            tested = fixed_fields.get(field)
        if isinstance(tested, bool) or not isinstance(tested, int):
            return dict(ok=False, error='question value must be a finite integer stat value')
        clamped = self._clamp_question_value(reference_scenario, field, tested)
        if clamped is not None and clamped != tested:
            # A tested value outside the legal build domain can never be built by any stream, so the
            # question would be silently unspendable. Clamp it to the domain and report the change.
            self._note('question value %d at %s clamped to the legal build domain value %d'
                       % (int(tested), field, int(clamped)))
            tested = int(clamped)
        fixed_fields[field] = tested
        if kind == 'support' and not field.endswith(('.parameters.10', '.parameters.11', '.parameters.14')):
            return dict(ok=False, error='Support questions require HP, MP or DEF.')
        if value.get('adjustableFields'):
            adjustable_fields = list(value['adjustableFields'])
        elif kind == 'compensated':
            atk_field = self._canonical_field(reference_scenario, 'dps', 'atk')
            if atk_field is None or atk_field == field:
                return dict(ok=False, error='a compensated question needs a distinct DPS ATK axis')
            adjustable_fields = [atk_field]
        else:
            # A fixed sensitivity adjusts nothing: the questioned field is held, not moved.
            adjustable_fields = []
        minimum_stat = value.get('minimum')
        if minimum_stat is not None and (type(minimum_stat) is not int or tested < minimum_stat):
            return dict(ok=False, error='The tested stat must satisfy the declared minimum.')
        minimum_pairs = value.get('minimumPairs', 16)
        if isinstance(minimum_pairs, bool) or not isinstance(minimum_pairs, int) or minimum_pairs < 2:
            return dict(ok=False, error='minimumPairs must be a whole number of pairs, at least 2')
        if minimum_pairs * 2 > budget:
            return dict(ok=False, error='question budget cannot buy the declared minimum pairs')
        try:
            actual_policy = self._freeze_policy(observed_reference or {})
            if value.get('policy') is not None and value['policy'] != actual_policy:
                return dict(ok=False, error='Configure the resource/Finish policy before freezing a question.')
            if self.state.get('policy') is None:
                self.state['policy'] = actual_policy
                self._compat = None
            elif actual_policy != self.state['policy']:
                return dict(ok=False, error='Question reference policy differs from this search session.')
            question = regions.question(
                kind=kind, reference_id=reference_id,
                encounter_revision=value.get('encounterRevision')
                or self._encounter_revision(observed_reference),
                mechanics_revision=value.get('mechanicsRevision') or self._mechanics_revision(),
                policy=actual_policy,
                constraints=self.constraints, field=field, fixed_fields=fixed_fields,
                adjustable_fields=adjustable_fields, tolerance=value.get('tolerance', 0.9),
                reliability=value.get('reliability'))
        except ValueError as exc:
            return dict(ok=False, error=str(exc))
        # `value` is the tested point, not part of the frozen domain: keep it beside the question so
        # the proposal generator can read it without perturbing the frozen question digest.
        question['value'] = tested
        question['minimumPairs'] = minimum_pairs
        question['budget'] = budget
        question['id'] = regions.digest({k: v for k, v in question.items() if k != 'id'})
        self.state['question'] = dict(question=question, budget=budget, spent=0,
                                      minimumPairs=minimum_pairs, referenceId=reference_id)
        self.state['unspendable'] = [x for x in self.state.get('unspendable', [])
                                      if x not in ('boundary', 'support')]
        # Freeze the reduced-model presentation for this reference/constraints once, at question time.
        self._presentation(reference_scenario, str(reference_id))
        # A frozen question is a planning input: re-plan even if the previous pass went idle.
        self.state['idle'] = False
        self._planning_dirty = True
        self._budget_dirty = True
        self._invalidate_revisions()
        self._persist(store)
        return dict(ok=True, question=question)

    def set_budget(self, store, value):
        """Explicit inactive, or resize the CURRENT session to ABSOLUTE totals.

        The UI sends requested absolute totals (not increments). The paused session is deactivated,
        its totals are updated in place preserving every already-reserved/completed charge, then it
        is reactivated. A resize below the reserved work is refused and the unchanged session is
        reactivated, so a rejected request never silently disables the run.
        """
        value = value or {}
        if value.get('enabled') is False:
            result = self.rollback(store)
            result['ok'] = True
            return result
        purposes = value.get('purposes')
        if not isinstance(purposes, dict) or not purposes:
            return dict(ok=False, error='a finite purposes mapping of absolute totals is required')
        config = dict(self.config)
        had_matrix = bool(config.get('budgets'))
        merged = dict(config.get('purposes') or {})
        for name, total in purposes.items():
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                return dict(ok=False, error=f'{name} budget must be a non-negative integer')
            merged[name] = total
        config['purposes'] = merged
        if not value.get('budgets'):
            # A purpose resize re-apportions the derived per-owner matrix in EVERY mode; an explicit
            # caller matrix is respected (and validated against the new totals, refusing a
            # double-spend). The matrix is RE-DERIVED (not dropped) so the owner-keyed session rows
            # keep matching: dropping them would fall back to owner='*' rows and refuse a resize
            # against already-reserved work.
            config.pop('budgets', None)
            if had_matrix:
                # Only re-derive when the session was actually owner-keyed. A session opened with a
                # session-wide ('*') config must keep that shape, or the owner-keyed re-derivation
                # would look like an attempt to erase every already-reserved row.
                derived = modes.derive_budget_matrix(config)
                config['budgets'] = {owner: dict(row)
                                     for owner, row in (derived.get('matrix') or {}).items()}
        modes.validate_config(config)
        db = store.db
        ledger.initialize(db)
        try:
            if self.session_id is None:
                session_id = ledger.configure_session(db, config)
            else:
                ledger.deactivate_session(db, self.session_id)
                ledger.configure_session(db, config, session_id=self.session_id)
                session_id = ledger.configure_session(db, config)
        except ValueError as exc:
            if self.session_id is not None:
                try:
                    ledger.configure_session(db, self.config)
                except ValueError:
                    pass
            return dict(ok=False, error=str(exc))
        self.config = config
        self.session_id = session_id
        self.state['unspendable'] = []
        self.state['idle'] = False
        self._initialized_db = db
        self._invalidate_revisions()
        self._planning_dirty = True
        self._budget_dirty = True
        mapping = store.get(MODE_KEY) or {}
        mapping['config'] = config
        store.set(MODE_KEY, mapping)
        self.state['sessionId'] = session_id
        self._refresh_budget_rows(db)
        self._persist(store)
        return dict(ok=True, sessionId=self.session_id, purposes=merged)

    # -- the runtime cycle ------------------------------------------------------------------------
    def _measure_stage(self, name, function, *args):
        began = time.perf_counter()
        try:
            return function(*args)
        finally:
            timings = self.state.setdefault('timings', {})
            timings[name] = timings.get(name, 0.0) + time.perf_counter() - began

    def _authorized_dispatch_count(self, db):
        '''Count existing frozen jobs that remain unreserved under this coordinator.'''
        if not self.enabled:
            return 0
        cohort = self._cohort()
        if not cohort:
            return len(self._pending_confirmation_jobs(db)) if self._confirmation_pending() else 0
        available = 0
        for current in cohort:
            if not self._hydrate_current(db, current):
                continue
            if (current.get('blocked')
                    and not self._recover_known_good_scheduler_block(current)):
                continue
            compatible, reason = self._current_compatible(current)
            if not compatible:
                self._block_current(reason, current)
                continue
            experiment_id = current.get('experimentId')
            if experiment_id is None:
                continue
            summary = self._experiment_state(db, experiment_id)
            if summary['complete'] or summary['remaining'] <= 0:
                continue
            reserved = self._reserved_jobs(db, experiment_id)
            candidate_id = str(current.get('candidateId'))
            for job in current.get('jobs') or []:
                cid = str(job.get('candidateId'))
                pair = job.get('seedPair') or []
                if len(pair) != 2:
                    continue
                pair = (int(pair[0]), int(pair[1]))
                scenario = (current.get('observedCandidate') if cid == candidate_id
                            else current.get('observedReference'))
                if scenario is None or (cid, pair[0], pair[1]) in reserved:
                    continue
                available += 1
                if available >= summary['remaining']:
                    break
            if available >= summary['remaining']:
                break
        return available

    def _has_compatible_frozen_work(self, db):
        """Whether this cohort contains a frozen plan that still passes every revision guard."""
        for current in self._cohort():
            if not self._hydrate_current(db, current):
                continue
            if current.get('blocked') and not self._recover_known_good_scheduler_block(current):
                continue
            compatible, _reason = self._current_compatible(current)
            if compatible:
                return True
        return False

    def dispatch_authorized_ready(self, store, *, invalidate=True):
        '''Run only the ordinary guarded dispatch for already-frozen coordinator jobs.'''
        if not self.enabled or self.evaluator is None:
            return 0
        db = store.db
        self._sync_db(db)
        self._ensure_schema(db)
        self._ensure_session(db)
        if not self._cohort() and not self._confirmation_pending():
            return 0
        if invalidate:
            self._invalidate_revisions()
        before = len(self.evaluator.pending)
        self._measure_stage('priorityDispatchSeconds', self._dispatch, db, store)
        return max(0, len(self.evaluator.pending) - before)

    def run_pass(self, store, *, running=True, harvested_entries=None,
                 recovery_snapshot=_RECOVERY_NOT_PROVIDED, return_report=True):
        """One bounded pass. Harvest always; dispatch only while `running`.

        `return_report=False` returns only the three progress fields needed by the campaign
        generation gate. Fleet callers can defer the full public snapshot until all owners have
        completed their pass, instead of deep-copying it once per owner and again for publication.

        Everything is driven by change, not by the clock. A pass only re-reads the (file-backed)
        mechanics/scope digests, rescans the outcome ledger, or rewrites the persisted runtime state
        when there is new evidence or planning work. An unchanged idle or paused pass therefore does
        none of those. The revision digests are re-derived at the start of every ACTIVE pass, so a
        mechanics/config change still fails closed at the next dispatch boundary rather than being
        masked by a process-lifetime cache.
        """
        if not self.enabled:
            return None
        if self.mode not in modes.MODES:
            self.enabled = False
            self._note('unknown search mode %r; refusing to run' % (self.mode,))
            return self._run_pass_result(return_report)
        if self.evaluator is not None:
            # Diagnostic metadata only (the opt-in trace's encounter id); never a scheduler input.
            try:
                self.evaluator.encounter_id = self.encounter
            except Exception:  # noqa: BLE001 - a diagnostic attribute must never break a pass
                pass
        began = time.monotonic()
        self._submission_started = 0
        self._portfolio_refreshed_this_pass = False
        db = store.db
        self._sync_db(db)
        self._ensure_schema(db)
        self._ensure_session(db)
        harvested = False
        if harvested_entries is not None:
            for entry in harvested_entries:
                self._absorb(db, entry)
                harvested = True
        elif self.evaluator is not None:
            for entry in self._measure_stage('harvestSeconds', self.evaluator.harvest, db):
                self._absorb(db, entry)
                harvested = True
        if harvested:
            # New canonical outcomes invalidate the evidence caches and can re-enable planning.
            self._planning_dirty = True
            self._budget_dirty = True
        admission = getattr(self.evaluator, 'admission_capacity', None)
        capacity = (admission() if callable(admission) else
                    max(0, self.workers - len(self.evaluator.pending))
                    if self.evaluator is not None else self.workers)
        if (running and not harvested and self.evaluator is not None and capacity <= 0):
            # Every authorised slot is still occupied. No selection, dispatch or durable state
            # can change until harvest. Revision checks still run before the next submission.
            return self._run_pass_result(return_report)
        if running:
            if self._has_work():
                # Re-derive the file-backed digests once for this active pass.
                self._invalidate_revisions()
                self._measure_stage('recoverySeconds', self._recover, db, store,
                                    recovery_snapshot)
                self._measure_stage('planningSeconds', self._advance, db, store)
                # An unavailable purpose ends this planning pass, not the encounter. Other
                # authorised purposes must still get their turn before the campaign advances.
                if (self.state.get('idle') and not self._cohort()
                        and not self._confirmation_pending() and not self._readiness_blocked
                        and any(left >= 2 and not self._task_unspendable(owner, purpose)
                                for (owner, purpose), left in self._task_remaining(db).items())):
                    self.state['idle'] = False
                self._measure_stage('dispatchSeconds', self._dispatch, db, store)
                self._planning_dirty = False
            elif not self.state.get('idle'):
                self.state['idle'] = True
        elif self._confirmation_pending():
            # Paused: settle a frozen confirmation's arithmetic on already-stored holdout rows only.
            self._advance_confirmation(db, store)
        # A harvested outcome can change the portfolio. Completing an experiment refreshes it
        # inside _finish_experiment before confirmation is frozen. Planning, reservations, idle
        # state, and holdout results do not alter development evidence; rescanning for those passes
        # only delays the next encounter in a serial campaign fleet.
        if harvested and not self._portfolio_refreshed_this_pass:
            self._measure_stage('portfolioSeconds', self._refresh_portfolio, db)
        if self._budget_dirty:
            self._refresh_budget_rows(db)
        self._measure_stage('checkpointSeconds', self._persist, store)
        self.state['timings']['lastPassSeconds'] = round(time.monotonic() - began, 3)
        return self._run_pass_result(return_report)

    def _run_pass_result(self, return_report):
        """Return a full public snapshot or the compact campaign rollover view."""
        if return_report:
            return self.report()
        return {
            'progress': {
                'idle': bool(self.state.get('idle')),
                'experiments': len(self.state.get('completedExperiments') or []),
                'retiredFrozenPlanCount': len(self.state.get('retiredFrozenPlans') or []),
            },
            # The rollover caller carries the previous purpose budget into the next generation.
            'budgets': {'session': deepcopy(self._last_budget_rows)},
        }

    def _ensure_session(self, db):
        if self.session_id is None and self.enabled:
            self.session_id = ledger.configure_session(db, self.config)
            _activate_session(ledger, db, self.session_id, self.config)
            self.state['sessionId'] = self.session_id
            self._budget_dirty = True

    # -- change detection / cache invalidation ----------------------------------------------------
    def inherit_evidence_caches(self, previous, db):
        """Reuse evidence caches across an exact, same-connection generation rollover."""
        if previous is None or previous._db_ref is not db or db.in_transaction:
            return False
        cached = previous._compatible_ids_cache
        if cached is None or not isinstance(cached, tuple) or len(cached) != 2:
            return False
        old_token, old_ids = cached
        if not isinstance(old_token, tuple) or len(old_token) != 7:
            return False
        try:
            current = (db.execute('PRAGMA data_version').fetchone()[0],
                       db.execute('SELECT MAX(id) FROM ea_experiment').fetchone()[0],
                       self._compatibility(), self._mechanics_revision(), self.revision,
                       self.encounter, self._encounter_revision_scope)
        except Exception:  # noqa: BLE001 - fail closed on uncertain cache scope
            return False
        if (old_token[0] != current[0] or old_token[2:] != current[2:]
                or int(old_token[1] or 0) > int(current[1] or 0)):
            return False
        ids = set(old_ids)
        self._db_ref = db
        self._initialized_db = db
        self._intent_cache = {key: value for key, value in previous._intent_cache.items()
                              if key in ids}
        # Keep the old high-water so the next read validates newly appended experiments.
        self._compatible_ids_cache = (old_token, ids)
        self._outcomes_cache = {key: value for key, value in previous._outcomes_cache.items()
                                if key in ids}
        return True

    def _sync_db(self, db):
        """Reset every per-database cache when the loop hands the coordinator a new connection."""
        if self._db_ref is db:
            return
        self._db_ref = db
        self._initialized_db = None
        self._intent_cache = {}
        self._confirmation_projection_cache = None
        self._compatible_ids_cache = None
        self._outcomes_cache = {}
        self._compat = None
        self._mech = None
        self._planning_dirty = True
        self._budget_dirty = True
        self._last_budget_rows = {}
        # A new library object invalidates the historical seed snapshot and the prior caches.
        self._freshness = None
        self._freshness_key = None
        self._freshness_report = None
        self._freshness_blocked = None
        self._readiness_blocked = None
        self._legacy_cache = {}
        self._legacy_read_cache = {}

    def _ensure_schema(self, db):
        """Create the additive ledger schema once per database object, not once per pass."""
        if self._initialized_db is not db:
            was_open = bool(getattr(db, 'in_transaction', False))
            ledger.initialize(db)
            # `initialize` uses raw SQLite metadata writes, which can leave an implicit transaction
            # open. Commit only when this known setup write began outside a caller transaction.
            if not was_open and getattr(db, 'in_transaction', False):
                db.commit()
                if getattr(db, 'in_transaction', False):
                    raise RuntimeError('SQLite schema transaction remained open after commit')
            self._initialized_db = db

    def _invalidate_revisions(self):
        """Force the next digest read to observe current files/scope; call only at a real boundary."""
        self._mech = None
        self._compat = None

    def _confirmation_pending(self):
        confirmation = self.state.get('confirmation')
        return bool(confirmation and confirmation.get('frozen') and not confirmation.get('ready'))

    # -- bounded cohort ---------------------------------------------------------------------------
    def _cohort(self):
        """The active immutable development-plan members, in their frozen order.

        A legacy single-``current`` state is exposed as a one-member cohort, so every dispatch,
        advance and recovery path sees exactly one shape. Writing a multi-member cohort always also
        sets ``current`` to its first member for the read-only views.
        """
        cohort = self.state.get('cohort')
        if isinstance(cohort, list) and cohort:
            return cohort
        current = self.state.get('current')
        return [current] if current is not None else []

    def _set_cohort(self, members):
        """Store the ordered cohort and keep ``current`` as its first member (or None when drained)."""
        members = list(members)
        self.state['cohort'] = members
        self.state['current'] = members[0] if members else None
        return members

    def _cohort_planned(self, purpose=None):
        """Battles already frozen in this cohort but not yet reserved in the ledger."""
        total = 0
        for member in self._cohort():
            if purpose is None or member.get('purpose') == purpose:
                total += int(member.get('planned') or 0)
        return total

    def _has_work(self):
        """Whether this running pass could plan, resume or dispatch anything, without touching SQLite."""
        if self._cohort() or self._confirmation_pending():
            return True
        if self._planning_dirty:
            return True
        return not self.state.get('idle')

    # -- planning ---------------------------------------------------------------------------------
    def _budget_remaining(self, db):
        if self.session_id is None:
            return {}
        remaining = {}
        for row in ledger.session_status(db, self.session_id)['budgets']:
            remaining[row['purpose']] = remaining.get(row['purpose'], 0) + max(
                0, row['total'] - row['reserved'])
        return remaining

    def _task_remaining(self, db):
        """Per (owner, purpose) remaining battles, keyed by the ledger's owner budget row."""
        if self.session_id is None:
            return {}
        remaining = {}
        for row in ledger.session_status(db, self.session_id)['budgets']:
            key = (row['owner'], row['purpose'])
            remaining[key] = remaining.get(key, 0) + max(0, row['total'] - row['reserved'])
        return remaining

    def _remaining(self, db, owner, purpose):
        """Remaining budget for one (owner, purpose) under the current mode."""
        if self.mode == 'community-first':
            return self._budget_remaining(db).get(purpose, 0)
        return self._task_remaining(db).get((owner, purpose), 0)

    def _next_purpose(self, db):
        remaining = self._budget_remaining(db)
        unspendable = set(self.state.get('unspendable') or [])
        order = ('improvement', 'boundary', 'support', 'comparison', 'exploration')
        offset = int(self.state.get('purposeRotation') or 0)
        for step in range(len(order)):
            purpose = order[(offset + step) % len(order)]
            left = remaining.get(purpose, 0)
            if left <= 0 or purpose in unspendable:
                continue
            if purpose == 'comparison' and any(
                    remaining.get(other, 0) >= 2 and other not in unspendable
                    for other in order if other != 'comparison'):
                continue
            if left < 2:
                # A single remaining battle cannot buy a complete pair. It stays UNSPENT with a
                # reason rather than being paired against a filler second seed.
                self._note('%s budget left unspent: one remaining battle cannot buy a complete '
                           'reference-matched pair' % purpose)
                self._mark_unspendable(purpose)
                continue
            self.state['purposeRotation'] = (offset + step + 1) % len(order)
            return purpose
        return None

    def _mark_unspendable(self, purpose, owner=None):
        """A (owner, purpose) with no authorised useful work stays UNSPENT; never refilled.

        Community-first keeps the historical purpose-only marker. All-strategy records the exact
        owner too, so one owner's dried-up purpose never disables another owner's identical purpose.
        """
        if self.mode != 'community-first' and owner is not None:
            self.state.setdefault('unspendableTasks', [])
            if [owner, purpose] not in self.state['unspendableTasks']:
                self.state['unspendableTasks'].append([owner, purpose])
        else:
            self.state.setdefault('unspendable', [])
            if purpose not in self.state['unspendable']:
                self.state['unspendable'].append(purpose)
        self._note(f'{purpose} budget left unspent: no authorised useful work')

    def _task_unspendable(self, owner, purpose):
        if self.mode == 'community-first':
            return purpose in (self.state.get('unspendable') or [])
        return [owner, purpose] in (self.state.get('unspendableTasks') or [])

    def _owner_useful(self, owner, purpose, remaining):
        """Whether one owner's purpose can still buy a complete reference-matched pair."""
        if self._task_unspendable(owner, purpose):
            return False
        return remaining.get((owner, purpose), 0) >= 2

    def _next_task(self, db):
        """Choose the next (owner, purpose) with useful work; community-first returns (None, purpose).

        All-strategy rotates FAIRLY across the owners that still have useful work, and each owner
        spends its own finite (owner, purpose) budget. Comparison is the final act and is only chosen
        once no other owner/purpose can buy a complete pair, so one nomination holds and unused
        purpose budgets stay unspent. A lone leftover battle is labelled and never paired with filler.
        """
        if self.mode == 'community-first':
            purpose = self._next_purpose(db)
            return (None, purpose) if purpose else (None, None)
        remaining = self._task_remaining(db)
        order = ('improvement', 'boundary', 'support', 'comparison', 'exploration')
        owners = [owner for owner in modes.eligible_streams(self.config)]
        for owner in owners:
            if any(self._owner_useful(owner, purpose, remaining)
                   for purpose in order if purpose != 'comparison'):
                continue
            for purpose in order:
                if (not self._task_unspendable(owner, purpose)
                        and remaining.get((owner, purpose), 0) == 1):
                    self._note('%s budget left unspent: one remaining battle cannot buy a complete '
                               'reference-matched pair' % purpose)
                    self._mark_unspendable(purpose, owner)
        non_comparison = {owner: [purpose for purpose in order
                                  if purpose != 'comparison'
                                  and self._owner_useful(owner, purpose, remaining)]
                          for owner in owners}
        any_non_comparison = any(non_comparison.values())
        live = []
        for owner in owners:
            if any_non_comparison:
                if non_comparison[owner]:
                    live.append(owner)
            elif self._owner_useful(owner, 'comparison', remaining):
                live.append(owner)
        if not live:
            return (None, None)
        start = int(self.state.get('ownerRotation') or 0) % len(live)
        owner = live[start]
        self.state['ownerRotation'] = (start + 1) % len(live)
        choices = non_comparison[owner] if any_non_comparison else [
            purpose for purpose in order if self._owner_useful(owner, purpose, remaining)]
        if not choices:
            return (None, None)
        offset = int(self.state.get('purposeRotation') or 0) % len(choices)
        self.state['purposeRotation'] = (offset + 1) % len(choices)
        return (owner, choices[offset])

    def _reference_candidate(self, store, owner=None):
        """Choose a parent only from this encounter's exact compiled enemy revision and policy."""
        parent_id = parent = None

        # A fresh search can be opened before its encounter is selected. An explicit reference is
        # sufficient to bind that scope; otherwise the canonical fallback below binds it. Do this
        # before portfolio filtering so an unscoped reference is not silently discarded in favour
        # of the default encounter.
        if self.encounter is None and self.reference:
            supplied_reference = store.scenario(self.reference)
            if isinstance(supplied_reference, dict):
                try:
                    inferred_encounter = int(supplied_reference.get('encounterId'))
                except (TypeError, ValueError):
                    inferred_encounter = None
                if inferred_encounter is not None:
                    self.encounter = inferred_encounter
                    self._compat = None
                    self._compatible_ids_cache = None
                    self._encounter_revision_scope = None

        # Resolve the encounter-local compiled enemy revision before considering empirical
        # parents. A library portfolio is global persisted state and can contain rows from an
        # older study; candidate identity and a high mean do not make those rows compatible.
        if self.encounter is not None:
            _anchor, expected_encounter_revision = self._encounter_scope_anchor(store)
            if expected_encounter_revision is None:
                raise ValueError('compiled encounter revision unavailable for encounter %s'
                                 % self.encounter)
            self._encounter_revision_scope = expected_encounter_revision
        else:
            expected_encounter_revision = None

        # Community-first (owner None) keeps the historical unfiltered selection. In all-strategy a
        # stream - Community INCLUDED - reuses only its OWN owner/family rows; a Community candidate
        # is never built from another family's empirical parents. Stumble is a blind stream: it never
        # reads the empirical productive-parent portfolio at all.
        blind = owner == students.STUDENT_STUMBLING

        def mine(row):
            if owner is None:
                return True
            # Empirical parent reuse is owner-contextual: a named stream (Community included) never
            # borrows another owner's/family's measured parents. Rows carry an explicit owner under
            # all-strategy, so exact equality is the constraint the reviewer asked for.
            return row.get('owner') == owner

        # The fixed reference already owns every fifth parent slot below. Its public earning row
        # must not consume another region slot and crowd out the distinct candidate parents.
        measured = [row for row in self.state.get('portfolio') or []
                    if row.get('meanEarned') is not None and row.get('eligible', True) and mine(row)
                    and row.get('arm') != 'reference'
                    and self._portfolio_scenario_compatible(
                        store, row, expected_encounter_revision)]
        # Keep several productive regions alive, including the original reference every fifth
        # round. A promising uncertain parent receives more paired observations while its
        # descendants are tested; ranking alone never grants an unlimited sample bank.
        measured.sort(key=lambda row: (-row['meanEarned'], row['candidateId']))
        if measured and not blind and int(self.state.get('roundIndex') or 0) % 5:
            regions_seen = set()
            productive = []
            for row in measured:
                features = self.state.get('features', {}).get(row['candidateId'], {})
                region = tuple(features.get(k) for k in ('dps_interval', 'dps_crit_rate', 'dps_dex'))
                if region not in regions_seen:
                    productive.append(row)
                    regions_seen.add(region)
                if len(productive) >= 4:
                    break
            chosen = productive[int(self.state.get('roundIndex') or 0) % len(productive)]
            parent_id = chosen['candidateId']
            parent = store.scenario(parent_id)
        if parent is None and self.reference:
            candidate = store.scenario(self.reference)
            # A stored reference from ANOTHER encounter is incompatible: admit it only when it
            # belongs to this study, otherwise a foreign baseline would cross encounters.
            if self._scenario_matches_encounter(candidate) and (
                    expected_encounter_revision is None or
                    self._encounter_revision(candidate) == expected_encounter_revision):
                parent, parent_id = candidate, self.reference
        if parent is None and self.encounter is not None:
            parent_id, parent = students.community_parent(store, self.encounter)
        if parent is None and not blind:
            measured = [row for row in self.state.get('portfolio') or []
                        if row.get('meanEarned') is not None and mine(row)
                        and self._portfolio_scenario_compatible(
                            store, row, expected_encounter_revision)]
            if measured:
                best = max(measured, key=lambda row: (row['meanEarned'], row['candidateId']))
                parent = store.scenario(best['candidateId'])
                parent_id = best['candidateId']
        if parent is None:
            if self.encounter is None:
                from strategy_optimizer_adapter import default_scenario
                parent = default_scenario()
            else:
                parent, _revision = self._encounter_scope_anchor(store)
                if parent is None:
                    raise ValueError('no canonical baseline exists for encounter %s' % self.encounter)
        if self.encounter is None:
            self.encounter = int(parent.get('encounterId'))
            self._compat = None
            self._compatible_ids_cache = None
            _anchor, inferred_revision = self._encounter_scope_anchor(store)
            if inferred_revision is None:
                raise ValueError('compiled encounter revision unavailable for inferred encounter %s'
                                 % self.encounter)
            if self._encounter_revision(parent) != inferred_revision:
                raise ValueError('refusing inferred parent with a different compiled encounter revision')
            expected_encounter_revision = inferred_revision
            self._encounter_revision_scope = inferred_revision
        if not self._scenario_matches_encounter(parent):
            raise ValueError('refusing foreign-encounter parent before freezing policy')
        if (expected_encounter_revision is not None and
                self._encounter_revision(parent) != expected_encounter_revision):
            raise ValueError('refusing parent with a different compiled encounter revision')
        # Compare the worker-visible policy, not only the raw library row. The observer may add
        # `mpWatchUnits` (which is intentionally excluded from candidate identity) before dispatch;
        # a question freezes that same observed projection. Comparing the raw row here would reject
        # an otherwise identical supplied parent solely because this optional observation field is
        # absent from storage. All declared consumable/Finish settings remain exact-match checked.
        observed_parent = store.observed_scenario(parent, candidate=parent_id) or parent
        if (self.state.get('policy') is not None and
                self._freeze_policy(observed_parent) != self.state.get('policy')):
            raise ValueError('refusing parent whose policy differs from the frozen search policy')
        if self.state.get('policy') is None:
            self.state['policy'] = self._freeze_policy(observed_parent)
            self._compat = None
        return parent_id, parent

    def _encounter_scope_anchor(self, store):
        """Resolve the encounter's exact reference revision, using a catalog baseline if needed."""
        if self.encounter is None:
            return None, None
        from strategy_optimizer_adapter import default_scenario
        from strategy_optimizer import baseline_scenarios

        anchor = (store.scenario(self.reference)
                  if store is not None and self.reference is not None else None)
        if not self._scenario_matches_encounter(anchor) and store is not None:
            _anchor_id, anchor = students.community_parent(store, self.encounter)
        if not self._scenario_matches_encounter(anchor):
            baselines = baseline_scenarios(default_scenario())
            anchor = next((scenario for scenario, _label in baselines
                           if scenario.get('encounterId') == self.encounter), None)
        if anchor is None:
            return None, None
        return anchor, self._encounter_revision(anchor)

    def _scenario_matches_encounter(self, scenario):
        return (isinstance(scenario, dict) and self.encounter is not None
                and scenario.get('encounterId') == self.encounter)

    def _portfolio_scenario_compatible(self, store, row, expected_encounter_revision):
        scenario = store.scenario(row.get('candidateId'))
        if not self._scenario_matches_encounter(scenario):
            return False
        actual_revision = self._encounter_revision(scenario)
        if actual_revision is None:
            return False
        if expected_encounter_revision is not None and actual_revision != expected_encounter_revision:
            return False
        observed = store.observed_scenario(scenario, candidate=row.get('candidateId')) or scenario
        if self._freeze_policy(observed) != self.state.get('policy'):
            return False
        return True

    def _ensure_reference_candidate(self, store, parent_id, parent_scenario):
        """A stored candidate id (a string) for the reference arm of a matched pair."""
        if parent_id is not None and store.scenario(parent_id) is not None:
            return str(parent_id)
        from strategy_optimizer_adapter import stats
        return str(store.add(parent_scenario, 'Community reference', 'supplied',
                             stats(parent_scenario)))

    def _question_reference(self, store, value):
        """Resolve the frozen question's reference candidate and its raw stored scenario."""
        reference_id = value.get('referenceId') or self.reference
        if reference_id is not None:
            scenario = store.scenario(reference_id)
            if scenario is not None and (self.encounter is None
                                         or scenario.get('encounterId') == self.encounter):
                return str(reference_id), scenario
        parent_id, parent = self._reference_candidate(store)
        reference_id = self._ensure_reference_candidate(store, parent_id, parent)
        return reference_id, store.scenario(reference_id)

    def _canonical_field(self, scenario, role, stat):
        """Translate a named role + stat into the canonical ``ownUnits.<i>.parameters.<id>`` path."""
        parameter_id = STAT_PARAMETER_IDS.get(str(stat).lower())
        if parameter_id is None:
            return None
        try:
            normalized = mechanics.normalize_scenario(scenario)
            roles = proposals_service._role_indices(normalized)
        except Exception:  # noqa: BLE001 - an unresolved role is reported, never guessed
            return None
        index = roles.get(str(role).lower())
        if index is None:
            return None
        return 'ownUnits.%d.parameters.%d' % (int(index), parameter_id)

    def _clamp_question_value(self, scenario, field, value):
        """Clamp a question's tested value into the legal build domain, or None if unresolvable."""
        try:
            import strategy_build_domain as build_domain
            normalized = mechanics.normalize_scenario(scenario)
            prepared = mechanics.prepared_setup(normalized)
            constraints = self.constraints or proposals_service.SYNTHETIC_CONSTRAINTS
            table, _context, _provenance = proposals_service._domain_table(prepared, constraints)
            parsed, _reason = build_domain._parse_field(field)
            if not parsed:
                return None
            index, parameter_id = parsed
            return proposals_service._clamp(table, index, parameter_id, int(value))
        except Exception:  # noqa: BLE001 - an unresolved domain is reported, never guessed
            return None

    def _advance(self, db, store):
        # Planning-stage wall time includes several independent operations, notably portfolio and
        # adviser refreshes when an experiment finishes. Preserve a small last-call breakdown so a
        # long coordinator pass can be attributed without logs or per-item timing histories.
        started = time.perf_counter()
        stages = dict(retireIncompatible=0.0, hydrate=0.0, experimentState=0.0,
                      finishExperiment=0.0, confirmation=0.0, planRound=0.0)
        finish_details = dict(outcomes=0.0, aggregate=0.0, pairedEvidence=0.0,
                              portfolioWrite=0.0, mechanismArchive=0.0,
                              boundaryAssessment=0.0, cohortRemoval=0.0,
                              portfolioRefresh=0.0, adviserFit=0.0, confirmation=0.0)

        def measure(name, function, *args, **kwargs):
            began = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                stages[name] = stages.get(name, 0.0) + time.perf_counter() - began

        cohort_size = len(self._cohort())
        finished_count = 0
        try:
            # A frozen plan is immutable work. If its semantic engine revision is no longer accepted,
            # detach it from the live cohort without changing its intent, reservations or results. The
            # campaign can then plan fresh work under the current revision; the old ledger evidence stays
            # available for audit and remains excluded by the existing compatibility filter.
            measure('retireIncompatible', self._retire_incompatible_frozen_plans, db)
            # Finish EVERY completed cohort member exactly once, in the frozen order, before planning the
            # next boundary. A member that is not yet complete keeps the whole cohort open, so no new
            # selection is made and no holdout is frozen while any development job is still outstanding.
            for current in list(self._cohort()):
                measure('hydrate', self._hydrate_current, db, current)
                if current.get('blocked'):
                    continue
                summary = measure('experimentState', self._experiment_state, db,
                                  current['experimentId'])
                if summary['complete']:
                    measure('finishExperiment', self._finish_experiment, db, store,
                            current, summary, stage_totals=finish_details)
                    finished_count += 1
            # Drop anything that finished (or was already recorded by a restart) from the cohort.
            done = set(self.state.get('completedExperiments') or [])
            self._set_cohort([member for member in self._cohort()
                              if member.get('experimentId') not in done])
            if self._cohort():
                return
            if self.state.get('confirmation') and not self.state['confirmation'].get('ready'):
                measure('confirmation', self._advance_confirmation, db, store)
                return
            measure('planRound', self._plan_round, db, store)
        finally:
            total = time.perf_counter() - started
            measured = sum(stages.values())
            timings = self.state.setdefault('timings', {})
            timings['lastAdvanceStagesSeconds'] = {
                **{name: round(seconds, 4) for name, seconds in stages.items()},
                'other': round(max(0.0, total - measured), 4),
                'total': round(total, 4),
                'cohortMembers': cohort_size,
                'finishedExperiments': finished_count,
            }
            measured_finish = sum(finish_details.values())
            finish_details['other'] = max(0.0, stages['finishExperiment'] - measured_finish)
            finish_details['total'] = stages['finishExperiment']
            finish_details['finishedExperiments'] = finished_count
            timings['lastFinishStagesSeconds'] = {
                name: (round(value, 4) if isinstance(value, (int, float)) else value)
                for name, value in finish_details.items()
            }

    def _retire_incompatible_frozen_plans(self, db):
        """Retire stale engine-revision plans after their already-submitted jobs have drained.

        This is a scheduler-state transition only: the immutable ledger intent, charged budget,
        reservations and samples are never edited or restamped. Incompatible evidence remains
        excluded by ``_compatible_experiment_ids``. Waiting for evaluator jobs to leave ``pending``
        lets already-submitted work finish and be durably harvested before its coordinator is
        detached. On restart there is no live evaluator work, so stale plans can be retired directly.
        """
        members = list(self._cohort())
        if not members:
            return 0
        keep = []
        retired = []
        for current in members:
            if not self._hydrate_current(db, current):
                keep.append(current)
                continue
            if current.get('blocked') and self._recover_known_good_scheduler_block(current):
                keep.append(current)
                continue
            compatible, reason = self._current_compatible(current)
            if compatible or reason != 'loaded engine revision differs from the frozen plan':
                keep.append(current)
                continue
            experiment_id = current.get('experimentId')
            if experiment_id is not None and self.evaluator is not None:
                pending = getattr(self.evaluator, 'pending', {}) or {}
                has_inflight = any(
                    not (job or {}).get('holdout') and isinstance(key, tuple) and key
                    and key[0] == experiment_id
                    for key, job in pending.items())
                if has_inflight:
                    keep.append(current)
                    continue
            retired.append(dict(
                experimentId=experiment_id,
                engineRevision=current.get('engineRevision'),
                currentEngineRevision=self.revision,
                reason=reason,
                retiredAt=time.time()))

        if not retired:
            return 0

        self._set_cohort(keep)
        history = self.state.setdefault('retiredFrozenPlans', [])
        known = {row.get('experimentId') for row in history if isinstance(row, dict)}
        added = 0
        for row in retired:
            key = row.get('experimentId')
            if key is not None and key in known:
                continue
            history.append(row)
            known.add(key)
            added += 1
            self._note('retired frozen plan %s after rechecking its engine revision; ledger intent '
                       'and measurements were preserved' % (key if key is not None else 'unknown'))
        if len(history) > 100:
            del history[:-100]
        if added:
            self.state['idle'] = False
            self._planning_dirty = True
        return added

    def _plan_round(self, db, store):
        self.state['idle'] = False
        if self._planner_pending is not None:
            # One proposal group is still outstanding for this encounter. Do not decide or submit a
            # second one; the harvested result is admitted by `accept_planning_result`.
            return
        owner, purpose = self._next_task(db)
        if purpose is None:
            self._maybe_freeze_confirmation(db, store)
            if self._readiness_blocked:
                self._note('purpose budgets left unspent: seed-history readiness is blocked (%s)'
                           % self._readiness_blocked.get('reason'))
            else:
                self._note('all finite purpose budgets are spent')
            self.state['idle'] = True
            return
        if purpose == 'comparison':
            self._maybe_freeze_confirmation(db, store)
            if not self.state.get('confirmation'):
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
            return
        question = None
        active_question = (self.state.get('question') or {}).get('question')
        if purpose == 'boundary' or (purpose == 'support' and active_question
                                    and active_question.get('kind') == 'support'):
            frozen = self.state.get('question') or {}
            question = frozen.get('question')
            if purpose == 'boundary' and question and question.get('kind') == 'support':
                self._mark_unspendable(purpose, owner)
                return
            if question is None:
                # A boundary pass with no frozen question would be filler: leave the budget unspent.
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
                return
            pool_purpose = BOUNDARY_POOL_PURPOSES.get(question.get('kind'))
            if pool_purpose is None:
                # A support-kind question is a support probe, not a fixed/compensated boundary claim.
                self._note('boundary budget left unspent: the frozen question is not a '
                           'fixed-build or compensated boundary question')
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
                return
            parent_id, parent = question['referenceId'], store.scenario(question['referenceId'])
            if parent is None:
                self._note('boundary budget left unspent: the frozen reference is not in this library')
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
                return
            # The OVERALL question stays frozen; each experiment tests ONE untested child point it
            # generates. `boundary_points` puts the exact requested value first, never re-emits an
            # already-measured value, and emits both bracket directions from discrete mechanical
            # landmarks - no ranked 0/retest filler and no assumed continuous monotone range.
            tested_values = self._question_tested_values(question)
            points = decisions.boundary_points(question, parent, constraints=self.constraints,
                                               tested_points=tested_values, maximum=self.maximum)
            if not points:
                self._note('boundary budget left unspent: no untested child point remains for the '
                           'frozen question')
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
                return
            question = points[0]
            self._active_child = question
        else:
            if purpose == 'support':
                # A support-boundary study must be authorised by a FROZEN support question. Without one
                # the budget buys no questionless landmark filler: it stays unspent with a reason.
                self._note('support budget left unspent: no frozen support question authorises a '
                           'support boundary study')
                self._mark_unspendable(purpose, owner)
                self.state['idle'] = True
                return
            parent_id, parent = self._reference_candidate(store, owner=owner)
            pool_purpose = {'support': 'support'}.get(purpose, 'improvement')
            self._active_child = None
        # The round decision is complete and captured in `plan`, so the pool build may proceed on the
        # shared asynchronous planning host WITHOUT blocking the pass, or inline on the unchanged
        # synchronous path. Both routes converge on `_realize_pool`.
        plan = dict(owner=owner, purpose=purpose, pool_purpose=pool_purpose, question=question,
                    parent_id=parent_id, parent=parent,
                    round_index=int(self.state.get('roundIndex') or 0))
        if self._planning_enabled():
            # The shared host owns the planner: submit exactly one group and return. A rejected or
            # errored submission is reported and retried on a later pass; it NEVER falls back to the
            # blocking serial build, so a slow or failed planner can never stall dispatch.
            self._defer_round(plan, db)
            return
        pool = self._measure_proposal(plan, db)
        self._realize_pool(plan, pool, db, store)

    def _measure_proposal(self, plan, db):
        """Serial proposal build with the unchanged ``proposalSeconds`` accounting."""
        proposal_started = time.perf_counter()
        try:
            return self._propose(plan['owner'], plan['parent'], plan['pool_purpose'],
                                 plan['question'], db=db, parent_id=plan['parent_id'])
        finally:
            timings = self.state.setdefault('timings', {})
            timings['proposalSeconds'] = timings.get('proposalSeconds', 0.0) + (
                time.perf_counter() - proposal_started)

    def _realize_pool(self, plan, pool, db, store):
        """Rank, choose, extend or freeze the cohort AFTER a proposal pool exists.

        The synchronous planning path and `accept_planning_result` both call this, so a harvested
        asynchronous group obeys EXACTLY the same ranking, extension, cohort and finite-budget rules.
        """
        owner, purpose = plan['owner'], plan['purpose']
        question, parent_id, parent = plan['question'], plan['parent_id'], plan['parent']
        self._active_child = question
        source_bundle = (self._source_prior_bundle(db, parent)
                         if self._source_prior_enabled(owner, purpose) else {})
        self._tag_source_transfer(pool, source_bundle)
        source_proposals = list(source_bundle.get('proposals') or ())
        pool = list(pool or ()) + source_proposals
        if not pool:
            self._mark_unspendable(purpose, owner)
            self.state['idle'] = True
            return
        # Blind/discovery streams are never reordered or vetoed by the empirical (Community) adviser.
        # Their proposals keep the generator's own deterministic order, so a learned preference can
        # never suppress a blind mutation or a fresh discovery root. Every other owner is ranked on
        # its own family evidence.
        if owner in (students.STUDENT_STUMBLING, students.STREAM_DISCOVERY):
            ranked = list(pool)
        else:
            ranked = self._rank(pool, owner)
        chosen = (ranked[0] if ranked else None) if question is not None else self._first_unmeasured(ranked)
        # Purposeful interleaving: an UNCERTAIN, upside-plausible challenger may buy one more finite
        # paired stage even while distinct children remain, but only when enough coverage has advanced
        # since the previous extension. A large open pool can therefore never starve uncertain
        # comparisons forever, and an extension can never crowd out diverse coverage. A comparison
        # that is already determinate (known zero SE, or a resolved interval clear of 0) is left for
        # independent confirmation instead of being re-sampled.
        if purpose == 'improvement' and chosen is not None:
            extension = self._choose_extension(db, owner, purpose)
            if (extension is not None and extension.get('undecided')
                    and self._extension_due()):
                proposal = self._extension_proposal(store, extension, owner)
                if proposal is not None:
                    self._start_extension(db, store, proposal, purpose, extension, owner)
                    return
        batchable = (purpose in COHORT_PURPOSES and question is None
                     and self.mode == 'community-first')
        if (batchable and self._source_prior_enabled(owner, purpose) and source_proposals):
            # Admit the bounded identity-verified source strategies once for fresh measurement.
            # They receive no score from the historical library; after this first pass ordinary
            # fitted Development evidence controls ranking and parent selection.
            bound = cohort_bound(self.workers)
            priors = [proposal for proposal in ranked if proposal.get('sourcePrior')]
            ordinary = [proposal for proposal in ranked if not proposal.get('sourcePrior')]
            targets = self._cohort_targets(priors)[:bound]
            if len(targets) < bound:
                targets.extend(self._cohort_targets(ordinary)[:bound - len(targets)])
        else:
            targets = self._cohort_targets(ranked) if batchable else [chosen]
        targets = [proposal for proposal in targets if proposal is not None]
        if not targets:
            # Every distinct child in this parent's pool is already measured. Only now may the finite
            # improvement budget buy extra KNOWLEDGE on an under-sampled, reference-compatible
            # challenger (same reference, NEW pairs): a genuinely new development observation always
            # takes priority, so a knowledge extension can never starve distinct coverage.
            if purpose == 'improvement':
                extension = self._choose_extension(db, owner, purpose)
                if extension is not None:
                    proposal = self._extension_proposal(store, extension, owner)
                    if proposal is not None:
                        self._start_extension(db, store, proposal, purpose, extension, owner)
                        return
            self._mark_unspendable(purpose, owner)
            self.state['idle'] = True
            return
        self._start_cohort(db, store, targets, purpose, parent_id, parent, owner)

    def _cohort_targets(self, ranked):
        """Up to ``cohort_bound(workers)`` DISTINCT, not-yet-measured children from ONE ranked pool.

        The pool is built once; every frozen challenger keeps its own immutable paired plan and ledger
        budget, so the batch only amortises pool construction and worker utilisation. A child already in
        history, the portfolio or this cohort is skipped, so no candidate is measured twice.
        """
        seen = {row.get('candidateId') for row in self.state.get('history') or []}
        seen |= {row.get('candidateId') for row in self.state.get('portfolio') or []}
        seen |= {member.get('candidateId') for member in self._cohort()}
        bound = cohort_bound(self.workers)
        targets = []
        for proposal in ranked:
            identity = proposal.get('childIdentity')
            if not identity or identity in seen:
                continue
            seen.add(identity)
            targets.append(proposal)
            if len(targets) >= bound:
                break
        return targets

    def _start_cohort(self, db, store, targets, purpose, parent_id, parent, owner):
        """Freeze a bounded prospective cohort from one pool, within the remaining purpose budget.

        Each member is an independent immutable paired experiment with its own fresh ordered pairs; the
        remaining budget is reduced by each member's PLANNED (not yet reserved) battles so the cohort as
        a whole never over-commits the finite purpose budget. Pairs already drawn for earlier members are
        passed forward so no two cohort members share an ordered pair. A shortfall leaves the remainder
        of the purpose budget unspent (or preserved for readiness) exactly as a single experiment would.
        """
        bound = cohort_bound(self.workers)
        remaining = self._remaining(db, owner, purpose) - self._cohort_planned(purpose)
        used_pairs = [list(job.get('seedPair') or []) for member in self._cohort()
                      for job in member.get('jobs') or []]
        for position, proposal in enumerate(targets[:bound]):
            if remaining < 2:
                break
            selection = dict(policy='bounded-cohort', purpose=purpose, bound=bound,
                             queuePolicy='three-waves-v1',
                             workers=self.workers, considered=len(targets), position=position + 1,
                             roundIndex=int(self.state.get('roundIndex') or 0))
            member = self._start_experiment(db, store, proposal, purpose, parent_id, parent, owner,
                                            budget_remaining=remaining,
                                            selection_batch=selection, reserved_pairs=used_pairs)
            if member is None:
                break
            remaining -= int(member.get('planned') or 0)
            used_pairs.extend(list(job.get('seedPair') or []) for job in member.get('jobs') or [])
        if not self._cohort():
            self.state['idle'] = True

    def _extension_due(self):
        """Whether enough distinct coverage has advanced since the last knowledge extension."""
        round_index = int(self.state.get('roundIndex') or 0)
        last = self.state.get('lastExtensionRound')
        if last is None:
            return round_index >= EXTENSION_INTERLEAVE_ROUNDS
        return round_index - int(last) >= EXTENSION_INTERLEAVE_ROUNDS

    def _start_extension(self, db, store, proposal, purpose, extension, owner):
        """Buy exactly ONE declared finite stage, then record the interleave boundary."""
        self.state['lastExtensionRound'] = int(self.state.get('roundIndex') or 0)
        reference_id = str(extension['referenceId'])
        self._start_experiment(db, store, proposal, purpose, reference_id,
                               store.scenario(reference_id), owner)

    def _propose(self, owner, parent, purpose, question, db=None, parent_id=None):
        """Candidate rows for one owner, through the REAL stream service.

        Community-first keeps the historical strict `strategy_joint_proposals.pool`. All-strategy
        routes EVERY owner (Community included) through `strategy_stream_proposals.proposal_pool`,
        which delegates Community to the unchanged strict pool and serves each other owner from its
        own registered generator under its own scope. A refusal yields an empty pool with a reason
        and NEVER falls back to another owner. The CHOSEN parent and its ledger are threaded through so
        the shared support reader can explain the parent's OWN persisted development telemetry (a
        parent intervention), never a future feature of some other candidate.
        """
        round_index = int(self.state.get('roundIndex') or 0)
        evidence = self._evidence(owner, parent=parent, parent_id=parent_id, db=db)
        source_bundle = (self._source_prior_bundle(db, parent)
                         if (self._source_prior_enabled(owner, purpose) and db is not None) else {})
        if source_bundle.get('transferScenario') is not None:
            evidence['sourceScenario'] = source_bundle['transferScenario']
        if self.mode == 'community-first':
            pool = proposals_service.pool(parent, constraints=self.constraints,
                                          evidence=evidence, round_index=round_index,
                                          maximum=self.maximum, purpose=purpose, question=question)
            self._tag_source_transfer(pool, source_bundle)
            return pool
        diagnostics = {}
        context = {'baseScenario': parent} if owner == students.STREAM_DISCOVERY else None
        return stream_proposals.proposal_pool(
            owner, parent, constraints=self.constraints, evidence=evidence,
            round_index=round_index, maximum=self.maximum, purpose=purpose, question=question,
            diagnostics=diagnostics,
            allocation=float((self.config.get('allocations') or {}).get(owner, 0.0)),
            config=self.config, context=context)

    def _source_prior_enabled(self, owner, purpose):
        """Student1 priors are only fed into a Community-first improvement task."""
        return (self.mode == 'community-first' and owner in (None, students.STUDENT_COMMUNITY)
                and purpose == 'improvement')

    def _source_prior_bundle(self, db, parent):
        """Bounded Student1 strategy-only portfolio, validated against this frozen build context."""
        if db is None or self.encounter is None or not isinstance(parent, dict):
            return {}
        policy = self.state.get('policy') or self._freeze_policy(parent)
        key = _digest(dict(encounter=self.encounter, parent=proposals_service.domain.identity(parent),
                           policy=policy, constraints=self.constraints,
                           engine=self.revision, mechanics=self._mechanics_revision(),
                           encounterRevision=self._encounter_revision(parent)))
        cache = getattr(self, '_source_prior_cache', None)
        if cache is None:
            cache = self._source_prior_cache = {}
        if key in cache:
            return cache[key]
        try:
            import strategy_source_priors as source_priors
            bundle = source_priors.prepare(
                db, encounter=self.encounter, parent=parent, policy=policy,
                constraints=self.constraints, encounter_revision=self._encounter_revision(parent),
                current_engine_revision=self.revision)
        except Exception as exc:  # noqa: BLE001 - optional seeds fail closed
            bundle = dict(proposals=[], transferScenario=None, transferBasis=None,
                          refusals=['source-prior admission failed closed: %s' % exc])
        for reason in bundle.get('refusals') or ():
            self._note('Student1 strategy prior refused: %s' % reason)
        if len(cache) >= 12:
            cache.clear()
        cache[key] = bundle
        return bundle

    @staticmethod
    def _tag_source_transfer(pool, source_bundle):
        basis = source_bundle.get('transferBasis') if source_bundle else None
        if not basis:
            return
        for proposal in pool or ():
            differences = proposal.get('expectedMechanicalDifferences') or {}
            if differences.get('proposalKind') == 'encounter-transfer':
                proposal['strategyBasis'] = dict(
                    basis, kind='student1-source-transfer', historicalOutcomesUsed=False)

    # -- asynchronous planning bridge -------------------------------------------------------------
    def _planning_enabled(self):
        """Whether a shared pool and the joint worker entry point are both available.

        Resolved by string so the optional API can land later without editing this module; when it is
        absent this returns False and the unchanged synchronous planning path runs.
        """
        if self.mode != 'community-first':
            return False
        host = self.planning_host
        if host is None or not callable(getattr(host, 'submit_planning', None)):
            return False
        return planning_task_function() is not None

    def _planner_context(self, plan):
        """The identity envelope a harvested result must still match to be admitted.

        It carries every compatibility dimension the acceptance gate checks: library, campaign,
        session, generation, focus, policy, encounter, mechanics and the parent build. The
        host-owned fields (campaign/generation/focus) come from the shared pool's host, so a result
        computed for a different campaign or focus is provably stale.
        """
        host_context = {}
        host = self.planning_host
        provider = getattr(host, 'planning_context', None) if host is not None else None
        if callable(provider):
            try:
                host_context = dict(provider(self) or {})
            except Exception:  # noqa: BLE001 - an absent host context is reported, never guessed
                host_context = {}
        study_scope = (self.config or {}).get('studyScope')
        return dict(
            library=str(self.path),
            runtimeKey=self.runtime_key,
            campaignScope=(study_scope.get('campaignScope')
                           if isinstance(study_scope, dict) else None),
            campaignId=host_context.get('campaignId'),
            generation=host_context.get('generation'),
            focus=list(host_context.get('focus') or []),
            sessionId=self.session_id,
            encounter=self.encounter,
            policy=_digest(self._freeze_policy(plan['parent'])),
            engineRevision=self.revision,
            mechanicsRevision=self._mechanics_revision(),
            compatibility=self._compatibility(),
            parentId=plan['parent_id'],
            parentIdentity=_digest(plan['parent']),
            owner=plan['owner'],
            purpose=plan['purpose'],
            roundIndex=plan['round_index'],
            maximum=self.maximum)

    def _planning_identity(self, plan):
        """Stable, plain-data identity of one frozen planning group (no live objects)."""
        return dict(library=str(self.path), runtimeKey=self.runtime_key, sessionId=self.session_id,
                    encounter=self.encounter, parentId=plan['parent_id'],
                    parentIdentity=_digest(plan['parent']), owner=plan['owner'],
                    purpose=plan['purpose'], poolPurpose=plan['pool_purpose'],
                    roundIndex=int(plan['round_index'] or 0), maximum=int(self.maximum))

    def _planning_scope_revision(self, plan):
        """Digest of the frozen search scope: library/session/mechanics/compatibility/parent."""
        return _digest(dict(library=str(self.path), runtimeKey=self.runtime_key,
                            sessionId=self.session_id, encounter=self.encounter,
                            mechanicsRevision=self._mechanics_revision(),
                            compatibility=self._compatibility(), parentId=plan['parent_id'],
                            parentIdentity=_digest(plan['parent']), owner=plan['owner']))

    def _build_planning_request(self, plan, db):
        """Build the immutable joint ``EncounterPlanningRequest`` for one frozen planning group.

        Every field is frozen, plain-serializable data: the EXACT parent scenario, constraints and
        evidence the synchronous ``propose`` path would hand to ``strategy_joint_proposals.pool``,
        plus a stable request id, the scope/policy/engine revision digests and an evidence digest.
        No store, database, coordinator, future or whole library can reach a worker. The immutable
        dataclass is preferred; a published factory is only a fallback. ``None`` means the joint
        worker API cannot build a valid request, which is reported rather than run serially.
        """
        evidence = self._evidence(plan['owner'], parent=plan['parent'],
                                  parent_id=plan['parent_id'], db=db)
        source_bundle = (self._source_prior_bundle(db, plan['parent'])
                         if self._source_prior_enabled(plan['owner'], plan['pool_purpose']) else {})
        if source_bundle.get('transferScenario') is not None:
            evidence['sourceScenario'] = source_bundle['transferScenario']
        policy = self._freeze_policy(plan['parent'])
        scope_revision = self._planning_scope_revision(plan)
        policy_revision = _digest(policy)
        engine_revision = self.revision if self.revision is not None else ''
        identity = self._planning_identity(plan)
        identity.update(scopeRevision=scope_revision, policyRevision=policy_revision,
                        engineRevision=engine_revision, evidenceRevision=_digest(evidence))
        fields = dict(requestId='encounter-planning:%s' % _digest(identity)[:32],
                      scopeRevision=scope_revision, policyRevision=policy_revision,
                      engineRevision=engine_revision, parentId=plan['parent_id'],
                      scenario=plan['parent'], evidence=evidence, constraints=self.constraints,
                      question=plan['question'], roundIndex=int(plan['round_index'] or 0),
                      purpose=plan['pool_purpose'], maximum=int(self.maximum))
        preparation_policy = (plan['question']['policy'] if plan['question'] is not None
                              else self.state.get('policy'))
        # Without a frozen observed reference policy, the canonical serial path must first
        # resolve MP observation. Do not remove that declaration by aligning to a raw policy.
        if preparation_policy or plan['question'] is not None:
            fields['admissionPreparation'] = dict(
                policy=preparation_policy, engineRevision=self.revision,
                mechanicsRevision=self._mechanics_revision())
        request_class = planning_request_class()
        if request_class is not None:
            return request_class(**fields)
        factory = planning_request_factory()
        if factory is None or factory is request_class:
            return None
        for attempt in (lambda: factory(**fields), lambda: factory(fields)):
            try:
                return attempt()
            except TypeError:
                continue
            except Exception:  # noqa: BLE001 - reported, never a silent serial fallback
                break
        return None

    def _defer_round(self, plan, db):
        """Submit ONE proposal group to the shared host without blocking; True when outstanding.

        A build or submission failure is REPORTED and the round is left for a later pass. It never
        falls back to the blocking synchronous build, so a rejected or errored planner task cannot
        stall dispatch, harvest or commands on the optimizer thread.
        """
        request_created_at = time.monotonic()
        try:
            request = self._build_planning_request(plan, db)
        except Exception as exc:  # noqa: BLE001 - reported, never a silent serial fallback
            self._note('async planning request build failed; round not planned: %s' % (exc,))
            self._planning_dirty = True
            return False
        if request is None:
            self._note('async planning request unavailable; round not planned')
            self._planning_dirty = True
            return False
        self._planner_request_created_at = request_created_at
        envelope = self._planner_context(plan)
        try:
            request_id = self.planning_host.submit_planning(self, request, envelope)
        except Exception as exc:  # noqa: BLE001 - reported, never a silent serial fallback
            self._note('async planning submission failed; round not planned: %s' % (exc,))
            self._planning_dirty = True
            return False
        if request_id is None:
            self._note('async planning submission was rejected by the host; round not planned')
            self._planning_dirty = True
            return False
        self._planner_pending = dict(requestId=request_id, plan=plan, envelope=envelope,
                                     request=request, submittedAt=time.monotonic(),
                                     requestCreatedAt=request_created_at)
        self._planner_metrics['submitted'] = int(self._planner_metrics.get('submitted') or 0) + 1
        self._planning_dirty = False
        return True

    def _planner_compatible(self, envelope, plan):
        """True only when the live identity still matches the submitted envelope exactly."""
        try:
            live = self._planner_context(plan)
        except Exception:  # noqa: BLE001 - an unreadable context is stale by definition
            return False
        return live == envelope

    def _planner_latency_note(self, submitted_at):
        """Observer-only submission-to-harvest latency, bounded to the last 64 samples."""
        if submitted_at is None:
            return
        latency = max(0.0, time.monotonic()-submitted_at)
        self._planner_metrics['latencySeconds'] = latency
        self._planner_latency.append(latency)
        if len(self._planner_latency) > 64:
            del self._planner_latency[:-64]

    def _planning_result_reason(self, request, result):
        """Why a harvested result must be refused, or ``None`` when it matches the frozen request.

        The worker echoes the request id, the request digest and every revision it was frozen with,
        so a result that was computed for another request, another scope or another revision cannot
        be admitted even if it arrives under the right host-returned id.
        """
        if request is None:
            return 'the submitted request is no longer available'
        if not isinstance(result, dict):
            return 'the worker result is not a plain compute envelope'
        if result.get('requestId') != request.requestId:
            return 'result request id does not match the frozen request'
        request_version = getattr(proposals_service, 'REQUEST_VERSION', None)
        if request_version is not None and result.get('requestVersion') != request_version:
            return 'result request version %r does not match %r' % (
                result.get('requestVersion'), request_version)
        try:
            expected = request.digest()
        except Exception as exc:  # noqa: BLE001 - an unresolvable digest cannot be trusted
            return 'the frozen request digest could not be derived: %s' % (exc,)
        if result.get('requestDigest') != expected:
            return 'result request digest does not match the frozen request'
        for name in ('scopeRevision', 'policyRevision', 'engineRevision'):
            if result.get(name) != getattr(request, name, None):
                return 'result %s does not match the frozen request' % (name,)
        if not isinstance(result.get('proposals'), list):
            return 'result envelope carries no proposal list'
        return None

    def accept_planning_result(self, record, store):
        """Admit a harvested planning group. Runs on the optimizer thread and only there.

        The host-returned request id is checked first, then the frozen request digest and the full
        live compatibility envelope, and only then are the proposals realized. Duplicate or stale
        delivery is refused idempotently: the pending group is cleared on the first matching
        delivery, so a second copy finds nothing to admit. Pending state is per coordinator, so a
        completed result for one encounter can never clear or stale another encounter's request.
        """
        pending = self._planner_pending
        if pending is None:
            return False
        if not isinstance(record, dict) or record.get('requestId') != pending['requestId']:
            return False
        self._planner_pending = None
        self._planner_latency_note(pending.get('submittedAt'))
        if record.get('error') is not None:
            self._planner_metrics['rejected'] = int(self._planner_metrics.get('rejected') or 0) + 1
            self._note('planner group failed; no work admitted: %s' % (record.get('error'),))
            self._planning_dirty = True
            return False
        plan = pending['plan']
        # The host normalizes a harvested record before routing it: the complete compute envelope is
        # preserved under ``resultEnvelope`` and ``result`` is replaced by the ordered proposals. An
        # unnormalized record (a direct delivery that skipped the host) still carries the envelope
        # under ``result``; accept that shape too. Either way, validate the ORIGINAL envelope so the
        # request id/digest/revisions are checked against the frozen request.
        envelope = record.get('resultEnvelope')
        if not isinstance(envelope, dict):
            candidate = record.get('result')
            if isinstance(candidate, dict):
                envelope = candidate
        try:
            reason = self._planning_result_reason(pending.get('request'), envelope)
        except Exception as exc:  # noqa: BLE001 - an unreadable request cannot be trusted
            reason = 'the frozen request could not be validated: %s' % (exc,)
        if reason is not None:
            self._planner_metrics['rejected'] = int(self._planner_metrics.get('rejected') or 0) + 1
            self._note('planner result refused: %s' % (reason,))
            self._planning_dirty = True
            return False
        if not self._planner_compatible(pending['envelope'], plan):
            self._planner_metrics['rejected'] = int(self._planner_metrics.get('rejected') or 0) + 1
            self._note('planner result discarded as stale: library/campaign/session/generation/'
                       'focus/policy/encounter/mechanics/parent compatibility changed')
            self._planning_dirty = True
            return False
        pool = [dict(proposal) for proposal in envelope['proposals']]
        if envelope.get('referenceAdmission') is not None:
            # One parent packet per envelope on the wire; share it locally with the bounded cohort.
            for proposal in pool:
                proposal['referenceAdmission'] = envelope['referenceAdmission']
        db = store.db if store is not None else self._db_ref
        try:
            self._measure_stage('admissionSeconds', self._realize_pool, plan, pool, db, store)
        except Exception as exc:  # noqa: BLE001 - one failed admission must not kill the loop
            self._planner_metrics['rejected'] = int(self._planner_metrics.get('rejected') or 0) + 1
            self._note('planner result not admitted: %s' % (exc,))
            self._planning_dirty = True
            return False
        self._planner_metrics['accepted'] = int(self._planner_metrics.get('accepted') or 0) + 1
        return True

    def planner_status(self):
        """Observer-only planner accounting: submitted/running/finished-unharvested/latency."""
        metrics = self._planner_metrics
        latencies = list(self._planner_latency)
        latency = (sum(latencies)/len(latencies)) if latencies else metrics.get('latencySeconds')
        host = self.planning_host
        provider = getattr(host, 'planning_allocation', None) if host is not None else None
        allocation = dict(self._planner_allocation)
        if callable(provider):
            try:
                allocation = dict(provider(self) or allocation)
            except Exception:  # noqa: BLE001 - allocation telemetry is optional
                pass
        return dict(available=self._planning_enabled(),
                    pending=self._planner_pending is not None,
                    submitted=int(metrics.get('submitted') or 0),
                    running=1 if self._planner_pending is not None else 0,
                    finishedUnharvested=0,
                    accepted=int(metrics.get('accepted') or 0),
                    rejected=int(metrics.get('rejected') or 0),
                    latencySeconds=latency,
                    allocation=allocation)

    def allocation_status(self):
        """Requested/effective battle and planner allocation, preserving the duty reading."""
        planner = dict(self._planner_allocation)
        host = self.planning_host
        provider = getattr(host, 'planning_allocation', None) if host is not None else None
        if callable(provider):
            try:
                planner = dict(provider(self) or planner)
            except Exception:  # noqa: BLE001 - allocation telemetry is optional
                pass
        return dict(battle=dict(requested=int(self.workers), effective=int(self.workers)),
                    planner=dict(requested=int(planner.get('requested') or 0),
                                 effective=int(planner.get('effective') or 0)))

    def _first_unmeasured(self, ranked):
        seen = {row.get('candidateId') for row in self.state.get('history') or []}
        seen |= {row.get('candidateId') for row in self.state.get('portfolio') or []}
        for proposal in ranked:
            if proposal.get('childIdentity') and proposal['childIdentity'] not in seen:
                return proposal
        return None

    def _owner_rows(self, key, owner):
        """Owner-scoped history/portfolio rows; `owner is None` is the community-first view."""
        rows = self.state.get(key) or []
        if owner is None:
            return list(rows)
        return [row for row in rows if row.get('owner') == owner]

    def _choose_extension(self, db, owner, purpose):
        """The pure adaptive decision over OWNER-FILTERED history/portfolio, or `None`."""
        history = self._owner_rows('history', owner)
        if not history:
            return None
        return decisions.choose_extension(
            history, self._owner_rows('portfolio', owner),
            remaining_runs=self._remaining(db, owner, purpose),
            max_pairs=EXTENSION_MAX_PAIRS,
            round_index=int(self.state.get('roundIndex') or 0))

    def _extension_proposal(self, store, extension, owner):
        """A proposal that re-measures the SAME candidate against the SAME reference on NEW pairs."""
        candidate_id = str(extension.get('candidateId') or '')
        reference_id = str(extension.get('referenceId') or '')
        if not candidate_id or not reference_id or candidate_id == reference_id:
            return None
        scenario = store.scenario(candidate_id)
        if scenario is None or store.scenario(reference_id) is None:
            return None
        pairs = int(extension.get('additionalPairs') or 0)
        if pairs <= 0:
            return None
        domain = (self.state.get('domains') or {}).get(candidate_id) or {}
        return dict(id='knowledge-extension:%s:%s:%d' % (candidate_id, reference_id, pairs),
                    scenario=scenario, purpose='improvement',
                    plannedBudget=dict(runs=2 * pairs), changedFields=[], fixedFields={},
                    features=(self.state.get('features') or {}).get(candidate_id) or {},
                    domain=domain, label='knowledge-extension',
                    expectedMechanicalDifferences=None, approximatelyPreserved=None,
                    whySimulation='knowledge-extension: %d extra COMMON pairs against the same '
                                  'reference; extra samples are knowledge, not an earning gain'
                                  % pairs)

    def _start_experiment(self, db, store, proposal, purpose, parent_id, parent_scenario, owner=None,
                          *, budget_remaining=None, selection_batch=None, reserved_pairs=None):
        """Freeze ONE finite, immutable, ordered paired plan BEFORE any dispatch.

        The plan is a list of jobs `(candidateId, seedPair)`: every seed pair is run twice - once for
        the candidate and once for its reference - so an N-pair sample costs 2N battles and both arms
        are measured on the SAME ordered pairs. The full intent (raw scenarios, observed views, policy,
        encounter/mechanics/engine revisions, the job list and its digest) is persisted with the
        experiment before the first reservation, so a restart resumes the identical plan and never
        re-selects a partially dispatched one. It returns the frozen member (or None when nothing could
        be frozen) and appends it to the active cohort, keeping ``current`` as the first member.
        """
        owner = owner or 'community'
        scenario = proposal['scenario']
        import strategy_admission_preparation as admission
        preparation_policy = (getattr(self, '_active_child', None) or {}).get('policy')
        if preparation_policy is None:
            preparation_policy = self.state.get('policy') or self._freeze_policy(parent_scenario)
        packet = proposal.get('admissionPreparation')
        if not admission.matches(packet, scenario, preparation_policy,
                                 self.revision, self._mechanics_revision()) or not isinstance(
                                     (packet or {}).get('stats'), dict):
            packet = None
        reference_packet = proposal.get('referenceAdmission')
        if not admission.matches(reference_packet, parent_scenario, preparation_policy,
                                 self.revision, self._mechanics_revision()):
            reference_packet = None
        runs = int((proposal.get('plannedBudget') or {}).get('runs') or IMPROVEMENT_RUNS)
        remaining = (self._remaining(db, owner, purpose) if budget_remaining is None
                     else max(0, int(budget_remaining)))
        if remaining <= 0:
            self._note(f'{purpose} budget exhausted before this experiment')
            return None
        # Freeze the CHILD point chosen for THIS experiment; the overall question stays frozen in
        # state and is only the fallback when no planner child was selected.
        question = getattr(self, '_active_child', None)
        self._active_child = None
        if question is None and (purpose == 'boundary' or (
                purpose == 'support' and
                ((self.state.get('question') or {}).get('question') or {}).get('kind') == 'support')):
            question = (self.state.get('question') or {}).get('question')
            if question is None:
                self._mark_unspendable(purpose, owner)
                return None
        # A knowledge extension freezes a DECLARED finite stage: the whole stage must be funded by the
        # owner's improvement budget, so it is never silently truncated to a smaller pair count. A
        # non-extension proposal keeps its bounded small budget cap.
        is_extension = (proposal.get('label') == 'knowledge-extension'
                        or proposal.get('adaptiveExtension') is True)
        if is_extension:
            intended = max(1, runs // 2)
            if intended > EXTENSION_MAX_PAIRS or intended * 2 > remaining:
                self._note('%s budget left unspent: the remaining improvement budget cannot fund the '
                           'complete %d-pair knowledge stage' % (purpose, intended))
                self._mark_unspendable(purpose, owner)
                return None
            pairs_wanted = intended
        else:
            pairs_wanted = min(max(1, runs // 2), max(0, remaining // 2), CHALLENGER_RUNS // 2)
        if question is not None:
            slot = self.state.get('question') or {}
            question_left = int(slot.get('budget') or 0) - int(slot.get('spent') or 0)
            pairs_wanted = min(max(pairs_wanted, int(slot.get('minimumPairs') or 16)),
                               max(0, question_left // 2), max(0, remaining // 2))
        if pairs_wanted <= 0:
            self._note('%s budget left unspent: too little remains to buy one complete pair' % purpose)
            self._mark_unspendable(purpose, owner)
            return None
        # A blocked global seed-history read is a READINESS condition, not exhausted work:
        # create no candidate and reserve no battle, preserve the whole purpose budget (never
        # mark it unspendable) and report it distinctly, so a restart or a changed
        # library/session retries instead of recording the budget as spent.
        if not self._seed_history_ready(store):
            self._preserve_for_readiness(purpose, owner)
            return None
        cid = self._ensure_candidate(store, proposal, parent_id, owner, prepared=packet)
        if packet is not None:
            timings = self.state.setdefault('timings', {})
            timings['admissionPreparedStats'] = int(timings.get('admissionPreparedStats') or 0) + 1
        self.state.setdefault('features', {})[cid] = dict(proposal.get('features') or {})
        domain = proposal.get('domain') if isinstance(proposal.get('domain'), dict) else {}
        public_domain = dict(context=domain.get('context'),
                             provenanceStatus=domain.get('provenanceStatus'),
                             synthetic=domain.get('context') == 'synthetic',
                             playerUnknown=bool(domain)
                             and domain.get('provenanceStatus') == 'unknown',
                             valid=domain.get('valid'))
        self.state.setdefault('domains', {})[cid] = dict(
            context=domain.get('context'), provenanceStatus=domain.get('provenanceStatus'),
            synthetic=domain.get('context') == 'synthetic',
            playerUnknown=bool(domain) and domain.get('provenanceStatus') == 'unknown')
        ref_id = self._ensure_reference_candidate(store, parent_id, parent_scenario)
        self._presentation(parent_scenario, str(ref_id))
        # An equivalent resident may retain different raw inert fields. Preserve the canonical
        # resident-key observation path whenever the admitted id differs from the planned child.
        if packet is not None and str(cid) != str(proposal.get('childIdentity')):
            packet = None
        if reference_packet is not None and str(ref_id) != str(parent_id):
            reference_packet = None
        observed_candidate = (admission.observed_view(packet, scenario, preparation_policy)
                              if packet is not None else
                              store.observed_scenario(scenario, candidate=cid) or scenario)
        observed_reference = (admission.observed_view(reference_packet, parent_scenario,
                                                    preparation_policy)
                              if reference_packet is not None else
                              store.observed_scenario(parent_scenario, candidate=ref_id) or parent_scenario)
        timings = self.state.setdefault('timings', {})
        timings['admissionPreparedObservations'] = int(
            timings.get('admissionPreparedObservations') or 0) + int(packet is not None) + int(
                reference_packet is not None)
        if question is not None:
            policy = dict(question['policy'])
        else:
            policy = self.state.get('policy') or self._freeze_policy(observed_reference)
            self.state['policy'] = policy
            self._compat = None
        observed_candidate = self._align_policy(observed_candidate, policy)
        observed_reference = self._align_policy(observed_reference, policy)
        # Re-check the final policy: a changed question must never reuse a prepared compilation.
        if packet is not None and packet['policyDigest'] != admission.digest(policy):
            packet = None
        if packet is not None:
            timings['admissionPreparedRevisions'] = int(
                timings.get('admissionPreparedRevisions') or 0) + 1
            self._memo_encounter_revision(observed_candidate, packet['encounterRevision'])
        if reference_packet is not None and reference_packet['policyDigest'] == admission.digest(policy):
            self._memo_encounter_revision(observed_reference, reference_packet['encounterRevision'])
        if question is None and self._freeze_policy(observed_reference) != policy:
            self._note('reference measurement/resource policy aligned to the candidate for a matched '
                       'pair; only the intended build fields differ')
        pairs_list = self._fresh_pairs(store, [cid, ref_id], pairs_wanted,
                                       reserved_extra=reserved_pairs)
        if not pairs_list:
            if not self._seed_history_ready(store):
                self._preserve_for_readiness(purpose, owner)
                return None
            self._note('%s budget left unspent: no fresh collision-free seed pairs available' % purpose)
            self._mark_unspendable(purpose, owner)
            return None
        if is_extension and len(pairs_list) < pairs_wanted:
            # The frozen plan must dispatch exactly the declared stage; a short draw leaves it unspent
            # rather than quietly buying a smaller sample.
            self._note('%s budget left unspent: only %d of the %d fresh collision-free pairs for the '
                       'declared %d-pair knowledge stage are available'
                       % (purpose, len(pairs_list), pairs_wanted, pairs_wanted))
            self._mark_unspendable(purpose, owner)
            return None
        jobs = []
        for pair in pairs_list:
            seed_pair = [int(pair[0]), int(pair[1])]
            jobs.append(dict(candidateId=cid, seedPair=list(seed_pair), arm='candidate'))
            jobs.append(dict(candidateId=ref_id, seedPair=list(seed_pair), arm='reference'))
        planned_battles = len(jobs)
        plan_digest = _digest(jobs)
        stopping = dict(proposal.get('stopping') or {})
        stopping.update(maxRuns=planned_battles,
                        rule='finite reference-matched paired sample; BOTH arms counted per pair',
                        pairs=len(pairs_list), planDigest=plan_digest)
        owner_share = (1.0 if self.mode == 'community-first'
                       else float((self.config.get('allocations') or {}).get(owner, 0.0)))
        intent = dict(scope=owner, owner=owner, ownerShare=owner_share, parentId=parent_id,
                      referenceId=ref_id, purpose=purpose, sessionId=self.session_id,
                      planned_budget=planned_battles, stopping=stopping, policy=policy,
                      mechanicsRevision=self._mechanics_revision(),
                      encounterRevision=(packet['encounterRevision'] if packet is not None else
                                         self._encounter_revision(observed_candidate)),
                      engineRevision=self.revision, compatibility=self._compatibility(),
                      measurementWindow='development',
                      changedFields=proposal.get('changedFields'),
                      fixedFields=proposal.get('fixedFields'),
                      expectedMechanicalDifferences=proposal.get('expectedMechanicalDifferences'),
                      approximatelyPreserved=proposal.get('approximatelyPreserved'),
                      whySimulation=proposal.get('whySimulation'),
                      proposalId=proposal.get('id'), jobs=jobs,
                      ownerParentId=proposal.get('ownerParentId'),
                      seedId=proposal.get('seedId'),
                      lineage=proposal.get('lineage'),
                      strategyBasis=proposal.get('strategyBasis'),
                      referenceRawScenario=parent_scenario, candidateRawScenario=scenario,
                      observedScenarios={cid: observed_candidate, ref_id: observed_reference})
        if selection_batch is not None:
            intent['selectionBatch'] = dict(selection_batch)
        experiment_id = ledger.create_experiment(db, intent)
        self._intent_cache[experiment_id] = json.loads(json.dumps(intent, default=str))
        slot = self.state.get('question') or {}
        member = dict(
            experimentId=experiment_id, candidateId=cid, referenceId=ref_id, purpose=purpose,
            owner=owner, ownerShare=owner_share,
            planned=planned_battles, plannedPairs=len(pairs_list), reserved=0, completed=0,
            proposalId=proposal.get('id'), parentId=parent_id, jobs=jobs,
            observedCandidate=observed_candidate, observedReference=observed_reference,
            expectedMechanicalDifferences=proposal.get('expectedMechanicalDifferences'),
            approximatelyPreserved=proposal.get('approximatelyPreserved'),
            whySimulation=proposal.get('whySimulation'), domain=public_domain,
            policy=policy, encounterRevision=intent['encounterRevision'],
            mechanicsRevision=intent['mechanicsRevision'], engineRevision=self.revision,
            compatibility=intent['compatibility'], planDigest=plan_digest, blocked=False,
            question=question,
            minimumPairs=(slot.get('minimumPairs') if question is not None else None))
        self._set_cohort(self._cohort() + [member])
        if question is not None:
            slot['spent'] = int(slot.get('spent') or 0) + planned_battles
            self.state['question'] = slot
        self.state['roundIndex'] = int(self.state.get('roundIndex') or 0) + 1
        self._persist(store)
        store.db.commit()
        self._last_frozen_plan_available_at = time.monotonic()
        return member

    @staticmethod
    def _align_policy(scenario, policy):
        """Force the shared frozen measurement/resource policy onto one arm's worker scenario."""
        aligned = dict(scenario)
        for key in POLICY_KEYS:
            if key in policy:
                aligned[key] = policy[key]
            else:
                aligned.pop(key, None)
        return aligned

    def _ensure_candidate(self, store, proposal, parent_id, owner='community', *, prepared=None):
        """Store the candidate with its OWNER as the lineage lane source (never relabelled)."""
        from strategy_optimizer_adapter import stats
        source_prior = proposal.get('sourcePrior')
        if source_prior:
            change = 'sourceId=%s;sourceLabel=%s;sourceCandidateId=%s;identity=%s;rank=%s;' \
                     'sourceOwnership=%s;sourceProvenanceDigest=%s;historicalOutcomesUsed=false' % (
                         source_prior.get('sourceId'), source_prior.get('sourceLabel'),
                         source_prior.get('sourceCandidateId'), source_prior.get('sourceIdentity'),
                         source_prior.get('sourceRank'), source_prior.get('sourceOwnership'),
                         source_prior.get('sourceProvenanceDigest'))
            operation = 'source-strategy-prior'
            target = source_prior.get('sourceCandidateId')
            label = proposal.get('label') or 'Student1 strategy prior'
        else:
            change = ';'.join(str(name) for name in (proposal.get('changedFields') or []))
            operation = 'joint-proposal'
            target = proposal.get('id')
            label = 'Joint %s' % proposal.get('purpose')
        proposal_int = int(hashlib.sha256(str(proposal.get('id')).encode('utf-8')).hexdigest()[:8], 16)
        cid, _existed = store.add_child(proposal['scenario'], label,
                                        (prepared['stats'] if prepared is not None else
                                         stats(proposal['scenario'])), parent_id, operation,
                                        target, change, owner, proposal_int)
        return cid

    # -- dispatch ---------------------------------------------------------------------------------
    def _recover(self, db, store, recovery_snapshot=_RECOVERY_NOT_PROVIDED):
        """Re-dispatch only the ledger's single outstanding charge-owner task per sample."""
        if self.evaluator is None:
            return
        # Exact-equivalence early exit: when every worker is already occupied the recovery loop below
        # could submit nothing anyway, so the whole-ledger unfinished-sample scan is provably redundant.
        # Keeping it off the saturated hot path removes one full scan per active pass.
        if self._submission_capacity() <= 0:
            return
        if recovery_snapshot is None:
            return
        report = (ledger.recover(db, session_id=self.session_id, count_completed=False)
                  if recovery_snapshot is _RECOVERY_NOT_PROVIDED else
                  recovery_snapshot.coordinator_view(self.session_id))
        if report.get('blockedCount'):
            self.state['blockedSamples'] = report['blockedCount']
        for entry in report['outstanding']:
            if self._submission_capacity() <= 0:
                break
            intent = self._intent(db, entry['chargedExperimentId']) or {}
            if not self._intent_matches_live(intent):
                self._note('Interrupted work from another scope/revision/policy remains blocked.')
                continue
            scenario = self._frozen_scenario(db, store, entry['chargedExperimentId'],
                                             entry['candidateId'])
            if scenario is None:
                # The frozen plan did not record this candidate's observed scenario. Recomputing it
                # from current library state would dispatch against a scenario the plan never
                # authorised, so the sample stays blocked instead.
                self._note('Interrupted work has no frozen scenario for its candidate; not recomputed.')
                continue
            if self._is_canonical_evaluator():
                self._prepare_durable_dispatch(db)
            if self.evaluator.submit_recovered(db, entry, scenario, **self._runnable_kwargs()):
                self._submission_started += 1

    def _dispatch(self, db, store):
        cohort = self._cohort()
        if not cohort:
            jobs = self._pending_confirmation_jobs(db)
            if jobs:
                self._dispatch_confirmation(db, store, jobs)
            return
        if self.evaluator is None and any(
                not member.get('blocked') for member in cohort):
            # Build the pool once before the fill loop so free-worker capacity is real on the first
            # member; an already-saturated pool is left entirely alone.
            self._make_evaluator()
        submitted = False
        for current in cohort:
            if self._submission_capacity() <= 0:
                break
            if self._dispatch_member(db, current):
                submitted = True
        if submitted:
            # A reservation changed the session's reserved totals; refresh the cached budget rows.
            self._budget_dirty = True

    def _runnable_kwargs(self):
        """Trace-only scheduler stamp supplied to the evaluator at dispatch time.

        The timestamp is taken on the evaluator's own clock so it is comparable with the evaluator's
        submit entry and the host trace sink. A non-tracing evaluator (the default, or an injected
        fake) returns no keyword arguments, so its submit call keeps the unchanged signature and
        behaviour.
        """
        if not getattr(self.evaluator, 'tracing', False):
            return {}
        clock = getattr(self.evaluator, 'clock', None)
        return {'runnable_at': clock() if callable(clock) else time.perf_counter()}

    def _is_canonical_evaluator(self):
        try:
            import strategy_encounter_evaluation as evaluation
            return isinstance(self.evaluator, evaluation.Evaluator)
        except (ImportError, AttributeError):
            return False

    def _prepare_durable_dispatch(self, db):
        """Refuse worker submission until the caller's open transaction is made durable."""
        if getattr(db, 'in_transaction', False):
            raise RuntimeError('cannot dispatch while caller-owned SQLite transaction is open; '
                               'reservation durability is unknown')

    def _dispatch_member(self, db, current):
        """Fill free workers with one cohort member's already-frozen, authorised jobs."""
        hydrated = self._hydrate_current(db, current)
        if current.get('blocked'):
            if not hydrated or not self._recover_known_good_scheduler_block(current):
                return False
        experiment_id = current['experimentId']
        summary = self._experiment_state(db, experiment_id)
        if summary['complete'] or summary['remaining'] <= 0:
            return False
        # Cheap availability gate BEFORE the revision recheck. `_current_compatible` recompiles the
        # encounter revision (a serial rebuild that re-reads the mechanics/compiler digests), and that
        # work is only justified when a submission would actually follow. With every job already
        # reserved, or every slot occupied, nothing can be handed to a worker this pass, so the pool is
        # left alone and the compile is skipped. The revision is still checked on the pass that does
        # submit, so the frozen-plan fail-closed boundary is unchanged.
        if self._submission_capacity() <= 0:
            return False
        # Ready-ahead evaluators can admit a bounded window larger than the physical worker count.
        # Collect only what this coordinator may submit now, while keeping the atomic reservation
        # batch bounded independently of the pool's window.
        ready_limit = min(self._submission_capacity(), RESERVATION_BATCH_MAX)
        candidate_id = str(current['candidateId'])
        reserved = self._reserved_jobs(db, experiment_id)
        ready = []
        for job in current.get('jobs') or []:
            cid = str(job['candidateId'])
            seed_pair = job.get('seedPair') or []
            if len(seed_pair) != 2:
                continue
            pair = (int(seed_pair[0]), int(seed_pair[1]))
            if (cid, pair[0], pair[1]) in reserved:
                continue
            scenario = (current.get('observedCandidate') if cid == candidate_id
                        else current.get('observedReference'))
            if scenario is None:
                continue
            ready.append((cid, pair, scenario))
            if len(ready) >= ready_limit:
                break
        if not ready:
            return False
        # Fill the bounded admission capacity from this already-frozen, authorised job list. The
        # ready-ahead window may exceed physical workers; admission remains bounded by free quota,
        # the finite plan, and the atomic reservation batch limit.
        capacity = self._submission_capacity()
        compatible, reason = self._current_compatible(current)
        if not compatible:
            self._block_current(reason, current)
            return False
        submitted = 0
        submit_recovered = getattr(self.evaluator, 'submit_recovered', None)
        canonical_evaluator = self._is_canonical_evaluator()
        process_duty_queue = bool(getattr(self.evaluator, '_ready_queue_enabled', False))
        batch_submission_ready = canonical_evaluator and callable(submit_recovered)
        try:
            duty = float(getattr(self.evaluator, 'duty', 1.0))
        except (TypeError, ValueError):
            duty = 0.0
        if getattr(self.evaluator, 'closed', False) or (duty < 1.0 and not process_duty_queue):
            batch_submission_ready = False
        # A worker can still be duty-held after duty was changed back to 1.0. Match the
        # evaluator's own slot gate before making a durable reservation; otherwise retain its
        # original per-seed submit path for this pass. ReadyQueueEvaluator's slots are logical
        # admission IDs; its pool owns physical-process cooldown, so that diagnostic vector must
        # not prevent an atomic reservation batch from filling the bounded ready window.
        slot_ready = getattr(self.evaluator, '_slot_ready', None)
        slot_clock = getattr(self.evaluator, 'clock', None)
        if (batch_submission_ready and not process_duty_queue
                and isinstance(slot_ready, (list, tuple)) and callable(slot_clock)):
            now = slot_clock()
            occupied = {job.get('slot') for job in
                        (getattr(self.evaluator, 'pending', {}) or {}).values()}
            if any(slot not in occupied and ready_at > now
                   for slot, ready_at in enumerate(slot_ready)):
                batch_submission_ready = False
        if batch_submission_ready:
            # The canonical evaluator can dispatch an already-reserved sample. Admit the bounded
            # ordered group atomically, commit it, then hand its durable rows to HeadlessPool. A
            # completed coalesced sample has no pending_sample and consumes no worker capacity, so
            # continue through this frozen list just as the former per-job submit loop did.
            dispatchable = self._reserve_dispatch_batch(
                db, experiment_id, ready, capacity=min(capacity, RESERVATION_BATCH_MAX))
            for entry, scenario in dispatchable:
                pair = tuple(entry['seedPair'])
                cid = str(entry['candidateId'])
                if submit_recovered(db, entry, scenario, **self._runnable_kwargs()):
                    reserved.add((cid, pair[0], pair[1]))
                    submitted += 1
                    self._submission_started += 1
        else:
            if canonical_evaluator and not getattr(self.evaluator, 'closed', False):
                self._prepare_durable_dispatch(db)
            # Preserve the established seam for injected/fake evaluators, which may only expose
            # submit(db, experiment, candidate, scenario, pair).
            for cid, pair, scenario in ready:
                if submitted >= capacity:
                    break
                # Stamp the scheduler's runnable decision IMMEDIATELY before each dispatch; the
                # evaluator records its own submit entry separately.
                if self.evaluator.submit(db, experiment_id, cid, scenario, pair,
                                         **self._runnable_kwargs()):
                    reserved.add((cid, pair[0], pair[1]))
                    submitted += 1
                    self._submission_started += 1
        self._record_submission_limit(capacity, len(ready))
        return bool(submitted)

    def _reserve_dispatch_batch(self, db, experiment_id, ready, *, capacity):
        """Atomically reserve an ordered, dispatchable subset before any worker submit.

        ``ledger.reserve`` keeps its public one-pair API and its own SAVEPOINTs. This coordinator
        transaction groups up to 64 such calls, bounded by free worker slots. Only pending samples
        owned by this experiment are returned for dispatch; cross-experiment reuse keeps the ledger's
        canonical charge owner and completed reuse remains immediately visible as evidence.
        """
        limit = min(RESERVATION_BATCH_MAX, max(0, int(capacity)))
        if limit <= 0 or not ready:
            return []
        self._prepare_durable_dispatch(db)

        pending = getattr(self.evaluator, 'pending', {}) or {}
        pending_keys = set(pending)
        dispatchable = []
        seen = set()
        admitted = False
        reserve_calls = 0
        try:
            db.execute('BEGIN')
            if not getattr(db, 'in_transaction', False):
                raise RuntimeError('SQLite did not open the reservation batch transaction')
            for candidate_id, pair, scenario in ready:
                pair = tuple(pair)
                intent_key = (experiment_id, candidate_id, pair)
                if intent_key in seen or intent_key in pending_keys:
                    continue
                if reserve_calls >= RESERVATION_BATCH_MAX:
                    break
                reserve_calls += 1
                if not ledger.reserve(db, experiment_id, candidate_id, pair):
                    continue
                admitted = True
                entry = ledger.pending_sample(db, experiment_id, candidate_id, pair)
                if entry is None:
                    # Completed coalesced work or work owned by another experiment consumes no
                    # worker slot here. Its ledger link still commits with the rest of the bank.
                    continue
                dispatch_key = (entry['chargedExperimentId'], entry['candidateId'],
                                tuple(entry['seedPair']))
                if dispatch_key in pending_keys or dispatch_key in seen:
                    continue
                dispatchable.append((entry, scenario))
                seen.add(intent_key)
                seen.add(dispatch_key)
                if len(dispatchable) >= limit:
                    break
            db.commit()
        except BaseException:
            if getattr(db, 'in_transaction', False):
                db.rollback()
            raise

        # Do not dirty cached coordinator counters on a rolled-back/failed bank. A successful bank
        # can include only a coalesced-completed link and still changes this experiment's ledger view.
        if admitted:
            self._budget_dirty = True
        return dispatchable

    def _intent(self, db, experiment_id):
        # Keep full-intent hydration on the canonical reader so schema 5 resolves the exact BLOB,
        # while schema 3/4 continue to use their full-text intent_json fallback. The SQL selector
        # above this path deliberately consumes only the thin metadata projection.
        if experiment_id not in self._intent_cache:
            try:
                self._intent_cache[experiment_id] = ledger.experiment_intent(db, experiment_id)
            except Exception:  # noqa: BLE001 - a missing intent is reported by the caller
                self._intent_cache[experiment_id] = None
        return self._intent_cache[experiment_id]

    def _hydrate_current(self, db, current):
        """Rebuild a frozen plan from the persisted intent when in-memory state is incomplete."""
        if current.get('jobs') and current.get('observedCandidate') is not None:
            return True
        intent = self._intent(db, current.get('experimentId')) or {}
        scenarios = intent.get('observedScenarios') or {}
        for key in ('jobs', 'policy', 'encounterRevision', 'mechanicsRevision', 'engineRevision',
                    'compatibility', 'planDigest', 'referenceId', 'parentId'):
            if key not in current or current.get(key) in (None, [], {}):
                if key in intent:
                    current[key] = intent[key]
        current['observedCandidate'] = scenarios.get(str(current.get('candidateId')))
        current['observedReference'] = scenarios.get(str(current.get('referenceId')))
        if current.get('planned') in (None, 0):
            current['planned'] = len(current.get('jobs') or [])
        if current.get('plannedPairs') in (None, 0):
            current['plannedPairs'] = len(current.get('jobs') or []) // 2
        return bool(current.get('jobs'))

    def _current_compatible(self, current):
        """Revisions must still match the frozen plan; a changed scope refuses to dispatch."""
        engine_revision = current.get('engineRevision')
        if not self._engine_revision_matches(engine_revision):
            return False, 'loaded engine revision differs from the frozen plan'
        if current.get('mechanicsRevision') != self._mechanics_revision():
            return False, 'mechanics revision differs from the frozen plan'
        observed = current.get('observedCandidate')
        reference = current.get('observedReference')
        encounter_revision = current.get('encounterRevision')
        encounter_scope = getattr(self, '_encounter_revision_scope', None)
        if encounter_scope is None:
            return False, 'compiled encounter reference scope is unavailable'
        if (not self._scenario_matches_encounter(observed)
                or not self._scenario_matches_encounter(reference)):
            return False, 'frozen candidate or reference belongs to another encounter'
        if (encounter_revision is None
                or self._encounter_revision(observed) != encounter_revision
                or self._encounter_revision(reference) != encounter_revision):
            return False, 'encounter revision differs from the frozen plan'
        if encounter_revision != encounter_scope:
            return False, 'compiled encounter revision differs from the live reference scope'
        if not self._frozen_scope_matches(engine_revision, current.get('compatibility')):
            return False, 'search-scope compatibility differs from the frozen plan'
        return True, None

    def _compatibility_for_revision(self, revision):
        return _digest(dict(encounter=self.encounter,
                            mechanics=self._mechanics_revision(),
                            revision=revision, constraints=self.constraints,
                            policy=self.state.get('policy')))

    def _frozen_compatibility_matches(self, engine_revision, compatibility):
        """Current semantic revision, or the one exact pre-split revision on its captured baseline."""
        return (self._engine_revision_matches(engine_revision)
                and self._frozen_scope_matches(engine_revision, compatibility))

    def _engine_revision_matches(self, engine_revision):
        if engine_revision == self.revision:
            return True
        return strategy_revision.accepts_legacy_scheduler_revision(engine_revision, self.revision)

    def _frozen_scope_matches(self, engine_revision, compatibility):
        if engine_revision == self.revision:
            return compatibility == self._compatibility()
        if not strategy_revision.accepts_legacy_scheduler_revision(engine_revision, self.revision):
            return False
        return compatibility == self._compatibility_for_revision(engine_revision)

    def _compatible_search_digests(self):
        values = [self._compatibility()]
        legacy = strategy_revision.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION
        if strategy_revision.accepts_legacy_scheduler_revision(legacy, self.revision):
            values.append(self._compatibility_for_revision(legacy))
        return values

    def _block_current(self, reason, current=None):
        current = current if current is not None else self.state.get('current')
        if current is None:
            return
        already_blocked_for_reason = (current.get('blocked')
                                      and current.get('blockedReason') == reason)
        current['blocked'] = True
        current['blockedReason'] = reason
        if not already_blocked_for_reason:
            self._note('dispatch refused, no observation restamped: ' + reason)

    def _recover_known_good_scheduler_block(self, current):
        """Recheck a plan blocked by the captured scheduler-only revision transition.

        Older builds persisted ``blocked=True`` after comparing their frozen scheduler digest with
        the then-loaded runtime. That flag short-circuits dispatch before the revision guard can run,
        so the explicit legacy revision bridge alone cannot resume those plans after restart. Clear
        only that exact stale reason and only after revalidating every frozen compatibility field.
        No intent, sample, outcome, or measurement revision is rewritten.
        """
        if (not current.get('blocked')
                or current.get('blockedReason') != 'loaded engine revision differs from the frozen plan'
                or current.get('engineRevision')
                != strategy_revision.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION):
            return False
        compatible, reason = self._current_compatible(current)
        if not compatible:
            self._block_current(reason, current)
            return False
        current['blocked'] = False
        current.pop('blockedReason', None)
        self._note('resumed a frozen plan after revalidating its known-good scheduler revision')
        return True

    def _submission_capacity(self):
        """Remaining bounded admission window, further limited by this encounter's fair quota.

        Ready-ahead evaluators include waiting jobs in their finite window. Older pools retain
        one outstanding job per worker. Every admission still uses the same ledger reservations.
        """
        if self.evaluator is None:
            return 0
        admission = getattr(self.evaluator, 'admission_capacity', None)
        capacity = (admission() if callable(admission) else
                    max(0, self.workers - len(self.evaluator.pending)))
        if self._submission_quota is not None:
            capacity = min(capacity, max(0, int(self._submission_quota)-self._submission_started))
        return capacity

    def _record_submission_limit(self, capacity, finite_jobs):
        """Observer-only record of the batch actually allowed this pass; never a selection input."""
        self.last_submission_limit = dict(workers=self.workers, capacity=int(capacity),
                                          finiteJobs=int(finite_jobs),
                                          historicalCap=MAX_SUBMISSIONS_PER_PASS)
        self.state['timings']['submissionLimit'] = self.last_submission_limit

    def _frozen_scenario(self, db, store, experiment_id, candidate_id):
        """The FROZEN observed view of one job's candidate, never recomputed from current state.

        Recovery may only dispatch the exact scenario the frozen plan recorded. An intent with no
        stored observed scenario (a legacy or unknown plan) returns None so the caller leaves the
        sample blocked rather than re-deriving a scenario the plan never authorised.
        """
        cid = str(candidate_id)
        for member in self._cohort():
            if member.get('experimentId') != experiment_id:
                continue
            if cid == str(member.get('candidateId')):
                return member.get('observedCandidate')
            if cid == str(member.get('referenceId')):
                return member.get('observedReference')
        intent = self._intent(db, experiment_id) or {}
        return (intent.get('observedScenarios') or {}).get(cid)

    def _pair_for(self, experiment_id, candidate_id, index):
        digest = hashlib.sha256(('%s|%s|%s' % (experiment_id, candidate_id,
                                               index)).encode('utf-8')).digest()
        # The ledger accepts ordered pairs in 0..2**31-1, so each half is masked to 31 bits.
        return ((int.from_bytes(digest[:4], 'big') & 0x7FFFFFFF) or 1,
                (int.from_bytes(digest[4:8], 'big') & 0x7FFFFFFF) or 1)

    def _pair_taken(self, db, pair):
        if db.execute('SELECT 1 FROM ea_holdout WHERE seed_a=? AND seed_b=?', pair).fetchone():
            return True
        return db.execute('SELECT 1 FROM ea_sample_link WHERE seed_a=? AND seed_b=?',
                          pair).fetchone() is not None

    def _reserved_pairs(self, db, experiment_id):
        return {tuple(row) for row in db.execute(
            'SELECT seed_a, seed_b FROM ea_sample_link WHERE experiment_id=?', (experiment_id,))}

    def _reserved_jobs(self, db, experiment_id):
        return {(str(row[0]), int(row[1]), int(row[2])) for row in db.execute(
            'SELECT candidate_id, seed_a, seed_b FROM ea_sample_link WHERE experiment_id=?',
            (experiment_id,))}

    def _experiment_state(self, db, experiment_id):
        row = db.execute('SELECT total, reserved, completed FROM ea_experiment_budget '
                         'WHERE experiment_id=?', (experiment_id,)).fetchone()
        if row is None:
            raise ValueError('unknown experiment %r' % (experiment_id,))
        total, reserved, completed = int(row[0]), int(row[1]), int(row[2])
        return dict(planned=total, reserved=reserved, completed=completed,
                    complete=(reserved >= total and completed >= total), remaining=total - reserved)

    # -- evidence ---------------------------------------------------------------------------------
    def _absorb(self, db, entry):
        if entry.get('holdout'):
            return
        for member in self._cohort():
            if member.get('experimentId') == entry['experimentId']:
                member['reserved'] = len(self._reserved_jobs(db, entry['experimentId']))
                member['completed'] = member.get('completed', 0) + int(bool(entry.get('accepted')))
                break
        if entry.get('timedOut'):
            self._note('a bounded worker timeout occurred; the affected claim is blocked')

    def _finish_experiment(self, db, store, current, summary, *, stage_totals=None):
        def measured(name, function, *args, **kwargs):
            if stage_totals is None:
                return function(*args, **kwargs)
            began = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                stage_totals[name] = stage_totals.get(name, 0.0) + time.perf_counter() - began

        experiment_id = current['experimentId']
        # Idempotency: a restart that already recorded this experiment must never append a second
        # history/portfolio row. It is simply dropped from the cohort.
        if experiment_id in (self.state.get('completedExperiments') or []):
            self._set_cohort([member for member in self._cohort()
                              if member.get('experimentId') != experiment_id])
            return
        candidate_id = str(current['candidateId'])
        reference_id = (str(current['referenceId']) if current.get('referenceId') is not None
                        else None)
        rows = measured('outcomes', lambda: self._dedupe_rows(ledger.outcomes(db, experiment_id)))
        # The candidate's own row aggregates ONLY its own arm, never the reference's outcomes.
        candidate_rows = [row for row in rows if str(row['candidateId']) == candidate_id]
        aggregate = measured('aggregate', outcomes_service.summarize,
                             [row['outcome'] for row in candidate_rows])
        paired = measured('pairedEvidence', self._paired_development,
                          rows, candidate_id, reference_id)
        owner = current.get('owner') or 'community'
        record = dict(candidateId=candidate_id, experimentId=experiment_id,
                      purpose=current['purpose'], referenceId=reference_id, owner=owner,
                      scope=owner,
                      window='development', compatibility=current.get('compatibility'),
                      pairs=(paired or {}).get('pairs'),
                      pairedMeanDifference=(paired or {}).get('meanDifference'),
                      pairedStandardError=(paired or {}).get('pairedStandardError'),
                      meanEarned=aggregate['meanEarned'], resolved=aggregate['resolvedCount'],
                      total=aggregate['total'], eligible=aggregate.get('eligibleForRecommendation'),
                      unknownCount=aggregate.get('unresolvedCount'),
                      standardError=self._standard_error(aggregate),
                      median=aggregate.get('median'),
                      lossFrequency=aggregate.get('lossFrequency'),
                      jackpotSensitive=(aggregate.get('diagnostics') or {}).get('jackpotSensitive'))
        self.state.setdefault('history', []).append(record)
        self.state.setdefault('completedExperiments', []).append(experiment_id)
        self.state.setdefault('paired', []).append(
            dict(candidateId=candidate_id, referenceId=reference_id, experimentId=experiment_id,
                 purpose=current['purpose'], owner=owner, comparison=paired))
        measured('portfolioWrite', ledger.record_portfolio, db, experiment_id, record,
                 candidate_id=candidate_id)
        # Mechanism/diversity diagnostics live in their OWN durable archive and never enter the
        # earning mean ranking: a rare/jackpot one-off is recorded, not promoted.
        if current.get('purpose') == 'mechanism' or record.get('jackpotSensitive'):
            measured('mechanismArchive', self._archive_mechanism,
                     db, experiment_id, current, aggregate, record)
        if current.get('question'):
            measured('boundaryAssessment', self._record_boundary_assessment,
                     db, current, rows)
        # Remove THIS member, keeping every other cohort member (and the stable order) intact.
        measured('cohortRemoval', self._set_cohort,
                 [member for member in self._cohort()
                  if member.get('experimentId') != experiment_id])
        # Refresh the earning portfolio BEFORE any freeze decision: the confirmation nominates on
        # measured development evidence, not on a stale view from the previous pass.
        incumbent = measured('portfolioRefresh', self._refresh_portfolio, db)
        self._portfolio_refreshed_this_pass = True
        measured('adviserFit', self._fit_adviser, db, owner)
        self._budget_dirty = True
        measured('confirmation', self._maybe_freeze_confirmation, db, store,
                 incumbent=incumbent, incumbent_precomputed=True)

    @staticmethod
    def _dedupe_rows(rows):
        """Coalesced samples counted once: one (candidate, sample) row per charged battle."""
        seen = set()
        deduped = []
        for row in rows:
            identity = (str(row.get('candidateId')),
                        row.get('sampleKey') or tuple(row.get('seedPair') or ()))
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(row)
        return deduped

    def _archive_mechanism(self, db, experiment_id, current, aggregate, record):
        """Persist a rare/mechanism diagnostic to the SEPARATE mechanism archive.

        Idempotent per experiment: a restart or an empty tick re-entering the same completed
        experiment never writes a second row. This archive is never read for mean ranking.
        """
        archived = self.state.setdefault('mechanismArchive', [])
        if experiment_id in archived:
            return
        diagnostics = aggregate.get('diagnostics') or {}
        payload = dict(experimentId=experiment_id, candidateId=str(current['candidateId']),
                       owner=current.get('owner'), purpose=current.get('purpose'),
                       window='development', meanEarned=aggregate.get('meanEarned'),
                       maxEarned=aggregate.get('max'), skewness=diagnostics.get('skewness'),
                       top1ShareOfTotal=diagnostics.get('top1ShareOfTotal'),
                       jackpotSensitive=diagnostics.get('jackpotSensitive'),
                       note='separate mechanism/diversity archive; not used for mean ranking')
        ledger.record_mechanism(db, experiment_id, payload, candidate_id=str(current['candidateId']))
        archived.append(experiment_id)

    def _holdout_publication_row(self, db, experiment_id, candidate_id):
        """The persisted holdout publication row's id, or None (durable crash-safe identity).

        The unique identity is (confirmation experiment, nominee candidate, window='holdout'); the
        session owns the experiment, so no separate session column is needed. A restart after the
        archive INSERT committed but before the runtime state was saved must not append a second row.
        """
        row = db.execute(
            'SELECT id, payload FROM ea_earning_portfolio WHERE experiment_id=? AND candidate_id=? '
            'ORDER BY id', (experiment_id, candidate_id)).fetchone()
        while row is not None:
            try:
                payload = json.loads(row[1])
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get('window') == 'holdout':
                return row[0]
            row = db.execute(
                'SELECT id, payload FROM ea_earning_portfolio WHERE experiment_id=? AND candidate_id=? '
                'AND id>? ORDER BY id', (experiment_id, candidate_id, row[0])).fetchone()
        return None

    def _record_holdout_publication(self, db, confirmation, decision):
        """Persist a CONFIRMED publication as a durable window='holdout' portfolio record.

        `confirmation_service.decide` reports a real confirmation as
        ``status='supported-improvement', confirmed=True`` (never ``status='confirmed'``), so the
        claim is read from the boolean. Idempotent per (experiment, nominee, window) using the
        PERSISTED row: an empty tick, a restart, or a crash between the archive commit and the
        runtime-state save never appends a duplicate, and an inconclusive/withheld decision is NOT
        stored as a publication.
        """
        experiment_id = confirmation.get('experimentId')
        candidate_id = confirmation.get('nominee')
        if decision.get('confirmed') is not True or experiment_id is None:
            return
        if self.state.get('holdoutRecord') == experiment_id:
            return
        try:
            if self._holdout_publication_row(db, experiment_id, candidate_id) is not None:
                self.state['holdoutRecord'] = experiment_id
                return
        except Exception as exc:  # noqa: BLE001 - a failed idempotency read fails closed
            self._note('holdout archive idempotency check failed: %s' % (exc,))
            return
        comparison = self._paired_comparison(db, experiment_id, confirmation) or {}
        payload = dict(candidateId=confirmation.get('nominee'), referenceId=confirmation.get('reference'),
                       owner=confirmation.get('owner'), scope=confirmation.get('scope'),
                       window='holdout', confirmation=True, confirmed=True,
                       metric='finalEarned', pairs=comparison.get('pairs'),
                       pairedDifference=comparison.get('pairedDifference'),
                       pairedStandardError=comparison.get('pairedStandardError'),
                       nomineeMean=comparison.get('nomineeMean'),
                       referenceMean=comparison.get('referenceMean'),
                       reason=decision.get('reason'))
        ledger.record_portfolio(db, experiment_id, payload, candidate_id=confirmation.get('nominee'))
        self.state['holdoutRecord'] = experiment_id

    def _paired_development(self, rows, candidate_id, reference_id):
        """Matched-pair candidate - reference difference, only where BOTH arms resolved."""
        if reference_id is None:
            return None
        by_pair = {}
        for row in rows:
            key = tuple(row['seedPair'])
            slot = by_pair.setdefault(key, {})
            cid = str(row['candidateId'])
            if cid == candidate_id:
                slot['candidate'] = row['outcome']
            elif cid == reference_id:
                slot['reference'] = row['outcome']
        differences = []
        candidate_values = []
        reference_values = []
        pairs = 0
        for slot in by_pair.values():
            candidate = slot.get('candidate')
            reference = slot.get('reference')
            if not candidate or not reference:
                continue
            pairs += 1
            c_value, r_value = candidate.get('finalEarned'), reference.get('finalEarned')
            if candidate.get('resolved') and reference.get('resolved') \
                    and c_value is not None and r_value is not None:
                differences.append(c_value - r_value)
                candidate_values.append(c_value)
                reference_values.append(r_value)
        mean = sum(differences) / len(differences) if differences else None
        standard_error = None
        if len(differences) >= 2:
            variance = sum((value - mean) ** 2 for value in differences) / (len(differences) - 1)
            standard_error = math.sqrt(variance / len(differences))
        return dict(pairs=pairs, resolvedPairs=len(differences), meanDifference=mean,
                    pairedStandardError=standard_error,
                    candidateMean=(sum(candidate_values) / len(candidate_values)
                                   if candidate_values else None),
                    referenceMean=(sum(reference_values) / len(reference_values)
                                   if reference_values else None))

    def _question_tested_values(self, question):
        """Every value already measured or attempted for a frozen question, from persisted state.

        Restart safety: the tested set is re-derived from the durable boundary records (published
        points AND unresolved gaps alike), never from an in-memory cursor, so a resumed session does
        not retest a point it already bought and never silently re-emits the same child.
        """
        if not isinstance(question, dict):
            return []
        root_id = question.get('id')
        values = []
        for entry in self.state.get('boundaries') or []:
            if entry.get('questionId') != root_id:
                continue
            for bucket in ('testedPoints', 'unresolvedGaps'):
                for item in entry.get(bucket) or []:
                    tested = item.get('value') if isinstance(item, dict) else None
                    value = tested.get('value') if isinstance(tested, dict) else None
                    if isinstance(value, int) and not isinstance(value, bool):
                        values.append(int(value))
        return values

    def _record_boundary_assessment(self, db, current, rows):
        """Assess ONE tested candidate/stat point through the real conditional-regions service."""
        slot = self.state.get('question') or {}
        question = current.get('question') or slot.get('question')
        if not question or question.get('kind') not in BOUNDARY_KINDS:
            return
        candidate_id = str(current['candidateId'])
        reference_id = str(current.get('referenceId'))

        def evidence(cid):
            out = []
            for row in rows:
                if str(row['candidateId']) != cid:
                    continue
                outcome = row['outcome']
                out.append(dict(seeds=[int(row['seedPair'][0]), int(row['seedPair'][1])],
                                finalEarned=outcome.get('finalEarned'),
                                resolved=outcome.get('resolved') is True,
                                policy=outcome.get('policy'), candidateId=str(row['candidateId']),
                                encounterRevision=outcome.get('encounterRevision'),
                                mechanicsRevision=outcome.get('mechanicsRevision')))
            return out

        tested_value = dict(field=question.get('field'), kind=question.get('kind'),
                            value=(question.get('fixedFields') or {}).get(question.get('field')))
        minimum_pairs = int(current.get('minimumPairs') or slot.get('minimumPairs') or 16)
        # The ASSESSMENT belongs to the frozen OVERALL question (its id/tolerance/reference); the
        # tested value is the per-experiment CHILD point. Without this the child's own id would leak
        # into the persisted assessment, splitting one question across several pseudo-questions.
        study = slot.get('question') if isinstance(slot.get('question'), dict) else question
        try:
            result = regions.assess(study, evidence(reference_id), evidence(candidate_id),
                                    candidate_id=candidate_id, value=tested_value,
                                    minimum_pairs=minimum_pairs)
        except Exception as exc:  # noqa: BLE001 - a refused assessment must not stop the cycle
            self._note('boundary assessment refused: %s' % (exc,))
            return
        classification = result.get('classification')
        published = classification in ('supported-acceptable', 'supported-degraded')
        tested = []
        gaps = []
        if published:
            tested.append(dict(candidateId=candidate_id, value=tested_value,
                               classification=classification, pairedCount=result.get('pairedCount'),
                               diagnosticInterval=result.get('diagnosticInterval'),
                               uncertaintyMethod=result.get('uncertaintyMethod')))
        else:
            gaps.append(dict(candidateId=candidate_id, value=tested_value,
                             classification=classification, pairedCount=result.get('pairedCount'),
                             reason=result.get('reason')))
        entry = dict(
            kind=question['kind'], experimentId=current['experimentId'],
            frozenReference=reference_id,
            # The child point freezes its OWN value/id; the root question id is carried separately so
            # the tested-set can be re-derived per overall question across a restart.
            questionId=question.get('questionId') or question['id'], childId=question.get('id'),
            childValue=tested_value, testedPoints=tested,
            unresolvedGaps=gaps, assessment=result, published=published,
            note='a tested point is a candidate/stat value; gaps stay unknown and no safe '
                 'Cartesian box or no-compensation claim is inferred')
        payload = dict(study=question, assessment=result, testedValue=tested_value, inference=None,
                       safeCartesianProductClaimed=False, published=published)
        try:
            ledger.record_boundary(db, current['experimentId'], BOUNDARY_KINDS[question['kind']],
                                   reference_id, tested, gaps, payload)
            entry['persisted'] = True
        except Exception as exc:  # noqa: BLE001 - a refused/failed write must not fake a publication
            # A published claim is only real once the ledger stored it. Keep the assessment for
            # diagnostics, but do not let the in-memory record pretend the write succeeded.
            entry['published'] = False
            entry['persisted'] = False
            entry['persistenceError'] = '%s: %s' % (type(exc).__name__, exc)
            entry['unresolvedGaps'] = list(entry['unresolvedGaps']) + [dict(
                candidateId=candidate_id, value=tested_value, classification='unpersisted',
                pairedCount=result.get('pairedCount'),
                reason='boundary record was not durably stored; no publication is claimed')]
            self._note('boundary record not persisted; publication withheld: %s' % (exc,))
        self.state.setdefault('boundaries', []).append(entry)

    def _evidence(self, owner=None, parent=None, parent_id=None, db=None):
        """Observed stat vectors, plus the chosen parent's OWN support diagnosis, for the repair
        specs.

        A blind stream (stumble) never reads empirical conclusions, and a discovery stream is not
        vetoed by Community's learned preferences, so both get an empty evidence set. Every other
        all-strategy owner sees only its OWN measured rows, keeping empirical evidence per-family.
        When a chosen parent and its ledger are supplied, the shared read-only support reader names
        the parent's limiting role/stats from the parent's OWN compatible development telemetry; that
        explains an intervention on the parent and is never a future feature of another candidate.
        """
        if owner in (students.STUDENT_STUMBLING, students.STREAM_DISCOVERY):
            return {}
        vectors = []
        for row in self.state.get('history') or []:
            if owner is not None and row.get('owner') != owner:
                continue
            features = (self.state.get('features') or {}).get(row['candidateId'])
            if features:
                vectors.append(features)
        evidence = {'observedStatVectors': vectors}
        if parent is not None and parent_id is not None and db is not None:
            evidence.update(self._support_diagnosis(db, owner, parent_id, parent))
        return evidence

    def _support_diagnosis(self, db, owner, parent_id, parent):
        """The chosen parent's evidence-backed support trigger, or ``{}``.

        Only the parent's own frozen, compatible DEVELOPMENT rows are read (the shared reader excludes
        holdout and future windows); a missing or mismatched parent yields an empty result rather than
        a guessed trigger. No future telemetry or holdout row can reach this path.
        """
        try:
            import strategy_support_evidence as support_evidence
            report = support_evidence.diagnose(
                db, candidate_id=str(parent_id), scenario=parent, owner=owner,
                compatibility=self._compatibility())
        except Exception as exc:  # noqa: BLE001 - a refused diagnosis is reported, never fabricated
            self._note('support diagnosis unavailable: %s' % (exc,))
            return {}
        if not isinstance(report, dict) or not report.get('supported'):
            return {}
        return {'limitingRole': report.get('limitingRole'),
                'limitingStats': report.get('limitingStats'),
                'parentTelemetry': report.get('parentTelemetry')}

    def _intent_matches_live(self, intent):
        """Fail closed unless every frozen scope field still matches the live search.

        The compatibility digest alone is not sufficient: an intent written by another code path (or a
        stale/forged one) can carry a matching digest while its independently stored policy, mechanics
        revision or engine revision disagree with the live values. Such an intent must never contribute
        evidence, ranking or recovery.
        """
        if not isinstance(intent, dict) or intent.get('compatibility') is None:
            return False
        if not self._frozen_compatibility_matches(intent.get('engineRevision'),
                                                  intent.get('compatibility')):
            return False
        if intent.get('mechanicsRevision') != self._mechanics_revision():
            return False
        if intent.get('policy') != self.state.get('policy'):
            return False
        # The digest contains encounter id but not the compiled enemy payload. Verify the
        # immutable scenario payloads too, including defeatCount-sensitive compiled revisions.
        encounter = getattr(self, 'encounter', None)
        encounter_scope = getattr(self, '_encounter_revision_scope', None)
        if (encounter is None or encounter_scope is None
                or intent.get('encounterRevision') is None):
            return False
        if intent.get('encounterRevision') != encounter_scope:
            return False
        candidate = intent.get('candidateRawScenario', intent.get('candidateScenario'))
        reference = intent.get('referenceRawScenario', intent.get('referenceScenario'))
        if not isinstance(candidate, dict) or not isinstance(reference, dict):
            return False
        jobs = intent.get('jobs') or []
        candidate_id = intent.get('candidateId')
        if candidate_id is None and jobs and isinstance(jobs[0], dict):
            candidate_id = jobs[0].get('candidateId')
        reference_id = intent.get('referenceId')
        if candidate_id is None or reference_id is None:
            return False
        try:
            import strategy_optimizer as optimizer
            if (optimizer.identity(candidate) != str(candidate_id)
                    or optimizer.identity(reference) != str(reference_id)):
                return False
        except Exception:  # noqa: BLE001 - uncertain identity must never admit old evidence
            return False
        for scenario in (candidate, reference):
            if scenario.get('encounterId') != encounter:
                return False
            if self._encounter_revision(scenario) != intent.get('encounterRevision'):
                return False
        return True

    def _compatible_experiment_ids(self, db):
        """Experiment ids whose frozen intent matches the live compatibility AND frozen fields."""
        # Intents are immutable. Local sample/meta writes do not change this set. An external
        # commit, a newly frozen experiment or a changed scope invalidates the memo.
        encounter = getattr(self, 'encounter', None)
        encounter_scope = getattr(self, '_encounter_revision_scope', None)
        token = (db.execute('PRAGMA data_version').fetchone()[0],
                 db.execute('SELECT MAX(id) FROM ea_experiment').fetchone()[0],
                 self._compatibility(), self._mechanics_revision(), self.revision,
                 encounter, encounter_scope)
        cached = self._compatible_ids_cache
        if cached is not None and cached[0] == token:
            return cached[1]
        if cached is not None and cached[0][0] != token[0]:
            self._intent_cache.clear()
            self._outcomes_cache.clear()
        if encounter is None or encounter_scope is None:
            # Never decode a global portfolio until a canonical baseline or verified reference
            # establishes the exact compiled enemy revision for this encounter.
            self._compatible_ids_cache = (token, set())
            return set()
        # An experiment's frozen intent is immutable. Adding a new experiment (including one
        # for another encounter) must not revalidate every historical scenario. Extend only
        # across a local, monotonic id high-water change; external commits and any semantic
        # scope change still take the full fail-closed validation path above.
        incremental = (cached is not None and cached[0][0] == token[0]
                       and cached[0][2:] == token[2:]
                       and int(token[1] or 0) >= int(cached[0][1] or 0))
        ids = set(cached[1]) if incremental else set()
        compatibility_digests = self._compatible_search_digests()
        marks = ','.join('?' for _ in compatibility_digests)
        # SQL narrows by frozen compatibility metadata; the canonical compressed-intent reader then
        # validates actual candidate/reference scenario payloads before admitting an experiment.
        sql = (
            'SELECT e.id '
            'FROM ea_experiment AS e WHERE e.mechanics_revision=? '
            "AND json_extract(e.intent_json, '$.compatibility') IN (%s)" % marks
        )
        params = (self._mechanics_revision(), *compatibility_digests)
        if incremental:
            sql += ' AND e.id>?'
            params += (int(cached[0][1] or 0),)
        if encounter_scope is not None:
            sql += ' AND e.encounter_revision=?'
            params += (encounter_scope,)
        for row in db.execute(sql, params):
            experiment_id = int(row[0])
            if self._intent_matches_live(self._intent(db, experiment_id) or {}):
                ids.add(experiment_id)
        self._compatible_ids_cache = (token, ids)
        return ids

    def _deduped_outcomes(self, db, experiments=None):
        """Outcomes per candidate with every COALESCED sample counted once.

        A shared sample linked to several experiments appears once per ``ea_sample_link`` row;
        aggregating links would double-count one battle, so dedupe by (candidate, sample identity)
        before any mean, standard error, portfolio or adviser use. When ``experiments`` is given,
        only outcomes from those (compatibility-filtered) experiments are admitted, so an observation
        from an incompatible policy/revision is never restamped into the current view.
        """
        by_candidate = {}
        seen = set()
        # Filter BEFORE reading/decoding results. Keep completed finite experiments warm across
        # harvests; their ledger counters change on reuse/completion, including out-of-order jobs.
        # This bounds reads by compatible studies instead of the entire library every pass.
        if experiments is None:
            source = ledger.outcomes(db)
        else:
            source = []
            experiment_ids = sorted(experiments)
            compatible_ids = set(experiment_ids)
            # Only the active compatible set is useful to this scoped cache. In particular, do
            # not retain a prior scope's rows when a policy/revision change selects a new set.
            if len(self._outcomes_cache) > len(compatible_ids) or any(
                    experiment_id not in compatible_ids for experiment_id in self._outcomes_cache):
                self._outcomes_cache = {
                    experiment_id: cached
                    for experiment_id, cached in self._outcomes_cache.items()
                    if experiment_id in compatible_ids
                }

            # Fetch freshness tokens in bounded batches. The old per-experiment point read left
            # thousands of tiny SQLite calls on every warm pass even when every outcome was cached.
            budget_by_experiment = {}
            for offset in range(0, len(experiment_ids), 800):
                batch = experiment_ids[offset:offset + 800]
                marks = ','.join('?' for _ in batch)
                sql = ('SELECT experiment_id,reserved,completed FROM ea_experiment_budget '
                       'WHERE experiment_id IN (%s)' % marks)
                for row in db.execute(sql, batch):
                    budget_by_experiment[row[0]] = (row[1], row[2])

            for experiment_id in experiment_ids:
                token = budget_by_experiment.get(experiment_id)
                cached = self._outcomes_cache.get(experiment_id)
                if cached is None or cached[0] != token:
                    cached = (token, ledger.outcomes(db, experiment_id))
                    self._outcomes_cache[experiment_id] = cached
                source.extend(cached[1])
        for row in source:
            if row['state'] != 'completed':
                continue
            if experiments is not None and row['experimentId'] not in compatible_ids:
                continue
            identity = (row['candidateId'],
                        row.get('sampleKey') or tuple(row.get('seedPair') or ()))
            if identity in seen:
                continue
            seen.add(identity)
            by_candidate.setdefault(row['candidateId'], []).append(row['outcome'])
        return by_candidate

    @staticmethod
    def _standard_error(aggregate):
        """Sample SD divided by sqrt(n); None when there are fewer than two readings."""
        resolved = aggregate.get('resolvedCount') or 0
        stdev = aggregate.get('stdev')
        if resolved < 2 or stdev is None:
            return None
        return stdev / math.sqrt(resolved)

    def _observation_entry(self, cid, aggregate, *, meanEarned, features=None,
                           compatibility=None, window='development'):
        resolved = aggregate['resolvedCount']
        se = self._standard_error(aggregate)
        entry = dict(candidateId=cid, resolved=resolved, samples=resolved,
                     total=aggregate['total'], unknownCount=aggregate.get('unresolvedCount'),
                     standardError=se, uncertainty=dict(kind='SE', value=se),
                     reliability=(resolved / float(aggregate['total'])) if aggregate['total'] else None,
                     lossFrequency=aggregate.get('lossFrequency'),
                     median=aggregate.get('median'))
        if features is not None:
            entry['features'] = features
        if compatibility is not None:
            entry['compatibility'] = compatibility
        if meanEarned is not None:
            entry['meanEarned'] = meanEarned
            entry['arithmeticMeanEarned'] = meanEarned
        entry['window'] = window
        return entry

    def _observations(self, db, owner=None):
        compatibility = self._compatibility()
        by_candidate = self._deduped_outcomes(db, experiments=self._compatible_experiment_ids(db))
        owner_of = {str(row.get('candidateId')): row.get('owner')
                    for row in self.state.get('history') or []}
        observations = []
        for cid, rows in by_candidate.items():
            key = str(cid)
            if owner is not None and owner_of.get(key) != owner:
                continue
            aggregate = outcomes_service.summarize(rows)
            features = (self.state.get('features') or {}).get(key)
            if not features or aggregate['meanEarned'] is None:
                continue
            observations.append(self._observation_entry(
                cid, aggregate, meanEarned=aggregate['meanEarned'], features=features,
                compatibility=compatibility))
        return observations

    @staticmethod
    def _adviser_key(owner):
        return str(owner) if owner is not None else 'community'

    def _candidate_owner(self, db, candidate_id):
        """The honest stream/family a candidate belongs to, resolved from its lineage row.

        A supplied baseline (lineage source ``origin``) or any row with no stream belongs to the
        community-first lane; a real student/stream source is returned unchanged so a nominee is never
        relabelled as another family's work.
        """
        source = None
        for sql in ('SELECT source FROM lineage WHERE candidate=?',
                    'SELECT source FROM candidate WHERE id=?'):
            try:
                row = db.execute(sql, (str(candidate_id),)).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row is not None and row[0]:
                source = row[0]
                break
        if source in OWNERLESS_SOURCES:
            return students.STUDENT_COMMUNITY
        return str(source or students.STUDENT_COMMUNITY)

    def _legacy_candidate_ids(self, db, owner=None):
        """Bounded, compatible candidate ids drawn from the REAL legacy library.

        The reference comes first, then the newest mature candidates of this encounter. A candidate
        produced by the blind Stumble family is excluded; a Discovery candidate is NOT vetoed. Owner
        budget and evidence family are kept distinct: any compatible Community-admitted historical
        candidate may be reused whatever stream produced it, and its original lineage stays the
        attribution. Identities come from candidate/candidate_meta/lineage, never from the new EA
        history, so a migrated mature library with an empty EA state still yields priors.
        """
        ids, seen = [], set()

        def add(cid):
            cid = str(cid)
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)

        reference_row = db.execute('SELECT scenario FROM candidate WHERE id=?',
                                   (self.reference,)).fetchone() if self.reference else None
        reference = json.loads(reference_row[0]) if reference_row else None
        # A compact imported library has no old ``aggregate:<id>:validation`` chest count; its
        # coverage/strong-history signal is the retained ``history_build_summary`` sample count.
        # Selection stays bounded (256 scanned, at most 24 admitted/read) and never filters on mean
        # reward. Keep the non-null encounter predicate as a direct equality: wrapping the indexed
        # column in ``(? IS NULL OR ...)`` makes SQLite scan candidates before it can sort the small
        # encounter-local set.
        if self.encounter is None:
            encounter_filter = '(? IS NULL OR m.encounter=?)'
            encounter_args = (None, None)
        else:
            encounter_filter = 'm.encounter=?'
            encounter_args = (self.encounter,)
        try:
            compact = db.execute(
                "SELECT 1 FROM meta WHERE key='historySummaryVersion'").fetchone() is not None
        except sqlite3.OperationalError:
            compact = False
        try:
            if compact:
                rows = db.execute(
                    'SELECT c.id, m.encounter, '
                    '(SELECT l.source FROM lineage l WHERE l.candidate=c.id '
                    'ORDER BY l.rowid DESC LIMIT 1), c.scenario, '
                    'COALESCE((SELECT MAX(s.sample_count) FROM history_build_summary s '
                    'WHERE s.candidate_id=c.id),0) AS coverage FROM candidate c '
                    'LEFT JOIN candidate_meta m ON m.id = c.id '
                    'WHERE %s '
                    'ORDER BY (c.id=?) DESC, coverage DESC, '
                    'COALESCE((SELECT MAX(s.resolved_count) FROM history_build_summary s '
                    'WHERE s.candidate_id=c.id),0) DESC, c.created DESC, c.id '
                    'LIMIT ?' % encounter_filter,
                    (*encounter_args, self.reference, LEGACY_CANDIDATE_SCAN)).fetchall()
            else:
                rows = db.execute(
                    'SELECT c.id, m.encounter, '
                    '(SELECT l.source FROM lineage l WHERE l.candidate=c.id '
                    'ORDER BY l.rowid DESC LIMIT 1), c.scenario FROM candidate c '
                    'LEFT JOIN candidate_meta m ON m.id = c.id '
                    'LEFT JOIN meta a ON a.key = \'aggregate:\' || c.id || \':validation\' '
                    'WHERE %s '
                    'ORDER BY (c.id=?) DESC, '
                    'COALESCE(json_extract(a.value, \'$.chestCount\'),0) DESC, c.created DESC,c.id '
                    'LIMIT ?' % encounter_filter,
                    (*encounter_args, self.reference, LEGACY_CANDIDATE_SCAN)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            cid, encounter, source = row[0], row[1], row[2]
            if source == students.STUDENT_STUMBLING:
                continue  # the blind family's evidence is never read as a prior
            if (encounter is not None and self.encounter is not None
                    and int(encounter) != int(self.encounter)):
                continue
            scenario = json.loads(row[3])
            if (self.mode == 'community-first' or owner in (None, students.STUDENT_COMMUNITY)):
                if not students.community_admits(scenario, reference)[0]:
                    continue
            if self.constraints is not None:
                import strategy_build_domain as domain_service
                if not domain_service.validate_constraints(scenario, self.constraints).get('valid'):
                    continue
            add(cid)
            # ``load_observations`` consumes only this ordered prefix. Do not run costly placement
            # admission checks on candidates it will never read.
            if len(ids) >= LEGACY_MAXIMUM_CANDIDATES:
                break
        return ids

    def _legacy_observations(self, db, owner):
        """Bounded compatible historical priors, or ``[]``.

        Selects at most 24 candidate ids from the REAL legacy candidate/meta/lineage tables (reference
        + appropriate mature candidates), reads <=256 rows each through the shared read-only reader,
        admits only the candidates the reader proves encounter/policy/constraints/engine compatible,
        excludes the blind Stumble family (Discovery is not vetoed), and caches per (owner context,
        compatibility). Historical observations guide the adviser and remain labelled, unconfirmed
        earning options after the reader proves an equivalent engine/policy basis. They are never
        pooled with fresh development or substituted for independent holdout evidence. The budget owner
        (``owner``) and the evidence family are distinct: a compatible Community-admitted historical
        candidate is reused with its ORIGINAL lineage attribution.
        """
        key = (self._adviser_key(owner), self._compatibility())
        if key in self._legacy_cache:
            return self._legacy_cache[key]
        entries = []
        try:
            candidate_ids = self._legacy_candidate_ids(db, owner)
        except Exception as exc:  # noqa: BLE001 - a failed selection is reported, never fabricated
            candidate_ids = []
            self._note('historical prior selection unavailable: %s' % (exc,))
        if candidate_ids:
            try:
                import strategy_legacy_observations as legacy
                from strategy_optimizer_adapter import provenance
                report = legacy.load_observations(
                    db, candidate_ids,
                    encounter=self.encounter, policy=self.state.get('policy'),
                    mechanics_revision=self._mechanics_revision(),
                    engine_revision=provenance(), constraints=self.constraints,
                    maximum_candidates=LEGACY_MAXIMUM_CANDIDATES,
                    rows_per_candidate=LEGACY_ROWS_PER_CANDIDATE,
                    cache=self._legacy_read_cache)
                compatibility = report.get('compatibility') or {}
                if compatibility.get('compatible'):
                    live = self._compatibility()
                    budget_owner = self._adviser_key(owner)
                    for observation in report.get('observations') or []:
                        if observation.get('adviserEligible') and observation.get('meanEarned') is not None:
                            cid = str(observation.get('candidateId'))
                            family = self._candidate_owner(db, cid)
                            entries.append(dict(observation, compatibility=live,
                                                evidenceClass='historical-prior',
                                                independentConfirmation=False,
                                                confirmationEligible=False,
                                                evidenceFamily=family, budgetOwner=budget_owner))
                else:
                    self._note('historical priors refused: %s' % (compatibility.get('reason'),))
            except Exception as exc:  # noqa: BLE001 - an optional prior is reported, never fabricated
                self._note('historical priors unavailable: %s' % (exc,))
        self._legacy_cache[key] = entries
        return entries

    def _fit_adviser(self, db, owner=None):
        observations = list(self._observations(db, owner))
        # A fresh development view of a candidate wins over its own prior: a prior is never pooled
        # with the new evidence for the same identity, and owner contexts are never mixed.
        development = {str(o['candidateId']) for o in observations}
        for prior in self._legacy_observations(db, owner):
            if str(prior.get('candidateId')) in development:
                continue
            observations.append(prior)
        signature = _digest([[o['candidateId'], o['resolved'], o['meanEarned'],
                              o.get('evidenceClass') or 'development']
                             for o in sorted(observations, key=lambda o: str(o['candidateId']))])
        key = self._adviser_key(owner)
        advisers = self.state.setdefault('advisers', {})
        signatures = self.state.setdefault('adviserSignatures', {})
        if signature == signatures.get(key) and key in advisers:
            return advisers.get(key)
        model = adviser.fit(observations, compatibility=self._compatibility(),
                            previous=advisers.get(key))
        advisers[key] = model
        signatures[key] = signature
        # Back-compat for report consumers that read the single default model signature.
        if self.state.get('adviser') is None or key == 'community':
            self.state['adviser'] = model
        self.state['adviserSignature'] = _digest(sorted(signatures.items()))
        return model

    def _rank(self, pool, owner=None):
        # Owner-contextual: only this owner's own fitted model is ever consulted (a Community model
        # never reorders another stream and vice versa).
        model = (self.state.get('advisers') or {}).get(self._adviser_key(owner))
        if not model or model.get('status') != 'fitted':
            return list(pool)
        return adviser.rank(model, pool, round_index=int(self.state.get('roundIndex') or 0),
                            coverage_slots=1)

    def _compatibility(self):
        if self._compat is None:
            self._compat = self._compatibility_for_revision(self.revision)
        return self._compat

    def _mechanics_revision(self):
        if self._mech is None:
            try:
                self._mech = mechanics.dependency_digest()
            except Exception:  # noqa: BLE001 - a missing digest is reported, never fabricated
                return None
        return self._mech

    def _freeze_policy(self, scenario):
        """The EXACT policy frozen from the actual scenario handed to the worker.

        Never a hardcoded ``{'finishPolicy': 'on-verdict'}``: the Finish rule, horizon and declared
        consumable stock come from the observed scenario and are frozen across matched pairs.
        """
        return json.loads(json.dumps({key: scenario[key] for key in POLICY_KEYS if key in scenario}))

    def _encounter_revision(self, scenario):
        """The compiled encounter revision digest, never the integer encounter id."""
        key = (self.revision, self._mechanics_revision(), _digest(scenario))
        cached = self._encounter_revision_cache.get(key)
        if cached is not None:
            return cached
        try:
            from strategy_encounter_revision_native import native_revision
            revision = native_revision(scenario)
            if revision is None:
                import strategy_encounter_compiler as compiler
                compiled = compiler.compile_encounter(scenario)
                revision = compiled.get('revision') or {}
            digest = revision.get('digest')
        except Exception as exc:  # noqa: BLE001 - reported, never fabricated
            self._note('encounter revision unavailable: %s' % (exc,))
            return None
        if digest is not None:
            self._memo_encounter_revision(scenario, digest)
        return digest

    def _memo_encounter_revision(self, scenario, digest):
        """Reuse a canonical compiled revision on the exact loaded-engine/mechanics/view key.

        Admission calls this only after all prepared-packet bindings match. Dispatch still runs
        its normal live compatibility checks; it can reuse the worker's canonical compilation.
        """
        key = (self.revision, self._mechanics_revision(), _digest(scenario))
        known = self._encounter_revision_cache.get(key)
        if known is not None and known != digest:
            raise ValueError('prepared encounter revision conflicts with the canonical memo')
        if len(self._encounter_revision_cache) > 256:
            self._encounter_revision_cache.clear()
        self._encounter_revision_cache[key] = digest

    def _note(self, message):
        if message not in self.limitations:
            self.limitations.append(message)

    # -- portfolio and confirmation ---------------------------------------------------------------
    def _presentation(self, reference_scenario, reference_id):
        """Cached, pure presentation view (never full scenarios or the job list).

        Recomputed only when the reference id or the frozen build constraints change, so a long
        session does not re-derive the reduced-model explanation on every report.
        """
        if not isinstance(reference_scenario, dict):
            return self.state.get('presentation')
        key = _digest(dict(referenceId=reference_id, constraints=self.constraints))
        cached = self.state.get('presentationCache') or {}
        if cached.get('key') == key and cached.get('value') is not None:
            return cached['value']
        try:
            value = presentation_service.describe(reference_scenario, self.constraints,
                                                  reference_id=reference_id)
        except Exception as exc:  # noqa: BLE001 - a refused view must not stop the cycle
            self._note('presentation describe failed: %s' % (exc,))
            return self.state.get('presentation')
        self.state['presentationCache'] = {'key': key, 'value': value}
        self.state['presentation'] = value
        return value

    def _refresh_portfolio(self, db):
        # Every row retains its own evidence window. Compatible historical builds and the measured
        # reference remain earning options; a poor child must not replace a stronger incumbent.
        by_candidate = self._deduped_outcomes(db, experiments=self._compatible_experiment_ids(db))
        features = self.state.get('features') or {}
        domains = self.state.get('domains') or {}
        built = {row['candidateId']: row for row in self.state.get('history') or []}
        paired_by_candidate = {}
        for row in self.state.get('paired') or []:
            paired_by_candidate.setdefault(str(row.get('candidateId')), []).append(row)
        nominee = (self.state.get('confirmation') or {}).get('nominee')
        portfolio = []
        maxima = {}
        for cid, rows in by_candidate.items():
            if str(cid) not in features:
                continue
            aggregate = outcomes_service.summarize(rows)
            entry = self._observation_entry(cid, aggregate, meanEarned=aggregate['meanEarned'],
                                            features=features.get(str(cid)), window='development')
            entry['key'] = cid
            entry['label'] = cid
            entry['fullMean'] = aggregate['meanEarned']
            entry['partialMean'] = aggregate['partialMean']
            entry['arithmeticMeanEarned'] = aggregate['partialMean']
            entry['eligible'] = aggregate.get('eligibleForRecommendation')
            entry['development'] = True
            entry['confirmation'] = (cid == nominee)
            record = built.get(cid) or {}
            entry['owner'] = record.get('owner') or 'community'
            domain = domains.get(str(cid)) or {}
            # Truthful domain provenance, kept SEPARATE from sample resolution: `synthetic` is the
            # declared synthetic search-contract domain, `playerUnknown` marks a player/unrestricted
            # build whose real-player reachability is unverified. Neither is derived from unresolved
            # sample counts (`unknownCount` stays its own honest field).
            entry['synthetic'] = bool(domain.get('synthetic'))
            entry['playerUnknown'] = bool(domain.get('playerUnknown'))
            entry['domainContext'] = domain.get('context')
            entry['provenanceStatus'] = domain.get('provenanceStatus')
            entry['expectedEarned'] = (built.get(cid) or {}).get('expectedEarned')
            entry['arm'] = 'candidate'
            entry['pairedComparisons'] = paired_by_candidate.get(str(cid))
            # Surface the quantiles / zero / loss / resource statistics the canonical aggregator
            # already computed; no number is invented here.
            entry['quantiles'] = aggregate.get('quantiles')
            entry['min'] = aggregate.get('min')
            entry['max'] = aggregate.get('max')
            entry['zeroCount'] = aggregate.get('zeroCount')
            entry['zeroFrequency'] = aggregate.get('zeroFrequency')
            entry['lossCount'] = aggregate.get('lossCount')
            entry['resourceCount'] = aggregate.get('resourceCount')
            entry['resourceMean'] = aggregate.get('resourceMean')
            entry['resourceFrequency'] = aggregate.get('resourceFrequency')
            # P(earned >= K) is reported ONLY for explicit user thresholds; a missing denominator is
            # left as None rather than drawn as a zero probability.
            thresholds = self.state.get('thresholds') or self.thresholds
            if thresholds:
                earned = [r.get('finalEarned') for r in rows
                          if r.get('finalEarned') is not None]
                denominator = len(earned)
                entry['thresholdProbabilities'] = {
                    str(int(k)): dict(
                        count=sum(1 for value in earned if value >= int(k)),
                        denominator=denominator,
                        probability=(sum(1 for value in earned if value >= int(k)) / denominator
                                     if denominator else None))
                    for k in thresholds}
            portfolio.append(entry)
            numeric = [r.get('finalEarned') for r in rows if r.get('finalEarned') is not None]
            maxima[cid] = max(numeric) if numeric else None
        incumbent = self._incumbent_evidence(db, by_candidate)
        if incumbent is not None and not any(row['candidateId'] == incumbent['candidateId']
                                             for row in portfolio):
            portfolio.append(incumbent)
        owners = (['community'] if self.mode == 'community-first' else
                  [owner for owner, share in (self.config.get('allocations') or {}).items() if share > 0])
        retained_ids = set(by_candidate)
        for owner in owners:
            if owner in (students.STUDENT_STUMBLING, students.STREAM_DISCOVERY):
                continue
            for prior in self._legacy_observations(db, owner):
                cid = str(prior['candidateId'])
                if cid in retained_ids or not prior.get('eligibleForRecommendation'):
                    continue
                retained_ids.add(cid)
                portfolio.append(dict(prior, key=cid, label=cid, owner=owner, arm='candidate',
                                      eligible=True, development=False, confirmation=False,
                                      evidenceClass='historical-unconfirmed',
                                      independentConfirmation=False, confirmationEligible=False,
                                      fullMean=prior['meanEarned']))
                self.state.setdefault('features', {})[cid] = dict(prior.get('features') or {})
        portfolio.sort(key=lambda row: (row.get('meanEarned') is None,
                                        -(row.get('meanEarned') or 0.0), row['candidateId']))
        self.state['portfolio'] = portfolio
        # The mechanism/diversity archive is kept separate: a high single maximum never evicts the
        # earning portfolio's better mean candidate.
        self.state['maxima'] = maxima
        return incumbent

    def _incumbent_evidence(self, db, outcomes_by_candidate=None):
        """The reference/incumbent's own canonical distinct DEVELOPMENT evidence, or None.

        The reference has no proposal features, but remains an earning option when children fail
        to beat it. This reads the same deduped compatible development outcomes the portfolio uses
        and reports the incumbent as an explicit `arm='reference'` row. It is NEVER a confirmation
        of itself: `_maybe_freeze_confirmation` refuses to nominate it.
        """
        reference_id = self.reference
        if reference_id is None:
            return None
        compatibility = self._compatibility()
        if outcomes_by_candidate is None:
            outcomes_by_candidate = self._deduped_outcomes(
                db, experiments=self._compatible_experiment_ids(db))
        rows = outcomes_by_candidate.get(str(reference_id))
        if not rows:
            return None
        aggregate = outcomes_service.summarize(rows)
        if aggregate.get('meanEarned') is None or aggregate.get('resolvedCount', 0) < CONFIRM_MIN_DEVELOPMENT:
            return None
        entry = self._observation_entry(reference_id, aggregate,
                                        meanEarned=aggregate['meanEarned'], window='development')
        entry['candidateId'] = str(reference_id)
        entry['key'] = str(reference_id)
        entry['arm'] = 'reference'
        entry['incumbent'] = True
        entry['eligible'] = aggregate.get('eligibleForRecommendation')
        # Resolve the incumbent's OWNER honestly from its lineage rather than assuming Community: a
        # supplied baseline has no stream and belongs to the community-first lane, while a reference
        # owned by another stream keeps that name. It is offered as a historical-unconfirmed EARNING
        # option and is never restamped as a fresh confirmation.
        entry['owner'] = self._candidate_owner(db, reference_id)
        entry['evidenceClass'] = 'development-reference'
        entry['independentConfirmation'] = False
        entry['confirmationEligible'] = False
        entry['features'] = None
        entry['compatibility'] = compatibility
        return entry

    def _maybe_freeze_confirmation(self, db, store, *, incumbent=None,
                                   incumbent_precomputed=False):
        if self.state.get('confirmation') and self.state['confirmation'].get('frozen'):
            return
        # No holdout may be frozen while ANY cohort development job is still outstanding: the
        # nomination must follow every member's measured evidence, not a partially drained batch.
        if self._cohort():
            return
        eligible = [row for row in self.state.get('portfolio') or []
                    if row.get('meanEarned') is not None
                    and row.get('resolved', 0) >= CONFIRM_MIN_DEVELOPMENT]
        if not incumbent_precomputed:
            incumbent = self._incumbent_evidence(db)
        if incumbent is not None:
            eligible = [row for row in eligible
                        if str(row.get('candidateId')) != incumbent['candidateId']]
            eligible.append(incumbent)
            eligible.sort(key=lambda row: (-(row.get('meanEarned') or 0.0), row['candidateId']))
        if not eligible:
            return
        nominee = eligible[0]
        if nominee.get('incumbent') or nominee.get('arm') == 'reference':
            self._note('retained incumbent/reference remains the best admissible development '
                       'evidence; no demonstrated improvement to confirm.')
            return
        owner = nominee.get('owner') or 'community'
        if self.mode == 'community-first':
            budgets = self._budget_remaining(db)
            unavailable = self.state.get('unspendable') or []
            if any(value >= 2 and purpose not in unavailable for purpose, value in budgets.items()
                   if purpose != 'comparison'):
                return
            remaining = budgets.get('comparison', 0)
        else:
            remaining_map = self._task_remaining(db)
            tasks = self.state.get('unspendableTasks') or []
            if any(value >= 2 and [other, purpose] not in tasks
                   for (other, purpose), value in remaining_map.items() if purpose != 'comparison'):
                return
            # The frozen confirmation spends the NOMINEE OWNER's comparison budget only.
            remaining = remaining_map.get((owner, 'comparison'), 0)
        # The smallest frozen comparison is two pairs (four runs); fewer leaves it unspent.
        if remaining < 4:
            return
        reference_id = self.reference
        if not reference_id or reference_id == nominee['candidateId']:
            reference_id = self._legacy_reference(store)
        if not reference_id or reference_id == nominee['candidateId']:
            return
        pairs = self._fresh_pairs(store, [nominee['candidateId'], reference_id],
                                  max(2, min(CONFIRM_PAIRS, remaining // 2)))
        if not pairs:
            if not self._seed_history_ready(store):
                self._preserve_for_readiness('comparison', owner)
                return
            self._note('no fresh collision-checked holdout pairs available')
            return
        plan = [dict(candidateId=nominee['candidateId'], seeds=[list(p) for p in pairs]),
                dict(candidateId=reference_id, seeds=[list(p) for p in pairs])]
        runs = sum(len(entry['seeds']) for entry in plan)
        observed_pair = self._confirmation_scenarios(
            db, (nominee['candidateId'], reference_id), store)
        nominee_observed = observed_pair[str(nominee['candidateId'])]
        reference_observed = observed_pair[str(reference_id)]
        if nominee_observed is None or reference_observed is None:
            self._note('confirmation unavailable: no unique compatible frozen development '
                       'scenario for the nominee/reference; saved evidence retained')
            return
        policy = self._freeze_policy(nominee_observed or {})
        reference_observed = self._align_policy(reference_observed, policy)
        nomination = confirmation_service.freeze(
            nominee=nominee['candidateId'], reference=reference_id, pairs=pairs, policy=policy,
            encounter_revision=self._encounter_revision(nominee_observed),
            mechanics_revision=self._mechanics_revision(), engine_revision=self.revision)
        owner_share = (1.0 if self.mode == 'community-first'
                       else float((self.config.get('allocations') or {}).get(owner, 0.0)))
        intent = dict(scope=owner, owner=owner, ownerShare=owner_share, purpose='comparison',
                      sessionId=self.session_id, planned_budget=runs,
                      stopping={'maxRuns': runs,
                                'rule': 'fixed sample; report inconclusive if unresolved'},
                      policy=policy,
                      mechanicsRevision=self._mechanics_revision(),
                      encounterRevision=self._encounter_revision(nominee_observed),
                      engineRevision=self.revision, compatibility=self._compatibility(),
                      measurementWindow='confirmation', proposalId='confirmation',
                      nomination=nomination,
                      observedScenarios={nominee['candidateId']: nominee_observed,
                                         reference_id: reference_observed})
        experiment_id = ledger.create_experiment(db, intent)
        ledger.freeze_confirmation(db, experiment_id, nominee['candidateId'], reference_id,
                                   policy, 'finalEarned', plan)
        self.state['confirmation'] = dict(experimentId=experiment_id, frozen=True, ready=False,
                                          owner=owner, scope=owner,
                                          nominee=nominee['candidateId'], reference=reference_id,
                                          planned=runs, completed=0, metric='finalEarned',
                                          pairs=[list(p) for p in pairs], policy=policy,
                                          nomination=nomination)
        # The comparison budget is charged at freeze time; refresh the cached rows.
        self._budget_dirty = True
        self._persist(store)
        store.db.commit()

    def _confirmation_projection_rows(self, db, compatible_ids, candidate_ids):
        """Batch-read a bounded, pair-scoped view without populating the full-intent cache."""
        token = self._compatible_ids_cache[0]
        ordered_ids = tuple(sorted(compatible_ids))
        requested = tuple(sorted(set(candidate_ids)))
        scope = (token, ordered_ids, requested)
        # Older integrations and bounded __new__-based readers may not have run __init__.
        cache = getattr(self, '_confirmation_projection_cache', None)
        if cache is None or cache['connection'] is not db or cache['scope'] != scope:
            cache = dict(connection=db, scope=scope, rows={}, bytes=0)
            self._confirmation_projection_cache = cache

        missing = [experiment_id for experiment_id in ordered_ids
                   if experiment_id not in self._intent_cache
                   and experiment_id not in cache['rows']]
        if not missing:
            # A complete canonical cache is already the cheapest exact reader. The partial cache
            # is useful only while at least one pair row remains outside the full cache.
            return not all(experiment_id in self._intent_cache for experiment_id in ordered_ids)
        if len(missing) < 2:
            return False
        if len(cache['rows']) + len(missing) > CONFIRMATION_PROJECTION_ROW_LIMIT:
            self._confirmation_projection_cache = None
            return False

        try:
            variable_limit = int(db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
        except (AttributeError, TypeError, ValueError):
            variable_limit = CONFIRMATION_PROJECTION_BATCH_MAX
        batch_size = max(1, min(CONFIRMATION_PROJECTION_BATCH_MAX, variable_limit))
        new_rows = {}
        new_bytes = 0
        try:
            for offset in range(0, len(missing), batch_size):
                batch = missing[offset:offset + batch_size]
                marks = ','.join('?' for _ in batch)
                sql = ('SELECT id,intent_json,full_intent FROM ea_experiment '
                       'WHERE id IN (%s) ORDER BY id' % marks)
                batch_rows = {}
                batch_bytes = 0
                for experiment_id, metadata, full_blob in db.execute(sql, batch):
                    raw = intent_codec.decode_intent(metadata, full_blob)
                    view, retained_bytes = _confirmation_projection_view(raw, requested)
                    batch_rows[int(experiment_id)] = view
                    batch_bytes += retained_bytes
                if len(new_rows) + len(batch_rows) > CONFIRMATION_PROJECTION_ROW_LIMIT:
                    raise _ConfirmationProjectionUnsupported('projection row bound exceeded')
                if cache['bytes'] + new_bytes + batch_bytes > CONFIRMATION_PROJECTION_BYTE_LIMIT:
                    raise _ConfirmationProjectionUnsupported('projection byte bound exceeded')
                # Publish no partial batch. A later failed batch discards the entire scoped view.
                new_rows.update(batch_rows)
                new_bytes += batch_bytes
            version = int(db.execute('PRAGMA data_version').fetchone()[0])
            if version != token[0]:
                raise _ConfirmationProjectionUnsupported('database changed during projection')
        except Exception:  # noqa: BLE001 - preserve the canonical reader's cached-None behavior
            self._confirmation_projection_cache = None
            return False
        cache['rows'].update(new_rows)
        cache['bytes'] += new_bytes
        return True

    def _confirmation_projection_intent(self, db, experiment_id, candidate_ids, projected):
        # A full cached value, including None, is authoritative. Partial rows remain invisible to
        # every other consumer and are never inserted into _intent_cache.
        if experiment_id in self._intent_cache:
            return self._intent_cache[experiment_id]
        cache = self._confirmation_projection_cache
        if projected and cache is not None and experiment_id in cache['rows']:
            return cache['rows'][experiment_id]
        return self._intent(db, experiment_id)

    def _confirmation_scenarios(self, db, candidate_ids, store=None):
        """Recover both frozen arms from one bounded projection, or use the canonical full reader.

        Confirmation never reconstructs a different build from mutable library settings. Digest
        conflicts fail closed before encounter-revision filtering, and holdout authorization remains
        in the caller before this read.
        """
        # Preserve caller order: the old single-candidate reader scans the nominee completely
        # before it begins the reference scan, including its fail-fast conflict boundary.
        requested = tuple(dict.fromkeys(str(candidate_id) for candidate_id in candidate_ids))
        found = {candidate_id: None for candidate_id in requested}
        digests = {candidate_id: None for candidate_id in requested}
        conflicted = set()
        compatible = self._compatible_experiment_ids(db)
        projected = self._confirmation_projection_rows(db, compatible, requested)
        for candidate_id in requested:
            for experiment_id in sorted(compatible):
                intent = self._confirmation_projection_intent(
                    db, experiment_id, requested, projected) or {}
                if intent.get('measurementWindow') != 'development':
                    continue
                scenarios = intent.get('observedScenarios') or {}
                scenario = scenarios.get(candidate_id)
                if not isinstance(scenario, dict):
                    continue
                value = _digest(scenario)
                if digests[candidate_id] is not None and digests[candidate_id] != value:
                    conflicted.add(candidate_id)
                    found[candidate_id] = None
                    break
                if intent.get('encounterRevision') != self._encounter_revision(scenario):
                    continue
                found[candidate_id], digests[candidate_id] = scenario, value
            if (found[candidate_id] is None and candidate_id not in conflicted
                    and store is not None):
                # A supplied comparator may not have a development parent. Its exact resident build
                # remains eligible only while it belongs to this encounter. Resolve each arm before
                # scanning the next one, matching the former nominee-then-reference call ordering.
                resident = store.scenario(candidate_id)
                if isinstance(resident, dict) and resident.get('encounterId') == self.encounter:
                    found[candidate_id] = store.observed_scenario(resident, candidate=candidate_id)
        return found

    def _confirmation_scenario(self, db, candidate_id, store=None):
        """Compatibility wrapper for one requested arm."""
        key = str(candidate_id)
        return self._confirmation_scenarios(db, (key,), store).get(key)

    def _legacy_reference(self, store):
        """The encounter's supplied Community cast as the confirmation comparator.

        Explicitly the `supplied` baseline, NOT `community_parent`: the latter ranks by legacy
        aggregates and would pick the fresh joint nominee itself (which has no legacy runs), making
        the comparison a candidate against itself.
        """
        if self.encounter is None:
            return None
        rows = store.db.execute(
            "SELECT c.id AS cid, c.scenario AS scenario FROM candidate c "
            "JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=? AND c.source='supplied' "
            "ORDER BY c.created LIMIT 4", (int(self.encounter),)).fetchall()
        for row in rows:
            raw = row['scenario']
            scenario = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if students.community_admits(scenario)[0]:
                return row['cid']
        return None

    def _live_library_version(self, store):
        """The coordinator own change counter for the library, or None when unavailable.

        ``store.db`` is the single writer this coordinator owns, and SQLite ``data_version`` for a
        connection moves only when ANOTHER connection commits - an external write to the shared
        library - never on the coordinator own candidate/``ea_*`` writes. It is therefore the cheap,
        sound signal that a cached history snapshot must be re-read, while the live ``ea_*`` union in
        `_fresh_pairs` covers this coordinator own new reservations without a rescan.
        """
        db = getattr(store, 'db', None) if store is not None else None
        if db is None:
            return None
        try:
            return int(db.execute('PRAGMA data_version').fetchone()[0])
        except Exception:  # noqa: BLE001 - an unreadable version must never fabricate a cache hit
            return None

    def _seed_freshness(self, store=None):
        """Global legacy+EA forbidden seed pairs, or None when the read fails closed.

        Delegates to the completed `strategy_seed_freshness.collect`: every distinct evidence pair
        (all candidates, not just the nominee/reference), every deterministic bank, and every ea_*
        pair. The snapshot is read once per (revision, mechanics, session, path, live library version);
        live ``ea_*`` reservations are still unioned fresh by `_fresh_pairs`. A read error or an
        exceeded bounded deadline is surfaced as a limitation and the caller refuses to draw; it is
        never swallowed, never replaced by a narrower historic check, and never retried again for
        the same scope, so one failed full scan cannot repeat within a session tick.
        """
        import strategy_confirmation
        import strategy_seed_freshness as freshness
        key = (self.revision, self._mechanics_revision(), self.session_id, str(self.path),
               self._live_library_version(store))
        if key[-1] is not None and self._freshness_key == key:
            if self._freshness is not None:
                return self._freshness
            if self._freshness_blocked is not None:
                self._note('seed freshness failed closed: %s' % (self._freshness_blocked,))
                return None
        # Campaign encounters share one writer connection and one immutable historical seed
        # snapshot. Their own new ea_* reservations are added by _fresh_pairs below. Re-reading
        # the multi-gigabyte library for every encounter and every new session starves dispatch.
        host = self.planning_host
        db = getattr(store, 'db', None) if store is not None else None
        shared_key = (self.revision, key[1], str(self.path), key[-1])
        shared = getattr(host, '_seed_freshness_snapshot', None) if host is not None else None
        if (key[-1] is not None and shared is not None and shared.get('db') is db
                and shared.get('key') == shared_key):
            self._freshness = shared['pairs']
            self._freshness_report = shared['report']
            self._freshness_key = key
            self._freshness_blocked = None
            return self._freshness
        try:
            connection = strategy_confirmation.open_library_readonly(self.path)
        except Exception as exc:  # noqa: BLE001 - surface, never guess
            self._freshness = None
            self._freshness_report = None
            self._freshness_key = key
            self._freshness_blocked = str(exc)
            self._note('seed freshness unavailable: %s' % (exc,))
            return None
        try:
            # This call opens a one-shot connection, so the module-global cache cannot be reused on
            # the next call. Keep the returned compact set alive here without retaining a second
            # process-global copy of millions of historical seed pairs.
            report = freshness.collect(connection, cache={}, revision=self.revision,
                                       timeout_seconds=freshness.DEFAULT_TIMEOUT_SECONDS)
            # The collector validates every pair and returns a set of integer tuples. Rebuilding
            # that set here repeated a full Python pass (and duplicated its memory) on the cold
            # library scan, so retain the collector-owned set for read-only membership checks.
            pairs = report.get('pairs')
            if not isinstance(pairs, AbstractSet):
                raise TypeError('seed freshness collector did not return a pair set')
        except Exception as exc:  # noqa: BLE001 - fail closed, never a partial reserved set
            self._freshness = None
            self._freshness_report = None
            self._freshness_key = key
            self._freshness_blocked = str(exc)
            self._note('seed freshness failed closed: %s' % (exc,))
            return None
        finally:
            connection.close()
        coverage = (report.get('counts') or {}).get('coverage') or {}
        if coverage.get('partial'):
            self._note('seed-history coverage is partial: the imported compact history covers only '
                       'established exclusions, so missing historical seeds are not claimed fresh')
        self._freshness = pairs
        self._freshness_key = key
        self._freshness_blocked = None
        self._freshness_report = dict(cacheKey=report.get('cacheKey'), counts=report.get('counts'),
                                      provenance=report.get('provenance'), coverage=coverage)
        if host is not None and db is not None and key[-1] is not None:
            host._seed_freshness_snapshot = dict(
                db=db, key=shared_key, pairs=pairs, report=self._freshness_report)
        return pairs

    def _seed_history_ready(self, store=None):
        """Whether the forbidden-seed history is available for this scope; never re-scans a failure.

        A blocked read is remembered against the same library/session scope, so a failed full
        scan is not repeated within a session tick, and it is recorded as a READINESS block (never
        as spent or unspendable budget). A restart, a new session or a changed library produces a
        new key and retries exactly once.
        """
        ready = self._seed_freshness(store) is not None
        if ready:
            self._readiness_blocked = None
        else:
            reason = self._freshness_blocked or 'seed-history read unavailable'
            record = self._readiness_blocked or {}
            record.update(reason=reason, sessionId=self.session_id, revision=self.revision)
            self._readiness_blocked = record
            self._note('seed-history readiness blocked (%s)' % (reason,))
        return ready

    def _preserve_for_readiness(self, purpose, owner=None):
        """Attach a purpose to a readiness block without spending or forfeiting its budget.

        The purpose budget is left intact (never marked unspendable) so a restart or a changed
        library/session can retry, and the block is surfaced as a distinct limitation rather than
        being conflated with exhausted work. No candidate or reservation is created.
        """
        reason = self._freshness_blocked or 'seed-history read unavailable'
        record = self._readiness_blocked or {}
        record.update(purpose=purpose, owner=owner, reason=reason, sessionId=self.session_id,
                      revision=self.revision)
        self._readiness_blocked = record
        self._note('%s budget preserved: seed-history readiness blocked (%s)' % (purpose, reason))
        self.state['idle'] = True

    def _fresh_pairs(self, store, candidates, count, reserved_extra=None):
        """Fresh holdout pairs, collision-checked against the global historic + new ledger seeds.

        The historic check is the shared `strategy_seed_freshness` collector read-only over the
        whole library and the coordinator's own ea_* rows: it never launches a battle and never
        writes. A failed read refuses to draw (no silently narrower fallback).
        """
        import strategy_confirmation
        reserved = self._seed_freshness(store)
        if reserved is None:
            return []
        # Pairs already drawn for earlier members of the SAME cohort are not yet reserved in the
        # ledger, so they are folded in here to guarantee every member's ordered pairs are distinct.
        extra_pairs = set()
        for pair in reserved_extra or ():
            if pair and len(pair) == 2:
                extra_pairs.add((int(pair[0]), int(pair[1])))
        db = store.db
        host = self.planning_host
        snapshot = getattr(host, '_live_seed_pairs_snapshot', None) if host is not None else None
        # The shared historical snapshot already contains every ea_* pair present when it was
        # read. Ledger links and holdouts are append-only here, so only rows added by this writer
        # since the previous draw need reading. An external commit changes data_version, which
        # rebuilds _seed_freshness and this live snapshot before another draw.
        if (host is not None and snapshot is not None and snapshot.get('db') is db
                and snapshot.get('base') is self._freshness):
            for table, marker in (('ea_sample_link', 'linkRowid'),
                                  ('ea_holdout', 'holdoutRowid')):
                for rowid, seed_a, seed_b in db.execute(
                        'SELECT rowid, seed_a, seed_b FROM %s WHERE rowid>? ORDER BY rowid'
                        % table, (snapshot[marker],)):
                    snapshot['newPairs'].add((int(seed_a), int(seed_b)))
                    snapshot[marker] = int(rowid)
            live_pairs = snapshot['newPairs']
        elif host is not None:
            # A new historical snapshot already includes every ea_* row visible at its read
            # boundary. Start its append-only delta at the current rowids without a second
            # full table scan; later own reservations enter through the incremental query.
            host._live_seed_pairs_snapshot = dict(
                db=db, base=self._freshness, newPairs=set(),
                linkRowid=int(db.execute('SELECT COALESCE(MAX(rowid),0) '
                                         'FROM ea_sample_link').fetchone()[0]),
                holdoutRowid=int(db.execute('SELECT COALESCE(MAX(rowid),0) '
                                            'FROM ea_holdout').fetchone()[0]))
            live_pairs = host._live_seed_pairs_snapshot['newPairs']
        else:
            live_pairs = set()
            for row in db.execute('SELECT seed_a, seed_b FROM ea_sample_link'):
                live_pairs.add((int(row[0]), int(row[1])))
            for row in db.execute('SELECT seed_a, seed_b FROM ea_holdout'):
                live_pairs.add((int(row[0]), int(row[1])))

        class SeedExclusions:
            """Membership view for draw_seed_pairs, avoiding a full historical set copy."""

            def __contains__(self, pair):
                return pair in reserved or pair in live_pairs or pair in extra_pairs

            def __iter__(self):
                yield from reserved
                for pair in live_pairs:
                    if pair not in reserved:
                        yield pair
                for pair in extra_pairs:
                    if pair not in reserved and pair not in live_pairs:
                        yield pair
        state = int(hashlib.sha256(('holdout|%s|%s' % (
            self.encounter, '|'.join(str(c) for c in candidates))).encode('utf-8')).hexdigest()[:16], 16)

        def randbits(bits):
            nonlocal state
            state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
            return state >> (64 - bits)

        pairs, _rejected = strategy_confirmation.draw_seed_pairs(
            count, SeedExclusions(), randbits=randbits)
        return pairs

    def _pending_confirmation_jobs(self, db):
        confirmation = self.state.get('confirmation')
        if not confirmation or not confirmation.get('frozen') or confirmation.get('ready'):
            return []
        experiment_id = confirmation['experimentId']
        # Scheduling only needs the frozen-plan counts. Keep completed outcomes hidden and avoid
        # decoding them on the repeated dispatch gate; the full report belongs to publication.
        progress = ledger.confirmation_progress(db, experiment_id)
        confirmation['completed'] = progress['runsCompleted']
        confirmation['planned'] = progress['runsPlanned']
        jobs = []
        for row in db.execute('SELECT candidate_id, seed_a, seed_b FROM ea_holdout '
                              'WHERE experiment_id=? AND result IS NULL '
                              'ORDER BY candidate_id, seed_a', (experiment_id,)):
            jobs.append((row[0], (int(row[1]), int(row[2]))))
        return jobs

    def _dispatch_confirmation(self, db, store, jobs):
        if self.evaluator is None:
            self._make_evaluator()
        experiment_id = self.state['confirmation']['experimentId']
        intent = self._intent(db, experiment_id) or {}
        if (not self._frozen_compatibility_matches(intent.get('engineRevision'),
                                                   intent.get('compatibility'))
                or intent.get('mechanicsRevision') != self._mechanics_revision()):
            self._note('Frozen confirmation belongs to another scope/revision; dispatch blocked.')
            return
        # Same filling rule as a development dispatch: every free worker takes an already-frozen,
        # authorised holdout job. The paired plan is untouched; only the batch size changes.
        capacity = self._submission_capacity()
        submitted = 0
        durable_boundary_checked = False
        for candidate_id, pair in jobs:
            if submitted >= capacity:
                break
            scenario = (intent.get('observedScenarios') or {}).get(candidate_id)
            if scenario is None:
                self._note('Frozen confirmation scenario missing; dispatch blocked.')
                return
            if (not durable_boundary_checked and self._is_canonical_evaluator()
                    and not getattr(self.evaluator, 'closed', False)):
                self._prepare_durable_dispatch(db)
                durable_boundary_checked = True
            if self.evaluator.submit_holdout(db, experiment_id, candidate_id, scenario, pair,
                                              **self._runnable_kwargs()):
                submitted += 1
        self._record_submission_limit(capacity, len(jobs))

    def _advance_confirmation(self, db, store):
        confirmation = self.state['confirmation']
        experiment_id = confirmation['experimentId']
        report = ledger.confirmation_report(db, experiment_id)
        confirmation['planned'] = report['runsPlanned']
        confirmation['completed'] = report['runsCompleted']
        if not report['ready']:
            return False
        confirmation['ready'] = True
        plan = confirmation.get('nomination')
        if not isinstance(plan, dict) or plan.get('version') != confirmation_service.VERSION:
            decision = dict(status='inconclusive', confirmed=False,
                            reason='This historical confirmation has no frozen current decision rule.')
        else:
            rows = []
            for cid, a, b, raw in db.execute(
                    'SELECT candidate_id,seed_a,seed_b,outcome FROM ea_holdout WHERE experiment_id=?',
                    (experiment_id,)):
                outcome = json.loads(payload_codec.decode_text(raw)) if raw else {}
                rows.append(dict(candidateId=cid, seedPair=[a,b], outcome=outcome,
                                 **{key: outcome.get(key) for key in
                                    ('policy','encounterRevision','mechanicsRevision','engineRevision')}))
            decision = confirmation_service.decide(plan, rows)
        confirmation['comparison'] = decision
        confirmation['verdict'] = decision['status']
        confirmation['unresolved'] = sum(not row.get('resolved') for row in report.get('outcomes') or [])
        if confirmation['unresolved']:
            self._note('Unresolved holdout work blocks publication; no favourable subset is used.')
        self.state['publication'] = dict(
            status=('published' if decision.get('confirmed') else
                    'withheld' if confirmation['unresolved'] else 'inconclusive'),
            experimentId=experiment_id, candidateId=confirmation['nominee'],
            comparison=decision, reason=decision.get('reason'),
            uncertainty=dict(method=decision.get('method'), interval=decision.get('diagnosticInterval'),
                             coverageLimit=decision.get('coverageLimit')))
        self._record_holdout_publication(db, confirmation, decision)
        self._persist(store)
        return True

    def _paired_comparison(self, db, experiment_id, confirmation):
        rows = db.execute('SELECT candidate_id, seed_a, seed_b, outcome FROM ea_holdout '
                          'WHERE experiment_id=? AND result IS NOT NULL', (experiment_id,)).fetchall()
        by_pair = {}
        for candidate_id, seed_a, seed_b, outcome in rows:
            by_pair.setdefault((int(seed_a), int(seed_b)), {})[candidate_id] = (
                json.loads(payload_codec.decode_text(outcome)) if outcome else None)
        differences = []
        for values in by_pair.values():
            nominee = values.get(confirmation['nominee'])
            reference = values.get(confirmation['reference'])
            if not nominee or not reference:
                continue
            if nominee.get('finalEarned') is None or reference.get('finalEarned') is None:
                continue
            differences.append(nominee['finalEarned'] - reference['finalEarned'])
        if not differences:
            return None
        mean = sum(differences) / len(differences)
        if len(differences) >= 2:
            variance = sum((value - mean) ** 2 for value in differences) / (len(differences) - 1)
            paired_se = math.sqrt(variance / len(differences))
        else:
            paired_se = None
        nominee_values = [v[confirmation['nominee']].get('finalEarned') for v in by_pair.values()
                          if (v.get(confirmation['nominee']) or {}).get('finalEarned') is not None]
        reference_values = [v[confirmation['reference']].get('finalEarned') for v in by_pair.values()
                            if (v.get(confirmation['reference']) or {}).get('finalEarned') is not None]
        return dict(pairs=len(differences), pairedDifference=mean,
                    pairedStandardError=paired_se,
                    nomineeMean=(sum(nominee_values) / len(nominee_values)) if nominee_values else None,
                    referenceMean=((sum(reference_values) / len(reference_values))
                                   if reference_values else None),
                    nomineeResolved=sum(1 for v in by_pair.values()
                                        if (v.get(confirmation['nominee']) or {}).get('resolved')),
                    referenceResolved=sum(1 for v in by_pair.values()
                                          if (v.get(confirmation['reference']) or {}).get('resolved')))

    # -- persistence / reporting ------------------------------------------------------------------
    def _persist(self, store):
        """Persist the FULL internal state (never a lossy report).

        Writing only ``report()`` loses the current experiment, queue, history, features, adviser,
        question budget/spent and the confirmation plan, so a restart re-planned work and could
        double-charge a paired experiment. The report is kept as a nested convenience view for the
        read-only bootstrap status, but every resumable field lives at the top level.
        """
        if store is None:
            return
        payload = dict(self.state)
        payload['sessionId'] = self.session_id
        payload['encounter'] = self.encounter
        payload['limitations'] = list(self.limitations)
        # An unchanged idle/paused pass must not rewrite the whole state. The signature covers every
        # durable field (plus the cached budget rows) but not the volatile per-pass timings or the
        # nested convenience report.
        signature = _digest(dict(
            durable={key: value for key, value in payload.items()
                     if key not in ('report', 'timings')},
            budgets=self._last_budget_rows))
        store_ref = id(store)
        if signature == self._persisted_signature and store_ref == self._persisted_store:
            return
        payload['report'] = self.report()
        try:
            was_open = bool(getattr(store.db, 'in_transaction', False))
            store.set(self.runtime_key, payload)
            # `Store.set` writes through raw SQL and can leave an implicit transaction open. Make a
            # standalone checkpoint durable now; never commit a transaction that was open on entry
            # (activation and caller batches remain atomic).
            if not was_open and getattr(store.db, 'in_transaction', False):
                store.db.commit()
                if getattr(store.db, 'in_transaction', False):
                    raise RuntimeError('SQLite checkpoint transaction remained open after commit')
            self._persisted_signature = signature
            self._persisted_store = store_ref
            self.state['persistError'] = None
        except Exception as exc:  # noqa: BLE001 - surface, never swallow
            self.state['persistError'] = f'{type(exc).__name__}: {exc}'
            self._note('persistence failed: %s' % (exc,))
            raise

    def _refresh_budget_rows(self, db):
        if self.session_id is None:
            self._last_budget_rows = {}
            return
        rows = {}
        for row in ledger.session_status(db, self.session_id)['budgets']:
            slot = rows.setdefault(row['purpose'], dict(total=0, reserved=0, completed=0))
            slot['total'] += row['total']
            slot['reserved'] += row['reserved']
            slot['completed'] += row['completed']
        self._last_budget_rows = rows

    def report(self):
        purposes = (self.config or {}).get('purposes') or {}
        matrix = {owner: dict(row)
                  for owner, row in ((self.config or {}).get('budgets') or {}).items()}
        try:
            budget_view = modes.derive_budget_matrix(self.config) if self.config else None
        except ValueError:
            budget_view = None
        eligible = list(modes.eligible_streams(self.config)) if self.config else []
        spending = {purpose: dict(total=total,
                                  reserved=self._last_budget_rows.get(purpose, {}).get('reserved', 0),
                                  completed=self._last_budget_rows.get(purpose, {}).get('completed', 0))
                    for purpose, total in purposes.items()}
        cohort = self._cohort()
        current = cohort[0] if cohort else None
        # The report is a public/UI view: keep the resumable observed scenarios and the full job list
        # in the persisted state, but never echo them twice through the status payload.
        def public_member(member):
            view = {key: value for key, value in member.items()
                    if key not in ('observedCandidate', 'observedReference',
                                   'candidateRawScenario', 'referenceRawScenario', 'jobs')}
            view['planJobs'] = len(member.get('jobs') or [])
            return view

        public_current = public_member(current) if current is not None else None
        public_cohort = [public_member(member) for member in cohort]
        return deepcopy(dict(
            version=VERSION, enabled=bool(self.enabled), mode=self.mode, config=self.config,
            supportedModes=list(modes.MODES), eligibleOwners=eligible,
            runtimeRevision=self.scheduler_revision,
            battleCompatibilityRevision=self.revision, sessionId=self.session_id,
            question=(self.state.get('question') or {}).get('question'), encounter=self.encounter,
            presentation=self.state.get('presentation'),
            thresholds=self.state.get('thresholds'),
            questionBudget=(dict(self.state['question'])
                            if isinstance(self.state.get('question'), dict) else None),
            mechanicalRegions=dict(
                points=[row.get('testedPoints') for row in self.state.get('boundaries') or []],
                gaps=[row.get('unresolvedGaps') for row in self.state.get('boundaries') or []],
                note='diagnostic bootstrap points; gaps remain unknown and no safe box is inferred'),
            budgets=dict(purposes=purposes, session=self._last_budget_rows, matrix=matrix,
                         owners=eligible,
                         remainders=list((budget_view or {}).get('remainders') or []),
                         source=(budget_view or {}).get('source')),
            purposeSpending=spending, portfolio=list(self.state.get('portfolio') or []),
            boundaries=list(self.state.get('boundaries') or []),
            progress=dict(roundIndex=int(self.state.get('roundIndex') or 0),
                          experiments=len(self.state.get('completedExperiments') or []),
                          retiredFrozenPlanCount=len(self.state.get('retiredFrozenPlans') or []),
                          reserved=sum(int(member.get('reserved') or 0) for member in cohort),
                          completed=sum(int(member.get('completed') or 0) for member in cohort),
                          current=public_current, cohort=public_cohort,
                          cohortPending=len(cohort), cohortBound=cohort_bound(self.workers),
                          paired=list(self.state.get('paired') or []),
                          confirmation=self.state.get('confirmation'),
                          publication=self.state.get('publication'),
                          queueLength=len(self.state.get('queue') or []), freshLibrary=False,
                          idle=bool(self.state.get('idle')),
                          unspendable=list(self.state.get('unspendable') or []),
                          unspendableTasks=list(self.state.get('unspendableTasks') or []),
                          blocked=any(bool(member.get('blocked')) for member in cohort)),
            timings=dict(self.state.get('timings') or {},
                         preparationDiagnostics=dict(proposals_service.LAST_PREPARATION),
                         proposalWorkers=parallel_proposals.diagnostics(),
                         duty=self.duty,
                         allocation=self.allocation_status(),
                         planner=self.planner_status(),
                         evaluator=(self.evaluator.status() if self.evaluator is not None else None)),
            limitations=list(dict.fromkeys(self.limitations)),
            historyCoverage=(self._freshness_report or {}).get('coverage'),
            migrationPreview=self.migration_preview or self.state.get('migrationPreview')))
