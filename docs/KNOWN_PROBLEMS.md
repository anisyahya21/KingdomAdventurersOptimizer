# Known problems and investigation starting points

These are a debugging handoff, not promises of correctness. Historical evidence is in `project/coordination/native-finish/resumed-acceptance/a-only-acceptance/functional-acceptance.json`; its paths refer to the original test machine and are historical evidence, not launch dependencies.

* **C++ resume:** historical R24 acceptance saved 9 rows, paused successfully, then Resume failed with duplicate parent candidate ID `822566ec2e800688edfe7fc27c401eedb1b8cdfab46476652997217a1f8bb0b5`. New-session reset also remained blocked. Inspect `strategy_native_run_config.py` parent ID guard and `strategy_native_controller.py`. The included desktop pin is R23; R24 historical evidence is not evidence that the included R23 or latest source produces the same failure.
* **Rust persistence:** historical R16 acceptance reached 875 durable/imported trials then failed `atomic checkpoint replacement failed: Access is denied. (os error 5)`. Inspect Rust `src/store.rs`, checkpoint replacement and competing readers on Windows. R16 is the included desktop pin. Trigger in UI: start a bounded campaign, allow saved rows, pause/resume; capture logs. Exact historical checkpoint/library was excluded, so the 875-trial reproduction is not guaranteed.
* **Go UI rates:** earlier reports describe blank rates. Connection, stop and reopen were observed working; current visual rate display needs retesting with a fresh library.
* **Latest fixed formation versus desktop:** fixed-policy source and separate binaries/configs are included. The shared desktop's finishing manifests pin older engines; the diagnostic host also contains legacy R8/R11/R9 fallback constants. Use `run_fixed_smoke.py` to explicitly activate the latest fixed policy. Ordinary desktop launch does not establish fixed-policy enforcement.
* Full clean-machine GUI behavior and all-engine resume/export/replay remain unverified. Recovered mechanics retain research assumptions; compare results against the supplied reports rather than treating them as authoritative game truth.

## Useful reproduction record

Launch one engine with a new library; record engine revision, mode, encounter selection, worker count, saved row count and exact error. Start, wait for a few saved rows, Pause, Resume, Stop, close and reopen. Copy the new runtime logs/config/checkpoint and the minimal database needed for the failure into a private bug report. Avoid an unbounded campaign while investigating.
