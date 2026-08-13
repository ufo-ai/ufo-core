import {
  BAR_CONTROL,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Placement } from "@/kernel/pager";
import { cn } from "@/lib/cn";
import type { Agent } from "@/lib/types";

/** The agent a place names, else the one the workspace answers with. `undefined` where the place
 *  names an agent this member cannot reach, which the screen reports rather than quietly falling
 *  back to a different agent's records. */
export function chosenAgent(agents: Agent[], place: Placement): Agent | undefined {
  if (place.agent) return agents.find((entry) => entry.id === place.agent);
  return agents.find((entry) => entry.main) ?? agents[0];
}

/** The subject a Customize tab's records belong to, led in the section bar. A workspace with one
 *  agent is offered nothing to pick, the way the sidebar offers no agent list for a new
 *  conversation it can only start one way. */
export function AgentPicker({
  agent,
  agents,
  onPick,
}: {
  agent: Agent;
  agents: Agent[];
  onPick: (agentId: string) => void;
}) {
  if (agents.length < 2) return null;
  return (
    <Select value={agent.id} onValueChange={onPick}>
      <SelectTrigger aria-label="Agent" className={cn(BAR_CONTROL, "w-(--container-control-row)")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {agents.map((entry) => (
          <SelectItem key={entry.id} value={entry.id}>
            {entry.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
