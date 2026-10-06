/**
 * Encounter-aware operating regions.
 *
 * The encounter-aware search replaces the old "students" split and the per-value stat-track controls
 * with an experiment/reward/boundary surface: what question the search is asking, what work it has
 * been authorised to spend (whole runs, per purpose), what the earned portfolio actually measured
 * (with the partial mean, the unknowns and the resolved fraction kept apart from each other), and
 * which conditional points have been tested versus which remain unknown.
 *
 * Everything here is read from the host payload declared in `@/types/encounter-optimizer`, aligned to
 * the ACTUAL `strategy_encounter_search.Coordinator.report()`. No field is invented and no command is
 * sent on render or status: the migration preview is a pure read, and activation/rollback/budget
 * changes only happen from an explicit button press on a draft that was previewed exactly. When the
 * host build predates the encounter-aware payload the panel says so and leaves the legacy controls
 * untouched below.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToneBadge } from "@/components/ka/badges";
import {
  PAIRED_TEST_EXPLANATION,
  WHY_SPEND_MORE,
  humanClassification,
  humanFieldLabel,
  humanPublicationStatus,
  humanReferenceLabel,
  humanStatName,
  preservationTargetSentence,
  type EncounterFieldLabelContext,
  type EncounterFieldMapping,
  type EncounterHumanLabels,
} from "@/lib/encounter-human-labels";
import type {
  EncounterAllocations,
  EncounterAwareConfig,
  EncounterAwareQuestion,
  EncounterAwareSnapshot,
  EncounterBoundaryRow,
  EncounterChoice,
  EncounterCurrentWork,
  EncounterMigrationRow,
  EncounterMode,
  EncounterOptimizerPanelProps,
  EncounterPortfolioRow,
  EncounterPresentation,
  EncounterPurposeBudgets,
  EncounterPurposeKey,
  EncounterPurposeSpend,
  EncounterQuestionKind,
  EncounterQuestionSlot,
  EncounterStreamKey,
  EncounterTestedPoint,
  EncounterUnknownGap,
} from "@/types/encounter-optimizer";

/* ------------------------------------------------------------------ */
/* Guards and formatting                                               */
/* ------------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** Format a number, or say "unavailable" - never 0 - when the backend did not supply it. */
function formatNumber(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "unavailable";
}

function formatInt(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : "unavailable";
}

