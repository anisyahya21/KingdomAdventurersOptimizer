export type EntityId = number;
export type ChipId = number;

export type CombinationKind = 0 | 1 | 2;
export type TerrainKind = "soil" | "water";
export type PlacementTerrainRule = "any" | "soil_only";
export type F2SemanticGroup =
  | "terrain/ground/water"
  | "road/path/traffic/buildable-related"
  | "special overlay/runtime-marker"
  | "unknown";

export interface CellPoint {
  x: number;
  y: number;
}

export interface CellRect extends CellPoint {
  width: number;
  height: number;
}

export interface MapChipData {
  id: ChipId;
  type: number;
  category: number;
  name: string;
  res: number;
  img: number;
  seb: number;
  layer: number;
  rotation: number;
  relatedDataType: number;
  relatedDataId: number;
  sizeWidth: number;
  sizeHeight: number;
  flags: number;
  placementTerrainRule?: PlacementTerrainRule;
}

export interface FacilityData {
  id: number;
  name: string;
  dataId: number;
  combination: CombinationKind;
  parentChipId: ChipId;
  childChipIds: ChipId[];
}

export interface MapCell {
  x: number;
  y: number;
  terrainKind: TerrainKind;
  f2ChipId?: number;
  semanticGroup?: F2SemanticGroup;
  isTerrainLike?: boolean;
  isRoadLike?: boolean;
  isSpecialOverlay?: boolean;
  canTraverse?: boolean;
  canConstruct?: boolean;
  isBlocked?: boolean;
  townAreaId?: number;
  entityIds: EntityId[];
}

export interface TownArea {
  id: number;
  originEntityId: EntityId;
  rect: CellRect;
}

export interface MapChipComponent {
  chipId: ChipId;
}

export interface MapChipRectComponent extends CellRect {}

export interface FacilityComponent {
  facilityId: number;
  parentFacilityEntityId?: EntityId;
}

export interface RenderComponent {
  stackOrder: number;
}

export interface FenceComponent {
  kind: "townhall";
  mask: number;
  frameKey: string;
}

export interface EntityComponents {
  mapChip: MapChipComponent;
  mapChipRect: MapChipRectComponent;
  facility?: FacilityComponent;
  fence?: FenceComponent;
  render: RenderComponent;
}

export interface WorldEntity {
  id: EntityId;
  chipId: ChipId;
  cell: CellPoint;
  parentId?: EntityId;
  childIds: EntityId[];
  components: EntityComponents;
  alive: boolean;
}

export interface WorldEntitySnapshot {
  id: EntityId;
  chipId: ChipId;
  cell: CellPoint;
  parentId?: EntityId;
  childIds: EntityId[];
  components: EntityComponents;
  alive: boolean;
}

export interface SimWorldSnapshot {
  width: number;
  height: number;
  terrainKinds: TerrainKind[][];
  cellTownAreaIds: Array<Array<number | null>>;
  nextTownAreaId: number;
  townAreas: TownArea[];
  nextEntityId: number;
  entities: WorldEntitySnapshot[];
}

export interface RawMapCellFields {
  f0: number;
  f1: number;
  f2: number;
  f3: number;
  f4: number;
  f5: number;
}

export interface ParsedMapCell extends CellPoint {
  fields: RawMapCellFields;
}

export interface ParsedMapBinary {
  width: number;
  height: number;
  cells: ParsedMapCell[];
  remainingBytes: number;
}

export interface Renderable {
  entityId: EntityId;
  chipId: ChipId;
  name: string;
  cell: CellPoint;
  layer: number;
  stackOrder: number;
  z: number;
  frameKey: string;
}

export interface PlaceChipCommand {
  chipId: ChipId;
  x: number;
  y: number;
  parentEntityId?: EntityId;
  source?: "user" | "system" | "load";
  skipSystems?: ReadonlySet<string>;
}

export type PlacementValidationReasonCode =
  | "UNKNOWN_CHIP"
  | "OUT_OF_BOUNDS"
  | "CELL_BLOCKED"
  | "TERRAIN_BLOCKED"
  | "ROAD_ONLY"
  | "NON_BUILDABLE"
  | "BLOCKED_BY_RUNTIME_MASK";

export interface PlacementValidationReason {
  code: PlacementValidationReasonCode;
  message: string;
  x?: number;
  y?: number;
  blockingEntityIds?: EntityId[];
}

export interface PlacementValidationResult {
  ok: boolean;
  occupiedCells: CellPoint[];
  reasons: PlacementValidationReason[];
}

export interface PlacementContext {
  catalog: RuntimeCatalog;
  world: SimWorldApi;
  command: PlaceChipCommand;
  placedEntity: WorldEntity;
  commandBuffer: CommandBufferApi;
}

export interface RemovalContext {
  catalog: RuntimeCatalog;
  world: SimWorldApi;
  removedEntity: WorldEntity;
}

