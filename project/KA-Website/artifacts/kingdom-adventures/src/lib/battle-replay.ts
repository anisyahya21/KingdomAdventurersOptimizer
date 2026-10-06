/**
 * First-pass Wairo/Kairo battle replay spec.
 *
 * Shared by both fights on purpose: current working conclusion is that Wairo
 * Dungeon and Kairo Room use the same battle presentation system, and that the
 * differences are encounter content (lineups, timeline) rather than renderer
 * structure.
 *
 * Evidence tags used everywhere below:
 *   CONFIRMED = directly supported by an extracted original asset or by a
 *               traced original code path (arguments/constants read from the
 *               frozen libil2cpp.so or from the original xls/img.inf tables).
 *   SUPPORTED = strong inference from available evidence, not directly proven.
 *   UNKNOWN   = not established. Never upgraded by this file.
 *
 * This is a VISUAL REPLAY / VISUAL AID. Nothing here claims exact native
 * animation timing or exact native screen placement.
 */

import monsterSprites from "@/game-data/monster-sprites.json";
import battleAnimation from "@/game-data/battle-animation.json";

export type EvidenceLevel = "CONFIRMED" | "SUPPORTED" | "UNKNOWN";

export type Tagged<T> = { value: T; evidence: EvidenceLevel; note: string };

/* ------------------------------------------------------------------ */
/* Layers: a single shared draw order for both encounters              */
/* ------------------------------------------------------------------ */

export type BattleLayerId =
  | "arena"
  | "ground"
  | "units"
  | "effects"
  | "overlay-text"
  | "balloon"
  | "hud";

export type BattleLayer = {
  id: BattleLayerId;
  label: string;
  evidence: EvidenceLevel;
  note: string;
};

export const BATTLE_LAYERS: BattleLayer[] = [
  {
    id: "arena",
    label: "Arena / background",
    evidence: "CONFIRMED",
    note: "battle/bg_*.png are the battle-group images (battle/img.inf); which image a given fight shows is UNKNOWN.",
  },
  {
    id: "ground",
    label: "Ground shading",
    evidence: "UNKNOWN",
    note: "No separated original battle-ground layer was found; the arena image already contains the ground.",
  },
  {
    id: "units",
    label: "Units (allies left / enemies right)",
    evidence: "SUPPORTED",
    note: "Human/monster combat animations reuse the world chara/monster SEB sets; left/right split is a replay choice, not a traced native screen mapping.",
  },
  {
    id: "effects",
    label: "Hit / skill effects",
    evidence: "CONFIRMED",
    note: "Impact effects come from battle Effect creation with resource 28 (effect) and seb 0 (effect_00).",
  },
  {
    id: "overlay-text",
    label: "Combat text and damage numbers",
    evidence: "CONFIRMED",
    note: "Text = resource 6 (game) seb 126 attack_str; numbers = resource 4 (com) seb 78 / 112 / 237.",
  },
  {
    id: "balloon",
    label: "Skill balloon",
    evidence: "SUPPORTED",
    note: "Resource 6 (game) seb 160 skill_balloon, two 80x24 frames, spawned at Y+50 with a 20 frame lifetime.",
  },
  {
    id: "hud",
    label: "Optional HUD / dialog bubble",
    evidence: "SUPPORTED",
    note: "battle/member_info_bar, command_bar/button, treasure_bar, exp_box, gauge/battle_gauge, fukidashi/dialog panels.",
  },
];

/* ------------------------------------------------------------------ */
/* Resource id table (original constants)                              */
/* ------------------------------------------------------------------ */

/**
 * Original resource group constants. CONFIRMED: read from the game's own
 * constant table (`RSRES_*`, resource id block next to RSFILENAMESS).
 */
export const RESOURCE_IDS = {
  COM: 4,
  COM2: 5,
  GAME: 6,
  FUKIDASHI: 8,
  CHARA: 11,
  MONSTER: 22,
  NUM: 24,
  EFFECT: 28,
  BATTLE: 35,
} as const;

/* ------------------------------------------------------------------ */
/* Arena backgrounds                                                   */
/* ------------------------------------------------------------------ */

export type Arena = {
  id: string;
  file: string;
  /** short visual description taken from looking at the extracted image */
  look: string;
  /** seen in the user's recordings of both fight types */
  seenInRecordings?: boolean;
};

/**
 * CONFIRMED assets: these are every image in the battle resource group
 * (battle/img.inf indices 0-6 and 99-102). Their selection rule per fight is
 * UNKNOWN, so the replay exposes them as a picker instead of guessing.
 */
export const ARENAS: Arena[] = [
  { id: "bg_00", file: "/battle-assets/arena/bg_00.png", look: "open dirt field, grass tufts" },
  { id: "bg_01", file: "/battle-assets/arena/bg_01.png", look: "green field" },
  { id: "bg_02", file: "/battle-assets/arena/bg_02.png", look: "sand / desert" },
  { id: "bg_03", file: "/battle-assets/arena/bg_03.png", look: "rocky ground" },
  { id: "bg_04", file: "/battle-assets/arena/bg_04.png", look: "red clay / scorched" },
  { id: "bg_05", file: "/battle-assets/arena/bg_05.png", look: "snow" },
  { id: "bg_06", file: "/battle-assets/arena/bg_06.png", look: "dark grass" },
  { id: "bg_99", file: "/battle-assets/arena/bg_99.png", look: "stone bridge over water" },
  {
    id: "bg_100",
    file: "/battle-assets/arena/bg_100.png",
    look: "red-carpet hall with columns",
    seenInRecordings: true,
  },
  { id: "bg_101", file: "/battle-assets/arena/bg_101.png", look: "brown cave / rock" },
  { id: "bg_102", file: "/battle-assets/arena/bg_102.png", look: "violet sparkle cave" },
];

export const ARENA_SELECTION_EVIDENCE: Tagged<string> = {
  value: "both recorded fights show the same arena image",
  evidence: "SUPPORTED",
  note:
    "User recordings of a Kairo Room fight (avg Lv 251) and a Wairo Dungeon fight (avg Lv 83) both show the same red-carpet arena art, which matches battle/bg_100.png. The per-fight selection rule itself is still UNKNOWN (BattleForm.Init loads resource group 35; BattleSystem.GetTerrain reads AreaData.terrain but its only BL caller is the attribute-match check), so the picker stays available.",
};

/** Arena chosen for both encounter families by default: the art seen in the recordings. */
export const SHARED_ARENA_ID = "bg_100";

/* ------------------------------------------------------------------ */
/* Combat text (Missed / Guard / Dodge / Weak / Critical Hit / ...)     */
/* ------------------------------------------------------------------ */

export type CombatTextLabel =
  | "Missed"
  | "Guard"
  | "Dodge"
  | "Weak"
  | "Critical Hit"
  | "DEF Down"
  | "Sleep";

/**
 * CONFIRMED rects: game/attack_str.png is a 50x112 sheet whose seb record list
 * holds seven 50x16 frames stacked at y = 0,16,...,96. The English sheet reads
 * exactly Missed / Guard / Dodge / Weak / Critical Hit / DEF Down / Sleep, so
 * frame index -> label is a direct read of the original asset.
 */
export const COMBAT_TEXT_SHEET = {
  file: "/battle-assets/text/combat-text-en.png",
  width: 50,
  height: 112,
  alternativeFile: "/battle-assets/text/combat-text-jp.png",
} as const;

export const COMBAT_TEXT_ROWS: { index: number; label: CombatTextLabel; y: number }[] = [
  { index: 0, label: "Missed", y: 0 },
  { index: 1, label: "Guard", y: 16 },
  { index: 2, label: "Dodge", y: 32 },
  { index: 3, label: "Weak", y: 48 },
  { index: 4, label: "Critical Hit", y: 64 },
  { index: 5, label: "DEF Down", y: 80 },
  { index: 6, label: "Sleep", y: 96 },
];

export const COMBAT_TEXT_SIZE = { width: 50, height: 16 } as const;

/* ------------------------------------------------------------------ */
/* Damage numbers                                                      */
/* ------------------------------------------------------------------ */

export type NumberStyle = "ally" | "critical" | "enemy";

/**
 * CONFIRMED rects: com/num_m_02.png is a 90x50 sheet with four digit rows of
 * 8x10 glyphs at y = 0 (white), 10 (yellow), 20 (red), 30 (green); glyph k sits
 * at x = 8k and the trailing symbol frame is 7x10 at x = 80.
 *
 * CONFIRMED style selection: the original attack-effect path allocates the
 * damage number with resource 4 (com) and seb 78 (num_m_02, white) or 237
 * (num_m_02_yellow) when the attacker is an ally, and seb 112 (num_red) when
 * the attacker is not an ally. Critical hits pick the yellow row.
 */
export const NUMBER_SHEET = {
  white: "/battle-assets/numbers/num_m_02.png",
  red: "/battle-assets/numbers/num_red.png",
  yellow: "/battle-assets/numbers/num_m_02.png",
} as const;

export const NUMBER_ROW_Y: Record<NumberStyle, number> = {
  ally: 0,
  critical: 10,
  enemy: 20,
};

export const NUMBER_ROW_IMAGES: Record<NumberStyle, string> = {
  ally: "/battle-assets/numbers/num_m_02.png",
  critical: "/battle-assets/numbers/num_m_02.png",
  enemy: "/battle-assets/numbers/num_m_02.png",
};

export const NUMBER_GLYPH = { width: 8, height: 10, symbolX: 80, symbolWidth: 7 } as const;

/* ------------------------------------------------------------------ */
/* Skill balloon                                                       */
/* ------------------------------------------------------------------ */

/**
 * CONFIRMED asset/geometry: game/skill_balloon.png is 80x48 with two 80x24
 * frames (orange at y=0, blue at y=24); the original CreateSkillBalloon call
 * uses resource 6, seb 160, Y+50 and a 20 frame lifetime with value2 = not ally.
 * SUPPORTED mapping: value2 0 (ally) -> orange frame, 1 (enemy) -> blue frame.
 */
export const SKILL_BALLOON_SHEET = {
  file: "/battle-assets/balloon/skill_balloon.png",
  width: 80,
  height: 24,
  offsetY: -50,
  lifetimeFrames: 20,
} as const;

