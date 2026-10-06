export type NavLink = { href: string; label: string; external?: boolean };
export type NavSection = { title: string; children: NavLink[] };

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Game Database",
    children: [
      { href: "/jobs", label: "Jobs" },
      { href: "/skill", label: "Skills" },
      { href: "/houses", label: "Houses" },
      { href: "/shops", label: "Shops" },
      { href: "/items-reference", label: "Items" },
      { href: "/equipment-stats", label: "Equipments" },
      { href: "/training-facilities", label: "Facilities" },
      { href: "/research", label: "Research" },
      { href: "/monster-spawn", label: "Enemies" },
      { href: "/monster-pet-stats", label: "Pets" },
      { href: "/world-map-v2", label: "Maps" },
    ],
  },
  {
    title: "Tools",
    children: [
      { href: "/match-finder", label: "Marriage Match Finder" },
      { href: "/loadout", label: "Loadout Builder" },
      { href: "/synthetic-legal", label: "Synthetic to Legal Builds" },
      { href: "/equipment-exchange", label: "Equipment Exchange" },
      { href: "/equipment-level-optimizer", label: "Equipment lv Optimizer" },
      { href: "/survey", label: "Survey Calculator" },
      { href: "/eggs", label: "Egg Planner" },
      { href: "/chaos-setup-lab", label: "Chaos Stone Setup" },
      { href: "/world-builder", label: "World Builder" },
    ],
  },
  {
    title: "Events",
    children: [
      { href: "/timed-events", label: "Overview" },
      { href: "/briefing-room", label: "Briefing Room" },
      { href: "/gacha-events", label: "Gacha Events" },
      { href: "/weekly-conquest", label: "Weekly Conquest" },
      { href: "/wario-dungeon", label: "Wairo Dungeon" },
      { href: "/daily-rank-rewards", label: "Daily Rank Rewards" },
      { href: "/kairo-room", label: "Kairo Room" },
      { href: "/job-center", label: "Job Center" },
    ],
  },
  {
    title: "Guides",
    children: [
      { href: "/jobs-marriage", label: "Job overview" },
      { href: "/playthrough-guide", label: "Playthrough" },
      { href: "/guides", label: "Tips tricks" },
    ],
  },
  {
    title: "Community",
    children: [
      { href: "https://discord.gg/5suHUXQ9p", label: "Kairosoft Community Discord", external: true },
    ],
  },
];
