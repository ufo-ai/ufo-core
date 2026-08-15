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
  onAgentsIndex: () => void;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
};

export function ChatPane({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  onAgentsIndex,
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
    <PaneHeader
      parent={{ label: "Agents", onGo: onAgentsIndex }}
      current={agent.name}
      note={<span className="font-mono text-mono text-ink-soft">{agent.model}</span>}
      actions={
        slots.phase === "ready" && slots.payload.slots.length ? (
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
