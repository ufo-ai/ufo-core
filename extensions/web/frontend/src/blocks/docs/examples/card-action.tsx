import { IconX } from "@tabler/icons-react";

import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";

export default function CardWithAction() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage</CardTitle>
        <CardDescription>The workspace is at 92 percent of its quota.</CardDescription>
        <CardAction>
          <button type="button" className="blk-card-close" aria-label="Dismiss">
            <IconX size={16} stroke={1.5} />
          </button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <p>Files older than a year can be archived to free 18 GB.</p>
      </CardContent>
    </Card>
  );
}
