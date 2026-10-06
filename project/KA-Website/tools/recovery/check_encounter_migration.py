"""Synthetic certificate contracts; never issues a production approval or touches live state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import strategy_encounter_migration as migration


class Store:
    def __init__(self, old):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        self.set('provenance', old)
        self.set('historicalCandidatesAndResults', {'preserved': True})
        self.compatible = False

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def get(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None


def run():
    # A small existing source file stands in for a pinned binary/evidence asset in these
    # synthetic unit tests. This is never written into the production certificate path.
    root = Path(__file__).resolve().parents[2]
    asset = Path(__file__).resolve()
    relative = asset.relative_to(root).as_posix()
    sha = hashlib.sha256(asset.read_bytes()).hexdigest()
    old = {'digest': 'old', 'missing': [], 'files': {
        'tools/recovery/combat_example.py': 'before', 'runtime-data/table.json': 'same'}}
    current = deepcopy(old)
    current['digest'] = 'new'
    current['files']['tools/recovery/combat_example.py'] = 'after'
    certificate = dict(schema=1, review='accepted', sourceFrom=migration.inventory(old),
                       sourceTo=migration.inventory(current), nativeBinaries={relative: sha},
                       evidence={'path': relative, 'sha256': sha})
    store = Store(old)
    checks = 0
    def check(value, message):
        nonlocal checks
        assert value, message
        checks += 1
    original_certificates = migration.CERTIFICATES
    try:
        with tempfile.TemporaryDirectory() as folder:
            migration.CERTIFICATES = (Path(folder) / 'synthetic-only.json',)
            check(not migration.preview(old, current)['eligible'], 'missing proof refuses')
            migration.CERTIFICATES[0].write_text(json.dumps(certificate))
            before = store.db.iterdump()
            snapshot = list(before)
            plan = migration.preview(old, current)
            check(plan['eligible'], 'exact synthetic transition eligible')
            check(list(store.db.iterdump()) == snapshot, 'preview has no writes')
            try:
                migration.apply(store, current, 'stale')
            except ValueError:
                checks += 1
            else:
                raise AssertionError('stale preview applied')
            migration.apply(store, current, plan['planId'])
            check(store.get('provenance') == old, 'historical provenance remains exact')
            check(store.get('historicalCandidatesAndResults') == {'preserved': True},
                  'unrelated state preserved')
            check(migration.applied(store, current), 'grant valid after reading persisted state')
            saved_grant = store.get(migration.KEY)
            store.db.commit()
            store.db.execute('BEGIN')
            store.set('activation-marker', {'pending': True})
            migration.apply(store, current, plan['planId'])
            store.db.rollback()
            check(store.get('activation-marker') is None and store.get(migration.KEY) == saved_grant,
                  'migration respects outer activation transaction rollback')
            changed = deepcopy(current)
            changed['files']['runtime-data/table.json'] = 'different'
            check(not migration.applied(store, changed), 'data mutation invalidates grant')
            changed = deepcopy(current)
            changed['files']['tools/recovery/combat_example.py'] = 'unreviewed'
            check(not migration.applied(store, changed), 'unreviewed source invalidates grant')
            changed = deepcopy(current)
            changed['files']['tools/recovery/strategy_optimizer.py'] = 'scheduler-only'
            check(migration.applied(store, changed), 'scheduler-only update preserves reviewed combat')
            bad = deepcopy(certificate)
            bad['nativeBinaries'][relative] = 'wrong'
            migration.CERTIFICATES[0].write_text(json.dumps(bad))
            check(not migration.applied(store, current), 'changed pinned asset fails closed')
            bad = deepcopy(certificate)
            bad['evidence']['sha256'] = 'wrong'
            migration.CERTIFICATES[0].write_text(json.dumps(bad))
            check(not migration.preview(old, current)['eligible'], 'missing parity evidence fails closed')
            bad = deepcopy(certificate)
            bad['reviewedRuntimeAssets'] = {relative: 'wrong'}
            migration.CERTIFICATES[0].write_text(json.dumps(bad))
            check(not migration.preview(old, current)['eligible'],
                  'changed native adapter or ABI invalidates grant')
            bad['reviewedRuntimeAssets'] = {relative: sha}
            migration.CERTIFICATES[0].write_text(json.dumps(bad))
            check(migration.preview(old, current)['eligible'], 'matching native runtime asset accepted')
    finally:
        migration.CERTIFICATES = original_certificates
        store.db.close()

    # Production allow-list regression: only the exact saved inventory -> current inventory pair
    # recorded in the new certificate is eligible. This uses fake provenance values but validates
    # the real reviewed certificate and its real, pinned runtime assets/evidence.
    reviewed_path = migration.TIMING_CERTIFICATE
    reviewed = json.loads(reviewed_path.read_text(encoding='utf-8'))
    saved = {'digest': 'saved-transition-fixture', 'missing': [],
             'files': deepcopy(reviewed['sourceFrom'])}
    latest = {'digest': 'current-transition-fixture', 'missing': [],
              'files': deepcopy(reviewed['sourceTo'])}
    migration.CERTIFICATES = (reviewed_path,)
    try:
        check(migration.preview(saved, latest)['eligible'],
              'exact reviewed saved-to-current transition is eligible')
        changed_source = deepcopy(saved)
        changed_source['files']['tools/recovery/strategy_optimizer_adapter.py'] = 'unreviewed'
        check(not migration.preview(changed_source, latest)['eligible'],
              'a neighboring saved adapter source is refused')
        changed_target = deepcopy(latest)
        changed_target['files']['tools/recovery/strategy_optimizer_adapter.py'] = 'unreviewed'
        check(not migration.preview(saved, changed_target)['eligible'],
              'a neighboring current adapter source is refused')
        reversed_transition = migration.preview(latest, saved)
        check(not reversed_transition['eligible'], 'reverse transition is refused')
    finally:
        migration.CERTIFICATES = original_certificates
    print(f'PASS {checks} migration guard checks; no production database changes')


if __name__ == '__main__':
    run()
