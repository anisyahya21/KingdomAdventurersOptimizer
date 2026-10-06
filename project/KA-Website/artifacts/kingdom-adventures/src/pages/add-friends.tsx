import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiUrl } from "@/lib/api";
import { fetchAuthSession, startTelegramAuth, type AuthSessionResponse } from "@/lib/auth-session";

type FriendEntry = {
  id: string;
  userId: string;
  displayName: string;
  gameId: string;
  createdAt: number;
  updatedAt: number;
  expiresAt: number;
  remainingMs: number;
};

type FriendsPoolResponse = {
  now: number;
  ttlMs: number;
  authenticated?: boolean;
  me: FriendEntry | null;
  entries: FriendEntry[];
};

function formatRemaining(ms: number) {
  if (ms <= 0) return "expired";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatTimeAgo(timestampMs: number, nowMs: number) {
  const diffMs = Math.max(0, nowMs - timestampMs);
  const totalSeconds = Math.floor(diffMs / 1000);
  if (totalSeconds < 60) return "just now";

  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m ago`;

  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 24) return `${totalHours}h ago`;

  const totalDays = Math.floor(totalHours / 24);
  if (totalDays < 30) return `${totalDays}d ago`;

  const totalMonths = Math.floor(totalDays / 30);
  if (totalMonths < 12) return `${totalMonths}mo ago`;

  const totalYears = Math.floor(totalDays / 365);
  return `${totalYears}y ago`;
}

export default function AddFriendsPage() {
  const [authSession, setAuthSession] = useState<AuthSessionResponse>({ authenticated: false, guest: true });
  const [authLoading, setAuthLoading] = useState(true);
  const [poolLoading, setPoolLoading] = useState(false);
  const [joining, setJoining] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [pool, setPool] = useState<FriendsPoolResponse | null>(null);
  const [serverNowMs, setServerNowMs] = useState(0);
  const [localAnchorMs, setLocalAnchorMs] = useState(0);
  const [tick, setTick] = useState(0);
  const authPollRef = useRef<number | null>(null);
  const authTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setTick((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    return () => {
      if (authPollRef.current !== null) {
        window.clearInterval(authPollRef.current);
        authPollRef.current = null;
      }
      if (authTimeoutRef.current !== null) {
        window.clearTimeout(authTimeoutRef.current);
        authTimeoutRef.current = null;
      }
    };
  }, []);

  const clearAuthPolling = () => {
    if (authPollRef.current !== null) {
      window.clearInterval(authPollRef.current);
      authPollRef.current = null;
    }
    if (authTimeoutRef.current !== null) {
      window.clearTimeout(authTimeoutRef.current);
      authTimeoutRef.current = null;
    }
  };

  const loadPool = async () => {
    setPoolLoading(true);
    try {
      const response = await fetch(apiUrl("/friends"), {
        credentials: "include",
      });
      const payload = await response.json().catch(() => null) as { error?: string } & Partial<FriendsPoolResponse> | null;
      if (!response.ok) {
        if (response.status === 401) {
          setError(null);
          setPool(null);
          return;
        }
        throw new Error(payload?.error || "Could not load the friends pool.");
      }
      const nextPool = payload as FriendsPoolResponse;
      setPool(nextPool);
      setServerNowMs(nextPool.now);
      setLocalAnchorMs(Date.now());
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Could not load the friends pool.";
      if (/log in required/i.test(message)) {
        setError(null);
        return;
      }
      setError(message);
    } finally {
      setPoolLoading(false);
    }
  };

  const refreshAuthSession = async () => {
    try {
      const session = await fetchAuthSession();
      setAuthSession(session);
      return session;
    } catch {
      const fallback = { authenticated: false, guest: true } satisfies AuthSessionResponse;
      setAuthSession(fallback);
      return fallback;
    }
  };

  useEffect(() => {
    let mounted = true;
    setAuthLoading(true);
    fetchAuthSession()
      .then((session) => {
        if (!mounted) return;
        setAuthSession(session);
      })
      .catch(() => {
        if (!mounted) return;
        setAuthSession({ authenticated: false, guest: true });
      })
      .finally(() => {
        if (mounted) setAuthLoading(false);
      });

    void loadPool();

    return () => {
      mounted = false;
    };
  }, []);

  const startTelegramLoginFlow = async () => {
    if (!window.confirm("Log in with Telegram to add yourself to the friends list?")) {
      return;
    }

    setAuthBusy(true);
    setError(null);
    try {
      const started = await startTelegramAuth();
      const popup = window.open(
        started.widgetUrl,
        "ka-telegram-login",
        "popup=yes,width=540,height=720,resizable=yes,scrollbars=yes",
      );

      if (!popup) {
        window.location.href = started.widgetUrl;
        return;
      }

      clearAuthPolling();
      authPollRef.current = window.setInterval(() => {
        void (async () => {
          if (popup.closed) {
            clearAuthPolling();
            setAuthBusy(false);
            const session = await refreshAuthSession();
            if (session.authenticated) {
              await loadPool();
            }
            return;
          }

          const session = await fetchAuthSession().catch(() => null);
          if (!session?.authenticated) return;

          setAuthSession(session);
          await loadPool();
          popup.close();
          clearAuthPolling();
          setAuthBusy(false);
        })();
      }, 1000);

      authTimeoutRef.current = window.setTimeout(() => {
        clearAuthPolling();
        if (!popup.closed) popup.close();
        setAuthBusy(false);
      }, 120000);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "Telegram login is unavailable.");
      setAuthBusy(false);
    }
  };

  const joinPool = async () => {
    if (!authSession.authenticated) {
      await startTelegramLoginFlow();
      return;
    }

    if (profileMissing) {
      setError("Please set display name and game ID in your profile first.");
      return;
    }

    setJoining(true);
    setError(null);
    try {
      const response = await fetch(apiUrl("/friends/join"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: "{}",
      });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) {
        if (response.status === 401) {
          const session = await refreshAuthSession();
          if (!session.authenticated) {
            await startTelegramLoginFlow();
            return;
          }
        }
        throw new Error(payload?.error || "Could not add you to the friends pool.");
      }
      await loadPool();
    } catch (joinError) {
      const message = joinError instanceof Error ? joinError.message : "Could not add you to the friends pool.";
      if (/log in required/i.test(message)) {
        await startTelegramLoginFlow();
        return;
      }
      setError(message);
    } finally {
      setJoining(false);
    }
  };

  const leavePool = async () => {
    if (!authSession.authenticated) {
      await startTelegramLoginFlow();
      return;
    }

    if (!window.confirm("Remove your entry from the player list?")) {
      return;
    }

    setLeaving(true);
    setError(null);
    try {
      const response = await fetch(apiUrl("/friends/leave"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: "{}",
      });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) {
        if (response.status === 401) {
          const session = await refreshAuthSession();
          if (!session.authenticated) {
            await startTelegramLoginFlow();
            return;
          }
        }
        throw new Error(payload?.error || "Could not remove you from the friends pool.");
      }
      await loadPool();
    } catch (leaveError) {
      const message = leaveError instanceof Error ? leaveError.message : "Could not remove you from the friends pool.";
      if (/log in required/i.test(message)) {
        await startTelegramLoginFlow();
        return;
      }
      setError(message);
    } finally {
      setLeaving(false);
    }
  };

  const profileMissing = useMemo(() => {
    if (!authSession.authenticated) return false;
    const displayName = String(authSession.user?.displayName || "").trim();
    const gameId = String(authSession.user?.gameId || "").trim();
    return !displayName || !gameId;
  }, [authSession]);

  const nowMs = useMemo(
    () => (serverNowMs > 0 ? serverNowMs + Math.max(0, Date.now() - localAnchorMs) : Date.now()),
    [serverNowMs, localAnchorMs, tick],
  );

  const entries = useMemo(
    () => [...(pool?.entries ?? [])].sort((a, b) => b.updatedAt - a.updatedAt),
    [pool?.entries],
  );

  const me = pool?.me ?? null;

  const copyGameId = async (entry: FriendEntry) => {
    try {
      await navigator.clipboard.writeText(entry.gameId);
      setCopiedId(entry.id);
      window.setTimeout(() => setCopiedId(null), 1200);
    } catch {
      setCopiedId(null);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Add Friends</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-muted-foreground">
              {me
                ? (
                  <>
                    {`Your timer: ${formatRemaining(me.expiresAt - nowMs)} until your entry is removed from the list. You can `}
                    <button
                      type="button"
                      onClick={() => void joinPool()}
                      disabled={joining || leaving || authBusy}
                      className="font-medium underline underline-offset-2 text-foreground disabled:opacity-60"
                    >
                      reset
                    </button>
                    {" your timer or "}
                    <button
                      type="button"
                      onClick={() => void leavePool()}
                      disabled={joining || leaving || authBusy}
                      className="font-medium underline underline-offset-2 text-foreground disabled:opacity-60"
                    >
                      remove yourself
                    </button>
                    {" from the list."}
                  </>
                )
                : "Click + to add yourself to the list."}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="icon"
                onClick={() => void joinPool()}
                disabled={joining || leaving || authBusy || authLoading}
                title="Add yourself"
              >
                {joining || leaving || authBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-5 w-5" />}
              </Button>
            </div>
          </div>
          {authSession.authenticated && profileMissing ? (
            <div className="text-xs text-muted-foreground">
              Missing profile info. Set display name and game ID in account profile first.
            </div>
          ) : null}
        </CardContent>
      </Card>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Player List</CardTitle>
          <CardDescription>Most recent players are on top.</CardDescription>
        </CardHeader>
        <CardContent>
          {poolLoading && entries.length === 0 ? (
            <div className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading list...
            </div>
          ) : entries.length === 0 ? (
            <div className="text-sm text-muted-foreground">No players in the list yet.</div>
          ) : (
            <div className="space-y-2">
              {entries.map((entry) => (
                <div key={entry.id} className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 gap-3">
                  <div className="min-w-0">
                    <div className="font-medium truncate">{entry.displayName}</div>
                    <div className="text-xs text-muted-foreground flex items-center gap-2">
                      <span>{entry.gameId}</span>
                      <button
                        type="button"
                        onClick={() => void copyGameId(entry)}
                        className="inline-flex items-center text-muted-foreground hover:text-foreground"
                        title="Copy game ID"
                      >
                        {copiedId === entry.id ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground shrink-0 text-right leading-5">
                    <div>Added {formatTimeAgo(entry.createdAt || entry.updatedAt, nowMs)}</div>
                    <div>Last refreshed {formatTimeAgo(entry.updatedAt || entry.createdAt, nowMs)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
