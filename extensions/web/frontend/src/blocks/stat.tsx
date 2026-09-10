import type { ReactNode } from "react";
import { IconMinus, IconTrendingDown, IconTrendingUp } from "@tabler/icons-react";

import "@/blocks/stat.css";

const DELTA_ICON = 12;
const SPARK_GAP = 6;
const SPARK_FLOOR = 6;
const SPARK_RADIUS = 4;
const SPARK_STROKE = 1.5;
const FILL_TOP = 1;
const FILL_STEP = 0.22;
const FILL_FLOOR = 0.28;

export type DeltaDirection = "up" | "down" | "flat";
export type DeltaTone = "auto" | "positive" | "negative" | "neutral";
export type StatDelta = { value: string; direction: DeltaDirection; tone?: DeltaTone };

type StatProps = {
  label: ReactNode;
  value: string;
  media?: ReactNode;
  delta?: StatDelta;
  sub?: string;
  size?: "default" | "sm" | "lg";
  casing?: "upper" | "sentence";
  font?: "sans" | "mono";
};

function DeltaIcon({ direction }: { direction: DeltaDirection }) {
  switch (direction) {
    case "up":
      return <IconTrendingUp size={DELTA_ICON} stroke={1.5} />;
    case "down":
      return <IconTrendingDown size={DELTA_ICON} stroke={1.5} />;
    case "flat":
      return <IconMinus size={DELTA_ICON} stroke={1.5} />;
  }
}

/** A signed figure and its direction glyph. `auto` reads the colour off the direction; a tone names it instead. */
export function Delta({ value, direction, tone = "auto" }: StatDelta) {
  return (
    <span className="blk-delta" data-direction={direction} data-tone={tone}>
      <DeltaIcon direction={direction} />
      {value}
    </span>
  );
}

/** A labelled number, with the change against the period before it and a caption under both. */
export function Stat({
  label,
  value,
  media,
  delta,
  sub,
  size = "default",
  casing = "upper",
  font = "sans",
}: StatProps) {
  return (
    <div className="blk-stat" data-size={size} data-casing={casing} data-font={font}>
      <span className="blk-stat-label">
        {media ? <span className="blk-stat-media">{media}</span> : null}
        {label}
      </span>
      <span className="blk-stat-value">{value}</span>
      {delta ? <Delta {...delta} /> : null}
      {sub ? <span className="blk-stat-sub">{sub}</span> : null}
    </div>
  );
}

/** A `Stat` on its own ground, for a grid of measures that each need a surface. */
export function StatTile(props: StatProps) {
  return (
    <div className="blk-stat-tile">
      <Stat {...props} />
    </div>
  );
}

/** An even row of tiles. The column count is the widest it takes: it halves at 720px and drops to one at 360px. */
export function StatGrid({ columns = 2, children }: { columns?: 2 | 3 | 4; children: ReactNode }) {
  return (
    <div className="blk-stat-grid-box">
      <div className="blk-stat-grid" data-columns={columns}>
        {children}
      </div>
    </div>
  );
}

/** A measure against the target it is counted towards, with the share reached drawn between them. */
export function ProgressStat({
  label,
  value,
  percent,
  target,
  achievedLabel,
}: {
  label: string;
  value: string;
  percent: number;
  target: string;
  achievedLabel?: string;
}) {
  return (
    <div className="blk-progress-stat">
      <span className="blk-stat-label">{label}</span>
      <span className="blk-progress-stat-value">{value}</span>
      <div className="blk-progress-stat-track">
        <div className="blk-progress-stat-fill" style={{ width: `${percent}%` }} />
      </div>
      <div className="blk-progress-stat-footer">
        <span>{achievedLabel ?? `${percent}% achieved`}</span>
        <span>{target}</span>
      </div>
    </div>
  );
}

/** A run of values drawn small enough to sit inside a row of text. */
export function Sparkline({
  values,
  kind,
  width = 88,
  height = 32,
}: {
  values: number[];
  kind: "bars" | "line";
  width?: number;
  height?: number;
}) {
  const peak = Math.max(...values) || 1;
  const ranked = [...values].sort((a, b) => b - a);
  const band = (width - SPARK_GAP * (values.length - 1)) / values.length;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const reach = height - SPARK_STROKE;
  const line = values
    .map((value, at) => {
      const y = SPARK_STROKE / 2 + (1 - value / peak) * reach;
      return `${at === 0 ? "M" : "L"}${at * step} ${y}`;
    })
    .join(" ");
  return (
    <svg
      className="blk-sparkline"
      data-kind={kind}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
    >
      {kind === "bars"
        ? values.map((value, at) => {
            const tall = Math.max(SPARK_FLOOR, (value / peak) * height);
            const left = at * (band + SPARK_GAP);
            const top = height - tall;
            const round = Math.min(SPARK_RADIUS, band / 2, tall);
            return (
              <path
                key={at}
                data-bar=""
                d={`M${left} ${height}V${top + round}A${round} ${round} 0 0 1 ${left + round} ${top}H${left + band - round}A${round} ${round} 0 0 1 ${left + band} ${top + round}V${height}Z`}
                fill="var(--blk-text-1)"
                fillOpacity={Math.max(FILL_FLOOR, FILL_TOP - ranked.indexOf(value) * FILL_STEP)}
              />
            );
          })
        : (
            <path
              d={line}
              fill="none"
              stroke="var(--blk-text-1)"
              strokeWidth={SPARK_STROKE}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
    </svg>
  );
}

/** A named thing and its count on one tile, with its shape over time on the right. */
export function StatRow({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <div className="blk-stat-row">
      <div className="blk-stat-row-text">
        <span className="blk-stat-row-title">{title}</span>
        <span className="blk-stat-row-sub">{sub}</span>
      </div>
      {children}
    </div>
  );
}

/** A stack of `StatRow` tiles, optionally fading out where the list runs past the panel. */
export function StatRows({ fade = false, children }: { fade?: boolean; children: ReactNode }) {
  return (
    <div className="blk-stat-rows" data-fade={fade ? "on" : "off"}>
      {children}
    </div>
  );
}
