"""Windows WebView2 shell for the local React optimiser and existing battle renderer.

Normal use is through the installed shortcut. No Node server, hosted site or external browser
is used. The loopback server serves only compiled UI and public assets; the native bridge owns
all application operations. Bulk workers never import a browser or renderer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import secrets
import sqlite3
import statistics
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[2] / 'coordination/native-preparation/shared'
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(HERE.parents[2])
APP = HERE.parents[1] / 'artifacts/kingdom-adventures'
SETTINGS = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KingdomAdventurersOptimizer' / 'settings.json'
DEFAULT_LIBRARY = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KingdomAdventurersOptimizer' / 'strategies.sqlite'
sys.path.insert(0, str(HERE))
import strategy_optimizer
from strategy_optimizer import (Optimizer, canonical, chest_count, identity, load_holder,
                                merge_aggregates, potential_chests, seed_pair)
from strategy_optimizer_limits import initialize_worker, terminate_pool, RUN_TIMEOUT_SECONDS
import strategy_evidence
import strategy_experiments
import strategy_encounter_overview


def _frozen_candidate_scenario(db, candidate_id):
    """The exact observed scenario a pruned candidate was dispatched under, from frozen intents only.

    Thin read-only wrapper over the encounter ledger's frozen-intent index so the investigation
    readers can reach a build whose `candidate` row is gone without re-deriving anything from the
    currently selected fight. Returns `{scenario, encounterId, defeatCount, basis, experimentId}` or
    None when no consistent frozen intent names the candidate.
    """
    return strategy_encounter_overview.frozen_scenario_for(db, candidate_id)


def replay_worker(scenario, seeds):
    from strategy_optimizer_adapter import simulate
    return simulate(scenario, seeds, trace=True)


_JOB_ROWS = None
_RANK_TOKENS = ('SS', 'S', 'A', 'B', 'C', 'D', 'E', 'F', 'G')

#: The fewest qualifying observations a build needs before its mean may stand as a fight's average
#: in the Encounter overview. Qualifying means a resolved defeat that carried a measured opportunity
#: (mean potential) or a resolved run with an earned reading, a defeat contributing the loss gate's
#: zero (mean earned). One or two samples is a record, not an average: a single high-potential defeat
#: otherwise reads as the fight's average while n=1, which is exactly the misleading leader this
#: floor removes. Below it the overview reports no leader - unknown/pending - instead of a thin mean;
#: the lifetime maxima are a separate path and stay visible and clickable at any sample count.
AVERAGE_MIN_SAMPLES = 10


#: Newest saved evaluate batches one candidate report returns. The library keeps every job, but a
#: single read stays bounded so a long session still answers compactly; `batchesTruncated` is set
#: when older matching jobs were dropped rather than silently omitted.
EXPERIMENT_BATCH_LIMIT = 20


def _job_rows(app=APP):
    """The canonical combat job identities (Job.csv rows), loaded once.

    Read from the site's own generated identity document rather than re-deriving anything: it is
    already the single owner of `appName` / `sheetName` / `sheetRankToken`, and the renderer resolves
    a fighter through `COMBAT_JOB_ROWS_BY_NAME`, which is keyed by exactly `appName`.
    """
    global _JOB_ROWS
    if _JOB_ROWS is None:
        rows = []
        try:
            document = json.loads((app/'src'/'game-data'/'native-job-identity.json').read_text(encoding='utf-8'))
            rows = [row for row in document.get('jobRows', []) if row.get('appName')]
        except Exception:
            rows = []
        _JOB_ROWS = rows
    return _JOB_ROWS


def _match_job_name(name, rows):
    """(job, rank) read out of the unit's own name, or (None, None) when the name does not say.

    The research fixture names its units after the job they stand in for ("Ninja (A aw20)",
    "Healer (representative Wizard S, assumed/editable)", "Scholar Fodder 1"). The longest job name
    appearing in that text wins, and a rank token is taken only from the text next to it. Nothing is
    defaulted: a name that does not carry a rank keeps none, and a name that names no job stays
    unmatched so the fighter keeps its honest placeholder instead of being drawn as a guess.
    """
    text = str(name or '')
    if not text or not rows:
        return None, None
    lowered = text.lower()
    matches = {row['appName'] for row in rows if row['appName'].lower() in lowered}
    if not matches:
        return None, None
    job = max(matches, key=len)
    tail = text[lowered.rfind(job.lower()) + len(job):]
    head = text[:lowered.rfind(job.lower())]
    rank = None
    for token in _RANK_TOKENS:
        pattern = r'(?<![A-Za-z])' + token + r'(?![A-Za-z])'
        if re.search(pattern, tail) or re.search(pattern, head):
            rank = token
            break
    return job, rank


def replay_visual_setup(scenario, payload, app=APP):
    """Per-unit visual identity for one replay, from what that scenario actually states.

    The runner's replay payload carries the party's real facts - name, human/monster, weapon,
    equipment, skill order - but no job identity, which is why the desktop replay drew labelled
    placeholders instead of fighters. This joins the recovered visual identity the way the site's own
    builder does, and says so: `jobIdentity` names whether the job came from the unit's own name
    (`name-match`) or was not determinable at all. Appearance inputs are never synthesised, so a
    human still renders through the shared loadout renderer rather than from battle art.
    """
    rows = _job_rows(app)
    units = []
    derived = []
    unknown = []
    for unit in payload.get('units', []):
        if unit.get('side') != 'ally':
            continue
        human = bool(unit.get('human'))
        entry = dict(name=unit.get('name'), kind='human' if human else 'monster',
                     weaponId=int(unit.get('weaponId') or 0), weaponMotion=None,
                     shieldId=None,
                     skills=[dict(skillId=int(skill_id),
                                  invocationLevel=max(0, min(2, int(level))),
                                  motion=None, requiredEquipType=None)
                             for skill_id, level in zip(unit.get('skillIds') or [],
                                                        unit.get('invocationLevels') or [])])
        if human:
            job, rank = _match_job_name(unit.get('name'), rows)
            if job:
                entry['jobId'] = job
                entry['jobIdentity'] = 'name-match'
                if rank:
                    entry['rank'] = rank
                derived.append(unit.get('name'))
            else:
                entry['jobIdentity'] = 'not-in-scenario'
                unknown.append(unit.get('name'))
        else:
            entry['monsterId'] = unit.get('monsterId')
        units.append(entry)
    warnings = []
    if derived:
        warnings.append(
            'Job identity for ' + ', '.join(str(name) for name in derived) +
            ' was matched from the unit name in this scenario; the scenario itself states no job or '
            'appearance inputs, so these fighters are drawn by the shared loadout renderer.')
    if unknown:
        warnings.append(
            'No job identity could be determined for ' + ', '.join(str(name) for name in unknown) +
            ', so those fighters keep a labelled placeholder.')
    return dict(visualSetup=dict(encounter=None, units=units),
                jobIdentity=dict(derived=derived, unknown=unknown), warnings=warnings)


def asset_handler(app=APP, bridge=None):
    class Assets(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if bridge is not None and urlsplit(self.path).path == bridge._status_path:
                try:
                    body = bridge._status_transport_body()
                    status = 200
                except Exception:  # noqa: BLE001 - report stale status without exposing host details
                    body = b'{"error":"optimizer status transport unavailable"}'
                    status = 503
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def translate_path(self, path):
            relative = unquote(urlsplit(path).path).lstrip('/') or 'desktop.html'
            # Icon URLs in the shared replay renderer are rooted at `/website_icons/`, while the
            # canonical files live beside the app under `KA-Website/website_icons` (not in Vite's
            # `public/`). Keep this as an exact, traversal-checked mount instead of exposing the
            # surrounding repository through the static handler.
            icon_prefix = 'website_icons/'
            if relative.startswith(icon_prefix):
                icon_root = HERE.parents[1] / 'website_icons'
                icon_file = (icon_root / relative[len(icon_prefix):]).resolve()
                if icon_file.is_relative_to(icon_root.resolve()) and icon_file.is_file():
                    return str(icon_file)
            for root in (app/'desktop-dist', app/'public'):
                resolved = (root/relative).resolve()
                if resolved.is_relative_to(root.resolve()) and resolved.is_file():
                    return str(resolved)
            return str(app/'desktop-dist'/'__missing__')

        def list_directory(self, path):
            self.send_error(404)
            return None

        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'")
            super().end_headers()
    return Assets


class LibraryLock:
    """One app writer per library; OS releases this lock even after a forced exit."""
    def __init__(self, path):
        self.file = open(str(path)+'.lock', 'a+b')
        self.file.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.file.close()
                raise ValueError('This optimiser library is already open in another window.')
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self):
        self.file.close()


_NUMBERED_LIBRARY = re.compile(r'^strategiesv(\d+)\.sqlite$', re.IGNORECASE)


def next_library_path(directory):
    """The next free `strategiesv<N>.sqlite` in `directory`.

    `<N>` is a *user-facing sequence number for the library files themselves*. It is never derived
    from, or written into, the library's search-space/objective version or its schema; it exists so
    the default name in the save dialog never collides with a library the user already has.

    Only the exact numbered convention counts: `strategies.sqlite`, `strategiesv5.notes.sqlite` and
    the like are ignored. If the computed number is already taken the result advances until it is
    free, so a race or a stale listing can never offer an overwrite.
    """
    directory = Path(directory)
    highest = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        match = _NUMBERED_LIBRARY.match(entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    number = highest + 1
    candidate = directory / f'strategiesv{number}.sqlite'
    while candidate.exists():
        number += 1
        candidate = directory / f'strategiesv{number}.sqlite'
    return candidate


# --- Strategy investigation ---------------------------------------------------------------------
#
# The read-only investigation workflow behind the desktop's Strategy Optimiser page: list the builds
# that were searched for one encounter/difficulty, inspect one build's stored attempts and results,
# and rerun its exact frozen scenario on fresh seeds. Listing never resimulates and never writes to
# the optimiser library; ad-hoc trial history lives in its own sidecar file beside the library.

INVESTIGATION_SCHEMA = 'ka-strategy-investigation-1'
MAX_TRIALS_PER_BATCH = 8
TRIAL_WORKERS = 4


def trial_worker(scenario, seeds):
    """One fresh investigation trial through the canonical adapter (compact metrics, no trace)."""
    from strategy_optimizer_adapter import simulate
    return simulate(scenario, seeds, trace=False)


def _readonly_db(path):
    """A read-only connection to the library, so an investigation query can never mutate it."""
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _meta(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _load_holders(db):
    return _meta(db, 'recordHolders', {}) or {}


def _candidate_row(db, candidate_id):
    row = db.execute('SELECT id, label, source, scenario FROM candidate WHERE id=?',
                     (candidate_id,)).fetchone()
    return dict(row) if row else None


def _holder_matches(holder, key, encounter_id, defeat_count):
    """Whether one record holder belongs to `encounter_id`/`defeat_count`.

    The holder carries its own encounter/difficulty fields; older holders without them fall back to
    the `"<metric>:<encounterId>:<defeatCount>"` key rather than being dropped.
    """
    if holder.get('encounterId') is not None and holder.get('defeatCount') is not None:
        return (int(holder['encounterId']) == encounter_id
                and int(holder['defeatCount']) == defeat_count)
    parts = str(key).split(':')
    return (len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit()
            and int(parts[1]) == encounter_id and int(parts[2]) == defeat_count)


def _holder_summary(holder):
    """A holder without its frozen scenario (the scenario travels as its own payload field)."""
    return {field: value for field, value in holder.items() if field != 'scenario'}


def _matching_holder(db, candidate_id, scenario):
    """The record holder a candidate currently holds for its own encounter/difficulty, or None."""
    encounter_id = int(scenario.get('encounterId', -1))
    defeat_count = int(scenario.get('defeatCount') or 0)
    fallback = None
    for key, holder in _load_holders(db).items():
        if holder.get('candidate') != candidate_id:
            continue
        if not _holder_matches(holder, key, encounter_id, defeat_count):
            continue
        if key.startswith('earned:'):
            return _holder_summary(holder)
        if fallback is None:
            fallback = _holder_summary(holder)
    return fallback


def _encounter_candidates(db, encounter_id, defeat_count):
    """Resident candidates for one encounter/difficulty, indexed rather than scenario-scanned.

    The per-candidate index table answers the question without decoding every stored scenario; a
    candidate with no index row (an older or partially written library) is resolved from its own
    scenario instead of being silently left out.
    """
    if _table_exists(db, 'candidate_meta'):
        rows = [dict(r) for r in db.execute(
            'SELECT c.id AS id, c.label AS label, c.source AS source FROM candidate c '
            'JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=? AND m.defeat=? '
            'ORDER BY c.created, c.id', (encounter_id, defeat_count))]
        known = {row['id'] for row in rows}
        for r in db.execute('SELECT c.id AS id, c.label AS label, c.source AS source, '
                            'c.scenario AS scenario FROM candidate c '
                            'LEFT JOIN candidate_meta m ON m.id=c.id WHERE m.id IS NULL'):
            if r['id'] in known:
                continue
            scenario = json.loads(r['scenario'])
            if (int(scenario.get('encounterId', -1)) == encounter_id
                    and int(scenario.get('defeatCount') or 0) == defeat_count):
                rows.append(dict(id=r['id'], label=r['label'], source=r['source']))
        return rows
    rows = []
    for r in db.execute('SELECT id, label, source, scenario FROM candidate ORDER BY created, id'):
        scenario = json.loads(r['scenario'])
        if (int(scenario.get('encounterId', -1)) == encounter_id
                and int(scenario.get('defeatCount') or 0) == defeat_count):
            rows.append(dict(id=r['id'], label=r['label'], source=r['source']))
    return rows


def _sql_chunks(values, size=400):
    """`values` split into slices small enough for one `IN (?,?,...)` clause."""
    values = [value for value in values if value is not None]
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _structured_names(db, candidates):
    """`{candidate id: naming dict}` for one encounter's rows, from lineage and stored scenarios.

    Read-only and bounded to the fight on screen: the lineage rows of its residents, plus the stored
    scenarios of those residents and of their immediate parents (which may have been pruned, in which
    case the row's own recorded change text is used instead). The names are *derived* - the creator
    from the lineage source, the change from diffing the two scenarios - never parsed out of the old
    free-text label, which is returned in `detail` for the tooltip.
    """
    import strategy_naming
    labels = {row['id']: row.get('label') for row in candidates}
    ids = list(labels)
    if not ids or not _table_exists(db, 'lineage'):
        return {cid: strategy_naming.name_for(cid, {}, {}, fallback=labels.get(cid)) for cid in ids}
    lineage = {}
    for chunk in _sql_chunks(ids):
        marks = ','.join('?' * len(chunk))
        for row in db.execute('SELECT candidate,parent,operation,target,change,source FROM lineage '
                              f'WHERE candidate IN ({marks})', chunk):
            lineage[row['candidate']] = dict(row)
    needed = set(ids) | {row['parent'] for row in lineage.values() if row.get('parent')}
    scenarios = {}
    for chunk in _sql_chunks(sorted(needed)):
        marks = ','.join('?' * len(chunk))
        for row in db.execute(f'SELECT id,scenario FROM candidate WHERE id IN ({marks})', chunk):
            try:
                scenarios[row['id']] = json.loads(row['scenario'])
            except (TypeError, ValueError):
                continue
    return {cid: strategy_naming.name_for(cid, lineage, scenarios, fallback=labels.get(cid))
            for cid in ids}


#: Chest counters the shared `merge_aggregates` does not add: it fuses the lane evidence the live
#: optimizer reads (attempts, loss opportunity, maxima), never a chest mean, so calling it on two
#: phase totals keeps only the first phase's chest readings. The desktop averages do read a chest
#: mean, so `_merge_totals` fuses these itself rather than editing the shared module.
_CHEST_TOTAL_KEYS = ('chestCount', 'chestSum', 'chestSum2', 'chestMin', 'chestMax')


def _merge_totals(*totals):
    """One candidate's discovery+validation lifetime counters with the chest readings fused.

    `merge_aggregates` starts from a copy of the first phase and adds only its own keys, so
    `chestCount`/`chestSum`/`chestSum2` (and the chest min/max) from the validation phase are lost.
    Add them here from the raw phase totals. A library whose aggregates never measured a chest keeps
    no chest key at all, so the caller falls back to retained evidence instead of an invented zero.
    """
    totals = [total for total in totals if total]
    if not totals:
        return None
    merged = merge_aggregates(*totals)
    if merged is None:
        return None
    chest_count = sum(int(total.get('chestCount') or 0) for total in totals)
    if chest_count:
        merged['chestCount'] = chest_count
        merged['chestSum'] = sum(float(total.get('chestSum') or 0.) for total in totals)
        merged['chestSum2'] = sum(float(total.get('chestSum2') or 0.) for total in totals)
        minima = [total['chestMin'] for total in totals if total.get('chestMin') is not None]
        maxima = [total['chestMax'] for total in totals if total.get('chestMax') is not None]
        if minima:
            merged['chestMin'] = min(minima)
        if maxima:
            merged['chestMax'] = max(maxima)
    else:
        for key in _CHEST_TOTAL_KEYS:
            merged.pop(key, None)
    return merged


def _lifetime_counters(db, candidate_id):
    """One candidate's persistent lifetime counters, or None when the library never wrote them."""
    totals = [_meta(db, f'aggregate:{candidate_id}:{phase}') for phase in ('discovery', 'validation')]
    totals = [total for total in totals if total]
    return _merge_totals(*totals) if totals else None


def _stored_runs(db, candidate_id):
    """Every retained run row of one candidate, in phase/ordinal order."""
    rows = []
    for row in db.execute('SELECT phase, ordinal, result FROM run WHERE candidate=? '
                          'ORDER BY phase, ordinal', (candidate_id,)):
        rows.append(dict(json.loads(row['result']), phase=row['phase'], ordinal=int(row['ordinal'])))
    return rows


def _resident_index(db):
    """Every resident candidate as `{id, label, source, encounterId, defeatCount}`.

    The `candidate_meta` index answers the encounter/difficulty question without decoding a stored
    scenario; a candidate with no index row (an older or partially written library) is resolved from
    its own scenario instead of being silently dropped, exactly as `_encounter_candidates` does for a
    single encounter. Pruned candidates are absent from `candidate`, so they are absent here too.
    """
    residents = []
    for row in db.execute('SELECT c.id AS id, c.label AS label, c.source AS source, '
                          'c.scenario AS scenario, m.encounter AS encounter, m.defeat AS defeat '
                          'FROM candidate c LEFT JOIN candidate_meta m ON m.id=c.id '
                          'ORDER BY c.created, c.id'):
        if row['encounter'] is None:
            scenario = json.loads(row['scenario'])
            encounter = int(scenario.get('encounterId', -1))
            defeat = int(scenario.get('defeatCount') or 0)
        else:
            encounter, defeat = int(row['encounter']), int(row['defeat'])
        residents.append(dict(id=row['id'], label=row['label'], source=row['source'],
                              encounterId=encounter, defeatCount=defeat))
    return residents


def _resident_run_rows(db):
    """`{candidateId: [result, ...]}` for every resident candidate, in one indexed pass.

    Only runs whose candidate is still in the library are read. A pruned candidate's retained rows
    describe a build that no longer exists, and the maximum-holder path (which keeps its own frozen
    scenario) is what keeps such a record reachable - it is never resurrected here as an average.
    """
    runs = {}
    for row in db.execute('SELECT c.id AS id, r.result AS result FROM candidate c '
                          'JOIN run r ON r.candidate=c.id'):
        runs.setdefault(row['id'], []).append(json.loads(row['result']))
    return runs


def _aggregate_counters(db, candidate_ids):
    """Every resident candidate's merged lifetime counters, from one read of the lane aggregates.

    The same `aggregate:<id>:<phase>` counters `_lifetime_counters` reads per candidate, but taken in
    a single query so the overview does not pay a separate meta lookup for every build it ranks. A
    candidate with no aggregate is omitted rather than given zeroes.
    """
    phases = {}
    keys = [f'aggregate:{cid}:{phase}' for cid in candidate_ids
            for phase in ('discovery', 'validation')]
    if not keys:
        return {}
    query = 'SELECT key, value FROM meta WHERE key IN (' + ','.join('?' for _ in keys) + ')'
    for key, value in db.execute(query, keys):
        parts = key.split(':')
        if len(parts) == 3 and parts[1] in candidate_ids:
            phases.setdefault(parts[1], {})[parts[2]] = json.loads(value)
    counters = {}
    for candidate_id, phase in phases.items():
        merged = _merge_totals(phase.get('discovery'), phase.get('validation'))
        if merged:
            counters[candidate_id] = merged
    return counters


#: The one phase whose compact evidence becomes a build's chest distribution. Discovery runs are
#: exploratory (the search's own widening) and the sidecar's fresh trials are deliberately separate,
#: so neither is ever merged into this histogram.
_DISTRIBUTION_PHASE = 'validation'


def _level_at(levels, cumulative, index):
    """The sample value at rank `index` (0-based) of an ascending, weighted sample."""
    for level, reached in zip(levels, cumulative):
        if index < reached:
            return level
    return levels[-1]


def _weighted_quantile(levels, cumulative, total, fraction):
    """Linear-interpolation quantile of a weighted ascending sample, or None with no samples.

    The interpolation makes no distributional assumption: it reads only the observed chest readings
    and their counts, so an empirical p10/median/p90 is a statement about this evidence alone.
    """
    if not total:
        return None
    if total == 1:
        return float(levels[0])
    position = fraction*(total-1)
    lower_index = int(position)
    upper_index = min(lower_index+1, total-1)
    lower = _level_at(levels, cumulative, lower_index)
    upper = _level_at(levels, cumulative, upper_index)
    return lower + (upper-lower)*(position-lower_index)


def _outcome_distribution(db, candidate_id):
    """One resident build's empirical validation chest histogram, read without decoding a replay.

    The source is the additive `evidence` table, which keeps one compact row per accepted result for
    every validation ordinal - including the runs the rolling `run` replay window has already dropped
    - holding only the outcome fields `chest_count` reads. Those rows are grouped in SQL by exactly
    those fields (verdict, censored, prizeCallbacks, awardedChests, awardedBasis, pendingChests), and
    each distinct group is interpreted through the shared `chest_count` rule, so the histogram cannot
    drift from the label a run card shows: a resolved defeat contributes the native loss gate's zero,
    a win its certified award or queued count at the verdict, and a censored or unreadable run
    contributes nothing (never a fabricated zero). Only the small per-group counts are returned, so
    no full replay JSON is ever loaded.

    Honest limits, stated in the payload rather than smoothed over:
      * `bins` holds only KNOWN chest readings, one `{chests, count}` per distinct value; `unresolved`
        (censored, no verdict) and `unknown` (resolved but carrying no chest reading) are separate
        counts and are never folded in as a zero bucket;
      * `knownSamples` is how many rows produced a reading, `evidenceRows` how many compact rows
        exist and `lifetimeValidationTotal` the lifetime validation attempt count. A legacy library
        whose rows rolled off before the evidence table existed reports `coverage='partial'` with the
        number of missing observations instead of pretending the histogram is complete;
      * the statistics (min/max/mean/p10/median/p90/mode) are `None`, not zero, when nothing was
        measured, and mode ties resolve to the smallest chest value;
      * a library without the evidence table (or a build with none) reports `available=False` with a
        reason instead of an invented distribution.
    """
    if not _table_exists(db, 'evidence'):
        return dict(available=False, phase=_DISTRIBUTION_PHASE,
                    reason='This library has no compact evidence table, so no chest distribution '
                           'can be read for it.')
    groups = db.execute(
        'SELECT verdict, censored, prizeCallbacks, awardedChests, awardedBasis, pendingChests, '
        'COUNT(*) FROM evidence WHERE candidate=? AND phase=? '
        'GROUP BY verdict, censored, prizeCallbacks, awardedChests, awardedBasis, pendingChests',
        (candidate_id, _DISTRIBUTION_PHASE)).fetchall()
    bins = {}
    evidence_rows = unresolved = unknown = 0
    for verdict, censored, prize, awarded, awarded_basis, pending, count in groups:
        count = int(count)
        evidence_rows += count
        row = strategy_evidence.decode(None, verdict, censored, prize, awarded, awarded_basis,
                                       pending)
        value, basis = chest_count(row)
        if value is None:
            if basis == 'unresolved':
                unresolved += count
            else:
                unknown += count
            continue
        value = int(value)
        bins[value] = bins.get(value, 0)+count
    if not evidence_rows:
        return dict(available=False, phase=_DISTRIBUTION_PHASE,
                    reason='This build has no retained validation evidence to distribute.')
    lifetime = _meta(db, f'aggregate:{candidate_id}:{_DISTRIBUTION_PHASE}') or {}
    lifetime_total = int(lifetime['n']) if lifetime.get('n') is not None else None
    if lifetime_total is None:
        coverage, missing = 'unknown', None
    elif evidence_rows >= lifetime_total:
        coverage, missing = 'complete', 0
    else:
        coverage, missing = 'partial', lifetime_total-evidence_rows
    levels = sorted(bins)
    cumulative, running = [], 0
    for level in levels:
        running += bins[level]
        cumulative.append(running)
    known = running
    return dict(
        available=True, phase=_DISTRIBUTION_PHASE,
        bins=[dict(chests=level, count=bins[level]) for level in levels],
        knownSamples=known, evidenceRows=evidence_rows,
        lifetimeValidationTotal=lifetime_total,
        unresolved=unresolved, unknown=unknown,
        coverage=coverage, missingEvidence=missing,
        min=levels[0] if known else None, max=levels[-1] if known else None,
        mean=(sum(level*bins[level] for level in levels)/known) if known else None,
        p10=_weighted_quantile(levels, cumulative, known, .10),
        median=_weighted_quantile(levels, cumulative, known, .50),
        p90=_weighted_quantile(levels, cumulative, known, .90),
        mode=(max(levels, key=lambda level: (bins[level], -level)) if known else None))


def _outranks_mean(leader, entry):
    """Whether `entry` takes the mean crown from `leader`: higher mean, then larger n, then lower id."""
    if leader is None:
        return True
    if entry['mean'] != leader['mean']:
        return entry['mean'] > leader['mean']
    if entry['samples'] != leader['samples']:
        return entry['samples'] > leader['samples']
    return entry['candidateId'] < leader['candidateId']


def _mean_leaders(members, summaries, counters):
    """One fight's `(highest mean earned, highest mean potential)` resident build, or None each.

    Both readings come from `_aggregate_measure` - mean earned over every resolved run the lifetime
    counters saw (a defeat contributing the native loss gate's zero) and mean potential over every
    defeat that carried an opportunity - so they cannot drift from the ranked `encounter_strategies`
    listing, and a meagre replay bank cannot shrink them. A build with no measured sample for a
    metric is skipped rather than ranked, and so is one whose mean rests on fewer than
    `AVERAGE_MIN_SAMPLES` qualifying observations: a one-defeat opportunity is a record, not a fight
    average. The measured sample count, the lifetime attempt count and the retained replay count
    still travel with every mean.
    """
    best = {}
    for member in members:
        summary = summaries.get(member['id']) or _summarize_attempts([])
        measure = _aggregate_measure(summary, counters.get(member['id']), summary['samples'])
        attempts = int(measure['attempts'] or 0)
        for metric, mean, samples in (('earned', measure['meanEarned'], measure['earnedSamples']),
                                      ('potential', measure['meanPotential'],
                                       measure['potentialSamples'])):
            # The floor is per qualifying observation of that metric, for that same build: a fight
            # with four defeats and one high opportunity has no average, only a record.
            if mean is None or samples < AVERAGE_MIN_SAMPLES:
                continue
            entry = dict(candidateId=member['id'], label=member['label'], source=member['source'],
                         mean=mean, samples=samples, attempts=attempts,
                         comparable=bool(measure['comparable']),
                         noVerdict=measure['noVerdict'],
                         retainedSampleCount=measure['retainedSampleCount'])
            if _outranks_mean(best.get(metric), entry):
                best[metric] = entry
    return best.get('earned'), best.get('potential')


def _seed_pairs_from_rows(rows):
    pairs = set()
    for row in rows:
        seeds = row.get('seeds')
        if isinstance(seeds, (list, tuple)) and len(seeds) == 2:
            pairs.add((int(seeds[0]), int(seeds[1])))
    return pairs


def _row_seed_pair(row):
    """The (mathSeed, libSeed) pair one stored row carries, or None when it carries none."""
    seeds = row.get('seeds')
    if isinstance(seeds, (list, tuple)) and len(seeds) == 2 and not isinstance(seeds, (str, bytes)):
        try:
            return (int(seeds[0]), int(seeds[1]))
        except (TypeError, ValueError):
            return None
    return None


def _seed_values(seeds):
    """A requested seed pair as two ints, or a refusal; a bool is never accepted as a seed."""
    if isinstance(seeds, (list, tuple)) and len(seeds) == 2 and not isinstance(seeds, (str, bytes)):
        values = []
        for value in seeds:
            if isinstance(value, bool):
                break
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                break
        else:
            return values
    raise ValueError('seeds must be a [mathSeed, libSeed] pair of whole numbers.')


def _run_payload(row):
    """One run row in the investigation payload's shape, using the canonical chest reading.

    `chests` is the run's released-chest reading and `earned` its contribution to the mean: a
    resolved loss contributes the native gate's zero, a win its labelled award/pending reading, and
    an unresolved run stays null. The raw prize-callback count is carried beside them, never as a
    substitute for the reading.
    """
    value, basis = chest_count(row)
    resolved = not row.get('censored') and row.get('verdict') in (1, 2)
    return dict(row, chests=value, earned=(value if resolved else None),
                potential=potential_chests(row), basis=basis)


def _trial_row(seeds, result, ordinal):
    return dict(result, seeds=[int(seeds[0]), int(seeds[1])], phase='trial', ordinal=ordinal)


def _summarize_attempts(rows, attempts=None):
    """Wins/losses/no-verdict plus earned and potential readings over the rows handed in.

    Earned is each resolved run's own chest reading: a defeat contributes the award gate's zero, a win
    its labelled award/pending count, and a win whose reading is missing contributes nothing. Potential
    is the opportunity a defeat reached, so it is averaged over defeats only. Sample counts travel
    beside the lifetime attempt total, because retention may have rolled off older rows and a mean
    taken over fewer rows must say so.
    """
    wins = losses = no_verdict = 0
    earned, potential = [], []
    best_earned = best_potential = None
    for row in rows:
        verdict = None if row.get('censored') else row.get('verdict')
        if verdict == 2:
            losses += 1
            value, _basis = chest_count(row)
            earned.append(0 if value is None else int(value))
            opportunity = potential_chests(row)
            if opportunity is not None:
                potential.append(int(opportunity))
                best_potential = (opportunity if best_potential is None
                                  else max(best_potential, opportunity))
        elif verdict == 1:
            wins += 1
            value, _basis = chest_count(row)
            if value is not None:
                earned.append(int(value))
                best_earned = value if best_earned is None else max(best_earned, value)
        else:
            no_verdict += 1
    samples = len(rows)
    total = attempts if attempts is not None else samples
    return dict(attempts=total, samples=samples, wins=wins, losses=losses, noVerdict=no_verdict,
                meanEarned=statistics.mean(earned) if earned else None,
                earnedSamples=len(earned),
                eaMeanEarned=None, eaBestEarned=None, eaEarnedSamples=0,
                meanPotential=statistics.mean(potential) if potential else None,
                potentialSamples=len(potential),
                bestEarned=best_earned, bestPotential=best_potential,
                winRate=(wins/total) if total else None,
                comparable=bool(total and no_verdict == 0 and samples == total))


def _aggregate_measure(summary, counters, retained_count):
    """A build's measured summary, taken from the lifetime aggregates wherever they carry the metric.

    `_summarize_attempts` reads the rolling replay bank (at most `MAX_SAMPLES` rows), so its mean and
    sample count shrink with eviction even though the lane aggregates keep counting every recorded
    run. Here the authoritative lifetime `chestSum/chestCount` and `lossPotentialSum/lossCount`
    supersede the retained readings, and the lifetime attempts/wins/losses/censored supersede the
    retained tallies; the actual maxima come from `earnedMax`/`lossPotentialMax`. A metric the
    aggregate never measured - an older library without the chest counters, or a build whose only
    runs were censored - keeps the retained reading rather than a fabricated zero (a missing count
    is never inferred), and `retainedSampleCount` always states how many replay rows remain on disk.
    The returned mapping is in `_summarize_attempts`' own shape, so the ranked list, the overview
    leaders and the detail summary all consume one definition and cannot drift apart.

    Legacy lifetime counters fill only the legacy top-level fields. The encounter-aware window pair
    (`eaMeanEarned`/`eaBestEarned`/`eaEarnedSamples`) is never derived from them - only the ea_*
    ledger supplies it - so a legacy-only build can never present legacy chest readings as a
    compatible Community window, and a known encounter-aware mean never comes from legacy evidence.
    """
    measure = dict(summary)
    measure['retainedSampleCount'] = retained_count
    if not counters:
        return measure
    attempts = int(counters.get('n') or 0)
    censored = int(counters.get('censored') or 0)
    if attempts:
        measure['attempts'] = attempts
        measure['wins'] = int(counters.get('wins') or 0)
        measure['losses'] = int(counters.get('losses') or 0)
        measure['noVerdict'] = censored
        measure['winRate'] = measure['wins']/attempts
    chest_count = int(counters.get('chestCount') or 0)
    if chest_count:
        measure['meanEarned'] = float(counters.get('chestSum') or 0.)/chest_count
        measure['earnedSamples'] = chest_count
    loss_count = int(counters.get('lossCount') or 0)
    if loss_count:
        measure['meanPotential'] = float(counters.get('lossPotentialSum') or 0.)/loss_count
        measure['potentialSamples'] = loss_count
    if chest_count and counters.get('chestMax') is not None:
        # Keep best and mean on the same lifetime chest evidence. The replay rows are a rolling
        # example window and may no longer contain the lifetime max once their mean is superseded.
        measure['bestEarned'] = counters['chestMax']
    elif counters.get('earnedMax') is not None:
        # Older libraries did not persist chest aggregates; preserve their legacy-only maximum.
        measure['bestEarned'] = counters['earnedMax']
    if counters.get('lossPotentialMax') is not None:
        measure['bestPotential'] = counters['lossPotentialMax']
    measure['comparable'] = bool(attempts and censored == 0
                                 and measure.get('earnedSamples') == attempts)
    return measure


def _strategy_rank(entry):
    """Opportunity first, then conversion, then reliability - the order the elite lanes use."""
    return (entry['bestPotential'] if entry['bestPotential'] is not None else -1,
            entry['bestEarned'] if entry['bestEarned'] is not None else -1,
            entry['winRate'] if entry['winRate'] is not None else -1.0,
            entry['candidateId'])


def _apply_ea_evidence(strategy, evidence):
    """Fold one build's persisted ea_* readings into its ranked row without fabricating a mean.

    The legacy `run` ledger and the Community `ea_*` ledger are separate histories, so their counts
    add while a mean is only borrowed from the EA group when the legacy row measured nothing AND the
    EA evidence is one exact identity. Several identities (a different table/experiment/policy/window)
    leave the top-level mean None and publish every window separately, so incompatible conditions are
    never pooled into a combined average.

    Best earned is read from the very window that supplies the mean: one compatible resolved window
    gives that window own `earnedMax` (the maximum `finalEarned` of the same resolved canonical set),
    and several incompatible windows withhold it. A legacy `earnedMax`, a loss `pending`/potential
    reading or an unresolved sample is never used, so a known encounter-aware mean can never pair
    with an unknown or mismatched best. A legacy-only build (no ea_* evidence) keeps its legacy
    mean/max untouched.
    """
    windows = evidence.get('windows') or []
    strategy['attempts'] = int(strategy.get('attempts') or 0) + int(evidence['attempts'] or 0)
    for name in ('wins', 'losses', 'noVerdict'):
        strategy[name] = int(strategy.get(name) or 0) + int(evidence.get(name) or 0)
    strategy['ea'] = evidence
    strategy['windows'] = windows
    single = len(windows) == 1
    if not strategy.get('earnedSamples') and evidence['earnedCount']:
        strategy['meanEarned'] = evidence['meanEarned'] if single else None
        strategy['earnedSamples'] = int(evidence['earnedCount'])
        strategy['bestEarned'] = windows[0].get('earnedMax') if single else None
    # Publish this exact Community window beside legacy aggregates without pooling them.
    if evidence['earnedCount']:
        strategy['eaMeanEarned'] = evidence['meanEarned'] if single else None
        strategy['eaBestEarned'] = windows[0].get('earnedMax') if single else None
        strategy['eaEarnedSamples'] = int(evidence['earnedCount'])
    if not strategy.get('potentialSamples') and evidence['potentialCount']:
        strategy['meanPotential'] = evidence['meanPotential'] if single else None
        strategy['potentialSamples'] = int(evidence['potentialCount'])
    if single and evidence.get('comparable') and not strategy.get('comparable'):
        strategy['comparable'] = True
    attempts = strategy['attempts']
    strategy['winRate'] = (strategy['wins'] / attempts) if attempts else None
    return strategy


def _ea_strategy_row(evidence, fight_row):
    """A ranked row for a build whose only evidence is the persisted ea_* ledger.

    The same exact observation window supplies the row earned mean and its earned maximum: one
    compatible resolved window gives that window own mean/max, several incompatible windows withhold
    the pair (None) rather than pooling them, and the sample count still states the evidence. The
    Community ea_* pair is published beside the top-level pair so an EA-only build exposes the
    encounter-aware window exactly as a resident one does.
    """
    windows = evidence.get('windows') or []
    single = len(windows) == 1
    attempts = int(evidence['attempts'] or 0)
    wins = int(evidence['wins'] or 0)
    best_earned = best_potential = None
    ea_mean = ea_best = None
    if single and windows[0].get('earnedCount'):
        best_earned = windows[0].get('earnedMax')
        ea_mean = windows[0].get('meanEarned')
        ea_best = windows[0].get('earnedMax')
    if fight_row and fight_row.get('potentialCandidateId') == evidence['candidateId']:
        best_potential = fight_row.get('highestPotentialChests')
    return dict(candidateId=evidence['candidateId'], label=evidence.get('label'),
                source=evidence.get('source') or 'ea-ledger', displayName=None, creator=None,
                change=None, parentId=None, parentProducer=None, nameDerived=None, nameDetail=None,
                attempts=attempts, wins=wins, losses=int(evidence['losses'] or 0),
                noVerdict=int(evidence['noVerdict'] or 0), meanEarned=evidence['meanEarned'],
                earnedSamples=int(evidence['earnedCount'] or 0),
                eaMeanEarned=ea_mean, eaBestEarned=ea_best,
                eaEarnedSamples=int(evidence['earnedCount'] or 0),
                meanPotential=evidence['meanPotential'],
                potentialSamples=int(evidence['potentialCount'] or 0), bestEarned=best_earned,
                bestPotential=best_potential, winRate=(wins / attempts) if attempts else None,
                comparable=bool(evidence['comparable']), retainedSampleCount=0, windows=windows,
                ea=evidence)


def _scenario_identity(scenario):
    """The seed-independent identity a trial history is keyed by."""
    return identity(scenario)


def _canonical_formation(scenario):
    """The prepared starting grid for one scenario, straight from `combat_setup.prepare_setup`.

    Row, column and the unit set are whatever the canonical preparation placed for this exact
    scenario - never inferred here. `slot` is that unit's incomingIndex plus one, matching the typed
    team-build slots the desktop renders. A scenario the preparation refuses returns an empty grid
    with the reason, so the rest of the strategy detail stays usable instead of failing whole.
    """
    try:
        from combat_setup import prepare_setup
        prepared = prepare_setup(scenario)
        units = [dict(name=member['name'], slot=member['incomingIndex']+1,
                      row=member['row'], column=member['column'],
                      parameters=member['effectiveParameters'],
                      effectiveDefense=member['effectiveDefense'])
                 for member in prepared['ownUnits']]
        return dict(units=units)
    except Exception as error:
        return dict(units=[], error=f'{type(error).__name__}: {error}')


def _sidecar_path(library):
    return library.with_suffix('.investigation.json')


def _load_sidecar(path):
    if not path.is_file():
        return dict(schema=INVESTIGATION_SCHEMA, scenarios={})
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'the investigation sidecar could not be read ({exc}); no trials were '
                         f'changed.') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('scenarios'), dict):
        raise ValueError('the investigation sidecar is not in the expected shape; no trials were '
                         'changed.')
    payload.setdefault('schema', INVESTIGATION_SCHEMA)
    return payload


