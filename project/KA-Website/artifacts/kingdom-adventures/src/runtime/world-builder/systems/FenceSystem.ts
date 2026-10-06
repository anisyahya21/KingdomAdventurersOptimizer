import type {
  PlacementContext,
  PlacementSystem,
  RemovalContext,
  RuntimeCatalog,
  SimWorldApi,
  WorldEntity,
} from "../types";

const NORTH = 1;
const EAST = 2;
const SOUTH = 4;
const WEST = 8;
const TOWNHALL_FENCE_TYPE = 13;

export class FenceSystem implements PlacementSystem {
  readonly name = "fence";

  afterPlace({ catalog, world, placedEntity }: PlacementContext): void {
    if (!isTownHallFenceChip(catalog, placedEntity)) {
      return;
    }

    this.recomputeSelfAndNeighbors(catalog, world, placedEntity.cell.x, placedEntity.cell.y);
  }

  afterRemove({ catalog, world, removedEntity }: RemovalContext): void {
    if (!isTownHallFenceChip(catalog, removedEntity)) {
      return;
    }

    this.recomputeSelfAndNeighbors(catalog, world, removedEntity.cell.x, removedEntity.cell.y);
  }

  private recomputeSelfAndNeighbors(
    catalog: RuntimeCatalog,
    world: SimWorldApi,
    x: number,
    y: number,
  ): void {
    this.recomputeAt(catalog, world, x, y);
    this.recomputeAt(catalog, world, x, y - 1);
    this.recomputeAt(catalog, world, x + 1, y);
    this.recomputeAt(catalog, world, x, y + 1);
    this.recomputeAt(catalog, world, x - 1, y);
  }

  private recomputeAt(catalog: RuntimeCatalog, world: SimWorldApi, x: number, y: number): void {
    if (x < 0 || y < 0 || x >= world.width || y >= world.height) {
      return;
    }

    const entity = world.getTopEntityAt(x, y);
    if (!entity || !isTownHallFenceChip(catalog, entity)) {
      return;
    }

    const mask = this.computeMask(catalog, world, x, y);
    entity.components.fence = {
      kind: "townhall",
      mask,
      frameKey: this.resolveFrameKey(mask),
    };
  }

  private computeMask(catalog: RuntimeCatalog, world: SimWorldApi, x: number, y: number): number {
    let mask = 0;

    if (this.hasFenceAt(catalog, world, x, y - 1)) {
      mask |= NORTH;
    }
    if (this.hasFenceAt(catalog, world, x + 1, y)) {
      mask |= EAST;
    }
    if (this.hasFenceAt(catalog, world, x, y + 1)) {
      mask |= SOUTH;
    }
    if (this.hasFenceAt(catalog, world, x - 1, y)) {
      mask |= WEST;
    }

    return mask;
  }

  private hasFenceAt(catalog: RuntimeCatalog, world: SimWorldApi, x: number, y: number): boolean {
    if (x < 0 || y < 0 || x >= world.width || y >= world.height) {
      return false;
    }

    const entity = world.getTopEntityAt(x, y);
    if (!entity) {
      return false;
    }

    return isTownHallFenceChip(catalog, entity);
  }

  private resolveFrameKey(mask: number): string {
    return `townhall-fence-mask-${mask}`;
  }
}

function isTownHallFenceChip(catalog: RuntimeCatalog, entity: WorldEntity): boolean {
  const chip = catalog.getMapChip(entity.chipId);
  return chip.type === TOWNHALL_FENCE_TYPE;
}
