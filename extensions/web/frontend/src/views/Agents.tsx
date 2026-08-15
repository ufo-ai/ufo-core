import { useState } from "react";
import { IconRefresh } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Td, TdFact } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SpecPanel, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { useBeside } from "@/kernel/beside";
import { Page, PageHeader, PageToolbar } from "@/kernel/pane";
import { DataTable } from "@/kernel/table";
import { outcomeNotice, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import type { Agent, NewAgentForm, Subagent } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  subagents: Subagent[];
  newAgent: NewAgentForm | null;
  onOpen: (agentId: string) => void;
  onOpenSubagent: (name: string) => void;
  onAgents: () => void;
};

const AGENT_KIND = "agent";
const MODEL_FIELD = "model";
const MAIN = "The agent this workspace answers with by default.";
const SPAWNED = "Spawned by an agent for one task. A member does not address it.";
const INHERITS = "Spawned by an agent for one task, on that agent's model.";
const COLUMNS = ["Name", "Details", { label: "Model", fact: true }];
const MANAGE = "Manage";
/** A subagent with no model of its own runs on the model of the agent that spawned it, so the
 *  column has no id to name and says so rather than standing empty. */
const INHERITED = "—";
const AGENT_FAMILY = "agent";
/** The one answer to which rows are subagents: the value the filter narrows on is the value the
 *  pill is drawn from, so no row can carry the pill and fall outside the Subagents tab. */
const SUBAGENT_FAMILY = "subagent";
const FAMILIES = [
  { label: "Agents", value: AGENT_FAMILY },
  { label: "Subagents", value: SUBAGENT_FAMILY },
];
const SUBAGENT_PILL = "Subagent";
const SUBAGENT_HINT = "An agent starts one to do a single task and report back.";
const PILL = cn(
  "ml-xs inline-flex items-center rounded-full border-0 bg-fill px-sm py-hair",
  "align-middle font-sans text-small text-inherit",
);

/** One row per thing this workspace runs, whichever family it comes from: an agent a member
 *  addresses, and a subagent an agent spawns. A row says which one it is, what it is for, and the
 *  model it runs on — the one fact a member compares straight down this list, and the reason they
 *  open a record when it is not stated here. Who reaches it, what it may touch and the rest of its
 *  spec stand on the record's own page: a column for each would be a table read sideways. */
type AgentRow = {
  key: string;
  family: string;
  name: string;
  details: string;
  model: string;
  open: () => void;
};

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

/** What family a name belongs to, said on the name rather than left to the column beside it, and
 *  the one sentence a member who has never seen a subagent needs. Hover is not the only way to that
 *  sentence: the pill is a button, so focus opens the tooltip from the keyboard and a tap opens it
 *  on a touch screen, where a pointer never rests on anything. The press closes on Escape or on the
 *  next press outside it, and it never reaches the row — `rowControl` hands a press landing on a
 *  nested control to that control, so reading the pill does not open the record. */
function SubagentPill() {
  const [open, setOpen] = useState(false);
  return (
    <TooltipProvider>
      <Tooltip open={open} onOpenChange={setOpen}>
        <TooltipTrigger
          type="button"
          className={PILL}
          onClick={(event) => {
            event.preventDefault();
            setOpen(true);
          }}
        >
          {SUBAGENT_PILL}
        </TooltipTrigger>
        <TooltipContent side="top">{SUBAGENT_HINT}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function Agents({
  agents,
  subagents,
  newAgent,
  onOpen,
  onOpenSubagent,
  onAgents,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("");
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);

  const rows: AgentRow[] = [
    ...agents.map((agent) => ({
      key: "agent/" + agent.id,
      family: AGENT_FAMILY,
      name: agent.name,
      details: agent.main ? MAIN : "",
      model: agent.model,
      open: () => onOpen(agent.id),
    })),
    ...subagents.map((subagent) => ({
      key: "subagent/" + subagent.name,
      family: SUBAGENT_FAMILY,
      name: subagent.name,
      details: subagent.model ? SPAWNED : INHERITS,
      model: subagent.model ?? INHERITED,
      open: () => onOpenSubagent(subagent.name),
    })),
  ];
  const wanted = query.trim().toLowerCase();
  const found = rows.filter(
    (row) => row.name.toLowerCase().includes(wanted) && (!family || row.family === family),
  );

  /** The `agent` kind takes a create only from a workspace admin speaking on the main agent's
   *  lane, so that is the lane the intent rides. A create that landed is read back through the one
   *  answer to what agents exist, which the table, the sidebar, and the router all draw from. */
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
    <Page>
      <PageHeader
        title="Agents"
        search={
          <Search
            label="Search agents"
            placeholder="Search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        }
        action={
          newAgent && mainAgent ? (
            <Button variant="send" size="bar" onClick={() => setCreating(true)}>
              New agent
            </Button>
          ) : null
        }
      />
      <PageToolbar>
        <Filter options={FAMILIES} value={family} onChange={setFamily} />
        <Button size="icon" aria-label="Refresh" className="ml-auto" onClick={onAgents}>
          <IconRefresh className="size-icon" aria-hidden />
        </Button>
      </PageToolbar>
      <DataTable
        columns={COLUMNS}
        rows={found}
        rowKey={(row) => row.key}
        empty="No agent is visible to you."
        note={
          rows.length
            ? query
              ? "No agent matches this search."
              : "This deploy declares no subagents."
            : undefined
        }
        open={(row) => row.open}
        act={() => MANAGE}
      >
        {(row) => (
          <>
            <Td>
              {row.name}
              {row.family === SUBAGENT_FAMILY ? <SubagentPill /> : null}
            </Td>
            <Td>{row.details}</Td>
            <TdFact>{row.model}</TdFact>
          </>
        )}
      </DataTable>
      {beside}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </Page>
  );
}
