"""Import the hash-verified ELF and prepare bounded combat decompilation.

Run using the isolated pyghidra-mcp Python environment, with the MCP project closed.
Avoid full-program auto-analysis; retain the project for later MCP queries.
"""
import argparse,hashlib,json,os,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
EVIDENCE=ROOT/'RE-evidence/20260912-combat'
binary=ROOT/'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so'
expected='fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208'
binary_bytes=binary.read_bytes()
assert hashlib.sha256(binary_bytes).hexdigest()==expected
os.environ['GHIDRA_INSTALL_DIR']='C:/Ghidra/ghidra_12.1_PUBLIC'
os.environ['JAVA_HOME']='C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot'
import pyghidra
pyghidra.start()
from ghidra.app.decompiler import DecompInterface
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.program.model.address import AddressSet
from ghidra.program.model.symbol import SourceType
from ghidra.util.task import ConsoleTaskMonitor
audit=json.loads((EVIDENCE/'audit.json').read_text(encoding='utf-8'))
selected={0x14ed618,0x1582b10,0x168f480,0x1478ea0,0x16ac77c,0x168e800}
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--address',action='append',type=lambda value:int(value,0),help='Prepare selected audited RVA; repeat for a batch')
args=parser.parse_args()
if args.address:selected=set(args.address)
assert selected<={int(e['rva'],16) for e in audit['methods']},'Export requested methods into audit.json first'
metadata=json.loads((ROOT/'RE-evidence/G2.1/39257e72291d/dump/script.json').read_text(encoding='utf-8'))
names={m['Address']:m['Name'] for m in metadata['ScriptMethod']}
out=EVIDENCE/'ghidra';out.mkdir(exist_ok=True)
with pyghidra.open_program(binary,project_location='C:/Users/anisb/GhidraProjects/ka-combat',
    project_name='ka-combat',nested_project_location=False,analyze=False) as api:
    program=api.getCurrentProgram();monitor=ConsoleTaskMonitor()
    assert str(program.getExecutableSHA256()).lower()==expected
    transaction=program.startTransaction('Apply recovered combat method boundaries and labels')
    functions=[]
    try:
        # Ghidra imports this ET_DYN at00100000; normalize this dedicated
        # project to the RVA convention used by the matching IL2CPP dump.
        if program.getImageBase().getOffset()!=0:program.setImageBase(api.toAddr(0),True)
        assert program.getImageBase().getOffset()==0
        # Runtime null/bounds throw wrappers call exception-raising helpers
        # without a return epilogue. Their return assumption creates false
        # fallthrough into adjacent methods in bounded, no-auto-analysis mode.
        for address,name in ((0x12d23c8,'il2cpp_raise_null_reference'),(0x12d23d0,'il2cpp_raise_bounds_exception')):
            start=api.toAddr(address);body=AddressSet(start,api.toAddr(address+7))
            DisassembleCommand(start,body,False).applyTo(program,monitor)
            fn=program.getFunctionManager().getFunctionAt(start)
            if fn is None:fn=program.getFunctionManager().createFunction(name,start,body,SourceType.USER_DEFINED)
            fn.setNoReturn(True)
        for entry in audit['methods']:
            address=int(entry['rva'],16)
            if address not in selected:continue
            end=int(entry['end'],16)
            start=api.toAddr(address);body=AddressSet(start,api.toAddr(end-1))
            offset=int(entry['offset'],16)
            assert bytes(int(v)&255 for v in api.getBytes(start,min(16,end-address)))==binary_bytes[offset:offset+min(16,end-address)]
            DisassembleCommand(start,body,False).applyTo(program,monitor)
            name=re.sub(r'[^A-Za-z0-9_]','_',names.get(address,'combat'))+'_'+format(address,'x')
            fn=program.getFunctionManager().getFunctionAt(start)
            if fn is None:fn=program.getFunctionManager().createFunction(name,start,body,SourceType.USER_DEFINED)
            else:fn.setName(name,SourceType.USER_DEFINED)
            functions.append(fn)
    finally:program.endTransaction(transaction,True)
    decompiler=DecompInterface();decompiler.openProgram(program)
    rows=[]
    for fn in functions:
        result=decompiler.decompileFunction(fn,60,monitor)
        address=str(fn.getEntryPoint())
        row=dict(address=address,name=str(fn.getName()),completed=bool(result.decompileCompleted()),error=str(result.getErrorMessage()))
        if result.decompileCompleted():(out/(address+'.c')).write_text(str(result.getDecompiledFunction().getC()),encoding='utf-8')
        rows.append(row);print(json.dumps(row),flush=True)
    decompiler.dispose()
    report=dict(binarySha256=expected,imageBase=str(program.getImageBase()),language=str(program.getLanguageID()),
        methods=rows,limits=['Bounded method disassembly, not whole-program analysis.','Names and boundaries from matching native metadata/audit; decompiler prototypes remain inferred.'])
    (out/'preparation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    assert len(rows)==len(selected) and all(r['completed'] for r in rows)
