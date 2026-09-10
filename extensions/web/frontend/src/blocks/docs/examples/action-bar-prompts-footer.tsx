import { IconSparkles } from "@tabler/icons-react";

import { Prompt, Prompts } from "@/blocks/action-bar";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/blocks/card";

export function PromptsFooterExample() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Migration plan</CardTitle>
      </CardHeader>
      <CardContent>Four of six connectors are cut over. The rest wait on the credential audit.</CardContent>
      <CardFooter>
        <Prompts wrap>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>List the blocked connectors</Prompt>
          <Prompt>Draft the audit request</Prompt>
          <Prompt>Show the cutover order</Prompt>
        </Prompts>
      </CardFooter>
    </Card>
  );
}
