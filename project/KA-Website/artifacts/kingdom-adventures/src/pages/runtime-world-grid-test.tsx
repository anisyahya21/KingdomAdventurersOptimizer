import { useEffect, useMemo, useRef, useState } from "react";
import { parseMapBinarySectionA } from "@/runtime/world-builder/map-loader";
import type { ParsedMapBinary, RawMapCellFields } from "@/runtime/world-builder/types";

type FieldKey = keyof RawMapCellFields;
type NumberDisplayMode = "dec" | "hex";
type Camera = {
  offsetX: number;
  offsetY: number;
  zoom: number;
};
type HoverCell = { x: number; y: number };
type NatureVisualCategory = "terrain-nature" | "resource-treasure" | "human-npc" | "special-unknown";
type NatureCategoryVisibility = Record<NatureVisualCategory, boolean>;
type TerrainRow = {
  id: number;
  type: number;
  category: number;
  name: string;
  res: number;
  img: number;
  frame: number;
  natureId: number;
  natureGroupId: number;
};
type MapChipRow = {
  id: number;
  type: number;
  category: number;
  name: string;
  res: number;
  img: number;
  seb: number;
  frame: number;
  relatedDataType: number;
  relatedDataId: number;
  field17: number;
  field18: number;
  field19: number;
  layer: number;
  field21: number;
  sizeWidth: number;
  sizeHeight: number;
  field24: number;
  field25: number;
  field26: number;
  field27: number;
};
type NatureCell = {
  x: number;
  y: number;
  rowId: number;
  name: string;
  img: number;
  frame: number;
  natureId: number;
  natureGroupId: number;
  category: NatureVisualCategory;
  score: number;
  reason: string;
};

type AssetDiagnostic = {
  url: string;
  status: number;
  contentType: string | null;
  snippet: string;
};

type RuntimeWorldGridTestPageProps = {
  publicMode?: boolean;
};

const FIELD_KEYS: FieldKey[] = ["f0", "f1", "f2", "f3", "f4", "f5"];
const BASE_TILE_SIZE = 6;
const BASE_URL = typeof window !== "undefined"
  ? new URL(import.meta.env.BASE_URL ?? "/", window.location.origin).href
  : "/";

