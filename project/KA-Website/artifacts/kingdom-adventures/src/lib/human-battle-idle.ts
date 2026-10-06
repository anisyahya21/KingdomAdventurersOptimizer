import humanBattleIdleJson from "@/game-data/human-battle-idle.json";
import humanPartAtlasJson from "@/game-data/human-part-atlas.json";

/**
 * Frozen native-derived human battle idle - the resolution logic.
 *
 * The data in `src/game-data/human-battle-idle.json` is the frozen state handed over for PASS 12:
 * EQUIP_WAIT, image-set row TYPE_NORMAL row 0, direction UP, settled post-direction-update state,
 * SEB frame 0. It was read from the intact 980-byte `chara/equip_wait_up.seb`
 * (`RE-evidence/20260912-combat/chara-animation-original/equip_wait_up.seb`, sha256 in the data
 * file) - not from the 976-byte extracted copies that lose line 13's last record - plus the job,
 * equipment and HumanResourceSet tables.
 *
 * What this module does NOT claim:
 *  * it is not the complete human animation (walk, attack, hit, knock-down and every other clip are
 *    outside this data);
 *  * it is not a reproduction of any saved character (the two demo configurations are valid
 *    demonstration inputs);
 *  * lines 7-11 stay undrawn: the SEB hands them to the fighter's runtime ImageComponent, whose
 *    values are not part of this frozen state;
 *  * no original-game runtime was captured for it.
 */

export type HumanIdleEvidence = "CONFIRMED" | "SUPPORTED" | "UNKNOWN";

export type HumanIdleLine = {
  line: number;
  part: string;
  /** resource group of the line's image (the front resource row of the frozen image set) */
  res: number;
  /** the SEB record's own texId: an imgIds slot, or -1 for a runtime-supplied line */
  texId: number;
  u: number;
  v: number;
  w: number;
  h: number;
  transX: number;
  transY: number;
  reversU: number;
  reversV: number;
};

export type HumanIdleCharacter = {
  id: string;
  label: string;
  jobSourceId: number;
  genderIndex: number;
  flag: number;
  imgIds: number[];
  expectedDraws: number;
  expectedLines: number[];
};

export const HUMAN_BATTLE_IDLE = humanBattleIdleJson as unknown as {
  note: string;
  frozenState: {
    clip: string;
    imageSetRow: string;
    direction: string;
    frame: number;
    seb: {
      group: string;
      file: string;
      source: string;
      bytes: number;
      sha256: string;
      layers: number;
      keyRecords: number;
      maxFrame: number;
      serialization: string;
    };
  };
  resolution: { rule: string; evidence: HumanIdleEvidence; note: string };
  lineResourceGroups: {
    row: number[];
    source: string;
    evidence: HumanIdleEvidence;
    note: string;
    partDirs: Record<string, string>;
    partDirsEvidence: HumanIdleEvidence;
    partDirsNote: string;
  };
  lines: HumanIdleLine[];
  linesNote: string;
  characters: (HumanIdleCharacter & { sources: Record<string, string> })[];
  charactersNote: string;
};

export const HUMAN_IDLE_STATE = HUMAN_BATTLE_IDLE.frozenState;
export const HUMAN_IDLE_LINES = HUMAN_BATTLE_IDLE.lines;
export const HUMAN_IDLE_RESOLUTION = HUMAN_BATTLE_IDLE.resolution;
export const HUMAN_IDLE_GROUP = HUMAN_BATTLE_IDLE.lineResourceGroups;
export const HUMAN_IDLE_CHARACTERS = HUMAN_BATTLE_IDLE.characters;

/**
 * Part sheets that ship without a baked OPT entry.
 *
 * `character-rules.json` can only describe the images the original assets carry an `.opt` for. A few
 * part images have none, and those images are not packed sprites: they are plain, untrimmed cell
 * atlases (one SEB cell per direction), e.g. `face/m_face_32_0.png` = 24x96 = 4 stacked 24x24 cells
 * (the Beast Tamer rank-D head) and `weapon/weapon_28_3.png` = 240x60 = 4 side-by-side 60x60 cells
 * (a Ninja weapon). For those the OPT is the identity grid, so the only input the resolver lacks is
 * the PNG's pixel size - which is exactly what `human-part-atlas.json` carries.
 */
export const HUMAN_PART_ATLAS = humanPartAtlasJson as unknown as {
  note: string;
  source: string;
  rule: string;
  sheets: Record<string, Record<string, [number, number]>>;
};

/** Pixel size of an unbaked part sheet, or null when the sheet either is baked or is unknown. */
export function humanPartAtlasSize(dir: string, file: string): [number, number] | null {
  const size = HUMAN_PART_ATLAS.sheets?.[dir]?.[file];
  return Array.isArray(size) && size.length === 2 ? [size[0], size[1]] : null;
}

