import { DecodeLine } from "@/components/ui/decode";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Sources } from "@/components/ui/sources";
import { agentName } from "@/lib/agentName";
import { latestActivity } from "@/lib/turnRecord";
import type { SourceRef, SubagentRun } from "@/lib/types";

const AGENT_PROFILE = "agent:";

function runStep(run: SubagentRun): string {
  if (run.current) return run.current;
  const deeper = latestActivity(run.events, run.subagents);
  if (deeper) return deeper;
  if (run.name) return run.name;
  return run.profile.startsWith(AGENT_PROFILE)
    ? "App · " + agentName(run.profile.slice(AGENT_PROFILE.length))
    : "Subagent · " + run.profile;
}

/** The step a turn is on, what it has read, then the step of each subagent it waits on: a count
 *  alone reads as a stuck turn. A turn holding no running run draws no line — its reply is what it did. */
export function TurnActivity({
  working,
  runs,
  sources = [],
}: {
  working: string | null;
  runs: SubagentRun[];
  sources?: SourceRef[];
}) {
  const waiting = runs.filter((run) => run.running);
  const step =
    waiting.length > 0
      ? "Awaiting " + waiting.length + " subagent" + (waiting.length === 1 ? "" : "s")
      : working;
  if (!step && sources.length === 0) return null;
  return (
    <>
      {step ? (
        <Marker className="mt-2xs">
          <MarkerContent working>
            <DecodeLine text={step} />
          </MarkerContent>
        </Marker>
      ) : null}
      <Sources sources={sources} />
      {waiting.map((run) => (
        <Marker
          key={run.turn_id ?? run.conversation_id}
          indent
          className="mt-hair"
        >
          <MarkerContent working truncate>
            <DecodeLine text={runStep(run)} />
          </MarkerContent>
        </Marker>
      ))}
    </>
  );
}