function resolveAssetUrl(relativePath: string) {
  return new URL(relativePath.replace(/^\//, ""), BASE_URL).href;
}

const MAP_SECTION_A_PATH = resolveAssetUrl("world-assets/map/map_160_160.bin");
const TERRAIN_PATH = resolveAssetUrl("world-assets/xls/English.lproj/Terrain.txt");
const MAP_CHIP_PATH = resolveAssetUrl("world-assets/xls/English.lproj/MapChip.txt");
const CHIP_IMG_INF_PATH = resolveAssetUrl("world-assets/chip/img.inf");
const NATURE_IMG_INF_PATH = resolveAssetUrl("world-assets/nature/img.inf");
const NATURE_CATEGORY_LAYER_ORDER: Record<NatureVisualCategory, number> = {
  "terrain-nature": 1,
  "resource-treasure": 2,
  "human-npc": 3,
  "special-unknown": 4,
};
const NATURE_CATEGORY_CHANCE_BY_TERRAIN_TYPE: Record<number, Partial<Record<NatureVisualCategory, number>>> = {
  1: { "terrain-nature": 0.14, "resource-treasure": 0.02, "human-npc": 0.02, "special-unknown": 0.02 },
  2: { "terrain-nature": 0.3, "resource-treasure": 0.04, "human-npc": 0.02, "special-unknown": 0.04 },
  3: { "terrain-nature": 0.18, "resource-treasure": 0.08, "human-npc": 0.015, "special-unknown": 0.03 },
  4: { "terrain-nature": 0.28, "resource-treasure": 0.14, "human-npc": 0.015, "special-unknown": 0.04 },
  5: { "terrain-nature": 0.24, "resource-treasure": 0.12, "human-npc": 0.01, "special-unknown": 0.03 },
  6: { "terrain-nature": 0.2, "resource-treasure": 0.04, "human-npc": 0.01, "special-unknown": 0.02 },
  7: { "terrain-nature": 0.24, "resource-treasure": 0.04, "human-npc": 0.01, "special-unknown": 0.02 },
};

const F1_TERRAIN_TYPE_NAMES: Record<number, string> = {
  1: "Ground",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
  8: "Snow soil",
  9: "Desert soil",
  10: "Volcano soil",
  11: "Rocky soil",
  12: "Swamp soil",
  13: "Grassland soil",
  14: "End",
};

function UnverifiedRuntimeWorldGridTestPage({ publicMode = false }: RuntimeWorldGridTestPageProps = {}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const [camera, setCamera] = useState<Camera>({ offsetX: 20, offsetY: 20, zoom: 1 });
  const [dragging, setDragging] = useState(false);
  const [mapData, setMapData] = useState<ParsedMapBinary | null>(null);
  const [activeField, setActiveField] = useState<FieldKey>("f0");
  const [compareMode, setCompareMode] = useState(false);
  const [compareField, setCompareField] = useState<FieldKey>("f1");
  const [displayMode, setDisplayMode] = useState<NumberDisplayMode>("dec");
  const [hoverCell, setHoverCell] = useState<HoverCell | null>(null);
  const [pinnedCell, setPinnedCell] = useState<HoverCell | null>(null);
  const [isolatedValue, setIsolatedValue] = useState<number | null>(null);
  const [terrainRows, setTerrainRows] = useState<TerrainRow[]>([]);
  const [mapChipById, setMapChipById] = useState<Map<number, MapChipRow>>(new Map());
  const [chipImageById, setChipImageById] = useState<Map<number, string>>(new Map());
  const [natureImageById, setNatureImageById] = useState<Map<number, string>>(new Map());
  const [assetDiagnostics, setAssetDiagnostics] = useState<Record<string, AssetDiagnostic>>({});
  const [showNaturePlacement, setShowNaturePlacement] = useState(true);
  const [showTerrainNature, setShowTerrainNature] = useState(true);
  const [showResourceNature, setShowResourceNature] = useState(true);
  const [showHumanNature, setShowHumanNature] = useState(true);
  const [showSpecialNature, setShowSpecialNature] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    async function loadMap() {
      try {
        const [mapResponse, terrainResponse, mapChipResponse, chipImgResponse, natureImgResponse] = await Promise.all([
          fetch(MAP_SECTION_A_PATH),
          fetch(TERRAIN_PATH),
          fetch(MAP_CHIP_PATH),
          fetch(CHIP_IMG_INF_PATH),
          fetch(NATURE_IMG_INF_PATH),
        ]);
        if (!mapResponse.ok) {
          throw new Error(`HTTP ${mapResponse.status} while loading ${MAP_SECTION_A_PATH}`);
        }
        if (!terrainResponse.ok) {
          throw new Error(`HTTP ${terrainResponse.status} while loading ${TERRAIN_PATH}`);
        }
        if (!mapChipResponse.ok) {
          throw new Error(`HTTP ${mapChipResponse.status} while loading ${MAP_CHIP_PATH}`);
        }
        if (!chipImgResponse.ok) {
          throw new Error(`HTTP ${chipImgResponse.status} while loading ${CHIP_IMG_INF_PATH}`);
        }
        if (!natureImgResponse.ok) {
          throw new Error(`HTTP ${natureImgResponse.status} while loading ${NATURE_IMG_INF_PATH}`);
        }
        const [arrayBuffer, terrainText, mapChipText, chipImgText, natureImgText] = await Promise.all([
          mapResponse.arrayBuffer(),
          terrainResponse.text(),
          mapChipResponse.text(),
          chipImgResponse.text(),
          natureImgResponse.text(),
        ]);
        const parsed = parseMapBinarySectionA(arrayBuffer);

        if (disposed) {
          return;
        }

        setMapData(parsed);
        setTerrainRows(parseTerrainRows(terrainText));
        setMapChipById(new Map(parseMapChipRows(mapChipText).map((row) => [row.id, row])));
        setChipImageById(parseInfTable(chipImgText));
        setNatureImageById(parseInfTable(natureImgText));
        setAssetDiagnostics({
          [MAP_SECTION_A_PATH]: {
            url: MAP_SECTION_A_PATH,
            status: mapResponse.status,
            contentType: mapResponse.headers.get("content-type"),
            snippet: "",
          },
          [TERRAIN_PATH]: {
            url: TERRAIN_PATH,
            status: terrainResponse.status,
            contentType: terrainResponse.headers.get("content-type"),
            snippet: terrainText.slice(0, 100),
          },
          [MAP_CHIP_PATH]: {
            url: MAP_CHIP_PATH,
            status: mapChipResponse.status,
            contentType: mapChipResponse.headers.get("content-type"),
            snippet: mapChipText.slice(0, 100),
          },
          [CHIP_IMG_INF_PATH]: {
            url: CHIP_IMG_INF_PATH,
            status: chipImgResponse.status,
            contentType: chipImgResponse.headers.get("content-type"),
            snippet: chipImgText.slice(0, 100),
          },
          [NATURE_IMG_INF_PATH]: {
            url: NATURE_IMG_INF_PATH,
            status: natureImgResponse.status,
            contentType: natureImgResponse.headers.get("content-type"),
            snippet: natureImgText.slice(0, 100),
          },
        });
        setError(null);
      } catch (loadError) {
        if (disposed) {
          return;
        }
        const message = loadError instanceof Error ? loadError.message : String(loadError);
        setError(message);
      }
    }

    loadMap();
    return () => {
      disposed = true;
    };
  }, []);

  const hoveredCellData = useMemo(() => getCellAt(mapData, hoverCell), [hoverCell, mapData]);
  const pinnedCellData = useMemo(() => getCellAt(mapData, pinnedCell), [mapData, pinnedCell]);
  const natureCells = useMemo(() => buildNatureCells(mapData, terrainRows, natureImageById), [mapData, natureImageById, terrainRows]);
  const natureCategoryVisibility = useMemo<NatureCategoryVisibility>(() => ({
    "terrain-nature": showTerrainNature,
    "resource-treasure": showResourceNature,
    "human-npc": showHumanNature,
    "special-unknown": showSpecialNature,
  }), [showHumanNature, showResourceNature, showSpecialNature, showTerrainNature]);
  const visibleNatureCells = useMemo(
    () => natureCells.filter((cell) => natureCategoryVisibility[cell.category]),
    [natureCategoryVisibility, natureCells],
  );
  const natureCellsByKey = useMemo(() => {
    const byKey = new Map<string, NatureCell[]>();
    for (const cell of visibleNatureCells) {
      const key = `${cell.x},${cell.y}`;
      const existing = byKey.get(key);
      if (existing) existing.push(cell);
      else byKey.set(key, [cell]);
    }
    return byKey;
  }, [visibleNatureCells]);
  const hoveredNatureCells = hoverCell ? natureCellsByKey.get(`${hoverCell.x},${hoverCell.y}`) ?? [] : [];
  const pinnedNatureCells = pinnedCell ? natureCellsByKey.get(`${pinnedCell.x},${pinnedCell.y}`) ?? [] : [];
  const pinnedTerrainRow = useMemo(() => {
    if (!pinnedCellData) return null;
    return terrainRows.find((row) => row.type === pinnedCellData.fields.f1) ?? null;
  }, [pinnedCellData, terrainRows]);
  const pinnedTerrainImgFilename = useMemo(() => {
    if (!pinnedTerrainRow) return null;
    return chipImageById.get(pinnedTerrainRow.img) ?? null;
  }, [chipImageById, pinnedTerrainRow]);
  const pinnedMapChipRow = useMemo(() => {
    if (!pinnedCellData) return null;
    return mapChipById.get(pinnedCellData.fields.f5) ?? null;
  }, [mapChipById, pinnedCellData]);
  const pinnedImageFilename = useMemo(() => {
    if (!pinnedCellData) return null;
    return resolveImageFilename(pinnedCellData.fields.f2);
  }, [chipImageById, mapChipById, terrainRows, pinnedCellData]);

  function getF1TerrainTypeName(f1Value: number): string {
    return F1_TERRAIN_TYPE_NAMES[f1Value] ?? `Unknown (${f1Value})`;
  }

  function resolveImageFilename(imageId: number): string | null {
    const directFilename = chipImageById.get(imageId);
    if (directFilename) {
      return directFilename;
    }

    const mapChipRow = mapChipById.get(imageId);
    if (mapChipRow) {
      const mapChipFilename = chipImageById.get(mapChipRow.img);
      if (mapChipFilename) {
        return mapChipFilename;
      }
      return mapChipRow.name || null;
    }

    const terrainRow = terrainRows.find((row) => row.img === imageId || row.id === imageId);
    if (terrainRow) {
      const terrainFilename = chipImageById.get(terrainRow.img);
      if (terrainFilename) {
        return terrainFilename;
      }
      return terrainRow.name || null;
    }

    return null;
  }

  const topValues = useMemo(() => {
    if (!mapData) {
      return [] as Array<{ value: number; count: number }>;
    }

    const counts = new Map<number, number>();
    for (const cell of mapData.cells) {
      const value = cell.fields[activeField];
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }

    return [...counts.entries()]
      .map(([value, count]) => ({ value, count }))
      .sort((left, right) => {
        if (right.count !== left.count) {
          return right.count - left.count;
        }
        return left.value - right.value;
      })
      .slice(0, 16);
  }, [activeField, mapData]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) {
      return;
    }

    const width = container.clientWidth;
    const height = container.clientHeight;
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    context.clearRect(0, 0, width, height);
    context.fillStyle = "#09101a";
    context.fillRect(0, 0, width, height);

    if (!mapData) {
      return;
    }

    const tileSize = BASE_TILE_SIZE * camera.zoom;
    for (const cell of mapData.cells) {
      const screenX = camera.offsetX + cell.x * tileSize;
      const screenY = camera.offsetY + cell.y * tileSize;

      if (screenX + tileSize < 0 || screenY + tileSize < 0 || screenX > width || screenY > height) {
        continue;
      }

      const activeValue = cell.fields[activeField];
      const isIsolatedOut = isolatedValue !== null && activeValue !== isolatedValue;
      const baseColor = compareMode
        ? colorFromComparison(cell.fields[activeField], cell.fields[compareField])
        : colorFromValue(activeValue);

      context.fillStyle = isIsolatedOut ? "rgba(15,23,42,0.3)" : baseColor;
      context.fillRect(screenX, screenY, tileSize, tileSize);
    }

    if (hoveredCellData) {
      context.strokeStyle = "rgba(255,255,255,0.95)";
      context.lineWidth = 1;
      context.strokeRect(
        camera.offsetX + hoveredCellData.x * tileSize,
        camera.offsetY + hoveredCellData.y * tileSize,
        tileSize,
        tileSize,
      );
    }

    if (pinnedCellData) {
      context.strokeStyle = "rgba(250,204,21,0.95)";
      context.lineWidth = 1;
      context.strokeRect(
        camera.offsetX + pinnedCellData.x * tileSize,
        camera.offsetY + pinnedCellData.y * tileSize,
        tileSize,
        tileSize,
      );
    }

    if (showNaturePlacement) {
      context.strokeStyle = "rgba(255,255,255,0.72)";
      context.lineWidth = 1;
      for (const nature of visibleNatureCells) {
        const screenX = camera.offsetX + nature.x * tileSize;
        const screenY = camera.offsetY + nature.y * tileSize;
        if (screenX + tileSize < 0 || screenY + tileSize < 0 || screenX > width || screenY > height) {
          continue;
        }
        context.fillStyle = colorForNatureCategory(nature.category);
        const radius = Math.max(1.5, Math.min(5, tileSize * 0.35));
        context.beginPath();
        context.arc(screenX + tileSize / 2, screenY + tileSize / 2, radius, 0, Math.PI * 2);
        context.fill();
        if (tileSize >= 7) {
          context.stroke();
        }
      }
    }
  }, [activeField, camera, compareField, compareMode, hoveredCellData, isolatedValue, mapData, pinnedCellData, showNaturePlacement, visibleNatureCells]);

  function onPointerDown() {
    setDragging(true);
  }

  function onPointerUp() {
    setDragging(false);
  }

  function onPointerLeave() {
    setDragging(false);
    setHoverCell(null);
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    setHoverCell(getCellFromPointerEvent(event, camera, mapData, canvasRef.current));

    if (!dragging) {
      return;
    }

    setCamera((previous) => ({
      ...previous,
      offsetX: previous.offsetX + event.movementX,
      offsetY: previous.offsetY + event.movementY,
    }));
  }

  function onWheel(event: React.WheelEvent<HTMLCanvasElement>) {
    if (!event.ctrlKey && !event.metaKey) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    const zoomFactor = event.deltaY < 0 ? 1.1 : 0.9;
    setCamera((previous) => ({
      ...previous,
      zoom: Math.max(0.25, Math.min(4, previous.zoom * zoomFactor)),
    }));
  }

  function onCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const point = getCellFromMouseEvent(event, camera, mapData, canvasRef.current);
    if (!point) {
      setPinnedCell(null);
      return;
    }

    setPinnedCell((previous) => {
      if (previous && previous.x === point.x && previous.y === point.y) {
        return null;
      }
      return point;
    });
  }

  return (
    <div className={publicMode ? "mx-auto max-w-[1500px]" : "mx-auto max-w-[1500px] p-4"}>
      {!publicMode && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="text-lg font-semibold">Runtime World Grid Test (Top View F0-F5)</h1>
            <div className="flex items-center gap-2 text-xs">
              <a href="/runtime-world-grid-test" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open top-view grid test
              </a>
              <a href="/runtime-world-render-test" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open isometric render test
              </a>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            Raw map field inspector for f0-f5 with top-view navigation and value comparison. Use Ctrl+wheel to zoom; regular wheel scrolls the page.
            <br />
            Click a tile to pin it and see its debug values: terrain f1, image id f2, and map chip id f5.
          </p>
        </>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 rounded border border-border bg-card p-3 text-xs">
        <label className="flex items-center gap-2">
          active field
          <select
            value={activeField}
            onChange={(event) => setActiveField(event.target.value as FieldKey)}
            className="rounded border border-border bg-background px-2 py-1"
          >
            {(publicMode ? FIELD_KEYS.filter((fieldKey) => fieldKey !== "f2") : FIELD_KEYS).map((fieldKey) => (
              <option key={fieldKey} value={fieldKey}>
                {fieldKey}
              </option>
            ))}
          </select>
        </label>

        {!publicMode && (
          <label className="flex items-center gap-2">
            display mode
            <select
              value={displayMode}
              onChange={(event) => setDisplayMode(event.target.value as NumberDisplayMode)}
              className="rounded border border-border bg-background px-2 py-1"
            >
              <option value="dec">decimal</option>
              <option value="hex">hex</option>
            </select>
          </label>
        )}

        <label className="flex items-center gap-2">
          <input type="checkbox" checked={compareMode} onChange={(event) => setCompareMode(event.target.checked)} />
          compare mode
        </label>

        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showNaturePlacement} onChange={(event) => setShowNaturePlacement(event.target.checked)} />
          nature placement
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showTerrainNature} onChange={(event) => setShowTerrainNature(event.target.checked)} />
          terrain nature
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showResourceNature} onChange={(event) => setShowResourceNature(event.target.checked)} />
          resources
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showHumanNature} onChange={(event) => setShowHumanNature(event.target.checked)} />
          humans
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showSpecialNature} onChange={(event) => setShowSpecialNature(event.target.checked)} />
          special
        </label>

        <label className="flex items-center gap-2">
          compare with
          <select
            value={compareField}
            onChange={(event) => setCompareField(event.target.value as FieldKey)}
            disabled={!compareMode}
            className="rounded border border-border bg-background px-2 py-1 disabled:opacity-60"
          >
            {(publicMode ? FIELD_KEYS.filter((fieldKey) => fieldKey !== "f2") : FIELD_KEYS).map((fieldKey) => (
              <option key={fieldKey} value={fieldKey}>
                {fieldKey}
              </option>
            ))}
          </select>
        </label>

        {!publicMode && (
          <>
            <button
              type="button"
              onClick={() => {
                setActiveField("f0");
                setCompareField("f3");
                setCompareMode(true);
              }}
              className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
            >
              nature id/group
            </button>

            <button
              type="button"
              onClick={() => {
                setActiveField("f5");
                setCompareField("f0");
                setCompareMode(true);
              }}
              className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
            >
              nature frame
            </button>

            <button
              type="button"
              onClick={() => {
                setActiveField("f2");
                setCompareMode(false);
              }}
              className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
            >
              mapchip f2
            </button>
          </>
        )}
      </div>

      {error && <div className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-100">{error}</div>}

      <div ref={containerRef} className="mt-3 h-[74vh] overflow-hidden rounded border border-border bg-black">
        <canvas
          ref={canvasRef}
          className="h-full w-full touch-none cursor-grab"
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerLeave}
          onPointerMove={onPointerMove}
          onWheel={onWheel}
          onClick={onCanvasClick}
          aria-label="Runtime grid renderer canvas"
        />
      </div>

      {!publicMode && <div className="mt-3 grid gap-3 text-xs lg:grid-cols-3">
        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Map Stats</div>
          {mapData ? (
            <>
              <div className="mt-2 grid gap-1">
                <div>map: {mapData.width} x {mapData.height}</div>
                <div>cells: {mapData.cells.length}</div>
                <div>nature cells: {natureCells.length}</div>
                <div>camera: ({Math.round(camera.offsetX)}, {Math.round(camera.offsetY)})</div>
                <div>zoom: {camera.zoom.toFixed(2)}x</div>
                <div>isolation: {isolatedValue === null ? "none" : formatValue(isolatedValue, displayMode)}</div>
              </div>
              <div className="mt-3 space-y-1 text-[0.75rem] text-muted-foreground">
                <div className="font-medium">Asset diagnostics</div>
                {Object.values(assetDiagnostics).map((diagnostic) => (
                  <div key={diagnostic.url}>
                    <div>{diagnostic.url}: {diagnostic.status}{diagnostic.contentType ? ` (${diagnostic.contentType})` : ""}</div>
                    {diagnostic.snippet ? <div className="whitespace-pre-wrap">preview: {diagnostic.snippet}</div> : null}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="mt-2 text-muted-foreground">Loading map...</div>
          )}
        </div>

        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Hovered Cell</div>
          {hoveredCellData ? (
            <div className="mt-2 grid gap-1">
              <div>
                x/y: {hoveredCellData.x}, {hoveredCellData.y}
              </div>
              {FIELD_KEYS.map((fieldKey) => (
                <div key={`hover-${fieldKey}`}>
                  {fieldKey}: {formatValue(hoveredCellData.fields[fieldKey], displayMode)}
                </div>
              ))}
              <div>nature: {hoveredNatureCells.length > 0 ? hoveredNatureCells.map(formatNatureCellSummary).join(" | ") : "none"}</div>
            </div>
          ) : (
            <div className="mt-2 text-muted-foreground">Move cursor over a tile to inspect fields.</div>
          )}
        </div>

        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Pinned Cell</div>
          {pinnedCellData ? (
            <div className="mt-2 grid gap-1">
              <div>
                x/y: {pinnedCellData.x}, {pinnedCellData.y}
              </div>
              {FIELD_KEYS.map((fieldKey) => (
                <div key={`pin-${fieldKey}`}>
                  {fieldKey}: {formatValue(pinnedCellData.fields[fieldKey], displayMode)}
                </div>
              ))}
              <div>
                Terrain f1: Type {formatValue(pinnedCellData.fields.f1, displayMode)}, {getF1TerrainTypeName(pinnedCellData.fields.f1)}
              </div>
              <div>
                Img id f2: {formatValue(pinnedCellData.fields.f2, displayMode)}
                {pinnedImageFilename ? `, ${pinnedImageFilename}` : ", unknown png"}
              </div>
              <div className="text-[0.7rem] text-muted-foreground">
                img table: {chipImageById.size} entries, mapChip table: {mapChipById.size} entries
              </div>
              <div className="text-[0.7rem] text-muted-foreground">
                raw f2 direct: {chipImageById.get(pinnedCellData.fields.f2) ?? "none"}, mapChip row: {mapChipById.has(pinnedCellData.fields.f2) ? "yes" : "no"}
              </div>
              <div className="text-[0.7rem] text-muted-foreground">
                mapChip row img id: {mapChipById.get(pinnedCellData.fields.f2)?.img ?? "none"}, mapChip img filename: {mapChipById.has(pinnedCellData.fields.f2) ? chipImageById.get(mapChipById.get(pinnedCellData.fields.f2)!.img) ?? "none" : "n/a"}
              </div>
              <div>
                F5: map chip id {formatValue(pinnedCellData.fields.f5, displayMode)}
              </div>
              <div>nature: {pinnedNatureCells.length > 0 ? pinnedNatureCells.map(formatNatureCellSummary).join(" | ") : "none"}</div>
            </div>
          ) : (
            <div className="mt-2 text-muted-foreground">Click a tile to pin it.</div>
          )}
        </div>

        <div className="rounded border border-border bg-card p-3 lg:col-span-3">
          <div className="font-medium">Top Values ({activeField})</div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {topValues.map((entry) => (
              <button
                key={entry.value}
                type="button"
                onClick={() => setIsolatedValue((previous) => (previous === entry.value ? null : entry.value))}
                className={
                  "flex items-center justify-between rounded border border-border px-2 py-1 text-left " +
                  (isolatedValue === entry.value ? "bg-primary/20" : "bg-background hover:bg-muted")
                }
              >
                <span>{formatValue(entry.value, displayMode)}</span>
                <span className="text-muted-foreground">{entry.count}</span>
              </button>
            ))}
          </div>
        </div>
      </div>}
    </div>
  );
}

function getCellAt(parsedMap: ParsedMapBinary | null, point: HoverCell | null) {
  if (!parsedMap || !point) {
    return null;
  }

  if (point.x < 0 || point.x >= parsedMap.width || point.y < 0 || point.y >= parsedMap.height) {
    return null;
  }

  const index = point.y * parsedMap.width + point.x;
  return parsedMap.cells[index] ?? null;
}

function parseMapChipRows(text: string): MapChipRow[] {
  const rows: MapChipRow[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) continue;

    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0) continue;

    rows.push({
      id,
      type: asInt(parts[1], 0),
      category: asInt(parts[2], 0),
      name: parts[8] ?? "",
      res: asInt(parts[9], 0),
      img: asInt(parts[10], -1),
      seb: asInt(parts[11], -1),
      frame: asInt(parts[12], 0),
      relatedDataType: asInt(parts[15], 0),
      relatedDataId: asInt(parts[16], -1),
      field17: asInt(parts[17], 0),
      field18: asInt(parts[18], 0),
      field19: asInt(parts[19], 0),
      layer: asInt(parts[20], 0),
      field21: asInt(parts[21], 0),
      sizeWidth: asInt(parts[22], 1),
      sizeHeight: asInt(parts[23], 1),
      field24: asInt(parts[24], 0),
      field25: asInt(parts[25], 0),
      field26: asInt(parts[26], 0),
      field27: asInt(parts[27], 0),
    });
  }
  return rows;
}

