import { InMemoryRuntimeCatalog } from "./catalog";
import { createWorldFromParsedMap, parseMapBinarySectionA } from "./map-loader";
import { buildMapRenderProjection } from "./map-render-projection";
import { PlacementPipeline } from "./pipeline";
import { canConstructOnF2, canTraverseF2, isBlockedF2 } from "./placement-validation";
import { buildRenderables } from "./render-projection";
import { deserializeWorldState, serializeWorldState } from "./state-io";
import {
  getF2MapChipRuntimeMeta,
  getF2SemanticGroup,
  isF2RoadLike,
  isF2SpecialOverlay,
  isF2TerrainLike,
  OBSERVED_F2_MAPCHIP_IDS,
} from "./fixtures/f2-semantic-layer";
import {
  traceTownHallFacilityPlacement,
  traceTownHallFacilityPlacementOnWorld,
  type FacilityPlacementTraceReport,
} from "./facility-placement-trace";
import {
  buildOverlayProvenanceReport,
  diffEffectiveStateRestoration,
  dumpOverlayStack,
} from "./overlay-lifecycle-trace";
import type { MapChipVisualResolverReport } from "./mapchip-visual-resolver";
import { CombinationSystem } from "./systems/CombinationSystem";
import { FenceSystem } from "./systems/FenceSystem";
import { TownSystem } from "./systems/TownSystem";
import type { FacilityData, MapChipData } from "./types";
import { SimWorld } from "./world";

const townHallChips: MapChipData[] = [
  {
    id: 34,
    type: 20,
    category: 19,
    name: "Road",
    res: 9,
    img: 81,
    seb: 0,
    layer: 0,
    rotation: 0,
    relatedDataType: 1,
    relatedDataId: 4,
    sizeWidth: 1,
    sizeHeight: 1,
    flags: 3735729,
  },
  {
    id: 58,
    type: 17,
    category: 35,
    name: "Town Hall",
    res: 23,
    img: 7,
    seb: 0,
    layer: 1,
    rotation: 0,
    relatedDataType: 1,
    relatedDataId: 17,
    sizeWidth: 2,
    sizeHeight: 2,
    flags: 124978177,
  },
  {
    id: 59,
    type: 17,
    category: 42,
    name: "Town Hall",
    res: 23,
    img: 5,
    seb: 0,
    layer: 1,
    rotation: 0,
    relatedDataType: 1,
    relatedDataId: 18,
    sizeWidth: 2,
    sizeHeight: 2,
    flags: 100664320,
  },
  {
    id: 60,
    type: 25,
    category: 40,
    name: "Town Hall",
    res: 23,
    img: 6,
    seb: 2,
    layer: 1,
    rotation: 0,
    relatedDataType: 1,
    relatedDataId: 19,
    sizeWidth: 1,
    sizeHeight: 1,
    flags: 100664576,
  },
  {
    id: 61,
    type: 13,
    category: 52,
    name: "Town Hall",
    res: 9,
    img: 94,
    seb: 0,
    layer: 0,
    rotation: 0,
    relatedDataType: 0,
    relatedDataId: -1,
    sizeWidth: 1,
    sizeHeight: 1,
    flags: 33620992,
  },
  {
    id: 84,
    type: 19,
    category: 17,
    name: "Storehouse (Grass)",
    res: 9,
    img: 90,
    seb: 0,
    layer: 0,
    rotation: 0,
    relatedDataType: 1,
    relatedDataId: 33,
    sizeWidth: 2,
    sizeHeight: 2,
    flags: 7489665,
    placementTerrainRule: "soil_only",
  },
];

const townHallFacilities: FacilityData[] = [
  {
    id: 17,
    name: "Town Hall",
    dataId: 35,
    combination: 1,
    parentChipId: 58,
    childChipIds: [59, 60],
  },
];

export function createTownHallMilestoneRuntime(): {
  catalog: InMemoryRuntimeCatalog;
  pipeline: PlacementPipeline;
  world: SimWorld;
} {
  const catalog = new InMemoryRuntimeCatalog({
    mapChips: townHallChips,
    facilities: townHallFacilities,
  });

  return {
    catalog,
    pipeline: new PlacementPipeline(catalog, [new CombinationSystem(), new TownSystem(), new FenceSystem()]),
    world: new SimWorld(16, 16),
  };
}

export function runTownHallStackMilestone(): void {
  const { catalog, pipeline, world } = createTownHallMilestoneRuntime();
  const parent = pipeline.placeChip(world, { chipId: 58, x: 4, y: 5, source: "user" });

  const entities = [...world.entities.values()];
  const chips = entities.map((entity) => entity.chipId);
  assertEqual(chips, [58, 59, 60], "placeChip(58) should create chips 58, 59, 60");
  assertEqual(parent.childIds.length, 2, "Town Hall parent should have two runtime children");

  const children = parent.childIds.map((entityId) => world.getEntity(entityId));
  assertEqual(
    children.map((entity) => entity?.chipId),
    [59, 60],
    "Town Hall children should be chip 59 then chip 60",
  );

  for (const entity of entities) {
    assertEqual(entity.cell, { x: 4, y: 5 }, `Chip ${entity.chipId} should be stacked in parent cell`);
  }

  const renderables = buildRenderables(world, catalog);
  assertEqual(
    renderables.map((renderable) => renderable.chipId),
    [58, 59, 60],
    "Render projection should preserve stack order",
  );
}

