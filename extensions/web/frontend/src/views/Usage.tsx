import { Table, Td, Th } from "@/components/ui/table";
import { Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { cn } from "@/lib/cn";
import { money } from "@/lib/money";
import type { Agent } from "@/lib/types";

const HOUR_SECONDS = 3600;

const METERED: Record<string, string> = {
  tokens: "Model tokens",
  sandbox_tokens: "Sandbox tokens",
  egress: "Sandbox requests",
};

const ON_BREACH: Record<string, string> = {
  park: "Suspend the turn",
  reject: "Refuse new turns",
};

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
export type SubjectLine = { label: string; priced_micro_usd: number };

export type UsageReport = {
  window_seconds: number;
  total_micro_usd: number;
  by_dimension: DimensionLine[];
  caps: Cap[];
};

export type WorkspaceUsageReport = UsageReport & {
  workspace: {
    total_micro_usd: number;
    by_dimension: DimensionLine[];
    by_member: SubjectLine[];
    by_agent: SubjectLine[];
  } | null;
};

function hours(seconds: number): string {
  return seconds / HOUR_SECONDS + "h";
}

function windowLabel(seconds: number): string {
  const count = seconds / HOUR_SECONDS;
  return "Last " + count + (count === 1 ? " hour" : " hours");
}

function Figures({ items }: { items: { label: string; value: string; note: string }[] }) {
  return (
    <div className={cn("grid gap-lg", items.length > 1 && "grid-cols-2")}>
      {items.map((item) => (
        <div key={item.label} className="rounded-panel border border-edge bg-surface p-xl">
          <div className="text-small opacity-(--muted)">{item.label}</div>
          <div className="mt-2xs text-title font-strong">{item.value}</div>
          <div className="mt-2xs text-small opacity-(--muted)">{item.note}</div>
        </div>
      ))}
    </div>
  );
}

function Dimensions({ lines, empty }: { lines: DimensionLine[]; empty: string }) {
  if (!lines.length) return <PanelBlank body={empty} />;
  return (
    <Table>
      <thead>
        <tr>
          {["Metered", "Units", "Cost"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
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

function Caps({ caps, empty }: { caps: Cap[]; empty: string }) {
  if (!caps.length) return <PanelBlank body={empty} />;
  return (
    <Table>
      <thead>
        <tr>
          {["Window", "Limit", "On Breach"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
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

function Subjects({
  subject,
  rows,
  empty,
}: {
  subject: string;
  rows: SubjectLine[];
  empty: string;
}) {
  if (!rows.length) return <PanelBlank body={empty} />;
  return (
    <Table>
      <thead>
        <tr>
          {[subject, "Cost"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((entry) => (
          <tr key={entry.label}>
            <Td className="w-full">{entry.label}</Td>
            <Td className="whitespace-nowrap">{money(entry.priced_micro_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function AgentUsage({ agent }: { agent: Agent }) {
  const state = usePanelRead<UsageReport>("/agents/" + agent.id + "/usage");
  return (
    <Panel
      state={state}
      failed={(message) => (
        <PanelEmpty>
          {message.startsWith("Error 404")
            ? "Usage for this agent is not shared with you."
            : message}
        </PanelEmpty>
      )}
    >
      {(report) => (
        <>
          <Section title="Usage">
            <Figures
              items={[
                {
                  label: "This agent",
                  value: money(report.total_micro_usd),
                  note: windowLabel(report.window_seconds),
                },
              ]}
            />
          </Section>
          <Section title="Spend">
            <Dimensions
              lines={report.by_dimension}
              empty={"Nothing " + agent.name + " ran in this window carried a price."}
            />
          </Section>
          <Section title="Caps">
            <Caps caps={report.caps} empty="No spend cap is set on this agent." />
          </Section>
        </>
      )}
    </Panel>
  );
}

export function WorkspaceUsage() {
  const state = usePanelRead<WorkspaceUsageReport>("/workspace/usage");
  return (
    <Panel state={state}>
      {(payload) => {
        const note = windowLabel(payload.window_seconds);
        return (
          <>
            <Section title="Usage">
              <Figures
                items={[
                  { label: "You", value: money(payload.total_micro_usd), note },
                  ...(payload.workspace
                    ? [
                        {
                          label: "Workspace",
                          value: money(payload.workspace.total_micro_usd),
                          note,
                        },
                      ]
                    : []),
                ]}
              />
            </Section>
            <Section title="Your spend">
              <Dimensions
                lines={payload.by_dimension}
                empty="Nothing you ran in this window carried a price."
              />
            </Section>
            <Section title="Your caps">
              <Caps caps={payload.caps} empty="No spend cap is set on you." />
            </Section>
            {payload.workspace ? (
              <>
                <Section title="Workspace spend">
                  <Dimensions
                    lines={payload.workspace.by_dimension}
                    empty="Nothing in this workspace carried a price in this window."
                  />
                </Section>
                <Section title="Spend by member">
                  <Subjects
                    subject="Member"
                    rows={payload.workspace.by_member}
                    empty="No member was charged in this window."
                  />
                </Section>
                <Section title="Spend by agent">
                  <Subjects
                    subject="Agent"
                    rows={payload.workspace.by_agent}
                    empty="No agent was charged in this window."
                  />
                </Section>
              </>
            ) : null}
          </>
        );
      }}
    </Panel>
  );
}
