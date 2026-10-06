import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Link } from "wouter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { localSharedData } from "@/lib/local-shared-data";
import { getJobProfile, type JobProfile, type SharedJobProfileData } from "@/game-data/job-profile";

type StatEntry = {
  base: number;
  inc: number;
  levels?: Record<string, number>;
};

const PREVIEW_DIALOG_CLASS = "top-[max(0.5rem,env(safe-area-inset-top))] translate-y-0 w-[calc(100vw-1rem)] max-w-[1150px] max-h-[calc(100dvh-1rem-env(safe-area-inset-top))] rounded-xl border border-border bg-card p-0 text-card-foreground sm:top-[50%] sm:translate-y-[-50%] sm:w-[calc(100vw-2rem)]";
const STAT_ORDER = ["HP", "MP", "Vigor", "Attack", "Defence", "Speed", "Luck", "Intelligence", "Dexterity", "Gather", "Move", "Heart"] as const;
const SKILL_ACCESS_LABELS: Array<{ key: "attack" | "attackMagic" | "recovery"; label: string }> = [
  { key: "attack", label: "Attack" },
  { key: "attackMagic", label: "Attack magic" },
  { key: "recovery", label: "Recovery magic" },
];

function jobStatAtLevel(entry: StatEntry | undefined, level: number): number | null {
  if (!entry) return null;
  const override = entry.levels?.[String(level)];
  if (typeof override === "number") return override;
  return entry.base + (level - 1) * entry.inc;
}

function decodeJobNameFromAnchor(anchor: HTMLAnchorElement): string | null {
  const href = anchor.getAttribute("href");
  if (!href) return null;
  const url = new URL(anchor.href, window.location.origin);
  if (url.origin !== window.location.origin) return null;

  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  const prefix = `${base}/jobs/`;
  if (!url.pathname.startsWith(prefix)) return null;

  const encodedName = url.pathname.slice(prefix.length);
  if (!encodedName) return null;

  try {
    return decodeURIComponent(encodedName).trim();
  } catch {
    return encodedName.trim();
  }
}

