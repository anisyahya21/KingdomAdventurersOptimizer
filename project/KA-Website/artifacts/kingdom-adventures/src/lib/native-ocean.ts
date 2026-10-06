// FormManager.ctor 0x16b8e6c..74: normal target rate = 20.
// GameForm._update 0x16bc64c..74 increments backScroll once per game tick.
export const NATIVE_OCEAN_TICKS_PER_SECOND = 20;

/** GameForm.DynamicRender 0x16bf1d4..324, including integer rounding.
 * The two source rectangles wrap the original 120x200 background horizontally.
 * Camera coordinates are not inputs to this draw path.
 */
export function nativeOceanSlices(width: number, tick: number) {
  const percent = Math.trunc((Math.trunc(tick / 2) % 240) * 100 / 240);
  const sourceX = Math.trunc(percent * 120 / 100);
  const firstWidth = width - Math.trunc(width * percent / 100);
  return [
    { sx: sourceX, sw: 120 - sourceX, dx: 0, dw: firstWidth },
    { sx: 0, sw: sourceX, dx: firstWidth, dw: width - firstWidth },
  ].filter(slice => slice.sw > 0 && slice.dw > 0);
}
