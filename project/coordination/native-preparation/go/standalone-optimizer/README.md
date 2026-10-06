# Go standalone optimizer

Independent Go raw admission, catalog/stat/encounter preparation, initial snapshot construction, deterministic adaptive search, and durable result coordination. Native simulation calls the shared canonical **Rust** `ka_kernel_encounter_v3` DLL directly through Windows syscall. No Python/Rust executable is invoked by the runtime. `build.py` is an offline compile helper, not runtime preparation.

Only Chat 1 executes tests or battles. Build with the workspace Python: `python build.py`. The SDK is `../toolchain/go`; compilation uses a local cache. `build-manifest.json` records source and executable identities. A successful compile is not parity approval.

Workload JSON:

```json
{"schema":"ka-go-workload-1","tables":{},"candidates":[],"kernelPath":"absolute DLL path","kernelSha256":"sha256","mechanicsIdentity":{"revision":"explicit identity"}}
```

`tables` contains transparent canonical runtime documents under `encounters`, `weapon-skill-profiles`, `animation-resources`, `effect-resource-checks`, `skill-combat-constants`, plus `Monster` and `Treasure` documents with `rows:[{id,row}]` retaining original positional sheet fields. Candidates are complete raw `ka-special-combat-research-1` scenarios, including parameters, skills, invocations, equipment and seeds.

Coordinator commands:

```powershell
./go-optimizer.exe --mode prepare --input workload.json --output fresh-preparation
./go-optimizer.exe --input workload.json --output fresh-search --budget 128 --batch 64 --executors 5 --seed 1
```

For a long user-facing run, `launch.ps1 -Input workload.json -Output fresh-search` starts the hidden optimizer and lightweight progress window. `-Headless` is available for coordinator-owned short checks. The launcher and monitor are scripts only; builders never execute them.

Preparation mode produces `prepared.json` with full raw inputs, generated snapshots or explicit rejection reasons. Search mode saves fsynced full intent/preparation/results/provenance in `results.jsonl`, atomic search checkpoints, progress `status.json`, and `best-strategies.json`. The same command resumes after interruption. Source revision, workload, policy and batch configuration must match. `stop.request` pauses after a durable batch. A Windows file lock prevents simultaneous writers. Closing a progress monitor leaves the optimizer running.

The current supported slice rejects scheduled/automatic consumables and household expansion; source legality is failclosed. Individual native failures remain diagnostic and stop the run after other valid batch results are saved. Unknown/unresolved rewards do not become zero scores. Chest counts retain canonical loss/certified/queued accounting and `inventoryVerified:false`. Each record includes exact execution settings, full intent and seeds, and actual dispatch parent/candidate/generation/origin lineage. Source `on-verdict` runs remain `diagnostic:true`, `verified:false` even when their parity passes; isolated outputs are not automatically imported into the production strategy library.

Coordinator evidence: all 20 prepared snapshots match exactly (R6 preparation); all 20 baseline native results match canonical core/encounter reports, raw bytes, checksums and Earned with zero differences (`shared/runs/go-r9-native-cli-20261004T210752Z/canonical-parity.json`). R9 baseline20 and mutation40 save/restart checks pass. Current R10 changes record provenance/lineage only; its fresh40 save/restart also passes with an unchanged journal (`shared/runs/go-r10-native-cli-20261004T211811Z/report.json`). Preparation and native simulation code are unchanged from that measured parity path. These checks cover the supplied diagnostic fixture set, not every raw scenario or production reward policy.

Comparison against other languages is deferred until correctness and runnable coverage are established. No performance or strategy-improvement claim follows from compilation.
