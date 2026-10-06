import { CommandBuffer, type BufferedRuntimeCommand } from "./command-buffer";
import { canPlaceChip, getOccupiedCellsForChip } from "./placement-validation";
import type { PlaceChipCommand, PlacementSystem, RuntimeCatalog, WorldEntity } from "./types";
import { SimWorld } from "./world";

export class PlacementPipeline {
  constructor(
    private readonly catalog: RuntimeCatalog,
    private readonly systems: PlacementSystem[],
  ) {}

  placeChip(world: SimWorld, command: PlaceChipCommand): WorldEntity {
    const validation = this.canPlaceChip(world, command);
    if (!validation.ok) {
      const reasonSummary = validation.reasons
        .map((reason) => `${reason.code}: ${reason.message}`)
        .join("; ");
      throw new Error(`Cannot place chip ${command.chipId} at ${command.x},${command.y}. ${reasonSummary}`);
    }

    const chip = this.catalog.getMapChip(command.chipId);
    const stackOrder = this.getStackOrder(world, command.parentEntityId);
    const placedEntity = world.createEntity(command, stackOrder);

    placedEntity.components.mapChipRect = {
      x: command.x,
      y: command.y,
      width: chip.sizeWidth,
      height: chip.sizeHeight,
    };

    const facility = this.catalog.getFacilityForChip(command.chipId);
    if (facility) {
      placedEntity.components.facility = { facilityId: facility.id };
    }

    const commandBuffer = new CommandBuffer();

    for (const system of this.systems) {
      if (!command.skipSystems?.has(system.name)) {
        system.afterPlace?.({
          catalog: this.catalog,
          world,
          command,
          placedEntity,
          commandBuffer,
        });
      }
    }

    this.flush(world, commandBuffer.drain());
    return placedEntity;
  }

  getOccupiedCellsForChip(chipId: number, x: number, y: number) {
    return getOccupiedCellsForChip(this.catalog, chipId, x, y);
  }

  canPlaceChip(world: SimWorld, command: PlaceChipCommand) {
    return canPlaceChip(this.catalog, world, command);
  }

  removeEntity(world: SimWorld, entityId: number): WorldEntity {
    const removedEntity = world.removeEntity(entityId);

    for (const system of this.systems) {
      system.afterRemove?.({
        catalog: this.catalog,
        world,
        removedEntity,
      });
    }

    return removedEntity;
  }

  private flush(world: SimWorld, commands: BufferedRuntimeCommand[]): void {
    for (const runtimeCommand of commands) {
      if (runtimeCommand.type === "placeChip") {
        this.placeChip(world, runtimeCommand.command);
      }
    }
  }

  private getStackOrder(world: SimWorld, parentEntityId: number | undefined): number {
    if (parentEntityId === undefined) {
      return 0;
    }

    const parent = world.getEntity(parentEntityId);
    if (!parent) {
      throw new Error(`Missing parent entity ${parentEntityId}`);
    }

    return parent.components.render.stackOrder + parent.childIds.length + 1;
  }
}
