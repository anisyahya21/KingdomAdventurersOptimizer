import { Fragment, useMemo, useState, type ComponentType } from "react";
import { Link } from "wouter";
import {
  ChevronDown, Coins, Gift, Hammer, Info, PackageSearch, Repeat, Search, Sprout, Store, UtensilsCrossed, Wand2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ka/page-header";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { getEquipmentIcon, getFacilityIconByName, getFurnitureIcon, getItemIcon } from "@/lib/equipment-icons";
import { getMonsterSprite } from "@/lib/monster-sprites";
import { getSkillIcon } from "@/lib/skill-icons";
import { TREASURE_MONSTERS, treasureDisplayName } from "@/lib/treasure-lookup";
import {
  GRADE_LABELS_PLAIN,
  GRADE_NOTES_PLAIN,
  GRADE_ORDER,
  PROJECTION_STATS,
  activeLanes,
  buildItemDirectRows,
  buildItemSourceRows,
  dailyRankLabel,
  edgeGrade,
  formatPercent,
  formatQuantity,
  gradeSentence,
  gradeTone,
  groupProfileSources,
  rewardKey,
  safeRewardLabel,
  searchProfiles,
  searchRewardTargets,
  sourceNode,
  unresolvedProfilesForReward,
  type DisplaySourceGroup,
  type EvidenceGrade,
  type ItemSourceRow,
  type Lane,
  type Profile,
  type RewardType,
} from "@/lib/treasure-source-graph";

/* ------------------------------------------------------------------ small pieces */

/** Family icons. A cave row is one family ("Normal Cave"), so one shared appearance is honest. */
const SOURCE_IMAGES: Record<string, string> = {
  "legendary-cave": "/website_icons/facilities_confirmed/facility_196_legendary_cave.png",
  "legendary-cave-end-chest": "/website_icons/facilities_confirmed/facility_196_legendary_cave.png",
  "normal-cave": "/website_icons/facilities_confirmed/mapchip_071_cave.png",
  "normal-cave-end-chest": "/website_icons/facilities_confirmed/mapchip_071_cave.png",
  "daily-rank": "/website_icons/facilities_confirmed/facility_172_ranking_board.png",
};
const MONSTERS_BY_ID = new Map(TREASURE_MONSTERS.map((monster) => [monster.id, monster]));

/** Job rewards are rank jobs ("F Rank Scholar") → the rank token and the job name. */
const jobRankToken = (name: string) => /^\s*([FEDCBAS])\s/.exec(name)?.[1];
const jobNameToken = (name: string) => /^\s*[FEDCBAS]\s+(?:Rank|Grade)\s+(.+)$/.exec(name)?.[1];

function RewardIcon({ type, name, className = "h-8 w-8" }: { type: RewardType; name: string; className?: string }) {
  // Job rewards use the same character renderer as the Wairo Dungeon loot table, so the two pages
  // cannot drift: the rank stays in the text, the sprite is the job's character.
  if (type === "job") {
    const rank = jobRankToken(name);
    const job = jobNameToken(name);
    if (job) {
      const height = className.match(/h-\d+(?:\.\d+)?/)?.[0] ?? "h-8";
      return (
        <CharacterPreviewCanvas
          jobName={job}
          rank={rank}
          variant={1}
          equipState="right"
          scale={2}
          poseFrame={0}
          label={name}
          className={`${height} w-auto shrink-0`}
        />
      );
    }
  }
  const src =
    type === "skill" ? getSkillIcon(name)
      : type === "item" ? getItemIcon(name)
        : type === "equipment" ? getEquipmentIcon(undefined, name)
          : type === "furniture" ? getFurnitureIcon(name)
            : undefined;
  if (!src) return <PackageSearch className={`${className} shrink-0 text-muted-foreground`} />;
  return <img src={src} alt={treasureDisplayName(name)} className={`${className} shrink-0 object-contain [image-rendering:pixelated]`} />;
}

function SourceGlyph({ sourceId, large = false }: { sourceId: string; large?: boolean }) {
  const node = sourceNode(sourceId);
  const monsterId = node?.key.startsWith("monster:") ? Number(node.key.slice("monster:".length)) : undefined;
  const monster = monsterId !== undefined ? MONSTERS_BY_ID.get(monsterId) : undefined;
  const image = (node && SOURCE_IMAGES[node.kind]) ?? getMonsterSprite(monster?.name)?.src;
  // Neutral placeholder rather than an empty cell when a family has no shipped sprite.
  if (!image) {
    return (
      <span className={`${large ? "h-14 w-14" : "h-8 w-8"} flex shrink-0 items-center justify-center rounded border bg-muted/40`}>
        <PackageSearch className="h-4 w-4 text-muted-foreground" />
      </span>
    );
  }
  return (
    <img
      src={image}
      alt=""
      className={`${large ? "h-14 w-14" : "h-8 w-8"} shrink-0 object-contain [image-rendering:pixelated]`}
    />
  );
}

/** Non-treasure routes get a mechanism icon instead of a chest sprite. */
const MECHANISM_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  "item-flag-shop": Store,
  "item-flag-restaurant": UtensilsCrossed,
  "craft-group-orchard": Sprout,
  "craft-group-facility": Hammer,
  "skill-crafting": Wand2,
  "gacha-pool": Gift,
  "equipment-exchange": Repeat,
};

