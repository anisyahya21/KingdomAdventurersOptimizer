"""Run one pinned native proposal operation and preserve its complete provenance.

The native engines produce candidate intents only. This adapter does not prepare,
repair, mutate, simulate, or evaluate those intents in Python.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from strategy_native_assets import validate_engine


_ROOT = Path(__file__).resolve().parents[3]
_INPUT_MANIFEST = _ROOT / "coordination/native-preparation/shared/comparison-inputs/bank2-v3/input-set-manifest.json"
_QUEUE = _ROOT / "coordination/native-preparation/shared/language_test_queue.py"
_TIMEOUT_SECONDS = 180


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.write_text(data, encoding="utf-8", newline="\n")


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON file {path}: {exc}") from exc


def _language(engine: Any, scenario: Mapping[str, Any], request: Mapping[str, Any]) -> str:
    if isinstance(engine, str):
        language = engine
    elif isinstance(engine, Mapping):
        language = engine.get("language")
    else:
        language = None
    language = language or request.get("language") or scenario.get("language")
    if language not in {"rust", "go", "cpp"}:
        raise ValueError("engine must select rust, go or cpp")
    return str(language)


def _check_queue() -> Path:
    spec = importlib.util.spec_from_file_location("_ka_native_proposal_queue", _QUEUE)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load native coordinator queue lock: {_QUEUE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = module.locked()
    try:
        active = [row for row in module.load()["requests"] if row.get("status") == "active"]
        owner = os.environ.get("KA_NATIVE_COORDINATOR_REQUEST_ID")
        foreign = [str(row.get("request_id")) for row in active if row.get("request_id") != owner]
        if foreign:
            raise RuntimeError("native proposal blocked by active coordinator request(s): " + ", ".join(foreign))
    except Exception:
        lock.unlink(missing_ok=True)
        raise
    return lock


def _asset(engine: Mapping[str, Any], name: str) -> Path:
    asset = engine.get("staticInputs", {}).get(name)
    if not isinstance(asset, Mapping) or not isinstance(asset.get("path"), Path):
        raise ValueError(f"Pinned {engine.get('language')} engine does not declare static input {name!r}")
    return asset["path"]


def _go_workload(engine: Mapping[str, Any], scenario: Mapping[str, Any], dest: Path) -> tuple[Path, dict[str, str]]:
    manifest = _json_file(_INPUT_MANIFEST)
    encounter = scenario.get("encounterId")
    if isinstance(encounter, bool) or not isinstance(encounter, int):
        raise ValueError("Go proposals require a raw scenario with integer encounterId")
    try:
        row = manifest["outputs"]["goPerEncounter"][str(encounter)]
        source = Path(row["path"]).resolve()
        expected = row["sha256"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"No original Go workload template for encounter {encounter}") from exc
    actual = _file_sha(source)
    if actual != expected:
        raise ValueError(f"Original Go workload template hash mismatch: {source}")
    workload = _json_file(source)
    if not isinstance(workload, dict) or workload.get("schema") != "ka-go-workload-1":
        raise ValueError("Original Go workload template has an unsupported schema")
    if len(workload.get("candidates", [])) != 1:
        raise ValueError("Original Go proposal workload template must contain exactly one parent")
    workload["candidates"] = [scenario]
    workload["kernelPath"] = str(engine["kernel"]["path"])
    workload["kernelSha256"] = engine["kernel"]["sha256"]
    workload["catalogPath"] = str(_asset(engine, "catalog"))
    workload["factsPath"] = str(_asset(engine, "facts"))
    workload_path = dest / "go-workload.json"
    _write_json(workload_path, workload)
    return workload_path, {"goWorkloadTemplate": str(source), "goWorkloadTemplateSha256": actual}


def _normalize(language: str, native: Any, request: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(native, dict):
        return [], ["native response is not a JSON object"]
    errors: list[str] = []
    if language == "rust":
        rows = native.get("candidates", [])
        parent_id = native.get("parentCandidateSha256") or request.get("parentId")
        default_intent_key = "intent"
    elif language == "go":
        rows = native.get("proposals", [])
        parent_id = native.get("parentId") or request.get("parentId")
        default_intent_key = "intent"
    else:
        rows = native.get("children", [])
        parent_id = native.get("parentCandidateId") or request.get("parentId")
        default_intent_key = "rawScenario"
    if not isinstance(rows, list):
        errors.append("native response proposal collection is not an array")
        rows = []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"native proposal row {index} is not an object")
            continue
        intent = row.get("intent", row.get("rawScenario"))
        encoded = _canonical(intent) if isinstance(intent, (dict, list)) else b""
        legal_value = row.get("legal", row.get("Legal", row.get("admitted")))
        if legal_value is None:
            legal_value = not bool(row.get("error", row.get("Error", row.get("validationError"))))
        row_errors = [str(v) for v in (
            row.get("error", row.get("Error")), row.get("validationError"), row.get("reason")
        ) if v]
        candidate_id = row.get("candidateId", row.get("CandidateID", row.get("candidateSha256")))
        if candidate_id is None and encoded:
            candidate_id = _sha(encoded)
        normalized.append({
            "candidateId": candidate_id,
            "parentId": row.get("parentId", row.get("ParentID", row.get("parentCandidateId", row.get("parentCandidateSha256", parent_id)))),
            "operator": row.get("mutationOperator", row.get("operator", row.get("Operator", row.get("mutation", row.get("changedKnobs"))))),
            "intent": intent,
            "intentSha256": _sha(encoded) if encoded else None,
            "effective": row.get("effective", row.get("effectiveValue", row.get("Effective"))),
            "legal": bool(legal_value),
            "errors": row_errors,
            "nativeRow": row,
        })
    if language == "cpp":
        rejected = native.get("rejected", [])
        if isinstance(rejected, list):
            for index, row in enumerate(rejected):
                if not isinstance(row, dict):
                    errors.append(f"native rejected row {index} is not an object")
                    continue
                message = row.get("error", row.get("reason", row.get("validationError")))
                normalized.append({
                    "candidateId": row.get("candidateId"),
                    "parentId": row.get("parentCandidateId", parent_id),
                    "operator": row.get("mutation", row.get("operator")),
                    "intent": row.get("rawScenario"),
                    "intentSha256": _sha(_canonical(row["rawScenario"])) if isinstance(row.get("rawScenario"), dict) else None,
                    "effective": row.get("effectiveValue"),
                    "legal": False,
                    "errors": [str(message)] if message else ["native engine rejected proposal"],
                    "nativeRow": row,
                })
    if native.get("ok") is False:
        native_error = native.get("error", native.get("errors", "native engine reported ok=false"))
        errors.append(str(native_error))
    return normalized, errors


def propose_native(
    engine: Any,
    scenario: Mapping[str, Any],
    request: Mapping[str, Any],
    directory: str | Path,
) -> dict[str, Any]:
    """Ask the selected native engine to propose descendants of one exact intent."""
    if not isinstance(scenario, Mapping) or not isinstance(request, Mapping):
        raise ValueError("scenario and native request must be JSON objects")
    language = _language(engine, scenario, request)
    verified = validate_engine(language, engine=engine if isinstance(engine, Mapping) else None)
    if isinstance(engine, Mapping):
        supplied = (engine.get("revision"), engine.get("executable", {}).get("sha256"), engine.get("kernel", {}).get("sha256"))
        pinned = (verified["revision"], verified["executable"]["sha256"], verified["kernel"]["sha256"])
        if supplied != pinned:
            raise ValueError("Supplied engine identity differs from freshly validated pinned assets")

    out_dir = Path(directory).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scenario_bytes = _canonical(scenario)
    request_bytes = _canonical(request)
    scenario_path = out_dir / "parent-intent.json"
    request_path = out_dir / "native-request.json"
    _write_json(scenario_path, scenario)
    _write_json(request_path, request)
    scenario_hash = _file_sha(scenario_path)
    request_hash = _file_sha(request_path)
    executable = verified["executable"]["path"]
    provenance: dict[str, Any] = {
        "engineSource": verified.get("source"),
        "manifestPath": str(verified.get("manifestPath")),
        "revision": verified["revision"],
        "executable": {"path": str(executable), "sha256": verified["executable"]["sha256"]},
        "kernel": {"path": str(verified["kernel"]["path"]), "sha256": verified["kernel"]["sha256"]},
        "staticInputs": {name: {"path": str(value["path"]), "sha256": value["sha256"]} for name, value in verified["staticInputs"].items()},
        "parentIntentSha256": _sha(scenario_bytes),
        "requestSha256": request_hash,
        "requestCanonicalSha256": _sha(request_bytes),
    }

    if language == "rust":
        catalog = _asset(verified, "catalog")
        native_output_path = out_dir / "native-proposals.json"
        command = [str(executable), "--propose", str(scenario_path), str(catalog), str(request_path), str(native_output_path)]
        provenance["catalog"] = {"path": str(catalog), "sha256": _file_sha(catalog)}
    elif language == "cpp":
        facts = _asset(verified, "facts" if "facts" in verified["staticInputs"] else "catalog")
        native_output_path = out_dir / "native-proposals.json"
        command = [str(executable), "--propose", str(scenario_path), str(facts), str(request_path), str(native_output_path)]
        provenance["facts"] = {"path": str(facts), "sha256": _file_sha(facts)}
    else:
        workload_path, template_provenance = _go_workload(verified, scenario, out_dir)
        native_output_path = out_dir / "go-proposals" / "proposals.json"
        command = [str(executable), "--mode", "propose", "--input", str(workload_path), "--request", str(request_path), "--output", str(native_output_path.parent)]
        provenance.update(template_provenance)
        provenance["goWorkloadSha256"] = _file_sha(workload_path)

    stdout_path, stderr_path = out_dir / "stdout.log", out_dir / "stderr.log"
    result: dict[str, Any] = {
        "schema": "native-proposal-adapter-v1",
        "language": language,
        "command": command,
        "workingDirectory": str(executable.parent),
        "outputDirectory": str(out_dir),
        "nativeOutputPath": str(native_output_path),
        "provenance": provenance,
    }
    lock = _check_queue()
    started = time.monotonic()
    try:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            kwargs["preexec_fn"] = lambda: os.nice(5)
        try:
            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                try:
                    completed = subprocess.run(
                        command,
                        cwd=str(executable.parent),
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        timeout=_TIMEOUT_SECONDS,
                        check=False,
                        shell=False,
                        **kwargs,
                    )
                    exit_code = completed.returncode
                    timed_out = False
                except subprocess.TimeoutExpired:
                    exit_code = None
                    timed_out = True
        except Exception:
            raise
        result.update({
            "exitCode": exit_code,
            "timedOut": timed_out,
            "timeoutSeconds": _TIMEOUT_SECONDS,
            "durationSeconds": round(time.monotonic() - started, 3),
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "stdoutSha256": _file_sha(stdout_path),
            "stderrSha256": _file_sha(stderr_path),
        })
    finally:
        lock.unlink(missing_ok=True)

    native: Any = None
    if native_output_path.is_file():
        result["nativeOutputSha256"] = _file_sha(native_output_path)
        try:
            native = _json_file(native_output_path)
        except ValueError as exc:
            result["outputParseError"] = str(exc)
    result["nativeResponse"] = native
    proposals, errors = _normalize(language, native, request)
    if result.get("timedOut"):
        errors.append(f"native proposal command exceeded {_TIMEOUT_SECONDS} seconds")
    elif result.get("exitCode") != 0 and not (language == "cpp" and isinstance(native, dict) and native.get("ok") is False):
        errors.append(f"native proposal command exited with status {result.get('exitCode')}")
    if native is None and result.get("outputParseError"):
        errors.append(result["outputParseError"])
    if scenario_hash != _file_sha(scenario_path):
        errors.append("saved parent intent changed during native proposal operation")
    result["proposals"] = proposals
    result["errors"] = errors
    result["parentIntentImmutable"] = scenario_hash == _file_sha(scenario_path)
    result["ok"] = not errors and result.get("exitCode") == 0
    _write_json(out_dir / "adapter-result.json", result)
    return result
