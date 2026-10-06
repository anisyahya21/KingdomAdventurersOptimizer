/**
 * Thin query layer over the generated treasure-source projection.
 *
 * The data owner is the reverse-engineering foundation:
 *   RE-evidence/20260917-treasure-source-graph/treasure-source-graph.json
 *   RE-evidence/20260917-treasure-source-graph/treasure-profiles.json
 * regenerated into this app's game-data folder by
 *   RE-evidence/20260917-treasure-source-graph/website-export/export_website_profiles.py
 *
 * This module only indexes and shapes that output. It must never regroup treasures, invent a
 * source, promote an evidence grade, or derive a probability the foundation did not derive.
 */
import raw from "@/game-data/treasure-source-profiles.json";
import { DAILY_RANK_REWARDS } from "@/game-data/daily-rank-rewards";
import caves from "@/game-data/native-caves.json";
import { matchesLooseSearch, normalizeSearchText } from "@/lib/search-normalize";
import { treasureDisplayName } from "@/lib/treasure-lookup";

export type RewardType = "item" | "equipment" | "skill" | "furniture" | "valuable" | "job";
export type EvidenceGrade =
  | "native-executed confirmed"
  | "native/static-code confirmed"
  | "direct table relationship"
  | "supported inference"
  | "website-only/manual"
  | "unresolved";
export type ProbabilityBasis = "executed-confirmed" | "static-code-derived" | "direct-table-only" | "unknown";

export type Probability = { fraction: string; decimal: number };
export type FormulaProbability = { kind: "formula"; formula: string; note?: string };
/** A source probability is either an established number or an explicit formula placeholder. */
export type SourceProbability = Probability | FormulaProbability;

export type Lane = { lane: string; type: RewardType; id: number; name: string; rate: number; min: number; max: number };
export type ProfileEdge = {
  key: string;
  edgeId: string;
  source: string;
  mechanism: string;
  treasure: number;
  p?: SourceProbability;
  conditions?: Record<string, unknown>;
  note?: string;
  grade?: EvidenceGrade;
  basis?: ProbabilityBasis;
  ref?: string;
};
export type ProfileRoute = {
  source: string;
  mechanism: string;
  keys: string[];
  treasure: number[];
  p?: SourceProbability;
  conditions?: Record<string, unknown>;
};
export type DerivedRow = { key: string; lane: string; p: Probability; basis: ProbabilityBasis };
export type Variant = {
  id: number;
  name: string;
  hp: number;
  group: number;
  minLevel: number;
  maxLevel: number;
  rank: number;
  week: number;
  flag: number;
  keys: string[];
};
export type Profile = {
  id: string;
  grouped: boolean;
  rawEquivalent?: boolean;
  name: string;
  nativeNames: string[];
  res: number;
  img: number;
  icon: string;
  loot: string;
  memberIds: number[];
  lanes: Lane[];
  edges: ProfileEdge[];
  routes: ProfileRoute[];
  variants: Variant[];
  variantFields: string[];
  unresolvedIds: number[];
  derived: DerivedRow[];
};
export type DirectSource = {
  name: string;
  type: RewardType;
  source: string;
  mechanism: string;
  conditions?: Record<string, unknown>;
  input?: string;
  quantity?: number;
};
export type SourceNode = { key: string; kind: string; label: string; mechanism: string; conditions?: Record<string, unknown> };
export type MechanismInfo = { name: string; grade: EvidenceGrade; ref: string; basis: ProbabilityBasis };

type Projection = {
  schemaVersion: number;
  name: string;
  basis: Record<string, string>;
  note: string;
  probabilityConventions: Record<string, string>;
  evidenceGrades: Record<EvidenceGrade, string>;
  mechanisms: Record<string, MechanismInfo>;
  sources: Record<string, SourceNode>;
  profiles: Profile[];
  directSources: DirectSource[];
  stats: Record<string, number>;
};

const DATA = raw as unknown as Projection;

export const PROFILES: Profile[] = DATA.profiles;
export const PROFILE_BY_ID: Map<string, Profile> = new Map(PROFILES.map((profile) => [profile.id, profile]));
export const SOURCE_NODES: Record<string, SourceNode> = DATA.sources;
export const MECHANISMS: Record<string, MechanismInfo> = DATA.mechanisms;
export const EVIDENCE_GRADES: Record<EvidenceGrade, string> = DATA.evidenceGrades;
export const PROBABILITY_CONVENTIONS: Record<string, string> = DATA.probabilityConventions;
export const PROJECTION_STATS = DATA.stats;
export const PROJECTION_BASIS = DATA.basis;

/** Same normalisation the rest of the site uses, so "A/ Kairo Bow" and "A Kairo Bow" agree. */
export const rewardKey = (type: RewardType | string, name: string) => {
  const normalized = normalizeSearchText(name);
  return normalized ? `${type}|${normalized}` : "";
};

const lanesByReward = new Map<string, { profile: Profile; lanes: Lane[] }[]>();
const directByReward = new Map<string, DirectSource[]>();
const targets = new Map<string, { name: string; type: RewardType; profileIds: Set<string>; direct: number }>();

function targetFor(type: RewardType, name: string) {
  const key = rewardKey(type, name);
  if (!key) return undefined;
  let entry = targets.get(key);
  if (!entry) {
    entry = { name, type, profileIds: new Set<string>(), direct: 0 };
    targets.set(key, entry);
  }
  return entry;
}

