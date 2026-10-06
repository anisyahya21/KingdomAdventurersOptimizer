"""MP-limited builds: from a low-MP observation to a budgeted, separately-measured correction.

The engine can use a Holy Herb and the observer can sample MP. This check is about the optimiser half
that did not exist: an ordinary no-item candidate reporting that its healer ran out of MP, and that
observation becoming a changed build with its own identity, its own declared bank and its own results.

Asserted here, on a real temporary library:

  * the watched units are the role-resolved DPS and healer (from the derived placement and the unit's
    own skills), never the first two units in roster order, and a build with no healer says so;
  * observation is separate from permission: the watch list is written into the *evaluation copy*
    only, authorises nothing, and needs no stock;
  * with no allowance configured nothing is created - the eligible parent is recorded and the payload
    says the retry awaits an item allowance;
  * with the TEST FIXTURE allowance (stock 1, max uses 1) one herb-enabled child is created, differing
    from its parent only in the item policy, with a different canonical identity and separate
    statistics, and the parent is untouched;
  * a second pass reuses the same child instead of creating a copy, and a parent that already declares
    a herb policy is never corrected again - so the operation cannot recurse into itself;
  * a threshold crossing at or after the run's own end is recorded as context, not as a reason to spend
    runs;
  * the child is measured through the existing declared-bank map, which stops offering it once its
    bank is spent, and the dispatch gate halts it when the allowance is absent;
  * the change is named from the real lineage: `MP recovery · <item policy> · from <parent producer>`.

    python check_optimizer_mp_recovery.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_mp_recovery as mp  # noqa: E402
import strategy_naming  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance  # noqa: E402

#: A real encounter id: the fixture scenarios are the frozen default fight, so the encounter must be
#: one the game has (0..19) rather than an invented number.
ENCOUNTER = 19
TEST_FIXTURE = dict(enabled=True, stock=1, maxUses=1, bank=8, maxChildren=4)


def scenario(*, healer=True, encounter=ENCOUNTER):
    """The frozen default scenario, item-free, optionally with its healer removed.

    A real validated scenario rather than a hand-written stub: the roles this check resolves are read
    from the same derived placement a production candidate is read from.
    """
    built = default_scenario()
    built['encounterId'] = encounter
    built['defeatCount'] = 0
    built['holyHerbStock'] = 0
    built.pop('holyHerbMaxUses', None)
    built.pop('holyHerbTriggerUnits', None)
    built['inputs'] = []
    built['note'] = 'MP-recovery fixture'
    if not healer:
        built['ownUnits'] = [unit for unit in built['ownUnits']
                             if not any(skill in (37, 107) for skill in unit.get('skills') or [])]
    return built


#: The names the observer reports, taken from the same role resolution a production run uses: the MP
#: block is keyed by the watched unit's own name, so a fixture that invented a short name would not
#: match the aggregate it is read back from.
_WATCHED = mp.watch_units(scenario())[0]
DPS_NAME = _WATCHED[0]
HEALER_NAME = _WATCHED[1]


def mp_block(*, low, tick, minimum, percent, run_end, name=None):
    """The MP block in the shape the observer publishes: named by the *watched* unit's own name."""
    return [dict(name=name or HEALER_NAME, identity=101, minimumMp=minimum,
                 minimumMpPercent=percent,
                 reachedLowMp=bool(low), firstLowMpTick=(tick if low else -1),
                 firstLowMpPhase=('after_fighters' if low else None), reachedZero=minimum == 0)]


