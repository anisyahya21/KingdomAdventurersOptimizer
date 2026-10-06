import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Calculator, ShieldAlert, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { KAIRO_ROOM_DRAFTS } from "@/lib/en-event-drafts";
import { KAIRO_ROOM_LOOT_GROUPS, type EncounterLoot } from "@/lib/special-boss-loot";
import { getOffsetAdjustedNow, useEventHourOffset } from "@/lib/event-time";
import { eventStatusCardClass, eventStatusClass, eventStatusLabel } from "@/lib/event-status";
import { KairoFarmingCalculator } from "@/components/kairo-farming-calculator";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { useEquipmentIcons } from "@/hooks/use-equipment-icons";
import { useLootDifficulties } from "@/hooks/use-loot-difficulties";
import { getEquipmentIcon, getItemIcon } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { DifficultyDayLinks, LOOT_DIFFICULTIES, LootDifficultySwitches, LootBossIcon, LootBossTag, LootDayFilter, lootDifficultyClasses } from "@/components/ka/special-boss-loot-ui";

const FACILITY_ITEM_ICONS: Record<string, string> = {
  "Kairo King Statue": "/website_icons/facilities_confirmed/facility_186_kairo_king_statue.png",
};

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] as const;

function kairoDayAnchor(day: string): string {
  return `kairo-room-${day.toLowerCase()}`;
}

