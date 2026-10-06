import iconManifest from "../../../../website_icons/manifest.json";
import facilityIconManifest from "../../../../website_icons/facilities_confirmed/manifest.json";
import assembledFacilityIconManifest from "../../../../website_icons/facilities_assembled/manifest.json";

type ManifestVariant = { index?: number; filename?: string };
type ManifestFurnitureEntry = {
  name: string;
  filename: string;
  variants?: ManifestVariant[];
};
type ManifestFacilityEntry = {
  id: number;
  name?: string;
  filename: string;
  variants?: ManifestVariant[];
};
type ConfirmedFacilityIconEntry = {
  id: number;
  type?: string;
  name?: string;
  filename: string;
};
type ConfirmedFacilityManifest = {
  icons?: ConfirmedFacilityIconEntry[];
};

const ICON_CACHE_VERSION = "20260911r5";
const FIXED_EQUIPMENT_ICON_BY_NAME: Record<string, string> = {
  "B/ Legendary Shield (B)": "equip_192.png",
  "B/ Legendary Shield (R)": "equip_198.png",
  "E/ Hat (B)": "equip_235.png",
  "E/ Hat (R)": "equip_237.png",
};

function normalizeIconName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function normalizeItemLookupName(value: string): string {
  return value
    .toLowerCase()
    .replace(/<pic=([^>]+)>/g, "$1 ")
    .replace(/^(?:\d+\s*x\s*|x\s*\d+\s*)/i, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const FURNITURE_VARIANT2_PREFERRED = new Set([
  "cash register",
  "cash rigister",
  "ancestor statue",
  "study desk",
  "fireplace",
  "stove",
  "vanity mirror",
  "animal figurine",
  "candle",
  "flower",
  "flowers",
  "flower vase",
  "kitchen shelves",
  "chest of drawers",
  "shelf",
  "dresser",
  "decorative armor",
  "decortaive armor",
  "tool workshop",
  "chair",
  "ore workbench",
  "ore wirkbench",
  "shooting range",
  "training room",
]);

const FACILITY_STATE_PREFERENCE_BY_ID: Record<number, 1 | 2> = {
  70: 1,
  71: 1,
  76: 1,
  89: 1,
  238: 2,
};

function withPreferredFacilityStateFilename(id: number, filename: string): string {
  if (!filename.endsWith(".png") || filename.includes("_state")) return filename;
  const preferred = FACILITY_STATE_PREFERENCE_BY_ID[id];
  if (!preferred) return filename;
  return filename.replace(/\.png$/i, `_state${preferred}.png`);
}

function pickVariantFilename(
  variants: ManifestVariant[] | undefined,
  preferredVariantIndex: number,
  fallbackFilename: string,
): string {
  if (!Array.isArray(variants) || variants.length === 0) {
    return fallbackFilename;
  }

  const exact = variants.find(
    (variant) => variant.index === preferredVariantIndex && typeof variant.filename === "string" && variant.filename.length > 0,
  );
  if (exact?.filename) {
    return exact.filename;
  }

  const first = variants.find((variant) => typeof variant.filename === "string" && variant.filename.length > 0);
  return first?.filename ?? fallbackFilename;
}

// Build equipment name to icon path lookup from manifest
const equipmentIconLookup = new Map<string, string>();
if (iconManifest && iconManifest.equipment) {
  for (const equip of iconManifest.equipment) {
    const iconPath = `/website_icons/equipment/${equip.filename}?v=${ICON_CACHE_VERSION}`;
    // Store with original name
    equipmentIconLookup.set(equip.name, iconPath);
    // Also store with slash format (e.g., "A/ Ancient Sword")
    const slashName = toSlashEquipmentName(equip.name);
    equipmentIconLookup.set(slashName, iconPath);
    // Also store with dash format (e.g., "A- Ancient Sword")  
    const dashName = toDashEquipmentName(equip.name);
    equipmentIconLookup.set(dashName, iconPath);
  }
}

for (const [name, filename] of Object.entries(FIXED_EQUIPMENT_ICON_BY_NAME)) {
  const iconPath = `/website_icons/equipment/${filename}?v=${ICON_CACHE_VERSION}`;
  equipmentIconLookup.set(name, iconPath);
  equipmentIconLookup.set(toSlashEquipmentName(name), iconPath);
  equipmentIconLookup.set(toDashEquipmentName(name), iconPath);
}

export type EquipmentIconMap = Record<string, string> | undefined | null;

export function toSlashEquipmentName(name: string): string {
  return name.trim().replace(/^([FSABCDE])-\s*/i, (_, rank: string) => `${rank.toUpperCase()}/ `);
}

export function toDashEquipmentName(name: string): string {
  return name.trim().replace(/^([FSABCDE])\s*\/\s*/i, (_, rank: string) => `${rank.toUpperCase()}- `);
}

export function getEquipmentIconKeys(name: string | undefined | null): string[] {
  if (!name) return [];
  const cleanName = name.trim();
  if (!cleanName) return [];
  const slashName = toSlashEquipmentName(cleanName);
  const dashName = toDashEquipmentName(cleanName);
  return Array.from(new Set([
    `equip:${cleanName}`,
    cleanName,
    `equip:${slashName}`,
    slashName,
    `equip:${dashName}`,
    dashName,
  ]));
}

const PUBLIC_EQUIPMENT_ICON_NAMES = new Set([
  "A- Kairo Bow",
  "A- Kairo Gun",
  "A- Kairo Hammer",
  "A- Kairo Lance",
  "A- Kairo Sword",
  "A- Swift Bow",
  "D- Lumberjack's Axe",
  "E- Infantry Shield",
  "F- Adze",
  "F- Leather Shield",
  "F- Wooden Shield",
  "S- Legendary Axe",
]);

function publicEquipmentIconUrl(name: string): string | undefined {
  const dashName = toDashEquipmentName(name);
  if (!PUBLIC_EQUIPMENT_ICON_NAMES.has(dashName)) return undefined;
  return `/Images/cropped/${encodeURIComponent(dashName)}.png`;
}

export function getEquipmentIcon(icons: EquipmentIconMap, name: string | undefined | null): string | undefined {
  if (!name) return undefined;
  
  // First check the static equipment icon lookup from website_icons
  const cleanName = name.trim();
  if (equipmentIconLookup.has(cleanName)) {
    return equipmentIconLookup.get(cleanName);
  }
  
  // Try slash and dash variants
  const slashName = toSlashEquipmentName(cleanName);
  if (equipmentIconLookup.has(slashName)) {
    return equipmentIconLookup.get(slashName);
  }
  
  const dashName = toDashEquipmentName(cleanName);
  if (equipmentIconLookup.has(dashName)) {
    return equipmentIconLookup.get(dashName);
  }

  // Fallback to dynamic icon overrides if no canonical icon exists.
  if (icons) {
    for (const key of getEquipmentIconKeys(name)) {
      const icon = icons[key];
      if (icon) return icon;
    }
  }
  
  // Last fallback to public cropped images
  return publicEquipmentIconUrl(name);
}

// Build item name to icon path lookup from manifest
const itemIconLookup = new Map<string, string>();
if (iconManifest && iconManifest.items) {
  for (const item of iconManifest.items) {
    const rawFilename = String(item.filename ?? "").trim();
    if (!rawFilename) continue;
    // Some manifest entries are stored under logical folders (for example, valuable/*)
    // while exported website files live under /website_icons/<folder>/...
    const iconPath = rawFilename.includes("/")
      ? `/website_icons/${rawFilename}?v=${ICON_CACHE_VERSION}`
      : `/website_icons/items/${rawFilename}?v=${ICON_CACHE_VERSION}`;
    itemIconLookup.set(item.name, iconPath);
    itemIconLookup.set(item.name.toLowerCase(), iconPath);
    itemIconLookup.set(normalizeItemLookupName(item.name), iconPath);
  }
}

// Build egg color name to icon path lookup from manifest
const eggIconLookup = new Map<string, string>();
if (iconManifest && iconManifest.eggs) {
  for (const egg of iconManifest.eggs) {
    const iconPath = `/website_icons/eggs/${egg.filename}?v=${ICON_CACHE_VERSION}`;
    // Map full name "White Egg" → path
    eggIconLookup.set(egg.name, iconPath);
    // Map color-only "White" → path (strip " Egg" suffix)
    const colorOnly = egg.name.replace(/ Egg$/, "");
    eggIconLookup.set(colorOnly, iconPath);
    eggIconLookup.set(colorOnly.toLowerCase(), iconPath);
  }
}

export function getItemIcon(name: string | undefined | null): string | undefined {
  if (!name) return undefined;
  const clean = name.trim();
  if (!clean) return undefined;
  const normalized = normalizeItemLookupName(clean);
  if (normalized === "blessed rain") {
    return `/website_icons/items/item_058.png?v=${ICON_CACHE_VERSION}`;
  }
  return (
    itemIconLookup.get(clean)
    ?? itemIconLookup.get(clean.toLowerCase())
    ?? itemIconLookup.get(normalized)
  );
}

export function getEggIconByColor(colorName: string | undefined | null): string | undefined {
  if (!colorName) return undefined;
  const clean = colorName.trim();
  return eggIconLookup.get(clean) ?? eggIconLookup.get(clean.replace(/ Egg$/, "")) ?? eggIconLookup.get(clean.toLowerCase());
}

// Build facility ID to icon path lookup from manifest
const facilityIconLookup = new Map<number, string>();
const facilityNameIconLookup = new Map<string, string>();

// Finished catalog sprites composed from original parts. Keep their provenance
// separate from the older, individually confirmed sprite exports.
for (const icon of assembledFacilityIconManifest.icons) {
  const iconPath = `/website_icons/facilities_assembled/${icon.filename}?v=20260911r5`;
  facilityIconLookup.set(icon.id, iconPath);
  facilityNameIconLookup.set(normalizeIconName(icon.name), iconPath);
}

if (facilityIconManifest && Array.isArray((facilityIconManifest as ConfirmedFacilityManifest).icons)) {
  for (const icon of (facilityIconManifest as ConfirmedFacilityManifest).icons as ConfirmedFacilityIconEntry[]) {
    if (typeof icon.id !== "number" || !Number.isFinite(icon.id)) continue;
    if (typeof icon.filename !== "string" || icon.filename.length === 0) continue;
    if (icon.filename.startsWith("mapchip_")) continue;
    if (typeof icon.type === "string" && icon.type !== "facility") continue;
    const chosenFilename = withPreferredFacilityStateFilename(icon.id, icon.filename);
    const iconPath = `/website_icons/facilities_confirmed/${chosenFilename}?v=${ICON_CACHE_VERSION}`;
    if (!facilityIconLookup.has(icon.id)) {
      facilityIconLookup.set(icon.id, iconPath);
    }
    if (typeof icon.name === "string" && icon.name.trim().length > 0) {
      facilityNameIconLookup.set(normalizeIconName(icon.name), iconPath);
    }
  }
}

if (iconManifest && (iconManifest as Record<string, unknown>).facilities) {
  for (const facility of (iconManifest as Record<string, unknown>).facilities as ManifestFacilityEntry[]) {
    if (typeof facility.id !== "number" || !Number.isFinite(facility.id)) continue;
    if (!facility.filename) continue;
    const chosenFilename = pickVariantFilename(facility.variants, 1, facility.filename);
    const iconPath = `/website_icons/facilities_confirmed/${chosenFilename}?v=${ICON_CACHE_VERSION}`;
    if (!facilityIconLookup.has(facility.id)) {
      facilityIconLookup.set(facility.id, iconPath);
    }
    if (typeof facility.name === "string" && facility.name.trim().length > 0) {
      const normalizedName = normalizeIconName(facility.name);
      if (!facilityNameIconLookup.has(normalizedName)) {
        facilityNameIconLookup.set(normalizedName, iconPath);
      }
    }
  }
}

export function getFacilityIcon(id: number | undefined | null): string | undefined {
  if (typeof id !== "number" || !Number.isFinite(id)) return undefined;
  return facilityIconLookup.get(id);
}

export function getFacilityIconByName(name: string | undefined | null): string | undefined {
  if (!name) return undefined;
  const normalizedName = normalizeIconName(name);
  return facilityNameIconLookup.get(normalizedName);
}

// Build furniture name to icon path lookup from manifest
const furnitureIconLookup = new Map<string, string>();
if (iconManifest && (iconManifest as Record<string, unknown>).furniture) {
  for (const item of (iconManifest as Record<string, unknown>).furniture as ManifestFurnitureEntry[]) {
    const normalizedName = normalizeIconName(item.name);
    const preferredVariant = FURNITURE_VARIANT2_PREFERRED.has(normalizedName) ? 2 : 1;
    const chosenFilename = pickVariantFilename(item.variants, preferredVariant, item.filename);
    const iconPath = `/website_icons/furniture/${chosenFilename}?v=${ICON_CACHE_VERSION}`;
    furnitureIconLookup.set(item.name, iconPath);
    furnitureIconLookup.set(item.name.toLowerCase(), iconPath);
    furnitureIconLookup.set(normalizedName, iconPath);
  }
}

// Share the recovered, complete furniture crops with every furniture consumer.
for (const icon of assembledFacilityIconManifest.icons) {
  if (!icon.sources.some(source => source.startsWith("furniture/"))) continue;
  const iconPath = `/website_icons/facilities_assembled/${icon.filename}?v=20260911r5`;
  for (const name of [icon.name, icon.sourceName]) {
    furnitureIconLookup.set(name, iconPath);
    furnitureIconLookup.set(name.toLowerCase(), iconPath);
    furnitureIconLookup.set(normalizeIconName(name), iconPath);
  }
}

export function getFurnitureIcon(name: string | undefined | null): string | undefined {
  if (!name) return undefined;
  const clean = name.trim();
  return (
    furnitureIconLookup.get(clean) ??
    furnitureIconLookup.get(clean.toLowerCase()) ??
    furnitureIconLookup.get(normalizeIconName(clean))
  );
}
