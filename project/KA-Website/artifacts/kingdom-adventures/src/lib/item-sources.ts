/**
 * SUPERSEDED FOR DISPLAY — do not use this module to render acquisition data.
 *
 * Item & Skill Sources now renders from the generated treasure-source projection:
 *   src/game-data/treasure-source-profiles.json + src/lib/treasure-source-graph.ts
 * whose data owner is RE-evidence/20260917-treasure-source-graph/.
 *
 * This file is kept because the generated graph cites it as the evidence reference for the
 * inferred joins it contributed (Item.csv flag 128 shop, flag 1024 restaurant, craftGroup 70
 * orchard, craftGroup → FacilityData recipes) in its `mechanisms` table. Removing it would leave
 * those references dangling, so treat it as the historical evidence source for those joins
 * rather than as a live mapping. Nothing here should be re-exported to a page again.
 */
import itemCsv from "../../../../data/Sheet csv/KA GameData - Item.csv?raw";
import areaCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Area_lookup.csv?raw";
import surveyCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Survey.csv?raw";
import caves from "@/game-data/native-caves.json";
import gachaSources from "@/game-data/native-gacha-sources.json";
import facilityCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Facility_lookup.csv?raw";
import { DAILY_RANK_REWARDS } from "@/game-data/daily-rank-rewards";
import { getSkillCrafting } from "@/lib/skill-crafting";
import { localSharedData } from "@/lib/local-shared-data";
import { parseCsv } from "@/lib/monster-truth";
import { gatheringTerrains, TREASURE_TERRAIN_NAMES, TREASURE_BOXES, TREASURE_MONSTERS, TREASURE_SPECIAL_BOSSES, specialBossDay } from "@/lib/treasure-lookup";

export type SourceKind = "Monster" | "Terrain" | "Area reward" | "Survey" | "Dungeon" | "Gacha" | "Arena" | "Kairo Room" | "Wairo Dungeon" | "Daily Rank Reward" | "Item Shop" | "Restaurant" | "Orchard" | "Skill Shop" | "Skill crafting" | "Crafting" | "Treasure table";
export type SourceTargetType = "item" | "skill" | "equipment" | "furniture" | "job" | "valuable";
export type SourceTarget = { name: string; type: SourceTargetType; sources: ItemSource[] };
export type ItemSource = { kind: SourceKind; title: string; details: string; confidence: "confirmed" | "derived"; key: string; treasureId?: number; monsterName?: string; minLevel?: number; maxLevel?: number; rate?: number; quantity?: string; location?: string; terrainType?: number; boxRate?: number; boxDetails?: string; day?: string; difficulty?: string; rankLabel?: string; oneTime?: boolean; sourceImage?: string; href?: string };

const clean = (value: unknown) => String(value ?? "").trim();
const number = (value: unknown, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };
const norm = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const headers = (rows: string[][], preferred = 0) => rows[preferred]?.length ? rows[preferred].map(clean) : [];
const index = (header: string[], name: string) => header.findIndex((v) => v.toLowerCase() === name.toLowerCase());
const val = (row: string[], header: string[], name: string) => clean(row[index(header, name)]);
const range = (min: string, max: string) => min && max && min !== max ? `${min}-${max}` : (min || max || "");

const targets = new Map<string, SourceTarget>();
function add(name: string, type: SourceTargetType, source: Omit<ItemSource, "key">) {
  const display = name.replace(/^\d+\s*x\s*/i, "").trim();
  if (!display || display === "-1" || display === "0" || display.toLowerCase() === "n/a") return;
  const key = norm(display);
  const target = targets.get(key) ?? { name: display, type, sources: [] };
  if (!targets.has(key)) targets.set(key, target);
  const full = { ...source, key: `${source.kind}|${source.title}|${source.treasureId ?? ""}|${source.rate ?? ""}|${source.quantity ?? ""}|${source.minLevel ?? ""}|${source.maxLevel ?? ""}|${source.details}` };
  if (!target.sources.some((entry) => entry.key === full.key)) target.sources.push(full);
}

