// -- Data (from KA GameData - House.csv) --------------------------------------
//
// CSV COLUMN -> FIELD NAME -> ACTUAL MATERIAL -> matId -> icon
// -----------------------------------------------------------------------------
// The House.csv stores build costs in 5 numeric columns. The column order does
// NOT match the material names you'd expect. Verified against in-game values:
//
//   CSV col 0  ->  field "grass"   ->  Grass        (matId 0)  flat green
//   CSV col 1  ->  field "wood"    ->  Wood         (matId 1)  flat brown log
//   CSV col 2  ->  field "food"    ->  Food         (matId 2)  flat basket
//   CSV col 3  ->  field "ore"     ->  Ore          (matId 3)  crystal blue-grey
//   CSV col 4  ->  field "mystic"  ->  Mystic Ore   (matId 4)  flat red diamond
//
// Confirmed examples:
//   Inn              -> grass:5        (5 Grass)
//   Furniture Shop   -> wood:15        (15 Wood)
//   Restaurant       -> food:8         (8 Food)
//   Weapon Shop      -> ore:12         (12 Ore)
//   Skill Shop       -> mystic:12      (12 Mystic Ore)
//   Insectarium      -> wood:65        (65 Wood)
//   Zoo              -> food:80        (80 Food)
//   Hospital         -> food:25        (25 Food)
//   Monster House    -> food:15        (15 Food)
//   Orchard          -> grass:25       (25 Grass)
// -----------------------------------------------------------------------------

export type BuildingGroup = "house" | "shop" | "service" | "special";
export type PlotSize = "S" | "M" | "L" | "XL";
export type PlotValues = [number, number, number, number];

export type Building = {
  id: number;
  name: string;
  group: BuildingGroup;
  grass: number;
  wood: number;
  food: number;
  ore: number;
  mystic: number;
  /** Capacity from House.csv. Total beds/resident capacity, including the default bed. */
  cap: PlotValues;
  /** maxExtraBedPlacementNums from House.csv. Additional bed placements beyond the default bed. */
  beds: PlotValues;
  /** maxStoreShelfPlacementNums from House.csv. */
  store: PlotValues;
  /** maxMonsterRoomPlacementNums from House.csv. */
  monster: PlotValues;
};

