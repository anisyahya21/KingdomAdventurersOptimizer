import fs from "node:fs";
import path from "node:path";

const KEYWORDS = [
  "CellUtil",
  "MapSystem",
  "ChipPlaceSystem",
  "TownSystem",
  "MoveSystem",
  "Path",
  "Route",
  "Grid",
  "Area",
  "CanPlace",
  "IsBuildable",
  "IsWalkable",
  "GetCell",
  "SetCell",
  "MapChipRect",
  "PlaceMapChips",
  "GetTerrainData",
  "GetMapChip",
  "ModifyState",
  "ReadMapData",
  "WriteMapData",
];

const PRIORITY_METHOD_PATTERNS = [
  "ChipPlaceSystem::PlaceChip",
  "ChipPlaceSystem::CheckPlace",
  "MapSystem::ReadMapData",
  "MapSystem::WriteMapData",
  "MapSystem::CreateMapChips",
  "MapSystem::GetDefaultMapChipData",
  "MapSystem::GetModifyState",
  "MapSystem::SetModifyState",
  "EnemyBaseSystem::CanPlaceDungeon",
  "Astar::FindPath",
  "NodeGraph::CanConnectNode",
  "NodeGraph::UpdateNodeCost",
  "TownSystem",
  "MapChipRect",
];

function normalizeName(name) {
  return name.replace(/\u003C/g, "<").replace(/\u003E/g, ">");
}

function scoreCandidate(name, signature) {
  const hay = `${name} ${signature}`.toLowerCase();
  let score = 0;

  for (const keyword of KEYWORDS) {
    if (hay.includes(keyword.toLowerCase())) {
      score += 1;
    }
  }

  if (hay.includes("placechip") || hay.includes("checkplace") || hay.includes("canplace")) {
    score += 5;
  }
  if (hay.includes("mapdata") || hay.includes("createmapchips") || hay.includes("modifystate")) {
    score += 4;
  }
  if (hay.includes("findpath") || hay.includes("nodegraph") || hay.includes("route")) {
    score += 3;
  }
  if (hay.includes("terrain") || hay.includes("mapchiprect") || hay.includes("townarea")) {
    score += 2;
  }

  return score;
}

function likelyFieldIndices(name, signature) {
  const hay = `${name} ${signature}`.toLowerCase();

  if (hay.includes("placechip") || hay.includes("checkplace") || hay.includes("canplace")) {
    return {
      likelyFieldIndexRead: [3, 4],
      rationale: "Placement checks are strongest known consumers of binary region masks; f3/f4 complement pattern fits this path.",
    };
  }

  if (hay.includes("getterraindata") || hay.includes("water") || hay.includes("ground")) {
    return {
      likelyFieldIndexRead: [0],
      rationale: "Terrain helpers usually consume terrain-type fields, currently aligned with f0 terrain slice.",
    };
  }

  if (hay.includes("getmapchip") || hay.includes("mapchipdata") || hay.includes("changemapchip")) {
    return {
      likelyFieldIndexRead: [2],
      rationale: "MapChip reads likely align with chip/data id-like field currently modeled as f2.",
    };
  }

  if (hay.includes("modifystate") || hay.includes("mapdata") || hay.includes("createmapchips")) {
    return {
      likelyFieldIndexRead: [3, 4, 5],
      rationale: "Map serialization/modify-state code is a likely location for auxiliary section A flags including f3/f4/f5.",
    };
  }

  if (hay.includes("findpath") || hay.includes("nodegraph") || hay.includes("canconnectnode")) {
    return {
      likelyFieldIndexRead: [3, 4],
      rationale: "Path connectivity often consumes binary blocked/open flags, matching f3/f4 binary complement characteristics.",
    };
  }

  return {
    likelyFieldIndexRead: null,
    rationale: "No field-index evidence from signature alone.",
  };
}

