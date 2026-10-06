export { InMemoryRuntimeCatalog } from "./catalog";
export { createWorldFromParsedMap, parseMapBinarySectionA } from "./map-loader";
export { buildMapChipVisualResolverMarkdown, buildMapChipVisualResolverReport } from "./mapchip-visual-resolver";
export { buildMapRenderProjection } from "./map-render-projection";
export { buildOverlayProvenanceReport, diffEffectiveStateRestoration, dumpOverlayStack } from "./overlay-lifecycle-trace";
export { OBSERVED_F2_MAPCHIP_IDS } from "./fixtures/f2-semantic-layer";
export { PlacementPipeline } from "./pipeline";
export {
  buildFacilityPlacementTraceMarkdown,
  traceTownHallFacilityPlacement,
  traceTownHallFacilityPlacementOnWorld,
} from "./facility-placement-trace";
export { canPlaceChip, getOccupiedCellsForChip } from "./placement-validation";
export { buildRenderables, compareRenderables } from "./render-projection";
export { deserializeWorldState, serializeWorldState } from "./state-io";
export { CombinationSystem } from "./systems/CombinationSystem";
export { FenceSystem } from "./systems/FenceSystem";
export { TownSystem } from "./systems/TownSystem";
export { SimWorld } from "./world";
export {
  createTownHallMilestoneRuntime,
  runOccupancyValidationMilestone,
  runMapRenderProjectionMilestone,
  runF2SemanticFixtureMilestone,
  runPlacementSemanticMilestone,
  runMapCellSemanticAnnotationMilestone,
  runRealMapLoadingMilestone,
  runRealMapFacilityPlacementTraceMilestone,
  runEffectiveRuntimeLayerResolutionMilestone,
  runRuntimeOverlayLifecycleMilestone,
  runMapChipVisualResolverMilestone,
  runMapChipProjectedSampleRegionMilestone,
  runMapChipProjectedMixedRegionsMilestone,
  runTerrainRestrictionMilestone,
  runTownAreaMilestone,
  runTownHallFenceMilestone,
  runFacilityPlacementTraceMilestone,
  runSimWorldLegalityAccessorMilestone,
  runSaveLoadMilestone,
  runTownHallStackMilestone,
} from "./milestone-fixture";
export type { MapRenderProjection, MapRenderProjectionStats, MapRenderTile } from "./map-render-projection";
export type {
  AnnotationCellState,
  AnnotationEffectiveCellDiff,
  CellLegality,
  CellPoint,
  CellRect,
  ChipId,
  CombinationKind,
  EntityId,
  EffectiveCellState,
  EffectiveOverrideSource,
  EffectiveStateRestorationDiff,
  FenceComponent,
  FacilityData,
  MapCell,
  MapChipData,
  PlaceChipCommand,
  PlacementContext,
  PlacementValidationReason,
  PlacementValidationReasonCode,
  PlacementValidationResult,
  PlacementTerrainRule,
  PlacementSystem,
  ParsedMapBinary,
  ParsedMapCell,
  RawMapCellFields,
  Renderable,
  RuntimeCatalog,
  RuntimeOverlayApplyInput,
  RuntimeOverlayRecord,
  SimWorldSnapshot,
  TerrainKind,
  TownArea,
  WorldEntity,
  WorldEntitySnapshot,
} from "./types";
export type {
  FacilityPlacementTraceOptions,
  FacilityPlacementTraceCellDiff,
  FacilityPlacementTraceCellSnapshot,
  FacilityPlacementTraceReport,
} from "./facility-placement-trace";
export type { OverlayProvenanceReport, OverlayStackDump } from "./overlay-lifecycle-trace";
export type {
  MapChipProjectedSampleRegionMilestoneReport,
  MapChipProjectedMixedRegionModeStats,
  MapChipProjectedMixedRegionReport,
  MapChipProjectedMixedRegionsMilestoneReport,
} from "./milestone-fixture";
export type {
  MapChipAssetCandidate,
  MapChipCsvRow,
  MapChipVisualResolution,
  MapChipVisualResolverBuildInput,
  MapChipVisualResolverReport,
  MapChipVisualSampleTile,
} from "./mapchip-visual-resolver";
