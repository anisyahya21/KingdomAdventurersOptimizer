import { STAT_CANONICAL, type StatKey } from "@/game-data/stat-parameter-ids";

/**
 * Canonical short stat key -> the key the shared `statIcons` table uses.
 *
 * The shared table (`api-server/data/ka_shared.json`, served as `/shared`) is keyed by the
 * job/equipment spellings - HP, MP, Vigor, Attack, Defence, Speed, Luck, Intelligence,
 * Dexterity, Gather, Move, Heart - NOT by the loadout builder's short keys (hp, mp, vig, ...).
 * This map (plus `getStatIcon`) is the one shared resolver every stats surface must use, so a
 * short-key lookup can never silently miss again.
 */
export const STAT_ICON_KEY: Record<StatKey, string> = {
  hp: "HP",
  mp: "MP",
  vig: "Vigor",
  atk: "Attack",
  def: "Defence",
  spd: "Speed",
  lck: "Luck",
  int: "Intelligence",
  dex: "Dexterity",
  gth: "Gather",
  mov: "Move",
  hrt: "Heart",
};

export type StatIconMap = Record<string, string> | undefined | null;

/** Normalise any stat spelling (short key, full name, label alias) to the shared table key. */
export function statIconKey(stat: string | undefined | null): string | undefined {
  if (!stat) return undefined;
  const trimmed = stat.trim();
  if (!trimmed) return undefined;
  const canonical = STAT_CANONICAL[trimmed.toLowerCase()];
  if (canonical && canonical in STAT_ICON_KEY) return STAT_ICON_KEY[canonical as StatKey];
  return undefined;
}

/** Resolve a stat's icon source from the shared `statIcons` table for any stat spelling. */
export function getStatIcon(icons: StatIconMap, stat: string | undefined | null): string | undefined {
  if (!icons || !stat) return undefined;
  const raw = stat.trim();
  if (!raw) return undefined;
  const direct = icons[raw];
  if (direct) return direct;
  const key = statIconKey(raw);
  if (key && icons[key]) return icons[key];
  const lower = raw.toLowerCase();
  return icons[lower];
}
