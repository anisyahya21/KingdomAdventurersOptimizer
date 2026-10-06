import type { Renderable, RuntimeCatalog, WorldEntity } from "./types";
import { SimWorld } from "./world";

export function buildRenderables(world: SimWorld, catalog: RuntimeCatalog): Renderable[] {
  return [...world.entities.values()]
    .filter((entity) => entity.alive)
    .map((entity) => entityToRenderable(entity, catalog))
    .sort(compareRenderables);
}

function entityToRenderable(entity: WorldEntity, catalog: RuntimeCatalog): Renderable {
  const chip = catalog.getMapChip(entity.chipId);
  const stackOrder = entity.components.render.stackOrder;

  return {
    entityId: entity.id,
    chipId: chip.id,
    name: chip.name,
    cell: { ...entity.cell },
    layer: chip.layer,
    stackOrder,
    z: chip.layer * 1_000_000 + entity.cell.y * 1_000 + entity.cell.x * 10 + stackOrder,
    frameKey: entity.components.fence?.frameKey ?? `chip-${chip.id}`,
  };
}

export function compareRenderables(left: Renderable, right: Renderable): number {
  return left.z - right.z || left.entityId - right.entityId;
}
