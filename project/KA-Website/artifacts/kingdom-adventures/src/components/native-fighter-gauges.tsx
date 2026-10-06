import {
  FIGHTER_GAUGES,
  fighterGaugeLayout,
  type FighterGaugeKind,
  type FighterVitals,
} from "@/lib/native-fighter-gauges";

/**
 * Native per-fighter HP/MP bars: two 18x3 meters drawn from the original `gauge/mini_gauge.png`
 * crops (background = the dark 18x3 record, fill = the 15x1 record of the bar's SEB frame) at the
 * fighter's native logical screen position.
 *
 * The component has no colours, no CSS gauges and no offsets of its own: everything comes from
 * `native-fighter-gauges.json` (native `FighterSystem.DrawGauges` / `AppData.DrawHorizontalMeter`
 * data). It is placed inside the unit's own origin, so the shared scene transform applies exactly
 * once, and it never touches the team member-info HUD.
 */
export function NativeFighterGauges({ vitals }: { vitals: FighterVitals }) {
  return (
    <div className="relative" style={{ width: 0, height: 0 }} data-fighter-gauges="1">
      <Meter kind="hp" current={vitals.hp.current} max={vitals.hp.max} />
      <Meter kind="mp" current={vitals.mp.current} max={vitals.mp.max} />
    </div>
  );
}

function Meter({ kind, current, max }: { kind: FighterGaugeKind; current: number; max: number }) {
  // screenX/screenY are 0: this component lives at the unit origin, so the layout is relative to it
  const layout = fighterGaugeLayout(kind, 0, 0, current, max);
  const asset = FIGHTER_GAUGES.asset.path;
  return (
    <div
      data-fighter-gauge={kind}
      data-gauge-frame={layout.frame}
      data-gauge-rate={layout.rate}
      data-gauge-current={current}
      data-gauge-max={max}
      data-gauge-fill-width={layout.fill.width}
      data-gauge-x={layout.x}
      data-gauge-y={layout.y}
      data-gauge-asset={asset}
      data-gauge-crop={`${layout.backgroundCrop.u},${layout.backgroundCrop.v},${layout.backgroundCrop.w},${layout.backgroundCrop.h}`}
      style={{ position: "absolute", left: layout.x, top: layout.y, width: layout.width, height: layout.height }}
    >
      {/* background: the 18x3 crop at its own (u,v) */}
      <_Crop
        asset={asset}
        u={layout.backgroundCrop.u}
        v={layout.backgroundCrop.v}
        w={layout.backgroundCrop.w}
        h={layout.backgroundCrop.h}
        left={0}
        top={0}
      />
      {/* fill: the 15x1 crop, clipped to the native fill width (nothing at rate 0) */}
      {layout.fill.width > 0 && (
        <_Crop
          asset={asset}
          u={layout.fillCrop.u}
          v={layout.fillCrop.v}
          w={layout.fill.width}
          h={layout.fillCrop.h}
          left={layout.fill.x}
          top={layout.fill.y}
          sourceWidth={layout.fill.maxWidth}
        />
      )}
    </div>
  );
}

/**
 * One 1:1 drawn crop of the packed `mini_gauge.png`: the same "clipping viewport over the shifted
 * packed sheet" used by the monster and human paths (the native call is
 * `Graphics.DrawImage(image, dx, dy, sx, sy, width, height)`, i.e. a 1:1 source crop).
 */
function _Crop({
  asset,
  u,
  v,
  w,
  h,
  left,
  top,
  sourceWidth,
}: {
  asset: string;
  u: number;
  v: number;
  w: number;
  h: number;
  left: number;
  top: number;
  /** the full fill record width, so the crop keeps the sheet at its natural size */
  sourceWidth?: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width: w,
        height: h,
        overflow: "hidden",
      }}
    >
      <img
        src={asset}
        alt=""
        style={{
          position: "absolute",
          left: -u,
          top: -v,
          width: "auto",
          height: "auto",
          maxWidth: "none",
          imageRendering: "pixelated",
        }}
        {...(sourceWidth === undefined ? {} : { "data-gauge-source-width": sourceWidth })}
      />
    </div>
  );
}
