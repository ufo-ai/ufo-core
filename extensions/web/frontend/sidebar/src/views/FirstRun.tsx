/** The first run, which is one run on both shells. The screens, the enrichment read, the memory
 *  write, the thread per goal and the build screen live in the lanes shell's copy and are drawn
 *  from there; a shell supplies only the two acts that touch its own chat. Each shell's bundler
 *  resolves that module's `@/…` imports against its own tree, so the run reads the panel kernel,
 *  the API lane and the transport this shell ships rather than the other one's.
 *
 *  This file is the sidebar half of that seam, and it goes when the sidebar shell does. */
import { setPendingAsk } from "@/lib/pendingAsk";
import type { Agent, Member } from "@/lib/types";

import { FirstRun as Run } from "../../../src/views/FirstRun";

/** What this shell's other pages read off the run: the Connect page shares its catalog read and
 *  its two installs, and the seam test needs the build screen's own step. Nothing else crosses. */
export { BUILD_STEP_MS, CONNECT_INSTALLS, FIRST_RUN_READ, WATCH_MS } from "../../../src/views/FirstRun";
export type { FirstRunPayload } from "../../../src/views/FirstRun";

/** The sidebar shell stands one composer per agent rather than a track of lanes, so the words the
 *  run hands over are committed against the agent itself and the member is carried to that chat. */
export function FirstRun({
  agent,
  agents,
  member,
  onOpenChat,
}: {
  agent: Agent;
  agents: Agent[];
  member: Member;
  onOpenChat: () => void;
}) {
  return (
    <Run
      agent={agent}
      agents={agents}
      member={member}
      onClose={onOpenChat}
      onHandoff={(text) => setPendingAsk(agent.id, text, true)}
      onDone={onOpenChat}
    />
  );
}
