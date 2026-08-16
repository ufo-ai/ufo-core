import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { devLocalPath } from "./dev-routing";

export default defineConfig({
  base: "/surface/web/static/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: true,
  },
  server: {
    // The boot suite imports the built entry from `../ufo_ext_web/static`, one directory above
    // this root, which the default allow-list refuses.
    fs: { allow: [".."] },
    proxy: {
      // `changeOrigin` stays off because `open_session` answers a 303 rebuilt from the forwarded
      // Host: rewritten, the sign-in lands on the backend, which serves the gitignored built tree
      // this server exists to bypass. `tests/viteconfig.test.ts` holds that and the rest of this
      // rule; `dev-routing.ts` owns which requests stay here.
      "^/surface/web": {
        target: "http://localhost:8710",
        changeOrigin: false,
        bypass: (req) => devLocalPath(req.method ?? "GET", req.url ?? ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // A half-hour offset with no daylight rule: a run under it proves a wall clock is converted
    // to an instant rather than passed along, which a run under UTC cannot tell apart.
    env: { TZ: "Asia/Kolkata" },
  },
});
