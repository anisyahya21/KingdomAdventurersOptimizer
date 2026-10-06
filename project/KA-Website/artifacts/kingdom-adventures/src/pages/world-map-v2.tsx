import { lazy, Suspense, useState } from "react";
import RuntimeWorldGridTestPage from "@/pages/runtime-world-grid-test";
import RuntimeWorldRenderTestPage from "@/pages/runtime-world-render-test";

const TileMapPage = lazy(() => import("@/pages/world-map"));

type MapTab = "isometric" | "top";

export default function WorldMapV2Page() {
  const [activeTab, setActiveTab] = useState<MapTab>("isometric");
  const [topView, setTopView] = useState<"fields" | "tiles">("fields");
  const [showLevelsOverlay, setShowLevelsOverlay] = useState(false);

  return (
    <div className="mx-auto max-w-[2400px] px-4 py-4">
      <div className="text-xs leading-relaxed text-muted-foreground">
        work in progress...
        <br />
        Map with full terrain, facility locations, resource spawns and map chip.
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setActiveTab("isometric")}
          className={`rounded border px-5 py-2 text-sm font-semibold transition-colors ${
            activeTab === "isometric" ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted"
          }`}
        >
          Isometric map
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("top")}
          className={`rounded border px-5 py-2 text-sm font-semibold transition-colors ${
            activeTab === "top" ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted"
          }`}
        >
          Top-view map
        </button>
        {activeTab === "isometric" ? (
          <button
            type="button"
            onClick={() => setShowLevelsOverlay((previous) => !previous)}
            aria-pressed={showLevelsOverlay}
            className={`rounded border px-5 py-2 text-sm font-semibold transition-colors ${
              showLevelsOverlay
                ? "border-primary bg-primary/20 text-primary"
                : "border-border bg-card hover:bg-muted"
            }`}
          >
            {showLevelsOverlay ? "Hide levels" : "Show levels"}
          </button>
        ) : null}
      </div>

      {activeTab === "isometric"
        ? <RuntimeWorldRenderTestPage publicMode showLevelOverlay={showLevelsOverlay} />
        : <>
            <div className="mt-3 flex gap-2" role="group" aria-label="Top-view maps">
              <button type="button" aria-pressed={topView === "fields"} onClick={() => setTopView("fields")} className="rounded border px-4 py-2 text-sm aria-pressed:bg-primary aria-pressed:text-primary-foreground">F0-F5 maps</button>
              <button type="button" aria-pressed={topView === "tiles"} onClick={() => setTopView("tiles")} className="rounded border px-4 py-2 text-sm aria-pressed:bg-primary aria-pressed:text-primary-foreground">Tile map</button>
            </div>
            {topView === "fields" ? <RuntimeWorldGridTestPage publicMode /> : <Suspense fallback={<p>Loading map...</p>}><TileMapPage /></Suspense>}
          </>}
    </div>
  );
}