type Reward = { name: string; type: SourceTargetType; rate: string; quantity: string };
type Treasure = { id: number; group: number; name: string; source: string; minLevel: string; maxLevel: string; rewards: Reward[] };
const treasures = new Map<number, Treasure>();
for (const box of TREASURE_BOXES) {
  treasures.set(box.id, {id:box.id,group:box.group,name:box.name,source:"",minLevel:String(box.minLevel),maxLevel:String(box.maxLevel),rewards:box.rewards.map(r=>({name:r.name,type:r.type as SourceTargetType,rate:`${r.rate}%`,quantity:range(String(r.min),String(r.max))}))});
}

function addTreasureRewards(treasure: Treasure, kind: SourceKind, title: string, details: string, confidence: ItemSource["confidence"] = "confirmed", extra: Partial<ItemSource> = {}) {
  for (const reward of treasure.rewards) if (parseFloat(reward.rate) > 0) add(reward.name, reward.type, { kind, title, details, confidence, treasureId: treasure.id, rate: parseFloat(reward.rate), quantity: reward.quantity, ...extra });
}
for (const treasure of treasures.values()) {
  addTreasureRewards(treasure, "Treasure table", treasure.name, "Contents recorded in the original game table. This entry alone does not establish where the box is obtained.", "derived");
  const types = new Set(gatheringTerrains(treasure.group).map(t => t.type));
  for (const type of types) for (const reward of treasure.rewards) if (parseFloat(reward.rate)>0) add(reward.name,reward.type,{
    kind:"Terrain",terrainType:type,title:`Gather ${TREASURE_TERRAIN_NAMES[type] ?? `terrain ${type}`}`,location:TREASURE_TERRAIN_NAMES[type] ?? `Terrain ${type}`,
    details:"Complete gathering on a matching terrain tile → find a box → carry it for analysis. The box is chosen from all eligible rows in the terrain's treasure group. Nature on the tile can change which terrain record is used; a map biome alone is not enough to predict each dig.",
    confidence:"derived",treasureId:treasure.id,minLevel:number(treasure.minLevel),maxLevel:number(treasure.maxLevel),rate:parseFloat(reward.rate),quantity:reward.quantity,
  });
}

for (const row of TREASURE_MONSTERS) {
  if (row.type !== 0 || row.dropType !== 2) continue;
  const treasure = treasures.get(row.treasureId); if (!treasure) continue;
  const monster = row.name; const dropRate=row.dropRate;
  for (const reward of treasure.rewards) if (dropRate>0 && parseFloat(reward.rate)>0) add(reward.name, reward.type, { boxRate:dropRate, boxDetails:`${dropRate}% base chance per ordinary monster defeat to receive this box. Encounter frequency and skill effects are separate.`, kind: "Monster", title: `Defeat ${monster}`, monsterName:monster, location:TREASURE_TERRAIN_NAMES[row.terrain] || "Special encounter", minLevel:row.minLevel,maxLevel:row.maxLevel,treasureId:treasure.id,rate:parseFloat(reward.rate),quantity:reward.quantity,details: `${dropRate}% chance for this monster to drop the box. After analysis: ${(dropRate*parseFloat(reward.rate)/100).toLocaleString(undefined,{maximumFractionDigits:4})}% base chance per defeat for this reward slot. Monster encounter frequency and skill effects are not included.`, confidence: "derived" });
}

