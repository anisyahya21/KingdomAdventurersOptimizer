/**
 * Native battle-body rendering data (Wairo/Kairo shared renderer).
 *
 * Recovered chain (all of this is read from the frozen libil2cpp.so, RVAs in the
 * 39257e72291d build):
 *
 *   RenderSystem.InsertZSortObject(Entity)   0x15D0214   one z-sort entry per SEB line
 *     -> AppData.InsertZSortObject(...)      0x16709B0   int[18] entry pool, fields = the
 *                                                        method's own parameters
 *     -> AppData.DrawZSortObjects(Graphics,type,baseDepthLayer) 0x167110C
 *        -> Seb.GetSprite(frame, line)       0x2351CA0   per-line record (texId,u,v,w,h,transX,transY,reversU,reversV)
 *        -> ResourceManager.img[texId]        (ResourceManager+0x10, group from entry imgRes/res)
 *        -> Graphics.DrawImage               0x2305030   when dest == source
 *           Graphics.DrawScaledImage         0x2305160   when dest != source
 *
 * Placement (CONFIRMED): entry.x/y = ToScreenPos(worldPos, offsetsByCamera:false) + VIEW - camera,
 * then the SEB record's transX/transY is added at the draw call. GameForm.VIEW_X/VIEW_Y
 * (static +0x8/+0xC) are written only by BattleForm.DynamicRender 0x16AAE84..0x16AAED8 from
 * BattleForm.GetViewOffsetX/Y 0x16A8920/0x16A8984 = VIEW - camX_/camY_ (+0x148/+0x14C); with the
 * battle's cam fields at 0 this is 0, so the only pan term is the camera entity.
 *
 * Per-line image substitution (CONFIRMED carrier, values data-driven):
 *   Entity.AddImage(texIds, resIds, texU, texV, texW, texH)  0x146F2A4 is the only writer of
 *   ImageComponent (ecs.ImageComponent: resIds +0x10, texIds +0x18, texUList +0x20, texVList +0x28,
 *   texWList +0x30, texHList +0x38). It is called from
 *     World.CreateHuman   0x1476854  (human fighters; texIds = 14 entries, one per SEB line,
 *                                     resIds = a shared group table, texU/V/W/H = null)
 *     World.CreateMonster 0x147780C  (monsters/pets; texId = the unit's own image)
 *     World._CreateGauge  0x1478964
 *   In the producer texId == -2 skips the line, -1 keeps the SEB record's texId (a -1 there
 *   also skips), otherwise the component value wins (0x15D0A5C..0x15D0F38).
 */

export type BodyEvidence = "CONFIRMED" | "SUPPORTED" | "UNKNOWN";

export type BodyFrame = {
  key: number;
  u: number;
  v: number;
  w: number;
  h: number;
  transX: number;
  transY: number;
  reversU: number;
  reversV: number;
};

export type BodyLine = {
  line: number;
  /** SEB texId for this line (-1 = the runtime ImageComponent supplies it). */
  texId: number;
  part: string;
  /** asset for lines the SEB binds directly; null = runtime-supplied. */
  asset: string | null;
  evidence: BodyEvidence;
  frames: BodyFrame[];
};

export const VIEW_ORIGIN = {
  viewX: 0,
  viewY: 0,
  evidence: "SUPPORTED" as BodyEvidence,
  note:
    "GameForm.VIEW_X/VIEW_Y (static +0x8/+0xC) are written only by BattleForm.DynamicRender " +
    "0x16AAE60-0x16AB184: it saves the pair, sets it from BattleForm.GetViewOffsetX/Y " +
    "(= VIEW - camX_/camY_), renders the world, and restores it. camX_/camY_ (+0x148/+0x14C) " +
    "are the world-map drag fields; nothing in the battle path writes them, so VIEW_X/Y = 0 " +
    "while the battle world draws and the only pan is the camera entity position.",
} as const;

/**
 * Complete native body placement (CONFIRMED), expressed for the replay's 240x240 native view space:
 *   screenX = isometricX(cellX, cellY) - trunc(camera.x) + VIEW_X
 *   screenY = isometricY(cellX, cellY) - trunc(camera.y) + VIEW_Y
 *   destX   = screenX + SEB.transX      destY = screenY + SEB.transY
 *   destW/H = SEB.w/h (or the ImageComponent override / ModifyAnimationComponent scale)
 * RenderSystem.ToScreenPos 0x15CDEEC: mode 0 uses TransormIsometricViewX/Y(x, z) and
 * (IsometricY(x, z) - y); mode 1 uses (x - z/2, x - y + field[0x3C]).
 */
