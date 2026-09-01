import { defineConfig, mergeConfig } from "vite";

import { historyFixture } from "./history-fixture";
import portal from "./vite.config";

export default mergeConfig(
  portal,
  defineConfig({
    plugins: [historyFixture()],
    server: { host: "127.0.0.1" },
  }),
);
