import { IconChartBar, IconUsers } from "@tabler/icons-react";

import {
  Count,
  Item,
  ItemActions,
  ItemContent,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  StatusIcon,
  Tag,
} from "@/blocks/item";

const TODOS = [
  { status: "ready", title: "Draft the migration plan", people: "2", date: "Sept 23" },
  { status: "started", title: "Review the connector audit", people: "3", date: "Sept 23" },
  { status: "working", title: "Rewrite the onboarding copy", people: "2", date: "Sept 24" },
  { status: "done", title: "Ship the usage projection", people: "4", date: "Sept 21" },
] as const;

export function ItemSmall() {
  return (
    <ItemGroup>
      {TODOS.map((todo) => (
        <Item key={todo.title} size="sm">
          <ItemMedia variant="icon">
            <IconChartBar size={16} stroke={1.5} />
          </ItemMedia>
          <ItemMedia variant="status">
            <StatusIcon status={todo.status} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{todo.title}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <Tag>Project</Tag>
            <Count icon={<IconUsers size={14} stroke={1.5} />}>{todo.people}</Count>
            <ItemMeta>{todo.date}</ItemMeta>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