def _write_sidecar(path, payload):
    """Atomic replace, so a crash mid-write can never leave a half-written trial history."""
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, path)


def _ledger(sidecar, identity_key, create=False, meta=None):
    """The per-scenario trial ledger, created on demand with the scenario's own identity."""
    scenarios = sidecar.setdefault('scenarios', {})
    ledger = scenarios.get(identity_key)
    if ledger is None and create:
        ledger = dict(meta or {}, trials=[])
        scenarios[identity_key] = ledger
    if ledger is not None:
        ledger.setdefault('trials', [])
    return ledger


def _trial_rows(ledger):
    rows = []
    for trial in (ledger or {}).get('trials') or []:
        result = dict(trial.get('result') or {})
        result.setdefault('seeds', trial.get('seeds'))
        rows.append(dict(result, phase='trial', ordinal=trial.get('ordinal')))
    return rows


def _seed_pairs_from_trials(ledger):
    return _seed_pairs_from_rows(_trial_rows(ledger))


def _fresh_seed_pairs(count, used):
    """`count` fresh investigation seed pairs, absent from `used` and from each other."""
    pairs, chosen = [], set()
    for index in range(1_000_000):
        pair = seed_pair('investigation', index)
        key = (int(pair[0]), int(pair[1]))
        if key in used or key in chosen:
            continue
        chosen.add(key)
        pairs.append([key[0], key[1]])
        if len(pairs) == count:
            return pairs
    raise ValueError('No fresh seed pairs are available; every candidate seed is already recorded.')


