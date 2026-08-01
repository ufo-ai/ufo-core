import { useState, type ReactNode } from "react";

import { Table, Td, Th } from "@/components/ui/table";
import { Notice, Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { Pager, type Placement } from "@/kernel/pager";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";

export type RowContext<Row> = {
  open: (row: Row) => void;
  act: (envelope: unknown) => void;
  busy: boolean;
};

export type Column<Row> = {
  [Field in keyof Row & string]: {
    field: Field;
    label: string;
    render?: (value: Row[Field], row: Row, context: RowContext<Row>) => ReactNode;
  };
}[keyof Row & string];

export type ListingSpec<Payload, Row> = {
  read: string;
  rows: (payload: Payload) => Row[];
  rowKey: (row: Row) => string;
  columns: Column<Row>[];
  empty: string;
  unavailable?: (payload: Payload) => string | null;
  paged?: true;
  actions?: (row: Row, context: RowContext<Row>) => ReactNode;
  detail?: (row: Row, close: () => void) => ReactNode;
};

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
  const [notice, setNotice] = useState(place.notice ?? "");
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState<Row | null>(null);
  const state = usePanelRead<Payload>(spec.read + cursor(spec, place));

  async function act(envelope: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, envelope);
    setBusy(false);
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcome.message);
  }

  const context: RowContext<Row> = { open: setOpened, act, busy };

  return (
    <Panel state={state} empty={spec.unavailable}>
      {(payload) => {
        const rows = spec.rows(payload);
        if (!rows.length)
          return (
            <>
              <PanelEmpty>{spec.empty}</PanelEmpty>
              <Notice>{notice}</Notice>
            </>
          );
        return (
          <>
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
                {rows.map((row) => (
                  <tr key={spec.rowKey(row)}>
                    {spec.columns.map((column) => (
                      <Td key={column.field}>{cell(column, row, context)}</Td>
                    ))}
                    {spec.actions ? <Td>{spec.actions(row, context)}</Td> : null}
                  </tr>
                ))}
              </tbody>
            </Table>
            {spec.paged ? (
              <Pager
                payload={payload as { newer?: string | null; older?: string | null }}
                place={place}
                onPlace={onPlace}
              />
            ) : null}
            <Notice>{notice}</Notice>
            {opened && spec.detail ? spec.detail(opened, () => setOpened(null)) : null}
          </>
        );
      }}
    </Panel>
  );
}

function cursor<Payload, Row>(spec: ListingSpec<Payload, Row>, place: Placement): string {
  if (!spec.paged || !place.after) return "";
  return "?" + new URLSearchParams({ after: place.after }).toString();
}

function cell<Row>(column: Column<Row>, row: Row, context: RowContext<Row>): ReactNode {
  const value = row[column.field as keyof Row];
  if (column.render) {
    const render = column.render as (
      value: Row[keyof Row],
      row: Row,
      context: RowContext<Row>,
    ) => ReactNode;
    return render(value, row, context);
  }
  return value === null || value === undefined ? "" : String(value);
}
