import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

const rawPort = process.env.PORT ?? "5173";

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const basePath = process.env.BASE_PATH ?? "/";

export default defineConfig({
  base: basePath,
  plugins: await (async () => {
    const plugins = [react(), tailwindcss()];

    try {
      const { default: runtimeErrorOverlay } = await import(
        "@replit/vite-plugin-runtime-error-modal"
      );
      plugins.push(runtimeErrorOverlay());
    } catch {
      // This plugin is available in Replit, but local installs may not include it.
    }

    if (
      process.env.NODE_ENV !== "production" &&
      process.env.REPL_ID !== undefined
    ) {
      try {
        const { cartographer } = await import("@replit/vite-plugin-cartographer");
        plugins.push(
          cartographer({
            root: path.resolve(import.meta.dirname, ".."),
          }),
        );
      } catch {
        // Local development can proceed without Replit-specific tooling.
      }

      try {
        const { devBanner } = await import("@replit/vite-plugin-dev-banner");
        plugins.push(devBanner());
      } catch {
        // Local development can proceed without Replit-specific tooling.
      }
    }

    return plugins;
  })(),
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@assets": path.resolve(import.meta.dirname, "..", "..", "attached_assets"),
    },
    dedupe: ["react", "react-dom"],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
  },
  server: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
    proxy: {
      /*
       * PASS 16 COMMAND 16.9: in development the page posts to the relative `/api/battle-run`,
       * which production serves from the Vercel Python function. Locally that path is forwarded to
       * the same handler run as a plain Python process (`BATTLE_RUNNER_PORT`, default 8787), so no
       * environment-dependent URL is ever hardcoded in the app.
       */
      "/api/battle-run": {
        target: `http://127.0.0.1:${process.env.BATTLE_RUNNER_PORT || 8787}`,
        changeOrigin: true,
      },
      /*
       * Local-only strategy-search jobs (2026-09-20). Same local runner process as battle-run;
       * the endpoints are transport only and refuse to invent scores while the engine module is
       * absent. Production keeps these routes unproxied (local-only feature).
       */
      "/api/strategy-search": {
        target: `http://127.0.0.1:${process.env.BATTLE_RUNNER_PORT || 8787}`,
        changeOrigin: true,
      },
      /** Browser-only development fallback for read-only uploaded SQLite snapshots. */
      "/api/optimizer-library": {
        target: `http://127.0.0.1:${process.env.BATTLE_RUNNER_PORT || 8787}`,
        changeOrigin: true,
      },
      "^/ka(?:/|$)": {
        target: `http://localhost:${process.env.API_PORT || 3001}`,
        changeOrigin: true,
        // No rewrite: keep /ka for backend
      },
      "^/ka-api(?:/|$)": {
        target: `http://localhost:${process.env.API_PORT || 3001}`,
        rewrite: (p) => p.replace(/^\/ka-api/, "/api"),
      },
    },
    fs: {
      strict: true,
      deny: ["**/.*"],
    },
    // Prevent Vite from triggering HMR when the API server writes to its data
    // files (e.g. ka_shared.json). local-shared-data.ts imports that file as a
    // static module, so without this ignore Vite watches it and every
    // PUT /api/ka/pairs causes an HMR cycle that remounts the page.
    watch: {
      ignored: ["**/api-server/data/**"],
    },
  },
  preview: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
  },
});
