import { googleSheetUrl } from "@/lib/api";
import campaignLookupCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Campaign_lookup.csv?raw";

const VERIFIED_WEEKLY_ANCHOR_EVENT_ID = 18;
const VERIFIED_WEEKLY_ANCHOR_START = Date.parse("2026-04-05T00:00:00+09:00");

export type AutomaticWeeklyReward = {
  jobName: string;
  jobRank: string;
  diamonds: number;
  equipment: string;
};

export type AutomaticWeeklyConquest = {
  id: number;
  name: string;
  monsters: string[];
  monsterCounts?: Record<string, number>;
  reward: AutomaticWeeklyReward;
  startedAt: number;
  endsAt: number;
  source: "automatic";
};

export type AutomaticWeeklyConquestTimeline = {
  currentId: number;
  entries: AutomaticWeeklyConquest[];
};

type GvizCell = { v?: string | number | boolean | null } | null;
type GvizRow = { c?: GvizCell[] };

type CampaignEvent = {
  id: number;
  name: string;
  periodDays: number;
  reward: AutomaticWeeklyReward;
  monsters: string[];
  monsterCounts?: Record<string, number>;
};

type CampaignScheduleEntry = {
  id: number;
  day: number;
  hour: number;
};

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (inQuotes) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  return rows;
}

function parseCampaignLookupCsv(text: string): Map<number, CampaignEvent> {
  const rows = parseCsv(text);
  const events = new Map<number, CampaignEvent>();

  for (const row of rows) {
    const id = asNumber(row[0] ?? null);
    const name = asText(row[1] ?? null);
    if (!name || !name.toLowerCase().includes("conquest event")) continue;

    const periodDays = asNumber(row[3] ?? null) || 7;
    const equipment = asText(row[8] ?? null);
    const rawJob = asText(row[14] ?? null);
    const diamonds = asNumber(row[18] ?? null);

    const monsterIndices = [23, 29, 35, 41, 47];
    const countIndices = [24, 30, 36, 42, 48];
    const monsters: string[] = [];
    const monsterCounts: Record<string, number> = {};

    monsterIndices.forEach((monsterIndex, entryIndex) => {
      const monsterName = asText(row[monsterIndex] ?? null);
      if (!monsterName) return;
      const count = asNumber(row[countIndices[entryIndex]] ?? null);
      monsters.push(monsterName);
      monsterCounts[monsterName] = count;
    });

    const rewardJobMatch = rawJob.match(/^([A-Z])\s+(?:Grade|Rank)\s+(.+)$/i);
    const reward: AutomaticWeeklyReward = {
      jobName: rewardJobMatch?.[2] ?? rawJob,
      jobRank: rewardJobMatch?.[1] ?? "S",
      diamonds,
      equipment,
    };

    events.set(id, { id, name, periodDays, reward, monsters, monsterCounts });
  }

  return events;
}

function parseGvizResponse(raw: string): { rows: GvizRow[] } {
  const json = raw
    .replace(/^\/\*O_o\*\/\s*google\.visualization\.Query\.setResponse\(/, "")
    .replace(/\);\s*$/, "");
  return JSON.parse(json).table as { rows: GvizRow[] };
}

function cellValue(row: GvizRow, index: number): string | number | boolean | null {
  return row.c?.[index]?.v ?? null;
}

function asNumber(value: string | number | boolean | null): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return 0;
}

function asText(value: string | number | boolean | null): string {
  if (value == null) return "";
  return String(value).trim();
}

function parseCampaignLookup(rows: GvizRow[]): Map<number, CampaignEvent> {
  const events = new Map<number, CampaignEvent>();

  for (const row of rows) {
    const id = asNumber(cellValue(row, 0));
    const name = asText(cellValue(row, 1));
    if (!name) continue;

    const periodDays = asNumber(cellValue(row, 3)) || 7;
    const equipment = asText(cellValue(row, 8));
    const rawJob = asText(cellValue(row, 14));
    const diamonds = asNumber(cellValue(row, 18));

    const monsterIndices = [23, 29, 35, 41, 47];
    const countIndices = [24, 30, 36, 42, 48];
    const monsters: string[] = [];
    const monsterCounts: Record<string, number> = {};

    monsterIndices.forEach((monsterIndex, entryIndex) => {
      const monsterName = asText(cellValue(row, monsterIndex));
      if (!monsterName) return;
      const count = asNumber(cellValue(row, countIndices[entryIndex]));
      monsters.push(monsterName);
      monsterCounts[monsterName] = count;
    });

    const rewardJobMatch = rawJob.match(/^([A-Z])\s+(?:Grade|Rank)\s+(.+)$/i);
    const reward: AutomaticWeeklyReward = {
      jobName: rewardJobMatch?.[2] ?? rawJob,
      jobRank: rewardJobMatch?.[1] ?? "S",
      diamonds,
      equipment,
    };

    events.set(id, { id, name, periodDays, reward, monsters, monsterCounts });
  }

  return events;
}

