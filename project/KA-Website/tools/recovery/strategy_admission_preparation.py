"""Pure worker preparation for admission; no library, grants, seeds or writes."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json

import strategy_mechanics as mechanics
import strategy_encounter_compiler as compiler
from strategy_optimizer_adapter import stats

VERSION = 'strategy-admission-preparation-1'
POLICY_KEYS = ('finishPolicy', 'tickLimit', 'holyHerbStock', 'startProfile', 'inputs',
               'holyHerbMaxUses', 'holyHerbTriggerUnits', 'mpWatchUnits')


@lru_cache(maxsize=1)
def _engine_revision():
    import strategy_revision
    return strategy_revision.current_battle_compatibility_revision()[0]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def prepare(scenario, context, *, include_stats=True):
    """Use canonical setup, observation and compilation on frozen plain inputs."""
    import fixed_formation
    reasons = fixed_formation.gate(scenario)
    if reasons is not None:
        raise fixed_formation.FixedFormationError(
            'fixed-formation policy refused scenario: ' + '; '.join(reasons))
    if not isinstance(context, dict):
        return None
    actual_mechanics = mechanics.dependency_digest()
    if actual_mechanics != context.get('mechanicsRevision'):
        raise ValueError('admission preparation mechanics changed')
    actual_engine = _engine_revision()
    if actual_engine != context.get('engineRevision'):
        raise ValueError('admission preparation engine changed')
    observed = deepcopy(scenario)
    patch = {}
    try:
        import strategy_mp_recovery
        patch = strategy_mp_recovery.observation_patch(scenario)
        if patch:
            observed.update(patch)
    except Exception:  # same optional observation contract as Store.observed_scenario
        pass
    policy = context['policy']
    for key in POLICY_KEYS:
        if key in policy:
            observed[key] = deepcopy(policy[key])
        else:
            observed.pop(key, None)
    revision = (compiler.compile_encounter(observed).get('revision') or {}).get('digest')
    return dict(version=VERSION, rawDigest=digest(scenario), policyDigest=digest(policy),
                engineRevision=actual_engine, mechanicsRevision=actual_mechanics,
                observationPatch=deepcopy(patch), observedDigest=digest(observed), encounterRevision=revision,
                stats=stats(scenario) if include_stats else None)


def observed_view(packet, scenario, policy):
    """Reassemble the prepared worker view without transporting another full scenario."""
    observed = dict(scenario)
    observed.update(packet['observationPatch'])
    for key in POLICY_KEYS:
        if key in policy:
            observed[key] = policy[key]
        else:
            observed.pop(key, None)
    return observed


def matches(packet, scenario, policy, engine_revision, mechanics_revision):
    """Cheap exact bindings checked on the coordinator after its live envelope gate."""
    return (isinstance(packet, dict) and packet.get('version') == VERSION
            and packet.get('rawDigest') == digest(scenario)
            and packet.get('policyDigest') == digest(policy)
            and packet.get('engineRevision') == engine_revision
            and packet.get('mechanicsRevision') == mechanics_revision
            and isinstance(packet.get('observationPatch'), dict)
            and set(packet['observationPatch']).issubset({'mpWatchUnits'})
            and packet.get('observedDigest') == digest(observed_view(packet, scenario, policy))
            and packet.get('encounterRevision') is not None)
