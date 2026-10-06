import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { fetchAuthSession } from "@/lib/auth-session";
import { configuredApiBase } from "@/lib/api";

type OverviewResponse = {
  users: number;
  activeSessions: number;
  reminderSubscriptions: number;
  guides: number;
  uniqueVisitors30d: number;
};

type TopRowsResponse = {
  days: number;
  rows: Array<{ route?: string; toolSlug?: string; views: number }>;
};

function adminUrl(path: string) {
  const base = configuredApiBase();
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${base}/ka-api/ka/admin${clean}`;
}

export default function AdminPage() {
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [topPages, setTopPages] = useState<Array<{ route: string; views: number }>>([]);
  const [topTools, setTopTools] = useState<Array<{ toolSlug: string; views: number }>>([]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await fetchAuthSession();
      const admin = Boolean(session.user?.isAdmin);
      setIsAdmin(admin);
      if (!admin) {
        setOverview(null);
        setTopPages([]);
        setTopTools([]);
        return;
      }

      const [overviewRes, topPagesRes, topToolsRes] = await Promise.all([
        fetch(adminUrl("/overview"), { credentials: "include" }),
        fetch(adminUrl("/top-pages?days=30"), { credentials: "include" }),
        fetch(adminUrl("/top-tools?days=30"), { credentials: "include" }),
      ]);

      const overviewBody = await overviewRes.json().catch(() => ({})) as Partial<OverviewResponse> & { error?: string };
      if (!overviewRes.ok) throw new Error(overviewBody.error || "Could not load overview.");
      setOverview({
        users: Number(overviewBody.users || 0),
        activeSessions: Number(overviewBody.activeSessions || 0),
        reminderSubscriptions: Number(overviewBody.reminderSubscriptions || 0),
        guides: Number(overviewBody.guides || 0),
        uniqueVisitors30d: Number(overviewBody.uniqueVisitors30d || 0),
      });

      const topPagesBody = await topPagesRes.json().catch(() => ({})) as TopRowsResponse & { error?: string };
      if (!topPagesRes.ok) throw new Error(topPagesBody.error || "Could not load top pages.");
      setTopPages((topPagesBody.rows || []).map((row) => ({ route: String(row.route || "/"), views: Number(row.views || 0) })));

      const topToolsBody = await topToolsRes.json().catch(() => ({})) as TopRowsResponse & { error?: string };
      if (!topToolsRes.ok) throw new Error(topToolsBody.error || "Could not load top tools.");
      setTopTools((topToolsBody.rows || []).map((row) => ({ toolSlug: String(row.toolSlug || "unknown"), views: Number(row.views || 0) })));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load admin dashboard.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">Loading admin dashboard...</CardContent>
        </Card>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <ShieldCheck className="h-5 w-5" />
              Admin Access Required
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>Your account is not marked as admin.</p>
            <p>Set TELEGRAM_ADMIN_USER_IDS on backend and log in again.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Admin Dashboard</h1>
        <Button variant="outline" onClick={() => void load()}>Refresh</Button>
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Card><CardContent className="py-5"><div className="text-xs text-muted-foreground">Users</div><div className="text-2xl font-semibold">{overview?.users ?? 0}</div></CardContent></Card>
        <Card><CardContent className="py-5"><div className="text-xs text-muted-foreground">Active Sessions</div><div className="text-2xl font-semibold">{overview?.activeSessions ?? 0}</div></CardContent></Card>
        <Card><CardContent className="py-5"><div className="text-xs text-muted-foreground">Reminder Subs</div><div className="text-2xl font-semibold">{overview?.reminderSubscriptions ?? 0}</div></CardContent></Card>
        <Card><CardContent className="py-5"><div className="text-xs text-muted-foreground">Guides</div><div className="text-2xl font-semibold">{overview?.guides ?? 0}</div></CardContent></Card>
        <Card><CardContent className="py-5"><div className="text-xs text-muted-foreground">Unique Visitors (30d)</div><div className="text-2xl font-semibold">{overview?.uniqueVisitors30d ?? 0}</div></CardContent></Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Top Pages (30d)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            {topPages.length === 0 ? <div className="text-muted-foreground">No data yet.</div> : topPages.map((row) => (
              <div key={row.route} className="flex items-center justify-between border-b border-border/50 pb-2 last:border-0">
                <span className="truncate pr-3">{row.route}</span>
                <span className="font-medium">{row.views}</span>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Top Tools (30d)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            {topTools.length === 0 ? <div className="text-muted-foreground">No data yet.</div> : topTools.map((row) => (
              <div key={row.toolSlug} className="flex items-center justify-between border-b border-border/50 pb-2 last:border-0">
                <span className="truncate pr-3">{row.toolSlug}</span>
                <span className="font-medium">{row.views}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