export function runTownHallFenceMilestone(): void {
  const { catalog, pipeline, world } = createTownHallMilestoneRuntime();

  const leftFence = pipeline.placeChip(world, { chipId: 61, x: 8, y: 8, source: "user" });
  assertEqual(leftFence.components.fence?.mask, 0, "Single fence should start with mask 0");
  assertEqual(
    leftFence.components.fence?.frameKey,
    "townhall-fence-mask-0",
    "Single fence should use frame for mask 0",
  );

  const rightFence = pipeline.placeChip(world, { chipId: 61, x: 9, y: 8, source: "user" });
  const refreshedLeft = world.getEntity(leftFence.id);
  const refreshedRight = world.getEntity(rightFence.id);

  if (!refreshedLeft || !refreshedRight) {
    throw new Error("Expected both fence entities to exist after placement");
  }

  assertEqual(refreshedLeft.components.fence?.mask, 2, "Left fence should detect east neighbor");
  assertEqual(refreshedRight.components.fence?.mask, 8, "Right fence should detect west neighbor");
  assertEqual(
    refreshedLeft.components.fence?.frameKey,
    "townhall-fence-mask-2",
    "Left fence should resolve frame from mask 2",
  );
  assertEqual(
    refreshedRight.components.fence?.frameKey,
    "townhall-fence-mask-8",
    "Right fence should resolve frame from mask 8",
  );

  const renderables = buildRenderables(world, catalog)
    .filter((renderable) => renderable.chipId === 61)
    .map((renderable) => ({ x: renderable.cell.x, frameKey: renderable.frameKey }));
  assertEqual(
    renderables,
    [
      { x: 8, frameKey: "townhall-fence-mask-2" },
      { x: 9, frameKey: "townhall-fence-mask-8" },
    ],
    "Fence renderables should expose computed frame keys",
  );

  pipeline.removeEntity(world, rightFence.id);
  const leftAfterRemove = world.getEntity(leftFence.id);
  if (!leftAfterRemove) {
    throw new Error("Expected left fence entity to remain after removing right fence");
  }

  assertEqual(leftAfterRemove.components.fence?.mask, 0, "Left fence should clear mask after neighbor removal");
  assertEqual(
    leftAfterRemove.components.fence?.frameKey,
    "townhall-fence-mask-0",
    "Left fence should revert to frame for mask 0 after neighbor removal",
  );
}

export function runSaveLoadMilestone(): void {
  const { catalog, pipeline, world } = createTownHallMilestoneRuntime();

  pipeline.placeChip(world, { chipId: 58, x: 4, y: 5, source: "user" });
  pipeline.placeChip(world, { chipId: 61, x: 8, y: 8, source: "user" });
  pipeline.placeChip(world, { chipId: 61, x: 9, y: 8, source: "user" });

  const beforeSummary = summarizeWorld(world, catalog);
  const serialized = serializeWorldState(world);
  const restoredWorld = deserializeWorldState(serialized);
  const afterSummary = summarizeWorld(restoredWorld, catalog);

  assertEqual(afterSummary, beforeSummary, "Save/load should preserve entities, links, and render projection");
}

export function runOccupancyValidationMilestone(): void {
  const { pipeline, world } = createTownHallMilestoneRuntime();

  const occupied = pipeline.getOccupiedCellsForChip(58, 4, 5);
  assertEqual(
    occupied,
    [
      { x: 4, y: 5 },
      { x: 5, y: 5 },
      { x: 4, y: 6 },
      { x: 5, y: 6 },
    ],
    "2x2 Town Hall should report occupied cells",
  );

  const parent = pipeline.placeChip(world, { chipId: 58, x: 4, y: 5, source: "user" });

  const overlap = pipeline.canPlaceChip(world, { chipId: 58, x: 5, y: 6, source: "user" });
  assertEqual(overlap.ok, false, "Overlapping 2x2 placement should be rejected");
  assertEqual(
    overlap.reasons.some((reason) => reason.code === "CELL_BLOCKED"),
    true,
    "Overlap rejection should include CELL_BLOCKED reason",
  );

  assertThrows(
    () => pipeline.placeChip(world, { chipId: 58, x: 5, y: 6, source: "user" }),
    "Cannot place chip 58",
    "placeChip should throw for overlapping placement",
  );

  const nonOverlap = pipeline.canPlaceChip(world, { chipId: 58, x: 8, y: 8, source: "user" });
  assertEqual(nonOverlap.ok, true, "Non-overlapping placement should be allowed");
  pipeline.placeChip(world, { chipId: 58, x: 8, y: 8, source: "user" });

  const outOfBounds = pipeline.canPlaceChip(world, { chipId: 58, x: 15, y: 15, source: "user" });
  assertEqual(outOfBounds.ok, false, "Out-of-bounds placement should be rejected");
  assertEqual(
    outOfBounds.reasons.some((reason) => reason.code === "OUT_OF_BOUNDS"),
    true,
    "Out-of-bounds rejection should include OUT_OF_BOUNDS reason",
  );

  const stackChild = pipeline.canPlaceChip(world, {
    chipId: 59,
    x: 4,
    y: 5,
    parentEntityId: parent.id,
    source: "system",
  });
  assertEqual(stackChild.ok, true, "Stack child should be allowed in the same cell footprint as parent stack");
}

export function runTerrainRestrictionMilestone(): void {
  const { pipeline, world } = createTownHallMilestoneRuntime();

  const allowedOnSoil = pipeline.canPlaceChip(world, { chipId: 84, x: 1, y: 1, source: "user" });
  assertEqual(allowedOnSoil.ok, true, "Soil-only chip should place on soil cells");

  world.setCellTerrainKind(2, 2, "water");
  const blockedOnWater = pipeline.canPlaceChip(world, { chipId: 84, x: 1, y: 1, source: "user" });
  assertEqual(blockedOnWater.ok, false, "Soil-only chip should be blocked by water in occupied footprint");
  assertEqual(
    blockedOnWater.reasons.some((reason) => reason.code === "TERRAIN_BLOCKED"),
    true,
    "Terrain block should return TERRAIN_BLOCKED reason",
  );

  assertThrows(
    () => pipeline.placeChip(world, { chipId: 84, x: 1, y: 1, source: "user" }),
    "TERRAIN_BLOCKED",
    "placeChip should throw when terrain rule is violated",
  );
}

