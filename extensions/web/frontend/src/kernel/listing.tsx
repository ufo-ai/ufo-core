import { Fragment, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Table, TableNote, Td, Th } from "@/components/ui/table";
import { CardGrid, type CardMark } from "@/kernel/cards";
import {
  OutcomeNotice,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { Pager, type Placement } from "@/kernel/pager";
import { RowLines } from "@/kernel/rows";
import { postIntent } from "@/lib/api";
import { useViewer } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";
import type { CredentialRequest } from "@/lib/types";

export type RowContext<Row> = {
  open: (row: Row) => void;
  act: (envelope: unknown) => void;
  busy: boolean;
  viewer: string | null;
};

export type Part<Row> = {
  [Field in keyof Row & string]: {
    field: Field;
    render?: (
      value: Row[Field],
      row: Row,
      context: RowContext<Row>,
    ) => ReactNode;
  };
}[keyof Row & string];

export type Column<Row> = Part<Row> & { label: string };

export type Chip<Row> = { label: string; has?: (row: Row) => boolean };

export type RowLine<Row> = {
  primary: Part<Row>;
  meta: Part<Row>[];
  when?: Part<Row>;
};

export type CardFace<Row> = {
  mark: CardMark<Row>;
  primary: Part<Row>;
  status?: Part<Row>;
  body?: Part<Row>;
  meta?: Part<Row>;
};

type Presentation<Row> =
  | { columns: Column<Row>[]; list?: undefined; cards?: undefined }
  | { list: RowLine<Row>; columns?: undefined; cards?: undefined }
  | { cards: CardFace<Row>; columns?: undefined; list?: undefined };

export type ListingSpec<Payload, Row> = {
  read: string;
  note?: string;
  rows: (payload: Payload) => Row[];
  rowKey: (row: Row) => string;
  empty: string;
  group?: (row: Row) => string;
  unavailable?: (payload: Payload) => string | null;
  paged?: true;
  serverQuery?: true;
  query?: (place: Placement) => URLSearchParams;
  search?: (row: Row) => string;
  chips?: Chip<Row>[];
  actions?: (row: Row, context: RowContext<Row>) => ReactNode;
  detail?: (row: Row, close: () => void) => ReactNode;
  credentials?: (
    request: CredentialRequest,
    onStored: (slots: string[]) => void,
    close: () => void,
  ) => ReactNode;
} & Presentation<Row>;

export function Listing<Payload, Row>({
  title,
  spec,
  place,
  onPlace,
}: {
  title: string;
  spec: ListingSpec<Payload, Row>;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const viewer = useViewer();
  const [notice, setNotice] = useState<NoticeState>({
    text: place.notice ?? "",
    refused: false,
  });
  const [busy, setBusy] = useState(false);
  const [credentials, setCredentials] = useState<CredentialRequest | null>(
    null,
  );
  const state = usePanelRead<Payload>(spec.read + cursor(spec, place));
  const query = place.q ?? "";
  const picked = place.chip ?? null;
  const [typed, setTyped] = useState(query);

  const setQuery = (value: string) => onPlace({ q: value || undefined, after: undefined });
  const setPicked = (value: string | null) =>
    onPlace({ chip: value ?? undefined, after: undefined });
  const open = (row: Row) => onPlace({ open: spec.rowKey(row) });
  const close = () => onPlace({ open: undefined });

  async function act(envelope: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, envelope);
    setBusy(false);
    if (outcome.credentials) {
      setCredentials(outcome.credentials);
      return;
    }
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcomeNotice(outcome));
  }

  const context: RowContext<Row> = { open, act, busy, viewer };
  const term = query.trim().toLowerCase();
  const bySearch = (row: Row) =>
    !spec.search || !term || spec.search(row).toLowerCase().includes(term);
  const loaded =
    state.phase === "ready" && !spec.unavailable?.(state.payload)
      ? spec.rows(state.payload)
      : null;
  const lastLoaded = useRef<Row[] | null>(null);
  if (loaded !== null) lastLoaded.current = loaded;
  const known =
    loaded ?? (state.phase === "loading" ? lastLoaded.current : null);
  const searched = known?.filter(bySearch) ?? null;

  return (
    <>
      <OutcomeNotice state={notice} />
      <Section
        title={title}
        note={spec.note}
        bar={
          known?.length || state.phase === "failed" || (spec.serverQuery && known) ? (
            <>
              {spec.serverQuery ? (
                <form
                  className="flex items-stretch"
                  onSubmit={(event) => {
                    event.preventDefault();
                    setQuery(typed);
                  }}
                >
                  <Input
                    type="search"
                    aria-label="Search"
                    placeholder="Search"
                    className="max-w-control-row"
                    value={typed}
                    onChange={(event) => setTyped(event.target.value)}
                  />
                </form>
              ) : spec.search && known?.length ? (
                <Input
                  type="search"
                  aria-label="Search"
                  placeholder="Search"
                  className="max-w-control-row"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              ) : null}
              {spec.chips && (searched?.length || spec.serverQuery) ? (
                <Filter
                  options={spec.chips.map((chip) => ({
                    label: chip.label,
                    value: chip.label,
                  }))}
                  value={picked ?? ""}
                  onChange={(value) => setPicked(value || null)}
                />
              ) : null}
              {state.phase === "failed" && place.after ? (
                <Button
                  variant="row"
                  onClick={() => onPlace({ after: undefined, open: undefined })}
                >
                  First page
                </Button>
              ) : null}
            </>
          ) : null
        }
      >
        <Panel state={state} shape={spec.cards ? "cards" : "table"} empty={spec.unavailable}>
          {(payload) => {
            const rows = spec.rows(payload);
            if (!rows.length)
              return (
                <PanelBlank body={spec.serverQuery && (term || picked) ? "Nothing matches." : spec.empty} />
              );
            const chip = spec.chips?.find((entry) => entry.label === picked);
            const unknown = picked !== null && chip === undefined;
            const matched = unknown
              ? []
              : spec.serverQuery
                ? rows
                : rows.filter((row) => (!chip?.has || chip.has(row)) && bySearch(row));
            const note = unknown ? "That filter is not available." : "Nothing matches.";
            if (!matched.length && (spec.cards || spec.list))
              return <PanelBlank body={note} />;
            const records = (rows: Row[]) =>
              spec.cards ? (
                <Cards
                  face={spec.cards}
                  spec={spec}
                  rows={rows}
                  context={context}
                />
              ) : spec.list ? (
                <RowList
                  line={spec.list}
                  spec={spec}
                  rows={rows}
                  context={context}
                />
              ) : (
                <Table>
                  <thead>
                    <tr>
                      {spec.columns.map((column) => (
                        <Th key={column.field}>{column.label}</Th>
                      ))}
                      {spec.actions ? <Th>{""}</Th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length ? (
                      rows.map((row) => (
                        <tr key={spec.rowKey(row)}>
                          {spec.columns.map((column) => (
                            <Td key={column.field}>
                              {part(column, row, context)}
                            </Td>
                          ))}
                          {spec.actions ? (
                            <Td>{spec.actions(row, context)}</Td>
                          ) : null}
                        </tr>
                      ))
                    ) : (
                      <TableNote span={spec.columns.length + (spec.actions ? 1 : 0)}>
                        {note}
                      </TableNote>
                    )}
                  </tbody>
                </Table>
              );
            const grouped: { title: string | null; rows: Row[] }[] =
              spec.group && matched.length
                ? groupRows(matched, spec.group)
                : [{ title: null, rows: matched }];
            return (
              <>
                {grouped.map(({ title: groupTitle, rows }) =>
                  groupTitle ? (
                    <Section key={groupTitle} title={groupTitle}>
                      {records(rows)}
                    </Section>
                  ) : (
                    <Fragment key="all">{records(rows)}</Fragment>
                  ),
                )}
                {spec.paged ? (
                  <Pager
                    payload={
                      payload as {
                        newer?: string | null;
                        older?: string | null;
                      }
                    }
                    onPlace={onPlace}
                  />
                ) : null}
                {spec.detail && place.open
                  ? (() => {
                      const shown = rows.find(
                        (row) => spec.rowKey(row) === place.open,
                      );
                      return shown ? (
                        spec.detail(shown, close)
                      ) : (
                        <PanelEmpty>That item is not on this page.</PanelEmpty>
                      );
                    })()
                  : null}
              </>
            );
          }}
        </Panel>
        {credentials && spec.credentials
          ? spec.credentials(
              credentials,
              (slots) => onPlace({ notice: counted(slots) }),
              () => setCredentials(null),
            )
          : null}
      </Section>
    </>
  );
}

