import type { ChipId, FacilityData, MapChipData, RuntimeCatalog } from "./types";

export interface RuntimeCatalogInput {
  mapChips: MapChipData[];
  facilities: FacilityData[];
}

export class InMemoryRuntimeCatalog implements RuntimeCatalog {
  private readonly mapChipsById = new Map<ChipId, MapChipData>();
  private readonly facilitiesByChipId = new Map<ChipId, FacilityData>();

  constructor(input: RuntimeCatalogInput) {
    for (const chip of input.mapChips) {
      this.mapChipsById.set(chip.id, chip);
    }

    for (const facility of input.facilities) {
      this.facilitiesByChipId.set(facility.parentChipId, facility);

      for (const childChipId of facility.childChipIds) {
        this.facilitiesByChipId.set(childChipId, {
          ...facility,
          parentChipId: childChipId,
          childChipIds: [],
          combination: 0,
        });
      }
    }
  }

  getMapChip(chipId: ChipId): MapChipData {
    const chip = this.mapChipsById.get(chipId);
    if (!chip) {
      throw new Error(`Unknown MapChip id ${chipId}`);
    }
    return chip;
  }

  getFacilityForChip(chipId: ChipId): FacilityData | undefined {
    return this.facilitiesByChipId.get(chipId);
  }
}
