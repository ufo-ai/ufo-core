import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { outsideRootPaths } from "./dev-routing";
import { tablerMarks } from "./vite-marks";

/** The playground draws the portal's own components and the chat simulator mounts the real `Chat`,
 *  so building it beside `index.html` would hoist the shared half into a chunk the portal's entry no
 *  longer holds. It builds alone for that reason: a design surface changes no shipped bundle.
 *
 *  Its chunks nest under `design/assets/` rather than beside the portal's, because the surface reads
 *  `static/assets` whole into memory at import and publishes every entry to the fleet blob store.
 *  Under this directory they are read and served only where `UFO_WEB_DESIGN_SURFACES` opens the page
 *  that names them, so the deploy withholding the page holds none of the megabytes behind it.
 *
 *  The page stays at the root of the tree beside `blocks.html`, which is the name the surface serves
 *  it under and the URL a dev server answers.
 *
 *  It emits no source map. The deploy uploads `static/assets` to the RUM application and deletes
 *  `static/assets/*.map` before the image is built, and this page declares no RUM block, so the
 *  14 MB of maps this graph wrote reached no reader and would now ship in the image as well. */
export default defineConfig({
  base: "/surface/web/static/",
  plugins: [react(), tailwindcss(), tablerMarks(), outsideRootPaths()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      "@brand": new URL("../../../assets/brand", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "../ufo_ext_web/static",
    assetsDir: "design/assets",
    emptyOutDir: false,
    rollupOptions: {
      input: new URL("./playground.html", import.meta.url).pathname,
    },
    sourcemap: false,
  },
  server: {
    fs: { allow: ["../../.."] },
  },
});
