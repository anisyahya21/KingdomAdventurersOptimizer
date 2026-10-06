import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  CONSUMABLE_UNSUPPORTED_TEXT,
  type GeneratedChestDrop,
  type GeneratedConsumableRow,
} from "@/lib/generated-battle-visuals";

/**
 * Fight HUD pieces for the generated battle replay, 2026-09-21 visual pass.
 *
 * These are presentation only. Every number here is read from the runner's own replay frames or from
 * the branch the runner returned for an item click - the HUD never re-simulates a fight, never applies
 * an item effect of its own and never paints a heal or a hit that the runner did not report.
 *
 * Layout intent (matched against the user's own battle screenshots): the treasure strip sits along the
 * top edge of the arena frame, the readable ally HP/MP block and the status tick sit under it, and the
 * consumable action bar lives inside the same frame at the bottom so battlefield and items share one
 * screen on desktop and on a narrow viewport. The HUD stays to a few short rows (the ally block is one
 * line on a desktop-width frame) because the arena below it is sized from what is left of the viewport.
 */

const TREASURE_STRIP_LIMIT = 8;

/**
 * How the runner certified the eligible chest count (SHIP-RUNTIME-CONTRACT.md section 3). The count is
 * only "pre-result" when the certificate basis is present; "native-win-loss-gate" is the loss-side 0.
 */
export function chestEligibilityLabel(awarded: number | null | undefined, basis: string | null | undefined): string | null {
  if (typeof awarded !== "number" || !Number.isSafeInteger(awarded) || awarded < 0) return null;
  if (basis === "reward-entitlement-certificate") return `${awarded} eligible (pre-result certificate)`;
  if (basis === "native-win-loss-gate") return `${awarded} eligible (native loss gate)`;
  return `${awarded} eligible in simulation`;
}

export type AllyVitalRow = {
  unitId: string;
  name: string;
  hp: number;
  maxHp: number | null;
  mp: number;
  maxMp: number | null;
  dead: boolean;
};

function gaugePercent(current: number, maximum: number): number {
  if (!Number.isFinite(current) || !Number.isFinite(maximum) || maximum <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((current * 100) / maximum)));
}

/* ------------------------------------------------------------------ */
/* Treasure strip                                                      */
/* ------------------------------------------------------------------ */

function TreasureIcon({ drop }: { drop: GeneratedChestDrop }) {
  if (!drop.icon) {
    return (
      <span
        className="ka-hud-chest ka-hud-chest--empty"
        data-chest-sprite="unresolved"
        title={drop.name ?? "unresolved box"}
      >
        ?
      </span>
    );
  }
  return (
    <img
      src={drop.icon}
      alt={drop.name ?? "treasure box"}
      className="ka-hud-chest"
      data-chest-sprite={drop.name ?? "treasure"}
      title={drop.name ? `${drop.name} - tick ${drop.tick}` : `tick ${drop.tick}`}
    />
  );
}

/**
 * The queued-chest strip: a count plus the real treasure sprites of the chests the runner has already
 * queued by the displayed tick. Mid-fight the count is explicitly provisional; the certified figure
 * only appears once the clock reaches the replay's own end tick.
 */
