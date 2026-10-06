import { KAIRO_ROOM_LOOT_GROUPS, WAIRO_DUNGEON_LOOT_GROUP, type EncounterLoot } from "./special-boss-loot";

export type Difficulty = EncounterLoot["difficulty"];

const KAIRO_BASE_COSTS: Record<Difficulty, number> = {
  Easy: 300,
  Normal: 450,
  Hard: 500,
  Extreme: 650,
};

const WAIRO_BASE_COSTS: Record<Difficulty, number> = {
  Easy: 60,
  Normal: 65,
  Hard: 70,
  Extreme: 80,
};

export const WAIRO_ENERGY_STOREHOUSE_CAPACITY = 20;
export const WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY = 150;
export const WAIRO_DIFFICULTIES: Difficulty[] = ["Easy", "Normal", "Hard", "Extreme"];

/** Kairo Room: energy per run = base_difficulty_cost + 8 × fighters */
export function kairoRunEnergy(difficulty: Difficulty, fighters: number): number {
  return KAIRO_BASE_COSTS[difficulty] + 8 * fighters;
}

/**
 * Wairo Dungeon: energy per run = max(base × max(50 × mines, maxEnergy) / 100, 1)
 * maxEnergy = energy storage + town hall storage (the value shown when energy is full).
 */
export function wairoRunEnergy(
  difficulty: Difficulty,
  mines: number,
  maxEnergy: number,
): number {
  const factor = Math.max(50 * mines, maxEnergy);
  return Math.max((WAIRO_BASE_COSTS[difficulty] * factor) / 100, 1);
}

export type WairoStorageDeleteState = {
  deletedEnergyStorehouses: number;
  deletedHgEnergyStorehouses: number;
  deletedCapacity: number;
};

export type WairoRunPlanStep = {
  difficulty: Difficulty;
  maxEnergy: number;
  energyBeforeRun: number;
  energyCost: number;
  energyAfterRun: number;
  collectActions: WairoCollectAction[];
  deletedEnergyStorehouses: number;
  deletedHgEnergyStorehouses: number;
  newlyDeletedEnergyStorehouses: number;
  newlyDeletedHgEnergyStorehouses: number;
};

export type WairoCollectSource = {
  label: string;
  amount: number;
};

export type WairoCollectAction = {
  sourceLabel: string;
  amount: number;
  remainingInSource: number;
};

export type WairoReplenishPlan = {
  targetRuns: number;
  totalRuns: number;
  leftoverEnergy: number;
  restoredMaxEnergy: number;
  rebuiltEnergyStorehouses: number;
  rebuiltHgEnergyStorehouses: number;
  repeatTargetRuns: number;
  repeatTotalRuns: number;
  steps: WairoRunPlanStep[];
  cycles: WairoReplenishCycle[];
};

export type WairoReplenishCycle = {
  cycleNumber: number;
  refillItemUsed: number | null;
  startingEnergy: number;
  startingMaxEnergy: number;
  targetRuns: number;
  totalRuns: number;
  leftoverEnergy: number;
  rebuiltEnergyStorehouses: number;
  rebuiltHgEnergyStorehouses: number;
  nextEnergyStorehouses: number;
  nextHgEnergyStorehouses: number;
  restoredMaxEnergy: number;
  repeatTargetRuns: number;
  repeatTotalRuns: number;
  remainingCollectSources: WairoCollectSource[];
  steps: WairoRunPlanStep[];
};

type DeleteOption = WairoStorageDeleteState & {
  maxEnergy: number;
};

type PlanState = {
  option: DeleteOption;
  energy: number;
  collectSources: WairoCollectSource[];
  steps: WairoRunPlanStep[];
};

const WAIRO_MAX_PLAN_STATES = 80;
const WAIRO_MAX_CYCLE_CANDIDATES = 8;
const WAIRO_MAX_ROUTE_CANDIDATES = 12;

