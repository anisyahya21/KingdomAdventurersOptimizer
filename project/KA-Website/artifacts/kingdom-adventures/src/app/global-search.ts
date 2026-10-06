import { localSharedData } from "@/lib/local-shared-data";
import { SHOP_RECORDS } from "@/lib/shop-utils";
import { getJobProfiles, type SharedJobProfileData } from "@/game-data/job-profile";

export type GlobalSearchEntry = { label: string; subtitle: string; href: string };

const FURNITURE_SEARCH_ROWS = [
  "Candle",
  "Kitchen Shelves",
  "Desk",
  "Red Carpet",
  "Decorative Plant",
  "Dining Table",
  "Study Desk",
  "Rainwater Barrel",
  "Chest of Drawers",
  "Flower Vase",
  "Shelf",
  "Bookshelf",
  "Training Room",
  "Rejuvenating Bath",
  "Flowers",
  "Tomato",
  "Dresser",
  "Couch",
  "Bathtub",
  "Stove",
  "Pansy",
  "Shooting Range",
  "Fluffy Carpet",
  "Cooking Counter",
  "Decorative Armor",
  "Vanity Mirror",
  "Window",
  "Magic Training Ground",
  "Glittering Stone",
  "Black Mat",
  "Fireplace",
  "Tree Nursery",
  "Ancestor Statue",
  "Animal Figurine",
  "Tool Workshop",
  "Ore Workbench",
  "Double Bed",
];

export function buildGlobalSearchEntries(): GlobalSearchEntry[] {
  const shared = localSharedData as {
    jobs?: Record<string, unknown>;
    monsters?: Record<string, unknown>;
    skills?: Record<string, unknown>;
    overrides?: Record<string, unknown>;
  };
  const entries: GlobalSearchEntry[] = [];

  entries.push({
    label: "Add Friends",
    subtitle: "Community tools",
    href: "/add-friends",
  });

  getJobProfiles(shared as SharedJobProfileData).forEach((profile) => {
    const relatedFacts = [
      profile.surveyCapable ? "Survey Corps HQ" : null,
      profile.shops.length > 0 ? profile.shops.join(", ") : null,
      profile.marriage.children.length > 0 ? `children: ${profile.marriage.children.join(", ")}` : null,
    ].filter(Boolean);

    entries.push({
      label: profile.name,
      subtitle: relatedFacts.length > 0 ? `Job Database - ${relatedFacts.join(" - ")}` : "Job Database",
      href: `/jobs/${encodeURIComponent(profile.name)}`,
    });
  });

  Object.keys(shared.monsters ?? {}).forEach((name) =>
    entries.push({ label: name, subtitle: "Monster Spawns & Loot", href: "/monster-spawns" }),
  );

  Object.keys(shared.skills ?? {}).forEach((name) =>
    entries.push({ label: name, subtitle: "Skills Database", href: "/skills" }),
  );

  Object.keys(shared.overrides ?? {}).forEach((name) => {
    entries.push({ label: name, subtitle: "Equipment Database", href: "/equipment-stats" });
  });

  FURNITURE_SEARCH_ROWS.forEach((name) =>
    entries.push({ label: name, subtitle: "Furniture Shop", href: `/shops/furniture-shop?search=${encodeURIComponent(name)}` }),
  );

  SHOP_RECORDS.forEach((shop) =>
    entries.push({ label: shop.title, subtitle: "Shops and items", href: `/shops/${shop.slug}` }),
  );

  return entries;
}
