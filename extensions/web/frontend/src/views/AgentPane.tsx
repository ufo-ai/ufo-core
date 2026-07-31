import { Chat } from "@/views/Chat";
import { Connections } from "@/views/Connections";
import { Conversations } from "@/views/Conversations";
import { Overview } from "@/views/Overview";
import { Skills } from "@/views/Skills";
import { Tasks } from "@/views/Tasks";
import { AgentUsage } from "@/views/Usage";
import { cn } from "@/lib/cn";
import type { AgentTab } from "@/lib/route";
import type { Agent } from "@/lib/types";

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
        <h1 className="m-0 text-body font-strong">{agent.name}</h1>
        <span className="font-mono text-mono opacity-(--muted-strong)">{agent.model}</span>
      </div>
      <div role="tablist" className="flex gap-2xs border-b border-edge px-lg pt-xs">
        {tabs.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === tab}
            onClick={() => onTab(name)}
            className={cn(
              "border-0 border-b-(length:--marker-width) border-b-transparent bg-transparent",
              "px-md py-xs text-inherit opacity-(--muted-soft)",
              name === tab && "border-b-ink font-strong opacity-100",
            )}
          >
            {name}
          </button>
        ))}
      </div>
      {tab === "chat" ? (
        <Chat agent={agent} />
      ) : (
        <div className="flex-1 overflow-y-auto p-2xl" data-testid="panel">
          {tab === "overview" ? <Overview agent={agent} /> : null}
          {tab === "tasks" ? <Tasks agent={agent} /> : null}
          {tab === "conversations" ? <Conversations agent={agent} /> : null}
          {tab === "connections" ? <Connections agent={agent} /> : null}
          {tab === "skills" ? <Skills agent={agent} /> : null}
          {tab === "usage" ? <AgentUsage agent={agent} /> : null}
        </div>
      )}
    </main>
  );
}