function betterState(candidate: PlanState, current: PlanState | undefined): boolean {
  if (!current) return true;
  if (candidate.energy !== current.energy) return candidate.energy > current.energy;
  const candidateCollectable = candidate.collectSources.reduce((sum, source) => sum + source.amount, 0);
  const currentCollectable = current.collectSources.reduce((sum, source) => sum + source.amount, 0);
  if (candidateCollectable !== currentCollectable) return candidateCollectable > currentCollectable;
  if (candidate.option.deletedCapacity !== current.option.deletedCapacity) {
    return candidate.option.deletedCapacity < current.option.deletedCapacity;
  }
  const candidateDeletes = candidate.option.deletedEnergyStorehouses + candidate.option.deletedHgEnergyStorehouses;
  const currentDeletes = current.option.deletedEnergyStorehouses + current.option.deletedHgEnergyStorehouses;
  return candidateDeletes < currentDeletes;
}

function comparePlanStates(a: PlanState, b: PlanState): number {
  if (a.steps.length !== b.steps.length) return b.steps.length - a.steps.length;
  if (a.energy !== b.energy) return b.energy - a.energy;
  const aCollectable = a.collectSources.reduce((sum, source) => sum + source.amount, 0);
  const bCollectable = b.collectSources.reduce((sum, source) => sum + source.amount, 0);
  if (aCollectable !== bCollectable) return bCollectable - aCollectable;
  if (a.option.deletedCapacity !== b.option.deletedCapacity) {
    return a.option.deletedCapacity - b.option.deletedCapacity;
  }
  const aDeletes = a.option.deletedEnergyStorehouses + a.option.deletedHgEnergyStorehouses;
  const bDeletes = b.option.deletedEnergyStorehouses + b.option.deletedHgEnergyStorehouses;
  return aDeletes - bDeletes;
}

function prunePlanStates(states: Map<string, PlanState>): Map<string, PlanState> {
  if (states.size <= WAIRO_MAX_PLAN_STATES) return states;
  return new Map(
    [...states.values()]
      .sort(comparePlanStates)
      .slice(0, WAIRO_MAX_PLAN_STATES)
      .map((state) => [planStateKey(state), state]),
  );
}

function collectSourceKey(sources: WairoCollectSource[]): string {
  return sources.map((source) => Math.max(0, Math.floor(source.amount))).join(",");
}

function sanitizeCollectSources(sources: WairoCollectSource[] = []): WairoCollectSource[] {
  return sources
    .map((source, index) => ({
      label: source.label || `Source ${index + 1}`,
      amount: Math.max(0, source.amount),
    }))
    .filter((source) => source.amount > 0);
}

function splitCollectSourcesByBudget(
  sources: WairoCollectSource[],
  budget: number,
): { available: WairoCollectSource[]; withheld: WairoCollectSource[] } {
  let remainingBudget = Math.max(0, budget);
  const available: WairoCollectSource[] = [];
  const withheld: WairoCollectSource[] = [];

  for (const source of sources) {
    if (remainingBudget <= 0) {
      withheld.push({ ...source });
      continue;
    }
    const offered = Math.min(source.amount, remainingBudget);
    if (offered > 0) available.push({ ...source, amount: offered });
    if (source.amount > offered) withheld.push({ ...source, amount: source.amount - offered });
    remainingBudget -= offered;
  }

  return { available, withheld };
}

function collectForRun({
  energy,
  maxEnergy,
  energyCost,
  sources,
}: {
  energy: number;
  maxEnergy: number;
  energyCost: number;
  sources: WairoCollectSource[];
}): { energy: number; sources: WairoCollectSource[]; actions: WairoCollectAction[] } | null {
  if (energy >= energyCost) return { energy, sources, actions: [] };
  if (energyCost > maxEnergy) return null;

  let nextEnergy = energy;
  const nextSources = sources.map((source) => ({ ...source }));
  const actions: WairoCollectAction[] = [];

  while (nextEnergy < energyCost) {
    const gap = maxEnergy - nextEnergy;
    if (gap <= 0) return null;
    const deficit = energyCost - nextEnergy;
    const indexed = nextSources
      .map((source, index) => ({ source, index }))
      .filter(({ source }) => source.amount > 0);
    if (indexed.length === 0) return null;
    indexed.sort((a, b) => {
      const aCanCover = a.source.amount >= deficit;
      const bCanCover = b.source.amount >= deficit;
      if (aCanCover !== bCanCover) return aCanCover ? -1 : 1;
      return aCanCover ? a.source.amount - b.source.amount : b.source.amount - a.source.amount;
    });
    const picked = indexed[0];
    const transfer = Math.min(picked.source.amount, gap);
    if (transfer <= 0) return null;
    nextSources[picked.index].amount -= transfer;
    nextEnergy += transfer;
    actions.push({
      sourceLabel: picked.source.label,
      amount: transfer,
      remainingInSource: nextSources[picked.index].amount,
    });
  }

  return { energy: nextEnergy, sources: nextSources, actions };
}

