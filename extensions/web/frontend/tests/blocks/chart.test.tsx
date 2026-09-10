import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { ComponentProps } from "react";
import { Bar, BarChart } from "recharts";

import {
  ChartContainer,
  ChartLegendContent,
  ChartStyle,
  ChartTick,
  ChartTooltipContent,
  type ChartConfig,
} from "@/blocks/chart";
import { ChartAreaInteractive } from "@/blocks/docs/examples/chart-area-interactive";
import { ChartBarInteractive } from "@/blocks/docs/examples/chart-bar-interactive";
import { ChartPage } from "@/blocks/docs/ChartPage";

const WIDE = 640;
const TALL = 360;

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

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

const THEMED = {
  desktop: { label: "Desktop", theme: { light: "#0095ff", dark: "#6cc4ff" } },
} satisfies ChartConfig;

type TooltipPayload = ComponentProps<typeof ChartTooltipContent>["payload"];

const PAYLOAD = [
  {
    dataKey: "desktop",
    name: "desktop",
    value: 186,
    color: "var(--blk-primary)",
    payload: { month: "January", desktop: 186, mobile: 80, browser: "chrome" },
  },
  {
    dataKey: "mobile",
    name: "mobile",
    value: 80,
    color: "var(--blk-secondary)",
    payload: { month: "January", desktop: 186, mobile: 80, browser: "chrome" },
  },
] as unknown as TooltipPayload;

const ONE = [PAYLOAD![0]] as unknown as TooltipPayload;

function tooltip(props: Partial<ComponentProps<typeof ChartTooltipContent>>) {
  return render(
    <ChartContainer config={{ ...CONFIG, chrome: { label: "Chrome" }, January: { label: "Jan 2024" } }}>
      <div>
        <ChartTooltipContent active payload={PAYLOAD} label="January" {...props} />
      </div>
    </ChartContainer>,
  );
}

test("the style names each series' colour under both schemes", () => {
  const { container } = render(<ChartStyle id="chart-a" config={THEMED} />);
  const css = container.querySelector("style")!.innerHTML;
  expect(css).toContain(':root[data-scheme="light"] [data-chart=chart-a] {');
  expect(css).toContain(':root[data-scheme="dark"] [data-chart=chart-a] {');
  expect(css).toContain("--blk-color-desktop: #0095ff;");
  expect(css).toContain("--blk-color-desktop: #6cc4ff;");
  expect(css).toContain("@media (prefers-color-scheme: dark)");
  expect(css).toContain(':root:not([data-scheme="light"]) [data-chart=chart-a] {');
});

test("a config with no colour writes no style", () => {
  const { container } = render(<ChartStyle id="chart-b" config={{ spend: { label: "Spend" } }} />);
  expect(container.querySelector("style")).toBeNull();
});

test("the container names itself and writes each series' colour out for the plot to read", () => {
  const { container } = render(
    <ChartContainer config={CONFIG}>
      <BarChart data={[{ month: "Jan", desktop: 4 }]}>
        <Bar dataKey="desktop" />
      </BarChart>
    </ChartContainer>,
  );
  const chart = container.querySelector(".blk-chart") as HTMLElement;
  expect(chart.getAttribute("data-chart")).toMatch(/^blk-chart-/);
  expect(chart.style.getPropertyValue("--blk-color-desktop")).toBe("var(--blk-primary)");
  expect(chart.style.getPropertyValue("--blk-color-mobile")).toBe("var(--blk-secondary)");
  expect(container.querySelector("style")!.innerHTML).toContain(
    `[data-chart=${chart.getAttribute("data-chart")}]`,
  );
  expect(container.querySelector(".recharts-bar-rectangle")).toBeTruthy();
});

test("the container states the box its plot is drawn in", () => {
  const plot = (
    <BarChart data={[{ month: "Jan", desktop: 4 }]}>
      <Bar dataKey="desktop" />
    </BarChart>
  );
  const { container, rerender } = render(<ChartContainer config={CONFIG}>{plot}</ChartContainer>);
  expect(container.querySelector(".blk-chart")!.getAttribute("data-ratio")).toBe("video");
  rerender(
    <ChartContainer config={CONFIG} ratio="half">
      {plot}
    </ChartContainer>,
  );
  expect(container.querySelector(".blk-chart")!.getAttribute("data-ratio")).toBe("half");
});

