import equipmentCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Equip.csv?raw";
import { parseCsv } from "@/lib/csv";

export type EquipmentOrderKey = {
  sourceId: number | null;
  sourceName?: string;
  name: string;
};

type EquipmentOrderComparator = (a: EquipmentOrderKey, b: EquipmentOrderKey) => number;

function normalizeEquipmentName(name: string | undefined): string {
  return (name ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase("en-US");
}

export function createEquipmentOrderComparator(csvText: string): EquipmentOrderComparator {
  const rows = parseCsv(csvText);
  const headerIndex = rows.findIndex((row) => row.some((cell) => cell.trim().toLowerCase() === "name"));
  const header = rows[headerIndex] ?? [];
  const nameIndex = header.findIndex((cell) => cell.trim().toLowerCase() === "name");
  const explicitIdIndex = header.findIndex((cell) => /^(?:id|equip(?:ment)?id)$/i.test(cell.trim()));
  const idIndex = explicitIdIndex >= 0 ? explicitIdIndex : 0;

  const positionById = new Map<number, number>();
  const positionByName = new Map<string, number>();
  const ambiguousNames = new Set<string>();

  if (nameIndex >= 0) {
    for (const [offset, row] of rows.slice(headerIndex + 1).entries()) {
      const position = headerIndex + 1 + offset;
      const name = String(row[nameIndex] ?? "").trim();
      if (!name) continue;

      const rawId = String(row[idIndex] ?? "").trim();
      const id = rawId === "" ? Number.NaN : Number(rawId);
      if (Number.isSafeInteger(id) && !positionById.has(id)) positionById.set(id, position);

      const nameKey = normalizeEquipmentName(name);
      if (ambiguousNames.has(nameKey)) continue;
      if (positionByName.has(nameKey)) {
        positionByName.delete(nameKey);
        ambiguousNames.add(nameKey);
      } else {
        positionByName.set(nameKey, position);
      }
    }
  }

  const getPosition = ({ sourceId, sourceName, name }: EquipmentOrderKey): number | undefined => {
    if (sourceId !== null) return positionById.get(sourceId);
    const nameKey = normalizeEquipmentName(sourceName ?? name);
    return nameKey && !ambiguousNames.has(nameKey) ? positionByName.get(nameKey) : undefined;
  };

  return (a, b) => {
    const aPosition = getPosition(a);
    const bPosition = getPosition(b);
    if (aPosition === undefined) return bPosition === undefined ? 0 : 1;
    if (bPosition === undefined) return -1;
    return aPosition - bPosition;
  };
}

export const compareEquipmentOriginalOrder = createEquipmentOrderComparator(equipmentCsv);
