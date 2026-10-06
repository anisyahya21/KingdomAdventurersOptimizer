import { ResearchPreviewGate } from "@/components/ka/research-preview-gate";
import { useEffect, useMemo, useRef, useState } from "react";
import { parseMapBinarySectionA } from "@/runtime/world-builder/map-loader";
import type { ParsedMapCell } from "@/runtime/world-builder/types";

type TerrainRow = {
  id: number;
  type: number;
  category: number;
  dataId: number;
  name: string;
  res: number;
  img: number;
  seb: number;
  frame: number;
  natureId: number;
  natureGroupId: number;
};

type OptSlot = {
  u: number;
  v: number;
  destX: number;
  destY: number;
  srcX: number;
  srcY: number;
  width: number;
  height: number;
  empty: boolean;
};

type OptMetadata = {
  cellWidth: number;
  cellHeight: number;
  cols: number;
  rows: number;
  slots: OptSlot[];
};

type SebRecord = {
  frameIndex: number;
  tick: number;
  sourceId: number;
  srcX: number;
  srcY: number;
  width: number;
  height: number;
  offsetX: number;
  offsetY: number;
};

type SebBlock = {
  blockIndex: number;
  period: number;
  records: SebRecord[];
};

type SebFile = {
  blockCount: number;
  headerValue: number;
  blocks: SebBlock[];
};

type AnchorMode = "bottom-center" | "center" | "top-left";
type DrawOrder = "base-first" | "overlay-first";
type SourceMode = "auto-rule" | "full-image" | "opt-rect";

type SourceRect = {
  srcX: number;
  srcY: number;
  srcW: number;
  srcH: number;
};

type LoadedImage = {
  image: HTMLImageElement;
  filename: string;
};

type MatchField = "f0" | "f1" | "f2" | "f3" | "f4" | "f5";

type MapMatchedPair = {
  cell: ParsedMapCell;
  baseRow: TerrainRow;
  baseReason: string;
  overlayRow: TerrainRow;
  overlayField: MatchField;
};

type CompositionGuess = {
  key: string;
  label: string;
  reason: string;
  anchorMode: AnchorMode;
  drawOrder: DrawOrder;
  baseSourceMode: SourceMode;
  overlaySourceMode: SourceMode;
  offsetX: number;
  offsetY: number;
};

type NaturePlacementRule = {
  anchorMode: AnchorMode;
  drawOrder: DrawOrder;
  overlaySourceMode: SourceMode;
  offsetX: number;
  offsetY: number;
  reason: string;
};

type OptInfoHint = {
  x: number;
  y: number;
  sourceLine: string;
};

type NatureAssetMetadata = {
  filename: string;
  imageWidth: number;
  imageHeight: number;
  opt: OptMetadata | null;
  optInfo: string | null;
  optHints: OptInfoHint[];
};

type AutoNatureAssetRule = {
  rowId: number;
  imgId: number;
  filename: string;
  sourceRect: SourceRect;
  sourceKind: "SEB" | "OPT" | "OPTINFO" | "BOTTOM_STATE_FALLBACK" | "FULL_IMAGE_FALLBACK";
  sourceSummary: string;
  containsMultipleStates: boolean;
  chosenState: string;
  offsetX: number;
  offsetY: number;
  offsetSource: "SEB" | "OPTINFO" | "OPT_BOUNDS" | "NONE";
  anchorMode: AnchorMode;
  drawOrder: DrawOrder;
  overlaySourceMode: SourceMode;
  drawWidth: number;
  drawHeight: number;
  reason: string;
};

type SourceEvidence = {
  seb: { available: boolean; file?: string; frame?: number; x?: number; y?: number; note: string };
  optInfo: { available: boolean; x?: number; y?: number; line?: string; note: string };
  optBounds: { available: boolean; x?: number; y?: number; minX?: number; maxX?: number; maxY?: number; note: string };
};

const TERRAIN_PATH = "/world-assets/xls/English.lproj/Terrain.txt";
const CHIP_IMG_INF_PATH = "/world-assets/chip/img.inf";
const NATURE_IMG_INF_PATH = "/world-assets/nature/img.inf";
const NATURE_SEB_INF_PATH = "/world-assets/nature/seb.inf";
const MAP_PATH = "/world-assets/map/map_160_160.bin";
const NATURE_RULES_STORAGE_KEY = "ka-lab-nature-placement-rules-v1";
const TERRAIN_TYPE_NAMES: Record<number, string> = {
  1: "ground",
  2: "grass",
  3: "sand",
  4: "rock",
  5: "volcano",
  6: "snow",
  7: "swamp",
  8: "snow soil",
  9: "desert soil",
  10: "volcano soil",
  11: "rocky soil",
  12: "swamp soil",
  13: "grassland soil",
};

