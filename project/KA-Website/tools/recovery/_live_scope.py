import sqlite3, time, json
SRC = "A:/KingdomAdventurersOptimizer/strategiesv19student9test.sqlite"
def sample():
    db = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    try:
        ev = dict(db.execute("SELECT m.encounter, COUNT(*) FROM evidence e JOIN candidate_meta m ON m.id=e.candidate GROUP BY m.encounter").fetchall())
        runs = dict(db.execute("SELECT m.encounter, COUNT(*) FROM run r JOIN candidate_meta m ON m.id=r.candidate GROUP BY m.encounter").fetchall())
        cands = dict(db.execute("SELECT encounter, COUNT(*) FROM candidate_meta GROUP BY encounter").fetchall())
        total = db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        runs_total = db.execute("SELECT COUNT(*) FROM run").fetchone()[0]
    finally:
        db.close()
    return ev, runs, cands, total, runs_total
a = sample(); time.sleep(20); b = sample()
enc = sorted(set(a[0]) | set(b[0]) | set(a[2]) | set(b[2]))
print(f"{'enc':>4} {'builds':>7} {'evidence':>9} {'+20s':>6} {'runs':>8} {'+20s':>6}")
for e in enc:
    d_ev = b[0].get(e,0)-a[0].get(e,0); d_run = b[1].get(e,0)-a[1].get(e,0)
    print(f"{e:>4} {b[2].get(e,0):>7} {b[0].get(e,0):>9} {d_ev:>6} {b[1].get(e,0):>8} {d_run:>6}")
print("evidence total", b[3], "runs total", b[4])
print("sum of deltas: evidence", sum(b[0].get(e,0)-a[0].get(e,0) for e in enc), "runs", sum(b[1].get(e,0)-a[1].get(e,0) for e in enc))
