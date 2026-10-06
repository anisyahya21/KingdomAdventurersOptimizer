"""One-rich-battle schema-driven binary experiment. No source database writes.

Field names/types live in a versioned external schema. Strings use external
dictionary IDs, flags use bits, seeds are uint32, numbers use small bit fields
or varints, floats retain IEEE-754 bits. Changed shapes/types use counted JSON
escape payloads. Schema NEVER embeds numeric/bool/null leaf values as defaults.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import threading
import zlib

HERE=Path(__file__).resolve().parent
VERSION=2
ENUMS={
    'status':['loss','certified','terminal-policy-dispatch','win-unproven','unresolved','error','censored'],
    'reward-basis':['reward-entitlement-certificate','native-win-loss-gate','native-win-dispatch-gate','unknown-win-without-certificate','unknown-unresolved-battle'],
    'role':['dps','healer','fodder'],
    'finish-policy':['on-verdict'],
}
REFERENCES={
    ('result','herbMetrics'):('result','encounterTelemetry','resource','herb'),
    ('result','rewardOutcome'):('result','encounterTelemetry','finalStatus','rewardOutcome'),
    ('result','resourceUses'):('result','encounterTelemetry','resource','resourceUses'),
    ('result','survivors'):('result','encounterTelemetry','ownSurvivors'),
    ('outcome','seeds'):('result','seeds'),
    ('outcome','ticks'):('result','ticks'),
    ('outcome','verdict'):('result','verdict'),
    ('outcome','censored'):('result','censored'),
    ('outcome','pending'):('result','rewardOutcome','pendingChests'),
    ('outcome','awardedReported'):('result','rewardOutcome','awardedChests'),
    ('outcome','finishPolicy'):('result','encounterTelemetry','finalStatus','finishPolicy'),
    ('outcome','costs','resourceUses'):('result','resourceUses'),
    ('outcome','status'):('outcome','basis'),
}
for field,target in {
    'bossDeathTick':('bossFirstDeathTick',),'bossIdentity':('bossIdentity',),'bossLeavingTick':('bossLeavingTick',),
    'commandsReleasedAfterDeath':('commands','releasedAfterDeath'),
    'commandsReleasedAfterDeathTargetingBoss':('commands','releasedAfterDeathTargetingBoss'),
    'commandsTargetingBoss':('commands','targetingBoss'),
    'commandsTargetingBossReleased':('commands','targetingBossReleased'),
    'firstPostDeathCommandReleaseTick':('commands','firstReleaseAfterDeathTick'),
    'lastPostDeathCommandReleaseTick':('commands','lastReleaseAfterDeathTick'),
    'maxSimultaneousCommandsTargetingBoss':('commands','maxSimultaneousTargetingBoss'),
    'maxSimultaneousStoredCommands':('commands','maxSimultaneous'),
    'postDeathBossLeavings':('bossLifecycle','leavings'),'postDeathBossReentries':('bossLifecycle','reentries'),
    'postDeathPrizes':('bossLifecycle','postDeathPrizes'),
    'storedCommandsAtDeath':('commands','remainingAtDeath'),
    'storedCommandsTargetingBossAtDeath':('commands','remainingTargetingBossAtDeath'),
    'storedTargetHoldersAtDeath':('commands','targetHoldersAtDeath'),'storedTargetHoldersPeak':('commands','targetHoldersPeak'),
}.items(): REFERENCES[('result','progressMetrics',field)]=('result','encounterTelemetry')+target
for field in ('sample_key','candidate_id','seed_a','seed_b','created_at'):
    REFERENCES[('links',0,field)]=('columns',field)

class Bits:
    def __init__(self,raw=None):
        self.raw=bytearray() if raw is None else bytearray(raw)
        self.position=0
    def put(self,value,width):
        if value<0 or value >= 1<<width:
            raise ValueError('value outside bit field')
        remaining=width
        while remaining:
            index=self.position//8
            if index==len(self.raw): self.raw.append(0)
            take=min(remaining,8-self.position%8)
            self.raw[index]|=(value&((1<<take)-1))<<(self.position%8)
            value>>=take; self.position+=take; remaining-=take
    def get(self,width):
        if self.position+width>len(self.raw)*8: raise ValueError('truncated record')
        result=0
        remaining=width; offset=0
        while remaining:
            take=min(remaining,8-self.position%8)
            result|=((self.raw[self.position//8]>>(self.position%8))&((1<<take)-1))<<offset
            self.position+=take; remaining-=take; offset+=take
        return result
    def varint(self,value):
        if value<0: raise ValueError('unsigned varint is negative')
        while value>=128:
            self.put((value&127)|128,8); value>>=7
        self.put(value,8)
    def read_varint(self):
        value=0
        for shift in range(0,1024,7):
            byte=self.get(8); value|=(byte&127)<<shift
            if byte<128: return value
        raise ValueError('varint exceeds bound')
    def bytes(self,raw):
        for byte in raw: self.put(byte,8)
    def read_bytes(self,length):
        if length>8*1024*1024: raise ValueError('escape exceeds bound')
        return bytes(self.get(8) for _ in range(length))

def dumps(value):
    return json.dumps(value,separators=(',',':'),ensure_ascii=False,allow_nan=False)

DIGEST_FIELDS=('verdict','censored','ticks','prizeCallbacks','retained','survivors','resourceUses','healthFraction','behavior','seeds')
def legacy_digest(result):
    core={key:result[key] for key in DIGEST_FIELDS}
    raw=json.dumps(core,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()

class Codec:
    def __init__(self,example):
        self.dictionary=[]
        self.ids={}
        self.schema=self.compile(example,())
        self.field_bits={}
    @classmethod
    def from_definitions(cls,schema_doc,dictionary):
        if schema_doc['version']!=VERSION or schema_doc['enums']!=ENUMS:
            raise ValueError('incompatible schema/enum definitions')
        codec=cls.__new__(cls)
        codec.schema=schema_doc['schema']; codec.dictionary=list(dictionary)
        codec.ids={s:i for i,s in enumerate(dictionary)}; codec.field_bits={}
        return codec
    def string_id(self,value):
        if value not in self.ids:
            self.ids[value]=len(self.dictionary); self.dictionary.append(value)
        return self.ids[value]
    def compile(self,value,path,allow_reference=True):
        if path==('result','digest'):
            return dict(type='legacy-core-sha256',fields=list(DIGEST_FIELDS),fallback=dict(type='hash256'))
        if allow_reference and path in REFERENCES:
            return dict(type='reference',target=list(REFERENCES[path]),fallback=self.compile(value,path,False))
        if isinstance(value,dict):
            return dict(type='object',fields=[[key,self.compile(v,path+(key,))] for key,v in value.items()])
        if isinstance(value,list):
            if value and all(type(v) is int for v in value) and not ('seeds' in path):
                return dict(type='integer-array-delta',length=len(value))
            return dict(type='array',items=[self.compile(v,path+(i,)) for i,v in enumerate(value)])
        if type(value) is bool: return dict(type='flag')
        if type(value) is int:
            if 'seeds' in path or path[-1:] in (('seed_a',),('seed_b',)): return dict(type='uint32')
            return dict(type='integer',signed=True)
        if type(value) is float: return dict(type='float64')
        if isinstance(value,str):
            if path==('columns','timing'):
                try:
                    parsed=json.loads(value)
                    if dumps(parsed)==value:
                        return dict(type='json-text',inner=self.compile(parsed,path+('parsed',)))
                except ValueError: pass
                return dict(type='literal-text')
            if (path==('result','digest') or (path and path[-1] in ('sample_key','candidate_id'))) and len(value)==64:
                bytes.fromhex(value)
                return dict(type='hash256')
            enum=('status' if path[-1:] == ('status',) else
                  'reward-basis' if path[-1:] == ('awardedBasis',) else
                  'role' if 'ownRoles' in path else
                  'finish-policy' if path[-1:] == ('finishPolicy',) else None)
            if enum and value in ENUMS[enum]: return dict(type='enum',enum=enum)
            self.string_id(value)
            return dict(type='dictionary-reference')
        if value is None: return dict(type='nullable-extension')
        raise TypeError(type(value))
    def collect(self,value,schema=None):
        schema=self.schema if schema is None else schema
        kind=schema['type']
        if kind=='legacy-core-sha256': return
        if kind=='reference': self.collect(value,schema['fallback']); return
        if kind=='json-text':
            if self.fits(schema,value): self.collect(json.loads(value),schema['inner'])
            return
        if value is None or not self.fits(schema,value): return
        if kind=='dictionary-reference': self.string_id(value)
        elif kind=='object':
            for key,child in schema['fields']: self.collect(value[key],child)
        elif kind=='array':
            for child,v in zip(schema['items'],value): self.collect(v,child)
    def escape(self,bits,value):
        raw=dumps(value).encode(); bits.varint(len(raw)); bits.bytes(raw)
    def read_escape(self,bits): return json.loads(bits.read_bytes(bits.read_varint()))
    def fits(self,schema,value):
        kind=schema['type']
        if kind=='object': return isinstance(value,dict) and set(value)=={k for k,_ in schema['fields']}
        if kind=='array': return isinstance(value,list) and len(value)==len(schema['items'])
        if kind=='integer-array-delta': return isinstance(value,list) and len(value)==schema['length'] and all(type(v) is int for v in value)
        if kind=='flag': return type(value) is bool
        if kind=='enum': return isinstance(value,str) and value in ENUMS[schema['enum']]
        if kind=='integer': return type(value) is int and (schema['signed'] or value>=0)
        if kind=='uint32': return type(value) is int and 0<=value<2**32
        if kind=='fixed-uint': return type(value) is int and 0<=value<2**schema['bits']
        if kind=='float64': return type(value) is float and math.isfinite(value)
        if kind=='dictionary-reference': return isinstance(value,str) and value in self.ids
        if kind=='literal-text': return isinstance(value,str)
        if kind=='json-text':
            try: return isinstance(value,str) and dumps(json.loads(value))==value
            except (ValueError,TypeError): return False
        if kind=='hash256':
            try: return isinstance(value,str) and len(value)==64 and bytes.fromhex(value).hex()==value
            except ValueError: return False
        return False
    def encode_node(self,schema,value,bits,width,path=()):
        before=bits.position
        self._encode_node(schema,value,bits,width,path)
        self.field_bits['/'.join(map(str,path))]=bits.position-before
        self.encoded_paths[path]=value
    def _encode_node(self,schema,value,bits,width,path):
        if schema['type']=='legacy-core-sha256':
            try: derived=type(value) is str and value==legacy_digest(self.encoding_root['result'])
            except (KeyError,ValueError,TypeError): derived=False
            bits.put(int(derived),1)
            if not derived: self._encode_node(schema['fallback'],value,bits,width,path)
            return
        if schema['type']=='reference':
            target=tuple(schema['target'])
            equal=False
            if target in self.encoded_paths:
                try: exact(value,self.encoded_paths[target]); equal=True
                except AssertionError: pass
            bits.put(int(equal),1)
            if not equal: self._encode_node(schema['fallback'],value,bits,width,path)
            return
        if schema['type']=='fixed-uint' and type(value) is int and value>=2**schema['bits']:
            bits.put(3,2); bits.varint(value); return
        if schema['type']=='flag':
            if value is None: bits.put(0,2)
            elif type(value) is bool: bits.put(2 if value else 1,2)
            else: bits.put(3,2); self.escape(bits,value)
            return
        # Two-bit state: NULL, normal schema value, or lossless extension.
        if value is None: bits.put(0,2); return
        if not self.fits(schema,value): bits.put(2,2); self.escape(bits,value); return
        bits.put(1,2)
        kind=schema['type']
        if kind=='object':
            for key,child in schema['fields']: self.encode_node(child,value[key],bits,width,path+(key,))
        elif kind=='array':
            for i,(child,v) in enumerate(zip(schema['items'],value)): self.encode_node(child,v,bits,width,path+(i,))
        elif kind=='integer-array-delta':
            prior=0
            for v in value:
                delta=v-prior; bits.varint(delta*2 if delta>=0 else -delta*2-1); prior=v
        elif kind=='flag': bits.put(int(value),1)
        elif kind=='uint32': bits.put(value,32)
        elif kind=='fixed-uint': bits.put(value,schema['bits'])
        elif kind=='integer':
            number=(value*2 if value>=0 else -value*2-1) if schema['signed'] else value
            small=number<16; bits.put(int(small),1)
            if small: bits.put(number,4)
            else: bits.varint(number)
        elif kind=='float64': bits.bytes(struct.pack('<d',value))
        elif kind=='hash256': bits.bytes(bytes.fromhex(value))
        elif kind=='dictionary-reference': bits.put(self.ids[value],width)
        elif kind=='literal-text':
            raw=value.encode(); bits.varint(len(raw)); bits.bytes(raw)
        elif kind=='json-text': self.encode_node(schema['inner'],json.loads(value),bits,width,path+('parsed',))
        elif kind=='enum':
            values=ENUMS[schema['enum']]; bits.put(values.index(value),max(1,(len(values)-1).bit_length()))
    def decode_node(self,schema,bits,width,path=()):
        value=self._decode_node(schema,bits,width,path)
        self.decoded_paths[path]=value
        return value
    def _decode_node(self,schema,bits,width,path):
        if schema['type']=='legacy-core-sha256':
            if schema['fields']!=list(DIGEST_FIELDS): raise ValueError('incompatible digest recipe')
            if bits.get(1): self.derive_digest=True; return None
            return self._decode_node(schema['fallback'],bits,width,path)
        if schema['type']=='reference':
            if bits.get(1):
                target=tuple(schema['target'])
                if target not in self.decoded_paths: raise ValueError('forward/invalid reference')
                return copy.deepcopy(self.decoded_paths[target])
            return self._decode_node(schema['fallback'],bits,width,path)
        state=bits.get(2)
        if schema['type']=='fixed-uint' and state==3: return bits.read_varint()
        if schema['type']=='flag':
            return None if state==0 else False if state==1 else True if state==2 else self.read_escape(bits)
        if state==0: return None
        if state==2: return self.read_escape(bits)
        if state!=1: raise ValueError('reserved state')
        kind=schema['type']
        if kind=='object':
            result={key:self.decode_node(child,bits,width,path+(key,)) for key,child in schema['fields']}
            if path==('result',) and self.derive_digest:
                result['digest']=legacy_digest(result)
                self.decoded_paths[('result','digest')]=result['digest']
            return result
        if kind=='array': return [self.decode_node(child,bits,width,path+(i,)) for i,child in enumerate(schema['items'])]
        if kind=='integer-array-delta':
            values=[]; prior=0
            for _ in range(schema['length']):
                n=bits.read_varint(); prior+=-(n//2)-1 if n&1 else n//2; values.append(prior)
            return values
        if kind=='flag': return bool(bits.get(1))
        if kind=='uint32': return bits.get(32)
        if kind=='fixed-uint': return bits.get(schema['bits'])
        if kind=='integer':
            n=bits.get(4) if bits.get(1) else bits.read_varint()
            return (-(n//2)-1 if n&1 else n//2) if schema['signed'] else n
        if kind=='float64': return struct.unpack('<d',bits.read_bytes(8))[0]
        if kind=='hash256': return bits.read_bytes(32).hex()
        if kind=='dictionary-reference':
            index=bits.get(width)
            if index>=len(self.dictionary): raise ValueError('dictionary ID outside definition')
            return self.dictionary[index]
        if kind=='literal-text': return bits.read_bytes(bits.read_varint()).decode()
        if kind=='json-text': return dumps(self.decode_node(schema['inner'],bits,width,path+('parsed',)))
        if kind=='enum':
            values=ENUMS[schema['enum']]; index=bits.get(max(1,(len(values)-1).bit_length()))
            if index>=len(values): raise ValueError('unknown enum code')
            return values[index]
        raise ValueError('unsupported schema type')
    def encode(self,value):
        self.encoding_root=value
        self.field_bits={}
        self.encoded_paths={}
        if not getattr(self,'frozen',False): self.collect(value)
        width=max(1,(len(self.dictionary)-1).bit_length())
        bits=Bits(); self.encode_node(self.schema,value,bits,width)
        body=bytes((VERSION,width))+bytes(bits.raw)
        return body+struct.pack('<I',zlib.crc32(body))
    def decode(self,packet):
        if len(packet)<6 or packet[0]!=VERSION: raise ValueError('bad version/header')
        if zlib.crc32(packet[:-4])!=struct.unpack('<I',packet[-4:])[0]: raise ValueError('checksum mismatch')
        self.decoded_paths={}
        self.derive_digest=False
        bits=Bits(packet[2:-4]); value=self.decode_node(self.schema,bits,packet[1])
        if len(bits.raw)*8-bits.position>=8 or bits.get(len(bits.raw)*8-bits.position)!=0:
            raise ValueError('trailing bits')
        return value

def exact(left,right):
    assert type(left) is type(right)
    if isinstance(left,dict):
        assert left.keys()==right.keys()
        for key in left: exact(left[key],right[key])
    elif isinstance(left,list):
        assert len(left)==len(right)
        for a,b in zip(left,right): exact(a,b)
    elif type(left) is float: assert struct.pack('<d',left)==struct.pack('<d',right)
    else: assert left==right

# The production-facing wrapper freezes training definitions. Unknown strings
# are inline escapes, never additions to an unaccounted mutable dictionary.
REGISTRY_VERSION=3
MAGIC=b'KAB\x03'
MAX_BYTES=8*1024*1024
DEFAULT_DEFINITIONS=Path(__file__).with_name('battle_binary_definitions.json')

def shape(value):
    if isinstance(value,dict): return {k:shape(v) for k,v in sorted(value.items())}
    if isinstance(value,list): return [shape(v) for v in value]
    return 'scalar'

def shape_key(value):
    return hashlib.sha256(dumps(shape(value)).encode()).hexdigest()

class RecordEncoder:
    def __init__(self,definitions):
        if definitions['registryVersion']!=REGISTRY_VERSION: raise ValueError('unsupported registry')
        self.definitions=definitions
        self.identity=hashlib.sha256(dumps(definitions).encode()).digest()[:8]
        self.shapes={key:i for i,key in enumerate(definitions['shapes'])}
        self.codecs=[]
        for schema in definitions['schemas']:
            codec=Codec.from_definitions(dict(version=VERSION,schema=schema,enums=ENUMS),definitions['dictionary'])
            codec.frozen=True; self.codecs.append(codec)
        self.fallbacks=0
    @classmethod
    def fit(cls,values):
        keys=[]; schemas=[]; strings={}
        for value in values:
            codec=Codec(value)
            for string in codec.dictionary: strings.setdefault(string,len(strings))
            key=shape_key(value)
            if key not in keys:
                keys.append(key); schemas.append(codec.schema)
        return cls(dict(registryVersion=REGISTRY_VERSION,codecVersion=VERSION,
                        shapes=keys,schemas=schemas,dictionary=list(strings)))
    def encode(self,value):
        index=self.shapes.get(shape_key(value),65535)
        if index==65535:
            self.fallbacks+=1
            raw=dumps(value).encode()
            if len(raw)>MAX_BYTES: raise ValueError('record exceeds bound')
            body=zlib.compress(raw,1)
            body=struct.pack('<II',len(raw),zlib.crc32(raw))+body
        else: body=self.codecs[index].encode(value)
        return MAGIC+self.identity+struct.pack('<H',index)+body
    def decode(self,packet):
        if len(packet)<14 or packet[:4]!=MAGIC or packet[4:12]!=self.identity:
            raise ValueError('packet version/definition fingerprint mismatch')
        index=struct.unpack('<H',packet[12:14])[0]
        if index==65535:
            if len(packet)<22: raise ValueError('truncated escape')
            length,crc=struct.unpack('<II',packet[14:22])
            if length>MAX_BYTES: raise ValueError('escape exceeds bound')
            inflater=zlib.decompressobj(); raw=inflater.decompress(packet[22:],length+1)
            if len(raw)!=length or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail or zlib.crc32(raw)!=crc:
                raise ValueError('invalid escape')
            return json.loads(raw)
        if index>=len(self.codecs): raise ValueError('schema ID outside definitions')
        return self.codecs[index].decode(packet[14:])

_default=None
_default_lock=threading.RLock()
def default_encoder():
    global _default
    if _default is None:
        _default=RecordEncoder(json.loads(DEFAULT_DEFINITIONS.read_text(encoding='utf-8')))
    return _default

def encode_record(value):
    with _default_lock:
        encoder=default_encoder()
        if isinstance(value,dict) and set(value)=={'result','outcome','timing'}:
            core=encoder.encode({k:value[k] for k in ('result','outcome')})
            timing=encoder.encode({'timing':value['timing']})
            return b'KAT\x01'+struct.pack('<I',len(core))+core+timing,encoder.definitions
        return encoder.encode(value),encoder.definitions

class RecordDecoder(RecordEncoder):
    """Reusable decoder for both core packets and timing-bearing shadow envelopes."""
    def decode(self,packet):
        if packet[:4]==b'KAT\x01':
            if len(packet)<8: raise ValueError('truncated timing envelope')
            length=struct.unpack('<I',packet[4:8])[0]
            if length<14 or length>len(packet)-22: raise ValueError('invalid timing envelope length')
            core=super().decode(packet[8:8+length]); timing=super().decode(packet[8+length:])
            if not isinstance(core,dict) or set(core)!={'result','outcome'} or not isinstance(timing,dict) or set(timing)!={'timing'}:
                raise ValueError('invalid timing envelope fields')
            return {**core,**timing}
        return super().decode(packet)

def decode_record(packet,definitions):
    return RecordDecoder(definitions).decode(packet)