function parseTerrainRows(text: string): TerrainRow[] {
  const rows: TerrainRow[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) continue;

    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0) continue;

    rows.push({
      id,
      type: asInt(parts[1], 0),
      category: asInt(parts[2], 0),
      name: parts[4] ?? "",
      res: asInt(parts[5], -1),
      img: asInt(parts[6], -1),
      frame: asInt(parts[8], 0),
      natureId: asInt(parts[9], -1),
      natureGroupId: asInt(parts[10], -1),
    });
  }
  return rows;
}

function buildNatureCells(parsedMap: ParsedMapBinary | null, terrainRows: TerrainRow[], natureImageById: Map<number, string>): NatureCell[] {
  if (!parsedMap || terrainRows.length === 0) return [];

  const terrainByType = groupRowsByKey(terrainRows, (row) => row.type);
  const cells: NatureCell[] = [];

  for (const cell of parsedMap.cells) {
    const selected = selectOneNatureRowForTile(terrainByType.get(cell.fields.f1) ?? [], cell.x, cell.y, cell.fields, natureImageById);
    for (const item of selected) {
      cells.push({
        x: cell.x,
        y: cell.y,
        rowId: item.row.id,
        name: item.row.name,
        img: item.row.img,
        frame: item.row.frame,
        natureId: item.row.natureId,
        natureGroupId: item.row.natureGroupId,
        category: item.category,
        score: item.score,
        reason: item.reason,
      });
    }
  }

  return cells;
}

