import { Card, CardDescription, CardHeader, CardImage, CardTitle } from "@/blocks/card";
import { ItemMeta, Tag } from "@/blocks/item";

const COVER =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'><rect width='640' height='360' fill='%230095ff'/><circle cx='500' cy='180' r='120' fill='%23ff6700'/></svg>";

export default function CardImageExample() {
  return (
    <Card>
      <CardImage src={COVER} alt="Quarterly revenue cover" />
      <CardHeader>
        <CardTitle>Quarterly report</CardTitle>
        <CardDescription>Revenue, retention and the two accounts that moved the number.</CardDescription>
      </CardHeader>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Tag>Project</Tag>
        <ItemMeta>Sept 23</ItemMeta>
      </div>
    </Card>
  );
}
