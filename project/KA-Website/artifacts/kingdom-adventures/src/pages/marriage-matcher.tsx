import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearch } from "wouter";
import {
  Plus, Trash2, Zap, RefreshCw, HelpCircle, ArrowLeftRight,
  X, Lock, LockOpen, Loader2, AlertTriangle, ExternalLink,
  Info, Star, Baby, Filter, Heart, Home, BarChart2, BookOpen,
  Download, Upload, Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { SearchableSelect } from "@/components/searchable-select";
import {
  Card, CardContent, CardHeader, CardTitle, CardDescription,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { EntityLink } from "@/components/ka/entity-link";
import { PageHeader } from "@/components/ka/page-header";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { localSharedData } from "@/lib/local-shared-data";
import { apiUrl } from "@/lib/api";
import { battleTypeLabel, typeFromJobCategory } from "@/game-data/job-normalization";
import { MARRIAGE_RANKS, completeMarriagePairs, normJob, pairKey, type MarriageRank } from "@/game-data/job-marriage";
import { getMarriagePair, getPossibleMarriageChildren } from "@/game-data/job-profile";
import { KA_AFFINITY_BADGE_CLASS, KA_RANK_BADGE_CLASS, KA_RANK_BORDER_CLASS, KA_RANK_HEADER_CLASS } from "@/design-system/category-styles";

// âââ API ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ


type StatEntry = { base: number; inc: number; maxLevel?: number };
type RankEntry = { stats: Record<string, StatEntry> };

type JobData = {
  generation: 1 | 2;
  type?: "combat" | "non-combat";
  category?: string;
  icon?: string;
  ranks: Record<string, RankEntry>;
  shield?: "can" | "cannot";
  weaponEquip?: Record<string, "can" | "cannot" | "weak">;
  skillAccess?: { attack?: "can" | "cannot"; casting?: "can" | "cannot" };
  skills: string[];
};

type SharedPair = { id: string; jobA: string; jobB: string; children: string[]; affinityNum?: number };
type MatcherSharedData = {
  jobs: Record<string, JobData>;
  pairs?: SharedPair[];
  marriageMatcher?: {
    rankSlots: Array<{ id: string; rank: string; jobName: string; males: number; females: number; unassigned: number }>;
  } | null;
};

function useSharedData() {
  return useQuery({
    queryKey: ["ka-marriage-reference"],
    // Jobs and compatibility are static reference data bundled with the Vercel app.
    queryFn: async () => localSharedData as unknown as MatcherSharedData,
    initialData: () => localSharedData as unknown as MatcherSharedData,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

async function persistPairs(pairs: SharedPair[], userName: string) {
  try {
    await fetch(apiUrl("/pairs"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data: pairs,
        history: {
          userName: userName || "community",
          changeType: "job",
          itemName: "pairs",
          description: `Updated compatible pairs (${pairs.length} total)`,
        },
      }),
    });
  } catch { /* ignore network errors */ }
}

// âââ Types ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

type Rank = MarriageRank;
const RANKS: Rank[] = MARRIAGE_RANKS;

const RANK_STYLE: Record<Rank, { badge: string; header: string; border: string }> = {
  S: { badge: KA_RANK_BADGE_CLASS.S, header: KA_RANK_HEADER_CLASS.S, border: KA_RANK_BORDER_CLASS.S },
  A: { badge: KA_RANK_BADGE_CLASS.A, header: KA_RANK_HEADER_CLASS.A, border: KA_RANK_BORDER_CLASS.A },
  B: { badge: KA_RANK_BADGE_CLASS.B, header: KA_RANK_HEADER_CLASS.B, border: KA_RANK_BORDER_CLASS.B },
  C: { badge: KA_RANK_BADGE_CLASS.C, header: KA_RANK_HEADER_CLASS.C, border: KA_RANK_BORDER_CLASS.C },
  D: { badge: KA_RANK_BADGE_CLASS.D, header: KA_RANK_HEADER_CLASS.D, border: KA_RANK_BORDER_CLASS.D },
};

const RANK_TABLE_GRID =
  "grid-cols-[minmax(96px,1fr)_44px_44px_52px_20px] sm:grid-cols-[minmax(120px,1fr)_50px_50px_58px_20px] xl:grid-cols-[minmax(160px,1fr)_56px_56px_72px_24px]";

interface RankSlot {
  id: string;
  rank: Rank;
  jobName: string;
  males: number;
  females: number;
  unassigned: number;
}

type RankSlotCountField = "males" | "females" | "unassigned";
type QuickInputResult = { ok: boolean; message: string };

interface Pair {
  id: string;
  jobA: string;
  jobB: string;
  children: string[];
  affinity?: string;
  affinityNum?: number;
}

const AFFINITY_STYLE: Record<string, string> = {
  ...KA_AFFINITY_BADGE_CLASS,
};

interface LockedPair {
  id: string;
  maleJob: string;
  femaleJob: string;
  rank: Rank;
}

interface HousedPair {
  maleJob: string;
  femaleJob: string;
  rank: Rank;
  housed: boolean;
}

interface MatchResult {
  id: string;
  maleJob: string;
  femaleJob: string;
  rank: Rank;
  maleWasUnassigned: boolean;
  femaleWasUnassigned: boolean;
  locked: boolean;
  housed: boolean;
}

interface UnassignedDecision {
  jobName: string;
  rank: Rank;
  assignedMales: number;
  assignedFemales: number;
}

interface OptimalResult {
  matches: MatchResult[];
  unmatchedMale: Array<{ job: string; rank: Rank }>;
  unmatchedFemale: Array<{ job: string; rank: Rank }>;
  unmatchedUnassigned: Array<{ job: string; rank: Rank }>;
  unassignedDecisions: UnassignedDecision[];
}

// âââ Algorithm ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

function runBipartiteMatching(
  effectiveMales: Record<string, number>,
  effectiveFemales: Record<string, number>,
  compatibleKeys: Set<string>
): Array<{ maleJob: string; femaleJob: string }> {
  const maleSlots: string[] = [];
  const femaleSlots: string[] = [];

  for (const [job, count] of Object.entries(effectiveMales)) {
    for (let i = 0; i < count; i++) maleSlots.push(job);
  }
  for (const [job, count] of Object.entries(effectiveFemales)) {
    for (let i = 0; i < count; i++) femaleSlots.push(job);
  }

  const graph: number[][] = maleSlots.map((maleJob) => {
    const edges: number[] = [];
    for (let femaleIndex = 0; femaleIndex < femaleSlots.length; femaleIndex++) {
      if (compatibleKeys.has(pairKey(maleJob, femaleSlots[femaleIndex]))) {
        edges.push(femaleIndex);
      }
    }
    return edges;
  });

  const matchFemaleToMale = new Array<number>(femaleSlots.length).fill(-1);

  function tryMatch(maleIndex: number, visited: boolean[]): boolean {
    for (const femaleIndex of graph[maleIndex]) {
      if (visited[femaleIndex]) continue;
      visited[femaleIndex] = true;

      const currentlyMatchedMale = matchFemaleToMale[femaleIndex];
      if (currentlyMatchedMale === -1 || tryMatch(currentlyMatchedMale, visited)) {
        matchFemaleToMale[femaleIndex] = maleIndex;
        return true;
      }
    }
    return false;
  }

  for (let maleIndex = 0; maleIndex < maleSlots.length; maleIndex++) {
    tryMatch(maleIndex, new Array<boolean>(femaleSlots.length).fill(false));
  }

  const matches: Array<{ maleJob: string; femaleJob: string }> = [];
  for (let femaleIndex = 0; femaleIndex < matchFemaleToMale.length; femaleIndex++) {
    const maleIndex = matchFemaleToMale[femaleIndex];
    if (maleIndex !== -1) {
      matches.push({ maleJob: maleSlots[maleIndex], femaleJob: femaleSlots[femaleIndex] });
    }
  }
  return matches;
}

const MAX_UNASSIGNED_SEARCH_STATES = 150000;
function computeBestUnassignedAllocation(
  withUnassigned: RankSlot[],
  baseMales: Record<string, number>,
  baseFemales: Record<string, number>,
  compatibleKeys: Set<string>
) {
  const totalSlots = withUnassigned.length;
  const totalStates = withUnassigned.reduce((sum, item) => sum * (item.unassigned + 1), 1);

  if (totalSlots === 0 || totalStates <= 20000) {
    return null;
  }

  const allocation: Record<string, { male: number; female: number }> = {};
  for (const s of withUnassigned) allocation[s.jobName] = { male: s.unassigned, female: 0 };

  let bestMatches = runBipartiteMatching(
    withUnassigned.reduce((acc, s) => {
      const amount = baseMales[s.jobName] + allocation[s.jobName].male;
      if (amount > 0) acc[s.jobName] = amount;
      return acc;
    }, { ...baseMales }),
    withUnassigned.reduce((acc, s) => {
      const amount = baseFemales[s.jobName] + allocation[s.jobName].female;
      if (amount > 0) acc[s.jobName] = amount;
      return acc;
    }, { ...baseFemales }),
    compatibleKeys
  );

  let improved = true;
  while (improved) {
    improved = false;
    for (const s of withUnassigned) {
      const current = allocation[s.jobName];
      if (current.male > 0) {
        const candidate = { ...allocation, [s.jobName]: { male: current.male - 1, female: current.female + 1 } };
        const effM = { ...baseMales };
        const effF = { ...baseFemales };
        for (const job of Object.keys(candidate)) {
          const alloc = candidate[job];
          if (alloc.male > 0) effM[job] = (effM[job] ?? 0) + alloc.male;
          if (alloc.female > 0) effF[job] = (effF[job] ?? 0) + alloc.female;
        }
        const candidateMatches = runBipartiteMatching(effM, effF, compatibleKeys);
        if (candidateMatches.length > bestMatches.length) {
          allocation[s.jobName] = candidate[s.jobName];
          bestMatches = candidateMatches;
          improved = true;
        }
      }
    }
  }

  return {
    matches: bestMatches,
    maleExtra: Object.fromEntries(Object.entries(allocation).map(([job, alloc]) => [job, alloc.male])),
    femaleExtra: Object.fromEntries(Object.entries(allocation).map(([job, alloc]) => [job, alloc.female])),
  };
}

function matchRank(
  slots: RankSlot[],
  rank: Rank,
  compatibleKeys: Set<string>,
  lockedForRank: LockedPair[]
): {
  matches: Array<{ maleJob: string; femaleJob: string; maleWasUnassigned: boolean; femaleWasUnassigned: boolean }>;
  unmatchedMale: Array<{ job: string; rank: Rank }>;
  unmatchedFemale: Array<{ job: string; rank: Rank }>;
  unmatchedUnassigned: Array<{ job: string; rank: Rank }>;
  decisions: UnassignedDecision[];
} {
  const rankSlots = slots.filter((s) => s.rank === rank);
  const lockedMaleNeeds = lockedForRank.reduce<Record<string, number>>((acc, lp) => {
    acc[lp.maleJob] = (acc[lp.maleJob] ?? 0) + 1;
    return acc;
  }, {});
  const lockedFemaleNeeds = lockedForRank.reduce<Record<string, number>>((acc, lp) => {
    acc[lp.femaleJob] = (acc[lp.femaleJob] ?? 0) + 1;
    return acc;
  }, {});

  const withUnassigned: RankSlot[] = [];
  const baseMales: Record<string, number> = {};
  const baseFemales: Record<string, number> = {};

  for (const s of rankSlots) {
    let remainingMales = s.males;
    let remainingFemales = s.females;
    let remainingUnassigned = s.unassigned;

    const lockedMale = lockedMaleNeeds[s.jobName] ?? 0;
    const lockedFemale = lockedFemaleNeeds[s.jobName] ?? 0;

    const maleFromAssigned = Math.min(remainingMales, lockedMale);
    remainingMales -= maleFromAssigned;
    const femaleFromAssigned = Math.min(remainingFemales, lockedFemale);
    remainingFemales -= femaleFromAssigned;

    const stillNeeded = Math.max(0, lockedMale - maleFromAssigned) + Math.max(0, lockedFemale - femaleFromAssigned);
    remainingUnassigned = Math.max(0, remainingUnassigned - stillNeeded);

    if (remainingMales > 0) baseMales[s.jobName] = (baseMales[s.jobName] ?? 0) + remainingMales;
    if (remainingFemales > 0) baseFemales[s.jobName] = (baseFemales[s.jobName] ?? 0) + remainingFemales;
    if (remainingUnassigned > 0) withUnassigned.push({ ...s, males: 0, females: 0, unassigned: remainingUnassigned });
  }

  let bestCount = -1;
  let bestMatches: Array<{ maleJob: string; femaleJob: string }> = [];
  let bestMaleExtra: Record<string, number> = {};
  let bestFemaleExtra: Record<string, number> = {};

  const totalSearchStates = withUnassigned.reduce((count, s) => count * (s.unassigned + 1), 1);
  const fallback = totalSearchStates > MAX_UNASSIGNED_SEARCH_STATES
    ? computeBestUnassignedAllocation(withUnassigned, baseMales, baseFemales, compatibleKeys)
    : null;

  if (fallback) {
    bestMatches = fallback.matches;
    bestMaleExtra = fallback.maleExtra;
    bestFemaleExtra = fallback.femaleExtra;
  } else {
    function recurse(idx: number, maleExtra: Record<string, number>, femaleExtra: Record<string, number>) {
      if (idx === withUnassigned.length) {
        const effM = { ...baseMales };
        const effF = { ...baseFemales };
        for (const n in maleExtra) if (maleExtra[n] > 0) effM[n] = (effM[n] ?? 0) + maleExtra[n];
        for (const n in femaleExtra) if (femaleExtra[n] > 0) effF[n] = (effF[n] ?? 0) + femaleExtra[n];
        const matches = runBipartiteMatching(effM, effF, compatibleKeys);
        if (matches.length > bestCount) {
          bestCount = matches.length; bestMatches = matches;
          bestMaleExtra = { ...maleExtra }; bestFemaleExtra = { ...femaleExtra };
        }
        return;
      }
      const s = withUnassigned[idx];
      for (let m = 0; m <= s.unassigned; m++) {
        recurse(idx + 1,
          { ...maleExtra, [s.jobName]: (maleExtra[s.jobName] ?? 0) + m },
          { ...femaleExtra, [s.jobName]: (femaleExtra[s.jobName] ?? 0) + (s.unassigned - m) });
      }
    }
    recurse(0, {}, {});
  }

  const effM = { ...baseMales };
  const effF = { ...baseFemales };
  for (const n in bestMaleExtra) if (bestMaleExtra[n] > 0) effM[n] = (effM[n] ?? 0) + bestMaleExtra[n];
  for (const n in bestFemaleExtra) if (bestFemaleExtra[n] > 0) effF[n] = (effF[n] ?? 0) + bestFemaleExtra[n];

  const matchesWithFlags = bestMatches.map((m) => ({
    ...m,
    maleWasUnassigned: false,
    femaleWasUnassigned: false,
  }));
  const availableBaseMales = { ...baseMales };
  const availableBaseFemales = { ...baseFemales };
  const matchedUnassignedMales: Record<string, number> = {};
  const matchedUnassignedFemales: Record<string, number> = {};
  for (const match of matchesWithFlags) {
    if ((availableBaseMales[match.maleJob] ?? 0) > 0) {
      availableBaseMales[match.maleJob] -= 1;
      match.maleWasUnassigned = false;
    } else {
      match.maleWasUnassigned = true;
      matchedUnassignedMales[match.maleJob] = (matchedUnassignedMales[match.maleJob] ?? 0) + 1;
    }
    if ((availableBaseFemales[match.femaleJob] ?? 0) > 0) {
      availableBaseFemales[match.femaleJob] -= 1;
      match.femaleWasUnassigned = false;
    } else {
      match.femaleWasUnassigned = true;
      matchedUnassignedFemales[match.femaleJob] = (matchedUnassignedFemales[match.femaleJob] ?? 0) + 1;
    }
  }

  const unmatchedMale: Array<{ job: string; rank: Rank }> = [];
  const unmatchedFemale: Array<{ job: string; rank: Rank }> = [];
  const unmatchedUnassigned: Array<{ job: string; rank: Rank }> = [];

  for (const n in effM) {
    const matched = bestMatches.filter((m) => m.maleJob === n).length;
    const baseCount = baseMales[n] ?? 0;
    const unmatchedCount = effM[n] - matched;
    const unmatchedBase = Math.max(0, baseCount - Math.min(baseCount, matched));
    const unmatchedFromUnassigned = Math.max(0, unmatchedCount - unmatchedBase);
    for (let i = 0; i < unmatchedBase; i++) unmatchedMale.push({ job: n, rank });
    for (let i = 0; i < unmatchedFromUnassigned; i++) unmatchedUnassigned.push({ job: n, rank });
  }
  for (const n in effF) {
    const matched = bestMatches.filter((m) => m.femaleJob === n).length;
    const baseCount = baseFemales[n] ?? 0;
    const unmatchedCount = effF[n] - matched;
    const unmatchedBase = Math.max(0, baseCount - Math.min(baseCount, matched));
    const unmatchedFromUnassigned = Math.max(0, unmatchedCount - unmatchedBase);
    for (let i = 0; i < unmatchedBase; i++) unmatchedFemale.push({ job: n, rank });
    for (let i = 0; i < unmatchedFromUnassigned; i++) unmatchedUnassigned.push({ job: n, rank });
  }

  return {
    matches: matchesWithFlags,
    unmatchedMale,
    unmatchedFemale,
    unmatchedUnassigned,
    decisions: withUnassigned.map((s) => ({
      jobName: s.jobName, rank,
      assignedMales: matchedUnassignedMales[s.jobName] ?? 0,
      assignedFemales: matchedUnassignedFemales[s.jobName] ?? 0,
    })),
  };
}

function housedPairKey(rank: Rank, maleJob: string, femaleJob: string) {
  return `${rank}:${pairKey(maleJob, femaleJob)}`;
}

function findOptimalMatching(slots: RankSlot[], pairs: Pair[], lockedPairs: LockedPair[], housedPairs: HousedPair[]): OptimalResult {
  const compatibleKeys = new Set(pairs.map((p) => pairKey(p.jobA, p.jobB)));
  const housedKeys = new Set(housedPairs.filter((entry) => entry.housed)
    .map((entry) => housedPairKey(entry.rank, entry.maleJob, entry.femaleJob)));
  const allMatches: MatchResult[] = [];
  const allUnmatchedMale: Array<{ job: string; rank: Rank }> = [];
  const allUnmatchedFemale: Array<{ job: string; rank: Rank }> = [];
  const allUnmatchedUnassigned: Array<{ job: string; rank: Rank }> = [];
  const allDecisions: UnassignedDecision[] = [];

  for (const lp of lockedPairs) {
    allMatches.push({
      id: lp.id, maleJob: lp.maleJob, femaleJob: lp.femaleJob, rank: lp.rank,
      maleWasUnassigned: false, femaleWasUnassigned: false, locked: true,
      housed: housedKeys.has(housedPairKey(lp.rank, lp.maleJob, lp.femaleJob)),
    });
  }

  for (const rank of RANKS) {
    const lockedForRank = lockedPairs.filter((lp) => lp.rank === rank);
    const res = matchRank(slots, rank, compatibleKeys, lockedForRank);
    allDecisions.push(...res.decisions);

    for (const m of res.matches) {
      allMatches.push({
        id: generateId(), maleJob: m.maleJob, femaleJob: m.femaleJob, rank,
        maleWasUnassigned: m.maleWasUnassigned,
        femaleWasUnassigned: m.femaleWasUnassigned,
        locked: false,
        housed: housedKeys.has(housedPairKey(rank, m.maleJob, m.femaleJob)),
      });
    }
    allUnmatchedMale.push(...res.unmatchedMale);
    allUnmatchedFemale.push(...res.unmatchedFemale);
    allUnmatchedUnassigned.push(...res.unmatchedUnassigned);
  }

  return {
    matches: allMatches,
    unmatchedMale: allUnmatchedMale,
    unmatchedFemale: allUnmatchedFemale,
    unmatchedUnassigned: allUnmatchedUnassigned,
    unassignedDecisions: allDecisions,
  };
}

// âââ Helpers ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

function generateId() { return Math.random().toString(36).slice(2, 9); }
function makePair(a: string, b: string): Pair { return { id: generateId(), jobA: a, jobB: b, children: [] }; }

type PlannerBackup = {
  version: 1;
  exportedAt: string;
  rankSlots: RankSlot[];
  lockedPairs: LockedPair[];
  housedPairs: HousedPair[];
  desiredChildren: string[];
};

function isRank(value: unknown): value is Rank {
  return typeof value === "string" && RANKS.includes(value as Rank);
}

function isRankSlot(value: unknown): value is RankSlot {
  if (!value || typeof value !== "object") return false;
  const slot = value as Partial<RankSlot>;
  return typeof slot.id === "string"
    && isRank(slot.rank)
    && typeof slot.jobName === "string"
    && typeof slot.males === "number"
    && typeof slot.females === "number"
    && typeof slot.unassigned === "number";
}

function isLockedPair(value: unknown): value is LockedPair {
  if (!value || typeof value !== "object") return false;
  const pair = value as Partial<LockedPair>;
  return typeof pair.id === "string"
    && typeof pair.maleJob === "string"
    && typeof pair.femaleJob === "string"
    && isRank(pair.rank);
}

function isPlannerBackup(value: unknown): value is PlannerBackup {
  if (!value || typeof value !== "object") return false;
  const backup = value as Partial<PlannerBackup>;
  return backup.version === 1
    && typeof backup.exportedAt === "string"
    && Array.isArray(backup.rankSlots)
    && backup.rankSlots.every(isRankSlot)
    && Array.isArray(backup.lockedPairs)
    && backup.lockedPairs.every(isLockedPair)
    && Array.isArray(backup.housedPairs)
    && backup.housedPairs.every((entry) => typeof entry === "object" && typeof (entry as any).maleJob === "string" && typeof (entry as any).femaleJob === "string" && isRank((entry as any).rank) && typeof (entry as any).housed === "boolean")
    && Array.isArray(backup.desiredChildren)
    && backup.desiredChildren.every((child) => typeof child === "string");
}

function consumeMatchFromSlots(slots: RankSlot[], match: MatchResult): RankSlot[] {
  let maleTaken = false;
  let femaleTaken = false;
  const maleJobKey = normJob(match.maleJob);
  const femaleJobKey = normJob(match.femaleJob);

  const next = slots.map((slot) => {
    const slotJobKey = normJob(slot.jobName);
    if (slot.rank !== match.rank || (slotJobKey !== maleJobKey && slotJobKey !== femaleJobKey)) {
      return slot;
    }

    let updated = slot;
    const isMaleSlot = slotJobKey === maleJobKey;
    const isFemaleSlot = slotJobKey === femaleJobKey;

    if (isMaleSlot && !maleTaken) {
      if (match.maleWasUnassigned) {
        if (updated.unassigned > 0) {
          updated = { ...updated, unassigned: updated.unassigned - 1 };
          maleTaken = true;
        }
      } else if (updated.males > 0) {
        updated = { ...updated, males: updated.males - 1 };
        maleTaken = true;
      }
    }

    if (isFemaleSlot && !femaleTaken) {
      if (match.femaleWasUnassigned) {
        if (updated.unassigned > 0) {
          updated = { ...updated, unassigned: updated.unassigned - 1 };
          femaleTaken = true;
        }
      } else if (updated.females > 0) {
        updated = { ...updated, females: updated.females - 1 };
        femaleTaken = true;
      }
    }

    return updated;
  });

  if (!maleTaken || !femaleTaken) return slots;
  return next.filter((slot) => slot.males + slot.females + slot.unassigned > 0);
}

function getPossibleChildren(match: MatchResult, pairs: Pair[]): string[] {
  return getPossibleMarriageChildren({ pairs }, match.maleJob, match.femaleJob, { includeParentInheritance: false });
}

function getPossibleChildrenWithInheritance(match: MatchResult, pairs: Pair[]): string[] {
  return getPossibleMarriageChildren({ pairs }, match.maleJob, match.femaleJob);
}

const FALLBACK_JOB_NAMES: string[] = [
  "Archer", "Artisan", "Blacksmith", "Carpenter", "Champion",
  "Cook", "Doctor", "Farmer", "Guard", "Gunner",
  "Knight", "Mage", "Merchant", "Monk", "Mover",
  "Ninja", "Paladin", "Pirate", "Rancher", "Researcher",
  "Samurai", "Trader", "Viking", "Wizard",
].sort();

const DEFAULT_PAIRS: Pair[] = [
  makePair("Artisan", "Champion"), makePair("Artisan", "Guard"), makePair("Artisan", "Ninja"),
  makePair("Blacksmith", "Doctor"), makePair("Blacksmith", "Monk"), makePair("Blacksmith", "Wizard"),
  makePair("Carpenter", "Blacksmith"), makePair("Carpenter", "Mage"), makePair("Carpenter", "Pirate"),
  makePair("Cook", "Guard"), makePair("Doctor", "Champion"),
  makePair("Guard", "Archer"), makePair("Guard", "Champion"), makePair("Guard", "Guard"),
  makePair("Guard", "Ninja"), makePair("Guard", "Paladin"),
  makePair("Knight", "Pirate"), makePair("Mage", "Mage"), makePair("Mage", "Samurai"),
  makePair("Merchant", "Artisan"), makePair("Merchant", "Champion"), makePair("Merchant", "Ninja"),
  makePair("Monk", "Champion"), makePair("Monk", "Guard"), makePair("Monk", "Gunner"), makePair("Monk", "Mage"),
  makePair("Mover", "Archer"), makePair("Mover", "Champion"), makePair("Mover", "Researcher"),
  makePair("Ninja", "Samurai"), makePair("Pirate", "Pirate"),
  makePair("Rancher", "Knight"), makePair("Trader", "Gunner"), makePair("Wizard", "Wizard"),
];

const BUNDLED_PAIRS: Pair[] = ((localSharedData.pairs ?? []) as Pair[])
  .map((pair) => ({ ...pair, children: pair.children ?? [] }));

function normalizeStringList(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
}

function rankSortValue(rank: Rank) {
  return RANKS.indexOf(rank);
}

// âââ RankTable ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

interface RankTableProps {
  rank: Rank;
  slots: RankSlot[];
  availableJobs: string[];
  totalFirstGenCount: number;
  onUpdate: (id: string, field: RankSlotCountField, value: number) => void;
  onRemove: (id: string) => void;
  onAdd: (rank: Rank, jobName: string) => void;
  onQuickAdd: (raw: string, fixedRank?: Rank) => QuickInputResult;
}

function formatCountField(field: RankSlotCountField) {
  if (field === "males") return "Male";
  if (field === "females") return "Female";
  return "Unassigned";
}

function parseQuickCountToken(token: string) {
  const cleaned = token.trim().toLowerCase().replace(/^x/, "").replace(/^\+/, "");
  if (!/^\d+$/.test(cleaned)) return null;
  const parsed = parseInt(cleaned, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseQuickGenderToken(token: string): RankSlotCountField | null {
  const key = token.trim().toLowerCase();
  if (["m", "male", "man", "men", "boy"].includes(key)) return "males";
  if (["f", "female", "woman", "women", "girl"].includes(key)) return "females";
  if (["u", "unassigned", "unknown", "any", "unset"].includes(key)) return "unassigned";
  return null;
}

function matchQuickJob(jobQuery: string, jobNames: string[]): { jobName?: string; error?: string } {
  const query = normJob(jobQuery);
  if (!query) return { error: "Add a job name." };

  const exact = jobNames.find((job) => normJob(job) === query);
  if (exact) return { jobName: exact };

  const startsWithMatches = jobNames.filter((job) => normJob(job).startsWith(query));
  if (startsWithMatches.length === 1) return { jobName: startsWithMatches[0] };
  if (startsWithMatches.length > 1) return { error: `Too many jobs match "${jobQuery}". Type a little more.` };

  const containsMatches = jobNames.filter((job) => normJob(job).includes(query));
  if (containsMatches.length === 1) return { jobName: containsMatches[0] };
  if (containsMatches.length > 1) return { error: `Too many jobs match "${jobQuery}". Type a little more.` };

  return { error: `No job found for "${jobQuery}".` };
}

function parseQuickRankInput(
  raw: string,
  jobNames: string[],
  fixedRank?: Rank,
): { rank: Rank; jobName: string; field: RankSlotCountField; amount: number } | { error: string } {
  const tokens = raw.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return { error: "Type a rank, job, and gender." };

  let rank = fixedRank;
  let field: RankSlotCountField | null = null;
  let amount = 1;
  const jobTokens: string[] = [];

  for (const token of tokens) {
    const lower = token.toLowerCase();
    const maybeRank = token.toUpperCase() as Rank;
    if (!fixedRank && RANKS.includes(maybeRank)) {
      rank = maybeRank;
      continue;
    }
    if (lower === "rank") continue;

    const maybeGender = parseQuickGenderToken(token);
    if (maybeGender) {
      field = maybeGender;
      continue;
    }

    const maybeCount = parseQuickCountToken(token);
    if (maybeCount !== null) {
      amount = maybeCount;
      continue;
    }

    jobTokens.push(token);
  }

  if (!rank) return { error: "Add a rank, like A rank Cook Female." };
  if (!field) return { error: "Add Male, Female, or Unassigned." };

  const jobQuery = jobTokens.join(" ").trim();
  const matched = matchQuickJob(jobQuery, jobNames);
  if (matched.error || !matched.jobName) return { error: matched.error ?? "Pick a valid job." };

  return { rank, jobName: matched.jobName, field, amount };
}

function QuickRankInput({
  fixedRank,
  onSubmit,
  className = "",
}: {
  fixedRank?: Rank;
  onSubmit: (raw: string, fixedRank?: Rank) => QuickInputResult;
  className?: string;
}) {
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<QuickInputResult | null>(null);

  const submit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed) {
      setStatus({ ok: false, message: fixedRank ? "Type a job and gender." : "Type a rank, job, and gender." });
      return;
    }
    const result = onSubmit(trimmed, fixedRank);
    setStatus(result);
    if (result.ok) setValue("");
  }, [fixedRank, onSubmit, value]);

  return (
    <div className={`space-y-1.5 ${className}`}>
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (status) setStatus(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={fixedRank ? "Job + gender" : "Rank + job + gender"}
          className="h-9"
        />
        <Button type="button" size="sm" onClick={submit} className="h-9 shrink-0 gap-1.5">
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
      {status && (
        <p className={`text-xs ${status.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
          {status.message}
        </p>
      )}
    </div>
  );
}

function CountInput({
  value,
  onCommit,
  className = "",
}: {
  value: number;
  onCommit: (value: number) => void;
  className?: string;
}) {
  const [local, setLocal] = useState(value === 0 ? "" : String(value));
  const [focused, setFocused] = useState(false);

  // Only sync from parent when not actively editing
  useEffect(() => {
    if (!focused) {
      setLocal(value === 0 ? "" : String(value));
    }
  }, [value, focused]);

  const commit = useCallback(() => {
    const trimmed = local.trim();
    const parsed = trimmed === "" ? 0 : parseInt(trimmed, 10);
    const next = Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
    onCommit(next);
    setLocal(next === 0 ? "" : String(next));
  }, [local, onCommit]);

  return (
    <Input
      type="text"
      inputMode="numeric"
      pattern="[0-9]*"
      value={local}
      onFocus={(e) => { setFocused(true); e.currentTarget.select(); }}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => { setFocused(false); commit(); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
          e.currentTarget.blur();
        }
      }}
      className={className}
    />
  );
}

function RankTable({ rank, slots, availableJobs, totalFirstGenCount, onUpdate, onRemove, onAdd, onQuickAdd }: RankTableProps) {
  const style = RANK_STYLE[rank];
  const maleTotal = slots.reduce((s, j) => s + j.males, 0);
  const femaleTotal = slots.reduce((s, j) => s + j.females, 0);
  const unassignedTotal = slots.reduce((s, j) => s + j.unassigned, 0);

  return (
    <Card className={`shadow-sm border ${style.border}`}>
      <CardHeader className={`pb-2 pt-3 px-4 rounded-t-lg ${style.header}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge className={`text-sm font-bold px-2.5 py-0.5 border ${style.badge}`}>Rank {rank}</Badge>
            <span className="text-xs text-muted-foreground">{slots.length} job{slots.length !== 1 ? "s" : ""}</span>
          </div>
          {(maleTotal + femaleTotal + unassignedTotal) > 0 && (
            <div className="flex gap-2 text-xs text-muted-foreground">
              {maleTotal > 0 && <span><strong className="text-foreground">{maleTotal}</strong> M</span>}
              {femaleTotal > 0 && <span><strong className="text-foreground">{femaleTotal}</strong> F</span>}
              {unassignedTotal > 0 && <span className="text-amber-600 dark:text-amber-400"><strong>{unassignedTotal}</strong> ⚥</span>}
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-3 pt-0">
        <QuickRankInput fixedRank={rank} onSubmit={onQuickAdd} className="mt-3" />
        {slots.length > 0 && (
          <div className="rounded-md border border-border overflow-hidden mt-3 mb-3 w-full">
            <div className={`grid ${RANK_TABLE_GRID} bg-muted/40 px-2 py-1.5 text-xs font-medium text-muted-foreground sm:px-3`}>
              <span>Job</span>
              <span className="text-center">Male</span>
              <span className="text-center">Female</span>
              <span className="text-center flex min-w-0 items-center justify-center gap-1">
                <span className="truncate">Unassigned</span>
                <Tooltip>
                  <TooltipTrigger asChild><HelpCircle className="w-3 h-3 cursor-help" /></TooltipTrigger>
                  <TooltipContent side="top" className="max-w-48 text-xs">
                    Gender not yet set. The algorithm tries all splits to maximise total matches.
                  </TooltipContent>
                </Tooltip>
              </span>
              <span />
            </div>
            <Separator />
            {slots.map((slot, i) => (
              <div key={slot.id}>
                {i > 0 && <Separator />}
                <div className={`grid ${RANK_TABLE_GRID} items-center gap-1 px-2 py-1.5 sm:px-3`}>
                  <span className="text-sm font-medium truncate pr-2">
                    {slot.jobName}
                    {slot.unassigned > 0 && (
                      <Badge variant="outline" className="ml-1.5 text-[10px] px-1 py-0 border-amber-400 text-amber-600 dark:text-amber-400">⚥</Badge>
                    )}
                  </span>
                  <CountInput value={slot.males} onCommit={(value) => onUpdate(slot.id, "males", value)} className="h-7 text-center text-xs px-1" />
                  <CountInput value={slot.females} onCommit={(value) => onUpdate(slot.id, "females", value)} className="h-7 text-center text-xs px-1" />
                  <CountInput value={slot.unassigned} onCommit={(value) => onUpdate(slot.id, "unassigned", value)} className="h-7 text-center text-xs px-1 border-amber-300 dark:border-amber-700 focus-visible:ring-amber-400" />
                  <button onClick={() => onRemove(slot.id)}
                    className="text-muted-foreground hover:text-destructive transition-colors justify-self-center">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {availableJobs.length > 0 ? (
          <SearchableSelect
            value=""
            clearOnSelect
            onChange={(v) => { if (v) onAdd(rank, v); }}
            options={availableJobs.map((n) => ({ value: n, label: n }))}
            placeholder={`+ Add a job you own at Rank ${rank}…`}
            className="mt-3"
            triggerClassName="h-8 text-sm"
          />
        ) : totalFirstGenCount === 0 ? (
          <p className="text-xs text-muted-foreground text-center mt-3 py-1">
            No Non-Marriage jobs in database yet — add them in the Jobs tool.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground text-center mt-3 py-1">
            All {totalFirstGenCount} Non-Marriage jobs already added to this rank.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Jobs Panel ───────────────────────────────────────────────────────────────

function JobsPanel({ jobNames, isLoading, isFromApi }: { jobNames: string[]; isLoading: boolean; isFromApi: boolean }) {
  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Non-Marriage Jobs</CardTitle>
            <CardDescription className="text-xs mt-0.5">
              Automatically loaded from the Jobs Tool. Only Non-Marriage jobs can be parents.
            </CardDescription>
          </div>
          {isLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground shrink-0" />}
          {!isLoading && isFromApi && (
            <Badge variant="outline" className="text-[10px] border-emerald-400 text-emerald-600 dark:text-emerald-400 shrink-0">Live</Badge>
          )}
          {!isLoading && !isFromApi && (
            <Badge variant="outline" className="text-[10px] border-amber-400 text-amber-600 dark:text-amber-400 shrink-0">Cached</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5 min-h-8">
          {jobNames.length === 0 && !isLoading && (
            <p className="text-xs text-muted-foreground">No Non-Marriage jobs found. Add them in the Jobs Tool first.</p>
          )}
          {jobNames.map((name) => (
            <Badge key={name} variant="secondary" className="text-xs px-2 py-1">{name}</Badge>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">{jobNames.length} Non-Marriage job{jobNames.length !== 1 ? "s" : ""}</p>
      </CardContent>
    </Card>
  );
}

// âââ Pairs Panel ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

const ALL_AFFINITIES = ["A", "B", "C", "D", "E"] as const;

type ChildTypeFilter = "all" | "combat" | "non-combat";
type ExclusiveFilter = "all" | "exclude-exclusive" | "only-exclusive";

interface PairsPanelProps {
  pairs: Pair[];
  jobTypeMap: Record<string, "combat" | "non-combat">;
  jobGenMap: Record<string, 1 | 2>;
  allJobNames: string[];
}

function PairsPanel({ pairs, jobTypeMap, jobGenMap, allJobNames }: PairsPanelProps) {
  const [pairAffinityFilter, setPairAffinityFilter] = useState<Set<string>>(new Set(["A", "B", "C", "D", "E"]));
  const [pairParentTypeFilter, setPairParentTypeFilter] = useState<ChildTypeFilter>("all");
  const [pairChildTypeFilter, setPairChildTypeFilter] = useState<ChildTypeFilter>("all");
  const [pairExclusiveFilter, setPairExclusiveFilter] = useState<ExclusiveFilter>("all");
  const [includeParentInheritance, setIncludeParentInheritance] = useState(true);
  const [pairParentFilter, setPairParentFilter] = useState<string[]>([]);
  const [pairChildFilter, setPairChildFilter] = useState<string[]>([]);

  const addPairParentFilter = (job: string) => {
    setPairParentFilter((prev) => prev.some((item) => normJob(item) === normJob(job)) ? prev : [...prev, job].sort());
  };
  const removePairParentFilter = (job: string) => {
    setPairParentFilter((prev) => prev.filter((item) => normJob(item) !== normJob(job)));
  };
  const addPairChildFilter = (job: string) => {
    setPairChildFilter((prev) => prev.some((item) => normJob(item) === normJob(job)) ? prev : [...prev, job].sort());
  };
  const removePairChildFilter = (job: string) => {
    setPairChildFilter((prev) => prev.filter((item) => normJob(item) !== normJob(job)));
  };

  const toggleAffinity = (aff: string) => {
    const next = new Set(pairAffinityFilter);
    if (next.has(aff)) next.delete(aff);
    else next.add(aff);
    setPairAffinityFilter(next);
  };

  const filtered = pairs.filter((p) => {
    if (p.affinity && !pairAffinityFilter.has(p.affinity)) return false;

    const possibleChildren = getPossibleMarriageChildren({ pairs }, p.jobA, p.jobB, { includeParentInheritance });

    if (pairParentFilter.length > 0) {
      const parentMatch = pairParentFilter.some((job) =>
        normJob(p.jobA) === normJob(job) || normJob(p.jobB) === normJob(job)
      );
      if (!parentMatch) return false;
    }

    if (pairParentTypeFilter !== "all") {
      // Parent-type filtering depends on the current jobTypeMap. See
      // data/sheet-research/notes/job-notes.md for the current CSV-vs-app
      // classification findings and known mismatches.
      const parentTypeMatch =
        jobTypeMap[p.jobA] === pairParentTypeFilter &&
        jobTypeMap[p.jobB] === pairParentTypeFilter;
      if (!parentTypeMatch) return false;
    }

    if (pairChildFilter.length > 0) {
      const childMatch = possibleChildren.some((child) =>
        pairChildFilter.some((job) => normJob(child) === normJob(job))
      );
      if (!childMatch) return false;
    }

    const typeOk = pairChildTypeFilter === "all"
      || possibleChildren.some((child) => jobTypeMap[child] === pairChildTypeFilter);
    if (!typeOk) return false;

    const exclusiveOk = pairExclusiveFilter === "all"
      || possibleChildren.some((child) =>
        pairExclusiveFilter === "exclude-exclusive" ? jobGenMap[child] !== 2 : jobGenMap[child] === 2
      );
    if (!exclusiveOk) return false;

    return true;
  });

  const sorted = [...filtered].sort((a, b) => pairKey(a.jobA, a.jobB).localeCompare(pairKey(b.jobA, b.jobB)));
  const parentOptions = allJobNames
    .filter((job) => jobGenMap[job] === 1)
    .sort((a, b) => a.localeCompare(b))
    .map((job) => ({ value: job, label: job }));

  const childOptions = [...new Set([
    ...allJobNames,
    ...pairs.flatMap((p) => getPossibleMarriageChildren({ pairs }, p.jobA, p.jobB)),
  ])].sort((a, b) => a.localeCompare(b)).map((job) => ({ value: job, label: job }));

  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Compatible Pairs & Children</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)] items-start">
        <div className="space-y-3 rounded-lg border border-border bg-background/40 p-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Affinity:</span>
            {ALL_AFFINITIES.map((aff) => {
              const active = pairAffinityFilter.has(aff);
              return (
                <button
                  key={aff}
                  onClick={() => toggleAffinity(aff)}
                  className={`text-xs font-bold px-2 py-0.5 rounded border transition-all ${active ? (AFFINITY_STYLE[aff] ?? "bg-muted border-border text-foreground") : "bg-transparent border-border text-muted-foreground opacity-40"}`}
                >
                  {aff}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div className="space-y-2">
              <span className="text-xs text-muted-foreground font-medium shrink-0">Parent job:</span>
              <div className="flex flex-wrap gap-2 min-h-[34px] items-center">
                {pairParentFilter.length > 0 ? pairParentFilter.map((job) => (
                  <Badge key={job} variant="outline" className="text-xs gap-1 px-2 py-0.5">
                    {job}
                    <button type="button" onClick={() => removePairParentFilter(job)} className="text-muted-foreground hover:text-foreground transition-colors">
                      <X className="w-3 h-3" />
                    </button>
                  </Badge>
                )) : (
                  <span className="text-xs text-muted-foreground">All parents</span>
                )}
              </div>
              <SearchableSelect
                value=""
                clearOnSelect
                onChange={(v) => { if (v) addPairParentFilter(v); }}
                options={parentOptions.filter((option) => !pairParentFilter.some((selected) => normJob(selected) === normJob(option.value)))}
                placeholder="+ Add parent…"
                triggerClassName="h-8 text-sm"
              />
            </div>
            <div className="space-y-2">
              <span className="text-xs text-muted-foreground font-medium shrink-0">Child job:</span>
              <div className="flex flex-wrap gap-2 min-h-[34px] items-center">
                {pairChildFilter.length > 0 ? pairChildFilter.map((job) => (
                  <Badge key={job} variant="outline" className="text-xs gap-1 px-2 py-0.5">
                    {job}
                    <button type="button" onClick={() => removePairChildFilter(job)} className="text-muted-foreground hover:text-foreground transition-colors">
                      <X className="w-3 h-3" />
                    </button>
                  </Badge>
                )) : (
                  <span className="text-xs text-muted-foreground">All children</span>
                )}
              </div>
              <SearchableSelect
                value=""
                clearOnSelect
                onChange={(v) => { if (v) addPairChildFilter(v); }}
                options={childOptions.filter((option) => !pairChildFilter.some((selected) => normJob(selected) === normJob(option.value)))}
                placeholder="+ Add child…"
                triggerClassName="h-8 text-sm"
              />
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Parent Battle-Type:</span>
            <div className="flex rounded-md overflow-hidden border border-input">
              {(["all", "combat", "non-combat"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setPairParentTypeFilter(mode)}
                  className={`px-2.5 h-7 text-[11px] font-medium transition-colors ${
                    pairParentTypeFilter === mode
                      ? mode === "combat"
                        ? "bg-red-500 text-white"
                        : mode === "non-combat"
                          ? "bg-sky-500 text-white"
                          : "bg-primary text-primary-foreground"
                      : "bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {battleTypeLabel(mode)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Child Battle-Type:</span>
            <div className="flex rounded-md overflow-hidden border border-input">
              {(["all", "combat", "non-combat"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setPairChildTypeFilter(mode)}
                  className={`px-2.5 h-7 text-[11px] font-medium transition-colors ${
                    pairChildTypeFilter === mode
                      ? mode === "combat"
                        ? "bg-red-500 text-white"
                        : mode === "non-combat"
                          ? "bg-sky-500 text-white"
                          : "bg-primary text-primary-foreground"
                      : "bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {battleTypeLabel(mode)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Marriage Exclusive:</span>
            <div className="flex rounded-md overflow-hidden border border-input">
              {([
                { value: "all", label: "All" },
                { value: "only-exclusive", label: "Marriage Exclusive" },
                { value: "exclude-exclusive", label: "Non-Marriage Exclusive" },
              ] as const).map((mode) => (
                <button
                  key={mode.value}
                  onClick={() => setPairExclusiveFilter(mode.value)}
                  className={`px-2.5 h-7 text-[11px] font-medium transition-colors ${
                    pairExclusiveFilter === mode.value
                      ? "bg-orange-500 text-white"
                      : "bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Parent inheritance:</span>
            <button
              onClick={() => setIncludeParentInheritance((v) => !v)}
              className={`px-2.5 h-7 text-[11px] font-medium rounded-md border transition-colors ${
                includeParentInheritance
                  ? "bg-emerald-500 text-white border-emerald-500"
                  : "bg-background text-muted-foreground border-input hover:text-foreground"
              }`}
            >
              {includeParentInheritance ? "Included" : "Ignored"}
            </button>
            <span className="text-[11px] text-muted-foreground">
              When included, parent jobs count as possible child outcomes.
            </span>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">
              Showing {filtered.length} of {pairs.length} pairs.
            </span>
            {(pairParentFilter.length > 0 || pairChildFilter.length > 0 || pairParentTypeFilter !== "all") && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPairParentFilter([]);
                  setPairChildFilter([]);
                  setPairParentTypeFilter("all");
                }}
                className="h-6 px-2 text-xs"
              >
                Clear filters
              </Button>
            )}
          </div>
        </div>
        <div className="rounded-md border border-border overflow-hidden min-w-0">
          <div className="max-h-80 overflow-y-auto divide-y divide-border">
            {sorted.length === 0 && <p className="text-xs text-muted-foreground text-center py-6">No pairs defined yet.</p>}
            {sorted.map((p) => {
              const [d1, d2] = [p.jobA, p.jobB].sort();
              const sortedChildren = getPossibleMarriageChildren({ pairs }, p.jobA, p.jobB, { includeParentInheritance });
              return (
                <div key={p.id} className="px-3 py-2 hover:bg-muted/30 transition-colors">
                  <div className="flex items-center gap-2 text-sm min-w-0 flex-1">
                    {p.affinity && (
                      <span className={`text-[10px] font-bold px-1 py-0 rounded border shrink-0 ${AFFINITY_STYLE[p.affinity] ?? "bg-muted border-border"}`}>{p.affinity}</span>
                    )}
                    <EntityLink type="job" name={d1} className="font-medium truncate hover:no-underline">
                      {d1}
                    </EntityLink>
                    <ArrowLeftRight className="w-3 h-3 text-muted-foreground shrink-0" />
                    <EntityLink type="job" name={d2} className="font-medium truncate hover:no-underline">
                      {d2}
                    </EntityLink>
                  </div>
                  {sortedChildren.length > 0 && (
                    <div className="mt-1.5 ml-6 flex flex-wrap gap-1.5">
                      {sortedChildren.map((c) => (
                        <Badge key={c} variant="secondary" className="text-xs gap-1.5 px-2 py-1">
                          <Baby className="w-3 h-3 text-violet-500" />
                          <EntityLink type="job" name={c} className="hover:no-underline">
                            {c}
                          </EntityLink>
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{pairs.length} pair{pairs.length !== 1 ? "s" : ""} loaded.</p>
      </CardContent>
    </Card>
  );
}

interface PlannerSetupProps {
  affinityFilter: Set<string>;
  onAffinityFilterChange: (aff: Set<string>) => void;
  targetChildTypeFilter: ChildTypeFilter;
  onTargetChildTypeFilterChange: (mode: ChildTypeFilter) => void;
  targetExclusiveFilter: ExclusiveFilter;
  onTargetExclusiveFilterChange: (mode: ExclusiveFilter) => void;
  targetIncludeJobs: string[];
  onAddIncludeJob: (job: string) => void;
  onRemoveIncludeJob: (job: string) => void;
  targetExcludeJobs: string[];
  onAddExcludeJob: (job: string) => void;
  onRemoveExcludeJob: (job: string) => void;
  targetPoolJobs: string[];
  onResetSetup: () => void;
  allJobNames: string[];
}

function PlannerSetup({
  affinityFilter,
  onAffinityFilterChange,
  targetChildTypeFilter,
  onTargetChildTypeFilterChange,
  targetExclusiveFilter,
  onTargetExclusiveFilterChange,
  targetIncludeJobs,
  onAddIncludeJob,
  onRemoveIncludeJob,
  targetExcludeJobs,
  onAddExcludeJob,
  onRemoveExcludeJob,
  targetPoolJobs,
  onResetSetup,
  allJobNames,
}: PlannerSetupProps) {
  const toggleAffinity = (aff: string) => {
    const next = new Set(affinityFilter);
    if (next.has(aff)) next.delete(aff);
    else next.add(aff);
    onAffinityFilterChange(next);
  };

  return (
    <Card className="shadow-sm border-primary/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Planner Setup</CardTitle>
        <CardDescription className="text-xs">
          Choose the affinities and child targets you want to plan around before calculating.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Allowed affinity:</span>
            {ALL_AFFINITIES.map((aff) => {
              const active = affinityFilter.has(aff);
              return (
                <button
                  key={aff}
                  onClick={() => toggleAffinity(aff)}
                  className={`text-xs font-bold px-2 py-0.5 rounded border transition-all ${active ? (AFFINITY_STYLE[aff] ?? "bg-muted border-border text-foreground") : "bg-transparent border-border text-muted-foreground opacity-40"}`}
                >
                  {aff}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-muted-foreground">
            Only pairs with these compatibility ranks will be considered during calculation.
          </p>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Target child pool:</span>
            <div className="flex rounded-md overflow-hidden border border-input">
              {(["all", "combat", "non-combat"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => onTargetChildTypeFilterChange(mode)}
                  className={`px-2.5 h-7 text-[11px] font-medium transition-colors ${
                    targetChildTypeFilter === mode
                      ? mode === "combat"
                        ? "bg-red-500 text-white"
                        : mode === "non-combat"
                          ? "bg-sky-500 text-white"
                          : "bg-primary text-primary-foreground"
                      : "bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {battleTypeLabel(mode)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Marriage Exclusive:</span>
            <div className="flex rounded-md overflow-hidden border border-input">
              {([
                { value: "all", label: "All" },
                { value: "only-exclusive", label: "Marriage Exclusive" },
                { value: "exclude-exclusive", label: "Non-Marriage Exclusive" },
              ] as const).map((mode) => (
                <button
                  key={mode.value}
                  onClick={() => onTargetExclusiveFilterChange(mode.value)}
                  className={`px-2.5 h-7 text-[11px] font-medium transition-colors ${
                    targetExclusiveFilter === mode.value
                      ? "bg-orange-500 text-white"
                      : "bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-xs text-muted-foreground font-medium">Include:</span>
            {targetIncludeJobs.map((job) => (
              <Badge key={job} className="gap-1 px-2 py-0.5 text-xs bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-700 border">
                {job}
                <button onClick={() => onRemoveIncludeJob(job)}><X className="w-2.5 h-2.5 ml-0.5" /></button>
              </Badge>
            ))}
            <SearchableSelect
              value=""
              clearOnSelect
              onChange={(v) => { if (v) onAddIncludeJob(v); }}
              options={allJobNames
                .filter((job) => !targetIncludeJobs.includes(job))
                .sort()
                .map((job) => ({ value: job, label: job }))}
              placeholder="+ Include job"
              triggerClassName="h-6 text-xs"
            />
          </div>

          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-xs text-muted-foreground font-medium">Exclude:</span>
            {targetExcludeJobs.map((job) => (
              <Badge key={job} className="gap-1 px-2 py-0.5 text-xs bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-300 dark:border-red-700 border">
                {job}
                <button onClick={() => onRemoveExcludeJob(job)}><X className="w-2.5 h-2.5 ml-0.5" /></button>
              </Badge>
            ))}
            <SearchableSelect
              value=""
              clearOnSelect
              onChange={(v) => { if (v) onAddExcludeJob(v); }}
              options={allJobNames
                .filter((job) => !targetExcludeJobs.includes(job))
                .sort()
                .map((job) => ({ value: job, label: job }))}
              placeholder="+ Exclude job"
              triggerClassName="h-6 text-xs"
            />
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground font-medium shrink-0">Setup:</span>
            <Button size="sm" variant="ghost" onClick={onResetSetup} className="h-7 text-xs">
              Reset Setup
            </Button>
          </div>

          <div className="rounded-lg border border-border bg-muted/30 px-3 py-2 space-y-2">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <p className="text-xs text-muted-foreground">
                Current target pool: <span className="font-medium text-foreground">{targetPoolJobs.length}</span> job{targetPoolJobs.length !== 1 ? "s" : ""}
              </p>
            </div>
            {targetPoolJobs.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {targetPoolJobs.slice(0, 18).map((job) => (
                  <Badge key={job} variant="outline" className="text-[10px] px-1.5 py-0">{job}</Badge>
                ))}
                {targetPoolJobs.length > 18 && (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                    +{targetPoolJobs.length - 18} more
                  </Badge>
                )}
              </div>
            )}
            <p className="text-[11px] text-muted-foreground">
              The planner now uses this target pool directly when checking desired child coverage.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// âââ Match Row ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

interface MatchRowProps {
  match: MatchResult;
  index: number;
  rankJobNames: string[];
  pairs: Pair[];
  desiredChildren: string[];
  onMarkMarried: (match: MatchResult) => void;
  onLock: (id: string) => void;
  onUnlock: (id: string) => void;
  onToggleHoused: (id: string) => void;
  onChangeMale: (id: string, job: string) => void;
  onChangeFemale: (id: string, job: string) => void;
}

function MatchRow({ match, index, rankJobNames, pairs, desiredChildren, onMarkMarried, onLock, onUnlock, onToggleHoused, onChangeMale, onChangeFemale }: MatchRowProps) {
  const isLocked = match.locked;

  const possibleChildren = useMemo(() => getPossibleChildrenWithInheritance(match, pairs), [match, pairs]);
  const desiredHere = useMemo(
    () => desiredChildren.filter((c) => possibleChildren.some((p) => normJob(p) === normJob(c))),
    [desiredChildren, possibleChildren]
  );
  const hasDesired = desiredHere.length > 0;

  return (
    <div className={`rounded-lg px-3 py-2 transition-all ${
      isLocked
        ? "bg-amber-50 border border-amber-300 dark:bg-amber-950/30 dark:border-amber-700"
        : hasDesired
          ? "bg-violet-50 border border-violet-200 dark:bg-violet-950/20 dark:border-violet-800"
          : "bg-accent/50"
    }`}>
      <div className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-x-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-5 h-5 rounded-full bg-secondary text-secondary-foreground text-xs flex items-center justify-center font-bold shrink-0">{index + 1}</span>
          {isLocked ? (
            <SearchableSelect
              value={match.maleJob}
              onChange={(v) => onChangeMale(match.id, v)}
              options={rankJobNames.map((n) => ({ value: n, label: n }))}
              className="flex-1 min-w-0"
              triggerClassName="h-7 text-sm"
            />
          ) : (
            <span className="flex items-center gap-1.5 min-w-0">
              <img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-4 h-5 shrink-0" style={{imageRendering: "pixelated"}} />
              <EntityLink type="job" name={match.maleJob} className="font-medium text-sm truncate hover:no-underline">
                {match.maleJob}
              </EntityLink>
              {match.maleWasUnassigned && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className="text-[10px] px-1 py-0 border-amber-400 text-amber-600 dark:text-amber-400 shrink-0 cursor-help">unassigned</Badge>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">This slot's gender was decided by the algorithm</TooltipContent>
                </Tooltip>
              )}
            </span>
          )}
        </div>
        {isLocked
          ? <Lock className="w-3.5 h-3.5 text-amber-500 shrink-0" />
          : <ArrowLeftRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
        {isLocked ? (
            <SearchableSelect
              value={match.femaleJob}
              onChange={(v) => onChangeFemale(match.id, v)}
              options={rankJobNames.map((n) => ({ value: n, label: n }))}
              className="flex-1 min-w-0"
              triggerClassName="h-7 text-sm"
            />
        ) : (
          <span className="flex items-center gap-1.5 min-w-0">
            <img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-4 h-5 shrink-0" style={{imageRendering: "pixelated"}} />
            <EntityLink type="job" name={match.femaleJob} className="font-medium text-sm text-rose-600 dark:text-rose-400 truncate hover:no-underline">
              {match.femaleJob}
            </EntityLink>
            {match.femaleWasUnassigned && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className="text-[10px] px-1 py-0 border-amber-400 text-amber-600 dark:text-amber-400 shrink-0 cursor-help">unassigned</Badge>
                </TooltipTrigger>
                <TooltipContent side="top" className="text-xs">This slot's gender was decided by the algorithm</TooltipContent>
              </Tooltip>
            )}
          </span>
        )}
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={() => onToggleHoused(match.id)}
                className={`shrink-0 p-1 rounded transition-colors ${match.housed ? "text-emerald-500 hover:text-emerald-700 dark:hover:text-emerald-300" : "text-muted-foreground hover:text-foreground"}`}>
                <Home className="w-3.5 h-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {match.housed ? "Already housed together" : "Mark as housed together"}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={() => isLocked ? onUnlock(match.id) : onLock(match.id)}
                className={`shrink-0 p-1 rounded transition-colors ${isLocked ? "text-amber-500 hover:text-amber-700 dark:hover:text-amber-300" : "text-muted-foreground hover:text-foreground"}`}>
                {isLocked ? <Lock className="w-3.5 h-3.5" /> : <LockOpen className="w-3.5 h-3.5" />}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {isLocked ? "Unlock — let algorithm reassign" : "Lock this pair for future calculations"}
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Children and Mark as Married row (side by side) */}
      {(possibleChildren.length > 0 || true) && (
        <div className="mt-2 flex flex-row flex-wrap items-center justify-between pl-7 gap-2">
          <div className="flex items-center gap-1.5 flex-wrap min-w-0">
            {possibleChildren.length > 0 && <>
              <Baby className="w-3 h-3 text-muted-foreground shrink-0" />
              <span className="text-[11px] text-muted-foreground">Child can be:</span>
              {possibleChildren.map((c) => {
                const isPriority = desiredChildren.some((d) => normJob(d) === normJob(c));
                return (
                  <Badge key={c} variant="outline" className={`text-[10px] px-1.5 py-0 ${isPriority ? "border-violet-400 text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-950/30" : "text-muted-foreground"}`}>
                    {isPriority && <Star className="w-2.5 h-2.5 mr-0.5 text-violet-500" />}
                    <EntityLink type="job" name={c} className="hover:no-underline">
                      {c}
                    </EntityLink>
                  </Badge>
                );
              })}
            </>}
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-xs whitespace-nowrap"
            onClick={() => onMarkMarried(match)}
          >
            <Check className="w-3.5 h-3.5" />
            Mark as married
          </Button>
        </div>
      )}
    </div>
  );
}

// âââ Info Dialog ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

function InfoDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="icon" className="h-8 w-8">
          <Info className="w-4 h-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>How to use Match Finder</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm text-muted-foreground leading-relaxed">
          <div>
            <h3 className="font-semibold text-foreground mb-1">Compatibility vs Character Rank</h3>
            <p>This tool helps you plan optimal marriages using the loaded compatible pairs. Character rank (S/A/B/C/D) is separate and refers to how leveled up your character is, and it is not the same as marriage compatibility.</p>
          </div>
          <div>
            <h3 className="font-semibold text-foreground mb-1">1. Jobs (auto-loaded)</h3>
            <p>Non-Marriage jobs are loaded automatically from the <strong>Jobs Tool</strong>. Only Non-Marriage jobs can be parents.</p>
          </div>
          <div>
            <h3 className="font-semibold text-foreground mb-1">2. Assign to character rank tables</h3>
            <p>For each character rank (S through D), add the relevant jobs and enter how many <strong>male</strong> / <strong>female</strong> characters you have at that rank. Use <strong>Unassigned</strong> for undecided genders — the algorithm splits them to maximise matches.</p>
          </div>
          <div>
            <h3 className="font-semibold text-foreground mb-1">3. Compatible pairs & children</h3>
            <p>Review which job pairs can marry in the reference section below the calculator.</p>
          </div>
          <div>
            <h3 className="font-semibold text-foreground mb-1">4. Child targets</h3>
            <p>Use Planner Setup to target the child jobs you want. After calculating, results highlight matches that can produce your desired children through parent inheritance or a defined outcome child. Monarch is excluded from parent inheritance and only produces Royal.</p>
          </div>
          <div>
            <h3 className="font-semibold text-foreground mb-1">5. Calculate & lock</h3>
            <p>Click <strong>Calculate</strong> to find the optimal matching. Lock pairs with ð to keep them fixed across recalculations.</p>
          </div>
          <div className="rounded-lg bg-muted px-3 py-2 text-xs">
            <strong className="text-foreground">Private to this browser:</strong> Rank assignments, locks, and filters are saved only on this device.
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// âââ Main Component âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
// ─── Marriage Simulator ───────────────────────────────────────────────────────

const SIM_RANKS = ["D", "C", "B", "A", "S"] as const;
type SimRank = typeof SIM_RANKS[number];

const SAME_RANK_BONUS: Record<SimRank, number> = { D: 1, C: 2, B: 3, A: 4, S: 5 };
const SIM_RANK_VALUE: Record<SimRank, number> = { D: 1, C: 2, B: 3, A: 4, S: 5 };
const SIM_VALUE_RANK: Record<number, SimRank> = { 1: "D", 2: "C", 3: "B", 4: "A", 5: "S" };
const MAX_SIM_STAT_LEVEL = 999;

const AFFINITY_NUM_TO_LETTER: Record<number, string> = {
  100: "A", 95: "B", 90: "B", 80: "C", 75: "D", 70: "D", 65: "E", 60: "E",
};

function calcChildRank(fatherRank: SimRank, motherRank: SimRank): SimRank {
  const averageRank = Math.round((SIM_RANK_VALUE[fatherRank] + SIM_RANK_VALUE[motherRank]) / 2);
  const sameRankBonus = fatherRank === motherRank ? 1 : 0;
  return SIM_VALUE_RANK[Math.min(5, averageRank + sameRankBonus)];
}

const SIM_STATS: Array<{ key: string; label: string }> = [
  { key: "HP",           label: "HP"    },
  { key: "MP",           label: "MP"    },
  { key: "Vigor",        label: "Vigor" },
  { key: "Attack",       label: "ATK"   },
  { key: "Defence",      label: "DEF"   },
  { key: "Speed",        label: "SPD"   },
  { key: "Luck",         label: "LCK"   },
  { key: "Intelligence", label: "INT"   },
  { key: "Dexterity",    label: "DEX"   },
  { key: "Gather",       label: "CONS"  },
  { key: "Move",         label: "MOVE"  },
  { key: "Heart",        label: "Heart" },
];

interface SimResult {
  childJob: string;
  childRank: SimRank;
  affinityLetter: string;
  affinityNum: number;
  childAwakening: number;
  childStats: Array<{ key: string; label: string; base: number; inc: number; maxLevel: number | null; maxValue: number | null }>;
}

type StatData = { base: number; inc: number; maxLevel?: number };

function buildSimStats(
  jobName: string,
  rank: SimRank,
  awakening: number,
  jobs: Record<string, JobData>
): Array<{ key: string; label: string; base: number; inc: number; maxLevel: number | null; maxValue: number | null }> {
  const rankData = (jobs[jobName]?.ranks as Record<string, { stats: Record<string, StatData> }>)?.[rank];

  return SIM_STATS.map(({ key, label }) => {
    const stat = rankData?.stats?.[key];
    if (!stat) return { key, label, base: 0, inc: 0, maxLevel: null, maxValue: null };
    const baseMaxLevel = stat.maxLevel ?? null;
    const maxLevel = baseMaxLevel !== null ? Math.min(MAX_SIM_STAT_LEVEL, baseMaxLevel + 30 * awakening) : null;
    const maxValue = maxLevel !== null ? stat.base + stat.inc * (maxLevel - 1) : null;
    return { key, label, base: stat.base, inc: stat.inc, maxLevel, maxValue };
  });
}

function calcSim(
  fatherJob: string, motherJob: string,
  fatherRank: SimRank, motherRank: SimRank,
  fatherAwk: number, motherAwk: number,
  pairs: Pair[],
  jobs: Record<string, JobData>,
): SimResult | { error: string } {
  const pair = getMarriagePair({ pairs }, fatherJob, motherJob);
  if (!pair) return { error: "This pair has no compatible marriage data." };

  const childJobName = getPossibleMarriageChildren(
    { pairs },
    fatherJob,
    motherJob,
    { includeParentInheritance: false },
  )[0] ?? null;
  if (!childJobName) return { error: "No child job found for this pair." };

  const childRank = calcChildRank(fatherRank, motherRank);

  const affinityNum = (pair as any).affinityNum as number | undefined;
  if (!affinityNum) return { error: "Affinity number missing — re-run the migration script." };
  const affinityLetter = AFFINITY_NUM_TO_LETTER[affinityNum] ?? pair.affinity ?? "?";

  const sameRankBonus = fatherRank === motherRank ? SAME_RANK_BONUS[fatherRank] : 0;
  const childAwakening = Math.floor((affinityNum * (fatherAwk + motherAwk + sameRankBonus)) / 100);

  return {
    childJob: childJobName,
    childRank,
    affinityLetter,
    affinityNum,
    childAwakening,
    childStats: buildSimStats(childJobName, childRank, childAwakening, jobs),
  };
}

const SIM_RANK_STYLE: Record<SimRank, string> = {
  ...KA_RANK_BADGE_CLASS,
};

function SimAwkInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [local, setLocal] = useState(String(value));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setLocal(String(value));
  }, [value, focused]);

  const commit = useCallback(() => {
    const n = parseInt(local, 10);
    onChange(isNaN(n) ? 0 : Math.max(0, n));
  }, [local, onChange]);

  return (
    <Input
      type="text"
      inputMode="numeric"
      value={local}
      placeholder="0"
      onFocus={(e) => { setFocused(true); e.currentTarget.select(); }}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => { setFocused(false); commit(); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit();
          e.currentTarget.blur();
        }
      }}
      className="h-8 text-center text-sm"
    />
  );
}

function SimTab({
  pairs, jobs, firstGenJobNames,
}: {
  pairs: Pair[];
  jobs: Record<string, JobData>;
  firstGenJobNames: string[];
}) {
  type SimState = {
    fatherJob: string;
    motherJob: string;
    fatherRank: SimRank;
    motherRank: SimRank;
    fatherAwk: number;
    motherAwk: number;
    statSource: "child" | "father" | "mother";
  };

  const createSimState = (): SimState => ({
    fatherJob: "",
    motherJob: "",
    fatherRank: "S",
    motherRank: "S",
    fatherAwk: 0,
    motherAwk: 0,
    statSource: "child",
  });

  const [simOne, setSimOne] = useState<SimState>(createSimState);
  const [simTwo, setSimTwo] = useState<SimState>(createSimState);

  const jobOptions = firstGenJobNames.map((n) => ({ value: n, label: n }));

  const calcForState = useCallback((state: SimState) => {
    if (!state.fatherJob || !state.motherJob) return null;
    return calcSim(
      state.fatherJob,
      state.motherJob,
      state.fatherRank,
      state.motherRank,
      state.fatherAwk,
      state.motherAwk,
      pairs,
      jobs
    );
  }, [jobs, pairs]);

  const resultOne = useMemo(() => calcForState(simOne), [calcForState, simOne]);
  const resultTwo = useMemo(() => calcForState(simTwo), [calcForState, simTwo]);

  const [previewPoseFrame, setPreviewPoseFrame] = useState<0 | 2>(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setPreviewPoseFrame((prev) => (prev === 0 ? 2 : 0));
    }, 500);
    return () => window.clearInterval(timer);
  }, []);

  const displayStatsFor = useCallback((state: SimState, result: SimResult | { error: string } | null) => {
    if (!result || "error" in result) return [];
    if (state.statSource === "child") {
      return result.childStats;
    }
    const sourceJob = state.statSource === "father" ? state.fatherJob : state.motherJob;
    return buildSimStats(sourceJob, result.childRank, result.childAwakening, jobs);
  }, [jobs]);

  const displayStatsOne = useMemo(() => displayStatsFor(simOne, resultOne), [displayStatsFor, resultOne, simOne]);
  const displayStatsTwo = useMemo(() => displayStatsFor(simTwo, resultTwo), [displayStatsFor, resultTwo, simTwo]);

  function CroppedBabyIcon({ className = "h-4 w-4" }: { className?: string }) {
    return (
      <span className={`${className} inline-block overflow-hidden align-[-2px]`} aria-hidden="true">
        <img
          src="/website_icons/requested/baby.png"
          alt=""
          className="h-full w-auto max-w-none object-left"
          style={{ imageRendering: "pixelated" }}
        />
      </span>
    );
  }

  function JobSourceIconButton({
    selected,
    onClick,
    jobName,
    rank,
    poseFrame,
    buttonLabel,
  }: {
    selected: boolean;
    onClick: () => void;
    jobName: string;
    rank: SimRank;
    poseFrame: 0 | 2;
    buttonLabel: string;
  }) {
    return (
      <button
        onClick={onClick}
        className={`w-[148px] rounded-md border px-2.5 py-2 text-center transition-colors ${selected ? "bg-primary/10 border-primary text-foreground" : "border-border text-muted-foreground hover:text-foreground hover:bg-muted/40"}`}
        aria-label={buttonLabel}
      >
        <div className="mx-auto flex h-20 w-[116px] items-center justify-center overflow-hidden rounded border border-border/60 bg-background/70">
          {jobName ? (
            <CharacterPreviewCanvas
              jobName={jobName}
              rank={rank}
              variant={1}
              equipState="right"
              scale={3}
              poseFrame={poseFrame}
              label={buttonLabel}
              className="h-16 w-auto max-w-[112px] shrink-0"
            />
          ) : (
            <span className="text-[10px] uppercase tracking-wide">No job</span>
          )}
        </div>
        <div className="mt-1.5 truncate text-xs font-semibold leading-tight">{jobName || "Not selected"}</div>
      </button>
    );
  }

  function renderSimBlock(
    title: string,
    state: SimState,
    setState: React.Dispatch<React.SetStateAction<SimState>>,
    result: SimResult | { error: string } | null,
    displayStats: Array<{ key: string; label: string; base: number; inc: number; maxLevel: number | null; maxValue: number | null }>
  ) {
    const sourceJobName = state.statSource === "father"
      ? state.fatherJob
      : state.statSource === "mother"
        ? state.motherJob
        : (result && !("error" in result) ? result.childJob : "");

    return (
      <div className="space-y-3 xl:max-w-[1200px]">
        <Card className="shadow-sm">
          <CardHeader className="pb-3">

            <CardTitle className="text-base">{title} — Parents</CardTitle>
            <CardDescription className="text-xs">Only 1st generation (Non-Marriage) jobs can marry in the game.</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div className="space-y-3 rounded-lg border border-border bg-background/40 p-4">
                <div className="flex items-center gap-2">
                  <img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-4 h-5" style={{imageRendering: "pixelated"}} />
                  <span className="text-sm font-semibold">Father</span>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">Job</label>
                  <SearchableSelect
                    value={state.fatherJob}
                    onChange={(v) => setState((prev) => ({ ...prev, fatherJob: v ?? "" }))}
                    options={jobOptions}
                    placeholder="Select father's job…"
                    triggerClassName="h-8 text-sm"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground">Rank</label>
                    <div className="flex rounded-md overflow-hidden border border-input">
                      {SIM_RANKS.map((r) => (
                        <button
                          key={r}
                          onClick={() => setState((prev) => ({ ...prev, fatherRank: r }))}
                          className={`flex-1 h-8 text-xs font-bold transition-colors ${state.fatherRank === r ? SIM_RANK_STYLE[r] + " border" : "bg-background text-muted-foreground hover:text-foreground"}`}
                        >
                          {r}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground">Awakening</label>
                    <SimAwkInput value={state.fatherAwk} onChange={(v) => setState((prev) => ({ ...prev, fatherAwk: v }))} />
                  </div>
                </div>
              </div>

              <div className="space-y-3 rounded-lg border border-border bg-background/40 p-4">
                <div className="flex items-center gap-2">
                  <img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-4 h-5" style={{imageRendering: "pixelated"}} />
                  <span className="text-sm font-semibold">Mother</span>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">Job</label>
                  <SearchableSelect
                    value={state.motherJob}
                    onChange={(v) => setState((prev) => ({ ...prev, motherJob: v ?? "" }))}
                    options={jobOptions}
                    placeholder="Select mother's job…"
                    triggerClassName="h-8 text-sm"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground">Rank</label>
                    <div className="flex rounded-md overflow-hidden border border-input">
                      {SIM_RANKS.map((r) => (
                        <button
                          key={r}
                          onClick={() => setState((prev) => ({ ...prev, motherRank: r }))}
                          className={`flex-1 h-8 text-xs font-bold transition-colors ${state.motherRank === r ? SIM_RANK_STYLE[r] + " border" : "bg-background text-muted-foreground hover:text-foreground"}`}
                        >
                          {r}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground">Awakening</label>
                    <SimAwkInput value={state.motherAwk} onChange={(v) => setState((prev) => ({ ...prev, motherAwk: v }))} />
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {!state.fatherJob || !state.motherJob ? (
          <Card className="shadow-sm border-dashed">
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Select both parents above to see the simulation result.
            </CardContent>
          </Card>
        ) : result && "error" in result ? (
          <Card className="shadow-sm border-destructive/40">
            <CardContent className="py-8 text-center">
              <p className="text-sm text-destructive">{result.error}</p>
            </CardContent>
          </Card>
        ) : result ? (
          <>
            <Card className="shadow-sm border-primary/20">
              <CardHeader className="pb-2">
                <div className="flex items-center gap-2">
                  <Baby className="w-4 h-4 text-violet-500" />
                  <CardTitle className="text-base flex items-center gap-1.5">
                    <span>{title} - Child</span>
                    <CroppedBabyIcon className="h-4 w-4" />
                    <span>Summary</span>
                  </CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 xl:grid-cols-5">
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">Compatibility</p>
                    <div className="flex items-center gap-1.5">
                      <span className={`text-sm font-bold px-2 py-0.5 rounded border ${AFFINITY_STYLE[result.affinityLetter] ?? "bg-muted border-border text-foreground"}`}>
                        {result.affinityLetter}
                      </span>
                      <span className="text-xs text-muted-foreground">({result.affinityNum}%)</span>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground flex items-center gap-1">Child <CroppedBabyIcon className="h-3.5 w-3.5" /> Job</p>
                    <EntityLink type="job" name={result.childJob} className="text-sm font-semibold hover:no-underline">
                      {result.childJob}
                    </EntityLink>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground flex items-center gap-1">Child <CroppedBabyIcon className="h-3.5 w-3.5" /> Rank</p>
                    <Badge className={`text-sm font-bold px-2.5 py-0.5 border ${SIM_RANK_STYLE[result.childRank]}`}>{result.childRank}</Badge>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground flex items-center gap-1">Child <CroppedBabyIcon className="h-3.5 w-3.5" /> Awakening</p>
                    <p className="text-sm font-semibold">{result.childAwakening}</p>
                    {state.fatherRank === state.motherRank && (
                      <p className="text-[10px] text-muted-foreground">+{SAME_RANK_BONUS[state.fatherRank]} same-rank bonus</p>
                    )}
                  </div>
                  <div className="col-span-2 flex items-center justify-center sm:col-span-4 xl:col-span-1 xl:justify-self-end">
                    <CharacterPreviewCanvas
                      jobName={result.childJob}
                      rank={result.childRank}
                      variant={2}
                      equipState="right"
                      scale={2}
                      poseFrame={previewPoseFrame}
                      label={`Child ${result.childRank} ${result.childJob}`}
                      className="h-16 w-auto max-w-[112px] shrink-0"
                    />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardHeader className="pb-2">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                  <div className="space-y-1.5">
                    <CardTitle className="text-base flex items-center gap-2">
                      <BarChart2 className="w-4 h-4 text-muted-foreground" />
                      Child <CroppedBabyIcon className="h-4 w-4" /> Stats Source - {state.statSource === "father" ? "Father" : state.statSource === "mother" ? "Mother" : "Child"} ({sourceJobName || "not selected"})
                    </CardTitle>
                    <p className="text-xs font-medium text-muted-foreground">Choose child <CroppedBabyIcon className="h-3.5 w-3.5" /> source:</p>
                    <CardDescription className="text-xs">
                      Uses child <CroppedBabyIcon className="h-3.5 w-3.5" /> rank {result.childRank} and awakening {result.childAwakening}. Max Level = base max level + 30 x awakening.
                    </CardDescription>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 self-start xl:justify-end">
                    <JobSourceIconButton
                      selected={state.statSource === "father"}
                      onClick={() => setState((prev) => ({ ...prev, statSource: prev.statSource === "father" ? "child" : "father" }))}
                      jobName={state.fatherJob}
                      rank={state.fatherRank}
                      poseFrame={previewPoseFrame}
                      buttonLabel={`Use father source ${state.fatherRank} ${state.fatherJob || "not-selected"}`}
                    />
                    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">OR</span>
                    <JobSourceIconButton
                      selected={state.statSource === "mother"}
                      onClick={() => setState((prev) => ({ ...prev, statSource: prev.statSource === "mother" ? "child" : "mother" }))}
                      jobName={state.motherJob}
                      rank={state.motherRank}
                      poseFrame={previewPoseFrame}
                      buttonLabel={`Use mother source ${state.motherRank} ${state.motherJob || "not-selected"}`}
                    />
                  </div>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <div className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground border-b border-border min-w-[400px]">
                    <span>Stat</span><span className="text-right">Base Val</span><span className="text-right">Growth</span><span className="text-right">Max Lvl</span><span className="text-right">Max Val</span>
                  </div>
                  {displayStats.map((s) => (
                    <div key={s.key} className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] px-4 py-2 text-sm border-b border-border last:border-0 hover:bg-muted/20 transition-colors min-w-[400px]">
                      <span className="font-medium">{s.label}</span>
                      <span className="text-right text-muted-foreground">{s.base}</span>
                      <span className="text-right text-muted-foreground">{s.inc}</span>
                      <span className="text-right font-medium">{s.maxLevel !== null ? s.maxLevel : <span className="text-muted-foreground/40">—</span>}</span>
                      <span className="text-right font-semibold text-primary">{s.maxValue !== null ? s.maxValue.toLocaleString() : <span className="text-muted-foreground/40">—</span>}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        ) : null}
      </div>
    );
  }

  const bothCalculated =
    resultOne && !("error" in resultOne) &&
    resultTwo && !("error" in resultTwo);

  const compareTopStat = (stats: Array<{ label: string; maxValue: number | null }>) => {
    const top = [...stats]
      .filter((s) => s.maxValue !== null)
      .sort((a, b) => (b.maxValue ?? 0) - (a.maxValue ?? 0))[0];
    return top ? `${top.label} (${(top.maxValue ?? 0).toLocaleString()})` : "—";
  };

  return (
    <div className="space-y-4">
      {renderSimBlock("Simulation 1", simOne, setSimOne, resultOne, displayStatsOne)}
      {renderSimBlock("Simulation 2", simTwo, setSimTwo, resultTwo, displayStatsTwo)}

      {bothCalculated && (
        <Card className="shadow-sm border-primary/20">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Simulation Comparison</CardTitle>
            <CardDescription className="text-xs">Quick comparison between Simulation 1 and Simulation 2 results.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground mb-2">Simulation 1</p>
                <div className="space-y-1 text-sm">
                  <p>
                    <span className="text-muted-foreground">Child job:</span>{" "}
                    <EntityLink type="job" name={(resultOne as SimResult).childJob} className="hover:no-underline">
                      {(resultOne as SimResult).childJob}
                    </EntityLink>
                  </p>
                  <p><span className="text-muted-foreground">Child rank:</span> {(resultOne as SimResult).childRank}</p>
                  <p><span className="text-muted-foreground">Affinity:</span> {(resultOne as SimResult).affinityLetter} ({(resultOne as SimResult).affinityNum}%)</p>
                  <p><span className="text-muted-foreground">Awakening:</span> {(resultOne as SimResult).childAwakening}</p>
                  <p><span className="text-muted-foreground">Top max stat:</span> {compareTopStat(displayStatsOne)}</p>
                </div>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground mb-2">Simulation 2</p>
                <div className="space-y-1 text-sm">
                  <p>
                    <span className="text-muted-foreground">Child job:</span>{" "}
                    <EntityLink type="job" name={(resultTwo as SimResult).childJob} className="hover:no-underline">
                      {(resultTwo as SimResult).childJob}
                    </EntityLink>
                  </p>
                  <p><span className="text-muted-foreground">Child rank:</span> {(resultTwo as SimResult).childRank}</p>
                  <p><span className="text-muted-foreground">Affinity:</span> {(resultTwo as SimResult).affinityLetter} ({(resultTwo as SimResult).affinityNum}%)</p>
                  <p><span className="text-muted-foreground">Awakening:</span> {(resultTwo as SimResult).childAwakening}</p>
                  <p><span className="text-muted-foreground">Top max stat:</span> {compareTopStat(displayStatsTwo)}</p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

type ActiveTab = "finder" | "simulator" | "data";

export default function MarriageMatcher() {
  // -- API data --
  const { data: sharedData, isLoading: jobsLoading, isPlaceholderData } = useSharedData();

  // -- Tab state (read/write from URL: ?tab=finder|simulator|data) --
  const search = useSearch();
  const urlTab = new URLSearchParams(search).get("tab") as ActiveTab | null;
  const [activeTab, setActiveTab] = useState<ActiveTab>(
    urlTab === "simulator" || urlTab === "data" ? urlTab : "finder",
  );

  const [pageNote, setPageNote] = useLocalFeature<string>("ka_note_marriage", "");
  const [showNote, setShowNote] = useState(false);

  const apiFirstGenJobs = useMemo(() => {
    if (!sharedData?.jobs) return null;
    const seen = new Set<string>();
    return Object.keys(sharedData.jobs)
      .filter((name) => {
        if (sharedData.jobs[name].generation !== 1) return false;
        const key = normJob(name);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort();
  }, [sharedData]);

  const apiAllJobs = useMemo(() => {
    if (!sharedData?.jobs) return null;
    const seen = new Set<string>();
    return Object.keys(sharedData.jobs)
      .filter((name) => {
        const key = normJob(name);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort();
  }, [sharedData]);

  const apiPairs = useMemo(() => {
    if (!sharedData) return null;
    return BUNDLED_PAIRS.map((p) => ({ ...p, children: p.children ?? [] }));
  }, [sharedData]);

  const jobTypeMap = useMemo(() => {
    if (!sharedData?.jobs) return {} as Record<string, "combat" | "non-combat">;
    const map: Record<string, "combat" | "non-combat"> = {};
    for (const [name, job] of Object.entries(sharedData.jobs)) {
      const type = typeFromJobCategory(job.category, job.type);
      if (type) map[name] = type;
    }
    return map;
  }, [sharedData]);

  const jobGenMap = useMemo(() => {
    if (!sharedData?.jobs) return {} as Record<string, 1 | 2>;
    const map: Record<string, 1 | 2> = {};
    for (const [name, job] of Object.entries(sharedData.jobs)) {
      map[name] = job.generation;
    }
    return map;
  }, [sharedData]);

  // ââ Job names: API-first, localStorage cache as fallback ââ
  const [cachedJobNames] = useState<string[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_jobNames");
      if (s) return JSON.parse(s) as string[];
    } catch { /* ignore */ }
    return FALLBACK_JOB_NAMES;
  });

  const prevFirstGenRef = useRef<string[] | null>(null);

  useEffect(() => {
    if (!apiFirstGenJobs) return;
    localStorage.setItem("ka_mf_jobNames", JSON.stringify(apiFirstGenJobs));

    // Auto-add pairs for any newly added Gen1 jobs
    const prev = prevFirstGenRef.current;
    if (prev !== null) {
      const newJobs = apiFirstGenJobs.filter((j) => !prev.includes(j));
      if (newJobs.length > 0) {
        setPairs((existing) => {
          const updated = [...existing];
          for (const newJob of newJobs) {
            for (const otherJob of apiFirstGenJobs) {
              const key = pairKey(newJob, otherJob);
              if (!updated.some((p) => pairKey(p.jobA, p.jobB) === key)) {
                updated.push(makePair(newJob, otherJob));
              }
            }
          }
          return updated;
        });
      }
    }
    prevFirstGenRef.current = apiFirstGenJobs;
  }, [apiFirstGenJobs]);

  const sortedJobNames = apiFirstGenJobs ?? cachedJobNames;
  const allJobNames = apiAllJobs ?? sortedJobNames;

  // ââ State: rank slots ââ
  const [rankSlots, setRankSlots] = useLocalFeature<RankSlot[]>("ka_mf_rankSlots", []);
  // Ref that always mirrors rankSlots — used inside effects to avoid stale closures
  const rankSlotsRef = useRef(rankSlots);
  useEffect(() => { rankSlotsRef.current = rankSlots; }, [rankSlots]);
  // Sync guards (same pattern as the existing pairs sync)
  const rankSlotsHydratedRef = useRef(false);
  const skipNextRankSlotsEchoRef = useRef(false);
  const rankSlotsPutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enablePlannerServerSync = false;

  // ââ State: pairs — loaded from API (community), fallback to localStorage ââ
  const [pairsLoadedFromApi, setPairsLoadedFromApi] = useState(false);
  const [pairs, setPairs] = useState<Pair[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_pairs");
      if (s) {
        const loaded = JSON.parse(s) as Pair[];
        return completeMarriagePairs(loaded.map((p) => ({ ...p, children: p.children ?? [] })), BUNDLED_PAIRS);
      }
    } catch { /* ignore */ }
    return BUNDLED_PAIRS.length ? BUNDLED_PAIRS : DEFAULT_PAIRS;
  });

  // ââ State: locked pairs ââ
  const [lockedPairs, setLockedPairs] = useState<LockedPair[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_lockedPairs");
      if (s) return JSON.parse(s) as LockedPair[];
    } catch { /* ignore */ }
    return [];
  });

  const [housedPairs, setHousedPairs] = useState<HousedPair[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_housedPairs");
      if (s) return JSON.parse(s) as HousedPair[];
    } catch { /* ignore */ }
    return [];
  });

  // ââ State: desired / priority children ââ
  const [desiredChildren, setDesiredChildren] = useState<string[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_desiredChildren");
      if (s) return JSON.parse(s) as string[];
    } catch { /* ignore */ }
    return [];
  });

  const [targetChildTypeFilter, setTargetChildTypeFilter] = useState<ChildTypeFilter>(() => {
    try {
      const s = localStorage.getItem("ka_mf_targetChildTypeFilter");
      if (s === "combat" || s === "non-combat") return s;
    } catch { /* ignore */ }
    return "all";
  });
  const [targetExclusiveFilter, setTargetExclusiveFilter] = useState<ExclusiveFilter>(() => {
    try {
      const s = localStorage.getItem("ka_mf_targetExclusiveFilter");
      if (s === "exclude-exclusive" || s === "only-exclusive") return s;
    } catch { /* ignore */ }
    return "all";
  });
  const [targetIncludeJobs, setTargetIncludeJobs] = useState<string[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_targetIncludeJobs");
      if (s) return JSON.parse(s) as string[];
    } catch { /* ignore */ }
    return [];
  });
  const [targetExcludeJobs, setTargetExcludeJobs] = useState<string[]>(() => {
    try {
      const s = localStorage.getItem("ka_mf_targetExcludeJobs");
      if (s) return JSON.parse(s) as string[];
    } catch { /* ignore */ }
    return [];
  });

  // ââ State: affinity filter (for matching and pair list) ââ
  const [affinityFilter, setAffinityFilter] = useState<Set<string>>(new Set(["A", "B", "C", "D", "E"]));
  const targetPoolJobs = useMemo(() => {
    const filtered = allJobNames.filter((name) => {
      const typeOk = targetChildTypeFilter === "all" || jobTypeMap[name] === targetChildTypeFilter;
      const exclusiveOk = targetExclusiveFilter === "all"
        || (targetExclusiveFilter === "exclude-exclusive" ? jobGenMap[name] !== 2 : jobGenMap[name] === 2);
      return typeOk && exclusiveOk;
    });
    return [...new Set([...filtered, ...targetIncludeJobs])]
      .filter((job) => !targetExcludeJobs.some((excluded) => normJob(excluded) === normJob(job)))
      .sort();
  }, [allJobNames, jobGenMap, jobTypeMap, targetChildTypeFilter, targetExclusiveFilter, targetIncludeJobs, targetExcludeJobs]);

  const filteredPairs = useMemo(() => {
    const pool = new Set(targetPoolJobs.map((job) => normJob(job)));
    return pairs.filter((p) => {
      if (p.affinity && !affinityFilter.has(p.affinity)) return false;
      if (pool.size === 0) return false;
      const possibleChildren = getPossibleMarriageChildren({ pairs }, p.jobA, p.jobB);
      return possibleChildren.some((child) => pool.has(normJob(child)));
    });
  }, [pairs, affinityFilter, targetPoolJobs]);

  // ââ State: result filters ââ
  const [resultTypeFilter, setResultTypeFilter] = useState<"all" | "combat" | "non-combat">("all");
  const [resultIncludeJobs, setResultIncludeJobs] = useState<string[]>([]);
  const [resultExcludeJobs, setResultExcludeJobs] = useState<string[]>([]);

  // ââ Sync pairs from API (one-time on first load) ââ
  // Guard: prevents echoing API-loaded pairs straight back to the server.
  // Without this, loading pairs from the API immediately triggers a PUT which
  // writes to ka_shared.json, which Vite watches (via the static import in
  // local-shared-data.ts), causing an HMR remount loop.
  const skipNextPairsApiEchoRef = useRef(false);

  useEffect(() => {
    if (!apiPairs || isPlaceholderData || pairsLoadedFromApi) return;
    setPairsLoadedFromApi(true);
    skipNextPairsApiEchoRef.current = true;
    setPairs(apiPairs);
  }, [apiPairs, isPlaceholderData]); // eslint-disable-line react-hooks/exhaustive-deps

  // ââ Persist ââ
  // ka_mf_rankSlots is persisted by useLocalFeature AND synced to server for cross-device access.

  // Hydration: on first API data load, pull rankSlots from server.
  // Rule: if marriageMatcher is non-null the server has been explicitly written to
  // and is authoritative — even if rankSlots is empty (means user deleted everything).
  // Only push local state when the server has NEVER been initialized (marriageMatcher === null).
  useEffect(() => {
    if (!enablePlannerServerSync) return;
    if (rankSlotsHydratedRef.current) return;
    if (!sharedData) return; // still loading
    rankSlotsHydratedRef.current = true;
    const mm = sharedData.marriageMatcher;
    if (mm !== null && mm !== undefined) {
      // Server has been written before — always take its state, even if empty
      skipNextRankSlotsEchoRef.current = true;
      setRankSlots((mm.rankSlots ?? []) as RankSlot[]);
    } else if (rankSlotsRef.current.length > 0) {
      // Server has never been synced — push local state as the initial seed
      fetch(apiUrl("/marriage-matcher/rank-slots"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: rankSlotsRef.current }),
      }).catch(() => {});
    }
  }, [enablePlannerServerSync, sharedData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced PUT: push rankSlots to server on every user change (after hydration)
  useEffect(() => {
    if (!enablePlannerServerSync) return;
    if (!rankSlotsHydratedRef.current) return;
    if (skipNextRankSlotsEchoRef.current) {
      skipNextRankSlotsEchoRef.current = false;
      return;
    }
    if (rankSlotsPutTimerRef.current) clearTimeout(rankSlotsPutTimerRef.current);
    rankSlotsPutTimerRef.current = setTimeout(() => {
      fetch(apiUrl("/marriage-matcher/rank-slots"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: rankSlots }),
      }).catch(() => {});
    }, 500);
    return () => {
      if (rankSlotsPutTimerRef.current) clearTimeout(rankSlotsPutTimerRef.current);
    };
  }, [enablePlannerServerSync, rankSlots]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    localStorage.setItem("ka_mf_pairs", JSON.stringify(pairs));
    if (pairsLoadedFromApi) {
      if (skipNextPairsApiEchoRef.current) {
        skipNextPairsApiEchoRef.current = false;
        return; // Pairs were just loaded from API — don't echo them back
      }
      persistPairs(pairs, "community");
    }
  }, [pairs, pairsLoadedFromApi]);

  useEffect(() => {
    localStorage.setItem("ka_mf_lockedPairs", JSON.stringify(lockedPairs));
  }, [lockedPairs]);

  useEffect(() => {
    localStorage.setItem("ka_mf_housedPairs", JSON.stringify(housedPairs));
  }, [housedPairs]);

  useEffect(() => {
    localStorage.setItem("ka_mf_desiredChildren", JSON.stringify(desiredChildren));
  }, [desiredChildren]);

  useEffect(() => {
    localStorage.setItem("ka_mf_targetChildTypeFilter", targetChildTypeFilter);
  }, [targetChildTypeFilter]);

  useEffect(() => {
    localStorage.setItem("ka_mf_targetExclusiveFilter", targetExclusiveFilter);
  }, [targetExclusiveFilter]);

  useEffect(() => {
    localStorage.setItem("ka_mf_targetIncludeJobs", JSON.stringify(targetIncludeJobs));
  }, [targetIncludeJobs]);

  useEffect(() => {
    localStorage.setItem("ka_mf_targetExcludeJobs", JSON.stringify(targetExcludeJobs));
  }, [targetExcludeJobs]);

  // ââ Result state ââ
  const [result, setResult] = useState<OptimalResult | null>(null);
  const [isCalculating, setIsCalculating] = useState(false);
  const [isStale, setIsStale] = useState(false);
  const [backupStatus, setBackupStatus] = useState<string | null>(null);
  const [backupStatusType, setBackupStatusType] = useState<"ok" | "error">("ok");

  const markStale = useCallback(() => setIsStale(true), []);

  // ââ Slot actions ââ
  const updateSlot = useCallback((id: string, field: RankSlotCountField, value: number) => {
    setRankSlots((prev) => prev.map((s) => (s.id === id ? { ...s, [field]: Math.max(0, value) } : s)));
    markStale();
  }, [markStale]);

  const removeSlot = useCallback((id: string) => {
    setRankSlots((prev) => prev.filter((s) => s.id !== id));
    markStale();
  }, [markStale]);

  const addSlot = useCallback((rank: Rank, jobName: string) => {
    const name = jobName.trim();
    if (!name) return;
    setRankSlots((prev) => {
      if (prev.some((s) => s.rank === rank && normJob(s.jobName) === normJob(name))) return prev;
      return [...prev, { id: generateId(), rank, jobName: name, males: 0, females: 0, unassigned: 0 }];
    });
    markStale();
  }, [markStale]);

  const applyQuickRankInput = useCallback((raw: string, fixedRank?: Rank): QuickInputResult => {
    const parsed = parseQuickRankInput(raw, sortedJobNames, fixedRank);
    if ("error" in parsed) return { ok: false, message: parsed.error };

    setRankSlots((prev) => {
      const existing = prev.find((slot) => slot.rank === parsed.rank && normJob(slot.jobName) === normJob(parsed.jobName));
      if (existing) {
        return prev.map((slot) => (
          slot.id === existing.id
            ? { ...slot, [parsed.field]: slot[parsed.field] + parsed.amount }
            : slot
        ));
      }

      return [
        ...prev,
        {
          id: generateId(),
          rank: parsed.rank,
          jobName: parsed.jobName,
          males: parsed.field === "males" ? parsed.amount : 0,
          females: parsed.field === "females" ? parsed.amount : 0,
          unassigned: parsed.field === "unassigned" ? parsed.amount : 0,
        },
      ];
    });
    markStale();

    const amountLabel = parsed.amount === 1 ? "" : `${parsed.amount} `;
    return {
      ok: true,
      message: `Added ${amountLabel}${formatCountField(parsed.field)} ${parsed.jobName} to Rank ${parsed.rank}.`,
    };
  }, [markStale, sortedJobNames]);

  // ââ Pair actions ââ
  const addPair = useCallback((a: string, b: string): string | null => {
    const key = pairKey(a, b);
    let dupe = false;
    setPairs((prev) => {
      if (prev.some((p) => pairKey(p.jobA, p.jobB) === key)) { dupe = true; return prev; }
      return [...prev, makePair(a, b)];
    });
    if (dupe) return "This pair already exists.";
    markStale();
    return null;
  }, [markStale]);

  const removePair = useCallback((id: string) => {
    setPairs((prev) => prev.filter((p) => p.id !== id));
    markStale();
  }, [markStale]);

  const updatePairChildren = useCallback((id: string, children: string[]) => {
    setPairs((prev) => prev.map((p) => p.id === id ? { ...p, children } : p));
    markStale();
  }, [markStale]);

  // ââ Lock actions ââ
  const lockMatch = useCallback((matchId: string) => {
    setResult((prev) => {
      if (!prev) return prev;
      const match = prev.matches.find((m) => m.id === matchId);
      if (!match) return prev;
      if (match.locked) return prev;
      const lp: LockedPair = { id: generateId(), maleJob: match.maleJob, femaleJob: match.femaleJob, rank: match.rank };
      setLockedPairs((lps) => lps.some((existing) => existing.id === matchId) ? lps : [...lps, lp]);
      return { ...prev, matches: prev.matches.map((m) => m.id === matchId ? { ...m, locked: true, id: lp.id } : m) };
    });
    setIsStale(true);
  }, []);

  const unlockMatch = useCallback((matchId: string) => {
    setLockedPairs((prev) => prev.filter((lp) => lp.id !== matchId));
    setResult((prev) => {
      if (!prev) return prev;
      return { ...prev, matches: prev.matches.map((m) => m.id === matchId ? { ...m, locked: false } : m) };
    });
    setIsStale(true);
  }, []);

  const changeLockedMale = useCallback((matchId: string, newJob: string) => {
    setLockedPairs((prev) => prev.map((lp) => lp.id === matchId ? { ...lp, maleJob: newJob } : lp));
    setResult((prev) => prev ? { ...prev, matches: prev.matches.map((m) => m.id === matchId ? { ...m, maleJob: newJob } : m) } : prev);
    setIsStale(true);
  }, []);

  const changeLockedFemale = useCallback((matchId: string, newJob: string) => {
    setLockedPairs((prev) => prev.map((lp) => lp.id === matchId ? { ...lp, femaleJob: newJob } : lp));
    setResult((prev) => prev ? { ...prev, matches: prev.matches.map((m) => m.id === matchId ? { ...m, femaleJob: newJob } : m) } : prev);
    setIsStale(true);
  }, []);

  // ââ Priority child actions ââ
  const addDesiredChild = useCallback((child: string) => {
    setDesiredChildren((prev) => prev.some((c) => normJob(c) === normJob(child)) ? prev : [...prev, child].sort());
  }, []);

  const removeDesiredChild = useCallback((child: string) => {
    setDesiredChildren((prev) => prev.filter((c) => normJob(c) !== normJob(child)));
  }, []);

  const addTargetIncludeJob = useCallback((job: string) => {
    setTargetIncludeJobs((prev) => prev.some((j) => normJob(j) === normJob(job)) ? prev : [...prev, job].sort());
  }, []);

  const removeTargetIncludeJob = useCallback((job: string) => {
    setTargetIncludeJobs((prev) => prev.filter((j) => normJob(j) !== normJob(job)));
  }, []);

  const addTargetExcludeJob = useCallback((job: string) => {
    setTargetExcludeJobs((prev) => prev.some((j) => normJob(j) === normJob(job)) ? prev : [...prev, job].sort());
  }, []);

  const removeTargetExcludeJob = useCallback((job: string) => {
    setTargetExcludeJobs((prev) => prev.filter((j) => normJob(j) !== normJob(job)));
  }, []);

  const resetPlannerSetup = useCallback(() => {
    setAffinityFilter(new Set(ALL_AFFINITIES));
    setTargetChildTypeFilter("all");
    setTargetExclusiveFilter("all");
    setTargetIncludeJobs([]);
    setTargetExcludeJobs([]);
    setDesiredChildren([]);
  }, []);

  const exportBackup = useCallback(() => {
    const payload: PlannerBackup = {
      version: 1,
      exportedAt: new Date().toISOString(),
      rankSlots,
      lockedPairs,
      housedPairs,
      desiredChildren,
    };
    const serialized = JSON.stringify(payload, null, 2);
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(serialized)
        .then(() => {
          setBackupStatusType("ok");
          setBackupStatus("Export copied to clipboard.");
        })
        .catch(() => {
          setBackupStatusType("error");
          setBackupStatus("Export copy failed.");
        });
      return;
    }
    setBackupStatusType("error");
    setBackupStatus("Clipboard copy is not available here.");
  }, [desiredChildren, lockedPairs, rankSlots]);

  const importBackupText = useCallback((backupText: string) => {
    try {
      const parsed: unknown = JSON.parse(backupText);
      if (!isPlannerBackup(parsed)) {
        setBackupStatusType("error");
        setBackupStatus("That text is not a valid Match Finder backup.");
        return;
      }
      setRankSlots(parsed.rankSlots);
      setLockedPairs(parsed.lockedPairs);
      setHousedPairs(parsed.housedPairs);
      setDesiredChildren(parsed.desiredChildren);
      setResult(null);
      setIsStale(false);
      setBackupStatusType("ok");
      setBackupStatus(`Imported backup from ${new Date(parsed.exportedAt).toLocaleString()}.`);
    } catch {
      setBackupStatusType("error");
      setBackupStatus("Could not import that backup.");
    }
  }, [setRankSlots]);

  const importBackup = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.readText) {
      setBackupStatusType("error");
      setBackupStatus("Clipboard paste is not available here.");
      return;
    }
    navigator.clipboard.readText()
      .then((text) => {
        if (!text.trim()) {
          setBackupStatusType("error");
          setBackupStatus("Clipboard is empty.");
          return;
        }
        importBackupText(text.trim());
      })
      .catch(() => {
        setBackupStatusType("error");
        setBackupStatus("Clipboard paste failed.");
      });
  }, [importBackupText]);

  const toggleHousedMatch = useCallback((matchId: string) => {
    setResult((prev) => {
      if (!prev) return prev;
      const match = prev.matches.find((m) => m.id === matchId);
      if (!match) return prev;

      const key = housedPairKey(match.rank, match.maleJob, match.femaleJob);
      setHousedPairs((prevHoused) => {
        const existingIndex = prevHoused.findIndex((entry) => housedPairKey(entry.rank, entry.maleJob, entry.femaleJob) === key);
        if (existingIndex === -1) {
          return [...prevHoused, { rank: match.rank, maleJob: match.maleJob, femaleJob: match.femaleJob, housed: true }];
        }
        return prevHoused.flatMap((entry, index) => {
          if (index !== existingIndex) return entry;
          return entry.housed ? [] : [{ ...entry, housed: true }];
        });
      });

      return {
        ...prev,
        matches: prev.matches.map((m) => m.id === matchId ? { ...m, housed: !m.housed } : m),
      };
    });
    setIsStale(true);
  }, []);

  const markMatchAsMarried = useCallback((match: MatchResult) => {
    let applied = false;
    setRankSlots((prev) => {
      const next = consumeMatchFromSlots(prev, match);
      applied = next !== prev;
      return next;
    });
    if (!applied) {
      setBackupStatus("Could not consume that pair from your current inputs. The counts may already have changed.");
      return;
    }

    setLockedPairs((prev) => {
      const next = prev.filter((pair) => pair.id !== match.id);
      setResult((prevResult) => prevResult ? {
        ...prevResult,
        matches: prevResult.matches.filter((entry) => entry.id !== match.id),
      } : prevResult);
      return next;
    });

    setIsStale(true);
    setBackupStatus(`Marked ${match.maleJob} + ${match.femaleJob} as married and removed them from the inputs above.`);
  }, [setRankSlots]);

  // ââ Calculate ââ
  const calculate = useCallback(() => {
    setIsCalculating(true);
    setIsStale(false);
    setTimeout(() => {
      setResult(findOptimalMatching(rankSlots, filteredPairs, lockedPairs, housedPairs));
      setIsCalculating(false);
    }, 80);
  }, [rankSlots, filteredPairs, lockedPairs]);

  const reset = useCallback(() => {
    const nextPairs = DEFAULT_PAIRS.map((p) => ({ ...p, id: generateId() }));
    setRankSlots([]);
    setPairs(nextPairs);
    setLockedPairs([]);
    setHousedPairs([]);
    setDesiredChildren([]);
    setTargetChildTypeFilter("all");
    setTargetExclusiveFilter("all");
    setTargetIncludeJobs([]);
    setTargetExcludeJobs([]);
    setResult(null);
    setIsStale(false);
  }, []);

  // ââ Derived ââ
  const availablePerRank = useMemo(() => {
    const map: Record<Rank, string[]> = { S: [], A: [], B: [], C: [], D: [] };
    const presentPerRank: Record<Rank, Set<string>> = {
      S: new Set(), A: new Set(), B: new Set(), C: new Set(), D: new Set(),
    };
    for (const s of rankSlots) presentPerRank[s.rank].add(s.jobName);
    for (const rank of RANKS) {
      map[rank] = sortedJobNames.filter((n) => !presentPerRank[rank].has(n));
    }
    return map;
  }, [rankSlots, sortedJobNames]);

  const jobsPerRank = useMemo(() => {
    const map: Record<Rank, string[]> = { S: [], A: [], B: [], C: [], D: [] };
    for (const s of rankSlots) {
      if (!map[s.rank].includes(s.jobName)) map[s.rank].push(s.jobName);
    }
    for (const rank of RANKS) map[rank].sort();
    return map;
  }, [rankSlots]);

  // ââ Child coverage for results ââ
  const childCoverage = useMemo(() => {
    if (!result || desiredChildren.length === 0) return [];
    return desiredChildren.map((child) => {
      const coveringMatches = result.matches.filter((m) =>
        getPossibleChildrenWithInheritance(m, pairs).some((p) => normJob(p) === normJob(child))
      );
      return { child, matches: coveringMatches };
    }).filter(({ matches }) => matches.length > 0);
  }, [result, desiredChildren, pairs]);

  const hasLocked = lockedPairs.length > 0;

  // ââ Filtered matches (result type filter) ââ
  const filteredMatches = useMemo(() => {
    if (!result) return [];
    return result.matches.filter((m) => {
      if (resultExcludeJobs.includes(m.maleJob) || resultExcludeJobs.includes(m.femaleJob)) return false;
      if (resultIncludeJobs.length > 0 && !resultIncludeJobs.includes(m.maleJob) && !resultIncludeJobs.includes(m.femaleJob)) return false;
      if (targetChildTypeFilter !== "all") {
        const childTypes = getPossibleChildrenWithInheritance(m, pairs)
          .map((child) => jobTypeMap[child]);
        if (!childTypes.some((type) => type === targetChildTypeFilter)) return false;
      }
      if (resultTypeFilter === "all") return true;
      const maleType = jobTypeMap[m.maleJob];
      const femaleType = jobTypeMap[m.femaleJob];
      return maleType === resultTypeFilter && femaleType === resultTypeFilter;
    });
  }, [result, resultTypeFilter, resultIncludeJobs, resultExcludeJobs, jobTypeMap, targetChildTypeFilter, pairs]);

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-5xl mx-auto px-4 py-10">
        <PageHeader
          icon={<Heart className="w-5 h-5 text-rose-500" />}
          title="Match Finder & Marriage Sim"
          className="mb-6"
          actions={(
            <div className="flex items-center gap-2 shrink-0">
              <Button variant="ghost" size="icon" onClick={() => setShowNote((v) => !v)} className="h-8 w-8 text-muted-foreground" title="Personal notes (private, stored on this device)">
                <Info className="w-3.5 h-3.5" />
              </Button>
              <InfoDialog />
              <Button variant="outline" size="sm" onClick={reset} className="flex items-center gap-2 h-8">
                <RefreshCw className="w-4 h-4" /> Reset
              </Button>
            </div>
          )}
        >
          <p>
            Find optimal Kingdom Adventures job pairings, simulate marriage outcomes, preview child stats, and browse full compatibility data.
          </p>
        </PageHeader>

        <div className="mb-4 rounded-lg border border-red-300 bg-red-50/70 px-4 py-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/20 dark:text-red-300">
          <strong className="text-foreground dark:text-red-200">Warning:</strong> marriage is forever in Kingdom Adventures. You cannot unmarry or divorce, so double-check pairs before committing in-game. Do not marry your Monarch until you completely understand the marriage mechanics, and it is strongly worth asking the community before committing to a Monarch marriage.
        </div>

        {showNote && (
          <div className="mb-4">
            <textarea
              value={pageNote}
              onChange={(e) => setPageNote(e.target.value)}
              placeholder="Personal notes for this page… (only visible to you, saved on this device)"
              className="w-full h-20 text-sm rounded-md border border-input bg-muted/20 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground/40"
            />
          </div>
        )}

        <Card className="mb-4 border-amber-300/60 bg-amber-50/40 dark:border-amber-800 dark:bg-amber-950/10">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Temporary Export / Import Failsafe</CardTitle>
            <CardDescription className="text-xs">
              The site already remembers your Match Finder inputs. These buttons are just a backup so you do not have to redo everything while this part of the site is still work in progress.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={exportBackup} className="gap-2">
                  <Upload className="w-4 h-4" /> Export current inputs
                </Button>
                <Button variant="outline" size="sm" onClick={importBackup} className="gap-2">
                  <Download className="w-4 h-4" /> Import from clipboard
                </Button>
              </div>
              {backupStatus && (
                <div className={`flex items-center gap-1.5 text-xs ${backupStatusType === "ok" ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
                  {backupStatusType === "ok" ? <Check className="h-3.5 w-3.5" /> : null}
                  {backupStatus}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Tab nav */}
        <div className="flex gap-1 mb-6 border-b border-border pb-0">
          {([
            { key: "finder",    icon: Heart,     label: "Match Finder" },
            { key: "simulator", icon: Baby,       label: "Marriage Simulator" },
            { key: "data",      icon: BarChart2,  label: "Compatibility Data" },
          ] as const).map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
                activeTab === key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* ── Finder tab ──────────────────────────────────────────────── */}
        {activeTab === "finder" && (<>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.75fr)_minmax(320px,0.85fr)] items-start">
          <div className="min-w-0 space-y-4">
            <Card className="shadow-sm border-primary/20">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Plus className="h-4 w-4 text-primary" />
                  Quick Add
                </CardTitle>
              </CardHeader>
              <CardContent>
                <QuickRankInput onSubmit={applyQuickRankInput} />
              </CardContent>
            </Card>

            {/* Rank tables */}
            <div className="grid gap-4 md:grid-cols-2">
              {RANKS.map((rank) => (
                <RankTable key={rank} rank={rank}
                  slots={rankSlots.filter((s) => s.rank === rank)}
                  availableJobs={availablePerRank[rank]}
                  totalFirstGenCount={sortedJobNames.length}
                  onUpdate={updateSlot} onRemove={removeSlot} onAdd={addSlot}
                  onQuickAdd={applyQuickRankInput}
                />
              ))}
            </div>
          </div>

          <div className="min-w-0 space-y-4 xl:sticky xl:top-20">
            {/* Jobs Panel */}
            <JobsPanel jobNames={sortedJobNames} isLoading={jobsLoading} isFromApi={!!apiFirstGenJobs} />

            <PlannerSetup
              affinityFilter={affinityFilter}
              onAffinityFilterChange={setAffinityFilter}
              targetChildTypeFilter={targetChildTypeFilter}
              onTargetChildTypeFilterChange={setTargetChildTypeFilter}
              targetExclusiveFilter={targetExclusiveFilter}
              onTargetExclusiveFilterChange={setTargetExclusiveFilter}
              targetIncludeJobs={targetIncludeJobs}
              onAddIncludeJob={addTargetIncludeJob}
              onRemoveIncludeJob={removeTargetIncludeJob}
              targetExcludeJobs={targetExcludeJobs}
              onAddExcludeJob={addTargetExcludeJob}
              onRemoveExcludeJob={removeTargetExcludeJob}
              targetPoolJobs={targetPoolJobs}
              onResetSetup={resetPlannerSetup}
              allJobNames={allJobNames}
            />

            {/* Stale / locked notice */}
            {(isStale && result) && (
              <div className="flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-700 px-4 py-3">
                <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
                <p className="text-sm text-amber-700 dark:text-amber-400 flex-1">
                  Inputs have changed{hasLocked ? " (some pairs are locked)" : ""}. Recalculate to update results.
                </p>
              </div>
            )}
            {(hasLocked && !isStale && result) && (
              <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50/60 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3">
                <Lock className="w-4 h-4 text-amber-500 shrink-0" />
                <p className="text-sm text-amber-600 dark:text-amber-400">
                  {lockedPairs.length} pair{lockedPairs.length !== 1 ? "s are" : " is"} locked. The algorithm works around them.
                </p>
              </div>
            )}

            {/* Calculate button */}
            <Card className="shadow-sm border-primary/20">
              <CardContent className="pt-6">
                <Button onClick={calculate} disabled={isCalculating} size="lg" className={`w-full gap-2 shadow-md transition-all ${isStale && result ? "ring-2 ring-amber-400 ring-offset-2" : ""}`}>
                  {isCalculating
                    ? <><Loader2 className="w-4 h-4 animate-spin" />Calculating…</>
                    : <><Zap className="w-4 h-4" />{result ? "Recalculate" : "Calculate Optimal Matching"}</>}
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Results */}
        {result && !isCalculating && (
          <div className="mt-6 space-y-4">

            {/* Desired children coverage */}
            {childCoverage.length > 0 && (
              <Card className="shadow-sm border-violet-200 dark:border-violet-800">
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <Star className="w-4 h-4 text-violet-500" />
                    <CardTitle className="text-sm text-violet-800 dark:text-violet-300">Desired Child Coverage</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {childCoverage.map(({ child, matches: coverMatches }) => (
                      <div key={child} className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm border-violet-300 bg-violet-50 dark:bg-violet-950/30 dark:border-violet-700">
                        <Star className="w-3.5 h-3.5 mt-0.5 shrink-0 text-violet-500" />
                        <div className="min-w-0">
                          <p className="font-semibold text-foreground text-xs">{child}</p>
                          <p className="text-[11px] text-muted-foreground">
                            Covered by {coverMatches.length} match{coverMatches.length !== 1 ? "es" : ""}: {coverMatches.map((m) => `${m.maleJob} × ${m.femaleJob}`).join(", ")}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {result.unassignedDecisions.some((d) => d.assignedMales + d.assignedFemales > 0) && (
              <Card className="shadow-sm border-amber-300 dark:border-amber-700">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-amber-700 dark:text-amber-400">Optimal gender assignment for unassigned slots</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {result.unassignedDecisions.map((d, i) => (
                      <div key={i} className="flex items-center gap-2 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-sm">
                        <Badge className={`text-[10px] px-1.5 border ${RANK_STYLE[d.rank].badge}`}>{d.rank}</Badge>
                        <span className="font-semibold text-foreground">{d.jobName}</span>
                        <span className="text-muted-foreground">→</span>
                        {d.assignedMales > 0 && <Badge variant="secondary" className="text-xs gap-1"><img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-3 h-4" style={{imageRendering: "pixelated"}} />{d.assignedMales}</Badge>}
                        {d.assignedFemales > 0 && <Badge variant="outline" className="text-xs border-primary/30 text-primary gap-1"><img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-3 h-4" style={{imageRendering: "pixelated"}} />{d.assignedFemales}</Badge>}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Result filter UI */}
            <Card className="shadow-sm">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <Filter className="w-4 h-4 text-muted-foreground" />
                  Filter Results
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-2 items-center">
                  <span className="text-xs text-muted-foreground font-medium">Battle-Type:</span>
                  <div className="flex rounded-md overflow-hidden border border-input">
                    {(["all", "combat", "non-combat"] as const).map((t) => (
                      <button key={t} onClick={() => setResultTypeFilter(t)}
                        className={`px-3 h-7 text-xs font-medium transition-colors ${resultTypeFilter === t
                          ? t === "combat" ? "bg-red-500 text-white" : t === "non-combat" ? "bg-sky-500 text-white" : "bg-primary text-primary-foreground"
                          : "bg-background text-muted-foreground hover:text-foreground"}`}>
                        {battleTypeLabel(t)}
                      </button>
                    ))}
                  </div>
                </div>
                {result.matches.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-1.5 items-center">
                      <span className="text-xs text-muted-foreground font-medium w-16 shrink-0">Include:</span>
                      <div className="flex flex-wrap gap-1">
                        {resultIncludeJobs.map((j) => (
                          <Badge key={j} className="gap-1 px-2 py-0.5 text-xs bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-700 border">
                            {j}<button onClick={() => setResultIncludeJobs((prev) => prev.filter((x) => x !== j))}><X className="w-2.5 h-2.5 ml-0.5" /></button>
                          </Badge>
                        ))}
                        <SearchableSelect
                          value=""
                          clearOnSelect
                          onChange={(v) => { if (v) setResultIncludeJobs((prev) => prev.includes(v) ? prev : [...prev, v]); }}
                          options={result.matches.flatMap((m) => [m.maleJob, m.femaleJob]).filter((v, i, a) => a.indexOf(v) === i && !resultIncludeJobs.includes(v)).sort().map((j) => ({ value: j, label: j }))}
                          placeholder="+ Add job…"
                          triggerClassName="h-6 text-xs"
                        />
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 items-center">
                      <span className="text-xs text-muted-foreground font-medium w-16 shrink-0">Exclude:</span>
                      <div className="flex flex-wrap gap-1">
                        {resultExcludeJobs.map((j) => (
                          <Badge key={j} className="gap-1 px-2 py-0.5 text-xs bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-300 dark:border-red-700 border">
                            {j}<button onClick={() => setResultExcludeJobs((prev) => prev.filter((x) => x !== j))}><X className="w-2.5 h-2.5 ml-0.5" /></button>
                          </Badge>
                        ))}
                        <SearchableSelect
                          value=""
                          clearOnSelect
                          onChange={(v) => { if (v) setResultExcludeJobs((prev) => prev.includes(v) ? prev : [...prev, v]); }}
                          options={result.matches.flatMap((m) => [m.maleJob, m.femaleJob]).filter((v, i, a) => a.indexOf(v) === i && !resultExcludeJobs.includes(v)).sort().map((j) => ({ value: j, label: j }))}
                          placeholder="+ Exclude job…"
                          triggerClassName="h-6 text-xs"
                        />
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="shadow-sm border-primary/20">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-base">Matched Pairs</CardTitle>
                    <CardDescription className="text-xs mt-0.5">
                      Lock a pair to keep it fixed across recalculations.
                      {desiredChildren.length > 0 && <>· <span className="text-violet-600 dark:text-violet-400">Purple rows</span> cover your desired children.</>}
                    </CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    {filteredMatches.length !== result.matches.length && (
                      <Badge variant="outline" className="text-xs">{result.matches.length} total</Badge>
                    )}
                    <Badge className="text-sm px-3 py-1 bg-primary text-primary-foreground shrink-0">{filteredMatches.length} shown</Badge>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {filteredMatches.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    {result.matches.length === 0
                      ? "No matches found. Check that compatible pairs cover jobs with both male and female slots at the same character rank."
                      : "No matches pass the current filters. Try adjusting the filter above."}
                  </p>
                ) : (
                  <div className="space-y-5">
                    {RANKS.map((rank) => {
                      const rankMatches = filteredMatches.filter((m) => m.rank === rank);
                      if (rankMatches.length === 0) return null;
                      const allJobsForRank = [...new Set([
                        ...jobsPerRank[rank],
                        ...rankMatches.map((m) => m.maleJob),
                        ...rankMatches.map((m) => m.femaleJob),
                      ])].sort();
                      return (
                        <div key={rank}>
                          <div className="flex items-center gap-2 mb-2">
                            <Badge className={`text-xs font-bold px-2 border ${RANK_STYLE[rank].badge}`}>Rank {rank}</Badge>
                            <span className="text-xs text-muted-foreground">
                              {rankMatches.length} match{rankMatches.length !== 1 ? "es" : ""}
                              <span className="ml-2 font-semibold text-foreground">+{SAME_RANK_BONUS[rank]} awakening</span>
                            </span>
                            {rankMatches.some((m) => m.locked) && (
                              <Badge variant="outline" className="text-[10px] gap-1 border-amber-400 text-amber-600 dark:text-amber-400 px-1.5 py-0">
                                <Lock className="w-2.5 h-2.5" />{rankMatches.filter((m) => m.locked).length} locked
                              </Badge>
                            )}
                          </div>
                          <div className="space-y-1.5 pl-1">
                            {rankMatches.map((m, i) => (
                              <MatchRow key={m.id} match={m} index={i}
                                rankJobNames={allJobsForRank.length > 0 ? allJobsForRank : sortedJobNames}
                                pairs={pairs}
                                desiredChildren={desiredChildren}
                                onMarkMarried={markMatchAsMarried}
                                onToggleHoused={toggleHousedMatch}
                                onLock={lockMatch} onUnlock={unlockMatch}
                                onChangeMale={changeLockedMale} onChangeFemale={changeLockedFemale}
                              />
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {(result.unmatchedMale.length > 0 || result.unmatchedFemale.length > 0 || result.unmatchedUnassigned.length > 0) && (
                  <div className="mt-4 p-3 rounded-lg bg-muted/50 border border-border space-y-2">
                    {result.unmatchedMale.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1.5"><img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-3 h-4 inline-block" style={{imageRendering: "pixelated"}} /> Unmatched male slots:</p>
                        <div className="flex flex-wrap gap-1.5">
                          {result.unmatchedMale.map((u, i) => (
                            <Badge key={i} variant="secondary" className="text-xs gap-1">
                              <img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-3 h-4" style={{imageRendering: "pixelated"}} />{u.job}
                              <span className={`text-[10px] ${RANK_STYLE[u.rank].badge} rounded px-1`}>{u.rank}</span>
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {result.unmatchedFemale.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1.5"><img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-3 h-4 inline-block" style={{imageRendering: "pixelated"}} /> Unmatched female slots:</p>
                        <div className="flex flex-wrap gap-1.5">
                          {result.unmatchedFemale.map((u, i) => (
                            <Badge key={i} variant="outline" className="text-xs text-muted-foreground gap-1">
                              <img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-3 h-4" style={{imageRendering: "pixelated"}} />{u.job}
                              <span className={`text-[10px] ${RANK_STYLE[u.rank].badge} rounded px-1`}>{u.rank}</span>
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {result.unmatchedUnassigned.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1.5">⚥ Unmatched unassigned slots:</p>
                        <div className="flex flex-wrap gap-1.5">
                          {result.unmatchedUnassigned.map((u, i) => (
                            <Badge key={i} variant="secondary" className="text-xs gap-1">
                              {u.job}
                              <span className={`text-[10px] ${RANK_STYLE[u.rank].badge} rounded px-1`}>{u.rank}</span>
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div className="mt-4 pt-4 border-t border-border flex items-center justify-end text-sm">
                  <span className="text-muted-foreground">Total matched: <strong className="text-foreground">{result.matches.length}</strong></span>
                </div>
              </CardContent>
            </Card>

          </div>
        )}

        {isCalculating && (
          <div className="mt-6 flex flex-col items-center gap-3 py-12 text-muted-foreground">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <p className="text-sm">Running optimal matching algorithm…</p>
          </div>
        )}

        </>)}
        {/* ── END Finder tab ─────────────────────────────────────────── */}

        {/* ── Simulator tab ──────────────────────────────────────────── */}
        {activeTab === "simulator" && (
          <SimTab
            pairs={pairsLoadedFromApi ? pairs : apiPairs ?? pairs}
            jobs={sharedData?.jobs ?? {}}
            firstGenJobNames={sortedJobNames}
          />
        )}

        {/* ── Data tab ───────────────────────────────────────────────── */}
        {activeTab === "data" && (
          <PairsPanel
            pairs={pairs}
            jobTypeMap={jobTypeMap}
            jobGenMap={jobGenMap}
            allJobNames={allJobNames}
          />
        )}

        {/* Footer */}
        <div className="mt-12 pt-6 border-t border-border flex items-center justify-between text-xs text-muted-foreground">
          <span>Match Finder &amp; Marriage Sim — open source</span>
          <a href="https://replit.com" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1 hover:text-foreground transition-colors">
            <ExternalLink className="w-3 h-3" /> Fork &amp; edit on Replit
          </a>
        </div>
      </div>
    </div>
  );
}
