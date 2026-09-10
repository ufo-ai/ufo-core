import { IconFileText } from "@tabler/icons-react";

import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/blocks/item";

const VARIANTS = [
  { variant: "default", title: "Default", description: "A row in a list, separated by a rule." },
  { variant: "outline", title: "Outline", description: "A bordered card that stands on its own." },
  { variant: "muted", title: "Muted", description: "A filled tile for secondary content." },
] as const;

export function ItemVariants() {
  return (
    <ItemGroup>
      {VARIANTS.map((entry) => (
        <Item key={entry.variant} variant={entry.variant}>
          <ItemMedia variant="icon">
            <IconFileText size={16} stroke={1.5} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{entry.title}</ItemTitle>
            <ItemDescription>{entry.description}</ItemDescription>
          </ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
}
