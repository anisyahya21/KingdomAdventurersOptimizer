const RANK_ICON_CACHE_VERSION = "20260526r1";

const rankIconPathByLabel: Record<string, string> = {
  F: `/website_icons/ranks/rank_f.png?v=${RANK_ICON_CACHE_VERSION}`,
  E: `/website_icons/ranks/rank_e.png?v=${RANK_ICON_CACHE_VERSION}`,
  D: `/website_icons/ranks/rank_d.png?v=${RANK_ICON_CACHE_VERSION}`,
  C: `/website_icons/ranks/rank_c.png?v=${RANK_ICON_CACHE_VERSION}`,
  B: `/website_icons/ranks/rank_b.png?v=${RANK_ICON_CACHE_VERSION}`,
  A: `/website_icons/ranks/rank_a.png?v=${RANK_ICON_CACHE_VERSION}`,
  S: `/website_icons/ranks/rank_s.png?v=${RANK_ICON_CACHE_VERSION}`,
};

export function getRankIcon(rankLabel: string | undefined | null): string | undefined {
  if (!rankLabel) return undefined;
  const key = rankLabel.trim().toUpperCase();
  return rankIconPathByLabel[key];
}
