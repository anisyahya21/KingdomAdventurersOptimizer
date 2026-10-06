# C++ standalone optimizer

Build from the repository root with `cmd /c coordination
ative-preparation\cpp\standalone-optimizer\build.cmd`.
Run `optimizer.exe CONFIG.json`. `--prepare RAW.json TABLES.json` emits prepared state without a battle.
`--bundle-tables PATH_MAP.json OUTPUT.json` assembles catalog files keyed by their filename stems.

Generate a run config with `powershell -ExecutionPolicy Bypass -File coordination
ative-preparation\cpp\standalone-optimizer\make-config.ps1 -RawCandidates <raw-array.json> -Tables <tables-bundle.json> -KernelPath <ka_kernel_encounter_v3.dll> -OutputDir <user-chosen-output-directory> -Provenance <provenance.json>`. The provenance file is a JSON object containing `engineSha256`, `mechanicsSha256`, `policySha256`, `abiSha256`, and `arenaSha256` (or those fields under a `provenance` property). The generator hashes the raw candidates, tables, and kernel files itself and replaces those three provenance fields. It writes `optimizer-config.json` in the current directory unless `-ConfigPath` is supplied; that path cannot overwrite an input file. Optional switches are `-CandidateMetadata <array.json>`, `-Executors <count>` (default 1, maximum 256), `-SeedStart` (default 0), `-SeedCount` (default 1, maximum 1,000,000), `-Generations` (default 1, maximum 100,000), and `-MutationsFile <array.json>`. The seed interval must fit nonnegative signed 31-bit seeds.

The mutations file is a JSON array of objects with `pointer`, `min`, `max`, and optional `step`/`stride` fields. For a fresh run, choose a new or empty `OutputDir`; the generator does not create, clear, or relocate it. To resume, point to the same output directory and use the same generated config and input files. The durable journal resumes compatible work and rejects changed provenance or search settings. Set `-Executors` explicitly when scaling beyond the conservative single-worker default.

This implementation performs raw scenario validation, equipment/stat calculation, seeded encounter construction,
formation, initial component/world construction, mutation and encounter-specific mean-Earned selection in C++.
It imports its constructed state directly into the shared **Rust** `ka_kernel_encounter_v3.dll`, which performs simulation.
No Python runtime, subprocess, or precomputed state template is used during optimization.
The kernel must expose encounter ABI version 3 (624 bytes), in addition to the core ABI. Results include
all 74 core and 32 encounter fields, exact report bytes, canonical encounter projection and whole-state checksum.

Configuration requires rawCandidates (JSON array file), tables (JSON bundle), kernelPath, outputDir, executors,
generations (per encounter), seedStart, seedCount, and provenance. Provenance requires SHA-256 values named
engineSha256, mechanicsSha256, policySha256, abiSha256, arenaSha256, rawCandidatesSha256, tablesSha256,
currentKernelSha256. The CLI checks actual raw/table/kernel file hashes. All trial metadata and prepared-state hashes
are saved. Policy, seeds and provenance must match on resume. Mutations specify an integer JSON pointer, min, max,
and optional step/stride; candidates outside those bounds fail closed. Default seeds update both mathSeed and libSeed.
Use `headless: true` for coordinator correctness checks; ordinary runs open a separate progress window.

On-verdict outcomes and leader entries carry diagnostic=true, verified=false, verificationOnly=true and importDisabled=true. Internal search scores and rankings still use their measured Earned; these markers prevent treating this diagnostic scope as production strategy evidence.

`results.jsonl` stores only native-valid outcomes with raw intent, seeds, parent lineage, exact prepared snapshot,
provenance, report and Earned basis. `errors.jsonl` stores rejected/errored trials separately. One locked writer syncs
each result batch with FlushFileBuffers. Resume restores complete newline records and discards only a torn final tail.
`checkpoint.json` is a hint; the durable journal is authoritative. `status.json` separates restored and fresh saves.

Supported preparation includes generated default formation and the current empty-status isolated-scene0 startProfile.
Captured placement, household expansion, nonempty startingStatus, vehicle/linked parameters and starting invoking skills
fail closed. See preparation-notes.md for table
shape and admission-notes.md for validation limits. Runtime correctness remains subject to Chat 1's centralized
checks; compilation does not prove parity or a performance/strategy gain.
