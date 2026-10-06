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
const mixedWorkDir = path.resolve(tmpDir, "work", "mapchip-projected-mixed");

const visualResolverReportPath = path.resolve(tmpDir, "mapchip-visual-resolver-report.json");
const mapPath = path.resolve(tmpDir, "KA_assets", "map", "map_160_160.map");
const mixedInputPath = path.resolve(mixedWorkDir, "mapchip-projected-mixed-regions-input.json");
const mixedSummaryPath = path.resolve(mixedWorkDir, "mapchip-projected-mixed-regions-summary.json");

fs.mkdirSync(mixedWorkDir, { recursive: true });

if (!fs.existsSync(visualResolverReportPath)) {
  throw new Error(
    `Missing required visual resolver output: ${visualResolverReportPath}. Run npm run analyze:mapchip:visual-resolver first.`,
  );
}
if (!fs.existsSync(mapPath)) {
  throw new Error(`Missing required map file: ${mapPath}`);
}

const bundlePath = path.resolve(tmpDir, "render-mapchip-projected-mixed-regions.bundle.mjs");
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

const visualReport = JSON.parse(fs.readFileSync(visualResolverReportPath, "utf8"));
const resolutionById = new Map((visualReport.resolutions ?? []).map((entry) => [Number(entry.mapChipId), entry]));
const FIELD_KEYS = ["f1", "f2", "f3", "f4", "f5"];
const TARGET_REGION_COUNT = 12;

function f1BiomeLabel(value) {
  const biomes = ["grass", "sand", "snow", "swamp", "volcano", "rock", "water"];
  if (value === null || value === undefined) {
    return "unknown";
  }
  const idx = Math.abs(Math.trunc(Number(value))) % biomes.length;
  return biomes[idx] ?? "unknown";
}

function sanitizeFieldValue(value) {
  if (value === null || value === undefined) {
    return null;
  }
  const asNumber = Number(value);
  if (!Number.isFinite(asNumber)) {
    return null;
  }
  return Math.trunc(asNumber);
}

function extractTileFields(cellFields) {
  return {
    f1: sanitizeFieldValue(cellFields?.f1),
    f2: sanitizeFieldValue(cellFields?.f2),
    f3: sanitizeFieldValue(cellFields?.f3),
    f4: sanitizeFieldValue(cellFields?.f4),
    f5: sanitizeFieldValue(cellFields?.f5),
  };
}

