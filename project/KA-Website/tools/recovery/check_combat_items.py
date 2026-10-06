"""Battle recovery items: canonical Item.txt catalogue, native ExecuteItem classification,
GetRandomBonusValue differential and finite stock."""
import hashlib
import json
from pathlib import Path
from combat_initial_state import EVIDENCE,i32,trunc_div
from combat_consumables import (bonus_value,recovery_parameters,recover_item_parameter,use_battle_item,
    RECOVERY_PARAMETERS)
from combat_sandbox import run_scenario
from combat_scenario import load_scenario,ScenarioError
from combat_resolution import random_range

ASM=Path(EVIDENCE).parent/'20260912-combat'/'167b834.asm'
text=ASM.read_text(encoding='utf-8')
# Native switch citation: bonusCategory3 case 0x167bb84 -> inner table 0x772584; even bonusTypes take
# the all-residents call 0x167aea0, odd bonusTypes the single-resident call 0x167acac. The percent is
# ItemData.GetRandomBonusValue 0x162b344 (fixed return at 0x162b3ac when min>=max, else math
# Random.Rand 0x145ff98); recovery success spends one item through SubItem 0x1673ba4 at 0x167c814.
ITEM_ASM_NEEDLES=('167bb54: bl #0x162b344','167bb94: add x9, x9, #0x584','167c2bc: bl #0x167aea0',
                 '167c36c: bl #0x167acac','167baa0: cmp w8, #4','167bba8: mov w2, #0xa',
                 '167c2ac: mov w2, #0xb','167c2fc: mov w2, #0xa','167c824: bl #0x1673ba4')
# 0x162b344 loads bonusMinValue/bonusMaxValue from the item (+0x78/+0x7c), compares signed and, only
# when min<max, tail-branches into math Random.Rand; the min>=max arm returns min at 0x162b3ac.
BONUS_ASM_NEEDLES=('162b370: ldp w19, w20, [x20, #0x78]','162b374: cmp w19, w20','162b378: b.ge #0x162b3ac',
                   '162b394: mov w0, w19','162b398: mov w1, w20','162b3a8: b #0x145ff98',
                   '162b3ac: mov w0, w19','162b3b8: ret')
for needle in ITEM_ASM_NEEDLES:
    assert needle in text,needle
bonus_asm=(Path(EVIDENCE).parent/'20260912-combat'/'162b344.asm').read_text(encoding='utf-8')
for needle in BONUS_ASM_NEEDLES:
    assert needle in bonus_asm,needle
assert RECOVERY_PARAMETERS=={0:10,1:10,2:11,3:11,4:12,5:12}

# ---- Canonical recovery-item catalogue, checked against the original Item.txt table ----
ROOT=Path(EVIDENCE).parent.parent
CATALOGUE=ROOT/'KA-Website/artifacts/kingdom-adventures/src/game-data/native-recovery-items.json'
ITEM_TXT=Path(EVIDENCE).parent/'20260911-treasure/xls-original/English.lproj/Item.txt'
assert CATALOGUE.is_file(),CATALOGUE
assert ITEM_TXT.is_file(),ITEM_TXT
cat=json.loads(CATALOGUE.read_text(encoding='utf-8'))
assert hashlib.sha256(ITEM_TXT.read_bytes()).hexdigest()==cat['source']['sha256']
rows={}
for line in ITEM_TXT.read_text(encoding='utf-8').splitlines():
    field=line.split('\t')
    if field and field[0].isdigit():rows[int(field[0])]=field
assert cat['source']['rowCount']==len(rows)==173
assert {r['id'] for r in cat['items']}=={27,28,31}
def canonical_row(alias):
    for r in cat['items']:
        if alias in r['aliases'] or alias==r['name']:
            return {k:r[k] for k in ('bonusCategory','bonusType','bonusMinValue','bonusMaxValue')}
    raise KeyError(alias)
