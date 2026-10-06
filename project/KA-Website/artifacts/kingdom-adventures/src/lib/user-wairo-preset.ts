import presetJson from "@/game-data/user-wairo-preset.json";
import type { SavedLoadout } from "@/lib/battle-legality";

/**
 * The user's authoritative Wairo Extreme reference (`user-reference-build.txt`), copied verbatim from
 * `RE-evidence/20260920-user-wairo-strategy/preset.json` (that worker's canonical preset). No stat,
 * skill, equipment level or trigger here is invented: the JSON is an exact copy, and the focused check
 * `tools/battle-setup-check/item_budget_check.mjs` re-reads both files and asserts equality.
 *
 * KNOWN (user-supplied): the rank-A awakening-20 Ninja training levels, its four equipment rows and
 * its six skills in order.
 * ASSUMED (labelled, editable): the healer build. The source does not name the job, and Doctor has no
 * canonical Job.csv rank, so the healer is a REPRESENTATIVE canonical caster with a known base profile:
 * Wizard rank S (csvId 124), awakening 20, modest legal HP/MP 300, ONLY Heal Maddy + Backup. The fodder
 * is the LITERAL Scholar (Job.csv csvId 132); the converter admits it, so there is no Merchant stand-in.
 * finishPolicy is a PLACEHOLDER ("automatic"): the exact native name is pending the automatic-finish worker.
 * Trigger "high" is native invocation index 0 - the HIGHEST activation chance (36/80/100% per
 * `RE-evidence/20260920-user-wairo-reference/invocation-semantics.json`), not the old n=2 guess.
 * NOT DECLARED HERE: item intervention timing (the user "sometimes" uses the potion), so the preset
 * loads an empty schedule and exposes the item controls as adjustable conditions.
 */

export type UserPresetLoadout = {
  jobName: string;
  rank: string;
  awakening: number;
  /**
   * Multiplicity of this saved loadout in the reference party (default 1). The grouped 'Scholar
   * Fodder 1-4' row carries `count: 4`, so the loader expands it into four identical fighters with
   * unique names/ids instead of parsing the "1-4" range out of the JSON key.
   */
  count?: number;
  statLevels: Record<string, number>;
  equipment: Array<{ name: string; level: number }>;
  skills: string[];
  triggerLevel?: number;
  combatCatalogStandIn?: string;
};

type UserWairoPresetFile = {
  schema: string;
  note: string;
  provenance: { source: string; converter: string; owners: string };
  assumptions: string[];
  loadouts: Record<string, UserPresetLoadout>;
  runner: {
    encounterId: number;
    defeatCount: number;
    level: number;
    title: string;
    seeds: number[][];
    tickLimit: number;
    finishPolicy: string;
    holyHerbStock: number;
    inputs: unknown[];
    sourceProfile: string;
  };
};

export const USER_WAIRO_PRESET = presetJson as unknown as UserWairoPresetFile;
export const USER_WAIRO_PRESET_SOURCE = USER_WAIRO_PRESET.provenance.source;

/** The exact labelled split the UI shows: user-supplied facts versus labelled assumptions. */
export const USER_WAIRO_PRESET_KNOWN: string[] = [
  "Ninja rank A, awakening 20; every supplied number is a per-stat TRAINING level (user-confirmed).",
  "Ninja training: HP 314, MP 330, ATK 5, DEF 173, SPD 3, LUCK 1, DEX 1; all unnamed stats level 1.",
  "Ninja equipment: A/ Legendary Hat 99, A/ Legendary Staff 76, B/ Green Shield 57, A/ Verdant Armor 1.",
  "Ninja skills in order: Counter, 7-Hit, 5-Hit, 4-Hit, 3-Hit, 2-Hit Attack, all high trigger (native invocation index 0).",
  "Party: Ninja first, healer behind it, maxed fodder in the first row.",
  "The user sustains MP with Holy Herb and sometimes HP with Large Potion.",
];

export const USER_WAIRO_PRESET_ASSUMED: string[] = [
  "Healer: unspecified in the source, so a labelled REPRESENTATIVE canonical caster is used - Wizard rank S (Job.csv csvId 124), awakening 20, modest legal HP/MP 300, ONLY Heal Maddy + Backup (high trigger). Backup is the recovered passive that keeps it in the back row. Doctor was rejected (no canonical Job.csv rank).",
  "Fodder: LITERAL Scholar (Job.csv csvId 132); the canonical converter admits it via native-job-identity, so there is NO Merchant stand-in.",
  "Fodder \"maxed\" (baseline) = training level 20, the Job.csv base cap of a 0-awakening Scholar; the community recipe uses four weak fodder at level 1. The old 300-maxed claim is retracted.",
  "Ninja Speed: the displayed owner total 320 is the un-halved value and is NOT an effective stat; native effective Speed is 200 (raw 80 + Green Shield 240//2, affinity 0).",
  "Learned-skill ownership and actual slot upgrades are declared inputs (slot ceiling 9).",
  "Item timing is a disclosed EXAMPLE finite policy, NOT automatic item AI; declare ticks in the item schedule card.",
  "Resident-wide 'Water of ...' valuables are NOT stated in the source, so none is assumed owned; the conversion reports RESIDENT_VALUABLES_NOT_CAPTURED and the run uses a labelled declared-input zero.",
];

/**
 * Expand the JSON loadout map into the flat saved-loadout list the converter consumes, in JSON key
 * order (Ninja, healer, then the four Scholar fodder). A loadout with `count > 1` is deep-cloned that
 * many times with a unique name/id per clone, so the grouped fodder row becomes four distinct
 * fighters (6 units total) exactly like the canonical scenario generator, without reading the count
 * out of the key string.
 *
 * `holyHerbStock` 0 + empty inputs keep the preset a baseline; timing stays a declared input.
 */
export function userWairoPresetLoadouts(): SavedLoadout[] {
  return Object.entries(USER_WAIRO_PRESET.loadouts).flatMap(([name, loadout]) => {
    const count = Number.isInteger(loadout.count) && (loadout.count as number) > 1 ? (loadout.count as number) : 1;
    return Array.from({ length: count }, (_, cloneIndex) => {
      const unitName = count > 1 ? `${name} ${cloneIndex + 1}` : name;
      return {
        id: `user-wairo:${unitName}`,
        name: unitName,
        jobName: loadout.jobName,
        rank: loadout.rank,
        statLevels: { ...loadout.statLevels },
        awakening: loadout.awakening,
        equipment: loadout.equipment.map((entry) => ({ ...entry })),
        skills: [...loadout.skills],
      };
    });
  });
}

export function userWairoPresetConversionOptions() {
  return {
    encounterId: USER_WAIRO_PRESET.runner.encounterId,
    defeatCount: USER_WAIRO_PRESET.runner.defeatCount,
    seeds: {
      mathSeed: USER_WAIRO_PRESET.runner.seeds[0][0],
      libSeed: USER_WAIRO_PRESET.runner.seeds[0][1],
    },
    partyMax: 6,
    tickLimit: USER_WAIRO_PRESET.runner.tickLimit,
    holyHerbStock: USER_WAIRO_PRESET.runner.holyHerbStock,
    invocationLevelFor: () => 0 as const,
  };
}
