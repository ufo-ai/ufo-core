import { useCallback, useState } from "react";

import { Chat, type ChatProps } from "@/views/Chat";
import { COLUMN, Pane } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { cn } from "@/lib/cn";
import {
  ConversationSlotPane,
  SlotIcon,
  type ConversationSlotsPayload,
} from "@/views/ConversationSlotPane";

export type ChatPaneProps = ChatProps & {
  onOpenAgent: (agentId: string) => void;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
};

export function ChatPane({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  onOpenAgent,
  slot,
  onSelectSlot,
}: ChatPaneProps) {
  const [slotReloads, setSlotReloads] = useState(0);
  const slots = usePanelRead<ConversationSlotsPayload>(
    conversationId
      ? "/agents/" + agent.id + "/conversations/" + conversationId + "/slots"
      : null,
    slotReloads,
  );
  const selected =
    slots.phase === "ready" ? slots.payload.slots.find((entry) => entry.id === slot) : undefined;
  const settled = useCallback(() => setSlotReloads((count) => count + 1), []);
  const header = (
    <div className="border-b border-edge">
      <div className={cn(COLUMN, "flex items-baseline gap-md px-2xl py-lg")}>
        <button
          type="button"
          onClick={() => onOpenAgent(agent.id)}
          className="m-0 border-0 bg-transparent p-0 text-title font-strong text-inherit"
        >
          {agent.name}
        </button>
        <span className="font-mono text-mono opacity-(--opacity-muted-strong)">{agent.model}</span>
        {slots.phase === "ready" && slots.payload.slots.length ? (
          <div className="ml-auto flex items-center gap-sm" aria-label="Conversation slots">
            {slots.payload.slots.map((entry) => (
              <button
                key={entry.id}
                type="button"
                aria-pressed={slot === entry.id}
                onClick={() => onSelectSlot?.(slot === entry.id ? null : entry.id)}
              >
                <span className="inline-flex items-center gap-xs">
                  <SlotIcon icon={entry.icon} />
                  <span>
                    {entry.label}
                    {entry.count ? " " + entry.count : ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
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
