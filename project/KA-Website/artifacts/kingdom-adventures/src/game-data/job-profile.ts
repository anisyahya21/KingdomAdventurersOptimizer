import { JOB_RANGE_DATA } from "@/lib/generated-job-range-data";
import { JOB_SKILL_DATA } from "@/lib/generated-job-skill-data";
import { getJobsForBuilding, KNOWN_JOB_SHOPS, splitBuildingRankNote, type BuildingJobOwner } from "./job-buildings";
import { getResolvedShieldAccess, getResolvedWeaponAccess, type WeaponValue } from "./job-equipment";
import { getResolvedSkillAccess, type SkillAccessMap } from "./job-skills";
import { normJob, pairKey, typeFromJobCategory, type JobBattleType } from "./job-normalization";
import { SURVEY_CAPABLE_JOB_BASE_NAMES } from "./job-surveys";

export type JobStatEntry = { base: number; inc: number; levels?: Record<string, number>; maxLevel?: number };
export type JobRankData = { stats: Record<string, JobStatEntry> };

export type ProfileJob = {
  generation: 1 | 2;
  type?: JobBattleType;
  category?: string;
  icon?: string;
  ranks: Record<string, JobRankData>;
  shield?: "can" | "cannot";
  weaponEquip?: Partial<Record<string, WeaponValue>>;
  skillAccess?: SkillAccessMap;
  skills?: string[];
  shops?: string[];
  notes?: string;
};

export type JobProfilePair = {
  id: string;
  partner: string;
  children: string[];
  affinity?: string;
  affinityNum?: number;
};

export type SharedJobProfileData = {
  jobs?: Record<string, ProfileJob>;
  pairs?: Array<{
    id: string;
    jobA: string;
    jobB: string;
    children?: string[];
    affinity?: string;
    affinityNum?: number;
  }>;
  weaponCategories?: string[];
};

export const JOB_RANGE_RANK_ORDER = ["D", "C", "B", "A", "S"] as const;
export type JobRangeRank = typeof JOB_RANGE_RANK_ORDER[number];
export type JobRangeIndex = 0 | 1 | 2;

export const JOB_RANGE_LABELS: Array<{ label: string; index: JobRangeIndex }> = [
  { label: "Searching Range", index: 0 },
  { label: "Deployment Range", index: 1 },
  { label: "Defog AoE", index: 2 },
];

export type JobRangeGroup = {
  label: string;
  value: number;
};

export type JobSkillGroup = {
  label: string;
  skills: string[];
};

export type JobProfile = {
  name: string;
  job: ProfileJob;
  battleType?: JobBattleType;
  generation: 1 | 2;
  rankNames: string[];
  shops: string[];
  buildings: Array<BuildingJobOwner & { buildingName: string }>;
  surveyCapable: boolean;
  skillAccess: SkillAccessMap;
  equipmentAccess: {
    shield?: WeaponValue;
    weapons: Record<string, WeaponValue | undefined>;
  };
  rangeGroups: Array<{ label: string; index: JobRangeIndex; groups: JobRangeGroup[] }>;
  learnedSkillGroups: JobSkillGroup[];
  marriage: {
    pairs: JobProfilePair[];
    children: string[];
  };
};

const JOB_RANGES = JOB_RANGE_DATA as Record<string, Partial<Record<JobRangeRank, readonly [number, number, number]>>>;
const JOB_SKILLS = JOB_SKILL_DATA as Record<string, Partial<Record<JobRangeRank, readonly string[]>>>;

function formatRangeRankLabel(start: JobRangeRank, end: JobRangeRank) {
  return start === end ? start : `${start}-${end}`;
}

function sameSkills(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((skill, index) => skill === b[index]);
}

function getCollapsedRangeGroups(jobName: string, rangeIndex: JobRangeIndex): JobRangeGroup[] {
  const ranges = JOB_RANGES[jobName];
  if (!ranges) return [];

  const entries = JOB_RANGE_RANK_ORDER
    .map((rank) => {
      const value = ranges[rank]?.[rangeIndex];
      return typeof value === "number" ? { rank, value } : null;
    })
    .filter((entry): entry is { rank: JobRangeRank; value: number } => Boolean(entry && Number.isFinite(entry.value)));

  if (entries.length === 0) return [];

  const groups: JobRangeGroup[] = [];
  let start = entries[0].rank;
  let end = entries[0].rank;
  let value = entries[0].value;

  for (const entry of entries.slice(1)) {
    if (entry.value === value) {
      end = entry.rank;
    } else {
      groups.push({ label: formatRangeRankLabel(start, end), value });
      start = entry.rank;
      end = entry.rank;
      value = entry.value;
    }
  }

  groups.push({ label: formatRangeRankLabel(start, end), value });
  return groups;
}

