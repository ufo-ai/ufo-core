import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { Playground } from "@/playground/Playground";
import { CHAT_PAGES } from "@/playground/pages";

const SRC = join(import.meta.dirname, "..", "src");
const UI = join(SRC, "components", "ui");
const CHAT_SURFACE = ["views/Chat.tsx", "views/ChatPane.tsx", "kernel/messages.tsx"];

/** Reached by the chat surface, but portal chrome a conversation stands in rather than a part a
 *  turn is drawn from. Documenting these here would say the chat surface owns them. */
const CHROME = [
  "breadcrumb.tsx",
  "dropdown-menu.tsx",
  "field.tsx",
  "filter.tsx",
  "item.tsx",
  "resizable.tsx",
  "select.tsx",
  "sheet.tsx",
  "toggle-group.tsx",
  "tooltip.tsx",
];

const resolve = (spec: string): string | null => {
  const base = join(SRC, spec.slice(2));
  return [".tsx", ".ts"].map((ext) => base + ext).find((path) => existsSync(path)) ?? null;
};

const componentsTheChatSurfaceDraws = (): Set<string> => {
  const walked = new Set<string>();
  const drawn = new Set<string>();
  const walk = (path: string | null) => {
    if (!path || walked.has(path)) return;
    walked.add(path);
    for (const found of readFileSync(path, "utf8").matchAll(/from "(@\/[^"]+)"/g)) {
      const spec = found[1];
      if (spec.startsWith("@/components/ui/")) drawn.add(spec.slice(16) + ".tsx");
      walk(resolve(spec));
    }
  };
  for (const entry of CHAT_SURFACE) walk(join(SRC, entry));
  return drawn;
};

const axisKeys = (component: string, axis: string): string[] => {
  const source = readFileSync(join(UI, component), "utf8");
  const block = new RegExp(`\\n( +)${axis}: \\{\\n([\\s\\S]*?)\\n\\1\\},`).exec(source);
  if (!block) throw new Error(`${component} declares no ${axis} axis`);
  const key = new RegExp(`^${block[1]}  "?([A-Za-z][\\w-]*)"?:`, "gm");
  return [...block[2].matchAll(key)].map((found) => found[1]);
};

const open = (slug: string) => {
  cleanup();
  location.hash = "#/" + slug;
  return render(<Playground />);
};

test("every component the chat surface draws is documented, or named as chrome", () => {
  const documented = new Set(CHAT_PAGES.flatMap((page) => page.modules));
  const drawn = componentsTheChatSurfaceDraws();
  expect(drawn.size).toBeGreaterThan(20);
  expect([...drawn].filter((module) => !documented.has(module) && !CHROME.includes(module))).toEqual(
    [],
  );
  expect(CHROME.filter((module) => !drawn.has(module))).toEqual([]);
  expect(
    [...documented].filter(
      (module) => !existsSync(module.includes("/") ? join(SRC, module) : join(UI, module)),
    ),
  ).toEqual([]);
});

test("a component's page shows every state it has at once, each named and grouped", () => {
  for (const page of CHAT_PAGES) {
    open(page.slug);
    expect(screen.getByRole("heading", { level: 1, name: page.name })).toBeTruthy();
    const titles = page.examples.map((shown) => shown.title);
    expect(new Set(titles).size).toBe(titles.length);
    /** All of them standing at the same time is the point of the page: a state behind a toggle is
     *  one the reader has to already know about to go looking for. */
    const drawn = screen.getAllByRole("figure");
    expect(drawn.length).toBe(page.examples.length);
    const captions = drawn.map((figure) => [...figure.querySelectorAll("figcaption span")]);
    expect(captions.map((parts) => parts[0]?.textContent)).toEqual(titles);
    for (const group of new Set(page.examples.map((shown) => shown.group))) {
      expect(screen.getByRole("heading", { level: 2, name: group })).toBeTruthy();
    }
  }
  cleanup();
});

test("an axis reads across one run of states rather than down several", () => {
  const split = CHAT_PAGES.flatMap((page) =>
    (page.axes ?? [])
      .map((axis) => {
        const groups = new Set(
          page.examples.filter((shown) => shown.title.startsWith(axis + "=")).map((s) => s.group),
        );
        return groups.size > 1 ? `${page.name} ${axis}: ${[...groups].join(", ")}` : null;
      })
      .filter((found) => found !== null),
  );
  expect(split).toEqual([]);
});

test("the nav reaches every component", () => {
  open(CHAT_PAGES[0].slug);
  const nav = screen.getByRole("navigation", { name: "Chat components" });
  for (const page of CHAT_PAGES) {
    expect(within(nav).getByRole("link", { name: page.name }).getAttribute("href")).toBe(
      "#/" + page.slug,
    );
  }
  cleanup();
});

test("every block documents the parts it is built from", () => {
  const bare = CHAT_PAGES.filter((page) => page.props.length === 0);
  expect(bare.map((page) => page.name)).toEqual([]);
  const empty = CHAT_PAGES.flatMap((page) =>
    page.props.filter((table) => table.rows.length === 0).map((table) => `${page.name}.${table.of}`),
  );
  expect(empty).toEqual([]);
});

test("a state that is only a motion offers a way to see it again", async () => {
  /** One-shot: once it has run, the tile is its own default and the state is gone. */
  const named = CHAT_PAGES.flatMap((page) =>
    page.examples.filter((shown) => shown.replay).map((shown) => shown.title),
  );
  expect(named.length).toBeGreaterThan(0);
  for (const page of CHAT_PAGES) {
    const motions = page.examples.filter((shown) => shown.replay);
    if (motions.length === 0) continue;
    open(page.slug);
    const again = screen.getAllByRole("button", { name: "Play again" });
    expect(again.length).toBe(motions.length);
    const before = screen.getAllByRole("figure").length;
    await userEvent.click(again[0]);
    expect(screen.getAllByRole("figure").length).toBe(before);
  }
  cleanup();
});

test("every variant a documented component declares is drawn on the page", () => {
  const missing = CHAT_PAGES.flatMap((page) =>
    (page.axes ?? []).flatMap((axis) => {
      const drawn = page.examples.map((shown) => shown.title).join(" | ");
      return axisKeys(page.modules[0], axis)
        .filter((key) => !drawn.includes(`${axis}="${key}"`))
        .map((key) => `${page.name} ${axis}="${key}"`);
    }),
  );
  expect(missing).toEqual([]);
});
