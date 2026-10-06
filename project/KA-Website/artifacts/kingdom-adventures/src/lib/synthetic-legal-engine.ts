import { STAT_KEYS, type StatKey } from "@/game-data/stat-parameter-ids";
import { RESIDENT_STAT_ITEMS } from "@/game-data/resident-stat-items";
import { ENCOUNTER_BY_ID, renderSkillName, SKILL_BY_ID, skillActivationStatus, type EquipmentSlotKey } from "@/lib/battle-setup";
import { canPickHumanSkill } from "@/lib/battle-picker-rules";
import { SYNTHETIC_LEGAL_EQUIPMENT_CATALOG } from "@/lib/synthetic-legal-inventory";
import { battleSetupFromLoadouts, jobCurveFor, type SavedLoadout, type SharedLoadoutData } from "@/lib/battle-legality";
import { draftStatRows, gearSlotForName } from "@/lib/battle-team-draft";
import type {
  BuildLock,
  OptimizerEvidence,
  OptimizerTargetUnit,
  PlayerCharacter,
  PlayerProfile,
  RequiredUpgrade,
  SolverSettings,
  TranslationResult,
} from "@/lib/synthetic-legal-model";

const SLOTS: EquipmentSlotKey[] = ["weapon", "shield", "head", "body", "accessory"];
const BATTLE_INVOCATION_SKILLS = new Set([...SKILL_BY_ID.values()]
  .filter((entry) => skillActivationStatus(entry.id) === "active")
  .map(renderSkillName));
const GEAR_LEVEL_CAP = 99;
const WATER_STAT_KEYS: Partial<Record<StatKey, string>> = {
  hp: "life", mp: "wisdom", vig: "vitality", atk: "might", def: "resilience",
};

export type LegalBuildSearchRequest = {
  profile: PlayerProfile;
  characterId: string;
  jobName: string;
  rank: string;
  target: OptimizerTargetUnit;
  evidence: OptimizerEvidence;
  shared: SharedLoadoutData;
  settings: SolverSettings;
  locks?: BuildLock[];
  encounterId?: number;
  collectFeasibleOptions?: boolean;
};

export type LegalBuildSearchResult = {
  result: TranslationResult | null;
  exactFeasible: boolean | null;
  complete: boolean;
  nodes: number;
  /** Options present in at least one complete, legal exact solution. */
  feasibleOptions?: Record<string, unknown[]>;
  /** Exact stat solutions exist for these options, but a rule or required target field is unresolved. */
  unknownOptions?: Record<string, unknown[]>;
  reason?: string;
};

type EquipmentOption = {
  slot: EquipmentSlotKey;
  equipmentName: string | null;
  level: number | null;
  stackId: string | null;
  instanceId: string | null;
  owned: boolean;
  currentLevel: number | null;
};

type CandidateBuild = {
  loadout: SavedLoadout;
  equipment: EquipmentOption[];
  stats: Record<string, number>;
  deltas: Record<string, number>;
  exact: boolean;
  distance: number;
  upgrades: RequiredUpgrade[];
  owned: boolean;
  issues: string[];
  unsupported: Array<{ field: string; reason: string }>;
};

/** Whether a conversion issue prevents an exact claim for the selected target stats. */
export function blocksExactTargetClaim(issue: string): boolean {
  if (issue.startsWith("ERROR:")) return true;
  return issue.startsWith("UNKNOWN_NATIVE_RULE:") &&
    !issue.startsWith("UNKNOWN_NATIVE_RULE:RESIDENT_VALUABLE_ITEM_NOT_CAPTURED:") &&
    !issue.startsWith("UNKNOWN_NATIVE_RULE:JOB_EQUIPMENT_COMPATIBILITY_UNKNOWN:");
}

/**
 * One finite-domain solver powers recommendations and option checks. Locks are applied as predicates
 * on complete candidates, so adding locks in a different UI order cannot change the feasible set.
 * If the configured node bound is reached, infeasibility is unknown and is never reported as false.
 */
