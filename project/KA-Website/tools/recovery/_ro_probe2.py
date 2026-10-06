import sqlite3
db = sqlite3.connect("file:A:/KingdomAdventurersOptimizer/strategiesv19student9test.sqlite?mode=ro", uri=True)
def q(sql, args=()):
    return [tuple(r) for r in db.execute(sql, args)]
for op in ('set-stat','track:atk','track:spd','ladder','rebel','set-weapon','replace-skill','add-unit','set-formation-skill','fine-tune','move-skill'):
    rows = q("SELECT candidate,parent,depth,operation,target,change,source,proposal FROM lineage WHERE operation=? LIMIT 4", (op,))
    print("OP", op)
    for r in rows:
        print("   ", r)
