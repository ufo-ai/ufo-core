import { Label, PolarRadiusAxis, RadialBar, RadialBarChart } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [{ month: "January", desktop: 1260, mobile: 570 }];

const TOTAL = VISITS[0].desktop + VISITS[0].mobile;

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

export function ChartRadialStacked() {
  return (
    <ChartContainer config={CONFIG} className="blk-chart-half">
      <RadialBarChart data={VISITS} endAngle={180} innerRadius={80} outerRadius={130}>
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <PolarRadiusAxis tick={false} tickLine={false} axisLine={false}>
          <Label
            content={({ viewBox }) =>
              viewBox && "cx" in viewBox && "cy" in viewBox ? (
                <text x={viewBox.cx} y={viewBox.cy} textAnchor="middle">
                  <tspan x={viewBox.cx} y={(viewBox.cy ?? 0) - 18} className="blk-chart-figure">
                    {TOTAL.toLocaleString()}
                  </tspan>
                  <tspan x={viewBox.cx} y={(viewBox.cy ?? 0) + 4} className="blk-chart-label-muted">
                    Visitors
                  </tspan>
                </text>
              ) : null
            }
          />
        </PolarRadiusAxis>
        <RadialBar
          dataKey="mobile"
          stackId="visits"
          cornerRadius={8}
          fill="var(--blk-color-mobile)"
          stroke="none"
        />
        <RadialBar
          dataKey="desktop"
          stackId="visits"
          cornerRadius={8}
          fill="var(--blk-color-desktop)"
          stroke="none"
        />
      </RadialBarChart>
    </ChartContainer>
  );
}
