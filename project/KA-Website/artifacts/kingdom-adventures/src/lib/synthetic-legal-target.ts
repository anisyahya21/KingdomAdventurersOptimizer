import { PARAMETER_STAT_KEYS } from "@/game-data/stat-parameter-ids";
import {
  ENCOUNTER_BY_ID,
  EQUIPMENT_BY_ID,
  SKILL_BY_ID,
  renderSkillName,
} from "@/lib/battle-setup";
import type { NormalizedOptimizerTarget, OptimizerEvidence, OptimizerTargetUnit } from "@/lib/synthetic-legal-model";

type RecordValue = Record<string, unknown>;

/**
 * Normalize one read-only library candidate without promoting display names to game identities.
 * The complete original candidate/scenario/stats payload is kept under `raw` and per-field unknowns.
 */
export function normalizeOptimizerTarget(
  candidateValue: unknown,
  options: { libraryName: string; librarySchemaVersion: number },
): NormalizedOptimizerTarget {
  const candidate = record(candidateValue);
  const scenario = record(candidate.scenario);
  const stats = record(candidate.stats);
  const indexed = record(candidate.encounterIdentity);
  const indexedId = integerOrNull(indexed.indexedId);
  const scenarioId = integerOrNull(indexed.scenarioId ?? scenario.encounterId);
  const encounterId = indexed.matches === true && indexedId === scenarioId && ENCOUNTER_BY_ID.has(indexedId ?? -1)
    ? indexedId
    : null;
  const evidence = normalizeEvidence(candidate, options);
  const ownUnits = Array.isArray(scenario.ownUnits) ? scenario.ownUnits : [];
  const nameCounts = new Map<string, number>();
  for (const value of ownUnits) {
    const name = stringOr(record(value).name, "");
    if (name) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
  }
  const units = ownUnits.map((unit, index) => {
    const name = stringOr(record(unit).name, "");
    return normalizeUnit(unit, index, stats, Boolean(name && (nameCounts.get(name) ?? 0) > 1));
  });
  const simulatorOnly: NormalizedOptimizerTarget["simulatorOnly"] = [];
  const unmapped: NormalizedOptimizerTarget["unmapped"] = [];
  if (encounterId === null) {
    unmapped.push({
      field: "encounterId",
      value: { indexedId, scenarioId },
      reason: "The library encounter index and scenario encounter ID do not both resolve to the site's encounter catalog.",
    });
  }
  for (const key of ["libSeed", "mathSeed", "tickLimit", "finishPolicy", "defeatCount", "inputs", "startProfile"]) {
    if (key in scenario) simulatorOnly.push({ field: key, value: scenario[key], source: "optimizer scenario" });
  }
  for (const unit of units) unmapped.push(...unit.unmapped);
  if (units.length === 0) {
    unmapped.push({ field: "ownUnits", value: scenario.ownUnits ?? null, reason: "Candidate contains no player-side unit requirements." });
  }
  return {
    schema: "ka-normalized-optimizer-target-1",
    encounterId,
    encounterIdentity: { indexedId, scenarioId, matches: encounterId !== null },
    evidence,
    units,
    exactPoint: Object.fromEntries(units.map((unit) => [unit.key, unit.exactPoint])),
    viabilityRegion: null,
    simulatorOnly,
    unmapped,
    raw: candidate,
  };
}

function normalizeEvidence(
  candidate: RecordValue,
  options: { libraryName: string; librarySchemaVersion: number },
): OptimizerEvidence {
  const performance = record(candidate.performance);
  const uncertainty = record(performance.uncertainty);
  return {
    libraryName: options.libraryName,
    librarySchemaVersion: options.librarySchemaVersion,
    candidateId: stringOr(candidate.id, ""),
    label: stringOrNull(candidate.label),
    source: stringOrNull(candidate.source),
    family: stringOrNull(candidate.family),
    phase: stringOr(performance.phase, "unknown"),
    meanEarned: numberOrNull(performance.meanEarned),
    highestEarned: numberOrNull(performance.highestEarned),
    sampleCount: integerOrNull(performance.sampleCount) ?? 0,
    uncertainty: {
      kind: stringOr(uncertainty.kind, "unknown"),
      value: numberOrNull(uncertainty.value),
    },
    raw: candidate,
  };
}

