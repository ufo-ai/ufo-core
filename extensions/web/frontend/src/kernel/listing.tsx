import { useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Filter } from "@/components/ui/filter";
import { Table, TableNote, Td, TdActs, Th, tableFloor } from "@/components/ui/table";
import { BANDS, PageToolbar, usePageSearch } from "@/kernel/pane";
import { CardGrid, type CardMark } from "@/kernel/cards";
import {
  OutcomeNotice,
  Panel,
  PanelBlank,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { Pager, type Placement } from "@/kernel/pager";
import { RowLines } from "@/kernel/rows";
import { postAction, postIntent, type IntentOutcome } from "@/lib/api";
import { useViewer } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionInput, ActionView, CredentialRequest } from "@/lib/types";

/** What a row's acts are drawn with: the lane to post on — `act` for an object mutation's envelope,
 *  `action` for one of the acts the read projected for the listing's kind, posted with the call its
 *  view carries — whether one is in flight, who is reading, and those projected acts. */
export type RowContext = {
  act: (envelope: unknown) => void;
  action: (view: ActionView, input: ActionInput) => void;
  busy: boolean;
  viewer: string | null;
  actions: ActionView[];
};

export type Part<Row> = {
  [Field in keyof Row & string]: {
    field: Field;
    render?: (
      value: Row[Field],
      row: Row,
      context: RowContext,
    ) => ReactNode;
  };
}[keyof Row & string];

export type Column<Row> = Part<Row> & { label: string };

export type Chip<Row> = { label: string; has?: (row: Row) => boolean };

export type RowLine<Row> = {
  primary: Part<Row>;
  meta: Part<Row>[];
  when?: Part<Row>;
  /** A record with no screen of its own: its sentence runs to the end rather than to the row's
   *  width, because the row is all there is to read it on. */
  whole?: true;
};

export type CardFace<Row> = {
  mark: CardMark<Row>;
  primary: Part<Row>;
  status?: Part<Row>;
  body?: Part<Row>;
  meta?: Part<Row>;
  whole?: true;
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
  actions?: (row: Row, context: RowContext) => ReactNode;
  /** Where the payload carries the acts the read projected for the listing's kind. */
  views?: (payload: Payload) => ActionView[];
  credentials?: (
    request: CredentialRequest,
    onStored: (slots: string[]) => void,
    close: () => void,
  ) => ReactNode;
} & Presentation<Row>;

export function Listing<Payload, Row>({
  spec,
  place,
  onPlace,
}: {
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

  const setPicked = (value: string | null) =>
    onPlace({ chip: value ?? undefined, after: undefined });

  async function act(envelope: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    settle(await postIntent(mainAgent.id, envelope));
  }

  async function action(view: ActionView, input: ActionInput) {
    if (!mainAgent) return;
    setBusy(true);
    settle(await postAction(mainAgent.id, view.call, input));
  }

  function settle(outcome: IntentOutcome) {
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
  const search = usePageSearch();

  return (
    <>
      <OutcomeNotice state={notice} />
      {search ? <PageToolbar /> : null}
      <Section
        note={spec.note}
        bar={
          known?.length || state.phase === "failed" || (spec.serverQuery && known) ? (
            <>
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
                <Button variant="row" onClick={() => onPlace({ after: undefined })}>
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
            const context: RowContext = {
              act,
              action,
              busy,
              viewer,
              actions: spec.views?.(payload) ?? [],
            };
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
                <Table
                  columns={[
                    ...spec.columns.map((column) => column.label),
                    ...(spec.actions ? [""] : []),
                  ]}
                  floor={tableFloor({
                    prose: spec.columns.length + (spec.actions ? 1 : 0),
                  })}
                >
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
                            <Td key={column.field}>{part(column, row, context)}</Td>
                          ))}
                          {spec.actions ? (
                            <TdActs>{spec.actions(row, context)}</TdActs>
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
            const families =
              spec.group && matched.length ? groupRows(matched, spec.group) : null;
            return (
              <div className={BANDS}>
                {families ? (
                  <div className={FAMILIES}>
                    {families.map(({ title: familyTitle, rows }) => (
                      <Section key={familyTitle} title={familyTitle}>
                        {records(rows)}
                      </Section>
                    ))}
                  </div>
                ) : (
                  records(matched)
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
              </div>
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

/** The gap between two families of one listing, wider than the gap a `Section` leaves between its
 *  own heading and its records. Stacked at the band gap the two distances are equal, and a family
 *  name sits as far from the records it heads as from the family above it — so it heads neither. */
const FAMILIES = "flex flex-col gap-8xl";

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
  context: RowContext;
}) {
  const { when, whole } = line;
  const { actions } = spec;
  return (
    <RowLines
      whole={whole}
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
  context: RowContext;
}) {
  const { mark, primary, status, body, meta, whole } = face;
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
      whole={whole}
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
  context: RowContext,
): ReactNode {
  const value = row[entry.field as keyof Row];
  if (entry.render) {
    const render = entry.render as (
      value: Row[keyof Row],
      row: Row,
      context: RowContext,
    ) => ReactNode;
    return render(value, row, context);
  }
  return value === null || value === undefined ? "" : String(value);
}