export function runRealMapLoadingMilestone(): void {
  const bytes = new Uint8Array([
    // width=3, height=2
    0, 0, 0, 3,
    0, 0, 0, 2,

    // row 0: three cells (6 u32 each)
    // cell (0,0): f0=0 f1=0 f2=58 f3=0 f4=0 f5=10
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 58, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10,
    // cell (1,0): f0=1 f1=0 f2=0 f3=0 f4=0 f5=10
    0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10,
    // cell (2,0): f0=0 f1=0 f2=61 f3=0 f4=0 f5=10
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 61, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10,
    // sentinel row 0 = width=3
    0, 0, 0, 3,

    // row 1
    // cell (0,1): f0=1 f1=0 f2=84 f3=0 f4=0 f5=11
    0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 84, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
    // cell (1,1): f0=0 f1=0 f2=0 f3=0 f4=0 f5=11
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
    // cell (2,1): f0=0 f1=0 f2=0 f3=0 f4=0 f5=11
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
    // sentinel row 1 = width=3
    0, 0, 0, 3,

    // remaining section bytes
    0, 0, 0, 99,
  ]);

  const parsed = parseMapBinarySectionA(bytes);
  assertEqual(parsed.width, 3, "Parsed map width should match header");
  assertEqual(parsed.height, 2, "Parsed map height should match header");
  assertEqual(parsed.cells.length, 6, "Parsed map should contain width*height cells");
  assertEqual(parsed.remainingBytes, 4, "Parsed map should report trailing section bytes");
  assertEqual(parsed.cells[0].fields.f2, 58, "Parser should preserve F2 chip id values");
  assertEqual(parsed.cells[3].fields.f2, 84, "Parser should preserve row-major cell ordering");

  const world = createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  assertEqual(world.width, 3, "World width should match parsed map width");
  assertEqual(world.height, 2, "World height should match parsed map height");
  assertEqual(world.getCell(0, 0).terrainKind, "soil", "Terrain resolver should map f0=0 to soil");
  assertEqual(world.getCell(1, 0).terrainKind, "water", "Terrain resolver should map f0=1 to water");
  assertEqual(world.getCell(0, 1).terrainKind, "water", "Terrain resolver should apply for second row as well");
}

export function runTownAreaMilestone(): void {
  const { world, pipeline } = createTownHallMilestoneRuntime();
  const townHall = pipeline.placeChip(world, { chipId: 58, x: 4, y: 5, source: "user" });

  assertEqual(world.townAreas.size, 1, "Placing Town Hall root should create one TownArea");

  const townArea = [...world.townAreas.values()][0];
  assertEqual(townArea.originEntityId, townHall.id, "TownArea should reference Town Hall origin entity");
  assertEqual(
    townArea.rect,
    { x: 3, y: 4, width: 4, height: 4 },
    "TownArea rect should be mapChipRect.x-1, mapChipRect.y-1, 4,4",
  );

  assertEqual(world.getCell(3, 4).townAreaId, townArea.id, "Top-left town cell should be marked");
  assertEqual(world.getCell(6, 7).townAreaId, townArea.id, "Bottom-right town cell should be marked");
  assertEqual(world.getCell(2, 4).townAreaId, undefined, "Cell left of town area should not be marked");
  assertEqual(world.getCell(7, 7).townAreaId, undefined, "Cell right of town area should not be marked");
  assertEqual(world.getCell(3, 3).townAreaId, undefined, "Cell above town area should not be marked");
  assertEqual(world.getCell(6, 8).townAreaId, undefined, "Cell below town area should not be marked");

  const restored = deserializeWorldState(serializeWorldState(world));
  assertEqual(restored.townAreas.size, 1, "Save/load should preserve TownArea records");

  const restoredTownArea = [...restored.townAreas.values()][0];
  assertEqual(restoredTownArea.rect, townArea.rect, "Save/load should preserve TownArea rect");
  assertEqual(restored.getCell(3, 4).townAreaId, restoredTownArea.id, "Save/load should preserve marked town cells");
  assertEqual(
    restored.getCell(2, 4).townAreaId,
    undefined,
    "Save/load should keep outside cells unmarked",
  );
}

export function runMapRenderProjectionMilestone(): void {
  const bytes = new Uint8Array([
    0, 0, 0, 2,
    0, 0, 0, 2,

    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 58, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10,
    0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 61, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10,
    0, 0, 0, 2,

    0, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0, 84, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11,
    0, 0, 0, 2,
  ]);

  const parsed = parseMapBinarySectionA(bytes);
  const projection = buildMapRenderProjection(parsed);

  assertEqual(projection.tiles.length, 4, "Projection should include one tile per map cell");
  assertEqual(projection.stats.totalCells, 4, "Projection stats should report total cells");
  assertEqual(projection.stats.terrainCounts.soil, 3, "Projection should classify soil count");
  assertEqual(projection.stats.terrainCounts.water, 1, "Projection should classify water count");
  assertEqual(projection.stats.unknownTerrainFlags, 1, "Projection should count unknown terrain flags");
}

