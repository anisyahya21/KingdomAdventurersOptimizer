import type { ReactNode } from "react";
import { SiteHeader } from "./site-header";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  return (
    <>
      <SiteHeader />
      <main className="ka-app-shell pt-14">
        {children}
      </main>
    </>
  );
}
