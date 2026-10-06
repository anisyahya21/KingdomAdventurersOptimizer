"""Compact checks for `strategy_optimizer_adapter`.

Two layers, run as one unittest suite:

  * structural / cap checks and a fake-runner test that pins the compact metric dict, the digest and
    the bulk `include_trace=False` contract without paying for a simulation;
  * one small *real* deterministic parity test: the same scenario and seed pair run once for bulk and
    once with `trace=True`, asserting one digest and a verified `ka-battle-replay-1` replay.

    python check_strategy_optimizer_adapter.py

`--quick` (or `-q`) is accepted by unittest; the real run is a 300-tick synthetic fixture, not the
player build, and its values are labelled test values, not game facts.
"""
import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import combat_replay_export
import combat_sandbox
import strategy_search
import strategy_optimizer_adapter as adapter
from combat_parameters import HUMAN_TRAINING_PARAMETERS

try:
    import strategy_optimizer as optimizer
except Exception:  # noqa: BLE001 - the consumer is optional for these adapter checks
    optimizer = None

EXPECTED_KEYS = {'verdict', 'censored', 'ticks', 'prizeCallbacks', 'retained', 'survivors',
                 'resourceUses', 'healthFraction', 'behavior', 'digest', 'seeds', 'rewardOutcome',
                 # Additive, attached after the digest: the stored-attack progress block, the MP
                 # telemetry and the explicit Holy Herb evidence. `resultBackend`/`routingVersion` are
                 # added by the native backend outside the digest and were already missing here.
                 'progressMetrics', 'mpMetrics', 'herbMetrics', 'resultBackend', 'routingVersion'}


def human(name, skills, stats):
    """Synthetic test unit (labelled values; never presented as the player build or as game data)."""
    return dict(name=name, human=True, monsterId=None, weaponId=0, equipment=[], visitor=False,
                leaderIdentity=False, skills=list(skills), invocationLevels=[1] * len(skills),
                parameters={p: dict(rawValue=stats.get(p, 1),
                                    rawMax=stats.get(p, 1) if p in (10, 11) else 2147483647,
                                    extraValue=0, extraMax=0, trainingLevel=123)
                            for p in HUMAN_TRAINING_PARAMETERS})


def small_scenario(tick_limit=40):
    return dict(schema='ka-special-combat-research-1', encounterId=0, defeatCount=0, mathSeed=7,
                libSeed=8, tickLimit=tick_limit,
                ownUnits=[human('probe fighter', [26, 25],
                                {10: 1500, 11: 400, 13: 80, 14: 120, 15: 60, 16: 40, 19: 5})],
                holyHerbStock=0, inputs=[],
                note='Synthetic test fixture; test values, not game facts.')


def resolved_scenario():
    """The small fixture with the horizon and stats the real parity test resolves on (verdict 1)."""
    scenario = small_scenario(tick_limit=300)
    scenario['ownUnits'] = [human('probe fighter', [26, 110, 25, 24, 23, 22],
                                  {10: 5000, 11: 1000, 13: 300, 14: 3000, 15: 220, 16: 110, 19: 0})]
    return scenario


def fake_report(data):
    own_names = [unit['name'] for unit in data['ownUnits']]
    names = {name: 100 + index for index, name in enumerate(own_names)}
    names['enemy:0:0'] = 900
    units = {100 + index: dict(hp=750, mp=0, state=1, commands=0) for index in range(len(own_names))}
    units[900] = dict(hp=0, mp=0, state=8, commands=0)
    return dict(
        result=dict(verdict=1, censored=False, ticks=10, verdictTick=9, prizeCallbacks=3,
                    preVerdictPrizeCallbacks=2, names=names, units=units),
        setup=dict(ownUnits=[dict(name=name, effectiveParameters={10: dict(value=750, maximum=1000)})
                             for name in own_names]),
        metrics=dict(attackAttempts=4, heals=1, prizeCallbacks=3),
        holyHerbUses=[dict(used=True), dict(used=False)], itemUses=[dict(used=True)])


def fake_replay(ticks=10):
    events = [dict(kind='prize', tick=5), dict(kind='prize', tick=9), dict(kind='prize', tick=99)]
    return dict(schema='ka-battle-replay-1', events=events,
                finalState=dict(verdict=1, censored=False, ticks=ticks, verdictTick=9, prizeCallbacks=3),
                metrics=dict(attackAttempts=4, heals=1, prizeCallbacks=3))


