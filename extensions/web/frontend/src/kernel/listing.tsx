import { useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { OutcomeNotice, Panel, PanelEmpty, outcomeNotice, usePanelRead, type NoticeState } from "@/kernel/panel";
import { Pager, type Placement } from "@/kernel/pager";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";

export type RowContext<Row> = {
  open: (row: Row) => void;
  act: (envelope: unknown) => void;
  busy: boolean;
};

export type Part<Row> = {
  [Field in keyof Row & string]: {
    field: Field;
    render?: (value: Row[Field], row: Row, context: RowContext<Row>) => ReactNode;
  };
}[keyof Row & string];

export type Column<Row> = Part<Row> & { label: string };

export type Chip<Row> = { label: string; has: (row: Row) => boolean };

export type RowLine<Row> = {
  primary: Part<Row>;
  meta: Part<Row>[];
  when?: Part<Row>;
};

type Presentation<Row> =
  | { columns: Column<Row>[]; list?: undefined }
  | { list: RowLine<Row>; columns?: undefined };

export type ListingSpec<Payload, Row> = {
  read: string;
  rows: (payload: Payload) => Row[];
  rowKey: (row: Row) => string;
  empty: string;
  unavailable?: (payload: Payload) => string | null;
  paged?: true;
  search?: (row: Row) => string;
  chips?: Chip<Row>[];
  actions?: (row: Row, context: RowContext<Row>) => ReactNode;
  detail?: (row: Row, close: () => void) => ReactNode;
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
  const [notice, setNotice] = useState<NoticeState>({ text: place.notice ?? "", refused: false });
  const [busy, setBusy] = useState(false);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<Payload>(spec.read + cursor(spec, place), reloads);
  const query = place.q ?? "";
  const picked = place.chip ?? null;

  const setQuery = (value: string) => onPlace({ q: value || undefined });
  const setPicked = (value: string | null) => onPlace({ chip: value ?? undefined });
  const open = (row: Row) => onPlace({ open: spec.rowKey(row) });
  const close = () => onPlace({ open: undefined });

  async function act(envelope: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, envelope);
    setBusy(false);
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcomeNotice(outcome));
  }

  const context: RowContext<Row> = { open, act, busy };
  const term = query.trim().toLowerCase();
  const bySearch = (row: Row) =>
    !spec.search || !term || spec.search(row).toLowerCase().includes(term);
  const loaded =
    state.phase === "ready" && !spec.unavailable?.(state.payload)
      ? spec.rows(state.payload)
      : null;
  const lastLoaded = useRef<Row[] | null>(null);
  if (loaded !== null) lastLoaded.current = loaded;
  const known = loaded ?? (state.phase === "loading" ? lastLoaded.current : null);
  const searched = known?.filter(bySearch) ?? null;

  return (
    <>
      {known !== null || state.phase === "failed" ? (
        <div className="mb-md flex flex-wrap items-center gap-sm">
          {spec.search && known?.length ? (
            <Input
              type="search"
              aria-label="Search"
              placeholder="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          ) : null}
          {spec.chips && searched?.length
            ? spec.chips.map((chip) => (
                <Button
                  key={chip.label}
                  variant="row"
                  aria-pressed={picked === chip.label}
                  className={cn("m-0", picked === chip.label && "bg-fill font-strong")}
                  onClick={() => setPicked(picked === chip.label ? null : chip.label)}
                >
                  {chip.label + " "}
                  <span className="tabular-nums">{searched.filter(chip.has).length}</span>
                </Button>
              ))
            : null}
          {state.phase === "failed" && place.after ? (
            <Button variant="row" className="m-0" onClick={() => onPlace({ after: undefined })}>
              First page
            </Button>
          ) : null}
          <Button
            variant="row"
            className="m-0 ml-auto"
            onClick={() => setReloads((count) => count + 1)}
          >
            Refresh
          </Button>
        </div>
      ) : null}
      <Panel state={state} empty={spec.unavailable}>
        {(payload) => {
          const rows = spec.rows(payload);
          if (!rows.length)
            return (
              <>
                <PanelEmpty>{spec.empty}</PanelEmpty>
                <OutcomeNotice state={notice} />
              </>
            );
          const chip = spec.chips?.find((entry) => entry.label === picked);
          if (picked !== null && chip === undefined)
            return (
              <>
                <PanelEmpty>That filter is not available.</PanelEmpty>
                <OutcomeNotice state={notice} />
              </>
            );
          const matched = rows.filter((row) => (!chip || chip.has(row)) && bySearch(row));
          if (!matched.length)
            return (
              <>
                <PanelEmpty>Nothing matches.</PanelEmpty>
                <OutcomeNotice state={notice} />
              </>
            );
          return (
            <>
              {spec.list ? (
                <RowList line={spec.list} spec={spec} rows={matched} context={context} />
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
                    {matched.map((row) => (
                      <tr key={spec.rowKey(row)}>
                        {spec.columns.map((column) => (
                          <Td key={column.field}>{part(column, row, context)}</Td>
                        ))}
                        {spec.actions ? <Td>{spec.actions(row, context)}</Td> : null}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
              {spec.paged ? (
                <Pager
                  payload={payload as { newer?: string | null; older?: string | null }}
                  onPlace={onPlace}
                />
              ) : null}
              <OutcomeNotice state={notice} />
              {spec.detail && place.open
                ? (() => {
                    const shown = rows.find((row) => spec.rowKey(row) === place.open);
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
    </>
  );
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
  return (
    <ul className="m-0 mb-4xl list-none p-0">
      {rows.map((row) => (
        <li
          key={spec.rowKey(row)}
          className="flex items-baseline gap-md border-b border-edge-soft py-md last:border-b-0"
        >
          <div className="min-w-0 flex-1">
            <div
              data-part="primary"
              className="overflow-hidden text-ellipsis whitespace-nowrap text-body"
            >
              {part(line.primary, row, context)}
            </div>
            <MetaLine parts={line.meta.map((entry) => part(entry, row, context))} />
          </div>
          {line.when ? (
            <div
              data-part="when"
              className="whitespace-nowrap font-mono text-small tabular-nums opacity-(--muted)"
            >
              {part(line.when, row, context)}
            </div>
          ) : null}
          {spec.actions ? <div>{spec.actions(row, context)}</div> : null}
        </li>
      ))}
    </ul>
  );
}

function MetaLine({ parts }: { parts: ReactNode[] }) {
  const shown = parts.filter((entry) => entry !== null && entry !== undefined && entry !== "");
  if (!shown.length) return null;
  return (
    <div
      data-part="meta"
      className="overflow-hidden text-ellipsis whitespace-nowrap text-small opacity-(--muted)"
    >
      {shown.map((entry, index) => (
        <span key={index}>
          {index ? " · " : ""}
          {entry}
        </span>
      ))}
    </div>
  );
}

function cursor<Payload, Row>(spec: ListingSpec<Payload, Row>, place: Placement): string {
  if (!spec.paged || !place.after) return "";
  return "?" + new URLSearchParams({ after: place.after }).toString();
}

function part<Row>(entry: Part<Row>, row: Row, context: RowContext<Row>): ReactNode {
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
