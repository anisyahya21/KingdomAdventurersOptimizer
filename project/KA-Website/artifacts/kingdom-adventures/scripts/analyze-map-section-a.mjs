import fs from "node:fs";
import path from "node:path";

const FIELD_KEYS = ["f0", "f1", "f2", "f3", "f4", "f5"];

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

      cells.push({
        x,
        y,
        fields: { f0, f1, f2, f3, f4, f5 },
      });
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
    remainingBytes: view.byteLength - offset,
  };
}

function ensureAvailable(view, offset, needed, label) {
  if (offset + needed > view.byteLength) {
    throw new Error(`Unexpected end of map binary while reading ${label}`);
  }
}

function tupleKey(fields) {
  return FIELD_KEYS.map((field) => fields[field]).join("|");
}

function computeFieldAnalyses(parsed) {
  const totalCells = parsed.cells.length;
  const result = {};

  for (const field of FIELD_KEYS) {
    const valueMap = new Map();

    for (const cell of parsed.cells) {
      const value = cell.fields[field];
      const existing = valueMap.get(value);

      if (existing) {
        existing.count += 1;
        existing.minX = Math.min(existing.minX, cell.x);
        existing.minY = Math.min(existing.minY, cell.y);
        existing.maxX = Math.max(existing.maxX, cell.x);
        existing.maxY = Math.max(existing.maxY, cell.y);
      } else {
        valueMap.set(value, {
          value,
          count: 1,
          minX: cell.x,
          minY: cell.y,
          maxX: cell.x,
          maxY: cell.y,
          area: 1,
          density: 1,
        });
      }
    }

    const values = [...valueMap.values()].map((entry) => {
      const width = entry.maxX - entry.minX + 1;
      const height = entry.maxY - entry.minY + 1;
      const area = width * height;
      const density = area > 0 ? entry.count / area : 0;
      return {
        ...entry,
        area,
        density,
      };
    });

    values.sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }
      return left.value - right.value;
    });

    const uniqueCount = values.length;
    const min = uniqueCount > 0 ? Math.min(...values.map((entry) => entry.value)) : 0;
    const max = uniqueCount > 0 ? Math.max(...values.map((entry) => entry.value)) : 0;
    const topValues = values.slice(0, 10).map((entry) => ({ value: entry.value, count: entry.count }));
    const zeroCount = valueMap.get(0)?.count ?? 0;
    const zeroRatio = totalCells > 0 ? zeroCount / totalCells : 0;
    const uniqueRatio = totalCells > 0 ? uniqueCount / totalCells : 0;

    const clusterCandidates = values
      .filter((entry) => entry.count >= 8 && entry.density >= 0.25)
      .map((entry) => ({
        value: entry.value,
        count: entry.count,
        minX: entry.minX,
        minY: entry.minY,
        maxX: entry.maxX,
        maxY: entry.maxY,
        area: entry.area,
        density: entry.density,
        clusterScore: entry.count * entry.density,
      }))
      .sort((left, right) => {
        if (right.clusterScore !== left.clusterScore) {
          return right.clusterScore - left.clusterScore;
        }
        if (right.count !== left.count) {
          return right.count - left.count;
        }
        return left.value - right.value;
      })
      .slice(0, 12);

    const highVariance = uniqueRatio >= 0.2;

    result[field] = {
      field,
      uniqueCount,
      min,
      max,
      constant: uniqueCount <= 1,
      topValues,
      zeroRatio,
      uniqueRatio,
      clusterCandidates,
      hypothesis: deriveFieldHypothesis({
        uniqueCount,
        totalCells,
        zeroRatio,
        clusteredCount: clusterCandidates.length,
        highVariance,
      }),
    };
  }

  return result;
}

