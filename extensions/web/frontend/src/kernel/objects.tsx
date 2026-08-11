import { useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, type Fact } from "@/components/ui/facts";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td } from "@/components/ui/table";
import { FormFromSchema, initialSpecValue, type SpecSchema, type SpecValue } from "@/kernel/form";
import {
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { DataTable, type Column } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { day, isMoment, relativeMoment } from "@/lib/moments";
import { agentHash, chatHash } from "@/lib/route";
import type { Agent } from "@/lib/types";

const AGENT_FIELD = "object-agent";
/** The conversation a record belongs to is where the member goes, never a column they read: the
 *  value is a uuid, which is the wire's word for a thread and nobody's answer to "which one". The
 *  name carries the press, and the row's own `View` still opens the record itself. */
const CONVERSATION_FIELD = "conversation";

export type ObjectValue = string | number | boolean | null;

export type ObjectRow = { name: string; summary: string } & Record<string, ObjectValue>;

/** One row of an index. The projection names the agent that owns the row whether the read ran in
 *  one namespace or across the audience, so an act reaches the right intent lane either way. */
type IndexRow = ObjectRow & { agent_id: string; agent_name: string };

export type ObjectLink = { relation: string; kind: string; name: string; opens: boolean };

/** What every object page knows about the kind it renders, ahead of any one row: the fields it
 *  declared for columns and filters, the schema its form draws, and whether the intent lane takes
 *  an `apply` for it at all. */
type Kind = {
  kind: string;
  fields: string[];
  spec_schema: SpecSchema | null;
  applies: boolean;
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
    <span className="rounded-control border border-edge-control px-sm py-hair text-small">
      {children}
    </span>
  );
}

/** One value of an index cell or a status row. The column or the label already carries the field's
 *  name, so the value stands alone — and a value the record does not hold takes an em dash rather
 *  than an empty cell, which reads as a table that failed to draw. A status moment is the one the
 *  member is waiting on, so it reads as the wait; every calendar day takes `day()`. */
