import { Avatar, AvatarStack } from "@/blocks/avatar";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/blocks/item";

const FACE =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect width='64' height='64' fill='%230095ff'/><circle cx='32' cy='24' r='11' fill='%23faf9f7'/><circle cx='32' cy='60' r='20' fill='%23faf9f7'/></svg>";

const TEAM = ["Sam Patel", "Priya Nair", "Ravi Shah"];

export function ItemAvatar() {
  return (
    <Item variant="outline">
      <ItemMedia variant="avatar">
        <Avatar alt="Ana Ruiz" fallback="AR" />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Ana Ruiz</ItemTitle>
        <ItemDescription>Owns the connector audit and the migration plan.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <AvatarStack>
          {TEAM.map((person) => (
            <Avatar key={person} alt={person} src={FACE} size={24} />
          ))}
        </AvatarStack>
      </ItemActions>
    </Item>
  );
}
