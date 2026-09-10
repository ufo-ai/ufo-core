import { IconUsers } from "@tabler/icons-react";

import {
  Count,
  Item,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  Tag,
} from "@/blocks/item";

const COVER =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='80' height='80'><rect width='80' height='80' fill='%230095ff'/><circle cx='40' cy='40' r='18' fill='%23ff6700'/></svg>";

export function ItemImage() {
  return (
    <Item variant="outline">
      <ItemMedia variant="image">
        <img src={COVER} alt="" />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Quarterly report</ItemTitle>
        <ItemDescription lines={2}>
          Revenue, retention and the two accounts that moved the number, with the churn cohort broken out and a note on
          the renewal that slipped to next quarter.
        </ItemDescription>
      </ItemContent>
      <ItemFooter>
        <Tag>Project</Tag>
        <Count icon={<IconUsers size={14} stroke={1.5} />}>4</Count>
        <ItemMeta>Sept 23</ItemMeta>
      </ItemFooter>
    </Item>
  );
}
