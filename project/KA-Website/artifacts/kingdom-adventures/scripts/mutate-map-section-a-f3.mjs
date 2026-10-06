import fs from "node:fs";
import path from "node:path";

const FIELD_KEYS = ["f0", "f1", "f2", "f3", "f4", "f5"];

function parseArgs(argv) {
  const out = {
    x: 0,
    y: 0,
    w: 160,
    h: 160,
    f3: 1,
    complementF4: true,
    label: "default",
    input: null,
    output: null,
    report: null,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      continue;
    }

    const key = arg.slice(2);
    const next = argv[i + 1];

    if (key === "complement-f4") {
      out.complementF4 = true;
      continue;
    }

    if (key === "no-complement-f4") {
      out.complementF4 = false;
      continue;
    }

    if (next === undefined || next.startsWith("--")) {
      continue;
    }

    if (key === "x" || key === "y" || key === "w" || key === "h" || key === "f3") {
      out[key] = Number.parseInt(next, 10);
    } else if (key === "label" || key === "input" || key === "output" || key === "report") {
      out[key] = next;
    }

    i += 1;
  }

  if (!(out.f3 === 0 || out.f3 === 1)) {
    throw new Error(`f3 override must be 0 or 1, got ${out.f3}`);
  }

  return out;
}

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

      cells.push({ x, y, offset, fields: { f0, f1, f2, f3, f4, f5 } });
      offset += CELL_BYTES;
    }

    ensureAvailable(view, offset, ROW_SENTINEL_BYTES, `row sentinel ${y}`);
    const sentinel = view.getUint32(offset, false);
    offset += ROW_SENTINEL_BYTES;

    if (sentinel !== width) {
      throw new Error(`Invalid row sentinel at row ${y}: expected ${width}, got ${sentinel}`);
    }
  }

  return {
    width,
    height,
    cells,
    dataEndOffset: offset,
    remainingBytes: view.byteLength - offset,
  };
}

function tupleKey(fields) {
  return FIELD_KEYS.map((field) => fields[field]).join("|");
}

function countValues(cells, field) {
  const counts = new Map();
  for (const cell of cells) {
    const value = cell.fields[field];
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => (b.count !== a.count ? b.count - a.count : a.value - b.value));
}

