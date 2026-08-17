import { useState, type FormEvent, type ReactNode } from "react";
import { IconRefresh } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, type Fact } from "@/components/ui/facts";
import { Field, Input, Search } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td, TdFact } from "@/components/ui/table";
import { FormFromSchema, initialSpecValue, type SpecSchema, type SpecValue } from "@/kernel/form";
import { useBeside } from "@/kernel/beside";
import { PageHeader, PageToolbar, RecordPanel } from "@/kernel/pane";
import {
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { DataTable, OPEN, type Column } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import { ownerLabel, useViewer } from "@/lib/audience";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { day, isMoment, relativeMoment } from "@/lib/moments";
import type { Agent } from "@/lib/types";

const AGENT_FIELD = "object-agent";
/** The conversation a record belongs to is where the member goes, never a column they read: the
 *  value is a uuid, which is the wire's word for a thread and nobody's answer to "which one". The
 *  name carries the press, and the row's own `View` still opens the record itself. */
const CONVERSATION_FIELD = "conversation";
/** A row's durable id is the wire's join key — the radar feed resolves a run's task by it — and
 *  the name is already the member's word for the record, so it is never a column either. */
const ID_FIELD = "id";

/** How many of the kind's own declared fields the index carries beside the name and the prose. The
 *  kind states its fields in the order it leads with, so the first is the one a member came to read
 *  — a scheduled task's last run — and the rest stand on the record's own page. A column for every
 *  declared field is a table read sideways to answer a question nobody asked. */
const LEADING_FIELDS = 1;

/** How many a kind carrying no prose leads with instead. The width the prose column would have
 *  taken goes to one more of the kind's own facts, rather than to a column of blank. */
const FACT_LED_FIELDS = 2;

/** The one field no index reads down. A scheduled task's prompt is a whole instruction: every row
 *  cut it mid-word, and a cut arrives at the reader indistinguishable from a prompt that ended. So
 *  a kind declaring it carries no prose column at all and reads by its facts, and its summary is no
 *  way around that — the projection composes the summary out of the same prompt. Every other kind
 *  reads down the one-line summary. */
const PROMPT_FIELD = "prompt";

/** Whether a row is stopped. It is the first thing asked of a task and the last thing a column
 *  should cost: it reads as a chip beside the name, where the member is already looking, and the
 *  filter above the table still narrows by it. */
const STATE_FIELD = "paused";

/** The run a member reads together with how it went, and the field that says how. An ending in a
 *  column of its own — `done`, beside no run — names nothing, so it reads inside the cell of the
 *  run it belongs to and takes no column. */
const RUN_FIELD = "last_run_at";
const ENDING_FIELD = "last_run_status";

/** How many characters of a spec value still read beside their label. A schedule, a day, a name fit
 *  the value column; a prompt does not, and neither does a url nobody can break a line in, so past
 *  this they take their own wrapped block under the label instead of being cut. */
const FACT_VALUE_LINE = 72;

export type ObjectValue = string | number | boolean | null;

export type ObjectRow = { name: string; summary: string } & Record<string, ObjectValue>;

/** One row of an index. The projection names the agent that owns the row whether the read ran in
 *  one namespace or across the audience, so an act reaches the right intent lane either way. */
type IndexRow = ObjectRow & { agent_id: string; agent_name: string };

export type ObjectLink = { relation: string; kind: string; name: string; opens: boolean };

/** What every object page knows about the kind it renders, ahead of any one row: the fields it
 *  declared for columns and filters, the schema its form draws, and which acts the intent lane
 *  takes for it — apply and delete answered apart, so a kind the lane only ever deletes offers no
 *  control that would be refused. */
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

/** The member's word for a kind. The wire names one `scheduled_task`; every line a member reads
 *  names it `scheduled task`, and no screen states an identifier the member never typed. */
function noun(kind: string): string {
  return kind.replaceAll("_", " ");
}

/** A column head, and the label on the tab that narrows by that column. The schema titles the
 *  fields it declares; a field the schema never mentions (`next_run_at`) is the wire's own word,
 *  and reads as `Next Run At` — the Title Case every other head on every other screen takes. */
function heading(field: string, schema: SpecSchema | null): string {
  const titled = schema?.properties?.[field]?.title;
  if (titled) return titled;
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

/** An enum member is one of a closed set the member picks from, so it is drawn as the thing picked
 *  rather than set in the mono a wire identifier takes. */
function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-control border border-edge px-sm py-hair text-small">
      {children}
    </span>
  );
}