def reward_entitlement(awarded, basis, *, pending=3, certificate=None, verdict=1):
    """A synthetic `rewardEntitlement` block using the runner's own field names."""
    return dict(
        certificateId='C1..C4',
        certificate=certificate or dict(holds=False, frame=None, timingRule='test rule',
                                        issuedBeforeVerdict=False),
        battleVerdict=verdict, pendingChestCount=pending, awardedChestCount=awarded,
        awardedChestCountBasis=basis, rewardCountSettled=awarded is not None,
        rewardCountReason='synthetic test reason; test value, not a game fact')


def reward_report(entitlement, **overrides):
    """The legacy `fake_report` with a runner-shaped reward entitlement attached."""
    report = fake_report(small_scenario())
    report['result']['rewardEntitlement'] = entitlement
    report['result'].update(overrides)
    return report


class FakeSandbox:
    def __init__(self, report, replay=None):
        self.report = report
        self.replay = replay or fake_replay()
        self.calls = []

    def __call__(self, data, include_trace=False, **kwargs):
        self.calls.append(dict(include_trace=include_trace, mathSeed=data.get('mathSeed'),
                               libSeed=data.get('libSeed')))
        return deepcopy(self.report)


class ScenarioValidationTests(unittest.TestCase):
    def test_default_scenario_is_validated_copy(self):
        first = adapter.default_scenario()
        second = adapter.default_scenario()
        self.assertEqual(len(first['ownUnits']), 6)
        self.assertEqual(first, second)
        first['ownUnits'][0]['name'] = 'mutated in place'
        self.assertEqual(adapter.default_scenario()['ownUnits'][0]['name'],
                         second['ownUnits'][0]['name'])
        self.assertEqual(adapter.validate_scenario(second), second)

    def test_declared_caps(self):
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario(dict(small_scenario(), tickLimit=adapter.MAX_TICK_LIMIT + 1))
        unit = small_scenario()['ownUnits'][0]
        many = dict(small_scenario(),
                    ownUnits=[dict(unit, name=f'probe fighter {index}')
                              for index in range(adapter.MAX_UNITS + 1)])
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario(many)
        oversized = dict(small_scenario(), note='x' * (adapter.MAX_SCENARIO_BYTES + 1))
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario(oversized)

    def test_invalid_scenarios_rejected(self):
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario({'schema': 'ka-special-combat-research-1'})
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario(dict(small_scenario(), schema='not-a-schema'))
        with self.assertRaises(adapter.StrategyOptimizerError):
            adapter.validate_scenario(dict(small_scenario(), tickLimit='40'))


class ProposalTests(unittest.TestCase):
    def test_propose_is_deterministic_and_legal(self):
        base = adapter.default_scenario()
        first = adapter.propose(base, 3)
        second = adapter.propose(base, 3)
        self.assertEqual(first, second)
        self.assertNotEqual(first['ownUnits'], base['ownUnits'])
        self.assertEqual(adapter.validate_scenario(first), first)

    def test_propose_preserves_roster_and_multiset(self):
        base = adapter.default_scenario()
        moved = adapter.propose(base, 3)

        def signature(units):
            rows = []
            for unit in units:
                fixed = {key: value for key, value in unit.items()
                         if key not in ('name', 'skills', 'invocationLevels')}
                rows.append((unit['name'], json.dumps(fixed, sort_keys=True),
                             tuple(sorted(zip(unit['skills'], unit['invocationLevels'])))))
            return sorted(rows)

        self.assertEqual(signature(base['ownUnits']), signature(moved['ownUnits']))

    def test_propose_rejects_degenerate_axes(self):
        with mock.patch.object(strategy_search, 'formation_axis',
                               lambda base: dict(arrangements=[], space=0)):
            with mock.patch.object(strategy_search, 'skill_axis',
                                   lambda base: dict(arrangements=[], space=0)):
                with self.assertRaises(adapter.StrategyOptimizerError):
                    adapter.propose(small_scenario(), 1)