function addDirectTreasure(raw: string, headerRow: number, idField: string, kind: SourceKind, title: (row: string[], header: string[]) => string, details: (row: string[], header: string[]) => string) {
  const rows = parseCsv(raw); const header = headers(rows, headerRow); const idIndex = index(header, idField); if (idIndex < 0) return;
  for (const row of rows.slice(headerRow + 1)) { const treasure = treasures.get(number(row[idIndex], -1)); if (treasure) addTreasureRewards(treasure, kind, title(row, header), details(row, header)); }
}
addDirectTreasure(areaCsv, 0, "treasureId", "Area reward", (r,h) => `Clear area #${val(r,h,"id")} · Level ${val(r,h,"level")}`, (r,h) => `Defeat this area’s boss to clear the fog, then send a unit to claim the chest. One chest per area, claimed once. Terrain: ${TREASURE_TERRAIN_NAMES[number(val(r,h,"terrain"))] ?? "unmapped"}.`);
addDirectTreasure(surveyCsv, 0, "rewardTreasureId", "Survey", (r,h) => val(r,h,"nameText").replace("<0>",val(r,h,"nameArg")) || `Survey #${val(r,h,"id")}`, (r,h) => `${TREASURE_TERRAIN_NAMES[number(val(r,h,"terrain"))] ?? "Unmapped terrain"}, area level ${val(r,h,"minAreaLevel")}+. Survey #${val(r,h,"id")}; success check, costs, time and reward limits are separate from the contents roll.`);
for (const dungeon of caves.dungeons) {
  const endless=(dungeon.flag & 1)!==0;
  const locations=endless ? [{id:-1,level:0}] : caves.areas.filter(area=>area.dungeonId===dungeon.id);
  for (const area of locations) {
    const place=endless ? "Legendary Cave" : `Normal cave · Area #${area.id} · Level ${area.level}`;
    const extra={location:place, sourceImage:endless ? "/website_icons/facilities_confirmed/facility_196_legendary_cave.png" : undefined};
    for (const [id,check] of dungeon.treasureTable[0]) {
      const treasure=treasures.get(id);
      if (!treasure || check<=0) continue;
      // Event reward definitions in endless-cave tables have disputed runtime semantics.
      // Keep raw data intact, but withhold these acquisition claims pending recovery.
      if (endless && treasure.group !== 0) continue;
      addTreasureRewards(treasure,"Dungeon",`${place} · Exploration chest`,
        endless ? "One exploration chest per floor, selected from the randomly chosen floor configuration. This box is one possible result. Selection uses ordered checks; the contents percentage is not a per-floor chance. Collected boxes retain their treasure ID when released outside the cave for analysis."
        : `Explore the cave in this area. Its configuration places ${range(String(dungeon.minChests),String(dungeon.maxChests))} exploration chests, selected using ordered checks. This box is one possible result; its contents percentage is not its appearance chance. The white completion chest is separate.`,
        "derived",extra);
    }
    const completion=treasures.get(dungeon.rewardTreasure);
    if (completion) addTreasureRewards(completion,"Dungeon",`${place} · White completion chest`,
      endless ? "Reach the end of a floor to collect its white completion chest, then continue to the next floor. This chest is separate from the floor's exploration chest. White is the appearance; this entry lists the actual contents."
      : "Finish this area's single-floor cave to collect its white completion chest. Exploration chests are separate. White is the appearance; different caves can use different reward entries.","derived",extra);
  }
}

for (const boss of TREASURE_SPECIAL_BOSSES) {
  const pool = [...treasures.values()].filter(t => t.group === boss.rewardGroup);
  const day = specialBossDay(boss);
  const kind: SourceKind = boss.rewardGroup >= 400 && boss.rewardGroup < 500 ? "Wairo Dungeon" : "Kairo Room";
  for (const treasure of pool) addTreasureRewards(treasure, kind, boss.title,
    `Win this encounter to receive the rival leader's reward box. One box is selected from ${pool.length} treasure rows in its reward group. Each row has one selection entry; treasure level bounds are not applied here. The box is placed into the world after victory, then carried for analysis.`,
    "derived", {boxRate:100/pool.length, day, difficulty:["Easy","Normal","Hard","Extreme"][boss.difficulty], monsterName:TREASURE_MONSTERS.find(m=>m.id===boss.boss)?.name,
      location:`${kind}${day ? ` · ${day}` : ""} · Encounter level ${boss.level}`,
      href:kind === "Kairo Room" && day ? `/kairo-room#kairo-room-${day.toLowerCase()}` : undefined});
}

