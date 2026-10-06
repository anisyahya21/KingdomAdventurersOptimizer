import {
  battleSetupToCombatScenario,
  BattleSetupAdapterError,
  type BattleSetupVisual,
} from "@/lib/battle-setup-adapter";
import {
  attachReplayVisualSetup,
  BATTLE_REPLAY_SCHEMA,
  parseBattleReplayResult,
  serializeBattleReplayResult,
  type BattleReplayResult,
} from "@/lib/battle-replay-result";
import type { BattleSetup, SetupIssue } from "@/lib/battle-setup";

/**
 * BattleSetup → authoritative simulator → generic replay — PASS 16 COMMAND 16.4.
 *
 * One callable path:
 *
 *   runBattleSetup(setup, transport)
 *     1. validates the setup and adapts it with the proven 16.3 adapter;
 *     2. hands the scenario JSON to `transport`;
 *     3. parses, checks and joins the replay payload it returns.
 *
 * Combat mechanics never run in JavaScript. The transport is the only thing that differs between
 * environments, so the same call site works everywhere:
 *
 *   * today  — the check driver (`tools/battle-setup-check/run_battle_check.mjs`) spawns
 *              `KA-Website/tools/recovery/combat_replay_export.py`, which is the authoritative
 *              wrapper around `load_scenario` → `prepare_setup` → `combat_sandbox.run_scenario`;
 *   * 16.5+  — a server route can expose that same command and use
 *              `createHttpBattleScenarioTransport(endpoint)` from the battle-setup page.
 */

export const BATTLE_SETUP_TRANSPORT_NOTE =
  "The transport runs the authoritative Python pipeline (combat_replay_export.py). " +
  "No combat iteration happens in the browser.";

export type BattleScenarioTransport = (scenarioJson: string) => Promise<string>;

export class BattleReplayTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BattleReplayTransportError";
  }
}

export type BattleSetupRun = {
  /** the validated generic replay payload, with the adapter's visual metadata joined in */
  result: BattleReplayResult;
  /** deterministic fixture text for the same payload */
  replayJson: string;
  /** the exact adapter output that was sent to the runner */
  scenarioJson: string;
  warnings: SetupIssue[];
  visualSetup: BattleSetupVisual;
};

/** HTTP transport for a future API route that exposes the same Python command. */
export function createHttpBattleScenarioTransport(
  endpoint: string,
  fetchImpl: typeof fetch = fetch,
): BattleScenarioTransport {
  return async (scenarioJson: string) => {
    const response = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: scenarioJson,
    });
    if (!response.ok) {
      throw new BattleReplayTransportError(`runner responded ${response.status} ${response.statusText}`);
    }
    return response.text();
  };
}

/**
 * Run one setup end to end. Throws `BattleSetupAdapterError` for ERROR-level setups (nothing is
 * sent to the runner) and `BattleReplayTransportError` when the returned payload does not satisfy
 * the `ka-battle-replay-1` contract.
 */
export async function runBattleSetup(
  setup: BattleSetup,
  transport: BattleScenarioTransport,
): Promise<BattleSetupRun> {
  const { scenario, warnings, visualSetup } = battleSetupToCombatScenario(setup);
  const scenarioJson = JSON.stringify(scenario);
  const replayJson = await transport(scenarioJson);

  let parsed: unknown;
  try {
    parsed = JSON.parse(replayJson);
  } catch (error) {
    throw new BattleReplayTransportError(`runner did not return JSON: ${String(error)}`);
  }
  const { result, issues } = parseBattleReplayResult(parsed);
  if (!result) {
    throw new BattleReplayTransportError(`replay payload rejected: ${issues.join("; ")}`);
  }
  if (issues.length > 0) {
    throw new BattleReplayTransportError(`replay payload is incomplete: ${issues.join("; ")}`);
  }

  const merged = attachReplayVisualSetup(result, visualSetup);
  const withWarnings: BattleReplayResult = {
    ...merged,
    setupSummary: {
      ...merged.setupSummary,
      warnings: warnings.map((issue) => `${issue.category}:${issue.code}`),
    },
  };
  return {
    result: withWarnings,
    replayJson: serializeBattleReplayResult(withWarnings),
    scenarioJson,
    warnings,
    visualSetup,
  };
}

export { BattleReplayTransportError as BattleSetupRunnerError, BATTLE_REPLAY_SCHEMA };
export type { BattleReplayResult };
