# Rust standalone optimizer

Independent Rust candidate admission, numeric preparation, formation, search, resident execution coordination and durable result saving. The existing canonical native Rust battle engine is reused for combat and initial AI state transitions; that shared component is part of the comparison boundary. No Python process or prepared binary template is used at runtime.

Status: R8 is validated for the supplied diagnostic scope. Chat 1 measured exact preparation and native parity across all 20 encounters, plus exact parity for all 12 mutation-smoke trials and unchanged journal on restart. The bounded byte diagnostic also matched all 17,272,024 bytes before and after battle. These diagnostic results remain import-disabled; performance comparisons and production strategy eligibility are separate work.

Frozen executable: `builds/r8/ka-rust-standalone-optimizer.exe`, SHA256 `99133754795fd345ae5616099b47244d933f1ec79105bbd8f7adb4e4bcbaa718`. Source and manifest are archived alongside it.

R9 comparison adapter compiled and frozen at `builds/r9/ka-rust-standalone-optimizer.exe`, SHA256 `2d269a1254829dbf971413c7c12ae77a3157f97d018eec0b442d579832cc0731`. Source review found no remaining blocker; runtime verification is pending with Chat 1. Adapter configuration and requested checks are in `requests/comparison-adapter-r9.json`; `requests/smoke-r9.json` is a small verification configuration.

Coordinator evidence: `shared/runs/rust-r8-all20-20261004T220159046436Z/canonical-parity.json`, `shared/runs/rust-R8-native-cli-20261004T220218Z/report.json` and `canonical-parity.json`, and `shared/runs/rust-R8-diagnostic-native-cli-20261004T220126Z/{before,after}-byte-diffs.json` (paths relative to `coordination/native-preparation`).

Build from workspace root:

```powershell
$env:CARGO_HOME=(Resolve-Path 'coordination/native-preparation/rust/cargo-cache').Path
cargo build --offline --release --manifest-path coordination/native-preparation/rust/standalone-optimizer/Cargo.toml
& coordination/native-preparation/rust/standalone-optimizer/build-catalog.ps1
```

The catalog export copies immutable recovered rows, resource tables and source hashes. It contains no prepared candidate states or historical battle outcomes.

`--prepare SCENARIO.json CATALOG.json OUTPUT.json` exports fresh state before placement and AI initialization. `--prepare-initialized SCENARIO.json CATALOG.json KERNEL.dll OUTPUT.json` applies the same native template initialization used for battles and exports its state before simulation, without advancing the runtime follower RNG skip. Compare its `snapshot` with the canonical post-initialization oracle; preparation-stage differences must not be counted as combat parity.

Configuration uses schema `ka-rust-optimizer-config-v1`, paths `kernel`, `catalog`, `candidates`, `output`; bounded settings `executors`, `generations`, `candidatesPerGeneration`, `searchSeed`, `seedPairs`; and explicit `mechanicsProvenance` and `policyProvenance` objects. Relative paths resolve against the config file. Candidates are raw scenario JSON arrays, or the candidate-only SQLite source with an encounter filter. Each run ranks one encounter and saves to its own output directory.

The R9 comparison adapter adds optional `resultSyncBatchSize` (1–64; default `min(executors, 8)`) and `stageTimingTelemetry` (default false). Set the batch size to 1 for a complete journal record followed by `File.sync_all` before refilling that worker. The effective batch size and telemetry setting are part of scope identity. Scheduling or channel failures flush an already-collected result wave before returning the error.

With telemetry enabled, `stage-timings.json` separates coordinator wall spans, summed worker operation times, inclusive worker trial time and actual journal serialization/write/sync counters. These quantities overlap and must not be summed or subtracted to infer missing stages. Partial reports are marked incomplete. Measure process start-to-exit externally; the internal elapsed field ends before telemetry output. R9 requires fresh output directories and coordinator verification before comparisons.

The executable writes a durable `battles.jsonl`, resumable generation `checkpoint.json`, `scope.json`, `leaders.json`, `status.json` and rejection diagnostics. Seed banks are fixed nonnegative 31-bit pairs; source candidate identities remain separate from newly generated identities. Losses score zero, unresolved outcomes remain unknown, and native reward bases remain explicit. Selection uses complete seed banks.

Battle records save the actual initialized native template in `prepared`, explicitly marked after follower RNG skipping and before simulation. Battle counters and event buffers begin at the canonical import boundary. Later generations retain at most four elites and at most half the candidate budget, leaving room for descendants; a one-candidate budget runs baselines only.

Use `launch.ps1 -ConfigPath CONFIG.json` for a hidden job and an independent progress window; closing the window leaves the job running. `-Headless` is available for coordinator-controlled checks. The monitor displays real counters and elapsed time; ETA stays unknown until a defensible estimate exists.

Before Chat 1 verifies the exact source/scope, results are verification-only and import-disabled. Explicit `on-verdict` policies remain unchanged through mutation and execute as native policy 2; their records and leaders always remain diagnostic, verification-only and import-disabled, even with an attestation. Parents with no supported mutation still execute unchanged baselines and report the expansion warning. Unsupported source-world state fails closed. No speed or strategy-improvement claim has been established for this implementation.

Permanent objective and user instructions: [`OPTIMIZER-CONTRACT.md`](../../../../OPTIMIZER-CONTRACT.md). Test coordinator: `01a10860-97c5-77f2-8b45-630a99c9ce73` on `local`.
