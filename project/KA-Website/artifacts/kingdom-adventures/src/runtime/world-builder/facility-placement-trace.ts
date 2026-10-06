import type { PlacementPipeline } from "./pipeline";
import type { CellPoint, SimWorldApi, WorldEntity } from "./types";
import type { SimWorld } from "./world";

export interface FacilityPlacementTraceOptions {
  x?: number;
  y?: number;
  preferInteriorTownArea?: boolean;
}

export interface FacilityPlacementTraceCellSnapshot {
  x: number;
  y: number;
  terrainKind: "soil" | "water";
  townAreaId?: number;
  annotation: {
    f2ChipId?: number;
    semanticGroup?: string;
    isTerrainLike?: boolean;
    isRoadLike?: boolean;
    isSpecialOverlay?: boolean;
    canTraverse?: boolean;
    canConstruct?: boolean;
    isBlocked?: boolean;
  };
  effective: {
    f2ChipId?: number;
    semanticGroup: string;
    canTraverse: boolean;
    canConstruct: boolean;
    isBlocked: boolean;
    overrideSource: string;
  };
  stack: {
    entityCount: number;
    entityIds: number[];
    topEntityId?: number;
    topChipId?: number;
    entities: Array<{
      id: number;
      chipId: number;
      parentId?: number;
      stackOrder: number;
    }>;
  };
}

export interface FacilityPlacementTraceCellDiff {
  x: number;
  y: number;
  before: FacilityPlacementTraceCellSnapshot;
  after: FacilityPlacementTraceCellSnapshot;
  changes: {
    terrainKindChanged: boolean;
    townAreaChanged: boolean;
    annotationF2Changed: boolean;
    annotationSemanticGroupChanged: boolean;
    annotationLegalityChanged: boolean;
    effectiveF2Changed: boolean;
    semanticGroupChanged: boolean;
    canTraverseChanged: boolean;
    canConstructChanged: boolean;
    isBlockedChanged: boolean;
    stackChanged: boolean;
    roadTransition: boolean;
    overlayAppeared: boolean;
  };
}

export interface FacilityPlacementTraceReport {
  source: "synthetic" | "real-map";
  worldSize: { width: number; height: number };
  facility: {
    parentChipId: number;
    expectedChildChipIds: number[];
    command: { x: number; y: number };
  };
  parentPlacement: {
    parentEntityId: number;
    childEntities: Array<{
      id: number;
      chipId: number;
      parentId?: number;
      stackOrder: number;
    }>;
  };
  influenceRegion: {
    occupiedCells: CellPoint[];
    expectedTownAreaRect: { x: number; y: number; width: number; height: number };
    expectedTownAreaCells: CellPoint[];
    expectedCellSet: CellPoint[];
  };
  changedCells: FacilityPlacementTraceCellDiff[];
  summary: {
    changedCellCount: number;
    semanticGroupChangedCells: number;
    legalityChangedCells: number;
    stackChangedCells: number;
    townAreaChangedCells: number;
    annotationF2ChangedCells: number;
    annotationSemanticGroupChangedCells: number;
    annotationLegalityChangedCells: number;
    effectiveF2ChangedCells: number;
    placementReplacesFloorChips: boolean;
    roadsOrPathsGenerated: boolean;
    overlaysAppeared: boolean;
    semanticGroupsChanged: boolean;
    unrelatedCellMutations: CellPoint[];
  };
}

interface RuntimeShape {
  world: SimWorld;
  pipeline: PlacementPipeline;
}

function toCellKey(x: number, y: number): string {
  return `${x},${y}`;
}

function deepEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function isRoadGroup(group: string): boolean {
  return group === "road/path/traffic/buildable-related";
}

function isOverlayGroup(group: string): boolean {
  return group === "special overlay/runtime-marker";
}

