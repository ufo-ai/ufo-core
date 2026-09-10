import { IconFolder } from "@tabler/icons-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";
import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemMeta, ItemTitle } from "@/blocks/item";

const FOLDERS = [
  { name: "Design", size: "4.2 GB" },
  { name: "Engineering", size: "11.8 GB" },
  { name: "Revenue", size: "1.1 GB" },
];

export default function CardItems() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage</CardTitle>
        <CardDescription>The three folders holding most of the quota.</CardDescription>
      </CardHeader>
      <CardContent>
        <ItemGroup flush>
          {FOLDERS.map((folder) => (
            <Item key={folder.name} size="sm">
              <ItemMedia variant="icon">
                <IconFolder size={16} stroke={1.5} />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{folder.name}</ItemTitle>
              </ItemContent>
              <ItemActions>
                <ItemMeta>{folder.size}</ItemMeta>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </CardContent>
    </Card>
  );
}
