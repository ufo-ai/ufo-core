import { Table, TableBody, TableEmpty, TableHead, TableHeader, TableRow } from "@/blocks/table";

export function TableEmptyState() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Company</TableHead>
          <TableHead>Category</TableHead>
          <TableHead align="right">Headcount</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableEmpty columns={3}>No leads match this filter.</TableEmpty>
      </TableBody>
    </Table>
  );
}