export const SKILL_BALLOON_FRAMES = { ally: 0, enemy: 24 } as const;

/* ------------------------------------------------------------------ */
/* Effects                                                             */
/* ------------------------------------------------------------------ */

export type EffectSprite = {
  id: string;
  file: string;
  evidence: EvidenceLevel;
  note: string;
};

export const EFFECT_SPRITES: Record<string, EffectSprite> = {
  impact: {
    id: "impact",
    file: "/battle-assets/effects/impact-effect_00.png",
    evidence: "CONFIRMED",
    note:
      "effect/effect_00 is the resource-28 seb-0 impact image used by the original attack-effect path. The extracted PNG is the optimizer's packed output, so frame slicing is not exact yet (see missing list).",
  },
  flash: {
    id: "flash",
    file: "/battle-assets/effects/flash.png",
    evidence: "SUPPORTED",
    note: "effect/flash.png; a generic original flash used here as a stand-in hit flash.",
  },
  sparkle: {
    id: "sparkle",
    file: "/battle-assets/effects/sparkle-com.png",
    evidence: "SUPPORTED",
    note: "com/effect_00.png, four 37x39 frames confirmed against com/effect_00.seb.",
  },
  smoke: {
    id: "smoke",
    file: "/battle-assets/effects/smoke-strip.png",
    evidence: "SUPPORTED",
    note: "effect/effect_37.png; used as a stand-in for the native knockdown/leaving smoke (native smoke is resource 28 / seb 0 with Y+10).",
  },
  orb: {
    id: "orb",
    file: "/battle-assets/effects/orb-trio.png",
    evidence: "SUPPORTED",
    note: "effect/effect_24.png; stand-in for a magic projectile.",
  },
  bossBalloon: {
    id: "bossBalloon",
    file: "/battle-assets/balloon/boss_balloon.png",
    evidence: "SUPPORTED",
    note: "com/boss_balloon.png, spiky boss speech balloon (240x90).",
  },
};

export const INDICATOR_SPRITES = {
  arrow: "/battle-assets/indicators/arrow_00.png",
  doubleArrow: "/battle-assets/indicators/arw_ud.png",
  skewArrow: "/battle-assets/indicators/skew_arrow.png",
} as const;

export const HUD_SPRITES = {
  memberInfoBar: "/battle-assets/ui/member_info_bar.png",
  commandBar: "/battle-assets/ui/command_bar.png",
  commandButton: "/battle-assets/ui/command_button.png",
  treasureBar: "/battle-assets/ui/treasure_bar.png",
  expBox: "/battle-assets/ui/exp_box.png",
  battleGauge: "/battle-assets/ui/battle_gauge.png",
  sleep: "/battle-assets/ui/zzz.png",
  bossBanner: "/battle-assets/ui/boss.png",
  itemSlot: "/battle-assets/ui/item_slot_mini.png",
  bubble: "/battle-assets/bubble/fukidashi_back.png",
  dialog: "/battle-assets/bubble/dialog_base.png",
} as const;

/* ------------------------------------------------------------------ */
/* Native CreateEffect arguments actually recovered for this fight      */
/* ------------------------------------------------------------------ */

export type NativeEffectSpec = {
  name: string;
  type: number;
  value1: string;
  value2: string;
  resource: number;
  resourceGroup: keyof typeof RESOURCE_IDS;
  seb: number | string;
  offsetY: number;
  lifetime: number | string;
  evidence: EvidenceLevel;
  note: string;
};

/**
 * CONFIRMED: these are the argument tuples the original code passes into the
 * shared Effect constructor for the combat cases that matter here.
 */
export const NATIVE_EFFECT_SPECS: NativeEffectSpec[] = [
  {
    name: "Normal hit impact",
    type: 0,
    value1: "0",
    value2: "0",
    resource: 28,
    resourceGroup: "EFFECT",
    seb: 0,
    offsetY: 10,
    lifetime: "resource frame count",
    evidence: "CONFIRMED",
    note: "Scale becomes 150 on a critical hit.",
  },
  {
    name: "Damaged target text (Missed)",
    type: 12,
    value1: "0",
    value2: "0",
    resource: 6,
    resourceGroup: "GAME",
    seb: 126,
    offsetY: 15,
    lifetime: "sprite default",
    evidence: "CONFIRMED",
    note: "Resource 6 (game) seb 126 is attack_str, the 7-row status-label sheet.",
  },
  {
    name: "Critical text",
    type: 12,
    value1: "9",
    value2: "0",
    resource: 6,
    resourceGroup: "GAME",
    seb: 126,
    offsetY: 15,
    lifetime: "sprite default",
    evidence: "CONFIRMED",
    note: "Same sheet as Missed; which row value1 9 selects is UNKNOWN.",
  },
  {
    name: "Damage number",
    type: 1,
    value1: "damage",
    value2: "0",
    resource: 4,
    resourceGroup: "COM",
    seb: "78 ally / 237 crit / 112 enemy",
    offsetY: 20,
    lifetime: "sprite default",
    evidence: "CONFIRMED",
    note: "White / yellow / red number rows of com/num_m_02.png plus com/num_red.png.",
  },
  {
    name: "Healing number",
    type: 1,
    value1: "amount",
    value2: "0",
    resource: 4,
    resourceGroup: "COM",
    seb: 279,
    offsetY: 20,
    lifetime: 30,
    evidence: "SUPPORTED",
    note: "Special Battle.OnCure path; the seb-279 image file itself is not yet pinned in the extracted set.",
  },
  {
    name: "Skill balloon",
    type: 15,
    value1: "skill id",
    value2: "not ally",
    resource: 6,
    resourceGroup: "GAME",
    seb: 160,
    offsetY: 50,
    lifetime: 20,
    evidence: "CONFIRMED",
    note: "game/skill_balloon.png; two 80x24 frames.",
  },
];

/* ------------------------------------------------------------------ */
/* Shared formation math (recovered) and the replay screen mapping      */
/* ------------------------------------------------------------------ */

export type Slot = { column: number; row: number };

/**
 * Provenance of every constant used to place a unit. Each entry names the
 * original function/address it was read from; `direct: false` means the value
 * was inferred rather than read.
 */
export type FormationRule = {
  id: string;
  value: string;
  source: string;
  direct: boolean;
  evidence: EvidenceLevel;
  note: string;
};

export const FORMATION_RULES: FormationRule[] = [
  {
    id: "columns",
    value: "5 columns per team; column = index % 5",
    source: "FighterSystem.CalcGridColumn 0x15870a8 / GridToCellXi 0x15882c8 (magic-number divide by 5)",
    direct: true,
    evidence: "CONFIRMED",
    note: "Disassembly shows the /5 and i - 5*(i/5) sequences.",
  },
  {
    id: "rows",
    value: "row = index // 5",
    source: "FighterSystem.CalcGridRow 0x158708c / GridToCellYi 0x1588338",
    direct: true,
    evidence: "CONFIRMED",
    note: "Same /5 magic constant; GridToCellYi multiplies the row by the direction offset.",
  },
  {
    id: "rowOffset",
    value: "offset = max(3, opponentCount // 5 + 1)",
    source: "FighterSystem.GetRowOffset 0x1588254 -> AppData.ClampMin 0x16657b0",
    direct: true,
    evidence: "CONFIRMED",
    note: "GetRowOffset computes count/5 + 1 then ClampMin(value, 3).",
  },
  {
    id: "ownCells",
    value: "own cell = (column, offset + 1 + row)",
    source: "FighterSystem.CalcGridCell 0x158344c / GridToCellYi 0x1588338",
    direct: true,
    evidence: "CONFIRMED",
    note: "cellY = Direction.OFFSETS[dir].y * row + offset + (dir == DOWN ? 1 : 0) with dir = DOWN(2) for team 0.",
  },
  {
    id: "opponentCells",
    value: "opponent cell = (column, offset - row)",
    source: "same CalcGridCell path with dir = UP(0)",
    direct: true,
    evidence: "CONFIRMED",
    note: "Team 0 gets direction 2 and team 1 gets direction 0 at 0x1582f18-0x1582f28 (cset+cset shift), so the row sign is +1 / -1.",
  },
  {
    id: "teamsAdjacent",
    value: "the two front rows are adjacent cells (offset vs offset+1)",
    source: "derived from the two formulas above",
    direct: true,
    evidence: "CONFIRMED",
    note: "Consequence of the recovered formulas; the gap between the teams is exactly one cell.",
  },
  {
    id: "cellSize",
    value: "one cell = 24 world units (both axes)",
    source: "BattleForm.Init 0x16aa384 -> World.CreateMap 0x1475c5c -> AddMap(stepX, stepZ); MapComponent.stepX +0x20 / stepZ +0x24",
    direct: true,
    evidence: "CONFIRMED",
    note: "BattleForm.Init passes 8, 16, 48, 24, 24, 24, 16, 16; CellXiToPositionX 0x1583550 multiplies by stepX and CellYiToPositionZ 0x158357c by stepZ.",
  },
  {
    id: "mapSize",
    value: "battle map is 8 cells wide x 16 cells deep",
    source: "BattleForm.Init 0x16aa35c (w1=8, w2=16) -> CreateMap -> AddMap(width, height)",
    direct: true,
    evidence: "CONFIRMED",
    note: "The 5-column formation lives inside that 8x16 field; no in-code clamp of the formation to the field was found.",
  },
  {
    id: "screenProjection",
    value: "screenX = x - z, screenY = (x + z) / 2 - y  (x,z = cell * 24)",
    source: "RenderSystem.TransormIsometricViewX 0x1671ce8, TransormIsometricViewY 0x1671cf0, ToScreenPos 0x15cdeec viewType 0 branch; fighter draw path FighterSystem.DrawDynamic 0x1587184 -> AppData.ToScreenPos 0x1671d1c -> RenderSystem.ToScreenPos",
    direct: true,
    evidence: "CONFIRMED",
    note: "viewType is never assigned (RenderSystem.set_viewType 0x15d5ff8 has zero BL callers), so the default branch 0 (isometric) is the one the fighter draw path reaches.",
  },
  {
    id: "stageScale",
    value: "single uniform px-per-world-unit scale used to fit the stage",
    source: "replay presentation choice",
    direct: false,
    evidence: "UNKNOWN",
    note: "The native battle camera/zoom was not recovered, so the replay fits the recovered lattice with one uniform scale and centres it. No per-unit coordinate is fitted.",
  },
  {
    id: "facing",
    value: "own team entity direction = UP(0), opponent = DOWN(2) (both face the enemy)",
    source: "FighterSystem.InitFighters 0x15830e0-0x15830f0 (Direction.Invert of the slot direction stored into the DirectionComponent)",
    direct: true,
    evidence: "CONFIRMED",
    note: "The slot-assignment direction (DOWN for team 0) is inverted before it is stored as the unit's facing.",
  },
  {
    id: "bossOrder",
    value: "opponent roster = [...followers, boss] (the boss is the last member)",
    source: "BattleForm.CreateBossBattle 0x16a9d18-0x16a9d70 (1-element array holding the boss clone -> Enumerable.Concat with the follower array -> ToList -> TeamMember)",
    direct: true,
    evidence: "CONFIRMED",
    note: "With the slot formula the last member is column 0 of the deepest row, i.e. the top-left of the opponent block - which is where the BOSS banner sits in the recordings.",
  },
];

