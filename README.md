# Kingdom Adventurers Optimizer â€” developer handoff

Current source and runnable Windows snapshots for the Python, Rust, Go and C++ optimizer paths. **This repository is for reproducing and fixing existing problems. It is not a finished release.** Strategy libraries and user databases are deliberately excluded.

## Run the current desktop

1. Install Python 3.12+ and Microsoft Edge WebView2 Runtime on Windows x64.
2. From this folder run `python -m pip install -r requirements.txt`.
3. Run `Launch-python.cmd`, `Launch-rust.cmd`, `Launch-go.cmd` or `Launch-cpp.cmd`; alternatively `python launch.py rust`.

Each launcher creates a fresh local library under `runtime/libraries`. No original library is needed. Start with a bounded run and few workers. The shared UI also lets you select engines. Existing bugs may affect start, pause, resume or counters; see [known problems](docs/KNOWN_PROBLEMS.md).

## Explicit latest fixed-policy smoke

Run `python run_fixed_smoke.py python` for one Python policy check and native battle, or replace `python` with `rust`, `go`, `cpp` for short native searches. The native smoke timeout is 120 seconds and output is under `runtime/fixed-smoke`. A second run refuses an existing output directory; move it aside to retain evidence.

Read [structure](docs/ARCHITECTURE.md), [build instructions](docs/BUILD.md), [binary/source provenance](docs/PROVENANCE.md), and [verification](docs/VERIFICATION.md). SHA256 inventory is in `docs/FILE_INVENTORY.json`.

The latest fixed-formation source differs from the pinned desktop builds. Included small binaries/DLLs are optional convenience artifacts; toolchains, venvs, node_modules, caches, databases, strategy libraries, game APK/ELF binaries and private model logs are excluded. Existing mechanics research is in `project/KA-Website/tools/recovery/mechanics-report.md`.
