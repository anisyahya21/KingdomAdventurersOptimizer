import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const tmpDir = path.resolve(projectRoot, "tmp");

const bundlePath = path.resolve(tmpDir, "trace-facility-placement-townhall.bundle.mjs");
const outJson = path.resolve(tmpDir, "facility-placement-trace-townhall.json");
const outMd = path.resolve(tmpDir, "facility-placement-trace-townhall.md");

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

const report = runtime.traceTownHallFacilityPlacement(runtime.createTownHallMilestoneRuntime);
const markdown = runtime.buildFacilityPlacementTraceMarkdown(report);

fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
fs.writeFileSync(outMd, markdown, "utf8");

console.log(`[trace:facility-placement:townhall] Wrote ${path.relative(projectRoot, outJson)}`);
console.log(`[trace:facility-placement:townhall] Wrote ${path.relative(projectRoot, outMd)}`);
