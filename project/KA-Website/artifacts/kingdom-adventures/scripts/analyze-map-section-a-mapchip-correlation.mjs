import fs from "node:fs";
import path from "node:path";

const FIELD_KEYS = ["f0", "f1", "f2", "f3", "f4", "f5"];

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

function parseFullTerrainGrid(text) {
  const rows = text
    .trim()
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => line.split(",").map((value) => Number.parseInt(value.trim(), 10)));

  const height = rows.length;
  const width = rows[0]?.length ?? 0;

  for (const row of rows) {
    if (row.length !== width) {
      throw new Error("Map (full, terrain).csv has inconsistent row width");
    }
  }

  return { width, height, rows };
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

function fieldValueCounts(cells, field) {
  const counts = new Map();
  for (const cell of cells) {
    const value = cell.fields[field];
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return counts;
}

function collectPairCounts(cells, fieldA, fieldB) {
  const pair = new Map();
  for (const cell of cells) {
    const a = cell.fields[fieldA];
    const b = cell.fields[fieldB];
    const key = `${a}|${b}`;
    pair.set(key, (pair.get(key) ?? 0) + 1);
  }
  return pair;
}

function computePurityFromPairs(cells, sourceField, targetField) {
  const groups = new Map();
  for (const cell of cells) {
    const source = cell.fields[sourceField];
    const target = cell.fields[targetField];
    if (!groups.has(source)) {
      groups.set(source, new Map());
    }
    const inner = groups.get(source);
    inner.set(target, (inner.get(target) ?? 0) + 1);
  }

  let total = 0;
  let dominant = 0;
  const mapping = [];

  for (const [source, inner] of groups.entries()) {
    const entries = [...inner.entries()].sort((a, b) => b[1] - a[1]);
    const sourceTotal = entries.reduce((sum, entry) => sum + entry[1], 0);
    const [topTarget, topCount] = entries[0];
    total += sourceTotal;
    dominant += topCount;
    mapping.push({
      sourceValue: source,
      dominantTargetValue: topTarget,
      dominantCount: topCount,
      sourceTotal,
      dominantRatio: sourceTotal > 0 ? topCount / sourceTotal : 0,
      alternatives: entries.slice(1, 4).map((entry) => ({ value: entry[0], count: entry[1] })),
    });
  }

  mapping.sort((a, b) => a.sourceValue - b.sourceValue);

  return {
    globalPurity: total > 0 ? dominant / total : 0,
    bySourceValue: mapping,
  };
}

function mapCellMetadata(cell, mapChipById, terrainById) {
  const chip = mapChipById.get(cell.fields.f2) ?? null;
  if (!chip) {
    return {
      chip: null,
      terrainRow: null,
      relatedDataType: null,
      relatedDataId: null,
      flags: new Set(),
    };
  }

  const relatedDataType = toInt(chip.relatedDataType);
  const relatedDataId = toInt(chip.relatedDataId);
  const terrainRow = relatedDataType === 2 ? (terrainById.get(relatedDataId) ?? null) : null;
  const flags = parseFlagSet(chip.flag_2 ?? chip.flag);

  return {
    chip,
    terrainRow,
    relatedDataType,
    relatedDataId,
    flags,
  };
}

function analyzeF3FlagCorrelations(rows, flagNames) {
  const total = rows.length;
  const baseF3One = rows.reduce((sum, row) => sum + (row.f3 === 1 ? 1 : 0), 0);
  const baseRate = total > 0 ? baseF3One / total : 0;

  const out = [];

  for (const flagName of flagNames) {
    let withFlag = 0;
    let withFlagF3One = 0;
    let withoutFlag = 0;
    let withoutFlagF3One = 0;

    for (const row of rows) {
      const hasFlag = row.flags.has(flagName);
      if (hasFlag) {
        withFlag += 1;
        if (row.f3 === 1) {
          withFlagF3One += 1;
        }
      } else {
        withoutFlag += 1;
        if (row.f3 === 1) {
          withoutFlagF3One += 1;
        }
      }
    }

    const withRate = withFlag > 0 ? withFlagF3One / withFlag : null;
    const withoutRate = withoutFlag > 0 ? withoutFlagF3One / withoutFlag : null;

    out.push({
      flag: flagName,
      withFlag,
      withFlagF3One,
      withFlagRate: withRate,
      withoutFlag,
      withoutFlagF3One,
      withoutFlagRate: withoutRate,
      baselineRate: baseRate,
      absoluteRateDelta:
        withRate === null || withoutRate === null ? null : Math.abs(withRate - withoutRate),
      liftVsBaseline: withRate === null || baseRate === 0 ? null : withRate / baseRate,
    });
  }

  out.sort((a, b) => {
    const left = a.absoluteRateDelta ?? -1;
    const right = b.absoluteRateDelta ?? -1;
    return right - left;
  });

  return out;
}

function buildMarkdown(report) {
  const lines = [];
  lines.push("# Section A vs MapChip Correlation");
  lines.push("");
  lines.push(`- generatedAt: ${report.generatedAt}`);
  lines.push(`- source map: ${report.sourceMapPath}`);
  lines.push(`- map size: ${report.map.width}x${report.map.height} (${report.map.totalCells} cells)`);
  lines.push(`- section A remaining bytes: ${report.map.remainingBytes}`);
  lines.push("");

  lines.push("## Proven Correlations");
  for (const item of report.findings.proven) {
    lines.push(`- ${item}`);
  }
  lines.push("");

  lines.push("## Strong Statistical Correlations");
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

  lines.push("## Key Field Interpretations");
  lines.push(`- f3/f4 likely control: ${report.interpretation.f3f4}`);
  lines.push(`- f1 likely represents: ${report.interpretation.f1}`);
  lines.push(`- f5 likely represents: ${report.interpretation.f5}`);
  lines.push("");

  lines.push("## Top f3 Flag Correlations");
  for (const row of report.correlations.f3FlagTop.slice(0, 12)) {
    lines.push(
      `- ${row.flag}: P(f3=1|flag)=${formatRate(row.withFlagRate)}, P(f3=1|no-flag)=${formatRate(row.withoutFlagRate)}, delta=${formatRate(row.absoluteRateDelta)}`,
    );
  }
  lines.push("");

  lines.push("## f1/f5 Mapping Purity");
  lines.push(`- f1 -> f5 global purity: ${formatRate(report.correlations.f1ToF5.globalPurity)}`);
  lines.push(`- f5 -> f1 global purity: ${formatRate(report.correlations.f5ToF1.globalPurity)}`);
  lines.push("");

  lines.push("## Next Recommended Decoding Target");
  lines.push(`- ${report.nextStep.target}`);
  lines.push(`- reason: ${report.nextStep.reason}`);
  lines.push(`- command: ${report.nextStep.command}`);

  return lines.join("\n");
}

function formatRate(value) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return `${(value * 100).toFixed(2)}%`;
}