export function searchLegalBuild(request: LegalBuildSearchRequest): LegalBuildSearchResult {
  const character = request.profile.characters.find((entry) => entry.id === request.characterId);
  const anchorError = validateAnchor(request, character);
  if (anchorError) return { result: null, exactFeasible: false, complete: true, nodes: 0, reason: anchorError };
  if (Object.keys(request.target.exactPoint).length === 0) {
    return { result: null, exactFeasible: null, complete: false, nodes: 0, reason: "The selected optimizer unit has no mapped exact stat point." };
  }
  const missingValuables = Object.keys(request.target.exactPoint).flatMap((key) => {
    const waterKey = WATER_STAT_KEYS[key as StatKey];
    return waterKey && request.profile.valuables.active[waterKey] == null ? [waterKey] : [];
  });
  if (missingValuables.length > 0) {
    return {
      result: null, exactFeasible: null, complete: false, nodes: 0,
      reason: `Set the global active counts for ${[...new Set(missingValuables)].join(", ")} before evaluating exact builds. Active counts are separate from remaining inventory.`,
    };
  }
  const baseLoadout = profileLoadout(request, character!);
  const domains = Object.fromEntries(SLOTS.map((slot) => [slot, equipmentOptions(request, slot)])) as Record<EquipmentSlotKey, EquipmentOption[]>;
  const lockMap = normalizeLocks(request.locks ?? []);
  if (lockMap.jobName !== undefined && lockMap.jobName !== request.jobName) {
    return { result: null, exactFeasible: false, complete: true, nodes: 0, reason: "The locked job conflicts with the selected job anchor." };
  }
  if (lockMap.rank !== undefined && lockMap.rank !== request.rank) {
    return { result: null, exactFeasible: false, complete: true, nodes: 0, reason: "The locked rank conflicts with the selected rank anchor." };
  }
  for (const slot of SLOTS) {
    const fieldBase = `equipment.${slot}.`;
    const requiredName = lockMap[`${fieldBase}name`];
    const requiredLevel = lockMap[`${fieldBase}level`];
    const requiredInstance = lockMap[`${fieldBase}instanceId`];
    domains[slot] = domains[slot].filter((option) =>
      (requiredName === undefined || requiredName === option.equipmentName) &&
      (requiredLevel === undefined || requiredLevel === option.level) &&
      (requiredInstance === undefined || requiredInstance === option.instanceId));
  }
  if (SLOTS.some((slot) => domains[slot].length === 0)) {
    return { result: null, exactFeasible: false, complete: true, nodes: 0, reason: "At least one locked equipment choice is outside the selected inventory/settings domain." };
  }

  const skillOptions = skillOrderOptions(request, character!);
  const requiredSkills = lockMap.skills;
  const filteredSkills = requiredSkills === undefined
    ? skillOptions
    : skillOptions.filter((skills) => JSON.stringify(skills) === JSON.stringify(requiredSkills));
  if (filteredSkills.length === 0) {
    const targetSkills = request.target.controllable.find((entry) => entry.field === "skills")?.value;
    const hasRequiredOptimizerSkills = Array.isArray(targetSkills) && targetSkills.length > 0;
    return { result: null, exactFeasible: false, complete: true, nodes: 0, reason: hasRequiredOptimizerSkills
      ? "The optimizer's required skill order includes a skill that is not unlocked globally or cannot be equipped by this Job."
      : "The locked skill order is not available in the profile/target choices." };
  }

  const nodeLimit = Math.max(1, Math.min(2_000_000, Math.floor(request.settings.maximumSearchNodes)));
  const visited: EquipmentOption[] = [];
  let nodes = 0;
  let stoppedAtBound = false;
  const builds: { exact: CandidateBuild | null; unknownExact: CandidateBuild | null; nearest: CandidateBuild | null } = {
    exact: null, unknownExact: null, nearest: null,
  };
  const feasibleOptionSets = new Map<string, Set<unknown>>();
  const unknownOptionSets = new Map<string, Set<unknown>>();
  const collectOptions = request.collectFeasibleOptions === true;
  const statLocks = Object.fromEntries(STAT_KEYS.map((key) => [key, lockMap[`statLevels.${key}`]])) as Partial<Record<StatKey, unknown>>;

  const visit = (slotIndex: number) => {
    if ((!collectOptions && builds.exact) || stoppedAtBound) return;
    if (slotIndex < SLOTS.length) {
      for (const option of domains[SLOTS[slotIndex]]) {
        if (stoppedAtBound || (!collectOptions && builds.exact)) break;
        nodes += 1;
        if (nodes > nodeLimit) {
          stoppedAtBound = true;
          break;
        }
        visited.push(option);
        visit(slotIndex + 1);
        visited.pop();
      }
      return;
    }
    nodes += 1;
    if (nodes > nodeLimit) {
      stoppedAtBound = true;
      return;
    }
    for (const skills of filteredSkills) {
      const candidate = evaluateEquipmentCombination(request, character!, baseLoadout, [...visited], skills, statLocks, lockMap);
      if (!candidate || !matchesRemainingLocks(candidate.loadout, candidate.equipment, lockMap)) continue;
      if (!builds.nearest || candidate.distance < builds.nearest.distance) builds.nearest = candidate;
      if (candidate.exact) {
        if (isProvenExactCandidate(candidate, request)) {
          builds.exact ??= candidate;
          if (collectOptions) collectFeasibleValues(feasibleOptionSets, candidate, request, character!);
          else break;
        } else {
          builds.unknownExact ??= candidate;
          if (collectOptions) collectFeasibleValues(unknownOptionSets, candidate, request, character!);
        }
      }
    }
  };
  visit(0);

  const exactBuild = builds.exact;
  const unknownExactBuild = builds.unknownExact;
  const selected = exactBuild ?? unknownExactBuild ?? (request.settings.targetPolicy === "exact" ? null : builds.nearest);
  const complete = !stoppedAtBound;
  const exactFeasible = exactBuild !== null ? true : unknownExactBuild !== null || stoppedAtBound ? null : false;
  if (!selected) {
    return {
      result: null,
      exactFeasible,
      complete: !stoppedAtBound,
      nodes: Math.min(nodes, nodeLimit),
      ...(collectOptions ? { feasibleOptions: serializeFeasibleOptions(feasibleOptionSets) } : {}),
      ...(collectOptions ? { unknownOptions: serializeFeasibleOptions(unknownOptionSets) } : {}),
      reason: stoppedAtBound ? "The search limit was reached before feasibility could be proved." : "No legal complete build satisfies all current locks.",
    };
  }
  const identityUnmapped = request.target.unmapped.filter((entry) =>
    entry.field === "characterIdentity" || entry.field === "jobName/rank");
  const unsupportedFields = [
    ...selected.unsupported,
    ...request.target.unmapped.filter((entry) => !identityUnmapped.includes(entry))
      .map((entry) => ({ field: entry.field, reason: entry.reason })),
  ];
  const unproven = !isProvenExactCandidate(selected, request) || unsupportedFields.length > 0;
  const hasUpgrades = selected.upgrades.length > 0;
  const theoretical = !selected.owned;
  const status = exactBuild === null && unknownExactBuild !== null
    ? "unmapped-or-unproven"
    : exactBuild === null && !complete
    ? "search-incomplete"
    : unproven
      ? "unmapped-or-unproven"
      : theoretical
        ? "theoretical-unowned"
        : selected.exact
          ? hasUpgrades ? "exact-owned-upgrades" : "exact-owned-now"
          : hasUpgrades ? "nearest-with-upgrades" : "nearest-owned-now";
  const result: TranslationResult = {
    characterId: request.characterId,
    status,
    savedLoadout: selected.loadout,
    target: request.target.exactPoint as Record<string, number>,
    achieved: selected.stats,
    deltas: selected.deltas,
    solverExact: selected.exact,
    exact: false,
    ...(selected.exact ? { runtimeVerification: { status: "failed" as const, message: "The authoritative runtime preview has not completed." } } : {}),
    owned: selected.owned,
    requiredUpgrades: selected.upgrades,
    equipmentInstances: Object.fromEntries(selected.equipment.map((option) => [option.slot, {
      stackId: option.stackId,
      instanceId: option.instanceId,
      equipmentName: option.equipmentName,
      level: option.level,
    }])),
    unsupportedFields,
    sourceUnmappedFields: identityUnmapped.map((entry) => ({ field: entry.field, reason: entry.reason })),
    legalityIssues: selected.issues,
    evidence: request.evidence,
    search: {
      complete,
      nodes: Math.min(nodes, nodeLimit),
      ...(!complete ? { reason: "The node limit was reached; exact infeasibility and nearest optimality are unproven." } : {}),
    },
  };
  return {
    result,
    exactFeasible,
    complete,
    nodes: Math.min(nodes, nodeLimit),
    ...(collectOptions ? { feasibleOptions: serializeFeasibleOptions(feasibleOptionSets) } : {}),
    ...(collectOptions ? { unknownOptions: serializeFeasibleOptions(unknownOptionSets) } : {}),
  };
}