/** One value of an index cell or a status row. The column or the label already carries the field's
 *  name, so the value stands alone — and a value the record does not hold takes an em dash rather
 *  than an empty cell, which reads as a table that failed to draw. A moment still ahead is one the
 *  member is waiting on and reads as the wait; a moment already past is the calendar day it fell
 *  on, which is how every other date in the portal reads. Only the comparison goes through `Date`;
 *  the day is still read off the ISO string, so no reader's zone moves it. */
function cell(field: string, value: ObjectValue, schema: SpecSchema | null, now: Date): ReactNode {
  if (value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (isMoment(value))
    return new Date(value).getTime() > now.getTime()
      ? relativeMoment(value, now)
      : (day(value) ?? value);
  if (enumerated(schema, field)) return <Chip>{value}</Chip>;
  return value;
}

/** How the run in a cell went, after the moment it ran at — the same `Last · status` line the
 *  conversation's own automations panel reads. A record with no run behind it states no ending, and
 *  a run whose turn the workspace no longer holds states the moment alone. */
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

/** Where an object page stands: one kind, and one of its objects once a row or a link is opened. */
export type ObjectAddress = { kind: string; name: string | null };

/** An address inside one agent's namespace. A row opened out of a cross-agent index belongs to the
 *  agent that owns it, and so does every link the detail follows out of it. */
type At = ObjectAddress & { agentId: string };

/** One kind's pages: its index, and one row's detail once a row or a link is opened. `agentId`
 *  names the one agent namespace to read, or null to read across the viewer's whole audience —
 *  the same choice the index route itself offers. */
export function ObjectPane({
  agentId,
  kind,
  title,
  lead,
}: {
  agentId: string | null;
  kind: string;
  /** What a page holding more than one kind says about which one is showing. It leads the toolbar,
   *  where what family to show already stands. */
  lead?: ReactNode;
  /** The page's own name, where this pane is the page. A tab inside another page passes none —
   *  the pane it stands in is already headed. */
  title?: string;
}) {
  const [at, setAt] = useState<At | null>(null);
  if (at !== null && at.name !== null) {
    return (
      <ObjectDetail
        key={at.agentId + "/" + at.kind + "/" + at.name}
        agentId={at.agentId}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => setAt({ ...next, agentId: at.agentId })}
        onBack={() => setAt(null)}
      />
    );
  }
  return <ObjectIndex agentId={agentId} kind={kind} title={title} lead={lead} onOpen={setAt} />;
}