function collectTownAreaCells(world: SimWorldApi, x: number, y: number): CellPoint[] {
  const rect = { x: x - 1, y: y - 1, width: 4, height: 4 };
  const cells: CellPoint[] = [];

  const startX = Math.max(0, rect.x);
  const startY = Math.max(0, rect.y);
  const endX = Math.min(world.width, rect.x + rect.width);
  const endY = Math.min(world.height, rect.y + rect.height);

  for (let yy = startY; yy < endY; yy++) {
    for (let xx = startX; xx < endX; xx++) {
      cells.push({ x: xx, y: yy });
    }
  }

  return cells;
}

function snapshotCell(world: SimWorldApi, x: number, y: number): FacilityPlacementTraceCellSnapshot {
  const cell = world.getCell(x, y);
  const annotation = world.getAnnotationCellState(x, y);
  const effective = world.getEffectiveCellState(x, y);
  const entities = world.getEntitiesAt(x, y)
    .slice()
    .sort((left, right) => left.id - right.id);
  const top = world.getTopEntityAt(x, y);

  return {
    x,
    y,
    terrainKind: cell.terrainKind,
    townAreaId: cell.townAreaId,
    annotation: {
      f2ChipId: annotation.f2ChipId,
      semanticGroup: annotation.semanticGroup,
      isTerrainLike: cell.isTerrainLike,
      isRoadLike: cell.isRoadLike,
      isSpecialOverlay: cell.isSpecialOverlay,
      canTraverse: annotation.canTraverse,
      canConstruct: annotation.canConstruct,
      isBlocked: annotation.isBlocked,
    },
    effective: {
      f2ChipId: effective.f2ChipId,
      semanticGroup: effective.semanticGroup,
      canTraverse: effective.canTraverse,
      canConstruct: effective.canConstruct,
      isBlocked: effective.isBlocked,
      overrideSource: effective.overrideSource,
    },
    stack: {
      entityCount: entities.length,
      entityIds: entities.map((entity) => entity.id),
      topEntityId: top?.id,
      topChipId: top?.chipId,
      entities: entities.map((entity) => ({
        id: entity.id,
        chipId: entity.chipId,
        parentId: entity.parentId,
        stackOrder: entity.components.render.stackOrder,
      })),
    },
  };
}

function snapshotGrid(world: SimWorldApi): Map<string, FacilityPlacementTraceCellSnapshot> {
  const map = new Map<string, FacilityPlacementTraceCellSnapshot>();
  for (let y = 0; y < world.height; y++) {
    for (let x = 0; x < world.width; x++) {
      map.set(toCellKey(x, y), snapshotCell(world, x, y));
    }
  }

  return map;
}

function toChildSummary(entity: WorldEntity): {
  id: number;
  chipId: number;
  parentId?: number;
  stackOrder: number;
} {
  return {
    id: entity.id,
    chipId: entity.chipId,
    parentId: entity.parentId,
    stackOrder: entity.components.render.stackOrder,
  };
}

function resolvePlacementCommand(
  world: SimWorld,
  pipeline: PlacementPipeline,
  options: FacilityPlacementTraceOptions | undefined,
): { x: number; y: number } {
  if (options?.x !== undefined || options?.y !== undefined) {
    if (options?.x === undefined || options?.y === undefined) {
      throw new Error("Both x and y must be provided together when overriding placement coordinates");
    }

    const result = pipeline.canPlaceChip(world, { chipId: 58, x: options.x, y: options.y, source: "user" });
    if (!result.ok) {
      const reasonSummary = result.reasons.map((reason) => reason.code).join(", ");
      throw new Error(`Requested placement ${options.x},${options.y} is invalid for Town Hall: ${reasonSummary}`);
    }

    return { x: options.x, y: options.y };
  }

  const preferInterior = options?.preferInteriorTownArea ?? false;
  const minX = preferInterior ? 1 : 0;
  const minY = preferInterior ? 1 : 0;
  const maxX = preferInterior ? world.width - 3 : world.width - 1;
  const maxY = preferInterior ? world.height - 3 : world.height - 1;

  for (let y = minY; y <= maxY; y++) {
    for (let x = minX; x <= maxX; x++) {
      const result = pipeline.canPlaceChip(world, { chipId: 58, x, y, source: "user" });
      if (result.ok) {
        return { x, y };
      }
    }
  }

  throw new Error("No valid Town Hall placement coordinate found in world");
}

