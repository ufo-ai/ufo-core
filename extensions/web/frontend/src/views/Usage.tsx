import { Button } from "@/components/ui/button";
import { Chart } from "@/components/ui/chart";
import { Table, Td, Th, tableFloor } from "@/components/ui/table";
import type { Placement } from "@/kernel/pager";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { money } from "@/lib/money";

const HOUR_SECONDS = 3600;
const DAY_SECONDS = 86_400;
const RANGES = ["1d", "7d", "30d", "90d", "all"] as const;
const DEFAULT_RANGE = "30d";

type Range = (typeof RANGES)[number];

const RANGE_PERIODS: Record<Range, string> = {
  "1d": "24 hours",
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
  all: "All time",
};

const METERED: Record<string, string> = {
  tokens: "Model tokens",
  sandbox_tokens: "Sandbox tokens",
  egress: "Sandbox requests",
  images: "Generated images",
  videos: "Generated videos",
};

const ON_BREACH: Record<string, string> = {
  park: "Suspend the turn",
  reject: "Refuse new turns",
};

const DIMENSION_COLUMNS = ["Metered", "Units", "Cost"];
const BREAKDOWN_COLUMNS = ["Tokens", "Token Share", "Cost", "$/Mtok"];
const CAP_COLUMNS = ["Window", "Limit", "On Breach"];

export type DimensionLine = {
  dimension: string;
  amount: number;
  priced_micro_usd: number;
};
export type Cap = {
  window_seconds: number;
  limit_micro_usd: number;
  on_breach: string;
};
export type BreakdownLine = {
  id?: string | null;
  label: string;
  tokens: number;
  priced_micro_usd: number;
};
export type UsageTotal = {
  tokens: number;
  token_micro_usd: number;
  total_micro_usd: number;
};
export type DailyLine = UsageTotal & { day: string };
export type UsageDetails = {
  selected: UsageTotal;
  all_time: UsageTotal;
  first_used_at: string | null;
  previous_tokens: number | null;
  daily: DailyLine[];
  by_execution: BreakdownLine[];
  by_model: BreakdownLine[];
};

export type UsageReport = {
  window_seconds: number | null;
  total_micro_usd: number;
  by_dimension: DimensionLine[];
  caps: Cap[];
  usage: UsageDetails;
};

export type WorkspaceUsageReport = UsageReport & {
  workspace: {
    total_micro_usd: number;
    by_dimension: DimensionLine[];
    by_member: BreakdownLine[];
    by_agent: BreakdownLine[];
    by_origin: BreakdownLine[];
    usage: UsageDetails;
  } | null;
};

function rangeOf(place: Placement): Range {
  return RANGES.find((range) => range === place.range) ?? DEFAULT_RANGE;
}

function hours(seconds: number): string {
  return seconds / HOUR_SECONDS + "h";
}

function rangeLabel(range: Range): string {
  return range === "all" ? RANGE_PERIODS.all : "Last " + RANGE_PERIODS[range];
}

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(value),
  );
}

function dayLabel(day: string): string {
  return dateLabel(day + "T00:00:00Z");
}

function periodDays(details: UsageDetails, windowSeconds: number | null): number {
  if (windowSeconds === null) return Math.max(details.daily.length, 1);
  return Math.max(Math.round(windowSeconds / DAY_SECONDS), 1);
}

