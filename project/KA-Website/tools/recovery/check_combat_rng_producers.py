"""Direct RNG entry-point inventory for the preserved combat build.

Frozen-input scan: every BL/b instruction word in the libil2cpp code segment is
decoded and attributed to the IL2CPP method that contains the call site. The
Math/Lib wrappers, their shared JRandom/System.Random primitives and any other
randomness-named entry point in the dump are inventoried, wrapper bodies are
disassembled to confirm the forwarding chain, and the callers of each RNG
constructor are disassembled so seed/state ownership stays inspectable.

Only direct, statically resolved calls in this build are covered. Indirect
dispatch, live backend selection and the runtime base are not resolved here.
"""
import hashlib
import json
import re
from array import array
from bisect import bisect_right
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile

from combat_run_manifest import NATIVE_SHA256
from recover_special_combat import NATIVE, OUT

DUMP = NATIVE / 'dump/script.json'
BINARY = NATIVE / 'inputs/libil2cpp.so'
EXPORTS = OUT
EVIDENCE = OUT / 'rng-producer-checks.json'

RANDOM_NAME = re.compile(r'JRandom|System\.Random|kairo\.unity\.math\.Random|'
                         r'ext\.util\.Lib\$\$(?:Random|Hits)|UnityEngine\.Random|\$\$Hits|\$\$Rand')
SEED_MARKER = re.compile(r'DateTime|TickCount|Stopwatch|Environment|Guid|Seed|UtcNow|get_Now|InitState')
PREFIXES = (('java.util.JRandom', 'jrandom'), ('System.Random', 'system-random'),
            ('kairo.unity.math.Random', 'math'), ('ext.util.Lib$$', 'lib'),
            ('UnityEngine.Random', 'unity'))
# Each recovered wrapper must forward to exactly this direct callee.
CHAINS = {'ext.util.Lib$$Hits': 'ext.util.Lib$$Random',
          'ext.util.Lib$$Random': 'java.util.JRandom$$NextInt',
          'kairo.unity.math.Random$$Hits': 'kairo.unity.math.Random$$Rand',
          'kairo.unity.math.Random$$Rand': 'java.util.JRandom$$NextInt'}
JRANDOM_BACKENDS = ['System.Random$$.ctor', 'java.util.JRandom.Random2018$$.ctor',
                    'java.util.JRandom.Xorshift128$$.ctor']
BUCKETS = (('death', r'Die$|Dead|Defeat|Postmortem|PostMortem|Knock|Leaving|Revive|Cure'),
           ('settlement', r'Finish|Ending|Treasure|Prize|Reward|Receipt|Lottery|Chest'),
           ('campaign', r'Campaign|Mission|BattleSystem|Progress'),
           ('animation', r'Animation|Animate|Appearance|Human|Vehicle|Body'),
           ('effects', r'Effect|Projectile|Trail|Balloon|Sound|Seb'),
           ('setup', r'Init|Setup|Formation|Placement|Deploy|Clone'))


def stream_of(name):
    for prefix, label in PREFIXES:
        if name.startswith(prefix):
            return label
    return 'unclassified'


def bucket_of(name):
    for label, pattern in BUCKETS:
        if re.search(pattern, name):
            return label
    return 'other'


