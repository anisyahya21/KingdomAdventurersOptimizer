"""Automatic ALL-ENCOUNTER Community campaign coordinator.

The normal one-button **Start** must not quietly reduce the run to a single focused
encounter (the old `Coordinator._reference_candidate(None)` behaviour). Instead it starts
(or resumes) ONE finite, persisted campaign that walks every encounter id the recovered
catalogue actually offers, in catalogue order, one Community study at a time.

This module is deliberately pure with respect to the optimiser:

  * it never imports a UI or `strategy_optimizer`;
  * it never touches the `ea_*` ledger, the run tables or a native battle directly;
  * preview/activate are supplied as callbacks by `Optimizer` (which already owns the
    library connection and the atomic activation transaction).

What a campaign is
------------------
A campaign declares, per encounter, a finite purpose budget DERIVED from the existing
`strategy_encounter_search.DEFAULT_PURPOSES` (improvement 128, boundary 64, support 32,
comparison 128, exploration 32 -> 384 runs). The finite campaign cap is therefore
``len(encounter_ids) * 384`` (7680 for the twenty recovered encounters) and is never
raised on pause, resume, restart or a repeated Start. Each encounter is activated through
the existing Atomic preview/activate path, so a resumed campaign coalesces onto the SAME
budget session and never double-grants.

Lifecycle
---------
  * ``start``   - start a campaign, or resume its persisted active round.
  * ``resume``  - resume only; refuses when there is nothing to resume.
  * ``after_pass`` - observe one encounter report and advance the campaign when the
                  current study is drained/idle. A blocked/readiness error pauses the
                  campaign with a reason; it never skips an encounter. When a finite round
                  completes, the next finite round starts automatically.

Measured evidence stays where it belongs: the encounter coordinator already separates
sessions by encounter/scope/revision in the ledger, and this module only records which
encounter is current and how much finite budget it declared. Unused boundary/support
budget is recorded, never filled with filler.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time

import strategy_search_mode as modes

VERSION = 1
#: Library meta key holding the campaign cursor. One coordinator, one campaign.
CAMPAIGN_KEY = 'communityCampaign'
#: The only mode this campaign drives. Community-first keeps every other stream at zero.
CAMPAIGN_MODE = 'community-first'
#: Human-facing reason surfaced with every status block.
NEW_CAMPAIGN_NOTE = ('Community runs in finite encounter rounds until paused or stopped; '
                     'repeated Start resumes the current round without regranting it')


class CampaignRefused(Exception):
    """A start/resume request that must not be honoured (nothing persisted)."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def default_purposes():
    """The per-encounter purpose budgets, read from the canonical encounter-search defaults.

    Never re-declared here: `strategy_encounter_search.DEFAULT_PURPOSES` is the one source of
    truth, so a reviewed change there flows into the campaign's declared cap.
    """
    import strategy_encounter_search as search
    declared = getattr(search, 'DEFAULT_PURPOSES', None)
    if not isinstance(declared, dict) or not declared:
        raise CampaignRefused('the encounter-search default purposes are unavailable')
    return {name: int(declared[name]) for name in modes.PURPOSES if name in declared}


def per_encounter_cap(purposes=None):
    """The finite run budget one encounter study declares (384 with the reviewed defaults)."""
    return sum((purposes or default_purposes()).values())


def _commit_store(store, commit=None):
    if commit is not None:
        commit()
        return
    db = getattr(store, 'db', None)
    if db is not None and hasattr(db, 'commit'):
        db.commit()
        return
    method = getattr(store, 'commit', None)
    if callable(method):
        method()


