import type { SharedLoadoutData } from "@/lib/battle-legality";
import { draftStatRows, gearSlotForName } from "@/lib/battle-team-draft";
import type {
  CandidateBuildOption,
  CoveragePlan,
  PlayerProfile,
  RequiredUpgrade,
  SolverSettings,
  TranslationResult,
} from "@/lib/synthetic-legal-model";
import { blocksExactTargetClaim, respectsSimultaneousInventory } from "@/lib/synthetic-legal-engine";

export type CoveragePlannerInput = {
  selectedEncounterIds: number[];
  characterBudget: number;
  options: CandidateBuildOption[];
  profile: PlayerProfile;
  shared: SharedLoadoutData;
  settings: SolverSettings;
  maximumNodes?: number;
};

type Chosen = { encounterId: number; option: CandidateBuildOption };

/**
 * Select among already-recorded candidates and exact build options. This does no battle simulation
 * or candidate generation. Permanent character foundations must agree between encounters; physical
 * gear is reusable between encounters, and any final global gear level is rechecked in every build.
 */
export function planEncounterCoverage(input: CoveragePlannerInput): CoveragePlan {
  const selectedEncounterIds = [...new Set(input.selectedEncounterIds)].filter((id) => Number.isInteger(id)).sort((a, b) => a - b);
  const budget = Math.max(0, Math.min(8, Math.floor(input.characterBudget)));
  const exactOptions = new Map<number, CandidateBuildOption[]>();
  const approximateOptions = new Map<number, CandidateBuildOption[]>();
  for (const encounterId of selectedEncounterIds) {
    const options = input.options.filter((option) => option.encounterId === encounterId);
    exactOptions.set(encounterId, options.filter(isCompleteExactOption));
    approximateOptions.set(encounterId, options.filter((option) => !isCompleteExactOption(option) && option.results.length > 0));
  }

  // A fail-first order shortens the exact set-cover search without changing its objective.
  const order = [...selectedEncounterIds].sort((a, b) =>
    (exactOptions.get(a)?.length ?? 0) - (exactOptions.get(b)?.length ?? 0) || a - b);
  const maxNodes = Math.max(1, Math.min(1_000_000, Math.floor(input.maximumNodes ?? input.settings.maximumSearchNodes)));
  let nodes = 0;
  let exhausted = true;
  let best: Chosen[] = [];
  let bestKey = scoreChosen(best);
  const chosen: Chosen[] = [];

  const visit = (index: number) => {
    if (nodes >= maxNodes) {
      exhausted = false;
      return;
    }
    nodes += 1;
    if (index >= order.length) {
      const key = scoreChosen(chosen);
      if (compareScores(key, bestKey) < 0) {
        best = [...chosen];
        bestKey = key;
      }
      return;
    }
    const remaining = order.length - index;
    if (chosen.length + remaining < best.length) return;
    const encounterId = order[index];
    const currentCharacters = new Set(chosen.flatMap((entry) => entry.option.requiredCharacterIds));
    for (const option of exactOptions.get(encounterId) ?? []) {
      const newCharacters = new Set([...currentCharacters, ...option.requiredCharacterIds]);
      if (newCharacters.size > budget) continue;
      const proposed = [...chosen, { encounterId, option }];
      if (!compatiblePermanentFoundations(proposed)) continue;
      if (!allPhysicalGearCanBeAllocated(proposed, input.profile)) continue;
      if (!finalGearLevelsPreserveExactness(proposed, input)) continue;
      chosen.push({ encounterId, option });
      visit(index + 1);
      chosen.pop();
      if (!exhausted && nodes >= maxNodes) return;
    }
    // The primary objective is exact encounter count, so leaving an encounter uncovered is a branch.
    visit(index + 1);
  };
  visit(0);

  const finalizedChoices = finalizeSharedUpgrades(best, input);
  const coveredEncounterIds = finalizedChoices.map((entry) => entry.encounterId).sort((a, b) => a - b);
  const approximatedEncounterIds = selectedEncounterIds.filter((id) =>
    !coveredEncounterIds.includes(id) && (approximateOptions.get(id)?.length ?? 0) > 0);
  const uncovered = selectedEncounterIds
    .filter((id) => !coveredEncounterIds.includes(id) && !approximatedEncounterIds.includes(id))
    .map((encounterId) => ({ encounterId, reason: explainUncovered(encounterId, input.options) }));
  const reusableCharacterIds = [...new Set(finalizedChoices.flatMap((entry) => entry.option.requiredCharacterIds))];
  const plannedUnitCount = finalizedChoices.reduce((sum, entry) => sum + (entry.option.plannedUnitCount ?? entry.option.requiredUnitCount), 0);
  const declaredAvailableUnitCount = finalizedChoices.reduce((sum, entry) => sum + (entry.option.declaredAvailableUnitCount ?? 0), 0);
  const sharedUpgrades = mergeGlobalUpgrades(finalizedChoices, input.profile);
  const addedEquipmentLevels = sharedUpgrades.reduce((sum, item) => sum + item.addedLevels, 0);

  return {
    complete: exhausted,
    searchNodes: nodes,
    selectedEncounterIds,
    coveredEncounterIds,
    approximatedEncounterIds,
    uncovered,
    reusableCharacterIds,
    characterBudget: budget,
    plannedUnitCount,
    declaredAvailableUnitCount,
    choices: finalizedChoices.map((entry) => entry.option),
    sharedUpgrades,
    objective: {
      exactCovered: coveredEncounterIds.length,
      charactersUsed: reusableCharacterIds.length,
      addedEquipmentLevels,
    },
  };
}