function normalizeUnit(value: unknown, index: number, allStats: RecordValue, ambiguousStatName: boolean): OptimizerTargetUnit {
  const unit = record(value);
  const name = stringOr(unit.name, `Unit ${index + 1}`);
  const stats = record(allStats[name]);
  const parameterStats = record(stats.parameters);
  const identity = normalizeIdentity(unit);
  const kind = unit.human === true ? "human" : unit.human === false && unit.monsterId != null ? "pet" : "unknown";
  const exactParameters: OptimizerTargetUnit["exactParameters"] = {};
  const exactPoint: OptimizerTargetUnit["exactPoint"] = {};
  const derived: OptimizerTargetUnit["derived"] = [];
  const controllable: OptimizerTargetUnit["controllable"] = [];
  const simulatorOnly: OptimizerTargetUnit["simulatorOnly"] = [];
  const unmapped: OptimizerTargetUnit["unmapped"] = [...identity.unmapped];

  if (ambiguousStatName) {
    unmapped.push({
      field: "statsIdentityAmbiguous",
      value: name,
      reason: "The optimizer stores stat summaries by unit display name, and this candidate repeats that name. The per-unit stat row cannot be assigned without guessing.",
    });
  }

  if (kind !== "human") {
    unmapped.push({
      field: "unitKind",
      value: { human: unit.human ?? null, monsterId: unit.monsterId ?? null },
      reason: kind === "pet"
        ? "This target is a monster/pet unit. The SavedLoadout resident translator does not yet model pet species, level curves, household ownership, or pet skills."
        : "The candidate does not identify this player-side unit as a resident human or a catalogued pet; it cannot be translated as a resident.",
    });
  }

  for (const [parameterId, rawParameter] of Object.entries(parameterStats)) {
    const parameter = record(rawParameter);
    const current = numberOrNull(parameter.value);
    const maximum = numberOrNull(parameter.maximum);
    exactParameters[parameterId] = { value: current, maximum };
    const statKey = PARAMETER_STAT_KEYS[Number(parameterId)];
    if (statKey) {
      // The first three parameters are bounded. Their permanent build target is the prepared
      // maximum; the current pool can also reflect simulator state and is retained separately.
      const targetValue = [10, 11, 12].includes(Number(parameterId)) ? maximum : current;
      if (targetValue !== null) {
        exactPoint[statKey] = targetValue;
        derived.push({ field: statKey, value: targetValue, source: `optimizer stats.parameters.${parameterId}.${[10, 11, 12].includes(Number(parameterId)) ? "maximum" : "value"}` });
      }
      if ([10, 11, 12].includes(Number(parameterId)) && current !== maximum) {
        simulatorOnly.push({ field: `${statKey}.currentValue`, value: current, source: "optimizer prepared current pool; not used as the permanent stat target" });
      }
    } else {
      unmapped.push({ field: `parameters.${parameterId}`, value: { current, maximum }, reason: "No canonical website stat key is mapped to this native parameter ID." });
    }
  }

  if (Array.isArray(unit.equipment)) {
    const equipment = unit.equipment.map((entry, slotIndex) => {
      const recordEntry = record(entry);
      const id = integerOrNull(recordEntry.id);
      const level = integerOrNull(recordEntry.level);
      const catalog = id === null ? undefined : EQUIPMENT_BY_ID.get(id);
      if (!catalog) {
        unmapped.push({ field: `equipment[${slotIndex}]`, value: entry, reason: "Equipment ID is not present in the recovered site's battle equipment catalog." });
      }
      return { id, name: catalog?.name ?? null, level, affinity: numberOrNull(recordEntry.affinity) };
    });
    controllable.push({ field: "equipment", value: equipment, source: "optimizer ownUnits[].equipment" });
  }

  if (Array.isArray(unit.skills)) {
    const skills = unit.skills.map((value, skillIndex) => {
      const id = integerOrNull(value);
      const catalog = id === null ? undefined : SKILL_BY_ID.get(id);
      if (!catalog) unmapped.push({ field: `skills[${skillIndex}]`, value, reason: "Skill ID is not in the recovered site skill catalog." });
      return { id, name: catalog ? renderSkillName(catalog) : null };
    });
    controllable.push({ field: "skills", value: skills, source: "optimizer ownUnits[].skills" });
  }
  if (Array.isArray(unit.invocationLevels)) {
    controllable.push({ field: "skillInvocations", value: unit.invocationLevels, source: "optimizer ownUnits[].invocationLevels" });
  }
  if (Array.isArray(unit.equipment) || Array.isArray(unit.skills)) {
    unmapped.push({ field: "jobName/rank", value: { jobId: unit.jobId ?? null, rank: unit.rank ?? null, name }, reason: "This candidate record does not carry a canonical character/job/rank identity. Display names are not parsed into game identity." });
  }
  for (const key of ["visitor", "leaderIdentity", "weaponId", "name"]) {
    if (key in unit) simulatorOnly.push({ field: key, value: unit[key], source: "optimizer ownUnits" });
  }

  return {
    key: `${index}:${name}`,
    name,
    kind,
    identityStatus: identity.jobName && identity.rank ? "mapped" : "unmapped",
    jobName: identity.jobName,
    rank: identity.rank,
    exactPoint,
    exactParameters,
    controllable,
    derived,
    simulatorOnly,
    unmapped,
    raw: unit,
  };
}

function normalizeIdentity(unit: RecordValue): { jobName: string | null; rank: string | null; unmapped: OptimizerTargetUnit["unmapped"] } {
  const jobName = stringOrNull(unit.jobName);
  const rank = stringOrNull(unit.rank);
  if (jobName && rank) return { jobName, rank, unmapped: [] };
  return {
    jobName,
    rank,
    unmapped: [{
      field: "characterIdentity",
      value: { jobName: unit.jobName ?? null, rank: unit.rank ?? null, jobId: unit.jobId ?? null, name: unit.name ?? null },
      reason: "A candidate display label does not prove a character, job, or rank identity.",
    }],
  };
}

function record(value: unknown): RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as RecordValue : {};
}

function integerOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function stringOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}
