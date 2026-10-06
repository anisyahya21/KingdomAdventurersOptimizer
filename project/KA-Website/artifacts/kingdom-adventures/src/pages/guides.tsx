import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import { BookMarked, Check, Copy, ExternalLink, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ka/page-header";
import { apiUrl } from "@/lib/api";
import {
  type CommunityGuide,
  fetchCommunityGuides,
  getCachedCommunityGuides,
  getGuideOwnerTokens,
  removeGuideOwnerToken,
} from "@/lib/community-guides";

const BUILT_IN_GUIDE = {
  href: "/playthrough-guide",
  title: "Playthrough Guide by Jaza",
  description: "Website-styled version of the community progression guide, with site links layered on top.",
  badge: "Guide",
  note: "Community guide",
};

export default function GuidesPage() {
  const cachedGuides = getCachedCommunityGuides();
  const [guides, setGuides] = useState<CommunityGuide[]>(() => cachedGuides?.guides ?? []);
  const [loading, setLoading] = useState(() => !cachedGuides);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [copiedGuideId, setCopiedGuideId] = useState<string | null>(null);
  const [ownerTokens, setOwnerTokens] = useState<Record<string, string>>(() => getGuideOwnerTokens());

  const ownedGuideIds = useMemo(() => new Set(Object.keys(ownerTokens)), [ownerTokens]);

  const loadGuides = async () => {
    try {
      setLoading(guides.length === 0);
      setError(null);
      const payload = await fetchCommunityGuides();
      setGuides(payload.guides);
      setOwnerTokens(getGuideOwnerTokens());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load guides.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadGuides();
  }, []);

  const startEditing = (guide: CommunityGuide) => {
    setEditingId(guide.id);
    setEditingTitle(guide.title);
  };

  const saveTitle = async (guide: CommunityGuide) => {
    const ownerToken = ownerTokens[guide.id];
    const title = editingTitle.trim();
    if (!title || (!ownerToken && !guide.editable)) return;

    setSavingId(guide.id);
    try {
      const response = await fetch(apiUrl(`/guides/${guide.id}`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(ownerToken ? { ownerToken, title } : { title }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Could not update guide.");
      setGuides((current) => current.map((item) => (item.id === guide.id ? payload.guide : item)));
      setEditingId(null);
      setEditingTitle("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update guide.");
    } finally {
      setSavingId(null);
    }
  };

  const removeGuide = async (guide: CommunityGuide) => {
    const ownerToken = ownerTokens[guide.id];
    if (!ownerToken && !guide.editable) return;
    if (!window.confirm(`Remove "${guide.title}" from the guide list?`)) return;

    setSavingId(guide.id);
    try {
      const response = await fetch(apiUrl(`/guides/${guide.id}`), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(ownerToken ? { ownerToken } : {}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error ?? "Could not remove guide.");
      removeGuideOwnerToken(guide.id);
      setOwnerTokens(getGuideOwnerTokens());
      setGuides((current) => current.filter((item) => item.id !== guide.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove guide.");
    } finally {
      setSavingId(null);
    }
  };

  const copyEditLink = async (guide: CommunityGuide) => {
    const ownerToken = ownerTokens[guide.id];
    if (!ownerToken) return;
    const editUrl = `${window.location.origin}/guides/${guide.slug}?edit=${encodeURIComponent(ownerToken)}`;
    try {
      await navigator.clipboard.writeText(editUrl);
      setCopiedGuideId(guide.id);
      window.setTimeout(() => setCopiedGuideId(null), 1600);
    } catch {
      setCopiedGuideId(null);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <PageHeader
        icon={<BookMarked className="w-5 h-5" />}
        title="Guides"
        actions={(
          <Link href="/add-guide">
            <Button className="gap-2 self-start">
              <Plus className="w-4 h-4" />
              Add Guide
            </Button>
          </Link>
        )}
      >
        <p>
          Community-written Kingdom Adventurers guides collected in one place, including progression advice,
          playthrough notes, event planning, town development, jobs, equipment, pets, monsters, and strategy help.
        </p>
      </PageHeader>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Link href={BUILT_IN_GUIDE.href}>
          <Card className="h-full cursor-pointer shadow-sm transition-all hover:shadow-md hover:border-primary/30">
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <div className="p-2 rounded-lg bg-muted">
                  <BookMarked className="w-5 h-5 text-primary" />
                </div>
                <Badge variant="outline">{BUILT_IN_GUIDE.badge}</Badge>
              </div>
              <CardTitle className="text-base mt-2">{BUILT_IN_GUIDE.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <CardDescription className="text-xs leading-relaxed">{BUILT_IN_GUIDE.description}</CardDescription>
              <div className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <ExternalLink className="w-3 h-3" />
                {BUILT_IN_GUIDE.note}
              </div>
            </CardContent>
          </Card>
        </Link>

        {loading ? (
          <Card>
            <CardContent className="flex items-center gap-3 py-10 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading community guides...
            </CardContent>
          </Card>
        ) : null}

        {guides.map((guide) => {
          const isTokenOwner = ownedGuideIds.has(guide.id);
          const isOwner = isTokenOwner || Boolean(guide.editable);
          const ownerBadge = isTokenOwner ? "Your Guide" : (guide.editable ? "Admin" : "Google Doc");
          const isEditing = editingId === guide.id;
          const saving = savingId === guide.id;
          return (
            <Card key={guide.id} className="h-full shadow-sm transition-all hover:shadow-md hover:border-primary/30">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="p-2 rounded-lg bg-muted">
                    <BookMarked className="w-5 h-5 text-primary" />
                  </div>
                  <Badge variant="outline">{ownerBadge}</Badge>
                </div>
                {isEditing ? (
                  <div className="mt-2 space-y-2">
                    <Input value={editingTitle} onChange={(event) => setEditingTitle(event.target.value)} />
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" onClick={() => saveTitle(guide)} disabled={saving || !editingTitle.trim()}>
                        {saving ? "Saving..." : "Save Title"}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditingId(null)} disabled={saving}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Link href={`/guides/${guide.slug}`} className="block">
                    <CardTitle className="text-base mt-2 hover:text-primary">{guide.title}</CardTitle>
                  </Link>
                )}
              </CardHeader>
              <CardContent className="space-y-3">
                <CardDescription className="text-xs leading-relaxed">
                  {guide.author ? `Added by ${guide.author}. ` : "Community guide. "}
                  Rendered live from Google Docs.
                </CardDescription>
                <div className="flex flex-wrap gap-2">
                  <Link href={`/guides/${guide.slug}`}>
                    <Button size="sm" variant="outline">Open Guide</Button>
                  </Link>
                  <a href={guide.docUrl} target="_blank" rel="noopener noreferrer">
                    <Button size="sm" variant="outline" className="gap-2">
                      <ExternalLink className="w-3.5 h-3.5" />
                      Google Doc
                    </Button>
                  </a>
                  {isOwner ? (
                    <>
                      <Button size="sm" variant="outline" className="gap-2" onClick={() => startEditing(guide)} disabled={saving}>
                        <Pencil className="w-3.5 h-3.5" />
                        Edit Title
                      </Button>
                      <Button size="sm" variant="outline" className="gap-2" onClick={() => copyEditLink(guide)} disabled={saving}>
                        {copiedGuideId === guide.id ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                        {copiedGuideId === guide.id ? "Copied" : "Edit Link"}
                      </Button>
                      <Button size="sm" variant="outline" className="gap-2 text-destructive hover:text-destructive" onClick={() => removeGuide(guide)} disabled={saving}>
                        <Trash2 className="w-3.5 h-3.5" />
                        Remove
                      </Button>
                    </>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

