import { Button } from "@/components/ui/button";
import type { Agent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  onOpen: (agentId: string) => void;
  onNewChat: (agentId: string) => void;
};

export function Agents({ agents, onOpen, onNewChat }: AgentsProps) {
  return (
    <main className="flex flex-col gap-3xl overflow-y-auto p-2xl">
      <h1 className="m-0 text-title font-strong">Agents</h1>
      <ul className="m-0 flex max-w-section list-none flex-col p-0">
        {agents.map((agent) => (
          <li
            key={agent.id}
            className="flex items-baseline gap-md border-b border-edge-soft py-md last:border-b-0"
          >
            <button
              type="button"
              onClick={() => onOpen(agent.id)}
              className="border-0 bg-transparent p-0 text-left text-body text-inherit"
            >
              {agent.name}
              {agent.main ? " · main agent" : ""}
            </button>
            <span className="font-mono text-mono opacity-(--muted-strong)">{agent.model}</span>
            <span className="ml-auto">
              <Button onClick={() => onNewChat(agent.id)}>New conversation</Button>
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
