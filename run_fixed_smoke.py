"""Bounded fixed-policy reproducer; existing outputs must not be reused."""
import json,sys,subprocess,os
from pathlib import Path
root=Path(__file__).resolve().parent; project=root/'project'
engine=sys.argv[1] if len(sys.argv)>1 else 'python'
output=root/'runtime/fixed-smoke'/engine
if output.exists(): raise SystemExit('Output already exists; move it before rerunning.')
output.mkdir(parents=True)
base=project/'coordination/fixed-formation-20261006'
def absolute(v):
 if isinstance(v,str) and v.startswith(('coordination/','KA-Website/')): return str(project/v)
 if isinstance(v,list): return [absolute(x) for x in v]
 if isinstance(v,dict): return {k:absolute(x) for k,x in v.items()}
 return v
if engine=='python':
 sys.path.insert(0,str(project/'KA-Website/tools/recovery'))
 import fixed_formation, strategy_optimizer_native
 scenario=fixed_formation.canonical_scenario(0,math_seed=100001,lib_seed=100001)
 accepted,reasons=fixed_formation.admits(scenario)
 if not accepted: raise RuntimeError(reasons)
 from strategy_optimizer_adapter import simulate
 result=simulate(scenario,(100001,100001),backend='native',telemetry=True)
 print(json.dumps(result,default=str)[:3000])
 # Policy reproduction is independent of the long-running scheduler.
 print(json.dumps({'admitted':accepted,'identity':fixed_formation.search_identity(scenario),'nativeAdapterImported':True,'evaluationNotRun':False}))
else:
 if engine=='go':
  command=[str(base/'go-optimizer-fixed.exe'),'--mode','search','--input',str(base/'workloads/go-fixed-all20.json'),'--output',str(output),'--executors','1','--batch','1','--policy','fixed-formation','--seed','20261006','--budget','2']
  workload=json.loads((base/'workloads/go-fixed-all20.json').read_text())
  workload=absolute(workload)
  inputfile=output/'input.json'; inputfile.write_text(json.dumps(workload))
  command[command.index('--input')+1]=str(inputfile)
 else:
  config=absolute(json.loads((base/f'launch/{engine}/config.json').read_text()))
  config['executors']=1; config['generations']=1
  config['output' if engine=='rust' else 'outputDir']=str(output/'results')
  cfg=output/'config.json';cfg.write_text(json.dumps(config))
  exe=project/('coordination/native-preparation/rust/standalone-optimizer/target/release/ka-rust-standalone-optimizer.exe' if engine=='rust' else 'coordination/native-preparation/cpp/standalone-optimizer/optimizer.exe')
  command=[str(exe),str(cfg)]
 subprocess.run(command,cwd=project,check=True,timeout=120)
