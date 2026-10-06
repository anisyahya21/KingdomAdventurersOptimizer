import { NATIVE_AREA_LEVELS } from "./monster-truth";
import { getItemIcon } from "./equipment-icons";
import { TREASURE_BOXES, TREASURE_MONSTERS } from "./treasure-lookup";

export type LootTerrain = "Ground/dirt" | "Grass" | "Sand" | "Rock" | "Snow" | "Swamp" | "Volcano";
export type LootItem = { id: number; name: string; icon?: string };
type LootSlot = { itemId: number; itemName: string; rate: number; min: number; max: number };
type Treasure = { id: number; minLevel: number; maxLevel: number; slots: LootSlot[] };
export type LootMonster = { id: number; name: string; minLevel: number; maxLevel: number; treasureChance: number; itemChance: number; expectedQuantity: number };
export type LootResult = {
  terrain: LootTerrain;
  level: number;
  monsters: LootMonster[];
  droppers: LootMonster[];
  averageChance: number;
  averageQuantity: number;
  minChance: number;
  maxChance: number;
};

const terrainByCode: Record<number, LootTerrain> = { 1: "Ground/dirt", 2: "Grass", 3: "Sand", 4: "Rock", 5: "Volcano", 6: "Snow", 7: "Swamp" };
const treasures = new Map<number, Treasure>();
for (const box of TREASURE_BOXES) {
  treasures.set(box.id, {...box,slots:box.rewards.filter(r=>r.type==="item").map(r=>({itemId:r.id,itemName:r.name,rate:r.rate/100,min:r.min,max:r.max}))});
}

const itemSet = new Map<number, LootItem>();
for (const treasure of treasures.values()) for (const slot of treasure.slots) itemSet.set(slot.itemId, { id: slot.itemId, name: slot.itemName, icon: getItemIcon(slot.itemName) });
export const LOOT_ITEMS = [...itemSet.values()].sort((a, b) => a.name.localeCompare(b.name));

const rawMonsters = TREASURE_MONSTERS.flatMap((row) => {
  const terrain=terrainByCode[row.terrain];
  if (!terrain || row.type!==0) return [];
  return [{...row,terrain,dropDataId:row.dropType===2 ? row.treasureId : -1,treasureChance:row.dropRate/100}];
});

export const LOOT_TERRAINS: LootTerrain[] = ["Ground/dirt", "Grass", "Sand", "Rock", "Snow", "Swamp", "Volcano"];
export const LOOT_AREA_LEVELS = Array.from(new Set(Object.values(NATIVE_AREA_LEVELS).flat())).sort((a, b) => a - b);
export function nativeLootTerrainsAtLevel(level: number) {
  return LOOT_TERRAINS.filter((terrain) => terrain === "Ground/dirt" ? (NATIVE_AREA_LEVELS.Ground ?? []).includes(level) : (NATIVE_AREA_LEVELS[terrain] ?? []).includes(level));
}

function slotsForItem(treasure: Treasure | undefined, itemId: number) {
  // Direct dropDataId lookup does not use the gathering selector's level bounds.
  return treasure?.slots.filter((slot) => slot.itemId === itemId) ?? [];
}

export function getLootResult(terrain: LootTerrain, level: number, itemId: number): LootResult {
  const monsters = rawMonsters.filter((monster) => monster.terrain === terrain && monster.minLevel <= level && monster.maxLevel >= level).map((monster) => {
    const slots = slotsForItem(treasures.get(monster.dropDataId), itemId);
    const itemChance = monster.treasureChance * (1-slots.reduce((miss,slot)=>miss*(1-slot.rate*(slot.min>0 ? 1 : slot.max/(slot.max-slot.min+1))),1));
    const expectedQuantity = monster.treasureChance * slots.reduce((sum,slot)=>sum+slot.rate*(slot.min+slot.max)/2,0);
    return { id: monster.id, name: monster.name, minLevel: monster.minLevel, maxLevel: monster.maxLevel, treasureChance: monster.treasureChance, itemChance, expectedQuantity };
  });
  const droppers = monsters.filter((monster) => monster.itemChance > 0);
  const chances = monsters.map((monster) => monster.itemChance * 100);
  return { terrain, level, monsters, droppers, averageChance: monsters.length ? chances.reduce((sum, value) => sum + value, 0) / monsters.length : 0, averageQuantity: monsters.length ? monsters.reduce((sum, monster) => sum + monster.expectedQuantity, 0) / monsters.length : 0, minChance: chances.length ? Math.min(...chances) : 0, maxChance: chances.length ? Math.max(...chances) : 0 };
}
