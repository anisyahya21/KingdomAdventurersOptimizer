import { ChevronDown, ChevronUp } from "lucide-react";
import { Input } from "@/components/ui/input";

type ThemedNumberInputProps = {
  id?: string;
  value: string | number;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
  inputClassName?: string;
  ariaLabel?: string;
  onValueChange?: (value: number) => void;
  onRawChange?: (value: string) => void;
  onRawBlur?: (value: string) => void;
  onEnter?: (value: string) => void;
};

function clamp(value: number, min: number, max?: number) {
  const upper = max ?? Number.POSITIVE_INFINITY;
  return Math.min(upper, Math.max(min, Math.floor(Number.isFinite(value) ? value : min)));
}

export function ThemedNumberInput({
  id,
  value,
  min = 0,
  max,
  step = 1,
  disabled = false,
  className = "",
  inputClassName = "",
  ariaLabel,
  onValueChange,
  onRawChange,
  onRawBlur,
  onEnter,
}: ThemedNumberInputProps) {
  const rawValue = String(value ?? "");
  const parsedValue = Number.parseInt(rawValue, 10);
  const numericValue = clamp(Number.isFinite(parsedValue) ? parsedValue : min, min, max);

  const commit = (nextRaw: string) => {
    const cleaned = nextRaw.replace(/\D/g, "");
    onRawChange?.(cleaned);
    if (cleaned !== "") onValueChange?.(clamp(Number.parseInt(cleaned, 10), min, max));
  };

  const stepBy = (direction: 1 | -1) => {
    const nextValue = clamp(numericValue + direction * step, min, max);
    onRawChange?.(String(nextValue));
    onValueChange?.(nextValue);
  };

  return (
    <div
      className={`grid h-9 grid-cols-[1fr_34px] overflow-hidden rounded-md border border-primary/40 bg-background shadow-sm transition-colors focus-within:border-primary ${
        disabled ? "opacity-60" : ""
      } ${className}`}
    >
      <Input
        id={id}
        type="text"
        inputMode="numeric"
        value={rawValue}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(event) => commit(event.target.value)}
        onBlur={(event) => onRawBlur?.(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onEnter?.(event.currentTarget.value);
        }}
        className={`h-full rounded-none border-0 bg-transparent px-3 text-center font-semibold tabular-nums shadow-none focus-visible:ring-0 ${inputClassName}`}
      />
      <div className="grid border-l border-primary/30 bg-primary/10">
        <button
          type="button"
          className="flex items-center justify-center text-primary transition-colors hover:bg-primary/20 disabled:text-muted-foreground"
          disabled={disabled || (max != null && numericValue >= max)}
          onClick={() => stepBy(1)}
          aria-label="Increase value"
        >
          <ChevronUp className="h-4 w-4 stroke-[3]" />
        </button>
        <button
          type="button"
          className="flex items-center justify-center border-t border-primary/30 text-primary transition-colors hover:bg-primary/20 disabled:text-muted-foreground"
          disabled={disabled || numericValue <= min}
          onClick={() => stepBy(-1)}
          aria-label="Decrease value"
        >
          <ChevronDown className="h-4 w-4 stroke-[3]" />
        </button>
      </div>
    </div>
  );
}
