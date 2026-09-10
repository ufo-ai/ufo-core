import { useState } from "react";
import { IconPencil } from "@tabler/icons-react";

import { IconButton } from "@/blocks/action-bar";
import { Checkbox } from "@/blocks/checkbox";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemGroup,
  ItemMedia,
  ItemTitle,
  StatusIcon,
  type ItemStatus,
} from "@/blocks/item";

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

export function ItemStatesInteractive() {
  const [rows, setRows] = useState(SEED);
  const [picked, setPicked] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  return (
    <ItemGroup
      onReorder={(from, to) =>
        setRows((held) => {
          const next = [...held];
          next.splice(to, 0, ...next.splice(from, 1));
          return next;
        })
      }
    >
      {rows.map((row) => (
        <Item
          key={row.id}
          size="sm"
          dragHandle
          selected={picked.includes(row.id)}
          editing={editing === row.id}
        >
          <ItemMedia variant="checkbox">
            <Checkbox
              label={`Select ${row.title}`}
              checked={picked.includes(row.id)}
              onCheckedChange={(on) =>
                setPicked((held) => (on ? [...held, row.id] : held.filter((id) => id !== row.id)))
              }
            />
          </ItemMedia>
          <ItemMedia variant="status">
            <StatusIcon
              status={row.status}
              label={`Move ${row.title} on`}
              onClick={() =>
                setRows((held) =>
                  held.map((held_row) =>
                    held_row.id === row.id ? { ...held_row, status: NEXT_STATUS[held_row.status] } : held_row,
                  ),
                )
              }
            />
          </ItemMedia>
          <ItemContent>
            {editing === row.id ? (
              <ItemTitle
                editable={{
                  value: draft,
                  onChange: setDraft,
                  onCommit: () => {
                    setRows((held) => held.map((r) => (r.id === row.id ? { ...r, title: draft } : r)));
                    setEditing(null);
                  },
                  onCancel: () => setEditing(null),
                }}
              />
            ) : (
              <ItemTitle>{row.title}</ItemTitle>
            )}
          </ItemContent>
          <ItemActions>
            <IconButton
              label={`Rename ${row.title}`}
              onClick={() => {
                setEditing(row.id);
                setDraft(row.title);
              }}
            >
              <IconPencil size={16} stroke={1.5} />
            </IconButton>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
