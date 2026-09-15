import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

import { cn } from "@/lib/cn";

const SHEET = readFileSync(join(import.meta.dirname, "../src/theme.css"), "utf8");
const MERGE = readFileSync(join(import.meta.dirname, "../src/lib/cn.ts"), "utf8");

/** The palette spells colours `--text-primary`/`-secondary`/`-tertiary`, which are not size steps, and
 *  `--text-…--line-height` is a pairing rather than a step. */
const NOT_A_SIZE = new Set(["primary", "secondary", "tertiary"]);

const declared = (scale: string) =>
  [...SHEET.matchAll(new RegExp(`^\\s*--${scale}-([a-z0-9-]+):`, "gm"))]
    .map((found) => found[1])
    .filter((name) => !name.includes("--line-height"));

const listed = (name: string) =>
  new Set(
    [...(new RegExp(`const ${name} = \\[([^\\]]*)\\]`, "s").exec(MERGE)?.[1] ?? "").matchAll(
      /"([^"]+)"/g,
    )].map((found) => found[1]),
  );

test("every spacing step the sheet declares is a step tailwind-merge knows", () => {
  const known = listed("SPACING");
  expect([...new Set(declared("spacing"))].filter((step) => !known.has(step))).toEqual([]);
});

test("every radius the sheet declares is a radius tailwind-merge knows", () => {
  // `lg`, `md` and `xl` are Tailwind's own steps, remapped rather than invented, so it knows them.
  const known = new Set([...listed("RADIUS"), "lg", "md", "xl"]);
  expect([...new Set(declared("radius"))].filter((step) => !known.has(step))).toEqual([]);
});

test("every type step the sheet declares is a size tailwind-merge knows", () => {
  const known = new Set([...listed("TEXT"), "xs", "sm", "base", "lg", "xl", "2xl", "3xl"]);
  const missing = [...new Set(declared("text"))]
    .filter((step) => !NOT_A_SIZE.has(step))
    .filter((step) => !known.has(step));
  expect(missing).toEqual([]);
});

test("a size and a colour spelled text-* both survive", () => {
  const said = cn("text-label", "text-ink-soft");
  expect(said).toContain("text-label");
  expect(said).toContain("text-ink-soft");
  for (const step of listed("TEXT")) {
    expect(cn(`text-${step} text-ink`)).toContain(`text-${step}`);
  }
});

test("two sizes still conflict, and the last one wins", () => {
  expect(cn("text-label", "text-title")).toBe("text-title");
  expect(cn("gap-2xl", "gap-8xl")).toBe("gap-8xl");
  expect(cn("rounded-panel", "rounded-card")).toBe("rounded-card");
});