function parseInfTable(text: string): Map<number, string> {
  const result = new Map<number, string>();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) continue;
    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    const token = parts[1]?.split(",")[0]?.trim();
    if (id >= 0 && token) {
      result.set(id, token);
    }
  }
  return result;
}

function classifyNatureVisualCategory(row: TerrainRow, natureImageById: Map<number, string>): NatureVisualCategory {
  const filename = natureImageById.get(row.img) ?? "";
  const token = `${row.name} ${filename}`.toLowerCase();
  if (/human_|miner|person|npc|people/.test(token)) return "human-npc";
  if (/special_(01|03|04|07|10)\.png/.test(token)) return "resource-treasure";
  if (/special_(00|02|08|09|11)\.png/.test(token)) return "terrain-nature";
  if (/gem|crystal|ore|chest|treasure|material|resource/.test(token)) return "resource-treasure";
  if (/special_/.test(token)) return "special-unknown";
  return "terrain-nature";
}

function selectOneNatureRowForTile(
  rowsForTerrainType: TerrainRow[],
  cellX: number,
  cellY: number,
  fields: RawMapCellFields,
  natureImageById: Map<number, string>,
): Array<{ row: TerrainRow; score: number; reason: string; category: NatureVisualCategory }> {
  const natureRows = rowsForTerrainType.filter((row) => row.res === 20 && row.type === fields.f1);
  if (natureRows.length === 0) return [];

  const categoryPools = new Map<NatureVisualCategory, TerrainRow[]>();
  for (const row of natureRows) {
    const category = classifyNatureVisualCategory(row, natureImageById);
    const existing = categoryPools.get(category);
    if (existing) existing.push(row);
    else categoryPools.set(category, [row]);
  }

  const chances = NATURE_CATEGORY_CHANCE_BY_TERRAIN_TYPE[fields.f1] ?? {
    "terrain-nature": 0.12,
    "resource-treasure": 0.03,
    "human-npc": 0.01,
    "special-unknown": 0.01,
  };
  const orderedCategories = Object.keys(NATURE_CATEGORY_LAYER_ORDER) as NatureVisualCategory[];
  const roll = stableUnitHash(cellX, cellY, fields.f1, 101);
  let threshold = 0;
  let selectedCategory: NatureVisualCategory | null = null;

  for (const category of orderedCategories) {
    if (!categoryPools.has(category)) continue;
    threshold += chances[category] ?? 0;
    if (roll < threshold) {
      selectedCategory = category;
      break;
    }
  }

  if (!selectedCategory) return [];

  const candidates = [...(categoryPools.get(selectedCategory) ?? [])].sort((left, right) => left.id - right.id || left.img - right.img);
  if (candidates.length === 0) return [];

  const index = Math.floor(stableUnitHash(cellX, cellY, fields.f1, 211) * candidates.length) % candidates.length;
  const row = candidates[index];
  if (!row) return [];

  return [{
    row,
    score: 1000 - index,
    reason: `one visual per tile: f1=${fields.f1} ${selectedCategory} chance=${Math.round((chances[selectedCategory] ?? 0) * 100)}%, row=${row.id}, pool=${candidates.length}`,
    category: selectedCategory,
  }];
}

