export type JobBattleType = "combat" | "non-combat";

export function normJob(name: string) {
  return name.trim().toLowerCase();
}

export function pairKey(a: string, b: string) {
  return [normJob(a), normJob(b)].sort().join("|");
}

export function typeFromJobCategory(category: string | undefined, fallback?: JobBattleType): JobBattleType | undefined {
  const normalized = category?.trim().toLowerCase();
  if (normalized === "fighter" || normalized === "1") return "combat";
  if (normalized === "worker" || normalized === "trader" || normalized === "0" || normalized === "2") return "non-combat";
  return fallback;
}

export function battleTypeLabel(type: JobBattleType | "all" | undefined) {
  if (type === "all") return "All";
  return type === "combat" ? "Battle-Type" : "Non Battle-Type";
}