function isProvenExactCandidate(candidate: CandidateBuild, request: LegalBuildSearchRequest): boolean {
  const sourceUnknown = request.target.unmapped.some((entry) =>
    entry.field !== "characterIdentity" && entry.field !== "jobName/rank");
  return candidate.unsupported.length === 0 && !sourceUnknown &&
    !candidate.issues.some(blocksExactTargetClaim);
}

function collectFeasibleValues(
  sets: Map<string, Set<unknown>>,
  candidate: CandidateBuild,
  request: LegalBuildSearchRequest,
  character: PlayerCharacter,
): void {
  const values: Array<[string, unknown]> = [["jobName", candidate.loadout.jobName], ["rank", candidate.loadout.rank], ["skills", candidate.loadout.skills ?? []]];
  const targetStats = new Set(Object.keys(request.target.exactPoint));
  for (const stat of STAT_KEYS) {
    if (targetStats.has(stat) || request.locks?.some((lock) => lock.field === `statLevels.${stat}`)) {
      if (candidate.loadout.statLevels?.[stat] !== undefined) values.push([`statLevels.${stat}`, candidate.loadout.statLevels[stat]]);
      continue;
    }
    const current = integerLevel(character.state.statLevels?.[stat] ?? character.state.level ?? 1);
    const cap = trainingCap(request, character, stat, current);
    for (const level of integerRange(current, cap)) values.push([`statLevels.${stat}`, level]);
  }
  for (const option of candidate.equipment) {
    values.push([`equipment.${option.slot}.name`, option.equipmentName]);
    values.push([`equipment.${option.slot}.level`, option.level]);
    values.push([`equipment.${option.slot}.instanceId`, option.instanceId]);
  }
  for (const skillName of candidate.loadout.skills ?? []) {
    if (!BATTLE_INVOCATION_SKILLS.has(skillName)) continue;
    for (const level of [0, 1, 2]) values.push([`skillInvocations.${skillName}`, level]);
  }
  for (const [field, value] of values) {
    const set = sets.get(field) ?? new Set<unknown>();
    set.add(value);
    sets.set(field, set);
  }
}

