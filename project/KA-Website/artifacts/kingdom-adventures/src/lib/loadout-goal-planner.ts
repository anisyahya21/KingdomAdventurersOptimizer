/**
 * Gear-goal planner for the loadout builder.
 *
 * Given the stats a resident already has (job curve + valuables), a set of
 * target stats and the gear the job is allowed to wear, it suggests one item per
 * slot (with the level to level it to) so the targets are met.
 *
 * This is a deterministic heuristic over the site's own equipment data - it does
 * not invent values. Contributions are supplied by the caller so the planner
 * uses exactly the same affinity / weak-penalty maths as the loadout totals.
 */

export type GoalMode = "min" | "max" | "range";

export type StatGoal = {
  mode: GoalMode;
  /** `min` mode: at least this much. `max` mode: at most this much. `range`: lower bound. */
  min?: number;
  /** `range` mode only: upper bound. */
  max?: number;
};

export type PlannerSlot = { slot: string; items: string[] };

export type PlannerPick = { slot: string; item: string; level: number };

export type PlannerInput = {
  /** Job curve + valuables (everything that is not equipment). */
  baseStats: Record<string, number>;
  goals: Record<string, StatGoal | undefined>;
  slots: PlannerSlot[];
  /** Stat contribution of one item at one level (same rules as the loadout totals). */
  contribution: (item: string, level: number) => Record<string, number>;
  /** Items the player owns at a fixed level (level is forced). */
  pinnedLevels?: Record<string, number>;
  /** Highest equipment level allowed (the game caps equipment at 99). */
  maxLevel?: number;
};

export type PlannerResult = {
  picks: PlannerPick[];
  stats: Record<string, number>;
  /** Goal stats that are still not satisfied. */
  unmet: string[];
  /** Total violation of the goals (0 means every goal is satisfied). */
  deficit: number;
};

const DEFAULT_MAX_LEVEL = 99;

function violated(goal: StatGoal | undefined, value: number): number {
  if (!goal) return 0;
  if (goal.mode === "min") return Math.max(0, (goal.min ?? 0) - value);
  if (goal.mode === "max") return Math.max(0, value - (goal.max ?? 0));
  const low = goal.min ?? -Infinity;
  const high = goal.max ?? Infinity;
  return Math.max(0, low - value) + Math.max(0, value - high);
}

function goalStats(goals: Record<string, StatGoal | undefined>): string[] {
  return Object.keys(goals).filter((stat) => goals[stat]);
}

function totalDeficit(goals: Record<string, StatGoal | undefined>, stats: Record<string, number>, keys: string[]): number {
  let sum = 0;
  for (const key of keys) sum += violated(goals[key], stats[key] ?? 0);
  return sum;
}

function addContribution(target: Record<string, number>, contribution: Record<string, number>) {
  for (const [stat, value] of Object.entries(contribution)) target[stat] = (target[stat] ?? 0) + value;
}

/**
 * Plan gear for the given goals.
 *
 * Greedy pass over the slots, then a couple of local-refinement passes. Level
 * candidates per item are the ends of the range plus the level that would close
 * each remaining goal, so the suggestion is as cheap as it can be.
 */
