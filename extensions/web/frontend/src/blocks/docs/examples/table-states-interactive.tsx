import { useState } from "react";

import { Checkbox } from "@/blocks/checkbox";
import { StatusIcon, type ItemStatus } from "@/blocks/item";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const NEXT_STATUS: Record<ItemStatus, ItemStatus> = {
  ready: "started",
  started: "working",
  working: "done",
  done: "ready",
};

const SEED = [
  { id: "plan", title: "Draft the migration plan", status: "ready" as ItemStatus },
  { id: "audit", title: "Review the connector audit", status: "started" as ItemStatus },
  { id: "copy", title: "Rewrite the onboarding copy", status: "working" as ItemStatus },
];

export function TableStatesInteractive() {
  const [rows, setRows] = useState(SEED);
  const [picked, setPicked] = useState<string[]>([]);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead width={32}>
            <Checkbox
              label="Select every task"
              checked={picked.length === rows.length}
              indeterminate={picked.length > 0 && picked.length < rows.length}
              onCheckedChange={(on) => setPicked(on ? rows.map((row) => row.id) : [])}
            />
          </TableHead>
          <TableHead width={24} />
          <TableHead>Task</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody
        onReorder={(from, to) =>
          setRows((held) => {
            const next = [...held];
            next.splice(to, 0, ...next.splice(from, 1));
            return next;
          })
        }
      >
        {rows.map((row) => (
          <TableRow key={row.id} selected={picked.includes(row.id)} state={row.status === "done" ? "done" : "default"}>
            <TableCell>
              <Checkbox
                label={`Select ${row.title}`}
                checked={picked.includes(row.id)}
                onCheckedChange={(on) =>
                  setPicked((held) => (on ? [...held, row.id] : held.filter((id) => id !== row.id)))
                }
              />
            </TableCell>
            <TableCell>
              <StatusIcon
                status={row.status}
                label={`Move ${row.title} on`}
                onClick={() =>
                  setRows((held) =>
                    held.map((r) => (r.id === row.id ? { ...r, status: NEXT_STATUS[r.status] } : r)),
                  )
                }
              />
            </TableCell>
            <TableCell>{row.title}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
