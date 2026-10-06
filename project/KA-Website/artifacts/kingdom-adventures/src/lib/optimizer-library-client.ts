import { syntheticLegalDesktopApi, unwrapDesktopResponse, type SyntheticLegalDesktopLibrary } from "@/lib/synthetic-legal-desktop";

export type OptimizerLibraryInfo = {
  schema: string;
  supported: boolean;
  librarySchemaVersion: number;
  filename: string;
  candidateCount: number;
  encounters: Array<{ id: number; candidateCount: number }>;
  tables: string[];
  metadata: Record<string, unknown>;
};

export type OptimizerLibrarySession = {
  schema: string;
  libraryId: string;
  readOnly: true;
  sourceFileWritten: false;
  note: string;
  library: OptimizerLibraryInfo;
  libraryPath?: string;
};

export type QualifiedOptimizerEvidence = {
  phase: string; label: string; meanEarned: number; highestEarned: number; sampleCount: number;
  uncertainty: { kind: string; value: number | null }; pairedInterval?: number[];
  engineRevision: string; mechanicsRevision: string; encounterRevision: string;
  policy: Record<string, unknown>; measurementWindow: unknown; encounterId: number;
};

export type OptimizerCandidateRecord = {
  qualifiedEvidence?: QualifiedOptimizerEvidence[];
  buildExpressibility?: { status: string; reason: string };
  id: string;
  label: string | null;
  source: string | null;
  family: string | null;
  encounterIdentity: { indexedId: number | null; scenarioId: number | null; matches: boolean };
  defeatCount: number | null;
  region: string | null;
  performance: {
    phase: string;
    runs: number;
    meanEarned: number | null;
    highestEarned: number | null;
    sampleCount: number;
    uncertainty: { kind: string; value: number | null };
    discovery: Record<string, unknown>;
    validation: Record<string, unknown>;
  };
  scenario?: unknown;
  stats?: unknown;
  lineage?: unknown;
  unknownFields?: Record<string, unknown>;
};

export type OptimizerCandidatePage = {
  schema: string;
  encounterId: number;
  total: number;
  offset: number;
  limit: number;
  candidates: OptimizerCandidateRecord[];
};

const PREFIX = "/api/optimizer-library/libraries";

/** Use the native desktop chooser when present; browsers keep the upload fallback. */
export async function importOptimizerLibrary(files?: FileList | File[], recentPath?: string): Promise<OptimizerLibrarySession | null> {
  const desktop = syntheticLegalDesktopApi();
  if (desktop) {
    const response = recentPath
      ? await desktop.open_optimizer_library_path(recentPath)
      : await desktop.open_optimizer_library();
    if (response.cancelled === true) return null;
    return unwrapDesktopResponse<SyntheticLegalDesktopLibrary & { library: OptimizerLibraryInfo }>(response);
  }
  if (!files?.length) throw new Error("Choose a SQLite library.");
  const selected = Array.from(files);
  const database = selected.find((file) => /\.(sqlite|sqlite3|db)$/i.test(file.name));
  if (!database) throw new Error("Choose a .sqlite, .sqlite3, or .db library file.");
  const created = await request<OptimizerLibrarySession>(`${PREFIX}`, {
    method: "POST",
    headers: { "content-type": "application/octet-stream", "x-library-filename": encodeURIComponent(database.name) },
    body: database,
  });
  const sidecars = selected.filter((file) => file.name.startsWith(database.name) && /(-(?:wal|shm|journal)|\.manifest\.json)$/i.test(file.name));
  for (const sidecar of sidecars) {
    await request(`${PREFIX}/${created.libraryId}/files`, {
      method: "POST",
      headers: { "content-type": "application/octet-stream", "x-library-filename": encodeURIComponent(sidecar.name) },
      body: sidecar,
    });
  }
  const library = await request<OptimizerLibraryInfo>(`${PREFIX}/${created.libraryId}`);
  return { ...created, library };
}

export async function getOptimizerCandidates(
  libraryId: string,
  encounterId: number,
  offset = 0,
  limit = 50,
): Promise<OptimizerCandidatePage> {
  const desktop = syntheticLegalDesktopApi();
  if (desktop) {
    return unwrapDesktopResponse<OptimizerCandidatePage>(
      await desktop.get_optimizer_candidates(libraryId, encounterId, offset, limit),
    );
  }
  const query = new URLSearchParams({ encounterId: String(encounterId), offset: String(offset), limit: String(limit) });
  return request(`${PREFIX}/${libraryId}/candidates?${query}`);
}

export async function getOptimizerLibrary(libraryId: string): Promise<OptimizerLibraryInfo> {
  const desktop = syntheticLegalDesktopApi();
  if (desktop) return unwrapDesktopResponse<OptimizerLibraryInfo>(await desktop.get_optimizer_library(libraryId));
  return request(`${PREFIX}/${libraryId}`);
}

export async function getOptimizerCandidate(libraryId: string, candidateId: string): Promise<OptimizerCandidateRecord> {
  const desktop = syntheticLegalDesktopApi();
  if (desktop) return unwrapDesktopResponse<OptimizerCandidateRecord>(await desktop.get_optimizer_candidate(libraryId, candidateId));
  return request(`${PREFIX}/${libraryId}/candidates/${encodeURIComponent(candidateId)}`);
}

async function request<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Optimizer library bridge returned invalid JSON (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const message = payload && typeof payload === "object" && "message" in payload && typeof payload.message === "string"
      ? payload.message
      : `Optimizer library request failed (HTTP ${response.status}).`;
    throw new Error(message);
  }
  return payload as T;
}
