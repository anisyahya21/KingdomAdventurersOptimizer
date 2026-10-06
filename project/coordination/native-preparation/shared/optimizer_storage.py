"""Route optimizer process data, temporary files and build caches to the A: allocation."""
from __future__ import annotations
import os
from pathlib import Path

def configure_optimizer_storage(project_root):
    project = Path(project_root).resolve()
    optimizer_root = project.parent
    runtime = optimizer_root / "runtime"
    local = runtime / "appdata-local"
    roaming = runtime / "appdata-roaming"
    temporary = runtime / "temp"
    cache = runtime / "cache"
    cargo_home = project / "coordination/native-preparation/rust/cargo-cache"
    go_cache = project / "coordination/native-preparation/go/full-pipeline/.gocache"
    go_path = runtime / "cache/go"
    paths = {
        "runtime": runtime,
        "localappdata": local,
        "appdata": roaming,
        "temp": temporary,
        "pycache": cache / "pycache",
        "uv_cache": cache / "uv",
        "pip_cache": cache / "pip",
        "npm_cache": cache / "npm",
        "cargo_home": cargo_home,
        "cargo_target": project / "coordination/native-preparation/rust/standalone-optimizer/target",
        "go_cache": go_cache,
        "go_mod_cache": cache / "go-mod",
        "go_path": go_path,
        "go_temp": temporary / "go",
        "webview": runtime / "webview2/strategy-optimizer",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    env = {
        "LOCALAPPDATA": paths["localappdata"],
        "APPDATA": paths["appdata"],
        "TEMP": paths["temp"],
        "TMP": paths["temp"],
        "TMPDIR": paths["temp"],
        "PYTHONPYCACHEPREFIX": paths["pycache"],
        "UV_CACHE_DIR": paths["uv_cache"],
        "PIP_CACHE_DIR": paths["pip_cache"],
        "NPM_CONFIG_CACHE": paths["npm_cache"],
        "CARGO_HOME": paths["cargo_home"],
        "CARGO_TARGET_DIR": paths["cargo_target"],
        "GOCACHE": paths["go_cache"],
        "GOMODCACHE": paths["go_mod_cache"],
        "GOPATH": paths["go_path"],
        "GOTMPDIR": paths["go_temp"],
    }
    for name, value in env.items():
        os.environ[name] = str(value)
    return paths
