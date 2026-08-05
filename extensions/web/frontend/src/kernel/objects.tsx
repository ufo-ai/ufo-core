import { useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/field";
import { FormFromSchema, initialSpecValue, type SpecSchema, type SpecValue } from "@/kernel/form";
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
import { postIntent } from "@/lib/api";
import { isMoment, relativeMoment } from "@/lib/moments";
import { cn } from "@/lib/cn";

export type ObjectValue = string | number | boolean | null;

export type ObjectRow = { name: string; summary: string } & Record<string, ObjectValue>;

export type ObjectLink = { relation: string; kind: string; name: string; opens: boolean };

type Kind = {
  kind: string;
  fields: string[];
  spec_schema: SpecSchema | null;
  applies: boolean;
};

type IndexPayload = Kind & { objects: ObjectRow[]; next_cursor: string | null };

type DetailPayload = Kind & {
  name: string;
  summary: string;
  spec: Record<string, ObjectValue> | null;
  status: Record<string, ObjectValue>;
  links: ObjectLink[];
  created_at: string | null;
  updated_at: string | null;
};

function enumerated(schema: SpecSchema | null, field: string): boolean {
  const property = schema?.properties?.[field];
  if (!property) return false;
  return Boolean(property.enum ?? (property.anyOf ?? []).some((entry) => entry.enum));
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-control border border-edge-control px-sm py-hair font-mono text-mono">
      {children}
    </span>
  );
}

function fieldPart(
  field: string,
  value: ObjectValue,
  schema: SpecSchema | null,
  now: Date,
): ReactNode {
  if (value === null || value === "") return null;
  if (typeof value === "boolean") return value ? <Chip>{field}</Chip> : null;
  if (typeof value === "number") return field + " " + String(value);
  if (isMoment(value)) return field + " " + relativeMoment(value, now);
  if (enumerated(schema, field)) return <Chip>{value}</Chip>;
  return value;
}

function MetaLine({ parts }: { parts: ReactNode[] }) {
  const shown = parts.filter((part) => part !== null && part !== undefined && part !== "");
  if (!shown.length) return null;
  return (
    <div
      data-part="meta"
      className="mt-2xs flex flex-wrap items-baseline gap-sm text-small opacity-(--muted)"
    >
      {shown.map((part, index) => (
        <span key={index}>{part}</span>
      ))}
    </div>
  );
}

/** Where an object page stands: one kind, and one of its objects once a row or a link is opened. */
export type ObjectAddress = { kind: string; name: string | null };

const NOT_FOUND = 404;

/** One kind's pages for one agent: its index, and one row's detail once a row or a link is opened.
 *  `absent` is what a kind this deploy may not install states when its pages are not found. */
export function ObjectPane({
  agentId,
  kind,
  absent,
}: {
  agentId: string;
  kind: string;
  absent?: string;
}) {
  const [at, setAt] = useState<ObjectAddress>({ kind, name: null });
  if (at.name === null) {
    return (
      <ObjectIndex
        agentId={agentId}
        kind={at.kind}
        absent={absent}
        onOpen={(name) => setAt({ kind: at.kind, name })}
      />
    );
  }
  return (
    <ObjectDetail
      key={at.kind + "/" + at.name}
      agentId={agentId}
      kind={at.kind}
      name={at.name}
      onOpen={(next) => setAt(next)}
      onBack={() => setAt({ kind, name: null })}
    />
  );
}