export const BODY_PLACEMENT_EQUATION =
  "dest = iso(cellX,cellY) - camera + VIEW(0,0) + SEB(transX,transY); size = SEB(w,h)";

/**
 * ModifyAnimationComponent (CONFIRMED layout): +0x14/+0x18 = float X/Y align,
 * +0x20/+0x24 = float scaleX/scaleY, +0x28 = int, +0x34 = flag word (bit 1 = centre X,
 * bit 0x20 = centre Y), +0x3C = tint code, read at 0x15D0F88..0x15D10C0.
 * Destination size = round(scaleX * texW), round(scaleY * texH) (0x15D0FF8..0x15D1014);
 * with the component absent the destination stays equal to the source (1:1 DrawImage).
 */
export const MODIFY_ANIMATION = {
  evidence: "SUPPORTED" as BodyEvidence,
  note:
    "Only entities carrying ModifyAnimationComponent scale their body. The caught encounters " +
    "(wairo-*) create fighters through World.CreateHuman / World.CreateMonster without it, so " +
    "all of them draw 1:1; no per-encounter scale value is assumed.",
} as const;

const HUMAN_PART_BY_LINE: Record<number, { part: string; evidence: BodyEvidence }> = {
  0: { part: "shadow", evidence: "SUPPORTED" },
  1: { part: "shield", evidence: "SUPPORTED" },
  2: { part: "body", evidence: "SUPPORTED" },
  3: { part: "foot", evidence: "SUPPORTED" },
  4: { part: "shoes", evidence: "SUPPORTED" },
  5: { part: "hand (back)", evidence: "SUPPORTED" },
  6: { part: "face", evidence: "SUPPORTED" },
  7: { part: "hair", evidence: "SUPPORTED" },
  8: { part: "hat/headgear", evidence: "UNKNOWN" },
  9: { part: "accessory", evidence: "UNKNOWN" },
  10: { part: "eye", evidence: "SUPPORTED" },
  11: { part: "mouth", evidence: "SUPPORTED" },
  12: { part: "weapon", evidence: "SUPPORTED" },
  13: { part: "hand (front)", evidence: "SUPPORTED" },
};

/**
 * Fallback artwork for the five runtime-supplied lines (7-11). These sheets exist in
 * chara/img.inf (z_tmp_img_hair_m_00, z_tmp_img_hat_00, z_tmp_img_accessory_00,
 * z_tmp_img_eye_00, z_tmp_img_mouth_00) but equip_wait_up.seb gives them texId -1 with a
 * zero crop/offset placeholder, so the SEB alone cannot place them; the crop and the
 * offset come from the fighter's ImageComponent, whose values come from the job/dress
 * tables (World.CreateHuman 0x1476854) and are NOT recovered yet.
 */
export const HUMAN_RUNTIME_LINES: Record<number, { asset: string; part: string }> = {
  7: { asset: "/battle-assets/human/z_tmp_img_hair_m_00.png", part: "hair" },
  8: { asset: "/battle-assets/human/z_tmp_img_hat_00.png", part: "hat/headgear" },
  9: { asset: "/battle-assets/human/z_tmp_img_accessory_00.png", part: "accessory" },
  10: { asset: "/battle-assets/human/z_tmp_img_eye_00.png", part: "eye" },
  11: { asset: "/battle-assets/human/z_tmp_img_mouth_00.png", part: "mouth" },
};

/** chara/img.inf name -> copied sheet. */
function humanAssetForImage(image: string): string | null {
  if (!image || image === "?") return null;
  const base = image.replace(/\.gif$/, "").replace(/\.png$/, "");
  return `/battle-assets/human/${base}.png`;
}