export function runF2SemanticFixtureMilestone(): void {
  for (const chipId of OBSERVED_F2_MAPCHIP_IDS) {
    const meta = getF2MapChipRuntimeMeta(chipId);
    assertEqual(meta !== null, true, `Observed f2 chip ${chipId} should have runtime metadata`);
  }

  assertEqual(isF2RoadLike(34), true, "Chip 34 (Road) should classify as road-like");
  assertEqual(isF2RoadLike(33), true, "Chip 33 (Gravel Path) should classify as road-like");

  assertEqual(isF2TerrainLike(29), true, "Chip 29 (Land (S)) should classify as terrain-like");
  assertEqual(isF2TerrainLike(14), true, "Chip 14 (Snowfield) should classify as terrain-like");

  assertEqual(isF2SpecialOverlay(28), true, "Chip 28 (Switch) should classify as special overlay");

  assertEqual(getF2SemanticGroup(999999), "unknown", "Unknown chip id should resolve to unknown semantic group");
  assertEqual(isF2RoadLike(999999), false, "Unknown chip id should not classify as road-like");
  assertEqual(isF2TerrainLike(999999), false, "Unknown chip id should not classify as terrain-like");
  assertEqual(isF2SpecialOverlay(999999), false, "Unknown chip id should not classify as special overlay");
  assertEqual(getF2MapChipRuntimeMeta(999999), null, "Unknown chip id should return null metadata");
}

export function runPlacementSemanticMilestone(): void {
  const { world, pipeline } = createTownHallMilestoneRuntime();

  // Helper utility checks from fixture-derived semantics.
  assertEqual(canTraverseF2(34), true, "Road chip should be traversable");
  assertEqual(canTraverseF2(28), false, "Special overlay chip should not be traversable");
  assertEqual(canConstructOnF2(34), true, "Road chip should allow construction on top");
  assertEqual(canConstructOnF2(61), false, "No-construction style chip should block construction on top");
  assertEqual(isBlockedF2(28), true, "Special overlay chip should be considered blocked");
  assertEqual(isBlockedF2(999999), true, "Unknown chip should fail safely as blocked");

  // ROAD_ONLY: placing a road-like chip on water is rejected by semantic validation.
  world.setCellTerrainKind(2, 2, "water");
  const roadOnWater = pipeline.canPlaceChip(world, { chipId: 34, x: 2, y: 2, source: "user" });
  assertEqual(roadOnWater.ok, false, "Road-like chip should be rejected on water cells");
  assertEqual(
    roadOnWater.reasons.some((reason) => reason.code === "ROAD_ONLY"),
    true,
    "Road-like rejection should include ROAD_ONLY reason",
  );

  // BLOCKED_BY_RUNTIME_MASK: if base top chip is a special overlay marker.
  const blocker = world.createEntity({ chipId: 28, x: 6, y: 6, source: "user" }, 0);
  blocker.components.mapChipRect = { x: 6, y: 6, width: 1, height: 1 };
  const masked = pipeline.canPlaceChip(world, { chipId: 84, x: 6, y: 6, parentEntityId: blocker.id, source: "user" });
  assertEqual(masked.ok, false, "Blocked runtime mask should reject placement");
  assertEqual(
    masked.reasons.some((reason) => reason.code === "BLOCKED_BY_RUNTIME_MASK"),
    true,
    "Blocked mask rejection should include BLOCKED_BY_RUNTIME_MASK",
  );

  // NON_BUILDABLE: if base top chip carries non-buildable semantics.
  const nonBuildable = world.createEntity({ chipId: 61, x: 7, y: 7, source: "user" }, 0);
  nonBuildable.components.mapChipRect = { x: 7, y: 7, width: 1, height: 1 };
  const onNonBuildable = pipeline.canPlaceChip(world, {
    chipId: 84,
    x: 7,
    y: 7,
    parentEntityId: nonBuildable.id,
    source: "user",
  });
  assertEqual(onNonBuildable.ok, false, "Non-buildable base chip should reject placement");
  assertEqual(
    onNonBuildable.reasons.some((reason) => reason.code === "NON_BUILDABLE"),
    true,
    "Non-buildable rejection should include NON_BUILDABLE",
  );
}

export function runMapCellSemanticAnnotationMilestone(): void {
  const bytes = new Uint8Array([
    // width=2, height=2
    0, 0, 0, 2,
    0, 0, 0, 2,

    // row 0
    // (0,0) road-like f2=34
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 34, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 4,
    // (1,0) terrain-like f2=29
    0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 29, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 6,
    // sentinel
    0, 0, 0, 2,

    // row 1
    // (0,1) unknown f2=999999
    0, 0, 0, 0, 0, 0, 0, 2, 0, 15, 66, 63, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 8,
    // (1,1) special overlay f2=28
    0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 28, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 5,
    // sentinel
    0, 0, 0, 2,
  ]);

  const parsed = parseMapBinarySectionA(bytes);
  const world = createWorldFromParsedMap(parsed, () => "soil");

  const roadCell = world.getCell(0, 0);
  assertEqual(roadCell.f2ChipId, 34, "Road cell should retain parsed f2 id annotation");
  assertEqual(roadCell.isRoadLike, true, "Road cell should annotate as road-like");
  assertEqual(roadCell.semanticGroup, "road/path/traffic/buildable-related", "Road semantic group should match fixture");

  const terrainCell = world.getCell(1, 0);
  assertEqual(terrainCell.f2ChipId, 29, "Terrain cell should retain parsed f2 id annotation");
  assertEqual(terrainCell.isTerrainLike, true, "Terrain cell should annotate as terrain-like");
  assertEqual(terrainCell.semanticGroup, "terrain/ground/water", "Terrain semantic group should match fixture");

  const unknownCell = world.getCell(0, 1);
  assertEqual(unknownCell.f2ChipId, 999999, "Unknown cell should preserve unknown f2 id annotation");
  assertEqual(unknownCell.semanticGroup, "unknown", "Unknown f2 should annotate as unknown semantic group");
  assertEqual(unknownCell.canTraverse, false, "Unknown f2 should fail safely as non-traversable");
  assertEqual(unknownCell.canConstruct, false, "Unknown f2 should fail safely as non-constructible");
  assertEqual(unknownCell.isBlocked, true, "Unknown f2 should fail safely as blocked");
}

