import { useState, useMemo, useRef, useCallback, useEffect } from "react";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { STAT_CANONICAL, STAT_COLUMNS, STAT_KEYS } from "@/game-data/stat-parameter-ids";
import { getStatIcon, STAT_ICON_KEY } from "@/lib/stat-icons";
import {
  battleSetupFromLoadouts,
  writeLoadoutHandoff,
  type SavedLoadoutPet,
  type SharedLoadoutData,
} from "@/lib/battle-legality";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "wouter";
import {
  Plus, Trash2, Loader2, Camera,
  ChevronDown, ChevronRight, Package, X, Check, Pencil,
  Download, Copy, Info, RotateCcw, Crown, Sword, Shield, Gem, ArrowUp, ArrowDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SearchableSelect } from "@/components/searchable-select";
import { PageHeader } from "@/components/ka/page-header";
import { ToneBadge } from "@/components/ka/badges";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { toPng } from "html-to-image";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { apiUrl } from "@/lib/api";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { planGear, type GoalMode, type StatGoal } from "@/lib/loadout-goal-planner";
import { simulateBatch, simulateDuel, type Combatant, type BattleResult, type BatchResult } from "@/lib/combat-simulator";
import { getJobProfile } from "@/game-data/job-profile";
import {
  RESIDENT_STAT_ITEMS,
  residentStatItemBonuses,
  type ResidentStatItemCounts,
} from "@/game-data/resident-stat-items";
import { RESIDENT_STAT_ITEMS_KEY } from "@/lib/resident-valuable-settings";
import { canPickHumanEquipment, canPickHumanSkill, dropUnsupportedHumanEquipment } from "@/lib/battle-picker-rules";
import { KA_RANK_BADGE_CLASS } from "@/design-system/category-styles";
import { MONSTER_CATALOG, MONSTER_PARAMETER_IDS } from "@/lib/battle-setup";
import { draftStatRows, type BuilderSharedData } from "@/lib/battle-team-draft";
import { normalizePlayerProfile, type PlayerProfile } from "@/lib/synthetic-legal-model";
import { searchProfileGoalBuildPages, type ProfileGoalBuild, type ProfileGoalSearchCharacter, type ProfileGoalSearchInput, type ProfileGoalSearchPage } from "@/lib/profile-loadout-goal-planner";

// ─── Types ────────────────────────────────────────────────────────────────────

type WeaponValue = "can" | "weak" | "cannot";
type Skill = { name: string; studioLevel?: number; craftingIntelligence?: number; buyPrice?: number; sellPrice?: number; description?: string; weaponResistance?: string };
type JobStatEntry = { base: number; inc: number };
type Job = {
  generation: 1 | 2;
  ranks: Record<string, { stats: Record<string, JobStatEntry> }>;
  weaponEquip?: Partial<Record<string, WeaponValue>>;
  shield?: "can" | "cannot";
};
type EquipEntry = { name: string; level: number };
type Loadout = {
  id: string;
  name: string;
  jobName: string;
  rank: string;
  level?: number;          // legacy / fallback default level
  statLevels?: Record<string, number>; // per-stat levels (primary)
  equipment: EquipEntry[];
  skills: string[];
  /**
   * Declared native grow/awakening step count for this character (per unit). Undefined = not
   * declared; the converter then reports an explicit unknown instead of inventing a cap.
   */
  awakening?: number;
  /** Declared invocation levels parallel to `skills`: native index 0/1/2. Absent = native default 1. */
  skillInvocations?: number[];
  /**
   * The resident's declared household pets (absent = not a house owner; `[]` = house owner with no
   * pets). Pet placement stays with the backend; the page never positions them.
   */
  householdPets?: SavedLoadoutPet[];
  // Resident-wide "Water of ..." valuables used (player-wide bonus, stored per
  // loadout so the totals stay self-contained). Counts keyed by item key.
  residentStatItems?: ResidentStatItemCounts;
  /** Profile import carries an immutable snapshot instead of following the page-wide setting. */
  residentStatItemsSource?: "profile";
  // Stat-goal planner: targets plus the constraints the player set on the last
  // suggestion (items they do not own, owned levels that cannot change).
  goalPlanner?: {
    goals?: Record<string, StatGoal>;
    unavailable?: string[];
    ownedLevels?: Record<string, number>;
  };
};
type BoxSetupKind = "kairo" | "wairo";
type BoxStatRuleMode = "level" | "value";
type BoxStatRule = { mode: BoxStatRuleMode; min?: number; max?: number };
type BoxSkillRule = { id: string; name: string; alternatives: string[] };
type BoxUnitRule = {
  id: string;
  label: string;
  anyJob: boolean;
  jobOptions: string[];
  recommendedJobs: string;
  stats: Record<string, BoxStatRule | undefined>;
  skillRules: BoxSkillRule[];
  recommendedGear: Record<string, string[] | undefined>;
  notes: string;
};
type BoxPetRule = { minLevel?: number; maxLevel?: number };
type BoxGridCell = { id: string; rule?: BoxUnitRule; assignedToPet?: boolean; assignedToFiller?: boolean; petRule?: BoxPetRule };
type BoxSetup = {
  id: BoxSetupKind;
  title: string;
  notes: string;
  rows: number;
  cells: BoxGridCell[];
  updatedAt?: number;
  publishedAt?: number;
  publishedBy?: string;
};
type BoxAttempt = Record<string, string | string[]>;
type SharedData = {
  jobs?: Record<string, Job>;
  skills?: Record<string, Skill>;
  overrides?: Record<string, Record<string, { base?: number; inc?: number }>>;
  slotAssignments?: Record<string, string>;
  equipIcons?: Record<string, string>;
  statIcons?: Record<string, string>;
  weaponTypes?: Record<string, string>;
  weaponCategories?: string[];
  pairs?: Array<{ id: string; jobA: string; jobB: string; children?: string[]; affinity?: string; affinityNum?: number }>;
  loadouts?: Loadout[];
  loadoutsUpdatedAt?: number | null;
  loadoutBoxSetups?: BoxSetup[];
  loadoutBoxSetupsUpdatedAt?: number | null;
};
type BoxSetupShare = { id: string; setup: BoxSetup; createdAt: number; updatedAt: number };

// ─── Constants ────────────────────────────────────────────────────────────────

const STAT_LABEL: Record<string, string> = {
  hp:"HP", mp:"MP", vig:"Vig", atk:"Atk", def:"Def",
  spd:"Spd", lck:"Lck", int:"Int", dex:"Dex", gth:"Gth", mov:"Mov", hrt:"Hrt",
};
const STAT_FULL: Record<string, string> = { ...STAT_ICON_KEY };
function StatLabel({ stat, icons, full = false, iconClassName = "h-3.5 w-3.5" }: { stat: string; icons?: Record<string, string>; full?: boolean; iconClassName?: string }) {
  const label = full ? (STAT_FULL[stat] ?? stat) : (STAT_LABEL[stat] ?? stat);
  const icon = getStatIcon(icons, stat);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      {icon && <img src={icon} alt="" className={`${iconClassName} shrink-0 object-contain`} />}
      <span className="truncate">{label}</span>
    </span>
  );
}
const EQUIP_SLOTS = [
  { slot: "Head",      Icon: Crown   },
  { slot: "Weapon",    Icon: Sword   },
  { slot: "Shield",    Icon: Shield  },
  { slot: "Armor",     Icon: Package },
  { slot: "Accessory", Icon: Gem     },
] as const;
type EquipSlot = typeof EQUIP_SLOTS[number]["slot"];

const RANK_COLORS: Record<string, string> = {
  ...KA_RANK_BADGE_CLASS,
};

function LoadoutMiniSummary({
  loadout,
  data,
  onRemove,
}: {
  loadout: Loadout;
  data: SharedData;
  onRemove?: () => void;
}) {
  const stats = calcStats(loadout, data);
  const topStats = STAT_KEYS.filter((key) => stats[key]).slice(0, 5);
  const equipment = loadout.equipment.slice(0, 5);

  return (
    <span className="block rounded-md border border-border/70 bg-background/75 px-2 py-1.5 text-left">
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-xs font-semibold text-foreground">{loadout.name || "Unnamed Loadout"}</span>
          <span className="block truncate text-[10px] text-primary">{loadout.jobName || "No job"}{loadout.rank ? ` - Rank ${loadout.rank}` : ""}</span>
        </span>
        {onRemove && (
          <span
            role="button"
            tabIndex={0}
            onClick={(event) => {
              event.stopPropagation();
              onRemove();
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              event.stopPropagation();
              onRemove();
            }}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-destructive"
            aria-label={`Remove ${loadout.name || "loadout"} from this slot`}
          >
            <X className="h-3 w-3" />
          </span>
        )}
      </span>
      {topStats.length > 0 && (
        <span className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
          {topStats.map((stat) => (
            <span key={stat} className="inline-flex items-center gap-1 text-[10px] tabular-nums text-muted-foreground">
              <StatLabel stat={stat} icons={data.statIcons} iconClassName="h-3.5 w-3.5" />
              <strong className="text-xs leading-none text-foreground">{stats[stat].toLocaleString()}</strong>
            </span>
          ))}
        </span>
      )}
      {equipment.length > 0 && (
        <span className="mt-1.5 flex flex-wrap gap-1">
          {equipment.map((item) => {
            const icon = getPreferredEquipmentIcon(data.equipIcons, item.name);
            return (
              <span key={`${item.name}-${item.level}`} className="flex h-9 w-9 items-center justify-center rounded border border-border/60 bg-muted/30">
                {icon ? <img src={icon} alt={item.name} title={`${item.name} Lv${item.level}`} className="h-8 w-8 object-contain" /> : <span className="text-muted-foreground">?</span>}
              </span>
            );
          })}
        </span>
      )}
    </span>
  );
}

function generateId() { return Math.random().toString(36).slice(2, 9); }

function statAtLevel(base: number, inc: number, level: number): number {
  return Math.round(base + (level - 1) * inc);
}

function closestStatLevel(base: number, inc: number, target: number): number {
  let closest = 1;
  let distance = Math.abs(statAtLevel(base, inc, closest) - target);
  for (let level = 2; level <= 999; level++) {
    const nextDistance = Math.abs(statAtLevel(base, inc, level) - target);
    if (nextDistance < distance) {
      closest = level;
      distance = nextDistance;
    }
  }
  return closest;
}

function commitOnEnter(e: React.KeyboardEvent<HTMLInputElement>, commit: () => void) {
  if (e.key === "Enter") {
    commit();
    e.currentTarget.blur();
  }
}

/**
 * Compact numeric field used for the small inline values on this page: no
 * spinner column, right-aligned tabular digits, selects its content on focus,
 * commits on blur/Enter and reverts on Escape.
 */
function CompactNumberInput({
  value,
  min = 0,
  max,
  ariaLabel,
  onValueChange,
  className = "",
}: {
  value: number;
  min?: number;
  max?: number;
  ariaLabel?: string;
  onValueChange: (value: number) => void;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const resolve = (raw: string) => {
    const cleaned = raw.replace(/[^\d]/g, "");
    const parsed = cleaned === "" ? min : Number.parseInt(cleaned, 10);
    const upper = max ?? Number.POSITIVE_INFINITY;
    return Math.min(upper, Math.max(min, Number.isFinite(parsed) ? parsed : min));
  };
  return (
    <Input
      type="text"
      inputMode="numeric"
      aria-label={ariaLabel}
      value={draft ?? String(value)}
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setDraft(event.target.value.replace(/[^\d]/g, ""))}
      onBlur={(event) => {
        onValueChange(resolve(event.target.value));
        setDraft(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          onValueChange(resolve(event.currentTarget.value));
          setDraft(null);
          event.currentTarget.blur();
        }
        if (event.key === "Escape") {
          setDraft(null);
          event.currentTarget.blur();
        }
      }}
      className={`min-h-10 rounded-md px-2 text-right text-xs font-semibold tabular-nums ${className}`}
    />
  );
}

function getPreferredEquipmentIcon(icons: Record<string, string> | undefined, name: string | undefined | null) {
  return getEquipmentIcon(null, name) ?? getEquipmentIcon(icons, name);
}

function residentItemsSome(counts: ResidentStatItemCounts | undefined): boolean {
  return RESIDENT_STAT_ITEMS.some((item) => (counts?.[item.key] ?? 0) > 0);
}

function residentItemsEqual(a: ResidentStatItemCounts | undefined, b: ResidentStatItemCounts | undefined): boolean {
  return RESIDENT_STAT_ITEMS.every((item) => (a?.[item.key] ?? 0) === (b?.[item.key] ?? 0));
}

const COLLAPSED_ICON_TRIM_CACHE = new Map<string, string>();

function CollapsedEquipmentIcon({ src, alt }: { src: string; alt: string }) {
  const [trimmedSrc, setTrimmedSrc] = useState<string>(src);

  useEffect(() => {
    if (!src) {
      setTrimmedSrc(src);
      return;
    }

    const cached = COLLAPSED_ICON_TRIM_CACHE.get(src);
    if (cached) {
      setTrimmedSrc(cached);
      return;
    }

    let cancelled = false;
    const image = new Image();
    image.decoding = "async";

    image.onload = () => {
      if (cancelled) return;
      const w = image.naturalWidth;
      const h = image.naturalHeight;
      if (!w || !h) {
        setTrimmedSrc(src);
        return;
      }

      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        setTrimmedSrc(src);
        return;
      }

      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, w, h).data;

      let minX = w;
      let minY = h;
      let maxX = -1;
      let maxY = -1;

      for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
          const alpha = data[(y * w + x) * 4 + 3];
          if (alpha === 0) continue;
          if (x < minX) minX = x;
          if (y < minY) minY = y;
          if (x > maxX) maxX = x;
          if (y > maxY) maxY = y;
        }
      }

      if (maxX < minX || maxY < minY) {
        COLLAPSED_ICON_TRIM_CACHE.set(src, src);
        setTrimmedSrc(src);
        return;
      }

      const trimPadding = 1;
      const sx = Math.max(0, minX - trimPadding);
      const sy = Math.max(0, minY - trimPadding);
      const ex = Math.min(w - 1, maxX + trimPadding);
      const ey = Math.min(h - 1, maxY + trimPadding);
      const tw = ex - sx + 1;
      const th = ey - sy + 1;

      const out = document.createElement("canvas");
      out.width = tw;
      out.height = th;
      const outCtx = out.getContext("2d");
      if (!outCtx) {
        setTrimmedSrc(src);
        return;
      }

      outCtx.drawImage(image, sx, sy, tw, th, 0, 0, tw, th);
      const url = out.toDataURL("image/png");
      COLLAPSED_ICON_TRIM_CACHE.set(src, url);
      if (!cancelled) setTrimmedSrc(url);
    };

    image.onerror = () => {
      if (!cancelled) setTrimmedSrc(src);
    };

    image.src = src;

    return () => {
      cancelled = true;
    };
  }, [src]);

  return (
    <img
      src={trimmedSrc}
      alt={alt}
      className="h-[80px] w-[80px] shrink-0 object-contain"
      style={{ imageRendering: "pixelated" }}
    />
  );
}

// ─── Weapon proficiency helpers ───────────────────────────────────────────────

function getWeaponProficiency(
  job: Job | undefined,
  equipName: string,
  slot: string,
  weaponTypes: Record<string, string> | undefined
): { weaponType: string | null; prof: WeaponValue | null } {
  if (!job || !equipName) return { weaponType: null, prof: null };
  if (slot === "Shield") {
    const prof = job.weaponEquip?.["Shield"] ?? (job.shield === "can" ? "can" : job.shield === "cannot" ? "cannot" : null);
    return { weaponType: "Shield", prof };
  }
  if (slot !== "Weapon") return { weaponType: null, prof: null };
  const wt = weaponTypes?.[equipName] ?? null;
  if (!wt || wt === "Tool") return { weaponType: wt, prof: null };
  const prof = job.weaponEquip?.[wt] ?? null;
  return { weaponType: wt, prof };
}

function findResistanceSkill(
  skills: Record<string, Skill> | undefined,
  weaponType: string | null
): Skill | null {
  if (!skills || !weaponType) return null;
  return Object.values(skills).find((s) => s.weaponResistance === weaponType) ?? null;
}

type EquipRuleState = {
  slot: string | null;
  weaponType: string | null;
  prof: WeaponValue | null;
  resistanceSkill: Skill | null;
  hasResistanceSkillEquipped: boolean;
  appliesPenalty: boolean;
  blocked: boolean;
};

function getEquipRuleState(loadout: Loadout, data: SharedData, equipName: string): EquipRuleState {
  const slot = data.slotAssignments?.[equipName] ?? null;
  const profile = getJobProfile(data, loadout.jobName);
  const weaponType = slot === "Shield" ? "Shield" : data.weaponTypes?.[equipName] ?? null;
  const prof =
    slot === "Shield"
      ? profile?.equipmentAccess.shield ?? null
      : slot === "Weapon" && weaponType && weaponType !== "Tool"
        ? profile?.equipmentAccess.weapons[weaponType] ?? null
        : null;
  const resistanceSkill = findResistanceSkill(data.skills, weaponType);
  const hasResistanceSkillEquipped = !!resistanceSkill && loadout.skills.includes(resistanceSkill.name);
  // Native `JobData.GetAffinity 0x16208d0` lifts BOTH the rejected (-1) and the weak (0) verdict to
  // 1 when a possessed type-48 EQUIP_MASTER skill's value equals the equipment type, before
  // `EquipData.CanEquip 0x1620e14` sees the value, so the declared resistance skill unblocks the
  // item as well as removing the halving. Same rule as `battle-legality` (equipment-job-admission).
  const blocked = prof === "cannot" && !hasResistanceSkillEquipped;
  const appliesPenalty = prof === "weak" && !hasResistanceSkillEquipped;
  return { slot, weaponType, prof, resistanceSkill, hasResistanceSkillEquipped, appliesPenalty, blocked };
}

// ─── Hooks ────────────────────────────────────────────────────────────────────

function useSharedData() {
  return useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<SharedData>(apiUrl("/shared")),
    staleTime: 15000,
  });
}


function useLoadouts(sharedData: SharedData | undefined) {
  const [loadouts, setLoadouts] = useLocalFeature<Loadout[]>("ka_loadouts", []);
  // Ref that always mirrors loadouts — used inside effects to avoid stale closures
  const loadoutsRef = useRef(loadouts);
  useEffect(() => { loadoutsRef.current = loadouts; }, [loadouts]);
  // Sync guards (same pattern as the pairs sync in marriage-matcher)
  const loadoutsHydratedRef = useRef(false);
  const skipNextLoadoutsEchoRef = useRef(false);
  const loadoutsPutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hydration: on first API data load, pull loadouts from server.
  // Rule: if loadoutsUpdatedAt is non-null the server has been explicitly saved to
  // and is authoritative — even if loadouts is empty (means user deleted everything).
  // Only push local state when the server has NEVER been initialized (loadoutsUpdatedAt === null).
  useEffect(() => {
    if (loadoutsHydratedRef.current) return;
    if (!sharedData) return; // still loading
    loadoutsHydratedRef.current = true;
    if (sharedData.loadoutsUpdatedAt != null) {
      // Server has been written before — always take its state, even if empty
      skipNextLoadoutsEchoRef.current = true;
      setLoadouts(sharedData.loadouts ?? []);
    } else if (loadoutsRef.current.length > 0) {
      // Server has never been synced — push local state as the initial seed
      fetch(apiUrl("/loadouts"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: loadoutsRef.current }),
      }).catch(() => {});
    }
  }, [sharedData, setLoadouts]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced PUT: push loadouts to server on every user change (after hydration)
  useEffect(() => {
    if (!loadoutsHydratedRef.current) return;
    if (skipNextLoadoutsEchoRef.current) {
      skipNextLoadoutsEchoRef.current = false;
      return;
    }
    if (loadoutsPutTimerRef.current) clearTimeout(loadoutsPutTimerRef.current);
    loadoutsPutTimerRef.current = setTimeout(() => {
      fetch(apiUrl("/loadouts"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: loadouts }),
      }).catch(() => {});
    }, 500);
    return () => {
      if (loadoutsPutTimerRef.current) clearTimeout(loadoutsPutTimerRef.current);
    };
  }, [loadouts]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback((next: Loadout[]) => {
    setLoadouts(next);
  }, [setLoadouts]);
  return { loadouts, save };
}

