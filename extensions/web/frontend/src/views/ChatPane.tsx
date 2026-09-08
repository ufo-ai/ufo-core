import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Chat, type ChatProps } from "@/views/Chat";
import { Header, Pane } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import type { Crumb } from "@/lib/title";
import type { ConversationAgent } from "@/lib/types";
import {
  ConversationSlotPane,
  SlotIcon,
  type ConversationSlotSummary,
  type ConversationSlotsPayload,
} from "@/views/ConversationSlotPane";

export type ChatPaneProps = ChatProps & {
  /** What the conversation is called — the rail row's own title. Absent for a conversation not yet
   *  opened, which is headed by nothing: a header over an empty screen states a conversation that
   *  does not exist yet. */
  title?: string;
  conversationOnly?: boolean;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
  /** The app holding the conversation, as the trail names it: the shell derives it once for the tab
   *  title and this band alike, so the two cannot name one app two ways. */
  crumb?: Crumb;
};

/** A conversation's own named sheet — its files, diffs or sources — over the transcript it belongs
 *  to. A caller already holding the inventory hands its summary over rather than reading it twice. */
export function ConversationSlot({
  agent,
  conversationId,
  slot,
  summary,
  onClose,
}: {
  agent: ConversationAgent;
  conversationId: string;
  slot: string;
  summary?: ConversationSlotSummary;
  onClose: () => void;
}) {
  const inventory = usePanelRead<ConversationSlotsPayload>(
    summary ? null : "/agents/" + agent.id + "/conversations/" + conversationId + "/slots",
  );
  const resolved =
    summary ??
    (inventory.phase === "ready"
      ? inventory.payload.slots.find((entry) => entry.id === slot)
      : undefined);
  return (
    <Sheet open title={resolved?.label ?? slot} onClose={onClose}>
      <ConversationSlotPane
        agent={agent}
        conversationId={conversationId}
        slot={slot}
        summary={resolved}
        embedded
      />
    </Sheet>
  );
}

/** The chat screen for one agent's conversation: a header with the slot acts, the transcript, and
 * the composer; a conversation founded here is reported through `onCreated`. */
export function ChatPane({
  agent,
  member,
  conversationId,
  focusComposer,
  onCreated,
  onActivity,
  title,
  conversationOnly = false,
  slot,
  onSelectSlot,
  crumb,
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
    <Header
      crumb={crumb}
      title={title ?? agentName(agent.name)}
      acts={
        !conversationOnly && slots.phase === "ready" && slots.payload.slots.length
          ? slots.payload.slots.map((entry) => (
              <Button
                key={entry.id}
                variant="quiet"
                size="icon"
                aria-label={entry.count ? entry.label + " " + entry.count : entry.label}
                aria-pressed={slot === entry.id}
                className={cn(slot === entry.id && "bg-fill")}
                onClick={() => onSelectSlot?.(slot === entry.id ? null : entry.id)}
              >
                <SlotIcon icon={entry.icon} />
              </Button>
            ))
          : null
      }
      pinned
    />
  ) : null;
  return (
    <Pane>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {header}
        <Chat
          agent={agent}
          member={member}
          conversationId={conversationId}
          focusComposer={focusComposer}
          onCreated={onCreated}
          onActivity={onActivity}
          onSettled={settled}
        />
      </div>
      {conversationId && slot ? (
        <ConversationSlot
          agent={agent}
          conversationId={conversationId}
          slot={slot}
          summary={selected}
          onClose={() => onSelectSlot?.(null)}
        />
      ) : null}
    </Pane>
  );
}
