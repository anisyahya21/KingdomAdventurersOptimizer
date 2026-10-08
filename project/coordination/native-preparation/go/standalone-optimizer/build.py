"""Build the corrected Go optimizer into an ignored local cache; no tests or battles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".build-cache"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    result = {p.name: sha(p) for p in sorted(HERE.glob("*.go"))}
    for name in ("go.mod", "native_search_bounds.json", "fixed_formation_policy.json"):
        path = HERE / name
        if path.is_file():
            result[name] = sha(path)
    return result


parser = argparse.ArgumentParser(description="Compile the corrected Go source into .build-cache; no tests or battles.")
parser.add_argument("--go", help="path to Go executable; otherwise GO_EXE or PATH is used")
parser.add_argument("--output", default=".build-cache/go-optimizer-corrected.exe", help="output path relative to this source directory")
parser.add_argument("-p", "--parallel", type=int, default=6)
args = parser.parse_args()
if args.parallel < 1:
    parser.error("parallel must be at least 1")
go_exe = args.go or os.environ.get("GO_EXE") or shutil.which("go")
if not go_exe:
    parser.error("Go was not found; pass --go PATH or set GO_EXE")
go_path = Path(go_exe).resolve()
if not go_path.is_file():
    parser.error("Go executable does not exist")

relative_output = Path(args.output)
if relative_output.is_absolute() or ".." in relative_output.parts:
    parser.error("output must stay inside the standalone optimizer directory")
executable = HERE / relative_output
manifest_path = CACHE / "build-manifest.json"
executable.parent.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

sources = source_hashes()
revision = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
env = dict(os.environ)
env.pop("GOROOT", None)
env["GOTOOLCHAIN"] = "local"
env["GOCACHE"] = str(CACHE / "go-cache")
env["GOMAXPROCS"] = str(args.parallel)
command = [str(go_path), "build", "-p", str(args.parallel), "-ldflags", "-X main.buildRevision=" + revision,
           "-o", str(executable), "."]
completed = subprocess.run(command, cwd=HERE, env=env, capture_output=True, text=True)
changed_during_build = source_hashes() != sources
manifest = {
    "schema": "ka-go-build-2",
    "sourceRevision": revision,
    "sources": sources,
    "goVersion": subprocess.run([str(go_path), "version"], capture_output=True, text=True, check=False).stdout.strip(),
    "command": ["go", "build", "-p", str(args.parallel), "-ldflags", "-X main.buildRevision=" + revision,
                "-o", relative_output.as_posix(), "."],
    "exitCode": completed.returncode,
    "stdout": completed.stdout,
    "stderr": completed.stderr,
    "testsExecuted": False,
    "sourceChangedDuringBuild": changed_during_build,
    "executablePath": relative_output.as_posix(),
}
if completed.returncode == 0 and not changed_during_build:
    manifest["executableSHA256"] = sha(executable)
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in manifest.items() if k != "sources"}, indent=2))
raise SystemExit(completed.returncode or int(changed_during_build))
