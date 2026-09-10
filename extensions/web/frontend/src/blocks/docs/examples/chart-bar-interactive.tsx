import { useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const SPAN = 60;
const LAST = new Date("2024-06-30");

const VISITS = Array.from({ length: SPAN }, (_, at) => {
  const day = new Date(LAST);
  day.setDate(day.getDate() - (SPAN - 1 - at));
  return {
    date: day.toISOString().slice(0, 10),
    desktop: 210 + Math.round(80 * Math.sin(at / 5)) + (at % 6) * 11,
    mobile: 150 + Math.round(60 * Math.cos(at / 4)) + (at % 8) * 9,
  };
});

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

const SERIES = ["desktop", "mobile"] as const;

const TOTALS = {
  desktop: VISITS.reduce((sum, row) => sum + row.desktop, 0),
  mobile: VISITS.reduce((sum, row) => sum + row.mobile, 0),
};

function readDay(date: string) {
  return new Date(date).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function ChartBarInteractive() {
  const [shown, setShown] = useState<(typeof SERIES)[number]>("desktop");
  return (
    <div>
      <div className="blk-chart-head">
        <div className="blk-chart-head-text">
          <span className="blk-chart-head-title">Visitors</span>
          <span className="blk-chart-head-sub">Pick a series to draw.</span>
        </div>
        <div className="blk-chart-toggles">
          {SERIES.map((series) => (
            <button
              key={series}
              type="button"
              className="blk-chart-toggle"
              aria-pressed={shown === series}
              onClick={() => setShown(series)}
            >
              <span className="blk-chart-toggle-label">{CONFIG[series].label}</span>
              <span className="blk-chart-toggle-value">{TOTALS[series].toLocaleString()}</span>
            </button>
          ))}
        </div>
      </div>
      <ChartContainer config={CONFIG}>
        <BarChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="date"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            minTickGap={32}
            tickFormatter={readDay}
          />
          <ChartTooltip
            content={<ChartTooltipContent labelFormatter={(date) => readDay(String(date))} />}
          />
          <Bar dataKey={shown} fill={`var(--blk-color-${shown})`} radius={4} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}