function stableUnitHash(cellX: number, cellY: number, terrainType: number, salt: number): number {
  let hash = (cellX * 374761393 + cellY * 668265263 + terrainType * 2246822519 + salt * 3266489917) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 15), 2246822507) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 13), 3266489909) >>> 0;
  return ((hash ^ (hash >>> 16)) >>> 0) / 4294967296;
}

function colorForNatureCategory(category: NatureVisualCategory): string {
  if (category === "resource-treasure") return "rgba(251,191,36,0.88)";
  if (category === "human-npc") return "rgba(96,165,250,0.88)";
  if (category === "special-unknown") return "rgba(216,180,254,0.86)";
  return "rgba(34,197,94,0.82)";
}

function groupRowsByKey<T, K>(rows: T[], keyFn: (row: T) => K): Map<K, T[]> {
  const grouped = new Map<K, T[]>();
  for (const row of rows) {
    const key = keyFn(row);
    const existing = grouped.get(key);
    if (existing) existing.push(row);
    else grouped.set(key, [row]);
  }
  return grouped;
}

function formatNatureCellSummary(cell: NatureCell): string {
  return `${cell.rowId}:${cell.category}:${cell.name || "nature"} img=${cell.img} frame=${cell.frame} n=${cell.natureId}/${cell.natureGroupId} score=${cell.score}`;
}

