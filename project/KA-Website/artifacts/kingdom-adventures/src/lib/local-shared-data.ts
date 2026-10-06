import fallbackSharedData from "../../../api-server/data/ka_shared.json";
import { readBrowserCache, writeBrowserCache } from "@/lib/browser-cache";
import { completeMarriagePairs } from "@/game-data/job-marriage";

export const localSharedData = fallbackSharedData as Record<string, unknown>;

function mergeSharedData<T>(base: unknown, overlay: unknown): T {
  const baseRecord = typeof base === "object" && base !== null ? base as Record<string, unknown> : {};
  const overlayRecord = typeof overlay === "object" && overlay !== null ? overlay as Record<string, unknown> : {};
  return {
    ...baseRecord,
    ...overlayRecord,
    jobs: baseRecord.jobs ?? overlayRecord.jobs,
    pairs: completeMarriagePairs(
      overlayRecord.pairs as unknown[] | undefined,
      (baseRecord.pairs ?? []) as unknown[],
    ),
    equipIcons: {
      ...(baseRecord.equipIcons as Record<string, string> | undefined),
      ...(overlayRecord.equipIcons as Record<string, string> | undefined),
    },
    statIcons: {
      ...(baseRecord.statIcons as Record<string, string> | undefined),
      ...(overlayRecord.statIcons as Record<string, string> | undefined),
    },
  } as T;
}

export async function fetchSharedWithFallback<T>(url: string): Promise<T> {
  const cacheKey = "shared-data";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Shared API returned ${res.status}`);
    const data = mergeSharedData<T>(localSharedData, await res.json());
    writeBrowserCache(cacheKey, data);
    return data;
  } catch {
    const local = JSON.parse(JSON.stringify(localSharedData));
    const cached = readBrowserCache<unknown>(cacheKey);
    return cached ? mergeSharedData<T>(local, cached) : local as T;
  }
}
