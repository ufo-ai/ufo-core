import { DataTable, DataTablePagination, useDataTable, type Column } from "@/blocks/data-table";
import { AMOUNT, PAYMENTS, type Payment } from "@/blocks/docs/examples/table-payments";

const COLUMNS: Column<Payment>[] = [
  { id: "id", header: "Payment", accessor: (row) => row.id, width: 104 },
  { id: "status", header: "Status", accessor: (row) => row.status, width: 112 },
  { id: "email", header: "Email", accessor: (row) => row.email },
  {
    id: "amount",
    header: "Amount",
    accessor: (row) => row.amount,
    cell: (row) => AMOUNT.format(row.amount),
    align: "right",
    width: 104,
  },
];

export function TableDataPagination() {
  const table = useDataTable({
    data: PAYMENTS,
    columns: COLUMNS,
    pageSize: 5,
    getRowId: (row) => row.id,
  });
  return (
    <div className="blk-data-frame">
      <DataTable table={table} emptyText="No results." />
      <DataTablePagination table={table} pageSizes={[5, 10, 25]} />
    </div>
  );
}