function ObjectIndex({
  agentId,
  kind,
  title,
  lead,
  onOpen,
}: {
  agentId: string | null;
  kind: string;
  title?: string;
  lead?: ReactNode;
  onOpen: (at: At) => void;
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
  const now = new Date();
  const owner = agentId ?? mainAgent?.id ?? agents[0]?.id ?? null;

  async function submit(lane: string, envelope: unknown) {
    const outcome = await postIntent(lane, envelope);
    setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  const beside = useBeside(
    creating?.spec_schema && owner !== null ? (
      <NewObject
        schema={creating.spec_schema}
        kind={creating.kind}
        agents={agentId === null ? agents : []}
        owner={owner}
        onDone={submit}
        onClose={() => setCreating(null)}
      />
    ) : null,
    () => setCreating(null),
  );

  return (
    <>
      <Panel state={state}>
      {(payload) => {
        const narrowing = Boolean(query || narrowed);
        const acts = payload.applies && payload.spec_schema !== null && owner !== null;
        const owned = agentId !== null && payload.fields.includes(OWNER_FIELD);
        const prose = payload.fields.includes(PROMPT_FIELD) ? null : "summary";
        const shown = payload.fields.filter(
          (field) =>
            field !== CONVERSATION_FIELD &&
            field !== OWNER_FIELD &&
            field !== PROMPT_FIELD &&
            field !== ID_FIELD &&
            field !== STATE_FIELD &&
            field !== ENDING_FIELD &&
            field !== "mine",
        );
        const led = shown.slice(0, prose === null ? FACT_LED_FIELDS : LEADING_FIELDS);
        const columns: Column[] = [{ label: heading("name", payload.spec_schema), sort: "name" }];
        if (agentId === null) columns.push({ label: "Agent", fact: true });
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
            (narrowed === field || payload.objects.some((row) => typeof row[field] === "boolean")),
        );
        return (
          <>
            <PageHeader
              title={title}
              search={
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
              }
              action={
                acts ? (
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
                ) : null
              }
            />
            <PageToolbar>
              {lead}
              {flags.length ? (
                <Filter
                  options={flags.map((field) => ({
                    label: heading(field, payload.spec_schema),
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
                <IconRefresh className="size-icon" aria-hidden />
              </Button>
            </PageToolbar>
            <DataTable
                columns={columns}
                rows={payload.objects}
                rowKey={(row) => row.agent_id + "/" + row.name}
                open={(row) => () =>
                  onOpen({ agentId: row.agent_id, kind: payload.kind, name: row.name })
                }
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
                          {row.name}
                        </span>
                        {typeof row[STATE_FIELD] === "boolean" ? (
                          <Chip>{row[STATE_FIELD] ? "Paused" : "Active"}</Chip>
                        ) : null}
                      </span>
                    </Td>
                    {agentId === null ? <TdFact>{row.agent_name}</TdFact> : null}
                    {owned ? <TdFact>{creator(row[OWNER_FIELD], viewer)}</TdFact> : null}
                    {prose === null ? null : (
                      <Td>
                        <span className="block truncate">{row.summary || "—"}</span>
                      </Td>
                    )}
                    {led.map((field) => (
                      <TdFact key={field}>
                        <span className="block truncate">
                          {cell(field, row[field] ?? null, payload.spec_schema, now)}
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
      {beside}
    </>
  );
}

/** The create act of an index read across the audience has one question a single namespace never
 *  raises: which agent runs the new row. The picker is drawn only where there is a choice to
 *  make — a select with one option states one the member does not have. */
function NewObject({
  schema,
  kind,
  agents,
  owner,
  onDone,
  onClose,
}: {
  schema: SpecSchema;
  kind: string;
  agents: Agent[];
  owner: string;
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
      title={"New " + noun(kind)}
      lead={
        agents.length > 1 ? (
          <Field label="Agent" htmlFor={AGENT_FIELD}>
            <Select value={lane} onValueChange={setLane}>
              <SelectTrigger id={AGENT_FIELD}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.name}
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

export function ObjectDetail({
  agentId,
  kind,
  name,
  onOpen,
  onBack,
}: {
  agentId: string;
  kind: string;
  name: string;
  onOpen: (at: ObjectAddress) => void;
  onBack: () => void;
}) {
  const viewer = useViewer();
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<DetailPayload>(
    "/objects/" + kind + "/" + encodeURIComponent(name) + "?agent=" + agentId,
    reloads,
  );
  const now = new Date();
  const [editing, setEditing] = useState<DetailPayload | null>(null);

  async function submit(envelope: unknown) {
    const outcome = await postIntent(agentId, envelope);
    setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  async function remove() {
    const outcome = await postIntent(agentId, { verb: "delete", kind, name });
    if (outcome.applied) {
      onBack();
      return;
    }
    setNotice(outcomeNotice(outcome));
  }

  const beside = useBeside(
    editing?.spec_schema && editing.spec ? (
      <SpecPanel
        schema={editing.spec_schema}
        kind={editing.kind}
        name={editing.name}
        spec={editing.spec}
        title={"Edit " + editing.name}
        onDone={submit}
        onClose={() => setEditing(null)}
      />
    ) : null,
    () => setEditing(null),
  );

  return (
    <>
      <div className="mb-lg flex items-baseline gap-md">
        <Button variant="row" onClick={onBack}>
          Back
        </Button>
        <h2 className="m-0 font-sans text-title font-strong">{name}</h2>
        <span className="text-ink-soft">{noun(kind)}</span>
      </div>
      <Panel state={state} shape="form">
        {(payload) => (
          <>
            <OutcomeNotice state={notice} />
            <Section title="Spec">
              {payload.spec ? (
                <Facts
                  rows={Object.entries(payload.spec).flatMap(([field, value]) => {
                    const fact = specFact(field, value, payload.spec_schema);
                    return fact ? [fact] : [];
                  })}
                />
              ) : (
                <p className="m-0">{payload.summary}</p>
              )}
            </Section>
            <Section title="Status">
              <Facts
                rows={payload.fields.filter((field) => field !== ID_FIELD).map((field) =>
                  field === OWNER_FIELD
                    ? { label: OWNER_HEADING, value: creator(payload.status[field], viewer) }
                    : {
                        label: heading(field, payload.spec_schema),
                        value: cell(field, payload.status[field] ?? null, payload.spec_schema, now),
                      },
                )}
              />
            </Section>
            <Section title="Links">
              {payload.links.length ? (
                <ul className="m-0 list-none p-0">
                  {payload.links.map((link) => (
                    <li key={link.relation + link.kind + link.name} className="py-2xs">
                      {link.opens ? (
                        <button
                          type="button"
                          data-part="link"
                          onClick={() => onOpen({ kind: link.kind, name: link.name })}
                          className="border-0 bg-transparent p-0 text-left font-strong text-inherit"
                        >
                          {link.relation + " " + noun(link.kind) + " " + link.name}
                        </button>
                      ) : (
                        <span data-part="link">
                          {link.relation + " " + noun(link.kind) + " " + link.name}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="m-0 text-ink-soft">Nothing links out of this one.</p>
              )}
            </Section>
            <div className="mb-2xl text-small text-ink-soft">
              {[
                payload.created_at ? "Created " + day(payload.created_at) : null,
                payload.updated_at ? "Updated " + day(payload.updated_at) : null,
              ]
                .filter((line) => line !== null)
                .join(" · ")}
            </div>
            {payload.deletes ? (
              <div className="flex flex-wrap gap-sm">
                {payload.applies && payload.spec_schema && payload.spec ? (
                  <Button variant="send" onClick={() => setEditing(payload)}>
                    Edit
                  </Button>
                ) : null}
                <ConfirmButton verb="Delete" variant="row" onClick={remove} />
              </div>
            ) : null}
          </>
        )}
      </Panel>
      {beside}
    </>
  );
}

function specFact(field: string, value: ObjectValue, schema: SpecSchema | null): Fact | null {
  if (value === null) return null;
  const rendered =
    typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : typeof value === "string" && isMoment(value)
        ? (day(value) ?? value)
        : String(value);
  if (enumerated(schema, field)) {
    return { label: heading(field, schema), value: <Chip>{rendered}</Chip> };
  }
  return {
    label: heading(field, schema),
    value: rendered,
    block: rendered.includes("\n") || rendered.length > FACT_VALUE_LINE,
  };
}

/** One mutation of one object as the lane takes it: the verb, the kind, the name the member typed,
 *  and the spec the schema's own fields produced. */
export type SpecEnvelope = {
  verb: "apply";
  kind: string;
  name: string;
  spec: Record<string, SpecValue>;
};

/** The one form a typed object is written through, for both the act that creates it and the act
 *  that changes it, made or changed in the same column the object itself opens in — the record
 *  panel, with the record's own heading and the record's own way out. Six schema fields under the
 *  records push the records off the screen and read as a seventh section of the page; in the column
 *  they are the act the member asked for, with the index still readable beside them and a refusal
 *  stated next to the rows it was refused against.
 *
 *  `lead` is for the one field the schema cannot state: which agent's namespace the new row lands
 *  in, which only a view listing across agents knows to ask. `options` is for the one facet the
 *  schema cannot state: a field whose choices are the deploy's rather than the type's, as an
 *  agent's model is. */
export function SpecPanel({
  schema,
  kind,
  name,
  spec,
  title,
  lead,
  options,
  onDone,
  onClose,
}: {
  schema: SpecSchema;
  kind: string;
  name: string | null;
  spec: Record<string, ObjectValue> | null;
  title: string;
  lead?: ReactNode;
  options?: Record<string, string[] | null>;
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
    const outcome = await onDone({ verb: "apply", kind, name: objectName.trim(), spec: submitted });
    setBusy(false);
    if (outcome.refused) {
      setNotice(outcome);
      return;
    }
    onClose();
  }

  return (
    <RecordPanel title={title} onClose={onClose}>
      <OutcomeNotice state={notice} />
      <form onSubmit={send} className="flex flex-col gap-xl">
        {lead}
        <Field label="Name" htmlFor="object-name">
          <Input
            id="object-name"
            value={objectName}
            required={name === null}
            disabled={name !== null}
            onChange={(event) => setObjectName(event.target.value)}
          />
        </Field>
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
    </RecordPanel>
  );
}