test("a two-line tick hangs both lines off the tick's own x", () => {
  const { container } = render(
    <svg>
      <ChartTick x={40} y={200} payload={{ value: "sep8" }} lines={(night) => [night, "1192/1666"]} />
    </svg>,
  );
  const spans = [...container.querySelectorAll("text.blk-chart-tick tspan")];
  expect(spans.map((span) => span.textContent)).toEqual(["sep8", "1192/1666"]);
  expect(spans.map((span) => span.getAttribute("x"))).toEqual(["40", "40"]);
  expect(spans[1].getAttribute("class")).toBe("blk-chart-tick-detail");
});

test("a tick given one line draws one tspan, so an axis without a detail keeps its height", () => {
  const { container } = render(
    <svg>
      <ChartTick x={40} y={200} payload={{ value: "sep8" }} lines={(night) => [night]} />
    </svg>,
  );
  expect(container.querySelectorAll("tspan")).toHaveLength(1);
  expect(container.querySelector("text")!.textContent).toBe("sep8");
});

test("a tooltip states the period and every series measured at it", () => {
  const { container } = tooltip({});
  expect(container.querySelector(".blk-chart-tooltip-label")!.textContent).toBe("Jan 2024");
  expect(Array.from(container.querySelectorAll(".blk-chart-tooltip-name"), (n) => n.textContent)).toEqual([
    "Desktop",
    "Mobile",
  ]);
  expect(Array.from(container.querySelectorAll(".blk-chart-tooltip-value"), (n) => n.textContent)).toEqual([
    "186",
    "80",
  ]);
});

test.each(["dot", "line", "dashed"] as const)("the %s indicator marks every row", (indicator) => {
  const { container } = tooltip({ indicator });
  const marks = container.querySelectorAll(".blk-chart-tooltip-mark");
  expect(marks).toHaveLength(2);
  expect(marks[0].getAttribute("data-indicator")).toBe(indicator);
  expect((marks[0] as HTMLElement).style.getPropertyValue("--blk-chart-mark")).toBe(
    "var(--blk-primary)",
  );
});

test("hideLabel drops the heading and hideIndicator drops the marks", () => {
  const { container } = tooltip({ hideLabel: true, hideIndicator: true });
  expect(container.querySelector(".blk-chart-tooltip-label")).toBeNull();
  expect(container.querySelectorAll(".blk-chart-tooltip-mark")).toHaveLength(0);
  expect(container.querySelectorAll(".blk-chart-tooltip-name")).toHaveLength(2);
});

test("a single series under a line indicator nests its heading beside the value", () => {
  const { container } = tooltip({ payload: ONE, indicator: "line" });
  expect(container.querySelector(".blk-chart-tooltip-measure")!.getAttribute("data-nested")).toBe("on");
  expect(container.querySelector(".blk-chart-tooltip-names")!.textContent).toBe("Jan 2024Desktop");
});

test("labelKey reads the heading off the datum", () => {
  const { container } = tooltip({ labelKey: "browser" });
  expect(container.querySelector(".blk-chart-tooltip-label")!.textContent).toBe("Chrome");
});

test("nameKey reads every row's name off the datum", () => {
  const { container } = tooltip({ nameKey: "browser" });
  expect(Array.from(container.querySelectorAll(".blk-chart-tooltip-name"), (n) => n.textContent)).toEqual([
    "Chrome",
    "Chrome",
  ]);
});

test("labelFormatter rewrites the heading", () => {
  const { container } = tooltip({ labelFormatter: (label) => `Week of ${label}` });
  expect(container.querySelector(".blk-chart-tooltip-label")!.textContent).toBe("Week of Jan 2024");
});

test("formatter draws each row in place of the mark, name and value", () => {
  const { container } = tooltip({
    formatter: (value, name) => <span className="blk-probe">{`${String(name)}=${String(value)}`}</span>,
  });
  expect(Array.from(container.querySelectorAll(".blk-probe"), (n) => n.textContent)).toEqual([
    "desktop=186",
    "mobile=80",
  ]);
  expect(container.querySelectorAll(".blk-chart-tooltip-mark")).toHaveLength(0);
});