/** CONFIRMED battle-field constants read from the original code. */
export const FORMATION_CONSTANTS = {
  columns: 5,
  cellSize: 24,
  mapWidth: 8,
  mapHeight: 16,
  minRowOffset: 3,
} as const;

/**
 * CONFIRMED: a battle has exactly ONE row offset, `max(3, rivalMemberCount // 5 + 1)`.
 *
 * `GetRowOffset() 0x15833C4` calls `get_RivalTeam() 0x1582048`, which reads the fixed rival slot
 * (`[FighterSystem + 0x38][1]`, the second team of the teams array) and passes its member count to
 * the static `GetRowOffset(int) 0x1588254`. The instance method takes no team argument and is the
 * only entry point (`GetRowOffset(int)` has zero BL callers), and its callers are
 * `InitFighters(Team) 0x1582C88` - once per team, so both teams read the same value - plus
 * `BattleSystem.Start 0x14ECF5C` for the camera and the movement/revival helpers.
 */
export function nativeRowOffset(rivalMemberCount: number): number {
  return Math.max(
    FORMATION_CONSTANTS.minRowOffset,
    Math.floor(rivalMemberCount / FORMATION_CONSTANTS.columns) + 1,
  );
}

/**
 * CONFIRMED: slot index i has column = i % 5 and row = i // 5, own cells are
 * (column, offset + 1 + row) and opponent cells are (column, offset - row).
 *
 * `rowOffset` is the single shared battle offset (`nativeRowOffset` of the rival roster) - it is an
 * argument here so a caller cannot accidentally give the two teams different offsets. Every step
 * above is read from `CalcGridColumn`/`CalcGridRow`/`GetRowOffset`/`CalcGridCell` (see
 * FORMATION_RULES) rather than from footage.
 */
export function nativeSlots(count: number, rowOffset: number, side: "own" | "opponent"): Slot[] {
  const slots: Slot[] = [];
  for (let i = 0; i < count; i += 1) {
    const column = i % FORMATION_CONSTANTS.columns;
    const row = Math.floor(i / FORMATION_CONSTANTS.columns);
    slots.push(
      side === "own"
        ? { column, row: rowOffset + 1 + row }
        : { column, row: rowOffset - row },
    );
  }
  return slots;
}

/**
 * The one shared initial formation of a battle: the offset the camera uses plus both teams' starting
 * cells. `rivalMemberCount` is the rival slot's member count, i.e. the **opponent** roster
 * (`teams[1]`), and it is always the full roster - never a roster cap and never the survivor count.
 *
 * The static layout and the occupancy simulation both start from this, so the two agree at the
 * initial state by construction instead of re-deriving the formula.
 */
export function nativeInitialFormation(
  allyMemberCount: number,
  rivalMemberCount: number,
): { rowOffset: number; allySlots: Slot[]; enemySlots: Slot[] } {
  const rowOffset = nativeRowOffset(rivalMemberCount);
  return {
    rowOffset,
    allySlots: nativeSlots(allyMemberCount, rowOffset, "own"),
    enemySlots: nativeSlots(rivalMemberCount, rowOffset, "opponent"),
  };
}

/**
 * CONFIRMED projection (see screenProjection rule): a cell becomes
 * screenX = x - z and screenY_up = (x + z) / 2 with x = column * 24 and
 * z = row * 24. Canvas Y grows downward, so the renderer negates screenY_up.
 * The height term (-y) is 0 for a standing unit.
 */
export function nativeScreenPos(cellX: number, cellY: number): { x: number; y: number } {
  const x = cellX * FORMATION_CONSTANTS.cellSize;
  const z = cellY * FORMATION_CONSTANTS.cellSize;
  // RenderSystem.ToScreenPos returns screenX = x - z and screenY = (x + z)/2 - y.
  // The canvas Y axis and the recovered lattice agree on this sign: larger
  // (cellX + cellY) draws lower on screen, which places the own team (largest
  // rows) at the bottom-left and the opponent's deeper rows up and to the right.
  return { x: x - z, y: (x + z) / 2 };
}

export const SCREEN_MAPPING_EVIDENCE: Tagged<string> = {
  value: "cells -> screen use the recovered isometric projection; only the stage scale is a replay choice",
  evidence: "SUPPORTED",
  note: "screenX = x - z and screenY = (x + z)/2 - y are read from TransormIsometricViewX/Y and the viewType-0 branch of ToScreenPos that the fighter draw path reaches. The px-per-world-unit scale of the replay stage is a presentation constant (native camera not recovered), applied uniformly to the whole formation.",
};

/** CONFIRMED battle view rect, read from the BattleForm static block at VA 0x751b40. */
export const BATTLE_VIEW = { x: 0, y: 0, width: 240, height: 240 } as const;

/**
 * CONFIRMED offscreen (backdrop) render target, from BattleForm.GetLayoutMethod 0x16aad04:
 * `mov w8,#0x1e1` (481) and `mov w9,#0xc5` (197), format from [this+0x118], filter 0.
 * 481x197 is exactly battle/bg_100.png (480x196) plus one pixel, so the backdrop buffer and the
 * arena image share one pixel space at 1:1.
 */
export const BATTLE_OFFSCREEN = { width: 481, height: 197 } as const;

/** CONFIRMED cell basis in screen units, excluding the (unrecovered) camera. */
export const CELL_BASIS = { xStep: { x: 24, y: 12 }, yStep: { x: -24, y: 12 } } as const;

export const BATTLE_REGISTRATION_EVIDENCE: Tagged<string> = {
  value: "native cell -> view-pixel transform recovered; bg_100 draw path still open",
  evidence: "SUPPORTED",
  note: "Camera is set once at battle start (BattleSystem.Start call 0x14ed108): a = stepX * -6, b = stepZ * (rowOffset - 3), SetCameraPosition(isoX(a,b) - 16, isoY(a,b) + 3). Zoom is 1.0 (BattleForm.Init writes 100 to scale_ +0x11c and GetScaleRange returns 100). The bg_100 draw call itself is still not identified, so the grid is shown in native 240x240 view pixels.",
};

/**
 * CONFIRMED camera setup at battle start (`BattleSystem.Start`, call site 0x14ed108):
 *   a   = stepX * -6                      (0x14ed0b8, stepX read from MapComponent +0x20)
 *   b   = stepZ * (rowOffset - 3)          (0x14ed0bc, rowOffset from FighterSystem.GetRowOffset 0x15833c4)
 *   cam = (TransormIsometricViewX(a, b) - 16, TransormIsometricViewY(a, b) + 3)
 * TransormIsometricViewX/Y here are the int overloads 0x1671ccc / 0x1671cd4 (SUPPORTED: adjacent
 * overloads of the confirmed float pair 0x1671ce8/0x1671cf0, which are a-b and (a+b)/2).
 */
export function battleCamera(rowOffset: number) {
  const a = FORMATION_CONSTANTS.cellSize * -6;
  const b = FORMATION_CONSTANTS.cellSize * (rowOffset - 3);
  return { x: a - b - 16, y: Math.trunc((a + b) / 2) + 3 };
}

/**
 * Final native view-space position of a cell centre.
 *
 * CONFIRMED chain: world = cell * 24 -> isometric (x-z, (x+z)/2) -> minus the camera
 * (the offsetsByCamera branch of RenderSystem.ToScreenPos 0x15cdeec subtracts the camera x/y).
 * CONFIRMED zoom 1.0, so view pixels equal world-screen units (no extra scaling).
 */
export function nativeViewPos(cellX: number, cellY: number, rowOffset: number) {
  const camera = battleCamera(rowOffset);
  const worldX = cellX * FORMATION_CONSTANTS.cellSize;
  const worldZ = cellY * FORMATION_CONSTANTS.cellSize;
  return {
    x: worldX - worldZ - camera.x,
    y: Math.trunc((worldX + worldZ) / 2) - camera.y,
  };
}

/**
 * The same recovered isometric projection for a live world position instead of a cell. The leaving
 * fighter is carried by its own `PositionComponent`, so its screen origin must follow the live
 * world X/Y/Z rather than the cell; the camera and projection rules are identical.
 */
export function nativeWorldViewPos(
  worldX: number,
  worldY: number,
  worldZ: number,
  rowOffset: number,
) {
  const camera = battleCamera(rowOffset);
  return {
    x: worldX - worldZ - camera.x,
    y: Math.trunc((worldX + worldZ) / 2 - worldY) - camera.y,
  };
}

/* ------------------------------------------------------------------ */
/* Battle presentation: view profile + backdrop placement              */
/* ------------------------------------------------------------------ */

/**
 * CONFIRMED backdrop bitmap (`battle/bg_100.png`), drawn 1:1 into the 481x197 offscreen buffer.
 */
export const BATTLE_BACKDROP = { width: 480, height: 196 } as const;

