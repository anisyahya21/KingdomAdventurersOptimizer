# Opt-in fixed baseline comparison

This adapter uses the same Go raw preparation, direct shared Rust `ka_kernel` DLL hydration, simulation, checksum checks and reward accounting as ordinary search. It does not mutate candidates or select winners. Previous R10 builds/results remain frozen separately. Only Chat 1 executes the adapter or tests it.

```
go-optimizer.exe --mode baseline-bank --input workload.json --seed-bank seed-bank.json --output fresh-output --batch 64 --executors 5 --sync-batch 1
```

`--executors` supports 5, 10 and 12. Workload schema remains `ka-go-workload-1`, containing complete raw candidates and tables, pinned kernel path/hash and mechanics identity. A one-encounter workload is supported. Seed-bank JSON is an explicit array such as `[[100,200],[101,200]]`; pairs must be distinct and contain exactly two nonnegative signed31-bit integers. Duplicate baseline genotype inputs are rejected. Only the two raw seed fields are replaced; every candidate/pair is freshly prepared through the normal preparer.

The stream is candidate input order, then seed-bank order within each candidate. Its total is candidate count × bank size. `--budget` and `--seed` affect ordinary adaptive search only. Trial IDs bind the candidate genotype and explicit seed pair, independently of workers or batch size. The bank hash, workload hash, scheduling policy, build, workers, batch size and sync batch are recorded. Resume requires these settings to match; it restores completed trials without rerunning them. Raw pending trials are checked against the frozen stream.

`--sync-batch 1` calls `os.File.Sync` after every complete journal record. Larger values defer journal sync until that many records are written; checkpoints and close flush any remaining pending records. Durably saved counts advance only after successful OS sync. Checkpoints cannot advance beyond the synced journal. Output retains full raw intent, prepared snapshots, seeds, native reports and bytes, checksums, reward basis, execution provenance and fixed-baseline lineage. Diagnostic outcomes remain unverified and are never automatically imported into the production library.

`summary.json` contains actual counters and measured nanosecond timings. Caller wall boundaries cover input parsing, catalog initialization, admission/scheduler construction, store replay, checkpoint restore, engine startup, preparation/binding, native batch, and persistence. Native worker stages measure hydration, pre-run state setup, the native call, report retrieval/decoding and arena freeing. Worker busy sums overlap across workers; they are not additive serial wall time. Store serialization, journal writes and sync measurements are children of persistence. Parent and child timings must not be summed together or presented as isolated speed gains.

Internal timing does not cover process startup before Go entry, the final summary write or shutdown. External process start-to-exit wall remains authoritative. Timings describe this invocation; durable totals describe the complete output scope, including resumed records. This adapter has no performance ranking or strategy-quality claim; its new paths require coordinator smoke, save/resume and canonical checks before measurement.
