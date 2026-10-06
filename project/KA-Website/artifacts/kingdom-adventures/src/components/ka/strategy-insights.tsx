import { Shield, Swords, Users } from "lucide-react";
import { getSkillIcon } from "@/lib/skill-icons";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { type TeamBuildSlot } from "@/lib/strategy-team-build";
import { PARAMETER_NATIVE_NAMES, STAT_PARAMETER_IDS } from "@/game-data/stat-parameter-ids";

export type OutcomeDistribution = {
  available: boolean; reason?: string; phase?: string;
  bins?: { chests: number; count: number }[];
  knownSamples?: number; evidenceRows?: number; lifetimeValidationTotal?: number | null;
  unresolved?: number; unknown?: number; coverage?: string; missingEvidence?: number | null;
  min?: number | null; max?: number | null; mean?: number | null;
  p10?: number | null; median?: number | null; p90?: number | null; mode?: number | null;
};
export type StrategyFormation = {
  units: { name: string; slot: number; row: number; column: number;
    parameters: Record<string, { value: number; maximum: number }>; effectiveDefense: number }[];
  error?: string;
};
const number = (value: number | null | undefined) => value == null ? "Not measured" : value.toLocaleString(undefined, { maximumFractionDigits: 2 });

export function OutcomeHistogram({ distribution: d }: { distribution?: OutcomeDistribution | null }) {
  const bins = d?.bins ?? [];
  if (!d?.available || !bins.length) return <div className="rounded-xl border p-4" data-outcome-distribution="unavailable">
    <h3 className="font-semibold">Chest distribution</h3>
    <p className="mt-1 text-sm text-muted-foreground">No measured validation distribution is available for this build yet. Saved examples alone do not describe its full results.</p>
  </div>;
  const low = bins[0].chests, high = bins[bins.length - 1].chests;
  const maximum = Math.max(...bins.map(bin => bin.count));
  const extent = Math.max(1, high - low + 1);
  return <section className="space-y-3 rounded-xl border p-4" data-outcome-distribution>
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="font-semibold">How often does it deliver?</h3>
      <span className="text-xs text-muted-foreground">{number(d.knownSamples)} measured validation outcomes</span>
    </div>
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {[["Typical · median", number(d.median)], ["Middle 80%", `${number(d.p10)}–${number(d.p90)}`],
        ["Worst observed", number(d.min)], ["Best observed", number(d.max)]].map(([label, value]) =>
        <div key={label} data-stat-label={label} className="rounded-lg bg-muted/40 p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="text-xl font-semibold tabular-nums">{value}</div></div>)}
    </div>
    <svg viewBox="0 0 640 165" role="img" aria-label={`Measured chest distribution: minimum ${d.min}, median ${d.median}, maximum ${d.max}. ${d.knownSamples} results.`} className="w-full max-h-48 text-primary">
      <line x1="28" y1="136" x2="628" y2="136" stroke="currentColor" opacity=".25" />
      <text x="28" y="12" fontSize="11" fill="currentColor">Frequency · highest bar {number(maximum)} runs</text>
      {bins.map(bin => <rect key={bin.chests} x={28 + (bin.chests - low) / extent * 596} y={136 - bin.count / maximum * 108}
        width={Math.max(1, 596 / extent - 1)} height={bin.count / maximum * 108} fill="currentColor" rx="1">
        <title>{bin.chests} chests: {number(bin.count)} runs ({((bin.count / (d.knownSamples || 1)) * 100).toFixed(1)}%)</title>
      </rect>)}
      <text x="28" y="157" fontSize="11" fill="currentColor">{low} chests</text><text x="626" y="157" textAnchor="end" fontSize="11" fill="currentColor">{high} chests</text>
    </svg>
    <p className="text-xs text-muted-foreground">Average {number(d.mean)} · most frequent result {number(d.mode)} chests. Observed frequencies; the shape need not be a bell curve.</p>
    {d.coverage !== "complete" || !!d.unresolved || !!d.unknown ? <p className="text-xs text-muted-foreground" data-distribution-coverage>
      Coverage: {number(d.evidenceRows)} recorded outcomes of {number(d.lifetimeValidationTotal)} validation attempts.
      {d.missingEvidence ? ` ${number(d.missingEvidence)} older outcomes are unavailable.` : ""}
      {d.unresolved ? ` ${number(d.unresolved)} unresolved fights excluded.` : ""}{d.unknown ? ` ${number(d.unknown)} unknown chest readings excluded.` : ""}
    </p> : null}
    <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Exact frequencies</summary>
      <div className="mt-2 max-h-48 overflow-auto"><table className="w-full text-left"><thead><tr><th>Chests</th><th>Runs</th><th>Share</th></tr></thead>
        <tbody>{bins.map(bin => <tr key={bin.chests}><td>{bin.chests}</td><td>{number(bin.count)}</td><td>{(bin.count / (d.knownSamples || 1) * 100).toFixed(1)}%</td></tr>)}</tbody></table></div>
    </details>
  </section>;
}

export function StrategyFormationPanel({ slots, formation }: { slots: TeamBuildSlot[]; formation?: StrategyFormation | null }) {
  const units = formation?.units ?? [];
  const rows = [...new Set(units.map(unit => unit.row))].sort((a, b) => a - b);
  return <section className="space-y-3 rounded-xl border p-4" data-strategy-formation>
    <div className="flex items-center gap-2"><Users className="h-4 w-4" /><h3 className="font-semibold">Formation & team</h3></div>
    {rows.length ? <div className="space-y-2" aria-label="Starting formation, front row first">
      <p className="text-xs text-muted-foreground">Enemy side ↑ · front row first</p>
      {rows.map(row => <div key={row} className="grid grid-cols-5 gap-1.5">{Array.from({ length: 5 }, (_, column) => {
        const unit = units.find(unit => unit.row === row && unit.column === column);
        const slot = slots.find(slot => slot.slot === unit?.slot);
        const icon = getEquipmentIcon({}, slot?.weaponName);
        return <div key={column} className="flex min-w-0 flex-col items-center justify-center gap-1 rounded-lg border bg-muted/30 px-1 py-3 text-center" data-formation-cell={`${column},${row}`}>
          {unit ? <><span className="text-[10px] text-muted-foreground">#{unit.slot}</span>{icon ? <img src={icon} alt="" className="h-7 w-7 object-contain [image-rendering:pixelated]" /> : <Swords className="h-5 w-5 text-muted-foreground" />}<span className="break-words text-xs font-medium">{unit.name}</span><span className="text-[10px] text-muted-foreground">{slot?.weaponName ?? slot?.kind}</span></> : <span className="text-xs text-muted-foreground">—</span>}
        </div>;
      })}</div>)}
      <p className="text-xs text-muted-foreground">Prepared starting positions for this exact build. Select Team & skills for the complete loadout.</p>
    </div> : <p className="text-sm text-muted-foreground">Starting positions are unavailable. The recorded team is shown below.</p>}
    <div className="grid gap-3 md:grid-cols-2">{slots.map(slot => {
      const prepared = units.find(unit => unit.slot === slot.slot);
      return <div key={slot.slot} className="space-y-2 rounded-lg border p-3" data-team-summary-slot={slot.slot}>
        <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">#{slot.slot} · {slot.name}</span><span className="text-xs text-muted-foreground">{slot.kind}</span></div>
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Swords className="h-3.5 w-3.5" />{slot.weaponName ?? "Weapon not recorded"}{prepared ? <><Shield className="ml-2 h-3.5 w-3.5" />{number(prepared.effectiveDefense)}</> : null}</p>
        {prepared ? <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums" data-effective-stats>{[STAT_PARAMETER_IDS.hp, STAT_PARAMETER_IDS.mp, STAT_PARAMETER_IDS.atk, STAT_PARAMETER_IDS.def, STAT_PARAMETER_IDS.spd, STAT_PARAMETER_IDS.int, STAT_PARAMETER_IDS.vig, STAT_PARAMETER_IDS.lck, STAT_PARAMETER_IDS.dex].map(id => {
          const value = prepared.parameters[String(id)];
          return value ? <span key={id}><span className="text-muted-foreground">{PARAMETER_NATIVE_NAMES[id]} </span>{number(value.value)}</span> : null;
        })}<span className="text-muted-foreground">effective values</span></div> : null}
        <div className="flex flex-wrap gap-1.5" aria-label="Equipped items">{slot.equipment.map((item, index) => {
          const icon = getEquipmentIcon({}, item.name);
          return <span key={index} className="inline-flex items-center gap-1 rounded-md border px-1.5 py-1 text-xs">{icon ? <img src={icon} alt="" className="h-4 w-4 object-contain [image-rendering:pixelated]" /> : null}{item.name ?? `Item ${item.id ?? "unknown"}`}{item.level == null ? "" : ` · Lv ${item.level}`}</span>;
        })}</div>
        <div className="flex flex-wrap gap-1.5">{slot.skills.map(skill => {
          const icon = getSkillIcon(skill.name);
          return <span key={skill.slot} className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-1 text-xs" title={`${skill.triggerLabel} · skill order ${skill.slot}`}>
            {icon ? <img src={icon} alt="" className="h-4 w-4 object-contain [image-rendering:pixelated]" /> : null}{skill.name ?? `Skill ${skill.id}`}<span className="text-[10px] text-muted-foreground">{skill.triggerLabel}</span>
          </span>;
        })}</div>
      </div>;
    })}</div>
  </section>;
}
