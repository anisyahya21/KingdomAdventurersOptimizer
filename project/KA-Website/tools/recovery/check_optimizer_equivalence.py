"""Inert-parameter equivalence (Gathering 20, Move 21, Love 22) in the strategy optimiser.

The stored id (`identity`) hashes the whole scenario, so two builds differing only in a parameter no
combat formula reads were stored as separate candidates, spent separate runs and ranked as separate
variants. The decision-domain `equivalence_identity` erases exactly those three slots. This check
proves, deterministically and without any battle:

  (a) an inert-only difference is one searchable strategy: it is refused as a new candidate and claims
      no run, through both `Store.add` and `Store.add_child`;
  (b) a combat-stat difference - including Energy (12), which is deliberately *not* treated as inert -
      stays a distinct candidate;
  (c) a legacy row written under the old full-scenario id is still readable and replayable, and a new
      proposal equivalent to it is refused against the read-only compatibility index;
  (d) no real input difference (encounter, difficulty, horizon, herbs, skills) is merged;
  (e) fine-tune combat axes still work and the non-combat slots stay refused;
  (f) building the equivalence index writes nothing to the library, and the live library is never
      opened.

    python check_optimizer_equivalence.py
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_finetune                                                  # noqa: E402
import strategy_learner as learner                                        # noqa: E402
import strategy_optimizer as optimizer                                    # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,     # noqa: E402
                                        validate_scenario)

LIVE_LIBRARY = 'strategiesv18.sqlite'
FAILURES = []


def expect(label, condition, detail=''):
    print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
    if not condition:
        FAILURES.append(label)


def parameter_value(scenario, parameter_id):
    unit = next(u for u in scenario['ownUnits'] if u.get('human'))
    entry = (unit.get('parameters') or {}).get(parameter_id) \
        or (unit.get('parameters') or {}).get(str(parameter_id)) or {}
    return int(entry.get('rawValue') or 0)


def set_parameter(scenario, parameter_id, value):
    """A copy of `scenario` with one human's raw value for `parameter_id` changed."""
    clone = copy.deepcopy(scenario)
    unit = next(u for u in clone['ownUnits'] if u.get('human'))
    entry = dict((unit.get('parameters') or {}).get(parameter_id)
                 or (unit.get('parameters') or {}).get(str(parameter_id)) or {})
    entry['rawValue'] = value
    unit['parameters'][parameter_id] = entry
    return clone


def candidate_count(store):
    return store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]


def run_count(store):
    return store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0]


def one_result(ordinal=0):
    return dict(verdict=1, censored=False, prizeCallbacks=3, ticks=100, survivors=2,
                resourceUses=0, behavior=dict(attacks=5, heals=1, prizes=3),
                seeds=[ordinal, 100 + ordinal], digest=f'd{ordinal}')


def add(store, scenario, label, source='mutation'):
    """`Store.add` inside its caller-managed transaction, exactly as the proposal path calls it."""
    with store.db:
        return store.add(scenario, label, source, {})


def add_child(store, scenario, parent, label):
    with store.db:
        return store.add_child(scenario, label, {}, parent, 'set-stat', 20, 'Gathering',
                               'branch', 1)


def insert_legacy(store, scenario):
    """Write one candidate exactly the way the old full-scenario-id path wrote it."""
    cid = optimizer.identity(scenario)
    with store.db:
        store.db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                         (cid, optimizer.canonical(scenario), f'Legacy {cid[:8]}', 'mutation',
                          '{}', time.time_ns()))
        learner.record_candidate_meta(store.db, cid, int(scenario['encounterId']),
                                      int(scenario.get('defeatCount') or 0),
                                      learner.strategy_region(scenario))
    return cid


