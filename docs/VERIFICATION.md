# Verification

## Mechanism-objective source correction (2026-10-09)

The newer correction's scope and saved-fixture verification are described in
[MECHANISM_OBJECTIVES.md](MECHANISM_OBJECTIVES.md). The results below describe
the original package; they do not claim the historical desktop pins contain
the new learner. No new full GUI or process-kill acceptance suite is claimed.

## Original package verification

Executed in the copied package, using the existing Python 3.12 environment and installed pywebview 6.2.1; this is not a clean-machine dependency installation.

* Python: canonical fixed scenario admitted; a real native battle completed with verdict 1, 424 ticks, deterministic digest `51e884a485594c298aeac65aafab1d8504b32aa8c2e23b21cf0e0d7dd36cc9ad`. The result explicitly refuses an unproven inventory certificate.
* Latest Rust fixed-policy: 8 completed trials, exit 0.
* Latest Go fixed-policy: 2 durable diagnostic trials, exit 0, no errors.
* Latest C++ fixed-policy: 4 saved trials, exit 0, zero invalid/execution errors.
* Current desktop Python imports/CLI help and all three pinned asset hashes validated.

* isolated desktop CLI/import: PASS, exit 0.
* desktop pinned assets hash validation: PASS, exit 0.
* all four engine fresh library initialization: PASS, exit 0.

No new full visual GUI suite, long optimization, historical database reproduction, clean dependency install or native rebuild was performed. Existing algorithm failures were not repaired. The local test runtime is ignored and excluded from the upload.

## Optimizer-only cleanup

All local TypeScript source imports, JSON dependencies and Web Worker new-URL imports resolve after pruning. Standalone npm dependency installation passed. Initial build exposed a missing Worker dependency, which was restored; corrected build reached Vite transformation but exceeded the bounded 120-second timeout, so a complete fresh UI build remains unverified. Shipped desktop-dist bytes remain unchanged.

Fresh Python desktop imports, all native pinned asset hashes and search-contract stat bounds passed. Native binaries, kernels and fixed-smoke fixture payloads are unchanged; prior valid four-engine smoke results were reused. New dependency node_modules and build output stay ignored.


Final cleanup check: `npm run typecheck` passed (exit 0) in the standalone copied UI. Full frontend rebuild remains unverified after the bounded build timeout; shipped compiled assets were retained.