/**
 * CONFIRMED native vertical presentation (`BattleForm.DrawOffscreen` 0x16aac0c together with
 * `BattleForm.Draw` 0x16aaaf0): the presentation blit samples the top 192 logical rows of the
 * battle composite and draws them into a 196-row destination band
 * (`GetGameHeight` 0x16ab9cc subtracts VIEW_Y from 0xC0 = 192, `Draw` uses 0xC4-0xE0 = 196).
 * This is a presentation-only 196/192 stretch; it never touches formation or fighter coordinates.
 */
export const BATTLE_VERTICAL_PRESENTATION = { sourceHeight: 192, presentationHeight: 196 } as const;

/**
 * A battle view profile describes how much of the logical battle space the platform's surface
 * window exposes and how the native vertical presentation maps it. It is deliberately *not* a
 * native constant:
 *
 * - `BattleForm.DynamicRender` 0x16aad2c draws the backdrop at
 *   `backgroundX = trunc((logicalGameViewWidth - 480) / 2)`, where the width is the surface's
 *   scale-normalised `SurfaceBase.GetGameWidth()` (0x23ea110 = width_ * 100 / scaleRatio_), i.e.
 *   the *logical* view width, not a raw pixel width.
 * - `BattleForm.Draw` presents the composite at `dx = 0`, `dy = (logicalViewHeight - 196) / 2`
 *   and `BattleForm.DrawOffscreen` blits source (VIEW_X, VIEW_Y, GetGameWidth(), GetGameHeight())
 *   onto dest (dx, dy, 480 - VIEW_X, 196 - VIEW_Y); VIEW_X/VIEW_Y are 0 in battle (BattleForm
 *   .cctor 0x16ac58c copies the (0, 0, 240, 240) constant at VA 0x751b40), so the full 480-wide
 *   battle region is sampled and the visible extent is the surface window.
 * - The runtime width is platform/setting dependent (`SurfaceManager.AdjustSurfaceLayout` ->
 *   `Canvas.GetWidth2` -> `KairoPlugin`/`Screen` width, over the app's own resolution scaling), so
 *   the visible background interval `[(480 - W) / 2, (480 + W) / 2]` is a per-platform result.
 */
export type ViewProfile = {
  id: string;
  label: string;
  /** visible surface window width in logical battle px */
  logicalViewWidth: number;
  /** native: how many logical rows the presentation blit samples */
  sourceHeight: number;
  /** native: how many rows that sampled band is drawn to */
  presentationHeight: number;
  evidence: EvidenceLevel;
  note: string;
};

/**
 * The recorded profile's 240 is SUPPORTED, never CONFIRMED: the supplied iPad recording measures a
 * ~240-logical-wide view (visible bg_100 source interval ~120..360) and that value satisfies the
 * recovered formula exactly, but it is one device's effective logical width. Another
 * platform/runtime width drops in here without touching formation, camera or fighter coordinates.
 */
export const VIEW_PROFILES: ViewProfile[] = [
  {
    id: "recording",
    label: "Recording-supported native view (~240 logical width)",
    logicalViewWidth: 240,
    sourceHeight: BATTLE_VERTICAL_PRESENTATION.sourceHeight,
    presentationHeight: BATTLE_VERTICAL_PRESENTATION.presentationHeight,
    evidence: "SUPPORTED",
    note:
      "Supplied iPad recording + the recovered native formula: backgroundX = trunc((240 - 480) / 2) = -120, so the window exposes bg_100 source X 120..360, and the native 192 -> 196 vertical presentation applies. Platform-profile value derived from that recording; it is not a proven universal GameView width.",
  },
  {
    id: "internal",
    label: "Full internal render-space view (481-wide debug)",
    logicalViewWidth: BATTLE_OFFSCREEN.width,
    sourceHeight: BATTLE_OFFSCREEN.height,
    presentationHeight: BATTLE_OFFSCREEN.height,
    evidence: "UNKNOWN",
    note:
      "The whole 481x197 internal buffer with no window crop and no vertical presentation - the page's previous behaviour. Comparison/debug only: this is not the framing the original battle shows.",
  },
];

export const DEFAULT_VIEW_PROFILE_ID = "recording";

export function viewProfileById(id: string): ViewProfile {
  return VIEW_PROFILES.find((profile) => profile.id === id) ?? VIEW_PROFILES[0];
}

/**
 * CONFIRMED native horizontal placement of the 480-wide backdrop inside the logical battle space
 * (`BattleForm.DynamicRender` 0x16aad2c: `sub w8, w23, #0x1e0` then `asr w2, w8, #0x1`).
 */
export function backgroundXForViewWidth(logicalViewWidth: number): number {
  return Math.trunc((logicalViewWidth - BATTLE_BACKDROP.width) / 2);
}

/**
 * The bg_100 source columns a window of `logicalViewWidth` logical px exposes, derived from the
 * placement above (background occupies [backgroundX, backgroundX + 480]).
 */
export function visibleBackgroundInterval(logicalViewWidth: number): { from: number; to: number } {
  const backgroundX = backgroundXForViewWidth(logicalViewWidth);
  return {
    from: Math.max(0, -backgroundX),
    to: Math.min(BATTLE_BACKDROP.width, logicalViewWidth - backgroundX),
  };
}

export function verticalPresentationRatio(profile: ViewProfile): number {
  return profile.presentationHeight / profile.sourceHeight;
}

/* ------------------------------------------------------------------ */
/* Recovered human battle image state (native)                          */
/* ------------------------------------------------------------------ */

/**
 * A battle human's `ecs.HumanComponent.imgIds` is an `int[17]` (allocation 0x1476F58/F64 in
 * `World.CreateHuman 0x1476854`, filled 0x1476F7C-0x1477050, passed as `Entity.AddHuman` argument
 * #9 and stored at `HumanComponent +0x20`). Each slot is seeded from the job/appearance record and
 * the two equipped item records; the consumer (`AnimationSet.SetHumanImgs 0x165EA88`) later maps a
 * 14-line image-index row through this array (see HUMAN_IMAGE_PIPELINE).
 *
 * The slot indices are the game's own `IMG_*` index space; three of them are independently
 * corroborated by the creation sources (job `imgFoots` -> IMG_FOOT, `imgHeads` -> IMG_FACE,
 * `imgHands` -> IMG_HAND) and by the battle-time writers (IMG_SHOES forced to -2, weapon/shield
 * rewritten from `EquipData.img`).
 */
export type HumanImageSlot = {
  index: number;
  /** metadata-verified IMG_* name, or null when the constant set names no slot for this index */
  kind: string | null;
  /** creation rule as recovered from the native instructions */
  rule: string;
  evidence: EvidenceLevel;
  note?: string;
};

/**
 * `flag` is `World.CreateHuman`'s 4th integer parameter (w4, ARM64: `this` = x0, floats = s0..s2),
 * `gender` is the 3rd (w3 - the array selector), `job` is the captured `data.JobData`.
 * `EquipData.img` is the equipment row's image id at metadata offset +0x60.
 */
export const HUMAN_IMAGE_SLOTS: HumanImageSlot[] = [
  { index: 0, kind: null, rule: "0", evidence: "CONFIRMED" },
  {
    index: 1,
    kind: null,
    rule: "(flag & 0x80) != 0 ? 1 : job.imgBodys[gender]",
    evidence: "CONFIRMED",
  },
  { index: 2, kind: "IMG_FOOT", rule: "job.imgFoots[gender]", evidence: "CONFIRMED", note: "JobData +0x88" },
  { index: 3, kind: "IMG_SHOES", rule: "-2 (skip)", evidence: "CONFIRMED", note: "re-forced to -2 at battle setup (FighterSystem.InitFighters 0x158327C)" },
  { index: 4, kind: "IMG_FACE", rule: "job.imgHeads[gender]", evidence: "CONFIRMED", note: "JobData +0x58" },
  { index: 5, kind: "IMG_MOUTH", rule: "-1 (SEB fallback)", evidence: "CONFIRMED" },
  { index: 6, kind: "IMG_EYE", rule: "(flag & 0x80) != 0 ? 6 : -1", evidence: "CONFIRMED" },
  { index: 7, kind: "IMG_HAIR", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 8, kind: "IMG_HAT", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 9, kind: "IMG_ACCESSARY", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 10, kind: "IMG_HAND", rule: "job.imgHands[gender]", evidence: "CONFIRMED", note: "JobData +0x78" },
  {
    index: 11,
    kind: "IMG_WEAPON",
    rule: "equipX.img   (EquipData +0x60)",
    evidence: "SUPPORTED",
    note: "one of the two equipment lookups; the job-default predicate compares EquipData[+0x18] with JobData.weapon (+0x90). Later rewritten by AISystem.ChangeWeaponImage 0x148B6CC",
  },
  {
    index: 12,
    kind: "IMG_SHIELD",
    rule: "equipY.img   (EquipData +0x60)",
    evidence: "SUPPORTED",
    note: "the twin lookup, whose predicate compares EquipData[+0x18] with JobData.shield (+0x94). Later rewritten by AISystem.ChangeShieldImage 0x148B764",
  },
  { index: 13, kind: "IMG_LIFT", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 14, kind: "IMG_FURNITURE", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 15, kind: "IMG_VEHICLE", rule: "-2 (skip)", evidence: "CONFIRMED" },
  { index: 16, kind: "IMG_GADGET", rule: "-2 (skip)", evidence: "CONFIRMED" },
];

/** Slot names are the game's own constants; the ids they hold are per-character data. */
export const HUMAN_IMAGE_MODEL_EVIDENCE: Tagged<string> = {
  value: "creation-time imgIds = 17 image-kind slots, filled from the job record and the equipped items",
  evidence: "CONFIRMED",
  note: "imgIds is NOT built from JobData.skills (that list is a List<int> passed to Entity.AddSkill). Fresh length is the literal 0x11 = 17 = IMG_END. The descriptive meaning of the (flag & 0x80) bit is not established; it is kept as the raw condition.",
};

/**
 * Recovered inputs of `World.CreateHuman 0x1476854` that feed the image state. ARM64 passes the
 * three floats in the FP sequence, so the integer/pointer arguments start at w1 (this = x0).
 */
