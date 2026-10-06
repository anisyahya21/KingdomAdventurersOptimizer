import { useState } from "react";
import { Shield } from "lucide-react";
import { Input } from "@/components/ui/input";

// ── Data (from KA GameData - Rank.csv) ────────────────────────────────────────
// Columns used: rank, exp, rankupTimeSeconds, maxResidents,
//   staminaPumpCapacity, staminaCapacity, maxDangeons, maxEnemies,
//   maxDangeonsEnemies, warehouseCap (col17 = all 8 types share same value)
// "exp" = XP required to trigger the Town Hall upgrade to this rank
// "rankupTimeSeconds" = upgrade time to reach this rank from the previous one
// ─────────────────────────────────────────────────────────────────────────────

export interface RankEntry {
  rank:          number;
  exp:           number;  // XP needed to unlock upgrade
  upgradeTime:   number;  // seconds
  maxResidents:  number;
  staminaPump:   number;
  staminaCap:    number;
  maxDungeons:   number;
  maxEnemies:    number;
  maxDungeonCap: number;  // enemies per dungeon
  warehouseCap:  number;
}

// Compact: [rank, exp, upgradeTimeSec, maxRes, pump, stamCap, dungeons, enemies, dungCap, warehouse]
const RAW: [number,number,number,number,number,number,number,number,number,number][] = [
  [  0,       0,      0,   3, 10, 50,  1,  3, 1,  4],
  [  1,       0,      5,   4, 10, 50,  1,  5, 1,  8],
  [  2,      48,    120,   5, 12, 52,  1,  5, 1,  8],
  [  3,     220,    300,   6, 14, 54,  1,  6, 1,  8],
  [  4,     348,    420,   7, 16, 56,  1,  6, 1,  8],
  [  5,     572,    600,   8, 18, 58,  1,  6, 1, 12],
  [  6,     948,    900,   9, 20, 60,  1,  7, 1, 12],
  [  7,    1540,   1200,  10, 22, 62,  1,  7, 1, 12],
  [  8,    2428,   1800,  11, 24, 64,  1,  8, 1, 12],
  [  9,    3700,   2700,  12, 26, 66,  1,  8, 1, 12],
  [ 10,    5500,   3600,  13, 28, 68,  1,  8, 1, 16],
  [ 11,    5800,   7200,  14, 30, 70,  2,  9, 2, 16],
  [ 12,    6100,  10800,  15, 32, 72,  2,  9, 2, 16],
  [ 13,    6400,  14400,  16, 34, 74,  2, 10, 2, 16],
  [ 14,    6700,  18000,  17, 36, 76,  2, 10, 2, 16],
  [ 15,    7000,  21600,  18, 38, 78,  2, 10, 2, 20],
  [ 16,    7300,  25200,  19, 40, 80,  2, 11, 2, 20],
  [ 17,    7600,  28800,  20, 42, 82,  2, 11, 2, 20],
  [ 18,    7900,  32400,  21, 44, 84,  2, 11, 2, 20],
  [ 19,    8200,  36000,  22, 46, 86,  2, 12, 2, 20],
  [ 20,    8500,  39600,  23, 48, 88,  2, 12, 2, 24],
  [ 21,    9000,  43200,  24, 50, 90,  2, 13, 2, 24],
  [ 22,    9500,  46800,  25, 52, 92,  2, 13, 2, 24],
  [ 23,   10000,  50400,  26, 54, 94,  2, 13, 2, 24],
  [ 24,   10500,  54000,  27, 56, 96,  2, 14, 2, 24],
  [ 25,   11000,  57600,  28, 58, 98,  2, 14, 2, 28],
  [ 26,   11500,  61200,  29, 60,100,  2, 15, 3, 28],
  [ 27,   12000,  64800,  30, 62,102,  2, 15, 3, 28],
  [ 28,   12500,  68400,  31, 64,104,  2, 15, 3, 28],
  [ 29,   13000,  72000,  32, 66,106,  2, 16, 3, 28],
  [ 30,   13500,  75600,  33, 68,108,  2, 16, 3, 32],
  [ 31,   14000,  79200,  34, 70,110,  3, 16, 3, 32],
  [ 32,   14500,  82800,  35, 72,112,  3, 17, 3, 32],
  [ 33,   15000,  86400,  36, 74,114,  3, 17, 3, 32],
  [ 34,   15500,  90000,  37, 76,116,  3, 18, 3, 32],
  [ 35,   16000,  93600,  38, 78,118,  3, 18, 3, 36],
  [ 36,   16500,  97200,  39, 80,120,  3, 18, 3, 36],
  [ 37,   17000, 100800,  40, 82,122,  3, 19, 3, 36],
  [ 38,   17500, 104400,  41, 84,124,  3, 19, 3, 36],
  [ 39,   18000, 108000,  42, 86,126,  3, 20, 3, 36],
  [ 40,   18500, 111600,  43, 88,128,  3, 20, 3, 40],
  [ 41,   19000, 115200,  44, 90,130,  3, 20, 3, 40],
  [ 42,   19500, 118800,  45, 92,132,  3, 21, 4, 40],
  [ 43,   20000, 122400,  46, 94,134,  3, 21, 4, 40],
  [ 44,   20500, 126000,  47, 96,136,  3, 21, 4, 40],
  [ 45,   21000, 129600,  48, 98,138,  3, 22, 4, 44],
  [ 46,   21500, 133200,  49,100,140,  3, 22, 4, 44],
  [ 47,   22000, 136800,  50,102,142,  3, 23, 4, 44],
  [ 48,   22500, 140400,  51,104,144,  3, 23, 4, 44],
  [ 49,   23000, 144000,  52,106,146,  3, 23, 4, 44],
  [ 50,   24000, 147600,  53,108,148,  3, 24, 4, 48],
  [ 51,   25000, 151200,  54,110,150,  4, 24, 4, 48],
  [ 52,   26000, 154800,  55,112,152,  4, 25, 4, 48],
  [ 53,   27000, 158400,  56,114,154,  4, 25, 4, 48],
  [ 54,   28000, 162000,  57,116,156,  4, 25, 4, 48],
  [ 55,   29000, 165600,  58,118,158,  4, 26, 4, 52],
  [ 56,   30000, 169200,  59,120,160,  4, 26, 4, 52],
  [ 57,   31000, 172800,  60,122,162,  4, 26, 4, 52],
  [ 58,   32000, 180000,  61,124,164,  4, 27, 5, 52],
  [ 59,   33000, 187200,  62,126,166,  4, 27, 5, 52],
  [ 60,   34000, 194400,  63,128,168,  4, 28, 5, 56],
  [ 61,   35000, 201600,  64,130,170,  4, 28, 5, 56],
  [ 62,   36000, 208800,  65,132,172,  4, 28, 5, 56],
  [ 63,   37000, 216000,  66,134,174,  4, 29, 5, 56],
  [ 64,   38000, 223200,  67,136,176,  4, 29, 5, 56],
  [ 65,   39000, 230400,  68,138,178,  4, 30, 5, 60],
  [ 66,   40000, 237600,  69,140,180,  4, 30, 5, 60],
  [ 67,   41000, 244800,  70,142,182,  4, 30, 5, 60],
  [ 68,   42000, 252000,  71,144,184,  4, 31, 5, 60],
  [ 69,   43000, 259200,  72,146,186,  4, 31, 5, 60],
  [ 70,   44000, 259200,  73,148,188,  4, 31, 5, 64],
  [ 71,   45000, 280800,  74,150,190,  5, 32, 5, 64],
  [ 72,   46000, 302400,  75,152,192,  5, 32, 5, 64],
  [ 73,   47000, 324000,  76,154,194,  5, 33, 6, 64],
  [ 74,   48000, 345600,  77,156,196,  5, 33, 6, 64],
  [ 75,   49000, 367200,  78,158,198,  5, 33, 6, 68],
  [ 76,   50000, 388800,  79,160,200,  5, 34, 6, 68],
  [ 77,   51000, 410400,  80,162,202,  5, 34, 6, 68],
  [ 78,   52000, 432000,  81,164,204,  5, 35, 6, 68],
  [ 79,   53000, 453600,  82,166,206,  5, 35, 6, 68],
  [ 80,   55000, 475200,  83,168,208,  5, 35, 6, 72],
  [ 81,   57000, 496800,  84,170,210,  6, 36, 6, 72],
  [ 82,   59000, 518400,  85,172,212,  6, 36, 6, 72],
  [ 83,   61000, 540000,  86,174,214,  6, 36, 6, 72],
  [ 84,   63000, 561600,  87,176,216,  6, 37, 6, 72],
  [ 85,   65000, 583200,  88,178,218,  6, 37, 6, 76],
  [ 86,   67000, 604800,  89,180,220,  6, 38, 6, 76],
  [ 87,   69000, 626400,  90,182,222,  6, 38, 6, 76],
  [ 88,   71000, 648000,  91,184,224,  6, 38, 6, 76],
  [ 89,   73000, 669600,  92,186,226,  6, 39, 7, 76],
  [ 90,   80000, 691200,  93,188,228,  6, 39, 7, 80],
  [ 91,   85000, 712800,  94,190,230,  7, 40, 7, 80],
  [ 92,   90000, 734400,  95,192,232,  7, 40, 7, 80],
  [ 93,   95000, 756000,  96,194,234,  7, 40, 7, 80],
  [ 94,  100000, 777600,  97,196,236,  8, 41, 7, 80],
  [ 95,  105000, 799200,  98,198,238,  8, 41, 7, 84],
  [ 96,  110000, 820800,  99,200,240,  8, 41, 7, 84],
  [ 97,  115000, 842400, 100,202,242,  9, 42, 7, 84],
  [ 98,  120000, 864000, 105,204,244,  9, 42, 7, 84],
  [ 99,  125000, 885600, 110,206,246, 10, 43, 7, 84],
  [100,  150000, 907200, 120,208,248, 10, 43, 7, 88],
];

