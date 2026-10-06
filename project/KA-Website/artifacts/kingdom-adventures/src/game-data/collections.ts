import { parseCsv } from "@/lib/monster-truth";
import collectionCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Collection.csv?raw";

export type CollectionCategory = 0 | 1 | 2;
export type CollectionItem = {
  id: number;
  category: CollectionCategory;
  name: string;
  silverPrice: number;
  studioLevel: number | null;
  intelligence: number | null;
  terrain: number | null;
  areaLevel: number | null;
  icon: string;
};

const rows = parseCsv(collectionCsv);
const columns = rows[0];
const value = (row: string[], key: string) => row[columns.indexOf(key)] ?? "";
const optionalNumber = (raw: string) => {
  const number = Number(raw);
  return raw !== "" && number > 0 ? number : null;
};

export const COLLECTION_ITEMS: CollectionItem[] = rows.slice(1)
  .filter((row) => row.length >= columns.length && value(row, "name"))
  .map((row) => {
    const terrain = Number(value(row, "terrain"));
    const id = Number(value(row, "id"));
    return {
      id,
      category: Number(value(row, "category")) as CollectionCategory,
      name: value(row, "name"),
      silverPrice: Number(value(row, "silverPrice")),
      studioLevel: optionalNumber(value(row, "craftTermStudioLevel")),
      intelligence: optionalNumber(value(row, "craftTermIntelligence")),
      terrain: terrain >= 0 ? terrain : null,
      areaLevel: terrain >= 0 ? optionalNumber(value(row, "areaLevel")) : null,
      icon: `${import.meta.env.BASE_URL}collection-icons/${id}.png`,
    };
  });
