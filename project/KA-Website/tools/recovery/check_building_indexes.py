"""Bounded APK -> building archive -> two INF indexes, with raw-byte provenance."""
import sys, json, re, struct, zipfile, hashlib, importlib.util, zlib
from pathlib import Path
sys.path.insert(0,r'C:\Users\anisb\unitypy_pkgs')
import UnityPy
from inf_index import InfIndex, IndexKey
ROOT=Path(__file__).resolve().parents[3]
RUN=ROOT/'RE-evidence/G4.1a/20260910'
APK=Path('C:/APK-RE/kingdom-adventurers/kingdom-adventurers.apk')
def sha(b): return hashlib.sha256(b).hexdigest()
def save(name,obj): (RUN/name).write_text(json.dumps(obj,indent=2),encoding='utf-8')

def extract():
    found={}; sources=[]
    baseline=json.loads((ROOT/'RE-evidence/G2.1/39257e72291d/manifest.json').read_text())
    assert sha(APK.read_bytes())==baseline['apk_sha256']
    with zipfile.ZipFile(APK) as z:
        names=[n for n in z.namelist() if n.startswith('assets/bin/Data/') and '/' not in n[len('assets/bin/Data/'):]]
        # Named split serialized file first; hashed Unity bundles only if needed.
        groups=[[n for n in names if 'globalgamemanagers.assets.split' in n]]
        groups += [[n] for n in names if re.fullmatch(r'[0-9a-f]{32}',n.rsplit('/',1)[-1])]
        for group in groups:
            if not group: continue
            raw=b''.join(z.read(n) for n in sorted(group))
            env=UnityPy.load(raw)
            hits=[]
            for obj in env.objects:
                if obj.type.name!='TextAsset': continue
                d=obj.read()
                if d.m_Name not in ['building','encrypt_key']: continue
                if d.m_Name in found: raise ValueError('Duplicate target TextAsset')
                value=d.m_Script
                b=value if isinstance(value,bytes) else value.encode('utf-8',errors='surrogateescape')
                found[d.m_Name]=b
                hits.append({'name':d.m_Name,'path_id':obj.path_id,'raw_object_sha256':sha(obj.get_raw_data()),'script_sha256':sha(b)})
                (RUN/(d.m_Name+'.textasset')).write_bytes(b)
            if hits:
                sources.append({'apk_entries':[{'name':n,'sha256':sha(z.read(n))} for n in sorted(group)],'joined_sha256':sha(raw),'objects':hits})
            if len(found)==2: break
    assert len(found)==2,'Required TextAssets missing'
    key=b''.join(struct.pack('<I',int(v,16)) for v in re.findall(rb'0x[0-9a-fA-F]+',found['encrypt_key']))
    assert key
    clear=bytes(v^key[i%len(key)] for i,v in enumerate(found['building']))
    (RUN/'building.archive').write_bytes(clear)
    save('provenance.json',{'apk':str(APK),'apk_sha256':baseline['apk_sha256'],'UnityPy':UnityPy.__version__,'sources':sources,'xor_key_sha256':sha(key),'archive_sha256':sha(clear)})
    return clear

def archive(data):
    toc,total,count=struct.unpack_from('>III',data)
    assert 12<=toc<=len(data) and 0<count<10000
    pos=12; names=[]
    for _ in range(count):
        size=struct.unpack_from('>I',data,pos)[0];pos+=4
        assert 0<size<=256 and pos+size<=toc
        names.append(data[pos:pos+size].decode('ascii'));pos+=size
    assert len(set(names))==count and pos+count*8<=toc
    offsets=struct.unpack_from('>'+str(count)+'I',data,pos);pos+=4*count
    sizes=struct.unpack_from('>'+str(count)+'I',data,pos);pos+=4*count
    assert not any(data[pos:toc]),'Nonzero unparsed TOC padding'
    rows=[]; result={}; end=toc+4
    for name,offset,size in zip(names,offsets,sizes):
        start=toc+4+offset
        assert start==end and size>=4 and start+4+size<=len(data)
        selfsize=struct.unpack_from('>I',data,start)[0]
        assert selfsize==size
        end=start+4+size
        rows.append({'name':name,'slot_offset':start,'size':size,'data_offset':start+4,'length':size})
        if name in ['img.inf','seb.inf']: result[name]=data[start+4:end]
        if name=='windmill_00.png':
            png=data[start+4:end]; assert png[:8]==b'\x89PNG\r\n\x1a\n'
            q=8; chunks=0
            while q<len(png):
                length=struct.unpack_from('>I',png,q)[0]; tag=png[q+4:q+8]
                assert q+12+length<=len(png)
                assert zlib.crc32(png[q+4:q+8+length])==struct.unpack_from('>I',png,q+8+length)[0]
                q+=12+length; chunks+=1
                if tag==b'IEND': break
            assert q==len(png) and tag==b'IEND'
            save('framing-control.json',{'member':name,'all_png_chunk_crcs_valid':True,'chunks':chunks,'length':len(png),'sha256':sha(png)})
    assert end==len(data),'Unconsumed archive bytes'
    # The old description of header word 2 as data-section size is not assumed.
    save('archive-header.json',{'toc':toc,'word2_uninterpreted':total,'prefix_uninterpreted':data[toc:toc+4].hex(),'count':count,'file_length':len(data),'consumed':end})
    assert set(result)=={'img.inf','seb.inf'}
    return result,rows

