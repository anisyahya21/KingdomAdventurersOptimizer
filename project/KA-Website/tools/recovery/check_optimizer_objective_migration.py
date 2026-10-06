"""Part J: an existing library moves onto the lane objective without resimulating, and says what it
could not reconstruct.

The fixture is a library in the *old* shape: run rows are present, the per-candidate aggregates and the
archive are wiped (exactly what a pre-redesign library looks like to the new code), and only some rows
carry the stored-attack metrics. The migration must

  * rebuild every aggregate counter from the stored rows, exactly as `accumulate` would;
  * re-admit the archive under the new rule - including a complete bank with a ~20% win rate, which the
    old Wilson 0.80 gate refused;
  * report the split between rows that carry stored-attack metrics and rows that cannot;
  * leave the setup lane empty rather than fabricating stored-attack evidence;
  * be idempotent.

    python check_optimizer_objective_migration.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def result(verdict, awarded, pending, seeds, digest, progress=None):
    row = dict(verdict=verdict, censored=False, ticks=40, prizeCallbacks=pending, survivors=1,
               resourceUses=0, behavior=dict(attacks=1, heals=0, prizes=pending),
               seeds=list(seeds), digest=digest, elapsedSeconds=.01,
               rewardOutcome=dict(awardedChests=awarded, pendingChests=pending,
                                  awardedBasis='reward-entitlement-certificate'))
    if progress is not None:
        row['progressMetrics'] = progress
    return row


def build(path):
    """A library whose rows exist but whose derived counters and archive do not. Returns the ids."""
    scenario = dict(default_scenario(), tickLimit=40, encounterId=19)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        rare = store.add(scenario, 'Rare high-yield', 'mutation', stats(scenario))
        defeat = store.add(dict(scenario, defeatCount=0, note='defeat'), 'Defeat',
                           'mutation', stats(scenario))
        setup = store.add(dict(scenario, note='setup'), 'Setup', 'mutation', stats(scenario))
        for ordinal in range(optimizer.VALIDATION_RUNS):
            # A complete bank that wins ~20% of the time, with one 99-chest outlier.
            win = ordinal % 5 == 0
            store.record(rare, 'validation', ordinal,
                         result(1 if win else 2, 99 if ordinal == 0 else (2 if win else 0),
                                (99 if ordinal == 0 else (2 if win else 0)),
                                (ordinal, ordinal+1), f'rare-{ordinal}'))
        for ordinal in range(optimizer.VALIDATION_RUNS):
            store.record(defeat, 'validation', ordinal,
                         result(2, 0, 120, (ordinal, ordinal+1), f'defeat-{ordinal}'))
        # Only the eight setup rows carry the stored-attack metrics - the rest of the library cannot.
        for ordinal in range(optimizer.DISCOVERY_RUNS):
            store.record(setup, 'discovery', ordinal,
                         result(2, 0, 0, (ordinal, ordinal+1), f'setup-{ordinal}',
                                progress=dict(postDeathPrizes=0,
                                              commandsReleasedAfterDeathTargetingBoss=6,
                                              storedCommandsTargetingBossAtDeath=9,
                                              maxSimultaneousCommandsTargetingBoss=11,
                                              maxSimultaneousStoredCommands=14,
                                              commandsTargetingBoss=12,
                                              commandsTargetingBossReleased=12,
                                              storedTargetHoldersPeak=2,
                                              bossDeathTick=-1, bossLeavingTick=-1,
                                              postDeathBossLeavings=0,
                                              firstPostDeathCommandReleaseTick=-1)))
        # Strip every derived counter and the archive: the pre-redesign shape.
        store.db.execute("DELETE FROM meta WHERE key LIKE 'aggregate:%'")
        store.db.execute('DELETE FROM archive')
        store.db.execute("DELETE FROM meta WHERE key IN ('objectiveVersion','objectiveMigration')")
    ids = dict(rare=rare, defeat=defeat, setup=setup)
    store.close()
    return ids


def main():
    failures = []

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    with tempfile.TemporaryDirectory(prefix='ka-migrate-', ignore_cleanup_errors=True) as root:
        path = Path(root)/'migrate.sqlite'
        ids = build(path)
        store = optimizer.Store(path, provenance())
        before_version = store.get('objectiveVersion', 1)
        expect('a pre-redesign library reports generation 1', before_version == 1, str(before_version))
        migration = optimizer.migrate_objective(store)
        expect('the migration is versioned', migration['version'] == optimizer.OBJECTIVE_VERSION,
               str(migration))
        expect('it counts the rows it could not reconstruct',
               migration['runsWithoutProgressMetrics'] == 2*optimizer.VALIDATION_RUNS
               and migration['runsWithProgressMetrics'] == optimizer.DISCOVERY_RUNS,
               f'{migration["runsWithProgressMetrics"]} with / '
               f'{migration["runsWithoutProgressMetrics"]} without')
        expect('and names them as not reconstructable',
               any('stored-attack' in note for note in migration['notReconstructable']),
               str(migration['notReconstructable']))

        # ---- the aggregates are exactly what a fresh fold over the stored rows produces --------
        rows = list(store.db.execute('SELECT id, label, source, scenario FROM candidate'))
        rebuilt = True
        for row in rows:
            for phase in ('discovery', 'validation'):
                total = None
                for stored in store.db.execute(
                        'SELECT result FROM run WHERE candidate=? AND phase=? ORDER BY ordinal',
                        (row['id'], phase)):
                    total = optimizer.accumulate(total, json.loads(stored[0]))
                if (store.get(f'aggregate:{row["id"]}:{phase}') or None) != (total or None):
                    rebuilt = False
        expect('every aggregate equals a fresh fold over the stored rows', rebuilt)

        # ---- the archive admits the rare bank the old reliability gate refused ------------------
        cells = {row['candidate']: row['cell'] for row in store.archive()}
        rare, defeat, setup = ids['rare'], ids['defeat'], ids['setup']
        expect('the rare high-yield bank holds an archive cell', rare in cells,
               f'archive {cells}')

        # ---- lanes come from the stored evidence, and the setup lane stays honest --------------
        aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
        records = [optimizer.lane_record(row, optimizer.merge_aggregates(
            aggregates.get(f'aggregate:{row["id"]}:discovery'),
            aggregates.get(f'aggregate:{row["id"]}:validation'))) for row in rows]
        lanes = optimizer.elite_lanes(records)
        leaders = optimizer.lane_leaders(records, lanes)
        key = '19:0'
        expect('the potential lane leads with the defeat that built the most opportunity',
               lanes[key]['potential'][0] == defeat, f"potential {lanes[key]['potential']}")
        expect('the earned lane leads with the rare high-yield win',
               lanes[key]['earned'][0] == rare, f"earned {lanes[key]['earned']}")
        expect('only rows carrying the metrics enter the setup lane',
               lanes[key]['setup'] == [setup], f"setup {lanes[key]['setup']}")
        expect('the setup leader reports the causal chain it was ranked by',
               leaders[key]['setup']['metrics']['progress']['storedCommandsTargetingBossAtDeath'] == 9,
               str(leaders[key]['setup']['metrics']['progress']))

        # ---- idempotent -------------------------------------------------------------------------
        first_state = dict(
            version=store.get('objectiveVersion'), archive=store.archive(),
            aggregates={row[0]: row[1] for row in store.db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")})
        again = optimizer.migrate_objective(store)
        second_state = dict(
            version=store.get('objectiveVersion'), archive=store.archive(),
            aggregates={row[0]: row[1] for row in store.db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")})
        expect('running it again changes no derived state',
               first_state == second_state,
               'the second migration changed the aggregates or the archive')
        expect('and its counters agree with the first run',
               (again['runsWithProgressMetrics'], again['runsWithoutProgressMetrics'])
               == (migration['runsWithProgressMetrics'], migration['runsWithoutProgressMetrics']),
               f'{again["runsWithProgressMetrics"]}/{again["runsWithoutProgressMetrics"]} vs '
               f'{migration["runsWithProgressMetrics"]}/{migration["runsWithoutProgressMetrics"]}')
        store.close()
        import os
        try:
            os.remove(path)
        except OSError as exc:
            print(f'  note: the fixture file stayed locked after close ({exc})')

    print()
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  an existing library is reused, re-scored and versioned without resimulating a battle, '
          'and the setup lane stays empty where the metrics were never recorded')
    return 0


if __name__ == '__main__':
    sys.exit(main())