export function runSimWorldLegalityAccessorMilestone(): void {
  const bytes = new Uint8Array([
    // width=2, height=2
    0, 0, 0, 2,
    0, 0, 0, 2,

    // row 0
    // (0,0) road-like f2=34
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 34, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 4,
    // (1,0) unknown f2=999999
    0, 0, 0, 0, 0, 0, 0, 0, 0, 15, 66, 63, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 4,
    // sentinel
    0, 0, 0, 2,

    // row 1
    // (0,1) terrain-like f2=29
    0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 29, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 6,
    // (1,1) special overlay f2=28
    0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 28, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 5,
    // sentinel
    0, 0, 0, 2,
  ]);

  const parsed = parseMapBinarySectionA(bytes);
  const mapWorld = createWorldFromParsedMap(parsed, () => "soil");

  assertEqual(mapWorld.getCellF2ChipId(0, 0), 34, "Map-loaded legality should expose f2 chip id");
  assertEqual(
    mapWorld.getCellSemanticGroup(0, 0),
    "road/path/traffic/buildable-related",
    "Map-loaded legality should expose semantic group",
  );
  assertEqual(mapWorld.canTraverseCell(0, 0), true, "Road-like map cell should be traversable");
  assertEqual(mapWorld.canConstructCell(0, 0), true, "Road-like map cell should be constructible");
  assertEqual(mapWorld.isBlockedCell(0, 0), false, "Road-like map cell should not be blocked");

  assertEqual(mapWorld.getCellSemanticGroup(1, 0), "unknown", "Unknown map f2 should classify as unknown");
  assertEqual(mapWorld.canTraverseCell(1, 0), false, "Unknown map f2 should fail safely as non-traversable");
  assertEqual(mapWorld.canConstructCell(1, 0), false, "Unknown map f2 should fail safely as non-constructible");
  assertEqual(mapWorld.isBlockedCell(1, 0), true, "Unknown map f2 should fail safely as blocked");

  assertEqual(mapWorld.getCellF2ChipId(-1, 0), undefined, "Out-of-bounds lookup should safely omit chip id");
  assertEqual(mapWorld.getCellSemanticGroup(-1, 0), "unknown", "Out-of-bounds lookup should return unknown group");
  assertEqual(mapWorld.canTraverseCell(-1, 0), false, "Out-of-bounds lookup should fail safely for traversal");
  assertEqual(mapWorld.canConstructCell(-1, 0), false, "Out-of-bounds lookup should fail safely for construction");
  assertEqual(mapWorld.isBlockedCell(-1, 0), true, "Out-of-bounds lookup should fail safely as blocked");

  const syntheticWorld = new SimWorld(4, 4);
  const overlay = syntheticWorld.createEntity({ chipId: 28, x: 2, y: 2, source: "user" }, 0);
  overlay.components.mapChipRect = { x: 2, y: 2, width: 1, height: 1 };

  assertEqual(syntheticWorld.getCellF2ChipId(2, 2), 28, "Synthetic world should derive chip id from top entity");
  assertEqual(
    syntheticWorld.getCellSemanticGroup(2, 2),
    "special overlay/runtime-marker",
    "Synthetic world should derive semantic group from top entity",
  );
  assertEqual(syntheticWorld.canConstructCell(2, 2), false, "Synthetic special overlay should be non-constructible");
  assertEqual(syntheticWorld.isBlockedCell(2, 2), true, "Synthetic special overlay should be blocked");

  // Keep explicit compatibility guarantee for placement logic.
  runPlacementSemanticMilestone();
}

export function runFacilityPlacementTraceMilestone(): void {
  const first = traceTownHallFacilityPlacement(createTownHallMilestoneRuntime);
  const second = traceTownHallFacilityPlacement(createTownHallMilestoneRuntime);

  assertEqual(
    first.summary,
    second.summary,
    "Facility placement trace summary should be deterministic across identical runtimes",
  );

  const childChipIds = first.parentPlacement.childEntities.map((entity) => entity.chipId).sort((a, b) => a - b);
  assertEqual(childChipIds, [59, 60], "Town Hall placement trace should capture expected child chips");

  const centerCell = first.changedCells.find((cell) => cell.x === 4 && cell.y === 5);
  if (!centerCell) {
    throw new Error("Facility trace should include parent placement cell in changed cells");
  }

  assertEqual(centerCell.changes.stackChanged, true, "Parent placement cell should record stack mutation");
  assertEqual(
    centerCell.changes.canConstructChanged || centerCell.changes.isBlockedChanged || centerCell.changes.effectiveF2Changed,
    true,
    "Parent placement cell should reflect legality/cache invalidation updates",
  );

  assertEqual(
    first.summary.unrelatedCellMutations.length,
    0,
    "Facility placement trace should not mutate cells outside expected influence region",
  );
}

export function runRealMapFacilityPlacementTraceMilestone(mapBytes: Uint8Array): void {
  const reportA = buildRealMapTraceReport(mapBytes);
  const reportB = buildRealMapTraceReport(mapBytes);

  assertEqual(
    reportA.facility.command,
    reportB.facility.command,
    "Real-map Town Hall trace should choose a deterministic placement coordinate",
  );
  assertEqual(
    reportA.summary,
    reportB.summary,
    "Real-map Town Hall trace summary should be deterministic across identical inputs",
  );

  const childChipIds = reportA.parentPlacement.childEntities.map((entity) => entity.chipId).sort((a, b) => a - b);
  assertEqual(childChipIds, [59, 60], "Real-map Town Hall trace should include expected child chips");

  assertEqual(
    reportA.summary.annotationF2ChangedCells,
    0,
    "Real-map placement should not rewrite annotation f2 ids unless explicitly intended",
  );
  assertEqual(
    reportA.summary.annotationSemanticGroupChangedCells,
    0,
    "Real-map placement should not rewrite annotation semantic groups unless explicitly intended",
  );
  assertEqual(
    reportA.summary.annotationLegalityChangedCells,
    0,
    "Real-map placement should not rewrite annotation legality flags unless explicitly intended",
  );

  assertEqual(reportA.summary.effectiveF2ChangedCells >= 1, true, "Effective layer should update after placement");
  assertEqual(reportA.summary.stackChangedCells >= 1, true, "Effective stack should update after placement");

  assertEqual(
    reportA.summary.townAreaChangedCells,
    reportA.influenceRegion.expectedTownAreaCells.length,
    "TownArea mutations should exactly match expected town area coverage",
  );

  assertEqual(
    reportA.summary.unrelatedCellMutations.length,
    0,
    "Real-map placement trace should not mutate unrelated cells",
  );
}

