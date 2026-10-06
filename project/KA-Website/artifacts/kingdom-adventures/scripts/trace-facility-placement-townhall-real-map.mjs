import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const tmpDir = path.resolve(projectRoot, "tmp");

const mapPath = path.resolve(projectRoot, "tmp", "KA_assets", "map", "map_160_160.map");
const bundlePath = path.resolve(tmpDir, "trace-facility-placement-townhall-real-map.bundle.mjs");
const outJson = path.resolve(tmpDir, "facility-placement-trace-townhall-real-map.json");
const outMd = path.resolve(tmpDir, "facility-placement-trace-townhall-real-map.md");

if (!fs.existsSync(mapPath)) {
  throw new Error(`Missing required map file: ${mapPath}`);
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

const moduleUrl = `${pathToFileURL(bundlePath).href}?v=${Date.now()}`;
const runtime = await import(moduleUrl);

const mapBytes = new Uint8Array(fs.readFileSync(mapPath));
const parsed = runtime.parseMapBinarySectionA(mapBytes);
const world = runtime.createWorldFromParsedMap(parsed, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
const { pipeline } = runtime.createTownHallMilestoneRuntime();

const report = runtime.traceTownHallFacilityPlacementOnWorld(world, pipeline, { preferInteriorTownArea: true });
runtime.runRealMapFacilityPlacementTraceMilestone(mapBytes);
runtime.runEffectiveRuntimeLayerResolutionMilestone(mapBytes);
runtime.runRuntimeOverlayLifecycleMilestone(mapBytes);

const markdown = runtime.buildFacilityPlacementTraceMarkdown(report);
fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
fs.writeFileSync(outMd, markdown, "utf8");

console.log(`[trace:facility-placement:townhall:real-map] placement=(${report.facility.command.x},${report.facility.command.y})`);
console.log(`[trace:facility-placement:townhall:real-map] Wrote ${path.relative(projectRoot, outJson)}`);
console.log(`[trace:facility-placement:townhall:real-map] Wrote ${path.relative(projectRoot, outMd)}`);
