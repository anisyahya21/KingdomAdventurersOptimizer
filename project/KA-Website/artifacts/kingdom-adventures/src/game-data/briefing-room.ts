import missionCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Mission.csv?raw";
import { getMonsterSpawnDataById, getMonsterSpawnDataByName, type MonsterSpawnData } from "@/game-data/monster-spawn-data";
import { parseCsv } from "@/lib/csv";

export type BriefingCalendarDate = { year: number; month: number; day: number };

export type BriefingMissionTemplate = {
  id: number;
  name: string;
  challengeTerms: number[][];
  conditionType: number;
  conditionTarget: number;
  goalMin: number;
  goalMax: number;
  rewardId: number;
  rewardMin: number;
  rewardMax: number;
};

export type BriefingMission = BriefingMissionTemplate & {
  monsterName?: string;
  goal: number | null;
  rewardName: string;
  rewardAmount: number | null;
  displayName: string;
};

export const BRIEFING_PROGRESS_MIN = 0;
export const BRIEFING_PROGRESS_MAX = 99;

// These reward IDs join to Item.csv IDs: Diamonds and the three trade tickets.
const MISSION_REWARD_NAMES: Record<number, string> = {
  0: "Diamonds",
  8: "Equipment Trade Ticket",
  9: "Facility Trade Ticket",
  10: "Item Trade Ticket",
};

function readMissionRow(row: string[]): BriefingMissionTemplate | null {
  const values = row.slice(2).map((value) => value.trim()).filter(Boolean);
  let cursor = 0;
  const takeInt = () => {
    const value = Number(values[cursor]);
    cursor += 1;
    return Number.isInteger(value) ? value : null;
  };

  const id = Number(row[0]);
  if (!Number.isInteger(id) || !row[1]) return null;

  const termCount = takeInt();
  if (termCount === null || termCount < 0 || termCount > 16) return null;
  const challengeTerms: number[][] = [];
  for (let termIndex = 0; termIndex < termCount; termIndex += 1) {
    const valueCount = takeInt();
    if (valueCount === null || valueCount < 1 || valueCount > 16) return null;
    const term: number[] = [];
    for (let valueIndex = 0; valueIndex < valueCount; valueIndex += 1) {
      const value = takeInt();
      if (value === null) return null;
      term.push(value);
    }
    challengeTerms.push(term);
  }

  const fields = Array.from({ length: 7 }, takeInt);
  if (fields.some((value) => value === null)) return null;
  const [conditionType, conditionTarget, goalMin, goalMax, rewardId, rewardMin, rewardMax] = fields as number[];

  return {
    id,
    name: row[1],
    challengeTerms,
    conditionType,
    conditionTarget,
    goalMin,
    goalMax,
    rewardId,
    rewardMin,
    rewardMax,
  };
}

export const BRIEFING_MISSION_TEMPLATES = parseCsv(missionCsv)
  .slice(1)
  .map(readMissionRow)
  .filter((mission): mission is BriefingMissionTemplate => mission !== null)
  .sort((left, right) => left.id - right.id);

export function getMonsterSpawnReference(monsterName: string | undefined): MonsterSpawnData | undefined {
  return getMonsterSpawnDataByName(monsterName);
}

export function getBriefingMissionTemplatesForDate(date: BriefingCalendarDate) {
  const javaCalendarWeekday = new Date(Date.UTC(date.year, date.month - 1, date.day, 12)).getUTCDay() + 1;

  return BRIEFING_MISSION_TEMPLATES.filter((mission) => mission.challengeTerms.every((term) => {
    const [kind, value, comparer] = term;
    if (kind === 2) {
      if (comparer === undefined) return date.day === value;
      switch (comparer) {
        case 0: return date.day > value;
        case 1: return date.day >= value;
        case 2: return date.day <= value;
        case 3: return date.day < value;
        case 4: return date.day === value;
        case 5: return date.day !== value;
        default: return false;
      }
    }
    if (kind === 3) return javaCalendarWeekday === value;
    return false;
  }));
}

export function isSpecificMonsterMission(mission: BriefingMissionTemplate) {
  return mission.conditionType === 0
    && mission.conditionTarget >= 0
    && mission.challengeTerms.some((term) => term[0] === 2 && term[2] === 4);
}

export function getSpecificMonsterMissionForDate(date: BriefingCalendarDate) {
  return getBriefingMissionTemplatesForDate(date).find(isSpecificMonsterMission);
}

export function getScaledBriefingValue(minimum: number, maximum: number, progression: number | null) {
  if (progression === null || !Number.isFinite(progression)) return null;
  const frame = Math.max(BRIEFING_PROGRESS_MIN, Math.min(BRIEFING_PROGRESS_MAX, Math.trunc(progression)));
  const progress = Math.fround(frame / BRIEFING_PROGRESS_MAX);
  const difference = Math.fround(maximum - minimum);
  return Math.trunc(Math.fround(minimum + Math.fround(difference * progress)));
}

export function presentBriefingMission(mission: BriefingMissionTemplate, progression: number | null): BriefingMission {
  const monsterName = mission.conditionType === 0 && mission.conditionTarget >= 0
    ? getMonsterSpawnDataById(mission.conditionTarget)?.name ?? `Monster #${mission.conditionTarget}`
    : undefined;
  const goal = getScaledBriefingValue(mission.goalMin, mission.goalMax, progression);
  const rewardAmount = getScaledBriefingValue(mission.rewardMin, mission.rewardMax, progression);
  const displayName = mission.name
    .replace(/<0>/g, goal === null ? "—" : goal.toLocaleString("en-US"))
    .replace(/<1>/g, monsterName ?? "—");

  return {
    ...mission,
    monsterName,
    goal,
    rewardName: MISSION_REWARD_NAMES[mission.rewardId] ?? `Reward #${mission.rewardId}`,
    rewardAmount,
    displayName,
  };
}

export function getBriefingMissionsForDate(date: BriefingCalendarDate, progression: number | null) {
  return getBriefingMissionTemplatesForDate(date).map((mission) => presentBriefingMission(mission, progression));
}

export function getMonthlySpecificMonsterMissions(_year: number, _month: number, progression: number | null) {
  const missionCycleDays = 31;
  return BRIEFING_MISSION_TEMPLATES
    .filter(isSpecificMonsterMission)
    .flatMap((mission) => {
      const targetDay = mission.challengeTerms.find((term) => term[0] === 2 && term[2] === 4)?.[1];
      return targetDay !== undefined && targetDay >= 1 && targetDay <= missionCycleDays
        ? [{ day: targetDay, mission: presentBriefingMission(mission, progression) }]
        : [];
    })
    .sort((left, right) => left.day - right.day);
}