function extractDumpMethods(dumpText) {
  const lines = dumpText.split(/\r?\n/);
  const methods = [];
  let currentClass = "";
  let pendingRva = null;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const classMatch = line.match(/^public class\s+([^\s:]+)/) || line.match(/^public static class\s+([^\s:]+)/) || line.match(/^private class\s+([^\s:]+)/) || line.match(/^public abstract class\s+([^\s:]+)/);
    if (classMatch) {
      currentClass = classMatch[1];
    }

    const rvaMatch = line.match(/\/\/ RVA:\s*(0x[0-9A-Fa-f]+)/);
    if (rvaMatch) {
      pendingRva = rvaMatch[1];
      continue;
    }

    const methodMatch = line.match(/^\s*(public|private|internal|protected)\s+[^\(]*\s+([A-Za-z0-9_<>\.]+)\(([^\)]*)\)\s*\{\s*\}/);
    if (methodMatch) {
      methods.push({
        className: currentClass,
        methodName: methodMatch[2],
        signatureText: line.trim(),
        rva: pendingRva,
        lineNumber: i + 1,
      });
      pendingRva = null;
    }
  }

  return methods;
}

function extractScriptEntries(scriptText) {
  const entries = [];

  const fullEntryRegex = /\{\s*"Address":\s*(\d+),\s*"Name":\s*"([^"]+)",\s*"Signature":\s*"([^"]*)",\s*"TypeSignature":\s*"([^"]*)"\s*\}/gs;
  let match;
  while ((match = fullEntryRegex.exec(scriptText)) !== null) {
    entries.push({
      address: Number.parseInt(match[1], 10),
      name: normalizeName(match[2]),
      signature: normalizeName(match[3]),
      typeSignature: match[4],
    });
  }

  const methodAddressRegex = /\{\s*"Address":\s*(\d+),\s*"Name":\s*"([^"]+)",\s*"MethodAddress":\s*(\d+)\s*\}/gs;
  const methodAddressMap = new Map();
  while ((match = methodAddressRegex.exec(scriptText)) !== null) {
    methodAddressMap.set(normalizeName(match[2]), Number.parseInt(match[3], 10));
  }

  return { entries, methodAddressMap };
}

function toHexAddress(n) {
  if (typeof n !== "number" || Number.isNaN(n)) {
    return null;
  }
  return `0x${n.toString(16).toUpperCase()}`;
}

function buildCandidates(dumpMethods, scriptEntries, methodAddressMap) {
  const candidates = [];

  const bySimpleName = new Map();
  for (const scriptEntry of scriptEntries) {
    const lastNamePart = scriptEntry.name.split("$$").pop() ?? scriptEntry.name;
    if (!bySimpleName.has(lastNamePart)) {
      bySimpleName.set(lastNamePart, []);
    }
    bySimpleName.get(lastNamePart).push(scriptEntry);
  }

  for (const method of dumpMethods) {
    const fullName = `${method.className}::${method.methodName}`;
    const methodHay = `${fullName} ${method.signatureText}`;
    const hasKeyword = KEYWORDS.some((keyword) => methodHay.toLowerCase().includes(keyword.toLowerCase()));
    if (!hasKeyword) {
      continue;
    }

    const scriptMatches = bySimpleName.get(method.methodName) ?? [];
    const primaryScript = scriptMatches.find((entry) => entry.name.toLowerCase().includes(method.className.toLowerCase())) ?? scriptMatches[0] ?? null;

    const fieldGuess = likelyFieldIndices(fullName, method.signatureText);
    const score = scoreCandidate(fullName, method.signatureText);

    let branchBehavior = "unknown (script.json + dump.cs signatures do not include instruction-level branches)";
    let constantsCompared = [];

    if (method.className === "MapSystem") {
      constantsCompared = [
        "TYPE_WATER=0",
        "TYPE_GROUND=1",
        "MapChipCreationFlags.Default=0",
        "MapChipCreationFlags.AreaToPlayable=1",
      ];
      branchBehavior = "likely branch logic inside MapSystem map creation/update methods; constants present in class metadata but compare sites require binary disassembly";
    }

    candidates.push({
      methodName: fullName,
      dumpLine: method.lineNumber,
      dumpRva: method.rva,
      scriptAddress: primaryScript ? toHexAddress(primaryScript.address) : null,
      scriptMethodAddress: primaryScript ? toHexAddress(methodAddressMap.get(`Method$${primaryScript.name}()`) ?? primaryScript.address) : null,
      scriptSignature: primaryScript?.signature ?? null,
      likelyFieldIndexRead: fieldGuess.likelyFieldIndexRead,
      likelyFieldRationale: fieldGuess.rationale,
      constantsCompared,
      branchBehavior,
      relevanceScore: score,
      relatesToF3F4: (fieldGuess.likelyFieldIndexRead ?? []).includes(3) || (fieldGuess.likelyFieldIndexRead ?? []).includes(4),
      evidenceType: "signature-level",
    });
  }

  candidates.sort((a, b) => b.relevanceScore - a.relevanceScore || a.methodName.localeCompare(b.methodName));

  return candidates;
}