function getCellFromPointerEvent(
  event: React.PointerEvent<HTMLCanvasElement>,
  camera: Camera,
  parsedMap: ParsedMapBinary | null,
  canvas: HTMLCanvasElement | null,
): HoverCell | null {
  if (!canvas || !parsedMap) {
    return null;
  }

  const rect = canvas.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  const tileSize = BASE_TILE_SIZE * camera.zoom;
  const mapX = Math.floor((localX - camera.offsetX) / tileSize);
  const mapY = Math.floor((localY - camera.offsetY) / tileSize);

  if (mapX < 0 || mapX >= parsedMap.width || mapY < 0 || mapY >= parsedMap.height) {
    return null;
  }

  return { x: mapX, y: mapY };
}

function getCellFromMouseEvent(
  event: React.MouseEvent<HTMLCanvasElement>,
  camera: Camera,
  parsedMap: ParsedMapBinary | null,
  canvas: HTMLCanvasElement | null,
): HoverCell | null {
  if (!canvas || !parsedMap) {
    return null;
  }

  const rect = canvas.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  const tileSize = BASE_TILE_SIZE * camera.zoom;
  const mapX = Math.floor((localX - camera.offsetX) / tileSize);
  const mapY = Math.floor((localY - camera.offsetY) / tileSize);

  if (mapX < 0 || mapX >= parsedMap.width || mapY < 0 || mapY >= parsedMap.height) {
    return null;
  }

  return { x: mapX, y: mapY };
}

function colorFromValue(value: number): string {
  if (value === 0) {
    return "#0f172a";
  }

  const mixed = (value * 2654435761) >>> 0;
  const hue = mixed % 360;
  const saturation = 55 + ((mixed >>> 8) % 30);
  const lightness = 34 + ((mixed >>> 16) % 28);
  return `hsl(${hue} ${saturation}% ${lightness}%)`;
}

function colorFromComparison(left: number, right: number): string {
  if (left === right) {
    if (left === 0) {
      return "#1e293b";
    }
    return "#16a34a";
  }
  return "#be123c";
}

function formatValue(value: number, mode: NumberDisplayMode): string {
  if (mode === "hex") {
    return `0x${value.toString(16).padStart(8, "0")}`;
  }
  return String(value);
}

function asInt(value: string | undefined, fallback: number): number {
  if (value == null || value === "") return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export default function RuntimeWorldGridTestPage(props: RuntimeWorldGridTestPageProps = {}) {
  return <UnverifiedRuntimeWorldGridTestPage {...props} />;
}
