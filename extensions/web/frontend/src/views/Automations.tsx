import { useState } from "react";

import { Filter } from "@/components/ui/filter";
import { ObjectPane } from "@/kernel/objects";

/** What runs an agent when nobody is typing: a clock, or a source that changed. The two are one
 *  destination because a member asking what fires here on its own asks one question, and an answer
 *  split across two of them is one the member has to know to look for twice. They are two kinds
 *  with almost nothing in common to put in a column — a schedule and a next run against a source
 *  and where it came in — so the switcher shows one at a time, each keeping the columns, order,
 *  search and pager its own kind declares. It leads the toolbar, where what family to show stands. */
const KINDS = [
  { label: "Scheduled", value: "scheduled_task" },
  { label: "Triggers", value: "source_trigger" },
];

/** Every agent's automations, or one agent's. The section names no agent, so each index route
 *  answers across the viewer's whole audience and each row states — and is acted on through — the
 *  agent that owns it; the agent tab names its own and is already headed, so it passes no title. */
export function Automations({ agentId, title }: { agentId: string | null; title?: string }) {
  const [kind, setKind] = useState(KINDS[0].value);
  return (
    <ObjectPane
      key={kind}
      agentId={agentId}
      kind={kind}
      title={title}
      lead={<Filter options={KINDS} value={kind} onChange={setKind} all={false} />}
    />
  );
}
