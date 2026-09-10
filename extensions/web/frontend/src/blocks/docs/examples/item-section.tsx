import { Item, ItemContent, ItemGroup, ItemSection, ItemTitle } from "@/blocks/item";

const GROUPS = [
  {
    name: "Today",
    badge: "26",
    accent: "primary" as const,
    rows: ["Draft the migration plan", "Review the connector audit"],
  },
  { name: "Tomorrow", badge: "27", accent: "muted" as const, rows: ["Rewrite the onboarding copy"] },
];

export function ItemSectionExample() {
  return (
    <ItemGroup>
      {GROUPS.map((group) => (
        <ItemSection
          key={group.name}
          label={group.name}
          badge={group.badge}
          count={group.rows.length}
          accent={group.accent}
        >
          {group.rows.map((row) => (
            <Item key={row} size="sm">
              <ItemContent>
                <ItemTitle>{row}</ItemTitle>
              </ItemContent>
            </Item>
          ))}
        </ItemSection>
      ))}
    </ItemGroup>
  );
}
