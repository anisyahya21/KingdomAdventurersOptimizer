"""Resolve RVA -> method declaration using the frozen Il2CppDumper dump.cs.

dump.cs carries a comment line per method of the form:
    // RVA: 0x159F554 Offset: 0x159B554 VA: 0x159F554
followed by the declaration on the next non-empty line. This maps a requested RVA set
to its declaration without touching any frozen evidence.
"""
import json
import re
from pathlib import Path


def find_native():
    for parent in Path(__file__).resolve().parents:
        cand = parent / 'RE-evidence/G2.1/39257e72291d'
        if (cand / 'dump/dump.cs').is_file():
            return cand
    raise SystemExit('could not locate dump/dump.cs')


NATIVE = find_native()
DUMP = NATIVE / 'dump/dump.cs'
OUT = NATIVE.parents[1] / '20260914-b3-receipt'

TARGETS = [
    0x1473234, 0x1473360, 0x1479898, 0x159F554, 0x1630CF4, 0x14D2608, 0x1475B3C,
    0x159F73C, 0x159F924,
    # unresolved callees referenced by the B3 slices
    0x191DE04, 0x16196B0, 0x18ABFAC, 0x18A5C5C, 0x1CB5FC8, 0x1CB6438,
    0x161D94C, 0x1332890, 0x161C200, 0x1474148,
    # param-component save chain (added 2026-09-14, step A)
    0x14CEC98, 0x14CED54, 0x14CEABC, 0x16831D0, 0x16833EC, 0x1682A9C, 0x1682B98,
    0x14C30C8, 0x147DDFC, 0x14C89D0, 0x1471200, 0x14C086C, 0x14C0870,
    # stream writers + dictionary helpers used by the save chain
    0x23AE20C, 0x23A45F8, 0x23AE24C, 0x1B11278, 0x1B11990, 0x1BE923C, 0x1BE9354,
    0x23AE47C, 0x23ACA88, 0x23AC6E4, 0x14C31C4, 0x147DE00, 0x1682DD0,
    0x23A37F8, 0x23AC7CC, 0x238FA5C, 0x1473E24,
]

RVA_RE = re.compile(r'//\s*RVA:\s*(0x[0-9A-Fa-f]+)')


def main():
    wanted = {t: None for t in TARGETS}
    pending = None
    with DUMP.open('r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            stripped = line.strip()
            m = RVA_RE.search(line)
            if m:
                rva = int(m.group(1), 16)
                # remember only if it is one we want and the decl follows
                pending = rva if rva in wanted and wanted[rva] is None else None
                continue
            if pending is not None and stripped:
                wanted[pending] = stripped
                pending = None
    missing = [hex(k) for k, v in wanted.items() if v is None]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'resolved-symbols.json').write_text(
        json.dumps({hex(k): v for k, v in sorted(wanted.items())}, indent=2) + '\n',
        encoding='utf-8')
    for k in sorted(wanted):
        print(f'{hex(k):>10}  {wanted[k]}')
    if missing:
        print('UNRESOLVED:', ', '.join(missing))


if __name__ == '__main__':
    main()
