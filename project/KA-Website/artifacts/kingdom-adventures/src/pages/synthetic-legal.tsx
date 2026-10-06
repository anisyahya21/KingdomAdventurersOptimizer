import { useEffect, useMemo, useState, type ChangeEvent, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { AlertTriangle, FileUp, Loader2, Plus, RefreshCw, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EquipmentSearchSelect, type EquipmentSearchOption } from "@/components/ka/equipment-search-select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ka/page-header";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { apiUrl } from "@/lib/api";
import { ENCOUNTER_VARIANTS, renderSkillName, SKILL_BY_ID, skillActivationStatus, type EquipmentSlotKey } from "@/lib/battle-setup";
import {
  type SavedLoadout,
  type SharedLoadoutData,
} from "@/lib/battle-legality";
import { STAT_KEYS } from "@/game-data/stat-parameter-ids";
import { RESIDENT_STAT_ITEMS } from "@/game-data/resident-stat-items";
import { draftStatRows, gearSlotForName, normalizeSavedLoadouts, setGearInSlot } from "@/lib/battle-team-draft";
import {
  getOptimizerCandidates,
  type QualifiedOptimizerEvidence,
  getOptimizerLibrary,
  importOptimizerLibrary,
  type OptimizerCandidatePage,
  type OptimizerCandidateRecord,
  type OptimizerLibraryInfo,
} from "@/lib/optimizer-library-client";
import { normalizeOptimizerTarget } from "@/lib/synthetic-legal-target";
import { blocksExactTargetClaim, searchLegalBuild, type LegalBuildSearchRequest, type LegalBuildSearchResult } from "@/lib/synthetic-legal-engine";
import {
  createEmptyPlayerProfile,
  DEFAULT_SOLVER_SETTINGS,
  normalizePlayerProfile,
  PLAYER_PROFILE_SCHEMA,
  type BuildLock,
  type PlayerCharacter,
  type PlayerProfile,
  type OptimizerTargetUnit,
  type SolverSettings,
} from "@/lib/synthetic-legal-model";
import { planEncounterCoverage } from "@/lib/synthetic-legal-coverage";
import { ensureEquipmentCatalog, SYNTHETIC_LEGAL_EQUIPMENT_CATALOG } from "@/lib/synthetic-legal-inventory";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { verifyPreparedBuilds } from "@/lib/synthetic-legal-runtime";
import { isSyntheticLegalDesktop, syntheticLegalDesktopApi, type SyntheticLegalDesktopRecentLibrary } from "@/lib/synthetic-legal-desktop";

const PROFILE_KEY = "ka_synthetic_legal_profile_v1";
const SETTINGS_KEY = "ka_synthetic_legal_settings_v1";
const SESSION_KEY = "ka_synthetic_legal_library_session_v1";
const EQUIPMENT_SLOTS: Array<{ key: EquipmentSlotKey; label: string }> = [
  { key: "head", label: "Head" },
  { key: "weapon", label: "Weapon" },
  { key: "shield", label: "Shield" },
  { key: "body", label: "Body" },
  { key: "accessory", label: "Accessory" },
];
const BATTLE_SKILL_NAMES = new Set([...SKILL_BY_ID.values()]
  .filter((entry) => skillActivationStatus(entry.id) === "active")
  .map(renderSkillName));
const WATER_KEY_BY_STAT: Partial<Record<(typeof STAT_KEYS)[number], string>> = {
  hp: "life", mp: "wisdom", vig: "vitality", atk: "might", def: "resilience",
};

type PreviewReport = {
  status: "verified" | "mismatch" | "failed";
  message: string;
  stats: Record<string, { target: number; actual: number | null; delta: number | null }>;
  cells: string[];
  warnings: string[];
};

export default function SyntheticLegalPage() {
  const [shared, setShared] = useState<SharedLoadoutData | null>(null);
  const [sharedError, setSharedError] = useState("");
  const [profile, setProfile] = useState<PlayerProfile>(() => ensureEquipmentCatalog(readProfile()));
  const [settings, setSettings] = useState<SolverSettings>(() => readSettings());
  const [library, setLibrary] = useState<{ id: string; info: OptimizerLibraryInfo; note: string; path?: string } | null>(null);
  const [libraryError, setLibraryError] = useState("");
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [desktopApp, setDesktopApp] = useState(isSyntheticLegalDesktop());
  const [desktopBridgeReady, setDesktopBridgeReady] = useState(Boolean(syntheticLegalDesktopApi()));
  const [recentLibraries, setRecentLibraries] = useState<SyntheticLegalDesktopRecentLibrary[]>([]);
  const [mode, setMode] = useState<"single" | "coverage" | "profile">("single");
  const [encounterId, setEncounterId] = useState<number | null>(null);
  const [candidatePage, setCandidatePage] = useState<OptimizerCandidatePage | null>(null);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidateId, setCandidateId] = useState("");
  const [candidateError, setCandidateError] = useState("");
  const [unitIndex, setUnitIndex] = useState(0);
  const [characterId, setCharacterId] = useState("");
  const [jobName, setJobName] = useState("");
  const [rank, setRank] = useState("");
  const [locks, setLocks] = useState<BuildLock[]>([]);
  const [search, setSearch] = useState<LegalBuildSearchResult | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [solverActivated, setSolverActivated] = useState(false);
  const [searchTrigger, setSearchTrigger] = useState(0);
  const [preview, setPreview] = useState<PreviewReport | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [profilePresets, setProfilePresets] = useState<SavedLoadout[]>([]);
  const [presetIndex, setPresetIndex] = useState("");
  const [coverageSelected, setCoverageSelected] = useState<number[]>([]);
  const [coverageCharacterBudget, setCoverageCharacterBudget] = useState(1);
  const [coverageCandidateIds, setCoverageCandidateIds] = useState<Record<number, string>>({});
  const [coverageAssignments, setCoverageAssignments] = useState<Record<string, string>>({});
  const [coverageUnitModes, setCoverageUnitModes] = useState<Record<string, "planned" | "available">>({});
  const [coveragePlan, setCoveragePlan] = useState<ReturnType<typeof planEncounterCoverage> | null>(null);
  const [coverageBusy, setCoverageBusy] = useState(false);
  const [coverageError, setCoverageError] = useState("");
  const [coveragePages, setCoveragePages] = useState<Record<number, OptimizerCandidatePage>>({});
  const [coverageOffsets, setCoverageOffsets] = useState<Record<number, number>>({});

  useEffect(() => {
    const refreshDesktopState = () => {
      const desktop = syntheticLegalDesktopApi();
      setDesktopApp(isSyntheticLegalDesktop());
      setDesktopBridgeReady(Boolean(desktop));
      if (!desktop) return;
      desktop.list_recent_optimizer_libraries().then((response) => {
        if (response.ok === true && Array.isArray(response.libraries)) {
          setRecentLibraries(response.libraries.filter((entry): entry is SyntheticLegalDesktopRecentLibrary =>
            typeof entry === "object" && entry !== null &&
            typeof (entry as SyntheticLegalDesktopRecentLibrary).path === "string" &&
            typeof (entry as SyntheticLegalDesktopRecentLibrary).filename === "string"));
        }
      }).catch(() => setRecentLibraries([]));
    };
    refreshDesktopState();
    window.addEventListener("pywebviewready", refreshDesktopState);
    return () => window.removeEventListener("pywebviewready", refreshDesktopState);
  }, []);

  useEffect(() => {
    fetchSharedWithFallback<SharedLoadoutData>(apiUrl("/shared"))
      .then((value) => { setShared(value); setSharedError(""); })
      .catch((error) => setSharedError(error instanceof Error ? error.message : String(error)));
  }, []);

  useEffect(() => {
    try { setProfilePresets(normalizeSavedLoadouts(JSON.parse(localStorage.getItem("ka_loadouts") ?? "[]"))); }
    catch { setProfilePresets([]); }
    const sessionId = isSyntheticLegalDesktop() ? null : sessionStorage.getItem(SESSION_KEY);
    if (sessionId) {
      getOptimizerLibrary(sessionId).then((info) => {
        setLibrary({ id: sessionId, info, note: "Restored local read-only snapshot session." });
        setEncounterId((current) => current ?? info.encounters.find((entry) => entry.candidateCount > 0)?.id ?? null);
      })
        .catch(() => sessionStorage.removeItem(SESSION_KEY));
    }
  }, []);

  useEffect(() => {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ ...profile, schema: PLAYER_PROFILE_SCHEMA, updatedAt: Date.now() }));
  }, [profile]);
  useEffect(() => localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)), [settings]);
  useEffect(() => { setSearch(null); setPreview(null); setCoveragePlan(null); }, [profile, settings]);

  const candidates = candidatePage?.encounterId === encounterId ? candidatePage.candidates : [];
  const candidate = candidates.find((entry) => entry.id === candidateId) ?? null;
  const normalized = useMemo(() => candidate && library
    ? normalizeOptimizerTarget(candidate, { libraryName: library.info.filename, librarySchemaVersion: library.info.librarySchemaVersion })
    : null, [candidate, library]);
  const targetUnit = normalized?.units[unitIndex] ?? null;
  const selectedCharacter = profile.characters.find((entry) => entry.id === characterId) ?? null;
  const activeCharacterIds = profile.characters.filter((entry) => entry.available).map((entry) => entry.id);

  useEffect(() => {
    if (!activeCharacterIds.includes(characterId)) setCharacterId(activeCharacterIds[0] ?? "");
  }, [activeCharacterIds.join("|"), characterId]);

  useEffect(() => {
    if (!selectedCharacter) { setJobName(""); setRank(""); return; }
    const storedJob = selectedCharacter.state.jobName ?? "";
    const storedRanks = Object.keys(shared?.jobs?.[storedJob]?.ranks ?? {});
    setJobName(storedJob);
    setRank(storedRanks.includes(selectedCharacter.state.rank ?? "") ? selectedCharacter.state.rank ?? "" : storedRanks[0] ?? "");
  }, [characterId, selectedCharacter?.state.jobName, selectedCharacter?.state.rank, shared]);

  useEffect(() => {
    if (!library || encounterId === null) { setCandidatePage(null); setCandidateId(""); return; }
    let current = true;
    setCandidateError("");
    setCandidatePage(null);
    getOptimizerCandidates(library.id, encounterId, candidateOffset, 50)
      .then((page) => { if (current) { setCandidatePage(page); setCandidateId((old) => page.candidates.some((item) => item.id === old) ? old : page.candidates[0]?.id ?? ""); } })
      .catch((error) => { if (current) setCandidateError(error instanceof Error ? error.message : String(error)); });
    return () => { current = false; };
  }, [library?.id, encounterId, candidateOffset]);

  useEffect(() => {
    if (!library) return;
    let current = true;
    Promise.all(coverageSelected.map(async (id) => [id, await getOptimizerCandidates(library.id, id, coverageOffsets[id] ?? 0, 50)] as const))
      .then((entries) => { if (current) setCoveragePages(Object.fromEntries(entries)); })
      .catch((error) => { if (current) setCoverageError(error instanceof Error ? error.message : String(error)); });
    return () => { current = false; };
  }, [library?.id, coverageSelected.join("|"), JSON.stringify(coverageOffsets)]);

  const updateCharacter = (id: string, update: (character: PlayerCharacter) => PlayerCharacter) => {
    setProfile((current) => ({
      ...current,
      updatedAt: Date.now(),
      characters: current.characters.map((entry) => entry.id === id ? update(entry) : entry),
    }));
  };

  const updateEquipmentItem = (name: string, update: (item: PlayerProfile["equipment"][number]) => PlayerProfile["equipment"][number]) => {
    setProfile((current) => ({
      ...current,
      updatedAt: Date.now(),
      equipment: current.equipment.map((item) => item.equipmentName === name ? update(item) : item),
    }));
  };

  const updateGlobalCount = (bucket: "active" | "remaining", key: string, value: number | null) => setProfile((current) => ({
    ...current, updatedAt: Date.now(), valuables: { ...current.valuables, [bucket]: { ...current.valuables[bucket], [key]: value } },
  }));

  const setEquipmentOwnership = (name: string, ownership: "owned" | "unowned" | "unknown") => updateEquipmentItem(name, (item) => ({
    ...item,
    ownership,
    quantity: ownership === "owned" ? Math.max(1, item.quantity) : 0,
  }));

  const attachLibrary = (imported: Awaited<ReturnType<typeof importOptimizerLibrary>>) => {
    if (!imported) return;
    if (!isSyntheticLegalDesktop()) sessionStorage.setItem(SESSION_KEY, imported.libraryId);
    setLibrary({ id: imported.libraryId, info: imported.library, note: imported.note,
      path: "libraryPath" in imported && typeof imported.libraryPath === "string" ? imported.libraryPath : undefined });
    const firstEncounter = imported.library.encounters.find((entry) => entry.candidateCount > 0)?.id;
    if (firstEncounter !== undefined) setEncounterId(firstEncounter);
    setSearch(null); setPreview(null); setCoveragePlan(null); setCandidateOffset(0); setCoverageOffsets({});
    setCoverageSelected([]); setCoverageCandidateIds({}); setCoverageAssignments({}); setCoveragePages({});
  };

  const onImportLibrary = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.currentTarget.files;
    if (!files?.length) return;
    setLibraryBusy(true);
    setLibraryError("");
    setLibrary(null);
    try {
      attachLibrary(await importOptimizerLibrary(files));
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : String(error));
    } finally {
      setLibraryBusy(false);
      event.currentTarget.value = "";
    }
  };

  const onOpenDesktopLibrary = async (path?: string) => {
    setLibraryBusy(true);
    setLibraryError("");
    setLibrary(null);
    try {
      attachLibrary(await importOptimizerLibrary(undefined, path));
      const desktop = syntheticLegalDesktopApi();
      if (desktop) {
        const response = await desktop.list_recent_optimizer_libraries();
        if (response.ok === true && Array.isArray(response.libraries)) {
          setRecentLibraries(response.libraries.filter((entry): entry is SyntheticLegalDesktopRecentLibrary =>
            typeof entry === "object" && entry !== null &&
            typeof (entry as SyntheticLegalDesktopRecentLibrary).path === "string" &&
            typeof (entry as SyntheticLegalDesktopRecentLibrary).filename === "string"));
        }
      }
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : String(error));
    } finally {
      setLibraryBusy(false);
    }
  };

  const anchorRequest = (): LegalBuildSearchRequest | null => {
    if (!library || !shared || !normalized || !targetUnit || !selectedCharacter || !jobName || !rank || encounterId === null) return null;
    return {
      profile,
      characterId,
      jobName,
      rank,
      target: targetUnit,
      evidence: normalized.evidence,
      shared,
      settings,
      locks: [{ field: "jobName", value: jobName }, { field: "rank", value: rank }, ...locks],
      encounterId,
      collectFeasibleOptions: true,
    };
  };

  useEffect(() => {
    if (!solverActivated || mode !== "single") return;
    const request = anchorRequest();
    if (!request) { setSearch(null); setSearchBusy(false); return; }
    setSearch(null); setPreview(null); setSearchBusy(true); setCandidateError("");
    const timer = window.setTimeout(() => {
      try { setSearch(searchLegalBuild(request)); }
      catch (error) { setCandidateError(error instanceof Error ? error.message : String(error)); }
      finally { setSearchBusy(false); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [solverActivated, searchTrigger, mode, profile, settings, characterId, jobName, rank, locks, normalized, targetUnit, encounterId, shared]);

  useEffect(() => {
    const result = search?.result;
    if (!result?.solverExact || !shared || encounterId === null || result.runtimeVerification?.status !== "failed" || !result.runtimeVerification.message.includes("has not completed")) return;
    if (normalized?.encounterId !== encounterId || targetUnit?.kind !== "human") {
      const message = "The imported candidate encounter does not match the selected site encounter, so runtime exactness cannot be verified.";
      setPreview({ status: "failed", message, stats: {}, cells: [], warnings: [] });
      setSearch((old) => old?.result?.savedLoadout === result.savedLoadout ? { ...old, result: { ...old.result!, exact: false, status: "runtime-failed", runtimeVerification: { status: "failed", message } } } : old);
      return;
    }
    let current = true;
    setPreviewBusy(true);
    void verifyPreparedBuilds([result.savedLoadout], [result.target], shared, encounterId).then((check) => {
      if (!current) return;
      const unit = check.units[0];
      const previewReport: PreviewReport = {
        status: check.status,
        message: check.message,
        stats: unit?.stats ?? {},
        cells: check.cells,
        warnings: check.warnings,
      };
      setPreview(previewReport);
      setSearch((old) => {
        if (!old?.result || old.result.savedLoadout !== result.savedLoadout) return old;
        const eligible = old.result.status === "exact-owned-now" || old.result.status === "exact-owned-upgrades";
        return { ...old, result: {
          ...old.result,
          exact: check.status === "verified" && eligible && old.result.unsupportedFields.length === 0,
          status: check.status === "mismatch" ? "runtime-mismatch" : check.status === "failed" ? "runtime-failed" : old.result.status,
          runtimeVerification: { status: check.status, message: check.message },
          ...(unit ? { achieved: Object.fromEntries(Object.entries(unit.stats).flatMap(([stat, value]) => value.actual === null ? [] : [[stat, value.actual]])),
            deltas: Object.fromEntries(Object.entries(unit.stats).flatMap(([stat, value]) => value.delta === null ? [] : [[stat, value.delta]])) } : {}),
        } };
      });
    }).finally(() => { if (current) setPreviewBusy(false); });
    return () => { current = false; };
  }, [search?.result?.savedLoadout, shared, encounterId, normalized?.encounterId, targetUnit?.kind]);

  const runSingleSearch = () => {
    const request = anchorRequest();
    if (!request) { setCandidateError("Choose a library candidate, target unit, profile character, job and rank first."); return; }
    setSolverActivated(true);
    setSearchTrigger((current) => current + 1);
  };

  const setLock = (field: string, value: unknown) => {
    setLocks((current) => [...current.filter((entry) => entry.field !== field), { field, value }]);
    setSearch(null); setPreview(null);
  };
  const clearLock = (field: string) => {
    setLocks((current) => current.filter((entry) => entry.field !== field));
    setSearch(null); setPreview(null);
  };
  const lockValue = (field: string): unknown => locks.find((entry) => entry.field === field)?.value;

  const createProfileCharacter = () => {
    const state: SavedLoadout = { id: crypto.randomUUID(), name: "Resident", jobName: "", rank: "", awakening: 0, statLevels: {}, equipment: [], skills: [] };
    setProfile((current) => ({ ...current, updatedAt: Date.now(), characters: [...current.characters, {
      id: state.id!, available: false, state, jobRanks: [], trainingCaps: {},
    }] }));
  };

  const addPresetCharacter = () => {
    const preset = profilePresets[Number(presetIndex)];
    if (!preset) return;
    const id = crypto.randomUUID();
    const state = { ...preset, id, name: preset.name || "Resident", equipment: [...(preset.equipment ?? [])], skills: [...(preset.skills ?? [])] };
    const jobRanks = state.jobName && state.rank ? [{ jobName: state.jobName, ranks: [state.rank] }] : [];
    setProfile((current) => ({ ...current, updatedAt: Date.now(), characters: [...current.characters, {
      id, available: false, state, jobRanks, trainingCaps: {},
    }] }));
    setPresetIndex("");
  };

  const runCoverage = async () => {
    setCoverageBusy(true); setCoverageError(""); setCoveragePlan(null);
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    try {
      if (!library || !shared) throw new Error("Load a library and shared game data first.");
      const options = [] as Array<Parameters<typeof planEncounterCoverage>[0]["options"][number]>;
      for (const selectedEncounter of coverageSelected) {
        const page = coveragePages[selectedEncounter];
        const selectedId = coverageCandidateIds[selectedEncounter] || page?.candidates[0]?.id;
        const selectedCandidate = page?.candidates.find((entry) => entry.id === selectedId);
        if (!selectedCandidate) continue;
        const normalizedCandidate = normalizeOptimizerTarget(selectedCandidate, {
          libraryName: library.info.filename, librarySchemaVersion: library.info.librarySchemaVersion,
        });
        const baseUnsupported = normalizedCandidate.unmapped
          .filter((entry) => entry.field !== "characterIdentity" && entry.field !== "jobName/rank")
          .map((entry) => `${entry.field}: ${entry.reason}`);
        if (normalizedCandidate.units.length === 0) baseUnsupported.push("This candidate has no mapped player-side units to plan.");
        const planRows: Array<{ unit: OptimizerTargetUnit; status: "planned" | "player-declared-available"; candidates: Array<{ characterId: string; result: NonNullable<ReturnType<typeof searchLegalBuild>["result"]> }> }> = [];
        const forcedFailures: string[] = [];

        for (const unit of normalizedCandidate.units) {
          const key = `${selectedEncounter}:${selectedCandidate.id}:${unit.key}`;
          if ((coverageUnitModes[key] ?? "planned") === "available") {
            planRows.push({ unit, status: "player-declared-available", candidates: [] });
            continue;
          }
          if (unit.kind !== "human") {
            forcedFailures.push(`${unit.name}: planned ${unit.kind} units are not supported by this resident build flow.`);
            planRows.push({ unit, status: "planned", candidates: [] });
            continue;
          }
          const forcedCharacterId = coverageAssignments[key];
          const characters = profile.characters.filter((entry) => entry.available && (!forcedCharacterId || entry.id === forcedCharacterId));
          const candidates: Array<{ characterId: string; result: NonNullable<ReturnType<typeof searchLegalBuild>["result"]> }> = [];
          for (const character of characters) {
            const selectedJob = character.state.jobName ?? "";
            const selectedRank = character.state.rank ?? "";
            if (!selectedJob || !selectedRank) continue;
            const found = searchLegalBuild({ profile, characterId: character.id, jobName: selectedJob, rank: selectedRank,
              target: unit, evidence: normalizedCandidate.evidence, shared, settings, encounterId: selectedEncounter });
            if (found.result) candidates.push({ characterId: character.id, result: found.result });
          }
          if (candidates.length === 0) forcedFailures.push(`${unit.name}: no available resident produced a build result for its current Job and Rank.`);
          planRows.push({ unit, status: "planned", candidates });
        }

        const plannedRows = planRows.filter((entry) => entry.status === "planned");
        const combinations: Array<Array<{ unitKey: string; unitName: string; characterId: string; result: NonNullable<ReturnType<typeof searchLegalBuild>["result"]> }>> = [];
        const chosen: typeof combinations[number] = [];
        const combine = (index: number) => {
          if (combinations.length >= 32) return;
          if (index >= plannedRows.length) { combinations.push([...chosen]); return; }
          const row = plannedRows[index];
          for (const candidateBuild of row.candidates) {
            if (chosen.some((entry) => entry.characterId === candidateBuild.characterId)) continue;
            chosen.push({ unitKey: row.unit.key, unitName: row.unit.name, ...candidateBuild });
            combine(index + 1);
            chosen.pop();
            if (combinations.length >= 32) return;
          }
        };
        if (forcedFailures.length === 0) combine(0);
        if (combinations.length === 0) combinations.push([]);

        for (const combination of combinations) {
          const results = combination.map((entry) => entry.result);
          const characterIds = combination.map((entry) => entry.characterId);
          const unsupportedRequirements = [...baseUnsupported, ...forcedFailures];
          const staticExact = forcedFailures.length === 0 && baseUnsupported.length === 0 && combination.length === plannedRows.length &&
            results.every((result) => result.solverExact && ["exact-owned-now", "exact-owned-upgrades"].includes(result.status) &&
              result.unsupportedFields.length === 0 && !result.legalityIssues.some(blocksExactTargetClaim));
          if (staticExact && results.length > 0) {
            const runtime = await verifyPreparedBuilds(results.map((result) => result.savedLoadout), results.map((result) => result.target), shared, selectedEncounter);
            results.forEach((result, index) => {
              const check = runtime.units[index];
              result.runtimeVerification = { status: runtime.status, message: runtime.message };
              result.exact = runtime.status === "verified" && check?.matched === true;
              if (check) {
                result.achieved = Object.fromEntries(Object.entries(check.stats).flatMap(([stat, value]) => value.actual === null ? [] : [[stat, value.actual]]));
                result.deltas = Object.fromEntries(Object.entries(check.stats).flatMap(([stat, value]) => value.delta === null ? [] : [[stat, value.delta]]));
              }
            });
            if (runtime.status !== "verified" || runtime.units.some((unit) => !unit.matched)) unsupportedRequirements.push(`Authoritative runtime preparation ${runtime.status}: ${runtime.message}`);
          } else {
            for (const entry of combination) if (!entry.result.solverExact || entry.result.unsupportedFields.length || entry.result.legalityIssues.some(blocksExactTargetClaim)) {
              unsupportedRequirements.push(`${entry.unitName}: ${entry.result.legalityIssues.join("; ") || "no complete exact legal build"}`);
            }
          }
          const unitPlans = planRows.map((row) => {
            const build = combination.find((entry) => entry.unitKey === row.unit.key);
            return { unitKey: row.unit.key, name: row.unit.name, status: row.status, ...(build ? { characterId: build.characterId } : {}) };
          });
          options.push({
            encounterId: selectedEncounter,
            candidateId: selectedCandidate.id,
            evidence: normalizedCandidate.evidence,
            requiredUnitCount: normalizedCandidate.units.length,
            plannedUnitCount: plannedRows.length,
            declaredAvailableUnitCount: planRows.length - plannedRows.length,
            unitPlans,
            requiredCharacterIds: characterIds,
            results,
            allRequiredUnitsExact: unsupportedRequirements.length === 0 && combination.length === plannedRows.length && results.every((result) => result.exact),
            unsupportedRequirements,
          });
        }
      }
      let plan = planEncounterCoverage({ selectedEncounterIds: coverageSelected, characterBudget: coverageCharacterBudget,
        options, profile, shared, settings });
      const invalidFinals = new Set<string>();
      for (const choice of plan.choices) {
        if (choice.results.length === 0) continue;
        const runtime = await verifyPreparedBuilds(choice.results.map((result) => result.savedLoadout), choice.results.map((result) => result.target), shared, choice.encounterId);
        const optionKey = `${choice.encounterId}:${choice.candidateId}`;
        if (runtime.status !== "verified" || runtime.units.some((unit) => !unit.matched)) {
          invalidFinals.add(optionKey);
          const option = options.find((entry) => `${entry.encounterId}:${entry.candidateId}` === optionKey);
          if (option) {
            option.allRequiredUnitsExact = false;
            option.unsupportedRequirements = [...option.unsupportedRequirements, `Final shared-upgrade runtime check ${runtime.status}: ${runtime.message}`];
            option.results.forEach((result) => { result.exact = false; result.runtimeVerification = { status: runtime.status, message: runtime.message }; });
          }
          continue;
        }
        choice.results.forEach((result, index) => {
          const unit = runtime.units[index];
          result.runtimeVerification = { status: "verified", message: runtime.message };
          result.exact = unit?.matched === true;
          if (unit) {
            result.achieved = Object.fromEntries(Object.entries(unit.stats).flatMap(([stat, value]) => value.actual === null ? [] : [[stat, value.actual]]));
            result.deltas = Object.fromEntries(Object.entries(unit.stats).flatMap(([stat, value]) => value.delta === null ? [] : [[stat, value.delta]]));
          }
        });
      }
      if (invalidFinals.size > 0) plan = planEncounterCoverage({ selectedEncounterIds: coverageSelected, characterBudget: coverageCharacterBudget,
        options, profile, shared, settings });
      setCoveragePlan(plan);
    } catch (error) { setCoverageError(error instanceof Error ? error.message : String(error)); }
    finally { setCoverageBusy(false); }
  };

  const toggleCoverageEncounter = (id: number, checked: boolean) => {
    setCoverageSelected((current) => checked ? [...new Set([...current, id])].sort((a, b) => a - b) : current.filter((entry) => entry !== id));
    setCoveragePlan(null);
  };

  const jobOptions = Object.keys(shared?.jobs ?? {}).sort().map((name) => ({ jobName: name, ranks: Object.keys(shared?.jobs?.[name]?.ranks ?? {}) }));
  const rankOptions = jobOptions.find((entry) => entry.jobName === jobName)?.ranks ?? [];
  const candidateEvidence = normalized?.evidence;
  const currentLoadout = selectedCharacter && shared
    ? loadoutWithLocks(selectedCharacter.state, profile, jobName, rank, locks, shared)
    : null;
  const currentStatRows = currentLoadout ? draftStatRows(currentLoadout, shared) : [];
  const currentByStat = new Map(currentStatRows.map((row) => [row.stat, row]));
  const statComparison = targetUnit ? STAT_KEYS.map((stat) => {
    const target = targetUnit.exactPoint[stat];
    const row = currentByStat.get(stat);
    const waterKey = WATER_KEY_BY_STAT[stat];
    const waterMissing = waterKey !== undefined && profile.valuables.active[waterKey] == null;
    const current = !selectedCharacter || !jobName || !rank || !shared || waterMissing ? null : row?.total ?? null;
    return { stat, target: typeof target === "number" ? target : null, current, delta: typeof target === "number" && current !== null ? current - target : null };
  }) : [];
  const mappedComparison = statComparison.filter((row) => row.target !== null);
  const exactCount = mappedComparison.filter((row) => row.delta === 0).length;
  const lowCount = mappedComparison.filter((row) => row.delta !== null && row.delta < 0).length;
  const highCount = mappedComparison.filter((row) => row.delta !== null && row.delta > 0).length;
  const missingWaterNames = targetUnit ? [...new Set(mappedComparison.flatMap((row) => {
    const key = WATER_KEY_BY_STAT[row.stat as (typeof STAT_KEYS)[number]];
    return key && profile.valuables.active[key] == null ? [RESIDENT_STAT_ITEMS.find((item) => item.key === key)?.name ?? key] : [];
  }))] : [];
  const nextStep = !library ? "Open an optimizer library to choose a recorded strategy."
    : !targetUnit ? "Choose an optimizer candidate and target unit."
      : targetUnit.kind !== "human" ? "Choose a resident human target; this screen builds resident loadouts."
        : !selectedCharacter ? "Choose one of your available characters."
          : !jobName || !rank ? "Choose the Job and Rank this character should use."
            : missingWaterNames.length > 0 ? `Set the active counts for ${missingWaterNames.join(", ")} in Player Profile and Settings.`
              : !search ? "Run an exact-build search to find a legal match and choices that can still reach it."
                : search.result?.exact ? "The recorded target has been reproduced and runtime checked. You can use the build or adjust it manually."
                  : search.exactFeasible === true ? "Use the highlighted exact-compatible choices, or let the recommended build fill every slot."
                    : search.exactFeasible === false && search.complete ? "No exact build exists under the current locks. Review the closest result and clear a lock to try again."
                      : "Search stopped before exact feasibility was known. Increase the search limit or adjust the current locks.";

  return (
    <div className="container mx-auto max-w-6xl px-4 py-6 space-y-5">
      <PageHeader title="Synthetic to Legal Builds"><p>Turn an exact optimizer point into a legal encounter build, using your shared inventory and reusable character roster.</p></PageHeader>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Optimizer library</CardTitle>
          <CardDescription>{desktopApp
            ? "Open an existing SQLite library from anywhere on this PC. The local read-only bridge opens the selected path directly."
            : "Choose an Optimizer SQLite library. The browser development fallback sends it to a temporary local snapshot for read-only browsing."}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            {desktopApp ? <Button type="button" variant="outline" onClick={() => void onOpenDesktopLibrary()} disabled={libraryBusy || !desktopBridgeReady}>
              {libraryBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileUp className="mr-2 h-4 w-4" />}
              {libraryBusy ? "Opening library…" : desktopBridgeReady ? "Open Library" : "Starting desktop…"}
            </Button> : <Label className="inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted">
              {libraryBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
              {libraryBusy ? "Importing snapshot…" : "Choose SQLite library"}
              <input className="sr-only" type="file" accept=".sqlite,.sqlite3,.db,.sqlite-wal,.sqlite-shm,.sqlite-journal" multiple onChange={onImportLibrary} disabled={libraryBusy} />
            </Label>}
            {library && <span className="text-sm text-muted-foreground">{library.info.filename} · schema {library.info.librarySchemaVersion} · {library.info.candidateCount.toLocaleString()} candidates</span>}
          </div>
          {desktopApp && recentLibraries.length > 0 && <div className="flex flex-wrap items-center gap-2" aria-label="Recently opened libraries">
            <span className="text-xs text-muted-foreground">Recent:</span>
            {recentLibraries.map((entry) => <Button key={entry.path} type="button" size="sm" variant="ghost"
              title={entry.path} onClick={() => void onOpenDesktopLibrary(entry.path)} disabled={libraryBusy || !desktopBridgeReady}>{entry.filename}</Button>)}
          </div>}
          {library?.path && <p className="break-all font-mono text-xs text-muted-foreground">{library.path}</p>}
          <p className="text-xs text-muted-foreground">{desktopApp
            ? "The selected database stays at its original location and is read in place. SQLite WAL files beside it are read by SQLite directly; no database copy is made."
            : "Web-only fallback: the local API creates a temporary snapshot. For a WAL database, include its adjacent -wal and -shm files. The original files are not modified; npm run api:serve must be running."}</p>
          {library?.note && <p className="text-xs text-muted-foreground">{library.note}</p>}
          {libraryError && <Notice tone="error">{libraryError}</Notice>}
          {sharedError && <Notice tone="error">Shared game data could not load: {sharedError}</Notice>}
          {!shared && !sharedError && <p className="text-sm text-muted-foreground">Loading canonical game data…</p>}
        </CardContent>
      </Card>

      <GlobalValuablesEditor profile={profile} updateGlobalCount={updateGlobalCount} />

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Synthetic to legal modes">
        <ModeButton active={mode === "single"} onClick={() => setMode("single")}>Single encounter</ModeButton>
        <ModeButton active={mode === "coverage"} onClick={() => setMode("coverage")}>Coverage planner</ModeButton>
        <ModeButton active={mode === "profile"} onClick={() => setMode("profile")}>Player profile and settings</ModeButton>
      </div>

      {mode === "profile" && <>
        <ProfileEditor
          profile={profile} setProfile={setProfile} shared={shared} presets={profilePresets}
          presetIndex={presetIndex} setPresetIndex={setPresetIndex} addPresetCharacter={addPresetCharacter}
          createCharacter={createProfileCharacter} updateCharacter={updateCharacter}
          updateEquipmentItem={updateEquipmentItem} setEquipmentOwnership={setEquipmentOwnership}
        />
        <SettingsEditor settings={settings} setSettings={setSettings} />
      </>}

      {mode === "single" && <>
        {!library && <Card><CardContent className="pt-5 text-sm text-muted-foreground">Import an optimizer library to browse recorded encounter candidates.</CardContent></Card>}
        {library && <Card>
          <CardHeader className="pb-3"><CardTitle className="text-lg">Choose the recorded target</CardTitle><CardDescription>Each candidate is a recorded point. Stat distance is only an approximation measure; it does not show combat equivalence.</CardDescription></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-3">
            <Field label="Encounter">
                <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={encounterId ?? ""} onChange={(event) => { setEncounterId(event.target.value ? Number(event.target.value) : null); setCandidateOffset(0); setSearch(null); setLocks([]); }}>
                <option value="">Choose encounter</option>
                {library.info.encounters.map((entry) => {
                  const encounter = ENCOUNTER_VARIANTS.find((row) => row.id === entry.id);
                  return encounter ? <option key={entry.id} value={entry.id}>{encounter.title} · {entry.candidateCount} candidates</option> : null;
                })}
              </select>
            </Field>
            <Field label="Candidate / strategy">
              <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={candidateId} onChange={(event) => { setCandidateId(event.target.value); setUnitIndex(0); setSearch(null); setLocks([]); }} disabled={!candidatePage}>
                {candidates.map((entry) => <option key={entry.id} value={entry.id}>{entry.label ?? entry.id.slice(0, 12)} · history mean {displayNumber(entry.performance.meanEarned)} · high {displayNumber(entry.performance.highestEarned)} · n={entry.performance.sampleCount}</option>)}
              </select>
            </Field>
            <Field label="Optimizer player unit">
              <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={unitIndex} onChange={(event) => { setUnitIndex(Number(event.target.value)); setSearch(null); }} disabled={!normalized}>
                {normalized?.units.map((unit, index) => <option key={unit.key} value={index}>{index + 1}. {unit.name} · {unit.kind} · {Object.keys(unit.exactPoint).length} mapped stats</option>)}
              </select>
            </Field>
          </CardContent>
          <CardContent className="border-t pt-4">
            {candidatePage && candidatePage.encounterId === encounterId && candidatePage.total > candidatePage.limit && <div className="mb-3 flex items-center gap-2 text-sm">
              <Button variant="outline" size="sm" disabled={candidateOffset <= 0} onClick={() => setCandidateOffset(Math.max(0, candidateOffset - candidatePage.limit))}>Previous candidates</Button>
              <span className="text-muted-foreground">{candidateOffset + 1}–{Math.min(candidateOffset + candidatePage.candidates.length, candidatePage.total)} of {candidatePage.total}</span>
              <Button variant="outline" size="sm" disabled={candidateOffset + candidatePage.limit >= candidatePage.total} onClick={() => setCandidateOffset(candidateOffset + candidatePage.limit)}>Next candidates</Button>
            </div>}
            {candidateEvidence && <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-sm">
              <span>Historical mean earned: <strong>{displayNumber(candidateEvidence.meanEarned)}</strong></span>
              <span>Highest earned: <strong>{displayNumber(candidateEvidence.highestEarned)}</strong></span>
              <span>Samples: <strong>{candidateEvidence.sampleCount}</strong></span>
              <span>Uncertainty: <strong>{displayNumber(candidateEvidence.uncertainty.value)} {candidateEvidence.uncertainty.kind}</strong></span>
              <span>Source/family: <strong>{candidateEvidence.source ?? "unknown"} / {candidateEvidence.family ?? "unknown"}</strong></span>
            </div>}
            {candidate?.qualifiedEvidence && <QualifiedEvidence rows={candidate.qualifiedEvidence} build={candidate.buildExpressibility} />}
            {targetUnit && <div className="space-y-3">
              {normalized && normalized.encounterId === null && <Notice tone="warning">The SQLite encounter index and scenario ID do not both match a current site encounter. This target cannot be sent to runtime preview as validated coverage.</Notice>}
              {targetUnit.kind !== "human" && <Notice tone="warning">This optimizer unit is not identified as a resident human. The resident SavedLoadout translator cannot validate it; its target and raw source fields remain available for review.</Notice>}
              {normalized && normalized.units.length > 1 && <Notice tone="warning">This candidate contains {normalized.units.length} player-side units. The current single view translates only the selected unit; it does not claim the whole candidate is reproduced.</Notice>}
              {targetUnit.identityStatus === "unmapped" && <p className="rounded-md border bg-muted/40 px-3 py-2 text-sm">The optimizer target records effective stats but does not identify a canonical game Job or Rank. Choose the character and proposed build Job and Rank.</p>}
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Character">
                  <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={characterId} onChange={(event) => { setCharacterId(event.target.value); setSearch(null); }}>
                    <option value="">Choose a character</option>
                    {profile.characters.filter((entry) => entry.available).map((entry) => <option key={entry.id} value={entry.id}>{entry.state.name || entry.id}</option>)}
                  </select>
                </Field>
                {selectedCharacter && jobOptions.length > 0 && <Field label="Build Job"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={jobName} onChange={(event) => {
                  const nextJob = event.target.value;
                  const ranks = Object.keys(shared?.jobs?.[nextJob]?.ranks ?? {});
                  setJobName(nextJob); setRank(ranks.length === 1 ? ranks[0] : ""); setSearch(null);
                }}>
                  {jobOptions.map((entry) => <option key={entry.jobName} value={entry.jobName}>{entry.jobName}</option>)}
                </select></Field>}
                {selectedCharacter && rankOptions.length > 0 && <Field label="Build Rank"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={rank} onChange={(event) => { setRank(event.target.value); setSearch(null); }}>
                  {rankOptions.map((entry) => <option key={entry} value={entry}>{entry}</option>)}
                </select></Field>}
              </div>
              {selectedCharacter && jobName && rank && <p className="text-xs text-muted-foreground">Using {selectedCharacter.state.name || "selected character"} · {jobName} · Rank {rank}</p>}
              {!selectedCharacter && <p className="text-sm text-muted-foreground">Next step: choose an available character. If you have none, add one in Player Profile and Settings.</p>}
              {!selectedCharacter && profile.characters.filter((entry) => entry.available).length === 0 && <Button variant="outline" onClick={() => setMode("profile")}>Open Player Profile to add a character</Button>}
              {selectedCharacter && jobOptions.length === 0 && <div className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm"><span>Canonical Job and Rank data is unavailable.</span><Button variant="outline" size="sm" onClick={() => setMode("profile")}>Open Player Profile</Button></div>}

              <TargetBuildSummary
                rows={statComparison}
                status={overallBuildStatus({ selectedCharacter, jobName, rank, search, exactCount, mappedCount: mappedComparison.length, lowCount, highCount, missingWater: missingWaterNames.length > 0 })}
                exactCount={exactCount}
                mappedCount={mappedComparison.length}
                lowCount={lowCount}
                highCount={highCount}
                exact={search?.result?.exact === true}
                hasBuild={Boolean(selectedCharacter && jobName && rank)}
              />
              {selectedCharacter && targetUnit.kind === "human" && <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-3">
                <div><div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Next step</div><div className="text-sm">{nextStep}</div></div>
                <Button onClick={runSingleSearch} disabled={searchBusy || !shared || !characterId || !jobName || !rank || missingWaterNames.length > 0}>
                  {searchBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                  {solverActivated ? "Search again" : "Find an exact legal build"}
                </Button>
              </div>}
              {missingWaterNames.length > 0 && <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"><span>Set the active Water counts in Player Profile and Settings to calculate the affected stats and run an exact search. Missing: {missingWaterNames.join(", ")}.</span><Button variant="outline" size="sm" onClick={() => setMode("profile")}>Open Player Profile</Button></div>}
              {searchBusy && <div className="text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />Searching legal combinations…</div>}
              {candidateError && <Notice tone="error">{candidateError}</Notice>}
              {search && <SearchResultPanel search={search} settings={settings} locks={locks} profile={profile}
                updateEquipmentItem={updateEquipmentItem} setEquipmentOwnership={setEquipmentOwnership} onUseBuild={(build) => {
                if (!selectedCharacter) return;
                updateCharacter(selectedCharacter.id, (entry) => ({ ...entry, state: { ...build, id: entry.state.id, name: entry.state.name },
                  jobRanks: build.jobName && build.rank ? [{ jobName: build.jobName, ranks: [build.rank] }] : [] }));
                setLocks([]);
              }} />}
              <WaysToReachTarget search={search} locks={locks} profile={profile} shared={shared} setLock={setLock} />
              {selectedCharacter && <details className="rounded-md border p-3">
                <summary className="cursor-pointer select-none font-semibold">Build manually</summary>
                <div className="mt-3"><ManualBuildLocks character={selectedCharacter} profile={profile} target={targetUnit} jobName={jobName} rank={rank} settings={settings} shared={shared} search={search} locks={locks} lockValue={lockValue} setLock={setLock} clearLock={clearLock} /></div>
                {locks.length > 0 && <Button variant="outline" onClick={() => { setLocks([]); setSearch(null); setPreview(null); }}>Clear {locks.length} locks</Button>}
              </details>}
              <details className="rounded-md border p-3">
                <summary className="cursor-pointer select-none text-sm font-semibold">Technical details</summary>
                <div className="mt-3 space-y-3 text-xs text-muted-foreground">
                  {targetUnit.unmapped.length > 0 && <div><strong>Target fields not mapped:</strong>{targetUnit.unmapped.map((entry, index) => <div key={`${entry.field}-${index}`}>{entry.field}: {entry.reason}</div>)}</div>}
                  {normalized?.unmapped.filter((entry) => entry.field !== "characterIdentity" && entry.field !== "jobName/rank").map((entry, index) => <div key={`${entry.field}-${index}`}>{entry.field}: {entry.reason}</div>)}
                  {search && <div>Search examined {search.nodes.toLocaleString()} combinations. {search.complete ? "The declared domain was exhausted." : "The search limit was reached; unlisted choices remain unknown."}</div>}
                  {previewBusy && <div><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Checking authoritative runtime preparation…</div>}
                  {preview && <PreviewPanel preview={preview} />}
                </div>
              </details>
            </div>}
          </CardContent>
        </Card>}
      </>}

      {mode === "coverage" && <>
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-lg">Select encounter families</CardTitle><CardDescription>Choose individual difficulties or select a whole family. For each optimizer unit, plan a reusable character or declare that unit already available; declared units are assumed and are not checked.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {!library && <Notice tone="warning">Import a library to choose its recorded candidates.</Notice>}
            <div className="space-y-3">{[...new Set(ENCOUNTER_VARIANTS.map((entry) => entry.familyId))].map((familyId) => {
              const variants = ENCOUNTER_VARIANTS.filter((entry) => entry.familyId === familyId);
              const presentIds = variants.filter((encounter) => library?.info.encounters.some((item) => item.id === encounter.id && item.candidateCount > 0)).map((entry) => entry.id);
              const selectedCount = presentIds.filter((id) => coverageSelected.includes(id)).length;
              return <section key={familyId} className="rounded-md border p-3">
                <label className="flex min-h-10 items-center gap-2 font-semibold">
                  <input type="checkbox" aria-label={`Select ${variants[0]?.familyName ?? familyId} family`} checked={presentIds.length > 0 && selectedCount === presentIds.length} disabled={presentIds.length === 0} onChange={(event) => {
                    setCoverageSelected((current) => event.target.checked
                      ? [...new Set([...current, ...presentIds])].sort((a, b) => a - b)
                      : current.filter((id) => !presentIds.includes(id)));
                    setCoveragePlan(null);
                  }} />
                  <span>{variants[0]?.familyName ?? familyId}</span><span className="text-xs font-normal text-muted-foreground">{selectedCount}/{presentIds.length} selected</span>
                </label>
                <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{variants.map((encounter) => {
                  const candidateCount = library?.info.encounters.find((item) => item.id === encounter.id)?.candidateCount ?? 0;
                  const present = candidateCount > 0;
                  return <label key={encounter.id} className={`flex min-h-14 items-start gap-2 rounded-md border p-2 text-sm ${present ? "" : "opacity-50"}`}>
                    <input type="checkbox" checked={coverageSelected.includes(encounter.id)} disabled={!present} onChange={(event) => toggleCoverageEncounter(encounter.id, event.target.checked)} />
                    <span>{encounter.difficultyName}<span className="block text-xs text-muted-foreground">{present ? `${candidateCount} candidates` : "No candidate"}</span></span>
                  </label>;
                })}</div>
              </section>;
            })}</div>
            <Field label="MAX CHARACTERS TO BUILD / REUSE">
              <select className="h-9 w-44 rounded-md border bg-background px-2 text-sm" value={coverageCharacterBudget} onChange={(event) => { setCoverageCharacterBudget(Number(event.target.value)); setCoveragePlan(null); }}>
                {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((value) => <option key={value} value={value}>{value} {value === 1 ? "character" : "characters"}</option>)}
              </select>
            </Field>
            <p className="text-xs text-muted-foreground">Only planned residents count toward this cap. Already available units are listed as assumptions and skip build solving, gear allocation, and the character budget.</p>
            {coverageSelected.map((selectedEncounter) => {
              const page = coveragePages[selectedEncounter];
              const id = coverageCandidateIds[selectedEncounter] || page?.candidates[0]?.id || "";
              const selectedCandidate = page?.candidates.find((entry) => entry.id === id);
              const target = selectedCandidate && library ? normalizeOptimizerTarget(selectedCandidate, { libraryName: library.info.filename, librarySchemaVersion: library.info.librarySchemaVersion }) : null;
              return <div key={selectedEncounter} className="rounded-md border p-3 space-y-3">
                <div className="font-semibold">{ENCOUNTER_VARIANTS.find((item) => item.id === selectedEncounter)?.title}</div>
                <Field label="Recorded candidate"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={id} onChange={(event) => { setCoverageCandidateIds((current) => ({ ...current, [selectedEncounter]: event.target.value })); setCoveragePlan(null); }}>
                  {page?.candidates.map((entry) => <option key={entry.id} value={entry.id}>{entry.label ?? entry.id.slice(0, 12)} · history mean {displayNumber(entry.performance.meanEarned)} · high {displayNumber(entry.performance.highestEarned)} · n={entry.performance.sampleCount}</option>)}
                </select></Field>
                {page && page.total > page.limit && <div className="flex items-center gap-2 text-xs">
                  <Button variant="outline" size="sm" disabled={page.offset <= 0} onClick={() => { setCoverageCandidateIds((current) => { const next = { ...current }; delete next[selectedEncounter]; return next; }); setCoverageOffsets((current) => ({ ...current, [selectedEncounter]: Math.max(0, page.offset - page.limit) })); setCoveragePlan(null); }}>Previous</Button>
                  <span className="text-muted-foreground">{page.offset + 1}–{Math.min(page.offset + page.candidates.length, page.total)} of {page.total}</span>
                  <Button variant="outline" size="sm" disabled={page.offset + page.limit >= page.total} onClick={() => { setCoverageCandidateIds((current) => { const next = { ...current }; delete next[selectedEncounter]; return next; }); setCoverageOffsets((current) => ({ ...current, [selectedEncounter]: page.offset + page.limit })); setCoveragePlan(null); }}>Next</Button>
                </div>}
                {target && target.units.map((unit) => {
                  const key = `${selectedEncounter}:${selectedCandidate!.id}:${unit.key}`;
                  const unitMode = coverageUnitModes[key] ?? "planned";
                  return <div key={key} className="grid items-center gap-2 sm:grid-cols-[1fr_1fr]">
                    <span className="text-sm">{unit.name}</span>
                    <div className="grid gap-2 sm:grid-cols-[minmax(150px,1fr)_minmax(150px,1fr)]">
                      <select aria-label={`${unit.name} plan`} className="h-9 rounded-md border bg-background px-2 text-sm" value={unitMode} onChange={(event) => {
                        setCoverageUnitModes((current) => ({ ...current, [key]: event.target.value as "planned" | "available" })); setCoveragePlan(null);
                      }}><option value="planned">PLAN / REUSE</option><option value="available">ALREADY AVAILABLE</option></select>
                      {unitMode === "planned" ? <select aria-label={`${unit.name} character mapping`} className="h-9 rounded-md border bg-background px-2 text-sm" value={coverageAssignments[key] ?? ""} onChange={(event) => { setCoverageAssignments((current) => ({ ...current, [key]: event.target.value })); setCoveragePlan(null); }}>
                        <option value="">Let planner choose</option>
                        {profile.characters.filter((entry) => entry.available).map((entry) => <option key={entry.id} value={entry.id}>{entry.state.name?.trim() || "Unnamed character"} · {entry.state.jobName || "no job"} {entry.state.rank || ""}</option>)}
                      </select> : <span className="self-center text-xs text-amber-800 dark:text-amber-200">Assumed available; not validated</span>}
                    </div>
                  </div>;
                })}
              </div>;
            })}
            <div className="flex flex-wrap gap-3">
              <Button onClick={runCoverage} disabled={coverageBusy || coverageSelected.length === 0 || !shared}>
                {coverageBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}Plan exact coverage
              </Button>
              {coverageSelected.length > 0 && <span className="self-center text-sm text-muted-foreground">Selected: {coverageSelected.length}</span>}
            </div>
            {coverageError && <Notice tone="error">{coverageError}</Notice>}
            {coveragePlan && <CoveragePanel plan={coveragePlan} profile={profile} />}
          </CardContent>
        </Card>
        <SettingsEditor settings={settings} setSettings={setSettings} />
      </>}
    </div>
  );
}

function overallBuildStatus(input: {
  selectedCharacter: PlayerCharacter | null;
  jobName: string;
  rank: string;
  search: LegalBuildSearchResult | null;
  exactCount: number;
  mappedCount: number;
  lowCount: number;
  highCount: number;
  missingWater: boolean;
}): string {
  const { selectedCharacter, jobName, rank, search, exactCount, mappedCount, lowCount, highCount, missingWater } = input;
  if (missingWater) return "BUILD INCOMPLETE · WATER COUNTS MISSING";
  if (!selectedCharacter || !jobName || !rank) return "BUILD INCOMPLETE";
  if (search?.result?.exact && mappedCount > 0 && exactCount === mappedCount) return "EXACT MATCH";
  if (search?.complete && search.exactFeasible === false) return "NO EXACT BUILD FOUND UNDER CURRENT LOCKS";
  if (search?.result?.exact) return "EXACT BUILD READY · APPLY TO PROFILE";
  if (search?.result?.solverExact && !["exact-owned-now", "exact-owned-upgrades", "theoretical-unowned"].includes(search.result.status)) return "TARGET STATS MATCH · LEGALITY REVIEW NEEDED";
  if (search?.result?.solverExact) return "EXACT STAT BUILD FOUND · FINAL CHECK PENDING";
  if (search && search.exactFeasible === null) return "EXACTNESS UNKNOWN";
  if (mappedCount > 0 && exactCount === mappedCount && !search) return "STATS MATCH · LEGALITY NOT CHECKED";
  if (search?.result || (search && search.exactFeasible === false)) return `NOT AN EXACT MATCH · ${exactCount}/${mappedCount} EXACT · ${lowCount} LOW · ${highCount} HIGH`;
  return "BUILD NOT SEARCHED";
}

function loadoutWithLocks(
  source: SavedLoadout,
  profile: PlayerProfile,
  jobName: string,
  rank: string,
  locks: BuildLock[],
  shared: SharedLoadoutData,
): SavedLoadout {
  const lockMap = new Map(locks.map((entry) => [entry.field, entry.value]));
  const capturedValuables = Object.fromEntries(Object.entries(profile.valuables.active).filter((entry): entry is [string, number] => typeof entry[1] === "number"));
  let loadout: SavedLoadout = { ...source, jobName, rank, residentStatItems: capturedValuables };
  for (const slot of EQUIPMENT_SLOTS) {
    const nameField = `equipment.${slot.key}.name`;
    const levelField = `equipment.${slot.key}.level`;
    const lockedName = lockMap.get(nameField);
    const lockedLevel = lockMap.get(levelField);
    if (lockedName === undefined && lockedLevel === undefined) continue;
    const current = loadout.equipment?.find((entry) => gearSlotForName(entry.name, shared.slotAssignments) === slot.key) ?? null;
    if (lockedName === null) {
      loadout = setGearInSlot(loadout, slot.key, null, shared.slotAssignments);
      continue;
    }
    const name = typeof lockedName === "string" ? lockedName : current?.name;
    if (!name) continue;
    const owned = profile.equipment.find((entry) => entry.equipmentName === name && entry.ownership === "owned");
    const knownLevel = Math.max(current?.name === name ? current.level : 1, owned?.currentLevel ?? 1);
    const level = typeof lockedLevel === "number" ? lockedLevel : knownLevel;
    loadout = setGearInSlot(loadout, slot.key, { name, level }, shared.slotAssignments);
  }
  const statLevels = { ...(loadout.statLevels ?? {}) };
  for (const stat of STAT_KEYS) {
    const level = lockMap.get(`statLevels.${stat}`);
    if (typeof level === "number") statLevels[stat] = level;
  }
  loadout.statLevels = statLevels;
  const skills = lockMap.get("skills");
  if (Array.isArray(skills) && skills.every((entry) => typeof entry === "string")) loadout.skills = skills;
  return loadout;
}

function TargetBuildSummary({
  rows,
  status,
  exactCount,
  mappedCount,
  lowCount,
  highCount,
  exact,
  hasBuild,
}: {
  rows: Array<{ stat: string; target: number | null; current: number | null; delta: number | null }>;
  status: string;
  exactCount: number;
  mappedCount: number;
  lowCount: number;
  highCount: number;
  exact: boolean;
  hasBuild: boolean;
}) {
  const low = rows.filter((row) => row.delta !== null && row.delta < 0);
  const high = rows.filter((row) => row.delta !== null && row.delta > 0);
  const exactRows = rows.filter((row) => row.delta === 0);
  const tone = exact ? "border-green-400 bg-green-50 text-green-900 dark:border-green-900 dark:bg-green-950/30 dark:text-green-100"
    : status.includes("NO EXACT") ? "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"
      : "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100";
  return <section className="rounded-lg border-2 p-3 sm:p-4" aria-labelledby="target-build-summary-title">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="target-build-summary-title" className="text-base font-bold">Target vs your current / edited build</h2>
      <span className={`rounded px-2.5 py-1 text-xs font-bold ${tone}`}>{status}</span>
    </div>
    <p className="mt-1 text-xs text-muted-foreground">Values include the selected build Job and Rank, account valuables, and this character's current setup. Gear ownership is checked separately in your inventory.</p>
    <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="py-2 pr-3">Stat</th><th className="py-2 pr-3">Optimizer target</th><th className="py-2 pr-3">Current build</th><th className="py-2 pr-3">Delta</th><th className="py-2">Status</th></tr></thead>
        <tbody>{rows.map((row) => {
          const label = row.target === null ? "NO TARGET" : row.delta === null ? "UNAVAILABLE" : row.delta === 0 ? "EXACT" : row.delta < 0 ? "LOW" : "HIGH";
          const style = label === "EXACT" ? "text-green-700 dark:text-green-300" : label === "LOW" ? "text-amber-700 dark:text-amber-300" : label === "HIGH" ? "text-red-700 dark:text-red-300" : "text-muted-foreground";
          return <tr key={row.stat} className="border-b last:border-0">
            <th scope="row" className="py-1.5 pr-3 text-left uppercase">{row.stat}</th>
            <td className="py-1.5 pr-3 tabular-nums">{row.target === null ? "—" : row.target.toLocaleString()}</td>
            <td className="py-1.5 pr-3 tabular-nums">{row.current === null ? "—" : row.current.toLocaleString()}</td>
            <td className={`py-1.5 pr-3 tabular-nums ${style}`}>{row.delta === null ? "—" : `${row.delta > 0 ? "+" : ""}${row.delta.toLocaleString()}`}</td>
            <td className={`py-1.5 text-xs font-semibold ${style}`}>{label}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <div className="mt-3 grid gap-3 sm:grid-cols-3">
      <div className="rounded-md border p-2"><div className="text-xs font-bold uppercase text-amber-800 dark:text-amber-200">You still need</div><div className="mt-1 text-sm">{low.length ? low.map((row) => `+${Math.abs(row.delta!)} ${row.stat.toUpperCase()}`).join(" · ") : "Nothing below target"}</div></div>
      <div className="rounded-md border p-2"><div className="text-xs font-bold uppercase text-red-800 dark:text-red-200">Currently over</div><div className="mt-1 text-sm">{high.length ? high.map((row) => `+${row.delta} ${row.stat.toUpperCase()}`).join(" · ") : "Nothing above target"}</div></div>
      <div className="rounded-md border p-2"><div className="text-xs font-bold uppercase text-green-800 dark:text-green-200">Exact stats</div><div className="mt-1 text-sm">{exactRows.length ? exactRows.map((row) => row.stat.toUpperCase()).join(" · ") : "None yet"}{mappedCount > 0 && <span className="block text-xs text-muted-foreground">{exactCount} of {mappedCount} mapped stats exact</span>}</div></div>
    </div>
    {hasBuild && !exact && <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
      <strong>Performance validation: UNKNOWN.</strong> This build does not have a runtime-verified exact match. No measured viable stat region is attached to this optimizer strategy, so the app cannot tell whether these differences preserve performance.
    </div>}
    {!hasBuild && <p className="mt-3 rounded-md border border-muted px-3 py-2 text-sm text-muted-foreground">Current stats are unavailable until you select an available character and choose a build Job and Rank.</p>}
    {hasBuild && mappedCount > 0 && rows.some((row) => row.target !== null && row.current === null) && <p className="mt-2 text-xs text-muted-foreground">Some current values are unavailable because profile data required for those stats has not been recorded.</p>}
  </section>;
}

function WaysToReachTarget({
  search,
  locks,
  profile,
  shared,
  setLock,
}: {
  search: LegalBuildSearchResult | null;
  locks: BuildLock[];
  profile: PlayerProfile;
  shared: SharedLoadoutData | null;
  setLock: (field: string, value: unknown) => void;
}) {
  if (!search) return <section className="rounded-md border p-3"><h3 className="font-semibold">Ways to reach the target</h3><p className="mt-1 text-sm text-muted-foreground">Run an exact-build search to see which equipment, training levels and skill orders can still complete an exact legal build.</p></section>;
  const lockMap = new Map(locks.map((entry) => [entry.field, entry.value]));
  const exactValues = (field: string) => search.feasibleOptions?.[field] ?? [];
  const unknownValues = (field: string) => search.unknownOptions?.[field] ?? [];
  const renderChoices = (field: string, values: unknown[], unknown: boolean, formatter: (value: unknown) => string) => values.map((value, index) => <button key={`${field}-${index}-${JSON.stringify(value)}`} type="button" onClick={() => setLock(field, value)} className="rounded-full border bg-background px-2.5 py-1 text-left text-xs hover:bg-muted">
    {formatter(value)}<FeasibilityBadge state={unknown ? "unknown" : "feasible"} compact />
  </button>);
  const equipmentGroups = EQUIPMENT_SLOTS.map((slot) => {
    const field = `equipment.${slot.key}.name`;
    const locked = lockMap.has(field);
    const names = exactValues(field).filter((entry): entry is string => typeof entry === "string");
    const unknownNames = unknownValues(field).filter((entry): entry is string => typeof entry === "string");
    const makeName = (value: string) => {
      const item = profile.equipment.find((entry) => entry.equipmentName === value);
      return `${value}${item?.ownership === "owned" ? ` · owned ${item.quantity} · Lv ${item.currentLevel}` : item?.ownership === "unknown" ? " · ownership unknown" : " · not owned"}`;
    };
    return <div key={slot.key} className="rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">{slot.label}</h4>{locked && <span className="text-xs text-muted-foreground">Locked to {String(lockMap.get(field) ?? "empty")}</span>}</div>
      {locked ? <p className="mt-1 text-xs text-muted-foreground">Clear this lock to compare other exact-compatible choices.</p>
        : names.length > 0 || unknownNames.length > 0 ? <div className="mt-2 flex flex-wrap gap-2">
          {renderChoices(field, names.slice(0, 8), false, (entry) => makeName(String(entry)))}
          {unknownNames.slice(0, 5).map((entry, index) => <span key={`unknown-${index}`} className="rounded-full border bg-background px-2.5 py-1 text-xs">{makeName(entry)}<FeasibilityBadge state="unknown" compact /></span>)}
          {names.length > 8 && <details className="basis-full"><summary className="cursor-pointer text-xs text-muted-foreground">Show {names.length - 8} more exact-compatible choices</summary><div className="mt-2 flex flex-wrap gap-2">{renderChoices(field, names.slice(8), false, (entry) => makeName(String(entry)))}</div></details>}
        </div>
          : <div className="mt-2"><FeasibilityBadge state={search.complete ? "impossible" : "unknown"} /> <span className="ml-1 text-sm">{search.complete ? "No exact-compatible choice remains under the current locks." : "The search did not finish checking this slot."}</span></div>}
    </div>;
  });
  const trainingGroups = STAT_KEYS.filter((stat) => !lockMap.has(`statLevels.${stat}`)).map((stat) => {
    const field = `statLevels.${stat}`;
    const levels = exactValues(field).filter((entry): entry is number => typeof entry === "number").sort((a, b) => a - b);
    const unknown = unknownValues(field).filter((entry): entry is number => typeof entry === "number").sort((a, b) => a - b);
    return <div key={stat} className="rounded-md border p-3"><h4 className="font-semibold">{stat.toUpperCase()} training</h4>
      {levels.length > 0 || unknown.length > 0 ? <div className="mt-2 flex flex-wrap gap-2">
        {levels.slice(0, 10).map((level) => <button key={level} type="button" onClick={() => setLock(field, level)} className="rounded-full border bg-background px-2.5 py-1 text-xs hover:bg-muted">Level {level}<FeasibilityBadge state="feasible" compact /></button>)}
        {unknown.slice(0, 5).map((level) => <span key={`u-${level}`} className="rounded-full border bg-background px-2.5 py-1 text-xs">Level {level}<FeasibilityBadge state="unknown" compact /></span>)}
        {levels.length > 10 && <details className="basis-full"><summary className="cursor-pointer text-xs text-muted-foreground">Show {levels.length - 10} more exact-compatible levels</summary><div className="mt-2 flex flex-wrap gap-2">{levels.slice(10).map((level) => <button key={level} type="button" onClick={() => setLock(field, level)} className="rounded-full border bg-background px-2.5 py-1 text-xs hover:bg-muted">Level {level}<FeasibilityBadge state="feasible" compact /></button>)}</div></details>}
      </div> : <div className="mt-2"><FeasibilityBadge state={search.complete ? "impossible" : "unknown"} /> <span className="ml-1 text-sm">{search.complete ? "No exact-compatible level remains." : "No level is confirmed yet."}</span></div>}
    </div>;
  });
  const skillsField = "skills";
  const skillChoices = exactValues(skillsField).filter(Array.isArray) as string[][];
  const unknownSkills = unknownValues(skillsField).filter(Array.isArray) as string[][];
  return <section className="space-y-3 rounded-md border p-3" aria-labelledby="ways-title">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 id="ways-title" className="text-base font-bold">Ways to reach the target</h3><span className="text-xs text-muted-foreground">Choices preserve current locks</span></div>
    {!search.complete && <p className="text-sm text-amber-800 dark:text-amber-200">The search reached its limit. Listed choices are known exact completions; other choices remain unknown.</p>}
    {search.complete && search.exactFeasible === false && <p className="text-sm text-red-800 dark:text-red-200">No complete exact build satisfies the current locks. Unlock or change a field, then search again.</p>}
    {search.exactFeasible === null && search.complete && <p className="text-sm text-amber-800 dark:text-amber-200">An exact stat build exists only with unresolved legality or source fields. These choices need technical review.</p>}
    <div className="grid gap-2 sm:grid-cols-2">{equipmentGroups}{trainingGroups}</div>
    {!lockMap.has(skillsField) && (skillChoices.length > 0 || unknownSkills.length > 0) && <div className="rounded-md border p-3"><h4 className="font-semibold">Ordered skills</h4><div className="mt-2 flex flex-wrap gap-2">
      {skillChoices.slice(0, 5).map((skills, index) => <button key={`s-${index}`} type="button" onClick={() => setLock(skillsField, skills)} className="rounded-full border bg-background px-2.5 py-1 text-left text-xs hover:bg-muted">{skills.length ? skills.join(" → ") : "No skills"}<FeasibilityBadge state="feasible" compact /></button>)}
      {unknownSkills.slice(0, 5).map((skills, index) => <span key={`su-${index}`} className="rounded-full border bg-background px-2.5 py-1 text-xs">{skills.length ? skills.join(" → ") : "No skills"}<FeasibilityBadge state="unknown" compact /></span>)}
    </div></div>}
    <div className="text-xs text-muted-foreground">Exact-compatible means the solver found this choice in at least one complete exact legal build under the current locks. It does not by itself complete the runtime check.</div>
  </section>;
}

function FeasibilityBadge({ state, compact = false }: { state: "feasible" | "unknown" | "impossible"; compact?: boolean }) {
  const label = state === "feasible" ? "EXACT-COMPATIBLE" : state === "unknown" ? "UNKNOWN" : "IMPOSSIBLE UNDER CURRENT LOCKS";
  const style = state === "feasible" ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-200"
    : state === "unknown" ? "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
      : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-200";
  return <span className={`${compact ? "ml-1.5 inline-flex align-middle text-[8px] sm:text-[9px]" : "inline-flex text-[10px]"} rounded px-1.5 py-0.5 font-bold ${style}`}>{label}</span>;
}

function ProfileEditor(props: {
  profile: PlayerProfile; setProfile: Dispatch<SetStateAction<PlayerProfile>>; shared: SharedLoadoutData | null;
  presets: SavedLoadout[]; presetIndex: string; setPresetIndex: (value: string) => void; addPresetCharacter: () => void;
  createCharacter: () => void; updateCharacter: (id: string, update: (character: PlayerCharacter) => PlayerCharacter) => void;
  updateEquipmentItem: (name: string, update: (item: PlayerProfile["equipment"][number]) => PlayerProfile["equipment"][number]) => void;
  setEquipmentOwnership: (name: string, ownership: "owned" | "unowned" | "unknown") => void;
}) {
  const { profile, setProfile, shared, presets, updateCharacter } = props;
  const jobs = Object.keys(shared?.jobs ?? {}).sort();
  return <Card>
    <CardHeader className="pb-3"><CardTitle className="text-lg">Player profile</CardTitle><CardDescription>Track residents and account ownership here. Saved Loadout presets can seed a character; they never prove that its gear or skills are owned.</CardDescription></CardHeader>
    <CardContent className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Saved Loadout preset (starting state only)"><select className="h-9 min-w-56 rounded-md border bg-background px-2 text-sm" value={props.presetIndex} onChange={(event) => props.setPresetIndex(event.target.value)}>
          <option value="">Choose preset</option>{presets.map((entry, index) => <option key={`${entry.id}-${index}`} value={index}>{entry.name || entry.jobName || `Preset ${index + 1}`} · {entry.jobName || "no job"} {entry.rank || ""}</option>)}
        </select></Field>
        <Button variant="outline" onClick={props.addPresetCharacter} disabled={!props.presetIndex}>Add preset character</Button>
        <Button variant="outline" onClick={props.createCharacter}><Plus className="mr-1 h-4 w-4" />Add character</Button>
      </div>
      <div className="space-y-4">{profile.characters.map((character) => {
        const job = character.state.jobName ?? "";
        const rank = character.state.rank ?? "";
        const ranks = Object.keys(shared?.jobs?.[job]?.ranks ?? {});
        return <div key={character.id} className="rounded-md border p-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label="Character name" className="max-w-xs" value={character.state.name ?? ""} placeholder="Character name" onChange={(event) => updateCharacter(character.id, (entry) => ({ ...entry, state: { ...entry.state, name: event.target.value } }))} />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={character.available} onChange={(event) => updateCharacter(character.id, (entry) => ({ ...entry, available: event.target.checked }))} />Available to build/reuse</label>
            <Button variant="ghost" size="icon" aria-label={`Remove ${character.state.name || "character"}`} onClick={() => setProfile((current) => ({ ...current, characters: current.characters.filter((entry) => entry.id !== character.id) }))}><Trash2 className="h-4 w-4" /></Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Current Job"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={job} onChange={(event) => updateCharacter(character.id, (entry) => ({ ...entry, state: { ...entry.state, jobName: event.target.value, rank: "" }, jobRanks: [] }))}><option value="">Choose job</option>{jobs.map((name) => <option key={name}>{name}</option>)}</select></Field>
            <Field label="Current Rank"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={rank} onChange={(event) => updateCharacter(character.id, (entry) => ({ ...entry, state: { ...entry.state, rank: event.target.value }, jobRanks: job ? [{ jobName: job, ranks: [event.target.value] }] : [] }))} disabled={!job}><option value="">Choose rank</option>{ranks.map((entry) => <option key={entry}>{entry}</option>)}</select></Field>
            <Field label="Awakening steps"><NumberField value={character.state.awakening ?? null} min={0} max={32} onCommit={(value) => updateCharacter(character.id, (entry) => ({ ...entry, state: { ...entry.state, awakening: value ?? undefined } }))} /></Field>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Current training</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">{STAT_KEYS.map((stat) => <div key={stat} className="grid grid-cols-[auto_1fr] items-center gap-2 rounded border p-2">
              <span className="text-xs uppercase text-muted-foreground">{stat}</span>
              <NumberField label={`${stat} current training level`} value={character.state.statLevels?.[stat] ?? null} min={1} max={999} onCommit={(value) => updateCharacter(character.id, (entry) => ({
                ...entry,
                state: { ...entry.state, statLevels: updateOptionalNumber(entry.state.statLevels, stat, value) },
              }))} />
            </div>)}</div>
          </div>
          <CharacterSkillEditor character={character} updateCharacter={(update) => updateCharacter(character.id, update)} />
        </div>;
      })}</div>
      <SkillOwnershipEditor profile={profile} setProfile={setProfile} />
      <EquipmentInventoryEditor profile={profile} shared={shared} updateEquipmentItem={props.updateEquipmentItem} setEquipmentOwnership={props.setEquipmentOwnership} />
    </CardContent>
  </Card>;
}

function GlobalValuablesEditor({ profile, updateGlobalCount }: {
  profile: PlayerProfile; updateGlobalCount: (bucket: "active" | "remaining", key: string, value: number | null) => void;
}) {
  return <Card>
    <CardHeader className="pb-3"><CardTitle className="text-lg">Global valuables</CardTitle><CardDescription>Account-wide counts used by every character. Active and remaining are recorded separately; an empty field means unknown.</CardDescription></CardHeader>
    <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{RESIDENT_STAT_ITEMS.map((item) => <div key={item.key} className="rounded border p-2">
      <div className="mb-2 text-sm font-medium">{item.name}</div>
      <label className="mb-1 grid grid-cols-[1fr_76px] items-center gap-2 text-xs">Active / applied<NumberField label={`${item.name} active count`} value={profile.valuables.active[item.key] ?? null} min={0} max={9999} onCommit={(value) => updateGlobalCount("active", item.key, value)} /></label>
      <label className="grid grid-cols-[1fr_76px] items-center gap-2 text-xs">Remaining<NumberField label={`${item.name} remaining count`} value={profile.valuables.remaining[item.key] ?? null} min={0} max={9999} onCommit={(value) => updateGlobalCount("remaining", item.key, value)} /></label>
    </div>)}</CardContent>
  </Card>;
}

function SkillOwnershipEditor({ profile, setProfile }: { profile: PlayerProfile; setProfile: Dispatch<SetStateAction<PlayerProfile>> }) {
  const [query, setQuery] = useState("");
  const skills = [...new Set([...SKILL_BY_ID.values()].map(renderSkillName))].filter(Boolean);
  const visible = skills.filter((name) => name.toLowerCase().includes(query.trim().toLowerCase()));
  return <section className="rounded-md border p-3" aria-labelledby="global-skill-ownership-title">
    <div id="global-skill-ownership-title" className="font-medium">Account-wide unlocked skills</div>
    <p className="mb-3 mt-1 text-xs text-muted-foreground">Mark each skill once. A character can use an unlocked skill only when the existing job skill rules allow it. Character default setups below are optional and do not establish ownership.</p>
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <div className="relative min-w-48 flex-1"><Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" /><Input className="pl-8" aria-label="Search skills" placeholder="Search skills" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <span className="text-xs text-muted-foreground">{profile.ownedSkills.length} unlocked</span>
    </div>
    <div className="grid max-h-80 gap-1 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">{visible.map((name) => <label key={name} className="flex min-h-9 items-center gap-2 rounded border px-2 py-1 text-sm">
      <input type="checkbox" checked={profile.ownedSkills.includes(name)} onChange={(event) => setProfile((current) => ({
        ...current,
        updatedAt: Date.now(),
        ownedSkills: event.target.checked ? [...new Set([...current.ownedSkills, name])] : current.ownedSkills.filter((entry) => entry !== name),
      }))} />{name}
    </label>)}</div>
  </section>;
}

function EquipmentInventoryEditor({ profile, shared, updateEquipmentItem, setEquipmentOwnership }: {
  profile: PlayerProfile; shared: SharedLoadoutData | null;
  updateEquipmentItem: (name: string, update: (item: PlayerProfile["equipment"][number]) => PlayerProfile["equipment"][number]) => void;
  setEquipmentOwnership: (name: string, ownership: "owned" | "unowned" | "unknown") => void;
}) {
  const [query, setQuery] = useState("");
  const [rankFilter, setRankFilter] = useState("all");
  const [slotFilter, setSlotFilter] = useState("all");
  const [weaponFilter, setWeaponFilter] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkLevel, setBulkLevel] = useState("1");
  const [bulkQuantity, setBulkQuantity] = useState("1");
  const icons = (shared as (SharedLoadoutData & { equipIcons?: Record<string, string> }) | null)?.equipIcons;
  const catalog = SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.filter((entry) => {
    const weaponType = shared?.weaponTypes?.[entry.name] ?? "";
    return (!query || entry.name.toLowerCase().includes(query.trim().toLowerCase())) &&
      (rankFilter === "all" || entry.rankLabel === rankFilter) &&
      (slotFilter === "all" || entry.slot === slotFilter) &&
      (weaponFilter === "all" || weaponType === weaponFilter);
  });
  const selectedVisible = selected.filter((name) => catalog.some((entry) => entry.name === name));
  const weaponTypes = [...new Set(SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.filter((entry) => entry.slot === "weapon")
    .map((entry) => shared?.weaponTypes?.[entry.name]).filter((value): value is string => Boolean(value) && value !== "Tool"))].sort();
  const applyBulk = (update: (item: PlayerProfile["equipment"][number]) => PlayerProfile["equipment"][number]) => {
    const names = new Set(selectedVisible);
    for (const item of profile.equipment) if (names.has(item.equipmentName)) updateEquipmentItem(item.equipmentName, update);
  };
  return <section className="space-y-3 rounded-md border p-3" aria-labelledby="combat-inventory-title">
    <div id="combat-inventory-title" className="font-medium">Combat equipment inventory</div>
    <p className="text-xs text-muted-foreground">One row per equipment type. Every copy shares the same equipment level. Unknown does not count as owned. Catalog order follows the recovered equipment IDs; an authoritative in-game display sort order has not been established.</p>
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <div className="relative"><Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" /><Input className="pl-8" aria-label="Search equipment" placeholder="Search equipment" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <Field label="Rank"><select className="h-9 rounded-md border bg-background px-2 text-sm" value={rankFilter} onChange={(event) => setRankFilter(event.target.value)}><option value="all">All ranks</option>{[...new Set(SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.map((entry) => entry.rankLabel))].sort().map((rank) => <option key={rank}>{rank}</option>)}</select></Field>
      <Field label="Slot"><select className="h-9 rounded-md border bg-background px-2 text-sm" value={slotFilter} onChange={(event) => setSlotFilter(event.target.value)}><option value="all">All slots</option>{EQUIPMENT_SLOTS.map((slot) => <option key={slot.key} value={slot.key}>{slot.label}</option>)}</select></Field>
      <Field label="Weapon type"><select className="h-9 rounded-md border bg-background px-2 text-sm" value={weaponFilter} onChange={(event) => setWeaponFilter(event.target.value)}><option value="all">All weapon types</option>{weaponTypes.map((type) => <option key={type}>{type}</option>)}</select></Field>
    </div>
    <div className="flex flex-wrap items-center gap-2 rounded border bg-muted/30 p-2">
      <Button type="button" size="sm" variant="outline" onClick={() => setSelected((current) => [...new Set([...current, ...catalog.map((entry) => entry.name)])])}>Select all visible</Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setSelected([])}>Clear selection</Button>
      <span className="text-xs text-muted-foreground">{selectedVisible.length} selected</span>
      <select aria-label="Bulk ownership" className="h-9 rounded-md border bg-background px-2 text-sm" defaultValue="" onChange={(event) => {
        const ownership = event.target.value;
        if (ownership === "owned" || ownership === "unowned" || ownership === "unknown") applyBulk((item) => ({ ...item, ownership, quantity: ownership === "owned" ? Math.max(1, item.quantity) : 0 }));
        event.target.value = "";
      }}><option value="">Set ownership…</option><option value="owned">Owned</option><option value="unowned">Unowned</option><option value="unknown">Unknown</option></select>
      <label className="flex items-center gap-1 text-xs">Shared level <input className="h-9 w-16 rounded-md border bg-background px-2" type="number" min={1} max={99} value={bulkLevel} onChange={(event) => setBulkLevel(event.target.value)} /></label>
      <Button type="button" size="sm" variant="outline" disabled={!selectedVisible.length || !Number.isInteger(Number(bulkLevel)) || Number(bulkLevel) < 1 || Number(bulkLevel) > 99} onClick={() => applyBulk((item) => ({ ...item, currentLevel: Number(bulkLevel) }))}>Apply level</Button>
      <label className="flex items-center gap-1 text-xs">Copies <input className="h-9 w-16 rounded-md border bg-background px-2" type="number" min={1} max={999} value={bulkQuantity} onChange={(event) => setBulkQuantity(event.target.value)} /></label>
      <Button type="button" size="sm" variant="outline" disabled={!selectedVisible.length || !Number.isInteger(Number(bulkQuantity)) || Number(bulkQuantity) < 1 || Number(bulkQuantity) > 999} onClick={() => applyBulk((item) => ({ ...item, ownership: "owned", quantity: Number(bulkQuantity) }))}>Apply copies</Button>
    </div>
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{catalog.map((entry) => {
      const item = profile.equipment.find((row) => row.equipmentName === entry.name);
      if (!item) return null;
      const icon = getEquipmentIcon(icons, entry.name);
      return <div key={entry.name} className="min-w-0 rounded border p-2">
        <div className="flex items-start gap-2">
          <input aria-label={`Select ${entry.name}`} className="mt-1" type="checkbox" checked={selected.includes(entry.name)} onChange={(event) => setSelected((current) => event.target.checked ? [...new Set([...current, entry.name])] : current.filter((name) => name !== entry.name))} />
          {icon && <img src={icon} alt="" className="h-9 w-9 shrink-0 rounded object-contain" />}
          <div className="min-w-0 flex-1"><div className="break-words text-sm font-medium">{entry.name}</div><div className="text-[11px] text-muted-foreground">Rank {entry.rankLabel} · {entry.slot}{entry.slot === "weapon" && shared?.weaponTypes?.[entry.name] ? ` · ${shared.weaponTypes[entry.name]}` : ""}</div></div>
        </div>
        <div className="mt-2 grid grid-cols-[minmax(0,1fr)_84px_84px] gap-2">
          <Field label="Ownership"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={item.ownership} onChange={(event) => setEquipmentOwnership(entry.name, event.target.value as "owned" | "unowned" | "unknown")}><option value="unknown">Unknown</option><option value="owned">Owned</option><option value="unowned">Unowned</option></select></Field>
          <Field label="Copies">{item.ownership === "owned"
            ? <NumberField value={item.quantity} min={1} max={999} onCommit={(value) => updateEquipmentItem(entry.name, (current) => ({ ...current, ownership: "owned", quantity: value ?? 1 }))} />
            : <span className="flex h-9 items-center rounded-md border px-2 text-sm text-muted-foreground">0</span>}</Field>
          <Field label="Level"><NumberField value={item.currentLevel} min={1} max={99} onCommit={(value) => updateEquipmentItem(entry.name, (current) => ({ ...current, currentLevel: value ?? current.currentLevel }))} /></Field>
        </div>
        {item.ownership !== "owned" && <div className="mt-1 text-[11px] text-muted-foreground">{item.ownership === "unknown" ? "Ownership not recorded" : "Not in inventory"} · level is kept for reference</div>}
      </div>;
    })}</div>
    <p className="text-xs text-muted-foreground">Showing {catalog.length} of {SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.length} combat equipment types.</p>
  </section>;
}

function CharacterSkillEditor(props: { character: PlayerCharacter; updateCharacter: (update: (character: PlayerCharacter) => PlayerCharacter) => void }) {
  const skills = props.character.state.skills ?? [];
  const [draft, setDraft] = useState(skills.join(", "));
  useEffect(() => setDraft(skills.join(", ")), [props.character.id]);
  const setSkills = (names: string[]) => props.updateCharacter((character) => {
    const previous = new Map((character.state.skills ?? []).map((name, index) => [name, character.state.skillInvocations?.[index]]));
    const invocations = names.map((name) => {
      const value = previous.get(name);
      return value === 0 || value === 1 || value === 2 ? value : BATTLE_SKILL_NAMES.has(name) ? -1 : 1;
    });
    return { ...character, state: { ...character.state, skills: names, skillInvocations: invocations } };
  });
  const setInvocation = (skillIndex: number, value: number | null) => props.updateCharacter((character) => {
    const current = character.state.skills ?? [];
    const invocations = current.map((name, index) => {
      if (index === skillIndex) return value ?? -1;
      const existing = character.state.skillInvocations?.[index];
      return existing === 0 || existing === 1 || existing === 2 ? existing : BATTLE_SKILL_NAMES.has(name) ? -1 : 1;
    });
    return { ...character, state: { ...character.state, skillInvocations: invocations } };
  });
  return <div className="space-y-2 rounded border p-2">
    <Field label="Optional default skill setup"><Input value={draft} placeholder="Comma-separated default skills" onChange={(event) => setDraft(event.target.value)} onBlur={() => setSkills(parseNames(draft))} /></Field>
    <p className="text-xs text-muted-foreground">This is a starting setup only. Account ownership is recorded once above, and recommended builds still apply the selected Job's skill rules.</p>
    {skills.filter((name) => BATTLE_SKILL_NAMES.has(name)).length > 0 && <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {skills.map((name, index) => BATTLE_SKILL_NAMES.has(name) ? <Field key={`${name}-${index}`} label={`${name} invocation level`}>
        <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={[0, 1, 2].includes(props.character.state.skillInvocations?.[index] ?? -1) ? String(props.character.state.skillInvocations![index]) : ""} onChange={(event) => setInvocation(index, event.target.value === "" ? null : Number(event.target.value))}>
          <option value="">Uncaptured · native uses level 1</option><option value="0">Level 0 · highest invocation chance</option><option value="1">Level 1 · native default</option><option value="2">Level 2 · lowest invocation chance</option>
        </select>
      </Field> : null)}
    </div>}
    {skills.length > 0 && <p className="text-xs text-muted-foreground">Skill order is preserved. Every invoked skill can have a captured 0, 1, or 2 setting; uncaptured entries use the native default and remain visibly unresolved.</p>}
  </div>;
}

function ManualBuildLocks(props: {
  character: PlayerCharacter; profile: PlayerProfile; target: OptimizerTargetUnit;
  jobName: string; rank: string; settings: SolverSettings; shared: SharedLoadoutData | null;
  search: LegalBuildSearchResult | null; locks: BuildLock[];
  lockValue: (field: string) => unknown; setLock: (field: string, value: unknown) => void; clearLock: (field: string) => void;
}) {
  const currentSkills = props.character.state.skills ?? [];
  const [skillText, setSkillText] = useState(currentSkills.join(", "));
  useEffect(() => setSkillText(currentSkills.join(", ")), [props.character.id]);
  const optionState = (field: string, value: unknown): "feasible" | "infeasible" | "unknown" => {
    const values = props.search?.feasibleOptions?.[field];
    const unresolved = props.search?.unknownOptions?.[field];
    if (Array.isArray(values) && values.some((candidate) => sameOption(candidate, value))) return "feasible";
    if (Array.isArray(unresolved) && unresolved.some((candidate) => sameOption(candidate, value))) return "unknown";
    if (!Array.isArray(values) && !Array.isArray(unresolved)) return "unknown";
    return props.search?.complete ? "infeasible" : "unknown";
  };
  const keepOption = (state: "feasible" | "infeasible" | "unknown") =>
    !(state === "infeasible" && props.settings.optionDisplay === "hide-infeasible");
  const badgeTone = (state: "feasible" | "infeasible" | "unknown"): NonNullable<EquipmentSearchOption["badge"]>["tone"] =>
    state === "feasible" ? "exact" : state === "infeasible" ? "impossible" : "unknown";
  const inventory = props.profile.equipment.filter((entry) => entry.ownership === "owned" && entry.quantity > 0);
  const targetSkillNames = props.target.controllable.find((entry) => entry.field === "skills")?.value;
  const skillChoices = uniqueOptions([
    currentSkills,
    ...(Array.isArray(targetSkillNames) ? [targetSkillNames.map((entry) => entry && typeof entry === "object" ? (entry as { name?: unknown }).name : null).filter((name): name is string => typeof name === "string")] : []),
    ...(props.search?.feasibleOptions?.skills ?? []).filter(Array.isArray),
    ...(props.search?.unknownOptions?.skills ?? []).filter(Array.isArray),
  ]);
  return <div className="rounded-md border p-3 space-y-3">
    <div className="font-medium">Build controls</div>
    <p className="text-xs text-muted-foreground">Choose gear, training and skills to constrain the search. Item names stay clean; feasibility is shown separately. If the search stopped early, unlisted choices are unknown.</p>
    <div className="space-y-3">{EQUIPMENT_SLOTS.map((slot) => {
      const field = `equipment.${slot.key}.name`;
      const value = props.lockValue(field);
      const domain = uniqueOptions([
        ...inventory.filter((entry) => gearSlotForName(entry.equipmentName, props.shared?.slotAssignments) === slot.key).map((entry) => entry.equipmentName),
        ...(props.settings.inventoryPolicy === "theoretical" ? SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.filter((entry) => entry.slot === slot.key).map((entry) => entry.name) : []),
        ...(typeof value === "string" ? [value] : []),
      ]);
      const nameFieldOptions = props.search?.feasibleOptions?.[field] ?? [];
      const unknownNameOptions = props.search?.unknownOptions?.[field] ?? [];
      const choices = uniqueOptions([...domain, ...nameFieldOptions.filter((entry): entry is string => typeof entry === "string"), ...unknownNameOptions.filter((entry): entry is string => typeof entry === "string")]);
      const selection = value === undefined ? "__unlocked__" : value === null ? "__empty__" : String(value);
      const selectedName = typeof value === "string" ? value : "";
      const selectedLevel = props.lockValue(`equipment.${slot.key}.level`);
      const allCopiesInSlot = inventory.filter((entry) => gearSlotForName(entry.equipmentName, props.shared?.slotAssignments) === slot.key);
      const levelDomain = uniqueOptions([
        ...allCopiesInSlot.flatMap((stack) => props.settings.inventoryPolicy === "owned-upgrades"
          ? Array.from({ length: 100 - stack.currentLevel }, (_, index) => stack.currentLevel + index)
          : [stack.currentLevel]),
        ...(props.settings.inventoryPolicy === "theoretical" ? Array.from({ length: 99 }, (_, index) => index + 1) : []),
        ...(props.search?.feasibleOptions?.[`equipment.${slot.key}.level`] ?? []).filter((entry): entry is number => typeof entry === "number"),
        ...(typeof selectedLevel === "number" ? [selectedLevel] : []),
      ]).sort((a, b) => a - b);
      const selectorOptions: EquipmentSearchOption[] = [
        { value: "__unlocked__", label: "No equipment lock", badge: { label: "UNLOCKED", tone: "neutral" } },
        { value: "__empty__", label: "Leave slot empty", disabled: optionState(field, null) === "infeasible", badge: { label: optionState(field, null) === "infeasible" ? "IMPOSSIBLE" : optionState(field, null) === "unknown" ? "UNKNOWN" : "EXACT", tone: badgeTone(optionState(field, null)) } },
        ...choices.filter((name) => keepOption(optionState(field, name))).sort((left, right) => {
          const order = { feasible: 0, unknown: 1, infeasible: 2 };
          return order[optionState(field, left)] - order[optionState(field, right)] || left.localeCompare(right);
        }).map((name) => {
          const owned = inventory.find((entry) => entry.equipmentName === name);
          const known = props.profile.equipment.find((entry) => entry.equipmentName === name);
          const metadata = SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.find((entry) => entry.name === name);
          const weaponType = props.shared?.weaponTypes?.[name];
          const state = optionState(field, name);
          return {
            value: name,
            label: name,
            iconName: name,
            detail: [metadata ? `Rank ${metadata.rankLabel} · ${metadata.slot}` : "", weaponType && weaponType !== "Tool" ? weaponType : "",
              owned ? `Owned ${owned.quantity} · shared level ${owned.currentLevel}` : known?.ownership === "unknown" ? "Ownership unknown" : "Not owned"]
              .filter(Boolean).join(" · "),
            disabled: state === "infeasible",
            badge: { label: state === "feasible" ? "EXACT-COMPATIBLE" : state === "infeasible" ? "IMPOSSIBLE UNDER CURRENT LOCKS" : "UNKNOWN", tone: badgeTone(state) },
          } satisfies EquipmentSearchOption;
        }),
      ];
      const selectedNameState = value === undefined ? null : optionState(field, value);
      const selectedLevelState = typeof selectedLevel === "number" ? optionState(`equipment.${slot.key}.level`, selectedLevel) : null;
      return <div key={slot.key} className="grid gap-2 rounded border p-2 sm:grid-cols-2">
        <div className="min-w-0"><EquipmentSearchSelect id={`gear-${slot.key}`} label={slot.label} value={selection} options={selectorOptions} onChange={(next) => {
          props.clearLock(`equipment.${slot.key}.instanceId`);
          if (next === "__unlocked__") props.clearLock(field);
          else if (next === "__empty__") props.setLock(field, null);
          else props.setLock(field, next);
        }} equipIcons={(props.shared as (SharedLoadoutData & { equipIcons?: Record<string, string> }) | null)?.equipIcons} />
          {selectedNameState && <FeasibilityBadge state={selectedNameState === "feasible" ? "feasible" : selectedNameState === "infeasible" ? "impossible" : "unknown"} />}</div>
        <Field label={`Gear level${selectedName ? "" : " (any selected gear)"}`}><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={typeof selectedLevel === "number" ? String(selectedLevel) : "__unlocked__"} onChange={(event) => {
          if (event.target.value === "__unlocked__") props.clearLock(`equipment.${slot.key}.level`);
          else props.setLock(`equipment.${slot.key}.level`, Number(event.target.value));
        }}>
          <option value="__unlocked__">No level lock</option>
          {levelDomain.filter((level) => keepOption(optionState(`equipment.${slot.key}.level`, level))).map((level) => {
            const state = optionState(`equipment.${slot.key}.level`, level);
            return <option key={level} value={level} disabled={state === "infeasible"}>Lv {level}</option>;
          })}
        </select>{selectedLevelState && <FeasibilityBadge state={selectedLevelState === "feasible" ? "feasible" : selectedLevelState === "infeasible" ? "impossible" : "unknown"} />}</Field>
        {(value !== undefined || selectedLevel !== undefined || props.lockValue(`equipment.${slot.key}.instanceId`) !== undefined) && <Button className="sm:col-span-2 sm:justify-self-end" variant="ghost" size="sm" onClick={() => {
          props.clearLock(field); props.clearLock(`equipment.${slot.key}.level`); props.clearLock(`equipment.${slot.key}.instanceId`);
        }}>Clear {slot.label} locks</Button>}
      </div>;
    })}</div>
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">{STAT_KEYS.map((stat) => {
      const field = `statLevels.${stat}`;
      const value = props.lockValue(field);
      const current = Math.max(1, Math.floor(props.character.state.statLevels?.[stat] ?? props.character.state.level ?? 1));
      const explicitCap = props.character.trainingCaps?.[stat];
      const baseCap = props.shared?.jobs?.[props.jobName]?.ranks?.[props.rank]?.stats?.[stat]?.maxLevel;
      const derivedCap = typeof baseCap === "number" && Number.isInteger(baseCap) && Number.isInteger(props.character.state.awakening)
        ? baseCap + (props.character.state.awakening ?? 0) * 30 : current;
      const cap = Math.max(current, Math.min(999, typeof explicitCap === "number" ? explicitCap : derivedCap));
      const levelDomain = uniqueOptions([
        ...Array.from({ length: Math.max(0, cap - current + 1) }, (_, index) => current + index),
        ...(props.search?.feasibleOptions?.[field] ?? []).filter((entry): entry is number => typeof entry === "number"),
        ...(props.search?.unknownOptions?.[field] ?? []).filter((entry): entry is number => typeof entry === "number"),
        ...(typeof value === "number" ? [value] : []),
      ]).sort((a, b) => a - b);
      const valueState = typeof value === "number" ? optionState(field, value) : null;
      return <div key={stat} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded border p-2">
        <span className="text-xs uppercase text-muted-foreground">{stat}</span>
        <select className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm" value={typeof value === "number" ? String(value) : "__unlocked__"} onChange={(event) => {
          if (event.target.value === "__unlocked__") props.clearLock(field);
          else props.setLock(field, Number(event.target.value));
        }}>
          <option value="__unlocked__">Use profile training / solve within cap</option>
          {levelDomain.filter((level) => keepOption(optionState(field, level))).map((level) => {
            const state = optionState(field, level);
            return <option key={level} value={level} disabled={state === "infeasible"}>Level {level}</option>;
          })}
        </select>
        {valueState && <FeasibilityBadge state={valueState === "feasible" ? "feasible" : valueState === "infeasible" ? "impossible" : "unknown"} />}
        {typeof value === "number" && <Button variant="ghost" size="sm" onClick={() => props.clearLock(field)}>Unlock</Button>}
      </div>;
    })}</div>
    <div className="flex flex-wrap items-end gap-2">
      <Field label="Ordered skills"><select className="h-9 min-w-64 rounded-md border bg-background px-2 text-sm" value={props.lockValue("skills") === undefined ? "__unlocked__" : JSON.stringify(props.lockValue("skills"))} onChange={(event) => {
        if (event.target.value === "__unlocked__") props.clearLock("skills");
        else { try { props.setLock("skills", JSON.parse(event.target.value)); } catch { /* a generated option is always valid JSON */ } }
      }}>
        <option value="__unlocked__">No skill-order lock</option>
        {skillChoices.filter((skills) => keepOption(optionState("skills", skills))).map((skills) => {
          const encoded = JSON.stringify(skills); const state = optionState("skills", skills);
          return <option key={encoded} value={encoded} disabled={state === "infeasible"}>{skills.length ? skills.join(" → ") : "No skills"}</option>;
        })}
      </select>{props.lockValue("skills") !== undefined && <FeasibilityBadge state={optionState("skills", props.lockValue("skills")) === "feasible" ? "feasible" : optionState("skills", props.lockValue("skills")) === "infeasible" ? "impossible" : "unknown"} />}</Field>
      <Field label="Custom ordered skills"><Input value={skillText} onChange={(event) => setSkillText(event.target.value)} /></Field>
      <Button variant="outline" size="sm" onClick={() => props.setLock("skills", parseNames(skillText))}>Lock custom order</Button>
      {props.lockValue("skills") !== undefined && <Button variant="ghost" size="sm" onClick={() => props.clearLock("skills")}>Unlock skills</Button>}
    </div>
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{(Array.isArray(props.lockValue("skills")) ? props.lockValue("skills") as string[] : currentSkills)
      .filter((name) => BATTLE_SKILL_NAMES.has(name)).map((name) => {
        const field = `skillInvocations.${name}`;
        const value = props.lockValue(field);
        const profileIndex = currentSkills.indexOf(name);
        const profileValue = profileIndex >= 0 ? props.character.state.skillInvocations?.[profileIndex] : undefined;
        return <Field key={name} label={`${name} invocation`}><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={value === 0 || value === 1 || value === 2 ? String(value) : "__profile__"} onChange={(event) => {
          if (event.target.value === "__profile__") props.clearLock(field);
          else props.setLock(field, Number(event.target.value));
        }}>
          <option value="__profile__">Use profile {profileValue === 0 || profileValue === 1 || profileValue === 2 ? `level ${profileValue}` : "value (uncaptured; native defaults to 1)"}</option>
          <option value="0">Level 0 · highest invocation chance</option><option value="1">Level 1 · native default</option><option value="2">Level 2 · lowest invocation chance</option>
        </select></Field>;
      })}</div>
    {props.locks.length > 0 && <p className="text-xs text-muted-foreground">Active manual choices: {props.locks.length}. Change or clear them to explore other builds.</p>}
  </div>;
}

function SettingsEditor(props: { settings: SolverSettings; setSettings: Dispatch<SetStateAction<SolverSettings>> }) {
  const { settings, setSettings } = props;
  const update = (patch: Partial<SolverSettings>) => setSettings((current) => ({ ...current, ...patch }));
  return <Card><CardHeader className="pb-3"><CardTitle className="text-lg">Shared solver settings</CardTitle><CardDescription>Used by both encounter modes. Nearest means stat-space distance only.</CardDescription></CardHeader>
    <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="Target policy"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={settings.targetPolicy} onChange={(event) => update({ targetPolicy: event.target.value as SolverSettings["targetPolicy"] })}><option value="exact">Exact only</option><option value="exact-then-nearest">Show nearest if exact is impossible</option></select></Field>
      <Field label="Inventory policy"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={settings.inventoryPolicy} onChange={(event) => update({ inventoryPolicy: event.target.value as SolverSettings["inventoryPolicy"] })}><option value="owned-current">Owned gear at current levels</option><option value="owned-upgrades">Allow upgrades to owned gear</option><option value="theoretical">Allow any catalog gear as unvalidated theory</option></select><span className="text-[11px] text-muted-foreground">Theoretical gear never counts as owned or verified.</span></Field>
      <Field label="Option display"><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={settings.optionDisplay} onChange={(event) => update({ optionDisplay: event.target.value as SolverSettings["optionDisplay"] })}><option value="show-disabled">Show impossible choices disabled</option><option value="hide-infeasible">Hide impossible choices</option></select></Field>
      <Field label="Search node limit"><NumberField value={settings.maximumSearchNodes} min={1} max={2_000_000} onCommit={(value) => update({ maximumSearchNodes: value ?? DEFAULT_SOLVER_SETTINGS.maximumSearchNodes })} /></Field>
    </CardContent>
  </Card>;
}

function SearchResultPanel({ search, settings, locks, profile, updateEquipmentItem, setEquipmentOwnership, onUseBuild }: {
  search: LegalBuildSearchResult;
  settings: SolverSettings;
  locks: BuildLock[];
  profile: PlayerProfile;
  updateEquipmentItem: (name: string, update: (item: PlayerProfile["equipment"][number]) => PlayerProfile["equipment"][number]) => void;
  setEquipmentOwnership: (name: string, ownership: "owned" | "unowned" | "unknown") => void;
  onUseBuild: (build: SavedLoadout) => void;
}) {
  const result = search.result;
  if (!result) return <div className="space-y-2 rounded-md border p-3">
    <div className="font-bold">{search.complete && search.exactFeasible === false ? "NO EXACT BUILD FOUND" : search.complete ? "EXACT BUILD NOT CONFIRMED" : "SEARCH INCOMPLETE · EXACTNESS UNKNOWN"}</div>
    <p className="text-sm">{search.reason ?? "No complete build was returned."}</p>
    {locks.filter((entry) => !entry.field.endsWith(".instanceId")).length > 0 && <div className="text-sm"><strong>Current constraints:</strong> {locks.filter((entry) => !entry.field.endsWith(".instanceId")).map((entry) => `${entry.field.replace(/^equipment\./, "").replace(/\.name$/, "")}: ${String(entry.value ?? "empty")}`).join(" · ")}</div>}
    {!search.complete && <p className="text-sm text-amber-800 dark:text-amber-200">The search limit was reached. This does not prove that an exact build is impossible.</p>}
    {search.complete && search.exactFeasible === false && settings.targetPolicy === "exact" && <p className="text-sm text-muted-foreground">The current Target policy hides nearest builds. Choose “Show nearest if exact is impossible” in Player Profile and Settings to see the closest legal result.</p>}
    {search.complete && search.exactFeasible === false && locks.length > 0 && <p className="text-sm text-muted-foreground">These choices together leave no exact completion. Clear or change a lock, then search again to see which choices restore exact-compatible options.</p>}
  </div>;
  const exactLegalBuild = result.solverExact && ["exact-owned-now", "exact-owned-upgrades"].includes(result.status);
  const buildHeading = result.exact ? "EXACT MATCH · RUNTIME VERIFIED"
    : result.status === "theoretical-unowned" ? "THEORETICAL BUILD · GEAR NOT OWNED"
      : exactLegalBuild ? "EXACT LEGAL BUILD FOUND"
        : result.solverExact ? "TARGET STATS MATCH · LEGALITY REVIEW NEEDED"
        : "CLOSEST LEGAL BUILD · UNVALIDATED APPROXIMATION";
  const gear = EQUIPMENT_SLOTS.map((slot) => {
    const item = result.equipmentInstances[slot.key];
    return `${slot.label}: ${item?.equipmentName ? `${item.equipmentName} · Lv ${item.level ?? "?"}` : "empty"}`;
  });
  return <section className="space-y-3 rounded-md border p-3" aria-labelledby="recommended-build-title">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 id="recommended-build-title" className="text-base font-bold">{buildHeading}</h3>
      {exactLegalBuild && <Button onClick={() => onUseBuild(result.savedLoadout)}>{result.exact ? "Use this build" : "Use this exact-stat build"}</Button>}
    </div>
    {result.exact
      ? <p className="text-sm text-green-800 dark:text-green-200">Runtime preparation matched every mapped optimizer stat for this build.</p>
      : result.solverExact
        ? <p className="text-sm text-amber-800 dark:text-amber-200">The legal-build search matched the recorded stat point. {result.runtimeVerification?.message ?? "The authoritative runtime check is still pending."}</p>
        : <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"><strong>This is an unvalidated approximation.</strong> The build differs from the recorded point. No measured viable region is attached to this strategy, so we do not know whether it preserves optimizer performance.</div>}
    {!result.solverExact && <>
      <div className="text-sm font-semibold">Closest legal result · target / actual / delta</div>
      <StatTable target={result.target} achieved={result.achieved} />
      {settings.targetPolicy === "exact" && <p className="text-xs text-muted-foreground">Nearest display is disabled by the current Target policy.</p>}
    </>}
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-md border p-3"><div className="font-semibold">Build</div><div className="mt-2 space-y-1 text-sm">
        <div>Character: {result.savedLoadout.name || "Selected character"} · {result.savedLoadout.jobName} · Rank {result.savedLoadout.rank}</div>
        <div>Training: {Object.entries(result.savedLoadout.statLevels ?? {}).map(([stat, level]) => `${stat.toUpperCase()} ${level}`).join(" · ") || "Profile training"}</div>
        {gear.map((entry) => <div key={entry}>{entry}</div>)}
        <div>Skills: {result.savedLoadout.skills?.join(" → ") || "None declared"}</div>
      </div></div>
      <div className="space-y-2 rounded-md border p-3 text-sm">
        <div className="font-semibold">Check recommended equipment</div>
        {Object.values(result.equipmentInstances).filter((item) => item.equipmentName).map((gearItem) => {
          const name = gearItem.equipmentName!;
          const saved = profile.equipment.find((entry) => entry.equipmentName === name);
          const inventoryLevel = saved?.currentLevel ?? 1;
          const levelDelta = Math.max(0, (gearItem.level ?? 1) - inventoryLevel);
          return <div key={name} className="rounded border p-2">
            <div className="font-medium">{name} · recommended Lv {gearItem.level ?? "?"}</div>
            {saved?.ownership === "owned" ? <>
              <div className="text-xs text-muted-foreground">Owned · {saved.quantity} {saved.quantity === 1 ? "copy" : "copies"} · shared level {saved.currentLevel}</div>
              {levelDelta > 0 && <div className="mt-1 text-xs font-medium text-amber-800 dark:text-amber-200">Upgrade needed: Lv {saved.currentLevel} → Lv {gearItem.level} (+{levelDelta})</div>}
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Field label="Owned copies"><NumberField value={saved.quantity} min={1} max={999} onCommit={(value) => updateEquipmentItem(name, (item) => ({ ...item, ownership: "owned", quantity: value ?? item.quantity }))} /></Field>
                <Field label="Shared equipment level"><NumberField value={saved.currentLevel} min={1} max={99} onCommit={(value) => updateEquipmentItem(name, (item) => ({ ...item, currentLevel: value ?? item.currentLevel }))} /></Field>
              </div>
            </> : <>
              <div className="text-xs text-amber-800 dark:text-amber-200">{saved?.ownership === "unowned" ? "Marked unowned" : "Ownership unknown"}. This recommendation does not count the item as owned.</div>
              <div className="mt-2 flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" onClick={() => setEquipmentOwnership(name, "owned")}>I have it</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEquipmentOwnership(name, "unowned")}>I don’t have it</Button></div>
            </>}
          </div>;
        })}
        {result.requiredUpgrades.length > 0 && <p className="text-xs text-muted-foreground">Upgrade cost is not modeled here. Each equipment type has one shared level across all copies.</p>}
      </div>
    </div>
    <details className="rounded border p-2">
      <summary className="cursor-pointer text-sm font-semibold">Technical details</summary>
      <div className="mt-2 space-y-1 text-xs text-muted-foreground">
        <div>Search checked {result.search.nodes.toLocaleString()} combinations; {result.search.complete ? "the declared domain was exhausted" : "the search limit was reached"}.</div>
        {result.runtimeVerification && <div>Runtime verification: {result.runtimeVerification.status} · {sanitizeInternalIdentifier(result.runtimeVerification.message)}</div>}
        {result.sourceUnmappedFields.length > 0 && <div>Optimizer record does not identify: {result.sourceUnmappedFields.map((entry) => entry.field).join(", ")}. The selected profile Job and Rank were used.</div>}
        {result.unsupportedFields.map((entry) => <div key={entry.field}>{entry.field}: {sanitizeInternalIdentifier(entry.reason)}</div>)}
        {result.legalityIssues.map((issue, index) => <div key={`${index}-${issue}`}>{sanitizeInternalIdentifier(issue)}</div>)}
      </div>
    </details>
  </section>;
}

function PreviewPanel({ preview }: { preview: PreviewReport }) {
  const tone = preview.status === "failed" ? "error" : preview.status === "verified" && preview.warnings.length === 0 ? "success" : "warning";
  return <div className="rounded-md border p-3 space-y-2"><div className="font-semibold">Runtime preparation preview: {preview.status}</div><Notice tone={tone}>{preview.message}</Notice>
    {Object.keys(preview.stats).length > 0 && <StatTable target={Object.fromEntries(Object.entries(preview.stats).map(([key, value]) => [key, value.target]))} achieved={Object.fromEntries(Object.entries(preview.stats).flatMap(([key, value]) => value.actual === null ? [] : [[key, value.actual]]))} />}
    {preview.warnings.length > 0 && <details><summary className="cursor-pointer text-sm">Preparation warnings ({preview.warnings.length})</summary><ul className="mt-2 space-y-1 text-xs text-amber-800 dark:text-amber-200">{preview.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></details>}
    {preview.cells.length > 0 && <details><summary className="cursor-pointer text-sm">Prepared formation cells</summary><ul className="mt-2 grid gap-x-3 text-xs sm:grid-cols-2">{preview.cells.map((cell) => <li key={cell}>{cell}</li>)}</ul></details>}
  </div>;
}

function CoveragePanel({ plan, profile }: { plan: ReturnType<typeof planEncounterCoverage>; profile: PlayerProfile }) {
  const characterName = (id: string) => profile.characters.find((entry) => entry.id === id)?.state.name?.trim() || "Character not found";
  const encounterName = (id: number) => ENCOUNTER_VARIANTS.find((entry) => entry.id === id)?.title ?? "Encounter " + id;
  return <div className="space-y-3 rounded-md border p-3">
    <div className="grid gap-2 text-sm sm:grid-cols-4">
      <div>Selected: <strong>{plan.selectedEncounterIds.length}</strong></div>
      <div>Covered under plan and declared assumptions: <strong>{plan.coveredEncounterIds.length}</strong></div>
      <div>Planned units: <strong>{plan.plannedUnitCount}</strong> · already available: <strong>{plan.declaredAvailableUnitCount}</strong></div>
      <div>Characters built/reused: <strong>{plan.reusableCharacterIds.length}/{plan.characterBudget}</strong></div>
    </div>
    <p className="text-xs text-muted-foreground">
      {plan.complete ? "The planner checked all available combinations." : "The planner stopped early; this result is provisional."}
      {" "}Job, rank, training, unlocked skills, active valuables, and equipment levels are account/character investments. Encounter equipment and legal skill selections can change between fights.
      {plan.choices.some((choice) => choice.results.some((result) => result.exact && result.runtimeVerification?.status === "verified")) ? " Exact planned builds passed the game's build legality checks using the final shared equipment levels." : ""}
      {plan.declaredAvailableUnitCount > 0 ? " Player-declared available units are assumptions and were not checked." : ""}
    </p>
    {plan.reusableCharacterIds.length > 0 && <div><strong className="text-sm">Reusable roster</strong><div className="text-sm">{plan.reusableCharacterIds.map(characterName).join(", ")}</div></div>}
    {plan.choices.map((choice) => <div key={choice.encounterId + ":" + choice.candidateId} className="rounded border p-2 text-sm">
      <div className="font-medium">{encounterName(choice.encounterId)}</div>
      <div className="text-xs text-muted-foreground">Planned units: {choice.plannedUnitCount ?? choice.requiredUnitCount} · already available: {choice.declaredAvailableUnitCount ?? 0}</div>
      <details className="mt-1">
        <summary className="cursor-pointer text-xs">Technical details</summary>
        <div className="mt-1 text-xs"><strong>Optimizer evidence:</strong> {choice.evidence.label ?? "Unlabeled strategy"} · candidate ID {sanitizeInternalIdentifier(choice.candidateId)}</div>
        <div className="mt-1 text-xs">Mean {displayNumber(choice.evidence.meanEarned)} · highest {displayNumber(choice.evidence.highestEarned)} · samples {choice.evidence.sampleCount} · uncertainty {choice.evidence.uncertainty.kind}: {displayNumber(choice.evidence.uncertainty.value)} · checked branches {plan.searchNodes.toLocaleString()}</div>
      </details>
      {(choice.unitPlans ?? []).map((unitPlan, index) => {
        if (unitPlan.status === "player-declared-available") return <div key={unitPlan.unitKey} className="mt-2 rounded border border-amber-300 bg-amber-50 px-2 py-2 text-sm dark:border-amber-900 dark:bg-amber-950/30">
          <div className="font-semibold">PLAYER-DECLARED AVAILABLE UNIT</div><div>{unitPlan.name} · assumed legal and available; build, gear, and job data were not checked.</div>
        </div>;
        const result = choice.results.find((entry) => entry.characterId === unitPlan.characterId);
        if (!result) return <div key={unitPlan.unitKey} className="mt-2 rounded border px-2 py-2 text-sm"><strong>MISSING</strong> · {unitPlan.name}: no complete planned build was selected.</div>;
        const skills = (result.savedLoadout.skills ?? []).map((name, skillIndex) => {
          const level = result.savedLoadout.skillInvocations?.[skillIndex];
          const invocation = level === 0 || level === 1 || level === 2 ? String(level) : "native default / uncaptured";
          return name + " (invocation " + invocation + ")";
        });
        const gear = Object.entries(result.equipmentInstances)
          .filter(([, item]) => item.equipmentName)
          .map(([slot, item]) => slot + ": " + item.equipmentName + " Lv " + item.level);
        return <div key={result.characterId + ":" + unitPlan.unitKey + ":" + index} className="mt-2 space-y-2 rounded border p-2">
          <div className="font-medium">
            <div className="font-bold">{result.exact && result.runtimeVerification?.status === "verified" ? "VERIFIED PLANNED UNIT" : "UNVALIDATED APPROXIMATION"}</div>
            {unitPlan.name}: {characterName(result.characterId)} · {result.savedLoadout.jobName} {result.savedLoadout.rank}
            {" "}· awakening {result.savedLoadout.awakening ?? "unknown"}
          </div>
          <StatTable target={result.target} achieved={result.achieved} />
          <div><strong className="text-xs">Training levels:</strong> <span className="text-xs">{Object.entries(result.savedLoadout.statLevels ?? {}).map(([stat, level]) => stat.toUpperCase() + " " + level).join(" · ") || "profile defaults"}</span></div>
          <div><strong className="text-xs">Gear:</strong> <span className="text-xs">{gear.join(" · ") || "empty slots"}</span></div>
          <div><strong className="text-xs">Ordered skills:</strong> <span className="text-xs">{skills.join(" → ") || "none declared"}</span></div>
          {(result.runtimeVerification || result.legalityIssues.length > 0 || result.unsupportedFields.length > 0) && <details>
            <summary className="cursor-pointer text-xs">Technical details</summary>
            {result.runtimeVerification && <div className="mt-1 text-xs"><strong>Runtime:</strong> {result.runtimeVerification.status} · {sanitizeInternalIdentifier(result.runtimeVerification.message)}</div>}
            {result.unsupportedFields.map((entry) => <div key={entry.field} className="mt-1 text-xs">{entry.field}: {sanitizeInternalIdentifier(entry.reason)}</div>)}
            <ul className="mt-1 space-y-1 text-xs text-amber-800 dark:text-amber-200">{result.legalityIssues.map((issue, issueIndex) => <li key={issueIndex + ":" + issue}>{issue}</li>)}</ul>
          </details>}
        </div>;
      })}
    </div>)}
    {plan.sharedUpgrades.length > 0 && <div><strong className="text-sm">Shared equipment level upgrades</strong>{plan.sharedUpgrades.map((item) => <div key={item.equipmentName} className="text-sm">{item.equipmentName}: Lv {item.currentLevel} → Lv {item.requiredLevel} · +{item.addedLevels} levels total</div>)}</div>}
    {plan.approximatedEncounterIds.length > 0 && <Notice tone="warning"><strong>UNVALIDATED APPROXIMATION.</strong> Nearest or solver-only builds exist for {plan.approximatedEncounterIds.map(encounterName).join(", ")}; they are not counted as covered.</Notice>}
    {plan.uncovered.map((item) => <div key={item.encounterId} className="text-sm text-red-700 dark:text-red-300"><strong>MISSING</strong> · {encounterName(item.encounterId)}: {item.reason}</div>)}
  </div>;
}
function StatTable({ target, achieved }: { target: Record<string, number>; achieved?: Record<string, number> }) {
  const names = Object.keys(target);
  if (names.length === 0) return <p className="text-xs text-muted-foreground">No exact target stats are available for this unit.</p>;
  return <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="py-1 pr-3">Stat</th><th className="py-1 pr-3">Target</th>{achieved && <><th className="py-1 pr-3">Achieved</th><th className="py-1">Delta</th></>}</tr></thead><tbody>{names.map((stat) => {
    const actual = achieved?.[stat]; const delta = actual === undefined ? undefined : actual - target[stat];
    return <tr key={stat} className="border-b last:border-0"><td className="py-1 pr-3 uppercase">{stat}</td><td className="py-1 pr-3">{target[stat].toLocaleString()}</td>{achieved && <><td className="py-1 pr-3">{actual === undefined ? "—" : actual.toLocaleString()}</td><td className="py-1">{delta === undefined ? "—" : `${delta > 0 ? "+" : ""}${delta.toLocaleString()}`}</td></>}</tr>;
  })}</tbody></table></div>;
}

function NumberField({ value, min, max, label, onCommit }: { value: number | null; min?: number; max?: number; label?: string; onCommit: (value: number | null) => void }) {
  const [draft, setDraft] = useState(value === null ? "" : String(value));
  useEffect(() => setDraft(value === null ? "" : String(value)), [value]);
  return <Input aria-label={label} type="number" min={min} max={max} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={() => {
    if (draft.trim() === "") { onCommit(null); return; }
    const parsed = Number(draft);
    if (!Number.isFinite(parsed)) { setDraft(value === null ? "" : String(value)); return; }
    const committed = Math.max(min ?? -Infinity, Math.min(max ?? Infinity, Math.trunc(parsed)));
    setDraft(String(committed)); onCommit(committed);
  }} />;
}

function ModeButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return <Button role="tab" aria-selected={active} variant={active ? "default" : "outline"} onClick={onClick}>{children}</Button>;
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <div className={`grid min-w-0 gap-1 ${className}`}><Label className="text-xs text-muted-foreground">{label}</Label>{children}</div>;
}

function Notice({ tone, children }: { tone: "warning" | "error" | "success"; children: ReactNode }) {
  const styles = tone === "success" ? "border-green-300 bg-green-50 text-green-900 dark:border-green-900 dark:bg-green-950/30 dark:text-green-100"
    : tone === "error" ? "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"
      : "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100";
  return <div className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${styles}`}><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><div>{children}</div></div>;
}

function readProfile(): PlayerProfile {
  try { return normalizePlayerProfile(JSON.parse(localStorage.getItem(PROFILE_KEY) ?? "null")); }
  catch { return createEmptyPlayerProfile(); }
}

function readSettings(): SolverSettings {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null") as Partial<SolverSettings> | null;
    return { ...DEFAULT_SOLVER_SETTINGS, ...(raw ?? {}) };
  } catch { return DEFAULT_SOLVER_SETTINGS; }
}

function parseNames(value: string): string[] {
  return [...new Set(value.split(/[,\n]/).map((name) => name.trim()).filter(Boolean))];
}

function displayNumber(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "unknown";
}

function sanitizeInternalIdentifier(value: string): string {
  return value.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:#\d+)?/gi, "owned item")
    .replace(/\b[A-Za-z0-9_-]{12,}#\d+\b/g, "owned item");
}

function updateOptionalNumber<T extends object>(source: T | undefined, key: keyof T, value: number | null): T {
  const next = { ...(source ?? {}) } as T;
  const writable = next as unknown as Record<string, unknown>;
  if (value === null) delete writable[String(key)];
  else writable[String(key)] = value;
  return next;
}

function sameOption(left: unknown, right: unknown): boolean {
  return Object.is(left, right) || (left !== null && right !== null && typeof left === "object" && typeof right === "object" && JSON.stringify(left) === JSON.stringify(right));
}

function uniqueOptions<T>(values: T[]): T[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = JSON.stringify(value);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}




function QualifiedEvidence({ rows, build }: { rows: QualifiedOptimizerEvidence[]; build?: { status: string; reason: string } }) {
  return <div className="mb-4 space-y-2 text-sm">
    {rows.map((row, index) => <div key={index} className="rounded-md border p-3">
      <strong>{row.label}</strong>
      <div>Fresh mean {displayNumber(row.meanEarned)} · highest {displayNumber(row.highestEarned)} · n={row.sampleCount} · mean uncertainty {row.uncertainty.kind}: {displayNumber(row.uncertainty.value)}</div>
      {row.pairedInterval && <div>Paired mean gain interval: {row.pairedInterval.map(displayNumber).join(" to ")}</div>}
      <details><summary>Measurement scope</summary><div>Window: {JSON.stringify(row.measurementWindow)} · policy: {JSON.stringify(row.policy)}</div></details>
    </div>)}
    {build && <div className="text-muted-foreground">Build translation: {build.status}. {build.reason}</div>}
  </div>;
}
