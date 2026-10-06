"""Check measured parent selection and invalidation of its coordinator hot-path cache."""
import json
import sqlite3

import strategy_students as students


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT, source TEXT, created INTEGER);
            CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER);
            CREATE TABLE lineage(candidate TEXT PRIMARY KEY, source TEXT);
        ''')
        self._scenario_cache = {}
        self.db.execute("INSERT INTO meta VALUES ('totalRuns', '2')")

    def scenario(self, cid):
        if cid not in self._scenario_cache:
            row = self.db.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
            self._scenario_cache[cid] = json.loads(row[0]) if row else None
        return self._scenario_cache[cid]

    def add(self, cid, created, mean):
        self.db.execute('INSERT INTO candidate VALUES (?,?,?,?)',
                        (cid, json.dumps({'allowed': True}), 'community', created))
        self.db.execute('INSERT INTO candidate_meta VALUES (?,2)', (cid,))
        self.db.execute('INSERT INTO lineage VALUES (?,?)', (cid, students.COMMUNITY_SOURCE))
        self.mean(cid, mean)

    def mean(self, cid, mean):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                        (f'aggregate:{cid}:discovery', json.dumps(
                            {'n': 4, 'chestCount': 4, 'chestSum': 4 * mean,
                             'chestMax': mean, 'potentialMax': 0})))


def main():
    store = Store()
    store.add('a', 1, 5)
    store.add('b', 2, 3)
    original = students.community_admits
    calls = []

    def admit(candidate, parent=None):
        calls.append(candidate)
        return bool(candidate['allowed']), []

    students.community_admits = admit
    try:
        assert students.community_parent(store, 2)[0] == 'a'
        first_calls = len(calls)
        assert students.community_parent(store, 2)[0] == 'a'
        assert len(calls) == first_calls, 'unchanged evidence must reuse the selected parent'

        store.mean('b', 7)
        store.db.execute("UPDATE meta SET value='3' WHERE key='totalRuns'")
        assert students.community_parent(store, 2)[0] == 'b', 'committed run must invalidate ranking'

        store.add('c', 3, 9)
        assert students.community_parent(store, 2)[0] == 'c', 'new candidate must invalidate ranking'

        store.db.execute("DELETE FROM candidate WHERE id='c'")
        store.db.execute("DELETE FROM candidate_meta WHERE id='c'")
        assert students.community_parent(store, 2)[0] == 'b', 'pruning must invalidate ranking'
        print('PASS community parent selection, cache reuse, run update, add and prune')
    finally:
        students.community_admits = original
        store.db.close()


if __name__ == '__main__':
    main()
