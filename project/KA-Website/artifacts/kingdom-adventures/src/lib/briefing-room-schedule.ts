import { getSpecificMonsterMissionForDate, type BriefingCalendarDate } from "@/game-data/briefing-room";
import { eventClockDateToLocalDate, getOffsetAdjustedNow } from "@/lib/event-time";

const JAPAN_TIME_ZONE = "Asia/Tokyo";
const JAPAN_UTC_OFFSET_MS = 9 * 60 * 60 * 1000;

function datePartsInJapan(eventClockNow: Date): BriefingCalendarDate {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: JAPAN_TIME_ZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(eventClockNow);
  const value = (type: "year" | "month" | "day") => Number(parts.find((part) => part.type === type)?.value ?? 0);
  return { year: value("year"), month: value("month"), day: value("day") };
}

export function getBriefingRoomDates(now = new Date(), eventOffset = 0) {
  const today = datePartsInJapan(getOffsetAdjustedNow(now, eventOffset));
  return { today, tomorrow: addBriefingCalendarDays(today, 1) };
}

export function addBriefingCalendarDays(date: BriefingCalendarDate, amount: number): BriefingCalendarDate {
  const shifted = new Date(Date.UTC(date.year, date.month - 1, date.day + amount, 12));
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
  };
}

export function formatBriefingDate(date: BriefingCalendarDate, includeYear = false) {
  const value = new Date(Date.UTC(date.year, date.month - 1, date.day, 12));
  return new Intl.DateTimeFormat("en-US", {
    timeZone: JAPAN_TIME_ZONE,
    weekday: "long",
    month: "short",
    day: "numeric",
    ...(includeYear ? { year: "numeric" as const } : {}),
  }).format(value);
}

function japanMidnightInEventClock(date: BriefingCalendarDate) {
  return new Date(Date.UTC(date.year, date.month - 1, date.day) - JAPAN_UTC_OFFSET_MS);
}

export function getCurrentOrUpcomingSpecificMonsterTarget(now = new Date(), eventOffset = 0) {
  const eventClockNow = getOffsetAdjustedNow(now, eventOffset);
  const today = datePartsInJapan(eventClockNow);
  const activeMission = getSpecificMonsterMissionForDate(today);

  if (activeMission) {
    const nextDay = addBriefingCalendarDays(today, 1);
    return {
      mission: activeMission,
      date: today,
      active: true,
      countdownAt: eventClockDateToLocalDate(japanMidnightInEventClock(nextDay), eventOffset),
    };
  }

  for (let daysAhead = 1; daysAhead <= 4; daysAhead += 1) {
    const date = addBriefingCalendarDays(today, daysAhead);
    const mission = getSpecificMonsterMissionForDate(date);
    if (mission) {
      return {
        mission,
        date,
        active: false,
        countdownAt: eventClockDateToLocalDate(japanMidnightInEventClock(date), eventOffset),
      };
    }
  }

  return null;
}
