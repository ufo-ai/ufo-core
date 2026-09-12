import { IconTerminal2 } from "@tabler/icons-react";
import { Suspense, lazy, useCallback, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Chat, type ChatProps } from "@/views/Chat";
import { ConversationDetail, Disclose } from "@/views/Conversations";
import { COLUMN, Header, Pane } from "@/kernel/pane";
import { Loading, usePanelRead } from "@/kernel/panel";
import { shellPath } from "@/lib/api";
import { subject, surfaceWord, useViewer, type AudienceEntry } from "@/lib/audience";
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
import type { ShellReport } from "@/views/ShellPane";

/** The terminal emulator is its own chunk: a header drawing the shell chip downloads none of it
 *  until the member opens the sheet. */
const ShellPane = lazy(() =>
  import("@/views/ShellPane").then((module) => ({ default: module.ShellPane })),
);

export type ChatPaneProps = ChatProps & {
  /** Who reads the open conversation, as the row that named it carries it. A conversation the
   *  member has not founded yet has no audience to state, and the title line states none. */
  audience?: AudienceEntry;
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

type ThreadActs = {
  acts: ReactNode;
  selected: ConversationSlotSummary | undefined;
  settled: () => void;
  shell: ReactNode;
};

function useThreadActs({
  agent,
  conversationId,
  enabled,
  slot,
  onSelectSlot,
}: {
  agent: ConversationAgent;
  conversationId: string | null | undefined;
  enabled: boolean;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
}): ThreadActs {
  const [slotReloads, setSlotReloads] = useState(0);
  const [shellOpen, setShellOpen] = useState(false);
  const open = enabled && conversationId ? conversationId : null;
  const slots = usePanelRead<ConversationSlotsPayload>(
    open ? "/agents/" + agent.id + "/conversations/" + open + "/slots" : null,
    slotReloads,
  );
  const shell = usePanelRead<ShellReport>(open ? shellPath(agent.id, open) : null);
  const settled = useCallback(() => setSlotReloads((count) => count + 1), []);
  const closeShell = useCallback(() => setShellOpen(false), []);
  const selected =
    slots.phase === "ready" ? slots.payload.slots.find((entry) => entry.id === slot) : undefined;
  const terminal = open && shell.phase === "ready" && shell.payload.available ? shell.payload : null;
  return {
    selected,
    settled,
    acts: (
      <>
        {open && slots.phase === "ready"
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
          : null}
        {terminal ? (
          <Button
            variant="quiet"
            size="icon"
            aria-label={terminal.active ? "Shell, sandbox running" : "Shell"}
            aria-pressed={shellOpen}
            className={cn(shellOpen && "bg-fill", !terminal.active && "text-ink-soft")}
            onClick={() => setShellOpen((raised) => !raised)}
          >
            <IconTerminal2 aria-hidden />
          </Button>
        ) : null}
      </>
    ),
    shell:
      open && terminal && shellOpen ? (
        <Sheet open title="Shell" onClose={closeShell}>
          <Suspense fallback={<Loading />}>
            <ShellPane agent={agent} conversationId={open} onEnded={closeShell} />
          </Suspense>
        </Sheet>
      ) : null,
  };
}

/** The chat screen for one agent's conversation: a header with the slot acts, the transcript, and
 * the composer; a conversation founded here is reported through `onCreated`. */
export function ChatPane({
  agent,
  member,
  conversationId,
  audience,
  focusComposer,
  readOnly,
  stops,
  focusRun,
  onCreated,
  onActivity,
  title,
  conversationOnly = false,
  slot,
  onSelectSlot,
  crumb,
}: ChatPaneProps) {
  const { acts, selected, settled, shell } = useThreadActs({
    agent,
    conversationId,
    enabled: !conversationOnly,
    slot,
    onSelectSlot,
  });
  const header =
    conversationId && !readOnly ? (
      <Header
        crumb={crumb}
        title={title ?? agentName(agent.name)}
        note={audience ? <AudienceMark entry={audience} /> : null}
        acts={acts}
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
          readOnly={readOnly}
          stops={stops}
          focusRun={focusRun}
          onCreated={onCreated}
          onActivity={onActivity}
          onSettled={settled}
        />
      </div>
      {shell}
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
  onActivity: NonNullable<ChatProps["onActivity"]>;
  onSelectSlot: (slot: string | null) => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const readable = conversation.readable || disclosed;
  const { acts, selected, settled, shell } = useThreadActs({
    agent,
    conversationId: conversation.id,
    enabled: readable,
    slot,
    onSelectSlot,
  });
  return (
    <Pane>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header
          crumb={crumb}
          title={subject(conversation, viewer)}
          note={<AudienceMark entry={conversation} />}
          acts={
            <>
              <SurfaceMark conversation={conversation} />
              {acts}
            </>
          }
          pinned
        />
        {readable ? (
          conversation.commentable ? (
            <Chat
              agent={agent}
              member={member}
              conversationId={conversation.id}
              onActivity={onActivity}
              onSettled={settled}
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
      {shell}
      {readable && slot ? (
        <ConversationSlot
          agent={agent}
          conversationId={conversation.id}
          slot={slot}
          summary={selected}
          onClose={() => onSelectSlot(null)}
        />
      ) : null}
    </Pane>
  );
}
