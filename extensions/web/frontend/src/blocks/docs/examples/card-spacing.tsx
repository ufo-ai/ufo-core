import type { CSSProperties } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";

export default function CardSpacing() {
  return (
    <Card style={{ "--blk-card-spacing": "20px" } as CSSProperties}>
      <CardHeader>
        <CardTitle>Spacing</CardTitle>
        <CardDescription>One variable sets the inset and every gap under it.</CardDescription>
      </CardHeader>
      <CardContent>
        <p>Set --blk-card-spacing to take a card off the three named steps.</p>
      </CardContent>
    </Card>
  );
}
