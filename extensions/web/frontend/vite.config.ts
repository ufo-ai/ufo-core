import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { devLocalPath } from "./dev-routing";
import { tablerMarks } from "./vite-marks";

const BASE = "/surface/web/static/";
export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss(), tablerMarks()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      // The brand's own artwork, drawn once and worn by every surface. The portal reads it where
      // the brand keeps it, so no copy of a mark can drift from the one the favicons are cut from.
      "@brand": new URL("../../../assets/brand", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: true,
    // Maps for the stack traces Datadog reports, written beside the bundle and never referenced
    // from it: the deploy uploads them to the RUM application and deletes them before the image is
    // built, so a member's browser is never offered the portal's source. The surface serves no
    // `.map` either — its media-type table declares none.
    sourcemap: "hidden",
  },
  server: {
    // The boot suite imports the built entry from `../ufo_ext_web/static`, and a mark is read from
    // `assets/brand` at the tree's root; both sit above this root, which the default allow-list
    // refuses.
    fs: { allow: ["../../.."] },
    proxy: {
      // `changeOrigin` stays off because `open_session` answers a 303 rebuilt from the forwarded
      // Host: rewritten, the sign-in lands on the backend, which serves the gitignored built tree
      // this server exists to bypass. `tests/viteconfig.test.ts` holds that and the rest of this
      // rule; `dev-routing.ts` owns which requests stay here.
      "^/(surface/web|ext/)": {
        target: process.env.UFO_SERVE_ORIGIN ?? "http://localhost:8710",
        changeOrigin: false,
        bypass: (req) => devLocalPath(req.method ?? "GET", req.url ?? ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    // An app page's whole dependency surface, resolved for the suite that imports the real
    // pages as modules. Only `vite.apps.config.ts` and the SDK build carry it otherwise; the
    // portal's own bundle names neither.
    alias: {
      "ufo/kit/jsx-runtime": new URL("./src/apps/kit.ts", import.meta.url).pathname,
      "ufo/kit": new URL("./src/apps/kit.ts", import.meta.url).pathname,
    },
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // A half-hour offset with no daylight rule: a run under it proves a wall clock is converted
    // to an instant rather than passed along, which a run under UTC cannot tell apart.
    env: { TZ: "Asia/Kolkata" },
  },
});
