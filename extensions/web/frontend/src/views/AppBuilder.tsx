import { useEffect, useState } from "react";

import { COLUMN, Header } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { Chat } from "@/views/Chat";
import type { TasksSlotPayload } from "@/views/ConversationSlotPane";
import { cn } from "@/lib/cn";
import { chatState, updateChat, useChat } from "@/lib/chatStore";
import { sendMessage } from "@/lib/turnStream";
import type { Agent, Member } from "@/lib/types";

const OPENING_MESSAGE = "Build me a new app.";

export const APP_CREATOR_TITLE = "App Creator";

/** Not the chat screen's `new:<agentId>`, so neither pane's founding send can ever hold the other's
 *  busy, and a wizard mount finds nothing on the key it watches but its own run. */
export function wizardKey(agentId: string): string {
  return "wizard:" + agentId;
}

export type AppBuilderProps = {
  agent: Agent;
  member: Member;
  onSettled: () => void;
  onClose: () => void;
};

export function AppBuilder({ agent, member, onSettled, onClose }: AppBuilderProps) {
  const key = wizardKey(agent.id);
  const held = useChat(key);
  const conversationId = held.founded?.conversationId ?? null;
  const [settles, setSettles] = useState(0);

  useEffect(() => {
    // Read here rather than from the render's snapshot: a StrictMode second pass and the sends of other
    // mounts have already marked the key, and only its current state says whether an opening send belongs.
    const current = chatState(key);
    if (current.closed) updateChat(key, (state) => ({ ...state, closed: false }));
    if (current.founded || current.busy || (current.messages ?? []).length > 0) return;
    void sendMessage({ key, agentId: agent.id, agentModel: agent.model, conversationId: null }, OPENING_MESSAGE, OPENING_MESSAGE);
  }, [key, agent.id]);

  return (
    <section aria-label={APP_CREATOR_TITLE} className="flex min-h-0 min-w-0 flex-col">
      <Header heading={2} title={APP_CREATOR_TITLE} onClose={onClose} pinned />
      <Phases conversationId={conversationId} agentId={agent.id} reloads={settles} />
      <Chat
        agent={agent}
        member={member}
        conversationId={conversationId}
        foundingKey={key}
        onSettled={() => {
          setSettles((count) => count + 1);
          onSettled();
        }}
      />
    </section>
  );
}

function Phases({
  conversationId,
  agentId,
  reloads,
}: {
  conversationId: string | null;
  agentId: string;
  reloads: number;
}) {
  const board = usePanelRead<TasksSlotPayload>(
    conversationId
      ? "/agents/" + agentId + "/conversations/" + conversationId + "/slots/tasks"
      : null,
    reloads,
  );
  if (board.phase !== "ready" || !board.payload.total_count) return null;
  const { tasks, total_count: total, completed_count: done } = board.payload;
  const reached =
    tasks.find((task) => task.status === "in_progress") ??
    tasks.filter((task) => task.status === "completed").at(-1) ??
    tasks[0];
  return (
    <div className={cn(COLUMN, "flex shrink-0 flex-col gap-xs px-2xl pt-lg")}>
      <div
        role="progressbar"
        aria-label={APP_CREATOR_TITLE}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-valuetext={done + " of " + total + " done"}
        className="h-2xs w-full overflow-hidden rounded-control bg-fill"
      >
        <div
          className="h-full bg-ink transition-[width] duration-200 ease-control motion-reduce:transition-none"
          style={{ width: (done / total) * 100 + "%" }}
        />
      </div>
      <div className="flex items-baseline gap-md font-mono text-small text-ink-soft">
        <span className="min-w-0 flex-1 truncate">{reached.description}</span>
        <span className="shrink-0 tabular-nums">
          {tasks.indexOf(reached) + 1} of {total}
        </span>
      </div>
    </div>
  );
}
