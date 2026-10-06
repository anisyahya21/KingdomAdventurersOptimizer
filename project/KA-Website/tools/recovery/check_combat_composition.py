"""B4 native composition fixture: StreamUtil byte order and the receipt component round trip.

Runs the original `StreamUtil.Write*/Read*`, `BitConverter` and `TreasureComponent.Serialize`/
`Deserialize` out of the frozen `libil2cpp.so` (fb834373...30208) under Unicorn with declared
stubs, records the ordered boundary calls, and compares that order against the offline model with
`combat_event_compare`. The comparison stops at the first divergence and reports it as data, so a
byte-order or field-order mismatch cannot hide behind a matching final value.

Result of this fixture (2026-09-14): the original writers are **big-endian** for every multi-byte
field, which retracts the B3 "little-endian assumed" limit. See findings in the evidence file.
"""
import json
import struct
from pathlib import Path

from combat_initial_state import EVIDENCE
from combat_event_compare import describe, first_divergence
from combat_native_composition import COMPOSITION_SLICES, Composition
from combat_receipt import _pack, _signed
from combat_run_manifest import canonical_hash

OUT = EVIDENCE / 'composition-checks.json'
# Class-initialization fast paths (`adrp` page + byte offset) of the loaded slices, so no run
# arrives at an initialization stub. Taken from the slice disassembly.
FLAGS = (0x316e0a0, 0x316f4a7, 0x316e1c1, 0x316e1ca, 0x316e1bf, 0x316e1c4,
         0x316ad71, 0x316ad72, 0x316f0a0, 0x316f0c4)
# GOT entries used by the loaded slices: StreamUtil, byte[] and the cached queue method handles.
GOT = ((0x2f5b828, 'StreamUtil'), (0x2f5af30, 'byte[]'), (0x2f614c0, 'WriteQueue handle'),
       (0x2f614c8, 'ReadQueue handle'))
WRITERS = {'WriteShort': (0x23ae24c, 2, (300, 0x0142, -2)),
           'WriteInt': (0x23a45f8, 4, (0x01020304, 300, -2)),
           'WriteBoolean': (0x23ae47c, 1, (1, 0)),
           'WriteByte': (0x23ae20c, 1, (0x80, 7))}
READERS = {'ReadShort': (0x23ac7cc, 2), 'ReadInt': (0x23a37f8, 4),
           'ReadBoolean': (0x23aca88, 1), 'ReadByte': (0x23ac6e4, 1)}
STUB_KEYS = ('il2cpp_class_init', 'il2cpp_method_init', 'array_alloc', 'il2cpp_throw',
             'read_bytes', 'write_byte', 'write_queue', 'read_queue')


def harness(fed=None, written=None):
    """One machine with the stream slices loaded, declared stubs and a synthetic stream pair."""
    machine = Composition([COMPOSITION_SLICES], name='treasure-component-stream')
    for address in FLAGS:
        machine.w8(address, 1)
    machine.stub(0x12d21a0, 'il2cpp_class_init')
    machine.stub(0x12d22a4, 'il2cpp_method_init')
    machine.stub(0x12d2214, 'array_alloc',
                 lambda h, klass, count: h.array(count & 0xffffffff, stride=4, klass=klass),
                 capture=('x0', 'w1'))
    machine.stub(0x12d23d0, 'il2cpp_throw',
                 lambda h, x0: (_ for _ in ()).throw(AssertionError('native throw path')),
                 capture=('x0',))
    for address, _ in GOT:
        machine.got(address, machine.klass({}))
    pending = list(fed or [])

    def read_bytes(h, stream, count):
        take = count & 0xffffffff
        data, pending[:] = pending[:take], pending[take:]
        array = h.array(take, stride=4)
        h.write(array + 0x20, bytes(data))
        h.note(byteArray=data)
        return array

    machine.stub(0x23ac1f0, 'read_bytes', read_bytes, capture=('x0', 'w1'))

    def write_byte(h, value):
        written.append(value & 0xff)
        h.note(byte=value & 0xff, stream=hex(h.reg('x0')))

    stream = machine.object_of(machine.klass({0x1a8: machine.new_stub('write_byte', write_byte,
                                                                     capture=('w1',))}), 0x40)
    machine.queues = {}
    machine.stub(0x1903ba8, 'write_queue',
                 lambda h, stream_, queue, method: h.queues.setdefault('written', queue),
                 capture=('x0', 'x1', 'x2'))
    machine.stub(0x1902854, 'read_queue',
                 lambda h, stream_, method: h.queues.setdefault('read', h.alloc(0x30)),
                 capture=('x0', 'x1'))
    machine.stream = stream
    machine.pending = pending
    return machine


