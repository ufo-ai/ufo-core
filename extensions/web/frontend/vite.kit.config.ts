import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { tablerMarks } from "./vite-marks";

/** The app kit: one library the portal serves beside its own assets, exporting the shipped app
 *  screens and the bridge transport for app pages to compose. Names carry no hash — a deployed
 *  page loads `assets/app-kit.js` off whatever portal it is framed in, so the kit versions with
 *  the portal deploy. The bundle stays unminified (it is source an app's agent may read) and
 *  inlines the theme's font files, since the page's own origin holds no assets of the portal's.
 *  Runs after the portal build — same `outDir`, `emptyOutDir` off. */

export default defineConfig({
  base: "/surface/web/static/",
  plugins: [react(), tailwindcss(), tablerMarks()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      "@brand": new URL("../../../assets/brand", import.meta.url).pathname,
    },
  },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: false,
    minify: false,
    assetsInlineLimit: 100_000_000,
    lib: {
      entry: new URL("./src/apps/kit.ts", import.meta.url).pathname,
      formats: ["iife"],
      name: "UfoAppKit",
      fileName: () => "assets/app-kit.js",
      cssFileName: "assets/app-kit",
    },
  },
});
