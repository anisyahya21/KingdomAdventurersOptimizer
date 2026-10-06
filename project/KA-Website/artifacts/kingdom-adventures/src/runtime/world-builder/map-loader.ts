import type { ParsedMapBinary, ParsedMapCell, TerrainKind } from "./types";
import {
  getF2SemanticGroup,
  isF2RoadLike,
  isF2SpecialOverlay,
  isF2TerrainLike,
} from "./fixtures/f2-semantic-layer";
import { canConstructOnF2, canTraverseF2, isBlockedF2 } from "./placement-validation";
import { SimWorld } from "./world";

const BYTES_PER_U32 = 4;
const SECTION_HEADER_BYTES = 8;
const CELL_FIELDS_COUNT = 6;
const CELL_BYTES = CELL_FIELDS_COUNT * BYTES_PER_U32;
const ROW_SENTINEL_BYTES = BYTES_PER_U32;

export function parseMapBinarySectionA(input: ArrayBuffer | Uint8Array): ParsedMapBinary {
  const view = toDataView(input);

  if (view.byteLength < SECTION_HEADER_BYTES) {
    throw new Error(`Map binary too small: ${view.byteLength} bytes`);
  }

  let offset = 0;
  const width = view.getUint32(offset, false);
  offset += BYTES_PER_U32;
  const height = view.getUint32(offset, false);
  offset += BYTES_PER_U32;

  const cells: ParsedMapCell[] = [];

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      ensureAvailable(view, offset, CELL_BYTES, `cell ${x},${y}`);

      const f0 = view.getUint32(offset, false);
      const f1 = view.getUint32(offset + 4, false);
      const f2 = view.getUint32(offset + 8, false);
      const f3 = view.getUint32(offset + 12, false);
      const f4 = view.getUint32(offset + 16, false);
      const f5 = view.getUint32(offset + 20, false);
      offset += CELL_BYTES;

      cells.push({
        x,
        y,
        fields: { f0, f1, f2, f3, f4, f5 },
      });
    }

    ensureAvailable(view, offset, ROW_SENTINEL_BYTES, `row sentinel ${y}`);
    const sentinel = view.getUint32(offset, false);
    offset += ROW_SENTINEL_BYTES;

    if (sentinel !== width) {
      throw new Error(`Invalid row sentinel at row ${y}: expected ${width}, got ${sentinel}`);
    }
  }

  return {
    width,
    height,
    cells,
    remainingBytes: view.byteLength - offset,
  };
}

export function createWorldFromParsedMap(
  parsed: ParsedMapBinary,
  resolveTerrainKind?: (cell: ParsedMapCell) => TerrainKind,
): SimWorld {
  const world = new SimWorld(parsed.width, parsed.height);
  const terrainResolver = resolveTerrainKind ?? (() => "soil");

  for (const cell of parsed.cells) {
    world.setCellTerrainKind(cell.x, cell.y, terrainResolver(cell));
    const worldCell = world.getCell(cell.x, cell.y);
    worldCell.f2ChipId = cell.fields.f2;
    worldCell.semanticGroup = getF2SemanticGroup(cell.fields.f2);
    worldCell.isTerrainLike = isF2TerrainLike(cell.fields.f2);
    worldCell.isRoadLike = isF2RoadLike(cell.fields.f2);
    worldCell.isSpecialOverlay = isF2SpecialOverlay(cell.fields.f2);
    worldCell.canTraverse = canTraverseF2(cell.fields.f2);
    worldCell.canConstruct = canConstructOnF2(cell.fields.f2);
    worldCell.isBlocked = isBlockedF2(cell.fields.f2);
  }

  return world;
}

function toDataView(input: ArrayBuffer | Uint8Array): DataView {
  if (input instanceof Uint8Array) {
    return new DataView(input.buffer, input.byteOffset, input.byteLength);
  }

  return new DataView(input);
}

function ensureAvailable(view: DataView, offset: number, needed: number, label: string): void {
  if (offset + needed > view.byteLength) {
    throw new Error(`Unexpected end of map binary while reading ${label}`);
  }
}
