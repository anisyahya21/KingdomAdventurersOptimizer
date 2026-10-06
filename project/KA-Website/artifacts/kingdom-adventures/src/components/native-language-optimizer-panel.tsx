"use client"

import * as React from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type Language = "rust" | "go" | "cpp"
type Mode = "baseline" | "search"
type Engine = { id: Language; name: string; revision: string; validation: string }
type NativeInfo = { preferredLanguage: string | null; cpuCount: number; suggestedWorkers: number; engines: Engine[]; warning: string }
type RunRow = { encounterId: number; samples: number; meanEarned: number | null; highestEarned: number | null; unknown: number }
type HistoryRow = { language: string; mode: string; completed: number; elapsedSeconds: number; runsPerSecond: number | null; output: string; encounterId?: number; workers?: number; revision?: string }
type NativeStatus = { state: string; language?: string; elapsedSeconds: number; completed: number; total: number | null; runsPerSecond: number | null; output?: string; error?: string; rows: RunRow[]; history: HistoryRow[] }
type NativeApi = {
  native_optimizer_info(): Promise<NativeInfo>
  native_optimizer_start(options: { language: Language; encounterId: number; workers: number; trials: number; mode: Mode; generations: number }): Promise<{ ok: boolean; error?: string }>
  native_optimizer_status(): Promise<NativeStatus>
  native_optimizer_open_output(): Promise<unknown>
  native_optimizer_attach_last(): Promise<{ ok: boolean; error?: string }>
}