export const BUILDINGS: Building[] = [
  //                                                        grass  wood   food   ore    mystic
  { id: 0,  name: "Commoner's House", group: "house",   grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 0,  cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [1,1,2,3] },
  { id: 15, name: "Mansion",          group: "house",   grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 20, cap: [3,4,5,6],   beds: [2,3,4,5], store: [0,0,0,0], monster: [1,1,2,3] },
  { id: 1,  name: "Royal Room",       group: "house",   grass: 0,  wood: 3,  food: 0,  ore: 0,  mystic: 0,  cap: [1,1,2,3],   beds: [0,0,1,2], store: [0,0,0,0], monster: [1,1,1,2] },
  { id: 7,  name: "Inn",              group: "house",   grass: 5,  wood: 0,  food: 0,  ore: 0,  mystic: 0,  cap: [4,6,8,10],  beds: [3,5,7,9], store: [0,0,0,0], monster: [0,0,0,1] },
  { id: 16, name: "Monster House",    group: "house",   grass: 0,  wood: 0,  food: 15, ore: 0,  mystic: 0,  cap: [1,1,1,2],   beds: [0,0,0,1], store: [0,0,0,0], monster: [2,3,4,5] },
  { id: 2,  name: "Weapon Shop",      group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 12, mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 3,  name: "Armor Shop",       group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 8,  mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 9,  name: "Accessory Shop",   group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 15, mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 6,  name: "Skill Shop",       group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 12, cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 4,  name: "Item Shop",        group: "shop",    grass: 0,  wood: 0,  food: 5,  ore: 0,  mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 5,  name: "Furniture Shop",   group: "shop",    grass: 0,  wood: 15, food: 0,  ore: 0,  mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 8,  name: "Restaurant",       group: "shop",    grass: 0,  wood: 0,  food: 8,  ore: 0,  mystic: 0,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [3,5,7,9], monster: [0,0,0,0] },
  { id: 21, name: "Insectarium",      group: "shop",    grass: 0,  wood: 65, food: 0,  ore: 0,  mystic: 0,  cap: [1,1,1,2],   beds: [0,0,0,1], store: [3,5,7,9], monster: [0,0,0,1] },
  { id: 22, name: "Aquarium",         group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 50, cap: [1,1,1,2],   beds: [0,0,0,1], store: [3,5,7,9], monster: [0,0,0,1] },
  { id: 23, name: "Zoo",              group: "shop",    grass: 0,  wood: 0,  food: 80, ore: 0,  mystic: 0,  cap: [1,1,1,2],   beds: [0,0,0,1], store: [3,5,7,9], monster: [0,0,0,1] },
  { id: 24, name: "Museum",           group: "shop",    grass: 0,  wood: 0,  food: 0,  ore: 99, mystic: 0,  cap: [1,1,1,2],   beds: [0,0,0,1], store: [3,5,7,9], monster: [0,0,0,1] },
  { id: 13, name: "Hospital",         group: "service", grass: 0,  wood: 0,  food: 25, ore: 0,  mystic: 0,  cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [0,0,0,0] },
  { id: 10, name: "Recovery Room",    group: "service", grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 5,  cap: [1,2,2,3],   beds: [0,1,1,2], store: [0,0,0,0], monster: [1,1,1,2] },
  { id: 14, name: "Church",           group: "service", grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 15, cap: [1,2,2,3],   beds: [0,1,1,2], store: [0,0,0,0], monster: [0,0,0,0] },
  { id: 11, name: "Analysis Lab",     group: "service", grass: 0,  wood: 8,  food: 0,  ore: 0,  mystic: 0,  cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [0,0,0,0] },
  { id: 12, name: "Research Lab",     group: "service", grass: 0,  wood: 10, food: 0,  ore: 0,  mystic: 0,  cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [0,0,0,0] },
  { id: 17, name: "Survey Corps HQ",  group: "special", grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 15, cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [1,1,2,3] },
  { id: 20, name: "Studio",           group: "special", grass: 0,  wood: 15, food: 0,  ore: 0,  mystic: 0,  cap: [2,2,2,3],   beds: [1,1,1,2], store: [0,0,0,0], monster: [1,1,1,2] },
  { id: 18, name: "Orchard",          group: "special", grass: 25, wood: 0,  food: 0,  ore: 0,  mystic: 0,  cap: [1,2,3,4],   beds: [0,1,2,3], store: [0,0,0,0], monster: [0,0,0,1] },
  { id: 19, name: "Santa's House",    group: "special", grass: 0,  wood: 0,  food: 0,  ore: 0,  mystic: 10, cap: [2,3,4,5],   beds: [1,2,3,4], store: [0,0,0,0], monster: [1,1,2,3] },
];

export const BUILDING_BY_NAME: Record<string, Building> = Object.fromEntries(
  BUILDINGS.map((building) => [building.name, building]),
);

export const PLOT_SIZES = ["S", "M", "L", "XL"] as const;

// Tile footprints per plot size (W x H, width=E-W, height=N-S). Plots are longer N-S.
export const PLOT_TILES: Record<PlotSize, string> = { S: "6×6", M: "6×8", L: "8×8", XL: "8×10" };

export const BUILDING_GROUP_LABEL: Record<BuildingGroup, string> = {
  house: "House",
  shop: "Shop",
  service: "Service",
  special: "Special",
};

export function getBuildingByName(name: string): Building | undefined {
  return BUILDING_BY_NAME[name];
}

export function requireBuilding(name: string): Building {
  const building = getBuildingByName(name);
  if (!building) throw new Error(`Unknown building: ${name}`);
  return building;
}
