import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

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
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith(".css"))
            ? "assets/pages-[hash][extname]"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
});
