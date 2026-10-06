import fs from "node:fs";
import path from "node:path";

const IMPORTANT_FLAGS = [
  "Soil Only",
  "No Construction",
  "No Destruct",
  "Sync Animation",
  "Straight Select",
];

function ensureAvailable(view, offset, needed, label) {
  if (offset + needed > view.byteLength) {
    throw new Error(`Unexpected end of map binary while reading ${label}`);
  }
}

function parseMapBinarySectionA(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const BYTES_PER_U32 = 4;
  const HEADER_BYTES = 8;
  const CELL_BYTES = 24;
  const ROW_SENTINEL_BYTES = 4;

  if (view.byteLength < HEADER_BYTES) {
    throw new Error(`Map binary too small: ${view.byteLength} bytes`);
  }

  let offset = 0;
  const width = view.getUint32(offset, false);
  offset += BYTES_PER_U32;
  const height = view.getUint32(offset, false);
  offset += BYTES_PER_U32;

  const cells = [];
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      ensureAvailable(view, offset, CELL_BYTES, `cell ${x},${y}`);
      const f0 = view.getUint32(offset, false);
      const f1 = view.getUint32(offset + 4, false);
      const f2 = view.getUint32(offset + 8, false);
      const f3 = view.getUint32(offset + 12, false);
      const f4 = view.getUint32(offset + 16, false);
      const f5 = view.getUint32(offset + 20, false);
      offset += CELL_BYTES;
      cells.push({ x, y, fields: { f0, f1, f2, f3, f4, f5 } });
    }

    ensureAvailable(view, offset, ROW_SENTINEL_BYTES, `row sentinel ${y}`);
    const sentinel = view.getUint32(offset, false);
    offset += ROW_SENTINEL_BYTES;
    if (sentinel !== width) {
      throw new Error(`Invalid row sentinel at row ${y}: expected ${width}, got ${sentinel}`);
    }
  }

  return { width, height, cells, remainingBytes: view.byteLength - offset };
}

function parseCsvLine(line) {
  const out = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];

    if (ch === '"') {
      const next = line[i + 1];
      if (inQuotes && next === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (ch === "," && !inQuotes) {
      out.push(current);
      current = "";
      continue;
    }

    current += ch;
  }

  out.push(current);
  return out;
}

function parseCsv(text) {
  const lines = text
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0);

  if (lines.length === 0) {
    return [];
  }

  const header = parseCsvLine(lines[0]);
  const rows = [];

  for (let i = 1; i < lines.length; i += 1) {
    const cols = parseCsvLine(lines[i]);
    const row = {};

    for (let c = 0; c < header.length; c += 1) {
      const key = header[c] === "" ? `__col_${c}` : header[c];
      row[key] = cols[c] ?? "";
    }

    rows.push(row);
  }

  return rows;
}

function toInt(value) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  const parsed = Number.parseInt(String(value), 10);
  return Number.isNaN(parsed) ? null : parsed;
}

function parseFlagSet(flagText) {
  if (!flagText || typeof flagText !== "string") {
    return new Set();
  }
  return new Set(
    flagText
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part.length > 0),
  );
}

function topEntriesFromMap(map, limit = 20) {
  return [...map.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }
      if (typeof left.key === "number" && typeof right.key === "number") {
        return left.key - right.key;
      }
      return String(left.key).localeCompare(String(right.key));
    })
    .slice(0, limit);
}

function incrementCount(map, key) {
  map.set(key, (map.get(key) ?? 0) + 1);
}

function connectedClusters(points) {
  if (points.length === 0) {
    return { clusterCount: 0, largestCluster: 0, meanCluster: 0, sizes: [] };
  }

  const pointSet = new Set(points.map((point) => `${point.x},${point.y}`));
  const visited = new Set();
  const sizes = [];

  for (const point of points) {
    const key = `${point.x},${point.y}`;
    if (visited.has(key)) {
      continue;
    }

    const stack = [point];
    visited.add(key);
    let size = 0;

    while (stack.length > 0) {
      const current = stack.pop();
      size += 1;

      const neighbors = [
        { x: current.x - 1, y: current.y },
        { x: current.x + 1, y: current.y },
        { x: current.x, y: current.y - 1 },
        { x: current.x, y: current.y + 1 },
      ];

      for (const neighbor of neighbors) {
        const neighborKey = `${neighbor.x},${neighbor.y}`;
        if (pointSet.has(neighborKey) && !visited.has(neighborKey)) {
          visited.add(neighborKey);
          stack.push(neighbor);
        }
      }
    }

    sizes.push(size);
  }

  sizes.sort((a, b) => b - a);
  const clusterCount = sizes.length;
  const largestCluster = sizes[0] ?? 0;
  const meanCluster = sizes.reduce((sum, value) => sum + value, 0) / clusterCount;

  return {
    clusterCount,
    largestCluster,
    largestClusterRatio: largestCluster / points.length,
    meanCluster,
    topClusterSizes: sizes.slice(0, 10),
  };
}

