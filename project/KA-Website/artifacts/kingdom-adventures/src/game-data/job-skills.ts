export type SkillValue = "can" | "cannot";
export type SkillAccessKey = "attack" | "attackMagic" | "recovery";
export type SkillAccessMap = Partial<Record<SkillAccessKey, SkillValue>> & { casting?: SkillValue };

export type JobSkillAccessLike = {
  skillAccess?: SkillAccessMap;
};

export const SKILL_ACCESS_LABELS: Record<SkillAccessKey, string> = {
  attack: "Attack",
  attackMagic: "Attack magic",
  recovery: "Recovery magic",
};

export const SHEET_SKILL_ACCESS_FALLBACKS: Record<string, SkillAccessMap> = {
  Artist: { attack: "cannot", attackMagic: "cannot", recovery: "cannot" },
  "Beast Tamer": { attack: "can", attackMagic: "can", recovery: "can" },
  Carpenter: { attack: "cannot", attackMagic: "cannot", recovery: "cannot" },
  Cook: { recovery: "can" },
  Doctor: { recovery: "can" },
  Entertainer: { attack: "cannot", attackMagic: "cannot", recovery: "cannot" },
  Farmer: { recovery: "can" },
  Monk: { recovery: "can" },
  Researcher: { recovery: "can" },
  Merchant: { attackMagic: "can", recovery: "can" },
  Artisan: { attack: "can" },
  Blacksmith: { attack: "can" },
  Mover: { attack: "can" },
  Porter: { attack: "can" },
  Rancher: { attack: "can" },
  Mage: { attackMagic: "can", recovery: "can" },
  Wizard: { attackMagic: "can", recovery: "can" },
  Archer: { attack: "can" },
  Guard: { attack: "can" },
  Gunner: { attack: "can" },
  Knight: { attack: "can" },
  Paladin: { attack: "can" },
  Pirate: { attack: "can" },
  Samurai: { attack: "can" },
  Viking: { attack: "can" },
  Trader: { attack: "cannot", attackMagic: "cannot", recovery: "cannot" },
  Berserker: { attack: "can", recovery: "can" },
  Ninja: { attack: "can", recovery: "can" },
  Champion: { attack: "can", attackMagic: "can", recovery: "can" },
  "Magic Knight": { attack: "can", attackMagic: "can", recovery: "can" },
  Royal: { attack: "can", attackMagic: "can", recovery: "can" },
  "Santa Claus": { attack: "cannot", attackMagic: "can", recovery: "can" },
};

export const RANKED_SKILL_ACCESS_FALLBACKS: Record<string, Partial<Record<string, SkillAccessMap>>> = {
  Monarch: {
    D: { attack: "can", attackMagic: "can", recovery: "can" },
    C: { attack: "cannot", attackMagic: "can", recovery: "can" },
    B: { attack: "cannot", attackMagic: "cannot", recovery: "can" },
    A: { attack: "cannot", attackMagic: "cannot", recovery: "cannot" },
    S: { attack: "can", attackMagic: "cannot", recovery: "cannot" },
  },
};

export function getResolvedSkillAccess(
  jobName: string,
  job: JobSkillAccessLike,
  selectedRank?: string,
): SkillAccessMap {
  const saved = job.skillAccess ?? {};
  const fallback = SHEET_SKILL_ACCESS_FALLBACKS[jobName] ?? {};
  const rankedFallback = selectedRank ? (RANKED_SKILL_ACCESS_FALLBACKS[jobName]?.[selectedRank] ?? {}) : {};
  return {
    attack: saved.attack ?? rankedFallback.attack ?? fallback.attack,
    attackMagic: saved.attackMagic ?? saved.casting ?? rankedFallback.attackMagic ?? fallback.attackMagic,
    recovery: saved.recovery ?? rankedFallback.recovery ?? fallback.recovery,
  };
}
