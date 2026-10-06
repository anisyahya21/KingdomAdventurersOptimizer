import { useCallback, useEffect, useMemo, useState } from "react";
import { Bell, BellOff, Check, ChevronDown, Clock3, Info, Loader2, Minus, Plus, RefreshCw, Search, Share, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { apiUrl } from "@/lib/api";
import { useEventRefresh } from "@/lib/event-refresh";
import { useEventHourOffset } from "@/lib/event-time";
import { getDeferredInstallPrompt, listenForInstallPrompt, promptInstall } from "@/lib/pwa";
import {
  getBrowserPushStatus,
  isIosDevice,
  subscribeBrowserPush,
  type BrowserPushStatus,
} from "@/lib/web-push";
import { buildLocalAutomaticWeeklyConquestTimeline } from "@/lib/weekly-conquest";
import { ALL_GACHA_EVENTS, buildGachaEventWindow, type GachaEvent } from "@/pages/gacha-events";
import { getNextWarioDungeonSpawn } from "@/pages/wario-dungeon";

type ReminderMode = "start" | "one-hour-and-start";
type ReminderDefinition =
  | { type: "gacha"; event: GachaEvent }
  | { type: "wairo" }
  | { type: "weekly-conquest" };

type ReminderOption = {
  id: string;
  group: "Wairo" | "S Rank Gacha" | "S Rank Facility" | "Weekly";
  title: string;
  detail: string;
  startsAt: Date;
  adjustedStartsAt: Date;
  offsetHours: number;
  mode: ReminderMode;
  definition: ReminderDefinition;
};

type SavedReminder = {
  id: string;
  offsetHours: number;
  mode: ReminderMode;
  savedAt: number;
};

type DeliveryChannels = {
  push: boolean;
  telegram: boolean;
  discord: boolean;
};

type DeliveryTargets = {
  telegramChatId: string;
};

type TelegramStatus = {
  configured: boolean;
  botUsername: string;
  botUrl: string;
  idHelperUrl: string;
};

type ReminderSchedulePreview = {
  subscriptionId: string;
  title: string;
  offsetHours: number;
  clientTimeZoneOffsetMinutes?: number;
  mode: ReminderMode;
  channels: DeliveryChannels;
  next: Array<{ kind: string; at: string; inMinutes: number }>;
};

type ReminderScheduleResponse = {
  now: string;
  graceMinutes: number;
  autoLookAheadMinutes: number;
  reminders: ReminderSchedulePreview[];
};

type DiscordStatus = {
  connected: boolean;
  username: string;
  lastError: string;
  oauthConfigured: boolean;
  missingErrors: string[];
  config: {
    botTokenConfigured: boolean;
    clientIdConfigured: boolean;
    clientSecretConfigured: boolean;
    redirectUriConfigured: boolean;
    oauthStateSecretConfigured: boolean;
  };
};

const STORAGE_KEY = "kaStandaloneEventReminders";
const DELIVERY_SETTINGS_KEY = "kaReminderDeliverySettings";
const CLIENT_ID_KEY = "kaReminderClientId";
const DISCORD_SERVER_INVITE_URL = "https://discord.gg/MkAKNJzye5";
const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const WEEKLY_ANCHOR_START = Date.parse("2026-04-05T00:00:00+09:00");

function readSaved(): Record<string, SavedReminder> {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeSaved(value: Record<string, SavedReminder>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

function readDeliverySettings(): { channels: DeliveryChannels; targets: DeliveryTargets } {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DELIVERY_SETTINGS_KEY) || "{}") as {
      channels?: Partial<DeliveryChannels>;
      targets?: Partial<DeliveryTargets>;
    };
    return {
      channels: {
        push: Boolean(parsed.channels?.push ?? true),
        telegram: Boolean(parsed.channels?.telegram ?? false),
        discord: Boolean(parsed.channels?.discord ?? false),
      },
      targets: {
        telegramChatId: String(parsed.targets?.telegramChatId || ""),
      },
    };
  } catch {
    return {
      channels: { push: true, telegram: false, discord: false },
      targets: { telegramChatId: "" },
    };
  }
}

function writeDeliverySettings(channels: DeliveryChannels, targets: DeliveryTargets) {
  window.localStorage.setItem(DELIVERY_SETTINGS_KEY, JSON.stringify({ channels, targets }));
}

function getOrCreateClientId() {
  const existing = window.localStorage.getItem(CLIENT_ID_KEY);
  if (existing) return existing;
  const id = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  window.localStorage.setItem(CLIENT_ID_KEY, id);
  return id;
}