for (const profile of PROFILES) {
  const perReward = new Map<string, Lane[]>();
  for (const lane of profile.lanes) {
    const key = rewardKey(lane.type, lane.name);
    if (!key || lane.rate <= 0) continue;
    perReward.set(key, [...(perReward.get(key) ?? []), lane]);
  }
  for (const [key, lanes] of perReward) {
    lanesByReward.set(key, [...(lanesByReward.get(key) ?? []), { profile, lanes }]);
    targetFor(lanes[0].type, lanes[0].name)?.profileIds.add(profile.id);
  }
}

for (const direct of DATA.directSources) {
  const key = rewardKey(direct.type, direct.name);
  if (!key) continue;
  directByReward.set(key, [...(directByReward.get(key) ?? []), direct]);
  const entry = targetFor(direct.type, direct.name);
  if (entry) entry.direct += 1;
}

export type RewardTarget = { name: string; type: RewardType; profileCount: number; directCount: number };

/** Every item / skill / equipment / furniture name the graph can say something about. */
export const REWARD_TARGETS: RewardTarget[] = [...targets.values()]
  .map((entry) => ({ name: entry.name, type: entry.type, profileCount: entry.profileIds.size, directCount: entry.direct }))
  .sort((a, b) => a.name.localeCompare(b.name) || a.type.localeCompare(b.type));

const targetByKey = new Map(REWARD_TARGETS.map((target) => [rewardKey(target.type, target.name), target]));

export function getRewardTarget(type: RewardType, name: string): RewardTarget | undefined {
  return targetByKey.get(rewardKey(type, name));
}

export function searchRewardTargets(query: string, limit = 30): RewardTarget[] {
  const trimmed = query.trim();
  if (!trimmed) return REWARD_TARGETS.slice(0, limit);
  return REWARD_TARGETS.filter((target) => matchesLooseSearch(target.name, trimmed)).slice(0, limit);
}

/** Treasure profiles whose contents include this exact reward. Grouped once per profile. */
export function profilesForReward(type: RewardType, name: string): { profile: Profile; lanes: Lane[] }[] {
  return lanesByReward.get(rewardKey(type, name)) ?? [];
}

/** Non-treasure acquisition routes (shop, gacha, exchange, crafting, facility, …). */
export function directSourcesForReward(type: RewardType, name: string): DirectSource[] {
  return directByReward.get(rewardKey(type, name)) ?? [];
}

const haystacks = new Map<string, string>(
  PROFILES.map((profile) => [
    profile.id,
    normalizeSearchText([
      profile.name,
      ...profile.nativeNames,
      ...profile.memberIds,
      profile.loot,
      ...profile.lanes.filter((lane) => lane.rate > 0).map((lane) => lane.name),
      ...profile.routes.map((route) => `${SOURCE_NODES[route.source]?.label ?? ""} ${route.mechanism}`),
      ...profile.variants.map((variant) => variant.name),
    ].join(" ")),
  ]),
);

/** Browse search over player-facing profiles — never over the 1,295 raw rows. */
export function searchProfiles(query: string, limit = 24): Profile[] {
  const trimmed = query.trim();
  if (!trimmed) return PROFILES.filter((profile) => profile.edges.length > 0).slice(0, limit);
  const words = normalizeSearchText(trimmed).split(" ").filter(Boolean);
  return PROFILES.filter((profile) => {
    const haystack = haystacks.get(profile.id) ?? "";
    return words.every((word) => haystack.includes(word));
  }).slice(0, limit);
}

export const activeLanes = (profile: Profile): Lane[] => profile.lanes.filter((lane) => lane.rate > 0);
export const profileHasSource = (profile: Profile): boolean => profile.edges.length > 0;
export const sourceNode = (sourceId: string): SourceNode | undefined => SOURCE_NODES[sourceId];
export const sourceLabel = (sourceId: string): string => SOURCE_NODES[sourceId]?.label ?? sourceId;
export const sourceKind = (sourceId: string): string => SOURCE_NODES[sourceId]?.kind ?? "unknown";
export const mechanismInfo = (mechanism: string): MechanismInfo | undefined => MECHANISMS[mechanism];

export function edgeGrade(edge: ProfileEdge): EvidenceGrade | undefined {
  return edge.grade ?? MECHANISMS[edge.mechanism]?.grade;
}
export function edgeBasis(edge: ProfileEdge): ProbabilityBasis | undefined {
  return edge.basis ?? MECHANISMS[edge.mechanism]?.basis;
}
export const routeGrade = (route: ProfileRoute): EvidenceGrade | undefined => MECHANISMS[route.mechanism]?.grade;
export const routeBasis = (route: ProfileRoute): ProbabilityBasis | undefined => MECHANISMS[route.mechanism]?.basis;

export const numericProbability = (value?: SourceProbability): number | undefined =>
  value && "decimal" in value ? value.decimal : undefined;
export const formulaProbability = (value?: SourceProbability): FormulaProbability | undefined =>
  value && "formula" in value ? value : undefined;

