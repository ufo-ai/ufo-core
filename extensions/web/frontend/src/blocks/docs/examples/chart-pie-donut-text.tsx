import { Cell, Label, Pie, PieChart } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const BROWSERS = [
  { browser: "chrome", visitors: 275 },
  { browser: "safari", visitors: 200 },
  { browser: "firefox", visitors: 287 },
  { browser: "edge", visitors: 173 },
  { browser: "other", visitors: 190 },
];

const TOTAL = BROWSERS.reduce((sum, row) => sum + row.visitors, 0);

const CONFIG = {
  visitors: { label: "Visitors" },
  chrome: { label: "Chrome", color: "var(--blk-primary)" },
  safari: { label: "Safari", color: "var(--blk-secondary)" },
  firefox: { label: "Firefox", color: "color-mix(in srgb, var(--blk-text-1) 70%, transparent)" },
  edge: { label: "Edge", color: "color-mix(in srgb, var(--blk-text-1) 46%, transparent)" },
  other: { label: "Other", color: "color-mix(in srgb, var(--blk-text-1) 28%, transparent)" },
} satisfies ChartConfig;

export function ChartPieDonutText() {
  return (
    <ChartContainer config={CONFIG} className="blk-chart-square">
      <PieChart>
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="browser" />} />
        <Pie
          data={BROWSERS}
          dataKey="visitors"
          nameKey="browser"
          innerRadius={64}
          stroke="var(--blk-bg-100)"
          strokeWidth={2}
        >
          {BROWSERS.map((row) => (
            <Cell key={row.browser} fill={`var(--blk-color-${row.browser})`} />
          ))}
          <Label
            content={({ viewBox }) =>
              viewBox && "cx" in viewBox && "cy" in viewBox ? (
                <text x={viewBox.cx} y={viewBox.cy} textAnchor="middle" dominantBaseline="middle">
                  <tspan x={viewBox.cx} y={viewBox.cy} className="blk-chart-figure">
                    {TOTAL.toLocaleString()}
                  </tspan>
                  <tspan x={viewBox.cx} y={(viewBox.cy ?? 0) + 22} className="blk-chart-label-muted">
                    Visitors
                  </tspan>
                </text>
              ) : null
            }
          />
        </Pie>
      </PieChart>
    </ChartContainer>
  );
}
