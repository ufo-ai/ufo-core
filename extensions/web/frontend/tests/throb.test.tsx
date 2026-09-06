import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, CONVO_ID, json, MEMBER, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

const theme = () => readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

/** Where each dot of the grid stands in the 24-unit box, left to right and top to bottom: the nine
 *  places the grid has always drawn, and the dot of the braille cell each one carries. */
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

/** The column that runs a tick ahead of the rest: the third one, holding the next character's head. */
const AHEAD = [2, 5, 8];

/** What the grid spells, a character per tick. */
const SPELLED = "⠋⠙⠹⠸⠼⠴⠦⠧";

const TICKS = 8;

const BRAILLE_BLOCK = 0x2800;

/** The six dots of a braille cell, in the order they are numbered: 1 to 3 down the left column, 4 to
 *  6 down the right. A cell's code point carries them as one bit each. */
const cellDots = (cell: string): number[] => {
  const bits = cell.codePointAt(0)! - BRAILLE_BLOCK;
  return [0, 1, 2, 3, 4, 5].map((dot) => (bits >> dot) & 1);
};

/** The grid a tick should stand at, in the order the icon writes its dots — left to right, top to
 *  bottom. The character stands in the left two columns, written as braille is written, and the
 *  next character's first column stands in the third. */
const gridAt = (tick: number): number[] => {
  const reading = cellDots(SPELLED[tick]);
  const coming = cellDots(SPELLED[(tick + 1) % TICKS]);
  return [0, 1, 2].flatMap((row) => [reading[row], reading[row + 3], coming[row]]);
};

const throbber = () => document.querySelector("[data-slot=marker] [data-throb]");

/** What the theme says about the dots carrying one dot of the cell. */
const declared = (cell: number): string => {
  const rule = new RegExp(
    '\\[data-throb\\] circle\\[data-cell="' + cell + '"\\] \\{([^}]*)\\}',
  ).exec(theme());
  if (rule === null) throw new Error("cell dot " + cell + " is given no keyframes");
  return rule[1];
};

/** How far the column that runs early is turned, in ticks. */
const aheadBy = (): number => {
  const rule = /\[data-throb\] circle\[data-ahead\] \{\s*animation-delay: (-?[\d.]+)s;/.exec(
    theme(),
  );
  if (rule === null) throw new Error("no dot runs ahead of the rest");
  return Math.round((-Number.parseFloat(rule[1]) * TICKS) / 0.8);
};

/** One keyframe as the eight ticks it lights: a stop holds its value until the next one, which is
 *  what `step-end` says, so the ticks are read off the stops rather than off a count of them. */
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

/** What one dot of the grid is lit to, tick by tick: the keyframe the theme gives the cell dot it
 *  carries, turned by a tick where the dot stands in the column that runs early. */
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

/** The dots are the throbber's whole shape: the theme lights them and never replaces them, so the
 *  nine stand where the grid has always drawn them, in the order the theme is written against. A dot
 *  that changed places would spell something else. */
test("the live line keeps the nine round dots, each saying what it carries", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });
  await screen.findByText("Reading the calendar.", { selector: "[data-slot=marker-content]" });

  const grid = throbber()!;
  // Circles, so a dot is round wherever it is drawn — a cell taken from a font is squared off by
  // whatever face answers for braille.
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
  // The line says what it is doing in words beside the grid, so the spelling is out of the reading
  // order.
  expect(grid.getAttribute("aria-hidden")).toBe("true");
});

/** The grid pulsed before it spelled anything, and it still does: the whole glyph breathes on the
 *  working pulse while the dots under it spell. It also stands on the letters rather than on the
 *  line box, whose middle falls below them by the room a descender takes. */
test("the grid keeps the working pulse", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the calendar." });
  await screen.findByText("Reading the calendar.", { selector: "[data-slot=marker-content]" });

  const grid = throbber()!;
  expect(grid.getAttribute("class")).toContain("animate-working");
  expect(grid.getAttribute("class")).toContain("motion-reduce:animate-none");
  expect(grid.getAttribute("class")).toContain("-translate-y-px");
});

/** One column of the grid, top to bottom, out of the nine dots as the icon writes them. */
const column = (grid: number[], at: number): number[] => [grid[at], grid[at + 3], grid[at + 6]];

/** The whole of the mapping, read back out of the theme: every tick of every dot, against the
 *  character that tick spells and the one standing beside it. */
test("the grid spells a braille character a tick, with the next one beside it", () => {
  const lit = [...Array(DOTS.length).keys()].map(litDots);
  const stood = [...Array(TICKS).keys()].map((tick) => lit.map((dot) => dot[tick]));
  expect(stood).toEqual([...Array(TICKS).keys()].map(gridAt));

  // Read the other way: the left two columns of a tick are that tick's character, and the third is
  // the first column of the character after it.
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

/** A character is one tick, and the eight are one turn of the run. */
test("the run steps a character at a time", () => {
  const css = theme();
  expect(css).toContain("animation-duration: 0.8s;");
  expect(css).toContain("animation-timing-function: step-end;");
  expect(css).toContain("animation-iteration-count: infinite;");
  // The right-hand column is the left one a tick early, so it takes that tick off its delay rather
  // than keyframes of its own.
  expect(aheadBy()).toBe(1);
  expect(declared(1)).not.toContain("animation-delay");
});

/** A member who asked for less motion is left with the grid standing still, rather than the flicker
 *  the global reduced-motion rule would make of a run that repeats for ever. */
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

  // The words carry no glyph of their own: nothing but the ink on the dots changes while the turn
  // runs.
  expect(line.textContent).toBe("Reading the calendar.");
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(label.textContent).toBe("Reading the calendar.");

  stream.emit("activity", { text: "Listing the workspace." });
  const next = await screen.findByText("Listing the workspace.", {
    selector: "[data-slot=marker-content]",
  });
  // A new step replaces the words whole, under the same grid.
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
