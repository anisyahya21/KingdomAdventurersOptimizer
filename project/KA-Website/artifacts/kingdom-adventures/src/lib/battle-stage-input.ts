import type { BeatState, BattleEncounter } from "@/lib/battle-replay";

/**
 * Renderer-facing stage input - PASS 16 COMMAND 16.11 (first step of the extraction).
 *
 * `VisualFighter` is the only contract the recovered `BattleStage` will accept. It deliberately
 * carries nothing the renderer does not draw: no commands, no RNG, no skill queues, no encounter
 * fixture. Both data sources (the reference fixture and, from COMMAND 16.12, a generated replay)
 * adapt into this shape.
 */

export type VisualFighterSide = "ally" | "enemy";

/** The recovered native lifecycles the stage already draws. */
export type VisualFighterState =
  | "waiting"
  | "attacking"
  | "damaging"
  | "knocking_down"
  | "down"
  | "leaving"
  | "revival_moving";

export type VisualFighterCoverage = {
  status: "FULL" | "PARTIAL";
  /** why the coverage is partial, in the page's own words */
  notes: string[];
};

export type VisualFighter = {
  unitId: string;
  side: VisualFighterSide;
  rosterIndex: number;
  human: boolean;
  monsterId?: number | null;
  /** logical cell (column, row) - stationary fighters derive world position from this */
  cell: { column: number; row: number };
  /** live world position, used while moving/leaving instead of the cell */
  liveWorldPosition?: { x: number; y: number; z: number } | null;
  /** 0 UP / 1 RIGHT / 2 DOWN / 3 LEFT for the human SEB variant, and the monster facing */
  direction: 0 | 1 | 2 | 3;
  /** current native lifecycle */
  visualState: VisualFighterState;
  /** native behaviour id (humans: 3 wait, weapon motion for an ordinary attack, 2 walk, 28/30/7 …) */
  behavior: number;
  /** decoded SEB clip id when the family is decoded; null while a family is not recovered yet */
  clip?: number | null;
  /** frame/update within the clip, in native updates */
  frame: number;
  update?: number;
  hp: number;
  maxHp: number;
  mp: number;
  maxMp: number;
  coverage: VisualFighterCoverage;
};

/**
 * Reference-side adapter (COMMAND 16.11 section 5).
 *
 * Converts the reference fixture's own beat state into `VisualFighter[]` **without changing any of
 * its rendering inputs**: the caller supplies the per-unit animation decision (`behavior`, `clip`,
 * `frame`, `direction`) that the reference page already computed, so the extraction stays
 * behaviour-neutral. Generated mode must NOT use this adapter.
 */
export function referenceVisualFighters(
  encounter: BattleEncounter,
  beatState: BeatState | null,
  animation: (
    side: VisualFighterSide,
    rosterIndex: number,
  ) => { behavior: number; clip?: number | null; frame: number; direction: 0 | 1 | 2 | 3; liveWorldPosition?: { x: number; y: number; z: number } | null; hp: number; maxHp: number; mp: number; maxMp: number; state?: VisualFighterState },
): VisualFighter[] {
  const units = beatState?.units ?? [];
  const fighters: VisualFighter[] = [];
  for (const side of ["ally", "enemy"] as const) {
    const roster = side === "ally" ? encounter.allies : encounter.enemies;
    roster.forEach((unit, index) => {
      const fromBeat = units.find((entry) => entry.side === side && entry.index === index);
      if (!fromBeat) return;
      const anim = animation(side, index);
      fighters.push({
        unitId: `${side}:${index}`,
        side,
        rosterIndex: index,
        human: unit.monsterId === null,
        monsterId: unit.monsterId,
        cell: { column: fromBeat.cell.column, row: fromBeat.cell.row },
        liveWorldPosition: anim.liveWorldPosition ?? null,
        direction: anim.direction,
        visualState: anim.state ?? "waiting",
        behavior: anim.behavior,
        clip: anim.clip ?? null,
        frame: anim.frame,
        update: anim.frame,
        hp: anim.hp,
        maxHp: anim.maxHp,
        mp: anim.mp,
        maxMp: anim.maxMp,
        coverage: { status: "FULL", notes: [] },
      });
    });
  }
  return fighters;
}
