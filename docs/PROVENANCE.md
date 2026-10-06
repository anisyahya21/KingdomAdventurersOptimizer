# Provenance

Copied from the current local project on 2026-10-06. The package preserves source byte-for-byte except path relocation in selected configuration manifests and a portable C++ build launcher; root entry points are new. No optimizer algorithm was changed. Inventory hashes identify this handoff independently of historical source hashes.

Desktop finishing manifests are authoritative and remain pinned to older builds. Fixed-policy binaries are separate. Rebuilding the latest source does not recreate the older desktop pin. Historical acceptance JSON is retained as evidence and may contain original machine paths; it is not executable configuration.

* **rust / desktop pin**: `r16`, `coordination/native-preparation/rust/standalone-optimizer/builds/r16/ka-rust-standalone-optimizer.exe`, SHA256 `a205ec23d4b0c7ebd32c52af744ddc29283b8ddd523005e961519c9633e05c7a`.
* **go / desktop pin**: `09d6242d59dc48c387180c64634684e0b5ae37c46fe230428c13cfec76228034`, `coordination/native-preparation/go/standalone-optimizer/revisions/resume-serialization-index-09d6242d59dc/go-optimizer.exe`, SHA256 `f0b38a20dd9a18f3b700e336a43387a0ac0fd784973e689e397f2752b8f161cb`.
* **cpp / desktop pin**: `r23`, `coordination/native-preparation/cpp/standalone-optimizer/revisions/r23/standalone-optimizer/optimizer.exe`, SHA256 `21889daeec64f7d1fd47d058d1760912c7822c2f06f79bedcca375e551231117`.
* **go / latest fixed-policy smoke**: `fixed-formation-20261006`, `coordination/fixed-formation-20261006/go-optimizer-fixed.exe`, SHA256 `d4e790d300438ceb707d6b2ebf4f2a16a660fd070906e270c7cc1eb67787eb5d`.
* **rust / latest fixed-policy smoke**: `fixed-formation-20261006`, `coordination/native-preparation/rust/standalone-optimizer/target/release/ka-rust-standalone-optimizer.exe`, SHA256 `160c883e4fcd852ad69e30e2f2229f7cbfe21f302401856454bd094c94ab3876`.
* **cpp / latest fixed-policy smoke**: `fixed-formation-20261006`, `coordination/native-preparation/cpp/standalone-optimizer/optimizer.exe`, SHA256 `2bd4fe930e5f5e643c5d772a28c81009c7b7e8035ad7a5c3b1e000b1deb500a9`.

Third-party source/dependencies preserve their supplied notices. No license ownership is inferred for recovered game mechanics data. Private publication is intended for authorized developer review.
