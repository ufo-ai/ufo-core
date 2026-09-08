import { render, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import type { ChartBar } from "@/components/ui/chart";
import { Chart, ChartBars } from "@/components/ui/chart";

const WIDE = 640;
const TALL = 360;

/** recharts sizes itself off the box it is given and jsdom measures every box at nothing, so a plot
 *  draws no marks until something reports a width. */
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

const AUTHORED = /#[0-9a-f]{3,8}\b|rgb\(|hsl\(|oklch\(/i;
const SERIES = [1, 4, 2, 6, 3, 5, 4];

test("a trend answers as one graphic, named once, rather than as unnamed points", () => {
  const { container } = render(<Chart label="Open pull requests each day" points={SERIES} />);
  const plot = container.querySelector('[data-slot="chart"]')!;
  expect(plot.getAttribute("role")).toBe("img");
  expect(plot.getAttribute("aria-label")).toBe("Open pull requests each day");
  // recharts v3 stamps `role=application` and a tab stop when its own accessibility layer is on.
  expect(container.querySelector('[role="application"]')).toBeNull();
  expect(plot.getAttribute("tabindex")).toBeNull();
});

test("the plot keeps a measurable box, or the library draws nothing on first paint", () => {
  const { container } = render(<Chart label="Open pull requests each day" points={SERIES} />);
  expect(container.querySelector('[data-slot="chart"]')!.className).toContain("aspect-video");
});

test("the plot draws no ground and no corner — the card it stands in owns both", () => {
  const { container } = render(<Chart label="Open pull requests each day" points={SERIES} />);
  const plot = container.querySelector('[data-slot="chart"]')!.className.split(" ");
  expect(plot).not.toContain("bg-fill");
  expect(plot).not.toContain("rounded-plot");
  expect(plot.filter((name) => name.startsWith("bg-") || name.startsWith("rounded-"))).toEqual([]);
});

test("no colour is authored in the plot — every channel names a theme token", () => {
  const { container } = render(<Chart label="Open pull requests each day" points={SERIES} />);
  expect(AUTHORED.test(container.innerHTML)).toBe(false);
});

test("a measure read a period at a time draws one column per period, not a curve", async () => {
  const { container } = render(<Chart label="Daily spend" points={SERIES} shape="bar" />);
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(SERIES.length),
  );
  expect(container.querySelector(".recharts-area-curve")).toBeNull();
});

const BANDED: ChartBar[] = [
  { value: 2, tone: "muted" },
  { value: 5, tone: "muted" },
  { value: 0, tone: "secondary" },
  { value: 7, tone: "secondary" },
  { value: 4, tone: "primary" },
];

const tracks = () => Array.from(document.querySelectorAll('[data-slot="chart-track"]'));
const columns = () => Array.from(document.querySelectorAll('[data-slot="chart-bar"]'));
const height = (el: Element) => (el as HTMLElement).style.height;

test("every column stands in its own track, so a short one reads as a share of a known whole", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  expect(tracks()).toHaveLength(BANDED.length);
  expect(columns()).toHaveLength(BANDED.length);
  expect(tracks()[0].className).toContain("bg-fill-strong");
});

test("the tallest column fills its track and the rest are read against it", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  const drawn = columns().map(height);
  expect(drawn[3]).toBe("100%");
  expect(Number.parseFloat(drawn[0])).toBeCloseTo((2 / 7) * 100, 5);
});

test("a period with nothing counted still draws a column, at the floor rather than at nothing", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  expect(height(columns()[2])).toBe("2%");
});

test("the band is the caller's to state, and each tone is the theme's", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  const toned = columns().map((column) => column.getAttribute("data-tone"));
  expect(toned).toEqual(["muted", "muted", "secondary", "secondary", "primary"]);
  expect(columns()[0].className).toContain("bg-ink-faint");
  expect(columns()[2].className).toContain("bg-blocked");
  expect(columns()[4].className).toContain("bg-live");
});

test("the plot answers once and its columns say nothing, so the run is heard as one graphic", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  const plot = document.querySelector('[data-slot="chart-plot"]')!;
  expect(plot.getAttribute("role")).toBe("img");
  expect(plot.getAttribute("aria-label")).toBe("Merged each day");
  expect(tracks().every((track) => track.getAttribute("aria-hidden") === "true")).toBe(true);
});

test("the period is stated as its two ends and nothing between them", () => {
  render(<ChartBars label="Merged each day" bars={BANDED} from="18 Aug" to="24 Aug" />);
  const caption = document.querySelector('[data-slot="chart-caption"]')!;
  expect(caption.textContent).toBe("18 Aug24 Aug");
  expect(caption.className).toContain("text-ink-quiet");
  expect(document.querySelectorAll("[data-slot=chart-tick]")).toHaveLength(0);
});

test("a run of nothing divides by no zero", () => {
  render(
    <ChartBars
      label="Nothing merged"
      bars={[{ value: 0, tone: "muted" }]}
      from="18 Aug"
      to="24 Aug"
    />,
  );
  expect(height(columns()[0])).toBe("2%");
});
