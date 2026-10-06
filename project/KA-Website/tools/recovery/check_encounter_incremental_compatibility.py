"""Incremental frozen-intent admission must equal a full scoped rescan."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import strategy_encounter_search as search


class CompatibilityTests(unittest.TestCase):
    def make_coordinator(self, db):
        coord = search.Coordinator(path=Path('unused.sqlite'), encounter=14, revision='engine')
        coord._encounter_revision_scope = 'enemies'
        coord._compatibility = lambda: 'compatible'
        coord._mechanics_revision = lambda: 'mechanics'
        coord._compatible_search_digests = lambda: ['compatible']
        coord._intent = lambda connection, eid: json.loads(connection.execute(
            'SELECT intent_json FROM ea_experiment WHERE id=?', (eid,)).fetchone()[0])
        checked = []
        def matches(intent):
            checked.append(intent['id'])
            return intent.get('valid', False)
        coord._intent_matches_live = matches
        return coord, checked

    def add(self, db, eid, scope='enemies', valid=True):
        db.execute('INSERT INTO ea_experiment VALUES(?,?,?,?)',
                   (eid, 'mechanics', scope, json.dumps(dict(id=eid, compatibility='compatible', valid=valid))))
        db.commit()

    def test_local_append_only_validates_new_scoped_intents(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ea_experiment(id INTEGER PRIMARY KEY, mechanics_revision TEXT, encounter_revision TEXT, intent_json TEXT)')
        self.add(db, 1)
        self.add(db, 2, scope='other')
        coord, checked = self.make_coordinator(db)
        self.assertEqual(coord._compatible_experiment_ids(db), {1})
        checked.clear()
        self.add(db, 3, scope='other')
        self.assertEqual(coord._compatible_experiment_ids(db), {1})
        self.assertEqual(checked, [])
        self.add(db, 4)
        self.add(db, 5, valid=False)
        self.assertEqual(coord._compatible_experiment_ids(db), {1, 4})
        self.assertEqual(checked, [4, 5])
        coord._compatible_ids_cache = None
        self.assertEqual(coord._compatible_experiment_ids(db), {1, 4})
        coord._encounter_revision_scope = 'other'
        self.assertEqual(coord._compatible_experiment_ids(db), {2, 3})
        db.close()

    def test_external_correction_and_local_high_water_regression_rescan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.sqlite'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE ea_experiment(id INTEGER PRIMARY KEY, mechanics_revision TEXT, encounter_revision TEXT, intent_json TEXT)')
            self.add(db, 1)
            self.add(db, 2)
            coord, checked = self.make_coordinator(db)
            self.assertEqual(coord._compatible_experiment_ids(db), {1, 2})
            writer = sqlite3.connect(path)
            writer.execute('UPDATE ea_experiment SET intent_json=? WHERE id=1',
                           (json.dumps(dict(id=1, compatibility='compatible', valid=False)),))
            writer.commit()
            checked.clear()
            self.assertEqual(coord._compatible_experiment_ids(db), {2})
            self.assertEqual(checked, [1, 2])
            db.execute('DELETE FROM ea_experiment WHERE id=2')
            db.commit()
            self.assertEqual(coord._compatible_experiment_ids(db), set())
            writer.close()
            db.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
