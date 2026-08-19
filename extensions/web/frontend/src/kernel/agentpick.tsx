import {
  BAR_CONTROL,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { Placement } from "@/kernel/pager";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
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
  agentId,
  agents,
  onPick,
  onClosed,
}: {
  agentId: string;
  agents: Agent[];
  onPick: (agentId: string) => void;
  /** Where focus belongs once the list closes. The primitive hands it back to the trigger, which is
   *  where it belongs for a picker leading a bar; a picker standing inside a box the member is
   *  writing in says the box instead, so a pick never takes their place in the words. */
  onClosed?: () => void;
}) {
  if (agents.length < 2) return null;
  return (
    <Select value={agentId} onValueChange={onPick}>
      <SelectTrigger aria-label="Agent" className={cn(BAR_CONTROL, "w-(--container-control-row)")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent
        onCloseAutoFocus={
          onClosed
            ? (event) => {
                event.preventDefault();
                onClosed();
              }
            : undefined
        }
      >
        {agents.map((entry) => (
          <SelectItem key={entry.id} value={entry.id}>
            <span className="flex min-w-0 items-center gap-xs">
              <Avatar>
                <AvatarFallback>
                  <AgentIcon name={entry.icon} />
                </AvatarFallback>
              </Avatar>
              <span className="min-w-0 truncate">{agentName(entry.name)}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