function buildWairoDeleteOptions(
  maxEnergy: number,
  energyStorehouses: number,
  hgEnergyStorehouses: number,
): DeleteOption[] {
  const byCapacity = new Map<number, WairoStorageDeleteState>();
  for (let normal = 0; normal <= energyStorehouses; normal += 1) {
    for (let hg = 0; hg <= hgEnergyStorehouses; hg += 1) {
      const deletedCapacity =
        normal * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
        hg * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
      if (deletedCapacity > maxEnergy) continue;
      const existing = byCapacity.get(deletedCapacity);
      const next = {
        deletedEnergyStorehouses: normal,
        deletedHgEnergyStorehouses: hg,
        deletedCapacity,
      };
      if (
        !existing ||
        normal + hg < existing.deletedEnergyStorehouses + existing.deletedHgEnergyStorehouses ||
        (normal + hg === existing.deletedEnergyStorehouses + existing.deletedHgEnergyStorehouses &&
          hg < existing.deletedHgEnergyStorehouses)
      ) {
        byCapacity.set(deletedCapacity, next);
      }
    }
  }

  return [...byCapacity.values()]
    .map((option) => ({ ...option, maxEnergy: maxEnergy - option.deletedCapacity }))
    .filter((option) => option.maxEnergy > 0)
    .sort((a, b) => a.deletedCapacity - b.deletedCapacity);
}

function makeWairoStep(
  difficulty: Difficulty,
  mines: number,
  previousOption: DeleteOption,
  option: DeleteOption,
  energyBeforeDelete: number,
  collectSources: WairoCollectSource[],
): { step: WairoRunPlanStep; collectSources: WairoCollectSource[] } | null {
  const energyBeforeCollect = Math.min(energyBeforeDelete, option.maxEnergy);
  const energyCost = wairoRunEnergy(difficulty, mines, option.maxEnergy);
  const collected = collectForRun({
    energy: energyBeforeCollect,
    maxEnergy: option.maxEnergy,
    energyCost,
    sources: collectSources,
  });
  if (!collected) return null;
  const energyBeforeRun = collected.energy;
  if (energyBeforeRun < energyCost) return null;
  return {
    step: {
      difficulty,
      maxEnergy: option.maxEnergy,
      energyBeforeRun,
      energyCost,
      energyAfterRun: energyBeforeRun - energyCost,
      collectActions: collected.actions,
      deletedEnergyStorehouses: option.deletedEnergyStorehouses,
      deletedHgEnergyStorehouses: option.deletedHgEnergyStorehouses,
      newlyDeletedEnergyStorehouses:
        option.deletedEnergyStorehouses - previousOption.deletedEnergyStorehouses,
      newlyDeletedHgEnergyStorehouses:
        option.deletedHgEnergyStorehouses - previousOption.deletedHgEnergyStorehouses,
    },
    collectSources: collected.sources,
  };
}

function planStateKey(state: PlanState): string {
  return `${state.option.deletedCapacity}|${collectSourceKey(state.collectSources)}`;
}

function extendWithDifficulty(
  states: Map<string, PlanState>,
  options: DeleteOption[],
  difficulty: Difficulty,
  mines: number,
): Map<string, PlanState> {
  const nextStates = new Map<string, PlanState>();
  for (const state of states.values()) {
    for (const option of options) {
      if (option.deletedCapacity < state.option.deletedCapacity) continue;
      const result = makeWairoStep(difficulty, mines, state.option, option, state.energy, state.collectSources);
      if (!result) continue;
      const nextState: PlanState = {
        option,
        energy: result.step.energyAfterRun,
        collectSources: result.collectSources,
        steps: [...state.steps, result.step],
      };
      const key = planStateKey(nextState);
      if (betterState(nextState, nextStates.get(key))) {
        nextStates.set(key, nextState);
      }
    }
  }
  return prunePlanStates(nextStates);
}

