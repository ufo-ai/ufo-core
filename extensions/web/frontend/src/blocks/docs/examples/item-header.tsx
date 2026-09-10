import { IconUsers } from "@tabler/icons-react";

import {
  Count,
  Item,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemHeader,
  ItemMeta,
  ItemTitle,
  Tag,
} from "@/blocks/item";

const COVER =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='320' height='96'><rect width='320' height='96' fill='%230095ff'/><circle cx='260' cy='48' r='34' fill='%23ff6700'/></svg>";

export function ItemHeaderExample() {
  return (
    <Item variant="outline">
      <ItemHeader>Featured</ItemHeader>
      <img className="blk-item-picture" src={COVER} alt="" />
      <ItemContent>
        <ItemTitle>Quarterly report</ItemTitle>
        <ItemDescription lines={2}>
          Revenue, retention and the two accounts that moved the number, with the churn cohort broken out.
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