def stream_events(trace, names=('write_byte', 'write_queue')):
    """The claimed stream channel: only the calls that carry payload bytes, in order."""
    return [dict(kind='call', name=event['name'], byte=event.get('byte'))
            for event in trace if event['kind'] == 'call' and event['name'] in names]


def model_bytes_events(payload, queue_event=False):
    """The model's wire claim for the same channel, byte by byte."""
    events = [dict(kind='call', name='write_byte', byte=byte) for byte in payload]
    if queue_event:
        events.append(dict(kind='call', name='write_queue', byte=None))
    return events


def model_component_stream(data_id, is_openable):
    """`TreasureComponent.Serialize`: WriteShort(dataId), WriteBoolean(isOpenable), then the queue.

    Byte order comes from the model's own helper, so this fixture compares the model's actual
    encoding instead of a re-implementation of it.
    """
    return _pack(data_id, 2) + _pack(is_openable, 1)


def writer_cases():
    """Per-type byte order: native channel against the model channel, plus the old assumption."""
    cases = []
    for name, (entry, width, samples) in WRITERS.items():
        fmt = {1: 'b', 2: 'h', 4: 'i'}[width]
        for value in samples:
            machine = harness(written=[])
            machine.run(entry, {'x0': machine.stream, 'w1': value & 0xffffffff})
            native = stream_events(machine.trace, ('write_byte',))
            model = model_bytes_events(_pack(value, width))
            observed = [event['byte'] for event in native]
            big = list(struct.pack('>' + fmt, _signed(value, width * 8)))
            little = list(struct.pack('<' + fmt, _signed(value, width * 8)))
            divergence = first_divergence(native, model, fields=('name', 'byte'))
            assert observed == big, (name, value, observed, big)
            assert divergence is None, describe(divergence, f'{name}({value:#x})')
            cases.append(dict(writer=name, value=value, width=width, nativeBytes=observed,
                              modelBytes=[event['byte'] for event in model],
                              retractedLittleEndian=little, divergence=divergence,
                              calls=[event['name'] for event in machine.trace
                                     if event['kind'] == 'call']))
    return cases


def component_cases():
    """Round trip of the receipt component, including its queue field and a little-endian feed."""
    cases = []
    for data_id, is_openable in ((300, 1), (300, 0), (0x7fff, 1), (-2, 1)):
        machine = harness(written=[])
        component = machine.object_of(machine.klass({}), 0x40)
        machine.w32(component + 0x10, data_id)
        machine.w8(component + 0x14, is_openable)
        queue = machine.alloc(0x30)
        machine.w64(component + 0x18, queue)
        machine.run(0x14d2684, {'x0': component, 'x1': machine.stream})
        native = stream_events(machine.trace)
        payload = model_component_stream(data_id, is_openable)
        model = model_bytes_events(payload, queue_event=True)
        divergence = first_divergence(native, model, fields=('name', 'byte'))
        assert [event['byte'] for event in native if event['name'] == 'write_byte'] == list(payload)
        assert divergence is None, describe(divergence, f'Serialize({data_id},{is_openable})')
        assert machine.queues['written'] == queue, (machine.queues, hex(queue))
        # The same bytes read back by the original Deserialize must restore every field.
        back = harness(fed=list(payload))
        restored = back.object_of(back.klass({}), 0x40)
        back.run(0x14d2734, {'x0': restored, 'x1': back.stream})
        # And the retracted little-endian order must not round-trip through the original reader.
        little = list(struct.pack('<h', _signed(data_id, 16))) + [payload[-1]]
        wrong = harness(fed=little)
        swapped = wrong.object_of(wrong.klass({}), 0x40)
        wrong.run(0x14d2734, {'x0': swapped, 'x1': wrong.stream})
        assert back.r32(restored + 0x10) == _signed(data_id, 16), (data_id, back.r32(restored + 0x10))
        assert back.r8(restored + 0x14) == (is_openable & 1)
        assert back.r64(restored + 0x18) == back.queues['read']
        distinct = little[:2] != list(payload[:2])
        assert not distinct or wrong.r32(swapped + 0x10) != _signed(data_id, 16)
        cases.append(dict(dataId=data_id, isOpenable=is_openable, nativeStream=list(payload),
                          modelStream=[event['byte'] for event in model],
                          restoredDataId=back.r32(restored + 0x10),
                          restoredIsOpenable=back.r8(restored + 0x14),
                          restoredQueueMatchesReadQueue=back.r64(restored + 0x18) == back.queues['read'],
                          littleEndianFeed=little, littleEndianDataId=wrong.r32(swapped + 0x10),
                          littleEndianDistinct=distinct, divergence=divergence))
    return cases