function serializeFeasibleOptions(sets: Map<string, Set<unknown>>): Record<string, unknown[]> {
  return Object.fromEntries([...sets].map(([key, values]) => [key, [...values]]));
}

/**
 * Evaluate a proposed dropdown choice by adding one lock and calling the same whole-build solver.
 * An incomplete search yields `unknown`, never a disabled/hidden result.
 */
export function checkBuildOption(
  request: LegalBuildSearchRequest,
  field: string,
  value: unknown,
): { state: "feasible" | "infeasible" | "unknown"; reason?: string } {
  const result = searchLegalBuild({ ...request, locks: [...(request.locks ?? []), { field, value }] });
  if (result.exactFeasible === true && result.result?.status !== "unmapped-or-unproven") return { state: "feasible" };
  if (result.exactFeasible === true) return { state: "unknown", reason: "A complete exact stat build exists, but at least one selected legality rule or required field is unresolved." };
  if (result.exactFeasible === false && result.complete) return { state: "infeasible", reason: result.reason };
  return { state: "unknown", reason: result.reason ?? "The bounded search did not prove exact feasibility." };
}

function validateAnchor(request: LegalBuildSearchRequest, character: PlayerCharacter | undefined): string | null {
  if (!character) return "Choose a character in the shared player profile.";
  if (!character.available) return "The selected character is not marked available in the profile.";
  const job = request.shared.jobs?.[request.jobName];
  if (!job?.ranks?.[request.rank]) return "The selected job/rank has no canonical shared job data.";
  const expectedEncounter = request.encounterId;
  if (expectedEncounter !== undefined && !ENCOUNTER_BY_ID.has(expectedEncounter)) return "The optimizer encounter does not resolve to the current encounter catalog.";
  return null;
}

