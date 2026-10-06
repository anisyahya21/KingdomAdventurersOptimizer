import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const workspaceRoot = path.resolve(projectRoot, "..", "..", "..");
const tmpDir = path.resolve(projectRoot, "tmp");

const visualResolverReportPath = path.resolve(tmpDir, "mapchip-visual-resolver-report.json");
const projectedReportPath = path.resolve(tmpDir, "mapchip-projected-sample-region.json");
const projectedPngPath = path.resolve(tmpDir, "mapchip-projected-sample-region.png");

if (!fs.existsSync(visualResolverReportPath)) {
  throw new Error(
    `Missing required visual resolver output: ${visualResolverReportPath}. Run npm run analyze:mapchip:visual-resolver first.`,
  );
}

const pythonCandidates = [
  process.env.PYTHON,
  path.resolve(workspaceRoot, ".venv", "Scripts", "python.exe"),
  "python",
].filter((value) => typeof value === "string" && value.length > 0);

let pythonExecutable;
for (const candidate of pythonCandidates) {
  const result = spawnSync(candidate, ["--version"], { stdio: "pipe" });
  if (result.status === 0) {
    pythonExecutable = candidate;
    break;
  }
}

if (!pythonExecutable) {
  throw new Error("Could not find a working Python executable for projected sample rendering");
}

const pythonScriptPath = path.resolve(projectRoot, "scripts", "render-mapchip-projected-sample-region.py");
const pythonResult = spawnSync(pythonExecutable, [pythonScriptPath], { cwd: projectRoot, stdio: "inherit" });
if (pythonResult.status !== 0) {
  throw new Error(`Projected sample renderer failed with exit code ${pythonResult.status}`);
}

if (!fs.existsSync(projectedReportPath)) {
  throw new Error(`Projected sample renderer did not produce report JSON: ${projectedReportPath}`);
}
if (!fs.existsSync(projectedPngPath)) {
  throw new Error(`Projected sample renderer did not produce PNG output: ${projectedPngPath}`);
}

const bundlePath = path.resolve(tmpDir, "render-mapchip-projected-sample-region.bundle.mjs");
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
const report = JSON.parse(fs.readFileSync(projectedReportPath, "utf8"));
runtime.runMapChipProjectedSampleRegionMilestone(report);

console.log(
  `[render:mapchip:projected-sample-region] milestone ok image=${path.relative(projectRoot, projectedPngPath)}`,
);
