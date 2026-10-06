import { useMemo, useState } from "react";
import { ChevronDown, Dumbbell, Eraser, ImageOff, Info, Minus, Plus } from "lucide-react";
import { PageHeader } from "@/components/ka/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PLOT_SIZES } from "@/game-data/buildings";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getFacilityIcon, getFacilityIconByName, getFurnitureIcon } from "@/lib/equipment-icons";
import { localSharedData } from "@/lib/local-shared-data";
import { TRAINING_FACILITIES, FACILITY_TRAINING_DATA, TRAINING_STATS, type TrainingFacilityRecord, type TrainingStatName } from "@/game-data/training-facilities";
import { createTrainingLayout, eligibleEmitters, emitterXpEffects, getBaseXp, getTrainingXp, normalizeTrainingLayout, placeXpEmitter, removeXpEmitter, trainingBonuses, trainingFootprint, trainingRoomOptions, type TrainingLayout } from "@/lib/training-xp";

const STAT_ORDER = TRAINING_STATS.map(({ name }) => name);
const LEVEL_MIN = 1;
const TOWN_RANK_MAX = 99;
const SORTED_FACILITIES = [...TRAINING_FACILITIES].sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);

function formatXp(value: number) {
  return value.toLocaleString();
}
function facilityImage(id: number, name: string) {
  return getFacilityIcon(id) ?? getFacilityIconByName(name) ?? getFurnitureIcon(name);
}

