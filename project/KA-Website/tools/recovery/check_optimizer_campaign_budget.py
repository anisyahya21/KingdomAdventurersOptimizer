"""The Breakthrough campaign budget: used, reserved/in-flight, remaining, stop reason, and resuming.

Budget exhaustion used to be a number the reader could not see and could not act on: the published
payload carried a run count and a hidden cap, and a fight that reached it simply stopped proposing
work with no stated reason and no way to continue. This check pins the replacement:

  * the cap is **one encounter's campaign** - stated as such in the payload, so it cannot be read as a
    per-experiment or library-wide limit;
  * `used` is this stream's own measured work in that encounter;
  * `reserved` is what its **in-flight** plans have been authorised but not yet measured, arm by arm
    and with the control included, counted over each plan's own window;
  * a cached compatible result costs no new battle: an ordinal already measured is never reserved
    (or charged) again, so filling a window with existing evidence reduces the reservation to zero
    without moving `used`;
  * a confirmation reserves only its **fresh** window (`bank - developmentBank`), per participant;
  * `remaining` is `cap - used - reserved` and never goes negative, so an over-committed plan shows as
    over-committed instead of as free budget;
  * `stopReason` is `budget` at or past the cap - exhaustion means "paused for budget", never "no
    better build exists" - and the encounter's hypotheses keep their evidence while paused;
  * the explicit extension raises this encounter's cap by a stated number of runs, resumes the paused
    encounter and its hypotheses, preserves every measured run, and touches no other library state -
    in particular it cannot switch a stream on, because whether runs may be dispatched is still the
    share gate's decision.

    python check_optimizer_campaign_budget.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_breakthrough as breakthrough  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 137


def scenario(marker=0):
    return dict(encounterId=ENCOUNTER, defeatCount=0, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], parameters={'13': {'rawValue': 100 + marker}})],
                enemies=[])


def add_evidence(store, candidate, count, start=0, phase='validation'):
    """`count` measured ordinals for one candidate, as the evidence table stores them."""
    with store.db:
        for offset in range(count):
            ordinal = start + offset
            store.db.execute(
                'INSERT OR REPLACE INTO evidence(candidate,phase,ordinal,seeds,verdict,censored) '
                'VALUES (?,?,?,?,?,0)',
                (candidate, phase, ordinal, json.dumps([ordinal, ordinal + 1]), 1))


def fixture(path, *, cap=1000, parent_rows=64, child_rows=0, watermark=True, existed=False,
            status='nothing_supported_yet', plan_created=None):
    """A library with one pending screening plan: parent `A` and arm `B`, with stated evidence.

    `watermark` records the plan's own per-participant evidence counts at authorisation (zero for both
    here, so every row the participants hold is the plan's work). Turning it off records a plan from
    before watermarks existed, which is how the "cannot be attributed" branch is exercised.
    """
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario()))
    parent = store.add(scenario(1), 'parent build', 'mutation', {})
    child = store.add(scenario(2), 'child build', 'mutation', {})
    add_evidence(store, parent, parent_rows)
    # The window a screening bank measures is ordinals 0..bank-1, so a participant's window is
    # "filled" when its rows sit there.
    add_evidence(store, child, child_rows)
    state = breakthrough.load(store)
    record = breakthrough.encounter_state(state, ENCOUNTER)
    record['status'] = status
    record['budget'] = dict(runs=0, plans=1, experiments=1, confirmations=0, cap=cap)
    plan = dict(id='e:137:1', kind='screening', status='pending', bank=64, parent=parent,
                created=plan_created,
                arms=[dict(name='A', candidate=parent, existed=True),
                      dict(name='B', candidate=child, existed=existed)])
    if watermark:
        plan['baseline'] = {parent: 0, child: 0}
    record['plans'] = [plan]
    record['hypotheses'] = [dict(id='h:137:prior:atk+spd', status='paused_for_budget')]
    breakthrough.save(store, state)
    return store, parent, child


def meta_keys(store):
    return {row[0] for row in store.db.execute('SELECT key FROM meta')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-campaign-budget-check-1', checks=[], cases=0)
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
        path = pathlib.Path(work) / 'strategies-campaign.sqlite'
        store, parent, child = fixture(path, cap=1000, parent_rows=64)
        try:
            state = breakthrough.load(store)
            campaign = breakthrough.campaign(store, state['encounters'][str(ENCOUNTER)])
            check(campaign['scope'] == 'one encounter', f'scope reads {campaign["scope"]!r}')
            check(campaign['cap'] == 1000 and campaign['used'] == 64,
                  f'cap/used read {campaign["cap"]}/{campaign["used"]}')
            # The parent already has its whole window; only the child's 64 runs are still owed.
            check(campaign['reserved'] == 64, f'reserved reads {campaign["reserved"]}, expected 64')
            check(campaign['remaining'] == 872, f'remaining reads {campaign["remaining"]}, expected 872')
            check(campaign['stopReason'] is None,
                  f'stopReason reads {campaign["stopReason"]!r} below the cap')
            record_case('one-encounters-campaign-stated-in-full', dict(campaign))

            # A cached compatible result costs no new battle: measuring the child's window removes the
            # reservation without changing what has been spent.
            add_evidence(store, child, 64)
            state = breakthrough.load(store)
            filled = breakthrough.campaign(store, state['encounters'][str(ENCOUNTER)])
            check(filled['reserved'] == 0, f'reserved after filling the window reads {filled["reserved"]}')
            # The child's 64 new rows move from reservation into spend exactly once; a second read
            # reserves nothing further for them.
            check(filled['used'] == campaign['used'] + 64,
                  f"used moved {campaign['used']} -> {filled['used']}, expected +64")
            again = breakthrough.campaign(store, breakthrough.load(store)['encounters'][str(ENCOUNTER)])
            check(again['used'] == filled['used'] and again['reserved'] == 0,
                  f'a second read moved the numbers again: {again}')
            record_case('cached-results-are-not-reserved-again',
                        dict(reserved=filled['reserved'], used=filled['used'],
                             why='64 ordinals already measured stop being reserved, and are charged once'))

            # Exhaustion is stated, and an over-committed plan is visible rather than silently negative.
            state = breakthrough.load(store)
            encounter = state['encounters'][str(ENCOUNTER)]
            encounter['budget']['cap'] = 64
            encounter['status'] = 'paused_for_budget'
            encounter['plans'].append(
                dict(id='e:137:2', kind='screening', status='pending', bank=128,
                     parent=parent, arms=[dict(name='C', candidate=child, existed=False)],
                     baseline={parent: 64, child: 64}))
            breakthrough.save(store, state)
            over = breakthrough.campaign(store, breakthrough.load(store)['encounters'][str(ENCOUNTER)])
            check(over['exhausted'] is True and over['stopReason'] == 'budget',
                  f'exhaustion reads {over}')
            check(over['remaining'] == 0 and over['overCommitted'] is True,
                  f'over-commitment reads {over}')
            record_case('exhaustion-and-over-commitment-are-stated',
                        dict(used=over['used'], reserved=over['reserved'], remaining=over['remaining'],
                             overCommitted=over['overCommitted'], stopReason=over['stopReason']))

            # A confirmation reserves only its fresh window, for both participants.
            state = breakthrough.load(store)
            encounter = state['encounters'][str(ENCOUNTER)]
            for plan in encounter['plans']:
                if plan['id'] in ('e:137:1', 'e:137:2'):
                    plan['status'] = 'evaluated'
            encounter['plans'].append(dict(id='c:137:1:B', kind='confirmation', status='pending',
                                           bank=512, developmentBank=64,
                                           parent=parent, nominee=child,
                                           arms=[dict(name='A', candidate=parent),
                                                 dict(name='N', candidate=child)],
                                           baseline={parent: 128, child: 128}))
            encounter['budget']['cap'] = 100000
            breakthrough.save(store, state)
            confirm = breakthrough.campaign(store, breakthrough.load(store)['encounters'][str(ENCOUNTER)])
            check(confirm['reserved'] == 2 * (512 - 64),
                  f'the confirmation reserved {confirm["reserved"]}, expected {2 * (512 - 64)}')
            record_case('a-confirmation-reserves-its-fresh-window-only',
                        dict(reserved=confirm['reserved'], window=[64, 512], participants=2))

            # A control with a long history is not billed as this stream's spend: what a plan caused is
            # its participants' rows above the plan's own watermark, and the rest is reported as
            # unattributable rather than invented.
            legacy_path = pathlib.Path(work) / 'strategies-campaign-legacy.sqlite'
            legacy, _lp, _lc = fixture(legacy_path, cap=1000, parent_rows=300, child_rows=10,
                                       watermark=False, existed=False)
            try:
                state = breakthrough.load(legacy)
                legacy_campaign = breakthrough.campaign(legacy, state['encounters'][str(ENCOUNTER)])
                check(legacy_campaign['used'] == 10,
                      f'the legacy plan billed {legacy_campaign["used"]} runs, expected its own 10')
                check(legacy_campaign['unattributedRuns'] == 300,
                      f'unattributable control history reads '
                      f'{legacy_campaign["unattributedRuns"]}, expected 300')
                record_case('a-controls-history-is-not-billed-to-the-experiment',
                            dict(used=legacy_campaign['used'],
                                 unattributedRuns=legacy_campaign['unattributedRuns'],
                                 why='the 300 pre-existing control runs are reported, never charged'))
            finally:
                legacy.close()

            # The published payload carries the campaign block, plus the scope stated at the top.
            payload = breakthrough.status(store)
            check(payload.get('campaignScope') == 'one encounter',
                  f'the payload scope reads {payload.get("campaignScope")!r}')
            published = (payload.get('encounters') or {}).get(str(ENCOUNTER), {}).get('campaign')
            check(isinstance(published, dict) and published.get('scope') == 'one encounter',
                  f'the encounter payload has no campaign block: {published!r}')
            record_case('the-surface-can-see-the-campaign',
                        dict(scope=payload.get('campaignScope'),
                             fields=sorted(published or {})))

            # The explicit extension: it raises the cap, resumes, and preserves everything measured.
            before_keys = meta_keys(store)
            before_evidence = store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]
            before_used = breakthrough.load(store)['encounters'][str(ENCOUNTER)]['budget']['runs']
            result = breakthrough.extend_budget(store, ENCOUNTER, 2500)
            check(result.get('ok') is True, f'the extension was refused: {result}')
            check(result['cap'] == 100000 + 2500, f"the cap became {result['cap']}")
            after = breakthrough.load(store)['encounters'][str(ENCOUNTER)]
            check(after['budget']['runs'] == before_used,
                  'the extension rewrote the measured spend')
            check(store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == before_evidence,
                  'the extension changed the evidence')
            check(after['status'] != 'paused_for_budget',
                  f"the encounter is still paused: {after['status']!r}")
            check(all(entry['status'] != 'paused_for_budget' for entry in after['hypotheses']),
                  'a paused hypothesis was left behind')
            changed = meta_keys(store) - before_keys
            check(not changed, f'the extension wrote unrelated library state: {sorted(changed)}')
            record_case('the-extension-resumes-without-rewriting-anything',
                        dict(added=result['added'], cap=result['cap'], used=after['budget']['runs'],
                             resumed=result['resumed'], evidenceUnchanged=True))

            for bad, why in ((0, 'zero runs'), (-5, 'a negative number'), ('lots', 'a non-number')):
                refused = breakthrough.extend_budget(store, ENCOUNTER, bad)
                check(refused.get('ok') is False, f'{why} was accepted: {refused}')
            missing = breakthrough.extend_budget(store, 99999, 100)
            check(missing.get('ok') is False, f'an unknown encounter was accepted: {missing}')
            record_case('a-meaningless-extension-is-refused',
                        dict(accepted=[refused, missing]))
        finally:
            store.close()

        # The whole dispatch path the surface uses: Bridge.command -> Optimizer.command queue -> the
        # coordinator's own handler. The action has to be on the running-time command surface for the
        # button in the Breakthrough card to reach it at all.
        command_path = pathlib.Path(work) / 'strategies-campaign-command.sqlite'
        seed, _parent, _child = fixture(command_path, cap=64, parent_rows=64,
                                        status='paused_for_budget')
        seed.close()
        bridge = desktop.Bridge(command_path)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and bridge.status().get('state') == 'Opening library':
                time.sleep(.05)
            accepted = bridge.command('breakthrough_budget', {'encounterId': ENCOUNTER, 'runs': 1500})
            check(accepted.get('ok') is True,
                  f'the host refused the extension command: {accepted}')
            reopened = optimizer.Store(command_path, provenance())
            try:
                after = breakthrough.load(reopened)['encounters'][str(ENCOUNTER)]
            finally:
                reopened.close()
            check(after['budget']['cap'] == 64 + 1500,
                  f"the command left the cap at {after['budget']['cap']}, expected 1564")
            check(after['status'] != 'paused_for_budget',
                  f"the command did not resume the encounter: {after['status']!r}")
            record_case('the-surface-command-reaches-the-budget',
                        dict(cap=after['budget']['cap'], status=after['status'],
                             used=after['budget']['runs']))
        finally:
            bridge.close()

    print(f'campaign budget checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