def result(*, verdict, ticks, seeds, digest, earned=0, low=False, low_tick=10, minimum=10,
           percent=2, unresolved=False):
    """One recorded run carrying real MP telemetry, in the shape `accumulate` reads."""
    return dict(verdict=verdict, censored=False, ticks=ticks, prizeCallbacks=earned, retained=None,
                survivors=1, resourceUses=0, elapsedSeconds=.01,
                behavior=dict(heals=1, attacks=2, prizes=earned), seeds=list(seeds), digest=digest,
                mpMetrics=mp_block(low=low, tick=low_tick, minimum=minimum, percent=percent,
                                   run_end=ticks),
                rewardOutcome=dict(pendingChests=earned, awardedChests=earned, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def build_library(path, *, healer=True, runs=8, low=True, low_tick=10, run_ticks=200):
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario(healer=healer)))
        parent = store.add(scenario(healer=healer), 'Community-shaped build', 'supplied', {})
    for ordinal in range(runs):
        store.record(parent, 'discovery', ordinal,
                     result(verdict=2, ticks=run_ticks, seeds=(100 + ordinal, 200 + ordinal),
                            digest=f'r{ordinal}', earned=0, low=low, low_tick=low_tick))
    store.close()
    return parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-mp-recovery-check-1', checks=[], cases=0)
    failures = []

    def record_case(title, detail):
        entry = dict(detail)
        entry['name'] = title
        report['checks'].append(entry)
        report['cases'] += 1
        print(f'  OK {title}: {json.dumps(detail, sort_keys=True)}')

    def check(condition, message):
        if not condition:
            failures.append(message)

    # ---- (1) role-resolved watching, from the derived placement rather than roster order ----------
    names, detail = mp.watch_units(scenario())
    check(names == [DPS_NAME, HEALER_NAME], f'the watch list reads {names}')
    check(detail is None, f'a complete build reported {detail!r}')
    fodder_first = scenario()
    fodder_first['ownUnits'] = list(reversed(fodder_first['ownUnits']))
    check(mp.watch_units(fodder_first)[0] == [DPS_NAME, HEALER_NAME],
          'reordering the roster changed which units are watched')
    _dps_only, missing = mp.watch_units(scenario(healer=False))
    check('no healer' in (missing or ''), f'a healer-less build reported {missing!r}')
    check(mp.observation_patch(scenario())['mpWatchUnits'] == [DPS_NAME, HEALER_NAME],
          'the observation patch does not carry the resolved watch list')
    with_policy = dict(scenario(), holyHerbTriggerUnits=['Healer'], holyHerbMaxUses=1,
                       holyHerbStock=1)
    check(mp.observation_patch(with_policy) == {},
          'a build that declares its own trigger units was given a different watch list')
    record_case('the-watch-list-is-role-resolved', dict(watched=names, healerless=missing))

    # ---- (2) the allowance: default off, validated, persisted ------------------------------------
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        work = pathlib.Path(work)
        path = work / 'mp-recovery.sqlite'
        parent = build_library(path)
        store = optimizer.Store(path, provenance())
        try:
            default = mp.setting(store)
            check(default['enabled'] is False and default['effective'] is False,
                  f'the default allowance reads {default}')
            refused = {
                'cap above stock': mp.configure(store, dict(enabled=True, stock=1, maxUses=2)),
                'enabled with no cap': mp.configure(store, dict(enabled=True, stock=1, maxUses=0)),
                'negative stock': mp.configure(store, dict(stock=-1)),
                'unknown field': mp.configure(store, dict(quantity=3)),
            }
            check(all(value.get('ok') is False for value in refused.values()),
                  f'a nonsensical allowance was accepted: {refused}')
            record_case('a-meaningless-allowance-is-refused',
                        {key: value.get('error') for key, value in refused.items()})

            # ---- (3) nothing is created without an allowance -------------------------------------
            first = mp.advance(store)
            check(not first['created'], f'a correction was created with no allowance: {first}')
            check(first['awaiting'], 'low MP was not reported as awaiting an allowance')
            switched_off = first['awaiting'][0].get('reason') or ''
            check('switched off' in switched_off, f'the reason reads {switched_off!r}')
            check(store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0] == 1,
                  'a candidate appeared without an allowance')
            # Switched on but with no quantity declared: the exact state the user asked the surface to
            # name, because it is the one that could otherwise look like it was working.
            mp.configure(store, dict(enabled=True, stock=0, maxUses=0))
            waiting = mp.advance(store)
            reason = (waiting['awaiting'] or [{}])[0].get('reason') or ''
            check(not waiting['created'], f'a correction was created with no quantity: {waiting}')
            check('awaits an item allowance' in reason, f'the reason reads {reason!r}')
            check(mp.status(store)['awaitingAllowance'] is False or True, 'status flag changed')
            record_case('no-allowance-means-no-work',
                        dict(switchedOff=switched_off, awaitingQuantity=reason,
                             decision=waiting['decision']))
            mp.configure(store, dict(enabled=False, stock=0, maxUses=0))

            # ---- (4) the test fixture creates one correction, named and identified separately -----
            applied = mp.configure(store, TEST_FIXTURE)
            check(applied.get('ok') is True, f'the fixture allowance was refused: {applied}')
            second = mp.advance(store)
            check(len(second['created']) == 1, f'the fixture created {second["created"]}')
            child = second['created'][0]['child']
            parent_after = store.scenario(parent)
            child_scenario = store.scenario(child)
            check(child != parent, 'the correction reused the parent id')
            check(optimizer.identity(parent_after) == optimizer.identity(store.scenario(parent)),
                  'the stored parent scenario changed')
            check(int(parent_after.get('holyHerbStock') or 0) == 0
                  and int(parent_after.get('holyHerbMaxUses') or 0) == 0
                  and not parent_after.get('holyHerbTriggerUnits'),
                  'the parent gained an item policy')
            check(int(child_scenario.get('holyHerbStock')) == 1
                  and int(child_scenario.get('holyHerbMaxUses')) == 1
                  and child_scenario.get('holyHerbTriggerUnits') == [HEALER_NAME],
                  f'the child policy reads {mp.herb_policy(child_scenario)}')
            differing = {key for key in set(parent_after) | set(child_scenario)
                         if parent_after.get(key) != child_scenario.get(key)}
            check(differing <= {'holyHerbStock', 'holyHerbMaxUses', 'holyHerbTriggerUnits', 'note'},
                  f'the child differs from its parent outside the item policy: {sorted(differing)}')
            source = store.db.execute('SELECT source,parent FROM lineage WHERE candidate=?',
                                      (child,)).fetchone()
            check(source['source'] == mp.SOURCE and source['parent'] == parent,
                  f'the child lineage reads {(source["source"], source["parent"])}')
            named = strategy_naming.name_for(child, store.lineage(), {cid: store.scenario(cid)
                                                                     for cid in (parent, child)})
            check(named['name'].startswith('MP recovery · '),
                  f'the correction is not attributed to its own operation: {named["name"]!r}')
            check('Holy Herb stock 0->1' in named['name']
                  and 'herb watch' in named['name'].lower(),
                  f'the name does not state the item policy: {named["name"]!r}')
            check(named['name'].endswith('· from Baseline'),
                  f'the name does not state its parent provenance: {named["name"]!r}')
            record_case('the-correction-is-a-separate-build',
                        dict(parent=parent, child=child, name=named['name'],
                             differing=sorted(differing)))

            # ---- (5) reuse, never a copy, and never from its own child ---------------------------
            third = mp.advance(store)
            check(not third['created'] and third['reused'],
                  f'the second pass created another copy: {third}')
            check(store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0] == 2,
                  'the second pass added a candidate')
            children = [row[0] for row in store.db.execute(
                'SELECT id FROM candidate_meta WHERE encounter=?', (ENCOUNTER,))]
            herb_children = [cid for cid in children
                             if int(store.scenario(cid).get('holyHerbMaxUses') or 0) > 0]
            check(herb_children == [child], 'the herb-enabled build is not unique')
            record_case('a-repeated-observation-reuses-the-request',
                        dict(reused=len(third['reused']), children=len(herb_children)))

            # ---- (6) the declared bank is offered, then stops -------------------------------------
            offered = mp.banks(store)
            check(offered.get(child) == TEST_FIXTURE['bank'],
                  f'the child bank reads {offered.get(child)}')
            for ordinal in range(TEST_FIXTURE['bank']):
                store.record(child, 'validation', ordinal,
                             result(verdict=1, ticks=180, seeds=(900 + ordinal, 901 + ordinal),
                                    digest=f'c{ordinal}', earned=3, low=False))
            check(mp.banks(store) == {}, 'a spent bank was offered again')
            measurement = mp.status(store)['requests'][0]
            check(measurement['child']['earnedSamples'] == TEST_FIXTURE['bank']
                  and measurement['child']['meanEarned'] == 3.0,
                  f'the child readings read {measurement["child"]}')
            check(measurement['status'] == 'complete', f'the request status is {measurement["status"]}')
            record_case('the-child-is-measured-on-its-own-bank',
                        dict(bank=TEST_FIXTURE['bank'], observed=measurement['observed'],
                             childMean=measurement['child']['meanEarned'],
                             parentMean=measurement['parent']['meanEarned'],
                             status=measurement['status']))
        finally:
            store.close()

        # ---- (7) a crossing only after the battle had ended is context, not work ----------------
        late = work / 'mp-recovery-late.sqlite'
        build_library(late, low=True, low_tick=250, run_ticks=200)
        store = optimizer.Store(late, provenance())
        try:
            mp.configure(store, TEST_FIXTURE)
            summary = mp.advance(store)
            check(not summary['created'], f'a correction was created from a late crossing: {summary}')
            reason = (summary['awaiting'] or [{}])[0].get('reason') or ''
            check('at or after the run ended' in reason, f'the reason reads {reason!r}')
            record_case('a-crossing-after-the-end-is-context', dict(reason=reason))
        finally:
            store.close()

        # ---- (8) the dispatch gate: no allowance, no runs ---------------------------------------
        gate_off = optimizer.dispatch_gate({'discovery': 1.0}, set(), set(), mp_recovery_ids={'x'},
                                           mp_recovery_on=False)
        gate_on = optimizer.dispatch_gate({'discovery': 1.0}, set(), set(), mp_recovery_ids={'x'},
                                          mp_recovery_on=True)
        check(gate_off('x') is False, 'an MP-recovery child ran with no allowance configured')
        check(gate_on('x') is True, 'a configured allowance did not authorise its own child')
        check(gate_off('other') is True,
              'an ordinary build was halted by the MP-recovery gate')
        record_case('the-allowance-is-the-permission', dict(withAllowance=gate_on('x'),
                                                            withoutAllowance=gate_off('x'),
                                                            ordinaryBuild=gate_off('other')))

        # ---- (9) the host publishes it, and the command reaches the allowance --------------------
        bridge = desktop.Bridge(late)
        try:
            import time
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and bridge.status().get('state') == 'Opening library':
                time.sleep(.05)
            accepted = bridge.command('mp_recovery', dict(enabled=True, stock=1, maxUses=1,
                                                          bank=8, maxChildren=2))
            check(accepted.get('ok') is True, f'the host refused the allowance: {accepted}')
            published = bridge.status().get('mpRecovery')
            check(isinstance(published, dict) and published.get('setting', {}).get('stock') == 1,
                  f'the published allowance reads {published}')
            record_case('the-host-publishes-the-allowance',
                        dict(stock=published['setting']['stock'],
                             maxUses=published['setting']['maxUses'],
                             threshold=published['thresholdPercent']))
        finally:
            bridge.close()

        # ---- (10) a scheduled build survives the population cap ---------------------------------
        # The defect the real pilot found: an MP-recovery child (or any build holding a declared bank)
        # was created, then pruned by the very next proposal on a saturated encounter. Pruning deletes
        # the candidate row and deliberately keeps the lineage, so the request stayed in the ledger at
        # `observed 0/16` for ever. The caps are lowered here so the same path is exercised in
        # milliseconds instead of by filling a library.
        cap_path = work / 'mp-recovery-cap.sqlite'
        build_library(cap_path)
        saved = (optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL)
        optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL = 3, 4
        try:
            store = optimizer.Store(cap_path, provenance())
            try:
                for index in range(3):
                    scenario_value = dict(scenario(), note=f'filler {index}')
                    scenario_value['ownUnits'][0]['parameters'][13]['rawValue'] += index + 1
                    store.add_child(scenario_value, f'filler {index}', lambda: {}, None, 'test',
                                    'atk', f'atk+{index}', 'community', index)
                same, total = store.pool_size(ENCOUNTER)
                check(same >= 3 or total >= 4, f'the cap was not reached: {(same, total)}')
                mp.configure(store, TEST_FIXTURE)
                summary = mp.advance(store)
                check(len(summary['created']) == 1, f'no guard child was created: {summary}')
                guard = summary['created'][0]['child']
                # One more proposal while the encounter is at its cap. Without the protection this is
                # exactly the call that deleted the guard.
                sibling = dict(scenario(), note='sibling after the guard')
                sibling['ownUnits'][0]['parameters'][13]['rawValue'] += 7
                try:
                    store.add_child(sibling, 'sibling', lambda: {}, None, 'test', 'atk',
                                    'atk+7', 'community', 900)
                except ValueError:
                    pass  # every replaceable slot protected: a refusal, never a silent deletion
                kept = bool(store.db.execute('SELECT 1 FROM candidate WHERE id=?', (guard,)).fetchone())
                meta = bool(store.db.execute('SELECT 1 FROM candidate_meta WHERE id=?',
                                             (guard,)).fetchone())
                check(kept and meta,
                      'a build holding a declared bank was pruned before it could be measured')
                check(mp.banks(store).get(guard) == TEST_FIXTURE['bank'],
                      'the guard lost its declared bank')
                record_case('a-declared-bank-survives-the-population-cap',
                            dict(guard=guard, kept=kept, meta=meta,
                                 why='the pilot measured this losing two children at observed 0/16'))
            finally:
                store.close()
        finally:
            optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL = saved

    print(f'MP recovery checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
