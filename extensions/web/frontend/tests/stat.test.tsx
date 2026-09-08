import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  Stat,
  StatDelta,
  StatDescription,
  StatHeader,
  StatLabel,
  StatMedia,
  StatValue,
} from "@/components/ui/stat";

const INK = /(?<![\w-])text-ink(?![\w-])/;
const SOFT = /(?<![\w-])text-ink-soft(?![\w-])/;

const tile = () => document.querySelector("[data-slot=stat]") as HTMLElement;
const partsOf = (element: Element) =>
  Array.from(element.children).map((part) => part.getAttribute("data-slot"));

function comped() {
  render(
    <Stat>
      <StatHeader>
        <StatLabel>Shipped</StatLabel>
        <span data-slot="badge">Weekly</span>
      </StatHeader>
      <StatValue>
        23
        <StatDelta tone="up">+4</StatDelta>
      </StatValue>
      <StatDescription>counted as pull requests merged into the default branch</StatDescription>
    </Stat>,
  );
}

function live(unarmed = false) {
  render(
    <Stat unarmed={unarmed}>
      <StatHeader>
        <StatMedia>
          <svg data-slot="mark" />
        </StatMedia>
        <StatLabel>Open past a week</StatLabel>
      </StatHeader>
      <StatValue>
        6
        <StatDelta tone="down">oldest 25 days</StatDelta>
      </StatValue>
      <StatDescription>counted as open pull requests over seven days old</StatDescription>
    </Stat>,
  );
}

test("the comped tile draws its label, the state at the far end of that line, and the rule", () => {
  comped();

  const header = screen.getByText("Shipped").parentElement!;
  expect(partsOf(header)).toEqual(["stat-label", "badge"]);
  expect(partsOf(tile())).toEqual(["stat-header", "stat-value", "stat-description"]);
  expect(screen.getByText("+4").className).toContain("text-link");
});

test("the live tile draws the provider's mark before the label it belongs to", () => {
  live();

  const header = screen.getByText("Open past a week").parentElement!;
  expect(partsOf(header)).toEqual(["stat-media", "stat-label"]);
  expect(header.className).toContain("h-(--size-glyph)");
  expect(screen.getByText("oldest 25 days").className).toContain("text-attention-ink");
});

test("the mark takes the glyph square whatever it is given, so no page states that size", () => {
  live();

  const media = document.querySelector("[data-slot=stat-media]")!;
  expect(media.className).toContain("size-(--size-glyph)");
  expect(media.className).toContain("shrink-0");
  expect(media.className).toContain("*:size-full");
});

test("the label is cut at the line's width, so the state beside it keeps its end", () => {
  comped();

  const label = screen.getByText("Shipped");
  expect(label.className).toContain("truncate");
  expect(label.className).toContain("min-w-0");
  expect(label.className).toContain("flex-1");
});

test("the label takes the tile's tone rather than naming one", () => {
  comped();

  expect(screen.getByText("Shipped").className).not.toMatch(INK);
  expect(tile().className).toMatch(SOFT);
});

test("the figure and its move share one line and one baseline", () => {
  comped();

  const value = document.querySelector("[data-slot=stat-value]")!;
  expect(value.textContent).toBe("23+4");
  expect(value.querySelector("[data-slot=stat-delta]")).toBeTruthy();
  expect(value.className).toContain("items-baseline");
});

test("the figure takes the step above the chrome at the label's weight, and its move the step under", () => {
  comped();

  const value = document.querySelector('[data-slot="stat-value"]')!;
  const delta = document.querySelector('[data-slot="stat-delta"]')!;
  expect(value.className).toContain("text-figure");
  expect(value.className).toContain("font-medium");
  expect(value.className).not.toContain("text-title");
  expect(delta.className).toContain("text-small");
  expect(delta.className).not.toContain("text-label");
  expect(delta.className).not.toContain("text-figure");
});

test("a move up draws its direction as a glyph before the figure it states", () => {
  comped();

  const delta = document.querySelector("[data-slot=stat-delta]")!;
  const glyph = delta.querySelector("svg")!;
  expect(glyph.getAttribute("class")).toContain("tabler-icon-arrow-up-right");
  expect(delta.firstElementChild).toBe(glyph);
  expect(delta.textContent).toBe("+4");
});

test("a move down draws the other glyph, so the two directions differ by shape and not by tone", () => {
  live();

  const glyph = document.querySelector("[data-slot=stat-delta] svg")!;
  expect(glyph.getAttribute("class")).toContain("tabler-icon-arrow-down-right");
});

test("a move that held draws no glyph, so a tile states a direction only when it has one", () => {
  render(<StatDelta>0</StatDelta>);

  expect(document.querySelector("[data-slot=stat-delta] svg")).toBeNull();
});

test("the glyph says nothing to a reader being read to and never sets the move's baseline", () => {
  comped();

  const delta = document.querySelector("[data-slot=stat-delta]")!;
  const glyph = delta.querySelector("svg")!;
  expect(glyph.getAttribute("aria-hidden")).toBe("true");
  expect(glyph.getAttribute("class")).toContain("size-(--size-glyph)");
  expect(glyph.getAttribute("class")).toContain("shrink-0");
  expect(glyph.getAttribute("class")).toContain("self-center");
  expect(delta.className).toContain("items-baseline");
});

test("a move states nothing of its own by default, so a column of deltas is one tone", () => {
  render(<StatDelta>+4</StatDelta>);

  const classes = screen.getByText("+4").className;
  expect(classes).toMatch(SOFT);
  expect(classes).not.toContain("text-link");
  expect(classes).not.toContain("text-attention-ink");
});

test("an unarmed measure recedes by tone, never by opacity", () => {
  live(true);

  const stat = tile();
  expect(stat.hasAttribute("data-unarmed")).toBe(true);
  expect(stat.className).toContain("data-unarmed:text-ink-faint");
  expect(document.querySelector("[data-slot=stat-value]")!.className).toContain(
    "group-data-unarmed/stat:text-ink-faint",
  );
  expect(screen.getByText("oldest 25 days").className).toContain(
    "group-data-unarmed/stat:text-ink-faint",
  );
  expect(stat.outerHTML).not.toContain("opacity");
});

test("an armed measure carries no unarmed mark, so the tone reaches only the tile that is off", () => {
  live();

  expect(tile().hasAttribute("data-unarmed")).toBe(false);
});