function isCompleteExactOption(option: CandidateBuildOption): boolean {
  const plannedCount = option.plannedUnitCount ?? option.requiredUnitCount;
  const availableCount = option.declaredAvailableUnitCount ?? 0;
  if (!option.allRequiredUnitsExact || option.results.length !== plannedCount ||
      option.requiredCharacterIds.length !== plannedCount || plannedCount + availableCount !== option.requiredUnitCount ||
      option.unsupportedRequirements.length > 0) return false;
  if (option.unitPlans && (option.unitPlans.length !== option.requiredUnitCount ||
      option.unitPlans.filter((entry) => entry.status === "planned").length !== plannedCount ||
      option.unitPlans.filter((entry) => entry.status === "player-declared-available").length !== availableCount)) return false;
  if (new Set(option.requiredCharacterIds).size !== plannedCount) return false;
  return option.results.every((result) => result.exact &&
    result.runtimeVerification?.status === "verified" &&
    result.status !== "unmapped-or-unproven" && result.status !== "search-incomplete" &&
    result.unsupportedFields.length === 0 &&
    !result.legalityIssues.some(blocksExactTargetClaim));
}

function compatiblePermanentFoundations(chosen: Chosen[]): boolean {
  const foundations = new Map<string, string>();
  for (const { option } of chosen) {
    for (const result of option.results) {
      const saved = result.savedLoadout;
      const fingerprint = JSON.stringify({
        jobName: saved.jobName,
        rank: saved.rank,
        awakening: saved.awakening ?? null,
        statLevels: normalizedStatLevels(saved.statLevels),
        residentStatItems: saved.residentStatItems ?? null,
      });
      const previous = foundations.get(result.characterId);
      if (previous !== undefined && previous !== fingerprint) return false;
      foundations.set(result.characterId, fingerprint);
    }
  }
  return true;
}

function normalizedStatLevels(value: Record<string, number> | undefined): Record<string, number> {
  return Object.fromEntries(Object.entries(value ?? {}).sort(([a], [b]) => a.localeCompare(b)));
}

function allPhysicalGearCanBeAllocated(chosen: Chosen[], profile: PlayerProfile): boolean {
  for (const { option } of chosen) {
    if (!respectsSimultaneousInventory(option.results, profile)) return false;
  }
  return true;
}

function finalGearLevelsPreserveExactness(chosen: Chosen[], input: CoveragePlannerInput): boolean {
  const finalLevels = new Map<string, number>();
  for (const { option } of chosen) {
    for (const result of option.results) {
      for (const use of Object.values(result.equipmentInstances)) {
        if (!use.equipmentName || use.level === null) continue;
        finalLevels.set(use.equipmentName, Math.max(finalLevels.get(use.equipmentName) ?? 0, use.level));
      }
    }
  }
  for (const { option } of chosen) {
    for (const result of option.results) {
      const loadout = { ...result.savedLoadout, equipment: result.savedLoadout.equipment?.map((entry) => {
        const slot = gearSlotForName(entry.name, input.shared.slotAssignments);
        if (!slot) return entry;
        const use = result.equipmentInstances[slot];
        const level = use?.equipmentName ? finalLevels.get(use.equipmentName) : undefined;
        return level === undefined ? entry : { ...entry, level };
      }) };
      const actual = Object.fromEntries(draftStatRows(loadout, input.shared)
        .filter((row) => row.total !== null).map((row) => [row.stat, row.total as number]));
      for (const [stat, target] of Object.entries(result.target)) {
        if (actual[stat] !== target) return false;
      }
    }
  }
  return true;
}