def main():
    data=extract(); files,rows=archive(data)
    save('archive-records.json',rows)
    for name,b in files.items(): (RUN/name).write_bytes(b)
    comparisons=[]; indexes={}
    legacy_path=ROOT/'KA-Website/tools/asset_extractor/parsers/inf_parser.py'
    spec=importlib.util.spec_from_file_location('legacy_inf',legacy_path); legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    for name,b in files.items():
        domain='building/image' if name=='img.inf' else 'building/seb'
        index=InfIndex(domain,b); indexes[domain]=index
        # Separate byte-level row decomposition; no reuse of parser regex.
        rawrows=[]
        for line in b.split(b'\r\n'):
            if not line: continue
            idraw,value=line.split(b'\t');fields=value.split(b',')
            rawrows.append((int(idraw),fields[0].decode(),fields[1].decode() if len(fields)==2 else None))
        assert [(r['id'],r['filename'],r['suffix_uninterpreted']) for r in index.rows.values()]==rawrows
        old=ROOT/'KA-Website/artifacts/kingdom-adventures/tmp/KA_assets/building'/name
        candidate=old.read_bytes(); assert b[:-4]==candidate
        oldrows=legacy.parse_img_inf(old)
        differences=[{'id':i,'legacy_filename':oldrows.get(i),'original_filename':r['filename']} for i,r in index.rows.items() if oldrows.get(i)!=r['filename']]
        comparisons.append({'file':name,'original_sha256':sha(b),'candidate_path':str(old),'candidate_sha256':sha(candidate),'missing_tail_hex':b[-4:].hex(),'rows':len(index.rows),'filename_differences':differences})
        save(name+'.rows.json',list(index.rows.values()))
    negatives=[]
    for label,fn in [
        ('duplicate ID',lambda:InfIndex('building/seb',b'0\ta.seb\r\n0\tb.seb')),
        ('truncated row',lambda:InfIndex('building/seb',b'0\t')),
        ('invalid UTF8',lambda:InfIndex('building/seb',b'0\t\xff.seb')),
        ('extra columns',lambda:InfIndex('building/seb',b'0\ta.seb\tx')),
        ('cross domain key',lambda:indexes['building/image'].lookup(IndexKey('building/seb',0))),
        ('bare numeric key',lambda:indexes['building/image'].lookup(0)),
    ]:
        try: fn()
        except (ValueError,UnicodeError): negatives.append(label)
        else: raise AssertionError('Negative did not reject: '+label)
    result={'step':'G4.1a','success':True,'comparisons':comparisons,'negative_tests':negatives,
            'domain_example':{'image0':indexes['building/image'].lookup(IndexKey('building/image',0)),'seb0':indexes['building/seb'].lookup(IndexKey('building/seb',0))},
            'limits':['Two indexes and framing of this building archive only.','No ID join, mode meaning, placement or rendering algorithm established.','Syntactically valid truncation needs provenance/hash comparison; parser syntax checks alone cannot detect it.','Legacy copies and production outputs unchanged; use reviewed fresh indexes for this research scope only.'],
            'authored':{str(p):sha(p.read_bytes()) for p in [Path(__file__),Path(__file__).with_name('inf_index.py')]}}
    save('result.json',result)
    print(json.dumps({'success':True,'index_rows':sum(len(i.rows) for i in indexes.values()),'negative_tests':len(negatives),'truncated_legacy_indexes':len(comparisons)}))

if __name__=='__main__': main()
