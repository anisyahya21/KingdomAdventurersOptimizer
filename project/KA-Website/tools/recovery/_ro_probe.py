import sqlite3, json
db = sqlite3.connect("file:A:/KingdomAdventurersOptimizer/strategiesv19student9test.sqlite?mode=ro", uri=True)
def q(sql, args=()):
    return [tuple(r) for r in db.execute(sql, args)]
print("candidate.source:", q("SELECT source, COUNT(*) FROM candidate GROUP BY source ORDER BY 2 DESC"))
print("lineage.source:", q("SELECT source, COUNT(*) FROM lineage GROUP BY source ORDER BY 2 DESC")[:25])
print("lineage.operation:", q("SELECT operation, COUNT(*) FROM lineage GROUP BY operation ORDER BY 2 DESC")[:25])
print("encounters:", q("SELECT encounter, COUNT(*) FROM candidate_meta GROUP BY encounter ORDER BY 2 DESC"))
print("n keys:", q("SELECT COUNT(*) FROM meta")[0])
keys = [r[0] for r in q("SELECT key FROM meta")]
import re
print("non-aggregate keys:", [k for k in keys if not k.startswith('aggregate:')][:60])
print("sample lineage rows:")
for r in q("SELECT candidate,parent,depth,operation,target,change,source,proposal FROM lineage WHERE change NOT IN ('','supplied build') LIMIT 8"):
    print("   ", r)
print("depth hist:", q("SELECT depth, COUNT(*) FROM lineage GROUP BY depth ORDER BY depth")[:12])