function profileLoadout(request: LegalBuildSearchRequest, character: PlayerCharacter): SavedLoadout {
  const active = request.profile.valuables.active;
  const declaredActive: Record<string, number | undefined> = {};
  for (const item of RESIDENT_STAT_ITEMS) {
    const value = active[item.key];
    declaredActive[item.key] = typeof value === "number" ? value : undefined;
  }
  return {
    ...character.state,
    id: character.id,
    name: character.state.name || character.id,
    jobName: request.jobName,
    rank: request.rank,
    // Search preflight has already rejected unknown counts for every target stat that a water can
    // change. Keep other missing counts explicitly undefined so conversion can report them rather
    // than treating the partial profile as a declared-empty valuables record.
    residentStatItems: declaredActive,
  };
}

function equipmentOptions(request: LegalBuildSearchRequest, slot: EquipmentSlotKey): EquipmentOption[] {
  const options: EquipmentOption[] = [{ slot, equipmentName: null, level: null, stackId: null, instanceId: null, owned: true, currentLevel: null }];
  const ownedNames = new Set<string>();
  for (const stack of request.profile.equipment) {
    if ((stack.ownership ?? "owned") !== "owned" || stack.quantity < 1) continue;
    if (gearSlotForName(stack.equipmentName, request.shared.slotAssignments) !== slot) continue;
    ownedNames.add(stack.equipmentName);
    const count = Math.max(1, Math.min(999, Math.floor(stack.quantity)));
    for (let index = 0; index < count; index += 1) {
      const currentLevel = stack.currentLevel;
      const instanceId = `${stack.id}#${index + 1}`;
      const levels = request.settings.inventoryPolicy === "owned-upgrades"
        ? integerRange(currentLevel, GEAR_LEVEL_CAP)
        : [currentLevel];
      for (const level of levels) options.push({
        slot, equipmentName: stack.equipmentName, level, stackId: stack.id, instanceId,
        owned: true, currentLevel,
      });
    }
  }
  if (request.settings.inventoryPolicy === "theoretical") {
    for (const entry of SYNTHETIC_LEGAL_EQUIPMENT_CATALOG) {
      if (entry.slot !== slot || ownedNames.has(entry.name)) continue;
      const targetEquipment = request.target.controllable.find((field) => field.field === "equipment");
      const targetRows = Array.isArray(targetEquipment?.value) ? targetEquipment!.value as Array<{ id?: number; level?: number }> : [];
      const recorded = targetRows.find((row) => row.id === entry.id)?.level;
      const levels = recorded && recorded >= 1 && recorded <= GEAR_LEVEL_CAP
        ? [recorded]
        : integerRange(1, GEAR_LEVEL_CAP);
      for (const level of levels) options.push({
        slot, equipmentName: entry.name, level, stackId: null, instanceId: null,
        owned: false, currentLevel: null,
      });
    }
  }
  return options;
}