export function formatPercent(decimal: number, digits = 2): string {
  const percent = decimal * 100;
  return `${percent.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

export function formatQuantity(min: number, max: number): string {
  return min === max ? `${min}` : `${min}–${max}`;
}

/**
 * The foundation's already-derived "overall from this source" number, if it emitted one for this
 * route and reward lane. Never recomputed here.
 */
export function routeDerived(profile: Profile, route: ProfileRoute, lane: string): DerivedRow[] {
  return profile.derived.filter((row) => row.lane === lane && route.keys.includes(row.key));
}

export type GradeTone = "executed" | "confirmed" | "table" | "inference" | "manual" | "unknown";

export function gradeTone(grade?: EvidenceGrade | string): GradeTone {
  switch (grade) {
    case "native-executed confirmed": return "executed";
    case "native/static-code confirmed": return "confirmed";
    case "direct table relationship": return "table";
    case "supported inference": return "inference";
    case "website-only/manual": return "manual";
    default: return "unknown";
  }
}

/** Display order: executed evidence first, unknown last. */
export const GRADE_ORDER: Record<GradeTone, number> = {
  executed: 0, confirmed: 1, table: 2, inference: 3, manual: 4, unknown: 5,
};

/** Short label for badges; the full grade string stays available in the technical section. */
export function gradeShort(grade?: EvidenceGrade | string): string {
  switch (grade) {
    case "native-executed confirmed": return "Executed";
    case "native/static-code confirmed": return "Native code";
    case "direct table relationship": return "Table link";
    case "supported inference": return "Inferred";
    case "website-only/manual": return "Site mapping";
    default: return "Unknown";
  }
}

/** Player-facing wording. The RE wording lives in the graph, not on the page. */
export const GRADE_LABELS_PLAIN: Record<EvidenceGrade, string> = {
  "native-executed confirmed": "Verified by running the game code",
  "native/static-code confirmed": "Verified from the game code",
  "direct table relationship": "Taken from the game data tables",
  "supported inference": "Inferred from the game data tables",
  "website-only/manual": "From a site-maintained list",
  "unresolved": "No source established",
};

export const GRADE_NOTES_PLAIN: Record<EvidenceGrade, string> = {
  "native-executed confirmed": "the original code was run and produced this result",
  "native/static-code confirmed": "read from the original code, not run here",
  "direct table relationship": "the game data references it; no code path traced",
  "supported inference": "a plausible table join only",
  "website-only/manual": "kept by this site, not confirmed in the game",
  "unresolved": "no acquisition route established",
};

export function gradeSentence(grade?: EvidenceGrade | string): string {
  return GRADE_LABELS_PLAIN[grade as EvidenceGrade] ?? "Not established";
}

export function basisShort(basis?: ProbabilityBasis | string): string {
  switch (basis) {
    case "executed-confirmed": return "from the executed code path";
    case "static-code-derived": return "from static code, not executed";
    case "direct-table-only": return "from the table link only";
    default: return "not established";
  }
}

/** Human summary of the conditions an edge/route carries, without inventing anything. */
const CONDITION_LABELS: Record<string, string> = {
  dungeonTemplateId: "floor configuration",
  dungeonRowId: "cave layout",
  slotCheckRate: "slot check",
  treasureRate: "placement check",
  minTreasureHitCount: "minimum chests",
  maxTreasureHitCount: "maximum chests",
  dropRate: "drop chance",
  dropGroup: "terrain drop group",
  areaId: "area",
  level: "area level",
  terrain: "terrain",
  minLevel: "minimum level",
  maxLevel: "maximum level",
  surveyId: "survey",
  minAreaLevel: "minimum area level",
  rewardGroup: "encounter reward group",
  poolSize: "pool size",
  bossLevel: "encounter level",
  difficulty: "difficulty",
  sourceRowId: "reward row",
  treasureMinLevel: "box level bound (min)",
  treasureMaxLevel: "box level bound (max)",
  treasureId: "box reference",
  maxDropCount: "maximum drops",
  dropNum: "drop count",
  craftGroup: "craft group",
  facility: "facility",
  materialShopRowId: "shop row",
  saleItemId: "sale item id",
  saleAmount: "sale amount",
  priceMin: "price min",
  priceMax: "price max",
  purchaseLimit: "purchase limit",
  saleTerm: "availability term",
};

export function conditionLabel(key: string): string {
  return CONDITION_LABELS[key] ?? key.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}

export function conditionEntries(conditions?: Record<string, unknown>): { key: string; label: string; value: string }[] {
  if (!conditions) return [];
  return Object.entries(conditions)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => ({
      key,
      label: conditionLabel(key),
      value: Array.isArray(value) ? value.join(", ") : String(value),
    }));
}

export function mechanismName(mechanism: string): string {
  return MECHANISMS[mechanism]?.name ?? mechanism;
}

/* ------------------------------------------------- display-only source grouping */

/**
 * A cave dungeon row is placed in exactly one map area; the area carries the player-facing number
 * and level. The graph labels normal-cave rows by their internal row id, so the label is lifted
 * from the same recovered cave table the rest of the site uses. Display only — the graph's own
 * source key, conditions and edges are untouched.
 */
type NativeArea = { id: number; level: number; terrain: number; dungeonId: number };
const AREA_BY_DUNGEON: Map<number, NativeArea> = new Map(
  (caves as { areas: NativeArea[] }).areas.map((area) => [area.dungeonId, area]),
);
const CAVE_ROW_KEY = /^(?:cave|cave-end):(\d+)$/;
const TECHNICAL_SUFFIX = /\s*\((?:SpecialBoss\s*\d+|#\d+)\)\s*$/;

function tidyLabel(label: string): string {
  return treasureDisplayName(label)
    .replace(TECHNICAL_SUFFIX, "")
    // "terrain drop group 100" is internal shorthand; keep the number, drop the jargon.
    .replace(/terrain drop group/i, "terrain group")
    .trim();
}

/**
 * The Source column and the Requirement column of the item table. Caves become
 * "Normal Cave" + "Area #74 · Level 320"; a daily-rank row becomes
 * "Daily Rank Bonus" + "Wednesday · Rank E".
 */
export function sourceFamilyAndDetail(sourceId: string): { family: string; detail: string } {
  const node = SOURCE_NODES[sourceId];
  if (!node) return { family: sourceId, detail: "" };
  const match = CAVE_ROW_KEY.exec(node.key);
  if (match) {
    const area = AREA_BY_DUNGEON.get(Number(match[1]));
    const parts = area ? [`Area #${area.id}`, `Level ${area.level}`] : [`Row ${match[1]}`];
    if (node.kind === "normal-cave-end-chest") parts.push("completion chest");
    return { family: "Normal Cave", detail: parts.join(" · ") };
  }
  const label = tidyLabel(node.label);
  switch (node.kind) {
    case "legendary-cave": return { family: "Legendary Cave", detail: "Any completed floor" };
    case "legendary-cave-end-chest": return { family: "Legendary Cave", detail: "Fixed end chest" };
    case "terrain":
    case "terrain-reference": return { family: "Gathering", detail: label.replace(/^Gathering · /, "") };
    case "monster": return { family: "Monster drop", detail: label.replace(/^Defeat /, "") };
    case "daily-rank": return { family: "Daily Rank Bonus", detail: "" };
    case "survey": return { family: "Survey", detail: label.replace(/^Survey: /, "") };
    case "area": return { family: "Area reward", detail: label.replace(/^Clear area /, "Area ") };
    case "kairo-room": return { family: "Kairo Room", detail: label };
    case "wairo-dungeon": return { family: "Wairo Dungeon", detail: label };
    case "arena": return { family: "Arena", detail: "PvP reward pool" };
    default: return { family: label, detail: "" };
  }
}