export function buildHumanLines(
  composite: { layers: { layer: number; tex: number; image: string; frames: BodyFrame[] }[] } | undefined,
): BodyLine[] {
  if (!composite) return [];
  return composite.layers.map((layer) => {
    const part = HUMAN_PART_BY_LINE[layer.layer] ?? { part: `line ${layer.layer}`, evidence: "UNKNOWN" as BodyEvidence };
    return {
      line: layer.layer,
      texId: layer.tex,
      part: part.part,
      asset: humanAssetForImage(layer.image),
      evidence: part.evidence,
      frames: layer.frames,
    };
  });
}

/* ------------------------------------------------------------------ */
/* Monsters / pets                                                     */
/* ------------------------------------------------------------------ */

export type MonsterFamilyId = "small" | "xl";

/**
 * Both families are two-line SEBs: line 0 = shadow, line 1 = body. Values read directly
 * from the original SEBs (CONFIRMED as data):
 *   monster/monster_s_wait_up.seb   (monster/seb.inf #0,  maxFrame 20) 80x60 cells, body at transY -60
 *   monster/monster_xl_wait_up.seb  (monster/seb.inf #4,  maxFrame 20) body 150x150 at transY -150
 * Line 1's texId is a template (mizu_shadow_s / iwa_monster_l_01); every monster draws its
 * own image there through ImageComponent (World.CreateMonster -> Entity.AddImage), and the
 * SEB's transY is the bottom-centre offset of the template cell, i.e. -(cell h) with
 * transX = -(cell w / 2). Actual creatures differ in size because their images do.
 */
export const MONSTER_SEB_FAMILIES: Record<
  MonsterFamilyId,
  {
    seb: string;
    sebInfIndex: number;
    cellW: number;
    cellH: number;
    /** second pose cell, used by frame keys >= 10 (u = one cell width). */
    poseU: number;
    shadow: { asset: string; w: number; h: number; transX: number; transY: number };
    evidence: BodyEvidence;
  }
> = {
  small: {
    seb: "monster_s_wait_up.seb",
    sebInfIndex: 0,
    cellW: 80,
    cellH: 60,
    poseU: 80,
    shadow: { asset: "/battle-assets/anim/shadow_s.png", w: 80, h: 60, transX: -40, transY: -59 },
    evidence: "CONFIRMED",
  },
  xl: {
    seb: "monster_xl_wait_up.seb",
    sebInfIndex: 4,
    cellW: 150,
    cellH: 150,
    poseU: 150,
    shadow: { asset: "/battle-assets/anim/shadow_xl.png", w: 80, h: 60, transX: -40, transY: -59 },
    evidence: "CONFIRMED",
  },
};

/**
 * Family selection. Native rule (SUPPORTED, from AnimationSet..cctor): the wait group base is
 * 0, plus 4 when the creature's monster_size is 3 (the XL family), which is how the game
 * reaches monster_xl_wait_* for large species. The replay keys the family off the creature's
 * own sprite family, which the same assets make visible:
 */
export function monsterFamily(sprite: { name: string; width: number; height: number } | undefined): MonsterFamilyId {
  if (!sprite) return "small";
  return sprite.width >= 40 || sprite.height >= 40 ? "xl" : "small";
}

/**
 * Bottom-centre anchor for a substituted creature image. The native template sets
 * transX = -(w/2), transY = -h; the same rule is applied to the image the creature actually
 * draws, because the ImageComponent overrides the crop but the SEB keeps its template offsets.
 * SUPPORTED (the override's exact offset is not recovered).
 */
export function bottomCentreOffset(w: number, h: number) {
  return { transX: -Math.trunc(w / 2), transY: -h };
}

/* ------------------------------------------------------------------ */
/* Verified monster OPT sheets (original KA assets)                    */
/* ------------------------------------------------------------------ */

/**
 * Decoded with the existing parser (`tools/asset_extractor/parsers/opt_parser.py`) against the
 * intact originals in `RE-evidence/20260911-treasure/monster-original/` (copied to
 * `public/battle-assets/monster-original/`). The 15/60-byte copies under the legacy
 * `KA_assets` trees and the pnpm copy are truncated by 4 bytes and are NOT used.
 *
 * Records are the parser's field order: [imageRef, destX, destY, srcX, srcY, w, h].
 * The packed PNG is NOT a cell grid: each component gives the packed source rect
 * `(srcX, srcY, w, h)` and its logical placement `(destX, destY)` inside a `cellW x cellH`
 * cell; the logical image is `cols x rows` such cells, so transparent padding is preserved.
 */
