import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const kaWebsiteRoot = path.resolve(projectRoot, "..", "..");
const tmpDir = path.resolve(projectRoot, "tmp");

const mapPath = path.resolve(projectRoot, "tmp", "KA_assets", "map", "map_160_160.map");
const mapChipCsvPath = path.resolve(kaWebsiteRoot, "data", "sheet-research", "raw-copies", "KA GameData - MapChip.csv");
const mapAssetsJsonPath = path.resolve(kaWebsiteRoot, "tools", "asset_extractor", "generated", "mappings", "map_assets.json");

const spriteRoot = path.resolve(kaWebsiteRoot, "tools", "asset_extractor", "generated", "sprites", "chip");
const apkExtractedRoot = path.resolve(kaWebsiteRoot, "tools", "asset_extractor", "apk_extracted");

const imgInfPath = path.resolve(kaWebsiteRoot, "tools", "asset_extractor", "apk_extracted", "img.inf");
const sebInfPath = path.resolve(kaWebsiteRoot, "tools", "asset_extractor", "apk_extracted", "seb.inf");

const bundlePath = path.resolve(tmpDir, "analyze-mapchip-visual-resolver.bundle.mjs");
const outJson = path.resolve(tmpDir, "mapchip-visual-resolver-report.json");
const outMd = path.resolve(tmpDir, "mapchip-visual-resolver-report.md");
const outSampleJson = path.resolve(tmpDir, "mapchip-visual-resolver-sample-region.json");
const outSampleMd = path.resolve(tmpDir, "mapchip-visual-resolver-sample-region.md");

for (const requiredPath of [mapPath, mapChipCsvPath, mapAssetsJsonPath]) {
  if (!fs.existsSync(requiredPath)) {
    throw new Error(`Missing required file: ${requiredPath}`);
  }
}

function toPosixRelativeFromKaWebsite(absPath) {
  return path.relative(kaWebsiteRoot, absPath).replace(/\\/g, "/");
}

function collectRelevantFiles(rootDir) {
  const output = new Set();
  if (!fs.existsSync(rootDir)) {
    return output;
  }

  const stack = [rootDir];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.resolve(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(absolute);
        continue;
      }

      const lower = entry.name.toLowerCase();
      if (lower.endsWith(".png") || lower.endsWith(".seb") || lower.endsWith(".opt") || lower.endsWith(".optinfo") || lower.endsWith(".inf")) {
        output.add(toPosixRelativeFromKaWebsite(absolute));
      }
    }
  }

  return output;
}

fs.mkdirSync(tmpDir, { recursive: true });

await build({
  absWorkingDir: projectRoot,
  entryPoints: ["src/runtime/world-builder/index.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: bundlePath,
  logLevel: "info",
});

const runtime = await import(`${pathToFileURL(bundlePath).href}?v=${Date.now()}`);

const mapBytes = new Uint8Array(fs.readFileSync(mapPath));
const parsed = runtime.parseMapBinarySectionA(mapBytes);
const world = runtime.createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
const { pipeline } = runtime.createTownHallMilestoneRuntime();
const trace = runtime.traceTownHallFacilityPlacementOnWorld(world, pipeline, { preferInteriorTownArea: true });

const sampleRegion = {
  x: Math.max(0, trace.facility.command.x - 3),
  y: Math.max(0, trace.facility.command.y - 3),
  width: 8,
  height: 8,
};

const existingRelativeFilePaths = new Set([
  ...collectRelevantFiles(spriteRoot),
  ...collectRelevantFiles(apkExtractedRoot),
]);

const mapAssetsByMapchipId = JSON.parse(fs.readFileSync(mapAssetsJsonPath, "utf8"));
const mapChipCsvText = fs.readFileSync(mapChipCsvPath, "utf8");

const report = runtime.buildMapChipVisualResolverReport({
  mapChipCsvText,
  observedF2Ids: runtime.OBSERVED_F2_MAPCHIP_IDS,
  mapAssetsByMapchipId,
  spriteRootRelativePath: toPosixRelativeFromKaWebsite(spriteRoot),
  existingRelativeFilePaths,
  parsedMap: parsed,
  sampleRegion,
  infPaths: {
    imgInfRelativePath: toPosixRelativeFromKaWebsite(imgInfPath),
    sebInfRelativePath: toPosixRelativeFromKaWebsite(sebInfPath),
  },
  lookupRoots: {
    sebRootRelativePath: toPosixRelativeFromKaWebsite(path.resolve(apkExtractedRoot, "chara")),
    optRootRelativePath: toPosixRelativeFromKaWebsite(apkExtractedRoot),
  },
});

runtime.runMapChipVisualResolverMilestone(report);

const markdown = runtime.buildMapChipVisualResolverMarkdown(report);
const sampleMarkdownLines = [
  "# MapChip Visual Resolver Sample Region",
  "",
  `- Origin: (${report.sampleRegion.x}, ${report.sampleRegion.y})`,
  `- Size: ${report.sampleRegion.width}x${report.sampleRegion.height}`,
  `- Tiles: ${report.sampleRegion.tileCount}`,
  `- Resolved tiles: ${report.sampleRegion.resolvedTileCount}`,
  `- Unresolved tiles: ${report.sampleRegion.unresolvedTileCount}`,
  "",
  "## First 100 Tiles",
  ...report.sampleRegion.tiles.slice(0, 100).map((tile) => {
    if (tile.resolved) {
      return `- (${tile.x},${tile.y}) f2=${tile.f2} ${tile.mapChipName} -> ${tile.spriteSourcePng}`;
    }
    return `- (${tile.x},${tile.y}) f2=${tile.f2} ${tile.mapChipName} -> fallback ${tile.debugFallbackColor}`;
  }),
  "",
];

fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
fs.writeFileSync(outMd, markdown, "utf8");
fs.writeFileSync(outSampleJson, JSON.stringify(report.sampleRegion, null, 2), "utf8");
fs.writeFileSync(outSampleMd, `${sampleMarkdownLines.join("\n")}\n`, "utf8");

console.log(`[analyze:mapchip:visual-resolver] observed=${report.observedF2Count} resolved=${report.resolvedMapChipVisualCount} unresolved=${report.unresolvedMapChipIds.length}`);
console.log(`[analyze:mapchip:visual-resolver] Wrote ${path.relative(projectRoot, outJson)}`);
console.log(`[analyze:mapchip:visual-resolver] Wrote ${path.relative(projectRoot, outMd)}`);
console.log(`[analyze:mapchip:visual-resolver] Wrote ${path.relative(projectRoot, outSampleJson)}`);
console.log(`[analyze:mapchip:visual-resolver] Wrote ${path.relative(projectRoot, outSampleMd)}`);