test("the legend names one series per payload entry", () => {
  const legend = [
    { dataKey: "desktop", value: "desktop", color: "var(--blk-primary)" },
    { dataKey: "mobile", value: "mobile", color: "var(--blk-secondary)" },
  ] as unknown as ComponentProps<typeof ChartLegendContent>["payload"];
  const { container } = render(
    <ChartContainer config={CONFIG}>
      <div>
        <ChartLegendContent payload={legend} />
      </div>
    </ChartContainer>,
  );
  expect(Array.from(container.querySelectorAll(".blk-chart-legend-item"), (n) => n.textContent)).toEqual([
    "Desktop",
    "Mobile",
  ]);
  expect(container.querySelector(".blk-chart-legend")!.getAttribute("data-align")).toBe("bottom");
});

test("hideIcon draws the colour mark where the config names an icon", () => {
  const iconed = {
    desktop: { label: "Desktop", color: "var(--blk-primary)", icon: () => <svg data-icon="" /> },
  } satisfies ChartConfig;
  const legend = [
    { dataKey: "desktop", value: "desktop", color: "var(--blk-primary)" },
  ] as unknown as ComponentProps<typeof ChartLegendContent>["payload"];
  const shown = render(
    <ChartContainer config={iconed}>
      <div>
        <ChartLegendContent payload={legend} />
      </div>
    </ChartContainer>,
  );
  expect(shown.container.querySelector("[data-icon]")).toBeTruthy();
  expect(shown.container.querySelector(".blk-chart-legend-mark")).toBeNull();
  shown.unmount();
  const hidden = render(
    <ChartContainer config={iconed}>
      <div>
        <ChartLegendContent payload={legend} hideIcon />
      </div>
    </ChartContainer>,
  );
  expect(hidden.container.querySelector("[data-icon]")).toBeNull();
  expect(hidden.container.querySelector(".blk-chart-legend-mark")).toBeTruthy();
});

function drawn(container: HTMLElement): number {
  const curve = container.querySelector(".recharts-area-curve") as SVGPathElement;
  return (curve.getAttribute("d")!.match(/C/g) ?? []).length + 1;
}

test("the range select redraws the area over fewer days", () => {
  const { container } = render(<ChartAreaInteractive />);
  expect(drawn(container)).toBe(90);
  fireEvent.change(screen.getByLabelText("Time range"), { target: { value: "7" } });
  expect(drawn(container)).toBe(7);
  fireEvent.change(screen.getByLabelText("Time range"), { target: { value: "30" } });
  expect(drawn(container)).toBe(30);
});

test("the series toggle draws the series it names", () => {
  const { container } = render(<ChartBarInteractive />);
  const plot = container.querySelector("svg.recharts-surface")!;
  const desktop = screen.getByRole("button", { name: /Desktop/ });
  const mobile = screen.getByRole("button", { name: /Mobile/ });
  expect(desktop.getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(plot, { key: "ArrowRight" });
  expect(container.querySelector(".blk-chart-tooltip-name")!.textContent).toBe("Desktop");
  fireEvent.click(mobile);
  expect(mobile.getAttribute("aria-pressed")).toBe("true");
  expect(desktop.getAttribute("aria-pressed")).toBe("false");
  fireEvent.keyDown(plot, { key: "ArrowRight" });
  expect(container.querySelector(".blk-chart-tooltip-name")!.textContent).toBe("Mobile");
});

const HEADINGS = [
  "Area chart",
  "Area linear",
  "Area step",
  "Area stacked",
  "Area stacked expanded",
  "Area legend",
  "Area icons",
  "Area interactive",
  "Bar chart",
  "Bar horizontal",
  "Bar multiple",
  "Bar label",
  "Bar custom label",
  "Bar mixed",
  "Bar stacked",
  "Bar active",
  "Bar negative",
  "Bar interactive",
  "Line chart",
  "Line linear",
  "Line step",
  "Line multiple",
  "Line dots",
  "Line label",
  "Line custom dots",
  "Pie chart",
  "Pie donut with text",
  "Pie legend",
  "Radar chart",
  "Radial chart",
  "Radial stacked",
  "Tooltip variants",
  "Empty",
  "Loading",
  "Two-line axis tick",
  "Half ratio",
];

test("the page shows every example, the theming note and the API beneath them", { timeout: 60000 }, () => {
  const { container } = render(<ChartPage />);
  const shown = Array.from(container.querySelectorAll(".blk-example h3"), (h) => h.textContent);
  expect(shown).toEqual(HEADINGS);
  expect(screen.getByRole("heading", { name: "Theming", level: 2 })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "API", level: 2 })).toBeTruthy();
  expect(container.querySelectorAll(".blk-docs-api .blk-props").length).toBe(6);
});
