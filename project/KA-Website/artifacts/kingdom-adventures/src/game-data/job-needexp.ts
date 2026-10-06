import { parseCsv } from "@/lib/monster-truth";

export const JOB_PARAMETER_ORDER = [
  "HP", "MP", "Vigor", "ATK", "DEF", "SPEED", "LUCK", "Owned?",
  "INT", "DEX", "CONS", "MOVE", "Heart",
] as const;
export type JobParameterKey = (typeof JOB_PARAMETER_ORDER)[number];

export type JobNeedExpProfile = {
  name: string;
  needExpByParameter: Record<JobParameterKey, number>;
  maxLevelByParameter: Record<JobParameterKey, number>;
};

/** Maps STAT_ORDER names (used in jobs.tsx) to JobParameterKey (used in the CSV). */
export const STAT_TO_NEEDEXP_KEY: Record<string, JobParameterKey> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vigor",
  Attack: "ATK",
  Defence: "DEF",
  Speed: "SPEED",
  Luck: "LUCK",
  Intelligence: "INT",
  Dexterity: "DEX",
  Gather: "CONS",
  Move: "MOVE",
  Heart: "Heart",
};

/**
 * Parses the Job.csv to produce per-job needExp multiplier profiles.
 *
 * @param rawCsv - raw CSV string (import with `?raw`)
 * @param canonicalNames - optional set of lower-cased canonical job names to filter by
 */
export function parseJobNeedExpProfiles(
  rawCsv: string,
  canonicalNames?: Set<string>,
): JobNeedExpProfile[] {
  const rows = parseCsv(rawCsv);
  if (rows.length === 0) return [];

  const headerRowIndex = rows.findIndex((row) => {
    const normalized = row.map((cell) => cell.trim());
    return normalized.includes("id") && normalized.includes("name") && normalized.includes("maxLevel");
  });
  if (headerRowIndex < 0) return [];

  const header = rows[headerRowIndex].map((cell) => cell.trim());
  const nameIndex = header.findIndex((cell) => /^name$/i.test(cell));
  const statStartIndex = header.findIndex((cell) => /^maxLevel$/i.test(cell));
  if (nameIndex < 0 || statStartIndex < 0) return [];

  const profilesByBaseName = new Map<string, JobNeedExpProfile>();
  const gradePrefix = /^(S|A|B|C|D|E|F)\s+(Grade|Rank)\s+/i;

  for (const row of rows.slice(headerRowIndex + 1)) {
    const rawName = String(row[nameIndex] ?? "").trim();
    const baseName = rawName.replace(gradePrefix, "").trim();
    if (canonicalNames && canonicalNames.size > 0 && !canonicalNames.has(baseName.toLowerCase())) continue;
    if (!baseName || profilesByBaseName.has(baseName)) continue;

    const needExpByParameter = {} as Record<JobParameterKey, number>;
    const maxLevelByParameter = {} as Record<JobParameterKey, number>;
    JOB_PARAMETER_ORDER.forEach((parameter, index) => {
      const maxLevelIndex = statStartIndex + (index * 5);
      const needExpIndex = statStartIndex + (index * 5) + 1;
      const maxLevelValue = Number(row[maxLevelIndex] ?? "");
      const value = Number(row[needExpIndex] ?? "");
      maxLevelByParameter[parameter] = Number.isFinite(maxLevelValue) && maxLevelValue > 0 ? maxLevelValue : 1;
      needExpByParameter[parameter] = Number.isFinite(value) && value > 0 ? value : 100;
    });

    profilesByBaseName.set(baseName, { name: baseName, needExpByParameter, maxLevelByParameter });
  }

  return Array.from(profilesByBaseName.values()).sort((a, b) => a.name.localeCompare(b.name));
}
