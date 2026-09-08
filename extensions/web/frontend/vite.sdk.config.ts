import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { tablerMarks } from "./vite-marks";

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
