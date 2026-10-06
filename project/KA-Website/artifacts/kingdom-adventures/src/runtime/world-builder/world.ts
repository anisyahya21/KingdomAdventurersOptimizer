import type {
  AnnotationCellState,
  AnnotationEffectiveCellDiff,
  CellLegality,
  CellPoint,
  CellRect,
  EffectiveCellState,
  EffectiveStateRestorationDiff,
  EntityId,
  F2SemanticGroup,
  MapCell,
  PlaceChipCommand,
  RuntimeOverlayApplyInput,
  RuntimeOverlayRecord,
  SimWorldSnapshot,
  SimWorldApi,
  TownArea,
  WorldEntity,
} from "./types";
import { getF2SemanticGroup } from "./fixtures/f2-semantic-layer";
import { canConstructOnF2, canTraverseF2, isBlockedF2 } from "./placement-validation";

const DEFAULT_ANNOTATION_STATE: AnnotationCellState = {
  semanticGroup: "unknown",
  canTraverse: false,
  canConstruct: false,
  isBlocked: true,
};

const DEFAULT_EFFECTIVE_STATE: EffectiveCellState = {
  semanticGroup: "unknown",
  canTraverse: false,
  canConstruct: false,
  isBlocked: true,
  overrideSource: "none",
};

const EMPTY_SYNTHETIC_EFFECTIVE_STATE: EffectiveCellState = {
  semanticGroup: "unknown",
  canTraverse: true,
  canConstruct: true,
  isBlocked: false,
  overrideSource: "none",
};

export class SimWorld implements SimWorldApi {
  readonly entities = new Map<EntityId, WorldEntity>();
  readonly townAreas = new Map<number, TownArea>();

  private readonly cells: MapCell[][];
  private readonly effectiveStateCache = new Map<string, EffectiveCellState>();
  private readonly runtimeOverlays = new Map<EntityId, RuntimeOverlayRecord>();
  private readonly runtimeOverlayIdsByCell = new Map<string, EntityId[]>();
  private nextEntityId = 1;
  private nextTownAreaId = 1;
  private nextOverlayOrder = 1;

  constructor(
    readonly width: number,
    readonly height: number,
  ) {
    this.cells = Array.from({ length: height }, (_row, y) =>
      Array.from({ length: width }, (_cell, x) => ({ x, y, terrainKind: "soil" as const, entityIds: [] })),
    );
  }

  createEntity(command: PlaceChipCommand, stackOrder: number): WorldEntity {
    const cell = this.getCell(command.x, command.y);
    const entity: WorldEntity = {
      id: this.nextEntityId++,
      chipId: command.chipId,
      cell: { x: command.x, y: command.y },
      parentId: command.parentEntityId,
      childIds: [],
      alive: true,
      components: {
        mapChip: { chipId: command.chipId },
        mapChipRect: { x: command.x, y: command.y, width: 1, height: 1 },
        render: { stackOrder },
      },
    };

    this.entities.set(entity.id, entity);
    cell.entityIds.push(entity.id);
    this.rebuildEffectiveState(command.x, command.y);

    if (command.parentEntityId !== undefined) {
      this.linkChild(command.parentEntityId, entity.id);
    }

    return entity;
  }

  removeEntity(entityId: EntityId): WorldEntity {
    const entity = this.entities.get(entityId);
    if (!entity) {
      throw new Error(`Cannot remove missing entity ${entityId}`);
    }

    for (const childId of entity.childIds) {
      const child = this.entities.get(childId);
      if (child) {
        child.parentId = undefined;
      }
    }

    if (entity.parentId !== undefined) {
      const parent = this.entities.get(entity.parentId);
      if (parent) {
        parent.childIds = parent.childIds.filter((childId) => childId !== entity.id);
      }
    }

    const cell = this.getCell(entity.cell.x, entity.cell.y);
    cell.entityIds = cell.entityIds.filter((id) => id !== entity.id);

    const overlayRecord = this.unregisterRuntimeOverlayMetadata(entity.id);
    if (!overlayRecord) {
      this.rebuildEffectiveState(entity.cell.x, entity.cell.y);
    }

    entity.alive = false;
    this.entities.delete(entity.id);
    this.removeTownAreasByOriginEntity(entity.id);
    return entity;
  }

  getEntity(entityId: EntityId): WorldEntity | undefined {
    return this.entities.get(entityId);
  }

  createTownArea(originEntityId: EntityId, rect: CellRect): TownArea {
    const townArea: TownArea = {
      id: this.nextTownAreaId++,
      originEntityId,
      rect: { ...rect },
    };

    this.townAreas.set(townArea.id, townArea);
    this.markTownAreaCells(townArea);
    return townArea;
  }

