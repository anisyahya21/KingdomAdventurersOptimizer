"""Persistent discrete quality-diversity search. No combat mechanics live here.

Archive membership is provisional: retained inventory is not exposed by the runner.
SQLite transactions commit the result and its scheduling cursor together. Interrupted work
is retried with the same seeds. Only supplied builds and canonical legal mutations are used.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sqlite3
import statistics
import threading
import time
import tempfile
import zlib
from collections import Counter, deque
from copy import deepcopy
from pathlib import Path

from strategy_optimizer_limits import clamp_duty, default_workers, worker_ceiling
import strategy_learner as learner
import search_contract
import strategy_probe
import strategy_finetune
import strategy_evidence
import strategy_experiments
# The proposal path is a nested function inside `_loop`, so the exception types its handlers name
# have to be module-level names rather than loop locals. All three are ValueError subclasses, but
# naming them keeps the handler honest: a refused proposal is an expected, free outcome, while any
# other failure still lands in the loop's own failure path and is reported instead of being hidden.
from combat_scenario import ScenarioError
from strategy_optimizer_adapter import StrategyOptimizerError
import strategy_students as students
import strategy_encounter_search as encounter_search
import strategy_encounter_migration as encounter_migration
import strategy_community_campaign as community_campaign
import strategy_revision


#: Persisted multi-select campaign focus: the encounter ids the automatic Community campaign should
#: schedule. An empty list means the whole recovered catalogue. Separate from the legacy single
#: ``focusEncounter`` so a scheduling filter can never be mistaken for a focus experiment.
COMMUNITY_FOCUS_KEY = 'communityFocusEncounters'
#: Persisted "the automatic campaign is enabled" flag, so a restart knows an automatic campaign
#: exists without resuming/activating it (the loop only loads status on open).
COMMUNITY_ENABLED_KEY = 'communityCampaignEnabled'


def _loaded_revision():
    """Digest of the scheduler sources as loaded at import, not of whatever is on disk later.

    The running host keeps the code it loaded at start-up; a later edit of the files must not make
    the desktop believe the change is live. This value is computed once, when those sources are
    first imported, and is what `encounterAware.runtimeRevision` reports.
    """
    digest = hashlib.sha256()
    here = Path(__file__).resolve().parent
    names = ('strategy_optimizer.py', 'strategy_encounter_search.py',
             'strategy_encounter_evaluation.py', 'strategy_experiment_store.py',
             'strategy_outcomes.py', 'strategy_search_mode.py', 'strategy_mechanics.py',
             'strategy_encounter_compiler.py', 'strategy_build_domain.py',
             'strategy_joint_proposals.py', 'strategy_operating_regions.py',
             'strategy_encounter_adviser.py', 'strategy_optimizer_adapter.py',
             'strategy_optimizer_native.py', 'strategy_optimizer_fast.py',
             'ka_encounter_abi.py', 'strategy_encounter_migration.py',
             'strategy_encounter_confirmation.py', 'strategy_stream_proposals.py',
             'strategy_encounter_decisions.py', 'strategy_legacy_observations.py',
             'strategy_encounter_presentation.py', 'strategy_seed_freshness.py',
             'strategy_history_summary.py', 'strategy_support_evidence.py',
             'strategy_students.py', 'strategy_encounter_ready_queue.py',
             'strategy_encounter_revision_native.py',
             'strategy_publication.py',
             'search_contract.py')
    for name in names:
        digest.update(name.encode('utf-8'))
        digest.update((here/name).read_bytes())
    from strategy_encounter_revision_native import _library_path
    native_path = _library_path()
    digest.update(b'ka_revision.dll')
    digest.update(native_path.read_bytes() if native_path.is_file() else b'unavailable')
    return digest.hexdigest()


_LOADED_REVISION = _loaded_revision()
# Frozen experiments use only inputs that can change battle or measurement meaning. Keep the
# broader scheduler digest above for diagnostics; batching/recovery edits must not invalidate plans.
_BATTLE_COMPATIBILITY_REVISION, _BATTLE_COMPATIBILITY_MANIFEST = (
    strategy_revision.current_battle_compatibility_revision())


def _campaign_runnable_encounters(coordinators, db):
    """Encounters eligible for a worker share, excluding cohorts blocked by revision drift.

    Coordinators with no frozen cohort can still create a plan in their pass. Existing cohorts only
    compete for a share if at least one member still passes the frozen revision guards. The
    coordinator pass itself still runs for excluded encounters so it can retire stale plans and
    replan without consuming a share reserved for work that cannot be dispatched.
    """
    runnable = set()
    for encounter_id, coord in coordinators.items():
        if not coord.enabled or not coord._has_work():
            continue
        if not coord._cohort() or coord._has_compatible_frozen_work(db):
            runnable.add(encounter_id)
    return runnable


def _campaign_should_rollover(progress):
    """Start a fresh bounded generation after useful work or a stale-plan retirement drains."""
    return bool(progress.get('idle') and
                (progress.get('experiments') or progress.get('retiredFrozenPlanCount')))


def _reactivate_campaign_session(db, encounter_id, row):
    """Resume the exact persisted ledger session behind a campaign generation."""
    import strategy_experiment_store as ledger

    session_id = int(row['sessionId'])
    session_row = db.execute('SELECT config FROM ea_session WHERE id=?',
                             (session_id,)).fetchone()
    if session_row is None or (json.loads(session_row[0]).get('studyScope') or {}).get(
            'campaignScope') != row['campaignScope']:
        raise ValueError('Campaign encounter %d has a mismatched ledger session'
                         % encounter_id)
    ledger.activate_session(db, session_id)

#: How many (tier, encounter) pairs one planning pass tops the rebel track up with. A rebel draw costs
#: several times a value draw, because working out which numbered rules it broke means reading the
#: derived placement again; so the track is filled in rotating slices instead of all at once. A planning
#: pass must not stall the coordinator for the sake of the far tiers, and what is left owed is reported
#: in the diagnostics rather than hidden.
REBEL_PASS_BUDGET = 4

SCHEMA = 1
WORKER_BATCH_LIMIT = 8
ASYNC_PUBLICATION = True
DISCOVERY_RUNS = 8
VALIDATION_RUNS = 64
MAX_CANDIDATES = 256
MAX_FAMILIES = 128
MAX_SAMPLES = 512  # detailed replay examples per candidate; compact evidence is retained separately

#: The comparable-run bank a build gets while it holds **first place at something**.
#:
#: The staged policy stops a promising build at 88 runs (24 discovery + 64 validation), and that is all
#: it ever receives. A build sitting first on a leaderboard was therefore measured exactly as thinly as
#: an unproven child - and once its 88 were spent it could never gain another run, so it could neither
#: defend its place nor lose it on evidence. That is the wrong way round: the one build whose number is
#: worth being sure of is the one every other result is read against.
#:
#: So first place at anything buys this bank instead: first in any of the four lanes, or holding the
#: fight's highest potential or highest earned. It keeps being handed runs while it holds the place,
#: because the limit is recomputed from current evidence on every pass. The moment it drops to second
#: it falls back to 88 - which it has already outgrown, so it receives nothing further. It is out of the
#: **top position**, and still in the library with its evidence intact; nothing here prunes.
#:
#: `MAX_SAMPLES` is the same ceiling the codebase already uses for a hard-tested point, so the incumbent
#: bank cannot outgrow a deliberately tested build - it is about 6x an ordinary build's 88 runs.
RANK_ONE_RUNS = MAX_SAMPLES

#: How many builds per fight, by **best earned**, get the extended treatment, and the bank they get.
#:
#: First place alone was too narrow a net. The builds just behind the leader on earnings are the ones a
#: challenger comes from, and they were being screened on 88 runs exactly like an untried child - so the
#: second, third and tenth best earnings on a fight were known to within a fraction of a chest against a
#: noise of several. This gives the top ten by best earned a bank between the ordinary one and the
#: incumbent's, so the order behind the leader is settled on evidence rather than on whose 64-run mean
#: happened to land high.
TOP_EARNED_SLOTS = 10
TOP_EARNED_RUNS = 256

#: The bank for a build ranked **first** on either highest earned or highest average earned. That place
#: is worth more evidence than the leader rung, but it is a *finite review*, not an entitlement: **rank
#: alone never authorises work without limit.**
#:
#: This constant used to be `1_000_000`, and the reasoning that produced it was that a record set by one
#: lucky tail draw is not evidence, so the cure was to keep feeding the build until its average caught up
#: with its best. The flaw is that the highest-earned record is **monotonic**: a build that reached 167
#: holds the place until another reaches 168, which may never happen, so one unchanged build was fed for
#: the rest of the session - the observed effect was a single fight's champion reaching ~9,000 attempts
#: out of a 500,000-run library while nothing challenged it.
#:
#: The replacement keeps the *reason* and removes the unbounded part. First place gets
#: `RANK_REVIEW_RUNS` as a cumulative endpoint, exactly like the other finite rungs: the limit is
#: compared against the candidate's own ordinal count, so it is spent once and never reissued - losing
#: and regaining the lead does not create a new allowance. Anything beyond it must be asked for
#: explicitly (a screening or confirmation experiment, a diagnostic replay, or a user-authorised
#: evaluation action), which is what `strategy_breakthrough` already does.
RANK_REVIEW_RUNS = 2 * RANK_ONE_RUNS

#: The rungs are ordered ordinary < top-earned < incumbent, and a build holding first place must never be
#: given less than one merely in the top ten. `check_optimizer_rank_one.py` asserts that ordering rather
#: than this module: an assert here would run on import, and a future edit to the constants would then
#: stop the optimiser from loading at all instead of failing a check.
# SQLite grows on demand; this is a ceiling, not an allocation. Keep the configured finite guard
# while allowing several million recorded battles.
MAX_DB_MB = 4096


def library_capacity_pages(capacity_mib, page_size):
    """Convert an explicit MiB library limit to pages without assuming SQLite's page size."""
    page_size = int(page_size)
    if page_size <= 0:
        raise ValueError('SQLite page size must be positive.')
    return max(1, int(capacity_mib) * 1024 * 1024 // page_size)


def _is_sqlite_full(exc):
    code = getattr(exc, 'sqlite_errorcode', None)
    return isinstance(code, int) and (code & 0xff) == sqlite3.SQLITE_FULL


def storage_full_message(path, connection, requested_mib, exc):
    """Return compact, connection-specific evidence after a storage-full rollback."""
    def pragma(name):
        try:
            return connection.execute('PRAGMA ' + name).fetchone()[0]
        except Exception:
            return 'unknown'

    current_pages = pragma('page_count')
    page_size = pragma('page_size')
    freelist_pages = pragma('freelist_count')
    effective_cap = pragma('max_page_count')
    import shutil
    try:
        library_volume_free = shutil.disk_usage(str(Path(path).parent)).free
    except Exception:
        library_volume_free = 'unknown'
    wal_path = Path(str(path) + '-wal')
    try:
        wal_bytes = wal_path.stat().st_size
    except OSError:
        wal_bytes = 0
    temp_path = os.environ.get('TEMP') or os.environ.get('TMP') or tempfile.gettempdir()
    try:
        temp_volume_free = shutil.disk_usage(temp_path).free
    except Exception:
        temp_volume_free = 'unknown'
    try:
        current_bytes = int(current_pages) * int(page_size)
        current_gib = f'{current_bytes / (1024 ** 3):.1f}'
    except (TypeError, ValueError):
        current_gib = 'unknown'
    try:
        requested_gib = f'{int(requested_mib) / 1024:.1f}'
    except (TypeError, ValueError):
        requested_gib = 'unknown'
    try:
        cap_reached = int(current_pages) >= int(effective_cap) and int(freelist_pages) == 0
    except (TypeError, ValueError):
        cap_reached = False
    try:
        library_free_gib = f'{int(library_volume_free) / (1024 ** 3):.1f}'
    except (TypeError, ValueError):
        library_free_gib = 'unknown'
    cause = ('configured page-cap boundary reached' if cap_reached else
             'cause not isolated; SQLITE_FULL can also reflect library-volume or TEMP exhaustion')
    action = ('Restart with a larger explicit capacity to continue.' if cap_reached else
              'Check library and TEMP free space and the configured capacity before restarting.')
    return (f'SQLite SQLITE_FULL: could not allocate storage ({cause}). Library is about {current_gib} GiB; '
            f'configured capacity is {requested_gib} GiB; library volume has {library_free_gib} GiB free. '
            f'path={Path(path)}; '
            f'currentPages={current_pages}; pageSizeBytes={page_size}; '
            f'freelistPages={freelist_pages}; effectiveConnectionMaxPages={effective_cap}; '
            f'requestedCapacityMiB={requested_mib}; '
            f'libraryVolumeFreeBytes={library_volume_free}; walBytes={wal_bytes}; '
            f'tempPath={temp_path}; tempVolumeFreeBytes={temp_volume_free}. {action} '
            'The configured limit was not changed; committed history remains in the library, and '
            'uncommitted seeds are retried from the last committed ordinal after restart.')
# A mature-library snapshot takes around 0.2 s on the coordinator thread. The desktop polls every
# two seconds, so publishing more often only takes time away from feeding workers.
PUBLISH_MIN_SECONDS = 2.0
# Rolling window for the measured aggregate throughput, and the shortest window worth reporting.
RATE_WINDOW_SECONDS = 30
RATE_MIN_SECONDS = 5
# Probe candidates and their pivot are validated over an extended shared bank: a threshold claim has
# to separate a real fall-off from RNG noise, and 64 shared seeds cannot do that for a 2-chest
# effect. The scheduler keeps extending while the paired evidence is still inconclusive.
PROBE_VALIDATION_RUNS = 160

# Fine-tune admission is a bounded growth policy, not a lifetime encounter total. The old fixed cap of
# 48 retained fine-tune children was exhausted by about twelve four-child programs, so a long-running
# fight could no longer tune the four-objective portfolio's top tens (up to 40 distinct one-axis
# parents, overlapping where a build holds two lane places) and could never admit a new challenger once
# that handful of historical programs existed. Probe builds are exempt from the mutation population cap,
# so a bound is still needed - but it has to scale with the work the fight has actually recorded. The
# budget below grants one more retained child per `FINETUNE_CHILDREN_PER_ATTEMPTS` accepted encounter
# runs, on top of a base floor, and is capped by an explicit true ceiling. A fresh library therefore
# cannot spawn hundreds at once, a fight that keeps running keeps earning slots, and live retained
# inventory stays bounded regardless of library age. Retained children are counted from the lineage
# source, so pruning a candidate releases its slot without deleting any saved program or measured range.
FINETUNE_BASE_CHILDREN = 48
FINETUNE_CHILDREN_PER_ATTEMPTS = 64
FINETUNE_MAX_CHILDREN = 512


def encounter_attempts(store, encounter_id):
    """The encounter's recorded accepted-run total, summed across its difficulty keys.

    This is the encounter's own search work as the library already counts it (`lifetimeAttempts` is
    incremented in the same transaction as every accepted run), never a per-page or per-program tally.
    """
    prefix = f'{int(encounter_id)}:'
    total = 0
    for key, entry in (store.get('encounterLifetime') or {}).items():
        if str(key).startswith(prefix):
            total += int((entry or {}).get('lifetimeAttempts') or 0)
    return total


def finetune_capacity(store, encounter_id):
    """The encounter's bounded fine-tune admission: its work-scaled budget and the room left in it.

    The whole policy is reported, not a bare yes/no, so status lines and refusals can state it:
    `attempts` is the recorded encounter work, `allowed` is `base + attempts // perAttempts` capped by
    `ceiling`, `retained` is the fine-tune children still present in the lineage, and `room` is what a
    new candidate may consume. `allowed` is monotonic in recorded work, so a fight that keeps running
    keeps earning slots and can never permanently lock after a fixed handful of historical programs.
    """
    attempts = encounter_attempts(store, encounter_id)
    growth = attempts // FINETUNE_CHILDREN_PER_ATTEMPTS
    allowed = min(FINETUNE_MAX_CHILDREN, FINETUNE_BASE_CHILDREN + growth)
    row = store.db.execute(
        "SELECT COUNT(*) FROM lineage l JOIN candidate c ON c.id=l.candidate "
        "WHERE l.source LIKE 'fine-tune:%' AND l.encounterId=?", (int(encounter_id),)).fetchone()
    retained = int(row[0] or 0)
    return dict(encounterId=int(encounter_id), attempts=attempts,
                base=FINETUNE_BASE_CHILDREN, perAttempts=FINETUNE_CHILDREN_PER_ATTEMPTS,
                growth=growth, ceiling=FINETUNE_MAX_CHILDREN, allowed=allowed,
                retained=retained, room=max(0, allowed - retained),
                saturated=allowed >= FINETUNE_MAX_CHILDREN)


def finetune_capacity_reason(capacity):
    """One line stating the work-scaled budget, its true ceiling and what a newcomer would need."""
    return (f"the encounter fine-tune cap is full: {capacity['retained']} of {capacity['allowed']} "
            f"retained children used (base {capacity['base']} + {capacity['attempts']} recorded runs / "
            f"{capacity['perAttempts']}, ceiling {capacity['ceiling']}); the fight must record more "
            "runs before another candidate is admitted")


def _finetune_candidates(program):
    """Every candidate one fine-tune program reads: its frozen parent, its points and its cells.

    One definition, so the view's cache signature and the batched evidence prefetch cannot drift
    apart about which builds a program's picture actually depends on.
    """
    seen = []
    for cid in ([ (program.get('parent') or {}).get('candidateId') ]
                + [point.get('candidateId') for point in (program.get('points') or {}).values()]
                + [cell.get('candidateId') for cell in (program.get('cells') or {}).values()]):
        if cid and cid not in seen:
            seen.append(cid)
    return seen



#: Measured loss opportunity (chests a run built before the native loss gate discarded it) at or
#: above which a candidate earns the ordinary staged follow-up even when it is not (yet) a member of
#: its fight's potential lane. A defeat that queued ten or more chests is a mechanism worth
#: re-testing; single-digit opportunity is within the noise of one or two prize callbacks. The number
#: is anchored on the chest bands `accumulate` already histograms (0, 1, 2-4, 5-9, 10-24, 25-49,
#: 50+), so it is the first band that is not single-digit rather than a fitted value. The follow-up
#: is the same one rung any promising candidate gets - extended discovery, then a validation bank -
#: and never an open-ended resimulation, so a high-potential build cannot outgrow the staged policy.
POTENTIAL_FOLLOW_UP_MIN_CHESTS = 10

# ---------------------------------------------------------------------------------------------
# Focused exploitation lane
# ---------------------------------------------------------------------------------------------
#: The staged policy stops a build at 88 runs (24 discovery + 64 validation) and a probe or tested
#: point at :data:`MAX_SAMPLES`. While one fight is focused that ceiling *is* the whole search: every
#: new attempt belongs to the same encounter, so a convincing build can never be measured past the
#: screening bank and a close boundary can never be resolved. This lane is the bounded second stage
#: the focused search was missing. Once a build has shown a converted win on a complete, comparable
#: selection bank it earns a larger paired-seed target, and the target grows with how competitive or
#: how uncertain its evidence is. Competitiveness is judged on the validation bank's own mean earned
#: (chests per resolved run, a loss counting the zero its native gate released), never on the best
#: single run ever recorded - a lifetime maximum would let one lucky seed outrank a build that pays
#: on every seed. It is bounded in three ways so it can never become the unbounded generator the
#: focus-liveness work removed: a fixed target per build, a fixed number of builds exploited per
#: fight, and a per-pass budget that is ring-fenced from the reservoir.
FOCUS_EXPLOIT_FLOOR = 1024        #: a certified contender's paired-run target
FOCUS_EXPLOIT_COMPETITIVE = 3072  #: a competitive or still-uncertain build's paired-run target
FOCUS_EXPLOIT_CAP = 65536         #: resource ceiling; reaching it is not statistical confirmation
FOCUS_EXPLOIT_STEP = 96           #: the most one build is granted per pass
FOCUS_EXPLOIT_SLOTS = 4           #: builds exploited at once per fight
FOCUS_EXPLOIT_SHARE = 3           #: the lane may hold at most 1/FOCUS_EXPLOIT_SHARE of the reservoir
#: The Wilson 95 % lower win bound at which a contender's reliability is certified. It is the same
#: 0.80 the archive's old gate used, and below it the build is still "uncertain" and earns the larger
#: target: a rare mechanism that only converts occasionally must not be starved of samples.
FOCUS_EXPLOIT_CERTIFIED_LOWER = .80
#: How close to the fight's best validation-bank mean earned a build must be to count as competitive.
FOCUS_EXPLOIT_COMPETITIVE_SHARE = .95

# ---------------------------------------------------------------------------------------------
# Focused record repair lane
# ---------------------------------------------------------------------------------------------
#: A Highest Earned record is a possibly rare, exceptional outcome. The mean-based competitive band
#: deliberately refuses to select it - one lucky seed must not outrank a build that pays on every
#: seed - but the record is still worth *repeating*: the question is whether that exceptional outcome
#: can be turned into a consistent strategy rather than a one-off. This lane reserves a repeat bank
#: for the resident Highest Earned holder of the focused encounter even when its mean sits below the
#: mean leader, and the auto-tuner freezes that same build for controlled stat tuning. It is bounded
#: like the mean lane (one build, a fixed target, a ring-fenced budget) so it can never starve novel
#: exploration or the mean-leader lane.
FOCUS_RECORD_FLOOR = 1024              #: a record holder's paired-run floor; the record repeated this far
FOCUS_RECORD_CAP = FOCUS_EXPLOIT_CAP   #: the hard ceiling; a repeat bank never grows past this
FOCUS_RECORD_STEP = 96                 #: the most one pass grants the record build
FOCUS_RECORD_SHARE = 4                 #: the lane may hold at most 1/FOCUS_RECORD_SHARE of the reservoir
#: The resolved sample floor at which the record holder's candidate is credible enough to repeat: the
#: same screening bank the mean lane already uses (`VALIDATION_RUNS`). A record set before that bank
#: completed is real evidence, but there is not yet a measurable build to repeat or tune.
FOCUS_RECORD_MIN_RESOLVED = VALIDATION_RUNS

# The proposal rotation: one name per legal dimension of the hard search-space contract
# (`search_contract.MUTATION_OPS`) plus the stat axes and the consumable input. A turn belongs to
# whichever dimension has been proposed least, so no dimension can starve and none can dominate; the
# learning redesign later decides which to prefer.
PROPOSAL_AXES = ('add-skill', 'remove-skill', 'replace-skill', 'move-skill', 'set-trigger',
                 'set-formation-skill', 'set-weapon', 'reorder-roster', 'herbs',
                 'stat:hp', 'stat:mp', 'stat:atk', 'stat:def', 'stat:spd', 'stat:lck', 'stat:dex',
                 'stat:int')

#: The rotation the previous generator used: roster reorders, skill-order permutations and one stat
#: step per confirmed axis. Kept only so a controlled measurement can reproduce the old behaviour
#: (`SEARCH_MODE='legacy'`); the shipped mode is the contract.
LEGACY_PROPOSAL_AXES = ('skills', 'formation', 'stat:def', 'herbs', 'stat:atk', 'stat:hp',
                        'stat:mp', 'stat:spd', 'stat:lck', 'stat:dex')

#: Measurement-only switch: `contract` is the hard search-space contract, `legacy` reproduces the
#: previous generator exactly (bounded formation/skill-order permutations plus probe-style stat
#: steps). Nothing but the comparison harness sets it.
SEARCH_MODE = 'contract'

#: The learning policy. `branching` is the learner in `strategy_learner`: lineage, staged evidence, a
#: reservoir of independent siblings, and learned operator/scale weights. `legacy` reproduces the
#: previous behaviour (one child at a time, the two-loss deferral, an equal axis rotation) so a
#: controlled before/after can be measured; nothing in production sets it.
LEARNER_MODE = 'branching'

#: Active population bounds. The old single global cap of 256 crowded twenty encounters and their
#: lanes against each other; the active search is now bounded *per encounter*, with lineage rows kept
#: outside the candidate table so pruning can never erase ancestry.
MAX_CANDIDATES_PER_ENCOUNTER = 24
MAX_CANDIDATES_TOTAL = 640
#: How far each pruned build had been measured, kept so a resurrected copy does not re-run its prefix.
#: Most-recent-first and capped: two integers per build, dropped oldest-first past the cap.
PRUNED_ORDINALS_KEY = 'prunedOrdinals'
PRUNED_ORDINALS_LIMIT = 2000
#: The evidence rungs a paired comparison can actually be decided at - the staged policy's own banks.
#: Anything that caches a comparison keys on *these* crossings rather than on the run counter: battle
#: 12,438 tells a comparison nothing that battle 12,437 did not, and rebuilding a 0.6-second analysis
#: because it happened is what starved the pool when a fight was focused.
EVIDENCE_MILESTONES = (8, 24, 64, 128, 256, 512, 1024, 2048)
#: How long a focused portfolio plan may be reused even if its signature says nothing changed. The
#: trigger is the evidence milestone; this is only a backstop against an unforeseen burst.
PORTFOLIO_REFRESH_SECONDS = 15.0
#: The adaptive analysis cadence: auto-tune, probe plans, breakthrough, MP recovery and the
#: fine-tune advance all run on this tick rather than on the few-millisecond wake the loop uses to
#: harvest completions. None of that work is a battle, and a worker cannot consume any of it.
ANALYSIS_CADENCE_SECONDS = 5.0
#: The longest a single fine-tune program may go without being measured again while its own key
#: says nothing about it changed. `strategy_finetune.plan` reads a point's bank completion, its
#: paired verdict and the program's own definition, and a point cannot even be judged before its
#: authorised bank finishes (`_point_ready` requires `completedBank >= bank`), so the key below is
#: faithful; this only bounds the delay of a change it cannot express.
FINETUNE_ADVANCE_SECONDS = 60.0
#: The longest the analysis may go without running at all. The trigger is the analysis signature -
#: a bank completing, a program changing, a participant crossing an evidence rung - so this only
#: bounds how long a change the signature cannot express (a new mean leader, say) may wait. It is
#: deliberately slower than the cadence: analysis is bookkeeping, and a fight that already has
#: authorised work does not need its comparisons rebuilt to keep its workers busy.
ANALYSIS_MAX_SECONDS = 20.0


def evidence_milestone(count):
    """The highest rung at or below `count` - the level a comparison is decided at."""
    level = 0
    for rung in EVIDENCE_MILESTONES:
        if count >= rung:
            level = rung
        else:
            break
    return level

#: Record batching. A finished result is accepted immediately (the ordering guard and the planner's
#: own ordinal map move with acceptance), but its durable bookkeeping shares one transaction and one
#: `synchronous=FULL` commit with up to this many other results, or with whatever is pending after
#: `RECORD_BATCH_SECONDS`. Measured on the live library, a per-result commit costs ~0.5 ms of the
#: ~2.1 ms `record` call on the coordinator thread - the thread that feeds the pool - so batching is
#: what stops the database from gating dispatch. Durability is unchanged: every result is committed
#: at least every `RECORD_BATCH_SECONDS`, and the batch is flushed on Pause, Stop, Close and before
#: any prune or publish. A flush that fails puts its results back and pauses the search.
RECORD_BATCH_SIZE = 32
RECORD_BATCH_SECONDS = 0.1

#: Measurement-only switch for the planner-ceiling probe. `False` is production: normal branching and
#: starvation reseeding run and the evidence view keeps its 0.25 s refresh. `True` measures what the
#: pool does when the planner is (almost) idle - it stops creating candidates and stretches the
#: evidence refresh to 30 s, so the workers consume only the banks the last refresh authorised. That
#: gives the upper bound any planner parallelisation could reach; nothing in production sets it.
MINIMAL_PLANNER = False
MINIMAL_PLANNER_EVIDENCE_SECONDS = 30.
# Ranking the whole mature population took about 0.1 s per refresh and ran roughly 3 times per
# second. Aggregate ordinals still update on every completion; a one-second ranking refresh keeps
# feedback prompt while leaving the coordinator more time to dispatch ready work.
PLANNER_EVIDENCE_SECONDS = 1.0

# Keep discovery available even when fine-tuning has authorized large validation banks.
# The switch also retains the previous scheduler for controlled comparisons.
BALANCED_DISCOVERY = True
DISCOVERY_REFRESH_SECONDS = 5.0

#: Publish after dispatch, not before it. The status snapshot costs ~0.2 s on a mature library (538
#: candidates, 46k runs) and used to run *before* the refill in the same pass, so every publish could
#: leave free workers waiting on bookkeeping that no worker consumes. Deferring it to the end of the
#: pass changes nothing about what is published, only when in the pass it happens.
DEFER_PUBLISH = True


def same_simulator(left, right):
    """Decide whether two builds can share one library's stored runs.

    A library may be reopened by a later build only when every module it recorded is either identical
    or a *measured* digest-neutral revision. Each equivalence class below is a set of revisions whose
    stored runs were replayed on both backends and reproduced byte-identical digests; a revision
    outside its class fails closed, which is the whole point of not trusting the change itself.

    Scheduler-side modules are ignored outright: they never take part in a simulation, so their
    contents cannot reach a digest.
    """
    ignored = {'tools/recovery/strategy_optimizer.py', 'tools/recovery/strategy_optimizer_limits.py',
               # A pure observer module. It is imported by the sandbox, but it only appends report
               # fields computed after the digest, and the two hooks that feed it live in modules that
               # are themselves in the equivalence classes below.
               'tools/recovery/combat_progress.py'}
    a = {k: v for k, v in left.get('files', {}).items() if k not in ignored}
    b = {k: v for k, v in right.get('files', {}).items() if k not in ignored}
    # Exact 2026-09-30 live-library transition. Eighteen stored ea_sample battles across fourteen
    # encounters replayed to their stored digest on both the native and Python backends. Hashing the
    # whole retained manifest keeps this bridge closed for every other source combination. The
    # scheduler and post-digest progress observer above are deliberately omitted from both sides.
    # Evidence: TEMP/optimizer-compat-review-20260930.json in the workspace root.
    def fingerprint(provenance, files):
        if not files or provenance.get('missing'):
            return None
        payload = dict(mode=provenance.get('mode'), files=files, missing=[])
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()

    live_library_pair = {
        'f345963a93566275d53fde98f99c61b76740a84194b1ee3389fbc71cd674698b',
        '8f391c5186eeae13cbcac561aefb4f84378b2a4fe19201ebc03e0ed0f898587d',
    }
    if (left.get('mode') == right.get('mode') == 'recovery-workspace'
            and len(a) == len(b) == 55
            and {fingerprint(left, a), fingerprint(right, b)} == live_library_pair):
        return True
    # The stored-attack objective's revisions. Between them, the only engine changes were the two
    # read-only observers and the optional release callback on the skill-command queue; 8 stored runs
    # spanning three horizons (up to 10,000 ticks) replayed byte-identical on the Python engine and on
    # the native kernel, both against the digest the library already held. See check_simulator_compat.py.
    neutral = {
        'tools/recovery/combat_shared_controllers.py': {
            '797000821d5dc0dca722209dddd2be8ed197c2667d612b5844b04ad2dbe3e444',
            'ea4f43b263293c232fb8a9b819c28a002653b6fe7370ea0ecd9317695230184d',
            '3dc3226ba2eb396ef7b5646efe64529965e00bb28d43ef5b198761e2a4e4b5ec',
            '34dc91de54337238184fe102c51370c20cdb99847086f899c9b29521e9cc4a5c',
            # The stored-attack ordering probe (reads the boss's HP at the moment a command is
            # released, and passes it to the observer). Re-verified digest-neutral by replaying stored
            # runs on both backends: check_simulator_compat.py.
            'cc5d127c3dd2f966090c4283396dd024b683c54e90a2c02ef5fe6d150fab5494',
        },
        'tools/recovery/combat_commands.py': {
            '8949444aa16a66ef0a9db1a3007731c74e11c1dda2cce8727cbf2a657fcbdf33',
            '5b770fa2c5d25ce55cdb76fde7b36563899adfe68c3b34ba80fd64aaed88a404',
        },
        'tools/recovery/combat_shared_resolution.py': {
            'bdbd19c78e48f775b4850d754c46ebd84899e9e39eab537678f542fc30ac5ffb',
            '8ae877a11bc1b8fae784facd55a489295b46d51acd5936bec1d4e125427cabf8',
        },
        'tools/recovery/combat_sandbox.py': {
            '55a6e29b8605b96b929655aed02886272acb331068d833ece6eb9c34edc554b2',
            '5ca4d1aa59db234341af9953ab3c4f5b04d8b5f974fffc970fc4b3eee7790953',
            # The live MP telemetry and the live `<=3%` Holy Herb policy. Both ride the *existing*
            # after-fighters observer and the *existing* item seam, and both are gated on a declared
            # trigger list; a scenario that declares none (every stored scenario) takes the same path
            # it always did. Re-verified digest-neutral by the same replay: 40 stored runs spread over
            # the library and 20 candidates x 6 of their own rows, every digest byte-identical on the
            # Python engine and on the kernel (RE-evidence/20260925-herb-policy/simulator-compat.json).
            '0356637203181dc736f088d5bb7d4f1d1de4cafe5a82e89c577ad16cb27463a6',
            # The observation-only `mpWatchUnits` list: the same observer, sampling a unit that is
            # merely watched rather than one that may spend a charge. A stored scenario declares
            # neither field, so it takes the path it always did - re-verified by replaying 8 stored
            # runs from the live library on both backends, every digest byte-identical
            # (RE-evidence/20260926-mp-observer/simulator-compat.json).
            '2730df54d5c4cc915163e58febfd6535f3d2190f3819b2fb9e483ccc10213403',
        },
        # `combat_interaction` and `combat_scenario` gained the declared `holyHerbMaxUses` and
        # `holyHerbTriggerUnits` parameters. Both are validators/publishers on the interaction side:
        # they decide whether a *declaration* is legal, never how a battle runs, so a stored scenario
        # that declares neither is read and simulated exactly as before (same replay evidence).
        'tools/recovery/combat_interaction.py': {
            '391f7f0bde4b6a2ed6f73f49a1a7f0ab18ebf64a68ac67a05b5f568bce328dc5',
            '8d1c871c5c527a744bb25731f6d16b0a010fcae9a73b741fbe3db8af52765a82',
        },
        'tools/recovery/combat_scenario.py': {
            'b99c6064a2fe75e305d68f6ae34693268b87110d6e9f7bbcb2663aefcfae846e',
            '4a83794a35ea6707371bc25aa0299483a11772e13f73fe3f86a97e5e1a6af9a8',
            # `mpWatchUnits` is validated beside `holyHerbTriggerUnits`: a declaration check only, on
            # a field a stored scenario does not carry, so no stored scenario is read differently.
            # Same replay evidence as `combat_sandbox` above.
            'de484dcaea7efb773e57b458f31c32a77941bc95ecca65d5978e27ee18d657cb',
        },
        # The adapter gained the additive `rewardOutcome` block and then the additive
        # `progressMetrics` block, both attached *after* the compact digest is computed.
        'tools/recovery/strategy_optimizer_adapter.py': {
            'ffde21b857adff8738131d8ac1a54bc339183a0998306bba8c485051d66522f7',
            '6304aed52cd53c11f048a814953a53fece23a18e755a328d65819d6350659c10',
            # The revision the live 10,000-tick library was written by.
            '4ae176b228899749bf27d9caa483956623c2d587bfa8c7f1d5fb82afb813f99a',
            '21f7bed4f38ff1e25beb48e5b45f604bf001a78d20be87c1dfd5051c396a5a7f',
            # The search-space contract's `propose`: the generator path changed, the simulator path
            # did not. Re-verified digest-neutral by replaying stored runs on both backends
            # (check_simulator_compat.py).
            'b5951a6f139f8c82cec55403e93ac31ce90a303bc5624f8ae0dc4b72300f0a5c',
            # Same generator path again for the synthetic-human contract (no fallback to the legacy
            # generator). Digest-neutrality re-verified the same way.
            '1d94c87bb1e58e5b804b37126a57f3fb06cd1ce077de3e280951a91585c68d98',
            # The 30,000-tick horizon and the terminal-verdict policy. Only the *default* scenario the
            # optimiser creates is affected: a stored scenario carries its own `finishPolicy` and
            # `tickLimit`, so replaying one is unchanged. Verified by replaying 8 stored runs from
            # `strategiesv6.sqlite` at their own horizon/policy on both backends
            # (RE-evidence/20260922-horizon/simulator-compat-v6.json).
            '92a77e17b0f41959ae904a911ad202085718dc918499cf52ce0d5874584b25f1',
            # The MP telemetry and the explicit Holy Herb evidence: two more additive blocks attached
            # after the compact digest, so no stored digest moves or is reinterpreted.
            '4c525be106853eaa0758773cc0be4b5d5035bee1cc4d5803b8502f8ec414b7e7',
        },
        # The safety horizon moved 10,000 -> 30,000. `strategy_search` owns the horizon the optimiser
        # generates new libraries with; a stored library keeps its own scope and its stored scenarios
        # keep their own `tickLimit`, so nothing already recorded changes (same replay evidence).
        'tools/recovery/strategy_search.py': {
            '7139741779fff8bf811971d5b1841660bf20443ba78e7ebb91cd767eda6cb0c4',
            '4ee85cb53b60c90d6aaa8235ae1faf9479e8337d1affd268feff7df3cc0f4e91',
        },
        # `combat_evaluation` is the older bounded evaluation surface, not part of the sandbox's
        # simulation path; its `HARD_MAX_TICKS` ceiling was raised with the horizon. Replay
        # neutrality is the same measurement as above.
        'tools/recovery/combat_evaluation.py': {
            'a81172067a4743e12e8d7cdae58b3de468c4a50f98f241e252a22628ac861738',
            '40bf71c3039db2393210473c7810527779388b1e220b63e8b51dad6ab9c46b02',
        },
    }
    for key, revisions in neutral.items():
        if a.get(key) in revisions and b.get(key) in revisions:
            a[key] = b[key]
    return bool(a) and a == b and left.get('mode') == right.get('mode')


def _shallow_encounter_mapping(raw):
    """Read a legacy mode mapping without decoding its recursive rollback ancestry.

    Older campaign activations copied the entire prior mapping into ``previous.mapping``.
    Repeating that operation produced thousands of nested ``previous`` objects. Keep the immediate
    rollback snapshot, but replace its own ancestry with an empty object before JSON decoding.
    This is a read-time projection; it never rewrites the library.
    """
    marker = '"previous"'
    found = 0
    i = 0
    while i < len(raw):
        if raw[i] != '"':
            i += 1
            continue
        start = i
        i += 1
        escaped = False
        while i < len(raw):
            ch = raw[i]
            i += 1
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                break
        if raw[start:i] != marker:
            continue
        colon = i
        while colon < len(raw) and raw[colon].isspace():
            colon += 1
        if colon >= len(raw) or raw[colon] != ':':
            continue
        found += 1
        if found < 2:
            continue
        value = colon + 1
        while value < len(raw) and raw[value].isspace():
            value += 1
        if value >= len(raw) or raw[value] != '{':
            raise ValueError('Invalid nested encounter rollback mapping')
        depth = 0
        in_string = False
        escaped = False
        for end in range(value, len(raw)):
            ch = raw[end]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return json.loads(raw[:value] + '{}' + raw[end + 1:])
        raise ValueError('Unterminated encounter rollback mapping')
    return json.loads(raw)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(scenario):
    scenario = deepcopy(scenario)
    scenario.pop('mathSeed', None)
    scenario.pop('libSeed', None)
    return hashlib.sha256(canonical(scenario).encode()).hexdigest()


#: Native combat parameters no recovered combat formula reads: Gathering (20), Move (21) and
#: Love/Heart (22). A stored scenario keeps them for exact seed replay and provenance, but they are
#: outside the optimiser's decision domain - two otherwise identical builds that differ only here are
#: one searchable strategy. Only these three are treated as inert; native parameter 12 (Energy) is
#: deliberately *not* included, because `search_contract.search_identity` omits it for a different
#: reason and no equivalent proof covers it here.
INERT_PARAMETER_IDS = (20, 21, 22)


def equivalence_identity(scenario):
    """`identity` with the inert parameters (20/21/22) erased, for duplicate detection and ranking.

    The stored candidate id and the raw stored scenario keep full fidelity - including the inert
    slots - so seed replay and provenance are untouched. This second key exists only so the optimiser
    can see that a candidate differing solely in Gathering/Move/Love is the same searchable strategy
    as one already held. It is *not* `search_contract.search_identity`: that projection is narrower in
    other ways (it groups weapon behaviour and omits Energy), so substituting it would merge real
    differences. This erases exactly the three proven-inert slots and nothing else.
    """
    normalized = deepcopy(scenario)
    for unit in normalized.get('ownUnits') or []:
        if not isinstance(unit, dict):
            continue
        parameters = unit.get('parameters')
        if not isinstance(parameters, dict):
            continue
        for parameter_id in INERT_PARAMETER_IDS:
            parameters.pop(parameter_id, None)
            parameters.pop(str(parameter_id), None)
    return identity(normalized)


def seed_pair(phase, index):
    """Disjoint reproducible pseudo-random banks, shared by all compared builds."""
    raw = hashlib.sha256(f'ka-optimizer-v1:{phase}:{index}'.encode()).digest()
    return [int.from_bytes(raw[:4], 'big') & 0x7fffffff,
            int.from_bytes(raw[4:8], 'big') & 0x7fffffff]


def scope(scenario):
    # A different source condition or horizon is a different experiment, never pooled.
    return canonical({key: value for key, value in scenario.items()
                      if key not in ('ownUnits', 'mathSeed', 'libSeed')})


def wilson(successes, count):
    if not count:
        return [0.0, 1.0]
    z = 1.96
    p = successes / count
    center = (p + z*z/(2*count)) / (1 + z*z/count)
    half = z*math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / (1 + z*z/count)
    return [max(0., center-half), min(1., center+half)]


def summarize(rows):
    n = len(rows)
    wins = sum(row['verdict'] == 1 and not row['censored'] for row in rows)
    losses = sum(row['verdict'] == 2 and not row['censored'] for row in rows)
    unresolved = n-wins-losses
    callbacks = [row['prizeCallbacks'] for row in rows
                 if not row['censored'] and row['prizeCallbacks'] is not None]
    # Do not substitute zero for unknown/censored runs, or rank their partial means.
    ordered = sorted(callbacks)
    # Chests per run over resolved runs only, using the same rule the run cards show.
    chests = [chest_count(row)[0] for row in rows]
    chests = [value for value in chests if value is not None]
    # Per-run chest counts are wide (roughly +/- 10 in the first library searched here), so a mean
    # without its spread invites reading noise as improvement. Report the standard deviation and the
    # standard error of the mean over the resolved runs that produced it.
    chest_sd = statistics.stdev(chests) if len(chests) > 1 else None
    return dict(n=n, wins=wins, losses=losses, censored=unresolved,
                winRate=wins/n if n else None, winInterval=wilson(wins, n),
                failureRate=losses/n if n else None, unresolvedRate=unresolved/n if n else None,
                retainedMean=None, callbackMean=statistics.mean(callbacks) if callbacks else None,
                chestMean=statistics.mean(chests) if chests else None,
                chestSampleCount=len(chests),
                chestMin=min(chests) if chests else None,
                chestMax=max(chests) if chests else None,
                chestSD=chest_sd,
                chestSE=chest_sd/math.sqrt(len(chests)) if chest_sd is not None else None,
                callbackSD=statistics.stdev(callbacks) if len(callbacks)>1 else None,
                callbackMin=min(callbacks) if callbacks else None,
                callbackP10=ordered[int((len(ordered)-1)*.1)] if ordered else None,
                callbackMax=max(callbacks) if callbacks else None,
                meanResources=statistics.mean(r['resourceUses'] for r in rows) if n else None,
                meanSurvivors=statistics.mean(r['survivors'] for r in rows) if n else None,
                meanTicks=statistics.mean(r['ticks'] for r in rows) if n else None,
                comparable=bool(n and unresolved == 0 and len(callbacks) == n))


#: The elite lanes a candidate can be preserved by. Each lane answers a different question about a
#: build, and every lane can nominate parents on its own, so no single number decides what is
#: promising (see `elite_lanes`).
LANE_EARNED = 'earned'
LANE_POTENTIAL = 'potential'
LANE_SETUP = 'setup'
LANE_EFFICIENCY = 'efficiency'
LANE_NAMES = (LANE_EARNED, LANE_POTENTIAL, LANE_SETUP, LANE_EFFICIENCY)

#: How many members a lane keeps per encounter/difficulty. One bound, used by the planner, the
#: published view and the pruning guard, so a build that is a lane leader cannot be pruned by a rule
#: that used a different pool size.
LANE_POOL = 4

#: Objective/scoring generation. Libraries written before this carry archive/quality data produced by
#: the single-score objective, which is never silently reinterpreted: the version is explicit in the
#: store, `elite_lanes` is derived from the stored run evidence, and the setup lane is empty (not
#: fabricated) for runs recorded before the stored-attack metrics existed.
OBJECTIVE_VERSION = 3

#: Measurement-only switch. `lanes` is the objective. `legacy` reproduces the previous single-score
#: objective - the Wilson 0.80 archive gate plus archive-only parents - so a controlled search can be
#: run before and after on identical fixtures and seeds. Nothing but the measurement harness reads it,
#: and no behaviour of the lane objective depends on it.
OBJECTIVE_MODE = 'lanes'

#: Stored-attack progress maxima kept per candidate. These are the fields the causal chain in
#: `combat_progress` produces; the setup lane orders by them.
PROGRESS_MAX_FIELDS = ('postDeathPrizes', 'postDeathBossLeavings', 'postDeathBossReentries',
                       'commandsReleasedAfterDeath', 'commandsReleasedAfterDeathTargetingBoss',
                       'storedCommandsAtDeath', 'storedCommandsTargetingBossAtDeath',
                       'maxSimultaneousStoredCommands', 'maxSimultaneousCommandsTargetingBoss',
                       'storedTargetHoldersPeak', 'storedTargetHoldersAtDeath',
                       'commandsTargetingBoss', 'commandsTargetingBossReleased')

#: MP telemetry kept per candidate, keyed by the declared trigger unit's name. These are **minima**:
#: a lower reading is the stronger evidence, so they aggregate with `min`, the opposite direction to
#: every maximum above. `-1` is the observer's "never sampled" sentinel and is never a reading - a
#: genuine minimum of `0` (a drained pool) is, which is exactly the distinction the sentinel exists for.
MP_MIN_FIELDS = ('minimumMp', 'minimumMpPercent')


def merge_mp(left, right):
    """Per-unit MP telemetry, aggregated in the direction these readings actually point."""
    merged = {unit: dict(reading) for unit, reading in (left or {}).items()}
    for unit, reading in (right or {}).items():
        target = merged.get(unit)
        if target is None:
            merged[unit] = dict(reading)
            continue
        for key in ('runs', 'lowRuns', 'zeroRuns'):
            target[key] = int(target.get(key, 0) or 0) + int(reading.get(key, 0) or 0)
        for field in MP_MIN_FIELDS:
            value = reading.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                current = target.get(field)
                target[field] = value if not isinstance(current, int) or current < 0 else min(current, value)
        tick = reading.get('firstLowMpTick')
        if isinstance(tick, int) and not isinstance(tick, bool) and tick >= 0:
            current = target.get('firstLowMpTick')
            if not isinstance(current, int) or current < 0 or tick < current:
                target['firstLowMpTick'] = tick
                target['firstLowMpPhase'] = reading.get('firstLowMpPhase')
    return merged


def merge_herb(left, right):
    """Explicit Holy Herb evidence: counts add, declared caps and use maxima keep the best."""
    merged = dict(left or {})
    for key in ('runs', 'useTotal', 'useRuns'):
        merged[key] = int(merged.get(key, 0) or 0) + int((right or {}).get(key, 0) or 0)
    for key in ('useMax', 'maxUses', 'startingStock'):
        value = (right or {}).get(key)
        if value is not None:
            current = merged.get(key)
            merged[key] = value if current is None else max(current, value)
    remaining = (right or {}).get('remainingMin')
    if remaining is not None:
        current = merged.get('remainingMin')
        merged['remainingMin'] = remaining if current is None else min(current, remaining)
    return merged


def accumulate(total, row):
    total = dict(total or dict(n=0, wins=0, losses=0, censored=0, count=0, sum=0., sum2=0.,
                              resources=0., survivors=0., ticks=0., minimum=None, maximum=None,
                              histogram=[0]*7))
    # Chests joined the lifetime aggregate after some libraries were already written, so read them
    # defensively: an old aggregate simply starts counting from the next run.
    for key, default in (('chestCount', 0), ('chestSum', 0.), ('chestSum2', 0.),
                         ('chestMin', None), ('chestMax', None)):
        total.setdefault(key, default)
    total['n'] += 1
    verdict = row['verdict'] if not row['censored'] else None
    total['wins' if verdict == 1 else 'losses' if verdict == 2 else 'censored'] += 1
    total['resources'] += row['resourceUses']
    total['survivors'] += row['survivors']
    total['ticks'] += row['ticks']
    value = row['prizeCallbacks']
    if value is not None and not row['censored']:
        total['count'] += 1
        total['sum'] += value
        total['sum2'] += value*value
        total['minimum'] = value if total['minimum'] is None else min(value, total['minimum'])
        total['maximum'] = value if total['maximum'] is None else max(value, total['maximum'])
        index = next((i for i, bound in enumerate((0, 1, 4, 9, 24, 49)) if value <= bound), 6)
        total['histogram'][index] += 1
    chests, _ = chest_count(row)
    if chests is not None:
        total['chestCount'] += 1
        total['chestSum'] += chests
        total['chestSum2'] += chests*chests
        total['chestMin'] = chests if total['chestMin'] is None else min(chests, total['chestMin'])
        total['chestMax'] = chests if total['chestMax'] is None else max(chests, total['chestMax'])
    # Lane evidence. The maxima are monotonic like the lifetime records: a candidate that once
    # reached 99 potential keeps that number even when later runs are weaker, so the potential lane
    # cannot be erased by a single bad seed. `potentialMax` is the opportunity a run built (a defeat
    # does not erase it), `earnedMax`/`earnedCount` the chests a win actually released and how many
    # wins stood behind that number.
    potential = potential_chests(row)
    if potential is not None:
        total['potentialMax'] = potential if total.get('potentialMax') is None \
            else max(total['potentialMax'], potential)
    # Loss-only opportunity. `potentialMax` mixes a win's released chests with a defeat's discarded
    # opportunity, which is fine for the lane that asks "what is the best number this build ever
    # showed" but wrong for the portfolio's highest-potential objective, which asks specifically what
    # a *defeat* built. Only a resolved loss moves these counters, and the mean divides by losses, so
    # a build's wins can neither inflate nor dilute its loss-only reading. Read defensively: libraries
    # written before these counters simply start counting from their next loss.
    if potential is not None and verdict == 2:
        total['lossPotentialSum'] = total.get('lossPotentialSum', 0)+potential
        total['lossCount'] = total.get('lossCount', 0)+1
        total['lossPotentialMax'] = potential if total.get('lossPotentialMax') is None \
            else max(total['lossPotentialMax'], potential)
    # `earnedMax` is the best ACTUAL conversion, so only a resolved win can set it. A loss reports
    # `chest_count == 0` from the native loss gate, and that zero must never read as "this build has
    # converted something" - it is exactly the confusion the earned lane exists to avoid.
    if verdict == 1 and chests is not None:
        total['earnedCount'] = total.get('earnedCount', 0)+1
        total['earnedMax'] = chests if total.get('earnedMax') is None \
            else max(total['earnedMax'], chests)
    progress = row.get('progressMetrics')
    if isinstance(progress, dict):
        total['progressRuns'] = total.get('progressRuns', 0)+1
        maxima = dict(total.get('progress') or {})
        for field in PROGRESS_MAX_FIELDS:
            value = progress.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                maxima[field] = int(value) if field not in maxima else max(maxima[field], int(value))
        total['progress'] = maxima
        deaths = progress.get('bossDeathTick')
        if isinstance(deaths, int) and deaths >= 0:
            earliest = total.get('earliestBossDeathTick')
            total['earliestBossDeathTick'] = deaths if earliest is None else min(earliest, deaths)
        first = progress.get('firstPostDeathCommandReleaseTick')
        if isinstance(first, int) and first >= 0:
            earliest = total.get('earliestPostDeathReleaseTick')
            total['earliestPostDeathReleaseTick'] = first if earliest is None else min(earliest, first)
    # MP telemetry for the declared trigger units. Read defensively and aggregated with `min`: a
    # reading of `0` is a drained pool and must survive as the strongest evidence, while the
    # observer's `-1` sentinel means "never sampled" and must never be allowed to win a minimum.
    mp = row.get('mpMetrics')
    if isinstance(mp, list):
        readings = {}
        for entry in mp:
            if not isinstance(entry, dict) or not entry.get('name'):
                continue
            unit = entry['name']
            reading = readings.get(unit) or dict(runs=0, lowRuns=0, zeroRuns=0)
            reading['runs'] += 1
            for field in MP_MIN_FIELDS:
                value = entry.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    current = reading.get(field)
                    reading[field] = value if not isinstance(current, int) or current < 0 \
                        else min(current, value)
            if entry.get('reachedLowMp'):
                reading['lowRuns'] += 1
            if entry.get('reachedZero'):
                reading['zeroRuns'] += 1
            tick = entry.get('firstLowMpTick')
            if isinstance(tick, int) and not isinstance(tick, bool) and tick >= 0:
                current = reading.get('firstLowMpTick')
                if not isinstance(current, int) or current < 0 or tick < current:
                    reading['firstLowMpTick'] = tick
                    reading['firstLowMpPhase'] = entry.get('firstLowMpPhase')
            readings[unit] = reading
        if readings:
            # `mpRuns` counts the runs that carried an MP block at all; each unit's own `runs` counts
            # the runs that measured *that* unit. They differ whenever the declared watch changed.
            total['mpRuns'] = int(total.get('mpRuns', 0) or 0) + 1
            total['mp'] = merge_mp(total.get('mp'), readings)
    # Explicit Holy Herb evidence. `resourceUses` cannot say which consumable spent a charge, so the
    # herb's own counts and declared stock/cap are kept separately.
    herb = row.get('herbMetrics')
    if isinstance(herb, dict):
        summary = dict(total.get('herb') or dict(runs=0, useTotal=0, useRuns=0))
        summary['runs'] = int(summary.get('runs', 0) or 0) + 1
        uses = herb.get('useCount')
        if isinstance(uses, int) and not isinstance(uses, bool) and uses >= 0:
            summary['useTotal'] = int(summary.get('useTotal', 0) or 0) + uses
            summary['useRuns'] = int(summary.get('useRuns', 0) or 0) + (1 if uses else 0)
            summary['useMax'] = uses if summary.get('useMax') is None \
                else max(summary['useMax'], uses)
        for key in ('maxUses', 'startingStock'):
            value = herb.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                summary[key] = value if summary.get(key) is None else max(summary[key], value)
        remaining = herb.get('remainingStock')
        if isinstance(remaining, int) and not isinstance(remaining, bool):
            summary['remainingMin'] = remaining if summary.get('remainingMin') is None \
                else min(summary['remainingMin'], remaining)
        total['herb'] = summary
    return total


def aggregate_summary(total, retained_rows):
    summary = summarize(retained_rows)
    if not total:
        return summary
    n, count = total['n'], total['count']
    chest_counted = total.get('chestCount') or 0
    chest_sd = (math.sqrt(max(0., (total['chestSum2']-total['chestSum']**2/chest_counted)
                              /(chest_counted-1))) if chest_counted > 1 else None)
    summary.update(n=n, wins=total['wins'], losses=total['losses'], censored=total['censored'],
        winRate=total['wins']/n, failureRate=total['losses']/n, unresolvedRate=total['censored']/n,
        winInterval=wilson(total['wins'], n), callbackMean=total['sum']/count if count else None,
        chestMean=total['chestSum']/chest_counted if chest_counted else None,
        chestSampleCount=chest_counted,
        chestMin=total.get('chestMin'), chestMax=total.get('chestMax'),
        chestSD=chest_sd,
        chestSE=chest_sd/math.sqrt(chest_counted) if chest_sd is not None else None,
        callbackSD=math.sqrt(max(0., (total['sum2']-total['sum']**2/count)/(count-1))) if count>1 else None,
        callbackMin=total['minimum'], callbackMax=total['maximum'], callbackP10=None,
        callbackHistogram=dict(zip(('0','1','2–4','5–9','10–24','25–49','50+'), total['histogram'])),
        meanResources=total['resources']/n, meanSurvivors=total['survivors']/n, meanTicks=total['ticks']/n,
        # MP telemetry (per declared trigger unit, minima) and the explicit Holy Herb evidence travel
        # with the summary so the UI can show what the herb rule actually did.
        mp=dict(total.get('mp') or {}), herb=dict(total.get('herb') or {}),
        mpRuns=int(total.get('mpRuns', 0) or 0),
        comparable=total['censored']==0 and count==n, retainedSampleCount=len(retained_rows))
    return summary


def family_key(rows):
    """Coarse measured behavioral cells, NOT one family per parameter vector.

    Healing intensity, item use and prize-callback intensity describe actual runs. These
    cells are hypotheses about families, explicitly exposed for inspection in the UI.
    """
    attacks = sum(r['behavior'].get('attacks', 0) for r in rows)
    heals = sum(r['behavior'].get('heals', 0) for r in rows)
    ratio = heals / max(1, attacks)
    healing = 'healing-heavy' if ratio >= .2 else 'some-healing' if heals else 'no-healing'
    resource = 'items' if any(r['resourceUses'] for r in rows) else 'no-items'
    prizes = statistics.mean((r['prizeCallbacks'] or 0) for r in rows)
    intensity = 'repeated-callbacks' if prizes >= 2 else 'single-callback' if prizes else 'no-callback'
    return f'{healing} / {resource} / {intensity}'


def encounter_family(scenario, rows):
    # Keep genuinely different team structures in different quality-diversity cells. The measured
    # healing/resource/prize descriptor alone put solo melee, rear magic and bow/gun teams into the
    # same cell whenever their first battles happened to have similar chest counts.
    humans = [unit for unit in scenario['ownUnits'] if unit.get('human')]
    size = len(humans)
    size_band = 'solo' if size == 1 else 'pair' if size == 2 else 'trio' if size == 3 \
        else 'team4-6' if size <= 6 else 'team7+'
    magic = any(any(5 <= int(skill) <= 19 for skill in unit['skills']) for unit in humans)
    reach_types = {int(row['type']) for unit in humans
                   for row in (search_contract._weapon_row(unit),)
                   if int(row.get('shootingRange') or 0) > 1}
    names = {4: 'spear', 7: 'gun', 8: 'bow'}
    reach = (names.get(next(iter(reach_types)), 'reach') if len(reach_types) == 1
             else 'mixed-reach' if reach_types else '')
    style = ('magic+' + reach if magic and reach else 'magic' if magic
             else reach or 'close')
    return f'Encounter {scenario["encounterId"]} · {size_band} / {style} · {family_key(rows)}'


def encounter_catalogue():
    """Human-readable identity of every encounter the search can run.

    Read from the same runtime data the engine composes its enemies from, so the label shown next
    to a run is the fight that run actually simulated - not a guess from the scenario id. Returns
    an empty mapping if the data is unavailable, which the UI renders as a bare encounter number.
    """
    try:
        import combat_runtime_data
        data = combat_runtime_data.load_data('encounters.json')
    except Exception:
        return {}
    monsters = {monster['id']: monster for monster in data.get('monsters', [])}
    catalogue = {}
    for encounter in data.get('encounters', []):
        followers = [f['monsterId'] for f in encounter.get('followers', [])]
        roster = [monsters.get(monster_id, {}).get('name') for monster_id in followers]
        catalogue[str(encounter['id'])] = dict(
            title=encounter.get('title'),
            boss=(monsters.get(encounter.get('bossId')) or {}).get('name'),
            enemyCount=len(followers) + 1,
            distinctFollowerCount=len({name for name in roster if name}),
            level=encounter.get('levelField'),
            evidenceNote='Recovered encounter constructor inputs; wave and level fields are source data.')
    return catalogue


def discovery_viable(rows):
    # Two initial losses are sufficient to defer expensive validation, not to prove
    # that this build never works. It remains saved and may be revisited later.
    return len(rows) < 2 or any(r['verdict'] == 1 and not r['censored'] for r in rows)


def quality(summary):
    """Admission to the quality-diversity archive (one elite per measured behaviour cell).

    This used to be the whole objective: `(mean callbacks, Wilson lower bound, -resources)`, admitted
    only when a 64-run bank was complete AND the Wilson 95% lower win bound reached 0.80. Two things
    were wrong with that as an admission rule:

      * a strategy can be extremely close to the multi-chest timing and still finish with 0 earned, so
        a mean-based score threw away exactly the runs worth keeping;
      * the reliability gate made a rare mechanism unreachable: at 20% success the bound never clears
        0.80, so a build that occasionally releases 60 chests could not hold an archive cell at all.

    The cell is still only compared against equal-sized, complete banks (`comparable`: every run
    reached a verdict with a prize reading) - that is comparability, and it is what stops a lucky
    single seed or a half-run bank from taking a cell. The Wilson threshold is gone, and the ordering
    is by the *best* observed progress rather than the mean, so a rare high-yield mechanism keeps its
    cell. Reliability still matters, one lane over and in the final recommendation; it is no longer a
    prerequisite for being preserved.
    """
    if OBJECTIVE_MODE == 'legacy':
        return legacy_quality(summary)
    if summary['n'] != VALIDATION_RUNS or not summary['comparable']:
        return None
    return (summary['callbackMax'] or 0, summary['chestMax'] or 0,
            summary['winInterval'][0], -summary['meanResources'])


def legacy_quality(summary):
    """The objective this one replaced, kept verbatim for the before/after measurement.

    Mean prize callbacks, gated on a complete comparable 64-run bank AND a Wilson 95% lower win bound
    of at least 0.80, with the mean consumable count as the last tie-break.
    """
    if summary['n'] != VALIDATION_RUNS or not summary['comparable']:
        return None
    if summary['winInterval'][0] < .80:
        return None
    return (summary['callbackMean'], summary['winInterval'][0], -summary['meanResources'])


def merge_aggregates(*totals):
    """One candidate's discovery and validation counters as a single lane record.

    Counts add, maxima keep the best: the lane evidence a candidate contributes is the best its runs
    ever showed, exactly like the lifetime records, so a strong discovery run is never diluted by a
    weaker validation bank (or the other way round).
    """
    merged = None
    for total in totals:
        if not total:
            continue
        if merged is None:
            merged = dict(total)
            merged['progress'] = dict(total.get('progress') or {})
            continue
        for key in ('n', 'wins', 'losses', 'censored', 'resources', 'survivors', 'ticks',
                    'count', 'sum', 'sum2', 'lossPotentialSum', 'lossCount',
                    # Chest totals add across the phases so the merged record can answer "what does
                    # this build average", which is what the highest-average ranking needs. They were
                    # absent because no lane ranked by a mean before.
                    'chestSum', 'chestSum2', 'chestCount',
                    # `mpRuns` is a run count like the per-unit `mp[*].runs`, so it adds; only the MP
                    # *readings* are minima. (`progressRuns` above stays a per-phase maximum, which is
                    # what the stored-attack lanes were ranked with.)
                    'mpRuns'):
            if key in total:
                merged[key] = merged.get(key, 0)+total[key]
        for key in ('earnedMax', 'potentialMax', 'lossPotentialMax', 'progressRuns'):
            if total.get(key) is not None:
                merged[key] = total[key] if merged.get(key) is None else max(merged[key], total[key])
        for key in ('earnedCount',):
            if total.get(key) is not None:
                merged[key] = merged.get(key, 0)+total[key]
        for key, value in (total.get('progress') or {}).items():
            merged['progress'][key] = max(merged['progress'].get(key, value), value)
        for key in ('earliestBossDeathTick', 'earliestPostDeathReleaseTick'):
            if total.get(key) is not None:
                merged[key] = total[key] if merged.get(key) is None else min(merged[key], total[key])
        # The MP minima and the herb counts do not follow the "maxima keep the best" rule the block
        # above applies, so they are merged with their own directions rather than copied.
        merged['mp'] = merge_mp(merged.get('mp'), total.get('mp'))
        merged['herb'] = merge_herb(merged.get('herb'), total.get('herb'))
    return merged


def lane_record(row, aggregate, encounter=None, defeat=None):
    """One candidate's lane evidence, read from the aggregate counters written with each run.

    Nothing here decodes a stored run row: the maxima are maintained by `accumulate`, so a lane can be
    ranked during planning with the same single meta query the planner already runs. `encounter` and
    `defeat`, when supplied from the store's own candidate map, avoid decoding the scenario: the
    planner groups a large population by encounter on every proposal, and parsing every scenario for
    two integers was the largest avoidable cost in planning.
    """
    if encounter is None or defeat is None:
        scenario = json.loads(row['scenario'])
        encounter = scenario['encounterId']
        defeat = scenario.get('defeatCount') or 0
    total = aggregate or {}
    n = int(total.get('n', 0) or 0)
    wins = int(total.get('wins', 0) or 0)
    # The average earned, over every resolved run that produced a chest reading - the lifetime
    # `chestSum/chestCount`, which is the same number the overview's own average column shows. It is a
    # different question from `earnedMax` (the best single run) and the two are ranked separately,
    # because a build can top one by luck and the other on merit.
    chest_count = int(total.get('chestCount', 0) or 0)
    chest_sum = float(total.get('chestSum', 0.) or 0.)
    # The spread of those runs, so a mean can be read with its own uncertainty. A high mean over 12 runs
    # and the same mean over 2,000 runs are not the same claim, and the average student has to tell them
    # apart before it decides what to spend runs on.
    chest_sum2 = float(total.get('chestSum2', 0.) or 0.)
    earned_sd = None
    if chest_count > 1:
        variance = max(0.0, (chest_sum2 - chest_sum*chest_sum/chest_count)/(chest_count-1))
        earned_sd = math.sqrt(variance)
    return dict(candidate=row['id'], label=row['label'], source=row['source'],
                encounterId=int(encounter), defeatCount=int(defeat),
                n=n, wins=wins, losses=int(total.get('losses', 0) or 0),
                censored=int(total.get('censored', 0) or 0),
                winInterval=wilson(wins, n), winRate=(wins/n if n else None),
                earnedMax=total.get('earnedMax'), earnedCount=int(total.get('earnedCount', 0) or 0),
                earnedMean=(chest_sum/chest_count if chest_count else None),
                earnedSamples=chest_count,
                earnedSD=earned_sd,
                potentialMax=total.get('potentialMax'),
                meanResources=(total['resources']/n if n and 'resources' in total else None),
                progress=dict(total.get('progress') or {}),
                progressRuns=int(total.get('progressRuns', 0) or 0),
                earliestBossDeathTick=total.get('earliestBossDeathTick'),
                earliestPostDeathReleaseTick=total.get('earliestPostDeathReleaseTick'),
                # MP telemetry (the configured DPS and healer, as minima) and the explicit Holy Herb
                # evidence. Persisted beside the stored-attack progress so a lane or the UI can ask
                # how close a build actually ran to exhaustion and what the herb rule did.
                mp=dict(total.get('mp') or {}),
                herb=dict(total.get('herb') or {}),
                mpRuns=int(total.get('mpRuns', 0) or 0))


def lane_key(record, lane):
    """One lane's ordering; lexicographic, primary signal first, supporting evidence behind it.

    The orderings are structural on purpose - the task that created them forbids inventing weights we
    cannot justify, and a weighted sum is exactly what erases a 99-potential defeat next to a 20-chest
    win. Each tuple is read left to right, so a strictly better primary always wins and the later
    elements only ever break ties.
    """
    progress = record['progress']
    def field(name):
        value = progress.get(name)
        return int(value) if isinstance(value, (int, float)) else 0
    earned = record['earnedMax'] or 0
    potential = record['potentialMax'] or 0
    reliability = (record['winInterval'] or [0., 1.])[0]
    resources = record['meanResources'] if record['meanResources'] is not None else 0.
    if lane == LANE_EARNED:
        # Proven conversion first, then how reliably it reproduces, then the cheaper build.
        return (earned, reliability, -resources, record['n'])
    if lane == LANE_POTENTIAL:
        # **UNEARned opportunity first** - the prize a build has shown and not yet cashed.
        #
        # This pool used to rank by raw `potentialMax`, which made it point at whatever had ever
        # reached furthest and stay there. `potentialMax` is a lifetime maximum that never falls
        # (`accumulate`), so a build that once reached 50 and then stopped winning led this pool
        # permanently: the build that reliably earns 48 could never take the place, because 48 < 50.
        # Measured on the live library, that is not hypothetical - on Kairobot Knight / Normal this
        # pool's leader had 0 wins and 0 earned in 88 runs while the earned leader won 88 of 88.
        #
        # Ranking by `potential - earned` turns the pool into the thing it was standing in for: a
        # queue of unrealised prizes. `potential` breaks a tie between two equally unrealised prizes,
        # so the bigger prize wins.
        #
        # Membership is gated in `lane_qualifies`, and it has to be. Ranking by the gap ALONE was
        # measured against the live library and behaved worse than what it replaced: because a fully
        # converted build has a gap of exactly zero, any gap at all beat it, so fights began leading
        # with builds whose prize was 3 or 5 chests over a build that had cashed 46. Seventeen of the
        # twenty fights changed leader, most of them for the worse. The gate is the floor the
        # follow-up policy already uses - a prize too small to be worth re-testing is not a prize.
        return (potential - earned, potential, reliability, -resources, record['n'])
    if lane == LANE_SETUP:
        # The traced causal chain, in order of how far a build got: opportunity actually produced by
        # the post-death dump, then the stored attacks correctly released after the death, then the
        # mass stored when the boss died, then the raw stored-attack evidence - so a build whose boss
        # never died still ranks by how much stored attack it pointed at the mechanism.
        return (field('postDeathPrizes'), field('commandsReleasedAfterDeathTargetingBoss'),
                field('storedCommandsTargetingBossAtDeath'),
                field('maxSimultaneousCommandsTargetingBoss'),
                field('commandsTargetingBossReleased'), field('storedTargetHoldersPeak'),
                field('maxSimultaneousStoredCommands'), potential, earned)
    if lane == LANE_EFFICIENCY:
        # Same proven conversion, but resources break the tie immediately: 40 earned with 0 items
        # beats 40 earned with 3. Membership is restricted to the Pareto frontier, so a cheaper but
        # weaker build never displaces a dramatically better one.
        return (earned, -resources, reliability, record['n'])
    raise ValueError(f'unknown lane {lane!r}')


def lane_qualifies(record, lane):
    """Whether a candidate has any evidence for this lane at all (no fabricated entries)."""
    if lane == LANE_EARNED:
        # "Proven successful conversion" needs an actual win behind the number. A resolved loss
        # reports `chest_count == 0` from the native loss gate; that zero is not a conversion, so a
        # build that has only ever lost is not an Earned Elite regardless of how much it queued.
        return record['earnedMax'] is not None and (record.get('earnedCount') or 0) > 0
    if lane == LANE_POTENTIAL:
        # A **prize worth chasing**, not merely a number. The pool is the queue of opportunity this
        # build has shown and not cashed, so it takes a build only while `potential - earned` clears
        # the floor the follow-up policy already uses: "a defeat that queued ten or more chests is a
        # mechanism worth re-testing; single-digit opportunity is within the noise of one or two prize
        # callbacks" (`POTENTIAL_FOLLOW_UP_MIN_CHESTS`). Without the gate, ranking by the gap let a
        # 3-chest prize displace a build that had cashed 46.
        #
        # A build that has cashed its best run has no unrealised prize and leaves this pool entirely -
        # it is not lost, because the earned lane holds it and a lane leader is protected from pruning
        # through whichever lane named it. A fight where nothing has a prize worth chasing leaves this
        # pool empty, and `choose_parent` falls through to a lane that does have something.
        return (record['potentialMax'] is not None
                and (record['potentialMax'] - (record['earnedMax'] or 0))
                >= POTENTIAL_FOLLOW_UP_MIN_CHESTS)
    if lane == LANE_SETUP:
        # Only runs that actually carry the stored-attack metrics can enter. Historic rows predate the
        # metrics and are reported as unranked rather than guessed at.
        return record['progressRuns'] > 0
    if lane == LANE_EFFICIENCY:
        return record['earnedMax'] is not None and (record.get('earnedCount') or 0) > 0
    raise ValueError(f'unknown lane {lane!r}')


def pareto_frontier(records):
    """Candidates nothing else beats on (earned chests, consumables): at least as many chests for no
    more items. 40 earned/2 items and 20 earned/0 items are both on it - neither dominates - while
    40 earned/0 items dominates 40 earned/3 items."""
    frontier = []
    for record in records:
        earned = record['earnedMax'] or 0
        resources = record['meanResources'] if record['meanResources'] is not None else 0.
        dominated = False
        for other in records:
            other_earned = other['earnedMax'] or 0
            other_resources = other['meanResources'] if other['meanResources'] is not None else 0.
            if other_earned >= earned and other_resources <= resources and (
                    other_earned > earned or other_resources < resources):
                dominated = True
                break
        if not dominated:
            frontier.append(record)
    return frontier


def elite_lanes(records, pool=LANE_POOL):
    """Every lane's ordered members, per encounter/difficulty.

    `pool` bounds how many members a lane keeps; the bounds are per lane and per fight, so one lane
    cannot fill the population and no lane can be starved by another lane's better absolute numbers.
    """
    keys = sorted({(record['encounterId'], record['defeatCount']) for record in records})
    lanes = {f'{encounter}:{defeat}': {lane: [] for lane in LANE_NAMES} for encounter, defeat in keys}
    for encounter, defeat in keys:
        members = [record for record in records
                   if record['encounterId'] == encounter and record['defeatCount'] == defeat]
        for lane in LANE_NAMES:
            eligible = [record for record in members if lane_qualifies(record, lane)]
            if lane == LANE_EFFICIENCY:
                eligible = pareto_frontier(eligible)
            ranked = sorted(eligible, key=lambda record: lane_key(record, lane), reverse=True)
            lanes[f'{encounter}:{defeat}'][lane] = [record['candidate'] for record in ranked[:pool]]
    return lanes


def choose_parent(pools, population, proposal_number, rng, exploration_every=5):
    """The planner's parent choice: rotate the starting lane, never lose a proposal to an empty pool.

    Returns `(candidate_id, lane_used)`. The rotation is what makes starvation provable: lane
    `(proposal_number + offset) % len(LANE_NAMES)` is tried first, and an empty pool falls through to
    the next lane, then to a uniform draw from the whole encounter. Every `exploration_every`
    proposals the uniform draw is taken deliberately, which is the quality-diversity escape hatch that
    keeps the population from becoming four narrow lineages.
    """
    if proposal_number % exploration_every == exploration_every-1:
        return rng.choice(population), 'exploration'
    for offset in range(len(LANE_NAMES)):
        lane = LANE_NAMES[(proposal_number+offset) % len(LANE_NAMES)]
        pool = [cid for cid in pools.get(lane, []) if cid in population]
        if pool:
            return rng.choice(pool), lane
    return rng.choice(population), 'exploration'


def legacy_choose_parent(archive_ids, population, proposal_number, rng):
    """The previous parent rule, kept verbatim for the before/after measurement.

    Parents were the archive members of this encounter, two proposals in three; otherwise a uniform
    draw from the whole encounter. Because the archive was gated on Wilson >= 0.80, a population that
    cleared no cell had no elite parents at all and every proposal was uniform.
    """
    elite = [cid for cid in population if cid in archive_ids]
    if elite and proposal_number % 3:
        return rng.choice(elite), 'legacy-archive'
    return rng.choice(population), 'legacy-uniform'


def legacy_proposal(parent_scenario, axis_key, proposal_number):
    """The previous generator's single proposal for one axis, kept verbatim for measurement.

    `skills` and `formation` were served by the bounded search generators, which can only re-order
    what a fighter already carries (`strategy_search.build_axes` + `_select_candidates`); the other
    axes stepped one confirmed stat or the consumable input through the probe module's validated
    setters. Returns `(scenario, label)`.
    """
    if axis_key in ('skills', 'formation'):
        # The old bounded generators, called directly: the adapter's `propose` is contract-first now,
        # so the legacy measurement must not go through it or it would measure the new generator.
        import strategy_search
        try:
            axes = strategy_search.build_axes(parent_scenario, strategy_search.DEFAULT_SEARCH_AXIS)
            candidates, _rejected = strategy_search._select_candidates(
                parent_scenario, axes, 1, proposal_number % (2**31))
            generated = candidates[0]['scenario'] if candidates else parent_scenario
        except (ValueError, strategy_search.StrategySearchError) as exc:
            if 'degenerate' not in str(exc):
                raise
            generated = parent_scenario
        return generated, f'Legal mutation {proposal_number+1}'
    axis = axis_key.split(':', 1)[1] if ':' in axis_key else axis_key
    humans = [unit for unit in parent_scenario.get('ownUnits') or [] if unit.get('human')]
    if not humans:
        return None, None
    unit_name = humans[proposal_number % len(humans)]['name']
    try:
        info = strategy_probe.axis_info(axis)
        current = strategy_probe.pivot_value(parent_scenario, unit_name, axis)
        factor = 1.18 if proposal_number % 2 else 0.85
        target = max(1, int(round(current * factor)))
        if target == current:
            return None, None
        candidate = strategy_probe.apply_axis(parent_scenario, unit_name, axis, target)
        return (validate_scenario(candidate),
                f'Axis step · {info["label"]} {current} → {target} · {unit_name} · '
                f'{proposal_number+1}')
    except (ValueError, ScenarioError, strategy_probe.ProbeError):
        return None, None


def candidate_keys(store):
    """{candidate id: '<encounter>:<defeat>'} - written when a candidate is added."""
    return store.candidate_keys()


def population_records(store, keys, aggregates):
    """Lane records for the whole population, grouped by encounter without decoding scenarios."""
    records = []
    # No scenario column: `lane_record` only needs it when the caller cannot supply encounter/defeat,
    # and fetching it here pulled every candidate's stored scenario (tens of kilobytes each) across
    # the wire on every evidence rebuild, for a value nothing in this path reads.
    for row in store.db.execute('SELECT id, label, source FROM candidate'):
        key = keys.get(row['id'], '19:0')
        encounter_id, defeat = (int(part) for part in key.split(':', 1))
        records.append(lane_record(row, merge_aggregates(
            aggregates.get(f'aggregate:{row["id"]}:discovery'),
            aggregates.get(f'aggregate:{row["id"]}:validation')),
            encounter=encounter_id, defeat=defeat))
    return records


def dispatch_gate(shares, community_ids, rebel_ids, average_ids=(), mechanism_ids=(),
                  mp_recovery_ids=(), mp_recovery_on=True):
    """`cid -> is the stream that produced it switched on` - where a share of zero becomes a halt.

    The one place a student's percentage turns into "this build receives no runs". Every candidate is
    owned by the stream named on its lineage row: Student 1's track children, Student 2's rebel
    children, and everything else - the ordinary search's mutations, probes, fresh roots - by the open
    discovery share. A stream at zero returns False for all of its builds, and `ready()` removes them
    from **every** view before handing out seeds.

    That last word is the point. Before this, the reserved streams had only *first refusal*: each took
    a window proportional to its share and whatever it did not use fell through to the general pool. A
    student at 0% therefore still had its builds run whenever the favoured stream had less ready work
    than the window - measured on the live library, 38 Student 1 builds advanced in 45 seconds at 0%
    while only 11 Student 2 builds advanced at 100%. A share that only decides who is asked first is
    not the share the number claims to be.
    """
    on = {name: float(shares.get(name, 0.0) or 0.0) > 0 for name in students.SHARE_STREAMS}
    average_ids = set(average_ids)
    mechanism_ids = set(mechanism_ids)
    mp_recovery_ids = set(mp_recovery_ids)

    def switched_on(cid):
        if cid in community_ids:
            return on[students.STUDENT_COMMUNITY]
        if cid in rebel_ids:
            return on[students.STUDENT_REBEL]
        if cid in average_ids:
            return on[students.STUDENT_AVERAGE]
        if cid in mechanism_ids:
            return on[students.STUDENT_MECHANISM]
        if cid in mp_recovery_ids:
            # MP recovery is not a student and has no share: its permission is the item allowance the
            # user configured (stock + use cap), and nothing runs when that allowance is absent. Without
            # this branch its children would fall through to the discovery share, which is a coupling
            # the user never asked for - and with the discovery share above zero they would run even
            # though no herb quantity had been chosen.
            return bool(mp_recovery_on)
        return on[students.STREAM_DISCOVERY]

    return switched_on


def incumbent_banks(lanes, records, active=None):
    """`{candidate id: bank}` - which builds have earned more than the ordinary run budget, and how much.

    Four of them come straight out of the lanes: the leader of each lane is first at what that lane
    measures. The fight's highest potential and highest earned are the two records the overview shows,
    and they are *not* necessarily a lane's leader - the potential lane ranks by the unearned prize
    rather than by the number reached - so they are read from the records directly.

    On top of those, the **top `TOP_EARNED_SLOTS` builds of each fight by best earned** get the smaller
    extended bank. First place alone was too narrow a net: the builds just behind the leader on earnings
    are where a challenger comes from, and they were being screened on 88 runs exactly like an untried
    child, so the order behind the leader was settled by whose 64-run mean happened to land high.

    And the build **ranked first on highest earned or on highest average earned** gets the larger finite
    review bank instead: more evidence than a leader rung, spent once, with nothing renewed by holding
    the place. Rank alone never authorises work without limit - see `RANK_REVIEW_RUNS` for why the old
    million-run entitlement was removed.

    `active` is the set of candidates whose stream is switched on. A build's retry budget belongs to the
    student that produced it, so turning a student off stops its leaders from being retried - which is
    what "as long as its student is active" means. The bounded rungs are not gated this way: they are
    one-shot budgets, and a build that already earned one keeps the runs it was given.

    A build's bank is the largest it qualifies for, so holding first place never pays less than merely
    being in the top ten. Because this is recomputed from current evidence on every pass, a build stops
    being an incumbent on the same pass it stops qualifying: nothing has to be tracked or expired.
    """
    def live(cid):
        return active is None or cid in active

    banks = {}
    for groups in lanes.values():
        for members in groups.values():
            if members:
                banks[members[0]] = RANK_ONE_RUNS
    holders = {}
    ranked_first = {}
    per_fight = {}
    for record in records:
        fight = (record['encounterId'], record['defeatCount'])
        per_fight.setdefault(fight, []).append(record)
        for field in ('potentialMax', 'earnedMax'):
            value = record.get(field) or 0
            if value > holders.get((fight, field), (0, None))[0]:
                holders[(fight, field)] = (value, record['candidate'])
    for _value, cid in holders.values():
        if cid:
            banks[cid] = max(banks.get(cid, 0), RANK_ONE_RUNS)
    # The two places that buy the larger *finite* review: best single earned, and best average earned.
    # Keyed by `(fight, column)`, not by column alone: keying by column meant each fight overwrote the
    # previous fight's champion and only the last fight iterated ever got the larger rung. That is
    # how "2 retried without limit" came out of twenty fights and two columns.
    for fight, members in per_fight.items():
        for field, pick in (('earnedMax', lambda r: (r.get('earnedMax') or 0)),
                            ('earnedMean', lambda r: (r.get('earnedMean') or 0))):
            best = max(members, key=lambda record: (pick(record), record['candidate']))
            if pick(best):
                ranked_first[(fight, field)] = best['candidate']
    for cid in ranked_first.values():
        if live(cid):
            # Finite, cumulative, and gated on the producing stream: rank alone never authorises work
            # without limit, and a build that has already spent this endpoint receives nothing more from
            # holding the place again. Work beyond it has to be requested - an experiment, a replay, or
            # an explicit user action - which is where the justification for more samples belongs.
            banks[cid] = max(banks.get(cid, 0), RANK_REVIEW_RUNS)
    for members in per_fight.values():
        ranked = sorted(members, key=lambda record: (-(record.get('earnedMax') or 0),
                                                     record['candidate']))
        for record in ranked[:TOP_EARNED_SLOTS]:
            if record.get('earnedMax'):
                banks[record['candidate']] = max(banks.get(record['candidate'], 0), TOP_EARNED_RUNS)
    return banks


def active_streams(shares):
    """`{stream: is it on}` - which streams are currently given a share of the pool.

    A build's retry budget belongs to the stream that produced it, so this is what "as long as its
    student is active" is read against. A build with no recorded stream is the ordinary search's own
    work, so it is governed by the discovery share.
    """
    return {name: float(shares.get(name, 0.0) or 0.0) > 0 for name in students.SHARE_STREAMS}


def staged_limits(nd, wins, better, new_region, rank, earned_max, potential_max, progress,
                  is_probe, probe_bank, has_parent, mode='branching', incumbent=0):
    """(discoveryLimit, validationLimit): how much evidence this candidate deserves.

    `branching` is the staged policy: the first bank for everyone, a second bank only for a candidate
    that showed something (a lane improvement over its parent, a new strategy region, a place in a
    lane's top ranks, a converted win, a measured loss opportunity of at least
    POTENTIAL_FOLLOW_UP_MIN_CHESTS chests, or being the reference baseline), and a validation bank
    only for one that still shows something after that. A candidate that shows nothing stops after 8
    seeds. That opportunity promotion is the same one rung any promising candidate gets, so a
    high-potential build cannot outgrow the staged policy.

    `legacy` reproduces the old rule exactly: `viable = n < 2 or wins > 0 or probe`, which removed a
    candidate from the search the moment its first two seeds lost.

    `incumbent` is the bank that overrides every other reason for promotion - 0 for an ordinary build,
    `TOP_EARNED_RUNS` for a fight's top earners, `RANK_ONE_RUNS` for anything holding first place at
    something. The bank is a ceiling, not a renewal, so a build cannot grow without bound by repeatedly
    taking and losing the lead - it spends its one large budget on evidence and keeps whatever that
    evidence says.
    """
    if mode == 'legacy':
        viable = nd < 2 or wins > 0 or is_probe
        discovery_limit = learner.DISCOVERY_RUNS if (nd < learner.DISCOVERY_RUNS and viable) else nd
        validation_limit = (probe_bank if is_probe else learner.VALIDATION_RUNS) if viable else 0
        return discovery_limit, validation_limit
    if is_probe:
        return nd, probe_bank
    if incumbent > 0:
        # Screening first, then the large comparable bank. Validation is where the bombardment goes:
        # the leaderboard readings are comparable-bank numbers, so piling runs into discovery would
        # make the build busier without making its number any better evidenced.
        return max(nd, learner.EXTENDED_DISCOVERY_RUNS), max(incumbent, nd)
    if nd < learner.DISCOVERY_RUNS:
        return learner.DISCOVERY_RUNS, 0
    promising = bool(better) or bool(new_region) or (rank is not None and rank < 8) \
        or (earned_max or 0) > 0 or not has_parent
    # Opportunity is a promotion reason in its own right, not only through the potential lane: the
    # lane pool holds LANE_POOL members per fight, so a build can reach a real opportunity and still
    # sit outside it. This threshold says so, and the promotion below is the same one follow-up rung
    # any promising candidate gets - it can never outgrow the staged policy.
    high_potential = (potential_max or 0) >= POTENTIAL_FOLLOW_UP_MIN_CHESTS
    promising = promising or high_potential
    # A novel region retains its first eight runs and remains available as a parent. It does not
    # earn more seeds on novelty alone: a large losing team can take seconds per battle without ever
    # reaching the boss-death or prize mechanisms. A rare converted win or any chest opportunity is
    # enough to keep testing, as is concrete recovered progress at boss death.
    mechanism = any((progress or {}).get(key, 0) > 0 for key in (
        'postDeathPrizes', 'storedCommandsTargetingBossAtDeath',
        'commandsReleasedAfterDeathTargetingBoss'))
    evidence_signal = wins > 0 or (potential_max or 0) > 0 or mechanism or not has_parent
    if nd < learner.EXTENDED_DISCOVERY_RUNS:
        return (learner.EXTENDED_DISCOVERY_RUNS if promising and evidence_signal else nd), 0
    return nd, (learner.VALIDATION_RUNS if promising and evidence_signal else 0)


def weighted_parent(rng, pool, lineage, productivity):
    """Pick a parent from one source, favouring lineages that have produced useful descendants.

    The weighting is bounded (0.5 .. 1.5) and the pool itself comes from a rotation over the four
    elite lanes, the region nominees and a guaranteed exploration draw, so a temporarily productive
    lineage can earn more work without ever being able to starve the others.
    """
    scored = []
    for cid in pool:
        root = (lineage.get(cid) or {}).get('root', cid)
        rate = (productivity.get(root) or {}).get('rate', 0.5)
        scored.append((cid, 0.5 + rate))
    total = sum(weight for _, weight in scored)
    target = rng.random()*total
    upto = 0.0
    for cid, weight in scored:
        upto += weight
        if target <= upto:
            return cid
    return scored[-1][0]


def _local_probe_steps(rng, parent_scenario, evidence, encounter, pairs=learner.LOCAL_PAIRS):
    """One local probe of a promising parent: `(child, operation, target, change, value)` or None.

    Picks a searchable statistic and a probe size using only *this encounter's* contextual evidence,
    then optionally adds the partner of a compensation pair when the single-variable evidence suggests
    an interaction. Everything stays inside Search Space v3 legality and inside the stat's legal
    bounds; the move is cheap because it is derived from the parent's own values.
    """
    variables = {**search_contract.CORE_STATS, **search_contract.INT_STAT}
    searchable = [(unit, stat) for unit in parent_scenario['ownUnits'] if unit.get('human')
                  for stat in variables
                  if search_contract.stat_is_searchable(parent_scenario, unit, stat)]
    if not searchable:
        return None
    region = learner.strategy_region(parent_scenario)
    evidence = evidence or {}
    # Every searchable stat competes, weighted by how much its local evidence has improved something.
    scores = {}
    for unit, stat in searchable:
        entries = learner.local_evidence_for(evidence, encounter, region, unit['name'], stat)
        improved = sum(int((entry or {}).get('improved', 0) or 0) for entry in entries.values())
        scores[(unit['name'], stat)] = 1.0+improved
    unit_name, stat = learner._weighted_choice(rng, scores)
    unit = next(entry for entry in parent_scenario['ownUnits'] if entry.get('name') == unit_name)
    entries = learner.local_evidence_for(evidence, encounter, region, unit_name, stat)
    percent = learner.choose_local_percent(rng, entries)
    parameter = search_contract.stat_parameter(stat)
    minimum, maximum = search_contract.stat_bounds()[parameter]
    current = search_contract.battle_value(parent_scenario, unit, parameter) or minimum
    # A local probe must be relative to this parent's actual value. A region-wide anchor can be
    # far away from it and silently turn a +/-5% probe into a large jump.
    target_value = max(int(minimum), min(int(maximum), int(round(current*(1+percent/100.0)))))
    if target_value == current:
        target_value = max(int(minimum), min(int(maximum),
                                             current + (1 if percent >= 0 else -1)))
    try:
        # `mutate(..., op='set-stat')` randomly picks among all eligible humans. The value above was
        # computed for `unit`, so use the named legal primitive or the change is not local at all.
        child = search_contract.set_stat(parent_scenario, unit['name'], stat, target_value)
    except (search_contract.ContractError, ValueError):
        return None
    change = search_contract.describe_change(parent_scenario, child)
    operation = 'set-stat'
    # A compensation pair only after enough single-variable probes failed to improve this region.
    # With no evidence, pairing two variables obscures which direction caused the result.
    if (sum(int((entry or {}).get('n', 0) or 0) for entry in entries.values())
            >= learner.LOCAL_PAIR_MIN_OBSERVATIONS
            and not any((entry or {}).get('improved') for entry in entries.values())):
        partner = next((other for pair in pairs if stat in pair for other in pair if other != stat),
                       None)
        if partner and (unit, partner) in searchable:
            partner_entries = learner.local_evidence_for(evidence, encounter, region,
                                                         unit_name, partner)
            partner_percent = learner.choose_local_percent(rng, partner_entries)
            partner_parameter = search_contract.stat_parameter(partner)
            low, high = search_contract.stat_bounds()[partner_parameter]
            partner_unit = next((entry for entry in child['ownUnits']
                                 if entry.get('name') == unit['name']), None)
            if partner_unit is not None:
                partner_value = search_contract.battle_value(child, partner_unit, partner_parameter) \
                    or low
                partner_target = max(int(low), min(int(high),
                                                  int(round(partner_value*(1+partner_percent/100.0)))))
                try:
                    child = search_contract.set_stat(child, unit['name'], partner, partner_target)
                    operation = 'set-stat-pair'
                    change = search_contract.describe_change(parent_scenario, child)
                    change = f'{change}' if change else f'{stat}{percent:+d}% + {partner}{partner_percent:+d}%'
                except (search_contract.ContractError, ValueError):
                    pass
    return child, operation, stat, change, target_value


def spawn_children(store, encounter, count, proposal_number, diagnostics=None, *, sources=None,
                   reseed=False):
    """Generate up to `count` independent children from this encounter's evidence-bearing parents.

    This is the change that gives the worker pool something useful to do: instead of waiting for one
    candidate's bank to finish before proposing one more, a proposal turn can branch several siblings
    off known parents at once. Only parents with evidence are used (lane members, region nominees, or
    the encounter's own population for the exploration share), the operator and numeric step are drawn
    from the learner's measured weights, and every child records its lineage.

    Two callers share this: ordinary branching (the normal search mechanism) and the anti-starvation
    reseed path (`reseed=True`, `sources=RESEED_SOURCES`), which is used only while sustained idle
    workers prove normal branching cannot keep the pool fed. A reseed is labelled in the lineage
    (`source='reseed:<kind>'`), deliberately includes stat-space exploration (the learned operator
    weighting is free to down-weight a stat that has not paid off yet, which is what made stat vectors
    rare in the measured branching runs), draws older lineages back in, and occasionally produces a
    multi-change restart instead of another small nudge.
    """
    diagnostics = diagnostics if diagnostics is not None else {}
    from strategy_optimizer_adapter import stats as adapter_stats
    keys = store.candidate_keys()
    # Only this encounter's candidates, and without their scenarios: the draw needs the pool and its
    # lane evidence, and the one parent it actually picks is read through `store.scenario` (which
    # caches it). Selecting every candidate's scenario here pulled hundreds of documents per turn.
    encounters = store.candidate_encounters()
    rows = [dict(row) for row in store.db.execute('SELECT id, label, source FROM candidate')
            if encounters.get(row['id']) == encounter]
    if not rows:
        return []
    # A proposal only ranks this encounter's resident parents. The meta table also contains
    # aggregate documents for every pruned build in every encounter; decoding them on each
    # sibling batch makes candidate creation grow with the entire library's history.
    aggregate_keys = [f'aggregate:{row["id"]}:{phase}' for row in rows
                      for phase in ('discovery', 'validation')]
    placeholders = ','.join('?' for _ in aggregate_keys)
    aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
        f'SELECT key,value FROM meta WHERE key IN ({placeholders})', aggregate_keys)}
    records = {row['id']: lane_record(row, merge_aggregates(
        aggregates.get(f'aggregate:{row["id"]}:discovery'),
        aggregates.get(f'aggregate:{row["id"]}:validation')),
        encounter=encounter, defeat=int(keys.get(row['id'], '0:0').split(':', 1)[1]))
        for row in rows}
    lanes = elite_lanes(list(records.values()))
    pools = {lane: [] for lane in LANE_NAMES}
    ranks = {}
    for groups in lanes.values():
        for lane, members in groups.items():
            for index, cid in enumerate(members):
                pools[lane].append(cid)
                ranks[cid] = min(ranks.get(cid, 99), index)
    regions = dict(store.get('strategyRegions') or {})
    candidate_regions = store.candidate_regions()
    from strategy_mean_parents import mean_parent_pool
    mean_parents = mean_parent_pool([
        dict(candidate=row['id'], encounterId=encounter,
             defeatCount=int(keys.get(row['id'], '0:0').split(':', 1)[1]),
             region=candidate_regions.get(row['id']),
             total=aggregates.get(f'aggregate:{row["id"]}:validation')) for row in rows])
    pools['mean'] = [entry['candidate'] for entry in mean_parents]
    diagnostics['meanParentPool'] = mean_parents
    # The region nominees are one exemplar per region of this encounter: a genuinely different approach
    # stays in the parent pool even while another region earns more chests.
    region_pool = []
    seen_regions = set()
    for cid, key in sorted(candidate_regions.items()):
        if not key.startswith(f'{encounter}:') or cid not in records:
            continue
        if key in seen_regions:
            continue
        seen_regions.add(key)
        region_pool.append(cid)
    # Parent selection needs only ancestry root and birth order. Avoid converting the full
    # twelve-column lifetime lineage (including change descriptions) for every proposal turn.
    lineage = {cid: {'root': root, 'created': created}
               for cid, root, created in store.db.execute(
                   'SELECT candidate,root,created FROM lineage WHERE encounterId=?',
                   (int(encounter),))}
    improvements = store.get('childImprovements') or {}
    productivity = {}
    for cid, row in lineage.items():
        entry = productivity.setdefault(row['root'], dict(children=0, improved=0, rate=.5))
        entry['children'] += 1
        entry['improved'] += 1 if (improvements.get(cid) or {}).get('lanes') else 0
    for entry in productivity.values():
        entry['rate'] = (entry['improved'] + 1.0) / (entry['children'] + 2.0)
    operators = list(search_contract.MUTATION_OPS)
    op_stats = dict((store.get('operatorStats') or {}).get(str(encounter)) or {})
    scale_stats = dict((store.get('statScales') or {}).get(str(encounter)) or {})
    anchors = dict((store.get('statAnchors') or {}).get(str(encounter)) or {})
    population = [row['id'] for row in rows]
    by_id = {row['id']: row for row in rows}
    # Which parent families this turn may draw from. Ordinary branching rotates over the four elite
    # lanes plus region nominees and a guaranteed exploration draw; a starvation reseed adds the
    # surviving representatives of older lineages and genuine restarts.
    pools['elite'] = [cid for lane in LANE_NAMES for cid in pools.get(lane, [])]
    pools['elite'] = list(dict.fromkeys(pools['elite'] + pools['mean']))
    root_created = {}
    for cid, row in lineage.items():
        root_created[row['root']] = min(root_created.get(row['root'], row['created']), row['created'])
    ordered = sorted(population, key=lambda cid: (root_created.get(
        (lineage.get(cid) or {}).get('root', cid), 0), cid))
    # "Historical" is the older half of the population by lineage age: a family that stopped being
    # productive stays reachable, so convergence cannot permanently erase it.
    historical_pool = ordered[:max(1, len(ordered)//2)]
    pools['historical'] = historical_pool
    kinds = list(sources) if sources else list(learner.PARENT_SOURCES)
    created = rejected = duplicates = 0
    created_ids = []
    attempt = 0
    # Local exploitation budget for this turn: at most `LOCAL_SHARE` of it, and never more than the
    # global remainder, so global exploration keeps its hard minimum share of every turn.
    local_budget = int(count*learner.LOCAL_SHARE) if not reseed else 0
    local_created = 0
    # One independent synthetic team per normal proposal turn. This is a floor, not a response to
    # starvation: the search keeps trying new team sizes and role mixes while it refines winners.
    fresh_budget = 0 if reseed else max(1, count//4)
    fresh_created = 0
    fresh_indexes = dict(store.get('freshRootIndex') or {})
    supplied = next((row[0] for row in store.db.execute(
        "SELECT c.scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id "
        "WHERE m.encounter=? AND c.source='supplied' ORDER BY c.created LIMIT 1",
        (int(encounter),))), None)
    local_evidence = dict(store.get('localEvidence') or {})
    rng = random.Random((proposal_number or 0)*7919 + 17)
    while created < count and attempt < count*4:
        attempt += 1
        seed = (proposal_number or 0)*100003 + attempt
        if fresh_created < fresh_budget and supplied is not None:
            from strategy_synthetic_roots import synthetic_root
            index = int(fresh_indexes.get(str(encounter), 6))
            fresh_indexes[str(encounter)] = index+1
            store.set('freshRootIndex', fresh_indexes)
            try:
                root, roles = synthetic_root(json.loads(supplied), index,
                                             search_contract.enemy_count(encounter))
                cid, existed = store.add_child(
                    root, f'Fresh team {len(roles)} · {"/".join(roles)}'[:160],
                    lambda: adapter_stats(root), None, 'fresh-root', f'team:{len(roles)}',
                    '/'.join(roles), 'fresh', proposal_number)
            except (search_contract.ContractError, ValueError):
                rejected += 1
                continue
            if existed:
                duplicates += 1
                continue
            created += 1
            fresh_created += 1
            created_ids.append(cid)
            diagnostics['freshRoots'] = diagnostics.get('freshRoots', 0)+1
            continue
        # ---- local exploitation turn: tune one promising family one variable at a time ----------
        if local_created < local_budget and pools.get('elite'):
            elite = [cid for cid in pools['elite'] if cid in records]
            if elite:
                base_id = parent_id = weighted_parent(rng, elite, lineage, productivity)
                parent_scenario = store.scenario(base_id)
                if parent_scenario is not None:
                    steps = _local_probe_steps(rng, parent_scenario, local_evidence,
                                               keys.get(base_id, f'{encounter}:0').split(':', 1)[0])
                    if steps:
                        child, operation, target, change, value = steps
                        label = f'Local {operation}'
                        if change:
                            label += f' · {change}'
                        cid, existed = store.add_child(child, label[:160],
                                                       lambda: adapter_stats(child),
                                                       parent_id, operation, target, change,
                                                       f'local:{target or operation}',
                                                       proposal_number)
                        learner.bump(op_stats.setdefault('local', {}), operation, 'attempts')
                        if existed:
                            duplicates += 1
                            continue
                        created += 1
                        local_created += 1
                        created_ids.append(cid)
                        diagnostics['localProposals'] = diagnostics.get('localProposals', 0)+1
                        continue
        source = kinds[((proposal_number or 0) + attempt) % len(kinds)]
        if source == 'root':
            # A restart starts its own lineage. Its base is still a real member of this encounter's
            # population - a legal build cannot be invented from nothing - but the several changes it
            # receives make it a new root rather than a child of the build it started from.
            base_id = weighted_parent(rng, population, lineage, productivity)
            parent_id = None
        else:
            pool = region_pool if source == 'region' else pools.get(source) or population
            pool = [cid for cid in pool if cid in records] or population
            base_id = parent_id = weighted_parent(rng, pool, lineage, productivity)
        parent_scenario = store.scenario(base_id)
        if parent_scenario is None:
            rejected += 1
            continue
        if reseed and rng.random() < learner.RESEED_STAT_SHARE:
            # Deliberate stat exploration: a reseed's share of stat moves is fixed rather than learned,
            # so a stat whose changes have not yet paid off still gets fresh probes.
            operation = 'set-stat'
            target_operation = 'set-stat'
        else:
            operation = learner.choose_operator(op_stats, operators, rng)
            target_operation = operation
        target = None
        value = None
        stat_unit_name = None
        if operation == 'set-stat':
            eligible = [(unit, name) for unit in parent_scenario['ownUnits'] if unit.get('human')
                        for name in {**search_contract.CORE_STATS, **search_contract.INT_STAT}
                        if search_contract.stat_is_searchable(parent_scenario, unit, name)]
            searchable = list(dict.fromkeys(name for _unit, name in eligible))
            if not searchable:
                rejected += 1
                continue
            # Which stat to move is learned too: a stat whose changes have improved something keeps a
            # larger share, and every searchable stat keeps the floor share.
            weights = learner.operator_weights(op_stats, [f'stat:{name}' for name in searchable])
            target = learner._weighted_choice(rng, weights).split(':', 1)[1]
            unit = rng.choice([entry for entry, name in eligible if name == target])
            stat_unit_name = unit['name']
            parameter = search_contract.stat_parameter(target)
            minimum, maximum = search_contract.stat_bounds()[parameter]
            current = search_contract.battle_value(parent_scenario, unit, parameter) or minimum
            value, scale = learner.choose_stat_target(
                rng, current, minimum, maximum, scale_stats.setdefault(str(parameter), {}),
                anchors.get(str(parameter)),
                scales=learner.RESEED_STAT_SCALES if reseed else None)
            learner.bump(scale_stats[str(parameter)], scale, 'planned')
        try:
            if operation == 'set-stat':
                child = search_contract.set_stat(parent_scenario, stat_unit_name, target, value)
            else:
                child = search_contract.mutate(parent_scenario, seed, op=operation, stat=target,
                                               value=value)
        except (search_contract.ContractError, ValueError):
            rejected += 1
            learner.bump(op_stats, target_operation, 'attempts')
            continue
        if reseed and rng.random() < learner.RESEED_RESTART_SHARE:
            # A genuine restart rather than another local nudge: one or two further legal changes on
            # top of the first, so the candidate is not a neighbour of a single elite.
            for _ in range(rng.randint(1, 2)):
                try:
                    child = search_contract.mutate(child, rng.randrange(1 << 31))
                except (search_contract.ContractError, ValueError):
                    break
            operation = 'restart'
        change = search_contract.describe_change(parent_scenario, child)
        label = (f'Reseed {proposal_number}.{attempt} · {source} · {operation}' if reseed
                 else f'Branch {proposal_number}.{attempt} · {operation}')
        if change:
            label += f' · {change}'
        cid, existed = store.add_child(child, label[:160], lambda: adapter_stats(child), parent_id, operation,
                                      target, change, f'reseed:{source}' if reseed else source,
                                      proposal_number)
        learner.bump(op_stats, target_operation, 'attempts')
        if operation == 'set-stat':
            learner.bump(op_stats, f'stat:{target}', 'attempts')
            learner.bump(scale_stats.setdefault(str(search_contract.stat_parameter(target)), {}),
                         scale, 'attempts')
        if existed:
            duplicates += 1
            continue
        created += 1
        created_ids.append(cid)
        region = learner.strategy_region(child)
        if f'{encounter}:{region}' not in regions:
            # First candidate to reach a region becomes its nominee; the index table already carries the
            # per-candidate region, so nothing is rewritten here.
            regions[f'{encounter}:{region}'] = cid
        diagnostics.setdefault('sources', {})[source] = \
            diagnostics.setdefault('sources', {}).get(source, 0) + 1
        diagnostics.setdefault('operations', {})[operation] = \
            diagnostics.setdefault('operations', {}).get(operation, 0) + 1
        if reseed:
            diagnostics.setdefault('reseedSources', {})[source] = \
                diagnostics.setdefault('reseedSources', {}).get(source, 0) + 1
    with store.db:
        store.set('operatorStats', {**(store.get('operatorStats') or {}), str(encounter): op_stats})
        store.set('statScales', {**(store.get('statScales') or {}), str(encounter): scale_stats})
        store.set('strategyRegions', regions)
    diagnostics['created'] = diagnostics.get('created', 0) + created
    diagnostics['rejected'] = diagnostics.get('rejected', 0) + rejected
    diagnostics['duplicates'] = diagnostics.get('duplicates', 0) + duplicates
    return created_ids


def starvation_action(level, since_last_reseed, open_seeds, reservoir_target,
                      interval=None, batch=None):
    """`(create, count)` for one planning pass of the anti-starvation controller.

    The control law, in one testable place: nothing happens while the sustained utilisation is healthy
    (`level == 0`), nothing happens while the reservoir still holds enough runnable work, and nothing
    happens more often than the reseed interval. That is what keeps the mechanism pressure-controlled
    instead of a second, unbounded generator - the "867 children" failure mode cannot reoccur through
    this path even if the pressure stays at its highest level for hours.
    """
    interval = learner.RESEED_INTERVAL_SECONDS if interval is None else interval
    batch = learner.RESEED_BATCH if batch is None else batch
    if not level or since_last_reseed < interval or open_seeds >= reservoir_target:
        return False, 0
    return True, batch[min(int(level), len(batch) - 1)]


def unfinished_child(cid, limits, ordinals):
    """True while `cid` is a generated child whose first discovery bank is still open.

    `MAX_OPEN_PER_ENCOUNTER` bounds how many *unevaluated children* one encounter may hold, so the
    count must include only children the generator created and has not yet evaluated. Probe, saved and
    supplied builds are complete measured experiments, not speculation: their staged discovery limit
    *is* their current discovery count (`staged_limits` returns `(nd, bank)` for a probe), so testing
    `ordinals < DISCOVERY_RUNS` alone counted every banked probe as an unevaluated child forever. In
    the focused-search stall that miscount was decisive: one fight's banked probes filled the cap, so
    the encounter could neither propose nor reseed and the whole search idled with nothing dispatchable.
    Requiring an unfinished bank as well (`ordinal < dlimit`) restores the child-only accounting
    without changing how a real child is counted.
    """
    dlimit = limits[cid][0]
    ordinal = ordinals.get((cid, 'discovery'), 0)
    return ordinal < learner.DISCOVERY_RUNS and ordinal < dlimit


def focused_replenish_action(focus_encounter, runnable_seeds, since_last_reseed,
                             level, interval=None, batch=None):
    """`(create, count)` when a focused fight has nothing dispatchable at all, else `(False, 0)`.

    The utilisation controller only reacts to a sustained *global* shortfall, but a focused search is
    a stricter case: every new attempt must belong to one fight, so if that fight's scope holds no
    runnable seed the scheduler is stalled by construction whatever the pool-wide window reports. The
    bounded reseed is therefore authorised on the dry focused scope itself. It still never fires more
    often than the reseed interval and still draws its batch from the measured `RESEED_BATCH`, so a
    focused fight cannot become the unbounded generator the "867 children" failure mode was.
    """
    interval = learner.RESEED_INTERVAL_SECONDS if interval is None else interval
    batch = learner.RESEED_BATCH if batch is None else batch
    if focus_encounter is None or runnable_seeds or since_last_reseed < interval:
        return False, 0
    return True, batch[max(1, min(int(level or 0), len(batch) - 1))]


def focused_stall_reason(focus_encounter, inhabitants, runnable_seeds, dispatched, created,
                         population_blocked):
    """One actionable sentence for a Running search that has nothing to dispatch, or None.

    `inhabitants` is how many candidates the focused fight holds, `runnable_seeds` how many seeds the
    staged policy still authorises in the focused scope, `dispatched` the tasks handed to workers this
    pass, `created` the candidates branched or reseeded this pass, and `population_blocked` whether the
    population guard refused the last proposal. It returns a reason only when the search genuinely has
    no work in scope, so a busy pool or a deliberately duty-cycle-idling one is never labelled idle -
    the status can never claim active search that is not happening.
    """
    if focus_encounter is None or dispatched or created or runnable_seeds:
        return None
    if not inhabitants:
        return (f'Focused encounter {focus_encounter} has no candidates in this library. Choose '
                'another fight or clear the focus; there is nothing to branch this one from.')
    if population_blocked:
        return (f'Focused encounter {focus_encounter} has no runnable seeds and every remaining slot '
                'is a protected build, so no legal mutation could be added. Clear the focus, or open a '
                'new library to keep searching this fight.')
    return (f'Focused encounter {focus_encounter} has no runnable seeds and no new legal candidate '
            'could be generated this pass. The search keeps retrying; clear the focus to spread work '
            'across the other fights.')


def reseed_load(store, limits, ordinals):
    """`(per-encounter load, reseeds held, reseeds ever created)` for the starvation controller.

    Useful-work starvation is per encounter as much as global, so the controller asks two questions of
    every encounter: how much runnable work it still has (`runnable`), how many of its children are
    still unevaluated (`open`), and how many starvation-created candidates it is already holding
    (`reseeds`, counted from the lineage of the candidates still present - a pruned reseed stops
    counting, which is what makes the bound a permanent guardrail rather than a one-off budget).
    `created` counts every reseed an encounter has ever been given, pruned ones included, because
    that - not the surviving count - is what stops one fight from monopolising fresh exploration.
    """
    rows = store.db.execute(
        "SELECT encounterId, COUNT(*) FROM lineage WHERE source LIKE 'reseed:%' "
        'AND candidate IN (SELECT id FROM candidate) GROUP BY encounterId')
    reseeds = {int(row[0]): int(row[1]) for row in rows if row[0] is not None}
    ever = store.db.execute(
        "SELECT encounterId, COUNT(*) FROM lineage WHERE source LIKE 'reseed:%' "
        'GROUP BY encounterId')
    created = {int(row[0]): int(row[1]) for row in ever if row[0] is not None}
    index = store.candidate_encounters()
    load = {}
    for cid, (dlimit, vlimit) in limits.items():
        encounter_id = index.get(cid)
        if encounter_id is None:
            continue
        entry = load.setdefault(int(encounter_id), dict(runnable=0, open=0))
        entry['runnable'] += max(0, dlimit-ordinals.get((cid, 'discovery'), 0))
        entry['runnable'] += max(0, vlimit-ordinals.get((cid, 'validation'), 0))
        if unfinished_child(cid, limits, ordinals):
            entry['open'] += 1
    return load, reseeds, created


def reseed_ranked(store, limits, ordinals):
    """`(encounters in preference order, reseeds per encounter)` for a starvation reseed.

    The choice goes to the encounter with the *least* runnable work, then the fewest open children,
    then the fewest reseeds it has ever been given: one fight with a huge runnable bank must not
    hide the fact that another has no active exploration, and one fight - fastest or slowest - must
    not consume every fresh candidate. The ever-count is the tie-break rather than the surviving
    count because a reseed that fills an encounter's only free slot is itself the first thing
    replaced, so a survivor count would keep offering the turn to the same fight. The whole order is
    returned rather than only the best, because an encounter can
    refuse a proposal (its active population is all protected builds); the caller then offers the
    decision to the next one instead of losing the turn.
    """
    load, reseeds, created = reseed_load(store, limits, ordinals)
    by_encounter = {str(key): value for key, value in reseeds.items()}
    if sum(reseeds.values()) >= learner.MAX_RESEED_TOTAL:
        return [], by_encounter
    available = [encounter_id for encounter_id, entry in load.items()
                 if reseeds.get(encounter_id, 0) < learner.MAX_RESEED_PER_ENCOUNTER
                 and entry['open'] < learner.MAX_OPEN_PER_ENCOUNTER]
    available.sort(key=lambda key: (load[key]['runnable'], load[key]['open'],
                                    created.get(key, 0), key))
    return available, by_encounter


def reseed_target(store, limits, ordinals):
    """`(best encounter or None, reseeds per encounter)` - the first of `reseed_ranked`."""
    available, by_encounter = reseed_ranked(store, limits, ordinals)
    return (available[0] if available else None), by_encounter


def encounter_scope(encounter_of, focus_encounter):
    """The candidate ids a focused scheduler may dispatch, or None for the whole population.

    `encounter_of` maps candidate id -> encounter id (the store's own index). With no focus the whole
    population is in scope, which is what leaves the unfocused plan the same as before. With a focus,
    only that encounter's candidates are in scope, so no other fight's run can be handed to a worker
    by the ready list.
    """
    if focus_encounter is None:
        return None
    return {cid for cid, encounter_id in encounter_of.items() if encounter_id == focus_encounter}


def interleave_ready(limits, ordinals, reserved, focus_ids=None):
    """Ready `(candidate, phase, ordinal)` seeds, interleaved across candidates.

    The one place a ready list is built, so it is also the one place focus has to bite. `focus_ids`
    narrows the population to those candidate ids (None = the whole population); the focused
    scheduler therefore builds its ready list from the focused fight's candidates only, so every seed
    a worker is handed belongs to that encounter.
    """
    per_candidate = {}
    for cid, (dlimit, vlimit) in limits.items():
        if focus_ids is not None and cid not in focus_ids:
            continue
        nd = ordinals.get((cid, 'discovery'), 0)
        nv = ordinals.get((cid, 'validation'), 0)
        seeds = [(cid, 'discovery', ordinal) for ordinal in range(nd, dlimit)]
        seeds += [(cid, 'validation', ordinal) for ordinal in range(nv, vlimit)]
        seeds = [seed for seed in seeds if seed not in reserved]
        if seeds:
            per_candidate[cid] = deque(seeds)
    order = []
    while per_candidate:
        for cid in list(per_candidate):
            order.append(per_candidate[cid].popleft())
            if not per_candidate[cid]:
                del per_candidate[cid]
    return order


def focused_encounters(encounter_ids, focus_encounter):
    """Narrow an encounter list to the focused fight, preserving order; None keeps them all.

    Used by the proposal and reseed controllers, so while a fight is focused the reservoir is refilled
    with *that* fight's work instead of spending proposals, and population slots, on fights whose runs
    the scheduler would then refuse to dispatch.
    """
    if focus_encounter is None:
        return list(encounter_ids)
    return [encounter_id for encounter_id in encounter_ids if encounter_id == focus_encounter]


def focused_exploit_target(record, focus_encounter, best_mean=0, band_size=0):
    """The focused exploitation target for one build, or None to leave the staged policy alone.

    `record` is one candidate's planning evidence: `candidate`, `encounterId`, `validationRuns`, `wins`,
    `winInterval` (the Wilson interval of its own validation bank), `earnedMean` (the validation
    bank's mean chests per resolved run, a loss counting the zero its native gate released), and
    `comparable` (a complete selection bank with every run resolved and prize-read - the store's own
    comparability rule). `best_mean` is the fight's best comparable build's `earnedMean` and
    `band_size` how many comparable builds sit within `FOCUS_EXPLOIT_COMPETITIVE_SHARE` of it, so
    "competitive" means this build is in that band *and* the band has a rival - a lone best build is a
    contender, not a race. The bank's own mean is used deliberately, never the lifetime maximum: one
    lucky seed must not select a build.

    A build that has not passed screening - an incomplete or non-comparable bank, or no win at all -
    returns None, so the 8/24/64 staged policy still governs it and a weak idea still stops early. A
    build that has passed earns `FOCUS_EXPLOIT_FLOOR` when its reliability is certified, and the larger
    `FOCUS_EXPLOIT_COMPETITIVE` when it is competitive or its win interval is still too wide to
    certify. Both are capped at `FOCUS_EXPLOIT_CAP`, which is what makes the lane stop.
    """
    if focus_encounter is None or record is None:
        return None
    if int(record.get('encounterId', -1)) != int(focus_encounter):
        return None
    current = int(record.get('validationRuns', 0) or 0)
    wins = int(record.get('wins', 0) or 0)
    if not record.get('comparable') or wins <= 0 or current < VALIDATION_RUNS:
        return None
    interval = list(record.get('winInterval') or [0., 1.])
    certified = float(interval[0]) >= FOCUS_EXPLOIT_CERTIFIED_LOWER
    mean = record.get('earnedMean')
    competitive = (bool(best_mean) and int(band_size) >= 2 and mean is not None
                   and float(mean) >= float(best_mean)*FOCUS_EXPLOIT_COMPETITIVE_SHARE)
    # Real aggregate evidence carries reward variance. Allocate depth to expected-chest
    # precision, not to a win-rate threshold. Keep the legacy input contract for old callers
    # that cannot supply the second moment; do not manufacture zero uncertainty for them.
    if record.get('earnedVariance') is not None and mean is not None:
        from strategy_sampling import precision_budget
        measurement = precision_budget(current, record['earnedVariance'],
                                       maximum=FOCUS_EXPLOIT_CAP)
        width = measurement['approximateHalfWidth']
        competitive = (not best_mean or float(mean) + (width or 0.)
                       >= float(best_mean)*FOCUS_EXPLOIT_COMPETITIVE_SHARE)
        target = measurement['target'] if competitive else FOCUS_EXPLOIT_FLOOR
        target = min(target, FOCUS_EXPLOIT_CAP)
        measurement['target'] = target
        tier = 'capped' if current >= target else ('competitive' if competitive else 'contender')
        return dict(candidate=record.get('candidate'), current=current,
                    tier=tier, certified=False, competitive=competitive, comparable=True,
                    **measurement,
                    reason=(f'expected-chest precision planning target {target}; '
                            'approximate half-width goal 0.5 chests; independent confirmation '
                            'is still required, and the resource ceiling is not confirmation'))
    if certified and not competitive:
        tier, target = 'contender', FOCUS_EXPLOIT_FLOOR
    else:
        tier = 'competitive' if competitive else 'uncertain'
        target = FOCUS_EXPLOIT_COMPETITIVE
    target = min(int(target), FOCUS_EXPLOIT_CAP)
    decision = dict(candidate=record.get('candidate'), target=target, current=current, tier=tier,
                    certified=bool(certified), competitive=bool(competitive), comparable=True)
    if current >= target:
        decision.update(tier='capped',
                        reason=(f'focused exploitation reached its {target}-run target for this '
                                'build; the staged policy and the rest of the fight keep the capacity'))
        return decision
    if tier == 'competitive':
        decision['reason'] = (f'competitive on encounter {int(focus_encounter)} (mean earned within '
                              f'{FOCUS_EXPLOIT_COMPETITIVE_SHARE:.0%} of the fight\'s best); '
                              f'target {target} paired runs, {target-current} to go')
    elif tier == 'uncertain':
        decision['reason'] = (f'win interval lower bound {float(interval[0]):.2f} is below '
                              f'{FOCUS_EXPLOIT_CERTIFIED_LOWER:.2f}; target {target} paired runs, '
                              f'{target-current} to go')
    else:
        decision['reason'] = (f'certified contender on encounter {int(focus_encounter)}; target '
                              f'{target} paired runs, {target-current} to go')
    return decision


def focused_record_target(record, focus_encounter, leader_decision=None):
    """The repeat-bank decision for the focused fight's resident Highest Earned record holder.

    `record` is `focused_record_holder`'s summary and `leader_decision` the mean lane's decision for the
    same candidate, when it has one. Unlike the mean lane this does not require a competitive band or a
    converted win in the current validation bank: the record *is* the evidence that the build can
    produce an exceptional outcome, and the whole point of the lane is to find out whether it can do so
    consistently. The bank floor is guaranteed, a still-uncertain record build earns the larger
    "competitive" bank, and a mean-lane grant is never lowered. Both are capped, which is what makes the
    lane stop.
    """
    if focus_encounter is None or record is None:
        return None
    if int(record.get("encounterId", -1)) != int(focus_encounter):
        return None
    current = int(record.get("validationRuns", 0) or 0)
    interval = list(record.get("winInterval") or [0., 1.])
    certified = float(interval[0]) >= FOCUS_EXPLOIT_CERTIFIED_LOWER
    target = FOCUS_RECORD_FLOOR if certified else FOCUS_EXPLOIT_COMPETITIVE
    if leader_decision is not None:
        target = max(target, int(leader_decision.get("target") or 0))
    target = min(int(target), FOCUS_RECORD_CAP)
    value = int(record.get("recordValue") or 0)
    decision = dict(candidate=record.get("candidate"), target=target, current=current,
                    tier="record", certified=bool(certified), record=True, recordValue=value)
    if current >= target:
        decision.update(tier="record-capped",
                        reason=("the Highest Earned record repeat bank reached its " + str(target) +
                                "-run target; the mean lane and novel search keep the capacity"))
        return decision
    decision["reason"] = ("Highest Earned record " + str(value) + " on encounter " +
                          str(int(focus_encounter)) + "; repeating the build toward " + str(target) +
                          " paired runs, " + str(target-current) + " to go")
    return decision


def focused_exploitation(summaries, limits, focus_encounter, budget, groups=(), record=None,
                           record_budget=None):
    """Overlay the bounded focused exploitation lane on a planning `limits` map.

    `summaries` are the per-candidate evidence records `focused_exploit_target` reads; `limits` is the
    staged `{candidate: (discoveryLimit, validationLimit)}` map; `budget` is the total extra runnable
    validation seeds this pass may authorise. The caller sets `budget` to a fraction of the reservoir
    target, so the lane can never take the whole reservoir and novel mutations keep their share.
    `groups` are candidate collections that must be banked together - a fine-tune tested point and its
    frozen parent, which are only meaningful over the same seed ordinals.

    Returns `(limits, assignments)`: a new limit map with the exploitation limits raised in bounded
    per-pass steps, and `{candidate: {target, current, tier, granted, limit, reason}}` diagnostics.
    Only the focused encounter's candidates are ever touched, and a weak build is never touched at all.
    `record` (with `record_budget`) is the resident Highest Earned holder's own repair lane: it
    guarantees a repeat bank for that build even when the mean rule above returns nothing for it,
    banks its fine-tune variants with it, and never lowers a mean-lane grant.
    """
    if focus_encounter is None or not summaries:
        return dict(limits), {}
    encounter = int(focus_encounter)
    # The competitive band is the validation bank's mean earned, losses included as the zero the
    # native gate enforced - not the lifetime maximum, which one lucky seed could set. Only a
    # complete, comparable bank carries a mean, so a partial bank can neither define nor join the band.
    best_mean = max([0.] + [float(summary['earnedMean']) for summary in summaries
                            if int(summary.get('encounterId', -1)) == encounter
                            and summary.get('comparable') and summary.get('earnedMean') is not None])
    band_size = sum(1 for summary in summaries
                    if int(summary.get('encounterId', -1)) == encounter
                    and summary.get('comparable') and summary.get('earnedMean') is not None
                    and best_mean
                    and float(summary['earnedMean']) >= best_mean*FOCUS_EXPLOIT_COMPETITIVE_SHARE)
    decisions = {}
    # A lucky, shallow estimate must not set the admission bar for every rival. Use the
    # strongest lower precision estimate as a planning reference when variances are available.
    lower_estimates = [float(row['earnedMean']) - 1.96 * math.sqrt(
                          max(0., float(row['earnedVariance'])) / max(1, row['validationRuns']))
                       for row in summaries if int(row.get('encounterId', -1)) == encounter
                       and row.get('comparable') and row.get('earnedMean') is not None
                       and row.get('earnedVariance') is not None]
    planning_reference = max(0., max(lower_estimates)) if lower_estimates else best_mean
    for summary in summaries:
        reference = planning_reference if summary.get('earnedVariance') is not None else best_mean
        decision = focused_exploit_target(summary, encounter, reference, band_size)
        if decision is not None:
            decisions[decision['candidate']] = decision
    if not decisions and (record is None or int(record.get('encounterId', -1)) != encounter):
        return dict(limits), {}
    # A pair (tested point and frozen parent) must receive the same bank, so units are the caller's
    # groups; every other build is its own unit.
    partner = {}
    for group in groups or ():
        members = [cid for cid in group if cid in decisions]
        for cid in members:
            partner[cid] = [other for other in members if other != cid]
    units, seen = [], set()
    for cid in sorted(decisions):
        if cid in seen:
            continue
        unit = [cid]
        seen.add(cid)
        for other in partner.get(cid, ()):
            if other not in seen:
                unit.append(other)
                seen.add(other)
        units.append(sorted(unit))
    units.sort(key=lambda unit: (-max(decisions[cid]['target'] for cid in unit),
                                 min(decisions[cid]['current'] for cid in unit), unit[0]))
    if lower_estimates:
        # Rotate progress across precision budgets. Always sorting by the largest target
        # would let four high-variance candidates occupy every depth slot until their caps.
        units.sort(key=lambda unit: (min(decisions[cid]['current'] /
                                        max(1, decisions[cid]['target']) for cid in unit),
                                     min(decisions[cid]['current'] for cid in unit), unit[0]))
    out = dict(limits)
    assignments, left, slots = {}, max(0, int(budget)), FOCUS_EXPLOIT_SLOTS
    # Spread one pass's budget across the exploited builds rather than handing it all to the first:
    # several contenders should advance together, which is what "progressively" means here.
    per_slot = max(1, left // max(1, min(len(units), slots))) if left else 0
    for unit in units:
        if left <= 0 or slots <= 0:
            break
        target = max(decisions[cid]['target'] for cid in unit)
        current = min(decisions[cid]['current'] for cid in unit)
        limit = max(out.get(cid, (0, 0))[1] for cid in unit)
        need = target-max(current, limit)
        if need <= 0:
            for cid in unit:
                assignments[cid] = dict(decisions[cid], granted=0, limit=int(out[cid][1]))
            continue
        grant = min(FOCUS_EXPLOIT_STEP, need, left // len(unit), per_slot)
        if grant <= 0:
            break
        raised = max(limit, current+grant)
        for cid in unit:
            dlimit, _vlimit = out.get(cid, (0, 0))
            out[cid] = (dlimit, raised)
            assignments[cid] = dict(decisions[cid], granted=grant, limit=raised,
                                    paired=len(unit) > 1)
        left -= grant*len(unit)
        slots -= 1
    # -----------------------------------------------------------------------------------------
    # Record repair lane. The resident Highest Earned holder of the focused fight keeps a repeat
    # bank even when its mean is below the mean leader's, so an exceptional record is studied for
    # repeatability instead of being dropped for a low average. Its own budget is ring-fenced and it
    # never lowers a mean-lane grant: it only guarantees the floor and lets a still-uncertain record
    # build earn the larger bank. The record build and its fine-tune variants are banked together, so
    # a nearby stat variant is measured over the same seed ordinals as its frozen record parent -
    # past the 512-run screening window, not only up to it.
    # -----------------------------------------------------------------------------------------
    if record is not None and int(record.get('encounterId', -1)) == encounter:
        record_id = record.get('candidate')
        by_candidate = {summary.get('candidate'): summary for summary in summaries}
        decision = focused_record_target(record, encounter, assignments.get(record_id))
        if decision is not None and record_id in by_candidate:
            unit = [record_id]
            for group in groups or ():
                if record_id in group:
                    unit.extend(cid for cid in group if cid in out)
            unit = sorted(set(unit))
            for cid in unit:
                member = decisions.get(cid)
                if member is not None:
                    decision['target'] = max(decision['target'], int(member['target']))
            decision['target'] = min(int(decision['target']), FOCUS_RECORD_CAP)
            current = min(int((by_candidate.get(cid) or {}).get('validationRuns', 0) or 0)
                          for cid in unit)
            limit = max(int((out.get(cid) or (0, 0))[1]) for cid in unit)
            left = max(0, int(budget if record_budget is None else record_budget))
            base = max(current, limit)
            room = decision['target']-base
            step = min(FOCUS_RECORD_STEP, room, left // len(unit)) if room > 0 else 0
            prior = assignments.get(record_id)
            before = int((out.get(record_id) or (0, 0))[1])
            if step > 0:
                raised = base+step
                for cid in unit:
                    dlimit, vlimit = out.get(cid, (0, 0))
                    out[cid] = (dlimit, max(int(vlimit), raised))
                assignments[record_id] = dict(
                    decision, granted=int(out[record_id][1])-before,
                    limit=int(out[record_id][1]), paired=len(unit) > 1,
                    meanTier=(prior or {}).get('tier'))
            else:
                capped = base >= decision['target']
                assignments[record_id] = dict(
                    decision, tier=('record-capped' if capped else 'record-waiting'),
                    granted=0, limit=int(out[record_id][1]), paired=len(unit) > 1,
                    meanTier=(prior or {}).get('tier'),
                    reason=(decision.get('reason') if capped else
                            'the record repair budget for this pass is spent; the next pass '
                            'continues the repeat bank'))
    return out, assignments


def focused_exploit_inputs(store, records, aggregates, focus_encounter):
    """`(summaries, groups)` for `focused_exploitation`, read from the store's own indexes.

    `summaries` reduces every candidate's *validation* counters to the fields the target rule reads,
    including the bank's mean earned (`chestSum/chestCount`, a loss contributing the zero its native
    gate released) - never the lifetime maximum, and never mixed with discovery counts. `groups` pairs
    each focused fine-tuning program's tested points with its frozen parent, because a paired
    comparison only means something when both sides carry the same seed bank. Neither reads a stored
    run row or a scenario, so the evidence refresh stays one aggregate query plus one program document.
    """
    summaries = []
    for record in records:
        cid = record['candidate']
        total = aggregates.get(f'aggregate:{cid}:validation') or {}
        n = int(total.get('n', 0) or 0)
        wins = int(total.get('wins', 0) or 0)
        chest_count = int(total.get('chestCount', 0) or 0)
        # A complete, comparable validation bank: at least the screening bank, no unresolved run, and
        # a prize reading on every run. Only then can the bank be averaged at all.
        comparable = bool(total) and n >= VALIDATION_RUNS \
            and int(total.get('censored', 0) or 0) == 0 \
            and int(total.get('count', 0) or 0) == n
        # Mean chests per resolved run over that bank alone, a loss contributing its gate-enforced
        # zero. Nothing from the discovery phase or the lifetime maxima enters here, so one lucky run
        # cannot make a build look competitive.
        earned_mean = None
        if comparable and chest_count == n:
            earned_mean = float(total.get('chestSum', 0.) or 0.)/n
        from strategy_sampling import variance_from_totals
        earned_variance = (variance_from_totals(n, total.get('chestSum', 0.),
                                               total.get('chestSum2'))
                           if earned_mean is not None else None)
        summaries.append(dict(
            candidate=cid, encounterId=int(record.get('encounterId', -1)),
            validationRuns=n, wins=wins, earnedMean=earned_mean,
            earnedVariance=earned_variance,
            winInterval=wilson(wins, n), comparable=comparable))
    groups = []
    if focus_encounter is not None:
        for program in (store.get('fineTunePrograms') or {}).values():
            if int(program.get('encounterId', -1)) != int(focus_encounter):
                continue
            members = {program['parent']['candidateId']}
            members.update(point.get('candidateId')
                           for point in (program.get('points') or {}).values()
                           if point.get('candidateId'))
            if len(members) > 1:
                groups.append(frozenset(members))
    return summaries, groups


def focused_record_holder(store, focus_encounter, aggregates=None):
    """The resident Highest Earned record holder of the focused encounter, or `(None, report)`.

    Identity comes from the canonical stored `recordHolders` - the same map the overview and the replay
    path read - never from the published page or a page-local copy. The focused encounter may carry
    several difficulties; the highest record across them is the one worth repeating, ties broken by
    holder key so one library always selects the same build.

    A holder whose candidate is no longer resident is reported as `pruned` and *not* replaced: the
    record's frozen scenario still replays (it travels with the holder), but there is no stored build
    left to repeat or tune, and silently freezing some other parent instead would misrepresent the
    record. A holder whose candidate has not yet completed the screening validation bank is reported as
    `screening`, so a one-run fluke is repeated only once there is a build to repeat.
    """
    if focus_encounter is None:
        return None, dict(status='none',
                          reason='No encounter is focused; record repair waits until one is.')
    encounter = int(focus_encounter)
    holders = store.get('recordHolders') or {}
    prefix = 'earned:' + str(encounter) + ':'
    keys = sorted(key for key in holders if key.startswith(prefix))
    if not keys:
        return None, dict(status='none', encounterId=encounter,
                          reason=('no Highest Earned record holder exists for encounter ' +
                                  str(encounter) + ' yet'))
    key = min(keys, key=lambda name: (-int((holders[name] or {}).get('value') or 0), name))
    holder = holders[key] or {}
    candidate = holder.get('candidate')
    row = store.db.execute('SELECT label, source FROM candidate WHERE id=?', (candidate,)).fetchone()
    if row is None:
        return None, dict(status='pruned', key=key, candidate=candidate, encounterId=encounter,
                          value=holder.get('value'),
                          reason=('the Highest Earned record holder for encounter ' + str(encounter) +
                                  ' (' + str(holder.get('value')) + ' chests) has been pruned; its '
                                  'frozen replay is kept and no other build is silently substituted'))
    total = (aggregates or {}).get('aggregate:' + str(candidate) + ':validation')
    if total is None:
        total = store.get('aggregate:' + str(candidate) + ':validation') or {}
    total = total or {}
    n = int(total.get('n', 0) or 0)
    wins = int(total.get('wins', 0) or 0)
    unresolved = int(total.get('censored', 0) or 0)
    chest_count = int(total.get('chestCount', 0) or 0)
    if n < FOCUS_RECORD_MIN_RESOLVED or unresolved:
        return None, dict(status='screening', key=key, candidate=candidate, encounterId=encounter,
                          value=holder.get('value'), resolved=n, unresolved=unresolved,
                          reason=('the Highest Earned record holder for encounter ' + str(encounter) +
                                  ' has not completed its screening validation bank yet (' + str(n) +
                                  ' resolved, ' + str(unresolved) + ' unresolved)'))
    return dict(candidate=candidate, encounterId=encounter,
                defeat=int(holder.get('defeatCount') or 0), label=row['label'],
                source=row['source'], recordValue=holder.get('value'), recordKey=key,
                recordPhase=holder.get('phase'), recordOrdinal=holder.get('ordinal'),
                validationRuns=n, wins=wins, unresolved=unresolved,
                earnedMean=(float(total.get('chestSum', 0.) or 0.)/n if n else None),
                winInterval=wilson(wins, n)), None


def normalize_focus_encounter(value, catalogue):
    """`(encounter id or None)` for a focus command, raising on a value that cannot be honoured.

    `catalogue` is the set of encounter ids the recovered catalogue offers. Clearing the focus is
    always legal; focusing a fight the engine does not know is refused, so the UI can never show a
    focus the scheduler would silently ignore.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('A focus needs an encounter id, or null to clear it.')
    if isinstance(value, float) and not value.is_integer():
        raise ValueError('A focus needs a whole encounter id, or null to clear it.')
    try:
        encounter_id = int(value)
    except (TypeError, ValueError):
        raise ValueError('A focus needs an encounter id, or null to clear it.') from None
    if catalogue and encounter_id not in catalogue:
        raise ValueError(f'Encounter {encounter_id} is not in the recovered catalogue.')
    return encounter_id


def normalize_focus_encounters(values, catalogue):
    """`[encounter ids]` for a multi-select campaign focus, validated and de-duplicated.

    An empty list means "every encounter". Every id must exist in the recovered catalogue, so the
    UI can never store a selection the scheduler would silently ignore. Order is preserved as the
    caller sent it; the campaign schedules in catalogue order regardless.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set)):
        raise ValueError('A campaign focus needs a list of encounter ids, or [] for all.')
    selected = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError('A campaign focus needs whole encounter ids.')
        if isinstance(value, float) and not value.is_integer():
            raise ValueError('A campaign focus needs whole encounter ids.')
        try:
            encounter_id = int(value)
        except (TypeError, ValueError):
            raise ValueError('A campaign focus needs whole encounter ids.') from None
        if catalogue and encounter_id not in catalogue:
            raise ValueError(f'Encounter {encounter_id} is not in the recovered catalogue.')
        if encounter_id not in selected:
            selected.append(encounter_id)
    return selected


def repair_learner_evidence(store):
    """Once per library, discard corrupt learning counters while retaining their raw snapshot.

    Version 1 could repeatedly observe an active child after lexical truncation, and its local
    region keys did not match the probe lookup. Version 2 inferred local direction from lane maxima
    instead of comparing matched discovery seeds. Version 3 mixed the six fighters' stat evidence
    and only probed the first fighter. Runs and candidates remain intact; the next
    `observe_children` rebuilds evidence from resident candidates with completed first banks.
    """
    previous_version = int(store.get('learnerEvidenceVersion') or 0)
    if previous_version >= 4:
        return
    fields = ('observedChildren', 'childImprovements', 'operatorStats', 'statScales',
              'statAnchors', 'localEvidence', 'laneImprovementCount', 'newRegionCount')
    old = {field: store.get(field) for field in fields}
    with store.db:
        archive_key = f'learnerEvidencePreRepairV{previous_version or 1}'
        if any(old.values()) and store.get(archive_key) is None:
            store.set(archive_key, old)
        for field in fields:
            store.set(field, 0 if field in ('laneImprovementCount', 'newRegionCount')
                      else [] if field == 'observedChildren' else {})
        store.set('learnerEvidenceVersion', 4)


def changed_stat_unit(parent_scenario, child_scenario, parameter):
    """The one human whose named stat changed, or None for a nonlocal change."""
    parents = {unit.get('name'): unit for unit in parent_scenario.get('ownUnits', [])
               if unit.get('human')}
    changed = []
    for child_unit in child_scenario.get('ownUnits', []):
        parent_unit = parents.get(child_unit.get('name'))
        if parent_unit is None or not child_unit.get('human'):
            continue
        before = search_contract.battle_value(parent_scenario, parent_unit, parameter)
        after = search_contract.battle_value(child_scenario, child_unit, parameter)
        if before != after:
            changed.append((child_unit['name'], before, after))
    return changed[0] if len(changed) == 1 else None


def paired_local_outcome(store, parent_id, child_id):
    """Compare a numeric probe with its parent on the same discovery seed ordinals.

    The global lanes retain rare high-water marks; the local direction learner needs repeatable
    differences, so one lucky maximum cannot declare a direction successful. Actual released
    chests take precedence, with queued opportunity breaking a tie when both results report it.
    """
    def bank(cid):
        return {int(ordinal): json.loads(result) for ordinal, result in store.db.execute(
            'SELECT ordinal,result FROM run WHERE candidate=? AND phase=? AND ordinal<?',
            (cid, 'discovery', learner.DISCOVERY_RUNS))}

    parents, children = bank(parent_id), bank(child_id)
    if len(parents.keys() & children.keys()) < learner.DISCOVERY_RUNS:
        return None
    improved = worsened = 0
    for ordinal in parents.keys() & children.keys():
        parent, child = parents[ordinal], children[ordinal]
        parent_chests, _ = chest_count(parent)
        child_chests, _ = chest_count(child)
        if parent_chests is None or child_chests is None:
            continue
        comparison = child_chests - parent_chests
        if comparison == 0:
            parent_potential = potential_chests(parent)
            child_potential = potential_chests(child)
            if parent_potential is not None and child_potential is not None:
                comparison = child_potential - parent_potential
        improved += comparison > 0
        worsened += comparison < 0
    return ('improved' if improved > worsened else
            'worsened' if worsened > improved else 'flat')


def observe_children(store, aggregates, keys, mode='branching'):
    """Fold each child's completed first bank into the learner's evidence.

    Runs once per child (the ids are remembered) and records, per encounter: which lanes the child
    improved over its parent, which operator and numeric scale produced it, and - when a stat change
    improved something - the value at which it improved. That is the whole feedback loop: nothing here
    scores a strategy on one number, and a losing child with better potential or a better stored-attack
    chain is recorded as an improvement exactly like a winning one.
    """
    if mode == 'legacy':
        return
    repair_learner_evidence(store)
    # Only resident children can contribute fresh evidence. Lifetime lineage remains available to
    # ancestry views, but scanning every pruned row on each evidence refresh grows without bound.
    lineage = {row['candidate']: dict(row) for row in store.db.execute(
        'SELECT l.* FROM lineage l JOIN candidate c ON c.id=l.candidate '
        'WHERE l.parent IS NOT NULL')}
    if not lineage:
        return
    observed = set(store.get('observedChildren') or [])
    # No scenario column: this runs on every evidence refresh and only the handful of children in
    # `fresh` whose stat change actually improved something needs their own scenario (for the anchor
    # value), so that read happens lazily below instead of pulling every candidate's document.
    rows = {row['id']: dict(row) for row in store.db.execute(
        'SELECT id, label, source FROM candidate')}
    fresh = []
    for cid, row in lineage.items():
        if cid in observed or row.get('parent') is None or cid not in rows:
            continue
        discovery = aggregates.get(f'aggregate:{cid}:discovery') or {}
        if int(discovery.get('n', 0) or 0) < learner.DISCOVERY_RUNS:
            continue
        fresh.append((cid, row))
    if not fresh:
        return

    # Most refreshes have no newly completed child. Load the large learning maps only when
    # there is evidence to fold, preserving the same transaction and first-bank decisions.
    improvements = dict(store.get('childImprovements') or {})
    prior_improved = set(improvements)
    op_stats_all = dict(store.get('operatorStats') or {})
    scale_all = dict(store.get('statScales') or {})
    anchors_all = dict(store.get('statAnchors') or {})
    local_evidence = dict(store.get('localEvidence') or {})
    regions = dict(store.get('strategyRegions') or {})
    candidate_regions = store.candidate_regions()

    def record_for(cid):
        row = rows.get(cid)
        if row is None:
            return None
        key = keys.get(cid, '19:0')
        encounter_id, defeat = (int(part) for part in key.split(':', 1))
        return lane_record(row, merge_aggregates(
            aggregates.get(f'aggregate:{cid}:discovery'),
            aggregates.get(f'aggregate:{cid}:validation')),
            encounter=encounter_id, defeat=defeat)

    for cid, row in fresh:
        child = record_for(cid)
        parent = record_for(row.get('parent'))
        encounter = str(row.get('encounterId'))
        better = learner.improved_lanes(parent, child) if (parent and child) else []
        if parent is None and child is not None:
            # The parent was pruned: the child's own qualification is the evidence that survives.
            better = [lane for lane in LANE_NAMES if lane_qualifies(child, lane)]
        region = regions.get(candidate_regions.get(cid, ''))
        new_region = region == cid
        # A lane improvement and a new strategy region are different kinds of useful result, so they
        # are recorded separately: only the lane half drives the operator/scale learning, while the
        # region half is the diversity signal the parent sources use.
        if better or new_region:
            improvements[cid] = dict(lanes=list(better), region=bool(new_region))
        # Local exploitation evidence: when this child was a single-variable numeric probe, record how
        # that move turned out *for this encounter and this strategy region*. A direction is never
        # concluded globally - "SPEED +10% improved" means improved for this family, not for the game.
        if (child is not None and parent is not None and row.get('operation') == 'set-stat'
                and row.get('target')):
            try:
                parameter = search_contract.stat_parameter(row['target'])
            except search_contract.ContractError:
                parameter = None
            if parameter is not None:
                parent_scenario = store.scenario(row.get('parent'))
                child_scenario = store.scenario(cid)
                if parent_scenario and child_scenario:
                    change = changed_stat_unit(parent_scenario, child_scenario, parameter)
                    if change is not None:
                        unit_name, before, after = change
                        if before:
                            percent = learner.local_scale_bucket(after/before - 1.0)
                            better_lanes = list(better)
                            worse_lanes = [lane for lane in LANE_NAMES
                                           if parent and lane_qualifies(parent, lane)
                                           and lane_qualifies(child, lane)
                                           and lane_key(child, lane) < lane_key(parent, lane)]
                            outcome = paired_local_outcome(store, row.get('parent'), cid)
                            if outcome is None:
                                outcome = learner.local_outcome(better_lanes, worse_lanes)
                            # The probe was chosen from the parent's region. `candidate_regions`
                            # includes an encounter prefix, while `local_key` adds that prefix itself;
                            # use the raw region hash that `_local_probe_steps` looks up.
                            region = learner.strategy_region(parent_scenario)
                            key = learner.local_key(int(row.get('encounterId') or 0), region,
                                                    unit_name, row['target'], percent)
                            entry = dict(local_evidence.get(key) or
                                         dict(improved=0, flat=0, worsened=0, n=0))
                            entry[outcome] = int(entry.get(outcome, 0) or 0)+1
                            entry['n'] = int(entry.get('n', 0) or 0)+1
                            if after:
                                entry['anchor'] = int(after)
                            local_evidence[key] = entry
        stats_for = op_stats_all.setdefault(encounter, {})
        learner.bump(stats_for, row.get('operation') or 'unknown',
                     'improved' if better else 'unproductive')
        if new_region:
            learner.bump(stats_for, row.get('operation') or 'unknown', 'newRegion')
        if better and row.get('operation') == 'set-stat' and row.get('target'):
            parameter = None
            try:
                parameter = search_contract.stat_parameter(row['target'])
            except search_contract.ContractError:
                parameter = None
            if parameter is not None:
                # The one scenario this path needs, read on demand (and cached by the store).
                scenario = store.scenario(cid)
                origin = store.scenario(row.get('parent'))
                change = (changed_stat_unit(origin, scenario, parameter)
                          if origin and scenario else None)
                if change is not None:
                    value = change[2]
                    if value:
                        # The learner's anchor for this stat is the value that actually improved
                        # something, so later refinements cluster around a measured good region.
                        anchors_all.setdefault(encounter, {})[str(parameter)] = int(value)
        observed.add(cid)
    # Cumulative counters live beside the per-child map: the map is bounded to the children still in
    # the population (that is all the planner asks it about), while these keep the session totals the
    # diagnostics report - so a long search neither grows the map without limit nor loses its count.
    lane_total = int(store.get('laneImprovementCount') or 0) + sum(
        1 for cid, entry in improvements.items() if entry.get('lanes') and cid not in prior_improved)
    region_total = int(store.get('newRegionCount') or 0) + sum(
        1 for cid, entry in improvements.items()
        if entry.get('region') and cid not in prior_improved)
    improvements = {cid: entry for cid, entry in improvements.items() if cid in rows}
    with store.db:
        # Only current candidates can enter `fresh`. Keep every one of their ids, regardless of
        # lexical order. The old `sorted(observed)[-4000:]` dropped active low-sorting ids once a
        # long-lived library passed 4000 children, causing their evidence to be counted again on
        # every refresh. Pruned candidates need no marker because they cannot re-enter `fresh`.
        store.set('observedChildren', sorted(observed.intersection(rows)))
        store.set('childImprovements', improvements)
        store.set('operatorStats', op_stats_all)
        store.set('statScales', scale_all)
        store.set('statAnchors', anchors_all)
        store.set('localEvidence', local_evidence)
        store.set('laneImprovementCount', lane_total)
        store.set('newRegionCount', region_total)


def lane_leaders(records, lanes):
    """The leader of every lane with the numbers the page shows, including the evidence behind it."""
    by_id = {record['candidate']: record for record in records}
    leaders = {}
    for key, groups in lanes.items():
        leaders[key] = {}
        for lane in LANE_NAMES:
            members = [by_id[cid] for cid in groups[lane] if cid in by_id]
            if not members:
                leaders[key][lane] = None
                continue
            leader = members[0]
            leaders[key][lane] = dict(
                candidate=leader['candidate'], label=leader['label'], source=leader['source'],
                # The lane's own primary number, the evidence behind it and the reliability reading
                # that the final recommendation weighs but discovery does not require.
                metrics=dict(earned=leader['earnedMax'], potential=leader['potentialMax'],
                             resources=leader['meanResources'], winRate=leader['winRate'],
                             winLower=(leader['winInterval'] or [None])[0], runs=leader['n'],
                             progress=leader['progress'], progressRuns=leader['progressRuns']),
                members=len(members), pool=len(groups[lane]),
                validated=bool(leader['n'] >= VALIDATION_RUNS))
    return leaders


def chest_count(row):
    """Chests one resolved run released, with the basis for that number. Returns (value, basis).

    This is the number the player actually watches, so it is derived rather than declared:

      * unresolved (censored) - unknown. The fight never reached a verdict, so nothing is claimed.
      * a loss - 0, from the recorded native dispatch gate. A losing run can queue prizes (259 of the
        261 stored losses here did) but the gate dispatches no chest, which is exactly why a raw
        callback count must never be shown as a chest result.
      * a win - the certified award when the runner holds one, otherwise the queued count at the
        verdict (`pendingChests`, or `prizeCallbacks` on rows written before that field existed).
        Every win in the stored library so far carries at least one, which is the player's own
        expectation of a victory.

    The number is what the fight produced and released, not an inventory receipt: automatic Finish
    and world collection remain unproven, so the label must stay "chests", never "retained".
    """
    if row.get('censored') or row.get('verdict') is None:
        return None, 'unresolved'
    if row['verdict'] == 2:
        return 0, 'loss-gate'
    if row['verdict'] != 1:
        return None, 'unknown-verdict'
    reward = row.get('rewardOutcome') or {}
    awarded = reward.get('awardedChests')
    if isinstance(awarded, (int, float)) and not isinstance(awarded, bool):
        basis = ('certified-award'
                 if reward.get('awardedBasis') == 'reward-entitlement-certificate' else 'awarded')
        return int(awarded), basis
    pending = reward.get('pendingChests')
    if isinstance(pending, (int, float)) and not isinstance(pending, bool):
        return int(pending), 'queued-at-victory'
    prizes = row.get('prizeCallbacks')
    if isinstance(prizes, (int, float)) and not isinstance(prizes, bool):
        return int(prizes), 'queued-at-victory'
    return None, 'no-data'


def _default_horizon():
    """The declared search horizon, read from the one module that owns it."""
    import strategy_search
    return strategy_search.DEFAULT_TICK_LIMIT


def stored_scope(store):
    """The library's declared scope as a mapping (`scope()` is stored as canonical JSON text)."""
    value = store.get('scope')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def horizon_ticks(store):
    """The simulation safety limit this library is scoped to, in combat ticks.

    `scope()` is stored as the canonical *string* it is compared with, so the value read back is
    JSON text rather than a mapping; decode it before looking for the horizon.
    """
    scope = store.get('scope')
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except ValueError:
            scope = None
    if isinstance(scope, dict) and isinstance(scope.get('tickLimit'), int):
        return int(scope['tickLimit'])
    return _default_horizon()


def finetune_bounds(parameter_id):
    """(minimum, maximum) for one combat parameter from the derived bounds, or None.

    The derived walls live in recovered evidence and may be absent (the focused-search checks run
    without them). A missing or incomplete table leaves the axis unbounded rather than substituting an
    invented wall, and the schedule then clamps only at zero.
    """
    try:
        return search_contract.stat_bounds().get(int(parameter_id))
    except search_contract.ContractError:
        return None


def potential_chests(row):
    """The chest opportunity a resolved LOSS reached, before the native loss gate discarded it.

    A loss dispatches no chest (`chest_count` returns 0 from the recorded gate), so the number worth
    showing for a defeat is the opportunity the fight actually built: the queued/pending count from
    the reward reading when the row carries one, else the pre-verdict prize-callback count the
    compact result has always recorded. Returns None when the row carries neither.
    """
    reward = row.get('rewardOutcome') or {}
    pending = reward.get('pendingChests')
    if isinstance(pending, (int, float)) and not isinstance(pending, bool):
        return int(pending)
    callbacks = row.get('prizeCallbacks')
    if isinstance(callbacks, (int, float)) and not isinstance(callbacks, bool):
        return int(callbacks)
    return None


#: The five battle lines the overview shows, in the order the desktop presents them, keyed by the
#: recovered boss/family name. The catalogue is the source of truth for which encounters belong to
#: each line; only the card's own wording lives here.
CAMPAIGN_ORDER = ('Kairobot Mage', 'Aloha Kairobot', 'Kairobot Knight', 'Kairo Kommander',
                  'Wairo Dungeon')
CAMPAIGN_TITLE = {'Kairobot Mage': "Kairobot Mage's"}
DIFFICULTY_ORDER = {'Easy': 0, 'Normal': 1, 'Hard': 2, 'Extreme': 3}
DUNGEON_FAMILY = 'Wairo Dungeon'
_CAMPAIGN_TITLE_RE = re.compile(r'^Vs\.\s+(?P<figure>.+?)\s+\((?P<difficulty>[^)]+)\)$')


def scenario_changes(baseline, scenario):
    """Concrete, checkable ways a strategy differs from its encounter's supplied baseline.

    Only differences the two scenarios actually prove are reported, comparing unit by unit on the
    shared names: a stat tag is emitted only for a parameter that really moved, a skill tag only when
    the activation order changed, and so on. Nothing here is a judgement about quality, and an
    unknown or renamed roster reports the roster change instead of a guessed stat claim.
    """
    if not isinstance(baseline, dict) or not baseline:
        return []
    import strategy_probe
    tags = []
    base_units = {unit.get('name'): unit for unit in baseline.get('ownUnits') or []}
    units = {unit.get('name'): unit for unit in scenario.get('ownUnits') or []}
    base_order = [unit.get('name') for unit in baseline.get('ownUnits') or []]
    order = [unit.get('name') for unit in scenario.get('ownUnits') or []]
    if set(units) != set(base_units):
        tags.append('Roster changed')
    if order != base_order:
        tags.append('Formation changed')
    if any(list(unit.get('skills') or []) != list((base_units.get(name) or {}).get('skills') or [])
           for name, unit in units.items() if name in base_units):
        tags.append('Different skill order')
    if any((unit.get('prePlacement') or {}).get('cell')
           != ((base_units.get(name) or {}).get('prePlacement') or {}).get('cell')
           for name, unit in units.items() if name in base_units):
        tags.append('Placement changed')
    for _axis, (parameter, label) in sorted(strategy_probe.STAT_AXES.items()):
        moved = []
        for name, unit in units.items():
            other = base_units.get(name)
            if other is None:
                continue
            mine = ((unit.get('parameters') or {}).get(parameter) or {}).get('rawValue')
            theirs = ((other.get('parameters') or {}).get(parameter) or {}).get('rawValue')
            if mine is None or theirs is None or mine == theirs:
                continue
            moved.append(int(mine) > int(theirs))
        if moved:
            direction = 'Higher' if sum(moved) * 2 >= len(moved) else 'Lower'
            tags.append(f'{direction} {label}')
    if scenario.get('inputs') or scenario.get('items') or scenario.get('itemStock'):
        tags.append('Uses consumables')
    elif (scenario.get('holyHerbStock') or 0) != (baseline.get('holyHerbStock') or 0):
        tags.append('Different Holy Herb stock')
    return tags


def baseline_scenarios(base, catalogue=None):
    """One supplied baseline per encounter/difficulty the recovered catalogue describes.

    A library has to *contain* a fight before the search can propose anything for it, so a new
    library starts with every encounter instead of only the one its base scenario happened to carry.
    The encounter set comes from the catalogue (never a hand-written list), and the labels are the
    catalogue's own titles, so the desktop shows recovered names rather than ids.
    """
    catalogue = encounter_catalogue() if catalogue is None else catalogue
    if not catalogue:
        return [(base, 'Recovered UREF baseline')]
    scenarios = []
    for key in sorted(catalogue, key=int):
        entry = catalogue[key]
        scenarios.append((dict(base, encounterId=int(key)),
                          f"Baseline · {entry.get('title') or f'encounter {key}'}"))
    return scenarios


def encounter_campaigns(catalogue):
    """Group the recovered catalogue into the five battle lines and their four difficulties."""
    """The five encounter cards and their four difficulties, derived from the recovered catalogue.

    A `Vs. <boss> (<difficulty>)` title is one difficulty of that boss's line. The dungeon records do
    not use that shape (they name the stage itself), so they are the one hand-grouped line - the
    grouping is display metadata, while every id, title, level and enemy count still comes from the
    catalogue. The grouping is derived so a catalogue change cannot silently mis-file an encounter.
    """
    families = {}
    for key, entry in catalogue.items():
        title = entry.get('title') or f'Encounter {key}'
        match = _CAMPAIGN_TITLE_RE.match(title)
        if match:
            family, difficulty = match.group('figure'), match.group('difficulty')
        else:
            family, difficulty = DUNGEON_FAMILY, title
        families.setdefault(family, []).append(dict(
            encounterId=int(key), label=difficulty, title=title,
            level=entry.get('level'), enemyCount=entry.get('enemyCount'),
            boss=entry.get('boss')))
    campaigns = []
    for family in CAMPAIGN_ORDER:
        rows = families.get(family)
        if not rows:
            continue
        rows.sort(key=lambda row: (DIFFICULTY_ORDER.get(row['label'], 9), row['encounterId']))
        campaigns.append(dict(key=family, title=CAMPAIGN_TITLE.get(family, family),
                              boss=family if family != DUNGEON_FAMILY else None, difficulties=rows))
    # Any future catalogue entry that joins neither shape still has to be visible somewhere.
    for family, rows in families.items():
        if family in CAMPAIGN_ORDER:
            continue
        rows.sort(key=lambda row: row['encounterId'])
        campaigns.append(dict(key=family, title=family, boss=None, difficulties=rows))
    return campaigns


def encounter_stats(store):
    """The persisted lifetime record for every encounter/difficulty, reconstructed once if needed.

    The aggregates are written by `Store.record` inside the same transaction as the run, so they are
    authoritative and monotonic. A library written before that existed is reconstructed exactly once
    from its surviving rows. Where rows are provably missing, the attempt shortfall is attributed
    only if the library can be shown to hold a single encounter (the stored scope names it and every
    surviving run belongs to it); otherwise the shortfall is reported as unattributed instead of
    being invented, and the maxima are flagged as the best *known* values rather than complete.
    """
    stored = store.get('encounterLifetime')
    if stored is not None:
        return stored
    lifetime = {}
    holders = {}
    for row in store.db.execute(
            'select r.candidate as candidate, c.scenario as scenario, r.phase as phase, '
            'r.ordinal as ordinal, r.result as result '
            'from run r join candidate c on c.id = r.candidate'):
        scenario = json.loads(row['scenario'])
        result = json.loads(row['result'])
        key = encounter_key(scenario)
        entry = lifetime.setdefault(key, blank_lifetime())
        entry['lifetimeAttempts'] += 1
        verdict = None if result.get('censored') else result.get('verdict')
        if verdict is None:
            entry['lifetimeNoVerdict'] += 1
            continue
        if verdict == 2:
            entry['lifetimeLosses'] += 1
            value = potential_chests(result)
            if value is not None and (entry['highestPotentialChests'] is None
                                      or value > entry['highestPotentialChests']):
                entry['highestPotentialChests'] = value
                entry['potentialBasis'] = 'best-known-after-reconstruction'
                holders[f'potential:{key}'] = record_holder(
                    scenario, row['candidate'], row['phase'], row['ordinal'],
                    result.get('seeds') or (0, 0), verdict, value, result.get('digest'))
        elif verdict == 1:
            entry['lifetimeWins'] += 1
            value, basis = chest_count(result)
            if value is not None and (entry['highestChestsEarned'] is None
                                      or value > entry['highestChestsEarned']):
                entry['highestChestsEarned'] = value
                entry['earnedBasis'] = basis
                holders[f'earned:{key}'] = record_holder(
                    scenario, row['candidate'], row['phase'], row['ordinal'],
                    result.get('seeds') or (0, 0), verdict, value, result.get('digest'))
    survivors = sum(entry['lifetimeAttempts'] for entry in lifetime.values())
    total = int(store.get('totalRuns') or 0)
    shortfall = max(0, total - survivors)
    scope_record = store.get('scope')
    if isinstance(scope_record, str):
        try:
            scope_record = json.loads(scope_record)
        except ValueError:
            scope_record = None
    scope_encounter = (str(int(scope_record['encounterId']))
                       if isinstance(scope_record, dict) and 'encounterId' in scope_record else None)
    single = {key.split(':')[0] for key in lifetime}
    if shortfall and len(single) == 1 and scope_encounter in single:
        # The library was scoped to this encounter and holds no other, so the lifetime counter can
        # only have belonged to it. The attempt total becomes the lifetime counter exactly.
        only = next(iter(lifetime))
        lifetime[only]['lifetimeAttempts'] = total
        lifetime[only]['reconstructedAttempts'] = shortfall
    elif shortfall:
        lifetime.setdefault('unattributed', blank_lifetime())['unattributedAttempts'] = shortfall
    flags = dict(reconstructed=True, survivors=survivors, totalRuns=total, shortfall=shortfall,
                 maximaComplete=shortfall == 0, singleEncounter=len(single) == 1,
                 scopeEncounter=scope_encounter)
    with store.db:
        store.set('encounterLifetime', lifetime)
        store.set('recordHolders', holders)
        store.set('encounterLifetimeReconstruction', flags)
    return lifetime


#: One encounter/difficulty's lifetime record, persisted with every accepted run so that pruning a
#: candidate can never lower it. `lifetimeAttempts` counts accepted completed simulations; the two
#: chest maxima follow the same canonical gates the run cards use (a defeat releases none, so its
#: number is the opportunity it reached).
LIFETIME_FIELDS = ('lifetimeAttempts', 'lifetimeWins', 'lifetimeLosses', 'lifetimeNoVerdict',
                   'highestPotentialChests', 'highestChestsEarned')


def encounter_key(scenario):
    """`"<encounterId>:<defeatCount>"` - the encounter/difficulty an overview row belongs to."""
    return f"{int(scenario['encounterId'])}:{int(scenario.get('defeatCount') or 0)}"


def blank_lifetime():
    return dict(lifetimeAttempts=0, lifetimeWins=0, lifetimeLosses=0, lifetimeNoVerdict=0,
                highestPotentialChests=None, highestChestsEarned=None,
                potentialBasis=None, earnedBasis=None)


def holder_summary(holders):
    """The record holders without their frozen scenarios: the page needs identity, the host needs
    the scenario to replay. Keys stay `"<metric>:<encounterId>:<defeatCount>"`."""
    return {key: {field: value for field, value in holder.items() if field != 'scenario'}
            for key, holder in holders.items()}


def load_holder(path, key):
    """Read one persisted record holder (scenario included) straight from a library file.

    The holder carries its own frozen scenario, so a record stays replayable even after the candidate
    that set it has been pruned out of the library.
    """
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='recordHolders'").fetchone()
    finally:
        db.close()
    holders = json.loads(row[0]) if row else {}
    return holders.get(key)


def record_holder(scenario, candidate, phase, ordinal, seeds, verdict, value, digest):
    """The minimal immutable data needed to replay a lifetime record after candidate pruning.

    The candidate row can be deleted, so the scenario itself travels with the record rather than a
    reference to it; everything else is what `simulate` needs (seeds) plus the verdict and the
    number being claimed, so the page can replay and re-check the claim.
    """
    return dict(encounterId=int(scenario['encounterId']),
                defeatCount=int(scenario.get('defeatCount') or 0),
                candidate=candidate, phase=phase, ordinal=int(ordinal),
                mathSeed=int(seeds[0]), libSeed=int(seeds[1]), verdict=verdict, value=int(value),
                digest=digest, tickLimit=int(scenario.get('tickLimit') or 0),
                label=None, scenario=scenario)


def _parsed_meta(value):
    """A stored meta blob as its mapping (`None` stays `None` for an absent counter)."""
    return json.loads(value) if value else None


def migrate_objective(store):
    """Move a pre-redesign library onto the lane objective without resimulating one battle.

    What can be reused: every stored run's own compact result. The per-candidate aggregate counters are
    a pure function of those results, so they are recomputed from the stored rows (which is what the
    lane evidence reads), and the quality-diversity archive is re-derived under the new admission rule.

    What cannot be reconstructed: the stored-attack metrics. They were not recorded before this
    objective existed, and they cannot be inferred from `prizeCallbacks` - a prize callback says a
    chest was queued, not whether a stored command released after the boss died produced it. Rows
    written before the change therefore carry no `progressMetrics`, the setup lane holds no members
    for them, and the migration records the counts so the UI can say exactly that.

    The migration is explicit, transactional and idempotent: nothing is reinterpreted as though it had
    been produced by the new objective, and running it twice changes nothing.
    """
    rows = list(store.db.execute('SELECT id, scenario FROM candidate'))
    aggregates = {}
    with_progress = without_progress = 0
    previous_version = store.get('objectiveVersion', 1)
    with store.db:
        for row in rows:
            for phase in ('discovery', 'validation'):
                total = None
                for stored in store.db.execute(
                        'SELECT result FROM run WHERE candidate=? AND phase=? ORDER BY ordinal',
                        (row['id'], phase)):
                    result = json.loads(stored[0])
                    if isinstance(result.get('progressMetrics'), dict):
                        with_progress += 1
                    else:
                        without_progress += 1
                    total = accumulate(total, result)
                if total is not None:
                    store.set(f'aggregate:{row["id"]}:{phase}', total)
                    aggregates[(row['id'], phase)] = total
        archive = {}
        for row in rows:
            runs = [json.loads(stored[0]) for stored in store.db.execute(
                'SELECT result FROM run WHERE candidate=? AND phase=? ORDER BY ordinal',
                (row['id'], 'validation'))][:VALIDATION_RUNS]
            score = quality(summarize(runs))
            if score is None:
                continue
            scenario = json.loads(row['scenario'])
            cell = encounter_family(scenario, runs)
            if cell not in archive or tuple(archive[cell][1]) < score:
                archive[cell] = (row['id'], score)
        store.db.execute('DELETE FROM archive')
        for cell, (cid, score) in archive.items():
            store.db.execute('INSERT INTO archive VALUES (?,?,?)', (cell, cid, canonical(score)))
        store.set('objectiveVersion', OBJECTIVE_VERSION)
        store.set('objectiveMigration', dict(
            previousVersion=previous_version, version=OBJECTIVE_VERSION, at=time.time(),
            candidates=len(rows), archiveCells=len(archive),
            runsReused=with_progress+without_progress,
            runsWithProgressMetrics=with_progress,
            runsWithoutProgressMetrics=without_progress,
            reconstructed=['per-candidate discovery/validation aggregates',
                           'quality-diversity archive under the new admission rule',
                           'elite-lane membership and leaders'],
            notReconstructable=['stored-attack progress metrics for runs recorded before the '
                                'objective change (the setup lane therefore starts empty)']))
    return store.get('objectiveMigration')


def full_candidate_payloads(store):
    """The original full-rebuild candidate list, kept as the reference the cache is checked against.

    It is deliberately the naive implementation: every candidate reread and reparsed on every call.
    `check_optimizer_publish.py` requires the cached view to be deep-equal to this one after every
    kind of change, so the cache can never quietly serve stale state.
    """
    baselines = {}
    for c in store.candidates():
        if c['source'] != 'supplied':
            continue
        scenario = json.loads(c['scenario'])
        baselines.setdefault(scenario['encounterId'], scenario)
    representative, equivalence_counts = store.equivalence_view()
    candidates = []
    for c in store.candidates():
        discovery = store.rows(c['id'], 'discovery')
        validation = store.rows(c['id'], 'validation')
        scenario = json.loads(c['scenario'])
        combined = discovery + validation
        potential = [value for value in (potential_chests(row) for row in combined
                                         if not row.get('censored') and row.get('verdict') == 2)
                     if value is not None]
        earned = [value for value in (chest_count(row)[0] for row in combined
                                      if not row.get('censored') and row.get('verdict') == 1)
                  if value is not None]
        candidates.append(dict(id=c['id'], label=c['label'], source=c['source'],
            scenario=scenario, stats=json.loads(c['stats']),
            changes=scenario_changes(baselines.get(scenario['encounterId']), scenario),
            equivalentTo=representative.get(c['id'], c['id']),
            equivalenceCount=equivalence_counts.get(c['id'], 1),
            bestPotentialChests=max(potential) if potential else None,
            bestChestsEarned=max(earned) if earned else None,
            discovery=aggregate_summary(store.get(f'aggregate:{c["id"]}:discovery'), discovery),
            validation=aggregate_summary(store.get(f'aggregate:{c["id"]}:validation'), validation),
            selection=summarize(validation[:VALIDATION_RUNS]),
            validationRuns=store.next_ordinal(c['id'], 'validation'),
            family=encounter_family(scenario, validation[:VALIDATION_RUNS] or discovery) if (validation or discovery) else None,
            encounterId=scenario['encounterId'],
            # The lane evidence this candidate contributes, merged over both phases. It is part of the
            # published record so no consumer has to re-read the stored rows to rank a lane.
            evidence=lane_record(dict(id=c['id'], label=c['label'], source=c['source'],
                                      scenario=c['scenario']),
                                 merge_aggregates(store.get(f'aggregate:{c["id"]}:discovery'),
                                                  store.get(f'aggregate:{c["id"]}:validation')),
                                 encounter=scenario['encounterId'],
                                 defeat=scenario.get('defeatCount') or 0),
            examples=interesting(validation or discovery)))
    return candidates


def interesting(rows):
    if not rows:
        return {}
    good = [r for r in rows if r['verdict'] == 1 and not r['censored']]
    bad = [r for r in rows if r['verdict'] == 2 and not r['censored']]
    result = {}
    def describe(row):
        """The stored row plus the two fields the run summary presents."""
        value, basis = chest_count(row)
        return dict(row, chests=value, chestBasis=basis)
    if good:
        median = statistics.median(r['prizeCallbacks'] or 0 for r in good)
        # "Most chests" is chosen on the released chest count, not on raw callback activity.
        result['Typical success'] = describe(min(good, key=lambda r: abs((r['prizeCallbacks'] or 0)-median)))
        result['Most chests'] = describe(max(good, key=lambda r: (chest_count(r)[0] or 0)))
        result['Lowest-yield success'] = describe(min(good, key=lambda r: (chest_count(r)[0] or 0)))
        result['Fewest survivors on success'] = describe(min(good, key=lambda r: r['survivors']))
    if bad:
        result['Failure'] = describe(bad[0])
    unresolved = [r for r in rows if r['censored']]
    if unresolved:
        # The card's own wording for a run that reached the simulation safety limit with no verdict.
        result['No verdict by limit'] = describe(unresolved[0])
    return result


class Store:
    def __init__(self, path, provenance):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA wal_autocheckpoint=128')
        self.db.execute('PRAGMA journal_size_limit=1048576')
        page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
        self.db.execute(f'PRAGMA max_page_count={library_capacity_pages(MAX_DB_MB, page_size)}')
        # (objective mode, candidate) -> the archive decision for that candidate's immutable
        # selection bank. One small tuple per held candidate, dropped when the candidate is pruned.
        self._selection = {}
        # candidate id -> '<encounter>:<defeat>' for the lifetime record, so an accepted run does not
        # have to read and parse the candidate's stored scenario again.
        self._encounter_keys = {}
        # Batched record bookkeeping. `batch_size` of 1 keeps the historical behaviour exactly (one
        # transaction per result); the coordinator raises it so up to N results share one transaction
        # and one `synchronous=FULL` commit. `_next` is the authoritative next ordinal per
        # (candidate, phase) while results are accepted but not yet written, so the ordering guard and
        # the planner never depend on uncommitted rows.
        self.batch_size = 1
        self.batch_seconds = 0.
        self._batch = []
        self._batch_keys = set()
        self._batch_started = None
        self._next = {}
        # A page-cap/storage-full failure is not retried by status publication or shutdown.
        self._flush_blocked = False
        # A view of a fine-tuning program only changes when one of its own run banks changes.
        # Increment after a successful commit, never for accepted but uncommitted results.
        self._run_revisions = {}
        # Milestone-level revisions, bumped only when a candidate crosses an evidence rung or is added
        # or pruned. Analyses that are expensive to recompute key on this, not on `_run_revisions`.
        self._milestone_revisions = {}
        # Flush accounting: where the batched durable work actually goes. `_flush_apply_seconds` is
        # the Python/statement work, `_flush_commit_seconds` is the transaction commit (its
        # `synchronous=FULL` fsync, plus the WAL checkpoint SQLite runs inside a commit when the WAL
        # crosses `wal_autocheckpoint`), and the checkpoint counters record when that last happened.
        self._flush_apply_seconds = 0.
        self._flush_commit_seconds = 0.
        self._checkpoints = 0
        self._checkpoint_seconds = 0.
        self._wal_high = 0
        #: Called with a candidate id whenever pruning removes it, so a coordinator that keeps planner
        #: state of its own can drop that candidate from it in the same instant.
        self.on_prune = None
        # One parsed scenario per candidate, on demand: a branching turn only ever needs the parents
        # it actually draws, not every candidate's stored scenario. Dropped when a candidate is pruned.
        self._scenario_cache = {}
        # Candidate metadata is immutable until a candidate is added or pruned. The coordinator
        # asks for these maps on every dispatch pass, so repeated full-table reads waste the time
        # in which workers wait for their next battle.
        self._candidate_meta_cache = None
        self._elite_cache = {}
        # The read-only equivalence index: `equivalence_identity` (inert parameters 20/21/22 erased)
        # -> which stored candidate represents that strategy for each candidate id. Built once from
        # the stored scenarios when first needed and maintained as candidates are added or pruned, so
        # a proposal that differs from a held candidate only in an inert parameter is refused before
        # it can spend a battle - including against a legacy library written before this rule existed.
        # Nothing here is ever written back to the database: historical ids, raw scenarios and runs
        # stay untouched.
        self._equivalence = None
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL,
                label TEXT NOT NULL, source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS candidate_source_active ON candidate(source, id);
            CREATE TABLE IF NOT EXISTS run(candidate TEXT NOT NULL, phase TEXT NOT NULL,
                ordinal INTEGER NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(candidate,phase,ordinal));
            CREATE TABLE IF NOT EXISTS archive(cell TEXT PRIMARY KEY, candidate TEXT NOT NULL,
                quality TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS predictor_history(
                candidate TEXT PRIMARY KEY, scenario_zlib BLOB NOT NULL,
                root TEXT NOT NULL, encounter INTEGER NOT NULL, created INTEGER NOT NULL,
                discovery TEXT, validation TEXT, pruned INTEGER NOT NULL);
        ''')
        # Whether this library existed before this open. Captured here, before the schema/setup
        # writes below, so the coordinator can tell a brand-new library (Community-first default)
        # from an existing one (mode disabled until the user activates it explicitly).
        self.fresh = self.get('schema') is None
        with self.db:
            if self.get('schema') is None:
                self.set('schema', SCHEMA)
                self.set('provenance', provenance)
                self.set('proposals', 0)
                self.set('improvements', 0)
                self.set('totalRuns', 0)
                # A library created now is created under the lane objective. Libraries written before
                # it carry no version at all, which reads as generation 1 and is never silently
                # upgraded: `migrate_objective` has to be asked for.
                self.set('objectiveVersion', OBJECTIVE_VERSION)
                # The search space a library was generated under. Version 1 is every library written
                # before the hard contract existed; those remain readable but never receive new
                # proposals from a generator that follows different rules.
                self.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            if self.get('schema') != SCHEMA:
                raise ValueError('Unsupported optimiser database version; use another library.')
        # The lineage table lives outside `candidate` on purpose: pruning a candidate row must never
        # erase the ancestry that explains its surviving descendants.
        learner.store_schema(self.db)
        # The compact per-seed evidence table is additive and separate from `run`: it keeps the
        # fields the chest/pairing readers need past the rolling replay window. Creating it is a
        # no-op for a library that already has it.
        strategy_evidence.store_schema(self.db)
        # The rebellions' ledger, added the same way and for the same reason: a tier, a version and the
        # set of rules a child broke cannot be read back off `lineage` without changing a table every
        # part of the search writes to, and a library written before the rebels existed must read back
        # unchanged. `CREATE TABLE IF NOT EXISTS` is a no-op on every library that already has it.
        students.store_schema(self.db)
        old = self.get('provenance')
        self.compatible = old['digest'] == provenance['digest'] or same_simulator(old, provenance)
        if self.compatible and old['digest'] != provenance['digest']:
            with self.db:
                history = self.get('schedulerUpdateHistory', [])
                self.set('schedulerUpdateHistory', (history+[dict(previous=old['digest'], updated=time.time())])[-8:])
                self.set('provenance', provenance)
        if not self.compatible:
            self.compatible = encounter_migration.applied(self, provenance)
        # Fold any library written before the evidence table into it, once and idempotently. Only
        # run rows still resident are re-read; a run an earlier prune already evicted is gone and is
        # never claimed reconstructed. Lifetime counters and the provenance keys are untouched.
        with self.db:
            strategy_evidence.ensure_backfill(self.db)

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if not row:
            return default
        if key == 'encounterAware':
            return _shallow_encounter_mapping(row[0])
        return json.loads(row[0])

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, canonical(value)))

    def candidates(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM candidate ORDER BY created,id')]

    def has_candidates(self):
        """Check whether the resident population is nonempty without decoding candidate payloads."""
        return self.db.execute('SELECT 1 FROM candidate LIMIT 1').fetchone() is not None

    def candidate_encounters(self):
        """{candidate id: encounter id}, straight from the index table.

        The planner groups candidates by encounter on every proposal; decoding every stored scenario to
        find that out was the largest avoidable cost in planning once the population grew, so the map
        is written with the candidate and read from the index.
        """
        return self._candidate_meta()['encounters']

    def _candidate_meta(self):
        if self._candidate_meta_cache is None:
            rows = list(self.db.execute('SELECT id, encounter, defeat, region FROM candidate_meta'))
            self._candidate_meta_cache = dict(
                encounters={cid: int(encounter) for cid, encounter, _, _ in rows},
                keys={cid: f'{encounter}:{defeat}' for cid, encounter, defeat, _ in rows},
                regions={cid: f'{encounter}:{region}' for cid, encounter, _, region in rows})
        return self._candidate_meta_cache

    def candidate_keys(self):
        """{candidate id: '<encounter>:<defeat>'} straight from the index table."""
        return self._candidate_meta()['keys']

    def candidate_regions(self):
        """{candidate id: '<encounter>:<region>'} for the diversity signal."""
        return self._candidate_meta()['regions']

    def _ensure_equivalence(self):
        """Build the read-only equivalence index from the stored scenarios, once per open library.

        It derives `equivalence_identity` for every stored scenario and rewrites nothing, so
        historical candidate ids, raw scenarios and runs are untouched. Building it lazily keeps a
        library that is only ever opened for reading from paying for it, and the scan happens at most
        once no matter how many proposals a session makes.
        """
        if self._equivalence is not None:
            return self._equivalence
        key_of, representative, members = {}, {}, {}
        for cid, scenario in self.db.execute('SELECT id, scenario FROM candidate ORDER BY created, id'):
            try:
                key = equivalence_identity(json.loads(scenario))
            except (TypeError, ValueError):
                continue
            key_of[cid] = key
            members.setdefault(key, []).append(cid)
            representative.setdefault(key, cid)
        self._equivalence = dict(key_of=key_of, representative=representative, members=members)
        return self._equivalence

    def _remember_equivalence(self, cid, key):
        """Fold one newly stored candidate into the equivalence index (a no-op before it is built)."""
        if self._equivalence is None:
            return
        self._equivalence['key_of'][cid] = key
        self._equivalence['members'].setdefault(key, []).append(cid)
        self._equivalence['representative'].setdefault(key, cid)

    def _forget_equivalence(self, cid):
        """Drop a pruned candidate and re-elect its group's representative, if it held that role."""
        if self._equivalence is None:
            return
        key = self._equivalence['key_of'].pop(cid, None)
        if key is None:
            return
        members = self._equivalence['members'].get(key)
        if members is not None and cid in members:
            members.remove(cid)
        if not members:
            self._equivalence['members'].pop(key, None)
            self._equivalence['representative'].pop(key, None)
        elif self._equivalence['representative'].get(key) == cid:
            self._equivalence['representative'][key] = members[0]

    def equivalent_candidate(self, scenario):
        """The held candidate that is the same searchable strategy as `scenario`, or None.

        "Same strategy" means equal `equivalence_identity`: identical in every real input - encounter,
        difficulty, horizon, holy herbs, roster, skills, triggers, weapon behaviour, placement and
        every combat parameter - differing at most in the inert parameters (Gathering 20, Move 21,
        Love 22). Encounter, seed, horizon and every combat-stat difference remain distinct.
        """
        return self._ensure_equivalence()['representative'].get(equivalence_identity(scenario))

    def reset_equivalence(self):
        """Drop the cached equivalence index after a bulk population change (a full import reset).

        A single prune updates the index in place; only discarding the whole candidate table needs the
        index rebuilt from scratch, so the next lookup rescans the (now empty) population.
        """
        self._equivalence = None

    def equivalence_view(self):
        """({candidate id: representative id}, {candidate id: group size}) - read-only.

        A candidate that is the only holder of its `equivalence_identity` maps to itself with size 1.
        The representative is the earliest-created holder. This lets ranking collapse legacy inert-only
        duplicates without touching their stored ids or runs.
        """
        state = self._ensure_equivalence()
        representative = {cid: state['representative'][key] for cid, key in state['key_of'].items()}
        counts = {cid: len(state['members'][state['key_of'][cid]]) for cid in state['key_of']}
        return representative, counts

    def region_nominees(self):
        """{'<encounter>:<region>': candidate} - the build that opened each strategy region.

        Derived from the index table and the candidate's own creation stamp rather than kept as a map
        beside the population: the earliest surviving build in a region is its nominee, and rebuilding
        that is a scan the planner can afford once per evidence refresh instead of a whole-population
        rewrite on every child.
        """
        nominees = {}
        for cid, encounter, region in self.db.execute(
                'SELECT m.id, m.encounter, m.region FROM candidate_meta m '
                'JOIN candidate c ON c.id=m.id ORDER BY c.created'):
            nominees.setdefault(f'{encounter}:{region}', cid)
        return nominees

    def scenario(self, candidate):
        """One candidate's stored scenario, parsed once and cached until the candidate is pruned.

        A branching turn only ever needs the parents it draws, so selecting the scenario column for
        the whole population (which the proposal path used to do) was the largest avoidable planning
        cost on a large library: hundreds of multi-kilobyte documents per turn, for a handful of uses.
        """
        if candidate not in self._scenario_cache:
            row = self.db.execute('SELECT scenario FROM candidate WHERE id=?', (candidate,)).fetchone()
            self._scenario_cache[candidate] = json.loads(row[0]) if row else None
        return self._scenario_cache[candidate]

    def lineage(self, encounter_id=None):
        """{candidate id: lineage row} - the ancestry the learner reasons about."""
        return {row['candidate']: row for row in learner.lineage_rows(
            self.db, encounter_id=encounter_id)}

    def observed_scenario(self, scenario, candidate=None):
        """One stored scenario as a *worker* is given it: the build, plus the MP watch list.

        The stored candidate is never touched. `mpWatchUnits` is an observation-only declaration - it
        names the role-resolved DPS and healer whose MP the existing observer samples, cannot spend a
        charge, and is deliberately absent from `identity`, so it can be added to every evaluation
        without renaming a single build or invalidating a library. It is what lets an ordinary no-item
        candidate report that its healer ran out of MP.

        Returns the scenario unchanged when roles cannot be resolved, when the build already declares
        its own herb trigger units, or when anything about the observation is unavailable: a missing
        observation must never stop a battle.
        """
        if scenario is None:
            return None
        try:
            import strategy_mp_recovery
            patch = strategy_mp_recovery.observation_patch(scenario, key=candidate)
        except Exception:  # noqa: BLE001 - observation is optional, the battle is not
            return scenario
        if not patch or scenario.get('mpWatchUnits') == patch['mpWatchUnits']:
            return scenario
        observed = dict(scenario)
        observed.update(patch)
        return observed

    def add(self, scenario, label, source, stats):
        if not self.compatible:
            raise ValueError('Simulator version changed. Open a new library to search; old results remain readable.')
        cid = identity(scenario)
        if self.db.execute('SELECT 1 FROM candidate WHERE id=?', (cid,)).fetchone():
            return cid
        # A candidate that differs from one already held only in an inert parameter (Gathering 20,
        # Move 21, Love 22) is the same searchable strategy: refuse it and hand back the held id, so
        # no second run is allocated and no false variant enters the population. The stored scenario
        # is never rewritten - the held candidate keeps its own raw inert values for seed replay.
        key = equivalence_identity(scenario)
        equivalent = self._ensure_equivalence()['representative'].get(key)
        if equivalent is not None:
            return equivalent
        # The identical build was pruned earlier: put its compact history back before it is inserted, so
        # it returns with the measurements it already earned rather than as an untried candidate.
        resurrected, _detail = self.resurrect(cid)
        encounter_id = int(json.loads(canonical(scenario))['encounterId'])
        # Only pay for the population audit when a bound could actually be reached: `_make_room`
        # re-derives lane records to decide what may be pruned, which is wasted work while the
        # population still has room. Both counts come from the index tables - never from decoding the
        # stored scenarios, which would put the whole population on the per-child hot path.
        same_encounter, total = self.pool_size(encounter_id)
        if same_encounter >= MAX_CANDIDATES_PER_ENCOUNTER or total >= MAX_CANDIDATES_TOTAL:
            # Only pruning needs a durability barrier: buffered results are invisible to its elite
            # guard and could otherwise be reinserted after their candidate was deleted. A normal
            # child insert need not force a FULL sync for every sibling in the proposal batch.
            self.flush()
            self._make_room(encounter_id)
        self.db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                        (cid, canonical(scenario), label[:160], source, canonical(stats), time.time_ns()))
        # The indexes the learner reads every pass - encounter, defeat and the coarse strategy region -
        # are written as one row. They used to be three JSON maps rewritten in full for every child
        # added, which put every other candidate in the library on this hot path.
        stored = json.loads(canonical(scenario))
        learner.record_candidate_meta(self.db, cid, encounter_id,
                                      int(stored.get('defeatCount') or 0),
                                      learner.strategy_region(stored))
        self._candidate_meta_cache = None
        # Every candidate has a lineage row, including a supplied/imported build that is its own root:
        # the learner's branching, productivity and pruning guards all read this table, and a candidate
        # without a row would simply be invisible to them.
        if not self.db.execute('SELECT 1 FROM lineage WHERE candidate=?', (cid,)).fetchone():
            learner.record_child(self.db, cid, None, cid, 0, 'origin', None, 'supplied build',
                                 'origin', encounter_id, 0, search_contract.SEARCH_SPACE_VERSION,
                                 time.time_ns())
        self._remember_equivalence(cid, key)
        return cid

    def pool_size(self, encounter_id):
        """(this encounter's, all encounters') active mutation counts.

        Archive winners and explicit probes are durable evidence, not active search slots. Counting
        them against the mutation ceiling eventually filled the whole library with protected builds
        and stopped all proposals. The active pool stays bounded while those results remain saved.
        """
        same = self.db.execute(
            "SELECT COUNT(*) FROM candidate WHERE source='mutation' AND id NOT IN "
            '(SELECT candidate FROM archive) AND id IN '
            '(SELECT id FROM candidate_meta WHERE encounter=?)',
            (int(encounter_id),)).fetchone()[0]
        total = self.db.execute(
            "SELECT COUNT(*) FROM candidate WHERE source='mutation' AND id NOT IN "
            '(SELECT candidate FROM archive)').fetchone()[0]
        return int(same), int(total)

    def _make_room(self, encounter_id):
        """Bounded active population: per encounter first, then the global ceiling.

        The victim is the oldest *replaceable* build of this encounter. Replaceable is the complement
        of everything the objective must not lose: quality-diversity archive members, the current
        elite lane members of this encounter/difficulty, and the parent of any build still held in
        the population (the learner compares a child with its parent, so that evidence has to
        survive until the comparison is made). All three are bounded - `len(LANE_NAMES) * LANE_POOL`
        lane members, one archive member per cell, and parents whose children's first comparison is
        still pending. Once a child's discovery bank has been folded into `observedChildren`, its
        parent no longer needs protection merely because the child remains resident. A joint-proposal
        child never enters `observedChildren`; its comparison is the durable encounter-aware paired
        experiment, so it releases its parent once that experiment has completed every sample.
        """
        same, total = self.pool_size(encounter_id)
        if same < MAX_CANDIDATES_PER_ENCOUNTER and total < MAX_CANDIDATES_TOTAL:
            return
        protected = {row[0] for row in self.db.execute('SELECT candidate FROM archive')}
        experiment = self.get(strategy_experiments.KEY) or {}
        if experiment:
            protected.add(experiment.get('candidateId'))
            protected.update(arm['candidateId'] for arm in experiment.get('arms', []))
        protected |= self.elite_members(encounter_id)
        # Peak and mechanism leaders do not necessarily include high-average families.
        protected |= self.mean_members(None if total >= MAX_CANDIDATES_TOTAL else encounter_id)
        # A build that holds a **declared, unspent run bank** is work the optimiser has already
        # authorised - a probe rung, Student 9's evaluation, a Breakthrough arm, an MP-recovery child.
        # A brand-new build has no evidence yet, which made it the first thing the population cap
        # pruned, so scheduled work was silently deleted between being created and being measured.
        # Measured on the live library: two MP-recovery children were created, their lineage rows
        # survived (pruning never erases ancestry) and their candidate rows did not, so both requests
        # sat at `observed 0/16` for the rest of the pilot. Protecting the declared bank is the fix.
        protected |= self._declared_bank_candidates()
        # A parent must survive until the learner compares its child after the discovery bank.
        # Protecting it for the child's entire resident lifetime eventually protected nearly every
        # mutation in a mature library and stopped all new proposals at the global cap.
        #
        # The encounter-aware (joint-proposal) search never writes `observedChildren`: its finished
        # paired comparison lives in the durable `ea_*` ledger instead. A joint-proposal child whose
        # frozen experiment has completed every planned sample has therefore already been measured
        # against its reference (the proposal parent), so awaiting legacy discovery for it forever is
        # the stall this guard used to cause. Unfinished EA children and every legacy child keep
        # their protection; unavailable ledger evidence releases nothing (fail closed).
        observed = set(self.get('observedChildren') or [])
        children = list(self.db.execute(
            'SELECT l.candidate,l.parent,l.operation FROM lineage l '
            'JOIN candidate c ON c.id=l.candidate JOIN candidate p ON p.id=l.parent '
            "WHERE p.source='mutation' AND p.id NOT IN (SELECT candidate FROM archive) "
            'AND (? OR p.id IN (SELECT id FROM candidate_meta WHERE encounter=?))',
            (total >= MAX_CANDIDATES_TOTAL, int(encounter_id))))
        released = self._durably_compared_children(
            {child for child, parent, operation in children
             if operation == 'joint-proposal' and child not in observed})
        protected |= {parent for child, parent, operation in children
                      if child not in observed and child not in released}
        victim = None
        if same >= MAX_CANDIDATES_PER_ENCOUNTER:
            # Usually the per-encounter cap is the bound being hit. Stream only that encounter's
            # candidates through the encounter index and stop at the first replaceable one. Building
            # the full `candidate_encounters()` map and full mutation-id list here repeated O(library)
            # Python work for every sibling admitted to a saturated cohort.
            same_encounter = self.db.execute(
                "SELECT c.id FROM candidate_meta m INDEXED BY candidate_meta_encounter "
                "CROSS JOIN candidate c ON c.id=m.id "
                "WHERE m.encounter=? AND c.source='mutation' AND c.id NOT IN "
                '(SELECT candidate FROM archive) ORDER BY c.created',
                (int(encounter_id),))
            victim = next((row[0] for row in same_encounter if row[0] not in protected), None)
        if victim is None and total >= MAX_CANDIDATES_TOTAL:
            # Keep the same oldest-first global fallback, but stream it too; a per-encounter victim
            # is almost always found above, so loading the whole global pool on every prune was wasted.
            replaceable = self.db.execute(
                "SELECT id FROM candidate WHERE source='mutation' AND id NOT IN "
                '(SELECT candidate FROM archive) ORDER BY created')
            victim = next((row[0] for row in replaceable if row[0] not in protected), None)
        if victim is None:
            raise ValueError('This encounter already holds its protected builds. Open a new library '
                             'for additional builds.')
        self._archive_predictor_history(victim)
        # Remember how far it had got *before* its rows are deleted, so a reproposal of the identical
        # build resumes instead of re-running and re-counting the seeds it already measured.
        self._remember_pruned_ordinals(victim)
        self.db.execute('DELETE FROM run WHERE candidate=?', (victim,))
        # Evidence belongs to the candidate: a build that is actually pruned takes its compact
        # pairing history with it. Held candidates (and the rolling replay window) are untouched.
        strategy_evidence.delete_candidate(self.db, victim)
        self.db.execute('DELETE FROM candidate WHERE id=?', (victim,))
        self.db.execute('DELETE FROM meta WHERE key IN (?,?)',
                        (f'aggregate:{victim}:discovery', f'aggregate:{victim}:validation'))
        self._selection.pop((OBJECTIVE_MODE, victim), None)
        self._encounter_keys.pop(victim, None)
        self._scenario_cache.pop(victim, None)
        self._run_revisions[victim] = self._run_revisions.get(victim, 0)+1
        self._next.pop((victim, 'discovery'), None)
        self._next.pop((victim, 'validation'), None)
        self._forget_equivalence(victim)
        if self.on_prune is not None:
            self.on_prune(victim)
        learner.forget_candidate_meta(self.db, victim)
        self._candidate_meta_cache = None

    def _durably_compared_children(self, candidates):
        """Resident joint-proposal children whose encounter-aware paired experiment is finished.

        The legacy guard waits for a child's discovery bank; the encounter-aware search records its
        comparison in the durable ``ea_*`` ledger and never writes ``observedChildren``. This returns
        the subset of `candidates` whose frozen experiment has completed every planned sample, read
        from the persisted budget, links and samples alone - never from a raw scenario or a live
        evaluation.

        One batched query filters links to the relevant resident children before loading their
        completed samples. JSON supplies the bounded identity set without a variable per child or
        a temporary database write. Returns an empty set when the ledger is missing or unreadable: an unavailable
        experiment must fail closed and leave the proposal parent protected.
        """
        candidates = set(candidates)
        if not candidates:
            return set()
        try:
            rows = self.db.execute(
                'SELECT DISTINCT l.candidate_id FROM ea_experiment_budget b '
                'JOIN ea_experiment e ON e.id=b.experiment_id '
                'JOIN ea_sample_link l ON l.experiment_id=b.experiment_id '
                'JOIN ea_sample s ON s.sample_key=l.sample_key '
                'WHERE b.total>0 AND b.completed>=b.total AND s.result IS NOT NULL '
                'AND l.candidate_id IS NOT NULL AND l.candidate_id<>e.reference_id '
                'AND l.candidate_id IN (SELECT value FROM json_each(?))',
                (canonical(sorted(candidates)),))
            finished = {row[0] for row in rows}
        except Exception:  # noqa: BLE001 - an unavailable ledger must not release a parent
            return set()
        return candidates.intersection(finished)

    def _declared_bank_candidates(self):
        """Ids holding a declared, unspent run bank, from every owner that declares one.

        Read from the same maps the scheduler merges into its own `banks`, so "protected" and
        "authorised" cannot disagree. Each owner keeps its bank in one `meta` document, so this costs
        a handful of point reads on the prune path only, never on the per-run path.
        """
        ids = set(dict(self.get('probeBanks') or {}))
        # `strategy_students` names its map `average_banks` (Student 9's evaluation requests); the
        # other two owners expose `banks`. Both are read, so every declared bank is protected however
        # its owner spells the accessor.
        for module in ('strategy_breakthrough', 'strategy_mp_recovery'):
            try:
                banks = __import__(module)
                ids |= set(banks.banks(self))
            except Exception:  # noqa: BLE001 - an unreadable owner must not stop pruning
                continue
        try:
            import strategy_students
            ids |= set(strategy_students.average_banks(self))
        except Exception:  # noqa: BLE001 - same
            pass
        return ids

    def _remember_pruned_ordinals(self, victim):
        """Record how far a build had been measured, so a resurrected copy does not re-run its prefix.

        Pruning deletes the candidate row, its aggregates, its evidence and its run rows - the library
        would otherwise grow without bound - but the *identity* is a hash of the scenario, so the same
        build proposed again gets the same id. Without this record that rebuild starts again at ordinal
        zero: the same seed pairs are run a second time and the same outcomes are counted again, which is
        the recreate-forget-retest loop the trace showed (`8019aafb…` re-added repeatedly as a new child).

        Two integers per pruned build, kept most-recent-first and capped, so the cost of remembering is
        negligible beside the runs it saves.
        """
        ordinals = dict(self.get(PRUNED_ORDINALS_KEY) or {})
        entry = {}
        for phase in ('discovery', 'validation'):
            buffered = self._next.get((victim, phase))
            row = self.db.execute('SELECT MAX(ordinal) FROM run WHERE candidate=? AND phase=?',
                                  (victim, phase)).fetchone()
            from_result = 0 if row is None or row[0] is None else int(row[0])+1
            entry[phase] = max(int(buffered or 0), from_result)
        ordinals.pop(victim, None)
        ordinals[victim] = entry
        if len(ordinals) > PRUNED_ORDINALS_LIMIT:
            for stale in list(ordinals)[:len(ordinals)-PRUNED_ORDINALS_LIMIT]:
                ordinals.pop(stale, None)
        self.set(PRUNED_ORDINALS_KEY, ordinals)

    def resurrect(self, candidate):
        """Restore a pruned build's compact history when the identical build is proposed again.

        `(restored, detail)`: the per-phase aggregates archived at pruning are written back, so the
        staged policy sees the measurements the build already has instead of treating it as untried, and
        the seed ordinals resume after the prefix it was measured over - which is what stops the same
        seed pairs being run and counted twice.

        What is *not* restored is the per-seed evidence rows: those are the bulk of what pruning deletes,
        and keeping them would defeat the population bound. The build therefore comes back with its
        recorded mean, its counters and its provenance, but a paired comparison against it can only use
        runs recorded from now on. That limit is stated rather than hidden.
        """
        history = self.db.execute('SELECT discovery, validation FROM predictor_history WHERE candidate=?',
                                  (candidate,)).fetchone()
        if history is None:
            return False, None
        restored = {}
        for phase, blob in (('discovery', history[0]), ('validation', history[1])):
            if blob:
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                                (f'aggregate:{candidate}:{phase}', blob))
                restored[phase] = True
        ordinals = (self.get(PRUNED_ORDINALS_KEY) or {}).get(candidate) or {}
        for phase, value in ordinals.items():
            self._next[(candidate, phase)] = int(value)
        return True, dict(aggregates=sorted(restored), ordinals=ordinals)

    def _archive_predictor_history(self, candidate):
        """Keep a compact training example before bounded-population pruning.

        Raw runs are still deleted by the existing retention policy, but the
        frozen proposal and its per-phase aggregates remain available for
        future predictors. This is insert-only and in the prune transaction.
        """
        row = self.db.execute('''
            SELECT c.scenario,c.created,m.encounter,l.root
            FROM candidate c JOIN candidate_meta m ON m.id=c.id
            LEFT JOIN lineage l ON l.candidate=c.id WHERE c.id=?''', (candidate,)).fetchone()
        if row is None:
            raise ValueError('Cannot archive a missing candidate before pruning.')
        discovery = self.db.execute('SELECT value FROM meta WHERE key=?',
                                    (f'aggregate:{candidate}:discovery',)).fetchone()
        validation = self.db.execute('SELECT value FROM meta WHERE key=?',
                                     (f'aggregate:{candidate}:validation',)).fetchone()
        self.db.execute('''
            INSERT OR REPLACE INTO predictor_history VALUES (?,?,?,?,?,?,?,?)''',
            (candidate, zlib.compress(row['scenario'].encode('utf-8'), level=1),
             row['root'] or candidate, row['encounter'], row['created'],
             discovery[0] if discovery else None,
             validation[0] if validation else None, time.time_ns()))

    def add_child(self, scenario, label, stats, parent, operation, target, change, lane_source,
                  proposal):
        """Add one generated child together with its lineage row (same transaction as the caller's).

        The lineage records what the mutation *was* - parent, root, depth, operation, target, the
        before/after text and which elite lane or exploration draw nominated the parent - so ancestry
        never has to be reconstructed from a label, and pruning the parent cannot erase it.
        """
        cid = identity(scenario)
        # Fixed-formation policy (opt-in, default off): a generated child that is not the pinned
        # six-unit formation with only Synthetic DPS raw-stat change is refused before insertion.
        import fixed_formation
        fixed_reasons = fixed_formation.gate(scenario)
        if fixed_reasons is not None:
            raise fixed_formation.FixedFormationError(
                'fixed-formation policy refused generated child: ' + '; '.join(fixed_reasons))
        exists = self.db.execute('SELECT 1 FROM candidate WHERE id=?', (cid,)).fetchone()
        if exists:
            # A duplicate proposal is not a new child. In particular it must not replace the
            # original parent/root row: that would corrupt the lineage used to choose future parents.
            return cid, True
        # The identical build was resident earlier and was pruned. It is the same build, so it comes
        # back with its original creation provenance and its compact measured history - and it is
        # reported as *reused*, not as a new child, because nothing about it is new. Overwriting the
        # lineage here would relabel a build the user has already measured as the work of whoever
        # happened to repropose it.
        if self.db.execute('SELECT 1 FROM lineage WHERE candidate=?', (cid,)).fetchone():
            self.add(scenario, label, 'mutation', stats() if callable(stats) else stats)
            return cid, True
        # Same strategy under a different inert value (Gathering 20, Move 21, Love 22): not a child.
        # Returning the held representative with `existed` set means the planner counts a duplicate
        # and spends no run, and the held candidate's lineage is left exactly as it was.
        equivalent = self.equivalent_candidate(scenario)
        if equivalent is not None:
            return equivalent, True
        # This runs once per proposed child. Fetch its one indexed parent row rather than reading
        # the entire lifetime lineage (thousands of rows in a mature library) for every sibling.
        parent_row = (self.db.execute('SELECT root, depth FROM lineage WHERE candidate=?',
                                      (parent,)).fetchone() if parent else None)
        root = parent_row['root'] if parent_row else parent or cid
        depth = (int(parent_row['depth']) + 1) if parent_row else 0
        self.add(scenario, label, 'mutation', stats() if callable(stats) else stats)
        learner.record_child(self.db, cid, parent, root, depth, operation, target, change,
                             lane_source, int(scenario['encounterId']), proposal,
                             search_contract.SEARCH_SPACE_VERSION, time.time_ns())
        return cid, False

    def mean_members(self, encounter_id=None):
        """Bounded high-average family representatives, using all measured validation runs."""
        from strategy_mean_parents import mean_parent_pool
        cache_key = ('mean', encounter_id)
        if cache_key in self._elite_cache:
            return set(self._elite_cache[cache_key])
        sql = ('SELECT c.id,m.encounter,m.defeat,m.region,v.value FROM candidate c '
               'JOIN candidate_meta m ON m.id=c.id '
               "LEFT JOIN meta v ON v.key='aggregate:'||c.id||':validation'")
        if encounter_id is not None:
            sql += ' WHERE m.encounter=?'
        entries = [dict(candidate=cid, encounterId=encounter, defeatCount=defeat,
                        region=region, total=json.loads(total) if total else None)
                   for cid, encounter, defeat, region, total in self.db.execute(
                       sql, () if encounter_id is None else (int(encounter_id),))]
        selected = {entry['candidate'] for entry in mean_parent_pool(entries)}
        self._elite_cache[cache_key] = selected
        return set(selected)

    def elite_members(self, encounter_id=None):
        """The candidate ids currently in any elite lane pool, for the pruning guard.

        Read from the same aggregate counters and the same `elite_lanes` the planner and the published
        view use, so a build that is a lane leader cannot be pruned out from under the objective that
        just elected it. Bounded: `len(LANE_NAMES) * LANE_POOL` per encounter/difficulty.

        Lane pools are per encounter/difficulty, so a prune restricted to one encounter only has to
        rank that encounter's builds. Reading the whole population here (with every stored scenario)
        was the most expensive thing the coordinator did while the population was saturated.
        """
        if encounter_id in self._elite_cache:
            return set(self._elite_cache[encounter_id])
        sql = (
            'SELECT c.id,c.label,c.source,m.encounter,m.defeat,d.value,v.value '
            'FROM candidate c JOIN candidate_meta m ON m.id=c.id '
            "LEFT JOIN meta d ON d.key='aggregate:'||c.id||':discovery' "
            "LEFT JOIN meta v ON v.key='aggregate:'||c.id||':validation'")
        if encounter_id is not None:
            sql += ' WHERE m.encounter=?'
        records = []
        for cid, label, source, encounter, defeat, discovery, validation in self.db.execute(
                sql, (() if encounter_id is None else (int(encounter_id),))):
            records.append(lane_record(dict(id=cid, label=label, source=source),
                merge_aggregates(json.loads(discovery) if discovery else None,
                                 json.loads(validation) if validation else None),
                encounter=encounter, defeat=defeat))
        leaders = {}
        for groups in elite_lanes(records, pool=LANE_POOL).values():
            for members in groups.values():
                for candidate in members:
                    leaders[candidate] = True
        self._elite_cache[encounter_id] = set(leaders)
        return set(leaders)

    def rows(self, cid, phase=None):
        sql = 'SELECT result FROM run WHERE candidate=?'
        args = [cid]
        if phase:
            sql += ' AND phase=?'
            args.append(phase)
        sql += ' ORDER BY phase,ordinal'
        return [json.loads(r[0]) for r in self.db.execute(sql, args)]

    def run_revision(self, cid):
        """Session-local revision of a candidate's committed run rows."""
        return self._run_revisions.get(cid, 0)

    def milestone_revision(self, cid):
        """Session-local revision that moves only when the candidate crosses an evidence rung.

        `run_revision` moves on every accepted battle, which makes it the wrong key for anything that
        caches an *analysis*: the analysis is unchanged while a bank fills, and re-running it each time a
        seed lands put ~0.6 s of portfolio comparison on the coordinator's worker-feeding path. This
        counter moves when a bank completes, when a candidate crosses a rung, and when a build is added
        or pruned - the events that can actually change a comparison.
        """
        return getattr(self, '_milestone_revisions', {}).get(cid, 0)

    def next_ordinal(self, cid, phase):
        """The next unrecorded ordinal - from the batch while results are buffered, else the table."""
        key = (cid, phase)
        if key not in self._next:
            row = self.db.execute('SELECT MAX(ordinal) FROM run WHERE candidate=? AND phase=?',
                                  (cid, phase)).fetchone()
            self._next[key] = 0 if row[0] is None else row[0]+1
        return self._next[key]

    def record(self, cid, phase, ordinal, result):
        """Accept one finished result.

        The ordering guard and the duplicate check happen *here*, when the result is accepted, not
        when it is written: with batching enabled the row is not in the table yet, so the guard reads
        the store's own next-ordinal map and the batch. Acceptance is the cheap part; the durable
        bookkeeping is `_apply`, and the coordinator folds up to `batch_size` results into one
        transaction and one `synchronous=FULL` commit (see `flush`).
        """
        key = (cid, phase)
        buffered = (key, ordinal) in self._batch_keys
        expected = self.next_ordinal(cid, phase)
        if not buffered:
            if ordinal < expected:
                if self.db.execute('SELECT 1 FROM run WHERE candidate=? AND phase=? AND ordinal=?',
                                   (cid, phase, ordinal)).fetchone():
                    return
            # The rolling replay window evicts ordinals the compact evidence table keeps, so a
            # repeat of an evicted run is recognised here and dropped rather than counted twice
            # (and an ordinal that was accepted while its run row is no longer derivable is
            # dropped too). An ordinal that was never accepted still falls through to the guard.
            if ordinal <= expected and strategy_evidence.has(self.db, cid, phase, ordinal):
                return
            if ordinal != expected:
                raise ValueError('Out-of-order completion must be retried after its preceding seed.')
        else:
            return
        self._batch.append(dict(cid=cid, phase=phase, ordinal=ordinal, result=result))
        self._batch_keys.add((key, ordinal))
        self._next[key] = ordinal+1
        if self._batch_started is None:
            self._batch_started = time.monotonic()
        if len(self._batch) >= self.batch_size:
            self.flush()

    def pending_seconds(self):
        """How long the oldest accepted-but-unwritten result has been waiting (None when empty)."""
        return None if self._batch_started is None else time.monotonic()-self._batch_started

    @property
    def pending_records(self):
        """Accepted results waiting for the next flush."""
        return len(self._batch)

    def flush(self):
        """Write every accepted result in one transaction, then commit once.

        The whole record bookkeeping moves together: the run rows, the run/timing counters, each
        candidate's aggregate, the encounter/difficulty lifetime record and the archive/lane effects,
        so the run and its scheduling cursor still commit together. `synchronous=FULL` is unchanged -
        batching means one durability sync per batch instead of one per result.

        A failed flush is never allowed to lose work: the batch is put back and the caller sees the
        error (the coordinator pauses and retries the same seeds on resume).
        """
        if not self._batch:
            return 0
        batch, self._batch = self._batch, []
        self._batch_keys.clear()
        self._batch_started = None
        try:
            wal = Path(str(self.path)+'-wal')
            before = wal.stat().st_size if wal.exists() else 0
            # One accumulator per flush. Every derived counter, aggregate and lifetime record is read
            # from `meta` once, updated in memory for each result in the batch, and written back once -
            # which is where most of the per-record cost was (the same keys were re-read and re-encoded
            # for every result). The transaction and its single FULL commit are unchanged.
            acc = dict(totalRuns=0, simulationSeconds=self.get('simulationSeconds', 0.), timedRuns=0,
                       base_total=self.get('totalRuns', 0),
                       aggregates={}, seen=set(), changed_candidates=set(), lifetime=None, holders=None,
                       improvements=0, last_run=None, milestone_bumps=set(), milestones={})
            _apply_started = time.monotonic()
            for entry in batch:
                self._apply(entry, acc)
            self._write_accumulated(acc)
            self._flush_apply_seconds += time.monotonic()-_apply_started
            _commit_started = time.monotonic()
            self.db.commit()
            self._flush_blocked = False
            for cid in acc['changed_candidates']:
                self._run_revisions[cid] = self._run_revisions.get(cid, 0)+1
            # A rung crossing is the only run-driven event an analysis cache should react to.
            for cid in acc.get('milestone_bumps', ()):
                self._milestone_revisions[cid] = self._milestone_revisions.get(cid, 0)+1
            self._elite_cache.clear()
            self._flush_commit_seconds += time.monotonic()-_commit_started
            after = wal.stat().st_size if wal.exists() else 0
            self._wal_high = max(self._wal_high, after)
            if after < before:
                # SQLite checkpointed the WAL inside this commit: the write-back to the main database
                # is the expensive part, and it is what the flush timing has to attribute.
                self._checkpoints += 1
                self._checkpoint_seconds += time.monotonic()-_commit_started
        except Exception as exc:
            self.db.rollback()
            self._selection.clear()
            self._elite_cache.clear()
            self._batch = batch+self._batch
            self._batch_keys = {((entry['cid'], entry['phase']), entry['ordinal'])
                                for entry in self._batch}
            self._batch_started = time.monotonic()
            # Restore ordinals from every retained accepted record. A failed transaction may have
            # auto-rolled back in SQLite; treating these as unaccepted would admit the same seed twice.
            self._next.clear()
            for entry in self._batch:
                key = (entry['cid'], entry['phase'])
                self._next[key] = max(self._next.get(key, 0), entry['ordinal']+1)
            if _is_sqlite_full(exc):
                self._flush_blocked = True
                detail = storage_full_message(self.path, self.db, MAX_DB_MB, exc)
                detail += (f' This session retained {len(self._batch)} accepted record(s) in memory; '
                           'if restarting, SQLite replays their seeds from the last committed ordinal.')
                raise RuntimeError(detail) from exc
            raise
        return len(batch)

    def _write_accumulated(self, acc):
        """Write every key the batch touched, once each, inside the flush's transaction."""
        if acc['totalRuns']:
            self.set('totalRuns', acc['base_total']+acc['totalRuns'])
        if acc['timedRuns']:
            # Seeded with the committed value and added to per result, so the running total is the
            # exact same sequence of float additions the per-record path performed.
            self.set('simulationSeconds', acc['simulationSeconds'])
        if acc['timedRuns']:
            self.set('timedRuns', self.get('timedRuns', 0)+acc['timedRuns'])
        for key, value in acc['aggregates'].items():
            self.set(key, value)
        if acc['lifetime'] is not None:
            self.set('encounterLifetime', acc['lifetime'])
        if acc['holders'] is not None:
            self.set('recordHolders', acc['holders'])
        if acc['improvements']:
            self.set('improvements', self.get('improvements', 0)+acc['improvements'])
        if acc['last_run'] is not None:
            self.set('lastImprovementRun', acc['last_run'])

    def _apply(self, entry, acc):
        """One accepted result's durable bookkeeping (the body of the old `record`).

        Called inside `flush`'s transaction, in acceptance order, so a candidate's validation bank is
        still scored and archived in ascending ordinal order exactly as it was before batching.
        """
        cid, phase, ordinal, result = entry['cid'], entry['phase'], entry['ordinal'], entry['result']
        if strategy_evidence.has(self.db, cid, phase, ordinal):
            return
        # One transaction for the whole batch is opened by `flush`; this body writes into it.
        cursor = self.db.execute('INSERT OR IGNORE INTO run VALUES (?,?,?,?)',
                                 (cid, phase, ordinal, canonical(result)))
        if cursor.rowcount:
            acc['totalRuns'] += 1
            acc['changed_candidates'].add(cid)
            # The compact per-seed evidence row is written in this same transaction, before the
            # rolling retention below drops the replay example: it keeps only the fields the
            # chest/pairing readers consume, so a paired comparison survives the window.
            strategy_evidence.record(self.db, cid, phase, ordinal, result)
            if isinstance(result.get('elapsedSeconds'), (int, float)):
                acc['simulationSeconds'] += result['elapsedSeconds']
                acc['timedRuns'] += 1
            key = f'aggregate:{cid}:{phase}'
            if key not in acc['seen']:
                acc['seen'].add(key)
                acc['aggregates'][key] = self.get(key)
            acc['aggregates'][key] = accumulate(acc['aggregates'][key], result)
            # Did that land the candidate on a new evidence rung? Only then is a cached comparison's
            # input different in a way that matters.
            level = evidence_milestone(int((acc['aggregates'][key] or {}).get('n') or 0))
            phase_milestones = acc['milestones'].setdefault(cid, {})
            if phase_milestones.get(phase) != level:
                phase_milestones[phase] = level
                acc['milestone_bumps'].add(cid)
            # The encounter/difficulty lifetime record moves in the SAME transaction as the run
            # itself, so the counter, the totals and the two maxima cannot drift apart - and
            # pruning a candidate later can never lower any of them.
            self._record_lifetime(cid, phase, ordinal, result, acc)
        # Keep the shared selection bank plus the latest bounded validation sample.
        # The highest ordinal persists, so no seed is repeated when the window rolls.
        if phase == 'validation' and ordinal >= MAX_SAMPLES:
            self.db.execute('DELETE FROM run WHERE candidate=? AND phase=? AND ordinal>=? AND ordinal<=?',
                (cid, phase, VALIDATION_RUNS, ordinal-(MAX_SAMPLES-VALIDATION_RUNS)))
        if phase == 'validation' and ordinal >= VALIDATION_RUNS-1:
            # The archive decision is a function of the shared 64-run selection bank, which is the
            # first 64 validation ordinals - immutable once written (the rolling sample only ever
            # drops ordinals from VALIDATION_RUNS upward) - and of comparability, which moves with
            # the running totals. A partial bank cannot enter the archive, so skip all bank reads
            # until its 64th result. Thereafter it is scored once and reused; repeatedly parsing
            # every growing partial bank was quadratic work on the coordinator thread.
            total = acc['aggregates'].get(f'aggregate:{cid}:validation') \
                or self.get(f'aggregate:{cid}:validation')
            comparable = bool(total) and total['censored'] == 0 and total['count'] == total['n']
            cached = self._selection.get((OBJECTIVE_MODE, cid))
            if cached is None:
                bank = self.rows(cid, 'validation')
                rows = bank[:VALIDATION_RUNS]
                score = quality(summarize(rows))
                cell = None
                if score is not None:
                    stored = self.db.execute('SELECT scenario FROM candidate WHERE id=?',
                                             (cid,)).fetchone()
                    cell = encounter_family(json.loads(stored[0]), rows) if stored else None
                if len(bank) >= VALIDATION_RUNS:
                    self._selection[(OBJECTIVE_MODE, cid)] = (score, cell)
            else:
                score, cell = cached
            # Objective v2: the reliability threshold is gone. A build is not withdrawn from the
            # quality-diversity archive merely because recent seeds went badly - that is exactly
            # the rule that made a rare high-yield mechanism unreachable, and reliability now lives
            # in the lane tie-breaks and the final recommendation instead. Comparability stays: a
            # bank that has an unresolved run, or a run with no prize reading, cannot be compared
            # with a complete one, so the cell is released rather than held on incomplete evidence.
            if not comparable:
                self.db.execute('DELETE FROM archive WHERE candidate=?', (cid,))
                score = None
            if score is not None:
                old = self.db.execute('SELECT quality FROM archive WHERE cell=?', (cell,)).fetchone()
                if old is None or tuple(json.loads(old[0])) < score:
                    self.db.execute('INSERT OR REPLACE INTO archive VALUES (?,?,?)',
                                    (cell, cid, canonical(score)))
                    acc['improvements'] += 1
                    acc['last_run'] = acc['base_total']+acc['totalRuns']

    def archive(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM archive ORDER BY cell')]

    def lifetime(self):
        return self.get('encounterLifetime') or {}

    def holders(self):
        return self.get('recordHolders') or {}

    def _record_lifetime(self, cid, phase, ordinal, result, acc=None):
        """Fold one accepted run into its encounter/difficulty lifetime record (same transaction)."""
        # A candidate's encounter/difficulty key never changes, and this path runs once per accepted
        # run: reading back (and parsing) its stored scenario every time was pure per-run cost on the
        # coordinator thread. The cache is dropped when the candidate is pruned.
        key = self._encounter_keys.get(cid)
        if key is None:
            row = self.db.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
            if row is None:
                return
            key = encounter_key(json.loads(row[0]))
            self._encounter_keys[cid] = key

        def candidate_scenario():
            """The stored scenario, read only when a new record holder has to carry it for replay."""
            row = self.db.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
            return json.loads(row[0]) if row else None

        if acc is not None:
            if acc['lifetime'] is None:
                acc['lifetime'] = dict(self.get('encounterLifetime') or {})
            lifetime = acc['lifetime']
        else:
            lifetime = self.get('encounterLifetime') or {}
        entry = dict(blank_lifetime(), **(lifetime.get(key) or {}))
        entry['lifetimeAttempts'] += 1
        verdict = None if result.get('censored') else result.get('verdict')
        if verdict is None:
            entry['lifetimeNoVerdict'] += 1
        elif verdict == 1:
            entry['lifetimeWins'] += 1
        elif verdict == 2:
            entry['lifetimeLosses'] += 1
        if acc is not None:
            if acc['holders'] is None:
                acc['holders'] = dict(self.get('recordHolders') or {})
            holders = acc['holders']
        else:
            holders = self.get('recordHolders') or {}
        # One record holder per metric per encounter: a new record replaces the old one, so this
        # never grows into a history.
        if verdict == 2:
            value = potential_chests(result)
            if value is not None and (entry['highestPotentialChests'] is None
                                      or value > entry['highestPotentialChests']):
                entry['highestPotentialChests'] = value
                entry['potentialBasis'] = ('queued-at-verdict' if (result.get('rewardOutcome') or {})
                                           .get('pendingChests') is not None else 'pre-verdict-callbacks')
                holders[f'potential:{key}'] = record_holder(
                    candidate_scenario(), cid, phase, ordinal, result.get('seeds') or (0, 0),
                    verdict, value,
                    result.get('digest'))
        elif verdict == 1:
            value, basis = chest_count(result)
            if value is not None and (entry['highestChestsEarned'] is None
                                      or value > entry['highestChestsEarned']):
                entry['highestChestsEarned'] = value
                entry['earnedBasis'] = basis
                holders[f'earned:{key}'] = record_holder(
                    candidate_scenario(), cid, phase, ordinal, result.get('seeds') or (0, 0),
                    verdict, value,
                    result.get('digest'))
        lifetime[key] = entry
        if acc is None:
            self.set('encounterLifetime', lifetime)
            self.set('recordHolders', holders)

    def close(self):
        # Never drop accepted results on the way out: a failed flush is raised to the caller.
        # SQLITE_FULL was already reported and rolled back. Do not retry it during shutdown: the
        # retained results are replayed from the last committed ordinal when the library is reopened.
        if self._flush_blocked:
            self.db.rollback()
        else:
            self.flush()
        # A writer outside the batch path (a proposal pass that inserted a candidate and was stopped
        # before its own commit) can leave a transaction open, and SQLite refuses a TRUNCATE checkpoint
        # while one is pending - `sqlite3.OperationalError: database table is locked`. The checkpoint is
        # housekeeping, not correctness: commit first so it does what it is for, and if it still cannot
        # run, closing is more important than compacting.
        try:
            self.db.commit()
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.OperationalError:
            pass
        self.db.close()


def worker(scenario, pair):
    from strategy_optimizer_adapter import simulate
    started = time.perf_counter()
    result = simulate(scenario, pair, trace=False)
    result['elapsedSeconds'] = time.perf_counter()-started
    return result


def duty_ready_slot(pending, slot_ready_at, now):
    """Find an idle worker whose own duty cooldown has expired."""
    occupied = {task['slot'] for task in pending}
    return next((slot for slot, ready_at in enumerate(slot_ready_at)
                 if slot not in occupied and now >= ready_at), None)


def duty_hold_seconds(busy_seconds, duty):
    return max(0., float(busy_seconds))*(1-duty)/duty


class Optimizer:
    """UI facade. One coordinator owns SQLite; no unbounded work queue or executor queue."""
    #: Projection helpers (`StatusProjection`/`ProcessProjection`) are built with `__new__` and only
    #: the subset of state listed in `strategy_publication.INPUTS`; these class defaults keep the
    #: status render independent of the encounter runtime's presence. `_encounter_ledger is None`
    #: means "not yet checked", so a library whose ledger is created later (on activation) is
    #: re-checked rather than remembered as absent.
    _encounter = None
    _encounter_report = None
    _encounter_session_base = None
    _encounter_rate_samples = ()
    _encounter_ledger = None
    _encounter_preview_token = None
    #: The automatic Community campaign coordinator and its last published status. Built in `_loop`
    #: after the coordinator loads; before that `status()` publishes a read-only bootstrap view.
    _campaign = None
    _campaign_report = None
    #: True only after THIS process ran an explicit `community_start`; opening a library loads the
    #: campaign status but never resumes/activates, so a restart cannot stomp a manual study.
    _campaign_started = False
    _campaign_coordinators = None
    _community_evaluator = None
    _campaign_resize_pending = None
    _campaign_fair_cursor = 0
    def __init__(self, path):
        import queue
        from strategy_optimizer_adapter import provenance
        import strategy_optimizer_backend as acceleration
        self._startup_started_at = time.monotonic()
        self._startup_timing = dict(
            phase='constructing', phaseStartedAt=self._startup_started_at,
            ready=False, libraryLoadCount=0, phases={}, failurePhase=None, error=None)
        # libraryLoadCount is scoped to this Optimizer instance; a desktop library switch creates a
        # new instance and starts a new diagnostic record.
        self.path = Path(path)
        self.provenance = provenance()
        # The Community-first encounter runtime. It is created for real once the library is open
        # (`_loop`); until then `status()` reports a read-only bootstrap view of the persisted mode.
        self._encounter = None
        self._encounter_report = None
        self._campaign = None
        self._campaign_report = None
        self._campaign_started = False
        self._campaign_drain_status = None
        self._campaign_resize_pending = None
        self._worker_resize_status = dict(state='idle')
        self._campaign_coordinators = {}
        self._campaign_fair_cursor = 0
        self._community_evaluator = None
        # The revision actually loaded into this process, captured at import above.
        self.runtimeRevision = _LOADED_REVISION
        self.battleCompatibilityRevision = _BATTLE_COMPATIBILITY_REVISION
        # When the encounter mode is enabled these hold the exact in-memory student shares / track
        # config that activation replaced, so `encounter_rollback` restores them verbatim.
        self._encounter_previous_shares = None
        self._encounter_previous_tracks = None
        # The students' split of the pool. Scheduler-side only: it decides which authorised seeds are
        # handed to a worker, so it cannot reach a battle and cannot invalidate a library. Settings
        # changes replace this mapping at runtime (`set_student_shares`).
        self._student_shares = students.shares()
        # Per-pass counters for the student tracks, published so the UI can show what each student was
        # actually given rather than what it was configured to get.
        self._student_diagnostics = {}
        # What the last slow pass published its reports from: (totalRuns, share, retired count). The
        # reports are rebuilt when this changes or when a plan moves, never merely because a pass ran.
        self._report_revision = None
        # The focused portfolio's last computed comparisons and plan, with the milestone signature they
        # were computed from. Reused while nothing relevant has changed.
        self._portfolio_cache = None
        # The rebellion ledger, produced on request by `student_report` like `_student_runs`.
        self._student_ledger = None
        # Student 1's per-track report and the pair track's coverage, also produced on request.
        self._student_tracks = None
        self._pair_coverage = None
        # How Student 1's own share divides across its tracks. Scheduler-side only, like the student
        # split: it decides which authorised seeds are handed out, so it cannot reach a battle.
        self._track_shares = students.track_shares()
        # What the bulk workers will actually run: the canonical engine, or that engine plus the
        # accelerators pinned by a recorded parity report. Reported, never guessed.
        self.acceleration = acceleration.status()
        # The fights this library can contain: id -> title/boss/roster size, from the engine's data.
        self.encounters = encounter_catalogue()
        # The settings the running pool was started with, so a throughput reading can state how many
        # workers produced it instead of leaving the reader to remember.
        self._workers, self._duty = default_workers(), clamp_duty(None)
        # (monotonic, totalRuns) samples of *completed* battles, used to report one measured
        # aggregate rate instead of leaving the UI to guess from two status polls.
        self._rate_samples = []
        # Recent per-battle engine seconds, so the capacity reading describes the work actually being
        # run rather than the library's whole history. A mature library's cumulative mean is dominated
        # by older, faster fights, and dividing today's throughput by it understates the pool's real
        # occupancy by a large factor (measured: 262/s "capacity" against 88-94/s achieved, while the
        # current work mix - including the long healing tail - only permits ~96/s).
        self._engine_samples = []
        # True worker occupancy from task lifetime: a worker is active from the moment a task is
        # submitted to the pool until its result is harvested. The rolling window is time-weighted, so
        # a 10 s average is the honest "how many of the configured workers were actually running a
        # battle", independent of battles/s and of any capacity arithmetic.
        self._active_window = []
        self._idle_coordinator_seconds = 0.
        self._idle_no_work_seconds = 0.
        self._pass_had_ready = False
        self._pass_coordinator_seconds = 0.
        # Published candidate records, keyed by candidate id -> (change signature, payload). A publish
        # only re-derives the candidates whose signature moved; see `_published_candidates`.
        self._candidate_cache = {}
        # Which encounter ids this library actually holds a build for, refreshed by each publish from
        # the payloads it just assembled (no extra parsing). The desktop uses it to offer the
        # expansion action *only* when fights are genuinely missing, so opening a library never
        # mutates it and an already-complete library is never offered the button.
        self._library_encounters = []
        # The exact encounter/difficulty every *new* attempt is focused on, or None for the normal
        # distribution. It is a property of the open library's session only: it is never written to
        # the library and a new library gets a new Optimizer, so a focus can never carry a stale
        # encounter id onto a different library. Changing it only changes future scheduling.
        self._focus_encounter = None
        # The last fine-tuning program this session created, for the one-shot acknowledgement the
        # desktop probe/fine-tune control reads back; the live programs themselves live in the store.
        self._last_finetune = None
        self._finetune_cache = None
        # The automatic-tuning diagnostic for the current focus: the selected parent, the axes it
        # started, and why it is waiting or has stopped. Kept on the session (like the focus itself)
        # and published with the status snapshot; the programs it starts live in the store.
        self._auto_tune_state = None
        # Scheduler health, accumulated per session so the desktop can show *why* workers are idle
        # instead of only how fast they are going. Timings are coordinator-thread seconds.
        self._scheduler = self._blank_scheduler()
        # Incremental planner state. These live for the life of the search instead of being rebuilt
        # inside the per-pass planning block: the ready list is maintained as work is authorised, the
        # evidence view keeps its real time-to-live, and the next-ordinal map is read from the table
        # once rather than re-aggregated every pass.
        self._planner_order = []
        self._planner_limits = {}
        # The builds currently holding first place at something, refreshed with the evidence. Published
        # so the surface can show that the incumbent rung is actually firing.
        self._planner_rank_one = set()
        # The focused exploitation lane's current decision per candidate (target, current, tier,
        # granted, reason). Written by the planner's evidence build and published with the snapshot so
        # the overview can show the bounded target, how far the bank has come, and why it stopped.
        self._planner_exploit = {}
        # The focused record repair lane's report for the current focus (the Highest Earned
        # holder's identity, its repeat target, or why it was pruned / not yet measurable).
        self._planner_record = None
        # The focused four-objective portfolio's plan for the current focus, the validation bank each
        # of its grants authorises (so the view never understates the work it asked for), and the
        # cached paired tune comparisons the graduation ledger is built from.
        self._planner_portfolio = None
        self._planner_portfolio_banks = {}
        self._planner_portfolio_tune = None
        self._planner_evidence = dict(at=0., value=None)
        self._planner_scenarios = {}
        self._planner_ordinals = None
        self._planner_dirty = True
        # The shared asynchronous planning host. The Optimizer owns exactly ONE planner pool (shared
        # by every encounter coordinator) and ONE battle evaluator; coordinators never construct or
        # close a pool. `_planner_routes` maps a stable request id to its owning coordinator and the
        # campaign/session/generation/focus identity it was submitted under, so a harvested result is
        # routed exactly once and a result whose owner was replaced (rolled over) is dropped rather
        # than admitted into a stale generation.
        self._planning_pool = None
        self._planner_routes = {}
        self._planning_sequence = 0
        self._planner_submitted = 0
        self._planner_harvested = 0
        self._planner_accepted = 0
        self._planner_rejected = 0
        self._planner_cancelled = 0
        self._planner_unrouted = 0
        self._planner_latency = deque(maxlen=64)
        self._planner_last_error = None
        self._planner_close_report = None
        # Phase state machine for the shared planner. One logical route is one proposal group; it
        # moves prepare -> compute -> finalize and is removed only when exactly one result is
        # delivered to its coordinator. The fan-out of a group's prepared window into chunk tasks
        # is streamed (never the whole window at once) so a single focused encounter can occupy
        # every planner lane while several encounters share the same lanes fairly.
        self._planner_chunk_tasks = {}
        self._planner_task_phase = {}
        self._planner_group_cursor = 0
        self._planner_fanout_submitted = 0
        self._planner_chunks_total = 0
        self._planner_chunks_completed = 0
        self._planner_phase_counts = {}
        # Cache of the persisted multi-select campaign focus, refreshed whenever the status publishes
        # or the focus command runs, so `planning_context` never pays a SQLite read per planning call.
        self._planning_focus = []
        self.commands = queue.Queue(maxsize=16)
        self.lock = threading.Lock()
        self.snapshot = {'state': 'Opening library', 'candidates': [], 'archive': []}
        # The desktop's HTTP status reader serves the latest projection as immutable JSON bytes.
        # The full view is encoded by the projection process while Running; synchronous states are
        # encoded once on their first read. State-only replacements retain the old base bytes and
        # are represented by the small live overlay.
        self._snapshot_wire_json = None
        self._snapshot_wire_lock = threading.Lock()
        self._status_transport_bootstrap_lock = threading.Lock()
        self._status_transport_bootstrap_ready = False
        self._status_transport_encounter_bootstrap = None
        self._status_transport_campaign_bootstrap = None
        self.shutdown = threading.Event()
        # Active-session accounting. `_session_active` is the accumulated *running* time and
        # `_session_since` the monotonic stamp of the stretch that is running now, so a paused
        # stretch never accumulates and a status poll can never drift or reset the reading: it is
        # derived from the coordinator's own clock, never from a browser timer.
        self._session_active = 0.
        self._session_since = None
        # A session is "live" from the Start that began it until Stop; the next Start resets.
        self._session_live = False
        # `totalRuns` at the start of the live session, so the desktop can show the session's own
        # run count beside the library-wide one. `None` means no session has started yet.
        self._session_runs_base = None
        # Encounter (ea_*) session accounting. Encounter results are deliberately NOT written into
        # the legacy `totalRuns` counter (historical separation), which would otherwise leave the
        # published session count and throughput frozen. These derive the same public numbers from
        # the persisted ea_* ledger instead: the session's charged run count (each unique sample is
        # charged once, so a coalesced copy is never counted twice) plus the evaluator's own timing.
        self._encounter_session_base = None
        self._encounter_rate_samples = []
        self._encounter_last_measured_rate = None
        # `None` = the ea_sample table has not been looked for yet; False/True is the cached answer.
        self._encounter_ledger = None
        #: The most recent pure preview the backend produced, so activation can be bound to it.
        self._encounter_preview_token = None
        self._work_started = None
        self._publisher = None
        self._publication_epoch = 0
        self._publication_key = None
        self._publication_failure = None
        self._startup_advance('open_library')
        self.thread = threading.Thread(target=self._loop, daemon=False)
        self.thread.start()

    def _startup_advance(self, phase):
        """Finish the previous open phase and begin another using cached in-memory timings."""
        now = time.monotonic()
        timing = self._startup_timing
        previous = timing.get('phase')
        phases = dict(timing.get('phases') or {})
        if previous and previous not in ('ready', 'failed'):
            phases[previous] = round(max(0.0, now-float(timing.get('phaseStartedAt', now))), 6)
        timing.update(phase=str(phase), phaseStartedAt=now, phases=phases)

    def _startup_mark_ready(self):
        now = time.monotonic()
        self._startup_advance('ready')
        self._startup_timing.update(ready=True, elapsedSeconds=round(
            max(0.0, now-self._startup_started_at), 6), error=None, failurePhase=None)

    def _startup_mark_failed(self, error):
        now = time.monotonic()
        timing = self._startup_timing
        if timing.get('ready'):
            # An exception after open completed is a runtime failure, not a failed library load.
            return
        failed_phase = timing.get('phase')
        phases = dict(timing.get('phases') or {})
        if failed_phase and failed_phase not in ('ready', 'failed'):
            phases[failed_phase] = round(
                max(0.0, now-float(timing.get('phaseStartedAt', now))), 6)
        timing.update(phase='failed', ready=False, failurePhase=failed_phase, phases=phases,
                      elapsedSeconds=round(max(0.0, now-self._startup_started_at), 6),
                      error=str(error))

    def _startup_view(self):
        """Return a detached, JSON-safe view; this reads no library metadata."""
        timing = getattr(self, '_startup_timing', None)
        if timing is None:
            # Projection workers and fixture-only owners are allocated without running __init__.
            return dict(phase='unknown', ready=False, elapsedSeconds=None,
                        libraryLoadCount=0, phases={}, failurePhase=None, error=None)
        now = time.monotonic()
        elapsed = timing.get('elapsedSeconds')
        if elapsed is None:
            elapsed = max(0.0, now-self._startup_started_at)
        phases = dict(timing.get('phases') or {})
        phase = timing.get('phase')
        if phase and phase not in ('ready', 'failed'):
            phases[phase] = round(max(0.0, now-float(timing.get('phaseStartedAt', now))), 6)
        return dict(phase=phase, ready=bool(timing.get('ready')),
                    elapsedSeconds=round(float(elapsed), 6),
                    libraryLoadCount=int(timing.get('libraryLoadCount', 0)),
                    phases=phases, failurePhase=timing.get('failurePhase'),
                    error=timing.get('error'))

    def command(self, action, value=None, wait=False):
        acknowledged = threading.Event()
        self.commands.put_nowait((action, value, acknowledged))
        if wait and not acknowledged.wait(10):
            raise TimeoutError('The command is still pending; wait for the status to update.')

    def status(self):
        with self.lock:
            # The snapshot contains only built-in, JSON-compatible values. Pickle makes an
            # independent copy in about half the time of recursive deepcopy on a mature library;
            # the bytes never leave this process or come from an external source.
            import pickle
            # Published snapshots are detached from the renderer and replaced atomically.
            # Copying a large view must not hold the coordinator's publication lock.
            snapshot = self.snapshot
            clock_values = (self._active_session_seconds(),
                            time.monotonic()-self._work_started if self._work_started else None)
        result = pickle.loads(pickle.dumps(snapshot, protocol=5))
        result.update(self._status_overlay(snapshot, allow_bootstrap=True,
                                           clock_values=clock_values))
        return result

    def prepare_status_transport(self):
        """Cache the read-only bootstrap reports before the HTTP poll path is enabled.

        The steady status GET must never open SQLite. The legacy API keeps its existing bootstrap
        behavior, while this one-time preparation lets the transport path serve the same fields from
        cached read-only reports until the live projection publishes them.
        """
        with self._status_transport_bootstrap_lock:
            if self._status_transport_bootstrap_ready:
                return
            with self.lock:
                needs_encounter = not self._encounter_report
                needs_campaign = self._campaign_report is None and not self.snapshot.get('campaign')
                encounter_ids = sorted((int(key) for key in (self.encounters or {})), key=int)
            encounter = (encounter_search.bootstrap_report(self.path) if needs_encounter else None)
            campaign = (community_campaign.bootstrap_status(
                self.path, encounter_ids=encounter_ids) if needs_campaign else None)
            with self.lock:
                self._status_transport_encounter_bootstrap = encounter
                self._status_transport_campaign_bootstrap = campaign
                self._status_transport_bootstrap_ready = True

    @staticmethod
    def _encode_status_wire(snapshot):
        return json.dumps(snapshot, separators=(',', ':'), ensure_ascii=False,
                          allow_nan=False).encode('utf-8')

    def status_transport_data(self):
        """Return cached base JSON plus the complete cheap live overlay for the local desktop GET."""
        if not self._status_transport_bootstrap_ready:
            raise RuntimeError('Status transport descriptor has not been prepared')
        with self._snapshot_wire_lock:
            with self.lock:
                snapshot = self.snapshot
                wire_json = self._snapshot_wire_json
                clock_values = (self._active_session_seconds(),
                                time.monotonic()-self._work_started
                                if self._work_started else None)
                if wire_json is None:
                    # This is a one-time path for bootstrap and synchronous Paused/Error states.
                    # Serializing under the publication lock keeps the bytes paired with precisely
                    # the same snapshot; normal Running publications arrive with worker-encoded bytes.
                    wire_json = self._encode_status_wire(snapshot)
                    self._snapshot_wire_json = wire_json
            live = self._status_overlay(snapshot, allow_bootstrap=False,
                                        include_snapshot_metadata=True,
                                        clock_values=clock_values)
        return wire_json, live

    def _status_overlay(self, snapshot, *, allow_bootstrap, include_snapshot_metadata=False,
                        clock_values=None):
        """Build status fields that are live outside the published base, without mutating it."""
        if clock_values is None:
            clock_values = (self._active_session_seconds(),
                            time.monotonic()-self._work_started if self._work_started else None)
        overlay = dict(sessionElapsedSeconds=clock_values[0],
                       currentRunElapsedSeconds=clock_values[1])
        overlay['startupDiagnostics'] = self._startup_view()
        # State-only error/close updates can retain a previously encoded Running base. Mask its
        # rolling rate here too, so both status APIs always describe the current state.
        if isinstance(snapshot.get('throughput'), dict):
            overlay['throughput'] = self._encounter_rate_status(
                snapshot['throughput'], snapshot.get('state'))
        # The students' split, published with every snapshot because it is cheap and it is what explains
        # where the attempts went. The run counts are kept from the last explicit `student_report`, so the
        # two-second publish path never pays for an evidence read.
        overlay['students'] = dict(shares=dict(self._student_shares),
                                   warning=getattr(self, '_student_warning', None),
                                   report=getattr(self, '_student_runs', None),
                                   ledger=getattr(self, '_student_ledger', None),
                                   tiers=[dict(name=tier.name, rules=list(tier.rules),
                                               minimum=tier.minimum, maximum=tier.maximum)
                                          for tier in students.tiers()],
                                   tracks=[dict(name=track.name, kind=track.kind,
                                                title=track.title, stats=list(track.stats),
                                                share=self._track_shares.get(track.name, 0.0))
                                           for track in students.tracks()],
                                   trackReport=getattr(self, '_student_tracks', None),
                                   pairCoverage=getattr(self, '_pair_coverage', None),
                                   names=dict(community=students.STUDENT_COMMUNITY,
                                              rebel=students.STUDENT_REBEL,
                                              stumble=students.STUDENT_STUMBLING,
                                              # Student 9: the mean-earned student. Published with the
                                              # rest because it is a share stream like the others, and
                                              # the surface that offers the controls reads this list.
                                              average=students.STUDENT_AVERAGE,
                                              # Breakthrough / reliability: the mechanism-guided
                                              # stream, a share like any other and 0% by default.
                                              mechanism=students.STUDENT_MECHANISM,
                                              discovery=students.STREAM_DISCOVERY),
                                   floor=students.DISCOVERY_FLOOR)
        # Student 9's own metric and its last decision, and the breakthrough stream's state. All three
        # are stashed by the slow analysis pass rather than read here: this block is published every
        # couple of seconds, and each of them reads the evidence table.
        overlay['students']['average'] = getattr(self, '_average_report', None)
        overlay['students']['averagePlan'] = getattr(self, '_average_plan', None)
        overlay['breakthrough'] = getattr(self, '_breakthrough_status', None)
        # The MP-recovery operation: the configured allowance and the requests it has created. The
        # surface uses it to say "low MP detected; herb-supported retry awaits item allowance" rather
        # than appearing to work while creating no trials.
        overlay['mpRecovery'] = getattr(self, '_mp_recovery_status', None)
        # The incumbent rung: how many builds currently hold first place at something, and the bank
        # each is given. Published so the surface can show the bombardment is firing rather than
        # leaving it to be inferred from a run count.
        banks = getattr(self, '_planner_rank_one', None) or {}
        overlay['rankOne'] = dict(
            candidates=len(banks),
            first=sum(1 for bank in banks.values() if bank >= RANK_ONE_RUNS),
            topEarned=sum(1 for bank in banks.values() if bank == TOP_EARNED_RUNS),
            # "rank review" replaces the old `unlimited`: the count of builds holding first place on
            # Best Earned or Mean Earned, whose finite review bank is now the largest rung. The name
            # changed with the policy so a UI reading `unlimited` cannot keep reporting an entitlement
            # that no longer exists.
            rankReview=sum(1 for bank in banks.values() if bank >= RANK_REVIEW_RUNS),
            bank=RANK_ONE_RUNS, topEarnedBank=TOP_EARNED_RUNS,
            rankReviewBank=RANK_REVIEW_RUNS,
            ordinary=learner.EXTENDED_DISCOVERY_RUNS + learner.VALIDATION_RUNS)
        # The Community-first runtime's read-only report: enabled/mode/config, the frozen question,
        # budgets, portfolio, boundaries, progress and the loaded revision. Cheap enough to publish
        # with every snapshot; before the loop opens the library it is a read-only bootstrap view.
        encounter_report = self._encounter_report
        if not encounter_report:
            encounter_report = (encounter_search.bootstrap_report(self.path) if allow_bootstrap
                                else self._status_transport_encounter_bootstrap)
        overlay['encounterAware'] = deepcopy(encounter_report or {})
        # The automatic campaign status and the multi-select focus. The loop publishes both with each
        # snapshot; before it has built the campaign (or before the first publish) fall back to the
        # read-only bootstrap so `status()` never omits them.
        if not snapshot.get('campaign'):
            if self._campaign_report is not None:
                overlay['campaign'] = deepcopy(self._campaign_report)
            elif allow_bootstrap:
                catalog = sorted((int(key) for key in (self.encounters or {})), key=int)
                overlay['campaign'] = community_campaign.bootstrap_status(
                    self.path, encounter_ids=catalog)
            else:
                overlay['campaign'] = deepcopy(self._status_transport_campaign_bootstrap or {})
        if 'focusEncounters' not in snapshot:
            overlay['focusEncounters'] = []
        # The shared asynchronous planning host's own accounting (pool liveness, the explicit
        # requested/effective battle/planner split, and planner routing counters). Observer-only;
        # never a selection input. Computed on the live Optimizer, not on the status projection.
        overlay['planningHost'] = self._planning_status()
        overlay['workerAllocation'] = overlay['planningHost']['workerAllocation']
        if include_snapshot_metadata:
            # State-only replacements can advance these keys while retaining the last encoded base.
            # Always overlay their current authoritative values so the merged response equals status().
            for name in ('state', 'error', 'focusEncounter', 'publicationPending',
                         'publicationError', 'publicationSeconds', 'publishedAt'):
                if name in snapshot:
                    overlay[name] = snapshot[name]
        return overlay

    def _active_session_seconds(self):
        """Elapsed active search time for the live session; paused stretches never count."""
        total = self._session_active
        if self._session_since is not None:
            total += time.monotonic()-self._session_since
        return total

    @staticmethod
    def _blank_scheduler():
        return dict(workers=0, busy=0, idle=0, pending=0, buffered=0, duplicates=0,
                    rejectedProposals=0, duplicateProposals=0, lastRejection=None,
                    workerAllocation=None,
                    runnableDiscovery=0, runnableValidation=0, plan=None,
                    idleWorkerSeconds=0., planSeconds=0., proposalSeconds=0., recordSeconds=0.,
                    publishSeconds=0., lastPass=0., evidenceSeconds=0., readySeconds=0.,
                    planSkips=0, heldByDuty=0, ranOutOfReadySeeds=0,
                    # Anti-starvation pressure control: how short of useful work the pool has been
                    # over the sustained window, and what was created because of it.
                    starvationActive=False, starvationLevel=0, starvationUtilisation=None,
                    reseedCreated=0, reseedSeconds=0., reseedBySource={}, reseedByEncounter={},
                    reseedCreatedByEncounter={}, reseedBlocked=0, reseedSkipped=0,
                    coordinatorFraction=None, starvationVetoed=0,
                    reseedIntervalSeconds=learner.RESEED_INTERVAL_SECONDS,
                    reservoirSeeds=0, reservoirTarget=0, reservoirFill=0, reservoirFull=False,
                    openCandidates=0, emptyPlans=0,
                    # Why a Running search with free workers had nothing to dispatch, or None. Set
                    # only from the focused scope (see `focused_stall_reason`).
                    idleReason=None, focusedReplenish=0,
                    # Direct worker activity (task-lifetime based) and the idle split.
                    activeWorkers=0, activeWorkersAverage10s=None, workerOccupancy=None,
                    occupancyWindowSeconds=None, idleCoordinatorSeconds=0., idleNoWorkSeconds=0.,
                    coordinatorBusySeconds=0.,
                    # Record batching: accepted-but-unwritten results, and what the flushes cost.
                    flushSeconds=0., flushRecords=0, pendingRecords=0,
                    # Incremental planner: how often the ready list had to be rebuilt from scratch.
                    orderRebuilds=0, orderAppends=0)

    def _resume_session_clock(self):
        """Start (or restart after a pause) the running stretch. A fresh session resets first."""
        if self._session_since is None:
            self._session_since = time.monotonic()

    def worker_occupancy(self, workers):
        """Direct worker activity from task lifetimes: active now, 10 s average, and occupancy.

        A worker counts as active from the moment its task is submitted to the pool until that task's
        result is harvested - the only definition that does not depend on battles/s, on the capacity
        arithmetic, or on how long a pass happened to take. The average is time-weighted over the
        window, so sampling jitter cannot skew it.
        """
        window = self._active_window
        active_now = window[-1][1] if window else 0
        average = None
        if len(window) > 1:
            span = window[-1][0]-window[0][0]
            if span > 0:
                busy = sum(window[index][1]*(window[index+1][0]-window[index][0])
                           for index in range(len(window)-1))
                average = busy/span
        return dict(activeWorkers=active_now, activeWorkersAverage10s=average,
                    workerOccupancy=(average/workers if (average is not None and workers) else None),
                    occupancyWindowSeconds=(window[-1][0]-window[0][0]) if len(window) > 1 else None)

    def flush_records(self, store, pending):
        """Write the record batch when it is due, after dispatch rather than before it.

        A worker is handed its next battle first; only then does the coordinator spend time on the
        database. The batch is drained when it is full, when it is older than its bound, or when the
        pool has nothing outstanding - so no accepted result waits longer than the bound, and a
        Paused/Stopped library is fully written before its state is published.
        """
        if not store.pending_records:
            return 0
        age = store.pending_seconds()
        if (age is None or (age < store.batch_seconds and pending
                            and store.pending_records < store.batch_size)):
            return 0
        # The flush is coordinator time, not worker-idle time: restart the idle window here so the
        # next pass does not charge the flush's duration against the pool it just fed.
        self._scheduler['lastPass'] = time.monotonic()
        _started = time.monotonic()
        written = store.flush()
        self._scheduler['flushSeconds'] += time.monotonic()-_started
        self._scheduler['flushRecords'] = self._scheduler.get('flushRecords', 0)+written
        # Where that time went: the batch's own statements, the commit (fsync), and how much of the
        # commit was SQLite writing the WAL back into the main database.
        self._scheduler['flushApplySeconds'] = round(store._flush_apply_seconds, 3)
        self._scheduler['flushCommitSeconds'] = round(store._flush_commit_seconds, 3)
        self._scheduler['flushCheckpoints'] = store._checkpoints
        self._scheduler['flushCheckpointSeconds'] = round(store._checkpoint_seconds, 3)
        self._scheduler['walHighBytes'] = store._wal_high
        return written

    def _forget_planner_state(self, candidate):
        """Drop a pruned candidate from every planner cache, in the instant it is pruned.

        The ready list, the staged limits and the scenario cache are the coordinator's own copies of
        state that lives in the store; reacting to the prune callback is what keeps a removed build
        from being handed out one more time before the next evidence refresh notices.
        """
        self._planner_scenarios.pop(candidate, None)
        self._planner_limits.pop(candidate, None)
        if self._planner_order:
            self._planner_order = [seed for seed in self._planner_order if seed[0] != candidate]

    def _schedule_completion(self, cid, phase, ordinal):
        """Rebuild only when a completed bank may authorise another stage.

        ``ready()`` owns queue construction and refills its bounded window when it empties.
        Appending a next ordinal here can duplicate one already queued or reserved by a worker.
        """
        dlimit, vlimit = self._planner_limits.get(cid, (0, 0))
        if phase == 'discovery':
            nxt, limit = ordinal+1, dlimit
        elif phase == 'validation':
            nxt, limit = ordinal+1, vlimit
        else:
            return
        if nxt >= limit and (phase == 'discovery' or limit < learner.VALIDATION_RUNS):
            # A bank just finished, so the staged evidence policy may authorise the next one. That is
            # the only case that needs the full computation - a finished 64-run validation bank is the
            # end of the line, and marking it dirty would rebuild the population's list on every
            # completion, which is exactly the cost this change removes.
            self._planner_dirty = True

    def _hold_session_clock(self, ended):
        """Stop the running stretch and accumulate it. `ended` also closes the session, so the next
        Start begins a new one rather than resuming this one."""
        if self._session_since is not None:
            self._session_active += time.monotonic()-self._session_since
            self._session_since = None
        if ended:
            self._session_live = False

    def _candidate_payload(self, store, row, aggregates, baselines, baseline_digest, equivalence):
        """One candidate's published record - the only work a publish redoes for it."""
        discovery = store.rows(row['id'], 'discovery')
        validation = store.rows(row['id'], 'validation')
        scenario = json.loads(row['scenario'])
        representative, equivalence_counts = equivalence
        combined = discovery + validation
        # Per-candidate records, so a family card can show its own best numbers without the page
        # re-deriving them from a sample of runs.
        potential = [value for value in (potential_chests(item) for item in combined
                                         if not item.get('censored') and item.get('verdict') == 2)
                     if value is not None]
        earned = [value for value in (chest_count(item)[0] for item in combined
                                      if not item.get('censored') and item.get('verdict') == 1)
                  if value is not None]
        payload = dict(id=row['id'], label=row['label'], source=row['source'],
            scenario=scenario, stats=json.loads(row['stats']),
            changes=scenario_changes(baselines.get(scenario['encounterId']), scenario),
            equivalentTo=representative.get(row['id'], row['id']),
            equivalenceCount=equivalence_counts.get(row['id'], 1),
            bestPotentialChests=max(potential) if potential else None,
            bestChestsEarned=max(earned) if earned else None,
            discovery=aggregate_summary(
                _parsed_meta(aggregates.get(f'aggregate:{row["id"]}:discovery')), discovery),
            validation=aggregate_summary(
                _parsed_meta(aggregates.get(f'aggregate:{row["id"]}:validation')), validation),
            selection=summarize(validation[:VALIDATION_RUNS]),
            validationRuns=store.next_ordinal(row['id'], 'validation'),
            family=encounter_family(scenario, validation[:VALIDATION_RUNS] or discovery) if (validation or discovery) else None,
            encounterId=scenario['encounterId'],
            evidence=lane_record(dict(id=row['id'], label=row['label'], source=row['source'],
                                      scenario=row['scenario']),
                                 merge_aggregates(
                                     _parsed_meta(aggregates.get(f'aggregate:{row["id"]}:discovery')),
                                     _parsed_meta(aggregates.get(f'aggregate:{row["id"]}:validation'))),
                                 encounter=scenario['encounterId'],
                                 defeat=scenario.get('defeatCount') or 0),
            examples=interesting(validation or discovery))
        return payload, (row['label'], row['source'], row['stats'], row['scenario'],
                         aggregates.get(f'aggregate:{row["id"]}:discovery'),
                         aggregates.get(f'aggregate:{row["id"]}:validation'), baseline_digest)

    def _published_candidates(self, store):
        """The candidate list, rebuilt only for the candidates whose inputs actually changed.

        A published candidate record is a function of exactly three things: its own row (label,
        source, stats, scenario), the two per-phase counters written with every accepted run, and the
        runs themselves. The counters move whenever a run is accepted, so comparing them is a complete
        change signal - an untouched candidate is reused verbatim instead of re-parsing a 10 KB
        scenario and up to 72 stored run results on every publish. Pruned candidates are dropped and
        new ones added by the same pass, and the supplied-baseline digest keeps the "different
        because" diff honest if a baseline itself is ever edited.
        """
        # Only resident candidates are published. Pruned candidates' lifetime aggregates
        # remain in meta, so scanning all of them makes status cost grow with search history.
        aggregates = {row[0]: row[1] for row in store.db.execute(
            "SELECT m.key,m.value FROM candidate c "
            "CROSS JOIN (SELECT 'discovery' AS phase UNION ALL SELECT 'validation') p "
            "JOIN meta m ON m.key='aggregate:'||c.id||':'||p.phase")}
        rows = list(store.db.execute(
            'SELECT id, label, source, stats, scenario FROM candidate ORDER BY created,id'))
        baselines = {}
        for row in rows:
            if row['source'] != 'supplied':
                continue
            scenario = json.loads(row['scenario'])
            baselines.setdefault(scenario['encounterId'], scenario)
        baseline_digest = hashlib.sha256('\x00'.join(
            f"{row['id']}:{row['scenario']}" for row in rows
            if row['source'] == 'supplied').encode()).hexdigest()
        equivalence = store.equivalence_view()
        published = []
        seen = set()
        for row in rows:
            cid = row['id']
            seen.add(cid)
            cached = self._candidate_cache.get(cid)
            if cached is not None:
                signature = (row['label'], row['source'], row['stats'], row['scenario'],
                             aggregates.get(f'aggregate:{cid}:discovery'),
                             aggregates.get(f'aggregate:{cid}:validation'), baseline_digest)
                if cached[0] == signature:
                    published.append(cached[1])
                    continue
            payload, signature = self._candidate_payload(store, row, aggregates, baselines,
                                                         baseline_digest, equivalence)
            self._candidate_cache[cid] = (signature, payload)
            published.append(payload)
        for cid in [cid for cid in self._candidate_cache if cid not in seen]:
            del self._candidate_cache[cid]
        # Stamp the equivalence block last. A prune can re-elect a group's representative, and a
        # candidate whose own payload was reused from the cache must still reflect that; this is a
        # read-only derivation over the store, never a rewrite of any stored row.
        representative, equivalence_counts = equivalence
        for payload in published:
            cid = payload['id']
            payload['equivalentTo'] = representative.get(cid, cid)
            payload['equivalenceCount'] = equivalence_counts.get(cid, 1)
        # Every published payload already carries its own `encounterId`, cached or fresh, so the set of
        # encounters this library holds costs nothing extra to state here.
        self._library_encounters = sorted({payload['encounterId'] for payload in published})
        return published

    @staticmethod
    def _lane_view(candidates):
        """Every lane's leader per fight, from the evidence already inside the published records.

        Nothing is read here: the lane evidence travelled with the candidate, so ranking the four
        lanes costs a sort over the population, not a re-read of its runs. The counts alongside the
        leaders say how much of the population could enter each lane at all - which is how a library
        whose runs predate the stored-attack metrics reports an empty setup lane instead of a
        fabricated one.

        Legacy candidates that differ only in an inert parameter (Gathering 20, Move 21, Love 22) are
        one strategy, so at most one of them contributes evidence: a duplicate is skipped when its
        representative also carries evidence, and kept only when the representative has none (so no
        measured evidence is dropped). Stored ids and runs are never altered by this view.
        """
        ranked = {candidate['id']: candidate for candidate in candidates if candidate.get('evidence')}
        records = []
        for candidate in candidates:
            evidence = candidate.get('evidence')
            if not evidence:
                continue
            representative = candidate.get('equivalentTo', candidate['id'])
            if representative != candidate['id'] and representative in ranked:
                continue
            records.append(evidence)
        if not records:
            return dict(leaders={}, total=0, withProgress=0, members={})
        lanes = elite_lanes(records)
        members = {lane: 0 for lane in LANE_NAMES}
        for groups in lanes.values():
            for lane, pool in groups.items():
                members[lane] += len(pool)
        return dict(leaders=lane_leaders(records, lanes), total=len(records),
                    withProgress=sum(1 for record in records if record['progressRuns'] > 0),
                    members=members)

    def _learner_snapshot(self, store):
        """Part Q diagnostics: ancestry, learned-operator evidence and useful-improvement counts.

        Deliberately aggregate-only. The detailed per-child evidence stays in the report/test output,
        because a status payload the page polls every two seconds must not carry a growing log.
        """
        # This snapshot is published every two seconds. Reading and converting every lifetime
        # lineage row twice made its cost grow with the full history, even though the UI needs
        # only counts. Let SQLite aggregate the rows and return a handful of values instead.
        counts = store.db.execute('''
            SELECT COUNT(*), COUNT(DISTINCT root), MAX(depth),
                   SUM(CASE WHEN parent IS NOT NULL AND source NOT LIKE 'reseed:%'
                            THEN 1 ELSE 0 END),
                   SUM(CASE WHEN source LIKE 'reseed:%' THEN 1 ELSE 0 END)
            FROM lineage''').fetchone()
        generations = {int(depth): int(n) for depth, n in store.db.execute(
            'SELECT depth, COUNT(*) FROM lineage GROUP BY depth')}
        reseed_by_kind = {str(source).split(':', 1)[1]: int(n)
                          for source, n in store.db.execute(
                              "SELECT source, COUNT(*) FROM lineage WHERE source LIKE 'reseed:%' "
                              'GROUP BY source')}
        operators = store.get('operatorStats') or {}
        attempts = improved = new_region = unproductive = 0
        for group in operators.values():
            for entry in group.values():
                attempts += int(entry.get('attempts', 0) or 0)
                improved += int(entry.get('improved', 0) or 0)
                unproductive += int(entry.get('unproductive', 0) or 0)
                new_region += int(entry.get('newRegion', 0) or 0)
        return dict(learnerVersion=learner.LEARNER_VERSION, mode=LEARNER_MODE,
                    activeLineages=int(counts[1] or 0), lineageEntries=int(counts[0] or 0),
                    deepestGeneration=int(counts[2] or 0),
                    generations=generations,
                    # Anti-starvation provenance: how many candidates the search created because the
                    # pool ran out of useful work, how many were ordinary branches, and which kinds of
                    # reseed they were (`elite`/`historical`/`region`/`root`).
                    normalBranches=int(counts[3] or 0),
                    starvationBranches=int(counts[4] or 0),
                    starvationByKind=reseed_by_kind,
                    reseedCreated=int(store.get('reseedCreated') or 0),
                    operatorAttempts=attempts, operatorImprovements=improved,
                    operatorUnproductive=unproductive, operatorNewRegions=new_region,
                    laneImprovements=int(store.get('laneImprovementCount') or 0),
                    newRegions=int(store.get('newRegionCount') or 0),
                    improvedChildren=len(store.get('childImprovements') or {}),
                    observedChildren=len(store.get('observedChildren') or []),
                    discoveryRuns=learner.DISCOVERY_RUNS,
                    extendedDiscoveryRuns=learner.EXTENDED_DISCOVERY_RUNS,
                    validationRuns=learner.VALIDATION_RUNS,
                    branchFactor=learner.BRANCH_FACTOR,
                    maxOpenPerEncounter=learner.MAX_OPEN_PER_ENCOUNTER,
                    maxCandidatesPerEncounter=MAX_CANDIDATES_PER_ENCOUNTER,
                    maxCandidatesTotal=MAX_CANDIDATES_TOTAL)

    def _publish(self, store, state, error=None):
        from strategy_publication import LatestPublication, ProcessProjection, capture
        if store.pending_records and not error and not getattr(store, '_flush_blocked', False):
            started = time.monotonic()
            written = store.flush()
            self._scheduler['flushSeconds'] += time.monotonic()-started
            self._scheduler['flushRecords'] = self._scheduler.get('flushRecords', 0)+written
        key = (state, error, self._focus_encounter, self._session_runs_base)
        with self.lock:
            if key != self._publication_key or state != 'Running':
                self._publication_epoch += 1
                self._publication_key = key
            epoch = self._publication_epoch
        if self._publication_failure and state == 'Running' and not error:
            raise RuntimeError('Status projection failed: '+self._publication_failure)
        if not ASYNC_PUBLICATION or state != 'Running' or error:
            self._publish_sync(store, state, error)
            return
        if self._publisher is None:
            projection = ProcessProjection()
            # The synchronous startup view has already folded the ledger once. Transfer only its
            # detached aggregate cache to the long-lived Running renderer so that renderer does not
            # repeat the same full fold on its first publication. This is a one-shot private packet;
            # later publications use the process-owned incremental cache.
            try:
                from strategy_encounter_overview import LedgerCache
                cache = getattr(self, '_encounter_overview_cache', None)
                if cache is not None:
                    seed_packet, seed_diag = cache.export_seed_packet(
                        self.path, self.provenance, 256 * 1024 * 1024 - 64 * 1024)
                else:
                    seed_packet, seed_diag = None, dict(
                        state='fallback_full_fold', reason='owner_cache_missing', packetBytes=0)
                projection.offer_ledger_seed(seed_packet, seed_diag)
            except Exception as exc:  # noqa: BLE001 - cache seeding is an optimization only
                projection.offer_ledger_seed(None, dict(
                    state='fallback_full_fold', reason='offer_%s' % type(exc).__name__, packetBytes=0))
            self._publisher = LatestPublication(projection, self._accept_publication,
                                                self._fail_publication, projection.close)
        with self.lock:
            self.snapshot = dict(self.snapshot, state=state, error=error,
                                 focusEncounter=self._focus_encounter,
                                 publicationPending=True)
        self._publisher.submit(epoch, capture(self))

    def _accept_publication(self, epoch, view, seconds):
        with self.lock:
            if epoch != self._publication_epoch or self.shutdown.is_set():
                return
            wire_json = getattr(view, 'wire_json', None)
            # Keep the wire metadata out of `snapshot`: legacy `status()` must continue cloning only
            # the ordinary UI mapping, and no binary transport bytes should enter exported status.
            view = dict(view)
            view['publicationPending'] = False
            view['publicationSeconds'] = seconds
            view['publishedAt'] = time.time()
            throughput = view.get('throughput') or {}
            if throughput.get('isLive') and throughput.get('battlesPerSecond') is not None:
                self._encounter_last_measured_rate = dict(
                    battlesPerSecond=throughput.get('battlesPerSecond'),
                    battlesPerHour=throughput.get('battlesPerHour'),
                    measuredWindowSeconds=throughput.get('windowSeconds'))
            self._snapshot_wire_json = wire_json
            self.snapshot = view

    def _fail_publication(self, epoch, message):
        with self.lock:
            if epoch == self._publication_epoch and not self.shutdown.is_set():
                self._publication_failure = message
                self.snapshot = dict(self.snapshot, publicationError=message)

    def _bridge_rate_view(self, store, overview=None, state=None):
        """The UI's run counters and throughput, from the ledger that actually owns them.

        Encounter (ea_*) results deliberately never increment the legacy ``totalRuns`` counter, so in
        Community-first mode the published session count and rate would otherwise stay frozen at their
        legacy values. This derives them from the persisted ea_* ledger and the evaluator instead; a
        library with no encounter ledger returns exactly the legacy view.
        """
        report = self._encounter_report if isinstance(self._encounter_report, dict) else {}
        # The background projector receives immutable report data, never the live coordinator.
        encounter_active = bool(report.get('enabled'))
        charged = self._encounter_counters(report)
        ledger_counts = (overview or {}).get('counts') or {}
        completed = int(ledger_counts.get('total') or 0)
        legacy_total = int(store.get('totalRuns') or 0)
        encounter_total = completed
        # Session accounting is CAMPAIGN-WIDE. The per-study ``ea_session_budget`` rows reset at
        # every study switch, so a session count built from them fell back to zero each time the
        # campaign moved to the next encounter. The completed-battle ledger never resets, so
        # ``sessionRuns`` is a delta of the distinct completed ea_* results: monotonic across study
        # switches and pause/resume, reset only by an explicit Start after Stop (or the first Start),
        # which re-bases it. Until then it is honestly unset rather than a fabricated zero.
        if self._encounter_session_base is not None:
            session_runs = max(0, completed-self._encounter_session_base)
            basis = 'encounter-session'
        elif self._session_runs_base is not None:
            session_runs = max(0, legacy_total-self._session_runs_base)
            basis = 'legacy-session'
        else:
            session_runs = None
            basis = 'unstarted'
        throughput = (self._encounter_rate_status(
            self._encounter_throughput(report, completed), state)
            if encounter_active else self._throughput(store))
        evidence = dict(total=completed, development=int(ledger_counts.get('development') or 0),
                        confirmation=int(ledger_counts.get('confirmation') or 0),
                        native=int(ledger_counts.get('native') or 0),
                        fallback=int(ledger_counts.get('fallback') or 0),
                        errors=int(ledger_counts.get('errors') or 0),
                        censored=int(ledger_counts.get('censored') or 0),
                        unknownOutcome=int(ledger_counts.get('unknownOutcome') or 0),
                        unmapped=int(ledger_counts.get('unmapped') or 0),
                        excluded=int(ledger_counts.get('excluded') or 0))
        return dict(totalRuns=legacy_total+encounter_total, legacyTotalRuns=legacy_total,
                    encounterRuns=encounter_total, encounterSessionRuns=charged['completed'],
                    encounterSessionReserved=charged['reserved'], sessionRuns=session_runs,
                    sessionRunsBasis=basis, encounterEvidence=evidence, throughput=throughput)

    def _publish_sync(self, store, state, error=None, *, detach=True):
        """Give the desktop one snapshot, including one measured aggregate throughput reading.

        Three numbers must never contradict each other, so they are derived here from one place:
        `secondsPerRun` is the runner's own mean engine time per timed battle, `battlesPerSecond` is
        the *measured* aggregate over a rolling window of completed battles across every worker
        (start-up and idle included, because that is what the user experiences), and
        `battlesPerHour` is that same measurement times 3600 - not a separate projection.
        `capacityBattlesPerSecond` states what the configured workers and duty could sustain if the
        coordinator never stalled, so the gap between achieved and capacity is visible.
        """
        # Normal publication first drains accepted records so the counters, aggregates and candidate
        # cache agree. Error publication keeps a requeued failing batch visible as uncommitted rather
        # than retrying SQLITE_FULL while trying to report it.
        if store.pending_records and not error and not getattr(store, '_flush_blocked', False):
            _flush_started = time.monotonic()
            written = store.flush()
            self._scheduler['flushSeconds'] += time.monotonic()-_flush_started
            self._scheduler['flushRecords'] = self._scheduler.get('flushRecords', 0)+written
        self._scheduler['pendingRecords'] = store.pending_records
        candidates = self._published_candidates(store)
        lanes = self._lane_view(candidates)
        fine_tune = self._finetune_snapshot(store)
        auto_tune = self._auto_tune_snapshot(store, programs=fine_tune)
        # One cached, incremental read of the ea_* ledger serves the counters, the merged encounter
        # rows and the additive average leaders below, so a status poll never rescans the ledger.
        overview = self._encounter_overview(store)
        view = self._bridge_rate_view(store, overview, state=state)
        # The automatic campaign's status block and the persisted multi-select focus. The background
        # Running projection deliberately omits the campaign (it has no coordinator here and must not
        # reopen the library); `status()` fills it from the live in-process report, so no per-publish
        # file open is added to the projection path.
        campaign_view = (deepcopy(self._campaign_report)
                         if self._campaign_report is not None else None)
        focus_view = self._community_focus(store)
        # Refresh the host's planning identity cache from the one focus read this publish already
        # pays for, so `planning_context` never issues its own SQLite read.
        self._planning_focus = [int(encounter) for encounter in focus_view]
        worker_allocation = self._status_worker_allocation()
        if hasattr(self, '_projection_execution_queue'):
            # StatusProjection is detached from the owner and must use only the
            # scalar snapshot captured from the live evaluator.
            queue_status = self._projection_execution_queue
        else:
            queue_evaluator = (getattr(self, '_community_evaluator', None)
                               or (getattr(self, '_encounter', None).evaluator
                                   if getattr(self, '_encounter', None) is not None else None))
            queue_status = None
            if queue_evaluator is not None:
                try:
                    status_reader = getattr(queue_evaluator, 'status', None)
                    status = status_reader() if callable(status_reader) else {}
                    if isinstance(status, dict):
                        queue_status = {key: status.get(key) for key in (
                            'workers', 'inflight', 'readyQueueDepth', 'executingWorkers',
                            'coolingWorkers', 'availableWorkers', 'completedUnharvested',
                            'windowCapacity', 'admissionCapacity', 'readyWindow')}
                except Exception:  # scheduler telemetry must never prevent publication
                    queue_status = None
        self._scheduler['executionQueue'] = queue_status
        with self.lock:
            self.snapshot = dict(state=state, error=error, candidates=candidates, archive=store.archive(),
                focusedExperiment=strategy_experiments.progress(store),
                compatible=store.compatible, totalRuns=view['totalRuns'],
                # The persisted legacy counter is never rewritten; these keep the two histories
                # separable for any consumer while `totalRuns` stays the honest library-wide count.
                legacyTotalRuns=view['legacyTotalRuns'], encounterRuns=view['encounterRuns'],
                encounterSessionRuns=view['encounterSessionRuns'],
                encounterSessionReserved=view['encounterSessionReserved'],
                # The library-wide counter is persistent, so the live session's own count is a delta
                # from the value at Start; `None` until a session has been started.
                sessionRuns=view['sessionRuns'], sessionRunsBasis=view['sessionRunsBasis'],
                proposals=store.get('proposals'), improvements=store.get('improvements'),
                lastImprovementRun=store.get('lastImprovementRun'), provenance=store.get('provenance'),
                acceleration=self.acceleration,
                encounters=self.encounters,
                # The five encounter cards, their four difficulties, and the per-difficulty readings
                # the overview shows. Derived here from the store and the recovered catalogue so the
                # page renders one authoritative answer instead of aggregating runs in the browser.
                campaigns=encounter_campaigns(self.encounters or {}),
                # The legacy lifetime rows with the ea_* ledger folded in additively (see
                # strategy_encounter_overview.merge_stats): the two ledgers are disjoint, so the
                # top-level attempts/wins/losses/maxima the overview reads are their union, while
                # the preserved legacy numbers and the ea breakdown stay separately addressable.
                encounterStats=self._encounter_stats_view(store, overview),
                # The ea_* evidence counts (native/fallback/error/excluded and coverage) so an
                # incomplete or failed ledger is labelled instead of published as a wrong zero.
                encounterLedger=(overview or {}).get('counts'),
                encounterCoverage=(overview or {}).get('coverage'),
                # The additive mean-leader read for the encounter ledger, shaped like the bridge's
                # overview_average_leaders(): the frontend/bridge can prefer this when the legacy
                # run table has no samples for a Community fight.
                encounterAverageLeaders=(overview or {}).get('leaders_payload'),
                # The new objective: which build leads each lane for every fight, with the evidence
                # behind it. A "promising discovery parent" is not the same claim as "recommended
                # reliable strategy", so the two are published separately (`eliteLanes` here, the
                # reliability reading inside each leader's metrics).
                objectiveVersion=store.get('objectiveVersion', 1),
                # The hard search-space contract this library's candidates were generated under. It is
                # published next to the objective version so the page can say plainly when a library is
                # older than the generator's own contract instead of surfacing that only on Start.
                searchSpaceVersion=int(store.get('searchSpaceVersion', 1)),
                objectiveMigration=store.get('objectiveMigration'),
                eliteLanes=lanes['leaders'],
                laneEvidence=dict(population=lanes['total'], withProgress=lanes['withProgress'],
                                  members=lanes['members']),
                # The encounter ids present in *this* library, so the page can tell an encounter-19
                # library from a full one without recounting runs or guessing from the catalogue.
                libraryEncounters=list(self._library_encounters),
                # The encounter every new attempt is focused on right now, or None for the normal
                # distribution. Published so the overview can show the live focus and its clear
                # control, instead of the page keeping a second copy that could drift from the host.
                focusEncounter=self._focus_encounter,
                # The multi-select campaign focus ([], the whole catalogue) and the automatic
                # campaign status. `focusEncounter` is kept above for old consumers.
                focusEncounters=list(focus_view),
                campaign=campaign_view,
                projectionCacheSeed=(dict(getattr(self, '_projection_cache_seed', {})) or None),
                # The bounded exploitation lane for the focused fight: each convincing build's target,
                # how many paired runs it has, what it was granted this pass, and why it is running or
                # has stopped. `None` while nothing is focused or no build has passed screening.
                focusExploit=(dict(self._planner_exploit) if self._planner_exploit else None),
                # The focused record repair lane: the resident Highest Earned holder's identity, its
                # repeat-bank target, or why it was pruned / not yet measurable. Kept separate from
                # `focusExploit` (the mean-leader lane) so the two goals never overwrite each other.
                focusRecord=(dict(self._planner_record) if self._planner_record else None),
                # The four-objective portfolio for the focused fight: the four lanes (with the sample
                # count behind each number), the bounded active slots, the reserved challenger, the
                # graduated/maintenance tier and its periodic rechecks. None while nothing is focused.
                focusPortfolio=(dict(self._planner_portfolio)
                                if self._planner_portfolio else None),
                # A Running search with free workers and nothing dispatchable says so here, with the
                # one action that unblocks it, instead of looking like active search that is merely
                # slow. None whenever the pool is busy or the focused scope still holds work.
                idleReason=self._scheduler.get('idleReason'),
                encounterLifetime=store.get('encounterLifetimeReconstruction'),
                recordHolders=holder_summary(store.get('recordHolders') or {}),
                horizonTicks=horizon_ticks(store),
                probes=self._probe_snapshot(store),
                lastProbe=getattr(self, '_last_probe', None),
                # Controlled fine-tuning programs: the explicit frozen-parent experiments, with their
                # points, banks, paired results and bracketed boundary. Read-only here; the `fine_tune`
                # command creates one and the scheduler advances it while its fight is focused.
                fineTune=fine_tune,
                lastFineTune=getattr(self, '_last_finetune', None),
                # Automatic tuning: which build the focus selected, which axes it started, their
                # current points/runs, and why it is waiting or has stopped. `autoTuneReason` is the
                # one-line form the overview shows when a focused fight has no qualified build yet.
                autoTune=auto_tune,
                autoTuneReason=(auto_tune or {}).get('reason'),
                throughput=dict(workers=worker_allocation['effectiveWorkers'], duty=self._duty,
                                requestedWorkers=worker_allocation['requestedWorkers'],
                                effectiveWorkers=worker_allocation['effectiveWorkers'],
                                effectiveBattleWorkers=worker_allocation['effectiveBattleWorkers'],
                                plannerWorkers=worker_allocation['effectivePlannerWorkers'],
                                requestedBattleWorkers=worker_allocation['requestedBattleWorkers'],
                                requestedPlannerWorkers=worker_allocation['requestedPlannerWorkers'],
                                pendingResize=worker_allocation['pendingResize'],
                                ceiling=worker_ceiling(),
                                accelerators=[name for name in (self.acceleration or {}).get('modules', [])],
                                **view['throughput']),
                # Part Q: enough for the page and the reports to explain the learner without either
                # of them re-deriving it from the candidate list. Cheap by construction - one
                # aggregate row and three counters, never a scan of the population's scenarios.
                learner=self._learner_snapshot(store),
                scheduler=dict({key: value for key, value in self._scheduler.items()
                                if key != 'lastPass'},
                               workerAllocation=worker_allocation),
                averageSimulationSeconds=store.get('simulationSeconds', 0.)/store.get('timedRuns', 1) if store.get('timedRuns', 0) else None,
                timedRuns=store.get('timedRuns', 0),
                diskBytes=sum(p.stat().st_size for p in self.path.parent.glob(self.path.name+'*') if p.is_file()))
            self._snapshot_wire_json = None
            if detach:
                from strategy_publication import detached
                self.snapshot = detached(self.snapshot)

    def _create_probe(self, store, value):
        """Create the ladder of probe candidates for one strategy on one axis.

        Each rung becomes a real candidate in the same library, so it is scheduled, seeded, persisted
        and displayed by the machinery that already exists - a probe is not a side channel around the
        search. A rung whose scenario is already present is reused instead of duplicated, which is
        what makes a probe resumable across sessions.
        """
        from strategy_optimizer_adapter import stats, validate_scenario
        from combat_scenario import ScenarioError
        pivot = next((c for c in store.candidates() if c['id'] == value.get('candidateId')), None)
        if pivot is None:
            raise ValueError('The strategy to probe is not in this library.')
        axis = value.get('axis')
        scenario = json.loads(pivot['scenario'])
        try:
            info = strategy_probe.axis_info(axis)
        except strategy_probe.ProbeError as exc:
            raise ValueError(str(exc)) from None
        axis2 = value.get('axis2')
        info2 = None
        if axis2:
            try:
                info2 = strategy_probe.axis_info(axis2)
            except strategy_probe.ProbeError as exc:
                raise ValueError(str(exc)) from None
        # One human serves both axes of a grid. A conditional axis (INT) refuses a unit that carries
        # no magic attack skill and the default picks a magic attacker rather than the first human, so
        # the ladder always moves a number the fight reads.
        try:
            unit_name = strategy_probe.resolve_probe_unit(scenario, [axis, axis2], value.get('unit'))
        except strategy_probe.ProbeError as exc:
            raise ValueError(str(exc)) from None
        try:
            points = int(value.get('points') or 7)
            ladder = ([int(item) for item in value['values']] if value.get('values')
                      else strategy_probe.default_ladder(scenario, unit_name, axis, points))
            ladder2 = None
            if axis2:
                ladder2 = ([int(item) for item in value['values2']] if value.get('values2')
                           else strategy_probe.default_ladder(scenario, unit_name, axis2,
                                                              int(value.get('points2') or points)))
        except strategy_probe.ProbeError as exc:
            raise ValueError(str(exc)) from None
        known = {equivalence_identity(json.loads(candidate['scenario'])): candidate
                 for candidate in store.candidates()}
        created, reused, refused = [], [], []
        with store.db:
            # A two-axis probe is the cross product: every cell is its own candidate, so the grid is
            # measured and persisted exactly like a single-axis ladder.
            cells = ([(first, second) for first in ladder for second in ladder2] if ladder2
                     else [(step, None) for step in ladder])
            for step, second_step in cells:
                try:
                    candidate_scenario = strategy_probe.apply_axis(scenario, unit_name, axis, step)
                    if second_step is not None:
                        candidate_scenario = strategy_probe.apply_axis(
                            candidate_scenario, unit_name, axis2, second_step)
                    rung = validate_scenario(candidate_scenario)
                except (ValueError, ScenarioError) as exc:
                    # An illegal rung is reported, never forced into the library.
                    refused.append(dict(value=step, value2=second_step, reason=str(exc)))
                    continue
                existing = known.get(equivalence_identity(rung))
                if existing is not None:
                    reused.append(dict(value=step, value2=second_step,
                                       candidateId=existing['id'], label=existing['label']))
                    continue
                label = (strategy_probe.grid_label(axis, step, axis2, second_step, unit_name, pivot['label'])
                         if second_step is not None
                         else strategy_probe.probe_label(axis, unit_name, step, pivot['label']))
                rung_id = store.add(rung, label, strategy_probe.PROBE_SOURCE, stats(rung))
                known[equivalence_identity(rung)] = dict(id=rung_id)
                created.append(dict(value=step, value2=second_step, candidateId=rung_id))
            # The pivot joins the probe's bank: a paired comparison needs the reference measured over
            # the same seeds as each rung. The bank is per candidate, so a quick scan (a small bank)
            # and a full threshold scan (the extended bank) are both expressible.
            bank = max(VALIDATION_RUNS, min(MAX_SAMPLES, int(value.get('runs') or PROBE_VALIDATION_RUNS)))
            banks = dict(store.get('probeBanks') or {})
            banks[pivot['id']] = max(banks.get(pivot['id'], 0), bank)
            for entry in created + reused:
                if entry.get('candidateId'):
                    banks[entry['candidateId']] = max(banks.get(entry['candidateId'], 0), bank)
            store.set('probeBanks', banks)
            if axis2 is None and 'parameter' in info:
                plans = dict(store.get('adaptiveProbePlans') or {})
                key = f'{pivot["id"]}:{unit_name}:{axis}'
                plan = dict(plans.get(key) or dict(pivot=pivot['id'], axis=axis,
                                                   unit=unit_name, rungs={}, done=False))
                rungs = dict(plan['rungs'])
                for entry in created + reused:
                    if entry.get('candidateId'):
                        rungs[str(entry['value'])] = entry['candidateId']
                plan.update(rungs=rungs, done=False)
                plans[key] = plan
                store.set('adaptiveProbePlans', plans)
        return None, dict(axis=axis, axis2=axis2, unit=unit_name, pivotId=pivot['id'],
                          pivotLabel=pivot['label'], runs=bank, ladder=ladder, ladder2=ladder2,
                          established=bool(info.get('established')
                                           and (info2 is None or info2.get('established'))),
                          axisNote=info.get('evidence') if not info.get('established') else None,
                          created=created, reused=reused, refused=refused)

    def _advance_breakthrough(self, store):
        """One slow pass of the breakthrough / reliability stream.

        Thin on purpose: the stream owns its own evidence view, hypotheses, experiments, confirmation
        and budget in `strategy_breakthrough`, and this only supplies the two things the coordinator
        owns - its share of the pool and the encounter focus. It returns whether anything changed, so
        the planner rebuilds its evidence view instead of dispatching a plan it has not seen yet.
        """
        try:
            import strategy_breakthrough
            share = float(self._student_shares.get(students.STUDENT_MECHANISM, 0.0) or 0.0)
            summary = strategy_breakthrough.advance(
                store, share, workers=getattr(self, 'workers', 1) or 1,
                focus_encounter=getattr(self, '_focus_encounter', None))
            self._breakthrough_summary = summary
            self._student_diagnostics['breakthrough'] = dict(
                enabled=summary.get('enabled'), closed=summary.get('closed'),
                started=summary.get('started'), encounter=summary.get('encounter'),
                decision=summary.get('decision'))
            # **Reporting is not scheduling.** These three reads are the expensive part of this pass -
            # measured at 486 ms median for the pass, of which Student 9's own report is 279 ms and the
            # Breakthrough status the rest - and none of them can change while nothing has happened to
            # the library: no new accepted run, no in-flight plan closed or started, the same share. A
            # disabled stream used to rebuild its unchanged view every five seconds regardless, which
            # the trace showed as `breakthrough.pass` costing 29.7 s of a five-minute recording.
            changed = bool(summary.get('started') or summary.get('closed'))
            revision = (int(store.get('totalRuns') or 0), round(share, 6),
                        len(store.get(students.AVERAGE_RETIRED_KEY) or {}))
            if changed or revision != self._report_revision:
                try:
                    # Student 9's own report, on the same slow cadence and for the same reason: it reads
                    # the evidence table, and the two-second publish path must not.
                    self._average_report = students.average_report(store, shares=self._student_shares)
                    self._average_plan = store.get(students.AVERAGE_PLAN_KEY)
                except Exception as exc:  # never let a report read stop the search
                    self._student_diagnostics['averageReportError'] = repr(exc)
                try:
                    # The published view of the stream's own state, bounded per encounter.
                    self._breakthrough_status = strategy_breakthrough.status(store)
                except Exception as exc:  # never let a status read stop the search
                    self._breakthrough_status = dict(error=repr(exc))
                self._report_revision = revision
                self._scheduler['reportRebuilds'] = self._scheduler.get('reportRebuilds', 0) + 1
            return changed
        except Exception as exc:  # never let the new stream stop the search
            self._student_diagnostics['breakthroughError'] = repr(exc)
            return False

    def _advance_mp_recovery(self, store):
        """One slow pass of the MP-recovery operation.

        Thin on purpose, like the Breakthrough pass beside it: the operation owns its allowance,
        eligibility rules, ledger and reporting in `strategy_mp_recovery`, and this only runs it on the
        slow cadence and stashes the published view. It is not a student and has no share of its own -
        its permission is the item allowance the user configured - so nothing here reads
        `_student_shares`, and it never runs a battle: any child it creates is measured through the
        declared bank `strategy_mp_recovery.banks()` offers the scheduler.
        """
        try:
            import strategy_mp_recovery
            summary = strategy_mp_recovery.advance(store)
            self._mp_recovery_summary = summary
            self._student_diagnostics['mpRecovery'] = dict(
                enabled=summary.get('enabled'), effective=summary.get('effective'),
                created=len(summary.get('created') or []), reused=len(summary.get('reused') or []),
                awaiting=len(summary.get('awaiting') or []), decision=summary.get('decision'))
            try:
                # Bounded: the allowance plus the most recent requests, never the whole ledger.
                self._mp_recovery_status = strategy_mp_recovery.status(store)
            except Exception as exc:  # never let a status read stop the search
                self._mp_recovery_status = dict(error=repr(exc))
            return bool(summary.get('created') or summary.get('reused'))
        except Exception as exc:  # noqa: BLE001 - never let the new operation stop the search
            self._student_diagnostics['mpRecoveryError'] = repr(exc)
            return False

    def _advance_probe_plans(self, store):
        """Spend the next probe bank where it reduces uncertainty or narrows a measured edge."""
        from strategy_optimizer_adapter import stats, validate_scenario
        plans = dict(store.get('adaptiveProbePlans') or {})
        if not plans:
            return False
        banks = dict(store.get('probeBanks') or {})
        changed = False
        for key, plan in plans.items():
            if plan.get('done'):
                continue
            pivot_id = plan['pivot']
            pivot = store.scenario(pivot_id)
            if pivot is None:
                plan['done'] = True
                changed = True
                continue
            rungs = dict(plan.get('rungs') or {})
            if (not rungs or store.next_ordinal(pivot_id, 'validation') < banks.get(pivot_id, 0)
                    or any(store.next_ordinal(cid, 'validation') < banks.get(cid, 0)
                           for cid in rungs.values())):
                continue
            reference = dict(strategy_probe.chest_series(store.rows(pivot_id, 'validation')))
            reference.update(strategy_probe.chest_series(store.rows(pivot_id, 'discovery')))
            rows = []
            for value, cid in rungs.items():
                measured = dict(strategy_probe.chest_series(store.rows(cid, 'validation')))
                measured.update(strategy_probe.chest_series(store.rows(cid, 'discovery')))
                delta = strategy_probe.paired_delta(measured, reference)
                rows.append(dict(value=int(value), viable=strategy_probe.paired_status(delta),
                                 n=delta['n'] if delta else 0))
            parameter = strategy_probe.axis_info(plan['axis'])['parameter']
            minimum, maximum = search_contract.stat_bounds()[parameter]
            actions = strategy_probe.next_probe_actions(
                rows, minimum, maximum, max_runs=MAX_SAMPLES,
                max_new=min(2, max(0, 24-len(rungs))))
            if actions['extend']:
                for value in actions['extend']:
                    cid = rungs[str(value)]
                    banks[cid] = min(MAX_SAMPLES, max(VALIDATION_RUNS, banks.get(cid, 0)*2))
                    banks[pivot_id] = max(banks.get(pivot_id, 0), banks[cid])
                changed = True
                continue
            new_count = 0
            pivot_row = store.db.execute('SELECT label FROM candidate WHERE id=?',
                                         (pivot_id,)).fetchone()
            pivot_label = pivot_row[0] if pivot_row else 'adaptive boundary'
            for value in actions['values']:
                try:
                    scenario = validate_scenario(strategy_probe.apply_axis(
                        pivot, plan['unit'], plan['axis'], value))
                except (ValueError, strategy_probe.ProbeError):
                    continue
                cid = store.add(scenario, strategy_probe.probe_label(
                    plan['axis'], plan['unit'], value, pivot_label),
                    strategy_probe.PROBE_SOURCE, stats(scenario))
                rungs[str(value)] = cid
                banks[cid] = max(banks.get(cid, 0), VALIDATION_RUNS)
                banks[pivot_id] = max(banks.get(pivot_id, 0), VALIDATION_RUNS)
                new_count += 1
            if new_count:
                plan['rungs'] = rungs
                changed = True
            else:
                plan['done'] = True
                changed = True
        if changed:
            with store.db:
                store.set('probeBanks', banks)
                store.set('adaptiveProbePlans', plans)
        return changed

    def _probe_snapshot(self, store):
        """Recompute probe analysis only when a probe or its reference actually changes.

        The old global run counter invalidated every ladder after an unrelated fight. The cheap
        metadata query also avoids loading every candidate's scenario merely to test its label.
        """
        metadata = [dict(row) for row in store.db.execute(
            'SELECT id,label,source FROM candidate ORDER BY created,id')]
        probes = [row for row in metadata if strategy_probe.is_probe_candidate(row)]
        if not probes:
            self._probe_cache = None
            return []
        by_label = {row['label']: row['id'] for row in metadata}
        relevant = {row['id'] for row in probes}
        relevant.update(by_label[label] for label in
                        (strategy_probe.pivot_label_of(row['label']) for row in probes)
                        if label in by_label)
        signature = (tuple((row['id'], row['label'], row['source']) for row in metadata),
                     tuple(sorted((cid, store.run_revision(cid)) for cid in relevant)))
        cache = getattr(self, '_probe_cache', None)
        if cache and cache[0] == signature:
            return cache[1]
        analyses = self._probes(store)
        self._probe_cache = (signature, analyses)
        return analyses

    def _probes(self, store):
        """Every probe in this library, analysed into a boundary statement with its evidence.

        The ladder is rebuilt from what is stored, so reopening a library shows the same thresholds
        and every new session sharpens them instead of starting over.
        """
        groups = {}
        grids = {}

        def reference_series(candidate):
            """Every resolved run of a candidate, keyed by seed pair, across both banks.

            A paired comparison only needs *shared seeds*, and the two banks are disjoint by design,
            so using one phase alone would silently drop the pairing whenever the reference happens
            to have runs in the other phase.
            """
            merged = self._finetune_series(store, candidate['id'])
            return merged, len(merged)

        for candidate in store.candidates():
            if not strategy_probe.is_probe_candidate(candidate):
                continue
            pivot_label = strategy_probe.pivot_label_of(candidate['label'])
            if ' × ' in str(candidate['label']):
                head = str(candidate['label'])[len(strategy_probe.PROBE_LABEL_PREFIX):].split(' · ')[0]
                # Group by the axis *pair*, not by the head text: the head carries the values, so
                # grouping on it would make every cell its own one-cell grid.
                first_axis = next((name for name, info in strategy_probe.AXES.items()
                                   if head.startswith(f'{info["label"]} ')), None)
                second_axis = next((name for name, info in strategy_probe.AXES.items()
                                    if f' × {info["label"]} ' in head), None)
                if first_axis and second_axis:
                    grids.setdefault((pivot_label, first_axis, second_axis), []).append(candidate)
                continue
            for axis, info in strategy_probe.AXES.items():
                value = strategy_probe.candidate_value(candidate['label'], axis)
                if value is None:
                    continue
                key = (pivot_label, axis)
                groups.setdefault(key, []).append(dict(value=value, candidate=candidate))
        analyses = []
        by_label = {candidate['label']: candidate for candidate in store.candidates()}
        for (pivot_label, axis, axis2), members in sorted(grids.items(), key=lambda item: str(item[0])):
            pivot = by_label.get(pivot_label)
            if pivot is None:
                continue
            reference, reference_runs = reference_series(pivot)
            cells = []
            for candidate in sorted(members, key=lambda item: item['label']):
                first, second = strategy_probe.grid_values(candidate['label'], axis, axis2)
                if first is None:
                    continue
                rows = (self._finetune_rows(store, candidate['id'], 'validation')
                        or self._finetune_rows(store, candidate['id'], 'discovery'))
                cells.append(dict(v1=first, v2=second, chests=strategy_probe.chest_series(rows),
                                  candidateId=candidate['id'], label=candidate['label'],
                                  runs=len(rows)))
            if not cells:
                continue
            result = strategy_probe.analyse_grid(cells, reference)
            id_by_cell = {(cell['v1'], cell['v2']): cell['candidateId'] for cell in cells}
            analyses.append(dict(
                kind='grid', pivotId=pivot['id'], pivotLabel=pivot_label,
                axis=axis, axisLabel=strategy_probe.AXES[axis]['label'],
                axis2=axis2, axis2Label=strategy_probe.AXES[axis2]['label'],
                referenceRuns=reference_runs,
                statement=result['statement'], basis=result['basis'],
                interaction=result['interaction'], thresholds=result['thresholds'],
                boundaryAt=None,
                points=[dict(value=row['v1'], value2=row['v2'],
                             effective=row['effective1'], effective2=row['effective2'],
                             candidateId=id_by_cell.get((row['v1'], row['v2'])),
                             runs=row['n'], chestMean=row['chestMean'], chestSE=row['chestSE'],
                             viable=row['viable'],
                             delta=(None if row['delta'] is None else dict(
                                 mean=row['delta']['mean'], se=row['delta']['se'], t=row['delta']['t'],
                                 lower=row['delta']['lower'], upper=row['delta']['upper'],
                                 n=row['delta']['n'])))
                        for row in result['rows']]))
        for (pivot_label, axis), members in sorted(groups.items(), key=lambda item: str(item[0])):
            info = strategy_probe.AXES[axis]
            pivot = by_label.get(pivot_label)
            if pivot is None:
                continue
            reference, reference_runs = reference_series(pivot)
            scenario = json.loads(pivot['scenario'])
            unit_name = next((member['candidate']['scenario'] for member in members), None)
            unit_from_label = None
            for member in members:
                parts = str(member['candidate']['label']).split(' · ')
                if len(parts) >= 3:
                    unit_from_label = parts[2]
                    break
            ladder = []
            for member in sorted(members, key=lambda item: item['value']):
                candidate = member['candidate']
                rows = (self._finetune_rows(store, candidate['id'], 'validation')
                        or self._finetune_rows(store, candidate['id'], 'discovery'))
                rung_scenario = json.loads(candidate['scenario'])
                ladder.append(dict(
                    value=member['value'],
                    effective=(strategy_probe.effective_stat(rung_scenario, unit_from_label, axis)
                               if 'parameter' in info and unit_from_label else None),
                    label=candidate['label'],
                    candidateId=candidate['id'],
                    chests=strategy_probe.chest_series(rows),
                    runs=len(rows)))
            analysis = strategy_probe.analyse(ladder, reference)
            # `analyse` returns one row per ladder rung in ladder order, and the rung is what knows
            # the candidate id - join them positionally rather than guessing a key.
            points = [dict(value=row['value'], effective=row['effective'],
                           candidateId=rung['candidateId'], runs=row['n'],
                           chestMean=row['chestMean'], chestSE=row['chestSE'], viable=row['viable'],
                           delta=(None if row['delta'] is None else dict(
                               mean=row['delta']['mean'], se=row['delta']['se'], t=row['delta']['t'],
                               lower=row['delta']['lower'], upper=row['delta']['upper'],
                               n=row['delta']['n'])))
                    for rung, row in zip(ladder, analysis['rows'])]
            analyses.append(dict(
                pivotId=pivot['id'], pivotLabel=pivot_label, axis=axis, axisLabel=info['label'],
                unit=unit_from_label, referenceRuns=reference_runs,
                boundaryAt=(', '.join(f'{min(edge["fromValue"], edge["toValue"])}-'
                                      f'{max(edge["fromValue"], edge["toValue"])}'
                                      for edge in analysis['transitions']) or None),
                **{key: value for key, value in analysis.items()
                   if key in ('viableLow', 'viableHigh', 'statement', 'basis')},
                points=points))
        return analyses

    # ---------------------------------------------------------------------------------------------
    # Controlled fine-tuning: a frozen-parent, single-axis experiment on one focused encounter.
    #
    # A program freezes one parent build and varies one combat statistic at a time, giving every
    # tested point its own run bank and comparing it with the parent over shared seeds. It reuses the
    # probe machinery end to end - a point *is* a probe candidate, so it is scheduled, seeded and
    # persisted by the same code, and the parent joins the probe bank so the paired reference is
    # measured over the same ordinals. The one guarantee on top is focus: a program advances only
    # while its own encounter is the focused one, so every attempt it authorises stays on the fight
    # the user selected, and its child budget keeps it from flooding that fight.
    # ---------------------------------------------------------------------------------------------

    def _prefetch_finetune_rows(self, store, candidates):
        """Bring many candidates' decoded evidence rows current with one statement per chunk.

        Invalidation stays exact per accepted run - a point's readiness and its bracket both depend
        on the rows it can see, so keying the reader on milestones changed what the planner decided
        and a test caught it. What was wrong was the *read*, not the key: a program's points were
        fetched one candidate at a time on the coordinator's worker-feeding path (3,515 whole-table
        reads in 30 seconds on one focused fight). Every candidate whose committed runs moved is now
        fetched in a single `IN (...)`, unchanged candidates are not fetched at all, and only the
        rows that arrived are decoded.
        """
        cache = getattr(self, '_finetune_run_cache', None)
        if cache is None:
            cache = self._finetune_run_cache = {}
        stale = [cid for cid in dict.fromkeys(candidates)
                 if cid and (cid not in cache or cache[cid][0] != store.run_revision(cid))]
        if not stale:
            return
        fetched = strategy_evidence.select_many(store.db, stale)
        for cid in stale:
            all_rows, by_phase = [], {}
            for saved_phase, row in fetched.get(cid, ()):
                all_rows.append(row)
                by_phase.setdefault(saved_phase, []).append(row)
            cache.pop(cid, None)
            cache[cid] = (store.run_revision(cid), all_rows, by_phase)
        # Most-recently-refreshed wins the bounded memory. A batch larger than the bound keeps
        # everything it just fetched and drops the older entries around it.
        keep, fresh = max(256, len(stale)), set(stale)
        for key in [key for key in cache if key not in fresh]:
            if len(cache) <= keep:
                break
            del cache[key]

    def _finetune_rows(self, store, candidate_id, phase=None):
        """Decoded fine-tune rows, refreshed only after this candidate's committed runs change.

        The rows come from the compact persistent evidence table, not the `run` replay window: the
        window deliberately rolls, and a paired comparison must keep every shared seed it measured
        (a threshold edge can need more than `MAX_SAMPLES`). The `run` table remains the legacy
        replay source `Store.rows` exposes. A view needs the same parent/point rows for its
        measurements and its range series, so keep only a bounded set of active candidates in
        memory; SQLite remains authoritative. Callers that need a whole program ask for all of it
        through `_prefetch_finetune_rows` first, so the refresh is one statement rather than one per
        point.
        """
        self._prefetch_finetune_rows(store, (candidate_id,))
        entry = self._finetune_run_cache[candidate_id]
        return entry[1] if phase is None else entry[2].get(phase, [])

    def _finetune_series(self, store, candidate_id):
        """A candidate's resolved runs keyed by seed pair, across both banks (as `_probes` reads it).

        The two banks are disjoint by design, so merging them gives a paired comparison the most
        shared seeds; a point that only ran in one bank still pairs on whatever the parent shares.
        """
        cache = getattr(self, '_finetune_series_cache', None)
        if cache is None:
            cache = self._finetune_series_cache = {}
        revision = store.run_revision(candidate_id)
        previous = cache.pop(candidate_id, None)
        if previous is not None and previous[0] is store and previous[1] == revision:
            cache[candidate_id] = previous
            return dict(previous[2])
        merged = dict(strategy_probe.chest_series(
            self._finetune_rows(store, candidate_id, 'validation')))
        merged.update(strategy_probe.chest_series(
            self._finetune_rows(store, candidate_id, 'discovery')))
        cache[candidate_id] = (store, revision, merged)
        if len(cache) > 256:
            del cache[next(iter(cache))]
        return dict(merged)

    def _finetune_summary(self, store, candidate_id, parent_id, reference):
        """Reuse identical paired statistics across programs until either arm changes.

        Statistical evidence is separate from current bank authorisation and point metadata.
        Both writer and read-only projection stores advance run revisions on evidence changes.
        """
        cache = getattr(self, '_finetune_summary_cache', None)
        if cache is None:
            cache = self._finetune_summary_cache = {}
        key = (candidate_id, parent_id)
        revision = (store.run_revision(candidate_id), store.run_revision(parent_id))
        previous = cache.pop(key, None)
        if previous is None or previous[0] is not store or previous[1] != revision:
            previous = (store, revision, strategy_finetune.summarise_rows(
                self._finetune_rows(store, candidate_id), reference))
        cache[key] = previous
        if len(cache) > 256:
            del cache[next(iter(cache))]
        return previous[2]

    def _finetune_effective(self, scenario, unit_name, parameter_id):
        """The engine's own effective value for this unit's parameter, or None."""
        unit = next((entry for entry in scenario.get('ownUnits') or []
                     if entry.get('name') == unit_name), None)
        if unit is None:
            return None
        try:
            return int(search_contract.battle_value(scenario, unit, parameter_id))
        except (KeyError, TypeError, ValueError):
            return None

    def _finetune_scenario(self, store, program, axis, value, axis2=None, value2=None):
        """The frozen parent with one (or, for a grid cell, two) combat parameter set to a target.

        The write goes through the search contract's own neutral representation
        (`search_contract.set_effective_parameter`), so the target is the *effective* value the player
        sees: for the bounded vitals (HP/MP/Energy) it lowers the prepared maximum the runner refills,
        which is what makes "MP 7149 -> 6500" mean what a player expects. Everything else in the frozen
        parent is copied unchanged, so a measured difference is attributable to this one input.
        """
        scenario = deepcopy(store.scenario(program['parent']['candidateId']))
        unit = next((entry for entry in scenario.get('ownUnits') or []
                     if entry.get('name') == program['unit']), None)
        if unit is None:
            raise ValueError(f'the frozen parent no longer carries unit {program["unit"]!r}')
        search_contract.set_effective_parameter(
            scenario, unit, strategy_probe.AXES[axis]['parameter'], int(value))
        if axis2 is not None:
            search_contract.set_effective_parameter(
                scenario, unit, strategy_probe.AXES[axis2]['parameter'], int(value2))
        return scenario

    def _finetune_add(self, store, program, axis, value, banks, bank, axis2=None, value2=None):
        """Create (or reuse) one tested point/cell as a probe candidate and bank it.

        Returns `(candidate_id, created, effective)`. The frozen parent is banked to the same point, so
        the paired reference is measured over the same ordinals. The candidate is added with the probe
        source, which keeps it out of the mutation population cap and out of the "unevaluated child"
        accounting the focus-liveness work fixed, and records its parent in the lineage table as well as
        the program document.
        """
        from strategy_optimizer_adapter import stats, validate_scenario
        scenario = validate_scenario(self._finetune_scenario(store, program, axis, value, axis2, value2))
        parameter = strategy_probe.AXES[axis]['parameter']
        effective = self._finetune_effective(scenario, program['unit'], parameter)
        tag = f'fine-tune {program["id"]}'
        label = (strategy_probe.grid_label(axis, int(value), axis2, int(value2), program['unit'], tag)
                 if axis2 is not None
                 else strategy_probe.probe_label(axis, program['unit'], int(value), tag))
        cid = identity(scenario)
        parent = program['parent']['candidateId']
        if store.db.execute('SELECT 1 FROM candidate WHERE id=?', (cid,)).fetchone():
            banks[cid] = max(int(banks.get(cid, 0) or 0), int(bank))
            banks[parent] = max(int(banks.get(parent, 0) or 0), int(bank))
            return cid, False, effective
        # A fine-tune point never moves an inert parameter, but the parent may carry values that make
        # the point the same searchable strategy as a held candidate. Reuse that representative rather
        # than storing a false variant; a fine-tune axis is still refused up front, so this cannot
        # turn a combat-stat point into a no-op.
        equivalent = store.equivalent_candidate(scenario)
        if equivalent is not None:
            banks[equivalent] = max(int(banks.get(equivalent, 0) or 0), int(bank))
            banks[parent] = max(int(banks.get(parent, 0) or 0), int(bank))
            return equivalent, False, effective
        cid = store.add(scenario, label, strategy_probe.PROBE_SOURCE, stats(scenario))
        parent_row = store.db.execute('SELECT root, depth FROM lineage WHERE candidate=?',
                                      (parent,)).fetchone()
        root = parent_row['root'] if parent_row else parent
        depth = (int(parent_row['depth']) + 1) if parent_row else 0
        change = (f'{program["axisLabel"]} {int(value)}'
                  + (f' x {strategy_finetune.axis_label(axis2)} {int(value2)}' if axis2 is not None
                     else ''))
        learner.record_child(store.db, cid, parent, root, depth, 'fine-tune', int(value), change,
                             f'fine-tune:{program["id"]}', int(program['encounterId']),
                             store.get('proposals') or 0, search_contract.SEARCH_SPACE_VERSION,
                             time.time_ns())
        banks[cid] = max(int(banks.get(cid, 0) or 0), int(bank))
        banks[parent] = max(int(banks.get(parent, 0) or 0), int(bank))
        return cid, True, effective

    def _finetune_capacity(self, store, encounter_id):
        """The encounter's work-scaled fine-tune admission; the policy lives in `finetune_capacity`."""
        return finetune_capacity(store, encounter_id)

    def _finetune_room(self, store, encounter_id):
        """How many more fine-tuning candidates this encounter may hold right now across every program.

        Retained children are counted from the lineage source so the bound is the fight's, not one
        program's; a candidate that is later pruned stops counting and releases its slot without any
        saved program or measured range being deleted.
        """
        return self._finetune_capacity(store, encounter_id)['room']

    def _finetune_measurements(self, store, program):
        """Per-point evidence for one program, plus the frozen parent's reference series."""
        parent_id = program['parent']['candidateId']
        # One statement covers the whole program: parent, every tested point and every interaction
        # cell. Reading them one at a time is what put a few hundred whole-table reads on the
        # coordinator every time a focused fight's programs were measured.
        self._prefetch_finetune_rows(store, [parent_id]
                                     + [point.get('candidateId')
                                        for point in (program.get('points') or {}).values()]
                                     + [cell.get('candidateId')
                                        for cell in (program.get('cells') or {}).values()])
        reference = self._finetune_series(store, parent_id)
        banks = store.get('probeBanks') or {}
        minimum = int((program.get('budget') or {}).get('minSeeds',
                                                        strategy_finetune.MIN_POINT_SEEDS))
        measurements = {}
        for value, point in (program.get('points') or {}).items():
            cid = point.get('candidateId')
            if cid is None:
                continue
            # A future precision target is not yet authorised work. Waiting for that entire
            # target here can strand an inconclusive point whose current bank already finished.
            authorised_bank = max(int(banks.get(cid, 0) or 0), int(
                (getattr(self, '_planner_limits', None) or {}).get(cid, (0, 0))[1]))
            measurements[int(value)] = strategy_finetune.point_evidence(
                self._finetune_rows(store, cid), reference, value=int(value), candidate_id=cid,
                effective=point.get('effective'), bank=authorised_bank,
                minimum=minimum,
                summary=self._finetune_summary(store, cid, parent_id, reference))
            # An old library may have lost historical samples before compact evidence existed.
            # Distinguish authorised work completion from the number of surviving observations.
            measurements[int(value)]['completedBank'] = store.next_ordinal(cid, 'validation')
        return reference, measurements

    def _effective_bank(self, cid, banks):
        """A candidate's reported run bank: its probe bank, or the focused exploitation target when the
        lane has raised it. The two are the same quantity - how many paired runs the build may hold -
        so the view never shows a bank smaller than the work actually authorised.
        """
        target = (getattr(self, '_planner_exploit', None) or {}).get(cid) or {}
        portfolio_target = int((getattr(self, '_planner_portfolio_banks', None) or {}).get(cid) or 0)
        return max(int((banks or {}).get(cid, 0) or 0), int(target.get('target') or 0),
                   portfolio_target)

    def _finetune_partner(self, store, program):
        """`{axis, baseline, step}` for the interaction partner axis, or None.

        The program's own declared `axis2` is used when present; otherwise the next combat priority
        axis the unit can express, skipping the conditional Intelligence for a non-magic attacker.
        """
        scenario = store.scenario(program['parent']['candidateId'])
        unit = next((entry for entry in (scenario or {}).get('ownUnits') or []
                     if entry.get('name') == program['unit']), None)
        if unit is None:
            return None
        options = ([program['axis2']] if program.get('axis2')
                   else [axis for axis in strategy_finetune.COMBAT_STAT_PRIORITY
                         if axis != program['axis']])
        for axis in options:
            if (strategy_finetune.conditional_axis(axis)
                    and not strategy_probe.unit_has_magic_attack(scenario, unit)):
                continue
            baseline = self._finetune_effective(scenario, program['unit'],
                                                strategy_probe.AXES[axis]['parameter'])
            if baseline is None:
                continue
            return dict(axis=axis, baseline=baseline, step=strategy_finetune.default_step(baseline))
        return None

    def _finetune_advance_one(self, store, program):
        """One pass for one program: create/extend points and, once bracketed, the interaction grid.

        Returns whether anything changed. The program advances only while its encounter is the focused
        one; otherwise it stays inert and is published as waiting for focus, so no attempt it would
        authorise can land on a fight the user is not looking at.
        """
        schedule = program.setdefault('schedule', {})
        if self._focus_encounter != program['encounterId']:
            return False
        upgraded = strategy_finetune.upgrade_depth_budget(program)
        if program.get('status') != 'active':
            if upgraded:
                with store.db:
                    programs = dict(store.get('fineTunePrograms') or {})
                    programs[program['id']] = program
                    store.set('fineTunePrograms', programs)
            return upgraded
        _reference, measurements = self._finetune_measurements(store, program)
        learned = strategy_experiments.learn_stat_points(store, program, measurements)
        actions = strategy_finetune.plan(program, measurements, program.get('bounds'))
        # A per-encounter budget on fine-tuning candidates keeps one fight from accumulating probe
        # builds without bound (they are exempt from the mutation population cap). The budget grows
        # with the fight's recorded runs up to an explicit ceiling, so it is a sustainable bound
        # rather than a lifetime total that a handful of historical programs could exhaust.
        capacity = self._finetune_capacity(store, program['encounterId'])
        room = capacity['room']
        created_here = 0
        cap_changed = bool(schedule.get('encounterCapReached')) != (room <= 0)
        if room <= 0:
            schedule['encounterCapReached'] = True
            schedule['encounterCap'] = dict(capacity)
            schedule['encounterCapReason'] = finetune_capacity_reason(capacity)
            # No space for new candidates must not stop measuring existing points.
        else:
            for key in ('encounterCapReached', 'encounterCap', 'encounterCapReason'):
                schedule.pop(key, None)
        changed = upgraded or cap_changed or learned
        with store.db:
            banks = dict(store.get('probeBanks') or {})
            bank = strategy_finetune.point_bank(program)
            for extend in actions['extend']:
                point = (program.get('points') or {}).get(str(extend['value']))
                if not point:
                    continue
                target_bank = int(extend['bank'])
                cid = point['candidateId']
                if int(banks.get(cid, 0) or 0) < target_bank:
                    banks[cid] = max(int(banks.get(cid, 0) or 0), target_bank)
                    banks[program['parent']['candidateId']] = max(
                        int(banks.get(program['parent']['candidateId'], 0) or 0), target_bank)
                    point['bank'] = target_bank
                    changed = True
            for action in actions['creates']:
                if strategy_finetune.children_left(program) <= 0 or created_here >= room:
                    break
                cid, created, effective = self._finetune_add(
                    store, program, program['axis'], action['value'], banks, bank)
                program.setdefault('points', {})[str(action['value'])] = dict(
                    value=int(action['value']), candidateId=cid, bank=bank, effective=effective,
                    reason=action['reason'])
                if created:
                    program['counters']['children'] = strategy_finetune.children_used(program) + 1
                    created_here += 1
                program['counters']['points'] = len(program['points'])
                changed = True
            if (not schedule.get('interactionDone') and strategy_finetune.children_left(program) > 0
                    and not actions['extend'] and actions.get('coverageComplete', True)):
                grid = strategy_finetune.interaction_plan(
                    program, measurements, self._finetune_partner(store, program))
                if grid:
                    for cell in grid['cells']:
                        if strategy_finetune.children_left(program) <= 0 or created_here >= room:
                            break
                        cid, created, effective = self._finetune_add(
                            store, program, program['axis'], cell['value'], banks, bank,
                            axis2=grid['axis2'], value2=cell['value2'])
                        key = f'{int(cell["value"])}x{int(cell["value2"])}'
                        if key not in program.setdefault('cells', {}):
                            program['cells'][key] = dict(value=int(cell['value']),
                                                         value2=int(cell['value2']),
                                                         candidateId=cid, bank=bank,
                                                         effective=effective)
                        if created:
                            program['counters']['children'] = strategy_finetune.children_used(program) + 1
                            created_here += 1
                        changed = True
                    program['counters']['interactionCells'] = len(program['cells'])
                    schedule['interactionAxis'] = grid['axis2']
                    schedule['interactionDone'] = True
            if actions['status'] == 'done':
                program['status'] = 'done'
                schedule['resolvedBoundary'] = actions['resolved']
            elif (not changed and strategy_finetune.children_left(program) <= 0
                  and not actions['extend']
                  and not any(int(point.get('completedBank', point.get('runs')) or 0)
                              < int(point.get('bank') or 0) for point in measurements.values())):
                program['status'] = 'done'
                schedule['budgetExhausted'] = True
            if changed or program['status'] != 'active':
                programs = dict(store.get('fineTunePrograms') or {})
                programs[program['id']] = program
                store.set('fineTunePrograms', programs)
                store.set('probeBanks', banks)
        return changed

    def _analysis_signature(self, store):
        """The events the slow analysis reacts to - and nothing else.

        Everything in the analysis block decides about *evidence*, not about a battle, so its input
        is the evidence state rather than the run counter. A fine-tune program's stored plan moves
        when one of its points finishes its authorised bank (that is what makes a point ready for a
        bracket or due for an extension), when the program's own definition or bank changes, or when
        one of its participants crosses an evidence rung; the auto-tuned parent set and the
        breakthrough and MP-recovery ledgers move only when a pass actually acts on them, so they are
        carried by their own stored state. Filling a bank with one more seed is not an event any of
        these reacts to, and treating it as one is what put two seconds of evidence decoding on the
        coordinator every five seconds of a focused fight.
        """
        programs = dict(store.get('fineTunePrograms') or {})
        banks = store.get('probeBanks') or {}
        focus = self._focus_encounter
        return (focus,
                tuple((program_id, self._finetune_advance_key(store, program, banks))
                      for program_id, program in sorted(programs.items())
                      if int(program.get('encounterId', -1))
                      == int(focus if focus is not None else -1)),
                canonical(store.get('breakthrough') or {}),
                canonical(store.get('mpRecoveryLedger') or {}))

    def _finetune_advance_key(self, store, program, banks):
        """What one fine-tune program's next plan reads, and nothing besides.

        The program's own definition (its points, cells, targets and schedule), and per participant
        its evidence rung, the bank it is measured against and whether that bank has finished. The
        *set* of declared banks is deliberately not part of it: a child of some unrelated stream
        adding one moves that map constantly without changing a single decision, which is why keying
        on it deferred almost nothing.
        """
        parts = [canonical(program)]
        for cid in _finetune_candidates(program):
            bank = self._effective_bank(cid, banks)
            parts.append((cid, store.milestone_revision(cid), bank,
                          int(bank > 0 and store.next_ordinal(cid, 'validation')-1 >= bank)))
        return tuple(parts)

    def _advance_finetune_programs(self, store):
        """Advance the fine-tuning programs whose own evidence actually moved.

        Measuring all sixty-odd programs of a focused fight on every cadence tick decoded the whole
        encounter's stored evidence - about 43,000 rows - to answer a question whose answer had not
        changed, and it was the largest single consumer of the coordinator's time (16.8 s of 23.5 s
        of analysis in the recording that named it). What a program's plan actually reacts to is a
        point's authorised bank *finishing* (a point cannot be judged or extended before that -
        `strategy_finetune._point_ready` requires `completedBank >= bank`), its own definition
        changing, or the bank it is measured against changing. Each program now carries that key;
        only the ones whose key moved are measured, and a single batched read covers just those.
        """
        programs = dict(store.get('fineTunePrograms') or {})
        if not programs:
            self._finetune_advance_keys = None
            return False
        banks = store.get('probeBanks') or {}
        keys = getattr(self, '_finetune_advance_keys', None)
        keys = keys if isinstance(keys, dict) else {}
        stamps = getattr(self, '_finetune_advance_at', None)
        stamps = stamps if isinstance(stamps, dict) else {}
        now = time.monotonic()
        due = []
        for program_id, program in programs.items():
            if self._focus_encounter != program['encounterId']:
                # An unfocused program is inert by design: authorising anything would put work on a
                # fight the user is not looking at.
                continue
            current = self._finetune_advance_key(store, program, banks)
            if (keys.get(program_id) != current
                    or now-stamps.get(program_id, 0.) >= FINETUNE_ADVANCE_SECONDS):
                due.append(program_id)
                keys[program_id] = current
                stamps[program_id] = now
        for stale in [program_id for program_id in keys if program_id not in programs]:
            keys.pop(stale, None)
            stamps.pop(stale, None)
        self._finetune_advance_keys, self._finetune_advance_at = keys, stamps
        if not due:
            return False
        # One batched read for the programs that are actually due, the same way the publish snapshot
        # reads them: measuring them one candidate at a time put hundreds of statements on the
        # coordinator for a single cadence tick.
        self._prefetch_finetune_rows(
            store, [cid for program_id in due
                    for cid in _finetune_candidates(programs[program_id])])
        changed = False
        for key in due:
            program = programs[key]
            try:
                changed = self._finetune_advance_one(store, program) or changed
            except (ValueError, ScenarioError) as exc:
                # A point the engine refuses is a bounded stop, not a crash: record the reason and block
                # the program so it is never silently retried every pass.
                program['status'] = 'blocked'
                notes = list((program.get('schedule') or {}).get('notes') or [])
                program.setdefault('schedule', {})['notes'] = (notes + [str(exc)])[-8:]
                with store.db:
                    current = dict(store.get('fineTunePrograms') or {})
                    current[key] = program
                    store.set('fineTunePrograms', current)
                changed = True
        if changed:
            self._finetune_cache = None
        return changed

    def _create_finetune(self, store, value):
        """Register one frozen-parent fine-tuning program (the host API's `fine_tune` action).

        A bounded library write on the coordinator, like `probe`: it validates the request, freezes the
        parent (recording its encounter, unit, axis, baseline and any explicit target), and - only when
        that encounter is already focused - creates the first points. Returns `(None, view)` to match
        the probe action's shape. The automatic tuner calls this same method with `auto=True`, so an
        auto-started program is an ordinary program in every respect (lineage, banks, caps) and only
        carries the flag that lets the diagnostics and the auto budget tell it apart.
        """
        parent = next((candidate for candidate in store.candidates()
                       if candidate['id'] == value.get('candidateId')), None)
        if parent is None:
            raise ValueError('The build to fine-tune is not in this library.')
        scenario = json.loads(parent['scenario'])
        axis = value.get('axis')
        try:
            strategy_finetune.require_combat_axis(axis)
        except strategy_finetune.FineTuneError as exc:
            raise ValueError(str(exc)) from None
        axis2 = value.get('axis2') or None
        if axis2:
            try:
                strategy_finetune.require_combat_axis(axis2)
            except strategy_finetune.FineTuneError as exc:
                raise ValueError(str(exc)) from None
        try:
            unit_name = strategy_probe.resolve_probe_unit(scenario, [axis, axis2], value.get('unit'))
        except strategy_probe.ProbeError as exc:
            raise ValueError(str(exc)) from None
        if unit_name is None:
            raise ValueError('a fine-tuning program needs a human unit to vary')
        parameter = strategy_probe.AXES[axis]['parameter']
        baseline = self._finetune_effective(scenario, unit_name, parameter)
        if baseline is None:
            raise ValueError(f'unable to read {unit_name!r} effective '
                             f'{strategy_finetune.axis_label(axis)}')
        programs = dict(store.get('fineTunePrograms') or {})
        suffix = sum(1 for program in programs.values()
                     if program['parent']['candidateId'] == parent['id'])
        program = strategy_finetune.new_program(
            parent=dict(candidateId=parent['id'], label=parent['label']),
            axis=axis, unit=unit_name, baseline=baseline,
            encounter_id=int(scenario['encounterId']),
            targets=value.get('targets') or value.get('values') or (),
            direction=value.get('direction'), step=value.get('step'), budget=value.get('budget'),
            axis2=axis2, bounds=finetune_bounds(parameter), suffix=suffix)
        program['auto'] = bool(value.get('auto'))
        program['portfolio'] = bool(value.get('portfolio'))
        programs[program['id']] = program
        with store.db:
            store.set('fineTunePrograms', programs)
        self._finetune_advance_one(store, program)
        self._finetune_cache = None
        self._last_finetune = self._finetune_view(store, program)
        return None, self._last_finetune

    # Auto-tuning: the frozen-parent programs a focus implies, started without a host command. The
    # parent rule is the library's own result evidence, never an id or a label; the axes are the three
    # the motivating question names; and every budget below keeps the automatic set well inside the
    # encounter's base fine-tune floor (`FINETUNE_BASE_CHILDREN`, 48) so the novel-mutation stream
    # keeps its own capacity even before the fight has recorded the runs that grow the budget.
    def _auto_tune_evidence(self, store, candidate_id):
        """One build's resolved / mean-earned reading from the aggregate counters written with each run.

        This is the host's own result semantics (`chest_count`: unresolved is unknown, a loss released
        zero, a win its certified/queued count) reduced to the numbers the parent rule needs. It reads
        the aggregate rows the publish path already maintains instead of re-parsing up to 512 stored
        runs per candidate on every auto-tune pass.
        """
        n = resolved = unresolved = wins = chest_counted = 0
        chest_sum = 0.
        for phase in ('discovery', 'validation'):
            total = store.get(f'aggregate:{candidate_id}:{phase}') or {}
            n += int(total.get('n', 0) or 0)
            resolved += int(total.get('wins', 0) or 0) + int(total.get('losses', 0) or 0)
            unresolved += int(total.get('censored', 0) or 0)
            wins += int(total.get('wins', 0) or 0)
            chest_counted += int(total.get('chestCount', 0) or 0)
            chest_sum += float(total.get('chestSum', 0.) or 0.)
        return dict(candidateId=candidate_id, n=n, resolved=resolved, unresolved=unresolved, wins=wins,
                    meanEarned=(chest_sum/chest_counted if chest_counted else None),
                    earnedSamples=chest_counted)

    def _auto_tune_parent(self, store, encounter_id):
        """`(record, None)` for the strongest credibly measured build on one encounter, else the reason.

        Every resident non-probe build of the fight is read through `_auto_tune_evidence` and handed to
        `strategy_finetune.select_parent`, which owns the rule: at least 100 resolved runs, no
        unresolved run, at least one win, highest mean earned chests, candidate id as the deterministic
        tie break. Probe builds are skipped: they are the controlled experiments themselves, so freezing
        a tested point as a parent would nest programs instead of tuning the build the player runs.
        """
        encounters = store.candidate_encounters()
        meta = {row['id']: (row['source'], row['label'])
                for row in store.db.execute('SELECT id, source, label FROM candidate')}
        records = []
        for cid, encounter in encounters.items():
            if int(encounter) != int(encounter_id):
                continue
            source, label = meta.get(cid, (None, None))
            if source == strategy_probe.PROBE_SOURCE:
                continue
            evidence = self._auto_tune_evidence(store, cid)
            evidence['label'] = label
            records.append(evidence)
        return strategy_finetune.select_parent(records)

    def _auto_tune_units(self, scenario, axes=None):
        """`[{axis, axisLabel, unit, baseline, reason}]` for the combat axes a frozen team can express.

        The unit is chosen by the build's own usable stat - the human carrying the largest effective
        value for that axis - not by slot order, so "tune Attack" sweeps the attacker's Attack rather
        than the first roster entry's. An axis no human carries a positive value for is reported with a
        reason and never started. `axes` defaults to the core automatic set (MP, Attack, Agility); the
        portfolio passes the whole combat priority order so it also sweeps the vitals and the other
        physical stats. The conditional Intelligence axis is offered only for a human unit that
        actually carries a magic attack skill (`strategy_probe.unit_has_magic_attack`), so INT is never
        swept on a build whose damage reads Attack; Gathering, Move and Heart/Love are never in any
        axis set, combat priority or otherwise.
        """
        axes = strategy_finetune.AUTO_TUNE_AXES if axes is None else tuple(axes)
        options = []
        for axis in axes:
            label = strategy_finetune.axis_label(axis)
            parameter = strategy_probe.AXES[axis]['parameter']
            best = None
            for unit in (scenario or {}).get('ownUnits') or []:
                if not unit.get('human'):
                    continue
                if (strategy_finetune.conditional_axis(axis)
                        and not strategy_probe.unit_has_magic_attack(scenario, unit)):
                    continue
                try:
                    value = int(search_contract.battle_value(scenario, unit, parameter))
                except (KeyError, TypeError, ValueError, search_contract.ContractError):
                    continue
                if value <= 0:
                    continue
                key = (-value, unit['name'])
                if best is None or key < best[0]:
                    best = (key, unit['name'], value)
            if best is None:
                options.append(dict(axis=axis, axisLabel=label, unit=None, baseline=None,
                                    reason=f'no human unit in this build carries a usable {label}'))
            else:
                options.append(dict(axis=axis, axisLabel=label, unit=best[1], baseline=best[2],
                                    reason=None))
        return options


    def _portfolio_tune_lanes(self, store, focus):
        """`(lanes, ledger)` for the four-objective portfolio's automatic tuning, focus-scoped.

        The planner already builds the four lanes on every evidence refresh, so the plan it left for
        the current focus is reused when it matches; only a focus that has no plan yet builds them
        here, from the same persisted evidence the planner reads and without decoding a single run
        row. The ledger is the persisted graduation/tuning-cursor state, so a rotation resumes rather
        than restarting.
        """
        ledger = portfolio.portfolio_state(store.get(portfolio.PORTFOLIO_STATE_KEY) or {})
        plan = getattr(self, '_planner_portfolio', None)
        if plan and int(plan.get('encounterId') or -1) == int(focus):
            return plan.get('lanes') or {}, ledger
        keys = store.candidate_keys()
        aggregate_keys = [f'aggregate:{cid}:{phase}' for cid in keys
                          for phase in ('discovery', 'validation')]
        aggregates = {}
        if aggregate_keys:
            placeholders = ','.join('?' for _ in aggregate_keys)
            aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
                f'SELECT key, value FROM meta WHERE key IN ({placeholders})', aggregate_keys)}
        records = population_records(store, keys, aggregates)
        entries, representative = portfolio.focused_portfolio_inputs(store, records, aggregates, focus)
        return portfolio.focused_portfolio_lanes(entries, representative), ledger

    def _auto_tune(self, store):
        """Start the focus's frozen-parent programs, never letting a failure stop the search.

        The work lives in `_auto_tune_impl`; this wrapper is what the coordinator loop and the focus
        command call, so an unexpected error is reported through the diagnostics instead of killing the
        search thread or failing the focus command.
        """
        try:
            return self._auto_tune_impl(store)
        except Exception as exc:
            self._auto_tune_state = dict(
                encounterId=self._focus_encounter, status='blocked', parent=None, plannedAxes=[],
                reason=f'automatic tuning stopped after an unexpected error: {exc}',
                updatedAt=time.time())
            return False

    def _auto_tune_impl(self, store):
        """Start, or explain why it is not starting, the frozen-parent programs the current focus implies.

        The mean leader (the library's own strongest credibly measured build) and the resident Highest
        Earned record holder - which is often *not* the mean leader, because one exceptional run, not a
        high average, is what it is being studied for - are always tuned. Beside them a bounded rotating
        set of the focused four-objective portfolio's lane candidates is tuned too, so Highest Potential
        and Average Potential builds are tweaked and not only granted repeat banks; that set is capped by
        its own hard program/child totals and skips the mean leader, the record holder and every build
        that already owns a program. Each program is identified by (encounter, parent, axis), so repeated
        passes and status polls never duplicate one; a later leader may add programs as long as the
        per-encounter budgets allow, but an existing program's measured points are never rewritten or
        removed. A record holder that has been pruned is reported, never silently replaced by another
        parent. Returns whether it created programs.
        """
        focus = self._focus_encounter
        if focus is None:
            self._auto_tune_state = dict(
                encounterId=None, status='waiting', parent=None, plannedAxes=[], record=None,
                reason='No encounter is focused; automatic tuning waits until one is.',
                updatedAt=time.time())
            return False
        leader, reason = self._auto_tune_parent(store, focus)
        record, record_report = focused_record_holder(store, focus)
        record_parent = None
        if record is not None:
            record['candidateId'] = record['candidate']
            record['resolved'] = record['validationRuns']
            if record.get('source') != strategy_probe.PROBE_SOURCE:
                record_parent = record
        parents = []
        if leader is not None:
            parents.append(('leader', leader))
        if record_parent is not None and (leader is None
                                          or record_parent['candidateId'] != leader['candidateId']):
            parents.append(('record', record_parent))
        programs = dict(store.get('fineTunePrograms') or {})
        encounter_programs = [program for program in programs.values()
                              if int(program['encounterId']) == int(focus)]
        existing = {(program['parent']['candidateId'], program['axis'])
                    for program in encounter_programs}
        auto = [program for program in encounter_programs if program.get('auto')]
        core_auto = [program for program in auto if not program.get('portfolio')
                     and program.get('status') == 'active']
        planned, started, changed = [], [], False
        for kind, parent in parents:
            scenario = store.scenario(parent['candidateId'])
            if scenario is None:
                planned.append(dict(parentKind=kind, parentId=parent['candidateId'], axis=None,
                                    axisLabel=None, unit=None, baseline=None,
                                    reason=('the selected build is no longer in this library; that '
                                            'expectation is reported rather than silently swapped')))
                continue
            axes = strategy_finetune.AUTO_TUNE_AXES + tuple(
                axis for axis in strategy_finetune.COMBAT_STAT_PRIORITY
                if axis not in strategy_finetune.AUTO_TUNE_AXES)
            for option in self._auto_tune_units(scenario, axes):
                option['parentKind'] = kind
                option['parentId'] = parent['candidateId']
                planned.append(option)
                if option.get('reason'):
                    continue
                fine_capacity = self._finetune_capacity(store, focus)
                if fine_capacity['room'] <= 0:
                    option['reason'] = finetune_capacity_reason(fine_capacity)
                    continue
                key = (parent['candidateId'], option['axis'])
                if key in existing:
                    continue
                budgeted = sum(int((program.get('budget') or {}).get('maxChildren', 0))
                               for program in core_auto)
                if sum(program['parent']['candidateId'] == parent['candidateId']
                       for program in core_auto) >= len(strategy_finetune.AUTO_TUNE_AXES):
                    option['reason'] = 'Three axes are already measuring for this build; remaining axes follow.'
                    continue
                if (len(core_auto) >= strategy_finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER
                        or budgeted + strategy_finetune.AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM
                        > strategy_finetune.AUTO_TUNE_ENCOUNTER_CHILDREN):
                    option['reason'] = ('auto-tune budget for this encounter is full; the existing '
                                        'programs keep measuring')
                    continue
                request = dict(candidateId=parent['candidateId'], axis=option['axis'],
                               unit=option['unit'], budget=strategy_finetune.auto_budget(), auto=True)
                try:
                    self._create_finetune(store, request)
                except (ValueError, ScenarioError) as exc:
                    option['reason'] = str(exc)
                    continue
                changed = True
                started.append(dict(parentKind=kind, parentId=parent['candidateId'],
                                    axis=option['axis']))
                existing.add(key)
                programs = dict(store.get('fineTunePrograms') or {})
                encounter_programs = [program for program in programs.values()
                                      if int(program['encounterId']) == int(focus)]
                auto = [program for program in encounter_programs if program.get('auto')]
                core_auto = [program for program in auto if not program.get('portfolio')
                             and program.get('status') == 'active']

        # Rotating portfolio tuning: the focused fight's lane members - Highest Potential and Average
        # Potential included - are frozen on the combat axes beside the mean leader and record holder.
        # The window is *concurrent*: only programs for builds still in the rotation occupy a slot, so a
        # program that finishes, is blocked, or whose build graduated releases its slot for the next
        # lane member or challenger while the finished program and its measured points stay stored. The
        # encounter's work-scaled fine-tune budget (`finetune_capacity`, enforced on every creation)
        # caps the retained fine-tune children at an explicit ceiling, so a 3.3m-run library still
        # cannot spawn a program per candidate while a fresh one cannot spawn hundreds at once.
        # Graduated builds are skipped by the rotation; they return only after a periodic recheck
        # measures fresh evidence.
        active_portfolio = [program for program in auto
                            if program.get('portfolio') and program.get('status') == 'active']
        capacity = strategy_finetune.AUTO_TUNE_PORTFOLIO_MAX_PROGRAMS-len(active_portfolio)
        lanes = None
        if active_portfolio:
            # A graduated build has left the rotation, so its residual program no longer holds a
            # rotating slot even while its last points finish measuring; the ledger says which of the
            # active programs those are. Fetching the ledger is deferred until a program is active, so
            # the empty/late passes stay cheap.
            lanes, ledger = self._portfolio_tune_lanes(store, focus)
            graduated = {candidate for candidate, row in (ledger.get('members') or {}).items()
                         if row.get('stable')}
            active_portfolio = [program for program in active_portfolio
                                if program['parent']['candidateId'] not in graduated]
            capacity = strategy_finetune.AUTO_TUNE_PORTFOLIO_MAX_PROGRAMS-len(active_portfolio)
        portfolio_budgeted = sum(int((program.get('budget') or {}).get('maxChildren', 0))
                                 for program in active_portfolio)
        if capacity > 0:
            if lanes is None:
                lanes, ledger = self._portfolio_tune_lanes(store, focus)
            axes_by_candidate = {}
            for program in encounter_programs:
                axes_by_candidate.setdefault(program['parent']['candidateId'], set()).add(
                    program['axis'])
            # Stage one - breadth. One useful combat-stat sweep goes to each *distinct* credible lane
            # member that does not already own a program, across all four objectives, before any
            # candidate is revisited. The mean leader and record holder are excluded here because the
            # core path already gives them their axes; excluding every build that owns a program is
            # what keeps breadth first rather than letting an incumbent consume the window.
            used = {program['parent']['candidateId'] for program in encounter_programs}
            used.update(parent['candidateId'] for _kind, parent in parents)
            targets, ledger = portfolio.portfolio_tune_targets(lanes, ledger, capacity,
                                                               exclude=used)
            # Stage two - depth. Only once breadth has taken every slot it can - i.e. no credible
            # untuned candidate is waiting - may a promising member that already owns its breadth
            # sweep take its next established combat axis. This is what learns a second stat range
            # (Attack then Speed, say) and gives the engine a second single-axis program to pair into
            # a two-axis interaction. It stays bounded by the per-candidate axis cap and the
            # concurrent program/child budgets; candidates at the cap, and the core-tuned
            # leader/record, are never revisited.
            depth_capacity = capacity-len(targets)
            if depth_capacity > 0:
                depth_exclude = {candidate for candidate, axes in axes_by_candidate.items()
                                 if len(axes) >= portfolio.PORTFOLIO_TUNE_AXES_PER_CANDIDATE}
                # Depth only revisits a candidate that already owns a *portfolio* program - its
                # breadth sweep. A build tuned by the core path (the mean leader, the record holder)
                # or one that owns no program at all is never picked up here, so depth cannot shadow
                # the core axes or jump a member still waiting for its first sweep.
                swept = {program['parent']['candidateId'] for program in encounter_programs
                         if program.get('portfolio')}
                depth_exclude.update(candidate for candidate in used if candidate not in swept)
                depth_exclude.update(parent['candidateId'] for _kind, parent in parents)
                depth_targets, ledger = portfolio.portfolio_tune_targets(
                    lanes, ledger, depth_capacity, exclude=depth_exclude)
                targets = targets+depth_targets
            axis_offset = int(ledger.get('tuneAxis') or 0)
            created_portfolio = 0
            for index, target in enumerate(targets):
                target_id = target['candidate']
                option = dict(parentKind='portfolio', parentId=target_id,
                              objective=target.get('objective'), axis=None, axisLabel=None,
                              unit=None, baseline=None, reason=None)
                if not portfolio.portfolio_tune_credible(target, target.get('objective')):
                    option['reason'] = ('the portfolio candidate is not credibly measured for its '
                                        'lane yet; it keeps its rotation slot until it is')
                    planned.append(option)
                    continue
                scenario = store.scenario(target_id)
                if scenario is None:
                    option['reason'] = ('the portfolio candidate is no longer in this library; that '
                                        'expectation is reported rather than silently swapped')
                    planned.append(option)
                    continue
                taken = axes_by_candidate.get(target_id, set())
                if len(taken) >= strategy_finetune.AUTO_TUNE_PORTFOLIO_AXES_PER_CANDIDATE:
                    option['reason'] = ('this build already holds its full combat-axis allowance; the '
                                        'rotation moves on so other lane members are reached')
                    planned.append(option)
                    continue
                fine_capacity = self._finetune_capacity(store, focus)
                if fine_capacity['room'] <= 0:
                    option['reason'] = finetune_capacity_reason(fine_capacity)
                    planned.append(option)
                    continue
                if (portfolio_budgeted + strategy_finetune.AUTO_TUNE_PORTFOLIO_CHILDREN_PER_PROGRAM
                        > strategy_finetune.AUTO_TUNE_PORTFOLIO_CHILDREN):
                    option['reason'] = ('the concurrent portfolio auto-tune budget is full; the '
                                        'existing programs keep measuring')
                    planned.append(option)
                    continue
                usable = [unit for unit in self._auto_tune_units(
                              scenario, strategy_finetune.COMBAT_STAT_PRIORITY)
                          if not unit.get('reason') and unit['axis'] not in taken]
                if not usable:
                    option['reason'] = ('no untuned human combat statistic is available in this '
                                        'build')
                    planned.append(option)
                    continue
                chosen = usable[(axis_offset+index) % len(usable)]
                axis = chosen['axis']
                if (target_id, axis) in existing:
                    planned.append(option)
                    continue
                request = dict(candidateId=target_id, axis=axis, unit=chosen['unit'],
                               budget=strategy_finetune.auto_budget(), auto=True, portfolio=True)
                try:
                    self._create_finetune(store, request)
                except (ValueError, ScenarioError) as exc:
                    option['reason'] = str(exc)
                    planned.append(option)
                    continue
                option.update(axis=axis, axisLabel=chosen.get('axisLabel'), unit=chosen['unit'],
                              baseline=chosen.get('baseline'))
                planned.append(option)
                changed = True
                created_portfolio += 1
                started.append(dict(parentKind='portfolio', parentId=target_id, axis=axis,
                                    objective=target.get('objective')))
                existing.add((target_id, axis))
                axes_by_candidate.setdefault(target_id, set()).add(axis)
                programs = dict(store.get('fineTunePrograms') or {})
                encounter_programs = [program for program in programs.values()
                                      if int(program['encounterId']) == int(focus)]
                auto = [program for program in encounter_programs if program.get('auto')]
                active_portfolio = [program for program in auto if program.get('portfolio')
                                    and program.get('status') == 'active']
                portfolio_budgeted = sum(int((program.get('budget') or {}).get('maxChildren', 0))
                                         for program in active_portfolio)
            if created_portfolio:
                ledger['tuneAxis'] = axis_offset+created_portfolio
                with store.db:
                    store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
        if changed:
            self._finetune_cache = None
        # The paired two-axis interaction reading. Only a candidate holding two or more single-axis
        # programs can answer "do these stats interact?", and only a grid the engine actually
        # scheduled may claim one; anything else is reported as the exact gap, never a joint range
        # invented from two independent single-axis brackets. It is read from the same program views
        # the diagnostics publish, and only when such a candidate exists, so a breadth-only pass
        # stays cheap.
        joint = []
        portfolio_counts = {}
        for program in encounter_programs:
            if program.get('portfolio'):
                cid = program['parent']['candidateId']
                portfolio_counts[cid] = portfolio_counts.get(cid, 0) + 1
        if any(count >= 2 for count in portfolio_counts.values()):
            joint = portfolio.portfolio_joint_interaction(
                [view for view in self._finetune_snapshot(store)
                 if view.get('portfolio') and int(view['encounterId']) == int(focus)])
        active = [dict(option) for option in planned
                  if not option.get('reason')
                  and (option.get('parentId'), option.get('axis')) in existing]
        previous = None
        if (self._auto_tune_state and int(self._auto_tune_state.get('encounterId') or -1) == int(focus)
                and (self._auto_tune_state.get('parent') or {})):
            previous = self._auto_tune_state['parent'].get('candidateId')
        primary = leader if leader is not None else record_parent
        leader_changed = bool(previous and primary and previous != primary.get('candidateId'))
        notes = []
        if reason:
            notes.append(reason)
        if record_report is not None and record_report.get('reason'):
            notes.append(record_report['reason'])
        notes.extend(option['reason'] for option in planned if option.get('reason'))
        for reading in joint:
            if reading.get('gap'):
                notes.append(f"the paired two-axis interaction on {reading['candidate']} is open: "
                             f"{reading['gap']}")
        if leader_changed:
            notes.append('the strongest build changed; the existing programs and their measured '
                         'points are kept')
        if leader is None and record_parent is None:
            status = 'no-parent'
        elif active or started:
            status = 'active'
        else:
            status = 'waiting'
        parent_view = None
        if primary is not None:
            parent_view = dict(candidateId=primary['candidateId'], label=primary.get('label'),
                               meanEarned=primary.get('meanEarned'), resolved=primary.get('resolved'),
                               unresolved=primary.get('unresolved'), wins=primary.get('wins'),
                               n=primary.get('n'))
        record_view = dict(record_report or {})
        if record is not None:
            record_view.update(candidateId=record['candidateId'], label=record.get('label'),
                               recordValue=record.get('recordValue'),
                               resolved=record.get('validationRuns'), wins=record.get('wins'),
                               sameAsLeader=bool(leader and leader.get('candidateId')
                                                 == record['candidateId']))
            if record_parent is not None:
                record_view['status'] = 'tuning'
            else:
                record_view['status'] = 'probe'
                record_view.setdefault('reason', (
                    'the record holder is a measured fine-tune point; it keeps its repeat bank, but '
                    'a point is not frozen as another program parent'))
        self._auto_tune_state = dict(
            encounterId=int(focus), status=status, parent=parent_view, plannedAxes=planned,
            startedAxes=started, leaderChanged=leader_changed, record=record_view,
            jointInteractions=joint,
            reason=('; '.join(notes) if notes else None), updatedAt=time.time())
        return changed


    def _auto_tune_snapshot(self, store, programs=None):
        """The automatic-tuning diagnostic: parent, active axes, points/runs and why it waits or stops.

        Built from the session's last auto-tune decision plus the live program views, so a status poll
        reports the current points and runs rather than the numbers at decision time.
        """
        state = getattr(self, '_auto_tune_state', None)
        if not state:
            return None
        view = dict(state)
        parent_id = (state.get('parent') or {}).get('candidateId')
        if programs is None:
            programs = self._finetune_snapshot(store) if parent_id else []
        axes = []
        for option in state.get('plannedAxes') or []:
            option_parent = option.get('parentId') or parent_id
            program = next((program for program in programs
                            if program['axis'] == option['axis']
                            and int(program['encounterId']) == int(state['encounterId'])
                            and program['parent']['candidateId'] == option_parent), None)
            entry = dict(axis=option['axis'], axisLabel=option.get('axisLabel'),
                         unit=option.get('unit'), baseline=option.get('baseline'),
                         reason=option.get('reason'))
            if program is not None:
                entry.update(programId=program['id'], status=program['status'],
                             points=len(program['points']),
                             runs=sum(int(point.get('runs') or 0) for point in program['points']),
                             children=program['used']['children'],
                             maxChildren=program['budget'].get('maxChildren'),
                             waitingForFocus=program['waitingForFocus'])
            else:
                entry.update(programId=None, status=None, points=0, runs=0, children=0,
                             maxChildren=None, waitingForFocus=False)
            axes.append(entry)
        view['axes'] = axes
        return view

    def _finetune_view(self, store, program):
        """One machine-readable program view: identity, budget, lineage, points, results, bracket."""
        _reference, measurements = self._finetune_measurements(store, program)
        minimum = int((program.get('budget') or {}).get('minSeeds', strategy_finetune.MIN_POINT_SEEDS))
        banks = store.get('probeBanks') or {}
        fields = ('runs', 'resolved', 'wins', 'losses', 'unresolved', 'winRate', 'winInterval',
                  'meanEarned', 'earnedSamples', 'earnedSE', 'meanPotential', 'potentialSamples',
                  'chestMean', 'chestSE', 'chestMin', 'chestMax', 'paired', 'viable', 'ready',
                  'comparable')
        evidence_points, points = [], []
        for value in sorted(int(key) for key in (program.get('points') or {})):
            point = program['points'][str(value)]
            evidence = measurements.get(value)
            if evidence is None:
                continue
            evidence_points.append(dict(value=value, effective=point.get('effective'),
                                        label=point.get('label'),
                                        chests=(self._finetune_series(store, point['candidateId'])
                                                if evidence['comparable'] else {})))
            points.append(dict(value=value, effective=point.get('effective'),
                               candidateId=point['candidateId'],
                               bank=self._effective_bank(point['candidateId'], banks),
                               reason=point.get('reason'),
                               **{key: evidence.get(key) for key in fields}))
        cells, cell_points, grid_view = [], [], None
        if program.get('cells'):
            grid_axis2 = (program.get('schedule') or {}).get('interactionAxis') or program.get('axis2')
            for key in sorted(program['cells']):
                cell = program['cells'][key]
                evidence = strategy_finetune.point_evidence(
                    self._finetune_rows(store, cell['candidateId']), _reference, value=cell['value'],
                    candidate_id=cell['candidateId'], effective=cell.get('effective'),
                    bank=self._effective_bank(cell['candidateId'], banks), minimum=minimum)
                cell_points.append(dict(value=cell['value'], value2=cell['value2'],
                                        effective=cell.get('effective'),
                                        chests=(self._finetune_series(store, cell['candidateId'])
                                                if evidence['comparable'] else {})))
                cells.append(dict(value=cell['value'], value2=cell['value2'],
                                  candidateId=cell['candidateId'],
                                  bank=self._effective_bank(cell['candidateId'], banks),
                                  **{field: evidence.get(field) for field in fields}))
            analysis_grid = (strategy_finetune.analyse_cells(cell_points, _reference)
                             if cell_points else None)
            grid_view = dict(axis2=grid_axis2,
                             statement=(analysis_grid['statement'] if analysis_grid else None),
                             interaction=(analysis_grid['interaction'] if analysis_grid else None))
        analysis = (strategy_finetune.analyse_points(evidence_points, _reference)
                    if evidence_points else None)
        ready = strategy_finetune._ready_measurements(measurements, minimum)
        pairs = strategy_finetune.brackets(program, ready)
        boundary = None
        if pairs:
            left, right = pairs[0]
            boundary = dict(low=min(left, right), high=max(left, right),
                            resolution=abs(right - left), bracketed=True)
        focused = self._focus_encounter == program['encounterId']
        return dict(
            id=program['id'], status=program['status'],
            auto=bool(program.get('auto')),
            portfolio=bool(program.get('portfolio')),
            axis=program['axis'], axisLabel=program['axisLabel'], axis2=program.get('axis2'),
            unit=program['unit'], encounterId=int(program['encounterId']),
            parent=dict(program['parent']), baseline=dict(program['baseline']),
            direction=program['direction'], step=int(program['step']),
            targets=list(program.get('targets') or []),
            budget=dict(program.get('budget') or {}),
            used=dict(children=strategy_finetune.children_used(program),
                      points=len(program.get('points') or {}),
                      interactionCells=len(program.get('cells') or {})),
            focused=focused,
            dispatchable=bool(focused and program['status'] == 'active'
                              and strategy_finetune.children_left(program) > 0),
            waitingForFocus=not focused,
            minSeeds=minimum,
            boundary=boundary,
            resolved=bool(program.get('status') == 'done'
                          and (program.get('schedule') or {}).get('resolvedBoundary')),
            viableLow=(analysis['viableLow'] if analysis else None),
            viableHigh=(analysis['viableHigh'] if analysis else None),
            statement=(analysis['statement'] if analysis else None),
            basis=(analysis['basis'] if analysis else None),
            grid=grid_view,
            points=points, cells=cells,
        )

    def _finetune_snapshot(self, store):
        """Refresh only programs whose own measurements or presentation inputs changed.

        A global ``totalRuns`` key rebuilt every program after every unrelated fight. On a mature
        library that decoded the same retained run banks again for each two-second status publish.
        Program definitions, focus and effective banks are included so a cache hit cannot hide a
        new target, point, interaction cell, or focus change.
        """
        programs = dict(store.get('fineTunePrograms') or {})
        if not programs:
            self._finetune_cache = None
            return []
        cache = getattr(self, '_finetune_cache', None)
        cache = cache if isinstance(cache, dict) else {}
        banks = store.get('probeBanks') or {}
        # Every candidate every program reads, in one statement per chunk, before any program is
        # rendered. A focused fight holds sixty-odd programs; one read each was most of the publish
        # cost the desktop paid for its two-second snapshot.
        self._prefetch_finetune_rows(
            store, [cid for program in programs.values() for cid in _finetune_candidates(program)])
        refreshed = {}
        views = []
        for program_id, program in programs.items():
            candidates = _finetune_candidates(program)
            signature = (canonical(program), self._focus_encounter,
                         tuple(sorted((cid, store.run_revision(cid), self._effective_bank(cid, banks),
                                       (getattr(self, '_planner_limits', None) or {}).get(cid, (0, 0))[1])
                                      for cid in candidates)))
            previous = cache.get(program_id)
            view = previous[1] if previous and previous[0] == signature else self._finetune_view(store, program)
            refreshed[program_id] = (signature, view)
            views.append(view)
        views.sort(key=lambda view: view['id'])
        self._finetune_cache = refreshed
        return views
    def _throughput(self, store):
        """One measured aggregate rate, from the completed-battle samples the loop records."""
        samples = [sample for sample in self._rate_samples
                   if sample[0] >= time.monotonic()-RATE_WINDOW_SECONDS]
        self._rate_samples = samples
        recent = [row for row in self._engine_samples
                  if row[0] >= time.monotonic()-RATE_WINDOW_SECONDS]
        self._engine_samples = recent
        timed = store.get('timedRuns', 0)
        # The current mix first; the library-wide mean only until a window exists.
        seconds_per_run = (sum(row[1] for row in recent)/len(recent)) if recent else (
            store.get('simulationSeconds', 0.)/timed if timed else None)
        measured = window = None
        if len(samples) > 1:
            window = samples[-1][0]-samples[0][0]
            runs = samples[-1][1]-samples[0][1]
            if window >= RATE_MIN_SECONDS and runs > 0:
                measured = runs/window
        # In planner mode only the effective battle pool runs simulations. Legacy mode reports
        # every configured worker as a battle worker through this same allocation snapshot.
        capacity_workers = int(self._status_worker_allocation()['effectiveBattleWorkers'])
        capacity = None
        if seconds_per_run:
            capacity = capacity_workers*self._duty/seconds_per_run
        return dict(secondsPerRun=seconds_per_run,
                    secondsPerRunBasis='rolling-window' if recent else 'library-average',
                    battlesPerSecond=measured,
                    battlesPerHour=measured*3600 if measured is not None else None,
                    windowSeconds=window,
                    capacityWorkers=capacity_workers,
                    capacityBattlesPerSecond=capacity,
                    achievedFraction=(measured/capacity) if (measured is not None and capacity) else None)

    # -- encounter (ea_*) session accounting ------------------------------------------------------
    @staticmethod
    def _encounter_counters(report):
        """Charged run counts for the active ea_* session, from the ledger's own budget rows.

        `reserved` is the session's actual spending: the ledger charges each unique sample exactly
        once, so a coalesced copy another experiment links to is never charged - and therefore never
        counted - twice. `completed` is the persisted result count. Both are absolute for the session.
        """
        rows = ((report or {}).get('budgets') or {}).get('session') or {}
        reserved = completed = total = 0
        for row in rows.values():
            row = row or {}
            reserved += int(row.get('reserved') or 0)
            completed += int(row.get('completed') or 0)
            total += int(row.get('total') or 0)
        return dict(total=total, reserved=reserved, completed=completed)

    @staticmethod
    def _encounter_evaluator(report):
        return ((report or {}).get('timings') or {}).get('evaluator') or {}

    def _encounter_overview(self, store):
        """The cached, incremental read model over the ea_* ledger (strategy_encounter_overview).

        The cache lives on this renderer and survives across status renders (including the process
        projection's persistent renderer), so an unchanged poll pays only a cheap change signature.
        A failure is published as a labelled diagnostic, never as a fabricated zero.
        """
        from strategy_encounter_overview import LedgerCache
        cache = getattr(self, '_encounter_overview_cache', None)
        if cache is None:
            cache = LedgerCache()
            self._encounter_overview_cache = cache
        try:
            return cache.view(store.db)
        except Exception as exc:  # noqa: BLE001 - diagnose, never invent counts
            message = '%s: %s' % (type(exc).__name__, exc)
            return dict(available=False, error=message, counts={}, rows={}, leaders=[],
                        leaders_payload=dict(ok=False, error=message),
                        coverage=dict(available=False, complete=False, error=message))

    def _encounter_stats_view(self, store, overview=None):
        """The legacy lifetime rows with the ea_* ledger folded in additively (union, no double count)."""
        from strategy_encounter_overview import merge_stats
        legacy = store.get('encounterLifetime')
        if legacy is None:
            # A read-only projection cannot reconstruct-and-write; fall back to the stored rows and
            # let the ea_* ledger supply the counts rather than fail the whole publication.
            try:
                legacy = encounter_stats(store)
            except Exception:  # noqa: BLE001
                legacy = {}
        return merge_stats(legacy if isinstance(legacy, dict) else {},
                           overview if overview is not None else self._encounter_overview(store))

    def _encounter_lifetime_total(self, store):
        """Distinct persisted ea_* results in this library; each coalesced sample counted once.

        Reads the same cached view the publication uses, so it is never a second scan of the ledger.
        """
        counts = (self._encounter_overview(store).get('counts') or {})
        return int(counts.get('total') or 0)

    def _reset_encounter_rate_samples(self, completed, *, now=None):
        """Begin a fresh wall-clock measurement window at the current durable count."""
        sampled_at = time.monotonic() if now is None else float(now)
        self._encounter_rate_samples = [(sampled_at, int(completed))]

    def _sample_encounter_rate(self, completed, *, running, now=None):
        """Sample only active Running wall time; a pause cannot dilute the next resumed rate."""
        if not running:
            self._encounter_rate_samples = []
            return
        sampled_at = time.monotonic() if now is None else float(now)
        samples = self._encounter_rate_samples
        if (not samples or int(completed) > samples[-1][1]
                or sampled_at-samples[-1][0] >= 0.5):
            samples.append((sampled_at, int(completed)))
        cutoff = sampled_at-RATE_WINDOW_SECONDS
        while len(samples) > 1 and samples[0][0] < cutoff:
            samples.pop(0)

    def _encounter_rate_status(self, throughput, state):
        """Mark rolling speed as live only while running; keep the last active sample diagnostic."""
        result = dict(throughput)
        measured = result.get('battlesPerSecond')
        if state == 'Running' and measured is not None:
            self._encounter_last_measured_rate = dict(
                battlesPerSecond=measured,
                battlesPerHour=result.get('battlesPerHour'),
                measuredWindowSeconds=result.get('windowSeconds'))
        last = dict(getattr(self, '_encounter_last_measured_rate', None) or {})
        result.update(state=state or 'unknown', isLive=(state == 'Running'),
                      lastMeasuredBattlesPerSecond=last.get('battlesPerSecond'),
                      lastMeasuredBattlesPerHour=last.get('battlesPerHour'),
                      lastMeasuredWindowSeconds=last.get('measuredWindowSeconds'))
        if state != 'Running':
            result.update(battlesPerSecond=None, battlesPerHour=None, achievedFraction=None)
        return result

    def _encounter_throughput(self, report, completed=None):
        """Measured throughput for the active encounter session, from the evaluator's own timing.

        `secondsPerRun` is the evaluator's own mean battle time (`simulationSeconds/completed`), and
        the aggregate rate is measured over the campaign-wide completed-battle samples the loop
        records from the persisted ledger - monotonic across study switches, so the window never sees
        a negative delta when the campaign moves to the next encounter.
        """
        evaluator = self._encounter_evaluator(report)
        charged = int(evaluator.get('completed') or 0)
        seconds = float(evaluator.get('simulationSeconds') or 0.0)
        seconds_per_run = (seconds/charged) if charged else None
        samples = [sample for sample in (self._encounter_rate_samples or ())
                   if sample[0] >= time.monotonic()-RATE_WINDOW_SECONDS]
        self._encounter_rate_samples = samples
        measured = window = None
        if len(samples) > 1:
            window = samples[-1][0]-samples[0][0]
            runs = samples[-1][1]-samples[0][1]
            if window >= RATE_MIN_SECONDS and runs >= 0:
                measured = runs/window
        capacity_workers = int(self._status_worker_allocation()['effectiveBattleWorkers'])
        capacity = (capacity_workers*self._duty/seconds_per_run) if seconds_per_run else None
        ledger_completed = int(completed) if completed is not None else charged
        return dict(secondsPerRun=seconds_per_run,
                    secondsPerRunBasis='encounter-session' if seconds_per_run else 'unmeasured',
                    battlesPerSecond=measured,
                    battlesPerHour=measured*3600 if measured is not None else None,
                    windowSeconds=window,
                    capacityWorkers=capacity_workers,
                    capacityBattlesPerSecond=capacity,
                    achievedFraction=(measured/capacity) if (measured is not None and capacity) else None,
                    basis='encounter-ledger',
                    completedBattles=ledger_completed,
                    inflightBattles=int(evaluator.get('inflight') or 0))

    @staticmethod
    def _encounter_publish_signature(report, state, error):
        """A cheap change signature so a paused encounter loop publishes only when something moved."""
        report = report if isinstance(report, dict) else {}
        progress = report.get('progress') or {}
        rows = (report.get('budgets') or {}).get('session') or {}
        evaluator = (report.get('timings') or {}).get('evaluator') or {}
        budget_sig = tuple(sorted(
            (str(purpose), int((row or {}).get('total') or 0),
             int((row or {}).get('reserved') or 0), int((row or {}).get('completed') or 0))
            for purpose, row in rows.items()))
        metric_sig = tuple(int(evaluator.get(name) or 0) for name in
                           ('submitted', 'completed', 'native', 'fallback', 'errors', 'timeouts',
                            'holdout', 'inflight', 'gatedWorkers'))
        question = report.get('question')
        question_id = question.get('id') if isinstance(question, dict) else question
        publication = progress.get('publication')
        if isinstance(publication, dict):
            publication = publication.get('status')
        confirmation = progress.get('confirmation')
        ready = bool(confirmation.get('ready')) if isinstance(confirmation, dict) else None
        return (state, error, bool(report.get('enabled')), report.get('sessionId'),
                int(progress.get('roundIndex') or 0), int(progress.get('experiments') or 0),
                progress.get('reserved'), progress.get('completed'), progress.get('queueLength'),
                bool(progress.get('idle')), bool(progress.get('blocked')),
                tuple(progress.get('unspendable') or ()),
                len(report.get('portfolio') or []), len(report.get('boundaries') or []),
                question_id, publication, ready, budget_sig, metric_sig,
                bool(report.get('migrationPreview')))

    @staticmethod
    def _encounter_request(value):
        value = dict(value) if isinstance(value, dict) else {}
        if 'referenceId' in value:
            if 'reference' in value and value['reference'] != value['referenceId']:
                raise ValueError('Conflicting reference identifiers')
            value['reference'] = value.pop('referenceId')
        return value

    def _encounter_snapshot(self, store):
        memo = {id(store.db): store.db}
        # `planning_host` is a back-reference to this Optimizer (set in `_loop` and the campaign
        # wiring). It is an external identity, not coordinator state: deep-copying it would walk the
        # whole Optimizer, whose `lock`/`commands` are unpicklable, so a rollback snapshot raised
        # `TypeError: cannot pickle '_thread.lock' object` and paused every activation. Keep it by
        # identity like the evaluator and the sqlite connection.
        for name in ('evaluator', '_db_ref', '_initialized_db', 'planning_host'):
            item = getattr(self._encounter, name, None)
            if item is not None:
                memo[id(item)] = item
        return deepcopy(self._encounter.__dict__, memo)

    def _rollback_encounter(self, store):
        """Disable the mode and revoke its grant together, preserving all measured evidence."""
        store.db.commit()
        snapshot = self._encounter_snapshot(store)
        compatible = store.compatible
        previous = deepcopy(self._encounter.previous)
        try:
            store.db.execute('BEGIN IMMEDIATE')
            result = self._encounter.rollback(store)
            if store.get(encounter_migration.KEY) is not None:
                encounter_migration.rollback(store, self.provenance)
            store.db.commit()
        except BaseException:
            store.db.rollback()
            self._encounter.__dict__.clear()
            self._encounter.__dict__.update(snapshot)
            store.compatible = compatible
            raise
        if previous.get('shares') is not None:
            self._student_shares = students.shares(previous['shares'])
        if previous.get('tracks') is not None:
            self._track_shares = students.track_shares(previous['tracks'])
        self._encounter_previous_shares = None
        self._encounter_previous_tracks = None
        self._encounter_preview_token = None
        self._encounter_session_base = None
        self._planner_dirty = True
        return result

    def _community_focus(self, store):
        """The persisted multi-select campaign focus, validated against the recovered catalogue."""
        catalogue = {int(key) for key in (self.encounters or {})}
        raw = store.get(COMMUNITY_FOCUS_KEY)
        if not isinstance(raw, (list, tuple)):
            return []
        selected = []
        for item in raw:
            try:
                encounter_id = int(item)
            except (TypeError, ValueError):
                continue
            if catalogue and encounter_id not in catalogue:
                continue
            if encounter_id not in selected:
                selected.append(encounter_id)
        return selected

    def _community_set_focus(self, store, selected):
        """Persist the campaign's multi-select focus. Scheduling only; never a budget/session."""
        selected = [int(value) for value in selected]
        store.set(COMMUNITY_FOCUS_KEY, selected)
        store.db.commit()
        # Keep the in-memory planning identity in step with the persisted focus, so a harvested
        # planner result computed under the previous focus rejects as stale at the next accept.
        self._planning_focus = list(selected)

    # -- shared asynchronous planning host --------------------------------------------------------
    # The Optimizer owns exactly ONE planner pool, shared by every encounter coordinator, and ONE
    # battle evaluator. Coordinators never construct or close a pool; they call back into these four
    # host methods. ``submit_planning``/``cancel_planning``/``planning_context``/``planning_allocation``
    # all run on the optimizer thread, so no pool state is shared across threads.
    def _planner_default_workers(self):
        """The joint service's bounded planner default (P), resolved by string."""
        import strategy_parallel_proposals as parallel_proposals
        try:
            default = int(getattr(parallel_proposals, 'DEFAULT_PLANNING_WORKERS', 2) or 2)
        except (TypeError, ValueError):
            default = 2
        try:
            ceiling = int(getattr(parallel_proposals, 'MAX_PLANNING_WORKERS', default) or default)
        except (TypeError, ValueError):
            ceiling = default
        return max(1, min(default, ceiling))

    def _planner_worker_count(self, total=None):
        """Effective planner workers P for a requested total T: P = min(default, T - 1) >= 0.

        ``0`` disables the shared planner (the coordinator keeps its unchanged synchronous planning
        path); there is no silent synchronous fallback inside the pool itself.
        """
        if total is None:
            total = getattr(self, '_workers', default_workers())
        try:
            total = max(1, int(total))
        except (TypeError, ValueError):
            total = 1
        return max(0, min(self._planner_default_workers(), total - 1))

    def _battle_worker_count(self, total=None):
        """Effective battle workers B = max(1, T - P). T = P + B, with duty unchanged."""
        if total is None:
            total = getattr(self, '_workers', default_workers())
        try:
            total = max(1, int(total))
        except (TypeError, ValueError):
            total = 1
        return max(1, total - self._planner_worker_count(total))

    def _planning_runtime_active(self):
        """Whether the encounter runtime (which uses the shared planner) is live in this process."""
        encounter = getattr(self, '_encounter', None)
        if encounter is not None and getattr(encounter, 'enabled', False):
            return True
        report = getattr(self, '_encounter_report', None)
        if isinstance(report, dict) and report.get('enabled'):
            return True
        return bool(getattr(self, '_campaign_started', False))

    def _status_worker_allocation(self):
        """Use the live owner's detached role split during async status projection."""
        captured = getattr(self, '_projection_worker_allocation', None)
        return (captured if isinstance(captured, dict)
                else self._worker_allocation_status())

    def _worker_allocation_status(self):
        """Current role allocation plus any explicit resize still draining accepted work."""
        try:
            effective_total = max(1, int(getattr(self, '_workers', default_workers()) or 1))
        except (TypeError, ValueError):
            effective_total = max(1, int(default_workers()))
        active = bool(self._planning_runtime_active())
        effective_planners = self._planner_worker_count(effective_total) if active else 0
        effective_battles = (self._battle_worker_count(effective_total)
                             if active else effective_total)
        pending = getattr(self, '_campaign_resize_pending', None)
        requested_total = effective_total
        requested_duty = getattr(self, '_duty', None)
        if isinstance(pending, dict):
            try:
                requested_total = max(1, int(pending.get('workers') or effective_total))
            except (TypeError, ValueError):
                requested_total = effective_total
            requested_duty = pending.get('duty', requested_duty)
        requested_planners = self._planner_worker_count(requested_total) if active else 0
        requested_battles = (self._battle_worker_count(requested_total)
                             if active else requested_total)
        evaluator = getattr(self, '_community_evaluator', None)
        evaluator_workers = (int(getattr(evaluator, 'workers', 0) or 0)
                             if evaluator is not None and not getattr(evaluator, 'closed', False)
                             else 0)
        pool_view = self._planning_pool_view()
        last = getattr(self, '_worker_resize_status', None)
        resize_state = ('draining' if isinstance(pending, dict) else
                        (last.get('state') if isinstance(last, dict) else 'idle'))
        pending_view = None
        if isinstance(pending, dict):
            pending_view = dict(state='draining', requestedWorkers=requested_total,
                                requestedDuty=requested_duty,
                                requestedPlannerWorkers=requested_planners,
                                requestedBattleWorkers=requested_battles)
            if isinstance(last, dict) and last.get('waitingFor') is not None:
                pending_view['waitingFor'] = dict(last['waitingFor'])
        last_view = (dict(last) if isinstance(last, dict) else dict(state='idle'))
        return dict(requestedWorkers=int(requested_total),
                    requestedPlannerWorkers=int(requested_planners),
                    requestedBattleWorkers=int(requested_battles),
                    effectiveWorkers=int(effective_total),
                    effectivePlannerWorkers=int(effective_planners),
                    effectiveBattleWorkers=int(effective_battles),
                    configuredBattlePoolWorkers=evaluator_workers,
                    configuredPlanningPoolWorkers=int(pool_view.get('workers') or 0),
                    requestedDuty=requested_duty,
                    effectiveDuty=getattr(self, '_duty', None),
                    resizeState=resize_state, pendingResize=pending_view,
                    lastResize=last_view,
                    allocationNote=('P + B equals the effective requested total while the shared '
                                    'Community planner is active. This split does not include the '
                                    'transient proposal-preparation child pool.'))

    def _reported_worker_split(self, total=None):
        """(planner, battle) for STATUS: legacy mode reports no planner and all battle workers."""
        if total is None:
            total = getattr(self, '_workers', default_workers())
        try:
            total = max(1, int(total))
        except (TypeError, ValueError):
            total = max(1, int(default_workers()))
        planners = self._planner_worker_count(total) if self._planning_runtime_active() else 0
        return int(planners), max(1, total - int(planners))

    def _planning_focus_ids(self):
        """The persisted multi-select campaign focus as validated encounter ids."""
        focus = getattr(self, '_planning_focus', None)
        if focus is None:
            return []
        out = []
        for value in focus:
            try:
                out.append(int(value))
            except (TypeError, ValueError):
                continue
        return out

    def _planning_live_context(self, coord):
        """Campaign/generation/focus identity stamped onto one planner request.

        These are the host-owned dimensions; the coordinator folds them into the acceptance envelope
        it re-checks when the result is harvested, so a request computed for another campaign,
        generation or focus can never admit work.
        """
        fleet = self._fleet_state()
        fleet = fleet if isinstance(fleet, dict) else {}
        encounter = getattr(coord, 'encounter', None)
        row = (fleet.get('encounters') or {}).get(str(encounter))
        row = row if isinstance(row, dict) else {}
        return dict(campaignId=fleet.get('campaignId'),
                    generation=int(row.get('generation') or 0),
                    focus=list(self._planning_focus_ids()))

    def _planning_scope_ids(self, coord=None):
        """The encounters sharing the planner pool right now (focus narrows this to one)."""
        coords = getattr(self, '_campaign_coordinators', None) or {}
        if coords:
            focus = [enc for enc in self._planning_focus_ids() if enc in coords]
            return focus or sorted(coords)
        single = getattr(self, '_encounter', None)
        if single is not None and getattr(single, 'enabled', False):
            return [getattr(single, 'encounter', None)]
        return [getattr(coord, 'encounter', None)] if coord is not None else []

    def planning_context(self, coord):
        """Host-owned identity a coordinator folds into its acceptance envelope."""
        return self._planning_live_context(coord)

    def planning_allocation(self, coord):
        """Requested/effective planner allocation plus the explicit total/battle split.

        In focus mode (the campaign focus narrows to a single encounter, or one activated study is
        running) the focused encounter may borrow the whole planner pool; otherwise the pool is
        divided fairly across the encounters in scope. The total/battle/planner fields are reported
        separately so the operator value is never silently re-labelled.
        """
        try:
            total = max(1, int(getattr(self, '_workers', default_workers()) or 1))
        except (TypeError, ValueError):
            total = max(1, int(default_workers()))
        planners = self._planner_worker_count(total)
        battles = self._battle_worker_count(total)
        scope = [enc for enc in self._planning_scope_ids(coord) if enc is not None]
        if len(scope) <= 1:
            effective = planners
        else:
            effective = max(1, int(math.ceil(planners / float(len(scope))))) if planners else 0
        return dict(requested=int(planners), effective=int(effective),
                    plannerWorkers=int(planners), battleRequested=int(total),
                    battleEffective=int(battles), focusMode=len(scope) <= 1,
                    pool=self._planning_pool_view())

    def _planning_pool_view(self):
        """Observer-only shared-pool snapshot; never a selection input."""
        pool = getattr(self, '_planning_pool', None)
        if pool is None or getattr(pool, 'closed', True):
            return dict(alive=False, workers=0, pending=0, running=0, finishedUnharvested=0)
        try:
            state = pool.status()
        except Exception:  # noqa: BLE001 - telemetry must never break a publish
            return dict(alive=True, workers=0, pending=0, running=0, finishedUnharvested=0)
        return dict(alive=True, workers=state.get('workers'), pending=state.get('pending'),
                    running=state.get('running'), outstanding=state.get('outstanding'),
                    finishedUnharvested=state.get('completedUnharvested'))

    def _ensure_planning_pool(self, total=None):
        """The single host-owned shared planner pool, created once at the effective planner size."""
        import strategy_parallel_proposals as parallel_proposals
        if total is None:
            total = getattr(self, '_workers', default_workers())
        try:
            total = max(1, int(total))
        except (TypeError, ValueError):
            total = max(1, int(default_workers()))
        workers = self._planner_worker_count(total)
        if workers < 1:
            return None
        pool = getattr(self, '_planning_pool', None)
        if pool is not None and not pool.closed and pool.workers == workers:
            return pool
        max_pending = int(getattr(parallel_proposals, 'DEFAULT_MAX_PENDING', 64) or 64)
        max_bytes = int(getattr(parallel_proposals, 'DEFAULT_MAX_PENDING_BYTES',
                                32 * 1024 * 1024) or (32 * 1024 * 1024))
        if pool is not None and not pool.closed:
            # A changed requested worker count is honoured by a bounded close and one recreate of
            # the single shared pool; coordinators never own or resize it.
            self._close_planning_pool()
        pool = parallel_proposals.get_planning_pool(
            workers=workers, max_pending=max_pending, max_pending_bytes=max_bytes)
        if pool.workers != workers:
            # A stale singleton created at another size: close it and create the sized pool once.
            parallel_proposals.close_planning_pool()
            pool = parallel_proposals.get_planning_pool(
                workers=workers, max_pending=max_pending, max_pending_bytes=max_bytes)
        self._planning_pool = pool
        return pool

    def _close_planning_pool(self, timeout=5.0):
        """Bounded cleanup of the shared planner service; reports surviving owned PIDs."""
        import strategy_parallel_proposals as parallel_proposals
        try:
            report = parallel_proposals.close_planning_pool(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - cleanup must never raise into the loop finally
            report = dict(closed=False, error='%s: %s' % (type(exc).__name__, exc))
        self._planning_pool = None
        self._planner_close_report = report
        return report

    @staticmethod
    def _planning_request_id(coord, context, envelope, sequence):
        """Stable, collision-free routing id over campaign/generation/session/encounter/focus/policy."""
        envelope = envelope if isinstance(envelope, dict) else {}
        scope = '%s|%s|%s|%s|%s|%s|%s' % (
            context.get('campaignId'), context.get('generation'),
            getattr(coord, 'encounter', None), getattr(coord, 'session_id', None),
            ','.join(str(value) for value in (context.get('focus') or [])),
            envelope.get('policy'), envelope.get('parentId'))
        digest = hashlib.sha256(('%s|%d' % (scope, sequence)).encode('utf-8')).hexdigest()
        return 'planning-%s' % digest[:32]

    @staticmethod
    def _repair_planning_request(request):
        """Pass a well-formed joint request through; repair the single-positional-payload shape.

        The coordinator's request factory may be the frozen dataclass itself, in which case a single
        positional payload binds to its first field instead of its named fields. Only that
        unmistakable shape (empty frozen scenario, payload mapping in ``requestId``) is rebuilt; any
        other request is handed to the worker unchanged.
        """
        try:
            import strategy_joint_proposals as joint
        except Exception:  # noqa: BLE001 - an absent joint service is the caller's fallback
            return request
        cls = getattr(joint, 'EncounterPlanningRequest', None)
        if cls is None or not isinstance(request, cls):
            return request
        if request.scenario:
            return request
        payload = getattr(request, 'requestId', None)
        if not isinstance(payload, dict) or 'scenario' not in payload:
            return request
        names = {'scenario': 'scenario', 'evidence': 'evidence', 'constraints': 'constraints',
                 'question': 'question', 'round_index': 'roundIndex', 'maximum': 'maximum',
                 'purpose': 'purpose'}
        fields = {field: payload[key] for key, field in names.items() if key in payload}
        try:
            return cls(**fields)
        except Exception:  # noqa: BLE001 - leave the original request for the worker to judge
            return request

    def submit_planning(self, coord, request, envelope):
        """Queue one logical proposal group's PHASE A on the shared pool; return its routing id.

        The pool only enqueues (never runs inline, never blocks). The logical id returned is the id
        the coordinator holds as pending, and it is the id of exactly one delivered result. ``None``
        (no pool, rejected capacity, or an unusable request) makes the coordinator report the round
        unplanned; it never falls back to a blocking synchronous build.
        """
        if coord is None:
            return None
        pool = self._ensure_planning_pool()
        if pool is None:
            return None
        request = self._repair_planning_request(request)
        context = self._planning_live_context(coord)
        self._planning_sequence = int(getattr(self, '_planning_sequence', 0) or 0) + 1
        request_id = self._planning_request_id(coord, context, envelope, self._planning_sequence)
        request_queued_at = time.monotonic()
        try:
            accepted = pool.submit(request_id, 'strategy_joint_proposals',
                                   'prepare_planning_task', (request,))
        except Exception as exc:  # noqa: BLE001 - a submission error degrades to serial planning
            self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
            return None
        if not accepted:
            try:
                self._planner_last_error = pool.status().get('lastError')
            except Exception:  # noqa: BLE001 - the rejection reason is optional telemetry
                self._planner_last_error = 'planner-submit-rejected'
            return None
        route = dict(
            requestId=request_id, coord=coord, encounter=getattr(coord, 'encounter', None),
            sessionId=getattr(coord, 'session_id', None), campaignId=context.get('campaignId'),
            generation=context.get('generation'), focus=list(context.get('focus') or []),
            envelope=envelope, requestCreatedAt=getattr(
                coord, '_planner_request_created_at', request_queued_at),
            requestQueuedAt=request_queued_at, submittedAt=time.monotonic(),
            phase='prepare', request=request,
            prepared=None, specs=[], chunkBounds=[], chunkTotal=0, chunkSubmitted=0,
            chunkCompleted=0, chunkFailed=0, inFlight={request_id}, results=[], empty=False,
            requestBytes=0, resultBytes=0, wallSeconds=0.0, cpuSeconds=0.0,
            finalizeSeconds=None, deliveredAt=None, taskCount=1, harvestedTasks=0,
            taskLifecycle=[])
        self._planner_routes[request_id] = route
        self._planner_chunk_map()
        self._planner_task_phase_map()[request_id] = 'prepare_planning_task'
        self._planner_phase_bump('prepare_planning_task', 'submitted', 1)
        self._planner_bump('_planner_submitted', 1)
        return request_id

    def _planning_route_current(self, route):
        """True while the route's coordinator still owns its encounter (not rolled over)."""
        coord = route.get('coord')
        encounter = route.get('encounter')
        coords = getattr(self, '_campaign_coordinators', None) or {}
        if coords:
            return coords.get(encounter) is coord
        return coord is getattr(self, '_encounter', None)

    def cancel_planning(self, coord):
        """Cancel every unstarted planner task this coordinator owns; drop its logical routes.

        Called by ``Coordinator.close``. A running chunk cannot be cancelled; its route is still
        dropped so the late result can never re-enter a closed coordinator.
        """
        pool = getattr(self, '_planning_pool', None)
        routes = getattr(self, '_planner_routes', None) or {}
        cancelled = 0
        for logical_id, route in list(routes.items()):
            if route.get('coord') is not coord:
                continue
            for task_id in list(route.get('inFlight') or ()):
                self._planner_chunk_map().pop(task_id, None)
                self._planner_task_phase_map().pop(task_id, None)
                if pool is not None and not getattr(pool, 'closed', True):
                    try:
                        if pool.cancel(task_id):
                            cancelled += 1
                    except Exception:  # noqa: BLE001 - best-effort cancellation
                        pass
            routes.pop(logical_id, None)
        self._planner_bump('_planner_cancelled', cancelled)
        return cancelled

    def _cancel_all_planning(self, store=None):
        """Cancel every queued planner job on stop/close and clear the owners' pending marker."""
        pool = getattr(self, '_planning_pool', None)
        routes = getattr(self, '_planner_routes', None) or {}
        cancelled = 0
        for logical_id, route in list(routes.items()):
            for task_id in list(route.get('inFlight') or ()):
                self._planner_chunk_map().pop(task_id, None)
                self._planner_task_phase_map().pop(task_id, None)
                if pool is not None and not getattr(pool, 'closed', True):
                    try:
                        if pool.cancel(task_id):
                            cancelled += 1
                    except Exception:  # noqa: BLE001 - best-effort cancellation
                        pass
            if store is not None:
                coord = route.get('coord')
                pending = getattr(coord, '_planner_pending', None) if coord is not None else None
                if isinstance(pending, dict) and pending.get('requestId') == logical_id:
                    # A cancelled-but-running job never returns a record. Clearing the coordinator's
                    # own marker through its acceptance gate keeps it from freezing on a pending
                    # request that will never arrive.
                    try:
                        coord.accept_planning_result(
                            dict(requestId=logical_id, result=None,
                                 error='planning-cancelled-by-host'), store)
                    except Exception:  # noqa: BLE001 - cancellation must never mask the stop
                        pass
            routes.pop(logical_id, None)
        self._planner_bump('_planner_cancelled', cancelled)
        return cancelled

    # -- bounded non-blocking phase state machine --------------------------------------------------
    def _planner_bump(self, name, delta=1):
        """Bump a host counter that may not have been initialised on a partial host stand-in."""
        self.__dict__[name] = int(getattr(self, name, 0) or 0) + int(delta)

    def _planner_chunk_map(self):
        value = self.__dict__.get('_planner_chunk_tasks')
        if not isinstance(value, dict):
            value = self.__dict__['_planner_chunk_tasks'] = {}
        return value

    def _planner_task_phase_map(self):
        value = self.__dict__.get('_planner_task_phase')
        if not isinstance(value, dict):
            value = self.__dict__['_planner_task_phase'] = {}
        return value

    def _planner_phase_bump(self, phase, field, delta):
        table = self.__dict__.get('_planner_phase_counts')
        if not isinstance(table, dict):
            table = self.__dict__['_planner_phase_counts'] = {}
        row = table.get(phase)
        if not isinstance(row, dict):
            row = table[phase] = {}
        if field.endswith('Seconds'):
            row[field] = float(row.get(field) or 0.0) + float(delta)
        else:
            row[field] = int(row.get(field) or 0) + int(delta)
        return row

    @staticmethod
    def _planning_chunk_bounds(spec_count, workers):
        """Contiguous index ranges for one prepared window: at most 2P chunks, at least one.

        A focused group still owns at least P chunks, so every planner lane can run an independent
        spec chunk; a window with fewer than two specs yields a single chunk, reported by the group
        status as the ``independentChunks < 2`` limitation.
        """
        spec_count = max(0, int(spec_count))
        if spec_count <= 0:
            return []
        workers = max(1, int(workers or 1))
        # At least 2P chunks fill every lane and make the bounded queue; a large window splits
        # further (about 24 specs per chunk) so the rest genuinely streams as lanes free up.
        target_chunks = max(2 * workers, -(-spec_count // 24))
        preferred = min(spec_count, target_chunks)
        chunk_size = max(1, -(-spec_count // preferred))
        bounds = []
        start = 0
        while start < spec_count:
            end = min(spec_count, start + chunk_size)
            bounds.append((start, end))
            start = end
        return bounds

    def _empty_planning_envelope(self, request):
        """The unchanged synchronous empty envelope for a legitimately empty group (never an error)."""
        try:
            import strategy_joint_proposals as joint
            meta = joint._request_meta(request)
            return joint._envelope(meta, 0, getattr(request, 'parentId', None),
                                   dict(workers=0, reason='no-prepared-plan', specs=0), [])
        except Exception as exc:  # noqa: BLE001 - reported, never a silent serial fallback
            self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
            return None

    def _planning_route_context_current(self, route):
        """True while the live host identity still matches the group's frozen submission scope.

        A stale focus, campaign, generation, session, replaced coordinator, or a coordinator whose
        frozen policy/compatibility envelope no longer matches its live pending plan makes every
        remaining unstarted chunk of that group out of scope, so it is cancelled and never admitted.
        """
        coord = route.get('coord')
        if coord is None or not self._planning_route_current(route):
            return False
        try:
            live = self._planning_live_context(coord)
        except Exception:  # noqa: BLE001 - an unreadable context is stale by definition
            return False
        if route.get('campaignId') != live.get('campaignId'):
            return False
        if route.get('generation') != live.get('generation'):
            return False
        if list(route.get('focus') or []) != list(live.get('focus') or []):
            return False
        if route.get('sessionId') != getattr(coord, 'session_id', None):
            return False
        if route.get('encounter') != getattr(coord, 'encounter', None):
            return False
        return self._planning_route_policy_current(route, coord)

    def _planning_route_policy_current(self, route, coord):
        """True unless the coordinator's live policy/compatibility has drifted from the frozen group.

        The host scope fields checked above only cover campaign/generation/focus/session/encounter.
        The coordinator freezes the fuller acceptance envelope (library, policy, engine, mechanics,
        compatibility, parent) and re-checks it inside ``accept_planning_result``; reuse that exact
        live check here against the coordinator's current pending plan, so a group whose policy or
        compatibility went stale is dropped before any more of its chunks are submitted.

        Submission assigns ``_planner_pending`` only AFTER ``submit_planning`` returns (which is what
        registers this route), and a coordinator never submits a second group while one is pending.
        So when the pending request id does not match this route there is no current plan to compare
        (the marker is not published yet, or has already cleared); leave the route to the
        coordinator's own acceptance gate rather than falsely discarding it.
        """
        pending = getattr(coord, '_planner_pending', None)
        if not isinstance(pending, dict):
            return True
        if pending.get('requestId') != route.get('requestId'):
            return True
        compatible = getattr(coord, '_planner_compatible', None)
        plan = pending.get('plan')
        envelope = pending.get('envelope')
        if plan is None or envelope is None or not callable(compatible):
            return True
        try:
            return bool(compatible(envelope, plan))
        except Exception:  # noqa: BLE001 - an unreadable compatibility check is stale
            return False

    def _record_planning_task_telemetry(self, route, record, phase):
        """Accumulate one harvested pool record's bytes, worker wall/CPU and counters onto a group."""
        request_bytes = int(record.get('requestBytes') or 0)
        result_bytes = int(record.get('resultBytes') or 0)
        route['requestBytes'] = int(route.get('requestBytes') or 0) + request_bytes
        route['resultBytes'] = int(route.get('resultBytes') or 0) + result_bytes
        started, finished = record.get('startedAt'), record.get('finishedAt')
        wall = (max(0.0, float(finished) - float(started))
                if isinstance(started, (int, float)) and isinstance(finished, (int, float)) else 0.0)
        cpu = record.get('workerCpuSeconds')
        cpu = float(cpu) if isinstance(cpu, (int, float)) else 0.0
        route['wallSeconds'] = float(route.get('wallSeconds') or 0.0) + wall
        route['cpuSeconds'] = float(route.get('cpuSeconds') or 0.0) + cpu
        route['harvestedTasks'] = int(route.get('harvestedTasks') or 0) + 1
        self._planner_phase_bump(phase, 'harvested', 1)
        self._planner_phase_bump(phase, 'requestBytes', request_bytes)
        self._planner_phase_bump(phase, 'resultBytes', result_bytes)
        self._planner_phase_bump(phase, 'wallSeconds', wall)
        self._planner_phase_bump(phase, 'cpuSeconds', cpu)
        self._planner_bump('_planner_harvested', 1)

    def _finalize_and_deliver(self, logical_id, route, store, indexed_results):
        """Phase C on the optimizer thread: deterministic finalize, then exactly one delivery."""
        import strategy_joint_proposals as joint
        started = time.perf_counter()
        try:
            envelope = joint.finalize_planning_task(route.get('prepared'), indexed_results)
        except Exception as exc:  # noqa: BLE001 - a failed finalize is reported, never simulated
            self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
            self._deliver_planning(logical_id, route, None,
                                   'planning-finalize-failed: %s' % (exc,), store)
            return
        route['finalizeSeconds'] = max(0.0, time.perf_counter() - started)
        self._planner_phase_bump('finalize_planning_task', 'submitted', 1)
        self._planner_phase_bump('finalize_planning_task', 'harvested', 1)
        route['phase'] = 'finalize'
        self._deliver_planning(logical_id, route, envelope, None, store)

    def _deliver_planning(self, logical_id, route, envelope, error, store):
        """Deliver exactly one ``{requestId, result, error}`` for a group, then forget the route."""
        self._planner_routes.pop(logical_id, None)
        for task_id in list(route.get('inFlight') or ()):
            self._planner_chunk_map().pop(task_id, None)
            self._planner_task_phase_map().pop(task_id, None)
        route['inFlight'] = set()
        route['deliveredAt'] = time.monotonic()
        route['harvestEndAt'] = route['deliveredAt']
        submitted_at = route.get('submittedAt')
        if submitted_at is not None:
            latency_log = self.__dict__.get('_planner_latency')
            if latency_log is None:
                latency_log = self._planner_latency = []
            try:
                latency_log.append(max(0.0, route['deliveredAt'] - submitted_at))
            except Exception:  # noqa: BLE001 - telemetry must never break the loop
                pass
        route['phase'] = 'finalized' if error is None else 'discarded'
        coord = route.get('coord')
        if error is None and (coord is None or not self._planning_route_current(route)):
            self._planner_bump('_planner_unrouted', 1)
            return
        accepted = False
        route['resultConsumedAt'] = time.monotonic()
        if coord is not None:
            try:
                accepted = bool(coord.accept_planning_result(
                    dict(requestId=logical_id, result=envelope, error=error), store))
            except Exception as exc:  # noqa: BLE001 - one encounter never stops the loop
                self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
                accepted = False
        route['consumptionEndedAt'] = time.monotonic()
        lifecycle = {
            'requestId': logical_id,
            'encounter': route.get('encounter'),
            'requestCreatedAt': route.get('requestCreatedAt'),
            'requestQueuedAt': route.get('requestQueuedAt'),
            'workerTasks': list(route.get('taskLifecycle') or ()),
            'futureReadyAt': route.get('latestFutureReadyAt'),
            'hostHarvestStartAt': route.get('latestReadyHarvestStartAt'),
            'harvestEndAt': route.get('harvestEndAt'),
            'resultConsumedAt': route.get('resultConsumedAt'),
            'consumptionEndedAt': route.get('consumptionEndedAt'),
            'frozenPlanAvailableAt': (getattr(coord, '_last_frozen_plan_available_at', None)
                                      if accepted and coord is not None else None),
            'accepted': bool(accepted),
        }
        events = self.__dict__.setdefault('_planner_lifecycle_events', [])
        events.append(lifecycle)
        if len(events) > 4096:
            del events[:-4096]
        if accepted:
            self._planner_bump('_planner_accepted', 1)
        else:
            self._planner_bump('_planner_rejected', 1)

    def _planning_cancel_group(self, logical_id, reason, store=None):
        """Cancel a group's unstarted chunks and deliver one cancellation so its owner unblocks."""
        route = self._planner_routes.get(logical_id)
        if route is None:
            return 0
        pool = getattr(self, '_planning_pool', None)
        cancelled = 0
        for task_id in list(route.get('inFlight') or ()):
            self._planner_chunk_map().pop(task_id, None)
            self._planner_task_phase_map().pop(task_id, None)
            if pool is not None and not getattr(pool, 'closed', True):
                try:
                    if pool.cancel(task_id):
                        cancelled += 1
                except Exception:  # noqa: BLE001 - best-effort cancellation
                    pass
        route['inFlight'] = set()
        self._planner_bump('_planner_cancelled', cancelled)
        self._deliver_planning(logical_id, route, None, reason, store)
        return cancelled

    def _harvest_planning_prepare(self, logical_id, route, record, store):
        """Harvest PHASE A: transition to compute (or finalize a legitimately empty group)."""
        self._planner_task_phase_map().pop(logical_id, None)
        route.get('inFlight', set()).discard(logical_id)
        self._record_planning_task_telemetry(route, record, 'prepare_planning_task')
        if not self._planning_route_context_current(route):
            self._planning_cancel_group(logical_id, 'planning-discarded-stale-scope', store)
            return
        if record.get('error') is not None:
            self._deliver_planning(logical_id, route, None,
                                   'planning-prepare-failed: %s' % (record.get('error'),), store)
            return
        prepared = record.get('result')
        if prepared is None:
            envelope = self._empty_planning_envelope(route.get('request'))
            if envelope is None:
                self._deliver_planning(logical_id, route, None,
                                       'planning-empty-envelope-unavailable', store)
            else:
                route['empty'] = True
                route['prepared'] = None
                self._deliver_planning(logical_id, route, envelope, None, store)
            return
        if not isinstance(prepared, dict):
            self._deliver_planning(logical_id, route, None,
                                   'planning-prepare-malformed-payload', store)
            return
        window = prepared.get('window') or []
        route['prepared'] = prepared
        if not window:
            # A valid prepared payload with no specs finalizes to the empty envelope, not an error.
            route['empty'] = True
            self._finalize_and_deliver(logical_id, route, store, [])
            return
        route['specs'] = [[index, spec] for index, spec in enumerate(window)]
        workers = max(1, int(getattr(self._planning_pool, 'workers', 1) or 1))
        route['chunkBounds'] = self._planning_chunk_bounds(len(route['specs']), workers)
        route['chunkTotal'] = len(route['chunkBounds'])
        self._planner_bump('_planner_chunks_total', route['chunkTotal'])
        route['phase'] = 'compute'

    def _harvest_planning_chunk(self, logical_id, chunk_index, record, store):
        """Harvest one PHASE B chunk: accumulate it, then finalize once every chunk is in."""
        route = self._planner_routes.get(logical_id)
        task_id = record.get('requestId')
        if route is None:
            self._planner_task_phase_map().pop(task_id, None)
            self._planner_bump('_planner_unrouted', 1)
            return
        route.get('inFlight', set()).discard(task_id)
        self._planner_task_phase_map().pop(task_id, None)
        self._record_planning_task_telemetry(route, record, 'compute_planning_chunk')
        if not self._planning_route_context_current(route):
            self._planning_cancel_group(logical_id, 'planning-discarded-stale-scope', store)
            return
        if record.get('error') is not None:
            route['chunkFailed'] = int(route.get('chunkFailed') or 0) + 1
            self._deliver_planning(logical_id, route, None,
                                   'planning-chunk-failed: %s' % (record.get('error'),), store)
            return
        route.setdefault('results', []).append(record.get('result'))
        route['chunkCompleted'] = int(route.get('chunkCompleted') or 0) + 1
        self._planner_bump('_planner_chunks_completed', 1)
        if (int(route.get('chunkCompleted') or 0) + int(route.get('chunkFailed') or 0)
                >= int(route.get('chunkTotal') or 0)):
            self._finalize_and_deliver(logical_id, route, store, route.get('results') or [])

    def _planning_fanout(self, store=None):
        """Stream bounded PHASE B chunks into free lanes: at most 2P tasks, round-robin groups.

        Never submits the whole prepared window at once and never waits on another encounter: the
        round-robin cursor keeps every ready group fed while the shared queue stays small, so one
        focused encounter can fill every planner lane and many encounters still share them fairly.
        """
        routes = getattr(self, '_planner_routes', None) or {}
        if not routes:
            return 0
        pool = getattr(self, '_planning_pool', None)
        if pool is None or getattr(pool, 'closed', True):
            return 0
        workers = max(1, int(getattr(pool, 'workers', 1) or 1))
        target = 2 * workers
        chunk_map = self._planner_chunk_map()
        task_phase = self._planner_task_phase_map()
        ready = sorted(logical_id for logical_id, route in routes.items()
                       if route.get('phase') == 'compute'
                       and int(route.get('chunkSubmitted') or 0) < int(route.get('chunkTotal') or 0))
        if not ready:
            return 0
        submitted = 0
        cursor = int(getattr(self, '_planner_group_cursor', 0) or 0)
        while ready and len(chunk_map) < target:
            n = len(ready)
            start = cursor % n
            order = ready[start:] + ready[:start]
            progressed = False
            for logical_id in order:
                if len(chunk_map) >= target:
                    break
                route = routes.get(logical_id)
                if (route is None or route.get('phase') != 'compute'
                        or int(route.get('chunkSubmitted') or 0)
                        >= int(route.get('chunkTotal') or 0)):
                    if logical_id in ready:
                        ready.remove(logical_id)
                    continue
                if not self._planning_route_context_current(route):
                    self._planning_cancel_group(logical_id, 'planning-discarded-stale-scope', store)
                    if logical_id in ready:
                        ready.remove(logical_id)
                    continue
                nxt = int(route.get('chunkSubmitted') or 0)
                start_index, end_index = route['chunkBounds'][nxt]
                chunk_specs = route['specs'][start_index:end_index]
                chunk_id = '%s#c%d' % (logical_id, nxt)
                try:
                    accepted = pool.submit(chunk_id, 'strategy_joint_proposals',
                                           'compute_planning_chunk',
                                           (route.get('prepared'), chunk_specs))
                except Exception as exc:  # noqa: BLE001 - a submit error is reported, not hidden
                    self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
                    accepted = False
                if not accepted:
                    try:
                        self._planner_last_error = pool.status().get('lastError')
                    except Exception:  # noqa: BLE001 - rejection reason is optional telemetry
                        pass
                    self._planning_cancel_group(logical_id, 'planning-chunk-submit-rejected', store)
                    if logical_id in ready:
                        ready.remove(logical_id)
                    continue
                route['chunkSubmitted'] = nxt + 1
                route.setdefault('inFlight', set()).add(chunk_id)
                route['taskCount'] = int(route.get('taskCount') or 0) + 1
                chunk_map[chunk_id] = dict(logicalId=logical_id, chunkIndex=nxt)
                task_phase[chunk_id] = 'compute_planning_chunk'
                self._planner_phase_bump('compute_planning_chunk', 'submitted', 1)
                self._planner_bump('_planner_fanout_submitted', 1)
                submitted += 1
                progressed = True
                cursor += 1
            if not progressed:
                break
        self._planner_group_cursor = cursor
        return submitted

    def _harvest_planning(self, store, *, eligible_coordinators=None):
        """One optimizer-thread slice: harvest finished pool records, then stream the fan-out.

        Every harvested record is routed at most once; Phase A transitions the group to compute,
        each Phase B chunk accumulates, and the group is finalized (Phase C, inline on this thread)
        and delivered exactly once when all chunks are in. The fan-out at the end keeps the shared
        queue bounded to <= 2P planner tasks, so dispatch and commands below run between slices.
        """
        pool = getattr(self, '_planning_pool', None)
        if pool is None or getattr(pool, 'closed', True):
            return 0
        try:
            records = list(self.__dict__.get('_planner_deferred_records') or ())
            records.extend(pool.harvest_ready())
        except Exception as exc:  # noqa: BLE001 - a harvest error must not stop the loop
            self._planner_last_error = '%s: %s' % (type(exc).__name__, exc)
            return 0
        eligible_ids = (None if eligible_coordinators is None else
                        {id(coord) for coord in eligible_coordinators if coord is not None})
        deferred = []
        delivered_before = int(getattr(self, '_planner_accepted', 0) or 0) + int(
            getattr(self, '_planner_rejected', 0) or 0)
        if records:
            routes = getattr(self, '_planner_routes', None)
            if routes is None:
                routes = self._planner_routes = {}
            chunk_map = self._planner_chunk_map()
            for record in records:
                task_id = record.get('requestId')
                info = chunk_map.get(task_id)
                logical_id = info.get('logicalId') if info is not None else task_id
                route = routes.get(logical_id)
                if (eligible_ids is not None and route is not None
                        and id(route.get('coord')) not in eligible_ids):
                    deferred.append(record)
                    continue
                record['hostHarvestStartAt'] = time.time()
                record['hostHarvestStartMonotonicAt'] = time.monotonic()
                phase = self._planner_task_phase_map().get(task_id)
                task_sample = None
                if route is not None:
                    task_sample = dict(
                        requestId=task_id, phase=phase,
                        queuedAt=record.get('queuedMonotonicAt'),
                        workerStartAt=record.get('startedAt'),
                        laneStartAt=record.get('laneStartedMonotonicAt'),
                        workerEndAt=record.get('finishedAt'),
                        futureReadyAt=record.get('futureReadyMonotonicAt'),
                        futureReadyWallAt=record.get('futureReadyAt'),
                        hostNoticedAt=record.get('hostNoticedMonotonicAt'),
                        hostHarvestStartAt=record.get('hostHarvestStartMonotonicAt'))
                    route.setdefault('taskLifecycle', []).append(task_sample)
                    ready_at = record.get('futureReadyMonotonicAt')
                    if isinstance(ready_at, (int, float)) and ready_at >= float(
                            route.get('latestFutureReadyAt') or 0.0):
                        route['latestFutureReadyAt'] = float(ready_at)
                        route['latestReadyHarvestStartAt'] = float(
                            record['hostHarvestStartMonotonicAt'])
                    samples = self.__dict__.setdefault('_planner_lifecycle_samples', [])
                    samples.append(dict(taskId=task_id, logicalRequestId=logical_id,
                                        encounter=route.get('encounter'), **task_sample))
                    if len(samples) > 8192:
                        del samples[:-8192]
                info = chunk_map.pop(task_id, None)
                if info is not None:
                    self._harvest_planning_chunk(info.get('logicalId'), info.get('chunkIndex'),
                                                 record, store)
                    continue
                route = routes.get(task_id)
                if route is None:
                    self._planner_bump('_planner_unrouted', 1)
                    continue
                self._harvest_planning_prepare(task_id, route, record, store)
        self.__dict__['_planner_deferred_records'] = deferred
        self._planning_fanout(store)
        delivered_after = int(getattr(self, '_planner_accepted', 0) or 0) + int(
            getattr(self, '_planner_rejected', 0) or 0)
        return max(0, delivered_after - delivered_before)

    def _planning_pool_task_states(self):
        """Non-blocking pool task-state snapshot for telemetry: pending/running/finished."""
        pool = getattr(self, '_planning_pool', None)
        if pool is None or getattr(pool, 'closed', True):
            return {}
        requests = getattr(pool, '_requests', None)
        if not isinstance(requests, dict):
            return {}
        results = getattr(pool, '_results', None)
        cond = getattr(pool, '_cond', None)
        acquired = False
        if cond is not None:
            try:
                acquired = bool(cond.acquire(blocking=False))
            except Exception:  # noqa: BLE001 - telemetry must never block the loop
                acquired = False
            if not acquired:
                return {}
        states = {}
        try:
            for task_id, request in requests.items():
                states[task_id] = request.get('state') or 'pending'
            if results is not None:
                for record in list(results):
                    if isinstance(record, dict) and record.get('requestId') is not None:
                        states[record['requestId']] = 'finished'
        except Exception:  # noqa: BLE001 - a torn snapshot is simply incomplete telemetry
            pass
        finally:
            if cond is not None and acquired:
                try:
                    cond.release()
                except Exception:  # noqa: BLE001
                    pass
        return states

    def _planning_group_status(self, routes, states):
        """Per logical group telemetry: phase, chunk counts, bytes, wall/CPU and end-to-end latency."""
        now = time.monotonic()
        groups = []
        for logical_id in list(sorted(routes)):
            route = routes.get(logical_id) or {}
            submitted_at = route.get('submittedAt')
            delivered_at = route.get('deliveredAt')
            spec_count = len(route.get('specs') or ())
            chunk_total = int(route.get('chunkTotal') or 0)
            try:
                in_flight = list(route.get('inFlight') or ())
            except Exception:  # noqa: BLE001 - a status poll must not race a live fan-out
                in_flight = []
            task_running = sum(1 for task_id in in_flight if states.get(task_id) == 'running')
            task_pending = sum(1 for task_id in in_flight if states.get(task_id) == 'pending')
            task_finished = sum(1 for task_id in in_flight if states.get(task_id) == 'finished')
            if route.get('empty') or not route.get('specs'):
                limitation = None
            elif spec_count < 2:
                limitation = 'fewer-than-two-independent-specs'
            elif chunk_total < 2:
                limitation = 'single-chunk'
            else:
                limitation = None
            if submitted_at is None:
                end_to_end = None
            else:
                anchor = delivered_at if delivered_at is not None else now
                end_to_end = max(0.0, anchor - submitted_at)
            groups.append(dict(
                requestId=logical_id, encounter=route.get('encounter'),
                sessionId=route.get('sessionId'), campaignId=route.get('campaignId'),
                generation=route.get('generation'), phase=route.get('phase'),
                empty=bool(route.get('empty')),
                specCount=spec_count, limitation=limitation,
                chunksTotal=chunk_total,
                chunksSubmitted=int(route.get('chunkSubmitted') or 0),
                chunksCompleted=int(route.get('chunkCompleted') or 0),
                chunksInFlight=len(route.get('inFlight') or ()),
                chunksFailed=int(route.get('chunkFailed') or 0),
                independentChunks=chunk_total,
                tasksSubmitted=int(route.get('taskCount') or 0),
                tasksHarvested=int(route.get('harvestedTasks') or 0),
                tasksRunning=task_running, tasksPending=task_pending,
                tasksFinishedUnharvested=task_finished,
                requestBytes=int(route.get('requestBytes') or 0),
                resultBytes=int(route.get('resultBytes') or 0),
                plannerWallSeconds=round(float(route.get('wallSeconds') or 0.0), 6),
                plannerCpuSeconds=round(float(route.get('cpuSeconds') or 0.0), 6),
                finalizeSeconds=(round(float(route['finalizeSeconds']), 6)
                                 if route.get('finalizeSeconds') is not None else None),
                endToEndLatencySeconds=(round(end_to_end, 6) if end_to_end is not None else None)))
        return groups

    def _planning_phase_status(self, routes, states):
        """Per phase telemetry; 'running' counts only tasks a planner process is executing now."""
        task_phase = getattr(self, '_planner_task_phase', None) or {}
        table = getattr(self, '_planner_phase_counts', None) or {}
        try:
            task_phase_items = list(task_phase.items())
        except Exception:  # noqa: BLE001 - a status poll must not race a live fan-out
            task_phase_items = []
        names = ('prepare_planning_task', 'compute_planning_chunk', 'finalize_planning_task')
        out = {}
        for name in names:
            row = table.get(name) if isinstance(table.get(name), dict) else {}
            running = pending = finished_unharvested = 0
            for task_id, phase in task_phase_items:
                if phase != name:
                    continue
                state = states.get(task_id)
                if state == 'running':
                    running += 1
                elif state == 'pending':
                    pending += 1
                elif state == 'finished':
                    finished_unharvested += 1
            out[name] = dict(
                submitted=int(row.get('submitted') or 0), harvested=int(row.get('harvested') or 0),
                running=running, pending=pending, finishedUnharvested=finished_unharvested,
                chunksCompleted=int(row.get('harvested') or 0),
                chunksTotal=int(row.get('submitted') or 0),
                requestBytes=int(row.get('requestBytes') or 0),
                resultBytes=int(row.get('resultBytes') or 0),
                plannerWallSeconds=round(float(row.get('wallSeconds') or 0.0), 6),
                plannerCpuSeconds=round(float(row.get('cpuSeconds') or 0.0), 6))
        return out

    def _planning_status(self):
        """Observer-only snapshot of the shared planning host; never a selection input."""
        allocation = self._worker_allocation_status()
        total = allocation['effectiveWorkers']
        planners = allocation['effectivePlannerWorkers']
        battles = allocation['effectiveBattleWorkers']
        try:
            latencies = list(getattr(self, '_planner_latency', ()) or ())
        except Exception:  # noqa: BLE001 - a status poll must never raise on a racing append
            latencies = []
        latency = (sum(latencies)/len(latencies)) if latencies else None
        routes = getattr(self, '_planner_routes', None) or {}
        pool_view = self._planning_pool_view()
        states = self._planning_pool_task_states()
        return dict(hostOwned=True,
                    workerAllocation=allocation,
                    requestedWorkers=allocation['requestedWorkers'],
                    requestedPlannerWorkers=allocation['requestedPlannerWorkers'],
                    requestedBattleWorkers=allocation['requestedBattleWorkers'],
                    effectiveTotalWorkers=total,
                    effectiveBattleWorkers=int(battles), plannerWorkers=int(planners),
                    effectivePlannerWorkers=int(planners),
                    configuredBattlePoolWorkers=allocation['configuredBattlePoolWorkers'],
                    configuredPlanningPoolWorkers=allocation['configuredPlanningPoolWorkers'],
                    pendingResize=allocation['pendingResize'],
                    resizeState=allocation['resizeState'],
                    active=bool(self._planning_runtime_active()),
                    configuredBattleWorkers=int(battles),
                    configuredPlannerWorkers=int(planners),
                    duty=getattr(self, '_duty', None),
                    submitted=int(getattr(self, '_planner_submitted', 0) or 0),
                    harvested=int(getattr(self, '_planner_harvested', 0) or 0),
                    accepted=int(getattr(self, '_planner_accepted', 0) or 0),
                    rejected=int(getattr(self, '_planner_rejected', 0) or 0),
                    cancelled=int(getattr(self, '_planner_cancelled', 0) or 0),
                    unrouted=int(getattr(self, '_planner_unrouted', 0) or 0),
                    outstanding=len(routes),
                    chunksCompleted=int(getattr(self, '_planner_chunks_completed', 0) or 0),
                    chunksTotal=int(getattr(self, '_planner_chunks_total', 0) or 0),
                    fanoutSubmitted=int(getattr(self, '_planner_fanout_submitted', 0) or 0),
                    finishedUnharvested=pool_view.get('finishedUnharvested'),
                    latencySeconds=latency, lastError=getattr(self, '_planner_last_error', None),
                    groups=self._planning_group_status(routes, states),
                    phases=self._planning_phase_status(routes, states),
                    pool=pool_view, closeReport=getattr(self, '_planner_close_report', None),
                    note='effectiveBattleWorkers + effectivePlannerWorkers equals '
                         'effectiveTotalWorkers while the shared Community planner is active. '
                         'A draining explicit resize reports its requested split separately. '
                         'This role split excludes transient proposal-preparation child workers. '
                         'Duty applies to the effective battle pool. '
                         'phases.*.running counts only planner tasks actually executing; '
                         'finishedUnharvested is finished work not yet harvested, never executing.')

    def _campaign_resize_scope_matches(self, store):
        """True only when a role request preserves the active fleet's exact encounter set."""
        if not getattr(self, '_campaign_started', False):
            return False
        coordinators = getattr(self, '_campaign_coordinators', None) or {}
        current_ids = sorted(int(encounter) for encounter in coordinators)
        if not current_ids:
            return False
        focus = self._community_focus(store)
        requested_ids = sorted(int(encounter) for encounter in
                               (focus or sorted((int(key) for key in (self.encounters or {})))))
        return requested_ids == current_ids

    def _campaign_start(self, store, value):
        """Load or activate independent encounter coordinators for one shared worker pool."""
        if self._encounter is None:
            raise ValueError('This library has no Community campaign runtime yet.')
        from strategy_optimizer_limits import clamp_workers
        value = value if isinstance(value, dict) else {}
        workers = clamp_workers(value.get('workers', getattr(self, '_workers', default_workers())))
        duty = clamp_duty(value.get('duty', getattr(self, '_duty', 1.0)))
        focus = self._community_focus(store)
        ids = list(focus or sorted((int(key) for key in (self.encounters or {}))))
        if not ids:
            raise ValueError('The recovered encounter catalogue lists no enabled encounters.')
        ids = sorted(int(encounter) for encounter in ids)
        coordinators = getattr(self, '_campaign_coordinators', None) or {}
        current_ids = sorted(int(encounter) for encounter in coordinators)
        current_workers = max(1, int(getattr(self, '_workers', default_workers()) or 1))
        current_duty = getattr(self, '_duty', 1.0)
        resize_pending = getattr(self, '_campaign_resize_pending', None)

        # Repeated Start/Resume with the same scope must not replace live coordinators or cancel
        # accepted planner routes. A changed explicit worker request is staged and drained below.
        if getattr(self, '_campaign_started', False) and current_ids:
            if (isinstance(resize_pending, dict) or workers != current_workers
                    or duty != current_duty) and ids != current_ids:
                raise ValueError('Change campaign focus separately from worker settings; active '
                                 'accepted work must drain before the worker split can change.')
            if ids == current_ids:
                if workers == current_workers and duty == current_duty:
                    if isinstance(resize_pending, dict):
                        self._campaign_resize_pending = None
                        self._worker_resize_status = dict(
                            state='cancelled', requestedWorkers=workers, requestedDuty=duty,
                            completedAt=time.time(), reason='request-matches-effective-allocation')
                    return self._campaign_resume_existing(store)
                self._campaign_resize_pending = dict(workers=workers, duty=duty,
                                                     requestedAt=time.time())
                self._worker_resize_status = dict(
                    state='draining', requestedWorkers=workers, requestedDuty=duty,
                    requestedAt=self._campaign_resize_pending['requestedAt'], waitingFor=None)
                self._campaign_report = self._campaign_resume_existing(store)
                self._scheduler['workerAllocation'] = self._worker_allocation_status()
                return self._campaign_report

        # A real focus/generation replacement still uses the established route-release path. A
        # role-only change never reaches it: accepted battle and planner work drains in place.
        self._cancel_all_planning(store)
        self._workers, self._duty = workers, duty
        # The requested total is split once: effective battle workers plus the shared planner pool.
        battle_workers = self._battle_worker_count(workers)
        self._planning_focus = [int(encounter) for encounter in focus]
        fleet = store.get('communityCampaignFleet')
        if not isinstance(fleet, dict) or fleet.get('version') != 1:
            nonce = '%x' % time.time_ns()
            campaign_id = 'ccf-' + hashlib.sha256((str(self.path)+nonce).encode()).hexdigest()[:16]
            fleet = dict(version=1, campaignId=campaign_id, encounterIds=ids,
                         enabledEncounterIds=ids, status='active', encounters={})
        else:
            fleet['enabledEncounterIds'] = ids
            fleet['encounterIds'] = sorted(set(fleet.get('encounterIds') or []) | set(ids))
            fleet['status'] = 'active'
        self._community_fleet_state = fleet
        coordinators = {}
        for encounter_id in ids:
            key = str(encounter_id)
            row = fleet.setdefault('encounters', {}).get(key)
            if not isinstance(row, dict):
                row = dict(encounterId=encounter_id, generation=0)
            generation = int(row.get('generation') or 0)
            runtime_key = row.get('runtimeKey') or (
                'encounterRuntime:campaign:%s:%d:%d' %
                (fleet['campaignId'], encounter_id, generation))
            campaign_scope = row.get('campaignScope') or (
                'community-campaign:%s:encounter:%d:generation:%d' %
                (fleet['campaignId'], encounter_id, generation))
            config = row.get('config')
            coord = encounter_search.Coordinator(
                        path=self.path, revision=self.battleCompatibilityRevision,
                        scheduler_revision=self.runtimeRevision, workers=battle_workers, telemetry=True,
                maximum=12, encounter=encounter_id, runtime_key=runtime_key)
            # The shared planning host is INJECTED; the coordinator never constructs or closes a pool.
            coord.planning_host = self
            mapping = dict(enabled=True, mode='community-first',
                           config=config or encounter_search.default_config('community-first'),
                           constraints=None, encounter=encounter_id, reference=None, thresholds=None,
                           previous=dict(shares={}, tracks={}, mapping={}))
            coord.load(store, mapping_override=mapping)
            if row.get('sessionId') is not None and coord.session_id is None:
                coord.session_id = row['sessionId']
            row.update(encounterId=encounter_id, generation=generation,
                       runtimeKey=runtime_key, campaignScope=campaign_scope)
            fleet['encounters'][key] = row
            coordinators[encounter_id] = coord
        # One evaluator owns all worker slots and all child battle jobs. SQLite harvest/reserve/
        # complete calls still run serially here on the optimizer thread.
        first = next(iter(coordinators.values()))
        if (self._community_evaluator is not None
                and (self._community_evaluator.closed
                     or self._community_evaluator.workers != battle_workers)):
            self._community_evaluator.close()
            self._community_evaluator = None
        if self._community_evaluator is None:
            first.configure_duty(duty)
            first.configure_workers(battle_workers)
            self._community_evaluator = first._make_evaluator()
        else:
            self._community_evaluator.configure_duty(duty)
        for coord in coordinators.values():
            coord.workers = battle_workers
            coord.duty = duty
            coord.evaluator = self._community_evaluator
        self._campaign_coordinators = coordinators
        self._encounter = first
        self._encounter_previous_shares = None
        for encounter_id, coord in coordinators.items():
            key = str(encounter_id)
            row = fleet['encounters'][key]
            if row.get('sessionId') is not None:
                # A manual study may have used the ledger's exclusive activation since this
                # campaign last ran. Its current frozen experiments still belong to these
                # sessions, but reserve() refuses every job while their sessions are inactive.
                # Restore only the session whose persisted scope matches this fleet row.
                _reactivate_campaign_session(store.db, encounter_id, row)
                continue
            previous = self._encounter
            self._encounter = coord
            activation_value = dict(mode='community-first', purposes=row.get('purposes'),
                                    encounter=encounter_id, reference=None, constraints=None,
                                    thresholds=None, campaignScope=row['campaignScope'])
            preview = self._preview_encounter(store, activation_value)
            if self._encounter_preview_token is None:
                raise ValueError('Community encounter preview failed for %d' % encounter_id)
            migration = preview.get('simulatorMigration') or {}
            if migration.get('required'):
                # Bind activation to the exact read-only plan just previewed. The downstream
                # activation guard recomputes and checks this plan before applying the grant.
                activation_value['migrationPlanId'] = migration.get('planId')
            # Commit the exact config/scope before activation. A crash after ledger activation but
            # before the session id reaches the fleet row can then retry this identical request;
            # configure_session coalesces it onto the same grant.
            row.update(config=preview.get('config'), activationDigest=canonical(preview.get('config')))
            fleet['encounters'][key] = row
            store.set('communityCampaignFleet', fleet)
            store.db.commit()
            activation_value['config'] = preview.get('config')
            result = self._activate_encounter(store, activation_value)
            if not isinstance(result, dict) or result.get('ok') is False:
                raise ValueError((result or {}).get('error') or
                                 'Community encounter activation failed for %d' % encounter_id)
            row.update(config=preview['config'], sessionId=result.get('sessionId'),
                       activatedAt=time.time(), campaignScope=activation_value['config']['studyScope']['campaignScope'])
            row.pop('activationDigest', None)
            self._encounter = previous
            store.set('communityCampaignFleet', fleet)
            store.db.commit()
        # Persist the complete enabled set before returning to the desktop command loop.
        store.set('communityCampaignFleet', fleet)
        store.set(COMMUNITY_ENABLED_KEY, True)
        store.db.commit()
        self._campaign_started = True
        self._campaign_report = self._campaign_fleet_status()
        self._encounter_report = self._encounter.report()
        return self._campaign_report

    def _campaign_resume_existing(self, store):
        """Mark an already-loaded fleet active without rebuilding its coordinators or grants."""
        fleet = store.get('communityCampaignFleet')
        if isinstance(fleet, dict):
            if fleet.get('status') != 'active':
                fleet['status'] = 'active'
                fleet['updatedAt'] = time.time()
                store.set('communityCampaignFleet', fleet)
                store.db.commit()
            self._community_fleet_state = fleet
        self._campaign_started = True
        self._campaign_report = self._campaign_fleet_status()
        return self._campaign_report

    @staticmethod
    def _campaign_coordinator_status(coord):
        """Build the campaign's public progress/timing subset without a full report copy.

        ``Coordinator.report()`` deliberately returns a detached, UI-ready snapshot containing the
        full portfolio, boundary history, budgets and diagnostics.  Fleet status only needs its
        session id, progress and timings, so asking for that full snapshot here needlessly copies
        every coordinator's historical state once more on every fleet refresh.
        """
        state = coord.state
        cohort = coord._cohort()
        current = cohort[0] if cohort else None

        def public_member(member):
            view = {key: value for key, value in member.items()
                    if key not in ('observedCandidate', 'observedReference',
                                   'candidateRawScenario', 'referenceRawScenario', 'jobs')}
            view['planJobs'] = len(member.get('jobs') or [])
            return view

        public_current = public_member(current) if current is not None else None
        public_cohort = [public_member(member) for member in cohort]
        progress = dict(
            roundIndex=int(state.get('roundIndex') or 0),
            experiments=len(state.get('completedExperiments') or []),
            retiredFrozenPlanCount=len(state.get('retiredFrozenPlans') or []),
            reserved=sum(int(member.get('reserved') or 0) for member in cohort),
            completed=sum(int(member.get('completed') or 0) for member in cohort),
            current=public_current,
            cohort=public_cohort, cohortPending=len(cohort),
            cohortBound=encounter_search.cohort_bound(coord.workers),
            paired=list(state.get('paired') or []),
            confirmation=state.get('confirmation'), publication=state.get('publication'),
            queueLength=len(state.get('queue') or []), freshLibrary=False,
            idle=bool(state.get('idle')),
            unspendable=list(state.get('unspendable') or []),
            unspendableTasks=list(state.get('unspendableTasks') or []),
            blocked=any(bool(member.get('blocked')) for member in cohort))
        timings = dict(
            state.get('timings') or {},
            preparationDiagnostics=dict(encounter_search.proposals_service.LAST_PREPARATION),
            proposalWorkers=encounter_search.parallel_proposals.diagnostics(),
            duty=coord.duty, allocation=coord.allocation_status(),
            planner=coord.planner_status(),
            evaluator=(coord.evaluator.status() if coord.evaluator is not None else None))
        # Match report()'s public immutability: callers may mutate their status snapshot without
        # changing the live coordinator's persisted runtime objects.
        return deepcopy(dict(sessionId=coord.session_id, progress=progress, timings=timings))

    def _campaign_fleet_status(self, reports=None):
        coordinators = getattr(self, '_campaign_coordinators', None) or {}
        encounters = {}
        active = runnable = experiments = 0
        for encounter_id, coord in sorted(coordinators.items()):
            # Fleet publication has already materialized a detached full report for each owner.
            # Reuse those values instead of recomputing planner/evaluator diagnostics and copying
            # progress/timings from the same live state a second time. Copy just the public subset
            # so campaign rows stay independently mutable from `perEncounter` in the response.
            source = (reports or {}).get(encounter_id)
            if source is None:
                source = (reports or {}).get(str(encounter_id))
            if isinstance(source, dict):
                report = deepcopy(dict(sessionId=source.get('sessionId'),
                                        progress=source.get('progress') or {},
                                        timings=source.get('timings') or {}))
            else:
                report = self._campaign_coordinator_status(coord)
            progress = report['progress']
            current = progress.get('current')
            has_work = bool(current or progress.get('cohortPending') or not progress.get('idle'))
            active += int(has_work)
            runnable += int(bool(current and not current.get('blocked')))
            experiments += int(progress.get('cohortPending') or 0)
            encounters[str(encounter_id)] = dict(encounterId=encounter_id,
                generation=int((self._fleet_row(encounter_id) or {}).get('generation') or 0),
                sessionId=report.get('sessionId'), status='active' if has_work else 'idle',
                progress=progress, timings=report.get('timings'))
        fleet = self._fleet_state()
        ids = list((fleet or {}).get('enabledEncounterIds') or coordinators.keys())
        persisted_status = (fleet or {}).get('status')
        status = (persisted_status if persisted_status in ('paused', 'stopped') else
                  'active' if coordinators else 'none')
        return dict(ok=True, status=status,
                    campaignId=(fleet or {}).get('campaignId'), total=len(ids), completed=0,
                    round=max((row.get('generation', 0) for row in encounters.values()), default=0),
                    encounterIds=ids, enabledEncounterIds=ids, encounters=encounters,
                    activeEncounters=active, runnableEncounters=runnable,
                    activeExperiments=experiments, concurrent=True,
                    currentEncounterId=None, stopRuntime=False, newCampaignAutomatic=True)

    def _campaign_resize_drain_status(self):
        """Report whether every accepted battle and planner route is fully harvested."""
        evaluator = getattr(self, '_community_evaluator', None)
        if evaluator is None:
            coordinators = getattr(self, '_campaign_coordinators', None) or {}
            evaluator = next((getattr(coord, 'evaluator', None)
                              for coord in coordinators.values()
                              if getattr(coord, 'evaluator', None) is not None), None)
        battle_pending = len(getattr(evaluator, 'pending', None) or {}) if evaluator else 0
        try:
            busy = bool(evaluator.busy()) if evaluator is not None else False
        except Exception:  # noqa: BLE001 - an unreadable pool is not a safe resize boundary
            busy = True
        pool = getattr(self, '_planning_pool', None)
        pool_status = {}
        pool_readable = True
        if pool is not None:
            try:
                pool_status = pool.status()
            except Exception:  # noqa: BLE001 - do not replace a pool with an unknown queue state
                pool_readable = False
        outstanding = int(pool_status.get('outstanding') or 0)
        running = int(pool_status.get('running') or 0)
        queued = int(pool_status.get('pending') or 0)
        completed_unharvested = int(pool_status.get('completedUnharvested') or 0)
        routes = len(getattr(self, '_planner_routes', None) or {})
        try:
            chunks = len(self._planner_chunk_map())
            task_phases = len(self._planner_task_phase_map())
        except Exception:  # noqa: BLE001 - a missing route map means the drain is not proven
            chunks = task_phases = 1
        deferred = len(getattr(self, '_planner_deferred_records', None) or ())
        waiting = dict(battlePending=battle_pending, battleBusy=busy,
                       plannerRoutes=routes, plannerOutstanding=outstanding,
                       plannerQueued=queued, plannerRunning=running,
                       plannerFinishedUnharvested=completed_unharvested,
                       plannerChunks=chunks, plannerTaskPhases=task_phases,
                       plannerDeferredRecords=deferred)
        ready = (pool_readable and battle_pending == 0 and not busy and routes == 0
                 and outstanding == 0 and running == 0 and queued == 0
                 and completed_unharvested == 0 and chunks == 0 and task_phases == 0
                 and deferred == 0)
        return dict(ready=ready, waitingFor=waiting, poolStatus=pool_status)

    def _apply_campaign_resize_if_drained(self, store):
        """Replace explicit role counts only after all accepted work has been saved and routed."""
        pending = getattr(self, '_campaign_resize_pending', None)
        if not isinstance(pending, dict):
            return False
        drain = self._campaign_resize_drain_status()
        status = getattr(self, '_worker_resize_status', None)
        if not isinstance(status, dict):
            status = {}
            self._worker_resize_status = status
        status.update(state='draining', waitingFor=drain['waitingFor'])
        self._scheduler['workerAllocation'] = self._worker_allocation_status()
        if not drain['ready']:
            return False

        from strategy_optimizer_limits import clamp_workers
        workers = clamp_workers(pending.get('workers'))
        duty = clamp_duty(pending.get('duty'))
        battles = self._battle_worker_count(workers)
        planners = self._planner_worker_count(workers)
        old_workers = int(getattr(self, '_workers', default_workers()) or 1)
        old_duty = getattr(self, '_duty', 1.0)
        old_battles = self._battle_worker_count(old_workers)
        old_planners = self._planner_worker_count(old_workers)
        pool = getattr(self, '_planning_pool', None)
        pool_workers = (int(getattr(pool, 'workers', 0) or 0)
                        if pool is not None and not getattr(pool, 'closed', True) else 0)

        # The routes and pool counters above are all zero, so a planner pool replacement cannot
        # discard accepted work. Keep an equal-sized pool alive; only a changed P needs a rebuild.
        if pool_workers and pool_workers != planners:
            close_report = self._close_planning_pool()
            if (int(close_report.get('cancelledPending') or 0)
                    or int(close_report.get('unharvestedResults') or 0)):
                status.update(state='error', error='Planner pool closed with unexpected queued or '
                              'unharvested work; resize remains pending.')
                self._scheduler['workerAllocation'] = self._worker_allocation_status()
                return False

        evaluator = getattr(self, '_community_evaluator', None)
        evaluator_matches = bool(evaluator is not None
                                 and not getattr(evaluator, 'closed', False)
                                 and int(getattr(evaluator, 'workers', 0) or 0) == battles)
        if evaluator is not None and not getattr(evaluator, 'closed', False) and not evaluator_matches:
            evaluator.close()
        if not evaluator_matches:
            self._community_evaluator = None
        coordinators = getattr(self, '_campaign_coordinators', None) or {}
        for coord in coordinators.values():
            coord.workers = battles
            coord.duty = duty

        self._workers, self._duty = workers, duty
        if coordinators:
            first = next(iter(coordinators.values()))
            first.configure_duty(duty)
            if not evaluator_matches:
                for coord in coordinators.values():
                    coord.evaluator = None
                first.configure_workers(battles)
                evaluator = first._make_evaluator()
                self._community_evaluator = evaluator
            for coord in coordinators.values():
                coord.workers = battles
                coord.duty = duty
                coord.evaluator = evaluator

        # Occupancy samples use the configured worker count as their denominator. Start a fresh
        # window so observations from the old split cannot be relabelled under the new split.
        self._active_window = []
        self._work_started = None
        self._campaign_resize_pending = None
        applied_at = time.time()
        self._worker_resize_status = dict(
            state='applied', requestedWorkers=workers, requestedDuty=duty,
            effectiveWorkers=workers, effectivePlannerWorkers=planners,
            effectiveBattleWorkers=battles, appliedAt=applied_at,
            previousWorkers=old_workers, previousDuty=old_duty,
            previousPlannerWorkers=old_planners, previousBattleWorkers=old_battles,
            waitingFor=drain['waitingFor'])
        self._scheduler['workerAllocation'] = self._worker_allocation_status()
        self._campaign_report = self._campaign_fleet_status()

        # Keep the just-published per-encounter report honest until the next normal fleet pass.
        report = getattr(self, '_encounter_report', None)
        if isinstance(report, dict):
            report['campaign'] = self._campaign_report
            timings = report.setdefault('timings', {})
            if isinstance(timings, dict):
                timings['evaluator'] = evaluator.status() if evaluator is not None else None
                timings['allocation'] = (self._encounter.allocation_status()
                                         if self._encounter is not None else None)
                timings['duty'] = duty
            for encounter_id, coord in coordinators.items():
                row = (report.get('perEncounter') or {}).get(str(encounter_id))
                row_timings = row.get('timings') if isinstance(row, dict) else None
                if isinstance(row_timings, dict):
                    row_timings['evaluator'] = evaluator.status() if evaluator is not None else None
                    row_timings['allocation'] = coord.allocation_status()
                    row_timings['duty'] = duty
        return True

    def _campaign_finish(self, store, status):
        """Durably pause/stop the fleet after its in-flight battles have drained."""
        if not getattr(self, '_campaign_started', False):
            return False
        fleet = store.get('communityCampaignFleet')
        if not isinstance(fleet, dict):
            return False
        changed = fleet.get('status') != status
        if not changed:
            return False
        fleet['status'] = status
        fleet['updatedAt'] = time.time()
        store.set('communityCampaignFleet', fleet)
        store.db.commit()
        self._community_fleet_state = fleet
        if status == 'stopped':
            pending_resize = getattr(self, '_campaign_resize_pending', None)
            if isinstance(pending_resize, dict):
                self._campaign_resize_pending = None
                self._worker_resize_status = dict(
                    state='cancelled', requestedWorkers=pending_resize.get('workers'),
                    requestedDuty=pending_resize.get('duty'), completedAt=time.time(),
                    reason='campaign-stopped')
            # Stop means stop: queued planner jobs are cancelled and their owners released before
            # the fleet is torn down, so no late planner result can admit work into a stopped run.
            self._cancel_all_planning(store)
            evaluator = getattr(self, '_community_evaluator', None)
            if evaluator is not None:
                evaluator.close()
            self._community_evaluator = None
            for coord in (getattr(self, '_campaign_coordinators', None) or {}).values():
                coord.evaluator = None
            self._campaign_started = False
        self._campaign_report = self._campaign_fleet_status()
        return changed

    def _fleet_state(self):
        return getattr(self, '_community_fleet_state', None)

    def _fleet_row(self, encounter_id):
        fleet = self._fleet_state()
        return ((fleet or {}).get('encounters') or {}).get(str(encounter_id))

    def _campaign_supervise(self, store, state, *, reuse_fresh_report=False):
        """Per-encounter idle state never gates another encounter's dispatch."""
        started = time.perf_counter()
        status_seconds = 0.0
        reused = False
        if not self._campaign_started:
            scheduler = getattr(self, '_scheduler', None)
            if isinstance(scheduler, dict):
                scheduler['communitySuperviseSeconds'] = round(
                    time.perf_counter()-started, 4)
                scheduler['communitySuperviseStatusSeconds'] = status_seconds
                scheduler['communitySuperviseStatusReused'] = reused
            return state, None
        self._community_fleet_state = store.get('communityCampaignFleet')
        # In the campaign worker loop, _campaign_fleet_pass has just built a complete status
        # snapshot and there is no state mutation between that call and supervision. Reuse that
        # snapshot instead of walking/copying every coordinator a second time. Other callers keep
        # the refresh behavior unless they explicitly establish the same freshness guarantee.
        reused = bool(reuse_fresh_report and isinstance(self._campaign_report, dict))
        if not reused:
            status_started = time.perf_counter()
            self._campaign_report = self._campaign_fleet_status()
            status_seconds = time.perf_counter()-status_started
        scheduler = getattr(self, '_scheduler', None)
        if isinstance(scheduler, dict):
            scheduler['communitySuperviseSeconds'] = round(
                time.perf_counter()-started, 4)
            scheduler['communitySuperviseStatusSeconds'] = round(status_seconds, 4)
            scheduler['communitySuperviseStatusReused'] = reused
        return state, None

    def _campaign_fleet_pass(self, store, *, running):
        """Harvest once, then plan/dispatch every encounter fairly into the shared evaluator."""
        import strategy_experiment_store as ledger

        fleet = store.get('communityCampaignFleet') or {}
        self._community_fleet_state = fleet
        coordinators = getattr(self, '_campaign_coordinators', None) or {}
        if not coordinators:
            return None
        evaluator = self._community_evaluator
        if evaluator is None:
            first = next(iter(coordinators.values()))
            evaluator = first._make_evaluator()
            self._community_evaluator = evaluator
            for coord in coordinators.values():
                coord.evaluator = evaluator
        began = time.perf_counter()
        harvest_started = time.perf_counter()
        harvested = evaluator.harvest(store.db)
        initial_harvest_seconds = time.perf_counter() - harvest_started
        by_session = {}
        for entry in harvested:
            row = store.db.execute('SELECT session_id FROM ea_experiment WHERE id=?',
                                   (entry.get('experimentId'),)).fetchone()
            if row is not None:
                by_session.setdefault(row[0], []).append(entry)
        ids = sorted(coordinators)
        if ids:
            offset = self._campaign_fair_cursor % len(ids)
            ids = ids[offset:] + ids[:offset]
        # Fair dispatch is measured against the EFFECTIVE battle workers (the requested total minus
        # the shared planner's workers), so the pool can never over-subscribe the battle evaluator.
        battle_workers = self._battle_worker_count(self._workers)
        def evaluator_admission_capacity():
            """Free bounded ready-window capacity, falling back to worker slots."""
            capacity = getattr(evaluator, 'admission_capacity', None)
            if callable(capacity):
                try:
                    return max(0, int(capacity()))
                except (TypeError, ValueError):
                    pass
            return max(0, battle_workers-len(evaluator.pending))
        # Only encounters with an empty cohort that can plan, or a compatible frozen plan, compete
        # for a worker share. `_has_work()` alone includes blocked frozen plans; dividing by those
        # plans strands free workers even though they can never pass dispatch validation.
        runnable_started = time.perf_counter()
        runnable = _campaign_runnable_encounters(coordinators, store.db)
        runnable_scan_seconds = time.perf_counter() - runnable_started
        runnable_left = len(runnable)
        pass_times = []
        stage_totals = {name: 0.0 for name in
                        ('proposalSeconds', 'planningSeconds', 'recoverySeconds',
                         'portfolioSeconds', 'dispatchSeconds', 'priorityDispatchSeconds',
                         'reportConstructionSeconds', 'campaignStatusSeconds',
                         'battleHarvestSeconds', 'plannerHarvestSeconds',
                         'runnableScanSeconds', 'authorizedScanSeconds',
                         'checkpointSeconds', 'rolloverSeconds')}
        stage_totals['battleHarvestSeconds'] = initial_harvest_seconds
        stage_totals['runnableScanSeconds'] = runnable_scan_seconds
        rollover_count = 0
        capacity_left = evaluator_admission_capacity()
        recovery_snapshot = None
        recovery_snapshot_seconds = 0.0
        pending_evidence_sessions = set(by_session)
        safe_planner_owners = {
            encounter_id: coord for encounter_id, coord in coordinators.items()
            if coord.session_id not in pending_evidence_sessions}
        visited_encounters = set()
        newly_submitted_by_encounter = {}
        # A planner result is safe to realize only after its owner has absorbed this pass's
        # persisted outcomes, refreshed any evidence-derived state, and checkpointed. Keep the
        # owners with no newly harvested evidence eligible immediately, then add each owner after
        # its pass. A ready result can therefore be consumed at the first boundary where its own
        # state is current, rather than after every encounter has completed its serial pass.
        def harvest_safe_planner_results():
            nonlocal capacity_left
            eligible = list(safe_planner_owners.values())
            accepted_before = {id(coord): int((getattr(coord, '_planner_metrics', {}) or {}).get(
                'accepted') or 0) for coord in eligible}
            planner_harvest_started = time.perf_counter()
            self._harvest_planning(store, eligible_coordinators=eligible)
            stage_totals['plannerHarvestSeconds'] += (
                time.perf_counter() - planner_harvest_started)
            newly_realized = [coord for coord in eligible
                              if int((getattr(coord, '_planner_metrics', {}) or {}).get(
                                  'accepted') or 0) > accepted_before.get(id(coord), 0)]
            if not running or not newly_realized:
                return
            # Results only create ordinary frozen plans. Refill from those plans through the existing
            # revision, compatibility and reservation gates before more host work runs. A not-yet-
            # visited owner is capped at its fair share of the current free capacity; its later pass
            # may use only the remainder of that share, so early readiness cannot steal another
            # encounter's turn.
            realized_ids = {id(coord) for coord in newly_realized}
            active = [encounter_id for encounter_id in ids if
                      id(coordinators[encounter_id]) in realized_ids
                      and coordinators[encounter_id]._authorized_dispatch_count(store.db) > 0]
            share = max(1, math.ceil(capacity_left/max(1, runnable_left))) if capacity_left else 0
            validated = set()
            for encounter_id in active:
                if capacity_left <= 0:
                    break
                coord = coordinators[encounter_id]
                if encounter_id in visited_encounters:
                    allowance = max(0, int(coord._submission_quota or 0)
                                    - int(coord._submission_started or 0))
                else:
                    allowance = max(0, share - newly_submitted_by_encounter.get(
                        encounter_id, 0))
                    coord._submission_quota = allowance
                    coord._submission_started = 0
                if allowance <= 0:
                    continue
                before = dict(coord.state.get('timings') or {})
                pending_before = len(evaluator.pending)
                coord.dispatch_authorized_ready(store, invalidate=encounter_id not in validated)
                validated.add(encounter_id)
                submitted = max(0, len(evaluator.pending) - pending_before)
                newly_submitted_by_encounter[encounter_id] = (
                    newly_submitted_by_encounter.get(encounter_id, 0) + submitted)
                capacity_left = evaluator_admission_capacity()
                stage_totals['priorityDispatchSeconds'] += max(
                    0.0, float((coord.state.get('timings') or {}).get(
                        'priorityDispatchSeconds') or 0.0)
                    - float(before.get('priorityDispatchSeconds') or 0.0))

        # First spend free evaluator slots only on frozen, already-authorised work. Reuse this
        # fair sweep after each harvest too: a completed battle releases a real slot only after its
        # result has been persisted by Evaluator.harvest.
        def dispatch_authorized_fair():
            nonlocal capacity_left
            capacity_left = evaluator_admission_capacity()
            if not running or capacity_left <= 0:
                return
            # Authorization can be expensive for frozen confirmations: it reads the confirmation
            # report and the pending holdout rows. Take one snapshot for this refill boundary, then
            # track only reservations made by this sweep. Dispatch still revalidates the frozen
            # intent and every reservation; a zero-submit result is deferred until the next refill
            # boundary instead of repeatedly rescanning an unchanged blocked plan.
            authorized_scan_started = time.perf_counter()
            authorized_remaining = {
                encounter_id: max(0, int(coordinators[encounter_id]._authorized_dispatch_count(
                    store.db) or 0))
                for encounter_id in ids
            }
            stage_totals['authorizedScanSeconds'] += (
                time.perf_counter() - authorized_scan_started)
            priority_active = [encounter_id for encounter_id in ids
                               if authorized_remaining[encounter_id] > 0]
            validated_priority = set()
            while capacity_left > 0 and priority_active:
                least_submitted = min(newly_submitted_by_encounter.get(encounter_id, 0)
                                      for encounter_id in priority_active)
                fair_active = [encounter_id for encounter_id in priority_active
                               if newly_submitted_by_encounter.get(encounter_id, 0)
                               == least_submitted]
                remaining_priority = list(fair_active)
                submitted_this_sweep = 0
                for encounter_id in fair_active:
                    if capacity_left <= 0:
                        break
                    coord = coordinators[encounter_id]
                    quota = max(1, math.ceil(capacity_left / max(1, len(remaining_priority))))
                    remaining_priority.remove(encounter_id)
                    coord._submission_quota = quota
                    coord._submission_started = 0
                    pending_before = len(evaluator.pending)
                    timings_before = dict(coord.state.get('timings') or {})
                    coord.dispatch_authorized_ready(
                        store, invalidate=encounter_id not in validated_priority)
                    validated_priority.add(encounter_id)
                    submitted = max(0, len(evaluator.pending) - pending_before)
                    newly_submitted_by_encounter[encounter_id] = (
                        newly_submitted_by_encounter.get(encounter_id, 0) + submitted)
                    submitted_this_sweep += submitted
                    # Reservations consume exactly the jobs actually submitted. If the guarded
                    # dispatch submits nothing (for example a stale frozen confirmation), do not
                    # retry the same plan in this invocation; a later pass will re-check it after
                    # any recovery, retirement or planner update.
                    authorized_remaining[encounter_id] = (
                        max(0, authorized_remaining[encounter_id] - submitted)
                        if submitted else 0)
                    capacity_left = evaluator_admission_capacity()
                    timings_after = coord.state.get('timings') or {}
                    stage_totals['priorityDispatchSeconds'] += max(
                        0.0, float(timings_after.get('priorityDispatchSeconds') or 0.0)
                        - float(timings_before.get('priorityDispatchSeconds') or 0.0))
                if capacity_left <= 0:
                    break
                next_active = [encounter_id for encounter_id in priority_active
                               if authorized_remaining[encounter_id] > 0]
                if submitted_this_sweep <= 0 and next_active == priority_active:
                    break
                priority_active = next_active

        def harvest_ready_battle_results():
            """Persist newly completed battles at this coordinator boundary, then refill slots."""
            nonlocal capacity_left
            if not any(job.get('future') is not None and job['future'].done()
                       for job in evaluator.pending.values()):
                return []
            harvest_started = time.perf_counter()
            completed = evaluator.harvest(store.db)
            stage_totals['battleHarvestSeconds'] += time.perf_counter() - harvest_started
            for entry in completed:
                row = store.db.execute('SELECT session_id FROM ea_experiment WHERE id=?',
                                       (entry.get('experimentId'),)).fetchone()
                if row is None:
                    continue
                session_id = row[0]
                by_session.setdefault(session_id, []).append(entry)
                # A planner owner with newly persisted battle evidence must absorb it and complete
                # its evidence/checkpoint work before a ready planner result is admitted.
                for encounter_id, coord in coordinators.items():
                    if coord.session_id == session_id:
                        safe_planner_owners.pop(encounter_id, None)
            capacity_left = evaluator_admission_capacity()
            if completed:
                dispatch_authorized_fair()
            return completed

        # Fairly fill the slots that were already free when the fleet pass began.
        dispatch_authorized_fair()

        # Ready results for owners without new outcomes are safe after the authorized battle refill.
        # Consume and dispatch those frozen plans before campaign recovery scans or coordinator work.
        harvest_safe_planner_results()

        # Recovery is also authorised work, but its campaign-wide ledger scan is deferred until
        # the fast path has had first claim on every free slot. Keep recovery ahead of planning.
        if running and capacity_left > 0 and runnable:
            recovery_started = time.perf_counter()
            recovery_snapshot = ledger.RecoverySnapshot(store.db)
            recovery_snapshot_seconds = time.perf_counter() - recovery_started
            stage_totals['recoverySeconds'] += recovery_snapshot_seconds
            recovery_active = [encounter_id for encounter_id in ids
                               if recovery_snapshot.coordinator_view(
                                   coordinators[encounter_id].session_id).get('outstandingCount')]
            remaining_recovery = list(recovery_active)
            for encounter_id in recovery_active:
                if capacity_left <= 0:
                    break
                coord = coordinators[encounter_id]
                quota = max(1, math.ceil(capacity_left / max(1, len(remaining_recovery))))
                remaining_recovery.remove(encounter_id)
                coord._submission_quota = quota
                coord._submission_started = 0
                pending_before = len(evaluator.pending)
                started = time.perf_counter()
                coord._measure_stage('recoverySeconds', coord._recover, store.db, store,
                                     recovery_snapshot)
                recovery_elapsed = time.perf_counter() - started
                stage_totals['recoverySeconds'] += recovery_elapsed
                submitted = max(0, len(evaluator.pending) - pending_before)
                newly_submitted_by_encounter[encounter_id] = (
                    newly_submitted_by_encounter.get(encounter_id, 0) + submitted)
                capacity_left = evaluator_admission_capacity()

        for encounter_id in ids:
            coord = coordinators[encounter_id]
            if encounter_id in runnable:
                # Fair share of the capacity still free, over the runnable encounters that still
                # have a turn; the last runnable encounter can take whatever is left.
                coord._submission_quota = (max(0, math.ceil(capacity_left/runnable_left)
                                               - newly_submitted_by_encounter.get(
                                                   encounter_id, 0))
                                           if capacity_left else 0)
                runnable_left -= 1
            else:
                # Nothing to dispatch this pass; it must not hold a share another encounter can use.
                coord._submission_quota = 0
            pending_before = len(evaluator.pending)
            entries = by_session.get(coord.session_id, [])
            t0 = time.perf_counter()
            prior_timings = dict(coord.state.get('timings') or {})
            report = coord.run_pass(store, running=running, harvested_entries=entries,
                                    recovery_snapshot=None if running else recovery_snapshot,
                                    return_report=False)
            pass_times.append(time.perf_counter()-t0)
            after_timings = coord.state.get('timings') or {}
            for name in stage_totals:
                stage_totals[name] += max(0.0, float(after_timings.get(name) or 0.0)
                                          - float(prior_timings.get(name) or 0.0))
            used = max(0, len(evaluator.pending)-pending_before)
            newly_submitted_by_encounter[encounter_id] = (
                newly_submitted_by_encounter.get(encounter_id, 0) + used)
            capacity_left = evaluator_admission_capacity()
            if report is None:
                safe_planner_owners[encounter_id] = coordinators.get(encounter_id, coord)
                visited_encounters.add(encounter_id)
                harvest_ready_battle_results()
                harvest_safe_planner_results()
                continue
            # A drained encounter's bounded per-encounter budget gets a fresh generation only
            # after it has produced measured work. Empty/unspendable studies stay idle rather than
            # spinning up duplicate no-op sessions. Other encounters always keep dispatching.
            progress = report.get('progress') or {}
            if running and _campaign_should_rollover(progress):
                row = (fleet.get('encounters') or {}).get(str(encounter_id)) or {}
                if not row.get('rolloverPending'):
                    rollover_started = time.perf_counter()
                    row['rolloverPending'] = True
                    row['previousRemainder'] = (report.get('budgets') or {}).get('session')
                    row['generation'] = int(row.get('generation') or 0) + 1
                    row['sessionId'] = None
                    row['runtimeKey'] = ('encounterRuntime:campaign:%s:%d:%d' %
                        (fleet.get('campaignId'), encounter_id, row['generation']))
                    row['campaignScope'] = ('community-campaign:%s:encounter:%d:generation:%d' %
                        (fleet.get('campaignId'), encounter_id, row['generation']))
                    row.pop('config', None)
                    coord2 = encounter_search.Coordinator(
                        path=self.path, revision=self.battleCompatibilityRevision,
                        scheduler_revision=self.runtimeRevision, workers=battle_workers,
                        telemetry=True, maximum=12, encounter=encounter_id,
                        runtime_key=row['runtimeKey'])
                    coord2.planning_host = self
                    coord2.load(store, mapping_override=dict(enabled=True, mode='community-first',
                        config=encounter_search.default_config('community-first'), encounter=encounter_id,
                        constraints=None, reference=None, thresholds=None))
                    coord2.evaluator = evaluator
                    coord2.duty = self._duty
                    previous_coord = coordinators.get(encounter_id)
                    # A rolled-over generation must not admit the retired coordinator's planning
                    # result: cancel its queued job and drop its route before it is replaced.
                    if previous_coord is not None:
                        self.cancel_planning(previous_coord)
                    coordinators[encounter_id] = coord2
                    old = self._encounter
                    self._encounter = coord2
                    val = dict(mode='community-first', encounter=encounter_id, reference=None,
                               constraints=None, thresholds=None, campaignScope=row['campaignScope'])
                    preview = self._preview_encounter(store, val)
                    migration = preview.get('simulatorMigration') or {}
                    if migration.get('required'):
                        val['migrationPlanId'] = migration.get('planId')
                    val['config'] = preview['config']
                    activated = self._activate_encounter(store, val)
                    if previous_coord is not None:
                        coord2.inherit_evidence_caches(previous_coord, store.db)
                    self._encounter = old
                    row.update(config=preview['config'], sessionId=activated.get('sessionId'),
                               rolloverPending=False, activatedAt=time.time())
                    fleet['encounters'][str(encounter_id)] = row
                    if self._encounter is previous_coord:
                        self._encounter = coord2
                    store.set('communityCampaignFleet', fleet)
                    store.db.commit()
                    stage_totals['rolloverSeconds'] += time.perf_counter() - rollover_started
                    rollover_count += 1
            safe_planner_owners[encounter_id] = coordinators.get(encounter_id, coord)
            visited_encounters.add(encounter_id)
            harvest_ready_battle_results()
            harvest_safe_planner_results()
        # Catch results that became ready during the final coordinator/report boundary. Every live
        # owner's evidence and checkpoint work has now completed for this fleet pass.
        harvest_safe_planner_results()
        self._campaign_fair_cursor = (self._campaign_fair_cursor + 1) % max(1, len(ids))
        report_started = time.perf_counter()
        reports = {encounter_id: coord.report()
                   for encounter_id, coord in coordinators.items()}
        stage_totals['reportConstructionSeconds'] += time.perf_counter()-report_started
        primary_id = min(coordinators)
        primary = reports[primary_id]
        # Keep the historical single report shape usable, and attach complete per-encounter status.
        combined = dict(primary)
        combined['timings'] = dict(primary.get('timings') or {})
        combined['timings']['fleetPassSeconds'] = round(time.perf_counter()-began, 4)
        combined['timings']['campaignRecoverySnapshotSeconds'] = round(
            recovery_snapshot_seconds, 6)
        combined['timings']['fleetRolloverCount'] = rollover_count
        combined['timings']['encounterPassSeconds'] = [round(t, 4) for t in pass_times]
        combined['timings']['evaluator'] = evaluator.status()
        campaign_status_started = time.perf_counter()
        combined['campaign'] = self._campaign_fleet_status(reports=reports)
        stage_totals['campaignStatusSeconds'] += (
            time.perf_counter()-campaign_status_started)
        combined['timings']['fleetStagesSeconds'] = {k: round(v, 4)
                                                       for k, v in stage_totals.items()}
        combined['perEncounter'] = {str(cid): report for cid, report in reports.items()}
        self._campaign_report = combined['campaign']
        self._encounter_report = combined
        return combined

    def _preview_encounter(self, store, value):
        """Resolve presentation without inserting a candidate or changing campaign state."""
        from strategy_optimizer_adapter import default_scenario
        from strategy_encounter_presentation import describe
        value = self._encounter_request(value)
        if 'allocations' in value or 'purposes' in value:
            # This is a new target draft. Previous owner totals describe the old plan, not the
            # requested allocation; inheriting them silently gives a newly enabled owner no runs.
            value['oldBudgets'] = None
        value.setdefault('allocations', dict(self._student_shares))
        value.update(previousShares=dict(self._student_shares),
                     previousTracks=dict(self._track_shares))
        scope = {key: value.get(key, getattr(self._encounter, key, None))
                 for key in ('encounter', 'reference', 'constraints', 'thresholds')}
        reference_id = scope['reference']
        encounter_id = scope['encounter']
        if encounter_id is not None and (isinstance(encounter_id, bool)
                                        or not isinstance(encounter_id, int)):
            raise ValueError('Encounter must be a whole-number catalogue identifier')
        thresholds = scope['thresholds']
        if thresholds is not None and (not isinstance(thresholds, list) or any(
                isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in thresholds)):
            raise ValueError('Reward thresholds must be non-negative whole numbers')
        parent = store.scenario(reference_id) if reference_id is not None else None
        if reference_id is not None and parent is None:
            raise ValueError('The selected reference is absent from this library')
        if parent is not None and encounter_id is not None and parent.get('encounterId') != encounter_id:
            raise ValueError('The selected reference belongs to a different encounter')
        if parent is None and encounter_id is not None:
            reference_id, parent = students.community_parent(store, encounter_id)
        if parent is None:
            parent = default_scenario()
            if encounter_id is not None:
                parent = dict(parent, encounterId=encounter_id)
        presentation = describe(parent, scope['constraints'], reference_id=reference_id)
        if not presentation['buildDomain']['schemaValid']:
            raise ValueError('Invalid build constraint schema')
        preview = self._encounter.preview(value)
        import strategy_search_mode as modes
        preview['config']['allocations'] = {
            name: float(preview['config']['allocations'].get(name, 0.0))
            for name in modes.share_streams()}
        preview['config']['purposes'] = {
            name: int(preview['config']['purposes'].get(name, 0)) for name in modes.PURPOSES}
        preview['config']['budgets'] = modes.derive_budget_matrix(preview['config'])['matrix']
        # A new encounter/constraint study owns a distinct budget session. Re-activating the SAME
        # study still coalesces onto its existing charges; changing display thresholds does not
        # create a fresh allowance.
        study_scope = {key: scope[key] for key in ('encounter', 'reference', 'constraints')}
        # A campaign passes its own finite `campaignScope`; folding it into the persisted budget
        # identity gives each campaign its own sessions without changing a manual activation (which
        # never sends it) and therefore preserves the existing manual preview/activate behaviour.
        campaign_scope = value.get('campaignScope')
        if campaign_scope is not None:
            study_scope['campaignScope'] = campaign_scope
        preview['config']['studyScope'] = study_scope
        preview['simulatorMigration'] = (
            dict(eligible=True, required=False) if store.compatible else
            dict(encounter_migration.preview(store.get('provenance'), self.provenance), required=True))
        preview.update(previousTracks=dict(self._track_shares), scope=scope,
                       encounter=scope['encounter'], referenceId=scope['reference'],
                       constraints=scope['constraints'], thresholds=scope['thresholds'],
                       presentation=presentation)
        migration = preview['simulatorMigration']
        self._encounter_preview_token = dict(
            configDigest=canonical(preview.get('config')), scopeDigest=canonical(scope),
            migrationPlanId=migration.get('planId') if migration.get('required') else None,
            required=bool(migration.get('required')))
        return preview

    def _activate_encounter(self, store, value):
        """Commit the previewed grant, mapping, session and runtime as one atomic activation."""
        import strategy_experiment_store as ledger
        import strategy_search_mode as modes
        encounter = self._encounter
        value = self._encounter_request(value)
        config = value.get('config')
        if config is None:
            config = encounter_search.default_config(
                value.get('mode') or encounter.mode,
                purposes=value.get('purposes') or encounter_search.DEFAULT_PURPOSES)
        config = dict(config)
        modes.validate_config(config)
        token = self._encounter_preview_token or {}
        if token.get('configDigest') != canonical(config):
            raise ValueError('Preview this exact configuration before activating it.')
        scope = {key: value.get(key, getattr(encounter, key, None))
                 for key in ('encounter', 'reference', 'constraints', 'thresholds')}
        if token.get('scopeDigest') != canonical(scope):
            raise ValueError('The encounter, reference or build constraints changed; preview again.')
        study_scope = {key: scope[key] for key in ('encounter', 'reference', 'constraints')}
        campaign_scope = value.get('campaignScope')
        if campaign_scope is not None:
            study_scope['campaignScope'] = campaign_scope
        if 'studyScope' in config and config['studyScope'] != study_scope:
            raise ValueError('The previewed budget session belongs to another study')
        config['studyScope'] = study_scope
        config['allocations'] = {name: float(config['allocations'].get(name, 0.0))
                                 for name in modes.share_streams()}
        config['purposes'] = {name: int(config['purposes'].get(name, 0)) for name in modes.PURPOSES}
        config['budgets'] = modes.derive_budget_matrix(config)['matrix']
        if scope['constraints'] is not None:
            import strategy_build_domain
            checked = strategy_build_domain.constraint_schema(scope['constraints'])
            if not checked['valid']:
                raise ValueError('Invalid build constraints: %s' % checked.get('violations'))
        need_migration = not bool(store.compatible)
        migration_plan = None
        if need_migration:
            migration_plan = encounter_migration.preview(store.get('provenance'), self.provenance)
            if not migration_plan.get('eligible'):
                raise ValueError(migration_plan.get('reason') or 'observer migration is not eligible')
            token = self._encounter_preview_token or {}
            if token.get('migrationPlanId') != migration_plan.get('planId'):
                raise ValueError('Preview this exact simulator migration before activating it.')
            if value.get('migrationPlanId') != migration_plan.get('planId'):
                raise ValueError('The activated migration plan does not match the previewed plan.')
        # Create the ledger tables BEFORE the grant, so a table-creation failure cannot leave a
        # compatibility grant behind with no session able to use it.
        ledger.initialize(store.db)
        # Finish unrelated prior writes before establishing the activation transaction. Preserve
        # connection/evaluator identities while copying all mutable coordinator state and caches.
        store.db.commit()
        snapshot = self._encounter_snapshot(store)
        compatible = store.compatible
        try:
            store.db.execute('BEGIN IMMEDIATE')
            # Before this in-place study switch archives/replaces the prior session, capture its full
            # public report into the same transaction so the read-only Strategy guide can render a
            # completed encounter. This never alters the current evidence or fabricates compatibility.
            prior_encounter = encounter.encounter
            if prior_encounter is not None:
                store.set('encounterPlayerSnapshot:%s' % int(prior_encounter),
                          json.loads(json.dumps(encounter.report(), default=str)))
            # Keep resumable cursors per budget session when explicitly switching studies. The
            # public RUNTIME_KEY remains the active study; old candidates/evidence stay in the ledger.
            prior_session = encounter.session_id
            if prior_session is not None:
                store.set('encounterStudyRuntime:%s' % prior_session,
                          json.loads(json.dumps(encounter.state, default=str)))
                encounter.state = encounter._fresh_state()
                encounter._last_budget_rows = {}
                encounter._persisted_signature = None
            if need_migration:
                encounter_migration.apply(store, self.provenance, migration_plan['planId'])
            request = dict(value)
            request['config'] = config
            request['previousShares'] = dict(self._student_shares)
            request['previousTracks'] = dict(self._track_shares)
            # Rollback needs the immediately replaced mode, never that mode's own rollback chain.
            previous_mapping = store.get(encounter_search.MODE_KEY) or {}
            request['previousMapping'] = {key: item for key, item in previous_mapping.items()
                                          if key != 'previous'}
            result = encounter.activate(store, request)
            if not result.get('ok'):
                raise ValueError(result.get('error') or 'activation refused')
            saved_state = store.get('encounterStudyRuntime:%s' % encounter.session_id)
            if isinstance(saved_state, dict):
                encounter.state = saved_state
                encounter.state['sessionId'] = encounter.session_id
                encounter.state['idle'] = False
                encounter._persisted_signature = None
                encounter._persist(store)
            store.db.commit()
        except BaseException:
            store.db.rollback()
            encounter.__dict__.clear()
            encounter.__dict__.update(snapshot)
            store.compatible = compatible
            raise
        # Absolute zero for every other stream: no legacy loop competes.
        self._student_shares = {name: encounter.config['allocations'].get(name, 0.0)
                                for name in students.SHARE_STREAMS}
        self._track_shares = {track.name: 0.0 for track in students.tracks()}
        self._planner_dirty = True
        self._encounter_preview_token = None
        self._encounter_ledger = None       # the ledger may have just been created
        # NOTE: `_encounter_session_base` is deliberately NOT reset here. A study switch (the
        # campaign advancing to the next encounter, or a re-activation) is not a new user session;
        # resetting it here made the campaign-wide session count fall back to zero on every switch.
        return result

    def _outstanding_simulations(self):
        """True while a submitted battle has not yet been harvested and saved.

        ``Coordinator.busy`` (and the evaluator behind it) also report a worker that is inside its
        post-battle duty hold. That hold is deliberate pacing, and it begins only after the result has
        been persisted, so it is never an "outstanding simulation still saving". The start/switch
        guards key on real in-flight work; otherwise an already-drained campaign is refused with
        "Wait until outstanding simulations have saved." until the last duty hold expires.
        """
        encounter = self._encounter
        if encounter is None:
            return False
        evaluator = getattr(encounter, 'evaluator', None)
        return bool(getattr(evaluator, 'pending', None))

    def _loop(self):
        try:
            import queue
            from concurrent.futures import ProcessPoolExecutor
            from strategy_optimizer_limits import (clamp_duty, clamp_workers, default_workers,
                                                   initialize_worker, terminate_pool,
                                                   worker_ceiling, RUN_TIMEOUT_SECONDS)
            from strategy_optimizer_fast import HeadlessPool, runtime_path
            from strategy_dispatch import (batch_size, take_affine, task_keys, reserved_keys,
                                           ready_window, discovery_backlog, evidence_refresh_delay)
            from strategy_optimizer_adapter import (StrategyOptimizerError, default_scenario, propose,
                                                    stats, validate_scenario)
        except Exception as exc:
            self._startup_mark_failed(exc)
            with self.lock:
                self.snapshot = dict(self.snapshot, state='Error', error=str(exc),
                                     startupDiagnostics=self._startup_view())
            return
        store = None
        pool = None
        pending = []
        order = []             # the ready seeds the last planning pass produced
        plan = None            # the job sequence currently being submitted, or None to choose one
        last_publish = 0.      # monotonic time of the last snapshot handed to the desktop
        last_state = None      # the state that snapshot reported, so a change is never throttled
        dirty = False          # something changed that the desktop has not seen yet
        # Completions that arrived before their preceding seed, keyed by (candidate, phase, ordinal).
        # They are recorded in canonical order as soon as the gap fills; nothing is discarded.
        completion_buffer = {}
        ordinals = {}
        candidate_costs = {}
        # The anti-starvation controller judges the pool from a sustained window, never from one
        # instantaneous reading, so a momentary dip (a completion burst, a publish, a slow coordinator
        # pass) cannot trigger it. Each sample is `(time, cumulative idle worker-seconds, workers,
        # starved since the last sample)`, taken from the loop's own idle accounting rather than from
        # the moments the planner was awake - a saturated pool hardly ever plans, and sampling only
        # those passes would report it as idle.
        pressure_samples = []
        starved_pending = False
        last_sample = 0.
        last_reseed = 0.
        last_adaptive_probe = 0.
        last_analysis = 0.
        last_analysis_signature = None
        last_topup = 0.
        duplicates = 0         # duplicate worker responses dropped (idempotent, counted for the UI)
        state, error = 'Paused', None
        drained_state = 'Paused'
        workers, duty = default_workers(), clamp_duty(None)
        slot_ready_at = []  # one duty-cycle gate per logical worker, never one global gate
        turn = 0
        publish_due = False     # a publish is owed, and is paid after dispatch in the same pass

        def available_slot():
            return duty_ready_slot(pending, slot_ready_at, time.monotonic())

        def submit_ready(cid, phase, ordinal, scenario, slot, reserved):
            first = (cid, phase, ordinal)
            maximum = min(WORKER_BATCH_LIMIT, batch_size(candidate_costs.get(cid)))
            supported = callable(getattr(pool, 'submit_batch', None)) and maximum > 1
            keys = take_affine(order, first, reserved, maximum) if supported else [first]
            batched = len(keys) > 1
            if batched:
                future = pool.submit_batch(worker, scenario,
                                           [seed_pair(key[1], key[2]) for key in keys])
            else:
                future = pool.submit(worker, scenario, seed_pair(phase, ordinal))
            pending.append(dict(future=future, cid=cid, phase=phase, slot=slot,
                                ordinal=ordinal, seeds=keys, batched=batched,
                                started=time.monotonic()))
            reserved.update(keys)
            self._scheduler['batchRequests'] = self._scheduler.get('batchRequests', 0) + int(batched)
            self._scheduler['dispatchedSeeds'] = self._scheduler.get('dispatchedSeeds', 0) + len(keys)
            return len(keys)

        def publish_if_due():
            """Publish the snapshot, but never before the pass has fed the pool.

            The snapshot is what the desktop reads; on a mature library it costs ~0.2 s. Running it
            before the refill could leave free workers waiting on bookkeeping no worker consumes, so
            it is deferred to the end of the pass (and to the passes that skip planning, where there is
            nothing to dispatch anyway). What is published is unchanged.
            """
            nonlocal dirty, last_publish, last_state, publish_due
            if not dirty:
                publish_due = False
                return False
            now = time.monotonic()
            # A running pool often empties briefly between batches. Publishing on every such gap
            # bypassed the rate limit and made the coordinator repeatedly rebuild a large view
            # while workers waited. Stopped/paused states still publish immediately, including the
            # final saved batch; a running gap gets its next normal two-second refresh.
            if not (state != last_state or (state != 'Running' and not pending)
                    or now-last_publish >= PUBLISH_MIN_SECONDS):
                return False
            _started = time.monotonic()
            self._publish(store, state, error)
            self._scheduler['publishSeconds'] += time.monotonic()-_started
            last_publish = time.monotonic()
            last_state = state
            dirty = False
            publish_due = False
            return True
        try:
            self._startup_advance('store_open')
            self._startup_timing['libraryLoadCount'] += 1
            store = Store(self.path, self.provenance)
            # Batch the durable half of recording: accept every result immediately, write up to
            # RECORD_BATCH_SIZE of them per transaction, and never let a pending batch outlive
            # RECORD_BATCH_SECONDS (drained by the loop's own flush below and by `_publish`).
            store.batch_size = RECORD_BATCH_SIZE
            store.batch_seconds = RECORD_BATCH_SECONDS
            self._startup_advance('candidate_bootstrap_and_initial_projection')
            if not store.has_candidates() and store.compatible:
                base = default_scenario()
                # The declared search horizon comes from the search module, so the optimiser and the
                # bounded evaluation/search surface cannot drift apart. See `DEFAULT_TICK_LIMIT` for
                # the measurement behind the current value.
                import strategy_search
                base['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
                # Every encounter/difficulty gets a supplied baseline, so the search can actually
                # work on all twenty instead of only the fight the base scenario named.
                baselines = baseline_scenarios(base)
                with store.db:
                    store.set('scope', scope(base))
                    for scenario, label in baselines:
                        store.add(scenario, label, 'supplied', stats(scenario))
                if getattr(store, 'fresh', False):
                    # A brand-new library is set up Community-first. This is the DEFAULT mode, not
                    # spent work: nothing dispatches until the user starts a session, and every
                    # purpose budget below is explicit and finite. An existing library is never
                    # auto-migrated (it has no `encounterAware` mapping and stays disabled).
                    setup_config = encounter_search.default_config('community-first')
                    with store.db:
                        store.set(encounter_search.MODE_KEY, dict(
                            enabled=True, mode='community-first', config=setup_config,
                            constraints=None, encounter=None, reference=None,
                            previous=dict(shares={}, tracks={}, mapping={}),
                            note='new library default; no work spent on open'))
            self._publish(store, state)
            self._startup_advance('encounter_campaign_bootstrap')
            # The single activated ("focused") study borrows the whole planner pool and the whole
            # battle pool: its coordinator gets the shared host and the effective battle worker size,
            # while the requested total is split once into planner + battle workers.
            self._encounter = encounter_search.Coordinator(
                path=self.path, revision=self.battleCompatibilityRevision,
                scheduler_revision=self.runtimeRevision,
                workers=self._battle_worker_count(workers), telemetry=True, maximum=12)
            self._encounter.planning_host = self
            self._encounter.load(store)
            if self._encounter.enabled:
                self._student_shares = {name: self._encounter.config['allocations'].get(name, 0.0)
                                        for name in students.SHARE_STREAMS}
                self._track_shares = {track.name: 0.0 for track in students.tracks()}
            self._encounter_report = self._encounter.report()
            # The automatic ALL-ENCOUNTER Community campaign. It reuses THIS coordinator's atomic
            # preview/activate pair, so a resumed campaign coalesces onto the same budget session.
            # Opening a library only LOADS its status; nothing activates until an explicit
            # `community_start`, so a restart can never stomp a manually activated study.
            catalog = sorted((int(key) for key in (self.encounters or {})), key=int)
            self._campaign = community_campaign.CommunityCampaign(
                encounter_ids=catalog,
                preview=lambda value: self._preview_encounter(store, value),
                activate=lambda value: self._activate_encounter(store, value),
                commit=store.db.commit, focus=self._community_focus(store))
            self._campaign_started = False
            self._campaign_report = self._campaign.status_view(store, self._encounter_report)
            self._startup_mark_ready()
            while (not self.shutdown.is_set() or pending
                   or (self._encounter is not None and self._encounter.enabled
                       and self._encounter.busy)):
                acknowledged = None
                try:
                    # A finished battle only frees its slot when harvested. Even a 4 ms command
                    # wait on every pass costs a large share of a 50 ms battle when completions
                    # arrive across 24 workers. Poll briefly while all slots are occupied, and
                    # do not wait at all when an authorised seed can fill an idle slot.
                    action, value, acknowledged = self.commands.get(
                        timeout=(0 if state == 'Running' and order and len(pending) < workers
                                 else .001 if pending or (self._encounter.enabled and
                                      (state == 'Running' or self._encounter.busy)) else .10))
                    if (self._encounter.enabled and action in (
                            'students', 'track', 'fine_tune', 'probe', 'focused_experiment',
                            'end_experiment', 'breakthrough_budget', 'mp_recovery',
                            'migrate_objective', 'all_encounters', '_record_manual_trials')):
                        raise ValueError('Use the encounter search budgets and operating-region '
                                         'questions while this mode is active.')
                    if action == 'start':
                        self._publication_failure = None
                        if not store.compatible:
                            raise ValueError('Simulator changed: create a new library before starting.')
                        # Search-space versioning: a library generated under a different contract is a
                        # different experiment. Continuing it here would mix incompatible assumptions
                        # (its candidates were produced under rules this generator no longer follows),
                        # so the search refuses to start and says exactly that instead of doing it
                        # silently. The stored runs stay readable; only new proposals are refused.
                        if int(store.get('searchSpaceVersion', 1)) != search_contract.SEARCH_SPACE_VERSION:
                            raise ValueError(
                                f'This library was generated under search-space version '
                                f'{store.get("searchSpaceVersion", 1)}; this optimiser generates '
                                f'version {search_contract.SEARCH_SPACE_VERSION} (approved skill set, '
                                f'9-skill and no-duplicate rules, team-wide 7-Hit cap, conditional INT, '
                                f'weapon behaviour groups, and variable synthetic teams). Open a new library for a search under the '
                                f'new contract; the stored runs here remain readable.')
                        if pending or self._outstanding_simulations():
                            raise ValueError('Wait until outstanding simulations have saved.')
                        workers = clamp_workers(value.get('workers'))
                        duty = clamp_duty(value.get('duty'))
                        if self._encounter.enabled:
                            self._encounter.configure_duty(duty)
                        slot_ready_at = [0.] * workers
                        self._workers, self._duty = workers, duty
                        self._rate_samples = []
                        if pool:
                            pool.shutdown(wait=True)
                        pool = None
                        if not self._encounter.enabled:
                            pool = (HeadlessPool(workers) if runtime_path().is_file() else
                                    ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker))
                        state, error = 'Running', None
                        active_experiment = store.get(strategy_experiments.KEY) or {}
                        if not self._encounter.enabled and active_experiment.get('status') == 'active':
                            self._focus_encounter = active_experiment['encounterId']
                        fresh_session = not self._session_live
                        if fresh_session:
                            # A Start after Stop (or the first Start) begins a new session: elapsed
                            # time and the session run count restart from zero.
                            self._session_live = True
                            self._session_active = 0.
                            self._session_runs_base = store.get('totalRuns', 0)
                            self._scheduler = self._blank_scheduler()
                            del pressure_samples[:]
                            last_reseed = 0.
                            # A new session starts from a fresh planner view: the ready list and the
                            # evidence snapshot are rebuilt on the first pass rather than resumed.
                            self._planner_dirty = True
                            self._planner_evidence['value'] = None
                        if (self._encounter.enabled
                                and (fresh_session or self._encounter_session_base is None)):
                            # Encounter sessions re-base their published run count at the first Start
                            # that owns the mode, and reset the rolling rate samples with it. The base
                            # is the CAMPAIGN-WIDE completed ledger, not the per-study budget rows
                            # (which reset at every study switch). A resume keeps both, because it is
                            # the same session.
                            self._encounter_session_base = int(
                                (self._encounter_overview(store).get('counts') or {}).get('total') or 0)
                        if self._encounter.enabled:
                            completed_now = int((self._encounter_overview(store).get('counts') or {}
                                                 ).get('total') or 0)
                            self._reset_encounter_rate_samples(completed_now)
                        self._resume_session_clock()
                    elif action == 'community_start':
                        # The normal one-button Start for the automatic ALL-ENCOUNTER Community
                        # campaign. It activates (or resumes) the finite campaign through the real
                        # atomic preview/activate path - there is no UI wizard. The busy/drain guards
                        # match `start`, but an incompatible library is NOT rejected here up front:
                        # the module takes the reviewed simulator-migration path (never bypassing the
                        # certificate) and pauses with a reason when the migration is not eligible.
                        if self._campaign is None:
                            raise ValueError('This library has no Community campaign runtime yet.')
                        value = value if isinstance(value, dict) else {}
                        workers = clamp_workers(value.get('workers', self._workers))
                        duty = clamp_duty(value.get('duty', self._duty))
                        resize_in_progress = isinstance(
                            getattr(self, '_campaign_resize_pending', None), dict)
                        same_scope = self._campaign_resize_scope_matches(store)
                        resize_requested = (same_scope and
                                            (workers != self._workers or duty != self._duty
                                             or resize_in_progress))
                        if ((pending or self._outstanding_simulations())
                                and not resize_requested):
                            raise ValueError('Wait until outstanding simulations have saved.')
                        previous_campaign = (self._campaign_report or {}).get('campaignId')
                        directive = self._campaign_start(store, value)
                        if isinstance(getattr(self, '_campaign_resize_pending', None), dict):
                            self._scheduler['workerAllocation'] = self._worker_allocation_status()
                            dirty = True
                        if directive.get('status') in ('complete', 'paused', 'refused', 'none'):
                            state, error = 'Paused', (directive.get('reason')
                                                      or directive.get('note'))
                        else:
                            self._publication_failure = None
                            # `_campaign_start` owns the authoritative requested total/split; align
                            # the loop-local values with what it actually applied so the evaluator is
                            # sized on the same effective battle count.
                            workers = getattr(self, '_workers', workers)
                            duty = getattr(self, '_duty', duty)
                            if self._encounter.enabled:
                                self._encounter.configure_duty(duty)
                            self._workers, self._duty = workers, duty
                            self._encounter.configure_workers(
                                self._battle_worker_count(workers))
                            if not self._session_live or directive.get('campaignId') != previous_campaign:
                                self._session_live = True
                                self._session_active = 0.
                                self._session_runs_base = store.get('totalRuns', 0)
                                self._scheduler = self._blank_scheduler()
                                del pressure_samples[:]
                                # The first Start (or a Start after Stop) re-bases the published run
                                # count on the campaign-wide ledger; a pause/resume keeps it. Without
                                # this the community path published sessionRuns 0 while totalRuns grew.
                                if self._encounter is not None and self._encounter.enabled:
                                    self._encounter_session_base = int(
                                        (self._encounter_overview(store).get('counts') or {}
                                         ).get('total') or 0)
                                    del self._encounter_rate_samples[:]
                            if state != 'Running':
                                completed_now = int((self._encounter_overview(store).get('counts') or {}).get('total') or 0)
                                self._reset_encounter_rate_samples(completed_now)
                            self._resume_session_clock()
                            state, error = 'Running', None
                    elif action in ('pause', 'stop', 'close'):
                        if action in ('stop', 'close') and isinstance(
                                getattr(self, '_campaign_resize_pending', None), dict):
                            pending_resize = self._campaign_resize_pending
                            self._campaign_resize_pending = None
                            self._worker_resize_status = dict(
                                state='cancelled',
                                requestedWorkers=pending_resize.get('workers'),
                                requestedDuty=pending_resize.get('duty'),
                                completedAt=time.time(), reason='campaign-stopped')
                        self._hold_session_clock(ended=action != 'pause')
                        drained_state = 'Stopped' if action != 'pause' else 'Paused'
                        state = 'Saving' if pending or self._outstanding_simulations() else drained_state
                        self._campaign_drain_status = ('paused' if action == 'pause' else 'stopped')
                        if action == 'close':
                            self.shutdown.set()
                        if action in ('stop', 'close'):
                            # Stop means stop: cancel queued planner jobs and release their owners
                            # now, so no planner result computed for a stopped run can admit work.
                            self._cancel_all_planning(store)
                        if state != 'Saving':
                            self._campaign_finish(store, self._campaign_drain_status)
                            self._campaign_drain_status = None
                    elif action == 'students':
                        # The students' split of the pool. A setting, never a candidate: it decides what
                        # is dispatched next, so it cannot reach a battle, cannot change a stored result
                        # and cannot invalidate a library. `shares` may be a partial mapping; the rest
                        # keeps its current value and the total is normalised.
                        requested = value.get('shares') if isinstance(value, dict) else None
                        if requested is not None:
                            self._student_shares = students.shares(requested)
                        # How a student's own share divides across its lines. Student 1's tracks are
                        # separate lines with separate budgets, so "the Attack track" can be the only
                        # thing running without disturbing the rest of Student 1's share.
                        wanted_tracks = value.get('tracks') if isinstance(value, dict) else None
                        if wanted_tracks is not None:
                            self._track_shares = students.track_shares(wanted_tracks)
                        self._student_warning = students.share_warning(self._student_shares)
                    elif action == 'student_report':
                        # Expensive enough to be asked for rather than published every two seconds: it
                        # reads the whole evidence table so the answer is measured runs, not an estimate.
                        try:
                            self._student_runs = students.run_report(store)
                            # The rule-break ledger travels with the counts, because "25% went to
                            # student 2" hides which rebel actually spent it: only the ledger says
                            # which tier, which version and which rules were broken for that budget.
                            self._student_ledger = students.rebel_report(store)
                            # And the same question for Student 1: which of its tracks spent the share,
                            # and which pairs of values the pair track has actually got to.
                            self._student_tracks = students.track_report(store)
                            self._pair_coverage = [
                                dict(encounter=encounter, **students.pair_coverage(store, encounter))
                                for encounter in sorted(set(store.candidate_encounters().values()))]
                        except Exception as exc:
                            self._student_runs = dict(error=repr(exc))
                    elif action == 'import':
                        if pending or state == 'Running':
                            raise ValueError('Pause and let outstanding runs save before importing a build.')
                        scenario = validate_scenario(value['scenario'])
                        # A library that already holds several encounters accepts an import for any
                        # of them when everything except the encounter id matches: that is how a
                        # multi-encounter library takes in a supplied build without mixing horizons
                        # or source conditions. A single-encounter library is unchanged.
                        wanted = json.loads(scope(scenario))
                        current = stored_scope(store)
                        peers = {json.loads(c['scenario'])['encounterId'] for c in store.candidates()}
                        shares = (scenario['encounterId'] in peers and len(peers) > 1 and
                                  {key: value_ for key, value_ in wanted.items()
                                   if key != 'encounterId'} ==
                                  {key: value_ for key, value_ in current.items()
                                   if key != 'encounterId'})
                        if scope(scenario) != store.get('scope') and not shares:
                            if store.get('totalRuns') == 0 and len(store.candidates()) == 1:
                                with store.db:
                                    store.db.execute('DELETE FROM candidate')
                                    store.set('scope', scope(scenario))
                                    store.reset_equivalence()
                            else:
                                raise ValueError('Encounter, horizon and source conditions must match this library. '
                                                 'Create a new library and import this scenario before starting.')
                        with store.db:
                            store.add(scenario, value['label'], 'supplied', stats(scenario))
                        error = None
                    elif action == 'all_encounters':
                        if pending or state == 'Running':
                            raise ValueError('Pause before expanding the encounter set.')
                        # The library's own base scenario carries its horizon and conditions, so the
                        # expansion adds fights at the scope this library already declares - an old
                        # 7,000-tick library stays a 7,000-tick library.
                        base = json.loads(store.candidates()[0]['scenario'])
                        with store.db:
                            for raw, label in baseline_scenarios(base):
                                scenario = validate_scenario(raw)
                                store.add(scenario, label, 'supplied', stats(scenario))
                    elif action == 'migrate_objective':
                        # Part J: the lane objective is a different objective, so a library written
                        # under the old one is upgraded only when asked, never silently on open.
                        if pending or state == 'Running':
                            raise ValueError('Pause before upgrading the objective.')
                        migrate_objective(store)
                        error = None
                    elif action == 'focus_encounter':
                        # Two scheduling-only focus styles share this command:
                        #  * the automatic campaign's multi-select (`encounterIds`, [] = all), and
                        #  * the legacy single encounter pick (`encounterId`).
                        # Neither rewrites recorded results nor allocates budget. The multi-select is
                        # a campaign filter that switches only at the campaign's safe boundary, so it
                        # is legal while the automatic campaign is running.
                        catalogue = {int(key) for key in (self.encounters or {})}
                        if isinstance(value, dict) and 'encounterIds' in value:
                            selected = normalize_focus_encounters(value.get('encounterIds'), catalogue)
                            self._community_set_focus(store, selected)
                            if self._campaign is not None:
                                self._campaign.set_focus(store, selected)
                                self._campaign_report = self._campaign.status_view(
                                    store, self._encounter_report)
                            self._planner_dirty = True
                            error = None
                        else:
                            new_focus = normalize_focus_encounter(
                                (value or {}).get('encounterId'), catalogue)
                            if self._encounter.enabled:
                                raise ValueError('Pause and choose the encounter in the encounter '
                                                 'search configuration before starting a new session.')
                            experiment = store.get(strategy_experiments.KEY) or {}
                            if (experiment.get('status') == 'active'
                                    and new_focus != experiment['encounterId']):
                                raise ValueError('End the focused experiment before changing encounter.')
                            self._focus_encounter = new_focus
                            # An explicit focus is also the signal to tune it: start the frozen-parent
                            # programs for the strongest credibly measured build now. Clearing the
                            # focus publishes the wait state; refocusing resumes the same programs
                            # without duplicating them.
                            if self._auto_tune(store):
                                dirty = True
                            # Rebuild the ready list on the next pass so the switch is atomic: the
                            # seeds already dispatched stay in flight and finish, but every seed
                            # chosen after this command comes from the new scope.
                            self._planner_dirty = True
                            self._planner_evidence['value'] = None
                            error = None
                    elif action == 'new':
                        raise ValueError('Close this window and launch with another library path.')
                    elif action == 'keep':
                        with store.db:
                            cursor = store.db.execute("UPDATE candidate SET source='saved' WHERE id=?", (value['id'],))
                            if not cursor.rowcount:
                                raise ValueError('This candidate is no longer in the library.')
                    elif action == 'probe':
                        if pending and state == 'Running':
                            raise ValueError('Pause the search before starting a range probe.')
                        error, created = self._create_probe(store, value)
                        self._last_probe = created
                    elif action == 'fine_tune':
                        # Register a controlled frozen-parent experiment. Like `probe` it is a bounded
                        # library write; unlike `probe` it advances only while its own encounter is the
                        # focused one, so every attempt it authorises stays on the selected fight.
                        if pending and state == 'Running':
                            raise ValueError('Pause the search before starting a fine-tuning program.')
                        error, created = self._create_finetune(store, value)
                        self._last_finetune = created
                    elif action == 'focused_experiment':
                        if pending or state == 'Running':
                            raise ValueError('Pause and save outstanding battles before starting an experiment.')
                        job = strategy_experiments.create(store, value.get('candidateId'),
                                                         value.get('count'), value.get('mode', 'evaluate'))
                        self._focus_encounter = job['encounterId']
                        order.clear()
                        self._planner_dirty = True
                        self._planner_evidence['value'] = None
                        error = None
                    elif action == '_record_manual_trials':
                        # Internal bridge command only. Replays never call this; a fresh visual or
                        # small legacy batch records the next normal validation seeds while paused.
                        if pending or state == 'Running':
                            raise ValueError('Pause and save before recording manual trials.')
                        cid = value['candidateId']
                        if not store.compatible or store.scenario(cid) is None:
                            raise ValueError('This build cannot receive evidence in the current library.')
                        start = int(value['start'])
                        results = value['results']
                        for offset, result in enumerate(results):
                            if list(result.get('seeds') or []) != list(seed_pair('validation', start + offset)):
                                raise ValueError('Manual result seed does not match its validation ordinal.')
                        if start > store.next_ordinal(cid, 'validation'):
                            raise ValueError('Manual trial bank has a gap.')
                        for offset, result in enumerate(results):
                            store.record(cid, 'validation', start + offset, result)
                        store.flush()
                        self._planner_ordinals = None
                        self._planner_dirty = True
                        self._planner_evidence['value'] = None
                        error = None
                    elif action == 'end_experiment':
                        if pending or state == 'Running':
                            raise ValueError('Pause and save outstanding battles before ending an experiment.')
                        strategy_experiments.finish(store, 'ended')
                        order.clear()
                        self._planner_dirty = True
                        self._planner_evidence['value'] = None
                        error = None
                    elif action == 'breakthrough_budget':
                        # The explicit extension/resume control for one encounter's Breakthrough
                        # campaign. It is a scheduling change like `students` or `focus_encounter`, not
                        # a battle, so it is legal while running - and it only ever raises the cap and
                        # clears a budget pause. Whether the runs may then be dispatched is still the
                        # share gate's decision, so this is not a way around a disabled stream.
                        import strategy_breakthrough
                        result = strategy_breakthrough.extend_budget(
                            store, value.get('encounterId'), value.get('runs'))
                        if not result.get('ok'):
                            raise ValueError(result.get('error') or 'the budget extension was refused')
                        self._breakthrough_status = strategy_breakthrough.status(store)
                        error = None
                    elif action == 'mp_recovery':
                        # The MP-recovery allowance: whether the optimiser may create herb-supported
                        # corrections at all, the stock and per-battle use cap they may declare, the
                        # comparable bank each is measured over, and how many may exist at once. A
                        # running-time setting like `students`, not a library write: it changes what is
                        # authorised next and never reaches a battle by itself. Persisted so a restart
                        # resumes with the same permission.
                        import strategy_mp_recovery
                        result = strategy_mp_recovery.configure(store, value)
                        if not result.get('ok'):
                            raise ValueError(result.get('error') or 'the allowance was refused')
                        self._mp_recovery_status = strategy_mp_recovery.status(store)
                        self._planner_dirty = True
                        error = None
                    elif action in ('encounter_preview', 'encounter_activate',
                                    'encounter_question', 'encounter_rollback',
                                    'encounter_budget'):
                        # The desktop API contract for the Community-first runtime. `preview` is a
                        # pure plan; the mutating commands require a paused, drained coordinator.
                        if self._encounter is None:
                            raise ValueError('This library has no encounter runtime yet.')
                        value = value if isinstance(value, dict) else {}
                        if action == 'encounter_preview':
                            preview = self._preview_encounter(store, value)
                            error = None
                        else:
                            if pending or state in ('Running', 'Saving') or self._outstanding_simulations():
                                raise ValueError('Pause optimisation and wait for outstanding '
                                                 'simulations to save first.')
                            if action == 'encounter_activate':
                                if self._encounter.enabled:
                                    raise ValueError('Encounter search is already active. Update '
                                                     'its budgets or roll it back before changing mode.')
                                self._encounter_previous_shares = dict(self._student_shares)
                                self._encounter_previous_tracks = dict(self._track_shares)
                                result = self._activate_encounter(store, value)
                                self._planner_dirty = True
                                error = None
                            elif action == 'encounter_rollback':
                                result = self._rollback_encounter(store)
                                error = None
                            elif action == 'encounter_question':
                                result = self._encounter.set_question(store, value)
                                if not result.get('ok'):
                                    raise ValueError(result.get('error') or 'question refused')
                                error = None
                            else:
                                result = self._encounter.set_budget(store, value)
                                if not result.get('ok'):
                                    raise ValueError(result.get('error') or 'budget refused')
                                error = None
                        self._encounter_report = self._encounter.report()
                    self._publish(store, state, error)
                except queue.Empty:
                    pass
                except Exception as exc:
                    error = str(exc)
                    self._publish(store, state, error)
                finally:
                    if acknowledged:
                        acknowledged.set()

                # Harvest every finished job, in whatever order the workers produced them. Order is
                # restored per (candidate, phase) stream by the completion buffer below, so a fast
                # worker is neither made to wait for a slow one nor able to advance the persisted
                # seed cursor past a gap.
                finished = [task for task in pending if task['future'].done()]
                for task in finished:
                    pending.remove(task)
                    # A duty setting applies to each worker's own battle time. The previous shared
                    # gate serialised every completion behind one cooldown, reducing a 24-worker
                    # pool to only a few active workers at the default 90% duty.
                    try:
                        result = task['future'].result()
                        results = result if task.get('batched') else [result]
                        keys = task_keys(task)
                        if len(results) != len(keys):
                            raise ValueError('Batch result count differs from its reserved seeds')
                    except Exception as exc:
                        slot_ready_at[task['slot']] = time.monotonic()
                        # The coordinator stopped running work, so the active clock stops with it.
                        self._hold_session_clock(ended=False)
                        state, error = 'Paused', f'Simulation failed; saved work intact: {exc}'
                        continue
                    timings = [row.get('elapsedSeconds') for row in results]
                    busy_seconds = (sum(max(0., float(value)) for value in timings)
                                    if all(isinstance(value, (int, float)) for value in timings)
                                    else max(0., time.monotonic()-task['started']))
                    slot_ready_at[task['slot']] = time.monotonic()+duty_hold_seconds(busy_seconds, duty)
                    observed_cost = busy_seconds / len(results)
                    previous_cost = candidate_costs.get(task['cid'], observed_cost)
                    candidate_costs[task['cid']] = .5 * previous_cost + .5 * observed_cost
                    for key, row in zip(keys, results):
                        completion_buffer[key] = (row, task['started'])

                # Flush each stream's now-contiguous prefix of buffered completions, ascending. A
                # duplicate response for an ordinal the store already holds is dropped, and a gap
                # (a seed still running, retried, or failed) simply leaves the later ones buffered.
                for cid, phase in sorted({(cid, phase) for cid, phase, _ in completion_buffer}):
                    next_expected = store.next_ordinal(cid, phase)
                    for key in [key for key in completion_buffer
                                if key[0] == cid and key[1] == phase and key[2] < next_expected]:
                        completion_buffer.pop(key)
                        duplicates += 1
                    while True:
                        entry = completion_buffer.pop((cid, phase, next_expected), None)
                        if entry is None:
                            break
                        result, started = entry
                        try:
                            _record_started = time.monotonic()
                            store.record(cid, phase, next_expected, result)
                            self._scheduler['recordSeconds'] += time.monotonic()-_record_started
                        except Exception as exc:
                            self._hold_session_clock(ended=False)
                            state, error = 'Paused', f'Simulation failed; saved work intact: {exc}'
                            break
                        next_expected += 1
                        # The ordinal map is the coordinator's own bookkeeping and moves with the
                        # accepted run, so the next planning pass can see the seed as taken without
                        # re-reading the counters.
                        ordinals[(cid, phase)] = next_expected
                        # The seed this run just authorised joins the live ready list, so the next
                        # pass can feed the pool without recomputing the population's seed list.
                        self._schedule_completion(cid, phase, next_expected-1)
                        engine_seconds = result.get('elapsedSeconds') if isinstance(result, dict) else None
                        if isinstance(engine_seconds, (int, float)):
                            self._engine_samples.append((time.monotonic(), engine_seconds))
                        # One sample per completed battle: the only place a battle is known to be
                        # finished, and therefore the only honest source for a throughput reading.
                        self._rate_samples.append((time.monotonic(), store.get('totalRuns', 0)))
                        cutoff = time.monotonic()-RATE_WINDOW_SECONDS
                        while len(self._rate_samples) > 1 and self._rate_samples[0][0] < cutoff:
                            self._rate_samples.pop(0)
                        while self._engine_samples and self._engine_samples[0][0] < cutoff:
                            self._engine_samples.pop(0)
                if finished:
                    if not pending and not self._encounter.busy and state == 'Saving':
                        state = drained_state
                        if self._campaign_drain_status is not None:
                            self._campaign_finish(store, self._campaign_drain_status)
                            self._campaign_drain_status = None
                    dirty = True
                # The record batch is drained *after* dispatch, never before it (see `flush_records`
                # at the end of the pass): a worker is handed its next battle before the coordinator
                # spends time on the database, which is the whole point of decoupling the two.
                # One snapshot per harvest, rate-limited, instead of one per completed battle: a
                # snapshot of a large library costs tens of milliseconds on the same thread that
                # feeds the pool, and the desktop polls it every two seconds, so publishing per run
                # was pure overhead against the search. Persistence is never delayed - every
                # finished run is written above, before this.
                #
                # A state change is never throttled. In particular the last write of a paused
                # batch changes Saving to Paused, so the desktop cannot remain on "Saving".
                # The published scheduler must describe the pool as it is *now*, not as it was one
                # pass ago: these counters used to be refreshed after the snapshot was taken, so an
                # idle pool could keep showing its last in-flight seed forever and a genuinely
                # stalled worker was indistinguishable from a finished reservoir.
                self._scheduler.update(workers=workers, busy=len(pending),
                                       idle=max(0, workers-len(pending)), pending=len(pending),
                                       buffered=len(completion_buffer), duplicates=duplicates)
                # Direct activity reading, plus the idle split: idle while ready work existed (the
                # coordinator thread was the reason a worker waited) versus idle with nothing
                # authorised to hand out.
                self._scheduler.update(self.worker_occupancy(workers))
                self._scheduler['idleCoordinatorSeconds'] = round(self._idle_coordinator_seconds, 2)
                self._scheduler['idleNoWorkSeconds'] = round(self._idle_no_work_seconds, 2)
                self._scheduler['coordinatorBusySeconds'] = round(
                    (self._scheduler.get('planSeconds', 0.)+self._scheduler.get('publishSeconds', 0.)
                     +self._scheduler.get('flushSeconds', 0.)
                     +self._scheduler.get('recordSeconds', 0.)), 2)
                self._scheduler['inflight'] = [
                    dict(cid=task['cid'], phase=task['phase'], ordinal=task['ordinal'],
                         batchSize=len(task_keys(task)),
                         seconds=round(time.monotonic()-task['started'], 2))
                    for task in sorted(pending, key=lambda task: task['started'])][:12]
                if dirty and (state != last_state or (state != 'Running' and not pending)
                              or time.monotonic()-last_publish >= PUBLISH_MIN_SECONDS):
                    if DEFER_PUBLISH:
                        publish_due = True
                    else:
                        _started = time.monotonic()
                        self._publish(store, state, error)
                        self._scheduler['publishSeconds'] += time.monotonic()-_started
                        last_publish = time.monotonic()
                        last_state = state
                        dirty = False
                if not pending:
                    self._work_started = None

                if any(time.monotonic()-task['started'] > RUN_TIMEOUT_SECONDS for task in pending):
                    terminate_pool(pool)
                    pool = None
                    pending.clear()
                    self._hold_session_clock(ended=False)
                    state, error = 'Paused', 'Worker exceeded 180 seconds; completed results are safe. Resume retries the same seeds.'
                    self._publish(store, state, error)

                # ---- Community-first encounter runtime --------------------------------------
                # While this mode is enabled, the coordinator is the ONLY producer. It is reached
                # before every legacy experiment / planner / average / track / MP-recovery /
                # reservation / fallback path below, and the loop `continue`s, so no old loop can
                # compete for a worker. Harvest and persistence still run while paused or saving,
                # so a pause is a real coordinator pause rather than a dropped in-flight battle.
                if self._encounter is not None and self._encounter.enabled:
                    self._scheduler['lastPass'] = time.monotonic()
                    fleet_report_fresh = False
                    # The requested total is split into effective battle + planner workers; the
                    # battle evaluator is sized on the effective battle count, never the raw total.
                    battle_workers = self._battle_worker_count(workers)
                    self._encounter.configure_workers(battle_workers)
                    # Route every finished planner group exactly once, on this thread, before any
                    # pass can decide or dispatch. `harvest_ready` is non-blocking, so a slow or
                    # absent planner never delays commands, dispatch or another encounter.
                    before_signature = self._encounter_publish_signature(
                        self._encounter_report, state, error)
                    try:
                        if self._campaign_started and self._campaign_coordinators:
                            resize_draining = isinstance(
                                getattr(self, '_campaign_resize_pending', None), dict)
                            self._campaign_fleet_pass(
                                store, running=(state == 'Running' and not resize_draining))
                            fleet_report_fresh = True
                            if resize_draining and self._apply_campaign_resize_if_drained(store):
                                workers, duty = self._workers, self._duty
                                battle_workers = self._battle_worker_count(workers)
                                self._scheduler['workers'] = battle_workers
                                self._scheduler['workerAllocation'] = (
                                    self._worker_allocation_status())
                                fleet_report_fresh = False
                                dirty = True
                            # The fleet pass harvested planners at its final safe boundary.
                            # Defer later completions to the next pass so this report stays fresh.
                        else:
                            # The single-coordinator path preserves its existing planning order.
                            self._harvest_planning(store)
                            self._encounter_report = self._encounter.run_pass(
                                store, running=(state == 'Running'))
                    except Exception as exc:  # noqa: BLE001 - report, keep saved work
                        self._hold_session_clock(ended=False)
                        if _is_sqlite_full(exc):
                            detail = storage_full_message(self.path, store.db, MAX_DB_MB, exc)
                            state, error = 'Paused', f'Encounter search paused on SQLite SQLITE_FULL; {detail}'
                        else:
                            state, error = 'Paused', f'Encounter search failed; saved work intact: {exc}'
                        self._encounter_report = self._encounter.report()
                        self._publish(store, state, error)
                        continue
                    # Surface encounter-search preparation/coordinator cost beside direct worker
                    # occupancy so a low battle rate can be attributed during the live run.
                    encounter_timings = (self._encounter_report or {}).get('timings') or {}
                    self._scheduler['communityProposalSeconds'] = encounter_timings.get('proposalSeconds')
                    self._scheduler['communityLastPassSeconds'] = encounter_timings.get('lastPassSeconds')
                    self._scheduler['communityPreparation'] = encounter_timings.get('preparationDiagnostics')
                    self._scheduler['communityProposalWorkers'] = encounter_timings.get('proposalWorkers')
                    self._scheduler['communityStagesSeconds'] = encounter_timings.get('fleetStagesSeconds')
                    self._scheduler['communityCoordinatorPassSeconds'] = encounter_timings.get('fleetPassSeconds')
                    # Automatic ALL-ENCOUNTER campaign supervision. It advances only at a safe
                    # boundary and only while THIS process started the campaign and the host is
                    # Running; a completion/focus-pause/block stops the clock and runtime.
                    supervised_state, supervise_error = self._campaign_supervise(
                        store, state, reuse_fresh_report=fleet_report_fresh)
                    state = supervised_state
                    if supervise_error is not None:
                        error = supervise_error
                    campaign_status = self._campaign_report or {}
                    self._scheduler['communityRunnableEncounters'] = campaign_status.get('runnableEncounters')
                    self._scheduler['communityActiveExperiments'] = campaign_status.get('activeExperiments')
                    # Throughput is sampled from the CAMPAIGN-WIDE completed ledger, so a study
                    # switch (which resets the per-study budget rows) can never produce a negative
                    # delta; append only on a new high-water mark.
                    progress = int((self._encounter_overview(store).get('counts') or {}
                                    ).get('total') or 0)
                    self._sample_encounter_rate(progress, running=(state == 'Running'))
                    inflight = (self._encounter.evaluator.pending
                                if self._encounter.evaluator is not None else {})
                    self._scheduler.update(workers=battle_workers, busy=len(inflight),
                                           idle=max(0, battle_workers-len(inflight)),
                                           pending=len(inflight),
                                           buffered=0, duplicates=duplicates)
                    # The legacy pool is empty in Community mode. Measure this evaluator's actual
                    # open tasks instead, otherwise activeWorkers remains permanently zero.
                    now = time.monotonic()
                    self._active_window.append((now, len(inflight)))
                    while len(self._active_window) > 1 and now-self._active_window[0][0] > 10.:
                        self._active_window.pop(0)
                    self._scheduler.update(self.worker_occupancy(battle_workers))
                    self._scheduler['workerAllocation'] = self._worker_allocation_status()
                    self._scheduler['inflight'] = [dict(
                        cid=job.get('candidateId'), phase='encounter', batchSize=1,
                        seconds=job.get('seconds'))
                        for job in self._encounter_evaluator(self._encounter_report).get('jobs', [])][:24]
                    if inflight and self._work_started is None:
                        self._work_started = now
                    if not inflight:
                        self._work_started = None
                        if state == 'Saving':
                            state = drained_state
                            if self._campaign_drain_status is not None:
                                self._campaign_finish(store, self._campaign_drain_status)
                                self._campaign_drain_status = None
                    # Event-driven: publish only when the report, the counters, the pool or the loop
                    # state actually moved. This branch used to set `dirty` unconditionally and then
                    # publish on every paused iteration even with nothing new to show. State changes
                    # (including the final Saving->Paused drain) still differ in the signature, and an
                    # error/migration/resume still publishes, so a final status is never lost.
                    if self._encounter_publish_signature(
                            self._encounter_report, state, error) != before_signature:
                        dirty = True
                    publish_if_due()
                    if self.shutdown.is_set() and not inflight:
                        break
                    continue
                if state != 'Running' or self.shutdown.is_set():
                    if publish_due:
                        publish_if_due()
                    self._scheduler['lastPass'] = time.monotonic()
                    continue
                # Idle-worker seconds: the share of the session the machine spent waiting for
                # runnable work, which is what the utilisation reading is actually measuring.
                _now = time.monotonic()
                _dt = _now-self._scheduler['lastPass'] if self._scheduler['lastPass'] else 0.
                self._scheduler['lastPass'] = _now
                _idle = max(0, workers-len(pending))*max(0., _dt)
                self._scheduler['idleWorkerSeconds'] += _idle
                # Attribute that idle time by what the PREVIOUS pass left behind, because the workers
                # were idle *during* this interval: ready work existed (so the coordinator thread was
                # the reason they waited) or it did not (so there was nothing authorised to hand out).
                if _idle:
                    if self._pass_had_ready:
                        self._idle_coordinator_seconds += _idle
                    else:
                        self._idle_no_work_seconds += _idle
                self._pass_coordinator_seconds = 0.
                self._pass_had_ready = bool(order)
                # One occupancy sample per pass: the active count is `len(pending)`, i.e. tasks whose
                # lifetime is currently open.
                self._active_window.append((_now, len(pending)))
                while len(self._active_window) > 1 and _now-self._active_window[0][0] > 10.:
                    self._active_window.pop(0)
                # One utilisation sample every 250 ms of running time, carrying the scheduler's own
                # cumulative idle seconds so the reading covers every pass, not just the planning
                # ones. `starved_pending` accumulates across passes so a brief moment with free workers
                # and nothing to submit is never lost between samples.
                if _now-last_sample >= .25:
                    pressure_samples.append((_now, self._scheduler['idleWorkerSeconds'], workers,
                                             starved_pending,
                                             (self._scheduler['planSeconds']
                                              + self._scheduler['recordSeconds']
                                              + self._scheduler['publishSeconds'])))
                    starved_pending = False
                    last_sample = _now
                    if len(pressure_samples) > 400:
                        del pressure_samples[:-400]
                # Explicit user experiments use the same pool, seed streams, completion buffer,
                # Store.record, counters and learner evidence as search. No frontend polling loop
                # owns their lifetime. Restrict dispatch to the saved arms until the budget drains.
                experiment = store.get(strategy_experiments.KEY) or {}
                if experiment.get('status') == 'active':
                    reserved = reserved_keys(pending) | set(completion_buffer)
                    limits = {arm['candidateId']: (0, arm['target']) for arm in experiment['arms']}
                    experiment_ordinals = {(cid, 'validation'): store.next_ordinal(cid, 'validation')
                                           for cid in limits}
                    ordinals = experiment_ordinals
                    self._planner_ordinals = ordinals
                    order, _ = ready_window(limits, experiment_ordinals, reserved,
                                            maximum=max(workers * 48, 48))
                    self._planner_order = order
                    self._planner_limits = limits
                    self._planner_dirty = False
                    while order and len(pending) < workers and not self.shutdown.is_set():
                        slot = available_slot()
                        if slot is None:
                            break
                        cid, phase, ordinal = order.pop(0)
                        scenario = self._planner_scenarios.get(cid)
                        if scenario is None:
                            scenario = store.observed_scenario(store.scenario(cid), candidate=cid)
                            self._planner_scenarios[cid] = scenario
                        if scenario is None:
                            raise ValueError('An experiment build is missing; saved results are intact.')
                        submit_ready(cid, phase, ordinal, scenario, slot, reserved)
                    if self.flush_records(store, pending):
                        dirty = True
                    if not pending and all(store.next_ordinal(cid, 'validation') >= limit[1]
                                           for cid, limit in limits.items()):
                        store.flush()
                        strategy_experiments.finish(store)
                        self._hold_session_clock(ended=False)
                        state = 'Paused'
                        self._planner_dirty = True
                        self._planner_evidence['value'] = None
                        dirty = True
                    if dirty:
                        publish_if_due()
                    continue
                # The previous pass already authorised these seeds. Refill from that ready queue
                # before recomputing evidence, proposing children, or publishing the next view.
                # Otherwise every free worker waits for the entire coordinator pass. A dirty plan
                # is rebuilt below before dispatch so changed staged limits still take effect.
                if order and not self._planner_dirty and len(pending) < workers:
                    early_reserved = reserved_keys(pending) | set(completion_buffer)
                    early_started = time.monotonic()
                    while order and len(pending) < workers and not self.shutdown.is_set():
                        slot = available_slot()
                        if slot is None:
                            break
                        cid, phase, ordinal = order.pop(0)
                        if (cid, phase, ordinal) in early_reserved:
                            continue
                        scenario = self._planner_scenarios.get(cid)
                        if scenario is None:
                            scenario = store.observed_scenario(store.scenario(cid), candidate=cid)
                            if scenario is None:
                                continue
                            self._planner_scenarios[cid] = scenario
                        submit_ready(cid, phase, ordinal, scenario, slot, early_reserved)
                    if pending:
                        self._work_started = pending[0]['started']
                    self._scheduler['earlyDispatchSeconds'] = self._scheduler.get(
                        'earlyDispatchSeconds', 0.) + time.monotonic()-early_started
                # ---- analysis is dirty-driven, and off the worker-feeding path ----------------
                # Everything in this block is analysis rather than a battle: it recomputes paired
                # comparisons, advances fine-tune programs and opens banks. No worker can consume
                # any of it, it is the one part of this loop that can take seconds rather than
                # microseconds, and it used to run on a fixed tick whatever the pool was doing. What
                # it decides about does not change between two battles either: a point becomes ready
                # for a bracket when its authorised bank *finishes*, not when battle 12,438 replaces
                # battle 12,437. So the trigger is the analysis signature, the cadence is only the
                # rate at which that signature is consulted, and `ANALYSIS_MAX_SECONDS` is the
                # backstop that keeps a change the signature cannot express from waiting forever.
                _analysis_now = time.monotonic()
                if _analysis_now-last_adaptive_probe >= ANALYSIS_CADENCE_SECONDS:
                    last_adaptive_probe = _analysis_now
                    signature = self._analysis_signature(store)
                    if (signature == last_analysis_signature
                            and _analysis_now-last_analysis < ANALYSIS_MAX_SECONDS):
                        self._scheduler['analysisDeferred'] = \
                            self._scheduler.get('analysisDeferred', 0)+1
                    else:
                        last_analysis = _analysis_now
                        _analysis_started = time.monotonic()
                        _analysis_changed = False
                        _steps = {}

                        def _timed(name, run):
                            """One analysis step, timed so its cost is named rather than assumed."""
                            _step_started = time.monotonic()
                            outcome = run()
                            _steps[name] = _steps.get(name, 0.)+time.monotonic()-_step_started
                            return outcome

                        # Auto-tuning runs first so a focused fight that only *later* obtains a
                        # qualified build gets its programs started, and a program created this
                        # pass gets its first points on the same pass.
                        if _timed('autoTune', lambda: self._auto_tune(store)):
                            self._planner_dirty = True
                            dirty = True
                            _analysis_changed = True
                        if _timed('probePlans', lambda: self._advance_probe_plans(store)):
                            self._planner_dirty = True
                            dirty = True
                            _analysis_changed = True
                        # Breakthrough / reliability. It creates real candidates and banks and closes
                        # finished comparisons. At a 0% share it only closes in-flight plans.
                        if _timed('breakthrough', lambda: self._advance_breakthrough(store)):
                            self._planner_dirty = True
                            dirty = True
                            _analysis_changed = True
                        # MP recovery. It reads MP telemetry that ordinary runs already carry, and
                        # when an allowance is configured it creates a herb-enabled child with its
                        # own identity and a declared bank. With no allowance configured it creates
                        # nothing and says so; either way it never runs a battle here.
                        if _timed('mpRecovery', lambda: self._advance_mp_recovery(store)):
                            self._planner_dirty = True
                            dirty = True
                            _analysis_changed = True
                        # Fine-tuning programs: they create real candidates and banks, so they must
                        # not run every few-millisecond pass either.
                        if _timed('finetune', lambda: self._advance_finetune_programs(store)):
                            self._planner_dirty = True
                            dirty = True
                            _analysis_changed = True
                        # One reading per analysis run, so the report can show what analysis costs,
                        # step by step, and how often deferring it happened.
                        recorded = dict(self._scheduler.get('analysisStepSeconds') or {})
                        for _name, _seconds in _steps.items():
                            recorded[_name] = round(recorded.get(_name, 0.)+_seconds, 3)
                        self._scheduler['analysisStepSeconds'] = recorded
                        self._scheduler['analysisSeconds'] = \
                            self._scheduler.get('analysisSeconds', 0.)+time.monotonic()-_analysis_started
                        self._scheduler['analysisRuns'] = \
                            self._scheduler.get('analysisRuns', 0)+1
                        if _analysis_changed:
                            self._scheduler['analysisChangingRuns'] = \
                                self._scheduler.get('analysisChangingRuns', 0)+1
                        # Recomputed *after* the run: a pass that created a program or opened a bank
                        # has itself changed the state the next comparison is against.
                        last_analysis_signature = self._analysis_signature(store)
                # Re-plan only when there is something to plan: a result arrived, a worker is free,
                # or the ready list ran out. This loop wakes every few milliseconds to harvest
                # completions promptly, and rebuilding the whole evidence view, the ready list and
                # the reservoir on each of those wakes - with every closure and cache in it re-created
                # from scratch - made the coordinator the bottleneck while the pool was full.
                exploration_due = (BALANCED_DISCOVERY and LEARNER_MODE != 'legacy'
                    and not MINIMAL_PLANNER
                    and time.monotonic()-last_topup >= DISCOVERY_REFRESH_SECONDS
                    and discovery_backlog(self._planner_limits or {},
                                          self._planner_ordinals or {},
                                          encounter_scope(store.candidate_encounters(),
                                                          self._focus_encounter)) < workers)
                if order and len(pending) >= workers and not self._planner_dirty and not exploration_due:
                    self._scheduler['planSkips'] = self._scheduler.get('planSkips', 0)+1
                    if self.flush_records(store, pending):
                        dirty = True
                    if publish_due:
                        publish_if_due()
                    continue
                try:
                    _plan_started = time.monotonic()
                    # Keep every worker fed with *useful* work. The old chooser opened one
                    # candidate's bank at a time and only proposed a single successor once every
                    # bank had drained, so the pool spent most of its life with nothing independent
                    # to run (`runnableDiscovery 0`, `runnableValidation 0-3` at plan time). The
                    # reservoir below collects the ready seeds of the whole population, interleaves
                    # them across candidates, and tops the runnable set up with new siblings from
                    # evidence-bearing parents before the pool is allowed to go idle. Nothing here
                    # submits a seed the learner would not want: a task is an unfinished bank the
                    # staged policy authorised, or a child of a parent that has evidence.
                    # The reservoir. `ready()` returns the population's interleaved ready seeds; when
                    # that is below the target the scheduler branches new siblings off parents that
                    # already have evidence, so the pool has genuinely independent work instead of
                    # waiting on one bank at a time. Every seed submitted below is either an unfinished
                    # bank the staged evidence policy authorised or a child of an evidence-bearing
                    # parent - nothing is run just to keep a CPU busy.
                    reservoir_target = max(workers, workers*learner.RESERVOIR_SEEDS_PER_WORKER)
                    keys = store.candidate_keys()
                    # Anything already running OR already finished-but-buffered must not be planned
                    # twice: the buffer holds a result the store has not taken yet.
                    reserved = reserved_keys(pending) | set(completion_buffer)
                    def load_evidence():
                        aggregates = read_aggregates()
                        if LEARNER_MODE != 'legacy':
                            observe_children(store, aggregates, keys, LEARNER_MODE)
                            # Child observation updates learning maps, never run aggregates. The
                            # coordinator is the sole writer, so these counters remain current.
                        records = population_records(store, keys, aggregates)
                        lanes = elite_lanes(records)
                        ranks = {}
                        for groups in lanes.values():
                            for lane, members in groups.items():
                                for index, cid in enumerate(members):
                                    ranks[cid] = min(ranks.get(cid, 99), index)
                        # The staged policy only asks whether a resident candidate has ancestry; legacy
                        # rotation additionally needs its encounter, and the retry gate below needs the
                        # stream that produced it. Scanning and decoding every pruned lifetime lineage
                        # row here made each evidence refresh grow with the number of historical
                        # proposals, so this reads residents only.
                        lineage = {row[0]: {'encounterId': row[1], 'source': row[2]}
                                   for row in store.db.execute(
                                       'SELECT l.candidate,l.encounterId,l.source FROM lineage l '
                                       'JOIN candidate c ON c.id=l.candidate')}
                        # A build's retry budget belongs to the stream that produced it: switch a student
                        # off and its leaders stop being retried. A build with no recorded stream is the
                        # ordinary search's own work and answers to the discovery share.
                        streams = active_streams(self._student_shares)
                        active = {cid for cid, row in lineage.items()
                                  if streams.get(row.get('source') or students.STREAM_DISCOVERY, True)}
                        incumbents = incumbent_banks(lanes, records, active=active)
                        self._planner_rank_one = incumbents
                        improvements = store.get('childImprovements') or {}
                        # Did this candidate open a strategy region nothing else had reached? The
                        # nominee of each region is the earliest surviving build in it, so the two
                        # index-derived maps answer that without a stored per-candidate blob.
                        regions = store.candidate_regions()
                        nominees = store.region_nominees()
                        new_regions = {cid for cid, key in regions.items() if nominees.get(key) == cid}
                        # Every declared bank travels through this one map: the probe facility's, the
                        # average student's "evaluate this parent" banks, and the breakthrough stream's
                        # experiment arms. A candidate in it is measured to its own bank instead of the
                        # ordinary staged one, which is how a controlled comparison gets its shared
                        # seeds and how a promising candidate continues past the 64-run screening bank.
                        banks = dict(store.get('probeBanks') or {})
                        banks.update(students.average_banks(store))
                        try:
                            import strategy_breakthrough as _breakthrough
                            banks.update(_breakthrough.banks(store))
                        except Exception as exc:  # never let the new stream stop the search
                            self._student_diagnostics['breakthroughError'] = repr(exc)
                        try:
                            # MP-recovery children are measured through the same declared-bank map, so
                            # a bounded item experiment is scheduled by the existing machinery rather
                            # than by a second scheduler. A finished request stops being offered.
                            import strategy_mp_recovery as _mp_recovery
                            banks.update(_mp_recovery.banks(store))
                        except Exception as exc:  # never let the new stream stop the search
                            self._student_diagnostics['mpRecoveryError'] = repr(exc)
                        limits = {}
                        for record in records:
                            cid = record['candidate']
                            discovery = aggregates.get(f'aggregate:{cid}:discovery') or {}
                            limits[cid] = staged_limits(
                                int(discovery.get('n', 0) or 0),
                                int(discovery.get('wins', 0) or 0),
                                (improvements.get(cid) or {}).get('lanes'),
                                cid in new_regions, ranks.get(cid),
                                record.get('earnedMax'), record.get('potentialMax'),
                                record.get('progress'), cid in banks,
                                banks.get(cid, VALIDATION_RUNS), cid in lineage, LEARNER_MODE,
                                incumbent=incumbents.get(cid, 0))
                        # Focused exploitation lane: the staged policy above is the screening stage;
                        # while a fight is focused its convincing builds then earn a bounded, larger
                        # paired-seed target. The overlay only raises validation limits and only for
                        # the focused encounter's builds, and its budget is a fraction of the
                        # reservoir, so novel mutations keep their share of the pool.
                        focus = self._focus_encounter
                        if focus is None:
                            self._planner_exploit = {}
                            self._planner_record = None
                            self._planner_portfolio = None
                            self._planner_portfolio_banks = {}
                            self._planner_portfolio_tune = None
                        else:
                            summaries, groups = focused_exploit_inputs(store, records, aggregates,
                                                                       focus)
                            record, record_report = focused_record_holder(store, focus, aggregates)
                            if record is not None:
                                self._planner_record = dict(
                                    status='tuning', candidate=record['candidate'],
                                    encounterId=record['encounterId'], label=record.get('label'),
                                    value=record.get('recordValue'))
                            else:
                                self._planner_record = dict(record_report or {})
                            limits, self._planner_exploit = focused_exploitation(
                                summaries, limits, focus,
                                reservoir_target//FOCUS_EXPLOIT_SHARE, groups,
                                record=record,
                                record_budget=reservoir_target//FOCUS_RECORD_SHARE)
                            # Four-objective portfolio: the focused fight's intensive capacity is
                            # shared across highest-earned, highest-potential, average-earned and
                            # average-potential lanes, each with its own evidence rule, plus a
                            # reserved challenger slot and a ring-fenced maintenance tier. It is
                            # additive to the two lanes above and draws a minority of the reservoir,
                            # so novel exploration keeps its share. Everything it reads is persisted
                            # evidence; nothing here measures a battle.
                            entries, representative = portfolio.focused_portfolio_inputs(
                                store, records, aggregates, focus)
                            challenge_lanes = portfolio.focused_portfolio_lanes(entries,
                                                                                representative)
                            challengers = portfolio.portfolio_challenger_candidates(
                                entries, challenge_lanes, improvements, new_regions)
                            signature = portfolio.portfolio_tune_signature(store, focus)
                            # **Recompute on events, not on battles.** The signature now moves only when a
                            # participant crosses an evidence rung, when a relevant candidate appears or
                            # leaves, or when a program definition changes - and the plan itself is a
                            # pure function of that evidence and the persisted ledger, so the previous
                            # plan can be reused while nothing relevant has moved. The time guard is a
                            # backstop, not the trigger. Measured on the live library: the comparison it
                            # guards costs 613 ms a call, and it used to run on every planning pass
                            # because the key included each battle's own run revision.
                            cache = self._portfolio_cache
                            now = time.monotonic()
                            if (cache is None or cache['signature'] != signature
                                    or now - cache['at'] >= PORTFOLIO_REFRESH_SECONDS):
                                readings_cache = cache['readings'] if cache else {}
                                comparisons = portfolio.portfolio_tune_comparisons(
                                    store, focus, cache=readings_cache)
                                prior = store.get(portfolio.PORTFOLIO_STATE_KEY) or {}
                                plan, ledger = portfolio.focused_portfolio(
                                    entries, prior,
                                    budget=reservoir_target//portfolio.PORTFOLIO_SHARE,
                                    representative=representative, challengers=challengers,
                                    comparisons=comparisons, encounter_id=focus)
                                cache = dict(signature=signature, readings=readings_cache, plan=plan,
                                             ledger=ledger, at=now)
                                self._portfolio_cache = cache
                                self._scheduler['portfolioRefresh'] = \
                                    self._scheduler.get('portfolioRefresh', 0) + 1
                                if ledger != prior:
                                    with store.db:
                                        store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
                            plan = cache['plan']
                            self._planner_portfolio_tune = cache
                            if plan['active'] or plan['maintenance'] or plan['graduated']:
                                limits = portfolio.portfolio_limits(limits, plan)
                            self._planner_portfolio = plan
                            self._planner_portfolio_banks = {
                                decision['candidate']: int(decision.get('target') or 0)
                                for decision in (plan['active']+plan['maintenance']
                                                 + plan.get('recheckGrants', []))
                                if int(decision.get('granted') or 0) > 0}
                        return aggregates, lanes, ranks, limits, lineage

                    def read_aggregates():
                        """Fresh run counters for resident candidates only.

                        This part must never be cached: `ready()` derives the next ordinal from it, and a
                        stale count would offer a seed that has already finished. The meta table also
                        holds pruned candidates' counters; scanning and decoding those on
                        every refresh costs more than direct indexed reads of the active population.
                        """
                        aggregate_keys = [f'aggregate:{cid}:{phase}' for cid in keys
                                          for phase in ('discovery', 'validation')]
                        if not aggregate_keys:
                            return {}
                        placeholders = ','.join('?' for _ in aggregate_keys)
                        return {row[0]: json.loads(row[1]) for row in store.db.execute(
                            f'SELECT key, value FROM meta WHERE key IN ({placeholders})',
                            aggregate_keys)}

                    # The evidence view is refreshed at most a few times a second. Rebuilding it on
                    # every completion made the coordinator's own planning the biggest consumer of the
                    # wall clock once the pool was genuinely busy, which is exactly the kind of
                    # self-inflicted idle the reservoir is supposed to remove.
                    evidence = self._planner_evidence
                    def evidence_now(force=False):
                        now = time.monotonic()
                        ttl = (MINIMAL_PLANNER_EVIDENCE_SECONDS if MINIMAL_PLANNER
                               else PLANNER_EVIDENCE_SECONDS)
                        if BALANCED_DISCOVERY and not MINIMAL_PLANNER:
                            ttl = evidence_refresh_delay(evidence.get('duration', 0.), ttl)
                        if force or evidence['value'] is None or now-evidence['at'] > ttl:
                            _rebuild = time.monotonic()
                            evidence['value'] = load_evidence()
                            self._scheduler['evidenceRebuildSeconds'] = \
                                self._scheduler.get('evidenceRebuildSeconds', 0.)+time.monotonic()-_rebuild
                            self._scheduler['evidenceRebuilds'] = \
                                self._scheduler.get('evidenceRebuilds', 0)+1
                            evidence['duration'] = time.monotonic()-_rebuild
                            evidence['at'] = time.monotonic()
                        # The cached aggregates travel with the cached evidence. Nothing outside this
                        # view needs fresher counters: `ready()` and `open_seeds()` derive ordinals
                        # from the coordinator's own map, and the staged limits are a property of the
                        # evidence refresh itself. Re-reading and re-parsing every candidate's
                        # aggregate document on each pass was pure per-pass cost.
                        return evidence['value']
                    _section = time.monotonic()
                    # Expensive comparison work may wait while authorised battles run. A drained
                    # pool must refresh immediately so a completed screening bank can advance.
                    aggregates, lanes, ranks, limits, lineage = evidence_now(
                        force=BALANCED_DISCOVERY and not pending and not order)
                    self._scheduler['evidenceSeconds'] = self._scheduler.get(
                        'evidenceSeconds', 0.)+time.monotonic()-_section
                    # The planner's own state outlives the pass: the scenario cache, the next-ordinal
                    # map and the limits the staged policy last authorised. Rebuilding them here meant
                    # the caches above could never actually cache anything.
                    self._planner_limits = limits
                    scenarios = self._planner_scenarios
                    # The next unrecorded ordinal per (candidate, phase). The coordinator is the only
                    # writer, so it can keep this in memory: `ready()` and `open_seeds()` ask for it on
                    # every planning pass, and reading it back out of the stored per-candidate
                    # counters meant parsing one JSON document per candidate per pass.
                    if self._planner_ordinals is None:
                        self._planner_ordinals = {
                            (row[0], row[1]): int(row[2])+1 for row in store.db.execute(
                                'SELECT candidate, phase, MAX(ordinal) FROM run '
                                'GROUP BY candidate, phase')}
                    ordinals = self._planner_ordinals

                    def scenario_for(cid):
                        if cid not in scenarios:
                            row = store.db.execute('SELECT scenario FROM candidate WHERE id=?',
                                                   (cid,)).fetchone()
                            scenarios[cid] = (store.observed_scenario(json.loads(row[0]), candidate=cid)
                                              if row else None)
                        return scenarios[cid]

                    # The focused encounter (or None) narrows the whole planning pass. It is read from
                    # the store's own candidate->encounter index on each use rather than a frozen set,
                    # so a child branched for the focused fight *this* pass is in scope immediately.
                    focus_encounter = self._focus_encounter

                    # Which student owns each candidate. Read from the lineage row, not from
                    # `candidate.source`: that column is the coarse evidence category and records every
                    # generated child as 'mutation', so it cannot tell one student from another.
                    # `Store.add_child` already stores the proposing stream on `lineage.source`.
                    attribution = students.student_of(store)
                    community_ids = {cid for cid, source in attribution.items()
                                     if source == students.COMMUNITY_SOURCE}
                    # Keep the community track supplied before the plan is materialised, so the reserved
                    # share has candidates to dispatch in *this* pass rather than next time. The trigger is
                    # runnable community seeds, not elapsed time: the reservation is only real while there
                    # is community work to take, and a fight whose value neighbourhood is saturated stops
                    # asking for children instead of paying for draws that all collide.
                    try:
                        community_scope = encounter_scope(store.candidate_encounters(), focus_encounter)
                        # A student switched off does not propose either. Creating children it can
                        # never be given a run for is pure churn - and worse, they arrive on the
                        # leaderboard as fresh names with sample counts too small to mean anything.
                        if (float(self._student_shares.get(students.STUDENT_COMMUNITY, 0.0) or 0.0) > 0
                                and students.community_runnable(
                                    store, limits, ordinals, community_scope) < workers*2):
                            from strategy_optimizer_adapter import stats as adapter_stats
                            for encounter_id in focused_encounters(
                                    sorted(set(store.candidate_encounters().values())),
                                    focus_encounter):
                                for cid in students.replenish_community(
                                        store, encounter_id, students.COMMUNITY_TRACK_WIDTH,
                                        store.get('proposals') or 0, adapter_stats,
                                        shares=self._track_shares):
                                    community_ids.add(cid)
                                    limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            self._student_diagnostics['communityCreated'] = \
                                len(community_ids)-len(attribution)
                    except Exception as exc:  # never let the community track stop the search
                        self._student_diagnostics['communityError'] = repr(exc)

                    # The rebel track, kept supplied the same way and for the same reason: the reserved
                    # share is only real while there is rebel work to take. What is different is the
                    # cost - a rebel draw has to work out which rules it broke, so it is several times a
                    # value draw - which is why the pass works a rotating slice of the (tier, encounter)
                    # pairs rather than every one of them. A pass must not stall the coordinator for the
                    # sake of the far tiers; the rest of the work is simply owed and reported.
                    rebel_ids = {cid for cid, source in attribution.items()
                                 if source == students.STUDENT_REBEL}
                    # Student 9's own children. They are counted separately so the dispatch gate can
                    # halt this student on its own share exactly as it halts the other two.
                    average_ids = {cid for cid, source in attribution.items()
                                   if source == students.STUDENT_AVERAGE}
                    # Breakthrough / reliability: the mechanism-guided experiments. Its own share,
                    # its own budget, and the same gate - at 0% not a single arm it created may run.
                    mechanism_ids = {cid for cid, source in attribution.items()
                                     if source == students.STUDENT_MECHANISM}
                    # MP-recovery children, owned by their own operation rather than by a student. They
                    # are gated on *their own* allowance below: a configured stock and use cap is the
                    # permission, and with none configured they are removed from every view exactly as a
                    # zero-share student's builds are, so nothing runs "for free".
                    try:
                        import strategy_mp_recovery as _mp_recovery
                        mp_recovery_ids = {cid for cid, source in attribution.items()
                                           if source == _mp_recovery.SOURCE}
                        mp_recovery_on = bool(_mp_recovery.setting(store)['effective'])
                    except Exception:  # noqa: BLE001 - never let the stream stop the search
                        mp_recovery_ids, mp_recovery_on = set(), False
                    try:
                        rebel_scope = encounter_scope(store.candidate_encounters(), focus_encounter)
                        if (float(self._student_shares.get(students.STUDENT_REBEL, 0.0) or 0.0) > 0
                                and students.rebel_runnable(
                                    store, limits, ordinals, rebel_scope) < workers*2):
                            from strategy_optimizer_adapter import stats as adapter_stats
                            encounters = focused_encounters(
                                sorted(set(store.candidate_encounters().values())), focus_encounter)
                            wanted = []
                            for tier in students.tiers():
                                target = students.REBEL_TRACK_WIDTH * len(encounters)
                                if students.rebel_runnable(store, limits, ordinals, rebel_scope,
                                                           tier=tier.name) >= target:
                                    continue
                                wanted.extend((tier, encounter_id) for encounter_id in encounters)
                            if wanted:
                                start = int(store.get('proposals') or 0) % len(wanted)
                                ordered = wanted[start:] + wanted[:start]
                                for tier, encounter_id in ordered[:REBEL_PASS_BUDGET]:
                                    for cid in students.replenish_rebels(
                                            store, encounter_id, tier, students.REBEL_TRACK_WIDTH,
                                            store.get('proposals') or 0, adapter_stats):
                                        rebel_ids.add(cid)
                                        limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            self._student_diagnostics['rebelCreated'] = \
                                len(rebel_ids)-len(attribution)
                            self._student_diagnostics['rebelPending'] = max(
                                0, len(wanted)-REBEL_PASS_BUDGET)
                    except Exception as exc:  # never let the rebel track stop the search
                        self._student_diagnostics['rebelError'] = repr(exc)

                    # The average student's own supply, gated on its own share for the same reason the
                    # other two are: a student switched off must not create children it can never run.
                    try:
                        if (float(self._student_shares.get(students.STUDENT_AVERAGE, 0.0) or 0.0) > 0
                                and students.average_runnable(
                                    store, limits, ordinals,
                                    encounter_scope(store.candidate_encounters(), focus_encounter))
                                < workers*2):
                            from strategy_optimizer_adapter import stats as adapter_stats
                            for encounter_id in focused_encounters(
                                    sorted(set(store.candidate_encounters().values())),
                                    focus_encounter):
                                for cid in students.replenish_average(
                                        store, encounter_id, students.AVERAGE_TRACK_WIDTH,
                                        store.get('proposals') or 0, adapter_stats):
                                    average_ids.add(cid)
                                    limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            self._student_diagnostics['averageCreated'] = \
                                len(average_ids)-len(attribution)
                    except Exception as exc:  # never let the average student stop the search
                        self._student_diagnostics['averageError'] = repr(exc)

                    def ready():
                        """Ready (cid, phase, ordinal) seeds, interleaved across candidates.

                        While a fight is focused the view is that fight's candidates only, so every seed
                        a worker is handed belongs to the focused encounter. Clearing the focus restores
                        the interleaved population exactly.

                        The community student's share is taken *first*, from its own view, and the rest of
                        the pool rotates into what remains. That ordering is the whole point: a share that
                        only raises a candidate's authorised limit is not a share, because the single
                        interleaved window would still hand the community build roughly its fraction of
                        the *population* - which on encounter 11 measured out to 88 runs in 1.13 million.
                        """
                        focus_ids = encounter_scope(store.candidate_encounters(), focus_encounter)
                        view = {cid: value for cid, value in limits.items()
                                if (focus_ids is None or cid in focus_ids)
                                and scenario_for(cid) is not None}
                        if not BALANCED_DISCOVERY:
                            return interleave_ready(view, ordinals, reserved)
                        maximum = max(reservoir_target, workers*48)
                        cursors = {'offsets': (getattr(self, '_planner_ready_cursors', None)
                                               or {}).get('offsets', {})}
                        community_share = float(
                            self._student_shares.get(students.STUDENT_COMMUNITY, 0.0) or 0.0)
                        rebel_share = float(
                            self._student_shares.get(students.STUDENT_REBEL, 0.0) or 0.0)
                        average_share = float(
                            self._student_shares.get(students.STUDENT_AVERAGE, 0.0) or 0.0)
                        discovery_share = float(
                            self._student_shares.get(students.STREAM_DISCOVERY, 0.0) or 0.0)
                        # **A stream at zero halts.** Its candidates are removed from *every* view,
                        # including the general one, so nothing hands them a seed - no view, no
                        # overlay, no leftover. Before this the reserved streams only had first
                        # refusal: they took their window, and whatever they did not use fell through
                        # to the rest of the pool. So setting a student to 0% never stopped its builds
                        # being run, it only stopped them being preferred - measured on the live
                        # library, 38 Student 1 builds advanced in 45 seconds at a 0% share while only
                        # 11 Student 2 builds advanced at 100%. "100% on one student" has to mean the
                        # others stop, or the number on the card is not a share.
                        switched_on = dispatch_gate(self._student_shares, community_ids, rebel_ids,
                                                    average_ids, mechanism_ids, mp_recovery_ids,
                                                    mp_recovery_on)
                        view = {cid: value for cid, value in view.items() if switched_on(cid)}
                        community_view = {cid: value for cid, value in view.items()
                                          if cid in community_ids}
                        rebel_view = {cid: value for cid, value in view.items()
                                      if cid in rebel_ids}
                        average_view = {cid: value for cid, value in view.items()
                                        if cid in average_ids}
                        other_view = {cid: value for cid, value in view.items()
                                      if cid not in community_ids and cid not in rebel_ids
                                      and cid not in average_ids}
                        taken = []
                        if community_view and community_share > 0:
                            taken, cursors = ready_window(
                                community_view, ordinals, reserved,
                                maximum=int(maximum*community_share), cursors=cursors)
                        if rebel_view and rebel_share > 0:
                            # The rebel share is taken from its own view, after the community's and
                            # before the rest of the pool, for the same reason the community's is: a
                            # share that only raises a candidate's authorised limit is not a share,
                            # because the single interleaved window would still hand the rebel
                            # roughly its fraction of the *population*.
                            window, cursors = ready_window(
                                rebel_view, ordinals, reserved,
                                maximum=min(int(maximum*rebel_share), maximum-len(taken)),
                                cursors=cursors)
                            taken = taken + window
                        if average_view and average_share > 0:
                            # After the students the user prioritised, and again from its own view: the
                            # average student's whole purpose is to be credited for raising a mean, so it
                            # cannot be handed its fraction of the *population* and hope.
                            window, cursors = ready_window(
                                average_view, ordinals, reserved,
                                maximum=min(int(maximum*average_share), maximum-len(taken)),
                                cursors=cursors)
                            taken = taken + window
                        rest, self._planner_ready_cursors = ready_window(
                            other_view, ordinals, reserved, maximum=maximum-len(taken),
                            # A dirty plan discards unsubmitted queue entries. Only rotate
                            # candidates across rebuilds; accepted ordinals/reservations own
                            # seed progress, never the previous queue's materialization cursor.
                            cursors=cursors)
                        return taken + rest

                    def open_seeds():
                        """Runnable seeds the pool could be handed now, in the focused scope."""
                        focus_ids = encounter_scope(store.candidate_encounters(), focus_encounter)
                        total = 0
                        for cid, (dlimit, vlimit) in limits.items():
                            if focus_ids is not None and cid not in focus_ids:
                                continue
                            total += max(0, dlimit-ordinals.get((cid, 'discovery'), 0))
                            total += max(0, vlimit-ordinals.get((cid, 'validation'), 0))
                        return total

                    def reseed_target():
                        """The shared `reseed_target` helper, plus the diagnostics it implies."""
                        chosen, by_encounter = reseed_target(store, limits, ordinals)
                        self._scheduler['reseedByEncounter'] = by_encounter
                        return chosen

                    def top_up():
                        """Branch siblings from evidence-bearing parents until the reservoir is full."""
                        nonlocal aggregates, population_blocked
                        created = 0
                        # Persist the next turn: surviving population size is a poor fairness
                        # signal because pruning can keep one encounter permanently small.
                        cursor = int(store.get('proposalEncounterCursor') or 0)
                        # Encounters that refused a proposal *this pass* (their population is full of
                        # protected builds, or every draw collided with an existing build). Kept local
                        # so the other encounters get the turn instead of this loop hammering the
                        # same one, and so the decision is retried from fresh counts next pass.
                        blocked = set()
                        # Keep proposal bursts short enough for the coordinator to harvest and
                        # refill workers between batches, while still opening multiple new tactics.
                        for attempt in range(2):
                            discovery_ready = discovery_backlog(
                                limits, ordinals,
                                encounter_scope(store.candidate_encounters(), focus_encounter))
                            if (self.shutdown.is_set() or (open_seeds() >= reservoir_target
                                    and (not BALANCED_DISCOVERY or discovery_ready >= workers))):
                                break
                            # Limit unfinished discovery banks per fight. Store.add enforces the
                            # separate active-population cap and prunes replaceable builds.
                            open_children = {}
                            index = store.candidate_encounters()
                            for cid in limits:
                                encounter_id = index.get(cid)
                                if encounter_id is None:
                                    continue
                                open_children.setdefault(encounter_id, 0)
                                if unfinished_child(cid, limits, ordinals):
                                    open_children[encounter_id] += 1
                            available = []
                            # While a fight is focused the reservoir may only be refilled with *that*
                            # fight's work, so proposals and the population slots they consume cannot
                            # be spent on runs the scheduler would then refuse to dispatch.
                            for encounter_id in focused_encounters(sorted(open_children), focus_encounter):
                                if encounter_id in blocked:
                                    continue
                                if open_children[encounter_id] >= learner.MAX_OPEN_PER_ENCOUNTER:
                                    continue
                                available.append(encounter_id)
                            if not available:
                                self._scheduler['reservoirFull'] = True
                                break
                            # Visit eligible encounters in order, wrapping at the end. Advance on
                            # every attempt, including a protected-cap refusal, so one fight cannot
                            # monopolize proposals or strand another indefinitely.
                            encounter = next((value for value in available if value >= cursor),
                                             available[0])
                            cursor = encounter + 1
                            with store.db:
                                store.set('proposalEncounterCursor', cursor)
                            attempts_by_encounter = self._scheduler.setdefault(
                                'proposalAttemptsByEncounter', {})
                            key = str(encounter)
                            attempts_by_encounter[key] = attempts_by_encounter.get(key, 0) + 1
                            self._scheduler['reservoirFill'] = self._scheduler.get('reservoirFill', 0)+1
                            _proposal_started = time.monotonic()
                            try:
                                ids = spawn_children(store, encounter, learner.BRANCH_FACTOR,
                                                     store.get('proposals') or 0, self._scheduler)
                            except ValueError:
                                # The encounter is at its protected population cap: a bounded stop, not
                                # an error. Nothing more can be branched there until its evidence moves
                                # the protection, so give the other encounters this pass and let the
                                # next one re-ask from fresh counts.
                                self._scheduler.setdefault('cappedEncounters', {})[str(encounter)] = True
                                blocked.add(encounter)
                                if encounter == focus_encounter:
                                    population_blocked = True
                                continue
                            finally:
                                self._scheduler['proposalSeconds'] += time.monotonic()-_proposal_started
                            if not ids:
                                self._scheduler['emptyPlans'] = self._scheduler.get('emptyPlans', 0)+1
                                blocked.add(encounter)
                                continue
                            created_by_encounter = self._scheduler.setdefault(
                                'proposalCreatedByEncounter', {})
                            created_by_encounter[key] = created_by_encounter.get(key, 0) + len(ids)
                            with store.db:
                                store.set('proposals', (store.get('proposals') or 0)+1)
                            created += len(ids)
                            for cid in ids:
                                limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            # The children's own evidence rows arrive as their seeds complete; refresh
                            # the aggregate view so `ready` sees them immediately.
                            aggregates = read_aggregates()
                        return created

                    _section = time.monotonic()
                    # `order` is maintained across passes. It is rebuilt only when it has run dry or a
                    # structural change invalidated it; completing a bank marks the next stage for
                    # a rebuild, and a new child adds its first bank. Rebuilding the whole seed list
                    # on every pass was the single
                    # largest coordinator cost once recording stopped blocking dispatch.
                    if not self._planner_order or self._planner_dirty:
                        self._planner_order = ready()
                        self._planner_dirty = False
                        self._scheduler['orderRebuilds'] = self._scheduler.get('orderRebuilds', 0)+1
                    order = self._planner_order
                    self._scheduler['readySeconds'] = self._scheduler.get(
                        'readySeconds', 0.)+time.monotonic()-_section
                    # Per-pass liveness accounting: what this pass handed to workers, and what it had
                    # to create to do so. Used only to explain a genuinely idle focused search (see
                    # `focused_stall_reason`); it never gates dispatch.
                    dispatched = 0
                    created = 0
                    population_blocked = False
                    idle_no_work = False
                    # Run ready battles before creating fresh candidates. Branching still happens
                    # below while the workers are occupied, preserving the exploration reservoir.
                    while order and len(pending) < workers and not self.shutdown.is_set():
                        slot = available_slot()
                        if slot is None:
                            break
                        cid, phase, ordinal = order.pop(0)
                        if (cid, phase, ordinal) in reserved:
                            continue
                        scenario = scenario_for(cid)
                        if scenario is None:
                            continue
                        dispatched += submit_ready(cid, phase, ordinal, scenario, slot, reserved)
                    if pending:
                        self._work_started = pending[0]['started']
                    if LEARNER_MODE == 'legacy':
                        # The previous learner: one child at a time, and only when nothing at all is
                        # runnable. Kept behind the measurement switch so the before/after comparison
                        # is honest - production never takes this branch.
                        if not order:
                            encounter = store.get('proposalEncounter') or 0
                            rotation = sorted({row['encounterId'] for row in lineage.values()})
                            if rotation:
                                encounter = rotation[(store.get('proposals') or 0) % len(rotation)]
                            # Focus overrides the rotation here too: a focused library branches only
                            # the focused fight even on the legacy path.
                            if focus_encounter is not None:
                                encounter = focus_encounter
                            _proposal_started = time.monotonic()
                            try:
                                ids = spawn_children(store, encounter, 1, store.get('proposals') or 0,
                                                     self._scheduler)
                            except ValueError:
                                ids = []
                            finally:
                                self._scheduler['proposalSeconds'] += time.monotonic()-_proposal_started
                            with store.db:
                                store.set('proposals', (store.get('proposals') or 0)+1)
                            for cid in ids:
                                limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            self._planner_order = ready(); order = self._planner_order
                    elif ((len(order) < reservoir_target or exploration_due) and not MINIMAL_PLANNER
                          and (time.monotonic()-last_topup >= 1.
                               or len(order) < workers-len(pending))):
                        # Branch regularly, including while other workers are busy, but do not
                        # repeat a whole-population proposal pass on every fast completion. If
                        # the queue cannot fill free slots, replenish immediately.
                        _topup_started = time.monotonic()
                        branched = top_up()
                        last_topup = time.monotonic()
                        self._scheduler['topUpSeconds'] = \
                            self._scheduler.get('topUpSeconds', 0.)+time.monotonic()-_topup_started
                        self._scheduler['topUpCalls'] = self._scheduler.get('topUpCalls', 0)+1
                        created += branched
                        if branched:
                            self._planner_order = ready(); order = self._planner_order
                    # ---- anti-starvation pressure control --------------------------------------
                    # Normal branching has already had its turn. If the sustained window still shows
                    # idle workers *and* the planner genuinely had nothing to submit, the pool is
                    # starved rather than merely busy: create a small, bounded batch of new
                    # exploration candidates. The pressure comes from the measured shortfall, it is
                    # capped by the reservoir target and by in-population reseed limits, and it falls
                    # straight back to zero when the reservoir is healthy again.
                    _now = time.monotonic()
                    _utilisation, _starved_window = learner.window_utilisation(pressure_samples)
                    _coordinator = learner.window_coordinator_fraction(pressure_samples)
                    _saturated = (_coordinator is not None
                                  and _coordinator >= learner.COORDINATOR_SATURATED)
                    _level = (learner.starvation_level(_utilisation)
                              if (_starved_window and _utilisation is not None and not _saturated)
                              else 0)
                    self._scheduler['starvationUtilisation'] = (None if _utilisation is None
                                                                else round(_utilisation, 4))
                    self._scheduler['coordinatorFraction'] = (None if _coordinator is None
                                                              else round(_coordinator, 4))
                    if _starved_window and _saturated:
                        # Low utilisation with a dry planner *and* a coordinator that is already the
                        # bottleneck: more candidates would only add work to the saturated thread, so
                        # the pressure is deliberately not raised. Counted, so the decision is visible.
                        self._scheduler['starvationVetoed'] = \
                            self._scheduler.get('starvationVetoed', 0) + 1
                    self._scheduler['starvationActive'] = bool(_level)
                    self._scheduler['starvationLevel'] = _level
                    self._scheduler['reservoirTarget'] = reservoir_target
                    self._scheduler['reservoirSeeds'] = open_seeds()
                    # The work test is the *runnable* list rather than `open_seeds()`: that counter
                    # also counts banks already in flight or sitting in the completion buffer, so a
                    # pool with almost nothing left to submit could look full while its workers idle.
                    # `len(order)` is exactly what the planner could hand a worker this pass.
                    _create, _count = starvation_action(_level, _now - last_reseed, len(order),
                                                        reservoir_target)
                    if not _create and not self.shutdown.is_set() and not MINIMAL_PLANNER:
                        # A focused fight with nothing runnable is a stall whatever the global window
                        # says, because no other fight's work may be dispatched. Authorise the same
                        # bounded reseed on the dry scope itself, so the search cannot sit Running
                        # with idle workers until the pool-wide measurement happens to catch up.
                        _focused, _focused_count = focused_replenish_action(
                            focus_encounter, len(order), _now - last_reseed, _level)
                        if _focused:
                            _create, _count = True, _focused_count
                            self._scheduler['focusedReplenish'] = \
                                self._scheduler.get('focusedReplenish', 0) + 1
                    if _create and not self.shutdown.is_set() and not MINIMAL_PLANNER:
                        ranked, by_encounter = reseed_ranked(store, limits, ordinals)
                        self._scheduler['reseedByEncounter'] = by_encounter
                        # A starvation reseed is still a new attempt, so it obeys the focus: only the
                        # focused fight may be reseeded while one is set.
                        ranked = focused_encounters(ranked, focus_encounter)
                        if not ranked:
                            self._scheduler['reseedSkipped'] = \
                                self._scheduler.get('reseedSkipped', 0) + 1
                        ids = []
                        for encounter_id in ranked[:4]:
                            _reseed_started = time.monotonic()
                            try:
                                ids = spawn_children(store, encounter_id, _count,
                                                     store.get('proposals') or 0, self._scheduler,
                                                     sources=learner.RESEED_SOURCES, reseed=True)
                            except ValueError:
                                # Every replaceable slot in that encounter is protected, so nothing may
                                # be added there. That is a bounded refusal, not an error: offer the
                                # turn to the next encounter instead of losing the pressure signal.
                                self._scheduler['reseedBlocked'] = \
                                    self._scheduler.get('reseedBlocked', 0) + 1
                                if encounter_id == focus_encounter:
                                    population_blocked = True
                                continue
                            finally:
                                self._scheduler['reseedSeconds'] = self._scheduler.get(
                                    'reseedSeconds', 0.) + time.monotonic() - _reseed_started
                            if ids:
                                break
                        if ids:
                            with store.db:
                                store.set('proposals', (store.get('proposals') or 0) + 1)
                                store.set('reseedCreated',
                                          (store.get('reseedCreated') or 0) + len(ids))
                            for cid in ids:
                                limits[cid] = (learner.DISCOVERY_RUNS, 0)
                            self._scheduler['reseedCreated'] = self._scheduler.get(
                                'reseedCreated', 0) + len(ids)
                            self._scheduler['reseedBySource'] = dict(
                                self._scheduler.get('reseedSources') or {})
                            # Cumulative per encounter, so fairness is auditable even after newer
                            # candidates replace the reseeds that were created (the resident counts in
                            # `reseedByEncounter` are what the bounds are enforced against).
                            created_by_encounter = self._scheduler.setdefault(
                                'reseedCreatedByEncounter', {})
                            key = str(encounter_id)
                            created_by_encounter[key] = created_by_encounter.get(key, 0) + len(ids)
                            created += len(ids)
                            last_reseed = _now
                            self._planner_order = ready(); order = self._planner_order
                    self._scheduler['runnableDiscovery'] = sum(
                        1 for cid, (dlimit, _vlimit) in limits.items()
                        if int((aggregates.get(f'aggregate:{cid}:discovery') or {}).get('n', 0) or 0)
                        < dlimit)
                    self._scheduler['runnableValidation'] = sum(
                        1 for cid, (_dlimit, vlimit) in limits.items()
                        if int((aggregates.get(f'aggregate:{cid}:validation') or {}).get('n', 0) or 0)
                        < vlimit)
                    self._scheduler['reservoirSeeds'] = open_seeds()
                    self._scheduler['openCandidates'] = len(limits)
                    while len(pending) < workers and not self.shutdown.is_set():
                        slot = available_slot()
                        if slot is None:
                            self._scheduler['heldByDuty'] = self._scheduler.get('heldByDuty', 0)+1
                            break          # duty-cycle idle, counted on the same terms as before
                        if not order:
                            self._scheduler['ranOutOfReadySeeds'] = \
                                self._scheduler.get('ranOutOfReadySeeds', 0)+1
                            # Free workers *and* nothing runnable: the one situation the anti-starvation
                            # controller is allowed to react to. Deliberate duty-cycle idle and a busy
                            # coordinator both leave this flag false.
                            starved_pending = True
                            idle_no_work = True
                            break
                        cid, phase, ordinal = order.pop(0)
                        if (cid, phase, ordinal) in reserved:
                            continue
                        scenario = scenario_for(cid)
                        if scenario is None:
                            continue
                        dispatched += submit_ready(cid, phase, ordinal, scenario, slot, reserved)
                    if pending:
                        self._work_started = pending[0]['started']
                    self._scheduler['planSeconds'] = self._scheduler.get(
                        'planSeconds', 0.)+time.monotonic()-_plan_started
                    # A free worker plus nothing to hand it is the only honest "idle" reading, and a
                    # focused search cannot fix it by working elsewhere. Record why, so the status can
                    # show an actionable reason instead of presenting a stalled run as active search.
                    self._scheduler['idleReason'] = (
                        focused_stall_reason(
                            focus_encounter,
                            len(encounter_scope(store.candidate_encounters(), focus_encounter) or ()),
                            open_seeds(), dispatched, created, population_blocked)
                        if idle_no_work else None)
                    # Dispatch has already happened: the pool is fed, so the durable half of the
                    # batch can use the rest of this pass without a worker waiting on it.
                    if self.flush_records(store, pending):
                        dirty = True
                    # Dispatch is done for this pass: the publish owed to the desktop is paid now.
                    if publish_due:
                        publish_if_due()
                except Exception as exc:
                    state, error = 'Paused', str(exc)
                    self._publish(store, state, error)
        except Exception as exc:
            self._startup_mark_failed(exc)
            with self.lock:
                self._publication_epoch += 1
                self.snapshot = dict(self.snapshot, state='Error', error=str(exc),
                                     startupDiagnostics=self._startup_view())
        finally:
            with self.lock:
                self._publication_epoch += 1
            # Release the shared planning service first: cancel every queued job and drop every
            # route so a late result cannot reach a coordinator that is being torn down.
            try:
                self._cancel_all_planning(store)
            except Exception:  # noqa: BLE001 - shutdown must still release the library
                pass
            if self._encounter is not None:
                try:
                    self._encounter.close()
                except Exception:  # noqa: BLE001 - shutdown must still release the library
                    pass
            # Bounded cleanup of the one host-owned shared planner pool; reports surviving PIDs.
            try:
                self._close_planning_pool(timeout=5.0)
            except Exception:  # noqa: BLE001 - bounded cleanup must never mask the real exit
                pass
            try:
                if self._publisher:
                    self._publisher.close()
            finally:
                try:
                    if pool:
                        pool.shutdown(wait=True, cancel_futures=True)
                finally:
                    try:
                        if store:
                            store.close()
                    finally:
                        with self.lock:
                            self.snapshot = dict(self.snapshot, state='Closed')


def tested_regions(candidates, cell):
    """Return joint measured builds, not a rectangular promise between independent extrema."""
    valid = [c for c in candidates if c['selection']['n'] >= VALIDATION_RUNS
             and c['selection']['comparable'] and c['selection']['winInterval'][0] >= .8
             and c.get('family') == cell]
    best = max((c['selection']['callbackMean'] for c in valid), default=None)
    return dict(bestObserved=best, globalOptimum=False,
                recommended=[dict(id=c['id'], stats=c['stats']) for c in valid
                             if c['selection']['callbackMean'] >= best*.95],
                viable=[dict(id=c['id'], stats=c['stats']) for c in valid],
                note='Only these joint builds were tested. No interpolation, independent min/max '
                     'range, or in-game achievability certification is implied.')


# ---------------------------------------------------------------------------------------------
# Four-objective focused portfolio (module import)
# ---------------------------------------------------------------------------------------------
# The portfolio's scheduling/model logic lives in `strategy_optimizer_portfolio` so this module stays
# focused on the store, the scheduler and the combat-free search. The module import is deliberately at
# the *bottom*: the portfolio imports this one at its top, and importing it here (after every constant
# and function above is defined) keeps the dependency a clean one-way edge instead of a cycle. Its
# public names are re-exported lazily, so callers and checkers keep a single optimiser entry point
# without a load-time `from ... import` that a first import of the portfolio would break.
import strategy_optimizer_portfolio as portfolio  # noqa: E402

_PORTFOLIO_EXPORTS = frozenset({
    'PORTFOLIO_ACTIVE_SLOTS', 'PORTFOLIO_AVERAGE_EARNED', 'PORTFOLIO_AVERAGE_POTENTIAL',
    'PORTFOLIO_CAP', 'PORTFOLIO_CHALLENGER_SLOTS', 'PORTFOLIO_HIGHEST_EARNED',
    'PORTFOLIO_HIGHEST_POTENTIAL', 'PORTFOLIO_LANE_SIZE', 'PORTFOLIO_MIN_MEAN_EVIDENCE',
    'PORTFOLIO_NON_TUNABLE_AXES', 'PORTFOLIO_OBJECTIVES', 'PORTFOLIO_RECHECK_SLOTS',
    'PORTFOLIO_RECHECK_MARGIN', 'PORTFOLIO_RECHECK_MIN_SAMPLES', 'PORTFOLIO_SHARE',
    'PORTFOLIO_STABLE_BUDGET_SHARE', 'PORTFOLIO_STABLE_MIN_RUNS',
    'PORTFOLIO_STABLE_PLATEAU', 'PORTFOLIO_STABLE_RECHECK_EVERY', 'PORTFOLIO_STATE_KEY',
    'PORTFOLIO_STATE_VERSION', 'PORTFOLIO_STEP', 'PORTFOLIO_TUNE_AXES_PER_CANDIDATE',
    'PORTFOLIO_TUNE_BREADTH_AXES',
    'PORTFOLIO_TUNE_CHILDREN', 'PORTFOLIO_TUNE_CHILDREN_PER_PROGRAM',
    'PORTFOLIO_TUNE_MAX_PROGRAMS', 'focused_portfolio',
    'focused_portfolio_inputs', 'focused_portfolio_lanes', 'portfolio_axis_tunable',
    'portfolio_challenger_candidates', 'portfolio_entry', 'portfolio_lane_eligible',
    'portfolio_limits', 'portfolio_objective_samples', 'portfolio_objective_value',
    'portfolio_overlap', 'portfolio_recheck_evidence', 'portfolio_run_reading', 'portfolio_state',
    'portfolio_tune_comparison',
    'portfolio_tune_comparisons', 'portfolio_tune_credible', 'portfolio_tune_signature',
    'portfolio_tune_targets',
})


def __getattr__(name):
    """Lazily re-export the portfolio module's public names (see the block comment above)."""
    if name in _PORTFOLIO_EXPORTS:
        return getattr(portfolio, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


