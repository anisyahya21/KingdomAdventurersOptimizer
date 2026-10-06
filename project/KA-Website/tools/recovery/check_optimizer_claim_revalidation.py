"""Migration of earlier confirmation claims: a fixed judge does not retroactively bless a claim.

The confirmation arithmetic was wrong in three separate ways while it was being built: a nominee could
be frozen against itself, a window could be reported complete when only its favourable pairs survived,
and the means it published were lifetime means under field names that said "window". A library written
under that code therefore holds `confirmed` entries that no longer stand up. Relabelling them as valid
because the code is fixed now would be exactly the mistake this check exists to prevent.

What is asserted here:

  * a claim is re-read against **its own frozen evidence** - the confirmation plan, its frozen
    comparator, its fresh window, its completed-versus-requested counts and its paired sample count;
  * a claim with no frozen comparator, a missing/never-completed window, a self-comparison or fewer
    paired samples than the floor is marked `needs_revalidation`, never left standing;
  * a claim that does carry the whole proof stays supported;
  * an encounter whose only claims are withdrawn loses its confirmed badge (the status becomes
    `claims_need_revalidation`), and the published `confirmed` list is empty;
  * a mixed encounter publishes only the claims that stand up;
  * nothing is deleted: the claim, its plan and its runs stay in the record with the reason it was
    withdrawn, and the re-check is idempotent across restarts.

    python check_optimizer_claim_revalidation.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_breakthrough as breakthrough  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 137


def scenario(marker=0):
    return dict(encounterId=ENCOUNTER, defeatCount=0, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], parameters={'13': {'rawValue': 100 + marker}})],
                enemies=[])


def proof(*, comparator='comparator-1', nominee='nominee-1', paired=64, completed=448, requested=448,
          frozen=True):
    """The result block the fixed confirmation writes, or a deliberately damaged version of it."""
    result = dict(delta=dict(mean=1.5, lower=0.4, upper=2.6, n=paired, paired=paired),
                  paired=paired, requestedSamples=requested, completedSamples=completed,
                  unresolvedSamples=requested - completed, nomineeMean=4.0, incumbentMean=2.5,
                  comparator=comparator, freshWindow=[64, 512], supports=True, incomplete=False)
    if not frozen:
        result.pop('comparator')
        result.pop('freshWindow')
        result.pop('nomineeMean')
    return result


def record_with(path, *, plan_status='confirmed', result=None, incumbent='comparator-1',
                nominee='nominee-1', status='confirmed_improvement', claims=None):
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario()))
    state = breakthrough.load(store)
    record = breakthrough.encounter_state(state, ENCOUNTER)
    record['plans'] = [dict(id='c:137:1:B', kind='confirmation', status=plan_status,
                            hypothesis='h:137:prior:atk+spd', encounter=ENCOUNTER,
                            nominee=nominee, incumbent=incumbent, developmentBank=64, bank=512,
                            arms=[dict(name='A', candidate=incumbent), dict(name='N', candidate=nominee)],
                            result=result if result is not None else proof())]
    record['confirmed'] = list(claims if claims is not None else
                               [dict(candidate=nominee, hypothesis='h:137:prior:atk+spd',
                                     delta=dict(mean=1.5, n=64), screening='e:137:1',
                                     confirmation='c:137:1:B')])
    record['status'] = status
    breakthrough.save(store, state)
    return store, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-claim-revalidation-check-1', checks=[], cases=0)
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

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        work = pathlib.Path(work)

        # (1) Every way a claim can fail its own re-read.
        cases = [
            ('a self-comparison', dict(incumbent='nominee-1', nominee='nominee-1')),
            ('no frozen comparator', dict(result=proof(frozen=False))),
            ('an incomplete window', dict(result=proof(completed=100, requested=448))),
            ('too few paired samples', dict(result=proof(paired=8))),
            ('a plan that is not a pass', dict(plan_status='not_promoted')),
        ]
        rejected = {}
        for index, (label, overrides) in enumerate(cases):
            store, record = record_with(work / f'claim-{index}.sqlite', **overrides)
            try:
                status, reason = breakthrough.claim_status(record, record['confirmed'][0])
                rejected[label] = dict(status=status, reason=reason)
                check(status == 'needs_revalidation', f'{label} was accepted as {status!r}')
            finally:
                store.close()
        record_case('a-claim-must-still-show-its-own-proof', rejected)

        # (2) A fully proven claim stays supported, and the published payload keeps it.
        store, record = record_with(work / 'claim-good.sqlite')
        try:
            status, reason = breakthrough.claim_status(record, record['confirmed'][0])
            check(status == 'supported' and reason is None,
                  f'a fully proven claim reads {(status, reason)}')
            breakthrough.migrate_claims(store, record)
            payload = breakthrough.status(store)
            entry = payload['encounters'][str(ENCOUNTER)]
            check(len(entry['confirmed']) == 1, f'the proven claim was dropped: {entry["confirmed"]}')
            record_case('a-proven-claim-survives-the-re-read',
                        dict(status=status, published=len(entry['confirmed'])))
        finally:
            store.close()

        # (3) An encounter whose only claim is withdrawn loses the badge and the published claim.
        store, record = record_with(work / 'claim-withdrawn.sqlite',
                                    result=proof(frozen=False))
        try:
            summary = breakthrough.migrate_claims(store, record)
            # `migrate_claims` edits the record in place; the caller owns the write, exactly as the
            # coordinator's own pass does.
            state = breakthrough.load(store)
            state['encounters'][str(ENCOUNTER)] = record
            breakthrough.save(store, state)
            check(summary['needsRevalidation'] == 1 and summary['supported'] == 0, summary)
            check(record['status'] == 'claims_need_revalidation',
                  f"the encounter still reads {record['status']!r}")
            check(len(breakthrough.published_claims(record)) == 0,
                  'the withdrawn claim is still published')
            # Nothing was deleted: the claim, its reason and its plan are all still there.
            check(len(record['claims']) == 1 and record['claims'][0]['reason'],
                  'the withdrawn claim lost its record or its reason')
            check(record['plans'] and record['plans'][0]['status'] == 'confirmed',
                  'the confirmation plan was rewritten instead of preserved')
            payload = breakthrough.status(store)
            published = payload['encounters'][str(ENCOUNTER)]
            check(published['confirmed'] == [],
                  f'the payload still shows a withdrawn claim: {published["confirmed"]}')
            check(published['claimsRevalidation']['needsRevalidation'] == 1,
                  'the payload does not report the withdrawal')
            check(published['status'] == 'claims_need_revalidation',
                  f"the payload status reads {published['status']!r}")
            record_case('a-withdrawn-claim-loses-its-badge-not-its-record',
                        dict(status=record['status'], published=published['confirmed'],
                             revalidation=published['claimsRevalidation'],
                             planPreserved=record['plans'][0]['status']))
            # (4) Idempotent: the second pass rewrites nothing.
            snapshot = json.dumps(record, sort_keys=True)
            breakthrough.migrate_claims(store, record)
            check(json.dumps(record, sort_keys=True) == snapshot,
                  'the re-check ran again and rewrote the record on its second pass')
            record_case('the-re-check-is-idempotent', dict(marker=record['claimsChecked']))
        finally:
            store.close()

        # (5) Mixed: only the claim that stands up is published.
        good = dict(candidate='nominee-1', hypothesis='h:good', delta=dict(mean=1.5, n=64),
                    screening='e:137:1', confirmation='c:137:1:B')
        bad = dict(candidate='nominee-2', hypothesis='h:bad', delta=dict(mean=9.0, n=8),
                   screening='e:137:2', confirmation='c:137:2:B')
        store, record = record_with(work / 'claim-mixed.sqlite', claims=[good, bad])
        try:
            breakthrough.migrate_claims(store, record)
            published = breakthrough.published_claims(record)
            check([entry['hypothesis'] for entry in published] == ['h:good'],
                  f'the published list reads {published}')
            check(record['status'] == 'confirmed_improvement',
                  f'the encounter lost its badge although one claim stands up: {record["status"]!r}')
            record_case('only-the-claims-that-stand-up-are-published',
                        dict(published=[entry['hypothesis'] for entry in published],
                             withdrawn=[entry['hypothesis'] for entry in record['claims']
                                        if entry['status'] != 'supported'],
                             status=record['status']))
        finally:
            store.close()

    print(f'claim revalidation checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
