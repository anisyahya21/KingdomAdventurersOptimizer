"""Persistent compact per-seed evidence for the strategy optimiser.

The `run` table is a replay window: `Store._apply` rolls the validation bank so only a bounded
sample of each candidate's results stays resident (the 64-run selection bank plus the latest
`MAX_SAMPLES-VALIDATION_RUNS`), which is what keeps the library small. That window is exactly
what the fine-tune pairing must *not* depend on: a paired threshold edge can need more than
`MAX_SAMPLES` shared seeds, and a reopened library must still see them.

This module owns the second, additive table. It keeps one compact row per accepted result,
keyed `(candidate, phase, ordinal)`, holding only what the chest/pairing readers actually
consume:

  * `seeds`             - the seed pair a paired comparison joins on,
  * `verdict`/`censored`- the resolved outcome, or unresolved,
  * `prizeCallbacks`    - the fallback/pre-verdict callback count,
  * `awardedChests`, `awardedBasis`, `pendingChests` - the minimal `rewardOutcome` fields.

Nothing here is invented: a censored or unknown run keeps NULL chest fields rather than a
fabricated zero, and no digest, trace, `behavior` or `progressMetrics` block is copied (the
lifetime counters keep using the live result inside `Store._apply`). Evidence is written in the
same transaction as the run row, *before* the rolling replay retention runs, so the compact
record survives the very prune that drops the replay example.

Retention: a candidate that is actually pruned has its evidence removed with it (see
`delete_candidate`); the rolling replay window never touches evidence, so a held candidate's
pairing history is preserved past the window. Backfill only re-reads run rows that are still
resident - rows a previous prune already dropped are gone and are never claimed reconstructed -
and it never changes a lifetime counter.

    python -c "import strategy_evidence"
"""
from __future__ import annotations

import json

#: Bumped when the compact column set or its meaning changes.
EVIDENCE_VERSION = 1

#: meta key that records a completed backfill for a library. Scoped to evidence only; it is
#: never read by the provenance/compatibility decision.
MARKER_KEY = 'evidenceBackfill'

#: Insert batch size for a backfill pass. Bounded so a large legacy library is folded in
#: predictable chunks instead of one unbounded statement.
BACKFILL_BATCH = 2000

COLUMNS = ('seeds', 'verdict', 'censored', 'prizeCallbacks',
           'awardedChests', 'awardedBasis', 'pendingChests')

ROW_SQL = ('SELECT phase,seeds,verdict,censored,prizeCallbacks,'
           'awardedChests,awardedBasis,pendingChests FROM evidence WHERE candidate=?')
INSERT_SQL = ('INSERT OR IGNORE INTO evidence(candidate,phase,ordinal,' + ','.join(COLUMNS) +
              ') VALUES (?,?,?,?,?,?,?,?,?,?)')


def store_schema(db):
    """Create the evidence table. Additive: it never touches the legacy `run` table."""
    db.execute('''CREATE TABLE IF NOT EXISTS evidence(
        candidate TEXT NOT NULL,
        phase TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        seeds TEXT,
        verdict INTEGER,
        censored INTEGER NOT NULL DEFAULT 0,
        prizeCallbacks INTEGER,
        awardedChests INTEGER,
        awardedBasis TEXT,
        pendingChests INTEGER,
        PRIMARY KEY(candidate,phase,ordinal)) WITHOUT ROWID''')


def _number(value):
    """The observed number, or None. A bool is not a chest count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def compact_result(result):
    """The seven compact values for one accepted result. Unknown stays NULL, never zero."""
    reward = result.get('rewardOutcome')
    reward = reward if isinstance(reward, dict) else {}
    seeds = result.get('seeds')
    if isinstance(seeds, (list, tuple)) and len(seeds) == 2:
        seeds = json.dumps([seeds[0], seeds[1]], separators=(',', ':'))
    else:
        seeds = None
    verdict = result.get('verdict')
    verdict = int(verdict) if isinstance(verdict, int) and not isinstance(verdict, bool) else None
    basis = reward.get('awardedBasis')
    basis = basis if isinstance(basis, str) else None
    return (seeds, verdict, 1 if result.get('censored') else 0,
            _number(result.get('prizeCallbacks')), _number(reward.get('awardedChests')),
            basis, _number(reward.get('pendingChests')))


def record(db, candidate, phase, ordinal, result):
    """Insert one compact row inside the caller's transaction. Idempotent (PK)."""
    return db.execute(INSERT_SQL, (candidate, phase, int(ordinal)) + compact_result(result))


def has(db, candidate, phase, ordinal):
    return db.execute('SELECT 1 FROM evidence WHERE candidate=? AND phase=? AND ordinal=?',
                      (candidate, phase, int(ordinal))).fetchone() is not None


