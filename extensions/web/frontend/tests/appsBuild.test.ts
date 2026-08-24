// @vitest-environment node
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

const TREE = join(import.meta.dirname, "..", "..", "ufo_ext_web", "apps");
const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");
const KIT = join(EXTENSIONS, "sites", "ufo_ext_sites", "page", "kit");
const APPS = readdirSync(EXTENSIONS, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name.startsWith("app_"))
  .map((entry) => entry.name.slice("app_".length))
  .sort();
const SHEET_CEILING_BYTES = 300_000;
const FIRST_PAINT_CEILING_BYTES = 1_500_000;
const BABEL = "availablePresets";

const readFrom = (root: string, ...parts: string[]): string => {
  const file = join(root, ...parts);
  if (!existsSync(file)) throw new Error(`${file} is unbuilt: npm run build`);
  return readFileSync(file, "utf8");
};

const read = (...parts: string[]): string => readFrom(TREE, ...parts);

const appSource = (app: string): string =>
  readFileSync(
    join(EXTENSIONS, `app_${app}`, `ufo_ext_app_${app}`, "skills", `app-${app}-home`, "app.tsx"),
    "utf8",
  );

const scripts = (root: string, directory: string): string[] =>
  readdirSync(join(root, directory), { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => join(directory, entry.name));

const kitExports = (): Set<string> => {
  const clause = /export\s*\{([^}]*)\}/.exec(readFrom(KIT, "kit.js"));
  if (!clause) throw new Error("kit.js exports nothing");
  return new Set(clause[1].split(",").map((held) => held.split(" as ").at(-1)!.trim()));
};

const kitImports = (source: string): string[] =>
  [...source.matchAll(/import\s*\{([^}]*)\}\s*from\s*"ufo\/kit"/g)].flatMap((held) =>
    held[1]
      .split(",")
      .map((name) => name.trim())
      .filter((name) => name.length > 0),
  );

test("the served tree is the five pages and the chunks they name, and nothing else", () => {
  const held = readdirSync(TREE, { withFileTypes: true });
  expect(held.filter((entry) => entry.isFile())).toEqual([]);
  expect(
    held
      .map((entry) => entry.name)
      .filter((name) => name !== "assets")
      .sort(),
  ).toEqual(APPS);
  for (const app of APPS) {
    expect(readdirSync(join(TREE, app))).toEqual(["index.html"]);
  }
});

test("the tree publishes no source and no SDK — a browser fetches neither", () => {
  const suffixes = new Set<string>();
  const walk = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(join(directory, entry.name));
      else suffixes.add(entry.name.slice(entry.name.lastIndexOf(".")));
    }
  };
  walk(TREE);
  expect([...suffixes].filter((held) => held === ".ts" || held === ".tsx")).toEqual([]);
  expect(existsSync(join(TREE, "sdk"))).toBe(false);
});

test.each(APPS)("%s is a built page naming its module and stylesheet under /assets", (app) => {
  const page = read(app, "index.html");
  const script = /<script type="module"[^>]* src="\/(assets\/[^"]+\.js)"/.exec(page);
  const sheet = /<link rel="stylesheet"[^>]* href="\/(assets\/pages-[^"]+\.css)"/.exec(page);
  if (!script) throw new Error(`${app}/index.html names no module`);
  if (!sheet) throw new Error(`${app}/index.html names no stylesheet under assets/pages-`);
  expect(existsSync(join(TREE, script[1]))).toBe(true);
  expect(existsSync(join(TREE, sheet[1]))).toBe(true);
  expect(page).not.toContain("app.tsx");
});

test.each(APPS)("%s loads a bounded precompiled first paint", (app) => {
  const page = read(app, "index.html");
  const assets = [
    ...page.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:css|js))"/g),
  ].map((match) => match[1]);
  expect(assets.some((asset) => /^assets\/kit-[^/]+\.js$/.test(asset))).toBe(true);
  expect(new Set(assets).size).toBe(assets.length);
  const bytes =
    Buffer.byteLength(page) +
    assets.reduce((total, asset) => total + readFileSync(join(TREE, asset)).byteLength, 0);
  expect(bytes).toBeLessThan(FIRST_PAINT_CEILING_BYTES);
});

test("the one stylesheet the pages share is named for the pages, not for mermaid", () => {
  const sheets = readdirSync(join(TREE, "assets")).filter((name) => name.endsWith(".css"));
  expect(sheets).toHaveLength(1);
  expect(sheets[0]).toMatch(/^pages-[A-Za-z0-9_-]+\.css$/);
  const linked = new Set(
    APPS.map((app) => /href="\/assets\/([^"]+\.css)"/.exec(read(app, "index.html"))?.[1]),
  );
  expect([...linked]).toEqual([sheets[0]]);
});

test("the mark sprites keep the names the bundle bakes in as literals", () => {
  const sprites = readdirSync(join(TREE, "assets")).filter((name) => name.startsWith("tabler-"));
  expect(sprites.length).toBeGreaterThan(0);
  const bundle = scripts(TREE, "assets")
    .map((file) => readFileSync(join(TREE, file), "utf8"))
    .join("");
  for (const sprite of sprites) {
    expect(sprite).toMatch(/^tabler-[a-z]-[0-9a-f]{8}\.svg$/);
    expect(bundle).toContain(sprite);
  }
});

test("the SDK exports every name the app pages import, and the JSX runtime they compile against", () => {
  const held = kitExports();
  for (const app of APPS) {
    for (const name of kitImports(appSource(app))) {
      expect(held).toContain(name);
    }
  }
  expect(held).toContain("jsx");
  expect(held).toContain("jsxs");
  expect(held).toContain("Fragment");
});

test("the SDK stylesheet names its fonts beside itself instead of carrying them", () => {
  const sheet = readFrom(KIT, "kit.css");
  expect(sheet).not.toContain("data:font");
  expect(sheet).toContain("url(./assets/");
  expect(sheet.length).toBeLessThan(SHEET_CEILING_BYTES);
});

test("no chunk a page or a fork loads carries a compiler", () => {
  const carrying = [
    ...scripts(TREE, "assets").map((file) => join(TREE, file)),
    ...scripts(KIT, ".").map((file) => join(KIT, file)),
  ].filter((file) => readFileSync(file, "utf8").includes(BABEL));
  expect(carrying).toEqual([]);
});
