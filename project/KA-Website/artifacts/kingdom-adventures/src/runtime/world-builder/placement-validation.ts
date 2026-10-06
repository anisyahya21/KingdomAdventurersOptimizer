import type {
  CellPoint,
  EntityId,
  PlaceChipCommand,
  PlacementValidationResult,
  RuntimeCatalog,
  SimWorldApi,
} from "./types";
import {
  getF2MapChipRuntimeMeta,
  isF2RoadLike,
  isF2SpecialOverlay,
} from "./fixtures/f2-semantic-layer";

export function canTraverseF2(chipId: number): boolean {
  const meta = getF2MapChipRuntimeMeta(chipId);
  if (!meta) {
    return false;
  }

  if (isF2SpecialOverlay(chipId)) {
    return false;
  }

  if (meta.flags.includes("No Traffic")) {
    return false;
  }

  if ((meta.movementCost ?? 0) >= 500 || (meta.targetCost ?? 0) >= 500) {
    return false;
  }

  return true;
}

export function canConstructOnF2(chipId: number): boolean {
  const meta = getF2MapChipRuntimeMeta(chipId);
  if (!meta) {
    return false;
  }

  if (isF2SpecialOverlay(chipId)) {
    return false;
  }

  if (meta.flags.includes("No Construction")) {
    return false;
  }

  return true;
}

export function isBlockedF2(chipId: number): boolean {
  const meta = getF2MapChipRuntimeMeta(chipId);
  if (!meta) {
    return true;
  }

  if (isF2SpecialOverlay(chipId)) {
    return true;
  }

  if (meta.flags.includes("No Traffic")) {
    return true;
  }

  return false;
}

export function getOccupiedCellsForChip(
  catalog: RuntimeCatalog,
  chipId: number,
  x: number,
  y: number,
): CellPoint[] {
  const chip = catalog.getMapChip(chipId);
  const cells: CellPoint[] = [];

  for (let yy = y; yy < y + chip.sizeHeight; yy++) {
    for (let xx = x; xx < x + chip.sizeWidth; xx++) {
      cells.push({ x: xx, y: yy });
    }
  }

  return cells;
}

export function canPlaceChip(
  catalog: RuntimeCatalog,
  world: SimWorldApi,
  command: PlaceChipCommand,
): PlacementValidationResult {
  const reasons: PlacementValidationResult["reasons"] = [];
  let occupiedCells: CellPoint[] = [];

  try {
    occupiedCells = getOccupiedCellsForChip(catalog, command.chipId, command.x, command.y);
  } catch {
    reasons.push({
      code: "UNKNOWN_CHIP",
      message: `Unknown chip id ${command.chipId}`,
    });

    return {
      ok: false,
      occupiedCells: [],
      reasons,
    };
  }

  const stackAllowedEntityIds = resolveStackAllowedEntities(world, command.parentEntityId);

  for (const cell of occupiedCells) {
    if (cell.x < 0 || cell.y < 0 || cell.x >= world.width || cell.y >= world.height) {
      reasons.push({
        code: "OUT_OF_BOUNDS",
        message: `Cell ${cell.x},${cell.y} is out of bounds`,
        x: cell.x,
        y: cell.y,
      });
      continue;
    }

    const runtimeReason = getRuntimeSemanticReason(world, command, cell.x, cell.y);
    if (runtimeReason) {
      reasons.push(runtimeReason);
      continue;
    }

    const terrainReason = getTerrainBlockReason(catalog, world, command.chipId, cell.x, cell.y);
    if (terrainReason) {
      reasons.push(terrainReason);
      continue;
    }

    const entitiesAtCell = getEntitiesOccupyingCell(world, cell.x, cell.y);
    const blockingEntityIds = entitiesAtCell
      .map((entity) => entity.id)
      .filter((entityId) => !stackAllowedEntityIds.has(entityId));

    if (blockingEntityIds.length > 0) {
      reasons.push({
        code: "CELL_BLOCKED",
        message: `Cell ${cell.x},${cell.y} is occupied by blocking entities`,
        x: cell.x,
        y: cell.y,
        blockingEntityIds,
      });
    }
  }

  return {
    ok: reasons.length === 0,
    occupiedCells,
    reasons,
  };
}

function getRuntimeSemanticReason(
  world: SimWorldApi,
  command: PlaceChipCommand,
  x: number,
  y: number,
): PlacementValidationResult["reasons"][number] | undefined {
  // Keep system-driven composition (e.g. stacked child placement) behavior unchanged.
  if (command.source === "system") {
    return undefined;
  }

  if (isF2RoadLike(command.chipId) && world.getCell(x, y).terrainKind !== "soil") {
    return {
      code: "ROAD_ONLY",
      message: `Road-like chip ${command.chipId} requires soil cell at ${x},${y}`,
      x,
      y,
    };
  }

  const baseChipId = world.getEffectiveF2ChipId(x, y);
  if (world.isBlockedCell(x, y)) {
    return {
      code: "BLOCKED_BY_RUNTIME_MASK",
      message: `Cell ${x},${y} base chip ${baseChipId ?? "unknown"} is runtime-blocked`,
      x,
      y,
    };
  }

  const semanticGroup = world.getEffectiveSemanticGroup(x, y);
  if (
    !world.canConstructCell(x, y) &&
    semanticGroup !== "terrain/ground/water" &&
    semanticGroup !== "road/path/traffic/buildable-related"
  ) {
    return {
      code: "NON_BUILDABLE",
      message: `Cell ${x},${y} base chip ${baseChipId ?? "unknown"} is non-buildable`,
      x,
      y,
    };
  }

  return undefined;
}

function getTerrainBlockReason(
  catalog: RuntimeCatalog,
  world: SimWorldApi,
  chipId: number,
  x: number,
  y: number,
): PlacementValidationResult["reasons"][number] | undefined {
  const chip = catalog.getMapChip(chipId);
  const rule = chip.placementTerrainRule ?? "any";
  const terrain = world.getCell(x, y).terrainKind;

  if (rule === "soil_only" && terrain !== "soil") {
    return {
      code: "TERRAIN_BLOCKED",
      message: `Cell ${x},${y} terrain ${terrain} violates rule ${rule}`,
      x,
      y,
    };
  }

  return undefined;
}

function resolveStackAllowedEntities(world: SimWorldApi, parentEntityId: EntityId | undefined): Set<EntityId> {
  const allowed = new Set<EntityId>();

  if (parentEntityId === undefined) {
    return allowed;
  }

  const parent = world.getEntity(parentEntityId);
  if (!parent) {
    return allowed;
  }

  allowed.add(parent.id);
  for (const childId of parent.childIds) {
    allowed.add(childId);
  }

  return allowed;
}

function getEntitiesOccupyingCell(world: SimWorldApi, x: number, y: number) {
  return [...world.entities.values()].filter((entity) => {
    const rect = entity.components.mapChipRect;
    return x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height;
  });
}
