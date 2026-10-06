#!/usr/bin/env python
r"""Bounded frozen-vs-current encounter migration parity audit (evidence only).

This check answers one narrow question for an explicit migration review: on four **pre-selected**
fixtures, does the *frozen PRE-CHANGE runtime* (`...\work\encounter-redesign\legacy-source`) produce
the same compact digest / verdict / reward as the current Python engine, the current native v9
engine (telemetry off) and the current native v3 encounter engine (telemetry on)?

It is deliberately tiny and mechanical:

  * FOUR fixtures, each one ordered RNG pair, no discovery and no exploratory simulations;
  * HARD budget: 4 frozen Python + 4 current Python = 8 Python battles, and 4 native v9 + 4 native
    v3 = 8 native battles. A failed cell is reported as a gap; the budget is never extended.
  * the frozen `baseline.sqlite` is opened read-only + immutable (it is never written);
  * source provenance is taken from BOTH engines (`strategy_optimizer_adapter.provenance()`), the
    DLL file hashes are recorded, and the baseline source manifest is diffed against the live tree;
  * a missing source, a missing DLL or a non-native "native" result fails closed.

It does NOT approve semantic equivalence; it reports evidence for review (Astra). The digest covers
the legacy compact metrics only, so "digest equal" is not a claim about every unmeasured case.

Usage:

    python -B -u -X utf8 check_encounter_migration_parity.py            # full audit (16 battles)
    python -B -u -X utf8 check_encounter_migration_parity.py --json OUT # also write compact JSON
    python -B -u -X utf8 check_encounter_migration_parity.py --provenance-only   # no battles
    python -B -u -X utf8 check_encounter_migration_parity.py --engine <kind> --fixture-file F --out O
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CURRENT_RECOVERY = SCRIPT.parent
KA_ROOT = CURRENT_RECOVERY.parent.parent
REPO_ROOT = KA_ROOT.parent

WORK = Path(r"C:\Users\anisb\Documents\Codex\2026-09-28"
            r"\deepastra-handoff-community-first-optimiser-implementation"
            r"\work\encounter-redesign")
FROZEN = WORK / "legacy-source"
FROZEN_RECOVERY = FROZEN / "tools" / "recovery"
BASELINE_DB = WORK / "baseline.sqlite"
BASELINE_SOURCE_MANIFEST = WORK / "baseline-source-manifest.json"
BASELINE_DATA_MANIFEST = WORK / "baseline-data-manifest.json"
OUT_DIR = KA_ROOT / "tmp" / "encounter-redesign-20260928"
UREF = REPO_ROOT / "RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json"

WINNER_ID = "f7e6911be2a49c5744039ed1f99653845cf98bb8e075dd0ab6cf5c1d7d278e6d"
COMMUNITY_ENC0 = "063686a29344aa604d1ec8619f96bd0c0a71fa276011d5f4933bbd846bf190bd"
COMMUNITY_ENC15 = "e71e08225961dc2725af7242447c3ae5885b1a2567c61a80abc72df8875aa539"

ENGINES = ("frozen-python", "current-python", "current-native-v9", "current-native-v3")
CORE_FIELDS = ("verdict", "censored", "ticks", "prizeCallbacks", "survivors", "resourceUses",
               "healthFraction", "behavior", "seeds")
REWARD_FIELDS = ("pendingChests", "awardedChests", "awardedBasis", "inventoryVerified", "reason")

PYTHON_TIMEOUT = 600
NATIVE_TIMEOUT = 240
WALL_BUDGET = 16 * 60


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _readonly_db():
    uri = "file:" + str(BASELINE_DB).replace("\\", "/") + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


# --------------------------------------------------------------------------------------------------
# Fixtures (pre-selected; no search).
# --------------------------------------------------------------------------------------------------

def build_fixtures():
    con = _readonly_db()
    try:
        def scenario(candidate_id):
            row = con.execute("select scenario from candidate where id=?", (candidate_id,)).fetchone()
            if row is None:
                raise SystemExit(f"fixture candidate {candidate_id} not found in frozen baseline")
            return json.loads(row[0])

        winner = scenario(WINNER_ID)
        enc0 = scenario(COMMUNITY_ENC0)
        enc0["tickLimit"] = 3000
        enc15 = scenario(COMMUNITY_ENC15)
        enc15["tickLimit"] = 3000
        loss = json.loads(UREF.read_text(encoding="utf-8"))
        loss["finishPolicy"] = "on-verdict"
        loss["tickLimit"] = 12000
    finally:
        con.close()
    return [
        dict(name="enc19_win", encounter=19, candidate=WINNER_ID,
             note="real mature encounter19 winning candidate f7e6911b (Average refine hp 17179->19287); "
                  "frozen row tickLimit 30000",
             scenario=winner, seeds=[1210174508, 618222241]),
        dict(name="community_enc0", encounter=0, candidate=COMMUNITY_ENC0,
             note="Community baseline supplied candidate Vs. Kairobot Knight (Easy); tickLimit forced to 3000",
             scenario=enc0, seeds=[135791, 246802]),
        dict(name="community_enc15", encounter=15, candidate=COMMUNITY_ENC15,
             note="Community baseline supplied candidate Vs. Kairo Kommander (Extreme); tickLimit forced to 3000",
             scenario=enc15, seeds=[135791, 246802]),
        dict(name="terminal_loss", encounter=19, candidate=None,
             note="existing telemetry terminal loss fixture (UREF default scenario, finishPolicy on-verdict, "
                  "tickLimit 12000)",
             scenario=loss, seeds=[7, 8]),
    ]


# --------------------------------------------------------------------------------------------------
# Single-engine runner (invoked as a fresh subprocess so module namespaces never mix).
# --------------------------------------------------------------------------------------------------

def _engine_root(kind):
    return FROZEN_RECOVERY if kind.startswith("frozen") else CURRENT_RECOVERY


def run_engine(kind, fixture_path, out_path):
    if kind not in ENGINES:
        raise SystemExit(f"unknown engine {kind!r}")
    engine = str(_engine_root(kind))
    here = str(SCRIPT.parent)
    sys.path[:] = [engine] + [p for p in sys.path if os.path.abspath(p or ".") != os.path.abspath(here)]
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    scenario, seeds = fixture["scenario"], fixture["seeds"]
    raw_sha = hashlib.sha256(_canonical(scenario).encode("utf-8")).hexdigest()

    # The prepared frozen sibling RE-evidence is complete except for the 51 MB native ELF, which the
    # preservation copy left out (the real file lives at .../39257e72291d/inputs/libil2cpp.so). Supply
    # that one read-only input from the canonical repo root so the frozen module can attest its own
    # native build hash; no file is written and no frozen source line is changed.
    native_redirect = None
    if kind == "frozen-python":
        import combat_runtime_data  # noqa: E402
        if not (combat_runtime_data.NATIVE_DIR / "inputs/libil2cpp.so").is_file():
            canonical = REPO_ROOT / "RE-evidence/G2.1/39257e72291d"
            combat_runtime_data.NATIVE_DIR = canonical
            native_redirect = str(canonical)

    import strategy_optimizer_adapter as adapter  # noqa: E402
    normalized = adapter.validate_scenario(scenario)
    norm_sha = hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()
    started = time.perf_counter()
    identity = {}
    if kind.endswith("python"):
        if kind.startswith("frozen"):
            compact = adapter._python_simulate_compact(normalized, seeds)
        else:
            compact = adapter._python_simulate_compact(normalized, seeds, telemetry=True)
    else:
        import strategy_optimizer_native as native  # noqa: E402
        compact = native.simulate_compact(normalized, seeds, backend="native",
                                          telemetry=kind.endswith("v3"))
        identity["default"] = list(native._library_identity(native.library()))
        if kind.endswith("v3"):
            identity["encounter"] = list(native._library_identity(native.encounter_library()))
    elapsed = time.perf_counter() - started
    record = dict(engine=kind, engine_root=engine, executable=sys.executable,
                  python=sys.version.split()[0], elapsedSeconds=round(elapsed, 3),
                  rawScenarioSha256=raw_sha, normalizedScenarioSha256=norm_sha,
                  encounterId=normalized.get("encounterId"), tickLimit=normalized.get("tickLimit"),
                  units=len(normalized.get("ownUnits") or []), nativeDirRedirect=native_redirect,
                  libraryIdentity=identity,
                  result=compact)
    Path(out_path).write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    return record


def provenance_only(kind):
    engine = str(_engine_root(kind))
    here = str(SCRIPT.parent)
    sys.path[:] = [engine] + [p for p in sys.path if os.path.abspath(p or ".") != os.path.abspath(here)]
    import strategy_optimizer_adapter as adapter  # noqa: E402
    return dict(engine=kind, engine_root=engine, provenance=adapter.provenance(refresh=True))


# --------------------------------------------------------------------------------------------------
# Harness: spawn the four engines per fixture and assert parity.
# --------------------------------------------------------------------------------------------------

def _run_cell(kind, fixture, work_dir, deadline):
    out = work_dir / f"parity-audit-cell-{fixture['name']}-{kind}.json"
    if out.is_file():
        return json.loads(out.read_text(encoding="utf-8")), None
    if time.time() > deadline:
        return None, "wall budget exhausted before this cell"
    fx = work_dir / f"parity-audit-fixture-{fixture['name']}.json"
    if not fx.is_file():
        fx.write_text(json.dumps({"scenario": fixture["scenario"], "seeds": fixture["seeds"]}),
                      encoding="utf-8")
    timeout = PYTHON_TIMEOUT if kind.endswith("python") else NATIVE_TIMEOUT
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-u", "-X", "utf8", str(SCRIPT),
             "--engine", kind, "--fixture-file", str(fx), "--out", str(out)],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    if proc.returncode != 0 or not out.is_file():
        return None, f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-400:]}"
    return json.loads(out.read_text(encoding="utf-8")), None


def _core(record):
    result = record["result"]
    return {field: result.get(field) for field in CORE_FIELDS}


def _reward(record):
    outcome = record["result"].get("rewardOutcome") or {}
    return {field: outcome.get(field) for field in REWARD_FIELDS}


def _final_reward(record):
    telemetry = record["result"].get("encounterTelemetry") or {}
    return telemetry.get("finalReward")


def audit(deadline):
    fixtures = build_fixtures()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cells, gaps, assertions = {}, [], []
    for fixture in fixtures:
        cells[fixture["name"]] = {}
        for kind in ENGINES:
            record, error = _run_cell(kind, fixture, OUT_DIR, deadline)
            if error is not None:
                gaps.append(dict(fixture=fixture["name"], engine=kind, error=error))
                continue
            cells[fixture["name"]][kind] = record

    for fixture in fixtures:
        name = fixture["name"]
        available = cells[name]
        if len(available) != len(ENGINES):
            assertions.append(dict(fixture=name, check="all-engines-ran", ok=False,
                                   detail=f"{sorted(available)} of {list(ENGINES)}"))
            continue
        digests = {kind: rec["result"].get("digest") for kind, rec in available.items()}
        assertions.append(dict(fixture=name, check="digest-equal",
                               ok=len(set(digests.values())) == 1, detail=digests))
        for field in CORE_FIELDS:
            values = {kind: _core(rec)[field] for kind, rec in available.items()}
            serial = [json.dumps(v, sort_keys=True) for v in values.values()]
            assertions.append(dict(fixture=name, check=f"core:{field}",
                                   ok=len(set(serial)) == 1, detail=values))
        for field in REWARD_FIELDS:
            values = {kind: _reward(rec)[field] for kind, rec in available.items()}
            serial = [json.dumps(v, sort_keys=True) for v in values.values()]
            assertions.append(dict(fixture=name, check=f"reward:{field}",
                                   ok=len(set(serial)) == 1, detail=values))
        for kind in ("current-native-v9", "current-native-v3"):
            backend = available[kind]["result"].get("resultBackend")
            assertions.append(dict(fixture=name, check=f"native-backend:{kind}",
                                   ok=backend == "native", detail=backend))
        assertions.append(dict(fixture=name, check="v9-v3-digest-equal",
                               ok=available["current-native-v9"]["result"].get("digest")
                                  == available["current-native-v3"]["result"].get("digest"),
                               detail=None))
        # The telemetry-only reward/dispatch reading is not covered by the existing shared-scalar
        # check, so compare it between the two current observers explicitly.
        telemetry_only = {kind: available[kind]["result"].get("encounterTelemetry") or {}
                          for kind in ("current-python", "current-native-v3")}
        dispatch = {kind: (block.get("finalReward") or {}).get("dispatchedChests")
                    for kind, block in telemetry_only.items()}
        assertions.append(dict(fixture=name, check="telemetry:dispatch-python-vs-native",
                               ok=len(set(dispatch.values())) == 1, detail=dispatch))
        boundary = {kind: (block.get("finalStatus") or {}).get("finishBoundary")
                    for kind, block in telemetry_only.items()}
        assertions.append(dict(fixture=name, check="telemetry:finishBoundary-python-vs-native",
                               ok=len({json.dumps(value) for value in boundary.values()}) == 1,
                               detail=boundary))
        if name == "enc19_win":
            win = available["current-native-v3"]["result"]
            telemetry = win.get("encounterTelemetry") or {}
            reward = (telemetry.get("finalReward") or {})
            assertions.append(dict(fixture=name, check="expected-real-win",
                                   ok=win.get("verdict") == 1 and reward.get("dispatchedChests") == 176
                                      and _reward(available["current-native-v3"]).get("awardedChests") == 176,
                                   detail=dict(verdict=win.get("verdict"),
                                               dispatched=reward.get("dispatchedChests"),
                                               awarded=_reward(available["current-native-v3"]).get("awardedChests"))))
        if name == "terminal_loss":
            loss = _core(available["current-native-v3"])
            reward = _reward(available["current-native-v3"])
            assertions.append(dict(fixture=name, check="expected-terminal-loss",
                                   ok=loss["verdict"] == 2 and loss["censored"] is False
                                      and reward.get("awardedChests") == 0,
                                   detail=dict(verdict=loss["verdict"], censored=loss["censored"],
                                               awarded=reward.get("awardedChests"))))
    return fixtures, cells, gaps, assertions


# --------------------------------------------------------------------------------------------------
# Source / DLL provenance.
# --------------------------------------------------------------------------------------------------

def source_diff():
    manifest = json.loads(BASELINE_SOURCE_MANIFEST.read_text(encoding="utf-8"))
    rows, changed, missing = {}, [], []
    for rel, frozen_sha in manifest.items():
        rel_posix = rel.replace("\\", "/")
        frozen_path = FROZEN / rel_posix
        current_path = KA_ROOT / rel_posix
        current_sha = _sha256_file(current_path) if current_path.is_file() else None
        frozen_actual = _sha256_file(frozen_path) if frozen_path.is_file() else None
        rows[rel_posix] = dict(frozenExpected=frozen_sha, frozenActual=frozen_actual,
                               currentActual=current_sha)
        if current_sha is None:
            missing.append(rel_posix)
        elif current_sha != frozen_sha:
            changed.append(dict(path=rel_posix, frozen=frozen_sha, current=current_sha))
    return dict(count=len(manifest), changedCount=len(changed), missingCount=len(missing),
                changed=changed, missing=missing, files=rows)


def dll_hashes():
    current_release = CURRENT_RECOVERY / "native/ka_kernel/target/release"
    frozen_release = FROZEN_RECOVERY / "native/ka_kernel/target/release"
    backup = KA_ROOT / "tmp/encounter-redesign-20260928/build/dll-backup/ka_kernel_v9.dll"
    names = ("ka_kernel_v9.dll", "ka_kernel_v9_large.dll",
             "ka_kernel_encounter_v3.dll", "ka_kernel_encounter_v3_large.dll")
    out = {"current": {}, "frozen": {}}
    for name in names:
        path = current_release / name
        out["current"][name] = _sha256_file(path) if path.is_file() else None
        fpath = frozen_release / name
        out["frozen"][name] = _sha256_file(fpath) if fpath.is_file() else None
    out["dllBackupV9"] = _sha256_file(backup) if backup.is_file() else None
    expected = {}
    if BASELINE_DATA_MANIFEST.is_file():
        data = json.loads(BASELINE_DATA_MANIFEST.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.replace("\\", "/").endswith("ka_kernel_v9.dll"):
                expected[key.replace("\\", "/")] = value
    out["previousReportExpectedV9"] = expected
    out["v9Unchanged"] = (
        out["current"].get("ka_kernel_v9.dll") is not None
        and out["current"].get("ka_kernel_v9.dll") == out["frozen"].get("ka_kernel_v9.dll")
        and out["current"].get("ka_kernel_v9.dll") == out["dllBackupV9"]
        and all(value == out["current"].get("ka_kernel_v9.dll") for value in expected.values())
    )
    return out


def runtime_data_diff():
    data_files = ("weapon-skill-profiles.json", "encounters.json", "formation-rules.json",
                  "skill-combat-constants.json")
    tables = ("Monster.txt", "Treasure.txt")
    result = {}
    for label, root in (("current", REPO_ROOT), ("frozen", WORK)):
        entry = {}
        for name in data_files:
            path = root / "RE-evidence/20260912-combat" / name
            entry[f"data/{name}"] = _sha256_file(path) if path.is_file() else None
        for name in tables:
            path = root / "RE-evidence/20260911-treasure/xls-original/English.lproj" / name
            entry[f"table/{name}"] = _sha256_file(path) if path.is_file() else None
        result[label] = entry
    result["identical"] = result["current"] == result["frozen"] and all(result["current"].values())
    return result


def write_report(path, payload):
    lines = ["# Encounter migration parity audit (frozen vs current)", "",
             f"- wall: {payload['wallSeconds']:.1f}s; python battles: {payload['pythonBattles']}; "
             f"native battles: {payload['nativeBattles']}; gaps: {payload['gapCount']}",
             f"- parity assertions: {payload['assertionPass']} pass / {payload['assertionFail']} fail",
             f"- v9 unchanged across frozen/current/backup/previous-report: {payload['dll']['v9Unchanged']}",
             f"- runtime data identical (current vs frozen sibling): {payload['runtimeData']['identical']}",
             f"- source files changed vs baseline manifest: {payload['sourceDiff']['changedCount']}",
             "", "## Fixtures", ""]
    for fx in payload["fixtures"]:
        lines.append(f"- `{fx['name']}` (encounter {fx['encounter']}, seeds {fx['seeds']}): {fx['note']}")
    lines += ["", "## Per-fixture digests", ""]
    for name, engines in payload["cells"].items():
        digests = {kind: (rec["result"].get("digest") or "")[:16] for kind, rec in engines.items()}
        lines.append(f"- `{name}`: {digests}")
    core = [item for item in payload["assertions"]
            if item["check"].startswith(("digest", "core:", "reward:", "native-backend",
                                         "v9-v3", "all-engines", "expected"))]
    core_fail = [item for item in core if not item["ok"]]
    lines += ["", "## Core parity (digest / verdict / reward entitlement)", "",
              f"- {len(core) - len(core_fail)}/{len(core)} core checks pass"]
    lines += ["", "## Telemetry-only readings (current Python vs current native v3)", ""]
    telemetry = [item for item in payload["assertions"] if item["check"].startswith("telemetry:")]
    for item in telemetry:
        lines.append(f"- {'OK' if item['ok'] else 'DIVERGES'} `{item['fixture']}` "
                     f"{item['check']}: {item['detail']}")
    lines += ["", "## Other failures / gaps", ""]
    for item in payload["assertions"]:
        if not item["ok"] and not item["check"].startswith("telemetry:"):
            lines.append(f"- FAIL `{item['fixture']}` {item['check']}: {item['detail']}")
    for gap in payload["gaps"]:
        lines.append(f"- GAP `{gap['fixture']}` {gap['engine']}: {gap['error']}")
    if (not payload["assertions"]
            or all(item["ok"] or item["check"].startswith("telemetry:")
                   for item in payload["assertions"])) and not payload["gaps"]:
        lines.append("- none")
    lines += ["", "## Limits", "",
              "- The digest covers the legacy compact metrics only (verdict/censored/ticks/prizes/",
              "  survivors/resources/health/behavior/seeds). Equality here is not semantic equivalence",
              "  and does not cover unmeasured behaviour outside these four fixtures.",
              "- Frozen native was NOT run: the 8-native budget is the current v9/v3 pair only.",
              "- Native cells fail closed on `resultBackend != 'native'`.",
              "- Two telemetry-only readings diverge between the current Python observer and the",
              "  current native v3 kernel: the resolved-loss finish dispatch count and the finish",
              "  boundary availability. These are new observations, not frozen-vs-current differences.",
              "- The frozen sibling's 51 MB native ELF was absent; frozen runs redirected only that",
              "  read-only input to the canonical repo evidence (recorded as nativeDirRedirect)."]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--provenance-only", action="store_true")
    parser.add_argument("--engine", choices=ENGINES)
    parser.add_argument("--provenance-out", type=Path)
    parser.add_argument("--fixture-file", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if args.engine:
        if args.provenance_out:
            record = provenance_only(args.engine)
            Path(args.provenance_out).write_text(json.dumps(record, indent=1, sort_keys=True),
                                                 encoding="utf-8")
            return 0
        if not args.fixture_file or not args.out:
            raise SystemExit("--engine needs --fixture-file and --out")
        run_engine(args.engine, args.fixture_file, args.out)
        return 0

    if args.provenance_only:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        engines = {}
        for kind in ("frozen-python", "current-python"):
            out = OUT_DIR / f"parity-audit-provenance-{kind}.json"
            proc = subprocess.run([sys.executable, "-B", "-u", "-X", "utf8", str(SCRIPT),
                                   "--engine", kind, "--provenance-out", str(out)],
                                  capture_output=True, text=True, timeout=300)
            if proc.returncode != 0 or not out.is_file():
                raise SystemExit(f"provenance failed for {kind}: {proc.stderr.strip()[-500:]}")
            engines[kind] = json.loads(out.read_text(encoding="utf-8"))
        report = dict(engines=engines, dll=dll_hashes(), runtimeData=runtime_data_diff(),
                      sourceDiff={k: v for k, v in source_diff().items() if k != "files"})
        (OUT_DIR / "parity-audit-provenance.json").write_text(
            json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        print("provenance digests:",
              {kind: rec["provenance"]["digest"] for kind, rec in engines.items()})
        print("v9 unchanged:", report["dll"]["v9Unchanged"],
              "runtime data identical:", report["runtimeData"]["identical"],
              "changed source files:", report["sourceDiff"]["changedCount"])
        return 0

    started = time.time()
    deadline = started + WALL_BUDGET
    fixtures, cells, gaps, assertions = audit(deadline)
    diff = source_diff()
    source_files = diff.pop("files")
    changed_paths = {row["path"] for row in diff["changed"]}
    relevant = {path: row for path, row in source_files.items()
                if path in changed_paths or path.endswith("strategy_optimizer_adapter.py")
                or path.endswith("strategy_optimizer_native.py")}
    payload = dict(
        generatedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        wallSeconds=round(time.time() - started, 1),
        pythonBattles=sum(1 for name in cells for kind in cells[name] if kind.endswith("python")),
        nativeBattles=sum(1 for name in cells for kind in cells[name] if kind.startswith("current-native")),
        gapCount=len(gaps),
        assertionPass=sum(1 for item in assertions if item["ok"]),
        assertionFail=sum(1 for item in assertions if not item["ok"]),
        fixtures=[{k: fx[k] for k in ("name", "encounter", "candidate", "note", "seeds")}
                  for fx in fixtures],
        scenarioCanonical={fx["name"]: _canonical(fx["scenario"]) for fx in fixtures},
        cells=cells, gaps=gaps, assertions=assertions,
        dll=dll_hashes(), runtimeData=runtime_data_diff(), sourceDiff=diff,
        relevantSourceHashes=relevant,
        limits=["digest/verdict/reward agreement only, not semantic equivalence",
                "four pre-selected fixtures; no untested case is covered",
                "frozen native not run (budget = current v9/v3 only)"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "parity-audit.json").write_text(json.dumps(payload, indent=1, sort_keys=True),
                                               encoding="utf-8")
    (OUT_DIR / "parity-audit-source-diff.json").write_text(
        json.dumps(source_files, indent=1, sort_keys=True), encoding="utf-8")
    write_report(OUT_DIR / "parity-audit-report.md", payload)
    print(f"parity audit: {payload['assertionPass']} pass / {payload['assertionFail']} fail, "
          f"{payload['gapCount']} gaps, {payload['pythonBattles']} python + "
          f"{payload['nativeBattles']} native battles in {payload['wallSeconds']}s")
    for item in assertions:
        if not item["ok"]:
            print(f"  FAIL {item['fixture']} {item['check']}: {item['detail']}")
    for gap in gaps:
        print(f"  GAP {gap['fixture']} {gap['engine']}: {gap['error']}")
    return 1 if gaps or any(not item["ok"] for item in assertions) else 0


if __name__ == "__main__":
    sys.exit(main())
