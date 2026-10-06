"""Verify the overview's per-encounter aggregates against the stored library.

The encounter cards show three numbers per encounter/difficulty. This harness recomputes them from
the raw `run` rows by an independent route and requires the coordinator's own answer to match:

  * ATTEMPTS                    - every stored simulation for that encounter/difficulty;
  * HIGHEST POTENTIAL CHESTS    - max opportunity over RESOLVED LOSSES only;
  * HIGHEST CHESTS EARNED       - max released chests over WINS only, via `chest_count`.

It also proves the exclusion rule directly (dropping the censored rows cannot change either maximum),
that a combination with no matching runs reports None rather than raising, and it prints the full
5 x 4 table.

    python check_encounter_aggregates.py            # the live library
    python check_encounter_aggregates.py --library path.sqlite
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

DEFAULT_LIBRARY = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / \
    'KingdomAdventurersOptimizer' / 'strategies.sqlite'


class Reader:
    """The raw rows, so the aggregate can be recomputed without the coordinator's code."""

    def __init__(self, path):
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row

    def runs(self, include_censored=True):
        for row in self.db.execute('select c.scenario as scenario, r.result as result '
                                   'from run r join candidate c on c.id = r.candidate'):
            scenario, result = json.loads(row['scenario']), json.loads(row['result'])
            if not include_censored and (result.get('censored') or result.get('verdict') is None):
                continue
            yield int(scenario['encounterId']), int(scenario.get('defeatCount') or 0), result


def independent(path, include_censored=True):
    """Attempts, best defeated opportunity and best winning chests, recomputed from the rows."""
    totals = {}
    for encounter_id, defeat_count, result in Reader(path).runs(include_censored):
        key = f'{encounter_id}:{defeat_count}'
        entry = totals.setdefault(key, dict(attempts=0, potential=None, earned=None))
        entry['attempts'] += 1
        if result.get('censored') or result.get('verdict') is None:
            continue
        if result['verdict'] == 2:
            reward = result.get('rewardOutcome') or {}
            value = reward.get('pendingChests')
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                value = result.get('prizeCallbacks')
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry['potential'] = int(value) if entry['potential'] is None \
                    else max(entry['potential'], int(value))
        elif result['verdict'] == 1:
            # `chest_count` is the canonical gate: certified award, else queued at the verdict.
            value = optimizer.chest_count(result)[0]
            if value is not None:
                entry['earned'] = value if entry['earned'] is None else max(entry['earned'], value)
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    args = parser.parse_args()

    store = optimizer.Store(args.library, provenance())
    reported = optimizer.encounter_stats(store)
    total_runs = store.get('totalRuns') or 0
    expected = independent(args.library)
    without_censored = independent(args.library, include_censored=False)
    campaigns = optimizer.encounter_campaigns(store.get('encounters') or
                                             optimizer.encounter_catalogue())
    store.close()

    failures = []
    # The persisted lifetime record counts every accepted run; the surviving `run` rows are what is
    # left after candidate pruning and the rolling validation window. So on a library that has lost
    # rows the independent recount is a LOWER BOUND, and only a library that has never lost a row can
    # require exact equality. `sum(lifetimeAttempts) == totalRuns` holds either way, because the
    # counter is written in the same transaction as the run and is never decremented.
    surviving = sum(want['attempts'] for want in expected.values())
    lost = total_runs - surviving
    if lost < 0:
        failures.append(f'{surviving} surviving rows exceed totalRuns {total_runs}')
    if sum(entry['lifetimeAttempts'] for entry in reported.values()) != total_runs:
        failures.append(f"sum(lifetimeAttempts)={sum(entry['lifetimeAttempts'] for entry in reported.values())}"
                        f' != totalRuns={total_runs}')

    def compare(key, field, value, mine, note=''):
        """The stored reading against the independent one: exact, or a lower bound once rows are gone.

        A pruned row can have held the best reading (or the largest attempt count), so on a library
        that has lost rows the independent recount is a floor; it can never be exceeded.
        """
        if lost:
            if mine is not None and (value is None or value < mine):
                failures.append(f'{key}.{field}: coordinator {value!r} < surviving {mine!r}{note}')
        elif value != mine:
            failures.append(f'{key}.{field}: coordinator {value!r} != independent {mine!r}{note}')

    for key, want in expected.items():
        have = reported.get(key)
        if have is None:
            failures.append(f'{key}: missing from the coordinator output')
            continue
        # The coordinator reports the PERSISTED lifetime record (never a recount of surviving rows),
        # so the field names differ from the independent path on purpose.
        for field, mine in (('lifetimeAttempts', want['attempts']),
                            ('highestPotentialChests', want['potential']),
                            ('highestChestsEarned', want['earned'])):
            compare(key, field, have.get(field), mine)
        clean = without_censored.get(key, dict(attempts=0, potential=None, earned=None))
        compare(key, 'highestPotentialChests', have.get('highestPotentialChests'), clean['potential'],
                ' (unresolved rows must not change the loss maximum)')
        compare(key, 'highestChestsEarned', have.get('highestChestsEarned'), clean['earned'],
                ' (unresolved rows must not change the win maximum)')
    for key in reported:
        if key not in expected:
            failures.append(f'{key}: reported but no run rows back it')
    check_field_names(reported, failures)

    # A combination with no runs must read as empty, not as an error or a zero.
    empty_store = store_stub()
    empty = optimizer.encounter_stats(empty_store)
    empty_store.close()
    if empty or any(entry['highestPotentialChests'] is not None
                    or entry['highestChestsEarned'] is not None for entry in empty.values()):
        failures.append('an empty library produced a chest reading')

    print(f'library {args.library}')
    seen = Counter()
    for campaign in campaigns:
        print(f"  {campaign['title']}")
        for difficulty in campaign['difficulties']:
            key = f"{difficulty['encounterId']}:0"
            entry = reported.get(key) or dict(lifetimeAttempts=0, lifetimeWins=0, lifetimeLosses=0,
                                              lifetimeNoVerdict=0,
                                              highestPotentialChests=None,
                                              highestChestsEarned=None)
            seen[key] += 1
            print(f"    {difficulty['label']:<28} id={difficulty['encounterId']:<3} "
                  f"attempts={entry['lifetimeAttempts']:<5} "
                  f"potential={entry['highestPotentialChests'] if entry['highestPotentialChests'] is not None else '—':<5} "
                  f"earned={entry['highestChestsEarned'] if entry['highestChestsEarned'] is not None else '—':<5} "
                  f"W/L/U={entry['lifetimeWins']}/{entry['lifetimeLosses']}/{entry['lifetimeNoVerdict']}")
    print(f'  {len(reported)} encounter/difficulty keys with runs; '
          f'{sum(entry["lifetimeAttempts"] for entry in reported.values())} attempts total')
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print('  attempts, loss opportunity and winning chests all match an independent recount, '
          'and unresolved runs contaminate neither maximum')
    return 0