// LotPvPReward filters FLAG_PVP_REWARD=8 and the calculated reward tier.
// The fallback pool also requires flag8. A generic Battle Bonus name is insufficient.
for (const box of TREASURE_BOXES.filter(box=>(box.flag & 8)!==0)) {
  const treasure=treasures.get(box.id);
  if (treasure) addTreasureRewards(treasure,"Arena","Arena battle reward",
    "Possible Arena (PvP) reward. The game selects one reward entry from a pool determined by battle-rank conditions, then rolls that entry’s contents. The contents percentage is not the chance per battle. This is separate from the daily rank bonus.",
    "derived",{location:"Arena (PvP)"});
}

for (const reward of gachaSources) add(reward.name,reward.type as SourceTargetType,{
  kind:"Gacha",title:reward.title,confidence:"derived",quantity:String(reward.quantity),
  details:reward.availability==="normal"
    ? `Included in the normal ${reward.title.toLowerCase()} pool. Receive ${reward.quantity} when selected. Active events can change the pool and probabilities; check the in-game probability list.`
    : `Available during matching ${reward.rank===6 ? "S-rank " : ""}${reward.title.toLowerCase()} events in the recovered schedule. Receive ${reward.quantity} when selected. Availability depends on the active event; check the in-game probability list.`,
});

const items = parseCsv(itemCsv); const itemHeader = headers(items, 1); const flags = index(itemHeader, "flag"); const craftGroup = index(itemHeader, "craftGroup");
const facilities = parseCsv(facilityCsv); const facilityHeader = headers(facilities); const facilityCraft = index(facilityHeader, "craftGroup"); const facilityName = index(facilityHeader, "name"); const facilityByGroup = new Map<string, string[]>();
if (facilityCraft >= 0 && facilityName >= 0) for (const row of facilities.slice(1)) { const group = clean(row[facilityCraft]); if (group !== "-1" && group) facilityByGroup.set(group, [...(facilityByGroup.get(group) || []), clean(row[facilityName])]); }
for (const row of items.slice(2)) { const name = val(row, itemHeader, "name"); if (!name) continue; const flag = flags >= 0 ? number(row[flags]) : 0; const group = craftGroup >= 0 ? clean(row[craftGroup]) : "-1"; if (flag & 128) add(name, "item", { kind: "Item Shop", title: "Item Shop", details: "Available through the item shop data flag", confidence: "confirmed" }); if (flag & 1024) add(name, "item", { kind: "Restaurant", title: "Restaurant", details: "Available through the restaurant data flag", confidence: "confirmed" }); if (group === "70") add(name, "item", { kind: "Orchard", title: "Orchard", details: "Produced by orchard craft group 70", confidence: "confirmed" }); else if (group !== "-1" && group !== "") for (const facility of new Set(facilityByGroup.get(group) || [])) add(name, "item", { kind: "Crafting", title: facility, details: `Item.csv craft group ${group}`, confidence: "derived" }); }

const skills = (localSharedData.skills || {}) as Record<string, { name?: string; buyPrice?: number; studioLevel?: number | null; craftingIntelligence?: number | null }>;
for (const skill of Object.values(skills)) {
  if (!skill.name) continue;
  const crafting=getSkillCrafting(skill.name);
  if (crafting) add(skill.name,"skill", {kind:"Skill crafting",title:"Skill Shop",details:`Craft at shop level ${crafting.studioLevel} with ${crafting.intelligence} intelligence.`,confidence:"confirmed",href:"/skills"});
}

