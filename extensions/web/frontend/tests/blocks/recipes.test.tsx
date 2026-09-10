import { render } from "@testing-library/react";
import type { ComponentType } from "react";
import { expect, test } from "vitest";

import { RecipesPage, emptied } from "@/blocks/docs/RecipesPage";

const SECTIONS = ["Intent", "Data", "Flowchart", "Interactions", "Checklist"];
const PACKAGES = ["react", "@tabler/icons-react", "recharts"];
const GUIDES = ["README", "TEMPLATE"];
const LEVELS = 3;
const LEAVES = 3;
const BULLETS = 2;
const HANDLERS = 2;
const FRAMES = [349, 640, 1200];
const HANDLER = /\bon(?:Click|Change|CheckedChange|OpenChange|Submit|Sort|Reorder|Select)=/g;
const STATE = /\buse(?:State|Reducer)\b/;
const EMPTY = /\(\)\s*=>\s*(?:\{\s*\}|undefined)|\bnoop\b/;
const PIXELS = /width:\s*\d|\bmaxWidth\b/;

const MARKDOWN = import.meta.glob<string>("../../src/blocks/recipes/prompts/*.md", { query: "?raw", import: "default", eager: true });
const DATA = import.meta.glob<string>("../../src/blocks/recipes/data/*.json", { query: "?raw", import: "default", eager: true });
const SOURCES = import.meta.glob<string>("../../src/blocks/recipes/out/*.tsx", { query: "?raw", import: "default", eager: true });
const BLOCKS = import.meta.glob<string>("../../src/blocks/*.tsx", { query: "?raw", import: "default", eager: true });
const BUILDS = import.meta.glob<{ default: ComponentType<{ data: unknown }> }>(
  "../../src/blocks/recipes/out/*.tsx",
);

const basename = (path: string) => path.slice(path.lastIndexOf("/") + 1).replace(/\.\w+$/, "");

const names = Object.keys(MARKDOWN)
  .map(basename)
  .filter((name) => !GUIDES.includes(name))
  .sort();

const builds = Object.keys(SOURCES)
  .map(basename)
  .filter((name) => !name.startsWith("_"))
  .sort();

const EXPORTED = new Set(
  Object.values(BLOCKS)
    .flatMap((source) => [...source.matchAll(/^export (?:function|const|type) (\w+)/gm)])
    .map((match) => match[1]),
);

const family = (name: string) => /^[A-Z][a-z]+/.exec(name)?.[0] ?? "";

const FAMILIES = new Set([...EXPORTED].map(family));
FAMILIES.delete("Icon");

const title = (name: string) => name[0].toUpperCase() + name.slice(1);

const section = (source: string, heading: string) =>
  source.split(/^## /m).find((part) => part.startsWith(heading)) ?? "";

function levels(value: unknown): number {
  if (Array.isArray(value)) {
    if (value.every((item) => item === null || typeof item !== "object")) return 0;
    return 1 + Math.max(0, ...value.map(levels));
  }
  if (value !== null && typeof value === "object") {
    return 1 + Math.max(0, ...Object.values(value).map(levels));
  }
  return 0;
}

const sample = (name: string) =>
  JSON.parse(DATA[`../../src/blocks/recipes/data/${name}.json`]) as Record<string, unknown>;

for (const name of names) {
  test(`the ${name} recipe holds its five sections in order`, () => {
    const source = MARKDOWN[`../../src/blocks/recipes/prompts/${name}.md`];
    const headings = [...source.matchAll(/^##\s+(.+)$/gm)].map((match) => match[1].trim());
    expect(headings.filter((heading) => SECTIONS.includes(heading))).toEqual(SECTIONS);
  });

  test(`the ${name} recipe lists its interactions`, () => {
    const source = MARKDOWN[`../../src/blocks/recipes/prompts/${name}.md`];
    const lines = [...section(source, "Interactions").matchAll(/^- /gm)];
    expect(lines.length, name).toBeGreaterThanOrEqual(BULLETS);
  });

  test(`the ${name} flowchart names blocks that exist`, () => {
    const source = MARKDOWN[`../../src/blocks/recipes/prompts/${name}.md`];
    const flowchart = section(source, "Flowchart");
    const tokens = [...new Set([...flowchart.matchAll(/\b[A-Z][A-Za-z]+\b/g)].map((match) => match[0]))];
    for (const token of tokens.filter((candidate) => /[a-z][A-Z]/.test(candidate) && FAMILIES.has(family(candidate)))) {
      expect(EXPORTED.has(token), `${name} names ${token}`).toBe(true);
    }
    expect(tokens.filter((token) => EXPORTED.has(token)).length, name).toBeGreaterThanOrEqual(LEAVES);
  });

  test(`the ${name} recipe carries flat sample data`, () => {
    expect(Object.keys(DATA), name).toContain(`../../src/blocks/recipes/data/${name}.json`);
    expect(levels(sample(name)), name).toBeLessThanOrEqual(LEVELS);
  });

  test(`the recipes page opens the ${name} recipe`, () => {
    const { container } = render(<RecipesPage path={name} />);
    expect(container.querySelector("h1")?.textContent).toBe(title(name));
  });
}

for (const name of builds) {
  test(`the ${name} build imports only the block library`, () => {
    const source = SOURCES[`../../src/blocks/recipes/out/${name}.tsx`];
    for (const match of source.matchAll(/^import\s+(?:[^;]*?\s+from\s+)?"([^"]+)"/gm)) {
      const specifier = match[1];
      expect(PACKAGES.includes(specifier) || specifier.startsWith("@/blocks/"), specifier).toBe(true);
    }
    expect(source, name).not.toMatch(/className=/);
    expect(source, name).not.toMatch(/\bstyle=/);
    expect(names, name).toContain(name);
  });

  test(`the ${name} build holds state and handles every control`, () => {
    const source = SOURCES[`../../src/blocks/recipes/out/${name}.tsx`];
    expect(source, name).toMatch(STATE);
    expect([...source.matchAll(HANDLER)].length, name).toBeGreaterThanOrEqual(HANDLERS);
    expect(source, name).not.toMatch(EMPTY);
  });

  test(`the ${name} build states no pixel width`, () => {
    expect(SOURCES[`../../src/blocks/recipes/out/${name}.tsx`], name).not.toMatch(PIXELS);
  });

  test(`the ${name} build renders its sample data and its empty state`, async () => {
    const { default: App } = await BUILDS[`../../src/blocks/recipes/out/${name}.tsx`]();
    const data = sample(name);
    for (const shown of [data, emptied(data)]) {
      const view = render(<App data={shown} />);
      expect(view.container.firstChild, name).not.toBeNull();
      view.unmount();
    }
  });

  test(`the ${name} build renders at container widths 349, 640 and 1200 without throwing, which jsdom proves only as the absence of a width-dependent crash because it lays nothing out`, async () => {
    const { default: App } = await BUILDS[`../../src/blocks/recipes/out/${name}.tsx`]();
    const data = sample(name);
    for (const width of FRAMES) {
      const view = render(
        <div style={{ width: `${width}px` }}>
          <App data={data} />
        </div>,
      );
      expect(view.container.firstChild, `${name} at ${width}`).not.toBeNull();
      view.unmount();
    }
  });
}

test("the recipes page lists every recipe in its sub-nav", () => {
  const { container } = render(<RecipesPage />);
  const links = [...container.querySelectorAll(".blk-recipes-nav a")];
  expect(links.map((link) => link.textContent)).toEqual(names.map(title));
  expect(links.map((link) => link.getAttribute("href"))).toEqual(names.map((name) => `#/recipes/${name}`));
});
