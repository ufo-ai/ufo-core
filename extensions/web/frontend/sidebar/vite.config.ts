import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { devLocalPath, outsideRootPaths } from "../dev-routing";
import { tablerMarks } from "../vite-marks";

const BASE = "/surface/web/static/";
export default defineConfig({
  root: new URL(".", import.meta.url).pathname,
  base: BASE,
  plugins: [react(), tailwindcss(), tablerMarks(), outsideRootPaths()],
  resolve: {
    alias: [
      { find: "@/App", replacement: new URL("./src/App.tsx", import.meta.url).pathname },
      {
        find: "@",
        replacement: new URL("../src", import.meta.url).pathname,
      },
      {
        find: "@brand",
        replacement: new URL("../../../../assets/brand", import.meta.url).pathname,
      },
    ],
  },
  build: {
    outDir: "../../ufo_ext_web/static",
    emptyOutDir: false,
    rollupOptions: {
      input: new URL("./sidebar.html", import.meta.url).pathname,
    },
    // Written beside the bundle and never referenced from it: the deploy uploads them to the RUM
    // application and deletes them before the image is built. The surface serves no `.map` either.
    sourcemap: "hidden",
  },
  server: {
    // The boot suite imports the built entry from `../../ufo_ext_web/static`, and a mark is read from
    // `assets/brand`; both sit above this root, which the default allow-list refuses.
    fs: { allow: ["../../../.."] },
    proxy: {
      // `changeOrigin` stays off because `open_session` answers a 303 rebuilt from the forwarded Host:
      // rewritten, the sign-in lands on the backend, which serves the gitignored built tree.
      "^/(surface/web|ext/|login|logout|join|v1/onboard)": {
        target: process.env.UFO_STACK_ORIGIN ?? "http://localhost:8080",
        changeOrigin: false,
        // This root's page is `sidebar.html`, named under `base` because vite's base middleware redirects
        // `/index.html` into the base and answers a 404 for any other html path outside it.
        bypass: (req) => {
          const path = devLocalPath(req.method ?? "GET", req.url ?? "");
          return path === "/index.html" ? `${BASE}sidebar.html` : path;
        },
      },
    },
  },
  test: {
    name: "sidebar",
    environment: "jsdom",
    setupFiles: ["../tests/setup.ts"],
    alias: {
      "ufo/kit/jsx-runtime": new URL("../src/apps/kit.ts", import.meta.url).pathname,
      "ufo/kit": new URL("../src/apps/kit.ts", import.meta.url).pathname,
    },
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    env: { TZ: "Asia/Kolkata" },
  },
});