def main():
    base = validate_scenario(default_scenario())
    base_key = optimizer.equivalence_identity(base)

    with tempfile.TemporaryDirectory(prefix='ka-equivalence-') as root:
        path = Path(root) / 'library.sqlite'
        expect('the check never targets the live library', LIVE_LIBRARY not in str(path))

        # ---- (a) an inert-only difference is not a new strategy ------------------------------
        store = optimizer.Store(path, provenance())
        try:
            for parameter_id in optimizer.INERT_PARAMETER_IDS:
                inert = set_parameter(base, parameter_id, 424242 + parameter_id)
                expect(f'identity distinguishes inert parameter {parameter_id}',
                       optimizer.identity(inert) != optimizer.identity(base))
                expect(f'equivalence ignores inert parameter {parameter_id}',
                       optimizer.equivalence_identity(inert) == base_key)

            baseline = add(store, base, 'Baseline', 'supplied')
            expect('the baseline is stored', candidate_count(store) == 1)

            inert_variant = set_parameter(base, 20, 999)
            expect('Store.add refuses the inert-only duplicate and reuses the id',
                   add(store, inert_variant, 'Dup') == baseline
                   and candidate_count(store) == 1)

            child, existed = add_child(store, inert_variant, baseline, 'Dup child')
            expect('Store.add_child reports the duplicate',
                   existed and child == baseline)
            expect('no candidate and no run were allocated for the duplicate',
                   candidate_count(store) == 1 and run_count(store) == 0)

            combined = set_parameter(set_parameter(set_parameter(base, 20, 11), 21, 12), 22, 13)
            expect('all three inert slots together are still one strategy',
                   add(store, combined, 'All inert') == baseline
                   and candidate_count(store) == 1)

            # ---- (b) combat differences stay distinct ----------------------------------------
            attack = set_parameter(base, 13, parameter_value(base, 13) + 7)
            energy = set_parameter(base, 12, parameter_value(base, 12) + 3)
            attack_id = add(store, attack, 'Attack')
            expect('an Attack (13) difference is a distinct candidate',
                   attack_id != baseline and candidate_count(store) == 2)
            energy_id = add(store, energy, 'Energy')
            expect('Energy (12) is NOT treated as inert',
                   energy_id not in (baseline, attack_id) and candidate_count(store) == 3)

            # ---- (d) no real input difference is merged --------------------------------------
            skills = copy.deepcopy(base)
            human = next(u for u in skills['ownUnits'] if u.get('human'))
            human['skills'] = list(human['skills']) + [1]
            variants = {
                'encounter': dict(base, encounterId=int(base['encounterId']) + 1),
                'horizon': dict(base, tickLimit=int(base['tickLimit']) + 1),
                'difficulty': dict(base, defeatCount=int(base.get('defeatCount') or 0) + 1),
                'herbs': dict(base, holyHerbStock=int(base.get('holyHerbStock') or 0) + 1),
                'skills': skills,
            }
            for label, scenario in variants.items():
                expect(f'a {label} difference is a distinct strategy',
                       optimizer.equivalence_identity(scenario) != base_key)
            seeded = dict(base, mathSeed=123, libSeed=456)
            expect('the seed difference stays a non-difference (unchanged rule)',
                   optimizer.equivalence_identity(seeded) == base_key)
        finally:
            store.close()

        # ---- (c) legacy ids and runs are preserved and replayable ---------------------------
        legacy_path = Path(root) / 'legacy.sqlite'
        legacy = set_parameter(base, 21, 777)
        legacy_store = optimizer.Store(legacy_path, provenance())
        legacy_id = insert_legacy(legacy_store, legacy)
        legacy_store.close()

        reopened = optimizer.Store(legacy_path, provenance())
        try:
            stored = reopened.scenario(legacy_id)
            expect('the legacy id still resolves to its raw scenario', stored is not None)
            expect('the legacy scenario replays to its stored id',
                   stored is not None and optimizer.identity(stored) == legacy_id)

            changes_before = reopened.db.total_changes
            representative = reopened.equivalence_view()[0].get(legacy_id)
            expect('scanning the existing library is read-only',
                   reopened.db.total_changes == changes_before
                   and candidate_count(reopened) == 1 and run_count(reopened) == 0)
            expect('the compatibility index finds the legacy representative',
                   representative == legacy_id)

            variant = set_parameter(legacy, 20, 555)
            expect('the compatibility index matches the inert-only variant',
                   reopened.equivalent_candidate(variant) == legacy_id)
            expect('the legacy equivalent is refused, not duplicated',
                   add(reopened, variant, 'variant') == legacy_id
                   and candidate_count(reopened) == 1)

            reopened.record(legacy_id, 'validation', 0, one_result())
            expect('legacy runs remain readable', len(reopened.rows(legacy_id)) == 1)
        finally:
            reopened.close()

        # ---- (e) fine-tune combat axes still work -------------------------------------------
        for axis in ('gth', 'mov', 'hrt'):
            expect(f'fine-tune still refuses non-combat axis {axis!r}',
                   not strategy_finetune.is_combat_axis(axis))
        expect('fine-tune still accepts the Attack axis', strategy_finetune.is_combat_axis('atk'))

        # ---- ranking: legacy duplicates collapse to one strategy ----------------------------
        rank_path = Path(root) / 'rank.sqlite'
        rank = optimizer.Store(rank_path, provenance())
        try:
            cid_a = insert_legacy(rank, set_parameter(base, 22, 111))
            cid_b = insert_legacy(rank, set_parameter(base, 22, 222))
            expect('two inert-only legacy rows can both exist',
                   candidate_count(rank) == 2)
            rank.record(cid_a, 'validation', 0, one_result())
            rank.record(cid_b, 'validation', 0, one_result(1))

            live = optimizer.Optimizer.__new__(optimizer.Optimizer)
            live._candidate_cache = {}
            published = live._published_candidates(rank)
            by_id = {payload['id']: payload for payload in published}
            representative = rank.equivalence_view()[0][cid_a]
            expect('every legacy id is still published',
                   set(by_id) == {cid_a, cid_b})
            expect('both duplicates point at one representative',
                   by_id[cid_a]['equivalentTo'] == representative
                   and by_id[cid_b]['equivalentTo'] == representative)
            expect('the equivalence group reports its size',
                   by_id[cid_a]['equivalenceCount'] == 2
                   and by_id[cid_b]['equivalenceCount'] == 2)
            expect('lane ranking shows one strategy, not two',
                   optimizer.Optimizer._lane_view(published)['total'] == 1)
        finally:
            rank.close()

        # ---- (f) the index scan writes nothing ----------------------------------------------
        scan_path = Path(root) / 'scan.sqlite'
        scan = optimizer.Store(scan_path, provenance())
        try:
            insert_legacy(scan, base)
            changes_before = scan.db.total_changes
            counts_before = (candidate_count(scan), run_count(scan))
            scan.equivalence_view()
            scan.equivalent_candidate(set_parameter(base, 20, 1))
            expect('building the compatibility index changes no library row',
                   scan.db.total_changes == changes_before
                   and (candidate_count(scan), run_count(scan)) == counts_before)
        finally:
            scan.close()

    if FAILURES:
        print(f'\n{len(FAILURES)} equivalence check(s) failed')
        return 1
    print('\ninert-parameter equivalence, combat distinctness, legacy replay and read-only indexing passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
