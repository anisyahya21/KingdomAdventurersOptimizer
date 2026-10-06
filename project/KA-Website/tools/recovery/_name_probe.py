import sqlite3, json, sys
sys.path.insert(0, ".")
import strategy_naming as naming
db = sqlite3.connect("file:A:/KingdomAdventurersOptimizer/strategiesv19student9test.sqlite?mode=ro", uri=True)
db.row_factory = sqlite3.Row
lin = {}
for r in db.execute("SELECT * FROM lineage"):
    lin[r["candidate"]] = dict(r)
scen = {}
for cid, blob in db.execute("SELECT id, scenario FROM candidate"):
    scen[cid] = json.loads(blob)
enc = {}
for cid, e in db.execute("SELECT id, encounter FROM candidate_meta"):
    enc[cid] = e
interesting = [c for c in lin if lin[c]["parent"] and lin[c]["source"] in ("average","community","mechanism","rebel")]
print("total named candidates:", len(lin), "with parent:", sum(1 for c in lin if lin[c]["parent"]))
import collections
shown = 0
for cid in interesting:
    if enc.get(cid) not in (19, 18, 4, 15):
        continue
    n = naming.name_for(cid, lin, scen, fallback=None)
    if n["derived"]:
        print(f'{enc.get(cid):>3} | {n["name"]}')
        shown += 1
    if shown >= 22:
        break
print("--- fallbacks / underivable ---")
for cid in interesting:
    n = naming.name_for(cid, lin, scen)
    if not n["derived"]:
        print("  raw:", repr(n["name"])[:90], "| detail:", n["detail"][:80])
        shown += 1
    if shown >= 34:
        break
