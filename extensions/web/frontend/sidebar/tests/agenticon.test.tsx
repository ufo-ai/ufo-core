import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { render } from "@testing-library/react";
import { expect, test } from "vitest";

import { AgentIcon } from "@/lib/agentIcon";

const STATIC = join(import.meta.dirname, "..", "..", "..", "ufo_ext_web", "static");
const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "..", "assets", "brand");
const BRAND_MARK = "ufo-mark.svg";
const OUTLINE: Record<string, [string, Record<string, string>][]> = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "node_modules", "@tabler", "icons", "tabler-nodes-outline.json"),
    "utf8",
  ),
);

const entry = () => {
  const page = readFileSync(join(STATIC, "sidebar.html"), "utf8");
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(page);
  if (!asset) throw new Error("the built page references no module");
  return readFileSync(join(STATIC, asset[1]), "utf8");
};

const modules = () =>
  readdirSync(join(STATIC, "assets"))
    .filter((file) => file.endsWith(".js"))
    .map((file) => readFileSync(join(STATIC, "assets", file), "utf8"))
    .join("");

test("the reserved mark wears the brand file the tab icons are cut from, and no saucer", () => {
  const { container } = render(<AgentIcon name="ufo" />);
  const mask = container.querySelector<HTMLElement>(".brand-mark")!.style.mask;

  expect(mask).toContain("assets/brand/" + BRAND_MARK);
  expect(existsSync(join(BRAND, BRAND_MARK))).toBe(true);
  const favicon = /assets\/ufo-mark-[A-Za-z0-9_-]+\.svg/.exec(
    readFileSync(join(STATIC, "sidebar.html"), "utf8"),
  )![0];
  expect(entry().includes(favicon)).toBe(true);
  // `includes` rather than a matcher: a failure should name the mark, not print a megabyte of minified module.
  expect(entry().includes(OUTLINE.ufo[0][1].d)).toBe(false);
});

test("the bundle carries the marks the picker offers and none of the rest", () => {
  const bundle = modules();
  const { container } = render(<AgentIcon name="propylon" />);
  const drawn = container.querySelector("path")!.getAttribute("d")!;

  expect(bundle.includes(drawn)).toBe(true);
  expect(bundle.includes(OUTLINE.rocket[0][1].d)).toBe(false);
  expect(bundle.includes(OUTLINE.anchor[0][1].d)).toBe(false);
  expect(bundle.includes(OUTLINE.zeppelin[0][1].d)).toBe(false);
});