/** Player-facing source name: area + level for caves, otherwise the graph label without its id. */
export function playerFacingSourceLabel(sourceId: string): string {
  const { family, detail } = sourceFamilyAndDetail(sourceId);
  return detail ? `${family} · ${detail}` : family;
}

/** The source's own key, e.g. "terrain-gather:100" — kept for the technical section. */
export const sourceKeyOf = (sourceId: string): string => SOURCE_NODES[sourceId]?.key ?? "";

export type DisplaySourceVariant = {
  keys: string[];
  treasureIds: number[];
  edges: ProfileEdge[];
  p?: SourceProbability;
  conditions?: Record<string, unknown>;
  /** The graph source node this variant's edge comes from, e.g. "survey:0". */
  sourceKey: string;
  /** Player-facing difference (level range). Empty when the difference is internal only. */
  summary: string;
  /** Internal difference — only ever shown inside the Evidence / mechanics detail. */
  technical: string;
};

/**
 * One row in the UI for one player-facing acquisition method. Several graph routes/edges can feed
 * it (terrain groups, endless-cave floor configurations, repeated cave rows); none of them are
 * merged, dropped or rewritten — `routes` and `edges` hold the originals verbatim.
 */
export type DisplaySourceGroup = {
  key: string;
  /** Every graph source node folded into this row (surveys of the same name share a label). */
  sourceIds: string[];
  sourceId: string;
  sourceKeys: string[];
  kind: string;
  label: string;
  sublabel: string[];
  mechanism: string;
  grade?: EvidenceGrade;
  basis?: ProbabilityBasis;
  evidenceRef?: string;
  routes: ProfileRoute[];
  edges: ProfileEdge[];
  variants: DisplaySourceVariant[];
  derived: DerivedRow[];
  probability?: SourceProbability;
  probabilityVaries: boolean;
};

const conditionNumber = (conditions: Record<string, unknown> | undefined, key: string): number | undefined => {
  const value = conditions?.[key];
  return typeof value === "number" ? value : undefined;
};

/** Player-facing difference inside a group: only an area/box level range is worth showing. */
function variantSummary(conditions?: Record<string, unknown>): string {
  if (!conditions) return "";
  const min = conditionNumber(conditions, "treasureMinLevel") ?? conditionNumber(conditions, "minLevel");
  const max = conditionNumber(conditions, "treasureMaxLevel") ?? conditionNumber(conditions, "maxLevel");
  if (min !== undefined && max !== undefined) return `level ${min}\u2013${max}`;
  const areaLevel = Number(conditions.minAreaLevel);
  if (Number.isFinite(areaLevel) && conditions.minAreaLevel !== undefined) return `area level ${areaLevel}+`;
  return "";
}

/** Internal difference between sibling edges: row id, floor setup, slot check, drop group… */
function variantTechnical(conditions?: Record<string, unknown>): string {
  return conditionEntries(conditions).map((entry) => `${entry.label} ${entry.value}`).join(" · ");
}

/** Distinct player-facing level requirements inside one source group, lowest first. */
function levelRanges(variants: DisplaySourceVariant[]): string[] {
  const seen = new Map<string, number>();
  for (const variant of variants) {
    if (!variant.summary) continue;
    const numbers = variant.summary.match(/\d+/g)?.map(Number) ?? [];
    seen.set(variant.summary, numbers.length ? Math.min(...numbers) : Number.MAX_SAFE_INTEGER);
  }
  const sorted = [...seen.entries()].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]));
  return sorted.length > 6
    ? [...sorted.slice(0, 6).map(([text]) => text), `+${sorted.length - 6} more`]
    : sorted.map(([text]) => text);
}

/**
 * View-layer grouping: same player-facing source (one graph source node, one mechanism) becomes
 * one row. Never written back to the projection and never used for probability math.
 */
