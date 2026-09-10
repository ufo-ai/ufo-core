import { useState } from "react";

import { Prompt, Prompts } from "@/blocks/action-bar";
import { Card, CardButton, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/blocks/card";

const WIDTHS = [640, 480, 320];

export function CardStatesInteractive() {
  const [width, setWidth] = useState(640);
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Prompts wrap>
        {WIDTHS.map((step) => (
          <Prompt key={step} active={width === step} onClick={() => setWidth(step)}>
            {`${step}px`}
          </Prompt>
        ))}
      </Prompts>
      <div style={{ maxWidth: width }}>
        <Card size="lg">
          <CardHeader>
            <CardTitle size="lg">Connector audit</CardTitle>
            <CardDescription>Four of six connectors are cut over.</CardDescription>
          </CardHeader>
          {open ? (
            <CardContent>The rest wait on the credential audit, which runs on Thursday.</CardContent>
          ) : null}
          <CardFooter>
            <CardButton onClick={() => setOpen(!open)}>{open ? "Hide the detail" : "Show the detail"}</CardButton>
            <CardButton variant="secondary">Send a reminder</CardButton>
          </CardFooter>
        </Card>
      </div>
    </div>
  );
}
