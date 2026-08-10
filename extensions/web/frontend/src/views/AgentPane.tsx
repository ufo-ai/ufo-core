import { ObjectPane } from "@/kernel/objects";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import { Connections } from "@/views/Connections";
import { Conversations } from "@/views/Conversations";
import { Overview } from "@/views/Overview";
import { Skills } from "@/views/Skills";
import { AgentUsage } from "@/views/Usage";
import type { AgentTab } from "@/lib/route";
import type { Agent } from "@/lib/types";

const SCHEDULED_TASK_KIND = "scheduled_task";

const TAB_LABELS: Record<AgentTab, string> = {
  overview: "Overview",
  conversations: "Conversations",
  scheduled: "Scheduled",
  connections: "Connections",
  skills: "Skills",
  usage: "Usage",
};

export type AgentPaneProps = {
  agent: Agent;
  tab: AgentTab;
  tabs: readonly AgentTab[];
  onTab: (tab: AgentTab) => void;
};

export function AgentPane({ agent, tab, tabs, onTab }: AgentPaneProps) {
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">{agent.name}</h1>
        <span className="font-mono text-mono opacity-(--muted-strong)">{agent.model}</span>
      </div>
      <TabStrip
        group="agent"
        tabs={tabs}
        current={tab}
        label={(name) => TAB_LABELS[name]}
        onPick={onTab}
      />
      <TabPanel
        group="agent"
        current={tab}
        className="flex-1 overflow-y-auto p-2xl"
        data-testid="panel"
      >
        {tab === "overview" ? <Overview agent={agent} /> : null}
        {tab === "scheduled" ? (
          <ObjectPane
            key={agent.id}
            agentId={agent.id}
            kind={SCHEDULED_TASK_KIND}
            label={TAB_LABELS.scheduled}
          />
        ) : null}
        {tab === "conversations" ? <Conversations agent={agent} /> : null}
        {tab === "connections" ? <Connections agent={agent} /> : null}
        {tab === "skills" ? <Skills agent={agent} /> : null}
        {tab === "usage" ? <AgentUsage agent={agent} /> : null}
      </TabPanel>
    </main>
  );
}