export function groupProfileSources(profile: Profile, routes: ProfileRoute[] = profile.routes): DisplaySourceGroup[] {
  const buckets = new Map<string, ProfileRoute[]>();
  for (const route of routes) {
    // Group by what the player sees: several source nodes can share one player-facing name
    // (e.g. surveys that differ only by required area level). The nodes stay separate below.
    const key = `${playerFacingSourceLabel(route.source)}|${route.mechanism}`;
    buckets.set(key, [...(buckets.get(key) ?? []), route]);
  }
  const groups: DisplaySourceGroup[] = [];
  for (const [key, groupRoutes] of buckets) {
    const mechanism = groupRoutes[0].mechanism;
    const sourceIds = [...new Set(groupRoutes.map((route) => route.source))];
    const keys = new Set(groupRoutes.flatMap((route) => route.keys));
    const edges = profile.edges.filter((edge) => sourceIds.includes(edge.source) && keys.has(edge.key));
    const variants: DisplaySourceVariant[] = groupRoutes.map((route) => {
      const routeEdges = edges.filter((edge) => route.keys.includes(edge.key));
      // Node conditions describe where the source exists (terrain, minimum area level), edge
      // conditions describe this particular selection. Both are foundation data; the merge is
      // for display only and the edge wins on any shared key.
      const conditions = { ...(SOURCE_NODES[route.source]?.conditions ?? {}), ...(route.conditions ?? {}) };
      return {
        keys: route.keys,
        treasureIds: [...new Set(routeEdges.map((edge) => edge.treasure))].sort((a, b) => a - b),
        edges: routeEdges,
        p: route.p,
        conditions,
        sourceKey: sourceKeyOf(route.source),
        summary: variantSummary(conditions),
        technical: variantTechnical(conditions),
      };
    });
    const signatures = new Set(variants.map((variant) => JSON.stringify(variant.p ?? null)));
    const probability = signatures.size === 1 ? variants[0].p : undefined;
    groups.push({
      key,
      sourceIds,
      sourceId: sourceIds[0],
      sourceKeys: sourceIds.map(sourceKeyOf),
      kind: sourceKind(sourceIds[0]),
      label: playerFacingSourceLabel(sourceIds[0]),
      sublabel: levelRanges(variants),
      mechanism,
      grade: MECHANISMS[mechanism]?.grade,
      basis: MECHANISMS[mechanism]?.basis,
      evidenceRef: MECHANISMS[mechanism]?.ref,
      routes: groupRoutes,
      edges,
      variants,
      derived: profile.derived.filter((row) => keys.has(row.key)),
      probability,
      probabilityVaries: signatures.size > 1,
    });
  }
  return groups.sort((a, b) =>
    GRADE_ORDER[gradeTone(a.grade)] - GRADE_ORDER[gradeTone(b.grade)] || a.label.localeCompare(b.label));
}

/** Overall-from-this-source rows for one reward lane inside a group (empty when not established). */
export function groupDerivedForLane(group: DisplaySourceGroup, lane: string): DerivedRow[] {
  return group.derived.filter((row) => row.lane === lane);
}

/* ---------------------------------------- item → source table (item-search mode) */

/** Every floor configuration a source has, read from the graph's own edge conditions. */
const TEMPLATE_IDS_BY_SOURCE: Map<string, Set<number>> = new Map();
for (const profile of PROFILES) {
  for (const edge of profile.edges) {
    const template = edge.conditions?.dungeonTemplateId;
    if (typeof template !== "number") continue;
    const set = TEMPLATE_IDS_BY_SOURCE.get(edge.source) ?? new Set<number>();
    set.add(template);
    TEMPLATE_IDS_BY_SOURCE.set(edge.source, set);
  }
}
export const templateCountFor = (sourceId: string): number => TEMPLATE_IDS_BY_SOURCE.get(sourceId)?.size ?? 0;

/** What one unit of the overall chance is. The denominator is never left implicit. */
const MECHANISM_OUTCOME: Record<string, string> = {
  "place-treasures-endless": "per floor",
  "place-treasures-normal": "per chest",
  "place-dungeon-objects-end-chest": "per floor",
  "ordinary-monster-drop": "per defeat",
  "guerrilla-prize-group": "per victory",
  "survey-reward": "per survey",
  "area-reward": "per area",
  "daily-rank-reward": "",
  "pvp-reward-pool": "per battle",
  "terrain-gather": "",
  "terrain-gather-category1": "",
};
/** Mechanisms whose stored probability is scoped to one floor configuration. */
const TEMPLATE_SCOPED = new Set(["place-treasures-endless"]);

export type ItemSourceNumber = { text: string; detail: string; established: boolean };

const REWARD_TYPE_LABELS: Record<string, string> = {
  item: "Item", equipment: "Equipment", skill: "Skill", furniture: "Furniture", valuable: "Valuable", job: "Job",
};
const PLACEHOLDER_REWARD_NAMES = new Set(["", "-1", "not used", "未使用", "unused", "n/a"]);

/**
 * Never let a placeholder or bare-number label reach the main view. The projection already
 * resolves the skill/furniture names; this is the fallback the spec asks for when it cannot.
 */
export function safeRewardLabel(type: RewardType, name: string, id?: number): string {
  const text = (name ?? "").trim();
  const placeholder = PLACEHOLDER_REWARD_NAMES.has(text.toLowerCase()) || /^-?\d+$/.test(text);
  if (!placeholder) return text;
  const label = REWARD_TYPE_LABELS[type] ?? "Reward";
  return id !== undefined ? `${label} #${id}` : label;
}