export function ObjectIndex({
  agentId,
  kind,
  absent,
  onOpen,
}: {
  agentId: string;
  kind: string;
  absent?: string;
  onOpen: (name: string) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [orderBy, setOrderBy] = useState("name");
  const [descending, setDescending] = useState(false);
  const [narrowed, setNarrowed] = useState<string[]>([]);
  const [cursor, setCursor] = useState("");
  const params = new URLSearchParams({ agent: agentId, order_by: orderBy });
  if (query) params.set("q", query);
  if (descending) params.set("order", "desc");
  if (cursor) params.set("cursor", cursor);
  for (const field of narrowed) params.set(field, "true");
  const state = usePanelRead<IndexPayload>("/objects/" + kind + "?" + params.toString(), reloads);
  const now = new Date();

  async function submit(envelope: unknown) {
    const outcome = await postIntent(agentId, envelope);
    setNotice(outcomeNotice(outcome));
    setReloads((count) => count + 1);
  }

  return (
    <Panel
      state={state}
      failed={(message, status) => (
        <PanelEmpty>{absent !== undefined && status === NOT_FOUND ? absent : message}</PanelEmpty>
      )}
    >
      {(payload) => {
        const flags = payload.fields.filter(
          (field) =>
            narrowed.includes(field) ||
            payload.objects.some((row) => typeof row[field] === "boolean"),
        );
        return (
          <>
            <div className="mb-md flex flex-wrap items-center gap-sm">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  setCursor("");
                  setQuery(typed);
                }}
              >
                <Input
                  type="search"
                  aria-label="Search"
                  placeholder="Search"
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </form>
              <Label htmlFor="object-order">Order by</Label>
              <Select
                id="object-order"
                value={orderBy}
                onChange={(event) => {
                  setCursor("");
                  setOrderBy(event.target.value);
                }}
              >
                {["name", "summary"].concat(payload.fields).map((field) => (
                  <option key={field} value={field}>
                    {field}
                  </option>
                ))}
              </Select>
              <Button
                variant="row"
                aria-pressed={descending}
                className={cn("m-0", descending && "bg-fill font-strong")}
                onClick={() => {
                  setCursor("");
                  setDescending(!descending);
                }}
              >
                Descending
              </Button>
              {flags.map((field) => (
                <Button
                  key={field}
                  variant="row"
                  aria-pressed={narrowed.includes(field)}
                  className={cn("m-0", narrowed.includes(field) && "bg-fill font-strong")}
                  onClick={() => {
                    setCursor("");
                    setNarrowed(
                      narrowed.includes(field)
                        ? narrowed.filter((held) => held !== field)
                        : narrowed.concat(field),
                    );
                  }}
                >
                  {field}
                </Button>
              ))}
              <Button
                variant="row"
                className="m-0 ml-auto"
                onClick={() => setReloads((count) => count + 1)}
              >
                Refresh
              </Button>
            </div>
            {payload.objects.length ? (
              <ul className="m-0 mb-2xl list-none p-0">
                {payload.objects.map((row) => (
                  <li
                    key={row.name}
                    className="border-b border-edge-soft py-md last:border-b-0"
                  >
                    <button
                      type="button"
                      data-part="primary"
                      onClick={() => onOpen(row.name)}
                      className="block max-w-full overflow-hidden text-ellipsis whitespace-nowrap border-0 bg-transparent p-0 text-left text-body text-inherit underline"
                    >
                      {row.name}
                    </button>
                    <MetaLine
                      parts={[
                        row.summary as ReactNode,
                        ...payload.fields.map((field) =>
                          fieldPart(field, row[field] ?? null, payload.spec_schema, now),
                        ),
                      ]}
                    />
                  </li>
                ))}
              </ul>
            ) : (
              <PanelEmpty>
                {query || narrowed.length
                  ? "No " + payload.kind + " objects match."
                  : "No " + payload.kind + " objects."}
              </PanelEmpty>
            )}
            {payload.next_cursor ? (
              <div className="mb-lg">
                <Button variant="row" onClick={() => setCursor(payload.next_cursor ?? "")}>
                  Next page
                </Button>
              </div>
            ) : null}
            {cursor ? (
              <div className="mb-lg">
                <Button variant="row" onClick={() => setCursor("")}>
                  First page
                </Button>
              </div>
            ) : null}
            <OutcomeNotice state={notice} />
            {payload.applies && payload.spec_schema ? (
              <Section title={"New " + payload.kind}>
                <SpecForm
                  schema={payload.spec_schema}
                  kind={payload.kind}
                  name={null}
                  spec={null}
                  onDone={submit}
                />
              </Section>
            ) : null}
          </>
        );
      }}
    </Panel>
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

  async function submit(envelope: unknown) {
    const outcome = await postIntent(agentId, envelope);
    setNotice(outcomeNotice(outcome));
    setReloads((count) => count + 1);
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
      <div className="mb-md flex items-baseline gap-md">
        <Button variant="row" className="m-0" onClick={onBack}>
          Back
        </Button>
        <h1 className="m-0 text-title font-strong">{name}</h1>
        <span className="font-mono text-mono opacity-(--muted-strong)">{kind}</span>
      </div>
      <Panel state={state}>
        {(payload) => (
          <>
            <Section title="Spec">
              {payload.spec ? (
                <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-lg gap-y-2xs">
                  {Object.entries(payload.spec).map(([field, value]) => (
                    <SpecEntry
                      key={field}
                      field={field}
                      value={value}
                      schema={payload.spec_schema}
                      now={now}
                    />
                  ))}
                </dl>
              ) : (
                <p className="m-0">{payload.summary}</p>
              )}
            </Section>
            <Section title="Status">
              <MetaLine
                parts={payload.fields.map((field) =>
                  fieldPart(field, payload.status[field] ?? null, payload.spec_schema, now),
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
                          className="border-0 bg-transparent p-0 text-left text-inherit underline"
                        >
                          {link.relation + " " + link.kind + " " + link.name}
                        </button>
                      ) : (
                        <span data-part="link">
                          {link.relation + " " + link.kind + " " + link.name}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="m-0 opacity-(--muted)">Nothing links out of this one.</p>
              )}
            </Section>
            <div className="mb-2xl font-mono text-mono opacity-(--muted-strong)">
              {[
                payload.created_at ? "created " + relativeMoment(payload.created_at, now) : null,
                payload.updated_at ? "updated " + relativeMoment(payload.updated_at, now) : null,
              ]
                .filter((line) => line !== null)
                .join(" · ")}
            </div>
            <OutcomeNotice state={notice} />
            {payload.applies && payload.spec_schema ? (
              <>
                {payload.spec ? (
                  <Section title={"Edit " + payload.name}>
                    <SpecForm
                      schema={payload.spec_schema}
                      kind={payload.kind}
                      name={payload.name}
                      spec={payload.spec}
                      onDone={submit}
                    />
                  </Section>
                ) : null}
                <Button variant="row" onClick={remove}>
                  Delete
                </Button>
              </>
            ) : null}
          </>
        )}
      </Panel>
    </>
  );
}

