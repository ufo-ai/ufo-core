import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  base: "./",
  build: {
    outDir: "../ufo_ext_debugger/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/surface/debug/api": "http://127.0.0.1:8710",
    },
  },
});
