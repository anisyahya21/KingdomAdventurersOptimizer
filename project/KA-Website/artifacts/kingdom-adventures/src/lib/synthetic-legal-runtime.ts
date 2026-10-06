import type { StatKey } from "@/game-data/stat-parameter-ids";
import { STAT_PARAMETER_IDS } from "@/game-data/stat-parameter-ids";
import type { SavedLoadout, SharedLoadoutData } from "@/lib/battle-legality";
import { battleSetupFromLoadouts } from "@/lib/battle-legality";
import { battlePreviewParameterNumbers, requestBattlePreview } from "@/lib/battle-preview";
import { battleSetupToCombatScenario } from "@/lib/battle-setup-adapter";

export type PreparedUnitCheck = {
  index: number;
  name: string;
  matched: boolean;
  stats: Record<string, { target: number; actual: number | null; delta: number | null }>;
};

export type PreparedBuildCheck = {
  status: "verified" | "mismatch" | "failed";
  message: string;
  units: PreparedUnitCheck[];
  cells: string[];
  warnings: string[];
};

/** Compare the solver proposal with the values produced by the game's load/prepare runtime path. */
export async function verifyPreparedBuilds(
  loadouts: SavedLoadout[],
  targetPoints: Array<Record<string, number>>,
  shared: SharedLoadoutData,
  encounterId: number,
): Promise<PreparedBuildCheck> {
  try {
    if (loadouts.length === 0 || loadouts.length !== targetPoints.length) {
      throw new Error("The runtime check needs one target point for every proposed human loadout.");
    }
    const conversion = battleSetupFromLoadouts(loadouts, shared, { encounterId });
    if (!conversion.setup) {
      throw new Error(conversion.issues.filter((issue) => issue.category === "ERROR").map((issue) => issue.message).join("; ") || "The loadouts could not be converted into a battle setup.");
    }
    const adapted = battleSetupToCombatScenario(conversion.setup);
    const prepared = await requestBattlePreview(adapted.scenario);
    const humans = prepared.units
      .filter((entry) => entry.side === "ally" && entry.kind === "human")
      .sort((left, right) => (left.rosterIndex ?? Number.MAX_SAFE_INTEGER) - (right.rosterIndex ?? Number.MAX_SAFE_INTEGER));
    if (humans.length !== loadouts.length || conversion.units.length !== loadouts.length) {
      throw new Error(`Runtime preparation returned ${humans.length} ally humans for ${loadouts.length} requested loadouts.`);
    }

    const units: PreparedUnitCheck[] = loadouts.map((loadout, index) => {
      const unit = humans[index];
      const stats: PreparedUnitCheck["stats"] = {};
      for (const [rawStat, target] of Object.entries(targetPoints[index])) {
        const parameterId = STAT_PARAMETER_IDS[rawStat as StatKey];
        const parameter = battlePreviewParameterNumbers(unit.parameters[String(parameterId)]);
        const actual = [10, 11, 12].includes(parameterId) ? parameter.maximum : parameter.value;
        stats[rawStat] = { target, actual, delta: actual === null ? null : actual - target };
      }
      return {
        index,
        name: unit.name,
        matched: Object.values(stats).every((entry) => entry.delta === 0),
        stats,
      };
    });
    const warnings = [
      ...conversion.issues.filter((issue) => issue.category !== "ERROR").map((issue) => `${issue.category}:${issue.code}: ${issue.message}`),
      ...adapted.warnings.map((warning) => `${warning.category}:${warning.code}: ${warning.message}`),
      ...prepared.diagnostics.map((diagnostic) => `${diagnostic.code}: ${diagnostic.message}`),
    ];
    const cells = humans.flatMap((unit) => unit.cell ? [`${unit.name}: (${unit.cell[0]}, ${unit.cell[1]})`] : []);
    const matched = units.every((unit) => unit.matched);
    return {
      status: matched ? "verified" : "mismatch",
      message: matched
        ? `Runtime preparation matched every mapped target parameter for all ${units.length} human unit(s).`
        : "Runtime preparation differs from the fast site calculation; the prepared values are authoritative.",
      units,
      cells,
      warnings,
    };
  } catch (error) {
    return {
      status: "failed",
      message: error instanceof Error ? error.message : String(error),
      units: [],
      cells: [],
      warnings: [],
    };
  }
}