export type OptComponent = {
  u: number;
  v: number;
  imageRef: number;
  destX: number;
  destY: number;
  srcX: number;
  srcY: number;
  w: number;
  h: number;
};

export type MonsterOptSheet = {
  name: string;
  png: string;
  cellW: number;
  cellH: number;
  cols: number;
  rows: number;
  components: OptComponent[];
  evidence: BodyEvidence;
  optSha256_16: string;
};

function rec(u: number, v: number, fields: [number, number, number, number, number, number, number]): OptComponent {
  const [imageRef, destX, destY, srcX, srcY, w, h] = fields;
  return { u, v, imageRef, destX, destY, srcX, srcY, w, h };
}

export const MONSTER_OPT_SHEETS: Record<string, MonsterOptSheet> = {
  iwa_monster_s_00: {
    name: "iwa_monster_s_00",
    png: "/battle-assets/monster-original/iwa_monster_s_00.png",
    cellW: 80,
    cellH: 60,
    cols: 2,
    rows: 2,
    optSha256_16: "3f7dd7e46c13dc38",
    evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 29, 40, 27, 0, 26, 19]),
      rec(1, 0, [-1, 29, 40, 0, 0, 27, 19]),
      rec(0, 1, [-1, 30, 40, 79, 0, 25, 19]),
      rec(1, 1, [-1, 30, 40, 53, 0, 26, 19]),
    ],
  },
  iwa_monster_l_01: {
    name: "iwa_monster_l_01",
    png: "/battle-assets/monster-original/iwa_monster_l_01.png",
    cellW: 80,
    cellH: 60,
    cols: 2,
    rows: 2,
    optSha256_16: "5f0d7d43fb2ca41c",
    evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 11, 3, 0, 171, 59, 57]),
      rec(1, 0, [-1, 10, 3, 0, 114, 60, 57]),
      rec(0, 1, [-1, 9, 3, 0, 0, 62, 57]),
      rec(1, 1, [-1, 9, 3, 0, 57, 62, 57]),
    ],
  },
  iwa_monster_xl_00: {
    name: "iwa_monster_xl_00",
    png: "/battle-assets/monster-original/iwa_monster_xl_00.png",
    cellW: 150,
    cellH: 150,
    cols: 2,
    rows: 2,
    optSha256_16: "07fde7a5d1134200",
    evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 21, 27, 110, 0, 109, 123]),
      rec(1, 0, [-1, 21, 24, 110, 123, 109, 122]),
      rec(0, 1, [-1, 20, 27, 0, 0, 110, 123]),
      rec(1, 1, [-1, 20, 24, 0, 123, 110, 122]),
    ],
  },
  kazan_monster_l_00: {
    name: "kazan_monster_l_00",
    png: "/battle-assets/monster-original/kazan_monster_l_00.png",
    cellW: 80,
    cellH: 60,
    cols: 2,
    rows: 2,
    optSha256_16: "0f1edfc9270d55e0",
    evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 15, 11, 47, 0, 49, 49]),
      rec(1, 0, [-1, 16, 12, 141, 0, 49, 47]),
      rec(0, 1, [-1, 16, 10, 0, 0, 47, 50]),
      rec(1, 1, [-1, 19, 11, 96, 0, 45, 49]),
    ],
  },
  shadow_s: {
    name: "shadow_s",
    png: "/battle-assets/monster-original/shadow_s.png",
    cellW: 80,
    cellH: 60,
    cols: 1,
    rows: 1,
    optSha256_16: "a7282b941d474c3c",
    evidence: "CONFIRMED",
    components: [rec(0, 0, [-1, 32, 57, 0, 0, 16, 3])],
  },
  shadow_xl: {
    name: "shadow_xl",
    png: "/battle-assets/monster-original/shadow_xl.png",
    cellW: 80,
    cellH: 60,
    cols: 1,
    rows: 1,
    optSha256_16: "b5e73847d98166e5",
    evidence: "CONFIRMED",
    components: [rec(0, 0, [-1, 8, 43, 0, 0, 64, 17])],
  },
};

/**
 * One logical cell of a sheet = the SEB's cell (e.g. 80x60 or 150x150). Inside it the component's
 * packed sprite is drawn at (destX, destY) with size (w x h) taken from the PNG at (srcX, srcY);
 * everything else stays transparent, which is what keeps the SEB's cell coordinates valid.
 */