def main():
    digest = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    if digest != NATIVE_SHA256:
        raise AssertionError('Native evidence build hash mismatch')
    methods = json.loads(DUMP.read_text(encoding='utf-8'))['ScriptMethod']
    rows = sorted(methods, key=lambda m: m['Address'])
    addresses = [m['Address'] for m in rows]
    by_address = {m['Address']: m['Name'] for m in rows}
    by_name = {}
    for m in rows:
        by_name.setdefault(m['Name'], []).append(m['Address'])

    def owner(site):
        index = bisect_right(addresses, site) - 1
        return rows[index] if index >= 0 else None

    discovered = [m for m in rows if RANDOM_NAME.search(m['Name'])]
    producers = {}
    for m in discovered:
        # Randomness-named helper without a recovered draw role stays inventory-only.
        if stream_of(m['Name']) == 'unclassified' and not re.search(r'\$\$(?:Hits|Rand)$', m['Name']):
            continue
        producers.setdefault(m['Name'], []).append(m['Address'])
    producer_targets = {a for values in producers.values() for a in values}

    with BINARY.open('rb') as stream:
        elf = ELFFile(stream)
        segments = [dict(s.header) for s in elf.iter_segments()
                    if s['p_type'] == 'PT_LOAD' and s['p_flags'] & 1]
        stream.seek(0)
        blob = stream.read()
    if len(segments) != 1:
        raise AssertionError('Expected one executable load segment')

    sites, scanned = [], 0
    segment = segments[0]
    words = array('I', blob[segment['p_offset']:segment['p_offset'] + segment['p_filesz']])
    for index, word in enumerate(words):
        scanned += 1
        opcode = word & 0xFC000000
        if opcode != 0x94000000 and opcode != 0x14000000:
            continue
        immediate = word & 0x03FFFFFF
        if immediate & 0x02000000:
            immediate -= 0x04000000
        site = segment['p_vaddr'] + index * 4
        target = site + immediate * 4
        if target in producer_targets:
            sites.append((site, target, 'bl' if opcode == 0x94000000 else 'b'))
    sites.sort()

    calls, unattributed = [], []
    for site, target, kind in sites:
        method = owner(site)
        if method is None or not (method['Address'] <= site < method['Address'] + 0x10000):
            unattributed.append(hex(site))
            continue
        calls.append(dict(caller=method['Name'], callerAddress=hex(method['Address']), site=hex(site),
                          kind=kind, producer=by_address[target], producerAddress=hex(target),
                          stream=stream_of(by_address[target])))
    if unattributed:
        raise AssertionError('Call sites outside their containing method: ' + json.dumps(unattributed))

    cs = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    def disassemble(va, size):
        if not (segment['p_vaddr'] <= va < segment['p_vaddr'] + segment['p_filesz']):
            raise AssertionError('Address not file-backed: ' + hex(va))
        offset = va - segment['p_vaddr'] + segment['p_offset']
        return list(cs.disasm(blob[offset:offset + size], va))

    def body_calls(instructions):
        names = []
        for ins in instructions:
            if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#'):
                target = int(ins.op_str[1:], 16)
                names.append(by_address.get(target, hex(target)))
        return names

    wrapper_bodies, chain_failures = {}, []
    for name in [n for n in CHAINS] + sorted(n for n in producers if n.endswith('$$.ctor')):
        for va in by_name.get(name, []):
            wrapper_bodies.setdefault(name, []).append(
                dict(address=hex(va), callees=body_calls(disassemble(va, 0x400))))
        if name in CHAINS:
            hits = [c for entry in wrapper_bodies.get(name, []) for c in entry['callees']]
            if CHAINS[name] not in hits:
                chain_failures.append(dict(wrapper=name, expected=CHAINS[name], callees=hits))
    if chain_failures:
        raise AssertionError('Wrapper chains do not match: ' + json.dumps(chain_failures))

    jrandom_bootstrap = [c for entry in wrapper_bodies.get('java.util.JRandom$$.ctor', [])
                         for c in entry['callees']]
    for backend in JRANDOM_BACKENDS:
        if backend not in jrandom_bootstrap:
            raise AssertionError('JRandom constructor no longer bootstraps ' + backend)

    constructors = {n for n in producers if n.endswith('$$.ctor')}
    seeds = []
    for call in calls:
        if call['producer'] not in constructors:
            continue
        address = int(call['callerAddress'], 16)
        end = next((a for a in addresses if a > address), address + 0x1000)
        instructions = disassemble(address, min(end, address + 0x1000) - address)
        site = int(call['site'], 16)
        seeds.append(dict(constructor=call['producer'], caller=call['caller'], site=call['site'],
                          ownerBodyBytes=min(end, address + 0x1000) - address,
                          window=[f'{i.mnemonic} {i.op_str}'.strip() for i in instructions
                                  if site - 0x30 <= i.address <= site + 0x14],
                          ownerCallees=sorted({c for c in body_calls(instructions) if not c.startswith('0x')}),
                          seedMarkers=sorted({c for c in body_calls(instructions) if SEED_MARKER.search(c)})))

    exported = {}
    skipped = []
    for path in sorted(EXPORTS.glob('*.asm')):
        try:
            va = int(path.stem, 16)
        except ValueError:
            skipped.append(path.name)
            continue
        if va not in by_address:
            skipped.append(path.name)
            continue
        exported[va] = path
    asm_calls = set()
    for va, path in exported.items():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            match = re.match(r'\s*([0-9a-f]+): (bl|b) #0x([0-9a-f]+)', line)
            if match and int(match[3], 16) in producer_targets:
                asm_calls.add((int(match[1], 16), int(match[3], 16)))
    scan_calls = {(int(c['site'], 16), int(c['producerAddress'], 16)) for c in calls}
    exported_sites = {site for site in scan_calls if owner(site[0])['Address'] in exported}
    if asm_calls - scan_calls:
        raise AssertionError('Exported call sites missing from the scan: ' + json.dumps(sorted(asm_calls - scan_calls)))
    if exported_sites != asm_calls:
        raise AssertionError('Export/scan call-site disagreement: ' + json.dumps(sorted(exported_sites ^ asm_calls)))

    per_producer, per_stream, per_bucket, per_caller = {}, {}, {}, {}
    for call in calls:
        per_producer.setdefault(call['producer'], []).append(call)
        per_stream[call['stream']] = per_stream.get(call['stream'], 0) + 1
        per_bucket[bucket_of(call['caller'])] = per_bucket.get(bucket_of(call['caller']), 0) + 1
        per_caller.setdefault(call['caller'], set()).add(call['producer'])
    report = dict(
        schema='ka-combat-rng-producers-1',
        nativeSha256=digest,
        methodsScanned=len(rows),
        instructionWordsScanned=scanned,
        entryPoints={name: [hex(a) for a in sorted(values)] for name, values in sorted(producers.items())},
        entryPointsByStream={label: sorted({n for n in producers if stream_of(n) == label})
                             for _, label in PREFIXES},
        randomnessNamedExcluded=sorted({m['Name'] for m in discovered if m['Name'] not in producers}),
        wrapperBodies=wrapper_bodies,
        directCallSites=calls,
        callersPerProducer={name: sorted({c['caller'] for c in values}) for name, values in sorted(per_producer.items())},
        callerMethodCount=len(per_caller),
        sitesPerStream=dict(sorted(per_stream.items())),
        sitesPerSubsystem=dict(sorted(per_bucket.items())),
        constructorCallers=seeds,
        exportCrossCheck=dict(exportedFunctions=len(exported), exportedProducerSites=len(asm_calls),
                              scanSitesInExportedFunctions=len(exported_sites), agreed=True, skippedFiles=skipped),
        findings=[
            'Every Math draw in this build reaches java.util.JRandom$$NextInt through kairo.unity.math.Random$$Rand; every Lib draw reaches the same primitive through ext.util.Lib$$Random.',
            'kairo.unity.math.Random$$Hits forwards to Random$$Rand and ext.util.Lib$$Hits forwards to Lib$$Random, so the chance helpers are wrappers, not third streams.',
            'Math and Lib therefore differ by the JRandom instance each wrapper holds, not by a distinct primitive or formula.',
            'The inventory lists every randomness-named entry point in the dump, including any UnityEngine.Random use, so an unmodelled third draw source would appear here rather than silently.',
        ],
        limits=['Direct BL/b calls in the preserved build only; indirect (interface/virtual) dispatch, non-IL2CPP native code and the runtime base remain unresolved.',
                'A call site proves a draw producer is reachable from that method, not that the method runs in a Wairo/Kairo attempt.',
                'Stream names follow the recovered wrappers; the live selector, instance ownership and seed of each stream are not certified here.'],
    )
    EVIDENCE.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(methods=len(rows), entryPoints=len(producers), sites=len(calls),
                          callers=len(per_caller), streams=report['sitesPerStream'],
                          subsystems=report['sitesPerSubsystem'], constructors=len(seeds),
                          exportCrossCheck=report['exportCrossCheck']), indent=2))


if __name__ == '__main__':
    main()
