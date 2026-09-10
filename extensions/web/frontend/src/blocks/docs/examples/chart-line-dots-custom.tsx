import { CartesianGrid, Line, LineChart, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186 },
  { month: "February", desktop: 305 },
  { month: "March", desktop: 237 },
  { month: "April", desktop: 73 },
  { month: "May", desktop: 209 },
  { month: "June", desktop: 214 },
];

const CONFIG = { desktop: { label: "Desktop", color: "var(--blk-primary)" } } satisfies ChartConfig;

const DIAMOND = 5;

export function ChartLineDotsCustom() {
  return (
    <ChartContainer config={CONFIG}>
      <LineChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Line
          dataKey="desktop"
          type="natural"
          strokeWidth={2}
          stroke="var(--blk-color-desktop)"
          dot={({ cx = 0, cy = 0, payload }) => (
            <rect
              key={payload.month}
              x={cx - DIAMOND}
              y={cy - DIAMOND}
              width={DIAMOND * 2}
              height={DIAMOND * 2}
              transform={`rotate(45 ${cx} ${cy})`}
              fill="var(--blk-bg-100)"
              stroke="var(--blk-color-desktop)"
              strokeWidth={2}
            />
          )}
        />
      </LineChart>
    </ChartContainer>
  );
}
