import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { tablerMarks } from "./vite-marks";

/** The kit as the SDK a member's app page is built against in their own sandbox, where the app
 *  pages' entry chunks are not what a single-page project can resolve. It builds into the sites
 *  extension's own package, because that extension's page materialization is its only reader, and
 *  arrives beside the project as `sdk/` — `ufo/kit` aliased at `kit.js`, its chunks, stylesheet and
 *  fonts beside it.
 *
 *  `preserveEntrySignatures` is what keeps `kit.js` a module with names: an entry vite does not
 *  treat as a page has no exports worth keeping to rollup, so without it the fork's imports resolve
 *  to nothing and rollup says so in a warning the build survives. `base` is `./`, so the stylesheet
 *  names its fonts beside itself and the fork's own build re-emits them into its `dist` — an
 *  entry-less `lib` build instead inlines every font and logo as a data URI, which is 1.34 MB of
 *  stylesheet for a page whose whole first paint is 365 KB. */

const KIT = new URL("./src/apps/kit.ts", import.meta.url).pathname;

export default defineConfig({
  base: "./",
  plugins: [react({ jsxImportSource: "ufo/kit" }), tailwindcss(), tablerMarks()],
  resolve: {
    alias: [
      { find: "ufo/kit/jsx-runtime", replacement: KIT },
      { find: "@brand", replacement: new URL("../../../assets/brand", import.meta.url).pathname },
      { find: "@", replacement: new URL("./src", import.meta.url).pathname },
    ],
  },
  build: {
    outDir: new URL("../../sites/ufo_ext_sites/page/kit", import.meta.url).pathname,
    emptyOutDir: true,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: KIT,
      preserveEntrySignatures: "strict",
      output: {
        entryFileNames: "kit.js",
        chunkFileNames: "[name]-[hash].js",
        assetFileNames: (asset) =>
          asset.names.includes("style.css") ? "kit.css" : "assets/[name]-[hash][extname]",
      },
    },
  },
});