function pickBestPlanState(states: Map<string, PlanState>): PlanState | null {
  let best: PlanState | null = null;
  for (const state of states.values()) {
    if (!best || betterState(state, best)) best = state;
  }
  return best;
}

function calculateWairoRunPlan({
  currentEnergy,
  maxEnergy,
  mines,
  energyStorehouses,
  hgEnergyStorehouses,
  targetDifficulty,
  collectSources = [],
}: {
  currentEnergy: number;
  maxEnergy: number;
  mines: number;
  energyStorehouses: number;
  hgEnergyStorehouses: number;
  targetDifficulty: Difficulty;
  collectSources?: WairoCollectSource[];
}): { targetRuns: number; finalState: PlanState } {
  const safeMaxEnergy = Math.max(1, maxEnergy);
  const options = buildWairoDeleteOptions(
    safeMaxEnergy,
    Math.max(0, Math.floor(energyStorehouses)),
    Math.max(0, Math.floor(hgEnergyStorehouses)),
  );
  const initialOption = options[0] ?? {
    deletedEnergyStorehouses: 0,
    deletedHgEnergyStorehouses: 0,
    deletedCapacity: 0,
    maxEnergy: safeMaxEnergy,
  };
  const initialState = {
    option: initialOption,
    energy: Math.min(Math.max(0, currentEnergy), safeMaxEnergy),
    collectSources: sanitizeCollectSources(collectSources),
    steps: [],
  } satisfies PlanState;
  let states = new Map<string, PlanState>([[planStateKey(initialState), initialState]]);
  let targetRuns = 0;

  while (true) {
    const nextStates = extendWithDifficulty(states, options, targetDifficulty, mines);
    if (nextStates.size === 0) break;
    states = nextStates;
    targetRuns += 1;
  }

  let fallbackStates = states;
  const easierDifficulties = WAIRO_DIFFICULTIES.slice(
    0,
    WAIRO_DIFFICULTIES.indexOf(targetDifficulty),
  ).reverse();

  while (easierDifficulties.length > 0) {
    const nextFallbackStates = new Map<string, PlanState>();
    for (const difficulty of easierDifficulties) {
      const nextStates = extendWithDifficulty(fallbackStates, options, difficulty, mines);
      for (const state of nextStates.values()) {
        const key = planStateKey(state);
        if (betterState(state, nextFallbackStates.get(key))) {
          nextFallbackStates.set(key, state);
        }
      }
    }
    if (nextFallbackStates.size === 0) break;
    fallbackStates = nextFallbackStates;
  }

  const fallbackState = pickBestPlanState(fallbackStates) ?? initialState;
  return { targetRuns, finalState: fallbackState };
}

function compareCycles(a: WairoReplenishCycle, b: WairoReplenishCycle): number {
  if (a.targetRuns !== b.targetRuns) return b.targetRuns - a.targetRuns;
  if (a.totalRuns !== b.totalRuns) return b.totalRuns - a.totalRuns;
  const aRebuilds = a.rebuiltEnergyStorehouses + a.rebuiltHgEnergyStorehouses;
  const bRebuilds = b.rebuiltEnergyStorehouses + b.rebuiltHgEnergyStorehouses;
  if (aRebuilds !== bRebuilds) return aRebuilds - bRebuilds;
  const aCapacity = a.rebuiltEnergyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY + a.rebuiltHgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
  const bCapacity = b.rebuiltEnergyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY + b.rebuiltHgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
  if (aCapacity !== bCapacity) return aCapacity - bCapacity;
  if (a.restoredMaxEnergy !== b.restoredMaxEnergy) return a.restoredMaxEnergy - b.restoredMaxEnergy;
  return b.leftoverEnergy - a.leftoverEnergy;
}

