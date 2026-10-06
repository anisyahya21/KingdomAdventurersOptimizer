import { requireBuilding, type Building } from "@/game-data/buildings";
import type { CollectionCategory } from "@/game-data/collections";

export type ShopBuilding = Building;

export type ShopFacility = {
  id: number;
  name: string;
  size: string;
  upgGrass: number;
  upgWood: number;
  upgFood: number;
  upgOre: number;
  upgMystic: number;
  maxUpgGrass: number;
  maxUpgWood: number;
  maxUpgFood: number;
  maxUpgOre: number;
  maxUpgMystic: number;
};

export type ShopSlug =
  | "items-reference"
  | "weapon-shop"
  | "armor-shop"
  | "accessory-shop"
  | "item-shop"
  | "furniture-shop"
  | "restaurant"
  | "skill-shop"
  | "insectarium"
  | "aquarium"
  | "museum"
  | "zoo"
  | "orchard";

export type ShopCategory = "shop" | "facility";

export type ShopRecord = {
  slug: ShopSlug;
  category: ShopCategory;
  title: string;
  shortTitle: string;
  description: string;
  owner: string;
  collectionCategory?: CollectionCategory;
  currency?: "silver";
  /** Building stats from House.csv — present for all shop-type buildings */
  building?: ShopBuilding;
  /** Primary crafting workbench placed inside this shop */
  workbench?: ShopFacility;
};

export const SHOP_RECORDS: ShopRecord[] = [
  {
    slug: "items-reference",
    category: "shop",
    title: "Items Reference",
    shortTitle: "Items Ref",
    description: "Cross-shop item reference and ally feed planner.",
    owner: "Reference",
  },
  {
    slug: "weapon-shop",
    category: "shop",
    title: "Weapon Shop",
    shortTitle: "Weapon",
    description: "Weapons with rank, type, and crafting requirements.",
    owner: "Blacksmith",
    building: requireBuilding("Weapon Shop"),
    workbench: { id: 115, name: "Weapon Workbench", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "armor-shop",
    category: "shop",
    title: "Armor Shop",
    shortTitle: "Armor",
    description: "Armor, headgear, and shields with rank and crafting requirements.",
    owner: "Blacksmith",
    building: requireBuilding("Armor Shop"),
    workbench: { id: 116, name: "Armor Workbench", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "accessory-shop",
    category: "shop",
    title: "Accessory Shop",
    shortTitle: "Accessories",
    description: "Accessories with rank and crafting requirements.",
    owner: "Trader",
    building: requireBuilding("Accessory Shop"),
    workbench: { id: 110, name: "Accessory Workshop", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "item-shop",
    category: "shop",
    title: "Item Shop",
    shortTitle: "Items",
    description: "Craftable items with studio level and intelligence requirements.",
    owner: "Trader",
    building: requireBuilding("Item Shop"),
    workbench: { id: 118, name: "Item Workbench", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "furniture-shop",
    category: "shop",
    title: "Furniture Shop",
    shortTitle: "Furniture",
    description: "Furniture with studio level and intelligence requirements.",
    owner: "Artisan",
    building: requireBuilding("Furniture Shop"),
    workbench: { id: 114, name: "Furniture Workbench", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "restaurant",
    category: "shop",
    title: "Restaurant",
    shortTitle: "Restaurant",
    description: "Cooked foods with studio level and intelligence requirements.",
    owner: "Cook",
    building: requireBuilding("Restaurant"),
    workbench: { id: 111, name: "Cooking Station", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 4, upgMystic: 0, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 75, maxUpgMystic: 8 },
  },
  {
    slug: "skill-shop",
    category: "shop",
    title: "Skill Shop",
    shortTitle: "Skills",
    description: "Skills with crafting requirements and prices.",
    owner: "Mage",
    building: requireBuilding("Skill Shop"),
    workbench: { id: 117, name: "Skill Workbench", size: "2×2", upgGrass: 0, upgWood: 8, upgFood: 0, upgOre: 0, upgMystic: 5, maxUpgGrass: 15, maxUpgWood: 110, maxUpgFood: 12, maxUpgOre: 10, maxUpgMystic: 70 },
  },
  {
    slug: "insectarium",
    category: "shop",
    title: "Insectarium",
    shortTitle: "Insectarium",
    description: "Insect collections sold for silver coins.",
    owner: "Entertainer",
    collectionCategory: 0,
    currency: "silver",
    building: requireBuilding("Insectarium"),
  },
  {
    slug: "aquarium",
    category: "shop",
    title: "Aquarium",
    shortTitle: "Aquarium",
    description: "Fish collections sold for silver coins.",
    owner: "Entertainer",
    collectionCategory: 1,
    currency: "silver",
    building: requireBuilding("Aquarium"),
  },
  {
    slug: "museum",
    category: "shop",
    title: "Museum",
    shortTitle: "Museum",
    description: "Art pieces sold for silver coins.",
    owner: "Entertainer",
    collectionCategory: 2,
    currency: "silver",
    building: requireBuilding("Museum"),
  },
  {
    slug: "zoo",
    category: "shop",
    title: "Zoo",
    shortTitle: "Zoo",
    description: "Animals sold for silver coins.",
    owner: "Beast Tamer",
    currency: "silver",
    building: requireBuilding("Zoo"),
  },
  {
    slug: "orchard",
    category: "facility",
    title: "Orchard",
    shortTitle: "Orchard",
    description: "Orchard harvest and food items with studio level and intelligence requirements.",
    owner: "Farmer",
    building: requireBuilding("Orchard"),
  },
];

export const SHOP_NAME_TO_SLUG: Record<string, ShopSlug> = {
  "Weapon Shop": "weapon-shop",
  "Armor Shop": "armor-shop",
  "Accessory Shop": "accessory-shop",
  "Item Shop": "item-shop",
  "Furniture Shop": "furniture-shop",
  Restaurant: "restaurant",
  "Skill Shop": "skill-shop",
  Insectarium: "insectarium",
  Aquarium: "aquarium",
  Museum: "museum",
  Zoo: "zoo",
  Orchard: "orchard",
};

export function getShopSlug(shopName: string): ShopSlug | null {
  return SHOP_NAME_TO_SLUG[shopName] ?? null;
}

export function getShopHref(shopName: string): string | null {
  const slug = getShopSlug(shopName);
  return slug ? `/shops/${slug}` : null;
}