export type ItemSourceRow = {
  key: string;
  /** True for a route that is not a treasure box (shop, gacha, exchange, crafting, facility). */
  direct?: boolean;
  /** Source column, e.g. "Legendary Cave", "Monster drop", "Daily Rank Bonus". */
  family: string;
  /** Requirement column, e.g. "Area #74 · Level 320", "Wednesday · Rank E". */
  requirement: string;
  sourceId: string;
  kind: string;
  mechanism: string;
  grade?: EvidenceGrade;
  grades: EvidenceGrade[];
  treasureName: string;
  treasureIcon: string;
  /** Short qualifier under the treasure name, e.g. "3 of 10 floor setups". */
  treasureNote: string;
  /** Full one-line explanation for the expandable detail. */
  note: string;
  /** Non-treasure routes only: the recorded fields of the route (craft group, shop row, price…). */
  conditions?: Record<string, unknown>;
  quantity: string;
  treasureRate: ItemSourceNumber;
  itemRate: ItemSourceNumber;
  overall: ItemSourceNumber;
  variantIds: number[];
  edges: ProfileEdge[];
  /** Human-readable derivation for the expandable detail. */
  derivation: string;
};

const DIRECT_FAMILY: Record<string, string> = {
  "item-flag-shop": "Item Shop",
  "item-flag-restaurant": "Restaurant",
  "craft-group-orchard": "Orchard",
  "craft-group-facility": "Crafting",
  "skill-crafting": "Skill shop",
  "gacha-pool": "Gacha",
  "equipment-exchange": "Equipment Exchange",
  "material-shop": "Material Shop",
};

/** Non-treasure routes for an item, shaped like treasure rows so one table can hold both. */
export function buildItemDirectRows(type: RewardType, name: string): ItemSourceRow[] {
  const rows: ItemSourceRow[] = [];
  for (const source of directSourcesForReward(type, name)) {
    const info = MECHANISMS[source.mechanism];
    const label = tidyLabel(sourceLabel(source.source));
    const node = SOURCE_NODES[source.source];
    const facility = typeof node?.conditions?.facility === "string" ? node.conditions.facility : undefined;
    const priceMin = Number(node?.conditions?.priceMin ?? source.conditions?.priceMin);
    const priceMax = Number(node?.conditions?.priceMax ?? source.conditions?.priceMax);

    // A crafting route is answered by the facility that owns the recipe, not by "Crafting".
    const family = source.mechanism === "craft-group-facility" && facility
      ? facility
      : DIRECT_FAMILY[source.mechanism] ?? label;
    const requirement = (() => {
      if (source.input) return `Trade ${source.input}`;
      if (source.mechanism === "material-shop") {
        return Number.isFinite(priceMin) && Number.isFinite(priceMax) ? `Price ${priceMin}–${priceMax}` : "";
      }
      if (source.mechanism === "gacha-pool" || source.mechanism === "equipment-exchange") return label;
      return "";
    })();

    rows.push({
      key: `direct|${source.source}|${source.mechanism}|${source.input ?? ""}`,
      direct: true,
      family,
      requirement,
      sourceId: source.source,
      kind: sourceKind(source.source),
      mechanism: source.mechanism,
      grade: info?.grade,
      grades: info ? [info.grade] : [],
      treasureName: source.mechanism === "material-shop" ? "Purchase" : DIRECT_FAMILY[source.mechanism] ?? label,
      treasureIcon: "",
      treasureNote: "",
      note: "",
      quantity: source.quantity !== undefined ? String(source.quantity) : "—",
      treasureRate: { text: "—", detail: "", established: false },
      itemRate: { text: "—", detail: "", established: false },
      overall: source.mechanism === "gacha-pool"
        ? { text: "Unknown", detail: "", established: false }
        : source.mechanism === "material-shop"
          ? { text: "Guaranteed", detail: "", established: true }
          : { text: "—", detail: "", established: false },
      variantIds: [],
      edges: [],
      conditions: { ...(node?.conditions ?? {}), ...(source.conditions ?? {}) },
      derivation: info ? `${info.name} — ${info.grade} (${info.basis})` : source.mechanism,
    });
  }
  return rows;
}

/**
 * Requirement column text for one route, plus a precise key used only for grouping.
 *
 * The displayed text is deliberately short ("Rank E", "Level 20"), but rows may only be merged
 * when they are the same event, so the key still distinguishes the day, the area and each survey.
 */
