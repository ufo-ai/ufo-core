/** The first run, which is one run on both shells. The screens, the enrichment read, the memory
 *  write, the thread per goal and the build screen live in the lanes shell's copy and are drawn
 *  from there; a shell supplies only the act that touches its own chat. Each shell's bundler
 *  resolves that module's `@/…` imports against its own tree, so the run reads the panel kernel,
 *  the API lane and the transport this shell ships rather than the other one's.
 *
 *  This file is the sidebar half of that seam, and it goes when the sidebar shell does. */
import type { Agent, Member } from "@/lib/types";

import { FirstRun as Run } from "../../../src/views/FirstRun";

/** What this shell's other pages read off the run: the Connect page shares its catalog read, and
 *  the seam test needs the build screen's own step. Nothing else crosses. */
export { BUILD_STEP_MS, FIRST_RUN_READ, WATCH_MS } from "../../../src/views/FirstRun";
export type { FirstRunPayload } from "../../../src/views/FirstRun";

/** The sidebar shell stands one chat at a time rather than a track of lanes, so the member is
 *  carried to the thread the run founded, and to this agent's own chat where it founded none. */
export function FirstRun({
  agent,
  agents,
  member,
  step,
  onStep,
  onOpenChat,
}: {
  agent: Agent;
  agents: Agent[];
  member: Member;
  step: string | undefined;
  onStep: (step: string | undefined) => void;
  onOpenChat: (conversationId: string | null) => void;
}) {
  return (
    <Run
      agent={agent}
      agents={agents}
      member={member}
      step={step}
      onStep={onStep}
      onClose={() => onOpenChat(null)}
      onDone={onOpenChat}
    />
  );
}
