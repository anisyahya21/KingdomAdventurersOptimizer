import { battleSetupToCombatScenario, type CombatScenario } from "@/lib/battle-setup-adapter";
import type { BattleSetup } from "@/lib/battle-setup";
import type { BattleInput } from "@/lib/battle-setup";
import type { BattleReplayResult } from "@/lib/battle-replay-result";
import type { GeneratedBattleRecord } from "@/lib/generated-battle-store";

// Keep these aligned with tools/recovery/strategy_optimizer_adapter.py's import limits.
const MAX_UNITS = 32;
const MAX_TICK_LIMIT = 30_000;
const MAX_SCENARIO_BYTES = 262_144;

/** Produce the desktop optimizer's existing Import Build format. */
export function optimizerScenarioFromSetup(setup: BattleSetup): CombatScenario {
  const { scenario } = battleSetupToCombatScenario(setup);
  const totalUnits = scenario.ownUnits.length + Object.values(scenario.housePets ?? {}).reduce((count, pets) => count + pets.length, 0);
  if (totalUnits > MAX_UNITS) throw new Error(`The optimizer accepts up to ${MAX_UNITS} characters and pets; this team has ${totalUnits}.`);
  if (scenario.tickLimit > MAX_TICK_LIMIT) throw new Error(`The optimizer accepts up to ${MAX_TICK_LIMIT / 20} battle seconds. Lower the time limit before exporting.`);
  if (new TextEncoder().encode(JSON.stringify(scenario)).length > MAX_SCENARIO_BYTES) {
    throw new Error("The optimizer accepts scenario files up to 256 KiB; this team is larger.");
  }
  return scenario;
}

export function optimizerScenarioFilename(name: string): string {
  const safe = name.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, "-").replace(/\s+/g, "-").replace(/[. -]+$/g, "").slice(0, 64);
  return `${safe || "team"}.scenario.json`;
}

/** Archive the displayed fight and give Import Build its exact setup and consumable schedule. */
export function optimizerFightExport(
  scenario: CombatScenario,
  replay: BattleReplayResult,
  record: GeneratedBattleRecord,
): { schema: "ka-combat-fight-export-1"; scenario: CombatScenario; replay: BattleReplayResult; visualSetup?: GeneratedBattleRecord["visualSetup"]; summary: GeneratedBattleRecord["summary"] } {
  if (replay.finalState.windowed) throw new Error("Wait for the whole fight to finish simulating before downloading it.");
  const inputs: BattleInput[] = [...scenario.inputs];
  const key = (input: BattleInput) => `${input.tick}|${input.phase}|${input.type}|${input.type === "item" ? input.item : ""}`;
  const existing = new Map<string, number>();
  for (const input of inputs) existing.set(key(input), (existing.get(key(input)) ?? 0) + 1);
  const observed = [...(replay.holyHerbUses ?? []), ...(replay.itemUses ?? [])];
  const seen = new Map<string, number>();
  for (const use of observed) {
    if (!Number.isInteger(use.tick) || (use.tick ?? -1) < 0 || (use.phase !== "before_fighters" && use.phase !== "after_fighters")) {
      throw new Error("The replay has a consumable use without a valid tick or phase.");
    }
    const input: BattleInput = use.item === "holy_herb"
      ? { tick: use.tick!, phase: use.phase, type: "holy_herb" }
      : { tick: use.tick!, phase: use.phase, type: "item", item: use.item ?? "", target: "all" };
    if (input.type === "item" && !input.item) throw new Error("The replay has an unnamed consumable use.");
    const inputKey = key(input);
    const count = (seen.get(inputKey) ?? 0) + 1;
    seen.set(inputKey, count);
    if (count > (existing.get(inputKey) ?? 0)) inputs.push(input);
  }
  const completeScenario = { ...scenario, inputs };
  if (new TextEncoder().encode(JSON.stringify(completeScenario)).length > MAX_SCENARIO_BYTES) {
    throw new Error("This fight's setup exceeds the optimizer's 256 KiB import limit.");
  }
  return {
    schema: "ka-combat-fight-export-1",
    scenario: completeScenario,
    replay,
    ...(record.visualSetup ? { visualSetup: record.visualSetup } : {}),
    summary: record.summary,
  };
}
