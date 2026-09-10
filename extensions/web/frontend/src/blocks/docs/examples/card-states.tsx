import { Card, CardButton, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/blocks/card";

const WIDTHS = [640, 320];

export function CardStates() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {WIDTHS.map((width) => (
        <div key={width} style={{ maxWidth: width }}>
          <Card size="lg">
            <CardHeader>
              <CardTitle size="lg">Connector audit</CardTitle>
              <CardDescription>Four of six connectors are cut over.</CardDescription>
            </CardHeader>
            <CardContent>The rest wait on the credential audit, which runs on Thursday.</CardContent>
            <CardFooter>
              <CardButton>Open the audit</CardButton>
              <CardButton variant="secondary">Send a reminder</CardButton>
            </CardFooter>
          </Card>
        </div>
      ))}
    </div>
  );
}