/** One drawn SEB line, fully resolved down to the packed sheet rectangle it paints. */
export type HumanIdleDraw = {
  line: number;
  part: string;
  res: number;
  /**
   * true when this draw came from the identity cell grid of an unbaked atlas sheet (no OPT in the
   * original assets); `src`/`dest` are then the derived grid cell, not a packed component.
   */
  atlasCell?: boolean;
  /** the image index inside `dir`'s img.inf (what the line's own resource actually holds) */
  tex: number;
  /** the imgIds slot the SEB record named */
  slot: number;
  dir: string;
  file: string;
  png: string;
  /** SEB record: logical crop + the translation the native draw adds to the entity origin */
  u: number;
  v: number;
  w: number;
  h: number;
  transX: number;
  transY: number;
  reversU: number;
  reversV: number;
  /** OPT cell the crop lands on */
  cellW: number;
  cellH: number;
  cellColumn: number;
  cellRow: number;
  /** packed source rectangle inside `png` */
  src: { x: number; y: number; w: number; h: number };
  /** placement of that rectangle inside the logical cell */
  dest: { x: number; y: number };
};

export type HumanIdleSkip = { line: number; part: string; reason: string };

/** The subset of `character_sprites/character-rules.json` this path needs. */
export type HumanPartSheet = {
  cellW: number;
  cellH: number;
  slots: Record<
    string,
    {
      status: string;
      destX?: number;
      destY?: number;
      srcX?: number;
      srcY?: number;
      w?: number;
      h?: number;
    }
  >;
};

export type HumanPartRules = {
  spriteBase: string;
  dirs: Record<
    string,
    {
      inf: Record<string, Record<string, string>>;
      opts: Record<string, HumanPartSheet>;
    }
  >;
};

/** `character_sprites/<dir>/<file>`; the sheets are the baked original img.inf + OPT data. */
function humanPartUrl(rules: HumanPartRules, dir: string, file: string): string {
  return `${rules.spriteBase}/${encodeURIComponent(dir)}/${encodeURIComponent(file)}`;
}

/**
 * Resolve an `img.inf` name against the baked sheets of one part directory.
 *
 * The baked document keys every OPT entry by the PNG's basename, and the baker writes that PNG as
 * `<key>.png`, so the key is also the file name that exists on disk. The `img.inf` name itself is
 * used first and only falls back to its extension-less stem: the original asset table keeps one
 * genuinely truncated name (`shadow/img.inf` reads `shadow.pn` while the file is `shadow.png`),
 * and that stem lookup is what resolves it without a special case per part.
 */
function sheetFor(
  sheet: { inf: Record<string, Record<string, string>>; opts: Record<string, HumanPartSheet> } | undefined,
  infName: string,
): { key: string; entry: HumanPartSheet; file: string } | null {
  if (!sheet) return null;
  const stem = infName.replace(/\.[^.]*$/, "");
  for (const key of [infName, stem]) {
    const entry = sheet.opts?.[key];
    if (entry) return { key, entry, file: `${key}.png` };
  }
  return null;
}

/**
 * Demo-character selection for the battle viewer. The replay's ally units carry a website label
 * (`jobName` + `rank`) and nothing else, so the frozen path is limited to the two configurations
 * that were handed over; every other unit keeps the previous renderer. The match is on the data
 * file's own label, so adding a configuration is a data edit, not a code edit.
 */
export function humanIdleCharacterForUnit(unit: {
  jobName?: string;
  rank?: string;
}): HumanIdleCharacter | null {
  if (!unit.jobName) return null;
  const wanted = `${unit.jobName} ${unit.rank ?? ""}`.trim().toLowerCase();
  return HUMAN_IDLE_CHARACTERS.find((entry) => entry.label.toLowerCase() === wanted) ?? null;
}

/**
 * Resolve one character's SEB lines against the baked part sheets.
 *
 * `lines` is the per-line record list for the animation frame being drawn; it defaults to the frozen
 * EQUIP_WAIT frame-0 records, which is what the PASS 12/12.36 renderer was verified against. PASS 15
 * COMMAND 15.2 passes the records of `humanLineRecordsAt(clip, frame)` instead, so the idle loop and
 * the attack clips go through this one pipeline rather than a second renderer.
 *
 * Per line, in ascending line order:
 *   1. the SEB record's texId names an imgIds slot (-1 = the runtime supplies the line -> not drawn);
 *   2. imgIds[slot] is the image index in the line's own resource group (-2 skips, -1 keeps the
 *      SEB's texId, which is also -1 -> not drawn);
 *   3. the group picks the `character_sprites` part directory;
 *   4. the SEB crop (u,v,w,h) is matched against that sheet's OPT grid, so the packed source
 *      rectangle comes from the original img.inf/OPT data rather than from a guessed layout. A sheet
 *      the original assets ship without an `.opt` is drawn through its identity cell grid instead
 *      (`humanPartAtlasSize`), and is marked `atlasCell` so the DOM contract shows which rule drew
 *      it.
 *
 * Anything that fails a step is reported in `skipped` with its reason instead of being drawn.
 */
