"""Native strategy library isolated from legacy and ea_* evidence ledgers.

Native search, preparation, legality and simulation remain in the selected engine. The default
scope is diagnostic and preserves historical imports; an explicit production scope verifies the
engine assets and run provenance, then stores only canonical strategy_outcomes Earned as eligible
strategy evidence. This module never writes the original candidate/run/ea_* tables.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

from strategy_native_validation import (canonical_digest, normalize_real_purpose,
    purpose_operation, purposes_compatible, record_execution_mode, record_purpose, validate_engine_assets,
    verify_record_engine_identity)
from strategy_native_ui_projection import project_run

SCHEMA = 4
ALL_ENCOUNTERS = range(20)
AVERAGE_MIN_SAMPLES = 10


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def _pack(value):
    return zlib.compress(_json(value).encode('utf-8'), 6)


def _unpack(value):
    return json.loads(zlib.decompress(value).decode('utf-8'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) else None


def _wilson_interval(successes, count):
    if not count:
        return [0.0, 1.0]
    z = 1.96
    p = successes / count
    center = (p + z*z/(2*count)) / (1 + z*z/count)
    half = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / (1 + z*z/count)
    return [max(0.0, center-half), min(1.0, center+half)]


def _intent(record, production=False):
    # Preserve the exact object supplied by native code.  No normalization or clamping is allowed.
    if production and isinstance(record.get('trialIntent'), dict):
        value = record['trialIntent']
    else:
        value = record.get('rawScenario')
        if value is None:
            value = record.get('intent')
        if value is None:
            value = record.get('scenario')
    if not isinstance(value, dict):
        raise ValueError('native record requires an exact intent/scenario object')
    if not isinstance(value.get('encounterId'), int) or isinstance(value.get('encounterId'), bool):
        raise ValueError('native intent requires an integer encounterId')
    if value['encounterId'] < 0:
        raise ValueError('native intent encounterId must not be negative')
    return value


def _native_flags(record):
    """Preserve the original flag payload, including flags unknown to this reader."""
    found = {}
    for key in ('diagnostic', 'verificationOnly', 'verified', 'strategyEvidence',
                'importDisabled', 'websiteReady', 'onVerdict', 'finishPolicy',
                'finishDiagnostic', 'executionMode', 'testPurpose', 'purpose',
                'measurementEligible', 'scoreEligible', 'productionValidationAccepted',
                'earnedBasis', 'runSpecSha256', 'configSha256', 'inputSetSha256',
                'executionMode', 'purpose', 'seedPair', 'trialIntent'):
        if key in record:
            found[key] = record[key]
    for key in ('nativeFlags', 'diagnosticFlags', 'flags'):
        if key in record:
            found[key] = record[key]
    return found


def _diagnostic(record, intent, flags):
    # Keep the historical diagnostic-scope classifier. Production records use the separate
    # strategy_outcomes path, where finishDiagnostic does not itself invalidate Earned.
    if record.get('diagnostic') is True:
        return True
    if record.get('diagnosticFlags'):
        return True
    result = record.get('result') if isinstance(record.get('result'), dict) else {}
    if result.get('diagnostic') is True or result.get('importDisabled') is True:
        return True
    for key in ('nativeFlags', 'flags'):
        nested = record.get(key)
        if isinstance(nested, dict) and any(
                value is True for name, value in nested.items()
                if 'diagnostic' in name.lower() or name.lower() in ('invalid', 'synthetic')):
            return True
        if isinstance(nested, (list, tuple, set)) and any(
                isinstance(value, str) and any(tag in value.lower()
                    for tag in ('diagnostic', 'synthetic', 'invalid')) for value in nested):
            return True
        if isinstance(nested, str) and any(tag in nested.lower()
                for tag in ('diagnostic', 'synthetic', 'invalid')):
            return True
    policy = record.get('finishPolicy', intent.get('finishPolicy'))
    if isinstance(policy, str) and policy.lower().replace('_', '-') == 'on-verdict':
        return True
    # A record explicitly marked failed/invalid is retained as raw history but not measured.
    return record.get('ok') is False or record.get('validMeasurement') is False


def _resolved(record):
    if record.get('resolved') is True:
        return True
    return record.get('verdict') in (1, 2) and not record.get('censored', False)


def _seed_pair(value):
    if isinstance(value, dict):
        aliases = (('mathSeed', 'math_seed', 'math'), ('libSeed', 'lib_seed', 'lib'))
        found = [next((value.get(key) for key in keys if key in value), None) for keys in aliases]
        value = found
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
            not isinstance(x, int) or isinstance(x, bool) for x in value):
        return None
    return [int(value[0]), int(value[1])]


def _seeds(record, intent, language=None, production=False):
    lang = str(language or '').lower()
    choices = []
    if lang.startswith('rust'):
        report = record.get('report') if isinstance(record.get('report'), dict) else {}
        nested = report.get('report') if isinstance(report.get('report'), dict) else {}
        choices.extend(((report.get('mathSeed'), report.get('libSeed')),
                        (nested.get('mathSeed'), nested.get('libSeed'))))
    if lang.startswith('cpp'):
        choices.append(record.get('seedPair'))
    if lang.startswith('go'):
        result = record.get('result') if isinstance(record.get('result'), dict) else {}
        choices.extend((record.get('seeds'), result.get('seeds')))
    choices.extend((record.get('seeds'), record.get('seedPair'), record.get('seedValues')))
    choices.append((record.get('mathSeed', intent.get('mathSeed')),
                    record.get('libSeed', intent.get('libSeed'))))
    if isinstance(intent.get('seeds'), (dict, list, tuple)):
        choices.append(intent['seeds'])
    if isinstance(intent.get('seedPair'), (dict, list, tuple)):
        choices.append(intent['seedPair'])
    valid = [_seed_pair(value) for value in choices]
    valid = [value for value in valid if value is not None]
    if not valid:
        return None
    if any(value != valid[0] for value in valid[1:]):
        raise ValueError('native record contains conflicting executed seed pairs')
    return valid[0]


def _genotype(intent):
    """Stable strategy identity excludes only the two RNG seeds."""
    value = dict(intent)
    value.pop('mathSeed', None)
    value.pop('libSeed', None)
    value.pop('seedPair', None)
    value.pop('seeds', None)
    return value


def _extract_native(language, record, production=False):
    """Adapt saved Rust battles, Go results and C++ results without rerunning a battle."""
    intent = _intent(record, production=production)
    lang = str(language).lower()
    result = record.get('result') if isinstance(record.get('result'), dict) else {}
    report = record.get('report') if isinstance(record.get('report'), dict) else {}
    if lang.startswith('rust') or record.get('recordKind') == 'battle':
        resolved = (record.get('outcomeClassification') == 'resolved'
                    and report.get('completed') is True and report.get('status') == 0)
        earned = _number(report.get('earned')) if resolved else None
    elif lang.startswith('go') or record.get('schema') == 'ka-go-standalone-result-1':
        resolved = result.get('completed') is True and result.get('earnedValid') is True
        earned = _number(result.get('earned')) if resolved else None
    elif lang.startswith('cpp') or record.get('schema') == 'kaopt-result-1':
        resolved = result.get('ok') is True and record.get('scoreEligible') is True
        earned = _number(record.get('earned', result.get('earned'))) if resolved else None
    else:
        resolved = record.get('resolved') is True
        earned = _number(record.get('earned')) if resolved else None
    flags = _native_flags(record)
    diagnostic = _diagnostic(record, intent, flags)
    if diagnostic:
        earned = None
    return dict(intent=intent, seeds=_seeds(record, intent, language, production=production), earned=earned,
                higherPotential=None, resolved=bool(resolved), diagnostic=diagnostic,
                flags=flags, genotype=_genotype(intent))


def _excluded_record_parts(language, record):
    """Retain diagnostic/test rows as raw history without assigning strategy measurements."""
    intent = _intent(record, production=True)
    purpose = record_purpose(str(language).lower(), record)
    flags = _native_flags(record)
    return dict(intent=intent, seeds=_seeds(record, intent, language, production=True),
        earned=None, higherPotential=None, resolved=False, diagnostic=True, flags=flags,
        genotype=_genotype(intent), earned_eligible=False, earned_basis=None,
        source_earned_basis=None, finish_diagnostic=bool(record.get('finishDiagnostic')),
        purpose=(str(purpose) if purpose is not None else 'diagnostic'), verdict=None,
        outcome=None, engine_identity=None)


_DIAGNOSTIC_PURPOSES = {
    'diagnostic', 'diagnostic-test', 'user-native-desktop-diagnostic',
    'verification', 'verification-only', 'test', 'synthetic', 'invalid',
}
_BASIS_ALIASES = {
    'native-loss-gate': 'native-win-loss-gate',
    'loss-gate': 'native-win-loss-gate',
    'certified-award': 'reward-entitlement-certificate',
}
_QUEUED_BASES = {'queued-at-victory', 'queued-at-victory-callback-fallback'}


def _as_maps(*values):
    return [value for value in values if isinstance(value, dict)]


def _pick(maps, *keys):
    for block in maps:
        for key in keys:
            if key in block and block[key] is not None:
                return block[key]
    return None


def _truth(value):
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


_VOLATILE_REPORT_KEYS = {
    'duration', 'durationms', 'elapsed', 'elapsedms', 'elapsedus', 'walltime',
    'startedat', 'completedat', 'timestamp', 'rundirectory', 'outputpath',
    'logpath', 'processid', 'pid', 'workerid', 'sequence', 'ordinal',
}
_CHECKSUM_KEYS = (
    'snapshotSha256', 'snapshotChecksumBeforeRun', 'snapshotChecksumBeforeFollowerSkip',
    'checksumAfter', 'preparedStateSha256', 'preparedSnapshotSha256',
    'reportSha256', 'rawReportSha256', 'reportChecksum', 'checksum',
)


def _stable_report_value(value):
    if isinstance(value, dict):
        stable = {}
        for key, child in value.items():
            normalized = ''.join(char.lower() for char in str(key) if char.isalnum())
            if normalized in _VOLATILE_REPORT_KEYS or any(
                    marker in normalized for marker in ('duration', 'elapsed', 'walltime', 'timestamp')):
                continue
            stable[key] = _stable_report_value(child)
        return stable
    if isinstance(value, (list, tuple)):
        return [_stable_report_value(child) for child in value]
    return value


def _semantic_record_hash(language, record, normalized):
    """Fingerprint battle meaning, ABI facts and stable checksums, not wrapper timing or paths."""
    lang = str(language).lower()
    outer = record.get('report') if isinstance(record.get('report'), dict) else {}
    native = outer if lang == 'rust' else (
        record.get('result') if isinstance(record.get('result'), dict) else {})
    report = native.get('report') if isinstance(native.get('report'), dict) else {}
    fields = report.get('fields') if isinstance(report.get('fields'), dict) else report
    if lang == 'rust' and isinstance(outer.get('report'), dict):
        fields = outer['report']
    encounter_report = native.get('encounterReport')
    if isinstance(encounter_report, dict):
        encounter_report = encounter_report.get('raw', encounter_report)
    checksums = {}
    for block_name, block in (('record', record), ('result', native), ('report', report)):
        if isinstance(block, dict):
            for key in _CHECKSUM_KEYS:
                if block.get(key) is not None:
                    checksums[f'{block_name}.{key}'] = block[key]
    source_claim = _pick([native, outer, record],
                         'awardedChests', 'awardedChestCount', 'earned')
    candidate_scenario = record.get('candidateScenario')
    if candidate_scenario is None:
        candidate_scenario = record.get('candidate')
    prepared = record.get('prepared')
    prepared_hash = (_digest(_stable_report_value(prepared)) if isinstance(prepared, (dict, list)) else None)
    meaning = dict(
        genotype=_digest(normalized['genotype']), seeds=normalized.get('seeds'),
        battleValid=bool(normalized.get('battle_valid')),
        resolved=bool(normalized.get('resolved')), verdict=normalized.get('verdict'),
        earned=normalized.get('earned'), earnedBasis=normalized.get('earned_basis'),
        sourceEarnedBasis=normalized.get('source_earned_basis'),
        sourceEarnedClaim=source_claim,
        candidateScenario=(_stable_report_value(candidate_scenario)
                           if isinstance(candidate_scenario, dict) else None),
        completionStatus=native.get('status'), completed=native.get('completed'),
        finishDiagnostic=bool(normalized.get('finish_diagnostic')),
        nativeReport=_stable_report_value(fields),
        encounterReport=_stable_report_value(encounter_report),
        checksums=checksums, preparedSha256=prepared_hash)
    return _digest(meaning)


def _production_record_parts(language, record, assets, run_provenance):
    """Classify one explicitly real production record through strategy_outcomes.outcome()."""
    lang = str(language).lower()
    if lang not in ('rust', 'go', 'cpp'):
        raise ValueError(f'unsupported native language: {language}')
    intent = _intent(record, production=True)
    outer_report = record.get('report') if isinstance(record.get('report'), dict) else {}
    native_result = (outer_report if lang == 'rust' else
                     record.get('result') if isinstance(record.get('result'), dict) else {})
    inner_report = native_result.get('report') if isinstance(native_result.get('report'), dict) else {}
    # Native ABIs do not all wrap the decoded fact map the same way. Go/Rust adapters
    # commonly expose ``report.fields``; the C++ adapter stores the decoded snake-case
    # facts directly in ``result.report``. Preserve that authoritative map instead of
    # silently turning a successful report into an empty one.
    report_fields = (inner_report.get('fields')
                     if isinstance(inner_report.get('fields'), dict) else inner_report)
    reward = native_result.get('rewardOutcome')
    if not isinstance(reward, dict):
        reward = native_result.get('rewardEntitlement')
    reward = reward if isinstance(reward, dict) else {}
    certificate = reward.get('certificate') if isinstance(reward.get('certificate'), dict) else {}
    fact_maps = _as_maps(report_fields, inner_report, reward, certificate)

    if lang in ('rust', 'go'):
        completed = (native_result.get('completed') is True
                     and native_result.get('status') == 0)
    else:
        completed = (native_result.get('completed') is True or native_result.get('ok') is True)
        if 'status' in native_result and native_result.get('status') not in (0, 'ok', 'completed'):
            completed = False

    # Verdict and certificate facts must come from the engine report/ABI, never the wrapper's
    # convenience verdict or inferred flags.
    verdict = _pick(fact_maps, 'verdict', 'winner')
    if isinstance(verdict, bool) or not isinstance(verdict, int):
        verdict = None
    # A derived rewardOutcome may label its unusable award claim
    # ``unknown-win-without-certificate``. Prefer the native result's own basis so the
    # canonical reader can take its separately labeled on-verdict pending-count path.
    basis = _pick([native_result, outer_report, record, reward],
                  'awardedBasis', 'earnedBasis', 'awardedChestCountBasis')
    pending = _pick([reward] + fact_maps, 'pendingChests', 'pendingChestCount',
                    'pending_chests', 'pending_final', 'pendingFinal',
                    'certificate_pending', 'certificatePending')
    earned_claim = _pick([reward, native_result, outer_report, record],
                         'awardedChests', 'awardedChestCount', 'earned')
    if _number(earned_claim) is None:
        earned_claim = None
    finish_policy = _pick([intent, record,
        record.get('searchPolicy') if isinstance(record.get('searchPolicy'), dict) else {},
        (record.get('scope', {}).get('policy', {})
         if isinstance(record.get('scope'), dict) else {}),
        run_provenance.get('runSpec', {}) if isinstance(run_provenance.get('runSpec'), dict) else {}],
        'finishPolicy', 'finish_policy')

    held = _pick([certificate] + fact_maps, 'holds', 'certificate_held',
                 'certificateHeld', 'rewardCertified')
    scope_allowed = _pick(fact_maps + [native_result], 'scope_allowed', 'scopeAllowed', 'rewardValid')
    post_delta = _pick(fact_maps, 'post_certificate_delta', 'postCertificateDelta')
    cert_frame = _pick([certificate] + fact_maps, 'frame', 'certificate_frame', 'certificateFrame')
    verdict_tick = _pick(fact_maps, 'verdict_tick', 'verdictTick', 'ticks')
    issued_before = _pick([certificate] + fact_maps, 'issuedBeforeVerdict', 'issued_before_verdict')
    cert_pending = _pick(fact_maps, 'certificate_pending', 'certificatePending')
    zero_delta = isinstance(post_delta, (int, float)) and not isinstance(post_delta, bool) \
        and post_delta == 0
    certificate_valid = _truth(held) and zero_delta and (scope_allowed is None or _truth(scope_allowed))
    if issued_before is not None:
        certificate_valid = certificate_valid and _truth(issued_before)
    else:
        certificate_valid = (certificate_valid and isinstance(cert_frame, int)
            and not isinstance(cert_frame, bool) and isinstance(verdict_tick, int)
            and not isinstance(verdict_tick, bool) and 0 <= cert_frame < verdict_tick)
    if cert_pending is not None and earned_claim is not None:
        certificate_valid = certificate_valid and cert_pending == earned_claim

    censored = _pick(fact_maps, 'censored') is True
    error = _pick([record, native_result, inner_report],
                  'error', 'errorMessage', 'error_message', 'exception', 'traceback')
    if not completed and error is None:
        error = 'native record is not a completed successful execution'

    canonical_basis = _BASIS_ALIASES.get(basis, basis)
    reward_block = dict(pendingChests=pending)
    if canonical_basis == 'reward-entitlement-certificate':
        if certificate_valid:
            reward_block.update(awardedChests=earned_claim, awardedBasis=canonical_basis)
    elif canonical_basis == 'native-win-dispatch-gate':
        reward_block.update(awardedChests=earned_claim, awardedBasis=canonical_basis)
    elif canonical_basis == 'native-win-loss-gate':
        reward_block.update(awardedChests=earned_claim, awardedBasis=canonical_basis)
    elif basis not in _QUEUED_BASES and earned_claim is not None:
        # Keep unknown source bases intact so the canonical reader refuses an arbitrary win basis.
        reward_block.update(awardedChests=earned_claim, awardedBasis=basis)
    # queued-at-victory is only a reported callback path. The canonical reader may yield its
    # separately labeled terminal-policy-dispatch when the declared policy and pending count allow.

    seeds = _seeds(record, intent, lang, production=True)
    adapted = dict(verdict=verdict, censored=censored, seeds=seeds,
                   finishPolicy=finish_policy, rewardOutcome=reward_block)
    if error:
        adapted['error'] = error
    from strategy_outcomes import outcome as strategy_outcome
    canonical = strategy_outcome(adapted, policy={'finishPolicy': finish_policy})

    mode = record_execution_mode(lang, record)
    if str(mode).lower() != 'production':
        raise ValueError('native record does not declare executionMode production')
    source_purpose = record_purpose(lang, record)
    record_purpose_class = normalize_real_purpose(source_purpose)
    # Go's explicit testPurpose=production identifies the mode; the active run's purpose supplies
    # the strategy/search/evaluate operation. Native C++ may use umbrella purpose `strategy`.
    purpose_matches = purposes_compatible(source_purpose, run_provenance.get('purpose'), lang)
    if not purpose_matches:
        raw_purpose = (str(source_purpose).strip().lower().replace('_', '-').replace(' ', '-')
                       if isinstance(source_purpose, str) else None)
        if raw_purpose not in _DIAGNOSTIC_PURPOSES:
            raise ValueError('record purpose must explicitly identify real strategy/search/evaluate work')
        purpose_class = 'diagnostic'
    else:
        purpose_class = 'production'

    run_mode = str(run_provenance.get('executionMode', 'production')).lower()
    if run_mode != 'production':
        raise ValueError('active per-run provenance is not production mode')
    expected_encounter = run_provenance.get('encounterId')
    expected_encounters = run_provenance.get('encounterIds')
    if intent['encounterId'] >= 20:
        raise ValueError('production encounterId must be in the native campaign range 0..19')
    if expected_encounter is not None and expected_encounter != intent['encounterId']:
        raise ValueError('native intent encounter does not match active run provenance')
    if expected_encounters is not None and intent['encounterId'] not in expected_encounters:
        raise ValueError('native intent encounter is outside active portfolio provenance')
    expected_pairs = run_provenance.get('seedPairs')
    encounter_banks = run_provenance.get('admittedSeedPairsByEncounter')
    if isinstance(encounter_banks, dict):
        expected_pairs = encounter_banks.get(str(intent['encounterId']), expected_pairs)
    run_spec = run_provenance.get('runSpec') if isinstance(run_provenance.get('runSpec'), dict) else {}
    generated_go_search = (lang == 'go' and run_spec.get('mode') == 'search'
                           and run_spec.get('seedApplication') == 'native-search-rng-from-searchSeed')
    if isinstance(expected_pairs, (list, tuple)) and expected_pairs and not generated_go_search:
        normalized_pairs = [_seed_pair(value) for value in expected_pairs]
        normalized_pairs = [value for value in normalized_pairs if value is not None]
        if normalized_pairs and seeds not in normalized_pairs:
            raise ValueError('native executed seed pair is outside active run provenance')

    engine_identity = verify_record_engine_identity(lang, record, assets)
    explicit_exclusion = (record.get('diagnostic') is True
        or record.get('importDisabled') is True
        or native_result.get('diagnostic') is True
        or native_result.get('importDisabled') is True
        or purpose_class != 'production')
    earned = canonical.get('finalEarned') if completed else None
    eligible = bool(not explicit_exclusion and completed and canonical.get('resolved')
                    and earned is not None)
    finish_marker = record.get('finishDiagnostic', native_result.get('finishDiagnostic'))
    finish_diagnostic = (_truth(finish_marker)
        or str(finish_policy or '').lower().replace('_', '-') == 'on-verdict')
    pending_potential = (_number(canonical.get('pending'))
                         if canonical.get('verdict') == 2 and canonical.get('resolved') else None)
    return dict(intent=intent, seeds=seeds, earned=earned, higherPotential=pending_potential,
        resolved=bool(completed and canonical.get('resolved')), battle_valid=bool(completed),
        earned_eligible=eligible, earned_basis=canonical.get('basis'),
        source_earned_basis=basis, diagnostic=bool(explicit_exclusion),
        finish_diagnostic=bool(finish_diagnostic), purpose=purpose_class,
        flags=_native_flags(record), genotype=_genotype(intent), verdict=canonical.get('verdict'),
        outcome=canonical, engine_identity=engine_identity)


def read_native_journal(path):
    """Yield (line number, parsed record, exact line SHA-256) from saved JSONL, without execution."""
    with Path(path).open('rb') as source:
        for line_number, raw in enumerate(source, 1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f'invalid saved journal JSON at line {line_number}: {exc}') from exc
            if not isinstance(record, dict):
                raise ValueError(f'saved journal line {line_number} is not an object')
            yield line_number, record, hashlib.sha256(raw.rstrip(b'\r\n')).hexdigest()


class NativeLibrary:
    """One SQLite writer connection; all durable state is namespaced to ``nui_*`` tables.

    Compatibility scope is the hash of engine + mechanics revision + policy.  The complete
    provenance is retained per raw record in ``nui_runs`` and in scope metadata for audit.
    Methods are intended to be called on the constructing (writer/UI) thread only.
    """

    def __init__(self, path, engine, provenance):
        if not isinstance(engine, str) or not engine.strip():
            raise ValueError('engine identity is required')
        if not isinstance(provenance, dict):
            raise ValueError('provenance must be an object')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self.provenance = dict(provenance)
        requested_mode = self.provenance.get('executionMode', 'diagnostic')
        if requested_mode not in ('diagnostic', 'production'):
            raise ValueError('executionMode must be explicitly diagnostic or production')
        self.execution_mode = requested_mode
        self.run_provenance = {}
        self.engine_assets = None
        if self.execution_mode == 'production':
            if normalize_real_purpose(self.provenance.get('purpose')) is None:
                raise ValueError('production scope requires an explicit strategy/search/evaluate purpose')
            self.engine_assets = validate_engine_assets(engine, self.provenance.get('engineAssets'))
        run = provenance.get('run') if isinstance(provenance.get('run'), dict) else {}
        build = run.get('build') if isinstance(run.get('build'), dict) else {}
        engine_block = provenance.get('engine') if isinstance(provenance.get('engine'), dict) else {}
        scope_block = provenance.get('scope') if isinstance(provenance.get('scope'), dict) else {}
        self.mechanics = provenance.get('mechanicsSha256', provenance.get(
            'mechanicsRevision', provenance.get('mechanics_revision',
            provenance.get('mechanics', run.get('mechanics', scope_block.get('mechanics'))))))
        self.policy = provenance.get('policySha256', provenance.get('policyHash',
            provenance.get('policy', run.get('policyHash', scope_block.get('policy')))))
        self.executable = provenance.get('actualExecutableSha256', provenance.get(
            'executableSha256', provenance.get('executableIdentity', build.get('executableSHA256'))))
        self.kernel = provenance.get('currentKernelSha256', provenance.get(
            'kernelSha256', provenance.get('engineSha256',
            engine_block.get('kernelSha256', scope_block.get('kernelSha256')))))
        self.scope_identity = dict(engine=engine, mechanics=self.mechanics, policy=self.policy,
                                   executable=self.executable, kernel=self.kernel)
        if self.execution_mode == 'production':
            self.scope_identity.update(executionMode='production',
                                       engineAssetsSha256=self.engine_assets['contentSha256'])
        self.scope = _digest(self.scope_identity)
        self._thread = threading.get_ident()
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS nui_meta(
                scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(scope,key));
            CREATE TABLE IF NOT EXISTS nui_candidate(
                scope TEXT NOT NULL, candidate_id TEXT NOT NULL, intent_zlib BLOB NOT NULL,
                intent_sha256 TEXT NOT NULL, label TEXT NOT NULL, parent_id TEXT,
                language TEXT NOT NULL, source_identity TEXT, created_at REAL NOT NULL,
                PRIMARY KEY(scope,candidate_id));
            CREATE TABLE IF NOT EXISTS nui_observation(
                scope TEXT NOT NULL, observation_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                dedup_key TEXT NOT NULL, encounter_id INTEGER NOT NULL, defeat_count INTEGER NOT NULL,
                seed_a INTEGER, seed_b INTEGER, earned REAL, higher_potential REAL,
                verdict INTEGER, resolved INTEGER NOT NULL, diagnostic INTEGER NOT NULL,
                native_flags TEXT NOT NULL, raw_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
                earned_eligible INTEGER NOT NULL DEFAULT 0,
                execution_mode TEXT NOT NULL DEFAULT 'diagnostic',
                purpose TEXT NOT NULL DEFAULT 'diagnostic', earned_basis TEXT,
                source_earned_basis TEXT, finish_diagnostic INTEGER NOT NULL DEFAULT 0,
                engine_identity_zlib BLOB, genotype_sha256 TEXT,
                dedup_conflict INTEGER NOT NULL DEFAULT 0, battle_valid INTEGER NOT NULL DEFAULT 0,
                semantic_sha256 TEXT,
                UNIQUE(scope,dedup_key), PRIMARY KEY(scope,observation_id));
            CREATE INDEX IF NOT EXISTS nui_obs_encounter
                ON nui_observation(scope,encounter_id,defeat_count,candidate_id);
            CREATE TABLE IF NOT EXISTS nui_runs(
                scope TEXT NOT NULL, journal_id TEXT NOT NULL, observation_id TEXT NOT NULL,
                raw_sha256 TEXT NOT NULL, raw_zlib BLOB NOT NULL,
                provenance_zlib BLOB NOT NULL, created_at REAL NOT NULL,
                UNIQUE(scope,raw_sha256), PRIMARY KEY(scope,journal_id));
            CREATE INDEX IF NOT EXISTS nui_runs_observation
                ON nui_runs(scope,observation_id,created_at);
        ''')
        existing_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(nui_observation)')}
        additive = {
            'earned_eligible': 'INTEGER NOT NULL DEFAULT 0',
            'execution_mode': "TEXT NOT NULL DEFAULT 'diagnostic'",
            'purpose': "TEXT NOT NULL DEFAULT 'diagnostic'",
            'earned_basis': 'TEXT',
            'source_earned_basis': 'TEXT',
            'finish_diagnostic': 'INTEGER NOT NULL DEFAULT 0',
            'engine_identity_zlib': 'BLOB',
            'genotype_sha256': 'TEXT',
            'dedup_conflict': 'INTEGER NOT NULL DEFAULT 0',
            'battle_valid': 'INTEGER NOT NULL DEFAULT 0',
            'semantic_sha256': 'TEXT',
        }
        for column, declaration in additive.items():
            if column not in existing_columns:
                self.db.execute(f'ALTER TABLE nui_observation ADD COLUMN {column} {declaration}')
        with self.db:
            self._meta_set('schema', SCHEMA)
            self._meta_set('engine', engine)
            self._meta_set('compatibility', self.scope_identity)
            self._meta_set('provenance', self.provenance)
            self._meta_set('verificationOnly', self.execution_mode != 'production')
            self._meta_set('totalRuns', int(self.get('totalRuns', 0)))
            self._meta_set('focusEncounter', self.get('focusEncounter'))
            self._meta_set('candidateCursor', self.get('candidateCursor', 0))
            self._meta_set('session', self.get('session', {}))

    def set_run_provenance(self, extra):
        """Bind the next imported rows to the controller's active real run context."""
        self._writer()
        if not isinstance(extra, dict):
            raise ValueError('per-run provenance must be an object')
        if self.execution_mode == 'production':
            if str(extra.get('executionMode', '')).lower() != 'production':
                raise ValueError('production run provenance requires executionMode production')
            if purpose_operation(extra.get('purpose')) not in ('strategy', 'search', 'evaluate'):
                raise ValueError('production run provenance requires a strategy/search/evaluate purpose')
            for key in ('runSpecSha256', 'configSha256'):
                value = extra.get(key)
                if not isinstance(value, str) or len(value) != 64 or any(
                        char not in '0123456789abcdefABCDEF' for char in value):
                    raise ValueError(f'production run provenance requires {key}')
            input_set = extra.get('inputSetSha256')
            if input_set is not None and (not isinstance(input_set, str) or len(input_set) != 64
                    or any(char not in '0123456789abcdefABCDEF' for char in input_set)):
                raise ValueError('inputSetSha256 must be a SHA-256 digest when supplied')
            encounter_ids = extra.get('encounterIds')
            if encounter_ids is not None:
                if (not isinstance(encounter_ids, list) or not encounter_ids or
                        len(set(encounter_ids)) != len(encounter_ids) or any(
                            isinstance(e, bool) or not isinstance(e, int) or not 0 <= e < 20
                            for e in encounter_ids)):
                    raise ValueError('production portfolio provenance requires unique encounterIds0..19')
            elif not isinstance(extra.get('encounterId'), int) or isinstance(extra.get('encounterId'), bool):
                raise ValueError('production run provenance requires an integer encounterId')
            if not isinstance(extra.get('seedPairs'), (list, tuple)) or not extra['seedPairs']:
                raise ValueError('production run provenance requires the configured seedPairs')
            encounter_banks = extra.get('admittedSeedPairsByEncounter')
            if encounter_banks is not None and (not isinstance(encounter_banks, dict) or any(
                    str(encounter) not in {str(e) for e in range(20)} or
                    not isinstance(pairs, list) or not pairs or any(_seed_pair(pair) is None for pair in pairs)
                    for encounter, pairs in encounter_banks.items())):
                raise ValueError('invalid per-encounter admitted native seed banks')
        self.run_provenance = dict(extra)
        self.provenance = dict(self.provenance, run=dict(extra))
        with self.db:
            self._meta_set('provenance', self.provenance)

    def _writer(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('NativeLibrary writes must use its owning writer thread')

    @contextmanager
    def _record_transaction(self):
        if getattr(self, '_batch_import_active', False):
            yield
        else:
            with self.db:
                yield

    def import_records(self, language, records):
        """Commit a bounded batch atomically with identical validation/dedup rules."""
        self._writer()
        if not isinstance(records, list) or not 1 <= len(records) <= 128:
            raise ValueError('native import batch must contain1..128 records')
        if getattr(self, '_batch_import_active', False):
            raise RuntimeError('nested native import batches are unsupported')
        try:
            with self.db:
                self._batch_import_active = True
                return [self.import_record(language, record) for record in records]
        finally:
            self._batch_import_active = False

    def _meta_set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO nui_meta(scope,key,value) VALUES(?,?,?)',
                        (self.scope, key, _json(value)))

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM nui_meta WHERE scope=? AND key=?',
                              (self.scope, key)).fetchone()
        return default if row is None else json.loads(row['value'])

    def set(self, key, value):
        self._writer()
        with self.db:
            self._meta_set(key, value)

    def set_focus(self, encounter_id):
        if encounter_id is not None and (not isinstance(encounter_id, int) or not 0 <= encounter_id < 20):
            raise ValueError('focus encounter must be 0..19 or null')
        self.set('focusEncounter', encounter_id)

    def set_candidate_cursor(self, cursor):
        if not isinstance(cursor, int) or cursor < 0:
            raise ValueError('candidate cursor must be a nonnegative integer')
        self.set('candidateCursor', cursor)

    def set_session(self, value):
        if not isinstance(value, dict):
            raise ValueError('session state must be an object')
        self.set('session', value)

    def import_candidate(self, raw, label='Native strategy'):
        """Persist an exact native intent; actual legality remains a native-preparation decision."""
        self._writer()
        intent = _intent(raw) if isinstance(raw, dict) and any(
            key in raw for key in ('intent', 'scenario', 'rawScenario')) else raw
        if (not isinstance(intent, dict) or not isinstance(intent.get('encounterId'), int)
                or isinstance(intent.get('encounterId'), bool)):
            raise ValueError('candidate requires an exact intent with integer encounterId')
        intent_hash = _digest(intent)
        candidate_id = _digest(_genotype(intent))
        parent = raw.get('parentCandidateSha256', raw.get('parentCandidateId')) if isinstance(raw, dict) else None
        lang = str(raw.get('language', self.engine)) if isinstance(raw, dict) else self.engine
        source = raw.get('inputSourceCandidateId') if isinstance(raw, dict) else None
        with self._record_transaction():
            self.db.execute('''INSERT OR IGNORE INTO nui_candidate
                (scope,candidate_id,intent_zlib,intent_sha256,label,parent_id,language,source_identity,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (self.scope, candidate_id, _pack(intent), intent_hash, str(label)[:160], parent,
                 lang, source, time.time()))
        return candidate_id

    def import_record(self, language, record):
        """Append an exact native attempt and raw journal/provenance idempotently.

        Diagnostic scopes retain legacy raw rows without making measurements. Production scopes
        bind each row to explicit strategy purpose, exact seeds and validated engine identity; only
        canonical strategy_outcomes results feed measured read models.
        """
        self._writer()
        if not isinstance(record, dict):
            raise ValueError('native record must be an object')
        lang = str(language).lower()
        if self.execution_mode == 'production':
            raw_mode = record_execution_mode(lang, record)
            raw_purpose = record_purpose(lang, record)
            raw_purpose_key = (str(raw_purpose).strip().lower().replace('_', '-').replace(' ', '-')
                               if isinstance(raw_purpose, str) else None)
            result = record.get('result') if isinstance(record.get('result'), dict) else {}
            explicitly_excluded = (str(raw_mode).lower() != 'production'
                or raw_purpose_key in _DIAGNOSTIC_PURPOSES
                or record.get('diagnostic') is True
                or record.get('importDisabled') is True
                or result.get('diagnostic') is True
                or result.get('importDisabled') is True)
            if explicitly_excluded:
                normalized = _excluded_record_parts(lang, record)
            else:
                normalized = _production_record_parts(lang, record, self.engine_assets,
                                                       self.run_provenance)
        else:
            normalized = _extract_native(lang, record)
            # Legacy scopes remain diagnostic by default; prior rows and scope IDs are untouched.
            normalized['earned_eligible'] = False
            normalized['earned_basis'] = None
            normalized['source_earned_basis'] = None
            normalized['finish_diagnostic'] = bool(record.get('finishDiagnostic'))
            normalized['purpose'] = str(record_purpose(lang, record) or 'diagnostic')
            normalized['engine_identity'] = None
        intent = normalized['intent']
        cid = self.import_candidate(intent, str(record.get('label') or language))
        lineage = record.get('lineage') if isinstance(record.get('lineage'), dict) else {}
        parent_id = record.get('parentCandidateSha256', record.get('parentCandidateId',
                    lineage.get('parentId')))
        source_id = record.get('candidateSha256', record.get('identityHash',
                    record.get('strategyId', record.get('candidateId', record.get('inputSourceCandidateId')))))
        with self._record_transaction():
            self.db.execute('UPDATE nui_candidate SET parent_id=COALESCE(parent_id,?), '
                'source_identity=COALESCE(source_identity,?), language=? '
                'WHERE scope=? AND candidate_id=?',
                (parent_id, source_id, str(language), self.scope, cid))
        seeds = normalized['seeds']
        flags = normalized['flags']
        diagnostic = normalized['diagnostic']
        earned, higher, resolved = normalized['earned'], None, normalized['resolved']
        raw_hash = _digest(record)
        # Deduplicate by exact seedless strategy genotype and executed pair. Diagnostic records
        # without actual seeds retain a stable raw-record identity instead of collapsing together.
        genotype_hash = _digest(normalized['genotype'])
        semantic_hash = (_semantic_record_hash(lang, record, normalized)
                         if self.execution_mode == 'production'
                         and normalized.get('purpose') == 'production' else raw_hash)
        seed_identity = seeds if seeds is not None else {'rawSha256': raw_hash}
        dedup = _digest(dict(genotype=genotype_hash, seeds=seed_identity))
        oid = _digest(dict(scope=self.scope, dedup=dedup))
        if (self.execution_mode == 'production' and normalized.get('earned_eligible')
                and seeds is None):
            raise ValueError('production strategy record lacks its actual executed seed pair')
        raw = dict(nativeRecord=record, exactIntent=intent, nativeFlags=flags,
                   verificationOnly=self.execution_mode != 'production',
                   canonicalOutcome=normalized.get('outcome'),
                   engineIdentity=normalized.get('engine_identity'))
        defeat = intent.get('defeatCount', 0)
        if (self.execution_mode == 'production'
                and (not isinstance(defeat, int) or isinstance(defeat, bool) or defeat < 0)):
            raise ValueError('production intent defeatCount must be a nonnegative integer')
        if not isinstance(defeat, int) or isinstance(defeat, bool) or defeat < 0:
            defeat = 0
        now = time.time()
        with self._record_transaction():
            inserted = self.db.execute('''INSERT OR IGNORE INTO nui_observation
                (scope,observation_id,candidate_id,dedup_key,encounter_id,defeat_count,seed_a,seed_b,
                 earned,higher_potential,verdict,resolved,diagnostic,native_flags,raw_sha256,created_at,
                 earned_eligible,execution_mode,purpose,earned_basis,source_earned_basis,
                 finish_diagnostic,engine_identity_zlib,genotype_sha256,dedup_conflict,battle_valid,
                 semantic_sha256)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (self.scope, oid, cid, dedup, intent['encounterId'], defeat,
                 seeds[0] if seeds else None, seeds[1] if seeds else None, earned, higher,
                 normalized.get('verdict'), int(resolved), int(diagnostic), _json(flags), raw_hash, now,
                 int(bool(normalized.get('earned_eligible'))), self.execution_mode,
                 str(normalized.get('purpose') or 'diagnostic'), normalized.get('earned_basis'),
                 normalized.get('source_earned_basis'), int(bool(normalized.get('finish_diagnostic'))),
                 _pack(normalized['engine_identity']) if normalized.get('engine_identity') else None,
                 genotype_hash, 0, int(bool(normalized.get('battle_valid'))), semantic_hash)).rowcount
            conflict = False
            if not inserted:
                existing = self.db.execute('SELECT raw_sha256,semantic_sha256 FROM nui_observation '
                    'WHERE scope=? AND dedup_key=?', (self.scope, dedup)).fetchone()
                if existing and existing['semantic_sha256'] is None:
                    self.db.execute('UPDATE nui_observation SET semantic_sha256=? '
                        'WHERE scope=? AND dedup_key=?', (semantic_hash, self.scope, dedup))
                conflict = bool(existing and existing['semantic_sha256'] is not None
                                and existing['semantic_sha256'] != semantic_hash)
                if conflict:
                    self.db.execute('UPDATE nui_observation SET dedup_conflict=1,earned_eligible=0 '
                        'WHERE scope=? AND dedup_key=?', (self.scope, dedup))
            journal_id = _digest(dict(scope=self.scope, rawSha256=raw_hash))
            self.db.execute('''INSERT OR IGNORE INTO nui_runs
                (scope,journal_id,observation_id,raw_sha256,raw_zlib,provenance_zlib,created_at)
                VALUES(?,?,?,?,?,?,?)''',
                (self.scope, journal_id, oid, raw_hash, _pack(raw), _pack(self.provenance), now))
            if inserted:
                self._meta_set('totalRuns', int(self.get('totalRuns', 0)) + 1)
        return dict(ok=True, candidateId=cid, observationId=oid, duplicate=not bool(inserted),
                    dedupConflict=conflict, diagnostic=diagnostic,
                    executionMode=self.execution_mode,
                    verificationOnly=self.execution_mode != 'production',
                    strategyEvidence=bool(normalized.get('earned_eligible') and not conflict),
                    earned=(None if conflict else earned), earnedBasis=normalized.get('earned_basis'),
                    finishDiagnostic=bool(normalized.get('finish_diagnostic')),
                    higherPotential=higher, seeds=seeds)

    def _observations(self, encounter_id=None, defeat_count=None, candidate_id=None,
                      include_diagnostic=True):
        sql = '''SELECT o.*,c.label,c.language,c.parent_id,c.intent_zlib
                 FROM nui_observation o JOIN nui_candidate c
                   ON c.scope=o.scope AND c.candidate_id=o.candidate_id
                 WHERE o.scope=?'''
        args = [self.scope]
        for col, val in (('encounter_id', encounter_id), ('defeat_count', defeat_count),
                         ('candidate_id', candidate_id)):
            if val is not None:
                sql += f' AND o.{col}=?'
                args.append(val)
        if not include_diagnostic:
            sql += ' AND o.earned_eligible=1 AND o.resolved=1'
        sql += ' ORDER BY o.created_at,o.observation_id'
        return self.db.execute(sql, args).fetchall()

    @staticmethod
    def _summary(rows, execution_mode=None):
        measured = [r for r in rows if r['earned_eligible'] and r['resolved']
                    and not r['dedup_conflict']]
        earned = [r['earned'] for r in measured if r['earned'] is not None]
        potential = [r['higher_potential'] for r in measured if r['higher_potential'] is not None]
        verdict_rows = [r for r in measured if r['verdict'] in (1, 2)]
        basis_counts = {}
        for row in measured:
            basis = row['earned_basis'] or 'unclassified'
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
        verdict_known = len(verdict_rows) == len(measured)
        wins = sum(r['verdict'] == 1 for r in verdict_rows) if verdict_known else None
        losses = sum(r['verdict'] == 2 for r in verdict_rows) if verdict_known else None
        return dict(attempts=len(rows), samples=len(measured), wins=wins, losses=losses,
                    noVerdict=len(rows)-len(measured),
                    meanEarned=(sum(earned)/len(earned) if earned else None),
                    earnedSamples=len(earned), eaMeanEarned=None, eaBestEarned=None,
                    eaEarnedSamples=0,
                    meanPotential=(sum(potential)/len(potential) if potential else None),
                    potentialSamples=len(potential), bestEarned=max(earned) if earned else None,
                    bestPotential=max(potential) if potential else None,
                    winRate=(wins/(wins+losses) if verdict_known and wins+losses else None),
                    verdictSamples=len(verdict_rows),
                    comparable=bool(measured and len(measured) == len(rows)),
                    retainedSampleCount=len(rows), strategyEvidenceSamples=len(measured),
                    resolvedBattles=sum(bool(r['battle_valid']) for r in rows),
                    excludedRows=len(rows)-len(measured),
                    earnedBasisCounts=basis_counts,
                    finishDiagnosticSamples=sum(bool(r['finish_diagnostic']) for r in measured),
                    diagnosticRows=sum(bool(r['diagnostic']) for r in rows),
                    dedupConflicts=sum(bool(r['dedup_conflict']) for r in rows),
                    verificationOnly=(execution_mode != 'production' if execution_mode is not None
                                      else not any(r['execution_mode'] == 'production' for r in rows)))

    def _run_view(self, row):
        raw = self.db.execute('SELECT raw_zlib,provenance_zlib,raw_sha256 FROM nui_runs '
                              'WHERE scope=? AND observation_id=? '
                              'ORDER BY created_at,journal_id LIMIT 1',
                              (self.scope, row['observation_id'])).fetchone()
        envelope = _unpack(raw['raw_zlib']) if raw else {}
        record = envelope.get('nativeRecord', envelope)
        seeds = ([row['seed_a'], row['seed_b']] if row['seed_a'] is not None
                 and row['seed_b'] is not None else None)
        exact_intent = envelope.get('exactIntent')
        if not isinstance(exact_intent, dict):
            exact_intent = _unpack(row['intent_zlib'])
        exact_intent = dict(exact_intent)
        if seeds is not None:
            exact_intent['mathSeed'], exact_intent['libSeed'] = seeds
        identity = (_unpack(row['engine_identity_zlib']) if row['engine_identity_zlib'] else None)
        run_provenance = _unpack(raw['provenance_zlib']) if raw else {}
        exact_digest = _digest(dict(scope=self.scope, intent=exact_intent,
                                    runProvenance=run_provenance))
        native_record_provenance = record.get('provenance')
        provenance = dict(controllerRun=run_provenance,
                          nativeRecord=(native_record_provenance
                                        if isinstance(native_record_provenance, dict) else None),
                          engineIdentity=identity)
        view = dict(record, seeds=seeds, phase='native', ordinal=None,
                    scenario=exact_intent, digest=exact_digest,
                    nativeProvenance=provenance,
                    nativeRecordSha256=(raw['raw_sha256'] if raw else None),
                    earned=row['earned'], higherPotential=row['higher_potential'],
                    resolved=bool(row['resolved']), diagnostic=bool(row['diagnostic']),
                    nativeFlags=json.loads(row['native_flags']),
                    verificationOnly=row['execution_mode'] != 'production',
                    observationId=row['observation_id'],
                    executionMode=row['execution_mode'], purpose=row['purpose'],
                    battleValid=bool(row['battle_valid']),
                    earnedEligible=bool(row['earned_eligible']),
                    strategyEvidence=bool(row['earned_eligible'] and not row['dedup_conflict']),
                    earnedBasis=row['earned_basis'], sourceEarnedBasis=row['source_earned_basis'],
                    finishDiagnostic=bool(row['finish_diagnostic']),
                    engineIdentity=identity, dedupConflict=bool(row['dedup_conflict']))
        normalized = dict(verdict=row['verdict'], resolved=bool(row['resolved']),
            battle_valid=bool(row['battle_valid']), earned=row['earned'],
            earned_eligible=bool(row['earned_eligible']), diagnostic=bool(row['diagnostic']),
            dedup_conflict=bool(row['dedup_conflict']), execution_mode=row['execution_mode'],
            purpose=row['purpose'], earned_basis=row['earned_basis'],
            source_earned_basis=row['source_earned_basis'], higher_potential=row['higher_potential'])
        return project_run(view, normalized=normalized)

    def strategies(self, encounter_id, defeat_count=0):
        """Native rows in the shape consumed by the original encounter strategy controls."""
        rows = self._observations(encounter_id, defeat_count)
        grouped = {}
        for row in rows:
            grouped.setdefault(row['candidate_id'], []).append(row)
        strategies = []
        for cid, attempts in grouped.items():
            summary = self._summary(attempts, self.execution_mode)
            # Keep a candidate-validation summary available to the UI while marking
            # unavailable legacy metrics as unknown. Native observations persist Earned,
            # verdict and evidence eligibility; they do not persist the original callback,
            # resource, survivor or timing aggregates as first-class columns.
            trial_rows = [r for r in attempts if r['battle_valid'] and not r['diagnostic']
                          and r['purpose'] == 'production' and not r['dedup_conflict']]
            trial_wins = sum(r['verdict'] == 1 for r in trial_rows)
            trial_losses = sum(r['verdict'] == 2 for r in trial_rows)
            trial_n = len(trial_rows)
            trial_unresolved = trial_n - trial_wins - trial_losses
            earned_values = [float(r['earned']) for r in attempts
                             if r['earned_eligible'] and r['resolved']
                             and not r['dedup_conflict'] and r['earned'] is not None]
            earned_count = len(earned_values)
            earned_sd = statistics.stdev(earned_values) if earned_count > 1 else None
            win_rate = trial_wins / trial_n if trial_n else None
            win_interval = _wilson_interval(trial_wins, trial_n)
            native_summary = dict(
                n=trial_n, wins=trial_wins, losses=trial_losses, censored=trial_unresolved,
                winRate=win_rate, winInterval=win_interval,
                failureRate=trial_losses / trial_n if trial_n else None,
                unresolvedRate=trial_unresolved / trial_n if trial_n else None,
                retainedMean=None,
                callbackMean=None, callbackSD=None, callbackMin=None,
                callbackP10=None, callbackMax=None, callbackHistogram=None,
                chestMean=(sum(earned_values) / earned_count if earned_count else None),
                chestSampleCount=earned_count,
                chestMin=min(earned_values) if earned_values else None,
                chestMax=max(earned_values) if earned_values else None,
                chestSD=earned_sd,
                chestSE=(earned_sd / math.sqrt(earned_count)
                         if earned_sd is not None else None),
                meanResources=None, meanSurvivors=None, meanTicks=None,
                comparable=bool(trial_n and trial_unresolved == 0
                                and earned_count == trial_n),
                source='native', native=True,
            )
            intent = _unpack(attempts[-1]['intent_zlib'])
            strategies.append(dict(candidateId=cid, label=attempts[-1]['label'],
                source='native-' + attempts[-1]['language'], displayName=attempts[-1]['label'],
                creator=attempts[-1]['language'], change=None, parentId=attempts[-1]['parent_id'],
                parentProducer=None, nameDerived=False, nameDetail=None,
                attempts=summary['attempts'], wins=summary['wins'], losses=summary['losses'],
                noVerdict=summary['noVerdict'], meanEarned=summary['meanEarned'],
                earnedSamples=summary['earnedSamples'], meanPotential=summary['meanPotential'],
                potentialSamples=summary['potentialSamples'], bestEarned=summary['bestEarned'],
                bestPotential=summary['bestPotential'], winRate=summary['winRate'],
                comparable=summary['comparable'], retainedSampleCount=summary['retainedSampleCount'],
                eaMeanEarned=None, eaBestEarned=None, eaEarnedSamples=0,
                verificationOnly=self.execution_mode != 'production', native=True, intent=intent,
                strategyEvidence=summary['strategyEvidenceSamples'] > 0,
                strategyEvidenceSamples=summary['strategyEvidenceSamples'],
                nativeSummary=native_summary,
                diagnosticRows=summary['diagnosticRows'],
                dedupConflicts=summary['dedupConflicts'],
                nativeFlags=[json.loads(r['native_flags']) for r in attempts]))
        strategies.sort(key=lambda r: (r['meanEarned'] if r['meanEarned'] is not None else -1,
                                      r['bestEarned'] if r['bestEarned'] is not None else -1,
                                      r['candidateId']), reverse=True)
        return dict(ok=True, strategies=strategies, orphanHolders=[],
                    verificationOnly=self.execution_mode != 'production',
                    engine=self.engine, scope=self.scope)

    def detail(self, candidate_id):
        rows = self._observations(candidate_id=candidate_id)
        if not rows:
            return dict(ok=False, error='Native candidate was not found in this compatibility scope.')
        intent = _unpack(rows[-1]['intent_zlib'])
        runs = [self._run_view(row) for row in rows]
        journals = self.read_log(candidate_id)
        summary = self._summary(rows, self.execution_mode)
        return dict(ok=True, candidateId=candidate_id, label=rows[-1]['label'], scenario=intent,
                    holder=None, resident=True, storedRuns=runs, trialRuns=[],
                    storedSummary=summary, trialSummary=self._summary([], self.execution_mode),
                    encounterLedger=None, outcomeDistribution=None, formation=None,
                    verificationOnly=self.execution_mode != 'production', native=True, nativeJournals=journals,
                    nativeFlags=[entry['nativeFlags'] for entry in journals],
                    engine=self.engine, provenance=self.provenance)

    def evidence_runs(self, candidate_id, seed_pairs):
        """Compact paired evidence for workflow projections, without expanding every stored journal."""
        wanted = {tuple(pair) for pair in (seed_pairs or [])
                  if isinstance(pair, (list, tuple)) and len(pair) == 2}
        if not wanted:
            return []
        output = []
        fields = ('seeds', 'resolved', 'censored', 'verdict', 'diagnostic', 'earned',
                  'higherPotential', 'rewardOutcome', 'prizeCallbacks')
        pairs = list(wanted)
        for start in range(0, len(pairs), 300):
            batch = pairs[start:start+300]
            predicates = ' OR '.join('(o.seed_a=? AND o.seed_b=?)' for _ in batch)
            args = [self.scope, candidate_id]
            for first, second in batch:
                args.extend((first, second))
            rows = self.db.execute('''SELECT o.*,c.label,c.language,c.parent_id,c.intent_zlib
                FROM nui_observation o JOIN nui_candidate c
                  ON c.scope=o.scope AND c.candidate_id=o.candidate_id
                WHERE o.scope=? AND o.candidate_id=? AND (''' + predicates + ''')
                ORDER BY o.created_at,o.observation_id''', args)
            for row in rows:
                full = self._run_view(row)
                output.append({key: full.get(key) for key in fields})
        return output

    def seed_pairs(self, candidate_id):
        """Read exact executed seed pairs without decoding the runs or their journals."""
        return [[int(row['seed_a']), int(row['seed_b'])] for row in self.db.execute(
            'SELECT seed_a,seed_b FROM nui_observation WHERE scope=? AND candidate_id=? '
            'AND seed_a IS NOT NULL AND seed_b IS NOT NULL ORDER BY created_at,observation_id',
            (self.scope, candidate_id))]

    def leaders(self):
        """Original overview payload shape, with explicit all-20 coverage and honest counts."""
        groups = {}
        for row in self._observations(include_diagnostic=True):
            groups.setdefault((row['encounter_id'], row['defeat_count'], row['candidate_id']), []).append(row)
        leaders = []
        for encounter in ALL_ENCOUNTERS:
            entries = [(key, rows) for key, rows in groups.items() if key[0] == encounter]
            by_diff = sorted({key[1] for key, _ in entries} | {0})
            for difficulty in by_diff:
                members = [(key, rows) for key, rows in entries if key[1] == difficulty]
                def best(metric, sample_key):
                    eligible = []
                    for key, attempts in members:
                        s = self._summary(attempts, self.execution_mode)
                        n = s[sample_key]
                        value = s[metric]
                        if value is not None and n >= AVERAGE_MIN_SAMPLES:
                            eligible.append((value, n, key[2], attempts[-1]['label'], s))
                    if not eligible:
                        return None
                    value, n, cid, label, s = max(eligible, key=lambda x: (x[0], x[1], x[2]))
                    return dict(candidateId=cid, label=label, source='native', mean=value,
                                samples=n, attempts=s['attempts'],
                                retainedSampleCount=s['retainedSampleCount'], comparable=s['comparable'],
                                verificationOnly=self.execution_mode != 'production')
                leaders.append(dict(encounterId=encounter, defeatCount=difficulty,
                    residentCount=len(members), highestAvgEarned=best('meanEarned','earnedSamples'),
                    highestAvgPotential=best('meanPotential','potentialSamples'),
                    verificationOnly=self.execution_mode != 'production'))
        return dict(ok=True, leaders=leaders, encounters=20, minSamples=AVERAGE_MIN_SAMPLES,
                    verificationOnly=self.execution_mode != 'production',
                    engine=self.engine, scope=self.scope)

    def encounter_stats(self):
        """Compact ``encounterId:0`` UI map, always covering encounter IDs 0 through 19."""
        output = {}
        for encounter in ALL_ENCOUNTERS:
            rows = self._observations(encounter_id=encounter, defeat_count=0)
            valid_trials = [r for r in rows if r['execution_mode'] == 'production'
                            and r['purpose'] == 'production' and r['battle_valid']
                            and not r['diagnostic'] and not r['dedup_conflict']]
            by_candidate = {r['candidate_id'] for r in valid_trials}
            wins = sum(r['resolved'] and r['verdict'] == 1 for r in valid_trials)
            losses = sum(r['resolved'] and r['verdict'] == 2 for r in valid_trials)
            no_verdict = len(valid_trials) - wins - losses
            measured_earned = [r for r in valid_trials if r['earned_eligible'] and r['resolved']
                               and r['earned'] is not None]
            earned_wins = [r for r in measured_earned if r['verdict'] == 1]
            potentials = [r for r in valid_trials if r['earned_eligible'] and r['resolved']
                          and r['verdict'] == 2
                          and r['higher_potential'] is not None]
            earned_holder = max(earned_wins, key=lambda r: (r['earned'], r['candidate_id'],
                                                            r['observation_id']), default=None)
            potential_holder = max(potentials, key=lambda r: (r['higher_potential'],
                r['candidate_id'], r['observation_id']), default=None)
            earned_total = sum(float(r['earned']) for r in measured_earned)
            output[f'{encounter}:0'] = dict(encounterId=encounter, defeatCount=0,
                lifetimeAttempts=len(valid_trials), lifetimeWins=wins, lifetimeLosses=losses,
                lifetimeNoVerdict=no_verdict,
                highestPotentialChests=(potential_holder['higher_potential']
                                        if potential_holder else None),
                highestChestsEarned=(earned_holder['earned'] if earned_holder else None),
                earnedBasis=(earned_holder['earned_basis'] if earned_holder else None),
                potentialBasis=None,
                highestPotentialCandidateId=(potential_holder['candidate_id']
                                             if potential_holder else None),
                highestEarnedCandidateId=(earned_holder['candidate_id'] if earned_holder else None),
                attempts=len(valid_trials), candidateCount=len(by_candidate),
                measuredSamples=len(measured_earned),
                meanEarned=(earned_total/len(measured_earned) if measured_earned else None),
                highestEarned=(earned_holder['earned'] if earned_holder else None),
                highestPotential=(potential_holder['higher_potential'] if potential_holder else None),
                candidateOwners=sorted(by_candidate),
                verificationOnly=self.execution_mode != 'production')
        return output

    def record_holders(self):
        """Return the original overview holder map, backed by canonical native measurements.

        Every holder is tied to its actual executed seed pair, frozen intent and run provenance.
        Earned holders require canonical eligible wins; potential holders require a resolved loss
        with a reported pending count. Diagnostic, conflicted and merely claimed rows are excluded.
        """
        holders = {}
        rows = self._observations(include_diagnostic=True)
        for row in rows:
            if (row['execution_mode'] != 'production' or row['purpose'] != 'production'
                    or not row['battle_valid'] or not row['resolved'] or row['diagnostic']
                    or row['dedup_conflict']):
                continue
            if (row['earned_eligible'] and row['verdict'] == 1 and row['earned'] is not None):
                metric, value, basis = 'earned', row['earned'], row['earned_basis']
            elif (row['earned_eligible'] and row['verdict'] == 2
                  and row['higher_potential'] is not None):
                metric, value, basis = 'potential', row['higher_potential'], None
            else:
                continue
            key = f"{metric}:{row['encounter_id']}:{row['defeat_count']}"
            old = holders.get(key)
            tie_key = (value, row['candidate_id'], row['observation_id'])
            if old is not None and tie_key <= old['_tieKey']:
                continue
            trial = self._run_view(row)
            exact_intent = trial['scenario']
            seeds = trial.get('seeds') or [None, None]
            provenance = trial.get('nativeProvenance') or {}
            identity = trial.get('engineIdentity')
            holders[key] = dict(
                encounterId=row['encounter_id'], defeatCount=row['defeat_count'],
                candidate=row['candidate_id'], phase='native', ordinal=None,
                mathSeed=seeds[0], libSeed=seeds[1], verdict=row['verdict'], value=value,
                digest=trial['digest'], digestType='native-intent-scope',
                tickLimit=exact_intent.get('tickLimit'), label=row['label'],
                scenario=exact_intent, intent=exact_intent,
                basis=basis, sourceBasis=row['source_earned_basis'],
                engineIdentity=identity, provenance=provenance,
                nativeRecordSha256=trial.get('nativeRecordSha256'),
                _tieKey=tie_key)
        for holder in holders.values():
            holder.pop('_tieKey', None)
        return holders

    def snapshot(self):
        """Small status payload for the original all-encounter UI, with every fight explicit."""
        count = self.db.execute('SELECT COUNT(*) FROM nui_candidate WHERE scope=?',
                                (self.scope,)).fetchone()[0]
        total = self.db.execute('SELECT COUNT(*) FROM nui_observation WHERE scope=?',
                                (self.scope,)).fetchone()[0]
        return dict(ok=True, engine=self.engine, compatibility=self.scope_identity,
                    verificationOnly=self.execution_mode != 'production', nativeLibrary=True,
                    executionMode=self.execution_mode, encounterStats=self.encounter_stats(),
                    recordHolders=self.record_holders(),
                    encounterAverageLeaders=self.leaders(), candidateCount=int(count),
                    totalRuns=int(total), focusEncounter=self.get('focusEncounter'),
                    candidateCursor=self.get('candidateCursor', 0), session=self.get('session', {}))

    def candidate_seed_inputs(self, encounter_id, max_items=8):
        if not isinstance(max_items, int) or not 0 <= max_items <= 8:
            raise ValueError('max_items must be between 0 and 8')
        listing = self.strategies(encounter_id, 0)['strategies']
        usable = (list(listing) if self.execution_mode == 'production' else
                  [row for row in listing if row['meanEarned'] is not None
                   and row['earnedSamples'] > 0])
        usable.sort(key=lambda r: (r['meanEarned'] is not None, r['meanEarned'] or 0,
                                   r['earnedSamples'], r['candidateId']), reverse=True)
        return [dict(candidateId=row['candidateId'], scenario=row['intent'],
                     meanEarned=row['meanEarned'], samples=row['earnedSamples'],
                     strategyEvidence=row.get('strategyEvidence', False),
                     seedSource='native-intent',
                     verificationOnly=self.execution_mode != 'production') for row in usable[:max_items]]

    def read_log(self, candidate_id, observation_id=None):
        return list(self.iter_log(candidate_id, observation_id))

    def iter_log(self, candidate_id, observation_id=None):
        query = '''SELECT o.observation_id,o.battle_valid,o.earned_eligible,o.earned_basis,
                          o.source_earned_basis,o.finish_diagnostic,o.execution_mode,
                          o.dedup_conflict,r.journal_id,r.raw_sha256,r.raw_zlib,r.provenance_zlib
                   FROM nui_observation o JOIN nui_runs r
                   USING(scope,observation_id) WHERE o.scope=? AND o.candidate_id=?'''
        args = [self.scope, candidate_id]
        if observation_id is not None:
            query += ' AND o.observation_id=?'
            args.append(observation_id)
        query += ' ORDER BY r.created_at,r.journal_id'
        for r in self.db.execute(query, args):
            envelope = _unpack(r['raw_zlib'])
            yield dict(observationId=r['observation_id'], journalId=r['journal_id'],
                rawRecord=envelope.get('nativeRecord', envelope),
                provenance=_unpack(r['provenance_zlib']),
                nativeFlags=envelope.get('nativeFlags', {}), rawSha256=r['raw_sha256'],
                verificationOnly=self.execution_mode != 'production',
                strategyEvidence=bool(r['earned_eligible'] and not r['dedup_conflict']),
                battleValid=bool(r['battle_valid']),
                executionMode=r['execution_mode'], earnedBasis=r['earned_basis'],
                sourceEarnedBasis=r['source_earned_basis'],
                finishDiagnostic=bool(r['finish_diagnostic']),
                dedupConflict=bool(r['dedup_conflict']))

    def import_journal(self, path, language):
        """Stream a saved JSONL journal into this scope; reruns are idempotent, no battles occur."""
        self._writer()
        path = Path(path)
        source_hash = hashlib.sha256()
        rows = duplicates = diagnostic = measured = 0
        with path.open('rb') as source:
            for line_number, raw in enumerate(source, 1):
                source_hash.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f'invalid saved journal JSON at line {line_number}: {exc}') from exc
                if not isinstance(record, dict):
                    raise ValueError(f'saved journal line {line_number} is not an object')
                result = self.import_record(language, record)
                rows += 1
                duplicates += int(result['duplicate'])
                diagnostic += int(result['diagnostic'])
                measured += int(result['strategyEvidence'])
        digest = source_hash.hexdigest()
        with self.db:
            self._meta_set('journal:' + digest, dict(path=path.name, rows=rows,
                                                    language=str(language), importedAt=time.time()))
        return dict(ok=True, path=str(path), sourceSha256=digest, rows=rows,
                    uniqueObservations=rows-duplicates, duplicates=duplicates,
                    diagnosticRows=diagnostic, earnedMeasurements=measured,
                    verificationOnly=self.execution_mode != 'production', execution='none')

    def verify_journal(self, path):
        """Confirm parsed source rows are durably represented by raw hashes; never executes them."""
        self._writer()
        path = Path(path)
        source_hash = hashlib.sha256()
        rows = set()
        line_count = 0
        with path.open('rb') as source:
            for line_number, raw in enumerate(source, 1):
                source_hash.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f'invalid saved journal JSON at line {line_number}: {exc}') from exc
                if not isinstance(record, dict):
                    raise ValueError(f'saved journal line {line_number} is not an object')
                rows.add(_digest(record))
                line_count += 1
        present = {row['raw_sha256'] for row in self.db.execute(
            'SELECT raw_sha256 FROM nui_runs WHERE scope=?', (self.scope,))}
        missing = sorted(rows-present)
        digest = source_hash.hexdigest()
        return dict(ok=not missing, path=str(path), sourceSha256=digest,
                    sourceRows=line_count, uniqueSourceRecords=len(rows),
                    persistedSourceRecords=len(rows & present), missingRawHashes=missing[:20],
                    sourceRegistered=bool(self.get('journal:' + digest)),
                    verificationOnly=self.execution_mode != 'production', execution='none')

    def export_diagnostics(self, path, count=None, progress=None):
        """Stream the most recent count observations as bounded JSON, preserving the target on error."""
        from strategy_diagnostic_export import MAX_EXPORT_BYTES, normalize_count

        requested = normalize_count(count)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        latest = ('SELECT * FROM nui_observation WHERE scope=? '
                  'ORDER BY created_at DESC, observation_id DESC LIMIT ?')
        included = int(self.db.execute('SELECT COUNT(*) FROM (' + latest + ')',
                                       (self.scope, requested)).fetchone()[0])
        # Candidate metadata is intentionally scoped to the exported observations.
        candidate_total = int(self.db.execute(
            'SELECT COUNT(DISTINCT candidate_id) FROM (' + latest + ')',
            (self.scope, requested)).fetchone()[0])
        total_observations = int(self.db.execute(
            'SELECT COUNT(*) FROM nui_observation WHERE scope=?', (self.scope,)).fetchone()[0])
        journal_total = int(self.db.execute(
            'SELECT COUNT(*) FROM nui_runs r JOIN nui_observation o USING(scope,observation_id) '
            'WHERE o.scope=? AND o.observation_id IN (SELECT observation_id FROM (' + latest + '))',
            (self.scope, self.scope, requested)).fetchone()[0])
        started_at = time.monotonic()
        last_progress_at = 0.0
        journal_done = 0

        def report(stage, label, included_now=0, journal_now=0, candidate_now=0, force=False):
            nonlocal last_progress_at
            if not callable(progress):
                return
            now = time.monotonic()
            if not force and now-last_progress_at < .5:
                return
            elapsed = max(0.0, now-started_at)
            rate = included_now/elapsed if stage == 'observations' and elapsed > .25 else None
            eta = ((included-included_now)/rate if rate and stage == 'observations' else None)
            progress(dict(stage=stage, stageLabel=label, requested=included, included=included_now,
                requestedCount=requested,
                examined=included, totalObservations=included, totalLibraryObservations=total_observations,
                candidateDone=candidate_now, candidateCount=candidate_total,
                journalDone=journal_now, journalTotal=journal_total,
                bytesWritten=written, elapsedSeconds=elapsed, ratePerSecond=rate, etaSeconds=eta))
            last_progress_at = now

        fd, temp_name = __import__('tempfile').mkstemp(prefix=target.name+'.', suffix='.tmp',
                                                        dir=str(target.parent))
        temp = Path(temp_name)
        written = 0
        def emit(handle, value):
            nonlocal written
            encoded = (value if isinstance(value, bytes) else
                       json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))
            if written + len(encoded) > MAX_EXPORT_BYTES:
                raise ValueError(f'Diagnostic JSON exceeds the {MAX_EXPORT_BYTES:,}-byte export cap; destination was left unchanged.')
            handle.write(encoded)
            written += len(encoded)

        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(b'{')
                written = 1
                report('prepare', 'Preparing export', force=True)
                header = dict(schema='ka-native-library-export-1', engine=self.engine, scope=self.scope,
                    compatibility=self.scope_identity, provenance=self.provenance,
                    verificationOnly=self.execution_mode != 'production', requestedCount=requested,
                    includedCount=included, totalObservations=int(self.db.execute(
                        'SELECT COUNT(*) FROM nui_observation WHERE scope=?', (self.scope,)).fetchone()[0]))
                for key, value in header.items():
                    emit(handle, (json.dumps(key) + ': ' + json.dumps(value, ensure_ascii=False,
                         allow_nan=False) + ',').encode('utf-8'))
                emit(handle, b'"candidates": [')
                candidates = self.db.execute(
                    'SELECT c.* FROM nui_candidate c WHERE c.scope=? AND c.candidate_id IN '
                    '(SELECT DISTINCT candidate_id FROM (' + latest + ')) ORDER BY c.candidate_id',
                    (self.scope, self.scope, requested))
                for index, row in enumerate(candidates):
                    if index:
                        emit(handle, b',')
                    emit(handle, dict(candidateId=row['candidate_id'],
                        intent=_unpack(row['intent_zlib']), intentSha256=row['intent_sha256'],
                        label=row['label'], parentId=row['parent_id'], language=row['language'],
                        sourceIdentity=row['source_identity']))
                    if index % 64 == 63:
                        report('candidates', 'Writing candidate metadata', candidate_now=index+1)
                report('candidates', 'Writing candidate metadata', candidate_now=candidate_total,
                       force=True)
                report('observations', 'Writing observations', force=True)
                emit(handle, b'], "observations": [')
                rows = self.db.execute('SELECT * FROM (' + latest + ') ORDER BY created_at, observation_id',
                                       (self.scope, requested))
                for index, row in enumerate(rows):
                    if index:
                        emit(handle, b',')
                    observation = dict(observationId=row['observation_id'],
                        candidateId=row['candidate_id'], nativeFlags=json.loads(row['native_flags']),
                        earned=row['earned'], higherPotential=row['higher_potential'],
                        diagnostic=bool(row['diagnostic']), rawSha256=row['raw_sha256'],
                        earnedEligible=bool(row['earned_eligible']), executionMode=row['execution_mode'],
                        battleValid=bool(row['battle_valid']), purpose=row['purpose'],
                        earnedBasis=row['earned_basis'], sourceEarnedBasis=row['source_earned_basis'],
                        finishDiagnostic=bool(row['finish_diagnostic']),
                        dedupConflict=bool(row['dedup_conflict']))
                    emit(handle, observation)
                    report('observations', 'Writing observations', index+1)
                emit(handle, b'], "journals": [')
                journal_count = 0
                report('journals', 'Writing journal records', included, 0, force=True)
                rows = self.db.execute('SELECT candidate_id,observation_id FROM (' + latest + ') '
                                        'ORDER BY created_at, observation_id', (self.scope, requested))
                for row in rows:
                    for log in self.iter_log(row['candidate_id'], row['observation_id']):
                        if journal_count:
                            emit(handle, b',')
                        emit(handle, log)
                        journal_count += 1
                        journal_done = journal_count
                        if journal_count % 32 == 0:
                            report('journals', 'Writing journal records', included, journal_done)
                report('finalize', 'Finalizing diagnostics file', included, journal_count, force=True)
                emit(handle, b']}')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except Exception:
            try:
                temp.unlink()
            except OSError:
                pass
            raise
        return dict(ok=True, path=str(target), format='json', count=requested,
                    candidates=candidate_total, observations=included, journals=journal_count,
                    bytes=written, capBytes=MAX_EXPORT_BYTES,
                    verificationOnly=self.execution_mode != 'production')

    def close(self):
        self._writer()
        self.db.close()