export function BattleTreasureStrip({
  queued,
  drops,
  atEnd,
  awarded,
  awardedBasis,
  pending,
}: {
  /** chests the runner queued at or before the displayed tick */
  queued: number;
  /** those same drops, in runner order (already filtered to the displayed tick) */
  drops: GeneratedChestDrop[];
  atEnd: boolean;
  awarded: number | null;
  awardedBasis?: string | null;
  pending: number;
}) {
  const shown = drops.slice(0, TREASURE_STRIP_LIMIT);
  const overflow = drops.length - shown.length;
  return (
    <div className="ka-hud-strip" data-generated-chest-summary data-generated-live-chests={queued}>
      <span className="ka-hud-strip__label">Chests</span>
      <span className="ka-hud-strip__count" data-generated-live-chest-count={queued}>
        {queued}
      </span>
      <span className="ka-hud-strip__icons" data-generated-chest-drops>
        {shown.map((drop) => (
          <span key={drop.seq} data-generated-chest={drop.treasureId ?? "unresolved"}>
            <TreasureIcon drop={drop} />
          </span>
        ))}
        {overflow > 0 ? (
          <span className="ka-hud-chip" data-chest-overflow>
            +{overflow}
          </span>
        ) : null}
        {drops.length === 0 ? <span className="ka-hud-strip__empty">none yet</span> : null}
      </span>
      <span className="ka-hud-strip__note" data-generated-chest-live-note>
        {atEnd
          ? `${chestEligibilityLabel(awarded, awardedBasis) ?? "final eligibility unknown"} · ${pending} queued`
          : queued === 0
            ? "provisional until the fight ends"
            : "provisional while the fight plays"}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Readable ally HP / MP                                               */
/* ------------------------------------------------------------------ */

/**
 * The readable ally block. The stage draws the recovered native bars on the front row of each team;
 * this block repeats the same frame values as text so a dark or crowded arena never hides an ally's
 * HP/MP. It reads `frame.units` verbatim - no derived damage, no optimistic local heal.
 */
export function BattleAllyVitals({ rows }: { rows: AllyVitalRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="ka-hud-vitals" data-ally-vitals>
      {rows.map((row) => {
        const hpPercent = row.maxHp === null ? 0 : gaugePercent(row.hp, row.maxHp);
        const mpPercent = row.maxMp === null ? 0 : gaugePercent(row.mp, row.maxMp);
        return (
          <div
            key={row.unitId}
            className={`ka-hud-vital${row.dead ? " is-dead" : ""}`}
            data-ally-vitals-unit={row.unitId}
            data-ally-vitals-name={row.name}
            data-ally-hp={row.hp}
            data-ally-max-hp={row.maxHp ?? "unknown"}
            data-ally-mp={row.mp}
            data-ally-max-mp={row.maxMp ?? "unknown"}
            data-ally-dead={row.dead ? "1" : "0"}
          >
            <span className="ka-hud-vital__name">{row.name}</span>
            <span className="ka-hud-vital__bars">
              <span className="ka-hud-bar">
                <span className="ka-hud-bar__fill ka-hud-bar__fill--hp" style={{ width: `${hpPercent}%` }} />
              </span>
              <span className="ka-hud-bar">
                <span className="ka-hud-bar__fill ka-hud-bar__fill--mp" style={{ width: `${mpPercent}%` }} />
              </span>
            </span>
            <span className="ka-hud-vital__nums" data-ally-vitals-number>
              <span className="ka-hud-vital__hp">
                HP {row.hp}/{row.maxHp ?? "?"}
              </span>
              <span className="ka-hud-vital__mp">
                MP {row.mp}/{row.maxMp ?? "?"}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* In-frame consumable action bar                                      */
/* ------------------------------------------------------------------ */

/**
 * The action bar that sits inside the arena frame. One button per provisioned item, showing the real
 * item icon, the remaining stock at the displayed tick and a pending indicator the instant a click is
 * accepted. The click itself branches the same fight through the interaction contract; the HUD only
 * reports what the runner returned.
 */
export function ConsumableActionBar({
  rows,
  tick,
  busy,
  blockedReason,
  onUse,
}: {
  rows: GeneratedConsumableRow[];
  tick: number;
  busy: string | null;
  /** non-null when the fight cannot be branched at all (no stored source scenario) */
  blockedReason: string | null;
  onUse: (row: GeneratedConsumableRow) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="ka-hud-empty" data-consumable-none>
        This fight carried no recovery items.
      </p>
    );
  }
  return (
    <div
      className="ka-hud-items"
      data-consumable-bar
      data-consumable-tick={tick}
      data-consumable-pending={busy ? "1" : "0"}
      data-consumable-busy={busy ?? ""}
    >
      {rows.map((row) => {
        const pending = busy === row.key;
        const usable = row.supported && row.usableAtTick && blockedReason === null;
        const disabled = !row.supported || !row.usableAtTick || blockedReason !== null || busy !== null;
        /* the button's own words: what the item can do right now, not the contract's tick bookkeeping */
        const reason = blockedReason
          ? blockedReason
          : !row.supported
            ? `${CONSUMABLE_UNSUPPORTED_TEXT}.`
            : row.usableAtTick
              ? `Usable now: ${row.usableReason}.`
              : `${row.usableReason}.`;
        return (
          <button
            key={row.key}
            type="button"
            disabled={disabled}
            aria-label={`${row.label} - stock ${row.stockAtTick} of ${row.declaredStock}`}
            title={reason}
            onClick={() => onUse(row)}
            className={`ka-hud-item${usable ? " is-usable" : ""}${pending ? " is-pending" : ""}`}
            data-consumable={row.key}
            data-consumable-stock={row.stockAtTick}
            data-consumable-declared-stock={row.declaredStock}
            data-consumable-supported={row.supported ? "1" : "0"}
            data-consumable-usable={row.usableAtTick ? "1" : "0"}
            data-consumable-action={row.key}
            data-consumable-pending={pending ? "1" : "0"}
            data-action={`use-${row.key}`}
          >
            {row.icon ? (
              <img src={row.icon} alt="" className="ka-hud-item__icon" />
            ) : (
              <span className="ka-hud-item__icon ka-hud-item__icon--empty" />
            )}
            <span className="ka-hud-item__label">{row.label}</span>
            <span className="ka-hud-item__stock" data-consumable-stock-badge>
              {row.stockAtTick}
              <span className="ka-hud-item__stock-max">/{row.declaredStock}</span>
            </span>
            {pending ? (
              <span className="ka-hud-item__pending" data-consumable-pending-label>
                sending…
              </span>
            ) : usable ? (
              <span className="ka-hud-item__ready">use</span>
            ) : null}
            <span className="sr-only" data-consumable-reason>
              {reason}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Verdict popup                                                       */
/* ------------------------------------------------------------------ */

/**
 * The end-of-fight popup. It appears when the clock reaches the replay's own verdict tick
 * (`finalState.verdictTick`, the frame Ending was entered) - not the playback cut and not the
 * declared horizon - and reports the runner's verdict word plus the queued vs eligible chest pair;
 * it never claims a collected reward.
 */
export function BattleVerdictPopup({
  word,
  verdict,
  tick,
  censored,
  liveWindow = false,
  queued,
  awarded,
  awardedBasis,
  onRestart,
}: {
  word: string;
  verdict: number | null;
  /** the runner's own verdict tick (`finalState.verdictTick`), not the playback cut */
  tick: number | null;
  censored: boolean;
  liveWindow?: boolean;
  queued: number;
  awarded: number | null;
  awardedBasis?: string | null;
  onRestart: () => void;
}) {
  const tone = verdict === 1 ? "win" : verdict === 2 ? "loss" : "unresolved";
  const eligible = chestEligibilityLabel(awarded, awardedBasis);
  return (
    <div
      className={`ka-verdict ka-verdict--${tone}`}
      data-verdict-popup
      data-verdict={word}
      data-verdict-tone={tone}
      data-verdict-tick={tick ?? ""}
    >
      <div className="ka-verdict__card">
        <p className="ka-verdict__word">{word}</p>
        <p className="ka-verdict__sub" data-verdict-summary>
          {liveWindow && verdict !== null
            ? `${queued} chest${queued === 1 ? "" : "s"} queued so far · rewards provisional`
            : censored
            ? "incomplete simulation record"
            : `${queued} chest${queued === 1 ? "" : "s"} queued${eligible ? ` · ${eligible}` : ""}`}
          {tick === null ? "" : ` · verdict tick ${tick}`}
        </p>
        <div className="ka-verdict__actions">
          <Badge variant="outline" className="text-[10px]">
            simulated fight
          </Badge>
          <Button size="sm" variant="outline" onClick={onRestart} data-action="verdict-restart">
            Watch again
          </Button>
        </div>
      </div>
    </div>
  );
}
