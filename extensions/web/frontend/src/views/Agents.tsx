import { useState } from "react";
import {
  IconBrandAirtable,
  IconBrandAsana,
  IconBrandFacebook,
  IconBrandGithub,
  IconBrandGmail,
  IconBrandGoogle,
  IconBrandGoogleDrive,
  IconBrandInstagram,
  IconBrandIntercom,
  IconBrandJira,
  IconBrandMonday,
  IconBrandNotion,
  IconBrandSentry,
  IconBrandSlack,
  IconBrandStripe,
  IconBrandTeams,
  IconPlug,
  IconRefresh,
  type Icon,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Filter, Segmented } from "@/components/ui/filter";
import { Td, TdFact } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SpecPanel, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { useBeside } from "@/kernel/beside";
import { Page, PageHeader, PageToolbar } from "@/kernel/pane";
import { DataTable } from "@/kernel/table";
import { outcomeNotice, usePanelRead, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { AgentGraph } from "@/views/AgentGraph";
import type { PoolPayload } from "@/views/Connectors";
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
const COLUMNS = ["Name", { label: "Connectors", fact: true }, { label: "Model", fact: true }];
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
const MAIN_PILL = "Main";
const MAIN_HINT = "The agent this workspace answers with by default.";
/** What the column draws while the connections read is in flight, and what it keeps if that read
 *  is refused: the marks are a second answer beside the rows, so the table states the agents it
 *  was opened for either way rather than waiting on them. */
const NO_CONNECTIONS: PoolPayload = { connections: [] };
/** The mark a provider is drawn as. The connector catalog is open — a provider this deploy connects
 *  tomorrow stands in no map written today — so an unmapped one takes the plug and is still named
 *  on the mark. */
const PROVIDER_MARKS: Record<string, Icon> = {
  airtable: IconBrandAirtable,
  asana: IconBrandAsana,
  facebook_ads: IconBrandFacebook,
  github: IconBrandGithub,
  gmail: IconBrandGmail,
  googleads: IconBrandGoogle,
  googlecalendar: IconBrandGoogle,
  googledocs: IconBrandGoogle,
  googledrive: IconBrandGoogleDrive,
  googlemeet: IconBrandGoogle,
  googlesheets: IconBrandGoogle,
  instagram: IconBrandInstagram,
  intercom: IconBrandIntercom,
  jira: IconBrandJira,
  microsoft_teams: IconBrandTeams,
  monday: IconBrandMonday,
  notion: IconBrandNotion,
  sentry: IconBrandSentry,
  slack: IconBrandSlack,
  stripe: IconBrandStripe,
};
/** The column is one fact wide, so the marks past this many are counted rather than drawn over the
 *  cell's own edge. */
const MARK_LIMIT = 4;
/** What a row with no connector states, so an empty cell is read as an answer rather than as a
 *  column that failed to draw. */
const NO_MARKS = "—";
/** The held answer to how this member reads the page: rows to compare, or the topology the rows
 *  sit in. The graph is a read of more than this payload, so it earns its reads only when picked. */
const VIEW_KEY = "agents-view";
const GRAPH = "graph";
const LIST = "list";
const VIEWS = [
  { label: "List", value: LIST },
  { label: "Graph", value: GRAPH },
];
const PILL = cn(
  "ml-xs inline-flex items-center rounded-full border-0 bg-fill px-sm py-hair",
  "align-middle font-sans text-small text-inherit",
);

/** One row per thing this workspace runs, whichever family it comes from: an agent a member
 *  addresses, and a subagent an agent spawns. A row says which one it is, the providers it holds a
 *  connected account on, and the model it runs on — facts of this one agent, which a member reads
 *  straight down the list. What an agent is for is a paragraph, and it stands on the record's own
 *  page: a column of clipped paragraphs repeats one sentence down every row and can be finished on
 *  none of them. */
type AgentRow = {
  key: string;
  family: string;
  name: string;
  main: boolean;
  providers: string[];
  model: string;
  open: () => void;
};

/** Which providers each agent holds a connected account on, from the same pool read the connectors
 *  screen draws — one entry per provider however many accounts are attached, ordered so the marks
 *  land the same way in every row. The read is this member's, so a row states the connections that
 *  member may see and never another member's private ones. */
function attachedProviders(payload: PoolPayload): Map<string, string[]> {
  const held = new Map<string, Set<string>>();
  for (const entry of payload.connections)
    for (const agent of entry.agents)
      held.set(agent.id, (held.get(agent.id) ?? new Set()).add(entry.provider));
  return new Map([...held].map(([id, providers]) => [id, [...providers].sort()]));
}

/** The connectors an agent reaches, drawn as the marks a member already knows those services by.
 *  Each mark names its provider, so the column is read by a screen reader and under the pointer as
 *  well as by the logo. */
function Providers({ providers }: { providers: string[] }) {
  if (providers.length === 0) return NO_MARKS;
  const drawn = providers.slice(0, MARK_LIMIT);
  const counted = providers.length - drawn.length;
  return (
    <span className="flex items-center gap-xs">
      {drawn.map((provider) => {
        const Mark = PROVIDER_MARKS[provider] ?? IconPlug;
        return (
          <Mark key={provider} role="img" aria-label={provider} className="size-icon shrink-0" />
        );
      })}
      {counted ? <span>{"+" + counted}</span> : null}
    </span>
  );
}

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

/** What a name is, said on the name rather than in a column of prose beside it, and the one
 *  sentence a member who has never seen this kind of row needs. Hover is not the only way to that
 *  sentence: the pill is a button, so focus opens the tooltip from the keyboard and a tap opens it
 *  on a touch screen, where a pointer never rests on anything. The press closes on Escape or on the
 *  next press outside it, and it never reaches the row — `rowControl` hands a press landing on a
 *  nested control to that control, so reading the pill does not open the record. */
function HintPill({ label, hint }: { label: string; hint: string }) {
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
          {label}
        </TooltipTrigger>
        <TooltipContent side="top">{hint}</TooltipContent>
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
  const [view, setView] = useState(() => (localStorage.getItem(VIEW_KEY) === GRAPH ? GRAPH : LIST));
  const [reloads, setReloads] = useState(0);
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);

  function pickView(next: string) {
    setView(next);
    localStorage.setItem(VIEW_KEY, next);
  }

  const pool = usePanelRead<PoolPayload>(view === LIST ? "/connections" : null, reloads);
  const attached = attachedProviders(pool.phase === "ready" ? pool.payload : NO_CONNECTIONS);
  const rows: AgentRow[] = [
    ...agents.map((agent) => ({
      key: "agent/" + agent.id,
      family: AGENT_FAMILY,
      name: agent.name,
      main: agent.main,
      providers: attached.get(agent.id) ?? [],
      model: agent.model,
      open: () => onOpen(agent.id),
    })),
    ...subagents.map((subagent) => ({
      key: "subagent/" + subagent.name,
      family: SUBAGENT_FAMILY,
      name: subagent.name,
      main: false,
      /** A connector is granted to an agent, so a subagent holds none of its own: a child reaches
       *  what the agent that spawned it reaches. */
      providers: [],
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
        <Segmented label="View" segments={VIEWS} value={view} onPick={pickView} />
        {view === LIST ? <Filter options={FAMILIES} value={family} onChange={setFamily} /> : null}
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
      </PageToolbar>
      {view === GRAPH ? (
        <AgentGraph
          agents={agents}
          subagents={subagents}
          query={query}
          reloads={reloads}
          onOpen={onOpen}
          onOpenSubagent={onOpenSubagent}
        />
      ) : (
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
                {row.family === SUBAGENT_FAMILY ? (
                  <HintPill label={SUBAGENT_PILL} hint={SUBAGENT_HINT} />
                ) : null}
                {row.main ? <HintPill label={MAIN_PILL} hint={MAIN_HINT} /> : null}
              </Td>
              <TdFact>
                <Providers providers={row.providers} />
              </TdFact>
              <TdFact>{row.model}</TdFact>
            </>
          )}
        </DataTable>
      )}
      {beside}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </Page>
  );
}
