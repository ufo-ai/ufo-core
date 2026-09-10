import {
  createContext,
  useContext,
  useId,
  useMemo,
  type ComponentType,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  Legend,
  ResponsiveContainer,
  Tooltip,
  type DefaultLegendContentProps,
  type TooltipContentProps,
} from "recharts";

import "@/blocks/chart.css";

const SCHEMES = ["light", "dark"] as const;

type Scheme = (typeof SCHEMES)[number];

export type ChartSeries = { label?: ReactNode; icon?: ComponentType } & (
  | { color?: string; theme?: never }
  | { color?: never; theme: Record<Scheme, string> }
);

export type ChartConfig = Record<string, ChartSeries>;

export type ChartRatio = "video" | "square" | "half";

const ChartContext = createContext<ChartConfig | null>(null);

function useChartConfig(): ChartConfig {
  const config = useContext(ChartContext);
  if (!config) throw new Error("a chart part must be rendered inside a ChartContainer");
  return config;
}

function seriesFor(config: ChartConfig, entry: unknown, key: string): ChartSeries | undefined {
  if (typeof entry !== "object" || entry === null) return undefined;
  const record = entry as Record<string, unknown>;
  const datum = record.payload;
  const named = typeof datum === "object" && datum !== null ? (datum as Record<string, unknown>)[key] : undefined;
  const own = record[key];
  const configKey = typeof own === "string" ? own : typeof named === "string" ? named : key;
  return config[configKey] ?? config[key];
}

/** The colour of every series in one config, written out per scheme for the chart that carries the id. */
export function ChartStyle({ id, config }: { id: string; config: ChartConfig }) {
  const coloured = Object.entries(config).filter(([, series]) => series.theme ?? series.color);
  if (!coloured.length) return null;
  const declare = (scheme: Scheme) =>
    coloured
      .map(([key, series]) => {
        const color = series.theme?.[scheme] ?? series.color;
        return color ? `  --blk-color-${key}: ${color};` : null;
      })
      .filter(Boolean)
      .join("\n");
  const css = [
    `[data-chart=${id}] {\n${declare("light")}\n}`,
    `:root[data-scheme="light"] [data-chart=${id}] {\n${declare("light")}\n}`,
    `:root[data-scheme="dark"] [data-chart=${id}] {\n${declare("dark")}\n}`,
    `@media (prefers-color-scheme: dark) {\n:root:not([data-scheme="light"]) [data-chart=${id}] {\n${declare("dark")}\n}\n}`,
  ].join("\n");
  return <style dangerouslySetInnerHTML={{ __html: css }} />;
}