function deriveFieldHypothesis(input) {
  if (input.uniqueCount <= 1) {
    return "constant / probably header-default";
  }

  const uniqueRatio = input.totalCells > 0 ? input.uniqueCount / input.totalCells : 0;

  if (input.zeroRatio >= 0.9 && uniqueRatio <= 0.05) {
    return "sparse / likely object or overlay";
  }

  if (input.clusteredCount > 0 && uniqueRatio <= 0.15) {
    return "clustered / likely terrain-region-layer";
  }

  if (input.highVariance || uniqueRatio >= 0.2) {
    return "high-variance / unknown index-id field";
  }

  return "mixed behavior / requires cross-map comparison";
}

function computeTupleStats(parsed) {
  const tupleCounts = new Map();

  for (const cell of parsed.cells) {
    const key = tupleKey(cell.fields);
    tupleCounts.set(key, (tupleCounts.get(key) ?? 0) + 1);
  }

  const sorted = [...tupleCounts.entries()]
    .map(([tuple, count]) => ({ tuple, count }))
    .sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }
      return left.tuple.localeCompare(right.tuple);
    });

  let rareTupleCount = 0;
  let rareTupleCellCount = 0;

  for (const entry of sorted) {
    if (entry.count === 1) {
      rareTupleCount += 1;
      rareTupleCellCount += 1;
    }
  }

  return {
    uniqueTupleCount: sorted.length,
    topFrequencies: sorted.slice(0, 100),
    rareTupleCount,
    rareTupleCellCount,
  };
}

function recommendNextDecodingTarget(fieldAnalyses) {
  const fields = FIELD_KEYS.map((field) => fieldAnalyses[field]);

  const clustered = fields
    .filter((entry) => entry.hypothesis.startsWith("clustered") && entry.clusterCandidates.length > 0)
    .sort((left, right) => {
      const leftScore = left.clusterCandidates[0].clusterScore;
      const rightScore = right.clusterCandidates[0].clusterScore;
      if (rightScore !== leftScore) {
        return rightScore - leftScore;
      }
      return left.uniqueCount - right.uniqueCount;
    });

  if (clustered.length > 0) {
    return {
      field: clustered[0].field,
      reason:
        "strong spatial clustering with moderate uniqueness; likely terrain-region-layer semantics worth decoding first",
      hypothesis: clustered[0].hypothesis,
    };
  }

  const sparse = fields
    .filter((entry) => entry.hypothesis.startsWith("sparse") && entry.topValues.some((value) => value.value !== 0))
    .sort((left, right) => left.uniqueCount - right.uniqueCount);

  if (sparse.length > 0) {
    return {
      field: sparse[0].field,
      reason: "mostly default values with sparse non-zero islands; likely overlays-objects and good second decode target",
      hypothesis: sparse[0].hypothesis,
    };
  }

  const highVariance = fields
    .filter((entry) => entry.hypothesis.startsWith("high-variance"))
    .sort((left, right) => left.uniqueCount - right.uniqueCount);

  if (highVariance.length > 0) {
    return {
      field: highVariance[0].field,
      reason: "high variance but comparatively constrained cardinality; possible index table or IDs",
      hypothesis: highVariance[0].hypothesis,
    };
  }

  return {
    field: fields[0]?.field ?? "f0",
    reason: "fallback target; no strong automated signal",
    hypothesis: fields[0]?.hypothesis ?? "unknown",
  };
}

