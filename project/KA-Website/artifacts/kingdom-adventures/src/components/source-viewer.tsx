import { useState } from "react";
import { Code, Copy, Check } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface SourceViewerButtonProps {
  source: string;
  title?: string;
}

export function SourceViewerButton({ source, title = "Source Code" }: SourceViewerButtonProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(source);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2 h-8">
          <Code className="w-4 h-4" />
          View Source
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl h-[80vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-6 py-4 border-b border-border shrink-0">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-base">{title}</DialogTitle>
            <Button variant="outline" size="sm" onClick={copy} className="gap-2 h-7">
              {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? "Copied!" : "Copy all"}
            </Button>
          </div>
        </DialogHeader>
        <div className="flex-1 overflow-auto">
          <pre className="p-6 text-xs font-mono leading-relaxed whitespace-pre text-foreground bg-muted/40 min-h-full">
            {source}
          </pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}
