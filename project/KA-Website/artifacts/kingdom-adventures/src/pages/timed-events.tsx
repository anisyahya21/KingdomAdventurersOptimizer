import { useCallback, useEffect, useState } from "react";
import { Link } from "wouter";
import QRCode from "qrcode";
import { Bell, BriefcaseBusiness, CalendarDays, ClipboardList, Clock3, Trophy, Wand2, Award, AlertTriangle, Plus, Minus, ExternalLink, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useEventRefresh } from "@/lib/event-refresh";
import { useEventHourOffset } from "@/lib/event-time";
import { KAIRO_ROOM_DRAFTS } from "@/lib/en-event-drafts";
import { eventStatusCardClass, eventStatusClass, eventStatusLabel, type EventStatus } from "@/lib/event-status";
import { isWarioDungeonLive } from "@/pages/wario-dungeon";
import { getFacilityIcon } from "@/lib/equipment-icons";

const GACHA_BUTTON_X4_ICON = "/website_icons/requested/gacha_button_x4.png";
const WAIRO_DUNGEON_ICON = "/website_icons/facilities_confirmed/mapchip_223_wairo_dungeon.png";

const EVENT_CARDS = [
  {
    href: "/briefing-room",
    title: "Briefing Room",
    description: "Today’s and tomorrow’s mission goals and rewards, with monster spawn details and this month’s target schedule.",
    icon: ClipboardList,
    facilityIconId: 167,
    status: "live" as EventStatus,
  },
  {
    href: "/gacha-events",
    title: "Gacha Events",
    description: "Featured S-rank jobs, S facilities, Kairo windows, and weapon banner timing.",
    customIcon: GACHA_BUTTON_X4_ICON,
    status: "live" as EventStatus,
  },
  {
    href: "/weekly-conquest",
    title: "Weekly Conquest",
    description: "Current conquest targets, rewards, and the locations you need to clear each week.",
    icon: Trophy,
    facilityIconId: 168,
    status: "live" as EventStatus,
  },
  {
    href: "/wario-dungeon",
    title: "Wairo Dungeon",
    description: "Dedicated event page for the monthly dungeon spawn windows.",
    customIcon: WAIRO_DUNGEON_ICON,
    icon: Clock3,
    facilityIconId: 223,
    status: "inactive" as EventStatus,
  },
  {
    href: "/daily-rank-rewards",
    title: "Daily Rank Rewards",
    description: "Daily ranking board reward tables for S and A rank, grouped by weekday.",
    icon: Award,
    facilityIconId: 172,
    status: "live" as EventStatus,
  },
  {
    href: "/kairo-room",
    title: "Kairo Room",
    description: "Active days, challenge names, and equipment box rewards from the EN sheet.",
    icon: Wand2,
    facilityIconId: 180,
    status: "inactive" as EventStatus,
  },
  {
    href: "/job-center",
    title: "Job Center",
    description: "Weekly profession rotation by day, using the EN sheet as a readable event schedule.",
    icon: BriefcaseBusiness,
    facilityIconId: 174,
    status: "live" as EventStatus,
  },
];

type EventCard = (typeof EVENT_CARDS)[number] & { disabled?: boolean; facilityIconId?: number };

function EventReminderInstallPanel() {
  const [qrCodeUrl, setQrCodeUrl] = useState("");
  const [installUrl, setInstallUrl] = useState("/event-reminders?install=1");

  useEffect(() => {
    const url = `${window.location.origin}/event-reminders?install=1`;
    setInstallUrl(url);
    QRCode.toDataURL(url, {
      width: 132,
      margin: 1,
      color: {
        dark: "#020617",
        light: "#f8fafc",
      },
    }).then(setQrCodeUrl).catch(() => setQrCodeUrl(""));
  }, []);

  return (
    <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-4 py-4">
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <div className="rounded-md bg-sky-500/15 p-2">
              <Bell className="h-5 w-5 text-sky-300" />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-sky-100">Event Reminder App</div>
              <div className="mt-1 text-xs leading-relaxed text-sky-100/75">
                One-page mobile PWA for Wairo, Weekly Conquest reset, S Rank gacha, and S Rank facility notifications.
              </div>
            </div>
          </div>

          <div className="grid gap-2 text-xs text-sky-100/75 sm:grid-cols-3">
            <div className="rounded-md bg-black/15 px-3 py-2">1. Scan the QR code with your phone.</div>
            <div className="rounded-md bg-black/15 px-3 py-2">2. Follow the big install prompt.</div>
            <div className="rounded-md bg-black/15 px-3 py-2">3. iPhone still needs Share, then Add to Home Screen.</div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button asChild className="bg-sky-500 text-white hover:bg-sky-500">
              <Link href="/event-reminders?install=1">
                <Smartphone className="h-4 w-4" />
                Open install screen
              </Link>
            </Button>
            <Button asChild variant="outline" className="border-sky-400/40 bg-transparent text-sky-100 hover:bg-sky-500/10">
              <a href={installUrl} target="_blank" rel="noreferrer">
                <ExternalLink className="h-4 w-4" />
                Open in new tab
              </a>
            </Button>
          </div>
        </div>

        <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/5 p-3">
          {qrCodeUrl ? (
            <img src={qrCodeUrl} alt="QR code for the Event Reminder App" className="h-32 w-32 rounded-md bg-white p-1" />
          ) : (
            <div className="grid h-32 w-32 place-items-center rounded-md bg-white/10 text-xs text-sky-100/60">QR</div>
          )}
          <div className="max-w-40 text-xs leading-relaxed text-sky-100/70">
            Generated on this page from this website. Scan it to open the install help screen for KA Events.
          </div>
        </div>
      </div>
    </div>
  );
}

