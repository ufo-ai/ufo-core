import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";

export default function CardSmall() {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Connector audit</CardTitle>
        <CardDescription>Three accounts need a new token.</CardDescription>
      </CardHeader>
      <CardContent>
        <p>The 12px inset is the step a card takes inside another surface.</p>
      </CardContent>
    </Card>
  );
}