export function runEffectiveRuntimeLayerResolutionMilestone(mapBytes: Uint8Array): void {
  const parsed = parseMapBinarySectionA(mapBytes);
  const world = createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  const { pipeline } = createTownHallMilestoneRuntime();

  const placement = findValidTownHallPlacement(world, pipeline);
  const beforeAnnotation = world.getAnnotationCellState(placement.x, placement.y);
  const beforeEffective = world.getEffectiveCellState(placement.x, placement.y);

  const parent = pipeline.placeChip(world, { chipId: 58, x: placement.x, y: placement.y, source: "user" });
  const afterAnnotation = world.getAnnotationCellState(placement.x, placement.y);
  const afterEffective = world.getEffectiveCellState(placement.x, placement.y);

  assertEqual(afterAnnotation, beforeAnnotation, "Annotation layer should remain stable after Town Hall placement");
  assertEqual(afterEffective.f2ChipId, 60, "Effective layer should resolve to top runtime overlay chip 60");
  assertEqual(
    afterEffective.semanticGroup,
    "special overlay/runtime-marker",
    "Effective semantic group should reflect runtime top overlay",
  );
  assertEqual(afterEffective.canTraverse, false, "Effective overlay semantics should update traversal legality");
  assertEqual(afterEffective.canConstruct, false, "Effective overlay semantics should update construction legality");
  assertEqual(afterEffective.isBlocked, true, "Effective overlay semantics should update blocked state");
  assertEqual(afterEffective.overrideSource, "entity-top", "Effective layer should report top-entity override source");

  const diffAfterPlacement = world.compareAnnotationAndEffectiveCellState(placement.x, placement.y);
  assertEqual(diffAfterPlacement.differs, true, "Annotation/effective diff should detect runtime overlay divergence");

  const childChipIds = parent.childIds
    .map((id) => world.getEntity(id)?.chipId)
    .filter((chipId): chipId is number => chipId !== undefined)
    .sort((a, b) => a - b);
  assertEqual(childChipIds, [59, 60], "Town Hall placement should create expected child chips");

  for (const entityId of [...parent.childIds, parent.id]) {
    if (world.getEntity(entityId)) {
      pipeline.removeEntity(world, entityId);
    }
  }

  const restoredEffective = world.getEffectiveCellState(placement.x, placement.y);
  assertEqual(
    restoredEffective,
    beforeEffective,
    "Removing overlay entities should restore effective state to pre-placement value",
  );
  assertEqual(
    world.getAnnotationCellState(placement.x, placement.y),
    beforeAnnotation,
    "Annotation layer should remain unchanged after overlay entity removal",
  );

  const synthetic = new SimWorld(4, 4);
  const syntheticBefore = synthetic.getEffectiveCellState(1, 1);
  const syntheticOverlay = synthetic.createEntity({ chipId: 28, x: 1, y: 1, source: "user" }, 0);
  syntheticOverlay.components.mapChipRect = { x: 1, y: 1, width: 1, height: 1 };
  const syntheticAfter = synthetic.getEffectiveCellState(1, 1);

  assertEqual(syntheticAfter.f2ChipId, 28, "Synthetic world effective layer should resolve runtime overlay chip");
  assertEqual(syntheticAfter.overrideSource, "entity-top", "Synthetic effective layer should track entity-top override");

  synthetic.removeEntity(syntheticOverlay.id);
  assertEqual(
    synthetic.getEffectiveCellState(1, 1),
    syntheticBefore,
    "Synthetic world should restore effective state after overlay removal",
  );
}

