import { useEffect, useState } from "react";
import { Link, useRoute } from "wouter";
import { BookOpenText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { GuideDocumentPage } from "@/components/guides/guide-document-page";
import { type CommunityGuide, fetchCommunityGuides, getCachedCommunityGuides, getGuideOwnerTokens, setGuideOwnerToken } from "@/lib/community-guides";

export default function CommunityGuidePage() {
  const [, params] = useRoute<{ slug: string }>("/guides/:slug");
  const slug = params?.slug ?? "";
  const cachedGuide = getCachedCommunityGuides()?.guides.find((item) => item.slug === slug) ?? null;
  const [guide, setGuide] = useState<CommunityGuide | null>(cachedGuide);
  const [loading, setLoading] = useState(() => !cachedGuide);
  const [error, setError] = useState<string | null>(null);
  const [ownerTokens, setOwnerTokens] = useState<Record<string, string>>(() => getGuideOwnerTokens());

  useEffect(() => {
    const url = new URL(window.location.href);
    const editToken = url.searchParams.get("edit") ?? url.searchParams.get("ownerToken") ?? "";
    if (!cachedGuide || !editToken) return;
    setGuideOwnerToken(cachedGuide.id, editToken);
    setOwnerTokens(getGuideOwnerTokens());
    url.searchParams.delete("edit");
    url.searchParams.delete("ownerToken");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [cachedGuide?.id]);

  useEffect(() => {
    let cancelled = false;

    async function loadGuide() {
      try {
        setLoading(!guide);
        setError(null);
        const payload = await fetchCommunityGuides();
        const found = payload.guides.find((item) => item.slug === slug) ?? null;
        if (!cancelled) {
          const url = new URL(window.location.href);
          const editToken = url.searchParams.get("edit") ?? url.searchParams.get("ownerToken") ?? "";
          if (found && editToken) {
            setGuideOwnerToken(found.id, editToken);
            url.searchParams.delete("edit");
            url.searchParams.delete("ownerToken");
            window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
          }
          setGuide(found);
          setOwnerTokens(getGuideOwnerTokens());
          if (!found) setError("Guide not found.");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load guide.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadGuide();

    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-6">
        <Card>
          <CardContent className="flex items-center gap-3 py-10 text-sm text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" />
            Loading guide...
          </CardContent>
        </Card>
      </div>
    );
  }

  if (error || !guide) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-6">
        <Card>
          <CardContent className="py-10 space-y-4">
            <div className="flex items-center gap-2 font-medium">
              <BookOpenText className="w-5 h-5 text-muted-foreground" />
              {error ?? "Guide not found."}
            </div>
            <Link href="/guides">
              <Button variant="outline">Back to Guides</Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <GuideDocumentPage
      title={guide.title}
      description={`${guide.author ? `Added by ${guide.author}. ` : ""}Rendered live from the linked Google Doc.`}
      docId={guide.docId}
      docUrl={guide.docUrl}
      ownerGuideId={ownerTokens[guide.id] || guide.editable ? guide.id : undefined}
      ownerToken={ownerTokens[guide.id]}
      serverLinkOverrides={guide.linkOverrides}
    />
  );
}