for r in cat['items']:
    field=rows[r['id']]
    assert field[1]==r['name'],(r['id'],field[1])
    assert (int(field[2]),int(field[3]))==(r['category'],r['type']),(r['id'],field[2:4])
    assert (int(field[18]),int(field[19]))==(r['bonusCategory'],r['bonusType']),(r['id'],field[18:20])
    assert (int(field[20]),int(field[21]))==(r['bonusMinValue'],r['bonusMaxValue']),(r['id'],field[20:22])
    assert cat['native']['recoveryParameterByBonusType'][str(r['bonusType'])]==r['parameter']
    assert cat['native']['scopeByBonusType'][str(r['bonusType'])]==r['scope']
    assert recovery_parameters(dict(bonusCategory=r['bonusCategory'],bonusType=r['bonusType']))==(r['parameter'],
        r['scope']=='all')
assert cat['sizes']['recoveryPotion']==['S','L']
assert not any(x[1]=='Recovery Potion (M)' for x in rows.values())

# ---- Primitive bonus draw: fixed when min>=max, otherwise one truncating math Rand draw ----
def never():raise AssertionError('fixed bonus must not draw')
assert bonus_value(canonical_row('Large Potion'),never)==50
assert bonus_value(canonical_row('Holy Herb'),never)==100
hp=dict(bonusCategory=3,bonusType=0,bonusMinValue=100,bonusMaxValue=200)
draws=[]
# native Rand(low,high): raw - trunc_div(raw,span)*span + low, span=high-low+1
assert bonus_value(hp,lambda:draws.append(1) or -7)==i32(-7-trunc_div(-7,101)*101+100)==93
assert len(draws)==1
raws=((0,50,51),(12345,100,200),(-2147483648,1,6),(2147483647,100,101),(-7,0,1))
for raw,low,high in raws:
    item=dict(bonusCategory=3,bonusType=1,bonusMinValue=low,bonusMaxValue=high)
    span=i32(high-low+1);r=i32(raw)
    assert bonus_value(item,lambda raw=raw:raw)==i32(r-trunc_div(r,span)*span+low),(raw,low,high)
assert recovery_parameters(dict(bonusCategory=3,bonusType=4))==(12,True)
assert recovery_parameters(dict(bonusCategory=3,bonusType=5))==(12,False)
for bad in (dict(bonusCategory=2,bonusType=0),dict(bonusCategory=3,bonusType=6),dict(bonusCategory=3)):
    try:recovery_parameters(bad)
    except ValueError:pass
    else:raise AssertionError(bad)

# ---- Native differential: run GetRandomBonusValue 0x162b344 and compare to bonus_value ----
# Static needle assertions above only pin the opcode shape; the RNG branch is novel behavior, so it is
# emulated here over bounded (min,max,raw) inputs with the math Random.Rand callee supplied.
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
for got in (0x2f5c538,):
    ptr,cls=0x10001000,0x10002000
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
uc.mem_write(0x316b6bd,b'\x01')
item_buf=0x10004000
rand_calls=[];pending={}
def external(machine,address,size,user):
    result=0
    if 0x162b344<=address<0x162b3bc:return
    if address in (0x12d21a0,0x12d22a4):pass
    elif address==0x145ff98:
        low,high=i32(reg('W0')),i32(reg('W1'));rand_calls.append((low,high))
        result=random_range(pending['raw'],low,high)
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def native_bonus(low,high,raw=0):
    w32(item_buf+0x78,i32(low));w32(item_buf+0x7c,i32(high))
    rand_calls.clear();pending['raw']=raw
    value=i32(run(0x162b344,x0=item_buf))
    return value,list(rand_calls)
native_bonus_cases=0
PAIRS=((50,50),(100,100),(-3,-3),(200,100),(0,1),(1,6),(100,200),(-5,5),(2147483646,2147483647))
NATIVE_RAWS=(0,1,-1,7,50,100,12345,2147483647,-2147483648,-7)
for low,high in PAIRS:
    row=dict(bonusCategory=3,bonusType=0,bonusMinValue=low,bonusMaxValue=high)
    if low>=high:
        value,calls_=native_bonus(low,high)
        assert calls_==[] and value==low and bonus_value(row,never)==low,(low,high,value,calls_)
        native_bonus_cases+=1
    else:
        for raw in NATIVE_RAWS:
            value,calls_=native_bonus(low,high,raw)
            expected=bonus_value(row,lambda raw=raw:raw)
            assert calls_==[(low,high)] and value==expected,(low,high,raw,value,expected,calls_)
            native_bonus_cases+=1
