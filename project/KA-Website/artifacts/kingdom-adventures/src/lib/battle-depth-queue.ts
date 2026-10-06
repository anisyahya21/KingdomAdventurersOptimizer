/**
 * Native battle depth queue (PASS 14, recovered from the frozen binary).
 *
 * `RenderSystem.InsertZSortObject(Entity)` 0x15D0214 pushes **one entry per SEB line** into one
 * shared queue; `AppData.DrawZSortObjects` 0x167110C then sorts each queue by the entry's `depth`
 * alone (`AppData.Zsort`/`QuickSort`/`Pivot`/`Partition`) and paints the entries in that order.
 * There is no per-unit draw order and no second key.
 *
 * For an ordinary battle fighter the queued depth is
 *
 *     DepthComponent.depth = worldX + worldY + worldZ + extra + add
 *     queue depth         = DepthComponent.depth + SEB line index
 *
 * and the fighter's world position comes from its cell (`CellXiToPositionX`/`CellYiToPositionZ`):
 * `worldX = stepX * cell.xi`, `worldZ = stepZ * cell.yi` with the battle map's 24 units per cell, so
 *
 *     depth = 24 * (cell.xi + cell.yi) + height + extra + add + lineIndex
 *
 * Nothing else takes part: not the sprite's painted bottom, not the DOM box, not the roster index,
 * not the projected screen Y, not the SEB transY and not the camera.
 */

/** CONFIRMED: one battle cell is 24 world units on both axes (`BattleForm.Init` -> `AddMap`). */
export const BATTLE_CELL_STEP = 24;

/**
 * Ordinary fighter entries carry no depth extra and no UI add: `DepthComponent.extra` is 0 for the
 * plain fighter entities this queue draws, and the only `add` the native path uses is the
 * 9999999 UI depth for a fukidashi/gauge entity, which is not part of this sprite queue (the
 * recovered HP/MP gauges are drawn by `FighterSystem.DrawGauges`, outside the z-sort queue).
 */
export const BATTLE_SPRITE_EXTRA_DEPTH = 0;
export const BATTLE_SPRITE_UI_ADD = 0;

export type BattleQueueCell = { column: number; row: number };
export type BattleQueueSide = "ally" | "enemy";
export type BattleWorldPosition = { x: number; y: number; z: number };

/** `DepthComponent.depth` for a fighter standing on `cell`. */
export function battleBaseDepth(
  cell: BattleQueueCell,
  height = 0,
  extra = BATTLE_SPRITE_EXTRA_DEPTH,
  add = BATTLE_SPRITE_UI_ADD,
): number {
  return BATTLE_CELL_STEP * (cell.column + cell.row) + height + extra + add;
}

/**
 * `DepthComponent.depth` for a fighter whose own `PositionComponent` is live.
 *
 * The leaving fighter is its own kind-10 projectile, so its depth must follow the live world
 * position. `worldX + worldY + worldZ` is exactly the recovered native sum with the zero extra/add
 * of an ordinary fighter entry; the cell form above remains the fast path for stationary units and
 * is unchanged.
 */
export function battleWorldDepth(
  world: BattleWorldPosition,
  extra = BATTLE_SPRITE_EXTRA_DEPTH,
  add = BATTLE_SPRITE_UI_ADD,
): number {
  return world.x + world.y + world.z + extra + add;
}

export type BattleQueueLine<T> = { lineIndex: number; payload: T };

export type BattleQueueItem<T> = {
  unitId: string;
  side: BattleQueueSide;
  unitIndex: number;
  /** the unit's live cell for the rendered replay state (never the initial slot after movement) */
  cell: BattleQueueCell;
  /**
   * Optional live world position. Present only while the unit's own `PositionComponent` is moving
   * (PASS 15 COMMAND 15.9 leaving flight); omitted for every stationary unit so the PASS 14 cell
   * depths stay byte-for-byte identical.
   */
  worldPosition?: BattleWorldPosition | null;
  height?: number;
  lines: readonly BattleQueueLine<T>[];
};

export type BattleSpriteEntry<T> = {
  key: string;
  unitId: string;
  side: BattleQueueSide;
  unitIndex: number;
  cell: BattleQueueCell;
  lineIndex: number;
  baseDepth: number;
  depth: number;
  /** null for the ordinary cell-derived entries */
  worldPosition: BattleWorldPosition | null;
  /** insertion sequence of the website's own queue (never a game field) */
  order: number;
  payload: T;
};

/**
 * Flatten every visible unit's lines into one queue and sort it ascending by `depth`.
 *
 * Ties keep the website's own insertion sequence (unit order, then ascending SEB line index).
 * Native's concrete equal-depth result is **UNREPRODUCED**: it is the product of an unstable
 * hand-written QuickSort over a `HashSet<Entity>` enumeration order, so no secondary key is added
 * here and equal-depth order must not be described as the native winner.
 */
export function buildBattleSpriteQueue<T>(
  items: readonly BattleQueueItem<T>[],
): BattleSpriteEntry<T>[] {
  const entries: BattleSpriteEntry<T>[] = [];
  let order = 0;
  for (const item of items) {
    const worldPosition = item.worldPosition ?? null;
    const baseDepth = worldPosition
      ? battleWorldDepth(worldPosition)
      : battleBaseDepth(item.cell, item.height ?? 0);
    for (const line of item.lines) {
      entries.push({
        key: `${item.side}-${item.unitIndex}-${line.lineIndex}`,
        unitId: item.unitId,
        side: item.side,
        unitIndex: item.unitIndex,
        cell: item.cell,
        worldPosition,
        lineIndex: line.lineIndex,
        baseDepth,
        depth: baseDepth + line.lineIndex,
        order: order++,
        payload: line.payload,
      });
    }
  }
  return entries.sort((a, b) => a.depth - b.depth || a.order - b.order);
}
