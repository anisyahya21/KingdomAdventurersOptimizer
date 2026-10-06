// ─────────────────────────────────────────────────────────────────────────────
// sync-registry.ts
//
// Single source of truth for every feature slice in the KA sync system.
// Adding a feature here is the only change needed to bring it under the system.
//
// Rules enforced by convention (not runtime checks — add a validator hook later):
//   • local-only features must not set readEndpoint or writeEndpoint/writeEndpoints
//   • link-scope features must not carry community or personal data payloads
//   • pollMs is documentation metadata — it does not start independent polling
//   • enabled:false entries are planned but must be skipped by all hooks
//
// Excluded from this registry (intentional, permanent unless re-evaluated):
//   • /world-map  — clipboard import/export tool; no server sync surface
//   • /eggs       — read-only external viewer (Google Sheets); no KA API surface
//   • /shops      — read-only external viewer (Google Sheets); no KA API surface
//   • weekly-conquest-automatic query in Monsters — external Google Sheets source
// ─────────────────────────────────────────────────────────────────────────────

import type { SyncFeature } from "./sync-types";

// ─────────────────────────────────────────────────────────────────────────────
// Registry
// ─────────────────────────────────────────────────────────────────────────────

export const SYNC_REGISTRY: readonly SyncFeature[] = [

  // ── Home ────────────────────────────────────────────────────────────────────

  {
    id: "home-projects",
    route: "/",
    mode: "local-only",
    scope: "personal",
    localKeys: ["ka_custom_projects"],
    statusLabel: "Home Projects",
    enabled: true,
  },

  // ── Jobs ─────────────────────────────────────────────────────────────────────

  {
    id: "jobs-data",
    route: "/jobs",
    mode: "remote-commit",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    writeEndpoint: "/ka-api/ka/jobs",
    pollMs: 30000,
    commitActions: ["saveJob"],
    statusLabel: "Job Database",
    enabled: true,
  },

  {
    id: "jobs-pairs",
    route: "/jobs",
    mode: "remote-commit",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    writeEndpoint: "/ka-api/ka/pairs",
    // ka_mf_jobNames is a community read-cache for offline resilience.
    // It lives here, NOT in match-finder-planner, because it reflects community
    // data (Gen1 job names), not personal planner choices.
    localKeys: ["ka_mf_jobNames"],
    pollMs: 30000,
    commitActions: ["savePairs"],
    statusLabel: "Compatible Pairs (Jobs)",
    enabled: true,
    notes:
      "Shares PUT /ka-api/ka/pairs with match-finder-pairs. Both must produce " +
      "the same canonical SharedPair[] payload shape. Concurrent writes from " +
      "both routes use last-write-wins (Phase 1). Precondition header deferred " +
      "to Phase 2.",
  },

  {
    id: "jobs-prefs",
    route: "/jobs",
    mode: "local-only",
    scope: "personal",
    localKeys: ["ka_fav_jobs", "ka_jobs_compact_view", "ka_note_jobs"],
    statusLabel: "Job Preferences",
    enabled: true,
  },

  // ── Skills ────────────────────────────────────────────────────────────────────

  {
    id: "skills-data",
    route: "/skills",
    mode: "remote-commit",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    writeEndpoint: "/ka-api/ka/skills",
    pollMs: 30000,
    commitActions: ["saveSkillNotes"],
    statusLabel: "Skill Database",
    enabled: true,
    notes: "First recommended migration target. Simplest writer in the codebase.",
  },

  // ── Equipment ─────────────────────────────────────────────────────────────────

  {
    id: "equipment-shared",
    route: "/equipment-stats",
    mode: "remote-commit",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    // Multiple write sub-endpoints. Do not use the single writeEndpoint field.
    writeEndpoints: [
      "/ka-api/ka/shared/overrides",
      "/ka-api/ka/shared/slots",
      "/ka-api/ka/shared/icons/equip",
      "/ka-api/ka/shared/icons/stat",
      "/ka-api/ka/shared/weapon-types",
      "/ka-api/ka/shared/weapon-categories",
      // Note: /shared/rename-user is a POST, not a PUT. Treated separately.
      "/ka-api/ka/shared/rename-user",
    ],
    pollMs: 15000,
    statusLabel: "Equipment Database",
    enabled: true,
    notes:
      "Most complex writer. Six PUT sub-endpoints plus a POST rename-user. " +
      "Migrate last among community features. rename-user rewrites history " +
      "across the full shared state. Do not batch with other migrations.",
  },

  // ── Monsters ──────────────────────────────────────────────────────────────────

  {
    id: "monsters-data",
    route: "/monsters",
    mode: "remote-readonly",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    pollMs: 30000,
    statusLabel: "Monster Database",
    enabled: true,
    notes:
      "The weekly-conquest-automatic query (queryKey: weekly-conquest-automatic) " +
      "fetches from an external Google Sheets URL via fetchAutomaticWeeklyConquestTimeline. " +
      "It is excluded from this registry. Do not add it here.",
  },

  {
    id: "monsters-tracker",
    route: "/monsters",
    mode: "local-only",
    scope: "personal",
    localKeys: [
      "ka_spawn_levels",
      "ka_conquest_covered_areas",
      "ka_note_monsters",
    ],
    statusLabel: "Monster Tracker",
    enabled: true,
  },

  // ── Match Finder ──────────────────────────────────────────────────────────────

  {
    id: "match-finder-pairs",
    route: "/match-finder",
    mode: "remote-commit",
    scope: "community",
    readEndpoint: "/ka-api/ka/shared",
    writeEndpoint: "/ka-api/ka/pairs",
    // ka_mf_pairs is a local read-cache for offline resilience, NOT personal
    // planner state. Community writes from the server must overwrite it.
    localKeys: ["ka_mf_pairs"],
    pollMs: 30000,
    commitActions: ["pairsModified"],
    statusLabel: "Match Finder Pairs",
    enabled: true,
    notes:
      "Shares PUT /ka-api/ka/pairs with jobs-pairs. Same canonical payload " +
      "contract required. ka_mf_pairs is a cache of community data — it must " +
      "never be treated as a personal override.",
  },

  {
    id: "match-finder-planner",
    route: "/match-finder",
    mode: "local-only",
    scope: "personal",
    localKeys: [
      "ka_mf_rankSlots",
      "ka_mf_lockedPairs",
      "ka_mf_desiredChildren",
      "ka_mf_targetChildTypeFilter",
      "ka_mf_targetExclusiveFilter",
      "ka_mf_targetIncludeJobs",
      "ka_mf_targetExcludeJobs",
      "ka_note_marriage",
      // ka_mf_jobNames intentionally excluded here — it belongs to
      // match-finder-pairs as a community read-cache, not planner state.
    ],
    statusLabel: "Match Finder Planner",
    enabled: true,
    notes:
      "Personal planner state. Must never be auto-overwritten by remote state. " +
      "Remote restore is a future opt-in (local-with-manual-backup upgrade path).",
  },

  // ── Loadout ───────────────────────────────────────────────────────────────────

  {
    id: "loadout-builds",
    route: "/loadout",
    mode: "local-only",
    scope: "personal",
    localKeys: ["ka_loadouts", "ka_note_loadout"],
    statusLabel: "Loadout Builds",
    enabled: true,
    notes:
      "Mode will upgrade to local-with-manual-backup once " +
      "GET/PUT /ka-api/ka/user/loadouts is implemented. Backend endpoint does " +
      "not exist yet. Do not set readEndpoint/writeEndpoint until it does.",
  },

  // ── Device Link ───────────────────────────────────────────────────────────────

  {
    id: "device-link",
    route: "/sync-devices",
    mode: "local-only",
    scope: "link",
    // Exact key names as defined by LOCAL_DEVICE_ID_KEY / LOCAL_DEVICE_NAME_KEY
    // in sync-devices.tsx.
    localKeys: ["kaSyncCurrentDeviceId", "kaSyncCurrentDeviceName"],
    // readEndpoint listed for documentation; device-link API calls are managed
    // directly by sync-devices.tsx, not through the shared feature sync hooks.
    readEndpoint: "/ka-api/ka/sync/devices",
    statusLabel: "Device Link",
    enabled: true,
    notes:
      "Link plane only. Carries device identity, not feature payloads. " +
      "syncedDevices are persisted in the shared state file; one-time syncCodes " +
      "remain in-memory and expire quickly. Sync Center UI must distinguish " +
      "durable linked devices from short-lived linking codes.",
  },

] satisfies readonly SyncFeature[];

// ─────────────────────────────────────────────────────────────────────────────
// Lookup helpers — pure functions, no side effects
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns the feature entry for the given id, or undefined if not registered.
 * In development, callers should assert the return value is not undefined when
 * passing a known literal id.
 */
export function getFeature(id: string): SyncFeature | undefined {
  return SYNC_REGISTRY.find((f) => f.id === id);
}

/**
 * Returns all feature entries registered for the given route.
 * A single route can have multiple slices (e.g. /jobs has jobs-data,
 * jobs-pairs, and jobs-prefs).
 */
export function getFeaturesByRoute(route: string): readonly SyncFeature[] {
  return SYNC_REGISTRY.filter((f) => f.route === route);
}

/**
 * Returns all enabled feature entries, omitting registry placeholders
 * (enabled: false) that exist only for planning purposes.
 */
export function getEnabledFeatures(): readonly SyncFeature[] {
  return SYNC_REGISTRY.filter((f) => f.enabled);
}
