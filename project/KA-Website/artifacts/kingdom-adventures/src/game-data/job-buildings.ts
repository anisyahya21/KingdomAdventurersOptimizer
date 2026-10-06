export type BuildingJobOwner = {
  jobName: string;
  rankNote?: string;
};

export const KNOWN_JOB_SHOPS: Record<string, string[]> = {
  Artisan: ["Furniture Shop"],
  Artist: ["Studio"],
  Blacksmith: ["Weapon Shop", "Armor Shop"],
  "Beast Tamer": ["Zoo"],
  Carpenter: ["Survey Corps HQ (Rank B+)"],
  Cook: ["Restaurant"],
  Doctor: ["Hospital"],
  Entertainer: ["Insectarium", "Aquarium", "Museum"],
  Farmer: ["Orchard (Rank C+)", "Survey Corps HQ (Rank B+)"],
  Mage: ["Skill Shop"],
  Merchant: ["Inn", "Survey Corps HQ (Rank B+)"],
  Monk: ["Church", "Recovery Room"],
  Mover: ["Survey Corps HQ (Rank B+)"],
  Rancher: ["Monster House", "Survey Corps HQ (Rank B+)"],
  Researcher: ["Analysis Lab", "Research Lab"],
  "Santa Claus": ["Santa's House"],
  Trader: ["Item Shop", "Accessory Shop"],
};

export const JOB_BUILDING_ROUTE_NAMES = new Set([
  "Analysis Lab",
  "Aquarium",
  "Church",
  "Hospital",
  "Insectarium",
  "Monster House",
  "Museum",
  "Orchard",
  "Research Lab",
  "Santa's House",
  "Studio",
  "Survey Corps HQ",
  "Zoo",
]);

export const SURVEY_CAPABLE_JOB_BASE_NAMES = new Set(
  getJobsForBuildingName("Survey Corps HQ").map((owner) => owner.jobName),
);

export function splitBuildingRankNote(entry: string) {
  const match = entry.match(/^(.*?)\s*\((Rank [^)]+)\)$/);
  if (!match) return { buildingName: entry, rankNote: undefined };
  return { buildingName: match[1].trim(), rankNote: match[2] };
}

function getJobsForBuildingName(buildingName: string): BuildingJobOwner[] {
  const normalizedLookupName = normalizeBuildingLookupName(buildingName);
  return Object.entries(KNOWN_JOB_SHOPS).flatMap(([jobName, buildings]) =>
    buildings.flatMap((entry) => {
      const parsed = splitBuildingRankNote(entry);
      return normalizeBuildingLookupName(parsed.buildingName) === normalizedLookupName
        ? [{ jobName, rankNote: parsed.rankNote }]
        : [];
    }),
  );
}

function normalizeBuildingLookupName(value: string) {
  return value.trim().replace(/\s+plot$/i, "").trim();
}

export function getJobsForBuilding(buildingName: string): BuildingJobOwner[] {
  return getJobsForBuildingName(buildingName);
}

export function formatBuildingJobOwners(buildingName: string): string | null {
  const owners = getJobsForBuilding(buildingName);
  if (owners.length === 0) return null;

  const notes = Array.from(new Set(owners.map((owner) => owner.rankNote).filter(Boolean)));
  const jobs = owners.map((owner) => owner.jobName).join("/");

  return notes.length === 1 ? `${jobs} ${notes[0]}` : owners.map((owner) =>
    owner.rankNote ? `${owner.jobName} ${owner.rankNote}` : owner.jobName
  ).join("/");
}
