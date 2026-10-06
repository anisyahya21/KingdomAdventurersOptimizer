import type { ParsedMapBinary, ParsedMapCell } from "./types";

export interface NativeMapCell extends ParsedMapCell {
  /** Five values in the original cell array; meanings are not assigned here. */
  chipValues: number[];
  heightLevel: number;
  /** Base terrain Y only, before chip/attached-entity heights. */
  baseHeightPixels: number;
}

export interface NativeMapBinary extends ParsedMapBinary {
  cells: NativeMapCell[];
  heightLevels: number[][];
}

/** MapSystem.CreateMapChips 0x15b1b90..1bac, ordinary int32 levels. */
export function nativeBaseHeightPixels(level: number): number {
  if (!Number.isInteger(level) || level < -2147483648 || level > 2147483647)
    throw new Error("Invalid native height level");
  return level > 0 ? (Math.imul(level, 20) - 10) | 0 : 0;
}

/** ReadMapData 0x15b3690..36a8: counted int[][][] chips, then int[][] heights.
 * This renderer supports rectangular maps with five values per cell. Reject
 * other layouts rather than silently feeding guessed fields to old consumers.
 */
export function parseNativeMapBinary(input: ArrayBuffer | Uint8Array): NativeMapBinary {
  const view = input instanceof Uint8Array
    ? new DataView(input.buffer, input.byteOffset, input.byteLength) : new DataView(input);
  let offset = 0;
  const readInt = () => {
    if (offset + 4 > view.byteLength) throw new Error(`Truncated native map at byte ${offset}`);
    const value = view.getInt32(offset, false); offset += 4; return value;
  };
  const readCount = () => {
    const count = readInt();
    if (count <= 0 || count > Math.floor((view.byteLength - offset) / 4))
      throw new Error(`Invalid native map array length at byte ${offset - 4}`);
    return count;
  };
  const height = readCount();
  let width = 0;
  const cells: NativeMapCell[] = [];
  for (let y = 0; y < height; y++) {
    const columns = readCount();
    if (y === 0) width = columns;
    if (columns !== width) throw new Error("Nonrectangular native chip map");
    for (let x = 0; x < width; x++) {
      const count = readCount();
      if (count !== 5) throw new Error("Unsupported native cell array length");
      const chipValues = Array.from({ length: count }, readInt);
      // Compatibility aliases: f0 is framing, not a sixth game value.
      const [f1, f2, f3, f4, f5] = chipValues;
      cells.push({ x, y, chipValues, fields: { f0: count, f1, f2, f3, f4, f5 },
        heightLevel: 0, baseHeightPixels: 0 });
    }
  }
  if (readCount() !== height) throw new Error("Native height-map row count mismatch");
  const heightLevels: number[][] = [];
  for (let y = 0; y < height; y++) {
    if (readCount() !== width) throw new Error("Native height-map column count mismatch");
    const row: number[] = [];
    for (let x = 0; x < width; x++) {
      const level = readInt(); row.push(level);
      const cell = cells[y * width + x];
      cell.heightLevel = level; cell.baseHeightPixels = nativeBaseHeightPixels(level);
    }
    heightLevels.push(row);
  }
  if (offset !== view.byteLength) throw new Error("Unexpected trailing native map bytes");
  return { width, height, cells, heightLevels, remainingBytes: 0 };
}