export const HUMAN_IMAGE_INPUTS: { name: string; detail: string; evidence: EvidenceLevel }[] = [
  {
    name: "job  (data.JobData)",
    detail:
      "captured record; supplies imgBodys (+0x68), imgFoots (+0x88), imgHeads (+0x58), imgHands (+0x78) and the default weapon/shield ids (+0x90 / +0x94)",
    evidence: "CONFIRMED",
  },
  {
    name: "gender  (w3)",
    detail: "the index into those per-gender job image arrays; the same value is passed on as Entity.AddHuman's `gender` argument",
    evidence: "CONFIRMED",
  },
  {
    name: "jobId  (w2)",
    detail: "passed to Entity.AddHuman; selects the job row used above",
    evidence: "CONFIRMED",
  },
  {
    name: "flag  (w4)",
    detail: "only bit 0x80 is used and only for slots 1 and 6; the descriptive meaning of the bit is not established",
    evidence: "CONFIRMED",
  },
  {
    name: "equipped weapon / shield",
    detail:
      "two data.Data.get_equipData() + Enumerable.FirstOrDefault lookups whose closure predicates match EquipData[+0x18] against JobData.weapon / JobData.shield; the stored value is EquipData.img (+0x60)",
    evidence: "SUPPORTED",
  },
  {
    name: "(flag ? 1/-1 and 6/-1, +6 constants)",
    detail: "slots 0, 3, 5, 7, 8, 9, 13-16 are creation constants (0 / -2 skip / -1 SEB fallback)",
    evidence: "CONFIRMED",
  },
];

/**
 * The consumer side. `SetHumanImgs` writes the human's two 14-line arrays from the
 * `HumanResourceSet` image-index tables, selecting a row with `HumanComponent.animType (+0x48;
 * not serialized)`, remapping each non-negative value through `imgIds` and passing -2/-1 through.
 * `image.texIds` / `image.resIds` are the *same array objects* as the human's front arrays, so the
 * refresh needs no copy step.
 */
export const HUMAN_IMAGE_PIPELINE: { step: string; detail: string; evidence: EvidenceLevel }[] = [
  { step: "1", detail: "World.CreateHuman builds job defaults → imgIds = new int[17]", evidence: "CONFIRMED" },
  { step: "2", detail: "Entity.AddHuman(arg #9) → HumanComponent +0x20 imgIds", evidence: "CONFIRMED" },
  {
    step: "3",
    detail: "Entity.AddImage(texIds, resIds, null…) → ImageComponent +0x18 / +0x10; those arrays are the SAME objects as the human's frontImgIds / frontResIds",
    evidence: "CONFIRMED",
  },
  {
    step: "4",
    detail: "battle setup: ChangeShieldImage → imgIds[12], ChangeWeaponImage → imgIds[11], imgIds[3] = -2, then SetHumanImgs(entity)",
    evidence: "CONFIRMED",
  },
  {
    step: "5",
    detail: "SetHumanImgs: frontImgIds[line] = (IMG_TABLE[animType][line] >= 0 ? imgIds[value] : value)",
    evidence: "SUPPORTED",
  },
  {
    step: "6",
    detail: "RenderSystem z-sorts one entry per SEB line and applies the per-line image override",
    evidence: "SUPPORTED",
  },
  {
    step: "7",
    detail: "HumanResourceSet row placement (which literal fills FRONT_IMG_IDS[animType] etc.) is still UNKNOWN, so the per-line art assignment is not applied",
    evidence: "UNKNOWN",
  },
];

/* ------------------------------------------------------------------ */
/* Dynamic formation: occupancy, advancement and reflow                */
/* ------------------------------------------------------------------ */

export const DYNAMIC_RULES: FormationRule[] = [
  {
    id: "occupancy",
    value: "a cell is free when its entity list holds no non-destroyed entity",
    source: "FighterSystem.IsEmptyCell 0x1588d78 -> GetCellEntities 0x1500e38 + predicates 0x15896ec / 0x1589714",
    direct: true,
    evidence: "CONFIRMED",
    note: "IsEmptyCell returns false as soon as Any(list, !IsDestroyed); the second predicate only adds the pending-state filter. GetCellEntities is the per-cell entity list.",
  },
  {
    id: "koStopsCounting",
    value: "units with HP < 1 stop counting for formation helpers",
    source: "FighterSystem.<GetLastRow>b__0 0x15899bc (Param lookup id 10 = HP, require >= 1)",
    direct: true,
    evidence: "SUPPORTED",
    note: "The last-row scan filters on HP >= 1. The exact moment a knocked-down entity leaves the cell list was not traced.",
  },
  {
    id: "advanceGate",
    value: "advance only when not in the front row AND the cell toward the enemy is free",
    source: "FighterSystem.IsAdvanceable 0x1588cb8 (only caller: DecideNextState 0x15839c0); IsMostFront 0x15884f8 = (grid + 4) < 9; IsFrontEmpty 0x1588f70",
    direct: true,
    evidence: "CONFIRMED",
    note: "IsFrontEmpty inverts the direction (Direction.Invert 0x145f3f4) and tests cellY + OFFSETS[inverted].y, i.e. one cell toward the enemy.",
  },
  {
    id: "oneCellPerStep",
    value: "movement is one adjacent cell at a time; the slot index is not stored separately",
    source: "FighterSystem.GetPath 0x1584310 / UpdateMoving 0x15845dc / ExitMoving 0x1584b90, and CellToGrid 0x158491c which rebuilds grid = ((cellY - offset - (dir==2?1:0)) / dirY) * 5 + cellX",
    direct: true,
    evidence: "SUPPORTED",
    note: "The inverse formula shows the AI grid key is derived from the current cell, so a unit keeps no separate original-slot field. Cascades therefore happen across successive updates.",
  },
  {
    id: "revivePosition",
    value: "a revived unit is sent to the queueing cell: one row behind the rear-most living unit of the emptiest column",
    source: "FighterSystem.OnCure 0x1587e64 -> GetQueueingPosition 0x1588078 -> GetLastRow 0x1588414 / GetRowOffset 0x15833c4 / CalcGridCell 0x158344c, then ChangeState 0x1583b48",
    direct: true,
    evidence: "CONFIRMED",
    note: "GetQueueingPosition scans columns 0..4, keeps the column with the smallest last row, then evaluates CalcGridCell(dir, offset, grid + 5); OnCure then changes state, so the unit walks to that cell.",
  },
  {
    id: "noBackwardRestore",
    value: "no rule that moves units backward to restore the original ordering was found",
    source: "search across FighterSystem movement/placement methods",
    direct: false,
    evidence: "UNKNOWN",
    note: "Only forward advancement and the queueing position were found. A backward restore may exist in an untraced system.",
  },
  {
    id: "advanceOrder",
    value: "per-tick order when several units may advance at once",
    source: "not traced",
    direct: false,
    evidence: "UNKNOWN",
    note: "The replay advances at most one cell per beat for each eligible unit and reports the ordering as unresolved.",
  },
];

export type UnitDynamicState = {
  index: number;
  side: "ally" | "enemy";
  /** slot the unit was assigned at InitFighters */
  original: Slot;
  /** cell the unit occupies now */
  cell: Slot;
  alive: boolean;
  /** true when this beat changed the cell */
  movedThisBeat: boolean;
  note?: string;
};

export type BeatState = {
  beat: number;
  units: UnitDynamicState[];
  /** cells that are free at the end of this beat */
  free: Slot[];
};

export function cellKey(slot: Slot): string {
  return `${slot.column}:${slot.row}`;
}

/**
 * Occupancy/reflow simulation using only the recovered rules.
 *
 * Per beat: apply KO/revive marks, then let every living unit advance at most
 * one cell if `advanceGate` allows it (not front row + front cell free). A
 * revived unit is placed on the queueing cell and then advances normally.
 *
 * The per-tick order between several advanceable units is UNKNOWN, so this
 * simulation is a rule-faithful approximation, not a native trace.
 */
export function simulateOccupancy(encounter: BattleEncounter): BeatState[] {
  /* the shared initial formation, so the occupancy start state cannot drift from the static layout */
  const { rowOffset: offset, allySlots, enemySlots } = nativeInitialFormation(
    encounter.allies.length,
    encounter.enemies.length,
  );
  const states: UnitDynamicState[] = [
    ...encounter.enemies.map((_, index) => ({
      index,
      side: "enemy" as const,
      original: enemySlots[index],
      cell: enemySlots[index],
      alive: true,
      movedThisBeat: false,
    })),
    ...encounter.allies.map((_, index) => ({
      index,
      side: "ally" as const,
      original: allySlots[index],
      cell: allySlots[index],
      alive: true,
      movedThisBeat: false,
    })),
  ];

  const at = (side: "ally" | "enemy", index: number) =>
    states.find((unit) => unit.side === side && unit.index === index);

  const occupants = (): Map<string, UnitDynamicState> => {
    const map = new Map<string, UnitDynamicState>();
    for (const unit of states) if (unit.alive) map.set(cellKey(unit.cell), unit);
    return map;
  };

  const queueingCell = (side: "ally" | "enemy", targetIndex: number): Slot =>
    nativeQueueingPosition({
      side,
      targetIndex,
      units: states,
      rowOffset: offset,
    }).cell;

  // direction from a unit toward the enemy, in cell rows
  const forward = (side: "ally" | "enemy") => (side === "ally" ? -1 : 1);
  const frontRow = (side: "ally" | "enemy", row: number) =>
    side === "ally" ? row === offset + 1 : row === offset;

  const beats: BeatState[] = [];
  for (let beat = 0; beat <= encounter.timeline.length; beat += 1) {
    for (const unit of states) unit.movedThisBeat = false;
    const event = beat > 0 ? encounter.timeline[beat - 1] : undefined;
    let revivedTarget: UnitDynamicState | null = null;

    if (event) {
      const target = at(event.targetSide, event.target);
      if (target && event.ko) {
        target.alive = false;
        target.note = `KO on beat ${beat}`;
      }
      if (target && event.revive) {
        target.alive = true;
        const cell = queueingCell(target.side, target.index);
        target.cell = cell;
        target.note = `revived to queueing cell c${cell.column}r${cell.row}`;
        /*
         * Native OnCure changes straight to state 2; the revived unit walks to this cell under
         * UpdateMoving and does not also take the ordinary formation advancement pass on the beat
         * that revived it.
         */
        revivedTarget = target;
      }
    }

    // advancement pass (one cell per beat, gate = not front row + front cell free)
    const occupied = occupants();
    for (const unit of states) {
      if (!unit.alive) continue;
      if (unit === revivedTarget) continue;
      const step = forward(unit.side);
      const next: Slot = { column: unit.cell.column, row: unit.cell.row + step };
      if (frontRow(unit.side, unit.cell.row)) continue;
      if (occupied.has(cellKey(next))) continue;
      occupied.delete(cellKey(unit.cell));
      unit.cell = next;
      unit.movedThisBeat = true;
      occupied.set(cellKey(next), unit);
    }

    beats.push({
      beat,
      units: states.map((unit) => ({ ...unit, cell: { ...unit.cell }, original: { ...unit.original } })),
      free: [
        ...Array.from({ length: FORMATION_CONSTANTS.columns }, (_, column) =>
          Array.from({ length: offset + 3 }, (_, row) => ({ column, row })),
        ).flat(),
      ].filter((slot) => !occupants().has(cellKey(slot))),
    });
  }
  return beats;
}