function usePrivateLoadouts() {
  const [loadouts, setLoadouts] = useLocalFeature<Loadout[]>("ka_loadouts", []);

  const save = useCallback((next: Loadout[]) => {
    setLoadouts(next);
  }, [setLoadouts]);

  return { loadouts, save };
}

function createEmptyBoxSetup(id: BoxSetupKind, title: string): BoxSetup {
  const rows = 2;
  return {
    id,
    title,
    notes: "",
    rows,
    cells: Array.from({ length: rows * 5 }, (_, index) => ({ id: `${id}-${index}` })),
  };
}

const DEFAULT_BOX_SETUPS: BoxSetup[] = [
  createEmptyBoxSetup("kairo", "Multi Box Kairo Setup"),
  createEmptyBoxSetup("wairo", "Multi Box Wairo Setup"),
];
const BOX_SETUPS_STORAGE_KEY = "ka_loadout_box_setups";

function normalizeBoxSetups(value: unknown): BoxSetup[] {
  const incoming = Array.isArray(value) ? value : [];
  return DEFAULT_BOX_SETUPS.map((fallback) => {
    const found = incoming.find((item) => item && typeof item === "object" && (item as Partial<BoxSetup>).id === fallback.id) as Partial<BoxSetup> | undefined;
    const rows = Math.max(1, Math.min(12, Number(found?.rows) || fallback.rows));
    const cells = Array.from({ length: rows * 5 }, (_, index) => {
      const existing = found?.cells?.[index];
      return existing && typeof existing === "object" ? {
        id: existing.id || `${fallback.id}-${index}`,
        rule: existing.rule,
        assignedToPet: existing.assignedToPet === true,
        assignedToFiller: existing.assignedToFiller === true,
        petRule: existing.petRule && typeof existing.petRule === "object" ? existing.petRule : undefined,
      } : { id: `${fallback.id}-${index}` };
    });
    return {
      ...fallback,
      ...found,
      title: fallback.title,
      rows,
      cells,
      notes: typeof found?.notes === "string" ? found.notes : "",
      publishedAt: typeof found?.publishedAt === "number" ? found.publishedAt : undefined,
      publishedBy: typeof found?.publishedBy === "string" ? found.publishedBy : undefined,
    };
  });
}

function useCommunityBoxSetups(sharedData: SharedData | undefined) {
  const [setups, setSetups] = useLocalFeature<BoxSetup[]>(BOX_SETUPS_STORAGE_KEY, DEFAULT_BOX_SETUPS);
  const hydratedRef = useRef(false);
  const hadLocalDraftRef = useRef(
    typeof window !== "undefined" && window.localStorage.getItem(BOX_SETUPS_STORAGE_KEY) !== null,
  );

  useEffect(() => {
    if (hydratedRef.current || !sharedData) return;
    hydratedRef.current = true;
    if (!hadLocalDraftRef.current) {
      setSetups(normalizeBoxSetups(sharedData.loadoutBoxSetups));
    }
  }, [sharedData, setSetups]);

  const save = useCallback((next: BoxSetup[]) => setSetups(normalizeBoxSetups(next)), [setSetups]);
  return { setups: normalizeBoxSetups(setups), save };
}

// ─── Stat Calculators ─────────────────────────────────────────────────────────

function getStatLevel(loadout: Loadout, k: string): number {
  return loadout.statLevels?.[k] ?? loadout.level ?? 1;
}

function normStat(raw: string): string {
  return STAT_CANONICAL[raw.toLowerCase()] ?? raw.toLowerCase();
}

function calcJobStats(loadout: Loadout, data: SharedData): Record<string, number> {
  return Object.fromEntries(draftStatRows(loadout, data as unknown as BuilderSharedData)
    .filter((row) => row.curveValue !== null)
    .map((row) => [row.stat, row.curveValue as number]));
}

function calcEquipStats(loadout: Loadout, data: SharedData): Record<string, number> {
  return Object.fromEntries(draftStatRows(loadout, data as unknown as BuilderSharedData)
    .filter((row) => row.equipmentValue !== 0)
    .map((row) => [row.stat, row.equipmentValue]));
}

function calcStats(loadout: Loadout, data: SharedData): Record<string, number> {
  return Object.fromEntries(draftStatRows(loadout, data as unknown as BuilderSharedData)
    .filter((row) => row.total !== null)
    .map((row) => [row.stat, row.total as number]));
}

type CombatSourceMode = "manual" | "loadout";

function formatNum(v: number | null | undefined) {
  return v == null ? "-" : v.toLocaleString();
}

function formatPct(part: number, total: number) {
  if (total <= 0) return "0.0%";
  return `${((part * 100) / total).toFixed(1)}%`;
}

function ordinalWord(value: number) {
  const words = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"];
  return words[value - 1] ?? `${value}th`;
}

function attackOutcomeText(round: BattleResult["rounds"][number]) {
  if (!round.result.hit) return "missed";
  return `dealt ${formatNum(round.result.damage)} damage`;
}

const COMBAT_SIDE_STYLE = {
  a: {
    row: "border-l-4 border-l-sky-400 bg-sky-50/25 dark:border-l-sky-500 dark:bg-sky-950/10",
    chip: "border-sky-300/70 bg-sky-50/30 text-sky-900 dark:border-sky-700 dark:bg-sky-950/15 dark:text-sky-100",
    label: "text-sky-800 dark:text-sky-200",
  },
  b: {
    row: "border-l-4 border-l-rose-300 bg-rose-50/20 dark:border-l-rose-500/80 dark:bg-rose-950/10",
    chip: "border-rose-300/60 bg-rose-50/25 text-rose-900 dark:border-rose-800/80 dark:bg-rose-950/15 dark:text-rose-100",
    label: "text-rose-700 dark:text-rose-200",
  },
} as const;

function baseManualCombatant(name: string): Combatant {
  return {
    name,
    maxHp: 1200,
    atk: 220,
    def: 110,
    spd: 120,
    dex: 110,
    lck: 90,
    int: 80,
    weaponAdvantage: false,
    action: { type: "normal", value: 0 },
  };
}

function loadoutToCombatant(loadout: Loadout, data: SharedData): Combatant {
  const stats = calcStats(loadout, data);
  return {
    name: loadout.name || "Loadout",
    maxHp: Math.max(1, stats.hp ?? 1),
    atk: stats.atk ?? 0,
    def: stats.def ?? 0,
    spd: stats.spd ?? 0,
    dex: stats.dex ?? 0,
    lck: stats.lck ?? 0,
    int: stats.int ?? 0,
    weaponAdvantage: false,
    action: { type: "normal", value: 0 },
  };
}