function parseCampaignSchedule(rows: GvizRow[]): CampaignScheduleEntry[] {
  return rows
    .map((row) => ({
      id: asNumber(cellValue(row, 0)),
      day: asNumber(cellValue(row, 1)),
      hour: asNumber(cellValue(row, 2)),
    }))
    .filter((entry) => entry.day > 0);
}

function buildMonthlyCandidate(base: Date, day: number, hour: number): Date | null {
  const candidate = new Date(base.getFullYear(), base.getMonth(), day, hour, 0, 0, 0);
  if (candidate.getMonth() !== base.getMonth()) return null;
  return candidate;
}

function resolveCurrentConquest(
  events: Map<number, CampaignEvent>,
  schedule: CampaignScheduleEntry[],
  now = new Date(),
): AutomaticWeeklyConquest | null {
  const candidates = schedule.flatMap((entry) => {
    const event = events.get(entry.id);
    if (!event) return [];

    const monthBases = [
      new Date(now.getFullYear(), now.getMonth() - 1, 1),
      new Date(now.getFullYear(), now.getMonth(), 1),
      new Date(now.getFullYear(), now.getMonth() + 1, 1),
    ];

    return monthBases
      .map((base) => buildMonthlyCandidate(base, entry.day, entry.hour))
      .filter((candidate): candidate is Date => Boolean(candidate))
      .map((startedAt) => ({
        event,
        startedAt,
        endsAt: new Date(startedAt.getTime() + event.periodDays * 24 * 60 * 60 * 1000),
      }));
  });

  const active = candidates
    .filter(({ startedAt, endsAt }) => startedAt.getTime() <= now.getTime() && now.getTime() < endsAt.getTime())
    .sort((a, b) => b.startedAt.getTime() - a.startedAt.getTime());

  const picked = active[0]
    ?? candidates
      .filter(({ startedAt }) => startedAt.getTime() <= now.getTime())
      .sort((a, b) => b.startedAt.getTime() - a.startedAt.getTime())[0]
    ?? null;

  if (!picked) return null;

  return {
    id: picked.event.id,
    name: picked.event.name,
    monsters: picked.event.monsters,
    monsterCounts: picked.event.monsterCounts,
    reward: picked.event.reward,
    startedAt: picked.startedAt.getTime(),
    endsAt: picked.endsAt.getTime(),
    source: "automatic",
  };
}

function resolveAnchoredConquest(
  events: Map<number, CampaignEvent>,
  now = new Date(),
): AutomaticWeeklyConquest | null {
  const anchorEvent = events.get(VERIFIED_WEEKLY_ANCHOR_EVENT_ID);
  if (!anchorEvent) return null;

  const periodMs = (anchorEvent.periodDays || 7) * 24 * 60 * 60 * 1000;
  const offset = Math.floor((now.getTime() - VERIFIED_WEEKLY_ANCHOR_START) / periodMs);
  const event = events.get(VERIFIED_WEEKLY_ANCHOR_EVENT_ID + offset);
  if (!event) return null;

  const startedAt = VERIFIED_WEEKLY_ANCHOR_START + offset * periodMs;
  return {
    id: event.id,
    name: event.name,
    monsters: event.monsters,
    monsterCounts: event.monsterCounts,
    reward: event.reward,
    startedAt,
    endsAt: startedAt + event.periodDays * 24 * 60 * 60 * 1000,
    source: "automatic",
  };
}

export async function fetchAutomaticWeeklyConquest(now = new Date()): Promise<AutomaticWeeklyConquest | null> {
  const timeline = await fetchAutomaticWeeklyConquestTimeline(now);
  return timeline.entries.find((entry) => entry.id === timeline.currentId) ?? null;
}

