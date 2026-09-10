import { useState } from "react";

import {
  Card,
  CardButton,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/blocks/card";

export function CardButtonStates() {
  const [approved, setApproved] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Credential audit</CardTitle>
      </CardHeader>
      <CardContent>Four of six connectors are cut over. The rest wait on this audit.</CardContent>
      <CardFooter>
        <CardButton size="sm" disabled={approved} onClick={() => setApproved(true)}>
          {approved ? "Approved" : "Approve"}
        </CardButton>
        <CardButton size="sm" variant="secondary" onClick={() => setApproved(false)}>
          Reset
        </CardButton>
        <CardButton size="sm" variant="secondary" disabled>
          Archive
        </CardButton>
      </CardFooter>
    </Card>
  );
}