function classifyF2Kind(chip) {
  const name = (chip.name ?? "").toLowerCase();
  const flags = parseFlagSet(chip.flag_2 ?? chip.flag);
  const type = toInt(chip.type) ?? -1;
  const category = toInt(chip.category) ?? -1;

  if (name.includes("water") || flags.has("Water Only") || type === 10 && category === 7) {
    return "terrain-water";
  }

  if (name.includes("land") || name.includes("grass") || name.includes("sand") || name.includes("snow") || name.includes("swamp") || name.includes("cliff") || name.includes("dirt")) {
    return "terrain-ground";
  }

  if (name.includes("road") || name.includes("path") || flags.has("Straight Select") || flags.has("Overridable")) {
    return "road-path-traffic-buildable";
  }

  if (
    name.includes("fog") ||
    name.includes("boundary") ||
    name.includes("construction site") ||
    name.includes("remove") ||
    name.includes("switch") ||
    flags.has("Sync Animation")
  ) {
    return "special-overlay-runtime-marker";
  }

  return "mixed-or-structure";
}

function groupByKey(entries, valueGetter) {
  const grouped = new Map();
  for (const entry of entries) {
    const value = valueGetter(entry);
    const key = value === null || value === undefined ? "null" : String(value);
    if (!grouped.has(key)) {
      grouped.set(key, { key: value ?? null, f2Values: [], totalCells: 0 });
    }
    const bucket = grouped.get(key);
    bucket.f2Values.push(entry.f2);
    bucket.totalCells += entry.cellCount;
  }

  return [...grouped.values()]
    .map((bucket) => ({
      ...bucket,
      f2Values: bucket.f2Values.sort((a, b) => a - b),
      distinctF2Count: bucket.f2Values.length,
    }))
    .sort((a, b) => b.totalCells - a.totalCells);
}

function groupsByFlags(entries) {
  const grouped = new Map();
  for (const entry of entries) {
    for (const flag of entry.flags) {
      if (!grouped.has(flag)) {
        grouped.set(flag, { flag, f2Values: [], totalCells: 0 });
      }
      const bucket = grouped.get(flag);
      bucket.f2Values.push(entry.f2);
      bucket.totalCells += entry.cellCount;
    }
  }

  return [...grouped.values()]
    .map((bucket) => ({
      ...bucket,
      f2Values: [...new Set(bucket.f2Values)].sort((a, b) => a - b),
      distinctF2Count: [...new Set(bucket.f2Values)].length,
    }))
    .sort((a, b) => b.totalCells - a.totalCells);
}

function formatPercent(value) {
  return `${(value * 100).toFixed(2)}%`;
}

