import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { AlertTriangle, Boxes, Download, Play, RefreshCw, Square } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ka/page-header";
import {
  cancelStrategySearchJob,
  downloadJson,
  fetchStrategySearchDefaults,
  fetchStrategySearchJob,
  formatStrategyScore,
  replayStrategyScenario,
  startStrategySearchJob,
  STRATEGY_SEARCH_AXES,
  StrategySearchApiError,
  strategyCandidateMetric,
  type StrategyCandidateSummary,
  type StrategyJobSnapshot,
  type StrategySearchDefaults,
  type StrategySearchRequest,
  type StrategySearchSeed,
  type StrategySearchUnavailable,
} from "@/lib/strategy-search";

/**
 * Local Strategy Search page.
 *
 * The engine module (`KA-Website/tools/recovery/strategy_search.py`) owns every game default,
 * metric and ranking rule. This page only:
 *   * loads the engine's own `default_request()` and offers bounded edits on top of it;
 *   * starts one local job at a time, polls it, and cancels cooperatively;
 *   * renders the returned summaries, downloads the best scenario, and replays the best candidate
 *     through the existing `/api/battle-run` -> generated replay path.
 *
 * When the engine module is not installed the page shows the precise 503 reason. It never shows
 * placeholder scores and never claims a best real box yield.
 */

type NumberFieldProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  attribute?: Record<string, string | number>;
};

function NumberField({ label, value, min, max, onChange, disabled, attribute }: NumberFieldProps) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        className="h-8 w-28 text-xs"
        min={min}
        max={max}
        value={Number.isFinite(value) ? value : min}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        {...attribute}
      />
      <p className="text-[10px] text-muted-foreground">
        {min}–{max}
      </p>
    </div>
  );
}

