/** Runs the existing Python combat engine in this visitor's browser, off the UI thread. */
const PYODIDE_ROOT = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const PYODIDE_MODULE = `${PYODIDE_ROOT}pyodide.mjs`;

type PythonRuntime = {
  FS: { writeFile(path: string, data: Uint8Array): void };
  globals: { set(name: string, value: unknown): void };
  runPython(source: string): unknown;
};

type BattleRequest = {
  id: number;
  action: "start" | "advance" | "use-item";
  scenarioJson?: string;
  ticks?: number;
  item?: string;
  tick?: number;
};

let runtimePromise: Promise<PythonRuntime> | null = null;

async function runtime(): Promise<PythonRuntime> {
  if (!runtimePromise) runtimePromise = (async () => {
    const module = await import(/* @vite-ignore */ PYODIDE_MODULE) as {
      loadPyodide: (options: { indexURL: string }) => Promise<PythonRuntime>;
    };
    const python = await module.loadPyodide({ indexURL: PYODIDE_ROOT });
    const bundleUrl = new URL(`${import.meta.env.BASE_URL}browser-combat.zip`, self.location.origin);
    const response = await fetch(bundleUrl);
    if (!response.ok) throw new Error(`Could not load browser combat package (${response.status}).`);
    python.FS.writeFile("/tmp/browser-combat.zip", new Uint8Array(await response.arrayBuffer()));
    python.runPython(`
import sys, zipfile
from pathlib import Path
_runtime_dir = Path('/home/pyodide/battle-runtime')
_runtime_dir.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile('/tmp/browser-combat.zip') as _archive:
    _archive.extractall(_runtime_dir)
sys.path.insert(0, str(_runtime_dir))
import json
from browser_battle_session import BrowserBattleSession
`);
    return python;
  })();
  try {
    return await runtimePromise;
  } catch (error) {
    runtimePromise = null;
    throw error;
  }
}

async function execute(request: BattleRequest): Promise<string> {
  const python = await runtime();
  if (request.action === "start") {
    if (!request.scenarioJson) throw new Error("A battle scenario is required.");
    python.globals.set("_browser_scenario_json", request.scenarioJson);
    return String(python.runPython(`
_browser_session = BrowserBattleSession(json.loads(_browser_scenario_json), window_ticks=80, checkpoint_limit=16)
json.dumps(_browser_session.start(), separators=(',', ':'))
`));
  }
  if (request.action === "advance") {
    python.globals.set("_browser_advance_ticks", Math.max(1, Math.min(200, Math.trunc(request.ticks ?? 100))));
    return String(python.runPython("json.dumps(_browser_session.advance(_browser_advance_ticks), separators=(',', ':'))"));
  }
  if (request.action === "use-item") {
    if (typeof request.item !== "string" || !Number.isInteger(request.tick)) {
      throw new Error("A valid item and displayed tick are required.");
    }
    python.globals.set("_browser_item", request.item);
    python.globals.set("_browser_tick", request.tick);
    return String(python.runPython("json.dumps(_browser_session.use_consumable(_browser_item, _browser_tick)['replay'], separators=(',', ':'))"));
  }
  throw new Error("Unknown browser combat action.");
}

let queue = Promise.resolve();
self.onmessage = ({ data }: MessageEvent<BattleRequest>) => {
  queue = queue.then(async () => {
    try {
      self.postMessage({ id: data.id, replay: await execute(data) });
    } catch (error) {
      self.postMessage({ id: data.id, error: error instanceof Error ? error.message : String(error) });
    }
  });
};
