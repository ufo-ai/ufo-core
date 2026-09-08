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
      "@brand": new URL("../../../assets/brand", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: true,
    sourcemap: "hidden",
  },
  server: {
    fs: { allow: ["../../.."] },
    proxy: {
      "^/(surface/web|ext/|login|logout|join|v1/onboard)": {
        target: process.env.UFO_STACK_ORIGIN ?? "http://localhost:8080",
        changeOrigin: false,
        bypass: (req) => devLocalPath(req.method ?? "GET", req.url ?? ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    alias: {
      "ufo/kit/jsx-runtime": new URL("./src/apps/kit.ts", import.meta.url).pathname,
      "ufo/kit": new URL("./src/apps/kit.ts", import.meta.url).pathname,
    },
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    env: { TZ: "Asia/Kolkata" },
    projects: [
      { extends: true, test: { name: "lanes" } },
      "./sidebar/vite.config.ts",
    ],
  },
});
