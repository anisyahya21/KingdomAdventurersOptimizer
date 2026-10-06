"use client"

import * as React from "react"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

type EngineId = "original" | "rust" | "go" | "cpp"
type EngineOption = { id: EngineId; label: string; description: string }
type EngineInfo = { selected: EngineId; options: EngineOption[]; busy: boolean }
type OptimizerApi = {
  optimizer_engines(): Promise<EngineInfo>
  optimizer_select_engine(id: EngineId): Promise<{ ok: boolean; error?: string }>
}

function getOptimizerApi(): OptimizerApi | undefined {
  const host = window as Window & { pywebview?: { api?: unknown } }
  return host.pywebview?.api as OptimizerApi | undefined
}

export default function OptimizerEngineSelector() {
  const [api, setApi] = React.useState<OptimizerApi | undefined>(() => typeof window === "undefined" ? undefined : getOptimizerApi())
  const [info, setInfo] = React.useState<EngineInfo | null>(null)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState("")

  React.useEffect(() => {
    const onReady = () => setApi(getOptimizerApi())
    window.addEventListener("pywebviewready", onReady)
    onReady()
    return () => window.removeEventListener("pywebviewready", onReady)
  }, [])

  React.useEffect(() => {
    if (!api) return
    let disposed = false
    let inFlight = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = async () => {
      if (disposed || inFlight) return
      inFlight = true
      try {
        const next = await api.optimizer_engines()
        if (!disposed) {
          setInfo(next)
          setError("")
        }
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        inFlight = false
        if (!disposed) timer = setTimeout(refresh, 2000)
      }
    }
    void refresh()
    return () => { disposed = true; if (timer) clearTimeout(timer) }
  }, [api])

  const choose = async (value: string) => {
    if (!api || !info || info.busy || pending) return
    setPending(true)
    setError("")
    try {
      const result = await api.optimizer_select_engine(value as EngineId)
      if (!result.ok) {
        setError(result.error || "Could not change optimizer engine.")
        return
      }
      setInfo(await api.optimizer_engines())
      // The original workspace caches strategy and holder projections. Recreate it so
      // an engine switch cannot display measurements from the previous engine.
      window.location.reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPending(false)
    }
  }

  const selected = info?.options.find((option) => option.id === info.selected)
  return <section className="rounded-lg border bg-card p-4" aria-label="Optimizer engine">
    <div className="flex flex-wrap items-center gap-3">
      <Label htmlFor="optimizer-engine">Optimizer engine</Label>
      {info && <Badge variant={info.busy ? "secondary" : "outline"}>{info.busy ? "Busy" : "Ready"}</Badge>}
      <div className="w-full sm:w-72">
        <Select value={info?.selected} onValueChange={choose} disabled={!api || !info || info.busy || pending}>
          <SelectTrigger id="optimizer-engine" aria-label="Select optimizer engine"><SelectValue placeholder={api ? "Loading engines…" : "Waiting for desktop bridge…"} /></SelectTrigger>
          <SelectContent>{info?.options.map((option) => <SelectItem key={option.id} value={option.id}>{option.label}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {selected && <p className="min-w-0 flex-1 text-sm text-muted-foreground">{selected.description}</p>}
    </div>
    {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
  </section>
}
