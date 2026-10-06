Chat 1 exclusively executes these checks. Builders have only compiled the implementation.

First request: `target/release/ka-rust-standalone-optimizer.exe requests/smoke.json`, followed by the identical command for resume/dedup. The configuration uses real candidate-only source intent, encounter 0, two raw candidates, two fixed legal seed pairs, two generations and two workers. It is verification-only until Chat 1 supplies a matching parity attestation.

Required invariants:

- Full raw source identities/intent remain separate from per-trial seed substitution and descendant lineage.
- Initial reward queue is zero; static prize candidates are not starting earned rewards.
- Rust numeric preparation and each sequential placement/occupancy/AI callback match the canonical prepared state for the same raw intent and seed pair.
- Compare exact native report, encounter-v3 telemetry, RNG draw counts and reward basis against canonical native execution, with callback/prepared-stage differences disclosed.
- Native status is zero; clone starts with the initialized-template checksum; template remains unchanged.
- Resident workers return all distinct tasks once; one writer saves full provenance. Repeated command resumes without duplicate battle records.
- Negative seeds, unsupported raw state, malformed declaration and diagnostic finish-policy checks are verification only and never strategy evidence.
- Durability checks cover a torn final JSONL suffix, conflicting duplicate identity, checkpoint fallback and OS writer lock release after crash.

After bounded parity succeeds, expand one actual raw source candidate per encounter 0–19 with the same seed pairs. Then test changed supported mutation fields. Do not benchmark or rank language versions yet.
