"""Native-only strategy workflow methods mixed into NativeOptimizer by the coordinator.

This module adapts exact user intents and native result rows to the original desktop API. It never
generates strategy mutations or simulates battles in Python. Evaluation is delegated to
NativeOptimizer.evaluate; SQLite mutations go through its single writer queue.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from strategy_native_ui_projection import project_detail, project_strategy_row


_WORKFLOW_KEY = 'nativeWorkflowV1'
_CHUNK = 32


class NativeWorkflowMixin:
    """Private-prefixed bridge adapter methods. Coordinator wires the public bridge dispatch."""

    def _workflow_lock(self):
        lock = getattr(self, '_native_workflow_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._native_workflow_lock = lock
        return lock

    def _workflow_state(self):
        state = self._libcall('get', _WORKFLOW_KEY, {}) or {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault('candidates', {})
        state.setdefault('experiments', [])
        state.setdefault('activeExperimentId', None)
        return state, state

    def _workflow_save(self, state):
        state['_projectionRevision'] = int(state.get('_projectionRevision', 0)) + 1
        self._libcall('set', _WORKFLOW_KEY, state)

    def _workflow_projection_cache(self):
        with self._workflow_lock():
            cache = getattr(self, '_native_workflow_projection_cache', None)
            if cache is None:
                cache = OrderedDict()
                self._native_workflow_projection_cache = cache
            return cache

    def _workflow_projection_token(self, state=None):
        if state is None:
            state, _ = self._workflow_state()
        try:
            total_runs = int(self._libcall('get', 'totalRuns', 0) or 0)
        except Exception:
            total_runs = None
        event = self._workflow_chunk_event()
        return (int(getattr(self, '_native_library_import_epoch', 0)), total_runs,
                int(state.get('_projectionRevision', 0)), state.get('activeExperimentId'),
                bool(event.is_set()))

    def _workflow_cache_get(self, key):
        with self._workflow_lock():
            cache = self._workflow_projection_cache()
            value = cache.get(key)
            if value is None:
                return None
            cache.move_to_end(key)
            return json.loads(value)

    def _workflow_cache_put(self, key, value):
        with self._workflow_lock():
            cache = self._workflow_projection_cache()
            try:
                cache[key] = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            except (TypeError, ValueError):
                return
            cache.move_to_end(key)
            while len(cache) > 32:
                cache.popitem(last=False)

    def _workflow_verification_only(self):
        provenance = self._provenance()
        return provenance.get('verificationOnly', True) if isinstance(provenance, dict) else True

    def _workflow_chunk_event(self):
        event = getattr(self, '_native_workflow_chunk_done', None)
        if event is None:
            event = threading.Event()
            event.set()
            self._native_workflow_chunk_done = event
        return event

    @staticmethod
    def _workflow_intent(value):
        if isinstance(value, dict) and value.get('schema') == 'ka-combat-fight-export-1':
            value = value.get('scenario')
        if not isinstance(value, dict):
            raise ValueError('Build must be an exact scenario object.')
        encounter = value.get('encounterId')
        if isinstance(encounter, bool) or not isinstance(encounter, int) or encounter < 0:
            raise ValueError('Scenario requires its exact nonnegative integer encounterId.')
        # Preserve the full payload byte-for-byte at the JSON-value level. Preparation owns legality.
        return dict(value)

    def _workflow_import_build(self, scenario, label='Imported native build'):
        """Persist exact intent only; legality is checked by native preparation on execution."""
        try:
            intent = self._workflow_intent(scenario)
            with self._workflow_lock():
                candidate_id = self._libcall('import_candidate', intent, str(label)[:160])
                _, state = self._workflow_state()
                candidates = dict(state.get('candidates') or {})
                candidates[candidate_id] = dict(intent=intent, label=str(label)[:160],
                                                importedAt=time.time(), source='native-import')
                state['candidates'] = candidates
                self._workflow_save(state)
            return {'ok': True, 'candidateId': candidate_id}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_read_legacy(self):
        """Read original frozen holders/candidates from the co-located legacy tables, read-only."""
        uri = self.path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            meta = {}
            if 'meta' in tables:
                row = db.execute('SELECT value FROM meta WHERE key=?', ('recordHolders',)).fetchone()
                if row:
                    try:
                        meta = json.loads(row['value'])
                    except (TypeError, ValueError):
                        meta = {}
            return tables, (meta if isinstance(meta, dict) else {})

    def _workflow_target(self, candidate_id=None, holder_key=None):
        if (candidate_id is None) == (holder_key is None):
            raise ValueError('Provide exactly one of candidateId or holderKey.')
        _, state = self._workflow_state()
        if holder_key is not None:
            native_holder = self._libcall('record_holders').get(str(holder_key))
            if isinstance(native_holder, dict):
                return self._workflow_intent(native_holder['scenario']), \
                    str(native_holder.get('candidateId') or native_holder.get('candidate')), \
                    str(native_holder.get('label') or 'Native record holder'), dict(native_holder)
            tables, holders = self._workflow_read_legacy()
            holder = holders.get(str(holder_key))
            if not isinstance(holder, dict):
                raise ValueError('That frozen record holder is not in the selected library.')
            scenario = holder.get('scenario')
            legacy_id = holder.get('candidate')
            if not isinstance(scenario, dict) and legacy_id and 'candidate' in tables:
                with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                    row = db.execute('SELECT scenario FROM candidate WHERE id=?', (legacy_id,)).fetchone()
                    if row:
                        scenario = json.loads(row[0])
            if not isinstance(scenario, dict):
                raise ValueError('This holder has no frozen scenario and cannot be evaluated.')
            return self._workflow_intent(scenario), str(legacy_id) if legacy_id else None, \
                str(holder.get('label') or 'Restored frozen record'), dict(holder)

        candidate_id = str(candidate_id)
        local = (state.get('candidates') or {}).get(candidate_id)
        if isinstance(local, dict) and isinstance(local.get('intent'), dict):
            return self._workflow_intent(local['intent']), candidate_id, local.get('label') or candidate_id, None
        detail = self._libcall('_native_detail', candidate_id)
        if not isinstance(detail, dict) or not detail.get('ok'):
            raise ValueError((detail or {}).get('error') or 'Candidate is not available in this library.')
        return self._workflow_intent(detail.get('scenario')), candidate_id, \
            detail.get('label') or candidate_id, detail.get('holder')

    def _workflow_restore_strategy(self, holder_key):
        try:
            scenario, _, label, _holder = self._workflow_target(holder_key=holder_key)
            result = self._workflow_import_build(scenario, label)
            if not result.get('ok'):
                return result
            result['restoredFromHolder'] = str(holder_key)
            return result
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_candidate_rows(self, encounter_id, defeat_count=0):
        """Unmeasured imported intents in original encounter-row shape; no invented outcomes."""
        if (isinstance(encounter_id, bool) or not isinstance(encounter_id, int) or encounter_id < 0
                or isinstance(defeat_count, bool) or not isinstance(defeat_count, int)
                or defeat_count < 0):
            raise ValueError('encounterId and defeatCount must be nonnegative integers.')
        _, state = self._workflow_state()
        measured = self._libcall('strategies', encounter_id, defeat_count)
        seen = {str(row.get('candidateId')) for row in (measured or {}).get('strategies', [])
                if isinstance(row, dict)}
        rows = []
        verification_only = self._workflow_verification_only()
        for candidate_id, record in (state.get('candidates') or {}).items():
            if str(candidate_id) in seen or not isinstance(record, dict):
                continue
            scenario = record.get('intent')
            if not isinstance(scenario, dict) or scenario.get('encounterId') != encounter_id:
                continue
            raw_defeat = scenario.get('defeatCount', 0) or 0
            if isinstance(raw_defeat, bool) or not isinstance(raw_defeat, int) \
                    or raw_defeat != defeat_count:
                continue
            label = str(record.get('label') or 'Imported native build')
            rows.append(project_strategy_row(dict(candidateId=str(candidate_id), id=str(candidate_id), label=label,
                source='native-import', displayName=label, creator='native-import', change=None,
                parentId=None, parentProducer=None, nameDerived=False, nameDetail=None,
                attempts=0, wins=None, losses=None, noVerdict=0, meanEarned=None,
                earnedSamples=0, meanPotential=None, potentialSamples=0, bestEarned=None,
                bestPotential=None, winRate=None, comparable=False, retainedSampleCount=0,
                eaMeanEarned=None, eaBestEarned=None, eaEarnedSamples=0,
                verificationOnly=verification_only, native=True, originalCandidate=False,
                measured=False, intent=scenario, nativeFlags=[])))
        rows.sort(key=lambda row: (row['label'].casefold(), row['candidateId']))
        return rows

    def _workflow_summary(self, rows):
        attempts = len(rows)
        measured = [r for r in rows if r.get('resolved') is True and r.get('diagnostic') is not True]
        earned = [r['earned'] for r in measured if isinstance(r.get('earned'), (int, float))
                  and not isinstance(r.get('earned'), bool) and math.isfinite(r['earned'])]
        potential = [r['higherPotential'] for r in measured
                     if isinstance(r.get('higherPotential'), (int, float))
                     and not isinstance(r.get('higherPotential'), bool)
                     and math.isfinite(r['higherPotential'])]
        verdicts = [r.get('verdict') for r in measured]
        verdict_known = bool(measured) and all(v in (1, 2) for v in verdicts)
        wins = sum(v == 1 for v in verdicts) if verdict_known else None
        losses = sum(v == 2 for v in verdicts) if verdict_known else None
        return dict(attempts=attempts, samples=len(measured), wins=wins, losses=losses,
                    noVerdict=attempts-len(measured), meanEarned=sum(earned)/len(earned) if earned else None,
                    earnedSamples=len(earned), meanPotential=sum(potential)/len(potential) if potential else None,
                    potentialSamples=len(potential), bestEarned=max(earned) if earned else None,
                    bestPotential=max(potential) if potential else None,
                    winRate=(wins/(wins+losses) if verdict_known and wins+losses else None),
                    verdictSamples=len([v for v in verdicts if v in (1, 2)]),
                    comparable=bool(measured and len(measured) == attempts), retainedSampleCount=attempts,
                    verificationOnly=self._workflow_verification_only())

    def _workflow_project_strategy_result(self, result):
        """Adapt the native strategy-list response at the API boundary; evidence stays native."""
        if not isinstance(result, dict):
            return result
        shaped = dict(result)
        shaped['strategies'] = [project_strategy_row(row) for row in (result.get('strategies') or [])]
        return shaped

    def _workflow_detail(self, candidate_id=None, holder_key=None):
        state, _ = self._workflow_state()
        cache_key = ('detail', candidate_id, holder_key, self._workflow_projection_token(state))
        cached = self._workflow_cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            scenario, cid, label, holder = self._workflow_target(candidate_id, holder_key)
            detail = self._libcall('_native_detail', cid) if cid else None
            if isinstance(detail, dict) and detail.get('ok'):
                detail['scenario'] = scenario
                detail['label'] = label
                detail['resident'] = True
                detail['verificationOnly'] = self._workflow_verification_only()
                for summary_key in ('storedSummary', 'trialSummary'):
                    if isinstance(detail.get(summary_key), dict):
                        detail[summary_key]['verificationOnly'] = self._workflow_verification_only()
                projected = project_detail(detail)
                self._workflow_cache_put(cache_key, projected)
                return projected
            projected = project_detail(dict(ok=True, candidateId=cid, label=label, scenario=scenario, holder=holder,
                        resident=bool(cid), storedRuns=[], trialRuns=[],
                        storedSummary=self._workflow_summary([]), trialSummary=self._workflow_summary([]),
                        encounterLedger=None, outcomeDistribution=None, formation=None,
                        verificationOnly=self._workflow_verification_only(), native=True, measured=False,
                        provenance=self._provenance()))
            self._workflow_cache_put(cache_key, projected)
            return projected
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_evidence_runs(self, candidate_id, seed_pairs):
        """Read only compact rows for one paired seed bank, never full candidate journals."""
        if not candidate_id:
            return []
        return self._libcall('_native_evidence_runs', str(candidate_id), list(seed_pairs or [])) or []

    def _workflow_existing_seed_pairs(self, candidate_id):
        if not candidate_id:
            return []
        return self._libcall('_native_seed_pairs', str(candidate_id)) or []

    def _workflow_run_strategy(self, candidate_id=None, holder_key=None, count=8):
        try:
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
                raise ValueError('count must be a whole number from 1 through 8.')
            if self.status().get('state') in ('Running', 'Saving', 'Opening library'):
                raise ValueError('Pause the native search and wait for its current wave to save first.')
            scenario, cid, label, _holder = self._workflow_target(candidate_id, holder_key)
            imported = self._workflow_import_build(scenario, label)
            if not imported.get('ok'):
                return imported
            cid = imported['candidateId']
            existing_detail = self._workflow_detail(candidate_id=cid)
            used = {tuple(row.get('seeds') or ())
                    for row in (existing_detail.get('storedRuns') or [])}
            if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                used.add((scenario['mathSeed'], scenario['libSeed']))
            seeds = self._workflow_reserve_seeds(scenario['encounterId'], count, used)
            records = self.evaluate(scenario, seeds, count)
            all_detail = self._workflow_detail(candidate_id=cid)
            if not all_detail.get('ok'):
                return all_detail
            wanted = {tuple(pair) for pair in seeds}
            batch = [r for r in all_detail.get('storedRuns', []) if tuple(r.get('seeds') or ()) in wanted]
            if len(batch) != len(records):
                return {'ok': False, 'error': f'Native evaluation produced {len(records)} rows but only {len(batch)} matching durable strategy rows.'}
            all_detail.update(trialRuns=batch, trialSummary=self._workflow_summary(batch),
                              batchSummary=self._workflow_summary(batch),
                              workflowNative=True)
            return all_detail
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_reserve_seeds(self, encounter_id, count, used=()):
        from strategy_optimizer import seed_pair
        return self._reserve_seed_pairs(
            encounter_id, count, used,
            lambda ordinal: seed_pair('investigation', ordinal))

    def _workflow_start_strategy_experiment(self, candidate_id=None, holder_key=None,
                                            count=4096, mode='evaluate'):
        """Run a focused evaluate batch through native executable, checkpointing every bounded wave."""
        try:
            if mode in ('skills', 'remove-skills'):
                return self._workflow_start_native_skills(candidate_id, count=count,
                                                           holder_key=holder_key)
            if mode != 'evaluate':
                raise ValueError('Native workflow supports evaluate and native paired skills experiments only.')
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError('count must be a positive integer.')
            if self.status().get('state') in ('Running', 'Saving', 'Opening library'):
                raise ValueError('Pause the native search and wait for its current wave to save first.')
            scenario, cid, label, _holder = self._workflow_target(candidate_id, holder_key)
            imported = self._workflow_import_build(scenario, label)
            if not imported.get('ok'):
                return imported
            cid = imported['candidateId']
            detail = self._workflow_detail(candidate_id=cid)
            used = {tuple(row.get('seeds') or ()) for row in (detail.get('storedRuns') or [])}
            if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                used.add((scenario['mathSeed'], scenario['libSeed']))
            job = dict(id=uuid.uuid4().hex, mode='evaluate', status='queued', candidateId=cid,
                       encounterId=scenario['encounterId'], requestedPerBuild=count,
                       completed=0, total=count, createdAt=time.time(), label=label,
                       engine=self.language, provenance=self._provenance(),
                       seedPairs=[], cancelRequested=False)
            with self._workflow_lock():
                _, state = self._workflow_state()
                if state.get('activeExperimentId'):
                    return {'ok': False, 'error': 'A focused native evaluation is already queued or running.'}
                history = list(state.get('experiments') or [])
                history.append(job)
                state.update(experiments=history, activeExperimentId=job['id'])
                self._workflow_save(state)
            return {'ok': True, 'candidateId': cid,
                    'experiment': self._workflow_experiment_payload(job)}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_native_propose(self, scenario, request, directory):
        """Load the exact CLI proposal adapter without importing a Python mutation layer."""
        recovery = Path(__file__).resolve().parent
        if str(recovery) not in sys.path:
            sys.path.insert(0, str(recovery))
        from strategy_native_proposals import propose_native
        return propose_native(self._engine_assets, scenario, request, directory)

    @staticmethod
    def _workflow_int_targets(targets):
        if targets is None:
            return None
        if not isinstance(targets, (list, tuple)):
            raise ValueError('targets must be an array of exact effective integer values.')
        result = []
        for value in targets:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**31-1:
                raise ValueError('each fine-tune target must be a nonnegative signed32 integer.')
            if value not in result:
                result.append(value)
        return sorted(set(result))

    def _workflow_proposal_request(self, operation, candidate_id, scenario=None, axis=None, unit=None,
                                  targets=None, count=0, axis2=None, targets2=None,
                                  points=7, points2=None, pivot_label=None, unestablished=False):
        """Build only the selected engine's published native request schema."""
        if operation == 'probe':
            selected = getattr(self, '_engine_assets', {}) or {}
            capabilities = selected.get('capabilities') or {}
            capability = capabilities.get('nativeProbe', capabilities.get('probe'))
            if not capability:
                raise ValueError(f'The selected {self.language} build has no published native Probe capability.')
            if capabilities.get('probeValueMode') not in (None, 'raw'):
                raise ValueError('The selected native Probe build does not declare raw-input probe semantics.')
            if not targets and capabilities.get('probeDefaultLadder') is False:
                raise ValueError('The selected native Probe build does not support its original default ladder.')
            if axis2 is not None and capabilities.get('probeCartesianGrid') is False:
                raise ValueError('The selected native Probe build does not support Cartesian two-axis grids.')
            if self.language == 'rust':
                request = {'kind': 'probe', 'axis': axis, 'unit': unit, 'points': points}
                if targets:
                    request['targets'] = list(targets)
                if axis2 is not None:
                    request.update(axis2=axis2, points2=points2 or points)
                    if targets2:
                        request['targets2'] = list(targets2)
                if unestablished:
                    request['unestablished'] = True
                return request
            if self.language == 'go':
                request = {'schema': 'ka-go-proposal-request-1', 'operation': 'probe',
                           'parentId': str(candidate_id), 'axis': axis, 'unit': unit, 'points': points}
                if targets:
                    request['targets'] = list(targets)
                if axis2 is not None:
                    request.update(axis2=axis2, points2=points2 or points)
                    if targets2:
                        request['targets2'] = list(targets2)
                return request
            if self.language == 'cpp':
                request = {'mode': 'probe', 'axis': axis, 'unit': unit,
                           'candidateId': str(candidate_id), 'pivotLabel': pivot_label,
                           'points': points, 'runs': count, 'unestablished': bool(unestablished)}
                if targets:
                    request['values'] = list(targets)
                if axis2 is not None:
                    request.update(axis2=axis2, points2=points2 or points)
                    if targets2:
                        request['values2'] = list(targets2)
                return request
            raise ValueError('The selected engine has no published original-Probe request schema.')
        if operation == 'fine-tune':
            if self.language == 'rust':
                request = {'kind': 'stat-axis', 'axis': axis, 'unit': unit}
                if axis2 is not None:
                    request['axis2'] = axis2
                if targets:
                    request['targets'] = list(targets)
                if targets2:
                    request['targets2'] = list(targets2)
                return request
            if axis2 is not None:
                raise ValueError(f'{self.language} native tuning does not support a two-axis interaction request.')
            if self.language == 'go':
                selected = getattr(self, '_engine_assets', {}) or {}
                capabilities = selected.get('capabilities') or {}
                revision = str(selected.get('revision') or '')
                effective_supported = (
                    revision == '5f63a6f777e80bce5fcd58dbbfb410b4346215023c958a5a720c471c27d1fe0c'
                    or capabilities.get('fineTuneValueMode') == 'effective'
                )
                if effective_supported:
                    request = {'schema': 'ka-go-proposal-request-1', 'operation': 'fine-tune',
                               'parentId': str(candidate_id), 'unit': unit, 'axis': axis,
                               'valueMode': 'effective'}
                    if targets:
                        request['values'] = list(targets)
                    return request
                if not targets:
                    raise ValueError('The selected Go build supports raw-value tuning only; effective-value targets or the effective broad ladder are unavailable.')
                return {'schema': 'ka-go-proposal-request-1', 'operation': 'fine-tune',
                        'parentId': str(candidate_id), 'unit': unit, 'axis': axis,
                        'values': list(targets)}
            if self.language == 'cpp':
                selected = getattr(self, '_engine_assets', {}) or {}
                if selected.get('capabilities', {}).get('effectiveTuningTargets') is True:
                    request = {'mode': 'tuning', 'axis': axis, 'unit': unit,
                               'targetDomain': 'effective'}
                    if targets:
                        request['targets'] = list(targets)
                    bounds = selected.get('staticInputs', {}).get('statBounds')
                    if bounds:
                        request.update(statBoundsPath=str(bounds['path']),
                                       statBoundsSha256=bounds['sha256'])
                    return request
                if not targets:
                    raise ValueError('C++ native tuning requires explicit targets; its CLI does not implement the original effective-value broad ladder.')
                return {'mode': 'tuning', 'axis': axis, 'unit': unit, 'targets': list(targets)}
            raise ValueError('This engine has no accepted native fine-tuning schema.')
        if operation == 'skills':
            if self.language == 'rust':
                request = {'kind': 'remove-skills'}
                if unit is not None:
                    matches = [index for index, row in enumerate((scenario or {}).get('ownUnits') or [])
                               if row.get('human') is True and row.get('name') == unit]
                    if len(matches) != 1:
                        raise ValueError('Rust named-unit skill removal requires one exact matching human roster entry.')
                    request['units'] = matches
                return request
            if self.language == 'go':
                return {'schema': 'ka-go-proposal-request-1', 'operation': 'skills',
                        'parentId': str(candidate_id), **({'unit': unit} if unit else {})}
            if self.language == 'cpp':
                supports_named = bool((getattr(self, '_engine_assets', {}).get('capabilities') or {}).get('nativeNamedUnitSkillArms'))
                if unit is not None and not supports_named:
                    raise ValueError('C++ skill proposals are team-wide; named-unit-only skill removal is unsupported.')
                if count < 128:
                    raise ValueError('C++ native skill experiments require at least 128 paired evaluation seeds.')
                return {'mode': 'skills', 'count': count, **({'unit': unit} if unit is not None else {})}
        raise ValueError('Unsupported native proposal operation.')

    def _workflow_start_paired_proposals(self, operation, candidate_id, *, axis=None,
                                         unit=None, targets=None, axis2=None, targets2=None,
                                         count=128, holder_key=None, points=7, points2=None,
                                         unestablished=False):
        """Propose exact descendants natively, then queue parent/descendant paired evaluation."""
        try:
            if self.status().get('state') in ('Running', 'Saving', 'Opening library'):
                raise ValueError('Pause the native search and wait for its current wave to save first.')
            with self._workflow_lock():
                _, state = self._workflow_state()
                if state.get('activeExperimentId'):
                    raise ValueError('A focused native evaluation is already queued or running.')
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError('count must be a positive whole number of paired runs per arm.')
            if operation == 'fine-tune':
                if axis not in {'hp', 'mp', 'vig', 'atk', 'def', 'spd', 'lck', 'dex', 'int'}:
                    raise ValueError('Choose one of the original combat fine-tune axes.')
                targets = self._workflow_int_targets(targets)
                if targets == []:
                    targets = None
                if axis2 is not None:
                    selected_capabilities = getattr(self, '_engine_assets', {}).get('capabilities') or {}
                    tuning_knobs = selected_capabilities.get('tuningKnobs') or []
                    if self.language != 'rust' or 'two-axis-stat-interaction' not in tuning_knobs:
                        raise ValueError('Two-axis native fine-tune interactions are currently supported only by the selected Rust build.')
                    if axis2 not in {'hp', 'mp', 'vig', 'atk', 'def', 'spd', 'lck', 'dex', 'int'} or axis2 == axis:
                        raise ValueError('Choose a distinct supported combat axis for the second interaction axis.')
                    targets2 = self._workflow_int_targets(targets2)
                    if targets2 == []:
                        targets2 = None
            elif operation == 'probe':
                if axis not in {'hp', 'mp', 'vig', 'atk', 'def', 'spd', 'lck', 'dex', 'int', 'herbs'}:
                    raise ValueError('Choose an original supported Probe axis.')
                if axis2 is not None and (axis2 not in {'hp', 'mp', 'vig', 'atk', 'def', 'spd', 'lck', 'dex', 'int', 'herbs'} or axis2 == axis):
                    raise ValueError('The second Probe axis must be distinct and supported.')
                targets = self._workflow_int_targets(targets)
                targets2 = self._workflow_int_targets(targets2) if axis2 else None
            scenario, parent_id, label, _holder = self._workflow_target(
                candidate_id=candidate_id, holder_key=holder_key)
            imported_parent = self._workflow_import_build(scenario, label)
            if not imported_parent.get('ok'):
                return imported_parent
            parent_id = imported_parent['candidateId']
            if operation == 'fine-tune' and unit is None:
                # Retain the legacy host's selected unit semantics. This only reads the exact intent;
                # all descendant construction and legality remain in the chosen native engine.
                recovery = Path(__file__).resolve().parent
                if str(recovery) not in sys.path:
                    sys.path.insert(0, str(recovery))
                import strategy_probe
                unit = strategy_probe.resolve_probe_unit(scenario, [axis] + ([axis2] if axis2 else []), None)
                if unit is None:
                    raise ValueError('The parent has no human unit with a usable value for this axis.')
            if operation == 'probe' and unit is None and (axis != 'herbs' or axis2 not in (None, 'herbs')):
                recovery = Path(__file__).resolve().parent
                if str(recovery) not in sys.path:
                    sys.path.insert(0, str(recovery))
                import strategy_probe
                unit = strategy_probe.resolve_probe_unit(scenario, [axis] + ([axis2] if axis2 else []), None)
            baseline_effective = None
            baseline_effective2 = None
            if operation == 'fine-tune':
                recovery = Path(__file__).resolve().parent
                if str(recovery) not in sys.path:
                    sys.path.insert(0, str(recovery))
                from search_contract import battle_value
                import strategy_probe
                axis_info = strategy_probe.AXES[axis]
                if 'parameter' not in axis_info:
                    raise ValueError('This native fine-tuning workflow supports combat parameters only.')
                baseline_unit = next((entry for entry in (scenario.get('ownUnits') or [])
                                      if entry.get('name') == unit), None)
                if baseline_unit is None:
                    raise ValueError(f'Fine-tune unit {unit!r} is absent from the exact parent intent.')
                baseline_effective = int(battle_value(scenario, baseline_unit, axis_info['parameter']))
                if axis2 is not None:
                    axis2_info = strategy_probe.AXES[axis2]
                    if 'parameter' not in axis2_info:
                        raise ValueError('The second native fine-tuning axis must be a combat parameter.')
                    baseline_effective2 = int(battle_value(scenario, baseline_unit, axis2_info['parameter']))
            native_request = self._workflow_proposal_request(
                operation, parent_id, scenario=scenario, axis=axis, unit=unit, targets=targets,
                axis2=axis2, targets2=targets2, count=count, points=points, points2=points2,
                pivot_label=label, unestablished=unestablished)

            # Proposal output lives beside the library in a separate transient namespace. Native
            # outputs/logs are retained with the workflow job for review and provenance.
            proposal_dir = Path(self.path).resolve().parent / 'native-proposals' / uuid.uuid4().hex
            native_result = self._workflow_native_propose(scenario, native_request, proposal_dir)
            if not native_result.get('ok'):
                return {'ok': False, 'error': '; '.join(native_result.get('errors') or ['Native proposal failed.']),
                        'proposal': native_result}

            proposals = []
            rejected = []
            native_probe_reused = []
            requested_set = set(targets or [])
            requested_set2 = set(targets2 or [])
            for row in native_result.get('proposals') or []:
                intent = row.get('intent')
                if operation == 'probe' and not isinstance(intent, dict) and not row.get('errors'):
                    native_row = row.get('nativeRow') or {}
                    recovery = Path(__file__).resolve().parent
                    if str(recovery) not in sys.path:
                        sys.path.insert(0, str(recovery))
                    import strategy_probe
                    raw_first = native_row.get('value', native_row.get('Value'))
                    raw_second = native_row.get('value2', native_row.get('Value2'))
                    baseline_first = strategy_probe.pivot_value(scenario, unit, axis)
                    baseline_second = strategy_probe.pivot_value(scenario, unit, axis2) if axis2 else None
                    if raw_first == baseline_first and (axis2 is None or raw_second == baseline_second):
                        native_probe_reused.append(dict(value=raw_first, value2=raw_second,
                            candidateId=parent_id, effective=native_row.get('effective', native_row.get('Effective')),
                            effective2=native_row.get('effective2', native_row.get('Effective2'))))
                        continue
                if row.get('legal') is not True or not isinstance(intent, dict):
                    rejected.append(row)
                    continue
                probe_value = probe_value2 = None
                if operation == 'probe':
                    recovery = Path(__file__).resolve().parent
                    if str(recovery) not in sys.path:
                        sys.path.insert(0, str(recovery))
                    import strategy_probe
                    def raw_probe_value(raw_scenario, probe_axis):
                        info = strategy_probe.AXES[probe_axis]
                        if 'parameter' not in info:
                            return int(raw_scenario.get(info['input']) or 0)
                        raw_unit = next((entry for entry in (raw_scenario.get('ownUnits') or [])
                                         if entry.get('name') == unit), None)
                        parameters = (raw_unit or {}).get('parameters') or {}
                        parameter = parameters.get(str(info['parameter']), parameters.get(info['parameter']))
                        if not isinstance(parameter, dict):
                            raise ValueError(f'Native Probe child lacks parameter {info["parameter"]}.')
                        return int(parameter.get('rawValue'))
                    probe_value = raw_probe_value(intent, axis)
                    probe_value2 = raw_probe_value(intent, axis2) if axis2 else None
                    if ((requested_set and probe_value not in requested_set)
                            or (axis2 and requested_set2 and probe_value2 not in requested_set2)):
                        rejected.append({**row, 'legal': False,
                                         'errors': list(row.get('errors') or []) + [
                                             'Native Probe child raw values do not match the requested ladder.'
                                         ]})
                        continue
                if operation == 'fine-tune' and (targets or targets2):
                    native_row = row.get('nativeRow') or {}
                    # Go's `values` and C++'s `targets` address rawValue. The original desktop
                    # targets address effective stats. Admit only native descendants that reached
                    # an exact requested effective target; never relabel an input value as effective.
                    if self.language == 'rust':
                        reached = native_row.get('targetReached') is True
                        effective = native_row.get('effective')
                    else:
                        effective = row.get('effective')
                        reached = effective in requested_set
                    if not reached:
                        rejected.append({**row, 'legal': False,
                                         'errors': list(row.get('errors') or []) + [
                                             'Native effective value did not equal an exact requested effective target.'
                                         ]})
                        continue
                proposals.append(row)
            if not proposals:
                return {'ok': False, 'error': 'Native engine produced no legal proposal matching the requested effective target(s).',
                        'proposal': native_result, 'rejected': rejected}

            arms = [{'candidateId': parent_id, 'label': label or parent_id, 'role': 'parent',
                     'effectiveBaseline': baseline_effective, 'effectiveBaseline2': baseline_effective2,
                     'axis': axis, 'axis2': axis2, 'unit': unit}]
            seen = {parent_id}
            reused_probe = []
            for index, row in enumerate(proposals):
                probe_value = probe_value2 = probe_effective = None
                candidate_label = f"Native {operation}: {row.get('operator') or index+1}"
                if operation == 'probe':
                    raw_scenario = row.get('intent') or {}
                    recovery = Path(__file__).resolve().parent
                    if str(recovery) not in sys.path:
                        sys.path.insert(0, str(recovery))
                    import strategy_probe
                    info = strategy_probe.AXES[axis]
                    if 'parameter' in info:
                        raw_unit = next((entry for entry in (raw_scenario.get('ownUnits') or [])
                                         if entry.get('name') == unit), None)
                        parameters = (raw_unit or {}).get('parameters') or {}
                        parameter = parameters.get(str(info['parameter']), parameters.get(info['parameter']))
                        probe_value = int(parameter.get('rawValue'))
                    else:
                        probe_value = int(raw_scenario.get(info['input']) or 0)
                    probe_value2 = None
                    if axis2:
                        info2 = strategy_probe.AXES[axis2]
                        if 'parameter' in info2:
                            raw_unit = next((entry for entry in (raw_scenario.get('ownUnits') or [])
                                             if entry.get('name') == unit), None)
                            parameters = (raw_unit or {}).get('parameters') or {}
                            parameter = parameters.get(str(info2['parameter']), parameters.get(info2['parameter']))
                            probe_value2 = int(parameter.get('rawValue'))
                        else:
                            probe_value2 = int(raw_scenario.get(info2['input']) or 0)
                    candidate_label = (strategy_probe.grid_label(axis, probe_value, axis2,
                                      probe_value2, unit, label) if axis2 else
                                      strategy_probe.probe_label(axis, unit, probe_value, label))
                    native_row = row.get('nativeRow') or {}
                    effective_data = row.get('effective')
                    if axis2:
                        effective_data = effective_data if isinstance(effective_data, dict) else {}
                        probe_effective = dict(
                            value=effective_data.get('value', effective_data.get('effective',
                                native_row.get('effective', native_row.get('effectiveValue')))),
                            value2=effective_data.get('value2', effective_data.get('effective2',
                                native_row.get('effective2', native_row.get('effectiveValue2')))))
                    else:
                        probe_effective = (effective_data.get('value', effective_data.get('effective',
                                                native_row.get('effective', native_row.get('effectiveValue'))))
                                           if isinstance(effective_data, dict) else
                                           (effective_data if effective_data is not None else
                                            native_row.get('effective', native_row.get('effectiveValue'))))
                imported = self._workflow_import_build(row['intent'], candidate_label)
                if not imported.get('ok'):
                    rejected.append({**row, 'legal': False, 'errors': list(row.get('errors') or []) + [imported.get('error', 'Import failed')]})
                    continue
                cid = str(imported['candidateId'])
                if cid in seen:
                    if operation == 'probe':
                        rung = dict(value=probe_value, value2=probe_value2, candidateId=cid)
                        reused_probe.append(rung)
                        if cid == parent_id:
                            arms[0].update(probeValue=probe_value, probeValue2=probe_value2,
                                           probeEffective=probe_effective)
                        continue
                    rejected.append({**row, 'legal': False, 'errors': list(row.get('errors') or []) + ['Proposal duplicates an existing paired arm.']})
                    continue
                seen.add(cid)
                with self._workflow_lock():
                    _, state = self._workflow_state()
                    records = dict(state.get('candidates') or {})
                    saved = dict(records.get(cid) or {})
                    saved.update(intent=row['intent'], label=candidate_label,
                                 source='native-proposal', nativeProposal=row,
                                 parentId=parent_id, proposalProvenance=native_result.get('provenance'))
                    records[cid] = saved
                    state['candidates'] = records
                    self._workflow_save(state)
                arms.append({'candidateId': cid, 'label': saved['label'], 'role': 'proposal',
                             'nativeCandidateId': row.get('candidateId'),
                             'parentId': parent_id, 'operator': row.get('operator'),
                             'effective': row.get('effective'),
                             'axis': axis, 'axis2': axis2, 'unit': unit,
                             'probeValue': probe_value if operation == 'probe' else None,
                             'probeValue2': probe_value2 if operation == 'probe' else None,
                             'probeEffective': probe_effective if operation == 'probe' else None,
                             'requestedEffective': ((row.get('nativeRow') or {}).get('requested',
                                 (row.get('nativeRow') or {}).get('value',
                                 (row.get('nativeRow') or {}).get('targetValue', row.get('effective'))))),
                             'nativeRow': row.get('nativeRow')})

            if len(arms) < 2:
                return {'ok': False, 'error': 'No distinct native proposal could be imported for paired evaluation.',
                        'proposal': native_result, 'rejected': rejected}
            used = set()
            for arm in arms:
                used.update(tuple(pair) for pair in self._workflow_existing_seed_pairs(arm['candidateId']))
            if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                used.add((scenario['mathSeed'], scenario['libSeed']))
            seed_pairs = self._workflow_reserve_seeds(scenario['encounterId'], count, used)
            job = dict(id=uuid.uuid4().hex, mode=operation, status='queued', candidateId=parent_id,
                       parentCandidateId=parent_id, candidateIds=[arm['candidateId'] for arm in arms],
                       arms=arms, encounterId=scenario['encounterId'], requestedPerBuild=count,
                       completed=0, total=len(arms)*count, nextArm=0, armCompleted=0,
                       createdAt=time.time(), label=label, engine=self.language,
                       axis=axis, axis2=axis2, unit=unit, targets=targets, targets2=targets2,
                       effectiveBaseline=baseline_effective, effectiveBaseline2=baseline_effective2,
                       provenance=self._provenance(), seedPairs=seed_pairs,
                       proposal=native_result, rejectedProposals=rejected, cancelRequested=False)
            if operation == 'probe':
                recovery = Path(__file__).resolve().parent
                if str(recovery) not in sys.path:
                    sys.path.insert(0, str(recovery))
                import strategy_probe
                probe_info = strategy_probe.AXES[axis]
                probe_info2 = strategy_probe.AXES.get(axis2) if axis2 else None
                native_response = native_result.get('nativeResponse') or {}
                response_reused = native_response.get('reused') or []
                response_refused = native_response.get('refused') or []
                native_ladder_evidence = (response_reused + response_refused + native_probe_reused +
                                          reused_probe)

                def raw_ladder_values(requested, requested_other, arm_key, response_key,
                                      response_other_key):
                    values = {value for value in (requested or [])
                              if isinstance(value, int) and not isinstance(value, bool)}
                    values.update(arm.get(arm_key) for arm in arms
                                  if isinstance(arm.get(arm_key), int) and
                                  not isinstance(arm.get(arm_key), bool))
                    for item in native_ladder_evidence:
                        if not isinstance(item, dict):
                            continue
                        value = item.get(response_key)
                        other = item.get(response_other_key) if axis2 else None
                        if not isinstance(value, int) or isinstance(value, bool):
                            continue
                        # A reused parent anchor outside an explicit Cartesian grid is the
                        # reference, not a requested grid point. Retain reused/refused values
                        # only when they lie inside the requested axes.
                        if requested is not None and value not in requested:
                            continue
                        if (axis2 and requested_other is not None and
                                (not isinstance(other, int) or other not in requested_other)):
                            continue
                        values.add(value)
                    return sorted(values)

                probe_values = raw_ladder_values(targets, targets2, 'probeValue', 'value', 'value2')
                probe_values2 = (raw_ladder_values(targets2, targets, 'probeValue2', 'value2', 'value')
                                 if axis2 else [])
                job.update(probe=True, probeValues=probe_values,
                           probeValues2=probe_values2,
                           probeBaselineValue=strategy_probe.pivot_value(scenario, unit, axis),
                           probeBaselineValue2=(strategy_probe.pivot_value(scenario, unit, axis2)
                                                if axis2 else None),
                           probeNativeReused=response_reused + native_probe_reused,
                           probeNativeRefused=response_refused,
                           probeReused=reused_probe,
                           probeEstablished=bool(probe_info.get('established') and
                               (not probe_info2 or probe_info2.get('established'))),
                           probeRuns=count, probePoints=points, probePoints2=points2,
                           probeUnestablished=bool(unestablished))
            with self._workflow_lock():
                _, state = self._workflow_state()
                if state.get('activeExperimentId'):
                    return {'ok': False, 'error': 'A focused native evaluation is already queued or running.',
                            'proposal': native_result}
                history = list(state.get('experiments') or [])
                history.append(job)
                state.update(experiments=history, activeExperimentId=job['id'])
                self._workflow_save(state)
            return {'ok': True, 'candidateId': parent_id, 'candidateIds': [arm['candidateId'] for arm in arms],
                    'seedPairs': seed_pairs, 'rejectedProposals': rejected,
                    'experiment': self._workflow_experiment_payload(job)}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_start_native_finetune(self, candidate_id, axis, unit=None,
                                        targets=None, count=128, axis2=None, targets2=None):
        """Native descendants for original exact one-axis or supported Rust interaction tuning."""
        return self._workflow_start_paired_proposals(
            'fine-tune', candidate_id, axis=axis, unit=unit, targets=targets,
            axis2=axis2, targets2=targets2, count=count)

    def _workflow_start_native_probe(self, candidate_id, axis, axis2=None, unit=None,
                                     unestablished=False, count=160, values=None,
                                     values2=None, points=7, points2=None):
        """Create original raw-value ladder arms with the selected native proposal adapter."""
        try:
            if not isinstance(unestablished, bool):
                raise ValueError('unestablished must be a boolean opt-in.')
            caps = getattr(self, '_engine_assets', {}).get('capabilities') or {}
            probe_capability = caps.get('nativeProbe', caps.get('probe'))
            if not probe_capability:
                raise ValueError(f'The selected {self.language} build has no published native Probe capability; no arms were created.')
            scenario, candidate_id, _label, _holder = self._workflow_target(candidate_id=candidate_id)
            recovery = Path(__file__).resolve().parent
            if str(recovery) not in sys.path:
                sys.path.insert(0, str(recovery))
            import strategy_probe
            info = strategy_probe.axis_info(axis)
            info2 = strategy_probe.axis_info(axis2) if axis2 else None
            if not info.get('established') and not unestablished:
                raise ValueError(info.get('evidence') or f'{axis} is not an established Probe axis; explicit unestablished opt-in is required.')
            if info2 and not info2.get('established') and not unestablished:
                raise ValueError(info2.get('evidence') or f'{axis2} is not an established Probe axis; explicit unestablished opt-in is required.')
            if axis2 == axis:
                raise ValueError('A two-axis Probe requires two distinct axes.')
            unit = strategy_probe.resolve_probe_unit(scenario, [axis] + ([axis2] if axis2 else []), unit)
            first_values = self._workflow_int_targets(values)
            second_values = self._workflow_int_targets(values2) if axis2 else None
            points = int(points or 7)
            points2 = int(points2 or points) if axis2 else None
            if points < 2 or (points2 is not None and points2 < 2):
                raise ValueError('Native Probe default ladders require at least two points per axis.')
            # Probe runs are paired and use the original bank bounds/default.
            count = max(64, min(512, int(count or 160)))
            result = self._workflow_start_paired_proposals(
                'probe', candidate_id, axis=axis, axis2=axis2, unit=unit,
                targets=first_values, targets2=second_values, count=count,
                points=points, points2=points2, unestablished=unestablished)
            if result.get('ok'):
                result['lastProbe'] = self._workflow_native_probe_last()
            return result
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_start_native_skills(self, candidate_id=None, unit=None, count=128, holder_key=None):
        """Native human-skill removals, each paired against the frozen parent on one seed bank."""
        return self._workflow_start_paired_proposals(
            'skills', candidate_id, unit=unit, count=count, holder_key=holder_key)

    def _workflow_run_pending_chunk(self):
        """Run at most one native evaluation wave; root scheduler calls this before search waves."""
        event = self._workflow_chunk_event()
        with self._workflow_lock():
            _, state = self._workflow_state()
            active_id = state.get('activeExperimentId')
            job = next((dict(item) for item in (state.get('experiments') or [])
                        if item.get('id') == active_id), None)
            if not job:
                return False
            if job.get('cancelRequested'):
                self._workflow_finish_job(state, job, 'ended')
                return True
            paired = isinstance(job.get('arms'), list) and len(job['arms']) > 1
            if paired:
                arms = list(job['arms'])
                arm_index = int(job.get('nextArm', 0))
                arm_done = int(job.get('armCompleted', 0))
                while arm_index < len(arms) and arm_done >= int(job.get('requestedPerBuild', 0)):
                    arm_index += 1
                    arm_done = 0
                if arm_index >= len(arms):
                    self._workflow_finish_job(state, job, 'complete')
                    return True
                arm = arms[arm_index]
                remaining = int(job.get('requestedPerBuild', 0)) - arm_done
                count = min(_CHUNK, remaining)
                scenario_record = (state.get('candidates') or {}).get(arm['candidateId'])
                candidate_id = arm['candidateId']
                seeds = list(job.get('seedPairs') or [])[arm_done:arm_done+count]
                if len(seeds) != count:
                    self._workflow_finish_job(state, job, 'incomplete', 'Paired seed bank is shorter than the requested arm count.')
                    return True
                job['nextArm'] = arm_index
                job['armCompleted'] = arm_done
            else:
                remaining = int(job.get('requestedPerBuild', 0))-int(job.get('completed', 0))
                if remaining <= 0:
                    self._workflow_finish_job(state, job, 'complete')
                    return True
                count = min(_CHUNK, remaining)
                candidate_id = job['candidateId']
                scenario_record = (state.get('candidates') or {}).get(candidate_id)
                seeds = None
            if not isinstance(scenario_record, dict) or not isinstance(scenario_record.get('intent'), dict):
                self._workflow_finish_job(state, job, 'incomplete', 'Exact native intent is missing from workflow metadata.')
                return True
            scenario = dict(scenario_record['intent'])
            if not paired:
                used = {tuple(pair) for pair in self._workflow_existing_seed_pairs(candidate_id)}
                used.update(tuple(pair) for pair in job.get('seedPairs') or [])
                if isinstance(scenario.get('mathSeed'), int) and isinstance(scenario.get('libSeed'), int):
                    used.add((scenario['mathSeed'], scenario['libSeed']))
                seeds = self._workflow_reserve_seeds(job['encounterId'], count, used)
                job['seedPairs'] = list(job.get('seedPairs') or []) + seeds
            job['status'] = 'running'
            self._workflow_update_job(state, job)
            event.clear()
        try:
            records = self.evaluate(scenario, seeds, count)
            with self._workflow_lock():
                _, state = self._workflow_state()
                current = self._workflow_find_job(state, job['id']) or job
                current['completed'] = int(current.get('completed', 0))+len(records)
                if paired:
                    current['armCompleted'] = int(current.get('armCompleted', 0))+len(records)
                current.pop('error', None)
                if len(records) != count:
                    self._workflow_finish_job(state, current, 'incomplete',
                        f'Native evaluation saved {len(records)} of {count} requested rows.')
                elif current.get('cancelRequested'):
                    self._workflow_finish_job(state, current, 'ended')
                elif paired and int(current.get('armCompleted', 0)) >= int(current.get('requestedPerBuild', 0)):
                    current['nextArm'] = int(current.get('nextArm', 0))+1
                    current['armCompleted'] = 0
                    if current['nextArm'] >= len(current.get('arms') or []):
                        self._workflow_finish_job(state, current, 'complete')
                    else:
                        current['status'] = 'queued'
                        self._workflow_update_job(state, current)
                elif current['completed'] >= current['requestedPerBuild']:
                    if not paired:
                        self._workflow_finish_job(state, current, 'complete')
                    else:
                        current['status'] = 'queued'
                        self._workflow_update_job(state, current)
                else:
                    current['status'] = 'queued'
                    self._workflow_update_job(state, current)
            return True
        except Exception as exc:
            with self._workflow_lock():
                _, state = self._workflow_state()
                current = self._workflow_find_job(state, job['id']) or job
                self._workflow_finish_job(state, current, 'incomplete', str(exc))
            return True
        finally:
            event.set()

    @staticmethod
    def _workflow_find_job(state, job_id):
        return next((item for item in (state.get('experiments') or [])
                     if item.get('id') == job_id), None)

    def _workflow_update_job(self, state, job):
        items = list(state.get('experiments') or [])
        for index, old in enumerate(items):
            if old.get('id') == job.get('id'):
                items[index] = dict(job)
                break
        state['experiments'] = items
        self._workflow_save(state)

    def _workflow_finish_job(self, state, job, status, error=None):
        job['status'] = status
        job['endedAt'] = time.time()
        if error:
            job['error'] = error
        state['activeExperimentId'] = None
        self._workflow_update_job(state, job)

    def _workflow_status(self):
        """Status fragment for the coordinator to merge into the normal optimizer snapshot."""
        try:
            with self._workflow_lock():
                _, state = self._workflow_state()
                active_id = state.get('activeExperimentId')
                job = dict(self._workflow_find_job(state, active_id) or {}) if active_id else None
            if job:
                job['status'] = 'running' if not self._workflow_chunk_event().is_set() else 'queued'
            programs = self._workflow_native_finetune_programs()
            probes = self._workflow_native_probe_reports()
            last_probe = self._workflow_native_probe_last()
            return {'focusedExperiment': self._workflow_experiment_payload(job) if job else None,
                    'fineTune': programs, 'lastFineTune': programs[-1] if programs else None,
                    'probes': probes, 'lastProbe': last_probe}
        except Exception as exc:
            return {'focusedExperiment': None, 'fineTune': [], 'lastFineTune': None,
                    'probes': [], 'lastProbe': None, 'workflowError': str(exc)}

    @staticmethod
    def _workflow_probe_raw_ladder(job, second_axis=False):
        """Project the complete native-requested ladder, including reused/refused rungs."""
        if not isinstance(job, dict):
            return []
        field = 'probeValue2' if second_axis else 'probeValue'
        response_field = 'value2' if second_axis else 'value'
        values = {value for value in (job.get('probeValues2' if second_axis else 'probeValues') or [])
                  if isinstance(value, int) and not isinstance(value, bool)}
        for arm in job.get('arms') or []:
            value = arm.get(field) if isinstance(arm, dict) else None
            if isinstance(value, int) and not isinstance(value, bool):
                values.add(value)
        native_result = job.get('proposal') if isinstance(job.get('proposal'), dict) else {}
        native_response = native_result.get('nativeResponse') if isinstance(native_result, dict) else {}
        evidence = list(job.get('probeNativeReused') or []) + list(job.get('probeNativeRefused') or [])
        evidence += list(job.get('probeReused') or []) + list(job.get('rejectedProposals') or [])
        if isinstance(native_response, dict):
            evidence += list(native_response.get('reused') or []) + list(native_response.get('refused') or [])
        for item in evidence:
            if not isinstance(item, dict):
                continue
            value = item.get(response_field)
            first = item.get('value')
            second = item.get('value2')
            requested_first = job.get('targets')
            requested_second = job.get('targets2')
            if requested_first is not None and first not in requested_first:
                continue
            if job.get('axis2') and requested_second is not None and second not in requested_second:
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                values.add(value)
        return sorted(values)

    def _workflow_native_probe_last(self):
        _, state = self._workflow_state()
        job = next((item for item in reversed(state.get('experiments') or [])
                    if item.get('mode') == 'probe'), None)
        if not job:
            return None
        return dict(axis=job.get('axis'), axis2=job.get('axis2'), unit=job.get('unit'),
                    pivotId=job.get('candidateId'), pivotLabel=job.get('label'),
                    runs=job.get('requestedPerBuild'), ladder=self._workflow_probe_raw_ladder(job),
                    ladder2=(self._workflow_probe_raw_ladder(job, True) or None),
                    established=job.get('probeEstablished'),
                    created=[dict(value=arm.get('probeValue'), value2=arm.get('probeValue2'),
                                  candidateId=arm.get('candidateId'))
                             for arm in (job.get('arms') or []) if arm.get('role') == 'proposal'],
                    reused=(job.get('probeNativeReused') or []) + (job.get('probeReused') or []),
                    refused=(job.get('probeNativeRefused') or []) + (job.get('rejectedProposals') or []))

    def _workflow_native_probe_reports(self):
        _, state = self._workflow_state()
        cache_key = ('probe-reports', self._workflow_projection_token(state))
        cached = self._workflow_cache_get(cache_key)
        if cached is not None:
            return cached
        jobs = [job for job in (state.get('experiments') or []) if job.get('mode') == 'probe']
        reports = []
        for job in jobs:
            recovery = Path(__file__).resolve().parent
            if str(recovery) not in sys.path:
                sys.path.insert(0, str(recovery))
            import strategy_probe
            pairs = list(job.get('seedPairs') or [])
            parent_id = job.get('candidateId')
            _, workflow_state = self._workflow_state()
            parent_record = (workflow_state.get('candidates') or {}).get(parent_id) or {}
            scenario = parent_record.get('intent') or {}
            parent_rows = self._workflow_evidence_runs(parent_id, pairs)
            reference = strategy_probe.chest_series(parent_rows)
            axis, axis2 = job.get('axis'), job.get('axis2')
            cells = []
            ladder = []
            baseline_value = job.get('probeBaselineValue')
            if baseline_value is None:
                baseline_value = strategy_probe.pivot_value(scenario, job.get('unit'), axis)
            baseline_value2 = job.get('probeBaselineValue2')
            if axis2 and baseline_value2 is None:
                baseline_value2 = strategy_probe.pivot_value(scenario, job.get('unit'), axis2)
            anchor_reused = any(isinstance(item, dict) and
                item.get('candidateId') == parent_id and
                item.get('value') == baseline_value and
                item.get('value2') == (baseline_value2 if axis2 else None)
                for item in (job.get('probeNativeReused') or []) +
                             (job.get('probeReused') or []))
            anchor_requested = (job.get('targets') is None or baseline_value in job.get('targets', []))
            if axis2:
                anchor_requested = anchor_requested and (
                    job.get('targets2') is None or baseline_value2 in job.get('targets2', []))
            include_anchor_point = anchor_reused and anchor_requested
            for arm in (job.get('arms') or []):
                if arm.get('role') != 'proposal':
                    continue
                if (arm.get('probeValue') == baseline_value and
                        (not axis2 or arm.get('probeValue2') == baseline_value2)):
                    continue
                rows = self._workflow_evidence_runs(arm.get('candidateId'), pairs)
                chests = strategy_probe.chest_series(rows)
                effective = arm.get('probeEffective')
                if axis2:
                    effective = effective if isinstance(effective, dict) else {}
                    cells.append(dict(v1=arm.get('probeValue'), v2=arm.get('probeValue2'),
                                      effective1=effective.get('value'), effective2=effective.get('value2'),
                                      chests=chests, candidateId=arm.get('candidateId'), runs=len(chests)))
                else:
                    ladder.append(dict(value=arm.get('probeValue'), effective=effective,
                                       chests=chests, candidateId=arm.get('candidateId'), runs=len(chests)))
            if include_anchor_point:
                baseline = dict(chests=reference, candidateId=parent_id, runs=len(reference))
                if axis2:
                    cells.append(dict(v1=baseline_value, v2=baseline_value2,
                                      effective1=job.get('effectiveBaseline'),
                                      effective2=job.get('effectiveBaseline2'), **baseline))
                else:
                    ladder.append(dict(value=baseline_value,
                                       effective=job.get('effectiveBaseline'), **baseline))
            common = dict(pivotId=parent_id, pivotLabel=job.get('label'), axis=axis,
                          axisLabel=strategy_probe.AXES[axis]['label'], unit=job.get('unit'),
                          referenceRuns=len(reference), basis='Original native Probe raw-value ladder; native-produced legal intents; paired chest counts over identical seeds.')
            if axis2:
                result = strategy_probe.analyse_grid(cells, reference)
                report = dict(kind='grid', **common, axis2=axis2,
                              axis2Label=strategy_probe.AXES[axis2]['label'],
                              statement=result['statement'], interaction=result['interaction'],
                              thresholds=result['thresholds'], boundaryAt=None,
                              points=[dict(value=row['v1'], value2=row['v2'], effective=row['effective1'],
                                  effective2=row['effective2'], candidateId=next((cell['candidateId'] for cell in cells
                                      if (cell['v1'], cell['v2']) == (row['v1'], row['v2'])), None),
                                  runs=row['n'], chestMean=row['chestMean'], chestSE=row['chestSE'],
                                  viable=row['viable'], delta=row['delta']) for row in result['rows']])
            else:
                ladder.sort(key=lambda item: item.get('value'))
                result = strategy_probe.analyse(ladder, reference)
                report = dict(**common, viableLow=result['viableLow'], viableHigh=result['viableHigh'],
                              boundaryAt=', '.join(f"{min(edge['fromValue'], edge['toValue'])}-{max(edge['fromValue'], edge['toValue'])}"
                                                   for edge in result['transitions']) or None,
                              statement=result['statement'],
                              points=[dict(value=row['value'], effective=row['effective'],
                                  candidateId=next((item['candidateId'] for item in ladder
                                      if item['value'] == row['value']), None),
                                  runs=row['n'], chestMean=row['chestMean'], chestSE=row['chestSE'],
                                  viable=row['viable'], delta=row['delta']) for row in result['rows']])
            reports.append(report)
        self._workflow_cache_put(cache_key, reports)
        return reports

    def _workflow_native_finetune_programs(self, candidate_id=None):
        """Expose native one-shot fine-tune jobs in the desktop's FineTuneProgram shape."""
        _, state = self._workflow_state()
        jobs = [job for job in (state.get('experiments') or []) if job.get('mode') == 'fine-tune']
        if candidate_id is not None:
            jobs = [job for job in jobs if job.get('candidateId') == candidate_id
                    or candidate_id in (job.get('candidateIds') or [])]
        return [self._workflow_finetune_program(job) for job in jobs]

    def _workflow_finetune_program(self, job):
        _, state = self._workflow_state()
        cache_key = ('finetune-program', job.get('id'), job.get('status'),
                     self._workflow_projection_token(state))
        cached = self._workflow_cache_get(cache_key)
        if cached is not None:
            return cached
        result = self._workflow_finetune_program_uncached(job)
        self._workflow_cache_put(cache_key, result)
        return result

    def _workflow_finetune_program_uncached(self, job):
        """Project native arms through the original exact per-point evidence vocabulary."""
        recovery = Path(__file__).resolve().parent
        if str(recovery) not in sys.path:
            sys.path.insert(0, str(recovery))
        import strategy_finetune
        import strategy_probe

        pairs = list(job.get('seedPairs') or [])
        arms = list(job.get('arms') or [])
        parent_arm = next((arm for arm in arms if arm.get('role') == 'parent'), {})
        parent_rows = self._workflow_evidence_runs(parent_arm.get('candidateId'), pairs)
        reference = strategy_probe.chest_series(parent_rows)
        minimum = strategy_finetune.MIN_POINT_SEEDS
        points = []
        cells = []
        for arm in arms:
            if arm.get('role') != 'proposal':
                continue
            rows = self._workflow_evidence_runs(arm.get('candidateId'), pairs)
            requested_value = arm.get('requestedEffective', arm.get('effective'))
            native_row = arm.get('nativeRow')
            if requested_value is None and isinstance(native_row, dict):
                requested_value = native_row.get('requested', native_row.get('value', native_row.get('targetValue')))
            effective_value = arm.get('effective')
            if job.get('axis2'):
                if not isinstance(requested_value, dict):
                    requested_value = {}
                if not isinstance(effective_value, dict):
                    effective_value = {}
                value = requested_value.get('value')
                value2 = requested_value.get('value2')
                effective = effective_value.get('value')
                effective2 = effective_value.get('value2')
            else:
                value = requested_value
                value2 = None
                effective = effective_value
                effective2 = None
            evidence = strategy_finetune.point_evidence(
                rows, reference, value=value, candidate_id=arm.get('candidateId'),
                effective=effective, bank=len(pairs), minimum=minimum)
            projected = dict(evidence)
            if job.get('axis2'):
                projected.update(value2=value2, effective2=effective2)
                cells.append(projected)
            else:
                projected['reason'] = None
                points.append(projected)

        axis = job.get('axis')
        axis_info = strategy_probe.AXES.get(axis, {}) if axis else {}
        target_values = list(job.get('targets') or [])
        if not target_values:
            projected_rows = cells if job.get('axis2') else points
            target_values = sorted({point['value'] for point in projected_rows
                                    if isinstance(point.get('value'), int) and not isinstance(point.get('value'), bool)})
        target_values2 = list(job.get('targets2') or [])
        if not target_values2 and job.get('axis2'):
            target_values2 = sorted({cell['value2'] for cell in cells
                                     if isinstance(cell.get('value2'), int) and not isinstance(cell.get('value2'), bool)})
        baseline = job.get('effectiveBaseline')
        direction = 'both'
        if target_values and isinstance(baseline, int):
            if all(value < baseline for value in target_values):
                direction = 'down'
            elif all(value > baseline for value in target_values):
                direction = 'up'
        focused = int(job.get('encounterId', -1)) in set(getattr(self, '_focus', []) or [])
        raw_intent = ((self._workflow_state()[0].get('candidates') or {}).get(
            parent_arm.get('candidateId')) or {}).get('intent') or {}
        input_value = None
        if axis_info.get('parameter') is not None:
            unit_row = next((row for row in (raw_intent.get('ownUnits') or [])
                             if row.get('name') == job.get('unit')), None)
            parameters = (unit_row or {}).get('parameters') or {}
            parameter = parameters.get(str(axis_info['parameter']), parameters.get(axis_info['parameter']))
            if isinstance(parameter, dict):
                input_value = parameter.get('rawValue')
        active_status = job.get('status') in ('queued', 'running', 'stopping')
        status = 'active' if active_status else ('done' if job.get('status') == 'complete' else 'blocked')
        return dict(
            id=job.get('id'), mode='fine-tune', status=status, auto=False, portfolio=False,
            axis=axis, axisLabel=axis_info.get('label'), axis2=job.get('axis2'), unit=job.get('unit'),
            encounterId=job.get('encounterId'),
            parent=dict(candidateId=job.get('candidateId'), label=job.get('label')),
            baseline=dict(effective=baseline, input=input_value), direction=direction, step=None,
            targets=target_values,
            budget=dict(pointBank=job.get('requestedPerBuild'), minSeeds=minimum,
                        maxChildren=max(0, len(arms)-1)),
            used=dict(children=max(0, len(arms)-1), points=len(points), interactionCells=len(cells)),
            focused=focused, dispatchable=bool(active_status and focused),
            waitingForFocus=not focused, minSeeds=minimum, boundary=None, resolved=False,
            viableLow=None, viableHigh=None,
            statement=('Each native proposal is compared with the unchanged parent on the exact shared seed bank. '
                       'Only requested and measured points are reported; untested values and gaps remain unknown.'),
            basis='Original chest-count semantics and native paired seed identities; no boundary is inferred.',
            grid=({'axis2': job.get('axis2'), 'targets2': target_values2,
                   'statement': 'Native two-axis proposals were evaluated against the unchanged parent on the same seed bank; no continuous boundary is inferred.'}
                  if job.get('axis2') else None),
            points=points, cells=cells,
        )

    def _workflow_experiment_payload(self, job):
        _, state = self._workflow_state()
        cache_key = ('experiment-payload', job.get('id'), job.get('status'),
                     self._workflow_projection_token(state))
        cached = self._workflow_cache_get(cache_key)
        if cached is not None:
            return cached
        result = self._workflow_experiment_payload_uncached(job)
        self._workflow_cache_put(cache_key, result)
        return result

    def _workflow_experiment_payload_uncached(self, job):
        arms = job.get('arms') if isinstance(job, dict) else None
        if isinstance(arms, list) and len(arms) > 1:
            seed_pairs = list(job.get('seedPairs') or [])
            wanted = {tuple(pair) for pair in seed_pairs}
            recovery = Path(__file__).resolve().parent
            if str(recovery) not in sys.path:
                sys.path.insert(0, str(recovery))
            import strategy_probe
            parent_arm = next((arm for arm in arms if arm.get('role') == 'parent'), arms[0])
            parent_rows = self._workflow_evidence_runs(parent_arm.get('candidateId'), seed_pairs)
            reference_series = strategy_probe.chest_series(parent_rows)
            comparisons = []
            for arm in arms:
                rows = (parent_rows if arm is parent_arm else
                        self._workflow_evidence_runs(arm.get('candidateId'), seed_pairs))
                summary = self._workflow_summary(rows)
                series = strategy_probe.chest_series(rows)
                paired = strategy_probe.paired_delta(series, reference_series)
                comparisons.append(dict(candidateId=arm.get('candidateId'),
                    label=arm.get('label') or arm.get('candidateId'), role=arm.get('role'),
                    parentId=arm.get('parentId'), operator=arm.get('operator'),
                    requestedEffective=arm.get('effective'), runs=len(rows),
                    effectiveBaseline=arm.get('effectiveBaseline'), axis=arm.get('axis'), unit=arm.get('unit'),
                    meanEarned=summary['meanEarned'], earnedSamples=summary['earnedSamples'],
                    wins=summary['wins'], losses=summary['losses'], unresolved=summary['noVerdict'],
                    pairedSeeds=len(set(series) & set(reference_series)), paired=paired))
            done = sum(int(item.get('runs', 0)) for item in comparisons)
            total = len(arms) * int(job.get('requestedPerBuild', len(seed_pairs)))
            result = dict(id=job['id'], mode=job.get('mode', 'native-experiment'),
                status=job.get('status', 'incomplete'), candidateId=job.get('candidateId'),
                candidateIds=[arm.get('candidateId') for arm in arms], encounterId=job['encounterId'],
                completed=done, total=total, requestedPerBuild=job.get('requestedPerBuild', len(seed_pairs)),
                createdAt=job.get('createdAt'), endedAt=job.get('endedAt'), error=job.get('error'),
                verificationOnly=self._workflow_verification_only(), seedPairs=seed_pairs,
                proposal=job.get('proposal'), rejectedProposals=job.get('rejectedProposals') or [],
                comparisons=comparisons)
            if job.get('mode') == 'fine-tune':
                result.update(self._workflow_finetune_program(job))
                ui_status = ('active' if job.get('status') in ('queued', 'running', 'stopping')
                             else ('done' if job.get('status') == 'complete' else 'blocked'))
                result.update(id=job['id'], mode='fine-tune', status=ui_status,
                              candidateId=job.get('candidateId'), candidateIds=[arm.get('candidateId') for arm in arms],
                              encounterId=job['encounterId'], completed=done, total=total,
                              requestedPerBuild=job.get('requestedPerBuild', len(seed_pairs)),
                              createdAt=job.get('createdAt'), endedAt=job.get('endedAt'), error=job.get('error'),
                              seedPairs=seed_pairs, proposal=job.get('proposal'),
                              rejectedProposals=job.get('rejectedProposals') or [], comparisons=comparisons)
            return result
        seed_pairs = list(job.get('seedPairs') or [])
        rows = self._workflow_evidence_runs(job.get('candidateId'), seed_pairs)
        summary = self._workflow_summary(rows)
        return dict(id=job['id'], mode=job.get('mode', 'evaluate'), status=job.get('status', 'incomplete'),
                    candidateId=job['candidateId'], encounterId=job['encounterId'],
                    completed=len(rows), total=job.get('requestedPerBuild', len(seed_pairs)),
                    requestedPerBuild=job.get('requestedPerBuild', len(seed_pairs)),
                    createdAt=job.get('createdAt'), endedAt=job.get('endedAt'), error=job.get('error'),
                    verificationOnly=self._workflow_verification_only(),
                    comparisons=[dict(candidateId=job['candidateId'], label=job.get('label') or
                                      job['candidateId'], runs=len(rows),
                                      meanEarned=summary['meanEarned'], earnedSamples=summary['earnedSamples'],
                                      wins=summary['wins'], losses=summary['losses'],
                                      unresolved=summary['noVerdict'], paired=None)])

    def _workflow_experiment_report(self, candidate_id=None):
        try:
            _, state = self._workflow_state()
            jobs = list(state.get('experiments') or [])
            active_id = state.get('activeExperimentId')
            reports = []
            for job in reversed(jobs):
                if candidate_id is not None and job.get('candidateId') != candidate_id \
                        and candidate_id not in (job.get('candidateIds') or []):
                    continue
                view = dict(job)
                if view.get('id') == active_id and self.status().get('state') not in ('Running', 'Saving'):
                    view['status'] = 'incomplete'
                reports.append(self._workflow_experiment_payload(view))
            current = reports[0] if reports else None
            return {'ok': True, 'experiment': current,
                    **({'batches': reports, 'batchLimit': len(reports), 'batchesTruncated': False}
                       if candidate_id is not None else {})}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_end_experiment(self):
        try:
            with self._workflow_lock():
                _, state = self._workflow_state()
                active = state.get('activeExperimentId')
                if active is None:
                    return {'ok': True, 'unchanged': True}
                job = self._workflow_find_job(state, active)
                if job:
                    job['cancelRequested'] = True
                    job['status'] = 'stopping'
                    self._workflow_update_job(state, job)
            # A native wave is not interruptible. Wait for the in-flight batch to be durably imported.
            self._workflow_chunk_event().wait()
            with self._workflow_lock():
                _, state = self._workflow_state()
                active = state.get('activeExperimentId')
                if active is not None:
                    job = self._workflow_find_job(state, active)
                    if job:
                        self._workflow_finish_job(state, job, 'ended')
            return {'ok': True}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _workflow_replay(self, *args, **kwargs):
        return {'ok': False, 'error': 'Native replay is unavailable until this engine provides a trace payload compatible with the desktop battle renderer.'}

    def _workflow_simulate_strategy_visual(self, *args, **kwargs):
        return {'ok': False, 'error': 'Native visual simulation is unavailable until this engine provides and verifies a trace payload compatible with the desktop battle renderer.'}