def reader_cases():
    """The read side must interpret the same order and reject the little-endian one."""
    owners = {'ReadShort': 'WriteShort', 'ReadInt': 'WriteInt', 'ReadBoolean': 'WriteBoolean',
              'ReadByte': 'WriteByte'}
    cases = []
    for name, (entry, width) in READERS.items():
        fmt = {1: 'b', 2: 'h', 4: 'i'}[width]
        for value in WRITERS[owners[name]][2]:
            big = list(struct.pack('>' + fmt, _signed(value, width * 8)))
            little = list(struct.pack('<' + fmt, _signed(value, width * 8)))
            machine = harness(fed=big)
            result = machine.run(entry, {'x0': machine.stream, 'w1': 0})
            swapped = harness(fed=little)
            other = swapped.run(entry, {'x0': swapped.stream, 'w1': 0})
            # The readers return the raw field-width value (`ReadShort` is a plain `ldrh`; the
            # `sxth` widening lives in the caller, e.g. TreasureComponent.Deserialize).
            mask = (1 << (width * 8)) - 1
            agrees = (result & mask) == (_signed(value, width * 8) & mask)
            assert agrees, (name, value, result)
            cases.append(dict(reader=name, value=value, width=width, fed=big, result=result,
                              fedLittleEndian=little, resultLittleEndian=other,
                              agreesWithNativeOrder=agrees,
                              littleEndianRoundTrips=other == result))
    return cases


def main():
    writers = writer_cases()
    readers = reader_cases()
    components = component_cases()
    machine = harness(written=[])
    assert any(case['retractedLittleEndian'] != case['nativeBytes'] for case in writers), \
        'the retracted little-endian assumption only differs on multi-byte fields'
    report = dict(schema='ka-b4-composition-checks-1',
                  fixtures=dict(writers=len(writers), readers=len(readers),
                                componentRoundTrips=len(components)),
                  sliceManifest=machine.slice_manifest(),
                  stubs=[dict(address=hex(address), name=entry[0])
                         for address, entry in sorted(machine.stubs.items())],
                  writers=writers, readers=readers, componentRoundTrips=components,
                  findings=[
                      'The original StreamUtil writers emit every multi-byte field big-endian: '
                      'WriteShort(300) sends 01 2c and WriteInt(0x01020304) sends 01 02 03 04, and '
                      'the readers invert exactly that order. This retracts the B3 "little-endian '
                      'assumed" limit for dataId and for every int16/int32 on the same save stream, '
                      'including Parameter.value/exp. The bytes come from the native BitConverter '
                      'bodies (strh/str plus the in-place reversal in 0x2393330), not from a port.',
                      'TreasureComponent.Serialize calls WriteShort(dataId), WriteBoolean(isOpenable) '
                      'then WriteQueue(resultQueue) in that order, and Deserialize reads short, '
                      'boolean and queue in the same order and stores the queue handle at +0x18; '
                      'both directions round-trip every field through the original code.',
                      'Feeding the retracted little-endian order to the original reader yields the '
                      'byte-swapped value, so the two encodings are not interchangeable: the model '
                      'was corrected instead of the mismatch being tolerated.'],
                  limits=[
                      'One composition only: the StreamUtil wrappers, the BitConverter helpers and '
                      'the receipt component. Entity/warehouse creation, capacity readers, the '
                      'dispatch switch and every world effect remain outside this fixture.',
                      'Allocation (il2cpp_array_new), class/method initialization, throw helpers, '
                      'WriteQueue/ReadQueue and the stream payload port are stubs: they supply '
                      'memory and bytes, never order.',
                      'Null, empty and overflow paths inside the loaded slices were not run, and no '
                      'audio, rendering or end-to-end encounter behavior is claimed here.',
                      'Expected results are asserted independently of the model (explicit big-endian '
                      'packing); the model side is compared through its own _pack helper.'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(fixtures=report['fixtures'], slices=report['sliceManifest']['slices'],
                          stubs=len(report['stubs']), output=str(OUT),
                          digest=canonical_hash(report))))


if __name__ == '__main__':
    main()


