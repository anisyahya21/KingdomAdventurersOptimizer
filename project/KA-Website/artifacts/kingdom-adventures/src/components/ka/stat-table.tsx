import type React from "react";
import { cn } from "@/lib/utils";

type StatTableProps = {
  children: React.ReactNode;
  className?: string;
};

type StatTableHeaderCellProps = {
  label: React.ReactNode;
  sublabel?: React.ReactNode;
};

export function StatTable({ children, className }: StatTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse", className)}>
        {children}
      </table>
    </div>
  );
}

export function StatTableHeaderCell({ label, sublabel }: StatTableHeaderCellProps) {
  return (
    <th className="text-center px-2 text-[10px] font-semibold text-muted-foreground pb-1">
      <div>{label}</div>
      {sublabel && <div className="font-normal text-muted-foreground/50 text-[9px] tabular-nums">{sublabel}</div>}
    </th>
  );
}
