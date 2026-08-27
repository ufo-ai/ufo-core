import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { tablerMarks } from "./vite-marks";

/** The app pages: one static site per app, built as one tree — the bundle every unforked
 *  workspace is served. Each page is an ordinary vite entry importing the kit as a module, so the shared code
 *  lands in hashed chunks the pages carry once between them and a page ships only the kit it
 *  reaches. The tree is published under `apps/<digest>/` and served at a frame origin's root, so
 *  `base` is `/`: a page sits at `/<slug>/index.html` and names its chunks at `/assets/…`.
 *
 *  The tree is the served bytes and nothing else — one document per app and the `assets/` they name. The
 *  project a fork starts from is assembled by the site kind, out of the app extension's own
 *  `app.tsx` and `index.html` and the SDK that ships as the sites extension's package data, so
 *  nothing a browser never fetches is published here and the digest names exactly one serving
 *  generation.
 *
 *  A page's whole dependency surface is the name `ufo/kit` — the kit carries the JSX runtime too —
 *  so the same page source builds here and in a member's sandbox, where `vite.sdk.config.ts`'s SDK
 *  stands in for this build's chunks. That build writes elsewhere now, so its run is unordered
 *  against this one. */

const APPS = [
  "artifacts",
  "chat",
  "code",
  "issues",
  "meetings",
  "metrics",
  "radar",
  "wiki",
];
const KIT = new URL("./src/apps/kit.ts", import.meta.url).pathname;
const FRONTEND = new URL("./src/", import.meta.url).pathname;

export default defineConfig({
  root: new URL("./apps", import.meta.url).pathname,
  base: "/",
  plugins: [react({ jsxImportSource: "ufo/kit" }), tailwindcss(), tablerMarks()],
  resolve: {
    alias: [
      { find: "ufo/kit/jsx-runtime", replacement: KIT },
      { find: "ufo/kit", replacement: KIT },
      { find: "@brand", replacement: new URL("../../../assets/brand", import.meta.url).pathname },
      { find: "@", replacement: new URL("./src", import.meta.url).pathname },
    ],
  },
  build: {
    outDir: new URL("../ufo_ext_web/apps", import.meta.url).pathname,
    emptyOutDir: true,
    rollupOptions: {
      preserveEntrySignatures: "strict",
      input: Object.fromEntries(
        APPS.map((app) => [app, new URL(`./apps/${app}/index.html`, import.meta.url).pathname]),
      ),
      output: {
        manualChunks(id) {
          if (id.startsWith(FRONTEND)) return "kit";
        },
        // The one stylesheet every page links is the theme and tailwind output for all of them. Left
        // to rollup it is named after the largest module that happened to land in it — mermaid —
        // which says nothing true about 154 KB of styling the pages share. Only the sheet is
        // renamed: the mark sprites pass `emitFile` an explicit `fileName`, which rollup takes
        // over this hook, and `vite-marks` bakes those names into the bundle as literals.
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith(".css"))
            ? "assets/pages-[hash][extname]"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
});