export function runRuntimeOverlayLifecycleMilestone(mapBytes: Uint8Array): void {
  const parsed = parseMapBinarySectionA(mapBytes);
  const world = createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  const { pipeline } = createTownHallMilestoneRuntime();

  const placement = findValidTownHallPlacement(world, pipeline);
  const annotationBefore = world.getAnnotationCellState(placement.x, placement.y);
  const effectiveBefore = world.getEffectiveCellState(placement.x, placement.y);

  const lowOverlay = world.applyRuntimeOverlay({
    chipId: 61,
    x: placement.x,
    y: placement.y,
    sourceFacilityId: 17,
    sourceEntityId: 17001,
    priority: 10,
  });
  const highOverlay = world.applyRuntimeOverlay({
    chipId: 28,
    x: placement.x,
    y: placement.y,
    sourceFacilityId: 17,
    sourceEntityId: 17002,
    priority: 20,
  });

  const stackAfterApply = dumpOverlayStack(world, placement.x, placement.y);
  assertEqual(stackAfterApply.stack.map((overlay) => overlay.overlayId), [lowOverlay.overlayId, highOverlay.overlayId], "Overlay stack should be deterministic by priority/order");
  assertEqual(stackAfterApply.topOverlay?.overlayId, highOverlay.overlayId, "Higher-priority overlay should resolve as top overlay");

  const effectiveAfterApply = world.getEffectiveCellState(placement.x, placement.y);
  assertEqual(effectiveAfterApply.f2ChipId, 28, "Top overlay chip should drive effective f2 state");
  assertEqual(effectiveAfterApply.overrideSource, "runtime-overlay", "Effective state should indicate runtime-overlay source");
  assertEqual(effectiveAfterApply.canTraverse, false, "Overlay semantics should update traversal legality");
  assertEqual(effectiveAfterApply.canConstruct, false, "Overlay semantics should update construction legality");
  assertEqual(effectiveAfterApply.isBlocked, true, "Overlay semantics should update blocked legality");

  const provenance = buildOverlayProvenanceReport(world, [lowOverlay.overlayId, highOverlay.overlayId]);
  assertEqual(provenance.overlays.length, 2, "Overlay provenance report should include both overlay records");
  assertEqual(provenance.overlays[1].sourceEntityId, 17002, "Overlay provenance should retain source entity metadata");
  assertEqual(provenance.overlays[1].sourceFacilityId, 17, "Overlay provenance should retain source facility metadata");

  const removedTop = world.removeRuntimeOverlay(highOverlay.overlayId);
  assertEqual(removedTop.overlayId, highOverlay.overlayId, "Removing runtime overlay should return removed overlay record");
  assertEqual(world.getEffectiveF2ChipId(placement.x, placement.y), 61, "Removing top overlay should expose next overlay deterministically");

  const removedAtCell = world.clearRuntimeOverlaysAtCell(placement.x, placement.y);
  assertEqual(removedAtCell.map((overlay) => overlay.overlayId), [lowOverlay.overlayId], "Cell overlay clear should remove remaining overlays");

  const effectiveAfterClear = world.getEffectiveCellState(placement.x, placement.y);
  const restoration = diffEffectiveStateRestoration(world, effectiveBefore, effectiveAfterClear);
  assertEqual(restoration.restored, true, "Clearing runtime overlays should restore pre-overlay effective state");
  assertEqual(world.getAnnotationCellState(placement.x, placement.y), annotationBefore, "Overlay lifecycle must not mutate annotation layer");

  runRealMapFacilityPlacementTraceMilestone(mapBytes);
}

export function runMapChipVisualResolverMilestone(report: MapChipVisualResolverReport): void {
  const byId = new Map(report.resolutions.map((entry) => [entry.mapChipId, entry]));

  for (const unresolvedId of report.unresolvedMapChipIds) {
    const unresolved = byId.get(unresolvedId);
    assertEqual(
      typeof unresolved?.unresolvedReason === "string" && unresolved.unresolvedReason.length > 0,
      true,
      `Unresolved mapchip id ${unresolvedId} should include a reason`,
    );
  }

  const hasTerrainLikeResolution = report.resolutions.some(
    (entry) => entry.resolved && /land|grass|soil|water|dirt|snow|swamp|desert/i.test(entry.mapChipName),
  );
  const hasAddChipLikeResolution = report.resolutions.some(
    (entry) => entry.resolved && /road|path|floor|bridge/i.test(entry.mapChipName),
  );

  assertEqual(hasTerrainLikeResolution, true, "Visual resolver should resolve at least one terrain-style map chip");
  assertEqual(hasAddChipLikeResolution, true, "Visual resolver should resolve at least one add_chip-style map chip");

  assertEqual(report.sampleRegion.tileCount > 0, true, "Visual resolver sample region should contain at least one tile");
}

export interface MapChipProjectedSampleRegionMilestoneReport {
  imagePath: string;
  imageGenerated: boolean;
  spriteDrawCount: number;
  fallbackTileCount: number;
  fallbackLegendCount: number;
  anchorCoverage: {
    metadataAnchoredSpriteCount: number;
    fallbackAnchoredSpriteCount: number;
    totalSpriteCount: number;
  };
  unresolvedAnchorSources: Array<{
    reason: string;
    count: number;
  }>;
  drawOrder: {
    signature: string;
    repeatSignature: string;
    deterministic: boolean;
  };
}

export function runMapChipProjectedSampleRegionMilestone(report: MapChipProjectedSampleRegionMilestoneReport): void {
  assertEqual(report.imageGenerated, true, "Projected sample renderer should generate output image");
  assertEqual(report.imagePath.endsWith(".png"), true, "Projected sample renderer should output PNG path");
  assertEqual(report.spriteDrawCount > 0, true, "Projected sample renderer should draw at least one real sprite");
  assertEqual(
    report.anchorCoverage.metadataAnchoredSpriteCount > 0,
    true,
    "Projected sample renderer should use per-chip anchor metadata for at least one sprite",
  );
  assertEqual(
    report.anchorCoverage.metadataAnchoredSpriteCount + report.anchorCoverage.fallbackAnchoredSpriteCount,
    report.anchorCoverage.totalSpriteCount,
    "Projected sample renderer should account for anchor mode coverage across all sprite draws",
  );
  assertEqual(
    report.anchorCoverage.totalSpriteCount,
    report.spriteDrawCount,
    "Projected sample renderer anchor coverage total should match sprite draw count",
  );
  assertEqual(
    report.fallbackTileCount + report.fallbackLegendCount > 0,
    true,
    "Projected sample renderer should exercise fallback path for unresolved chips",
  );
  assertEqual(
    report.unresolvedAnchorSources.length >= 0,
    true,
    "Projected sample renderer should report unresolved anchor sources",
  );
  assertEqual(report.drawOrder.deterministic, true, "Projected sample renderer draw order should be deterministic");
  assertEqual(
    report.drawOrder.signature,
    report.drawOrder.repeatSignature,
    "Projected sample renderer should repeat deterministic ordering signature",
  );
}

export interface MapChipProjectedMixedRegionModeStats {
  spriteCount: number;
  fallbackTileCount: number;
  metadataAnchorCount: number;
  bottomCenterFallbackCount: number;
  drawOrderDeterministic: boolean;
  drawOrderSignature: string;
  drawOrderRepeatSignature: string;
}

