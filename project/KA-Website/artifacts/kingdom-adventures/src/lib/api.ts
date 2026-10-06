const STORED_API_BASE_KEY = "kaApiBaseUrl";

export function configuredApiBase() {
  const envBase = String(import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
  if (typeof window === "undefined") return envBase;

  const isLocalHost = /^(localhost|127\.0\.0\.1|192\.168\.)$/i.test(window.location.hostname);
  if (window.location.protocol === "https:" && !isLocalHost && envBase) {
    try {
      const envOrigin = new URL(envBase).origin;
      if (envOrigin !== window.location.origin) {
        // Prefer same-origin /ka-api rewrites in production to keep auth cookies first-party on mobile browsers.
        return "";
      }
    } catch {
      // Ignore invalid env base and continue with existing logic.
    }
  }

  const params = new URLSearchParams(window.location.search);
  const queryBase = params.get("apiBase")?.trim().replace(/\/$/, "");
  if (queryBase) {
    window.localStorage.setItem(STORED_API_BASE_KEY, queryBase);
    return queryBase;
  }

  const storedBase = (window.localStorage.getItem(STORED_API_BASE_KEY) || "").replace(/\/$/, "");
  if (window.location.protocol === "https:" && /^http:\/\/(localhost|127\.0\.0\.1|192\.168\.)/i.test(storedBase)) {
    window.localStorage.removeItem(STORED_API_BASE_KEY);
    return envBase;
  }
  if (storedBase && window.location.protocol === "https:") {
    try {
      const storedUrl = new URL(storedBase);
      const liveApiOverride = storedUrl.origin !== window.location.origin && storedUrl.origin !== envBase;
      if (liveApiOverride && !/^(localhost|127\.0\.0\.1|192\.168\.)$/i.test(window.location.hostname)) {
        window.localStorage.removeItem(STORED_API_BASE_KEY);
        return envBase;
      }
    } catch {
      window.localStorage.removeItem(STORED_API_BASE_KEY);
      return envBase;
    }
  }

  return (storedBase || envBase).replace(/\/$/, "");
}

export function saveConfiguredApiBase(value: string) {
  const clean = value.trim().replace(/\/$/, "");
  if (!clean) window.localStorage.removeItem(STORED_API_BASE_KEY);
  else window.localStorage.setItem(STORED_API_BASE_KEY, clean);
  return clean;
}

const SHEET_FALLBACK_URLS: Record<string, string> = {
  equipment: "https://docs.google.com/spreadsheets/d/1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk/gviz/tq?tqx=out:json&gid=123527243",
  "shops-items": "https://docs.google.com/spreadsheets/d/1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk/gviz/tq?tqx=out:json&gid=1863106351",
  "weekly-conquest-lookup": "https://docs.google.com/spreadsheets/d/1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk/gviz/tq?tqx=out:json&gid=421403004",
  "weekly-conquest-schedule": "https://docs.google.com/spreadsheets/d/1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk/gviz/tq?tqx=out:json&gid=1625050714",
  eggs: "https://docs.google.com/spreadsheets/d/1pNx7SjpgjuKFI9Hgr21y3ammRlZjKNTTdvfLYQL7l7A/gviz/tq?tqx=out:json&gid=1439838004",
};

export function apiUrl(path: string): string {
  const apiBase = configuredApiBase();
  const clean = path.startsWith("/") ? path : "/" + path;
  return `${apiBase}/ka-api/ka${clean}`;
}

/** Returns the API-proxied URL for a whitelisted Google Sheet cache key. */
export function googleSheetUrl(key: string): string {
  const apiBase = configuredApiBase();
  if (apiBase) {
    return `${apiBase}/ka-api/ka/google/sheet/${encodeURIComponent(key)}`;
  }
  return SHEET_FALLBACK_URLS[key] ?? `/ka-api/ka/google/sheet/${encodeURIComponent(key)}`;
}

/** Returns the API-proxied URL for a Google Doc cache entry. */
export function googleDocUrl(docId: string): string {
  const apiBase = configuredApiBase();
  if (apiBase) {
    return `${apiBase}/ka-api/ka/google/doc/${encodeURIComponent(docId)}`;
  }
  return `https://docs.google.com/document/d/${encodeURIComponent(docId)}/export?format=md`;
}
