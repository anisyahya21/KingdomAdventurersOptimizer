import json, statistics, sys, time
sys.path.insert(0, ".")
import strategy_optimizer_desktop as desktop

LIB = ".tmp-bench/live-copy.sqlite"
db = desktop._readonly_db(LIB)
counts = [tuple(r) for r in db.execute(
    "SELECT m.encounter, m.defeat, COUNT(*) FROM candidate_meta m GROUP BY m.encounter, m.defeat ORDER BY 3 DESC")]
encounter, defeat, size = counts[0]
print("biggest fight:", encounter, defeat, size, "builds")

candidates = desktop._encounter_candidates(db, encounter, defeat)
def timed(fn, n=7):
    times = []
    for _ in range(n):
        s = time.perf_counter(); fn(); times.append(time.perf_counter()-s)
    return dict(medianMs=round(statistics.median(times)*1000, 2),
                minMs=round(min(times)*1000, 2), maxMs=round(max(times)*1000, 2))

def listing_rest():
    out = []
    for row in candidates:
        stored = desktop._stored_runs(db, row["id"])
        out.append(desktop._aggregate_measure(desktop._summarize_attempts(stored),
                                              desktop._lifetime_counters(db, row["id"]), len(stored)))
    return out

names = timed(lambda: desktop._structured_names(db, candidates))
rest = timed(listing_rest)
d = desktop._structured_names(db, candidates)
derived = sum(1 for v in d.values() if v.get("derived"))
print(json.dumps(dict(builds=len(candidates), derivedNames=derived,
                      structuredNames=names, listingWithoutNames=rest,
                      namingSharePct=round(names["medianMs"]/max(rest["medianMs"], .001)*100, 1),
                      sample=[v["name"] for v in list(d.values())[:6]]), indent=1, ensure_ascii=False))
db.close()
