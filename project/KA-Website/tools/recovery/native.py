"""Frozen-input native doctor and bounded exact-target Ghidra export runner."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from inventory import ROOT, WEB, EXT, DOCS, digest, write_json

TOOLS=Path(__file__).parent
GHIDRA=Path('C:/ghidra_11.4.2_PUBLIC_20250826/ghidra_11.4.2_PUBLIC')
JAVA=Path('C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot')
DUMPER=WEB/'tools/asset_extractor/il2cpp_tools/Il2CppDumper'
TARGETS=['data.MapChipData$$get_res','data.MapChipData$$get_img','data.MapChipData$$get_seb','ResourceManagerExtension$$DrawSebEx']

def command(run,label,args,timeout=1800,cwd=None,env=None):
    try:
        result=subprocess.run([str(a) for a in args],cwd=cwd,env=env,input='\n',capture_output=True,text=True,errors='replace',timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        for stream in ['stdout','stderr']:
            value=getattr(exc,stream) or ''
            if isinstance(value,bytes): value=value.decode('utf-8',errors='replace')
            (run/(label+'.'+stream+'.log')).write_text(value,encoding='utf-8')
        write_json(run/(label+'.command.json'),{'args':[str(a) for a in args],'cwd':str(cwd),'exit_code':None,'timed_out':True,'timeout_seconds':timeout})
        raise
    (run/(label+'.stdout.log')).write_text(result.stdout,encoding='utf-8')
    (run/(label+'.stderr.log')).write_text(result.stderr,encoding='utf-8')
    write_json(run/(label+'.command.json'),{'args':[str(a) for a in args],'cwd':str(cwd),'exit_code':result.returncode,'timeout_seconds':timeout})
    if result.returncode: raise RuntimeError(f'{label} failed: {result.returncode}; see logs')
    return result

def elf_info(path,expect_machine='EM_AARCH64'):
    with path.open('rb') as f:
        elf=ELFFile(f)
        if elf['e_machine']!=expect_machine or elf.elfclass!=64 or not elf.little_endian:
            raise ValueError('Wrong ABI for ARM64 contract')
        return [dict(s.header) for s in elf.iter_segments() if s['p_type']=='PT_LOAD']

def file_offset(segments,va):
    for s in segments:
        if s['p_vaddr']<=va<s['p_vaddr']+s['p_filesz']:
            if not s['p_flags'] & 1: raise ValueError('Target not executable')
            return va-s['p_vaddr']+s['p_offset']
    raise ValueError('Target VA not file-backed')

def resolve(methods,name):
    rows=[m for m in methods if m['Name']==name]
    if len(rows)!=1: raise ValueError('Missing or ambiguous exact method: '+name)
    return rows[0]

def setup():
    run=ROOT/'RE-evidence/G2.1'/uuid.uuid4().hex[:12]
    run.mkdir(parents=True); inputs=run/'inputs'; inputs.mkdir()
    apk=EXT/'kingdom-adventurers.apk'
    with zipfile.ZipFile(apk) as z:
        for source,name in [('lib/arm64-v8a/libil2cpp.so','libil2cpp.so'),('assets/bin/Data/Managed/Metadata/global-metadata.dat','global-metadata.dat')]:
            data=z.read(source)
            if data!=(EXT/source).read_bytes(): raise ValueError('APK/extracted input mismatch')
            (inputs/name).write_bytes(data)
    binary=inputs/'libil2cpp.so'; metadata=inputs/'global-metadata.dat'; segments=elf_info(binary)
    # Copy distribution so unattended config does not mutate the installed tool.
    local_dumper=run/'dumper-tool'; shutil.copytree(DUMPER,local_dumper)
    config=json.loads((local_dumper/'config.json').read_text()); config['RequireAnyKey']=False
    write_json(local_dumper/'config.json',config)
    dump=run/'dump'; dump.mkdir()
    command(run,'dumper',[local_dumper/'Il2CppDumper.exe',binary,metadata,dump],timeout=180,cwd=local_dumper)
    methods=json.loads((dump/'script.json').read_text())['ScriptMethod']
    previous=json.loads((EXT/'docs/il2cpp-mapping-pass-002/il2cppdumper-output/script.json').read_text())['ScriptMethod']
    text=(dump/'dump.cs').read_text(encoding='utf-8-sig')
    start=text.index('public class MapChipData ')
    end=text.index('\n// Namespace:',start)
    mapchip=text[start:end]
    (run/'MapChipData.metadata.txt').write_text(mapchip,encoding='utf-8')
    targets=[]; cs=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN); b=binary.read_bytes()
    for name in TARGETS:
        m=resolve(methods,name); old=resolve(previous,name)
        if (m['Address'],m['Signature'])!=(old['Address'],old['Signature']): raise ValueError('Regenerated mapping differs for '+name)
        va=m['Address']; offset=file_offset(segments,va)
        target={'name':name,'signature':m['Signature'],'elf_va':va,'file_offset':offset,'prefix_hex':b[offset:offset+32].hex()}
        if '$$get_' in name:
            field=name.split('$$get_')[1]
            match=re.search(r'<'+field+r'>k__BackingField; // (0x[0-9A-Fa-f]+)',mapchip)
            if not match: raise ValueError('Field layout not found: '+field)
            field_offset=int(match[1],16)
            ins=list(cs.disasm(b[offset:offset+8],va))
            expected=f'w0, [x0, #0x{field_offset:x}]'
            if len(ins)!=2 or ins[0].mnemonic!='ldr' or ins[0].op_str!=expected or ins[1].mnemonic!='ret':
                raise ValueError('Getter does not match metadata field: '+name)
            target.update(field_name=field,field_offset=field_offset,decoded_getter=[i.mnemonic+' '+i.op_str for i in ins])
        targets.append(target)
    negative=[]
    for label,fn in [('wrong_abi',lambda:elf_info(EXT/'lib/armeabi-v7a/libil2cpp.so')),('missing_exact_method',lambda:resolve(methods,'data.MapChipData$$not_a_real_target'))]:
        try: fn()
        except ValueError as exc: negative.append({'case':label,'rejected':True,'error':str(exc)})
        else: raise AssertionError('Negative case did not fail')
    manifest={'schema_version':1,'work_item':'G2.1','apk':str(apk),'apk_sha256':digest(apk.read_bytes()),
              'binary':str(binary),'binary_sha256':digest(b),'metadata':str(metadata),'metadata_sha256':digest(metadata.read_bytes()),
              'abi':'arm64-v8a','original_min_load_va':min(s['p_vaddr'] for s in segments),'load_segments':segments,
              'targets':targets,'negative_cases':negative,'fresh_dump_matches_historical_selected_targets':True,
              'tools':{'python':sys.version,'ghidra':str(GHIDRA),'java':str(JAVA),'dumper_sha256':digest((local_dumper/'Il2CppDumper.dll').read_bytes())},
              'runtime_mapping':'Not established; runtime capture is outside G2.1.',
              'limit':'Four exact functions only; field layout is partial; no game rendering formula certified.'}
    (run/'ghidra-application.properties').write_bytes((GHIDRA/'Ghidra/application.properties').read_bytes())
    write_json(run/'manifest.json',manifest)
    write_json(run/'request.json',manifest)
    write_json(run/'request-wrong-base.json',{**manifest,'test_wrong_delta':0x100000})
    for p in [Path(__file__),TOOLS/'RecoveryExport.java']:
        (run/p.name).write_bytes(p.read_bytes())
    print(json.dumps({'run':str(run),'targets':len(targets),'input_pair_verified':True}),flush=True)
    return run

def export(run,label,negative=False):
    manifest=json.loads((run/'manifest.json').read_text()); binary=Path(manifest['binary'])
    if digest(binary.read_bytes())!=manifest['binary_sha256']: raise ValueError('Frozen binary changed')
    projects=run/('project-'+label); projects.mkdir(exist_ok=False)
    out=run/('export-'+label)
    env=os.environ.copy(); env['JAVA_HOME']=str(JAVA)
    command(run,'ghidra-'+label,[GHIDRA/'support/analyzeHeadless.bat',projects,'Recovery',
            '-import',binary,'-noanalysis','-scriptPath',run,'-postScript','RecoveryExport.java',
            run/('request-wrong-base.json' if negative else 'request.json'),out],cwd=run,env=env)
    receipt=json.loads((out/'receipt.json').read_text())
    if negative:
        if receipt['success'] or 'Target byte mismatch' not in receipt.get('error',''): raise ValueError('Wrong-base case not rejected for expected reason')
    elif not receipt['success'] or len(receipt['exports'])!=4:
        raise ValueError('Exact export failed: '+receipt.get('error','missing exports'))
    print(json.dumps({'export':label,'success':receipt['success'],'expected_negative':negative}),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['setup','export']); ap.add_argument('--run'); ap.add_argument('--label',default='first'); ap.add_argument('--negative',action='store_true'); args=ap.parse_args()
    if args.action=='setup': setup()
    else: export(Path(args.run).resolve(),args.label,args.negative)