# Every canonical row has min==max, so native returns the fixed min and never reaches Random.Rand.
for r in cat['items']:
    value,calls_=native_bonus(r['bonusMinValue'],r['bonusMaxValue'])
    assert calls_==[] and value==r['bonusMinValue']==r['bonusMaxValue'],(r['id'],value,calls_)
    native_bonus_cases+=1

# ---- Recovery primitive boundaries (inherits the 248 native herb cases) ----
# check_combat_herbs.py emulates 0x167aea0/0x167acac/0x14f34ac/0x162b344 directly; its 248 recorded
# cases certify the same recovery semantics modelled by combat_consumables.recover_item_parameter.
herb=json.loads((Path(EVIDENCE)/'herb-checks.json').read_text(encoding='utf-8'))
counts=herb['nativeHerbChecks']
assert counts=={'itemDispatch':4,'singleRecovery':216,'battleTargetFilter':12,'joinedGroupRecovery':16}
inherited_recovery_cases=sum(counts.values())
assert inherited_recovery_cases==248
herb_src=Path(__file__).with_name('check_combat_herbs.py').read_text(encoding='utf-8')
for fn in ('0x167aea0','0x167acac','0x14f34ac','0x162b344'):
    assert fn in herb_src,fn
seen={}
def rate(i,p):return seen[i]['rate']
def maximum(i,p):return 99999
def human_ok(i):return seen[i]['human']
def add_value(i,p,amount):seen[i]['gained'].append((p,amount))
seen={'a':dict(rate=100,human=True,gained=[]),'b':dict(rate=0,human=False,gained=[]),'c':dict(rate=50,human=True,gained=[])}
assert recover_item_parameter(['a','b','c'],11,100,rate,maximum,human_ok,add_value) is True
assert seen['a']['gained']==[] and seen['b']['gained']==[] and seen['c']['gained']==[(11,99999)]
seen={i:dict(rate=100,human=True,gained=[]) for i in ('a','b')}
assert recover_item_parameter(['a','b'],11,100,rate,maximum,human_ok,add_value) is False
seen={'a':dict(rate=0,human=True,gained=[])}
assert recover_item_parameter(['a'],10,50,rate,maximum,human_ok,add_value) is True
assert seen['a']['gained']==[(10,49999)]

# ---- Stock gate, spend-on-success, and the caller-side state filter for the all-residents scope ----
stock=[1];spent=[]
def make_item(**kw):
    row=canonical_row('Large Potion');row.update(kw);return row
state=lambda i:{'a':3,'b':8,'c':3}[i]
seen={'a':dict(rate=0,human=True,gained=[]),'b':dict(rate=0,human=True,gained=[]),'c':dict(rate=100,human=True,gained=[])}
record=use_battle_item(make_item(),['a','b','c'],lambda:stock[0]>0,lambda:0,rate,maximum,human_ok,add_value,
                       lambda:spent.append(1),state=state)
assert record['scope']=='all' and record['targets']==['a','c'] and record['used'] and record['percent']==50
assert spent==[1] and seen['a']['gained']==[(10,49999)] and seen['b']['gained']==[]
seen['a']['gained'].clear();seen['c']['rate']=100;stock[0]=0
blocked=use_battle_item(make_item(),['a','c'],lambda:stock[0]>0,lambda:spent.append('draw') or 0,rate,maximum,human_ok,add_value,
                        lambda:spent.append(1),state=state)
assert blocked['used'] is False and blocked['blocked']=='no stock' and spent==[1]
stock[0]=1
seen['a']['rate']=100
failed=use_battle_item(make_item(),['a','c'],lambda:stock[0]>0,lambda:0,rate,maximum,human_ok,add_value,lambda:spent.append(1),state=state)
assert failed['used'] is False and spent==[1] and stock[0]==1
try:use_battle_item(make_item(bonusType=1),['a'],lambda:True,lambda:0,rate,maximum,human_ok,add_value,lambda:None,state=state)
except ValueError:pass
else:raise AssertionError('single-resident scope without a resident must fail closed')