function planSingleWairoCycleCandidates({
  cycleNumber,
  refillItemUsed,
  currentEnergy,
  maxEnergy,
  fixedCapacity,
  mines,
  builtEnergyStorehouses,
  builtHgEnergyStorehouses,
  totalEnergyStorehouses,
  totalHgEnergyStorehouses,
  targetDifficulty,
  collectSources,
}: {
  cycleNumber: number;
  refillItemUsed: number | null;
  currentEnergy: number;
  maxEnergy: number;
  fixedCapacity: number;
  mines: number;
  builtEnergyStorehouses: number;
  builtHgEnergyStorehouses: number;
  totalEnergyStorehouses: number;
  totalHgEnergyStorehouses: number;
  targetDifficulty: Difficulty;
  collectSources: WairoCollectSource[];
}): WairoReplenishCycle[] {
  const basePlan = calculateWairoRunPlan({
    currentEnergy,
    maxEnergy,
    mines,
    energyStorehouses: builtEnergyStorehouses,
    hgEnergyStorehouses: builtHgEnergyStorehouses,
    targetDifficulty,
    collectSources,
  });
  const options = buildWairoDeleteOptions(
    Math.max(1, maxEnergy),
    Math.max(0, Math.floor(builtEnergyStorehouses)),
    Math.max(0, Math.floor(builtHgEnergyStorehouses)),
  );
  let states = new Map<string, PlanState>([
    [
      planStateKey({
        option: options[0],
        energy: Math.min(Math.max(0, currentEnergy), Math.max(1, maxEnergy)),
        collectSources: sanitizeCollectSources(collectSources),
        steps: [],
      }),
      {
        option: options[0],
        energy: Math.min(Math.max(0, currentEnergy), Math.max(1, maxEnergy)),
        collectSources: sanitizeCollectSources(collectSources),
        steps: [],
      },
    ],
  ]);
  for (let index = 0; index < basePlan.targetRuns; index += 1) {
    const nextStates = extendWithDifficulty(states, options, targetDifficulty, mines);
    if (nextStates.size === 0) break;
    states = nextStates;
  }
  const easierDifficulties = WAIRO_DIFFICULTIES.slice(
    0,
    WAIRO_DIFFICULTIES.indexOf(targetDifficulty),
  ).reverse();
  for (let index = basePlan.targetRuns; index < basePlan.finalState.steps.length; index += 1) {
    const nextStates = new Map<string, PlanState>();
    for (const difficulty of easierDifficulties) {
      const candidates = extendWithDifficulty(states, options, difficulty, mines);
      for (const state of candidates.values()) {
        const key = planStateKey(state);
        if (betterState(state, nextStates.get(key))) nextStates.set(key, state);
      }
    }
    if (nextStates.size === 0) break;
    states = nextStates;
  }

  const cycles: WairoReplenishCycle[] = [];

  for (const state of states.values()) {
    const remainingEnergyStorehouses = builtEnergyStorehouses - state.option.deletedEnergyStorehouses;
    const remainingHgEnergyStorehouses = builtHgEnergyStorehouses - state.option.deletedHgEnergyStorehouses;
    const rebuildCandidates: Array<{
      normal: number;
      hg: number;
      rebuilds: number;
      rebuildCapacity: number;
      maxEnergy: number;
    }> = [];
    for (let normal = remainingEnergyStorehouses; normal <= totalEnergyStorehouses; normal += 1) {
      for (let hg = remainingHgEnergyStorehouses; hg <= totalHgEnergyStorehouses; hg += 1) {
        const candidateMaxEnergy =
          fixedCapacity +
          normal * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
          hg * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
        if (candidateMaxEnergy <= 0) continue;
        const rebuilds = normal - remainingEnergyStorehouses + hg - remainingHgEnergyStorehouses;
        const rebuildCapacity =
          (normal - remainingEnergyStorehouses) * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
          (hg - remainingHgEnergyStorehouses) * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
        rebuildCandidates.push({ normal, hg, rebuilds, rebuildCapacity, maxEnergy: candidateMaxEnergy });
      }
    }
    rebuildCandidates.sort((a, b) => {
      if (a.rebuilds !== b.rebuilds) return a.rebuilds - b.rebuilds;
      if (a.rebuildCapacity !== b.rebuildCapacity) return a.rebuildCapacity - b.rebuildCapacity;
      return a.maxEnergy - b.maxEnergy;
    });

    for (const rebuild of rebuildCandidates) {
        const candidate = calculateWairoRunPlan({
          currentEnergy: rebuild.maxEnergy,
          maxEnergy: rebuild.maxEnergy,
          mines,
          energyStorehouses: rebuild.normal,
          hgEnergyStorehouses: rebuild.hg,
          targetDifficulty,
          collectSources: state.collectSources,
        });
        const candidateTotalRuns = candidate.finalState.steps.length;
        if (candidate.targetRuns < basePlan.targetRuns || candidateTotalRuns < basePlan.finalState.steps.length) {
          continue;
        }
        cycles.push({
          cycleNumber,
          refillItemUsed,
          startingEnergy: Math.min(Math.max(0, currentEnergy), maxEnergy),
          startingMaxEnergy: maxEnergy,
          targetRuns: basePlan.targetRuns,
          totalRuns: state.steps.length,
          leftoverEnergy: state.energy,
          rebuiltEnergyStorehouses: rebuild.normal - remainingEnergyStorehouses,
          rebuiltHgEnergyStorehouses: rebuild.hg - remainingHgEnergyStorehouses,
          nextEnergyStorehouses: rebuild.normal,
          nextHgEnergyStorehouses: rebuild.hg,
          restoredMaxEnergy: rebuild.maxEnergy,
          repeatTargetRuns: candidate.targetRuns,
          repeatTotalRuns: candidateTotalRuns,
          remainingCollectSources: state.collectSources,
          steps: state.steps,
        });
    }
  }
  if (cycles.length === 0) {
    const finalState = basePlan.finalState;
    cycles.push({
      cycleNumber,
      refillItemUsed,
      startingEnergy: Math.min(Math.max(0, currentEnergy), maxEnergy),
      startingMaxEnergy: maxEnergy,
      targetRuns: basePlan.targetRuns,
      totalRuns: finalState.steps.length,
      leftoverEnergy: finalState.energy,
      rebuiltEnergyStorehouses: finalState.option.deletedEnergyStorehouses,
      rebuiltHgEnergyStorehouses: finalState.option.deletedHgEnergyStorehouses,
      nextEnergyStorehouses: builtEnergyStorehouses,
      nextHgEnergyStorehouses: builtHgEnergyStorehouses,
      restoredMaxEnergy: maxEnergy,
      repeatTargetRuns: basePlan.targetRuns,
      repeatTotalRuns: finalState.steps.length,
      remainingCollectSources: finalState.collectSources,
      steps: finalState.steps,
    });
  }

  return cycles.sort(compareCycles).slice(0, WAIRO_MAX_CYCLE_CANDIDATES);
}