function getNativeApi(): NativeApi | undefined {
  const win = window as Window & { pywebview?: { api?: unknown } }
  return win.pywebview?.api as NativeApi | undefined
}
function resolveLanguage(value?: string | null): Language | undefined {
  const normalized = value?.toLowerCase()
  if (normalized === "rust" || normalized === "go" || normalized === "cpp") return normalized
  if (normalized === "c++") return "cpp"
  return undefined
}
function displayNumber(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}
function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—"
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`
}
function humanState(state: string) {
  return state.replace(/[_-]+/g, " ") || "unknown"
}

export default function NativeLanguageOptimizerPanel() {
  const [info, setInfo] = React.useState<NativeInfo | null>(null)
  const [status, setStatus] = React.useState<NativeStatus | null>(null)
  const [language, setLanguage] = React.useState<Language>("rust")
  const [encounterId, setEncounterId] = React.useState("0")
  const [workers, setWorkers] = React.useState("1")
  const [trials, setTrials] = React.useState("128")
  const [mode, setMode] = React.useState<Mode>("baseline")
  const [generations, setGenerations] = React.useState("2")
  const [busy, setBusy] = React.useState(false)
  const [message, setMessage] = React.useState("")
  const [pollError, setPollError] = React.useState("")
  const [api, setApi] = React.useState<NativeApi | undefined>(() => typeof window === "undefined" ? undefined : getNativeApi())

  React.useEffect(() => {
    const onBridgeReady = () => setApi(getNativeApi())
    window.addEventListener("pywebviewready", onBridgeReady)
    // Check again after subscribing so an API injected between render and effect
    // setup is still discovered.
    onBridgeReady()
    return () => window.removeEventListener("pywebviewready", onBridgeReady)
  }, [])

  React.useEffect(() => {
    if (!api) {
      setMessage("Native optimizer bridge is unavailable. Open this panel in the desktop WebView2 app.")
      return
    }
    let disposed = false
    void api.native_optimizer_info().then((data) => {
      if (disposed) return
      setInfo(data)
      setMessage("")
      const preferred = resolveLanguage(data.preferredLanguage)
      if (preferred) setLanguage(preferred)
      setWorkers(String(Math.max(1, data.suggestedWorkers || 1)))
    }).catch((error: unknown) => {
      if (!disposed) setMessage(error instanceof Error ? error.message : String(error))
    })
    return () => { disposed = true }
  }, [api])

  React.useEffect(() => {
    if (!api) return
    let disposed = false
    let inFlight = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      if (inFlight || disposed) return
      inFlight = true
      try {
        const next = await api.native_optimizer_status()
        if (!disposed) { setStatus(next); setPollError("") }
      } catch (error) {
        if (!disposed) setPollError(error instanceof Error ? error.message : String(error))
      } finally {
        inFlight = false
        if (!disposed) timer = setTimeout(poll, 1000)
      }
    }
    void poll()
    return () => { disposed = true; if (timer) clearTimeout(timer) }
  }, [api])

  const start = async () => {
    if (!api) return
    const encounter = Number(encounterId), workerCount = Number(workers), trialCount = Number(trials), generationCount = Number(generations)
    const maxWorkers = info ? Math.max(1, Math.floor(info.cpuCount * 0.8)) : Number.POSITIVE_INFINITY
    if ([encounterId, workers, trials, generations].some((value) => value.trim() === "") || ![encounter, workerCount, trialCount, generationCount].every(Number.isInteger) || encounter < 0 || encounter > 19 || workerCount < 1 || workerCount > maxWorkers || trialCount < 1 || trialCount > 4096 || generationCount < 1 || generationCount > 100) {
      setMessage(`Enter encounter 0–19, workers 1–${Number.isFinite(maxWorkers) ? maxWorkers : "available CPU limit"}, trials 1–4096, and generations 1–100.`)
      return
    }
    setBusy(true); setMessage("")
    try {
      const result = await api.native_optimizer_start({ language, encounterId: encounter, workers: workerCount, trials: trialCount, mode, generations: generationCount })
      setMessage(result.ok ? "Run started. It continues if this monitor is closed." : result.error || "The run could not be started.")
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  const reconnect = async () => {
    if (!api) return
    setBusy(true); setMessage("")
    try {
      const result = await api.native_optimizer_attach_last()
      setMessage(result.ok ? "Reconnected to the last run." : result.error || "No run could be reconnected.")
      if (result.ok) setStatus(await api.native_optimizer_status())
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  const openOutput = async () => {
    if (!api) return
    try {
      const result = await api.native_optimizer_open_output() as { ok?: boolean; error?: string }
      if (result?.ok === false) setMessage(result.error || "Could not open the output location.")
      else setMessage("")
    }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)) }
  }

  const progress = status?.total != null && status.total > 0 ? Math.min(100, 100 * status.completed / status.total) : null
  const selectedEngine = info?.engines.find((engine) => engine.id === language)
  const history = status?.history ?? []
  const runActive = ["starting", "preparing", "running"].includes(status?.state.toLowerCase() ?? "")
  return <Card>
    <CardHeader>
      <CardTitle>Native optimizer</CardTitle>
      <CardDescription>Run the Rust, Go, or C++ engine in the desktop app and monitor its durable results here.</CardDescription>
    </CardHeader>
    <CardContent className="space-y-6">
      {info?.warning && <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{info.warning}</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-2"><Label htmlFor="native-language">Engine</Label><Select value={language} onValueChange={(value) => setLanguage(value as Language)}><SelectTrigger id="native-language"><SelectValue /></SelectTrigger><SelectContent>{(["rust", "go", "cpp"] as const).map((id) => <SelectItem key={id} value={id}>{info?.engines.find((engine) => engine.id === id)?.name ?? ({ rust: "Rust", go: "Go", cpp: "C++" }[id])}</SelectItem>)}</SelectContent></Select>{selectedEngine && <p className="text-xs text-muted-foreground">{selectedEngine.revision} · {selectedEngine.validation}</p>}</div>
        <div className="space-y-2"><Label htmlFor="native-encounter">Encounter (0–19)</Label><Input id="native-encounter" type="number" min={0} max={19} step={1} value={encounterId} onChange={(event) => setEncounterId(event.target.value)} /></div>
        <div className="space-y-2"><Label htmlFor="native-workers">Workers{info ? ` (suggested ${info.suggestedWorkers}; max ${Math.max(1, Math.floor(info.cpuCount * 0.8))} of ${info.cpuCount} CPUs)` : ""}</Label><Input id="native-workers" type="number" min={1} max={info ? Math.max(1, Math.floor(info.cpuCount * 0.8)) : undefined} step={1} value={workers} onChange={(event) => setWorkers(event.target.value)} /></div>
        <div className="space-y-2"><Label htmlFor="native-trials">Trials</Label><Input id="native-trials" type="number" min={1} max={4096} step={1} value={trials} onChange={(event) => setTrials(event.target.value)} /></div>
        <div className="space-y-2"><Label htmlFor="native-mode">Mode</Label><Select value={mode} onValueChange={(value) => setMode(value as Mode)}><SelectTrigger id="native-mode"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="baseline">Baseline</SelectItem><SelectItem value="search">Search</SelectItem></SelectContent></Select><p className="text-xs text-muted-foreground">Baseline repeats a fixed strategy for speed; search evolves candidates.</p></div>
        <div className="space-y-2"><Label htmlFor="native-generations">Generations (search)</Label><Input id="native-generations" type="number" min={1} max={100} step={1} value={generations} onChange={(event) => setGenerations(event.target.value)} disabled={mode === "baseline"} /></div>
      </div>
      <div className="flex flex-wrap gap-2"><Button onClick={start} disabled={!api || busy || runActive}>{busy ? "Working…" : runActive ? "Run active" : "Start run"}</Button><Button variant="outline" onClick={reconnect} disabled={!api || busy}>Reconnect last run</Button><Button variant="secondary" onClick={openOutput} disabled={!api}>Open output</Button></div>
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
      <section aria-label="Current run" className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-medium">Run status: {status ? humanState(status.state) : "connecting"}</h3><span className="text-sm text-muted-foreground">Elapsed {status ? formatDuration(status.elapsedSeconds) : "unknown"}</span></div>
        {progress == null ? <p className="text-sm text-muted-foreground">Progress total unknown; {status?.completed ?? 0} durable records observed.</p> : <><Progress value={progress} aria-label="Run progress" /><p className="text-sm text-muted-foreground">{status?.completed.toLocaleString()} / {status?.total?.toLocaleString()} completed ({progress.toFixed(1)}%)</p></>}
        <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3"><p>Language: {status?.language ?? "—"}</p><p>Rate: {status?.runsPerSecond == null ? "unknown" : `${displayNumber(status.runsPerSecond)} runs/s`}</p><p>Output: <span className="break-all">{status?.output ?? "—"}</span></p></div>
        <p className="text-xs text-muted-foreground">Rate counts fresh durable records since this invocation, including startup; restored records are excluded. These measurements describe this diagnostic run and do not establish production performance.</p>
        {(status?.error || pollError) && <p role="alert" className="text-sm text-destructive">{status?.error || pollError}</p>}
      </section>
      <section className="space-y-2"><h3 className="font-medium">Encounter results</h3><p className="text-xs text-muted-foreground">Mean and highest Earned summarize recorded candidates in this run. For search runs, the mean is across candidates; it is not the mean score of the best strategy.</p><Table><TableHeader><TableRow><TableHead>Encounter</TableHead><TableHead>Samples</TableHead><TableHead>Mean Earned</TableHead><TableHead>Highest Earned</TableHead><TableHead>Unknown</TableHead></TableRow></TableHeader><TableBody>{status?.rows?.length ? status.rows.map((row) => <TableRow key={row.encounterId}><TableCell>{row.encounterId}</TableCell><TableCell>{row.samples.toLocaleString()}</TableCell><TableCell>{displayNumber(row.meanEarned)}</TableCell><TableCell>{displayNumber(row.highestEarned)}</TableCell><TableCell>{row.unknown.toLocaleString()}</TableCell></TableRow>) : <TableRow><TableCell colSpan={5} className="text-muted-foreground">No durable encounter records reported yet.</TableCell></TableRow>}</TableBody></Table></section>
      <section className="space-y-2"><h3 className="font-medium">Recent runs</h3><Table><TableHeader><TableRow><TableHead>Engine / revision</TableHead><TableHead>Mode</TableHead><TableHead>Encounter</TableHead><TableHead>Workers</TableHead><TableHead>Completed</TableHead><TableHead>Elapsed</TableHead><TableHead>Rate</TableHead><TableHead>Output</TableHead></TableRow></TableHeader><TableBody>{history.length ? history.map((row, index) => <TableRow key={`${row.output}-${index}`}><TableCell>{row.language}{row.revision ? <span className="block text-xs text-muted-foreground">{row.revision}</span> : null}</TableCell><TableCell>{row.mode}</TableCell><TableCell>{row.encounterId ?? "—"}</TableCell><TableCell>{row.workers ?? "—"}</TableCell><TableCell>{row.completed.toLocaleString()}</TableCell><TableCell>{formatDuration(row.elapsedSeconds)}</TableCell><TableCell>{row.runsPerSecond == null ? "unknown" : `${displayNumber(row.runsPerSecond)} runs/s`}</TableCell><TableCell className="max-w-48 break-all">{row.output}</TableCell></TableRow>) : <TableRow><TableCell colSpan={8} className="text-muted-foreground">No previous runs available.</TableCell></TableRow>}</TableBody></Table></section>
      <p className="text-xs text-muted-foreground">Closing this monitor leaves the engine runner active; use “Reconnect last run” when you return.</p>
    </CardContent>
  </Card>
}
