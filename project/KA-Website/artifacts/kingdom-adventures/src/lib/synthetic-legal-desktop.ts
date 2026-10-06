export type SyntheticLegalDesktopLibrary = {
  schema: string;
  libraryId: string;
  libraryPath: string;
  readOnly: true;
  sourceFileWritten: false;
  library: unknown;
  note: string;
};

export type SyntheticLegalDesktopRecentLibrary = {
  path: string;
  filename: string;
};

export type SyntheticLegalDesktopApi = {
  open_optimizer_library: () => Promise<Record<string, unknown>>;
  open_optimizer_library_path: (path: string) => Promise<Record<string, unknown>>;
  list_recent_optimizer_libraries: () => Promise<Record<string, unknown>>;
  get_optimizer_library: (libraryId: string) => Promise<Record<string, unknown>>;
  get_optimizer_candidates: (libraryId: string, encounterId: number, offset: number, limit: number) => Promise<Record<string, unknown>>;
  get_optimizer_candidate: (libraryId: string, candidateId: string) => Promise<Record<string, unknown>>;
  preview_battle: (request: unknown) => Promise<unknown>;
};

declare global {
  interface Window {
    __KA_SYNTHETIC_LEGAL_DESKTOP__?: boolean;
  }
}

export function syntheticLegalDesktopApi(): SyntheticLegalDesktopApi | null {
  if (typeof window === "undefined") return null;
  const desktopWindow = window as unknown as { pywebview?: { api?: SyntheticLegalDesktopApi } };
  return desktopWindow.pywebview?.api ?? null;
}

export function isSyntheticLegalDesktop(): boolean {
  return typeof window !== "undefined" &&
    (window.__KA_SYNTHETIC_LEGAL_DESKTOP__ === true || Boolean(syntheticLegalDesktopApi()));
}

export function unwrapDesktopResponse<T>(response: Record<string, unknown>): T {
  if (response.ok === false) {
    throw new Error(typeof response.error === "string" ? response.error : "The desktop bridge could not complete the request.");
  }
  return response as T;
}
