import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  getCharacterPreviewEnvelope,
  renderCharacterPreview,
  type CharacterRenderParams,
  type RenderEnvelope,
} from "@/lib/character-renderer";

type Props = CharacterRenderParams & {
  className?: string;
  style?: CSSProperties;
  label?: string;
  /**
   * Battle/native scene mode: display one source-art pixel as one logical scene pixel and anchor the
   * canvas on the **SEB origin** instead of stretching it to a fixed CSS box. The envelope's
   * `poseReferences[frame]` is the pose reference in SEB line coordinates, so adding it puts the SEB
   * origin - the point the native draw chain places on the entity origin - on the entity origin, the
   * same convention the human composite uses. The backing canvas keeps `scale` device pixels per
   * source pixel, so it stays crisp without changing the logical size.
   */
  logicalAnchoring?: boolean;
};

/** the renderer draws pose frames 0..3, so the pose-reference table is indexed the same way */
function clampFrame(frame: number): number {
  return Math.max(0, Math.min(3, Math.trunc(frame)));
}

export function CharacterPreviewCanvas({
  className,
  style,
  label = "character",
  logicalAnchoring = false,
  ...params
}: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [available, setAvailable] = useState(true);
  const [envelope, setEnvelope] = useState<RenderEnvelope | null>(null);
  const renderKey = useMemo(() => JSON.stringify(params), [params]);
  const canvasClassName = ["ka-pixel-art", className].filter(Boolean).join(" ");

  useEffect(() => {
    let cancelled = false;
    if (!logicalAnchoring) {
      setEnvelope(null);
      return () => {
        cancelled = true;
      };
    }
    getCharacterPreviewEnvelope(params)
      .then((next) => {
        if (!cancelled) setEnvelope(next);
      })
      .catch(() => {
        if (!cancelled) setEnvelope(null);
      });
    return () => {
      cancelled = true;
    };
  }, [renderKey, logicalAnchoring]);

  useEffect(() => {
    let cancelled = false;
    const canvas = ref.current;
    if (!canvas) return;
    setAvailable(true);
    renderCharacterPreview(canvas, params)
      .then((ok) => {
        if (!cancelled) setAvailable(ok);
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [renderKey]);

  /* the drawn frame's pose reference (body line x / shadow line y) in SEB line coordinates */
  const poseReference = envelope?.poseReferences?.[clampFrame(params.poseFrame)] ?? { x: 0, y: 0 };

  return (
    <canvas
      ref={ref}
      aria-label={label}
      role="img"
      className={logicalAnchoring ? "ka-pixel-art" : canvasClassName}
      style={{
        imageRendering: "pixelated",
        display: available ? undefined : "none",
        ...(logicalAnchoring && envelope
          ? {
              position: "absolute",
              left: envelope.cropX - envelope.originX + poseReference.x,
              top: envelope.cropY - envelope.originY + poseReference.y,
              width: envelope.cropW,
              height: envelope.cropH,
            }
          : null),
        ...style,
      }}
    />
  );
}
