import { Button } from "@/components/ui/button";
import { CardGrid } from "@/kernel/cards";
import { PanelBlank, Section } from "@/kernel/panel";
import type { Agent, Subagent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  subagents: Subagent[];
  onOpen: (agentId: string) => void;
  onOpenSubagent: (name: string) => void;
  onNewChat: (agentId: string) => void;
};

const MODEL = "font-mono text-mono opacity-(--muted-strong)";
const SPAWNED = "Spawned by an agent for one task. A member does not address it.";
const INHERITS = "Spawned by an agent for one task, on that agent's model.";

export function Agents({ agents, subagents, onOpen, onOpenSubagent, onNewChat }: AgentsProps) {
  return (
    <main className="flex flex-col overflow-y-auto p-2xl">
      <h1 className="m-0 mb-2xl text-title font-strong">Agents</h1>
      <Section title="Workspace agents">
        <CardGrid
          rows={agents}
          rowKey={(agent) => agent.id}
          mark={{ shape: "square" }}
          primary={(agent) => agent.name}
          status={(agent) => <span className={MODEL}>{agent.model}</span>}
          body={(agent) => (agent.main ? "The agent this workspace answers with by default." : null)}
          action={(agent) => (
            <div className="flex flex-wrap gap-xs">
              <Button variant="send" onClick={() => onNewChat(agent.id)}>
                New conversation
              </Button>
              <Button variant="row" onClick={() => onOpen(agent.id)}>
                View
              </Button>
            </div>
          )}
        />
      </Section>
      <Section title="Subagents">
        {subagents.length ? (
          <CardGrid
            rows={subagents}
            rowKey={(subagent) => subagent.name}
            mark={{ shape: "square" }}
            primary={(subagent) => subagent.name}
            status={(subagent) =>
              subagent.model ? <span className={MODEL}>{subagent.model}</span> : null
            }
            body={(subagent) => (subagent.model ? SPAWNED : INHERITS)}
            action={(subagent) => (
              <Button variant="row" onClick={() => onOpenSubagent(subagent.name)}>
                View
              </Button>
            )}
          />
        ) : (
          <PanelBlank body="This deploy declares no subagents." />
        )}
      </Section>
    </main>
  );
}
