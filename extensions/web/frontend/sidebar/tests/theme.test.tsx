import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, useStreamFake, wire } from "../../tests/harness";

const STATIC = join(import.meta.dirname, "..", "..", "..", "ufo_ext_web", "static");
const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "..", "assets", "brand");

const page = () => readFileSync(join(STATIC, "sidebar.html"), "utf8");

const packedStyles = (): string => {
  const asset = /href="\/surface\/web\/static\/(assets\/[^"]+\.css)"/.exec(page());
  if (!asset) throw new Error("the built page references no stylesheet");
  return readFileSync(join(STATIC, asset[1]), "utf8").replace(/\s+/g, "");
};

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the page height tracks the visible viewport and respects device insets", () => {
  const css = packedStyles();
  expect(css).toContain(".h-dvh{height:100dvh}");
  expect(css).toContain("box-sizing:border-box;height:100dvh;padding-top:env(safe-area-inset-top)");
  expect(css).toContain("env(safe-area-inset-bottom)");
  expect(page()).toContain("viewport-fit=cover");
});

test("the sidebar heads itself with the drawn ufo mark", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const marks = await screen.findAllByRole("img", { name: "ufo" });
  const brand = marks.find((mark) => mark.getAttribute("style")?.includes("ufo-mark.svg"))!;
  expect(brand.getAttribute("class")).not.toContain("tracking");
  expect(brand.getAttribute("style")).toContain("ufo-mark.svg");
  expect(page()).toContain('<body data-shell="sidebar">');
  expect(packedStyles()).toContain(
    'body[data-shell=sidebar]{--size-wordmark:18px;--size-logo:72px}',
  );
});

test("the favicons use the exact light and dark brand marks", () => {
  const light = /href="\/surface\/web\/static\/(assets\/ufo-mark-[^"]+\.svg)"[^>]+media="\(prefers-color-scheme: light\)"/.exec(page());
  const dark = /href="\/surface\/web\/static\/(assets\/ufo-mark-on-dark-[^"]+\.svg)"[^>]+media="\(prefers-color-scheme: dark\)"/.exec(page());
  if (!light || !dark) throw new Error("the built page references no light and dark favicons");
  expect(readFileSync(join(STATIC, light[1]), "utf8")).toBe(
    readFileSync(join(BRAND, "ufo-mark.svg"), "utf8"),
  );
  expect(readFileSync(join(STATIC, dark[1]), "utf8")).toBe(
    readFileSync(join(BRAND, "ufo-mark-on-dark.svg"), "utf8"),
  );
});