/**
 * Encounter monsters: MonsterData.img (+0x2C) -> body sheet, MonsterData.size (+0x34) -> shadow
 * sheet and SEB family (size == SIZE_XL 3 -> XL). monster ids from the seven battle encounters.
 * Image ids resolved through monster/img.inf; sheet records decoded from the verified originals.
 */
export const MONSTER_IMG_SHEETS: Record<number, string> = {
  117: "ex_monster_m_01", // 106 King Ackbar
  125: "ex_monster_m_09", // 114 Chimpan Z
  127: "ex_monster_m_11", // 116 Wairobot
  132: "ex_monster_m_16", // 121 Tuxy
  133: "ex_monster_m_17", // 122 Manager Clapperclaw
  143: "all_monster_l_wairo02", // 142 Wairo Tank (size 2 = SIZE_L, ordinary family)
};

/** MonsterData.size -> shadow sheet (img.inf ids 0..3 are shadow_s/m/l/xl). */
export const MONSTER_SHADOW_BY_SIZE: Record<number, string> = {
  0: "shadow_s",
  1: "shadow_m",
  2: "shadow_l",
  3: "shadow_xl",
};

export const MONSTER_EXTRA_OPT_SHEETS: Record<string, MonsterOptSheet> = {
  ex_monster_m_01: {
    name: "ex_monster_m_01",
    png: "/battle-assets/monster-original/ex_monster_m_01.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "3abe76a7e6c87148", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 32, 36, 17, 0, 16, 23]),
      rec(1, 0, [-1, 32, 37, 0, 23, 16, 22]),
      rec(0, 1, [-1, 31, 36, 0, 0, 17, 23]),
      rec(1, 1, [-1, 32, 37, 16, 23, 16, 22]),
    ],
  },
  ex_monster_m_09: {
    name: "ex_monster_m_09",
    png: "/battle-assets/monster-original/ex_monster_m_09.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "55f7152fb99b596b", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 32, 41, 17, 0, 15, 18]),
      rec(1, 0, [-1, 33, 42, 32, 0, 15, 17]),
      rec(0, 1, [-1, 31, 41, 0, 0, 17, 18]),
      rec(1, 1, [-1, 34, 42, 47, 0, 13, 17]),
    ],
  },
  ex_monster_m_11: {
    name: "ex_monster_m_11",
    png: "/battle-assets/monster-original/ex_monster_m_11.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "c7aeef8784ba2b6f", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 30, 35, 20, 0, 19, 24]),
      rec(1, 0, [-1, 32, 36, 39, 0, 16, 23]),
      rec(0, 1, [-1, 30, 35, 0, 0, 20, 24]),
      rec(1, 1, [-1, 32, 36, 55, 0, 16, 23]),
    ],
  },
  ex_monster_m_16: {
    name: "ex_monster_m_16",
    png: "/battle-assets/monster-original/ex_monster_m_16.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "a53f1100b9121bab", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 33, 39, 16, 0, 15, 20]),
      rec(1, 0, [-1, 30, 40, 31, 0, 17, 19]),
      rec(0, 1, [-1, 32, 39, 0, 0, 16, 20]),
      rec(1, 1, [-1, 30, 40, 48, 0, 17, 19]),
    ],
  },
  ex_monster_m_17: {
    name: "ex_monster_m_17",
    png: "/battle-assets/monster-original/ex_monster_m_17.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "68879330073b5090", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 33, 41, 16, 0, 15, 18]),
      rec(1, 0, [-1, 33, 42, 0, 18, 15, 17]),
      rec(0, 1, [-1, 33, 41, 0, 0, 16, 18]),
      rec(1, 1, [-1, 33, 42, 15, 18, 15, 17]),
    ],
  },
  all_monster_l_wairo02: {
    name: "all_monster_l_wairo02",
    png: "/battle-assets/monster-original/all_monster_l_wairo02.png",
    cellW: 80, cellH: 60, cols: 2, rows: 2, optSha256_16: "bd1832bcff56947a", evidence: "CONFIRMED",
    components: [
      rec(0, 0, [-1, 6, 1, 0, 118, 61, 58]),
      rec(1, 0, [-1, 6, 0, 0, 0, 61, 59]),
      rec(0, 1, [-1, 6, 1, 0, 176, 61, 58]),
      rec(1, 1, [-1, 6, 0, 0, 59, 61, 59]),
    ],
  },
  shadow_m: {
    name: "shadow_m",
    png: "/battle-assets/monster-original/shadow_m.png",
    cellW: 80, cellH: 60, cols: 1, rows: 1, optSha256_16: "bf52d8d3bf88f74c", evidence: "CONFIRMED",
    components: [rec(0, 0, [-1, 28, 57, 0, 0, 23, 3])],
  },
  shadow_l: {
    name: "shadow_l",
    png: "/battle-assets/monster-original/shadow_l.png",
    cellW: 80, cellH: 60, cols: 1, rows: 1, optSha256_16: "b4b38af3090d51b4", evidence: "CONFIRMED",
    components: [rec(0, 0, [-1, 24, 57, 0, 0, 32, 3])],
  },
};