function pickStrongest(candidates) {
  const picked = [];

  for (const pattern of PRIORITY_METHOD_PATTERNS) {
    const found = candidates.find((item) => item.methodName.includes(pattern));
    if (found && !picked.includes(found)) {
      picked.push(found);
    }
  }

  for (const item of candidates) {
    if (picked.length >= 20) {
      break;
    }
    if (!picked.includes(item)) {
      picked.push(item);
    }
  }

  return picked.slice(0, 20);
}

function buildSummary(strongest) {
  const proven = [];
  const likely = [];
  const unknowns = [];

  if (strongest.some((item) => item.methodName.includes("ChipPlaceSystem::PlaceChip"))) {
    proven.push("Native method signature for placement exists: ChipPlaceSystem::PlaceChip (instance and static overloads). This is a top candidate reader path for cell-state gates.");
  }
  if (strongest.some((item) => item.methodName.includes("ChipPlaceSystem::CheckPlace"))) {
    proven.push("Native method signature for placement validation exists: ChipPlaceSystem::CheckPlace variants.");
  }
  if (strongest.some((item) => item.methodName.includes("MapSystem::ReadMapData")) && strongest.some((item) => item.methodName.includes("MapSystem::WriteMapData"))) {
    proven.push("Map serialization/deserialization signatures exist: MapSystem::ReadMapData and MapSystem::WriteMapData.");
  }
  if (strongest.some((item) => item.methodName.includes("MapSystem::CreateMapChips"))) {
    proven.push("MapSystem::CreateMapChips exists and is likely involved in map-layer reconstruction from saved map data.");
  }

  likely.push("f3/f4 are likely consumed in ChipPlaceSystem::CheckPlace / PlaceChip path as binary region/buildability flags due complement behavior and placement-method proximity.");
  likely.push("MapSystem::GetModifyState / SetModifyState / GetDefaultMapChipData are likely key translation points between serialized chip layers and runtime cell flags.");
  likely.push("Pathing stack (Astar::FindPath + NodeGraph::CanConnectNode / UpdateNodeCost) is likely the next reader family if f3/f4 influence walkability or connectivity.");

  unknowns.push("Instruction-level constants and branch comparisons for f3/f4 are not present in dump.cs/script.json signatures alone.");
  unknowns.push("Exact field-slot index loads (e.g., chips[layer][y][x] index for f3/f4) require disassembly or decompilation of method bodies.");
  unknowns.push("Ownership vs buildability vs pathability semantics cannot be proven without method-body trace or runtime instrumentation.");

  return {
    provenNativeEvidence: proven,
    likelyHypotheses: likely,
    unknowns,
    recommendedNextRuntimeSystemToReproduce: {
      system: "ChipPlaceSystem placement validation pipeline",
      exactReason: "Strongest native candidates are PlaceChip/CheckPlace; this is the closest path to proving whether f3/f4 gate buildability or occupancy checks.",
      followupMethods: [
        "ChipPlaceSystem::CheckPlace",
        "ChipPlaceSystem::PlaceChip",
        "MapSystem::GetModifyState",
        "MapSystem::CreateMapChips",
      ],
    },
  };
}