def _trial_count(value):
    if isinstance(value, bool):
        raise ValueError(f'count must be a whole number between 1 and {MAX_TRIALS_PER_BATCH}.')
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'count must be a whole number between 1 and {MAX_TRIALS_PER_BATCH}.') from None
    if not 1 <= count <= MAX_TRIALS_PER_BATCH:
        raise ValueError(f'count must be between 1 and {MAX_TRIALS_PER_BATCH}.')
    return count


class Bridge:
    def __init__(self, library, remember=False):
        self._window = None
        self._status_path = '/__optimizer_status/' + secrets.token_urlsafe(32)
        self._library = Path(library)
        self._library.parent.mkdir(parents=True, exist_ok=True)
        self._guard = LibraryLock(self._library)
        self._optimizer = Optimizer(self._library)
        self._operation = threading.Lock()
        # Guards the investigation sidecar only: the read APIs must never wait on a long replay, and
        # a trial batch must never race a detail read of the same file.
        self._sidecar_lock = threading.RLock()
        # Average leaders use retained-run means. Keep each resident build's summary until its run
        # rows change, instead of decoding every resident run on each 30-second overview refresh.
        self._average_lock = threading.Lock()
        self._average_summaries = {}
        # Read-only, incremental projection of the ea_* encounter ledger on its own persistent
        # connection: SQLite connections are thread-affine, so the bridge must not borrow the
        # optimiser's, and a persistent cache means a click resolves an EA candidate without ever
        # rescanning the whole ledger.
        self._ledger_lock = threading.RLock()
        self._ledger_cache = None
        self._ledger_conn = None
        self._ledger_path = None
        self._remember = remember
        self._start_requested = False
        # Exactly one on-demand diagnostic export may run at a time. It is independent of the
        # optimiser: it reads a captured source path with its own read-only connection, so it never
        # pauses, restarts or writes to the live library and is unaffected by a library switch.
        self._export = None

    def _save_settings(self):
        if self._remember:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            temp = SETTINGS.with_suffix('.tmp')
            temp.write_text(json.dumps(dict(library=str(self._library))), encoding='utf-8')
            os.replace(temp, SETTINGS)

    def status(self):
        result = self._optimizer.status()
        result['library'] = str(self._library)
        # Additive, read-only: the capacity ceiling currently in force for this process.
        result['libraryCapacityMiB'] = strategy_optimizer.MAX_DB_MB
        return result

    def status_transport(self):
        """One-time same-origin URL for the cached, read-only browser status projection."""
        self._optimizer.prepare_status_transport()
        return dict(path=self._status_path)

    def _status_transport_body(self):
        optimizer = self._optimizer
        library = str(getattr(optimizer, 'path', self._library))
        base_json, live = optimizer.status_transport_data()
        # A library switch replaces the Optimizer. Never pair an old owner's immutable base with
        # fields from the newly selected library; an in-flight poll can safely retry next interval.
        if optimizer is not self._optimizer:
            raise RuntimeError('Optimizer library changed during status read')
        live['library'] = library
        live['libraryCapacityMiB'] = strategy_optimizer.MAX_DB_MB
        live_json = json.dumps(live, separators=(',', ':'), ensure_ascii=False,
                               allow_nan=False).encode('utf-8')
        return b'{"base":' + base_json + b',"live":' + live_json + b'}'

    def encounter_report(self):
        """Read-only host API: the Community-first runtime report, verbatim.

        It is the same object published as `status()['encounterAware']`; no mutation and no pause.
        """
        return self._optimizer.status().get('encounterAware')

    def encounter_player_regions(self, candidate_limit=None, encounter_id=None):
        """Read-only host API: the player operating-region read model.

        Built from the current encounter-aware snapshot plus a bounded read-only load of each
        snapshot candidate's own stored scenario. It never issues an optimiser command, runs a
        battle, consumes a seed or pools rewards across candidates, and the surface is refreshed
        only by an explicit user action - it is deliberately not wired into any poll loop.
        """
        import strategy_player_regions as regions_model
        try:
            limit = int(candidate_limit) if candidate_limit else regions_model.CANDIDATE_LIMIT
        except (TypeError, ValueError):
            limit = regions_model.CANDIDATE_LIMIT
        if limit <= 0:
            limit = regions_model.CANDIDATE_LIMIT
        try:
            snapshot = self._optimizer.status().get('encounterAware')
            if encounter_id is not None:
                if isinstance(encounter_id, bool) or int(encounter_id) != float(encounter_id):
                    raise ValueError('encounter must be a whole catalogue id')
                selected = int(encounter_id)
                current = (snapshot or {}).get('encounter')
                if isinstance(current, dict):
                    current = current.get('id')
                if current != selected:
                    db = _readonly_db(self._library)
                    try:
                        snapshot = _meta(db, 'encounterPlayerSnapshot:%s' % selected)
                    finally:
                        db.close()
            return regions_model.build_summary_from_library(
                snapshot, self._library, readonly_db=_readonly_db, candidate_limit=limit)
        except Exception as exc:  # noqa: BLE001 - a read API must degrade, never raise to the UI
            summary = regions_model.build_summary(None, {}, candidate_limit=limit)
            summary['reason'] = 'player region summary unavailable: %s' % (exc,)
            return summary

    def command(self, action, value=None):
        # `probe` creates candidates for a threshold/range scan. It is a bounded library write on the
        # same coordinator, so it belongs on this surface with the other commands; without it here
        # the desktop's probe control was rejected as an unknown command before reaching the host.
        # `migrate_objective` upgrades a library written under the old single-score objective onto the
        # lane objective. It is a bounded, explicit library write like `all_encounters`, and it is
        # never performed on open, so it belongs on this surface with the other library commands.
        # `focus_encounter` points every new attempt at one encounter (or clears that focus). It is a
        # scheduling change that is legal while running, so it belongs on this same command surface.
        # `students` sets the split of every encounter's attempts between the student tracks, and
        # `student_report` measures what each track actually received. Both are scheduling surface, not
        # library writes: they change what is dispatched next and never reach a battle, so they belong
        # here with the other running-time controls rather than behind a pause.
        if action not in ('start', 'community_start', 'pause', 'stop', 'keep', 'all_encounters', 'probe',
                          'fine_tune', 'migrate_objective', 'focus_encounter',
                          'focused_experiment', 'end_experiment', 'students', 'student_report',
                          'breakthrough_budget', 'mp_recovery', 'encounter_preview',
                          'encounter_activate', 'encounter_question', 'encounter_rollback',
                          'encounter_budget'):
            return dict(ok=False, error='Unknown command')
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='Please wait for the selected replay or library operation.')
        try:
            if action in ('start', 'community_start'):
                self._start_requested = True
            self._optimizer.command(action, value or {}, wait=True)
            self._start_requested = False
            if action in ('probe', 'fine_tune', 'focused_experiment', 'end_experiment', 'start', 'community_start',
                          'focus_encounter', 'encounter_activate', 'encounter_question',
                          'encounter_rollback', 'encounter_budget', 'encounter_preview',
                          'students', 'breakthrough_budget', 'mp_recovery', 'all_encounters',
                          'migrate_objective'):
                # A refused probe (an illegal ladder, or an axis that is not an established combat
                # input) is reported through the snapshot, not by raising; surface it here too so a
                # caller is never told "ok" about a probe that created nothing.
                reported = self._optimizer.status().get('error')
                if reported:
                    return dict(ok=False, error=reported)
            if action == 'encounter_preview':
                report = self._optimizer.status().get('encounterAware') or {}
                return dict(ok=True, plan=report.get('migrationPreview'))
            return dict(ok=True)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._start_requested = False
            self._operation.release()

    def fine_tune_programs(self, candidate_id=None):
        """Read-only host API: the fine-tuning programs for one parent build (or all of them).

        The programs are published in the status snapshot by the optimizer; this is the inspect entry
        point a later UI uses, so it never mutates the library and needs no pause.
        """
        programs = list(self._optimizer.status().get('fineTune') or [])
        if candidate_id is None:
            return programs
        return [program for program in programs
                if (program.get('parent') or {}).get('candidateId') == candidate_id]

    def _paused(self):
        state = self._optimizer.status()['state']
        if self._start_requested or not self._optimizer.commands.empty() or state in ('Running', 'Saving', 'Opening library'):
            raise ValueError('Pause optimisation and wait for outstanding simulations to save first.')

    def restore_strategy(self, holder_key):
        """Restore an existing frozen record through the coordinator's normal import checks."""
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='Wait for the current replay or library operation.')
        try:
            self._paused()
            scenario, candidate_id, label, holder, resident = self._resolve_target(None, holder_key)
            if not resident:
                self._optimizer.command('import', dict(scenario=scenario,
                    label=label or 'Restored frozen record'), wait=True)
                reported = self._optimizer.status().get('error')
                if reported:
                    return dict(ok=False, error=reported)
                candidate_id = identity(scenario)
                db = _readonly_db(self._library)
                try:
                    if _candidate_row(db, candidate_id) is None:
                        return dict(ok=False, error='The exact frozen build was not restored. '
                                    'An equivalent build may already exist in this library.')
                finally:
                    db.close()
            return dict(ok=True, candidateId=candidate_id)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def import_build(self):
        import webview
        try:
            self._paused()
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN,
                       allow_multiple=False, file_types=('Combat scenario (*.json)',))
            if not paths:
                return dict(ok=True)
            path = Path(paths[0])
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('Import file exceeds 64 MiB')
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(payload, dict) and payload.get('schema') == 'ka-combat-fight-export-1':
                payload = payload.get('scenario')
            self._optimizer.command('import', dict(scenario=payload,
                                                  label=path.stem))
            return dict(ok=True)
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def export_build(self, candidate_id):
        import webview
        try:
            candidate = next(c for c in self.status()['candidates'] if c['id'] == candidate_id)
            paths = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='strategy.scenario.json')
            if not paths:
                return dict(ok=True)
            path = Path(paths[0] if isinstance(paths, (tuple, list)) else paths)
            path.write_text(json.dumps(candidate['scenario'], indent=2), encoding='utf-8')
            return dict(ok=True)
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def new_library(self):
        import webview
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='Wait for the replay to finish.')
        try:
            self._paused()
            # Default to the next free user-facing library number in the current library's own
            # directory; the user can still type any name in the save dialog.
            paths = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=next_library_path(self._library.parent).name)
            if not paths:
                return dict(ok=True)
            path = Path(paths[0] if isinstance(paths, (list, tuple)) else paths)
            # The save dialog asks the operating system whether an existing file should be replaced,
            # because that is what a save dialog is for. Nothing here ever replaces one: an existing
            # path is opened in place, so the answer to that prompt is only ever "open it", and the
            # result says so plainly rather than letting the prompt stand as a threat.
            existed = path.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            result = self._switch_library(path)
            if result.get('ok') and existed:
                result['existed'] = True
                result['notice'] = (f'opened the existing library {path.name}; nothing was replaced. '
                                    'Use Open library to pick an existing library without this prompt.')
            return result
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def open_library(self):
        """Open an *existing* library, with no save dialog and therefore no "replace?" prompt.

        `new_library` uses the SAVE dialog because it offers a name for a book that does not exist yet,
        and picking an existing file there makes the operating system ask whether it should be
        replaced - which reads as "this will overwrite my results" and hides the thing the reader
        actually wants to do. Opening an existing library is a different action, so it gets its own
        dialog, its own label, and never asks about replacing anything: the file is opened in place and
        nothing is written over. The switch itself is the same code path for both.
        """
        import webview
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='Wait for the replay to finish.')
        try:
            self._paused()
            paths = self._window.create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=False,
                file_types=('Optimiser library (*.sqlite;*.sqlite3)', 'All files (*.*)'))
            if not paths:
                return dict(ok=True)
            path = Path(paths[0] if isinstance(paths, (list, tuple)) else paths)
            if not path.is_file():
                return dict(ok=False, error=f'There is no library file at {path}.')
            return self._switch_library(path)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def _switch_library(self, path):
        """Close the open library and open `path` instead. Never truncates or deletes anything.

        An existing library is opened in place: the store creates only the tables a newer build needs
        and leaves every recorded run, candidate and result untouched. Naming the library that is
        already open is a no-op reported as success, and a library another window holds is refused by
        its own lock rather than opened twice.
        """
        path = Path(path)
        if path.resolve() == self._library.resolve():
            return dict(ok=True, library=str(path), unchanged=True)
        guard = LibraryLock(path)
        self._optimizer.command('close')
        self._optimizer.thread.join()
        self._guard.close()
        self._close_encounter_ledger()
        self._guard, self._library = guard, path
        self._optimizer = Optimizer(path)
        self._optimizer.prepare_status_transport()
        self._save_settings()
        return dict(ok=True, library=str(path))

    def export_diagnostics(self, count=None):
        """Save a detailed, shareable log of the most recent battles. Read-only and non-blocking.

        The host owns the save dialog; the export then runs on a background thread against a *captured*
        source path with its own read-only connection. It never calls ``_paused`` and never takes the
        operation lock, so it is allowed while a search is running. The worker/coordinator diagnostics
        are read from ``status()`` exactly once, here, before the thread starts.
        """
        import webview
        import strategy_diagnostic_export as export_module
        if self._export is not None and self._export.running():
            return dict(ok=False, error='A diagnostic export is already running; wait for it to '
                                        'finish before starting another.')
        try:
            requested = export_module.normalize_count(count)
        except ValueError as exc:
            return dict(ok=False, error=str(exc))
        try:
            paths = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename='strategy-diagnostics-%s.zip' % time.strftime('%Y%m%d-%H%M%S'))
        except Exception as exc:  # noqa: BLE001 - a dialog failure is reported, never raised to the UI
            return dict(ok=False, error=str(exc))
        if not paths:
            return dict(ok=True, cancelled=True)
        destination = Path(paths[0] if isinstance(paths, (list, tuple)) else paths)
        source = Path(self._library)
        try:
            if destination.resolve() == source.resolve():
                return dict(ok=False, error='Choose a file other than the library itself; nothing '
                                            'was written.')
        except OSError as exc:
            return dict(ok=False, error=str(exc))
        diagnostics = export_module.diagnostic_view(self.status())
        try:
            job = export_module.start_export(source, destination, requested, diagnostics=diagnostics)
        except Exception as exc:  # noqa: BLE001 - a refused start is a normal result
            return dict(ok=False, error=str(exc))
        self._export = job
        return dict(ok=True, jobId=job.job_id, path=str(destination), count=requested)

    def export_diagnostics_status(self, job_id=None):
        """Cheap cached progress for the one on-demand export. Never touches the library."""
        if self._export is None:
            return dict(ok=False, error='there is no diagnostic export running')
        if job_id is not None and job_id != self._export.job_id:
            return dict(ok=False, error='that diagnostic export job is no longer current')
        return dict(self._export.status(), ok=True)

    def replay(self, candidate_id, seeds):
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='A selected replay is already being prepared.')
        pool = None
        try:
            self._paused()
            status = self.status()
            if not status['compatible']:
                raise ValueError('The simulator changed; this run cannot be reproduced by the current version.')
            candidate = next(c for c in status['candidates'] if c['id'] == candidate_id)
            original = next(r for r in candidate['examples'].values() if r['seeds'] == seeds)
            pool = ProcessPoolExecutor(max_workers=1, initializer=initialize_worker)
            result = pool.submit(replay_worker, candidate['scenario'], seeds).result(timeout=RUN_TIMEOUT_SECONDS)
            if result['digest'] != original['digest']:
                raise ValueError('Replay metrics differ from the stored run; refusing an inaccurate replay.')
            payload = result['replay']
            encoded = canonical(payload).encode()
            if len(encoded) > 32*1024*1024:
                raise ValueError('Selected trace exceeds the 32 MiB replay limit.')
            # Only one cached trace; all interesting seed metadata stays in SQLite.
            target = self._library.with_suffix('.replay.json')
            temp = target.with_suffix('.tmp')
            temp.write_bytes(encoded)
            os.replace(temp, target)
            scenario = dict(candidate['scenario'], mathSeed=seeds[0], libSeed=seeds[1])
            # Join the visual identity the site's own builder would have carried, so the replay
            # draws the units this run actually used instead of placeholders. Additive: the payload,
            # its digest and every combat fact stay exactly as the runner produced them.
            visual = replay_visual_setup(scenario, payload)
            return dict(ok=True, replay=payload, scenario=scenario,
                        formation=_canonical_formation(scenario),
                        visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'],
                        warnings=visual['warnings'])
        except TimeoutError:
            terminate_pool(pool)
            pool = None
            return dict(ok=False, error='Selected replay exceeded 180 seconds; saved results remain intact.')
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            self._operation.release()

    def replay_holder(self, key):
        """Replay a persisted encounter-wide record holder.

        This is the host side of the two primary replay controls inside the Encounter Overview
        ("Replay highest earned" / "Replay highest potential"). The lifetime record holder keeps the
        *frozen scenario and seed pair* of the run that set the record, so the replay is the exact
        battle the displayed number came from and it keeps working after the candidate that produced
        it has been pruned out of the active population. Nothing here looks at the currently selected
        Strategy Family: the key names the encounter/difficulty and the metric, nothing else.

        A holder written before the frozen scenario existed cannot be replayed; that is reported as a
        refusal instead of failing after a click.
        """
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='A selected replay is already being prepared.')
        pool = None
        try:
            self._paused()
            status = self.status()
            if not status['compatible']:
                raise ValueError('The simulator changed; this run cannot be reproduced by the '
                                 'current version.')
            holder = load_holder(self._library, key)
            if holder is None:
                raise ValueError('That record holder is no longer stored in this library.')
            missing = [field for field in ('scenario', 'mathSeed', 'libSeed', 'digest')
                       if holder.get(field) in (None, '')]
            if missing:
                raise ValueError(f'That record holder has no frozen replay data (missing '
                                 f'{", ".join(missing)}), so it cannot be replayed.')
            scenario = dict(holder['scenario'])
            seeds = [int(holder['mathSeed']), int(holder['libSeed'])]
            scenario['mathSeed'], scenario['libSeed'] = seeds
            pool = ProcessPoolExecutor(max_workers=1, initializer=initialize_worker)
            result = pool.submit(replay_worker, scenario, seeds).result(timeout=RUN_TIMEOUT_SECONDS)
            if result['digest'] != holder.get('digest'):
                raise ValueError('The record holder no longer replays to its stored digest; refusing '
                                 'an inaccurate replay.')
            payload = result['replay']
            encoded = canonical(payload).encode()
            if len(encoded) > 32*1024*1024:
                raise ValueError('Selected trace exceeds the 32 MiB replay limit.')
            target = self._library.with_suffix('.replay.json')
            temp = target.with_suffix('.tmp')
            temp.write_bytes(encoded)
            os.replace(temp, target)
            visual = replay_visual_setup(scenario, payload)
            return dict(ok=True, replay=payload, scenario=scenario,
                        formation=_canonical_formation(scenario),
                        visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'],
                        warnings=visual['warnings'], holder=key, value=holder.get('value'),
                        verdict=holder.get('verdict'), mathSeed=seeds[0], libSeed=seeds[1])
        except TimeoutError:
            terminate_pool(pool)
            pool = None
            return dict(ok=False, error='Selected replay exceeded 180 seconds; saved results remain '
                                        'intact.')
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            self._operation.release()

    # --- Strategy investigation ------------------------------------------------------------------

    def _ledger_for(self, identity_key, create=False, meta=None):
        """The per-scenario trial ledger in the sidecar, read or created under the sidecar lock."""
        with self._sidecar_lock:
            sidecar = _load_sidecar(_sidecar_path(self._library))
            return _ledger(sidecar, identity_key, create=create, meta=meta)

    def _record_trials(self, identity_key, scenario, candidate_id, results):
        """Append completed trials to the sidecar and replace the file atomically."""
        # The library is authoritative; the sidecar keeps the convenient manual-run replay list.
        # The bridge has reserved these next seeds while paused under its operation lock.
        reservation = getattr(self, '_manual_reservation', None)
        if reservation is None or reservation[0] != candidate_id:
            raise ValueError('Fresh trial has no validation seed reservation.')
        self._optimizer.command('_record_manual_trials', dict(candidateId=candidate_id,
            start=reservation[1], results=[dict(result, seeds=list(seeds)) for seeds, result in results]), wait=True)
        if self.status().get('error'):
            raise ValueError(self.status()['error'])
        path = _sidecar_path(self._library)
        with self._sidecar_lock:
            sidecar = _load_sidecar(path)
            ledger = _ledger(sidecar, identity_key, create=True,
                             meta=dict(encounterId=int(scenario.get('encounterId', -1)),
                                       defeatCount=int(scenario.get('defeatCount') or 0),
                                       candidate=candidate_id))
            ordinal = max((int(trial.get('ordinal', -1)) for trial in ledger['trials']),
                          default=-1)+1
            for seeds, result in results:
                ledger['trials'].append(dict(mathSeed=int(seeds[0]), libSeed=int(seeds[1]),
                                             seeds=[int(seeds[0]), int(seeds[1])],
                                             ordinal=ordinal, created=time.time(), result=result))
                ordinal += 1
            _write_sidecar(path, sidecar)

    def _reserve_manual_trials(self, scenario, candidate_id, label, resident, count):
        if not resident:
            self._optimizer.command('import', dict(scenario=scenario, label=label or 'Restored build'), wait=True)
            if self.status().get('error'):
                raise ValueError(self.status()['error'])
            candidate_id = identity(scenario)
        db = _readonly_db(self._library)
        try:
            if not _candidate_row(db, candidate_id):
                raise ValueError('The exact build could not be restored for recording.')
            row = db.execute("SELECT MAX(ordinal) FROM run WHERE candidate=? AND phase='validation'",
                             (candidate_id,)).fetchone()
            start = 0 if row[0] is None else int(row[0]) + 1
        finally:
            db.close()
        self._manual_reservation = (candidate_id, start)
        return candidate_id, [list(seed_pair('validation', start + index)) for index in range(count)]

    def _stored_for(self, candidate_id, resident):
        """A resident candidate's retained run rows and lifetime counters; `([], None)` if pruned."""
        if not resident or not candidate_id:
            return [], None
        db = _readonly_db(self._library)
        try:
            db.execute('BEGIN')
            return _stored_runs(db, candidate_id), _lifetime_counters(db, candidate_id)
        finally:
            db.close()

    def _encounter_ledger(self):
        """`(cache, connection)` for the bridge's own incremental ea_* read model, or `(None, None)`.

        The cache and its read-only connection stay alive across clicks and are rebuilt only when the
        library changes, so the first EA read pays the one-time catch-up scan and every later read is
        a cheap change-signature short-circuit. Read-only: nothing here writes to the library.
        """
        lock = getattr(self, '_ledger_lock', None)
        if lock is None:
            lock = self._ledger_lock = threading.RLock()
        with lock:
            cache = getattr(self, '_ledger_cache', None)
            conn = getattr(self, '_ledger_conn', None)
            path = getattr(self, '_ledger_path', None)
            if cache is not None and conn is not None and path == self._library:
                return cache, conn
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - a broken old connection must not block a new read
                    pass
                conn = None
            try:
                conn = _readonly_db(self._library)
            except Exception:  # noqa: BLE001 - a library with no ledger simply has no EA reader
                self._ledger_cache = None
                self._ledger_path = None
                return None, None
            self._ledger_cache = strategy_encounter_overview.LedgerCache()
            self._ledger_conn = conn
            self._ledger_path = self._library
            return self._ledger_cache, conn

    def _close_encounter_ledger(self):
        lock = getattr(self, '_ledger_lock', None)
        if lock is None:
            return
        with lock:
            conn = getattr(self, '_ledger_conn', None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            self._ledger_cache = None
            self._ledger_conn = None
            self._ledger_path = None

    def _fight_ledger_evidence(self, encounter_id, defeat_count):
        cache, conn = self._encounter_ledger()
        if cache is None:
            return None
        with self._ledger_lock:
            try:
                return cache.fight_evidence(encounter_id, defeat_count, db=conn)
            except Exception:  # noqa: BLE001 - report absence, never fabricate evidence
                return None

    def _candidate_ledger_evidence(self, candidate_id):
        cache, conn = self._encounter_ledger()
        if cache is None:
            return None
        with self._ledger_lock:
            try:
                return cache.candidate_evidence(candidate_id, db=conn)
            except Exception:  # noqa: BLE001
                return None

    def experiment_report(self, candidate_id=None):
        """The current focused experiment, plus one candidate's saved evaluate batches when asked.

        `candidate_id` is optional and additive: without it this returns exactly the previous reply,
        `experiment` only. With it, `batches` lists that candidate's saved `evaluate` jobs from
        `focusedExperimentHistory` plus the current `focusedExperiment`, newest first and deduped by
        job id, each keeping its original job fields and gaining `comparisons` from
        `strategy_experiments.report` (canonical reward semantics; unresolved runs stay absent rather
        than being replaced). `completed`/`total` come from that batch's own evidence rows, never the
        lifetime counters, so a reopened library still reports what the batch itself recorded.

        The reply is bounded to the newest `EXPERIMENT_BATCH_LIMIT` matches; `batchesTruncated`
        states whether older matching jobs were dropped. Malformed history entries that cannot be
        reported are skipped rather than failing the whole read.
        """
        db = _readonly_db(self._library)
        try:
            db.execute('BEGIN')
            current = _meta(db, strategy_experiments.KEY)
            payload = dict(ok=True, experiment=strategy_experiments.report(db, current))
            if candidate_id is not None:
                history = _meta(db, strategy_experiments.HISTORY, []) or []
                batches, seen = [], set()
                for job in [current] + list(reversed(history)):
                    if not isinstance(job, dict) or job.get('id') in seen:
                        continue
                    seen.add(job.get('id'))
                    arms = job.get('arms')
                    if (job.get('mode') != 'evaluate' or job.get('candidateId') != candidate_id
                            or not isinstance(arms, list) or not arms
                            or job.get('pairedStart') is None or job.get('pairedEnd') is None
                            or any(not isinstance(arm, dict) or arm.get('candidateId') is None
                                   or arm.get('start') is None or arm.get('target') is None
                                   for arm in arms)):
                        continue
                    report = strategy_experiments.report(db, job)
                    report.update(strategy_experiments.batch_progress(db, job))
                    batches.append(report)
                payload.update(batches=batches[:EXPERIMENT_BATCH_LIMIT],
                               batchLimit=EXPERIMENT_BATCH_LIMIT,
                               batchesTruncated=len(batches) > EXPERIMENT_BATCH_LIMIT)
            return payload
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            db.close()

    def start_strategy_experiment(self, candidate_id=None, holder_key=None, count=4096, mode='evaluate'):
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='Wait for the current replay or library operation.')
        try:
            self._paused()
            scenario, cid, label, _holder, resident = self._resolve_target(candidate_id, holder_key)
            if not resident:
                self._optimizer.command('import', dict(scenario=scenario, label=label or 'Restored build'), wait=True)
                if self.status().get('error'):
                    raise ValueError(self.status()['error'])
                cid = identity(scenario)
            self._optimizer.command('focused_experiment', dict(candidateId=cid, count=count, mode=mode), wait=True)
            status = self.status()
            if status.get('error'):
                raise ValueError(status['error'])
            return dict(ok=True, candidateId=cid)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def _outcome_distribution_for(self, candidate_id, resident):
        """The build's compact validation chest histogram, or an unavailable marker.

        A frozen record holder whose candidate was pruned keeps no evidence at all (`evidence` rows
        are removed with their candidate), and the sidecar's fresh trials are a separate exploratory
        ledger, so neither is presented as a lifetime validation distribution.
        """
        if not resident or not candidate_id:
            return dict(available=False, phase=_DISTRIBUTION_PHASE,
                        reason='This target is a frozen record holder, not a resident build, so it '
                               'has no lifetime validation evidence.')
        db = _readonly_db(self._library)
        try:
            return _outcome_distribution(db, candidate_id)
        finally:
            db.close()

    def _resolve_target(self, candidate_id, holder_key):
        """Resolve exactly one investigation target to its frozen scenario and identity.

        Returns `(scenario, candidateId, label, holder, resident)`. The holder form freezes the
        scenario that travels with the record, so it stays usable after the candidate that set it was
        pruned; `resident` then reports whether that candidate is still in the library.
        """
        if (candidate_id is None) == (holder_key is None):
            raise ValueError('Provide exactly one of candidate_id or holder_key.')
        if holder_key is not None:
            holder = load_holder(self._library, holder_key)
            if holder is None:
                raise ValueError('That record holder is not stored in this library.')
            scenario = dict(holder.get('scenario') or {})
            if not scenario:
                raise ValueError('That record holder has no frozen scenario, so it cannot be '
                                 'investigated.')
            if isinstance(holder.get('mathSeed'), int) and isinstance(holder.get('libSeed'), int):
                scenario['mathSeed'], scenario['libSeed'] = int(holder['mathSeed']), int(holder['libSeed'])
            cid = holder.get('candidate')
            db = _readonly_db(self._library)
            try:
                row = _candidate_row(db, cid) if cid else None
            finally:
                db.close()
            return (scenario, cid, (row['label'] if row else holder.get('label')),
                    _holder_summary(holder), row is not None)
        db = _readonly_db(self._library)
        try:
            row = _candidate_row(db, candidate_id)
            if row is None:
                # The candidate row was pruned, but its EA samples may still name the exact frozen
                # scenario it ran under. Recover that rather than refusing the reading; a candidate
                # with no consistent frozen intent keeps the honest refusal instead of a guess.
                found = _frozen_candidate_scenario(db, candidate_id)
                if not found:
                    raise ValueError('That candidate is not in this library and no frozen observed '
                                     'scenario names it.')
                scenario = dict(found['scenario'])
                return scenario, str(candidate_id), scenario.get('label'), None, False
            scenario = json.loads(row['scenario'])
            holder = _matching_holder(db, candidate_id, scenario)
        finally:
            db.close()
        return scenario, row['id'], row['label'], holder, True

    def _detail_payload(self, scenario, candidate_id, label, holder, resident, batch_rows=None):
        identity_key = _scenario_identity(scenario)
        stored_rows, counters = self._stored_for(candidate_id, resident)
        ledger = self._ledger_for(identity_key)
        trial_rows = _trial_rows(ledger)
        if batch_rows:
            recorded = _seed_pairs_from_rows(trial_rows)
            trial_rows = trial_rows+[row for row in batch_rows
                                     if tuple(row.get('seeds') or ()) not in recorded]
        stored_summary = _aggregate_measure(
            _summarize_attempts(stored_rows, attempts=(counters or {}).get('n')),
            counters, len(stored_rows))
        return dict(ok=True, candidateId=candidate_id, label=label, scenario=scenario, holder=holder,
                    resident=bool(resident),
                    storedRuns=[_run_payload(row) for row in stored_rows],
                    trialRuns=[_run_payload(row) for row in trial_rows],
                    storedSummary=stored_summary,
                    trialSummary=_summarize_attempts(trial_rows),
                    # The candidate's own persisted ea_* readings, by exact observation identity, so
                    # a Community build shows its measured attempts and retained examples even when
                    # the legacy run table is empty or the candidate row was pruned.
                    encounterLedger=(self._candidate_ledger_evidence(candidate_id)
                                     if candidate_id else None),
                    outcomeDistribution=self._outcome_distribution_for(candidate_id, resident),
                    formation=_canonical_formation(scenario))

    def encounter_strategies(self, encounter_id, defeat_count=0):
        """Every resident build for one encounter/difficulty, with its stored attempts and readings.

        Read-only: it never resimulates and never writes. Each entry's means, sample counts and
        maxima come from the candidate's lifetime lane aggregates (`_aggregate_measure`), which keep
        counting every recorded run past the rolling replay bank, so a rolled-off retention can no
        longer shrink or bias a mean. `earnedSamples`/`potentialSamples` state the measured samples,
        `retainedSampleCount` how many replay rows are still on disk, and both travel beside the
        lifetime `attempts`. A record holder whose candidate was pruned away is returned as a
        synthetic navigable entry so the frozen record stays reachable.
        """
        try:
            encounter_id, defeat_count = int(encounter_id), int(defeat_count)
        except (TypeError, ValueError):
            return dict(ok=False, error='encounter_id and defeat_count must be whole numbers.')
        if encounter_id < 0 or defeat_count < 0:
            return dict(ok=False, error='encounter_id and defeat_count must not be negative.')
        db = None
        try:
            db = _readonly_db(self._library)
            db.execute('BEGIN')
            candidates = _encounter_candidates(db, encounter_id, defeat_count)
            resident = {row['id'] for row in candidates}
            names = _structured_names(db, candidates)
            strategies = []
            for row in candidates:
                stored_rows = _stored_runs(db, row['id'])
                summary = _aggregate_measure(_summarize_attempts(stored_rows),
                                             _lifetime_counters(db, row['id']), len(stored_rows))
                named = names.get(row['id']) or {}
                strategies.append(dict(
                    candidateId=row['id'], label=row['label'], source=row['source'],
                    # One exact build per row: `displayName` describes *this* candidate (creator, the
                    # actual old->new change, and the immediate parent's producer), while `label` stays
                    # as recorded and travels in the tooltip with the ids it carries.
                    displayName=named.get('name'), creator=named.get('creator'),
                    change=named.get('change'), parentId=named.get('parent'),
                    parentProducer=named.get('parentProducer'), nameDerived=named.get('derived'),
                    nameDetail=named.get('detail'),
                    attempts=summary['attempts'], wins=summary['wins'], losses=summary['losses'],
                    noVerdict=summary['noVerdict'], meanEarned=summary['meanEarned'],
                    earnedSamples=summary['earnedSamples'], meanPotential=summary['meanPotential'],
                    potentialSamples=summary['potentialSamples'], bestEarned=summary['bestEarned'],
                    bestPotential=summary['bestPotential'], winRate=summary['winRate'],
                    comparable=summary['comparable'],
                    retainedSampleCount=summary['retainedSampleCount']))
            # Fold the persisted ea_* readings in, so a Community build shows its measured attempts
            # and readings (never a false 0 / unmeasured) and an EA-only build - including one whose
            # candidate row was pruned - gets a real, navigable row keyed by the same candidate id the
            # overview's ea block publishes.
            evidence = self._fight_ledger_evidence(encounter_id, defeat_count)
            ea_by_candidate = {}
            if evidence:
                for candidate in evidence.get('candidates') or []:
                    ea_by_candidate[str(candidate['candidateId'])] = candidate
            for strategy in strategies:
                ea = ea_by_candidate.pop(str(strategy['candidateId']), None)
                if ea is not None:
                    _apply_ea_evidence(strategy, ea)
            for candidate_id, ea in ea_by_candidate.items():
                strategies.append(_ea_strategy_row(ea, (evidence or {}).get('row')))
            strategies.sort(key=_strategy_rank, reverse=True)
            covered = {str(entry['candidateId']) for entry in strategies}
            orphans = []
            for key, holder in _load_holders(db).items():
                if not _holder_matches(holder, key, encounter_id, defeat_count):
                    continue
                # A candidate that now has a real ranked row (resident or EA) must not also appear as
                # an orphan holder: the legacy `earned:<id>:0` path no longer owns EA candidates.
                if str(holder.get('candidate')) in covered:
                    continue
                entry = dict(key=key, value=holder.get('value'), candidateId=holder.get('candidate'))
                if holder.get('label'):
                    entry['label'] = holder['label']
                orphans.append(entry)
            orphans.sort(key=lambda entry: entry['key'])
            return dict(ok=True, strategies=strategies, orphanHolders=orphans)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            if db is not None:
                db.close()

    def overview_average_leaders(self):
        """Every encounter/difficulty's best MEASURED MEAN, for the overview's average columns.

        The Encounter overview's two lifetime records (highest potential, highest chests earned) are
        maxima over a fight's whole history; this is the other half of each row: the resident build
        whose *average* resolved run was best, ranked independently for

          * mean earned - the lifetime `chestSum/chestCount`, i.e. every resolved run's own chest
            reading with a defeat contributing the native loss gate's zero, and
          * mean potential - the lifetime `lossPotentialSum/lossCount`, the chest opportunity every
            resolved DEFEAT reached, averaged over the defeats that carry one, never a win's
            conversion.

        Both are `_aggregate_measure`'s numbers, taken from the lane aggregates rather than the
        rolling replay bank, and are the same numbers `encounter_strategies` and the detail summary
        show for a build, so it cannot read one way in the overview and another way in the ranked
        list. Each leader carries its candidateId, label, source, mean, measured sample count,
        lifetime attempt count, retained replay count and comparability so a thin mean stays visible.

        Honest limits, by construction:
          * a resident build with no measured sample for a metric is excluded from that metric, and a
            fight whose remaining builds were never measured (or that has no resident build left, its
            holder's candidate having been pruned) returns `None` for the metric rather than falling
            back to a best max. The frozen record holders are a separate path and are never consulted
            here;
          * a metric needs at least `AVERAGE_MIN_SAMPLES` qualifying observations for the same build
            before its mean can lead: ten defeats that carried a measured opportunity for mean
            potential, ten resolved runs with an earned reading for mean earned. A fight whose best
            build is still thinner than that returns `None` - the page renders unknown/pending - and
            a one-defeat opportunity is never presented as an average. `samples`, `attempts` and
            `retainedSampleCount` travel with every mean that does qualify;
          * probes are not filtered out: a probe that truly holds the highest measured mean is
            returned with `source: 'probe'` so the page can label it;
          * ties resolve deterministically to the larger sample count, then the lower candidate id.

        Read-only: indexed row counts supply replay coverage, and lifetime aggregates supply the
        means. Only legacy builds missing a required aggregate decode retained rows, cached by
        fingerprint. A rolled-off observation still contributes to its lifetime mean.
        """
        # Community measurements already have a cached projection, refreshed with status.
        # Do not scan legacy run banks to answer a Community overview request.
        with self._optimizer.lock:
            snapshot = self._optimizer.snapshot or {}
            cached = snapshot.get('encounterAverageLeaders')
            if cached is not None and snapshot.get('encounterRuns', 0) > 0:
                return deepcopy(cached)
        db = None
        self._average_lock.acquire()
        try:
            db = _readonly_db(self._library)
            # Keep fingerprints, retained rows and lifetime counters on one WAL snapshot.
            db.execute('BEGIN')
            residents = _resident_index(db)
            fingerprints = {row['candidate']: (int(row['count']), row['latest'])
                            for row in db.execute(
                                'SELECT r.candidate AS candidate, COUNT(*) AS count, '
                                'MAX(r.rowid) AS latest FROM candidate c JOIN run r '
                                'ON r.candidate=c.id GROUP BY r.candidate')}
            cached = self._average_summaries
            counters = _aggregate_counters(db, {resident['id'] for resident in residents})
            summaries, retained_cache = {}, {}
            for resident in residents:
                cid = resident['id']
                fingerprint = fingerprints.get(cid, (0, None))
                total = counters.get(cid) or {}
                no_losses = (int(total.get('n') or 0) ==
                             int(total.get('wins') or 0)+int(total.get('censored') or 0))
                if (int(total.get('chestCount') or 0) > 0
                        and (int(total.get('lossCount') or 0) > 0 or no_losses)):
                    # _mean_leaders needs no per-run values when both means have counters.
                    # Keep the real retained count, but do not cache this as a decoded summary:
                    # removing legacy counters later must fall back to the actual rows.
                    summaries[cid] = dict(_summarize_attempts([]), samples=fingerprint[0])
                else:
                    previous = cached.get(cid)
                    summary = (previous[1] if previous and previous[0] == fingerprint
                               else _summarize_attempts(_stored_runs(db, cid)))
                    summaries[cid] = summary
                    retained_cache[cid] = (fingerprint, summary)
            self._average_summaries = retained_cache
            groups = {}
            for resident in residents:
                key = (resident['encounterId'], resident['defeatCount'])
                groups.setdefault(key, []).append(resident)
            leaders = []
            for encounter_id, defeat_count in sorted(groups):
                members = groups[(encounter_id, defeat_count)]
                earned, potential = _mean_leaders(members, summaries, counters)
                leaders.append(dict(encounterId=encounter_id, defeatCount=defeat_count,
                                    residentCount=len(members),
                                    highestAvgEarned=earned, highestAvgPotential=potential))
            return dict(ok=True, leaders=leaders, encounters=len(leaders),
                        minSamples=AVERAGE_MIN_SAMPLES)
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            if db is not None:
                db.close()
            self._average_lock.release()

    def strategy_detail(self, candidate_id=None, holder_key=None):
        """One build's frozen scenario, stored attempts and trial history, by candidate or holder.

        Exactly one target is required. The holder form reads the frozen scenario out of the library
        so it works after pruning, and returns empty `storedRuns` when the candidate is gone; the
        candidate form reads its persisted scenario and every retained run row.
        """
        try:
            scenario, candidate_id, label, holder, resident = self._resolve_target(candidate_id,
                                                                                   holder_key)
            return self._detail_payload(scenario, candidate_id, label, holder, resident)
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def _simulate_trials(self, scenario, seed_pairs):
        """Run the batch on bounded workers; return `(completed, error)`.

        `completed` holds `(seeds, compact_result)` in request order for every trial that finished
        before any failure, so a failed trial never throws away the ones already run. The simulator is
        the canonical `strategy_optimizer_adapter.simulate(..., trace=False)`; nothing here scores,
        re-banks seeds or touches the search's own counters.
        """
        pool = ProcessPoolExecutor(max_workers=min(TRIAL_WORKERS, len(seed_pairs)),
                                   initializer=initialize_worker)
        completed = []
        try:
            submitted = [(list(seeds), pool.submit(trial_worker, scenario, list(seeds)))
                         for seeds in seed_pairs]
            for seeds, future in submitted:
                try:
                    completed.append((seeds, future.result(timeout=RUN_TIMEOUT_SECONDS)))
                except TimeoutError:
                    terminate_pool(pool)
                    pool = None
                    return completed, (f'Trial {seeds[0]}/{seeds[1]} exceeded {RUN_TIMEOUT_SECONDS} '
                                       f'seconds; completed trials were kept.')
                except Exception as exc:
                    return completed, f'Trial {seeds[0]}/{seeds[1]} failed: {exc}'
        finally:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        return completed, None

    def run_strategy(self, candidate_id=None, holder_key=None, count=8):
        """Rerun one exact frozen scenario on 1..8 fresh independent seed pairs and persist the trials.

        Refuses while optimisation is active, while the simulator is incompatible, or while another
        replay/trial batch holds the operation lock. Every new batch draws seed pairs absent from both
        the build's stored runs and its earlier trials, and each trial's seed pair, metrics and digest
        are recorded through the coordinator into library evidence and counters. The sidecar retains
        the manual replay list as well; it is not added a second time to totals.
        """
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='A replay or trial batch is already running.')
        try:
            count = _trial_count(count)
            self._paused()
            status = self.status()
            if not status.get('compatible'):
                raise ValueError('The simulator changed; fresh trials cannot be reproduced by this '
                                 'version.')
            scenario, candidate_id, label, holder, resident = self._resolve_target(candidate_id,
                                                                                   holder_key)
            identity_key = _scenario_identity(scenario)
            stored_rows, _counters = self._stored_for(candidate_id, resident)
            used = _seed_pairs_from_rows(stored_rows)
            if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                used.add((int(scenario['mathSeed']), int(scenario['libSeed'])))
            used |= _seed_pairs_from_trials(self._ledger_for(identity_key))
            candidate_id, seed_pairs = self._reserve_manual_trials(scenario, candidate_id, label, resident, count)
            resident = True
            results, error = self._simulate_trials(scenario, seed_pairs)
            batch_rows = [_trial_row(pair, result, index)
                          for index, (pair, result) in enumerate(results)]
            persist_error = None
            if results:
                try:
                    self._record_trials(identity_key, scenario, candidate_id, results)
                except Exception as exc:
                    persist_error = str(exc)
            payload = self._detail_payload(scenario, candidate_id, label, holder, resident,
                                           batch_rows=batch_rows if persist_error else None)
            if batch_rows:
                payload['batchSummary'] = _summarize_attempts(batch_rows)
            if persist_error:
                detail = {key: value for key, value in payload.items() if key != 'ok'}
                return dict(ok=False, error=f'Completed trials could not be saved: {persist_error}',
                            **detail)
            if error:
                detail = {key: value for key, value in payload.items() if key != 'ok'}
                return dict(ok=False, error=error, **detail)
            return payload
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def _trace_replay(self, scenario, seeds):
        """The one expensive simulator call a replay or fresh visual simulation makes, on its own
        bounded worker.

        One Python process running the canonical `trace=True` path, bounded by
        `RUN_TIMEOUT_SECONDS`, exactly like the existing `replay`/`replay_holder` calls. A timeout
        terminates the stray worker rather than leaving it to outlive the call. This is the only seam
        a focused test has to replace to exercise the replay or fresh-simulation logic without a real
        battle.
        """
        pool = ProcessPoolExecutor(max_workers=1, initializer=initialize_worker)
        try:
            return pool.submit(replay_worker, scenario, seeds).result(timeout=RUN_TIMEOUT_SECONDS)
        except TimeoutError:
            terminate_pool(pool)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _recorded_run_digest(self, scenario, candidate_id, resident, holder, seeds):
        """The digest one target persisted for the run on `seeds`, or None when it kept none.

        A seed pair counts as recorded only where the target itself kept it: a retained `run` row,
        the scenario's own investigation trial sidecar, or the record holder's frozen pair. A pair
        found nowhere - or a record that carries no digest - is never accepted as a verification
        target, so the replay can only be checked against a number the page actually displays.
        """
        wanted = (int(seeds[0]), int(seeds[1]))
        stored_rows, _counters = self._stored_for(candidate_id, resident)
        for row in stored_rows:
            if _row_seed_pair(row) == wanted:
                return row.get('digest')
        for row in _trial_rows(self._ledger_for(_scenario_identity(scenario))):
            if _row_seed_pair(row) == wanted:
                return row.get('digest')
        if holder is not None and (holder.get('mathSeed'), holder.get('libSeed')) == wanted:
            return holder.get('digest')
        return None

    def replay_strategy_run(self, candidate_id=None, holder_key=None, seeds=None):
        """Reproduce one recorded investigation run - stored, fresh trial or frozen holder - exactly.

        The page's run tables call this rather than `replay`: `replay` only accepts a seed pair the
        candidate's own example bank holds, so a fresh trial (whose seed pair exists only in the
        investigation sidecar) and a stored run of a candidate that has since been pruned both fell
        through to an unresolvable example. This resolves the target's exact frozen scenario the same
        way `strategy_detail` does, accepts a seed pair only when the target actually recorded it (a
        retained run row, an investigation trial, or the record holder itself), reruns the canonical
        trace and verifies the result against the digest that record persisted. An unrecorded pair,
        or a rerun whose digest disagrees, is refused with its own reason. The optimiser library is
        only ever read; the one cached trace is written beside it, never into it.
        """
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='A selected replay is already being prepared.')
        try:
            self._paused()
            status = self.status()
            if not status.get('compatible'):
                raise ValueError('The simulator changed; this run cannot be reproduced by the '
                                 'current version.')
            scenario, candidate_id, label, holder, resident = self._resolve_target(candidate_id,
                                                                                   holder_key)
            seeds = _seed_values(seeds)
            digest = self._recorded_run_digest(scenario, candidate_id, resident, holder, seeds)
            if digest is None:
                raise ValueError('That seed pair is not one of this target\'s recorded runs with a '
                                 'stored digest, so there is nothing to verify the replay against.')
            replay_scenario = dict(scenario, mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
            result = self._trace_replay(replay_scenario, seeds)
            if result.get('digest') != digest:
                raise ValueError('Replay metrics differ from the stored run; refusing an inaccurate '
                                 'replay.')
            payload = result['replay']
            encoded = canonical(payload).encode()
            if len(encoded) > 32*1024*1024:
                raise ValueError('Selected trace exceeds the 32 MiB replay limit.')
            # Only one cached trace, written beside the library; the library itself stays read-only.
            target = self._library.with_suffix('.replay.json')
            temp = target.with_suffix('.tmp')
            temp.write_bytes(encoded)
            os.replace(temp, target)
            visual = replay_visual_setup(replay_scenario, payload)
            return dict(ok=True, replay=payload, scenario=replay_scenario,
                        formation=_canonical_formation(replay_scenario),
                        visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'],
                        warnings=visual['warnings'], candidateId=candidate_id, label=label,
                        mathSeed=int(seeds[0]), libSeed=int(seeds[1]),
                        seeds=[int(seeds[0]), int(seeds[1])])
        except TimeoutError:
            return dict(ok=False, error=f'Selected replay exceeded {RUN_TIMEOUT_SECONDS} seconds; '
                                        f'saved results remain intact.')
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def simulate_strategy_visual(self, candidate_id=None, holder_key=None):
        """Run one brand-new fight of a target's exact frozen build and return it with visuals.

        Distinct from `replay_strategy_run`, which reproduces a run the target already recorded on a
        seed pair it kept. This instead draws one fresh seed pair the target has never run - absent
        from its stored runs, its investigation trials and its record holder, by the same seed rules
        `run_strategy` uses - runs the canonical simulator once with `trace=True` so the scored
        metrics and the visual trace are the same battle, records the result as one new investigation
        trial in the sidecar, and returns the updated strategy-detail rows together with the
        replay-compatible trace/setup payload, its fresh seeds and its digest. The optimiser library
        is only ever read; the one cached trace is written beside it, never into it.
        """
        if not self._operation.acquire(blocking=False):
            return dict(ok=False, error='A replay or trial batch is already running.')
        try:
            self._paused()
            status = self.status()
            if not status.get('compatible'):
                raise ValueError('The simulator changed; a fresh visual simulation cannot be '
                                 'reproduced by this version.')
            scenario, candidate_id, label, holder, resident = self._resolve_target(candidate_id,
                                                                                   holder_key)
            identity_key = _scenario_identity(scenario)
            stored_rows, _counters = self._stored_for(candidate_id, resident)
            used = _seed_pairs_from_rows(stored_rows)
            if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                used.add((int(scenario['mathSeed']), int(scenario['libSeed'])))
            # A resident candidate's matching record holder may carry a frozen pair its retained rows
            # no longer hold (retention roll-off), so exclude it explicitly as well.
            if holder is not None:
                frozen = (holder.get('mathSeed'), holder.get('libSeed'))
                if all(isinstance(value, int) and not isinstance(value, bool) for value in frozen):
                    used.add((int(frozen[0]), int(frozen[1])))
            used |= _seed_pairs_from_trials(self._ledger_for(identity_key))
            candidate_id, new_pairs = self._reserve_manual_trials(scenario, candidate_id, label, resident, 1)
            resident = True
            seeds = new_pairs[0]
            replay_scenario = dict(scenario, mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
            result = self._trace_replay(replay_scenario, seeds)
            digest = result.get('digest')
            if not digest:
                raise ValueError('The fresh simulation returned no digest, so its result cannot be '
                                 'trusted.')
            payload = result['replay']
            encoded = canonical(payload).encode()
            if len(encoded) > 32*1024*1024:
                raise ValueError('Fresh simulation trace exceeds the 32 MiB replay limit.')
            # The sidecar keeps the run's compact metrics, exactly like a batch trial; the trace
            # travels only in this call's answer, never into the trial history.
            compact = {key: value for key, value in result.items() if key != 'replay'}
            self._record_trials(identity_key, scenario, candidate_id, [(seeds, compact)])
            recorded = self._recorded_run_digest(scenario, candidate_id, resident, holder, seeds)
            if recorded != digest:
                raise ValueError('The fresh simulation did not persist to its own digest; refusing '
                                 'a mismatched result.')
            target = self._library.with_suffix('.replay.json')
            temp = target.with_suffix('.tmp')
            temp.write_bytes(encoded)
            os.replace(temp, target)
            visual = replay_visual_setup(replay_scenario, payload)
            detail = self._detail_payload(scenario, candidate_id, label, holder, resident)
            detail.update(replay=payload, scenario=replay_scenario,
                          formation=_canonical_formation(replay_scenario),
                          visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'],
                          warnings=visual['warnings'], digest=digest,
                          mathSeed=int(seeds[0]), libSeed=int(seeds[1]),
                          seeds=[int(seeds[0]), int(seeds[1])])
            return detail
        except TimeoutError:
            return dict(ok=False, error=f'Fresh simulation exceeded {RUN_TIMEOUT_SECONDS} seconds; '
                                        f'saved results remain intact.')
        except Exception as exc:
            return dict(ok=False, error=str(exc))
        finally:
            self._operation.release()

    def close(self):
        # The native closing callback starts shutdown and returns; WebView stays responsive
        # while the bounded outstanding batch drains, then a monitor destroys the window.
        self._close_encounter_ledger()
        self._optimizer.command('close')


#: Bounds for `--library-capacity-mib`. The flag is an explicit opt-in capacity ceiling for the
#: on-disk library: it raises the SQLite page ceiling the optimiser uses when it opens the
#: library. It is never a pre-allocation, it is never persisted and it never touches a shortcut.
LIBRARY_CAPACITY_MIN_MIB = 4096
LIBRARY_CAPACITY_MAX_MIB = 1048576


def _library_capacity_mib(text):
    """argparse type for the optional explicit library capacity ceiling.

    A whole number of MiB is required. The lower bound is the optimiser's current compiled-in
    default (4096 MiB) or higher, so an explicit flag can only raise the ceiling and never
    silently lower it. Invalid values are rejected here, before any optimiser or window exists.
    """
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f'library capacity must be a whole number of MiB, got {text!r}.') from None
    lower = max(LIBRARY_CAPACITY_MIN_MIB, int(strategy_optimizer.MAX_DB_MB))
    if not lower <= value <= LIBRARY_CAPACITY_MAX_MIB:
        raise argparse.ArgumentTypeError(
            f'library capacity must be between {lower} and {LIBRARY_CAPACITY_MAX_MIB} MiB, got {value}.')
    return value


def apply_library_capacity(capacity_mib):
    """Apply an explicit capacity ceiling to the process-local optimiser module.

    ``None`` - the flag's default - is a deliberate no-op that leaves the existing 4096 MiB
    default untouched. Nothing is written to settings, no shortcut is changed and no library is
    resized; this only sets the ceiling the optimiser uses the next time it opens a library. It
    must run before ``Bridge`` (and therefore ``Optimizer``) is constructed.
    """
    if capacity_mib is None:
        return None
    if isinstance(capacity_mib, bool) or not isinstance(capacity_mib, int):
        raise TypeError('library capacity must be an integer number of MiB, or None.')
    lower = max(LIBRARY_CAPACITY_MIN_MIB, int(strategy_optimizer.MAX_DB_MB))
    if not lower <= capacity_mib <= LIBRARY_CAPACITY_MAX_MIB:
        raise ValueError(
            f'library capacity must be between {lower} and {LIBRARY_CAPACITY_MAX_MIB} MiB.')
    strategy_optimizer.MAX_DB_MB = capacity_mib
    return capacity_mib


def build_parser():
    parser = argparse.ArgumentParser(prog='strategy_optimizer_desktop')
    parser.add_argument('--library', type=Path)
    parser.add_argument('--auto-start', action='store_true',
                        help='Start the selected library after it finishes opening.')
    parser.add_argument('--workers', type=int, default=24,
                        help='Worker count for --auto-start (bounded by the optimiser).')
    parser.add_argument('--planning-workers', type=int, choices=range(1, 13), default=None,
                        help='Planner share of the total worker budget (1-12). '
                             'Omitted, use the existing planner default.')
    parser.add_argument('--duty', type=float, default=1.0,
                        help='Worker duty for --auto-start (bounded by the optimiser).')
    parser.add_argument('--focus-encounter', type=int,
                        help='Focus this encounter before --auto-start, for a resumed search.')
    parser.add_argument('--library-capacity-mib', dest='library_capacity_mib',
                        type=_library_capacity_mib, default=None, metavar='MIB',
                        help='Optional explicit capacity ceiling for the library in MiB (between '
                             '%d and %d). It is a ceiling, not an allocation: it raises the SQLite '
                             'page limit the optimiser applies when it opens the library and does '
                             'not create or pre-allocate a larger file, persist a setting or change '
                             'a shortcut. Omitted, the built-in default (%d MiB) is left untouched. '
                             'Supplying it does not start a run; use --auto-start for that.'
                             % (LIBRARY_CAPACITY_MIN_MIB, LIBRARY_CAPACITY_MAX_MIB,
                                strategy_optimizer.MAX_DB_MB))
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # Explicit capacity is applied process-locally and only before Bridge/Optimizer exist;
    # None (the default) leaves strategy_optimizer.MAX_DB_MB exactly as compiled.
    apply_library_capacity(args.library_capacity_mib)
    if args.planning_workers is not None:
        import strategy_parallel_proposals
        strategy_parallel_proposals.DEFAULT_PLANNING_WORKERS = args.planning_workers
    remembered = None
    if args.library is None and SETTINGS.is_file():
        try:
            remembered = Path(json.loads(SETTINGS.read_text(encoding='utf-8'))['library'])
        except (ValueError, KeyError, TypeError):
            pass
    if not (APP/'desktop-dist/desktop.html').is_file():
        raise RuntimeError('Desktop UI has not been built. Run the provided Install Strategy Optimiser script once.')
    import webview
    bridge = Bridge(args.library or remembered or DEFAULT_LIBRARY, remember=args.library is None)
    if args.auto_start:
        def start_after_open():
            while bridge._optimizer.thread.is_alive():
                if bridge._optimizer.status()['state'] != 'Opening library':
                    if args.focus_encounter is not None:
                        bridge._optimizer.command('focus_encounter',
                                                  {'encounterId': args.focus_encounter})
                    bridge._optimizer.command('start',
                                              {'workers': args.workers, 'duty': args.duty})
                    return
                time.sleep(.1)
        threading.Thread(target=start_after_open, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', 0), asset_handler(bridge=bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disk-cache-size=33554432 --disable-features=msEdgeSidebarV2'
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    window = webview.create_window('Kingdom Adventurers · Strategy Optimiser',
        f'http://127.0.0.1:{server.server_port}/desktop.html', js_api=bridge,
        width=1320, height=900, min_size=(980, 700))
    bridge._window = window
    closing = threading.Event()
    allow_destroy = threading.Event()

    def on_closing():
        if allow_destroy.is_set():
            return True
        if bridge._optimizer.thread.is_alive() or bridge._operation.locked():
            if not closing.is_set():
                closing.set()
                window.set_title('Saving simulations — Kingdom Adventurers Strategy Optimiser')
                bridge.close()

                def finish():
                    bridge._optimizer.thread.join()
                    with bridge._operation:
                        allow_destroy.set()
                    window.destroy()
                threading.Thread(target=finish, daemon=True).start()
            return False
        return True

    def on_loaded():
        # Keep the embedded renderer in this app. Its legacy navigation links are not
        # needed for read-only playback; no hosted-site/external-browser handoff is permitted.
        window.evaluate_js("document.addEventListener('click', e => { if (e.target.closest('a')) e.preventDefault(); }, true)")

    window.events.closing += on_closing
    window.events.loaded += on_loaded
    try:
        webview.start(gui='edgechromium', private_mode=True, storage_path=str(STORAGE['webview']))
    finally:
        if bridge._optimizer.thread.is_alive():
            bridge.close()
            bridge._optimizer.thread.join()
        bridge._guard.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as error:
        import traceback
        log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KingdomAdventurersOptimizer'
        log.mkdir(parents=True, exist_ok=True)
        (log/'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Strategy Optimiser could not start', 0x10)
        else:
            raise
