import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { ArrowDown, ArrowUp, Plus, RotateCcw, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ka/page-header";
import { BattleComparisonResult } from "@/components/ka/battle-comparison";
import { BattleSetupAdapterError } from "@/lib/battle-setup-adapter";
import { runBattleSetup } from "@/lib/battle-setup-runner";
import { startBrowserBattle } from "@/lib/browser-battle";
import { apiUrl } from "@/lib/api";
import { useLocalFeature } from "@/hooks/sync/use-local-feature";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import {
  battleSetupFromLoadouts,
  readLoadoutHandoff,
  type SavedLoadout,
  type SetupFieldOrigin,
  type SharedLoadoutData,
} from "@/lib/battle-legality";
import { normalizeSavedLoadouts } from "@/lib/battle-team-draft";
import {
  RESIDENT_STAT_ITEMS_KEY,
  deviceResidentValuables,
  loadoutsWithResidentValuables,
} from "@/lib/resident-valuable-settings";
import { writeGeneratedBattle } from "@/lib/generated-battle-store";
import {
  USER_WAIRO_PRESET,
  USER_WAIRO_PRESET_ASSUMED,
  USER_WAIRO_PRESET_KNOWN,
  userWairoPresetConversionOptions,
  userWairoPresetLoadouts,
} from "@/lib/user-wairo-preset";
import {
  BattleComparisonError,
  buildEvaluationRequest,
  buildSearchRequest,
  comparisonClaim,
  comparisonItemBudget,
  comparisonPolicyProblems,
  comparisonPolicySummary,
  createComparisonCandidate,
  defaultComparisonSettings,
  runComparison,
  runSearchEnvelope,
  searchBudget,
  storeSearchWinnerReplay,
  storeWinnerReplay,
  type BattleEvaluationResult,
  type BattleSearchResult,
  type ComparisonCandidate,
  type ComparisonLevel,
  type ComparisonSettings,
  type ComparisonStrategy,
} from "@/lib/battle-comparison";
import {
  DIFFICULTY_NAMES,
  ENCOUNTER_BY_ID,
  ENCOUNTER_FAMILIES,
  ENCOUNTER_VARIANTS,
  EQUIPMENT_BY_ID,
  EQUIPMENT_SLOT_ORDER,
  GENDER_INDICES,
  JOB_BY_ID,
  JOB_CATALOG,
  MONSTER_BY_ID,
  MONSTER_CATALOG,
  CANONICAL_RECOVERY_ITEMS,
  LARGE_POTION_ITEM,
  LARGE_POTION_UNAVAILABLE_REASON,
  PARTY_LIMIT_INITIAL_MAX,
  SKILL_BY_ID,
  SKILL_CATALOG,
  SKILL_SLOT_CAP,
  START_PROFILE_DECLARED_NOTE,
  START_PROFILE_KIND,
  AUTOMATIC_FINISH_STATUS_NOTE,
  createDefaultBattleSetup,
  createDefaultStartProfile,
  createHumanUnit,
  createPetMonsterUnit,
  declaredItemRows,
  canonicalRecoveryItem,
  difficultyName,
  emptyEquipmentSlots,
  emptyParameters,
  encounterCoverage,
  equipmentForSlot,
  importBattleSetup,
  isPlayerFacingBattleIssue,
  serializeBattleSetup,
  validateBattleSetup,
  type BattleSetup,
  type BattleInput,
  type EquipmentSlotKey,
  type HumanUnit,
  type PetMonsterUnit,
  type PlayerUnit,
  type FinishPolicy,
  type SetupIssueCategory,
  type SkillSelection,
  type StartProfile,
} from "@/lib/battle-setup";

const loadoutAwareTransport = startBrowserBattle;

/**
 * Shared job/equipment/skill data for the loadout import. Deliberately not react-query here: this
 * page is also rendered by the stand-alone browser harness without a query provider, and
 * `fetchSharedWithFallback` already falls back to the bundled local shared data.
 */
function useSharedLoadoutData(): SharedLoadoutData | null {
  const [data, setData] = useState<SharedLoadoutData | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchSharedWithFallback<SharedLoadoutData>(apiUrl("/shared"))
      .then((loaded) => {
        if (!cancelled) setData(loaded);
      })
      .catch(() => {
        /* the local fallback is returned instead of throwing */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return data;
}

function categoryVariant(category: SetupIssueCategory) {
  if (category === "ERROR") return "destructive" as const;
  if (category === "WARNING") return "secondary" as const;
  return "outline" as const;
}

function coverageLabel(coverage: "FULL" | "PARTIAL") {
  return coverage === "FULL" ? "FULL" : "PARTIAL";
}

function NumberField({
  value,
  onChange,
  dataAttribute,
}: {
  value: number;
  onChange: (value: number) => void;
  dataAttribute?: Record<string, string | number>;
}) {
  return (
    <Input
      type="number"
      className="h-7 w-20 text-xs"
      value={Number.isFinite(value) ? value : 0}
      onChange={(event) => onChange(Number(event.target.value))}
      {...dataAttribute}
    />
  );
}

function ParameterEditor({
  unit,
  onChange,
}: {
  unit: PlayerUnit;
  onChange: (parameters: PlayerUnit["parameters"]) => void;
}) {
  const ids = Object.keys(unit.parameters).sort((a, b) => Number(a) - Number(b));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-parameter-table>
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="px-1 py-1">Param</th>
            <th className="px-1 py-1">rawValue</th>
            <th className="px-1 py-1">rawMax</th>
            <th className="px-1 py-1">extraValue</th>
            <th className="px-1 py-1">extraMax</th>
            <th className="px-1 py-1">trainingLevel</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((id) => {
            const parameter = unit.parameters[id];
            return (
              <tr key={id} className="border-b last:border-0" data-parameter-id={id}>
                <td className="px-1 py-1 font-mono">{id}</td>
                {(["rawValue", "rawMax", "extraValue", "extraMax", "trainingLevel"] as const).map(
                  (field) => (
                    <td key={field} className="px-1 py-1">
                      <NumberField
                        value={parameter[field]}
                        onChange={(value) =>
                          onChange({
                            ...unit.parameters,
                            [id]: { ...parameter, [field]: value },
                          })
                        }
                        dataAttribute={{ [`data-param-${field}`]: id }}
                      />
                    </td>
                  ),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SkillEditor({
  unit,
  onChange,
}: {
  unit: PlayerUnit;
  onChange: (skills: SkillSelection[]) => void;
}) {
  const move = (index: number, direction: -1 | 1) => {
    const next = [...unit.skills];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Skills (ordered)</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-action="add-skill"
          onClick={() => onChange([...unit.skills, { skillId: SKILL_CATALOG[0]?.id ?? 0, invocationLevel: 1 }])}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> Add skill
        </Button>
      </div>
      {unit.skills.length === 0 ? (
        <p className="text-xs text-muted-foreground">No skills selected.</p>
      ) : (
        <div className="space-y-1">
          {unit.skills.map((selection, index) => {
            const skill = SKILL_BY_ID.get(selection.skillId);
            return (
              <div
                key={`${selection.skillId}-${index}`}
                className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1"
                data-skill-index={index}
                data-skill-id={selection.skillId}
                data-skill-motion={skill?.motion ?? ""}
                data-invocation-level={selection.invocationLevel}
              >
                <Badge variant="outline" className="font-mono text-[10px]">
                  #{selection.skillId}
                </Badge>
                <Select
                  value={String(selection.skillId)}
                  onValueChange={(value) => {
                    const next = [...unit.skills];
                    next[index] = { ...next[index], skillId: Number(value) };
                    onChange(next);
                  }}
                >
                  <SelectTrigger className="h-7 min-w-[260px] text-xs" data-skill-select={index}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SKILL_CATALOG.map((entry) => (
                      <SelectItem key={entry.id} value={String(entry.id)}>
                        #{entry.id} {entry.nameText} · motion {entry.motion} · type {entry.type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={String(selection.invocationLevel)}
                  onValueChange={(value) => {
                    const next = [...unit.skills];
                    next[index] = { ...next[index], invocationLevel: Number(value) as 0 | 1 | 2 };
                    onChange(next);
                  }}
                >
                  <SelectTrigger className="h-7 w-24 text-xs" data-invocation-select={index}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {/* GetInvocationRate 0x15dff08 indexes its rate array directly: 0 = highest
                        activation chance (high), 1 = the native default (normal), 2 = lowest (low). */}
                    <SelectItem value="0">0 · high</SelectItem>
                    <SelectItem value="1">1 · normal (default)</SelectItem>
                    <SelectItem value="2">2 · low</SelectItem>
                  </SelectContent>
                </Select>
                <span className="text-[10px] text-muted-foreground">
                  motion {skill?.motion ?? "?"} · equip {skill?.requiredEquipType ?? "?"} · MP{" "}
                  {skill?.minMp ?? "?"}–{skill?.maxMp ?? "?"}
                </span>
                <div className="ml-auto flex gap-1">
                  <Button type="button" size="icon" variant="ghost" className="h-6 w-6" onClick={() => move(index, -1)}>
                    <ArrowUp className="h-3 w-3" />
                  </Button>
                  <Button type="button" size="icon" variant="ghost" className="h-6 w-6" onClick={() => move(index, 1)}>
                    <ArrowDown className="h-3 w-3" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6"
                    onClick={() => onChange(unit.skills.filter((_, itemIndex) => itemIndex !== index))}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        Native skill-slot ceiling: <span className="font-mono">{SKILL_SLOT_CAP}</span> (all SkillComponent.ExtendSlot
        callers pass max=9). A character's own maxSlotNum is a declared input, so this is not a claim that every
        character owns {SKILL_SLOT_CAP} slots.
      </p>
    </div>
  );
}

function EquipmentEditor({
  unit,
  onChange,
}: {
  unit: HumanUnit;
  onChange: (unit: HumanUnit) => void;
}) {
  return (
    <div className="space-y-2">
      <Label>Equipment slots</Label>
      {EQUIPMENT_SLOT_ORDER.map((slot) => {
        const selection = unit.equipmentSlots[slot];
        const options = equipmentForSlot(slot);
        return (
          <div key={slot} className="flex flex-wrap items-center gap-2" data-equipment-slot={slot}>
            <span className="w-20 text-xs capitalize">{slot}</span>
            <Select
              value={selection ? String(selection.id) : "none"}
              onValueChange={(value) => {
                const nextSlots = { ...unit.equipmentSlots };
                if (value === "none") {
                  nextSlots[slot] = null;
                } else {
                  const entry = EQUIPMENT_BY_ID.get(Number(value));
                  if (entry) nextSlots[slot] = { id: entry.id, level: 1, affinity: 0 };
                }
                onChange({
                  ...unit,
                  equipmentSlots: nextSlots,
                  weaponId:
                    slot === "weapon"
                      ? nextSlots.weapon?.id ?? 0
                      : unit.weaponId,
                });
              }}
            >
              <SelectTrigger className="h-7 min-w-[220px] text-xs" data-equipment-select={slot}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None</SelectItem>
                {options.map((entry) => (
                  <SelectItem key={entry.id} value={String(entry.id)}>
                    #{entry.id} {entry.name} · motion {entry.motion} · range {entry.shootingRange}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selection && (
              <>
                <span className="text-[10px] text-muted-foreground">level</span>
                <NumberField
                  value={selection.level}
                  onChange={(level) =>
                    onChange({
                      ...unit,
                      equipmentSlots: {
                        ...unit.equipmentSlots,
                        [slot]: { ...selection, level },
                      },
                    })
                  }
                />
                <span className="text-[10px] text-muted-foreground">affinity</span>
                <NumberField
                  value={selection.affinity}
                  onChange={(affinity) =>
                    onChange({
                      ...unit,
                      equipmentSlots: {
                        ...unit.equipmentSlots,
                        [slot]: { ...selection, affinity },
                      },
                    })
                  }
                />
                <span className="text-[10px] text-muted-foreground" data-equipment-id={selection.id}>
                  id {selection.id}
                </span>
                {slot === "weapon" && (
                  <Badge variant="outline" data-weapon-motion={selection ? EQUIPMENT_BY_ID.get(selection.id)?.motion : ""}>
                    weapon motion {selection ? EQUIPMENT_BY_ID.get(selection.id)?.motion : "?"}
                  </Badge>
                )}
              </>
            )}
          </div>
        );
      })}
      <p className="text-[11px] text-muted-foreground">
        weaponId: <span className="font-mono" data-weapon-id>{unit.weaponId}</span>
      </p>
    </div>
  );
}

function HumanEditor({
  unit,
  onChange,
}: {
  unit: HumanUnit;
  onChange: (unit: HumanUnit) => void;
}) {
  const job = JOB_BY_ID.get(unit.jobId);
  return (
    <div className="space-y-4" data-unit-editor-kind="human">
      <div className="grid gap-3 md:grid-cols-4">
        <div className="space-y-1">
          <Label>Name</Label>
          <Input
            className="h-8 text-sm"
            value={unit.name}
            data-field="unit-name"
            onChange={(event) => onChange({ ...unit, name: event.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label>Job</Label>
          <Select
            value={unit.jobId}
            onValueChange={(value) => {
              const nextJob = JOB_BY_ID.get(value);
              onChange({
                ...unit,
                jobId: value,
                rank: nextJob?.ranks.includes(unit.rank) ? unit.rank : nextJob?.ranks[0] ?? "D",
              });
            }}
          >
            <SelectTrigger className="h-8 text-sm" data-field="job-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {JOB_CATALOG.map((entry) => (
                <SelectItem key={entry.id} value={entry.id}>
                  {entry.name} · {entry.ranks.join("/")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label>Rank</Label>
          <Select value={unit.rank} onValueChange={(value) => onChange({ ...unit, rank: value as HumanUnit["rank"] })}>
            <SelectTrigger className="h-8 text-sm" data-field="rank-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(job?.ranks ?? ["D", "C", "B", "A", "S"]).map((rank) => (
                <SelectItem key={rank} value={rank}>
                  {rank}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label>Gender index</Label>
          <Select value={String(unit.gender)} onValueChange={(value) => onChange({ ...unit, gender: Number(value) })}>
            <SelectTrigger className="h-8 text-sm" data-field="gender-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GENDER_INDICES.map((index) => (
                <SelectItem key={index} value={String(index)}>
                  index {index}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <EquipmentEditor unit={unit} onChange={onChange} />
      <SkillEditor unit={unit} onChange={(skills) => onChange({ ...unit, skills })} />
      <div className="space-y-1">
        <Label>Raw / training parameters</Label>
        <ParameterEditor unit={unit} onChange={(parameters) => onChange({ ...unit, parameters })} />
      </div>
    </div>
  );
}

function MonsterEditor({
  unit,
  onChange,
}: {
  unit: PetMonsterUnit;
  onChange: (unit: PetMonsterUnit) => void;
}) {
  return (
    <div className="space-y-4" data-unit-editor-kind="monster">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label>Name</Label>
          <Input
            className="h-8 text-sm"
            value={unit.name}
            data-field="unit-name"
            onChange={(event) => onChange({ ...unit, name: event.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label>Monster</Label>
          <Select value={String(unit.monsterId)} onValueChange={(value) => onChange({ ...unit, monsterId: Number(value) })}>
            <SelectTrigger className="h-8 text-sm" data-field="monster-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MONSTER_CATALOG.map((entry) => (
                <SelectItem key={entry.id} value={String(entry.id)}>
                  #{entry.id} {entry.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <SkillEditor unit={unit} onChange={(skills) => onChange({ ...unit, skills })} />
      <div className="space-y-1">
        <Label>Raw / training parameters</Label>
        <ParameterEditor unit={unit} onChange={(parameters) => onChange({ ...unit, parameters })} />
      </div>
    </div>
  );
}

export default function BattleSetupPage() {
  const [, navigate] = useLocation();
  const [setup, setSetup] = useState<BattleSetup>(() => createDefaultBattleSetup(19));
  const [selectedUnit, setSelectedUnit] = useState(0);
  const [jsonText, setJsonText] = useState(() => serializeBattleSetup(createDefaultBattleSetup(19)));
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [runState, setRunState] = useState<{
    status: "idle" | "running" | "error";
    message?: string;
    details?: string[];
  }>({ status: "idle" });
  const [selectedLoadoutIds, setSelectedLoadoutIds] = useState<string[]>([]);
  const [loadoutMessage, setLoadoutMessage] = useState<string | null>(null);
  const [comparisonCandidates, setComparisonCandidates] = useState<ComparisonCandidate[]>([]);
  const [comparisonLabel, setComparisonLabel] = useState("");
  const [comparisonSettings, setComparisonSettings] = useState<ComparisonSettings>(() =>
    defaultComparisonSettings(19, 0),
  );
  const [comparisonState, setComparisonState] = useState<{
    status: "idle" | "running" | "error";
    message?: string;
    details?: string[];
  }>({ status: "idle" });
  const [comparisonResult, setComparisonResult] = useState<BattleEvaluationResult | null>(null);
  const [searchResult, setSearchResult] = useState<BattleSearchResult | null>(null);
  const handoffPending = useRef(false);
  const handoffRead = useRef(false);
  const [storedLoadouts] = useLocalFeature<unknown>("ka_loadouts", []);
  const [deviceValuables] = useLocalFeature<unknown>(RESIDENT_STAT_ITEMS_KEY, {});
  /**
   * Saved Loadout Builder presets, read defensively and completed with the device-wide universal
   * valuable counts only when a loadout carries no `residentStatItems` of its own (no double count).
   */
  const savedLoadouts: SavedLoadout[] = useMemo(
    () => loadoutsWithResidentValuables(normalizeSavedLoadouts(storedLoadouts), deviceResidentValuables(deviceValuables)),
    [storedLoadouts, deviceValuables],
  );
  const sharedData = useSharedLoadoutData();
  const issues = useMemo(() => validateBattleSetup(setup), [setup]);
  const selected = setup.playerTeam[selectedUnit] ?? setup.playerTeam[0];
  const variant = ENCOUNTER_BY_ID.get(setup.encounter.encounterId) ?? ENCOUNTER_VARIANTS[0];
  // Declared simulation conditions (isolated-scene0 startProfile + finish policy). These are
  // explicit inputs, never captured save data, and are distinct from an imported prePlacement.
  const declaredStartProfile = setup.startProfile ?? createDefaultStartProfile();
  const enemySpawnCell = declaredStartProfile.enemySpawnCell;
  const bossCell = declaredStartProfile.bossCell;
  const updateStartProfile = (patch: Partial<StartProfile>) => {
    setSetup((current) => ({
      ...current,
      startProfile: { ...(current.startProfile ?? createDefaultStartProfile()), ...patch },
    }));
  };
  const updateStartingStatus = (name: string, entry: [number, number] | null) => {
    setSetup((current) => {
      const profile = current.startProfile ?? createDefaultStartProfile();
      const startingStatus = { ...profile.startingStatus };
      if (entry) startingStatus[name] = entry;
      else delete startingStatus[name];
      return { ...current, startProfile: { ...profile, startingStatus } };
    });
  };

  /**
   * Consumable budget + scheduled uses. These are declared PLAYER POLICY over a finite inventory
   * (the user "sometimes" uses a potion; the source declares no timing), never native AI behaviour.
   * Holy Herb spends `holyHerbStock`; a battle item spends `itemStock[name]` and names a declared row.
   */
  const declaredItemStock = setup.itemStock ?? {};
  const itemRows = setup.items ?? {};
  const itemSchedule = setup.inputs;
  const [presetMessage, setPresetMessage] = useState<string | null>(null);
  const [repeatKind, setRepeatKind] = useState<"holy_herb" | "large_potion">("holy_herb");
  const [repeatConfig, setRepeatConfig] = useState({
    firstTick: 0,
    interval: 60,
    count: 4,
    phase: "before_fighters" as BattleInput["phase"],
  });

  const setHolyHerbBudget = (value: number) =>
    setSetup((current) => ({
      ...current,
      holyHerbStock: Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0,
    }));

  /** Declare a canonical recovery row and its finite count together (0 is a valid empty budget). */
  const updateItemStock = (name: string, value: number) => {
    const canonical = canonicalRecoveryItem(name);
    if (!canonical) return;
    const count = Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
    setSetup((current) => ({
      ...current,
      items: { ...(current.items ?? {}), [canonical.name]: { ...canonical.row } },
      itemStock: { ...(current.itemStock ?? {}), [canonical.name]: count },
    }));
  };

  const updateInputAt = (
    index: number,
    patch: { tick?: number; phase?: BattleInput["phase"]; item?: string },
  ) =>
    setSetup((current) => ({
      ...current,
      inputs: current.inputs.map((entry, entryIndex) =>
        entryIndex === index ? ({ ...entry, ...patch } as BattleInput) : entry,
      ),
    }));

  const removeInputAt = (index: number) =>
    setSetup((current) => ({ ...current, inputs: current.inputs.filter((_, entryIndex) => entryIndex !== index) }));

  const addScheduledUse = (kind: "holy_herb" | "large_potion") => {
    setSetup((current) => {
      if (kind === "holy_herb") {
        return {
          ...current,
          inputs: [...current.inputs, { tick: 0, phase: "before_fighters", type: "holy_herb" } as BattleInput],
        };
      }
      const canonical = LARGE_POTION_ITEM;
      if (!canonical) return current;
      const stock = { ...(current.itemStock ?? {}) };
      if (!(canonical.name in stock)) stock[canonical.name] = 1;
      return {
        ...current,
        items: { ...(current.items ?? {}), [canonical.name]: { ...canonical.row } },
        itemStock: stock,
        inputs: [
          ...current.inputs,
          { tick: 0, phase: "before_fighters", type: "item", item: canonical.name, target: "all" } as BattleInput,
        ],
      };
    });
  };

  /** Convenience expansion: an explicit repeated schedule bounded to the declared inventory. */
  const addRepeatedSchedule = () => {
    const count = Math.max(1, Math.min(200, Math.trunc(repeatConfig.count) || 1));
    const interval = Math.max(1, Math.trunc(repeatConfig.interval) || 1);
    const firstTick = Math.max(0, Math.trunc(repeatConfig.firstTick) || 0);
    setSetup((current) => {
      const additions: BattleInput[] = [];
      const items = { ...(current.items ?? {}) };
      const stock = { ...(current.itemStock ?? {}) };
      let itemName: string | null = null;
      if (repeatKind === "large_potion") {
        if (!LARGE_POTION_ITEM) return current;
        itemName = LARGE_POTION_ITEM.name;
        items[itemName] = { ...LARGE_POTION_ITEM.row };
        if (!(itemName in stock)) stock[itemName] = count;
      }
      for (let index = 0; index < count; index += 1) {
        const tick = firstTick + interval * index;
        additions.push(
          itemName
            ? { tick, phase: repeatConfig.phase, type: "item", item: itemName, target: "all" }
            : { tick, phase: repeatConfig.phase, type: "holy_herb" },
        );
      }
      return { ...current, items, itemStock: stock, inputs: [...current.inputs, ...additions] };
    });
  };

  /** Load the user's authoritative Wairo reference through the canonical saved-loadout converter. */
  const loadUserWairoPreset = () => {
    if (!sharedData) {
      setPresetMessage("Waiting for the shared job/equipment data before converting the preset.");
      return;
    }
    const conversion = battleSetupFromLoadouts(
      userWairoPresetLoadouts(),
      sharedData,
      userWairoPresetConversionOptions(),
    );
    if (!conversion.setup) {
      const errors = conversion.issues.filter((issue) => issue.category === "ERROR");
      setPresetMessage(
        `The user preset was blocked by the shared converter: ${errors.map((issue) => issue.code).join(", ") || "unknown error"}.`,
      );
      return;
    }
    setSetup(conversion.setup);
    setSelectedUnit(0);
    setPresetMessage(
      `Loaded the user Wairo Extreme preset (${conversion.units.length} units). Healer and fodder are labelled assumptions; item timing stays empty until you declare it below.`,
    );
  };

  const partyCapWarning = setup.playerTeam.length > PARTY_LIMIT_INITIAL_MAX && setup.partyLimit.effectiveMax === undefined;
  const comparisonFloor = useMemo(
    () => searchBudget(comparisonSettings, comparisonCandidates.length),
    [comparisonSettings, comparisonCandidates.length],
  );
  const comparisonPolicyIssues = useMemo(
    () => comparisonPolicyProblems(comparisonCandidates),
    [comparisonCandidates],
  );
  const comparisonBudgetReport = useMemo(
    () => (comparisonResult ? comparisonItemBudget(comparisonResult, comparisonCandidates) : null),
    [comparisonResult, comparisonCandidates],
  );

  useEffect(() => {
    if (handoffRead.current) return;
    handoffRead.current = true;
    const { payload, message } = readLoadoutHandoff();
    if (message) setLoadoutMessage(message);
    if (!payload) return;
    handoffPending.current = payload.loadoutIds.length > 0;
    setSelectedLoadoutIds(payload.loadoutIds);
    setLoadoutMessage(
      payload.loadoutIds.length > 0
        ? `Received ${payload.loadoutIds.length} selected loadout(s) from the Loadout Builder.`
        : "The Loadout Builder sent an empty selection.",
    );
  }, []);

  const selectedLoadouts = useMemo(
    () => savedLoadouts.filter((loadout) => loadout.id !== undefined && selectedLoadoutIds.includes(loadout.id)),
    [savedLoadouts, selectedLoadoutIds],
  );

  const loadoutConversion = useMemo(() => {
    if (!sharedData || selectedLoadouts.length === 0) return null;
    return battleSetupFromLoadouts(selectedLoadouts, sharedData, {
      encounterId: setup.encounter.encounterId,
    });
  }, [sharedData, selectedLoadouts, setup.encounter.encounterId]);
  const loadoutVisibleIssues = loadoutConversion?.issues.filter(isPlayerFacingBattleIssue) ?? [];
  const visibleIssues = issues.filter(isPlayerFacingBattleIssue);

  useEffect(() => {
    if (!handoffPending.current || !loadoutConversion) return;
    handoffPending.current = false;
    if (loadoutConversion.setup) {
      setSetup(loadoutConversion.setup);
      setSelectedUnit(0);
      setLoadoutMessage(
        `Applied ${loadoutConversion.units.length} loadout(s) as the player team. Warnings and unknown native rules stay listed below.`,
      );
    } else {
      const errors = loadoutConversion.issues.filter((issue) => issue.category === "ERROR");
      setLoadoutMessage(
        `The handed-over loadouts were not applied: ${errors.map((issue) => issue.code).join(", ")}.`,
      );
    }
  }, [loadoutConversion]);

  const updateUnit = (index: number, unit: PlayerUnit) => {
    setSetup((current) => ({
      ...current,
      playerTeam: current.playerTeam.map((entry, entryIndex) => (entryIndex === index ? unit : entry)),
    }));
  };

  const moveUnit = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= setup.playerTeam.length) return;
    const next = [...setup.playerTeam];
    [next[index], next[target]] = [next[target], next[index]];
    setSetup({ ...setup, playerTeam: next });
    setSelectedUnit(target);
  };

  const addHuman = () => {
    const unit = createHumanUnit(setup.playerTeam.length + 1);
    setSetup({ ...setup, playerTeam: [...setup.playerTeam, unit] });
    setSelectedUnit(setup.playerTeam.length);
  };

  const addPet = () => {
    const unit = createPetMonsterUnit(setup.playerTeam.length + 1, 116);
    setSetup({ ...setup, playerTeam: [...setup.playerTeam, unit] });
    setSelectedUnit(setup.playerTeam.length);
  };

  const humanUnits = setup.playerTeam.filter((unit): unit is HumanUnit => unit.kind === "human");
  const households = setup.households ?? {};
  // Declare (or clear) one owner's COMPLETE household list. `null` removes the declaration; `[]`
  // is the explicit "this house owner has no pets" claim. Nothing here infers membership.
  const setHousehold = (owner: string, pets: PetMonsterUnit[] | null) => {
    setSetup((current) => {
      const next = { ...(current.households ?? {}) };
      if (pets === null) delete next[owner];
      else next[owner] = pets;
      return { ...current, households: Object.keys(next).length > 0 ? next : undefined };
    });
  };

  /**
   * Comparison: add teams as candidates, measure them on the same deterministic seeds, and open
   * the backend's winner. ERROR-level issues (including a skill the native runner cannot execute)
   * reject the candidate here; nothing is stripped or silently repaired.
   */
  const addComparisonCandidate = (candidateSetup: BattleSetup, label: string) => {
    const id = `team-${comparisonCandidates.length + 1}`;
    const built = createComparisonCandidate(id, label, candidateSetup);
    if (!built.ok) {
      setComparisonState({
        status: "error",
        message: "The team was not added: it has ERROR-level issues, including any native-unsupported skill.",
        details: built.errors.map((error) => `${error.code}: ${error.message}`),
      });
      return;
    }
    setComparisonCandidates((current) => [...current, built.candidate]);
    setComparisonResult(null);
    setSearchResult(null);
    setComparisonState({ status: "idle", message: `Added ${label} as ${id}.` });
  };

  const addCurrentTeamCandidate = () => {
    addComparisonCandidate(setup, comparisonLabel.trim() || `Current team ${comparisonCandidates.length + 1}`);
    setComparisonLabel("");
  };

  const addSavedLoadoutsCandidate = () => {
    if (!loadoutConversion || !loadoutConversion.setup) {
      setComparisonState({
        status: "error",
        message: "No converted saved-loadout team is available to add.",
        details: loadoutConversion
          ? loadoutConversion.issues.map((issue) => `${issue.code}: ${issue.message}`)
          : ["Select saved loadouts above first."],
      });
      return;
    }
    addComparisonCandidate(loadoutConversion.setup, `Loadouts ${selectedLoadouts.map((l) => l.id).join("+")}`);
  };

  const removeComparisonCandidate = (id: string) => {
    setComparisonCandidates((current) => current.filter((candidate) => candidate.id !== id));
    setComparisonResult(null);
    setSearchResult(null);
  };

  const updateComparisonLevel = (index: number, patch: Partial<ComparisonLevel>) => {
    setComparisonSettings((current) => ({
      ...current,
      levels: current.levels.map((level, levelIndex) =>
        levelIndex === index ? { ...level, ...patch } : level,
      ),
    }));
  };

  const updateComparisonStrategy = (patch: Partial<ComparisonStrategy>) => {
    setComparisonSettings((current) => ({
      ...current,
      strategy: { ...(current.strategy ?? { formations: true, skillPriorities: true, maxVariantsPerCandidate: 8 }), ...patch },
    }));
  };

  const runTeamComparison = async () => {
    const strategy = comparisonSettings.strategy;
    const useSearch = Boolean(strategy && (strategy.formations || strategy.skillPriorities));
    const built = useSearch
      ? buildSearchRequest(comparisonSettings, comparisonCandidates)
      : buildEvaluationRequest(comparisonSettings, comparisonCandidates);
    if (!built.ok) {
      setComparisonState({ status: "error", message: "The comparison was not sent.", details: built.problems });
      return;
    }
    setComparisonState({ status: "running" });
    try {
      if (useSearch) {
        const result = await runSearchEnvelope(built.request);
        setSearchResult(result);
        setComparisonResult(result);
      } else {
        const result = await runComparison(built.request);
        setComparisonResult(result);
        setSearchResult(null);
      }
      setComparisonState({ status: "idle" });
    } catch (error) {
      if (error instanceof BattleComparisonError) {
        setComparisonState({ status: "error", message: error.message, details: [error.code] });
        return;
      }
      setComparisonState({
        status: "error",
        message: "The comparison endpoint did not return a usable result.",
        details: [String(error instanceof Error ? error.message : error)],
      });
    }
  };

  /** Open the winner through the existing generated-replay store, using the winning candidate's visualSetup. */
  const openComparisonWinner = () => {
    if (!comparisonResult) return;
    const stored = searchResult
      ? storeSearchWinnerReplay(searchResult, comparisonCandidates, variant?.title ?? null)
      : storeWinnerReplay(comparisonResult, comparisonCandidates, variant?.title ?? null);
    if (!stored.ok) {
      setComparisonState({ status: "error", message: stored.reason });
      return;
    }
    navigate("/battle-replay?mode=generated");
  };

  /**
   * PASS 16 COMMAND 16.9: run this setup through the authoritative simulator and open the generated
   * replay. ERROR-level issues block the run; WARNING / UNKNOWN_NATIVE_RULE issues never do and are
   * stored with the battle instead of being dropped.
   */
  const runBattle = async () => {
    const current = validateBattleSetup(setup);
    const errors = current.filter((issue) => issue.category === "ERROR");
    if (errors.length > 0) {
      setRunState({
        status: "error",
        message: "This setup has errors and was not sent to the simulator.",
        details: errors.map((issue) => `${issue.code}: ${issue.message}`),
      });
      return;
    }
    setRunState({ status: "running" });
    try {
      const run = await runBattleSetup(setup, loadoutAwareTransport);
      writeGeneratedBattle(
        run.result,
        run.visualSetup,
        run.warnings.map((issue) => `${issue.category}:${issue.code}`),
        variant?.title ?? null,
        run.scenarioJson,
      );
      navigate("/battle-replay?mode=generated");
    } catch (error) {
      if (error instanceof BattleSetupAdapterError) {
        setRunState({
          status: "error",
          message: "The setup was rejected by the adapter.",
          details: error.issues.map((issue) => `${issue.code}: ${issue.message}`),
        });
        return;
      }
      setRunState({
        status: "error",
        message: "The combat runner did not return a usable replay.",
        details: [String(error instanceof Error ? error.message : error)],
      });
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6" data-battle-setup-root data-encounter-count={ENCOUNTER_VARIANTS.length} data-family-count={ENCOUNTER_FAMILIES.length}>
      <PageHeader
        icon={<RotateCcw className="h-5 w-5" />}
        title="Battle setup"
        actions={
          <Badge variant="secondary" className="font-mono text-[10px]">
            authoritative runner
          </Badge>
        }
      >
        <p>
          Configure the encounter and player team, then run it through the authoritative Python
          combat simulator. The Recovered Replay (Wairo Tank reference) stays available from the
          replay page.
        </p>
      </PageHeader>

      <Card data-run-battle-card>
        <CardHeader>
          <CardTitle>Run battle</CardTitle>
          <CardDescription>
            Runs this setup in your browser, stores the first replay window and opens it
            in generated replay mode. Warnings and unknown native rules do not block a run. The runner
            is a native-checked conditional prediction: it executes the recovered engine behaviour
            under the exact declared inputs, seeds, start profile and item schedule. It is not the live
            game session, so unmodelled parts (ground collection, receipt/inventory dispatch, unknown
            real-world inputs) stay listed, and a native rejection is shown with its exact message.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={runBattle}
              disabled={runState.status === "running"}
              data-action="run-battle"
              data-run-state={runState.status}
            >
              {runState.status === "running" ? "Running battle…" : "Run Battle"}
            </Button>
            <span className="text-xs text-muted-foreground" data-run-summary>
              encounter {setup.encounter.encounterId} · {setup.playerTeam.length} unit
              {setup.playerTeam.length === 1 ? "" : "s"} · {variant?.title ?? "unknown encounter"}
            </span>
          </div>
          {runState.status === "error" ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" data-run-error>
              <p className="font-medium">{runState.message}</p>
              {runState.details?.length ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {runState.details.map((detail) => (
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
        </CardContent>
      </Card>

      <Card data-strategy-search-link>
        <CardHeader>
          <CardTitle className="text-base">Strategy Search (local)</CardTitle>
          <CardDescription>
            Bounded local search over candidate strategies through this same authoritative runner,
            with downloadable scenarios and a replayable best candidate. Engine-owned metrics only;
            box-farming numbers stay provisional until Finish/reward settlement is native-validated.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild type="button" variant="outline" size="sm">
            <Link href="/strategy-search" data-action="open-strategy-search">
              Open Strategy Search
            </Link>
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Encounter</CardTitle>
          <CardDescription>All 20 recovered native variants remain selectable, including partial-art encounters.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-5">
            {ENCOUNTER_FAMILIES.map((family) => (
              <div key={family.id} className="space-y-2">
                <div className="text-xs font-medium">{family.name}</div>
                {family.variants.map((entry) => {
                  const coverage = encounterCoverage(entry);
                  const active = entry.id === setup.encounter.encounterId;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      data-encounter-option
                      data-encounter-id={entry.id}
                      data-family-id={entry.familyId}
                      data-difficulty={entry.difficulty}
                      data-coverage={coverage}
                      data-selectable="true"
                      onClick={() =>
                        setSetup({
                          ...setup,
                          encounter: {
                            encounterId: entry.id,
                            familyId: entry.familyId,
                            difficulty: entry.difficulty,
                            sourceTitle: entry.title,
                          },
                        })
                      }
                      className={`w-full rounded-md border p-2 text-left text-xs transition ${
                        active ? "border-primary bg-primary/5" : "hover:bg-muted/50"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="font-mono">#{entry.id}</span>
                        <Badge variant={coverage === "FULL" ? "default" : "secondary"} className="text-[9px]">
                          {coverageLabel(coverage)}
                        </Badge>
                      </div>
                      <div className="mt-1 font-medium">
                        {difficultyName(entry.difficulty)} · {entry.title}
                      </div>
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        {entry.enemyTotal} enemies · Lv {entry.levelField}/{entry.bossLevelField} · art{" "}
                        {entry.battleArtCoverage.covered}/{entry.battleArtCoverage.total}
                      </div>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <div
            className="rounded-md border bg-muted/30 p-3 text-xs"
            data-selected-encounter={setup.encounter.encounterId}
          >
            <div className="font-medium">
              Selected: {variant.title} · {difficultyName(variant.difficulty)}
            </div>
            {encounterCoverage(variant) === "PARTIAL" && (
              <div className="mt-1 text-amber-700 dark:text-amber-300" data-encounter-warning>
                Combat data recovered; some enemy battle sprites are not yet recovered.
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor="battle-setup-defeat-count" className="text-xs">
              Encounter defeat count
            </Label>
            <Input
              id="battle-setup-defeat-count"
              type="number"
              min={0}
              className="h-8 w-28 text-xs"
              value={setup.defeatCount}
              data-defeat-count
              onChange={(event) => setSetup({ ...setup, defeatCount: Number(event.target.value) })}
            />
            <span className="text-[11px] text-muted-foreground">
              Passed directly to the authoritative scenario; the simulator derives its level offset.
            </span>
          </div>
        </CardContent>
      <Card>
        <CardHeader>
          <CardTitle>Declared start conditions</CardTitle>
          <CardDescription>
            Declared isolated-scene0 simulation conditions, never captured save data. They are
            supplied explicitly; a captured prePlacement is mutually exclusive with a declared profile.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Badge variant="outline" className="text-[9px]" data-declared-conditions>
            DECLARED (not captured save data)
          </Badge>
          {setup.prePlacement ? (
            <div
              className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs"
              data-start-profile-captured
            >
              A captured prePlacement is present, so this declared startProfile is inactive. Declared
              conditions and captured save state are mutually exclusive.
            </div>
          ) : (
            <p className="text-[11px] text-muted-foreground" data-start-profile-note>
              {START_PROFILE_DECLARED_NOTE} kind: {START_PROFILE_KIND}.
            </p>
          )}

          <div className="flex flex-wrap items-end gap-3">
            <Label className="text-xs">Finish policy</Label>
            <Select
              value={setup.finishPolicy ?? "at-horizon"}
              onValueChange={(value) =>
                setSetup((current) => ({ ...current, finishPolicy: value as FinishPolicy }))
              }
            >
              <SelectTrigger className="h-8 w-72 text-xs" data-finish-policy>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="at-horizon">Run post-verdict updates to the declared horizon</SelectItem>
                <SelectItem value="on-verdict">Diagnostic cut at victory — truncated rewards</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div
            className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs"
            data-automatic-finish-status
          >
            {AUTOMATIC_FINISH_STATUS_NOTE}
          </div>

          <div className="flex flex-wrap items-end gap-3" data-start-profile-enemy-cell>
            <Label className="text-xs">Enemy spawn cell (X / Y)</Label>
            <NumberField
              value={enemySpawnCell[0]}
              onChange={(value) => updateStartProfile({ enemySpawnCell: [value, enemySpawnCell[1]] })}
              dataAttribute={{ "data-enemy-spawn-x": 1 }}
            />
            <NumberField
              value={enemySpawnCell[1]}
              onChange={(value) => updateStartProfile({ enemySpawnCell: [enemySpawnCell[0], value] })}
              dataAttribute={{ "data-enemy-spawn-y": 1 }}
            />
          </div>

          <div className="flex flex-wrap items-end gap-3" data-start-profile-boss-cell>
            <Label className="text-xs">Boss cell</Label>
            <Button
              type="button"
              size="sm"
              variant={bossCell ? "default" : "outline"}
              data-action="toggle-boss-cell"
              onClick={() =>
                updateStartProfile({ bossCell: bossCell ? null : [enemySpawnCell[0], enemySpawnCell[1]] })
              }
            >
              {bossCell ? "Declared (click to clear)" : "Not declared (follower spawn)"}
            </Button>
            <NumberField
              value={bossCell ? bossCell[0] : 0}
              onChange={(value) => updateStartProfile({ bossCell: [value, bossCell ? bossCell[1] : 0] })}
              dataAttribute={{ "data-boss-cell-x": 1 }}
            />
            <NumberField
              value={bossCell ? bossCell[1] : 0}
              onChange={(value) => updateStartProfile({ bossCell: [bossCell ? bossCell[0] : 0, value] })}
              dataAttribute={{ "data-boss-cell-y": 1 }}
            />
            <span className="text-[11px] text-muted-foreground">
              Not captured: null follows the recovered World.CreateMonster follower spawn.
            </span>
          </div>

          <div className="space-y-2" data-start-profile-status>
            <Label className="text-xs">Starting status (per selected fighter; native fields 62/63)</Label>
            {setup.playerTeam.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No selected fighters.</p>
            ) : (
              setup.playerTeam.map((unit, index) => {
                const entry = declaredStartProfile.startingStatus[unit.name];
                return (
                  <div key={unit.name} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="w-32 truncate font-mono text-[11px]">{unit.name}</span>
                    <label className="flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={Boolean(entry)}
                        data-status-toggle={index}
                        onChange={(event) =>
                          updateStartingStatus(unit.name, event.target.checked ? [0, 0] : null)
                        }
                      />
                      declared
                    </label>
                    <NumberField
                      value={entry ? entry[0] : 0}
                      onChange={(value) => updateStartingStatus(unit.name, [value, entry ? entry[1] : 0])}
                      dataAttribute={{ "data-status-62": index }}
                    />
                    <NumberField
                      value={entry ? entry[1] : 0}
                      onChange={(value) => updateStartingStatus(unit.name, [entry ? entry[0] : 0, value])}
                      dataAttribute={{ "data-status-63": index }}
                    />
                    <span className="text-[10px] text-muted-foreground">[field62, field63]</span>
                  </div>
                );
              })
            )}
            <p className="text-[11px] text-muted-foreground">
              Empty by default. The native loader integrates only the recovered defense-down/sleep
              status and requires all three fields 62/63/64; a declared entry supplies 62/63.
            </p>
          </div>
        </CardContent>
      </Card>
      </Card>

      <Card data-user-preset-card>
        <CardHeader>
          <CardTitle>User Wairo Extreme reference preset</CardTitle>
          <CardDescription>
            Load the supplied Ninja build with Scholars and an assumed healer. Review the
            assumptions below before using this reference fight.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              data-action="load-user-preset"
              disabled={!sharedData}
              onClick={loadUserWairoPreset}
            >
              Load user Ninja preset
            </Button>
            {presetMessage ? (
              <span className="text-xs text-muted-foreground" data-user-preset-message>
                {presetMessage}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground" data-user-preset-message>
                Supplied Ninja build + Scholars + assumed healer; choose when to use items.
              </span>
            )}
          </div>
          <div className="grid gap-3 text-xs md:grid-cols-2">
            <div data-user-preset-known>
              <div className="font-medium">Known (user-supplied)</div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                {USER_WAIRO_PRESET_KNOWN.map((entry) => (
                  <li key={entry}>{entry}</li>
                ))}
              </ul>
            </div>
            <div data-user-preset-assumed>
              <div className="font-medium">Assumed / adjustable (labelled)</div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                {USER_WAIRO_PRESET_ASSUMED.map((entry) => (
                  <li key={entry}>{entry}</li>
                ))}
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card data-item-budget-card>
        <CardHeader>
          <CardTitle>Consumable budget &amp; scheduled uses</CardTitle>
          <CardDescription>
            Declared player policy over a finite inventory: Holy Herb spends the herb stock, a battle
            item spends its own stock. Uses are scheduled at exact ticks before/after the fighter
            updates, and the engine item events appear on the same replay timeline. This is not native
            AI, and the source declares no timing, so the schedule starts empty.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <Label className="text-xs" htmlFor="holy-herb-stock">
                Holy Herb stock (finite)
              </Label>
              <NumberField
                value={setup.holyHerbStock}
                onChange={setHolyHerbBudget}
                dataAttribute={{ id: "holy-herb-stock", "data-holy-herb-stock": setup.holyHerbStock }}
              />
            </div>
            {LARGE_POTION_ITEM ? (
              <div className="space-y-1" data-large-potion>
                <Label className="text-xs" htmlFor="large-potion-stock">
                  Large Potion stock (Item.txt id {LARGE_POTION_ITEM.id} {LARGE_POTION_ITEM.name})
                </Label>
                <NumberField
                  value={declaredItemStock[LARGE_POTION_ITEM.name] ?? 0}
                  onChange={(value) => updateItemStock(LARGE_POTION_ITEM!.name, value)}
                  dataAttribute={{
                    id: "large-potion-stock",
                    "data-large-potion-stock": declaredItemStock[LARGE_POTION_ITEM.name] ?? 0,
                  }}
                />
                <p className="text-[10px] text-muted-foreground">
                  bonusCategory {LARGE_POTION_ITEM.row.bonusCategory} / bonusType {LARGE_POTION_ITEM.row.bonusType} ·{" "}
                  {LARGE_POTION_ITEM.row.bonusMinValue}-{LARGE_POTION_ITEM.row.bonusMaxValue} · {LARGE_POTION_ITEM.effect}
                </p>
              </div>
            ) : (
              <p className="max-w-md text-xs text-destructive" data-large-potion-unavailable>
                {LARGE_POTION_UNAVAILABLE_REASON}
              </p>
            )}
          </div>

          <div className="space-y-2" data-item-schedule>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium">Scheduled uses ({itemSchedule.length})</span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-action="schedule-herb"
                onClick={() => addScheduledUse("holy_herb")}
              >
                + Holy Herb use
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-action="schedule-potion"
                disabled={!LARGE_POTION_ITEM}
                onClick={() => addScheduledUse("large_potion")}
              >
                + Large Potion use
              </Button>
            </div>
            {itemSchedule.length === 0 ? (
              <p className="text-xs text-muted-foreground" data-item-schedule-empty>
                No scheduled uses yet. Declare ticks here to represent the user&apos;s herb/potion use;
                the source gives no timing, so none is assumed.
              </p>
            ) : (
              <div className="space-y-1">
                {itemSchedule.map((input, index) => (
                  <div
                    key={`${input.type}-${index}`}
                    className="flex flex-wrap items-center gap-2 text-xs"
                    data-item-schedule-row={index}
                  >
                    <span className="font-mono text-[10px] text-muted-foreground">#{index}</span>
                    <Label className="text-[10px]">tick</Label>
                    <NumberField
                      value={input.tick}
                      onChange={(value) => updateInputAt(index, { tick: Math.max(0, Math.trunc(value) || 0) })}
                      dataAttribute={{ "data-input-tick": index }}
                    />
                    <Select
                      value={input.phase}
                      onValueChange={(value) => updateInputAt(index, { phase: value as BattleInput["phase"] })}
                    >
                      <SelectTrigger className="h-7 w-40 text-xs" data-input-phase={index}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="before_fighters">before_fighters</SelectItem>
                        <SelectItem value="after_fighters">after_fighters</SelectItem>
                      </SelectContent>
                    </Select>
                    <Badge variant="outline" className="text-[9px]" data-input-kind={input.type}>
                      {input.type === "item" ? `item: ${input.item}` : input.type}
                    </Badge>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-6 w-6"
                      data-action="remove-input"
                      onClick={() => removeInputAt(index)}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <details className="rounded-md border p-2 text-xs" data-item-repeat>
            <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Advanced: repeated exact-tick schedule (declared player policy)
            </summary>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <Select value={repeatKind} onValueChange={(value) => setRepeatKind(value as typeof repeatKind)}>
                <SelectTrigger className="h-7 w-40 text-xs" data-repeat-kind>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="holy_herb">Holy Herb</SelectItem>
                  <SelectItem value="large_potion" disabled={!LARGE_POTION_ITEM}>
                    Large Potion
                  </SelectItem>
                </SelectContent>
              </Select>
              <Label className="text-[10px]">first tick</Label>
              <NumberField
                value={repeatConfig.firstTick}
                onChange={(value) =>
                  setRepeatConfig((current) => ({ ...current, firstTick: Math.max(0, Math.trunc(value) || 0) }))
                }
              />
              <Label className="text-[10px]">every</Label>
              <NumberField
                value={repeatConfig.interval}
                onChange={(value) =>
                  setRepeatConfig((current) => ({ ...current, interval: Math.max(1, Math.trunc(value) || 1) }))
                }
              />
              <Label className="text-[10px]">uses</Label>
              <NumberField
                value={repeatConfig.count}
                onChange={(value) =>
                  setRepeatConfig((current) => ({ ...current, count: Math.max(1, Math.min(200, Math.trunc(value) || 1)) }))
                }
              />
              <Select
                value={repeatConfig.phase}
                onValueChange={(value) =>
                  setRepeatConfig((current) => ({ ...current, phase: value as BattleInput["phase"] }))
                }
              >
                <SelectTrigger className="h-7 w-40 text-xs" data-repeat-phase>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="before_fighters">before_fighters</SelectItem>
                  <SelectItem value="after_fighters">after_fighters</SelectItem>
                </SelectContent>
              </Select>
              <Button type="button" size="sm" variant="outline" data-action="add-repeat" onClick={addRepeatedSchedule}>
                Add repeated schedule
              </Button>
            </div>
            <p className="mt-2 text-[10px] text-muted-foreground">
              Expands to at most 200 explicit bounded input rows — a declared player policy over the
              stock above, not native AI. Large Potion needs a stock entry; Holy Herb spends herb stock.
            </p>
          </details>

          <p className="text-[11px] text-muted-foreground" data-item-policy-summary>
            declared item rows: {Object.keys(itemRows).length > 0 ? Object.keys(itemRows).join(", ") : "none"} ·
            catalogue: {CANONICAL_RECOVERY_ITEMS.map((entry) => `#${entry.id} ${entry.name}`).join(", ")}
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Player team</CardTitle>
            <CardDescription>Order is preserved exactly; roster index is the setup order.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" onClick={addHuman} data-action="add-human">
                <Plus className="mr-1 h-3.5 w-3.5" /> Add Human
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={addPet} data-action="add-pet">
                <Plus className="mr-1 h-3.5 w-3.5" /> Add Pet / Monster
              </Button>
            </div>
            <div className="text-xs" data-party-size={setup.playerTeam.length}>
              party size {setup.playerTeam.length} · initial cap {PARTY_LIMIT_INITIAL_MAX} · effective cap{" "}
              {setup.partyLimit.effectiveMax ?? "UNKNOWN"}
            </div>
            {partyCapWarning && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]" data-party-cap-warning>
                Native party cap cannot be fully validated without valuable-effect ownership; the larger
                roster is allowed.
              </div>
            )}
            <div className="space-y-2">
              {setup.playerTeam.map((unit, index) => (
                <div
                  key={`${unit.kind}-${index}`}
                  data-unit-index={index}
                  data-unit-kind={unit.kind}
                  data-unit-name={unit.name}
                  className={`rounded-md border p-2 ${index === selectedUnit ? "border-primary" : ""}`}
                >
                  <button type="button" className="w-full text-left" onClick={() => setSelectedUnit(index)}>
                    <div className="flex items-center gap-2 text-xs font-medium">
                      <span className="font-mono">{index}</span>
                      <span>{unit.name}</span>
                      <Badge variant="outline" className="ml-auto text-[9px]">
                        {unit.kind === "human" ? unit.jobId : MONSTER_BY_ID.get(unit.monsterId)?.name ?? "monster"}
                      </Badge>
                    </div>
                  </button>
                  <div className="mt-1 flex gap-1">
                    <Button type="button" size="icon" variant="ghost" className="h-6 w-6" onClick={() => moveUnit(index, -1)}>
                      <ArrowUp className="h-3 w-3" />
                    </Button>
                    <Button type="button" size="icon" variant="ghost" className="h-6 w-6" onClick={() => moveUnit(index, 1)}>
                      <ArrowDown className="h-3 w-3" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-6 w-6"
                      data-action="remove-unit"
                      onClick={() => {
                        const next = setup.playerTeam.filter((_, entryIndex) => entryIndex !== index);
                        setSetup({ ...setup, playerTeam: next });
                        setSelectedUnit(Math.max(0, Math.min(index, next.length - 1)));
                      }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Unit editor</CardTitle>
            <CardDescription>
              Raw simulator inputs only; stat formulas remain outside React.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {selected?.kind === "human" ? (
              <HumanEditor unit={selected} onChange={(unit) => updateUnit(selectedUnit, unit)} />
            ) : selected ? (
              <MonsterEditor unit={selected} onChange={(unit) => updateUnit(selectedUnit, unit)} />
            ) : (
              <p className="text-sm text-muted-foreground">Add a unit to begin.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card data-household-pets>
        <CardHeader>
          <CardTitle>Household pets (explicit complete list)</CardTitle>
          <CardDescription>
            House owners bring the allied monsters assigned to furniture in their house.
            For each selected owner, check the box and enter every pet in furniture and staff-slot
            order. An empty checked list means that owner has no pets.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {humanUnits.length === 0 ? (
            <p className="text-xs text-muted-foreground" data-household-empty>
              Add a human first; only a selected human can own household pets.
            </p>
          ) : (
            humanUnits.map((human) => {
              const declared = Object.prototype.hasOwnProperty.call(households, human.name);
              const pets = households[human.name] ?? [];
              return (
                <div key={human.name} className="space-y-2 rounded-md border p-2" data-household-owner={human.name}>
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={declared}
                      data-action="declare-household"
                      data-household-declare={human.name}
                      onChange={(event) => setHousehold(human.name, event.target.checked ? [] : null)}
                    />
                    <span className="font-medium">{human.name}</span>
                    <span className="text-muted-foreground">
                      household list complete
                      {declared ? ` · ${pets.length} pet${pets.length === 1 ? "" : "s"}` : ""}
                    </span>
                  </label>
                  {declared ? (
                    <div className="space-y-2">
                      {pets.map((pet, petIndex) => (
                        <div key={`${pet.name}-${petIndex}`} className="rounded-md border p-2" data-household-pet={pet.name}>
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium">{pet.name}</span>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6"
                              data-action="remove-household-pet"
                              onClick={() => setHousehold(human.name, pets.filter((_, index) => index !== petIndex))}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                          <MonsterEditor
                            unit={pet}
                            onChange={(next) =>
                              setHousehold(
                                human.name,
                                pets.map((entry, index) => (index === petIndex ? next : entry)),
                              )
                            }
                          />
                        </div>
                      ))}
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-action="add-household-pet"
                        onClick={() =>
                          setHousehold(human.name, [
                            ...pets,
                            {
                              ...createPetMonsterUnit(pets.length + 1, 116),
                              name: `${human.name} household pet ${pets.length + 1}`,
                            },
                          ])
                        }
                      >
                        <Plus className="mr-1 h-3.5 w-3.5" /> Add household pet
                      </Button>
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      <Card data-loadout-import-card>
        <CardHeader>
          <CardTitle>Player team from saved loadouts</CardTitle>
          <CardDescription>
            Imports characters saved in <Link href="/loadout" className="underline">Characters</Link>.
            Job, rank, per-stat levels, equipment and skills are converted through the existing
            canonical owners; constraints that are not recovered are listed instead of guessed, and a
            conversion with errors is blocked rather than shortened.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {savedLoadouts.length === 0 ? (
            <p className="text-xs text-muted-foreground" data-loadout-empty>
              No saved characters on this device yet.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2" data-loadout-options>
              {savedLoadouts.map((loadout) => {
                const id = loadout.id ?? "";
                const selectedOption = id !== "" && selectedLoadoutIds.includes(id);
                return (
                  <button
                    key={id || loadout.name}
                    type="button"
                    data-loadout-option
                    data-loadout-id={id}
                    data-selected={selectedOption ? "true" : "false"}
                    onClick={() =>
                      setSelectedLoadoutIds((current) =>
                        current.includes(id) ? current.filter((entry) => entry !== id) : id ? [...current, id] : current,
                      )
                    }
                    className={`rounded-md border px-2 py-1 text-left text-xs transition ${selectedOption ? "border-primary bg-primary/5" : "hover:bg-muted/50"}`}
                  >
                    <span className="block font-medium">{loadout.name || "Unnamed loadout"}</span>
                    <span className="block text-[10px] text-muted-foreground">
                      {loadout.jobName || "no job"} {loadout.rank ? `· rank ${loadout.rank}` : ""} · {(loadout.equipment ?? []).length} item(s) · {(loadout.skills ?? []).length} skill(s)
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {loadoutMessage ? (
            <p className="text-xs text-muted-foreground" data-loadout-message>{loadoutMessage}</p>
          ) : null}
          {loadoutConversion ? (
            <div className="space-y-2" data-loadout-conversion>
              <div className="flex flex-wrap gap-2 text-xs" data-loadout-summary>
                <Badge variant="destructive">
                  ERROR {loadoutVisibleIssues.filter((issue) => issue.category === "ERROR").length}
                </Badge>
                <Badge variant="secondary">
                  WARNING {loadoutVisibleIssues.filter((issue) => issue.category === "WARNING").length}
                </Badge>
                <Badge variant="outline">
                  UNKNOWN_NATIVE_RULE {loadoutVisibleIssues.filter((issue) => issue.category === "UNKNOWN_NATIVE_RULE").length}
                </Badge>
                <span className="self-center text-muted-foreground">
                  {selectedLoadouts.length} of {savedLoadouts.length} saved loadout(s) selected
                </span>
              </div>
              {loadoutVisibleIssues.length > 0 ? (
                <div className="space-y-1">
                  {loadoutVisibleIssues.map((issue, index) => (
                    <div
                      key={`${issue.code}-${index}`}
                      className="rounded-md border p-2 text-xs"
                      data-loadout-issue
                      data-category={issue.category}
                      data-code={issue.code}
                    >
                      <Badge variant={categoryVariant(issue.category)} className="mr-2 text-[9px]">
                        {issue.category}
                      </Badge>
                      <span className="font-mono text-[10px] text-muted-foreground">{issue.path}</span>
                      <div className="mt-1">{issue.message}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground" data-loadout-issues-empty>
                  Every converted field came from the saved loadout or the canonical site data.
                </p>
              )}
              <details className="rounded-md border p-2 text-xs" data-loadout-provenance>
                <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Field provenance ({loadoutConversion.provenance.length})
                </summary>
                <div className="mt-2 flex flex-wrap gap-1" data-provenance-summary>
                  {(["PLAYER_LOADOUT", "SITE_DERIVED", "UNKNOWN_NOT_CAPTURED", "RESEARCH_SYNTHETIC"] as SetupFieldOrigin[]).map((origin) => (
                    <Badge key={origin} variant="outline" data-provenance-origin={origin} className="text-[9px]">
                      {origin} {loadoutConversion.provenance.filter((entry) => entry.origin === origin).length}
                    </Badge>
                  ))}
                </div>
                <ul className="mt-2 space-y-1">
                  {loadoutConversion.provenance.map((entry) => (
                    <li key={entry.field} data-provenance-field={entry.field}>
                      <span className="font-mono text-[10px] text-muted-foreground">{entry.origin}</span>{" "}
                      <span className="font-medium">{entry.field}</span>: {entry.note}
                    </li>
                  ))}
                </ul>
              </details>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  data-action="apply-loadouts"
                  disabled={!loadoutConversion.setup}
                  onClick={() => {
                    if (!loadoutConversion.setup) return;
                    setSetup(loadoutConversion.setup);
                    setSelectedUnit(0);
                    setLoadoutMessage(
                      `Applied ${loadoutConversion.units.length} loadout(s) as the player team. Warnings and unknown native rules stay listed above.`,
                    );
                  }}
                >
                  Use selected loadouts as the player team
                </Button>
                {loadoutConversion.setup ? null : (
                  <span className="text-xs text-muted-foreground">
                    Blocked: fix the ERROR issues (or the loadout itself) before applying.
                  </span>
                )}
              </div>
            </div>
          ) : selectedLoadoutIds.length > 0 && !sharedData ? (
            <p className="text-xs text-muted-foreground" data-loadout-loading>
              Waiting for the shared job/equipment data before converting.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card data-team-comparison-card>
        <CardHeader>
          <CardTitle>Compare candidate teams</CardTitle>
          <CardDescription>
            Compare supplied teams with the same random seeds and starting resources.
            Chest-yield rankings are unavailable until automatic fight completion is validated.
            Unsupported skills are reported as errors.{" "}
            {comparisonResult ? comparisonClaim(comparisonResult) : "Current results are diagnostic only."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="comparison-candidate-label">Candidate label</Label>
            <div className="flex flex-wrap gap-2">
              <Input
                id="comparison-candidate-label"
                value={comparisonLabel}
                placeholder="e.g. Knight + healer, expert gear"
                className="max-w-xs"
                onChange={(event) => setComparisonLabel(event.target.value)}
              />
              <Button type="button" variant="secondary" onClick={addCurrentTeamCandidate} data-testid="add-current-team">
                Add current team
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={addSavedLoadoutsCandidate}
                data-testid="add-loadout-team"
              >
                Add selected saved-loadout team
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Missing player-specific inputs (gender, per-instance affinity, invocation levels, the
              native raw/training split) stay marked UNKNOWN; they are not guessed for a comparison.
            </p>
          </div>

          <div className="space-y-1" data-comparison-candidates>
            <p className="text-sm font-medium">Candidates ({comparisonCandidates.length})</p>
            {comparisonCandidates.length === 0 ? (
              <p className="text-xs text-muted-foreground">No candidates yet. Add at least one team.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {comparisonCandidates.map((candidate) => (
                  <li key={candidate.id} className="flex flex-wrap items-center gap-2 rounded border border-border/60 p-2">
                    <span className="font-mono">{candidate.id}</span>
                    <span>{candidate.label}</span>
                    {candidate.issues.length > 0 ? (
                      <Badge variant="outline">{candidate.issues.length} warning/unknown</Badge>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => removeComparisonCandidate(candidate.id)}
                    >
                      <Trash2 className="h-3 w-3" /> Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label>Encounter levels (defeat count)</Label>
              {comparisonSettings.levels.map((level, index) => (
                <div key={`${level.encounterId}-${index}`} className="flex items-center gap-2">
                  <Select
                    value={String(level.encounterId)}
                    onValueChange={(value) => updateComparisonLevel(index, { encounterId: Number(value) })}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ENCOUNTER_VARIANTS.map((entry) => (
                        <SelectItem key={entry.id} value={String(entry.id)}>
                          {entry.id}: {entry.title}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    type="number"
                    min={0}
                    className="w-24"
                    value={level.defeatCount}
                    onChange={(event) =>
                      updateComparisonLevel(index, { defeatCount: Math.max(0, Number(event.target.value) || 0) })
                    }
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={comparisonSettings.levels.length <= 1}
                    onClick={() =>
                      setComparisonSettings((current) => ({
                        ...current,
                        levels: current.levels.filter((_, levelIndex) => levelIndex !== index),
                      }))
                    }
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setComparisonSettings((current) => ({
                    ...current,
                    levels: [
                      ...current.levels,
                      {
                        encounterId: current.levels[0]?.encounterId ?? setup.encounter.encounterId,
                        defeatCount: (current.levels[current.levels.length - 1]?.defeatCount ?? 0) + 5,
                      },
                    ],
                  }))
                }
              >
                <Plus className="h-3 w-3" /> Add level
              </Button>
            </div>

            <div className="space-y-2">
              <div className="space-y-1">
                <Label htmlFor="comparison-samples">Samples per level (identical seeds for every candidate)</Label>
                <Input
                  id="comparison-samples"
                  type="number"
                  min={1}
                  max={16}
                  value={comparisonSettings.sampleCount}
                  onChange={(event) =>
                    setComparisonSettings((current) => ({
                      ...current,
                      sampleCount: Math.min(16, Math.max(1, Number(event.target.value) || 1)),
                    }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="comparison-base-seed">Base seed</Label>
                <Input
                  id="comparison-base-seed"
                  type="number"
                  value={comparisonSettings.baseSeed}
                  onChange={(event) =>
                    setComparisonSettings((current) => ({
                      ...current,
                      baseSeed: Number(event.target.value) || 0,
                    }))
                  }
                />
              </div>
            </div>

            <div className="space-y-2">
              <div className="space-y-1">
                <Label htmlFor="comparison-tick-limit">Tick limit</Label>
                <Input
                  id="comparison-tick-limit"
                  type="number"
                  min={1}
                  max={10000}
                  value={comparisonSettings.tickLimit}
                  onChange={(event) =>
                    setComparisonSettings((current) => ({
                      ...current,
                      tickLimit: Math.min(10000, Math.max(1, Number(event.target.value) || 1)),
                    }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="comparison-max-runs">Run budget</Label>
                <Input
                  id="comparison-max-runs"
                  type="number"
                  min={1}
                  max={256}
                  value={comparisonSettings.maxRuns}
                  onChange={(event) =>
                    setComparisonSettings((current) => ({
                      ...current,
                      maxRuns: Math.min(256, Math.max(1, Number(event.target.value) || 1)),
                    }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>Search legal variants (bounded)</Label>
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={comparisonSettings.strategy?.formations ?? true}
                    onChange={(event) => updateComparisonStrategy({ formations: event.target.checked })}
                    data-testid="comparison-strategy-formations"
                  />
                  Formation order (same units, native placement tie only)
                </label>
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={comparisonSettings.strategy?.skillPriorities ?? true}
                    onChange={(event) => updateComparisonStrategy({ skillPriorities: event.target.checked })}
                    data-testid="comparison-strategy-skills"
                  />
                  Skill priorities (same skillIds and invocation levels, one native pass)
                </label>
                <div className="space-y-1">
                  <Label htmlFor="comparison-variant-cap">Max generated variants per candidate</Label>
                  <Input
                    id="comparison-variant-cap"
                    type="number"
                    min={1}
                    max={32}
                    value={comparisonSettings.strategy?.maxVariantsPerCandidate ?? 8}
                    onChange={(event) =>
                      updateComparisonStrategy({
                        maxVariantsPerCandidate: Math.min(32, Math.max(1, Number(event.target.value) || 1)),
                      })
                    }
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  Generation only reorders each candidate's own units and per-unit skills; the response
                  reports the legal count, the generated count and whether the cap truncated the space.
                  Generated variants add runs, so the run budget must cover the reported plan.
                </p>
                <p className="text-xs text-muted-foreground" data-comparison-floor>
                  Local floor before generated variants: {comparisonFloor.runs} run(s) over{" "}
                  {comparisonSettings.levels.length} level(s) x {comparisonFloor.seeds.length} seed pair(s);
                  the backend validates the real plan.
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={runTeamComparison}
              disabled={
                comparisonState.status === "running" ||
                comparisonCandidates.length === 0 ||
                comparisonPolicyIssues.length > 0
              }
              data-testid="run-comparison"
            >
              {comparisonState.status === "running" ? "Comparing..." : "Run comparison"}
            </Button>
            <span className="text-xs text-muted-foreground">
              {searchResult
                ? `Search envelope: ${searchResult.enumeration.combinations} combination(s) x ${searchResult.budget.levels} level(s) x ${searchResult.budget.seeds} seed pair(s) = ${searchResult.budget.plannedRuns} planned runs.`
              : "Default modest budget; the endpoint rejects over-budget requests before any run."}
            </span>
          </div>

          <div className="rounded-md border bg-muted/30 p-2 text-xs" data-comparison-item-budget>
            <div className="font-medium">Item / input policy</div>
            {comparisonPolicyIssues.length > 0 ? (
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-destructive" data-comparison-policy-error>
                {comparisonPolicyIssues.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            ) : comparisonCandidates.length > 0 ? (
              <p className="mt-1 text-muted-foreground" data-comparison-policy-summary>
                Every candidate is compared under the same declared policy: herb stock{" "}
                {comparisonPolicySummary(comparisonCandidates[0]).holyHerbStock}, items{" "}
                {comparisonPolicySummary(comparisonCandidates[0]).itemNames.join(", ") || "none"},{" "}
                {comparisonPolicySummary(comparisonCandidates[0]).inputs} scheduled input(s), declared finish cut{" "}
                {comparisonPolicySummary(comparisonCandidates[0]).finishPolicy ?? "unspecified"}.
              </p>
            ) : (
              <p className="mt-1 text-muted-foreground">
                Add candidates to see the shared item/input policy. Candidates with a different item
                stock, herb budget, schedule or source profile are refused instead of pooled.
              </p>
            )}
            {comparisonBudgetReport?.winner ? (
              <div className="mt-2" data-comparison-item-consumption>
                <div className="font-medium">
                  Winner {comparisonBudgetReport.winner.candidateId} consumption
                </div>
                <p className="text-muted-foreground">
                  Holy Herb remaining {comparisonBudgetReport.winner.holyHerbRemaining ?? "n/a"}
                  {comparisonBudgetReport.winner.items.length > 0
                    ? `; ${comparisonBudgetReport.winner.items
                        .map((entry) => `${entry.name} used ${entry.uses}, remaining ${entry.remaining ?? "n/a"}`)
                        .join("; ")}`
                    : "; no battle-item stock declared"}
                </p>
                {comparisonBudgetReport.winner.reasons.length > 0 ? (
                  <ul className="mt-1 list-disc pl-4 text-muted-foreground">
                    {comparisonBudgetReport.winner.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </div>

          {comparisonState.status === "error" ? (
            <div className="rounded border border-destructive/40 p-2 text-xs text-destructive" data-comparison-error>
              <p>{comparisonState.message}</p>
              {comparisonState.details && comparisonState.details.length > 0 ? (
                <ul className="mt-1 list-disc pl-4">
                  {comparisonState.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {comparisonResult ? (
            <BattleComparisonResult result={comparisonResult} onOpenWinner={openComparisonWinner} />
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Validation</CardTitle>
          <CardDescription>Errors, warnings and unresolved native rules are kept separate.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex flex-wrap gap-2 text-xs" data-validation-summary>
            <Badge variant="destructive">ERROR {visibleIssues.filter((issue) => issue.category === "ERROR").length}</Badge>
            <Badge variant="secondary">WARNING {visibleIssues.filter((issue) => issue.category === "WARNING").length}</Badge>
            <Badge variant="outline">
              UNKNOWN_NATIVE_RULE {visibleIssues.filter((issue) => issue.category === "UNKNOWN_NATIVE_RULE").length}
            </Badge>
          </div>
          {visibleIssues.length === 0 ? (
            <p className="text-xs text-muted-foreground">No issues.</p>
          ) : (
            <div className="space-y-1">
              {visibleIssues.map((issue, index) => (
                <div
                  key={`${issue.code}-${index}`}
                  className="rounded-md border p-2 text-xs"
                  data-validation-issue
                  data-category={issue.category}
                  data-code={issue.code}
                >
                  <Badge variant={categoryVariant(issue.category)} className="mr-2 text-[9px]">
                    {issue.category}
                  </Badge>
                  <span className="font-mono text-[10px] text-muted-foreground">{issue.path}</span>
                  <div className="mt-1">{issue.message}</div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Deterministic JSON</CardTitle>
          <CardDescription>Setup JSON only; replay frame, camera and debug state are not serialized.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              data-action="export-json"
              onClick={() => {
                setJsonText(serializeBattleSetup(setup));
                setImportMessage("Exported deterministic setup JSON.");
              }}
            >
              Export Setup JSON
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-action="import-json"
              onClick={() => {
                const result = importBattleSetup(jsonText);
                if (result.setup) {
                  setSetup(result.setup);
                  setSelectedUnit(0);
                  setImportMessage(
                    `Imported setup. Validation: ${result.issues.filter((issue) => issue.category === "ERROR").length} errors.`,
                  );
                } else {
                  setImportMessage(result.issues[0]?.message ?? "Import failed.");
                }
              }}
            >
              Import Setup JSON
            </Button>
            {importMessage && <span className="self-center text-xs text-muted-foreground" data-import-message>{importMessage}</span>}
          </div>
          <Textarea
            className="min-h-[260px] font-mono text-xs"
            value={jsonText}
            data-export-json
            onChange={(event) => setJsonText(event.target.value)}
          />
        </CardContent>
      </Card>
    </div>
  );
}
