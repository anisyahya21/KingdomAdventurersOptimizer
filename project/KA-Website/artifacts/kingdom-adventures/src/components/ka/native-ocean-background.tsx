import { useEffect, useRef } from "react";
import { nativeOceanSlices, NATIVE_OCEAN_TICKS_PER_SECOND } from "@/lib/native-ocean";

export function NativeOceanBackground({ image }: { image?: HTMLImageElement }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context || !image) return;
    let request = 0;
    let elapsed = 0;
    let previousTime: number | undefined;
    let previousKey = "";
    const draw = (time: number) => {
      if (previousTime !== undefined && !document.hidden) elapsed += time - previousTime;
      previousTime = time;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const tick = Math.floor(elapsed * NATIVE_OCEAN_TICKS_PER_SECOND / 1000) % 480;
      const slices = nativeOceanSlices(width, tick);
      const key = `${width},${height},${slices[0]?.sx},${slices[0]?.dw}`;
      if (width > 0 && height > 0 && key !== previousKey) {
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
        }
        context.imageSmoothingEnabled = true;
        // Native BLEND_LIGHT is RGB multiplication (RenderPass.Begin 0x231d1fc).
        // Daytime white + AddColor(50,50,30) clamps to white: no tint required.
        for (const slice of slices) {
          context.drawImage(image, slice.sx, 0, slice.sw, 200, slice.dx, 0, slice.dw, height);
        }
        previousKey = key;
      }
      request = requestAnimationFrame(draw);
    };
    const resetTime = () => { previousTime = undefined; };
    document.addEventListener("visibilitychange", resetTime);
    request = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(request);
      document.removeEventListener("visibilitychange", resetTime);
    };
  }, [image]);
  return <canvas ref={ref} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" />;
}
