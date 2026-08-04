import { Button } from "@/components/ui/button";
import type { Agent, Subagent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  subagents: Subagent[];
  onOpen: (agentId: string) => void;
  onOpenSubagent: (name: string) => void;
  onNewChat: (agentId: string) => void;
};

const ROW = "flex items-baseline gap-md border-b border-edge-soft py-md last:border-b-0";
const MODEL = "font-mono text-mono opacity-(--muted-strong)";

export function Agents({ agents, subagents, onOpen, onOpenSubagent, onNewChat }: AgentsProps) {
  return (
    <main className="flex flex-col gap-3xl overflow-y-auto p-2xl">
      <h1 className="m-0 text-title font-strong">Agents</h1>
      <ul className="m-0 flex max-w-section list-none flex-col p-0">
        {agents.map((agent) => (
          <li key={agent.id} className={ROW}>
            <button
              type="button"
              onClick={() => onOpen(agent.id)}
              className="border-0 bg-transparent p-0 text-left text-body text-inherit"
            >
              {agent.name}
              {agent.main ? " · main agent" : ""}
            </button>
            <span className={MODEL}>{agent.model}</span>
            <span className="ml-auto">
              <Button onClick={() => onNewChat(agent.id)}>New conversation</Button>
            </span>
          </li>
        ))}
        {subagents.map((subagent) => (
          <li key={subagent.name} className={ROW}>
            <button
              type="button"
              onClick={() => onOpenSubagent(subagent.name)}
              className="border-0 bg-transparent p-0 text-left text-body text-inherit"
            >
              {subagent.name} · subagent
            </button>
            <span className={MODEL}>{subagent.model ?? ""}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
