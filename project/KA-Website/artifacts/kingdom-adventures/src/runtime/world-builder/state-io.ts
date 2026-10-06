import type { SimWorldSnapshot } from "./types";
import { SimWorld } from "./world";

export function serializeWorldState(world: SimWorld): string {
  return JSON.stringify(world.exportSnapshot());
}

export function deserializeWorldState(serialized: string): SimWorld {
  const raw = JSON.parse(serialized) as SimWorldSnapshot;
  return SimWorld.fromSnapshot(raw);
}
