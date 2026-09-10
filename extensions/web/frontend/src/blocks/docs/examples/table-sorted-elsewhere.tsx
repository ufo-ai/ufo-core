import { useState } from "react";

import { Menu, MenuButton, MenuCheckboxItem, MenuContent, MenuLabel } from "@/blocks/menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const ROWS = [
  { company: "Stripe", headcount: 8000 },
  { company: "Airbnb", headcount: 6000 },
  { company: "Discord", headcount: 2500 },
  { company: "Notion", headcount: 1000 },
];
const KEYS = ["Company", "Headcount"] as const;

type Key = (typeof KEYS)[number];

export function TableSortedElsewhere() {
  const [key, setKey] = useState<Key>("Headcount");
  const sorted = [...ROWS].sort((one, other) =>
    key === "Company"
      ? one.company.localeCompare(other.company)
      : other.headcount - one.headcount,
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Menu>
        <MenuButton>Sort by {key.toLowerCase()}</MenuButton>
        <MenuContent align="start">
          <MenuLabel>Sort</MenuLabel>
          {KEYS.map((name) => (
            <MenuCheckboxItem key={name} checked={name === key} onCheckedChange={() => setKey(name)}>
              {name}
            </MenuCheckboxItem>
          ))}
        </MenuContent>
      </Menu>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead sorted={key === "Company" ? "asc" : false}>Company</TableHead>
            <TableHead align="right" sorted={key === "Headcount" ? "desc" : false}>
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
    </div>
  );
}