export async function fetchAutomaticWeeklyConquestTimeline(
  now = new Date(),
  radius = 2,
): Promise<AutomaticWeeklyConquestTimeline> {
  const [lookupRes, scheduleRes] = await Promise.all([
    fetch(googleSheetUrl("weekly-conquest-lookup")),
    fetch(googleSheetUrl("weekly-conquest-schedule")),
  ]);

  if (!lookupRes.ok || !scheduleRes.ok) {
    throw new Error(`Weekly conquest source failed (${lookupRes.status}/${scheduleRes.status})`);
  }

  const [lookupRaw, scheduleRaw] = await Promise.all([
    lookupRes.text(),
    scheduleRes.text(),
  ]);

  const lookupRows = parseGvizResponse(lookupRaw).rows;
  const scheduleRows = parseGvizResponse(scheduleRaw).rows;

  const events = parseCampaignLookup(lookupRows);
  const schedule = parseCampaignSchedule(scheduleRows);
  const current = resolveAnchoredConquest(events, now) ?? resolveCurrentConquest(events, schedule, now);

  if (!current) {
    return { currentId: 0, entries: [] };
  }

  const entries: AutomaticWeeklyConquest[] = [];
  for (let offset = -radius; offset <= radius; offset += 1) {
    const event = events.get(current.id + offset);
    if (!event) continue;
    const shiftedStart = current.startedAt + offset * event.periodDays * 24 * 60 * 60 * 1000;
    const shiftedEnd = shiftedStart + event.periodDays * 24 * 60 * 60 * 1000;
    entries.push({
      id: event.id,
      name: event.name,
      monsters: event.monsters,
      monsterCounts: event.monsterCounts,
      reward: event.reward,
      startedAt: shiftedStart,
      endsAt: shiftedEnd,
      source: "automatic",
    });
  }

  return {
    currentId: current.id,
    entries,
  };
}

export function buildLocalAutomaticWeeklyConquestTimeline(
  now = new Date(),
  radius = 2,
): AutomaticWeeklyConquestTimeline {
  const events = parseCampaignLookupCsv(campaignLookupCsv);
  if (events.size === 0) return { currentId: 0, entries: [] };

  const current = resolveAnchoredConquest(events, now)
    ?? Array.from(events.values())
      .sort((a, b) => a.id - b.id)
      .map((event) => ({
        id: event.id,
        name: event.name,
        monsters: event.monsters,
        monsterCounts: event.monsterCounts,
        reward: event.reward,
        startedAt: now.getTime(),
        endsAt: now.getTime() + event.periodDays * 24 * 60 * 60 * 1000,
        source: "automatic" as const,
      }))[0]
    ?? null;

  if (!current) return { currentId: 0, entries: [] };

  const orderedIds = Array.from(events.keys()).sort((a, b) => a - b);
  const currentIndex = orderedIds.indexOf(current.id);
  if (currentIndex < 0) return { currentId: current.id, entries: [current] };

  const basePeriodMs = (events.get(current.id)?.periodDays || 7) * 24 * 60 * 60 * 1000;
  const entries: AutomaticWeeklyConquest[] = [];

  for (let offset = -radius; offset <= radius; offset += 1) {
    const circularIndex = (currentIndex + offset + orderedIds.length) % orderedIds.length;
    const eventId = orderedIds[circularIndex];
    const event = events.get(eventId);
    if (!event) continue;

    const startedAt = current.startedAt + offset * basePeriodMs;
    entries.push({
      id: event.id,
      name: event.name,
      monsters: event.monsters,
      monsterCounts: event.monsterCounts,
      reward: event.reward,
      startedAt,
      endsAt: startedAt + event.periodDays * 24 * 60 * 60 * 1000,
      source: "automatic",
    });
  }

  return {
    currentId: current.id,
    entries,
  };
}

/** Re-select the active event from an already-loaded timeline as local time advances. */
export function resolveAutomaticWeeklyConquestTimelineForNow(
  timeline: AutomaticWeeklyConquestTimeline | undefined,
  now = new Date(),
  radius = 4,
): AutomaticWeeklyConquestTimeline {
  const nowMs = now.getTime();
  const active = timeline?.entries.find((entry) => entry.startedAt <= nowMs && nowMs < entry.endsAt);
  if (timeline && active) return { ...timeline, currentId: active.id };

  // If the loaded window has expired, roll forward from the bundled repeating schedule.
  return buildLocalAutomaticWeeklyConquestTimeline(now, radius);
}
