import { useState } from "react";
import { IconChevronRight, IconRefresh } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Table, TableNote, Td, Th } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { SpecDialog, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { COLUMN, PageHeader } from "@/kernel/pane";
import { PanelBlank, Section, outcomeNotice, type NoticeState } from "@/kernel/panel";
import { rowControl } from "@/kernel/row";
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
const COLUMNS = ["Name", "Details"];
const FAMILIES = [
  { label: "Agents", value: "agent" },
  { label: "Subagents", value: "subagent" },
];

/** One row per thing this workspace runs, whichever family it comes from: an agent a member
 *  addresses, and a subagent an agent spawns. A row says which one it is and what it is for; the
 *  model it runs on, who reaches it, and everything else stand on the record's own page, one press
 *  away. A column for each would be a table read sideways to answer a question nobody asked. */
type AgentRow = {
  key: string;
  family: string;
  name: string;
  details: string;
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
      family: "agent",
      name: agent.name,
      details: agent.main ? MAIN : "",
      open: () => onOpen(agent.id),
    })),
    ...subagents.map((subagent) => ({
      key: "subagent/" + subagent.name,
      family: "subagent",
      name: subagent.name,
      details: subagent.model ? SPAWNED : INHERITS,
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
    const outcome = await postIntent(lane, envelope);
    if (outcome.applied) {
      onAgents();
      setToast({ title: "Created " + envelope.name + "." });
    }
    return outcomeNotice(outcome);
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-col overflow-y-auto scrollbar-gutter-stable">
      <PageHeader
        aside={
          <>
            <Search
              label="Search agents"
              placeholder="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {newAgent && mainAgent ? (
              <Button variant="send" size="bar" onClick={() => setCreating(true)}>
                New agent
              </Button>
            ) : null}
          </>
        }
      >
        <h1 className="m-0 text-title font-strong">Agents</h1>
      </PageHeader>
      <div
        className={cn(
          COLUMN,
          "flex flex-1 flex-col overflow-y-auto scrollbar-gutter-stable p-2xl pt-2xl",
        )}
      >
        <Section
          bar={
            <>
              <Filter options={FAMILIES} value={family} onChange={setFamily} />
              <Button size="icon" aria-label="Refresh" className="ml-auto" onClick={onAgents}>
                <IconRefresh className="size-icon" aria-hidden />
              </Button>
            </>
          }
        >
          {rows.length ? (
            <Table>
              <thead>
                <tr>
                  {COLUMNS.map((column) => (
                    <Th key={column}>{column}</Th>
                  ))}
                  <Th>{""}</Th>
                </tr>
              </thead>
              <tbody>
                {found.length ? (
                  found.map((row) => {
                    const control = rowControl(row.open, true);
                    return (
                      <tr
                        key={row.key}
                        {...control}
                        className={cn("hover:bg-fill-hover", control.className)}
                      >
                        <Td>{row.name}</Td>
                        <Td className="w-full max-w-0">{row.details}</Td>
                        <Td className="w-(--size-glyph)">
                          <IconChevronRight className="size-icon" aria-hidden />
                        </Td>
                      </tr>
                    );
                  })
                ) : (
                  <TableNote span={COLUMNS.length + 1}>
                    {query ? "No agent matches this search." : "This deploy declares no subagents."}
                  </TableNote>
                )}
              </tbody>
            </Table>
          ) : (
            <PanelBlank body="No agent is visible to you." />
          )}
        </Section>
      </div>
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
    </div>
  );
}