function finalizeSharedUpgrades(chosen: Chosen[], input: CoveragePlannerInput): Chosen[] {
  const finalLevels = new Map<string, number>();
  for (const { option } of chosen) for (const result of option.results) {
    for (const use of Object.values(result.equipmentInstances)) if (use.equipmentName && use.level !== null) {
      finalLevels.set(use.equipmentName, Math.max(finalLevels.get(use.equipmentName) ?? 0, use.level));
    }
  }
  return chosen.map(({ encounterId, option }) => ({
    encounterId,
    option: {
      ...option,
      // The coverage result has one shared upgrade ledger. Nested encounter entries carry final levels
      // and exactness, so summing per-encounter upgrades cannot double-count a physical item.
      results: option.results.map((result) => {
        const equipment = result.savedLoadout.equipment?.map((entry) => {
          const slot = gearSlotForName(entry.name, input.shared.slotAssignments);
          if (!slot) return entry;
          const use = result.equipmentInstances[slot];
          const level = use?.equipmentName ? finalLevels.get(use.equipmentName) : undefined;
          return level === undefined ? entry : { ...entry, level };
        });
        const savedLoadout = { ...result.savedLoadout, equipment };
        const achieved = Object.fromEntries(draftStatRows(savedLoadout, input.shared)
          .filter((row) => row.total !== null).map((row) => [row.stat, row.total as number]));
        const deltas = Object.fromEntries(Object.entries(result.target).map(([stat, target]) => [stat, achieved[stat] - target]));
        return { ...result, savedLoadout, achieved, deltas, exact: Object.values(deltas).every((delta) => delta === 0), requiredUpgrades: [] };
      }),
    },
  }));
}

function mergeGlobalUpgrades(chosen: Chosen[], profile: PlayerProfile): RequiredUpgrade[] {
  const uses = new Map<string, { equipmentName: string; stackId: string; currentLevel: number; requiredLevel: number }>();
  for (const { option } of chosen) for (const result of option.results) {
    for (const upgrade of result.requiredUpgrades) {
      if (!upgrade.inventoryStackId || upgrade.currentLevel === null) continue;
      const previous = uses.get(upgrade.equipmentName);
      uses.set(upgrade.equipmentName, {
        equipmentName: upgrade.equipmentName,
        stackId: upgrade.inventoryStackId,
        currentLevel: previous?.currentLevel ?? upgrade.currentLevel,
        requiredLevel: Math.max(previous?.requiredLevel ?? 0, upgrade.requiredLevel),
      });
    }
    for (const use of Object.values(result.equipmentInstances)) {
      if (!use.stackId || !use.equipmentName || use.level === null) continue;
      const previous = uses.get(use.equipmentName);
      const stack = profile.equipment.find((entry) => entry.id === use.stackId);
      if (!stack || (stack.ownership ?? "owned") !== "owned") continue;
      const currentLevel = stack.currentLevel;
      if (use.level <= currentLevel) continue;
      uses.set(use.equipmentName, {
        equipmentName: use.equipmentName,
        stackId: use.stackId,
        currentLevel,
        requiredLevel: Math.max(previous?.requiredLevel ?? 0, use.level),
      });
    }
  }
  return [...uses.entries()].map(([, entry]) => ({
    equipmentName: entry.equipmentName,
    inventoryStackId: entry.stackId,
    instanceId: null,
    quantity: 1,
    currentLevel: entry.currentLevel,
    requiredLevel: entry.requiredLevel,
    addedLevels: Math.max(0, entry.requiredLevel - entry.currentLevel),
    cost: null,
  })).filter((entry) => entry.addedLevels > 0);
}

function explainUncovered(encounterId: number, options: CandidateBuildOption[]): string {
  const candidates = options.filter((option) => option.encounterId === encounterId);
  if (candidates.length === 0) return "No recorded optimizer candidate is available for this encounter.";
  const unsupported = candidates.flatMap((candidate) => candidate.unsupportedRequirements).find(Boolean);
  if (unsupported) return unsupported;
  if (candidates.some((candidate) => candidate.results.some((result) => result.exact))) {
    return "At least one exact unit build exists, but not every required player-controlled unit or shared foundation was satisfied.";
  }
  return "No complete exact legal build was found; nearest results remain unvalidated approximations.";
}

function scoreChosen(chosen: Chosen[]): [number, number, number, number, number] {
  const characters = new Set(chosen.flatMap((entry) => entry.option.requiredCharacterIds));
  const uniqueUpgrades = new Map<string, number>();
  let swaps = 0;
  const previousBuilds = new Map<string, string>();
  for (const { option } of chosen) for (const result of option.results) {
    for (const [slot, use] of Object.entries(result.equipmentInstances)) {
      const key = `${result.characterId}:${slot}`;
      const fingerprint = JSON.stringify([use.equipmentName, use.level]);
      const previous = previousBuilds.get(key);
      if (previous !== undefined && previous !== fingerprint) swaps += 1;
      previousBuilds.set(key, fingerprint);
    }
  }
  for (const { option } of chosen) for (const result of option.results) {
    for (const upgrade of result.requiredUpgrades) {
      uniqueUpgrades.set(upgrade.equipmentName, Math.max(uniqueUpgrades.get(upgrade.equipmentName) ?? 0, upgrade.addedLevels));
    }
  }
  const addedLevels = [...uniqueUpgrades.values()].reduce((sum, value) => sum + value, 0);
  return [-chosen.length, characters.size, uniqueUpgrades.size, addedLevels, swaps];
}

function compareScores(a: [number, number, number, number, number], b: [number, number, number, number, number]): number {
  for (let index = 0; index < a.length; index += 1) if (a[index] !== b[index]) return a[index] - b[index];
  return 0;
}