function SpecEntry({
  field,
  value,
  schema,
  now,
}: {
  field: string;
  value: ObjectValue;
  schema: SpecSchema | null;
  now: Date;
}) {
  const rendered =
    value === null
      ? null
      : typeof value === "boolean"
        ? String(value)
        : typeof value === "string" && isMoment(value)
          ? relativeMoment(value, now)
          : String(value);
  if (rendered === null) return null;
  return (
    <>
      <dt className="font-mono text-mono opacity-(--muted)">{field}</dt>
      <dd className="m-0 whitespace-pre-wrap">
        {enumerated(schema, field) ? <Chip>{rendered}</Chip> : rendered}
      </dd>
    </>
  );
}

function SpecForm({
  schema,
  kind,
  name,
  spec,
  onDone,
}: {
  schema: SpecSchema;
  kind: string;
  name: string | null;
  spec: Record<string, ObjectValue> | null;
  onDone: (envelope: unknown) => Promise<void>;
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

  async function send(event: FormEvent) {
    event.preventDefault();
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
    await onDone({ verb: "apply", kind, name: objectName.trim(), spec: submitted });
    setBusy(false);
  }

  return (
    <form onSubmit={send}>
      <Label htmlFor="object-name">name</Label>
      <Input
        id="object-name"
        placeholder="object-name"
        value={objectName}
        disabled={name !== null}
        onChange={(event) => setObjectName(event.target.value)}
      />
      <FormFromSchema
        schema={schema}
        fields={fields}
        values={values}
        onChange={(field, value) => setValues((held) => ({ ...held, [field]: value }))}
      />
      <Button type="submit" variant="send" disabled={busy}>
        {name === null ? "Create" : "Save"}
      </Button>
    </form>
  );
}