  getTownArea(townAreaId: number): TownArea | undefined {
    return this.townAreas.get(townAreaId);
  }

  linkChild(parentId: EntityId, childId: EntityId): void {
    const parent = this.entities.get(parentId);
    const child = this.entities.get(childId);

    if (!parent || !child) {
      throw new Error(`Cannot link missing entities parent=${parentId} child=${childId}`);
    }

    child.parentId = parentId;

    if (!parent.childIds.includes(childId)) {
      parent.childIds.push(childId);
    }
  }

  getCell(x: number, y: number): MapCell {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) {
      throw new Error(`Cell out of bounds: ${x},${y}`);
    }

    return this.cells[y][x];
  }

  setCellTerrainKind(x: number, y: number, terrainKind: MapCell["terrainKind"]): void {
    this.getCell(x, y).terrainKind = terrainKind;
  }

  applyRuntimeOverlay(input: RuntimeOverlayApplyInput): RuntimeOverlayRecord {
    const width = input.width ?? 1;
    const height = input.height ?? 1;
    const priority = input.priority ?? 0;

    const entity = this.createEntity({ chipId: input.chipId, x: input.x, y: input.y, source: "system" }, priority);
    entity.components.mapChipRect = { x: input.x, y: input.y, width, height };

    const affectedCells = this.collectCoveredCells(input.x, input.y, width, height);

    const record: RuntimeOverlayRecord = {
      overlayId: entity.id,
      chipId: input.chipId,
      sourceEntityId: input.sourceEntityId,
      sourceFacilityId: input.sourceFacilityId,
      priority,
      order: this.nextOverlayOrder++,
      affectedCells,
      semanticOverrideBehavior: input.semanticOverrideBehavior ?? "full-replace",
    };

    this.runtimeOverlays.set(record.overlayId, record);
    for (const cell of affectedCells) {
      const key = this.toCellCacheKey(cell.x, cell.y);
      const ids = this.runtimeOverlayIdsByCell.get(key) ?? [];
      ids.push(record.overlayId);
      ids.sort((left, right) => this.compareOverlayIds(left, right));
      this.runtimeOverlayIdsByCell.set(key, ids);
      this.rebuildEffectiveState(cell.x, cell.y);
    }

    return this.cloneOverlayRecord(record);
  }

  removeRuntimeOverlay(overlayId: EntityId): RuntimeOverlayRecord {
    const record = this.runtimeOverlays.get(overlayId);
    if (!record) {
      throw new Error(`Cannot remove missing runtime overlay ${overlayId}`);
    }

    this.removeEntity(overlayId);
    return this.cloneOverlayRecord(record);
  }

  clearRuntimeOverlaysAtCell(x: number, y: number): RuntimeOverlayRecord[] {
    if (!this.isInBounds(x, y)) {
      return [];
    }

    const key = this.toCellCacheKey(x, y);
    const ids = [...(this.runtimeOverlayIdsByCell.get(key) ?? [])];
    const removed: RuntimeOverlayRecord[] = [];

    // Remove from top overlay down to maintain deterministic restoration sequence.
    for (let index = ids.length - 1; index >= 0; index--) {
      const overlayId = ids[index];
      if (this.runtimeOverlays.has(overlayId)) {
        removed.push(this.removeRuntimeOverlay(overlayId));
      }
    }

    return removed;
  }

  rebuildEffectiveState(x?: number, y?: number): void {
    if (x !== undefined || y !== undefined) {
      if (x === undefined || y === undefined) {
        throw new Error("Both x and y must be provided together when rebuilding a single cell state");
      }
      this.clearEffectiveStateCacheAt(x, y);
      return;
    }

    this.effectiveStateCache.clear();
  }

  getRuntimeOverlayStackAtCell(x: number, y: number): RuntimeOverlayRecord[] {
    if (!this.isInBounds(x, y)) {
      return [];
    }

    const key = this.toCellCacheKey(x, y);
    const ids = this.runtimeOverlayIdsByCell.get(key) ?? [];
    return ids
      .map((overlayId) => this.runtimeOverlays.get(overlayId))
      .filter((record): record is RuntimeOverlayRecord => record !== undefined)
      .sort((left, right) => this.compareOverlayRecords(left, right))
      .map((record) => this.cloneOverlayRecord(record));
  }

  getRuntimeOverlayProvenance(overlayId: EntityId): RuntimeOverlayRecord | undefined {
    const record = this.runtimeOverlays.get(overlayId);
    return record ? this.cloneOverlayRecord(record) : undefined;
  }

  diffEffectiveStateRestoration(before: EffectiveCellState, after: EffectiveCellState): EffectiveStateRestorationDiff {
    const changed = {
      f2ChipId: before.f2ChipId !== after.f2ChipId,
      semanticGroup: before.semanticGroup !== after.semanticGroup,
      canTraverse: before.canTraverse !== after.canTraverse,
      canConstruct: before.canConstruct !== after.canConstruct,
      isBlocked: before.isBlocked !== after.isBlocked,
      overrideSource: before.overrideSource !== after.overrideSource,
    };

    return {
      before,
      after,
      restored:
        !changed.f2ChipId &&
        !changed.semanticGroup &&
        !changed.canTraverse &&
        !changed.canConstruct &&
        !changed.isBlocked &&
        !changed.overrideSource,
      changed,
    };
  }

  getAnnotationCellState(x: number, y: number): AnnotationCellState {
    if (!this.isInBounds(x, y)) {
      return DEFAULT_ANNOTATION_STATE;
    }

    const cell = this.getCell(x, y);
    return {
      f2ChipId: cell.f2ChipId,
      semanticGroup: cell.semanticGroup ?? "unknown",
      canTraverse: cell.canTraverse ?? false,
      canConstruct: cell.canConstruct ?? false,
      isBlocked: cell.isBlocked ?? true,
    };
  }

  getEffectiveCellState(x: number, y: number): EffectiveCellState {
    if (!this.isInBounds(x, y)) {
      return DEFAULT_EFFECTIVE_STATE;
    }

    const cacheKey = this.toCellCacheKey(x, y);
    const cached = this.effectiveStateCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    const topOverlay = this.resolveTopOverlayAtCell(x, y);
    if (topOverlay) {
      const fromOverlay: EffectiveCellState = {
        f2ChipId: topOverlay.chipId,
        semanticGroup: getF2SemanticGroup(topOverlay.chipId),
        canTraverse: canTraverseF2(topOverlay.chipId),
        canConstruct: canConstructOnF2(topOverlay.chipId),
        isBlocked: isBlockedF2(topOverlay.chipId),
        overrideSource: "runtime-overlay",
      };
      this.effectiveStateCache.set(cacheKey, fromOverlay);
      return fromOverlay;
    }

    const top = this.getTopEntityAt(x, y);
    if (top) {
      const fromTopEntity: EffectiveCellState = {
        f2ChipId: top.chipId,
        semanticGroup: getF2SemanticGroup(top.chipId),
        canTraverse: canTraverseF2(top.chipId),
        canConstruct: canConstructOnF2(top.chipId),
        isBlocked: isBlockedF2(top.chipId),
        overrideSource: "entity-top",
      };
      this.effectiveStateCache.set(cacheKey, fromTopEntity);
      return fromTopEntity;
    }

    const annotation = this.getAnnotationCellState(x, y);
    if (annotation.f2ChipId !== undefined) {
      const fromAnnotation: EffectiveCellState = {
        ...annotation,
        overrideSource: "annotation",
      };
      this.effectiveStateCache.set(cacheKey, fromAnnotation);
      return fromAnnotation;
    }

    this.effectiveStateCache.set(cacheKey, EMPTY_SYNTHETIC_EFFECTIVE_STATE);
    return EMPTY_SYNTHETIC_EFFECTIVE_STATE;
  }

  getEffectiveLegality(x: number, y: number): CellLegality {
    const state = this.getEffectiveCellState(x, y);
    return {
      f2ChipId: state.f2ChipId,
      semanticGroup: state.semanticGroup,
      canTraverse: state.canTraverse,
      canConstruct: state.canConstruct,
      isBlocked: state.isBlocked,
    };
  }

  getEffectiveF2ChipId(x: number, y: number): number | undefined {
    return this.getEffectiveCellState(x, y).f2ChipId;
  }

  getEffectiveSemanticGroup(x: number, y: number): F2SemanticGroup {
    return this.getEffectiveCellState(x, y).semanticGroup;
  }

  compareAnnotationAndEffectiveCellState(x: number, y: number): AnnotationEffectiveCellDiff {
    const annotation = this.getAnnotationCellState(x, y);
    const effective = this.getEffectiveCellState(x, y);
    const changed = {
      f2ChipId: annotation.f2ChipId !== effective.f2ChipId,
      semanticGroup: annotation.semanticGroup !== effective.semanticGroup,
      canTraverse: annotation.canTraverse !== effective.canTraverse,
      canConstruct: annotation.canConstruct !== effective.canConstruct,
      isBlocked: annotation.isBlocked !== effective.isBlocked,
    };

    return {
      x,
      y,
      annotation,
      effective,
      differs: changed.f2ChipId || changed.semanticGroup || changed.canTraverse || changed.canConstruct || changed.isBlocked,
      changed,
    };
  }

  // Backward-compatible alias used by existing systems.
  getCellLegality(x: number, y: number): CellLegality {
    return this.getEffectiveLegality(x, y);
  }

  canTraverseCell(x: number, y: number): boolean {
    return this.getEffectiveCellState(x, y).canTraverse;
  }

  canConstructCell(x: number, y: number): boolean {
    return this.getEffectiveCellState(x, y).canConstruct;
  }

  isBlockedCell(x: number, y: number): boolean {
    return this.getEffectiveCellState(x, y).isBlocked;
  }

  getCellSemanticGroup(x: number, y: number): F2SemanticGroup {
    return this.getEffectiveCellState(x, y).semanticGroup;
  }

  getCellF2ChipId(x: number, y: number): number | undefined {
    return this.getEffectiveCellState(x, y).f2ChipId;
  }

  getEntitiesAt(x: number, y: number): WorldEntity[] {
    return this.getCell(x, y).entityIds.map((entityId) => {
      const entity = this.entities.get(entityId);
      if (!entity) {
        throw new Error(`Cell ${x},${y} references missing entity ${entityId}`);
      }
      return entity;
    });
  }

  getTopEntityAt(x: number, y: number): WorldEntity | undefined {
    const entities = this.getEntitiesAt(x, y);
    if (entities.length === 0) {
      return undefined;
    }

    return entities.reduce((top, entity) => {
      if (!top) {
        return entity;
      }

      return entity.components.render.stackOrder > top.components.render.stackOrder ? entity : top;
    }, undefined as WorldEntity | undefined);
  }

  exportSnapshot(): SimWorldSnapshot {
    const entities = [...this.entities.values()]
      .sort((left, right) => left.id - right.id)
      .map((entity) => deepCloneEntity(entity));

    return {
      width: this.width,
      height: this.height,
      terrainKinds: this.cells.map((row) => row.map((cell) => cell.terrainKind)),
      cellTownAreaIds: this.cells.map((row) => row.map((cell) => cell.townAreaId ?? null)),
      nextTownAreaId: this.nextTownAreaId,
      townAreas: [...this.townAreas.values()]
        .sort((left, right) => left.id - right.id)
        .map((townArea) => ({
          ...townArea,
          rect: { ...townArea.rect },
        })),
      nextEntityId: this.nextEntityId,
      entities,
    };
  }

  static fromSnapshot(snapshot: SimWorldSnapshot): SimWorld {
    const world = new SimWorld(snapshot.width, snapshot.height);
    world.nextEntityId = snapshot.nextEntityId;
    world.nextTownAreaId = snapshot.nextTownAreaId;

    for (const townArea of snapshot.townAreas) {
      world.townAreas.set(townArea.id, {
        ...townArea,
        rect: { ...townArea.rect },
      });
    }

    for (let y = 0; y < snapshot.terrainKinds.length; y++) {
      for (let x = 0; x < snapshot.terrainKinds[y].length; x++) {
        world.setCellTerrainKind(x, y, snapshot.terrainKinds[y][x]);
      }
    }

    for (let y = 0; y < snapshot.cellTownAreaIds.length; y++) {
      for (let x = 0; x < snapshot.cellTownAreaIds[y].length; x++) {
        const townAreaId = snapshot.cellTownAreaIds[y][x];
        world.getCell(x, y).townAreaId = townAreaId ?? undefined;
      }
    }

    for (const snapshotEntity of snapshot.entities) {
      const entity = deepCloneEntity(snapshotEntity);
      world.entities.set(entity.id, entity);
      world.getCell(entity.cell.x, entity.cell.y).entityIds.push(entity.id);
    }

    return world;
  }

  private resolveTopOverlayAtCell(x: number, y: number): RuntimeOverlayRecord | undefined {
    const key = this.toCellCacheKey(x, y);
    const ids = this.runtimeOverlayIdsByCell.get(key);
    if (!ids || ids.length === 0) {
      return undefined;
    }

    for (let index = ids.length - 1; index >= 0; index--) {
      const record = this.runtimeOverlays.get(ids[index]);
      if (record) {
        return record;
      }
    }

    return undefined;
  }

  private unregisterRuntimeOverlayMetadata(overlayId: EntityId): RuntimeOverlayRecord | undefined {
    const record = this.runtimeOverlays.get(overlayId);
    if (!record) {
      return undefined;
    }

    this.runtimeOverlays.delete(overlayId);
    for (const cell of record.affectedCells) {
      const key = this.toCellCacheKey(cell.x, cell.y);
      const ids = this.runtimeOverlayIdsByCell.get(key) ?? [];
      const filtered = ids.filter((id) => id !== overlayId);
      if (filtered.length === 0) {
        this.runtimeOverlayIdsByCell.delete(key);
      } else {
        this.runtimeOverlayIdsByCell.set(key, filtered);
      }
      this.rebuildEffectiveState(cell.x, cell.y);
    }

    return record;
  }

  private markTownAreaCells(townArea: TownArea): void {
    const startX = Math.max(0, townArea.rect.x);
    const startY = Math.max(0, townArea.rect.y);
    const endX = Math.min(this.width, townArea.rect.x + townArea.rect.width);
    const endY = Math.min(this.height, townArea.rect.y + townArea.rect.height);

    for (let y = startY; y < endY; y++) {
      for (let x = startX; x < endX; x++) {
        this.getCell(x, y).townAreaId = townArea.id;
      }
    }
  }

  private clearTownAreaCells(townArea: TownArea): void {
    const startX = Math.max(0, townArea.rect.x);
    const startY = Math.max(0, townArea.rect.y);
    const endX = Math.min(this.width, townArea.rect.x + townArea.rect.width);
    const endY = Math.min(this.height, townArea.rect.y + townArea.rect.height);

    for (let y = startY; y < endY; y++) {
      for (let x = startX; x < endX; x++) {
        const cell = this.getCell(x, y);
        if (cell.townAreaId === townArea.id) {
          cell.townAreaId = undefined;
        }
      }
    }
  }

  private removeTownAreasByOriginEntity(originEntityId: EntityId): void {
    for (const townArea of [...this.townAreas.values()]) {
      if (townArea.originEntityId === originEntityId) {
        this.clearTownAreaCells(townArea);
        this.townAreas.delete(townArea.id);
      }
    }
  }

  private collectCoveredCells(x: number, y: number, width: number, height: number): CellPoint[] {
    const cells: CellPoint[] = [];
    const startX = Math.max(0, x);
    const startY = Math.max(0, y);
    const endX = Math.min(this.width, x + width);
    const endY = Math.min(this.height, y + height);

    for (let yy = startY; yy < endY; yy++) {
      for (let xx = startX; xx < endX; xx++) {
        cells.push({ x: xx, y: yy });
      }
    }

    return cells;
  }

  private compareOverlayRecords(left: RuntimeOverlayRecord, right: RuntimeOverlayRecord): number {
    if (left.priority !== right.priority) {
      return left.priority - right.priority;
    }

    if (left.order !== right.order) {
      return left.order - right.order;
    }

    return left.overlayId - right.overlayId;
  }

  private compareOverlayIds(leftId: EntityId, rightId: EntityId): number {
    const left = this.runtimeOverlays.get(leftId);
    const right = this.runtimeOverlays.get(rightId);

    if (!left || !right) {
      return leftId - rightId;
    }

    return this.compareOverlayRecords(left, right);
  }

  private cloneOverlayRecord(record: RuntimeOverlayRecord): RuntimeOverlayRecord {
    return {
      ...record,
      affectedCells: record.affectedCells.map((cell) => ({ ...cell })),
    };
  }

  private toCellCacheKey(x: number, y: number): string {
    return `${x},${y}`;
  }

  private clearEffectiveStateCacheAt(x: number, y: number): void {
    if (!this.isInBounds(x, y)) {
      return;
    }
    this.effectiveStateCache.delete(this.toCellCacheKey(x, y));
  }

  private isInBounds(x: number, y: number): boolean {
    return x >= 0 && y >= 0 && x < this.width && y < this.height;
  }
}

function deepCloneEntity(entity: WorldEntity): WorldEntity {
  return {
    ...entity,
    cell: { ...entity.cell },
    childIds: [...entity.childIds],
    components: {
      ...entity.components,
      mapChip: { ...entity.components.mapChip },
      mapChipRect: { ...entity.components.mapChipRect },
      render: { ...entity.components.render },
      facility: entity.components.facility ? { ...entity.components.facility } : undefined,
      fence: entity.components.fence ? { ...entity.components.fence } : undefined,
    },
  };
}