# ---- Engine-level: the canonical id27 row drives the shared tick loop and trace ----
data=json.loads((Path(EVIDENCE)/'sandbox-synthetic-scenario.json').read_text(encoding='utf-8'))
data['inputs']=[dict(tick=20,type='item',phase='before_fighters',item='large potion',target='all')]
data['items']={'large potion':canonical_row('Large Potion')}
data['itemStock']={'large potion':2}
report=run_scenario(data,True)
uses=report['itemUses']
assert len(uses)==1 and uses[0]['used'] and uses[0]['remaining']==1 and uses[0]['percent']==50,uses
assert uses[0]['parameter']==10 and uses[0]['scope']=='all' and uses[0]['targets']
assert report['itemRemaining']=={'large potion':1} and report['holyHerbRemaining']==data['holyHerbStock']
assert any(e['kind']=='battle_item' and e['tick']==20 and e['item']=='large potion' for e in report['trace'])
# Canonical 50/50 takes the native fixed return, so its one dispatched use draws no engine RNG value.
assert sum(1 for e in report['trace'] if e['kind']=='rng' and e.get('purpose')=='item_bonus_value')==0
assert report==run_scenario(data,True)
assert report['trace']==run_scenario(data,True)['trace']
assert report['result']['mathDraws']==run_scenario(data,True)['result']['mathDraws']
# A bounded min<max row exercises the novel RNG path through the engine: exactly one bound draw.
rng_data=json.loads(json.dumps(data))
rng_data['items']={'large potion':dict(bonusCategory=3,bonusType=0,bonusMinValue=100,bonusMaxValue=200)}
rng_report=run_scenario(rng_data,True)
assert 100<=rng_report['itemUses'][0]['percent']<=200
assert sum(1 for e in rng_report['trace'] if e['kind']=='rng' and e.get('purpose')=='item_bonus_value')==1
# A full-HP eligible group takes no draw for the fixed row and spends nothing.
full=dict(data);full['inputs']=[dict(tick=1,type='item',phase='before_fighters',item='large potion',target='all')]
full_report=run_scenario(full,True)
assert full_report['itemUses'][0]['used'] is False and full_report['itemRemaining']=={'large potion':2}
for bad in (dict(items={'x':dict(bonusCategory=1,bonusType=0,bonusMinValue=1,bonusMaxValue=2)}),
            dict(inputs=[dict(tick=1,type='item',phase='before_fighters',item='x',target='all')]),
            dict(inputs=[dict(tick=1,type='item',phase='before_fighters',item='large potion',target='unit')]),
            dict(items={'large potion':dict(bonusCategory=3,bonusType=7,bonusMinValue=1,bonusMaxValue=2)}),
            dict(itemStock={'large potion':-1})):
    probe=json.loads(json.dumps(data));probe.update(bad)
    try:load_scenario(probe)
    except ScenarioError:pass
    else:raise AssertionError(bad)
assert load_scenario(data)==load_scenario(load_scenario(data))
out=dict(nativeSlices=['RE-evidence/20260912-combat/167b834.asm','162b344.asm'],
    canonicalSource='RE-evidence/20260911-treasure/xls-original/English.lproj/Item.txt',
    cases=dict(switchMappings=6,bonusValueRaws=len(raws)+1,recoveryBoundaries=3,stockGate=4,endToEndTicks=2,
               itemTxtRows=len(cat['items']),bonusValueNativeCases=native_bonus_cases,
               inheritedRecoveryNativeCases=inherited_recovery_cases),
    findings=['bonusCategory3/bonusType even -> all supplied residents, odd -> the single resident arg; parameter is 10+type//2',
              'canonical Item.txt rows: id27 Recovery Potion (L) 3/0/50/50 all-resident HP, id28 (S) 3/1/50/50 single-resident HP, id31 Holy Herb 3/2/100/100 all-resident MP; only S and L sizes exist',
              'percent comes from GetRandomBonusValue: fixed min when min>=max, one math Random.Rand draw otherwise (emulated natively over bounded inputs); taken before the recovery branch',
              'an item is spent exactly once, only on aggregate recovery success; zero stock blocks the whole dispatch including the draw'],
    inherited='248 native recovery cases (check_combat_herbs.py / herb-checks.json) certify the shared recovery primitive')
(EVIDENCE/'item-checks.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out))
