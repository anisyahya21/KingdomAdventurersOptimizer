"""Staged native composition harness: original slices under Unicorn with recorded boundaries.

B4 fixtures run several original routines against one shared synthetic object graph, record the
ordered boundary calls with their arguments and the state each boundary observes, and hand that
trace to combat_event_compare for a first-divergence comparison against the offline model.

Only instructions inside a loaded slice execute natively. Every call that leaves the loaded set is
an explicit stub, so the portion claimed equivalent is exactly the code that ran here; the stub
table plus the slice manifest hashes are the boundary record.
"""
import hashlib
import json
import struct
from pathlib import Path

import unicorn.arm64_const as arm64_const
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UcError
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_X0

from combat_initial_state import EVIDENCE
from combat_run_manifest import NATIVE_SHA256, canonical_hash

NATIVE = EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so'
AUDIT = EVIDENCE / 'audit.json'
COMPOSITION_SLICES = EVIDENCE / 'b4-composition-slices/slices.json'
# Slice manifests hash the slicer's whole window (slice_functions.WINDOW), not just the code.
SLICE_DIGEST_WINDOW = 0x1000
# The frozen B3 receipt slices, reached through the evidence junction (one physical home).
RECEIPT_SLICES = EVIDENCE.parent / '20260914-b3-receipt'

# Same address plan as the earlier Unicorn checks: one big region covering code, GOT and globals.
CODE_BASE, CODE_SIZE = 0x1000000, 0x2300000
HEAP_BASE, HEAP_SIZE = 0x10000000, 0x200000
STACK_BASE, STACK_SIZE = 0x20000000, 0x20000
PAGE_BASE, PAGE_SIZE = 0x30000000, 0x10000
SP = STACK_BASE + 0x18000
RETURN = PAGE_BASE
TRAMPOLINE = PAGE_BASE + 0x100
REGISTERS = {name: getattr(arm64_const, 'UC_ARM64_REG_' + name)
             for name in ([f'X{i}' for i in range(31)] + ['SP', 'LR', 'PC']
                          + [f'W{i}' for i in range(31)])}


class CompositionError(Exception):
    """A boundary the fixture did not declare: unmapped instruction or undeclared call."""

    def __init__(self, message, address=None, last_event=None):
        super().__init__(message)
        self.address, self.last_event = address, last_event


def manifest_key(path):
    """A unique stable identifier for one manifest, keyed by evidence-relative path.

    Slice sets share the basename `slices.json` (dispatch/, box-result-arms/, dispatch-gates/,
    getdata-arms/), so keying by `path.name` silently overwrote all but one of them. The key keeps
    the path relative to the evidence root, or to its parent for the sibling 20260914-b3-receipt
    tree shared through the junction. Only a manifest from outside the evidence tree falls back to
    its absolute path, which is still unique.
    """
    resolved = Path(path).resolve()
    for root in (EVIDENCE, EVIDENCE.parent):
        try:
            return resolved.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