function buildMarkdown(report) {
  const lines = [];
  lines.push("# Section A Native Reader Trace");
  lines.push("");
  lines.push(`- generatedAt: ${report.generatedAt}`);
  lines.push(`- dump source: ${report.sources.dumpCs}`);
  lines.push(`- script source: ${report.sources.scriptJson}`);
  lines.push("");

  lines.push("## Strongest Candidate Methods");
  for (const item of report.strongestCandidates) {
    lines.push(`### ${item.methodName}`);
    lines.push(`- dump line: ${item.dumpLine}`);
    lines.push(`- dump RVA: ${item.dumpRva ?? "unknown"}`);
    lines.push(`- script address: ${item.scriptAddress ?? "unknown"}`);
    lines.push(`- signature: ${item.scriptSignature ?? "unknown"}`);
    lines.push(`- likely field index read: ${item.likelyFieldIndexRead ? item.likelyFieldIndexRead.join(", ") : "unknown"}`);
    lines.push(`- constants compared: ${item.constantsCompared.length > 0 ? item.constantsCompared.join(", ") : "unknown"}`);
    lines.push(`- nearby branch behavior: ${item.branchBehavior}`);
    lines.push(`- f3/f4 relevance: ${item.relatesToF3F4}`);
    lines.push("");
  }

  lines.push("## Proven Native Evidence");
  for (const entry of report.conclusions.provenNativeEvidence) {
    lines.push(`- ${entry}`);
  }

  lines.push("");
  lines.push("## Likely Hypothesis");
  for (const entry of report.conclusions.likelyHypotheses) {
    lines.push(`- ${entry}`);
  }

  lines.push("");
  lines.push("## Unknowns");
  for (const entry of report.conclusions.unknowns) {
    lines.push(`- ${entry}`);
  }

  lines.push("");
  lines.push("## Recommended Next Runtime System");
  lines.push(`- system: ${report.conclusions.recommendedNextRuntimeSystemToReproduce.system}`);
  lines.push(`- reason: ${report.conclusions.recommendedNextRuntimeSystemToReproduce.exactReason}`);
  lines.push(`- follow-up methods: ${report.conclusions.recommendedNextRuntimeSystemToReproduce.followupMethods.join(", ")}`);

  return lines.join("\n");
}

function run() {
  const projectRoot = process.cwd();
  const dumpDir = process.env.KA_IL2CPP_DUMP_DIR || path.resolve(process.env.LOCALAPPDATA ?? "", "Temp", "ka_il2cpp_dump_arm64");

  const dumpCs = path.resolve(dumpDir, "dump.cs");
  const scriptJson = path.resolve(dumpDir, "script.json");

  if (!fs.existsSync(dumpCs) || !fs.existsSync(scriptJson)) {
    throw new Error(`IL2CPP dump files not found under ${dumpDir}`);
  }

  const dumpText = fs.readFileSync(dumpCs, "utf8");
  const scriptText = fs.readFileSync(scriptJson, "utf8");

  const dumpMethods = extractDumpMethods(dumpText);
  const { entries: scriptEntries, methodAddressMap } = extractScriptEntries(scriptText);
  const candidates = buildCandidates(dumpMethods, scriptEntries, methodAddressMap);
  const strongestCandidates = pickStrongest(candidates);
  const conclusions = buildSummary(strongestCandidates);

  const report = {
    generatedAt: new Date().toISOString(),
    sources: {
      dumpCs,
      scriptJson,
    },
    stats: {
      dumpMethodsScanned: dumpMethods.length,
      scriptEntriesScanned: scriptEntries.length,
      candidateCount: candidates.length,
      strongestCount: strongestCandidates.length,
    },
    strongestCandidates,
    conclusions,
  };

  const outJson = path.resolve(projectRoot, "tmp", "section-a-native-reader-trace.json");
  const outMd = path.resolve(projectRoot, "tmp", "section-a-native-reader-trace.md");

  fs.mkdirSync(path.dirname(outJson), { recursive: true });
  fs.writeFileSync(outJson, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(outMd, buildMarkdown(report), "utf8");

  const summary = {
    strongestCandidateMethods: strongestCandidates.slice(0, 8).map((item) => ({
      method: item.methodName,
      rva: item.dumpRva,
      address: item.scriptAddress,
      likelyFieldIndexRead: item.likelyFieldIndexRead,
      relatesToF3F4: item.relatesToF3F4,
    })),
    nextRuntimeSystem: conclusions.recommendedNextRuntimeSystemToReproduce,
    outputs: {
      json: outJson,
      md: outMd,
    },
  };

  console.log(JSON.stringify(summary, null, 2));
  console.log(`Wrote ${outJson}`);
  console.log(`Wrote ${outMd}`);
}

run();
