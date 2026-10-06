/**
 * Strategy guide - the normal-player surface for the encounter-aware search.
 *
 * Renders the host's player operating-region read model
 * (`Bridge.encounter_player_regions()`, per `player-region-contract.md`) as a human strategy guide:
 * a reward target the player is aiming for, the archetypes the search actually measured (headed by
 * the composition the formation roster proves) and a compact stat comparison per tested option.
 *
 * Invariants:
 * - The stat query is a LOCAL filter over the loaded summary. Changing it sends no command, runs no
 *   battle and never refetches. `autoLoad` loads once on mount and once per scope change - never on
 *   the status poll and never per keystroke; a manual button press always works too.
 * - A query reads `point.stats[<role>.<stat>]`. An exact value matches a measured point or resolves to
 *   explicit unknown with the nearby MEASURED options - never interpolated, never snapped, never
 *   assuming higher is better.
 * - Every measured point is retained, including lower-reward, below-target and below-reference ones.
 *   A positive `unknownCount` means the measurement is not complete: the point is never labelled as
 *   measured eligible.
 * - Raw ids, classifications, evidence windows and canonical paths stay VERBATIM but live under a
 *   per-option Technical details disclosure. No fixed reference-percentage label is hard-coded and
 *   historical records are never folded into development/confirmation.
 * - A missing stat is unknown, never zero. Support HP/MP/DEF show a tested value only when measured;
 *   otherwise they stay explicitly unknown and no minimum is assumed.
 *
 * No fixture data is imported here: this component renders whatever the host bridge returns.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Spinner } from "@/components/ui/spinner";
import { ToneBadge } from "@/components/ka/badges";
import {
  availableStatFields,
  describeFixedRequirementsOther,
  exportPlayerRegionsReadModel,
  filterInputInvalid,
  normalizePlayerRegionsSummary,
  observedFieldSpan,
  observedMeanSpan,
  organizeByMeanTarget,
  parseFilterInput,
  pointMissingStats,
  queryPlayerRegions,
  referenceRetentionText,
  statFieldLabel,
} from "@/lib/optimizer-player-regions";
import {
  axisHasStop,
  axisMinimum,
  collectStatPoints,
  resolveStatPins,
  statAxes,
  type StatAxis,
  type StatCombination,
  type StatPin,
} from "@/lib/optimizer-stat-tradeoffs";
import type {
  OptimizerPlayerRegionsProps,
  PlayerRegion,
  PlayerRegionArchetype,
  PlayerRegionFormation,
  PlayerRegionPoint,
  PlayerRegionQueryMatch,
  PlayerRegionRewardOption,
  PlayerRegionScope,
  PlayerRegionSparsePoint,
  PlayerRegionsSummary,
} from "@/types/player-regions";

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */

function formatNumber(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "unavailable";
}

function formatInt(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : "unavailable";
}

/** Render any opaque backend payload (uncertainty, thresholds, fixedFields) legibly. */
function formatLoose(value: unknown): string {
  if (value === null || value === undefined) return "unknown";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.length ? value.map((entry) => formatLoose(entry)).join(", ") : "none";
  }
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>).map(
      ([key, entry]) => `${key} ${formatLoose(entry)}`,
    );
    return parts.length ? parts.join(" · ") : "none";
  }
  return String(value);
}

/** `false` -> "not claimed"; a `true` claim is surfaced but never invented by the UI. */
function flagText(value: boolean | null, label: string): string | null {
  if (value === null) return null;
  return value ? `${label}: claimed` : `${label}: not claimed`;
}

/** The reward display unit requested for this surface; the mean itself is always a backend value. */
const REWARD_UNIT = "chests";