function skillOrderOptions(request: LegalBuildSearchRequest, character: PlayerCharacter): string[][] {
  const targetSkills = request.target.controllable.find((entry) => entry.field === "skills")?.value;
  if (Array.isArray(targetSkills)) {
    const names = targetSkills.map((entry) => entry && typeof entry === "object" ? (entry as { name?: unknown }).name : null);
    const valid = names.every((name): name is string => typeof name === "string" && name.length > 0);
    const selectedNames = names as string[];
    if (!valid || !isLegalUnlockedSkillOrder(selectedNames, request.jobName, request.profile.ownedSkills)) return [];
    return [selectedNames];
  }
  const current = Array.isArray(character.state.skills) ? [...character.state.skills] : [];
  const options = isLegalUnlockedSkillOrder(current, request.jobName, request.profile.ownedSkills) ? [current] : [[]];
  const unique = new Map(options.map((skills) => [JSON.stringify(skills), skills]));
  return [...unique.values()];
}

/** Account ownership is necessary; the shared battle picker then applies the existing job gate. */
function isLegalUnlockedSkillOrder(names: string[], jobName: string, unlockedNames: string[]): boolean {
  if (names.some((name) => !unlockedNames.includes(name))) return false;
  const selected: string[] = [];
  for (const name of names) {
    if (!canPickHumanSkill(jobName, name, selected)) return false;
    selected.push(name);
  }
  return true;
}