/**
 * Recovered `FighterSystem.GetQueueingPosition 0x1588078`.
 *
 * Despite its name, `GetLastRow 0x1588414` is a `Count`, not a maximum row: it counts living units
 * whose grid column matches the requested column. The queue picks the first column with the smallest
 * count, builds `column + 5*(count + 1)`, and converts that grid index through `CalcGridCell` with
 * the fixed `teams[1]` row offset. `targetIndex` is excluded because the revived unit's own
 * `board[7]` is the `-1` written by `EnterKnockingDown`.
 */
export type NativeQueueingPosition = {
  cell: Slot;
  world: { x: number; y: number; z: number };
  sourceColumn: number;
  sourceCount: number;
  index: number;
};

export function nativeQueueingPosition(input: {
  side: "ally" | "enemy";
  targetIndex: number;
  units: readonly UnitDynamicState[];
  rowOffset: number;
}): NativeQueueingPosition {
  const { side, targetIndex, units, rowOffset } = input;
  const living = units.filter(
    (unit) => unit.side === side && unit.index !== targetIndex && unit.alive,
  );
  const counts = Array.from({ length: FORMATION_CONSTANTS.columns }, (_, column) =>
    living.filter((unit) => unit.cell.column === column).length,
  );
  let bestColumn = 0;
  let bestCount = counts[0];
  for (let column = 1; column < FORMATION_CONSTANTS.columns; column += 1) {
    if (counts[column] < bestCount) {
      bestCount = counts[column];
      bestColumn = column;
    }
  }
  const index = bestColumn + FORMATION_CONSTANTS.columns * (bestCount + 1);
  const row = Math.trunc(index / FORMATION_CONSTANTS.columns);
  const column = index - row * FORMATION_CONSTANTS.columns;
  const cell: Slot =
    side === "ally"
      ? { column, row: rowOffset + 1 + row }
      : { column, row: rowOffset - row };
  return {
    cell,
    world: {
      x: column * FORMATION_CONSTANTS.cellSize,
      y: 0,
      z: cell.row * FORMATION_CONSTANTS.cellSize,
    },
    sourceColumn: bestColumn,
    sourceCount: bestCount,
    index,
  };
}

/* ------------------------------------------------------------------ */
/* Encounter content (the only part that differs between the fights)    */
/* ------------------------------------------------------------------ */

export type BattleUnit = {
  /** monster sprite id from src/game-data/monster-sprites.json, or null for a human ally */
  monsterId: number | null;
  /** display label; for monsters prefer the canonical name from monster-sprites.json */
  label?: string;
  /** only used for human allies rendered through the character preview */
  jobName?: string;
  rank?: string;
  isBoss?: boolean;
};

export type BattleEventKind =
  | "attack"
  | "critical"
  | "missed"
  | "guard"
  | "dodge"
  | "weak"
  | "def-down"
  | "sleep"
  | "skill"
  | "heal";

export type BattleEvent = {
  /** which unit acts; index into the matching roster */
  actor: number;
  side: "ally" | "enemy";
  target: number;
  targetSide: "ally" | "enemy";
  kind: BattleEventKind;
  /** damage/heal amount for number overlays */
  amount?: number;
  /** skill id for the balloon layer */
  skillId?: number;
  skillName?: string;
  /** target is knocked out by this beat (scenario input, not a native formula) */
  ko?: boolean;
  /** target is revived by this beat (scenario input) */
  revive?: boolean;
};

export type BattleEncounter = {
  id: string;
  /** Kairo Room and Wairo Dungeon are content families, not renderers. */
  family: "kairo-room" | "wairo-dungeon";
  title: string;
  difficulty: "Easy" | "Normal" | "Hard" | "Extreme";
  level: number;
  bossLevel: number;
  /** original MapChip that the encounter appears on */
  chipId: number;
  allies: BattleUnit[];
  enemies: BattleUnit[];
  timeline: BattleEvent[];
  source: string;
};

const HUMAN_ALLY_POOL: BattleUnit[] = [
  { monsterId: null, jobName: "Guard", rank: "D" },
  { monsterId: null, jobName: "Archer", rank: "C" },
];

function monster(id: number, isBoss = false): BattleUnit {
  return { monsterId: id, isBoss };
}

function roster(bossId: number, followerIds: number[]): BattleUnit[] {
  // CONFIRMED order: CreateBossBattle concatenates the follower members first and
  // the boss clone last, and the slot formula sends the last member to column 0 of
  // the deepest row (the top-left of the opponent block, where the BOSS banner is
  // in both recordings).
  return [...followerIds.map((id) => monster(id)), monster(bossId, true)];
}

/**
 * CONFIRMED rosters: follower monster ids in original table order plus the boss,
 * read from the extracted SpecialBoss rows (RE-evidence/20260912-combat/
 * encounters.json, ids 3 / 15 / 16 / 19). Every follower row in these records
 * has check rate 100, so the lineup is the full table list. Names are resolved
 * from the canonical src/game-data/monster-sprites.json at render time.
 */
export const BATTLE_ENCOUNTERS: BattleEncounter[] = [
  {
    id: "kairo-knight-extreme",
    family: "kairo-room",
    title: "Vs. Kairobot Knight (Extreme)",
    difficulty: "Extreme",
    level: 250,
    bossLevel: 500,
    chipId: 261,
    allies: HUMAN_ALLY_POOL,
    enemies: roster(135, [
      119, 119, 119, 119, 119, 119, 119, 121, 119, 119, 119, 122, 119, 119, 119, 114, 119, 119, 119, 106,
    ]),
    timeline: [
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "attack", amount: 412 },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 268 },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "missed" },
      { actor: 0, side: "ally", target: 0, targetSide: "enemy", kind: "critical", amount: 1487 },
      { actor: 2, side: "enemy", target: 1, targetSide: "ally", kind: "skill", skillId: 24, skillName: "4-Hit Attack" },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 940, ko: true },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "guard" },
      { actor: 0, side: "ally", target: 2, targetSide: "enemy", kind: "def-down", ko: true },
      { actor: 3, side: "enemy", target: 1, targetSide: "ally", kind: "sleep" },
      { actor: 1, side: "ally", target: 0, targetSide: "ally", kind: "heal", amount: 155, revive: true },
    ],
    source: "KA_assets/xls/English.lproj/SpecialBoss.txt row 3 + 20260912-combat/encounters.json",
  },
  {
    id: "kairo-kommander-extreme",
    family: "kairo-room",
    title: "Vs. Kairo Kommander (Extreme)",
    difficulty: "Extreme",
    level: 300,
    bossLevel: 600,
    chipId: 261,
    allies: HUMAN_ALLY_POOL,
    enemies: roster(
      138,
      [119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 118],
    ),
    timeline: [
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 355 },
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "attack", amount: 288 },
      { actor: 2, side: "enemy", target: 0, targetSide: "ally", kind: "skill", skillId: 35, skillName: "Area Attack III" },
      { actor: 1, side: "enemy", target: 1, targetSide: "ally", kind: "dodge" },
      { actor: 0, side: "ally", target: 0, targetSide: "enemy", kind: "critical", amount: 1204 },
      { actor: 4, side: "enemy", target: 2, targetSide: "enemy", kind: "heal", amount: 210 },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 1220 },
      { actor: 0, side: "ally", target: 3, targetSide: "enemy", kind: "weak", amount: 733 },
    ],
    source: "KA_assets/xls/English.lproj/SpecialBoss.txt row 15 + 20260912-combat/encounters.json",
  },
  {
    id: "wairo-mage",
    family: "wairo-dungeon",
    title: "Wairo Raid Dungeon appeared!",
    difficulty: "Extreme",
    level: 15,
    bossLevel: 30,
    chipId: 223,
    allies: HUMAN_ALLY_POOL,
    enemies: roster(139, [116, 111, 109, 117, 112]),
    timeline: [
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "attack", amount: 190 },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "missed" },
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "skill", skillId: 11, skillName: "Ice Magic II" },
      { actor: 2, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 96 },
      { actor: 0, side: "ally", target: 0, targetSide: "enemy", kind: "critical", amount: 640 },
    ],
    source: "KA_assets/xls/English.lproj/SpecialBoss.txt row 16 + 20260912-combat/encounters.json",
  },
  {
    id: "wairo-tank",
    family: "wairo-dungeon",
    title: "Take out the Wairo Tank!",
    difficulty: "Extreme",
    level: 80,
    bossLevel: 160,
    chipId: 223,
    allies: HUMAN_ALLY_POOL,
    enemies: roster(142, [
      116, 116, 116, 116, 116, 116, 116, 121, 116, 116, 116, 122, 116, 116, 116, 114, 116, 116, 116, 106,
    ]),
    timeline: [
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "attack", amount: 402 },
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "attack", amount: 260 },
      { actor: 0, side: "ally", target: 1, targetSide: "enemy", kind: "guard" },
      { actor: 2, side: "enemy", target: 0, targetSide: "ally", kind: "skill", skillId: 35, skillName: "Area Attack III" },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "sleep" },
      { actor: 0, side: "ally", target: 0, targetSide: "enemy", kind: "critical", amount: 1810 },
      { actor: 1, side: "enemy", target: 0, targetSide: "ally", kind: "def-down" },
    ],
    source: "KA_assets/xls/English.lproj/SpecialBoss.txt row 19 + 20260912-combat/encounters.json",
  },
];

