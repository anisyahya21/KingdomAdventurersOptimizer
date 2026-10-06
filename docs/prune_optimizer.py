"""Deterministic optimizer-only dependency allowlist; operate only on staged handoff."""
import ast,json,re,os,hashlib,shutil
from pathlib import Path
B=Path(__file__).resolve().parent.parent; O=B/'developer-handoff'; P=O/'project'; J=B/'developer-handoff-job'
A=P/'KA-Website/artifacts/kingdom-adventures'; SRC=A/'src'; R=P/'KA-Website/tools/recovery'
apply='--apply' in __import__('sys').argv
# Add the five actual source dependencies previously missing from the broad copy.
for rel in ['KA-Website/website_icons/manifest.json','KA-Website/website_icons/facilities_confirmed/manifest.json','KA-Website/website_icons/facilities_assembled/manifest.json','KA-Website/website_icons/skills/skill_icon_map.json','KA-Website/artifacts/api-server/data/ka_shared.json']:
 source=B/'project'/rel;target=P/rel
 if source.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
keep=set();why={};missing=[];external=set();graph={}
def retain(p,reason):
 p=Path(p).resolve()
 if not p.is_relative_to(O.resolve()):raise ValueError(p)
 if p.is_file():
  keep.add(p);why.setdefault(p.relative_to(O).as_posix(),[]).append(reason)
def resolve(spec,origin):
 if spec.startswith('@/'): base=SRC/spec[2:]
 elif spec.startswith('@assets/'):base=A.parent.parent/'attached_assets'/spec[8:]
 elif spec.startswith('.'):base=origin.parent/spec
 else:
  external.add(spec.split('/')[0] if not spec.startswith('@') else '/'.join(spec.split('/')[:2]));return None
 for p in [base]+[Path(str(base)+e) for e in ['.ts','.tsx','.js','.jsx','.json','.css','.svg']]+[base/('index'+e) for e in ['.ts','.tsx','.js','.jsx']]:
  if not p.is_file() and p.resolve().is_relative_to(P.resolve()):
   original=B/'project'/p.resolve().relative_to(P.resolve())
   if original.is_file():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,p)
  if p.is_file():return p.resolve()
 missing.append({'source':origin.relative_to(O).as_posix(),'specifier':spec});return None
# Covers import declarations, export-from, literal dynamic imports and CSS @import.
imports=re.compile(r'''(?:\b(?:import|export)\s+(?:[^;]*?\s+from\s*)?|\bimport\s*\(\s*|@(?:import|plugin)\s*)["']([^"']+)["']''')
todo=[SRC/'desktop.tsx',SRC/'synthetic-legal-desktop.tsx'];visited=set()
while todo:
 p=todo.pop().resolve()
 if p in visited:continue
 visited.add(p);retain(p,'desktop import closure')
 if p.suffix not in {'.ts','.tsx','.js','.jsx','.css'}:continue
 text=p.read_text(encoding='utf-8');deps=[]
 for spec in imports.findall(text)+re.findall(r'''new\s+URL\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url''',text):
  q=resolve(spec,p)
  if q:deps.append(q.relative_to(O).as_posix());todo.append(q)
 for patt in re.findall(r'''import\.meta\.glob(?:Eager)?\(\s*["']([^"']+)["']''',text):
  base=SRC if patt.startswith('@/') else p.parent
  for q in base.glob(patt[2:] if patt.startswith('@/') else patt):todo.append(q)
 graph[p.relative_to(O).as_posix()]=deps
# Local Python import closure, including lazy function imports. Retain native integration checks.
mods={p.stem:p for p in R.glob('*.py')}
roots=['strategy_language_desktop','fixed_formation','strategy_optimizer_adapter','strategy_optimizer_native']
roots += [n for n in mods if n.startswith(('check_strategy_','check_native_','check_combat_','check_optimizer_','check_encounter_','check_simulator_compat','check_fixed_formation','bench_optimizer_','benchmark_optimizer_','bench_community_','benchmark_battle_','benchmark_encounter_'))]
roots += ['strategy_optimizer_planner_desktop','synthetic_legal_desktop']
ptodo=[mods[n] for n in roots if n in mods];pseen=set()
while ptodo:
 p=ptodo.pop().resolve()
 if p in pseen:continue
 pseen.add(p);retain(p,'optimizer Python import closure or relevant optimizer check')
 try:tree=ast.parse(p.read_text(encoding='utf-8-sig'))
 except SyntaxError as e:missing.append({'pythonSyntax':str(p),'error':str(e)});continue
 names=[]
 for node in ast.walk(tree):
  if isinstance(node,ast.Import):names += [x.name.split('.')[0] for x in node.names]
  elif isinstance(node,ast.ImportFrom) and node.module:names.append(node.module.split('.')[0])
  elif isinstance(node,ast.Constant) and isinstance(node.value,str):
   for name in re.findall(r'\b([A-Za-z_]\w*)\.py\b',node.value):names.append(name)
 for n in names:
  if n in mods:ptodo.append(mods[n])
# Python reads game JSON by Path, including dynamically assembled lookups. Literal names are
# matched against staged game-data filenames; combat/replay animation tables are explicit.
gamedata=SRC/'game-data';gamefiles={p.name:p for p in gamedata.rglob('*') if p.is_file()}
texts='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in pseen)
for name,p in gamefiles.items():
 if name in texts:retain(p,'Python runtime literal game-data reference')
for name in ['battle-animation.json','native-job-identity.json']:
 if name in gamefiles:retain(gamefiles[name],'desktop replay runtime table')