def decode(seeds, verdict, censored, prizeCallbacks, awardedChests, awardedBasis, pendingChests):
    """The consumer-facing row, shaped like the fields `chest_count`/`potential_chests` read."""
    row = {'censored': bool(censored), 'verdict': None if verdict is None else int(verdict),
           'prizeCallbacks': prizeCallbacks}
    if seeds is not None:
        try:
            row['seeds'] = json.loads(seeds)
        except ValueError:
            pass
    reward = {}
    if awardedChests is not None:
        reward['awardedChests'] = awardedChests
    if pendingChests is not None:
        reward['pendingChests'] = pendingChests
    if awardedBasis is not None:
        reward['awardedBasis'] = awardedBasis
    if reward:
        row['rewardOutcome'] = reward
    return row


def select(db, candidate, phase=None):
    """`[(phase, decoded row)]` in ordinal order, optionally for one phase."""
    sql, args = ROW_SQL, [candidate]
    if phase is not None:
        sql += ' AND phase=?'
        args.append(phase)
    sql += ' ORDER BY phase,ordinal'
    return [(row[0], decode(*row[1:])) for row in db.execute(sql, args)]


def rows(db, candidate, phase=None):
    """Decoded evidence rows for one candidate (the compact analogue of `Store.rows`)."""
    return [row for _phase, row in select(db, candidate, phase)]


#: Candidates per `select_many` statement. Bounded so a program whose points number in the hundreds
#: still issues a small, fixed number of statements rather than one per candidate.
SELECT_MANY_CHUNK = 400


def select_many(db, candidates, chunk=SELECT_MANY_CHUNK):
    """`{candidate: [(phase, decoded row)]}` for many candidates, in ordinal order.

    `select` answers for one candidate, and the fine-tune reader asked it once per candidate: a
    whole program's points, then the next program's, all on the coordinator's worker-feeding path.
    The statements are identical apart from the id they bind, so they belong in one `IN (...)`
    list. A candidate with no stored evidence is simply absent from the mapping - a caller that
    needs a per-candidate answer asks for it with `select`/`rows` rather than reading an empty list
    as a measurement.
    """
    wanted = [cid for cid in dict.fromkeys(candidates) if cid]
    found = {}
    for start in range(0, len(wanted), chunk):
        batch = wanted[start:start+chunk]
        sql = ('SELECT candidate,phase,' + ','.join(COLUMNS) + ' FROM evidence WHERE candidate IN ('
               + ','.join('?' for _ in batch) + ') ORDER BY candidate,phase,ordinal')
        for row in db.execute(sql, batch):
            found.setdefault(row[0], []).append((row[1], decode(*row[2:])))
    return found


def indexed(db, candidate):
    """Stream compact observations with their original pairing identity, beyond replay retention."""
    sql = ('SELECT phase,ordinal,' + ','.join(COLUMNS) +
           ' FROM evidence WHERE candidate=? ORDER BY phase,ordinal')
    for row in db.execute(sql, (candidate,)):
        yield row[0], int(row[1]), decode(*row[2:])


def count(db, candidate=None, phase=None):
    sql, args = 'SELECT COUNT(*) FROM evidence', []
    clauses = []
    if candidate is not None:
        clauses.append('candidate=?')
        args.append(candidate)
    if phase is not None:
        clauses.append('phase=?')
        args.append(phase)
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    return db.execute(sql, args).fetchone()[0]


def delete_candidate(db, candidate):
    """Drop one pruned candidate's evidence. Held candidates are never touched."""
    return db.execute('DELETE FROM evidence WHERE candidate=?', (candidate,)).rowcount


def backfill(db, batch_size=BACKFILL_BATCH):
    """Copy still-retained `run` rows into `evidence`, once, idempotently.

    Only rows still resident in `run` are read, so a run a previous prune already evicted is
    never reconstructed and is never claimed to have been. Lifetime counters and every meta key
    that provenance/compatibility depends on are left untouched; the primary key makes a repeat
    pass a no-op. Returns how many evidence rows were newly inserted.
    """
    start = db.total_changes
    # Commit the whole pass here: a bounded batch is still one atomic unit of work, and a caller
    # cannot silently leave the connection holding an open write transaction.
    with db:
        cursor = db.execute('SELECT r.candidate,r.phase,r.ordinal,r.result FROM run r '
                            'JOIN candidate c ON c.id=r.candidate '
                            'ORDER BY r.candidate,r.phase,r.ordinal')
        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            payload = []
            for candidate, phase, ordinal, result in chunk:
                try:
                    decoded = json.loads(result)
                except ValueError:
                    continue
                payload.append((candidate, phase, int(ordinal)) + compact_result(decoded))
            if payload:
                db.executemany(INSERT_SQL, payload)
    return db.total_changes - start


def ensure_backfill(db, batch_size=BACKFILL_BATCH, version=EVIDENCE_VERSION):
    """Backfill a library once, recorded by an evidence-scoped meta marker."""
    row = db.execute('SELECT value FROM meta WHERE key=?', (MARKER_KEY,)).fetchone()
    if row is not None:
        return 0
    inserted = backfill(db, batch_size)
    with db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (MARKER_KEY, json.dumps(version)))
    return inserted
