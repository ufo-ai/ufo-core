import { Fragment } from "react";
import { IconFolder } from "@tabler/icons-react";

import { Item, ItemContent, ItemGroup, ItemMedia, ItemSeparator, ItemTitle } from "@/blocks/item";

const FOLDERS = ["Design", "Engineering", "Revenue"];

export function ItemSeparators() {
  return (
    <ItemGroup>
      {FOLDERS.map((folder, index) => (
        <Fragment key={folder}>
          {index === 0 ? null : <ItemSeparator />}
          <Item variant="muted" size="sm">
            <ItemMedia variant="icon">
              <IconFolder size={16} stroke={1.5} />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>{folder}</ItemTitle>
            </ItemContent>
          </Item>
        </Fragment>
      ))}
    </ItemGroup>
  );
}