function tokenCount(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function count(value: number): string {
  return new Intl.NumberFormat("en").format(value);
}

function percent(value: number, total: number): string {
  if (!total) return "—";
  const share = (100 * value) / total;
  return share < 0.1 && share > 0 ? "<0.1%" : share.toFixed(1) + "%";
}

function blended(tokens: number, microUsd: number): string {
  return tokens ? "$" + (microUsd / tokens).toFixed(2) + "/Mtok" : "—";
}

function otherDimensions(lines: DimensionLine[]): DimensionLine[] {
  return lines.filter((line) => line.dimension !== "tokens" && line.dimension !== "sandbox_tokens");
}

function changeNote(details: UsageDetails, range: Range): string {
  if (range === "all" || details.previous_tokens === null) return rangeLabel(range);
  if (!details.previous_tokens) return rangeLabel(range);
  const change = Math.round(
    (100 * (details.selected.tokens - details.previous_tokens)) / details.previous_tokens,
  );
  return rangeLabel(range) + " · " + (change >= 0 ? "+" : "") + change + "%";
}

function RangeControl({ range, onRange }: { range: Range; onRange: (range: Range) => void }) {
  return (
    <div className="flex flex-wrap gap-xs" aria-label="Usage range">
      {RANGES.map((entry) => (
        <Button
          key={entry}
          variant="option"
          aria-pressed={range === entry}
          onClick={() => onRange(entry)}
        >
          {RANGE_PERIODS[entry]}
        </Button>
      ))}
    </div>
  );
}

function Figures({
  details,
  range,
  windowSeconds,
}: {
  details: UsageDetails;
  range: Range;
  windowSeconds: number | null;
}) {
  const first = details.first_used_at ? "Since " + dateLabel(details.first_used_at) : "No usage";
  const days = periodDays(details, windowSeconds);
  const activeDays = Math.min(details.daily.filter((day) => day.tokens).length, days);
  const items = [
    { label: "Tokens", value: tokenCount(details.selected.tokens), note: changeNote(details, range) },
    { label: "All-time tokens", value: tokenCount(details.all_time.tokens), note: first },
    {
      label: "Daily average",
      value: tokenCount(details.selected.tokens / days),
      note: activeDays + (activeDays === 1 ? " active day" : " active days"),
    },
    {
      label: "Blended cost",
      value: blended(details.selected.tokens, details.selected.token_micro_usd),
      note: rangeLabel(range),
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-lg">
      {items.map((item) => (
        <div key={item.label} className="rounded-panel border border-edge bg-surface p-xl">
          <div className="text-small text-ink-soft">{item.label}</div>
          <div className="mt-2xs text-title font-strong">{item.value}</div>
          <div className="mt-2xs text-small text-ink-soft">{item.note}</div>
        </div>
      ))}
    </div>
  );
}

function DailyHistory({ rows }: { rows: DailyLine[] }) {
  if (!rows.length) return <PanelBlank body="Nothing was spent in this range." />;
  const total = rows.reduce((sum, row) => sum + row.total_micro_usd, 0);
  return (
    <div className="rounded-panel border border-edge bg-surface p-xl">
      <Chart
        className="aspect-auto h-(--size-usage-chart)"
        shape="bar"
        label={money(total) + " across " + rows.length + " daily buckets"}
        points={rows.map((row) => row.total_micro_usd)}
        hover={(at) => dayLabel(rows[at].day) + " · " + money(rows[at].total_micro_usd)}
      />
      <div className="flex justify-between text-small text-ink-soft">
        <span>{dayLabel(rows[0].day)}</span>
        <span>{dayLabel(rows[rows.length - 1].day)}</span>
      </div>
    </div>
  );
}

function Heads({ columns }: { columns: string[] }) {
  return (
    <thead>
      <tr>
        {columns.map((column) => (
          <Th key={column}>{column}</Th>
        ))}
      </tr>
    </thead>
  );
}

function Dimensions({ lines, empty }: { lines: DimensionLine[]; empty: string }) {
  if (!lines.length) return <PanelBlank body={empty} />;
  return (
    <Table columns={DIMENSION_COLUMNS} floor={tableFloor({ prose: 1, fact: 2 })}>
      <Heads columns={DIMENSION_COLUMNS} />
      <tbody>
        {lines.map((line) => (
          <tr key={line.dimension}>
            <Td className="w-full">{METERED[line.dimension] ?? line.dimension}</Td>
            <Td className="whitespace-nowrap">{count(line.amount)}</Td>
            <Td className="whitespace-nowrap">{money(line.priced_micro_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function Breakdown({
  heading,
  rows,
  empty,
}: {
  heading: string;
  rows: BreakdownLine[];
  empty: string;
}) {
  if (!rows.length) return <PanelBlank body={empty} />;
  const tokens = rows.reduce((sum, row) => sum + row.tokens, 0);
  const cost = rows.reduce((sum, row) => sum + row.priced_micro_usd, 0);
  const columns = [heading, ...BREAKDOWN_COLUMNS];
  return (
    <Table columns={columns} floor={tableFloor({ prose: 1, fact: 4 })}>
      <Heads columns={columns} />
      <tbody>
        {rows.map((row) => (
          <tr key={row.id ?? row.label}>
            <Td className="w-full">{row.label || "This app"}</Td>
            <Td className="whitespace-nowrap">{tokenCount(row.tokens)}</Td>
            <Td className="whitespace-nowrap">{percent(row.tokens, tokens)}</Td>
            <Td className="whitespace-nowrap">{money(row.priced_micro_usd)}</Td>
            <Td className="whitespace-nowrap">{blended(row.tokens, row.priced_micro_usd)}</Td>
          </tr>
        ))}
        <tr>
          <Td className="w-full font-strong">Total</Td>
          <Td className="whitespace-nowrap font-strong">{tokenCount(tokens)}</Td>
          <Td className="whitespace-nowrap">{percent(tokens, tokens)}</Td>
          <Td className="whitespace-nowrap font-strong">{money(cost)}</Td>
          <Td className="whitespace-nowrap">{blended(tokens, cost)}</Td>
        </tr>
      </tbody>
    </Table>
  );
}

function Caps({ caps, empty }: { caps: Cap[]; empty: string }) {
  if (!caps.length) return <PanelBlank body={empty} />;
  return (
    <Table columns={CAP_COLUMNS} floor={tableFloor({ prose: 1, fact: 2 })}>
      <Heads columns={CAP_COLUMNS} />
      <tbody>
        {caps.map((cap, index) => (
          <tr key={index}>
            <Td className="whitespace-nowrap">{hours(cap.window_seconds)}</Td>
            <Td className="whitespace-nowrap">{money(cap.limit_micro_usd)}</Td>
            <Td className="w-full">{ON_BREACH[cap.on_breach] ?? cap.on_breach}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function named(line: BreakdownLine): BreakdownLine {
  return line.id ? { ...line, label: agentName(line.label) } : line;
}

export function WorkspaceUsage({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const range = rangeOf(place);
  const state = usePanelRead<WorkspaceUsageReport>("/workspace/usage?range=" + range);
  return (
    <Panel state={state}>
      {(payload) => {
        const rollup = payload.workspace;
        const report = rollup?.usage ?? payload.usage;
        return (
          <>
            <Section title="Usage">
              <RangeControl range={range} onRange={(next) => onPlace({ range: next })} />
              <Figures details={report} range={range} windowSeconds={payload.window_seconds} />
            </Section>
            <Section title="Daily spend">
              <DailyHistory rows={report.daily} />
            </Section>
            {rollup ? (
              <>
                <Section title="Apps">
                  <Breakdown heading="App" rows={rollup.by_agent.map(named)} empty="No app used model tokens in this range." />
                </Section>
                <Section title="Members">
                  <Breakdown heading="Member" rows={rollup.by_member} empty="No member used model tokens in this range." />
                </Section>
                <Section title="Origins">
                  <Breakdown heading="Origin" rows={rollup.by_origin} empty="No origin used model tokens in this range." />
                </Section>
              </>
            ) : null}
            <Section title="Delegation">
              <Breakdown heading="Execution" rows={report.by_execution} empty="No model tokens were used in this range." />
            </Section>
            <Section title="Models">
              <Breakdown heading="Model" rows={report.by_model} empty="No model was used in this range." />
            </Section>
            <Section title="Other usage">
              <Dimensions lines={otherDimensions((rollup ?? payload).by_dimension)} empty="Nothing else carried a price in this range." />
            </Section>
            <Section title="Spend caps">
              <Caps caps={payload.caps} empty="No spend cap is set on you." />
            </Section>
          </>
        );
      }}
    </Panel>
  );
}
