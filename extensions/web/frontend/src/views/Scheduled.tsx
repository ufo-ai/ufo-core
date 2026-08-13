import { ObjectPane } from "@/kernel/objects";

const SCHEDULED_TASK_KIND = "scheduled_task";

/** Every agent's scheduled tasks in one list. The agent tab beside it holds the same kind for one
 *  agent; this one names no agent, so the index route answers across the viewer's whole audience
 *  and each row states — and is acted on through — the agent that owns it. */
export function Scheduled() {
  return <ObjectPane agentId={null} kind={SCHEDULED_TASK_KIND} title="Scheduled" />;
}
