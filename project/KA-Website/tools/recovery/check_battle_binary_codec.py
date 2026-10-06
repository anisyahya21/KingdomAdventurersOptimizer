"""Focused edge and corruption checks for the frozen binary registry."""
import copy
import json
from pathlib import Path
import strategy_battle_binary as binary

def run():
    root=Path(__file__).resolve().parent
    original=json.loads((root/'studies/compact_battle_prototype12/one-real-battle.json').read_text())['decoded']
    cases=[('real-rich-battle',original)]
    for name,edit in (
        ('integer-overflow',lambda v:v['result'].update(ticks=2**80)),
        ('negative-zero',lambda v:v['result'].update(healthFraction=-0.0)),
        ('nullable-award-zero',lambda v:v['result']['rewardOutcome'].update(awardedChests=0)),
        ('unknown-enum',lambda v:v['outcome'].update(status='new-state-unknown-to-schema')),
        ('unknown-string',lambda v:v['outcome'].update(reason='new unicode explanation \u03bb')),
        ('nonmatching-reference',lambda v:v['outcome'].update(ticks=77)),
        ('literal-digest',lambda v:v['result'].update(digest='0'*64)),
        ('uint32-ordered-seeds',lambda v:v['result'].update(seeds=[2**32-1,0])),
        ('unknown-fields',lambda v:v['result'].update(futurePayload={'items':[None,False,0,0.0]})),
    ):
        value=copy.deepcopy(original); edit(value); cases.append((name,value))
    cases.append(('pending',{'result':None,'outcome':None}))
    checks=[]
    for name,value in cases:
        packet,definitions=binary.encode_record(value)
        binary.exact(value,binary.decode_record(packet,json.loads(binary.dumps(definitions))))
        checks.append(name)
    timed={**original,'timing':{'completedAt':1.5,'elapsed':-0.0}}
    packet,definitions=binary.encode_record(timed); binary.exact(timed,binary.decode_record(packet,definitions))
    assert packet[:4]==b'KAT\x01' and packet[20:22]!=b'\xff\xff'
    checks.append('timing-envelope-keeps-packed-core')
    packet,definitions=binary.encode_record(original)
    corrupt=bytearray(packet); corrupt[-1]^=1
    for name,raw,defs in [('checksum-corruption',bytes(corrupt),definitions),
                          ('truncated-packet',packet[:10],definitions),
                          ('wrong-definitions',packet,{**definitions,'dictionary':definitions['dictionary']+['changed']})]:
        try: binary.decode_record(raw,defs)
        except ValueError: checks.append(name)
        else: raise AssertionError(name+' accepted')
    report=dict(checksPassed=checks,passed=True)
    (root/'studies/battle_binary_corpus/codec-checks.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

if __name__=='__main__': run()
