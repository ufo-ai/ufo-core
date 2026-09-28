import { chmodSync, cpSync, mkdirSync, readdirSync, rmSync } from "node:fs";

const HERE = new URL("./", import.meta.url).pathname;
const KIT = new URL("./sdk/kit.js", import.meta.url).pathname;
const BLOCKS = new URL("./sdk/blocks.js", import.meta.url).pathname;
const SOURCE_MODE = 0o644;

export default {
  base: "./",
  esbuild: { jsx: "automatic", jsxImportSource: "ufo/kit", jsxDev: false },
  resolve: {
    alias: [
      { find: "ufo/kit/jsx-runtime", replacement: KIT },
      { find: "ufo/kit", replacement: KIT },
      { find: "ufo/blocks", replacement: BLOCKS },
    ],
  },
  plugins: [
    {
      name: "ufo-carry-source",
      closeBundle() {
        rmSync(`${HERE}dist/src`, { recursive: true, force: true });
        mkdirSync(`${HERE}dist/src`, { recursive: true });
        for (const entry of readdirSync(HERE, { withFileTypes: true })) {
          if (entry.isFile() && entry.name !== "vite.config.ts") {
            cpSync(`${HERE}${entry.name}`, `${HERE}dist/src/${entry.name}`);
            chmodSync(`${HERE}dist/src/${entry.name}`, SOURCE_MODE);
          }
        }
      },
    },
  ],
};
