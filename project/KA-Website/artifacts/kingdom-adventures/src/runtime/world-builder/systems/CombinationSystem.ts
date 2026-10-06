import type { FacilityData, PlacementContext, PlacementSystem } from "../types";

export class CombinationSystem implements PlacementSystem {
  readonly name = "combination";

  afterPlace(context: PlacementContext): void {
    const facility = context.catalog.getFacilityForChip(context.placedEntity.chipId);
    if (!facility || facility.childChipIds.length === 0) {
      return;
    }

    if (facility.combination === 1) {
      this.placeStackChildren(context, facility);
      return;
    }

    if (facility.combination === 2) {
      this.placeNeighborChildren(context, facility);
    }
  }

  private placeStackChildren(context: PlacementContext, facility: FacilityData): void {
    for (const childChipId of facility.childChipIds) {
      context.commandBuffer.placeChip({
        chipId: childChipId,
        x: context.placedEntity.cell.x,
        y: context.placedEntity.cell.y,
        parentEntityId: context.placedEntity.id,
        source: "system",
        skipSystems: new Set([this.name]),
      });
    }
  }

  private placeNeighborChildren(context: PlacementContext, facility: FacilityData): void {
    let cursorX = context.placedEntity.cell.x;

    for (const childChipId of facility.childChipIds) {
      const childChip = context.catalog.getMapChip(childChipId);
      context.commandBuffer.placeChip({
        chipId: childChipId,
        x: cursorX,
        y: context.placedEntity.cell.y,
        parentEntityId: context.placedEntity.id,
        source: "system",
      });
      cursorX += childChip.sizeWidth;
    }
  }
}
