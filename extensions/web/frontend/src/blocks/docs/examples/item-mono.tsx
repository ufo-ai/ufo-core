import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMeta,
  ItemTitle,
} from "@/blocks/item";

const ISSUES = [
  { number: "#2419", title: "Thread-scoped URL watch: follow the URLs a thread mentions for 4 hours", date: "25 Aug" },
  { number: "#2349", title: "Make the apps and sites boundary, and let apps call tools", date: "24 Aug" },
  { number: "#2149", title: "Startup dash: add Stripe and Datadog", date: "20 Aug" },
];

export function ItemMono() {
  return (
    <ItemGroup>
      <Item variant="outline">
        <ItemContent>
          <ItemTitle font="mono">#eng</ItemTitle>
          <ItemDescription>PR #3116 bounds background work at balance lines to fix starvation.</ItemDescription>
          <ItemMeta font="mono">Closes #3112 · staging latency 42ms</ItemMeta>
        </ItemContent>
        <ItemActions>
          <ItemMeta>Led by @marshall · 14 msgs</ItemMeta>
        </ItemActions>
      </Item>
      {ISSUES.map((issue) => (
        <Item key={issue.number} size="sm">
          <ItemMeta font="mono">{issue.number}</ItemMeta>
          <ItemContent>
            <ItemTitle>{issue.title}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <ItemMeta>{issue.date}</ItemMeta>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