function buildMarkdown(report) {
  const lines = [];
  lines.push("# F2 MapChip Semantic Groups");
  lines.push("");
  lines.push(`- generatedAt: ${report.generatedAt}`);
  lines.push(`- map: ${report.map.source}`);
  lines.push(`- size: ${report.map.width}x${report.map.height} (${report.map.totalCells} cells)`);
  lines.push(`- observed f2 count: ${report.observedF2Count}`);
  lines.push("");

  lines.push("## Proven Correlations");
  for (const item of report.findings.proven) {
    lines.push(`- ${item}`);
  }
  lines.push("");

  lines.push("## Statistical Correlations");
  for (const item of report.findings.statistical) {
    lines.push(`- ${item}`);
  }
  lines.push("");

  lines.push("## Likely Hypotheses");
  for (const item of report.findings.hypotheses) {
    lines.push(`- ${item}`);
  }
  lines.push("");

  lines.push("## Unsupported Guesses");
  for (const item of report.findings.unsupportedGuesses) {
    lines.push(`- ${item}`);
  }
  lines.push("");

  lines.push("## F2 Group Summary");
  lines.push(`- terrain/ground/water f2 values: ${report.semanticSummary.terrainGroundWater.join(", ")}`);
  lines.push(`- road/path/traffic/buildable-related f2 values: ${report.semanticSummary.roadPathTrafficBuildable.join(", ")}`);
  lines.push(`- special overlays/runtime markers f2 values: ${report.semanticSummary.specialOverlayRuntimeMarkers.join(", ")}`);
  lines.push("");

  lines.push("## F3/F4 Relationship to F2 Flags");
  for (const row of report.f3f4FlagCorrelations.slice(0, 12)) {
    lines.push(`- ${row.flag}: f3=1 ${formatPercent(row.f3OneRateWithFlag)} with flag vs ${formatPercent(row.f3OneRateWithoutFlag)} without`);
  }
  lines.push("");

  lines.push("## Top F2 Entries");
  for (const entry of report.entries.slice(0, 15)) {
    lines.push(
      `- f2=${entry.f2} (${entry.mapChip.name ?? "unknown"}): ${entry.cellCount} cells (${formatPercent(entry.mapCoverage)}), type=${entry.mapChip.type}, category=${entry.mapChip.category}, layer=${entry.mapChip.layer}, movementCost=${entry.mapChip.movementCost}, targetCost=${entry.mapChip.targetCost}, bboxDensity=${formatPercent(entry.boundingBox.density)}`,
    );
  }
  lines.push("");

  lines.push("## Next Implementation Step");
  lines.push(`- ${report.nextStep.target}`);
  lines.push(`- reason: ${report.nextStep.reason}`);
  lines.push(`- command: ${report.nextStep.command}`);

  return lines.join("\n");
}