export interface PlacementSystem {
  readonly name: string;
  afterPlace?(context: PlacementContext): void;
  afterRemove?(context: RemovalContext): void;
}

export interface RuntimeCatalog {
  getMapChip(chipId: ChipId): MapChipData;
  getFacilityForChip(chipId: ChipId): FacilityData | undefined;
}

export interface CellLegality {
  f2ChipId?: number;
  semanticGroup: F2SemanticGroup;
  canTraverse: boolean;
  canConstruct: boolean;
  isBlocked: boolean;
}

export type EffectiveOverrideSource = "none" | "annotation" | "entity-top" | "runtime-overlay";

export interface AnnotationCellState {
  f2ChipId?: number;
  semanticGroup: F2SemanticGroup;
  canTraverse: boolean;
  canConstruct: boolean;
  isBlocked: boolean;
}

export interface EffectiveCellState {
  f2ChipId?: number;
  semanticGroup: F2SemanticGroup;
  canTraverse: boolean;
  canConstruct: boolean;
  isBlocked: boolean;
  overrideSource: EffectiveOverrideSource;
}

export interface AnnotationEffectiveCellDiff {
  x: number;
  y: number;
  annotation: AnnotationCellState;
  effective: EffectiveCellState;
  differs: boolean;
  changed: {
    f2ChipId: boolean;
    semanticGroup: boolean;
    canTraverse: boolean;
    canConstruct: boolean;
    isBlocked: boolean;
  };
}

export interface RuntimeOverlayApplyInput {
  chipId: ChipId;
  x: number;
  y: number;
  width?: number;
  height?: number;
  sourceEntityId?: EntityId;
  sourceFacilityId?: number;
  priority?: number;
  semanticOverrideBehavior?: "full-replace";
}

export interface RuntimeOverlayRecord {
  overlayId: EntityId;
  chipId: ChipId;
  sourceEntityId?: EntityId;
  sourceFacilityId?: number;
  priority: number;
  order: number;
  affectedCells: CellPoint[];
  semanticOverrideBehavior: "full-replace";
}

export interface EffectiveStateRestorationDiff {
  before: EffectiveCellState;
  after: EffectiveCellState;
  restored: boolean;
  changed: {
    f2ChipId: boolean;
    semanticGroup: boolean;
    canTraverse: boolean;
    canConstruct: boolean;
    isBlocked: boolean;
    overrideSource: boolean;
  };
}

export interface SimWorldApi {
  readonly width: number;
  readonly height: number;
  readonly entities: ReadonlyMap<EntityId, WorldEntity>;
  readonly townAreas: ReadonlyMap<number, TownArea>;
  createEntity(command: PlaceChipCommand, stackOrder: number): WorldEntity;
  removeEntity(entityId: EntityId): WorldEntity;
  getEntity(entityId: EntityId): WorldEntity | undefined;
  createTownArea(originEntityId: EntityId, rect: CellRect): TownArea;
  getTownArea(townAreaId: number): TownArea | undefined;
  linkChild(parentId: EntityId, childId: EntityId): void;
  getCell(x: number, y: number): MapCell;
  setCellTerrainKind(x: number, y: number, terrainKind: TerrainKind): void;
  applyRuntimeOverlay(input: RuntimeOverlayApplyInput): RuntimeOverlayRecord;
  removeRuntimeOverlay(overlayId: EntityId): RuntimeOverlayRecord;
  clearRuntimeOverlaysAtCell(x: number, y: number): RuntimeOverlayRecord[];
  rebuildEffectiveState(x?: number, y?: number): void;
  getRuntimeOverlayStackAtCell(x: number, y: number): RuntimeOverlayRecord[];
  getRuntimeOverlayProvenance(overlayId: EntityId): RuntimeOverlayRecord | undefined;
  diffEffectiveStateRestoration(before: EffectiveCellState, after: EffectiveCellState): EffectiveStateRestorationDiff;
  getAnnotationCellState(x: number, y: number): AnnotationCellState;
  getEffectiveCellState(x: number, y: number): EffectiveCellState;
  getEffectiveLegality(x: number, y: number): CellLegality;
  getEffectiveF2ChipId(x: number, y: number): number | undefined;
  getEffectiveSemanticGroup(x: number, y: number): F2SemanticGroup;
  compareAnnotationAndEffectiveCellState(x: number, y: number): AnnotationEffectiveCellDiff;
  getCellLegality(x: number, y: number): CellLegality;
  canTraverseCell(x: number, y: number): boolean;
  canConstructCell(x: number, y: number): boolean;
  isBlockedCell(x: number, y: number): boolean;
  getCellSemanticGroup(x: number, y: number): F2SemanticGroup;
  getCellF2ChipId(x: number, y: number): number | undefined;
  getEntitiesAt(x: number, y: number): WorldEntity[];
  getTopEntityAt(x: number, y: number): WorldEntity | undefined;
}

export interface CommandBufferApi {
  placeChip(command: PlaceChipCommand): void;
}
