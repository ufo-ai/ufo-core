import { Checkbox } from "@/blocks/checkbox";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  StatusIcon,
  type ItemState,
  type ItemStatus,
} from "@/blocks/item";

type Row = {
  title: string;
  status: ItemStatus;
  checked: boolean;
  state: ItemState;
  selected?: boolean;
  dragging?: boolean;
  editing?: boolean;
};

const ROWS: Row[] = [
  { title: "Default", status: "ready", checked: false, state: "default" },
  { title: "Selected", status: "started", checked: true, state: "default", selected: true },
  { title: "Dragging", status: "started", checked: false, state: "default", dragging: true },
  { title: "Editing", status: "working", checked: false, state: "default", editing: true },
  { title: "Done", status: "done", checked: true, state: "default" },
  { title: "Past", status: "ready", checked: false, state: "past" },
  { title: "Disabled", status: "ready", checked: false, state: "disabled" },
];

export function ItemStates() {
  return (
    <ItemGroup>
      {ROWS.map((row) => (
        <Item
          key={row.title}
          size="sm"
          dragHandle
          state={row.state}
          selected={row.selected}
          dragging={row.dragging}
          editing={row.editing}
        >
          <ItemMedia variant="checkbox">
            <Checkbox label={row.title} checked={row.checked} onCheckedChange={() => undefined} />
          </ItemMedia>
          <ItemMedia variant="status">
            <StatusIcon status={row.status} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{row.title}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <ItemMeta>Sept 23</ItemMeta>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