function requirementInfo(kind: string, conditions: Record<string, unknown> | undefined, sourceId: string, sourceKey: string): { display: string; key: string } {
  const values = conditions ?? {};
  const number = (key: string) => {
    const value = Number(values[key]);
    return Number.isFinite(value) && values[key] !== undefined && values[key] !== null ? value : undefined;
  };
  switch (kind) {
    case "daily-rank": {
      const rank = dailyRankLabel(values.sourceRowId);
      return {
        display: rank ? `Rank ${rank.rankLabel}` : "",
        key: rank ? `${rank.day}·${rank.rankLabel}` : sourceKey,
      };
    }
    case "terrain":
    case "terrain-reference":
      return { display: "", key: sourceKey };
    case "monster": {
      const min = number("minLevel");
      const max = number("maxLevel");
      return { display: min !== undefined && max !== undefined ? `Level ${min}–${max}` : "", key: sourceKey };
    }
    case "survey": {
      const level = number("minAreaLevel");
      return { display: level !== undefined ? `Area level ${level}+` : "", key: sourceKey };
    }
    case "area": {
      const level = number("level");
      return { display: level !== undefined ? `Level ${level}` : "", key: sourceKey };
    }
    case "normal-cave":
    case "normal-cave-end-chest": {
      // Level-focused wording: the area number lives in the expandable detail.
      const detail = sourceFamilyAndDetail(sourceId).detail;
      const level = /Level (\d+)/.exec(detail)?.[1];
      const chest = kind === "normal-cave-end-chest" ? " · completion chest" : "";
      return { display: level ? `Level ${level}${chest}` : detail + chest, key: sourceKey };
    }
    case "legendary-cave": return { display: "Completed floor", key: sourceKey };
    case "legendary-cave-end-chest": return { display: "End of the floor", key: sourceKey };
    case "wairo-dungeon": {
      // The recovered label is the event trigger ("… appeared!"); the practical qualifier is the
      // difficulty and level the encounter requires. Trigger text stays in the evidence detail.
      const difficulty = typeof values.difficulty === "string" ? values.difficulty : "";
      const level = number("level");
      const parts = [difficulty, level !== undefined ? `Level ${level}` : ""].filter(Boolean);
      return { display: parts.length ? parts.join(" · ") : sourceFamilyAndDetail(sourceId).detail, key: sourceKey };
    }
    case "arena": return { display: "Ranked PvP pool", key: sourceKey };
    default: return { display: sourceFamilyAndDetail(sourceId).detail, key: sourceKey };
  }
}

const distinctSorted = <T,>(values: T[]) => [...new Set(values)];

/**
 * One row per player-facing acquisition route for an item, merged across treasure profiles when
 * they answer the same question (the three Legendary Cave Holy-Herb Gold variants become one row).
 *
 * Probabilities keep their stages: the treasure rate, the item rate inside the treasure, and the
 * overall item chance — the last one only where the stages can be multiplied, and always labelled
 * with its denominator. Nothing here changes graph data.
 */