/** The ground a recharts plot is drawn on: it names each series' colour and holds the plot's box. */
export function ChartContainer({
  config,
  ratio = "video",
  children,
  className,
}: {
  config: ChartConfig;
  ratio?: ChartRatio;
  children: ReactNode;
  className?: string;
}) {
  const id = `blk-chart-${useId().replace(/:/g, "")}`;
  const colors = Object.fromEntries(
    Object.entries(config)
      .filter(([, series]) => series.color)
      .map(([key, series]) => [`--blk-color-${key}`, series.color]),
  ) as CSSProperties;
  return (
    <ChartContext.Provider value={config}>
      <div
        className={className ? `blk-chart ${className}` : "blk-chart"}
        data-chart={id}
        data-ratio={ratio}
        style={colors}
      >
        <ChartStyle id={id} config={config} />
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

/** An axis tick on two lines: what `lines` returns for the tick's value, the second line set back. */
export function ChartTick({
  x,
  y,
  payload,
  lines,
}: {
  x?: string | number;
  y?: string | number;
  payload?: { value?: string | number };
  lines: (value: string) => [string, string?];
}) {
  const [first, second] = lines(`${payload?.value ?? ""}`);
  return (
    <text className="blk-chart-tick" x={x} y={y} textAnchor="middle">
      <tspan x={x} dy="0.9em">
        {first}
      </tspan>
      {second === undefined ? null : (
        <tspan className="blk-chart-tick-detail" x={x} dy="1.2em">
          {second}
        </tspan>
      )}
    </text>
  );
}

export const ChartTooltip = Tooltip;

/** What one point on a plot says: the period, then every series measured at it. */
export function ChartTooltipContent({
  active,
  payload,
  label,
  labelFormatter,
  formatter,
  color,
  indicator = "dot",
  hideLabel = false,
  hideIndicator = false,
  labelKey,
  nameKey,
}: Partial<TooltipContentProps> & {
  color?: string;
  indicator?: "dot" | "line" | "dashed";
  hideLabel?: boolean;
  hideIndicator?: boolean;
  labelKey?: string;
  nameKey?: string;
}) {
  const config = useChartConfig();
  const heading = useMemo(() => {
    if (hideLabel || !payload?.length) return null;
    const [first] = payload;
    const key = `${labelKey ?? first?.dataKey ?? first?.name ?? "value"}`;
    const series = seriesFor(config, first, key);
    const named =
      !labelKey && typeof label === "string" ? (config[label]?.label ?? label) : series?.label;
    if (labelFormatter)
      return <div className="blk-chart-tooltip-label">{labelFormatter(named, payload)}</div>;
    if (!named) return null;
    return <div className="blk-chart-tooltip-label">{named}</div>;
  }, [config, hideLabel, label, labelFormatter, labelKey, payload]);

  if (!active || !payload?.length) return null;
  const nested = payload.length === 1 && indicator !== "dot";
  return (
    <div className="blk-chart-tooltip">
      {nested ? null : heading}
      <div className="blk-chart-tooltip-items">
        {payload
          .filter((item) => item.type !== "none")
          .map((item, at) => {
            const key = `${nameKey ?? item.name ?? item.dataKey ?? "value"}`;
            const series = seriesFor(config, item, key);
            const Icon = series?.icon;
            const mark = color ?? item.payload?.fill ?? item.color;
            return (
              <div className="blk-chart-tooltip-row" data-indicator={indicator} key={at}>
                {formatter && item.value !== undefined && item.name ? (
                  formatter(item.value, item.name, item, at, payload)
                ) : (
                  <>
                    {Icon ? (
                      <Icon />
                    ) : hideIndicator ? null : (
                      <span
                        className="blk-chart-tooltip-mark"
                        data-indicator={indicator}
                        style={{ "--blk-chart-mark": mark } as CSSProperties}
                      />
                    )}
                    <div className="blk-chart-tooltip-measure" data-nested={nested ? "on" : "off"}>
                      <div className="blk-chart-tooltip-names">
                        {nested ? heading : null}
                        <span className="blk-chart-tooltip-name">{series?.label ?? item.name}</span>
                      </div>
                      {item.value == null ? null : (
                        <span className="blk-chart-tooltip-value">
                          {typeof item.value === "number"
                            ? item.value.toLocaleString()
                            : String(item.value)}
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}
      </div>
    </div>
  );
}

export const ChartLegend = Legend;

/** The series a plot draws, named once each beside it. */
export function ChartLegendContent({
  payload,
  verticalAlign = "bottom",
  hideIcon = false,
  nameKey,
}: DefaultLegendContentProps & { hideIcon?: boolean; nameKey?: string }) {
  const config = useChartConfig();
  if (!payload?.length) return null;
  return (
    <div className="blk-chart-legend" data-align={verticalAlign}>
      {payload
        .filter((item) => item.type !== "none")
        .map((item, at) => {
          const key = `${nameKey ?? item.dataKey ?? "value"}`;
          const series = seriesFor(config, item, key);
          const Icon = series?.icon;
          return (
            <span className="blk-chart-legend-item" key={at}>
              {Icon && !hideIcon ? (
                <Icon />
              ) : (
                <span className="blk-chart-legend-mark" style={{ background: item.color }} />
              )}
              {series?.label ?? item.value}
            </span>
          );
        })}
    </div>
  );
}
