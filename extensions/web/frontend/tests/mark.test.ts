import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, vi } from "vitest";

import { deployment, markDeployment, markedFill } from "@/lib/mark";

const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "assets", "brand");
const MARKS = ["ufo-mark.svg", "ufo-mark-on-dark.svg"];
const FILL = /fill:\s*(#[0-9A-Fa-f]{3,8})/;

function served(answer: Response | string) {
  document.head.innerHTML = '<link rel="icon" href="/surface/web/static/assets/ufo-mark-a1.svg">';
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => (typeof answer === "string" ? new Response(answer) : answer)),
  );
  return () => document.querySelector<HTMLLinkElement>('link[rel="icon"]')!.href;
}

test("the host the page came from names the deploy", () => {
  expect(deployment("flyingobject.ai")).toBe("production");
  expect(deployment("app.flyingobject.ai")).toBe("production");
  expect(deployment("testing.flyingobject.ai")).toBe("testing");
  expect(deployment("app.testing.flyingobject.ai")).toBe("testing");
  expect(deployment("localhost")).toBe("local");
  expect(deployment("ufo-4.localhost")).toBe("local");
  expect(deployment("127.0.0.1")).toBe("local");
});

test("both brand marks state one fill, and painting one moves nothing else", () => {
  for (const name of MARKS) {
    const mark = readFileSync(join(BRAND, name), "utf8");
    const drawn = FILL.exec(mark);
    if (!drawn) throw new Error(name + " states no fill for a deploy's colour to paint");
    const painted = markedFill(mark, "#FF6700");
    expect(painted).toContain("fill: #FF6700");
    expect(markedFill(painted, drawn[1])).toBe(mark);
  }
});

test("a mark stating no fill is refused rather than hung on the tab", () => {
  expect(() => markedFill("<svg><path d='M0,0'/></svg>", "#FF6700")).toThrow();
});

test("a local page wears the mark in the local colour and production wears it as drawn", async () => {
  const mark = readFileSync(join(BRAND, "ufo-mark.svg"), "utf8");

  const local = served(mark);
  await markDeployment("ufo-4.localhost");
  expect(decodeURIComponent(local())).toContain("fill: #0095FF");

  const staging = served(mark);
  await markDeployment("app.testing.flyingobject.ai");
  expect(decodeURIComponent(staging())).toContain("fill: #FF6700");

  const production = served(mark);
  await markDeployment("app.flyingobject.ai");
  expect(production()).toContain("/surface/web/static/assets/ufo-mark-a1.svg");
});

test("a session the surface refuses leaves the built mark on the tab", async () => {
  const refused = served(new Response("unauthorized", { status: 401 }));
  await markDeployment("localhost");
  expect(refused()).toContain("/surface/web/static/assets/ufo-mark-a1.svg");
});
