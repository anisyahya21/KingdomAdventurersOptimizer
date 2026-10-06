/** Ordinary OPT branch, recovered from Image.LoadOptimize and Graphics._drawImage.
 * Fields: OPT_REF, OPT_X, OPT_Y, OPT_SRCX, OPT_SRCY, OPT_SRCW, OPT_SRCH.
 * Coordinates here are logical image pixels, before _drawBitmap texture scaling,
 * rounding, world projection, graphics transforms or clipping by the viewport.
 * For ordinary OPT on the current image, _drawBitmap skips standard-resolution
 * rescaling (0x23065d4..6638). Normal-color crops remain floating point; the
 * trunc(value + 0.1f) integers are used by grayscale caching (0x23066e0..69a4).
 * Do not apply that integer conversion to normal Canvas drawImage commands.
 */
export type OptComponent = {
  imageRef: number; x: number; y: number;
  sourceX: number; sourceY: number; width: number; height: number;
};
export type OptSprite = { cellWidth: number; cellHeight: number; columns: number; rows: number; cells: OptComponent[][] };
export type OptRect = { x: number; y: number; width: number; height: number };
export type OptDrawCommand = { imageRef: number; source: OptRect; destination: OptRect };

/** Compose an already resolved SEB layer with one ordinary OPT cell.
 * DrawZSortObjects (0x16719a4..19d4) adds SP_TRANS_X/Y (6/7) to
 * queued x/y before DrawImage. Source U/V/W/H are fields 2..5.
 * queueOrigin includes projection/camera offsets; zoom is a website display
 * transform applied after native placement. This does not select a building's
 * SEB, resolve animation/overrides, or implement mirroring/rotation.
 */
export function getSebOptDrawCommands(opt: OptSprite, sprite: readonly number[],
  queueOrigin: { x: number; y: number }, zoom = 1): OptDrawCommand[] {
  if (sprite.length < 10 || !sprite.every(Number.isInteger) ||
      ![queueOrigin.x, queueOrigin.y, zoom].every(Number.isFinite) || zoom <= 0)
    throw new Error("Invalid resolved SEB layer");
  if (sprite[8] !== 0 || sprite[9] !== 0) throw new Error("SEB mirroring is not supported");
  const [,, u, v, width, height, dx, dy] = sprite;
  if (u < 0 || v < 0 || width <= 0 || height <= 0 || opt.cellWidth <= 0 || opt.cellHeight <= 0)
    throw new Error("Invalid SEB source rectangle");
  const column = Math.floor(u / opt.cellWidth), row = Math.floor(v / opt.cellHeight);
  const request = { x: u % opt.cellWidth, y: v % opt.cellHeight, width, height };
  if (request.x + width > opt.cellWidth || request.y + height > opt.cellHeight)
    throw new Error("SEB source spans multiple OPT cells");
  return getOptDrawCommands(opt, column, row,
    { x: (queueOrigin.x + dx) * zoom, y: (queueOrigin.y + dy) * zoom,
      width: width * zoom, height: height * zoom }, request);
}

export function parseOptSprite(buffer: ArrayBuffer): OptSprite {
  const view = new DataView(buffer);
  if (view.byteLength < 4) throw new Error("Truncated OPT header");
  if (view.getUint8(0) === 255) throw new Error("Unsupported OPT sentinel variant");
  const cellWidth = view.getUint8(0), cellHeight = view.getUint8(1);
  const columns = view.getInt8(2), rows = view.getInt8(3);
  if (columns < 0 || rows < 0) throw new Error("Invalid OPT grid");
  const cells: OptComponent[][] = [];
  let offset = 4;
  for (let cell = 0; cell < columns * rows; cell++) {
    if (offset >= view.byteLength) throw new Error("Missing OPT count");
    const count = view.getInt8(offset++);
    if (count < 0 || offset + count * 14 > view.byteLength) throw new Error("Truncated OPT components");
    const components: OptComponent[] = [];
    for (let i = 0; i < count; i++, offset += 14) {
      components.push({ imageRef: view.getInt16(offset), x: view.getInt16(offset + 2), y: view.getInt16(offset + 4),
        sourceX: view.getInt16(offset + 6), sourceY: view.getInt16(offset + 8),
        width: view.getInt16(offset + 10), height: view.getInt16(offset + 12) });
    }
    cells.push(components);
  }
  if (offset !== view.byteLength) throw new Error("Unexpected OPT trailing bytes");
  return { cellWidth, cellHeight, columns, rows, cells };
}

/** Build commands for one logical cell. imageRef=-1 means the current image;
 * nonnegative references address the Image.images_ array, not global asset IDs.
 * request is relative to this cell; destination maps that requested rectangle.
 * Component order is preserved. Only positive, unmirrored scale is supported.
 */
export function getOptDrawCommands(opt: OptSprite, column: number, row: number,
  destination: OptRect, request: OptRect = { x: 0, y: 0, width: opt.cellWidth, height: opt.cellHeight }): OptDrawCommand[] {
  if (!Number.isInteger(column) || !Number.isInteger(row) || column < 0 || row < 0 || column >= opt.columns || row >= opt.rows)
    throw new Error("OPT cell outside grid");
  if (![...Object.values(request), ...Object.values(destination)].every(Number.isFinite) ||
      request.width <= 0 || request.height <= 0 || destination.width <= 0 || destination.height <= 0)
    throw new Error("OPT requires finite rectangles and positive scale");
  const scaleX = destination.width / request.width, scaleY = destination.height / request.height;
  const commands: OptDrawCommand[] = [];
  for (const c of opt.cells[column + row * opt.columns]) {
    const left = Math.max(request.x, c.x), top = Math.max(request.y, c.y);
    const right = Math.min(request.x + request.width, c.x + c.width);
    const bottom = Math.min(request.y + request.height, c.y + c.height);
    if (right <= left || bottom <= top) continue;
    commands.push({ imageRef: c.imageRef,
      source: { x: c.sourceX + left - c.x, y: c.sourceY + top - c.y, width: right - left, height: bottom - top },
      destination: { x: destination.x + (left - request.x) * scaleX, y: destination.y + (top - request.y) * scaleY,
        width: (right - left) * scaleX, height: (bottom - top) * scaleY } });
  }
  return commands;
}
