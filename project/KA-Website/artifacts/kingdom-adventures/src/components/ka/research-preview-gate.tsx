import { useState, type ReactNode } from "react";

/** Recovery RE-006: prevent unverified reconstruction from appearing as a trusted tool. */
export function ResearchPreviewGate({ children }: { children: ReactNode }) {
  const [opened, setOpened] = useState(false);
  return (
    <section>
      <div className="m-4 rounded-lg border border-border bg-muted p-4 text-sm" role="note">
        <p className="font-semibold">Unverified research preview</p>
        <p>Placement, footprints and visual details are being checked against the game. Do not rely on this preview for accurate layouts.</p>
        {!opened && (
          <button type="button" className="mt-3 rounded border border-border bg-background px-3 py-2" onClick={() => setOpened(true)}>
            Open unverified preview
          </button>
        )}
      </div>
      {opened ? children : null}
    </section>
  );
}