function groupRows<Row>(rows: Row[], group: (row: Row) => string) {
  const grouped = new Map<string, Row[]>();
  for (const row of rows) {
    const title = group(row);
    grouped.set(title, [...(grouped.get(title) ?? []), row]);
  }
  return [...grouped].map(([title, groupedRows]) => ({ title, rows: groupedRows }));
}

function counted(slots: string[]) {
  return slots.length === 1
    ? "Stored " + slots[0] + "."
    : slots.length + " credentials stored.";
}

function RowList<Payload, Row>({
  line,
  spec,
  rows,
  context,
}: {
  line: RowLine<Row>;
  spec: ListingSpec<Payload, Row>;
  rows: Row[];
  context: RowContext<Row>;
}) {
  const { when } = line;
  const { actions } = spec;
  return (
    <RowLines
      rows={rows}
      rowKey={spec.rowKey}
      primary={(row) => part(line.primary, row, context)}
      meta={(row) => line.meta.map((entry) => part(entry, row, context))}
      when={when ? (row) => part(when, row, context) : undefined}
      action={actions ? (row) => actions(row, context) : undefined}
    />
  );
}

function Cards<Payload, Row>({
  face,
  spec,
  rows,
  context,
}: {
  face: CardFace<Row>;
  spec: ListingSpec<Payload, Row>;
  rows: Row[];
  context: RowContext<Row>;
}) {
  const { mark, primary, status, body, meta } = face;
  const { actions } = spec;
  return (
    <CardGrid
      rows={rows}
      rowKey={spec.rowKey}
      mark={mark}
      primary={(row) => part(primary, row, context)}
      status={status ? (row) => part(status, row, context) : undefined}
      body={body ? (row) => part(body, row, context) : undefined}
      meta={meta ? (row) => part(meta, row, context) : undefined}
      action={actions ? (row) => actions(row, context) : undefined}
    />
  );
}


function cursor<Payload, Row>(
  spec: ListingSpec<Payload, Row>,
  place: Placement,
): string {
  const params = spec.query?.(place) ?? new URLSearchParams();
  if (spec.paged && place.after) params.set("after", place.after);
  const query = params.toString();
  return query ? "?" + query : "";
}

function part<Row>(
  entry: Part<Row>,
  row: Row,
  context: RowContext<Row>,
): ReactNode {
  const value = row[entry.field as keyof Row];
  if (entry.render) {
    const render = entry.render as (
      value: Row[keyof Row],
      row: Row,
      context: RowContext<Row>,
    ) => ReactNode;
    return render(value, row, context);
  }
  return value === null || value === undefined ? "" : String(value);
}
