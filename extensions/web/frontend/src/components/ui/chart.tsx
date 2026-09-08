import { useEffect, useMemo, useState, type ComponentProps } from "react";
import type {
  Area as AreaMark,
  AreaChart as AreaPlot,
  Bar as BarMark,
  BarChart as BarPlot,
  ResponsiveContainer as Box,
  Tooltip as Readout,
} from "recharts";

import { cn } from "@/lib/cn";

/** Read as strings and numbers because recharts takes its colours as properties rather than as classes,
 *  and measures in numbers. */
const SERIES = "var(--color-series)";

const HAIRLINE = 2;

const WASH = 0.12;
const AIR = { top: HAIRLINE, right: 0, bottom: 0, left: 0 };

const plotted = (points: readonly number[]) => points.map((value, at) => ({ at, value }));

type Cartesian = {
  Area: typeof AreaMark;
  AreaChart: typeof AreaPlot;
  Bar: typeof BarMark;
  BarChart: typeof BarPlot;
  ResponsiveContainer: typeof Box;
  Tooltip: typeof Readout;
};

let loading: Promise<Cartesian> | null = null;

function useCartesian(): Cartesian | null {
  const [drawn, setDrawn] = useState<Cartesian | null>(null);
  useEffect(() => {
    let live = true;
    loading ??= import("recharts")
      .then((mark) => ({
        Area: mark.Area,
        AreaChart: mark.AreaChart,
        Bar: mark.Bar,
        BarChart: mark.BarChart,
        ResponsiveContainer: mark.ResponsiveContainer,
        Tooltip: mark.Tooltip,
      }))
      .catch((refusal) => {
        loading = null;
        throw refusal;
      });
    loading
      .then((mark) => {
        if (live) setDrawn(mark);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return drawn;
}

/** `label` is required because a chart nobody can hear is what this would otherwise ship. The box is
 *  held from the first frame while the plotting library is fetched, so the curve lands inside it. */
export function Chart({
  label,
  points,
  hover,
  shape = "area",
  className,
  ...props
}: ComponentProps<"div"> & {
  label: string;
  points: readonly number[];
  hover?: (at: number) => string;
  shape?: "area" | "bar";
}) {
  const data = useMemo(() => plotted(points), [points]);
  const cartesian = useCartesian();
  const readout =
    cartesian === null || hover === undefined ? null : (
      <cartesian.Tooltip
        isAnimationActive={false}
        cursor={
          shape === "bar"
            ? { fill: SERIES, fillOpacity: WASH }
            : { stroke: SERIES, strokeWidth: HAIRLINE }
        }
        content={({ active, payload }) => {
          const point = active ? payload?.[0]?.payload : undefined;
          return point == null ? null : (
            <span className="rounded-full bg-ink px-2xl py-sm text-small font-medium text-surface">
              {hover(Number(point.at))}
            </span>
          );
        }}
      />
    );
  return (
    <div
      data-slot="chart"
      role="img"
      aria-label={label}
      className={cn("aspect-video w-full min-w-0", className)}
      {...props}
    >
      {cartesian === null ? null : shape === "bar" ? (
      <cartesian.ResponsiveContainer>
        <cartesian.BarChart accessibilityLayer={false} data={data} margin={AIR}>
          <cartesian.Bar
            dataKey="value"
            fill={SERIES}
            radius={HAIRLINE}
            activeBar={{ fill: SERIES }}
            isAnimationActive={false}
          />
          {readout}
        </cartesian.BarChart>
      </cartesian.ResponsiveContainer>
      ) : (
      <cartesian.ResponsiveContainer>
        <cartesian.AreaChart accessibilityLayer={false} data={data} margin={AIR}>
          <cartesian.Area
            dataKey="value"
            type="monotone"
            stroke={SERIES}
            strokeWidth={HAIRLINE}
            fill={SERIES}
            fillOpacity={WASH}
            dot={false}
            activeDot={hover === undefined ? false : { fill: SERIES, r: HAIRLINE }}
            isAnimationActive={false}
          />
          {readout}
        </cartesian.AreaChart>
      </cartesian.ResponsiveContainer>
      )}
    </div>
  );
}

export type ChartBarTone = "muted" | "primary" | "secondary";

export type ChartBar = { value: number; tone: ChartBarTone };

const BAR_TONES: Record<ChartBarTone, string> = {
  muted: "bg-ink-faint",
  primary: "bg-live",
  secondary: "bg-blocked",
};

const FLOOR = 2;

/** Drawn in flex and not by the chart library: a track behind every column is not a shape a cartesian
 *  plot draws, and forty fixed columns need no scale, no axis and no measuring pass. */
export function ChartBars({
  label,
  bars,
  from,
  to,
  className,
  ...props
}: ComponentProps<"div"> & {
  label: string;
  bars: readonly ChartBar[];
  from: string;
  to: string;
}) {
  const tallest = bars.reduce((high, bar) => Math.max(high, bar.value), 0) || 1;
  return (
    <div
      data-slot="chart-bars"
      className={cn("flex w-full min-w-0 flex-col gap-2xl", className)}
      {...props}
    >
      <div
        data-slot="chart-plot"
        role="img"
        aria-label={label}
        className="flex h-(--size-plot) w-full items-end justify-between gap-hair"
      >
        {bars.map((bar, at) => (
          <span
            key={at}
            aria-hidden
            data-slot="chart-track"
            className="relative h-full w-(--size-bar) shrink-0 rounded-row bg-fill-strong"
          >
            <span
              data-slot="chart-bar"
              data-tone={bar.tone}
              className={cn("absolute bottom-0 w-full rounded-row", BAR_TONES[bar.tone])}
              style={{ height: `${Math.max((bar.value / tallest) * 100, FLOOR)}%` }}
            />
          </span>
        ))}
      </div>
      <div
        data-slot="chart-caption"
        className="flex w-full items-center justify-between text-mono text-ink-quiet"
      >
        <span>{from}</span>
        <span>{to}</span>
      </div>
    </div>
  );
}
