import type {
  EffectiveCellState,
  EffectiveStateRestorationDiff,
  RuntimeOverlayRecord,
  SimWorldApi,
} from "./types";

export interface OverlayStackDump {
  x: number;
  y: number;
  stack: RuntimeOverlayRecord[];
  topOverlay?: RuntimeOverlayRecord;
}

export interface OverlayProvenanceReport {
  overlays: RuntimeOverlayRecord[];
}

export function dumpOverlayStack(world: SimWorldApi, x: number, y: number): OverlayStackDump {
  const stack = world.getRuntimeOverlayStackAtCell(x, y);
  return {
    x,
    y,
    stack,
    topOverlay: stack.length > 0 ? stack[stack.length - 1] : undefined,
  };
}

export function buildOverlayProvenanceReport(world: SimWorldApi, overlayIds: number[]): OverlayProvenanceReport {
  const overlays = overlayIds
    .map((overlayId) => world.getRuntimeOverlayProvenance(overlayId))
    .filter((overlay): overlay is RuntimeOverlayRecord => overlay !== undefined)
    .sort((left, right) => {
      if (left.priority !== right.priority) {
        return left.priority - right.priority;
      }
      if (left.order !== right.order) {
        return left.order - right.order;
      }
      return left.overlayId - right.overlayId;
    });

  return { overlays };
}

export function diffEffectiveStateRestoration(
  world: SimWorldApi,
  before: EffectiveCellState,
  after: EffectiveCellState,
): EffectiveStateRestorationDiff {
  return world.diffEffectiveStateRestoration(before, after);
}
