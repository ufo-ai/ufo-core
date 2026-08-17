import { useState } from "react";
import { IconRefresh } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Segmented } from "@/components/ui/filter";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { SpecPanel, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { useBeside } from "@/kernel/beside";
import { outcomeNotice, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { AgentGraph } from "@/views/AgentGraph";
import { AgentPane } from "@/views/AgentPane";
import { AGENT_TABS, type AgentTab, type PlaceStep, type WorkspacePlace } from "@/lib/route";
import type { Agent, NewAgentForm, Subagent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  subagents: Subagent[];
  newAgent: NewAgentForm | null;
  /** The agent the hash names, or null on the bare route — which shows the main agent without
   *  navigating. */
  selected: Agent | null;
  tab: AgentTab;
  place: WorkspacePlace;
  onOpen: (agentId: string) => void;
  onTab: (tab: AgentTab) => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onAgents: () => void;
};

const AGENT_KIND = "agent";
const MODEL_FIELD = "model";
const MAIN = "Main";
/** The held answer to how this member reads the wide pane: the selected agent, or the topology the
 *  index rows sit in. The graph is a read of more than the boot payload, so it earns its reads only
 *  when picked; opening an agent is a choice of record over topology, so it puts the list back. */
const VIEW_KEY = "agents-view";
const GRAPH = "graph";
const LIST = "list";
const VIEWS = [
  { label: "List", value: LIST },
  { label: "Graph", value: GRAPH },
];

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

/** The agents screen: a thin index — search, the List | Graph view, one row per agent, the New
 *  agent act — beside a wide pane holding the selected agent or the topology. On a narrow screen
 *  the index is the page and a hash-named agent overlays it. */
export function Agents({
  agents,
  subagents,
  newAgent,
  selected,
  tab,
  place,
  onOpen,
  onTab,
  onPlace,
  onAgents,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const shown = selected ?? mainAgent;
  const [query, setQuery] = useState("");
  const [view, setView] = useState(() => (localStorage.getItem(VIEW_KEY) === GRAPH ? GRAPH : LIST));
  const [reloads, setReloads] = useState(0);
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);

  function pickView(next: string) {
    setView(next);
    localStorage.setItem(VIEW_KEY, next);
  }

  function open(agentId: string) {
    if (view !== LIST) pickView(LIST);
    onOpen(agentId);
  }

  const wanted = query.trim().toLowerCase();
  const found = agents.filter((agent) => agent.name.toLowerCase().includes(wanted));

  /** The `agent` kind takes a create only from a workspace admin speaking on the main agent's
   *  lane, so that is the lane the intent rides. A create that landed is read back through the one
   *  answer to what agents exist, which the index, the sidebar, and the router all draw from. */
  async function create(lane: string, envelope: SpecEnvelope): Promise<NoticeState> {
    const outcome = await postIntent(lane, { ...envelope, create_only: true });
    if (outcome.applied) {
      onAgents();
      setToast({ title: "Created " + envelope.name + "." });
    }
    return outcomeNotice(outcome);
  }

  const beside = useBeside(
    creating && newAgent && mainAgent ? (
      <SpecPanel
        schema={newAgent.spec_schema}
        kind={AGENT_KIND}
        name={null}
        spec={initialSpec(newAgent)}
        title="New agent"
        options={{ [MODEL_FIELD]: newAgent.models }}
        onDone={(envelope) => create(mainAgent.id, envelope)}
        onClose={() => setCreating(false)}
      />
    ) : null,
    () => setCreating(false),
  );

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-[var(--container-index)_1fr] max-narrow:grid-cols-1">
      <nav
        aria-label="Agents"
        className={cn(
          "flex min-h-0 flex-col gap-lg overflow-y-auto",
          "border-r border-edge px-2xl py-(--size-page-top) max-narrow:border-r-0",
        )}
      >
        <Search
          label="Search agents"
          placeholder="Search"
          className="w-full shrink-0"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="flex shrink-0 items-center gap-sm">
          <div className="flex min-w-0 max-narrow:hidden">
            <Segmented label="View" segments={VIEWS} value={view} onPick={pickView} />
          </div>
          <Button
            size="icon"
            aria-label="Refresh"
            className="ml-auto"
            onClick={() => {
              onAgents();
              setReloads((count) => count + 1);
            }}
          >
            <IconRefresh className="size-icon" aria-hidden />
          </Button>
        </div>
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {found.map((agent) => (
            <li key={agent.id}>
              <button
                type="button"
                aria-current={agent.id === shown?.id}
                onClick={() => open(agent.id)}
                className={cn(
                  "flex w-full flex-col gap-2xs rounded-control border-0 bg-transparent",
                  "px-sm py-xs text-left text-inherit hover:bg-fill",
                  agent.id === shown?.id && "bg-fill",
                )}
              >
                <span className="flex w-full items-baseline gap-sm">
                  <span className="min-w-0 truncate text-label">{agent.name}</span>
                  {agent.main ? <span className="text-small text-ink-soft">{MAIN}</span> : null}
                </span>
                <span className="w-full truncate font-mono text-small text-ink-soft">
                  {agent.model}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {agents.length && !found.length ? (
          <p className="m-0 text-label text-ink-soft">No agent matches this search.</p>
        ) : null}
        {newAgent && mainAgent ? (
          <Button variant="send" size="bar" className="shrink-0" onClick={() => setCreating(true)}>
            New agent
          </Button>
        ) : null}
      </nav>
      {view === GRAPH ? (
        <div
          className={cn(
            "min-h-0 min-w-0 overflow-y-auto",
            "px-(--size-page-gutter) py-(--size-page-top) max-narrow:hidden",
          )}
        >
          <AgentGraph
            agents={agents}
            subagents={subagents}
            query={query}
            reloads={reloads}
            onOpen={open}
          />
        </div>
      ) : shown ? (
        <AgentPane
          agent={shown}
          tab={tab}
          tabs={AGENT_TABS}
          selected={selected !== null}
          onTab={onTab}
          place={place}
          onPlace={onPlace}
        />
      ) : (
        <div className="m-auto max-w-empty text-center text-ink-soft max-narrow:hidden">
          No agent is visible to you.
        </div>
      )}
      {beside}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </div>
  );
}
