import { useState } from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const ROWS = [
  { company: "Stripe", headcount: 8000 },
  { company: "Airbnb", headcount: 6000 },
  { company: "Discord", headcount: 2500 },
  { company: "Notion", headcount: 1000 },
];

export function TableSortable() {
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const sorted = [...ROWS].sort((a, b) =>
    order === "asc" ? a.headcount - b.headcount : b.headcount - a.headcount,
  );
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Company</TableHead>
          <TableHead
            align="right"
            sortable
            sorted={order}
            onSort={() => setOrder(order === "asc" ? "desc" : "asc")}
          >
            Headcount
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((row) => (
          <TableRow key={row.company}>
            <TableCell>{row.company}</TableCell>
            <TableCell align="right" muted>
              {row.headcount.toLocaleString("en-US")}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