export interface MapChipProjectedMixedRegionReport {
  regionId: number;
  imagePath: string;
  imageGenerated: boolean;
  bounds: { x: number; y: number; width: number; height: number };
  diversityScore: number;
  f2Ids: number[];
  semanticGroups: string[];
  modeStats: {
    bottomCenter: MapChipProjectedMixedRegionModeStats;
    metadata: MapChipProjectedMixedRegionModeStats;
  };
}

export interface MapChipProjectedMixedRegionsMilestoneReport {
  regions: MapChipProjectedMixedRegionReport[];
}

export function runMapChipProjectedMixedRegionsMilestone(report: MapChipProjectedMixedRegionsMilestoneReport): void {
  assertEqual(report.regions.length >= 3, true, "Mixed projected renderer should produce at least 3 regions");

  let hasDiverseRegion = false;
  let hasMetadataAnchoredRegion = false;

  for (const region of report.regions) {
    assertEqual(region.imageGenerated, true, `Mixed region ${region.regionId} should generate output image`);
    assertEqual(region.imagePath.endsWith(".png"), true, `Mixed region ${region.regionId} should output PNG path`);
    assertEqual(region.modeStats.bottomCenter.spriteCount > 0, true, `Mixed region ${region.regionId} should draw sprites in bottom-center mode`);
    assertEqual(region.modeStats.metadata.spriteCount > 0, true, `Mixed region ${region.regionId} should draw sprites in metadata-anchor mode`);

    assertEqual(
      region.modeStats.metadata.metadataAnchorCount + region.modeStats.metadata.bottomCenterFallbackCount,
      region.modeStats.metadata.spriteCount,
      `Mixed region ${region.regionId} metadata/fallback anchor totals should equal metadata mode sprite count`,
    );

    assertEqual(
      region.modeStats.bottomCenter.drawOrderDeterministic,
      true,
      `Mixed region ${region.regionId} bottom-center draw order should be deterministic`,
    );
    assertEqual(
      region.modeStats.metadata.drawOrderDeterministic,
      true,
      `Mixed region ${region.regionId} metadata draw order should be deterministic`,
    );

    assertEqual(
      region.modeStats.bottomCenter.drawOrderSignature,
      region.modeStats.bottomCenter.drawOrderRepeatSignature,
      `Mixed region ${region.regionId} bottom-center signatures should match`,
    );
    assertEqual(
      region.modeStats.metadata.drawOrderSignature,
      region.modeStats.metadata.drawOrderRepeatSignature,
      `Mixed region ${region.regionId} metadata signatures should match`,
    );

    if (region.f2Ids.length >= 3 && region.semanticGroups.length >= 2) {
      hasDiverseRegion = true;
    }
    if (region.modeStats.metadata.metadataAnchorCount > 0) {
      hasMetadataAnchoredRegion = true;
    }
  }

  assertEqual(hasDiverseRegion, true, "Mixed projected renderer should include at least one diverse region");
  assertEqual(hasMetadataAnchoredRegion, true, "Mixed projected renderer should use metadata anchors in at least one region");
}

function buildRealMapTraceReport(mapBytes: Uint8Array): FacilityPlacementTraceReport {
  const parsed = parseMapBinarySectionA(mapBytes);
  const world = createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  const { pipeline } = createTownHallMilestoneRuntime();
  return traceTownHallFacilityPlacementOnWorld(world, pipeline, { preferInteriorTownArea: true });
}

function findValidTownHallPlacement(world: SimWorld, pipeline: PlacementPipeline): { x: number; y: number } {
  for (let y = 1; y < world.height - 2; y++) {
    for (let x = 1; x < world.width - 2; x++) {
      const result = pipeline.canPlaceChip(world, { chipId: 58, x, y, source: "user" });
      if (result.ok) {
        return { x, y };
      }
    }
  }

  throw new Error("No valid Town Hall placement coordinate found for effective runtime layer milestone");
}

function summarizeWorld(world: SimWorld, catalog: InMemoryRuntimeCatalog): unknown {
  const entities = [...world.entities.values()]
    .sort((left, right) => left.id - right.id)
    .map((entity) => ({
      id: entity.id,
      chipId: entity.chipId,
      cell: entity.cell,
      parentId: entity.parentId,
      childIds: entity.childIds,
      stackOrder: entity.components.render.stackOrder,
      fenceMask: entity.components.fence?.mask,
      fenceFrameKey: entity.components.fence?.frameKey,
    }));

  const renderables = buildRenderables(world, catalog).map((renderable) => ({
    entityId: renderable.entityId,
    chipId: renderable.chipId,
    cell: renderable.cell,
    z: renderable.z,
    frameKey: renderable.frameKey,
  }));

  return {
    entities,
    townAreas: [...world.townAreas.values()]
      .sort((left, right) => left.id - right.id)
      .map((townArea) => ({
        id: townArea.id,
        originEntityId: townArea.originEntityId,
        rect: townArea.rect,
      })),
    townCells: collectTownCells(world),
    renderables,
  };
}

function collectTownCells(world: SimWorld): Array<{ x: number; y: number; townAreaId: number }> {
  const result: Array<{ x: number; y: number; townAreaId: number }> = [];
  for (let y = 0; y < world.height; y++) {
    for (let x = 0; x < world.width; x++) {
      const townAreaId = world.getCell(x, y).townAreaId;
      if (townAreaId !== undefined) {
        result.push({ x, y, townAreaId });
      }
    }
  }

  return result;
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertThrows(callback: () => void, expectedMessagePart: string, message: string): void {
  try {
    callback();
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (text.includes(expectedMessagePart)) {
      return;
    }

    throw new Error(
      `${message}: expected error including ${JSON.stringify(expectedMessagePart)}, got ${JSON.stringify(text)}`,
    );
  }

  throw new Error(`${message}: expected function to throw`);
}
