import { defineConfig } from "vite";

import { devLocalPath } from "./dev-routing";

export default defineConfig({
  base: "/surface/web/static/",
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: true,
  },
  server: {
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
    include: ["tests/**/*.test.ts"],
  },
});
