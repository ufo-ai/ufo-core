import { Card, CardDescription, CardHeader, CardTitle } from "@/blocks/card";

export default function CardVariants() {
  return (
    <div style={{ display: "flex", gap: 16 }}>
      <Card variant="outline" style={{ flex: 1 }}>
        <CardHeader>
          <CardTitle>Outline</CardTitle>
          <CardDescription>A hairline box on the page ground.</CardDescription>
        </CardHeader>
      </Card>
      <Card variant="muted" style={{ flex: 1 }}>
        <CardHeader>
          <CardTitle>Muted</CardTitle>
          <CardDescription>A filled box with no edge.</CardDescription>
        </CardHeader>
      </Card>
      <Card variant="plain" style={{ flex: 1 }}>
        <CardHeader>
          <CardTitle>Plain</CardTitle>
          <CardDescription>Padding and nothing else.</CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
