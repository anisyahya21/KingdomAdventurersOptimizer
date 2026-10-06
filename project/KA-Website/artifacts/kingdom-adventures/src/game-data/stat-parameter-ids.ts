/**
 * Canonical resident stat keys and their native combat parameter ids.
 *
 * Sources, in order of strength:
 *  * `docs/reverse-engineering/special-combat.md` (`MonsterData` curve mapping) names native ids
 *    10 HP, 11 MP, 13 Attack, 14 Defense, 15 Agility, 16 Luck, 19 Dexterity;
 *    `combat_native.py params` additionally names 12 Energy, 18 Intelligence, 20 Gathering and
 *    22 Love from live access sites. Native id 21 has no recovered combat access site.
 *  * `special-combat.md` also records that `EquipData.GetParamValue 0x16204c0` indexes an
 *    equipment row with `parameters[paramId-10]`, so the equipment table itself fixes the id
 *    order. The site equipment table (`api-server/data/ka_shared.json` `overrides`) and the
 *    recovered combat catalog (`src/game-data/battle-equipment-catalog.json`) agree on all 292
 *    shared equipment names and on every stat pair, which is what
 *    `tools/battle-setup-check/legality_check.mjs` asserts; the 13 parameter vectors are
 *    pairwise distinct, so that agreement determines the mapping uniquely.
 *
 * The site's short stat keys and their native ids therefore are:
 *   hp 10, mp 11, vig 12, atk 13, def 14, spd 15, lck 16, int 18, dex 19, gth 20, mov 21, hrt 22.
 * `mov` is the only key whose native name is not recovered - it is still the slot the equipment
 * table and the site totals both use, so it is converted rather than invented.
 */

/** Canonical short stat keys used by the loadout builder and the equipment/job tables. */
export const STAT_KEYS = ["hp", "mp", "vig", "atk", "def", "spd", "lck", "int", "dex", "gth", "mov", "hrt"] as const;
export type StatKey = (typeof STAT_KEYS)[number];

/**
 * Canonical display grouping of the twelve parameters: the native status screen and the loadout
 * builder both print them as three columns of 3 / 4 / 5 rows (vitals, physical, mental/utility).
 * This is presentation only - it is not a second stat list, it is an arrangement of `STAT_KEYS`.
 * Pages render this grouping instead of defining their own column order.
 */
export const STAT_COLUMNS: readonly (readonly StatKey[])[] = [
  ["hp", "mp", "vig"],
  ["atk", "def", "spd", "lck"],
  ["int", "dex", "gth", "mov", "hrt"],
];

/** Canonic short key -> native combat parameter id. */
export const STAT_PARAMETER_IDS: Record<StatKey, number> = {
  hp: 10,
  mp: 11,
  vig: 12,
  atk: 13,
  def: 14,
  spd: 15,
  lck: 16,
  int: 18,
  dex: 19,
  gth: 20,
  mov: 21,
  hrt: 22,
};

/** Native combat parameter id -> canonical short key. */
export const PARAMETER_STAT_KEYS: Record<number, StatKey> = Object.fromEntries(
  STAT_KEYS.map((key) => [STAT_PARAMETER_IDS[key], key]),
);

/** Native parameter id -> recovered native name (UNKNOWN where no access site names it). */
export const PARAMETER_NATIVE_NAMES: Record<number, string> = {
  10: "HP",
  11: "MP",
  12: "Energy",
  13: "Attack",
  14: "Defense",
  15: "Agility",
  16: "Luck",
  18: "Intelligence",
  19: "Dexterity",
  20: "Gathering",
  21: "UNKNOWN",
  22: "Love",
};

/**
 * Universal stat alias map - normalises any spelling/abbreviation to the canonical short key.
 * All variants are lowercased before lookup. The job tables use `Vigor`/`Defence`/`Gather`,
 * the equipment override tables use `Defence` as well, and the loadout page uses the short keys.
 */
export const STAT_CANONICAL: Record<string, string> = {
  // HP
  hp: "hp",
  // MP
  mp: "mp",
  // Vigor
  vig: "vig",
  vigor: "vig",
  // Attack
  atk: "atk",
  att: "atk",
  attack: "atk",
  // Defence / Defense
  def: "def",
  defence: "def",
  defense: "def",
  // Speed
  spd: "spd",
  speed: "spd",
  // Luck
  lck: "lck",
  luck: "lck",
  // Intelligence
  int: "int",
  intel: "int",
  intelligence: "int",
  // Dexterity
  dex: "dex",
  dext: "dex",
  dexterity: "dex",
  // Gather
  gth: "gth",
  gather: "gth",
  // Move / Movement
  mov: "mov",
  move: "mov",
  movement: "mov",
  // Heart
  hrt: "hrt",
  heart: "hrt",
};

/** Normalise a raw stat spelling to its canonical short key (identity when already canonical). */
export function canonicalStatKey(raw: string): string {
  return STAT_CANONICAL[raw.toLowerCase()] ?? raw.toLowerCase();
}
