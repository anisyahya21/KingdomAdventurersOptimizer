"""Resolve absolute data addresses used by IL2CPP codegen -> managed names.

Two indirections appear in the frozen ARM64 slices:

  1. direct metadata-usage global, e.g. `*slot` == Il2CppClass* / MethodInfo*
  2. a `.got` entry (`adrp xN,#page; ldr xN,[xN,#off]` with the address inside
     `.got`) whose relocation points at the real usage variable in `.data`

Il2CppDumper records the usage variables in the frozen `dump/script.json` as
`ScriptMetadata` (class/type usages) and `ScriptMetadataMethod` (method usages);
`.rela.dyn`/`.rela.plt` supply the GOT -> variable link (symbol name or addend).
This maps a requested absolute VA set to those entries, so a static dispatch
target or an owned static can be named without hand-reading the ~56 MB file.

`dump.cs` stays the cheap tool for RVA -> signature (see resolve_symbols.py);
this one is for data slots, which dump.cs does not carry.

Reads frozen inputs only; writes additive output under
RE-evidence/20260914-b3-receipt/.
"""
import json
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def find_native():
    for parent in Path(__file__).resolve().parents:
        cand = parent / 'RE-evidence/G2.1/39257e72291d'
        if (cand / 'dump/script.json').is_file():
            return cand
    raise SystemExit('could not locate dump/script.json')


NATIVE = find_native()
SCRIPT = NATIVE / 'dump/script.json'
BINARY = NATIVE / 'inputs/libil2cpp.so'
OUT = NATIVE.parents[1] / '20260914-b3-receipt'

AARCH64_RELOC = {
    257: 'ABS64', 258: 'ABS32', 1024: 'COPY', 1025: 'GLOB_DAT',
    1026: 'JUMP_SLOT', 1027: 'RELATIVE', 1028: 'TLS_DTPMOD64',
    1029: 'TLS_DTPREL64', 1030: 'TLS_TPREL64', 1031: 'TLSDESC',
    1032: 'IRELATIVE', 1033: 'ABS16', 1034: 'PREL64',
}


def section_of(elf, va):
    for sec in elf.iter_sections():
        addr, size = sec['sh_addr'], sec['sh_size']
        if addr and size and addr <= va < addr + size:
            return sec.name
    return None


def load_relocations(path):
    """r_offset -> (type_name, symbol_name, addend) for every RELA section."""
    relocs = {}
    with path.open('rb') as fh:
        elf = ELFFile(fh)
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            symtab = elf.get_section(sec['sh_link'])
            for entry in sec.iter_relocations():
                sym = None
                idx = entry['r_info_sym']
                if idx:
                    name = symtab.get_symbol(idx).name
                    sym = name or None
                relocs.setdefault(entry['r_offset'], (
                    AARCH64_RELOC.get(entry['r_info_type'], str(entry['r_info_type'])),
                    sym,
                    entry['r_addend'],
                    sec.name,
                ))
    return relocs