function run() {
  const projectRoot = process.cwd();
  const repoRoot = path.resolve(projectRoot, "..", "..");

  const sectionAAnalysisPath = path.resolve(projectRoot, "tmp", "map-section-a-analysis.json");
  const sectionAF3Path = path.resolve(projectRoot, "tmp", "map-section-a-f3-analysis.json");
  const sourceInventoryPath = path.resolve(projectRoot, "tmp", "map-data-source-inventory.json");

  const mapChipPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - MapChip.csv");
  const terrainPath = path.resolve(repoRoot, "data", "sheet-research", "raw-copies", "KA GameData - Terrain.csv");
  const mapPath = path.resolve(repoRoot, "data", "Sheet csv", "KA GameData - Map.csv");
  const fullTerrainPath = path.resolve(repoRoot, "data", "Sheet csv", "KA GameData - Map (full, terrain).csv");

  for (const p of [sectionAAnalysisPath, sectionAF3Path, mapChipPath, terrainPath, mapPath, fullTerrainPath]) {
    if (!fs.existsSync(p)) {
      throw new Error(`Missing required file: ${p}`);
    }
  }

  const sectionAAnalysis = JSON.parse(fs.readFileSync(sectionAAnalysisPath, "utf8"));
  const sectionAF3 = JSON.parse(fs.readFileSync(sectionAF3Path, "utf8"));
  const sourceInventory = fs.existsSync(sourceInventoryPath)
    ? JSON.parse(fs.readFileSync(sourceInventoryPath, "utf8"))
    : null;

  const mapBytes = fs.readFileSync(sectionAAnalysis.sourceMapPath);
  const parsedMap = parseMapBinarySectionA(mapBytes);

  const mapChipRows = parseCsv(fs.readFileSync(mapChipPath, "utf8"));
  const terrainRows = parseCsv(fs.readFileSync(terrainPath, "utf8"));
  const mapCsvLines = fs.readFileSync(mapPath, "utf8").split(/\r?\n/).filter((line) => line.trim().length > 0);
  const fullTerrain = parseFullTerrainGrid(fs.readFileSync(fullTerrainPath, "utf8"));

  const mapChipById = new Map();
  for (const row of mapChipRows) {
    const id = toInt(row.id ?? row.__col_0);
    if (id !== null) {
      mapChipById.set(id, row);
    }
  }

  const terrainById = new Map();
  for (const row of terrainRows) {
    const id = toInt(row.id ?? row.__col_0 ?? row.__col_1);
    if (id !== null) {
      terrainById.set(id, row);
    }
  }

  const metadataRows = [];
  let mapChipJoinHits = 0;
  let mapChipJoinMiss = 0;
  let fullTerrainMatch = 0;

  const f2Counts = fieldValueCounts(parsedMap.cells, "f2");
  const f1Counts = fieldValueCounts(parsedMap.cells, "f1");
  const f5Counts = fieldValueCounts(parsedMap.cells, "f5");

  const observedFlags = new Set();
  const movementCostByF3 = new Map();
  const targetCostByF3 = new Map();
  const layerByF3 = new Map();
  const rotationByF3 = new Map();
  const speedByF3 = new Map();

  for (const cell of parsedMap.cells) {
    const gridTerrain = fullTerrain.rows[cell.y]?.[cell.x];
    if (gridTerrain === cell.fields.f1) {
      fullTerrainMatch += 1;
    }

    const meta = mapCellMetadata(cell, mapChipById, terrainById);
    if (meta.chip) {
      mapChipJoinHits += 1;
    } else {
      mapChipJoinMiss += 1;
    }

    const movementCost = toInt(meta.chip?.movementCost);
    const targetCost = toInt(meta.chip?.targetCost);
    const layer = toInt(meta.chip?.layer);
    const rotation = toInt(meta.chip?.rotation);
    const moveSpeedRate = toInt(meta.chip?.moveSpeedRate);

    for (const flag of meta.flags) {
      observedFlags.add(flag);
    }

    metadataRows.push({
      f0: cell.fields.f0,
      f1: cell.fields.f1,
      f2: cell.fields.f2,
      f3: cell.fields.f3,
      f4: cell.fields.f4,
      f5: cell.fields.f5,
      relatedDataType: meta.relatedDataType,
      relatedDataId: meta.relatedDataId,
      movementCost,
      targetCost,
      layer,
      rotation,
      moveSpeedRate,
      flags: meta.flags,
      terrainCategory: toInt(meta.terrainRow?.category),
      terrainNatureGroupId: toInt(meta.terrainRow?.natureGroupId),
      chipType: toInt(meta.chip?.type),
      chipCategory: toInt(meta.chip?.category),
      chipName: meta.chip?.name ?? null,
    });

    if (!movementCostByF3.has(cell.fields.f3)) movementCostByF3.set(cell.fields.f3, []);
    if (!targetCostByF3.has(cell.fields.f3)) targetCostByF3.set(cell.fields.f3, []);
    if (!layerByF3.has(cell.fields.f3)) layerByF3.set(cell.fields.f3, []);
    if (!rotationByF3.has(cell.fields.f3)) rotationByF3.set(cell.fields.f3, []);
    if (!speedByF3.has(cell.fields.f3)) speedByF3.set(cell.fields.f3, []);

    if (movementCost !== null) movementCostByF3.get(cell.fields.f3).push(movementCost);
    if (targetCost !== null) targetCostByF3.get(cell.fields.f3).push(targetCost);
    if (layer !== null) layerByF3.get(cell.fields.f3).push(layer);
    if (rotation !== null) rotationByF3.get(cell.fields.f3).push(rotation);
    if (moveSpeedRate !== null) speedByF3.get(cell.fields.f3).push(moveSpeedRate);
  }

  const f3FlagCorr = analyzeF3FlagCorrelations(metadataRows, [...observedFlags]);

  const f1ToF5 = computePurityFromPairs(parsedMap.cells, "f1", "f5");
  const f5ToF1 = computePurityFromPairs(parsedMap.cells, "f5", "f1");

  const f3ComplementExact =
    parsedMap.cells.reduce((sum, cell) => sum + (cell.fields.f3 + cell.fields.f4 === 1 ? 1 : 0), 0) ===
    parsedMap.cells.length;

  const f2CoverageValues = {
    uniqueF2Count: f2Counts.size,
    uniqueF2PresentInMapChip: [...f2Counts.keys()].filter((id) => mapChipById.has(id)).length,
  };

  const topF3OneChips = topEntriesFromMap(
    metadataRows
      .filter((row) => row.f3 === 1)
      .reduce((map, row) => {
        map.set(row.f2, (map.get(row.f2) ?? 0) + 1);
        return map;
      }, new Map()),
    12,
  ).map((entry) => ({
    chipId: Number(entry.key),
    count: entry.count,
    chipName: mapChipById.get(Number(entry.key))?.name ?? null,
  }));

  const topF3ZeroChips = topEntriesFromMap(
    metadataRows
      .filter((row) => row.f3 === 0)
      .reduce((map, row) => {
        map.set(row.f2, (map.get(row.f2) ?? 0) + 1);
        return map;
      }, new Map()),
    12,
  ).map((entry) => ({
    chipId: Number(entry.key),
    count: entry.count,
    chipName: mapChipById.get(Number(entry.key))?.name ?? null,
  }));

  const proven = [];
  const statistical = [];
  const hypotheses = [];
  const unsupportedGuesses = [];

  if (f2CoverageValues.uniqueF2Count === f2CoverageValues.uniqueF2PresentInMapChip) {
    proven.push(
      `All observed f2 values map to valid MapChip ids (${f2CoverageValues.uniqueF2PresentInMapChip}/${f2CoverageValues.uniqueF2Count}).`,
    );
  }

  if (fullTerrainMatch === parsedMap.cells.length) {
    proven.push("f1 matches KA GameData Map (full, terrain) per-cell exactly (25600/25600)." );
  }

  if (f3ComplementExact) {
    proven.push("f3/f4 are exact complements across the map (f3 + f4 = 1 for all cells).");
  }

  statistical.push(
    `f1 -> f5 mapping purity is ${formatRate(f1ToF5.globalPurity)} and f5 -> f1 purity is ${formatRate(f5ToF1.globalPurity)}.`,
  );

  const topFlag = f3FlagCorr.find((entry) => entry.withFlag >= 200 && entry.withoutFlag >= 200 && entry.absoluteRateDelta !== null);
  if (topFlag) {
    statistical.push(
      `${topFlag.flag} shows strong f3 association: P(f3=1|flag)=${formatRate(topFlag.withFlagRate)} vs P(f3=1|no-flag)=${formatRate(topFlag.withoutFlagRate)}.`,
    );
  }

  const f3RoadShare = topF3OneChips
    .filter((chip) => chip.chipName && (chip.chipName.includes("Road") || chip.chipName.includes("Land (L)") || chip.chipName.includes("Land (XL)") || chip.chipName.includes("Gravel")))
    .reduce((sum, chip) => sum + chip.count, 0);
  const f3OneCount = metadataRows.filter((row) => row.f3 === 1).length;

  if (f3OneCount > 0) {
    const roadLikeShare = f3RoadShare / f3OneCount;
    hypotheses.push(
      `f3=1 is dominated by road/land-plot style MapChip entries (${formatRate(roadLikeShare)} of f3=1 cells), suggesting a placement/traffic/legality mask role rather than raw biome id.`,
    );
  }

  hypotheses.push(
    "f1 most likely represents surface terrain class id (directly aligned to Map (full, terrain) grid, and consistent with prior world-map usage).",
  );

  hypotheses.push(
    "f5 most likely represents higher-level terrain/zone grouping keyed to f1 regions (high bidirectional purity but not 1:1 identity).",
  );

  unsupportedGuesses.push("f3 is definitively No Construction only (not proven without runtime behavior checks)." );
  unsupportedGuesses.push("f4 alone is a direct occupancy bit for all gameplay systems (not proven from static data)." );
  unsupportedGuesses.push("f5 is exactly biome id (high correlation exists, exact semantics still unproven)." );

  const interpretation = {
    f3f4:
      "Most likely binary legality/state partition masks tied to placement/traffic-like chip classes (roads/land plots) and complementary split behavior; exact gameplay gate (construction/pathing/territory) remains unproven without runtime read checks.",
    f1:
      "Surface terrain tile id (strongly supported by exact per-cell equality with KA GameData Map (full, terrain)).",
    f5:
      "Terrain-region grouping/chunk classification correlated with f1 terrain classes, not a raw chip id field.",
  };

  const nextStep = {
    target: "Create an f2 -> MapChip semantic classifier report grouped by flags/movement/layer and compare against placement-validation runtime probes (no map mutation).",
    reason:
      "Data now strongly anchors f2 to MapChip and f1 to terrain; the biggest unresolved value is proving which runtime checks consume f3/f4 legality splits.",
    command: "node scripts/analyze-mapchip-f2-semantic-groups.mjs",
  };

  const report = {
    generatedAt: new Date().toISOString(),
    sourceMapPath: sectionAAnalysis.sourceMapPath,
    sources: {
      sectionAAnalysisPath,
      sectionAF3Path,
      sourceInventoryPath: sourceInventoryPath,
      mapChipPath,
      mapPath,
      fullTerrainPath,
      terrainPath,
      sourceInventoryUsed: sourceInventory !== null,
    },
    map: {
      width: parsedMap.width,
      height: parsedMap.height,
      totalCells: parsedMap.cells.length,
      remainingBytes: parsedMap.remainingBytes,
    },
    csvSummary: {
      mapChipRows: mapChipRows.length,
      mapCsvNonEmptyLines: mapCsvLines.length,
      fullTerrainDimensions: { width: fullTerrain.width, height: fullTerrain.height },
      terrainRows: terrainRows.length,
    },
    joins: {
      mapChipJoinHits,
      mapChipJoinMiss,
      mapChipJoinCoverage: parsedMap.cells.length > 0 ? mapChipJoinHits / parsedMap.cells.length : 0,
      uniqueF2Count: f2CoverageValues.uniqueF2Count,
      uniqueF2PresentInMapChip: f2CoverageValues.uniqueF2PresentInMapChip,
      fullTerrainF1ExactMatches: fullTerrainMatch,
      fullTerrainF1ExactRatio: parsedMap.cells.length > 0 ? fullTerrainMatch / parsedMap.cells.length : 0,
    },
    fieldStats: {
      f1Top: topEntriesFromMap(f1Counts, 16).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f2Top: topEntriesFromMap(f2Counts, 16).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f5Top: topEntriesFromMap(f5Counts, 16).map((entry) => ({ value: Number(entry.key), count: entry.count })),
      f1f5PairTop: topEntriesFromMap(collectPairCounts(parsedMap.cells, "f1", "f5"), 20).map((entry) => ({ pair: String(entry.key), count: entry.count })),
    },
    correlations: {
      f1ToF5,
      f5ToF1,
      f3FlagTop: f3FlagCorr,
      topF3OneChips,
      topF3ZeroChips,
      movementByF3: {
        f3_0_meanMovementCost: average(movementCostByF3.get(0) ?? []),
        f3_1_meanMovementCost: average(movementCostByF3.get(1) ?? []),
        f3_0_meanTargetCost: average(targetCostByF3.get(0) ?? []),
        f3_1_meanTargetCost: average(targetCostByF3.get(1) ?? []),
        f3_0_meanMoveSpeedRate: average(speedByF3.get(0) ?? []),
        f3_1_meanMoveSpeedRate: average(speedByF3.get(1) ?? []),
      },
      layerByF3Top: {
        f3_0: topEntriesFromArray(layerByF3.get(0) ?? [], 10),
        f3_1: topEntriesFromArray(layerByF3.get(1) ?? [], 10),
      },
      rotationByF3Top: {
        f3_0: topEntriesFromArray(rotationByF3.get(0) ?? [], 10),
        f3_1: topEntriesFromArray(rotationByF3.get(1) ?? [], 10),
      },
    },
    findings: {
      proven,
      statistical,
      hypotheses,
      unsupportedGuesses,
    },
    interpretation,
    nextStep,
  };

  const outJson = path.resolve(projectRoot, "tmp", "map-section-a-mapchip-correlation.json");
  const outMd = path.resolve(projectRoot, "tmp", "map-section-a-mapchip-correlation.md");

  fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(outMd, buildMarkdown(report), "utf8");

  const summary = {
    outputs: { json: outJson, md: outMd },
    proven: report.findings.proven,
    strongestHypotheses: report.findings.hypotheses.slice(0, 3),
    f3f4MostLikely: report.interpretation.f3f4,
    f1MostLikely: report.interpretation.f1,
    f5MostLikely: report.interpretation.f5,
    nextStep: report.nextStep,
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outJson}`);
  console.log(`Wrote ${outMd}`);
}

function average(values) {
  if (!values || values.length === 0) {
    return null;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function topEntriesFromArray(values, limit = 10) {
  const counts = new Map();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return topEntriesFromMap(counts, limit).map((entry) => ({ value: Number(entry.key), count: entry.count }));
}

run();
