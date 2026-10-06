/** One local Python combat session, kept alive across the builder → replay route change. */
type WorkerReply = { id: number; replay?: string; error?: string };

let worker: Worker | null = null;
let nextId = 0;
const pending = new Map<number, { resolve: (value: string) => void; reject: (error: Error) => void }>();
let active = false;

function sessionWorker(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL("../workers/browser-battle.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = ({ data }: MessageEvent<WorkerReply>) => {
    const request = pending.get(data.id);
    if (!request) return;
    pending.delete(data.id);
    if (data.error) request.reject(new Error(data.error));
    else if (data.replay) request.resolve(data.replay);
    else request.reject(new Error("The browser combat worker returned no replay."));
  };
  worker.onerror = (event) => {
    const error = new Error(event.message || "The browser combat worker failed.");
    for (const request of pending.values()) request.reject(error);
    pending.clear();
    active = false;
    worker?.terminate();
    worker = null;
  };
  return worker;
}

function request(action: "start" | "advance" | "use-item", payload: Record<string, unknown>): Promise<string> {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    sessionWorker().postMessage({ id, action, ...payload });
  });
}

export async function startBrowserBattle(scenarioJson: string): Promise<string> {
  active = false;
  const replay = await request("start", { scenarioJson });
  active = true;
  return replay;
}

export function browserBattleActive(): boolean {
  return active;
}

export function advanceBrowserBattle(ticks = 100): Promise<string> {
  return request("advance", { ticks });
}

export function useBrowserBattleItem(item: string, tick: number): Promise<string> {
  return request("use-item", { item, tick });
}
