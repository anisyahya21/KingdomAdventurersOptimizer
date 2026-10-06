import type React from "react";
import { cn } from "@/lib/utils";

type PageHeaderProps = {
  icon?: React.ReactNode;
  title: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
};

export function PageHeader({ icon, title, children, actions, className }: PageHeaderProps) {
  return (
    <header className={cn("space-y-1", className)}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <h1 className="text-xl font-bold tracking-tight">{title}</h1>
        </div>
        {actions}
      </div>
      {children && (
        <div className="max-w-4xl space-y-2 text-sm leading-relaxed text-muted-foreground">
          {children}
        </div>
      )}
    </header>
  );
}