function buildRegionCandidate(x0, y0, size) {
  const tiles = [];
  const f2Set = new Set();
  const semanticSet = new Set();
  let unresolvedCount = 0;
  let hasTerrain = false;
  let hasRoad = false;
  let hasSpecial = false;
  const sourcePresentCounts = Object.fromEntries(FIELD_KEYS.map((field) => [field, 0]));
  const sourceNullCounts = Object.fromEntries(FIELD_KEYS.map((field) => [field, 0]));
  const payloadPresentCounts = Object.fromEntries(FIELD_KEYS.map((field) => [field, 0]));
  const payloadNullCounts = Object.fromEntries(FIELD_KEYS.map((field) => [field, 0]));
  const propagationMismatchCounts = Object.fromEntries(FIELD_KEYS.map((field) => [field, 0]));
  let tilesWithAllFields = 0;
  let tilesWithAnyMissingField = 0;
  let roadLikeCount = 0;
  let terrainLikeCount = 0;
  let objectLikeCount = 0;
  let indoorLikeCount = 0;
  let waterLikeCount = 0;
  let reclaimedLikeCount = 0;
  const biomeCounts = {};

  for (let y = y0; y < y0 + size; y++) {
    for (let x = x0; x < x0 + size; x++) {
      const index = y * parsed.width + x;
      const sourceF0 = sanitizeFieldValue(parsed.cells[index]?.fields?.f0);
      const sourceFields = extractTileFields(parsed.cells[index]?.fields ?? {});
      const f2 = sourceFields.f2;
      const annotation = world.getAnnotationCellState(x, y);
      const resolution = resolutionById.get(f2);

      const mapChipName = resolution?.mapChipName ?? `Unknown (${f2})`;
      const semanticGroup = annotation.semanticGroup;
      const resolved = Boolean(resolution?.resolved && resolution?.spriteSourcePng);

      if (!resolved) {
        unresolvedCount += 1;
      }

      f2Set.add(f2);
      semanticSet.add(semanticGroup);

      if (semanticGroup.includes("terrain")) {
        hasTerrain = true;
        terrainLikeCount += 1;
      }
      if (semanticGroup.includes("road/path")) {
        hasRoad = true;
        roadLikeCount += 1;
      }
      if (semanticGroup.includes("object")) {
        objectLikeCount += 1;
      }
      if (/floor|storehouse|cave|dungeon|indoor|building/i.test(mapChipName)) {
        indoorLikeCount += 1;
      }
      if (sourceF0 === 1 || /water|swamp|coast|shore|sea|river/i.test(mapChipName)) {
        waterLikeCount += 1;
      }
      if (sourceFields.f3 !== null && sourceFields.f4 !== null && sourceFields.f3 !== sourceFields.f4) {
        reclaimedLikeCount += 1;
      }
      const biomeLabel = f1BiomeLabel(sourceFields.f1);
      biomeCounts[biomeLabel] = Number(biomeCounts[biomeLabel] ?? 0) + 1;
      if (
        semanticGroup.includes("special overlay") ||
        /edge|side|boundary|switch|construction|fog|burn|smoke|entrance|port|bridge/i.test(mapChipName)
      ) {
        hasSpecial = true;
      }

      const tilePayloadFields = { ...sourceFields };
      let tileMissingFieldCount = 0;
      for (const field of FIELD_KEYS) {
        const sourceValue = sourceFields[field];
        const payloadValue = tilePayloadFields[field];
        if (sourceValue === null) {
          sourceNullCounts[field] += 1;
        } else {
          sourcePresentCounts[field] += 1;
        }
        if (payloadValue === null) {
          payloadNullCounts[field] += 1;
          tileMissingFieldCount += 1;
        } else {
          payloadPresentCounts[field] += 1;
        }
        if (sourceValue !== payloadValue) {
          propagationMismatchCounts[field] += 1;
        }
      }
      if (tileMissingFieldCount === 0) {
        tilesWithAllFields += 1;
      } else {
        tilesWithAnyMissingField += 1;
      }

      tiles.push({
        x,
        y,
        ...tilePayloadFields,
        mapChipName,
        semanticGroup,
        resolved,
        spriteSourcePng: resolution?.spriteSourcePng,
        drawLayer: resolution?.drawLayer ?? 0,
        sizeWidth: resolution?.sizeWidth ?? 1,
        sizeHeight: resolution?.sizeHeight ?? 1,
        rotation: resolution?.rotation ?? 0,
      });
    }
  }

  const diversityScore =
    f2Set.size * 10 +
    semanticSet.size * 12 +
    (hasTerrain ? 25 : 0) +
    (hasRoad ? 25 : 0) +
    (hasSpecial ? 15 : 0) +
    unresolvedCount * 2;
  const tileCount = Math.max(1, tiles.length);
  const dominantBiome = Object.entries(biomeCounts)
    .sort((left, right) => Number(right[1]) - Number(left[1]) || String(left[0]).localeCompare(String(right[0])))[0]?.[0] ?? "unknown";

  const traits = {
    dominantBiome,
    biomeCounts,
    roadRatio: Number((roadLikeCount / tileCount).toFixed(4)),
    terrainRatio: Number((terrainLikeCount / tileCount).toFixed(4)),
    objectRatio: Number((objectLikeCount / tileCount).toFixed(4)),
    indoorRatio: Number((indoorLikeCount / tileCount).toFixed(4)),
    waterRatio: Number((waterLikeCount / tileCount).toFixed(4)),
    reclaimedRatio: Number((reclaimedLikeCount / tileCount).toFixed(4)),
    specialRatio: Number((Number(hasSpecial ? 1 : 0)).toFixed(4)),
  };

  return {
    x: x0,
    y: y0,
    width: size,
    height: size,
    tiles,
    f2Ids: [...f2Set].sort((a, b) => a - b),
    semanticGroups: [...semanticSet].sort(),
    unresolvedCount,
    hasTerrain,
    hasRoad,
    hasSpecial,
    diversityScore,
    traits,
    fieldPropagationStats: {
      tileCount: tiles.length,
      tilesWithAllFields,
      tilesWithAnyMissingField,
      propagationMismatchCounts,
    },
    extractionCoverageStats: {
      sourcePresentCounts,
      payloadPresentCounts,
      sourceNullCounts,
      payloadNullCounts,
    },
    missingFieldDiagnostics: {
      missingFieldCounts: payloadNullCounts,
      nullFieldCounts: payloadNullCounts,
      tilesWithAnyMissingField,
    },
    fieldPreservationRates: {
      byField: Object.fromEntries(
        FIELD_KEYS.map((field) => {
          const sourcePresent = Number(sourcePresentCounts[field] ?? 0);
          const payloadPresent = Number(payloadPresentCounts[field] ?? 0);
          const mismatchCount = Number(propagationMismatchCounts[field] ?? 0);
          return [
            field,
            {
              sourcePresentRate: Number((sourcePresent / Math.max(1, tiles.length)).toFixed(4)),
              payloadPresentRate: Number((payloadPresent / Math.max(1, tiles.length)).toFixed(4)),
              preservationRate: Number((1 - mismatchCount / Math.max(1, tiles.length)).toFixed(4)),
            },
          ];
        }),
      ),
    },
    regionFieldAvailability: Object.fromEntries(
      FIELD_KEYS.map((field) => {
        const payloadPresent = Number(payloadPresentCounts[field] ?? 0);
        return [
          field,
          {
            available: payloadPresent > 0,
            presentCount: payloadPresent,
            tileCoverageRate: Number((payloadPresent / Math.max(1, tiles.length)).toFixed(4)),
          },
        ];
      }),
    ),
  };
}