/* ------------------------------------------------------------------ */
/* Canonical monster lookup (single source: monster-sprites.json)       */
/* ------------------------------------------------------------------ */

const monsterById = new Map(
  (monsterSprites as { id: number; name: string; src: string; width: number; height: number }[]).map(
    (sprite) => [sprite.id, sprite],
  ),
);

export function monsterSpriteById(id: number | null) {
  return id === null ? undefined : monsterById.get(id);
}

export function encounterById(id: string): BattleEncounter {
  return BATTLE_ENCOUNTERS.find((entry) => entry.id === id) ?? BATTLE_ENCOUNTERS[0];
}

/* ------------------------------------------------------------------ */
/* Fighter animation path (recovered)                                  */
/* ------------------------------------------------------------------ */

export type AnimationStateId =
  | "idle"
  | "walk"
  | "attack"
  | "skill"
  | "hit"
  | "knockdown"
  | "leaving";

export type AnimationStateRule = {
  id: AnimationStateId;
  /** behaviour id passed to AnimationSet.ChangeAnimation */
  behavior: number | "computed";
  source: string;
  /** human clip base (clip = base + direction) when known */
  humanBase?: number;
  humanSeb?: string;
  monsterBase?: number;
  monsterSeb?: string;
  evidence: EvidenceLevel;
  note: string;
};

/**
 * CONFIRMED behaviour ids: each FighterSystem state handler tail-calls
 * AnimationSet.ChangeAnimation 0x165dea8 with these immediates (read this pass).
 * Human bases come from the original humanAnimationSebBases table; monster bases
 * from AnimationSet.cctor (0 = wait group, 16 = attack group, +4 for size 3).
 */
export const ANIMATION_STATE_RULES: AnimationStateRule[] = [
  {
    id: "idle",
    behavior: 3,
    source: "FighterSystem.EnterWaiting 0x15840f0 (mov w1,#3) and EnterCharging 0x1584b90; ExitUsingSkill 0x1585d2c",
    humanBase: 12,
    humanSeb: "chara/equip_wait_* .seb (12-15)",
    monsterBase: 0,
    monsterSeb: "monster/monster_s_wait_*.seb (0-3)",
    evidence: "CONFIRMED",
    note: "The equipped wait animation is used, so battle idle = world 'equip_wait'.",
  },
  {
    id: "walk",
    behavior: 2,
    source: "FighterSystem.EnterMoving 0x1584194 (mov w1,#2)",
    humanBase: 8,
    humanSeb: "chara/equip_walk_* .seb (8-11)",
    monsterBase: 0,
    monsterSeb: "monster/monster_s_wait_*.seb (0-3)",
    evidence: "CONFIRMED",
    note: "Monsters have no walk group in the recovered base table, so a monster reuses its wait clip while moving (SUPPORTED).",
  },
  {
    id: "attack",
    behavior: "computed",
    source: "FighterSystem.EnterAttacking 0x15854c0 (mov w1,w20) — behaviour chosen from weapon/attack type",
    humanBase: 16,
    humanSeb: "chara/attack_sword_* .seb (16-19); magic 48, gun 56, bow 68, spear 72, scoop 96",
    monsterBase: 16,
    monsterSeb: "monster/monster_s_attack_*.seb (16-19)",
    evidence: "SUPPORTED",
    note: "The immediate is a register, so the exact per-weapon behaviour is not decoded; the base table plus the weapon SEB names give the candidate set.",
  },
  {
    id: "skill",
    behavior: "computed",
    source: "FighterSystem.EnterUsingSkill 0x1585a78 (mov w1,w20)",
    humanBase: 48,
    humanSeb: "chara/attack_magic_* .seb (48-51) for the magic-looking skill motions",
    monsterBase: 16,
    monsterSeb: "monster/monster_s_attack_*.seb (16-19)",
    evidence: "UNKNOWN",
    note: "All 136 exported skill motions map to behaviours 0/2/4/5/10/11/31, i.e. the same weapon groups rather than a separate skill group.",
  },
  {
    id: "hit",
    behavior: 30,
    source: "FighterSystem.EnterDamaging 0x1585e34 (mov w1,#0x1e = 30)",
    humanBase: 188,
    humanSeb: "chara/equip_damege2_* .seb (188-191)",
    monsterBase: 0,
    monsterSeb: "monster/monster_s_wait_*.seb (0-3)",
    evidence: "CONFIRMED",
    note: "Monsters fall back to the wait group because only behaviour 4 has a monster base.",
  },
  {
    id: "knockdown",
    behavior: 28,
    source: "FighterSystem.EnterKnockingDown 0x1586444 (mov w1,#0x1c = 28)",
    humanBase: 44,
    humanSeb: "chara/equip_sit_* .seb (44-47)",
    monsterBase: 0,
    monsterSeb: "monster/monster_s_wait_*.seb (0-3)",
    evidence: "CONFIRMED",
    note: "The knock-down pose is the equipped 'sit' animation plus the native smoke effect (resource 28 seb 0, Y+10).",
  },
  {
    id: "leaving",
    behavior: 28,
    source: "FighterSystem.EnterLeaving 0x1586a80 — no ChangeAnimation call found in the handler",
    humanBase: 44,
    humanSeb: "the unchanged down pose; EnterLeaving makes no ChangeAnimation call and its AddAnimation flag is zero",
    monsterBase: 0,
    monsterSeb: "unknown",
    evidence: "CONFIRMED",
    note:
      "The unit keeps the static down pose while it becomes its own kind-10 projectile. UpdateLeaving " +
      "adds 2 to the direction each update, alternating down_right.seb and its ,u mirror.",
  },
];

/**
 * Native monster animation timing (CONFIRMED - replaces the temporary 20 Hz stand-in).
 *
 * Frame advance (AnimationSystem.Update 0x14DC884, called once per `World.Update` 0x147B240 ->
 * `SystemManager.Update` 0x14C1B08, i.e. once per rendered frame):
 *     SebComponent.frame += AnimationComponent.rate
 *     if (frame >= SEB.maxFrame_) frame %= maxFrame_        (the clip loops)
 *     ... then ChangeAnimation(AnimationComponent.backupBehavior) when it is not -1.
 *
 * * `AnimationComponent.rate` (int, +0x10) is an `Entity.AddAnimation(entity, rate, backupBehavior)`
 *   argument; `World.CreateMonster 0x1477A68` passes **rate = 1**, backupBehavior = -1, so a monster
 *   advances exactly one SEB frame per update and never auto-transitions.
 * * `SebComponent.frame` (int, +0x18) and the SEB's `maxFrame_` (+0x1C) are the wrap pair.
 * * The update frequency is the game's target frame rate: `main.Main.OnUpdate 0x169401C` sets
 *   `QualitySettings.vSyncCount = 0` and `Application.targetFrameRate = FormManagerBase.GetTargetFps
 *   0x2381B98`, which returns the top form's `frameRate_` (+0x4C, -1 for the battle form ->
 *   not used) or the manager default `tagFps_` (+0xB0) = **20** (form.FormManager..ctor 0x16B8E6C
 *   writes 0x14 there). 20 updates/s -> **50 ms per SEB frame**. (120 while a form change is in
 *   progress; the per-frame update is frame-rate dependent if the device cannot reach the target.)
 *
 * So a 20-frame monster wait clip cycles once per second and a 10-frame attack clip per 500 ms.
 * `ChangeAnimation 0x165DEA8` writes `SebComponent.id = sebBase + direction` and
 * `SebComponent.frame = 0`, i.e. every animation change restarts at frame 0 and each unit keeps its
 * own counter - units that changed animation at different updates are out of phase.
 */
export const NATIVE_ANIMATION = {
  evidence: "CONFIRMED" as EvidenceLevel,
  targetFps: 20,
  frameMs: 50,
  monsterRate: 1,
  wrap: "frame %= SEB.maxFrame_ (AnimationSystem.Update)",
  reset: "ChangeAnimation sets SebComponent.frame = 0 (0x165DEA8)",
  note:
    "20 updates/s x rate 1 = one SEB frame every 50 ms; the wait clip loops once per second and the " +
    "attack clip every 500 ms. The frame advance is per rendered frame, so a device below the target " +
    "frame rate slows the animation down.",
} as const;

/** Recovered replacement for the temporary 20 Hz stand-in (equivalent value, now derived). */
export const NATIVE_FRAME_MS = NATIVE_ANIMATION.frameMs;

/**
 * The animation frame a unit shows: native resets the counter to 0 on every animation change and
 * adds `rate` once per update, wrapping at the clip's `maxFrame`.
 */
export function nativeAnimationFrame(
  clip: { maxFrame: number },
  ticksSinceAnimationChange: number,
  rate: number = NATIVE_ANIMATION.monsterRate,
): number {
  const loop = Math.max(1, clip.maxFrame);
  const ticks = Math.max(0, Math.floor(ticksSinceAnimationChange)) * Math.max(0, Math.floor(rate));
  return ((ticks % loop) + loop) % loop;
}

type RawFrame = {
  /** SEB layer index this record belongs to (0 = shadow, 1 = body for monster clips). */
  line: number;
  frame: number;
  tex: number;
  u: number;
  v: number;
  w: number;
  h: number;
  transX: number;
  transY: number;
  reversU: number;
  reversV: number;
};

export type DecodedClip = {
  id: string;
  group: string;
  seb: string;
  maxFrame: number;
  layers: number;
  frames: RawFrame[];
  textures: Record<string, string>;
};

const clips = (battleAnimation as { clips: Record<string, {
  group: string;
  seb: string;
  maxFrame: number;
  layers: number;
  frames: RawFrame[];
  textures: Record<string, string>;
}> }).clips;

export const MONSTER_CLIPS: Record<"wait" | "waitLeft" | "attack" | "attackLeft", DecodedClip> = {
  wait: { id: "monsterWait", ...clips.monsterWait },
  waitLeft: { id: "monsterWaitLeft", ...clips.monsterWaitLeft },
  attack: { id: "monsterAttack", ...clips.monsterAttack },
  attackLeft: { id: "monsterAttackLeft", ...clips.monsterAttackLeft },
};

