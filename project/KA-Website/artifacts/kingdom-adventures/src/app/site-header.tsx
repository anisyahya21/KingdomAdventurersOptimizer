import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { ArrowLeft, ExternalLink, Loader2, Menu, Moon, Search, Sun, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  fetchAuthSession,
  logoutAuthSession,
  startTelegramAuth,
  startTelegramFallbackAuth,
  updateAuthProfile,
  verifyTelegramFallbackAuth,
  type AuthSessionResponse,
  type TelegramFallbackStartResponse,
} from "@/lib/auth-session";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { buildGlobalSearchEntries } from "./global-search";
import { NAV_SECTIONS } from "./navigation";

export function SiteHeader() {
  const [pathname, navigate] = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [dark, setDark] = useState(() =>
    typeof window !== "undefined"
      ? localStorage.getItem("theme") === "dark" ||
        (!localStorage.getItem("theme") && window.matchMedia("(prefers-color-scheme: dark)").matches)
      : false,
  );
  const [authSession, setAuthSession] = useState<AuthSessionResponse>({ authenticated: false, guest: true });
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [fallbackData, setFallbackData] = useState<TelegramFallbackStartResponse | null>(null);
  const [fallbackBusy, setFallbackBusy] = useState(false);
  const [fallbackError, setFallbackError] = useState<string | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [profileGameId, setProfileGameId] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const authPopupRef = useRef<Window | null>(null);
  const authPopupPollTimerRef = useRef<number | null>(null);
  const authPopupPollInFlightRef = useRef(false);
  const hasNavRef = useRef(false);
  const prevPathRef = useRef(pathname);

  useEffect(() => {
    if (prevPathRef.current !== pathname) {
      hasNavRef.current = true;
      prevPathRef.current = pathname;
    }
  }, [pathname]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    let mounted = true;
    setAuthLoading(true);
    fetchAuthSession()
      .then((session) => {
        if (mounted) setAuthSession(session);
      })
      .finally(() => {
        if (mounted) setAuthLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const refreshAuthSession = () => {
    setAuthLoading(true);
    return fetchAuthSession()
      .then((session) => {
        setAuthSession(session);
        window.dispatchEvent(new CustomEvent("ka-auth-changed", { detail: { authenticated: session.authenticated } }));
      })
      .finally(() => setAuthLoading(false));
  };

  const stopAuthPopupPolling = () => {
    if (typeof window !== "undefined" && authPopupPollTimerRef.current !== null) {
      window.clearInterval(authPopupPollTimerRef.current);
      authPopupPollTimerRef.current = null;
    }
    authPopupPollInFlightRef.current = false;
  };

  const startAuthPopupPolling = () => {
    stopAuthPopupPolling();
    let ticks = 0;
    authPopupPollTimerRef.current = window.setInterval(() => {
      ticks += 1;

      if (!authPopupRef.current || authPopupRef.current.closed) {
        stopAuthPopupPolling();
        authPopupRef.current = null;
        setAuthBusy(false);
        void refreshAuthSession();
        return;
      }

      // Stop trying after 2 minutes to avoid a stuck spinner on silent popup failures.
      if (ticks > 120) {
        stopAuthPopupPolling();
        setAuthBusy(false);
        return;
      }

      if (authPopupPollInFlightRef.current) {
        return;
      }
      authPopupPollInFlightRef.current = true;

      void fetchAuthSession()
        .then((session) => {
          if (!session.authenticated) return;
          setAuthSession(session);
          window.dispatchEvent(new CustomEvent("ka-auth-changed", { detail: { authenticated: true } }));
          setAuthBusy(false);
          if (authPopupRef.current && !authPopupRef.current.closed) {
            authPopupRef.current.close();
          }
          authPopupRef.current = null;
          stopAuthPopupPolling();
        })
        .finally(() => {
          authPopupPollInFlightRef.current = false;
        });
    }, 1000);
  };

  const goBack = () => {
    if (hasNavRef.current) {
      window.history.back();
    } else {
      navigate("/");
    }
  };

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;

      if (menuRef.current && !menuRef.current.contains(target)) {
        setMenuOpen(false);
      }

      if (searchRef.current && !searchRef.current.contains(target)) {
        setSearchOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const searchEntries = useMemo(() => buildGlobalSearchEntries(), []);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return searchEntries.filter((entry) =>
      entry.label.toLowerCase().includes(q),
    ).slice(0, 8);
  }, [query, searchEntries]);

  useEffect(() => {
    function handleAuthMessage(event: MessageEvent) {
      const payload = event.data as { source?: string; type?: string } | null;
      if (!payload || payload.source !== "ka-auth") return;
      stopAuthPopupPolling();
      if (authPopupRef.current && !authPopupRef.current.closed) {
        authPopupRef.current.close();
      }
      authPopupRef.current = null;
      setAuthBusy(false);
      void refreshAuthSession();
    }

    window.addEventListener("message", handleAuthMessage);
    return () => window.removeEventListener("message", handleAuthMessage);
  }, []);

  useEffect(() => {
    if (authSession.authenticated && authBusy) {
      setAuthBusy(false);
    }
  }, [authBusy, authSession.authenticated]);

  useEffect(() => {
    return () => {
      stopAuthPopupPolling();
    };
  }, []);

  const startPopupLogin = async () => {
    setAuthBusy(true);
    try {
      const started = await startTelegramAuth();
      const popup = window.open(
        started.widgetUrl,
        "ka-telegram-login",
        "popup=yes,width=540,height=720,resizable=yes,scrollbars=yes",
      );
      authPopupRef.current = popup;
      if (!popup) {
        window.location.href = started.widgetUrl;
        return;
      }

      startAuthPopupPolling();
    } catch {
      stopAuthPopupPolling();
      authPopupRef.current = null;
      setAuthBusy(false);
    }
  };

  const startFallbackLogin = async () => {
    setFallbackOpen(true);
    setFallbackBusy(true);
    setFallbackError(null);
    try {
      const started = await startTelegramFallbackAuth();
      setFallbackData(started);
    } catch (error) {
      setFallbackData(null);
      setFallbackError(error instanceof Error ? error.message : "Code login is unavailable.");
    } finally {
      setFallbackBusy(false);
    }
  };

  const verifyFallbackLogin = async () => {
    if (!fallbackData?.state) return;
    setFallbackBusy(true);
    setFallbackError(null);
    try {
      await verifyTelegramFallbackAuth(fallbackData.state);
      await refreshAuthSession();
      setFallbackOpen(false);
      setFallbackData(null);
    } catch (error) {
      setFallbackError(error instanceof Error ? error.message : "Could not verify code.");
    } finally {
      setFallbackBusy(false);
    }
  };

  const logout = async () => {
    setAuthBusy(true);
    try {
      await logoutAuthSession();
      setAuthSession({ authenticated: false, guest: true });
      window.dispatchEvent(new CustomEvent("ka-auth-changed", { detail: { authenticated: false } }));
    } finally {
      setAuthBusy(false);
    }
  };

  const openProfileDialog = () => {
    setProfileName(authSession.user?.displayName || "");
    setProfileGameId(authSession.user?.gameId || "");
    setProfileError(null);
    setProfileOpen(true);
  };

  const saveProfile = async () => {
    const normalizedName = profileName.trim();
    const normalizedGameId = profileGameId.trim();
    if (normalizedGameId && !/^\d{3},\d{3},\d{3}$/.test(normalizedGameId)) {
      setProfileError("Game ID must match 123,456,789 format.");
      return;
    }

    setProfileBusy(true);
    setProfileError(null);
    try {
      await updateAuthProfile({
        displayName: normalizedName,
        gameId: normalizedGameId,
      });
      await refreshAuthSession();
      setProfileOpen(false);
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "Could not save profile.");
    } finally {
      setProfileBusy(false);
    }
  };

  return (
    <div className="fixed inset-x-0 top-0 z-[60] border-b border-border bg-background/90 backdrop-blur">
      <div className="w-full min-w-0 px-2 sm:px-4 h-14 flex items-center justify-between gap-0.5 sm:gap-3">
        <div className="flex shrink-0 items-center gap-0.5">
          {pathname !== "/" && (
            <Button variant="ghost" size="icon" className="h-11 w-8 min-[350px]:w-9 sm:w-11" onClick={goBack} title="Go back">
              <ArrowLeft className="h-5 w-5 sm:h-[30px] sm:w-[30px]" />
            </Button>
          )}

          <div ref={menuRef}>
            <Button variant="ghost" size="icon" className="h-11 w-8 min-[350px]:w-9 sm:w-11" onClick={() => setMenuOpen(!menuOpen)} aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen}>
              <Menu className="h-5 w-5 sm:h-[30px] sm:w-[30px]" />
            </Button>

            {menuOpen && (
              <div className="absolute left-2 top-full z-50 mt-2 max-h-[calc(100dvh-4.5rem)] w-80 max-w-[calc(100vw-1rem)] overflow-y-auto sm:left-4">
                <Card>
                  <CardContent className="space-y-3 p-3">
                    <Button variant="ghost" className="w-full justify-start sm:hidden" onClick={() => setDark((d) => !d)}>
                      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                      {dark ? "Switch to light mode" : "Switch to dark mode"}
                    </Button>
                    <nav aria-label="Main menu" className="space-y-4">
                      {NAV_SECTIONS.map((section) => (
                        <div key={section.title} className="space-y-1">
                          <div className="px-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {section.title}
                          </div>
                          <div className="space-y-0.5">
                            {section.children.map((link) => link.external ? (
                              <a
                                key={link.href}
                                href={link.href}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={() => setMenuOpen(false)}
                                className="flex min-h-11 items-center justify-between rounded-md px-3 py-2 text-sm hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                {link.label}<ExternalLink className="h-4 w-4 shrink-0" aria-hidden="true" />
                              </a>
                            ) : (
                              <button
                                key={link.href}
                                type="button"
                                onClick={() => {
                                  navigate(link.href);
                                  setMenuOpen(false);
                                }}
                                aria-current={pathname === link.href ? "page" : undefined}
                                className="flex min-h-11 w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[current=page]:bg-muted"
                              >
                                {link.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </nav>
                  </CardContent>
                </Card>
              </div>
            )}
          </div>
        </div>

        <Link
          href="/"
          className="shrink-0 whitespace-nowrap text-[clamp(11px,3.7vw,16px)] sm:text-2xl font-semibold hover:opacity-80 transition-opacity"
          title="Go to home page"
        >
          Kingdom Adventurers
        </Link>

        <div className="flex min-w-0 flex-1 items-center justify-end gap-0.5 sm:flex-none">
          {authLoading ? (
            <Button variant="ghost" className="h-11 min-w-0 px-1 sm:px-3 text-xs" disabled>
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="hidden sm:inline">Loading</span>
            </Button>
          ) : authSession.authenticated ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="h-11 min-w-0 max-w-[4rem] flex-1 px-1 text-xs sm:max-w-none sm:flex-none sm:px-3" title={authSession.user?.displayName || "Open account menu"} disabled={authBusy}>
                  {authBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  <span className="min-w-0 truncate">{authSession.user?.displayName || "Account"}</span>
                  {authSession.user?.isAdmin ? <span className="hidden sm:inline">(Admin)</span> : null}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="text-xs text-muted-foreground">
                  {authSession.user?.telegramUsername ? `@${authSession.user.telegramUsername}` : "Signed in"}
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={openProfileDialog}>Edit profile</DropdownMenuItem>
                <DropdownMenuItem onClick={() => void logout()}>Log out</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button variant="ghost" className="h-11 shrink-0 px-1 min-[350px]:px-2 sm:px-3 text-xs" onClick={() => setLoginOpen(true)} disabled={authBusy || fallbackBusy}>
              {authBusy || fallbackBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Log in
            </Button>
          )}

          <Button variant="ghost" size="icon" className="hidden h-11 w-11 shrink-0 sm:inline-flex" onClick={() => setDark((d) => !d)} title={dark ? "Switch to light mode" : "Switch to dark mode"}>
            {dark ? <Sun className="h-[30px] w-[30px]" /> : <Moon className="h-[30px] w-[30px]" />}
          </Button>

          <div ref={searchRef}>
            <Button variant="ghost" size="icon" className="h-11 w-8 shrink-0 min-[350px]:w-9 sm:w-11" onClick={() => setSearchOpen(!searchOpen)}>
              <Search className="h-5 w-5 sm:h-[30px] sm:w-[30px]" />
            </Button>

            {searchOpen && (
              <div className="absolute right-4 top-full mt-2 z-50 w-[min(32rem,calc(100vw-2rem))]">
                <Card>
                  <CardContent className="p-3 space-y-3">
                    <div className="relative">
                      <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2" />
                      <Input
                        autoFocus
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search..."
                        className="pl-9 h-10 pr-9"
                      />

                      {query && (
                        <button
                          onClick={() => setQuery("")}
                          className="absolute right-3 top-1/2 -translate-y-1/2"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      )}
                    </div>

                    {filtered.map((entry) => (
                      <button
                        key={`${entry.subtitle}-${entry.label}`}
                        onClick={() => {
                          navigate(entry.href);
                          setSearchOpen(false);
                          setQuery("");
                        }}
                        className="block w-full text-left px-2 py-2 hover:bg-muted/40 rounded-md"
                      >
                        <div className="font-medium text-sm">{entry.label}</div>
                        <div className="text-xs opacity-70">{entry.subtitle}</div>
                      </button>
                    ))}
                  </CardContent>
                </Card>
              </div>
            )}
          </div>
        </div>
      </div>

      <Dialog open={loginOpen} onOpenChange={setLoginOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Log in</DialogTitle>
            <DialogDescription>Choose how to log in with Telegram.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Button onClick={() => { setLoginOpen(false); void startPopupLogin(); }} disabled={authBusy}>Log in with Telegram</Button>
            <Button variant="outline" onClick={() => { setLoginOpen(false); void startFallbackLogin(); }} disabled={fallbackBusy}>Log in with bot code</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={fallbackOpen} onOpenChange={setFallbackOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Log in with bot code</DialogTitle>
            <DialogDescription>
              This path avoids Telegram phone confirmation popups. Send this command to the bot and verify.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            {fallbackData ? (
              <>
                <div className="rounded-md border border-border/70 bg-muted/30 p-3 font-mono text-xs break-all">
                  {fallbackData.command}
                </div>
                <div className="flex flex-wrap gap-2">
                  <a href={fallbackData.botUrl} target="_blank" rel="noopener noreferrer">
                    <Button size="sm" variant="outline">Open Bot</Button>
                  </a>
                  {fallbackData.deepLinkUrl ? (
                    <a href={fallbackData.deepLinkUrl} target="_blank" rel="noopener noreferrer">
                      <Button size="sm" variant="outline">Open Bot with Code</Button>
                    </a>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      void navigator.clipboard?.writeText(fallbackData.command);
                    }}
                  >
                    Copy Command
                  </Button>
                  <Button size="sm" onClick={verifyFallbackLogin} disabled={fallbackBusy}>
                    {fallbackBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    I Sent It, Verify
                  </Button>
                </div>
              </>
            ) : (
              <div className="text-muted-foreground">Preparing login code...</div>
            )}

            {fallbackError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                {fallbackError}
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={profileOpen} onOpenChange={setProfileOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit profile</DialogTitle>
            <DialogDescription>
              Set how your name appears and optionally add your game ID.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Displayed Name</div>
              <Input
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
                maxLength={64}
                placeholder="Your name"
              />
            </div>

            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Game ID</div>
              <Input
                value={profileGameId}
                onChange={(event) => setProfileGameId(event.target.value)}
                placeholder="123,456,789"
              />
              <div className="text-[11px] text-muted-foreground">Format: 3 digits, comma, 3 digits, comma, 3 digits.</div>
            </div>

            {profileError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                {profileError}
              </div>
            ) : null}

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setProfileOpen(false)} disabled={profileBusy}>Cancel</Button>
              <Button onClick={saveProfile} disabled={profileBusy}>
                {profileBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