function buildMarkdownReport(report, reportPath) {
  const lines = [];

  lines.push("# Map Section A Automated Analysis");
  lines.push("");
  lines.push(`- source: ${report.sourceMapPath}`);
  lines.push(`- width x height: ${report.width} x ${report.height}`);
  lines.push(`- total cells: ${report.totalCells}`);
  lines.push(`- remaining bytes after section A: ${report.remainingBytes}`);
  lines.push(`- generated at: ${report.generatedAt}`);
  lines.push(`- report file: ${reportPath}`);
  lines.push("");
  lines.push("## Auto Hypotheses");

  for (const field of FIELD_KEYS) {
    const item = report.fields[field];
    lines.push(`- ${field}: ${item.hypothesis}`);
  }

  lines.push("");
  lines.push("## Recommended Next Decoding Target");
  lines.push(`- field: ${report.recommendedNextTarget.field}`);
  lines.push(`- hypothesis: ${report.recommendedNextTarget.hypothesis}`);
  lines.push(`- reason: ${report.recommendedNextTarget.reason}`);
  lines.push("");
  lines.push("## Per-Field Stats");

  for (const field of FIELD_KEYS) {
    const item = report.fields[field];
    lines.push(`### ${field}`);
    lines.push(`- unique: ${item.uniqueCount}`);
    lines.push(`- min/max: ${item.min} / ${item.max}`);
    lines.push(`- constant: ${item.constant}`);
    lines.push(`- zero ratio: ${(item.zeroRatio * 100).toFixed(2)}%`);
    lines.push(`- top values: ${item.topValues.map((entry) => `${entry.value}:${entry.count}`).join(", ")}`);
    const clusterPreview = item.clusterCandidates
      .slice(0, 3)
      .map((entry) => {
        return `${entry.value} [${entry.minX},${entry.minY}]-[${entry.maxX},${entry.maxY}] count=${entry.count} density=${(
          entry.density * 100
        ).toFixed(2)}%`;
      })
      .join(" | ");
    lines.push(`- strongest clusters: ${clusterPreview || "none"}`);
    lines.push("");
  }

  lines.push("## Tuple Frequency");
  lines.push(`- unique tuples: ${report.tuples.uniqueTupleCount}`);
  lines.push(`- rare tuple count: ${report.tuples.rareTupleCount}`);
  lines.push(`- rare tuple cell count: ${report.tuples.rareTupleCellCount}`);
  lines.push("");
  lines.push("### Top Tuple Frequencies");

  for (const entry of report.tuples.topFrequencies.slice(0, 20)) {
    lines.push(`- count ${entry.count}: ${entry.tuple}`);
  }

  return lines.join("\n");
}

function run() {
  const projectRoot = process.cwd();
  const sourceMapPath = path.resolve(projectRoot, "tmp", "KA_assets", "map", "map_160_160.map");
  const outJsonPath = path.resolve(projectRoot, "tmp", "map-section-a-analysis.json");
  const outMdPath = path.resolve(projectRoot, "tmp", "map-section-a-analysis.md");

  const bytes = fs.readFileSync(sourceMapPath);
  const parsed = parseMapBinarySectionA(bytes);

  const fields = computeFieldAnalyses(parsed);
  const tuples = computeTupleStats(parsed);
  const recommendedNextTarget = recommendNextDecodingTarget(fields);

  const report = {
    generatedAt: new Date().toISOString(),
    sourceMapPath,
    width: parsed.width,
    height: parsed.height,
    totalCells: parsed.cells.length,
    remainingBytes: parsed.remainingBytes,
    fields,
    tuples,
    recommendedNextTarget,
  };

  fs.mkdirSync(path.dirname(outJsonPath), { recursive: true });
  fs.writeFileSync(outJsonPath, JSON.stringify(report, null, 2), "utf8");

  const markdown = buildMarkdownReport(report, outJsonPath);
  fs.writeFileSync(outMdPath, markdown, "utf8");

  const summary = {
    width: report.width,
    height: report.height,
    totalCells: report.totalCells,
    remainingBytes: report.remainingBytes,
    hypotheses: Object.fromEntries(FIELD_KEYS.map((field) => [field, report.fields[field].hypothesis])),
    recommendedNextTarget: report.recommendedNextTarget,
    topTupleCount: report.tuples.topFrequencies[0]?.count ?? 0,
    rareTupleCount: report.tuples.rareTupleCount,
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outJsonPath}`);
  console.log(`Wrote ${outMdPath}`);
}

run();
