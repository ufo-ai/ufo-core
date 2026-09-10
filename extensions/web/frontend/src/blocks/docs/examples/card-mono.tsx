import { IconSparkles } from "@tabler/icons-react";

import { Card, CardContent } from "@/blocks/card";

export default function CardMono() {
  return (
    <Card>
      <CardContent data-font="mono">
        <p>
          Here is a summary of today's design sync. We covered a lot of ground — from the new
          navigation pattern to component library updates and a few outstanding questions that need
          engineering input before we can move forward.
        </p>
        <div className="blk-card-chips">
          <button type="button" className="blk-card-chip">
            <IconSparkles size={16} stroke={1.5} />
            List recent todos
          </button>
          <button type="button" className="blk-card-chip">
            <IconSparkles size={16} stroke={1.5} />
            Coach me
          </button>
          <button type="button" className="blk-card-chip">
            <IconSparkles size={16} stroke={1.5} />
            Streamline calendar
          </button>
        </div>
      </CardContent>
    </Card>
  );
}
