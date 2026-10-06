"""Test OPT structural hypotheses against hash-verified building inputs."""
import hashlib,json,struct,importlib.util,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];WEB=ROOT/'KA-Website'
RUN=ROOT/'RE-evidence/G4.1c/20260910';DIR=WEB/'artifacts/kingdom-adventures/tmp/KA_assets/building'
def sha(b):return hashlib.sha256(b).hexdigest()
def parse(data):
    if len(data)<4:raise ValueError('short header')
    cw,ch,cols,rows=data[:4]
    if not all([cw,ch,cols,rows]):raise ValueError('zero header dimension')
    pos=4;records=[];empty=0
    for slot in range(cols*rows):
        if pos>=len(data):raise ValueError('missing slot')
        flag=data[pos]
        if flag==0:pos+=1;empty+=1;continue
        if flag!=1:raise ValueError('unknown flag')
        if pos+15>len(data):raise ValueError('truncated filled record')
        values=struct.unpack_from('<HHHHH',data,pos+4)
        records.append({'slot':slot,'offset':pos,'unknown_hex':data[pos+1:pos+4].hex(),'candidate_dx':values[0],'candidate_dy':values[1],
                        'candidate_sx':values[2],'candidate_sy':values[3],'candidate_width':values[4],'candidate_height':data[pos+14]})
        pos+=15
    if pos!=len(data):raise ValueError('trailing bytes')
    return {'header':[cw,ch,cols,rows],'records':records,'empty_slots':empty,'consumed':pos}

def main():
    manifest=json.loads((ROOT/'RE-evidence/G4.1b/20260910/result.json').read_text())
    known={Path(r['path']).name:r['after_sha256'] for r in manifest['changes']}
    oldpath=WEB/'tools/asset_extractor/parsers/opt_parser.py'
    spec=importlib.util.spec_from_file_location('old_opt',oldpath);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    result=[];failures=[];negative=[]
    before=zipfile.ZipFile(ROOT/'RE-evidence/G4.1b/20260910/before-r2.zip')
    (RUN/'baseline').mkdir(exist_ok=True)
    files=sorted(DIR.glob('*.opt'));assert len(files)==114
    for p in files:
        data=p.read_bytes();assert sha(data)==known[p.name]
        png=p.with_suffix('.png');raw=png.read_bytes();assert sha(raw)==known[png.name]
        assert raw[:8]==b'\x89PNG\r\n\x1a\n' and raw[12:16]==b'IHDR'
        w,h=struct.unpack_from('>II',raw,16)
        try: parsed=parse(data)
        except ValueError as exc:
            failures.append({'name':p.name,'failure':str(exc),'sha256':sha(data)});continue
        bounds=[];legacy=old.parse_opt(p)
        assert len(legacy['sprites'])==len(parsed['records'])
        for r,l in zip(parsed['records'],legacy['sprites']):
            assert [r[k] for k in ['candidate_dx','candidate_dy','candidate_sx','candidate_sy','candidate_width','candidate_height']]==[l[k] for k in ['dest_x','dest_y','src_x','src_y','w','h']]
            # Independent direct-byte arithmetic for every candidate numeric field.
            q=r['offset']
            direct=[data[q+i]+256*data[q+i+1] for i in [4,6,8,10,12]]+[data[q+14]]
            assert direct==[l[k] for k in ['dest_x','dest_y','src_x','src_y','w','h']]
            if not (r['candidate_width']>0 and r['candidate_height']>0 and r['candidate_sx']+r['candidate_width']<=w and r['candidate_sy']+r['candidate_height']<=h):bounds.append(r['slot'])
        # Exact recovery of original bytes provides a consumption/unknown-field check.
        rebuilt=bytearray(parsed['header']);slots={r['slot']:r for r in parsed['records']}
        for s in range(parsed['header'][2]*parsed['header'][3]):
            if s not in slots:rebuilt.append(0);continue
            r=slots[s];rebuilt.extend(b'\x01'+bytes.fromhex(r['unknown_hex'])+struct.pack('<HHHHHB',*[r[k] for k in ['candidate_dx','candidate_dy','candidate_sx','candidate_sy','candidate_width','candidate_height']]))
        assert rebuilt==data
        for label,bad in [('truncate1',data[:-1]),('truncate4',data[:-4]),('trailing_byte',data+b'\x00'),('bad_flag',data[:4]+b'\x02'+data[5:])]:
            try:parse(bad)
            except ValueError:negative.append({'file':p.name,'case':label})
            else:raise AssertionError('Accepted corruption '+p.name+':'+label)
        damaged=before.read(p.relative_to(ROOT).as_posix());assert damaged==data[:-4]
        baseline=RUN/'baseline'/p.name;baseline.write_bytes(damaged)
        historical=old.parse_opt(baseline)
        guessed=sum(r['status']=='short_recovered' for r in historical['sprites'])
        result.append({'name':p.name,'sha256':sha(data),'png_sha256':sha(raw),'png_dimensions':[w,h],**parsed,'bounds_counterexamples':bounds,'legacy_guessed_records_before':guessed,'legacy_guessed_records_after':sum(r['status']=='short_recovered' for r in legacy['sprites'])})
    out={'step':'G4.1c','scope_files':114,'parsed_files':len(result),'structural_counterexamples':failures,
         'candidate_bounds_counterexamples':sum(len(r['bounds_counterexamples']) for r in result),'negative_cases_rejected':len(negative),
         'records':sum(len(r['records']) for r in result),'empty_slots':sum(r['empty_slots'] for r in result),
         'legacy_guessed_before':sum(r['legacy_guessed_records_before'] for r in result),'legacy_guessed_after':sum(r['legacy_guessed_records_after'] for r in result),
         'files':result,'negative_cases':negative,'script_sha256':sha(Path(__file__).read_bytes()),'legacy_parser_sha256':sha(oldpath.read_bytes()),
         'limits':['Candidate layout consistency only; source/destination meanings and draw behavior not proven.','Same basename pairing is a test fixture, not a verified native asset linkage.','No SEB, other asset domains or production implementation approved.']}
    (RUN/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({k:out[k] for k in ['scope_files','parsed_files','records','empty_slots','structural_counterexamples','candidate_bounds_counterexamples','negative_cases_rejected','legacy_guessed_before','legacy_guessed_after']}))
if __name__=='__main__':main()
