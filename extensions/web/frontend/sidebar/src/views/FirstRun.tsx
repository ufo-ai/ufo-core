import type { Agent, Member } from "@/lib/types";

import { FirstRun as Run } from "../../../src/views/FirstRun";

export { BUILD_STEP_MS, FIRST_RUN_READ, WATCH_MS } from "../../../src/views/FirstRun";
export type { FirstRunPayload } from "../../../src/views/FirstRun";

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