// Shared daily-rank mapping supplies the actual treasure row, day, and rank.
// Index native contents, rather than duplicating the summary's reward names.
for (const day of DAILY_RANK_REWARDS) for (const tier of day.rewards) {
  const treasure=treasures.get(tier.sourceRowId);
  if (treasure) addTreasureRewards(treasure,"Daily Rank Reward",`${day.day} · Rank ${tier.rankLabel}`,
    "Daily rank bonus for this day and achieved rank. Presented as a rank reward rather than a chest to collect.","confirmed",
    {day:day.day,rankLabel:tier.rankLabel,href:"/daily-rank-rewards"});
}


export const ITEM_SOURCE_TARGETS = [...targets.values()].map((target) => ({ ...target, sources: target.sources.sort((a,b) => a.kind.localeCompare(b.kind) || a.title.localeCompare(b.title)) })).sort((a,b) => a.name.localeCompare(b.name));
export function searchItemSources(query: string) { const q = norm(query); if (!q) return ITEM_SOURCE_TARGETS.slice(0, 30); return ITEM_SOURCE_TARGETS.filter((target) => norm(target.name).includes(q)).slice(0, 50); }
export function getItemSource(name: string) { return targets.get(norm(name)); }
export const ITEM_SOURCE_NOTES = ["Ordinary monster entries use type 0 and a direct treasure reference; other reward callers have separate rules.", "Monster base chances combine the box-drop percentage and contents rolls; encounter weighting and skill effects are excluded.", "Terrain selection uses a check out of 1,000 followed by group and area-level filtering. Exact tile and nature state matter.", "Other table links and the contents catalog do not establish every acquisition condition. Gacha membership is not included."];

// The reverse index uses the same acquisition joins as item search. Reward rolls
// are deliberately removed: they belong to the item → box relationship.
export type TreasureSource = Omit<ItemSource, "rate" | "quantity">;
const boxSources = new Map<number, Map<string,TreasureSource>>();
for (const target of ITEM_SOURCE_TARGETS) for (const source of target.sources) {
  if (source.treasureId === undefined || source.kind === "Treasure table") continue;
  const {rate, quantity, ...origin} = source;
  origin.details = source.boxDetails ?? source.details;
  origin.key = [origin.kind,origin.title,origin.treasureId,origin.minLevel,origin.maxLevel,origin.terrainType,origin.details].join("|");
  const sources = boxSources.get(source.treasureId) ?? new Map<string,TreasureSource>();
  sources.set(origin.key,origin); boxSources.set(source.treasureId,sources);
}
export function getTreasureSources(id:number): TreasureSource[] { return [...(boxSources.get(id)?.values() ?? [])]; }
export function treasureSourceLabel(id:number, nativeName:string) {
  if (nativeName !== "Battle Bonus") return nativeName;
  const sources=getTreasureSources(id);
  if (sources.some(s=>s.kind === "Arena")) return "Arena battle reward";
  if (sources.some(s=>s.kind === "Kairo Room")) return "Kairo Room reward box";
  if (sources.some(s=>s.kind === "Wairo Dungeon")) return "Wairo Dungeon reward box";
  return nativeName;
}
export function searchTreasureBoxes(query:string) {
  const words=norm(query).split(" ").filter(Boolean);
  return TREASURE_BOXES.filter(box=>{
    const sources=getTreasureSources(box.id);
    const haystack=norm([box.name,treasureSourceLabel(box.id,box.name),box.id,box.group,...box.rewards.map(r=>r.name),...sources.flatMap(s=>[s.title,s.day,s.difficulty,s.location])].join(" "));
    return words.every(word=>haystack.includes(word));
  });
}

export function getDailyRankSource(id:number) { return getTreasureSources(id).find(s=>s.kind==="Daily Rank Reward"); }
export const DAILY_RANK_SOURCE_IMAGE = "/website_icons/facilities_confirmed/facility_172_ranking_board.png";
