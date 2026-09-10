import { Bar, BarChart, CartesianGrid, Cell, LabelList, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const FLOW = [
  { month: "January", net: 186 },
  { month: "February", net: 205 },
  { month: "March", net: -207 },
  { month: "April", net: 173 },
  { month: "May", net: -209 },
  { month: "June", net: 214 },
];

const CONFIG = { net: { label: "Net" } } satisfies ChartConfig;

export function ChartBarNegative() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={FLOW} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel hideIndicator />} />
        <Bar dataKey="net" radius={8} maxBarSize={56}>
          <LabelList dataKey="net" position="top" offset={8} className="blk-chart-label-muted" />
          {FLOW.map((row) => (
            <Cell
              key={row.month}
              fill={row.net > 0 ? "var(--blk-primary)" : "var(--blk-secondary)"}
            />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
