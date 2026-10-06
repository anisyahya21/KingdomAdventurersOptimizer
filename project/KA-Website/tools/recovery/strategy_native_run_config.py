"""Build one exact, provenance-pinned native optimizer drain request.

This module writes config/input files only. The caller owns process lifecycle,
durable result ingestion and the shared strategy-library writer.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence

from strategy_native_assets import validate_engine
from strategy_native_parent_identity import validate_parent_identity_aliases


_ROOT = Path(__file__).resolve().parents[3]
_INPUT_MANIFEST = _ROOT / "coordination/native-preparation/shared/comparison-inputs/bank2-v3/input-set-manifest.json"
_LEGACY_PROJECT_ROOT = PureWindowsPath(r"C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy")
_A_PROJECT_ROOT = PureWindowsPath(str(_ROOT))
_GO_WORKLOAD_ROOT = (_INPUT_MANIFEST.parent / "inputs/go-workloads-by-encounter").resolve()
_POLICY_VERSION = "strategy-outcomes-terminal-policy-dispatch-v1"
_MAX_31BIT = 2**31 - 1


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON input {path}: {exc}") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"Cannot hash input file {path}: {exc}") from exc
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rust_checkpoint_for_config(path: Path, config: dict, engine: dict, folder: Path) -> Path:
    """Migrate only proven dispatch-field hashes; preserve the source checkpoint."""
    checkpoint = _json(path)
    catalog_sha = _hash_bytes(_canonical(_json(Path(engine['staticInputs']['catalog']['path']))))
    def identity(native_config: dict) -> dict:
        return dict(kernelSha256=engine['kernel']['sha256'],
            executableSha256=engine['executable']['sha256'],
            catalogSha256=catalog_sha,
            mechanics=native_config['mechanicsProvenance'],
            policy=native_config['policyProvenance'],
            executionMode=native_config['executionMode'],
            proposalRequest=native_config.get('proposalRequest'))
    wanted = _hash_bytes(_canonical(identity(config)))
    if checkpoint.get('resumeCompatibilitySha256') == wanted:
        return path
    source_folder = path.parent.parent
    old_config_path = source_folder / 'config.json'
    old_spec = _json(source_folder / 'run-spec.json')
    if (_hash_file(old_config_path) != old_spec.get('configSha256')
            or old_spec.get('executableSha256') != engine['executable']['sha256']
            or old_spec.get('kernelSha256') != engine['kernel']['sha256']):
        raise ValueError('Rust checkpoint source config/assets do not match verified provenance')
    old_config = _json(old_config_path)
    old_identity = identity(old_config)
    if _hash_bytes(_canonical(old_identity)) != checkpoint.get('resumeCompatibilitySha256'):
        raise ValueError('Rust checkpoint compatibility cannot be reproduced from its exact source config')
    old_identity['policy'] = {key: value for key, value in old_identity['policy'].items()
                              if key not in ('seedPairs', 'requestSha256')}
    if old_identity != identity(config):
        raise ValueError('Rust checkpoint has incompatible mechanics or stable search policy')
    checkpoint['adapterMigration'] = dict(kind='exclude-dispatch-fields-from-resume-guard',
        sourceCheckpoint=str(path), sourceCheckpointSha256=_hash_file(path),
        originalResumeCompatibilitySha256=checkpoint['resumeCompatibilitySha256'])
    checkpoint['resumeCompatibilitySha256'] = wanted
    target = folder / 'imported-checkpoint.json'
    _write_json(target, checkpoint)
    return target


def _extract_parents(parents: Sequence[Any], mixed_encounters: bool = False) -> tuple[list[dict[str, Any]], list[Any]]:
    if not isinstance(parents, (list, tuple)) or not parents:
        raise ValueError("At least one exact parent intent is required")
    intents: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    parent_ids: set[str] = set()
    encounter: int | None = None
    for index, row in enumerate(parents):
        if not isinstance(row, Mapping):
            raise ValueError(f"Parent {index} must be an object containing a raw intent")
        intent = row.get("scenario", row.get("intent", row))
        if not isinstance(intent, dict) or isinstance(intent, bool):
            raise ValueError(f"Parent {index} has no complete raw intent object")
        current_encounter = intent.get("encounterId")
        if isinstance(current_encounter, bool) or not isinstance(current_encounter, int) or not 0 <= current_encounter <= 19:
            raise ValueError(f"Parent {index} has an invalid encounterId")
        if encounter is None:
            encounter = current_encounter
        elif current_encounter != encounter and not mixed_encounters:
            raise ValueError("One native drain config must contain parents for exactly one encounter")
        parent_id = row.get("candidateId", row.get("id", f"caller-parent-{index}"))
        if not isinstance(parent_id, (str, int)) or isinstance(parent_id, bool) or not str(parent_id):
            raise ValueError(f"Parent {index} has an invalid stable candidate ID")
        parent_id = str(parent_id)
        if parent_id in parent_ids:
            raise ValueError(f"Duplicate parent candidate ID {parent_id!r}")
        parent_ids.add(parent_id)
        intents.append(intent)
        parent_metadata = {
            "candidateId": parent_id,
            "source": row.get("source", "caller"),
            "intentSha256": _hash_bytes(_canonical(intent)),
        }
        aliases = validate_parent_identity_aliases(parent_id, intent, row.get("identityAliases"))
        if aliases:
            parent_metadata["identityAliases"] = aliases
        metadata.append(parent_metadata)
    return intents, metadata


def _seed_bank(seed_pairs: Sequence[Sequence[int]]) -> list[list[int]]:
    if not isinstance(seed_pairs, (list, tuple)) or not seed_pairs:
        raise ValueError("At least one ordered [mathSeed, libSeed] pair is required")
    result: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    for index, pair in enumerate(seed_pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"Seed pair {index} must contain exactly mathSeed and libSeed")
        values: list[int] = []
        for seed in pair:
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_31BIT:
                raise ValueError(f"Seed pair {index} contains a value outside nonnegative signed 31-bit range")
            values.append(seed)
        key = (values[0], values[1])
        if key in seen:
            raise ValueError(f"Duplicate seed pair {key} would duplicate evidence")
        seen.add(key)
        result.append(values)
    return result


def _source_manifest() -> dict[str, Any]:
    manifest = _json(_INPUT_MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("complete") is not True:
        raise ValueError(f"Original native input manifest is incomplete: {_INPUT_MANIFEST}")
    return manifest


def _source_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Go workload manifest path must be a nonempty string")
    supplied = PureWindowsPath(value)
    if supplied.is_absolute():
        try:
            relative = supplied.relative_to(_LEGACY_PROJECT_ROOT)
        except ValueError:
            try:
                relative = supplied.relative_to(_A_PROJECT_ROOT)
            except ValueError as exc:
                raise ValueError(f"Go workload path is outside the known project roots: {value}") from exc
    else:
        relative = supplied
    path = (_ROOT.joinpath(*relative.parts)).resolve()
    try:
        path.relative_to(_GO_WORKLOAD_ROOT)
    except ValueError as exc:
        raise ValueError(f"Go workload path is outside the frozen per-encounter workload set: {value}") from exc
    return path


def _check_output_space(directory: Path, estimated_bytes: int) -> None:
    usage = shutil.disk_usage(directory.parent if directory.parent.exists() else _ROOT)
    if usage.free < estimated_bytes:
        raise ValueError(f"Insufficient free storage: estimated {estimated_bytes} bytes required, {usage.free} bytes available")


def prepare_native_run(
    options: Mapping[str, Any],
    directory: str | Path,
    parents: Sequence[Any],
    seed_pairs: Sequence[Sequence[int]],
    engine: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one native optimizer config and return a provenance-rich run spec.

    ``options`` accepts language, workers, mode (search/evaluate), generations,
    searchSeed, purpose, and testPurpose. A finishing build with a real strategy
    purpose is production; the legacy frozen fallback and coordinator diagnostic
    runs remain diagnostic.
    """
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    language = options.get("language")
    if language not in {"rust", "go", "cpp"}:
        raise ValueError("language must be rust, go or cpp")
    verified_engine = validate_engine(language, engine=engine)
    if engine is not None:
        supplied_identity = (engine.get("revision"), engine.get("executable", {}).get("sha256"), engine.get("kernel", {}).get("sha256"))
        verified_identity = (verified_engine.get("revision"), verified_engine["executable"]["sha256"], verified_engine["kernel"]["sha256"])
        if supplied_identity != verified_identity:
            raise ValueError("Supplied engine mapping differs from freshly validated selected assets")
    engine = verified_engine
    if engine.get("language") != language:
        raise ValueError("Selected engine manifest language does not match options")
    build_details: dict[str, Any] = {}
    manifest_data = engine.get("manifestData")
    if isinstance(manifest_data, Mapping):
        # Use the manifest captured with the selected executable; its path may now name a
        # newer builder revision, which must not retarget this controller mid-session.
        build_details = dict(manifest_data)
    mode = options.get("mode", "search")
    if mode not in {"search", "evaluate"}:
        raise ValueError("mode must be search or evaluate")
    purpose = options.get("purpose", "strategy-search" if mode == "search" else "strategy-evaluate")
    requested_execution = options.get("executionMode")
    if requested_execution not in {None, "diagnostic", "production"}:
        raise ValueError("executionMode must be diagnostic or production")
    test_purpose = options.get("testPurpose")
    if test_purpose is None:
        lowered_purpose = str(purpose).lower()
        test_purpose = "diagnostic" if any(tag in lowered_purpose for tag in ("diagnostic", "synthetic", "invalid", "verification")) else "production"
    if test_purpose not in {"production", "diagnostic"}:
        raise ValueError("testPurpose must be production or diagnostic")
    if test_purpose == "production" and any(
        tag in str(purpose).lower() for tag in ("diagnostic", "synthetic", "invalid", "verification")
    ):
        raise ValueError("Diagnostic, synthetic, invalid or verification purpose cannot be marked production")
    actual_mode = "production" if engine.get("source") == "finishing-manifest" and test_purpose == "production" and requested_execution != "diagnostic" else "diagnostic"
    production_reason = "Finishing build and real strategy purpose" if actual_mode == "production" else (
        "Explicit coordinator diagnostic purpose" if test_purpose == "diagnostic" else
        "Frozen fallback build remains diagnostic"
    )
    mixed_encounters = options.get('mixedEncounters', False)
    if not isinstance(mixed_encounters, bool):
        raise ValueError('mixedEncounters must be boolean')
    intents, parent_metadata = _extract_parents(parents, mixed_encounters)
    if language == "rust" and len(intents) > (2048 if mode == "search" else 4096):
        raise ValueError("Rust parent count exceeds candidatesPerGeneration capacity for this operation")
    pairs = _seed_bank(seed_pairs)
    encounter = intents[0]["encounterId"]
    encounter_ids = sorted({intent['encounterId'] for intent in intents})
    focus = options.get('focusEncounters', encounter_ids)
    if (not isinstance(focus, list) or not focus or any(isinstance(e, bool) or
            not isinstance(e, int) or e not in encounter_ids for e in focus)
            or len(set(focus)) != len(focus)):
        raise ValueError('focusEncounters must be unique admitted encounter IDs')
    if not mixed_encounters and options.get("encounterId", encounter) != encounter:
        raise ValueError("Configured encounterId differs from exact parent intents")
    workers = options.get("workers", 1)
    max_workers = 64 if language == "rust" else 256
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= max_workers:
        raise ValueError(f"workers must be in 1..{max_workers} for {language}")
    generations = options.get("generations", 2 if mode == "search" else 1)
    max_generations = 100_000
    if isinstance(generations, bool) or not isinstance(generations, int) or not 1 <= generations <= max_generations:
        raise ValueError(f"generations must be in 1..{max_generations}")
    if mode == "evaluate":
        generations = 1
    if language == "rust" and len(pairs) > 4096:
        raise ValueError("Rust accepts at most 4096 exact seed pairs per drain")
    if language == "cpp" and len(pairs) > 1_000_000:
        raise ValueError("C++ accepts at most 1,000,000 exact seed pairs per drain")
    if language == "go" and mode == "search" and len(pairs) > 2**64 - 1:
        raise ValueError("Go search budget exceeds the native counter range")
    search_seed = options.get("searchSeed", pairs[0][0])
    if isinstance(search_seed, bool) or not isinstance(search_seed, int) or not 0 <= search_seed <= _MAX_31BIT:
        raise ValueError("searchSeed must be a nonnegative signed 31-bit integer")
    batch = options.get("batch", min(32, max(1, len(pairs))))
    if language == "go" and (isinstance(batch, bool) or not isinstance(batch, int) or not 1 <= batch <= 4096):
        raise ValueError("Go batch must be in 1..4096")
    budget = options.get("budget", len(pairs))
    if language == "go" and mode == "search" and (isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 2**64 - 1):
        raise ValueError("Go search budget must be a positive uint64 drain budget")

    if bool(options.get("resourceCheck", True)):
        # Import only the existing hardware guard; no run or native operation is invoked.
        from strategy_native_assets import _ROOT as root
        app_file = root / "coordination/native-preparation/shared/native_optimizer_app.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ka_native_resource_guard", app_file)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load native resource guard from {app_file}")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        app.resource_check()

    output_dir = Path(directory).resolve()
    if output_dir.exists():
        raise ValueError(f"Run output directory already exists: {output_dir}")
    # Estimate one durable record per parent/seed/generation with 100 KiB per
    # record plus 64 MiB fixed journal/checkpoint room. This is a storage guard,
    # not an arbitrary work/trial ceiling; search growth remains engine-specific.
    generation_factor = generations if mode == "search" else 1
    if language == "rust" and mode == "search":
        estimated_records = len(pairs) * max(len(intents), 2 * len(intents)) * generations
    elif language == "cpp" and mode == "search":
        estimated_records = len(pairs) * len(intents) * (generations + 1)
    elif language == "go" and mode == "search":
        estimated_records = budget
    else:
        estimated_records = len(pairs) * len(intents) * generation_factor
    estimated_bytes = 64 * 1024**2 + estimated_records * 100 * 1024
    _check_output_space(output_dir, estimated_bytes)

    source_manifest: dict[str, Any] | None = None
    template_input: dict[str, str] | None = None
    if language in {"go", "cpp"}:
        source_manifest = _source_manifest()
    parent_path = output_dir / "candidates.json"
    candidate_metadata_path = output_dir / "candidate-metadata.json"
    seed_path = output_dir / "seed-bank.json"
    output_path = output_dir / "optimizer-output"
    config_path = output_dir / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=False)
    _write_json(parent_path, intents)
    _write_json(candidate_metadata_path, parent_metadata)
    _write_json(seed_path, pairs)
    output_path.mkdir()

    request_payload = {
        "schema": "native-run-request-v1",
        "language": language,
        "revision": engine["revision"],
        "mode": mode,
        "encounterId": encounter,
        "parents": parent_metadata,
        "seedPairs": pairs,
        "generations": generations,
        "purpose": purpose,
        "testPurpose": test_purpose,
        "executionMode": actual_mode,
        "executionModeRequested": requested_execution,
        "policySemanticsVersion": _POLICY_VERSION,
    }
    if mixed_encounters:
        request_payload.update(encounterId=None, encounterIds=encounter_ids,
                               focusEncounters=focus, scheduling='mixed-encounter-global-pool')
    request_sha = _hash_bytes(_canonical(request_payload))
    policy = {
        "schema": "native-run-policy-v1",
        "purpose": request_payload["purpose"],
        "testPurpose": test_purpose,
        "policySemanticsVersion": _POLICY_VERSION,
        "executionMode": actual_mode,
        "finishPolicies": sorted({intent.get("finishPolicy", "at-horizon") for intent in intents}),
        "earnedInterpretation": "strategy_outcomes.py; retain terminal-policy-dispatch separately for declared on-verdict",
        "missingEarned": "unknown-with-reason",
        "seedPairs": pairs,
        "requestSha256": request_sha,
    }
    policy_sha = _hash_bytes(_canonical(policy))

    config: dict[str, Any]
    command: list[str]
    source_set_sha: str | None = None
    if language == "rust":
        static = engine["staticInputs"]
        catalog = static.get("catalog")
        if catalog is None:
            raise ValueError("Rust engine manifest must declare staticInputs.catalog")
        config = {
            "schema": "ka-rust-optimizer-config-v1",
            "kernel": str(engine["kernel"]["path"]),
            "catalog": str(catalog["path"]),
            "candidates": str(parent_path),
            "output": str(output_path),
            "executors": workers,
            "generations": generations,
            "candidatesPerGeneration": len(intents) if mode == "evaluate" else max(2 * len(intents), len(intents) + 1),
            "candidateLimit": len(intents),
            "encounterFilter": encounter,
            "searchSeed": options.get("searchSeed", pairs[0][0]),
            "seedPairs": pairs,
            "mechanicsProvenance": {
                "engineSha256": engine["executable"]["sha256"],
                "kernelSha256": engine["kernel"]["sha256"],
                "staticInputs": {name: value["sha256"] for name, value in static.items()},
            },
            "policyProvenance": {key: value for key, value in policy.items()
                                 if key not in ('seedPairs', 'requestSha256')},
            "executionMode": actual_mode,
            "candidateSourceIds": [row["candidateId"] for row in parent_metadata],
        }
        if mixed_encounters:
            config.pop('encounterFilter', None)
            config['encounterFilters'] = encounter_ids
            config['focusEncounters'] = focus
            # Mixed search budgets are per encounter, rather than multiplying the
            # per-encounter population by the number of admitted encounters.
            per_encounter = max(sum(i['encounterId'] == e for i in intents) for e in encounter_ids)
            config['candidatesPerGeneration'] = per_encounter if mode == 'evaluate' else max(2 * per_encounter, per_encounter + 1)
            config['candidateLimit'] = per_encounter
        config['stageTimingTelemetry'] = True
        initial_state = options.get("initialSearchState", options.get("searchStatePath"))
        if initial_state is not None:
            config["initialSearchState"] = _json(Path(initial_state).resolve()) if isinstance(initial_state, (str, Path)) else initial_state
        if options.get('initialCheckpoint') is not None:
            config['initialCheckpoint'] = str(_rust_checkpoint_for_config(
                Path(options['initialCheckpoint']).resolve(), config, engine, output_dir))
        command = [str(engine["executable"]["path"]), str(config_path)]
    elif language == "go":
        assert source_manifest is not None
        row = source_manifest.get("outputs", {}).get("goPerEncounter", {}).get(str(encounter))
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not isinstance(row.get("sha256"), str):
            raise ValueError(f"Original source manifest lacks Go workload template for encounter {encounter}")
        workload_path = _source_path(row["path"])
        if _hash_file(workload_path) != row["sha256"]:
            raise ValueError(f"Go workload template hash mismatch: {workload_path}")
        template_input = {"goWorkloadTemplatePath": str(workload_path), "goWorkloadTemplateSha256": row["sha256"]}
        workload = _json(workload_path)
        if not isinstance(workload, dict) or workload.get("schema") != "ka-go-workload-1":
            raise ValueError(f"Invalid Go workload template: {workload_path}")
        workload["candidates"] = intents
        if mixed_encounters:
            workload['focusEncounters'] = focus
        workload["candidateSourceIds"] = [row["candidateId"] for row in parent_metadata]
        workload["kernelPath"] = str(engine["kernel"]["path"])
        workload["kernelSha256"] = engine["kernel"]["sha256"]
        workload["executionMode"] = actual_mode
        workload["testPurpose"] = test_purpose
        if "catalog" in engine["staticInputs"]:
            workload["catalogPath"] = str(engine["staticInputs"]["catalog"]["path"])
        if "facts" in engine["staticInputs"]:
            workload["factsPath"] = str(engine["staticInputs"]["facts"]["path"])
        workload["mechanicsIdentity"] = {
            "templateMechanics": workload.get("mechanicsIdentity"),
            "executableSha256": engine["executable"]["sha256"],
            "kernelSha256": engine["kernel"]["sha256"],
            "staticInputs": {name: value["sha256"] for name, value in engine["staticInputs"].items()},
        }
        workload["policyProvenance"] = policy
        if isinstance(build_details.get("sourceSetSha256"), str):
            workload["sourceSetSha256"] = build_details["sourceSetSha256"]
        config = workload
        mode_arg = "search" if mode == "search" else "evaluate"
        command = [
            str(engine["executable"]["path"]), "--mode", mode_arg,
            "--input", str(config_path), "--output", str(output_path),
            "--executors", str(workers), "--batch", str(batch),
        ]
        if mode == "search":
            command += ["--budget", str(budget), "--seed", str(search_seed)]
            search_state = options.get("searchStatePath")
            if search_state is not None:
                search_state_path = Path(search_state).resolve()
                if not search_state_path.is_file():
                    raise ValueError(f"Go search-state file does not exist: {search_state_path}")
                command += ["--search-state", str(search_state_path)]
        else:
            command += ["--seed-bank", str(seed_path), "--sync-batch", "1"]
    else:
        assert source_manifest is not None
        static = engine["staticInputs"]
        facts = static.get("facts", static.get("catalog"))
        if facts is None:
            raise ValueError("C++ engine manifest must declare staticInputs.facts")
        template = source_manifest.get("cppProvenance", {}).get(str(encounter))
        if not isinstance(template, dict):
            raise ValueError(f"Original source manifest lacks C++ provenance template for encounter {encounter}")
        cpp_provenance = dict(template)
        cpp_provenance.update({
            "engineSha256": engine["kernel"]["sha256"],
            "policySha256": policy_sha,
            "rawCandidatesSha256": _hash_file(parent_path),
            "tablesSha256": facts["sha256"],
            "currentKernelSha256": engine["kernel"]["sha256"],
        })
        mutations = []
        if mode == "search":
            mutations = options.get("mutations") or [{
                "pointer": "/ownUnits/0/equipment/0/level",
                "min": 1,
                "max": 99,
                "step": 1,
                "stride": 1,
            }]
        config = {
            "rawCandidates": str(parent_path),
            "tables": str(facts["path"]),
            "kernelPath": str(engine["kernel"]["path"]),
            "outputDir": str(output_path),
            "executors": workers,
            "generations": generations,
            "seedPairs": pairs,
            "seedCount": len(pairs),
            "seedStart": pairs[0][0],
            "seedPointers": ["/mathSeed", "/libSeed"],
            "candidateMetadata": str(candidate_metadata_path),
            "mutations": mutations,
            "searchProfile": "adaptive-native" if mode == "search" else "legacy",
            "headless": True,
            "provenance": cpp_provenance,
            "executionMode": actual_mode,
            "testPurpose": test_purpose,
            "purpose": "strategy" if actual_mode == "production" else "diagnostic",
            "revision": engine["revision"],
            "policySemanticsVersion": _POLICY_VERSION,
            "operation": mode,
            "policy": policy,
        }
        if mixed_encounters:
            config['focusEncounterIds'] = focus
            config['scheduling'] = 'mixed-encounter-global-pool'
            config['searchScheduling'] = 'encounter-wave'
            config['adaptiveScope'] = 'per-encounter'
        if options.get('searchStatePath') is not None:
            config['searchStatePath'] = str(Path(options['searchStatePath']).resolve())
        if options.get('pendingSeedPolicy') is not None:
            pending_policy = options['pendingSeedPolicy']
            required = ('seedPairs', 'seedStart', 'seedCount', 'seedPointers')
            if (not isinstance(pending_policy, dict)
                    or any(key not in pending_policy for key in required)
                    or pending_policy['seedPairs'] != pairs
                    or pending_policy['seedCount'] != len(pairs)
                    or pending_policy['seedPointers'] != ['/mathSeed', '/libSeed']
                    or isinstance(pending_policy['seedStart'], bool)
                    or not isinstance(pending_policy['seedStart'], int)):
                raise ValueError('C++ pending seed policy is incompatible with exact pending seeds')
            config.update({key: pending_policy[key] for key in required})
        if isinstance(build_details.get("sourceSetSha256"), str):
            config["sourceSetSha256"] = build_details["sourceSetSha256"]
        if "statBounds" in static:
            config["statBoundsPath"] = str(static["statBounds"]["path"])
            config["statBoundsSha256"] = static["statBounds"]["sha256"]
        command = [str(engine["executable"]["path"]), str(config_path)]

    static_hashes = {name: value["sha256"] for name, value in engine["staticInputs"].items()}
    static_paths = {name: str(value["path"]) for name, value in engine["staticInputs"].items()}
    input_hashes = {
        "candidatesSha256": _hash_file(parent_path),
        "candidateMetadataSha256": _hash_file(candidate_metadata_path),
        "seedBankSha256": _hash_file(seed_path),
        "parentMetadataSha256": _hash_bytes(_canonical(parent_metadata)),
        "requestSha256": request_sha,
        "staticInputs": static_hashes,
        "kernelSha256": engine["kernel"]["sha256"],
        "executableSha256": engine["executable"]["sha256"],
    }
    if config.get('initialCheckpoint'):
        input_hashes['initialCheckpointSha256'] = _hash_file(Path(config['initialCheckpoint']))
    if source_manifest is not None:
        source_set_sha = source_manifest.get("inputSetSha256")
        input_hashes["sourceInputManifestSha256"] = _hash_file(_INPUT_MANIFEST)
        input_hashes["sourceSetSha256"] = source_set_sha
    if isinstance(build_details.get("sourceSetSha256"), str):
        input_hashes["compiledSourceSetSha256"] = build_details["sourceSetSha256"]
    if template_input is not None:
        input_hashes.update(template_input)
    if language == "go" and mode == "search" and options.get("searchStatePath") is not None:
        state_path = Path(options["searchStatePath"]).resolve()
        input_hashes["priorSearchStatePath"] = str(state_path)
        input_hashes["priorSearchStateSha256"] = _hash_file(state_path)
    if mixed_encounters:
        control_path = output_dir / 'run-control.json'
        _write_json(control_path, {'focusEncounterIds': focus, 'pauseRequested': False,
                                  'stopRequested': False})
        config['controlPath'] = str(control_path)
    _write_json(config_path, config)
    config_sha = _hash_file(config_path)
    spec = {
        **dict(options),
        "schema": "native-desktop-run-v2",
        "language": language,
        "revision": engine["revision"],
        "executablePath": str(engine["executable"]["path"]),
        "executableSha256": engine["executable"]["sha256"],
        "kernelPath": str(engine["kernel"]["path"]),
        "kernelSha256": engine["kernel"]["sha256"],
        "staticInputPaths": static_paths,
        "staticInputHashes": static_hashes,
        "parentMetadata": parent_metadata,
        "candidateCount": len(intents),
        "seedPairs": pairs,
        "seedBankPath": str(seed_path),
        "mode": mode,
        "purpose": request_payload["purpose"],
        "executionMode": actual_mode,
        "executionModeReason": production_reason,
        "policySemanticsVersion": _POLICY_VERSION,
        "policySha256": policy_sha,
        "requestSha256": request_sha,
        "inputHashes": input_hashes,
        "configPath": str(config_path),
        "configSha256": config_sha,
        "command": command,
        "directory": str(output_dir),
        "output": str(output_path),
        "total": len(pairs) * len(intents) if mode == "evaluate" else None,
        "estimatedStorageBytes": estimated_bytes,
        "verificationOnly": actual_mode != "production",
        "importDisabled": actual_mode != "production",
        "seedApplication": "exact-ordered-pairs" if mode == "evaluate" or language in {"rust", "cpp"} else "native-search-rng-from-searchSeed",
        "estimatedStorageIsProvisional": True,
        "createdUtc": datetime.now(timezone.utc).isoformat(),
    }
    if mixed_encounters:
        spec.update(encounterId=None, encounterIds=encounter_ids, focusEncounters=focus,
                    scheduling='mixed-encounter-global-pool', controlPath=str(control_path))
    _write_json(output_dir / "parent-metadata.json", parent_metadata)
    _write_json(output_dir / "request.json", request_payload)
    _write_json(output_dir / "run-spec.json", spec)
    return spec

