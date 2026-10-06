import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarDays, ChevronDown, ClipboardList, Sparkles } from "lucide-react";
import { Link } from "wouter";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { BRIEFING_PROGRESS_MAX, BRIEFING_PROGRESS_MIN, getBriefingMissionsForDate, getMonsterSpawnReference, getMonthlySpecificMonsterMissions, type BriefingCalendarDate, type BriefingMission } from "@/game-data/briefing-room";
import { getItemIcon } from "@/lib/equipment-icons";
import { useEventRefresh } from "@/lib/event-refresh";
import { useEventHourOffset } from "@/lib/event-time";
import { formatBriefingDate, getBriefingRoomDates } from "@/lib/briefing-room-schedule";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");

function clampProgression(value: number) {
  return Math.max(BRIEFING_PROGRESS_MIN, Math.min(BRIEFING_PROGRESS_MAX, Math.trunc(value)));
}

function MissionReward({ mission }: { mission: BriefingMission }) {
  const icon = getItemIcon(mission.rewardName);
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
      {icon ? <img src={icon} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} /> : null}
      <span className="text-muted-foreground">Reward</span>
      <span className="min-w-0 truncate">
        {mission.rewardAmount === null ? "—" : `${NUMBER_FORMAT.format(mission.rewardAmount)} ${mission.rewardName}`}
      </span>
    </div>
  );
}

function MissionCard({ mission }: { mission: BriefingMission }) {
  const sprite = getMonsterSpawnReference(mission.monsterName)?.sprite;

  return (
    <article className="flex min-w-0 items-center gap-3 rounded-lg border border-border/70 bg-background/70 p-3">
      {sprite ? <img src={sprite} alt="" className="h-10 w-10 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} loading="lazy" /> : null}
      <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
        <h3 className="min-w-0 flex-1 text-sm font-semibold leading-snug">{mission.displayName}</h3>
        <MissionReward mission={mission} />
      </div>
    </article>
  );
}

function MissionDayCard({ title, date, missions }: { title: string; date: BriefingCalendarDate; missions: BriefingMission[] }) {
  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <CalendarDays className="h-4 w-4 text-primary" />
              {title}
            </CardTitle>
            <CardDescription className="mt-1">{formatBriefingDate(date)} · Japan time</CardDescription>
          </div>
          <Badge variant="outline">{missions.length} missions</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {missions.map((mission) => <MissionCard key={mission.id} mission={mission} />)}
      </CardContent>
    </Card>
  );
}