function planSingleWairoCycle(args: Parameters<typeof planSingleWairoCycleCandidates>[0]): WairoReplenishCycle {
  return planSingleWairoCycleCandidates(args)[0];
}

type WairoRouteState = {
  cycles: WairoReplenishCycle[];
  currentEnergy: number;
  maxEnergy: number;
  energyStorehouses: number;
  hgEnergyStorehouses: number;
  collectSources: WairoCollectSource[];
  targetRuns: number;
  totalRuns: number;
  rebuildBuildings: number;
  rebuildCapacity: number;
  maxCycleRebuildBuildings: number;
};

function compareRouteStates(a: WairoRouteState, b: WairoRouteState): number {
  if (a.targetRuns !== b.targetRuns) return b.targetRuns - a.targetRuns;
  if (a.totalRuns !== b.totalRuns) return b.totalRuns - a.totalRuns;
  if (a.rebuildBuildings !== b.rebuildBuildings) return a.rebuildBuildings - b.rebuildBuildings;
  if (a.rebuildCapacity !== b.rebuildCapacity) return a.rebuildCapacity - b.rebuildCapacity;
  if (a.maxCycleRebuildBuildings !== b.maxCycleRebuildBuildings) {
    return a.maxCycleRebuildBuildings - b.maxCycleRebuildBuildings;
  }
  const aLeftover = a.cycles.at(-1)?.leftoverEnergy ?? 0;
  const bLeftover = b.cycles.at(-1)?.leftoverEnergy ?? 0;
  return bLeftover - aLeftover;
}