/** Measured reward mean with the display unit; an unknown mean stays explicitly "unknown". */
function formatReward(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${formatNumber(value)} ${REWARD_UNIT}`
    : "unknown";
}

/** `ownUnits.0.parameters.19` -> `ownUnits[0].parameters.19`. Presentation only; ids stay verbatim. */
function humanizePath(text: string): string {
  return text.replace(/(\d+)\./g, "[$1].");
}

/**
 * A measured average describes the sample; it is NOT a single-battle guarantee. Censoring and any
 * unresolved observations are stated so an option is never read as fully known.
 */
function observationWarning(option: PlayerRegionRewardOption): string | null {
  const parts: string[] = [];
  if (option.rewardUnresolved) parts.push("measured average is censored (unknown)");
  if (option.hasUnknown) parts.push(`${formatInt(option.unknownCount)} unresolved observation(s)`);
  if (option.uncertainty != null)
    parts.push("uncertainty reported - the average does not guarantee any single battle");
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * Target-verdict chips. The primary verdict is descriptive ("meets"/"below"); a censored mean is
 * "target not evaluated". Every chip is a plain description of the measurement - no option is ever
 * given a viability or quality-downgrade verdict here.
 */
function OptionBadges({ option, target }: { option: PlayerRegionRewardOption; target: number | null }) {
  return (
    <>
      {target !== null && option.meetsMeanTarget === true ? (
        <ToneBadge category="success">
          Meets your {formatNumber(target)} {REWARD_UNIT} target
        </ToneBadge>
      ) : null}
      {target !== null && option.meetsMeanTarget === false ? (
        <ToneBadge category="muted">
          Below your {formatNumber(target)} {REWARD_UNIT} target (kept below)
        </ToneBadge>
      ) : null}
      {target !== null && option.meetsMeanTarget === null ? (
        <ToneBadge category="warning">Mean unknown - target not evaluated</ToneBadge>
      ) : null}
      {option.hasUnknown ? (
        <ToneBadge category="warning">{formatInt(option.unknownCount)} unresolved - not fully known</ToneBadge>
      ) : null}
      {option.rewardUnresolved ? (
        <ToneBadge category="warning">measured average unknown (censored)</ToneBadge>
      ) : null}
    </>
  );
}

function policySummary(scope: PlayerRegionScope | null | undefined): string {
  if (!scope) return "scope not published";
  const parts: string[] = [];
  if (scope.encounterId != null) parts.push(`encounter ${scope.encounterId}`);
  if (scope.mode) parts.push(scope.mode);
  if (scope.referenceId) parts.push(`reference ${scope.referenceId}`);
  return parts.length ? parts.join(" · ") : "scope not published";
}

/**
 * A human heading for the archetype, derived ONLY from the fixed formation roster. Fodder and healer
 * units are recognised by name and everything else counts as the damage dealer, so the counts are
 * read from the roster - never hard-coded. Returns null when the roster proves nothing (empty, or no
 * fodder/healer named), so the caller falls back to the backend label instead of guessing.
 */
function formationComposition(formation: PlayerRegionFormation | null | undefined): string | null {
  const roster = formation?.roster ?? [];
  if (roster.length === 0) return null;
  let fodder = 0;
  let healer = 0;
  for (const name of roster) {
    if (/fodder/i.test(name)) fodder += 1;
    else if (/heal/i.test(name)) healer += 1;
  }
  if (fodder === 0 && healer === 0) return null;
  const dps = roster.length - fodder - healer;
  const parts: string[] = [];
  if (dps > 0) parts.push(`${dps} DPS`);
  if (fodder > 0) parts.push(`${fodder} fodder${fodder === 1 ? "" : "s"}`);
  if (healer > 0) parts.push(`${healer} healer${healer === 1 ? "" : "s"}`);
  return parts.length > 0 ? parts.join(" + ") : null;
}

/** A human label for a region kind; the raw kind stays available under Technical details. */
function regionKindLabel(kind: string | null | undefined): string {
  if (!kind) return "region";
  if (kind === "tested-alternatives") return "Tested alternatives";
  if (kind === "fixed-build") return "Fixed-build boundary";
  if (kind === "compensated") return "Compensated boundary";
  return kind.replace(/-/g, " ");
}

/* ------------------------------------------------------------------ */
/* Compact stat comparison                                             */
/* ------------------------------------------------------------------ */

/** The damage-dealer knobs the comparison shows, in a stable reading order. */
const DPS_COMPARISON_STATS: { key: string; label: string }[] = [
  { key: "dps.atk", label: "ATK" },
  { key: "dps.lck", label: "Luck" },
  { key: "dps.spd", label: "Speed" },
  { key: "dps.dex", label: "DEX" },
];

/** Compact DPS knobs for one option. A missing knob is shown as unknown, never as zero. */
function StatChips({ point }: { point: PlayerRegionPoint }) {
  return (
    <div className="mt-1 flex flex-wrap gap-1" data-player-region-vector>
      {DPS_COMPARISON_STATS.map(({ key, label }) => {
        const value = point.stats[key];
        const measured = typeof value === "number" && Number.isFinite(value);
        return (
          <span
            key={key}
            className={
              measured
                ? "rounded border px-1.5 py-0.5 text-[11px] tabular-nums"
                : "rounded border border-dashed px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground"
            }
          >
            {label} {measured ? formatNumber(value) : "unknown"}
          </span>
        );
      })}
    </div>
  );
}

/** Support HP/MP/DEF entries actually published on this point (any role). */
function supportStatEntries(stats: Record<string, number>): { key: string; value: number }[] {
  const entries: { key: string; value: number }[] = [];
  for (const [key, value] of Object.entries(stats)) {
    const stat = key.split(".").slice(1).join(".").toLowerCase();
    if (stat === "hp" || stat === "mp" || stat === "def") entries.push({ key, value });
  }
  return entries;
}

/**
 * Support HP/MP/DEF are kept separate from the DPS knobs. A tested value is shown only when the
 * backend measured one; otherwise this states the value is unknown and assumes no minimum.
 */
function SupportStats({ point }: { point: PlayerRegionPoint }) {
  const entries = supportStatEntries(point.stats);
  return (
    <p className="mt-1 text-[11px] text-muted-foreground" data-player-region-support-stats>
      <span className="font-medium">Support HP / MP / DEF</span>{" "}
      {entries.length > 0
        ? entries.map((entry) => `${statFieldLabel(entry.key)} ${formatNumber(entry.value)}`).join(" · ")
        : "not measured for this option - unknown, and no minimum is assumed"}
    </p>
  );
}

/** The full canonical vector, kept out of the main view for the Technical details disclosure. */
function FullStatVector({ point, allFields }: { point: PlayerRegionPoint; allFields: string[] }) {
  const entries = Object.entries(point.stats);
  const missing = pointMissingStats(point, allFields);
  return (
    <p data-player-region-full-vector>
      full measured stat vector:{" "}
      {entries.length > 0
        ? entries.map(([key, value]) => `${statFieldLabel(key)} ${formatNumber(value)}`).join(", ")
        : "none published"}
      {missing.length > 0
        ? ` · unknown (not zero): ${missing.map((field) => statFieldLabel(field)).join(", ")}`
        : ""}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Point row                                                           */
/* ------------------------------------------------------------------ */

function OptionTechnicalDetails({
  option,
  allFields,
  onSelectCandidate,
}: {
  option: PlayerRegionRewardOption;
  allFields: string[];
  onSelectCandidate?: ((candidateId: string) => void) | null;
}) {
  return (
    <details className="mt-1" data-player-region-technical>
      <summary className="cursor-pointer text-[11px] text-muted-foreground">
        Technical details (raw ids, paths, classification)
      </summary>
      <div className="mt-1 space-y-1 text-[11px] text-muted-foreground">
        <p data-player-region-raw-classification>
          raw classification: <span className="font-mono">{option.classification ?? "not published"}</span>
        </p>
        <p data-player-region-candidate>
          candidate <span className="font-mono">{option.candidateId ?? "not published"}</span>
        </p>
        <FullStatVector point={option.point} allFields={allFields} />
        <p>
          archetype <span className="font-mono">{option.archetypeId}</span> · region{" "}
          <span className="font-mono">{option.regionId ?? "not published"}</span>
          {option.regionKind ? ` (${option.regionKind})` : ""}
        </p>
        <p>
          context {option.context ?? "unknown"} · reachability {option.reachability ?? "unknown"}
        </p>
        <p>
          reference id <span className="font-mono">{option.referenceId ?? "not published"}</span>
          {option.referenceMeanEarned !== null
            ? ` · reference mean ${formatNumber(option.referenceMeanEarned)}`
            : ""}
          {option.tolerance !== null ? ` · reported tolerance ${formatNumber(option.tolerance)}` : ""}
          {option.referenceRequirement !== null
            ? ` · computed reference band ${formatNumber(option.referenceRequirement)}`
            : ""}
        </p>
        {option.changedFields.length > 0 ? (
          <p>changed fields: {option.changedFields.map((entry) => humanizePath(entry)).join(", ")}</p>
        ) : null}
        {option.fixedFields != null ? (
          <p data-player-region-fixed-fields>fixed fields: {formatLoose(option.fixedFields)}</p>
        ) : null}
        {option.thresholds != null ? (
          <p data-player-region-thresholds>reported thresholds: {formatLoose(option.thresholds)}</p>
        ) : null}
        {option.uncertainty != null ? (
          <p data-player-region-uncertainty>uncertainty: {formatLoose(option.uncertainty)}</p>
        ) : null}
        {option.unknownCount !== null ? (
          <p>unresolved observations: {formatInt(option.unknownCount)}</p>
        ) : null}
        {option.point.resourcePolicy != null ? (
          <p>resource policy: {formatLoose(option.point.resourcePolicy)}</p>
        ) : null}
        {onSelectCandidate && option.candidateId ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onSelectCandidate(option.candidateId as string)}
            data-action="open-tested-build"
          >
            Open this tested build
          </Button>
        ) : null}
      </div>
    </details>
  );
}

/**
 * One measured option. The primary line is the descriptive measured average plus the target verdict;
 * the optional `heading` is shown above it (used by the secondary stat explorer). Raw classification,
 * ids and canonical paths live under Technical details.
 */
function OptionRow({
  option,
  heading,
  target,
  allFields,
  onSelectCandidate,
}: {
  option: PlayerRegionRewardOption;
  heading?: ReactNode;
  target: number | null;
  allFields: string[];
  onSelectCandidate?: ((candidateId: string) => void) | null;
}) {
  const referenceText = referenceRetentionText(option);
  const warning = observationWarning(option);
  return (
    <div className="min-w-0 rounded-md border p-2" data-player-region-option>
      <div className="flex flex-wrap items-center gap-2">
        {heading ? <span className="text-xs font-medium tabular-nums">{heading}</span> : null}
        <span className="text-xs font-medium tabular-nums" data-point-measured-average>
          Measured average: {formatReward(option.meanEarned)}
        </span>
        <OptionBadges option={option} target={target} />
      </div>
      <StatChips point={option.point} />
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] tabular-nums text-muted-foreground">
        {option.meanEarned === null && option.partialMean !== null ? (
          <span>average over known results {formatNumber(option.partialMean)}; other outcomes remain unknown</span>
        ) : null}
        <span>
          samples {formatInt(option.samples)}
          {option.total != null ? ` / total ${formatInt(option.total)}` : " / total unavailable"}
        </span>
        <span>
          {option.uncertainty != null
            ? `uncertainty ${formatLoose(option.uncertainty)}`
            : "uncertainty not published"}
        </span>
        {option.median !== null ? <span>median {formatNumber(option.median)}</span> : null}
        <span>evidence window {option.evidenceStatus ?? "unknown"}</span>
      </div>
      <SupportStats point={option.point} />
      {referenceText ? (
        <p
          className={
            option.meetsReference === false
              ? "mt-1 text-[11px] text-amber-700 dark:text-amber-300"
              : "mt-1 text-[11px] text-muted-foreground"
          }
          data-player-region-reference-band
        >
          Reference retention (separate from your target): {referenceText}
        </p>
      ) : null}
      {warning ? (
        <p
          className="mt-1 text-[11px] text-amber-700 dark:text-amber-300"
          data-player-region-not-fully-known
        >
          Evidence limits: {warning}
        </p>
      ) : null}
      <OptionTechnicalDetails option={option} allFields={allFields} onSelectCandidate={onSelectCandidate} />
    </div>
  );
}

function SparseRow({ sparse, field }: { sparse: PlayerRegionSparsePoint; field: string }) {
  return (
    <div className="min-w-0 rounded-md border border-dashed p-2" data-player-region-sparse>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">{statFieldLabel(field)} = unknown</span>
        <ToneBadge category="warning">sparse vector - not fully supported</ToneBadge>
        <Badge variant="outline" className="text-[10px]">
          evidence window {sparse.evidenceStatus ?? "unknown"}
        </Badge>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        This candidate publishes no measured {statFieldLabel(field)} value, so it is unknown - not zero
        and not an interpolated value.
        {sparse.meanEarned != null ? ` Measured average: ${formatReward(sparse.meanEarned)}.` : ""}
        {sparse.missingStats.length > 0
          ? ` · missing (unknown): ${sparse.missingStats.map((key) => statFieldLabel(key)).join(", ")}`
          : ""}
      </p>
      <details className="mt-1" data-player-region-technical>
        <summary className="cursor-pointer text-[11px] text-muted-foreground">
          Technical details (raw ids, classification)
        </summary>
        <div className="mt-1 space-y-1 text-[11px] text-muted-foreground">
          <p data-player-region-raw-classification>
            raw classification: <span className="font-mono">{sparse.classification ?? "not published"}</span>
          </p>
          <p>
            candidate <span className="font-mono">{sparse.candidateId ?? "not published"}</span>
          </p>
          <p>
            archetype <span className="font-mono">{sparse.archetypeId}</span> · region{" "}
            <span className="font-mono">{sparse.regionId ?? "not published"}</span>
          </p>
        </div>
      </details>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Region block                                                        */
/* ------------------------------------------------------------------ */

/**
 * A measured option rendered by `OptionRow`. The reward view passes plain `PlayerRegionRewardOption`s;
 * the secondary stat explorer passes `PlayerRegionQueryMatch`es (which carry the extra `value`), so the
 * view type leaves `value` optional.
 */
type RewardOptionView = PlayerRegionRewardOption & { value?: number | null };

function RegionBlock({
  region,
  options,
  sparse,
  field,
  target,
  allFields,
  headingFor,
  onSelectCandidate,
}: {
  region: PlayerRegion;
  options: RewardOptionView[];
  sparse: PlayerRegionSparsePoint[];
  field?: string;
  target: number | null;
  allFields: string[];
  headingFor?: (option: RewardOptionView) => ReactNode;
  onSelectCandidate?: ((candidateId: string) => void) | null;
}) {
  const coverageFlags = [
    flagText(region.continuousCoverage, "Values between tested points are covered"),
    flagText(region.safeCartesianProduct, "Stats may be combined freely"),
  ].filter((entry): entry is string => entry !== null);
  return (
    <div className="mt-2 min-w-0 rounded-md border border-dashed p-2" data-player-region-region>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-medium">{region.label ?? region.id ?? "region"}</span>
        {region.kind ? (
          <Badge variant="outline" className="text-[10px]">
            {regionKindLabel(region.kind)}
          </Badge>
        ) : null}
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        {options.map((option, index) => (
          <OptionRow
            key={`${option.candidateId ?? option.regionId ?? "option"}-${index}`}
            option={option}
            heading={headingFor ? headingFor(option) : undefined}
            target={target}
            allFields={allFields}
            onSelectCandidate={onSelectCandidate}
          />
        ))}
        {sparse.map((entry, index) => (
          <SparseRow
            key={`sparse-${entry.candidateId ?? index}-${index}`}
            sparse={entry}
            field={field ?? ""}
          />
        ))}
      </div>
      {region.gaps.length > 0 ? (
        <div className="mt-2 text-[11px] text-muted-foreground" data-player-region-gaps>
          <span className="font-medium">Explicit gaps (not interpolated):</span>
          <ul className="list-disc space-y-0.5 pl-4">
            {region.gaps.map((gap, index) => (
              <li key={index}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {region.lowImpact.length > 0 ? (
        <div className="mt-1 text-[11px] text-muted-foreground" data-player-region-low-impact>
          <span className="font-medium">Controlled low-impact evidence:</span>
          <ul className="list-disc space-y-0.5 pl-4">
            {region.lowImpact.map((entry, index) => (
              <li key={index}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {region.support.length > 0 ? (
        <div className="mt-1 text-[11px] text-muted-foreground" data-player-region-support>
          <span className="font-medium">Controlled boundary support:</span>
          <ul className="list-disc space-y-0.5 pl-4">
            {region.support.map((entry, index) => (
              <li key={index}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {coverageFlags.length > 0 ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{coverageFlags.join(" · ")}</p>
      ) : null}
      {region.conditions.length > 0 ? (
        <details className="mt-1" data-player-region-technical>
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            Technical details (raw region id and conditions)
          </summary>
          <div className="mt-1 space-y-1 text-[11px] text-muted-foreground">
            <p>
              region id <span className="font-mono">{region.id}</span>
            </p>
            <ul className="list-disc space-y-0.5 pl-4">
              {region.conditions.map((condition, index) => (
                <li key={index}>
                  <span className="font-mono">{condition}</span>
                </li>
              ))}
            </ul>
          </div>
        </details>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Archetype block                                                     */
/* ------------------------------------------------------------------ */

/** Render `fixedRequirements.other` human-legibly; object values (e.g. `startProfile`) are kept. */
function OtherRequirements({ other }: { other: Record<string, unknown> }) {
  const rows = describeFixedRequirementsOther(other);
  return (
    <ul className="mt-0.5 space-y-0.5 text-muted-foreground tabular-nums">
      {rows.map((row) => (
        <li key={row.key}>
          {row.key}: {row.text}
        </li>
      ))}
    </ul>
  );
}

function ArchetypeBlock({
  archetype,
  options,
  sparse,
  field,
  target,
  allFields,
  headingFor,
  onSelectCandidate,
}: {
  archetype: PlayerRegionArchetype;
  options: RewardOptionView[];
  sparse: PlayerRegionSparsePoint[];
  field?: string;
  target: number | null;
  allFields: string[];
  headingFor?: (option: RewardOptionView) => ReactNode;
  onSelectCandidate?: ((candidateId: string) => void) | null;
}) {
  const byRegion = new Map<string, RewardOptionView[]>();
  for (const option of options) {
    const key = option.regionId ?? "";
    const list = byRegion.get(key);
    if (list) list.push(option);
    else byRegion.set(key, [option]);
  }
  const sparseByRegion = new Map<string, PlayerRegionSparsePoint[]>();
  for (const entry of sparse) {
    const key = entry.regionId ?? "";
    const list = sparseByRegion.get(key);
    if (list) list.push(entry);
    else sparseByRegion.set(key, [entry]);
  }
  const regions = archetype.regions.filter((region) => {
    const key = region.id ?? "";
    return byRegion.has(key) || sparseByRegion.has(key);
  });
  const fixed = archetype.fixedRequirements;
  const formation = fixed?.formation ?? null;
  const composition = formationComposition(formation);
  const heading = composition ?? archetype.label ?? archetype.id;
  return (
    <section className="min-w-0 rounded-lg border p-3" data-player-region-archetype>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{heading}</h3>
        <ToneBadge category="muted">{archetype.context ?? "Strategy"}</ToneBadge>
        
      </div>
      {composition && fixed?.policy ? (
        <p className="mt-0.5 text-[11px] text-muted-foreground">Finish rule: {fixed.policy}</p>
      ) : null}
      {archetype.description ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{archetype.description}</p>
      ) : null}

      <details className="mt-2">
        <summary className="cursor-pointer text-xs">
          What stays fixed (formation, loadout, finish rule)
        </summary>
        <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
          <div className="min-w-0 text-[11px]">
            <p className="font-medium">Formation</p>
            {formation ? (
              <>
                {formation.roster.length > 0 ? (
                  <p className="mt-0.5 flex flex-wrap gap-1">
                    {formation.roster.map((name, index) => (
                      <Badge key={`${name}-${index}`} variant="outline" className="text-[10px]">
                        {name}
                      </Badge>
                    ))}
                  </p>
                ) : (
                  <p className="mt-0.5 text-muted-foreground">roster not published</p>
                )}
                {formation.placed ? (
                  <p className="mt-0.5 text-muted-foreground tabular-nums">
                    placement:{" "}
                    {formation.placed
                      .map((slot) => `${slot.name ?? "unknown"} at grid ${formatInt(slot.grid)}`)
                      .join(" · ")}
                  </p>
                ) : (
                  <p className="mt-0.5 text-muted-foreground">
                    placement {formation.placementStatus ?? "unknown"}
                  </p>
                )}
              </>
            ) : (
              <p className="mt-0.5 text-muted-foreground">not published (unknown)</p>
            )}
          </div>
          <div className="min-w-0 text-[11px]">
            <p className="font-medium">Fixed loadout</p>
            {fixed?.skills && fixed.skills.length > 0 ? (
              <p className="mt-0.5 text-muted-foreground">
                {fixed.skills.length} unit(s) hold a fixed skill / weapon set; raw ids are under
                Technical details.
              </p>
            ) : (
              <p className="mt-0.5 text-muted-foreground">not published (unknown)</p>
            )}
            <p className="mt-1 font-medium">Finish rule</p>
            <p className="mt-0.5 text-muted-foreground">
              {fixed?.policy ?? "unknown (no fixed finish rule published)"}
            </p>
          </div>
        </div>

        <details className="mt-2 text-[11px] text-muted-foreground" data-player-region-technical>
          <summary className="cursor-pointer">
            Technical details (raw ids, skills, structural context)
          </summary>
          <p className="mt-1">
            archetype id <span className="font-mono">{archetype.id}</span>
          </p>
          {fixed?.skills && fixed.skills.length > 0 ? (
            <ul className="mt-1 space-y-0.5">
              {fixed.skills.map((entry, index) => (
                <li key={index}>
                  {entry.unit ?? "unit unknown"}
                  {entry.skills.length > 0
                    ? ` · skills ${entry.skills.join(", ")}`
                    : " · skills not published"}
                  {entry.invocationLevels != null
                    ? ` · invocation ${formatLoose(entry.invocationLevels)}`
                    : ""}
                  {entry.weaponId ? ` · weapon ${entry.weaponId}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {fixed?.other ? (
            <OtherRequirements other={fixed.other} />
          ) : (
            <p className="mt-1">structured context not published (unknown)</p>
          )}
        </details>
      </details>
      {archetype.unknowns.length > 0 ? (
        <div className="mt-2 text-[11px] text-muted-foreground" data-player-region-unknowns>
          <span className="font-medium">What is still unknown here:</span>
          <ul className="list-disc space-y-0.5 pl-4">
            {archetype.unknowns.map((entry, index) => (
              <li key={index}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {regions.map((region) => {
        const key = region.id ?? "";
        return (
          <RegionBlock
            key={key}
            region={region}
            options={byRegion.get(key) ?? []}
            sparse={sparseByRegion.get(key) ?? []}
            field={field}
            target={target}
            allFields={allFields}
            headingFor={headingFor}
            onSelectCandidate={onSelectCandidate}
          />
        );
      })}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Linked stat dials (primary strategy controls)                       */
/* ------------------------------------------------------------------ */

type RewardCategory = "meets" | "below" | "unknown";
type CategorizedOption = { option: PlayerRegionRewardOption; category: RewardCategory };

const ROLE_NOTE: Record<string, string> = {
  dps: "damage dealer",
  support: "support",
};

function formatSigned(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${formatNumber(rounded)}`;
}

/**
 * One linked dial: an optional editable number, a slider that snaps ONLY to the measured stops, a
 * "min measured" button (the lowest stop seen in this evidence) and a reset / "free" button. A typed
 * value that is not a measured stop is allowed but resolves to unknown - nothing is snapped.
 */
function StatDial({
  axis,
  value,
  onChange,
}: {
  axis: StatAxis;
  value: number | undefined;
  onChange: (value: number | null) => void;
}) {
  const [draft, setDraft] = useState(value === undefined ? "" : String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(value === undefined ? "" : String(value));
  }, [value]);
  const pinned = value !== undefined;
  const measured = pinned && axisHasStop(axis, value as number);
  const index = pinned && measured ? axis.stops.indexOf(value as number) : 0;
  const commit = (raw: string) => {
    const text = raw.trim();
    if (text === "") {
      setDraft("");
      onChange(null);
      return;
    }
    const parsed = Number(text);
    if (Number.isFinite(parsed)) {
      setDraft(String(parsed));
      onChange(parsed);
    } else {
      setDraft(value === undefined ? "" : String(value));
      onChange(null);
    }
  };
  return (
    <div className="min-w-0 rounded-md border p-2" data-stat-dial data-stat-dial-field={axis.field}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">
          {statFieldLabel(axis.field)}{" "}
          <span className="text-[10px] font-normal text-muted-foreground">
            {ROLE_NOTE[axis.role] ?? axis.role}
          </span>
        </span>
        <span className="text-[10px] tabular-nums text-muted-foreground" data-stat-dial-status>
          {!pinned
            ? "free - not pinned"
            : measured
              ? "pinned to a measured stop"
              : "typed value not measured - unknown"}
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-end gap-2">
        <div className="min-w-0">
          <Label className="text-[10px]">Pinned value (optional)</Label>
          <Input
            className="h-7 w-20"
            inputMode="numeric"
            placeholder="free"
            value={draft}
            onFocus={() => {
              focused.current = true;
            }}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              focused.current = false;
              commit(draft);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") (event.target as HTMLInputElement).blur();
            }}
            aria-label={`${statFieldLabel(axis.field)} pinned value; leave blank to keep the axis free`}
            data-stat-dial-input
          />
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-[11px]"
          onClick={() => onChange(axisMinimum(axis))}
          title="Lowest stop OBSERVED in this evidence only - never a proven global floor or an independent guarantee."
          data-stat-dial-min
        >
          Min measured ({formatNumber(axisMinimum(axis))})
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 text-[11px]"
          onClick={() => onChange(null)}
          disabled={!pinned}
          data-stat-dial-reset
        >
          Reset / free
        </Button>
      </div>
      <Slider
        className="mt-2 w-full"
        min={0}
        max={Math.max(1, axis.stops.length - 1)}
        step={1}
        value={[index]}
        disabled={axis.stops.length < 2}
        onValueChange={(values) => {
          const next = values[0];
          if (typeof next === "number" && axis.stops[next] !== undefined) onChange(axis.stops[next]);
        }}
        aria-label={`${statFieldLabel(axis.field)} measured stops; the slider snaps only to stops observed here`}
        aria-valuetext={formatNumber(axis.stops[index] ?? axis.min)}
      />
      <p className="mt-1 text-[10px] tabular-nums text-muted-foreground" data-stat-dial-stops>
        Measured stops (only these are known): {axis.stops.map((stop) => formatNumber(stop)).join(", ")}
      </p>
    </div>
  );
}

/** One measured combination or closest alternative: reward, uncertainty and the stat compensation. */
function StatCombinationCard({ combo }: { combo: StatCombination }) {
  const changed = combo.deltas.filter((delta) => delta.delta !== null && delta.delta !== 0);
  return (
    <div className="min-w-0 rounded-md border p-2" data-stat-combination>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium tabular-nums" data-stat-combination-reward>
          Measured average: {formatReward(combo.meanEarned)}
        </span>
        <Badge variant="outline" className="text-[10px]">
          evidence window {combo.evidenceStatus ?? "unknown"}
        </Badge>
        {combo.unknownCount !== null && combo.unknownCount > 0 ? (
          <ToneBadge category="warning">{formatInt(combo.unknownCount)} unresolved</ToneBadge>
        ) : null}
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground">
        {combo.label ?? combo.candidateId ?? "measured point"}
      </p>
      <p className="mt-1 flex flex-wrap gap-1 text-[10px] tabular-nums" data-stat-combination-others>
        {combo.otherStats.length > 0
          ? combo.otherStats.map((stat) => (
              <span key={stat.field} className="rounded border px-1.5 py-0.5">
                {statFieldLabel(stat.field)} {formatNumber(stat.value)}
              </span>
            ))
          : <span className="text-muted-foreground">no other measured stat published</span>}
      </p>
      {changed.length > 0 ? (
        <p className="mt-1 text-[10px] tabular-nums text-muted-foreground" data-stat-combination-deltas>
          To use this measured alternative, change:{" "}
          {changed
            .map(
              (delta) =>
                `${statFieldLabel(delta.field)} ${formatNumber(delta.measured ?? delta.pinned)} (${formatSigned(
                  delta.delta as number,
                )})`,
            )
            .join(" · ")}
        </p>
      ) : null}
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] tabular-nums text-muted-foreground">
        <span>
          samples {formatInt(combo.samples)}
          {combo.total != null ? ` / total ${formatInt(combo.total)}` : ""}
        </span>
        <span>
          {combo.uncertainty != null
            ? `uncertainty ${formatLoose(combo.uncertainty)}`
            : "uncertainty not published"}
        </span>
      </div>
    </div>
  );
}

/**
 * The primary strategy control: linked stat dials for EVERY stat the archetype own points measured.
 * Changing a dial pins its observed value; the panel then shows the compatible measured combinations
 * (reward, uncertainty and the other stats they used) or, when nothing matches, an explicit unknown
 * with the closest measured alternatives and the exact change each pin would need.
 */
function LinkedStatDials({
  archetype,
  allFields,
  pins,
  onPinChange,
}: {
  archetype: PlayerRegionArchetype;
  allFields: string[];
  pins: Record<string, number>;
  onPinChange: (field: string, value: number | null) => void;
}) {
  const points = useMemo(() => collectStatPoints(archetype), [archetype]);
  const axes = useMemo(() => statAxes(points), [points]);
  const pinList = useMemo<StatPin[]>(
    () => Object.entries(pins).map(([field, value]) => ({ field, value })),
    [pins],
  );
  const resolution = useMemo(
    () => resolveStatPins(points, pinList, { axes }),
    [points, pinList, axes],
  );
  const observedFields = new Set(axes.map((axis) => axis.field));
  const absentFields = allFields.filter((field) => !observedFields.has(field));
  return (
    <div className="mt-3 min-w-0 rounded-lg border p-3" data-stat-dials>
      <div className="flex flex-wrap items-baseline gap-2">
        <p className="text-xs font-medium">Linked stat dials</p>
        <span className="text-[10px] text-muted-foreground">
          Pin a measured value; compatible measured options and the other stats they need appear below.
        </span>
      </div>
      {axes.length === 0 ? (
        <p className="mt-2 text-[11px] text-muted-foreground" data-stat-dials-empty>
          No measured stat values in this archetype, so there is no dial. Every axis stays unknown.
        </p>
      ) : (
        <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {axes.map((axis) => (
            <StatDial
              key={axis.field}
              axis={axis}
              value={pins[axis.field]}
              onChange={(value) => onPinChange(axis.field, value)}
            />
          ))}
        </div>
      )}
      <p className="mt-1.5 text-[10px] text-muted-foreground">
        "Min measured" only pins the lowest stop seen in this evidence. It is not a proven floor and
        the other stats are never assumed to sit at their own minimum.
      </p>
      {absentFields.length > 0 ? (
        <p className="mt-1 text-[10px] text-muted-foreground" data-stat-dials-absent>
          Not measured in this archetype (unknown, never zero):{" "}
          {absentFields.map((field) => statFieldLabel(field)).join(", ")}.
        </p>
      ) : null}

      <div className="mt-2" data-stat-pins-summary>
        {resolution.state === "free" ? (
          <p className="text-[11px] text-muted-foreground" data-stat-pins-resolution="free">
            Pin any dial to see which measured combinations stay compatible. Nothing is pooled across
            archetypes and no axis is assumed to be independently at its minimum.
          </p>
        ) : resolution.state === "matched" ? (
          <>
            <p className="text-[11px] text-muted-foreground" data-stat-pins-resolution="matched">
              {resolution.combinations.length} measured combination
              {resolution.combinations.length === 1 ? "" : "s"} in this archetype match your pinned
              value{resolution.pins.length === 1 ? "" : "s"}. The other stats listed are the measured
              values that option actually used.
            </p>
            <div className="mt-1 grid gap-2 md:grid-cols-2">
              {resolution.combinations.map((combo, index) => (
                <StatCombinationCard
                  key={`${combo.candidateId ?? combo.regionId ?? "combo"}-${index}`}
                  combo={combo}
                />
              ))}
            </div>
          </>
        ) : (
          <>
            <Alert data-stat-pins-resolution="unknown" data-stat-dial-unknown>
              <AlertTitle>Not measured - explicit unknown</AlertTitle>
              <AlertDescription>{resolution.axes.reduce((message, axis) => message.replaceAll(axis.field, statFieldLabel(axis.field)), resolution.unknownReason ?? "This combination has not been measured.")}</AlertDescription>
            </Alert>
            {resolution.nearest.length > 0 ? (
              <div className="mt-2">
                <p className="text-[11px] font-medium">Closest measured alternatives</p>
                <div className="mt-1 grid gap-2 md:grid-cols-2">
                  {resolution.nearest.map((combo, index) => (
                    <StatCombinationCard
                      key={`${combo.candidateId ?? combo.regionId ?? "near"}-${index}`}
                      combo={combo}
                    />
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* One strategy card per archetype                                     */
/* ------------------------------------------------------------------ */

/**
 * A single archetype card. The linked dials are primary; the reward categories (meets / below /
 * unknown mean) are SECTIONS inside this one card, so the same archetype is never repeated as several
 * strategies and options are never pooled across archetypes. Counts stay visible per category.
 */
function StrategyArchetypeCard({
  archetype,
  options,
  target,
  allFields,
  pins,
  onPinChange,
  onSelectCandidate,
}: {
  archetype: PlayerRegionArchetype;
  options: CategorizedOption[];
  target: number | null;
  allFields: string[];
  pins: Record<string, number>;
  onPinChange: (field: string, value: number | null) => void;
  onSelectCandidate?: ((candidateId: string) => void) | null;
}) {
  const fixed = archetype.fixedRequirements;
  const formation = fixed?.formation ?? null;
  const composition = formationComposition(formation);
  const heading = composition ?? archetype.label ?? archetype.id;
  const meets = options.filter((entry) => entry.category === "meets");
  const below = options.filter((entry) => entry.category === "below");
  const unknown = options.filter((entry) => entry.category === "unknown");
  const renderRow = (entry: CategorizedOption, index: number) => (
    <OptionRow
      key={`${entry.option.candidateId ?? entry.option.regionId ?? "option"}-${entry.category}-${index}`}
      option={entry.option}
      target={target}
      allFields={allFields}
      onSelectCandidate={onSelectCandidate}
    />
  );
  return (
    <section
      className="min-w-0 rounded-lg border p-3"
      data-player-region-archetype
      data-player-region-strategy-card
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{heading}</h3>
        <ToneBadge category="muted">{archetype.context ?? "Strategy"}</ToneBadge>
        <span className="text-[10px] tabular-nums text-muted-foreground">
          {meets.length} meet · {below.length} below · {unknown.length} unknown mean
        </span>
      </div>
      {composition && fixed?.policy ? (
        <p className="mt-0.5 text-[11px] text-muted-foreground">Finish rule: {fixed.policy}</p>
      ) : null}
      {archetype.description ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{archetype.description}</p>
      ) : null}

      <LinkedStatDials
        archetype={archetype}
        allFields={allFields}
        pins={pins}
        onPinChange={onPinChange}
      />

      <div className="mt-3" data-player-region-meets>
        <p className="text-xs font-medium">
          {target === null
            ? `Measured options (${meets.length})`
            : `Meets your ${formatNumber(target)} ${REWARD_UNIT} target (${meets.length})`}
        </p>
        {meets.length > 0 ? (
          <div className="mt-1 grid gap-2 md:grid-cols-2">{meets.map(renderRow)}</div>
        ) : (
          <p className="mt-1 text-[11px] text-muted-foreground">
            No measured option meets this target. Lower-reward options are still kept below.
          </p>
        )}
      </div>

      {below.length > 0 ? (
        <div className="mt-2" data-player-region-below>
          <p className="text-xs font-medium">Below your target - kept ({below.length})</p>
          <div className="mt-1 grid gap-2 md:grid-cols-2">{below.map(renderRow)}</div>
        </div>
      ) : null}

      {unknown.length > 0 ? (
        <div className="mt-2" data-player-region-unknown-mean>
          <p className="text-xs font-medium">
            Mean unknown - target not evaluated ({unknown.length})
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            These options publish no measured mean, so they are neither meeting nor below the target.
          </p>
          <div className="mt-1 grid gap-2 md:grid-cols-2">{unknown.map(renderRow)}</div>
        </div>
      ) : null}

      <details className="mt-3">
        <summary className="cursor-pointer text-xs">
          What stays fixed (formation, loadout, finish rule)
        </summary>
        <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
          <div className="min-w-0 text-[11px]">
            <p className="font-medium">Formation</p>
            {formation ? (
              <>
                {formation.roster.length > 0 ? (
                  <p className="mt-0.5 flex flex-wrap gap-1">
                    {formation.roster.map((name, index) => (
                      <Badge key={`${name}-${index}`} variant="outline" className="text-[10px]">
                        {name}
                      </Badge>
                    ))}
                  </p>
                ) : (
                  <p className="mt-0.5 text-muted-foreground">roster not published</p>
                )}
                <p className="mt-0.5 text-muted-foreground tabular-nums">
                  placement {formation.placementStatus ?? "unknown"}
                </p>
              </>
            ) : (
              <p className="mt-0.5 text-muted-foreground">not published (unknown)</p>
            )}
          </div>
          <div className="min-w-0 text-[11px]">
            <p className="font-medium">Fixed loadout</p>
            <p className="mt-0.5 text-muted-foreground">
              {fixed?.skills && fixed.skills.length > 0
                ? `${fixed.skills.length} unit(s) hold a fixed skill / weapon set.`
                : "not published (unknown)"}
            </p>
            <p className="mt-1 font-medium">Finish rule</p>
            <p className="mt-0.5 text-muted-foreground">
              {fixed?.policy ?? "unknown (no fixed finish rule published)"}
            </p>
          </div>
        </div>
        <details className="mt-2 text-[11px] text-muted-foreground" data-player-region-technical>
          <summary className="cursor-pointer">
            Technical details (raw ids, skills, structural context)
          </summary>
          <p className="mt-1">
            archetype id <span className="font-mono">{archetype.id}</span>
          </p>
          {fixed?.skills && fixed.skills.length > 0 ? (
            <ul className="mt-1 space-y-0.5">
              {fixed.skills.map((entry, index) => (
                <li key={index}>
                  {entry.unit ?? "unit unknown"}
                  {entry.skills.length > 0
                    ? ` · skills ${entry.skills.join(", ")}`
                    : " · skills not published"}
                  {entry.invocationLevels != null
                    ? ` · invocation ${formatLoose(entry.invocationLevels)}`
                    : ""}
                  {entry.weaponId ? ` · weapon ${entry.weaponId}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {fixed?.other ? (
            <OtherRequirements other={fixed.other} />
          ) : (
            <p className="mt-1">structured context not published (unknown)</p>
          )}
        </details>
      </details>

      <details className="mt-3" open>
        <summary className="cursor-pointer text-xs">Tested regions, support evidence and unknown gaps</summary>
        {archetype.regions.map((region, index) => (
          <RegionBlock key={region.id ?? index} region={region} options={[]} sparse={[]}
            target={target} allFields={allFields} />
        ))}
      </details>

      {archetype.unknowns.length > 0 ? (
        <div className="mt-2 text-[11px] text-muted-foreground" data-player-region-unknowns>
          <span className="font-medium">What is still unknown here:</span>
          <ul className="list-disc space-y-0.5 pl-4">
            {archetype.unknowns.map((entry, index) => (
              <li key={index}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

export default function OptimizerPlayerRegions({
  loadSummary,
  scopeKey,
  scopeLabel = null,
  available = true,
  autoLoad = false,
  onSelectCandidate = null,
  disabled = false,
}: OptimizerPlayerRegionsProps) {
  const [summary, setSummary] = useState<PlayerRegionsSummary | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [invalidated, setInvalidated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [field, setField] = useState("");
  const [exactDraft, setExactDraft] = useState("");
  const [lowDraft, setLowDraft] = useState("");
  const [highDraft, setHighDraft] = useState("");
  const [targetDraft, setTargetDraft] = useState("");
  /* Linked stat dials: pins are per archetype, so pinning a stat in one card never leaks into another. */
  const [pinsByArchetype, setPinsByArchetype] = useState<Record<string, Record<string, number>>>({});

  const loadRef = useRef(loadSummary);
  loadRef.current = loadSummary;
  const scopeKeyRef = useRef(scopeKey);
  scopeKeyRef.current = scopeKey;
  const hadSummary = useRef(false);
  hadSummary.current = summary !== null;
  const prevKey = useRef(scopeKey);
  const autoLoadedKey = useRef<string | null>(null);

  /*
   * Invalidate on scope/library/revision change: a summary measured for one encounter is dropped, so
   * it can never be shown for another. Nothing is fetched here; the reader presses Load again.
   */
  useEffect(() => {
    if (prevKey.current === scopeKey) return;
    prevKey.current = scopeKey;
    if (hadSummary.current) setInvalidated(true);
    setSummary(null);
    setLoadedKey(null);
    setError(null);
  }, [scopeKey]);

  const handleLoad = useCallback(async () => {
    setLoading(true);
    setError(null);
    setInvalidated(false);
    try {
      const raw = await loadRef.current();
      /* A reply that lands after the scope changed must not populate the new scope. */
      if (scopeKeyRef.current !== scopeKey) return;
      const normalized = normalizePlayerRegionsSummary(raw);
      if (!normalized) {
        setSummary(null);
        setLoadedKey(null);
        setError("The host returned no player-region summary. Nothing is inferred in its place.");
      } else {
        setSummary(normalized);
        setLoadedKey(scopeKey);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [scopeKey]);

  /*
   * `autoLoad` loads once on mount and once per scope/library/revision change, guarded by the same
   * stale-response check inside `handleLoad`. It runs from this effect (never from a status poll) and
   * fires at most once per scope, so a re-render or a recreated callback never re-fetches.
   */
  useEffect(() => {
    if (!autoLoad || !available) return;
    if (autoLoadedKey.current === scopeKey) return;
    autoLoadedKey.current = scopeKey;
    void handleLoad();
  }, [autoLoad, available, scopeKey, handleLoad]);

  const fields = useMemo(() => availableStatFields(summary), [summary]);
  useEffect(() => {
    if (fields.length === 0) return;
    if (!fields.includes(field)) setField(fields[0]);
  }, [fields, field]);

  const exact = parseFilterInput(exactDraft);
  const low = parseFilterInput(lowDraft);
  const high = parseFilterInput(highDraft);
  const target = parseFilterInput(targetDraft);
  const queryInvalid =
    filterInputInvalid(exactDraft) || filterInputInvalid(lowDraft) || filterInputInvalid(highDraft);

  /*
   * The slider spans ONLY the observed values published on the selected field. It is a convenience
   * entry for the exact filter: an unsampled integer position resolves to explicit unknown because the
   * exact query matches measured points only - it never snaps to a nearby point and never interpolates
   * reward. The numeric inputs stay unbounded, so a query outside the observed span is still allowed.
   */
  const span = useMemo(() => observedFieldSpan(summary, field), [summary, field]);
  const sliderPosition = useMemo(() => {
    if (!span) return null;
    const base = exact !== null ? exact : span.min;
    return Math.round(Math.min(Math.max(base, span.min), span.max));
  }, [span, exact]);
  const exactOutsideSpan = span !== null && exact !== null && (exact < span.min || exact > span.max);

  const result = useMemo(
    () => queryPlayerRegions(summary, { field, exact, low, high, meanTarget: target }),
    [summary, field, exact, low, high, target],
  );

  /*
   * The MAIN control: the expected-reward target. `organizeByMeanTarget` only ORGANISES the same
   * measured options - it never drops one. Its slider is bounded by the OBSERVED measured means only,
   * and an unknown mean is kept in `unknown` (never counted as below).
   */
  const reward = useMemo(() => organizeByMeanTarget(summary, target), [summary, target]);
  const meanSpan = useMemo(() => observedMeanSpan(summary), [summary]);

  /*
   * Every measured option grouped under ONE entry per archetype, keeping its reward category. The
   * listing then renders a single card per archetype instead of repeating the archetype once per
   * category; the categories stay visible as sections and counts inside that card.
   */
  const categorizedByArchetype = useMemo(() => {
    const map = new Map<string, CategorizedOption[]>();
    const push = (option: PlayerRegionRewardOption, category: RewardCategory) => {
      const list = map.get(option.archetypeId);
      if (list) list.push({ option, category });
      else map.set(option.archetypeId, [{ option, category }]);
    };
    for (const option of reward.meets) push(option, "meets");
    for (const option of reward.below) push(option, "below");
    for (const option of reward.unknown) push(option, "unknown");
    return map;
  }, [reward]);

  /* A pin belongs to one loaded summary: drop every pin when the summary or scope changes. */
  useEffect(() => {
    setPinsByArchetype({});
  }, [summary, scopeKey]);

  const setPin = useCallback((archetypeId: string, field: string, value: number | null) => {
    setPinsByArchetype((current) => {
      const group: Record<string, number> = { ...(current[archetypeId] ?? {}) };
      if (value === null) delete group[field];
      else group[field] = value;
      const next = { ...current };
      if (Object.keys(group).length === 0) delete next[archetypeId];
      else next[archetypeId] = group;
      return next;
    });
  }, []);
  const rewardSliderPosition = useMemo(() => {
    if (!meanSpan) return null;
    const base = target !== null ? target : meanSpan.min;
    return Math.round(Math.min(Math.max(base, meanSpan.min), meanSpan.max));
  }, [meanSpan, target]);
  const targetOutsideSpan =
    meanSpan !== null && target !== null && (target < meanSpan.min || target > meanSpan.max);

  const archetypeById = useMemo(() => {
    const map = new Map<string, PlayerRegionArchetype>();
    for (const archetype of summary?.archetypes ?? []) map.set(archetype.id, archetype);
    return map;
  }, [summary]);

  const grouped = useMemo(() => {
    const map = new Map<
      string,
      { matches: PlayerRegionQueryMatch[]; sparse: PlayerRegionSparsePoint[] }
    >();
    const entryFor = (id: string) => {
      const existing = map.get(id);
      if (existing) return existing;
      const created = {
        matches: [] as PlayerRegionQueryMatch[],
        sparse: [] as PlayerRegionSparsePoint[],
      };
      map.set(id, created);
      return created;
    };
    for (const match of result.matches) entryFor(match.archetypeId).matches.push(match);
    for (const entry of result.sparse) entryFor(entry.archetypeId).sparse.push(entry);
    return map;
  }, [result]);

  const statusUnavailable =
    summary !== null && summary.status !== null && summary.status !== "available";
  const stale = loadedKey !== null && loadedKey !== scopeKey;
  const coverage = summary?.coverage ?? null;
  const truncated = coverage?.truncated === true;

  return (
    <Card className="min-w-0" data-optimizer-player-regions>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base">Strategy guide</CardTitle>
            <CardDescription>
              What can I bring? Set the reward you are aiming for, then compare the builds the search
              actually measured. Lower-reward options stay listed; untested combinations stay unknown.
            </CardDescription>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading || disabled || !available}
            onClick={() => void handleLoad()}
            data-action="load-player-regions"
          >
            {loading ? <Spinner className="mr-1" /> : null}
            {summary ? "Refresh results" : "Load results"}
          </Button>
        </div>
        <details className="text-[11px] text-muted-foreground" data-player-region-scope>
          <summary className="cursor-pointer">Technical details: data source and revisions</summary>
          scope: {scopeLabel ?? policySummary(summary?.scope)}
          {summary?.synthesisRevision ? ` · synthesis ${summary.synthesisRevision}` : ""}
          {summary?.sourceRevision ? ` · source ${summary.sourceRevision}` : ""}
          {summary?.version ? ` · model ${summary.version}` : ""}
        </details>
      </CardHeader>
      <CardContent className="min-w-0 space-y-3">
        {!available ? (
          <Alert>
            <AlertTitle>Host build has no player-region read</AlertTitle>
            <AlertDescription>
              This host build does not expose `encounter_player_regions`, so no measured regions can be
              loaded. Nothing is guessed in its place.
            </AlertDescription>
          </Alert>
        ) : null}

        {available && invalidated && !summary ? (
          <Alert>
            <AlertTitle>Scope changed</AlertTitle>
            <AlertDescription>
              The encounter, library or revision changed since the last load, so the previous results
              were dropped rather than shown for the wrong encounter. Refresh for the current scope.
            </AlertDescription>
          </Alert>
        ) : null}

        {available && stale ? (
          <Alert>
            <AlertTitle>Summary out of scope</AlertTitle>
            <AlertDescription>
              These results were measured for a different scope. Refresh for the current scope.
            </AlertDescription>
          </Alert>
        ) : null}

        {available && error ? (
          <Alert variant="destructive">
            <AlertTitle>The player-region read did not answer</AlertTitle>
            <AlertDescription>
              <p className="font-mono text-[11px]">{error}</p>
            </AlertDescription>
          </Alert>
        ) : null}

        {available && !summary && !loading && !error && !invalidated ? (
          <div
            className="rounded-md border border-dashed p-3 text-sm text-muted-foreground"
            data-player-region-empty
          >
            No measured regions yet. Start search in the main view to measure candidates, then press
            Refresh results here. Results load when you open this guide; refresh to see new measurements.
          </div>
        ) : null}

        {available && summary && statusUnavailable ? (
          <Alert data-player-region-unavailable>
            <AlertTitle>No measured regions yet</AlertTitle>
            <AlertDescription>
              {summary.reason ?? "The host reported an unavailable player-region read with no reason."}
            </AlertDescription>
          </Alert>
        ) : null}

        {available && summary && !statusUnavailable ? (
          <>
            {coverage ? (
              <Alert variant={truncated ? "destructive" : "default"} data-player-region-coverage>
                <AlertTitle>{truncated ? "Read model truncated" : "Coverage"}</AlertTitle>
                <AlertDescription>
                  <span className="tabular-nums">
                    {formatInt(coverage.includedCandidates)} of {formatInt(coverage.availableCandidates)}{" "}
                    available candidate(s) represented
                    {coverage.candidateLimit != null
                      ? ` (limit ${formatInt(coverage.candidateLimit)})`
                      : ""}
                    .
                  </span>
                  {truncated
                    ? " This summary did not read every available candidate; the regions below are incomplete."
                    : ""}
                </AlertDescription>
              </Alert>
            ) : null}

            {summary.archetypes.length === 0 ? (
              <div
                className="rounded-md border border-dashed p-3 text-sm text-muted-foreground"
                data-player-region-empty
              >
                No measured regions yet. Start search in the main view to measure candidates, then press
                Refresh results here.
              </div>
            ) : null}

            {summary.limitations.length > 0 ? (
              <details className="text-[11px] text-muted-foreground" data-player-region-limitations>
                <summary className="cursor-pointer font-medium">How to interpret this evidence</summary>
                <ul className="list-disc space-y-0.5 pl-4">
                  {summary.limitations.map((entry, index) => (
                    <li key={index}>{entry}</li>
                  ))}
                </ul>
              </details>
            ) : null}

            <div className="rounded-lg border p-3" data-player-region-reward>
              <p className="mb-2 text-[11px] text-muted-foreground">
                Choose the average reward you want. Lower-reward builds remain available below; comparison
                with a reference build is shown separately.
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="min-w-0">
                  <Label className="text-[11px]">Target expected reward ({REWARD_UNIT}, optional)</Label>
                  <Input
                    className="h-8 w-32"
                    inputMode="numeric"
                    placeholder="blank"
                    value={targetDraft}
                    onChange={(event) => setTargetDraft(event.target.value)}
                    aria-label={`Target expected reward in ${REWARD_UNIT}; leave blank to list every measured option`}
                    data-player-region-target
                  />
                </div>
              </div>
              <div className="mt-3 min-w-0" data-player-region-target-slider>
                <Label className="text-[11px]">Choose your target</Label>
                {meanSpan ? (
                  <>
                    <Slider
                      className="mt-2 w-full sm:w-80"
                      min={meanSpan.min}
                      max={meanSpan.max}
                      step={1}
                      value={[rewardSliderPosition ?? meanSpan.min]}
                      onValueChange={(values) => {
                        const next = values[0];
                        if (typeof next === "number" && Number.isFinite(next)) {
                          setTargetDraft(String(Math.round(next)));
                        }
                      }}
                      aria-label={`Target expected reward in ${REWARD_UNIT}, bounded by the observed measured means`}
                      aria-valuetext={
                        target === null
                          ? `blank; observed measured means ${formatNumber(meanSpan.min)} to ${formatNumber(meanSpan.max)}`
                          : `${formatNumber(target)} ${REWARD_UNIT}${targetOutsideSpan ? " (outside the observed means)" : ""}`
                      }
                    />
                    <p className="mt-1 text-[11px] tabular-nums text-muted-foreground">
                      Bounds are only the measured means observed here: {formatNumber(meanSpan.min)}..
                      {formatNumber(meanSpan.max)} over {formatInt(meanSpan.count)} measured option(s) - an
                      observed extent, NOT a safe or guaranteed range. Clearing the box means "no target";
                      missing data stays unknown, never zero.
                      {targetOutsideSpan
                        ? " The typed target is outside the observed means; it stays allowed."
                        : ""}
                    </p>
                  </>
                ) : (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    No loaded candidate publishes a measured mean, so no reward-target slider exists. A
                    target can still be typed; missing data is unknown, never zero.
                  </p>
                )}
              </div>
              {filterInputInvalid(targetDraft) ? (
                <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
                  The target box holds text that is not a number, so it is ignored until it is cleared.
                </p>
              ) : null}
              <p className="mt-1 text-[11px] text-muted-foreground" data-player-region-reward-count>
                {reward.meets.length} measured option(s) meet
                {target != null ? ` your ${formatNumber(target)} ${REWARD_UNIT} target` : " this listing"}
                {reward.retainedBelowTarget > 0
                  ? ` · ${reward.retainedBelowTarget} below the target, kept below`
                  : ""}
                {reward.unknownMean > 0
                  ? ` · ${reward.unknownMean} with an unknown mean (not counted as below)`
                  : ""}
                .
              </p>
            </div>

            {categorizedByArchetype.size > 0 ? (
              <div className="space-y-3">
                {[...categorizedByArchetype.entries()].map(([archetypeId, entries]) => {
                  const archetype = archetypeById.get(archetypeId);
                  if (!archetype) return null;
                  return (
                    <StrategyArchetypeCard
                      key={archetypeId}
                      archetype={archetype}
                      options={entries}
                      target={target}
                      allFields={fields}
                      pins={pinsByArchetype[archetypeId] ?? {}}
                      onPinChange={(field, value) => setPin(archetypeId, field, value)}
                      onSelectCandidate={onSelectCandidate}
                    />
                  );
                })}
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground" data-player-region-reward-empty>
                No measured option to list. Lower-reward and unknown options are kept, never dropped.
              </p>
            )}

            <details className="rounded-lg border" data-player-region-stats-explorer>
              <summary className="cursor-pointer p-3 text-xs font-medium">
                Explore your stats (secondary)
              </summary>
              <div className="p-3 pt-0">
            {fields.length > 0 ? (
              <div className="rounded-lg border p-3" data-player-region-query>
                <div className="flex flex-wrap items-end gap-3">
                  <div className="min-w-0">
                    <Label className="text-[11px]">Stat field</Label>
                    <Select value={field} onValueChange={(value) => setField(value)}>
                      <SelectTrigger className="h-8 w-44">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {fields.map((option) => (
                          <SelectItem key={option} value={option}>
                            {statFieldLabel(option)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="min-w-0">
                    <Label className="text-[11px]">Exact value</Label>
                    <Input
                      className="h-8 w-28"
                      inputMode="numeric"
                      placeholder="blank"
                      value={exactDraft}
                      onChange={(event) => setExactDraft(event.target.value)}
                      data-player-region-exact
                    />
                  </div>
                  <div className="min-w-0">
                    <Label className="text-[11px]">Range low</Label>
                    <Input
                      className="h-8 w-24"
                      inputMode="numeric"
                      placeholder="blank"
                      value={lowDraft}
                      onChange={(event) => setLowDraft(event.target.value)}
                    />
                  </div>
                  <div className="min-w-0">
                    <Label className="text-[11px]">Range high</Label>
                    <Input
                      className="h-8 w-24"
                      inputMode="numeric"
                      placeholder="blank"
                      value={highDraft}
                      onChange={(event) => setHighDraft(event.target.value)}
                    />
                  </div>
                </div>
                <div className="mt-3 min-w-0" data-player-region-slider>
                  <Label className="text-[11px]">
                    Observed {statFieldLabel(field)} value (slider)
                  </Label>
                  {span ? (
                    <>
                      <Slider
                        className="mt-2 w-full sm:w-80"
                        min={span.min}
                        max={span.max}
                        step={1}
                        value={[sliderPosition ?? span.min]}
                        onValueChange={(values) => {
                          const next = values[0];
                          if (typeof next === "number" && Number.isFinite(next)) {
                            setExactDraft(String(Math.round(next)));
                          }
                        }}
                        aria-label={`Observed ${statFieldLabel(field)} value; the slider only moves the exact filter across measured points`}
                        aria-valuetext={
                          exact === null
                            ? `blank; observed span ${formatNumber(span.min)} to ${formatNumber(span.max)}`
                            : `${formatNumber(exact)}${exactOutsideSpan ? " (outside the observed span)" : ""}`
                        }
                      />
                      <p className="mt-1 text-[11px] tabular-nums text-muted-foreground">
                        observed span {formatNumber(span.min)}..{formatNumber(span.max)} over{" "}
                        {formatInt(span.count)} measured point(s) - this is an observed extent, NOT a safe
                        range. Untested integer positions resolve to explicit unknown; nothing is snapped to a
                        measured point and no reward is interpolated.
                        {exactOutsideSpan
                          ? " The typed exact value is outside this observed span; it stays allowed and is matched exactly."
                          : ""}
                      </p>
                    </>
                  ) : (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      No loaded point publishes a measured {statFieldLabel(field)} value, so no observed span
                      (and no slider) exists for this field.
                    </p>
                  )}
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Local filters over the loaded summary. An exact value only matches a measured point;
                  an unsampled value is unknown and the nearby measured options are listed - nothing is
                  interpolated or snapped, and higher is not assumed better. The reward target above only
                  highlights options; lower, failed and censored options are kept.
                </p>
                {queryInvalid ? (
                  <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
                    A filter box holds text that is not a number, so it is ignored until it is cleared.
                  </p>
                ) : null}
                <p className="mt-1 text-[11px] text-muted-foreground" data-player-region-result-count>
                  {result.mode === "no-field"
                    ? "no stat field available"
                    : `${result.matches.length} measured option(s) on ${statFieldLabel(field)}`}
                  {result.mode === "exact" ? " · exact value only" : ""}
                  {result.mode === "range" ? " · measured values inside the range" : ""}
                  {result.matchesWithUnknown > 0
                    ? ` · ${result.matchesWithUnknown} with unresolved samples`
                    : ""}
                  {target != null && result.retainedBelowTarget > 0
                    ? ` · ${result.retainedBelowTarget} below the target, retained`
                    : ""}
                </p>
              </div>
            ) : (
              <Alert>
                <AlertTitle>No measured stat fields</AlertTitle>
                <AlertDescription>
                  No loaded point publishes any effective stat value, so no knob query can be built.
                </AlertDescription>
              </Alert>
            )}

            {result.unknown ? (
              <Alert data-player-region-unknown>
                <AlertTitle>Unknown</AlertTitle>
                <AlertDescription>
                  <p>{result.unknownReason}</p>
                  {result.nearbyMeasured.length > 0 ? (
                    <p className="mt-1 tabular-nums">
                      Known measured values on {statFieldLabel(field)}:{" "}
                      {result.nearbyMeasured.map((value) => formatNumber(value)).join(", ")} - these are
                      measured points, not an interpolated range.
                    </p>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}

            {grouped.size > 0 ? (
              <div className="space-y-3">
                {[...grouped.entries()].map(([archetypeId, entry]) => {
                  const archetype = archetypeById.get(archetypeId);
                  if (!archetype) return null;
                  return (
                    <ArchetypeBlock
                      key={archetypeId}
                      archetype={archetype}
                      options={entry.matches}
                      sparse={entry.sparse}
                      field={field}
                      target={target}
                      allFields={fields}
                      headingFor={(option) => `${statFieldLabel(field)} = ${formatNumber(option.value)}`}
                      onSelectCandidate={onSelectCandidate}
                    />
                  );
                })}
              </div>
            ) : null}
              </div>
            </details>

            <p className="text-[11px] text-muted-foreground" data-player-region-counterfactual>
              Counterfactual predictions (what an unmeasured build would earn) are not available: no
              measured value exists for them, so none is shown.
            </p>

            <details data-player-region-export>
              <summary className="cursor-pointer text-[11px] text-muted-foreground">
                Export read model (JSON)
              </summary>
              <pre className="mt-1 max-h-80 overflow-auto rounded-md border p-2 text-[10px]">
                {exportPlayerRegionsReadModel(summary)}
              </pre>
            </details>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
