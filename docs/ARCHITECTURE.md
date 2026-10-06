# Architecture

This is the current optimizer debugging workspace, separated from the website repository. `project/KA-Website` preserves the old relative layout because runtime path resolution and Rust path dependencies depend on it.

| Location | Responsibility |
|---|---|
| `project/KA-Website/tools/recovery/strategy_optimizer.py` | Python scheduler, SQLite persistence, search |
| `strategy_language_desktop.py`, `strategy_optimizer_desktop.py` in that folder | Shared desktop bridge, engine selection, library and replay actions |
| `strategy_native_controller.py`, `strategy_native_run_config.py`, `strategy_native_assets.py` | Native orchestration, config preparation, immutable manifest pins |
| `project/coordination/native-preparation/{rust,go,cpp}/standalone-optimizer` | Latest native engine source |
| `project/KA-Website/tools/recovery/native/ka_kernel` | Shared Rust battle kernel used by the engines; Python engine also uses Rust simulation |
| `project/KA-Website/artifacts/kingdom-adventures/src` | React UI source |
| `desktop-dist` next to that source | Included current built desktop UI |
| `project/coordination/native-finish/{rust,go,cpp}/build-manifest.json` | Actual desktop native asset pins |
| `project/coordination/fixed-formation-20261006` | Latest policy, bounded workloads, verification evidence, launch configs |
| `runtime/` | New local application state and libraries, ignored by Git |

The host selects an engine, freezes asset identity and config, launches native work, and imports durable records into its own SQLite namespace. Search and simulation are separate layers. Do not assume the latest source was compiled into a pinned desktop executable.
