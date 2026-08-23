import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Chat, type ChatProps } from "@/views/Chat";
import { Header, Pane } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { useSlot } from "@/kernel/slots";
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

/** A conversation's own named pane — its files, its diffs, its sources — standing in the track
 *  beside the transcript it belongs to. It asks for the slot from inside the pane, because the
 *  track is the pane's: a caller outside its own `Pane` reaches none, and the panel would stand
 *  under the transcript instead of beside it.
 *
 *  The slot's name is the slot's own, so a caller already holding the conversation's inventory —
 *  the pane whose header draws a control for every slot — hands it over rather than making the
 *  same read a second time, and a caller holding none reads it here, once, for both the header the
 *  track draws and the list standing under it. */
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
  return useSlot(
    <ConversationSlotPane
      agent={agent}
      conversationId={conversationId}
      slot={slot}
      summary={resolved}
      embedded
    />,
    { id: slot, kind: "panel", title: resolved?.label ?? slot, onClose },
  );
}

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
          onCreated={onCreated}
          onActivity={onActivity}
          onSettled={settled}
          onOpenArtifacts={
            conversationId && onSelectSlot ? () => onSelectSlot("artifacts") : undefined
          }
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
