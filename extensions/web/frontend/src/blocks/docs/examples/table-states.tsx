import { Checkbox } from "@/blocks/checkbox";
import { StatusIcon } from "@/blocks/item";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableMeta, TableRow, TableTag } from "@/blocks/table";

type Row = {
  title: string;
  state: "default" | "done" | "disabled";
  checked: boolean;
  selected?: boolean;
  dragging?: boolean;
  editing?: boolean;
};

const ROWS: Row[] = [
  { title: "Default", state: "default", checked: false },
  { title: "Selected", state: "default", checked: true, selected: true },
  { title: "Dragging", state: "default", checked: false, dragging: true },
  { title: "Editing", state: "default", checked: false, editing: true },
  { title: "Done", state: "done", checked: true },
  { title: "Disabled", state: "disabled", checked: false },
];

export function TableStates() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead width={32} />
          <TableHead width={24} />
          <TableHead>Task</TableHead>
          <TableHead hideBelow={640} width={96}>
            Project
          </TableHead>
          <TableHead hideBelow={880} align="right" width={80}>
            Due
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROWS.map((row) => (
          <TableRow
            key={row.title}
            state={row.state}
            selected={row.selected}
            dragging={row.dragging}
            editing={row.editing}
          >
            <TableCell>
              <Checkbox label={row.title} checked={row.checked} onCheckedChange={() => undefined} />
            </TableCell>
            <TableCell>
              <StatusIcon status={row.state === "done" ? "done" : "ready"} />
            </TableCell>
            <TableCell>{row.title}</TableCell>
            <TableCell hideBelow={640}>
              <TableTag>Migration</TableTag>
            </TableCell>
            <TableCell hideBelow={880} align="right">
              <TableMeta>Sept 23</TableMeta>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