function MonthMissionCard({ day, mission }: { day: number; mission: BriefingMission }) {
  const dateText = `${NUMBER_FORMAT.format(day)}`;
  const spawn = getMonsterSpawnReference(mission.monsterName);
  const sprite = spawn?.sprite;
  return (
    <div className="grid gap-3 rounded-lg border border-border/60 bg-background/70 p-3 sm:grid-cols-[72px_minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
      <div className="text-sm font-semibold">Day {dateText}</div>
      <div className="flex min-w-0 items-center gap-2">
        {sprite ? <img src={sprite} alt="" className="h-9 w-9 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} loading="lazy" /> : null}
        <div className="min-w-0">
          <Link href={`/monsters?monster=${encodeURIComponent(mission.monsterName ?? "")}`} className="block truncate text-sm font-medium hover:underline">
            {mission.displayName}
          </Link>
        </div>
      </div>
      <div className="min-w-0">
        <MissionReward mission={mission} />
        {spawn ? (
          <div className="mt-1 space-y-0.5 text-[10px] text-muted-foreground">
            <div>{spawn.terrainName} · Area Lv. {spawn.minLevel}–{spawn.maxLevel}</div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function BriefingRoomPage() {
  const [now, setNow] = useState(() => new Date());
  const [eventOffset] = useEventHourOffset();
  const [progressionText, setProgressionText] = useState("0");
  const refreshNow = useCallback(() => setNow(new Date()), []);

  useEffect(() => {
    const timer = window.setInterval(refreshNow, 60_000);
    return () => window.clearInterval(timer);
  }, [refreshNow]);
  useEventRefresh(refreshNow);

  const progression = progressionText.trim() === "" || !Number.isFinite(Number(progressionText))
    ? null
    : clampProgression(Number(progressionText));
  const dates = useMemo(() => getBriefingRoomDates(now, eventOffset), [eventOffset, now]);
  const todayMissions = useMemo(() => getBriefingMissionsForDate(dates.today, progression), [dates.today, progression]);
  const tomorrowMissions = useMemo(() => getBriefingMissionsForDate(dates.tomorrow, progression), [dates.tomorrow, progression]);
  const monthMissions = useMemo(
    () => getMonthlySpecificMonsterMissions(dates.today.year, dates.today.month, progression),
    [dates.today.month, dates.today.year, progression],
  );

  const commitProgression = () => {
    if (progression === null) {
      setProgressionText(String(BRIEFING_PROGRESS_MIN));
      return;
    }
    setProgressionText(String(progression));
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <header className="space-y-2">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-5 w-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Briefing Room</h1>
          <Badge variant="outline">JST schedule</Badge>
          <Badge variant="outline">Events offset {eventOffset >= 0 ? "+" : ""}{eventOffset}h</Badge>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Today’s and tomorrow’s Briefing Room missions with progression-scaled goals and rewards.
          The Events offset is applied to the Japan-time calendar.
        </p>
      </header>

      <Card className="border-primary/25 bg-primary/[0.035] shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-primary" />
            Mission progression input
          </CardTitle>
          <CardDescription>
            One game value scales both mission goals and reward amounts from each row’s minimum to maximum. Native mission code reads saved integer slot 18 and eases it across 0–99; the game’s display name for that slot is not recovered.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_140px] sm:items-end">
            <div className="space-y-2">
              <label htmlFor="briefing-progression" className="text-sm font-medium">Progression value (0–99)</label>
              <Slider
                aria-label="Mission progression value"
                min={BRIEFING_PROGRESS_MIN}
                max={BRIEFING_PROGRESS_MAX}
                step={1}
                value={[progression ?? 0]}
                onValueChange={(value) => setProgressionText(String(clampProgression(value[0] ?? 0)))}
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Minimum goals/rewards</span>
                <span>Maximum goals/rewards</span>
              </div>
            </div>
            <Input
              id="briefing-progression"
              type="number"
              min={BRIEFING_PROGRESS_MIN}
              max={BRIEFING_PROGRESS_MAX}
              step={1}
              inputMode="numeric"
              value={progressionText}
              onChange={(event) => setProgressionText(event.target.value)}
              onBlur={commitProgression}
              aria-describedby="briefing-progression-help"
            />
          </div>
          <p id="briefing-progression-help" className="text-xs text-muted-foreground">
            Enter the value you want to model. Mission goals update in the name, and each reward shows its value at this progression.
          </p>
        </CardContent>
      </Card>

      <section className="grid gap-4 xl:grid-cols-2" aria-label="Today and tomorrow missions">
        <MissionDayCard title="Today’s mission board" date={dates.today} missions={todayMissions} />
        <MissionDayCard title="Tomorrow’s mission board" date={dates.tomorrow} missions={tomorrowMissions} />
      </section>

      <details className="group rounded-xl border border-border/70 bg-muted/10 p-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold marker:hidden">
          <span>See more data</span>
          <span className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
            This month’s specific-monster missions · {monthMissions.length} entries
            <ChevronDown className="h-4 w-4" />
          </span>
        </summary>
        <div className="mt-4 space-y-3">
          <div>
            <h2 className="text-base font-semibold">{new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Tokyo", month: "long", year: "numeric" }).format(new Date(Date.UTC(dates.today.year, dates.today.month - 1, 15, 12)))} target schedule</h2>
            <p className="mt-1 text-xs text-muted-foreground">Each row shows the target day and monster from Mission.csv, its reward, and the monster’s biome, level range, and sprite from the same data used by the Monsters page.</p>
            <p className="mt-1 text-[11px] text-muted-foreground">The listed dates are the Mission.csv schedule. The target shown can differ by account; whether map unlocks, progression, or another rule selects it is not confirmed. Use the listed terrain and area-level range as a reference for your map progress.</p>
          </div>
          <div className="space-y-2">
            {monthMissions.map(({ day, mission }) => <MonthMissionCard key={mission.id} day={day} mission={mission} />)}
          </div>
        </div>
      </details>
    </div>
  );
}
