import sqlite3, time, os, sys, json
sys.path.insert(0, ".")
src = "A:/KingdomAdventurersOptimizer/strategiesv19student9test.sqlite"
dst = ".tmp-bench/live-copy.sqlite"
if os.path.exists(dst): os.remove(dst)
started = time.time()
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source:
    with sqlite3.connect(dst) as target:
        source.backup(target)
print(f"backup {os.path.getsize(dst)/1e6:.1f} MB in {time.time()-started:.1f}s")
