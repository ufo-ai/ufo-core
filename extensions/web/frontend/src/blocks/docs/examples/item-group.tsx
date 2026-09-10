import { IconFolder } from "@tabler/icons-react";

import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/blocks/item";

const FOLDERS = [
  { name: "Design", description: "Component library and the lane mocks." },
  { name: "Engineering", description: "Connector audit, migrations, release notes." },
  { name: "Revenue", description: "Pipeline, pricing review, renewal risk." },
];

export function ItemGroupExample() {
  return (
    <ItemGroup flush>
      {FOLDERS.map((folder) => (
        <Item key={folder.name}>
          <ItemMedia variant="icon">
            <IconFolder size={16} stroke={1.5} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{folder.name}</ItemTitle>
            <ItemDescription>{folder.description}</ItemDescription>
          </ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
}
