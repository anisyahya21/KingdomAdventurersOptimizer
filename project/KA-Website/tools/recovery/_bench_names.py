import json, os, sqlite3, statistics, sys, time
sys.path.insert(0, ".")
import strategy_naming as naming
import strategy_optimizer_desktop as desktop

LIB = ".tmp-bench/live-copy.sqlite"
db = desktop._readonly_db(LIB)

# Which encounter/difficulty the live library actually holds most builds for.
counts = [tuple(r) for r in db.execute(
    "SELECT m.encounter, m.defeat, COUNT(*) FROM candidate_meta m GROUP BY m.encounter, m.defeat ORDER BY 3 DESC")]
encounter, defeat = counts[0][0], counts[0][1]
print("biggest fight:", encounter, defeat, counts[0][2], "builds; all:", counts[:6])

candidates = desktop._encounter_candidates(db, encounter, defeat)
def timed(fn, n=7):
    times = []
    for _ in range(n):
        started = time.perf_counter(); fn(); times.append(time.perf_counter()-started)
    return dict(median=round(statistics.median(times)*1000, 2), min=round(min(times)*1000, 2),
                max=round(max(times)*1000, 2))
names = timed(lambda: desktop._structured_names(db, candidates))
listing = timed(lambda: desktop.encounter_strategies(encounter, defeat))

# How much of the list is actually the added naming work, and how many names are derived.
payload = desktop.encounter_strategies(encounter, defeat)
rows = payload["strategies"]
derived = [row for row in rows if row.get("nameDerived")]
print(json.dumps(dict(encounter=encounter, defeat=defeat, rows=len(rows),
                      derivedNames=len(derived),
                      structuredNames=timed(lambda: desktop._structured_names(db, candidates)),
                      namingMs=names, listingMs=listing,
                      shareOfListing=round(names["median"]/listing["median"]*100, 1)), indent=1))
for row in rows[:5]:
    print("  ", row["displayName"] or row["label"])
db.close()
