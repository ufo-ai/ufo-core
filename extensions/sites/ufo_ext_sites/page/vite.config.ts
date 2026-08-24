/** The config every app page project is built with, written beside the page by `deploy_website`
 *  along with the kit under `sdk/`. It is the deploy's, never the project's: a page built a year
 *  after it was written is built by today's config against today's components.
 *
 *  The build carries the project into its own output: the page goes to `dist/`, and `closeBundle`
 *  copies the source beside it under `dist/src/`. What the deploy publishes is therefore both the
 *  page and the source that wrote it, so a later read of the site starts from a project again
 *  instead of handing an agent back a minified bundle with no source.
 *
 *  `node:fs` is the one thing it imports, and a builtin is all it may import: the project resolves
 *  no package but the vite the sandbox image carries, and there is no npm install.
 *
 *  `jsxDev` is pinned off. Left to vite it follows the ambient `NODE_ENV`, and a sandbox carrying a
 *  non-production one compiles the page against `ufo/kit/jsx-dev-runtime` — a specifier the kit has
 *  no entry for, which the bare `ufo/kit` alias then resolves to a path inside `kit.js`. The page a
 *  member deploys is production output whatever the shell around it says. */
import { cpSync, mkdirSync } from "node:fs";

const HERE = new URL("./", import.meta.url).pathname;
const KIT = new URL("./sdk/kit.js", import.meta.url).pathname;
const SOURCE = ["app.tsx", "index.html"];

export default {
  esbuild: { jsx: "automatic", jsxImportSource: "ufo/kit", jsxDev: false },
  resolve: {
    alias: [
      { find: "ufo/kit/jsx-runtime", replacement: KIT },
      { find: "ufo/kit", replacement: KIT },
    ],
  },
  plugins: [
    {
      name: "ufo-carry-source",
      closeBundle() {
        mkdirSync(`${HERE}dist/src`, { recursive: true });
        for (const name of SOURCE) cpSync(`${HERE}${name}`, `${HERE}dist/src/${name}`);
      },
    },
  ],
};