export function planWairoReplenishRuns({
  currentEnergy,
  maxEnergy,
  mines,
  energyStorehouses,
  hgEnergyStorehouses,
  targetDifficulty,
  refillItems = 1,
  collectSources = [],
}: {
  currentEnergy: number;
  maxEnergy: number;
  mines: number;
  energyStorehouses: number;
  hgEnergyStorehouses: number;
  targetDifficulty: Difficulty;
  refillItems?: number;
  collectSources?: WairoCollectSource[];
}): WairoReplenishPlan {
  const safeMaxEnergy = Math.max(1, maxEnergy);
  const safeEnergyStorehouses = Math.max(0, Math.floor(energyStorehouses));
  const safeHgEnergyStorehouses = Math.max(0, Math.floor(hgEnergyStorehouses));
  const safeRefillItems = Math.max(0, Math.floor(refillItems));
  const totalDeletableCapacity =
    safeEnergyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
    safeHgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
  const fixedCapacity = Math.max(1, safeMaxEnergy - totalDeletableCapacity);
  let routeStates: WairoRouteState[] = [{
    cycles: [],
    currentEnergy: Math.min(Math.max(0, currentEnergy), safeMaxEnergy),
    maxEnergy: safeMaxEnergy,
    energyStorehouses: safeEnergyStorehouses,
    hgEnergyStorehouses: safeHgEnergyStorehouses,
    collectSources: sanitizeCollectSources(collectSources),
    targetRuns: 0,
    totalRuns: 0,
    rebuildBuildings: 0,
    rebuildCapacity: 0,
    maxCycleRebuildBuildings: 0,
  }];

  for (let cycleIndex = 0; cycleIndex <= safeRefillItems; cycleIndex += 1) {
    const nextRouteStates: WairoRouteState[] = [];
    for (const routeState of routeStates) {
      const remainingCycles = safeRefillItems - cycleIndex + 1;
      const collectBudget =
        routeState.collectSources.reduce((sum, source) => sum + source.amount, 0) / remainingCycles;
      const { available: cycleCollectSources, withheld: withheldCollectSources } =
        splitCollectSourcesByBudget(routeState.collectSources, collectBudget);
      const cycleCandidates = planSingleWairoCycleCandidates({
        cycleNumber: cycleIndex + 1,
        refillItemUsed: cycleIndex === 0 ? null : cycleIndex,
        currentEnergy: routeState.currentEnergy,
        maxEnergy: routeState.maxEnergy,
        fixedCapacity,
        mines,
        builtEnergyStorehouses: routeState.energyStorehouses,
        builtHgEnergyStorehouses: routeState.hgEnergyStorehouses,
        totalEnergyStorehouses: safeEnergyStorehouses,
        totalHgEnergyStorehouses: safeHgEnergyStorehouses,
        targetDifficulty,
        collectSources: cycleCollectSources,
      });
      for (const cycle of cycleCandidates) {
        const cycleRebuildBuildings = cycle.rebuiltEnergyStorehouses + cycle.rebuiltHgEnergyStorehouses;
        const cycleRebuildCapacity =
          cycle.rebuiltEnergyStorehouses * WAIRO_ENERGY_STOREHOUSE_CAPACITY +
          cycle.rebuiltHgEnergyStorehouses * WAIRO_HG_ENERGY_STOREHOUSE_CAPACITY;
        nextRouteStates.push({
          cycles: [...routeState.cycles, cycle],
          currentEnergy: cycle.restoredMaxEnergy,
          maxEnergy: cycle.restoredMaxEnergy,
          energyStorehouses: cycle.nextEnergyStorehouses,
          hgEnergyStorehouses: cycle.nextHgEnergyStorehouses,
          collectSources: sanitizeCollectSources([
            ...cycle.remainingCollectSources,
            ...withheldCollectSources,
          ]),
          targetRuns: routeState.targetRuns + cycle.targetRuns,
          totalRuns: routeState.totalRuns + cycle.totalRuns,
          rebuildBuildings: routeState.rebuildBuildings + cycleRebuildBuildings,
          rebuildCapacity: routeState.rebuildCapacity + cycleRebuildCapacity,
          maxCycleRebuildBuildings: Math.max(routeState.maxCycleRebuildBuildings, cycleRebuildBuildings),
        });
      }
    }
    routeStates = nextRouteStates.sort(compareRouteStates).slice(0, WAIRO_MAX_ROUTE_CANDIDATES);
  }

  const cycles = routeStates.sort(compareRouteStates)[0]?.cycles ?? [];
  const firstCycle = cycles[0] ?? planSingleWairoCycle({
    cycleNumber: 1,
    refillItemUsed: null,
    currentEnergy,
    maxEnergy: safeMaxEnergy,
    fixedCapacity,
    mines,
    builtEnergyStorehouses: safeEnergyStorehouses,
    builtHgEnergyStorehouses: safeHgEnergyStorehouses,
    totalEnergyStorehouses: safeEnergyStorehouses,
    totalHgEnergyStorehouses: safeHgEnergyStorehouses,
    targetDifficulty,
    collectSources: sanitizeCollectSources(collectSources),
  });

  return {
    targetRuns: cycles.reduce((sum, cycle) => sum + cycle.targetRuns, 0),
    totalRuns: cycles.reduce((sum, cycle) => sum + cycle.totalRuns, 0),
    leftoverEnergy: cycles.at(-1)?.leftoverEnergy ?? firstCycle.leftoverEnergy,
    restoredMaxEnergy: cycles.at(-1)?.restoredMaxEnergy ?? firstCycle.restoredMaxEnergy,
    rebuiltEnergyStorehouses: cycles.at(-1)?.rebuiltEnergyStorehouses ?? firstCycle.rebuiltEnergyStorehouses,
    rebuiltHgEnergyStorehouses: cycles.at(-1)?.rebuiltHgEnergyStorehouses ?? firstCycle.rebuiltHgEnergyStorehouses,
    repeatTargetRuns: cycles.at(-1)?.repeatTargetRuns ?? firstCycle.repeatTargetRuns,
    repeatTotalRuns: cycles.at(-1)?.repeatTotalRuns ?? firstCycle.repeatTotalRuns,
    steps: firstCycle.steps,
    cycles,
  };
}

