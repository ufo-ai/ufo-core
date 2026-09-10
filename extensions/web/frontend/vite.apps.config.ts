import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { outsideRootPaths } from "./dev-routing";
import { tablerMarks } from "./vite-marks";

const APPS = [
  "artifacts",
  "chat",
  "code",
  "issues",
  "meetings",
  "metrics",
  "notification",
  "radar",
  "wiki",
];
const KIT = new URL("./src/apps/kit.ts", import.meta.url).pathname;
const FRONTEND = new URL("./src/", import.meta.url).pathname;

export default defineConfig({
  root: new URL("./apps", import.meta.url).pathname,
  base: "/",
  plugins: [react({ jsxImportSource: "ufo/kit" }), tailwindcss(), tablerMarks(), outsideRootPaths()],
  resolve: {
    alias: [
      // The kit's runtime is React's, so while developing its dev runtime is React's dev runtime.
      { find: "ufo/kit/jsx-dev-runtime", replacement: "react/jsx-dev-runtime" },
      { find: "ufo/kit/jsx-runtime", replacement: KIT },
      { find: "ufo/kit", replacement: KIT },
      { find: "@brand", replacement: new URL("../../../assets/brand", import.meta.url).pathname },
      { find: "@", replacement: new URL("./src", import.meta.url).pathname },
    ],
  },
  // The shell's dev server runs beside this one off the same package, and vite keys the pre-bundled
  // dependency cache by that package: sharing it leaves each server serving the other's hashes.
  cacheDir: new URL("./node_modules/.vite-apps", import.meta.url).pathname,
  server: {
    // Every page's entry sits in its own extension beside the frontend, outside the root vite serves
    // by default, and the ingress dials this server by its own name, which vite's host check refuses.
    fs: { allow: ["../../../.."] },
    allowedHosts: true,
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
        manualChunks(id, { getModuleInfo }) {
          if (!id.startsWith(FRONTEND)) return;
          // A view nothing imports statically keeps the chunk its `lazy` asked for: folded into the
          // kit, its terminal emulator would land in the first paint of every page.
          const held = getModuleInfo(id);
          if (held && held.importers.length === 0 && held.dynamicImporters.length > 0) return;
          return "kit";
        },
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith(".css"))
            ? "assets/pages-[hash][extname]"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
});