function MechanismGlyph({ mechanism }: { mechanism: string }) {
  const Icon = MECHANISM_ICONS[mechanism] ?? Coins;
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border bg-muted/40">
      <Icon className="h-4 w-4 text-muted-foreground" />
    </span>
  );
}

/**
 * Source cell artwork. For a direct route the subject is the facility or shop itself, and the
 * site already ships those sprites; otherwise the cave / rank / monster family image is used.
 */
function SourceCellGlyph({ row }: { row: ItemSourceRow }) {
  const direct = row.mechanism === "craft-group-facility" ? getFurnitureIcon(row.family)
    : row.mechanism === "material-shop" ? getFacilityIconByName(row.family)
      : row.mechanism === "item-flag-shop" ? getFacilityIconByName("Item Shop")
        : undefined;
  if (direct) return <img src={direct} alt="" className="h-8 w-8 shrink-0 object-contain [image-rendering:pixelated]" />;
  return <SourceGlyph sourceId={row.sourceId} />;
}

function ProbabilityStages() {
  return (
    <details className="rounded-lg border p-4 text-sm">
      <summary className="flex cursor-pointer items-center gap-2 font-medium">
        <Info className="h-4 w-4" /> How chances work
      </summary>
      <div className="mt-3 space-y-2 text-muted-foreground">
        <p>
          Overall chance = the chance of getting the treasure from that source × the chance of the item
          inside it. Sources that hand the item over directly stay at 100%.
        </p>
        <p><strong className="text-foreground">Treasure rarity</strong> — the chance that this source gives you that treasure.</p>
        <p><strong className="text-foreground">In treasure</strong> — the chance of the item inside that treasure. On its own it is not a per-dig, per-monster or per-floor chance.</p>
        <p><strong className="text-foreground">Overall</strong> — the two multiplied, always labelled with what it is per (“per floor”, “per chest”, “per defeat”). “Unknown” means the two stages cannot be multiplied yet.</p>
        <p className="pt-1 text-xs">
          Every row also says how it was verified. “Verified from the game code” comes from the original game data;
          “From a site-maintained list” has not been checked against the game.
        </p>
      </div>
    </details>
  );
}

/* --------------------------------------------------------------- source table */

const rowMatchesLevel = (row: ItemSourceRow, level: number | null) => {
  if (level === null || row.direct) return true;
  return row.edges.some((edge) => {
    const conditions = { ...(sourceNode(edge.source)?.conditions ?? {}), ...(edge.conditions ?? {}) };
    const min = Number(conditions.minLevel ?? conditions.treasureMinLevel);
    const max = Number(conditions.maxLevel ?? conditions.treasureMaxLevel);
    return !(Number.isFinite(min) && level < min) && !(Number.isFinite(max) && level > max);
  });
};

const groupMatchesLevel = (group: DisplaySourceGroup, level: number | null) => {
  if (level === null) return true;
  return group.routes.some((route) => {
    const conditions = { ...(sourceNode(route.source)?.conditions ?? {}), ...(route.conditions ?? {}) };
    const min = Number(conditions.minLevel ?? conditions.treasureMinLevel);
    const max = Number(conditions.maxLevel ?? conditions.treasureMaxLevel);
    return !(Number.isFinite(min) && level < min) && !(Number.isFinite(max) && level > max);
  });
};