class CommunityCampaign:
    """The finite, persisted ALL-ENCOUNTER campaign. State lives in the library meta store."""

    def __init__(self, *, encounter_ids, preview, activate, purposes=None,
                 store_key=CAMPAIGN_KEY, commit=None, focus=None):
        self.encounter_ids = [int(value) for value in encounter_ids]
        self.preview = preview
        self.activate = activate
        self.purposes = dict(purposes or default_purposes())
        self.cap_per_encounter = per_encounter_cap(self.purposes)
        self.store_key = store_key
        self._commit = commit
        self.focus = [int(value) for value in (focus or [])]

    # -- persistence ------------------------------------------------------------------------------
    def load(self, store):
        state = store.get(self.store_key)
        return state if isinstance(state, dict) else None

    def _save(self, store, state):
        state['updatedAt'] = time.time()
        store.set(self.store_key, state)
        _commit_store(store, self._commit)
        return state

    def _new_state(self):
        campaign_id = 'cc-' + _digest({'ids': self.encounter_ids, 'nonce': time.time()})[:16]
        state = {
            'version': VERSION,
            'campaignId': campaign_id,
            'scopeKey': 'community-campaign:%s:round:0' % campaign_id,
            'round': 0,
            'encounterIds': list(self.encounter_ids),
            'purposes': dict(self.purposes),
            'perEncounterCap': self.cap_per_encounter,
            'cap': self.cap_per_encounter * len(self.encounter_ids),
            'index': 0,
            'granted': 0,
            'declared': 0,
            'status': 'active',
            'reason': None,
            'current': None,
            'intent': None,
            'completedEncounters': [],
            'activatedEncounters': [],
            'focus': list(self.focus),
            'configDigests': {},
            'remainders': [],
            'createdAt': time.time(),
            'updatedAt': time.time(),
        }
        return state

    def _catalogue_drift(self, state):
        """A paused campaign when the recovered catalogue no longer contains a planned encounter."""
        planned = [int(value) for value in (state.get('encounterIds') or [])]
        if not self.encounter_ids or not planned:
            return None
        current = set(self.encounter_ids)
        missing = [value for value in planned if value not in current]
        if missing:
            return ('the recovered encounter catalogue changed; campaign paused rather than dropping '
                    'encounters %s' % (missing,))
        return None

    # -- scheduling (focus-aware) -----------------------------------------------------------------
    def _apply_focus(self, state, focus):
        """Validate and store the multi-select focus for scheduling only (never a budget)."""
        known = set(int(value) for value in (state.get('encounterIds') or []))
        selected = []
        for value in (focus or []):
            if isinstance(value, bool):
                raise CampaignRefused('a focus needs whole encounter ids')
            encounter_id = int(value)
            if known and encounter_id not in known:
                raise CampaignRefused(
                    'encounter %d is not in the recovered catalogue' % encounter_id)
            if encounter_id not in selected:
                selected.append(encounter_id)
        state['focus'] = selected
        return selected

    def _scheduled_set(self, state):
        """The ids the campaign may schedule: the focus subset, or the whole catalogue if empty."""
        known = set(int(value) for value in (state.get('encounterIds') or []))
        focus = [int(value) for value in (state.get('focus') or []) if int(value) in known]
        return set(focus) if focus else known

    def _target_index(self, state):
        """Index of the next scheduled, not-yet-completed encounter, or None when none remain."""
        ids = [int(value) for value in (state.get('encounterIds') or [])]
        completed = set(int(value) for value in (state.get('completedEncounters') or []))
        scheduled = self._scheduled_set(state)
        for index, encounter_id in enumerate(ids):
            if encounter_id in scheduled and encounter_id not in completed:
                return index
        return None

    def _finish_unavailable(self, store, state, report):
        """Complete when every catalogue encounter is done; otherwise pause with a clear reason."""
        ids = [int(value) for value in (state.get('encounterIds') or [])]
        if not ids:
            # Defensive: a persisted campaign with no encounters can schedule nothing. An empty
            # catalogue must never satisfy the all-completed check below (an empty set is a
            # superset of itself), which would bump the round and recursively re-enter
            # `_ensure_activation` forever. Pause with an actionable reason instead.
            return self._pause(
                store, state,
                'the persisted campaign lists no encounters; nothing to run', report)
        completed = set(int(value) for value in (state.get('completedEncounters') or []))
        if completed >= set(ids):
            # Each round is finite. A fresh persisted scope gives its encounters new bounded sessions.
            state['round'] = int(state.get('round') or 0) + 1
            state['scopeKey'] = 'community-campaign:%s:round:%d' % (
                state.get('campaignId'), state['round'])
            state['index'] = 0
            state['completedEncounters'] = []
            state['activatedEncounters'] = []
            state['configDigests'] = {}
            state['granted'] = 0
            state['declared'] = 0
            state['cap'] = self.cap_per_encounter * len(ids)
            state['status'] = 'active'
            state['reason'] = None
            state['current'] = None
            state['intent'] = None
            self._save(store, state)
            return self._ensure_activation(store, state)
        remaining = len(ids) - len(completed)
        reason = ('the selected encounters are complete; %d encounter(s) in the campaign remain '
                  '(clear the focus to run them)' % remaining)
        return self._pause(store, state, reason, report)

    # -- value construction -----------------------------------------------------------------------
    def _value(self, state, encounter_id):
        # The campaign's OWN declared budgets are authoritative: a resumed campaign can never pick
        # up a larger default and silently grant more than it declared.
        purposes = dict(state.get('purposes') or self.purposes)
        value = {
            'mode': CAMPAIGN_MODE,
            'purposes': purposes,
            'encounter': int(encounter_id),
            'reference': None,
            'constraints': None,
            'thresholds': None,
            'campaignScope': state.get('scopeKey'),
        }
        return value

    # -- activation -------------------------------------------------------------------------------
    def _activate(self, store, state, preview):
        """Activate the previewed encounter atomically, persisting the intent first.

        The intent (index, encounter id, declared budget and the previewed config digest) is
        written and committed BEFORE the activation call, so a crash between the two retries the
        exact same config/session and the ledger coalesces it instead of granting a second budget.
        """
        index = int(state['index'])
        encounter_id = int(state['encounterIds'][index])
        declared_cap = int(state.get('perEncounterCap') or self.cap_per_encounter)
        state['intent'] = {'index': index, 'encounterId': encounter_id,
                           'declaredCap': declared_cap,
                           'configDigest': _canonical(preview.get('config'))}
        state['status'] = 'active'
        state['reason'] = None
        # Charge one encounter budget per UNIQUE activated encounter. Re-activating the same
        # encounter (a resumed/focus-toggled switch) coalesces onto the same session and must not
        # bump the count, so the index-max of the old cursor would over-grant.
        activated = sorted(set(int(value) for value in (state.get('activatedEncounters') or []))
                           | {encounter_id})
        state['activatedEncounters'] = activated
        state['declared'] = len(activated)
        state['granted'] = min(int(state['cap']), declared_cap * len(activated))
        self._save(store, state)

        migration = preview.get('simulatorMigration') or {}
        if migration.get('required') and not migration.get('eligible'):
            return self._pause(store, state,
                               migration.get('reason') or 'the observer migration is not eligible')
        value = self._value(state, encounter_id)
        value['config'] = preview.get('config')
        if migration.get('required'):
            # Legacy migration goes through the EXISTING reviewed plan only; never bypassed here.
            value['migrationPlanId'] = migration.get('planId')
        result = self.activate(value)
        if isinstance(result, dict) and result.get('ok') is False:
            return self._pause(store, state, result.get('error') or 'activation refused')
        session_id = (result or {}).get('sessionId') if isinstance(result, dict) else None
        state['current'] = {'index': index, 'encounterId': encounter_id, 'sessionId': session_id}
        digests = dict(state.get('configDigests') or {})
        digests[str(index)] = _canonical(preview.get('config'))
        state['configDigests'] = digests
        self._save(store, state)
        return self._status(state, None, activated=encounter_id, switched=True)

    def _ensure_activation(self, store, state):
        index = self._target_index(state)
        if index is None:
            return self._finish_unavailable(store, state, None)
        state['index'] = index
        encounter_id = int(state['encounterIds'][index])
        preview = self.preview(self._value(state, encounter_id))
        if not isinstance(preview, dict):
            return self._pause(store, state, 'the encounter preview returned no plan')
        return self._activate(store, state, preview)

    # -- public commands --------------------------------------------------------------------------
    def start(self, store, *, active_encounter=None, runtime_enabled=None, report=None,
              focus=None, allow_new_campaign=False):
        """Start a campaign or resume its persisted round; drained rounds roll forward."""
        state = self.load(store)
        if state is None:
            if not self.encounter_ids:
                return self._inactive_status('the recovered catalogue lists no encounters')
            state = self._new_state_saved(store)
            self._apply_focus(state, focus if focus is not None else self.focus)
            self._save(store, state)
            return self._ensure_activation(store, state)
        drift = self._catalogue_drift(state)
        if drift:
            return self._pause(store, state, drift, report)
        if focus is not None:
            self._apply_focus(state, focus)
            self._save(store, state)
        status = state.get('status')
        if status == 'complete':
            # Continue older completed campaign records with a fresh bounded round.
            state['status'] = 'active'
            state['reason'] = None
            state['round'] = int(state.get('round') or 0) + 1
            state['scopeKey'] = 'community-campaign:%s:round:%d' % (
                state.get('campaignId'), state['round'])
            state['completedEncounters'] = []
            state['activatedEncounters'] = []
            state['configDigests'] = {}
            state['granted'] = 0
            state['declared'] = 0
            state['cap'] = self.cap_per_encounter * len(state.get('encounterIds') or [])
            state['current'] = None
            state['intent'] = None
            self._save(store, state)
            return self._ensure_activation(store, state)
        if status == 'paused':
            state['status'] = 'active'
            state['reason'] = None
        return self._resume(store, state, active_encounter=active_encounter,
                          runtime_enabled=runtime_enabled, report=report)

    def resume(self, store, *, active_encounter=None, runtime_enabled=None, report=None, focus=None):
        """Resume an existing unfinished campaign; refuse when there is nothing to resume."""
        state = self.load(store)
        if state is None:
            return self._inactive_status('there is no campaign to resume')
        drift = self._catalogue_drift(state)
        if drift:
            return self._pause(store, state, drift, report)
        if focus is not None:
            self._apply_focus(state, focus)
            self._save(store, state)
        if state.get('status') == 'complete':
            return self.start(store, active_encounter=active_encounter,
                              runtime_enabled=runtime_enabled, report=report, focus=focus)
        if state.get('status') == 'paused':
            state['status'] = 'active'
            state['reason'] = None
        return self._resume(store, state, active_encounter=active_encounter,
                          runtime_enabled=runtime_enabled, report=report)

    def _resume(self, store, state, *, active_encounter=None, runtime_enabled=None, report=None):
        # A paused prospective cohort retains authorised, undispatched jobs. Resume that exact
        # study first; a changed focus takes effect at after_pass's existing drained boundary.
        current = state.get('current') or {}
        if ((report or {}).get('progress', {}).get('current') is not None
                and runtime_enabled and current.get('encounterId') == active_encounter):
            self._save(store, state)
            return self._status(state, report, resumed=True)
        index = self._target_index(state)
        if index is None:
            return self._finish_unavailable(store, state, report)
        state['index'] = index
        encounter_id = int(state['encounterIds'][index])
        current = state.get('current') or {}
        wrong_study = ((runtime_enabled is False)
                       or (active_encounter is not None and int(active_encounter) != encounter_id))
        if wrong_study or state.get('intent') is None or current.get('encounterId') != encounter_id:
            return self._ensure_activation(store, state)
        self._save(store, state)
        return self._status(state, report, resumed=True)

    def _new_state_saved(self, store):
        state = self._new_state()
        self._save(store, state)
        return state

    def set_focus(self, store, focus):
        """Update the multi-select scheduling filter. No budget, no activation, legal while running."""
        state = self.load(store)
        if state is None:
            return self._inactive_status('there is no campaign to focus')
        self._apply_focus(state, focus)
        self._save(store, state)
        return self._status(state, None)

    def status_view(self, store, report=None):
        """Read-only campaign status built from the persisted state (never activates/resumes)."""
        state = self.load(store)
        if state is None:
            return self._inactive_status('there is no campaign')
        return self._status(state, report)

    def begin_new_campaign(self, store, *, confirm=False, report=None, focus=None):
        """Refuse manual campaign replacement; finite round rollover is automatic."""
        if not confirm:
            return self._inactive_status('a new campaign is not automatic; confirm an explicit start',
                                         status='refused')
        state = self.load(store)
        if state is None:
            return self._ensure_activation(store, self._new_state_saved(store))
        return self._status(state, report, note='campaign rounds start automatically when drained',
                            status='refused')

    def after_pass(self, store, report=None, *, busy=False, running=True, blocked_reason=None):
        """Observe one pass: advance when drained, pause on a block, complete at the last fight."""
        state = self.load(store)
        if state is None:
            return self._inactive_status('there is no campaign to advance')
        drift = self._catalogue_drift(state)
        if drift:
            return self._pause(store, state, drift, report)
        status = state.get('status')
        if status in ('complete', 'paused'):
            return self._status(state, report)
        if not running:
            return self._status(state, report)
        reason = self._blocked_reason(report, blocked_reason)
        if reason:
            return self._pause(store, state, reason, report)
        if busy:
            return self._status(state, report)
        progress = (report or {}).get('progress') or {}
        if progress.get('current') is not None:
            return self._status(state, report)
        confirmation = progress.get('confirmation')
        frozen_unresolved = (isinstance(confirmation, dict)
                             and bool(confirmation.get('frozen'))
                             and not confirmation.get('ready'))
        if frozen_unresolved:
            if progress.get('idle'):
                return self._pause(store, state,
                                   'a frozen confirmation is unresolved while the study is idle',
                                   report)
            return self._status(state, report)
        current_id = (state.get('current') or {}).get('encounterId')
        if (not progress.get('idle') and current_id is not None
                and int(current_id) not in self._scheduled_set(state)):
            # Finish the already-frozen experiment, then leave this partial study resumable.
            # A focus toggle never marks the unfinished encounter complete or grants it twice.
            return self._ensure_activation(store, state)
        if not progress.get('idle'):
            return self._status(state, report)
        return self._advance(store, state, report)

    # -- classification ---------------------------------------------------------------------------
    @staticmethod
    def _blocked_reason(report, explicit=None):
        """The CURRENT block, never an accumulated note.

        `explicit` is the integration's live, session-scoped readiness reason; the report's
        `progress.blocked` is the current claim's own block. The coordinator's `limitations` list
        is deliberately NOT scanned: it is never cleared between encounters, so a note from a past
        study would pause every later one.
        """
        if explicit:
            return str(explicit)
        if not isinstance(report, dict):
            return None
        progress = report.get('progress') or {}
        if progress.get('blocked'):
            current = progress.get('current') or {}
            return str(current.get('blockedReason') or 'a dispatch claim is blocked')
        return None

    def _advance(self, store, state, report):
        index = int(state['index'])
        self._record_remainder(state, report, index)
        encounter_id = int(state['encounterIds'][index])
        completed = sorted(set(int(value) for value in (state.get('completedEncounters') or []))
                           | {encounter_id})
        state['completedEncounters'] = completed
        state['current'] = None
        # The next scheduled encounter is the first focused id that is not yet complete. When the
        # whole catalogue is the focus this is simply the next one; when a selected subset is done
        # but other encounters remain, `_finish_unavailable` pauses with a reason instead of
        # declaring the campaign complete.
        return self._ensure_activation(store, state)

    @staticmethod
    def _record_remainder(state, report, index):
        session = ((report or {}).get('budgets') or {}).get('session') or {}
        unspent = {}
        for purpose, row in session.items():
            row = row or {}
            total = int(row.get('total') or 0)
            charged = max(int(row.get('completed') or 0), int(row.get('reserved') or 0))
            left = max(0, total - charged)
            if left:
                unspent[str(purpose)] = left
        if unspent:
            state.setdefault('remainders', []).append({
                'index': index,
                'encounterId': int(state['encounterIds'][index]),
                'unspent': unspent,
                'note': 'unused purpose budget is recorded, never filled with filler work',
            })

    # -- status -----------------------------------------------------------------------------------
    def _pause(self, store, state, reason, report=None):
        state['status'] = 'paused'
        state['reason'] = str(reason)
        self._save(store, state)
        return self._status(state, report, final=True)

    def _inactive_status(self, reason, status='none'):
        return {
            'ok': status == 'none', 'version': VERSION, 'status': status, 'reason': reason,
            'campaignId': None, 'scopeKey': None, 'round': 0, 'current': None, 'currentIndex': None,
            'currentEncounterId': None, 'total': 0, 'completed': 0, 'cap': 0, 'granted': 0,
            'remaining': 0, 'perEncounterCap': self.cap_per_encounter, 'purposes': dict(self.purposes),
            'encounterIds': list(self.encounter_ids), 'completedEncounters': [],
            'activatedEncounters': [], 'focus': list(self.focus), 'idle': False,
            'activated': None, 'switched': False, 'resumed': False, 'final': True,
            'stopRuntime': True, 'newCampaignAutomatic': False, 'note': NEW_CAMPAIGN_NOTE,
        }

    def _status(self, state, report=None, *, activated=None, switched=False, resumed=False,
                final=False, note=None, status=None):
        progress = (report or {}).get('progress') or {}
        total = len(state.get('encounterIds') or [])
        index = int(state.get('index') or 0)
        current = state.get('current')
        if current is None and total:
            current = {'index': index, 'encounterId': int(state['encounterIds'][index]),
                       'sessionId': None}
        return {
            'ok': True,
            'version': VERSION,
            'campaignId': state.get('campaignId'),
            'scopeKey': state.get('scopeKey'),
            'round': int(state.get('round') or 0),
            'status': status or state.get('status'),
            'reason': state.get('reason'),
            'note': note,
            'current': copy.deepcopy(current),
            'currentIndex': index if total else None,
            'currentEncounterId': (current or {}).get('encounterId'),
            'total': total,
            'completed': len(state.get('completedEncounters') or []),
            'cap': int(state.get('cap') or 0),
            'granted': int(state.get('granted') or 0),
            'remaining': max(0, int(state.get('cap') or 0) - int(state.get('granted') or 0)),
            'perEncounterCap': int(state.get('perEncounterCap') or self.cap_per_encounter),
            'purposes': dict(state.get('purposes') or {}),
            'encounterIds': list(state.get('encounterIds') or []),
            'completedEncounters': list(state.get('completedEncounters') or []),
            'activatedEncounters': list(state.get('activatedEncounters') or []),
            'focus': list(state.get('focus') or []),
            'idle': bool(progress.get('idle')),
            'activated': activated,
            'switched': bool(switched),
            'resumed': bool(resumed),
            'final': bool(final or state.get('status') in ('complete', 'paused')),
            'stopRuntime': state.get('status') in ('complete', 'paused'),
            'newCampaignAutomatic': False,
        }


