import json, sqlite3, pathlib, time
settings=pathlib.Path.home()/'AppData/Local/KingdomAdventurersOptimizer/settings.json'
p=pathlib.Path(json.loads(settings.read_text())['library'])
d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
d.execute('PRAGMA query_only=ON'); d.execute('BEGIN')
out={'settings':str(settings),'source':str(p),'bytes':p.stat().st_size,'sidecars':{s:pathlib.Path(str(p)+s).stat().st_size if pathlib.Path(str(p)+s).exists() else 0 for s in ('-wal','-shm')},'schema':d.execute("select value from ea_meta where key='schema_version'").fetchone()[0],'tables':{}}
for name,sql in d.execute("select name,sql from sqlite_master where type='table'").fetchall():
    out['tables'][name]={'rows':d.execute('select count(*) from "'+name+'"').fetchone()[0],'columns':[r[1] for r in d.execute('pragma table_info("'+name+'")')],'sql':sql}
out['indexes']=[r[0] for r in d.execute("select name from sqlite_master where type='index'")]
path=pathlib.Path(__file__).parent/'studies/battle_binary_corpus/full-trial-source.json'
path.write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k not in ('tables','indexes')}))
print(json.dumps({t:x['rows'] for t,x in out['tables'].items()}))
