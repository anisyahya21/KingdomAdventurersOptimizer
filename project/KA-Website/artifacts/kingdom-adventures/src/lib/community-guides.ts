import { apiUrl } from "@/lib/api";
import { readBrowserCache, writeBrowserCache } from "@/lib/browser-cache";

export type CommunityGuide = {
  id: string;
  slug: string;
  title: string;
  author: string;
  docUrl: string;
  docId: string;
  createdAt: number;
  updatedAt: number;
  editable?: boolean;
  linkOverrides?: GuideLinkOverrides;
};

export type GuideCustomLink = {
  id: string;
  phrase: string;
  href: string;
  target?: GuideLinkTarget;
  occurrenceKey?: string;
};

export type GuideCustomIcon = {
  id: string;
  phrase: string;
  iconSrc: string;
  occurrenceKey?: string;
};

export type GuideLinkTarget =
  | { type: "equipment"; equipmentName: string }
  | { type: "job"; jobName: string }
  | { type: "equipment-set"; equipment: Array<{ name: string; level: number }> }
  | { type: "marriage-sim"; parentA?: string; parentB?: string; child?: string }
  | { type: "custom"; href: string };

export type GuideLinkOverrides = {
  disabledAutoLinks: string[];
  disabledOccurrences?: string[];
  disabledAutoIcons?: string[];
  disabledIconOccurrences?: string[];
  customLinks: GuideCustomLink[];
  customIcons?: GuideCustomIcon[];
};

const OWNER_KEY = "ka_community_guide_owner_tokens";
const LINK_OVERRIDES_KEY = "ka_community_guide_link_overrides";

const EMPTY_LINK_OVERRIDES: GuideLinkOverrides = {
  disabledAutoLinks: [],
  disabledOccurrences: [],
  disabledAutoIcons: [],
  disabledIconOccurrences: [],
  customLinks: [],
  customIcons: [],
};

