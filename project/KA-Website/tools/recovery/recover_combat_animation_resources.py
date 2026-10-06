"""Recover original chara/monster SEB durations for headless animation timing."""
import hashlib,json,re,struct,sys,zipfile
from pathlib import Path
sys.path.insert(0,'C:/Users/anisb/unitypy_pkgs')
import UnityPy
from recover_special_combat import ROOT,OUT,NATIVE

def sha(data):return hashlib.sha256(data).hexdigest()

def members(data):
    toc,_,count=struct.unpack_from('>III',data);pos=12;names=[]
    for _ in range(count):
        size=struct.unpack_from('>I',data,pos)[0];pos+=4
        names.append(data[pos:pos+size].decode());pos+=size
    offsets=struct.unpack_from('>'+str(count)+'I',data,pos);pos+=count*4
    sizes=struct.unpack_from('>'+str(count)+'I',data,pos)
    result={};end=toc+4
    for name,offset,size in zip(names,offsets,sizes):
        start=toc+4+offset
        assert start==end and struct.unpack_from('>I',data,start)[0]==size
        end=start+4+size;result[name]=data[start+4:end]
    assert end==len(data) and len(result)==count
    return result

def main():
    apk=Path('C:/APK-RE/kingdom-adventurers/kingdom-adventurers.apk')
    expected=json.loads((NATIVE/'manifest.json').read_text())['apk_sha256']
    assert sha(apk.read_bytes())==expected
    found={};sources={}
    with zipfile.ZipFile(apk) as z:
        names=[n for n in z.namelist() if n.startswith('assets/bin/Data/') and '/' not in n[len('assets/bin/Data/'):]]
        groups=[[n for n in names if 'globalgamemanagers.assets.split' in n]]
        groups += [[n] for n in names if re.fullmatch('[0-9a-f]{32}',n.rsplit('/',1)[-1])]
        for group in groups:
            if not group:continue
            raw=b''.join(z.read(n) for n in sorted(group))
            for obj in UnityPy.load(raw).objects:
                if obj.type.name!='TextAsset':continue
                d=obj.read()
                if d.m_Name not in ('chara','monster','encrypt_key'):continue
                value=d.m_Script
                value=value if isinstance(value,bytes) else value.encode('utf-8',errors='surrogateescape')
                assert d.m_Name not in found
                found[d.m_Name]=value;sources[d.m_Name]=dict(entries=group,bundleSha256=sha(raw),scriptSha256=sha(value))
            if len(found)==3:break
    assert len(found)==3
    key=b''.join(struct.pack('<I',int(v,16)) for v in re.findall(rb'0x[0-9a-fA-F]+',found['encrypt_key']))
    resources={}
    for name in ('chara','monster'):
        clear=bytes(v^key[i%len(key)] for i,v in enumerate(found[name]));files=members(clear)
        directory=OUT/(name+'-animation-original');directory.mkdir(exist_ok=True)
        rows=[]
        index=files['seb.inf'];(directory/'seb.inf').write_bytes(index)
        for line in index.decode('utf-8-sig').splitlines():
            sid,binding=line.split('\t',1);filename=binding.split(',')[0];data=files[filename]
            version=0 if data[0]<128 else 256-data[0]
            layers,frames=struct.unpack_from('>hh',data,0 if version==0 else 1)
            assert frames>0
            (directory/filename).write_bytes(data)
            rows.append(dict(id=int(sid),file=filename,flags=binding.split(',')[1:],maxFrame=frames,layers=layers,format=version,sha256=sha(data)))
        resources[name]=rows;sources[name]['archiveSha256']=sha(clear)
    report=dict(apkSha256=expected,sources=sources,resources=resources,
                scope='Original versioned SEB header durations; no sprite decoding or live playback claim')
    (OUT/'animation-resources.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({res:len(rows) for res,rows in resources.items()}))

if __name__=='__main__':main()