function intersectsWithMargin(a, b, margin = 1) {
  const aLeft = a.x - margin;
  const aRight = a.x + a.width + margin;
  const aTop = a.y - margin;
  const aBottom = a.y + a.height + margin;

  const bLeft = b.x - margin;
  const bRight = b.x + b.width + margin;
  const bTop = b.y - margin;
  const bBottom = b.y + b.height + margin;

  return !(aRight <= bLeft || bRight <= aLeft || aBottom <= bTop || bBottom <= aTop);
}

const size = 8;
const step = 2;
const candidates = [];
for (let y = 0; y <= parsed.height - size; y += step) {
  for (let x = 0; x <= parsed.width - size; x += step) {
    const candidate = buildRegionCandidate(x, y, size);
    if (candidate.f2Ids.length < 4) {
      continue;
    }
    candidates.push(candidate);
  }
}

candidates.sort((left, right) => right.diversityScore - left.diversityScore);

const selected = [];

function pickCandidate(predicate) {
  for (const candidate of candidates) {
    if (!predicate(candidate)) {
      continue;
    }
    if (selected.some((item) => intersectsWithMargin(item, candidate, 2))) {
      continue;
    }
    selected.push(candidate);
    return;
  }
}

pickCandidate((candidate) => candidate.hasTerrain && candidate.hasRoad && candidate.f2Ids.length >= 6);
pickCandidate((candidate) => candidate.hasSpecial);
pickCandidate((candidate) => candidate.unresolvedCount > 0);

