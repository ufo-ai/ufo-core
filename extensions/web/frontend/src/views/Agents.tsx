import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { CardGrid } from "@/kernel/cards";
import { SpecDialog, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { COLUMN, Pane } from "@/kernel/pane";
import { PanelBlank, PanelEmpty, Section, outcomeNotice, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { webAudienceLabel } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";
import type { Agent, NewAgentForm, Subagent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  subagents: Subagent[];
  newAgent: NewAgentForm | null;
  onOpen: (agentId: string) => void;
  onOpenSubagent: (name: string) => void;
  onNewChat: (agentId: string) => void;
  onAgents: () => void;
};

const AGENT_KIND = "agent";
const MODEL_FIELD = "model";
const MODEL = "font-mono text-mono opacity-(--muted-strong)";
const MAIN = "The agent this workspace answers with by default.";
const SPAWNED = "Spawned by an agent for one task. A member does not address it.";
const INHERITS = "Spawned by an agent for one task, on that agent's model.";

/** The values a create form opens on: one for every field whose choices are closed — the deploy's
 *  model ids, and the enums the spec declares. A picker the member never opened would otherwise
 *  submit nothing and earn a refusal for a value the form was already showing. */
function initialSpec(form: NewAgentForm): Record<string, ObjectValue> {
  const properties = form.spec_schema.properties ?? {};
  return Object.fromEntries(
    Object.entries(properties).flatMap(([field, property]) => {
      const choices = field === MODEL_FIELD ? form.models : property.enum;
      return choices?.length ? [[field, choices[0]]] : [];
    }),
  );
}

export function Agents({
  agents,
  subagents,
  newAgent,
  onOpen,
  onOpenSubagent,
  onNewChat,
  onAgents,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [subagentsOpen, setSubagentsOpen] = useState(false);
  const wanted = query.trim().toLowerCase();
  const found = agents.filter((agent) => agent.name.toLowerCase().includes(wanted));

  /** The `agent` kind takes a create only from a workspace admin speaking on the main agent's
   *  lane, so that is the lane the intent rides. A create that landed is read back through the one
   *  answer to what agents exist, which the cards, the sidebar, and the router all draw from. */
  async function create(lane: string, envelope: SpecEnvelope): Promise<NoticeState> {
    const outcome = await postIntent(lane, envelope);
    if (outcome.applied) {
      onAgents();
      setToast({ title: "Created " + envelope.name + "." });
    }
    return outcomeNotice(outcome);
  }

  return (
    <Pane className={cn(COLUMN, "overflow-y-auto scrollbar-gutter-stable p-2xl")}>
      <h1 className="m-0 mb-2xl text-title font-strong">Agents</h1>
      <Section
        title="Workspace agents"
        bar={
          <>
            <Input
              type="search"
              aria-label="Search agents"
              placeholder="Search"
              className="max-w-control-row"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {newAgent && mainAgent ? (
              <Button variant="send" onClick={() => setCreating(true)}>
                New agent
              </Button>
            ) : null}
            <Button onClick={onAgents}>Refresh</Button>
          </>
        }
      >
        {found.length ? (
          <CardGrid
            rows={found}
            rowKey={(agent) => agent.id}
            mark={{ shape: "square" }}
            primary={(agent) => agent.name}
            status={(agent) => <span className={MODEL}>{agent.model}</span>}
            body={(agent) => {
              const reach = agent.web_audience
                ? webAudienceLabel(agent.main, agent.web_audience)
                : null;
              if (!agent.main) return reach;
              return [MAIN, reach].filter(Boolean).join(" · ");
            }}
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
        ) : query ? (
          <PanelEmpty>No agent matches this search.</PanelEmpty>
        ) : (
          <PanelBlank body="No agent is visible to you." />
        )}
      </Section>
      <Section
        title="Subagents"
        bar={
          subagents.length ? (
            <Button
              variant="outline"
              aria-expanded={subagentsOpen}
              onClick={() => setSubagentsOpen((open) => !open)}
            >
              {subagentsOpen ? "Hide subagents" : "Show subagents"}
            </Button>
          ) : null
        }
      >
        {subagents.length ? (
          subagentsOpen ? (
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
          ) : null
        ) : (
          <PanelBlank body="This deploy declares no subagents." />
        )}
      </Section>
      {creating && newAgent && mainAgent ? (
        <SpecDialog
          schema={newAgent.spec_schema}
          kind={AGENT_KIND}
          name={null}
          spec={initialSpec(newAgent)}
          title="New agent"
          options={{ [MODEL_FIELD]: newAgent.models }}
          onDone={(envelope) => create(mainAgent.id, envelope)}
          onClose={() => setCreating(false)}
        />
      ) : null}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </Pane>
  );
}