/** One plain sentence per non-treasure route; no implementation detail reaches the page. */
const DIRECT_COPY: Record<string, string> = {
  "material-shop": "Bought directly from the shop.",
  "craft-group-facility": "Crafted at this facility.",
  "craft-group-orchard": "Produced by the orchard.",
  "skill-crafting": "Crafted at the skill shop.",
  "gacha-pool": "Drawn from this gacha pool; the in-game odds list shows the rate.",
  "equipment-exchange": "Traded at the equipment exchange.",
  "item-flag-shop": "Sold in the item shop.",
  "item-flag-restaurant": "Sold at the restaurant.",
};

function RowDetails({ row }: { row: ItemSourceRow }) {
  const price = /^Price (.+)$/.exec(row.requirement)?.[1];
  const limit = Number(row.conditions?.purchaseLimit);
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      <p className="text-foreground">{gradeSentence(row.grade)}</p>
      <ul className="space-y-0.5">
        <li>Treasure chance from this source: <span className="tabular-nums text-foreground">{row.treasureRate.text}</span></li>
        <li>Item chance inside the treasure: <span className="tabular-nums text-foreground">{row.itemRate.text}</span></li>
        <li>Overall chance: <span className="tabular-nums text-foreground">{row.overall.text}{row.overall.detail && ` ${row.overall.detail}`}</span></li>
      </ul>
      {row.note && <p>{row.note}.</p>}
      {row.direct ? (
        <>
          <p>{DIRECT_COPY[row.mechanism] ?? "Documented route."}</p>
          {price && <p>Recorded price range: {price}.</p>}
          {Number.isFinite(limit) && <p>Purchase limit: {limit}.</p>}
        </>
      ) : (
        <p>
          Treasure rows:{" "}
          <span className="font-mono text-foreground">{row.variantIds.map((id) => `#${id}`).join(", ")}</span>
        </p>
      )}
    </div>
  );
}