const diversitySamplingPlan = [
  { id: "swamp-region", predicate: (candidate) => candidate.traits.dominantBiome === "swamp" || candidate.traits.waterRatio >= 0.45 },
  { id: "snow-region", predicate: (candidate) => candidate.traits.dominantBiome === "snow" },
  { id: "volcano-region", predicate: (candidate) => candidate.traits.dominantBiome === "volcano" },
  { id: "desert-sand-region", predicate: (candidate) => candidate.traits.dominantBiome === "sand" },
  { id: "indoor-heavy-region", predicate: (candidate) => candidate.traits.indoorRatio >= 0.35 },
  { id: "road-heavy-region", predicate: (candidate) => candidate.traits.roadRatio >= 0.28 },
  { id: "reclaimed-land-region", predicate: (candidate) => candidate.traits.reclaimedRatio >= 0.4 },
  { id: "coast-water-edge-region", predicate: (candidate) => candidate.traits.waterRatio >= 0.35 && candidate.traits.terrainRatio >= 0.2 },
  { id: "dense-building-region", predicate: (candidate) => candidate.traits.objectRatio >= 0.2 || candidate.traits.indoorRatio >= 0.45 },
  {
    id: "sparse-wilderness-region",
    predicate: (candidate) => candidate.traits.terrainRatio >= 0.55 && candidate.traits.objectRatio <= 0.08 && candidate.traits.roadRatio <= 0.12,
  },
];

for (const plan of diversitySamplingPlan) {
  pickCandidate((candidate) => plan.predicate(candidate));
}

for (const candidate of candidates) {
  if (selected.length >= TARGET_REGION_COUNT) {
    break;
  }
  if (selected.some((item) => intersectsWithMargin(item, candidate, 2))) {
    continue;
  }
  selected.push(candidate);
}

for (const candidate of candidates) {
  if (selected.length >= TARGET_REGION_COUNT) {
    break;
  }
  if (selected.includes(candidate)) {
    continue;
  }
  selected.push(candidate);
}

if (selected.length < 3) {
  throw new Error("Could not select at least 3 mixed-chip regions");
}

const selectedRegions = selected.slice(0, TARGET_REGION_COUNT).map((candidate, index) => ({
  regionId: index + 1,
  x: candidate.x,
  y: candidate.y,
  width: candidate.width,
  height: candidate.height,
  diversityScore: candidate.diversityScore,
  unresolvedCount: candidate.unresolvedCount,
  f2Ids: candidate.f2Ids,
  semanticGroups: candidate.semanticGroups,
  hasTerrain: candidate.hasTerrain,
  hasRoad: candidate.hasRoad,
  hasSpecial: candidate.hasSpecial,
  sampleTraits: candidate.traits,
  fieldPropagationStats: candidate.fieldPropagationStats,
  extractionCoverageStats: candidate.extractionCoverageStats,
  missingFieldDiagnostics: candidate.missingFieldDiagnostics,
  fieldPreservationRates: candidate.fieldPreservationRates,
  regionFieldAvailability: candidate.regionFieldAvailability,
  tiles: candidate.tiles,
}));

const upstreamFieldPropagationAggregate = {
  tileCount: 0,
  fieldPropagationStats: {
    tilesWithAllFields: 0,
    tilesWithAnyMissingField: 0,
    propagationMismatchCounts: Object.fromEntries(FIELD_KEYS.map((field) => [field, 0])),
  },
  extractionCoverageStats: {
    sourcePresentCounts: Object.fromEntries(FIELD_KEYS.map((field) => [field, 0])),
    payloadPresentCounts: Object.fromEntries(FIELD_KEYS.map((field) => [field, 0])),
    sourceNullCounts: Object.fromEntries(FIELD_KEYS.map((field) => [field, 0])),
    payloadNullCounts: Object.fromEntries(FIELD_KEYS.map((field) => [field, 0])),
  },
};

