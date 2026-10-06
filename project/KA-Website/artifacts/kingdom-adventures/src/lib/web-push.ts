import { apiUrl } from "@/lib/api";

export type BrowserPushStatus = {
  supported: boolean;
  standalone: boolean;
  installRequired: boolean;
  supportReason: string;
  notificationPermission: NotificationPermission | "unsupported";
  serviceWorkerState: string;
  subscriptionState: "subscribed" | "unsubscribed" | "unsupported";
  browserName: string;
  osName: string;
  deviceType: "mobile" | "desktop";
};

function urlBase64ToUint8Array(value: string) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

function isStandaloneDisplay() {
  return window.matchMedia("(display-mode: standalone)").matches
    || Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);
}

export function isIosDevice() {
  return /iPad|iPhone|iPod/.test(window.navigator.userAgent);
}

function browserName() {
  const ua = window.navigator.userAgent;
  if (/Edg/i.test(ua)) return "Edge";
  if (/CriOS|Chrome/i.test(ua)) return "Chrome";
  if (/Firefox|FxiOS/i.test(ua)) return "Firefox";
  if (/Safari/i.test(ua)) return isIosDevice() ? "Mobile Safari" : "Safari";
  return "Unknown";
}

function osName() {
  const ua = window.navigator.userAgent;
  if (/iPad|iPhone|iPod/.test(ua)) return "iOS";
  if (/Android/.test(ua)) return "Android";
  if (/Windows/.test(ua)) return "Windows";
  if (/Mac OS X/.test(ua)) return "macOS";
  return "Unknown";
}

function osVersion() {
  const ua = window.navigator.userAgent;
  const ios = ua.match(/OS (\d+)[._](\d+)(?:[._](\d+))?/);
  if (ios) return `${ios[1]}.${ios[2]}${ios[3] ? `.${ios[3]}` : ""}`;
  const android = ua.match(/Android (\d+(?:\.\d+)?)/);
  if (android) return android[1];
  return "";
}

export async function getBrowserPushStatus(): Promise<BrowserPushStatus> {
  const standalone = isStandaloneDisplay();
  const installRequired = isIosDevice() && !standalone;
  const hasServiceWorker = "serviceWorker" in navigator;
  const hasPushManager = "PushManager" in window;
  const hasNotification = "Notification" in window;
  const supported = !installRequired && hasServiceWorker && hasPushManager && hasNotification;
  let supportReason = "Ready for web push.";
  if (installRequired) supportReason = "Open this from the Home Screen icon to enable iPhone web push.";
  else if (!hasServiceWorker) supportReason = "Service workers are not available in this browser.";
  else if (!hasPushManager) supportReason = "PushManager is not available in this installed app.";
  else if (!hasNotification) supportReason = "The Notification API is not available in this browser.";
  let serviceWorkerState = "unsupported";
  let subscriptionState: BrowserPushStatus["subscriptionState"] = supported ? "unsubscribed" : "unsupported";

  if (supported) {
    try {
      const registration = await navigator.serviceWorker.getRegistration("/event-reminder-sw.js")
        ?? await navigator.serviceWorker.getRegistration();
      serviceWorkerState = registration?.active?.state ?? registration?.installing?.state ?? "not registered";
      const subscription = await registration?.pushManager.getSubscription();
      subscriptionState = subscription ? "subscribed" : "unsubscribed";
    } catch {
      serviceWorkerState = "unknown";
    }
  }

  return {
    supported,
    standalone,
    installRequired,
    supportReason,
    notificationPermission: "Notification" in window ? Notification.permission : "unsupported",
    serviceWorkerState,
    subscriptionState,
    browserName: browserName(),
    osName: osVersion() ? `${osName()} ${osVersion()}` : osName(),
    deviceType: /Mobi|Android|iPad|iPhone|iPod/.test(window.navigator.userAgent) ? "mobile" : "desktop",
  };
}

export async function subscribeBrowserPush() {
  if (isIosDevice() && !isStandaloneDisplay()) {
    throw new Error("On iPhone, add this app to the Home Screen first, then open it from the icon.");
  }

  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    throw new Error("This browser does not support web push notifications.");
  }

  await navigator.serviceWorker.register("/event-reminder-sw.js");
  const readyRegistration = await navigator.serviceWorker.ready;

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notifications are not allowed yet.");
  }

  const configResponse = await fetch(apiUrl("/event-reminders/config"));
  if (!configResponse.ok) throw new Error("Could not load notification server config.");
  const config = await configResponse.json() as { configured?: boolean; publicKey?: string };
  if (!config.configured || !config.publicKey) {
    throw new Error("Notification server is missing VAPID keys.");
  }

  return readyRegistration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(config.publicKey),
  });
}

export async function scheduleLocalTestNotification(delaySeconds = 5) {
  if (isIosDevice() && !isStandaloneDisplay()) {
    throw new Error("On iPhone, add this app to the Home Screen first, then open it from the icon.");
  }
  if (!("serviceWorker" in navigator) || !("Notification" in window)) {
    throw new Error("This browser does not support app notifications.");
  }

  await navigator.serviceWorker.register("/event-reminder-sw.js");
  const registration = await navigator.serviceWorker.ready;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notifications are not allowed yet.");
  }

  window.setTimeout(() => {
    registration.showNotification("KA Events test", {
      body: "This is your test notification.",
      icon: "/pwa-icon.svg",
      badge: "/pwa-icon.svg",
      tag: `ka-event-local-test-${Date.now()}`,
      data: { url: "/event-reminders" },
    });
  }, Math.max(1, delaySeconds) * 1000);
}

export async function getCurrentBrowserPushSubscription() {
  if (!("serviceWorker" in navigator)) return null;
  const registration = await navigator.serviceWorker.getRegistration("/event-reminder-sw.js")
    ?? await navigator.serviceWorker.getRegistration();
  return registration?.pushManager.getSubscription() ?? null;
}