function LoadoutCombatTool({ loadouts, data }: { loadouts: Loadout[]; data: SharedData }) {
  const [, navigate] = useLocation();
  const [aName, setAName] = useState("Attacker A");
  const [bName, setBName] = useState("Attacker B");
  const [aMode, setAMode] = useState<CombatSourceMode>("manual");
  const [bMode, setBMode] = useState<CombatSourceMode>("manual");
  const [aManual, setAManual] = useState<Combatant>(() => baseManualCombatant("Attacker A"));
  const [bManual, setBManual] = useState<Combatant>(() => baseManualCombatant("Attacker B"));
  const [aLoadoutId, setALoadoutId] = useState<string>(loadouts[0]?.id ?? "");
  const [bLoadoutId, setBLoadoutId] = useState<string>(loadouts[1]?.id ?? loadouts[0]?.id ?? "");
  const [batchCount, setBatchCount] = useState(1000);
  const [battle, setBattle] = useState<BattleResult | null>(null);
  const [batch, setBatch] = useState<BatchResult | null>(null);

  const aImported = useMemo(() => loadouts.find((l) => l.id === aLoadoutId) ?? null, [loadouts, aLoadoutId]);
  const bImported = useMemo(() => loadouts.find((l) => l.id === bLoadoutId) ?? null, [loadouts, bLoadoutId]);

  const resolvedA = useMemo(() => {
    if (aMode === "manual") {
      return { ...aManual, name: aName.trim() || aManual.name || "Attacker A" };
    }
    if (!aImported) return null;
    return { ...loadoutToCombatant(aImported, data), name: aName.trim() || aImported.name || "Attacker A" };
  }, [aMode, aManual, aImported, data, aName]);

  const resolvedB = useMemo(() => {
    if (bMode === "manual") {
      return { ...bManual, name: bName.trim() || bManual.name || "Attacker B" };
    }
    if (!bImported) return null;
    return { ...loadoutToCombatant(bImported, data), name: bName.trim() || bImported.name || "Attacker B" };
  }, [bMode, bManual, bImported, data, bName]);

  const run = () => {
    if (!resolvedA || !resolvedB) return;
    setBattle(simulateDuel(resolvedA, resolvedB));
    setBatch(simulateBatch(resolvedA, resolvedB, Math.max(1, batchCount)));
  };

  /**
   * Battle Setup transfer. Only the saved loadouts selected here are sent, and the setup page
   * re-runs the canonical conversion, so a manual (hand-typed) combatant is never presented as a
   * saved loadout.
   */
  const battleSetupLoadouts = useMemo(() => {
    const picked = [
      aMode === "loadout" ? aImported : null,
      bMode === "loadout" ? bImported : null,
    ].filter((entry): entry is Loadout => Boolean(entry));
    return picked.filter((entry, index) => picked.findIndex((other) => other.id === entry.id) === index);
  }, [aMode, bMode, aImported, bImported]);

  const battleSetupPreview = useMemo(() => {
    if (battleSetupLoadouts.length === 0) return null;
    return battleSetupFromLoadouts(battleSetupLoadouts, data as unknown as SharedLoadoutData, {
      encounterId: 19,
    });
  }, [battleSetupLoadouts, data]);

  const sendToBattleSetup = () => {
    if (battleSetupLoadouts.length === 0) return;
    writeLoadoutHandoff(battleSetupLoadouts.map((entry) => entry.id));
    navigate("/battle-setup");
  };

  const battleSummary = useMemo(() => {
    if (!battle || !resolvedA || !resolvedB) return null;
    let aAttacks = 0;
    let bAttacks = 0;
    const rows = battle.rounds.map((round, index) => {
      const isA = round.attacker === resolvedA.name;
      const attackNumber = isA ? ++aAttacks : ++bAttacks;
      return {
        id: `${round.attacker}-${index}`,
        side: (isA ? "a" : "b") as keyof typeof COMBAT_SIDE_STYLE,
        label: `${round.attacker} ${ordinalWord(attackNumber)} attack`,
        text: `${attackOutcomeText(round)}. ${round.defender} HP after: ${formatNum(round.defenderHpAfter)}.`,
        crit: round.result.crit,
        meta: `${round.result.attackType}${round.result.note ? ` - ${round.result.note}` : ""}`,
      };
    });
    return { aAttacks, bAttacks, rows };
  }, [battle, resolvedA, resolvedB]);

  const renderManualEditor = (c: Combatant, setC: (next: Combatant) => void) => (
    <div className="grid grid-cols-2 gap-2">
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">HP</span>
        <ThemedNumberInput value={c.maxHp} min={1} onValueChange={(value) => setC({ ...c, maxHp: Math.max(1, value) })} className="h-8" ariaLabel="HP" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">ATK</span>
        <ThemedNumberInput value={c.atk} min={0} onValueChange={(value) => setC({ ...c, atk: value })} className="h-8" ariaLabel="ATK" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">DEF</span>
        <ThemedNumberInput value={c.def} min={0} onValueChange={(value) => setC({ ...c, def: value })} className="h-8" ariaLabel="DEF" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">SPD</span>
        <ThemedNumberInput value={c.spd} min={0} onValueChange={(value) => setC({ ...c, spd: value })} className="h-8" ariaLabel="SPD" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">DEX</span>
        <ThemedNumberInput value={c.dex} min={0} onValueChange={(value) => setC({ ...c, dex: value })} className="h-8" ariaLabel="DEX" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">LCK</span>
        <ThemedNumberInput value={c.lck} min={0} onValueChange={(value) => setC({ ...c, lck: value })} className="h-8" ariaLabel="LCK" />
      </label>
      <label className="space-y-1 text-xs">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">INT</span>
        <ThemedNumberInput value={c.int} min={0} onValueChange={(value) => setC({ ...c, int: value })} className="h-8" ariaLabel="INT" />
      </label>
      <label className="text-xs text-muted-foreground col-span-2 flex items-center gap-2">
        <input type="checkbox" checked={c.weaponAdvantage} onChange={(e) => setC({ ...c, weaponAdvantage: e.currentTarget.checked })} />
        Weapon advantage (x1.5)
      </label>
    </div>
  );

  const renderImportedPreview = (selectedId: string, setSelectedId: (id: string) => void, c: Combatant | null) => (
    <div className="space-y-2">
      <select
        value={selectedId}
        onChange={(e) => setSelectedId(e.target.value)}
        className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
      >
        <option value="">Select loadout...</option>
        {loadouts.map((lo) => <option key={lo.id} value={lo.id}>{lo.name || "Unnamed"}</option>)}
      </select>
      {c && (
        <div className="text-xs text-muted-foreground border border-border rounded-md p-2 bg-muted/20">
          HP {formatNum(c.maxHp)} · ATK {formatNum(c.atk)} · DEF {formatNum(c.def)} · SPD {formatNum(c.spd)} · DEX {formatNum(c.dex)} · LCK {formatNum(c.lck)} · INT {formatNum(c.int)}
        </div>
      )}
    </div>
  );

  return (
    <Card className="mt-6" data-duel-model-card>
      <CardHeader>
        <CardTitle className="text-base">Simplified duel estimate (not the recovered combat model)</CardTitle>
        <CardDescription data-duel-model-note>
          This panel runs the site's own simplified JavaScript model (single-target normal attacks
          from atk/def/spd/dex/lck/int). It is not the recovered combat simulator, it does not model
          skills, equipment, targeting or timing, and its numbers are not authoritative. Use Battle
          Setup (below) to run the recovered Python runner with a legal team.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <div className="text-sm font-semibold">Attacker A</div>
            <Input value={aName} onChange={(e) => setAName(e.target.value)} className="h-8 text-xs" placeholder="Display name" />
            <select value={aMode} onChange={(e) => setAMode(e.target.value as CombatSourceMode)} className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs">
              <option value="manual">Manual input</option>
              <option value="loadout">Import from loadouts</option>
            </select>
            {aMode === "manual" ? renderManualEditor(aManual, setAManual) : renderImportedPreview(aLoadoutId, setALoadoutId, resolvedA)}
          </div>

          <div className="space-y-2">
            <div className="text-sm font-semibold">Attacker B</div>
            <Input value={bName} onChange={(e) => setBName(e.target.value)} className="h-8 text-xs" placeholder="Display name" />
            <select value={bMode} onChange={(e) => setBMode(e.target.value as CombatSourceMode)} className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs">
              <option value="manual">Manual input</option>
              <option value="loadout">Import from loadouts</option>
            </select>
            {bMode === "manual" ? renderManualEditor(bManual, setBManual) : renderImportedPreview(bLoadoutId, setBLoadoutId, resolvedB)}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <ThemedNumberInput value={batchCount} min={1} onValueChange={(value) => setBatchCount(Math.max(1, value))} className="h-8 w-32" />
          <span className="text-xs text-muted-foreground">batch size</span>
          <Button size="sm" onClick={run} data-action="run-duel-estimate">
            Run simplified duel estimate
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={sendToBattleSetup}
            disabled={battleSetupLoadouts.length === 0 || Boolean(battleSetupPreview && !battleSetupPreview.setup)}
            data-action="send-to-battle-setup"
            data-loadout-count={battleSetupLoadouts.length}
            data-legal={battleSetupPreview ? (battleSetupPreview.setup ? "true" : "false") : "none"}
            title={
              battleSetupLoadouts.length === 0
                ? "Select at least one saved loadout as Attacker A or B first."
                : battleSetupPreview && !battleSetupPreview.setup
                  ? "This selection has legality errors; open Battle Setup to see them."
                  : "Send the selected saved loadouts to Battle Setup as the player team."
            }
          >
            Send to Battle Setup
          </Button>
          <Button
            size="sm"
            className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-600/90"
            onClick={() => {
              if (battleSetupLoadouts.length === 0) return;
              writeLoadoutHandoff(battleSetupLoadouts.map((entry) => entry.id));
              navigate("/battle");
            }}
            disabled={battleSetupLoadouts.length === 0 || Boolean(battleSetupPreview && !battleSetupPreview.setup)}
            data-action="send-to-player-battle"
            title={
              battleSetupLoadouts.length === 0
                ? "Select at least one saved loadout as Attacker A or B first."
                : battleSetupPreview && !battleSetupPreview.setup
                  ? "This selection has legality errors; open Player Battle to review them."
                  : "Open the Player Battle page with these loadouts selected."
            }
          >
            <Sword className="w-3.5 h-3.5" />
            Send to Player Battle
          </Button>
        </div>

        {battleSetupPreview ? (
          <div className="rounded-md border border-border bg-transparent px-3 py-2 text-xs" data-duel-legality-preview data-legal={battleSetupPreview.setup ? "true" : "false"}>
            Battle Setup transfer for {battleSetupLoadouts.length} selected loadout(s):{" "}
            <strong>{battleSetupPreview.issues.filter((issue) => issue.category === "ERROR").length}</strong> error(s),{" "}
            <strong>{battleSetupPreview.issues.filter((issue) => issue.category === "WARNING").length}</strong> warning(s),{" "}
            <strong>{battleSetupPreview.issues.filter((issue) => issue.category === "UNKNOWN_NATIVE_RULE").length}</strong> unresolved native rule(s).
            {battleSetupPreview.setup ? "" : " The setup page will show the exact ERROR issues instead of guessing."}
          </div>
        ) : null}

        {(battle || batch) && (
          <div className="space-y-3 text-sm">
            {batch && (
              <div className="rounded-md border border-border bg-transparent px-3 py-2 text-xs">
                Simplified-model batch outcomes: <strong>{resolvedA?.name ?? "A"}</strong> ahead in {formatNum(batch.leftWins)} ({formatPct(batch.leftWins, batch.total)}), <strong>{resolvedB?.name ?? "B"}</strong> ahead in {formatNum(batch.rightWins)} ({formatPct(batch.rightWins, batch.total)}), draws {formatNum(batch.draws)} ({formatPct(batch.draws, batch.total)}) out of {formatNum(batch.total)}.
              </div>
            )}
            {battle && (
              <div className="rounded-md border border-border bg-background">
                <div className="space-y-3 border-b border-border bg-transparent p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-sm font-semibold">Duel summary</div>
                      <div className="text-xs text-muted-foreground">
                        {battle.winner ? `${battle.winner} standing` : "Draw"} after {battle.rounds.length} strikes
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs">
                      <span className="rounded-md border border-border bg-transparent px-2 py-1">{resolvedA?.name ?? "A"}: {formatNum(battle.leftHp)} HP left</span>
                      <span className="rounded-md border border-border bg-transparent px-2 py-1">{resolvedB?.name ?? "B"}: {formatNum(battle.rightHp)} HP left</span>
                    </div>
                  </div>
                  {battleSummary && (
                    <div className="grid gap-2 text-xs sm:grid-cols-2">
                      <div className={`rounded-md border px-2 py-1 ${COMBAT_SIDE_STYLE.a.chip}`}>
                        <strong>{resolvedA?.name ?? "A"}</strong>: {battleSummary.aAttacks} attacks
                      </div>
                      <div className={`rounded-md border px-2 py-1 ${COMBAT_SIDE_STYLE.b.chip}`}>
                        <strong>{resolvedB?.name ?? "B"}</strong>: {battleSummary.bAttacks} attacks
                      </div>
                    </div>
                  )}
                </div>
                {battleSummary && (
                  <div className="divide-y divide-border">
                    {battleSummary.rows.map((row) => (
                      <div key={row.id} className={`grid gap-1 px-3 py-2 text-xs sm:grid-cols-[180px_1fr] sm:items-start ${COMBAT_SIDE_STYLE[row.side].row}`}>
                        <div className={`font-semibold ${COMBAT_SIDE_STYLE[row.side].label}`}>{row.label}</div>
                        <div>
                          <div className="flex flex-wrap items-center gap-2 text-foreground">
                            <span>{row.text}</span>
                            {row.crit ? <span className="font-semibold text-orange-500">💥 Crit</span> : null}
                          </div>
                          <div className="text-muted-foreground">{row.meta}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Screenshot Card (portal-rendered) ───────────────────────────────────────

function createBoxUnitRule(): BoxUnitRule {
  return {
    id: generateId(),
    label: "Required unit",
    anyJob: false,
    jobOptions: [],
    recommendedJobs: "",
    stats: {},
    skillRules: [],
    recommendedGear: {},
    notes: "",
  };
}

function parseList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function formatList(value: string[]) {
  return value.join(", ");
}

function addUnique(value: string[], next: string) {
  if (!next || value.includes(next)) return value;
  return [...value, next];
}

function removeValue(value: string[], target: string) {
  return value.filter((item) => item !== target);
}

function statRulePasses(loadout: Loadout, stats: Record<string, number>, stat: string, rule: BoxStatRule | undefined) {
  if (!rule) return true;
  const value = rule.mode === "level" ? getStatLevel(loadout, stat) : (stats[stat] ?? 0);
  if (rule.min != null && value < rule.min) return false;
  if (rule.max != null && value > rule.max) return false;
  return true;
}

function unitMatchesRule(loadout: Loadout | null, rule: BoxUnitRule | undefined, data: SharedData) {
  if (!rule) return "empty";
  if (!loadout) return "missing";
  const stats = calcStats(loadout, data);
  if (!rule.anyJob && rule.jobOptions.length > 0 && !rule.jobOptions.includes(loadout.jobName)) return "fail";
  for (const stat of STAT_KEYS) {
    if (!statRulePasses(loadout, stats, stat, rule.stats[stat])) return "fail";
  }
  for (const skillRule of rule.skillRules) {
    const allowed = [skillRule.name, ...skillRule.alternatives].filter(Boolean);
    if (allowed.length > 0 && !allowed.some((skill) => loadout.skills.includes(skill))) return "fail";
  }
  return "pass";
}

function getAttemptIds(attempt: BoxAttempt, cellId: string): string[] {
  const raw = attempt[cellId];
  if (Array.isArray(raw)) return raw.filter(Boolean);
  return raw ? [raw] : [];
}

function setAttemptIds(attempt: BoxAttempt, cellId: string, ids: string[]): BoxAttempt {
  const next = { ...attempt };
  const unique = [...new Set(ids.filter(Boolean))];
  if (unique.length === 0) delete next[cellId];
  else next[cellId] = unique;
  return next;
}

function addAttemptId(attempt: BoxAttempt, cellId: string, id: string): BoxAttempt {
  return setAttemptIds(attempt, cellId, [...getAttemptIds(attempt, cellId), id]);
}

function removeAttemptId(attempt: BoxAttempt, cellId: string, id: string): BoxAttempt {
  return setAttemptIds(attempt, cellId, getAttemptIds(attempt, cellId).filter((item) => item !== id));
}

function BoxPetRuleEditor({ rule, onChange }: { rule: BoxPetRule | undefined; onChange: (next: BoxPetRule) => void }) {
  const current = rule ?? {};
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="space-y-1 text-xs">
        <span className="font-medium text-muted-foreground">Minimum pet level</span>
        <ThemedNumberInput
          value={current.minLevel ?? 0}
          min={0}
          max={999}
          onValueChange={(value) => onChange({ ...current, minLevel: value > 0 ? value : undefined })}
          className="h-8"
          ariaLabel="Minimum pet level"
        />
      </label>
      <label className="space-y-1 text-xs">
        <span className="font-medium text-muted-foreground">Maximum pet level</span>
        <ThemedNumberInput
          value={current.maxLevel ?? 0}
          min={0}
          max={999}
          onValueChange={(value) => onChange({ ...current, maxLevel: value > 0 ? value : undefined })}
          className="h-8"
          ariaLabel="Maximum pet level"
        />
      </label>
    </div>
  );
}

function BoxUnitRuleEditor({ rule, data, onChange }: { rule: BoxUnitRule; data: SharedData; onChange: (next: BoxUnitRule) => void }) {
  const jobs = Object.keys(data.jobs ?? {}).sort();
  const skills = Object.keys(data.skills ?? {}).sort();
  const equipBySlot = useMemo(() => {
    const slotMap = data.slotAssignments ?? {};
    const out: Record<string, string[]> = {};
    for (const { slot } of EQUIP_SLOTS) {
      out[slot] = Object.entries(slotMap).filter(([, itemSlot]) => itemSlot === slot).map(([name]) => name).sort();
    }
    return out;
  }, [data.slotAssignments]);
  const setStatRule = (stat: string, next: BoxStatRule | undefined) => onChange({ ...rule, stats: { ...rule.stats, [stat]: next } });

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs">
          <span className="font-medium text-muted-foreground">Slot label</span>
          <Input value={rule.label} onChange={(e) => onChange({ ...rule, label: e.target.value })} className="h-8 text-xs" />
        </label>
        <label className="flex items-center gap-2 pt-5 text-xs text-muted-foreground">
          <input type="checkbox" checked={rule.anyJob} onChange={(e) => onChange({ ...rule, anyJob: e.currentTarget.checked })} />
          Any job can satisfy this position
        </label>
      </div>

      {!rule.anyJob && (
        <div className="space-y-2 text-xs">
          <span className="font-medium text-muted-foreground">Allowed job requirement</span>
          <SearchableSelect
            value=""
            clearOnSelect
            onChange={(value) => onChange({ ...rule, jobOptions: addUnique(rule.jobOptions, value) })}
            options={jobs.filter((job) => !rule.jobOptions.includes(job)).map((job) => ({ value: job, label: job }))}
            placeholder="Add job that can fill this slot"
            triggerClassName="h-8 text-xs"
          />
          <div className="flex min-h-6 flex-wrap gap-1">
            {rule.jobOptions.map((job) => (
              <ToneBadge key={job} category="job" className="gap-1 px-2 py-0.5 text-xs">
                {job}
                <button onClick={() => onChange({ ...rule, jobOptions: removeValue(rule.jobOptions, job) })} className="hover:text-destructive">
                  <X className="h-2.5 w-2.5" />
                </button>
              </ToneBadge>
            ))}
            {rule.jobOptions.length === 0 && <span className="text-xs text-muted-foreground/60">No job restriction yet.</span>}
          </div>
        </div>
      )}

      <label className="block space-y-1 text-xs">
        <span className="font-medium text-muted-foreground">Recommended jobs</span>
        <Input value={rule.recommendedJobs} onChange={(e) => onChange({ ...rule, recommendedJobs: e.target.value })} placeholder="Optional notes about rank, stat scaling, or role" className="h-8 text-xs" />
      </label>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">Must-match stat rules</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {STAT_KEYS.map((stat) => {
            const statRule = rule.stats[stat];
            return (
              <div key={stat} className={`rounded-md border border-border bg-muted/15 p-2 ${statRule ? 'ring-2 ring-primary/60' : ''}`}>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold">{STAT_LABEL[stat]}</span>
                  <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    <input type="checkbox" checked={!!statRule} onChange={(e) => setStatRule(stat, e.currentTarget.checked ? { mode: "value" } : undefined)} />
                    Rule
                  </label>
                </div>
                {statRule && (
                  <div className="grid grid-cols-[88px_1fr_1fr] gap-1.5 items-center">
                    <select value={statRule.mode} onChange={(e) => setStatRule(stat, { ...statRule, mode: e.target.value as BoxStatRuleMode })} className="h-7 rounded-md border border-input bg-background px-1 text-[11px]">
                      <option value="value">Value</option>
                      <option value="level">Level</option>
                    </select>
                    <div className="flex flex-col">
                      <span className="text-[10px] text-muted-foreground pl-1">Min</span>
                      <ThemedNumberInput value={statRule.min ?? 0} min={0} onValueChange={(value) => setStatRule(stat, { ...statRule, min: value || undefined })} className="h-7 text-xs" ariaLabel={`${stat} min`} />
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[10px] text-muted-foreground pl-1">Max</span>
                      <ThemedNumberInput value={statRule.max ?? 0} min={0} onValueChange={(value) => setStatRule(stat, { ...statRule, max: value || undefined })} className="h-7 text-xs" ariaLabel={`${stat} max`} />
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Required skill order</p>
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onChange({ ...rule, skillRules: [...rule.skillRules, { id: generateId(), name: "", alternatives: [] }] })}>
            <Plus className="h-3 w-3" />Skill
          </Button>
        </div>
        <div className="space-y-2">
          {rule.skillRules.map((skillRule, index) => (
            <div key={skillRule.id} className="grid gap-2 rounded-md border border-border bg-muted/15 p-2 sm:grid-cols-[76px_1fr_1fr_28px]">
              <div className="pt-2 text-xs font-semibold text-muted-foreground">Skill {index + 1}</div>
              <SearchableSelect value={skillRule.name} onChange={(value) => onChange({ ...rule, skillRules: rule.skillRules.map((item) => item.id === skillRule.id ? { ...item, name: value } : item) })} options={skills.map((skill) => ({ value: skill, label: skill }))} placeholder="Primary skill" triggerClassName="h-8 text-xs" />
              <div className="space-y-1">
                <SearchableSelect
                  value=""
                  clearOnSelect
                  onChange={(value) => onChange({ ...rule, skillRules: rule.skillRules.map((item) => item.id === skillRule.id ? { ...item, alternatives: addUnique(item.alternatives, value) } : item) })}
                  options={skills.filter((skill) => !skillRule.alternatives.includes(skill) && skill !== skillRule.name).map((skill) => ({ value: skill, label: skill }))}
                  placeholder="Add replacement"
                  triggerClassName="h-8 text-xs"
                />
                <div className="flex flex-wrap gap-1">
                  {skillRule.alternatives.map((skill) => (
                    <ToneBadge key={skill} category="skill" className="gap-1 px-1.5 py-0.5 text-[10px]">
                      {skill}
                      <button onClick={() => onChange({ ...rule, skillRules: rule.skillRules.map((item) => item.id === skillRule.id ? { ...item, alternatives: removeValue(item.alternatives, skill) } : item) })} className="hover:text-destructive">
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </ToneBadge>
                  ))}
                </div>
              </div>
              <button onClick={() => onChange({ ...rule, skillRules: rule.skillRules.filter((item) => item.id !== skillRule.id) })} className="text-muted-foreground hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">Recommended gear</p>
        <div className="grid gap-2 sm:grid-cols-5">
          {EQUIP_SLOTS.map(({ slot }) => (
            <div key={slot} className="space-y-1">
              <span className="text-[10px] font-semibold text-muted-foreground">{slot}</span>
              <SearchableSelect
                value=""
                clearOnSelect
                onChange={(value) => onChange({ ...rule, recommendedGear: { ...rule.recommendedGear, [slot]: addUnique(Array.isArray(rule.recommendedGear[slot]) ? rule.recommendedGear[slot] : [], value) } })}
                options={(equipBySlot[slot] ?? []).filter((name) => !(rule.recommendedGear[slot] ?? []).includes(name)).map((name) => ({ value: name, label: name }))}
                placeholder="Add item"
                triggerClassName="h-8 text-[10px]"
              />
              <div className="flex min-h-5 flex-wrap gap-1">
                {(Array.isArray(rule.recommendedGear[slot]) ? rule.recommendedGear[slot] : []).map((item) => (
                  <ToneBadge key={item} category="equipment" className="gap-1 px-1.5 py-0.5 text-[10px]">
                    {item}
                    <button onClick={() => onChange({ ...rule, recommendedGear: { ...rule.recommendedGear, [slot]: removeValue(Array.isArray(rule.recommendedGear[slot]) ? rule.recommendedGear[slot] : [], item) } })} className="hover:text-destructive">
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </ToneBadge>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <label className="block space-y-1 text-xs">
        <span className="font-medium text-muted-foreground">Notes</span>
        <textarea value={rule.notes} onChange={(e) => onChange({ ...rule, notes: e.target.value })} className="min-h-20 w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-xs" />
      </label>
    </div>
  );
}

function BoxSetupCard({
  setup,
  loadouts,
  data,
  onChange,
  onPublish,
  publishStatus,
  onCreateLoadout,
  readOnly = false,
}: {
  setup: BoxSetup;
  loadouts: Loadout[];
  data: SharedData;
  onChange: (next: BoxSetup) => void;
  onPublish: (setup: BoxSetup) => void;
  publishStatus?: "working" | "ok" | "error";
  onCreateLoadout: () => string;
  readOnly?: boolean;
}) {
  const [mode, setMode] = useState<"rules" | "try">("rules");
  const [openCellIndex, setOpenCellIndex] = useState<number | null>(null);
  const [attempt, setAttempt] = useLocalFeature<BoxAttempt>(`ka_box_attempt_${setup.id}`, {});
  const [exampleViewIndex, setExampleViewIndex] = useState<Record<string, number>>({});
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [shareStatus, setShareStatus] = useState<null | "working" | "ok" | "error">(null);
  const openCell = openCellIndex == null ? null : setup.cells[openCellIndex] ?? null;
  const openRule = openCell?.rule ?? createBoxUnitRule();

  const updateCell = (index: number, cell: BoxGridCell) => {
    if (readOnly) return;
    onChange({ ...setup, cells: setup.cells.map((item, i) => i === index ? cell : item), updatedAt: Date.now() });
  };
  const toggleSpecialSlot = (index: number, type: "pet" | "filler") => {
    if (readOnly) return;
    const cell = setup.cells[index];
    if (!cell) return;
    const nextCell = type === "pet"
      ? { ...cell, assignedToPet: !cell.assignedToPet, assignedToFiller: false }
      : { ...cell, assignedToFiller: !cell.assignedToFiller, assignedToPet: false };
    updateCell(index, nextCell);
  };
  const setRows = (rows: number) => {
    if (readOnly) return;
    const nextRows = Math.max(1, Math.min(12, rows));
    const cells = Array.from({ length: nextRows * 5 }, (_, index) => setup.cells[index] ?? { id: `${setup.id}-${index}` });
    onChange({ ...setup, rows: nextRows, cells, updatedAt: Date.now() });
  };
  const moveCell = (from: number, to: number) => {
    if (from === to) return;
    if (readOnly && mode === "rules") return;
    if (mode === "rules") {
      const cells = [...setup.cells];
      const [moved] = cells.splice(from, 1);
      cells.splice(to, 0, moved);
      onChange({ ...setup, cells, updatedAt: Date.now() });
      return;
    }
    const fromId = setup.cells[from]?.id;
    const toId = setup.cells[to]?.id;
    if (!fromId || !toId) return;
    setAttempt({ ...attempt, [fromId]: attempt[toId] ?? "", [toId]: attempt[fromId] ?? "" });
  };
  const rulesCount = setup.cells.filter((cell) => cell.rule).length;
  const passCount = setup.cells.filter((cell) => {
    if (!cell.rule) return false;
    const examples = getAttemptIds(attempt, cell.id)
      .map((id) => loadouts.find((loadout) => loadout.id === id) ?? null)
      .filter(Boolean) as Loadout[];
    return examples.some((loadout) => unitMatchesRule(loadout, cell.rule, data) === "pass");
  }).length;
  const shareSetup = async () => {
    setShareStatus("working");
    try {
      const response = await fetch(apiUrl("/loadout-box-setups/share"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setup: { ...setup, updatedAt: Date.now() } }),
      });
      if (!response.ok) throw new Error("share failed");
      const payload = await response.json() as { id: string };
      const url = new URL(window.location.href);
      url.pathname = "/loadout";
      url.search = "";
      url.searchParams.set("setup", setup.id);
      url.searchParams.set("share", payload.id);
      await navigator.clipboard.writeText(url.toString());
      setShareStatus("ok");
    } catch {
      setShareStatus("error");
    } finally {
      setTimeout(() => setShareStatus(null), 2500);
    }
  };

  return (
    <Card className="shadow-sm">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">{setup.title}</CardTitle>
            <p className="text-xs text-muted-foreground">
              {readOnly
                ? "Shared link snapshot. Your matching attempt is private to this device."
                : setup.publishedBy
                  ? `Local draft. Last published by ${setup.publishedBy}. Your matching attempt is private.`
                  : "Local draft. Publish when this setup should show for everyone."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!readOnly && (
              <>
                <Button size="sm" variant={publishStatus === "ok" ? "default" : "outline"} className="h-8 text-xs" onClick={() => onPublish(setup)} disabled={publishStatus === "working"}>
                  {publishStatus === "working" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {publishStatus === "ok" && <Check className="h-3.5 w-3.5" />}
                  {publishStatus === "ok" ? "Published" : publishStatus === "error" ? "Failed" : "Publish"}
                </Button>
                <Button size="sm" variant="outline" className="h-8 text-xs" onClick={shareSetup} disabled={shareStatus === "working"}>
                  {shareStatus === "working" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Copy className="h-3.5 w-3.5" />}
                  {shareStatus === "ok" ? "Copied" : shareStatus === "error" ? "Failed" : "Share setup"}
                </Button>
              </>
            )}
            <div className="flex rounded-md border border-border p-0.5">
              <button onClick={() => setMode("rules")} className={`h-8 rounded px-3 text-xs ${mode === "rules" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>Rules</button>
              <button onClick={() => setMode("try")} className={`h-8 rounded px-3 text-xs ${mode === "try" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>Try match</button>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Rows</span>
          <ThemedNumberInput value={setup.rows} min={1} max={12} onValueChange={setRows} className="h-8 w-20" ariaLabel={`${setup.title} rows`} disabled={readOnly} />
          <Badge variant="outline" className="text-xs">{rulesCount} required positions</Badge>
          {mode === "try" && <Badge variant="outline" className="text-xs text-emerald-700 dark:text-emerald-300">{passCount}/{rulesCount} matched</Badge>}
        </div>
        <p className="text-xs text-muted-foreground">
          {mode === "rules"
            ? "Click a spot to add or edit its required unit rules. Switch to Try match when you want to place your own loadouts."
            : "Click a spot to choose one of your saved loadouts for that position."}
        </p>
        <textarea value={setup.notes} onChange={(e) => !readOnly && onChange({ ...setup, notes: e.target.value, updatedAt: Date.now() })} readOnly={readOnly} placeholder="Shared notes, rewards, farming steps, herbs per round, or setup context." className="min-h-16 w-full resize-y rounded-md border border-input bg-muted/15 px-3 py-2 text-xs" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-5 gap-2">
          {setup.cells.map((cell, index) => {
            const examples = getAttemptIds(attempt, cell.id)
              .map((id) => loadouts.find((loadout) => loadout.id === id) ?? null)
              .filter(Boolean) as Loadout[];
            const activeExampleIndex = examples.length > 0 ? Math.min(exampleViewIndex[cell.id] ?? 0, examples.length - 1) : 0;
            const activeExample = examples[activeExampleIndex] ?? null;
            const rotateExample = (delta: number) => {
              if (examples.length <= 1) return;
              setExampleViewIndex((prev) => ({
                ...prev,
                [cell.id]: (activeExampleIndex + delta + examples.length) % examples.length,
              }));
            };
            const removeExample = (id: string) => {
              setAttempt(removeAttemptId(attempt, cell.id, id));
              setExampleViewIndex((prev) => ({ ...prev, [cell.id]: Math.max(0, activeExampleIndex - 1) }));
            };
            const match = cell.rule && examples.some((loadout) => unitMatchesRule(loadout, cell.rule, data) === "pass") ? "pass" : examples.length > 0 ? "fail" : "missing";
            const stateClass = cell.assignedToPet
              ? "border-amber-400/70 bg-amber-50/40 dark:bg-amber-950/10"
              : cell.assignedToFiller
                ? "border-sky-400/70 bg-sky-50/40 dark:bg-sky-950/10"
                : mode === "try" && cell.rule
                  ? (match === "pass" ? "border-emerald-500 bg-emerald-50/40 dark:bg-emerald-950/15" : "border-red-500 bg-red-50/40 dark:bg-rose-950/15")
                  : cell.rule
                    ? "border-primary/50 bg-primary/5"
                    : "border-dashed border-border bg-muted/15";
            return (
              <div key={cell.id} role="button" tabIndex={0} draggable onDragStart={() => setDragIndex(index)} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (dragIndex != null) moveCell(dragIndex, index); setDragIndex(null); }} onClick={() => setOpenCellIndex(index)} onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                setOpenCellIndex(index);
              }} className={`min-h-36 rounded-md border-2 p-2 text-left transition-colors ${stateClass}`}>
                <div className="flex items-center gap-2 text-[10px]">
                  <span className="font-semibold text-muted-foreground">Spot {index + 1}</span>
                  {cell.rule && <span className="truncate font-semibold text-foreground">{cell.rule.label || "Required unit"}</span>}
                  {cell.rule && <span className="min-w-0 truncate text-muted-foreground">Rule: {cell.rule.anyJob ? "Any job" : cell.rule.jobOptions.join(" / ") || "Job not set"}</span>}
                  {mode === "try" && cell.rule && <span className={`ml-auto shrink-0 font-bold ${match === "pass" ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}`}>{match === "pass" ? "OK" : "Check"}</span>}
                </div>
                {cell.assignedToPet || cell.assignedToFiller ? (
                  <div className="mt-2 flex min-h-28 flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
                    <div className={`w-full rounded-md border px-3 py-4 text-center text-lg font-black tracking-wide ${cell.assignedToPet ? "border-amber-400/70 bg-amber-400/10 text-amber-600 dark:text-amber-300" : "border-sky-400/70 bg-sky-400/10 text-sky-600 dark:text-sky-300"}`}>
                      {cell.assignedToPet ? "PET" : "FILLER UNIT"}
                    </div>
                    <button
                      type="button"
                      onClick={(event) => { event.stopPropagation(); event.preventDefault(); toggleSpecialSlot(index, cell.assignedToPet ? "pet" : "filler"); }}
                      className={`inline-flex w-full justify-center rounded-md border px-2 py-2 text-[10px] font-medium ${cell.assignedToPet ? "border-amber-400/70 text-amber-600 dark:text-amber-300" : "border-sky-400/70 text-sky-600 dark:text-sky-300"}`}
                    >
                      Unassign {cell.assignedToPet ? "pet" : "filler unit"}
                    </button>
                  </div>
                ) : cell.rule ? (
                  <div className="mt-1.5 flex min-h-28 flex-col space-y-1.5">
                    {cell.rule.skillRules.length > 0 && <div className="text-[10px] text-muted-foreground">{cell.rule.skillRules.length} skill rule{cell.rule.skillRules.length === 1 ? "" : "s"}</div>}
                    {activeExample && (
                      <div className="space-y-1">
                        <LoadoutMiniSummary loadout={activeExample} data={data} onRemove={() => removeExample(activeExample.id)} />
                      </div>
                    )}
                    <div className="mt-auto flex items-center gap-1.5">
                      <span className="inline-flex w-fit items-center gap-1 rounded-md border border-border bg-background/70 px-2 py-1 text-[10px] font-medium text-primary">
                        {mode === "rules" ? <Pencil className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
                        {mode === "rules" ? "Edit slot" : examples.length > 0 ? "Change examples" : "Choose examples"}
                      </span>
                      {examples.length > 1 && (
                        <span className="ml-auto inline-flex items-center gap-1">
                          <span className="text-[10px] text-muted-foreground">{activeExampleIndex + 1}/{examples.length}</span>
                          <span className="inline-flex overflow-hidden rounded border border-border text-[10px] text-muted-foreground">
                            <span role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); rotateExample(-1); }} className="px-2 py-1 hover:bg-muted">{"<"}</span>
                            <span role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); rotateExample(1); }} className="border-l border-border px-2 py-1 hover:bg-muted">{">"}</span>
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex min-h-28 flex-col justify-center gap-2 text-xs text-muted-foreground">
                    {activeExample ? (
                      <div className="space-y-1">
                        <LoadoutMiniSummary loadout={activeExample} data={data} onRemove={() => removeExample(activeExample.id)} />
                      </div>
                    ) : <span className="text-center">Empty</span>}
                    <div className="flex items-center justify-center gap-1.5">
                      <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-border bg-background/70 px-2 py-1 text-[10px] font-medium text-primary">
                        {mode === "rules" ? <Plus className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
                        {mode === "rules" ? examples.length > 0 ? "Add requirement" : "Add examples or rules" : examples.length > 0 ? "Change examples" : "Choose examples"}
                      </span>
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); event.preventDefault(); toggleSpecialSlot(index, "pet"); }}
                        className="inline-flex w-fit items-center rounded-md border border-border bg-background/70 px-2 py-1 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                      >
                        Assign to pet
                      </button>
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); event.preventDefault(); toggleSpecialSlot(index, "filler"); }}
                        className="inline-flex w-fit items-center rounded-md border border-border bg-background/70 px-2 py-1 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                      >
                        Assign to filler unit
                      </button>
                      {examples.length > 1 && (
                        <span className="ml-auto inline-flex items-center gap-1">
                          <span className="text-[10px] text-muted-foreground">{activeExampleIndex + 1}/{examples.length}</span>
                          <span className="inline-flex overflow-hidden rounded border border-border text-[10px] text-muted-foreground">
                            <span role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); rotateExample(-1); }} className="px-2 py-1 hover:bg-muted">{"<"}</span>
                            <span role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); rotateExample(1); }} className="border-l border-border px-2 py-1 hover:bg-muted">{">"}</span>
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <Dialog modal={false} open={openCellIndex != null} onOpenChange={(open) => { if (!open) setOpenCellIndex(null); }}>
          <DialogContent className="bottom-[5.5rem] top-[calc(5.5rem+env(safe-area-inset-top))] flex h-auto max-h-none w-[calc(100vw-3rem)] max-w-5xl translate-y-0 flex-col overflow-hidden p-0">
            <DialogHeader className="shrink-0 border-b border-border px-5 py-4 pr-14">
              <DialogTitle className="text-sm">
                {openCell?.assignedToFiller ? "Edit filler rules" : openCell?.assignedToPet ? "Edit pet rules" : mode === "rules" ? "Edit requirements" : "Choose loadout"} - {setup.title} spot {(openCellIndex ?? 0) + 1}
              </DialogTitle>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {openCellIndex != null && (
                (() => {
                  const cellId = setup.cells[openCellIndex].id;
                  const exampleIds = getAttemptIds(attempt, cellId);
                  const exampleLoadouts = exampleIds
                    .map((id) => loadouts.find((loadout) => loadout.id === id) ?? null)
                    .filter(Boolean) as Loadout[];
                  const availableExampleOptions = loadouts
                    .filter((loadout) => !exampleIds.includes(loadout.id))
                    .map((loadout) => ({ value: loadout.id, label: `${loadout.name || "Unnamed Loadout"}${loadout.jobName ? ` - ${loadout.jobName}` : ""}` }));

                  return (
                    <div className="space-y-4">
                      <section className="rounded-md border border-border bg-muted/15 p-3">
                        <div className="mb-3">
                          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                            {openCell?.assignedToFiller ? "Section 1 - Filler rules" : openCell?.assignedToPet ? "Section 1 - Pet rules" : "Section 1 - Slot rules"}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {openCell?.assignedToFiller
                              ? "Filler slots only use level requirements."
                              : openCell?.assignedToPet
                                ? "Pet slots only use pet level requirements."
                                : "These are the public requirements for this position after you publish the setup."}
                          </p>
                        </div>
                        {openCell?.assignedToPet || openCell?.assignedToFiller ? (
                          <BoxPetRuleEditor
                            rule={openCell.petRule}
                            onChange={(next) => updateCell(openCellIndex, { ...setup.cells[openCellIndex], petRule: next })}
                          />
                        ) : mode === "rules" ? (
                          <BoxUnitRuleEditor rule={openRule} data={data} onChange={(next) => updateCell(openCellIndex, { ...setup.cells[openCellIndex], rule: next })} />
                        ) : (
                          <div className="rounded-md border border-border bg-background/60 p-3 text-xs text-muted-foreground">
                            Switch to Rules to edit this slot's public job, stat, skill, and gear requirements.
                          </div>
                        )}
                      </section>

                      {!openCell?.assignedToPet && !openCell?.assignedToFiller && <section className="rounded-md border border-border bg-muted/15 p-3">
                        <div className="mb-3">
                          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Section 2 - Example units</p>
                          <p className="mt-1 text-xs text-muted-foreground">Add saved characters as private examples for this slot.</p>
                        </div>
                        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                          <SearchableSelect
                            value=""
                            clearOnSelect
                            onChange={(value) => setAttempt(addAttemptId(attempt, cellId, value))}
                            options={availableExampleOptions}
                            placeholder="Add saved character"
                            triggerClassName="h-8 text-xs"
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-8 text-xs"
                            onClick={() => {
                              const id = onCreateLoadout();
                              setAttempt(addAttemptId(attempt, cellId, id));
                              setOpenCellIndex(null);
                              window.setTimeout(() => window.scrollTo({ top: 0, behavior: "smooth" }), 0);
                            }}
                          >
                            <Plus className="h-3.5 w-3.5" />Make new unit
                          </Button>
                        </div>
                        <div className="mt-3 flex min-h-6 flex-wrap gap-1.5">
                          {exampleLoadouts.map((loadout) => {
                            const match = unitMatchesRule(loadout, openCell?.rule, data);
                            return (
                              <ToneBadge key={loadout.id} category="job" className="gap-1 px-2 py-0.5 text-xs">
                                {loadout.name || "Unnamed Loadout"}{loadout.jobName ? ` - ${loadout.jobName}` : ""}
                                {openCell?.rule && (
                                  <span className={match === "pass" ? "text-emerald-600 dark:text-emerald-300" : "text-amber-600 dark:text-amber-300"}>
                                    {match === "pass" ? "OK" : "Check"}
                                  </span>
                                )}
                                <button onClick={() => setAttempt(removeAttemptId(attempt, cellId, loadout.id))} className="hover:text-destructive">
                                  <X className="h-2.5 w-2.5" />
                                </button>
                              </ToneBadge>
                            );
                          })}
                          {exampleLoadouts.length === 0 && <span className="text-xs text-muted-foreground/60">No example units yet.</span>}
                        </div>
                      </section>}

                      <div className="sticky bottom-0 -mx-5 flex justify-between gap-2 border-t border-border bg-background px-5 py-3">
                        {openCell?.assignedToPet || openCell?.assignedToFiller ? (
                          <Button size="sm" variant="destructive" onClick={() => updateCell(openCellIndex, { ...setup.cells[openCellIndex], petRule: undefined })}>
                            <Trash2 className="h-3.5 w-3.5" />Clear {openCell?.assignedToPet ? "pet" : "filler"} rules
                          </Button>
                        ) : mode === "rules" ? (
                          <Button size="sm" variant="destructive" onClick={() => updateCell(openCellIndex, { ...setup.cells[openCellIndex], rule: undefined })}>
                            <Trash2 className="h-3.5 w-3.5" />Clear rules
                          </Button>
                        ) : <span />}
                        <Button size="sm" onClick={() => setOpenCellIndex(null)}>Done</Button>
                      </div>
                    </div>
                  );
                })()
              )}
            </div>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}

function ScreenshotCard({ loadout, stats }: { loadout: Loadout; stats: Record<string, number> }) {
  const hasStats = STAT_KEYS.some((k) => stats[k]);
  return (
    <div style={{ background: "#0f172a", color: "#f1f5f9", padding: 20, borderRadius: 12, width: 480, fontFamily: "system-ui, sans-serif" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 2 }}>{loadout.name || "Loadout"}</div>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            {loadout.jobName || "No job"} {loadout.rank ? `· Rank ${loadout.rank}` : ""} {(loadout.level ?? 1) > 1 ? `· Lv ${loadout.level}` : ""}
          </div>
        </div>
        <div style={{ fontSize: 10, color: "#64748b" }}>Kingdom Adventures</div>
      </div>

      {hasStats && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 4, marginBottom: 14 }}>
          {STAT_KEYS.filter((k) => stats[k]).map((k) => (
            <div key={k} style={{ background: "#1e293b", borderRadius: 6, padding: "6px 4px", textAlign: "center" }}>
              <div style={{ fontSize: 9, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>{STAT_LABEL[k]}</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#f1f5f9" }}>{stats[k].toLocaleString()}</div>
            </div>
          ))}
        </div>
      )}

      {loadout.equipment.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>Equipment</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {loadout.equipment.map((eq, i) => (
              <div key={i} style={{ background: "#1e293b", borderRadius: 4, padding: "2px 8px", fontSize: 11, color: "#e2e8f0" }}>
                {eq.name} {eq.level > 1 ? <span style={{ color: "#64748b" }}>Lv{eq.level}</span> : ""}
              </div>
            ))}
          </div>
        </div>
      )}

      {loadout.skills.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>Skills</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {loadout.skills.map((s, i) => (
              <div key={i} style={{ background: "#312e81", borderRadius: 4, padding: "2px 8px", fontSize: 11, color: "#c7d2fe", display: "flex", alignItems: "center", gap: 4 }}>
                {getSkillIcon(s) && (
                  <img src={getSkillIcon(s)} alt="" style={{ width: 14, height: 14, objectFit: "contain", imageRendering: "pixelated" }} />
                )}
                {s}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Character Preview ────────────────────────────────────────────────────────

type PreviewPoseFrame = 0 | 2;

let sharedIdlePoseFrame: PreviewPoseFrame = 0;
let sharedIdlePoseTimer: number | null = null;
const sharedIdlePoseSubscribers = new Set<(poseFrame: PreviewPoseFrame) => void>();

function subscribeIdlePreviewPose(listener: (poseFrame: PreviewPoseFrame) => void) {
  sharedIdlePoseSubscribers.add(listener);
  listener(sharedIdlePoseFrame);

  if (sharedIdlePoseTimer === null) {
    sharedIdlePoseTimer = window.setInterval(() => {
      sharedIdlePoseFrame = sharedIdlePoseFrame === 0 ? 2 : 0;
      sharedIdlePoseSubscribers.forEach((subscriber) => subscriber(sharedIdlePoseFrame));
    }, 500);
  }

  return () => {
    sharedIdlePoseSubscribers.delete(listener);
    if (sharedIdlePoseSubscribers.size === 0 && sharedIdlePoseTimer !== null) {
      window.clearInterval(sharedIdlePoseTimer);
      sharedIdlePoseTimer = null;
    }
  };
}

function useIdlePreviewPose(): PreviewPoseFrame {
  const [poseFrame, setPoseFrame] = useState<PreviewPoseFrame>(sharedIdlePoseFrame);

  useEffect(() => subscribeIdlePreviewPose(setPoseFrame), []);

  return poseFrame;
}

function CollapsedCharPreview({ jobName, rank, weaponName, shieldName }: { jobName: string; rank?: string | null; weaponName?: string | null; shieldName?: string | null }) {
  const poseFrame = useIdlePreviewPose();
  return (
    <CharacterPreviewCanvas
      jobName={jobName}
      rank={rank}
      variant={1}
      equipState="right"
      weaponName={weaponName}
      shieldName={shieldName}
      scale={5}
      poseFrame={poseFrame}
      label="character"
      className="mx-auto shrink-0 rounded"
    />
  );
}

function CharacterPreview({
  jobName,
  rank,
  weaponName,
  shieldName,
}: {
  jobName: string;
  rank?: string;
  weaponName: string | null;
  shieldName: string | null;
}) {
  const [gender, setGender] = useState<1 | 2>(1);
  const [equipState, setEquipState] = useState<"right" | "up">("right");
  const poseFrame = useIdlePreviewPose();

  return (
    <div className="flex flex-col items-center gap-2 py-1">
      <CharacterPreviewCanvas
        jobName={jobName}
        rank={rank}
        variant={gender}
        equipState={equipState}
        weaponName={weaponName}
        shieldName={shieldName}
        scale={5}
        poseFrame={poseFrame}
        label={`${jobName} character`}
        className="rounded"
      />
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => setGender(1)}
          className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-colors flex items-center gap-1 ${gender === 1 ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:text-foreground"}`}
        ><img src="/website_icons/gender/gender_0_male.png" alt="Male" className="w-4 h-5" style={{imageRendering: "pixelated"}} /> Male</button>
        <button
          onClick={() => setGender(2)}
          className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-colors flex items-center gap-1 ${gender === 2 ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:text-foreground"}`}
        ><img src="/website_icons/gender/gender_1_female.png" alt="Female" className="w-4 h-5" style={{imageRendering: "pixelated"}} /> Female</button>
        <span className="w-px h-3 bg-border mx-0.5" />
        <button
          onClick={() => setEquipState("right")}
          className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-colors ${equipState === "right" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:text-foreground"}`}
        >Right</button>
        <button
          onClick={() => setEquipState("up")}
          className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-colors ${equipState === "up" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:text-foreground"}`}
        >Up</button>
      </div>
    </div>
  );
}

// ─── Loadout Editor ───────────────────────────────────────────────────────────

// ─── Stat goal planner ───────────────────────────────────────────────────────

const GOAL_MODES: Array<{ value: "off" | GoalMode; label: string }> = [
  { value: "off", label: "Any" },
  { value: "min", label: "At least" },
  { value: "max", label: "At most" },
  { value: "range", label: "Between" },
];

function GoalPlannerPanel({
  loadout,
  data,
  baseStats,
  onApply,
  onChange,
}: {
  loadout: Loadout;
  data: SharedData;
  /** Job curve + valuables: everything the planner cannot change. */
  baseStats: Record<string, number>;
  onApply: (picks: Array<{ name: string; level: number }>) => void;
  onChange: (next: Loadout) => void;
}) {
  const [open, setOpen] = useState(false);
  const planner = loadout.goalPlanner ?? {};
  const goals = planner.goals ?? {};
  const unavailable = useMemo(() => planner.unavailable ?? [], [planner.unavailable]);
  const ownedLevels = planner.ownedLevels ?? {};

  const setPlanner = (patch: Partial<NonNullable<Loadout["goalPlanner"]>>) =>
    onChange({ ...loadout, goalPlanner: { ...planner, ...patch } });

  const setGoal = (stat: string, next: StatGoal | null) => {
    const goalsNext = { ...goals };
    if (next && next.mode) goalsNext[stat] = next;
    else delete goalsNext[stat];
    setPlanner({ goals: goalsNext });
  };

  // Candidate gear + contributions, using the same affinity rules as the totals.
  const { slots, contribution } = useMemo(() => {
    const slotMap = data.slotAssignments ?? {};
    const overrides = data.overrides ?? {};
    const profile = loadout.jobName ? getJobProfile(data, loadout.jobName) : null;
    const bySlot = new Map<string, string[]>();
    const multipliers = new Map<string, number>();
    const contributions = new Map<string, Record<string, number>>();

    for (const [name, slot] of Object.entries(slotMap)) {
      if (unavailable.includes(name)) continue;
      if (!overrides[name]) continue;
      const weaponType = slot === "Shield" ? "Shield" : data.weaponTypes?.[name] ?? null;
      const prof = slot === "Shield"
        ? profile?.equipmentAccess.shield ?? null
        : slot === "Weapon" && weaponType && weaponType !== "Tool"
          ? profile?.equipmentAccess.weapons[weaponType] ?? null
          : null;
      if (!profile || prof === "cannot") continue;
      const resistanceSkill = findResistanceSkill(data.skills, weaponType);
      const resists = !!resistanceSkill && loadout.skills.includes(resistanceSkill.name);
      multipliers.set(name, prof === "weak" && !resists ? 0.5 : 1);
      const list = bySlot.get(slot) ?? [];
      list.push(name);
      bySlot.set(slot, list);
    }

    const contributionOf = (name: string, level: number): Record<string, number> => {
      const cacheKey = `${name}|${level}`;
      const hit = contributions.get(cacheKey);
      if (hit) return hit;
      const multiplier = multipliers.get(name) ?? 1;
      const out: Record<string, number> = {};
      for (const [stat, entry] of Object.entries(overrides[name] ?? {})) {
        const base = entry?.base ?? 0;
        const inc = entry?.inc ?? 0;
        if (base || inc) out[normStat(stat)] = Math.floor(statAtLevel(base, inc, level) * multiplier);
      }
      contributions.set(cacheKey, out);
      return out;
    };

    return {
      slots: EQUIP_SLOTS
        .map(({ slot }) => ({ slot, items: (bySlot.get(slot) ?? []).sort() }))
        .filter((entry) => entry.items.length > 0),
      contribution: contributionOf,
    };
  }, [data, loadout.jobName, loadout.skills, unavailable]);

  const result = useMemo(() => {
    if (!open) return null;
    if (Object.keys(goals).length === 0) return null;
    return planGear({ baseStats, goals, slots, contribution, pinnedLevels: ownedLevels });
  }, [open, baseStats, goals, slots, contribution, ownedLevels]);

  const goalKeys = Object.keys(goals);

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-muted/10">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "" : "-rotate-90"}`} />
          Stat goal planner
          {goalKeys.length > 0 && (
            <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium normal-case tracking-normal text-primary">
              {goalKeys.length} target{goalKeys.length === 1 ? "" : "s"}
            </span>
          )}
          {result && (
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium normal-case tracking-normal ${result.unmet.length === 0 ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-amber-500/15 text-amber-600 dark:text-amber-400"}`}>
              {result.unmet.length === 0 ? "targets met" : `${result.unmet.length} still short`}
            </span>
          )}
        </span>
        <span className="text-[10px] text-muted-foreground/70">suggests gear for the stats you want - valuables are included, never suggested</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border/50 px-3 py-3">
          {/* Targets */}
          <div>
            <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Targets</p>
            <div className="grid grid-cols-2 gap-1.5 lg:grid-cols-3 xl:grid-cols-4">
              {[...STAT_KEYS].map((stat) => {
                const goal = goals[stat];
                const mode: "off" | GoalMode = goal?.mode ?? "off";
                return (
                  <div key={stat} className="flex flex-col gap-1.5 rounded-md border border-border/50 bg-background/40 px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="w-14 shrink-0 text-[10px] font-medium uppercase text-muted-foreground">
                        <StatLabel stat={stat} icons={data.statIcons} />
                      </span>
                      <select
                        value={mode}
                        aria-label={`${stat} goal`}
                        onChange={(e) => {
                          const nextMode = e.target.value as "off" | GoalMode;
                          if (nextMode === "off") return setGoal(stat, null);
                          if (nextMode === "min") return setGoal(stat, { mode: "min", min: goal?.min ?? goal?.max ?? 0 });
                          if (nextMode === "max") return setGoal(stat, { mode: "max", max: goal?.max ?? goal?.min ?? 0 });
                          return setGoal(stat, { mode: "range", min: goal?.min ?? 0, max: goal?.max ?? 0 });
                        }}
                        className="h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-[11px]"
                      >
                        {GOAL_MODES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                      </select>
                    </div>
                    {goal && (
                      <div className="flex items-center gap-1.5">
                        {mode === "range" ? (
                          <>
                            <CompactNumberInput value={goal?.min ?? 0} min={0} ariaLabel={`${stat} lower bound`} className="min-w-0 flex-1"
                              onValueChange={(value) => setGoal(stat, { mode: "range", min: value, max: goal?.max ?? 0 })} />
                            <span className="shrink-0 text-[10px] font-medium text-muted-foreground">to</span>
                            <CompactNumberInput value={goal?.max ?? 0} min={0} ariaLabel={`${stat} upper bound`} className="min-w-0 flex-1"
                              onValueChange={(value) => setGoal(stat, { mode: "range", min: goal?.min ?? 0, max: value })} />
                          </>
                        ) : (
                          <CompactNumberInput
                            value={mode === "max" ? (goal?.max ?? 0) : (goal?.min ?? 0)}
                            min={0}
                            ariaLabel={mode === "max" ? `${stat} maximum` : `${stat} minimum`}
                            className="min-w-0 flex-1"
                            onValueChange={(value) => setGoal(stat, mode === "max" ? { mode: "max", max: value } : { mode: "min", min: value })}
                          />
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Suggestion */}
          {goalKeys.length === 0 && (
            <p className="text-[11px] text-muted-foreground">Pick at least one target above (for example Attack "At least" 300) to get gear suggestions.</p>
          )}

          {result && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Suggested gear {result.picks.length > 0 && <span className="normal-case tracking-normal">({result.picks.length} slot{result.picks.length === 1 ? "" : "s"})</span>}
                </p>
                {result.picks.length > 0 && (
                  <Button size="sm" className="h-7 gap-1.5 text-xs"
                    title="Equip every suggested item (slots you already have gear in are replaced)"
                    onClick={() => onApply(result.picks.map((pick) => ({ name: pick.item, level: pick.level })))}>
                    Equip all {result.picks.length} slot{result.picks.length === 1 ? "" : "s"}
                  </Button>
                )}
              </div>

              {result.picks.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">No gear in the allowed slots can reach these targets.</p>
              ) : (
                <div className="space-y-1">
                  {result.picks.map((pick) => {
                    const icon = getPreferredEquipmentIcon(data.equipIcons, pick.item);
                    const pinned = ownedLevels[pick.item];
                    return (
                      <div key={`${pick.slot}-${pick.item}`} className="flex flex-wrap items-center gap-2 rounded-md border border-border/50 bg-background/50 px-2 py-1.5">
                        <span className="w-16 shrink-0 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{pick.slot}</span>
                        {icon
                          ? <img src={icon} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                          : <span className="h-6 w-6 shrink-0" />}
                        <span className="min-w-0 flex-1 truncate text-xs font-medium">{pick.item}</span>
                        <label className="flex items-center gap-1 text-[10px] text-muted-foreground">
                          Lv
                          <CompactNumberInput value={pinned ?? pick.level} min={1} max={99} ariaLabel={`${pick.item} owned level`}
                            className="w-14"
                            onValueChange={(value) => setPlanner({ ownedLevels: { ...ownedLevels, [pick.item]: value } })} />
                        </label>
                        <Button size="sm" variant="outline" className="h-6 px-2 text-[10px]"
                          title={`Equip ${pick.item} in the ${pick.slot} slot`}
                          onClick={() => onApply([{ name: pick.item, level: pick.level }])}>
                          Equip
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] text-muted-foreground hover:text-destructive"
                          title="I do not have this item"
                          onClick={() => setPlanner({ unavailable: [...unavailable, pick.item] })}>
                          Don't have
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}

              <div className="grid grid-cols-2 gap-1 text-[11px] sm:grid-cols-3 lg:grid-cols-4">
                {goalKeys.map((stat) => {
                  const goal = goals[stat];
                  const value = result.stats[stat] ?? 0;
                  const short = result.unmet.includes(stat);
                  return (
                    <div key={stat} className={`flex items-center justify-between rounded border px-1.5 py-1 ${short ? "border-amber-500/40 bg-amber-500/10" : "border-emerald-500/30 bg-emerald-500/10"}`}>
                      <span className="text-[10px] uppercase text-muted-foreground"><StatLabel stat={stat} icons={data.statIcons} /></span>
                      <span className="tabular-nums text-foreground/80">
                        {value.toLocaleString()}
                        <span className="ml-1 text-[10px] text-muted-foreground">{goalLabelText(goal)}</span>
                      </span>
                    </div>
                  );
                })}
              </div>

              {(unavailable.length > 0 || Object.keys(ownedLevels).length > 0) && (
                <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                  {unavailable.length > 0 && <span>Excluded: {unavailable.join(", ")}</span>}
                  {Object.keys(ownedLevels).length > 0 && <span>Locked levels: {Object.entries(ownedLevels).map(([name, level]) => `${name} Lv${level}`).join(", ")}</span>}
                  <Button size="sm" variant="ghost" className="h-5 px-1 text-[10px]"
                    onClick={() => setPlanner({ unavailable: [], ownedLevels: {} })}>
                    Clear constraints
                  </Button>
                </div>
              )}
              <p className="text-[10px] text-muted-foreground/70">
                Values include your universal valuables. Set a level on an item you already own to force that level, or mark it "Don't have" to re-route.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProfileGoalSearchPanel({ data, onCreate }: { data: SharedData; onCreate: (build: ProfileGoalBuild, profile: PlayerProfile) => void }) {
  const goalKeys = STAT_KEYS;
  const [goals, setGoals] = useState<Record<string, StatGoal | undefined>>({});
  const [editing, setEditing] = useState<Record<string, { mode: GoalMode; min: string; max: string }>>({});
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [pageBuilds, setPageBuilds] = useState<ProfileGoalBuild[]>([]);
  const [searchComplete, setSearchComplete] = useState(false);
  const [matchCount, setMatchCount] = useState(0);
  const [passPolicy, setPassPolicy] = useState<"owned-current" | "owned-upgrades">("owned-current");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const iteratorRef = useRef<AsyncGenerator<ProfileGoalSearchPage> | null>(null);
  const searchInputRef = useRef<ProfileGoalSearchInput | null>(null);
  const generationRef = useRef(0);
  useEffect(() => () => abortRef.current?.abort(), []);

  const useProfile = () => {
    generationRef.current += 1; abortRef.current?.abort(); iteratorRef.current = null; searchInputRef.current = null; setBusy(false); setPageBuilds([]); setSearchComplete(false); setMatchCount(0);
    try {
      const raw = localStorage.getItem("ka_synthetic_legal_profile_v1");
      if (!raw) { setError("No optimizer profile found. Add profile data in the optimizer first."); return; }
      const next = normalizePlayerProfile(JSON.parse(raw));
      setProfile(next);
      const waters = ["life", "wisdom", "vitality", "might", "resilience"];
      const unknown = waters.filter((key) => next.valuables.active[key] == null);
      setStatus(`Profile loaded: ${next.characters.length} characters, ${next.equipment.filter((row) => row.ownership === "owned").length} owned gear types.`);
      setError(unknown.length ? `Profile valuables are unknown: ${unknown.join(", ")}. Targets using HP, MP, Vigor, Attack or Defence will be held until those active counts are recorded.` : "");
      setPageBuilds([]); setSearchComplete(false); iteratorRef.current = null;
    } catch {
      setError("Could not read the optimizer profile.");
    }
  };

  const invalidateSearch = () => { generationRef.current += 1; abortRef.current?.abort(); iteratorRef.current = null; searchInputRef.current = null; setBusy(false); setPageBuilds([]); setSearchComplete(false); setMatchCount(0); setStatus(""); };
  const setGoalValue = (stat: string, field: "mode" | "min" | "max", value: string) => {
    invalidateSearch(); setError("");
    const current = editing[stat] ?? { mode: "min" as GoalMode, min: "", max: "" };
    const next = { ...current, [field]: value };
    setEditing((previous) => ({ ...previous, [stat]: next }));
    const lo = next.min.trim() === "" ? undefined : Number(next.min);
    const hi = next.max.trim() === "" ? undefined : Number(next.max);
    const parsed: StatGoal | undefined = next.mode === "min" ? (lo === undefined ? undefined : { mode: "min", min: lo })
      : next.mode === "max" ? (hi === undefined ? undefined : { mode: "max", max: hi })
        : lo === undefined || hi === undefined || hi < lo ? undefined : { mode: "range", min: lo, max: hi };
    setGoals((all) => ({ ...all, [stat]: parsed }));
  };

  const runSearch = async () => {
    if (!profile) { setError("Click Use profile data to load your saved characters and gear first."); return; }
    const activeGoals = Object.fromEntries(Object.entries(goals).filter(([, goal]) => goal)) as Record<string, StatGoal>;
    for (const [stat, value] of Object.entries(editing)) {
      const rawValues = value.mode === "min" ? [value.min] : value.mode === "max" ? [value.max] : [value.min, value.max];
      if (rawValues.every((raw) => raw.trim() === "")) continue;
      if (rawValues.some((raw) => raw.trim() === "" || !Number.isInteger(Number(raw)) || Number(raw) < 0)) { setError(`Enter valid non-negative whole numbers for the ${stat} target.`); return; }
      if (value.mode === "range" && Number(value.max) < Number(value.min)) { setError(`The ${stat} range maximum must be at least its minimum.`); return; }
    }
    if (!Object.keys(activeGoals).length) { setError("Set at least one complete target first."); return; }
    const waterStat = ["hp", "mp", "vig", "atk", "def"];
    const unknownTargets = waterStat.filter((stat) => activeGoals[stat] && profile.valuables.active[({ hp: "life", mp: "wisdom", vig: "vitality", atk: "might", def: "resilience" } as Record<string, string>)[stat]] == null);
    if (unknownTargets.length) { setError(`Cannot search these targets while the matching active valuables are unknown: ${unknownTargets.join(", ")}. Record them in the optimizer profile.`); return; }
    const unknownWaters = ["life", "wisdom", "vitality", "might", "resilience"].filter((key) => profile.valuables.active[key] == null);
    if (unknownWaters.length) { setError(`Record all active valuable counts before searching so applied builds keep the full profile context. Unknown: ${unknownWaters.join(", ")}.`); return; }
    generationRef.current += 1; const generation = generationRef.current;
    abortRef.current?.abort(); iteratorRef.current = null; searchInputRef.current = null;
    const controller = new AbortController(); abortRef.current = controller;
    setBusy(true); setError(""); setPageBuilds([]); setMatchCount(0); setSearchComplete(false); setPassPolicy("owned-current"); setStatus("Preparing profile characters…");
    const items = profile.equipment.map((row) => ({ item: row.equipmentName, ownership: row.ownership, currentLevel: row.currentLevel, quantity: row.quantity }));
    const waters = Object.fromEntries(["life", "wisdom", "vitality", "might", "resilience"].map((key) => [key, profile.valuables.active[key] as number]));
    const characters: ProfileGoalSearchCharacter[] = [];
    for (const character of profile.characters) {
      const saved = character.state;
      for (const access of character.jobRanks) for (const rank of access.ranks) {
        const legalSkills: string[] = [];
        for (const skill of saved.skills ?? []) if (profile.ownedSkills.includes(skill) && canPickHumanSkill(access.jobName, skill, legalSkills)) legalSkills.push(skill);
        const candidate: Loadout = { id: character.id, name: saved.name || character.id, jobName: access.jobName, rank, level: saved.level,
          statLevels: saved.statLevels ?? {}, equipment: [], skills: legalSkills, awakening: saved.awakening, residentStatItems: waters };
        const jobProfile = getJobProfile(data, access.jobName);
        const curve = calcJobStats(candidate, data);
        const base = { ...curve };
        for (const [key, bonus] of Object.entries(residentStatItemBonuses(waters))) base[key] = (base[key] ?? 0) + bonus;
        const bySlot = new Map<string, string[]>();
        for (const [name, slot] of Object.entries(data.slotAssignments ?? {})) {
          if (!data.overrides?.[name] || !jobProfile || !canPickHumanEquipment(candidate, data, name)) continue;
          const list = bySlot.get(slot) ?? []; list.push(name); bySlot.set(slot, list);
        }
        const contributions = (item: string, level: number) => calcEquipStats({ ...candidate, equipment: [{ name: item, level }] }, data);
        const slots = EQUIP_SLOTS.map(({ slot }) => ({ slot, items: (bySlot.get(slot) ?? []).sort() })).filter((slot) => slot.items.length > 0);
        const hasAccess = character.available && character.jobRanks.some((entry) => entry.jobName === access.jobName && entry.ranks.includes(rank));
        characters.push({ id: character.id, name: saved.name || character.id, jobName: access.jobName, rank, available: character.available,
          eligible: !!hasAccess && !!jobProfile && Object.keys(activeGoals).every((stat) => Number.isFinite(curve[stat])),
          input: { baseStats: base, goals: activeGoals, slots, contribution: contributions } });
      }
    }
    try {
      searchInputRef.current = { characters, inventory: items };
      iteratorRef.current = searchProfileGoalBuildPages(searchInputRef.current, { signal: controller.signal, pageSize: 40, nodeSliceSize: 1500, inventoryPolicy: "owned-current" });
      const first = await iteratorRef.current.next();
      if (generation !== generationRef.current) return;
      if (first.done) { setSearchComplete(true); setStatus("Search complete."); }
      else {
        setPageBuilds(first.value.builds); setSearchComplete(first.value.complete);
        setMatchCount(first.value.matchingBuilds);
        setStatus(first.value.complete ? `Search complete · ${first.value.matchingBuilds} matching builds · ${first.value.searchNodes.toLocaleString()} search steps explored.` : `${first.value.completedCharacters}/${first.value.totalCharacters} character/job/rank options · ${first.value.matchingBuilds} matches found · ${first.value.searchNodes.toLocaleString()} search steps explored. Continue for more.`);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Profile search failed."); }
    finally { if (generation === generationRef.current) setBusy(false); }
  };

  const continueSearch = async () => {
    const iterator = iteratorRef.current;
    if (!iterator || busy) return;
    const generation = generationRef.current;
    setBusy(true);
    try {
      const next = await iterator.next();
      if (generation !== generationRef.current) return;
      if (next.done) { setSearchComplete(true); setStatus("Search complete."); return; }
      const page = next.value;
      if (page.builds.length > 0) setPageBuilds(page.builds);
      setMatchCount(page.matchingBuilds);
      setSearchComplete(page.complete);
      setStatus(page.cancelled ? "Search cancelled." : page.complete
        ? `Search complete · ${page.matchingBuilds} matching builds · ${page.searchNodes.toLocaleString()} search steps explored.`
        : `${page.completedCharacters}/${page.totalCharacters} character/job/rank options · ${page.matchingBuilds} matches found · ${page.searchNodes.toLocaleString()} search steps explored. Continue for more.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Profile search failed."); }
    finally { if (generation === generationRef.current) setBusy(false); }
  };

  const startUpgradeSearch = () => {
    if (!searchInputRef.current || busy) return;
    generationRef.current += 1;
    abortRef.current?.abort();
    const controller = new AbortController(); abortRef.current = controller;
    iteratorRef.current = searchProfileGoalBuildPages(searchInputRef.current, { signal: controller.signal, pageSize: 40, nodeSliceSize: 1500, inventoryPolicy: "owned-upgrades" });
    setPageBuilds([]); setMatchCount(0); setSearchComplete(false); setPassPolicy("owned-upgrades"); setStatus("Searching separately for builds that require equipment upgrades.");
    void continueSearch();
  };

  return <Card className="mb-4 border-primary/25 shadow-sm">
    <CardHeader className="pb-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><CardTitle className="text-base">Find builds for target stats</CardTitle><CardDescription>Searches profile characters with their recorded stats and valuables. Current owned gear levels are searched first; upgrade options are a separate pass.</CardDescription></div><Button variant="outline" className="min-h-11" onClick={useProfile}>Use profile data</Button></div></CardHeader>
    <CardContent className="space-y-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {goalKeys.map((stat) => { const value = editing[stat] ?? { mode: "min" as GoalMode, min: "", max: "" }; return <div key={stat} className="flex min-w-0 flex-wrap items-center gap-1.5 rounded-md border p-2">
          <StatLabel stat={stat} icons={data.statIcons} />
          <select aria-label={`${stat} target type`} className="h-9 rounded border bg-background px-2 text-xs" value={value.mode} onChange={(event) => setGoalValue(stat, "mode", event.target.value)}><option value="min">At least</option><option value="max">At most</option><option value="range">Between</option></select>
          <Input aria-label={`${stat} target ${value.mode === "max" ? "maximum" : "minimum"}`} className="h-9 min-w-16 flex-1" type="text" inputMode="numeric" placeholder={value.mode === "max" ? "Max" : "Min"} value={value.mode === "max" ? value.max : value.min} onChange={(event) => setGoalValue(stat, value.mode === "max" ? "max" : "min", event.target.value)} />
          {value.mode === "range" && <Input aria-label={`${stat} target maximum`} className="h-9 min-w-16 flex-1" type="text" inputMode="numeric" placeholder="Max" value={value.max} onChange={(event) => setGoalValue(stat, "max", event.target.value)} />}
        </div>; })}
      </div>
      <div className="flex flex-wrap items-center gap-2"><Button className="min-h-11" disabled={busy} onClick={runSearch}>{busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}Start profile search</Button>{iteratorRef.current && !searchComplete && <Button variant="outline" className="min-h-11" disabled={busy} onClick={continueSearch}>Continue search</Button>}{iteratorRef.current && !searchComplete && <Button variant="ghost" className="min-h-11" disabled={busy} onClick={() => { abortRef.current?.abort(); iteratorRef.current = null; setSearchComplete(true); setStatus("Search cancelled."); }}>Cancel</Button>}<span className="text-xs text-muted-foreground">{status}</span></div>
      {error && <p role="status" className="text-sm text-amber-700 dark:text-amber-300">{error}</p>}
      {profile && profile.characters.length === 0 && <p className="text-sm text-muted-foreground">The optimizer profile has no characters yet.</p>}
      {pageBuilds.length > 0 && <div className="space-y-2"><p className="text-xs text-muted-foreground">This batch contains {pageBuilds.length} matching build{pageBuilds.length === 1 ? "" : "s"} · {matchCount} found in this pass{pageBuilds.some((build) => build.addedLevels > 0) ? " · requires upgrades" : " · current levels"}.</p>{pageBuilds.map((build, index) => <div key={`${build.characterId}|${build.jobName}|${build.rank}|${index}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2"><div className="min-w-0"><div className="font-medium">{build.characterName} · {build.jobName} · Rank {build.rank}</div><div className="text-xs text-muted-foreground">{build.picks.length ? build.picks.map((pick) => `${pick.item} Lv${pick.level}`).join(" · ") : "No gear needed"}{build.requiredUpgrades.length > 0 ? ` · Upgrade ${build.requiredUpgrades.map((upgrade) => `${upgrade.item} ${upgrade.currentLevel}→${upgrade.requiredLevel}`).join(", ")}` : ""}</div><div className="mt-1 flex flex-wrap gap-2 text-[11px] tabular-nums text-muted-foreground">{Object.entries(goals).filter(([, goal]) => goal).map(([stat, goal]) => <span key={stat}><StatLabel stat={stat} icons={data.statIcons} /> {build.stats[stat]?.toLocaleString()} ({goalLabelText(goal)})</span>)}</div></div><Button variant="outline" className="min-h-11" onClick={() => profile && onCreate(build, profile)}>Use this build</Button></div>)}</div>}
      {searchComplete && passPolicy === "owned-current" && status.startsWith("Search complete") && <Button variant="outline" className="min-h-11" onClick={startUpgradeSearch} disabled={busy}>Search separately for builds needing gear upgrades</Button>}
      {searchComplete && matchCount === 0 && status.startsWith("Search complete") && <p className="text-sm text-muted-foreground">Search complete. No {passPolicy === "owned-current" ? "current-level owned-gear" : "gear-upgrade"} builds reach all selected targets.</p>}
    </CardContent>
  </Card>;
}

function goalLabelText(goal: StatGoal | undefined): string {
  if (!goal) return "";
  if (goal.mode === "min") return `>= ${goal.min ?? 0}`;
  if (goal.mode === "max") return `<= ${goal.max ?? 0}`;
  return `${goal.min ?? 0}-${goal.max ?? 0}`;
}

/**
 * Ordered skill slots with a per-slot declared invocation level.
 *
 * Native invocation index: 0 = highest activation chance, 1 = ordinary (the native default), 2 =
 * lowest (`GetInvocationRate 0x15dff08` indexes the rate array directly). "Default (1)" stores no
 * declared level, so the converter reports the native default instead of a made-up percentage.
 */
const INVOCATION_LEVEL_OPTIONS = [
  { value: "", label: "Trigger: default" },
  { value: "0", label: "Trigger: high (0)" },
  { value: "1", label: "Trigger: ordinary (1)" },
  { value: "2", label: "Trigger: low (2)" },
] as const;

function SkillSlotList({
  skills,
  invocations,
  allSkills,
  onChange,
  idPrefix,
  jobName,
}: {
  skills: string[];
  invocations: Array<number | undefined>;
  allSkills: string[];
  onChange: (skills: string[], invocations: Array<number | undefined>) => void;
  idPrefix: string;
  jobName?: string;
}) {
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= skills.length) return;
    const nextSkills = [...skills];
    const nextInvocations = skills.map((_, i) => invocations[i]);
    [nextSkills[index], nextSkills[target]] = [nextSkills[target], nextSkills[index]];
    [nextInvocations[index], nextInvocations[target]] = [nextInvocations[target], nextInvocations[index]];
    onChange(nextSkills, nextInvocations);
  };
  const setLevel = (index: number, raw: string) => {
    const next = skills.map((_, i) => invocations[i]);
    next[index] = raw === "" ? undefined : Number(raw);
    onChange([...skills], next);
  };
  const remove = (index: number) => {
    const nextSkills: string[] = [];
    const nextInvocations: Array<number | undefined> = [];
    skills.forEach((skill, i) => {
      if (i === index) return;
      nextSkills.push(skill);
      nextInvocations.push(invocations[i]);
    });
    onChange(nextSkills, nextInvocations);
  };
  const add = (name: string) => {
    if (!name || skills.includes(name) || skills.length >= 9) return;
    if (jobName !== undefined && !canPickHumanSkill(jobName, name, skills)) return;
    onChange([...skills, name], [...skills.map((_, i) => invocations[i]), undefined]);
  };
  const availableSkills = allSkills.filter((name) => !skills.includes(name) && (jobName === undefined || canPickHumanSkill(jobName, name, skills)));
  return (
    <div className="space-y-2">
      <div className="space-y-1" data-skill-slots={idPrefix}>
        {skills.map((skill, index) => {
          const icon = getSkillIcon(skill);
          const level = invocations[index];
          return (
            <div key={`${skill}-${index}`} className="flex items-center gap-1 rounded-md border border-border/60 bg-muted/20 px-1.5 py-1" data-skill-slot={index}>
              <span className="flex min-w-0 flex-1 items-center gap-1.5">
                {icon && <img src={icon} alt="" className="h-4 w-4 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />}
                <span className="truncate text-xs font-medium">{skill}</span>
              </span>
              <select
                value={level === 0 || level === 1 || level === 2 ? String(level) : ""}
                onChange={(event) => setLevel(index, event.target.value)}
                aria-label={`${skill} invocation level`}
                className="h-6 rounded border border-input bg-background px-1 text-[10px] text-foreground"
              >
                {INVOCATION_LEVEL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
              <button type="button" onClick={() => move(index, -1)} disabled={index === 0} className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30" title="Move skill up (higher priority)">
                <ArrowUp className="h-3 w-3" />
              </button>
              <button type="button" onClick={() => move(index, 1)} disabled={index === skills.length - 1} className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30" title="Move skill down (lower priority)">
                <ArrowDown className="h-3 w-3" />
              </button>
              <button type="button" onClick={() => remove(index)} className="rounded p-0.5 text-muted-foreground hover:text-destructive" title="Remove skill">
                <X className="h-3 w-3" />
              </button>
            </div>
          );
        })}
        {skills.length === 0 && <span className="text-xs text-muted-foreground/60">No skills selected</span>}
      </div>
      {skills.length < 9 && availableSkills.length > 0 && (
        <SearchableSelect
          value=""
          clearOnSelect
          onChange={(v) => { if (v) add(v); }}
          options={availableSkills.map((s) => ({ value: s, label: s, icon: getSkillIcon(s) }))}
          placeholder="+ Add skill..."
          triggerClassName="min-h-11 text-sm"
        />
      )}
      {skills.length < 9 && jobName === "" && <p className="text-xs text-muted-foreground">Choose a job above to add skills.</p>}
      {skills.length < 9 && jobName && allSkills.length > 0 && availableSkills.length === 0 && <p className="text-xs text-muted-foreground">No more skills available for this job.</p>}
      {allSkills.length === 0 && <p className="text-xs text-muted-foreground/60">No skills in database yet.</p>}
    </div>
  );
}

/** One attached household pet: catalog species, declared level, parameters and ordered skills. */
function HouseholdPetRow({
  pet,
  index,
  allSkills,
  onChange,
  onRemove,
}: {
  pet: SavedLoadoutPet;
  index: number;
  allSkills: string[];
  onChange: (next: SavedLoadoutPet) => void;
  onRemove: () => void;
}) {
  const species = MONSTER_CATALOG.find((entry) => entry.id === pet.monsterId);
  const [showParameters, setShowParameters] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  return (
    <div className="space-y-2 rounded-md border border-border/60 bg-muted/15 p-2" data-household-pet={index}>
      <div className="flex flex-wrap items-center gap-2">
        <SearchableSelect
          value={species ? String(pet.monsterId) : ""}
          onChange={(v) => {
            const id = Number(v);
            const found = MONSTER_CATALOG.find((entry) => entry.id === id);
            onChange({ ...pet, monsterId: Number.isFinite(id) ? id : pet.monsterId, name: pet.name || found?.name });
          }}
          options={MONSTER_CATALOG.map((entry) => ({ value: String(entry.id), label: entry.name, icon: entry.src }))}
          placeholder="Species..."
          triggerClassName="min-h-10 min-w-36 flex-1 text-xs"
        />
        <Input
          value={pet.name ?? ""}
          onChange={(event) => onChange({ ...pet, name: event.target.value })}
          placeholder={species?.name ?? "Pet name"}
          className="min-h-10 w-32 text-xs"
          aria-label="Pet name"
        />
        {confirmRemove ? (
          <><Button type="button" variant="outline" className="min-h-10" onClick={() => setConfirmRemove(false)}>Cancel</Button>
            <Button type="button" variant="destructive" className="min-h-10" onClick={onRemove}>Remove pet</Button></>
        ) : (
          <button type="button" onClick={() => setConfirmRemove(true)} className="flex h-10 w-10 items-center justify-center rounded text-destructive hover:bg-destructive/10" title="Remove pet" aria-label="Remove pet">
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
          Level
          <Input
            type="text"
            inputMode="numeric"
            value={pet.level === undefined ? "" : String(pet.level)}
            onChange={(event) => {
              const raw = event.target.value.trim();
              onChange({ ...pet, level: raw === "" ? undefined : Math.max(1, Number(raw) || 1) });
            }}
            className="h-6 w-14 text-right text-xs tabular-nums"
            aria-label="Pet level"
          />
        </label>
        <span className="text-[10px] text-muted-foreground/70">Declared training level for every monster parameter; no stat curve is inferred.</span>
        <button type="button" onClick={() => setShowParameters((v) => !v)} className="text-[10px] text-muted-foreground underline hover:text-foreground">
          {showParameters ? "Hide parameters" : "Raw parameters"}
        </button>
      </div>
      {showParameters && (
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-4">
          {MONSTER_PARAMETER_IDS.map((id) => (
            <label key={id} className="flex items-center justify-between gap-1 text-[10px] text-muted-foreground">
              <span>Param {id}</span>
              <Input
                type="text"
                inputMode="numeric"
                value={pet.parameters?.[String(id)]?.rawValue === undefined ? "" : String(pet.parameters[String(id)]?.rawValue)}
                onChange={(event) => {
                  const raw = event.target.value.trim();
                  const parameters = { ...(pet.parameters ?? {}) };
                  const current = { ...(parameters[String(id)] ?? {}) };
                  if (raw === "") delete current.rawValue;
                  else current.rawValue = Number(raw) || 0;
                  if (Object.keys(current).length === 0) delete parameters[String(id)];
                  else parameters[String(id)] = current;
                  onChange({ ...pet, parameters: Object.keys(parameters).length > 0 ? parameters : undefined });
                }}
                className="h-6 w-16 text-right text-xs tabular-nums"
                aria-label={`Pet parameter ${id} raw value`}
              />
            </label>
          ))}
        </div>
      )}
      <SkillSlotList
        skills={pet.skills ?? []}
        invocations={pet.skills?.map((_, i) => pet.skillInvocations?.[i]) ?? []}
        allSkills={allSkills}
        onChange={(skills, invocations) =>
          onChange({
            ...pet,
            skills,
            skillInvocations: invocations.map((level) =>
              level === 0 || level === 1 || level === 2 ? level : undefined,
            ) as number[],
          })
        }
        idPrefix={`pet-${index}`}
      />
    </div>
  );
}

function LoadoutEditor({ loadout, data, onChange, onDelete, onDuplicate }: {
  loadout: Loadout;
  data: SharedData;
  onChange: (updated: Loadout) => void;
  onDelete: () => void;
  onDuplicate: () => void;
}) {
  const [renamingName, setRenamingName] = useState(false);
  const [nameVal, setNameVal] = useState(loadout.name);
  const [screenshotStatus, setScreenshotStatus] = useState<null | "working" | "ok" | "error">(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null);
  const hiddenRef = useRef<HTMLDivElement>(null);

  const jobs = data.jobs ?? {};
  const allSkills = Object.keys(data.skills ?? {}).sort();
  const statIcons = data.statIcons ?? {};
  const allEquip = Object.keys(data.overrides ?? {}).filter((n) =>
    data.slotAssignments?.[n] || (data.overrides?.[n] && Object.keys(data.overrides[n]).length > 0)
  ).sort();

  const job = jobs[loadout.jobName];
  const ranks = job ? Object.keys(job.ranks).sort() : ["S","A","B","C","D"];
  const jobStats = useMemo(() => calcJobStats(loadout, data), [loadout, data]);
  const equipStats = useMemo(() => calcEquipStats(loadout, data), [loadout, data]);
  const itemBonuses = useMemo(() => residentStatItemBonuses(loadout.residentStatItems), [loadout.residentStatItems]);
  // Job curve + valuables only: what the goal planner starts from.
  const plannerBase = useMemo(() => {
    const t = { ...jobStats };
    for (const [k, v] of Object.entries(itemBonuses)) t[k] = (t[k] ?? 0) + v;
    return t;
  }, [jobStats, itemBonuses]);
  const stats = useMemo(() => {
    const t = { ...jobStats };
    for (const [k, v] of Object.entries(equipStats)) t[k] = (t[k] ?? 0) + v;
    for (const [k, v] of Object.entries(itemBonuses)) t[k] = (t[k] ?? 0) + v;
    return t;
  }, [jobStats, equipStats, itemBonuses]);

  const upd = useCallback(<K extends keyof Loadout>(field: K, val: Loadout[K]) => {
    onChange({ ...loadout, [field]: val });
  }, [loadout, onChange]);

  const setStatLevel = (k: string, lv: number) => {
    upd("statLevels", { ...(loadout.statLevels ?? {}), [k]: Math.max(1, Math.min(999, lv)) });
  };
  const setAllStatLevels = (lv: number) => {
    const next = Math.max(1, Math.min(999, lv));
    const nextLevels: Record<string, number> = {};
    for (const k of allStatKeys) nextLevels[k] = next;
    upd("statLevels", nextLevels);
  };

  // Slot-aware equipment helpers
  const setSlotEquip = (slot: EquipSlot, name: string) => {
    if (name && !canPickHumanEquipment(loadout, data, name)) return;
    const slotMap = data.slotAssignments ?? {};
    // Remove any existing item in this slot
    const withoutSlot = loadout.equipment.filter((e) => slotMap[e.name] !== slot);
    if (!name) {
      upd("equipment", withoutSlot);
      setSlotPickerOpen((prev) => ({ ...prev, [slot]: false }));
      return;
    }
    const existingLevel = loadout.equipment.find((item) => item.name === name)?.level;
    const rememberedLevel = rememberedEquipLevels[name];
    const nextLevel = Math.max(1, Math.min(99, existingLevel ?? rememberedLevel ?? 1));
    upd("equipment", [...withoutSlot, { name, level: nextLevel }]);
    setEquipLevelInputs((prev) => ({ ...prev, [name]: String(nextLevel) }));
    setSlotPickerOpen((prev) => ({ ...prev, [slot]: false }));
  };
  const removeEquip = (i: number) => upd("equipment", loadout.equipment.filter((_, j) => j !== i));
  const setEquipLevel = (i: number, level: number) => {
    const nextLevel = Math.max(1, Math.min(99, level));
    const target = loadout.equipment[i];
    upd("equipment", loadout.equipment.map((e, j) => j === i ? { ...e, level: nextLevel } : e));
    if (target?.name) {
      setRememberedEquipLevels((prev) => ({ ...prev, [target.name]: nextLevel }));
      setEquipLevelInputs((prev) => ({ ...prev, [target.name]: String(nextLevel) }));
    }
  };

  // Skills are ordered (the game uses slot order) and each slot carries its declared invocation
  // level (native 0 = highest chance, 1 = ordinary default, 2 = lowest); `undefined` for a slot
  // means "not declared" and converts to the labelled native default.
  const setSkills = (nextSkills: string[], nextInvocations: Array<number | undefined>) => {
    onChange(dropUnsupportedHumanEquipment({
      ...loadout,
      skills: nextSkills,
      skillInvocations: nextInvocations.map((level) =>
        level === 0 || level === 1 || level === 2 ? level : undefined,
      ) as number[],
    }, data));
  };

  const takeScreenshot = async () => {
    if (!hiddenRef.current) return;
    setScreenshotStatus("working");
    try {
      const url = await toPng(hiddenRef.current, { pixelRatio: 2 });
      setScreenshotUrl(url);
      setScreenshotStatus("ok");
      setTimeout(() => setScreenshotStatus(null), 2500);
    } catch {
      setScreenshotStatus("error");
      setTimeout(() => setScreenshotStatus(null), 2500);
    }
  };

  const downloadScreenshot = () => {
    if (!screenshotUrl) return;
    const a = document.createElement("a");
    a.href = screenshotUrl;
    a.download = `${loadout.name || "loadout"}.png`;
    a.click();
  };

  const allStatKeys = [...STAT_KEYS] as string[];
  const [allLv, setAllLv] = useState(1);
  const [allLvInput, setAllLvInput] = useState("1");
  const [statLevelInputs, setStatLevelInputs] = useState<Record<string, string>>({});
  const [statValueInputs, setStatValueInputs] = useState<Record<string, string>>({});
  const [equipLevelInputs, setEquipLevelInputs] = useState<Record<string, string>>({});
  const [rememberedEquipLevels, setRememberedEquipLevels] = useState<Record<string, number>>({});
  const [slotPickerOpen, setSlotPickerOpen] = useState<Partial<Record<EquipSlot, boolean>>>({});

  useEffect(() => {
    setRememberedEquipLevels((prev) => {
      const next = { ...prev };
      for (const eq of loadout.equipment) {
        next[eq.name] = eq.level;
      }
      return next;
    });
  }, [loadout.equipment]);

  const setAllStatLevelsInput = (raw: string) => {
    if (!/^\d*$/.test(raw)) return;
    setAllLvInput(raw);
  };

  const commitAllStatLevels = (raw: string) => {
    const parsed = parseInt(raw, 10);
    const next = Math.max(1, Math.min(999, isNaN(parsed) ? 1 : parsed));
    setAllLv(next);
    setAllLvInput(String(next));
    setAllStatLevels(next);
    const nextInputs: Record<string, string> = {};
    for (const k of allStatKeys) nextInputs[k] = String(next);
    setStatLevelInputs(nextInputs);
  };

  const setStatLevelInput = (k: string, raw: string) => {
    if (!/^\d*$/.test(raw)) return;
    setStatLevelInputs((prev) => ({ ...prev, [k]: raw }));
  };

  const commitStatLevel = (k: string, raw: string) => {
    const parsed = parseInt(raw, 10);
    const next = Math.max(1, Math.min(999, isNaN(parsed) ? 1 : parsed));
    setStatLevel(k, next);
    setStatLevelInputs((prev) => ({ ...prev, [k]: String(next) }));
  };

  const setStatValueInput = (k: string, raw: string) => {
    if (!/^\d*$/.test(raw)) return;
    setStatValueInputs((prev) => ({ ...prev, [k]: raw }));
  };

  const commitStatValue = (k: string, raw: string, entry: JobStatEntry) => {
    const target = Number(raw);
    if (raw !== "" && Number.isFinite(target)) {
      setStatLevel(k, closestStatLevel(entry.base, entry.inc, target));
    }
    setStatValueInputs((prev) => {
      const next = { ...prev };
      delete next[k];
      return next;
    });
    setStatLevelInputs((prev) => {
      const next = { ...prev };
      delete next[k];
      return next;
    });
  };

  const setEquipLevelInput = (name: string, raw: string) => {
    if (!/^\d*$/.test(raw)) return;
    setEquipLevelInputs((prev) => ({ ...prev, [name]: raw }));
  };

  const commitEquipLevel = (name: string, raw: string) => {
    const parsed = parseInt(raw, 10);
    const next = Math.max(1, Math.min(99, isNaN(parsed) ? 1 : parsed));
    const idx = loadout.equipment.findIndex((entry) => entry.name === name);
    if (idx < 0) return;
    setEquipLevel(idx, next);
    setRememberedEquipLevels((prev) => ({ ...prev, [name]: next }));
    setEquipLevelInputs((prev) => ({ ...prev, [name]: String(next) }));
  };

  return (
    <div className="space-y-4">
      {/* Hidden screenshot node */}
      <div style={{ position: "absolute", top: -9999, left: -9999, pointerEvents: "none" }}>
        <div ref={hiddenRef}>
          <ScreenshotCard loadout={loadout} stats={stats} />
        </div>
      </div>

      {/* Screenshot preview modal */}
      <Dialog open={!!screenshotUrl} onOpenChange={(open) => { if (!open) setScreenshotUrl(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-sm">Screenshot ready</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground -mt-2 mb-2">Right-click the image to save, or use the download button below.</p>
          {screenshotUrl && (
            <img src={screenshotUrl} alt="Loadout screenshot" className="w-full rounded-lg border border-border" style={{ imageRendering: "auto" }} />
          )}
          <div className="flex justify-end gap-2 mt-2">
            <Button size="sm" variant="outline" onClick={() => setScreenshotUrl(null)}>Close</Button>
            <Button size="sm" onClick={downloadScreenshot} className="gap-1.5">
              <Download className="w-3.5 h-3.5" /> Download PNG
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Name + actions */}
      <div className="flex items-center gap-2">
        {renamingName ? (
          <div className="flex gap-1.5 flex-1">
            <Input value={nameVal} onChange={(e) => setNameVal(e.target.value)} className="h-8 text-sm flex-1"
              onKeyDown={(e) => { if (e.key === "Enter") { upd("name", nameVal); setRenamingName(false); } if (e.key === "Escape") { setNameVal(loadout.name); setRenamingName(false); } }}
              autoFocus />
            <button onClick={() => { upd("name", nameVal); setRenamingName(false); }} className="text-emerald-600 hover:text-emerald-700"><Check className="w-4 h-4" /></button>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 flex-1 min-w-0">
            <h3 className="font-semibold text-foreground text-sm truncate">{loadout.name || "Unnamed Loadout"}</h3>
            <button onClick={() => { setNameVal(loadout.name); setRenamingName(true); }} className="text-muted-foreground hover:text-foreground shrink-0"><Pencil className="w-3 h-3" /></button>
          </div>
        )}
        <Button size="sm" variant="outline" onClick={takeScreenshot} disabled={screenshotStatus === "working"} className={`h-8 gap-1.5 shrink-0 text-xs ${screenshotStatus === "ok" ? "text-emerald-600 border-emerald-400" : screenshotStatus === "error" ? "text-destructive border-destructive/40" : ""}`}>
          {screenshotStatus === "working" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Camera className="w-3.5 h-3.5" />}
          {screenshotStatus === "ok" ? "Saved!" : screenshotStatus === "error" ? "Failed" : "Screenshot"}
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Left: Job + Per-stat breakdown */}
        <div className="space-y-3">
          {/* Job selector */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Job</p>
            <SearchableSelect
              value={loadout.jobName}
              onChange={(v) => onChange(dropUnsupportedHumanEquipment({ ...loadout, jobName: v }, data))}
              options={Object.keys(jobs).sort().map((n) => ({ value: n, label: n }))}
              placeholder="Select job..."
              triggerClassName="h-8 text-sm"
            />
            {loadout.jobName && (
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={loadout.rank}
                  onChange={(e) => upd("rank", e.target.value)}
                  className="h-8 w-28 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                >
                  <option value="" disabled>Select rank</option>
                  {ranks.map((r) => (
                    <option key={r} value={r}>
                      Rank {r}
                    </option>
                  ))}
                </select>
                <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground" title="Declared native grow/awakening step count for this character. The per-unit cap is the Job.csv maxLevel plus 30 per step; leave blank if not declared.">
                  Awakening
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={loadout.awakening === undefined ? "" : String(loadout.awakening)}
                    onChange={(event) => {
                      const raw = event.target.value.trim();
                      upd("awakening", raw === "" ? undefined : Math.max(0, Math.min(999, Number(raw) || 0)));
                    }}
                    placeholder="-"
                    className="h-7 w-14 text-right text-xs tabular-nums"
                    aria-label="Awakening steps"
                  />
                </label>
              </div>
            )}
            {loadout.jobName && (() => {
              const slotMap = data.slotAssignments ?? {};
              const weaponEntry = loadout.equipment.find((e) => slotMap[e.name] === "Weapon");
              const shieldEntry = loadout.equipment.find((e) => slotMap[e.name] === "Shield");
              return (
                <CharacterPreview
                  jobName={loadout.jobName}
                  rank={loadout.rank || undefined}
                  weaponName={weaponEntry?.name ?? null}
                  shieldName={shieldEntry?.name ?? null}
                />
              );
            })()}
          </div>

          {/* Per-stat breakdown table */}
          {loadout.jobName && loadout.rank && (
            <div className="max-w-[580px]">
              <div className="flex items-center justify-between mb-1.5">
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Stats</p>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-muted-foreground">All Lv:</span>
                  <Input type="text" inputMode="numeric" value={allLvInput}
                    onChange={(e) => setAllStatLevelsInput(e.target.value)}
                    onKeyDown={(e) => commitOnEnter(e, () => commitAllStatLevels(e.currentTarget.value))}
                    onBlur={(e) => commitAllStatLevels(e.target.value)}
                    className="h-7 w-16 rounded-md px-2 text-right text-xs font-semibold tabular-nums" />
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs border-collapse table-fixed">
                  <colgroup>
                    <col className="w-[48%]" />
                    <col className="w-[13%]" />
                    <col className="w-[13%]" />
                    <col className="w-[13%]" />
                    <col className="w-[13%]" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="text-left pb-1 text-[10px] text-muted-foreground/60 uppercase tracking-wide font-medium">Stat level</th>
                      <th className="pb-1 text-[10px] text-muted-foreground/60 uppercase tracking-wide font-medium text-right" title="Enter a job stat value to set the closest stat level">Job value</th>
                      <th className="pb-1 text-[10px] text-muted-foreground/60 uppercase tracking-wide font-medium text-right">Equip</th>
                      <th className="pb-1 text-[10px] text-muted-foreground/60 uppercase tracking-wide font-medium text-right" title="Water of ... items used">Items</th>
                      <th className="pb-1 text-[10px] text-muted-foreground/60 uppercase tracking-wide font-medium text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allStatKeys.map((k) => {
                      const hasJob = jobStats[k] !== undefined;
                      const entry = Object.entries(job?.ranks[loadout.rank]?.stats ?? {}).find(([stat]) => normStat(stat) === k)?.[1];
                      const lv = getStatLevel(loadout, k);
                      const eq = equipStats[k];
                      const item = itemBonuses[k];
                      const total = (jobStats[k] ?? 0) + (eq ?? 0) + (item ?? 0);
                      return (
                        <tr key={k} className="group border-t border-border/30 hover:bg-muted/20 transition-colors">
                          <td className="py-1 pr-2">
                            <div className="flex items-center gap-1.5">
                              <span className="min-w-24 text-muted-foreground uppercase text-[10px] font-medium">
                                <StatLabel stat={k} icons={statIcons} full />
                              </span>
                              <Input type="text" inputMode="numeric" value={statLevelInputs[k] ?? String(lv)}
                                aria-label={`${k.toUpperCase()} stat level`}
                                onChange={(e) => setStatLevelInput(k, e.target.value)}
                                onKeyDown={(e) => commitOnEnter(e, () => commitStatLevel(k, e.currentTarget.value))}
                                onBlur={(e) => commitStatLevel(k, e.target.value)}
                                onFocus={(e) => e.currentTarget.select()}
                                className="h-7 w-16 rounded-md px-2 text-right text-xs font-semibold tabular-nums" />
                            </div>
                          </td>
                          <td className="py-0.5 pl-2 text-right tabular-nums text-foreground/80 border-l border-border/25 group-hover:bg-muted/25">
                            <span className="inline-flex items-center justify-end gap-1">
                              <span className="opacity-80 text-[10px]">
                                <StatLabel stat={k} icons={statIcons} iconClassName="h-3 w-3" />
                              </span>
                              {hasJob && entry ? (
                                <Input type="text" inputMode="numeric" value={statValueInputs[k] ?? String(jobStats[k])}
                                  aria-label={`${k.toUpperCase()} job stat value`}
                                  title="Enter a target value to set the closest stat level"
                                  onChange={(e) => setStatValueInput(k, e.target.value)}
                                  onKeyDown={(e) => commitOnEnter(e, () => commitStatValue(k, e.currentTarget.value, entry))}
                                  onBlur={(e) => commitStatValue(k, e.target.value, entry)}
                                  onFocus={(e) => e.currentTarget.select()}
                                  className="h-7 w-full min-w-16 rounded-md px-1 text-right text-xs tabular-nums" />
                              ) : <span className="text-muted-foreground/30">-</span>}
                            </span>
                          </td>
                          <td className="py-0.5 text-right tabular-nums text-sky-600 dark:text-sky-400 group-hover:bg-muted/25">{eq ? `+${eq.toLocaleString()}` : <span className="text-muted-foreground/20">-</span>}</td>
                          <td className="py-0.5 text-right tabular-nums text-amber-600 dark:text-amber-400 group-hover:bg-muted/25">{item ? `+${item.toLocaleString()}` : <span className="text-muted-foreground/20">-</span>}</td>
                          <td className="py-0.5 text-right tabular-nums font-bold group-hover:bg-muted/25">{total > 0 ? total.toLocaleString() : <span className="text-muted-foreground/30">-</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

        </div>

        {/* Right: Equipment + Skills */}
        <div className="space-y-3">
          {/* Equipment — 5-slot card grid */}
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Equipment</p>
            {(() => {
              const slotMap = data.slotAssignments ?? {};
              // Items grouped by slot
              const slotToEquip: Record<string, EquipEntry> = {};
              for (const eq of loadout.equipment) {
                const s = slotMap[eq.name];
                if (s) slotToEquip[s] = eq;
              }
              // Items in no known slot
              const extra = loadout.equipment.filter((eq) => !slotMap[eq.name]);
              return (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                    {EQUIP_SLOTS.map(({ slot, Icon }) => {
                      const eq = slotToEquip[slot];
                      const icon = eq ? getPreferredEquipmentIcon(data.equipIcons, eq.name) : null;
                      const slotItems = Object.entries(slotMap)
                        .filter(([, s]) => s === slot)
                        .map(([n]) => n)
                        .filter((name) => canPickHumanEquipment(loadout, data, name))
                        .sort();
                      return (
                        <div key={slot} className={`flex min-w-0 flex-col rounded-lg border-2 transition-colors ${eq ? "border-primary/30 bg-primary/5" : "border-dashed border-border/60 bg-muted/20"}`}>
                          {/* Slot header */}
                          <div className="flex items-center justify-between px-2 pt-2 pb-0.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground/70">{slot}</span>
                            {eq && (
                              <button onClick={() => setSlotEquip(slot as EquipSlot, "")} className="flex h-10 w-10 items-center justify-center rounded text-destructive hover:bg-destructive/10 transition-colors" aria-label={`Remove ${slot} equipment`}>
                                <X className="w-4 h-4" />
                              </button>
                            )}
                          </div>
                          {/* Icon area */}
                          <div className="flex items-center justify-center py-3 px-1">
                            {eq && icon ? (
                              <img src={icon} alt={eq.name} className="w-14 h-14 object-contain rounded" />
                            ) : eq ? (
                              <div className="w-14 h-14 rounded bg-muted/40 flex items-center justify-center">
                                <Icon className="w-7 h-7 text-muted-foreground/50" />
                              </div>
                            ) : (
                              <div className="w-14 h-14 rounded bg-muted/20 flex items-center justify-center">
                                <Icon className="w-7 h-7 text-muted-foreground/25" />
                              </div>
                            )}
                          </div>
                          {/* Item name + level or select */}
                          <div className="px-2 pb-2 space-y-1">
                            {eq ? (
                              <>
                                <button
                                  onClick={() => setSlotPickerOpen((prev) => ({ ...prev, [slot as EquipSlot]: !prev[slot as EquipSlot] }))}
                                  className="mx-auto flex min-h-10 max-w-full items-center gap-1 rounded px-1 text-[11px] font-medium text-foreground/80 hover:bg-muted"
                                  title={`Change ${slot} equipment`}
                                >
                                  <span className="line-clamp-2 min-h-[30px] text-center leading-tight">{eq.name}</span>
                                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                </button>
                                {slotPickerOpen[slot as EquipSlot] && (
                                  <SearchableSelect
                                    value=""
                                    clearOnSelect
                                    onChange={(v) => { if (v) setSlotEquip(slot as EquipSlot, v); }}
                                    options={slotItems.map((n) => ({ value: n, label: n }))}
                                    placeholder="Change equipment..."
                                    triggerClassName="min-h-10 text-[10px]"
                                  />
                                )}
                                {/* Weapon proficiency badge */}
                                {(() => {
                                  const rule = getEquipRuleState(loadout, data, eq.name);
                                  if (!rule.prof || rule.prof === "can") return null;
                                  const isWeak = rule.prof === "weak";
                                  return (
                                    <div className="text-center space-y-0.5">
                                      <ToneBadge category={isWeak ? "shop" : "warning"} className="inline-block px-1.5 py-0.5 rounded text-[9px] font-bold">
                                        {isWeak
                                          ? (rule.appliesPenalty ? "Weak: 50%" : "Weak removed")
                                          : "Cannot wield"}
                                      </ToneBadge>
                                      {rule.blocked ? (
                                        <p className="text-[8px] text-muted-foreground leading-tight">
                                          This item is ignored in stat totals
                                        </p>
                                      ) : rule.resistanceSkill && (
                                        <p className="text-[8px] text-muted-foreground leading-tight">
                                          {rule.hasResistanceSkillEquipped
                                            ? `${rule.resistanceSkill.name} restores full stats`
                                            : `${rule.resistanceSkill.name} removes this penalty`}
                                        </p>
                                      )}
                                    </div>
                                  );
                                })()}
                                <div className="flex items-center gap-1 justify-center">
                                  <span className="text-xs text-muted-foreground">Lv</span>
                                  <Input type="text" inputMode="numeric" value={equipLevelInputs[eq.name] ?? String(eq.level)}
                                    onChange={(e) => setEquipLevelInput(eq.name, e.target.value)}
                                    onKeyDown={(e) => commitOnEnter(e, () => commitEquipLevel(eq.name, e.currentTarget.value))}
                                    onBlur={(e) => commitEquipLevel(eq.name, e.target.value)}
                                    onFocus={(e) => e.currentTarget.select()}
                                    className="h-7 w-16 rounded-md px-2 text-right text-xs font-semibold tabular-nums" />
                                </div>
                              </>
                            ) : (
                              <SearchableSelect
                                value=""
                                clearOnSelect
                                onChange={(v) => { if (v) setSlotEquip(slot as EquipSlot, v); }}
                                options={slotItems.map((n) => ({ value: n, label: n }))}
                                placeholder="- empty -"
                                triggerClassName="min-h-10 text-[10px]"
                              />
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {/* Unassigned items (no slot in db) */}
                  {extra.length > 0 && (
                    <div>
                      <p className="text-[10px] text-muted-foreground/60 font-medium uppercase tracking-wider mb-1">Other</p>
                      <div className="space-y-1">
                        {extra.map((eq) => {
                          const idx = loadout.equipment.findIndex((e) => e.name === eq.name);
                          return (
                            <div key={eq.name} className="flex items-center gap-1.5 bg-muted/30 rounded-md px-2 py-1">
                              {getPreferredEquipmentIcon(data.equipIcons, eq.name) ? <img src={getPreferredEquipmentIcon(data.equipIcons, eq.name)} alt="" className="w-4 h-4 object-contain" /> : <div className="w-4 h-4" />}
                              <span className="text-xs flex-1 truncate font-medium">{eq.name}</span>
                              <span className="text-[10px] text-muted-foreground">Lv</span>
                              <Input type="text" inputMode="numeric" value={equipLevelInputs[eq.name] ?? String(eq.level)}
                                onChange={(e) => setEquipLevelInput(eq.name, e.target.value)}
                                onKeyDown={(e) => commitOnEnter(e, () => commitEquipLevel(eq.name, e.currentTarget.value))}
                                onBlur={(e) => commitEquipLevel(eq.name, e.target.value)}
                                className="h-5 text-[10px] text-center w-12 px-0" />
                              <button onClick={() => removeEquip(idx)} className="text-muted-foreground hover:text-destructive"><X className="w-3 h-3" /></button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
          </div>

          {/* Skills */}
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Skills <span className="normal-case font-normal">({loadout.skills.length}/9, priority order)</span>
            </p>
            <SkillSlotList
              skills={loadout.skills}
              invocations={loadout.skills.map((_, i) => loadout.skillInvocations?.[i])}
              allSkills={allSkills}
              onChange={setSkills}
              idPrefix={loadout.id}
              jobName={loadout.jobName}
            />
            <p className="mt-1 text-[10px] leading-tight text-muted-foreground/70">
              Order is the slot priority sent to the simulator. Trigger is the native invocation index (0 high, 1 ordinary default, 2 low).
            </p>
          </div>

          {/* Household pets */}
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Household pets <span className="normal-case font-normal">({loadout.householdPets?.length ?? 0})</span>
              </p>
              <label
                className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
                title={
                  (loadout.householdPets?.length ?? 0) > 0
                    ? "Remove the attached pets individually before un-declaring this resident."
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={loadout.householdPets !== undefined}
                  disabled={(loadout.householdPets?.length ?? 0) > 0}
                  onChange={(event) => upd("householdPets", event.target.checked ? [] : undefined)}
                />
                This resident is a house owner
              </label>
            </div>
            {loadout.householdPets === undefined ? (
              <p className="text-[11px] leading-tight text-muted-foreground/70">
                Not declared a house owner. Declaring is explicit: an owner either brings the complete list below or explicitly brings none.
              </p>
            ) : (
              <div className="space-y-2">
                {loadout.householdPets.map((pet, index) => (
                  <HouseholdPetRow
                    key={`${pet.monsterId}-${index}`}
                    pet={pet}
                    index={index}
                    allSkills={allSkills}
                    onChange={(next) => upd("householdPets", (loadout.householdPets ?? []).map((entry, i) => (i === index ? next : entry)))}
                    onRemove={() => upd("householdPets", (loadout.householdPets ?? []).filter((_, i) => i !== index))}
                  />
                ))}
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1 text-xs"
                  onClick={() => upd("householdPets", [...(loadout.householdPets ?? []), { monsterId: MONSTER_CATALOG[0]?.id ?? 116 }])}
                >
                  <Plus className="h-3 w-3" /> Add pet
                </Button>
                <p className="text-[10px] leading-tight text-muted-foreground/70">
                  Pet capacity and placement come from the backend preview on the Player Battle page; this page never positions or caps pets.
                </p>
                <p className="text-[10px] leading-tight text-muted-foreground/70">
                  Pet stats are not derived: a pet with no entered level or raw parameters keeps empty defaults, so its combat parameters
                  must be entered explicitly to be real.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Stat goal planner (only exists while this loadout is expanded) */}
        <GoalPlannerPanel
          loadout={loadout}
          data={data}
          baseStats={plannerBase}
          onChange={onChange}
          onApply={(picks) => {
            // Replace only the suggested slots so gear in other slots is kept.
            const slotMap = data.slotAssignments ?? {};
            const touched = new Set(picks.map((pick) => slotMap[pick.name]));
            const kept = loadout.equipment.filter((entry) => !touched.has(slotMap[entry.name]));
            upd("equipment", [
              ...kept,
              ...picks.map((pick) => ({ name: pick.name, level: Math.max(1, Math.min(99, pick.level)) })),
            ]);
          }}
        />
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function PublishNameDialog({
  onSave,
  onCancel,
}: {
  onSave: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(() => localStorage.getItem("ka_username") ?? "");
  const trimmed = value.trim();

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="max-w-xs">
        <DialogHeader>
          <DialogTitle>Publish setup</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">Enter your name so people know who published this setup.</p>
        <Input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Your name"
          className="h-8 text-sm"
          onKeyDown={(event) => {
            if (event.key === "Enter" && trimmed) onSave(trimmed);
          }}
        />
        <Button onClick={() => trimmed && onSave(trimmed)} disabled={!trimmed} className="h-8">
          Publish
        </Button>
      </DialogContent>
    </Dialog>
  );
}

export default function LoadoutPage() {
  const { data, isLoading } = useSharedData();
  const queryClient = useQueryClient();
  const { loadouts, save } = usePrivateLoadouts();
  const { setups, save: saveSetups } = useCommunityBoxSetups(data);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | "all" | null>(null);
  const [pageNote, setPageNote] = useLocalFeature<string>("ka_note_loadout", "");
  const [showNote, setShowNote] = useState(false);
  // Universal "Water of ..." valuables: entered once for the whole page and
  // mirrored onto every loadout so all stat totals pick them up.
  const [residentItems, setResidentItems] = useLocalFeature<ResidentStatItemCounts>(RESIDENT_STAT_ITEMS_KEY, {});
  const residentMigrationRef = useRef(false);
  const [activeToolTab, setActiveToolTab] = useState<BoxSetupKind | "combat">("kairo");
  const [sharedSetup, setSharedSetup] = useState<BoxSetupShare | null>(null);
  const [shareLoadError, setShareLoadError] = useState<string | null>(null);
  const [publishNameSetup, setPublishNameSetup] = useState<BoxSetup | null>(null);
  const [publishStatus, setPublishStatus] = useState<Partial<Record<BoxSetupKind, "working" | "ok" | "error">>>({});

  const addLoadout = () => {
    const id = generateId();
    const newLoadout: Loadout = { id, name: "New Character", jobName: "", rank: "", statLevels: {}, equipment: [], skills: [] };
    save([...loadouts, newLoadout]);
    setExpandedId(id);
    return id;
  };

  const applyProfileBuild = (build: ProfileGoalBuild, profile: PlayerProfile) => {
    const source = profile.characters.find((character) => character.id === build.characterId);
    if (!source) return;
    const state = source.state;
    const waters = Object.fromEntries(["life", "wisdom", "vitality", "might", "resilience"].map((key) => [key, profile.valuables.active[key] as number]));
    const buildJob = build.jobName ?? state.jobName ?? "";
    const legalSkills: string[] = [];
    const invocations: number[] = [];
    (state.skills ?? []).forEach((skill, index) => {
      if (!profile.ownedSkills.includes(skill) || !canPickHumanSkill(buildJob, skill, legalSkills)) return;
      legalSkills.push(skill);
      invocations.push(state.skillInvocations?.[index] ?? 1);
    });
    const newLoadout: Loadout = {
      id: generateId(), name: `${state.name || build.characterName} · ${build.jobName} ${build.rank}`,
      jobName: buildJob, rank: build.rank ?? state.rank ?? "",
      level: state.level, statLevels: state.statLevels ?? {},
      equipment: build.picks.map(({ item, level }) => ({ name: item, level })),
      skills: legalSkills, awakening: state.awakening, skillInvocations: invocations,
      householdPets: state.householdPets, residentStatItems: waters, residentStatItemsSource: "profile",
    };
    save([...loadouts, newLoadout]); setExpandedId(newLoadout.id);
  };

  const updateLoadout = useCallback((updated: Loadout) => {
    save(loadouts.map((l) => l.id === updated.id ? updated : l));
  }, [loadouts, save]);

  const deleteLoadout = useCallback((id: string) => {
    save(loadouts.filter((l) => l.id !== id));
    if (expandedId === id) setExpandedId(null);
  }, [loadouts, save, expandedId]);

  const duplicateLoadout = useCallback((id: string) => {
    const source = loadouts.find((l) => l.id === id);
    if (!source) return;
    const newId = generateId();
    const duplicate: Loadout = { ...source, id: newId, name: `Copy of ${source.name}` };
    save([...loadouts, duplicate]);
    setExpandedId(newId);
  }, [loadouts, save]);

  // One-time: adopt counts an older loadout may already carry.
  useEffect(() => {
    if (residentMigrationRef.current) return;
    if (loadouts.length === 0 && !data) return;
    residentMigrationRef.current = true;
    if (residentItemsSome(residentItems)) return;
    const carried = loadouts.filter((l) => l.residentStatItemsSource !== "profile").map((l) => l.residentStatItems).find((counts) => residentItemsSome(counts));
    if (carried) setResidentItems({ ...carried });
  }, [loadouts, data, residentItems, setResidentItems]);

  // Keep every loadout in step with the universal setting.
  useEffect(() => {
    if (loadouts.length === 0) return;
    if (loadouts.every((l) => l.residentStatItemsSource === "profile" || residentItemsEqual(l.residentStatItems, residentItems))) return;
    save(loadouts.map((l) => l.residentStatItemsSource === "profile" ? l : ({ ...l, residentStatItems: { ...residentItems } })));
  }, [loadouts, residentItems, save]);

  const updateSetup = useCallback((updated: BoxSetup) => {
    saveSetups(setups.map((setup) => setup.id === updated.id ? updated : setup));
  }, [setups, saveSetups]);

  const publishSetupWithName = useCallback(async (setup: BoxSetup, name: string) => {
    const publisher = name.trim();
    if (!publisher) return;
    localStorage.setItem("ka_username", publisher);
    setPublishNameSetup(null);
    setPublishStatus((prev) => ({ ...prev, [setup.id]: "working" }));

    const now = Date.now();
    const publishedSetup: BoxSetup = { ...setup, updatedAt: now, publishedAt: now, publishedBy: publisher };
    const nextSetups = normalizeBoxSetups(setups.map((item) => item.id === setup.id ? publishedSetup : item));

    try {
      const response = await fetch(apiUrl("/loadout-box-setups"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data: nextSetups,
          history: {
            userName: publisher,
            changeType: "loadout",
            itemName: setup.title,
            description: `Published ${setup.title}`,
          },
        }),
      });
      if (!response.ok) throw new Error("Publish failed");
      saveSetups(nextSetups);
      queryClient.invalidateQueries({ queryKey: ["ka-shared"] });
      setPublishStatus((prev) => ({ ...prev, [setup.id]: "ok" }));
    } catch {
      setPublishStatus((prev) => ({ ...prev, [setup.id]: "error" }));
    } finally {
      setTimeout(() => {
        setPublishStatus((prev) => {
          const next = { ...prev };
          delete next[setup.id];
          return next;
        });
      }, 2500);
    }
  }, [queryClient, saveSetups, setups]);

  const publishSetup = useCallback((setup: BoxSetup) => {
    const savedName = localStorage.getItem("ka_username")?.trim();
    if (savedName) {
      publishSetupWithName(setup, savedName);
      return;
    }
    setPublishNameSetup(setup);
  }, [publishSetupWithName]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const shareId = params.get("share");
    const setupKind = params.get("setup");
    if (!shareId) return;
    let cancelled = false;
    setShareLoadError(null);
    fetch(apiUrl(`/loadout-box-setups/share/${encodeURIComponent(shareId)}`))
      .then((response) => {
        if (!response.ok) throw new Error("Shared setup not found");
        return response.json() as Promise<BoxSetupShare>;
      })
      .then((share) => {
        if (cancelled) return;
        const normalized = normalizeBoxSetups([share.setup]).find((setup) => setup.id === share.setup.id || setup.id === setupKind);
        if (!normalized) throw new Error("Shared setup is not a Kairo or Wairo setup");
        setSharedSetup({ ...share, setup: normalized });
        setActiveToolTab(normalized.id);
      })
      .catch((error: Error) => {
        if (!cancelled) setShareLoadError(error.message || "Could not load shared setup");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen bg-background transition-colors">
      {publishNameSetup && (
        <PublishNameDialog
          onSave={(name) => publishSetupWithName(publishNameSetup, name)}
          onCancel={() => setPublishNameSetup(null)}
        />
      )}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Delete {pendingDeleteId === "all" ? "all characters" : "character"}?</DialogTitle></DialogHeader>
          <div className="flex justify-end gap-3">
            <Button type="button" variant="outline" className="min-h-11" onClick={() => setPendingDeleteId(null)}>Cancel</Button>
            <Button type="button" variant="destructive" className="min-h-11" onClick={() => { if (pendingDeleteId === "all") save([]); else if (pendingDeleteId) deleteLoadout(pendingDeleteId); setPendingDeleteId(null); }}>Delete</Button>
          </div>
        </DialogContent>
      </Dialog>
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="max-w-7xl mx-auto px-4 py-3">
          <PageHeader
            icon={<Package className="w-5 h-5 text-orange-500" />}
            title="Characters"
            actions={(
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="ghost" size="icon" onClick={() => setShowNote((v) => !v)} className="h-8 w-8 text-muted-foreground" title="Personal notes (private, stored on this device)">
                  <Info className="w-3.5 h-3.5" />
                </Button>
                <Button variant="ghost" size="icon" onClick={() => setPendingDeleteId("all")} className="h-11 w-11 text-destructive" title="Delete all characters">
                  <RotateCcw className="w-3.5 h-3.5" />
                </Button>
                <Link href="/battle" data-action="open-player-battle" className="inline-flex">
                  <Button size="sm" className="h-8 gap-1.5 bg-emerald-600 text-white hover:bg-emerald-600/90">
                    <Sword className="w-3.5 h-3.5" />Player Battle
                  </Button>
                </Link>
                <Button size="sm" onClick={addLoadout} className="min-h-11 gap-1.5">
                  <Plus className="w-3.5 h-3.5" />New Character
                </Button>
              </div>
            )}
          />
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6">
        {showNote && (
          <div className="mb-4">
            <textarea
              value={pageNote}
              onChange={(e) => setPageNote(e.target.value)}
              placeholder="Personal notes for this page... (only visible to you, saved on this device)"
              className="w-full h-20 text-sm rounded-md border border-input bg-muted/20 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground/40"
            />
          </div>
        )}

        {/* Universal settings: resident-wide valuables ("Water of ...") */}
        <Card className="mb-5">
          <CardHeader className="pb-2 pt-4">
            <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
              Universal Settings
              <span className="text-[11px] font-normal text-muted-foreground">
                how many valuables you have used - applies to every resident
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pb-4">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {RESIDENT_STAT_ITEMS.map((item) => {
                const used = Math.max(0, Math.floor(residentItems[item.key] ?? 0));
                const bonus = used * item.amount;
                return (
                  <label key={item.key} className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/15 px-2 py-1.5">
                    <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground/80" title={item.name}>
                      {item.name}
                    </span>
                    <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-amber-600 dark:text-amber-400">
                      {bonus ? `+${bonus}` : ""}
                    </span>
                    <CompactNumberInput value={used} min={0} max={99} ariaLabel={`${item.name} used`} className="w-14 shrink-0"
                      onValueChange={(value) => setResidentItems({ ...residentItems, [item.key]: value })} />
                  </label>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] leading-tight text-muted-foreground">
              Each one raises that stat by {RESIDENT_STAT_ITEMS[0].amount} for all residents (original Valuable table). There is no equivalent item for
              Speed, Luck, Intelligence, Dexterity, Gather, Move or Heart. These values are copied onto every loadout below.
            </p>
          </CardContent>
        </Card>

        {!isLoading && data && <ProfileGoalSearchPanel data={data} onCreate={applyProfileBuild} />}

        {isLoading && (
          <div className="flex items-center justify-center py-20"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
        )}

        {!isLoading && loadouts.length === 0 && (
          <div className="text-center py-20">
            <Package className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm">No characters yet.</p>
            <Button onClick={addLoadout} className="min-h-11 gap-1.5"><Plus className="w-4 h-4" />Create Character</Button>
          </div>
        )}

        <div className="grid gap-3 xl:grid-cols-2">
          {loadouts.map((loadout) => {
            const isOpen = expandedId === loadout.id;
            const job = data?.jobs?.[loadout.jobName];
            const stats = data ? calcStats(loadout, data) : {};
            const hasStats = STAT_KEYS.some((k) => stats[k]);

            return (
              <Card key={loadout.id} className={`shadow-sm overflow-hidden ${isOpen ? "xl:col-span-2" : ""}`}>
                {/* Summary bar */}
                <div
                  role="button"
                  tabIndex={0}
                  className="w-full cursor-pointer text-left"
                  onClick={() => setExpandedId(isOpen ? null : loadout.id)}
                  onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setExpandedId(isOpen ? null : loadout.id); } }}
                >
                  <CardHeader className="py-3 px-4 hover:bg-muted/30 transition-colors">
                    {/* Row 1: chevron + name + job + rank + duplicate */}
                    <div className="flex items-center gap-2.5 flex-wrap">
                      {isOpen ? <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" /> : <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />}
                      <CardTitle className="text-base font-bold truncate flex-1 min-w-0">
                        {loadout.name || "Unnamed Loadout"}
                      </CardTitle>
                      {loadout.jobName && (
                        <span className="text-sm font-bold text-primary shrink-0">{loadout.jobName}</span>
                      )}
                      {loadout.rank && (
                        <Badge variant="outline" className={`text-xs px-2 py-0.5 border font-semibold ${RANK_COLORS[loadout.rank] ?? ""} shrink-0`}>
                          Rank {loadout.rank}
                        </Badge>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); duplicateLoadout(loadout.id); }}
                        className="flex h-11 w-11 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-primary"
                        title="Duplicate this loadout"
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setPendingDeleteId(loadout.id); }}
                        className="flex h-11 w-11 shrink-0 items-center justify-center rounded text-destructive hover:bg-destructive/10"
                        title="Delete this character"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {/* Collapsed detail rows */}
                    {!isOpen && (() => {
                      const slotMap = data?.slotAssignments ?? {};
                      const weaponEntry = loadout.equipment.find((e) => slotMap[e.name] === "Weapon");
                      const shieldEntry = loadout.equipment.find((e) => slotMap[e.name] === "Shield");
                      return (
                      <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(150px,205px)_minmax(0,1fr)] sm:items-start">
                        {hasStats && (
                          <div className="sm:col-span-2 px-1 py-1">
                            <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:max-w-[620px]">
                              {STAT_COLUMNS.map((column, columnIndex) => {
                                const visibleStats = column.filter((k) => stats[k]);
                                if (visibleStats.length === 0) return null;
                                return (
                                  <div key={columnIndex} className="flex min-w-0 flex-col gap-1.5">
                                    {visibleStats.map((k) => (
                                      <span key={k} className="inline-flex min-w-0 items-center gap-1.5 text-sm leading-none tabular-nums text-muted-foreground">
                                        <StatLabel stat={k} icons={data?.statIcons} iconClassName="h-4.5 w-4.5" />
                                        <strong className="text-base leading-none text-foreground">{stats[k].toLocaleString()}</strong>
                                      </span>
                                    ))}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                        <div className="min-w-0">
                          {loadout.jobName && (
                            <div className="flex min-h-[155px] items-start justify-center pt-1">
                              <CollapsedCharPreview
                                jobName={loadout.jobName}
                                rank={loadout.rank}
                                weaponName={weaponEntry?.name ?? null}
                                shieldName={shieldEntry?.name ?? null}
                              />
                            </div>
                          )}
                        </div>
                        <div className="min-w-0 space-y-2">
                          {loadout.equipment.length > 0 && (
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5 lg:gap-3">
                              {loadout.equipment.map((eq) => {
                                const icon = getPreferredEquipmentIcon(data?.equipIcons, eq.name);
                                const slot = data?.slotAssignments?.[eq.name];
                                const rule = data ? getEquipRuleState(loadout, data, eq.name) : null;
                                return (
                                  <span key={eq.name} className="flex min-w-0 flex-col items-center gap-1 rounded-md border border-border/40 bg-muted/10 px-2 py-1.5 text-center">
                                    <span className="relative h-[104px] w-[94px] shrink-0">
                                      <span className="mx-auto flex h-[86px] w-[86px] items-center justify-center overflow-hidden rounded-md border border-border/50 bg-muted/30 p-1">
                                        {icon ? (
                                          <CollapsedEquipmentIcon src={icon} alt={eq.name} />
                                        ) : (
                                          <span className="flex h-[86px] w-[86px] items-center justify-center text-xs text-muted-foreground">?</span>
                                        )}
                                      </span>
                                      <span className="absolute bottom-0 right-0 rounded-sm bg-background/95 px-1 text-[10px] font-bold leading-4 text-muted-foreground shadow-sm ring-1 ring-border/60">Lv{eq.level}</span>
                                    </span>
                                    <span className="text-[9px] font-semibold uppercase leading-none text-muted-foreground/70">{slot ?? "Gear"}</span>
                                    <span className="line-clamp-2 min-h-6 text-[10px] font-medium leading-tight text-foreground">{eq.name}</span>
                                    {rule?.blocked && <span className="text-[9px] font-semibold leading-none text-orange-600 dark:text-orange-400">Can't</span>}
                                    {rule?.appliesPenalty && <span className="text-[9px] font-semibold leading-none text-amber-600 dark:text-amber-400">Weak</span>}
                                  </span>
                                );
                              })}
                            </div>
                          )}
                          {loadout.skills.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1">
                              {loadout.skills.map((s) => (
                                <ToneBadge key={s} category="skill" className="inline-block rounded-md px-1.5 py-0.5 text-[10px]">
                                  {s}
                                </ToneBadge>
                              ))}
                            </div>
                          )}
                          {(loadout.householdPets?.length ?? 0) > 0 && (
                            <div className="flex flex-wrap gap-1" aria-label="Character pets">
                              {loadout.householdPets?.map((pet, index) => (
                                <span key={`${pet.monsterId}-${index}`} className="rounded border px-2 py-1 text-xs">
                                  {pet.name || MONSTER_CATALOG.find((monster) => monster.id === pet.monsterId)?.name || `Pet ${index + 1}`}
                                </span>
                              ))}
                            </div>
                          )}
                          {!hasStats && loadout.equipment.length === 0 && loadout.skills.length === 0 && !loadout.householdPets?.length && (
                            <span className="text-xs text-muted-foreground/50">Empty character</span>
                          )}
                        </div>
                      </div>
                      );
                    })()}
                  </CardHeader>
                </div>

                {isOpen && (
                  <>
                    <Separator />
                    <CardContent className="p-4">
                      {data ? (
                        <LoadoutEditor
                          loadout={loadout}
                          data={data}
                          onChange={updateLoadout}
                          onDelete={() => setPendingDeleteId(loadout.id)}
                          onDuplicate={() => duplicateLoadout(loadout.id)}
                        />
                      ) : (
                        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
                      )}
                    </CardContent>
                  </>
                )}
              </Card>
            );
          })}
        </div>

        {loadouts.length > 0 && !isLoading && (
          <button onClick={addLoadout}
            className="w-full mt-4 flex items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border hover:border-primary/40 hover:bg-muted/30 transition-colors py-4 text-sm text-muted-foreground hover:text-foreground">
            <Plus className="w-4 h-4" />Add another character
          </button>
        )}

        {!isLoading && data && (
          <div className="mt-6 space-y-3">
            {(sharedSetup || shareLoadError) && (
              <div className={`rounded-md border px-3 py-2 text-xs ${shareLoadError ? "border-destructive/40 bg-destructive/10 text-destructive" : "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-200"}`}>
                {shareLoadError ? shareLoadError : `Viewing shared setup link: ${sharedSetup?.setup.title}. Try match uses your private loadouts.`}
              </div>
            )}
            <div className="flex flex-wrap gap-2 rounded-lg border border-border bg-card p-2">
              {setups.map((setup) => (
                <button
                  key={setup.id}
                  onClick={() => setActiveToolTab(setup.id)}
                  className={`h-9 rounded-md px-3 text-sm font-medium transition-colors ${activeToolTab === setup.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
                >
                  {setup.title}
                </button>
              ))}
              <button
                onClick={() => setActiveToolTab("combat")}
                className={`h-9 rounded-md px-3 text-sm font-medium transition-colors ${activeToolTab === "combat" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
              >
                Combat Sandbox
              </button>
            </div>
            <p className="px-1 text-[11px] text-muted-foreground">
              Multi Box Kairo/Wairo setups and the Combat Sandbox are community research tools with a simplified model - not the authoritative player battle.{" "}
              <Link href="/battle" className="font-medium text-emerald-600 underline dark:text-emerald-400">Open Player Battle</Link>{" "}
              for a real run.
            </p>

            {setups.map((setup) => (
              activeToolTab === setup.id ? (
                <BoxSetupCard
                  key={setup.id}
                  setup={sharedSetup?.setup.id === setup.id ? sharedSetup.setup : setup}
                  loadouts={loadouts}
                  data={data}
                  onChange={sharedSetup?.setup.id === setup.id ? () => {} : updateSetup}
                  onPublish={publishSetup}
                  publishStatus={publishStatus[setup.id]}
                  onCreateLoadout={addLoadout}
                  readOnly={sharedSetup?.setup.id === setup.id}
                />
              ) : null
            ))}

            {activeToolTab === "combat" && (
              <LoadoutCombatTool loadouts={loadouts} data={data} />
            )}
          </div>
        )}

        <p className="text-xs text-muted-foreground mt-4 text-center">
          Characters are saved in this browser
        </p>
      </div>
    </div>
  );
}