for (const region of selectedRegions) {
  const regionTileCount = Number(region.fieldPropagationStats?.tileCount ?? 0);
  upstreamFieldPropagationAggregate.tileCount += regionTileCount;
  upstreamFieldPropagationAggregate.fieldPropagationStats.tilesWithAllFields += Number(region.fieldPropagationStats?.tilesWithAllFields ?? 0);
  upstreamFieldPropagationAggregate.fieldPropagationStats.tilesWithAnyMissingField += Number(region.fieldPropagationStats?.tilesWithAnyMissingField ?? 0);
  for (const field of FIELD_KEYS) {
    upstreamFieldPropagationAggregate.fieldPropagationStats.propagationMismatchCounts[field] += Number(
      region.fieldPropagationStats?.propagationMismatchCounts?.[field] ?? 0,
    );
    upstreamFieldPropagationAggregate.extractionCoverageStats.sourcePresentCounts[field] += Number(
      region.extractionCoverageStats?.sourcePresentCounts?.[field] ?? 0,
    );
    upstreamFieldPropagationAggregate.extractionCoverageStats.payloadPresentCounts[field] += Number(
      region.extractionCoverageStats?.payloadPresentCounts?.[field] ?? 0,
    );
    upstreamFieldPropagationAggregate.extractionCoverageStats.sourceNullCounts[field] += Number(
      region.extractionCoverageStats?.sourceNullCounts?.[field] ?? 0,
    );
    upstreamFieldPropagationAggregate.extractionCoverageStats.payloadNullCounts[field] += Number(
      region.extractionCoverageStats?.payloadNullCounts?.[field] ?? 0,
    );
  }
}

upstreamFieldPropagationAggregate.fieldPreservationRates = Object.fromEntries(
  FIELD_KEYS.map((field) => {
    const totalTiles = Math.max(1, upstreamFieldPropagationAggregate.tileCount);
    const mismatchCount = Number(upstreamFieldPropagationAggregate.fieldPropagationStats.propagationMismatchCounts[field] ?? 0);
    const payloadPresent = Number(upstreamFieldPropagationAggregate.extractionCoverageStats.payloadPresentCounts[field] ?? 0);
    const sourcePresent = Number(upstreamFieldPropagationAggregate.extractionCoverageStats.sourcePresentCounts[field] ?? 0);
    return [
      field,
      {
        sourcePresentRate: Number((sourcePresent / totalTiles).toFixed(4)),
        payloadPresentRate: Number((payloadPresent / totalTiles).toFixed(4)),
        preservationRate: Number((1 - mismatchCount / totalTiles).toFixed(4)),
      },
    ];
  }),
);

const mixedInput = {
  tileWidth: 96,
  tileHeight: 48,
  padding: 40,
  outputDir: path.relative(projectRoot, mixedWorkDir).replaceAll("\\", "/"),
  visualResolutions: visualReport.resolutions ?? [],
  upstreamFieldPropagationStats: upstreamFieldPropagationAggregate,
  regions: selectedRegions,
};

fs.writeFileSync(mixedInputPath, JSON.stringify(mixedInput, null, 2), "utf8");

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
  throw new Error("Could not find a working Python executable for mixed projected rendering");
}

const pythonScriptPath = path.resolve(projectRoot, "scripts", "render-mapchip-projected-mixed-regions.py");
console.log("[render:mapchip:projected-mixed-regions] pythonScriptPath", pythonScriptPath);
console.log("[render:mapchip:projected-mixed-regions] mixedInputPath", mixedInputPath);
console.log("[render:mapchip:projected-mixed-regions] mixedSummaryPath", mixedSummaryPath);
const pythonResult = spawnSync(pythonExecutable, [pythonScriptPath, mixedInputPath], { cwd: projectRoot, stdio: "inherit" });
if (pythonResult.status !== 0) {
  throw new Error(`Mixed region projected renderer failed with exit code ${pythonResult.status}`);
}

if (!fs.existsSync(mixedSummaryPath)) {
  throw new Error(`Mixed region projected renderer did not produce summary: ${mixedSummaryPath}`);
}

const summary = JSON.parse(fs.readFileSync(mixedSummaryPath, "utf8"));
runtime.runMapChipProjectedMixedRegionsMilestone(summary);

for (const region of summary.regions ?? []) {
  console.log(
    `[render:mapchip:projected-mixed-regions] region=${region.regionId} bounds=(${region.bounds.x},${region.bounds.y}) ` +
      `f2=${region.f2Ids.length} semantic=${region.semanticGroups.length} ` +
      `metadataAnchors=${region.modeStats.metadata.metadataAnchorCount} fallbackAnchors=${region.modeStats.metadata.bottomCenterFallbackCount}`,
  );
}

console.log("[render:mapchip:projected-mixed-regions] milestone ok");
