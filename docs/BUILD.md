# Build and development

For the mechanism-objective correction, build these canonical sources and pass
the newly built executable explicitly to `run_corrected_smoke.py`. The portable
C++ build writes `.build-cache/optimizer-corrected.exe`, leaving the packaged
binary unchanged. See [MECHANISM_OBJECTIVES.md](MECHANISM_OBJECTIVES.md).

Windows x64 is the included binary target. Install Python 3.12+, Microsoft Edge WebView2 Runtime, Rust stable MSVC, Go, Node.js/npm and Visual Studio C++ Build Tools as needed. Run Python dependency installation from the root: `python -m pip install -r requirements.txt`. The host uses stdlib plus pywebview; WebView2 is an OS dependency.

* Rust standalone: `cd project/coordination/native-preparation/rust/standalone-optimizer` then `cargo build --release`. Its `shared-kernel/Cargo.toml` points at the included canonical kernel source.
* Go standalone: `cd project/coordination/native-preparation/go/standalone-optimizer` then `go build -o go-optimizer.exe .`. Original `build.py` expects a excluded private toolchain; use installed Go for a fresh development build. Record a fresh source hash before changing desktop pins.
* C++ standalone: from an x64 Visual Studio Developer Command Prompt, run `project\coordination\native-preparation\cpp\standalone-optimizer\build.cmd`. Included nlohmann header is an ordinary small source dependency.
* Shared Rust kernels: build the Cargo crates under `project/KA-Website/tools/recovery/native`; source filenames/ABI versions and existing DLLs are preserved. Do not replace one versioned DLL with a differently configured build without reviewing the ABI.
* Desktop UI: `cd project/KA-Website/artifacts/kingdom-adventures`, then `npm install --ignore-scripts --no-audit --no-fund` and `npm run build:desktop`. The standalone manifest includes only imported optimizer dependencies and build tools. It does not require the website workspace or API client package. Included desktop-dist avoids a frontend build for ordinary launch.

Desktop manifest executable/kernel/input hashes are validated. Rebuilding does not automatically repin the desktop. Update the manifest path, revision and SHA256 deliberately after testing. Replacing files while a process has loaded them can cause Windows file-lock failures.

Original research/benchmark scripts are retained for understanding and may expect excluded RE evidence, toolchains or large libraries. The supported handoff entry points are root `launch.py`, `Launch-*.cmd`, `run_fixed_smoke.py` and the build commands above.
