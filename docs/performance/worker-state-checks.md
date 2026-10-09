# Worker state checks: overhead investigation

Measured 2026-10-09. **Status: local development prototype, not a rollout.** This documentation PR publishes the explanation and compact evidence; it does not include the prototype source, benchmark harness or development executables. Normal desktop pins remain unchanged.

## What got faster?

The Rust and Go worker wrappers performed redundant full-state checksum scans around a fight. Opting out of those scans increased completed evaluations per second in matched tests. The coordinator and combat simulation algorithms were unchanged. The final checksum of the simulated working state remains present for replay identity.

A prepared battle state is the native, parameterized starting state for a battle. A worker makes an independent mutable copy and runs the fight on that copy. A checksum scans bytes to detect differences; it does not create the copy or make it independent.

The native state occupies 17,272,024 bytes. Local microbenchmarks measured roughly 12.6–12.9 ms per full checksum scan and 2.1–2.3 ms per clone. These are separate operations. A short fight can therefore spend more time scanning state than executing combat. Battle cost varies with encounter; a sub-millisecond fight is not representative of every fight.

## Behavior by engine

| Engine/path | Local prototype change |
| --- | --- |
| Rust wrapper | Skips template-before, clone-before and template-after scans when explicitly disabled; retains clone-after checksum. Replay/diagnostic paths force checks. |
| Go snapshot wrapper | Skips initial and post-follower-skip scans when explicitly disabled; retains final checksum. Trace forces checks. |
| Go resident baseline path | Skips per-task pre-run scan; retains startup validation and final checksum. |
| Python | Normal native hot path had no equivalent redundant scans to remove; fallback unchanged. |
| C++ | Final replay checksum retained; no corresponding behavior change. |

The local option is `KA_VERIFY_BATTLE_STATE=0`; verification defaults to enabled in the prototype. This is not a claim that the option exists in this repository's published binaries.

## Matched evaluation throughput

Four workers; 512 seed pairs per arm; one fixed candidate; encounters 0 and 14. Each checked/reduced-check pair used the same build, inputs and kernel. Sequential, nonoverlapping arms measured full process wall time, including setup and durable result journals.

| Wrapper | Encounter | Checks enabled, evaluations/s | Reduced checks, evaluations/s | Speedup |
| --- | ---: | ---: | ---: | ---: |
| Rust | 0 | 59.75 | 137.10 | 2.29× |
| Rust | 14 | 36.04 | 90.98 | 2.52× |
| Go | 0 | 67.68 | 104.73 | 1.55× |
| Go | 14 | 33.58 | 52.62 | 1.57× |

These are bounded evaluation pipelines, not per-battle kernel timings, long adaptive searches, engine rankings or strategy-quality gains. The often quoted 2.29× refers to the entire Rust encounter-0 test: 512 evaluations took 8.569 s before and 3.735 s after. It does not mean the combat kernel became 2.29× faster.

## Verification and provenance

Saved local reports passed wrapper parity, replay checks, controller persistence/import and lifecycle checks covering pause, resume, stop, drain, reopen and export. Corresponding battle outputs and final checksums matched after normalizing execution order and excluding timing, worker assignment and deliberately omitted pre-check metadata.

Low-level tests covered 90 state/report comparisons across encounters 0, 4, 5, 12, 14 and 19, with multiple seeds and reset modes, plus concurrent clones. A full reset API was tested, but was not used for the throughput gains above. A block-scanning delta reset was slower than full reset; a mutation-journal rollback was not implemented.

[Compact evidence](evidence/worker-state-checks-20261009.json) preserves exact timings, seed generation, source hashes and check outcomes. The original local investigation directory was `project/coordination/worker-overhead-20261009`; its full reports, harness and binaries are not included here. Therefore the compact evidence supports auditing the recorded measurements, but is not a self-contained reproduction package. Go persistence/lifecycle validation used a build with source-revision metadata added; throughput used the earlier executable from the same source.

## Remaining work

- Publish and review a portable source/harness change against current repository source before rollout.
- Reproduce the matched tests from that published change and update build provenance before replacing pins.
- Measure representative adaptive optimization, worker utilization and strategy quality per hour. This test does not establish those outcomes.
- Continue independent coordinator, storage and memory investigations. This experiment does not establish RAM exhaustion, database I/O or scheduler starvation as solved.
- Resolve game-mechanics correctness independently; matching checked and reduced-check builds does not prove agreement with the game.
