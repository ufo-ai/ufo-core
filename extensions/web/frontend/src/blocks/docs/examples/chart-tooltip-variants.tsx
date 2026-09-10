import type { CSSProperties, ReactElement } from "react";
import { Bar, BarChart, Cell, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186, mobile: 80 },
  { month: "February", desktop: 305, mobile: 200 },
  { month: "March", desktop: 237, mobile: 120 },
];

const BROWSERS = [
  { browser: "chrome", visitors: 275 },
  { browser: "safari", visitors: 200 },
  { browser: "firefox", visitors: 187 },
];

const VISITS_CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

const BROWSERS_CONFIG = {
  visitors: { label: "Visitors" },
  chrome: { label: "Chrome", color: "var(--blk-primary)" },
  safari: { label: "Safari", color: "var(--blk-secondary)" },
  firefox: { label: "Firefox", color: "color-mix(in srgb, var(--blk-text-1) 70%, transparent)" },
} satisfies ChartConfig;

const SHOWN = 1;

function Months({ name, tooltip }: { name: string; tooltip: ReactElement }) {
  return (
    <div className="blk-chart-grid-cell">
      <span className="blk-chart-grid-name">{name}</span>
      <ChartContainer config={VISITS_CONFIG}>
        <BarChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
          <XAxis
            dataKey="month"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            tickFormatter={(month: string) => month.slice(0, 3)}
          />
          <ChartTooltip cursor={false} defaultIndex={SHOWN} content={tooltip} />
          <Bar dataKey="desktop" fill="var(--blk-color-desktop)" radius={8} maxBarSize={24} />
          <Bar dataKey="mobile" fill="var(--blk-color-mobile)" radius={8} maxBarSize={24} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}

function Browsers({ name, tooltip }: { name: string; tooltip: ReactElement }) {
  return (
    <div className="blk-chart-grid-cell">
      <span className="blk-chart-grid-name">{name}</span>
      <ChartContainer config={BROWSERS_CONFIG}>
        <BarChart accessibilityLayer data={BROWSERS} margin={{ left: 12, right: 12 }}>
          <XAxis
            dataKey="browser"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            tickFormatter={(browser: string) =>
              String(BROWSERS_CONFIG[browser as keyof typeof BROWSERS_CONFIG].label)
            }
          />
          <ChartTooltip cursor={false} defaultIndex={SHOWN} content={tooltip} />
          <Bar dataKey="visitors" radius={8} maxBarSize={24}>
            {BROWSERS.map((row) => (
              <Cell key={row.browser} fill={`var(--blk-color-${row.browser})`} />
            ))}
          </Bar>
        </BarChart>
      </ChartContainer>
    </div>
  );
}

export function ChartTooltipVariants() {
  return (
    <div className="blk-chart-grid">
      <Months name="Default" tooltip={<ChartTooltipContent />} />
      <Months name="Line indicator" tooltip={<ChartTooltipContent indicator="line" />} />
      <Months name="Dashed indicator" tooltip={<ChartTooltipContent indicator="dashed" />} />
      <Months name="No label" tooltip={<ChartTooltipContent hideLabel />} />
      <Months name="No indicator" tooltip={<ChartTooltipContent hideIndicator />} />
      <Browsers name="Custom label" tooltip={<ChartTooltipContent indicator="line" labelKey="browser" />} />
      <Browsers name="Custom name" tooltip={<ChartTooltipContent hideLabel nameKey="browser" />} />
      <Months
        name="Advanced formatter"
        tooltip={
          <ChartTooltipContent
            hideLabel
            formatter={(value, name, item, at) => (
              <>
                <span
                  className="blk-chart-tooltip-mark"
                  data-indicator="dot"
                  style={{ "--blk-chart-mark": `var(--blk-color-${name})` } as CSSProperties}
                />
                <span className="blk-chart-tooltip-name">
                  {VISITS_CONFIG[name as keyof typeof VISITS_CONFIG].label}
                </span>
                <span className="blk-chart-tooltip-value">{value} visits</span>
                {at === 1 ? (
                  <span className="blk-chart-tooltip-total">
                    Total
                    <span className="blk-chart-tooltip-value">
                      {item.payload.desktop + item.payload.mobile}
                    </span>
                  </span>
                ) : null}
              </>
            )}
          />
        }
      />
    </div>
  );
}