export type MonsterDirectionId = "up" | "right" | "down" | "left";

/**
 * Native monster direction -> SEB variant (CONFIRMED).
 *
 * `kairo.unity.math.Direction` (dump.cs TypeDefIndex 512) is UP=0, RIGHT=1, DOWN=2, LEFT=3, and
 * the animation clip is `groupBase + direction` (AnimationSet.GetBehaviorBySebId 0x165DDD8 clears
 * the low two bits: `id & ~3` = group base, `id & 3` = direction). `monster/seb.inf` lists exactly
 * four variants per wait/attack group, and the loop is on the *asset* plus a load flag:
 *
 *   0 monster_s_wait_up.seb      (UP)      1 monster_s_wait_right.seb      (RIGHT)
 *   2 monster_s_wait_right.seb,u (DOWN)    3 monster_s_wait_up.seb,u       (LEFT)
 *  16 monster_s_attack_up.seb   (UP)     17 monster_s_attack_right.seb   (RIGHT)
 *  18 monster_s_attack_right.seb,u (DOWN) 19 monster_s_attack_up.seb,u    (LEFT)
 *
 * So DOWN/LEFT are the RIGHT/UP assets loaded with the `,u` flag; there are no separate DOWN/LEFT
 * .seb files, and the records themselves carry reversU/reversV = 0. The flag reaches the sprite
 * through `Seb.Flip` (+0xB0): `Seb.GetSprite` 0x2351CA0 inverts the converted sprite's reversU
 * (sprite field 8) when Flip is set, and `AppData.DrawZSortObjects` 0x167110C turns the sprite's
 * reversU/reversV into the Graphics mirror mode that `Graphics.DrawImage` 0x2305030 resolves to
 * scaleX = -1 (bit 0) / scaleY = -1 (bit 1) about the *destination rect centre*. Mirroring is
 * therefore per resource (all lines of the Seb), applied at draw time, and it never moves dx/dy.
 *
 * The `,u` -> `Seb.Flip` write itself was not located (the setter 0x2352EF0 has no direct callers
 * and +0xB0 is only read inside GetSprite/GetSpriteLocal), so that single link is SUPPORTED.
 */
export const MONSTER_DIRECTION_VARIANTS: Record<
  MonsterDirectionId,
  {
    /** Direction constant (UP 0 / RIGHT 1 / DOWN 2 / LEFT 3) */
    index: 0 | 1 | 2 | 3;
    /** which of the two assets the variant loads */
    asset: "up" | "right";
    /** the `,u` flag in monster/seb.inf (none for the two un-mirrored variants) */
    assetFlags: string[];
    /** mirror state the flag produces on every SEB line of the resource */
    mirror: { u: boolean; v: boolean };
    evidence: EvidenceLevel;
    note: string;
  }
> = {
  up: {
    index: 0,
    asset: "up",
    assetFlags: [],
    mirror: { u: false, v: false },
    evidence: "CONFIRMED",
    note: "seb.inf 0/16 -> monster_s_(wait|attack)_up.seb, no load flag, no mirror",
  },
  right: {
    index: 1,
    asset: "right",
    assetFlags: [],
    mirror: { u: false, v: false },
    evidence: "CONFIRMED",
    note: "seb.inf 1/17 -> monster_s_(wait|attack)_right.seb, no load flag, no mirror",
  },
  down: {
    index: 2,
    asset: "right",
    assetFlags: ["u"],
    mirror: { u: true, v: false },
    evidence: "SUPPORTED",
    note:
      "seb.inf 2/18 -> the _right asset with the ,u flag; the flag is the u-axis (horizontal) " +
      "mirror, which Seb.Flip applies to every line. The exact suffix parser is not located.",
  },
  left: {
    index: 3,
    asset: "up",
    assetFlags: ["u"],
    mirror: { u: true, v: false },
    evidence: "SUPPORTED",
    note:
      "seb.inf 3/19 -> the _up asset with the ,u flag (horizontal mirror), same mechanism as DOWN",
  },
};

/**
 * Entity direction of each battle team (CONFIRMED). `FighterSystem.InitFighters` 0x15830E8 stores
 * `Direction.Invert(slot direction)` on the entity's direction component, and the slot direction is
 * DOWN(2) for team 0 and UP(0) for team 1 (formation pass: `CalcGridCell` row sign uses
 * `Direction.OFFSETS[dir].y`). `Invert 0x145F3F4` is `(dir + 2) % 4`, so team 0 -> UP(0) and
 * team 1 -> DOWN(2).
 */
export const BATTLE_DIRECTIONS: Record<"ally" | "enemy", MonsterDirectionId> = {
  ally: "up",
  enemy: "down",
};

/**
 * The clip a monster draws for a state and direction. `MONSTER_CLIPS.waitLeft`/`attackLeft` hold the
 * *_right* asset (the key name is a historical misnomer kept so the validated evidence and the
 * exported manifest keys stay stable).
 */
export function monsterClipForDirection(
  state: "wait" | "attack",
  direction: MonsterDirectionId,
): DecodedClip {
  const { asset } = MONSTER_DIRECTION_VARIANTS[direction];
  if (state === "attack") return asset === "up" ? MONSTER_CLIPS.attack : MONSTER_CLIPS.attackLeft;
  return asset === "up" ? MONSTER_CLIPS.wait : MONSTER_CLIPS.waitLeft;
}

/** SEB line indices. Every ordinary monster SEB has two layers, drawn in ascending order. */
export const MONSTER_LINES = { shadow: 0, body: 1 } as const;

/**
 * Seb.GetSprite(frameNo, lineNo) 0x2351CA0 - CONFIRMED selection rule (frozen build
 * 39257e72291d; annotated listing in RE-evidence/20260918-battle-visual-replay/seb-lookup):
 *
 *  1. the runtime wraps Seb.frame into [0, maxFrame) before the draw call
 *     (AnimationSystem.Update 0x14dc884: frame += Animation.rate, frame %= maxFrame).
 *  2. each SEB line owns a sorted key list; key i maps to the line's record i.
 *     frame < keys[first] or frame > keys[last]  ->  no sprite for that line (0x2351fb4/0x23551fcc).
 *  3. frame == keys[i]  ->  that record, copied verbatim (0x23552134 -> ConvBufferToSprite).
 *  4. keys[i-1] < frame < keys[i]  ->  the previous record's values persist. The native code
 *     copies every field from the previous record while its texId is >= 0 (0x23552254
 *     `tbnz w10,#0x1f`); only records with a negative texId (runtime-supplied image) tween their
 *     transform values between the two keys. Every record of the ordinary monster SEBs carries a
 *     real texId (0 = shadow_s.png, 4 = mizu_shadow_s.png, 15 = iwa_monster_s_00.png), so the
 *     crop values persist unchanged until the next key.
 *  5. frameNo >= 10000 marks the flipped variant (the value passed as `9999 < frame` to
 *     ConvBufferToSprite); the replay does not use that path. Line 0 is the shadow layer, line 1
 *     the body layer, and each line is looked up independently - the two lines can (and do)
 *     select different cells.
 *
 * The crop rectangle the renderer draws is the selected record's own u/v/w/h; u/v are the logical
 * atlas origin of the SEB cell, not a "pose" name.
 */
export const SEB_SPRITE_SELECTION_RULE = {
  evidence: "CONFIRMED" as EvidenceLevel,
  method: "kairo.unity.ui.Seb.GetSprite 0x2351CA0",
  pseudocode:
    "phase = wrap(frame, 0, maxFrame); records = line's key/record list; " +
    "if phase < records[0].key or phase > records[last].key -> none; " +
    "else pick the last key <= phase (values persist; exact key copies verbatim)",
  note:
    "Frame wrap happens in AnimationSystem.Update before the draw. The replay now feeds this lookup " +
    "the recovered per-unit frame (NATIVE_ANIMATION: 1 frame per update, 20 updates/s, restart at 0 " +
    "on every animation change).",
} as const;

/** Records of one SEB line, in file order (their `frame` values are the line's sorted key list). */
export function sebLineRecords(clip: DecodedClip, line: number): RawFrame[] {
  return clip.frames.filter((record) => record.line === line);
}

/** The wrapped SEB frame the runtime would hold for a tick: `Seb.frame` after AnimationSystem.Update. */
export function clipFramePhase(clip: DecodedClip, tick: number): number {
  const loop = Math.max(1, clip.maxFrame);
  return ((Math.floor(tick) % loop) + loop) % loop;
}

/**
 * The record `Seb.GetSprite(frame, line)` returns for a monster clip, or null when the frame is
 * outside that line's key range (native returns 0 / draws nothing for that line).
 */
export function sebSpriteAt(clip: DecodedClip, frame: number, line: number): RawFrame | null {
  const records = sebLineRecords(clip, line);
  if (records.length === 0) return null;
  const phase = clipFramePhase(clip, frame);
  if (phase < records[0].frame || phase > records[records.length - 1].frame) return null;
  let current = records[0];
  for (const record of records) {
    if (record.frame <= phase) current = record;
    else break;
  }
  return current;
}

/**
 * Frame geometry for one animation tick.
 *
 * The SEB key list defines which frames the clip actually contains and the
 * per-frame translation offsets that the original draw call applies
 * (`DrawScaledSeb(..., sebId, frame, layer, scale, imgId, anchor)`). The replay
 * uses those recovered offsets so the motion is not invented, and the body
 * layer's sprite-sheet cell (u offset) is reported so the pose swap can be seen
 * in the debug overlay.
 */
export function clipFrameAt(clip: DecodedClip, tick: number) {
  const bodyLayer = clip.frames.filter(
    (frame) => clip.textures[String(frame.tex)] !== undefined && !clip.textures[String(frame.tex)].includes("shadow"),
  );
  const frames = bodyLayer.length > 0 ? bodyLayer : clip.frames;
  const loop = Math.max(1, clip.maxFrame);
  const phase = ((tick % loop) + loop) % loop;
  let current = frames[0];
  for (const frame of frames) {
    if (frame.frame <= phase) current = frame;
    else break;
  }
  return {
    frame: current.frame,
    offsetX: current.transX,
    offsetY: current.transY,
    reversU: current.reversU,
  };
}