# Static assets: preserve referenced files; dynamically selected battle sprite families require
# the complete family because unit/animation IDs are runtime inputs.
public=A/'public';publicfiles={p.relative_to(public).as_posix():p for p in public.rglob('*') if p.is_file()}
assettexts='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in keep if p.suffix in {'.ts','.tsx','.js','.jsx','.json','.css','.py'})
assetdirs=set()
for match in re.findall(r'''["'`](/?(?:battle-assets|website_icons|character_sprites|monster-sprites|treasure-icons|assets|images|icons)/[^"'`\s]*)''',assettexts):
 match=match.lstrip('/')
 if '${' in match:
  prefix=match.split('${')[0].rstrip('/')
  # A partial filename prefix belongs to its parent directory.
  prefix=prefix if (public/prefix).is_dir() else str(Path(prefix).parent).replace('\\','/')
  if prefix not in {'','.'} and (public/prefix).is_dir():assetdirs.add(prefix)
 elif match in publicfiles:retain(publicfiles[match],'literal optimizer asset reference')
for rel,p in publicfiles.items():
 if rel in assettexts or '/'+rel in assettexts:retain(p,'optimizer source/data asset reference')
for directory in assetdirs:
 for p in (public/directory).rglob('*'):
  if p.is_file():retain(p,'runtime-generated asset family '+directory)
for name in ['favicon.svg']:
 if name in publicfiles:retain(publicfiles[name],'desktop HTML favicon')
# JSON visual manifests can reference leaf filenames under battle-assets; keep complete required
# battle-assets family as runtime-generated IDs are deliberately not statically enumerable.
if 'battle-assets/' in assettexts:
 for p in (public/'battle-assets').rglob('*'):
  if p.is_file():retain(p,'battle/replay runtime sprite family')
if 'browser-combat.zip' in assettexts:
 source=B/'project/KA-Website/artifacts/kingdom-adventures/public/browser-combat.zip';target=public/'browser-combat.zip'
 if source.is_file():shutil.copy2(source,target);retain(target,'browser battle worker optional runtime bundle')
# Icon helpers contain dynamic runtime filenames. Include only referenced top-level families,
# not unrelated website documentation or previews. A whole family is conservative where shared
# helpers serve several battle equipment/skill/item representations.
iconroot=P/'KA-Website/website_icons';originalicons=B/'project/KA-Website/website_icons'
iconfamilies=set(re.findall(r'/website_icons/([A-Za-z0-9_-]+)/',assettexts))
if '/website_icons/${' in assettexts:iconfamilies.add('items')
for family in iconfamilies:
 src=originalicons/family;dst=iconroot/family
 if src.is_dir():
  for source in src.rglob('*'):
   if source.is_file() and source.suffix in {'.png','.webp','.svg','.json'}:
    target=dst/source.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():shutil.copy2(source,target)
    retain(target,'dynamic battle/UI icon family '+family)
# Native engines, kernel sources/DLLs, packaged combat runtime and current policy/configs unchanged.
protected=[R/'native',R/'combat_runtime_data',P/'coordination']
for root in protected:
 for p in root.rglob('*'):
  if p.is_file():retain(p,'engine/kernel/fixture/packaged runtime protected')
for p in R.glob('*.json'):
 if p.name in texts:retain(p,'Python runtime literal JSON reference')
for p in R.glob('*.md'):retain(p,'optimizer mechanics documentation')
# Root entry points + configuration + required current compiled UI stay.
for p in A.glob('*'):
 if p.is_file() and p.name in ['package.json','package-lock.json','tsconfig.json','vite.desktop.config.ts','desktop.html','synthetic-legal.desktop.html']:retain(p,'optimizer build configuration')
for p in (A/'desktop-dist').rglob('*'):
 if p.is_file():retain(p,'current compiled desktop dependency closure')
for p in (P/'tools').glob('*'):
 if p.is_file():retain(p,'fixed-policy tooling')
retain(P/'KA-Website/tsconfig.base.json','frontend TypeScript base')
retain(P/'RE-evidence/20260922-search-contract/stat-bounds.json','Python search_contract bounds and Rust include_str/include_bytes compile input')
# Data exports unrelated to runtime are excluded. Literal full project-relative paths get retained.
for p in (P/'KA-Website/data').rglob('*'):
 if p.is_file() and (p.name in texts or p.relative_to(P).as_posix() in texts):retain(p,'Python literal input data reference')
removed=[]
for p in P.rglob('*'):
 if 'node_modules' in p.parts or '__pycache__' in p.parts:continue
 if p.is_file() and p.resolve() not in keep:
  removed.append({'path':p.relative_to(O).as_posix(),'bytes':p.stat().st_size,'reason':'outside desktop/Python dependency closure and protected engine runtime'})
summary={'apply':apply,'kept':len(keep),'removedCount':len(removed),'removedBytes':sum(x['bytes'] for x in removed),'missingImports':missing,'externalPackages':sorted(external),'dynamicAssetFamilies':sorted(assetdirs),'iconFamiliesConservativelyKept':sorted(iconfamilies),'removed':removed,'dependencyReasons':why,'typescriptGraph':graph,'pythonRoots':roots}
(J/'cleanup-dependency-report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
if apply:
 for item in removed:
  p=(O/item['path']).resolve()
  if not p.is_relative_to(O.resolve()):raise ValueError(p)
  p.unlink()
print(json.dumps({k:v for k,v in summary.items() if k not in ['removed','dependencyReasons','typescriptGraph','pythonRoots']},indent=2))
