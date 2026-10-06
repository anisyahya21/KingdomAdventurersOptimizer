import { getSebOptDrawCommands, type OptSprite } from "./opt-sprite";
import { nativeWaterRules, type recoverWaterTiles } from "./native-water";

type WaterAssets = Map<string, { image: HTMLImageElement; opt: OptSprite }>;
type Camera = { zoom: number; offsetX: number; offsetY: number };
type WaterTiles = ReturnType<typeof recoverWaterTiles>;
const surfaces = new WeakMap<WaterAssets, {
  canvas: HTMLCanvasElement; pattern: CanvasPattern;
  opaque: Map<string, HTMLCanvasElement>; alpha: number;
}>();

function drawSprite(context: CanvasRenderingContext2D, image: CanvasImageSource, opt: OptSprite,
  sprite: readonly number[], x: number, y: number, zoom: number) {
  for (const { source: s, destination: d } of getSebOptDrawCommands(opt, sprite, { x, y }, zoom)) {
    context.drawImage(image, s.x, s.y, s.width, s.height, d.x, d.y, d.width, d.height);
  }
}

function createSurface(assets: WaterAssets) {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d")!;
  const opaque = new Map<string, HTMLCanvasElement>();
  let alpha: number | undefined;
  for (const name of ["mizu00", "mizu01", "mizu02", "mizu03"]) {
    const asset = assets.get(name)!;
    const tile = document.createElement("canvas");
    tile.width = asset.image.width; tile.height = asset.image.height;
    const tileContext = tile.getContext("2d")!;
    tileContext.drawImage(asset.image, 0, 0);
    const pixels = tileContext.getImageData(0, 0, tile.width, tile.height);
    for (let i = 3; i < pixels.data.length; i += 4) {
      if (!pixels.data[i]) continue;
      alpha ??= pixels.data[i];
      if (pixels.data[i] !== alpha) throw new Error("Water surface requires uniform original tile opacity");
      pixels.data[i] = 255;
    }
    tileContext.putImageData(pixels, 0, 0);
    opaque.set(name, tile);
  }
  // Assemble the actual deep-water OPT at native pixel size before scaling.
  // Its two-dimensional repeat is 48x24; diamonds fill this rectangle exactly.
  const repeat = document.createElement("canvas");
  repeat.width = 48; repeat.height = 24;
  const repeatContext = repeat.getContext("2d")!;
  const deep = assets.get("mizu03")!;
  for (let y = -3; y <= 3; y++) for (let x = -3; x <= 3; x++) {
    drawSprite(repeatContext, opaque.get("mizu03")!, deep.opt, nativeWaterRules.waterSprite,
      (x - y) * 24, (x + y) * 12, 1);
  }
  const coverage = repeatContext.getImageData(0, 0, 48, 24).data;
  for (let i = 3; i < coverage.length; i += 4) {
    if (coverage[i] !== 255) throw new Error("Original water repeat has a coverage gap");
  }
  return { canvas, opaque, pattern: context.createPattern(repeat, "repeat")!, alpha: alpha! / 255 };
}

/** Website presentation extension requested by the user, not a recovered map-size rule.
 * Repeat original deep water over the viewport; no extra cells enter map data/hit testing.
 * Composite uniform-alpha water once, avoiding gaps between separately scaled diamonds.
 */
export function drawWaterSurface(context: CanvasRenderingContext2D, assets: WaterAssets,
  tiles: WaterTiles, camera: Camera, width: number, height: number) {
  let surface = surfaces.get(assets);
  if (!surface) { surface = createSurface(assets); surfaces.set(assets, surface); }
  const { canvas, pattern, opaque, alpha } = surface;
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const water = canvas.getContext("2d")!;
  water.imageSmoothingEnabled = false;
  const { zoom, offsetX, offsetY } = camera;
  pattern.setTransform(new DOMMatrix([zoom, 0, 0, zoom, offsetX, offsetY]));
  water.fillStyle = pattern;
  water.fillRect(0, 0, width, height);
  const visible = tiles.filter(tile => {
    const x = offsetX + (tile.x - tile.y) * 24 * zoom;
    const y = offsetY + (tile.x + tile.y) * 12 * zoom;
    return x >= -60 * zoom && x <= width + 60 * zoom && y >= -60 * zoom && y <= height + 60 * zoom;
  });
  for (const tile of visible) {
    if (tile.image === "mizu03") continue;
    drawSprite(water, opaque.get(tile.image)!, assets.get(tile.image)!.opt, nativeWaterRules.waterSprite,
      offsetX / zoom + (tile.x - tile.y) * 24, offsetY / zoom + (tile.x + tile.y) * 12, zoom);
  }
  context.save();
  context.globalAlpha *= alpha;
  context.drawImage(canvas, 0, 0);
  context.restore();
  const edge = assets.get("mizu_edge")!;
  for (const tile of visible) for (const layer of tile.layers) {
    drawSprite(context, edge.image, edge.opt, nativeWaterRules.edgeSprites[layer],
      offsetX / zoom + (tile.x - tile.y) * 24, offsetY / zoom + (tile.x + tile.y) * 12, zoom);
  }
}
