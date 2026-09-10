import { useState } from "react";

import { Item, ItemContent, ItemGroup, ItemSection, ItemTitle } from "@/blocks/item";

const SEED = ["Draft the migration plan", "Review the connector audit"];

export function ItemSectionEmpty() {
  const [waiting, setWaiting] = useState(SEED);
  const [done, setDone] = useState<string[]>([]);
  const move = (title: string) => {
    setWaiting(waiting.filter((row) => row !== title));
    setDone([...done, title]);
  };
  return (
    <ItemGroup>
      <ItemSection
        label="Waiting"
        count={waiting.length}
        empty="Every record is approved."
      >
        {waiting.map((title) => (
          <Item key={title} size="sm" onClick={() => move(title)}>
            <ItemContent>
              <ItemTitle>{title}</ItemTitle>
            </ItemContent>
          </Item>
        ))}
      </ItemSection>
      <ItemSection
        label="Approved"
        count={done.length}
        empty="A record appears here once you approve it."
      >
        {done.map((title) => (
          <Item key={title} size="sm" state="past">
            <ItemContent>
              <ItemTitle>{title}</ItemTitle>
            </ItemContent>
          </Item>
        ))}
      </ItemSection>
    </ItemGroup>
  );
}
