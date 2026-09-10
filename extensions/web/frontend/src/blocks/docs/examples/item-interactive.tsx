import { useState } from "react";
import { IconInbox } from "@tabler/icons-react";

import { Item, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/blocks/item";

export function ItemInteractive() {
  const [picked, setPicked] = useState(false);
  return (
    <Item state={picked ? "active" : "default"} onClick={() => setPicked(!picked)}>
      <ItemMedia variant="icon">
        <IconInbox size={16} stroke={1.5} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Inbox</ItemTitle>
        <ItemDescription>{picked ? "Selected" : "Click the row to select it."}</ItemDescription>
      </ItemContent>
    </Item>
  );
}
