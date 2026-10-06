// ─────────────────────────────────────────────────────────────────────────────
// sync-types.ts
//
// All TypeScript contracts for the KA sync system.
// Pure types only — no logic, no React, no imports from any other sync file.
// ─────────────────────────────────────────────────────────────────────────────

// ── Core enumerations ─────────────────────────────────────────────────────────

/**
 * How a feature persists and synchronises its state.
 *
 * - local-only               → localStorage only, never touches the server
 * - remote-readonly          → reads from the server, never writes
 * - remote-commit            → reads from the server, writes on explicit
 *                              commit actions (never auto-saves)
 * - local-with-manual-backup → primary state is localStorage; server is an
 *                              optional explicit backup/restore target
 */
export type SyncMode =
  | "local-only"
  | "remote-readonly"
  | "remote-commit"
  | "local-with-manual-backup";

/**
 * Who owns or shares the data.
 *
 * - community → shared across all users; server is authoritative
 * - personal  → private to this browser/user; local is authoritative
 * - link      → device identity and linking metadata; not a data payload
 */
export type SyncScope = "community" | "personal" | "link";

// ── Registry entry ────────────────────────────────────────────────────────────

/**
 * A single feature slice registered in the sync system.
 * Every field is readonly; the registry is never mutated at runtime.
 */
export type SyncFeature = {
  /** Unique identifier. Used as the lookup key throughout the system. */
  readonly id: string;

  /** The wouter route this feature belongs to (e.g. "/jobs"). */
  readonly route: string;

  readonly mode: SyncMode;
  readonly scope: SyncScope;

  /**
   * localStorage keys owned by this feature.
   * For local-only / personal features: the authoritative store.
   * For community features: a read cache only — never treated as the source of
   * truth when a server response is available.
   */
  readonly localKeys?: readonly string[];

  /**
   * API read endpoint.
   * Required for remote-readonly, remote-commit, local-with-manual-backup.
   * Omit for local-only features.
   */
  readonly readEndpoint?: string;

  /**
   * API write endpoint for features with a single PUT target.
   * Mutually exclusive with writeEndpoints.
   */
  readonly writeEndpoint?: string;

  /**
   * API write endpoints for features with multiple PUT targets (e.g. equipment).
   * Mutually exclusive with writeEndpoint.
   */
  readonly writeEndpoints?: readonly string[];

  /**
   * Polling interval in milliseconds for the shared query.
   * This is documentation metadata — it does not drive independent per-feature
   * polling. The TanStack Query cache deduplicates all reads to the same
   * endpoint regardless of how many features reference it.
   */
  readonly pollMs?: number;

  /**
   * Semantic names of the user actions that trigger a server commit.
   * Informational — consumed by hooks and Sync Center UI, not as strings at
   * runtime today.
   */
  readonly commitActions?: readonly string[];

  /**
   * Semantic names of the user actions that trigger a manual backup or restore.
   * Only meaningful for local-with-manual-backup features.
   */
  readonly backupActions?: readonly string[];

  readonly statusLabel: string;

  /**
   * Whether this feature participates in the sync system now.
   * false = exists in the registry for planning purposes but hooks must skip it.
   */
  readonly enabled: boolean;

  /**
   * Implementor notes. Not used at runtime.
   * Use for migration warnings, known gaps, or constraints future devs must
   * read before touching this slice.
   */
  readonly notes?: string;
};

// ── Status model ──────────────────────────────────────────────────────────────

export type SyncStatusLevel =
  | "idle"     // local-only, never syncs
  | "syncing"  // network request in flight
  | "success"  // last action completed successfully
  | "error"    // last action failed
  | "local";   // community feature has unsaved local changes (stale)

export type SyncStatus = {
  readonly featureId: string;
  readonly label: string;
  readonly level: SyncStatusLevel;
  /** Epoch ms of the last completed server round-trip. null means never. */
  readonly lastSyncAt: number | null;
  /** Human-readable description of the current state or last error. */
  readonly message?: string;
};

// ── Write payload ─────────────────────────────────────────────────────────────

/**
 * The envelope sent to every feature PUT endpoint.
 * Matches the shape the existing backend routes already expect.
 */
export type FeatureWritePayload<T> = {
  readonly data: T;
  readonly history: {
    readonly userName: string;
    /** Matches the changeType values already in use by the backend. */
    readonly changeType: string;
    readonly itemName: string;
    readonly description: string;
  };
};

/**
 * The normalised response shape all feature PUT endpoints should return.
 * The backend currently returns varied shapes; standardise during page
 * migrations, not during scaffolding.
 */
export type FeatureWriteResponse = {
  readonly ok: true;
  readonly updatedAt: number; // epoch ms
};

// ── Mutation hook contracts ───────────────────────────────────────────────────

/**
 * Options passed to useCommitMutation. Not the hook itself — just the shape.
 */
export type CommitMutationOptions<TPayload> = {
  readonly endpoint: string;
  readonly onSuccess?: () => void;
  readonly onError?: (err: unknown) => void;
};

/**
 * Return shape of useCommitMutation. Not the hook itself — just the shape.
 */
export type CommitMutationResult<TPayload> = {
  readonly commit: (payload: FeatureWritePayload<TPayload>) => Promise<void>;
  readonly isCommitting: boolean;
  readonly lastError: unknown | null;
};

// ── Synced feature hook return contract ───────────────────────────────────────

/**
 * Return shape of useSyncedFeature. Not the hook itself — just the shape.
 * Fields are conditionally present based on mode:
 *
 * - commit / isCommitting     → only when mode === "remote-commit"
 * - backupNow / restoreNow    → only when mode === "local-with-manual-backup"
 */
export type SyncedFeatureResult<T> = {
  readonly state: T;
  readonly setState: (next: T | ((prev: T) => T)) => void;
  readonly status: SyncStatus;
  readonly commit?: (payload: FeatureWritePayload<T>) => Promise<void>;
  readonly isCommitting?: boolean;
  readonly backupNow?: () => Promise<void>;
  readonly restoreNow?: () => Promise<void>;
};
