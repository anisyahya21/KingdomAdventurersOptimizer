import { useEffect, useState } from "react";
import { BriefcaseBusiness, CalendarDays } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { JOB_CENTER_DRAFTS } from "@/lib/en-event-drafts";
import { Link } from "wouter";
import { getOffsetAdjustedNow, useEventHourOffset } from "@/lib/event-time";
import { eventStatusCardClass, eventStatusClass, eventStatusLabel } from "@/lib/event-status";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] as const;

export default function JobCenterPage() {
  const [now, setNow] = useState(() => new Date());
  const [eventOffset] = useEventHourOffset();
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const currentEventDay = WEEKDAYS[getOffsetAdjustedNow(now, eventOffset).getDay()];

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <BriefcaseBusiness className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Job Center</h1>
        </div>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Kingdom Adventurers Job Center profession rotation by weekday, with the current event day highlighted
          for faster planning.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {JOB_CENTER_DRAFTS.map((entry) => {
          const isCurrentDay = entry.day === currentEventDay;
          return (
          <Card key={entry.day} className={`shadow-sm ${isCurrentDay ? eventStatusCardClass("live") : eventStatusCardClass("inactive")}`}>
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <CalendarDays className="w-4 h-4 text-primary" />
                  {entry.day}
                </CardTitle>
                <Badge variant="outline" className={eventStatusClass(isCurrentDay ? "live" : "inactive")}>
                  {eventStatusLabel(isCurrentDay ? "live" : "inactive")}
                </Badge>
              </div>
              <CardDescription>{entry.professions.length} professions listed for this rotation.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {entry.professions.map((profession) => (
                <Link
                  key={profession}
                  href={`/jobs/${encodeURIComponent(profession)}`}
                  className="inline-flex rounded-md border border-border px-2.5 py-0.5 text-xs font-semibold transition-colors hover:border-primary hover:bg-primary/10 focus:outline-none"
                >
                  {profession}
                </Link>
              ))}
            </CardContent>
          </Card>
          );
        })}
      </div>
    </div>
  );
}

