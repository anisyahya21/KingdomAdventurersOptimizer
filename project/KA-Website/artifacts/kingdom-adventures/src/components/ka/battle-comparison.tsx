/**
 * Shared presentation for a `ka-battle-eval-1` comparison result.
 *
 * Every number shown here comes from the backend envelope; the component adds no statistics of its
 * own. Rankings are per level first and pooled second, unranked candidates always state why, and
 * retained inventory is never rendered as a number.
 */
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  candidateResult,
  formatHistogram,
  formatMean,
  formatProbability,
  type BattleEvaluationResult,
  type BattleSearchResult,
  type ChestBlock,
  type ComparisonCandidateResult,
  type ComparisonLevelResult,
  type UnrankedEntry,
} from "@/lib/battle-comparison";

function GeneratedVariants({ generated }: { generated: NonNullable<BattleSearchResult["enumeration"]["generated"]> }) {
  const candidates = Object.entries(generated.perCandidate);
  return (
    <div className="space-y-2" data-generated-variants>
      <p className="text-sm font-medium">Generated legal variants (bounded, from each candidate's own units)</p>
      <p className="text-xs text-muted-foreground">{generated.rule}</p>
      <p className="text-xs text-muted-foreground">
        {generated.generatedVariants} generated from {generated.availableVariants} legal variant(s) before the
        per-candidate cap; no variant adds a skill, an equipment row, a stat, a level or a party member.
      </p>
      {candidates.map(([candidateId, report]) => (
        <div key={candidateId} className="rounded border border-border/60 p-2 text-xs">
          <p className="font-medium">
            <span className="font-mono">{candidateId}</span>{" "}
            <Badge variant="outline">
              {report.generated} of {report.available}
              {report.truncated ? ` (capped at ${report.cap})` : ""}
            </Badge>
          </p>
          <p className="text-muted-foreground">
            formation: {report.formation.enabled ? `${report.formation.arrangements} distinct order(s)` : "off"}
            {report.formation.enabled && report.formation.classes.length > 0
              ? ` · tie groups ${report.formation.classes
                  .map((group) => `[${group.members.join(", ")}] p${group.priority}/d${group.effectiveDefense}`)
                  .join(" ")}`
              : ""}
            {report.formation.reason ? ` · ${report.formation.reason}` : ""}
            {" | "}
            skill priorities:{" "}
            {report.skillPriorities.enabled ? `${report.skillPriorities.arrangements} distinct order(s)` : "off"}
            {report.skillPriorities.units.length > 0
              ? ` · ${report.skillPriorities.units
                  .map((unit) => `${unit.name} ${unit.skills.join(">")}`)
                  .join(", ")}`
              : ""}
            {report.skillPriorities.reason ? ` · ${report.skillPriorities.reason}` : ""}
          </p>
          <ul className="mt-1 space-y-1">
            {report.variants.map((variant) => (
              <li key={variant.id} className="text-muted-foreground">
                <span className="font-mono">{variant.id}</span>{" "}
                <Badge variant="outline">{variant.kind}</Badge> {variant.description}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function IssueList({ title, entries }: { title: string; entries: UnrankedEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="space-y-1 text-xs">
        {entries.map((entry) => (
          <li key={entry.id} className="rounded border border-border/60 p-2">
            <span className="font-mono">{entry.id}</span>{" "}
            <Badge variant="outline" className="ml-1">
              unranked
            </Badge>
            <ul className="mt-1 list-disc pl-4 text-muted-foreground">
              {entry.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
              {entry.errors.map((error) => (
                <li key={error} className="text-destructive">
                  {error}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChestCell({ block }: { block: ChestBlock }) {
  return (
    <div className="text-xs">
      <div>
        mean <span className="font-mono">{formatMean(block.statistics.mean)}</span> over{" "}
        {block.statistics.samples} resolved run(s)
      </div>
      <div className="text-muted-foreground">histogram {formatHistogram(block.histogram)}</div>
      {block.statistics.descriptiveRange ? (
        <div className="text-muted-foreground">
          descriptive range {block.statistics.descriptiveRange[0]}..{block.statistics.descriptiveRange[1]}
        </div>
      ) : null}
      <div className="text-muted-foreground">{block.basis}</div>
    </div>
  );
}

function LevelTable({
  level,
  result,
}: {
  level: ComparisonLevelResult;
  result: BattleEvaluationResult;
}) {
  const ranked = result.rankings.perLevel.find(
    (entry) =>
      entry.level.encounterId === level.level.encounterId &&
      entry.level.defeatCount === level.level.defeatCount,
  );
  const rankById = new Map((ranked?.ranked ?? []).map((entry) => [entry.id, entry.rank]));
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">
          Encounter {level.level.encounterId}, defeat {level.level.defeatCount}
        </span>
        <Badge variant="secondary">level {level.level.level ?? "unknown"}</Badge>
        <span className="text-xs text-muted-foreground">
          {level.resolvedRuns}/{level.runs} resolved · {level.errors.length} error(s) · {level.censored} censored
        </span>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Candidate</TableHead>
            <TableHead>Rank</TableHead>
            <TableHead>Won</TableHead>
            <TableHead>Lost</TableHead>
            <TableHead>Censored</TableHead>
            <TableHead>Dispatched chests / attempt</TableHead>
            <TableHead>Queued callbacks</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {result.candidates.map((candidate) => {
            const candidateLevel = candidate.levels.find(
              (entry) =>
                entry.level.encounterId === level.level.encounterId &&
                entry.level.defeatCount === level.level.defeatCount,
            );
            if (!candidateLevel) return null;
            const rank = rankById.get(candidate.id);
            return (
              <TableRow key={candidate.id}>
                <TableCell className="font-mono text-xs">{candidate.id}</TableCell>
                <TableCell>{rank ?? <span className="text-muted-foreground">unranked</span>}</TableCell>
                <TableCell>
                  {formatProbability(candidateLevel.winProbability)}
                  <span className="ml-1 text-xs text-muted-foreground">({candidateLevel.wins})</span>
                </TableCell>
                <TableCell>
                  {formatProbability(candidateLevel.lossProbability)}
                  <span className="ml-1 text-xs text-muted-foreground">({candidateLevel.losses})</span>
                </TableCell>
                <TableCell>{formatProbability(candidateLevel.censoredProbability)}</TableCell>
                <TableCell>
                  <ChestCell block={candidateLevel.dispatchedChests} />
                </TableCell>
                <TableCell>
                  <ChestCell block={candidateLevel.queuedChestCallbacks} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <IssueList title="Unranked at this level" entries={ranked?.unranked ?? []} />
      <p className="text-xs text-muted-foreground">{level.probabilities.label}</p>
      {level.censored > 0 ? (
        <p className="text-xs text-amber-600 dark:text-amber-400">{level.censoring.note}</p>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {level.retainedInventory.state} · counted: {String(level.retainedInventory.counted)}
      </p>
    </div>
  );
}

function PooledTable({ result }: { result: BattleEvaluationResult }) {
  const rankById = new Map(result.rankings.pooled.ranked.map((entry) => [entry.id, entry.rank]));
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Candidate</TableHead>
          <TableHead>Pooled rank</TableHead>
          <TableHead>Expected dispatched / attempt</TableHead>
          <TableHead>Won / lost / censored</TableHead>
          <TableHead>Dispatched histogram</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {result.candidates.map((candidate: ComparisonCandidateResult) => (
          <TableRow key={candidate.id}>
            <TableCell className="font-mono text-xs">{candidate.id}</TableCell>
            <TableCell>
              {rankById.get(candidate.id) ?? <span className="text-muted-foreground">unranked</span>}
            </TableCell>
            <TableCell className="font-mono text-xs">
              {formatMean(candidate.pooled.dispatchedChests.statistics.mean)}
            </TableCell>
            <TableCell className="text-xs">
              {candidate.pooled.wins} / {candidate.pooled.losses} / {candidate.pooled.censored}
            </TableCell>
            <TableCell className="text-xs">
              {formatHistogram(candidate.pooled.dispatchedChests.histogram)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function BattleComparisonResult({
  result,
  onOpenWinner,
}: {
  result: BattleEvaluationResult;
  onOpenWinner?: () => void;
}) {
  const selectedId = result.selected?.candidateId ?? null;
  // A search result ranks combination ids (`candidate::variant`) and reports the base candidate id.
  const winner = selectedId === null
    ? null
    : candidateResult(result, selectedId)
      ?? result.candidates.find((entry) => entry.id.startsWith(`${selectedId}::`))
      ?? null;
  const generated = (result as BattleSearchResult).enumeration?.generated;
  const selectedVariantId = (result as BattleSearchResult).selected?.variantId;
  return (
    <Card data-battle-comparison-result>
      <CardHeader>
        <CardTitle>Comparison result</CardTitle>
        <CardDescription>
          {result.support?.claim ?? result.rankings.metric} · {result.support?.mode ?? "research"} mode.
          {" "}Exact native replay support: {String(result.support?.exactReplaySupported ?? false)};
          gear recommendations: {String(result.support?.recommendationsSupported ?? false)}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded border border-border/60 p-2">
            <p className="font-medium">Ranking objective</p>
            <p className="text-muted-foreground">{result.rankings.metric}</p>
            <p className="text-muted-foreground">{result.rankings.metricNote}</p>
          </div>
          <div className="rounded border border-border/60 p-2">
            <p className="font-medium">Reproducibility inputs</p>
            <p className="text-muted-foreground">
              tickLimit {result.request.tickLimit} · {result.budget.plannedRuns} planned runs ·{" "}
              {result.request.seeds.length} seed pair(s) · groups: {result.comparability.rule}
            </p>
            <p className="break-all font-mono">
              seeds {JSON.stringify(result.request.seeds)}
            </p>
            <p className="text-muted-foreground">
              levels{" "}
              {result.request.levels
                .map((level) => `(${level.encounterId},${level.defeatCount})`)
                .join(" ")}
            </p>
          </div>
        </div>

        {result.selected === null ? (
          <p className="text-sm text-destructive">
            No candidate produced complete, uncensored runs, so nothing is ranked and no winner replay
            exists. Lower the tick limit pressure, add samples, or fix the errors below.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge>winner {result.selected.candidateId}</Badge>
            {selectedVariantId ? <Badge variant="outline">variant {selectedVariantId}</Badge> : null}
            <span className="text-muted-foreground">
              level {result.selected.level.level ?? "?"} · seed {JSON.stringify(result.selected.seed)} ·{" "}
              {result.selected.replaySelection}
            </span>
            {onOpenWinner ? (
              <Button size="sm" onClick={onOpenWinner} data-testid="open-comparison-winner">
                Open winning replay
              </Button>
            ) : null}
          </div>
        )}

        {generated ? <GeneratedVariants generated={generated} /> : null}

        <div className="space-y-4">
          {(result.candidates[0]?.levels ?? []).map((level) => (
            <LevelTable key={`${level.level.encounterId}-${level.level.defeatCount}`} level={level} result={result} />
          ))}
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">Pooled ranking (secondary)</p>
          <PooledTable result={result} />
          <IssueList title="Unranked (pooled)" entries={result.rankings.pooled.unranked} />
        </div>

        {winner && winner.errors.length > 0 ? (
          <div className="space-y-1">
            <p className="text-sm font-medium">Winner candidate run errors</p>
            <ul className="list-disc pl-5 text-xs text-destructive">
              {winner.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="space-y-2">
          <p className="text-sm font-medium">Strategy mechanism (evidence from runner event records)</p>
          {result.strategies.map((strategy) => (
            <div key={strategy.id} className="rounded border border-border/60 p-2 text-xs">
              <p className="font-medium">
                {strategy.title} <Badge variant="outline">{strategy.status}</Badge>
              </p>
              <p className="text-muted-foreground">{strategy.mechanism}</p>
              <p className="text-muted-foreground">Evidence rule: {strategy.evidenceRule}</p>
              <p className="text-muted-foreground">
                Observed leaving entries / prize event records per level are listed above from the run
                event records; incomplete inputs: {strategy.incompleteInputs.join("; ")}
              </p>
            </div>
          ))}
        </div>

        <details className="text-xs">
          <summary className="cursor-pointer font-medium">Caveats ({result.caveats.length})</summary>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
            {result.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </details>
      </CardContent>
    </Card>
  );
}
