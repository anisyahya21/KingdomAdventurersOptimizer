"""B4 receipt-chain composition: `Entity.AddTreasure` feeding the original receipt serializer.

One machine, one address space: the original `Entity.AddTreasure` `0x1473234` (frozen B3 manifest)
fills a pooled `TreasureComponent`, `TreasureComponent.Serialize` `0x14d2684` writes that same
component to a synthetic stream, and `Deserialize` `0x14d2734` reads it back. The ordered boundary
trace and the observed component state are compared against `combat_receipt.package_receipt` with
`combat_event_compare`, so a wrong field offset, a missing `& 1`, a swapped queue pointer or a width
mismatch surfaces as the first divergence instead of hiding behind matching final values.

The pooled instance is returned by a declared stub, i.e. "a pooled component exists" is an input of
this fixture and not a claim of it. The empty-stack and null-pool arms of `AddTreasure` leave the B3
slice set (`0x147333c` / `0x147335c`) and were not executed; the pool return leg
(`Entity.RemoveTreasure` `0x1473360`) needs `Entity.get_treasure` `0x147319c`, which is not in a
loaded manifest, so the teardown leg stays outside this fixture too.
"""
import json

from check_combat_composition import (harness as stream_harness, model_bytes_events,
                                      model_component_stream, stream_events)
from combat_event_compare import describe, first_divergence, state_divergences
from combat_initial_state import EVIDENCE
from combat_native_composition import RECEIPT_SLICES
from combat_receipt import _signed, package_receipt, treasure_result
from combat_run_manifest import canonical_hash

OUT = EVIDENCE / 'receipt-composition-checks.json'
MANIFEST = RECEIPT_SLICES / 'b3-receipt-slices.json'
ADD_TREASURE = 0x1473234
SERIALIZE = 0x14d2684
DESERIALIZE = 0x14d2734
POOL_POP = 0x204a0c4                # `Stack<TreasureComponent>.Pop()`: declared stub in this fixture
ADD_SLOT = 0x188                    # vtable slot 5: `this.Add(0x2c, tc)`
ENTITY_CLASS_INIT_FLAG = 0x316eadd  # `AddTreasure`'s fast-path byte (RemoveTreasure has another)
ENTITY_POOL_GOT = 0x2f5ea08         # GOT slot whose variable holds the `Entity` TypeInfo
COMPONENT_KLASS_GOT = 0x2f5f050     # GOT slot whose variable holds the `TreasureComponent` TypeInfo
COMPONENT_DATA_ID, COMPONENT_OPENABLE, COMPONENT_QUEUE = 0x10, 0x14, 0x18
QUEUE_ROWS = (treasure_result(24, 100, 5), treasure_result(0, 11, 2))
# The model's own packaging sample (710) plus the component's field-width boundaries.
SAMPLES = ((710, 1, 1), (710, 0, 0), (300, 1, 2), (0x7fff, 1, 1), (-2, 1, 1), (0x12345678, 1, 1))


def component_add(machine, entity, selector, component, klass):
    """The vtable slot `AddTreasure` tail-calls: record what the component handler receives."""
    machine.note(selector=selector & 0xffffffff, entity=hex(entity), component=hex(component),
                 klass=hex(klass), dataId=machine.r32(component + COMPONENT_DATA_ID),
                 isOpenable=machine.r8(component + COMPONENT_OPENABLE),
                 resultQueue=hex(machine.r64(component + COMPONENT_QUEUE)))
    return 1


def harness(written):
    """The stream composition (22 slices + stream stubs) with the B3 receipt slices loaded too."""
    machine = stream_harness(written=written)
    machine.load(MANIFEST)
    machine.w8(ENTITY_CLASS_INIT_FLAG, 1)
    statics = machine.object_of(machine.klass({}), 0x200)
    pool = machine.object_of(machine.klass({}), 0x40)
    machine.w32(pool + 0x18, 1)                     # `Stack<T>.size_ >= 1`: the pooled arm
    machine.w64(statics + 0x170, pool)              # static `Stack<TreasureComponent>` pool
    component = machine.object_of(machine.klass({}), 0x40)
    add_slot = machine.new_stub('component_add', component_add, capture=('x0', 'w1', 'x2', 'x3'))
    entity_klass = machine.klass({0xb8: statics, ADD_SLOT: add_slot})
    machine.stub(POOL_POP, 'pool_pop', lambda h, stack, klass: component, capture=('x0', 'x1'))
    machine.got(ENTITY_POOL_GOT, entity_klass)
    machine.got(COMPONENT_KLASS_GOT, component)
    machine.entity = machine.object_of(entity_klass, 0x40)
    machine.component = component
    return machine