function UnverifiedTerrainCompositionLabPage() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const [terrainRows, setTerrainRows] = useState<TerrainRow[]>([]);
  const [mapCells, setMapCells] = useState<ParsedMapCell[]>([]);
  const [imageById, setImageById] = useState<Map<number, string>>(new Map());
  const [natureImageById, setNatureImageById] = useState<Map<number, string>>(new Map());
  const [natureSebById, setNatureSebById] = useState<Map<number, string>>(new Map());
  const [natureSebFiles, setNatureSebFiles] = useState<Map<string, SebFile>>(new Map());

  const [selectedType, setSelectedType] = useState<number>(3);
  const [selectedBaseId, setSelectedBaseId] = useState<number | null>(null);
  const [selectedOverlayId, setSelectedOverlayId] = useState<number | null>(null);

  const [overlayOffsetX, setOverlayOffsetX] = useState<number>(0);
  const [overlayOffsetY, setOverlayOffsetY] = useState<number>(0);
  const [drawScale, setDrawScale] = useState<number>(2);
  const [anchorMode, setAnchorMode] = useState<AnchorMode>("bottom-center");
  const [drawOrder, setDrawOrder] = useState<DrawOrder>("base-first");
  const [baseSourceMode, setBaseSourceMode] = useState<SourceMode>("full-image");
  const [overlaySourceMode, setOverlaySourceMode] = useState<SourceMode>("auto-rule");

  const [baseImage, setBaseImage] = useState<LoadedImage | null>(null);
  const [overlayImage, setOverlayImage] = useState<LoadedImage | null>(null);
  const [baseOpt, setBaseOpt] = useState<OptMetadata | null>(null);
  const [overlayOpt, setOverlayOpt] = useState<OptMetadata | null>(null);
  const [baseOptInfo, setBaseOptInfo] = useState<string | null>(null);
  const [overlayOptInfo, setOverlayOptInfo] = useState<string | null>(null);
  const [natureMetadataByFilename, setNatureMetadataByFilename] = useState<Map<string, NatureAssetMetadata>>(new Map());
  const [savedNatureRules, setSavedNatureRules] = useState<Record<string, NaturePlacementRule>>({});
  const [autoApplyNatureRule, setAutoApplyNatureRule] = useState<boolean>(true);

  const typeRows = useMemo(() => {
    return terrainRows.filter((row) => row.type === selectedType);
  }, [selectedType, terrainRows]);

  const baseRows = useMemo(() => {
    return typeRows.filter((row) => row.category === 0);
  }, [typeRows]);

  const allNatureRows = useMemo(() => {
    return terrainRows.filter((row) => row.category === 1);
  }, [terrainRows]);

  const overlayRows = useMemo(() => {
    return typeRows
      .filter((row) => row.category === 1)
      .filter((row) => {
        const filename = natureImageById.get(row.img) ?? "";
        if (!filename) {
          return true;
        }
        return natureFilenameMatchesType(filename, selectedType);
      });
  }, [natureImageById, selectedType, typeRows]);

  useEffect(() => {
    let disposed = false;

    async function loadOverlayMetadata() {
      const filenames = [...new Set(allNatureRows
        .map((row) => natureImageById.get(row.img) ?? "")
        .filter((name) => Boolean(name)))];

      const loaded = await Promise.all(filenames.map(async (filename) => {
        const [image, opt, optInfo] = await Promise.all([
          loadImageOptional(filename, "nature"),
          loadOptOptional(filename, "nature"),
          loadOptInfoOptional(filename, "nature"),
        ]);

        return {
          filename,
          metadata: {
            filename,
            imageWidth: image?.width ?? 0,
            imageHeight: image?.height ?? 0,
            opt,
            optInfo,
            optHints: parseOptInfoHints(optInfo),
          } satisfies NatureAssetMetadata,
        };
      }));

      if (disposed) {
        return;
      }

      setNatureMetadataByFilename(new Map(loaded.map((entry) => [entry.filename, entry.metadata])));
    }

    loadOverlayMetadata().catch((error) => {
      if (!disposed) {
        setLoadingError(error instanceof Error ? error.message : String(error));
      }
    });

    return () => {
      disposed = true;
    };
  }, [allNatureRows, natureImageById]);

  const selectedBaseRow = useMemo(() => {
    return selectedBaseId == null ? null : baseRows.find((row) => row.id === selectedBaseId) ?? null;
  }, [baseRows, selectedBaseId]);

  const selectedOverlayRow = useMemo(() => {
    return selectedOverlayId == null ? null : overlayRows.find((row) => row.id === selectedOverlayId) ?? null;
  }, [overlayRows, selectedOverlayId]);

  const terrainTypes = useMemo(() => {
    return [...new Set(terrainRows.map((row) => row.type))].sort((a, b) => a - b);
  }, [terrainRows]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(NATURE_RULES_STORAGE_KEY);
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw) as Record<string, NaturePlacementRule>;
      if (parsed && typeof parsed === "object") {
        setSavedNatureRules(parsed);
      }
    } catch {
      // ignore local storage parse failures; lab can continue with derived rules.
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(NATURE_RULES_STORAGE_KEY, JSON.stringify(savedNatureRules));
    } catch {
      // ignore storage write failures.
    }
  }, [savedNatureRules]);

  useEffect(() => {
    let disposed = false;

    async function load() {
      try {
        const [terrainText, imgText, natureImgText, natureSebText, mapBuffer] = await Promise.all([
          fetchText(TERRAIN_PATH),
          fetchText(CHIP_IMG_INF_PATH),
          fetchText(NATURE_IMG_INF_PATH),
          fetchText(NATURE_SEB_INF_PATH),
          fetchArrayBufferOptional(MAP_PATH),
        ]);

        const parsedNatureSebById = parseInfTable(natureSebText);
        const sebFilenames = [...parsedNatureSebById.values()]
          .filter((name) => name.toLowerCase().endsWith(".seb"));
        const sebLoaded = await Promise.all(sebFilenames.map(async (filename) => {
          const buffer = await fetchArrayBufferOptional(`/world-assets/nature/${filename}`);
          return { filename, parsed: buffer ? parseSeb(buffer) : null };
        }));

        if (disposed) {
          return;
        }

        setTerrainRows(parseTerrainRows(terrainText));
        setImageById(parseInfTable(imgText));
        setNatureImageById(parseInfTable(natureImgText));
        setNatureSebById(parsedNatureSebById);
        setNatureSebFiles(new Map(sebLoaded.filter((entry) => entry.parsed).map((entry) => [entry.filename, entry.parsed as SebFile])));
        if (mapBuffer) {
          setMapCells(parseMapBinarySectionA(mapBuffer).cells);
        }
        setLoadingError(null);
      } catch (error) {
        if (disposed) {
          return;
        }
        setLoadingError(error instanceof Error ? error.message : String(error));
      }
    }

    load();
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (baseRows.length === 0) {
      setSelectedBaseId(null);
      return;
    }
    if (!selectedBaseRow) {
      setSelectedBaseId(baseRows[0]?.id ?? null);
    }
  }, [baseRows, selectedBaseRow]);

  useEffect(() => {
    if (overlayRows.length === 0) {
      setSelectedOverlayId(null);
      return;
    }
    if (!selectedOverlayRow) {
      setSelectedOverlayId(overlayRows[0]?.id ?? null);
    }
  }, [overlayRows, selectedOverlayRow]);

  useEffect(() => {
    let disposed = false;

    async function loadSelectedAssets() {
      const baseFilename = selectedBaseRow ? imageById.get(selectedBaseRow.img) ?? "" : "";
      const overlayFilename = selectedOverlayRow ? natureImageById.get(selectedOverlayRow.img) ?? "" : "";

      const [baseLoaded, overlayLoaded] = await Promise.all([
        loadImageOptional(baseFilename, "chip"),
        loadImageOptional(overlayFilename, "nature"),
      ]);

      const [baseOptData, overlayOptData, baseOptInfoText, overlayOptInfoText] = await Promise.all([
        loadOptOptional(baseFilename, "chip"),
        loadOptOptional(overlayFilename, "nature"),
        loadOptInfoOptional(baseFilename, "chip"),
        loadOptInfoOptional(overlayFilename, "nature"),
      ]);

      if (disposed) {
        return;
      }

      setBaseImage(baseLoaded ? { image: baseLoaded, filename: baseFilename } : null);
      setOverlayImage(overlayLoaded ? { image: overlayLoaded, filename: overlayFilename } : null);
      setBaseOpt(baseOptData);
      setOverlayOpt(overlayOptData);
      setBaseOptInfo(baseOptInfoText);
      setOverlayOptInfo(overlayOptInfoText);
    }

    loadSelectedAssets().catch((error) => {
      if (!disposed) {
        setLoadingError(error instanceof Error ? error.message : String(error));
      }
    });

    return () => {
      disposed = true;
    };
  }, [imageById, natureImageById, selectedBaseRow, selectedOverlayRow]);

  const selectedAutoRuleForCanvas = useMemo<AutoNatureAssetRule | null>(() => {
    if (!selectedOverlayRow) {
      return null;
    }
    const filename = natureImageById.get(selectedOverlayRow.img) ?? "";
    if (!filename) {
      return null;
    }
    const metadata = natureMetadataByFilename.get(filename);
    if (!metadata) {
      return null;
    }
    return deriveAutoNatureAssetRule(selectedOverlayRow, filename, metadata, natureSebById, natureSebFiles);
  }, [natureImageById, natureMetadataByFilename, natureSebById, natureSebFiles, selectedOverlayRow]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#0b1220";
    context.fillRect(0, 0, width, height);

    const baseRect = getSourceRect(baseImage?.image ?? null, baseOpt, selectedBaseRow?.frame ?? 0, baseSourceMode, null);
    const overlayRect = getSourceRect(
      overlayImage?.image ?? null,
      overlayOpt,
      selectedOverlayRow?.frame ?? 0,
      overlaySourceMode,
      selectedAutoRuleForCanvas?.sourceRect ?? null,
    );

    const basePlacement = computePlacement({
      anchorMode,
      anchorX: Math.round(width * 0.5),
      anchorY: Math.round(height * 0.72),
      sourceRect: baseRect,
      scale: drawScale,
      offsetX: 0,
      offsetY: 0,
    });

    const overlayPlacement = computePlacement({
      anchorMode,
      anchorX: Math.round(width * 0.5),
      anchorY: Math.round(height * 0.72),
      sourceRect: overlayRect,
      scale: drawScale,
      offsetX: overlayOffsetX,
      offsetY: overlayOffsetY,
    });

    const drawBase = () => {
      if (!baseImage || !baseRect) {
        return;
      }
      context.drawImage(
        baseImage.image,
        baseRect.srcX,
        baseRect.srcY,
        baseRect.srcW,
        baseRect.srcH,
        basePlacement.drawX,
        basePlacement.drawY,
        basePlacement.drawW,
        basePlacement.drawH,
      );
      context.strokeStyle = "rgba(255,255,255,0.35)";
      context.strokeRect(basePlacement.drawX + 0.5, basePlacement.drawY + 0.5, basePlacement.drawW - 1, basePlacement.drawH - 1);
    };

    const drawOverlay = () => {
      if (!overlayImage || !overlayRect) {
        return;
      }
      context.drawImage(
        overlayImage.image,
        overlayRect.srcX,
        overlayRect.srcY,
        overlayRect.srcW,
        overlayRect.srcH,
        overlayPlacement.drawX,
        overlayPlacement.drawY,
        overlayPlacement.drawW,
        overlayPlacement.drawH,
      );
      context.strokeStyle = "rgba(255, 80, 80, 0.95)";
      context.strokeRect(overlayPlacement.drawX + 0.5, overlayPlacement.drawY + 0.5, overlayPlacement.drawW - 1, overlayPlacement.drawH - 1);
    };

    if (drawOrder === "base-first") {
      drawBase();
      drawOverlay();
    } else {
      drawOverlay();
      drawBase();
    }

    context.beginPath();
    context.fillStyle = "rgba(56, 189, 248, 0.95)";
    context.arc(Math.round(width * 0.5), Math.round(height * 0.72), 3, 0, Math.PI * 2);
    context.fill();
  }, [
    anchorMode,
    baseImage,
    baseOpt,
    baseSourceMode,
    drawOrder,
    drawScale,
    overlayImage,
    overlayOffsetX,
    overlayOffsetY,
    overlayOpt,
    overlaySourceMode,
    selectedAutoRuleForCanvas,
    selectedBaseRow,
    selectedOverlayRow,
  ]);

  const baseFilename = selectedBaseRow ? imageById.get(selectedBaseRow.img) ?? "" : "";
  const overlayFilename = selectedOverlayRow ? natureImageById.get(selectedOverlayRow.img) ?? "" : "";

  const matchedPair = useMemo<MapMatchedPair | null>(() => {
    if (mapCells.length === 0 || baseRows.length === 0 || overlayRows.length === 0) {
      return null;
    }
    const overlayByImg = new Map<number, TerrainRow>();
    for (const row of allNatureRows) {
      overlayByImg.set(row.img, row);
    }

    for (const cell of mapCells) {
      if (cell.fields.f1 !== selectedType) {
        continue;
      }
      const basePick = pickBaseRowForMapCell(baseRows, cell);
      if (!basePick) {
        continue;
      }
      const overlayPick = pickNatureRowForMapCell(overlayByImg, cell);
      if (!overlayPick) {
        continue;
      }
      return {
        cell,
        baseRow: basePick.row,
        baseReason: basePick.reason,
        overlayRow: overlayPick.row,
        overlayField: overlayPick.field,
      };
    }
    return null;
  }, [baseRows, mapCells, overlayRows, selectedType]);

  useEffect(() => {
    if (!matchedPair) {
      return;
    }
    setSelectedBaseId((previous) => (previous === matchedPair.baseRow.id ? previous : matchedPair.baseRow.id));
    setSelectedOverlayId((previous) => (previous === matchedPair.overlayRow.id ? previous : matchedPair.overlayRow.id));
  }, [matchedPair]);

  const overlayOptInfoHints = useMemo(() => parseOptInfoHints(overlayOptInfo), [overlayOptInfo]);

  const sourceEvidence = useMemo<SourceEvidence>(() => {
    const evidence: SourceEvidence = {
      seb: { available: false, note: "No SEB record for selected row/frame." },
      optInfo: { available: false, note: "No numeric pair found in .optinfo." },
      optBounds: { available: false, note: "No non-empty slots in .opt." },
    };

    if (selectedOverlayRow && selectedOverlayRow.seb >= 0) {
      const sebName = natureSebById.get(selectedOverlayRow.seb);
      if (sebName) {
        const seb = natureSebFiles.get(sebName);
        const block = seb?.blocks[0];
        const record = block?.records.find((entry) => entry.frameIndex === selectedOverlayRow.frame) ?? block?.records[0] ?? null;
        if (record) {
          evidence.seb = {
            available: true,
            file: sebName,
            frame: selectedOverlayRow.frame,
            x: record.offsetX,
            y: record.offsetY,
            note: `frame ${selectedOverlayRow.frame} from block 0`,
          };
        }
      }
    }

    const hint = overlayOptInfoHints[0] ?? null;
    if (hint) {
      evidence.optInfo = {
        available: true,
        x: hint.x,
        y: hint.y,
        line: hint.sourceLine,
        note: "first numeric pair from .optinfo",
      };
    }

    const filledSlots = overlayOpt?.slots.filter((slot) => !slot.empty) ?? [];
    if (filledSlots.length > 0) {
      const minX = Math.min(...filledSlots.map((slot) => slot.destX));
      const maxX = Math.max(...filledSlots.map((slot) => slot.destX + slot.width));
      const maxY = Math.max(...filledSlots.map((slot) => slot.destY + slot.height));
      evidence.optBounds = {
        available: true,
        x: -Math.round((minX + maxX) * 0.5),
        y: -maxY,
        minX,
        maxX,
        maxY,
        note: "computed from .opt destination bounds",
      };
    }

    return evidence;
  }, [natureSebById, natureSebFiles, overlayOpt, overlayOptInfoHints, selectedOverlayRow]);

  const autoRuleMaps = useMemo(() => {
    const byRowId = new Map<number, AutoNatureAssetRule>();
    const byImgId = new Map<number, AutoNatureAssetRule>();
    const byFilename = new Map<string, AutoNatureAssetRule>();

    for (const row of overlayRows) {
      const filename = natureImageById.get(row.img) ?? "";
      if (!filename) {
        continue;
      }
      const metadata = natureMetadataByFilename.get(filename);
      if (!metadata) {
        continue;
      }
      const rule = deriveAutoNatureAssetRule(row, filename, metadata, natureSebById, natureSebFiles);
      byRowId.set(row.id, rule);
      if (!byImgId.has(row.img)) {
        byImgId.set(row.img, rule);
      }
      if (!byFilename.has(filename)) {
        byFilename.set(filename, rule);
      }
    }

    return { byRowId, byImgId, byFilename };
  }, [allNatureRows, natureImageById, natureMetadataByFilename, natureSebById, natureSebFiles]);

  const selectedAutoRule = useMemo<AutoNatureAssetRule | null>(() => {
    if (!selectedOverlayRow) {
      return null;
    }
    return autoRuleMaps.byRowId.get(selectedOverlayRow.id) ?? null;
  }, [autoRuleMaps.byRowId, selectedOverlayRow]);

  const selectedNatureRule = useMemo<NaturePlacementRule | null>(() => {
    if (!overlayFilename) {
      return null;
    }
    if (savedNatureRules[overlayFilename]) {
      return savedNatureRules[overlayFilename];
    }
    if (!selectedAutoRule) {
      return null;
    }
    return {
      anchorMode: selectedAutoRule.anchorMode,
      drawOrder: selectedAutoRule.drawOrder,
      overlaySourceMode: selectedAutoRule.overlaySourceMode,
      offsetX: selectedAutoRule.offsetX,
      offsetY: selectedAutoRule.offsetY,
      reason: selectedAutoRule.reason,
    };
  }, [overlayFilename, savedNatureRules, selectedAutoRule]);

  useEffect(() => {
    if (!autoApplyNatureRule || !selectedNatureRule) {
      return;
    }
    setAnchorMode(selectedNatureRule.anchorMode);
    setDrawOrder(selectedNatureRule.drawOrder);
    setOverlaySourceMode(selectedNatureRule.overlaySourceMode);
    setOverlayOffsetX(selectedNatureRule.offsetX);
    setOverlayOffsetY(selectedNatureRule.offsetY);
  }, [autoApplyNatureRule, overlayFilename, selectedNatureRule]);

  const mapMatchedGuesses = useMemo<CompositionGuess[]>(() => {
    const guesses: CompositionGuess[] = [];
    const filledSlots = overlayOpt?.slots.filter((slot) => !slot.empty) ?? [];
    const first = filledSlots[0] ?? null;

    if (selectedNatureRule) {
      guesses.push({
        key: "per-element-rule",
        label: "Per-element rule",
        reason: selectedNatureRule.reason,
        anchorMode: selectedNatureRule.anchorMode,
        drawOrder: selectedNatureRule.drawOrder,
        baseSourceMode: "full-image",
        overlaySourceMode: selectedNatureRule.overlaySourceMode,
        offsetX: selectedNatureRule.offsetX,
        offsetY: selectedNatureRule.offsetY,
      });
    }

    guesses.push({
      key: "native-anchor",
      label: "Native anchor",
      reason: "baseline bottom-center full-image with zero offset",
      anchorMode: "bottom-center",
      drawOrder: "base-first",
      baseSourceMode: "full-image",
      overlaySourceMode: "full-image",
      offsetX: 0,
      offsetY: 0,
    });

    if (first) {
      guesses.push({
        key: "first-slot-dest",
        label: "First slot dest",
        reason: `from first non-empty .opt slot dest=(${first.destX}, ${first.destY})`,
        anchorMode: "bottom-center",
        drawOrder: "base-first",
        baseSourceMode: "full-image",
        overlaySourceMode: "full-image",
        offsetX: first.destX,
        offsetY: first.destY,
      });

      guesses.push({
        key: "first-slot-opt-rect",
        label: "First slot crop",
        reason: "use opt-rect crop and first slot destination",
        anchorMode: "bottom-center",
        drawOrder: "base-first",
        baseSourceMode: "full-image",
        overlaySourceMode: "opt-rect",
        offsetX: first.destX,
        offsetY: first.destY,
      });
    }

    if (filledSlots.length > 0) {
      const minX = Math.min(...filledSlots.map((slot) => slot.destX));
      const maxX = Math.max(...filledSlots.map((slot) => slot.destX + slot.width));
      const maxY = Math.max(...filledSlots.map((slot) => slot.destY + slot.height));
      guesses.push({
        key: "bbox-bottom-center",
        label: "BBox bottom-center",
        reason: `center from opt dest bounds x=[${minX}, ${maxX}] yBottom=${maxY}`,
        anchorMode: "bottom-center",
        drawOrder: "base-first",
        baseSourceMode: "full-image",
        overlaySourceMode: "full-image",
        offsetX: -Math.round((minX + maxX) * 0.5),
        offsetY: -maxY,
      });
    }

    const optHint = overlayOptInfoHints[0] ?? null;
    if (optHint) {
      guesses.push({
        key: "optinfo-first-pair",
        label: "OPTINFO first pair",
        reason: `from .optinfo numeric pair (${optHint.x}, ${optHint.y})`,
        anchorMode: "bottom-center",
        drawOrder: "base-first",
        baseSourceMode: "full-image",
        overlaySourceMode: "full-image",
        offsetX: optHint.x,
        offsetY: optHint.y,
      });
    }

    return dedupeGuessesByPlacement(guesses).slice(0, 6);
  }, [overlayOpt, overlayOptInfoHints, selectedNatureRule]);

  const guessPreviews = useMemo(() => {
    return mapMatchedGuesses.map((guess) => ({
      guess,
      dataUrl: renderCompositionDataUrl({
        width: 520,
        height: 340,
        baseImage: baseImage?.image ?? null,
        overlayImage: overlayImage?.image ?? null,
        baseOpt,
        overlayOpt,
        baseFrame: selectedBaseRow?.frame ?? 0,
        overlayFrame: selectedOverlayRow?.frame ?? 0,
        anchorMode: guess.anchorMode,
        drawOrder: guess.drawOrder,
        drawScale,
        baseSourceMode: guess.baseSourceMode,
        overlaySourceMode: guess.overlaySourceMode,
        overlayAutoRect: selectedAutoRule?.sourceRect ?? null,
        overlayOffsetX: guess.offsetX,
        overlayOffsetY: guess.offsetY,
      }),
    }));
  }, [
    baseImage,
    baseOpt,
    drawScale,
    mapMatchedGuesses,
    overlayImage,
    overlayOpt,
    selectedAutoRule,
    selectedBaseRow,
    selectedOverlayRow,
  ]);

  return (
    <div className="mx-auto max-w-7xl px-4 pb-12 pt-6 text-sm">
      <h1 className="text-2xl font-semibold">Terrain Composition Lab</h1>
      <p className="mt-2 text-muted-foreground">
        Safe composition sandbox for one base terrain tile plus one nature or object overlay. This does not affect map rendering.
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        In this lab: Base row (category=0) is terrain, Nature row (category=1) is the overlay sprite.
      </p>

      {loadingError && (
        <div className="mt-4 rounded border border-red-500/40 bg-red-950/30 p-3 text-red-100">{loadingError}</div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Selectors</div>
          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Terrain type</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={selectedType}
              onChange={(event) => setSelectedType(Number(event.target.value))}
            >
              {terrainTypes.map((typeValue) => (
                <option key={typeValue} value={typeValue}>
                  {typeValue} - {TERRAIN_TYPE_NAMES[typeValue] ?? "unknown"}
                </option>
              ))}
            </select>
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Base row (category=0)</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={selectedBaseId ?? ""}
              onChange={(event) => setSelectedBaseId(Number(event.target.value))}
            >
              {baseRows.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id} | img={row.img} | frame={row.frame}
                </option>
              ))}
            </select>
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Nature row (category=1)</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={selectedOverlayId ?? ""}
              onChange={(event) => setSelectedOverlayId(Number(event.target.value))}
            >
              {overlayRows.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id} | {natureImageById.get(row.img) ?? "missing"}
                </option>
              ))}
            </select>
          </label>

          <div className="mt-3 rounded border border-border bg-background/40 p-2 text-xs">
            <div className="font-medium">Map cell matched pair</div>
            {matchedPair ? (
              <>
                <div className="mt-1 text-muted-foreground">
                  cell ({matchedPair.cell.x}, {matchedPair.cell.y}) fields f0={matchedPair.cell.fields.f0} f1={matchedPair.cell.fields.f1} f2={matchedPair.cell.fields.f2} f3={matchedPair.cell.fields.f3} f4={matchedPair.cell.fields.f4} f5={matchedPair.cell.fields.f5}
                </div>
                <div className="mt-1 text-muted-foreground">base row: {matchedPair.baseRow.id} ({matchedPair.baseReason})</div>
                <div className="mt-1 text-muted-foreground">nature row: {matchedPair.overlayRow.id} (direct img={matchedPair.overlayField})</div>
                <div className="mt-1 text-muted-foreground">This only selects the base/nature rows from one real map cell; it does not apply offsets.</div>
                <button
                  type="button"
                  className="mt-2 rounded border border-border bg-card px-2 py-1 text-foreground hover:bg-accent"
                  onClick={() => {
                    setSelectedBaseId(matchedPair.baseRow.id);
                    setSelectedOverlayId(matchedPair.overlayRow.id);
                  }}
                >
                  Use this map-matched pair (rows only)
                </button>
              </>
            ) : (
              <div className="mt-1 text-muted-foreground">No direct base+nature pair found for this terrain type yet.</div>
            )}
          </div>
        </div>

        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Composition Controls</div>
          <div className="mt-2 rounded border border-border bg-background/40 p-2 text-xs">
            <div className="font-medium">Nature element rule</div>
            <div className="mt-1 text-muted-foreground">
              {overlayFilename || "No nature selected"}
            </div>
            <div className="mt-1 text-muted-foreground">
              active rule: {savedNatureRules[overlayFilename] ? "manual override" : (selectedNatureRule ? "auto extracted" : "none")}
            </div>
            <div className="mt-1 text-muted-foreground">
              {selectedNatureRule?.reason ?? "no rule yet"}
            </div>
            <div className="mt-2 rounded border border-border bg-background/40 p-2 text-[11px]">
              <div className="font-medium text-foreground">Automatic extraction evidence</div>
              <div className="mt-1 text-muted-foreground">
                rule maps: row={autoRuleMaps.byRowId.size} img={autoRuleMaps.byImgId.size} filename={autoRuleMaps.byFilename.size}
              </div>
              {selectedAutoRule && (
                <>
                  <div className="mt-1 text-muted-foreground">
                    extractor source: {selectedAutoRule.sourceKind} ({selectedAutoRule.sourceSummary})
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    source rect: ({selectedAutoRule.sourceRect.srcX}, {selectedAutoRule.sourceRect.srcY}) {selectedAutoRule.sourceRect.srcW}x{selectedAutoRule.sourceRect.srcH}
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    multi-state: {selectedAutoRule.containsMultipleStates ? "yes" : "no"}, chosen: {selectedAutoRule.chosenState}
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    offset: ({selectedAutoRule.offsetX}, {selectedAutoRule.offsetY}) via {selectedAutoRule.offsetSource}, anchor={selectedAutoRule.anchorMode}, draw={selectedAutoRule.drawWidth}x{selectedAutoRule.drawHeight}
                  </div>
                </>
              )}
              <div className="mt-1 text-muted-foreground">
                SEB: {sourceEvidence.seb.available
                  ? `${sourceEvidence.seb.file} -> (${sourceEvidence.seb.x}, ${sourceEvidence.seb.y}) ${sourceEvidence.seb.note}`
                  : sourceEvidence.seb.note}
              </div>
              <div className="mt-1 text-muted-foreground">
                OPTINFO: {sourceEvidence.optInfo.available
                  ? `(${sourceEvidence.optInfo.x}, ${sourceEvidence.optInfo.y}) ${sourceEvidence.optInfo.note}`
                  : sourceEvidence.optInfo.note}
              </div>
              {sourceEvidence.optInfo.available && sourceEvidence.optInfo.line && (
                <div className="mt-1 break-all text-muted-foreground">line: {sourceEvidence.optInfo.line}</div>
              )}
              <div className="mt-1 text-muted-foreground">
                OPT bounds: {sourceEvidence.optBounds.available
                  ? `x=[${sourceEvidence.optBounds.minX}, ${sourceEvidence.optBounds.maxX}] yBottom=${sourceEvidence.optBounds.maxY} -> (${sourceEvidence.optBounds.x}, ${sourceEvidence.optBounds.y})`
                  : sourceEvidence.optBounds.note}
              </div>
            </div>
            <label className="mt-2 flex items-center gap-2 text-muted-foreground">
              <input
                type="checkbox"
                checked={autoApplyNatureRule}
                onChange={(event) => setAutoApplyNatureRule(event.target.checked)}
              />
              auto-apply rule when nature changes
            </label>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded border border-border bg-card px-2 py-1 text-foreground hover:bg-accent"
                onClick={() => {
                  if (!overlayFilename || !selectedNatureRule) {
                    return;
                  }
                  setAnchorMode(selectedNatureRule.anchorMode);
                  setDrawOrder(selectedNatureRule.drawOrder);
                  setOverlaySourceMode(selectedNatureRule.overlaySourceMode);
                  setOverlayOffsetX(selectedNatureRule.offsetX);
                  setOverlayOffsetY(selectedNatureRule.offsetY);
                }}
              >
                Apply active rule
              </button>
              <button
                type="button"
                className="rounded border border-border bg-card px-2 py-1 text-foreground hover:bg-accent"
                onClick={() => {
                  if (!overlayFilename) {
                    return;
                  }
                  setSavedNatureRules((previous) => ({
                    ...previous,
                    [overlayFilename]: {
                      anchorMode,
                      drawOrder,
                      overlaySourceMode,
                      offsetX: overlayOffsetX,
                      offsetY: overlayOffsetY,
                      reason: "saved from manual tuning",
                    },
                  }));
                }}
              >
                Save current as this element rule
              </button>
              <button
                type="button"
                className="rounded border border-border bg-card px-2 py-1 text-foreground hover:bg-accent"
                onClick={() => {
                  if (!overlayFilename) {
                    return;
                  }
                  setSavedNatureRules((previous) => {
                    const next = { ...previous };
                    delete next[overlayFilename];
                    return next;
                  });
                }}
              >
                Clear saved rule
              </button>
            </div>
          </div>

          <div className="mt-3 grid gap-2">
            <label>
              <span className="mb-1 block text-muted-foreground">Nature X offset: {overlayOffsetX}</span>
              <input
                type="range"
                min={-200}
                max={200}
                value={overlayOffsetX}
                onChange={(event) => setOverlayOffsetX(Number(event.target.value))}
                className="w-full"
              />
            </label>
            <label>
              <span className="mb-1 block text-muted-foreground">Nature Y offset: {overlayOffsetY}</span>
              <input
                type="range"
                min={-200}
                max={200}
                value={overlayOffsetY}
                onChange={(event) => setOverlayOffsetY(Number(event.target.value))}
                className="w-full"
              />
            </label>
            <label>
              <span className="mb-1 block text-muted-foreground">Draw scale: {drawScale.toFixed(2)}</span>
              <input
                type="range"
                min={0.25}
                max={4}
                step={0.05}
                value={drawScale}
                onChange={(event) => setDrawScale(Number(event.target.value))}
                className="w-full"
              />
            </label>
          </div>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Anchor mode</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={anchorMode}
              onChange={(event) => setAnchorMode(event.target.value as AnchorMode)}
            >
              <option value="bottom-center">bottom-center</option>
              <option value="center">center</option>
              <option value="top-left">top-left</option>
            </select>
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Draw order</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={drawOrder}
              onChange={(event) => setDrawOrder(event.target.value as DrawOrder)}
            >
              <option value="base-first">base then overlay</option>
              <option value="overlay-first">overlay then base</option>
            </select>
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Base source mode</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={baseSourceMode}
              onChange={(event) => setBaseSourceMode(event.target.value as SourceMode)}
            >
              <option value="full-image">full image</option>
              <option value="opt-rect">OPT rect</option>
            </select>
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-muted-foreground">Nature source mode</span>
            <select
              className="w-full rounded border border-border bg-background px-2 py-1"
              value={overlaySourceMode}
              onChange={(event) => setOverlaySourceMode(event.target.value as SourceMode)}
            >
              <option value="auto-rule">auto rule (primary)</option>
              <option value="full-image">full image</option>
              <option value="opt-rect">OPT rect</option>
            </select>
            <div className="mt-1 text-[11px] text-muted-foreground">Use full image or OPT rect only for inspection overrides.</div>
          </label>
        </div>

        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Composed Result</div>
          <canvas ref={canvasRef} width={420} height={280} className="mt-3 w-full rounded border border-border bg-black" />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Base Image</div>
          <div className="mt-2 text-muted-foreground">{baseFilename || "missing"}</div>
          {baseImage ? (
            <img
              src={`/world-assets/chip/${baseImage.filename}`}
              alt="base"
              className="mt-2 max-h-44 rounded border border-border bg-background"
            />
          ) : (
            <div className="mt-2 text-muted-foreground">No base image loaded.</div>
          )}
          <div className="mt-3 whitespace-pre-wrap text-xs">{renderRowInfo(selectedBaseRow, baseFilename, baseOpt, baseOptInfo)}</div>
        </div>

        <div className="rounded border border-border bg-card p-3">
          <div className="font-medium">Nature Image (Overlay)</div>
          <div className="mt-2 text-muted-foreground">{overlayFilename || "missing"}</div>
          {overlayImage ? (
            <img
              src={`/world-assets/nature/${overlayImage.filename}`}
              alt="overlay"
              className="mt-2 max-h-44 rounded border border-border bg-background"
            />
          ) : (
            <div className="mt-2 text-muted-foreground">No overlay image loaded.</div>
          )}
          <div className="mt-3 whitespace-pre-wrap text-xs">{renderRowInfo(selectedOverlayRow, overlayFilename, overlayOpt, overlayOptInfo)}</div>
        </div>
      </div>

      <div className="mt-4 rounded border border-border bg-card p-3">
        <div className="font-medium">Generated Placement Guesses (.opt and .optinfo)</div>
        <div className="mt-1 text-xs text-muted-foreground">
          Each preview uses one map-matched base+nature pair and applies per-element metadata guesses. The top guess is this element's active rule.
        </div>
        {guessPreviews.length === 0 ? (
          <div className="mt-2 text-xs text-muted-foreground">Choose rows with loaded images to generate previews.</div>
        ) : (
          <div className="mt-3 grid gap-3 md:grid-cols-1 xl:grid-cols-2">
            {guessPreviews.map(({ guess, dataUrl }) => (
              <div key={guess.key} className="rounded border border-border bg-background/50 p-2">
                <div className="text-xs font-medium">{guess.label}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">{guess.reason}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  offset=({guess.offsetX}, {guess.offsetY}) anchor={guess.anchorMode} src={guess.overlaySourceMode}
                </div>
                {dataUrl ? (
                  <img src={dataUrl} alt={guess.label} className="mt-2 h-[340px] w-full rounded border border-border bg-black object-contain" />
                ) : (
                  <div className="mt-2 h-[340px] rounded border border-border bg-black/60" />
                )}
                <button
                  type="button"
                  className="mt-2 rounded border border-border bg-card px-2 py-1 text-[11px] text-foreground hover:bg-accent"
                  onClick={() => {
                    setOverlayOffsetX(guess.offsetX);
                    setOverlayOffsetY(guess.offsetY);
                    setAnchorMode(guess.anchorMode);
                    setDrawOrder(guess.drawOrder);
                    setBaseSourceMode(guess.baseSourceMode);
                    setOverlaySourceMode(guess.overlaySourceMode);
                  }}
                >
                  Apply guess to main preview
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function pickBaseRowForMapCell(rows: TerrainRow[], cell: ParsedMapCell): { row: TerrainRow; reason: string } | null {
  const byIdF2 = rows.find((row) => row.id === cell.fields.f2);
  if (byIdF2) {
    return { row: byIdF2, reason: "id=f2" };
  }
  const byImgF2 = rows.find((row) => row.img === cell.fields.f2);
  if (byImgF2) {
    return { row: byImgF2, reason: "img=f2" };
  }
  const byDataIdF2 = rows.find((row) => row.dataId >= 0 && row.dataId === cell.fields.f2);
  if (byDataIdF2) {
    return { row: byDataIdF2, reason: "dataId=f2" };
  }
  return null;
}

function pickNatureRowForMapCell(
  overlayByImg: Map<number, TerrainRow>,
  cell: ParsedMapCell,
): { row: TerrainRow; field: MatchField } | null {
  const entries: Array<{ field: MatchField; value: number }> = [
    { field: "f0", value: cell.fields.f0 },
    { field: "f1", value: cell.fields.f1 },
    { field: "f2", value: cell.fields.f2 },
    { field: "f3", value: cell.fields.f3 },
    { field: "f4", value: cell.fields.f4 },
    { field: "f5", value: cell.fields.f5 },
  ];
  for (const entry of entries) {
    const row = overlayByImg.get(entry.value);
    if (row) {
      return { row, field: entry.field };
    }
  }
  return null;
}

function parseOptInfoHints(optInfo: string | null): OptInfoHint[] {
  if (!optInfo) {
    return [];
  }
  const seen = new Set<string>();
  const hints: OptInfoHint[] = [];
  const lines = optInfo.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

  for (const line of lines) {
    const matches = line.match(/-?\d+/g);
    if (!matches || matches.length < 2) {
      continue;
    }
    for (let index = 0; index + 1 < matches.length; index += 2) {
      const x = Number.parseInt(matches[index], 10);
      const y = Number.parseInt(matches[index + 1], 10);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        continue;
      }
      if (Math.abs(x) > 512 || Math.abs(y) > 512) {
        continue;
      }
      const key = `${x},${y}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      hints.push({ x, y, sourceLine: line });
      if (hints.length >= 8) {
        return hints;
      }
    }
  }
  return hints;
}

function deriveAutoNatureAssetRule(
  overlayRow: TerrainRow,
  overlayFilename: string,
  metadata: NatureAssetMetadata,
  sebById: Map<number, string>,
  sebFiles: Map<string, SebFile>,
): AutoNatureAssetRule {
  const token = overlayFilename.toLowerCase();
  const imageWidth = Math.max(1, metadata.imageWidth);
  const imageHeight = Math.max(1, metadata.imageHeight);
  const fullRect: SourceRect = { srcX: 0, srcY: 0, srcW: imageWidth, srcH: imageHeight };
  const filledSlots = metadata.opt?.slots.filter((slot) => !slot.empty) ?? [];
  const optHint = metadata.optHints[0] ?? null;

  let sourceRect: SourceRect = fullRect;
  let sourceKind: AutoNatureAssetRule["sourceKind"] = "FULL_IMAGE_FALLBACK";
  let sourceSummary = "full image fallback";
  let containsMultipleStates = false;
  let chosenState = "full-image";

  const sebName = overlayRow.seb >= 0 ? sebById.get(overlayRow.seb) : undefined;
  const seb = sebName ? sebFiles.get(sebName) : undefined;
  const sebBlock = seb?.blocks[0];
  const sebRecord = sebBlock?.records.find((entry) => entry.frameIndex === overlayRow.frame) ?? sebBlock?.records[0] ?? null;

  if (sebRecord && sebRecord.width > 0 && sebRecord.height > 0) {
    sourceRect = clampSourceRect(
      {
        srcX: sebRecord.srcX,
        srcY: sebRecord.srcY,
        srcW: sebRecord.width,
        srcH: sebRecord.height,
      },
      imageWidth,
      imageHeight,
    );
    sourceKind = "SEB";
    containsMultipleStates = (sebBlock?.records.length ?? 0) > 1;
    chosenState = `SEB frame ${overlayRow.frame}`;
    sourceSummary = `${sebName ?? "seb"} block=0 frame=${overlayRow.frame}`;
  } else if (filledSlots.length > 0) {
    const frameSlot = metadata.opt?.slots.find((entry) => !entry.empty && (entry.u + entry.v * Math.max(1, metadata.opt?.cols ?? 1)) === overlayRow.frame)
      ?? filledSlots[0];
    sourceRect = clampSourceRect(
      {
        srcX: frameSlot.srcX,
        srcY: frameSlot.srcY,
        srcW: frameSlot.width,
        srcH: frameSlot.height,
      },
      imageWidth,
      imageHeight,
    );
    sourceKind = "OPT";
    containsMultipleStates = filledSlots.length > 1;
    chosenState = `OPT slot frame ${overlayRow.frame}`;
    sourceSummary = `first matching non-empty .opt slot`;
  } else if (isLikelyMultiStateTallImage(imageWidth, imageHeight)) {
    const segments = imageHeight >= imageWidth * 2.4 ? 3 : 2;
    sourceRect = deriveBottomStateRect(imageWidth, imageHeight, segments);
    sourceKind = "BOTTOM_STATE_FALLBACK";
    containsMultipleStates = true;
    chosenState = `bottom state (${segments} stacked)`;
    sourceSummary = "PNG height-based bottom-state fallback";
  }

  let offsetX = 0;
  let offsetY = 0;
  let offsetSource: AutoNatureAssetRule["offsetSource"] = "NONE";
  if (sebRecord) {
    offsetX = sebRecord.offsetX;
    offsetY = sebRecord.offsetY;
    offsetSource = "SEB";
  } else if (optHint) {
    offsetX = optHint.x;
    offsetY = optHint.y;
    offsetSource = "OPTINFO";
  } else if (filledSlots.length > 0) {
    const minX = Math.min(...filledSlots.map((slot) => slot.destX));
    const maxX = Math.max(...filledSlots.map((slot) => slot.destX + slot.width));
    const maxY = Math.max(...filledSlots.map((slot) => slot.destY + slot.height));
    offsetX = -Math.round((minX + maxX) * 0.5);
    offsetY = -maxY;
    offsetSource = "OPT_BOUNDS";
  }

  return {
    rowId: overlayRow.id,
    imgId: overlayRow.img,
    filename: overlayFilename,
    sourceRect,
    sourceKind,
    sourceSummary,
    containsMultipleStates,
    chosenState,
    offsetX,
    offsetY,
    offsetSource,
    anchorMode: pickAnchorModeForNatureToken(token),
    drawOrder: "base-first",
    overlaySourceMode: "auto-rule",
    drawWidth: sourceRect.srcW,
    drawHeight: sourceRect.srcH,
    reason: `auto-derived rect=${sourceKind} offset=${offsetSource}`,
  };
}

function clampSourceRect(rect: SourceRect, imageWidth: number, imageHeight: number): SourceRect {
  const srcX = Math.max(0, Math.min(rect.srcX, Math.max(0, imageWidth - 1)));
  const srcY = Math.max(0, Math.min(rect.srcY, Math.max(0, imageHeight - 1)));
  const srcW = Math.max(1, Math.min(rect.srcW, imageWidth - srcX));
  const srcH = Math.max(1, Math.min(rect.srcH, imageHeight - srcY));
  return { srcX, srcY, srcW, srcH };
}

function isLikelyMultiStateTallImage(width: number, height: number): boolean {
  if (width <= 0 || height <= 0) {
    return false;
  }
  return height >= 64 && height / width >= 1.6;
}

function deriveBottomStateRect(width: number, height: number, segments: number): SourceRect {
  const safeSegments = Math.max(2, segments);
  const segmentHeight = Math.max(1, Math.floor(height / safeSegments));
  const srcY = Math.max(0, height - segmentHeight);
  return {
    srcX: 0,
    srcY,
    srcW: width,
    srcH: Math.max(1, height - srcY),
  };
}

function pickAnchorModeForNatureToken(token: string): AnchorMode {
  if (token.includes("tree") || token.includes("human") || token.includes("obj") || token.includes("special")) {
    return "bottom-center";
  }
  return "center";
}

function dedupeGuessesByPlacement(guesses: CompositionGuess[]): CompositionGuess[] {
  const seen = new Set<string>();
  const result: CompositionGuess[] = [];

  for (const guess of guesses) {
    const key = [guess.anchorMode, guess.drawOrder, guess.baseSourceMode, guess.overlaySourceMode, guess.offsetX, guess.offsetY].join("|");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(guess);
  }

  return result;
}

function renderCompositionDataUrl(input: {
  width: number;
  height: number;
  baseImage: HTMLImageElement | null;
  overlayImage: HTMLImageElement | null;
  baseOpt: OptMetadata | null;
  overlayOpt: OptMetadata | null;
  baseFrame: number;
  overlayFrame: number;
  anchorMode: AnchorMode;
  drawOrder: DrawOrder;
  drawScale: number;
  baseSourceMode: SourceMode;
  overlaySourceMode: SourceMode;
  overlayAutoRect: SourceRect | null;
  overlayOffsetX: number;
  overlayOffsetY: number;
}): string | null {
  if (!input.baseImage || !input.overlayImage) {
    return null;
  }
  const canvas = document.createElement("canvas");
  canvas.width = input.width;
  canvas.height = input.height;
  const context = canvas.getContext("2d");
  if (!context) {
    return null;
  }

  drawCompositionFrame({
    context,
    width: input.width,
    height: input.height,
    baseImage: input.baseImage,
    overlayImage: input.overlayImage,
    baseOpt: input.baseOpt,
    overlayOpt: input.overlayOpt,
    baseFrame: input.baseFrame,
    overlayFrame: input.overlayFrame,
    anchorMode: input.anchorMode,
    drawOrder: input.drawOrder,
    drawScale: input.drawScale,
    baseSourceMode: input.baseSourceMode,
    overlaySourceMode: input.overlaySourceMode,
    overlayAutoRect: input.overlayAutoRect,
    overlayOffsetX: input.overlayOffsetX,
    overlayOffsetY: input.overlayOffsetY,
  });

  return canvas.toDataURL("image/png");
}

function drawCompositionFrame(input: {
  context: CanvasRenderingContext2D;
  width: number;
  height: number;
  baseImage: HTMLImageElement | null;
  overlayImage: HTMLImageElement | null;
  baseOpt: OptMetadata | null;
  overlayOpt: OptMetadata | null;
  baseFrame: number;
  overlayFrame: number;
  anchorMode: AnchorMode;
  drawOrder: DrawOrder;
  drawScale: number;
  baseSourceMode: SourceMode;
  overlaySourceMode: SourceMode;
  overlayAutoRect: SourceRect | null;
  overlayOffsetX: number;
  overlayOffsetY: number;
}): void {
  const { context } = input;
  context.clearRect(0, 0, input.width, input.height);
  context.fillStyle = "#0b1220";
  context.fillRect(0, 0, input.width, input.height);

  const baseRect = getSourceRect(input.baseImage, input.baseOpt, input.baseFrame, input.baseSourceMode, null);
  const overlayRect = getSourceRect(input.overlayImage, input.overlayOpt, input.overlayFrame, input.overlaySourceMode, input.overlayAutoRect);

  const anchorX = Math.round(input.width * 0.5);
  const anchorY = Math.round(input.height * 0.72);

  const basePlacement = computePlacement({
    anchorMode: input.anchorMode,
    anchorX,
    anchorY,
    sourceRect: baseRect,
    scale: input.drawScale,
    offsetX: 0,
    offsetY: 0,
  });
  const overlayPlacement = computePlacement({
    anchorMode: input.anchorMode,
    anchorX,
    anchorY,
    sourceRect: overlayRect,
    scale: input.drawScale,
    offsetX: input.overlayOffsetX,
    offsetY: input.overlayOffsetY,
  });

  const drawBase = () => {
    if (!input.baseImage || !baseRect) {
      return;
    }
    context.drawImage(
      input.baseImage,
      baseRect.srcX,
      baseRect.srcY,
      baseRect.srcW,
      baseRect.srcH,
      basePlacement.drawX,
      basePlacement.drawY,
      basePlacement.drawW,
      basePlacement.drawH,
    );
    context.strokeStyle = "rgba(255,255,255,0.35)";
    context.strokeRect(basePlacement.drawX + 0.5, basePlacement.drawY + 0.5, basePlacement.drawW - 1, basePlacement.drawH - 1);
  };

  const drawOverlay = () => {
    if (!input.overlayImage || !overlayRect) {
      return;
    }
    context.drawImage(
      input.overlayImage,
      overlayRect.srcX,
      overlayRect.srcY,
      overlayRect.srcW,
      overlayRect.srcH,
      overlayPlacement.drawX,
      overlayPlacement.drawY,
      overlayPlacement.drawW,
      overlayPlacement.drawH,
    );
    context.strokeStyle = "rgba(255, 80, 80, 0.95)";
    context.strokeRect(overlayPlacement.drawX + 0.5, overlayPlacement.drawY + 0.5, overlayPlacement.drawW - 1, overlayPlacement.drawH - 1);
  };

  if (input.drawOrder === "base-first") {
    drawBase();
    drawOverlay();
  } else {
    drawOverlay();
    drawBase();
  }

  context.beginPath();
  context.fillStyle = "rgba(56, 189, 248, 0.95)";
  context.arc(anchorX, anchorY, 3, 0, Math.PI * 2);
  context.fill();
}

function renderRowInfo(row: TerrainRow | null, filename: string, opt: OptMetadata | null, optInfo: string | null): string {
  if (!row) {
    return "No row selected.";
  }

  const filledSlots = opt ? opt.slots.filter((slot) => !slot.empty).length : 0;
  const firstFilled = opt ? opt.slots.find((slot) => !slot.empty) : null;

  const lines = [
    `type: ${row.type}`,
    `category: ${row.category}`,
    `natureId: ${row.natureId}`,
    `natureGroupId: ${row.natureGroupId}`,
    `img: ${row.img}`,
    `seb: ${row.seb}`,
    `frame: ${row.frame}`,
    `dataId: ${row.dataId}`,
    `filename: ${filename || "missing"}`,
    `opt summary: ${opt ? `${opt.cols}x${opt.rows} slots=${opt.slots.length} filled=${filledSlots}` : "none"}`,
    `opt first filled slot: ${firstFilled ? `${firstFilled.srcX},${firstFilled.srcY},${firstFilled.width}x${firstFilled.height} dest=${firstFilled.destX},${firstFilled.destY}` : "none"}`,
    `optinfo summary: ${optInfo ?? "none"}`,
  ];

  return lines.join("\n");
}

function getSourceRect(
  image: HTMLImageElement | null,
  opt: OptMetadata | null,
  frame: number,
  mode: SourceMode,
  autoRect: SourceRect | null,
) {
  if (!image) {
    return null;
  }

  if (mode === "auto-rule" && autoRect) {
    return {
      srcX: Math.max(0, Math.min(autoRect.srcX, Math.max(0, image.width - 1))),
      srcY: Math.max(0, Math.min(autoRect.srcY, Math.max(0, image.height - 1))),
      srcW: Math.max(1, Math.min(autoRect.srcW, image.width - Math.max(0, autoRect.srcX))),
      srcH: Math.max(1, Math.min(autoRect.srcH, image.height - Math.max(0, autoRect.srcY))),
    };
  }

  if (mode === "opt-rect" && opt) {
    const slot = opt.slots.find((entry) => !entry.empty && (entry.u + entry.v * Math.max(1, opt.cols)) === frame)
      ?? opt.slots.find((entry) => !entry.empty)
      ?? null;

    if (slot) {
      return {
        srcX: Math.max(0, slot.srcX),
        srcY: Math.max(0, slot.srcY),
        srcW: Math.max(1, Math.min(slot.width, image.width - Math.max(0, slot.srcX))),
        srcH: Math.max(1, Math.min(slot.height, image.height - Math.max(0, slot.srcY))),
      };
    }
  }

  return {
    srcX: 0,
    srcY: 0,
    srcW: image.width,
    srcH: image.height,
  };
}

function computePlacement(input: {
  anchorMode: AnchorMode;
  anchorX: number;
  anchorY: number;
  sourceRect: { srcX: number; srcY: number; srcW: number; srcH: number } | null;
  scale: number;
  offsetX: number;
  offsetY: number;
}) {
  const srcW = input.sourceRect?.srcW ?? 1;
  const srcH = input.sourceRect?.srcH ?? 1;
  const drawW = Math.max(1, Math.round(srcW * input.scale));
  const drawH = Math.max(1, Math.round(srcH * input.scale));

  let drawX = input.anchorX;
  let drawY = input.anchorY;

  if (input.anchorMode === "bottom-center") {
    drawX = input.anchorX - Math.round(drawW / 2);
    drawY = input.anchorY - drawH;
  } else if (input.anchorMode === "center") {
    drawX = input.anchorX - Math.round(drawW / 2);
    drawY = input.anchorY - Math.round(drawH / 2);
  }

  drawX += input.offsetX;
  drawY += input.offsetY;

  return { drawX, drawY, drawW, drawH };
}

function parseTerrainRows(text: string): TerrainRow[] {
  const rows: TerrainRow[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split("\t").map((entry) => entry.trim());
    if (parts.length < 12) {
      continue;
    }
    const id = asInt(parts[0], -1);
    if (id < 0) {
      continue;
    }
    rows.push({
      id,
      type: asInt(parts[1], 0),
      category: asInt(parts[2], 0),
      dataId: asInt(parts[3], -1),
      name: parts[4] ?? "",
      res: asInt(parts[5], 0),
      img: asInt(parts[6], -1),
      seb: asInt(parts[7], -1),
      frame: asInt(parts[8], 0),
      natureId: asInt(parts[9], -1),
      natureGroupId: asInt(parts[10], -1),
    });
  }
  return rows;
}

function parseInfTable(text: string): Map<number, string> {
  const result = new Map<number, string>();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0 || !parts[1]) {
      continue;
    }
    const token = parts[1].split(",")[0]?.trim();
    if (!token) {
      continue;
    }
    result.set(id, token);
  }
  return result;
}

function parseOptSequential(buffer: ArrayBuffer): OptMetadata {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 4) {
    return {
      cellWidth: 48,
      cellHeight: 36,
      cols: 0,
      rows: 0,
      slots: [],
    };
  }

  const view = new DataView(buffer);
  const cellWidth = bytes[0];
  const cellHeight = bytes[1];
  const cols = bytes[2];
  const rows = bytes[3];
  let offset = 4;
  const slots: OptSlot[] = [];

  for (let v = 0; v < rows; v += 1) {
    for (let u = 0; u < cols; u += 1) {
      if (offset >= bytes.length) {
        slots.push({
          u,
          v,
          destX: 0,
          destY: 0,
          srcX: 0,
          srcY: 0,
          width: 0,
          height: 0,
          empty: true,
        });
        continue;
      }

      const flag = bytes[offset];
      if (flag === 0) {
        slots.push({
          u,
          v,
          destX: 0,
          destY: 0,
          srcX: 0,
          srcY: 0,
          width: 0,
          height: 0,
          empty: true,
        });
        offset += 1;
        continue;
      }

      if (flag === 1 && offset + 15 <= bytes.length) {
        const destX = view.getUint16(offset + 4, true);
        const destY = view.getUint16(offset + 6, true);
        const srcX = view.getUint16(offset + 8, true);
        const srcY = view.getUint16(offset + 10, true);
        const width = view.getUint16(offset + 12, true);
        const height = bytes[offset + 14];
        slots.push({
          u,
          v,
          destX,
          destY,
          srcX,
          srcY,
          width,
          height,
          empty: false,
        });
        offset += 15;
        continue;
      }

      slots.push({
        u,
        v,
        destX: 0,
        destY: 0,
        srcX: 0,
        srcY: 0,
        width: 0,
        height: 0,
        empty: true,
      });
      offset += 1;
    }
  }

  return {
    cellWidth,
    cellHeight,
    cols,
    rows,
    slots,
  };
}

function parseSeb(buffer: ArrayBuffer): SebFile {
  const view = new DataView(buffer);
  const totalBytes = buffer.byteLength;
  if (totalBytes < 4) {
    return { blockCount: 0, headerValue: 0, blocks: [] };
  }

  const blockCount = view.getUint16(0, false);
  const headerValue = view.getUint16(2, false);
  let offset = 4;
  const blocks: SebBlock[] = [];

  for (let blockIndex = 0; blockIndex < blockCount; blockIndex += 1) {
    if (offset + 4 > totalBytes) {
      break;
    }

    const frameCount = view.getUint16(offset, false);
    const period = view.getUint16(offset + 2, false);
    offset += 4;

    const records: SebRecord[] = [];
    for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
      if (offset + 20 > totalBytes) {
        break;
      }

      const tick = view.getInt16(offset, false);
      const sourceId = view.getInt16(offset + 2, false);
      const srcX = view.getInt16(offset + 4, false);
      const srcY = view.getInt16(offset + 6, false);
      const width = view.getInt16(offset + 8, false);
      const height = view.getInt16(offset + 10, false);
      const offsetX = view.getInt16(offset + 12, false);
      const offsetY = view.getInt16(offset + 14, false);
      offset += 20;

      records.push({
        frameIndex,
        tick,
        sourceId,
        srcX,
        srcY,
        width,
        height,
        offsetX,
        offsetY,
      });
    }

    blocks.push({
      blockIndex,
      period,
      records,
    });
  }

  return {
    blockCount,
    headerValue,
    blocks,
  };
}

async function fetchText(pathname: string): Promise<string> {
  const response = await fetch(pathname);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${pathname}: ${response.status}`);
  }
  return response.text();
}

async function fetchArrayBufferOptional(pathname: string): Promise<ArrayBuffer | null> {
  const response = await fetch(pathname);
  if (!response.ok) {
    return null;
  }
  return response.arrayBuffer();
}

async function fetchTextOptional(pathname: string): Promise<string | null> {
  const response = await fetch(pathname);
  if (!response.ok) {
    return null;
  }
  const contentType = response.headers.get("content-type") ?? "";
  const text = await response.text();
  if (contentType.includes("text/html") || /<!doctype html|<html/i.test(text.slice(0, 200))) {
    return null;
  }
  return text.trim() || null;
}

async function loadImageOptional(filename: string, folder: "chip" | "nature"): Promise<HTMLImageElement | null> {
  if (!filename) {
    return null;
  }
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = `/world-assets/${folder}/${filename}`;
  });
}

async function loadOptOptional(filename: string, folder: "chip" | "nature"): Promise<OptMetadata | null> {
  if (!filename) {
    return null;
  }
  const stem = filename.replace(/\.[^.]+$/, "");
  const buffer = await fetchArrayBufferOptional(`/world-assets/${folder}/${stem}.opt`);
  if (!buffer) {
    return null;
  }
  return parseOptSequential(buffer);
}

async function loadOptInfoOptional(filename: string, folder: "chip" | "nature"): Promise<string | null> {
  if (!filename) {
    return null;
  }
  const stem = filename.replace(/\.[^.]+$/, "");
  return fetchTextOptional(`/world-assets/${folder}/${stem}.optinfo`);
}

function natureFilenameMatchesType(filename: string, terrainType: number): boolean {
  const token = filename.toLowerCase();
  if (terrainType === 3) return token.includes("desert") || token.includes("suna");
  if (terrainType === 2 || terrainType === 13) return token.includes("grass") || token.includes("soil") || token.includes("tree") || token.includes("nature");
  if (terrainType === 4 || terrainType === 11) return token.includes("rock") || token.includes("rocky");
  if (terrainType === 6 || terrainType === 8) return token.includes("snow");
  if (terrainType === 7 || terrainType === 12) return token.includes("swamp");
  if (terrainType === 5 || terrainType === 10) return token.includes("volcano");
  if (terrainType === 1) return token.includes("soil") || token.includes("tree") || token.includes("human") || token.includes("special");
  return true;
}

function asInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export default function TerrainCompositionLabPage() {
  return <ResearchPreviewGate><UnverifiedTerrainCompositionLabPage /></ResearchPreviewGate>;
}
