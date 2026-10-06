"""The stored-attack observer must not move a single stored digest.

The objective redesign added two *read-only* observers to the canonical Python engine (the
stored-attack progress watch) plus an optional release observer on the skill-command queue. Neither
can change a battle, but "cannot" is a claim, so this replays stored runs from a real library through
both backends and requires the digest the library already holds:

  * Python canonical engine vs the stored digest;
  * native kernel vs the stored digest (the production bulk path);
  * the two backends against each other.

It also prints the exact old -> new provenance file hashes, which is the evidence `same_simulator`
needs before a library written by the earlier build may be reused instead of refused.

    python check_simulator_compat.py [--library PATH] [--runs 12] [--json OUT]
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402


def sample_rows(store, count):
    """Stored runs spread across candidates and horizons, newest candidate last."""
    rows = list(store.db.execute(
        'SELECT r.candidate AS candidate, r.phase AS phase, r.ordinal AS ordinal, '
        'r.result AS result, c.scenario AS scenario FROM run r JOIN candidate c ON c.id=r.candidate '
        'ORDER BY r.candidate, r.phase, r.ordinal'))
    if not rows:
        return []
    step = max(1, len(rows)//count)
    return [dict(row) for row in rows[::step][:count]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path(
        r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy\KA-Website'
        r'\strategiespostrust1000tickslimit.sqlite'))
    parser.add_argument('--runs', type=int, default=12)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix='ka-compat-') as root:
        path = Path(root)/'compat.sqlite'
        shutil.copyfile(args.library, path)
        store = optimizer.Store.__new__(optimizer.Store)
        store.path = path
        import sqlite3
        store.db = sqlite3.connect(path)
        store.db.row_factory = sqlite3.Row
        stored_provenance = store.get('provenance') or {}
        rows = sample_rows(store, args.runs)
        store.db.close()

    failures = []
    checked = []
    for row in rows:
        scenario = json.loads(row['scenario'])
        stored_digest = json.loads(row['result'])['digest']
        seeds = json.loads(row['result'])['seeds']
        py = native.simulate_compact(dict(scenario), tuple(seeds), backend='python')
        na = native.simulate_compact(dict(scenario), tuple(seeds), backend='native')
        ok = py['digest'] == stored_digest and na['digest'] == stored_digest
        if not ok:
            failures.append(f"{row['candidate'][:8]}:{row['phase']}:{row['ordinal']} "
                            f"stored {stored_digest[:12]} python {py['digest'][:12]} "
                            f"native {na['digest'][:12]}")
        checked.append(dict(candidate=row['candidate'][:8], phase=row['phase'],
                            ordinal=row['ordinal'], digest=stored_digest,
                            python=py['digest'], native=na['digest'],
                            progress=py.get('progressMetrics')))

    current = provenance()
    pairs = []
    for key in sorted(set(stored_provenance.get('files', {})) | set(current['files'])):
        before = stored_provenance.get('files', {}).get(key)
        after = current['files'].get(key)
        if before != after:
            pairs.append(dict(file=key, stored=before, current=after))

    print(f'library {args.library.name}: replayed {len(checked)} stored runs on both backends')
    for row in checked:
        print(f"  {row['candidate']} {row['phase']:<10} #{row['ordinal']:<3} "
              f"digest {row['digest'][:12]} "
              f"(python {'ok' if row['python'] == row['digest'] else 'MISMATCH'}, "
              f"native {'ok' if row['native'] == row['digest'] else 'MISMATCH'})")
    print('provenance files that differ from the library\'s stored build:')
    for pair in pairs:
        print(f"  {pair['file']}")
        print(f"    stored  {pair['stored']}")
        print(f"    current {pair['current']}")
    if failures:
        print('FAILURES:')
        for row in failures[:8]:
            print('  ' + row)
        return 1
    print('  every replayed digest is byte-identical on the Python engine and on the native kernel, '
          'so the new observers are digest-neutral')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(checked=checked, provenancePairs=pairs), indent=1,
                                        default=str), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