export default function TrainingFacilitiesPage() {
  const facilities = SORTED_FACILITIES;
  const recordsById = useMemo(() => new Map(FACILITY_TRAINING_DATA.map((facility) => [facility.id, facility])), []);
  const [desiredStats, setDesiredStats] = useState<TrainingStatName[]>([]);
  const [undesiredStats, setUndesiredStats] = useState<TrainingStatName[]>([]);
  const [facilityLevels, setFacilityLevels] = useState<Record<number, number>>({});
  const [levelDrafts, setLevelDrafts] = useState<Record<number, string>>({});
  const [townRank, setTownRank] = useState(TOWN_RANK_MAX);
  const [townRankDraft, setTownRankDraft] = useState<string | null>(null);
  const [facilityLayouts, setFacilityLayouts] = useState<Record<number, TrainingLayout>>({});
  const [activeFacilityId, setActiveFacilityId] = useState<number | null>(null);
  const [selectedEmitterId, setSelectedEmitterId] = useState<number | null>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [pendingRemoveId, setPendingRemoveId] = useState<string | null>(null);
  const [pendingRoomKey, setPendingRoomKey] = useState<string | null>(null);
  const [roomChangeConfirmation, setRoomChangeConfirmation] = useState(false);

  const statIcons = useMemo(() => {
    const icons = (localSharedData as { statIcons?: Record<string, unknown> }).statIcons ?? {};
    const normalized: Record<string, string> = {};
    for (const [key, value] of Object.entries(icons)) {
      if (typeof value === "string" && value.length > 0) normalized[key] = value;
    }
    return normalized;
  }, []);

  const effectiveLevels = useMemo(() => {
    const result: Record<number, number> = {};
    for (const facility of FACILITY_TRAINING_DATA) {
      const maximum = facility.canUpgrade ? townRank : LEVEL_MIN;
      result[facility.id] = Math.max(LEVEL_MIN, Math.min(maximum, Math.trunc(facilityLevels[facility.id] ?? LEVEL_MIN)));
    }
    return result;
  }, [facilityLevels, townRank]);

  const filteredFacilities = useMemo(() => facilities.filter((facility) => {
    const satisfiesDesired = desiredStats.every((stat) => facility.stats.includes(stat));
    const avoidsUndesired = undesiredStats.every((stat) => !facility.stats.includes(stat));
    return satisfiesDesired && avoidsUndesired;
  }), [facilities, desiredStats, undesiredStats]);

  const normalizedLayouts = useMemo(
    () => new Map(facilities.map((facility) => [
      facility.id,
      normalizeTrainingLayout(facilityLayouts[facility.id], facility),
    ])),
    [facilities, facilityLayouts],
  );

  const activeFacility = activeFacilityId === null ? null : recordsById.get(activeFacilityId) ?? null;
  const activeLayout = activeFacility === null ? null : normalizedLayouts.get(activeFacility.id) ?? normalizeTrainingLayout(undefined, activeFacility);
  const activeEmitters = useMemo(
    () => activeFacility ? eligibleEmitters(activeFacility, activeLayout ?? undefined) : [],
    [activeFacility, activeLayout],
  );
  const activeEmitterById = useMemo(() => new Map(activeEmitters.map((emitter) => [emitter.id, emitter])), [activeEmitters]);
  const availableSelectedEmitterId = selectedEmitterId !== null && activeEmitterById.has(selectedEmitterId) ? selectedEmitterId : null;
  const activeRoomOptions = useMemo(
    () => activeFacility ? trainingRoomOptions(activeFacility) : [],
    [activeFacility],
  );
  const roomGroups = useMemo(() => {
    const groups = new Map<string, Array<{ key: string; size?: string }>>();
    for (const room of activeRoomOptions) {
      const name = room.name.replace(/ (S|M|L|XL)(?= ·|$)/, "");
      const size = /^\d+-(S|M|L|XL)(?::\d+)?$/.exec(room.key)?.[1];
      const options = groups.get(name) ?? [];
      options.push({ key: room.key, size });
      groups.set(name, options);
    }
    return [...groups].map(([name, options]) => ({ name, options }));
  }, [activeRoomOptions]);
  const selectedRoomKey = activeLayout?.roomKey ?? activeRoomOptions[0]?.key;
  const selectedRoomGroup = roomGroups.find((group) => group.options.some((room) => room.key === selectedRoomKey));
  const selectedRoomSize = selectedRoomGroup?.options.find((room) => room.key === selectedRoomKey)?.size;

  function toggleDesiredStat(stat: TrainingStatName, checked: boolean) {
    setDesiredStats((current) => {
      if (checked) return current.includes(stat) ? current : [...current, stat];
      return current.filter((value) => value !== stat);
    });
    if (checked) setUndesiredStats((current) => current.filter((value) => value !== stat));
  }

  function toggleUndesiredStat(stat: TrainingStatName, checked: boolean) {
    setUndesiredStats((current) => {
      if (checked) return current.includes(stat) ? current : [...current, stat];
      return current.filter((value) => value !== stat);
    });
    if (checked) setDesiredStats((current) => current.filter((value) => value !== stat));
  }

  function currentLevel(facility: TrainingFacilityRecord) {
    if (!facility.canUpgrade) return LEVEL_MIN;
    return effectiveLevels[facility.id] ?? LEVEL_MIN;
  }

  function commitFacilityLevel(id: number, canUpgrade: boolean, value: string) {
    const parsed = Number.parseInt(value, 10);
    const maximum = canUpgrade ? townRank : LEVEL_MIN;
    const level = Math.max(LEVEL_MIN, Math.min(maximum, Number.isFinite(parsed) ? parsed : LEVEL_MIN));
    setFacilityLevels((current) => ({ ...current, [id]: level }));
    setLevelDrafts((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function commitTownRank(value: string) {
    const parsed = Number.parseInt(value, 10);
    setTownRank(Math.max(LEVEL_MIN, Math.min(TOWN_RANK_MAX, Number.isFinite(parsed) ? parsed : LEVEL_MIN)));
    setTownRankDraft(null);
  }

  function openSurroundEditor(facility: TrainingFacilityRecord) {
    const layout = normalizedLayouts.get(facility.id) ?? normalizeTrainingLayout(undefined, facility);
    setFacilityLayouts((current) => ({ ...current, [facility.id]: layout }));
    setActiveFacilityId(facility.id);
    setSelectedEmitterId(null);
    setLayoutError(null);
    setConfirmClear(false);
    setPendingRemoveId(null);
    setPendingRoomKey(null);
    setRoomChangeConfirmation(false);
  }

  function saveLayout(layout: TrainingLayout) {
    setFacilityLayouts((current) => ({ ...current, [layout.targetId]: layout }));
  }

  function clearSurrounds() {
    if (!activeLayout) return;
    const cleared = activeLayout.sources.reduce(
      (layout, source) => source.fixed ? layout : removeXpEmitter(layout, source.id),
      activeLayout,
    );
    saveLayout(cleared);
    setLayoutError(null);
    setConfirmClear(false);
  }

  function applyRoomChange(roomKey: string) {
    if (!activeFacility) return;
    saveLayout(createTrainingLayout(activeFacility, roomKey));
    setSelectedEmitterId(null);
    setLayoutError(null);
    setPendingRoomKey(null);
    setRoomChangeConfirmation(false);
  }

  function requestRoomChange(roomKey: string) {
    if (!activeFacility || !activeLayout) return;
    const currentRoomKey = activeLayout.roomKey ?? activeRoomOptions[0]?.key;
    if (roomKey === currentRoomKey) return;
    if (activeLayout.sources.some((source) => !source.fixed)) {
      setPendingRoomKey(roomKey);
      setRoomChangeConfirmation(true);
      return;
    }
    applyRoomChange(roomKey);
  }

  function renderLevelControl(facility: TrainingFacilityRecord, compact = false) {
    if (!facility.canUpgrade) {
      return <span className="text-sm text-muted-foreground">Lv. 1 fixed</span>;
    }
    const value = levelDrafts[facility.id] ?? String(currentLevel(facility));
    return (
      <Input
        aria-label={`${facility.name} effective level`}
        className={`h-11 ${compact ? "w-20" : "w-24"}`}
        type="number"
        inputMode="numeric"
        min={LEVEL_MIN}
        max={townRank}
        value={value}
        onChange={(event) => setLevelDrafts((current) => ({ ...current, [facility.id]: event.target.value }))}
        onBlur={(event) => commitFacilityLevel(facility.id, facility.canUpgrade, event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setLevelDrafts((current) => {
              const next = { ...current };
              delete next[facility.id];
              return next;
            });
            event.stopPropagation();
          }
        }}
      />
    );
  }

  function handleGridCellClick(x: number, y: number) {
    if (!activeFacility || !activeLayout || availableSelectedEmitterId === null) return;
    const result = placeXpEmitter(activeLayout, availableSelectedEmitterId, x, y);
    saveLayout(result.layout);
    setLayoutError(result.error ?? null);
  }

  function removeFromGrid(sourceId: string) {
    if (!activeLayout) return;
    saveLayout(removeXpEmitter(activeLayout, sourceId));
    setLayoutError(null);
  }

  function renderLayoutCell(x: number, y: number) {
    if (!activeLayout || !activeFacility) return null;
    const placed = activeLayout.sources.find((source) =>
      x >= source.x && x < source.x + source.width && y >= source.y && y < source.y + source.height,
    );
    if (placed) {
      const emitterName = activeEmitterById.get(placed.facilityId)?.name ?? recordsById.get(placed.facilityId)?.name ?? "XP emitter";
      const anchor = x === placed.x && y === placed.y;
      const cell = (
        <div
          key={`${x}-${y}`}
          title={`${emitterName} · ${placed.width} × ${placed.height}${placed.fixed ? " · fixed" : ""}`}
          aria-label={`${emitterName} footprint at column ${x + 1}, row ${y + 1}${anchor ? " (anchor)" : ""}${placed.fixed ? ", fixed" : ""}`}
          className={`flex h-full w-full items-center justify-center border text-center text-[10px] leading-tight ${placed.fixed ? "border-muted-foreground/40 bg-muted" : "border-accent-foreground/30 bg-accent"}`}
        >
          {anchor ? (() => {
            const sourceIcon = facilityImage(placed.facilityId, emitterName);
            return sourceIcon
              ? <img src={sourceIcon} alt="" aria-hidden="true" className="h-full w-full object-contain p-1" />
              : <ImageOff aria-hidden="true" className="h-5 w-5 text-muted-foreground" />;
          })() : <span aria-hidden="true" className="text-muted-foreground">·</span>}
        </div>
      );
      return (
        anchor && !placed.fixed ? (
          <button
            key={`${x}-${y}`}
            type="button"
            title={`Select to remove ${emitterName} · ${placed.width} × ${placed.height}`}
            aria-label={`Select to remove ${emitterName} at column ${x + 1}, row ${y + 1}`}
            className="group relative h-full w-full rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => setPendingRemoveId(placed.id)}
          >
            {cell}
            <Minus aria-hidden="true" className="absolute bottom-0.5 right-0.5 h-3 w-3 rounded-full bg-background text-destructive opacity-0 group-hover:opacity-100" />
          </button>
        ) : cell
      );
    }

    const blocked = activeLayout.blocked.find((cell) => cell.x === x && cell.y === y);
    if (blocked) {
      return (
        <div
          key={`${x}-${y}`}
          title={blocked.reason}
          aria-label={`${blocked.reason} at column ${x + 1}, row ${y + 1}`}
          className="flex h-11 w-11 items-center justify-center rounded border border-dashed bg-muted/70 text-muted-foreground"
        >
          {(() => {
            const blockedFacilityId = "facilityId" in blocked && typeof blocked.facilityId === "number" ? blocked.facilityId : undefined;
            const blockedFacility = blockedFacilityId === undefined ? undefined : recordsById.get(blockedFacilityId);
            const blockedIcon = blockedFacility
              ? facilityImage(blockedFacility.id, blockedFacility.name)
              : null;
            return blockedIcon
              ? <img src={blockedIcon} alt="" aria-hidden="true" className="h-8 w-8 object-contain opacity-70" />
              : <span aria-hidden="true" />;
          })()}
        </div>
      );
    }

    const targetContainsCell = x >= activeLayout.target.x
      && x < activeLayout.target.x + activeLayout.target.width
      && y >= activeLayout.target.y
      && y < activeLayout.target.y + activeLayout.target.height;
    if (targetContainsCell) {
      const anchor = activeLayout.target.x === x && activeLayout.target.y === y;
      return (
        <div
          key={`${x}-${y}`}
          title={activeFacility.name}
          aria-label={`${activeFacility.name}, training facility`}
          className="flex h-full w-full items-center justify-center border-2 border-primary bg-primary/10 text-center text-[10px] font-semibold leading-tight"
        >
          {anchor ? (() => {
            const targetIcon = facilityImage(activeFacility.id, activeFacility.name);
            return targetIcon
              ? <img src={targetIcon} alt="" aria-hidden="true" className="h-full w-full object-contain p-1" />
              : <ImageOff aria-hidden="true" className="h-5 w-5 text-muted-foreground" />;
          })() : <span aria-hidden="true" className="text-muted-foreground">·</span>}
        </div>
      );
    }

    return (
      <button
        key={`${x}-${y}`}
        type="button"
        className="flex h-11 w-11 items-center justify-center border border-dashed bg-background text-muted-foreground hover:border-primary hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
        aria-label={availableSelectedEmitterId === null
          ? `Empty cell at column ${x + 1}, row ${y + 1}; select an XP facility to place it`
          : `Place ${activeEmitterById.get(availableSelectedEmitterId)?.name ?? "XP facility"} at column ${x + 1}, row ${y + 1}`}
        title={availableSelectedEmitterId === null ? "Choose an XP facility first" : "Place selected XP facility"}
        disabled={availableSelectedEmitterId === null}
        onClick={() => handleGridCellClick(x, y)}
      >
        {availableSelectedEmitterId === null ? <span aria-hidden="true" className="text-xs">·</span> : <Plus aria-hidden="true" className="h-4 w-4" />}
      </button>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <PageHeader icon={<Dumbbell className="h-5 w-5" />} title="Training Facilities">
        <p>See the XP awarded for each stat separately. Change a facility’s level or place nearby emitters to calculate its XP surround bonus.</p>
        <p>Choose a room where needed, then place XP emitters by their full footprint. Fixed room facilities are included automatically.</p>
      </PageHeader>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-end justify-between gap-4 rounded-md border bg-muted/20 p-3">
            <div className="max-w-xl space-y-1">
              <label htmlFor="training-town-rank" className="text-sm font-medium">Town rank</label>
              <p className="text-xs leading-relaxed text-muted-foreground">This example uses normal town progression (up to rank 99). Enter the level shown in game if a facility has a lower cap. Special instance caps are not modeled.</p>
            </div>
            <Input
              id="training-town-rank"
              aria-label="Town rank"
              className="h-11 w-24"
              type="number"
              inputMode="numeric"
              min={LEVEL_MIN}
              max={TOWN_RANK_MAX}
              value={townRankDraft ?? String(townRank)}
              onChange={(event) => setTownRankDraft(event.target.value)}
              onBlur={(event) => {
                if (townRankDraft !== null) commitTownRank(event.currentTarget.value);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
                if (event.key === "Escape") {
                  setTownRankDraft(null);
                  event.stopPropagation();
                }
              }}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="gap-2">
                  Desired Stats ({desiredStats.length})
                  <ChevronDown className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuLabel>Must include all selected</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {STAT_ORDER.map((stat) => (
                  <DropdownMenuCheckboxItem
                    key={`desired-${stat}`}
                    checked={desiredStats.includes(stat)}
                    onCheckedChange={(checked) => toggleDesiredStat(stat, checked === true)}
                  >
                    <span className="inline-flex items-center gap-2">
                      {statIcons[stat] ? <img src={statIcons[stat]} alt="" className="h-4 w-4 object-contain" /> : null}
                      {stat}
                    </span>
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="gap-2">
                  Undesired Stats ({undesiredStats.length})
                  <ChevronDown className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuLabel>Must not include any selected</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {STAT_ORDER.map((stat) => (
                  <DropdownMenuCheckboxItem
                    key={`undesired-${stat}`}
                    checked={undesiredStats.includes(stat)}
                    onCheckedChange={(checked) => toggleUndesiredStat(stat, checked === true)}
                  >
                    <span className="inline-flex items-center gap-2">
                      {statIcons[stat] ? <img src={statIcons[stat]} alt="" className="h-4 w-4 object-contain" /> : null}
                      {stat}
                    </span>
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            <span className="text-sm text-muted-foreground">Showing {filteredFacilities.length} of {facilities.length}</span>
          </div>

          <div className="hidden grid-cols-[minmax(12rem,0.95fr)_minmax(0,1.75fr)_8rem_11rem] gap-3 border-b px-3 pb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground md:grid">
            <span>Facility</span>
            <span>Trainable stats · XP per stat</span>
            <span>Effective level</span>
            <span>XP surround</span>
          </div>

          <div className="space-y-2">
            {filteredFacilities.map((facility) => {
              const facilityIcon = facilityImage(facility.id, facility.name);
              const layout = normalizedLayouts.get(facility.id) ?? normalizeTrainingLayout(undefined, facility);
              const bonuses = trainingBonuses(layout, effectiveLevels);
              const fixedEmitterCount = layout.sources.filter((source) => source.fixed).length;
              const suppliedEmitterCount = layout.sources.filter((source) => source.provided && !source.fixed).length;
              const userEmitterCount = layout.sources.filter((source) => !source.provided && !source.fixed).length;
              return (
                <article key={facility.id} className="grid grid-cols-1 gap-3 rounded-md border bg-card p-3 md:grid-cols-[minmax(12rem,0.95fr)_minmax(0,1.75fr)_8rem_11rem] md:items-center">
                  <div className="flex min-w-0 items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      {facilityIcon ? (
                        <div className="relative h-11 w-16 shrink-0">
                          <img src={facilityIcon} alt="" className="absolute top-1/2 h-16 w-16 -translate-y-1/2 rounded-sm object-contain" />
                        </div>
                      ) : (
                        <div className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-sm border border-dashed text-muted-foreground">
                          <ImageOff className="h-4 w-4" aria-hidden="true" />
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="truncate font-medium">{facility.name}</div>
                        <div className="text-xs text-muted-foreground">Facility ID {facility.id}</div>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1 md:hidden">
                      <span className="text-[11px] text-muted-foreground">Level</span>
                      {renderLevelControl(facility, true)}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5" aria-label={`Stats gained by ${facility.name}`}>
                    {facility.stats.map((stat) => {
                      const level = currentLevel(facility);
                      const base = getBaseXp(facility, stat, level);
                      const xp = getTrainingXp(facility, stat, level, bonuses);
                      const surroundPercent = bonuses[stat] ?? 0;
                      const icon = statIcons[stat];
                      const details = `Base ${formatXp(base)} XP; XP surround ${surroundPercent >= 0 ? "+" : ""}${surroundPercent}%; final ${formatXp(xp)} XP`;
                      return (
                        <span key={`${facility.id}-${stat}`} title={details} className="inline-flex min-h-8 items-center gap-1.5 rounded-md border bg-muted/20 px-2 py-1 text-xs">
                          {icon ? <img src={icon} alt="" aria-hidden="true" className="h-4 w-4 shrink-0 object-contain" /> : null}
                          <span>{stat}</span>
                          <span className="whitespace-nowrap font-semibold">+{formatXp(xp)} XP</span>
                        </span>
                      );
                    })}
                  </div>

                  <div className="hidden flex-col gap-1 md:flex">
                    <span className="text-[11px] text-muted-foreground">Level</span>
                    {renderLevelControl(facility)}
                  </div>

                  <div className="flex flex-wrap items-center gap-2 md:items-start md:flex-col">
                    <Button variant="outline" className="min-h-11 gap-2" title="Edit compatible XP surroundings" onClick={() => openSurroundEditor(facility)}>
                      <Dumbbell className="h-4 w-4" />
                      Edit surround
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      {fixedEmitterCount > 0 ? `${fixedEmitterCount} fixed` : "No fixed emitters"}
                      {suppliedEmitterCount > 0 ? ` · ${suppliedEmitterCount} host supplied` : ""}
                      {userEmitterCount > 0 ? ` · ${userEmitterCount} added` : " · no added emitters"}
                    </span>
                  </div>
                </article>
              );
            })}
            {filteredFacilities.length === 0 && (
              <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">No training facilities match these stat filters.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={activeFacility !== null}
        onOpenChange={(open) => {
          if (!open) {
            setActiveFacilityId(null);
            setConfirmClear(false);
            setPendingRemoveId(null);
            setPendingRoomKey(null);
            setRoomChangeConfirmation(false);
          }
        }}
      >
        {activeFacility && activeLayout && (
          <DialogContent className="flex h-[calc(100dvh-1.5rem)] max-w-5xl flex-col gap-4 overflow-hidden p-4 sm:h-[calc(100dvh-2rem)] sm:p-6">
            <DialogHeader className="shrink-0 pr-8 text-left">
              <DialogTitle>XP surround · {activeFacility.name}</DialogTitle>
              <DialogDescription>Place XP emitters around the training facility. Room fixtures and built-in sources are shown as fixed; added emitters use their full footprint.</DialogDescription>
            </DialogHeader>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pr-1">
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
                  <section className="min-w-0 space-y-2" aria-label="Surround layout editor">
                    <div className="flex flex-wrap items-end justify-between gap-2">
                      <div>
                        <div className="text-sm font-medium">Nearby facilities</div>
                        <div className="text-xs text-muted-foreground">Choose an emitter and place its footprint; the game placement rules validate each position.</div>
                        {activeLayout.roomName && <div className="text-xs text-muted-foreground">Location: {activeLayout.roomName}</div>}
                      </div>
                      <span className="text-xs text-muted-foreground">{activeLayout.width} × {activeLayout.height}</span>
                    </div>
                    {activeRoomOptions.length > 0 && (
                      <div className="max-w-md space-y-1">
                        <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-2">
                          <div className="min-w-0 space-y-1">
                            <label className="text-sm font-medium" htmlFor="training-room-select">Surround location</label>
                            <Select value={selectedRoomGroup?.name} onValueChange={(name) => {
                              const group = roomGroups.find((room) => room.name === name);
                              const room = group?.options.find((option) => option.size === selectedRoomSize) ?? group?.options[0];
                              if (room) requestRoomChange(room.key);
                            }}>
                              <SelectTrigger id="training-room-select" className="min-h-11 [&>span]:truncate">
                                <SelectValue placeholder="Choose a room or host" />
                              </SelectTrigger>
                              <SelectContent className="z-[80] max-h-[min(20rem,var(--radix-select-content-available-height))]">
                                {roomGroups.map((room) => (
                                  <SelectItem key={room.name} value={room.name}>{room.name}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="space-y-1">
                            <label className="text-sm font-medium" htmlFor="training-room-size-select">Size</label>
                            <Select value={selectedRoomSize ?? "none"} disabled={!selectedRoomSize} onValueChange={(size) => {
                              const room = selectedRoomGroup?.options.find((option) => option.size === size);
                              if (room) requestRoomChange(room.key);
                            }}>
                              <SelectTrigger id="training-room-size-select" className="min-h-11"><SelectValue /></SelectTrigger>
                              <SelectContent className="z-[80] max-h-[min(20rem,var(--radix-select-content-available-height))]">
                                {selectedRoomSize ? PLOT_SIZES.flatMap((size) => selectedRoomGroup?.options.some((room) => room.size === size)
                                  ? [<SelectItem key={size} value={size}>Plot {size}</SelectItem>] : [])
                                  : <SelectItem value="none">N/A</SelectItem>}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>
                        {roomChangeConfirmation && pendingRoomKey !== null && (
                          <div role="alert" className="space-y-2 rounded-md border border-destructive/50 bg-destructive/5 p-3">
                            <p className="text-sm">Changing location clears the emitters you added. Continue?</p>
                            <div className="flex flex-wrap gap-2">
                              <Button variant="outline" className="min-h-11" onClick={() => { setPendingRoomKey(null); setRoomChangeConfirmation(false); }}>Cancel</Button>
                              <Button variant="destructive" className="min-h-11" onClick={() => applyRoomChange(pendingRoomKey)}>Change location</Button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                    <div className="max-w-full overflow-x-auto rounded-md border bg-muted/10 p-2">
                      <div className="grid w-max gap-0" style={{ gridTemplateColumns: `repeat(${activeLayout.width}, 2.75rem)`, gridTemplateRows: `repeat(${activeLayout.height}, 2.75rem)` }}>
                        {Array.from({ length: activeLayout.width * activeLayout.height }, (_, index) => {
                          const x = index % activeLayout.width, y = Math.floor(index / activeLayout.width);
                          const footprint = [activeLayout.target, ...activeLayout.sources].find(item => x >= item.x && x < item.x + item.width && y >= item.y && y < item.y + item.height);
                          if (footprint && (x !== footprint.x || y !== footprint.y)) return null;
                          return <div key={`${x}-${y}`} style={{ gridColumn: `${x + 1} / span ${footprint?.width ?? 1}`, gridRow: `${y + 1} / span ${footprint?.height ?? 1}` }}>{renderLayoutCell(x, y)}</div>;
                        })}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        {(() => {
                          const icon = facilityImage(activeFacility.id, activeFacility.name);
                          return icon ? <img src={icon} alt="" aria-hidden="true" className="h-5 w-5 object-contain" /> : <ImageOff aria-hidden="true" className="h-4 w-4" />;
                        })()}
                        Training facility
                      </span>
                      <span className="inline-flex items-center gap-1">
                        {availableSelectedEmitterId !== null ? (() => {
                          const selectedName = activeEmitterById.get(availableSelectedEmitterId)?.name;
                          const icon = selectedName ? facilityImage(availableSelectedEmitterId, selectedName) : null;
                          return icon ? <img src={icon} alt="" aria-hidden="true" className="h-5 w-5 object-contain" /> : <ImageOff aria-hidden="true" className="h-4 w-4" />;
                        })() : <Dumbbell aria-hidden="true" className="h-4 w-4" />}
                        XP emitter
                      </span>
                      {activeLayout.sources.some((source) => source.fixed) && <span>Fixed facilities cannot be removed</span>}
                      {activeLayout.blocked.length > 0 && <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded border border-dashed bg-muted" /> Blocked cell</span>}
                    </div>
                  </section>

                  <section className="min-w-0 space-y-3">
                    <div className="space-y-1">
                      <label className="text-sm font-medium" htmlFor="xp-emitter-select">Add an XP emitter</label>
                      <p className="text-xs text-muted-foreground">Each option boosts a stat this facility trains. XP rounds down, so small bonuses may give no extra XP at low levels.</p>
                      {activeEmitters.length > 0 ? (
                        <Select value={availableSelectedEmitterId === null ? "" : String(availableSelectedEmitterId)} onValueChange={(value) => setSelectedEmitterId(Number(value))}>
                          <SelectTrigger id="xp-emitter-select" className="min-h-11 [&>span]:truncate">
                            <SelectValue placeholder="Choose a compatible facility" />
                          </SelectTrigger>
                          <SelectContent className="z-[80] max-h-[min(20rem,var(--radix-select-content-available-height))]">
                            {activeEmitters.map((emitter) => (
                              <SelectItem key={emitter.id} value={String(emitter.id)} className="whitespace-normal break-words">
                                {emitter.name} · {trainingFootprint(emitter.id).width} × {trainingFootprint(emitter.id).height} · {Object.entries(emitterXpEffects(activeLayout, emitter.id, effectiveLevels)).map(([stat, xp]) => `${stat} XP +${xp}%`).join(', ')}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">No compatible XP emitter facilities are available for this target.</p>
                      )}
                    </div>

                    {layoutError && <p role="alert" className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-sm text-destructive">{layoutError}</p>}

                    {pendingRemoveId && activeLayout.sources.some(source => source.id === pendingRemoveId) && (
                      <div className="space-y-2 rounded-md border border-destructive/50 p-3">
                        <p className="text-sm">Remove this XP emitter?</p>
                        <div className="flex flex-wrap gap-2">
                          <Button variant="outline" className="min-h-11" onClick={() => setPendingRemoveId(null)}>Cancel</Button>
                          <Button variant="destructive" className="min-h-11" onClick={() => { removeFromGrid(pendingRemoveId); setPendingRemoveId(null); }}>Remove emitter</Button>
                        </div>
                      </div>
                    )}

                    <div className="space-y-2">
                      <div className="text-sm font-medium">Placed emitter levels</div>
                      {activeLayout.sources.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No XP emitters placed yet.</p>
                      ) : (
                        <div className="space-y-2">
                          {[...new Set(activeLayout.sources.map((source) => source.facilityId))].map((facilityId) => {
                            const sourceRecord = recordsById.get(facilityId);
                            const sourceMeta = activeEmitterById.get(facilityId);
                            const facility = sourceRecord ?? {
                              id: facilityId,
                              name: sourceMeta?.name ?? `Facility ${facilityId}`,
                              stats: [], baseXp: {}, flags: 0, canUpgrade: sourceMeta?.canUpgrade ?? false,
                            } satisfies TrainingFacilityRecord;
                            const sourcePlacements = activeLayout.sources.filter((source) => source.facilityId === facilityId);
                            const fixedPlacements = sourcePlacements.filter((source) => source.fixed).length;
                            const suppliedPlacements = sourcePlacements.filter((source) => source.provided && !source.fixed).length;
                            const addedPlacements = sourcePlacements.filter((source) => !source.provided && !source.fixed).length;
                            return (
                              <div key={facilityId} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-2">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-medium">{facility.name}</div>
                                  <div className="text-xs text-muted-foreground">ID {facilityId}{fixedPlacements > 0 ? ` · ${fixedPlacements} fixed` : ""}{suppliedPlacements > 0 ? ` · ${suppliedPlacements} host supplied` : ""}{addedPlacements > 0 ? ` · ${addedPlacements} added` : ""}</div>
                                </div>
                                <div className="flex items-center gap-2">
                                  {addedPlacements === 0 && (fixedPlacements > 0 || suppliedPlacements > 0) ? (
                                    <span className="text-sm text-muted-foreground">Lv. 1 built in</span>
                                  ) : (
                                    <>
                                      <span className="text-xs text-muted-foreground">Shared level</span>
                                      {renderLevelControl(facility, true)}
                                    </>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    {activeLayout.sources.some((source) => !source.fixed) && (
                      confirmClear ? (
                        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-2">
                          <span className="text-sm">Remove every placed XP emitter?</span>
                          <div className="flex gap-2">
                            <Button variant="ghost" className="min-h-11" onClick={() => setConfirmClear(false)}>Cancel</Button>
                            <Button variant="destructive" className="min-h-11" onClick={clearSurrounds}><Eraser className="mr-2 h-4 w-4" />Clear</Button>
                          </div>
                        </div>
                      ) : (
                        <Button variant="outline" className="min-h-11" onClick={() => setConfirmClear(true)}><Eraser className="mr-2 h-4 w-4" />Clear surrounds</Button>
                      )
                    )}
                  </section>
                </div>

                <div className="rounded-md border bg-muted/20 p-3 text-sm">
                  {activeLayout.warning && <p role="status" aria-live="polite" className="mb-3 flex items-start gap-2 rounded-md border bg-muted/30 p-3 text-sm"><Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{activeLayout.warning}</p>}
                  {activeLayout.unsupportedReason && <p role="status" className="mb-3 rounded-md border bg-muted/30 p-3 text-sm">{activeLayout.unsupportedReason}</p>}
                  <div className="font-medium">XP per stat awarded at effective level {currentLevel(activeFacility)}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {activeFacility.stats.map((stat) => {
                      const bonuses = trainingBonuses(activeLayout, effectiveLevels);
                      const xp = getTrainingXp(activeFacility, stat, currentLevel(activeFacility), bonuses);
                      return <span key={stat} className="rounded-md border bg-background px-2 py-1 text-xs">{stat}: <strong>+{formatXp(xp)} XP</strong></span>;
                    })}
                  </div>
                </div>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </div>
  );
}
