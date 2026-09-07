/** The config every app page project is built with, written beside the page by `deploy_website`
 *  along with the kit under `sdk/`. It is the deploy's, never the project's: a page built a year
 *  after it was written is built by today's config against today's components.
 *
 *  The build carries the project into its own output: the page goes to `dist/`, and `closeBundle`
 *  copies the source beside it under `dist/src/`. What the deploy publishes is therefore both the
 *  page and the source that wrote it, so a later read of the site starts from a project again
 *  instead of handing an agent back a minified bundle with no source.
 *
 *  Every file the project holds is carried, not a fixed pair: the radar page reads `tour.md` beside
 *  it, and a source the redeploy dropped would fail the build after it. The deploy's own config is
 *  left behind, because the next build is written its own.
 *
 *  `node:fs` is the one thing it imports, and a builtin is all it may import: the project resolves
 *  no package but the vite the sandbox image carries, and there is no npm install.
 *
 *  `jsxDev` is pinned off. Left to vite it follows the ambient `NODE_ENV`, and a sandbox carrying a
 *  non-production one compiles the page against `ufo/kit/jsx-dev-runtime` — a specifier the kit has
 *  no entry for, which the bare `ufo/kit` alias then resolves to a path inside `kit.js`. The page a
 *  member deploys is production output whatever the shell around it says. */
import { chmodSync, cpSync, mkdirSync, readdirSync, rmSync } from "node:fs";

const HERE = new URL("./", import.meta.url).pathname;
const KIT = new URL("./sdk/kit.js", import.meta.url).pathname;
/** What the deploy writes beside the project rather than what the project holds: neither belongs in
 *  the source a later read pulls back, and the next deploy writes both again. Everything else the
 *  directory carries — the page, its design — is the member's and rides along. */
const DEPLOY_WRITTEN = new Set(["vite.config.ts", "preview.html"]);
/** The mode the carried source is left at. A project scaffolded out of the read-only skills
 *  tree arrives at 0444, and `cpSync` copies the mode it finds: carried unchanged, the first
 *  deploy's own output is what the next deploy cannot overwrite, and a Rebuild that pulls this
 *  source back gets files it cannot edit. */
const SOURCE_MODE = 0o644;

export default {
  base: "./",
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
        rmSync(`${HERE}dist/src`, { recursive: true, force: true });
        mkdirSync(`${HERE}dist/src`, { recursive: true });
        for (const entry of readdirSync(HERE, { withFileTypes: true })) {
          if (entry.isFile() && !DEPLOY_WRITTEN.has(entry.name)) {
            cpSync(`${HERE}${entry.name}`, `${HERE}dist/src/${entry.name}`);
            chmodSync(`${HERE}dist/src/${entry.name}`, SOURCE_MODE);
          }
        }
      },
    },
  ],
};