function getCollapsedSkillGroups(jobName: string): JobSkillGroup[] {
  const skillsByRank = JOB_SKILLS[jobName];
  if (!skillsByRank) return [];

  const entries = JOB_RANGE_RANK_ORDER
    .map((rank) => {
      const skills = skillsByRank[rank];
      return skills?.length ? { rank, skills } : null;
    })
    .filter((entry): entry is { rank: JobRangeRank; skills: readonly string[] } => Boolean(entry));

  if (entries.length === 0) return [];

  const groups: JobSkillGroup[] = [];
  let start = entries[0].rank;
  let end = entries[0].rank;
  let skills = entries[0].skills;

  for (const entry of entries.slice(1)) {
    if (sameSkills(entry.skills, skills)) {
      end = entry.rank;
    } else {
      groups.push({ label: formatRangeRankLabel(start, end), skills: [...skills] });
      start = entry.rank;
      end = entry.rank;
      skills = entry.skills;
    }
  }

  groups.push({ label: formatRangeRankLabel(start, end), skills: [...skills] });
  return groups;
}

export function findJobName(shared: SharedJobProfileData, jobName: string) {
  return Object.keys(shared.jobs ?? {}).find((name) => normJob(name) === normJob(jobName));
}

export function getResolvedJobShops(jobName: string, shops?: string[]) {
  return shops?.length ? shops : KNOWN_JOB_SHOPS[jobName] ?? [];
}

export function getJobRangeValue(jobName: string, rank: string, rangeIndex: JobRangeIndex) {
  return JOB_RANGES[jobName]?.[rank as JobRangeRank]?.[rangeIndex] ?? null;
}

export function getJobProfile(
  shared: SharedJobProfileData,
  jobName: string,
  options: { selectedRank?: string } = {},
): JobProfile | null {
  const name = findJobName(shared, jobName) ?? jobName;
  const job = shared.jobs?.[name];
  if (!job) return null;

  const shops = getResolvedJobShops(name, job.shops);
  const buildings = shops.map((entry) => {
    const { buildingName, rankNote } = splitBuildingRankNote(entry);
    return { buildingName, jobName: name, rankNote };
  });
  const weaponCategories = shared.weaponCategories ?? [];
  const pairs = (shared.pairs ?? [])
    .filter((pair) => normJob(pair.jobA) === normJob(name) || normJob(pair.jobB) === normJob(name))
    .map((pair) => {
      const partner = normJob(pair.jobA) === normJob(name) ? pair.jobB : pair.jobA;
      return {
        id: pair.id,
        partner,
        children: [...(pair.children ?? [])].sort(),
        affinity: pair.affinity,
        affinityNum: pair.affinityNum,
      };
    });

  return {
    name,
    job,
    battleType: typeFromJobCategory(job.category, job.type),
    generation: job.generation,
    rankNames: Object.keys(job.ranks),
    shops,
    buildings,
    surveyCapable: SURVEY_CAPABLE_JOB_BASE_NAMES.has(name),
    skillAccess: getResolvedSkillAccess(name, job, options.selectedRank),
    equipmentAccess: {
      shield: getResolvedShieldAccess(name, job),
      weapons: Object.fromEntries(
        weaponCategories.map((weaponClass) => [weaponClass, getResolvedWeaponAccess(name, job, weaponClass)]),
      ),
    },
    rangeGroups: JOB_RANGE_LABELS
      .map((range) => ({ ...range, groups: getCollapsedRangeGroups(name, range.index) }))
      .filter((range) => range.groups.length > 0),
    learnedSkillGroups: getCollapsedSkillGroups(name),
    marriage: {
      pairs,
      children: Array.from(new Set(pairs.flatMap((pair) => pair.children))).sort(),
    },
  };
}

export function getJobProfiles(shared: SharedJobProfileData) {
  return Object.keys(shared.jobs ?? {})
    .map((jobName) => getJobProfile(shared, jobName))
    .filter((profile): profile is JobProfile => profile !== null);
}

export function getMarriagePair(shared: SharedJobProfileData, jobA: string, jobB: string) {
  const key = pairKey(jobA, jobB);
  return (shared.pairs ?? []).find((pair) => pairKey(pair.jobA, pair.jobB) === key) ?? null;
}

export function getPossibleMarriageChildren(
  shared: SharedJobProfileData,
  jobA: string,
  jobB: string,
  options: { includeParentInheritance?: boolean } = {},
) {
  if ([jobA, jobB].some((job) => normJob(job) === "monarch")) {
    return ["Royal"];
  }

  const includeParentInheritance = options.includeParentInheritance ?? true;
  const pair = getMarriagePair(shared, jobA, jobB);
  const explicitChildren = pair?.children ?? [];
  const pool = includeParentInheritance ? [jobA, jobB, ...explicitChildren] : [...explicitChildren];

  return Array.from(new Set(pool.map((name) => name.trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

export function getJobsThatOpenBuilding(buildingName: string) {
  return getJobsForBuilding(buildingName);
}