function formatCountdown(target: Date, now: Date) {
  const ms = Math.max(0, target.getTime() - now.getTime());
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatDate(target: Date) {
  return target.toLocaleString([], { month: "short", day: "numeric", weekday: "short", hour: "numeric", minute: "2-digit" });
}

function offsetLabel(offset: number) {
  return offset >= 0 ? `+${offset}h` : `${offset}h`;
}

function nextReminderGachaEvent(event: GachaEvent, now: Date, offset: number) {
  const eventClockYear = new Date(now.getTime() + offset * 60 * 60 * 1000).getFullYear();
  const windows = [eventClockYear - 1, eventClockYear, eventClockYear + 1, eventClockYear + 2]
    .map((year) => buildGachaEventWindow(event, year, offset))
    .filter((window) => window.endAt.getTime() >= now.getTime())
    .sort((a, b) => a.startAt.getTime() - b.startAt.getTime());
  return windows[0] ?? buildGachaEventWindow(event, eventClockYear + 1, offset);
}

function nextWeeklyConquestStart(now: Date, offset: number) {
  const eventClockNow = new Date(now.getTime() + offset * HOUR_MS);
  const cycle = Math.floor((eventClockNow.getTime() - WEEKLY_ANCHOR_START) / (7 * DAY_MS)) + 1;
  return new Date(WEEKLY_ANCHOR_START + cycle * 7 * DAY_MS - offset * HOUR_MS);
}

function StatusGrid({ status }: { status: BrowserPushStatus | null }) {
  const rows = [
    ["standalone", status?.standalone ? "true" : "false"],
    ["browserName", status?.browserName ?? "..."],
    ["osName", status?.osName ?? "..."],
    ["deviceType", status?.deviceType ?? "..."],
    ["notificationAPI", status?.notificationPermission ?? "..."],
    ["serviceWorkerState", status?.serviceWorkerState ?? "..."],
    ["subscriptionState", status?.subscriptionState ?? "..."],
  ];

  return (
    <div className="rounded-2xl border border-white/10 bg-slate-900/80 p-4 font-mono text-[13px] shadow-xl">
      {rows.map(([label, value]) => (
        <div key={label} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 py-1">
          <div className="truncate text-right text-slate-500">{label}</div>
          <div className="truncate text-slate-100">{value}</div>
        </div>
      ))}
      {status?.supportReason ? (
        <div className="mt-3 rounded-xl bg-black/25 p-3 font-sans text-xs leading-relaxed text-slate-300">
          {status.supportReason}
        </div>
      ) : null}
    </div>
  );
}

function OffsetControl({ value, disabled, onChange }: { value: number; disabled?: boolean; onChange: (value: number) => void }) {
  return (
    <div className="flex items-center gap-2 rounded-full bg-white/5 p-1">
      <button type="button" onClick={() => onChange(Math.max(-23, value - 1))} disabled={disabled} className="grid h-8 w-8 place-items-center rounded-full bg-slate-800 text-slate-100 disabled:opacity-40" aria-label="Decrease offset">
        <Minus className="h-4 w-4" />
      </button>
      <div className="w-12 text-center font-mono text-sm text-slate-100">{offsetLabel(value)}</div>
      <button type="button" onClick={() => onChange(Math.min(23, value + 1))} disabled={disabled} className="grid h-8 w-8 place-items-center rounded-full bg-slate-800 text-slate-100 disabled:opacity-40" aria-label="Increase offset">
        <Plus className="h-4 w-4" />
      </button>
    </div>
  );
}

function InstallAssistCard({
  status,
  installPromptAvailable,
  onInstall,
  onDismiss,
}: {
  status: BrowserPushStatus | null;
  installPromptAvailable: boolean;
  onInstall: () => void;
  onDismiss: () => void;
}) {
  const isIos = isIosDevice();
  const isInstalled = Boolean(status?.standalone);

  return (
    <div className="rounded-2xl border border-sky-400/40 bg-sky-400/10 p-4 text-sky-50 shadow-xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Install KA Events</div>
          <div className="mt-1 text-lg font-bold text-white">{isInstalled ? "App is installed" : "Add this reminder app"}</div>
        </div>
        <button type="button" onClick={onDismiss} className="rounded-full bg-white/10 px-3 py-1 text-xs text-sky-100">
          Hide
        </button>
      </div>

      {isIos ? (
        <div className="mt-4 space-y-3">
          <div className="rounded-xl bg-black/25 p-3 text-sm leading-relaxed">
            iPhone requires one manual step. Tap Safari Share, then tap Add to Home Screen.
          </div>
          <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 text-sm">
            <div className="grid h-10 w-10 place-items-center rounded-full bg-sky-500 text-white">
              <Share className="h-5 w-5" />
            </div>
            <div>
              <div className="font-semibold text-white">1. Tap the Share button</div>
              <div className="text-sky-100/75">It is in Safari's bottom bar.</div>
            </div>
            <div className="grid h-10 w-10 place-items-center rounded-full bg-sky-500 text-white">
              <Plus className="h-5 w-5" />
            </div>
            <div>
              <div className="font-semibold text-white">2. Tap Add to Home Screen</div>
              <div className="text-sky-100/75">Then press Add on the screen that opens.</div>
            </div>
            <div className="grid h-10 w-10 place-items-center rounded-full bg-sky-500 text-white">
              <Smartphone className="h-5 w-5" />
            </div>
            <div>
              <div className="font-semibold text-white">3. Open KA Events</div>
              <div className="text-sky-100/75">Notifications only unlock after opening the Home Screen app.</div>
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          <div className="rounded-xl bg-black/25 p-3 text-sm leading-relaxed">
            Tap the button below to install KA Events as a small standalone reminder app.
          </div>
          <Button type="button" onClick={onInstall} disabled={!installPromptAvailable || isInstalled} className="w-full bg-sky-500 text-white hover:bg-sky-500 disabled:bg-slate-700">
            <Smartphone className="h-4 w-4" />
            {isInstalled ? "Installed" : installPromptAvailable ? "Install app" : "Install prompt not available yet"}
          </Button>
          {!installPromptAvailable && !isInstalled ? (
            <div className="text-xs text-sky-100/70">If your browser does not show an install prompt, use its menu and choose Install app or Add to Home Screen.</div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ReminderRow({
  option,
  now,
  active,
  busy,
  disabledReason,
  onSubscribe,
  onUnsubscribe,
  onOffsetChange,
  onWairoModeChange,
  onTap,
  error,
}: {
  option: ReminderOption;
  now: Date;
  active: boolean;
  busy: boolean;
  disabledReason?: string;
  onSubscribe: () => void;
  onUnsubscribe: () => void;
  onOffsetChange: (value: number) => void;
  onWairoModeChange?: (enabled: boolean) => void;
  onTap: () => void;
  error?: string;
}) {
  const disabled = Boolean(disabledReason);
  const turningOn = busy && !active;
  const turningOff = busy && active;

  return (
    <div className={`rounded-2xl border p-4 transition-colors ${active || turningOn ? "border-emerald-400/40 bg-emerald-400/10" : "border-white/10 bg-slate-900/70"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-sky-300">{option.group}</div>
          <div className="mt-1 truncate text-base font-semibold text-white">{option.title}</div>
          <div className="mt-1 text-xs text-slate-400">{option.detail}</div>
        </div>
        {active || turningOn ? (
          <div className="rounded-full bg-emerald-400/15 px-2 py-1 text-xs font-semibold text-emerald-300">{turningOn ? "Turning on" : "On"}</div>
        ) : (
          <div className="rounded-full bg-slate-800 px-2 py-1 text-xs font-semibold text-slate-400">Off</div>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-xl bg-black/20 px-3 py-2">
          <div className="text-slate-500">Next</div>
          <div className="mt-0.5 font-medium text-slate-100">{formatDate(option.startsAt)}</div>
        </div>
        <div className="rounded-xl bg-black/20 px-3 py-2">
          <div className="text-slate-500">Countdown</div>
          <div className="mt-0.5 font-medium text-slate-100">{formatCountdown(option.startsAt, now)}</div>
        </div>
      </div>

      {option.definition.type === "wairo" && onWairoModeChange ? (
        <label className="mt-3 flex items-center justify-between rounded-xl bg-black/20 px-3 py-2 text-sm text-slate-200">
          <span>One-hour warning + spawn</span>
          <Switch checked={option.mode === "one-hour-and-start"} disabled={active} onCheckedChange={onWairoModeChange} />
        </label>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <OffsetControl value={option.offsetHours} disabled={active} onChange={onOffsetChange} />
          <div className="rounded-xl bg-black/20 px-3 py-2 text-right">
            <div className="text-[11px] text-slate-500">Offset timer</div>
            <div className="mt-0.5 font-mono text-sm text-slate-100">{formatCountdown(option.adjustedStartsAt, now)}</div>
          </div>
        </div>
        {active ? (
          <Button type="button" variant="outline" onClick={onUnsubscribe} disabled={busy} className="border-red-300/30 bg-red-500/10 text-red-50 hover:bg-red-500/20">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <BellOff className="h-4 w-4" />}
            {turningOff ? "Stopping..." : "Stop"}
          </Button>
        ) : (
          <Button type="button" onClick={disabled ? onTap : onSubscribe} disabled={busy} className={`${disabled ? "bg-slate-700 text-slate-100 hover:bg-slate-700" : "bg-sky-500 text-white hover:bg-sky-500"}`}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
            {turningOn ? "Turning on..." : "Notify me"}
          </Button>
        )}
      </div>
      {active ? <div className="mt-2 text-xs text-slate-500">Stop this reminder to change its offset.</div> : null}
      {turningOn ? <div className="mt-2 text-xs text-emerald-200">Asking the browser and saving this reminder...</div> : null}
      {!active && error ? <div className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-100">{error}</div> : null}
      {!active && disabledReason ? <div className="mt-2 text-xs text-amber-200">{disabledReason}</div> : null}
    </div>
  );
}

export default function EventReminderAppPage() {
  const [now, setNow] = useState(() => new Date());
  const [eventOffset] = useEventHourOffset();
  const [clientId] = useState(() => getOrCreateClientId());
  const [status, setStatus] = useState<BrowserPushStatus | null>(null);
  const [message, setMessage] = useState("");
  const [showInstallHelp, setShowInstallHelp] = useState(() => new URLSearchParams(window.location.search).get("install") === "1");
  const [installPromptAvailable, setInstallPromptAvailable] = useState(() => Boolean(getDeferredInstallPrompt()));
  const [saved, setSaved] = useState<Record<string, SavedReminder>>(() => readSaved());
  const [reminderErrors, setReminderErrors] = useState<Record<string, string>>({});
  const [customOffsets, setCustomOffsets] = useState<Record<string, number>>({});
  const [wairoTwoStep, setWairoTwoStep] = useState(true);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<"all" | "gacha">("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [discordTestBusy, setDiscordTestBusy] = useState(false);
  const [telegramTestBusy, setTelegramTestBusy] = useState(false);
  const [scheduleCheckBusy, setScheduleCheckBusy] = useState(false);
  const [channels, setChannels] = useState<DeliveryChannels>(() => readDeliverySettings().channels);
  const [targets, setTargets] = useState<DeliveryTargets>(() => readDeliverySettings().targets);
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus>({
    configured: false,
    botUsername: "",
    botUrl: "",
    idHelperUrl: "https://t.me/userinfobot",
  });
  const [discordStatus, setDiscordStatus] = useState<DiscordStatus>({
    connected: false,
    username: "",
    lastError: "",
    oauthConfigured: false,
    missingErrors: [],
    config: {
      botTokenConfigured: false,
      clientIdConfigured: false,
      clientSecretConfigured: false,
      redirectUriConfigured: false,
      oauthStateSecretConfigured: false,
    },
  });

  const syncDiscordStatus = useCallback(async () => {
    try {
      const response = await fetch(apiUrl(`/event-reminders/discord/status?clientId=${encodeURIComponent(clientId)}`));
      if (!response.ok) return;
      const data = await response.json() as DiscordStatus;
      setDiscordStatus(data);
      if (!data.connected && channels.discord) {
        setChannels((current) => ({ ...current, discord: false }));
      }
    } catch {
      // Ignore network errors for status polling.
    }
  }, [channels.discord, clientId]);

  const syncTelegramStatus = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/event-reminders/telegram/status"));
      if (!response.ok) return;
      const data = await response.json() as TelegramStatus;
      setTelegramStatus(data);
    } catch {
      // Ignore network errors for status polling.
    }
  }, []);

  const refresh = useCallback(() => {
    setNow(new Date());
    getBrowserPushStatus().then(setStatus).catch(() => {});
    void syncDiscordStatus();
    void syncTelegramStatus();
  }, [syncDiscordStatus, syncTelegramStatus]);

  useEventRefresh(refresh, 180_000);

  useEffect(() => {
    writeSaved(saved);
  }, [saved]);

  useEffect(() => {
    return listenForInstallPrompt(() => setInstallPromptAvailable(Boolean(getDeferredInstallPrompt())));
  }, []);

  useEffect(() => {
    void syncDiscordStatus();
  }, [syncDiscordStatus]);

  useEffect(() => {
    void syncTelegramStatus();
  }, [syncTelegramStatus]);

  useEffect(() => {
    void fetch(apiUrl("/event-reminders/subscriptions/sync-client-context"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientId,
        clientTimeZoneOffsetMinutes: new Date().getTimezoneOffset(),
      }),
    }).catch(() => {
      // Ignore sync failures; subscriptions can still be updated when user re-saves.
    });
  }, [clientId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const discord = params.get("discord");
    const discordMessage = params.get("message");
    if (discord === "connected") {
      setMessage("Discord connected. You can now enable Discord DM delivery.");
      void syncDiscordStatus();
      params.delete("discord");
      params.delete("message");
      const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
      window.history.replaceState({}, "", next);
    } else if (discord === "error") {
      setMessage(discordMessage || "Discord connect failed. Please try again.");
      params.delete("discord");
      params.delete("message");
      const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
      window.history.replaceState({}, "", next);
    }
  }, [syncDiscordStatus]);

  useEffect(() => {
    writeDeliverySettings(channels, targets);
  }, [channels, targets]);

  const options = useMemo(() => {
    const wairoOffset = customOffsets["wairo:all"] ?? saved["wairo:all"]?.offsetHours ?? eventOffset;
    const nextWairoBase = getNextWarioDungeonSpawn(now, eventOffset);
    const nextWairoAdjusted = getNextWarioDungeonSpawn(now, wairoOffset);
    const wairo: ReminderOption[] = nextWairoBase && nextWairoAdjusted ? [{
      id: "wairo:all",
      group: "Wairo",
      title: "All Wairo Dungeon spawns",
      detail: wairoTwoStep ? "Notify one hour before and when every Wairo spawn starts." : "Notify when every Wairo spawn starts.",
      startsAt: nextWairoBase.startsAt,
      adjustedStartsAt: nextWairoAdjusted.startsAt,
      offsetHours: wairoOffset,
      mode: wairoTwoStep ? "one-hour-and-start" : "start",
      definition: { type: "wairo" },
    }] : [];

    const weeklyTimeline = buildLocalAutomaticWeeklyConquestTimeline(now, 1);
    const currentWeekly = weeklyTimeline.entries.find((entry) => entry.id === weeklyTimeline.currentId);
    const weeklyOffset = customOffsets["weekly-conquest:rotation"] ?? saved["weekly-conquest:rotation"]?.offsetHours ?? eventOffset;
    const weeklyAdjusted = nextWeeklyConquestStart(now, weeklyOffset);
    const weekly: ReminderOption[] = currentWeekly ? [{
      id: "weekly-conquest:rotation",
      group: "Weekly",
      title: "Weekly Conquest reset",
      detail: "Notify when the next weekly conquest rotation starts.",
      startsAt: new Date(currentWeekly.endsAt),
      adjustedStartsAt: weeklyAdjusted,
      offsetHours: weeklyOffset,
      mode: "start",
      definition: { type: "weekly-conquest" },
    }] : [];

    const gacha = ALL_GACHA_EVENTS
      .filter((event) => event.title.startsWith("S Rank") || event.kind === "facilities")
      .map((event): ReminderOption => {
        const resolved = nextReminderGachaEvent(event, now, eventOffset);
        const id = `gacha:${event.id}`;
        const optionOffset = customOffsets[id] ?? saved[id]?.offsetHours ?? eventOffset;
        const adjusted = nextReminderGachaEvent(event, now, optionOffset);
        return {
          id,
          group: event.kind === "facilities" ? "S Rank Facility" : "S Rank Gacha",
          title: event.title,
          detail: event.poolLabel,
          startsAt: resolved.startAt,
          adjustedStartsAt: adjusted.startAt,
          offsetHours: optionOffset,
          mode: "start",
          definition: { type: "gacha", event },
        };
      })
      .filter((option) => !search || option.title.toLowerCase().includes(search.toLowerCase()))
      .sort((a, b) => a.startsAt.getTime() - b.startsAt.getTime());

    return [...wairo, ...weekly, ...(expanded === "gacha" || search ? gacha : gacha.slice(0, 10))];
  }, [customOffsets, eventOffset, expanded, now, saved, search, wairoTwoStep]);

  const syncStatus = async () => setStatus(await getBrowserPushStatus());

  const connectDiscord = () => {
    const connectUrl = apiUrl(`/event-reminders/discord/connect?clientId=${encodeURIComponent(clientId)}&returnTo=${encodeURIComponent(window.location.origin)}`);
    window.location.href = connectUrl;
  };

  const disconnectDiscord = async () => {
    try {
      const response = await fetch(apiUrl("/event-reminders/discord/disconnect"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "Could not disconnect Discord.");
      }
      setChannels((current) => ({ ...current, discord: false }));
      setMessage("Discord disconnected.");
      await syncDiscordStatus();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not disconnect Discord.");
    }
  };

  const sendDiscordTestDm = async () => {
    setDiscordTestBusy(true);
    setMessage("Sending Discord test DM...");
    try {
      const response = await fetch(apiUrl("/event-reminders/discord/test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "Could not send Discord test DM.");
      }
      setMessage("Discord test DM sent.");
      await syncDiscordStatus();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not send Discord test DM.");
      await syncDiscordStatus();
    } finally {
      setDiscordTestBusy(false);
    }
  };

  const sendTelegramTestDm = async () => {
    const chatId = targets.telegramChatId.trim();
    if (!chatId) {
      setMessage("Telegram test requires a valid chat id.");
      return;
    }

    setTelegramTestBusy(true);
    setMessage("Sending Telegram test DM...");
    try {
      const response = await fetch(apiUrl("/event-reminders/telegram/test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chatId }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "Could not send Telegram test DM.");
      }
      setMessage("Telegram test DM sent.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not send Telegram test DM.");
    } finally {
      setTelegramTestBusy(false);
    }
  };

  const subscribe = async (option: ReminderOption) => {
    setBusyId(option.id);
    setReminderErrors((current) => {
      const next = { ...current };
      delete next[option.id];
      return next;
    });
    setMessage(`Saving reminder channels for ${option.title}...`);
    try {
      let pushSubscriptionJson: ReturnType<PushSubscription["toJSON"]> | null = null;
      if (channels.push) {
        const pushSubscription = await subscribeBrowserPush();
        pushSubscriptionJson = pushSubscription.toJSON();
      }
      const response = await fetch(apiUrl("/event-reminders/subscriptions"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pushSubscription: pushSubscriptionJson,
          clientId,
          clientTimeZoneOffsetMinutes: new Date().getTimezoneOffset(),
          subscriptionId: option.id,
          title: option.title,
          offsetHours: option.offsetHours,
          mode: option.mode,
          definition: option.definition,
          channels,
          targets,
        }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "The reminder server could not save this subscription.");
      }
      setSaved((current) => ({ ...current, [option.id]: { id: option.id, offsetHours: option.offsetHours, mode: option.mode, savedAt: Date.now() } }));
      setMessage(`${option.title} reminders are on.`);
      await syncStatus();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Could not subscribe.";
      setReminderErrors((current) => ({ ...current, [option.id]: errorMessage }));
      setMessage(errorMessage);
    } finally {
      setBusyId(null);
    }
  };

  const unsubscribe = async (option: ReminderOption) => {
    setBusyId(option.id);
    setMessage(`Turning off ${option.title} reminders...`);
    try {
      await fetch(apiUrl("/event-reminders/subscriptions"), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId, subscriptionId: option.id }),
      });
      setSaved((current) => {
        const next = { ...current };
        delete next[option.id];
        return next;
      });
      setReminderErrors((current) => {
        const next = { ...current };
        delete next[option.id];
        return next;
      });
      setMessage(`${option.title} reminders are off.`);
      await syncStatus();
    } catch {
      setMessage("Removed locally. The server could not be reached.");
    } finally {
      setBusyId(null);
    }
  };

  const needsIosInstall = isIosDevice() && status?.supported && !status.standalone;
  const noChannelReason = !channels.push && !channels.telegram && !channels.discord ? "Select at least one delivery channel." : "";
  const pushBlockedReason = channels.push && status && !status.supported ? status.supportReason : "";
  const telegramTargetReason = channels.telegram && !targets.telegramChatId.trim() ? "Telegram enabled: add your Telegram chat ID." : "";
  const discordTargetReason = channels.discord && !discordStatus.connected ? "Discord DM requires Discord connection first." : "";
  const blockedReason = noChannelReason || pushBlockedReason || telegramTargetReason || discordTargetReason;
  const sendTestNotification = async () => {
    setTestBusy(true);
    setMessage("Scheduling a test notification for 5 seconds from now...");
    try {
      let pushSubscriptionJson: ReturnType<PushSubscription["toJSON"]> | null = null;
      if (channels.push) {
        const pushSubscription = await subscribeBrowserPush();
        pushSubscriptionJson = pushSubscription.toJSON();
      }
      const response = await fetch(apiUrl("/event-reminders/test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientId,
          delaySeconds: 5,
          channels,
          targets,
          pushSubscription: pushSubscriptionJson,
        }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "Test could not be scheduled.");
      }
      setMessage("Test notification scheduled across selected channels. It should arrive in about 5 seconds.");
      await syncStatus();
      await syncDiscordStatus();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not send a test notification.");
    } finally {
      setTestBusy(false);
    }
  };

  const checkReminderScheduleNow = async () => {
    setScheduleCheckBusy(true);
    setMessage("Checking reminder schedule on server...");
    try {
      await fetch(apiUrl("/event-reminders/subscriptions/sync-client-context"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientId,
          clientTimeZoneOffsetMinutes: new Date().getTimezoneOffset(),
        }),
      }).catch(() => {
        // Best effort sync before debug fetch.
      });

      const response = await fetch(apiUrl(`/event-reminders/debug/next-due?clientId=${encodeURIComponent(clientId)}`));
      if (!response.ok) {
        const errorBody = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(errorBody?.error || "Could not load reminder schedule.");
      }
      const data = await response.json() as ReminderScheduleResponse;
      const withNext = data.reminders.find((item) => item.next.length > 0);
      if (!withNext) {
        setMessage("Server has your reminders, but no upcoming due items were found right now.");
        return;
      }
      const next = withNext.next[0];
      const tzPart = withNext.clientTimeZoneOffsetMinutes === undefined
        ? "server tz missing"
        : `server tz offset ${withNext.clientTimeZoneOffsetMinutes}m`;
      setMessage(`Server next due: ${withNext.title} (${next.kind}) in ${next.inMinutes}m | saved offset ${withNext.offsetHours}h | mode ${withNext.mode} | ${tzPart}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not check reminder schedule.");
    } finally {
      setScheduleCheckBusy(false);
    }
  };

  const installApp = async () => {
    const result = await promptInstall();
    setInstallPromptAvailable(Boolean(getDeferredInstallPrompt()));
    if (result === "accepted") setMessage("KA Events was installed.");
    else if (result === "dismissed") setMessage("Install was dismissed. You can try again from your browser menu.");
    else setMessage("Install prompt is not available in this browser yet.");
    await syncStatus();
  };

  return (
    <main className="min-h-dvh bg-black text-white">
      <div className="mx-auto flex min-h-dvh max-w-md flex-col px-4 pb-6 pt-[max(1rem,env(safe-area-inset-top))]">
        <div className="sticky top-0 z-10 -mx-4 bg-black/90 px-4 pb-3 pt-[max(1rem,env(safe-area-inset-top))] backdrop-blur">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-300">Kingdom Adventurers</div>
              <h1 className="text-2xl font-bold tracking-tight">Event Reminders</h1>
            </div>
            <button type="button" onClick={refresh} className="grid h-11 w-11 place-items-center rounded-full bg-slate-900" aria-label="Refresh">
              <RefreshCw className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="space-y-4">
          {showInstallHelp ? (
            <InstallAssistCard
              status={status}
              installPromptAvailable={installPromptAvailable}
              onInstall={installApp}
              onDismiss={() => setShowInstallHelp(false)}
            />
          ) : null}

          <StatusGrid status={status} />

          {needsIosInstall || status?.installRequired ? (
            <div className="rounded-2xl border border-sky-400/30 bg-sky-400/10 p-4 text-sm text-sky-50">
              On iPhone, tap Share, choose Add to Home Screen, then open KA Events from the new icon. Safari only enables web push for installed Home Screen apps.
            </div>
          ) : null}

          {status && !status.supported && !status.installRequired ? (
            <div className="rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-50">
              <div className="flex items-center gap-2 font-semibold">
                <Info className="h-4 w-4" />
                Push is unavailable here
              </div>
              <p className="mt-1 text-amber-50/85">{status.supportReason}</p>
              {isIosDevice() ? <p className="mt-2 text-amber-50/75">For iPhone, web push needs iOS 16.4 or newer and the app must be opened from the Home Screen icon.</p> : null}
            </div>
          ) : null}

          {message ? <div className="rounded-2xl border border-white/10 bg-white/10 p-3 text-sm text-slate-100">{message}</div> : null}

          <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Delivery Channels</div>
            <div className="mt-3 space-y-2 text-sm">
              <label className="flex items-center justify-between rounded-xl bg-black/20 px-3 py-2">
                <span>Web Push</span>
                <Switch checked={channels.push} onCheckedChange={(checked) => setChannels((current) => ({ ...current, push: checked }))} />
              </label>
              <label className="flex items-center justify-between rounded-xl bg-black/20 px-3 py-2">
                <span>Telegram</span>
                <Switch
                  checked={channels.telegram}
                  onCheckedChange={(checked) => {
                    setChannels((current) => ({ ...current, telegram: checked }));
                    if (checked && telegramStatus.botUrl) {
                      window.open(telegramStatus.botUrl, "_blank", "noopener,noreferrer");
                    }
                  }}
                />
              </label>
              {channels.telegram ? (
                <div className="space-y-2 rounded-xl bg-black/20 p-3">
                  <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 1: Open KA bot and tap Start</div>
                  {telegramStatus.botUrl ? (
                    <a
                      href={telegramStatus.botUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center rounded-lg bg-sky-500 px-3 py-1.5 text-xs font-semibold text-white"
                    >
                      Open KA Reminders bot
                    </a>
                  ) : (
                    <div className="text-xs text-amber-200">Set TELEGRAM_BOT_USERNAME on the server to enable one-tap Telegram bot open.</div>
                  )}
                  <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 2: Get your Telegram ID and paste it</div>
                  <a
                    href={telegramStatus.idHelperUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center rounded-lg border border-white/20 px-3 py-1.5 text-xs font-semibold text-slate-100"
                  >
                    Get my Telegram ID
                  </a>
                  <Button
                    type="button"
                    onClick={sendTelegramTestDm}
                    disabled={telegramTestBusy || !targets.telegramChatId.trim()}
                    className="bg-sky-500 text-white hover:bg-sky-500 disabled:bg-slate-700"
                  >
                    {telegramTestBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
                    {telegramTestBusy ? "Sending..." : "Send Telegram test DM"}
                  </Button>
                  <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 3: Send Telegram test DM</div>
                  <input
                    value={targets.telegramChatId}
                    onChange={(event) => setTargets((current) => ({ ...current, telegramChatId: event.target.value }))}
                    placeholder="Telegram chat ID (example: 123456789)"
                    className="w-full rounded-xl bg-black/30 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500"
                  />
                </div>
              ) : null}
              <label className="flex items-center justify-between rounded-xl bg-black/20 px-3 py-2">
                <span>Discord</span>
                <Switch
                  checked={channels.discord}
                  disabled={!discordStatus.connected}
                  onCheckedChange={(checked) => setChannels((current) => ({ ...current, discord: checked }))}
                />
              </label>
              <div className="rounded-xl bg-black/20 px-3 py-2 text-xs text-slate-300">
                {discordStatus.connected ? (
                  <div className="space-y-2">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 1: Join Server</div>
                    <div className="flex items-center justify-between gap-2">
                      <a
                        href={DISCORD_SERVER_INVITE_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center rounded-lg border border-white/20 px-3 py-1.5 text-xs font-semibold text-slate-100"
                      >
                        Join KA Discord server
                      </a>
                    </div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 2: Connected to Discord</div>
                    <div className="flex items-center justify-between gap-2">
                    <div>
                      Connected as {discordStatus.username || "Discord user"}
                      {discordStatus.lastError ? <div className="mt-1 text-amber-200">{discordStatus.lastError}</div> : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <Button type="button" onClick={sendDiscordTestDm} disabled={discordTestBusy} className="bg-indigo-500 text-white hover:bg-indigo-500 disabled:bg-slate-700">
                        {discordTestBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
                        {discordTestBusy ? "Sending..." : "Send Discord test DM"}
                      </Button>
                      <Button type="button" variant="outline" onClick={disconnectDiscord} className="border-white/20 bg-transparent text-slate-100 hover:bg-white/10">
                        Disconnect
                      </Button>
                    </div>
                  </div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 3: Send test DM</div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 1: Join Server</div>
                    <div className="flex items-center justify-between gap-2">
                      <span>Join the KA Discord server first so bot DMs can work.</span>
                      <a
                        href={DISCORD_SERVER_INVITE_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center rounded-lg border border-white/20 px-3 py-1.5 text-xs font-semibold text-slate-100"
                      >
                        Join KA Discord server
                      </a>
                    </div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 2: Connect Discord</div>
                    <div className="flex items-center justify-between gap-2">
                      <span>{discordStatus.oauthConfigured ? "Connect Discord to enable Discord DM reminders." : "Discord OAuth is not configured on the server yet."}</span>
                      <Button type="button" onClick={connectDiscord} disabled={!discordStatus.oauthConfigured} className="bg-indigo-500 text-white hover:bg-indigo-500 disabled:bg-slate-700">
                        Connect Discord
                      </Button>
                    </div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Step 3: Send Test DM</div>
                    <div className="text-slate-300">After connecting, tap Send Discord test DM.</div>
                    {discordStatus.missingErrors.length > 0 ? (
                      <div className="space-y-1 text-amber-200">
                        {discordStatus.missingErrors.map((item) => (
                          <div key={item}>{item}</div>
                        ))}
                      </div>
                    ) : null}
                    {!discordStatus.config.redirectUriConfigured ? (
                      <div className="text-amber-200">Set DISCORD_REDIRECT_URI to the Render API callback URL.</div>
                    ) : null}
                    {!discordStatus.config.oauthStateSecretConfigured ? (
                      <div className="text-amber-200">Set DISCORD_OAUTH_STATE_SECRET for stable OAuth state validation.</div>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
            <div className="mt-2 text-xs text-slate-500">Reminder channel settings are saved on this device and applied when you turn reminders on.</div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Test mode</div>
            <Button type="button" onClick={sendTestNotification} disabled={testBusy || Boolean(blockedReason)} className="mt-3 w-full bg-violet-500 text-white hover:bg-violet-500 disabled:bg-slate-700">
              {testBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
              {testBusy ? "Scheduling..." : "Send test notification in 5 seconds"}
            </Button>
            <Button type="button" onClick={checkReminderScheduleNow} disabled={scheduleCheckBusy} className="mt-2 w-full border border-white/20 bg-transparent text-slate-100 hover:bg-white/10 disabled:bg-slate-700" variant="outline">
              {scheduleCheckBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clock3 className="h-4 w-4" />}
              {scheduleCheckBusy ? "Checking..." : "Check reminder schedule now"}
            </Button>
            {blockedReason ? <div className="mt-2 text-xs text-amber-200">{blockedReason}</div> : <div className="mt-2 text-xs text-slate-500">This tests the installed app notification permission without waiting for an event.</div>}
          </div>

          <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-3">
            <div className="flex items-center gap-2 rounded-xl bg-black/30 px-3 py-2">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Find S Rank Samurai, Ninja..."
                className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-slate-500"
              />
            </div>
            <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
              <div>Event offset: {offsetLabel(eventOffset)}</div>
              <div>{Object.keys(saved).length} active</div>
            </div>
          </div>

          <div className="space-y-3">
            {options.map((option) => (
              <ReminderRow
                key={option.id}
                option={option}
                now={now}
                active={Boolean(saved[option.id])}
                busy={busyId === option.id}
                disabledReason={blockedReason}
                onSubscribe={() => subscribe(option)}
                onUnsubscribe={() => unsubscribe(option)}
                onOffsetChange={(value) => setCustomOffsets((current) => ({ ...current, [option.id]: value }))}
                onWairoModeChange={option.definition.type === "wairo" ? setWairoTwoStep : undefined}
                onTap={() => setMessage(blockedReason || "Notifications are not available in this browser.")}
                error={reminderErrors[option.id]}
              />
            ))}
          </div>

          {!search ? (
            <button type="button" onClick={() => setExpanded((value) => value === "all" ? "gacha" : "all")} className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/10 bg-slate-900/70 px-4 py-3 text-sm font-semibold text-slate-200">
              {expanded === "all" ? "Show all S Rank gacha events" : "Show fewer gacha events"}
              <ChevronDown className={`h-4 w-4 transition-transform ${expanded === "gacha" ? "rotate-180" : ""}`} />
            </button>
          ) : null}

          <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-4 text-sm text-emerald-50">
            <div className="flex items-center gap-2 font-semibold">
              <Check className="h-4 w-4" />
              Standalone mode
            </div>
            <p className="mt-1 text-emerald-100/80">
              This installed app is only for event reminders. Notifications open this page, not the full website.
            </p>
          </div>

          <div className="pb-4 text-center text-[11px] text-slate-600">
            <Clock3 className="mx-auto mb-1 h-4 w-4" />
            Refreshes on open, focus, and every few minutes.
          </div>
        </div>
      </div>
    </main>
  );
}

