import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/blocks/table";

const INVOICES = [
  { id: "INV001", status: "Paid", amount: "$250.00" },
  { id: "INV002", status: "Pending", amount: "$150.00" },
  { id: "INV003", status: "Unpaid", amount: "$350.00" },
];

export function TableFooterCaption() {
  return (
    <Table>
      <TableCaption>Invoices raised in the last 30 days.</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead width={80}>Invoice</TableHead>
          <TableHead>Status</TableHead>
          <TableHead align="right">Amount</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {INVOICES.map((invoice) => (
          <TableRow key={invoice.id}>
            <TableCell>{invoice.id}</TableCell>
            <TableCell muted>{invoice.status}</TableCell>
            <TableCell align="right">{invoice.amount}</TableCell>
          </TableRow>
        ))}
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell colSpan={2}>Total</TableCell>
          <TableCell align="right">$750.00</TableCell>
        </TableRow>
      </TableFooter>
    </Table>
  );
}
