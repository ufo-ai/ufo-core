import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { Delta, ProgressStat, Sparkline, Stat, StatGrid } from "@/blocks/stat";
import { StatPage } from "@/blocks/docs/StatPage";
import { StatStatesInteractive } from "@/blocks/docs/examples/stat-states-interactive";

const WIDE = 640;
const TALL = 360;
const STYLESHEET = readFileSync(join(import.meta.dirname, "..", "..", "src", "blocks", "stat.css"), "utf8");

// recharts sizes itself off the box it is given and jsdom measures every box at nothing.
class Measured {
  constructor(private report: ResizeObserverCallback) {}
  observe(target: Element) {
    this.report(
      [{ target, contentRect: { width: WIDE, height: TALL } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = Measured as unknown as typeof ResizeObserver;

test("a stat states its label and value", () => {
  render(<Stat label="Revenue" value="$42,800" />);
  expect(screen.getByText("Revenue")).toBeTruthy();
  expect(screen.getByText("$42,800")).toBeTruthy();
});

test("a delta carries its direction, so the colour is the theme's to set", () => {
  const { container } = render(
    <Stat label="Churn" value="2.1%" delta={{ value: "0.3%", direction: "down" }} />,
  );
  const delta = container.querySelector(".blk-delta")!;
  expect(delta.getAttribute("data-direction")).toBe("down");
  expect(delta.getAttribute("data-tone")).toBe("auto");
});

test("a delta stands on its own beside text, so a cell reads the same figure a tile does", () => {
  const { container } = render(<Delta value="0.4 pts" direction="down" />);
  const delta = container.querySelector(".blk-delta")!;
  expect(delta.textContent).toBe("0.4 pts");
  expect(delta.getAttribute("data-direction")).toBe("down");
  expect(delta.querySelector("svg")).not.toBeNull();
});

test("an explicit tone overrides the direction, so a fall the member wants is not coloured as a loss", () => {
  const { container } = render(
    <>
      <Delta value="0.3%" direction="down" tone="positive" />
      <Delta value="6" direction="up" tone="negative" />
      <Delta value="56" direction="down" tone="neutral" />
    </>,
  );
  const tones = [...container.querySelectorAll(".blk-delta")].map((mark) => [
    mark.getAttribute("data-direction"),
    mark.getAttribute("data-tone"),
  ]);
  expect(tones).toEqual([
    ["down", "positive"],
    ["up", "negative"],
    ["down", "neutral"],
  ]);
});

test("a stat draws its source mark before the label and takes a label built of nodes", () => {
  const { container } = render(
    <Stat
      media={<svg data-mark="github" />}
      label={<span data-part="tag">Reverted</span>}
      value="7"
    />,
  );
  const label = container.querySelector(".blk-stat-label")!;
  expect(label.firstElementChild!.className).toBe("blk-stat-media");
  expect(label.querySelector("[data-mark='github']")).not.toBeNull();
  expect(label.querySelector("[data-part='tag']")!.textContent).toBe("Reverted");
});

test("a stat with no media leaves the slot out, so nothing indents a label that has no mark", () => {
  const { container } = render(<Stat label="Shipped" value="298" />);
  expect(container.querySelector(".blk-stat-media")).toBeNull();
});

test("casing and font are attributes, so the label's case and the value's face are the stylesheet's", () => {
  const { container } = render(
    <Stat casing="sentence" font="mono" label="Net cash flow" value="+$20.6k" />,
  );
  const stat = container.querySelector(".blk-stat")!;
  expect(stat.getAttribute("data-casing")).toBe("sentence");
  expect(stat.getAttribute("data-font")).toBe("mono");
});

test("a stat states the upper label and the sans value by default", () => {
  const { container } = render(<Stat label="Shipped" value="298" />);
  const stat = container.querySelector(".blk-stat")!;
  expect(stat.getAttribute("data-casing")).toBe("upper");
  expect(stat.getAttribute("data-font")).toBe("sans");
});

test("the progress bar is filled to the share reached", () => {
  const { container } = render(
    <ProgressStat label="Annual target" value="$177,450" percent={65} target="$273,000" />,
  );
  expect((container.querySelector(".blk-progress-stat-fill") as HTMLElement).style.width).toBe("65%");
  expect(screen.getByText("65% achieved")).toBeTruthy();
});

test("a bar sparkline draws one bar per value, rounded at the top only", () => {
  const { container } = render(<Sparkline values={[4, 9, 6, 12]} kind="bars" />);
  const bars = container.querySelectorAll("path[data-bar]");
  expect(bars).toHaveLength(4);
  expect(container.querySelectorAll("rect")).toHaveLength(0);
  expect(bars[3].getAttribute("d")).toBe("M70.5 32V4A4 4 0 0 1 74.5 0H84A4 4 0 0 1 88 4V32Z");
});

test("a line sparkline draws the run as one path", () => {
  const { container } = render(<Sparkline values={[4, 9, 6, 12]} kind="line" />);
  expect(container.querySelectorAll("path")).toHaveLength(1);
  expect(container.querySelector("path[data-bar]")).toBeNull();
  expect(container.querySelectorAll("rect")).toHaveLength(0);
});

test("a small stat sets the size the stylesheet reads for the 15px value", () => {
  const { container } = render(<Stat size="sm" label="Paid this year" value="$11,270" />);
  expect(container.querySelector(".blk-stat")!.getAttribute("data-size")).toBe("sm");
});

test("a stat grid states how many tiles stand in a row", () => {
  const { container } = render(
    <StatGrid columns={3}>
      <span>one</span>
    </StatGrid>,
  );
  expect(container.querySelector(".blk-stat-grid")!.getAttribute("data-columns")).toBe("3");
});

test("the caption wraps to three lines, so a sentence under a figure is not cut after one", () => {
  const rule = STYLESHEET.split(".blk-stat-sub {")[1].split("}")[0];
  expect(rule).toContain("line-clamp: 3");
  expect(rule).not.toContain("nowrap");
});

const HEADINGS = [
  "Stat",
  "Stat tiles",
  "Progress",
  "Rows with sparklines",
  "Sparkline",
  "Bar chart with tiles and action",
  "Source mark",
  "Sentence label",
  "Mono figure",
  "Delta",
  "Every state",
  "Interactive states",
];

test("the page shows every example and the API beneath them", () => {
  const { container } = render(<StatPage />);
  const shown = Array.from(container.querySelectorAll(".blk-example h3"), (h) => h.textContent);
  expect(shown).toEqual(HEADINGS);
  expect(screen.getByRole("heading", { name: "API", level: 2 })).toBeTruthy();
  expect(container.querySelectorAll(".blk-docs-api .blk-props").length).toBe(8);
});

test("the grid stands in its own container, so it reads its box and not the window", () => {
  const { container } = render(
    <StatGrid columns={4}>
      <div />
    </StatGrid>,
  );
  const box = container.querySelector(".blk-stat-grid-box")!;
  expect(box.firstElementChild!.className).toBe("blk-stat-grid");
  expect(STYLESHEET).toContain("container: blk-stat-grid / inline-size");
  expect(STYLESHEET).toContain("@container blk-stat-grid (max-width: 719px)");
  expect(STYLESHEET).toContain("@container blk-stat-grid (max-width: 359px)");
  // A bare .blk-stat-grid inside the query loses to .blk-stat-grid[data-columns="4"] on specificity.
  for (const query of ["719px", "359px"]) {
    const block = STYLESHEET.split(`(max-width: ${query}) {`)[1].split("\n}")[0];
    expect(block, query).toContain('[data-columns="4"]');
  }
});

test("the interactive example sets the box width the grid answers to", async () => {
  const user = userEvent.setup();
  const { container } = render(<StatStatesInteractive />);
  const box = container.querySelector(".blk-stat-grid-box")!.parentElement as HTMLElement;
  expect(box.style.width).toBe("880px");
  await user.click(screen.getByRole("button", { name: "320px" }));
  expect(screen.getByRole("button", { name: "320px" }).getAttribute("data-active")).toBe("true");
  expect((container.querySelector(".blk-stat-grid-box")!.parentElement as HTMLElement).style.width).toBe("320px");
  expect(container.querySelector(".blk-stat-grid")!.getAttribute("data-columns")).toBe("4");
});
