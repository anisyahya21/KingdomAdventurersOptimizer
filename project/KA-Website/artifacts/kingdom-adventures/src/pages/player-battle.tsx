import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { AlertTriangle, Download, Eye, Loader2, Play, RotateCcw, Save, Sword, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ka/page-header";
import { EncounterSection, TeamSection } from "@/components/ka/battle-builder/team-builder";
import { TeamFormation } from "@/components/ka/battle-builder/team-formation";
import { NumberField } from "@/components/ka/battle-builder/editors";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { apiUrl } from "@/lib/api";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import {
  battleSetupFromLoadouts,
  readLoadoutHandoff,
  type SavedLoadout,
  type SharedLoadoutData,
} from "@/lib/battle-legality";
import {
  CANONICAL_RECOVERY_ITEMS,
  ENCOUNTER_BY_ID,
  ENCOUNTER_VARIANTS,
  canonicalRecoveryItem,
  declaredItemRows,
  isPlayerFacingBattleIssue,
  validateBattleSetup,
  type SetupIssue,
  type SetupIssueCategory,
} from "@/lib/battle-setup";
import { BattleSetupAdapterError, battleSetupToCombatScenario } from "@/lib/battle-setup-adapter";
import {
  runBattleSetup,
  type BattleScenarioTransport,
} from "@/lib/battle-setup-runner";
import {
  BattlePreviewError,
  isAbortedPreview,
  requestBattlePreview,
  type BattlePreview,
  type BattlePreviewUnit,
} from "@/lib/battle-preview";
import { writeGeneratedBattle } from "@/lib/generated-battle-store";
import { startBrowserBattle } from "@/lib/browser-battle";
import { downloadJson } from "@/lib/download-json";
import { optimizerScenarioFilename, optimizerScenarioFromSetup } from "@/lib/strategy-optimizer-export";
import { deleteAccountRoster, fetchAccountRosters, saveAccountRoster, type SavedRoster } from "@/lib/account-rosters";
import { interactionCapabilities } from "@/lib/battle-interaction";
import {
  BATTLE_TEAM_DRAFT_KEY,
  DEFAULT_CONSUMABLES,
  builderSkillNames,
  createDraft,
  draftCharacterFromLoadout,
  normalizeDraft,
  normalizeSavedLoadouts,
  type BattleTeamDraft,
  type BuilderSharedData,
  type DraftCharacter,
  type DraftConsumables,
} from "@/lib/battle-team-draft";
import {
  RESIDENT_STAT_ITEMS_KEY,
  deviceResidentValuables,
  loadoutsWithResidentValuables,
} from "@/lib/resident-valuable-settings";

/**
 * Player Battle - the visual builder behind /battle.
 *
 * The page owns the flow, not the rules. It builds an ORDERED team whose members are Loadout Builder
 * characters (copied presets or created characters) and then hands that team to the existing,
 * already-verified contracts:
 *
 *   * `battleSetupFromLoadouts` (battle-legality) converts the ordered characters into one legal
 *     `ka-battle-setup-1` team, carrying the declared job/rank/gender, the per-stat levels through
 *     the shared job curve, the equipment slots with levels, the ordered `skillInvocations` and the
 *     declared `households` exactly as the editor stored them;
 *   * `requestBattlePreview` (battle-preview) asks the backend for the read-only prepared formation;
 *   * `runBattleSetup` starts the Python combat runner in a browser worker and validates its first
 *     `ka-battle-replay-1` window;
 *   * `writeGeneratedBattle` stores that replay for the existing generated replay viewer.
 *
 * The draft is local (`ka_battle_team_draft`); presets are COPIED from `ka_loadouts` and never
 * written back. Nothing here simulates combat, and no unresolved native rule (pet capacity,
 * per-species first pet skill, reward settlement) is claimed as recovered.
 */

const transport: BattleScenarioTransport = startBrowserBattle;

type PreviewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; preview: BattlePreview }
  | { status: "error"; message: string; details: string[] };

type RunState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "error"; message: string; details: string[] };

/**
 * The built-in herb spends from `holyHerbStock`; every other canonical Item.txt recovery row is
 * declared as an explicit item with its own finite count. Both come from the existing recovery
 * catalog (`CANONICAL_RECOVERY_ITEMS`) and the existing setup model - no new item data.
 */
const HERB_ITEM_NAME = "Holy Herb";
const CONSUMABLE_ITEM_ROWS = declaredItemRows(
  CANONICAL_RECOVERY_ITEMS.filter((item) => item.name !== HERB_ITEM_NAME),
);