function buildTraceReport(
  source: "synthetic" | "real-map",
  world: SimWorld,
  pipeline: PlacementPipeline,
  command: { x: number; y: number },
): FacilityPlacementTraceReport {
  const parentChipId = 58;
  const expectedChildChipIds = [59, 60];

  const occupiedCells = pipeline.getOccupiedCellsForChip(parentChipId, command.x, command.y);
  const townAreaCells = collectTownAreaCells(world, command.x, command.y);
  const expectedCellSet = new Map<string, CellPoint>();
  for (const cell of occupiedCells) {
    expectedCellSet.set(toCellKey(cell.x, cell.y), cell);
  }
  for (const cell of townAreaCells) {
    expectedCellSet.set(toCellKey(cell.x, cell.y), cell);
  }

  const before = snapshotGrid(world);
  const parent = pipeline.placeChip(world, { chipId: parentChipId, x: command.x, y: command.y, source: "user" });
  const after = snapshotGrid(world);

  const changedCells: FacilityPlacementTraceCellDiff[] = [];

  for (const [key, beforeCell] of before.entries()) {
    const afterCell = after.get(key);
    if (!afterCell || deepEqual(beforeCell, afterCell)) {
      continue;
    }

    const annotationLegalityChanged =
      beforeCell.annotation.canTraverse !== afterCell.annotation.canTraverse ||
      beforeCell.annotation.canConstruct !== afterCell.annotation.canConstruct ||
      beforeCell.annotation.isBlocked !== afterCell.annotation.isBlocked;

    changedCells.push({
      x: beforeCell.x,
      y: beforeCell.y,
      before: beforeCell,
      after: afterCell,
      changes: {
        terrainKindChanged: beforeCell.terrainKind !== afterCell.terrainKind,
        townAreaChanged: beforeCell.townAreaId !== afterCell.townAreaId,
        annotationF2Changed: beforeCell.annotation.f2ChipId !== afterCell.annotation.f2ChipId,
        annotationSemanticGroupChanged: beforeCell.annotation.semanticGroup !== afterCell.annotation.semanticGroup,
        annotationLegalityChanged,
        effectiveF2Changed: beforeCell.effective.f2ChipId !== afterCell.effective.f2ChipId,
        semanticGroupChanged: beforeCell.effective.semanticGroup !== afterCell.effective.semanticGroup,
        canTraverseChanged: beforeCell.effective.canTraverse !== afterCell.effective.canTraverse,
        canConstructChanged: beforeCell.effective.canConstruct !== afterCell.effective.canConstruct,
        isBlockedChanged: beforeCell.effective.isBlocked !== afterCell.effective.isBlocked,
        stackChanged: !deepEqual(beforeCell.stack, afterCell.stack),
        roadTransition:
          !isRoadGroup(beforeCell.effective.semanticGroup) && isRoadGroup(afterCell.effective.semanticGroup),
        overlayAppeared:
          !isOverlayGroup(beforeCell.effective.semanticGroup) && isOverlayGroup(afterCell.effective.semanticGroup),
      },
    });
  }

  changedCells.sort((left, right) => left.y - right.y || left.x - right.x);

  const childEntities = parent.childIds
    .map((childId) => world.getEntity(childId))
    .filter((entity): entity is WorldEntity => entity !== undefined)
    .sort((left, right) => left.id - right.id)
    .map(toChildSummary);

  const unrelatedCellMutations = changedCells
    .map((cell) => ({ x: cell.x, y: cell.y }))
    .filter((cell) => !expectedCellSet.has(toCellKey(cell.x, cell.y)));

  const summary = {
    changedCellCount: changedCells.length,
    semanticGroupChangedCells: changedCells.filter((cell) => cell.changes.semanticGroupChanged).length,
    legalityChangedCells: changedCells.filter(
      (cell) => cell.changes.canTraverseChanged || cell.changes.canConstructChanged || cell.changes.isBlockedChanged,
    ).length,
    stackChangedCells: changedCells.filter((cell) => cell.changes.stackChanged).length,
    townAreaChangedCells: changedCells.filter((cell) => cell.changes.townAreaChanged).length,
    annotationF2ChangedCells: changedCells.filter((cell) => cell.changes.annotationF2Changed).length,
    annotationSemanticGroupChangedCells: changedCells.filter((cell) => cell.changes.annotationSemanticGroupChanged)
      .length,
    annotationLegalityChangedCells: changedCells.filter((cell) => cell.changes.annotationLegalityChanged).length,
    effectiveF2ChangedCells: changedCells.filter((cell) => cell.changes.effectiveF2Changed).length,
    placementReplacesFloorChips: changedCells.some((cell) => cell.changes.annotationF2Changed),
    roadsOrPathsGenerated: changedCells.some((cell) => cell.changes.roadTransition),
    overlaysAppeared: changedCells.some((cell) => cell.changes.overlayAppeared),
    semanticGroupsChanged: changedCells.some((cell) => cell.changes.semanticGroupChanged),
    unrelatedCellMutations,
  };

  return {
    source,
    worldSize: { width: world.width, height: world.height },
    facility: {
      parentChipId,
      expectedChildChipIds,
      command,
    },
    parentPlacement: {
      parentEntityId: parent.id,
      childEntities,
    },
    influenceRegion: {
      occupiedCells,
      expectedTownAreaRect: { x: command.x - 1, y: command.y - 1, width: 4, height: 4 },
      expectedTownAreaCells: townAreaCells,
      expectedCellSet: [...expectedCellSet.values()].sort((left, right) => left.y - right.y || left.x - right.x),
    },
    changedCells,
    summary,
  };
}

