import {
  planWairoReplenishRuns,
  type Difficulty,
  type WairoCollectSource,
  type WairoReplenishPlan,
} from "@/lib/farming-calc";

export type WairoRouteWorkerInput = {
  currentEnergy: number;
  maxEnergy: number;
  mines: number;
  energyStorehouses: number;
  hgEnergyStorehouses: number;
  targetDifficulty: Difficulty;
  refillItems: number;
  collectSources: WairoCollectSource[];
};

type RequestMessage = {
  id: number;
  input: WairoRouteWorkerInput;
};

export type WairoRouteWorkerResponse =
  | {
      id: number;
      ok: true;
      plan: WairoReplenishPlan;
    }
  | {
      id: number;
      ok: false;
      error: string;
    };

self.onmessage = (event: MessageEvent<RequestMessage>) => {
  const { id, input } = event.data;
  try {
    const plan = planWairoReplenishRuns(input);
    self.postMessage({ id, ok: true, plan } satisfies WairoRouteWorkerResponse);
  } catch (error) {
    self.postMessage({
      id,
      ok: false,
      error: error instanceof Error ? error.message : "Unable to calculate route",
    } satisfies WairoRouteWorkerResponse);
  }
};
