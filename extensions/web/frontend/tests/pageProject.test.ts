// @vitest-environment node
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, test } from "vitest";

/** The app page fork loop, built with the real vite against the real kit.
 *
 *  The project's three fixed parts come from where the site kind reads them — the app extension's
 *  home skill for `app.tsx` and `index.html`, the sites extension's package for `vite.config.ts`
 *  and the kit — so this assembles exactly what `source.py` writes into a sandbox and then does
 *  what the skill tells an agent to do with it. It lives in this suite because `vite` is this
 *  package's devDependency: the Python suite has no node_modules to run a build with.
 *
 *  A staged project must be reached by its RESOLVED path. Through a symlinked `/tmp` — which is
 *  what macOS hands out — rollup refuses the html emit, because the path it derives is neither
 *  absolute nor relative to what it expected. */

const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");
const PAGE = join(EXTENSIONS, "sites", "ufo_ext_sites", "page");
const VITE = join(import.meta.dirname, "..", "node_modules", ".bin", "vite");
const APPS = readdirSync(EXTENSIONS, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name.startsWith("app_"))
  .map((entry) => entry.name.slice("app_".length))
  .sort();
const BUILD_CEILING_MS = 240_000;
const PROJECT = ["app.tsx", "index.html"];

const homeSkill = (app: string): string =>
  join(EXTENSIONS, `app_${app}`, `ufo_ext_app_${app}`, "skills", `app-${app}-home`);

const materialized = (source: string, page: string): string => {
  const site = resolve(mkdtempSync(join(tmpdir(), "ufo-page-")));
  const project = join(site, "src");
  mkdirSync(project, { recursive: true });
  cpSync(join(PAGE, "kit"), join(project, "sdk"), { recursive: true });
  cpSync(source, join(project, "app.tsx"));
  cpSync(page, join(project, "index.html"));
  cpSync(join(PAGE, "vite.config.ts"), join(project, "vite.config.ts"));
  return project;
};

const SPRITE_NAME = /tabler-[a-z]-[0-9a-f]{8}\.svg/g;

/** One `vite build` in the project, answering the `dist/src` a redeploy makes the next read's
 *  source of record. Every sprite the built page names is a file of its own tree: the kit reads a
 *  mark through a reference the build resolves, so a member's own build emits the sprites their
 *  page draws rather than naming ours, which no origin of theirs answers. */
const built = (project: string): string => {
  execFileSync(VITE, ["build"], { cwd: project, stdio: "pipe" });
  const dist = join(project, "dist");
  expect(readdirSync(dist).sort()).toEqual(["assets", "index.html", "src"]);
  const emitted = new Set(readdirSync(join(dist, "assets")));
  const named = new Set(
    readdirSync(join(dist, "assets"))
      .filter((file) => file.endsWith(".js"))
      .flatMap((file) => [...readFileSync(join(dist, "assets", file), "utf8").matchAll(SPRITE_NAME)])
      .map(([sprite]) => sprite),
  );
  for (const sprite of named) expect(emitted, sprite).toContain(sprite);
  const page = readFileSync(join(dist, "index.html"), "utf8");
  expect(page).toMatch(/<script type="module"[^>]* src="\.\/assets\/[^"]+\.js"/);
  expect(page).toMatch(/<link rel="stylesheet"[^>]* href="\.\/assets\/[^"]+\.css"/);
  expect(page).not.toContain("sdk/");
  expect(page).not.toContain("app.tsx");
  expect(readdirSync(join(dist, "assets")).length).toBeGreaterThan(0);
  expect(readdirSync(join(dist, "src")).sort()).toEqual(PROJECT);
  return join(dist, "src");
};

test("the package ships the config a materialized project is built with", () => {
  const config = readFileSync(join(PAGE, "vite.config.ts"), "utf8");
  expect(config).not.toMatch(/\brequire\(/);
  expect([...config.matchAll(/^import\s[^"]*"([^"]+)"/gm)].map((held) => held[1])).toEqual([
    "node:fs",
  ]);
  expect(config).toContain('new URL("./sdk/kit.js", import.meta.url).pathname');
  expect(config).toContain('jsxImportSource: "ufo/kit"');
  expect(config).toContain("jsxDev: false");
  expect(existsSync(join(PAGE, "kit", "kit.js"))).toBe(true);
  expect(existsSync(join(PAGE, "kit", "kit.css"))).toBe(true);
});

test.each(APPS)("%s's home skill holds the project's page and its source", (app) => {
  const page = readFileSync(join(homeSkill(app), "index.html"), "utf8");
  expect(page).toContain('<script type="module" src="./app.tsx"></script>');
  expect(page).toContain('<link rel="stylesheet" href="./sdk/kit.css">');
  expect(page).toMatch(/<title>[^<]+<\/title>/);
  expect(existsSync(join(homeSkill(app), "app.tsx"))).toBe(true);
});

test(
  "a materialized project builds the page and carries the source the next read starts from",
  () => {
    // radar of the five, because its page reads a mark through the kit: its build is the one that
    // proves a sprite reaches a member's own tree rather than naming an origin only ours answers.
    const home = homeSkill("radar");
    const forked = built(materialized(join(home, "app.tsx"), join(home, "index.html")));
    const assets = readdirSync(join(forked, "..", "assets"));
    expect(assets.filter((file) => file.startsWith("tabler-")).length).toBeGreaterThan(0);
    built(materialized(join(forked, "app.tsx"), join(forked, "index.html")));
  },
  BUILD_CEILING_MS,
);
