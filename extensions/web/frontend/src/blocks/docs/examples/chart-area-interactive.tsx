import { useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const SPAN = 90;
const LAST = new Date("2024-06-30");

const VISITS = Array.from({ length: SPAN }, (_, at) => {
  const day = new Date(LAST);
  day.setDate(day.getDate() - (SPAN - 1 - at));
  return {
    date: day.toISOString().slice(0, 10),
    desktop: 220 + Math.round(90 * Math.sin(at / 6)) + (at % 7) * 12,
    mobile: 160 + Math.round(70 * Math.cos(at / 5)) + (at % 5) * 14,
  };
});

const RANGES = [
  { days: 90, label: "Last 3 months" },
  { days: 30, label: "Last 30 days" },
  { days: 7, label: "Last 7 days" },
];

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

function readDay(date: string) {
  return new Date(date).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function ChartAreaInteractive() {
  const [days, setDays] = useState(SPAN);
  const shown = VISITS.slice(SPAN - days);
  return (
    <div>
      <div className="blk-chart-head">
        <div className="blk-chart-head-text">
          <span className="blk-chart-head-title">Visitors</span>
          <span className="blk-chart-head-sub">Desktop and mobile, by day.</span>
        </div>
        <span className="blk-chart-select">
          <select
            className="blk-select"
            aria-label="Time range"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            {RANGES.map((range) => (
              <option key={range.days} value={range.days}>
                {range.label}
              </option>
            ))}
          </select>
        </span>
      </div>
      <ChartContainer config={CONFIG}>
        <AreaChart accessibilityLayer data={shown} margin={{ left: 12, right: 12 }}>
          <defs>
            <linearGradient id="blk-range-desktop" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--blk-color-desktop)" stopOpacity={0.8} />
              <stop offset="95%" stopColor="var(--blk-color-desktop)" stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="blk-range-mobile" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--blk-color-mobile)" stopOpacity={0.8} />
              <stop offset="95%" stopColor="var(--blk-color-mobile)" stopOpacity={0.05} />
            </linearGradient>
          </defs>
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
            cursor={false}
            content={<ChartTooltipContent indicator="dot" labelFormatter={(date) => readDay(String(date))} />}
          />
          <Area
            dataKey="mobile"
            type="natural"
            stackId="visits"
            fill="url(#blk-range-mobile)"
            stroke="var(--blk-color-mobile)"
          />
          <Area
            dataKey="desktop"
            type="natural"
            stackId="visits"
            fill="url(#blk-range-desktop)"
            stroke="var(--blk-color-desktop)"
          />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}
