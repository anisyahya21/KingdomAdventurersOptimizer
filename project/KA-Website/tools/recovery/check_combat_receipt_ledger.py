"""Regression for audit finding 2: the multi-result ReceiptLedger lifecycle.

One native `dispatch_result` call consumes exactly one queued result. The old ledger flipped the
whole entry to `retained` on the first dispatch and then refused every later call, so a chest with
two ticket results could never finish. This check drives the corrected ledger with two ticket
results (quantities 2 and 3), with mixed suppressed/unresolved/granted results, with a partial
dispatch followed by a censor, and with an exhausted retry. Model-only: no native execution and no
inventory claim.
"""
import json

from check_combat_receipt import Recorder, item
from combat_receipt import ReceiptLedger, treasure_result


def port(rows, **fields):
    return Recorder(rows, **fields).port


def main():
    results = {}
    # --- two ticket results (2 and 3): two dispatches, both records kept, grants accumulate ---
    ledger = ReceiptLedger()
    entry = ledger.queue(710, tick=100, target=0, results=[treasure_result(24, 100, 2),
                                                           treasure_result(24, 100, 3)])
    ticket_port = port(item(100, category=6, type=18))
    first = ledger.dispatch(ticket_port, entry)
    assert entry['state'] == 'partial', entry['state']
    assert len(entry['results']) == 1 and entry['results'][0]['num'] == 3, entry['results']
    second = ledger.dispatch(ticket_port, entry)
    assert entry['state'] == 'retained', entry['state']
    assert entry['results'] == [] and len(entry['dispatches']) == 2, entry
    nums = [grant['num'] for grant in [g for record in entry['dispatches'] for g in record['grants']
                                       if g['kind'] == 'add_item']]
    assert nums == [2, 3], nums
    report = ledger.report()
    assert report['resultsDispatched'] == 2 and report['resultsRemaining'] == 0, report
    assert report['retainedReceipts'] == 2 and report['retained'] == 1, report
    results['twoTicketResults'] = dict(nums=nums, state=entry['state'])
    # --- exhausted retry is refused, not silently re-dispatched ---
    for error in (ValueError,):
        try:
            ledger.dispatch(ticket_port, entry)
        except error as raised:
            assert 'exhausted' in str(raised) or 'dispatchable' in str(raised), raised
        else:
            raise AssertionError('an exhausted receipt accepted another dispatch')
    results['exhaustedRetryRefused'] = True
    # --- mixed suppressed / unresolved / granted: every result is consumed and listed ---
    mixed = ReceiptLedger()
    mixed_entry = mixed.queue(711, results=[treasure_result(24, 0, 5), treasure_result(24, 1, 5),
                                            treasure_result(24, 2, 5)])
    rows = {24: [dict(id=0, category=0, type=2), None, dict(id=2, category=2, type=9)]}
    mixed_port = port(rows, materials=(dict(id=101, type=1),))
    states = [mixed.dispatch(mixed_port, mixed_entry)['state'] for _ in range(3)]
    assert states == ['suppressed', 'unresolved-data', 'granted'], states
    assert mixed_entry['state'] == 'mixed', mixed_entry['state']
    assert mixed_entry['results'] == [] and len(mixed_entry['dispatches']) == 3, mixed_entry
    mixed_report = mixed.report()
    assert mixed_report['resultsByState'] == {'suppressed': 1, 'unresolved-data': 1,
                                              'granted': 1}, mixed_report['resultsByState']
    assert mixed_report['retained'] == 0 and mixed_report['retainedReceipts'] == 0, mixed_report
    assert mixed_report['mixed'] == 1, mixed_report
    results['mixedStates'] = states
    # --- partial dispatch followed by censor: pending results stay pending, not retained ---
    partial = ReceiptLedger()
    partial_entry = partial.queue(712, results=[treasure_result(24, 100, 2),
                                                treasure_result(24, 100, 3)])
    partial.dispatch(ticket_port, partial_entry)
    partial.censor('tick horizon; settlement is not implemented')
    partial_report = partial.report()
    assert partial_entry['state'] == 'partial' and len(partial_entry['results']) == 1, partial_entry
    assert partial_report['partial'] == 1 and partial_report['retained'] == 0, partial_report
    assert partial_report['resultsRemaining'] == 1, partial_report
    assert partial_report['censoredPendingResults'] == 1, partial_report
    assert partial_report['censored'] is True, partial_report
    results['partialThenCensor'] = dict(state=partial_entry['state'],
                                        resultsRemaining=partial_report['resultsRemaining'])
    print(json.dumps(dict(cases=len(results), output=results)))


if __name__ == '__main__':
    main()