export function planGear(input: PlannerInput): PlannerResult {
  const maxLevel = Math.max(1, Math.min(999, Math.floor(input.maxLevel ?? DEFAULT_MAX_LEVEL)));
  const keys = goalStats(input.goals);
  const cache = new Map<string, Record<string, number>>();

  const contributionOf = (item: string, level: number): Record<string, number> => {
    const cacheKey = `${item}|${level}`;
    const hit = cache.get(cacheKey);
    if (hit) return hit;
    const value = input.contribution(item, level) ?? {};
    cache.set(cacheKey, value);
    return value;
  };

  const levelCandidates = (item: string, currentStats: Record<string, number>): number[] => {
    const levels = new Set<number>([1, maxLevel]);
    const pinned = input.pinnedLevels?.[item];
    if (typeof pinned === "number") levels.add(Math.max(1, Math.min(maxLevel, Math.floor(pinned))));

    const atOne = contributionOf(item, 1);
    const atTwo = contributionOf(item, Math.min(2, maxLevel));
    for (const key of keys) {
      const goal = input.goals[key];
      if (!goal) continue;
      const target = goal.mode === "max" ? (goal.max ?? 0) : (goal.min ?? 0);
      const needed = target - (currentStats[key] ?? 0);
      const base = atOne[key] ?? 0;
      const perLevel = (atTwo[key] ?? 0) - base;
      if (needed <= 0 || perLevel <= 0) continue;
      const raw = 1 + (needed - base) / perLevel;
      for (const candidate of [Math.floor(raw), Math.ceil(raw), Math.ceil(raw) + 1]) {
        if (candidate >= 1 && candidate <= maxLevel) levels.add(candidate);
      }
    }
    return [...levels].sort((a, b) => a - b);
  };

  const picks: PlannerPick[] = [];
  const usedItems = new Set<string>();

  const statsWith = (extra: PlannerPick[] | undefined, excludeSlot?: string): Record<string, number> => {
    const stats = { ...input.baseStats };
    for (const pick of picks) {
      if (excludeSlot && pick.slot === excludeSlot) continue;
      addContribution(stats, contributionOf(pick.item, pick.level));
    }
    if (extra) for (const pick of extra) addContribution(stats, contributionOf(pick.item, pick.level));
    return stats;
  };

  const chooseForSlot = (slot: PlannerSlot, fixedSlot?: string): PlannerPick | null => {
    const current = statsWith(undefined, fixedSlot ?? slot.slot);
    const blocked = fixedSlot ?? slot.slot;
    let best: PlannerPick | null = null;
    let bestScore = totalDeficit(input.goals, current, keys);
    for (const item of slot.items) {
      if (usedItems.has(item)) continue;
      const pinned = input.pinnedLevels?.[item];
      const levels = typeof pinned === "number"
        ? [Math.max(1, Math.min(maxLevel, Math.floor(pinned)))]
        : levelCandidates(item, current);
      for (const level of levels) {
        const stats = { ...current };
        addContribution(stats, contributionOf(item, level));
        // Tie-break on the level sum so an equal result prefers cheaper gear.
        const score = totalDeficit(input.goals, stats, keys) + level * 0.0001;
        if (score < bestScore - 1e-9) {
          bestScore = score;
          best = { slot: blocked, item, level };
        }
      }
    }
    return best;
  };

  for (const slot of input.slots) {
    const pick = chooseForSlot(slot);
    if (pick) {
      picks.push(pick);
      usedItems.add(pick.item);
    }
  }

  // Local refinement: re-pick each slot with the others held fixed.
  for (let pass = 0; pass < 3; pass += 1) {
    let improved = false;
    for (const slot of input.slots) {
      const index = picks.findIndex((pick) => pick.slot === slot.slot);
      const previous = index >= 0 ? picks[index] : null;
      if (previous) {
        picks.splice(index, 1);
        usedItems.delete(previous.item);
      }
      const current = statsWith(undefined, slot.slot);
      const before = totalDeficit(input.goals, current, keys);
      const next = chooseForSlot(slot);
      const after = next
        ? totalDeficit(input.goals, (() => { const s = { ...current }; addContribution(s, contributionOf(next.item, next.level)); return s; })(), keys)
        : before;
      if (next && (after < before - 1e-9 || !previous)) {
        picks.push(next);
        usedItems.add(next.item);
        if (after < before - 1e-9) improved = true;
      } else if (previous) {
        picks.push(previous);
        usedItems.add(previous.item);
      }
    }
    if (!improved) break;
  }

  picks.sort((a, b) => input.slots.findIndex((slot) => slot.slot === a.slot) - input.slots.findIndex((slot) => slot.slot === b.slot));
  const stats = statsWith(undefined);
  const unmet = keys.filter((key) => violated(input.goals[key], stats[key] ?? 0) > 0);
  return { picks, stats, unmet, deficit: totalDeficit(input.goals, stats, keys) };
}

/** Human-readable goal, e.g. "≥ 300", "≤ 500" or "30-300". */
export function goalLabel(goal: StatGoal | undefined): string {
  if (!goal) return "-";
  if (goal.mode === "min") return `>= ${goal.min ?? 0}`;
  if (goal.mode === "max") return `<= ${goal.max ?? 0}`;
  return `${goal.min ?? 0}-${goal.max ?? 0}`;
}