function evaluateEquipmentCombination(
  request: LegalBuildSearchRequest,
  character: PlayerCharacter,
  baseLoadout: SavedLoadout,
  equipment: EquipmentOption[],
  skills: string[],
  statLocks: Partial<Record<StatKey, unknown>>,
  lockMap: Record<string, unknown>,
): CandidateBuild | null {
  const loadout: SavedLoadout = {
    ...baseLoadout,
    equipment: equipment.filter((entry): entry is EquipmentOption & { equipmentName: string; level: number } =>
      entry.equipmentName !== null && entry.level !== null).map((entry) => ({ name: entry.equipmentName, level: entry.level })),
    skills: [...skills],
    skillInvocations: skills.length > 0 ? skills.map((name) => {
      const locked = lockMap[`skillInvocations.${name}`];
      if (locked === 0 || locked === 1 || locked === 2) return locked;
      const oldIndex = baseLoadout.skills?.indexOf(name) ?? -1;
      const captured = oldIndex >= 0 ? baseLoadout.skillInvocations?.[oldIndex] : undefined;
      return captured === 0 || captured === 1 || captured === 2 ? captured : -1;
    }) : [],
  };
  const rowByStat = new Map(draftStatRows(loadout, request.shared).map((row) => [row.stat, row]));
  const curve = jobCurveFor(request.shared, request.jobName, request.rank);
  const statLevels = { ...(loadout.statLevels ?? {}) } as Record<string, number>;
  const stats: Record<string, number> = {};
  const deltas: Record<string, number> = {};
  const unsupported: Array<{ field: string; reason: string }> = [];
  let exact = true;
  let distance = 0;

  for (const [rawStat, targetValue] of Object.entries(request.target.exactPoint)) {
    const stat = rawStat as StatKey;
    const row = rowByStat.get(stat);
    const curveEntry = curve[stat];
    if (!row || !curveEntry || row.curveValue === null || row.equipmentValue === undefined) {
      unsupported.push({ field: stat, reason: "The shared site evaluator has no complete curve/equipment row for this target stat." });
      exact = false;
      continue;
    }
    const currentLevel = integerLevel(character.state.statLevels?.[stat] ?? character.state.level ?? 1);
    const cap = trainingCap(request, character, stat, currentLevel);
    const lockedLevel = statLocks[stat];
    if (typeof lockedLevel === "number" && (!Number.isInteger(lockedLevel) || lockedLevel < currentLevel || lockedLevel > cap)) return null;
    const minimumLevel = typeof lockedLevel === "number" ? lockedLevel : currentLevel;
    const maximumLevel = typeof lockedLevel === "number" ? lockedLevel : cap;
    if (minimumLevel < currentLevel || maximumLevel > cap) return null;
    const best = bestStatLevel(curveEntry.base, curveEntry.inc, row.valuableValue + row.equipmentValue,
      targetValue, minimumLevel, maximumLevel);
    let bestLevel = best.level;
    let bestValue = best.value;
    let bestDelta = best.delta;
    const foundExact = best.delta === 0;
    if (!foundExact && typeof lockedLevel === "number") return null;
    statLevels[stat] = bestLevel;
    stats[stat] = bestValue;
    deltas[stat] = bestDelta;
    distance += Math.abs(bestDelta);
    if (bestDelta !== 0) exact = false;
  }
  const targetStats = new Set(Object.keys(request.target.exactPoint));
  for (const stat of STAT_KEYS) {
    if (targetStats.has(stat)) continue;
    const lockedLevel = statLocks[stat];
    if (lockedLevel === undefined) continue;
    const current = integerLevel(character.state.statLevels?.[stat] ?? character.state.level ?? 1);
    const cap = trainingCap(request, character, stat, current);
    if (typeof lockedLevel !== "number" || !Number.isInteger(lockedLevel) || lockedLevel < current || lockedLevel > cap) return null;
    statLevels[stat] = lockedLevel;
  }
  loadout.statLevels = statLevels;
  loadout.level = undefined;

  const conversion = battleSetupFromLoadouts([loadout], request.shared, {
    ...(request.encounterId === undefined ? {} : { encounterId: request.encounterId }),
  });
  const issues = conversion.issues
    .filter((issue) => {
      if (issue.code === "JOB_EQUIPMENT_COMPATIBILITY_UNKNOWN") return false;
      if (issue.code === "PROGRESSION_CAP_UNVERIFIED") {
        return Object.keys(request.target.exactPoint).some((rawStat) => {
          const stat = rawStat as StatKey;
          const current = integerLevel(character.state.statLevels?.[stat] ?? character.state.level ?? 1);
          const proposed = loadout.statLevels?.[stat] ?? current;
          return proposed > trainingCap(request, character, stat, current);
        });
      }
      if (issue.code === "SKILL_SLOT_CAP_PER_CHARACTER_DECLARED") {
        const count = loadout.skills?.length ?? 0;
        return count > 0 && !(typeof character.skillSlotCapacity === "number" && character.skillSlotCapacity >= count);
      }
      return true;
    })
    .map((issue) => `${issue.category}:${issue.code}: ${issue.message}`);
  if (!conversion.setup) return null;
  const upgrades = equipment.flatMap((entry) => {
    if (!entry.owned || !entry.equipmentName || entry.level === null || entry.currentLevel === null || entry.level <= entry.currentLevel) return [];
    return [{
      equipmentName: entry.equipmentName,
      inventoryStackId: entry.stackId,
      instanceId: entry.instanceId,
      quantity: 1,
      currentLevel: entry.currentLevel,
      requiredLevel: entry.level,
      addedLevels: entry.level - entry.currentLevel,
      cost: null,
    } satisfies RequiredUpgrade];
  });
  return {
    loadout,
    equipment,
    stats,
    deltas,
    exact,
    distance,
    upgrades,
    owned: equipment.every((entry) => entry.owned),
    issues,
    unsupported,
  };
}

