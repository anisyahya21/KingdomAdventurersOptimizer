import skillIconMap from "../../../../website_icons/skills/skill_icon_map.json";

type SkillIconRecord = {
  name: string;
  id: number;
  category: number;
  iconIndex: number;
};

type SkillIconMap = {
  categoryToIconIndex: Record<string, number>;
  categoryCounts: Record<string, number>;
  skillsByName: Record<string, SkillIconRecord>;
  skillsByNormalizedName: Record<string, SkillIconRecord>;
};

const SKILL_ICON_CACHE_VERSION = "20260526s1";
const mapData = skillIconMap as SkillIconMap;

function normalizeSkillName(value: string): string {
  const normalizedRoman = value
    .toLowerCase()
    .replace(/â… /g, " i")
    .replace(/â…¡/g, " ii")
    .replace(/â…¢/g, " iii")
    .replace(/â…£/g, " iv")
    .replace(/â…¤/g, " v")
    .replace(/Ⅰ/g, " i")
    .replace(/Ⅱ/g, " ii")
    .replace(/Ⅲ/g, " iii")
    .replace(/Ⅳ/g, " iv")
    .replace(/Ⅴ/g, " v");

  return normalizedRoman.replace(/[^a-z0-9]+/g, " ").trim();
}

function iconUrl(iconIndex: number): string {
  return `/website_icons/skills/skill_icon_${String(iconIndex).padStart(2, "0")}.png?v=${SKILL_ICON_CACHE_VERSION}`;
}

function fallbackIconIndexByName(normalized: string): number | undefined {
  if (normalized.startsWith("ice magic")) return 1;
  if (normalized.startsWith("lightning magic")) return 2;
  if (normalized.startsWith("fire magic")) return 3;
  if (normalized.startsWith("direct attack")) return 5;
  if (normalized.startsWith("area attack")) return 5;
  if (normalized.startsWith("deployment range")) return 5;
  if (normalized.startsWith("experience up")) return 6;
  return undefined;
}

export function getSkillIcon(name: string | undefined | null): string | undefined {
  if (!name) return undefined;
  const exact = mapData.skillsByName[name];
  if (exact && typeof exact.iconIndex === "number" && exact.iconIndex > 0) {
    return iconUrl(exact.iconIndex);
  }

  const normalized = normalizeSkillName(name);
  if (!normalized) return undefined;

  const record = mapData.skillsByNormalizedName[normalized];
  if (record && typeof record.iconIndex === "number" && record.iconIndex > 0) {
    return iconUrl(record.iconIndex);
  }

  const fallbackIcon = fallbackIconIndexByName(normalized);
  return typeof fallbackIcon === "number" ? iconUrl(fallbackIcon) : undefined;
}

export function getSkillIconCategory(name: string | undefined | null): number | undefined {
  if (!name) return undefined;
  const normalized = normalizeSkillName(name);
  const record = mapData.skillsByNormalizedName[normalized];
  if (record?.category != null) return record.category;

  const fallbackIcon = fallbackIconIndexByName(normalized);
  if (fallbackIcon === 1) return 0;
  if (fallbackIcon === 2) return 1;
  if (fallbackIcon === 3) return 2;
  if (fallbackIcon === 5) return 4;
  if (fallbackIcon === 6) return 5;
  return undefined;
}
