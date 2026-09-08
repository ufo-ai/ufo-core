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

/** A measure's shape over the period it was measured, drawn as one series and the wash beneath it:
 *  no axes, no gridlines, no legend. A trend is read for its direction, and every mark that is not
 *  the trend is ink the reader looks past to find it. The panel is a `Card` and what
 *  the series counts is the `Stat` above it, so the plot draws no ground, no corner, no title and
 *  no figure of its own: it is the frame's content, and a fill inside a box that already carries a
 *  border is the one pairing the four ways to divide rules out (`extensions/web/AGENTS.md`,
 *  "Layout: the grid, the rhythm, and the four ways to divide"). A plot standing on no card is that
 *  table's **Fill** — something a member reads into rather than across — and the ground is then the
 *  caller's to draw around it.
 *
 *  `hover` is what one point says when the pointer reaches it. A plot that states a direction alone
 *  answers "is it rising"; a page whose reader also asks "how much on that day" states the point
 *  itself, and the caller words it because the caller owns the units and the period. A plot given no
 *  `hover` draws no readout and no cursor, which is every plot read for its shape alone.
 *
 *  `shape` is what the series is drawn as. A measure read for its direction is a curve; a measure
 *  read a period at a time — what one day cost, against the day beside it — is a column per period,
 *  because a curve between two days states a value on the hours between them that nothing measured.
 *
 *  `label` is what the series plots, said once: the plot answers as one graphic rather than as a
 *  hundred unnamed points, and a member reading by ear hears the measure instead of the markup. It
 *  is a required argument because a chart nobody can hear is what this would otherwise ship.
 *
 *  The series is the palette's first accent, the tone the portal already marks what is live in, and
 *  the wash is that same accent at a weight the fill states. One accent accounts for the whole
 *  graphic: a second token for the same colour at a second strength is a tone the reader has to
 *  account for twice. The curve is monotone, which bends between measured points without carrying
 *  either of them past its own value.
 *
 *  Nothing moves on arrival. A read that lands again redraws the series, and a plot that replays
 *  its growth on every refresh states that the measure changed when only the answer arrived.
 *
 *  The box is held from the first frame while the plotting library is fetched, so the curve lands
 *  inside a space already reserved for it rather than pushing the page down as it arrives. */
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

/** A count per period, drawn as one column each against the track it could have filled: the shape of
 *  the run, and the two ends of the period under it. Every column stands in its own track, so a
 *  short column reads as a small share of a known whole rather than as a gap — which is what lets
 *  forty of them be read without a scale beside them.
 *
 *  A column carries a tone rather than the plot deciding one, because the bands a period divides
 *  into are the page's to state: what has happened, what is happening, what is only projected. The
 *  two accents are the theme's, and `muted` is the ink step held back, so no page spells a colour.
 *
 *  The period is stated as its two ends and nothing between them. A tick under every column is forty
 *  labels nobody reads and a scale up the side restates a height the eye already compares, so what
 *  a reader needs is where the run starts and where it stops. Anything more is ink to look past.
 *
 *  It is drawn in flex and not by the chart library: a track behind every column is not a shape a
 *  cartesian plot draws, and forty fixed columns need no scale, no axis and no measuring pass.
 *
 *  The column is fixed and the run takes the whole width, so the gap is what gives: the plot fills
 *  the measure it is given rather than stopping short of it, which reads as a chart that failed to
 *  draw. A hairline is the floor, so a run long enough to crowd the pane still separates. The
 *  design's own rhythm — a three pixel column against a gap a shade wider — comes out of a period
 *  the width can hold, and a period far longer than that is a period to summarise, not to draw. */
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