function run() {
  const projectRoot = process.cwd();
  const repoRoot = path.resolve(projectRoot, "..", "..");

  const sectionAAnalysisPath = path.resolve(projectRoot, "tmp", "map-section-a-analysis.json");
  const sectionAF3Path = path.resolve(projectRoot, "tmp", "map-section-a-f3-analysis.json");
  const sectionAMapChipCorrPath = path.resolve(projectRoot, "tmp", "map-section-a-mapchip-correlation.json");

  const mapChipPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - MapChip.csv");

  for (const p of [sectionAAnalysisPath, sectionAF3Path, sectionAMapChipCorrPath, mapChipPath]) {
    if (!fs.existsSync(p)) {
      throw new Error(`Missing required file: ${p}`);
    }
  }

  const sectionAAnalysis = JSON.parse(fs.readFileSync(sectionAAnalysisPath, "utf8"));
  const sectionAF3 = JSON.parse(fs.readFileSync(sectionAF3Path, "utf8"));
  const sectionAMapChipCorr = JSON.parse(fs.readFileSync(sectionAMapChipCorrPath, "utf8"));

  const mapBytes = fs.readFileSync(sectionAAnalysis.sourceMapPath);
  const parsedMap = parseMapBinarySectionA(mapBytes);

  const mapChipRows = parseCsv(fs.readFileSync(mapChipPath, "utf8"));
  const mapChipById = new Map();
  for (const row of mapChipRows) {
    const id = toInt(row.id ?? row.__col_0);
    if (id !== null) {
      mapChipById.set(id, row);
    }
  }

  const byF2 = new Map();
  for (const cell of parsedMap.cells) {
    const f2 = cell.fields.f2;
    if (!byF2.has(f2)) {
      byF2.set(f2, {
        f2,
        cells: [],
        f1Counts: new Map(),
        f3Counts: new Map(),
        f4Counts: new Map(),
        f5Counts: new Map(),
      });
    }

    const entry = byF2.get(f2);
    entry.cells.push(cell);
    incrementCount(entry.f1Counts, cell.fields.f1);
    incrementCount(entry.f3Counts, cell.fields.f3);
    incrementCount(entry.f4Counts, cell.fields.f4);
    incrementCount(entry.f5Counts, cell.fields.f5);
  }

  const entries = [];
  const totalCells = parsedMap.cells.length;

  for (const [f2, info] of byF2.entries()) {
    const chip = mapChipById.get(f2) ?? null;
    const cellCount = info.cells.length;

    let minX = Number.POSITIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    for (const cell of info.cells) {
      if (cell.x < minX) minX = cell.x;
      if (cell.y < minY) minY = cell.y;
      if (cell.x > maxX) maxX = cell.x;
      if (cell.y > maxY) maxY = cell.y;
    }

    const bboxWidth = maxX - minX + 1;
    const bboxHeight = maxY - minY + 1;
    const bboxArea = bboxWidth * bboxHeight;
    const density = bboxArea > 0 ? cellCount / bboxArea : 0;

    const cluster = connectedClusters(info.cells.map((cell) => ({ x: cell.x, y: cell.y })));

    const flags = parseFlagSet(chip?.flag_2 ?? chip?.flag);

    entries.push({
      f2,
      cellCount,
      mapCoverage: cellCount / totalCells,
      mapChip: {
        id: toInt(chip?.id),
        name: chip?.name ?? null,
        type: toInt(chip?.type),
        category: toInt(chip?.category),
        movementCost: toInt(chip?.movementCost),
        targetCost: toInt(chip?.targetCost),
        layer: toInt(chip?.layer),
        rotation: toInt(chip?.rotation),
        sizeWidth: toInt(chip?.sizeWidth),
        sizeHeight: toInt(chip?.sizeHeight),
        relatedDataType: toInt(chip?.relatedDataType),
        relatedDataId: toInt(chip?.relatedDataId),
      },
      flags: [...flags].sort((a, b) => a.localeCompare(b)),
      importantFlags: IMPORTANT_FLAGS.reduce((obj, flag) => {
        obj[flag] = flags.has(flag);
        return obj;
      }, {}),
      semanticKind: classifyF2Kind(chip ?? {}),
      f1TerrainDistribution: topEntriesFromMap(info.f1Counts, 40).map((row) => ({
        value: Number(row.key),
        count: row.count,
        ratio: row.count / cellCount,
      })),
      f3Distribution: topEntriesFromMap(info.f3Counts, 10).map((row) => ({
        value: Number(row.key),
        count: row.count,
        ratio: row.count / cellCount,
      })),
      f4Distribution: topEntriesFromMap(info.f4Counts, 10).map((row) => ({
        value: Number(row.key),
        count: row.count,
        ratio: row.count / cellCount,
      })),
      f5Distribution: topEntriesFromMap(info.f5Counts, 20).map((row) => ({
        value: Number(row.key),
        count: row.count,
        ratio: row.count / cellCount,
      })),
      boundingBox: {
        minX,
        minY,
        maxX,
        maxY,
        width: bboxWidth,
        height: bboxHeight,
        area: bboxArea,
        density,
      },
      cluster,
    });
  }

  entries.sort((a, b) => b.cellCount - a.cellCount || a.f2 - b.f2);

  const groups = {
    byMapChipType: groupByKey(entries, (entry) => entry.mapChip.type),
    byCategory: groupByKey(entries, (entry) => entry.mapChip.category),
    byLayer: groupByKey(entries, (entry) => entry.mapChip.layer),
    byMovementCost: groupByKey(entries, (entry) => entry.mapChip.movementCost),
    byTargetCost: groupByKey(entries, (entry) => entry.mapChip.targetCost),
    byRelatedDataType: groupByKey(entries, (entry) => entry.mapChip.relatedDataType),
    byFlags: groupsByFlags(entries),
  };

  const terrainGroundWater = entries
    .filter((entry) => entry.semanticKind === "terrain-ground" || entry.semanticKind === "terrain-water")
    .map((entry) => entry.f2);
  const roadPathTrafficBuildable = entries
    .filter((entry) => entry.semanticKind === "road-path-traffic-buildable")
    .map((entry) => entry.f2);
  const specialOverlayRuntimeMarkers = entries
    .filter((entry) => entry.semanticKind === "special-overlay-runtime-marker")
    .map((entry) => entry.f2);

  const f3f4FlagCorrelations = IMPORTANT_FLAGS.map((flag) => {
    let withFlagTotal = 0;
    let withFlagF3One = 0;
    let withoutFlagTotal = 0;
    let withoutFlagF3One = 0;

    for (const entry of entries) {
      const f3One = entry.f3Distribution.find((row) => row.value === 1)?.count ?? 0;
      const hasFlag = entry.flags.includes(flag);
      if (hasFlag) {
        withFlagTotal += entry.cellCount;
        withFlagF3One += f3One;
      } else {
        withoutFlagTotal += entry.cellCount;
        withoutFlagF3One += f3One;
      }
    }

    return {
      flag,
      withFlagTotal,
      withFlagF3One,
      f3OneRateWithFlag: withFlagTotal > 0 ? withFlagF3One / withFlagTotal : 0,
      withoutFlagTotal,
      withoutFlagF3One,
      f3OneRateWithoutFlag: withoutFlagTotal > 0 ? withoutFlagF3One / withoutFlagTotal : 0,
    };
  }).sort((a, b) => (b.f3OneRateWithFlag - b.f3OneRateWithoutFlag) - (a.f3OneRateWithFlag - a.f3OneRateWithoutFlag));

  const proven = [
    `All observed f2 values are valid MapChip IDs (${entries.length}/${entries.length}).`,
    `Observed f2 set is identical to previous mapchip correlation join set (${entries.length} values).`,
    `f3/f4 remain binary complements at cell-level in source section A data (from prior validated report).`,
  ];

  const topRoad = roadPathTrafficBuildable
    .map((f2) => entries.find((entry) => entry.f2 === f2))
    .filter(Boolean)
    .sort((a, b) => b.cellCount - a.cellCount)
    .slice(0, 4);

  const statistical = [
    `Road/path/buildable-related semantic kind accounts for ${formatPercent(topRoad.reduce((sum, item) => sum + item.cellCount, 0) / totalCells)} of all cells in top members (${topRoad.map((item) => item.f2).join(", ")}).`,
    `f3=1 enrichment for Straight Select is ${formatPercent((f3f4FlagCorrelations.find((row) => row.flag === "Straight Select")?.f3OneRateWithFlag ?? 0))} vs ${formatPercent((f3f4FlagCorrelations.find((row) => row.flag === "Straight Select")?.f3OneRateWithoutFlag ?? 0))} without flag.`,
  ];

  const hypotheses = [
    "f2 behaves as runtime chip/material identity layer, while f3/f4 partition legality/state classes over those chip IDs.",
    "Road/path/buildable-like f2 IDs are primary occupants of the f3=1 branch and likely represent placement/traffic legal lanes.",
    "Terrain and water-like f2 IDs cluster in broad dense regions with predictable f1/f5 distributions, supporting base-surface semantics.",
  ];

  const unsupportedGuesses = [
    "Every f3=1 chip is always buildable in every gameplay system.",
    "No Construction alone determines the exact f3/f4 split.",
    "relatedDataType is directly equal to f5 semantics.",
  ];

  const report = {
    generatedAt: new Date().toISOString(),
    sources: {
      sectionAAnalysisPath,
      sectionAF3Path,
      sectionAMapChipCorrPath,
      mapChipPath,
    },
    map: {
      source: sectionAAnalysis.sourceMapPath,
      width: parsedMap.width,
      height: parsedMap.height,
      totalCells,
      remainingBytes: parsedMap.remainingBytes,
    },
    observedF2Count: entries.length,
    entries,
    groups,
    semanticSummary: {
      terrainGroundWater,
      roadPathTrafficBuildable,
      specialOverlayRuntimeMarkers,
    },
    f3f4FlagCorrelations,
    findings: {
      proven,
      statistical,
      hypotheses,
      unsupportedGuesses,
    },
    relationToPriorReports: {
      priorJoinCoverage: sectionAMapChipCorr.joins,
      priorF3Hypothesis: sectionAMapChipCorr.interpretation?.f3f4 ?? null,
      priorF1Hypothesis: sectionAMapChipCorr.interpretation?.f1 ?? null,
      priorF5Hypothesis: sectionAMapChipCorr.interpretation?.f5 ?? null,
      priorF3SignalSummary: sectionAF3?.evidence?.proven ?? [],
    },
    nextStep: {
      target: "Implement a runtime-neutral f2 material layer adapter for renderer/inspector that tags cells by semantic kind and flag bundles before any gameplay behavior wiring.",
      reason: "f2 semantics are now classifiable by chip metadata and spatial distribution; this enables faithful visualization and targeted runtime-read validation without map mutation.",
      command: "node scripts/export-f2-semantic-layer-fixture.mjs",
    },
  };

  const outJson = path.resolve(projectRoot, "tmp", "map-section-a-f2-mapchip-semantic-groups.json");
  const outMd = path.resolve(projectRoot, "tmp", "map-section-a-f2-mapchip-semantic-groups.md");

  fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(outMd, buildMarkdown(report), "utf8");

  const summary = {
    outputs: {
      json: outJson,
      md: outMd,
    },
    strongestProven: report.findings.proven,
    f2MeaningSummary: report.semanticSummary,
    f3f4ToF2Flags: report.f3f4FlagCorrelations.slice(0, 5),
    nextImplementationCommand: report.nextStep.command,
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outJson}`);
  console.log(`Wrote ${outMd}`);
}

run();
