import type { PlacementContext, PlacementSystem } from "../types";

const TOWN_HALL_ROOT_CHIP_ID = 58;

export class TownSystem implements PlacementSystem {
  readonly name = "town";

  afterPlace(context: PlacementContext): void {
    const chipId = context.placedEntity.chipId;
    if (chipId !== TOWN_HALL_ROOT_CHIP_ID) {
      return;
    }

    const facility = context.catalog.getFacilityForChip(chipId);
    if (!facility) {
      return;
    }

    if (facility.parentChipId !== chipId) {
      return;
    }

    const mapChipRect = context.placedEntity.components.mapChipRect;
    context.world.createTownArea(context.placedEntity.id, {
      x: mapChipRect.x - 1,
      y: mapChipRect.y - 1,
      width: 4,
      height: 4,
    });
  }
}