function formatNumber(value: number | null, digits = 3): string {
  if (value === null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function formatCount(value: number | null): string {
  return value === null ? "—" : String(value);
}

/**
 * Compact, player-facing summary of one engine entry (best candidate or baseline). The engine's
 * lexicographic score stays an array; the raw entry (full scenario plus every evaluation) is only
 * shown when the caller asks for it, collapsed, so the default card stays readable.
 */
function CandidateSummaryCard({
  title,
  candidate,
  showRaw = false,
}: {
  title: string;
  candidate: StrategyCandidateSummary | null;
  showRaw?: boolean;
}) {
  if (!candidate) {
    return (
      <div className="rounded-md border p-2" data-candidate-card={title}>
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{title}</p>
        <p className="text-muted-foreground">not reported</p>
      </div>
    );
  }
  const wins = strategyCandidateMetric(candidate, "wins");
  const prizeCallbacks = strategyCandidateMetric(candidate, "preVerdictPrizeCallbacks");
  const seedRuns = strategyCandidateMetric(candidate, "runsCompleted");
  const seedsRequested = strategyCandidateMetric(candidate, "seedsRequested");
  return (
    <div className="rounded-md border p-2" data-candidate-card={title}>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{title}</p>
      <p className="font-mono">{candidate.candidateId}</p>
      <p>score {formatStrategyScore(candidate.score)}</p>
      <p className="text-[10px] text-muted-foreground">
        wins {formatCount(wins)} · prize callbacks {formatCount(prizeCallbacks)} · seed runs{" "}
        {formatCount(seedRuns)}/{formatCount(seedsRequested)}
      </p>
      <p className="text-[10px] text-muted-foreground">
        full seed bank completed:{" "}
        {candidate.comparable === null ? "—" : candidate.comparable ? "yes" : "no"}
        {candidate.beatsBaseline ? " · beats baseline" : ""}
      </p>
      {candidate.axis ? (
        <p className="text-[10px] text-muted-foreground">axis {candidate.axis}</p>
      ) : null}
      {candidate.unrankedReason ? (
        <p className="text-[10px] text-amber-600">not ranked: {candidate.unrankedReason}</p>
      ) : null}
      {candidate.scoreDefinition ? (
        <p className="mt-0.5 text-[10px] text-muted-foreground">{candidate.scoreDefinition}</p>
      ) : null}
      {showRaw ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-[10px] text-muted-foreground">
            raw entry (scenario + evaluations)
          </summary>
          <pre className="mt-1 max-h-56 overflow-auto rounded bg-muted/40 p-2 text-[10px]">
            {JSON.stringify(candidate, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

type ScenarioSummary =
  | {
      ok: true;
      schema: string;
      encounterId: unknown;
      ownUnits: number;
      skillSlots: number;
      units: Array<{
        name: string;
        human: boolean;
        weaponId: number;
        skills: number[];
        invocationLevels: number[];
      }>;
    }
  | { ok: false; message: string };

/**
 * Client-side shape check for a pasted base scenario. It only helps the caller see what they pasted
 * and catch obvious mistakes early; the engine re-validates the real scenario and returns 422
 * `problems` for anything it rejects, and this page never rewrites the scenario.
 */
function describeScenario(text: string): ScenarioSummary | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch (error) {
    return { ok: false, message: `not valid JSON: ${String(error)}` };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, message: "the scenario must be a JSON object" };
  }
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.ownUnits)) {
    return { ok: false, message: "the scenario needs an ownUnits array" };
  }
  const numbers = (raw: unknown): number[] =>
    Array.isArray(raw) ? raw.filter((entry): entry is number => typeof entry === "number") : [];
  const units = record.ownUnits
    .filter(
      (unit): unit is Record<string, unknown> =>
        Boolean(unit) && typeof unit === "object" && !Array.isArray(unit),
    )
    .map((unit) => ({
      name: typeof unit.name === "string" ? unit.name : "(unnamed)",
      human: unit.human === true,
      weaponId: typeof unit.weaponId === "number" ? unit.weaponId : -1,
      skills: numbers(unit.skills),
      invocationLevels: numbers(unit.invocationLevels),
    }));
  return {
    ok: true,
    schema: typeof record.schema === "string" ? record.schema : "(missing schema)",
    encounterId: record.encounterId ?? null,
    ownUnits: record.ownUnits.length,
    skillSlots: units.reduce((sum, unit) => sum + unit.skills.length, 0),
    units,
  };
}

export default function StrategySearchPage() {
  const [, navigate] = useLocation();
  const [defaults, setDefaults] = useState<StrategySearchDefaults | StrategySearchUnavailable | null>(null);
  const [defaultsError, setDefaultsError] = useState<string | null>(null);

  const [objective, setObjective] = useState<string>("");
  const [searchAxis, setSearchAxis] = useState<string>("");
  const [candidateCount, setCandidateCount] = useState<number>(4);
  const [searchSeed, setSearchSeed] = useState<number>(1);
  const [tickLimit, setTickLimit] = useState<number>(7000);
  const [seeds, setSeeds] = useState<StrategySearchSeed[]>([{ mathSeed: 7, libSeed: 8 }]);
  const [baseScenarioText, setBaseScenarioText] = useState<string>("");

  const [job, setJob] = useState<StrategyJobSnapshot | null>(null);
  const [actionError, setActionError] = useState<{ message: string; details: string[] } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const loadDefaults = useCallback(async () => {
    setDefaultsError(null);
    try {
      const loaded = await fetchStrategySearchDefaults();
      setDefaults(loaded);
      if (loaded.available) {
        const request = loaded.request;
        setObjective(typeof request.objective === "string" ? request.objective : "");
        setSearchAxis(typeof request.searchAxis === "string" ? request.searchAxis : "both");
        if (typeof request.candidateCount === "number") setCandidateCount(request.candidateCount);
        if (typeof request.searchSeed === "number") setSearchSeed(request.searchSeed);
        if (typeof request.tickLimit === "number") setTickLimit(request.tickLimit);
        if (Array.isArray(request.seeds) && request.seeds.length > 0) {
          setSeeds(
            request.seeds.map((seed) => ({ mathSeed: Number(seed.mathSeed), libSeed: Number(seed.libSeed) })),
          );
        }
      }
    } catch (error) {
      setDefaults(null);
      setDefaultsError(String(error instanceof Error ? error.message : error));
    }
  }, []);

  useEffect(() => {
    void loadDefaults();
  }, [loadDefaults]);

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearTimeout(pollRef.current);
  }, []);

  const bounds = defaults && defaults.available ? defaults.bounds : null;
  const engineRequest = defaults && defaults.available ? defaults.request : null;
  const engineBaseScenario = useMemo(() => {
    if (!engineRequest) return null;
    const scenario = (engineRequest as StrategySearchRequest).baseScenario;
    return scenario === undefined ? null : scenario;
  }, [engineRequest]);
  const engineBaseSource = useMemo(() => {
    if (!engineRequest) return null;
    const source = (engineRequest as Record<string, unknown>).baseScenarioSource;
    return typeof source === "string" ? source : null;
  }, [engineRequest]);
  const engineNotes = useMemo(() => {
    if (!engineRequest) return [] as string[];
    const notes = (engineRequest as Record<string, unknown>).notes;
    return Array.isArray(notes) ? notes.filter((entry): entry is string => typeof entry === "string") : [];
  }, [engineRequest]);
  const scenarioSummary = useMemo(() => describeScenario(baseScenarioText), [baseScenarioText]);
  const scenarioInvalid = scenarioSummary !== null && !scenarioSummary.ok;

  const running = job != null && (job.status === "queued" || job.status === "running" || job.status === "cancelling");

  /** Build the request the page will send. Defaults come from the engine, never from this file. */
  const buildRequest = useCallback((): { ok: true; request: StrategySearchRequest } | { ok: false; message: string } => {
    if (!defaults || !defaults.available) {
      return { ok: false, message: "The strategy search engine is not available locally." };
    }
    const request: StrategySearchRequest = { ...defaults.request };
    if (objective) request.objective = objective;
    if (searchAxis) request.searchAxis = searchAxis;
    request.candidateCount = candidateCount;
    request.searchSeed = searchSeed;
    request.tickLimit = tickLimit;
    request.seeds = seeds;
    const trimmed = baseScenarioText.trim();
    if (trimmed) {
      try {
        request.baseScenario = JSON.parse(trimmed);
      } catch (error) {
        return { ok: false, message: `Base scenario is not valid JSON: ${String(error)}` };
      }
    }
    return { ok: true, request };
  }, [baseScenarioText, candidateCount, defaults, objective, searchAxis, searchSeed, seeds, tickLimit]);
  const requestPreview = useMemo(() => buildRequest(), [buildRequest]);

  const startSearch = useCallback(async () => {
    const built = buildRequest();
    if (!built.ok) {
      setActionError({ message: built.message, details: [] });
      return;
    }
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const snapshot = await startStrategySearchJob(built.request);
      setJob(snapshot);
    } catch (error) {
      if (error instanceof StrategySearchApiError) {
        setActionError({ message: error.message, details: [error.code, ...error.problems] });
      } else {
        setActionError({
          message: "The local strategy-search endpoint did not return a usable job.",
          details: [String(error instanceof Error ? error.message : error)],
        });
      }
    } finally {
      setBusy(false);
    }
  }, [buildRequest]);

  const cancelSearch = useCallback(async () => {
    if (!job) return;
    setBusy(true);
    try {
      const snapshot = await cancelStrategySearchJob(job.jobId);
      setJob(snapshot);
      setNotice("Cancellation requested. The engine stops between simulations.");
    } catch (error) {
      setActionError({
        message: "The cancel request failed.",
        details: [String(error instanceof Error ? error.message : error)],
      });
    } finally {
      setBusy(false);
    }
  }, [job]);

  /* Poll the active job; one timer, cleaned up when the job settles. */
  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running" && job.status !== "cancelling")) {
      return;
    }
    const jobId = job.jobId;
    const timer = window.setTimeout(async () => {
      try {
        const snapshot = await fetchStrategySearchJob(jobId);
        setJob(snapshot);
      } catch (error) {
        setActionError({
          message: "Polling the job failed.",
          details: [String(error instanceof Error ? error.message : error)],
        });
      }
    }, 1000);
    pollRef.current = timer;
    return () => window.clearTimeout(timer);
  }, [job]);

  const best = job?.result?.best ?? null;
  const searchSpace = job?.result?.searchSpace ?? null;
  const resultCandidates = job?.result?.candidates ?? [];
  const zeroVariants =
    job?.result !== null && job?.result !== undefined
      ? resultCandidates.length === 0 || searchSpace?.candidateCountGenerated === 0
      : false;
  const degenerateReasons =
    searchSpace === null
      ? []
      : [
          ...(searchSpace.degenerateReason ? [searchSpace.degenerateReason] : []),
          ...searchSpace.axes
            .filter((axis) => axis.degenerateReason)
            .map((axis) => `${axis.axis}: ${axis.degenerateReason}`),
        ];

  const downloadBestScenario = useCallback(() => {
    if (!best || best.scenario === undefined) return;
    downloadJson(`strategy-best-${best.candidateId}.json`, best.scenario);
  }, [best]);

  const replayBest = useCallback(async () => {
    if (!best || best.scenario === undefined) return;
    setBusy(true);
    setActionError(null);
    try {
      const replayed = await replayStrategyScenario(best.scenario, `Strategy search best: ${best.candidateId}`);
      if (!replayed.ok) {
        setActionError({
          message: "The best candidate could not be replayed through the authoritative runner.",
          details: [replayed.message],
        });
        return;
      }
      navigate("/battle-replay?mode=generated");
    } finally {
      setBusy(false);
    }
  }, [best, navigate]);

  const defaultsState = defaults === null ? (defaultsError ? "error" : "loading") : defaults.available ? "available" : "unavailable";

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6" data-strategy-search-root data-defaults-state={defaultsState}>
      <PageHeader
        icon={<Boxes className="h-5 w-5" />}
        title="Strategy Search"
        actions={
          <Badge variant="secondary" className="font-mono text-[10px]">
            local-only · engine-owned metrics
          </Badge>
        }
      >
        <p>
          Bounded local search over two legal axes — formation order and skill activation order — run
          by the recovered combat engine. Defaults, scores and rankings come from the engine module;
          this page never invents them.
        </p>
        <p>
          Battle setup and generated replays live on{" "}
          <Link href="/battle-setup" className="underline" data-action="goto-battle-setup">
            /battle-setup
          </Link>
          . This page is the box-farming search front end for the same runner.
        </p>
      </PageHeader>

      {actionError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" data-strategy-search-error>
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-3.5 w-3.5" />
            {actionError.message}
          </p>
          {actionError.details.length > 0 ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 font-mono">
              {actionError.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {notice ? (
        <p className="text-xs text-muted-foreground" data-strategy-search-notice>
          {notice}
        </p>
      ) : null}

      {defaultsState === "unavailable" && defaults && !defaults.available ? (
        <Card className="border-amber-500/40" data-strategy-search-unavailable>
          <CardHeader>
            <CardTitle className="text-base">Search engine not available yet</CardTitle>
            <CardDescription>
              The local API answered a precise unavailability; no scores are shown because none exist.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <p className="font-mono" data-unavailable-code>{defaults.code}</p>
            <p>{defaults.message}</p>
            {defaults.expectedPath ? (
              <p className="font-mono text-muted-foreground">expected: {defaults.expectedPath}</p>
            ) : null}
            <p className="text-muted-foreground">
              Battle setup and generated replays keep working without the search engine.
            </p>
            <Button type="button" size="sm" variant="outline" onClick={() => void loadDefaults()} data-action="reload-defaults">
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> Check again
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {defaultsState === "error" ? (
        <Card className="border-destructive/40" data-strategy-search-defaults-error>
          <CardContent className="space-y-2 pt-6 text-xs">
            <p>The local API could not be reached for search defaults.</p>
            <p className="font-mono">{defaultsError}</p>
            <p className="text-muted-foreground">
              Start the local stack (see <code>KA-Website/scripts/start-local-stack.ps1</code>) so the
              local API and Vite dev server are both running.
            </p>
            <Button type="button" size="sm" variant="outline" onClick={() => void loadDefaults()} data-action="reload-defaults">
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> Retry
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {defaultsState === "available" && defaults && defaults.available ? (
        <Card data-strategy-search-form>
          <CardHeader>
            <CardTitle className="text-base">Request</CardTitle>
            <CardDescription>
              Pre-filled from the engine's own defaults. Objective, search axis and limits below are
              the only knobs; the base scenario stays the engine default unless you paste one.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <Label className="text-xs">Objective</Label>
                <Select value={objective} onValueChange={setObjective} disabled={running}>
                  <SelectTrigger className="h-8 w-56 text-xs" data-objective-select>
                    <SelectValue placeholder="engine default" />
                  </SelectTrigger>
                  <SelectContent>
                    {defaults.bounds.objectives.map((entry) => (
                      <SelectItem key={entry} value={entry}>
                        {entry}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-muted-foreground">
                  engine default: {String(defaults.request.objective ?? "—")}
                </p>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Search axis</Label>
                <Select value={searchAxis} onValueChange={setSearchAxis} disabled={running}>
                  <SelectTrigger className="h-8 w-40 text-xs" data-axis-select>
                    <SelectValue placeholder="engine default" />
                  </SelectTrigger>
                  <SelectContent>
                    {STRATEGY_SEARCH_AXES.map((entry) => (
                      <SelectItem key={entry} value={entry}>
                        {entry}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-muted-foreground">
                  engine default: {String(defaults.request.searchAxis ?? "—")}
                </p>
              </div>
              <NumberField
                label="candidate budget"
                value={candidateCount}
                min={1}
                max={defaults.bounds.candidateCount}
                onChange={setCandidateCount}
                disabled={running}
                attribute={{ "data-field": "candidate-count" }}
              />
              <NumberField
                label="reproducible seed (search)"
                value={searchSeed}
                min={0}
                max={2147483647}
                onChange={setSearchSeed}
                disabled={running}
                attribute={{ "data-field": "search-seed" }}
              />
              <NumberField
                label="simulation horizon (ticks)"
                value={tickLimit}
                min={1}
                max={defaults.bounds.tickLimit}
                onChange={setTickLimit}
                disabled={running}
                attribute={{ "data-field": "tick-limit" }}
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Label className="text-xs">Reproducible seeds (simulation, mathSeed / libSeed)</Label>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[10px]"
                  disabled={running || seeds.length >= defaults.bounds.seeds}
                  onClick={() => setSeeds((current) => [...current, { mathSeed: 7, libSeed: 8 }])}
                  data-action="add-seed"
                >
                  add
                </Button>
                {seeds.length > 1 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px]"
                    disabled={running}
                    onClick={() => setSeeds((current) => current.slice(0, -1))}
                    data-action="remove-seed"
                  >
                    remove
                  </Button>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-2" data-seed-list>
                {seeds.map((seed, index) => (
                  <div key={index} className="flex items-center gap-1" data-seed-index={index}>
                    <Input
                      type="number"
                      className="h-7 w-20 text-xs"
                      value={seed.mathSeed}
                      disabled={running}
                      onChange={(event) =>
                        setSeeds((current) =>
                          current.map((entry, entryIndex) =>
                            entryIndex === index ? { ...entry, mathSeed: Number(event.target.value) } : entry,
                          ),
                        )
                      }
                    />
                    <span className="text-xs text-muted-foreground">/</span>
                    <Input
                      type="number"
                      className="h-7 w-20 text-xs"
                      value={seed.libSeed}
                      disabled={running}
                      onChange={(event) =>
                        setSeeds((current) =>
                          current.map((entry, entryIndex) =>
                            entryIndex === index ? { ...entry, libSeed: Number(event.target.value) } : entry,
                          ),
                        )
                      }
                    />
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-muted-foreground">max {defaults.bounds.seeds} seed pairs</p>
            </div>

            <div className="space-y-1">
              <Label className="text-xs">Base scenario (JSON, optional)</Label>
              <Textarea
                className="min-h-16 font-mono text-[11px]"
                placeholder="leave empty to use the engine's default base scenario"
                value={baseScenarioText}
                disabled={running}
                onChange={(event) => setBaseScenarioText(event.target.value)}
                data-field="base-scenario"
              />
              {scenarioSummary && !scenarioSummary.ok ? (
                <p className="text-[11px] text-destructive" data-scenario-problem>
                  {scenarioSummary.message}
                </p>
              ) : null}
              {scenarioSummary && scenarioSummary.ok ? (
                <div className="rounded-md border bg-muted/30 p-2 text-[11px]" data-scenario-summary>
                  <p>
                    schema <span className="font-mono">{scenarioSummary.schema}</span> ·{" "}
                    {scenarioSummary.ownUnits} own unit
                    {scenarioSummary.ownUnits === 1 ? "" : "s"} · {scenarioSummary.skillSlots} skill
                    slot{scenarioSummary.skillSlots === 1 ? "" : "s"} · encounter{" "}
                    <span className="font-mono">{String(scenarioSummary.encounterId)}</span>
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {scenarioSummary.units.map((unit, index) => (
                      <li key={`${unit.name}-${index}`} className="font-mono">
                        {unit.name} · {unit.human ? "human" : "monster"} · weapon {unit.weaponId} ·
                        skills [{unit.skills.join(", ")}] · invocation [
                        {unit.invocationLevels.join(", ")}]
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-muted-foreground">
                    A pasted scenario is sent verbatim; the engine re-validates it and reports its own
                    problems. Edit unit order, skills and invocation levels here to aim the search at
                    a scenario you want to compare.
                  </p>
                </div>
              ) : null}
              <p className="text-[10px] text-muted-foreground">
                {engineBaseScenario
                  ? "engine default base scenario present (kept when this box is empty)"
                  : engineBaseSource
                    ? `empty box uses the engine's frozen default: ${engineBaseSource}`
                  : "engine reports no default base scenario"}
              </p>
            </div>

            {engineNotes.length > 0 ? (
              <div className="rounded-md border bg-muted/30 p-2 text-[11px]" data-engine-notes>
                <p className="font-medium">Engine notes</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                  {engineNotes.map((entry) => (
                    <li key={entry}>{entry}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                onClick={() => void startSearch()}
                disabled={running || busy || scenarioInvalid}
                data-action="start-search"
                data-job-state={job?.status ?? "none"}
              >
                <Play className="mr-1 h-3.5 w-3.5" />
                {running ? "Searching…" : "Start search"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => void cancelSearch()}
                disabled={!running || busy}
                data-action="cancel-search"
              >
                <Square className="mr-1 h-3.5 w-3.5" /> Cancel
              </Button>
              {job ? (
                <span className="text-xs text-muted-foreground" data-job-summary>
                  job {job.jobId.slice(0, 8)} · {job.status}
                  {job.progress?.completed != null && job.progress?.total != null
                    ? ` · ${job.progress.completed}/${job.progress.total}`
                    : ""}
                </span>
              ) : null}
            </div>

            <details className="text-[11px]">
              <summary className="cursor-pointer text-muted-foreground">request that will be sent</summary>
              <pre className="mt-1 max-h-56 overflow-auto rounded bg-muted/40 p-2" data-request-preview>
                {JSON.stringify(
                  requestPreview.ok ? requestPreview.request : { error: requestPreview.message },
                  null,
                  2,
                )}
              </pre>
            </details>
          </CardContent>
        </Card>
      ) : null}

      {defaultsState === "available" ? (
        <Card data-strategy-search-axes>
          <CardHeader>
            <CardTitle className="text-base">Search axes</CardTitle>
            <CardDescription>
              The engine varies legal source inputs only and never adds a skill, a stat, a level, a
              party member or a piece of equipment a unit did not already carry.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-xs text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">formation</span> — permutes the{" "}
              <code>ownUnits</code> input order inside a native formation tie (same priority, same
              effective defense). Permutations that cannot move a fighter are never emitted, and
              content-identical units are one group.
            </p>
            <p>
              <span className="font-medium text-foreground">skill-order</span> — permutes the
              activation priority order of a unit's own order-relevant skills, keeping every skill
              paired with its own invocation level and leaving ownership, stats and equipment
              untouched. Set <code>searchAxis</code> to <code>formation</code> or{" "}
              <code>skill-order</code> to isolate one axis.
            </p>
            <p>
              A candidate is ranked only when it and the baseline both finished every requested seed
              at the same horizon; a censored or errored run is reported with its honest partial
              score but can never be best or an improvement.
            </p>
          </CardContent>
        </Card>
      ) : null}

      {job && job.status === "failed" && job.error ? (
        <Card className="border-destructive/40" data-strategy-search-failed>
          <CardContent className="space-y-1 pt-6 text-xs">
            <p className="font-medium">The search job failed.</p>
            <p className="font-mono">{job.error.code}</p>
            <p>{job.error.message}</p>
          </CardContent>
        </Card>
      ) : null}

      {job && (job.status === "completed" || job.status === "cancelled") && job.result ? (
        <Card data-strategy-search-result data-result-status={job.result.status}>
          <CardHeader>
            <CardTitle className="text-base">Result</CardTitle>
            <CardDescription>
              schema {job.result.schema} · status {job.result.status} · {job.result.candidates.length}{" "}
              candidate{job.result.candidates.length === 1 ? "" : "s"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-xs">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-md border p-2">
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground">best candidate</p>
                <p className="font-mono" data-best-candidate>{best?.candidateId ?? "—"}</p>
                <p data-best-score>score {formatStrategyScore(best?.score ?? null)}</p>
                <p className="text-[10px] text-muted-foreground">
                  wins {formatCount(strategyCandidateMetric(best, "wins"))} · prize callbacks{" "}
                  {formatCount(strategyCandidateMetric(best, "preVerdictPrizeCallbacks"))} · seed runs{" "}
                  {formatCount(strategyCandidateMetric(best, "runsCompleted"))}/
                  {formatCount(strategyCandidateMetric(best, "seedsRequested"))}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  full seed bank completed:{" "}
                  {best?.comparable == null ? "—" : best.comparable ? "yes" : "no"}
                  {best?.beatsBaseline ? " · beats baseline" : ""}
                </p>
                {best?.axis ? (
                  <p className="text-[10px] text-muted-foreground">axis {best.axis}</p>
                ) : null}
                {best?.unrankedReason ? (
                  <p className="text-[10px] text-amber-600">not ranked: {best.unrankedReason}</p>
                ) : null}
                {best?.scoreDefinition ? (
                  <p className="mt-0.5 text-[10px] text-muted-foreground">{best.scoreDefinition}</p>
                ) : null}
                {best ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[10px] text-muted-foreground">
                      raw entry (scenario + evaluations)
                    </summary>
                    <pre className="mt-1 max-h-56 overflow-auto rounded bg-muted/40 p-2 text-[10px]">
                      {JSON.stringify(best, null, 2)}
                    </pre>
                  </details>
                ) : null}
              </div>
              <div className="rounded-md border p-2">
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground">improvement</p>
                <p>
                  {job.result.improvement === true
                    ? "yes — a candidate beats the baseline on the full seed bank"
                    : job.result.improvement === false
                      ? "no improvement found in this bounded run"
                      : formatValue(job.result.improvement)}
                </p>
                {job.result.improvements.length > 0 ? (
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    ranked above baseline: {job.result.improvements.join(", ")}
                  </p>
                ) : null}
              </div>
              <CandidateSummaryCard title="baseline" candidate={job.result.baseline} showRaw />
            </div>

            {zeroVariants ? (
              <div
                className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2"
                data-zero-variants
              >
                <p className="font-medium">No legal variants to compare</p>
                <p className="mt-1">
                  The engine generated {job.result.searchSpace?.candidateCountGenerated ?? 0}{" "}
                  candidates for this base scenario, so there is nothing to rank above the baseline.
                  It reports this instead of inventing variants.
                </p>
                {degenerateReasons.length > 0 ? (
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                    {degenerateReasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {job.result.searchSpace ? (
              <div className="space-y-1" data-search-space>
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  search axes (from the engine)
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="px-2 py-1">axis</th>
                        <th className="px-2 py-1">candidates</th>
                        <th className="px-2 py-1">distinct arrangements</th>
                        <th className="px-2 py-1">note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.result.searchSpace.axes.map((axis) => (
                        <tr key={axis.axis} className="border-b last:border-0" data-axis={axis.axis}>
                          <td className="px-2 py-1 font-mono">{axis.axis}</td>
                          <td className="px-2 py-1">{axis.arrangements ?? "—"}</td>
                          <td className="px-2 py-1">{axis.distinctArrangements ?? "—"}</td>
                          <td className="px-2 py-1 text-muted-foreground">
                            {axis.degenerateReason ?? "generated"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-muted-foreground">
                  requested {job.result.searchSpace.candidateCountRequested ?? "—"} · generated{" "}
                  {job.result.searchSpace.candidateCountGenerated ?? "—"} · runs{" "}
                  {job.result.searchSpace.completedRuns ?? "—"}/
                  {job.result.searchSpace.plannedRuns ?? "—"} · baseline finished full seed bank:{" "}
                  {String(job.result.searchSpace.baselineCompletedFullSeedBank ?? "—")}
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={downloadBestScenario}
                disabled={!best || best.scenario === undefined}
                data-action="download-best-scenario"
              >
                <Download className="mr-1 h-3.5 w-3.5" /> Best scenario JSON
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void replayBest()}
                disabled={!best || best.scenario === undefined || busy}
                data-action="replay-best"
              >
                <Play className="mr-1 h-3.5 w-3.5" /> Replay best candidate
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => job.result && downloadJson("strategy-search-result.json", job.result)}
                data-action="download-result"
              >
                <Download className="mr-1 h-3.5 w-3.5" /> Full result JSON
              </Button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs" data-candidate-table>
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="px-2 py-1">candidate</th>
                    <th className="px-2 py-1">axis</th>
                    <th className="px-2 py-1">score</th>
                    <th className="px-2 py-1">full bank</th>
                    <th className="px-2 py-1">seed runs</th>
                    <th className="px-2 py-1">wins</th>
                    <th className="px-2 py-1">prize callbacks</th>
                    <th className="px-2 py-1">note</th>
                  </tr>
                </thead>
                <tbody>
                  {job.result.candidates.map((candidate) => (
                    <tr key={candidate.candidateId} className="border-b last:border-0" data-candidate-id={candidate.candidateId}>
                      <td className="px-2 py-1 font-mono">
                        {candidate.candidateId}
                        {candidate.kind && candidate.kind !== "candidate" ? (
                          <span className="ml-1 text-[10px] text-muted-foreground">{candidate.kind}</span>
                        ) : null}
                        {candidate.provisional ? (
                          <Badge variant="secondary" className="ml-1 text-[9px]">
                            provisional
                          </Badge>
                        ) : null}
                      </td>
                      <td className="px-2 py-1 font-mono">{candidate.axis ?? "—"}</td>
                      <td className="px-2 py-1 font-mono">{formatStrategyScore(candidate.score)}</td>
                      <td className="px-2 py-1">
                        {candidate.comparable === null ? "—" : candidate.comparable ? "yes" : "no"}
                      </td>
                      <td className="px-2 py-1">
                        {formatCount(strategyCandidateMetric(candidate, "runsCompleted"))}/
                        {formatCount(strategyCandidateMetric(candidate, "seedsRequested"))}
                      </td>
                      <td className="px-2 py-1">
                        {formatCount(strategyCandidateMetric(candidate, "wins"))}
                      </td>
                      <td className="px-2 py-1">
                        {formatCount(strategyCandidateMetric(candidate, "preVerdictPrizeCallbacks"))}
                      </td>
                      <td className="px-2 py-1 text-[10px] text-muted-foreground">
                        {candidate.unrankedReason
                          ? candidate.unrankedReason
                          : candidate.beatsBaseline
                            ? "beats baseline"
                            : candidate.pairedBaselineScore != null
                              ? `paired baseline ${formatStrategyScore(candidate.pairedBaselineScore)}`
                              : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {job.result.limitations.length > 0 ? (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2" data-strategy-limitations>
                <p className="font-medium">Limitations (from the engine)</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {job.result.limitations.map((entry) => (
                    <li key={entry}>{entry}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {job.result.nativeValidationScope ? (
              <details className="text-[11px]">
                <summary className="cursor-pointer text-muted-foreground">native validation scope</summary>
                <pre className="mt-1 max-h-56 overflow-auto rounded bg-muted/40 p-2" data-native-validation-scope>
                  {JSON.stringify(job.result.nativeValidationScope, null, 2)}
                </pre>
              </details>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card data-strategy-search-caveats>
        <CardHeader>
          <CardTitle className="text-base">Model notes</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-xs text-muted-foreground">
          <p>
            Myriad Arrows: the bow requirement is enforced in combat — it is a bow skill
            (<code>requiredEquipType 8</code>), so <code>combat_skills.can_use_skill</code> refuses it
            unless the unit's weapon type is 8. Its bow visual feedback is incomplete; that is a
            presentation gap, not an unmodelled combat rule.
          </p>
          <p>Unverified: monster additional skill slots are not modelled.</p>
        </CardContent>
      </Card>
    </div>
  );
}
