import {
  DataTable,
  DataTableColumnHeader,
  DataTablePagination,
  useDataTable,
  type Column,
} from "@/blocks/data-table";
import { AMOUNT, PAYMENTS, type Payment } from "@/blocks/docs/examples/table-payments";

const COLUMNS: Column<Payment>[] = [
  { id: "id", header: "Payment", accessor: (row) => row.id, width: 104 },
  { id: "status", header: "Status", accessor: (row) => row.status, sortable: true, width: 112 },
  {
    id: "email",
    header: (context) => <DataTableColumnHeader {...context}>Email</DataTableColumnHeader>,
    accessor: (row) => row.email,
    sortable: true,
  },
  {
    id: "amount",
    header: (context) => <DataTableColumnHeader {...context}>Amount</DataTableColumnHeader>,
    accessor: (row) => row.amount,
    cell: (row) => AMOUNT.format(row.amount),
    sortable: true,
    align: "right",
    width: 104,
  },
];

export function TableDataSorting() {
  const table = useDataTable({ data: PAYMENTS, columns: COLUMNS, getRowId: (row) => row.id });
  return (
    <div className="blk-data-frame">
      <DataTable table={table} emptyText="No results." />
      <DataTablePagination table={table} />
    </div>
  );
}