function issueVariant(category: SetupIssueCategory) {
  if (category === "ERROR") return "destructive" as const;
  if (category === "WARNING") return "secondary" as const;
  return "outline" as const;
}

function IssueList({ issues, marker, limit = 12 }: { issues: SetupIssue[]; marker: string; limit?: number }) {
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-1 text-xs" data-issue-list={marker}>
      {issues.slice(0, limit).map((issue, index) => (
        <li
          key={marker + "-" + issue.code + "-" + index}
          className="flex items-start gap-2"
          data-issue-code={issue.code}
          data-issue-category={issue.category}
        >
          <Badge variant={issueVariant(issue.category)} className="mt-0.5 shrink-0 text-[9px]">
            {issue.category}
          </Badge>
          <span className="min-w-0 break-words">{issue.message}</span>
        </li>
      ))}
      {issues.length > limit ? (
        <li className="text-muted-foreground" data-issue-overflow>
          + {issues.length - limit} more
        </li>
      ) : null}
    </ul>
  );
}

function PreviewUnitRow({ unit }: { unit: BattlePreviewUnit }) {
  const hp = unit.parameters?.["10"];
  return (
    <tr className="border-b border-border/40 last:border-0" data-preview-unit={unit.name} data-preview-side={unit.side}>
      <td className="py-0.5 pr-2">{unit.name}</td>
      <td className="py-0.5 pr-2 text-muted-foreground">{unit.side}</td>
      <td className="py-0.5 pr-2">{unit.petOwnerName ?? "-"}</td>
      <td className="py-0.5 pr-2 tabular-nums">{unit.cell ? `${unit.cell[0]},${unit.cell[1]}` : "-"}</td>
      <td className="py-0.5 tabular-nums">{hp?.effectiveValue ?? "-"}</td>
    </tr>
  );
}