/** Find the closest level on a monotone linear job curve with binary search, preserving native rounding. */
function bestStatLevel(base: number, increment: number, fixedContribution: number, target: number, minimum: number, maximum: number) {
  const at = (level: number) => Math.round(base + (level - 1) * increment) + fixedContribution;
  if (minimum > maximum) return { level: minimum, value: at(minimum), delta: at(minimum) - target };
  if (increment === 0) return { level: minimum, value: at(minimum), delta: at(minimum) - target };
  let low = minimum;
  let high = maximum + 1;
  const increasing = increment > 0;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    const crossesTarget = increasing ? at(middle) >= target : at(middle) <= target;
    if (crossesTarget) high = middle;
    else low = middle + 1;
  }
  const candidates = [...new Set([minimum, maximum, Math.max(minimum, Math.min(maximum, low)), Math.max(minimum, Math.min(maximum, low - 1))])];
  return candidates.map((level) => ({ level, value: at(level), delta: at(level) - target }))
    .sort((a, b) => Math.abs(a.delta) - Math.abs(b.delta) || a.level - b.level)[0];
}

function trainingCap(request: LegalBuildSearchRequest, character: PlayerCharacter, stat: StatKey, current: number): number {
  const explicit = character.trainingCaps?.[stat];
  if (typeof explicit === "number" && Number.isInteger(explicit) && explicit >= current) return Math.min(explicit, 999);
  const jobStat = request.shared.jobs?.[request.jobName]?.ranks?.[request.rank]?.stats?.[stat];
  const baseCap = jobStat?.maxLevel;
  if (typeof baseCap !== "number" || !Number.isInteger(baseCap) || baseCap < current) return current;
  const awakening = character.state.awakening;
  if (typeof awakening !== "number" || !Number.isInteger(awakening) || awakening < 0) return current;
  return Math.min(999, baseCap + awakening * 30);
}

function matchesRemainingLocks(loadout: SavedLoadout, equipment: EquipmentOption[], locks: Record<string, unknown>): boolean {
  for (const [field, value] of Object.entries(locks)) {
    if (field === "jobName" && loadout.jobName !== value) return false;
    else if (field === "rank" && loadout.rank !== value) return false;
    else if (field.startsWith("statLevels.")) {
      const stat = field.slice("statLevels.".length);
      if (loadout.statLevels?.[stat] !== value) return false;
    } else if (field === "skills" && JSON.stringify(loadout.skills ?? []) !== JSON.stringify(value)) return false;
    else if (field.startsWith("skillInvocations.")) {
      const name = field.slice("skillInvocations.".length);
      const index = loadout.skills?.indexOf(name) ?? -1;
      if (index < 0 || loadout.skillInvocations?.[index] !== value) return false;
    }
    else if (field.startsWith("equipment.")) {
      const [, slot, prop] = field.split(".");
      const option = equipment.find((entry) => entry.slot === slot);
      if (!option) return false;
      const actual = prop === "name" ? option.equipmentName : prop === "level" ? option.level : prop === "instanceId" ? option.instanceId : undefined;
      if (actual !== value) return false;
    }
  }
  return true;
}

function normalizeLocks(locks: BuildLock[]): Record<string, unknown> {
  return Object.fromEntries(locks.map((lock) => [lock.field, lock.value]));
}

function integerLevel(value: number): number {
  return Number.isInteger(value) && value >= 1 ? value : 1;
}

function integerRange(start: number, end: number): number[] {
  const lo = Math.max(1, Math.min(999, Math.floor(start)));
  const hi = Math.max(lo, Math.min(999, Math.floor(end)));
  return Array.from({ length: hi - lo + 1 }, (_, index) => lo + index);
}

/** Resolve the physical copies used in one simultaneous team. A repeated stack copy cannot be reused. */
export function respectsSimultaneousInventory(results: TranslationResult[], profile: PlayerProfile): boolean {
  const counts = new Map<string, number>();
  for (const result of results) {
    for (const use of Object.values(result.equipmentInstances)) {
      if (!use.stackId || !use.instanceId) continue;
      const next = (counts.get(use.instanceId) ?? 0) + 1;
      if (next > 1) return false;
      counts.set(use.instanceId, next);
    }
  }
  return [...counts.keys()].every((instanceId) => {
    const [stackId] = instanceId.split("#");
    return profile.equipment.some((stack) => stack.id === stackId && (stack.ownership ?? "owned") === "owned" && stack.quantity > 0);
  });
}
