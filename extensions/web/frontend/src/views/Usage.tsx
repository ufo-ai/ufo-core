import { Table, Td, Th } from "@/components/ui/table";
import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { money } from "@/lib/money";
import type { Agent } from "@/lib/types";

export type DimensionLine = { dimension: string; amount: number; priced_micro_usd: number };
export type Cap = { window_seconds: number; limit_micro_usd: number; on_breach: string };
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

export function hours(seconds: number): string {
  return seconds / 3600 + "h";
}

export function Heading({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">{children}</h2>;
}

export function Dimensions({ lines, empty }: { lines: DimensionLine[]; empty: string }) {
  if (!lines.length) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <Table>
      <thead>
        <tr>
          {["dimension", "units", "cost"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.dimension}>
            <Td>{line.dimension}</Td>
            <Td>{line.amount.toLocaleString()}</Td>
            <Td>{money(line.priced_micro_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function Caps({ caps, empty }: { caps: Cap[]; empty: string }) {
  if (!caps.length) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <Table>
      <thead>
        <tr>
          {["window", "limit", "on breach"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {caps.map((cap, index) => (
          <tr key={index}>
            <Td>{hours(cap.window_seconds)}</Td>
            <Td>{money(cap.limit_micro_usd)}</Td>
            <Td>{cap.on_breach}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function Subjects({ rows, empty }: { rows: SubjectLine[]; empty: string }) {
  if (!rows.length) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <Table>
      <thead>
        <tr>
          {["subject", "cost"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((entry) => (
          <tr key={entry.label}>
            <Td>{entry.label}</Td>
            <Td>{money(entry.priced_micro_usd)}</Td>
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
      {(report) => {
        return (
          <>
            <Heading>
              Last {hours(report.window_seconds)} · {money(report.total_micro_usd)}
            </Heading>
            <Dimensions lines={report.by_dimension} empty={"No spend in window for " + agent.name + "."} />
            <Heading>Caps for this agent</Heading>
            <Caps caps={report.caps} empty="No agent-scoped caps." />
          </>
        );
      }}
    </Panel>
  );
}

export function WorkspaceUsage() {
  const state = usePanelRead<WorkspaceUsageReport>("/workspace/usage");
  return (
    <Panel
      state={state}
    >
      {(payload) => {
        return (
          <>
            <Heading>
              Your spend · last {hours(payload.window_seconds)} · {money(payload.total_micro_usd)}
            </Heading>
            <Dimensions lines={payload.by_dimension} empty="No spend of yours in window." />
            <Heading>Your caps</Heading>
            <Caps caps={payload.caps} empty="No caps are set on you." />
            {payload.workspace ? (
              <>
                <Heading>Workspace · {money(payload.workspace.total_micro_usd)}</Heading>
                <Dimensions
                  lines={payload.workspace.by_dimension}
                  empty="No workspace spend in window."
                />
                <Heading>By member</Heading>
                <Subjects rows={payload.workspace.by_member} empty="None in window." />
                <Heading>By agent</Heading>
                <Subjects rows={payload.workspace.by_agent} empty="None in window." />
              </>
            ) : null}
          </>
        );
      }}
    </Panel>
  );
}
