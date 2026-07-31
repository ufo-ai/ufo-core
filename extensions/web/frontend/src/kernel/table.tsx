import type { ReactNode } from "react";

import { Table, Th } from "@/components/ui/table";
import { PanelEmpty } from "@/kernel/panel";

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  empty,
  children,
}: {
  columns: string[];
  rows: Row[];
  rowKey: (row: Row) => string;
  empty: ReactNode;
  children: (row: Row) => ReactNode;
}) {
  if (!rows.length) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <Table>
      <thead>
        <tr>
          {columns.map((column, index) => (
            <Th key={column + index}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>{children(row)}</tr>
        ))}
      </tbody>
    </Table>
  );
}
