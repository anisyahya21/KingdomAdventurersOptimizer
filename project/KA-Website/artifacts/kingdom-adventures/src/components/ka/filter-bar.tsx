import type React from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type FilterBarProps = {
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  children?: React.ReactNode;
  className?: string;
};

export function FilterBar({
  searchValue,
  onSearchChange,
  searchPlaceholder = "Search",
  children,
  className,
}: FilterBarProps) {
  const hasSearch = searchValue !== undefined && onSearchChange;

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {children}
      {hasSearch && (
        <div className="relative sm:max-w-xs">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
          <Input
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder={searchPlaceholder}
            className="pl-8 h-9 text-sm"
          />
        </div>
      )}
    </div>
  );
}