export default function TimedEventsPage() {
  const [offset, setOffset] = useEventHourOffset();
  const [now, setNow] = useState(() => new Date());
  const refreshNow = useCallback(() => setNow(new Date()), []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  useEventRefresh(refreshNow);

  const localDay = new Date(now.getTime() + offset * 60 * 60 * 1000).toLocaleDateString("en-US", { weekday: "long" });
  const kairoRoomLive = Boolean(KAIRO_ROOM_DRAFTS.find((entry) => entry.day === localDay)?.active);
  const warioDungeonLive = isWarioDungeonLive(now, offset);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <CalendarDays className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Events</h1>
        </div>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Kingdom Adventurers event hub for briefing room missions, weekly conquest, gacha windows, Wairo Dungeon,
          daily rank rewards, Kairo Room, and Job Center schedules.
        </p>
      </div>

      {/* DST Warning Box - styled to match site theme, only icon is red */}
      <div className="border-2 border-border bg-muted text-foreground rounded-lg px-4 py-3 flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 mt-0.5 text-red-500 shrink-0" />
        <div>
          <div className="font-semibold">Warning: Daylight Saving Time (DST) Offset</div>
          <div className="text-sm mt-1">
            Some event times may be affected by DST or timezone differences. If event times look off, use the offset control below to adjust.<br />
            <span className="font-medium">Kairo Room, Job Center, Wairo Dungeon, and Gacha Events</span> follow your local time <span className="font-medium">plus any offset you set here</span>.<br />
            <span className="font-medium">Daily Rank Rewards & Weekly Conquest</span> follow Japan time. Briefing Room uses the Japan calendar with the Events offset applied.
          </div>
        </div>
      </div>

      {/* Offset Control - dark theme */}
      <div className="flex items-center gap-3 border border-border bg-muted rounded-lg px-4 py-2 w-fit my-2">
        <span className="font-medium text-primary">Event Time Offset:</span>
        <button
          className="p-1 rounded border border-border bg-background text-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
          onClick={() => setOffset((v) => Math.max(-23, v - 1))}
          disabled={offset <= -23}
          aria-label="Decrease offset"
        >
          <Minus className="w-4 h-4" />
        </button>
        <span className="font-mono text-base w-10 text-center">{offset >= 0 ? `+${offset}` : offset}h</span>
        <button
          className="p-1 rounded border border-border bg-background text-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
          onClick={() => setOffset((v) => Math.min(23, v + 1))}
          disabled={offset >= 23}
          aria-label="Increase offset"
        >
          <Plus className="w-4 h-4" />
        </button>
        <span className="text-xs text-muted-foreground">(applies to Briefing Room’s calendar and home timer, Kairo Room, Job Center, Wairo Dungeon, and Gacha Events)</span>
      </div>

      <EventReminderInstallPanel />

      <div className="grid gap-4 md:grid-cols-2">
        {(EVENT_CARDS as EventCard[]).map((card) => {
          const status =
            card.href === "/kairo-room" ? (kairoRoomLive ? "live" : "inactive")
            : card.href === "/wario-dungeon" ? (warioDungeonLive ? "live" : "inactive")
            : card.href === "/daily-rank-rewards" ? "live"
            : card.status;
          const facIcon = card.facilityIconId ? getFacilityIcon(card.facilityIconId) : undefined;
          const content = (
            <Card className={`h-full shadow-sm transition-all overflow-hidden ${eventStatusCardClass(status)} ${card.disabled ? "opacity-75" : "hover:shadow-md hover:border-primary/30 cursor-pointer"}`}>
              <div className="flex h-full">
                <div className="flex-none w-20 self-stretch flex items-center justify-center bg-muted/30 border-r border-border p-2">
                  {card.customIcon
                    ? <img src={card.customIcon} alt="" className="w-full h-full object-contain" style={{ imageRendering: "pixelated" }} />
                    : facIcon
                    ? <img src={facIcon} alt="" className="w-full h-full object-contain" style={{ imageRendering: "pixelated" }} />
                    : card.icon ? <card.icon className="w-8 h-8 text-primary" /> : null}
                </div>
                <div className="flex flex-col flex-1 min-w-0">
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-end">
                      <Badge variant="outline" className={eventStatusClass(status)}>{eventStatusLabel(status)}</Badge>
                    </div>
                    <CardTitle className="text-base">{card.title}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <CardDescription className="text-xs leading-relaxed">{card.description}</CardDescription>
                  </CardContent>
                </div>
              </div>
            </Card>
          );

          return card.disabled ? <div key={card.title}>{content}</div> : <Link key={card.href} href={card.href}>{content}</Link>;
        })}
      </div>
    </div>
  );
}