export function buildItemSourceRows(type: RewardType, name: string): ItemSourceRow[] {
  type Contribution = { edge: ProfileEdge; lane: Lane; summary: string; derived?: Probability };
  type Accumulator = {
    family: string;
    requirement: string;
    sourceIds: string[];
    kind: string;
    mechanism: string;
    contributions: Contribution[];
    treasureNames: string[];
    treasureIcon: string;
    variantIds: number[];
    grades: EvidenceGrade[];
  };

  const buckets = new Map<string, Accumulator>();
  for (const { profile, lanes } of profilesForReward(type, name)) {
    for (const group of groupProfileSources(profile)) {
      // One bucket per *event*: the displayed requirement may be short ("Rank E"), but two rows
      // may only be merged when they are the same route, so the key keeps the day and the node.
      for (const variant of group.variants) {
        const info = requirementInfo(group.kind, variant.conditions, group.sourceId, variant.sourceKey);
        const bucketKey = `${group.label}|${group.mechanism}|${info.key}`;
        let bucket = buckets.get(bucketKey);
        if (!bucket) {
          bucket = {
            family: sourceFamilyAndDetail(group.sourceId).family,
            requirement: info.display,
            sourceIds: [],
            kind: group.kind,
            mechanism: group.mechanism,
            contributions: [],
            treasureNames: [],
            treasureIcon: profile.icon,
            variantIds: [],
            grades: [],
          };
          buckets.set(bucketKey, bucket);
        }
        const nodeId = variant.edges[0]?.source ?? group.sourceId;
        if (!bucket.sourceIds.includes(nodeId)) bucket.sourceIds.push(nodeId);
        for (const lane of lanes) {
          for (const edge of variant.edges) {
            if (bucket.contributions.some((entry) => entry.edge.key === edge.key)) continue;
            const member = profile.variants.find((entry) => entry.id === edge.treasure);
            bucket.contributions.push({
              edge,
              lane,
              summary: variant.summary,
              derived: profile.derived.find((row) => row.key === edge.key && row.lane === lane.lane)?.p,
            });
            if (member && !bucket.treasureNames.includes(member.name)) bucket.treasureNames.push(member.name);
            if (!bucket.variantIds.includes(edge.treasure)) bucket.variantIds.push(edge.treasure);
            const grade = edgeGrade(edge);
            if (grade && !bucket.grades.includes(grade)) bucket.grades.push(grade);
          }
        }
      }
    }
  }

  const rows: ItemSourceRow[] = [];
  for (const [key, bucket] of buckets) {
    const contributions = bucket.contributions.sort((a, b) => a.edge.treasure - b.edge.treasure);
    const probabilities = contributions.map((entry) => numericProbability(entry.edge.p));
    const rates = distinctSorted(contributions.map((entry) => entry.lane.rate / 100));
    const templateScoped = TEMPLATE_SCOPED.has(bucket.mechanism);
    const templateTotal = templateScoped
      ? Math.max(...contributions.map((entry) => templateCountFor(entry.edge.source)), 1)
      : 1;
    const templateIds = distinctSorted(
      contributions.map((entry) => entry.edge.conditions?.dungeonTemplateId).filter((id): id is number => typeof id === "number"),
    );
    const quantity = {
      min: Math.min(...contributions.map((entry) => entry.lane.min)),
      max: Math.max(...contributions.map((entry) => entry.lane.max)),
    };
    // Terrain has no single level requirement; list the box eligibility ranges instead.
    const requirement = bucket.requirement
      || (bucket.kind === "terrain" || bucket.kind === "terrain-reference"
        ? distinctSorted(contributions.map((entry) => entry.summary).filter(Boolean)).join(" · ") || "Any area level"
        : "");

    const treasureRate: ItemSourceNumber = (() => {
      const formulas = distinctSorted(
        contributions.map((entry) => formulaProbability(entry.edge.p)).filter((value): value is FormulaProbability => Boolean(value)),
      );
      if (formulas.length) {
        return {
          text: "Varies",
          detail: formulas[0].note ?? formulas[0].formula,
          established: false,
        };
      }
      if (probabilities.every((p) => p === undefined)) {
        return { text: "Not established", detail: "no selection mechanism recovered for this source", established: false };
      }
      if (probabilities.some((p) => p === undefined)) {
        return { text: "Varies", detail: "more than one selection mechanism feeds this route", established: false };
      }
      const values = distinctSorted(probabilities as number[]);
      const detail = templateScoped
        ? "gold chest slot"
        : bucket.kind === "normal-cave" || bucket.kind === "normal-cave-end-chest"
          ? "per chest found"
          : "";
      return {
        text: values.length === 1 ? formatPercent(values[0]) : `Varies (${values.map((value) => formatPercent(value)).join(" – ")})`,
        detail,
        established: true,
      };
    })();

    const itemRate: ItemSourceNumber = {
      text: rates.length === 1 ? formatPercent(rates[0]) : `Varies (${rates.map((rate) => formatPercent(rate)).join(" – ")})`,
      detail: "",
      established: true,
    };

    const overall: ItemSourceNumber = (() => {
      if (!treasureRate.established) {
        return { text: "Unknown", detail: "not a fixed number here", established: false };
      }
      const parts = contributions.map((entry) => {
        const p = numericProbability(entry.edge.p);
        return p === undefined ? 0 : p * (entry.lane.rate / 100);
      });
      const sum = parts.reduce((total, value) => total + value, 0);
      const value = sum / templateTotal;
      return { text: formatPercent(value, 3), detail: MECHANISM_OUTCOME[bucket.mechanism] ?? "per source event", established: true };
    })();

    const primary = contributions[0];
    const primaryP = numericProbability(primary?.edge.p);
    const derivation = [
      templateScoped
        ? `${templateIds.length} qualifying floor setup${templateIds.length === 1 ? "" : "s"} × ${primaryP !== undefined ? formatPercent(primaryP) : "?"} × ${formatPercent(rates[0])} ÷ ${templateTotal} floor setups`
        : contributions.length > 1
          ? `${contributions.length} mutually exclusive treasure rows, each ${primaryP !== undefined ? formatPercent(primaryP) : "?"} × ${formatPercent(rates[0])}, summed`
          : `${primaryP !== undefined ? formatPercent(primaryP) : "?"} chance to get the treasure × ${formatPercent(rates[0])} chance for this item inside it`,
      overall.established ? `= ${overall.text} ${overall.detail}` : "",
      contributions.some((entry) => entry.derived) ? "the evidence layer already derives this edge's item-from-source value" : "",
    ].filter(Boolean).join(" ");

    rows.push({
      key,
      family: bucket.family,
      requirement,
      sourceId: bucket.sourceIds[0] ?? "",
      kind: bucket.kind,
      mechanism: bucket.mechanism,
      grade: bucket.grades[0],
      grades: bucket.grades,
      treasureName: bucket.treasureNames[0] ?? "",
      treasureIcon: bucket.treasureIcon,
      treasureNote: templateScoped && templateIds.length > 0
        ? `${templateIds.length} of ${templateTotal} ${bucket.treasureNames[0] ?? "treasure"} variants`
        : bucket.variantIds.length > 1
          ? `${bucket.variantIds.length} treasure variants`
          : "",
      note: templateScoped && templateIds.length > 0
        ? `${templateIds.length} of ${templateTotal} ${bucket.family} floor setups carry a ${bucket.treasureNames[0] ?? "treasure"} that contains ${name}`
        : "",
      quantity: formatQuantity(quantity.min, quantity.max),
      treasureRate,
      itemRate,
      overall,
      variantIds: bucket.variantIds,
      edges: contributions.map((entry) => entry.edge),
      derivation,
    });
  }

  const rank = (row: ItemSourceRow) => GRADE_ORDER[gradeTone(row.grade)];
  return rows.sort((a, b) => rank(a) - rank(b) || a.family.localeCompare(b.family) || a.requirement.localeCompare(b.requirement));
}

/** Lanes granting this item inside a profile that has no established source at all. */
export function unresolvedProfilesForReward(type: RewardType, name: string): { profile: Profile; lanes: Lane[] }[] {
  return profilesForReward(type, name).filter((entry) => entry.profile.edges.length === 0);
}

/**
 * The daily-rank schedule (day + rank per reward row) lives in its own recovered module, which
 * the graph references explicitly: its edge conditions only carry `sourceRowId`.
 */
export const DAILY_RANK_BY_ROW: Map<number, { day: string; rankLabel: string }> = new Map(
  DAILY_RANK_REWARDS.flatMap((day) => day.rewards.map((tier) => [tier.sourceRowId, { day: day.day, rankLabel: tier.rankLabel }] as const)),
);

export function dailyRankLabel(sourceRowId: unknown): { day: string; rankLabel: string } | undefined {
  return typeof sourceRowId === "number" ? DAILY_RANK_BY_ROW.get(sourceRowId) : undefined;
}

/** Profile-level summary used by the browse view. */
export function profileSourceLabels(profile: Profile): string[] {
  return [...new Set(profile.routes.map((route) => sourceLabel(route.source)))];
}

export function profileSourceKinds(profile: Profile): string[] {
  return [...new Set(profile.routes.map((route) => sourceKind(route.source)))];
}
