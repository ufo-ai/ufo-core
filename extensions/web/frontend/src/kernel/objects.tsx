import { useState, type FormEvent, type ReactNode } from "react";
import { IconRefresh } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, type Fact } from "@/components/ui/facts";
import { Field, Input, Search } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Sheet } from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td, TdFact } from "@/components/ui/table";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { ScheduledTaskPane } from "@/kernel/task";
import type { Placement } from "@/kernel/pager";
import { Header, PageToolbar } from "@/kernel/pane";
import { appended, beside, closed, opened } from "@/kernel/slots";
import {
  OutcomeNotice,
  Panel,
  PanelEmpty,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { DataTable, OPEN, type Column } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { ownerLabel, useViewer } from "@/lib/audience";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { ConversationLink } from "@/lib/conversationLink";
import { Moment, isMoment } from "@/lib/moments";
import type { Agent, SpecSchema } from "@/lib/types";

const AGENT_FIELD = "object-agent";
const CONVERSATION_FIELD = "conversation";
const ID_FIELD = "id";
const SUMMARY_FIELD = "summary";

/** A kind that names its rows by an id says which of its list fields titles a record instead, the
 *  most readable field first. Every row carries a `summary`, so a kind may name that among them. */
const TITLED_BY: Record<string, readonly string[]> = {
  conversation: ["title", SUMMARY_FIELD],
  memory: [SUMMARY_FIELD, "text"],
  notification: ["subject"],
  source_trigger: [SUMMARY_FIELD],
};

const TITLE_MAX = 72;

const PAGE_TITLE_FIELD = "created_from_page_title";
const CREATED_FROM_RELATION = "created_from";

/** An id-valued status field beside the readable field that says the same thing. */
const READABLE_BESIDE: Record<string, string> = {
  created_from_page_id: PAGE_TITLE_FIELD,
};

const MEMBER_ID_FIELD = "member_id";
const TRIAGED_TURN_FIELD = "triaged_turn";
const TRIAGED_FIELD = "triaged";
const TRIAGED_HEADING = "Triaged";

/** A uuid, with or without its dashes — how a kind that names its rows by an id spells one. */
const ID_SHAPED = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

const LEADING_FIELDS = 1;

const FACT_LED_FIELDS = 2;

const SCHEDULED_TASK_KIND = "scheduled_task";

const PROMPT_FIELD = "prompt";

const STATE_FIELD = "paused";

const MINE_FIELD = "mine";
const MINE_LABEL = "Created by me";

const RUN_FIELD = "last_run_at";
const ENDING_FIELD = "last_run_status";

const FACT_VALUE_LINE = 72;

export type ObjectValue = string | number | boolean | null;

export type ObjectRow = { name: string; summary: string } & Record<string, ObjectValue>;

type IndexRow = ObjectRow & { agent_id: string; agent_name: string };

export type ObjectLink = { relation: string; kind: string; name: string; opens: boolean };

type Kind = {
  kind: string;
  fields: string[];
  spec_schema: SpecSchema | null;
  applies: boolean;
  deletes: boolean;
};

type IndexPayload = Kind & { objects: IndexRow[]; next_cursor: string | null };

type DetailPayload = Kind & {
  name: string;
  summary: string;
  spec: Record<string, ObjectValue> | null;
  status: Record<string, ObjectValue>;
  links: ObjectLink[];
  created_at: string | null;
  updated_at: string | null;
};

function noun(kind: string): string {
  return kind.replaceAll("_", " ");
}

function firstLine(value: ObjectValue | undefined): string {
  if (typeof value !== "string") return "";
  const said = value.split("\n", 1)[0].trim();
  return said.length > TITLE_MAX ? said.slice(0, TITLE_MAX - 1).trimEnd() + "…" : said;
}

function sentence(said: string): string {
  return said.charAt(0).toUpperCase() + said.slice(1);
}

/** What a record is called: its own name, except where its kind names its rows by an id and states
 *  the readable field to title them by. A titled kind whose row carries none of those fields yet
 *  reads as the kind in words rather than as the id. */
export function titled(kind: string, name: string, row: Record<string, ObjectValue>): string {
  const titles = TITLED_BY[kind];
  if (titles === undefined) return name;
  for (const field of titles) {
    const said = firstLine(row[field]);
    if (said) return said;
  }
  return sentence(noun(kind));
}

function heading(field: string, schema: SpecSchema | null): string {
  const stated = schema?.properties?.[field]?.title;
  if (stated) return stated;
  return noun(field)
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function enumerated(schema: SpecSchema | null, field: string): boolean {
  const property = schema?.properties?.[field];
  if (!property) return false;
  return Boolean(property.enum ?? (property.anyOf ?? []).some((entry) => entry.enum));
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-control border border-edge px-sm py-hair text-small">
      {children}
    </span>
  );
}

const ADDRESS = /^https?:\/\/\S+$/;

function cell(field: string, value: ObjectValue, schema: SpecSchema | null): ReactNode {
  if (value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (isMoment(value)) return <Moment at={value} />;
  if (enumerated(schema, field)) return <Chip>{value}</Chip>;
  if (field === CONVERSATION_FIELD && typeof value === "string")
    return <ConversationLink id={value} />;
  if (typeof value === "string" && ADDRESS.test(value))
    return (
      <a
        href={value}
        target="_blank"
        rel="noopener noreferrer"
        className="text-inherit underline-offset-2 hover:underline focus-visible:underline"
      >
        {value}
      </a>
    );
  return value;
}

function ending(row: ObjectRow): string {
  const said = row[ENDING_FIELD];
  if (row[RUN_FIELD] === null || row[RUN_FIELD] === undefined) return "";
  return typeof said === "string" && said ? " · " + said : "";
}

export const OWNER_FIELD = "owner_email";
const OWNER_HEADING = "Created By";

/** Who made a row, in the member's words — the wire's `owner_email` never renders raw. A row
 *  carrying no creator is the workspace's own. */
export function creator(value: ObjectValue | undefined, viewer: string | null): string {
  return ownerLabel(typeof value === "string" && value ? value : null, viewer);
}

export type ObjectAddress = { agent: string; kind: string; name: string };

const SLOT_PREFIX = "object/";
const SLOT_SEPARATOR = "/";

/** The route id for one object in one app namespace. */
export function slotOf(at: ObjectAddress): string {
  return SLOT_PREFIX + at.agent + SLOT_SEPARATOR + at.kind + SLOT_SEPARATOR + at.name;
}

/** The object named by a route id, or null when the id names another kind of view. */
export function objectAt(id: string): ObjectAddress | null {
  if (!id.startsWith(SLOT_PREFIX)) return null;
  const [agent, kind, ...rest] = id.slice(SLOT_PREFIX.length).split(SLOT_SEPARATOR);
  const name = rest.join(SLOT_SEPARATOR);
  if (!agent || !kind || !name) return null;
  return { agent, kind, name };
}

/** One object kind's index and selected record sheet. */
export function ObjectPane({
  agentId,
  kind,
  title,
  section,
  lead,
  makes = true,
  opens,
  onPlace,
}: {
  agentId: string | null;
  kind: string;
  section?: string;
  makes?: boolean;
  lead?: ReactNode;
  title?: string;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const [generation, setGeneration] = useState(0);
  return (
    <>
      <ObjectIndex
        key={generation}
        agentId={agentId}
        kind={kind}
        title={title}
        section={section}
        lead={lead}
        makes={makes}
        opens={opens}
        onOpen={(at) => onPlace({ opens: opened(opens, slotOf(at), undefined) })}
      />
      {section ? null : (
        <ObjectSheets
          opens={opens}
          onPlace={onPlace}
          onShut={() => setGeneration((count) => count + 1)}
        />
      )}
    </>
  );
}

function ObjectSheets({
  opens,
  onPlace,
  onShut,
}: {
  opens: string[];
  onPlace: (place: Placement) => void;
  onShut?: () => void;
}) {
  const shut = (id: string) => {
    onPlace({ opens: closed(opens, id) });
    onShut?.();
  };
  return (
    <>
      {opens.slice(-1).map((id) => {
        const at = objectAt(id);
        if (at === null)
          return (
            <Sheet open key={id} title={id} onClose={() => shut(id)}>
              <PanelEmpty>That item is not on this page.</PanelEmpty>
            </Sheet>
          );
        return (
          <ObjectDetail
            key={id}
            agentId={at.agent}
            kind={at.kind}
            name={at.name}
            onOpen={(next, aside) =>
              onPlace({
                opens: aside
                  ? appended(opens, slotOf(next))
                  : opened(opens, slotOf(next), id),
              })
            }
            onBack={() => shut(id)}
          />
        );
      })}
    </>
  );
}

function SectionBand({ name, action }: { name: string; action: ReactNode }) {
  return (
    <div
      className={cn(
        "flex h-(--size-control) shrink-0 items-center gap-sm",
        "max-narrow:h-auto max-narrow:flex-col max-narrow:items-stretch",
      )}
    >
      <h2 className="m-0 min-w-0 flex-1 truncate text-body font-medium text-ink-soft">{name}</h2>
      {action ? <div className="flex shrink-0 items-center gap-sm">{action}</div> : null}
    </div>
  );
}

function ObjectIndex({
  agentId,
  kind,
  title,
  section,
  lead,
  makes,
  opens,
  onOpen,
}: {
  agentId: string | null;
  kind: string;
  title?: string;
  section?: string;
  lead?: ReactNode;
  makes: boolean;
  opens: string[];
  onOpen: (at: ObjectAddress) => void;
}) {
  const agents = useAgents();
  const mainAgent = useMainAgent();
  const viewer = useViewer();
  const [reloads, setReloads] = useState(0);
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [orderBy, setOrderBy] = useState("name");
  const [descending, setDescending] = useState(false);
  const [narrowed, setNarrowed] = useState("");
  const [cursor, setCursor] = useState("");
  const [creating, setCreating] = useState<Kind | null>(null);
  const params = new URLSearchParams({ order_by: orderBy });
  if (agentId) params.set("agent", agentId);
  if (query) params.set("q", query);
  if (descending) params.set("order", "desc");
  if (cursor) params.set("cursor", cursor);
  if (narrowed) params.set(narrowed, "true");
  const state = usePanelRead<IndexPayload>("/objects/" + kind + "?" + params.toString(), reloads);
  const owner = agentId ?? mainAgent?.id ?? agents[0]?.id ?? null;

  async function submit(lane: string, envelope: unknown) {
    const outcome = await postIntent(lane, envelope);
    setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  const sheet =
    creating?.spec_schema && owner !== null ? (
      <Sheet open title={"New " + noun(kind)} onClose={() => setCreating(null)}>
      <NewObject
        schema={creating.spec_schema}
        kind={creating.kind}
        agents={agentId === null ? agents : []}
        owner={owner}
        onDone={submit}
        onClose={() => setCreating(null)}
      />
      </Sheet>
    ) : null;

  return (
    <>
      <Panel state={state}>
      {(payload) => {
        const narrowing = Boolean(query || narrowed);
        const acts = makes && payload.applies && payload.spec_schema !== null && owner !== null;
        const owned = agentId !== null && payload.fields.includes(OWNER_FIELD);
        const titles = TITLED_BY[payload.kind] ?? [];
        const prose =
          payload.fields.includes(PROMPT_FIELD) || titles[0] === SUMMARY_FIELD
            ? null
            : SUMMARY_FIELD;
        const shown = payload.fields.filter(
          (field) =>
            !titles.includes(field) &&
            field !== CONVERSATION_FIELD &&
            field !== OWNER_FIELD &&
            field !== PROMPT_FIELD &&
            field !== ID_FIELD &&
            field !== STATE_FIELD &&
            field !== ENDING_FIELD &&
            field !== MINE_FIELD,
        );
        const led = shown.slice(0, prose === null ? FACT_LED_FIELDS : LEADING_FIELDS);
        const columns: Column[] = [{ label: heading("name", payload.spec_schema), sort: "name" }];
        if (agentId === null) columns.push({ label: "App", fact: true });
        if (owned) columns.push({ label: OWNER_HEADING, sort: OWNER_FIELD, fact: true });
        if (prose !== null) {
          columns.push({ label: heading(prose, payload.spec_schema), sort: prose });
        }
        for (const field of led) {
          columns.push({ label: heading(field, payload.spec_schema), sort: field, fact: true });
        }
        const flags = payload.fields.filter(
          (field) =>
            field !== CONVERSATION_FIELD &&
            field !== OWNER_FIELD &&
            field !== STATE_FIELD &&
            (narrowed === field || payload.objects.some((row) => typeof row[field] === "boolean")),
        );
        const searching = (
          <Search
            label={"Search " + noun(payload.kind)}
            placeholder="Search"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onSubmit={() => {
              setCursor("");
              setQuery(typed);
            }}
          />
        );
        const making = acts ? (
          <Button
            variant="send"
            size="bar"
            onClick={() =>
              setCreating({
                kind: payload.kind,
                fields: payload.fields,
                spec_schema: payload.spec_schema,
                applies: payload.applies,
                deletes: payload.deletes,
              })
            }
          >
            {"New " + noun(payload.kind)}
          </Button>
        ) : null;
        return (
          <>
            {section ? (
              <SectionBand name={section} action={making} />
            ) : title ? (
              <Header heading={1} title={title} acts={making} />
            ) : null}
            <PageToolbar>
              {section ? null : searching}
              {lead}
              {flags.length ? (
                <Filter
                  options={flags.map((field) => ({
                    label: field === MINE_FIELD ? MINE_LABEL : heading(field, payload.spec_schema),
                    value: field,
                  }))}
                  value={narrowed}
                  onChange={(field) => {
                    setCursor("");
                    setNarrowed(field);
                  }}
                />
              ) : null}
              <Button
                size="icon"
                aria-label="Refresh"
                className="ml-auto"
                onClick={() => setReloads((count) => count + 1)}
              >
                <IconRefresh aria-hidden />
              </Button>
              {section || title ? null : making}
            </PageToolbar>
            <DataTable
                columns={columns}
                rows={payload.objects}
                rowKey={(row) => row.agent_id + "/" + row.name}
                open={(row) => () =>
                  onOpen({ agent: row.agent_id, kind: payload.kind, name: row.name })}
                current={(row) =>
                  opens.includes(
                    slotOf({ agent: row.agent_id, kind: payload.kind, name: row.name }),
                  )}
                empty={"No " + noun(payload.kind) + " is visible to you."}
                note={
                  narrowed === "mine" && !query
                    ? "You have not created a " + noun(payload.kind) + "."
                    : narrowing
                      ? "No " + noun(payload.kind) + " matches this search."
                      : undefined
                }
                sort={{
                  by: orderBy,
                  descending,
                  onSort: (field) => {
                    setCursor("");
                    setDescending(field === orderBy ? !descending : false);
                    setOrderBy(field);
                  },
                }}
                act={() => OPEN}
              >
                {(row) => (
                  <>
                    <Td>
                      <span className="flex min-w-0 max-w-full items-center gap-xs">
                        <span data-part="primary" className="truncate">
                          {titled(payload.kind, row.name, row)}
                        </span>
                        {typeof row[STATE_FIELD] === "boolean" ? (
                          <Chip>{row[STATE_FIELD] ? "Paused" : "Active"}</Chip>
                        ) : null}
                      </span>
                    </Td>
                    {agentId === null ? <TdFact>{agentName(row.agent_name)}</TdFact> : null}
                    {owned ? <TdFact>{creator(row[OWNER_FIELD], viewer)}</TdFact> : null}
                    {prose === null ? null : (
                      <Td>
                        <span className="block truncate">{row.summary || "—"}</span>
                      </Td>
                    )}
                    {led.map((field) => (
                      <TdFact key={field}>
                        <span className="block truncate">
                          {cell(field, row[field] ?? null, payload.spec_schema)}
                          {field === RUN_FIELD ? ending(row) : null}
                        </span>
                      </TdFact>
                    ))}
                  </>
                )}
              </DataTable>
              {payload.next_cursor || cursor ? (
                <div className="flex gap-xs">
                  {payload.next_cursor ? (
                    <Button variant="row" onClick={() => setCursor(payload.next_cursor ?? "")}>
                      Next page
                    </Button>
                  ) : null}
                  {cursor ? (
                    <Button variant="row" onClick={() => setCursor("")}>
                      First page
                    </Button>
                  ) : null}
                </div>
              ) : null}
          </>
        );
      }}
      </Panel>
      {sheet}
    </>
  );
}

export function NewObject({
  schema,
  kind,
  agents,
  owner,
  nameFrom,
  onDone,
  onClose,
}: {
  schema: SpecSchema;
  kind: string;
  agents: Agent[];
  owner: string;
  nameFrom?: (spec: Record<string, SpecValue>) => string;
  onDone: (lane: string, envelope: unknown) => Promise<NoticeState>;
  onClose: () => void;
}) {
  const [lane, setLane] = useState(owner);
  return (
    <SpecPanel
      schema={schema}
      kind={kind}
      name={null}
      spec={null}
      nameFrom={nameFrom}
      lead={
        agents.length > 1 ? (
          <Field label="App" htmlFor={AGENT_FIELD}>
            <Select value={lane} onValueChange={setLane}>
              <SelectTrigger id={AGENT_FIELD}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agentName(agent.name)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        ) : null
      }
      onDone={(envelope) => onDone(lane, envelope)}
      onClose={onClose}
    />
  );
}

/** One object's record inside a sheet. */
export function ObjectDetail({
  agentId,
  kind,
  name,
  lead,
  actions,
  onOpen,
  onBack,
}: {
  agentId: string;
  kind: string;
  name: string;
  lead?: ReactNode;
  actions?: (
    status: Record<string, ObjectValue> | null,
    apply: (spec: Record<string, SpecValue>) => Promise<NoticeState>,
  ) => ReactNode;
  onOpen: (at: ObjectAddress, aside: boolean) => void;
  onBack: () => void;
}) {
  const viewer = useViewer();
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<DetailPayload>(
    "/objects/" + kind + "/" + encodeURIComponent(name) + "?agent=" + agentId,
    reloads,
  );
  const [editing, setEditing] = useState<DetailPayload | null>(null);

  async function submit(envelope: unknown) {
    const outcome = await postIntent(agentId, envelope);
    setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  async function apply(spec: Record<string, SpecValue>) {
    const next = await submit({ verb: "apply", kind, name, spec });
    setNotice(next);
    return next;
  }

  async function remove() {
    const outcome = await postIntent(agentId, { verb: "delete", kind, name });
    if (outcome.applied) {
      onBack();
      return;
    }
    setNotice(outcomeNotice(outcome));
  }

  const payload = state.phase === "ready" ? state.payload : null;
  return (
    <Sheet
      open
      title={titled(
        kind,
        name,
        payload === null ? {} : { ...payload.status, summary: payload.summary },
      )}
      onClose={onBack}
      actions={editing === null ? actions?.(payload?.status ?? null, apply) : undefined}
    >
      {lead}
      {editing?.spec_schema && editing.spec ? (
        <SpecPanel
          schema={editing.spec_schema}
          kind={editing.kind}
          name={editing.name}
          spec={editing.spec}
          onDone={submit}
          onClose={() => setEditing(null)}
        />
      ) : (
        <>
          <div className="text-ink-soft">{noun(kind)}</div>
          <Panel state={state} shape="form">
            {(record) => (
              <>
                <OutcomeNotice state={notice} />
                {record.kind === SCHEDULED_TASK_KIND ? (
                  <ScheduledTaskPane
                    agentId={agentId}
                    spec={record.spec}
                    status={record.status}
                    summary={record.summary}
                    links={record.links}
                    onApply={apply}
                    onOpen={onOpen}
                  />
                ) : (
                  <SpecAndStatus
                    agentId={agentId}
                    record={record}
                    viewer={viewer}
                    onOpen={onOpen}
                  />
                )}
                <div className="mb-2xl text-small text-ink-soft">
                  {record.created_at ? (
                    <span>
                      Created <Moment at={record.created_at} />
                    </span>
                  ) : null}
                  {record.created_at && record.updated_at ? " · " : null}
                  {record.updated_at ? (
                    <span>
                      Updated <Moment at={record.updated_at} />
                    </span>
                  ) : null}
                </div>
                {record.deletes &&
                (record.kind !== SCHEDULED_TASK_KIND || record.status.deletable === true) ? (
                  <div className="flex flex-wrap gap-sm">
                    {record.kind !== SCHEDULED_TASK_KIND &&
                    record.applies &&
                    record.spec_schema &&
                    record.spec ? (
                      <Button variant="send" onClick={() => setEditing(record)}>
                        Edit
                      </Button>
                    ) : null}
                    <ConfirmButton verb="Delete" variant="row" onClick={remove} />
                  </div>
                ) : null}
              </>
            )}
          </Panel>
        </>
      )}
    </Sheet>
  );
}

function told(field: string, status: Record<string, ObjectValue>): boolean {
  if (field === ID_FIELD || field === MEMBER_ID_FIELD) return false;
  if (field === TRIAGED_TURN_FIELD) return status[TRIAGED_FIELD] === undefined;
  const readable = READABLE_BESIDE[field];
  return readable === undefined || !status[readable];
}

function linkSaid(link: ObjectLink, status: Record<string, ObjectValue>): string {
  const said = link.relation + " " + noun(link.kind);
  if (!ID_SHAPED.test(link.name)) return said + " " + link.name;
  const stated = link.relation === CREATED_FROM_RELATION ? firstLine(status[PAGE_TITLE_FIELD]) : "";
  return stated ? said + " \u201c" + stated + "\u201d" : said;
}

function SpecAndStatus({
  agentId,
  record,
  viewer,
  onOpen,
}: {
  agentId: string;
  record: DetailPayload;
  viewer: string | null;
  onOpen: (at: ObjectAddress, aside: boolean) => void;
}) {
  return (
    <>
      <Section title="Spec">
        {record.spec ? (
          <Facts
            rows={Object.entries(record.spec).flatMap(([field, value]) => {
              const fact = specFact(field, value, record.spec_schema);
              return fact ? [fact] : [];
            })}
          />
        ) : (
          <p className="m-0">{record.summary}</p>
        )}
      </Section>
      <Section title="Status">
        <Facts
          rows={record.fields
            .filter((field) => told(field, record.status))
            .map((field) =>
              field === OWNER_FIELD
                ? { label: OWNER_HEADING, value: creator(record.status[field], viewer) }
                : field === TRIAGED_TURN_FIELD
                  ? {
                      label: TRIAGED_HEADING,
                      value: record.status[field] === null ? "No" : "Yes",
                    }
                  : {
                      label: heading(field, record.spec_schema),
                      value: cell(field, record.status[field] ?? null, record.spec_schema),
                    },
            )}
        />
      </Section>
      <Section title="Links">
        {record.links.length ? (
          <ul className="m-0 list-none p-0">
            {record.links.map((link) => {
              const at = { agent: agentId, kind: link.kind, name: link.name };
              const said = linkSaid(link, record.status);
              return (
                <li key={link.relation + link.kind + link.name} className="py-2xs">
                  {link.opens && link.kind === CONVERSATION_FIELD ? (
                    <span data-part="link">
                      {link.relation + " "}
                      <ConversationLink id={link.name} />
                    </span>
                  ) : link.opens ? (
                    <button
                      type="button"
                      data-part="link"
                      onClick={(event) => onOpen(at, beside(event))}
                      onAuxClick={(event) => {
                        if (!beside(event)) return;
                        onOpen(at, true);
                      }}
                      className="border-0 bg-transparent p-0 text-left font-strong text-inherit"
                    >
                      {said}
                    </button>
                  ) : (
                    <span data-part="link">{said}</span>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="m-0 text-ink-soft">Nothing links out of this one.</p>
        )}
      </Section>
    </>
  );
}

function specFact(field: string, value: ObjectValue, schema: SpecSchema | null): Fact | null {
  if (value === null) return null;
  if (typeof value === "string" && isMoment(value))
    return { label: heading(field, schema), value: <Moment at={value} /> };
  const rendered = typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
  if (enumerated(schema, field)) {
    return { label: heading(field, schema), value: <Chip>{rendered}</Chip> };
  }
  return {
    label: heading(field, schema),
    value: rendered,
    block: rendered.includes("\n") || rendered.length > FACT_VALUE_LINE,
  };
}

export type SpecEnvelope = {
  verb: "apply";
  kind: string;
  name: string;
  spec: Record<string, SpecValue>;
};

/** `nameFrom` is the kind whose name is not the member's to type: the panel derives it from the
 *  spec they filled in and draws no Name field. */
export function SpecPanel({
  schema,
  kind,
  name,
  spec,
  lead,
  options,
  nameFrom,
  onDone,
  onClose,
}: {
  schema: SpecSchema;
  kind: string;
  name: string | null;
  spec: Record<string, ObjectValue> | null;
  lead?: ReactNode;
  options?: Record<string, string[] | null>;
  nameFrom?: (spec: Record<string, SpecValue>) => string;
  onDone: (envelope: SpecEnvelope) => Promise<NoticeState>;
  onClose: () => void;
}) {
  const properties = schema.properties ?? {};
  const fields = Object.keys(properties);
  const [objectName, setObjectName] = useState(name ?? "");
  const [values, setValues] = useState<Record<string, SpecValue>>(() =>
    Object.fromEntries(
      fields.map((field) => [field, initialSpecValue(properties[field], spec?.[field])]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const submitted: Record<string, SpecValue> = {};
    for (const field of fields) {
      const held = values[field];
      if (typeof held === "boolean") {
        submitted[field] = held;
        continue;
      }
      if (String(held ?? "").trim()) submitted[field] = String(held).trim();
    }
    const asked = nameFrom === undefined ? objectName.trim() : nameFrom(submitted);
    const outcome = await onDone({ verb: "apply", kind, name: asked, spec: submitted });
    setBusy(false);
    if (outcome.refused) {
      setNotice(outcome);
      return;
    }
    onClose();
  }

  return (
    <>
      <OutcomeNotice state={notice} />
      <form onSubmit={send} className="flex flex-col gap-xl">
        {lead}
        {nameFrom === undefined ? (
          <Field label="Name" htmlFor="object-name">
            <Input
              id="object-name"
              value={objectName}
              required={name === null}
              disabled={name !== null}
              onChange={(event) => setObjectName(event.target.value)}
            />
          </Field>
        ) : null}
        <FormFromSchema
          schema={schema}
          fields={fields}
          values={values}
          options={options}
          onChange={(field, value) => setValues((held) => ({ ...held, [field]: value }))}
        />
        <div className="flex justify-end">
          <Button type="submit" variant="send" size="bar" busy={busy}>
            {name === null ? "Create" : "Save"}
          </Button>
        </div>
      </form>
    </>
  );
}
