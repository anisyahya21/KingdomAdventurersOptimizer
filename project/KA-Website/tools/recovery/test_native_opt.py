import importlib.util,json,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];WEB=ROOT/'KA-Website'
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
new=load(WEB/'tools/asset_extractor/parsers/opt_parser.py','new')
old=load(ROOT/'RE-evidence/20260911-building/opt_parser.before.py','old')
files=list((WEB/'artifacts/kingdom-adventures/tmp/KA_assets/building').glob('*.opt'))
records=0
for p in files:
    a=new.parse_opt(p);b=old.parse_opt(p);assert len(a['sprites'])==len(b['sprites'])
    for x,y in zip(a['sprites'],b['sprites']):
        assert all(x[k]==y[k] for k in ['u','v','dest_x','dest_y','x','y','w','h'])
    records+=len(a['sprites'])
fields=(-1,-5,300,512,9,260,258)
sample=bytes([96,128,1,1,2])+struct.pack('>7h',*fields)*2
decoded=new.parse_opt_bytes(sample)
assert len(decoded['sprites'])==2 and decoded['sprites'][0]['raw_fields']==list(fields)
assert new.parse_opt_bytes(bytes([96,128,1,1,0]))['sprites']==[]
rejected=0
for bad in [sample[:-1],sample+b'\x00',bytes([255,1,1,1]),bytes([96,128,1,1,255])]:
    try:new.parse_opt_bytes(bad)
    except ValueError:rejected+=1
    else:raise AssertionError('Malformed or unsupported input accepted')
result={'real_files':len(files),'real_records':records,'existing_single_component_values_unchanged':True,
        'multi_component_signed_and_large_values_preserved':True,'malformed_cases_rejected':rejected}
(ROOT/'RE-evidence/20260911-building/parser-tests.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
