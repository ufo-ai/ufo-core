import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Chat, type ChatProps } from "@/views/Chat";
import { Pane, PaneHeader } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { cn } from "@/lib/cn";
import {
  ConversationSlotPane,
  SlotIcon,
  type ConversationSlotsPayload,
} from "@/views/ConversationSlotPane";

export type ChatPaneProps = ChatProps & {
  /** What the conversation is called — the rail row's own title. Absent for a conversation not yet
   *  opened, which is headed by nothing: the agent is named by the picker in its own composer, and
   *  a header over an empty screen states a conversation that does not exist yet. */
  title?: string;
  conversationOnly?: boolean;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
  onOpenAgent?: (agentId: string) => void;
};

export function ChatPane({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  title,
  conversationOnly = false,
  slot,
  onSelectSlot,
  onOpenAgent,
  agents,
  onPickAgent,
}: ChatPaneProps) {
  const [slotReloads, setSlotReloads] = useState(0);
  const slots = usePanelRead<ConversationSlotsPayload>(
    conversationId && !conversationOnly
      ? "/agents/" + agent.id + "/conversations/" + conversationId + "/slots"
      : null,
    slotReloads,
  );
  const selected =
    slots.phase === "ready" ? slots.payload.slots.find((entry) => entry.id === slot) : undefined;
  const settled = useCallback(() => setSlotReloads((count) => count + 1), []);
  /** A conversation is headed by what it is called, under the agent holding it. A conversation not
   *  yet opened is headed by nothing at all. */
  const header = conversationId ? (
    <PaneHeader
      parent={{
        label: agent.name,
        onGo: onOpenAgent && !conversationOnly ? () => onOpenAgent(agent.id) : undefined,
      }}
      current={title ?? agent.name}
      actions={
        !conversationOnly && slots.phase === "ready" && slots.payload.slots.length ? (
          <div className="flex items-center gap-sm" aria-label="Conversation slots">
            {slots.payload.slots.map((entry) => (
              <Button
                key={entry.id}
                variant="option"
                size="bar"
                aria-pressed={slot === entry.id}
                onClick={() => onSelectSlot?.(slot === entry.id ? null : entry.id)}
              >
                <SlotIcon icon={entry.icon} />
                <span>
                  {entry.label}
                  {entry.count ? " " + entry.count : ""}
                </span>
              </Button>
            ))}
          </div>
        ) : null
      }
    />
  ) : null;
  return (
    <Pane>
      <div
        className={cn(
          "relative grid min-h-0 flex-1 grid-cols-1",
          slot && "grid-cols-(--grid-slot) max-narrow:grid-cols-1",
        )}
      >
        <div className="flex min-h-0 min-w-0 flex-col">
          {header}
          <Chat
            agent={agent}
            member={member}
            conversationId={conversationId}
            onCreated={onCreated}
            onActivity={onActivity}
            onSettled={settled}
            agents={agents}
            onPickAgent={onPickAgent}
            onOpenArtifacts={
              conversationId && onSelectSlot ? () => onSelectSlot("artifacts") : undefined
            }
          />
        </div>
        {conversationId && slot ? (
          <ConversationSlotPane
            agent={agent}
            conversationId={conversationId}
            slot={slot}
            summary={selected}
            embedded
            onClose={() => onSelectSlot?.(null)}
          />
        ) : null}
      </div>
    </Pane>
  );
}
