import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Table, Td, Th, tableFloor } from "@/components/ui/table";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelBlank,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { money } from "@/lib/money";
import { workspaceHash } from "@/lib/route";

const HOUR_SECONDS = 3600;
const RANGES = ["7d", "30d", "90d", "all"] as const;

type Range = (typeof RANGES)[number];

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

/** The heads each table states, named once: they size the tracks and, at a phone width, they are the
 *  label a stacked cell draws beside its own value. A breakdown names its first column after what it
 *  breaks down. */
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

function rangeFromHash(): Range {
  const value = new URLSearchParams(location.hash.split("?", 2)[1] ?? "").get("range");
  return RANGES.find((range) => range === value) ?? "30d";
}

function hours(seconds: number): string {
  return seconds / HOUR_SECONDS + "h";
}

function rangeLabel(range: Range): string {
  if (range === "all") return "All time";
  return "Last " + Number.parseInt(range) + " days";
}

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(value),
  );
}

function tokenCount(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
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

function delegation(lines: BreakdownLine[]): BreakdownLine[] {
  return [
    {
      label: "Apps",
      tokens: lines.filter((line) => !line.label).reduce((sum, line) => sum + line.tokens, 0),
      priced_micro_usd: lines
        .filter((line) => !line.label)
        .reduce((sum, line) => sum + line.priced_micro_usd, 0),
    },
    {
      label: "Subagents",
      tokens: lines.filter((line) => line.label).reduce((sum, line) => sum + line.tokens, 0),
      priced_micro_usd: lines
        .filter((line) => line.label)
        .reduce((sum, line) => sum + line.priced_micro_usd, 0),
    },
  ].filter((line) => line.tokens);
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
          {entry === "all" ? "All time" : entry.replace("d", " days")}
        </Button>
      ))}
    </div>
  );
}