function scrollToKairoDifficulty(day: string, difficulty: string): void {
  const id = `${kairoDayAnchor(day)}-${difficulty.toLowerCase()}`;
  const target = document.getElementById(id);
  if (!target) return;
  window.history.replaceState(null, "", `#${id}`);
  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function KairoRoomPage() {
  const equipIcons = useEquipmentIcons();
  const [now, setNow] = useState(() => new Date());
  const [eventOffset] = useEventHourOffset();
  const [dayFilter, setDayFilter] = useState("all");
  const { selectedDifficulties, setDifficulty: toggleDifficulty } = useLootDifficulties("ka:kairo-loot-difficulties:v1");
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const currentEventDay = WEEKDAYS[getOffsetAdjustedNow(now, eventOffset).getDay()];

  const weekdayByTitle = new Map(
    KAIRO_ROOM_DRAFTS.filter((entry) => entry.active && entry.questName).map((entry) => [
      entry.questName!.replace("'s Challenge", ""),
      entry.day,
    ]),
  );
  const activeDays = KAIRO_ROOM_DRAFTS.filter((entry) => entry.active && entry.questName);
  const visibleGroups = useMemo(
    () => KAIRO_ROOM_LOOT_GROUPS.filter((group) => dayFilter === "all" || weekdayByTitle.get(group.title) === (dayFilter === "today" ? currentEventDay : dayFilter)),
    [dayFilter, currentEventDay],
  );
  const jumpToDifficulty = (day: string, difficulty: EncounterLoot["difficulty"]) => {
    const title = [...weekdayByTitle.entries()].find(([, groupDay]) => groupDay === day)?.[0];
    if (title && !selectedDifficulties(title).has(difficulty)) toggleDifficulty(title, difficulty, true);
    setDayFilter("all");
    window.requestAnimationFrame(() => scrollToKairoDifficulty(day, difficulty));
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Wand2 className="w-5 h-5 text-muted-foreground" />
            <h1 className="text-xl font-bold tracking-tight">Kairo Room</h1>
          </div>
          <p className="text-sm text-muted-foreground max-w-3xl">
            Weekly Kairo Room schedule plus cleaned mined loot tables for each challenge and difficulty.
          </p>
        </div>
        <button
          type="button"
          onClick={() =>
            document.getElementById("kairo-farming-calc")?.scrollIntoView({ behavior: "smooth" })
          }
          className="w-full flex items-center justify-center gap-2 rounded-lg border border-primary/40 bg-primary/10 px-4 py-3 text-sm font-semibold text-primary transition-colors hover:bg-primary/20"
        >
          <Calculator className="w-4 h-4" />
          Jump to Farming Calculator — find the best run for any drop
        </button>
      </div>

      <div className="space-y-3">
        <LootDayFilter value={dayFilter} onChange={setDayFilter} days={activeDays.map((entry) => entry.day)} />
        <div className="grid gap-4 md:grid-cols-2">
        {activeDays.filter((entry) => dayFilter === "all" || entry.day === (dayFilter === "today" ? currentEventDay : dayFilter)).map((entry) => {
          const isCurrentDay = entry.day === currentEventDay;
          const isLive = isCurrentDay && entry.active;
          const lootTitle = [...weekdayByTitle.entries()].find(([, day]) => day === entry.day)?.[0];
          const dayBoss = lootTitle && KAIRO_ROOM_LOOT_GROUPS.find((group) => group.title === lootTitle)?.encounters[0];
          return (
          <Card
            key={entry.day}
            className={`shadow-sm ${eventStatusCardClass(isLive ? "live" : "inactive")}`}
          >
            <div className="grid grid-cols-[minmax(0,1fr)_176px]">
              <CardHeader className="min-w-0 pb-2">
                <div>
                  <CardTitle className="text-base flex items-center gap-2">
                    <CalendarDays className="w-4 h-4 text-primary" />
                    {entry.day}
                  </CardTitle>
                  <CardDescription>{entry.questName}</CardDescription>
                </div>
              </CardHeader>
              <div className="row-span-2 flex flex-col items-center px-2 pt-4 pb-3">
                  <Badge variant="outline" className={`${eventStatusClass(isLive ? "live" : "inactive")} self-end mr-4`}>
                    {eventStatusLabel(isLive ? "live" : "inactive")}
                  </Badge>
                  <div className="flex min-h-[112px] flex-1 items-center justify-center pt-4">
                    {dayBoss && <LootBossIcon encounter={dayBoss} size={96} />}
                  </div>
              </div>
            <CardContent className="min-w-0 space-y-3 pt-0">
              {entry.active ? (
                <>
                  <div className="space-y-1.5">
                    <div className="text-xs text-muted-foreground">Jump to loot</div>
                    <DifficultyDayLinks day={entry.day} onSelect={jumpToDifficulty} />
                  </div>
                </>
              ) : null}
            </CardContent>
            </div>
          </Card>
          );
        })}
      </div>
      </div>

      <Card className="shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-primary" />
            Loot tables
          </CardTitle>
          <CardDescription>
            Resolved from mined SpecialBoss and Treasure lookup data, then reformatted into readable drop tables.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {visibleGroups.length ? visibleGroups.map((group) => {
            const day = weekdayByTitle.get(group.title);
            const selected = selectedDifficulties(group.title);
            const isActiveDay = day === currentEventDay;
            const visibleEncounters = group.encounters.filter((encounter) => selected.has(encounter.difficulty));
            return (
            <div key={group.title} id={day ? kairoDayAnchor(day) : undefined} className={`scroll-mt-24 space-y-3 rounded-xl border p-3 ${isActiveDay ? "border-green-500 ring-2 ring-green-500/50" : "border-border"}`}>
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-sm font-semibold">{group.title}</h2>
                  {group.encounters[0] && <LootBossTag encounter={group.encounters[0]} />}
                  <Badge variant="outline">{day ?? "Event day"}</Badge>
                  {isActiveDay && <Badge className="border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300">Today</Badge>}
                </div>
                <LootDifficultySwitches selected={selected} onChange={(difficulty, checked) => toggleDifficulty(group.title, difficulty, checked)} />
              </div>
              <div className="grid min-w-0 gap-3">
                {visibleEncounters.map((encounter) => (
                  <div key={`${group.title}-${encounter.difficulty}`} id={day ? `${kairoDayAnchor(day)}-${encounter.difficulty.toLowerCase()}` : undefined} className={`min-w-0 scroll-mt-24 rounded-lg border p-3 space-y-3 ${lootDifficultyClasses(encounter.difficulty)}`}>
                    <div>
                      <div className="flex flex-wrap items-center gap-2 font-medium text-sm">
                        <span>{encounter.difficulty}</span>
                        <LootBossTag encounter={encounter} />
                      </div>
                      <div className="text-xs text-muted-foreground">
                        Lv {encounter.level} encounter • Boss Lv {encounter.bossLevel}
                      </div>
                    </div>
                    <div className="grid min-w-0 gap-3 md:grid-cols-2">
                      {encounter.tables.map((table, index) => (
                        <div key={index} className="min-w-0 overflow-hidden rounded-md border">
                          <div className="bg-muted/40 px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                            Loot table {index + 1}
                          </div>
                          <div className="min-w-0 overflow-x-auto">
                            <table className="w-full text-xs">
                              <thead className="bg-muted/20 text-muted-foreground">
                                <tr>
                                  <th className="px-3 py-2 text-left font-medium">Item</th>
                                  <th className="px-3 py-2 text-left font-medium">Quantity</th>
                                  <th className="px-3 py-2 text-left font-medium">Rarity</th>
                                </tr>
                              </thead>
                              <tbody>
                                {table.map((line) => (
                                  <tr key={`${index}-${line.item}`} className="border-t border-border/60">
                                  <td className="px-3 py-2 text-foreground">
                                      <div className="flex items-center gap-2">
                                        {line.item === "F Rank Scholar" ? (
                                          <CharacterPreviewCanvas jobName="Scholar" rank="F" variant={1} equipState="right" scale={2} poseFrame={0} label="F Rank Scholar" className="h-10 w-auto shrink-0" />
                                        ) : getEquipmentIcon(equipIcons, line.item) ? (
                                          <img src={getEquipmentIcon(equipIcons, line.item)} alt="" className="h-10 w-10 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                        ) : getSkillIcon(line.item) ? (
                                          <img src={getSkillIcon(line.item)!} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                        ) : getItemIcon(line.item) ? (
                                          <img src={getItemIcon(line.item)!} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                        ) : FACILITY_ITEM_ICONS[line.item] ? (
                                          <img src={FACILITY_ITEM_ICONS[line.item]} alt="" className="h-8 w-8 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                        ) : (
                                          <span className="h-8 w-8 shrink-0" />
                                        )}
                                        <span>{line.item}</span>
                                      </div>
                                    </td>
                                    <td className="px-3 py-2 text-muted-foreground">{line.quantity}</td>
                                    <td className="px-3 py-2">
                                      <Badge variant="secondary" className="font-mono text-[11px]">
                                        {line.chance}
                                      </Badge>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
          }) : <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">No Kairo Room challenge is listed for {dayFilter === "today" ? currentEventDay : dayFilter}.</div>}
        </CardContent>
      </Card>

      <KairoFarmingCalculator currentDay={currentEventDay} />
    </div>
  );
}
