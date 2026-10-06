"""Recheck the bounded G2.1 evidence; no rendering semantics are certified."""
import argparse
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path
from native import digest, write_json, file_offset, command, JAVA

def check(run):
    m=json.loads((run/'manifest.json').read_text())
    binary=Path(m['binary']).read_bytes()
    assert digest(binary)==m['binary_sha256']
    assert digest(Path(m['metadata']).read_bytes())==m['metadata_sha256']
    first=json.loads((run/'export-first/receipt.json').read_text())
    repeat=json.loads((run/'export-repeat/receipt.json').read_text())
    assert first==repeat and first['success'] and len(first['exports'])==4
    methods=json.loads((run/'dump/script.json').read_text())['ScriptMethod']
    checked=[]
    for target,actual in zip(m['targets'],first['exports']):
        exact=[x for x in methods if x['Name']==target['name']]
        assert len(exact)==1 and exact[0]['Address']==actual['elf_va']
        assert exact[0]['Signature']==actual['metadata_signature']==target['signature']
        assert actual['name']==target['name']
        assert int(actual['ghidra_entry'],16)-int(first['image_base'],16)+m['original_min_load_va']==target['elf_va']
        hashes={}
        for key in ['assembly_file','decompile_file']:
            a=(run/'export-first'/actual[key]).read_bytes()
            assert a==(run/'export-repeat'/actual[key]).read_bytes()
            hashes[key]=digest(a)
        instructions=0
        for line in (run/'export-first'/actual['assembly_file']).read_text().splitlines():
            address,raw,_=line.split(' ',2)
            va=int(address.rstrip(':'),16)-int(first['image_base'],16)+m['original_min_load_va']
            data=bytes.fromhex(raw); offset=file_offset(m['load_segments'],va)
            assert binary[offset:offset+len(data)]==data
            instructions+=1
        c=(run/'export-first'/actual['decompile_file']).read_text()
        if 'field_name' in target: assert 'return self->'+target['field_name']+';' in c
        checked.append({'name':target['name'],'elf_va':hex(target['elf_va']),'file_offset':hex(target['file_offset']),
                        'ghidra_entry':actual['ghidra_entry'],'instruction_bytes_checked':instructions,'hashes':hashes,
                        'decompiler_warnings':len(re.findall('WARNING:',c)),
                        'prototype_status':'partial verified getter types' if 'field_name' in target else 'UNVERIFIED Ghidra inference; exact metadata prototype recorded separately; no semantic use approved'})
    wrong=json.loads((run/'export-wrong-base/receipt.json').read_text())
    assert not wrong['success'] and not wrong['exports'] and 'Target byte mismatch' in wrong['error']
    assert all(n['rejected'] for n in m['negative_cases'])
    # Exercise actual timeout handler using a harmless child process.
    try: command(run,'timeout-negative',[sys.executable,'-c','import time; print("started",flush=True); time.sleep(5)'],timeout=0.2)
    except subprocess.TimeoutExpired: pass
    else: raise AssertionError('timeout did not occur')
    assert json.loads((run/'timeout-negative.command.json').read_text())['timed_out']
    assert 'started' in (run/'timeout-negative.stdout.log').read_text()
    command(run,'java-version',[JAVA/'bin/java.exe','-version'])
    artifacts={str(p.relative_to(run)):digest(p.read_bytes()) for p in run.rglob('*') if p.is_file() and
               (p.parent.name in ['export-first','export-repeat','export-wrong-base'] or p.name in ['script.json','dump.cs','manifest.json','request.json','request-wrong-base.json','ghidra-application.properties'] or p.name.endswith('.command.json'))}
    authored={str(Path(__file__).parent/name):digest((Path(__file__).parent/name).read_bytes()) for name in ['native.py','RecoveryExport.java','check_native.py']}
    result={'work_item':'G2.1','success':True,'targets':checked,'clean_imports_identical':True,
            'negative_cases':['wrong ABI','missing exact method','wrong base','command timeout logged'],
            'packages':{name:importlib.metadata.version(name) for name in ['capstone','pyelftools']},
            'artifacts':artifacts,'authored':authored,
            'limits':['Mapping verified for these four targets in this build only; runtime base unknown.',
                      'DrawSebEx metadata identity is verified; its inferred decompiler prototype, function extent and pseudocode semantics are NOT certified.',
                      'Getter types are partial; no full object-layout or rendering algorithm approval.']}
    write_json(run/'validation.json',result)
    print(json.dumps({'success':True,'targets':len(checked),'identical_clean_imports':True}))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('run'); check(Path(p.parse_args().run).resolve())
