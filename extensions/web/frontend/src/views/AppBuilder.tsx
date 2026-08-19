import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { COLUMN } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { Chat } from "@/views/Chat";
import type { TasksSlotPayload } from "@/views/ConversationSlotPane";
import { cn } from "@/lib/cn";
import { chatState, updateChat, useChat } from "@/lib/chatStore";
import { sendMessage } from "@/lib/turnStream";
import type { Agent, Member } from "@/lib/types";

/** What the portal says to open the run. The interview itself lives in the `create-application`
 *  skill, so this is an opening move and not a script: the agent reads what it already knows about
 *  the business and answers with a proposal. */
const OPENING_MESSAGE = "Build me a new app.";

export const APP_BUILDER_TITLE = "App Builder";

/** The wizard's own founding key — not the chat screen's `new:<agentId>`, so neither pane's
 *  founding send can ever hold the other's busy, and a wizard mount finds nothing on the key it
 *  watches but its own run. */
export function wizardKey(agentId: string): string {
  return "wizard:" + agentId;
}

export type AppBuilderProps = {
  agent: Agent;
  member: Member;
  /** Every settled turn: the apps read is re-run, so the app the last phase created reaches the
   *  rail with no signal of its own. */
  onSettled: () => void;
  onClose: () => void;
};

/** The app-building wizard, in the apps screen's own pane. It is a real conversation with the main
 *  agent over the chat transport every other conversation rides — no create endpoint of its own, and
 *  the app it lands is `object_apply` through the same gate a chat-founded build uses.
 *
 *  Unlike the panel-to-chat precedent (`pendingAsk`, which hands the composer words the member
 *  presses send on), the wizard opens speaking: the member already stated their intent by pressing
 *  `New application`, and what they want back is the proposal, not a prompt to type. Which
 *  conversation the run is lives in the store, not in this component: whichever send founds it —
 *  the opening send, or the composer's after that send failed — `migrateChat` leaves the founding
 *  record on the wizard's key, and this pane binds by reading the key it watches, so a mount holds
 *  no state an unmount can lose. A wizard reopened over a run in flight — or reached again after
 *  any other screen — joins that run where the key says it is, and a second conversation cannot be
 *  founded while the key wears the first. */
export function AppBuilder({ agent, member, onSettled, onClose }: AppBuilderProps) {
  const key = wizardKey(agent.id);
  const held = useChat(key);
  const conversationId = held.founded?.conversationId ?? null;
  const [settles, setSettles] = useState(0);

  useEffect(() => {
    // The store is read here rather than from the render's snapshot: a StrictMode second pass and
    // the sends of other mounts have already marked the key, and only the key's current state says
    // whether an opening send belongs. Anything already on it — a run in flight, a forwarding
    // record, a failed opening the member should read — means the pane joins or the member speaks
    // next, never a second send. A key closed mid-founding is reopened first, so the landing keeps
    // its forwarding record for the pane now watching it.
    const current = chatState(key);
    if (current.closed) updateChat(key, (state) => ({ ...state, closed: false }));
    if (current.founded || current.busy || (current.messages ?? []).length > 0) return;
    void sendMessage({ key, agentId: agent.id, conversationId: null }, OPENING_MESSAGE, OPENING_MESSAGE);
  }, [key, agent.id]);

  return (
    <section
      aria-label={APP_BUILDER_TITLE}
      className="flex min-h-0 min-w-0 flex-col max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:bg-surface"
    >
      <header className="flex h-(--size-control) shrink-0 items-center gap-2xl border-b border-edge px-2xl py-lg box-content">
        <h2 className="m-0 min-w-0 flex-1 truncate text-subtitle font-medium">New application</h2>
        <Button size="bar" onClick={onClose}>
          Close
        </Button>
      </header>
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

/** How far the run has come, read from the wizard conversation's own todo board — the durable record
 *  the `create-application` skill opens with one task per phase and marks as it goes, which the
 *  portal already serves as the conversation's `tasks` slot. Nothing else on the wire says where a
 *  conversation is, and a bar drawn from a guess about the words would state a phase the agent is
 *  not in. The board is re-read on every settled turn, which is where a phase changes; a run whose
 *  agent kept no board draws no bar rather than an invented one. */
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
  // The phase the bar names: the one under way, or between turns the last one finished. A run that
  // has finished none names the first, which is the phase it is about to take.
  const reached =
    tasks.find((task) => task.status === "in_progress") ??
    tasks.filter((task) => task.status === "completed").at(-1) ??
    tasks[0];
  return (
    // The bar stands in the reading column the transcript below it is set in, so the phase and the
    // words of the phase are read down one measure rather than across the whole pane.
    <div className={cn(COLUMN, "flex shrink-0 flex-col gap-xs px-2xl pt-lg")}>
      <div
        role="progressbar"
        aria-label={APP_BUILDER_TITLE}
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
