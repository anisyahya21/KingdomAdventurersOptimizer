export type RawSource =
  | "google-sheet"
  | "google-sheet-plus-community"
  | "browser-local";

export type PublicMode =
  | "read-only"
  | "read-only-target"
  | "mixed"
  | "mixed-moving-to-read-only"
  | "interactive-local"
  | "database-target"
  | "provisional";

export type ContributionMode =
  | "structured-submission"
  | "structured-or-admin"
  | "admin-or-import"
  | "manual-until-decoded"
  | "not-applicable";

export interface SystemPolicy {
  rawSource: RawSource;
  translationStatus: string;
  publicMode: PublicMode;
  contributionMode: ContributionMode;
  notes: string[];
}

export const UI_RULES = {
  simpleSelectMaxOptions: 8,
  defaultPublicMode: "read-only",
} as const;

export const SYSTEM_POLICY: Record<string, SystemPolicy> = {
  equipment: {
    rawSource: "google-sheet",
    translationStatus: "in-progress",
    publicMode: "mixed-moving-to-read-only",
    contributionMode: "structured-or-admin",
    notes: [
      "Base equipment data comes from the sheet.",
      "Translated slot and weapon typing should eventually replace public manual editing where confirmed.",
      "Internal or junk entries should be filtered from the player-facing database.",
    ],
  },
  jobs: {
    rawSource: "google-sheet",
    translationStatus: "in-progress",
    publicMode: "read-only-target",
    contributionMode: "structured-or-admin",
    notes: [
      "Job base and growth data should be treated as shared truth.",
      "Job pages should present researched rules in player-friendly language.",
    ],
  },
  skills: {
    rawSource: "google-sheet",
    translationStatus: "in-progress",
    publicMode: "read-only-target",
    contributionMode: "structured-or-admin",
    notes: [
      "Combat and non-combat skill grouping still needs final translation.",
      "Craftability and hidden or unreachable skills should be filtered by translation rules.",
    ],
  },
  monsters: {
    rawSource: "google-sheet",
    translationStatus: "in-progress",
    publicMode: "mixed",
    contributionMode: "structured-or-admin",
    notes: [
      "Monster database truth should move toward translated sheet-backed data.",
      "Weekly conquest remains research-first until the rotation is decoded.",
    ],
  },
  weeklyConquest: {
    rawSource: "google-sheet",
    translationStatus: "research-first",
    publicMode: "provisional",
    contributionMode: "manual-until-decoded",
    notes: [
      "Do not assume future rotation prediction until the sheet pattern is confirmed.",
      "Determine whether rewards, monsters, or both follow a fixed cycle.",
    ],
  },
  pairs: {
    rawSource: "google-sheet",
    translationStatus: "usable-with-special-cases",
    publicMode: "read-only",
    contributionMode: "admin-or-import",
    notes: [
      "Compatible pairs are treated as loaded shared truth.",
      "Special-case child rules like Monarch -> Royal only belong in translation logic.",
    ],
  },
  shops: {
    rawSource: "google-sheet-plus-community",
    translationStatus: "in-progress",
    publicMode: "database-target",
    contributionMode: "structured-submission",
    notes: [
      "Ownership patterns are already understandable.",
      "Furniture prices and unresolved shop outputs still need collection and translation.",
    ],
  },
  loadout: {
    rawSource: "browser-local",
    translationStatus: "uses-shared-rules",
    publicMode: "interactive-local",
    contributionMode: "not-applicable",
    notes: [
      "Loadouts are personal planning state and should stay browser-local.",
      "Confirmed game rules must still be applied consistently in calculations.",
    ],
  },
};
