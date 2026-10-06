import { useEffect, useMemo, useState } from "react";

import { loadHumanPartRules } from "@/lib/character-renderer";
import {
  humanIdleResolution,
  type HumanIdleLine,
  type HumanIdleCharacter,
  type HumanIdleDraw,
  type HumanPartRules,
} from "@/lib/human-battle-idle";

/**
 * Native human battle body: the 14 SEB lines of the character's current clip and frame
 * (EQUIP_WAIT by default, an attack clip while the fighter is attacking), drawn in ascending line
 * order.
 *
 * Placement is the recovered native chain with no per-character adjustment:
 *
 *   dest = entity origin + SEB(transX, transY)      (one wrapper per line, at the line's own record)
 *   size = SEB(w, h)                                (the crop the record carries)
 *   pixels = OPT cell of that line's own group sheet
 *
 * The entity origin is the caller's (`UnitSprite` puts this at the unit's native view position), the
 * SEB record supplies the translation and the crop, and the packed sheet rectangle comes from the
 * original `img.inf` / OPT data. The scene scale is the shared stage transform and is never applied
 * here.
 *
 * Lines the frozen state does not draw - the SEB's runtime-supplied lines 7-11, a slot the
 * configuration has no image for (Archer's missing shield), and the forced shoes skip - are simply
 * absent from the output; nothing is substituted.
 */
/** Loads the baked `img.inf`/OPT part rules once; shared by the per-unit and queued renderers. */
export function useHumanPartRules(): HumanPartRules | null {
  const [rules, setRules] = useState<HumanPartRules | null>(null);

  useEffect(() => {
    let live = true;
    loadHumanPartRules()
      .then((loaded) => {
        if (live) setRules(loaded);
      })
      .catch(() => {
        if (live) setRules(null);
      });
    return () => {
      live = false;
    };
  }, []);
  return rules;
}

/**
 * The frozen human's drawn lines (SEB record + OPT cell per line), or null until the rules load.
 * The queue build uses this directly so every human line can become its own depth-sorted entry.
 */
export function useHumanIdleDraws(character: HumanIdleCharacter | null) {
  const rules = useHumanPartRules();
  const resolved = useMemo(
    () => (rules && character ? humanIdleResolution(character, rules) : null),
    [rules, character],
  );
  return resolved;
}

/**
 * The drawn lines of one animation frame. `lines` is whatever record list the caller resolved -
 * EQUIP_WAIT, an attack clip or one of the PASS 15 COMMAND 15.5 reaction clips; when it is omitted
 * the frozen EQUIP_WAIT frame-0 records are used, which is the state the PASS 12 renderer was
 * verified against.
 */
export function useHumanAnimationDraws(
  character: HumanIdleCharacter | null,
  lines?: HumanIdleLine[] | null,
) {
  const rules = useHumanPartRules();
  return useMemo(() => {
    if (!rules || !character) return null;
    return humanIdleResolution(character, rules, lines ?? undefined);
  }, [rules, character, lines]);
}

export function NativeHumanBody({
  character,
  lines,
}: {
  character: HumanIdleCharacter;
  lines?: HumanIdleLine[] | null;
}) {
  const resolved = useHumanAnimationDraws(character, lines);
  if (!resolved) return <div className="relative" style={{ width: 0, height: 0 }} />;

  return (
    <div
      className="relative"
      style={{ width: 0, height: 0 }}
      data-human-idle={character.id}
      data-human-draws={resolved.draws.length}
      data-human-skipped={resolved.skipped.length}
    >
      {resolved.draws.map((draw) => <HumanIdleLineLayer key={draw.line} draw={draw} />)}
    </div>
  );
}

/** One drawn SEB line of the frozen human, at its own record translation/crop. */
export function HumanIdleLineLayer({ draw }: { draw: HumanIdleDraw }) {
  /**
   * The packed sheet is drawn inside a cell-clipping viewport, exactly like the monster OPT path:
   * the OPT component copies only `png[src]` to `logical[dest]` at its natural size, and these PNGs
   * are tightly packed, so a cell-sized box with the sheet shifted by `-src` is the same draw. The
   * JPEG-style `max-width` that Tailwind's preflight would otherwise apply must stay off, or the
   * sheet is resized and paints outside its cell.
   */
  const flip =
    draw.reversU || draw.reversV
      ? `scale(${draw.reversU ? -1 : 1}, ${draw.reversV ? -1 : 1})`
      : undefined;
  return (
    <div
      key={draw.line}
      data-human-line={draw.line}
      data-human-part={draw.part}
      data-human-res={draw.res}
      data-human-slot={draw.slot}
      data-human-tex={draw.tex}
      data-human-dir={draw.dir}
      data-human-file={draw.file}
      data-human-trans={`${draw.transX},${draw.transY}`}
      data-human-crop={`${draw.u},${draw.v},${draw.w},${draw.h}`}
      data-human-opt-cell={`${draw.cellRow},${draw.cellColumn}`}
      data-human-atlas-cell={draw.atlasCell ? "1" : undefined}
      style={{
        position: "absolute",
        left: draw.transX,
        top: draw.transY,
        width: draw.cellW,
        height: draw.cellH,
        transform: flip,
      }}
    >
      <div
        data-human-opt-dest={`${draw.dest.x},${draw.dest.y}`}
        data-human-opt-src={`${draw.src.x},${draw.src.y},${draw.src.w},${draw.src.h}`}
        style={{
          position: "absolute",
          left: draw.dest.x,
          top: draw.dest.y,
          width: draw.src.w,
          height: draw.src.h,
          overflow: "hidden",
        }}
      >
        <img
          src={draw.png}
          alt=""
          style={{
            position: "absolute",
            left: -draw.src.x,
            top: -draw.src.y,
            width: "auto",
            height: "auto",
            maxWidth: "none",
            imageRendering: "pixelated",
          }}
        />
      </div>
    </div>
  );
}
