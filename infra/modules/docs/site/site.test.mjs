import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import test from "node:test";

const dist = new URL("dist/", import.meta.url);
const brand = new URL("../../../../assets/brand/", import.meta.url);
const APEX = "https://docs.ufo.ai";
const NOT_FOUND = "/404";

async function pages(directory = dist, prefix = "/") {
  const found = new Map();
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === "pagefind" || entry.name === "_astro") continue;
    if (entry.isDirectory()) {
      for (const [route, html] of await pages(
        new URL(`${entry.name}/`, directory),
        `${prefix}${entry.name}/`,
      )) {
        found.set(route, html);
      }
    } else if (entry.name === "index.html") {
      found.set(prefix, await readFile(new URL(entry.name, directory), "utf8"));
    } else if (entry.name.endsWith(".html")) {
      found.set(`${prefix}${entry.name.slice(0, -".html".length)}`, await readFile(new URL(entry.name, directory), "utf8"));
    }
  }
  return found;
}

const built = await pages();
assert.ok(
  built.size > 1,
  "dist holds no pages — run `pnpm -C infra/modules/docs/site run build` before this suite",
);

function links(html) {
  return [...html.matchAll(/href="(\/[^"#?]*)(?:[#?][^"]*)?"/g)].map(([, path]) => path);
}

async function served(path) {
  if (built.has(path)) return true;
  if (!path.endsWith("/") && built.has(`${path}/`)) return true;
  try {
    return (await stat(new URL(`.${path}`, dist))).isFile();
  } catch {
    return false;
  }
}

test("every internal link the site draws is a page or a file it built", async () => {
  const broken = [];
  for (const [route, html] of built) {
    for (const path of links(html)) {
      if (!(await served(path))) broken.push(`${route} -> ${path}`);
    }
  }
  assert.deepEqual(broken, []);
});

test("every page is reached from another page", () => {
  const reached = new Set();
  for (const [route, html] of built) {
    for (const path of links(html)) {
      if (path !== route) reached.add(path.endsWith("/") ? path : `${path}/`);
    }
  }
  const orphans = [...built.keys()].filter(
    (route) => route !== "/" && route !== NOT_FOUND && !reached.has(route),
  );
  assert.deepEqual(orphans, []);
});

test("every canonical names production, so the testing door is never the indexed copy", () => {
  for (const [route, html] of built) {
    const canonical = html.match(/<link rel="canonical" href="([^"]+)"/);
    assert.ok(canonical, `${route} declares no canonical`);
    assert.ok(
      canonical[1].startsWith(APEX),
      `${route} points its canonical at ${canonical[1]}`,
    );
  }
});

test("the mark is served from its one home", async () => {
  for (const [served_as, source] of [
    ["favicon.svg", "ufo-mark.svg"],
    ["favicon-dark.svg", "ufo-mark-on-dark.svg"],
  ]) {
    assert.equal(
      await readFile(new URL(served_as, dist), "utf8"),
      await readFile(new URL(source, brand), "utf8"),
    );
  }
});