# Absolute data addresses referenced by the B3 slices (adrp page + ldr offset).
# The B3 code reaches these through `.got`, which resolves to the real usage
# variables in `.data` (see load_relocations).
# NOTE: addresses are page(base) + ldr offset from the slices, i.e. for
# `adrp xN,#0x2f65000; ldr xN,[xN,#0x408]` the address is 0x2F65408. Three
# CountTreasureStock entries were originally transcribed as 0x2F650xxx and
# resolved to nothing; corrected 2026-09-14.
TARGETS = [
    0x2F5EA08,   # kairo.unity.ecs.Entity_TypeInfo -> Entity.treasureComponents_ (static +0x170)
    0x2F5F048,   # ecs.TreasureComponent_TypeInfo (AddTreasure class-init)
    0x2F5F050,   # Method$Stack<TreasureComponent>.Pop()   (AddTreasure)
    0x2F5F058,   # Method$Stack<TreasureComponent>.get_Count()
    0x2F5F060,   # Method$Stack<TreasureComponent>.Push()  (RemoveTreasure)
    0x2F60A28,   # Method$Enumerable.Sum<Entity>()        (CountTreasureStock)
    0x2F5D7B0,   # Method$Enumerable.Where<Entity>()      (CountTreasureStock)
    0x2F5D5B8,   # System.Func<Entity, bool>_TypeInfo     (CountTreasureStock cache #1)
    0x2F5C508,   # System.Func<Entity, int>_TypeInfo      (CountTreasureStock cache #2)
    0x2F65408,   # CountTreasureStock: MethodInfo of the Func<Entity, bool> lambda body
    0x2F65410,   # CountTreasureStock: MethodInfo of the Func<Entity, int> lambda body
    0x2F653D0,   # CountTreasureStock: klass owning the cached statics + both lambda bodies
    0x2F5CB48,   # Method$kairo.unity.ecs.World.GetSystem<WarehouseSystem>()
    0x2F5B890,   # CreateTreasure / AISystem.TreasureBoxResult: master-data lookup class
    0x2F5B828,   # ecs.TreasureComponent.Serialize/Deserialize: int writer/reader type
    0x2F614C0,   # ecs.TreasureComponent.Serialize: MethodInfo of the resultQueue writer
    0x2F614C8,   # ecs.TreasureComponent.Deserialize: MethodInfo of the resultQueue reader
    0x2F60920,   # AISystem.TreasureBoxResult: MethodInfo of the queue take
    0x2F5D210,   # AISystem.TreasureBoxResult: cast target klass (bitset branch)
    0x2F5D1F8,   # AISystem.TreasureBoxResult: cast target klass (w25 == 0x18 branch)
    0x2F60908,   # AISystem.TreasureBoxResult: class-init only
    0x2F60910,   # AISystem.TreasureBoxResult: class-init only
    0x2F60918,   # AISystem.TreasureBoxResult: class-init only
]


def main():
    # ad-hoc form: `lookup_metadata_slots.py 0x2F603C0 0x2F5D0D0 ...` resolves just
    # those slots (printed only; the frozen TARGETS manifest is left untouched).
    wanted = [int(a, 16) for a in sys.argv[1:] if a.lower().startswith('0x')]
    ad_hoc = bool(wanted)
    targets = wanted or TARGETS

    doc = json.loads(SCRIPT.read_text(encoding='utf-8'))
    tables = {
        'ScriptMetadata': {e['Address']: e for e in doc.get('ScriptMetadata', [])},
        'ScriptMetadataMethod': {e['Address']: e for e in doc.get('ScriptMetadataMethod', [])},
        'ScriptMethod': {e['Address']: e for e in doc.get('ScriptMethod', [])},
    }

    def lookup(addr):
        return {kind: {k: v for k, v in table[addr].items() if k != 'Address'}
                for kind, table in tables.items() if addr in table}

    relocs = load_relocations(BINARY)
    resolved = {}
    with BINARY.open('rb') as fh:
        elf = ELFFile(fh)
        for va in targets:
            entry = {
                'section': section_of(elf, va),
                'direct': lookup(va) or None,
                'reloc': None,
                'followed': None,
            }
            reloc = relocs.get(va)
            if reloc:
                kind, sym, addend, where = reloc
                entry['reloc'] = {'kind': kind, 'symbol': sym,
                                  'addend': hex(addend) if addend else None,
                                  'table': where}
                if addend:
                    entry['followed'] = {
                        'address': hex(addend),
                        'section': section_of(elf, addend),
                        'hits': lookup(addend) or None,
                    }
            resolved[hex(va)] = entry

    if not ad_hoc:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'metadata-slots.json').write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    for va in targets:
        entry = resolved[hex(va)]
        print(f'{hex(va):>10}  [{entry["section"]}]')
        if entry['direct']:
            for kind, hit in entry['direct'].items():
                print(f'{"":>10}    direct {kind}: {hit.get("Name")}')
        if entry['reloc']:
            reloc = entry['reloc']
            print(f'{"":>10}    {reloc["table"]}/{reloc["kind"]}'
                  f' symbol={reloc["symbol"]} addend={reloc["addend"]}')
        if entry['followed']:
            f = entry['followed']
            print(f'{"":>10}    -> {f["address"]} [{f["section"]}]')
            hits = f['hits'] or {}
            if not hits:
                print(f'{"":>10}       <no dump entry at that address>')
            for kind, hit in hits.items():
                print(f'{"":>10}       {kind}: {hit.get("Name")}')
                if hit.get('Signature'):
                    print(f'{"":>10}         {hit["Signature"]}')
        if not entry['direct'] and not entry['reloc']:
            print(f'{"":>10}    <nothing>')


if __name__ == '__main__':
    main()