export function traceTownHallFacilityPlacementOnWorld(
  world: SimWorld,
  pipeline: PlacementPipeline,
  options?: FacilityPlacementTraceOptions,
): FacilityPlacementTraceReport {
  const command = resolvePlacementCommand(world, pipeline, options);
  return buildTraceReport("real-map", world, pipeline, command);
}

export function traceTownHallFacilityPlacement(
  createRuntime: () => RuntimeShape,
  options?: FacilityPlacementTraceOptions,
): FacilityPlacementTraceReport {
  const { world, pipeline } = createRuntime();
  const command = options?.x !== undefined && options?.y !== undefined
    ? { x: options.x, y: options.y }
    : { x: 4, y: 5 };

  return buildTraceReport("synthetic", world, pipeline, command);
}

export function buildFacilityPlacementTraceMarkdown(report: FacilityPlacementTraceReport): string {
  const lines: string[] = [];
  lines.push(`# Facility Placement Trace (Town Hall - ${report.source})`);
  lines.push("");
  lines.push("## Placement");
  lines.push(`- World size: ${report.worldSize.width}x${report.worldSize.height}`);
  lines.push(`- Parent chip: ${report.facility.parentChipId}`);
  lines.push(`- Command: (${report.facility.command.x}, ${report.facility.command.y})`);
  lines.push(`- Expected child chips: ${report.facility.expectedChildChipIds.join(", ")}`);
  lines.push(`- Parent entity id: ${report.parentPlacement.parentEntityId}`);
  lines.push(
    `- Child entities created: ${report.parentPlacement.childEntities.map((child) => `${child.chipId}#${child.id}`).join(", ") || "none"}`,
  );
  lines.push("");

  lines.push("## Summary");
  lines.push(`- Changed cells: ${report.summary.changedCellCount}`);
  lines.push(`- Semantic group changed cells: ${report.summary.semanticGroupChangedCells}`);
  lines.push(`- Legality changed cells: ${report.summary.legalityChangedCells}`);
  lines.push(`- Stack changed cells: ${report.summary.stackChangedCells}`);
  lines.push(`- Town area changed cells: ${report.summary.townAreaChangedCells}`);
  lines.push(`- Annotation f2 changed cells: ${report.summary.annotationF2ChangedCells}`);
  lines.push(`- Annotation semantic-group changed cells: ${report.summary.annotationSemanticGroupChangedCells}`);
  lines.push(`- Annotation legality changed cells: ${report.summary.annotationLegalityChangedCells}`);
  lines.push(`- Effective f2 changed cells: ${report.summary.effectiveF2ChangedCells}`);
  lines.push(`- Placement replaces floor chips: ${report.summary.placementReplacesFloorChips}`);
  lines.push(`- Roads/path chips generated: ${report.summary.roadsOrPathsGenerated}`);
  lines.push(`- Overlays appeared: ${report.summary.overlaysAppeared}`);
  lines.push(`- Semantic groups changed: ${report.summary.semanticGroupsChanged}`);
  lines.push(
    `- Unrelated cell mutations: ${report.summary.unrelatedCellMutations.length === 0 ? "none" : report.summary.unrelatedCellMutations.map((cell) => `(${cell.x},${cell.y})`).join(", ")}`,
  );
  lines.push("");

  lines.push("## Changed Cells");
  if (report.changedCells.length === 0) {
    lines.push("- None");
  } else {
    for (const cell of report.changedCells) {
      lines.push(`### (${cell.x}, ${cell.y})`);
      lines.push(`- Terrain: ${cell.before.terrainKind} -> ${cell.after.terrainKind}`);
      lines.push(`- TownArea: ${String(cell.before.townAreaId)} -> ${String(cell.after.townAreaId)}`);
      lines.push(`- Annotation f2: ${String(cell.before.annotation.f2ChipId)} -> ${String(cell.after.annotation.f2ChipId)}`);
      lines.push(
        `- Annotation semantic group: ${String(cell.before.annotation.semanticGroup)} -> ${String(cell.after.annotation.semanticGroup)}`,
      );
      lines.push(
        `- Annotation legality: traverse ${String(cell.before.annotation.canTraverse)} -> ${String(cell.after.annotation.canTraverse)}, construct ${String(cell.before.annotation.canConstruct)} -> ${String(cell.after.annotation.canConstruct)}, blocked ${String(cell.before.annotation.isBlocked)} -> ${String(cell.after.annotation.isBlocked)}`,
      );
      lines.push(`- Effective f2: ${String(cell.before.effective.f2ChipId)} -> ${String(cell.after.effective.f2ChipId)}`);
      lines.push(
        `- Effective semantic group: ${cell.before.effective.semanticGroup} -> ${cell.after.effective.semanticGroup}`,
      );
      lines.push(
        `- Effective legality: traverse ${cell.before.effective.canTraverse} -> ${cell.after.effective.canTraverse}, construct ${cell.before.effective.canConstruct} -> ${cell.after.effective.canConstruct}, blocked ${cell.before.effective.isBlocked} -> ${cell.after.effective.isBlocked}`,
      );
      lines.push(`- Effective override source: ${cell.before.effective.overrideSource} -> ${cell.after.effective.overrideSource}`);
      lines.push(
        `- Stack: count ${cell.before.stack.entityCount} -> ${cell.after.stack.entityCount}, top ${String(cell.before.stack.topChipId)} -> ${String(cell.after.stack.topChipId)}`,
      );
      lines.push(
        `- Flags: terrainChanged=${cell.changes.terrainKindChanged}, townAreaChanged=${cell.changes.townAreaChanged}, stackChanged=${cell.changes.stackChanged}, roadTransition=${cell.changes.roadTransition}, overlayAppeared=${cell.changes.overlayAppeared}`,
      );
      lines.push("");
    }
  }

  return `${lines.join("\n")}\n`;
}
