import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

/**
 * Desktop-only entry for the embedded strategy optimiser.
 *
 * This config is deliberately independent of `vite.config.ts`: it builds the desktop entry pages
 * into `desktop-dist` with `copyPublicDir: false` because the local desktop host serves the
 * existing `public/` assets itself, and it resolves the same `@`/`@assets` aliases (and React
 * dedupe) so the page, the shared design system and the existing `GeneratedBattleReplay` renderer
 * resolve exactly as they do in the website build.
 */
export default defineConfig({
  base: "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@assets": path.resolve(import.meta.dirname, "..", "..", "attached_assets"),
    },
    dedupe: ["react", "react-dom"],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    /*
     * `desktop-dist` also holds the desktop packaging folder the host ships from, so this
     * build must never empty it: `emptyOutDir: false` leaves that folder intact and only replaces
     * the hashed web assets this config emits.
     */
    outDir: path.resolve(import.meta.dirname, "desktop-dist"),
    emptyOutDir: false,
    copyPublicDir: false,
    rollupOptions: {
      input: {
        strategy: path.resolve(import.meta.dirname, "desktop.html"),
        syntheticLegal: path.resolve(import.meta.dirname, "synthetic-legal.desktop.html"),
      },
    },
  },
});
