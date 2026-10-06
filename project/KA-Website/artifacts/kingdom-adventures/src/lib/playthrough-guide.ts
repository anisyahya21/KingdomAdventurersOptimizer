export const PLAYTHROUGH_GUIDE_LOCAL_URL = "/guides/playthrough-guide.md";

export const PLAYTHROUGH_GUIDE_DOC_ID = "1HDY-6lgFfjIX9KfzmuBrR4xsSMWGXKViccsGQJ_o5yE";

export type GuideOverlay = {
  title: string;
  description: string;
  href: string;
  cta: string;
};

export const PLAYTHROUGH_GUIDE_SECTION_OVERLAYS: Record<string, GuideOverlay> = {
  "Marriage (Simple)": {
    title: "Use the site tools for pair planning",
    description: "Jump to Match Finder and Marriage Sim for compatibility checks and outcome planning.",
    href: "/match-finder",
    cta: "Open Match Finder",
  },
  "Lv 999 Children": {
    title: "Open the marriage simulator",
    description: "Use the Match Finder tools to plan pairings and child outcomes while reading this section.",
    href: "/match-finder",
    cta: "Open Match Finder",
  },
  "Useful Skills": {
    title: "Browse the site's skill reference",
    description: "Open the Skills page if you want the searchable skill database alongside the guide.",
    href: "/skills",
    cta: "Open Skills",
  },
};
