import { useEffect, useState } from "react";

export const EVENT_HOUR_OFFSET_KEY = "eventHourOffset";

export function getEventHourOffset() {
  if (typeof window === "undefined") return 0;
  const stored = window.localStorage.getItem(EVENT_HOUR_OFFSET_KEY);
  const parsed = Number.parseInt(stored ?? "0", 10);
  return Number.isFinite(parsed) ? Math.min(23, Math.max(-23, parsed)) : 0;
}

export function setEventHourOffset(offset: number) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(EVENT_HOUR_OFFSET_KEY, String(Math.min(23, Math.max(-23, offset))));
  window.dispatchEvent(new Event("event-hour-offset-change"));
}

export function applyEventHourOffset(date: Date, offset = getEventHourOffset()) {
  return new Date(date.getTime() + offset * 60 * 60 * 1000);
}

export function getOffsetAdjustedNow(now = new Date(), offset = getEventHourOffset()) {
  return applyEventHourOffset(now, offset);
}

export function eventClockDateToLocalDate(date: Date, offset = getEventHourOffset()) {
  return new Date(date.getTime() - offset * 60 * 60 * 1000);
}

export function useEventHourOffset() {
  const [offset, setOffsetState] = useState(() => getEventHourOffset());

  useEffect(() => {
    const sync = () => setOffsetState(getEventHourOffset());
    window.addEventListener("storage", sync);
    window.addEventListener("event-hour-offset-change", sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("event-hour-offset-change", sync);
    };
  }, []);

  const setOffset = (next: number | ((current: number) => number)) => {
    setOffsetState((current) => {
      const resolved = typeof next === "function" ? next(current) : next;
      const clamped = Math.min(23, Math.max(-23, resolved));
      setEventHourOffset(clamped);
      return clamped;
    });
  };

  return [offset, setOffset] as const;
}
