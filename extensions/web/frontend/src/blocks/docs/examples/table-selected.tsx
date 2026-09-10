import { useState } from "react";

import { Mark, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const ROWS = [
  { company: "Stripe", category: "Payments" },
  { company: "Airbnb", category: "Travel" },
  { company: "Discord", category: "Social" },
  { company: "Notion", category: "Productivity" },
];

export function TableSelected() {
  const [picked, setPicked] = useState("Airbnb");
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Company</TableHead>
          <TableHead>Category</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROWS.map((row) => (
          <TableRow
            key={row.company}
            selected={row.company === picked}
            onClick={() => setPicked(row.company)}
          >
            <TableCell>
              <Mark />
              {row.company}
            </TableCell>
            <TableCell muted>{row.category}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
