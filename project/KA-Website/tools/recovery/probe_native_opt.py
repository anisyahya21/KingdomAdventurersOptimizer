"""Targeted native OPT investigation with metadata-named direct callees."""
import json,re,shutil,sys
from pathlib import Path
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN
from native import ROOT,GHIDRA,JAVA,command,elf_info,file_offset
import os
run=ROOT/'RE-evidence/20260911-building'/(sys.argv[1] if len(sys.argv)>1 else 'native-opt');run.mkdir(exist_ok=True)
base=ROOT/'RE-evidence/G2.1/39257e72291d'
m=json.loads((base/'manifest.json').read_text());binary=Path(m['binary']);b=binary.read_bytes()
methods=json.loads((base/'dump/script.json').read_text())['ScriptMethod'];by={x['Address']:x for x in methods}
seeds=[int(a,16) for a in sys.argv[2:]] if len(sys.argv)>2 else [0x232d67c,0x23ac6e4,0x23ac7cc,0x232e860]
cs=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);segments=elf_info(binary)
targets=[];stubs={}
for address in seeds:
    end=min(a for a in by if a>address);entry=by[address];offset=file_offset(segments,address)
    targets.append({'name':entry['Name'],'signature':entry['Signature'],'elf_va':address,'prefix_hex':b[offset:offset+32].hex()})
    stubs[address]=entry;stubs[end]=by[end]
    for ins in cs.disasm(b[offset:offset+end-address],address):
        if ins.mnemonic in ('bl','b'):
            dest=int(ins.op_str.lstrip('#'),16)
            if dest in by:stubs[dest]=by[dest]
source=(Path(__file__).with_name('RecoveryExport.java')).read_text().replace('class RecoveryExport','class SliceExport')
source=source.replace('fn.setName(safeName, SourceType.USER_DEFINED);','safeName += "_" + Long.toHexString(t.get("elf_va").getAsLong());\n                fn.setName(safeName, SourceType.USER_DEFINED);')
marker='            decompiler.openProgram(currentProgram);'
code='''            for (JsonElement item : request.getAsJsonArray("stubs")) {
                JsonObject s=item.getAsJsonObject();
                Address a=currentProgram.getImageBase().add(s.get("Address").getAsLong()-originalBase);
                Function f=getFunctionAt(a); if(f==null) f=createFunction(a,s.get("Name").getAsString().replaceAll("[^A-Za-z0-9_]","_")+"_"+a);
                if(f==null) throw new Exception("Cannot create exact metadata stub "+a);
                String signature=s.get("Signature").getAsString();
                if (!s.get("applyPrototype").getAsBoolean()) continue; // Leave unsupported by-value structs uninferred.
                f.setReturnType(primitive(signature.substring(0,signature.indexOf(' '))),SourceType.USER_DEFINED);
                String params=signature.substring(signature.indexOf('(')+1,signature.lastIndexOf(')')).trim();
                ArrayList<ghidra.program.model.listing.Parameter> ps=new ArrayList<>();
                if(!params.isEmpty() && !params.equals("void")) for(String p:params.split(",")) {
                    p=p.trim(); int split=p.lastIndexOf(' ');
                    ps.add(new ParameterImpl(p.substring(split+1),primitive(p.substring(0,split)),currentProgram));
                }
                f.replaceParameters(FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS,true,SourceType.USER_DEFINED,ps.toArray(new ghidra.program.model.listing.Parameter[0]));
            }
'''
source=source.replace(marker,code+marker)
source=source.replace('    public void run()', '''    private DataType primitive(String t) {
        if(t.contains("*")) return PointerDataType.dataType;
        switch(t.trim()) {
          case "void": return VoidDataType.dataType;
          case "bool": case "uint8_t": return ByteDataType.dataType;
          case "int8_t": return SignedByteDataType.dataType;
          case "int16_t": return ShortDataType.dataType;
          case "uint16_t": return UnsignedShortDataType.dataType;
          case "int32_t": return IntegerDataType.dataType;
          case "uint32_t": return UnsignedIntegerDataType.dataType;
          case "int64_t": return LongLongDataType.dataType;
          case "uint64_t": return UnsignedLongLongDataType.dataType;
          case "float": return FloatDataType.dataType;
          case "double": return DoubleDataType.dataType;
          default: throw new IllegalArgumentException("Unmapped metadata scalar "+t);
        }
    }
    public void run()''')
(run/'SliceExport.java').write_text(source)
allowed={'void','bool','int8_t','uint8_t','int16_t','uint16_t','int32_t','uint32_t','int64_t','uint64_t','float','double'}
for s in stubs.values():
    signature=s['Signature']; params=signature[signature.index('(')+1:signature.rindex(')')].strip()
    types=[signature.split(' ')[0]]+[p.strip().rsplit(' ',1)[0] for p in params.split(',') if p.strip() and p.strip()!='void']
    s['applyPrototype']=all('*' in t or t in allowed for t in types)
(run/'request.json').write_text(json.dumps({**m,'targets':targets,'stubs':list(stubs.values())},indent=2))
project=run/'project';project.mkdir(exist_ok=True)
env=os.environ.copy();env['JAVA_HOME']=str(JAVA)
command(run,'export',[GHIDRA/'support/analyzeHeadless.bat',project,'OPT','-import',binary,'-noanalysis','-scriptPath',run,'-postScript','SliceExport.java',run/'request.json',run/'export'],env=env)
receipt=json.loads((run/'export/receipt.json').read_text());assert receipt['success'],receipt
print(json.dumps({'exported':len(receipt['exports']),'named_metadata_stubs':len(stubs)}))