def store_stub():
    """A store over an empty database, to prove an empty combination renders as empty."""
    import tempfile
    import strategy_optimizer as module
    path = Path(tempfile.mkdtemp(prefix='ka-empty-library-')) / 'empty.sqlite'
    return module.Store(path, provenance())


# The overview used to read `attempts`/`wins`/`losses`/`unresolved`, which are not the fields the
# coordinator persists: on a saturated library those rendered 0 while the lifetime counters were in
# the tens of thousands. Both halves of that mismatch - the payload and the page that reads it - are
# pinned here, so neither can drift back on its own.
AUTHORITATIVE_LIFETIME_FIELDS = ('lifetimeAttempts', 'lifetimeWins', 'lifetimeLosses',
                                 'lifetimeNoVerdict', 'highestPotentialChests', 'highestChestsEarned')
OBSOLETE_OVERVIEW_FIELDS = ('attempts', 'wins', 'losses', 'unresolved')


def check_field_names(reported, failures):
    """The published record carries the lifetime names, and the page reads exactly those."""
    for key, entry in reported.items():
        for field in OBSOLETE_OVERVIEW_FIELDS:
            if field in entry:
                failures.append(f'{key}: published record still carries the obsolete field {field!r}')
        for field in AUTHORITATIVE_LIFETIME_FIELDS:
            if field not in entry:
                failures.append(f'{key}: published record is missing {field!r}')
    page = (HERE.parents[1] / 'artifacts' / 'kingdom-adventures' / 'src' / 'pages' /
            'strategy-optimizer-desktop.tsx')
    source = page.read_text(encoding='utf-8')
    for field in OBSOLETE_OVERVIEW_FIELDS:
        if f'stat?.{field}' in source or f'stat.{field}' in source:
            failures.append(f'the desktop overview reads the obsolete stat field {field!r}')
    for field in ('lifetimeAttempts', 'lifetimeWins', 'lifetimeLosses', 'lifetimeNoVerdict'):
        if f'stat?.{field}' not in source:
            failures.append(f'the desktop overview does not read {field!r}')
    # The retired second producer of the obsolete names is gone from the module; anything that
    # reintroduces it would have to reintroduce this assertion too.
    if '_legacy_encounter_stats' in (HERE / 'strategy_optimizer.py').read_text(encoding='utf-8'):
        failures.append('the coordinator still defines a second encounter-stats producer')
    print(f'  field names: {len(reported)} published records carry only the lifetime names, '
          f'and {page.name} reads {", ".join(AUTHORITATIVE_LIFETIME_FIELDS[:4])}')


if __name__ == '__main__':
    sys.exit(main())