class FakeRunnerSimulationTests(unittest.TestCase):
    def test_compact_metrics_and_no_trace_transport(self):
        fake = FakeSandbox(fake_report(small_scenario()))
        with mock.patch.object(combat_sandbox, 'run_scenario', fake):
            compact = adapter.simulate(small_scenario(), (7, 8))
            again = adapter.simulate(small_scenario(), (7, 8))
        self.assertEqual(set(compact), EXPECTED_KEYS)
        self.assertEqual(fake.calls, [dict(include_trace=False, mathSeed=7, libSeed=8)] * 2)
        self.assertEqual(compact, again)
        self.assertEqual(compact['seeds'], [7, 8])
        self.assertIsNone(compact['retained'])
        self.assertEqual(compact['prizeCallbacks'], 2)
        self.assertEqual(compact['survivors'], 1)
        self.assertEqual(compact['healthFraction'], 0.75)
        self.assertEqual(compact['behavior'], dict(heals=1, attacks=4, prizes=3))
        # One successful Holy Herb use plus one successful item use; the unused entry does not count.
        self.assertEqual(compact['resourceUses'], 2)
        self.assertEqual(len(compact['digest']), 64)
        int(compact['digest'], 16)
        json.dumps(compact)

    def test_unresolved_run_censors_prize_metric(self):
        report = fake_report(small_scenario())
        report['result'].update(verdict=None, censored=True, verdictTick=None,
                                preVerdictPrizeCallbacks=None)
        fake = FakeSandbox(report)
        with mock.patch.object(combat_sandbox, 'run_scenario', fake):
            compact = adapter.simulate(small_scenario(), {'mathSeed': 7, 'libSeed': 8})
        self.assertIsNone(compact['prizeCallbacks'])
        self.assertEqual(compact['seeds'], [7, 8])

    def test_trace_path_verifies_and_attaches_replay(self):
        fake = FakeSandbox(fake_report(small_scenario()))
        with mock.patch.object(combat_sandbox, 'run_scenario', fake):
            with mock.patch.object(combat_replay_export, 'export_replay',
                                   lambda data, include_events=True: deepcopy(fake.replay)):
                bulk = adapter.simulate(small_scenario(), (7, 8))
                traced = adapter.simulate(small_scenario(), (7, 8), trace=True)
        self.assertEqual(bulk['digest'], traced['digest'])
        self.assertEqual(traced['replay']['schema'], 'ka-battle-replay-1')
        self.assertEqual(set(traced), EXPECTED_KEYS | {'replay'})

    def test_trace_mismatch_fails_closed(self):
        fake = FakeSandbox(fake_report(small_scenario()))
        with mock.patch.object(combat_sandbox, 'run_scenario', fake):
            with mock.patch.object(combat_replay_export, 'export_replay',
                                   lambda data, include_events=True: fake_replay(ticks=11)):
                with self.assertRaises(adapter.StrategyOptimizerError):
                    adapter.simulate(small_scenario(), (7, 8), trace=True)


class RewardOutcomeTests(unittest.TestCase):
    """The additive `rewardOutcome` reading: real values, but never a retained-chest count."""

    def compact_for(self, report):
        fake = FakeSandbox(report)
        with mock.patch.object(combat_sandbox, 'run_scenario', fake):
            return adapter.simulate(small_scenario(), (7, 8))

    def test_reward_outcome_is_outside_the_metric_digest(self):
        # The digest is computed before rewardOutcome is attached, so a row saved under the old
        # shape keeps exactly the same digest and the new field can never invalidate a library.
        legacy = self.compact_for(fake_report(small_scenario()))
        enriched = self.compact_for(reward_report(reward_entitlement(0, 'native-win-loss-gate')))
        self.assertEqual(legacy['digest'], enriched['digest'])
        for compact in (legacy, enriched):
            # Every additive block is attached *after* the digest, so none of them may enter it:
            # rewardOutcome, the stored-attack progress block, the MP telemetry and the explicit
            # Holy Herb evidence.
            covered = {key: value for key, value in compact.items()
                       if key not in ('rewardOutcome', 'replay', 'digest', 'progressMetrics',
                                      'mpMetrics', 'herbMetrics',
                                      # The backend label is added by `simulate` outside the digest.
                                      'resultBackend', 'routingVersion')}
            self.assertEqual(compact['digest'], adapter._digest(covered))

    def test_row_without_entitlement_stays_backward_compatible(self):
        compact = self.compact_for(fake_report(small_scenario()))
        self.assertEqual(compact['rewardOutcome'], dict(
            pendingChests=None, awardedChests=None, awardedBasis=None,
            inventoryVerified=False, reason=None))
        self.assertIsNone(compact['retained'])

    def test_loss_is_zero_from_the_native_gate_and_never_retained(self):
        compact = self.compact_for(reward_report(
            reward_entitlement(0, 'native-win-loss-gate', verdict=2), verdict=2))
        outcome = compact['rewardOutcome']
        self.assertEqual(outcome['awardedChests'], 0)
        self.assertEqual(outcome['awardedBasis'], 'native-win-loss-gate')
        self.assertFalse(outcome['inventoryVerified'])
        self.assertNotIn('certificate', outcome)
        self.assertIsNone(compact['retained'])

    def test_certified_win_reports_its_pending_count_as_entitlement_only(self):
        certificate = dict(holds=True, frame=235, issuedBeforeVerdict=True, timingRule='test rule')
        compact = self.compact_for(reward_report(reward_entitlement(
            1, 'reward-entitlement-certificate', pending=1, certificate=certificate)))
        outcome = compact['rewardOutcome']
        self.assertEqual(outcome['awardedChests'], 1)
        self.assertEqual(outcome['awardedBasis'], 'reward-entitlement-certificate')
        self.assertFalse(outcome['inventoryVerified'])
        self.assertEqual(outcome['certificate']['frame'], 235)
        self.assertTrue(outcome['certificate']['holds'])
        self.assertIsNone(compact['retained'])

    def test_unknown_win_and_unresolved_never_show_a_positive_award(self):
        unknown = self.compact_for(reward_report(
            reward_entitlement(None, 'unknown-win-without-certificate', pending=4)))
        self.assertIsNone(unknown['rewardOutcome']['awardedChests'])
        self.assertEqual(unknown['rewardOutcome']['pendingChests'], 4)
        self.assertNotIn('certificate', unknown['rewardOutcome'])
        self.assertIsNone(unknown['retained'])
        unresolved = self.compact_for(reward_report(
            reward_entitlement(None, 'unknown-unresolved-battle', pending=5),
            verdict=None, censored=True))
        self.assertIsNone(unresolved['rewardOutcome']['awardedChests'])
        self.assertNotIn('certificate', unresolved['rewardOutcome'])
        self.assertIsNone(unresolved['retained'])

    def test_pending_count_is_never_promoted_to_award_or_retained(self):
        compact = self.compact_for(reward_report(
            reward_entitlement(None, 'unknown-win-without-certificate', pending=9)))
        self.assertEqual(compact['rewardOutcome']['pendingChests'], 9)
        self.assertIsNone(compact['rewardOutcome']['awardedChests'])
        self.assertNotIn('retained', compact['rewardOutcome'])
        self.assertIsNone(compact['retained'])


