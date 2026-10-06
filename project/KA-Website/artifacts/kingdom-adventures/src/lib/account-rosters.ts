import { apiUrl } from "@/lib/api";
import type { DraftCharacter, DraftConsumables } from "@/lib/battle-team-draft";
import type { ResidentStatItemCounts } from "@/game-data/resident-stat-items";

export type SavedRoster = {
  id: string;
  name: string;
  encounterId: number;
  characters: DraftCharacter[];
  consumables: DraftConsumables;
  partyBonus?: number;
  residentStatItems?: ResidentStatItemCounts;
  updatedAt?: string;
};

async function accountRequest(path: string, options: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(`/account-rosters${path}`), { ...options, credentials: "include" });
}

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) as { error?: string } | null;
  return body?.error || `Account saves are unavailable (${response.status}).`;
}

export async function fetchAccountRosters(): Promise<SavedRoster[]> {
  const response = await accountRequest("");
  if (response.status === 401) throw new Error("ACCOUNT_LOGIN_REQUIRED");
  if (!response.ok) throw new Error(await errorMessage(response));
  const body = await response.json() as { rosters?: SavedRoster[] };
  return Array.isArray(body.rosters) ? body.rosters : [];
}

export async function saveAccountRoster(roster: SavedRoster): Promise<SavedRoster> {
  const response = await accountRequest("", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(roster),
  });
  if (response.status === 401) throw new Error("ACCOUNT_LOGIN_REQUIRED");
  if (!response.ok) throw new Error(await errorMessage(response));
  const body = await response.json() as { roster: SavedRoster };
  return body.roster;
}

export async function deleteAccountRoster(id: string): Promise<void> {
  const response = await accountRequest(`/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (response.status === 401) throw new Error("ACCOUNT_LOGIN_REQUIRED");
  if (!response.ok) throw new Error(await errorMessage(response));
}