def case(data_id, is_openable, queue_rows):
    """One packaging sample: native `AddTreasure` -> native `Serialize` -> native `Deserialize`."""
    written = []
    machine = harness(written)
    queue = machine.alloc(0x30)
    machine.run(ADD_TREASURE, {'x0': machine.entity, 'w1': data_id, 'w2': is_openable, 'x3': queue})
    add = next(event for event in machine.trace if event.get('name') == 'component_add')
    calls = [event for event in machine.trace
             if event['kind'] == 'call' and event['name'] in ('pool_pop', 'component_add')]
    model_calls = [dict(kind='call', name='pool_pop'), dict(kind='call', name='component_add')]
    order = first_divergence(calls, model_calls, fields=('name',))
    assert order is None, describe(order, 'AddTreasure boundary order')
    model = package_receipt(data_id, bool(is_openable), list(QUEUE_ROWS[:queue_rows]))
    native_state = {'componentType': add['selector'], 'dataId': add['dataId'],
                    'isOpenable': add['isOpenable']}
    divergences = state_divergences(native_state, {key: model[key] for key in native_state})
    assert divergences == [], (data_id, divergences)
    assert add['resultQueue'] == hex(queue), (hex(queue), add['resultQueue'])
    # The same component object, written and read back by the original serializer.
    machine.run(SERIALIZE, {'x0': machine.component, 'x1': machine.stream})
    native_stream = stream_events(machine.trace)
    payload = model_component_stream(data_id, is_openable)
    stream_divergence = first_divergence(native_stream, model_bytes_events(payload, queue_event=True),
                                         fields=('name', 'byte'))
    assert stream_divergence is None, describe(stream_divergence, 'Serialize')
    machine.pending[:] = written
    restored = machine.object_of(machine.klass({}), 0x40)
    machine.run(DESERIALIZE, {'x0': restored, 'x1': machine.stream})
    wire = _signed(data_id, 16)
    assert machine.r32(restored + COMPONENT_DATA_ID) == wire, (data_id, wire)
    assert machine.r8(restored + COMPONENT_OPENABLE) == (is_openable & 1)
    assert machine.r64(restored + COMPONENT_QUEUE) == machine.queues['read']
    assert machine.queues['written'] == queue, (hex(queue), hex(machine.queues['written']))
    return dict(dataId=data_id, isOpenable=is_openable, queueRows=queue_rows,
                boundaryOrder=[event['name'] for event in calls], boundaryOrderDivergence=order,
                nativeComponentState=native_state, modelReceipt=model,
                stateDivergences=divergences, queuePointerMatches=add['resultQueue'] == hex(queue),
                nativeStream=[event['byte'] for event in native_stream
                              if event['name'] == 'write_byte'],
                modelStream=list(payload), streamDivergence=stream_divergence,
                inMemoryDataId=add['dataId'], wireDataId=wire,
                widthTruncatedOnWire=add['dataId'] != wire,
                restoredDataId=machine.r32(restored + COMPONENT_DATA_ID),
                restoredIsOpenable=machine.r8(restored + COMPONENT_OPENABLE),
                restoredQueueMatchesReadQueue=machine.r64(restored + COMPONENT_QUEUE)
                == machine.queues['read'])



def main():
    cases = [case(*sample) for sample in SAMPLES]
    machine = harness([])
    assert any(case['widthTruncatedOnWire'] for case in cases), \
        'the component keeps 32-bit dataId while the stream carries 16 bits'
    report = dict(schema='ka-b4-receipt-composition-1', fixtures=dict(packagingCases=len(cases)),
                  sliceManifest=machine.slice_manifest(),
                  stubs=[dict(address=hex(address), name=entry[0])
                         for address, entry in sorted(machine.stubs.items())],
                  samples=[[data_id, is_openable, rows] for data_id, is_openable, rows in SAMPLES],
                  cases=cases,
                  findings=[
                      'The original AddTreasure fills the pooled component at +0x10 (int32 dataId), '
                      '+0x14 (`isOpenable & 1` as one byte) and +0x18 (the resultQueue pointer it was '
                      'given), then tail-calls the component handler with selector 0x2c - the pool '
                      'pop then the add, in the order the model declares.',
                      'The in-memory dataId is the full 32-bit parameter while the save stream '
                      'carries only its low 16 bits (`Serialize` writes a short), so a dataId above '
                      '0x7fff does not survive a save/load round trip; the model reproduces both '
                      'sides and the fixture records the truncation instead of hiding it.',
                      'Serializing the component the original AddTreasure just filled yields exactly '
                      'the model stream (dataId short, isOpenable boolean, queue event), and the '
                      'original Deserialize restores the truncated dataId, the masked boolean and a '
                      'queue handle: packaging and persistence are composed, not assumed.'],
                  limits=[
                      'The pooled component comes from a declared `Stack<T>.Pop` stub: instance '
                      'reuse is an input. The empty-stack and null-pool arms of AddTreasure lie '
                      'outside the frozen B3 slice set and were not executed, and the pool return leg '
                      '(RemoveTreasure 0x1473360) needs the unsliced Entity.get_treasure 0x147319c.',
                      'AddTreasure only packages a receipt: the dispatch (AISystem.TreasureBoxResult '
                      '0x14b8e4c), its arms, world effects and the ledger are not part of this '
                      'composition, and no capacity rule is modelled or observed.',
                      'Stubs supply class/method initialization, the pool pop, the two GOT variables, '
                      'the entity/component/queue objects and the stream payload: memory and handles, '
                      'never order. The compiler store order inside AddTreasure (dataId, queue, then '
                      'isOpenable) is not claimed; the resulting field values are.',
                      'One composition only: no world state, RNG, rendering, audio or end-to-end '
                      'encounter behaviour is claimed, and there is no runtime trace to compare.'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(fixtures=report['fixtures'], slices=report['sliceManifest']['slices'],
                          stubs=len(report['stubs']), output=str(OUT),
                          digest=canonical_hash(report))))


if __name__ == '__main__':
    main()
