import { IconChevronRight } from "@tabler/icons-react";

import { Avatar } from "@/blocks/avatar";
import { Card } from "@/blocks/card";

export default function CardPill() {
  return (
    <Card variant="pill">
      <Avatar alt="Assistant" gradient={["#0095ff", "#ff6700"]} />
      <span>Competitive Analysis</span>
      <IconChevronRight size={16} stroke={1.5} />
    </Card>
  );
}
