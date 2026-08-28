import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

import { cn } from "@/lib/cn";

const SHEET = readFileSync(join(import.meta.dirname, "../src/theme.css"), "utf8");
const MERGE = readFileSync(join(import.meta.dirname, "../src/lib/cn.ts"), "utf8");

/** The palette's own names are spelled `--text-primary` / `--text-secondary` / `--text-tertiary`
 *  but are colours, not sizes, and `--text-…--line-height` is a pairing rather than a step. */
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

test("a size stated beside a colour survives, which is the failure the lists exist to stop", () => {
  for (const step of listed("TEXT")) {
    expect(cn(`text-${step} text-ink`)).toContain(`text-${step}`);
  }
});

test("a caller's own step still replaces the one a component states", () => {
  expect(cn("text-label", "text-figure")).toBe("text-figure");
  expect(cn("gap-2xl", "gap-8xl")).toBe("gap-8xl");
  expect(cn("rounded-panel", "rounded-card")).toBe("rounded-card");
});