function cell(
  field: string,
  value: ObjectValue,
  schema: SpecSchema | null,
  now: Date,
): ReactNode {
  if (value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (isMoment(value)) return relativeMoment(value, now);
  if (enumerated(schema, field)) return <Chip>{value}</Chip>;
  return value;
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
  label,
}: {
  agentId: string | null;
  kind: string;
  label: string;
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
  return <ObjectIndex agentId={agentId} kind={kind} label={label} onOpen={setAt} />;
}

function ObjectIndex({
  agentId,
  kind,
  label,
  onOpen,
}: {
  agentId: string | null;
  kind: string;
  label: string;
  onOpen: (at: At) => void;
}) {
  const agents = useAgents();
  const mainAgent = useMainAgent();
  const [reloads, setReloads] = useState(0);
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [orderBy, setOrderBy] = useState("name");
  const [descending, setDescending] = useState(false);
  const [narrowed, setNarrowed] = useState("");
  const [cursor, setCursor] = useState("");
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
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

  return (
    <Panel state={state}>
      {(payload) => {
        const narrowing = Boolean(query || narrowed);
        const acts = payload.applies && payload.spec_schema !== null && owner !== null;
        const shown = payload.fields.filter((field) => field !== CONVERSATION_FIELD);
        const columns: Column[] = [{ label: heading("name", payload.spec_schema), sort: "name" }];
        if (agentId === null) columns.push("Agent");
        columns.push({ label: heading("summary", payload.spec_schema), sort: "summary" });
        for (const field of shown) {
          columns.push({ label: heading(field, payload.spec_schema), sort: field });
        }
        if (acts || payload.fields.includes(CONVERSATION_FIELD)) columns.push("");
        const flags = payload.fields.filter(
          (field) =>
            narrowed === field || payload.objects.some((row) => typeof row[field] === "boolean"),
        );
        return (
          <>
            <OutcomeNotice state={notice} />
            <Section
              title={label}
              bar={
                <>
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      setCursor("");
                      setQuery(typed);
                    }}
                  >
                    <Input
                      type="search"
                      aria-label={"Search " + noun(payload.kind)}
                      placeholder="Search"
                      className="max-w-control-row"
                      value={typed}
                      onChange={(event) => setTyped(event.target.value)}
                    />
                  </form>
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
                  {acts ? (
                    <Button variant="send" onClick={() => setCreating(true)}>
                      {"New " + noun(payload.kind)}
                    </Button>
                  ) : null}
                  <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
                </>
              }
            >
              <DataTable
                columns={columns}
                rows={payload.objects}
                rowKey={(row) => row.agent_id + "/" + row.name}
                empty={"No " + noun(payload.kind) + " is visible to you."}
                note={narrowing ? "No " + noun(payload.kind) + " matches this search." : undefined}
                sort={{
                  by: orderBy,
                  descending,
                  onSort: (field) => {
                    setCursor("");
                    setDescending(field === orderBy ? !descending : false);
                    setOrderBy(field);
                  },
                }}
              >
                {(row) => (
                  <>
                    <Td>
                      {typeof row[CONVERSATION_FIELD] === "string" ? (
                        <a
                          data-part="primary"
                          href={chatHash(row[CONVERSATION_FIELD])}
                          className="block max-w-full truncate font-strong text-inherit underline"
                        >
                          {row.name}
                        </a>
                      ) : (
                        <button
                          type="button"
                          data-part="primary"
                          onClick={() =>
                            onOpen({ agentId: row.agent_id, kind: payload.kind, name: row.name })
                          }
                          className="block max-w-full truncate border-0 bg-transparent p-0 text-left font-strong text-inherit"
                        >
                          {row.name}
                        </button>
                      )}
                    </Td>
                    {agentId === null ? (
                      <Td>
                        <a
                          href={agentHash(row.agent_id, "overview")}
                          className="text-inherit underline"
                        >
                          {row.agent_name}
                        </a>
                      </Td>
                    ) : null}
                    <Td>{row.summary || "—"}</Td>
                    {shown.map((field) => (
                      <Td key={field}>
                        {cell(field, row[field] ?? null, payload.spec_schema, now)}
                      </Td>
                    ))}
                    {acts || payload.fields.includes(CONVERSATION_FIELD) ? (
                      <Td>
                        <div className="flex justify-end gap-xs">
                          {payload.fields.includes(CONVERSATION_FIELD) ? (
                            <Button
                              variant="row"
                              onClick={() =>
                                onOpen({
                                  agentId: row.agent_id,
                                  kind: payload.kind,
                                  name: row.name,
                                })
                              }
                            >
                              View
                            </Button>
                          ) : null}
                          {acts ? (
                            <ConfirmButton
                              verb="Delete"
                              variant="row"
                              onClick={async () =>
                                setNotice(
                                  await submit(row.agent_id, {
                                    verb: "delete",
                                    kind: payload.kind,
                                    name: row.name,
                                  }),
                                )
                              }
                            />
                          ) : null}
                        </div>
                      </Td>
                    ) : null}
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
            </Section>
            {creating && payload.spec_schema && owner !== null ? (
              <NewObject
                schema={payload.spec_schema}
                kind={payload.kind}
                agents={agentId === null ? agents : []}
                owner={owner}
                onDone={submit}
                onClose={() => setCreating(false)}
              />
            ) : null}
          </>
        );
      }}
    </Panel>
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
    <SpecDialog
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
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<DetailPayload>(
    "/objects/" + kind + "/" + encodeURIComponent(name) + "?agent=" + agentId,
    reloads,
  );
  const now = new Date();
  const [editing, setEditing] = useState(false);

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

  return (
    <>
      <div className="mb-lg flex items-baseline gap-md">
        <Button variant="row" onClick={onBack}>
          Back
        </Button>
        <h2 className="m-0 text-title font-strong">{name}</h2>
        <span className="opacity-(--muted-strong)">{noun(kind)}</span>
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
                rows={payload.fields.map((field) => ({
                  label: heading(field, payload.spec_schema),
                  value: cell(field, payload.status[field] ?? null, payload.spec_schema, now),
                }))}
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
                <p className="m-0 opacity-(--muted)">Nothing links out of this one.</p>
              )}
            </Section>
            <div className="mb-2xl text-small opacity-(--muted-strong)">
              {[
                payload.created_at ? "Created " + day(payload.created_at) : null,
                payload.updated_at ? "Updated " + day(payload.updated_at) : null,
              ]
                .filter((line) => line !== null)
                .join(" · ")}
            </div>
            {payload.applies && payload.spec_schema ? (
              <div className="flex flex-wrap gap-sm">
                {payload.spec ? (
                  <Button variant="send" onClick={() => setEditing(true)}>
                    Edit
                  </Button>
                ) : null}
                <ConfirmButton verb="Delete" variant="row" onClick={remove} />
              </div>
            ) : null}
            {editing && payload.spec_schema && payload.spec ? (
              <SpecDialog
                schema={payload.spec_schema}
                kind={payload.kind}
                name={payload.name}
                spec={payload.spec}
                title={"Edit " + payload.name}
                onDone={submit}
                onClose={() => setEditing(false)}
              />
            ) : null}
          </>
        )}
      </Panel>
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
  return {
    label: heading(field, schema),
    value: enumerated(schema, field) ? (
      <Chip>{rendered}</Chip>
    ) : (
      <span className="whitespace-pre-wrap">{rendered}</span>
    ),
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
 *  that changes it. Six schema fields under the records push the records off the screen and read as
 *  a seventh section of the page; in a dialog they are the act the member asked for, committed or
 *  cancelled, with the index still behind them. `lead` is for the one field the schema cannot
 *  state: which agent's namespace the new row lands in, which only a view listing across agents
 *  knows to ask. `options` is for the one facet the schema cannot state: a field whose choices are
 *  the deploy's rather than the type's, as an agent's model is. */
export function SpecDialog({
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
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <OutcomeNotice state={notice} />
        <form id="object-spec" onSubmit={send} className="flex flex-col gap-xl">
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
        </form>
        <DialogFooter>
          <Button type="submit" form="object-spec" variant="send" busy={busy}>
            {name === null ? "Create" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