def bootstrap_status(path, report=None, *, encounter_ids=(), purposes=None, store_key=CAMPAIGN_KEY):
    """Read-only campaign status for a library the loop has not opened yet.

    Mirrors strategy_encounter_search.bootstrap_report: opens the library read-only, reads the one
    campaign meta key and never writes. A missing key/table means there is no campaign.
    """
    import sqlite3
    from pathlib import Path
    state = None
    candidate = Path(path)
    if candidate.is_file():
        try:
            connection = sqlite3.connect(candidate.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
            try:
                row = connection.execute('SELECT value FROM meta WHERE key=?', (store_key,)).fetchone()
            finally:
                connection.close()
            if row and row[0]:
                loaded = json.loads(row[0])
                if isinstance(loaded, dict):
                    state = loaded
        except (sqlite3.Error, ValueError):
            state = None
    campaign = _campaign(encounter_ids, None, None, purposes, store_key, None)
    if state is None:
        return campaign._inactive_status('there is no campaign')
    return campaign._status(state, report)

# -- thin module-level helpers so the optimiser integration reads clearly --------------------------
def _campaign(encounter_ids, preview, activate, purposes, store_key, commit):
    return CommunityCampaign(encounter_ids=encounter_ids, preview=preview, activate=activate,
                             purposes=purposes, store_key=store_key, commit=commit)


def start_campaign(store, *, encounter_ids, preview, activate, purposes=None,
                   store_key=CAMPAIGN_KEY, commit=None, active_encounter=None, runtime_enabled=None,
                   report=None, focus=None, allow_new_campaign=False):
    return _campaign(encounter_ids, preview, activate, purposes, store_key, commit).start(
        store, active_encounter=active_encounter, runtime_enabled=runtime_enabled, report=report,
        focus=focus, allow_new_campaign=allow_new_campaign)


def resume_campaign(store, *, encounter_ids, preview, activate, purposes=None,
                    store_key=CAMPAIGN_KEY, commit=None, active_encounter=None, runtime_enabled=None,
                    report=None, focus=None):
    return _campaign(encounter_ids, preview, activate, purposes, store_key, commit).resume(
        store, active_encounter=active_encounter, runtime_enabled=runtime_enabled, report=report,
        focus=focus)


def after_pass(store, report=None, *, encounter_ids, preview, activate, purposes=None,
               store_key=CAMPAIGN_KEY, commit=None, busy=False, running=True, blocked_reason=None):
    return _campaign(encounter_ids, preview, activate, purposes, store_key, commit).after_pass(
        store, report, busy=busy, running=running, blocked_reason=blocked_reason)


def campaign_status(store, report=None, *, encounter_ids=(), purposes=None,
                    store_key=CAMPAIGN_KEY):
    campaign = _campaign(encounter_ids, None, None, purposes, store_key, None)
    state = campaign.load(store)
    if state is None:
        return campaign._inactive_status('there is no campaign')
    return campaign._status(state, report)
