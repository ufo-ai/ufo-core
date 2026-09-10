import { Item, ItemContent, ItemGroup, ItemMedia, ItemMeta, ItemTitle } from "@/blocks/item";

const ROWS = [
  { id: "349", title: "Connector audit fails on refresh", stamp: "12:04" },
  { id: "351", title: "Cutover window moved to Friday", stamp: "12:18" },
  { id: "352", title: "Credential rotation blocked", stamp: "13:02" },
];

export function ItemMediaMono() {
  return (
    <ItemGroup>
      {ROWS.map((row) => (
        <Item key={row.id} size="sm">
          <ItemMedia variant="default" font="mono">
            {row.id}
          </ItemMedia>
          <ItemContent>
            <ItemTitle font="mono">{row.title}</ItemTitle>
          </ItemContent>
          <ItemMeta font="mono">{row.stamp}</ItemMeta>
        </Item>
      ))}
    </ItemGroup>
  );
}
