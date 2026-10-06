"""Regression for audit finding 3: retention means a confirmed modelled storage write.

Creating a town-selection form, pushing it, spawning a ground product, adding a gatherable, an
animation, a material or a popup does not put anything into inventory, and the opaque IStock
interface handoff resolves through an unnamed target. The old ledger called any nonempty grant list
`retained`, so a town-selection-only receipt and a ground-product receipt both claimed retained
inventory that never happened. This check asserts they report zero confirmed receipts, that a
direct AddItem does confirm, that a port failure cannot be hidden, and that a partial success keeps
the confirmed count separate from the rest. Model-only: no native execution and no inventory claim.
"""
import json

from check_combat_receipt import Recorder, item
from combat_receipt import ReceiptLedger, dispatch_result, retention_of, treasure_result


def run(rows, results, **fields):
    recorder = Recorder(rows, **fields)
    return dispatch_result(recorder.port, results, chest='chest'), recorder.port


def ledger_state(record_port, results):
    ledger = ReceiptLedger()
    entry = ledger.queue(710, results=results)
    ledger.dispatch(record_port, entry)
    return entry, ledger.report()


def main():
    out = {}
    # --- town-selection-only: a form is created and pushed, nothing is stored ---
    record, _ = run(item(100, category=5, type=16, flag=512), [treasure_result(24, 100, 5)],
                    alive=False, towns=2)
    assert [grant['kind'] for grant in record['grants']] == ['create_item_add_to_town_select',
                                                             'push_form'], record['grants']
    assert record['state'] == 'granted', record['state']
    assert record['retention']['confirmedReceipts'] == 0, record['retention']
    assert record['retention']['pendingChoice'] == ['create_item_add_to_town_select',
                                                    'push_form'], record['retention']
    assert record['retention']['acknowledgement'] == 'unconfirmed', record['retention']
    # --- ground product: a product is spawned and made gatherable, never collected here ---
    ground, ground_port = run(item(100, category=5, type=1), [treasure_result(24, 100, 5)])
    assert [grant['kind'] for grant in ground['grants']] == ['create_product', 'add_gatherable',
                                                             'add_modify_animation'], ground
    assert ground['retention']['confirmedReceipts'] == 0, ground['retention']
    assert ground['retention']['pendingCollection'] == ['create_product', 'add_gatherable'], ground
    assert ground['retention']['presentation'] == ['add_modify_animation'], ground
    # --- direct AddItem confirms; the ledger's retained count follows the confirmed kind only ---
    ticket, ticket_port = run(item(100, category=6, type=18), [treasure_result(24, 100, 5)])
    assert ticket['retention']['confirmedKinds'] == ['add_item'], ticket['retention']
    assert ticket['retention']['acknowledgement'] == 'modelled-storage-write', ticket['retention']
    # --- the opaque IStock handoff is not a storage write ---
    istock, _ = run({0: [dict(id=0, state=1, category=6, type=18)]},
                    [treasure_result(0, 0, 5)], is_istock=True, alive=False)
    assert istock['stockCall'] == dict(num=5) and istock['retention']['confirmedReceipts'] == 0, \
        istock
    assert istock['retention']['acknowledgement'] == 'unconfirmed', istock['retention']

    for name, (record, record_port, results) in {
            'townSelectOnly': (record, Recorder(item(100, category=5, type=16, flag=512),
                                                alive=False, towns=2).port,
                               [treasure_result(24, 100, 5)]),
            'groundProduct': (ground, ground_port, [treasure_result(24, 100, 5)]),
            'directAddItem': (ticket, ticket_port, [treasure_result(24, 100, 5)])}.items():
        entry, report = ledger_state(record_port, results)
        out[name] = dict(state=entry['state'], retainedReceipts=report['retainedReceipts'],
                         resultsRemaining=report['resultsRemaining'])
    assert out['townSelectOnly']['state'] == 'dispatched', out
    assert out['townSelectOnly']['retainedReceipts'] == 0, out
    assert out['groundProduct']['state'] == 'dispatched', out
    assert out['groundProduct']['retainedReceipts'] == 0, out
    assert out['directAddItem']['state'] == 'retained', out
    assert out['directAddItem']['retainedReceipts'] == 1, out

    # --- a port failure is not concealed: CreateProduct returns null, so the arm suppresses ---
    failing = Recorder(item(100, category=5, type=1), product=None)
    failed = dispatch_result(failing.port, [treasure_result(24, 100, 5)], chest='chest')
    assert failed['state'] == 'suppressed', failed['state']
    assert failed['retention']['confirmedReceipts'] == 0, failed['retention']
    failed_entry, failed_report = ledger_state(failing.port, [treasure_result(24, 100, 5)])
    assert failed_entry['state'] == 'suppressed' and failed_report['retained'] == 0, failed_report
    # --- partial success keeps the confirmed result separate from the suppressed one ---
    ledger = ReceiptLedger()
    entry = ledger.queue(713, results=[treasure_result(24, 0, 5), treasure_result(24, 1, 5)])
    rows = {24: [dict(id=0, category=6, type=18), None]}
    shared = Recorder(dict(rows))
    ledger.dispatch(shared.port, entry)
    ledger.dispatch(Recorder(rows).port, entry)
    # A confirmed result next to an unresolved one is a partially-known chest: `mixed`, never
    # `retained`, so one real receipt cannot make the whole chest look wholly received.
    assert entry['state'] == 'mixed' and entry['results'] == [], entry
    assert [record['retention']['confirmedReceipts'] for record in entry['dispatches']] == [1, 0]
    report = ledger.report()
    assert report['resultsByState'] == {'granted': 1, 'unresolved-data': 1}, report['resultsByState']
    assert report['retainedReceipts'] == 1, report
    out['portFailure'] = dict(state=failed['state'], retainedReceipts=0)
    out['partialSuccess'] = dict(state=entry['state'], retainedReceipts=report['retainedReceipts'],
                                 resultsByState=report['resultsByState'])

    # --- explicit acknowledgement contract: a structured flag confirms; void/False/opaque do not
    def one_result():
        return [treasure_result(24, 100, 5)]

    for channel, rows in (('add_item', item(100, category=6, type=18)),
                          ('add_coin', item(100, category=2, type=8)),
                          ('add_stamina', item(100, category=3, type=14))):
        acknowledged, _ = run(rows, one_result())                     # default structured success
        assert acknowledged['retention']['confirmedKinds'] == [channel], acknowledged['retention']
        assert acknowledged['retention']['acknowledgement'] == 'modelled-storage-write', acknowledged
        for value in (False, None):
            decline, _ = run(rows, one_result(), storage_ack=value)
            assert decline['retention']['confirmedReceipts'] == 0, (channel, value, decline)
            assert decline['retention']['unacknowledgedStorage'] == [channel], (channel, value)
            assert decline['retention']['acknowledgement'] == 'unconfirmed', (channel, value)
        _, declined_port = run(rows, one_result(), storage_ack=False)
        declined_entry, declined_report = ledger_state(declined_port, one_result())
        assert declined_entry['state'] != 'retained', (channel, declined_entry)
        assert declined_report['retainedReceipts'] == 0, (channel, declined_report)
    # An opaque callback result (bare True, or a dict without the flag) is not an acknowledgement.
    for opaque in (True, {'stored': True}, object()):
        bare = Recorder(item(100, category=6, type=18))
        bare.port['add_item'] = lambda *args, _opaque=opaque: _opaque
        bare_record = dispatch_result(bare.port, [treasure_result(24, 100, 5)], chest='chest')
        assert bare_record['retention']['confirmedReceipts'] == 0, opaque
        assert bare_record['retention']['unacknowledgedStorage'] == ['add_item'], opaque
    out['acknowledgement'] = dict(confirmed='structured', void='unconfirmed', false='unconfirmed',
                                  opaque='unconfirmed')

    # --- the exact Astra probe: a replaced adapter returning False must not retain ---
    probe = Recorder(item(0, category=6, type=18))
    probe.port['add_item'] = lambda *args: False
    probe_entry, probe_report = ledger_state(probe.port, [treasure_result(24, 0, 5)])
    assert probe_entry['state'] == 'dispatched' and probe_report['retainedReceipts'] == 0, \
        (probe_entry['state'], probe_report['retainedReceipts'])
    out['replacedAdapterFalse'] = dict(state=probe_entry['state'],
                                       retainedReceipts=probe_report['retainedReceipts'],
                                       unacknowledgedStorage=probe_entry['dispatches'][0][
                                           'retention']['unacknowledgedStorage'])

    # --- a raising storage callback keeps its lifecycle evidence and confirms nothing ---
    raising = Recorder(item(0, category=6, type=18), storage_raise=('add_item',))
    raised = dispatch_result(raising.port, [treasure_result(24, 0, 5)], chest='chest')
    assert raised['state'] == 'suppressed', raised['state']
    assert raised['retention']['confirmedReceipts'] == 0, raised['retention']
    assert raised['retention']['unacknowledgedStorage'] == ['add_item'], raised['retention']
    assert [item['kind'] for item in raised['storageErrors']] == ['add_item'], raised
    assert raised['grants'][0]['acknowledged'] is False and raised['grants'][0]['error'], raised
    assert ('add_item', None, 0, 5) in raising.mutations(), raising.mutations()
    raised_entry, raised_report = ledger_state(raising.port, [treasure_result(24, 0, 5)])
    assert raised_entry['state'] == 'suppressed' and raised_report['retainedReceipts'] == 0, \
        raised_report
    out['callbackException'] = dict(state=raised['state'], confirmedReceipts=0,
                                    evidence=raised['storageErrors'][0]['error'])

    # --- chest-level completion: confirmed + deferred is mixed, two acknowledged are retained ---
    mixed_choice = ReceiptLedger()
    choice_entry = mixed_choice.queue(715, results=[treasure_result(24, 0, 5),
                                                    treasure_result(24, 1, 5)])
    mixed_choice.dispatch(Recorder(item(0, category=6, type=18)).port, choice_entry)
    mixed_choice.dispatch(Recorder(item(1, category=5, type=16, flag=512), alive=False,
                                   towns=2).port, choice_entry)
    assert choice_entry['state'] == 'mixed', choice_entry['state']
    choice_report = mixed_choice.report()
    assert choice_report['retainedReceipts'] == 1, choice_report
    both = ReceiptLedger()
    both_entry = both.queue(716, results=[treasure_result(24, 100, 2), treasure_result(24, 100, 3)])
    both.dispatch(Recorder(item(100, category=6, type=18)).port, both_entry)
    both.dispatch(Recorder(item(100, category=6, type=18)).port, both_entry)
    both_report = both.report()
    assert both_entry['state'] == 'retained' and both_report['retainedReceipts'] == 2, both_report
    out['mixedConfirmedTownSelect'] = dict(state=choice_entry['state'],
                                           retainedReceipts=choice_report['retainedReceipts'])
    out['twoAcknowledged'] = dict(state=both_entry['state'],
                                  retainedReceipts=both_report['retainedReceipts'])
    # retention_of itself is total: no grant is silently unclassified.
    for sample in (record, ground, ticket, istock):
        classification = retention_of(sample)
        assert classification['acknowledgement'] in ('modelled-storage-write', 'unconfirmed'), sample
    print(json.dumps(dict(cases=len(out), output=out)))


if __name__ == '__main__':
    main()
