import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Chat, type ChatProps } from "@/views/Chat";
import { ConversationDetail, Disclose } from "@/views/Conversations";
import { COLUMN, Header, Pane } from "@/kernel/pane";
import { usePanelRead } from "@/kernel/panel";
import { subject, surfaceWord, useViewer } from "@/lib/audience";
import { AudienceMark } from "@/lib/audienceMark";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { SurfaceMark } from "@/lib/surfaceMark";
import type { Crumb } from "@/lib/title";
import type { Agent, ConversationAgent, Member, OwnedConversation } from "@/lib/types";
import {
  ConversationSlotPane,
  SlotIcon,
  type ConversationSlotSummary,
  type ConversationSlotsPayload,
} from "@/views/ConversationSlotPane";

export type ChatPaneProps = ChatProps & {
  title?: string;
  conversationOnly?: boolean;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
  crumb?: Crumb;
};

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
  audience,
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
          audience={audience}
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

export function LinkedPane({
  agent,
  conversation,
  member,
  crumb,
  slot,
  onActivity,
  onSelectSlot,
}: {
  agent: Agent;
  conversation: OwnedConversation;
  member: Member;
  crumb?: Crumb;
  slot?: string;
  onActivity: (conversationId: string) => void;
  onSelectSlot: (slot: string | null) => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const readable = conversation.readable || disclosed;
  return (
    <Pane>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header
          crumb={crumb}
          title={subject(conversation, viewer)}
          note={readable && conversation.commentable ? null : <AudienceMark entry={conversation} />}
          acts={<SurfaceMark conversation={conversation} />}
          pinned
        />
        {readable ? (
          conversation.commentable ? (
            <Chat
              agent={agent}
              member={member}
              conversationId={conversation.id}
              audience={conversation}
              onActivity={onActivity}
            />
          ) : (
            <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
              <ConversationDetail
                agent={agent}
                conversation={conversation}
                headed
              />
              <p className="max-w-hint text-ink-soft">
                This conversation is read-only here. Reply in {surfaceWord(conversation.surface)} to
                continue it.
              </p>
            </div>
          )
        ) : (
          <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
            <Disclose
              agent={agent}
              conversation={conversation}
              onOpened={() => setDisclosed(true)}
            />
          </div>
        )}
      </div>
      {readable && slot ? (
        <ConversationSlot
          agent={agent}
          conversationId={conversation.id}
          slot={slot}
          onClose={() => onSelectSlot(null)}
        />
      ) : null}
    </Pane>
  );
}