export function getGuideOwnerTokens(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(OWNER_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function setGuideOwnerToken(guideId: string, token: string) {
  const current = getGuideOwnerTokens();
  current[guideId] = token;
  localStorage.setItem(OWNER_KEY, JSON.stringify(current));
}

export function removeGuideOwnerToken(guideId: string) {
  const current = getGuideOwnerTokens();
  delete current[guideId];
  localStorage.setItem(OWNER_KEY, JSON.stringify(current));
}

function getAllGuideLinkOverrides(): Record<string, GuideLinkOverrides> {
  try {
    const raw = JSON.parse(localStorage.getItem(LINK_OVERRIDES_KEY) ?? "{}") as Record<string, Partial<GuideLinkOverrides>>;
    return Object.fromEntries(
      Object.entries(raw).map(([guideId, overrides]) => [
        guideId,
        {
          disabledAutoLinks: Array.isArray(overrides.disabledAutoLinks) ? overrides.disabledAutoLinks : [],
          disabledOccurrences: Array.isArray(overrides.disabledOccurrences) ? overrides.disabledOccurrences : [],
          disabledAutoIcons: Array.isArray(overrides.disabledAutoIcons) ? overrides.disabledAutoIcons : [],
          disabledIconOccurrences: Array.isArray(overrides.disabledIconOccurrences) ? overrides.disabledIconOccurrences : [],
          customLinks: Array.isArray(overrides.customLinks) ? overrides.customLinks : [],
          customIcons: Array.isArray(overrides.customIcons) ? overrides.customIcons : [],
        },
      ]),
    );
  } catch {
    return {};
  }
}

export function getGuideLinkOverrides(guideId: string): GuideLinkOverrides {
  const overrides = getAllGuideLinkOverrides()[guideId];
  if (!overrides) return EMPTY_LINK_OVERRIDES;
  return {
    disabledAutoLinks: [...overrides.disabledAutoLinks],
    disabledOccurrences: [...(overrides.disabledOccurrences ?? [])],
    disabledAutoIcons: [...(overrides.disabledAutoIcons ?? [])],
    disabledIconOccurrences: [...(overrides.disabledIconOccurrences ?? [])],
    customLinks: [...overrides.customLinks],
    customIcons: [...(overrides.customIcons ?? [])],
  };
}

export function setGuideLinkOverrides(guideId: string, overrides: GuideLinkOverrides) {
  const current = getAllGuideLinkOverrides();
  current[guideId] = normalizeGuideLinkOverrides(overrides);
  localStorage.setItem(LINK_OVERRIDES_KEY, JSON.stringify(current));
}

export function normalizeGuideLinkOverrides(overrides?: Partial<GuideLinkOverrides> | null): GuideLinkOverrides {
  return {
    disabledAutoLinks: Array.from(new Set((overrides?.disabledAutoLinks ?? []).map((label) => label.trim().toLowerCase()).filter(Boolean))),
    disabledOccurrences: Array.from(new Set((overrides?.disabledOccurrences ?? []).map((key) => key.trim()).filter(Boolean))),
    disabledAutoIcons: Array.from(new Set((overrides?.disabledAutoIcons ?? []).map((label) => label.trim().toLowerCase()).filter(Boolean))),
    disabledIconOccurrences: Array.from(new Set((overrides?.disabledIconOccurrences ?? []).map((key) => key.trim()).filter(Boolean))),
    customLinks: (overrides?.customLinks ?? [])
      .map((link) => ({
        id: link.id,
        phrase: link.phrase.trim(),
        href: link.href.trim(),
        target: normalizeGuideLinkTarget(link.target),
        occurrenceKey: String(link.occurrenceKey ?? "").trim() || undefined,
      }))
      .filter((link) => link.phrase && link.href),
    customIcons: (overrides?.customIcons ?? [])
      .map((icon) => ({
        id: icon.id,
        phrase: String(icon.phrase ?? "").trim(),
        iconSrc: String(icon.iconSrc ?? "").trim(),
        occurrenceKey: String(icon.occurrenceKey ?? "").trim() || undefined,
      }))
      .filter((icon) => icon.phrase && icon.iconSrc),
  };
}

function normalizeGuideLinkTarget(target?: GuideLinkTarget | null): GuideLinkTarget | undefined {
  if (!target || typeof target !== "object") return undefined;
  if (target.type === "equipment") {
    const equipmentName = String(target.equipmentName ?? "").trim();
    return equipmentName ? { type: "equipment", equipmentName } : undefined;
  }
  if (target.type === "job") {
    const jobName = String(target.jobName ?? "").trim();
    return jobName ? { type: "job", jobName } : undefined;
  }
  if (target.type === "equipment-set") {
    const equipment = (Array.isArray(target.equipment) ? target.equipment : [])
      .map((item) => ({
        name: String(item.name ?? "").trim(),
        level: Math.min(99, Math.max(1, Math.round(Number(item.level) || 99))),
      }))
      .filter((item) => item.name);
    return equipment.length ? { type: "equipment-set", equipment } : undefined;
  }
  if (target.type === "marriage-sim") {
    return {
      type: "marriage-sim",
      parentA: String(target.parentA ?? "").trim(),
      parentB: String(target.parentB ?? "").trim(),
      child: String(target.child ?? "").trim(),
    };
  }
  if (target.type === "custom") {
    const href = String(target.href ?? "").trim();
    return href ? { type: "custom", href } : undefined;
  }
  return undefined;
}

export async function saveGuideLinkOverrides(guideId: string, ownerToken: string | undefined, overrides: GuideLinkOverrides, title?: string) {
  const normalized = normalizeGuideLinkOverrides(overrides);
  setGuideLinkOverrides(guideId, normalized);
  const body: Record<string, unknown> = { title, linkOverrides: normalized };
  if (ownerToken) body.ownerToken = ownerToken;
  const response = await fetch(apiUrl(`/guides/${guideId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? "Could not save guide links.");
  return payload as { guide: CommunityGuide };
}

export function extractGoogleDocId(url: string) {
  return url.match(/\/document\/d\/([a-zA-Z0-9_-]+)/)?.[1] ?? "";
}

export async function fetchCommunityGuides() {
  const response = await fetch(apiUrl("/guides"), { credentials: "include" });
  if (!response.ok) throw new Error("Could not load guides.");
  const payload = await response.json() as { guides: CommunityGuide[] };
  writeBrowserCache("community-guides", payload);
  return payload;
}

export function getCachedCommunityGuides(maxAgeMs = 24 * 60 * 60 * 1000) {
  return readBrowserCache<{ guides: CommunityGuide[] }>("community-guides", maxAgeMs);
}
