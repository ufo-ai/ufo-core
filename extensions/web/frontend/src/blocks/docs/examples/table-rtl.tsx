import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/blocks/table";

const INVOICES = [
  { id: "INV001", status: "مدفوعة", method: "بطاقة ائتمان", amount: "$250.00" },
  { id: "INV002", status: "معلقة", method: "باي بال", amount: "$150.00" },
  { id: "INV003", status: "غير مدفوعة", method: "تحويل بنكي", amount: "$350.00" },
];

export function TableRtl() {
  return (
    <div dir="rtl">
      <Table>
        <TableCaption>قائمة بأحدث الفواتير.</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead width={96}>الفاتورة</TableHead>
            <TableHead>الحالة</TableHead>
            <TableHead>الطريقة</TableHead>
            <TableHead align="right">المبلغ</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {INVOICES.map((invoice) => (
            <TableRow key={invoice.id}>
              <TableCell>{invoice.id}</TableCell>
              <TableCell muted>{invoice.status}</TableCell>
              <TableCell muted>{invoice.method}</TableCell>
              <TableCell align="right">{invoice.amount}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
