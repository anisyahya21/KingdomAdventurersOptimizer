export type WeaponValue = "can" | "cannot" | "weak";

export type JobEquipmentAccessLike = {
  shield?: "can" | "cannot";
  weaponEquip?: Partial<Record<string, WeaponValue>>;
};

export const JOB_WEAPON_ACCESS_FALLBACKS: Record<string, Partial<Record<string, WeaponValue>>> = {
  Berserker: {
    Axe: "can",
    Book: "weak",
    Bow: "can",
    Club: "can",
    Gun: "can",
    Hammer: "can",
    Spear: "weak",
    Staff: "weak",
    Sword: "weak",
  },
  Researcher: {
    Axe: "cannot",
    Book: "can",
    Bow: "cannot",
    Club: "can",
    Gun: "cannot",
    Hammer: "cannot",
    Spear: "cannot",
    Staff: "weak",
    Sword: "weak",
  },
  Royal: {
    Axe: "can",
    Book: "can",
    Bow: "can",
    Club: "can",
    Gun: "can",
    Hammer: "can",
    Spear: "can",
    Staff: "can",
    Sword: "can",
  },
  "Santa Claus": {
    Axe: "cannot",
    Book: "cannot",
    Bow: "cannot",
    Club: "cannot",
    Gun: "cannot",
    Hammer: "cannot",
    Spear: "cannot",
    Staff: "can",
    Sword: "cannot",
  },
};

export const JOB_SHIELD_FALLBACKS: Record<string, WeaponValue> = {
  Berserker: "can",
  Researcher: "weak",
  Royal: "can",
  "Santa Claus": "can",
};

export const WEAPON_ACCESS_LABEL: Record<WeaponValue, string> = { can: "Can", weak: "Weak", cannot: "Can't" };
export const WEAPON_ACCESS_SORT: Record<WeaponValue, number> = { can: 2, weak: 1, cannot: 0 };
export const WEAPON_ACCESS_CLASS: Record<WeaponValue, string> = {
  can: "bg-green-100 dark:bg-green-950/40 border-green-400 text-green-700 dark:text-green-400",
  weak: "bg-amber-100 dark:bg-amber-950/40 border-amber-400 text-amber-700 dark:text-amber-400",
  cannot: "bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800 text-red-600 dark:text-red-400",
};

export function getResolvedWeaponAccess(
  jobName: string,
  job: JobEquipmentAccessLike,
  weaponClass: string,
): WeaponValue | undefined {
  return job.weaponEquip?.[weaponClass] ?? JOB_WEAPON_ACCESS_FALLBACKS[jobName]?.[weaponClass];
}

export function getResolvedShieldAccess(jobName: string, job: JobEquipmentAccessLike): WeaponValue | undefined {
  return (
    job.weaponEquip?.Shield
    ?? (job.shield === "can" ? "can" : job.shield === "cannot" ? "cannot" : undefined)
    ?? JOB_SHIELD_FALLBACKS[jobName]
  );
}
