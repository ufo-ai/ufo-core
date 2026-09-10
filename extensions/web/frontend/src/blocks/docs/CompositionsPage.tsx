import { DocPage, Example } from "@/blocks/docs/Docs";
import TodosLane from "@/blocks/docs/examples/compose-todos";
import MeetingsLane from "@/blocks/docs/examples/compose-meetings";
import LeadsLane from "@/blocks/docs/examples/compose-leads";
import CodingLane from "@/blocks/docs/examples/compose-coding";
import AssistantLane from "@/blocks/docs/examples/compose-assistant";
import Shell from "@/blocks/docs/examples/compose-shell";
import CardWithTable from "@/blocks/docs/examples/compose-card-table";
import CardWithItems from "@/blocks/docs/examples/compose-card-items";
import StatDashboard from "@/blocks/docs/examples/compose-stat-dashboard";
import todosSource from "./examples/compose-todos.tsx?raw";
import meetingsSource from "./examples/compose-meetings.tsx?raw";
import leadsSource from "./examples/compose-leads.tsx?raw";
import codingSource from "./examples/compose-coding.tsx?raw";
import assistantSource from "./examples/compose-assistant.tsx?raw";
import shellSource from "./examples/compose-shell.tsx?raw";
import cardTableSource from "./examples/compose-card-table.tsx?raw";
import cardItemsSource from "./examples/compose-card-items.tsx?raw";
import dashboardSource from "./examples/compose-stat-dashboard.tsx?raw";
import "@/blocks/docs/compositions.css";

export function CompositionsPage() {
  return (
    <DocPage
      title="Compositions"
      description="The blocks assembled into the lanes and cards the apps are built from."
    >
      <Example
        title="Todos lane"
        description="Header, prompt row, then one table whose four sections fold their rows away."
        code={todosSource}
        align="start"
      >
        <TodosLane />
      </Example>
      <Example
        title="Meetings lane"
        description="A day header opens its summary, its prompts and its meetings; the past day drops to the secondary ink."
        code={meetingsSource}
        align="start"
      >
        <MeetingsLane />
      </Example>
      <Example
        title="Leads lane"
        description="The toolbar's search field narrows the table under it."
        code={leadsSource}
        align="start"
      >
        <LeadsLane />
      </Example>
      <Example
        title="Coding lane"
        description="A mono listing between the header and the prompts held at the foot."
        code={codingSource}
        align="start"
      >
        <CodingLane />
      </Example>
      <Example
        title="Assistant lane"
        description="Prose, the card the agent made, the member's own turn, and the composer at the foot."
        code={assistantSource}
        align="start"
      >
        <AssistantLane />
      </Example>
      <Example
        title="Shell"
        description="The four lanes side by side, scaled to fit the page."
        code={shellSource}
        align="start"
      >
        <Shell />
      </Example>
      <Example title="Card with table" code={cardTableSource} align="start">
        <CardWithTable />
      </Example>
      <Example title="Card with items" code={cardItemsSource} align="start">
        <CardWithItems />
      </Example>
      <Example title="Stat dashboard" code={dashboardSource} align="start">
        <StatDashboard />
      </Example>
    </DocPage>
  );
}