class RealParityTests(unittest.TestCase):
    def test_real_trace_no_trace_parity(self):
        scenario = resolved_scenario()
        bulk = adapter.simulate(scenario, (7, 8))
        repeat = adapter.simulate(scenario, (7, 8))
        traced = adapter.simulate(scenario, (7, 8), trace=True)
        self.assertEqual(bulk, repeat)
        self.assertEqual(bulk['digest'], traced['digest'])
        self.assertEqual(set(bulk), EXPECTED_KEYS)
        self.assertEqual(bulk['verdict'], 1)
        self.assertFalse(bulk['censored'])
        self.assertIsNotNone(bulk['prizeCallbacks'])
        self.assertEqual(traced['replay']['schema'], 'ka-battle-replay-1')
        self.assertEqual(set(traced), EXPECTED_KEYS | {'replay'})
        self.assertEqual(traced['seeds'], [7, 8])


class StaticDataTests(unittest.TestCase):
    def test_stats_are_effective_and_serializable(self):
        stats = adapter.stats(small_scenario())
        self.assertEqual(set(stats), {'probe fighter'})
        fighter = stats['probe fighter']
        self.assertIn(10, fighter['parameters'])
        self.assertEqual(fighter['parameters'][10]['value'],
                         fighter['parameters'][10]['maximum'])
        self.assertTrue(fighter['weaponRange'] >= 0)
        json.dumps(stats)

    def test_provenance_is_cached_and_matches_files(self):
        first = adapter.provenance()
        second = adapter.provenance()
        refreshed = adapter.provenance(refresh=True)
        self.assertEqual(first, second)
        self.assertEqual(first['digest'], refreshed['digest'])
        self.assertEqual(len(first['digest']), 64)
        self.assertEqual(first['missing'], [])
        sandbox = HERE / 'combat_sandbox.py'
        self.assertEqual(first['files']['tools/recovery/combat_sandbox.py'],
                         hashlib.sha256(sandbox.read_bytes()).hexdigest())
        self.assertIn('tools/recovery/strategy_optimizer_adapter.py', first['files'])
        self.assertIn('tools/recovery/strategy_search.py', first['files'])
        self.assertIn('runtime-data/weapon-skill-profiles.json', first['files'])
        self.assertEqual(first['count'], len(first['files']))


@unittest.skipUnless(optimizer is not None, 'strategy_optimizer consumer is not present')
class OptimizerContractTests(unittest.TestCase):
    def test_compact_row_satisfies_optimizer_summary(self):
        compact = adapter.simulate(resolved_scenario(), (7, 8))
        summary = optimizer.summarize([compact])
        self.assertEqual(summary['meanSurvivors'], compact['survivors'])
        self.assertEqual(summary['meanTicks'], compact['ticks'])
        self.assertIsInstance(summary['meanResources'], (int, float))
        self.assertIsInstance(optimizer.family_key([compact]), str)
        json.dumps(optimizer.interesting([compact]), sort_keys=True, allow_nan=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
