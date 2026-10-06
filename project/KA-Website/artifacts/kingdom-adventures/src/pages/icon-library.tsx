import { ExternalLink, Image } from "lucide-react";
import { PageHeader } from "@/components/ka/page-header";
import { Button } from "@/components/ui/button";

function buildIconPreviewPath(): string {
  const base = import.meta.env.BASE_URL || "/";
  return `${base.replace(/\/?$/, "/")}website_icons/preview.html`;
}

export default function IconLibraryPage() {
  const previewPath = buildIconPreviewPath();

  return (
    <div className="mx-auto max-w-7xl space-y-4 px-4 py-6">
      <PageHeader icon={<Image className="h-5 w-5" />} title="Icon Name Library">
        <p>
          Browse all icon files and exact names used by the guide editor.
          Use this page to copy icon names and paths quickly.
        </p>
      </PageHeader>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/20 px-3 py-2">
        <span className="text-xs text-muted-foreground">Need full-page browsing or browser find?</span>
        <a href={previewPath} target="_blank" rel="noreferrer" className="inline-flex">
          <Button type="button" size="sm" variant="outline" className="gap-2">
            <ExternalLink className="h-4 w-4" />
            Open Raw HTML Preview
          </Button>
        </a>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-background shadow-sm">
        <iframe
          title="Icon name library"
          src={previewPath}
          className="h-[75vh] w-full"
        />
      </div>
    </div>
  );
}
