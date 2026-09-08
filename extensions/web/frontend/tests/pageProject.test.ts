// @vitest-environment node
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, test } from "vitest";

/** A staged project must be reached by its resolved path: through a symlinked `/tmp`, which is what
 *  macOS hands out, rollup refuses the html emit. `vite` is this package's devDependency, not Python's. */

const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");
const PAGE = join(EXTENSIONS, "sites", "ufo_ext_sites", "page");
const VITE = join(import.meta.dirname, "..", "node_modules", ".bin", "vite");
const APPS = readdirSync(EXTENSIONS, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name.startsWith("app_"))
  .map((entry) => entry.name.slice("app_".length))
  .sort();
const BUILD_CEILING_MS = 240_000;
const PROJECT = ["app.tsx", "index.html", "tour.md"];

const homeSkill = (app: string): string =>
  join(EXTENSIONS, `app_${app}`, `ufo_ext_app_${app}`, "skills", `app-${app}-home`);

const materialized = (source: string): string => {
  const site = resolve(mkdtempSync(join(tmpdir(), "ufo-page-")));
  const project = join(site, "src");
  mkdirSync(project, { recursive: true });
  cpSync(join(PAGE, "kit"), join(project, "sdk"), { recursive: true });
  for (const name of PROJECT) cpSync(join(source, name), join(project, name));
  cpSync(join(PAGE, "vite.config.ts"), join(project, "vite.config.ts"));
  return project;
};

const SPRITE_NAME = /tabler-[a-z]-[0-9a-f]{8}\.svg/g;

/** The kit reads a mark through a reference the build resolves, so a member's own build emits the
 *  sprites their page draws rather than naming ours, which no origin of theirs answers. */
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
    const forked = built(materialized(homeSkill("radar")));
    const assets = readdirSync(join(forked, "..", "assets"));
    expect(assets.filter((file) => file.startsWith("tabler-")).length).toBeGreaterThan(0);
    built(materialized(forked));
  },
  BUILD_CEILING_MS,
);