/**
 * Expected drops of targetItem per run for an encounter.
 * The two loot tables are assumed 50/50 probability.
 */
export function encounterExpectedDrops(encounter: EncounterLoot, targetItem: string): number {
  if (encounter.tables.length === 0) return 0;
  const tableEvs = encounter.tables.map((table) => {
    let ev = 0;
    for (const line of table) {
      if (line.item === targetItem) {
        // chance is stored as "1/N" fraction string
        const [num, den] = line.chance.split("/").map(Number);
        const chanceDecimal = den ? num / den : num;
        // quantity is "min-max" or a single number
        const qtyParts = line.quantity.split("-").map(Number);
        const minQty = qtyParts[0] ?? 1;
        const maxQty = qtyParts[1] ?? minQty;
        ev += chanceDecimal * ((minQty + maxQty) / 2);
      }
    }
    return ev;
  });
  return tableEvs.reduce((sum, ev) => sum + ev, 0) / tableEvs.length;
}

/** All unique droppable items across all Kairo Room encounters, sorted. */
export function getAllKairoItems(): string[] {
  const items = new Set<string>();
  for (const group of KAIRO_ROOM_LOOT_GROUPS) {
    for (const enc of group.encounters) {
      for (const table of enc.tables) {
        for (const line of table) items.add(line.item);
      }
    }
  }
  return [...items].sort((a, b) => a.localeCompare(b));
}

/** All unique droppable items across all Wairo Dungeon encounters, sorted. */
export function getAllWairoItems(): string[] {
  const items = new Set<string>();
  for (const enc of WAIRO_DUNGEON_LOOT_GROUP.encounters) {
    for (const table of enc.tables) {
      for (const line of table) items.add(line.item);
    }
  }
  return [...items].sort((a, b) => a.localeCompare(b));
}
