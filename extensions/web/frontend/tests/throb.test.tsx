import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, CONVO_ID, json, MEMBER, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

const theme = () => readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

const DOTS = [
  [5, 5, 1],
  [12, 5, 4],
  [19, 5, 1],
  [5, 12, 2],
  [12, 12, 5],
  [19, 12, 2],
  [5, 19, 3],
  [12, 19, 6],
  [19, 19, 3],
];

const AHEAD = [2, 5, 8];

const SPELLED = "⠋⠙⠹⠸⠼⠴⠦⠧";

const TICKS = 8;

const BRAILLE_BLOCK = 0x2800;

const cellDots = (cell: string): number[] => {
  const bits = cell.codePointAt(0)! - BRAILLE_BLOCK;
  return [0, 1, 2, 3, 4, 5].map((dot) => (bits >> dot) & 1);
};

const gridAt = (tick: number): number[] => {
  const reading = cellDots(SPELLED[tick]);
  const coming = cellDots(SPELLED[(tick + 1) % TICKS]);
  return [0, 1, 2].flatMap((row) => [reading[row], reading[row + 3], coming[row]]);
};

const throbber = () => document.querySelector("[data-slot=marker] [data-throb]");

const declared = (cell: number): string => {
  const rule = new RegExp(
    '\\[data-throb\\] circle\\[data-cell="' + cell + '"\\] \\{([^}]*)\\}',
  ).exec(theme());
  if (rule === null) throw new Error("cell dot " + cell + " is given no keyframes");
  return rule[1];
};

const aheadBy = (): number => {
  const rule = /\[data-throb\] circle\[data-ahead\] \{\s*animation-delay: (-?[\d.]+)s;/.exec(
    theme(),
  );
  if (rule === null) throw new Error("no dot runs ahead of the rest");
  return Math.round((-Number.parseFloat(rule[1]) * TICKS) / 0.8);
};

const litBy = (name: string): number[] => {
  const written = new RegExp("@keyframes " + name + " \\{([\\s\\S]*?)\\n  \\}").exec(theme());
  if (written === null) throw new Error("no keyframe " + name);
  const stops = [...written[1].matchAll(/(from|[\d.]+%) \{\s*opacity: ([^;]+);/g)].map((stop) => ({
    at: stop[1] === "from" ? 0 : Number.parseFloat(stop[1]),
    lit: stop[2] === "1" ? 1 : 0,
  }));
  return [...Array(TICKS).keys()].map((tick) => {
    const held = stops.filter((stop) => stop.at <= (tick * 100) / TICKS);
    return held[held.length - 1].lit;
  });
};

const litDots = (at: number): number[] => {
  const body = declared(DOTS[at][2]);
  const name = /animation-name: (throb-\d);/.exec(body);
  if (name === null) throw new Error("cell dot " + DOTS[at][2] + " runs no keyframe");
  const turned = AHEAD.includes(at) ? aheadBy() : 0;
  const lit = litBy(name[1]);
  return [...Array(TICKS).keys()].map((tick) => lit[(tick + turned) % TICKS]);
};

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.type(screen.getByLabelText("Ask UFO"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

beforeEach(() => {
  useStreamFake();
});

test("the live line keeps the nine round dots, each saying what it carries", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });
  await screen.findByText("Reading the calendar.", { selector: "[data-slot=marker-content]" });

  const grid = throbber()!;
  const drawn = [...grid.querySelectorAll("circle")].map((dot) => [
    Number(dot.getAttribute("cx")),
    Number(dot.getAttribute("cy")),
    Number(dot.getAttribute("data-cell")),
  ]);
  expect(drawn).toEqual(DOTS);
  expect(grid.querySelectorAll("circle")[0].getAttribute("r")).toBe("1");
  expect(
    [...grid.querySelectorAll("circle")].flatMap((dot, at) =>
      dot.hasAttribute("data-ahead") ? [at] : [],
    ),
  ).toEqual(AHEAD);
  expect(grid.getAttribute("aria-hidden")).toBe("true");
});

test("the grid keeps the working pulse", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });
  await screen.findByText("Reading the calendar.", { selector: "[data-slot=marker-content]" });

  const grid = throbber()!;
  expect(grid.getAttribute("class")).toContain("animate-working");
  expect(grid.getAttribute("class")).toContain("motion-reduce:animate-none");
  expect(grid.getAttribute("class")).toContain("-translate-y-px");
});

const column = (grid: number[], at: number): number[] => [grid[at], grid[at + 3], grid[at + 6]];

test("the grid spells a braille character a tick, with the next one beside it", () => {
  const lit = [...Array(DOTS.length).keys()].map(litDots);
  const stood = [...Array(TICKS).keys()].map((tick) => lit.map((dot) => dot[tick]));
  expect(stood).toEqual([...Array(TICKS).keys()].map(gridAt));

  const spelled = stood.map((grid) => {
    const cell = [...column(grid, 0), ...column(grid, 1)];
    return String.fromCodePoint(
      BRAILLE_BLOCK + cell.reduce((bits, dot, at) => bits | (dot << at), 0),
    );
  });
  expect(spelled.join("")).toBe(SPELLED);
  for (const [tick, grid] of stood.entries()) {
    expect(column(grid, 2)).toEqual(column(stood[(tick + 1) % TICKS], 0));
  }
});

test("the run steps a character at a time", () => {
  const css = theme();
  expect(css).toContain("animation-duration: 0.8s;");
  expect(css).toContain("animation-timing-function: step-end;");
  expect(css).toContain("animation-iteration-count: infinite;");
  expect(aheadBy()).toBe(1);
  expect(declared(1)).not.toContain("animation-delay");
});

test("the run stops for reduced motion", () => {
  expect(
    /@media \(prefers-reduced-motion: reduce\) \{\s*\[data-throb\] circle \{\s*animation: none !important;/.test(
      theme(),
    ),
  ).toBe(true);
});

test("the label stands still while the grid spells", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });

  const label = await screen.findByText("Reading the calendar.", {
    selector: "[data-slot=marker-content]",
  });
  const line = label.closest("[data-slot=marker]")!;
  const grid = throbber();

  expect(line.textContent).toBe("Reading the calendar.");
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(label.textContent).toBe("Reading the calendar.");

  stream.emit("activity", { text: "Listing the workspace." });
  const next = await screen.findByText("Listing the workspace.", {
    selector: "[data-slot=marker-content]",
  });
  expect(next).toBe(label);
  expect(next.textContent).toBe("Listing the workspace.");
  expect(throbber()).toBe(grid);
});

test("a settled turn takes the throbber down", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });
  await screen.findByText("Reading the calendar.", { selector: "[data-slot=marker-content]" });

  stream.emit("terminal", {
    status: "done",
    text: "Read it.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Completed 1 step")).toBeTruthy();
  expect(throbber()).toBeNull();
});