function ItemSourceTable({ rows, level, itemLabel }: { rows: ItemSourceRow[]; level: number | null; itemLabel: string }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setOpen((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  const visible = rows.filter((row) => rowMatchesLevel(row, level));
  const inTreasureHeader = itemLabel.length <= 16 ? `${itemLabel} chance in treasure` : "In treasure";
  return (
    <Card className="overflow-hidden" data-testid="item-source-table">
      <div className="flex items-baseline justify-between gap-3 px-5 pt-5">
        <h3 className="text-base font-semibold">Sources <span className="font-normal text-muted-foreground">{visible.length}</span></h3>
        <p className="text-xs text-muted-foreground">One row per player-facing route</p>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[56rem] border-collapse text-sm">
          <thead>
            <tr className="border-y bg-muted/30 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <th className="px-5 py-3 font-semibold">Source</th>
              <th className="px-5 py-3 font-semibold">Treasure / mechanism</th>
              <th className="px-5 py-3 font-semibold">Requirement</th>
              <th className="px-5 py-3 text-right font-semibold">Qty</th>
              <th className="px-5 py-3 text-right font-semibold">Treasure rarity</th>
              <th className="px-5 py-3 text-right font-semibold">{inTreasureHeader}</th>
              <th className="px-5 py-3 text-right font-semibold">Overall chance</th>
              <th className="w-10 px-3 py-3" />
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const isOpen = open.has(row.key);
              return (
                <Fragment key={row.key}>
                  <tr
                    className={`border-b align-middle transition-colors ${isOpen ? "bg-muted/30" : "hover:bg-muted/30"}`}
                    data-testid="source-row"
                    data-source={row.family}
                  >
                    <td className="px-5 py-3">
                      <span className="flex items-center gap-3">
                        <SourceCellGlyph row={row} />
                        <span className="font-medium">{row.family}</span>
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <span className="flex items-start gap-3">
                        {row.direct
                          ? <MechanismGlyph mechanism={row.mechanism} />
                          : <img src={row.treasureIcon} alt="" className="h-8 w-8 shrink-0 object-contain [image-rendering:pixelated]" />}
                        <span className="min-w-0">
                          <span className="block">{treasureDisplayName(row.treasureName)}</span>
                          {row.treasureNote && <span className="block text-xs text-muted-foreground">{row.treasureNote}</span>}
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-3">{row.requirement || <span className="text-muted-foreground">—</span>}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-right tabular-nums">{row.quantity}</td>
                    <td className="px-5 py-3 text-right">
                      <span className={`block tabular-nums ${row.treasureRate.established ? "font-medium text-emerald-700 dark:text-emerald-300" : "text-muted-foreground"}`}>
                        {row.treasureRate.text}
                      </span>
                      {row.treasureRate.detail && <span className="block text-[11px] text-muted-foreground">{row.treasureRate.detail}</span>}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <span className={`block tabular-nums ${row.itemRate.established ? "font-medium text-emerald-700 dark:text-emerald-300" : "text-muted-foreground"}`}>
                        {row.itemRate.text}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <span className={`block tabular-nums ${row.overall.established ? "font-semibold text-emerald-700 dark:text-emerald-300" : "text-muted-foreground"}`}>
                        {row.overall.text}
                      </span>
                      {row.overall.detail && <span className="block text-[11px] text-muted-foreground">{row.overall.detail}</span>}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => toggle(row.key)}
                        aria-expanded={isOpen}
                        aria-label={`Evidence for ${row.family} ${row.treasureName}`}
                        className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                      >
                        <ChevronDown className={`h-4 w-4 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b bg-muted/20">
                      <td colSpan={8} className="px-5 py-4"><RowDetails row={row} /></td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {!visible.length && (
              <tr>
                <td colSpan={8} className="px-5 py-8 text-center text-sm text-muted-foreground">No source matches these filters.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------ treasure mode */

const FIELD_NOTES: Record<string, string> = {
  name: "display text only — no game behaviour is keyed on it",
  hp: "analysis/opening time only — it never changes the rewards",
  group: "selects the box out of a terrain or encounter reward group",
  minLevel: "acquisition selector (level bounds)",
  maxLevel: "acquisition selector (level bounds)",
  rank: "acquisition eligibility only (daily rank, arena)",
  week: "acquisition eligibility only (daily rank)",
  flag: "acquisition eligibility only",
};

function VariantEvidence({ profile, groups }: { profile: Profile; groups: DisplaySourceGroup[] }) {
  const [showAll, setShowAll] = useState(false);
  const variants = showAll ? profile.variants : profile.variants.slice(0, 8);
  const labelByKey = new Map<string, string>();
  for (const group of groups) for (const key of group.edges.map((edge) => edge.key)) labelByKey.set(key, group.label);
  return (
    <details className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer">Treasure rows in this box</summary>
      <p className="mt-2 text-xs text-muted-foreground">
        {profile.memberIds.length === 1
          ? <>One row in the original game table: <span className="font-mono">#{profile.memberIds[0]}</span>.</>
          : <>{profile.memberIds.length} rows in the original game table give this box the same contents, so it is shown once. Native names: {profile.nativeNames.map(treasureDisplayName).join(", ")}.</>}
      </p>
      <ul className="mt-2 space-y-0.5 text-xs">
        {variants.map((variant) => {
          const sources = [...new Set(variant.keys.map((key) => labelByKey.get(key)).filter(Boolean))];
          return (
            <li key={variant.id}>
              <span className="font-mono">#{variant.id}</span> · {treasureDisplayName(variant.name)}
              {sources.length > 0 && <span className="text-muted-foreground"> · {sources.join(", ")}</span>}
            </li>
          );
        })}
      </ul>
      {profile.variants.length > variants.length && (
        <Button variant="outline" size="sm" className="mt-2" onClick={() => setShowAll(true)}>
          Show all {profile.variants.length} rows
        </Button>
      )}
    </details>
  );
}

function SourceGroupRow({ group, lane }: { group: DisplaySourceGroup; lane?: Lane }) {
  const treasure = group.probability;
  const fraction = treasure && "fraction" in treasure ? treasure.fraction : undefined;
  const formula = treasure && "kind" in treasure ? treasure.formula : undefined;
  const treasureDecimal = treasure && "decimal" in treasure ? treasure.decimal : undefined;
  const overallRows = lane ? group.derived.filter((row) => row.lane === lane.lane) : [];
  let overall: { text: string } | undefined;
  if (overallRows.length) {
    const values = overallRows.map((row) => row.p.decimal);
    const min = Math.min(...values);
    const max = Math.max(...values);
    overall = { text: min === max ? formatPercent(min) : `${formatPercent(min)}–${formatPercent(max)}` };
  }
  const rank = dailyRankLabel(group.routes[0]?.conditions?.sourceRowId);
  const produced = [...new Set(group.variants.flatMap((variant) => variant.treasureIds))].sort((a, b) => a - b);
  const treasureText = treasureDecimal !== undefined ? formatPercent(treasureDecimal) : "Not a fixed number";
  return (
    <li className="overflow-hidden rounded-md border">
      <div className="flex items-center gap-3 px-2.5 py-2">
        <SourceGlyph sourceId={group.sourceId} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{group.label}</p>
          {(group.sublabel.length > 0 || rank) && (
            <p className="text-xs leading-tight text-muted-foreground">
              {rank && `${rank.day} · Rank ${rank.rankLabel}`}
              {rank && group.sublabel.length > 0 && " · "}
              {group.sublabel.join(" · ")}
            </p>
          )}
        </div>
        <div className="shrink-0 text-right leading-tight">
          {overall ? (
            <>
              <p className="text-sm font-semibold tabular-nums">
                {overall.text} <span className="text-xs font-normal text-muted-foreground">for {treasureDisplayName(lane!.name)}</span>
              </p>
              <p className="text-xs tabular-nums text-muted-foreground">{treasureText} to get this treasure</p>
            </>
          ) : (
            <>
              <p className="text-sm font-semibold tabular-nums">{treasureText}</p>
              <p className="text-xs text-muted-foreground">to get this treasure</p>
            </>
          )}
        </div>
      </div>
      <details className="border-t bg-muted/20 px-2.5 py-1">
        <summary className="cursor-pointer text-xs text-muted-foreground">Chance details</summary>
        <div className="space-y-1 py-2 text-xs text-muted-foreground">
          <p className="text-foreground">{gradeSentence(group.grade)}</p>
          <p>
            Treasure chance from this source: <span className="tabular-nums text-foreground">{treasureText}</span>
          </p>
          {overall && (
            <p>
              Item chance from this source: <span className="tabular-nums text-foreground">{overall.text}</span>
            </p>
          )}
          <p>
            Treasure rows:{" "}
            <span className="font-mono text-foreground">{produced.map((id) => `#${id}`).join(", ") || "—"}</span>
          </p>
        </div>
      </details>
    </li>
  );
}

const MAX_VISIBLE_GROUPS = 6;

function ProfileCard({ profile, lane, level, kindFilter }: {
  profile: Profile;
  lane?: Lane;
  level: number | null;
  kindFilter: string;
}) {
  const allGroups = useMemo(() => groupProfileSources(profile), [profile]);
  const visible = allGroups.filter(
    (group) => (kindFilter === "All" || group.kind === kindFilter) && groupMatchesLevel(group, level),
  );
  const shown = visible.slice(0, MAX_VISIBLE_GROUPS);
  const collapsed = visible.slice(MAX_VISIBLE_GROUPS);
  const lanes = activeLanes(profile);
  const targetKey = lane ? rewardKey(lane.type, lane.name) : undefined;
  const unresolved = profile.edges.length === 0;
  const summary = unresolved
    ? "Contents are recorded; no way to obtain it has been established."
    : `${allGroups.length} known way${allGroups.length === 1 ? "" : "s"} to obtain this treasure.`;
  return (
    <Card className="overflow-hidden" data-testid="treasure-profile" data-profile-id={profile.id}>
      <CardHeader className="flex-row items-center gap-3 space-y-0 pb-3">
        <img
          src={profile.icon}
          alt={treasureDisplayName(profile.name)}
          className="h-16 w-16 shrink-0 object-contain [image-rendering:pixelated]"
        />
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base">{treasureDisplayName(profile.name)}</CardTitle>
          <p className="mt-0.5 text-xs text-muted-foreground">{summary}</p>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <section>
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Contents</h3>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Chance in this treasure</h3>
          </div>
          <ul className="space-y-0.5">
            {lanes.map((entry) => {
              const isTarget = targetKey !== undefined && rewardKey(entry.type, entry.name) === targetKey;
              return (
                <li
                  key={entry.lane}
                  className={`flex items-center justify-between gap-3 rounded-md px-2 py-1.5 ${isTarget ? "bg-emerald-500/10 ring-1 ring-emerald-500/40" : "bg-muted/40"}`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <RewardIcon type={entry.type} name={entry.name} className="h-7 w-7" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm">
                        {treasureDisplayName(entry.name) || `reward #${entry.id}`}
                        {isTarget && <span className="ml-1.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">your item</span>}
                      </span>
                      <span className="block text-xs text-muted-foreground">Receive {formatQuantity(entry.min, entry.max)} when rolled</span>
                    </span>
                  </span>
                  <span className="shrink-0 text-sm font-medium tabular-nums">{formatPercent(entry.rate / 100, 3)}</span>
                </li>
              );
            })}
          </ul>
        </section>

        <section className="space-y-1.5 border-t pt-3">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-semibold">Where to get it</h3>
            {!unresolved && <span className="text-xs text-muted-foreground">{visible.length === allGroups.length ? `${allGroups.length} way${allGroups.length === 1 ? "" : "s"}` : `showing ${visible.length} of ${allGroups.length} ways`}</span>}
          </div>
          {unresolved ? (
            <p className="rounded-md bg-muted/40 p-2 text-sm text-muted-foreground">
              The contents are in the original table, but no acquisition source has been established
              {profile.unresolvedIds.length > 1 ? " for these rows" : " for this row"}, so no chance is shown.
            </p>
          ) : visible.length ? (
            <>
              <ul className="space-y-1.5">
                {shown.map((group) => <SourceGroupRow key={group.key} group={group} lane={lane} />)}
              </ul>
              {collapsed.length > 0 && (
                <details className="rounded-md border px-2.5 py-1.5">
                  <summary className="cursor-pointer text-xs text-muted-foreground">Show {collapsed.length} more way{collapsed.length === 1 ? "" : "s"} to obtain it</summary>
                  <ul className="mt-1.5 space-y-1.5">
                    {collapsed.map((group) => <SourceGroupRow key={group.key} group={group} lane={lane} />)}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No source matches these filters.</p>
          )}
        </section>

        <VariantEvidence profile={profile} groups={allGroups} />
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------------ page */

const TYPE_LABEL: Record<RewardType, string> = {
  item: "Item",
  skill: "Skill",
  equipment: "Equipment",
  furniture: "Furniture",
  valuable: "Valuable",
  job: "Job",
};

/** Best evidence grade in a profile, so executed sources are listed first. */
const profileGradeRank = (profile: Profile): number =>
  profile.edges.length ? Math.min(...profile.edges.map((edge) => GRADE_ORDER[gradeTone(edgeGrade(edge))])) : 99;

export default function ItemSourcesPage() {
  const [mode, setMode] = useState<"item" | "treasure">("item");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<{ type: RewardType; name: string } | null>(null);
  const [kindFilter, setKindFilter] = useState("All");
  const [areaLevel, setAreaLevel] = useState("");
  const [treasureQuery, setTreasureQuery] = useState("");
  const [visible, setVisible] = useState(12);

  const level = areaLevel.trim() && Number.isInteger(Number(areaLevel)) && Number(areaLevel) > 0 ? Number(areaLevel) : null;
  const matches = useMemo(() => searchRewardTargets(query), [query]);
  const rows = useMemo(
    () => (selected
      ? [...buildItemSourceRows(selected.type, selected.name), ...buildItemDirectRows(selected.type, selected.name)]
        // Direct routes use the same evidence order, so a guaranteed shop row sits next to the
        // drop routes it competes with instead of being buried at the end.
        .sort((a, b) => GRADE_ORDER[gradeTone(a.grade)] - GRADE_ORDER[gradeTone(b.grade)]
          || a.family.localeCompare(b.family)
          || a.requirement.localeCompare(b.requirement))
      : []),
    [selected],
  );
  const unresolved = useMemo(
    () => (selected ? unresolvedProfilesForReward(selected.type, selected.name) : []),
    [selected],
  );
  const kindOptions = useMemo(() => ["All", ...[...new Set(rows.map((row) => row.kind))].sort()], [rows]);
  const filtered = useMemo(() => rows.filter((row) => kindFilter === "All" || row.kind === kindFilter), [rows, kindFilter]);

  const sortedBrowse = useMemo(() => [...searchProfiles(treasureQuery, 400)].sort((a, b) =>
    profileGradeRank(a) - profileGradeRank(b) || a.name.localeCompare(b.name)),
  [treasureQuery]);

  const select = (type: RewardType, name: string) => {
    setSelected({ type, name });
    setQuery(name);
    setKindFilter("All");
    setMode("item");
  };
  const itemLabel = selected ? safeRewardLabel(selected.type, selected.name) : "";

  return (
    <main className="container mx-auto max-w-6xl space-y-6 px-4 py-8">
      <PageHeader icon={<Search className="h-5 w-5" />} title="Item & Skill Sources">
        <p>
          Search for something you want and see every known route to it: which source, under what condition,
          how many you get, and the overall chance.
        </p>
      </PageHeader>

      <div className="flex flex-wrap gap-2">
        <Button variant={mode === "item" ? "default" : "outline"} onClick={() => setMode("item")}>Find an item or skill</Button>
        <Button variant={mode === "treasure" ? "default" : "outline"} onClick={() => setMode("treasure")}>Treasure data lookup</Button>
        <Button variant="ghost" asChild><Link href="/monster-spawns">Browse monster loot</Link></Button>
      </div>

      {mode === "item" ? (
        <>
          <Card>
            <CardContent className="space-y-4 pt-6">
              <label htmlFor="item-source-search" className="text-sm font-medium">Item or skill name</label>
              <Input
                id="item-source-search"
                value={query}
                onChange={(event) => { setQuery(event.target.value); setSelected(null); setKindFilter("All"); }}
                placeholder="Try Holy Herb, Pretty Cloth or Myriad Arrows"
                autoComplete="off"
              />
              {!selected && (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {matches.map((target) => (
                    <button
                      key={`${target.type}|${target.name}`}
                      onClick={() => select(target.type, target.name)}
                      className="flex items-center gap-3 rounded-md border p-3 text-left hover:bg-muted"
                    >
                      <RewardIcon type={target.type} name={target.name} className="h-10 w-10" />
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{treasureDisplayName(target.name)}</span>
                        <span className="text-xs text-muted-foreground">
                          {TYPE_LABEL[target.type]} · {target.profileCount} treasure{target.profileCount === 1 ? "" : "s"}
                          {target.directCount > 0 && ` · ${target.directCount} other source${target.directCount === 1 ? "" : "s"}`}
                        </span>
                      </span>
                    </button>
                  ))}
                  {query && !matches.length && <p className="text-sm text-muted-foreground">Nothing in the generated source graph matches that name.</p>}
                </div>
              )}
            </CardContent>
          </Card>

          {selected && (
            <>
              <div className="flex flex-wrap items-center gap-4" data-testid="item-header">
                <span className="flex h-20 w-20 shrink-0 items-center justify-center rounded-xl border bg-card">
                  <RewardIcon type={selected.type} name={selected.name} className="h-14 w-14" />
                </span>
                <div className="min-w-0">
                  <h2 className="text-2xl font-semibold tracking-tight">{itemLabel}</h2>
                  <p className="mt-0.5 text-sm text-muted-foreground">All known ways to obtain {itemLabel}.</p>
                  <Badge variant="secondary" className="mt-2">{TYPE_LABEL[selected.type]}</Badge>
                </div>
              </div>

              <div className="flex flex-wrap items-end gap-4 rounded-lg border p-3">
                <div>
                  <label htmlFor="source-kind" className="text-xs font-medium text-muted-foreground">Source type</label>
                  <select
                    id="source-kind"
                    value={kindFilter}
                    onChange={(event) => setKindFilter(event.target.value)}
                    className="mt-1 block h-9 rounded-md border bg-background px-2 text-sm"
                  >
                    {kindOptions.map((kind) => <option key={kind}>{kind}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="source-level" className="text-xs font-medium text-muted-foreground">Area level (optional)</label>
                  <Input
                    id="source-level"
                    type="number"
                    min="1"
                    step="1"
                    value={areaLevel}
                    onChange={(event) => setAreaLevel(event.target.value)}
                    placeholder="Any level"
                    className="mt-1 h-9 w-36"
                  />
                </div>
                <p className="pb-1 text-xs text-muted-foreground">
                  {filtered.length} route{filtered.length === 1 ? "" : "s"}
                  {unresolved.length > 0 && ` · ${unresolved.length} recorded treasure${unresolved.length === 1 ? "" : "s"} with no source`}
                </p>
              </div>

              <ItemSourceTable rows={filtered} level={level} itemLabel={itemLabel} />

              {unresolved.length > 0 && (
                <details className="rounded-lg border p-4 text-sm">
                  <summary className="cursor-pointer font-medium">
                    Recorded treasures with no established source ({unresolved.length})
                  </summary>
                  <p className="mt-2 text-xs text-muted-foreground">
                    These contents exist in the original treasure table, but no source is established, so no chance is shown.
                  </p>
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {unresolved.map(({ profile, lanes }) => (
                      <li key={profile.id} className="flex items-center gap-2">
                        <img src={profile.icon} alt="" className="h-5 w-5 object-contain [image-rendering:pixelated]" />
                        <span>{treasureDisplayName(profile.name)}</span>
                        <span className="font-mono">#{profile.memberIds.join(", #")}</span>
                        <span>· {lanes.map((lane) => `${formatPercent(lane.rate / 100, 3)} inside it`).join(", ")}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {!filtered.length && !unresolved.length && (
                <p className="rounded-lg border p-4 text-sm text-muted-foreground">
                  Nothing in the generated source graph grants this entry. That is not proof it is unobtainable.
                </p>
              )}

              <div className="flex items-start gap-2 rounded-lg border bg-muted/30 p-3 text-sm">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <p className="text-muted-foreground">
                  Overall chance = chance of the treasure from that source × chance of {itemLabel} inside that treasure.
                  Direct rewards stay at 100%.
                </p>
              </div>

              <ProbabilityStages />
            </>
          )}
        </>
      ) : (
        <>
          <Card>
            <CardContent className="space-y-3 pt-6">
              <label htmlFor="treasure-search" className="text-sm font-medium">Treasure name, contained item, or source</label>
              <Input
                id="treasure-search"
                value={treasureQuery}
                onChange={(event) => { setTreasureQuery(event.target.value); setVisible(12); }}
                placeholder="Try Treasure Chest, Holy Herb, or Legendary Cave"
                autoComplete="off"
              />
              <p className="text-sm text-muted-foreground">
                {PROJECTION_STATS.profiles.toLocaleString()} player-facing treasures from {PROJECTION_STATS.memberIds.toLocaleString()} internal rows
                {sortedBrowse.length > visible ? ` · showing ${visible} of ${sortedBrowse.length}` : ` · ${sortedBrowse.length} match${sortedBrowse.length === 1 ? "" : "es"}`}.
                Each treasure is shown in full: contents, every source, variants and evidence.
              </p>
            </CardContent>
          </Card>
          <ProbabilityStages />
          <div className="grid items-start gap-4 xl:grid-cols-2">
            {sortedBrowse.slice(0, visible).map((profile) => (
              <ProfileCard key={profile.id} profile={profile} level={null} kindFilter="All" />
            ))}
          </div>
          {visible < sortedBrowse.length && (
            <Button variant="outline" onClick={() => setVisible((count) => count + 12)}>Show 12 more</Button>
          )}
        </>
      )}

      <details className="rounded-lg border p-4 text-sm text-muted-foreground">
        <summary className="cursor-pointer font-medium text-foreground">Where this page's data comes from</summary>
        <div className="mt-3 space-y-2">
          <p>
            Display grouping, source attribution and every probability come from the generated treasure-source
            graph and its player-facing profile layer. This page only groups identical sources for readability,
            formats them and indexes them — it never merges treasures or derives a new number.
          </p>
          <p>
            {PROJECTION_STATS.sourceEdges.toLocaleString()} source→treasure edges in {PROJECTION_STATS.groupedProfiles} grouped profiles,
            {" "}{PROJECTION_STATS.profilesWithSource.toLocaleString()} profiles with a source, {PROJECTION_STATS.profilesWithoutSource.toLocaleString()} unresolved,
            {" "}and {PROJECTION_STATS.directSourceEdges.toLocaleString()} non-treasure routes.
          </p>
          <ul className="space-y-1">
            {Object.keys(GRADE_LABELS_PLAIN).map((grade) => (
              <li key={grade}>
                <span className="font-medium text-foreground">{GRADE_LABELS_PLAIN[grade as EvidenceGrade]}</span>
                {" — "}{GRADE_NOTES_PLAIN[grade as EvidenceGrade]}
              </li>
            ))}
          </ul>
          <p className="text-xs">
            These are static findings from the original game data. Only the Legendary Cave route-chest selection has
            been run against the original game; nothing here has been validated by playing the game.
          </p>
        </div>
      </details>
    </main>
  );
}