export function humanIdleResolution(
  character: HumanIdleCharacter,
  rules: HumanPartRules,
  lines: HumanIdleLine[] = HUMAN_IDLE_LINES,
): { draws: HumanIdleDraw[]; skipped: HumanIdleSkip[] } {
  const draws: HumanIdleDraw[] = [];
  const skipped: HumanIdleSkip[] = [];
  const ordered = [...lines].sort((a, b) => a.line - b.line);
  for (const line of ordered) {
    if (line.texId < 0) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason: `SEB texId ${line.texId} (runtime-supplied line)`,
      });
      continue;
    }
    const image = character.imgIds[line.texId];
    if (image === undefined) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason: `imgIds has no slot ${line.texId}`,
      });
      continue;
    }
    if (image < 0) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason:
          image === -2
            ? `imgIds[${line.texId}] = -2 (this configuration has no image for the slot)`
            : `imgIds[${line.texId}] = ${image} (SEB fallback, which is also -1)`,
      });
      continue;
    }
    const dir = HUMAN_IDLE_GROUP.partDirs[String(line.res)];
    if (!dir) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason: `resource group ${line.res} is not resolved to a part directory`,
      });
      continue;
    }
    const dirRules = rules.dirs[dir];
    const infName = dirRules?.inf?.img?.[String(image)];
    if (!infName) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason: `${dir}/img.inf has no image index ${image}`,
      });
      continue;
    }
    const sheet = sheetFor(dirRules, infName);
    /**
     * Unbaked atlas fallback. The original assets ship a few part images without an `.opt`; every one
     * of them is an untrimmed cell atlas, so its OPT is the identity grid and the cell the SEB crop
     * names is simply `(u / cropW, v / cropH)` inside the PNG. The grid is only accepted when the
     * sheet's own size is an exact multiple of the crop, so a *packed* sheet (whose PNG is smaller
     * than the cell grid) can never be misread as an atlas.
     */
    if (!sheet) {
      const atlas = humanPartAtlasSize(dir, `${infName}`);
      const cellW = line.w;
      const cellH = line.h;
      const usable =
        atlas !== null &&
        cellW > 0 &&
        cellH > 0 &&
        atlas[0] % cellW === 0 &&
        atlas[1] % cellH === 0 &&
        atlas[0] / cellW > 0 &&
        atlas[1] / cellH > 0;
      if (!usable) {
        skipped.push({
          line: line.line,
          part: line.part,
          reason: `${dir}/${infName} has no baked OPT entry`,
        });
        continue;
      }
      const cellColumn = line.u / cellW;
      const cellRow = line.v / cellH;
      draws.push({
        line: line.line,
        part: line.part,
        res: line.res,
        tex: image,
        slot: line.texId,
        dir,
        file: infName,
        png: humanPartUrl(rules, dir, infName),
        u: line.u,
        v: line.v,
        w: line.w,
        h: line.h,
        transX: line.transX,
        transY: line.transY,
        reversU: line.reversU,
        reversV: line.reversV,
        cellW,
        cellH,
        cellColumn,
        cellRow,
        src: { x: cellColumn * cellW, y: cellRow * cellH, w: cellW, h: cellH },
        dest: { x: 0, y: 0 },
        atlasCell: true,
      });
      continue;
    }
    const entry = sheet.entry;
    const file = sheet.file;
    if (
      line.w !== entry.cellW ||
      line.h !== entry.cellH ||
      line.u % entry.cellW !== 0 ||
      line.v % entry.cellH !== 0
    ) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason:
          `SEB crop ${line.u},${line.v} ${line.w}x${line.h} is not one cell of ` +
          `${dir}/${file} (${entry.cellW}x${entry.cellH})`,
      });
      continue;
    }
    const cellColumn = line.u / entry.cellW;
    const cellRow = line.v / entry.cellH;
    const slotKey = `${cellRow},${cellColumn}`;
    const slot = entry.slots[slotKey];
    if (
      !slot ||
      slot.destX == null ||
      slot.destY == null ||
      slot.srcX == null ||
      slot.srcY == null ||
      slot.w == null ||
      slot.h == null
    ) {
      skipped.push({
        line: line.line,
        part: line.part,
        reason: `${dir}/${file} has no packed content in OPT cell ${slotKey}`,
      });
      continue;
    }
    draws.push({
      line: line.line,
      part: line.part,
      res: line.res,
      tex: image,
      slot: line.texId,
      dir,
      file,
      png: humanPartUrl(rules, dir, file),
      u: line.u,
      v: line.v,
      w: line.w,
      h: line.h,
      transX: line.transX,
      transY: line.transY,
      reversU: line.reversU,
      reversV: line.reversV,
      cellW: entry.cellW,
      cellH: entry.cellH,
      cellColumn,
      cellRow,
      src: { x: slot.srcX, y: slot.srcY, w: slot.w, h: slot.h },
      dest: { x: slot.destX, y: slot.destY },
    });
  }
  return { draws, skipped };
}
