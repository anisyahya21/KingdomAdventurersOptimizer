import { useEffect, useMemo, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { matchesLooseSearch } from "@/lib/search-normalize";

export type EquipmentSearchOption = {
  value: string;
  label: string;
  detail?: string;
  searchText?: string;
  iconName?: string;
  disabled?: boolean;
  badge?: { label: string; tone: "exact" | "unknown" | "impossible" | "neutral" };
};

export function EquipmentSearchSelect({
  id,
  label,
  value,
  options,
  onChange,
  equipIcons,
  placeholder = "Search equipment",
}: {
  id: string;
  label?: string;
  value: string;
  options: EquipmentSearchOption[];
  onChange: (value: string) => void;
  equipIcons?: Record<string, string>;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const listId = `${id}-results`;
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    setQuery(selected?.label ?? "");
  }, [selected?.label, value]);

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const filtered = useMemo(() => {
    const trimmed = query.trim();
    const matching = trimmed
      ? options.filter((option) => matchesLooseSearch(`${option.label} ${option.detail ?? ""} ${option.searchText ?? ""}`, trimmed))
      : options;
    return matching.slice(0, 120);
  }, [options, query]);

  const choose = (option: EquipmentSearchOption) => {
    if (option.disabled) return;
    onChange(option.value);
    setQuery(option.label);
    setOpen(false);
  };

  const selectedIcon = selected?.iconName ? getEquipmentIcon(equipIcons, selected.iconName) : undefined;
  const badgeStyle = (tone: NonNullable<EquipmentSearchOption["badge"]>["tone"]) => tone === "exact"
    ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-200"
    : tone === "impossible"
      ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-200"
      : tone === "unknown"
        ? "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
        : "bg-muted text-muted-foreground";

  return <div className="min-w-0 space-y-1">
    {label && <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>}
    <div className="relative min-w-0" ref={containerRef}>
      <Input
        id={id}
        value={query}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onFocus={(event) => { event.currentTarget.select(); setOpen(true); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter" && filtered.length > 0) { event.preventDefault(); choose(filtered[0]); }
        }}
        placeholder={placeholder}
        className={`h-10 pr-9 text-sm ${selectedIcon ? "pl-9" : ""}`}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
      />
      {selectedIcon && <img src={selectedIcon} alt="" className="pointer-events-none absolute left-2 top-1/2 h-5 w-5 -translate-y-1/2 rounded object-contain" />}
      <button
        type="button"
        aria-label={open ? "Close equipment results" : "Open equipment results"}
        aria-controls={listId}
        aria-expanded={open}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setOpen((current) => {
          if (current) { setQuery(selected?.label ?? ""); return false; }
          setQuery(""); return true;
        })}
        className="absolute right-1 top-1 flex h-8 w-8 items-center justify-center rounded text-primary hover:bg-primary/10"
      >
        <svg className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && <div id={listId} role="listbox" aria-label={label ?? "Equipment choices"} className="absolute left-0 right-0 top-full z-50 max-h-72 overflow-y-auto rounded-b-md border border-primary bg-popover shadow-lg">
        {filtered.length === 0
          ? <div className="px-3 py-3 text-center text-xs text-muted-foreground">No matching equipment</div>
          : filtered.map((option) => {
            const icon = option.iconName ? getEquipmentIcon(equipIcons, option.iconName) : undefined;
            return <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
              className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${option.disabled ? "cursor-not-allowed opacity-50" : "hover:bg-muted"} ${option.value === value ? "bg-primary/10 font-medium" : ""}`}
            >
              <span className="flex min-w-0 items-center gap-2">
                {icon ? <img src={icon} alt="" className="h-6 w-6 shrink-0 rounded object-contain" /> : <span className="h-6 w-6 shrink-0" />}
                <span className="min-w-0"><span className="block truncate">{option.label}</span>{option.detail && <span className="block truncate text-[11px] font-normal text-muted-foreground">{option.detail}</span>}</span>
              </span>
              {option.badge && <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${badgeStyle(option.badge.tone)}`}>{option.badge.label}</span>}
            </button>;
          })}
      </div>}
    </div>
  </div>;
}
