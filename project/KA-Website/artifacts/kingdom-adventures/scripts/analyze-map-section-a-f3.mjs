import fs from "node:fs";
import path from "node:path";

const FIELD_KEYS = ["f0", "f1", "f2", "f3", "f4", "f5"];
const CARDINAL_DIRS = [
  { key: "N", dx: 0, dy: -1 },
  { key: "E", dx: 1, dy: 0 },
  { key: "S", dx: 0, dy: 1 },
  { key: "W", dx: -1, dy: 0 },
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

function tupleKey(fields) {
  return FIELD_KEYS.map((field) => fields[field]).join("|");
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

function toNumber(value) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  const parsed = Number.parseInt(String(value), 10);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return parsed;
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

function getCellAt(parsed, x, y) {
  if (x < 0 || x >= parsed.width || y < 0 || y >= parsed.height) {
    return null;
  }
  return parsed.cells[y * parsed.width + x] ?? null;
}

function computeBBox(cells) {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const cell of cells) {
    minX = Math.min(minX, cell.x);
    minY = Math.min(minY, cell.y);
    maxX = Math.max(maxX, cell.x);
    maxY = Math.max(maxY, cell.y);
  }

  const width = maxX - minX + 1;
  const height = maxY - minY + 1;
  const area = width * height;
  const density = area > 0 ? cells.length / area : 0;

  return { minX, minY, maxX, maxY, area, density };
}

function analyzeF3Value(parsed, value, tupleCounts) {
  const matchingCells = parsed.cells.filter((cell) => cell.fields.f3 === value);
  const count = matchingCells.length;
  const percentage = parsed.cells.length > 0 ? count / parsed.cells.length : 0;
  const bbox = computeBBox(matchingCells);

  const neighborValueCounts = new Map();
  const directionNeighborCounts = {
    N: new Map(),
    E: new Map(),
    S: new Map(),
    W: new Map(),
  };
  const neighborPatternCounts = new Map();

  const tupleSubset = new Map();
  const f1Counts = new Map();
  const f2Counts = new Map();
  const f4Counts = new Map();
  const f5Counts = new Map();

  for (const cell of matchingCells) {
    const tuple = tupleKey(cell.fields);
    tupleSubset.set(tuple, (tupleSubset.get(tuple) ?? 0) + 1);

    f1Counts.set(cell.fields.f1, (f1Counts.get(cell.fields.f1) ?? 0) + 1);
    f2Counts.set(cell.fields.f2, (f2Counts.get(cell.fields.f2) ?? 0) + 1);
    f4Counts.set(cell.fields.f4, (f4Counts.get(cell.fields.f4) ?? 0) + 1);
    f5Counts.set(cell.fields.f5, (f5Counts.get(cell.fields.f5) ?? 0) + 1);

    const patternParts = [];
    for (const dir of CARDINAL_DIRS) {
      const neighbor = getCellAt(parsed, cell.x + dir.dx, cell.y + dir.dy);
      const neighborValue = neighbor ? neighbor.fields.f3 : -1;
      patternParts.push(`${dir.key}:${neighborValue}`);
      neighborValueCounts.set(neighborValue, (neighborValueCounts.get(neighborValue) ?? 0) + 1);
      directionNeighborCounts[dir.key].set(
        neighborValue,
        (directionNeighborCounts[dir.key].get(neighborValue) ?? 0) + 1,
      );
    }

    const patternKey = patternParts.join("|");
    neighborPatternCounts.set(patternKey, (neighborPatternCounts.get(patternKey) ?? 0) + 1);
  }

  const commonTuples = [...tupleSubset.entries()]
    .map(([tuple, localCount]) => ({
      tuple,
      localCount,
      globalCount: tupleCounts.get(tuple) ?? localCount,
      fields: parseTuple(tuple),
    }))
    .sort((left, right) => {
      if (right.localCount !== left.localCount) {
        return right.localCount - left.localCount;
      }
      return left.tuple.localeCompare(right.tuple);
    })
    .slice(0, 20);

  return {
    value,
    count,
    percentage,
    boundingBox: bbox,
    density: bbox.density,
    siblingFieldDistributions: {
      f1: topEntriesFromMap(f1Counts, 20).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f2: topEntriesFromMap(f2Counts, 20).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f4: topEntriesFromMap(f4Counts, 20).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f5: topEntriesFromMap(f5Counts, 20).map((entry) => ({ value: Number(entry.key), count: entry.count })),
    },
    neighboringValuePatterns: {
      overallNeighborValueCounts: topEntriesFromMap(neighborValueCounts, 10).map((entry) => ({
        neighborValue: Number(entry.key),
        count: entry.count,
      })),
      byDirection: {
        N: topEntriesFromMap(directionNeighborCounts.N, 10).map((entry) => ({ neighborValue: Number(entry.key), count: entry.count })),
        E: topEntriesFromMap(directionNeighborCounts.E, 10).map((entry) => ({ neighborValue: Number(entry.key), count: entry.count })),
        S: topEntriesFromMap(directionNeighborCounts.S, 10).map((entry) => ({ neighborValue: Number(entry.key), count: entry.count })),
        W: topEntriesFromMap(directionNeighborCounts.W, 10).map((entry) => ({ neighborValue: Number(entry.key), count: entry.count })),
      },
      topNeighborPatterns: topEntriesFromMap(neighborPatternCounts, 20).map((entry) => ({ pattern: String(entry.key), count: entry.count })),
    },
    commonTuples,
  };
}

function parseTuple(tuple) {
  const numbers = tuple.split("|").map((chunk) => Number.parseInt(chunk, 10));
  return {
    f0: numbers[0],
    f1: numbers[1],
    f2: numbers[2],
    f3: numbers[3],
    f4: numbers[4],
    f5: numbers[5],
  };
}

function buildCorrelations(parsed, f3Analyses, mapChipById, facilityById, terrainById) {
  const perValue = [];

  for (const item of f3Analyses) {
    const chipCounts = new Map();
    const terrainCounts = new Map();
    const facilityCounts = new Map();
    let unknownChipRows = 0;

    for (const cell of parsed.cells) {
      if (cell.fields.f3 !== item.value) {
        continue;
      }

      const chipId = cell.fields.f2;
      chipCounts.set(chipId, (chipCounts.get(chipId) ?? 0) + 1);

      const mapChip = mapChipById.get(chipId);
      if (!mapChip) {
        unknownChipRows += 1;
        continue;
      }

      const relatedDataType = toNumber(mapChip.relatedDataType) ?? -1;
      const relatedDataId = toNumber(mapChip.relatedDataId) ?? -1;

      if (relatedDataType === 2) {
        const terrain = terrainById.get(relatedDataId);
        const terrainName = terrain?.name ?? `terrainId:${relatedDataId}`;
        terrainCounts.set(terrainName, (terrainCounts.get(terrainName) ?? 0) + 1);
      }

      if (relatedDataType === 1) {
        const facility = facilityById.get(relatedDataId);
        const facilityLabel = facility?.explain || facility?.id || `facilityId:${relatedDataId}`;
        facilityCounts.set(facilityLabel, (facilityCounts.get(facilityLabel) ?? 0) + 1);
      }
    }

    const topMapChips = topEntriesFromMap(chipCounts, 20).map((entry) => {
      const chipId = Number(entry.key);
      const chip = mapChipById.get(chipId);
      return {
        chipId,
        count: entry.count,
        chipName: chip?.name ?? null,
        type: toNumber(chip?.type),
        category: toNumber(chip?.category),
        relatedDataType: toNumber(chip?.relatedDataType),
        relatedDataId: toNumber(chip?.relatedDataId),
      };
    });

    perValue.push({
      f3Value: item.value,
      unknownMapChipRows: unknownChipRows,
      topMapChips,
      topTerrainNames: topEntriesFromMap(terrainCounts, 20).map((entry) => ({ name: String(entry.key), count: entry.count })),
      topFacilityNames: topEntriesFromMap(facilityCounts, 20).map((entry) => ({ name: String(entry.key), count: entry.count })),
    });
  }

  return perValue;
}

function parseReferenceTables(repoRoot) {
  const mapChipPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - MapChip.csv");
  const facilityPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - Facility.csv");
  const terrainPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - Terrain.csv");

  const mapChipRows = parseCsv(fs.readFileSync(mapChipPath, "utf8"));
  const facilityRows = parseCsv(fs.readFileSync(facilityPath, "utf8"));
  const terrainRows = parseCsv(fs.readFileSync(terrainPath, "utf8"));

  const mapChipById = new Map();
  for (const row of mapChipRows) {
    const id = toNumber(row.id ?? row.__col_0);
    if (id !== null) {
      mapChipById.set(id, row);
    }
  }

  const facilityById = new Map();
  for (const row of facilityRows) {
    const id = toNumber(row.id ?? row.__col_0);
    if (id !== null) {
      facilityById.set(id, row);
    }
  }

  const terrainById = new Map();
  for (const row of terrainRows) {
    const id = toNumber(row.id ?? row.__col_0 ?? row.__col_1);
    if (id !== null) {
      terrainById.set(id, row);
    }
  }

  return {
    mapChipPath,
    facilityPath,
    terrainPath,
    mapChipById,
    facilityById,
    terrainById,
  };
}

function deriveEvidence(report, f3Analyses, correlations) {
  const proofs = [];
  const unknowns = [];

  const f3Values = f3Analyses.map((item) => item.value);
  if (f3Values.length === 2 && f3Values.includes(0) && f3Values.includes(1)) {
    proofs.push("f3 is binary in map_160_160 (only values 0 and 1)");
  }

  const f3f4Same = parsedF3F4SameCount(report.sourceMapPath);
  if (f3f4Same.inverseRatio === 1) {
    proofs.push("f3 and f4 are exact complements across all cells (f3 + f4 = 1)");
  }

  const f3One = f3Analyses.find((item) => item.value === 1);
  if (f3One && f3One.siblingFieldDistributions.f1.length > 0) {
    const first = f3One.siblingFieldDistributions.f1[0];
    if (first.value === 0 && first.count === f3One.count) {
      proofs.push("f3=1 always co-occurs with f1=0 in this map");
    }
  }

  const terrainHits = correlations.reduce((sum, item) => {
    return sum + item.topTerrainNames.reduce((inner, terrain) => inner + terrain.count, 0);
  }, 0);

  if (terrainHits === 0) {
    unknowns.push("No direct Terrain.csv correlation is provable from f3 alone via MapChip relatedDataType linkage");
  }

  unknowns.push("f3 semantic label (e.g. occupied-mask, territory-mask, pathability-mask) is not provable from one map without runtime write/read traces");

  const nextTest = {
    title: "Section A f3 mutation test on controlled cell patches",
    reason:
      "Binary/complement behavior is proven, but semantic meaning is unresolved; controlled deltas can isolate whether f3 gates territory/placement/pathing overlays.",
    steps: [
      "Create a synthetic map variant by toggling f3 (and optionally f4 complement) inside a small rectangle while keeping f2/f1 unchanged.",
      "Load through existing parser and runtime-test visualization and compare diff behavior against baseline clusters.",
      "In IL2CPP placement path traces, locate reads that branch directly on the section A field mapped to f3 index.",
      "Confirm if toggled cells affect placement validation, road/path rendering, or territory expansion checks.",
    ],
  };

  return {
    proven: proofs,
    unknowns,
    nextDecodingTest: nextTest,
  };
}

function parsedF3F4SameCount(mapPath) {
  const bytes = fs.readFileSync(mapPath);
  const parsed = parseMapBinarySectionA(bytes);
  let same = 0;
  let inverse = 0;

  for (const cell of parsed.cells) {
    if (cell.fields.f3 === cell.fields.f4) {
      same += 1;
    }
    if (cell.fields.f3 + cell.fields.f4 === 1) {
      inverse += 1;
    }
  }

  return {
    same,
    inverse,
    inverseRatio: parsed.cells.length > 0 ? inverse / parsed.cells.length : 0,
  };
}

function buildMarkdown(report) {
  const lines = [];

  lines.push("# Section A f3 Analysis");
  lines.push("");
  lines.push(`- generated at: ${report.generatedAt}`);
  lines.push(`- source map: ${report.sourceMapPath}`);
  lines.push(`- source section-A report: ${report.sourceAnalysisPath}`);
  lines.push(`- map size: ${report.width} x ${report.height} (${report.totalCells} cells)`);
  lines.push(`- remaining bytes after section A: ${report.remainingBytes}`);
  lines.push("");
  lines.push("## f3 Values");

  for (const valueInfo of report.f3Values) {
    lines.push(`### f3=${valueInfo.value}`);
    lines.push(`- count: ${valueInfo.count}`);
    lines.push(`- percentage: ${(valueInfo.percentage * 100).toFixed(2)}%`);
    lines.push(
      `- bbox: (${valueInfo.boundingBox.minX},${valueInfo.boundingBox.minY})-(${valueInfo.boundingBox.maxX},${valueInfo.boundingBox.maxY})`,
    );
    lines.push(`- density in bbox: ${(valueInfo.density * 100).toFixed(2)}%`);
    lines.push(
      `- neighbor overall: ${valueInfo.neighboringValuePatterns.overallNeighborValueCounts
        .map((item) => `${item.neighborValue}:${item.count}`)
        .join(", ")}`,
    );
    lines.push(
      `- top tuples: ${valueInfo.commonTuples
        .slice(0, 5)
        .map((item) => `${item.tuple} (${item.localCount})`)
        .join(" | ")}`,
    );
    lines.push("");
  }

  lines.push("## Correlations");
  for (const corr of report.correlations.perValue) {
    lines.push(`### f3=${corr.f3Value}`);
    lines.push(
      `- top map chips: ${corr.topMapChips
        .slice(0, 8)
        .map((chip) => `${chip.chipId}:${chip.chipName ?? "unknown"} (${chip.count})`)
        .join(" | ")}`,
    );
    lines.push(
      `- top terrain names: ${corr.topTerrainNames.length > 0 ? corr.topTerrainNames.map((t) => `${t.name}:${t.count}`).join(" | ") : "none"}`,
    );
    lines.push(
      `- top facility names: ${corr.topFacilityNames.length > 0 ? corr.topFacilityNames.map((f) => `${f.name}:${f.count}`).join(" | ") : "none"}`,
    );
    lines.push(`- unknown mapchip rows: ${corr.unknownMapChipRows}`);
    lines.push("");
  }

  lines.push("## Proven");
  for (const item of report.evidence.proven) {
    lines.push(`- ${item}`);
  }

  lines.push("");
  lines.push("## Unknown");
  for (const item of report.evidence.unknowns) {
    lines.push(`- ${item}`);
  }

  lines.push("");
  lines.push("## Recommended Next Decoding Test");
  lines.push(`- ${report.evidence.nextDecodingTest.title}`);
  lines.push(`- reason: ${report.evidence.nextDecodingTest.reason}`);
  lines.push("- steps:");
  for (const step of report.evidence.nextDecodingTest.steps) {
    lines.push(`  - ${step}`);
  }

  return lines.join("\n");
}

function run() {
  const projectRoot = process.cwd();
  const repoRoot = path.resolve(projectRoot, "..", "..");

  const sourceAnalysisPath = path.resolve(projectRoot, "tmp", "map-section-a-analysis.json");
  const outJsonPath = path.resolve(projectRoot, "tmp", "map-section-a-f3-analysis.json");
  const outMdPath = path.resolve(projectRoot, "tmp", "map-section-a-f3-analysis.md");

  if (!fs.existsSync(sourceAnalysisPath)) {
    throw new Error(`Missing analysis file: ${sourceAnalysisPath}. Run npm run analyze:map-section-a first.`);
  }

  const sectionAReport = JSON.parse(fs.readFileSync(sourceAnalysisPath, "utf8"));
  const sourceMapPath = sectionAReport.sourceMapPath;

  const bytes = fs.readFileSync(sourceMapPath);
  const parsed = parseMapBinarySectionA(bytes);

  const tupleCounts = new Map();
  for (const cell of parsed.cells) {
    const key = tupleKey(cell.fields);
    tupleCounts.set(key, (tupleCounts.get(key) ?? 0) + 1);
  }

  const uniqueValues = [...new Set(parsed.cells.map((cell) => cell.fields.f3))].sort((a, b) => a - b);
  const f3Values = uniqueValues.map((value) => analyzeF3Value(parsed, value, tupleCounts));

  const refs = parseReferenceTables(repoRoot);
  const correlations = {
    mapChipSource: refs.mapChipPath,
    facilitySource: refs.facilityPath,
    terrainSource: refs.terrainPath,
    perValue: buildCorrelations(parsed, f3Values, refs.mapChipById, refs.facilityById, refs.terrainById),
  };

  const evidence = deriveEvidence({ sourceMapPath }, f3Values, correlations.perValue);

  const report = {
    generatedAt: new Date().toISOString(),
    sourceAnalysisPath,
    sourceMapPath,
    width: parsed.width,
    height: parsed.height,
    totalCells: parsed.cells.length,
    remainingBytes: parsed.remainingBytes,
    f3Values,
    correlations,
    evidence,
  };

  fs.writeFileSync(outJsonPath, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(outMdPath, buildMarkdown(report), "utf8");

  const summary = {
    uniqueF3Values: uniqueValues,
    f3Counts: f3Values.map((item) => ({ value: item.value, count: item.count, percentage: item.percentage })),
    proven: report.evidence.proven,
    unknowns: report.evidence.unknowns,
    nextDecodingTest: report.evidence.nextDecodingTest,
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outJsonPath}`);
  console.log(`Wrote ${outMdPath}`);
}

run();