export default function PlayerBattlePage() {
  const [, navigate] = useLocation();
  const [stored, setStored] = useLocalFeature<BattleTeamDraft>(BATTLE_TEAM_DRAFT_KEY, createDraft(ENCOUNTER_VARIANTS[0]?.id ?? 19));
  const draft: BattleTeamDraft = useMemo(() => normalizeDraft(stored) ?? createDraft(ENCOUNTER_VARIANTS[0]?.id ?? 19), [stored]);
  const [sharedData, setSharedData] = useState<BuilderSharedData | null>(null);
  const [teamMessage, setTeamMessage] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<PreviewState>({ status: "idle" });
  const [runState, setRunState] = useState<RunState>({ status: "idle" });
  const [battleSeconds, setBattleSeconds] = useState(900);
  const [secondsInput, setSecondsInput] = useState<string | null>(null);
  const [partyBonusInput, setPartyBonusInput] = useState<string | null>(null);
  const [storedLoadouts, setStoredLoadouts] = useLocalFeature<unknown>("ka_loadouts", []);
  const [savedRosters, setSavedRosters] = useLocalFeature<SavedRoster[]>("ka_battle_rosters", []);
  const [accountRosters, setAccountRosters] = useState<SavedRoster[]>([]);
  const [accountStatus, setAccountStatus] = useState<"loading" | "ready" | "guest" | "error">("loading");
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountMessage, setAccountMessage] = useState<string | null>(null);
  const [rosterName, setRosterName] = useState("");
  const [pendingRosterDelete, setPendingRosterDelete] = useState<string | null>(null);
  const [pendingAccountDelete, setPendingAccountDelete] = useState<string | null>(null);
  const [resetPending, setResetPending] = useState(false);
  const [deviceValuables, setDeviceValuables] = useLocalFeature<unknown>(RESIDENT_STAT_ITEMS_KEY, {});
  /**
   * The Loadout Builder's saved presets, read defensively (a legacy/loose stored row still imports
   * instead of throwing). Universal Water counts replace older per-character copies.
   */
  const savedLoadouts: SavedLoadout[] = useMemo(
    () => loadoutsWithResidentValuables(normalizeSavedLoadouts(storedLoadouts), deviceResidentValuables(deviceValuables)),
    [storedLoadouts, deviceValuables],
  );
  const handoffRead = useRef(false);
  const previewAbort = useRef<AbortController | null>(null);
  const accountRequestVersion = useRef(0);

  const refreshAccountRosters = useCallback(async () => {
    const version = ++accountRequestVersion.current;
    setAccountStatus("loading");
    try {
      const rosters = await fetchAccountRosters();
      if (version !== accountRequestVersion.current) return;
      setAccountRosters(rosters);
      setAccountStatus("ready");
      setAccountMessage(null);
    } catch (error) {
      if (version !== accountRequestVersion.current) return;
      setAccountRosters([]);
      if (error instanceof Error && error.message === "ACCOUNT_LOGIN_REQUIRED") {
        setAccountStatus("guest");
      } else {
        setAccountStatus("error");
        setAccountMessage(error instanceof Error ? error.message : "Could not load account loadouts.");
      }
    }
  }, []);

  useEffect(() => { void refreshAccountRosters(); }, [refreshAccountRosters]);
  useEffect(() => {
    const onAuthChanged = (event: Event) => {
      const authenticated = (event as CustomEvent<{ authenticated: boolean }>).detail?.authenticated;
      setAccountRosters([]);
      setAccountMessage(null);
      if (authenticated) void refreshAccountRosters();
      else { accountRequestVersion.current += 1; setAccountStatus("guest"); }
    };
    window.addEventListener("ka-auth-changed", onAuthChanged);
    return () => window.removeEventListener("ka-auth-changed", onAuthChanged);
  }, [refreshAccountRosters]);

  const allSkills = useMemo(() => builderSkillNames(sharedData?.skills), [sharedData]);
  const variant = ENCOUNTER_BY_ID.get(draft.encounterId) ?? ENCOUNTER_VARIANTS[0];
  const consumables: DraftConsumables = draft.consumables ?? DEFAULT_CONSUMABLES;
  // What the replay will actually offer as clickable consumables, from the existing helper.
  const providableConsumables = useMemo(
    () =>
      interactionCapabilities({
        scenario: {
          items: CONSUMABLE_ITEM_ROWS,
          itemStock: consumables.itemStock,
          holyHerbStock: consumables.holyHerbStock,
        },
      }),
    [consumables],
  );

  useEffect(() => {
    let cancelled = false;
    fetchSharedWithFallback<BuilderSharedData>(apiUrl("/shared"))
      .then((loaded) => {
        if (!cancelled) setSharedData(loaded);
      })
      .catch(() => {
        /* the bundled fallback is returned instead of throwing */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => previewAbort.current?.abort(), []);

  // Handoff from the Loadout Builder: the selected loadouts are COPIED into the local team.
  useEffect(() => {
    if (handoffRead.current) return;
    handoffRead.current = true;
    const { payload, message } = readLoadoutHandoff();
    if (message) setTeamMessage(message);
    if (!payload || payload.loadoutIds.length === 0) return;
    const byId = new Map(savedLoadouts.map((entry) => [entry.id ?? "", entry]));
    const copies = payload.loadoutIds
      .map((id) => byId.get(id))
      .filter((entry): entry is SavedLoadout => Boolean(entry))
      .map((entry, index) => draftCharacterFromLoadout(entry, index + 1));
    if (copies.length === 0) {
      setTeamMessage("The Loadout Builder handoff named no saved loadout that is still stored here.");
      return;
    }
    setStored((current) => {
      const base = normalizeDraft(current) ?? draft;
      return { ...base, characters: [...base.characters, ...copies] };
    });
    setTeamMessage(`Copied ${copies.length} loadout preset(s) from the Loadout Builder into the team.`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const conversion = useMemo(() => {
    if (!sharedData) return null;
    return battleSetupFromLoadouts(loadoutsWithResidentValuables(draft.characters, deviceResidentValuables(deviceValuables)) as SavedLoadout[], sharedData, {
      encounterId: draft.encounterId,
      tickLimit: battleSeconds * 20,
      holyHerbStock: consumables.holyHerbStock,
      items: CONSUMABLE_ITEM_ROWS,
      itemStock: consumables.itemStock,
      partyMax: draft.partyBonus === undefined ? undefined : 2 + draft.partyBonus,
    });
  }, [sharedData, draft.characters, draft.encounterId, draft.partyBonus, deviceValuables, consumables, battleSeconds]);

  const issues = useMemo(() => {
    if (conversion) return conversion.issues;
    return draft.characters.length === 0
      ? [{ category: "ERROR" as const, code: "LOADOUT_TEAM_EMPTY", path: "playerTeam", message: "Add at least one unit to the team." }]
      : [];
  }, [conversion, draft.characters.length]);

  const errors = issues.filter((issue) => issue.category === "ERROR");
  // The conversion also reports provenance and accepted native defaults. Those are diagnostics,
  // not problems a player can fix in this editor.
  const otherIssues = issues.filter((issue) => issue.category !== "ERROR" && isPlayerFacingBattleIssue(issue));
  const setup = conversion?.setup ?? null;

  // The formation card uses the preparation pass because placement depends on effective defense
  // and formation skills. Debounce editor changes and discard any response for an older setup.
  useEffect(() => {
    previewAbort.current?.abort();
    if (!setup || errors.length > 0) {
      setPreviewState({ status: "idle" });
      return;
    }
    const controller = new AbortController();
    previewAbort.current = controller;
    setPreviewState({ status: "loading" });
    const timer = window.setTimeout(async () => {
      try {
        const { scenario } = battleSetupToCombatScenario(setup);
        const preview = await requestBattlePreview(scenario, controller.signal);
        if (!controller.signal.aborted) setPreviewState({ status: "ok", preview });
      } catch (error) {
        if (controller.signal.aborted || isAbortedPreview(error)) return;
        setPreviewState({
          status: "error",
          message: error instanceof BattlePreviewError ? error.message : "The preview request failed: " + (error instanceof Error ? error.message : String(error)),
          details: [],
        });
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [setup, errors.length]);

  const setCharacters = (next: DraftCharacter[]) => {
    setStored((current) => ({ ...(normalizeDraft(current) ?? draft), characters: next }));
    setPreviewState({ status: "idle" });
    setRunState({ status: "idle" });
  };

  const setConsumables = (next: DraftConsumables) => {
    setStored((current) => ({ ...(normalizeDraft(current) ?? draft), consumables: next }));
    setPreviewState({ status: "idle" });
  };

  const setConsumableStock = (name: string, count: number) => {
    setConsumables({ ...consumables, itemStock: { ...consumables.itemStock, [name]: count } });
  };

  const resetDraft = () => {
    setStored(createDraft(draft.encounterId));
    setPreviewState({ status: "idle" });
    setRunState({ status: "idle" });
    setTeamMessage(null);
    setResetPending(false);
  };

  const saveCharacter = (character: DraftCharacter) => {
    const copy = draftCharacterFromLoadout(character, 1);
    const current = normalizeSavedLoadouts(storedLoadouts);
    setStoredLoadouts([...current, copy]);
    setTeamMessage(`${character.name || "Character"} copied to Saved characters.`);
  };

  const makeRoster = (name: string): SavedRoster => ({
      id: `roster-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      name,
      encounterId: draft.encounterId,
      ...(draft.partyBonus === undefined ? {} : { partyBonus: draft.partyBonus }),
      residentStatItems: deviceResidentValuables(deviceValuables),
      characters: draft.characters.map((character, index) => draftCharacterFromLoadout(character, index + 1)),
      consumables: { holyHerbStock: consumables.holyHerbStock, itemStock: { ...consumables.itemStock } },
  });

  const saveRoster = () => {
    const name = rosterName.trim();
    if (!name || draft.characters.length === 0) return;
    const saved = makeRoster(name);
    setSavedRosters((current) => [...current, saved]);
    setRosterName("");
    setTeamMessage(`${name} saved on this device.`);
  };

  const downloadForOptimizer = (name: string, roster?: SavedRoster) => {
    try {
      const source = roster && sharedData
        ? battleSetupFromLoadouts(
            loadoutsWithResidentValuables(roster.characters, deviceResidentValuables(roster.residentStatItems ?? deviceValuables)) as SavedLoadout[],
            sharedData,
            {
              encounterId: roster.encounterId,
              tickLimit: battleSeconds * 20,
              holyHerbStock: roster.consumables.holyHerbStock,
              items: CONSUMABLE_ITEM_ROWS,
              itemStock: roster.consumables.itemStock,
              partyMax: roster.partyBonus === undefined ? undefined : 2 + roster.partyBonus,
            },
          ).setup
        : setup;
      if (!source) throw new Error("Fix this loadout's battle errors before exporting it.");
      downloadJson(optimizerScenarioFilename(name), optimizerScenarioFromSetup(source));
      setTeamMessage(`${name} downloaded for the desktop optimizer.`);
    } catch (error) {
      setTeamMessage(error instanceof Error ? error.message : "Could not export this loadout.");
    }
  };

  const saveToAccount = async (roster?: SavedRoster) => {
    const name = roster?.name ?? rosterName.trim();
    if (!name || (!roster && draft.characters.length === 0) || accountBusy) return;
    setAccountBusy(true);
    setAccountMessage(null);
    const version = accountRequestVersion.current;
    try {
      const saved = await saveAccountRoster(roster
        ? { ...roster, residentStatItems: roster.residentStatItems ?? deviceResidentValuables(deviceValuables) }
        : makeRoster(name));
      if (version !== accountRequestVersion.current) return;
      setAccountRosters((current) => [saved, ...current]);
      if (!roster) setRosterName("");
      setAccountStatus("ready");
      setAccountMessage(`${name} saved to your account.`);
    } catch (error) {
      if (version !== accountRequestVersion.current) return;
      if (error instanceof Error && error.message === "ACCOUNT_LOGIN_REQUIRED") {
        setAccountStatus("guest");
        setAccountMessage("Log in with the account button above, then refresh account loadouts.");
      } else {
        setAccountMessage(error instanceof Error ? error.message : "Could not save to your account.");
      }
    } finally {
      setAccountBusy(false);
    }
  };

  const removeAccountRoster = async (roster: SavedRoster) => {
    if (accountBusy) return;
    setAccountBusy(true);
    setAccountMessage(null);
    const version = accountRequestVersion.current;
    try {
      await deleteAccountRoster(roster.id);
      if (version !== accountRequestVersion.current) return;
      setAccountRosters((current) => current.filter((item) => item.id !== roster.id));
      setPendingAccountDelete(null);
      setAccountMessage(`${roster.name} deleted from your account.`);
    } catch (error) {
      if (version !== accountRequestVersion.current) return;
      setAccountMessage(error instanceof Error ? error.message : "Could not delete the account loadout.");
    } finally {
      setAccountBusy(false);
    }
  };

  const loadRoster = (roster: SavedRoster) => {
    if (draft.characters.length > 0 && !window.confirm(`Replace the current team with ${roster.name}?`)) return;
    setStored({
      ...createDraft(roster.encounterId),
      characters: roster.characters.map((character, index) => draftCharacterFromLoadout(character, index + 1)),
      ...(roster.partyBonus === undefined ? {} : { partyBonus: roster.partyBonus }),
      consumables: { holyHerbStock: roster.consumables.holyHerbStock, itemStock: { ...roster.consumables.itemStock } },
    });
    if (roster.residentStatItems !== undefined) setDeviceValuables(roster.residentStatItems);
    setTeamMessage(`${roster.name} loaded.`);
  };

  const previewFormation = async () => {
    if (!setup) {
      setPreviewState({
        status: "error",
        message: "This team has errors, so no formation was requested.",
        details: errors.map((issue) => issue.code + ": " + issue.message),
      });
      return;
    }
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    setPreviewState({ status: "loading" });
    try {
      const { scenario } = battleSetupToCombatScenario(setup);
      const preview = await requestBattlePreview(scenario, controller.signal);
      if (controller.signal.aborted) return;
      setPreviewState({ status: "ok", preview });
    } catch (error) {
      if (isAbortedPreview(error)) return;
      setPreviewState({
        status: "error",
        message:
          error instanceof BattlePreviewError
            ? error.message
            : "The preview request failed: " + (error instanceof Error ? error.message : String(error)),
        details: [],
      });
    }
  };

  const runCurrentBattle = async () => {
    if (!setup) {
      setRunState({
        status: "error",
        message: "This team has errors and was not sent to the simulator.",
        details: errors.map((issue) => issue.code + ": " + issue.message),
      });
      return;
    }
    // Re-validate the exact setup that will be sent, so the run panel never reports a stale state.
    const liveErrors = validateBattleSetup(setup).filter((issue) => issue.category === "ERROR");
    if (liveErrors.length > 0) {
      setRunState({
        status: "error",
        message: "This setup has errors and was not sent to the simulator.",
        details: liveErrors.map((issue) => issue.code + ": " + issue.message),
      });
      return;
    }
    setRunState({ status: "running" });
    try {
      const run = await runBattleSetup(setup, transport);
      writeGeneratedBattle(
        run.result,
        run.visualSetup,
        run.warnings.filter(isPlayerFacingBattleIssue).map((issue) => issue.category + ":" + issue.code),
        variant.title,
        run.scenarioJson,
      );
      navigate("/battle-replay?mode=generated");
    } catch (error) {
      if (error instanceof BattleSetupAdapterError) {
        setRunState({
          status: "error",
          message: "The setup was rejected by the adapter before anything was sent.",
          details: error.issues.map((issue) => issue.code + ": " + issue.message),
        });
        return;
      }
      setRunState({
        status: "error",
        message: "The combat runner did not return a usable replay.",
        details: [error instanceof Error ? error.message : String(error)],
      });
    }
  };

  const preview = previewState.status === "ok" ? previewState.preview : null;
  const previewError = previewState.status === "error" ? previewState : null;
  const runError = runState.status === "error" ? runState : null;

  return (
    <div className="mx-auto max-w-7xl space-y-4 px-4 py-6" data-player-battle-root data-encounter-id={draft.encounterId} data-run-state={runState.status}>
      <PageHeader
        icon={<Sword className="h-5 w-5" />}
        title="Player Battle"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {resetPending ? (
              <><Button variant="outline" size="sm" className="min-h-11" onClick={() => setResetPending(false)}>Cancel</Button>
                <Button variant="destructive" size="sm" className="min-h-11" onClick={resetDraft}>Clear team</Button></>
            ) : (
              <Button variant="outline" size="sm" className="min-h-11 gap-1 text-xs text-destructive" onClick={() => setResetPending(true)} data-action="reset-draft">
                <RotateCcw className="h-3 w-3" /> Reset team
              </Button>
            )}
            <Link href="/loadout" className="text-xs font-medium underline">
              Characters
            </Link>
          </div>
        }
      >
        Build a team and run the fight.
      </PageHeader>

      <EncounterSection
        encounterId={draft.encounterId}
        onSelect={(encounterId) => {
          setStored((current) => ({ ...(normalizeDraft(current) ?? draft), encounterId }));
          setPreviewState({ status: "idle" });
          setRunState({ status: "idle" });
        }}
      />

      <TeamFormation characters={draft.characters} setup={errors.length === 0 ? setup : null} data={sharedData} preview={preview} previewStatus={previewState.status} />

      <TeamSection
        characters={draft.characters}
        data={sharedData}
        allSkills={allSkills}
        savedLoadouts={savedLoadouts}
        onSaveCharacter={saveCharacter}
        onChange={setCharacters}
      />

      <label className="flex flex-wrap items-center gap-2 rounded border px-3 py-2 text-sm">
        <span>Extra party slots from valuables</span>
        <Input type="text" inputMode="numeric" className="min-h-11 w-20" aria-label="Extra party slots from valuables"
          placeholder="Unknown" value={partyBonusInput ?? (draft.partyBonus === undefined ? "" : String(draft.partyBonus))}
          onChange={(event) => setPartyBonusInput(event.target.value.replace(/[^\d]/g, ""))}
          onBlur={() => { if (partyBonusInput !== null) setStored((current) => ({ ...(normalizeDraft(current) ?? draft), partyBonus: partyBonusInput === "" ? undefined : Math.min(999, Number(partyBonusInput)) })); setPartyBonusInput(null); }} />
        <span className="text-xs text-muted-foreground">Base capacity: 2</span>
      </label>

      <Card data-saved-rosters>
        <CardHeader><CardTitle className="text-base">Saved loadouts</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input value={rosterName} onChange={(event) => setRosterName(event.target.value)} maxLength={80} placeholder="Loadout name" aria-label="Loadout name" className="min-h-11 min-w-0 flex-1 basis-44" />
            <Button type="button" onClick={saveRoster} disabled={!rosterName.trim() || draft.characters.length === 0} className="min-h-11 gap-2"><Save className="h-4 w-4" />Save on device</Button>
            <Button type="button" variant="outline" onClick={() => void saveToAccount()} disabled={accountStatus !== "ready" || accountBusy || accountRosters.length >= 20 || !rosterName.trim() || draft.characters.length === 0} className="min-h-11 gap-2"><Save className="h-4 w-4" />Save to account</Button>
            <Button type="button" variant="outline" onClick={() => downloadForOptimizer(rosterName.trim() || "Current team")} disabled={!setup} className="min-h-11 gap-2" title="Import this JSON with the desktop optimizer's Import Build button"><Download className="h-4 w-4" />Download team JSON</Button>
          </div>
          <p className="text-xs font-medium text-muted-foreground">On this device</p>
          {savedRosters.map((roster) => (
            <div key={roster.id} className="flex flex-wrap items-center gap-2 rounded border p-2">
              <span className="min-w-0 flex-1 text-sm font-medium">{roster.name} · {roster.characters.length} character(s)</span>
              <Button type="button" variant="outline" className="min-h-11" onClick={() => loadRoster(roster)}>Load</Button>
              <Button type="button" variant="outline" className="min-h-11 gap-1" onClick={() => downloadForOptimizer(roster.name, roster)} disabled={!sharedData} title="Import this JSON with the desktop optimizer's Import Build button"><Download className="h-4 w-4" />JSON</Button>
              {accountStatus === "ready" ? <Button type="button" variant="outline" className="min-h-11" disabled={accountBusy || accountRosters.length >= 20} onClick={() => void saveToAccount(roster)}>Copy to account</Button> : null}
              {pendingRosterDelete === roster.id ? (
                <><Button type="button" variant="outline" className="min-h-11" onClick={() => setPendingRosterDelete(null)}>Cancel</Button>
                  <Button type="button" variant="destructive" className="min-h-11" onClick={() => { setSavedRosters((current) => current.filter((item) => item.id !== roster.id)); setPendingRosterDelete(null); }}>Delete</Button></>
              ) : (
                <Button type="button" variant="outline" className="min-h-11 text-destructive" aria-label={`Delete ${roster.name}`} onClick={() => setPendingRosterDelete(roster.id)}><Trash2 className="h-4 w-4" /></Button>
              )}
            </div>
          ))}
          <div className="border-t pt-3" data-account-rosters>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium text-muted-foreground">Your account · {accountRosters.length}/20</p>
              <Button type="button" variant="outline" className="min-h-11" onClick={() => void refreshAccountRosters()} disabled={accountStatus === "loading" || accountBusy}>Refresh</Button>
            </div>
            {accountStatus === "loading" ? <p className="text-xs text-muted-foreground">Loading account loadouts...</p> : null}
            {accountStatus === "guest" ? <p className="text-xs text-muted-foreground">Log in with the account button above, then tap Refresh.</p> : null}
            {accountMessage ? <p className="mb-2 text-xs" role="status">{accountMessage}</p> : null}
            {accountRosters.map((roster) => (
              <div key={roster.id} className="mb-2 flex flex-wrap items-center gap-2 rounded border p-2">
                <span className="min-w-0 flex-1 text-sm font-medium">{roster.name} · {roster.characters.length} character(s)</span>
                <Button type="button" variant="outline" className="min-h-11" onClick={() => loadRoster(roster)}>Load</Button>
                <Button type="button" variant="outline" className="min-h-11 gap-1" onClick={() => downloadForOptimizer(roster.name, roster)} disabled={!sharedData} title="Import this JSON with the desktop optimizer's Import Build button"><Download className="h-4 w-4" />JSON</Button>
                {pendingAccountDelete === roster.id ? (
                  <><Button type="button" variant="outline" className="min-h-11" onClick={() => setPendingAccountDelete(null)}>Cancel</Button>
                    <Button type="button" variant="destructive" className="min-h-11" disabled={accountBusy} onClick={() => void removeAccountRoster(roster)}>Delete</Button></>
                ) : (
                  <Button type="button" variant="destructive" className="min-h-11" aria-label={`Delete ${roster.name} from account`} onClick={() => setPendingAccountDelete(roster.id)}><Trash2 className="h-4 w-4" /></Button>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card data-builder-consumables>
        <CardHeader>
          <CardTitle className="text-base">4 - Provision consumables (optional)</CardTitle>
          <CardDescription>Stock the items the fight's action bar spends from. A row at 0 has nothing to click.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {providableConsumables.map((row) => {
            const item = row.type === "holy_herb" ? null : canonicalRecoveryItem(row.name);
            const label = row.type === "holy_herb" ? HERB_ITEM_NAME : row.name;
            const stock =
              row.type === "holy_herb" ? consumables.holyHerbStock : consumables.itemStock[row.name] ?? 0;
            return (
              <div
                key={row.name}
                className="flex items-center gap-2 text-xs"
                data-consumable-stock={label}
                data-consumable-clickable={row.supported ? "true" : "false"}
              >
                <span className="flex-1">
                  {label} <span className="text-muted-foreground">- {item?.effect ?? "restores all residents' MP"}</span>
                  {row.supported ? null : (
                    <span className="text-amber-600 dark:text-amber-400" title={row.disabledReason ?? undefined}>
                      {" "}Not clickable in a battle.
                    </span>
                  )}
                </span>
                <NumberField
                  value={stock}
                  min={0}
                  max={999}
                  onChange={(count) =>
                    row.type === "holy_herb"
                      ? setConsumables({ ...consumables, holyHerbStock: count })
                      : setConsumableStock(row.name, count)
                  }
                  ariaLabel={`${label} stock`}
                />
              </div>
            );
          })}
        </CardContent>
      </Card>

      {teamMessage ? (
        <p className="text-xs text-muted-foreground" data-team-message>
          {teamMessage}
        </p>
      ) : null}

      <Card data-builder-issues>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Eye className="h-4 w-4" /> 5 - Check the team
          </CardTitle>
          <CardDescription>
            {setup ? `${setup.playerTeam.length} unit(s) ready` : "not runnable yet"} · {errors.length} error(s) ·{" "}
            {otherIssues.length} item(s) to review
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!sharedData ? <p className="text-xs text-muted-foreground">Loading the shared job/equipment/skill data...</p> : null}
          {errors.length > 0 ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-medium">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                Fix these before running
              </div>
              <IssueList issues={errors} marker="setup-error" />
            </div>
          ) : null}

          {otherIssues.length > 0 ? (
            <details className="rounded-md border border-border/60 bg-muted/10 px-2 py-1.5" data-issue-details>
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                {otherIssues.length} item(s) to review
              </summary>
              <div className="mt-2">
                <IssueList issues={otherIssues} marker="setup-info" limit={4} />
              </div>
            </details>
          ) : null}

          {errors.length === 0 && otherIssues.length === 0 ? <p className="text-xs text-muted-foreground">Ready.</p> : null}

          <details className="rounded-md border px-3 py-2 text-xs">
            <summary className="cursor-pointer">Battle time limit: {battleSeconds} seconds</summary>
            <label className="mt-3 flex items-center gap-3">
              Maximum simulated seconds
              <Input type="text" inputMode="numeric" aria-label="Maximum simulated seconds"
                className="w-24" value={secondsInput ?? String(battleSeconds)} disabled={runState.status === "running"}
                onChange={(event) => setSecondsInput(event.target.value.replace(/[^\d]/g, ""))}
                onBlur={() => { if (secondsInput && Number.isFinite(Number(secondsInput))) setBattleSeconds(Math.max(1, Math.min(3600, Math.trunc(Number(secondsInput))))); setSecondsInput(null); }} />
            </label>
            <p className="mt-2 text-muted-foreground">If neither team wins within this limit, the result remains unfinished. Increase the limit to simulate a longer fight.</p>
          </details>
          <p className="text-xs text-muted-foreground">
            Battle ticks run on this device as you watch. The first run downloads the browser combat engine.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={previewFormation}
              disabled={previewState.status === "loading" || !setup}
              data-action="preview-formation"
              data-preview-state={previewState.status}
            >
              {previewState.status === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
              Preview formation
            </Button>
            <Button
              type="button"
              onClick={runCurrentBattle}
              disabled={runState.status === "running" || !setup}
              data-action="run-battle"
              data-run-state={runState.status}
            >
              {runState.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {runState.status === "running" ? "Running battle..." : "Run Battle"}
            </Button>
            <span className="text-xs text-muted-foreground">
              {variant.title} · encounter {draft.encounterId} · {draft.characters.length} unit(s)
            </span>
            <Link href="/battle-setup" className="text-xs font-medium underline">
              Raw setup editor
            </Link>
            <Link href="/battle-replay?mode=generated" className="text-xs font-medium underline">
              Generated replay
            </Link>
          </div>

          {previewError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" data-preview-error>
              <p className="font-medium">{previewError.message}</p>
              {previewError.details.length > 0 ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {previewError.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {runError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" data-run-error>
              <p className="font-medium">{runError.message}</p>
              {runError.details.length > 0 ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {runError.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {runState.status === "running" ? (
            <p className="text-xs text-muted-foreground" data-run-progress>
              Loading the combat engine in your browser and starting this fight. Duplicate submissions are disabled.
            </p>
          ) : null}

          {preview ? (
            <div className="space-y-3" data-preview-result>
              <table className="w-full text-xs" data-preview-table>
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-0.5 pr-2 font-medium">Unit</th>
                    <th className="py-0.5 pr-2 font-medium">Side</th>
                    <th className="py-0.5 pr-2 font-medium">Owner</th>
                    <th className="py-0.5 pr-2 font-medium">Cell</th>
                    <th className="py-0.5 font-medium">HP</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.units.map((unit, index) => (
                    <PreviewUnitRow key={unit.name + "-" + index} unit={unit} />
                  ))}
                </tbody>
              </table>
              <div className="text-xs" data-pet-limits>
                <p className="font-medium">Pet capacity reported by the backend</p>
                <ul className="space-y-0.5 text-muted-foreground">
                  {preview.petLimits.map((limit) => (
                    <li key={limit.owner} data-pet-limit-owner={limit.owner}>
                      {limit.owner}: {limit.attachedPets === null ? "unknown" : limit.attachedPets} attached - capacity{" "}
                      {limit.maxPets === null ? "unknown (not asserted)" : limit.maxPets}
                    </li>
                  ))}
                </ul>
              </div>
              {preview.diagnostics.length > 0 ? (
                <ul className="space-y-0.5 text-xs text-muted-foreground" data-preview-diagnostics>
                  {preview.diagnostics.map((diagnostic, index) => (
                    <li key={diagnostic.code + "-" + index}>
                      <span className="font-mono">{diagnostic.code}</span> - {diagnostic.message}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card data-limitations-card>
        <details data-limitations-details>
          <summary className="cursor-pointer list-none px-6 py-4 text-base font-semibold">
            Simulator limits
          </summary>
          <div className="space-y-2 px-6 pb-6 text-xs text-muted-foreground">
          <p>Rewards and chest totals are provisional. Some enemy art and effects are still missing.</p>
          </div>
        </details>
      </Card>
    </div>
  );
}
