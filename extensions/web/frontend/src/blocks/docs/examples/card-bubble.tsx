import { Card, CardContent } from "@/blocks/card";

export default function CardBubble() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Card variant="muted" size="sm" inline align="end" style={{ maxWidth: "72%" }}>
        <CardContent>
          <p>This is really cool. I cannot wait to work with you.</p>
        </CardContent>
      </Card>
      <Card variant="muted" size="sm" inline align="end" style={{ maxWidth: "72%" }}>
        <CardContent>
          <p>Send the summary to the team when it is ready.</p>
        </CardContent>
      </Card>
    </div>
  );
}