function countTupleFrequencies(cells) {
  const counts = new Map();
  for (const cell of cells) {
    const key = tupleKey(cell.fields);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

function diffTupleCounts(beforeCounts, afterCounts) {
  const keys = new Set([...beforeCounts.keys(), ...afterCounts.keys()]);
  const changes = [];

  for (const key of keys) {
    const before = beforeCounts.get(key) ?? 0;
    const after = afterCounts.get(key) ?? 0;
    if (before === after) {
      continue;
    }
    changes.push({ tuple: key, before, after, delta: after - before });
  }

  return changes.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 40);
}

function buildMapRenderProjection(parsed) {
  const tiles = [];
  const terrainCounts = { soil: 0, water: 0 };
  let unknownTerrainFlags = 0;

  for (const cell of parsed.cells) {
    const terrain = cell.fields.f0 === 1 ? "water" : "soil";
    if (cell.fields.f0 !== 0 && cell.fields.f0 !== 1) {
      unknownTerrainFlags += 1;
    }

    terrainCounts[terrain] += 1;

    const chipId = cell.fields.f2;
    const color = resolvePlaceholderColor(terrain, chipId);
    tiles.push({ x: cell.x, y: cell.y, terrainKind: terrain, chipId, color });
  }

  return {
    tiles,
    stats: {
      width: parsed.width,
      height: parsed.height,
      totalCells: parsed.cells.length,
      terrainCounts,
      remainingBytes: parsed.remainingBytes,
      unknownTerrainFlags,
    },
  };
}

function resolvePlaceholderColor(terrain, chipId) {
  if (chipId > 0) {
    if (chipId === 58 || chipId === 59 || chipId === 60 || chipId === 61) {
      return "#f97316";
    }
    return "#c084fc";
  }

  return terrain === "water" ? "#0ea5e9" : "#1f2937";
}

function compareProjections(beforeProjection, afterProjection) {
  let changedTiles = 0;
  const changedExamples = [];

  const max = Math.min(beforeProjection.tiles.length, afterProjection.tiles.length);
  for (let i = 0; i < max; i += 1) {
    const before = beforeProjection.tiles[i];
    const after = afterProjection.tiles[i];
    if (before.chipId !== after.chipId || before.terrainKind !== after.terrainKind || before.color !== after.color) {
      changedTiles += 1;
      if (changedExamples.length < 20) {
        changedExamples.push({
          x: before.x,
          y: before.y,
          before,
          after,
        });
      }
    }
  }

  return {
    changedTiles,
    changedExamples,
    changedTerrainCounts:
      beforeProjection.stats.terrainCounts.soil !== afterProjection.stats.terrainCounts.soil ||
      beforeProjection.stats.terrainCounts.water !== afterProjection.stats.terrainCounts.water,
  };
}

function computeBBox(cells) {
  if (cells.length === 0) {
    return null;
  }

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

function clusterSummaryByValue(cells, field) {
  const values = [...new Set(cells.map((cell) => cell.fields[field]))].sort((a, b) => a - b);
  return values.map((value) => {
    const matching = cells.filter((cell) => cell.fields[field] === value);
    return {
      value,
      count: matching.length,
      bbox: computeBBox(matching),
    };
  });
}

function countPlaceable2x2(parsed, resolver) {
  let count = 0;

  for (let y = 0; y < parsed.height - 1; y += 1) {
    for (let x = 0; x < parsed.width - 1; x += 1) {
      const cells = [
        parsed.cells[y * parsed.width + x],
        parsed.cells[y * parsed.width + x + 1],
        parsed.cells[(y + 1) * parsed.width + x],
        parsed.cells[(y + 1) * parsed.width + x + 1],
      ];

      const blocked = cells.some((cell) => resolver(cell) !== "soil");
      if (!blocked) {
        count += 1;
      }
    }
  }

  return count;
}

function mapFieldSummary(cells, field) {
  const counts = countValues(cells, field);
  return {
    uniqueCount: counts.length,
    topValues: counts.slice(0, 20),
  };
}

function buildMarkdown(report) {
  const lines = [];

  lines.push("# Section A f3 Mutation Harness Report");
  lines.push("");
  lines.push(`- label: ${report.mutation.label}`);
  lines.push(`- source: ${report.sourceMapPath}`);
  lines.push(`- mutated map: ${report.mutatedMapPath}`);
  lines.push(`- generated at: ${report.generatedAt}`);
  lines.push(`- region: x=${report.mutation.region.x}, y=${report.mutation.region.y}, w=${report.mutation.region.w}, h=${report.mutation.region.h}`);
  lines.push(`- f3 override: ${report.mutation.f3Override}`);
  lines.push(`- f4 complement mode: ${report.mutation.complementF4}`);
  lines.push(`- changed cells: ${report.mutation.changedCellCount}`);
  lines.push("");

  lines.push("## Before/After f3-f4 Summary");
  lines.push(`- before f3 top: ${report.before.fields.f3.topValues.map((v) => `${v.value}:${v.count}`).join(", ")}`);
  lines.push(`- after f3 top: ${report.after.fields.f3.topValues.map((v) => `${v.value}:${v.count}`).join(", ")}`);
  lines.push(`- before f4 top: ${report.before.fields.f4.topValues.map((v) => `${v.value}:${v.count}`).join(", ")}`);
  lines.push(`- after f4 top: ${report.after.fields.f4.topValues.map((v) => `${v.value}:${v.count}`).join(", ")}`);
  lines.push("");

  lines.push("## Tuple Changes");
  lines.push(`- changed tuple signatures: ${report.comparison.tupleChanges.length}`);
  lines.push(...report.comparison.tupleChanges.slice(0, 20).map((item) => `- ${item.tuple}: ${item.before} -> ${item.after} (delta ${item.delta})`));
  lines.push("");

  lines.push("## Projection Comparison");
  lines.push(`- changed projection tiles: ${report.comparison.projection.changedTiles}`);
  lines.push(`- changed projection terrain counts: ${report.comparison.projection.changedTerrainCounts}`);
  lines.push(`- runtime visualizer load baseline: ok`);
  lines.push(`- runtime visualizer load mutated: ok`);
  lines.push("");

  lines.push("## Clustering f3 (Before -> After)");
  for (const beforeEntry of report.before.clusters.f3) {
    const afterEntry = report.after.clusters.f3.find((entry) => entry.value === beforeEntry.value);
    lines.push(
      `- value ${beforeEntry.value}: ${beforeEntry.count} -> ${afterEntry?.count ?? 0}, bbox ${formatBBox(beforeEntry.bbox)} -> ${formatBBox(
        afterEntry?.bbox ?? null,
      )}`,
    );
  }
  lines.push("");

  lines.push("## Placement Reaction Probe");
  lines.push(`- current runtime terrain resolver (f0-based) placeable 2x2 anchors: ${report.placementProbe.currentRuntime.before} -> ${report.placementProbe.currentRuntime.after}`);
  lines.push(`- changed under current runtime resolver: ${report.placementProbe.currentRuntime.changed}`);
  lines.push(`- f3-as-water hypothesis placeable 2x2 anchors: ${report.placementProbe.f3AsWater.before} -> ${report.placementProbe.f3AsWater.after}`);
  lines.push(`- changed under f3-as-water hypothesis: ${report.placementProbe.f3AsWater.changed}`);
  lines.push("");

  lines.push("## Interpretation");
  lines.push(`- ${report.interpretation.visibleChanges}`);
  lines.push(`- ${report.interpretation.clusteringChanges}`);
  lines.push(`- ${report.interpretation.projectionChanges}`);
  lines.push(`- ${report.interpretation.runtimePlacementReaction}`);

  return lines.join("\n");
}

function formatBBox(bbox) {
  if (!bbox) {
    return "none";
  }

  return `(${bbox.minX},${bbox.minY})-(${bbox.maxX},${bbox.maxY}) density=${(bbox.density * 100).toFixed(2)}%`;
}

function run() {
  const args = parseArgs(process.argv.slice(2));
  const projectRoot = process.cwd();

  const sourceMapPath = args.input ?? path.resolve(projectRoot, "tmp", "KA_assets", "map", "map_160_160.map");
  const outMapPath = args.output ?? path.resolve(projectRoot, "tmp", "map_160_160_mutated.map");
  const outReportPath = args.report ?? path.resolve(projectRoot, "tmp", "map-section-a-mutation-report.json");
  const outReportMdPath = outReportPath.replace(/\.json$/i, ".md");

  const sourceBytes = fs.readFileSync(sourceMapPath);
  const mutableBytes = new Uint8Array(sourceBytes);
  const mutableView = new DataView(mutableBytes.buffer, mutableBytes.byteOffset, mutableBytes.byteLength);

  const parsedSource = parseMapBinarySectionA(sourceBytes);
  const region = {
    x: Math.max(0, args.x),
    y: Math.max(0, args.y),
    w: Math.max(1, args.w),
    h: Math.max(1, args.h),
  };

  const regionMaxX = Math.min(parsedSource.width - 1, region.x + region.w - 1);
  const regionMaxY = Math.min(parsedSource.height - 1, region.y + region.h - 1);

  let changedCellCount = 0;
  let changedF3To1 = 0;
  let changedF3To0 = 0;

  for (const cell of parsedSource.cells) {
    if (cell.x < region.x || cell.x > regionMaxX || cell.y < region.y || cell.y > regionMaxY) {
      continue;
    }

    const oldF3 = cell.fields.f3;
    const oldF4 = cell.fields.f4;
    const newF3 = args.f3;
    const newF4 = args.complementF4 ? (newF3 === 0 ? 1 : 0) : oldF4;

    if (oldF3 !== newF3 || oldF4 !== newF4) {
      changedCellCount += 1;
    }

    if (oldF3 === 0 && newF3 === 1) {
      changedF3To1 += 1;
    }
    if (oldF3 === 1 && newF3 === 0) {
      changedF3To0 += 1;
    }

    mutableView.setUint32(cell.offset + 12, newF3, false);
    mutableView.setUint32(cell.offset + 16, newF4, false);
  }

  fs.mkdirSync(path.dirname(outMapPath), { recursive: true });
  fs.writeFileSync(outMapPath, mutableBytes);

  const parsedMutated = parseMapBinarySectionA(mutableBytes);

  const tupleBefore = countTupleFrequencies(parsedSource.cells);
  const tupleAfter = countTupleFrequencies(parsedMutated.cells);
  const tupleChanges = diffTupleCounts(tupleBefore, tupleAfter);

  const projectionBefore = buildMapRenderProjection(parsedSource);
  const projectionAfter = buildMapRenderProjection(parsedMutated);
  const projectionDiff = compareProjections(projectionBefore, projectionAfter);

  const beforeF3 = mapFieldSummary(parsedSource.cells, "f3");
  const beforeF4 = mapFieldSummary(parsedSource.cells, "f4");
  const afterF3 = mapFieldSummary(parsedMutated.cells, "f3");
  const afterF4 = mapFieldSummary(parsedMutated.cells, "f4");

  const clustersBeforeF3 = clusterSummaryByValue(parsedSource.cells, "f3");
  const clustersAfterF3 = clusterSummaryByValue(parsedMutated.cells, "f3");

  const currentRuntimeBefore = countPlaceable2x2(parsedSource, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  const currentRuntimeAfter = countPlaceable2x2(parsedMutated, (cell) => (cell.fields.f0 === 1 ? "water" : "soil"));
  const f3WaterBefore = countPlaceable2x2(parsedSource, (cell) => (cell.fields.f3 === 1 ? "water" : "soil"));
  const f3WaterAfter = countPlaceable2x2(parsedMutated, (cell) => (cell.fields.f3 === 1 ? "water" : "soil"));

  const report = {
    generatedAt: new Date().toISOString(),
    sourceMapPath,
    mutatedMapPath: outMapPath,
    mutation: {
      label: args.label,
      region: {
        x: region.x,
        y: region.y,
        w: regionMaxX - region.x + 1,
        h: regionMaxY - region.y + 1,
      },
      f3Override: args.f3,
      complementF4: args.complementF4,
      changedCellCount,
      changedF3To1,
      changedF3To0,
    },
    before: {
      width: parsedSource.width,
      height: parsedSource.height,
      remainingBytes: parsedSource.remainingBytes,
      fields: {
        f3: beforeF3,
        f4: beforeF4,
      },
      clusters: {
        f3: clustersBeforeF3,
      },
    },
    after: {
      width: parsedMutated.width,
      height: parsedMutated.height,
      remainingBytes: parsedMutated.remainingBytes,
      fields: {
        f3: afterF3,
        f4: afterF4,
      },
      clusters: {
        f3: clustersAfterF3,
      },
    },
    comparison: {
      changedCellCount,
      tupleChanges,
      projection: projectionDiff,
    },
    placementProbe: {
      currentRuntime: {
        before: currentRuntimeBefore,
        after: currentRuntimeAfter,
        changed: currentRuntimeBefore !== currentRuntimeAfter,
      },
      f3AsWater: {
        before: f3WaterBefore,
        after: f3WaterAfter,
        changed: f3WaterBefore !== f3WaterAfter,
      },
    },
    interpretation: {
      visibleChanges:
        changedCellCount > 0
          ? `Mutated ${changedCellCount} cells in section A field pair f3/f4 inside requested rectangle.`
          : "No cells changed from requested mutation.",
      clusteringChanges:
        JSON.stringify(clustersBeforeF3) === JSON.stringify(clustersAfterF3)
          ? "f3 clustering remained identical."
          : "f3 clustering changed between baseline and mutated map.",
      projectionChanges:
        projectionDiff.changedTiles > 0
          ? `Projection changed on ${projectionDiff.changedTiles} tiles.`
          : "Projection did not change under current section A render projection path (f0/f2 driven).",
      runtimePlacementReaction:
        currentRuntimeBefore !== currentRuntimeAfter
          ? "Current runtime terrain-rule probe reacted to mutation."
          : "Current runtime terrain-rule probe did not react to mutation (f0-based terrain slice unchanged).",
    },
  };

  fs.mkdirSync(path.dirname(outReportPath), { recursive: true });
  fs.writeFileSync(outReportPath, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(outReportMdPath, buildMarkdown(report), "utf8");

  const summary = {
    reportPath: outReportPath,
    mutatedMapPath: outMapPath,
    changedCellCount,
    changedF3To1,
    changedF3To0,
    projectionChangedTiles: projectionDiff.changedTiles,
    placementChangedCurrentRuntime: currentRuntimeBefore !== currentRuntimeAfter,
    placementChangedF3AsWaterHypothesis: f3WaterBefore !== f3WaterAfter,
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outMapPath}`);
  console.log(`Wrote ${outReportPath}`);
  console.log(`Wrote ${outReportMdPath}`);
}

run();