export const RANKS: RankEntry[] = RAW.map(r => ({
  rank:          r[0],
  exp:           r[1],
  upgradeTime:   r[2],
  maxResidents:  r[3],
  staminaPump:   r[4],
  staminaCap:    r[5],
  maxDungeons:   r[6],
  maxEnemies:    r[7],
  maxDungeonCap: r[8],
  warehouseCap:  r[9],
}));

export function getTownRankStaminaPumpCapacity(rank: number): number {
  const safeRank = Math.min(100, Math.max(0, Math.floor(rank)));
  return RANKS[safeRank]?.staminaPump ?? 0;
}

export function getTownRankStaminaCapacity(rank: number): number {
  const safeRank = Math.min(100, Math.max(0, Math.floor(rank)));
  return RANKS[safeRank]?.staminaCap ?? 0;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(s: number): string {
  if (s <= 0) return "—";
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h < 24) return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh > 0 ? `${d}d ${rh}h` : `${d}d`;
}

function fmtExp(n: number): string {
  if (n === 0) return "—";
  return n.toLocaleString();
}

// Cells that changed vs previous row get a subtle highlight
function changed(cur: RankEntry, prev: RankEntry | undefined, key: keyof RankEntry): boolean {
  if (!prev) return false;
  return cur[key] !== prev[key];
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TownRankPage() {
  const [myRank, setMyRank] = useState<string>("");
  const highlighted = parseInt(myRank, 10);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-5">

      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Town Rank Progression</h1>
        </div>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Upgrading your Town Hall increases your town rank (0–100), unlocking higher resident caps,
          more stamina, additional dungeon slots, and larger warehouse limits.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <label className="text-sm text-muted-foreground whitespace-nowrap">My current rank:</label>
        <Input
          type="number"
          min={0}
          max={100}
          value={myRank}
          onChange={e => setMyRank(e.target.value)}
          placeholder="0–100"
          className="h-8 w-24 text-sm"
        />
        {myRank !== "" && !isNaN(highlighted) && highlighted >= 0 && highlighted <= 100 && (
          <span className="text-xs text-muted-foreground">
            Next upgrade: <span className="font-medium text-foreground">{highlighted < 100 ? fmtTime(RANKS[highlighted + 1]?.upgradeTime ?? 0) : "—"}</span>
            {highlighted < 100 && RANKS[highlighted + 1]?.exp > 0 && (
              <> · <span className="font-medium text-foreground">{fmtExp(RANKS[highlighted + 1].exp)} XP</span></>
            )}
          </span>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-muted-foreground">
        <span><span className="inline-block w-2 h-2 rounded-sm bg-amber-400/60 mr-1 align-middle" />Value changed at this rank</span>
        {myRank !== "" && !isNaN(highlighted) && (
          <span><span className="inline-block w-2 h-2 rounded-sm bg-sky-500/30 mr-1 align-middle" />Your current rank</span>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-muted/60 text-muted-foreground text-[11px] uppercase tracking-wide">
              <th className="sticky left-0 bg-muted/60 px-3 py-2 text-left font-semibold w-12">Rank</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">XP req.</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Upgrade time</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Residents</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Stamina pump capacity</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Stamina cap</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Dungeons</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Map enemies</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Dungeon cap</th>
              <th className="px-3 py-2 text-right font-semibold whitespace-nowrap">Warehouse</th>
            </tr>
          </thead>
          <tbody>
            {RANKS.map((r, i) => {
              const prev = i > 0 ? RANKS[i - 1] : undefined;
              const isMe = !isNaN(highlighted) && r.rank === highlighted;
              const rowBase = isMe
                ? "bg-sky-500/10 dark:bg-sky-500/15"
                : i % 2 === 0 ? "bg-background" : "bg-muted/20";

              function Cell({ val, field, right = true }: { val: string | number; field: keyof RankEntry; right?: boolean }) {
                const ch = changed(r, prev, field);
                return (
                  <td className={`px-3 py-1.5 tabular-nums ${right ? "text-right" : "text-left"} ${ch ? "font-semibold text-amber-600 dark:text-amber-400" : "text-muted-foreground"}`}>
                    {val}
                  </td>
                );
              }

              return (
                <tr key={r.rank} className={`${rowBase} border-t border-border/40 hover:bg-muted/40 transition-colors`}>
                  <td className={`sticky left-0 px-3 py-1.5 font-bold text-foreground ${isMe ? "bg-sky-500/10 dark:bg-sky-500/15" : i % 2 === 0 ? "bg-background" : "bg-muted/20"}`}>
                    {r.rank}
                  </td>
                  <Cell val={fmtExp(r.exp)}           field="exp" />
                  <Cell val={fmtTime(r.upgradeTime)}  field="upgradeTime" />
                  <Cell val={r.maxResidents}           field="maxResidents" />
                  <Cell val={r.staminaPump}            field="staminaPump" />
                  <Cell val={r.staminaCap}             field="staminaCap" />
                  <Cell val={r.maxDungeons}            field="maxDungeons" />
                  <Cell val={r.maxEnemies}             field="maxEnemies" />
                  <Cell val={r.maxDungeonCap}          field="maxDungeonCap" />
                  <Cell val={r.warehouseCap}           field="warehouseCap" />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

    </div>
  );
}
