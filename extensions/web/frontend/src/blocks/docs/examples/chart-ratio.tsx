import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const SUITES = [
  { suite: "triage", passed: 18 },
  { suite: "digest", passed: 12 },
  { suite: "memory", passed: 7 },
  { suite: "routing", passed: 3 },
];

const CONFIG = { passed: { label: "Passed", color: "var(--blk-primary)" } } satisfies ChartConfig;

export function ChartRatioExample() {
  return (
    <ChartContainer config={CONFIG} ratio="half">
      <BarChart accessibilityLayer data={SUITES} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="suite" tickLine={false} axisLine={false} tickMargin={8} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Bar dataKey="passed" fill="var(--blk-color-passed)" radius={8} maxBarSize={56} />
      </BarChart>
    </ChartContainer>
  );
}
