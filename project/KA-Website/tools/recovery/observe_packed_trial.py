"""Local completion signal and sparse milestones; no AI/API calls or job control."""
import json,pathlib,time,sys
p=pathlib.Path(sys.argv[1]); seen=None; last=0
while True:
    try: r=json.loads(p.read_text())
    except (FileNotFoundError,json.JSONDecodeError): time.sleep(1); continue
    key=(r.get('stage'),int((r.get('percent') or 0)//20),r.get('state'))
    if key!=seen and time.monotonic()-last>=15 or r.get('state') in ('complete','failed','interrupted'):
        print(json.dumps({k:r.get(k) for k in ('state','stage','completed','total','percent','elapsedSeconds','processingRate','etaSeconds','error')}),flush=True)
        seen=key;last=time.monotonic()
    if r.get('state') in ('failed','interrupted'): sys.exit(1)
    # Independent verifier briefly says complete; the final marker plus final filename must exist.
    if r.get('state')=='complete' and r.get('stage')=='verified packed trial complete':
        report=p.parent/'report.json'
        time.sleep(1)
        print('FINAL_REPORT '+str(report),flush=True)
        break
    time.sleep(1)