class Composition:
    """One shared Unicorn machine plus slice loading, stub dispatch and an ordered trace."""

    def __init__(self, manifests=(), name='composition'):
        self.name = name
        self.binary = NATIVE.read_bytes()
        digest = hashlib.sha256(self.binary).hexdigest()
        if digest != NATIVE_SHA256:
            raise CompositionError(f'Native evidence build hash mismatch: {digest}')
        self.native_sha256 = digest
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        for base, size in ((CODE_BASE, CODE_SIZE), (HEAP_BASE, HEAP_SIZE),
                           (STACK_BASE, STACK_SIZE), (PAGE_BASE, PAGE_SIZE)):
            self.uc.mem_map(base, size)
        self.cursor = HEAP_BASE + 0x1000
        self.trampoline = TRAMPOLINE
        self.slices, self.manifests = {}, {}
        self.trace = []
        self.current = None
        self.stubs = {}
        self.loaded = 0
        for manifest in manifests:
            self.load(manifest)
        self.uc.hook_add(UC_HOOK_CODE, self._dispatch, None, 1, 0)

    # --- slice loading -------------------------------------------------------------------
    def load(self, manifest):
        """Load every function of one slice manifest and verify its per-slice digest."""
        path = Path(manifest)
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('binary') and record['binary'] != NATIVE_SHA256:
            raise CompositionError(f'Slice manifest was taken from another build: {path}')
        rows = record.get('slices')
        if rows is None:
            # audit.json: rva/end/offset for the B1/B2 slice set.
            rows = [dict(rva=row['rva'], offset=row['offset'], name=row.get('name'),
                         size=int(row['end'], 16) - int(row['rva'], 16), sha256=None)
                    for row in record['methods']]
        for row in rows:
            rva, offset = int(row['rva'], 16), int(row['offset'], 16)
            size = row.get('size') or 4 * row['instrs']
            window = self.binary[offset:offset + size]
            if len(window) != size:
                raise CompositionError(f'Truncated slice {row.get("name")} at {row["rva"]}')
            digest = hashlib.sha256(window).hexdigest()
            if row.get('sha256'):
                # Slice manifests hash the slicer window, which is longer than the code that is
                # actually loaded: only the disassembled extent is mapped, so a stray tail branch
                # cannot silently execute padding inside the same window.
                taken = self.binary[offset:offset + SLICE_DIGEST_WINDOW]
                if hashlib.sha256(taken).hexdigest() != row['sha256']:
                    raise CompositionError(f'Slice digest mismatch for {row.get("name")}')
            self.uc.mem_write(rva, window)
            self.slices[rva] = dict(name=row.get('name'), instrs=size // 4, sha256=digest)
            self.loaded += 1
        key = manifest_key(path)
        self.manifests[key] = dict(path=key,
                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                   slices=len(rows))
        return len(rows)

    def slice_manifest(self):
        return dict(nativeSha256=self.native_sha256, slices=len(self.slices),
                    manifestsLoaded=len(self.manifests),
                    manifests=self.manifests, digest=canonical_hash(dict(self.manifests)))

    # --- memory -------------------------------------------------------------------------
    def write(self, address, data):
        self.uc.mem_write(address, data)

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def w8(self, address, value):
        self.write(address, struct.pack('<B', value & 0xff))

    def w16(self, address, value):
        self.write(address, struct.pack('<H', value & 0xffff))

    def w32(self, address, value):
        self.write(address, struct.pack('<I', value & 0xffffffff))

    def w64(self, address, value):
        self.write(address, struct.pack('<Q', value & 0xffffffffffffffff))

    def r8(self, address):
        return struct.unpack('<B', self.read(address, 1))[0]

    def r16(self, address):
        return struct.unpack('<H', self.read(address, 2))[0]

    def r32(self, address):
        return struct.unpack('<i', self.read(address, 4))[0]

    def r64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def alloc(self, size, align=16):
        base = (self.cursor + align - 1) & ~(align - 1)
        self.cursor = base + max(size, 16)
        if self.cursor >= HEAP_BASE + HEAP_SIZE:
            raise CompositionError('Composition heap exhausted')
        self.write(base, bytes(max(size, 16)))
        return base

    def array(self, count, stride=8, klass=None):
        """IL2CPP-style array object: [ptr]=klass, [ptr+0x18]=length, data from +0x20."""
        base = self.alloc(0x20 + max(count, 1) * stride)
        self.w64(base, klass or 0)
        self.w32(base + 0x18, count)
        return base

    def klass(self, slots=(), initialized=True):
        """Synthetic Il2CppClass: vtable slots at their real offsets, +0xe0 = initialization flag."""
        base = self.alloc(0x2000, 16)
        self.w64(base + 0xe0, 1 if initialized else 0)
        for offset, address in dict(slots).items():
            self.w64(base + offset, address)
        return base

    def object_of(self, klass, size=0x60):
        base = self.alloc(size)
        self.w64(base, klass)
        return base

    def got(self, address, value):
        """GOT slot with the indirection the original uses: the slot points at the variable."""
        variable = self.alloc(16)
        self.w64(variable, value)
        self.w64(address, variable)
        return variable

    # --- stubs and trace -----------------------------------------------------------------
    def new_stub(self, name, handler=None, capture=()):
        """Reserve a trampoline address, register the handler and return the address."""
        address = self.trampoline
        self.trampoline += 0x10
        if self.trampoline > PAGE_BASE + PAGE_SIZE:
            raise CompositionError('Stub trampoline page exhausted')
        return self.stub(address, name, handler, capture)

    def stub(self, address, name, handler=None, capture=()):
        if address in self.slices:
            raise CompositionError(f'Stub {name} overlaps a loaded slice at {hex(address)}')
        self.stubs[address] = (name, handler, tuple(capture))
        return address

    def emit(self, kind, **fields):
        event = dict(index=len(self.trace), kind=kind, **fields)
        self.trace.append(event)
        return event

    def note(self, **fields):
        """Attach fields to the call event currently being handled."""
        if self.current is None:
            raise CompositionError('note() outside a recorded stub call')
        self.current.update(fields)
        return self.current

    def _dispatch(self, machine, address, size, user):
        entry = self.stubs.get(address)
        if entry is None:
            return
        name, handler, capture = entry
        args = [self.reg(register) for register in capture]
        event = self.emit('call', name=name, address=hex(address), args=args)
        self.current = event
        try:
            result = handler(self, *args) if handler else None
        finally:
            self.current = None
        event['result'] = None if result is None else result & 0xffffffffffffffff
        if result is not None:
            self.uc.reg_write(UC_ARM64_REG_X0, result & 0xffffffffffffffff)
        self.uc.reg_write(UC_ARM64_REG_PC, self.reg('lr'))

    # --- execution ------------------------------------------------------------------------
    def reg(self, name):
        constant = REGISTERS.get(name.upper())
        if constant is None:
            raise CompositionError(f'Unknown register {name}')
        return self.uc.reg_read(constant)

    def set_reg(self, name, value):
        constant = REGISTERS.get(name.upper())
        if constant is None:
            raise CompositionError(f'Unknown register {name}')
        self.uc.reg_write(constant, value & 0xffffffffffffffff)

    def run(self, address, registers=None, limit=200000):
        self.uc.reg_write(UC_ARM64_REG_SP, SP)
        self.uc.reg_write(UC_ARM64_REG_LR, RETURN)
        for name, value in (registers or {}).items():
            self.set_reg(name, value)
        self.emit('run', entry=hex(address))
        try:
            self.uc.emu_start(address, RETURN, count=limit)
        except UcError as error:
            raise CompositionError(f'{error}: stopped at {hex(self.reg("pc"))}',
                                   self.reg('pc'), self.trace[-1] if self.trace else None)
        if self.reg('pc') != RETURN:
            raise CompositionError(f'Ran past the return sentinel to {hex(self.reg("pc"))}',
                                   self.reg('pc'), self.trace[-1] if self.trace else None)
        return self.reg('x0')

    def trace_digest(self):
        return canonical_hash(self.trace)