function JobQuickPreviewDialog({
  jobName,
  open,
  onOpenChange,
}: {
  jobName: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [rank, setRank] = useState("");
  const [levelInput, setLevelInput] = useState("1");
  const titleRef = useRef<HTMLHeadingElement | null>(null);

  const shared = localSharedData as SharedJobProfileData & { statIcons?: Record<string, string> };
  const profile = useMemo<JobProfile | null>(() => {
    if (!jobName) return null;
    return getJobProfile(shared, jobName, { selectedRank: rank || undefined });
  }, [jobName, rank, shared]);

  useEffect(() => {
    if (!open || !profile) return;
    const ranks = profile.rankNames;
    setRank(ranks.includes("S") ? "S" : ranks[0] ?? "");
    setLevelInput("1");
  }, [open, profile?.name]);

  const parsedLevel = Number.parseInt(levelInput, 10);
  const level = Number.isFinite(parsedLevel) ? Math.min(999, Math.max(1, parsedLevel)) : 1;
  const rankData = profile?.job.ranks[rank] ?? (profile ? profile.job.ranks[profile.rankNames[0] ?? ""] : undefined);
  const skillAccess = profile?.skillAccess ?? {};

  if (!profile) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={PREVIEW_DIALOG_CLASS}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          titleRef.current?.focus({ preventScroll: true });
        }}
      >
        <div className="max-h-[calc(100dvh-1rem)] overflow-y-auto p-4 pr-14 pt-[max(1rem,env(safe-area-inset-top))] sm:max-h-[calc(100dvh-2rem)] sm:p-6 sm:pr-16">
          <Button variant="ghost" size="sm" className="mb-3 -ml-2 min-h-10 sm:hidden" onClick={() => onOpenChange(false)}>
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
          <DialogHeader>
            <DialogTitle ref={titleRef} tabIndex={-1} className="flex items-center gap-2 text-xl">
              {profile.job.icon ? <img src={profile.job.icon} alt={profile.name} className="h-7 w-7 rounded object-contain" /> : null}
              {profile.name}
            </DialogTitle>
            <DialogDescription>Job reference from the database.</DialogDescription>
          </DialogHeader>

          <div className="mt-3 flex flex-wrap gap-2">
            <Badge variant="outline">{profile.generation === 1 ? "Non-Marriage" : "Marriage Exclusive"}</Badge>
            {profile.battleType ? <Badge variant="outline">{profile.battleType === "combat" ? "Battle-Type" : "Non Battle-Type"}</Badge> : null}
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-sm font-semibold">Equipment & Skills</div>
              <div className="space-y-3">
                <div>
                  <div className="mb-1 text-xs text-muted-foreground">Equipment</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(profile.equipmentAccess.weapons).map(([weapon, access]) => (
                      <Badge key={weapon} variant="outline" className={access === "can" ? "text-green-500" : access === "weak" ? "text-amber-500" : "text-muted-foreground"}>
                        {weapon}: {access === "can" ? "Can" : access === "weak" ? "Weak" : "Can't"}
                      </Badge>
                    ))}
                    <Badge variant="outline" className={profile.equipmentAccess.shield === "can" ? "text-green-500" : "text-muted-foreground"}>
                      Shield: {profile.equipmentAccess.shield === "can" ? "Can" : "Can't"}
                    </Badge>
                  </div>
                </div>
                <div>
                  <div className="mb-1 text-xs text-muted-foreground">Skill Access</div>
                  <div className="flex flex-wrap gap-2">
                    {SKILL_ACCESS_LABELS.map((skill) => {
                      const value = skill.key === "attackMagic" ? (skillAccess.attackMagic ?? skillAccess.casting) : skillAccess[skill.key];
                      return (
                        <Badge key={skill.key} variant="outline" className={value === "can" ? "text-green-500" : "text-muted-foreground"}>
                          {skill.label}: {value === "can" ? "Can" : "Can't"}
                        </Badge>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-4">
              <div className="rounded-lg border border-border p-3">
                <div className="mb-2 text-sm font-semibold">Lands & Shops</div>
                {profile.shops.length ? (
                  <div className="flex flex-wrap gap-2">
                    {profile.shops.map((shop) => <Badge key={shop} variant="outline">{shop}</Badge>)}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">No lands or shops listed.</div>
                )}
              </div>

              <div className="rounded-lg border border-border p-3">
                <div className="mb-2 text-sm font-semibold">Job Ranges</div>
                {profile.rangeGroups.length ? (
                  <div className="space-y-2">
                    {profile.rangeGroups.map((range) => (
                      <div key={range.label} className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-medium text-muted-foreground">{range.label}</span>
                        {range.groups.length === 1 ? (
                          <Badge variant="outline">{range.groups[0].value}</Badge>
                        ) : (
                          range.groups.map((group) => (
                            <Badge key={`${range.label}-${group.label}`} variant="outline">{group.label}: {group.value}</Badge>
                          ))
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">No job range data.</div>
                )}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-border p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm font-semibold">Stats</div>
              <div className="flex items-center gap-2">
                <select value={rank} onChange={(event) => setRank(event.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-sm">
                  {profile.rankNames.map((rankName) => <option key={rankName} value={rankName}>{rankName} rank</option>)}
                </select>
                <Input
                  value={levelInput}
                  onChange={(event) => setLevelInput(event.target.value)}
                  type="number"
                  min={1}
                  max={999}
                  className="h-8 w-20 px-2"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              {STAT_ORDER.map((stat) => {
                const entry = rankData?.stats[stat] as StatEntry | undefined;
                const value = jobStatAtLevel(entry, level);
                const icon = shared.statIcons?.[stat];
                return (
                  <div key={stat} className="rounded-md border border-border bg-background/70 px-2 py-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-muted-foreground">
                      {icon ? <img src={icon} alt={stat} className="h-3 w-3 object-contain" /> : null}
                      {stat}
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1 text-center">
                      <div><div className="text-[9px] text-muted-foreground/70">Base</div><div className="text-sm font-medium tabular-nums">{entry?.base ?? "-"}</div></div>
                      <div><div className="text-[9px] text-muted-foreground/70">+/Lv</div><div className="text-sm font-medium tabular-nums">{entry ? `+${entry.inc}` : "-"}</div></div>
                      <div><div className="text-[9px] text-muted-foreground/70">Lv {level}</div><div className="text-sm font-semibold text-primary tabular-nums">{value ?? "-"}</div></div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="mt-4">
            <Link href={`/jobs/${encodeURIComponent(profile.name)}`} data-job-preview-bypass="true" onClick={() => onOpenChange(false)}>
              <Button variant="outline" className="w-full">Open Full Job Page</Button>
            </Link>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function GlobalJobPreview() {
  const [open, setOpen] = useState(false);
  const [jobName, setJobName] = useState<string | null>(null);

  useEffect(() => {
    const onDocumentClick = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      if (!target) return;
      const anchor = target.closest("a") as HTMLAnchorElement | null;
      if (!anchor) return;
      if (anchor.dataset.jobPreviewBypass === "true") return;
      if (anchor.target === "_blank" || anchor.hasAttribute("download")) return;

      const decodedJobName = decodeJobNameFromAnchor(anchor);
      if (!decodedJobName) return;

      event.preventDefault();
      setJobName(decodedJobName);
      setOpen(true);
    };

    document.addEventListener("click", onDocumentClick, true);
    return () => document.removeEventListener("click", onDocumentClick, true);
  }, []);

  return <JobQuickPreviewDialog jobName={jobName} open={open} onOpenChange={setOpen} />;
}