function Figures({ details, range }: { details: UsageDetails; range: Range }) {
  const first = details.first_used_at ? "Since " + dateLabel(details.first_used_at) : "No usage";
  const activeDays = details.daily.filter((day) => day.tokens).length;
  const items = [
    { label: "Tokens", value: tokenCount(details.selected.tokens), note: changeNote(details, range) },
    { label: "All-time tokens", value: tokenCount(details.all_time.tokens), note: first },
    {
      label: "Daily average",
      value: tokenCount(details.selected.tokens / Math.max(details.daily.length, 1)),
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
  if (!rows.length) return <PanelBlank body="No tokens were used in this range." />;
  const top = Math.max(...rows.map((row) => row.tokens), 1);
  const points = rows
    .map((row, index) => {
      const x = rows.length === 1 ? 50 : (100 * index) / (rows.length - 1);
      return x + "," + (29 - (26 * row.tokens) / top);
    })
    .join(" ");
  const total = rows.reduce((sum, row) => sum + row.tokens, 0);
  return (
    <div className="rounded-panel border border-edge bg-surface p-xl">
      <svg
        viewBox="0 0 100 32"
        preserveAspectRatio="none"
        /* The stroke does not scale, so it is centred on the point rather than drawn inside the
           box: the first and last day would each lose half their line at the plot's own edge. */
        className="h-(--size-usage-chart) w-full overflow-visible"
        role="img"
        aria-label={tokenCount(total) + " tokens across " + rows.length + " daily buckets"}
      >
        <path d="M0 29 H100" fill="none" stroke="currentColor" opacity="0.2" />
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between text-small text-ink-soft">
        <span>{dateLabel(rows[0].day + "T00:00:00Z")}</span>
        <span>{dateLabel(rows[rows.length - 1].day + "T00:00:00Z")}</span>
      </div>
    </div>
  );
}

function Dimensions({ lines, empty }: { lines: DimensionLine[]; empty: string }) {
  if (!lines.length) return <PanelBlank body={empty} />;
  return (
    <Table columns={DIMENSION_COLUMNS} floor={tableFloor({ prose: 1, fact: 2 })}>
      <thead>
        <tr>
          {DIMENSION_COLUMNS.map((column) => <Th key={column}>{column}</Th>)}
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.dimension}>
            <Td className="w-full">{METERED[line.dimension] ?? line.dimension}</Td>
            <Td className="whitespace-nowrap">{line.amount.toLocaleString()}</Td>
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
      <thead>
        <tr>
          {columns.map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
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
      <thead>
        <tr>{CAP_COLUMNS.map((column) => <Th key={column}>{column}</Th>)}</tr>
      </thead>
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

const BILLING_PATH = "/ext/metronome/billing";

const REFILL_DOLLARS = 100;
const REFILL_BELOW_DOLLARS = 25;

type BillingReport =
  | { limited: false }
  | {
      limited: true;
      balance_micro_usd: number;
      refused_below_micro_usd: number;
      card_on_file: boolean;
      autopay_micro_usd?: number | null;
      autopay_below_micro_usd?: number | null;
    };

type BillingState = { phase: "unanswered" } | { phase: "ready"; report: BillingReport };

/** A balance is an exact figure, signed when the workspace is into its grace allowance, so it
 *  never takes `money`'s sub-cent shorthand. */
function dollars(micro: number): string {
  return (micro < 0 ? "-" : "") + "$" + (Math.abs(micro) / 1e6).toFixed(2);
}

/** An agent's line of the breakdown, headed the way every screen heads that agent. The line the
 *  rollup gives no agent id is the workspace's own jobs, whose label is the report's word rather
 *  than any agent's name. */
function named(line: BreakdownLine): BreakdownLine {
  return line.id ? { ...line, label: agentName(line.label) } : line;
}

function refillRule(amountMicro: number, belowMicro: number): string {
  return dollars(amountMicro) + " when the balance falls below " + dollars(belowMicro);
}

/** The workspace's prepaid balance, read from the metronome extension's own endpoint rather than
 *  the panel API. The route authenticates the session itself and answers admins only, so the
 *  section is drawn exactly when it answers and any refusal draws nothing. */
function Billing() {
  const mainAgent = useMainAgent();
  const [state, setState] = useState<BillingState>({ phase: "unanswered" });
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(BILLING_PATH, { credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) return;
        const report = (await res.json()) as BillingReport;
        if (live) setState({ phase: "ready", report });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [reloads]);
  if (state.phase !== "ready") return null;
  const report = state.report;
  const rule =
    report.limited &&
    typeof report.autopay_micro_usd === "number" &&
    typeof report.autopay_below_micro_usd === "number"
      ? refillRule(report.autopay_micro_usd, report.autopay_below_micro_usd)
      : null;

  async function refill(amountDollars: number | null, belowDollars: number | null) {
    if (busy || !mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "refill",
      kind: "billing",
      amount_dollars: amountDollars,
      below_dollars: belowDollars,
    });
    setBusy(false);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((count) => count + 1);
  }

  return (
    <Section title="Billing">
      {report.limited ? (
        <>
          <Facts
            rows={[
              {
                label: "Turns are refused below",
                value: dollars(report.refused_below_micro_usd),
              },
              { label: "Balance", value: dollars(report.balance_micro_usd) },
              { label: "Card on file", value: report.card_on_file ? "Yes" : "No" },
              { label: "Automatic refills", value: rule ?? "Off" },
            ]}
          />
          <OutcomeNotice state={notice} />
          {mainAgent ? (
            <div className="mt-lg">
              {rule ? (
                <Button busy={busy} onClick={() => refill(null, null)}>
                  Stop automatic refills
                </Button>
              ) : (
                <Button
                  busy={busy}
                  onClick={() => refill(REFILL_DOLLARS, REFILL_BELOW_DOLLARS)}
                >
                  {"Refill " + refillRule(REFILL_DOLLARS * 1e6, REFILL_BELOW_DOLLARS * 1e6)}
                </Button>
              )}
            </div>
          ) : null}
        </>
      ) : (
        <PanelBlank body="This workspace has no spending limit." />
      )}
    </Section>
  );
}

export function WorkspaceUsage() {
  const [range, setRange] = useState<Range>(rangeFromHash);
  const state = usePanelRead<WorkspaceUsageReport>("/workspace/usage?range=" + range);
  function pickRange(next: Range) {
    setRange(next);
    history.replaceState(null, "", workspaceHash("usage", { range: next }));
  }
  return (
    <Panel state={state}>
      {(payload) => {
        const report = payload.workspace?.usage ?? payload.usage;
        return (
          <>
            <Section title="Usage">
              <RangeControl range={range} onRange={pickRange} />
              <Figures details={report} range={range} />
            </Section>
            <Section title="Daily usage"><DailyHistory rows={report.daily} /></Section>
            {payload.workspace ? (
              <>
                <Section title="Apps">
                  <Breakdown heading="App" rows={payload.workspace.by_agent.map(named)} empty="No app used model tokens in this range." />
                </Section>
                <Section title="Delegation">
                  <Breakdown heading="Execution" rows={delegation(report.by_execution)} empty="No model tokens were used in this range." />
                </Section>
                <Section title="Members">
                  <Breakdown heading="Member" rows={payload.workspace.by_member} empty="No member used model tokens in this range." />
                </Section>
                <Section title="Origins">
                  <Breakdown heading="Origin" rows={payload.workspace.by_origin} empty="Nothing in this workspace used model tokens in this range." />
                </Section>
                <Section title="Models">
                  <Breakdown heading="Model" rows={report.by_model} empty="No models were used in this range." />
                </Section>
                <Section title="Other usage">
                  <Dimensions lines={otherDimensions(payload.workspace.by_dimension)} empty="Nothing else in this workspace carried a price in this range." />
                </Section>
              </>
            ) : (
              <>
                <Section title="Execution"><Breakdown heading="Execution" rows={report.by_execution} empty="No model tokens were used in this range." /></Section>
                <Section title="Models"><Breakdown heading="Model" rows={report.by_model} empty="No models were used in this range." /></Section>
                <Section title="Other usage"><Dimensions lines={otherDimensions(payload.by_dimension)} empty="Nothing else you ran in this range carried a price." /></Section>
              </>
            )}
            <Billing />
            <Section title="Caps"><Caps caps={payload.caps} empty="No spend cap is set on you." /></Section>
          </>
        );
      }}
    </Panel>
  );
}
