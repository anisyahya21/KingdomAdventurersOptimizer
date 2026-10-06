let deferredInstallPrompt: Event | null = null;

export function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/event-reminder-sw.js").catch(() => {});
  });
}

export function listenForInstallPrompt(onChange?: () => void) {
  const handler = (event: Event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    onChange?.();
  };
  window.addEventListener("beforeinstallprompt", handler);
  return () => window.removeEventListener("beforeinstallprompt", handler);
}

export function getDeferredInstallPrompt() {
  return deferredInstallPrompt;
}

export async function promptInstall() {
  const promptEvent = deferredInstallPrompt as (Event & {
    prompt?: () => Promise<void>;
    userChoice?: Promise<{ outcome: "accepted" | "dismissed" }>;
  }) | null;
  if (!promptEvent?.prompt) return "unavailable" as const;

  await promptEvent.prompt();
  const choice = await promptEvent.userChoice;
  if (choice?.outcome === "accepted") {
    deferredInstallPrompt = null;
    return "accepted" as const;
  }
  return "dismissed" as const;
}

export function isProbablyIosSafari() {
  const ua = window.navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
}
