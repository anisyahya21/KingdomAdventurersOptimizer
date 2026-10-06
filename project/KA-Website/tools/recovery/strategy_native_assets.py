"""Resolve and verify one native optimizer's pinned executable and inputs.

Finishing manifests are explicit per-language pins. The existing frozen asset
set remains the fallback until that language has a finishing manifest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Mapping


_RECOVERY = Path(__file__).resolve().parent
_ROOT = _RECOVERY.parents[2]
_FINISH = _ROOT / "coordination" / "native-finish"
_VALID_LANGUAGES = {"rust", "go", "cpp"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "native-finish-build-v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read native engine manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Native engine manifest must contain a JSON object: {path}")
    return value


def _resolved_path(raw: Any, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} path must be a nonempty string")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _asset(value: Any, base: Path, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain path and sha256")
    path = _resolved_path(value.get("path"), base, label)
    sha = value.get("sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise ValueError(f"{label} sha256 must be 64 lowercase hexadecimal characters")
    return {"path": path, "sha256": sha}


def _finishing_manifest(language: str, path: Path) -> dict[str, Any]:
    data = _read_json(path)
    if data.get("schema") != _SCHEMA:
        raise ValueError(f"Unsupported native build manifest schema in {path}")
    if data.get("language") != language:
        raise ValueError(f"Native build manifest language must be {language!r}: {path}")
    revision = data.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError(f"Native build manifest requires an exact revision: {path}")

    # Paths in finishing manifests are relative to the workspace root.
    base = _ROOT
    executable = _asset(
        {"path": data.get("executablePath"), "sha256": data.get("executableSha256")},
        base,
        "executable",
    )
    kernel = _asset(
        {"path": data.get("kernelPath"), "sha256": data.get("kernelSha256")},
        base,
        "kernel",
    )
    raw_inputs = data.get("staticInputs")
    if not isinstance(raw_inputs, dict) or not raw_inputs:
        raise ValueError(f"Native build manifest requires nonempty staticInputs: {path}")
    static_inputs: dict[str, dict[str, Any]] = {}
    for name, spec in raw_inputs.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid static input name in {path}")
        static_inputs[name] = _asset(spec, base, f"staticInputs.{name}")

    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError(f"Native build manifest capabilities must be an object: {path}")
    return {
        "schema": _SCHEMA,
        "language": language,
        "revision": revision,
        "executable": executable,
        "kernel": kernel,
        "staticInputs": static_inputs,
        "capabilities": capabilities,
        "manifestPath": path.resolve(),
        # Capture the selected manifest with the asset identity. Runtime consumers must not
        # reread this path after another builder has published a newer manifest.
        "manifestData": json.loads(json.dumps(data, ensure_ascii=False)),
        "source": "finishing-manifest",
    }


def _legacy_assets(language: str) -> dict[str, Any]:
    module_path = _ROOT / "coordination" / "native-preparation" / "shared" / "native_optimizer_app.py"
    spec = importlib.util.spec_from_file_location("_ka_native_optimizer_app_assets", module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load frozen native asset definitions from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if language not in module.ENGINES:
        raise ValueError(f"Unsupported native optimizer language: {language!r}")
    _, revision, relative_executable, executable_sha = module.ENGINES[language]
    executable = (module.SHARED.parent / relative_executable).resolve()
    source = _read_json(module.MANIFEST)
    source_paths = source.get("staticInputs")
    source_hashes = source.get("sourceHashes")
    if not isinstance(source_paths, dict) or not isinstance(source_hashes, dict):
        raise ValueError(f"Frozen source manifest is incomplete: {module.MANIFEST}")
    hash_keys = {"facts": "factsSha256", "catalog": "catalogSha256", "kernel": "kernelSha256"}
    static_inputs = {}
    for name, hash_key in hash_keys.items():
        if name not in source_paths or not isinstance(source_hashes.get(hash_key), str):
            raise ValueError(f"Frozen source manifest lacks {name} path/hash")
        static_inputs[name] = {
            "path": Path(source_paths[name]).resolve(),
            "sha256": source_hashes[hash_key],
        }
    # Kernel is also surfaced separately for callers that bind the simulator.
    kernel = dict(static_inputs["kernel"])
    return {
        "schema": "legacy-frozen-native-assets-v1",
        "language": language,
        "revision": revision,
        "executable": {"path": executable, "sha256": executable_sha},
        "kernel": kernel,
        "staticInputs": static_inputs,
        "capabilities": {},
        "manifestPath": Path(module.MANIFEST).resolve(),
        "manifestData": json.loads(json.dumps(source, ensure_ascii=False)),
        "source": "frozen-native-optimizer-app",
    }


def load_engine(language: str) -> dict[str, Any]:
    """Load one language's declared pinned assets without hashing file contents.

    An existing finishing manifest is authoritative; malformed or incomplete
    manifests raise ``ValueError`` and never fall back to the frozen build.
    """
    if language not in _VALID_LANGUAGES:
        raise ValueError(f"Unsupported native optimizer language: {language!r}")
    manifest = _FINISH / language / "build-manifest.json"
    if manifest.exists():
        return _finishing_manifest(language, manifest)
    return _legacy_assets(language)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"Cannot read pinned native asset {path}: {exc}") from exc
    return digest.hexdigest()


def _supplied_engine(language: str, selected: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one already-selected engine without consulting today's manifest."""
    if selected.get("language") != language:
        raise ValueError(f"Selected native engine language does not match {language!r}")
    revision = selected.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("Selected native engine has no exact revision")
    if selected.get("schema") not in {_SCHEMA, "legacy-frozen-native-assets-v1"}:
        raise ValueError("Selected native engine has an unsupported asset schema")
    if selected.get("source") not in {"finishing-manifest", "frozen-native-optimizer-app"}:
        raise ValueError("Selected native engine has an unsupported asset source")

    def normalize_asset(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"Selected {label} must contain path and sha256")
        raw_path = value.get("path")
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            raise ValueError(f"Selected {label} path must be a nonempty string")
        return _asset({"path": str(raw_path), "sha256": value.get("sha256")}, _ROOT, label)

    executable = normalize_asset(selected.get("executable"), "executable")
    kernel = normalize_asset(selected.get("kernel"), "kernel")
    raw_inputs = selected.get("staticInputs")
    if not isinstance(raw_inputs, Mapping) or not raw_inputs:
        raise ValueError("Selected native engine requires nonempty staticInputs")
    static_inputs = {}
    for name, value in raw_inputs.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Selected native engine has an invalid static input name")
        static_inputs[name] = normalize_asset(value, f"staticInputs.{name}")
    capabilities = selected.get("capabilities", {})
    if not isinstance(capabilities, Mapping):
        raise ValueError("Selected native engine capabilities must be an object")
    manifest_data = selected.get("manifestData")
    if manifest_data is not None and not isinstance(manifest_data, Mapping):
        raise ValueError("Selected engine manifest snapshot must be an object")
    if selected.get("source") == "finishing-manifest" and isinstance(manifest_data, Mapping):
        if manifest_data.get("schema") != _SCHEMA or manifest_data.get("language") != language:
            raise ValueError("Selected engine manifest snapshot has a mismatched schema or language")
        if manifest_data.get("revision") != revision:
            raise ValueError("Selected engine revision differs from its captured manifest")
        expected_executable = _asset({"path": manifest_data.get("executablePath"),
                                      "sha256": manifest_data.get("executableSha256")}, _ROOT, "manifest executable")
        expected_kernel = _asset({"path": manifest_data.get("kernelPath"),
                                  "sha256": manifest_data.get("kernelSha256")}, _ROOT, "manifest kernel")
        if expected_executable != executable or expected_kernel != kernel:
            raise ValueError("Selected engine assets differ from its captured manifest")
        manifest_inputs = manifest_data.get("staticInputs")
        if not isinstance(manifest_inputs, Mapping) or set(manifest_inputs) != set(static_inputs):
            raise ValueError("Selected static inputs differ from its captured manifest")
        for name, value in manifest_inputs.items():
            if _asset(value, _ROOT, f"manifest staticInputs.{name}") != static_inputs[name]:
                raise ValueError(f"Selected static input {name!r} differs from its captured manifest")

    engine = dict(selected)
    engine.update(schema=selected["schema"], language=language, revision=revision,
                  executable=executable, kernel=kernel, staticInputs=static_inputs,
                  capabilities=dict(capabilities),
                  manifestPath=Path(selected["manifestPath"]).resolve() if selected.get("manifestPath") else None,
                  manifestData=(json.loads(json.dumps(manifest_data, ensure_ascii=False))
                                if manifest_data is not None else None))
    return engine


def validate_engine(language: str, engine: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Freshly hash either the selected pin or, when omitted, the current declared engine."""
    if language not in _VALID_LANGUAGES:
        raise ValueError(f"Unsupported native optimizer language: {language!r}")
    engine = _supplied_engine(language, engine) if engine is not None else load_engine(language)
    assets = [("executable", engine["executable"]), ("kernel", engine["kernel"])]
    assets.extend((f"staticInputs.{name}", value) for name, value in engine["staticInputs"].items())
    seen: set[Path] = set()
    for label, asset in assets:
        path = asset["path"]
        # The kernel may also be listed among staticInputs. Verify it once.
        if path in seen:
            if asset["sha256"] != next(item["sha256"] for _, item in assets if item["path"] == path):
                raise ValueError(f"Conflicting pinned hashes for {path}")
            continue
        seen.add(path)
        actual = _digest(path)
        if actual != asset["sha256"]:
            raise ValueError(f"{language} {label} SHA-256 mismatch for {path}: expected {asset['sha256']}, got {actual}")
    return engine

