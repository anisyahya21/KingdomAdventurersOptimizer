import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, X, Search } from "lucide-react";
import { matchesLooseSearch } from "@/lib/search-normalize";

export interface SSOption {
  value: string;
  label: string;
  icon?: string;
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  options: SSOption[];
  placeholder?: string;
  className?: string;
  triggerClassName?: string;
  clearOnSelect?: boolean;
  disabled?: boolean;
  searchThreshold?: number | null;
}

export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Choose...",
  className = "",
  triggerClassName = "",
  clearOnSelect = false,
  disabled = false,
  searchThreshold = 8,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [dropStyle, setDropStyle] = useState<React.CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const selectedOption = options.find((o) => o.value === value);
  const selectedLabel = selectedOption?.label ?? (value || null);
  const selectedIcon = selectedOption?.icon;
  const filtered = query.trim()
    ? options.filter((o) => matchesLooseSearch(o.label, query))
    : options;
  const useSimpleSelect = searchThreshold !== null && options.length <= searchThreshold;

  const updateDropPos = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const above = spaceBelow < 240 && rect.top > 240;
    setDropStyle({
      position: "fixed",
      left: rect.left,
      width: Math.max(rect.width, 200),
      ...(above
        ? { bottom: window.innerHeight - rect.top + 2 }
        : { top: rect.bottom + 2 }),
      zIndex: 9999,
    });
  };

  const openDrop = () => {
    if (!triggerRef.current) return;
    updateDropPos();
    setOpen(true);
    setQuery("");
    setTimeout(() => searchRef.current?.focus(), 15);
  };

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (
        !dropRef.current?.contains(e.target as Node) &&
        !triggerRef.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    window.addEventListener("scroll", updateDropPos, true);
    window.addEventListener("resize", updateDropPos);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", updateDropPos, true);
      window.removeEventListener("resize", updateDropPos);
    };
  }, [open]);

  const select = (v: string) => {
    onChange(v);
    setOpen(false);
  };

  const dropdown = open
    ? createPortal(
        <div
          ref={dropRef}
          style={dropStyle}
          className="bg-popover border border-border rounded-md shadow-xl overflow-hidden"
        >
          <div className="p-1.5 border-b border-border">
            <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-muted/50">
              <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Type to filter..."
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/50 min-w-0"
                onKeyDown={(e) => {
                  if (e.key === "Escape") setOpen(false);
                  if (e.key === "Enter" && filtered.length > 0)
                    select(filtered[0].value);
                }}
              />
              {query && (
                <button
                  type="button"
                  onMouseDown={() => setQuery("")}
                >
                  <X className="w-3 h-3 text-muted-foreground hover:text-foreground" />
                </button>
              )}
            </div>
          </div>
          <div className="max-h-56 overflow-y-auto py-0.5">
            {filtered.length === 0 ? (
              <p className="text-xs text-muted-foreground/60 text-center py-3">
                No matches
              </p>
            ) : (
              filtered.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onMouseDown={() => select(opt.value)}
                  className={`w-full text-left px-3 py-1.5 text-sm hover:bg-muted/70 transition-colors flex items-center gap-2 ${
                    opt.value === value && !clearOnSelect
                      ? "bg-primary/10 text-primary font-medium"
                      : ""
                  }`}
                >
                  {opt.icon && (
                    <img src={opt.icon} alt="" className="h-5 w-5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                  )}
                  {opt.label}
                </button>
              ))
            )}
          </div>
        </div>,
        document.body
      )
    : null;

  if (useSimpleSelect) {
    return (
      <div className={`relative ${className}`}>
        <select
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring hover:border-ring/50 transition-colors ${triggerClassName}`}
        >
          <option value="">{placeholder}</option>
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openDrop())}
        onKeyDown={(e) => {
          if (open || disabled) return;
          if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
          e.preventDefault();
          if (options.length === 0) return;
          const currentIndex = options.findIndex((o) => o.value === value);
          if (e.key === "ArrowDown") {
            const nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % options.length;
            onChange(options[nextIndex].value);
          } else {
            const nextIndex = currentIndex < 0 ? options.length - 1 : (currentIndex - 1 + options.length) % options.length;
            onChange(options[nextIndex].value);
          }
        }}
        className={`flex items-center justify-between w-full rounded-md border border-input bg-background px-2 gap-1.5 text-left focus:outline-none focus:ring-1 focus:ring-ring hover:border-ring/50 transition-colors ${triggerClassName}`}
      >
        <span
          className={`min-w-0 flex-1 flex items-center gap-1.5 ${
            !selectedLabel ? "text-muted-foreground" : ""
          }`}
        >
          {selectedIcon && (
            <img src={selectedIcon} alt="" className="h-5 w-5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
          )}
          <span className="truncate">{selectedLabel ?? placeholder}</span>
        </span>
        {value && !clearOnSelect ? (
          <span
            role="button"
            aria-label="Clear"
            onMouseDown={(e) => {
              e.stopPropagation();
              e.preventDefault();
              onChange("");
            }}
            className="text-muted-foreground hover:text-foreground shrink-0 p-0.5 rounded hover:bg-muted cursor-pointer"
          >
            <X className="w-3 h-3" />
          </span>
        ) : (
          <ChevronDown
            className={`w-3.5 h-3.5 text-muted-foreground shrink-0 transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        )}
      </button>
      {dropdown}
    </div>
  );
}