export function monsterSheetFor(imgId: number | null, size: number): MonsterOptSheet | null {
  if (imgId === null) return null;
  return MONSTER_IMG_SHEETS[imgId] ? MONSTER_EXTRA_OPT_SHEETS[MONSTER_IMG_SHEETS[imgId]] ?? null : null;
}

export function shadowSheetFor(size: number): MonsterOptSheet | null {
  return MONSTER_EXTRA_OPT_SHEETS[MONSTER_SHADOW_BY_SIZE[size] ?? "shadow_s"]
    ?? MONSTER_OPT_SHEETS[MONSTER_SHADOW_BY_SIZE[size] ?? "shadow_s"]
    ?? null;
}

/**
 * Encounter monster id -> MonsterData {img, size}. Values read from the original
 * xls/Japanese.lproj/Monster.txt rows (col3 = img, col5 = size) after the column->field mapping
 * was tied to data.MonsterData.img (+0x2C) / .size (+0x34). Monsters not listed here fall back to
 * the legacy sprite path.
 */
export const MONSTER_DATA_ENCOUNTERS: Record<number, { img: number; size: number }> = {
  106: { img: 117, size: 1 },
  114: { img: 125, size: 1 },
  116: { img: 127, size: 1 },
  121: { img: 132, size: 1 },
  122: { img: 133, size: 1 },
  142: { img: 143, size: 2 },
};

export function monsterCell(sheet: MonsterOptSheet, u: number, v: number) {
  const comp = sheet.components.find((c) => c.u === u && c.v === v);
  if (!comp) return null;
  return {
    /** logical cell size addressed by the SEB */
    cellW: sheet.cellW,
    cellH: sheet.cellH,
    /** SEB crop origin for this cell */
    sebU: u * sheet.cellW,
    sebV: v * sheet.cellH,
    png: sheet.png,
    /** packed source rect inside the PNG */
    src: { x: comp.srcX, y: comp.srcY, w: comp.w, h: comp.h },
    /** logical placement inside the cell (transparent padding preserved) */
    dest: { x: comp.destX, y: comp.destY },
    imageRef: comp.imageRef,
    evidence: sheet.evidence,
  };
}

/**
 * Resolve a SEB record's own crop rectangle against the validated OPT grid.
 *
 * `Seb.GetSprite(frame, line)` returns u/v = the logical atlas origin of the cell the SEB
 * addresses and w/h = that cell's size (80x60 for the encounter sheets; 150x150 for the unused XL
 * family). The lookup below matches those SEB coordinates directly - the SEB values stay
 * authoritative and no pose index is derived from them - and verifies that the rect is exactly one
 * recovered cell. A rect outside the decoded grid returns null, which the caller renders as "this
 * line draws nothing", the same as a frame outside the line's key range.
 */
export function monsterCellAtSebRect(
  sheet: MonsterOptSheet,
  seb: { u: number; v: number; w: number; h: number },
) {
  if (seb.w !== sheet.cellW || seb.h !== sheet.cellH) return null;
  const component = sheet.components.find(
    (entry) => entry.u * sheet.cellW === seb.u && entry.v * sheet.cellH === seb.v,
  );
  if (!component) return null;
  return monsterCell(sheet, component.u, component.v);
}
