import { Table, TableHead, TableHeader, TableRow, TableSkeleton } from "@/blocks/table";

export function TableLoading() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Company</TableHead>
          <TableHead>Category</TableHead>
          <TableHead>Funding</TableHead>
          <TableHead>Headcount</TableHead>
        </TableRow>
      </TableHeader>
      <TableSkeleton rows={5} columns={4} />
    </Table>
  );
}