function formatPercent(value: number | null | undefined, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(digits)}%`
    : "unavailable";
}

type ParsedInteger = { value: number | null; error: string | null };

/**
 * Strict whole-number parse. A blank field stays blank (never replaced by a minimum or the old
 * config); a decimal, a sign or any other text is an ERROR, never silently stripped.
 */
function readInteger(raw: string): ParsedInteger {
  const text = raw.trim();
  if (text === "") return { value: null, error: null };
  if (!/^[+-]?\d+$/.test(text)) return { value: null, error: "whole number required" };
  const value = Number.parseInt(text, 10);
  if (!Number.isSafeInteger(value)) return { value: null, error: "whole number out of range" };
  return { value, error: null };
}

function normalizePortfolio(value: unknown): EncounterPortfolioRow[] {
  const record = asRecord(value);
  if (record) {
    return Object.entries(record).map(([key, entry]) => {
      const row = (asRecord(entry) ?? {}) as EncounterPortfolioRow;
      return { ...row, key: row.key ?? key };
    });
  }
  return asArray(value).map((entry, index) => {
    const row = (asRecord(entry) ?? {}) as EncounterPortfolioRow;
    return { ...row, key: row.key ?? `row-${index}` };
  });
}

function normalizeSpending(value: unknown): Array<[string, EncounterPurposeSpend]> {
  const record = asRecord(value);
  if (!record) return [];
  return Object.entries(record).map(([key, entry]) => [
    key,
    (asRecord(entry) ?? {}) as EncounterPurposeSpend,
  ]);
}

function normalizeMigrationRows(preview: unknown): EncounterMigrationRow[] {
  const record = asRecord(preview);
  const source = asArray(record?.ledger).length ? asArray(record?.ledger) : asArray(record?.rows);
  return source.map((entry, index) => {
    const row = (asRecord(entry) ?? {}) as EncounterMigrationRow;
    return { ...row, key: row.stream ?? row.key ?? `row-${index}` };
  });
}

/** One owner row of the host's whole-run budget matrix, keyed by the host's own purpose names. */
type BudgetMatrixRow = { owner: string; cells: Record<string, number | null> };
type BudgetMatrix = { columns: string[]; rows: BudgetMatrixRow[] };

/**
 * Read the host's per-owner/per-purpose whole-run budget matrix in the two shapes a coordinator may
 * publish: an owner -> purpose -> runs map, or a list of {owner, purpose, budget} rows. Nothing is
 * derived here: a cell the host did not publish stays null so the page can show a dash, not a 0.
 */
function normalizeBudgetMatrix(value: unknown): BudgetMatrix {
  const rows: BudgetMatrixRow[] = [];
  const columns: string[] = [];
  const addColumn = (key: string) => {
    if (!columns.includes(key)) columns.push(key);
  };
  const record = asRecord(value);
  if (record) {
    for (const [owner, entry] of Object.entries(record)) {
      const inner = asRecord(entry);
      if (inner) {
        const cells: Record<string, number | null> = {};
        for (const [purpose, cell] of Object.entries(inner)) {
          cells[purpose] = asNumber(cell);
          addColumn(purpose);
        }
        rows.push({ owner, cells });
      } else if (asNumber(entry) !== null) {
        rows.push({ owner, cells: { total: asNumber(entry) } });
        addColumn("total");
      }
    }
  } else {
    for (const entry of asArray(value)) {
      const row = asRecord(entry);
      if (!row) continue;
      const owner =
        asString(row.owner) ?? asString(row.ownerName) ?? asString(row.key) ?? asString(row.name);
      if (!owner) continue;
      const cells: Record<string, number | null> = {};
      const nested = asRecord(row.purposes) ?? asRecord(row.budgets) ?? asRecord(row.values);
      if (nested) {
        for (const [purpose, cell] of Object.entries(nested)) {
          cells[purpose] = asNumber(cell);
          addColumn(purpose);
        }
      } else {
        const purpose = asString(row.purpose) ?? asString(row.purposeKey) ?? "total";
        cells[purpose] =
          asNumber(row.budget) ?? asNumber(row.runs) ?? asNumber(row.authorized) ??
          asNumber(row.allocated) ?? asNumber(row.value);
        addColumn(purpose);
      }
      rows.push({ owner, cells });
    }
  }
  const orderIndex = (key: string) => {
    const index = (PURPOSE_ORDER as string[]).indexOf(key);
    return index === -1 ? PURPOSE_ORDER.length : index;
  };
  columns.sort((a, b) => orderIndex(a) - orderIndex(b) || (a < b ? -1 : a > b ? 1 : 0));
  return { columns, rows };
}

function readBoundaryRows(state: EncounterAwareSnapshot | null | undefined): EncounterBoundaryRow[] {
  const direct = asArray(state?.boundaries);
  if (direct.length) return direct.map((entry) => (asRecord(entry) ?? {}) as EncounterBoundaryRow);
  const regions = asRecord(state?.mechanicalRegions);
  if (!regions) return [];
  const points = asArray(regions.points);
  const gaps = asArray(regions.gaps);
  const length = Math.max(points.length, gaps.length);
  const rows: EncounterBoundaryRow[] = [];
  for (let index = 0; index < length; index += 1) {
    rows.push({
      testedPoints: asArray(points[index]) as EncounterTestedPoint[],
      unresolvedGaps: asArray(gaps[index]) as EncounterUnknownGap[],
    });
  }
  return rows;
}

/* ------------------------------------------------------------------ */
/* Vocabulary                                                          */
/* ------------------------------------------------------------------ */

/** The pure presentation payload, from the snapshot or the migration preview. */
function readPresentation(
  state: EncounterAwareSnapshot | null | undefined,
): EncounterPresentation | null {
  const preview = asRecord(state?.migrationPreview?.presentation);
  if (state?.enabled !== true && preview) return preview as EncounterPresentation;
  const direct = asRecord(state?.presentation);
  if (direct) return direct as EncounterPresentation;
  return preview ? (preview as EncounterPresentation) : null;
}

/**
 * A stable, order-independent view of a constraint document, so the draft signature and the host's
 * echoed preview compare the SAME fields and never mismatch only because two JS objects were built
 * in a different key order. Returns null when nothing was actually constrained.
 */
type CanonicalConstraints = {
  context: string | null;
  fixed: Array<[string, number]>;
  bounds: Array<[string, [number, number]]>;
};

function canonicalConstraints(value: unknown): CanonicalConstraints | null {
  const record = asRecord(value);
  if (!record) return null;
  const fixedRecord = asRecord(record.fixed) ?? {};
  const boundsRecord = asRecord(record.bounds) ?? {};
  const fixed: Array<[string, number]> = [];
  for (const [field, entry] of Object.entries(fixedRecord)) {
    const number = asNumber(entry);
    if (number !== null) fixed.push([field, number]);
  }
  const bounds: Array<[string, [number, number]]> = [];
  for (const [field, entry] of Object.entries(boundsRecord)) {
    if (!Array.isArray(entry) || entry.length !== 2) continue;
    const low = asNumber(entry[0]);
    const high = asNumber(entry[1]);
    if (low !== null && high !== null) bounds.push([field, [low, high]]);
  }
  const context = asString(record.context) ?? null;
  if (!fixed.length && !bounds.length && (context === null || context === "synthetic")) return null;
  fixed.sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));
  bounds.sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));
  return { context, fixed, bounds };
}

/** A strict, ordered list of whole-number reward thresholds. Blank means "no thresholds". */
function readIntegerList(raw: string): { values: number[]; error: string | null } {
  const text = raw.trim();
  if (text === "") return { values: [], error: null };
  const parts = text.split(/[\s,;]+/).filter((part) => part.length > 0);
  const values: number[] = [];
  for (const part of parts) {
    const parsed = readInteger(part);
    if (parsed.error || parsed.value === null) return { values: [], error: `"${part}" is not a whole number` };
    if (parsed.value < 0) return { values: [], error: `"${part}" is negative` };
    if (!values.includes(parsed.value)) values.push(parsed.value);
  }
  values.sort((left, right) => left - right);
  return { values, error: null };
}

/** A host-echoed threshold array, normalised to sorted unique whole numbers. */
function normalizeThresholdArray(value: unknown): number[] {
  const rows = Array.isArray(value) ? value : [];
  const values = new Set<number>();
  for (const entry of rows) {
    const number = asNumber(entry);
    if (number !== null && Number.isInteger(number) && number >= 0) values.add(number);
  }
  return [...values].sort((left, right) => left - right);
}

/** A finite number from either a top-level preview field or the preview's config object. */
function previewNumber(preview: unknown, key: string): number | null {
  const record = asRecord(preview);
  const config = asRecord(record?.config);
  return asNumber(record?.[key]) ?? asNumber(config?.[key]);
}

function previewString(preview: unknown, key: string): string | null {
  const record = asRecord(preview);
  const config = asRecord(record?.config);
  return asString(record?.[key]) ?? asString(config?.[key]);
}

const PURPOSE_ORDER: EncounterPurposeKey[] = [
  "improvement",
  "boundary",
  "support",
  "comparison",
  "exploration",
];

const PURPOSE_LABEL: Record<EncounterPurposeKey, string> = {
  improvement: "Improvement",
  boundary: "Boundary",
  support: "Support",
  comparison: "Comparison",
  exploration: "Exploration",
};

const STREAM_ORDER: EncounterStreamKey[] = [
  "community",
  "discovery",
  "rebel",
  "stumble",
  "mechanism",
];

const STREAM_LABEL: Record<EncounterStreamKey, string> = {
  community: "Community strategy",
  discovery: "Open discovery",
  rebel: "Rebellious cheater",
  stumble: "Stumbling to the truth",
  mechanism: "Breakthrough / reliability",
  average: "Mean earned (retired)",
};

const QUESTION_KIND_LABEL: Record<string, string> = {
  "fixed-build": "Fixed build",
  compensated: "Compensated",
  support: "Support",
};

const QUESTION_KIND_MEANING: Record<string, string> = {
  "fixed-build":
    "Holds the chosen stat at the tested value and moves nothing else. Answers whether that exact build still earns reliably.",
  compensated:
    "Holds the chosen stat at the tested value and lets the DPS ATK axis move to compensate. What it cannot compensate stays a gap, never a claim.",
  support:
    "A support question about HP, MP or DEF. It is frozen and measured, but it is never published as a compensated or filtered boundary claim.",
};

const QUESTION_KINDS: EncounterQuestionKind[] = ["fixed-build", "compensated", "support"];

/** Verified Community roles the backend translates into a canonical unit index. */
const ROLE_ORDER = ["dps", "healer", "fodder"];
const ROLE_LABEL: Record<string, string> = {
  dps: "DPS",
  healer: "Healer",
  fodder: "Fodder",
};

/** Verified stat words the backend maps to canonical parameter ids. */
const STAT_ORDER = ["atk", "lck", "spd", "dex", "hp", "mp", "def"];
const STAT_LABEL: Record<string, string> = {
  atk: "ATK",
  lck: "LCK",
  spd: "SPD",
  dex: "DEX",
  hp: "HP",
  mp: "MP",
  def: "DEF",
};
/** Support questions are limited to HP / MP / DEF, the fields the backend accepts there. */
const SUPPORT_STATS = ["hp", "mp", "def"];

/** The fail-closed sentence the coordinator appends when all-strategy is not wired. */
const ALL_STRATEGY_FRAGMENT = "all-strategy stream generators are not wired";

function purposeLabel(key: string): string {
  return PURPOSE_LABEL[key as EncounterPurposeKey] ?? key;
}

function streamLabel(key: string): string {
  return STREAM_LABEL[key as EncounterStreamKey] ?? key;
}

function kindLabel(kind: string | null | undefined): string {
  return kind ? (QUESTION_KIND_LABEL[kind] ?? kind) : "question";
}

function modeLabel(mode: string | null | undefined): string {
  if (mode === "community-first") return "Community-first";
  if (mode === "all-strategy") return "All-strategy";
  return mode ?? "not set";
}

function spendCell(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : "unavailable";
}

function allocationCell(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: 3 })
    : "unavailable";
}

/* ------------------------------------------------------------------ */
/* Small presentational pieces                                         */
/* ------------------------------------------------------------------ */

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="break-words text-xs text-foreground">{children}</dd>
    </div>
  );
}

function Unavailable() {
  return <span className="text-muted-foreground">unavailable</span>;
}

function SectionHeading({ children }: { children: ReactNode }) {
  return <h4 className="text-xs font-semibold">{children}</h4>;
}

function RawNumberInput({
  id,
  value,
  onChange,
  disabled = false,
  placeholder,
  ariaLabel,
}: {
  id?: string;
  value: string;
  onChange: (raw: string) => void;
  disabled?: boolean;
  placeholder?: string;
  ariaLabel?: string;
}) {
  return (
    <Input
      id={id}
      type="text"
      inputMode="numeric"
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className="h-9 text-center tabular-nums"
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function FieldError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="text-[11px] text-destructive" role="alert">
      {message}
    </p>
  );
}

/** Read-only resolvers the operator view threads into its sections. Nothing here mutates data. */
type OperatorLabels = {
  fieldLabel: (path: string | null | undefined) => string | null;
  referenceLabel: (referenceId: string | null | undefined) => string;
  policySummary: (policy: Record<string, unknown> | null | undefined) => string | null;
};

/** A one-line plain summary of the frozen run policy; the raw keys stay under technical details. */
function defaultPolicySummary(policy: Record<string, unknown> | null | undefined): string | null {
  if (!policy) return null;
  const parts: string[] = [];
  const finish = asString(policy.finishPolicy);
  if (finish) parts.push(`finishes on ${finish}`);
  const ticks = asNumber(policy.tickLimit);
  if (ticks !== null) parts.push(`${formatInt(ticks)}-tick limit`);
  const herb = asNumber(policy.holyHerbStock);
  if (herb !== null) parts.push(`holy herb stock ${formatInt(herb)}`);
  const start = asString(policy.startProfile);
  if (start) parts.push(`start profile ${start}`);
  return parts.length ? parts.join(", ") : null;
}

function StatusBadges({ row }: { row: EncounterPortfolioRow }) {
  const confirmed = asBoolean(row.confirmed);
  const degraded = asBoolean(row.degraded);
  const unresolved = asBoolean(row.unresolved);
  const status = asString(row.status);
  const synthetic = asBoolean(row.synthetic);
  const context = asString(row.context);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {confirmed === true ? <ToneBadge category="success">independently confirmed</ToneBadge> : null}
      {degraded === true ? (
        <ToneBadge category="warning">Below the reference-preservation target</ToneBadge>
      ) : null}
      {unresolved === true ? <ToneBadge category="warning">Still testing</ToneBadge> : null}
      {synthetic === true ? <ToneBadge category="muted">synthetic build</ToneBadge> : null}
      {synthetic === false ? (
        <ToneBadge category="muted">{context ? `${context}-effective` : "player-effective"}</ToneBadge>
      ) : null}
      {synthetic === null && !context ? <ToneBadge category="muted">domain unavailable</ToneBadge> : null}
      {asBoolean(row.playerUnknown) === true ? (
        <ToneBadge category="warning">reachability unknown</ToneBadge>
      ) : null}
      {status ? <ToneBadge category="muted">{humanClassification(status) ?? status}</ToneBadge> : null}
    </div>
  );
}

function PolicyBlock({ question }: { question: EncounterAwareQuestion }) {
  const policy = asRecord(question.policy);
  if (!policy) {
    return (
      <p className="text-[11px] text-muted-foreground" data-encounter-consumable-policy>
        Frozen policy: unavailable.
      </p>
    );
  }
  const entries = Object.entries(policy).filter(([key]) => key !== "note");
  return (
    <div className="space-y-1" data-encounter-consumable-policy>
      <p className="text-[11px] text-muted-foreground">
        Exact policy copied from the observed scenario (never a default):
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3">
        {entries.map(([key, value]) => (
          <Fact key={key} label={key}>
            {value === null || value === undefined
              ? "unavailable"
              : typeof value === "object"
                ? JSON.stringify(value)
                : String(value)}
          </Fact>
        ))}
      </dl>
      {asString(policy.note) ? (
        <p className="text-[10px] text-muted-foreground">{asString(policy.note)}</p>
      ) : null}
    </div>
  );
}

function QuestionSummary({
  question,
  labels,
}: {
  question: EncounterAwareQuestion | null | undefined;
  labels: OperatorLabels;
}) {
  if (!question) {
    return <p className="text-xs text-muted-foreground">No question is frozen right now.</p>;
  }
  const kind = asString(question.kind);
  const rawField = asString(question.field);
  const fieldText = labels.fieldLabel(rawField);
  const value = asNumber(question.value);
  const tolerance = asNumber(question.tolerance);
  const adjustable = asArray(question.adjustableFields)
    .map((entry) => asString(entry))
    .filter(Boolean) as string[];
  const adjustableWords = adjustable.map((path) => labels.fieldLabel(path) ?? path);
  const fixed = asRecord(question.fixedFields) ?? {};
  const policy = asRecord(question.policy);
  const policyLine = labels.policySummary(policy);
  return (
    <div className="space-y-3">
      <p className="text-sm">
        This question tests{" "}
        <span className="font-medium">
          {fieldText ?? (rawField ? "an unnamed field" : "no field")}
        </span>
        {value !== null ? (
          <>
            {" "}
            at value <span className="font-medium tabular-nums">{formatInt(value)}</span>
          </>
        ) : null}
        .
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ToneBadge category="muted">{kindLabel(kind)}</ToneBadge>
        {kind && QUESTION_KIND_MEANING[kind] ? (
          <Badge variant="outline" className="max-w-full whitespace-normal text-left text-[10px] font-normal">
            {QUESTION_KIND_MEANING[kind]}
          </Badge>
        ) : null}
      </div>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Fact label="What is tested">{fieldText ?? <Unavailable />}</Fact>
        <Fact label="Value being tested">
          {value !== null ? formatInt(value) : <Unavailable />}
        </Fact>
        <Fact label="Compared against">{labels.referenceLabel(question.referenceId ?? null)}</Fact>
      </dl>
      <div className="rounded-md border p-2 text-[11px]">
        <p className="font-medium">{preservationTargetSentence(tolerance)}</p>
        <p className="text-muted-foreground">
          A product choice (default 90%), not a measured range. Meeting it is one goal, not a verdict
          on the build.
        </p>
      </div>
      <div className="text-[11px] text-muted-foreground" data-encounter-adjustable-fields>
        {adjustableWords.length
          ? `${adjustableWords.join(" and ")} can adjust to compensate.`
          : "Nothing else is allowed to move; this question tests one field on its own."}
      </div>
      {policy ? (
        <details className="rounded-md border p-2" data-encounter-consumable-policy>
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            Technical details: exact frozen run policy
          </summary>
          {policyLine ? <p className="mt-2 text-[11px]">Run policy: {policyLine}.</p> : null}
          <div className="mt-2">
            <PolicyBlock question={question} />
          </div>
        </details>
      ) : (
        <p className="text-[11px] text-muted-foreground" data-encounter-consumable-policy>
          Frozen run policy: unavailable.
        </p>
      )}
      <details className="rounded-md border p-2" data-encounter-question-technical>
        <summary className="cursor-pointer text-[11px] text-muted-foreground">
          Technical details: canonical keys and revisions
        </summary>
        <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <Fact label="Canonical field path">
            <span className="font-mono text-[10px]">{rawField ?? <Unavailable />}</span>
          </Fact>
          <Fact label="Question id">
            <span className="font-mono text-[10px]">
              {asString(question.id) ? String(asString(question.id)).slice(0, 12) : <Unavailable />}
            </span>
          </Fact>
          <Fact label="Reference id">
            <span className="font-mono text-[10px]">{asString(question.referenceId) ?? <Unavailable />}</span>
          </Fact>
          <Fact label="Encounter revision">
            <span className="font-mono text-[10px]">
              {asString(question.encounterRevision)?.slice(0, 16) ?? <Unavailable />}
            </span>
          </Fact>
          <Fact label="Mechanics revision">
            <span className="font-mono text-[10px]">
              {asString(question.mechanicsRevision)?.slice(0, 16) ?? <Unavailable />}
            </span>
          </Fact>
          <Fact label="Held fixed (canonical)">
            {Object.entries(fixed).length
              ? Object.entries(fixed)
                  .map(([field, fixedValue]) => `${field} = ${formatInt(asNumber(fixedValue))}`)
                  .join(", ")
              : "nothing stated by the backend"}
          </Fact>
        </dl>
      </details>
    </div>
  );
}

function ProgressFacts({
  slot,
  progress,
}: {
  slot: EncounterQuestionSlot | null | undefined;
  progress: EncounterAwareSnapshot["progress"];
}) {
  const budget = asNumber(slot?.budget);
  const spent = asNumber(slot?.spent);
  const minimumPairs = asNumber(slot?.minimumPairs);
  const unspendable = asArray(progress?.unspendable);
  const confirmation = asRecord(progress?.confirmation);
  const publication = asRecord(progress?.publication);
  const confirmationPlanned = asNumber(confirmation?.planned);
  const confirmationCompleted = asNumber(confirmation?.completed);
  const rawPublication = asString(publication?.status);
  const publicationStatus = humanPublicationStatus(rawPublication);
  return (
    <div className="space-y-3" data-encounter-progress>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Fact label="This question: runs completed">
          {spent !== null || budget !== null ? `${formatInt(spent)} of ${formatInt(budget)}` : <Unavailable />}
        </Fact>
        <Fact label="Paired tests needed">
          {minimumPairs !== null ? formatInt(minimumPairs) : <Unavailable />}
        </Fact>
        <Fact label="Queued work items">
          {asNumber(progress?.queueLength) !== null ? formatInt(asNumber(progress?.queueLength)) : <Unavailable />}
        </Fact>
      </dl>
      <p className="text-[11px] text-muted-foreground" data-encounter-paired-test-note>
        {PAIRED_TEST_EXPLANATION}
      </p>
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        {asBoolean(progress?.idle) === true ? (
          <ToneBadge category="muted">idle: no authorised useful work</ToneBadge>
        ) : null}
        {asBoolean(progress?.blocked) === true ? <ToneBadge category="warning">blocked</ToneBadge> : null}
        {asBoolean(progress?.freshLibrary) === true ? <ToneBadge category="muted">fresh library</ToneBadge> : null}
      </div>
      {unspendable.length ? (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground" data-encounter-unspendable>
          {unspendable.map((entry, index) => (
            <li key={`unspendable-${index}`}>{asString(entry) ?? JSON.stringify(entry)}</li>
          ))}
        </ul>
      ) : null}
      {confirmation ? (
        <div className="rounded-md border p-2 text-[11px]" data-encounter-confirmation>
          <p className="font-medium">
            Confirmation runs: {formatInt(confirmationCompleted)} of {formatInt(confirmationPlanned)}{" "}
            completed.
          </p>
          <p className="text-muted-foreground">
            Separate from this question&apos;s runs above; the confirmation plan is not counted in the
            question total.
            {asBoolean(confirmation.frozen) === true ? " The plan is frozen." : ""}
            {asBoolean(confirmation.ready) === false ? " Not complete yet." : ""}
            {asString(confirmation.decisionLimitation)
              ? ` ${asString(confirmation.decisionLimitation)}`
              : ""}
          </p>
        </div>
      ) : null}
      {publication ? (
        <p className="text-[11px]" data-encounter-publication>
          <span className="font-medium">Publication: {publicationStatus ?? "unavailable"}</span>
          {rawPublication && publicationStatus !== rawPublication ? (
            <span className="text-muted-foreground"> ({rawPublication})</span>
          ) : null}
          {asString(publication.reason) ? ` - ${asString(publication.reason)}` : ""}
        </p>
      ) : null}
      <p className="text-[11px] text-muted-foreground" data-encounter-why-spend-more>
        {WHY_SPEND_MORE}
      </p>
    </div>
  );
}

/** Human word for a stat key the backend uses in `expectedMechanicalDifferences`. */
function statWord(name: string): string {
  return humanStatName(name) ?? STAT_LABEL[name] ?? name.toUpperCase();
}

function NumberChange({ before, after }: { before: unknown; after: unknown }) {
  const low = asNumber(before);
  const high = asNumber(after);
  if (low === null && high === null) return <Unavailable />;
  return (
    <span className="tabular-nums">
      {low !== null ? formatInt(low) : "unavailable"} &rarr; {high !== null ? formatInt(high) : "unavailable"}
    </span>
  );
}

/**
 * The mechanical justification the host attached to the frozen proposal. Human stat names come
 * first; the canonical field paths and the compensation model are secondary. Everything here is
 * read as data - a reduced model is never presented as a whole-fight prediction.
 */
function MechanicalJustification({
  current,
  labels,
}: {
  current: EncounterCurrentWork | null | undefined;
  labels: OperatorLabels;
}) {
  const expected = asRecord(current?.expectedMechanicalDifferences);
  const preserved = asRecord(current?.approximatelyPreserved);
  const whySimulation = asString(current?.whySimulation);
  if (!expected && !preserved && !whySimulation) {
    return (
      <p className="text-xs text-muted-foreground" data-encounter-justification-missing>
        The host has not published a mechanical justification for the current proposal yet. When it
        does, this reads as a reduced model - never a whole-fight prediction.
      </p>
    );
  }
  const dpsChanges = asRecord(expected?.dpsEffectiveChanges) ?? {};
  const supportChanges = asRecord(expected?.supportEffectiveChanges) ?? {};
  const crit = asRecord(expected?.critRateChange);
  const interval = asRecord(expected?.intervalChange);
  const contact = asRecord(expected?.incomingContact);
  const contactBefore = asRecord(contact?.before);
  const contactAfter = asRecord(contact?.after);
  const training = asRecord(expected?.trainingSideEffects);
  const mp = asRecord(expected?.mpSideEffects);
  const nominalFields = asRecord(expected?.nominalWrittenFields) ?? {};
  const preservedFlags: Array<[string, unknown]> = [
    ["Formation order", preserved?.formation],
    ["Skills", preserved?.skills],
    ["Invocation levels", preserved?.invocationLevels],
    ["Weapon", preserved?.weapon],
    ["Fodder", preserved?.fodder],
    ["Constraints", preserved?.constraints],
  ];
  return (
    <div className="space-y-3" data-encounter-mechanical-justification>
      <p className="text-[11px]">
        <span className="text-muted-foreground">Compared against </span>
        <span className="font-medium">{labels.referenceLabel(asString(current?.referenceId))}</span>
        <span className="text-muted-foreground">
          . Changes below are effective values from the host&apos;s own reduced model.
        </span>
      </p>
      {Object.keys(dpsChanges).length ? (
        <div>
          <p className="text-[11px] font-medium">What changes on the DPS unit</p>
          <ul className="mt-1 space-y-0.5 text-[11px]" data-encounter-dps-changes>
            {Object.entries(dpsChanges).map(([name, raw]) => {
              const row = asRecord(raw) ?? {};
              return (
                <li key={`dps-${name}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="w-16 font-medium">{statWord(name)}</span>
                  <NumberChange before={row.before} after={row.after} />
                  {asNumber(row.nominal) !== null ? (
                    <span className="text-muted-foreground">(requested {formatInt(asNumber(row.nominal))})</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {Object.keys(supportChanges).length ? (
        <div>
          <p className="text-[11px] font-medium">What changes on the support units</p>
          <ul className="mt-1 space-y-0.5 text-[11px]" data-encounter-support-changes>
            {Object.entries(supportChanges).map(([name, raw]) => {
              const row = asRecord(raw) ?? {};
              return (
                <li key={`support-${name}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="w-16 font-medium">{statWord(name)}</span>
                  <NumberChange before={row.before} after={row.after} />
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {crit ? (
          <Fact label="Critical rate">
            <NumberChange before={crit.before} after={crit.after} />
          </Fact>
        ) : null}
        {interval ? (
          <Fact label="Action interval">
            <NumberChange before={interval.before} after={interval.after} />
          </Fact>
        ) : null}
        {contactBefore && contactAfter ? (
          <Fact label="Incoming contact">
            <span className="tabular-nums">
              hit {asNumber(contactBefore.hitRateAtAgility) ?? "unavailable"} &rarr;{" "}
              {asNumber(contactAfter.hitRateAtAgility) ?? "unavailable"}
            </span>
            {asNumber(contactAfter.rateChangeCount) !== null
              ? `, ${formatInt(asNumber(contactAfter.rateChangeCount))} rate changes in band`
              : ""}
          </Fact>
        ) : null}
        {training ? (
          <Fact label="Training level">
            <NumberChange before={training.before} after={training.after} />
          </Fact>
        ) : null}
        {mp ? (
          <Fact label="Battle MP">
            <NumberChange before={mp.before} after={mp.after} />
          </Fact>
        ) : null}
      </dl>
      {preserved ? (
        <div>
          <p className="text-[11px] font-medium">What stays the same</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {preservedFlags.map(([label, value]) => (
              <ToneBadge key={label} category={asBoolean(value) === true ? "success" : "muted"}>
                {asBoolean(value) === true ? `${label}: preserved` : `${label}: ${String(value ?? "not stated")}`}
              </ToneBadge>
            ))}
          </div>
          {asString(preserved.outgoingDamageProfile) ? (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Outgoing damage profile: {asString(preserved.outgoingDamageProfile)}
            </p>
          ) : null}
          <p className="mt-1 text-[10px] text-muted-foreground">
            Compensation: {asString(preserved.compensationFidelity) ?? "unavailable"}
            {asNumber(preserved.compensationResidual) !== null
              ? ` (residual ${formatNumber(asNumber(preserved.compensationResidual), 4)})`
              : ""}
          </p>
        </div>
      ) : null}
      {whySimulation ? (
        <p className="text-[11px] text-muted-foreground" data-encounter-why-simulation>
          {whySimulation}
        </p>
      ) : null}
      {Object.keys(nominalFields).length ? (
        <details className="text-[10px] text-muted-foreground">
          <summary className="cursor-pointer">Technical details: canonical fields written</summary>
          <span className="font-mono">
            {Object.entries(nominalFields)
              .map(([field, value]) => {
                const human = labels.fieldLabel(field);
                return `${human ? `${human} (${field})` : field} = ${String(value)}`;
              })
              .join(", ")}
          </span>
        </details>
      ) : null}
      <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]">
        Reduced models are not a whole-fight prediction. Reward, survival and target selection stay
        stateful; only a simulation measures whether this joint change actually works.
      </p>
    </div>
  );
}
/** One published reward-distribution reading: quantiles / loss / zero / resource / thresholds. */
function DistributionDetail({ row }: { row: EncounterPortfolioRow }) {
  const quantiles = asRecord(row.quantiles);
  const thresholds = asRecord(row.thresholds);
  const counts = asRecord(row.metricSampleCounts);
  const denominators = asRecord(row.denominators);
  const resolved = asNumber(counts?.numeric) ?? asNumber(denominators?.resolved);
  const resourceSample = asNumber(counts?.resources);
  const lossFrequency = asNumber(row.lossFrequency);
  const zeroFrequency = asNumber(row.zeroFrequency);
  const resourceMean = asNumber(row.resourceMean);
  const resourceFrequency = asNumber(row.resourceFrequency);
  const hasAny =
    (quantiles && Object.keys(quantiles).length > 0) ||
    (thresholds && Object.keys(thresholds).length > 0) ||
    lossFrequency !== null || zeroFrequency !== null ||
    resourceMean !== null || resourceFrequency !== null;
  if (!hasAny) return null;
  const pct = (value: number | null, denominator: number | null) =>
    value === null
      ? "unavailable"
      : denominator !== null
        ? `${(value * 100).toFixed(1)}% (${formatInt(value * denominator)}/${formatInt(denominator)})`
        : `${(value * 100).toFixed(1)}%`;
  return (
    <li className="rounded border p-1.5" data-encounter-distribution>
      <span className="font-medium">{asString(row.label) ?? asString(row.key) ?? "strategy"}</span>
      {quantiles && Object.keys(quantiles).length ? (
        <span className="text-muted-foreground">
          {" - quantiles "}
          {Object.entries(quantiles)
            .map(([name, value]) => `${name} ${formatNumber(asNumber(value))}`)
            .join(" / ")}
        </span>
      ) : null}
      {lossFrequency !== null ? (
        <span className="text-muted-foreground"> - loss {pct(lossFrequency, resolved)}</span>
      ) : null}
      {zeroFrequency !== null ? (
        <span className="text-muted-foreground"> - zero {pct(zeroFrequency, resolved)}</span>
      ) : null}
      {resourceMean !== null ? (
        <span className="text-muted-foreground">
          {" - resources mean "}
          {formatNumber(resourceMean)}
          {resourceFrequency !== null ? `, used ${pct(resourceFrequency, resourceSample)}` : ""}
        </span>
      ) : null}
      {thresholds && Object.keys(thresholds).length ? (
        <span className="text-muted-foreground">
          {" - P(>=K): "}
          {Object.entries(thresholds)
            .map(([key, value]) => {
              const entry = asRecord(value);
              const rate = entry ? asNumber(entry.frequency) : asNumber(value);
              const count = entry ? asNumber(entry.count) : null;
              const denominator = entry ? asNumber(entry.n) ?? resolved : resolved;
              const shown =
                rate === null
                  ? "unavailable"
                  : denominator !== null
                    ? `${(rate * 100).toFixed(1)}% (${formatInt(count ?? rate * denominator)}/${formatInt(denominator)})`
                    : `${(rate * 100).toFixed(1)}%`;
              return `${key}: ${shown}`;
            })
            .join(", ")}
        </span>
      ) : null}
    </li>
  );
}
/**
 * The reference presentation the host published: the compiled encounter identity and the small set
 * of fidelity-labelled mechanical quantities. It is a reduced model and says so; the whole-battle
 * reward is unknown here.
 */
function PresentationSummary({ presentation }: { presentation: EncounterPresentation | null }) {
  if (!presentation) {
    return (
      <p className="text-xs text-muted-foreground" data-encounter-presentation-missing>
        The host has not published the reference presentation yet, so no reference mechanics are shown.
      </p>
    );
  }
  const details = asRecord(presentation.encounterDetails);
  const summary = asRecord(presentation.mechanicsSummary);
  const quantities = asArray(summary?.quantities).map((entry) => asRecord(entry) ?? {});
  return (
    <div className="space-y-2" data-encounter-presentation>
      {details ? (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Reference encounter">
            {asNumber(details.encounterId) !== null
              ? `Encounter ${formatInt(asNumber(details.encounterId))}` : <Unavailable />}
          </Fact>
          <Fact label="Title">{asString(details.title) ?? <Unavailable />}</Fact>
          <Fact label="Boss">{asString(details.bossName) ?? <Unavailable />}</Fact>
          <Fact label="Enemies">
            {asNumber(details.enemyCount) !== null ? formatInt(asNumber(details.enemyCount)) : <Unavailable />}
          </Fact>
          <Fact label="Level">
            {asNumber(details.level) !== null ? formatInt(asNumber(details.level)) : <Unavailable />}
          </Fact>
          <Fact label="Revision">
            <span className="font-mono text-[10px]">
              {asString(details.revision)?.slice(0, 16) ?? <Unavailable />}
            </span>
          </Fact>
        </dl>
      ) : null}
      {quantities.length ? (
        <ul className="space-y-1 text-[11px]" data-encounter-mechanics-quantities>
          {quantities.map((quantity, index) => (
            <li
              key={`quantity-${index}`}
              className="flex flex-wrap items-baseline justify-between gap-2 rounded border p-1.5"
            >
              <span className="font-medium">
                {asString(quantity.label) ?? asString(quantity.key) ?? "quantity"}
              </span>
              <span className="flex items-center gap-2">
                <span className="tabular-nums">
                  {asNumber(quantity.value) !== null ? formatNumber(asNumber(quantity.value)) : "unavailable"}
                  {asString(quantity.unit) ? ` ${asString(quantity.unit)}` : ""}
                  {asNumber(quantity.denominator) !== null
                    ? ` / ${formatInt(asNumber(quantity.denominator))}` : ""}
                </span>
                <ToneBadge category={asString(quantity.fidelity) === "exact" ? "success" : "muted"}>
                  {asString(quantity.fidelity) ?? "unavailable"}
                </ToneBadge>
              </span>
              {asString(quantity.scope) ? (
                <span className="w-full text-[10px] text-muted-foreground">{asString(quantity.scope)}</span>
              ) : null}
              {asString(quantity.reason) ? (
                <span className="w-full text-[10px] text-muted-foreground">{asString(quantity.reason)}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-[11px] text-muted-foreground">
        Whole-battle reward: {asString(summary?.wholeBattleReward) ?? "unknown"} -{" "}
        {asString(summary?.note) ?? "this reduced model predicts no whole-fight outcome."}
      </p>
      {asString(summary?.mechanicsRevision) || asString(summary?.compilerRevision) ? (
        <p className="font-mono text-[10px] text-muted-foreground">
          mechanics {asString(summary?.mechanicsRevision)?.slice(0, 16) ?? "-"} - compiler{" "}
          {asString(summary?.compilerRevision)?.slice(0, 16) ?? "-"}
        </p>
      ) : null}
    </div>
  );
}
/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

/** The purposes the host currently publishes, read as whole runs, used to seed the budget draft. */
function seedPurposes(state: EncounterAwareSnapshot | null | undefined): Record<string, string> {
  const purposes = asRecord(state?.config?.purposes) ?? {};
  const drafts: Record<string, string> = {};
  for (const key of PURPOSE_ORDER) {
    const value = asNumber(purposes[key]);
    drafts[key] = value !== null ? String(value) : "";
  }
  return drafts;
}

/** The host's own allocation fractions: the preview's planned config wins over the active config. */
function readHostAllocations(
  state: EncounterAwareSnapshot | null | undefined,
): Record<string, unknown> {
  return (
    asRecord(state?.migrationPreview?.config?.allocations) ??
    asRecord(state?.config?.allocations) ??
    {}
  );
}

/** The registered stream fractions the host publishes, as an editable string draft. */
function seedAllocations(state: EncounterAwareSnapshot | null | undefined): Record<string, string> {
  const source = readHostAllocations(state);
  const drafts: Record<string, string> = {};
  for (const key of STREAM_ORDER) {
    const value = asNumber(source[key]);
    drafts[key] = value !== null ? String(value) : "";
  }
  return drafts;
}

export default function EncounterOptimizerPanel({
  state,
  sendCommand,
  disabled = false,
  paused = false,
  encounterOptions = null,
  humanLabels = null,
}: EncounterOptimizerPanelProps) {
  const [modeDraft, setModeDraft] = useState<EncounterMode>("community-first");
  const [purposeDrafts, setPurposeDrafts] = useState<Record<string, string>>(() => seedPurposes(state));
  const [purposeDirty, setPurposeDirty] = useState(false);
  const [allocationDrafts, setAllocationDrafts] = useState<Record<string, string>>(() => seedAllocations(state));
  const [allocationDirty, setAllocationDirty] = useState(false);
  const [previewedSignature, setPreviewedSignature] = useState<string | null>(null);

  /* Encounter / reference scope and the constraint + threshold drafts, all part of the signature. */
  const [encounterDraft, setEncounterDraft] = useState<string>("");
  const [encounterTouched, setEncounterTouched] = useState(false);
  const [referenceDraft, setReferenceDraft] = useState("");
  const [constraintContext, setConstraintContext] = useState("synthetic");
  const [fixedDrafts, setFixedDrafts] = useState<Record<string, string>>({});
  const [boundsDrafts, setBoundsDrafts] = useState<Record<string, { low: string; high: string }>>({});
  const [thresholdsDraft, setThresholdsDraft] = useState("");

  const [qKind, setQKind] = useState<EncounterQuestionKind>("fixed-build");
  const [qRole, setQRole] = useState("dps");
  const [qStat, setQStat] = useState("atk");
  const [qFieldPath, setQFieldPath] = useState("");
  const [qValue, setQValue] = useState("");
  const [qMinimumPairs, setQMinimumPairs] = useState("16");
  const [qTolerance, setQTolerance] = useState("90");
  const [qBudget, setQBudget] = useState("");

  const enabled = asBoolean(state?.enabled) === true;
  const setupComplete = enabled && Boolean(asString(state?.sessionId))
    && asNumber(state?.encounter) !== null;
  const presentation = useMemo(() => readPresentation(state), [state]);
  const encounterChoices = useMemo<EncounterChoice[]>(() => {
    const rows = (encounterOptions ?? []).filter((row) => Number.isInteger(row?.id));
    return [...rows].sort((left, right) => left.id - right.id);
  }, [encounterOptions]);
  const hostEncounterId =
    asNumber(state?.encounter) ?? asNumber(presentation?.encounterDetails?.encounterId);
  const portfolio = useMemo(() => normalizePortfolio(state?.portfolio), [state?.portfolio]);
  /* Only render the distribution block when at least one row actually publishes a reading. */
  const hasDistribution = useMemo(
    () =>
      portfolio.some((row) => {
        const quantiles = asRecord(row.quantiles);
        const thresholds = asRecord(row.thresholds);
        return (
          (quantiles && Object.keys(quantiles).length > 0) ||
          (thresholds && Object.keys(thresholds).length > 0) ||
          asNumber(row.lossFrequency) !== null ||
          asNumber(row.zeroFrequency) !== null ||
          asNumber(row.resourceMean) !== null ||
          asNumber(row.resourceFrequency) !== null
        );
      }),
    [portfolio],
  );
  const spending = useMemo(() => normalizeSpending(state?.purposeSpending), [state?.purposeSpending]);
  const budgetMatrix = useMemo(
    () => normalizeBudgetMatrix(state?.migrationPreview?.budgetMatrix ?? state?.budgetMatrix),
    [state?.migrationPreview?.budgetMatrix, state?.budgetMatrix],
  );
  const migrationRows = useMemo(
    () => normalizeMigrationRows(state?.migrationPreview),
    [state?.migrationPreview],
  );
  const boundaryRows = useMemo(
    () => readBoundaryRows(state),
    [state?.boundaries, state?.mechanicalRegions],
  );

  const purposes = useMemo(
    () => (asRecord(state?.config?.purposes) ?? {}) as EncounterPurposeBudgets,
    [state?.config?.purposes],
  );
  const hostAllocations = useMemo(
    () => readHostAllocations(state),
    [state?.migrationPreview?.config?.allocations, state?.config?.allocations],
  );

  /* Canonical constraint-path rows the editor can use, straight from the presentation payload. */
  const constraintFields = useMemo<Array<Record<string, unknown>>>(() => {
    return asArray(presentation?.questionFields)
      .map((entry) => asRecord(entry) ?? {})
      .filter((entry) => asString(entry.field) && asString(entry.role) && asString(entry.stat));
  }, [presentation]);
  const constraintsAvailable = constraintFields.length > 0;
  const adjustableFields = asArray(presentation?.buildDomain?.adjustableFields)
    .map((entry) => asString(entry))
    .filter(Boolean) as string[];

  /*
   * Human read-only labels. A role is attached only when the snapshot tied that role to the exact
   * field (an explicit questionFields row, or a published roleIndices unit). `ownUnits.0` is never
   * assumed to be the DPS unit; an unresolved path falls back to "Unit N Stat".
   */
  const fieldLabelContext = useMemo<EncounterFieldLabelContext>(() => {
    const fields: EncounterFieldMapping[] = [];
    const entries = [...asArray(presentation?.questionFields), ...asArray(state?.questionFields)];
    for (const entry of entries) {
      const row = asRecord(entry);
      if (row) fields.push(row as EncounterFieldMapping);
    }
    const roleIndices = asRecord(presentation?.buildDomain?.roleIndices) as Record<
      string,
      number | null
    > | null;
    return { fields, roleIndices };
  }, [presentation, state?.questionFields]);

  const labels = useMemo<OperatorLabels>(
    () => ({
      fieldLabel: (path) => {
        if (!path) return null;
        const override = humanLabels?.fieldLabel?.(path);
        return override ?? humanFieldLabel(path, fieldLabelContext)?.label ?? null;
      },
      referenceLabel: (referenceId) => {
        const key = typeof referenceId === "string" ? referenceId : null;
        const override = humanLabels?.referenceLabel?.(key);
        return override ?? humanReferenceLabel(key);
      },
      policySummary: (policy) => {
        const override = humanLabels?.policySummary?.(policy ?? {});
        return override ?? defaultPolicySummary(policy);
      },
    }),
    [humanLabels, fieldLabelContext],
  );

  const constraintDraft = useMemo(() => {
    const fixed: Record<string, number> = {};
    const bounds: Record<string, [number, number]> = {};
    const errors: string[] = [];
    for (const field of constraintFields) {
      const path = String(field.field);
      const label = asString(field.label) ?? path;
      const fixedRaw = (fixedDrafts[path] ?? "").trim();
      if (fixedRaw !== "") {
        const parsed = readInteger(fixedRaw);
        if (parsed.error) errors.push(`${label}: fixed value ${parsed.error}.`);
        else if (parsed.value !== null) fixed[path] = parsed.value;
      }
      const span = boundsDrafts[path];
      const lowRaw = (span?.low ?? "").trim();
      const highRaw = (span?.high ?? "").trim();
      if (lowRaw !== "" || highRaw !== "") {
        if (lowRaw === "" || highRaw === "") {
          errors.push(`${label}: a bound needs both a low and a high.`);
        } else {
          const low = readInteger(lowRaw);
          const high = readInteger(highRaw);
          if (low.error) errors.push(`${label}: low ${low.error}.`);
          else if (high.error) errors.push(`${label}: high ${high.error}.`);
          else if (low.value !== null && high.value !== null) {
            if (low.value > high.value) errors.push(`${label}: low must not exceed high.`);
            else bounds[path] = [low.value, high.value];
          }
        }
      }
    }
    const any = Object.keys(fixed).length > 0 || Object.keys(bounds).length > 0;
    return {
      fixed,
      bounds,
      errors,
      any,
      document: any || constraintContext !== "synthetic"
        ? { context: constraintContext, fixed, bounds } : null,
    };
  }, [constraintFields, fixedDrafts, boundsDrafts, constraintContext]);

  const thresholds = useMemo(() => readIntegerList(thresholdsDraft), [thresholdsDraft]);
  const encounterId = encounterDraft.trim() === "" ? null : readInteger(encounterDraft).value;
  const referenceId = referenceDraft.trim();
  /* When a real catalogue is offered a selection is required; with no catalogue the host default applies. */
  const scopeReady = encounterChoices.length === 0 || encounterId !== null;

  /* Re-seed the budget boxes when the host's own purposes change, but never while editing. */
  const purposesSignature = useMemo(
    () => JSON.stringify(PURPOSE_ORDER.map((key) => [key, asNumber(purposes[key])])),
    [purposes],
  );
  useEffect(() => {
    if (purposeDirty) return;
    setPurposeDrafts(seedPurposes(state));
  }, [purposesSignature, purposeDirty, state]);

  /* Re-seed the allocation boxes when the host's own allocations change, but never while editing. */
  const allocationsSignature = useMemo(
    () => JSON.stringify(STREAM_ORDER.map((key) => [key, asNumber(hostAllocations[key])])),
    [hostAllocations],
  );
  useEffect(() => {
    if (allocationDirty) return;
    setAllocationDrafts(seedAllocations(state));
  }, [allocationsSignature, allocationDirty, state]);

  /* The host's mode, when it publishes one, is the draft's starting point. */
  useEffect(() => {
    const hostMode = asString(state?.config?.mode) ?? asString(state?.mode);
    if (hostMode === "community-first" || hostMode === "all-strategy") setModeDraft(hostMode);
  }, [state?.config?.mode, state?.mode]);

  /* The host's active encounter seeds the selection until the person changes it themselves. */
  useEffect(() => {
    if (encounterTouched) return;
    setEncounterDraft(hostEncounterId !== null ? String(hostEncounterId) : "");
  }, [hostEncounterId, encounterTouched]);

  /* Purpose budget validation: whole runs, non-negative, at least one positive. */
  const purposeValidation = useMemo(() => {
    const values: Record<string, number> = {};
    const errors: Record<string, string> = {};
    let complete = true;
    for (const key of PURPOSE_ORDER) {
      const parsed = readInteger(purposeDrafts[key] ?? "");
      if (parsed.error) {
        errors[key] = parsed.error;
        complete = false;
      } else if (parsed.value === null) {
        complete = false;
      } else if (parsed.value < 0) {
        errors[key] = "must be 0 or more";
        complete = false;
      } else {
        values[key] = parsed.value;
      }
    }
    const anyPositive = Object.values(values).some((value) => value > 0);
    return { values, errors, complete, anyPositive };
  }, [purposeDrafts]);

  /* Which modes this host build supports. An absent capability means all-strategy cannot be trusted. */
  const limitations = asArray(state?.limitations).map((entry) => asString(entry) ?? String(entry));
  const supportedModes = Array.isArray(state?.supportedModes)
    ? ((state?.supportedModes as unknown[]).map((entry) => asString(entry)).filter(Boolean) as string[])
    : null;
  const allStrategyLimitation = limitations.find((entry) => entry.includes(ALL_STRATEGY_FRAGMENT));
  const allStrategyReason = supportedModes
    ? supportedModes.includes("all-strategy")
      ? null
      : "This host build reports that it does not support the all-strategy mode."
    : allStrategyLimitation ??
      "This host build has not published which modes it supports, so all-strategy stays disabled until it does.";

  /* All-strategy allocations are explicit fractions; a blank box counts as 0 while editing, and the
     whole registered set must be finite, non-negative and sum to 1 (tolerance 1e-9, no floors). */
  const allocationValidation = useMemo(() => {
    const values: Record<string, number> = {};
    const errors: Record<string, string> = {};
    for (const key of STREAM_ORDER) {
      const raw = (allocationDrafts[key] ?? "").trim();
      if (raw === "") {
        values[key] = 0;
        continue;
      }
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) errors[key] = "a finite fraction is required";
      else if (parsed < 0) errors[key] = "must be 0 or more";
      else values[key] = parsed;
    }
    const total = STREAM_ORDER.reduce((sum, key) => sum + (values[key] ?? 0), 0);
    const sumValid = Math.abs(total - 1) <= 1e-9;
    return { values, errors, total, sumValid };
  }, [allocationDrafts]);

  /* Community-first is fixed: the community stream runs at 1 and every other registered stream at 0.
     The draft boxes never leak into this mode, so an old allocation cannot become the community one. */
  const communityFirstAllocations = useMemo<EncounterAllocations>(() => {
    const map: EncounterAllocations = {};
    for (const key of STREAM_ORDER) map[key] = key === "community" ? 1 : 0;
    return map;
  }, []);
  const allStrategyActive = modeDraft === "all-strategy" && allStrategyReason === null;
  const effectiveAllocations: EncounterAllocations =
    modeDraft === "all-strategy"
      ? (Object.fromEntries(STREAM_ORDER.map((key) => [key, allocationValidation.values[key]])) as EncounterAllocations)
      : communityFirstAllocations;

  const draftSignature = useMemo(() => {
    if (!purposeValidation.complete || !purposeValidation.anyPositive) return null;
    if (modeDraft === "all-strategy") {
      if (allStrategyReason !== null) return null;
      if (Object.keys(allocationValidation.errors).length > 0 || !allocationValidation.sumValid) {
        return null;
      }
    }
    if (constraintDraft.errors.length > 0 || thresholds.error !== null) return null;
    return JSON.stringify({
      mode: modeDraft,
      allocations: STREAM_ORDER.map((key) => [key, effectiveAllocations[key]]),
      purposes: PURPOSE_ORDER.map((key) => [key, purposeValidation.values[key]]),
      encounter: encounterId,
      referenceId: referenceId === "" ? null : referenceId,
      constraints: canonicalConstraints(constraintDraft.document),
      thresholds: thresholds.values,
    });
  }, [
    modeDraft, allStrategyReason, allocationValidation, effectiveAllocations, purposeValidation,
    encounterId, referenceId, constraintDraft, thresholds,
  ]);

  /* The signature of the config the host last echoed back in `migrationPreview`. */
  const reportSignature = useMemo(() => {
    const previewConfig = state?.migrationPreview?.config;
    const previewPurposes =
      (asRecord(previewConfig?.purposes) ?? asRecord(state?.migrationPreview?.purposes)) ?? {};
    const previewAllocations = asRecord(previewConfig?.allocations) ?? {};
    const previewMode = asString(previewConfig?.mode);
    if (!previewMode) return null;
    const allPresent = PURPOSE_ORDER.every((key) => asNumber(previewPurposes[key]) !== null);
    if (!allPresent) return null;
    const preview = state?.migrationPreview;
    return JSON.stringify({
      mode: previewMode,
      allocations: STREAM_ORDER.map((key) => [key, asNumber(previewAllocations[key]) ?? 0]),
      purposes: PURPOSE_ORDER.map((key) => [key, asNumber(previewPurposes[key])]),
      encounter: previewNumber(preview, "encounter"),
      referenceId: previewString(preview, "referenceId"),
      constraints: canonicalConstraints(preview?.constraints ?? previewConfig?.constraints),
      thresholds: normalizeThresholdArray(preview?.thresholds ?? previewConfig?.thresholds),
    });
  }, [state?.migrationPreview]);

  const previewMatches =
    previewedSignature !== null && draftSignature !== null && previewedSignature === draftSignature &&
    reportSignature === draftSignature;

  const simulatorMigration = state?.migrationPreview?.simulatorMigration ?? null;
  const simulatorEligible = simulatorMigration ? asBoolean(simulatorMigration.eligible) : null;
  const simulatorRequired = simulatorMigration ? asBoolean(simulatorMigration.required) : null;
  const simulatorPlanId = simulatorMigration ? asString(simulatorMigration.planId) : null;
  const simulatorBlocked = simulatorEligible === false;
  const planIdMissing = simulatorRequired === true && !simulatorPlanId;

  /* Question capability: prefer a host-published questionFields map, otherwise verified roles. */
  const capabilityFields = asArray(state?.questionFields)
    .map((entry) => asRecord(entry) ?? {})
    .filter((entry) => asString(entry.role) || asString(entry.stat));
  const roleOptions = capabilityFields.length
    ? Array.from(new Set(capabilityFields.map((entry) => asString(entry.role)).filter(Boolean) as string[]))
    : ROLE_ORDER;
  const statOptions = useMemo(() => {
    if (capabilityFields.length) {
      const stats = capabilityFields
        .filter((entry) => !asString(entry.role) || asString(entry.role) === qRole)
        .map((entry) => asString(entry.stat))
        .filter(Boolean) as string[];
      const unique = Array.from(new Set(stats));
      if (unique.length) return unique;
    }
    return qKind === "support" ? SUPPORT_STATS : STAT_ORDER;
  }, [capabilityFields, qKind, qRole]);

  useEffect(() => {
    if (!statOptions.includes(qStat)) setQStat(statOptions[0] ?? "atk");
  }, [statOptions, qStat]);

  /* Question validation. No command is sent while any of these hold. */
  const qValueParsed = readInteger(qValue);
  const qBudgetParsed = readInteger(qBudget);
  const qMinimumParsed = readInteger(qMinimumPairs);
  const qToleranceParsed = readInteger(qTolerance);
  const usesFieldPath = qFieldPath.trim().length > 0;
  const toleranceValue = qToleranceParsed.value !== null ? qToleranceParsed.value / 100 : null;

  const questionErrors: string[] = [];
  if (qValueParsed.error) questionErrors.push(`Tested value: ${qValueParsed.error}.`);
  else if (qValueParsed.value === null) questionErrors.push("A tested integer value is required.");
  if (!usesFieldPath && !qRole) questionErrors.push("Choose a role.");
  if (!usesFieldPath && !qStat) questionErrors.push("Choose a stat.");
  if (qMinimumParsed.error) questionErrors.push(`Minimum pairs: ${qMinimumParsed.error}.`);
  else if (qMinimumParsed.value === null) questionErrors.push("Minimum pairs is required.");
  else if (qMinimumParsed.value < 2) questionErrors.push("Minimum pairs must be at least 2.");
  if (qBudgetParsed.error) questionErrors.push(`Budget: ${qBudgetParsed.error}.`);
  else if (qBudgetParsed.value === null) questionErrors.push("A finite whole-run budget is required.");
  else if (qBudgetParsed.value <= 0)
    questionErrors.push("The question budget must be a positive whole number of runs.");
  if (qToleranceParsed.error) questionErrors.push(`Tolerance: ${qToleranceParsed.error}.`);
  else if (toleranceValue === null || toleranceValue <= 0 || toleranceValue > 1)
    questionErrors.push("Tolerance must be between 1% and 100%.");
  if (
    qMinimumParsed.value !== null && qMinimumParsed.value >= 2 &&
    qBudgetParsed.value !== null && qBudgetParsed.value > 0 &&
    qMinimumParsed.value * 2 > qBudgetParsed.value
  ) {
    questionErrors.push("The budget cannot buy the declared minimum pairs.");
  }
  const questionValid = questionErrors.length === 0;

  const submitQuestion = () => {
    if (!paused || disabled || !questionValid) return;
    const value: Record<string, unknown> = {
      kind: qKind,
      budget: qBudgetParsed.value,
      minimumPairs: qMinimumParsed.value,
      tolerance: toleranceValue,
      value: qValueParsed.value,
    };
    if (usesFieldPath) value.field = qFieldPath.trim();
    else {
      value.role = qRole;
      value.stat = qStat;
    }
    void sendCommand("Freeze encounter question", "encounter_question", value);
  };

  const budgetErrors = PURPOSE_ORDER.map((key) => purposeValidation.errors[key]).filter(Boolean);
  const budgetValid = purposeValidation.complete && purposeValidation.anyPositive && budgetErrors.length === 0;

  const submitBudget = () => {
    if (!budgetValid) return;
    void sendCommand("Encounter purpose budgets", "encounter_budget", {
      purposes: Object.fromEntries(PURPOSE_ORDER.map((key) => [key, purposeValidation.values[key]])),
    });
  };

  /*
   * The encounter/reference/constraints/threshold scope sent with BOTH the preview and the
   * activation, so the host matches them exactly. Changing any of them changes the draft signature
   * and so invalidates the previewed activation.
   */
  const scopePayload = (): Record<string, unknown> => {
    const scope: Record<string, unknown> = {};
    if (encounterId !== null) scope.encounter = encounterId;
    if (referenceId !== "") scope.referenceId = referenceId;
    if (constraintDraft.document) scope.constraints = constraintDraft.document;
    if (thresholds.values.length > 0) scope.thresholds = thresholds.values;
    return scope;
  };

  const preview = () => {
    if (draftSignature === null || !scopeReady) return;
    setPreviewedSignature(draftSignature);
    void sendCommand("Preview encounter migration", "encounter_preview", {
      mode: modeDraft,
      allocations: effectiveAllocations,
      purposes: purposeValidation.values,
      ...scopePayload(),
    });
  };

  const activate = () => {
    if (disabled || !paused || !previewMatches || !scopeReady || simulatorBlocked || planIdMissing) return;
    const config: EncounterAwareConfig =
      state?.migrationPreview?.config ??
      ({
        version: 1,
        mode: modeDraft,
        allocations: effectiveAllocations,
        purposes: Object.fromEntries(PURPOSE_ORDER.map((key) => [key, purposeValidation.values[key]])),
      } as EncounterAwareConfig);
    const value: Record<string, unknown> = { config };
    Object.assign(value, scopePayload());
    if (simulatorRequired === true && simulatorPlanId) value.migrationPlanId = simulatorPlanId;
    void sendCommand("Activate encounter-aware mode", "encounter_activate", value);
  };

  if (!state) {
    return (
      <Card data-encounter-operating-regions>
        <CardHeader>
          <CardTitle className="text-base">Encounter-aware operating regions</CardTitle>
          <CardDescription data-encounter-backend-required>
            Backend update required. This host build does not publish an encounter-aware snapshot yet,
            so the legacy search controls stay in charge below. Nothing here is changed by opening this
            page.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const revision = asString(state.runtimeRevision);
  const activatedBlockedReason = simulatorBlocked
    ? (asString(simulatorMigration?.reason) ?? "The simulator migration is not eligible in this library.")
    : planIdMissing
      ? "The preview says a simulator migration is required, but it did not publish a plan id."
      : null;

  /* The plain-language answers the operator screen leads with: what / why / progress / findings / next. */
  const frozenQuestion = state.question ?? null;
  const fieldSummary = labels.fieldLabel(asString(frozenQuestion?.field));
  const questionValueSummary = asNumber(frozenQuestion?.value);
  const confirmationProgress = asRecord(state.progress?.confirmation);
  const publicationProgress = asRecord(state.progress?.publication);
  const spentRuns = asNumber(state.questionBudget?.spent);
  const budgetRuns = asNumber(state.questionBudget?.budget);
  const questionRunsSummary =
    spentRuns !== null || budgetRuns !== null
      ? `${formatInt(spentRuns)} of ${formatInt(budgetRuns)} question runs`
      : "question runs unavailable";
  const confirmationSummary = confirmationProgress
    ? `${formatInt(asNumber(confirmationProgress.completed))} of ${formatInt(asNumber(confirmationProgress.planned))} confirmation runs`
    : "no confirmation plan yet";
  const findingsLine = portfolio.length
    ? (() => {
        const row = portfolio.find((entry) => asBoolean(entry.confirmation) === true) ?? portfolio[0];
        const mean = asNumber(row.partialMean) ?? asNumber(row.arithmeticMeanEarned);
        const resolved = asNumber(row.resolved) ?? asNumber(row.samples);
        const total = asNumber(row.total);
        const name = asString(row.label) ?? asString(row.key) ?? "candidate";
        return `${name}: average so far ${mean !== null ? formatNumber(mean, 2) : "unavailable"} over ${formatInt(resolved)} of ${formatInt(total)} runs.`;
      })()
    : "no earned-reward readings published yet.";
  const findingsStatus = humanPublicationStatus(asString(publicationProgress?.status));
  const currentWork = asRecord(state.progress?.current);
  const confirmationRemaining = confirmationProgress &&
    asNumber(confirmationProgress.planned) !== null &&
    asNumber(confirmationProgress.completed) !== null
      ? Number(confirmationProgress.planned) - Number(confirmationProgress.completed) : null;
  const whySpendMore = asBoolean(state.progress?.blocked) || asBoolean(currentWork?.blocked)
    ? `Work is blocked: ${asString(currentWork?.blockedReason) ?? "the host has not published a reason"}.`
    : asBoolean(state.progress?.idle)
      ? "No further work is currently scheduled. Unused budget does not require extra runs."
      : confirmationRemaining !== null && confirmationRemaining > 0
        ? `${formatInt(confirmationRemaining)} independent confirmation runs remain in the declared plan. They check the selected build using fresh tests.`
        : asString(currentWork?.purpose)
          ? `The current runs are assigned to ${purposeLabel(String(currentWork?.purpose)).toLowerCase()}. The host has not published a more specific spending reason.`
          : "The host has not published a current spending reason.";
  const nextAction = disabled
    ? "Waiting for the host to finish the current command."
    : !setupComplete
      ? "Start searches all encounters automatically. Search setup below is an optional advanced view for focusing one encounter or bounding its budgets."
    : enabled
      ? paused
        ? "Review the question and remaining budgets, then use Start to continue."
        : asBoolean(state.progress?.idle)
          ? "Review the findings; pause before setting another question or changing budgets."
          : "Let the current tests finish. Findings and confirmation progress update as results arrive."
      : !paused
        ? "Pause the search to change the question, budgets or activation."
        : !scopeReady
          ? "Choose an encounter to scope the preview."
          : !previewMatches
            ? "Preview the exact draft, then activate it while paused."
            : "Activate the previewed draft while paused.";
  return (
    <Card data-encounter-operating-regions data-encounter-enabled={enabled ? "true" : "false"}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">Search and findings</CardTitle>
          {setupComplete ? (
            <ToneBadge category="success">Active</ToneBadge>
          ) : (
            <ToneBadge category="muted">Community defaults</ToneBadge>
          )}
          <Badge variant="outline">{modeLabel(asString(state.mode) ?? asString(state.config?.mode))}</Badge>
        </div>
        <CardDescription>
          {setupComplete
            ? "The redesigned search is active. Review its current tests, measured rewards and remaining budget below."
            : "Start searches all encounters automatically at the Community defaults. Search setup below is an optional advanced view for focusing one encounter and bounding budgets."}
        </CardDescription>
        <details className="text-[11px] text-muted-foreground" data-encounter-runtime-revision>
          <summary className="cursor-pointer">Technical details: runtime revision</summary>
          <p className="mt-1">
            Runtime revision:{" "}
            <span className="font-mono">{revision ? revision.slice(0, 12) : "unavailable"}</span> - loaded
            when the host started. Reloading this page does not load a newer backend; restart the desktop
            host to pick up a new revision.
          </p>
        </details>
      </CardHeader>
      <CardContent className="space-y-5">
        <section className="space-y-2 rounded-md border bg-muted/30 p-3" data-encounter-operator-summary>
          <SectionHeading>Operator summary</SectionHeading>
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Fact label="Testing">
              {fieldSummary
                ? `${fieldSummary}${questionValueSummary !== null ? ` at ${formatInt(questionValueSummary)}` : ""}`
                : "No question is frozen yet."}
            </Fact>
            <Fact label="Why">
              {frozenQuestion
                ? `${preservationTargetSentence(asNumber(frozenQuestion.tolerance))}. ${asString(frozenQuestion.kind) === "compensated" ? "Other permitted stats may adjust to compensate." : "This applies only under the stated build and test conditions."}`
                : "Choose a boundary question to compare a change with a specific reference build."}
            </Fact>
            <Fact label="Progress">
              {questionRunsSummary}; {confirmationSummary}. Needs{" "}
              {asNumber(state.questionBudget?.minimumPairs) !== null
                ? formatInt(asNumber(state.questionBudget?.minimumPairs))
                : "an unstated number of"}{" "}
              paired tests.
            </Fact>
            <Fact label="Findings">
              {findingsLine}
              {findingsStatus ? ` Publication: ${findingsStatus}.` : ""}
            </Fact>
            <Fact label="Next action">{nextAction}</Fact>
            <Fact label="Why spend more">{whySpendMore}</Fact>
          </dl>
        </section>

        <section className="space-y-2" data-encounter-question>
          <SectionHeading>What is being tested</SectionHeading>
          <QuestionSummary question={state.question ?? null} labels={labels} />
        </section>

        <section className="space-y-2 border-t pt-4" data-encounter-justification>
          <SectionHeading>Why this change</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            The host&apos;s own pre-run comparison of the candidate against the reference build, read
            as data. Word names come first; canonical paths, model names and residuals sit under
            technical details. This is a reduced model, never a whole-fight prediction.
          </p>
          <MechanicalJustification current={state.progress?.current ?? null} labels={labels} />
        </section>
        <section className="space-y-2 border-t pt-4" data-encounter-question-slot>
          <SectionHeading>Progress</SectionHeading>
          <ProgressFacts slot={state.questionBudget ?? null} progress={state.progress ?? null} />
        </section>

        <section className="space-y-2 border-t pt-4" data-encounter-region>
          <SectionHeading>Encounter in scope</SectionHeading>
          {state.encounter !== null && state.encounter !== undefined ? (
            <dl
              className="grid grid-cols-2 gap-3 rounded-md border p-2 sm:grid-cols-4"
              data-encounter-current-region
            >
              {Object.entries(typeof state.encounter === "number"
                ? { "Encounter ID": state.encounter } : state.encounter)
                .filter(([, value]) => value !== null && value !== undefined && typeof value !== "object")
                .slice(0, 8)
                .map(([key, value]) => (
                  <Fact key={key} label={key}>
                    {String(value)}
                  </Fact>
                ))}
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">No encounter is published.</p>
          )}
          {asString(asRecord(state.mechanicalRegions)?.note) ? (
            <p className="text-[11px] text-muted-foreground">
              {asString(asRecord(state.mechanicalRegions)?.note)}
            </p>
          ) : null}
        </section>

        <section className="space-y-2 border-t pt-4" data-encounter-presentation-section>
          <SectionHeading>What the reference model fixes (reduced model)</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            Read from the reference presentation the host published. Each quantity carries its own
            label: exact where the compiled roster fixes it, reduced where the shared model simplifies.
            Whole-battle reward is unknown here.
          </p>
          <PresentationSummary presentation={presentation} />
        </section>
        <section className="space-y-2 border-t pt-4" data-encounter-purpose-work>
          <SectionHeading>Where the runs are going</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            These are whole runs. Purpose budgets are integers; stream allocations below are fractions.
          </p>
          {spending.length ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Purpose</TableHead>
                    <TableHead className="text-right">Authorised</TableHead>
                    <TableHead className="text-right">Reserved</TableHead>
                    <TableHead className="text-right">Completed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {spending.map(([key, entry]) => (
                    <TableRow key={key}>
                      <TableCell className="text-xs">{purposeLabel(key)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {spendCell(entry.total ?? entry.authorized)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{spendCell(entry.reserved)}</TableCell>
                      <TableCell className="text-right tabular-nums">{spendCell(entry.completed)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">The host has not published purpose spending yet.</p>
          )}
          {asRecord(state.progress?.current) ? (
            <p className="text-[11px] text-muted-foreground" data-encounter-current-work>
              Current work: purpose{" "}
              {purposeLabel(String(asRecord(state.progress?.current)?.purpose ?? "unavailable"))} -
              reserved {spendCell(asNumber(asRecord(state.progress?.current)?.reserved))}, completed{" "}
              {spendCell(asNumber(asRecord(state.progress?.current)?.completed))}
              {asBoolean(asRecord(state.progress?.current)?.blocked) === true
                ? ` - blocked: ${asString(asRecord(state.progress?.current)?.blockedReason) ?? "reason unavailable"}`
                : ""}
            </p>
          ) : null}
        </section>

        <section className="space-y-2 border-t pt-4" data-encounter-portfolio>
          <SectionHeading>Findings: earned reward</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            The average so far uses only runs with a known reward; the full average uses every run and
            appears only once nothing is unresolved. Neither average alone proves an improvement - that
            needs independent confirmation. The share measured is not a win chance. A candidate below
            the reference-preservation target is not &quot;unusable&quot;: a build earning 60 where the
            reference earns 120 can still be worth running for a different goal.
          </p>
          {portfolio.length ? (
            <div className="overflow-x-auto">
              <Table className="min-w-[56rem]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Candidate</TableHead>
                    <TableHead className="text-right">Average (all runs)</TableHead>
                    <TableHead className="text-right">Average so far (measured runs)</TableHead>
                    <TableHead className="text-right">Runs measured / all runs</TableHead>
                    <TableHead className="text-right">Share measured</TableHead>
                    <TableHead className="text-right">Standard error</TableHead>
                    <TableHead className="text-right">Median</TableHead>
                    <TableHead>Window</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {portfolio.map((row) => {
                    const resolved = asNumber(row.resolved) ?? asNumber(row.samples);
                    const total = asNumber(row.total);
                    const reliability = asNumber(row.reliability);
                    const partial = asNumber(row.partialMean) ?? asNumber(row.arithmeticMeanEarned);
                    const uncertaintyRecord = asRecord(row.uncertainty);
                    const se =
                      asNumber(row.standardError) ??
                      (uncertaintyRecord ? asNumber(uncertaintyRecord.value) : asNumber(row.uncertainty));
                    return (
                      <TableRow key={String(row.key)}>
                        <TableCell className="text-xs">
                          <div className="font-medium">
                            {asString(row.label) ?? asString(row.key) ?? "strategy"}
                          </div>
                          {asString(row.build) || asString(row.parent) || asString(row.parentChange) ? (
                            <div className="text-[10px] text-muted-foreground">
                              {asString(row.build) ?? ""}
                              {asString(row.parent) ? ` - parent ${asString(row.parent)}` : ""}
                              {asString(row.parentChange) ? ` - ${asString(row.parentChange)}` : ""}
                            </div>
                          ) : null}
                          {total !== null && asNumber(row.unknownCount) ? (
                            <div className="text-[10px] text-muted-foreground">
                              {formatInt(asNumber(row.unknownCount))} of {formatInt(total)} runs still unknown
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatNumber(asNumber(row.fullMean) ?? asNumber(row.meanEarned))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(partial)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatInt(resolved)} / {formatInt(total)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{formatPercent(reliability)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(se)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatNumber(asNumber(row.median))}
                        </TableCell>
                        <TableCell className="text-[11px]">
                          {asBoolean(row.confirmation) === true ? (
                            <ToneBadge category="muted">confirmation nominee</ToneBadge>
                          ) : asString(row.window) ? (
                            <span className="text-muted-foreground">{asString(row.window)}</span>
                          ) : (
                            <Unavailable />
                          )}
                        </TableCell>
                        <TableCell>
                          <StatusBadges row={row} />
                          {asBoolean(row.eligible) === false ? (
                            <div className="mt-1 text-[10px] text-muted-foreground">
                              not eligible for recommendation
                            </div>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No portfolio rows are published yet.</p>
          )}
        </section>

        {hasDistribution ? (
          <section className="space-y-2 border-t pt-4" data-encounter-distributions>
            <SectionHeading>Reward distribution detail (when published)</SectionHeading>
            <p className="text-[11px] text-muted-foreground">
              Optional readings the host publishes beside the mean: quantiles, loss and zero frequency,
              resource use and P(earned &ge; K) for the K actually selected. Each rate keeps the count
              and denominator it was measured over; nothing is filled in when the host is silent, and
              unresolved samples are never counted as zero.
            </p>
            <ul className="space-y-1 text-[11px]">
              {portfolio.map((row) => (
                <DistributionDetail key={`dist-${String(row.key)}`} row={row} />
              ))}
            </ul>
          </section>
        ) : null}

        <section className="space-y-2 border-t pt-4" data-encounter-boundaries>
          <SectionHeading>Findings: tested points and unknowns</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            Fixed-build, compensated and support questions are kept apart, and so are pairs and runs.
            These are tested conditional points, not a continuous safe range and not a safe box. A point
            below the reference-preservation target is not &quot;unusable&quot;.
          </p>
          {boundaryRows.length ? (
            <div className="space-y-2">
              {boundaryRows.map((row, index) => (
                <div key={`boundary-${index}`} className="rounded-md border p-2" data-encounter-boundary-row>
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <ToneBadge category="muted">{kindLabel(asString(row.kind))}</ToneBadge>
                    <span className="text-muted-foreground">
                      Compared against {labels.referenceLabel(asString(row.frozenReference))}
                    </span>
                    {row.published === false ? (
                      <ToneBadge category="warning">Still testing</ToneBadge>
                    ) : null}
                  </div>
                  <ul className="mt-1 space-y-1 text-[11px]" data-encounter-tested-points>
                    {asArray(row.testedPoints).map((entry, pointIndex) => {
                      const point = (asRecord(entry) ?? {}) as EncounterTestedPoint;
                      const value = asRecord(point.value) ?? {};
                      const rawField = asString(value.field);
                      const classification = asString(point.classification);
                      return (
                        <li key={`p-${pointIndex}`} className="rounded border p-1.5">
                          <ToneBadge category="muted">
                            {kindLabel(asString(value.kind) ?? asString(row.kind))}
                          </ToneBadge>{" "}
                          <span className="font-medium">
                            {labels.fieldLabel(rawField) ?? rawField ?? "field unavailable"}
                          </span>
                          {asNumber(value.value) !== null ? ` at ${formatInt(asNumber(value.value))}` : ""}
                          {classification
                            ? ` - ${humanClassification(classification) ?? classification}`
                            : ""}
                          {asNumber(point.pairedCount) !== null
                            ? ` - ${formatInt(asNumber(point.pairedCount))} paired tests`
                            : ""}
                        </li>
                      );
                    })}
                    {asArray(row.unresolvedGaps).map((entry, gapIndex) => {
                      const gap = (asRecord(entry) ?? {}) as EncounterUnknownGap;
                      const value = asRecord(gap.value) ?? {};
                      const rawField = asString(value.field);
                      const classification = asString(gap.classification);
                      return (
                        <li key={`g-${gapIndex}`} className="rounded border border-dashed p-1.5">
                          <ToneBadge category="warning">Still testing</ToneBadge>{" "}
                          <span className="font-medium">
                            {labels.fieldLabel(rawField) ?? rawField ?? "field unavailable"}
                          </span>
                          {asNumber(value.value) !== null ? ` at ${formatInt(asNumber(value.value))}` : ""}
                          {classification
                            ? ` - ${humanClassification(classification) ?? classification}`
                            : ""}
                          {asString(gap.reason) ? ` - ${asString(gap.reason)}` : ""}
                        </li>
                      );
                    })}
                  </ul>
                  {row.experimentId !== null && row.experimentId !== undefined ? (
                    <details className="mt-1 text-[10px] text-muted-foreground">
                      <summary className="cursor-pointer">Technical details</summary>
                      experiment {String(row.experimentId)}
                      {asString(row.frozenReference) ? (
                        <>
                          {" - "}
                          <span className="font-mono">reference {asString(row.frozenReference)}</span>
                        </>
                      ) : null}
                    </details>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No boundary records are published yet.</p>
          )}
        </section>
        <section className="space-y-3 border-t pt-4" data-encounter-question-editor>
          <SectionHeading>Question form: freeze or replace</SectionHeading>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="encounter-question-kind" className="text-xs">Question type</Label>
              <Select
                value={qKind}
                onValueChange={(value) => setQKind(value as EncounterQuestionKind)}
                disabled={disabled}
              >
                <SelectTrigger id="encounter-question-kind" aria-label="Question type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {QUESTION_KINDS.map((kind) => (
                    <SelectItem key={kind} value={kind}>{QUESTION_KIND_LABEL[kind]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[10px] text-muted-foreground">{QUESTION_KIND_MEANING[qKind]}</p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-role" className="text-xs">Unit role</Label>
              <Select value={qRole} onValueChange={setQRole} disabled={disabled || usesFieldPath}>
                <SelectTrigger id="encounter-question-role" aria-label="Role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {roleOptions.map((role) => (
                    <SelectItem key={role} value={role}>{ROLE_LABEL[role] ?? role}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-stat" className="text-xs">Stat</Label>
              <Select value={qStat} onValueChange={setQStat} disabled={disabled || usesFieldPath}>
                <SelectTrigger id="encounter-question-stat" aria-label="Stat">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {statOptions.map((stat) => (
                    <SelectItem key={stat} value={stat}>{STAT_LABEL[stat] ?? stat}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[10px] text-muted-foreground">
                The backend translates unit role + stat into its canonical unit/parameter path; the page
                never guesses it.
                {qKind === "support" ? " Support questions accept HP, MP and DEF only." : ""}
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-value" className="text-xs">Value to test</Label>
              <RawNumberInput
                id="encounter-question-value"
                value={qValue}
                disabled={disabled}
                ariaLabel="Tested integer value"
                onChange={setQValue}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-min-pairs" className="text-xs">Paired tests needed</Label>
              <RawNumberInput
                id="encounter-question-min-pairs"
                value={qMinimumPairs}
                disabled={disabled}
                ariaLabel="Paired tests needed"
                onChange={setQMinimumPairs}
              />
              <p className="text-[10px] text-muted-foreground">{PAIRED_TEST_EXPLANATION}</p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-tolerance" className="text-xs">
                Keep at least this % of the reference&apos;s average reward
              </Label>
              <RawNumberInput
                id="encounter-question-tolerance"
                value={qTolerance}
                disabled={disabled}
                ariaLabel="Reference-preservation percent"
                onChange={setQTolerance}
              />
              <p className="text-[10px] text-muted-foreground">
                Default 90% - the reference-preservation floor, not a measured range.
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="encounter-question-budget" className="text-xs">Run budget (whole runs)</Label>
              <RawNumberInput
                id="encounter-question-budget"
                value={qBudget}
                disabled={disabled}
                ariaLabel="Finite budget in runs"
                onChange={setQBudget}
              />
            </div>
          </div>
          <details className="rounded-md border p-2">
            <summary className="cursor-pointer text-[11px] text-muted-foreground">
              Advanced: exact canonical field path (overrides role and stat)
            </summary>
            <div className="mt-2 space-y-1">
              <Label htmlFor="encounter-question-field-path" className="text-xs">
                ownUnits.&lt;index&gt;.parameters.&lt;id&gt;
              </Label>
              <Input
                id="encounter-question-field-path"
                value={qFieldPath}
                disabled={disabled}
                placeholder="leave blank to use role + stat"
                onChange={(event) => setQFieldPath(event.target.value)}
              />
            </div>
          </details>
          {questionErrors.length ? (
            <div className="space-y-1" data-encounter-question-errors>
              {questionErrors.map((message) => (
                <p key={message} className="text-[11px] text-destructive">{message}</p>
              ))}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              size="sm"
              disabled={disabled || !paused || !questionValid}
              onClick={submitQuestion}
              data-action="encounter-question"
            >
              Freeze question
            </Button>
            {!paused ? (
              <span className="text-[11px] text-muted-foreground">
                Pause and let outstanding runs finish before replacing the question.
              </span>
            ) : null}
            {!questionValid ? (
              <span className="text-[11px] text-muted-foreground">
                Fill a whole-number value, the paired tests needed and a budget that can buy them before
                freezing.
              </span>
            ) : null}
          </div>
        </section>

        <section className="space-y-3 border-t pt-4" data-encounter-budgets>
          <SectionHeading>Purpose budgets (whole runs)</SectionHeading>
          <p className="text-[11px] text-muted-foreground">
            Absolute totals, not increments. A blank box stays blank; a decimal or a sign is an error,
            never rounded or stripped.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PURPOSE_ORDER.map((key) => (
              <div key={key} className="space-y-1">
                <Label htmlFor={`encounter-budget-${key}`} className="text-xs">{PURPOSE_LABEL[key]}</Label>
                <RawNumberInput
                  id={`encounter-budget-${key}`}
                  value={purposeDrafts[key] ?? ""}
                  disabled={disabled || !paused}
                  ariaLabel={`${PURPOSE_LABEL[key]} budget`}
                  onChange={(raw) => {
                    setPurposeDirty(true);
                    setPurposeDrafts((prev) => ({ ...prev, [key]: raw }));
                  }}
                />
                <FieldError message={purposeValidation.errors[key] ?? null} />
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || !paused || !budgetValid}
              onClick={submitBudget}
              data-action="encounter-budget"
            >
              Update budgets
            </Button>
            {!paused ? (
              <span className="text-[11px] text-muted-foreground">
                Pause the search to change its finite budgets.
              </span>
            ) : null}
          </div>
        </section>

        <section className="space-y-3 border-t pt-4" data-encounter-migration>
          <SectionHeading>Advanced: optional search setup</SectionHeading>
          <p className="text-[11px] text-muted-foreground" data-encounter-setup-advanced>
            Advanced. Normal Start already searches every encounter automatically at the Community
            defaults; nothing here is required first. Use it only to focus one encounter or bound budgets.
            The retired <span className="font-medium">average</span> stream (Student 9) folds into shared
            refinement. Preview the exact draft, then apply it while paused; nothing is applied on render.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="encounter-requested-mode" className="text-xs">Requested mode</Label>
              <Select
                value={modeDraft}
                onValueChange={(value) => setModeDraft(value as EncounterMode)}
                disabled={disabled}
              >
                <SelectTrigger id="encounter-requested-mode" aria-label="Requested mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="community-first">Community-first</SelectItem>
                  <SelectItem value="all-strategy" disabled={allStrategyReason !== null}>
                    All-strategy{allStrategyReason ? " (not supported)" : ""}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-encounter-target>
            <div className="space-y-1">
              <Label htmlFor="encounter-target" className="text-xs">Encounter</Label>
              {encounterChoices.length ? (
                <>
                  <Select
                    value={encounterDraft}
                    onValueChange={(value) => {
                      setEncounterTouched(true);
                      setEncounterDraft(value);
                    }}
                    disabled={disabled}
                  >
                    <SelectTrigger id="encounter-target" aria-label="Encounter">
                      <SelectValue placeholder="Choose an encounter" />
                    </SelectTrigger>
                    <SelectContent>
                      {encounterChoices.map((choice) => (
                        <SelectItem key={choice.id} value={String(choice.id)}>
                          {choice.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-[10px] text-muted-foreground">
                    The preview and activation scope the search to this encounter, the reference build
                    and any constraints below. Changing any of them invalidates the preview.
                  </p>
                </>
              ) : (
                <p
                  className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]"
                  data-encounter-catalog-missing
                >
                  This host build has not published its encounter catalogue, so no encounter can be
                  chosen here yet. The host default applies until it does.
                </p>
              )}
            </div>
          </div>
          {allStrategyReason ? (
            <p
              className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]"
              data-encounter-all-strategy-block
            >
              All-strategy stays disabled: {allStrategyReason}
            </p>
          ) : null}

          <div className="space-y-1" data-encounter-allocations>
            <p className="text-[11px] font-medium">Stream allocations (fractions)</p>
            {allStrategyActive ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {STREAM_ORDER.map((key) => (
                    <div key={key} className="space-y-1">
                      <Label htmlFor={`encounter-allocation-${key}`} className="text-[11px]">
                        {streamLabel(key)}
                      </Label>
                      <Input
                        id={`encounter-allocation-${key}`}
                        inputMode="decimal"
                        value={allocationDrafts[key] ?? ""}
                        disabled={disabled}
                        placeholder="0"
                        onChange={(event) => {
                          setAllocationDirty(true);
                          const next = event.target.value;
                          setAllocationDrafts((current) => ({ ...current, [key]: next }));
                        }}
                      />
                      {allocationValidation.errors[key] ? (
                        <p className="text-[10px] text-destructive">
                          {allocationValidation.errors[key]}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
                <p
                  className={
                    allocationValidation.sumValid
                      ? "text-[11px] text-muted-foreground"
                      : "text-[11px] text-destructive"
                  }
                  data-encounter-allocation-sum
                >
                  Fractions must be finite, non-negative and sum to exactly 1 (tolerance 1e-9). Sum:{" "}
                  {allocationValidation.total.toLocaleString(undefined, { maximumFractionDigits: 6 })}.
                </p>
              </div>
            ) : (
              <>
                <ul className="space-y-0.5 text-[11px] text-muted-foreground">
                  {STREAM_ORDER.map((key) => (
                    <li key={key}>
                      {streamLabel(key)}: {allocationCell(communityFirstAllocations[key])}
                    </li>
                  ))}
                </ul>
                <p className="text-[10px] text-muted-foreground">
                  Community-first always runs the community stream at 100%; every other registered
                  stream stays at 0 and has no control here. Choose all-strategy to allocate explicitly.
                </p>
              </>
            )}
          </div>

          <div className="space-y-2 rounded-md border p-2" data-encounter-constraints>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[11px] font-medium">Build constraints (optional)</p>
              {constraintDraft.any ? <ToneBadge category="muted">in scope</ToneBadge> : null}
            </div>
            <p className="text-[11px] text-muted-foreground">
              Fix a field, or bound it low..high, on the canonical paths the reference presentation
              published. Values are whole numbers and stay blank until typed; a blank box fixes
              nothing. A fixed DEX means the search may not move that DEX - it is not a claim that the
              value is reachable through equipment or rank. In the synthetic context a value must also
              sit inside the search contract&apos;s permitted interval; a user-supplied (player) value
              is taken as-is with its reachability left unknown.
            </p>
            {constraintsAvailable ? (
              <>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div className="space-y-1">
                    <Label htmlFor="encounter-constraint-context" className="text-xs">Context</Label>
                    <Select
                      value={constraintContext}
                      onValueChange={setConstraintContext}
                      disabled={disabled}
                    >
                      <SelectTrigger id="encounter-constraint-context" aria-label="Constraint context">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="synthetic">synthetic (search domain)</SelectItem>
                        <SelectItem value="player">player (user-supplied)</SelectItem>
                        <SelectItem value="unrestricted">unrestricted</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                {adjustableFields.length ? (
                  <p className="text-[10px] text-muted-foreground">
                    Host-declared adjustable fields:{" "}
                    <span className="font-mono">{adjustableFields.join(", ")}</span>
                  </p>
                ) : null}
                <ul className="space-y-2">
                  {constraintFields.map((field) => {
                    const path = String(field.field);
                    const label = asString(field.label) ?? path;
                    const current = asNumber(field.currentValue);
                    return (
                      <li
                        key={path}
                        className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[12rem_1fr_1fr]"
                        data-encounter-constraint-row
                      >
                        <div className="text-[11px]">
                          <div className="font-medium">{label}</div>
                          <div className="font-mono text-[10px] text-muted-foreground">
                            {path}
                            {current !== null ? ` - current ${formatInt(current)}` : ""}
                          </div>
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor={`encounter-fixed-${path}`} className="text-[10px]">Fixed</Label>
                          <RawNumberInput
                            id={`encounter-fixed-${path}`}
                            value={fixedDrafts[path] ?? ""}
                            disabled={disabled}
                            placeholder="blank"
                            ariaLabel={`${label} fixed value`}
                            onChange={(raw) => setFixedDrafts((prev) => ({ ...prev, [path]: raw }))}
                          />
                        </div>
                        <div className="flex items-end gap-1">
                          <div className="flex-1 space-y-1">
                            <Label htmlFor={`encounter-bounds-low-${path}`} className="text-[10px]">Low</Label>
                            <RawNumberInput
                              id={`encounter-bounds-low-${path}`}
                              value={boundsDrafts[path]?.low ?? ""}
                              disabled={disabled}
                              placeholder="blank"
                              ariaLabel={`${label} lower bound`}
                              onChange={(raw) =>
                                setBoundsDrafts((prev) => ({
                                  ...prev,
                                  [path]: { low: raw, high: prev[path]?.high ?? "" },
                                }))
                              }
                            />
                          </div>
                          <div className="flex-1 space-y-1">
                            <Label htmlFor={`encounter-bounds-high-${path}`} className="text-[10px]">High</Label>
                            <RawNumberInput
                              id={`encounter-bounds-high-${path}`}
                              value={boundsDrafts[path]?.high ?? ""}
                              disabled={disabled}
                              placeholder="blank"
                              ariaLabel={`${label} upper bound`}
                              onChange={(raw) =>
                                setBoundsDrafts((prev) => ({
                                  ...prev,
                                  [path]: { low: prev[path]?.low ?? "", high: raw },
                                }))
                              }
                            />
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                {constraintDraft.errors.length ? (
                  <div className="space-y-1" data-encounter-constraint-errors>
                    {constraintDraft.errors.map((message) => (
                      <p key={message} className="text-[11px] text-destructive">{message}</p>
                    ))}
                  </div>
                ) : null}
                {presentation?.buildDomain ? (
                  <p className="text-[10px] text-muted-foreground" data-encounter-constraint-status>
                    Document: {asBoolean(presentation.buildDomain.schemaValid) === false ? "invalid" : "valid"}
                    {asString(presentation.buildDomain.context) ? ` - context ${asString(presentation.buildDomain.context)}` : ""}
                    {asString(presentation.buildDomain.provenanceStatus) ? ` - provenance ${asString(presentation.buildDomain.provenanceStatus)}` : ""}
                    {` - reachability ${asString(presentation.buildDomain.reachability) ?? "unknown"}`}
                  </p>
                ) : null}
              </>
            ) : (
              <p
                className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]"
                data-encounter-constraints-need-preview
              >
                Reference preview needed: the host has not published the reference presentation
                (question fields and the build-domain schema) yet, so a constraint cannot be mapped to
                a canonical path. The editor stays disabled until it does.
              </p>
            )}
          </div>

          <div className="space-y-1" data-encounter-thresholds>
            <Label htmlFor="encounter-thresholds" className="text-xs">Reward thresholds (optional)</Label>
            <Input
              id="encounter-thresholds"
              value={thresholdsDraft}
              disabled={disabled}
              placeholder="e.g. 5, 10, 20"
              onChange={(event) => setThresholdsDraft(event.target.value)}
            />
            <p className="text-[10px] text-muted-foreground">
              Comma- or space-separated whole numbers. The host reports P(earned &ge; K) for exactly the
              K you list; a blank box means none, and no universal threshold is inferred.
            </p>
            <FieldError message={thresholds.error} />
          </div>

          <details className="rounded-md border p-2" data-encounter-reference>
            <summary className="cursor-pointer text-[11px] text-muted-foreground">
              Technical details: reference build id
            </summary>
            <div className="mt-2 space-y-1">
              <Label htmlFor="encounter-reference-id" className="text-xs">Reference id</Label>
              <Input
                id="encounter-reference-id"
                value={referenceDraft}
                disabled={disabled}
                placeholder="blank = Community baseline"
                onChange={(event) => setReferenceDraft(event.target.value)}
              />
            </div>
          </details>
          <details className="rounded-md border p-2" data-encounter-migration-technical>
            <summary className="cursor-pointer text-[11px] text-muted-foreground">
              Technical details: migration ledger and budget matrix
            </summary>
            <div className="mt-2 space-y-3">
          {migrationRows.length ? (
            <div className="overflow-x-auto" data-encounter-migration-rows>
              <Table className="min-w-[34rem]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Stream</TableHead>
                    <TableHead className="text-right">Prior</TableHead>
                    <TableHead className="text-right">New</TableHead>
                    <TableHead>Moves to</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {migrationRows.map((row) => (
                    <TableRow key={String(row.key)}>
                      <TableCell className="text-xs">
                        {streamLabel(asString(row.stream) ?? asString(row.key) ?? "")}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {allocationCell(asNumber(row.oldValue) ?? asNumber(row.prior))}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {allocationCell(asNumber(row.newValue) ?? asNumber(row.next))}
                      </TableCell>
                      <TableCell className="text-[11px] text-muted-foreground">
                        {asString(row.target) ?? asString(row.reason) ?? "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground" data-encounter-migration-empty>
              No migration preview has been produced. Preview before activating; nothing is sent
              automatically on render.
            </p>
          )}

          {budgetMatrix.rows.length ? (
            <div className="space-y-1" data-encounter-budget-matrix>
              <p className="text-[11px] font-medium">Per-owner / purpose budget matrix (whole runs)</p>
              <div className="overflow-x-auto">
                <Table className="min-w-[28rem]">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Owner</TableHead>
                      {budgetMatrix.columns.map((column) => (
                        <TableHead key={column} className="text-right">{purposeLabel(column)}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {budgetMatrix.rows.map((row) => (
                      <TableRow key={row.owner}>
                        <TableCell className="text-xs">{row.owner}</TableCell>
                        {budgetMatrix.columns.map((column) => (
                          <TableCell key={column} className="text-right tabular-nums">
                            {row.cells[column] === null || row.cells[column] === undefined ? (
                              <span className="text-muted-foreground">-</span>
                            ) : (
                              formatInt(row.cells[column])
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Whole runs, exactly as the host published them. A cell the host did not publish stays a
                dash; an owner row of zeros is a real 0, not a stand-in.
              </p>
            </div>
          ) : null}
            </div>
          </details>

          {simulatorMigration ? (
            <div className="space-y-1 rounded-md border p-2 text-[11px]" data-encounter-simulator-migration>
              <p>
                Simulator migration:{" "}
                {simulatorEligible === false ? (
                  <span className="text-destructive">not eligible</span>
                ) : simulatorRequired === true ? (
                  "required"
                ) : (
                  "not required"
                )}
                {simulatorPlanId ? (
                  <>
                    {" "}- plan id <span className="font-mono">{simulatorPlanId.slice(0, 12)}</span>
                  </>
                ) : null}
              </p>
              {asString(simulatorMigration.limitation) ? (
                <p className="text-muted-foreground">{asString(simulatorMigration.limitation)}</p>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || draftSignature === null || !scopeReady}
              onClick={preview}
              data-action="encounter-preview"
            >
              Preview this exact draft
            </Button>
            {(
              <Button
                type="button"
                size="sm"
                disabled={
                  disabled || !paused || !previewMatches || !scopeReady || simulatorBlocked || planIdMissing
                }
                onClick={activate}
                data-action="encounter-activate"
              >
                {enabled ? "Apply the previewed setup" : "Activate the previewed draft"}
              </Button>
            )}
            {enabled && setupComplete ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={disabled || !paused}
                onClick={() => void sendCommand("Roll back encounter-aware mode", "encounter_rollback", {})}
                data-action="encounter-rollback"
              >
                Roll back to previous configuration
              </Button>
            ) : null}
            {!paused ? (
              <span className="text-[11px] text-muted-foreground">
                Activation and rollback are only offered while the search is paused.
              </span>
            ) : !scopeReady ? (
              <span className="text-[11px] text-muted-foreground" data-encounter-scope-missing>
                Choose an encounter before previewing; the search scope includes it.
              </span>
            ) : !previewMatches ? (
              <span className="text-[11px] text-muted-foreground" data-encounter-preview-stale>
                Preview the current draft before activating; any change to the mode, budgets, encounter,
                reference, constraints or thresholds disables activation until a new preview.
              </span>
            ) : null}
          </div>
          {activatedBlockedReason ? (
            <Alert variant="destructive" data-encounter-activation-blocked>
              <AlertTitle>Activation blocked</AlertTitle>
              <AlertDescription>{activatedBlockedReason}</AlertDescription>
            </Alert>
          ) : null}
        </section>

        <section className="space-y-1 border-t pt-4 text-[11px] text-muted-foreground" data-encounter-limitations>
          <SectionHeading>Limitations</SectionHeading>
          {limitations.length ? (
            <ul className="list-disc space-y-0.5 pl-4">
              {limitations.map((entry, index) => (
                <li key={`limit-${index}`}>{entry}</li>
              ))}
            </ul>
          ) : (
            <p>No limitations were published by the backend.</p>
          )}
          <p>No unmeasured gain is claimed here; every rate keeps the denominator it was measured over.</p>
        </section>
      </CardContent>
    </Card>
  );
}

/**
 * Compact typed fixture for browser verification when no backend is available. It mirrors the shape
 * of a real `strategy_encounter_search.Coordinator.report()`, carries no measured battles, and is not
 * imported by the page. Purely illustrative test data, not game truth.
 */
export const MOCK_ENCOUNTER_AWARE_SNAPSHOT: EncounterAwareSnapshot = {
  version: 1,
  enabled: false,
  mode: "community-first",
  runtimeRevision: "mock-revision-000000000000",
  sessionId: "mock-session",
  config: {
    version: 1,
    mode: "community-first",
    allocations: { community: 1, discovery: 0, rebel: 0, stumble: 0, mechanism: 0 },
    purposes: { improvement: 128, boundary: 64, support: 32, comparison: 128, exploration: 32 },
  },
  question: {
    version: "conditional-operating-regions-1",
    id: "mock-question-id-0000",
    kind: "compensated",
    referenceId: "mock-reference",
    referenceRevision: 1,
    encounterRevision: "mock-encounter-revision",
    mechanicsRevision: "mock-mechanics-revision",
    field: "ownUnits.0.parameters.19",
    fixedFields: { "ownUnits.0.parameters.19": 11 },
    adjustableFields: ["ownUnits.0.parameters.13"],
    tolerance: 0.9,
    value: 11,
    policy: { finishPolicy: "on-verdict", tickLimit: 900, holyHerbStock: 0, note: "mock policy" },
  },
  questionBudget: { budget: 64, spent: 16, minimumPairs: 16, referenceId: "mock-reference" },
  encounter: 19,
  presentation: {
    version: "strategy-encounter-presentation-1",
    referenceId: "mock-reference",
    encounterDetails: {
      encounterId: 19,
      title: "Take out the Wairo Tank!",
      defeatCount: 0,
      level: 80,
      enemyCount: 21,
      bossName: "Wairo Tank",
      bossOrder: 20,
      revision: "mock-encounter-revision",
      referenceId: "mock-reference",
      note: "Compiled from the same roster revision the coordinator uses; no battle is run.",
    },
    questionFields: [
      { role: "dps", stat: "atk", label: "DPS ATK", field: "ownUnits.0.parameters.13", currentValue: 343, unitIndex: 0, parameterId: 13, status: "resolved" },
      { role: "dps", stat: "dex", label: "DPS DEX", field: "ownUnits.0.parameters.19", currentValue: 11, unitIndex: 0, parameterId: 19, status: "resolved" },
      { role: "healer", stat: "hp", label: "Healer HP", field: "ownUnits.1.parameters.10", currentValue: 420, unitIndex: 1, parameterId: 10, status: "resolved" },
      { role: "fodder", stat: "def", label: "Fodder DEF", field: "ownUnits.2.parameters.14", currentValue: 42, unitIndex: 2, parameterId: 14, status: "resolved" },
    ],
    buildDomain: {
      context: "synthetic",
      provenanceStatus: "declared",
      reachability: "unknown",
      schemaValid: true,
      valid: true,
      violations: [],
      fixed: {},
      bounds: {},
      adjustableFields: ["ownUnits.0.parameters.19"],
      syntheticBounds: { "13": [1, 999], "19": [1, 999] },
      constraintSchema: { context: "synthetic", provenanceStatus: "declared", valid: true, violations: [], fixed: {}, bounds: {} },
      roleIndices: { dps: 0, healer: 1, fodder: 2 },
      formationOrder: [0, 2, 3, 4, 5, 1],
      rolesStatus: "resolved",
      note: "mock build-domain status",
    },
    mechanicsSummary: {
      reducedModel: true,
      encounterRevision: "mock-encounter-revision",
      mechanicsRevision: "mock-mechanics-revision",
      compilerRevision: "mock-compiler-revision",
      quantities: [
        { key: "bossHp", label: "Boss HP", value: 1846, unit: "hp", fidelity: "exact", scope: "compiled roster: Wairo Tank" },
        { key: "followerHpMax", label: "Toughest follower HP", value: 1121, unit: "hp", fidelity: "exact", scope: "compiled roster, 20 followers" },
        { key: "incomingContact", label: "DPS contact rate vs boss", value: 97, unit: "%", fidelity: "reduced", reason: "static hit_rate; live invoking windows are not applied" },
        { key: "critRate", label: "DPS critical rate", value: 21, unit: "%", fidelity: "reduced", reason: "recovered critical law only" },
        { key: "actionInterval", label: "DPS action interval", value: 14, unit: "frames", fidelity: "reduced", reason: "uninterrupted normal-attack reduced path" },
        { key: "mpEligibility", label: "DPS skills affordable at battle MP", value: 5, unit: "skills", fidelity: "reduced", reason: "reduced skill-selection model", denominator: 5 },
      ],
      wholeBattleReward: "unknown",
      approximations: ["TTK is a reduced-model convolution capped at 256 hits; it is not a whole-battle prediction."],
      note: "Reduced mechanical model; a simulation is still required.",
    },
  },
  mechanicalRegions: {
    points: [[
      { candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 11 }, classification: "supported-acceptable", pairedCount: 8 },
      { candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 10 }, classification: "supported-degraded", pairedCount: 16 },
    ]],
    gaps: [[{ candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 9 }, classification: "unresolved", reason: "too few complete pairs" }]],
    note: "diagnostic bootstrap points; gaps remain unknown and no safe box is inferred",
  },
  boundaries: [
    {
      kind: "compensated",
      experimentId: "mock-experiment",
      frozenReference: "mock-reference",
      questionId: "mock-question-id-0000",
      testedPoints: [
        { candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 11 }, classification: "supported-acceptable", pairedCount: 8 },
        { candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 10 }, classification: "supported-degraded", pairedCount: 16 },
      ],
      unresolvedGaps: [{ candidateId: "mock-candidate", value: { field: "ownUnits.0.parameters.19", kind: "compensated", value: 9 }, classification: "unresolved", reason: "too few complete pairs" }],
      published: true,
      note: "conditional points only",
    },
  ],
  purposeSpending: {
    improvement: { total: 128, reserved: 104, completed: 96 },
    boundary: { total: 64, reserved: 16, completed: 16 },
  },
  portfolio: [
    {
      key: "mock-candidate",
      label: "Mock community candidate",
      fullMean: null,
      partialMean: 12.5,
      arithmeticMeanEarned: 12.5,
      meanEarned: null,
      resolved: 48,
      samples: 48,
      total: 64,
      unknownCount: 16,
      standardError: 0.42,
      uncertainty: { kind: "SE", value: 0.42 },
      reliability: 0.75,
      median: 12,
      window: "development",
      development: true,
      confirmation: false,
      synthetic: false,
      playerUnknown: true,
      context: "synthetic",
      provenanceStatus: "declared",
      reachability: "unknown",
      eligible: false,
      arm: "candidate",
      min: 0,
      max: 21,
      stdev: 3.1,
      zeroFrequency: 0.0417,
      resourceMean: 1.25,
      resourceFrequency: 0.5,
      resourceCount: 24,
      quantiles: { p0: 0, p25: 9, p50: 12, p75: 15, p100: 21 },
      thresholds: {
        "5": { count: 44, n: 48, frequency: 0.9167 },
        "10": { count: 30, n: 48, frequency: 0.625 },
      },
      metricSampleCounts: { total: 64, numeric: 48, resources: 48 },
      denominators: { attempted: 64, resolved: 48, lost: 8, unresolved: 16, errors: 0, censored: 0 },
    },
  ],
  progress: {
    roundIndex: 3,
    experiments: 6,
    reserved: 8,
    completed: 96,
    queueLength: 2,
    idle: false,
    unspendable: ["support budget left unspent: no authorised useful work"],
    current: {
      experimentId: "mock-experiment",
      candidateId: "mock-candidate",
      referenceId: "mock-reference",
      purpose: "improvement",
      reserved: 8,
      completed: 4,
      blocked: false,
      planJobs: 8,
      expectedMechanicalDifferences: {
        proposalKind: "progression",
        dpsEffectiveChanges: {
          atk: { nominal: 373, before: 343, after: 373 },
          dex: { nominal: 12, before: 11, after: 12 },
        },
        supportEffectiveChanges: {},
        critRateChange: { before: 21, after: 21 },
        intervalChange: { before: 14, after: 14 },
        incomingContact: {
          before: { hitRateAtAgility: 97, interval: 12, rateChangeCount: 0 },
          after: { hitRateAtAgility: 97, interval: 12, rateChangeCount: 0 },
        },
        trainingSideEffects: { before: 1, after: 1 },
        mpSideEffects: { before: 100, after: 100 },
        nominalWrittenFields: { "ownUnits.0.parameters.13": 373 },
      },
      approximatelyPreserved: {
        formation: true,
        skills: true,
        invocationLevels: true,
        weapon: true,
        fodder: true,
        outgoingDamageProfile: "approximated by the bounded ATK inverse",
        compensationResidual: 0.0,
        compensationFidelity: "reduced",
        admission: { admitted: true, failures: [] },
        constraints: true,
      },
      whySimulation:
        "The change is chosen from discrete mechanical landmarks and the ATK compensation from the bounded inverse. A simulation is required because earned reward, survival and target-selection are stateful and are never predicted here.",
    },
    confirmation: { experimentId: "mock-confirmation", frozen: true, ready: false, planned: 16, completed: 4 },
    publication: { status: "withheld", reason: "unresolved holdout work blocks publication" },
  },
  migrationPreview: {
    config: {
      version: 1,
      mode: "community-first",
      allocations: { community: 1, discovery: 0, rebel: 0, stumble: 0, mechanism: 0 },
      purposes: { improvement: 128, boundary: 64, support: 32, comparison: 128, exploration: 32 },
    },
    ledger: [
      { stream: "average", oldValue: 0.25, target: "community", newValue: 0.25, reason: "average retired" },
    ],
    total: 1,
    note: "pure plan only; the caller must persist it deliberately (never auto-applied)",
    purposes: { improvement: 128, boundary: 64, support: 32, comparison: 128, exploration: 32 },
    simulatorMigration: { eligible: true, required: false },
    budgetMatrix: {
      community: { improvement: 128, boundary: 64, support: 32, comparison: 128, exploration: 32 },
      discovery: { improvement: 0, boundary: 0, support: 0, comparison: 0, exploration: 0 },
      mechanism: { improvement: 0, boundary: 0, support: 0, comparison: 0, exploration: 0 },
    },
  },
  supportedModes: ["community-first", "all-strategy"],
  limitations: [
    "Illustrative mock data only.",
  ],
};
