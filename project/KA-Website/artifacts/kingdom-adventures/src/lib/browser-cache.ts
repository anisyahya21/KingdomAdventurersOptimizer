type CacheEntry<T> = {
  value: T;
  savedAt: number;
};

const CACHE_PREFIX = "ka_cache_v1:";

export function readBrowserCache<T>(key: string, maxAgeMs = Infinity): T | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(`${CACHE_PREFIX}${key}`);
    if (!raw) return undefined;
    const entry = JSON.parse(raw) as Partial<CacheEntry<T>>;
    if (!entry || typeof entry.savedAt !== "number" || !("value" in entry)) return undefined;
    if (Date.now() - entry.savedAt > maxAgeMs) return undefined;
    return entry.value as T;
  } catch {
    return undefined;
  }
}

export function writeBrowserCache<T>(key: string, value: T) {
  if (typeof window === "undefined") return;
  try {
    const entry: CacheEntry<T> = { value, savedAt: Date.now() };
    window.localStorage.setItem(`${CACHE_PREFIX}${key}`, JSON.stringify(entry));
  } catch {
    // Ignore quota/private-mode failures; caching should never block rendering.
  }
}
