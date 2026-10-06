export type EventStatus = "live" | "inactive";

export function eventStatusLabel(status: EventStatus) {
  return status === "live" ? "Live" : "Inactive";
}

export function eventStatusClass(status: EventStatus) {
  return status === "live"
    ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-300"
    : "border-red-500/60 bg-red-500/10 text-red-300";
}

export function eventStatusCardClass(status: EventStatus) {
  return status === "live"
    ? "border-emerald-500/40 bg-emerald-500/5"
    : "border-red-500/30 bg-red-500/[0.03]";
}

export function getJapanWeekday(now = new Date()) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    timeZone: "Asia/Tokyo",
  }).format(now);
}

export function getLocalWeekday(now = new Date()) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
  }).format(now);
}
