import { IconLock, IconTerminal2, IconUsers } from "@tabler/icons-react";
import { Suspense, lazy, useCallback, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Chat, type ChatProps } from "@/views/Chat";
import { Disclose } from "@/views/Conversations";
import { COLUMN, Header, Pane } from "@/kernel/pane";
import { Loading, usePanelRead } from "@/kernel/panel";
import { postObjectAction, shellPath } from "@/lib/api";
import {
  MEMBER_SUBJECT,
  SHARED_SUBJECT,
  WEB_SURFACE,
  audienceDetail,
  audienceLabel,
  isMemberAudience,
  subject,
  useViewer,
} from "@/lib/audience";
import { AudienceMark } from "@/lib/audienceMark";
import { cn } from "@/lib/cn";
import { changeRailVisibility, settleRailVisibility } from "@/lib/railStore";
import { SurfaceMark } from "@/lib/surfaceMark";
import type { Crumb } from "@/lib/title";
import type { Conversation, ConversationAgent, Member } from "@/lib/types";
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
  /** The open conversation as its resolve carried it. A chat the member has not founded yet, and a
   *  run watched from its automation, have none, and the pane draws no title line. */
  conversation?: Conversation;
  conversationOnly?: boolean;
  slot?: string;
  onSelectSlot?: (slot: string | null) => void;
  crumb?: Crumb;
};

const WORKSPACE = "Workspace";
const PRIVATE = "Private";
type Visibility = typeof WORKSPACE | typeof PRIVATE;
const VISIBILITY_LABEL = "Visibility: ";
/** The action each choice dispatches, by name, off the conversation's own action projection. */
const VISIBILITY_ACTIONS: Record<Visibility, string> = {
  [WORKSPACE]: "share_conversation",
  [PRIVATE]: "make_conversation_private",
};

function visibilityOwned(conversation: Conversation, member: Member): boolean {
  return (
    member.id !== undefined &&
    conversation.surface === WEB_SURFACE &&
    conversation.mine &&
    (conversation.audience === SHARED_SUBJECT ||
      conversation.audience === MEMBER_SUBJECT + member.id)
  );
}

/** The audience glyph as an act: choosing the other side posts the conversation's own action as a
 *  prepared intent — the dispatch `DiscloseBand` makes — and the rail re-reads once it applied. */
function VisibilityControl({
  agentId,
  conversation,
  member,
}: {
  agentId: string;
  conversation: Conversation;
  member: Member;
}) {
  const viewer = useViewer();
  const conversationId = conversation.id;
  const current: Visibility = isMemberAudience(conversation.audience) ? PRIVATE : WORKSPACE;
  const [changing, setChanging] = useState(false);

  async function change(next: Visibility) {
    if (changing || next === current || member.id === undefined) return;
    const audience = next === PRIVATE ? MEMBER_SUBJECT + member.id : SHARED_SUBJECT;
    const memberEmail = next === PRIVATE ? member.email : null;
    if (!changeRailVisibility(conversationId, audience, memberEmail)) return;
    setChanging(true);
    const outcome = await postObjectAction(
      agentId,
      {
        kind: "conversation",
        name: conversationId,
        action: VISIBILITY_ACTIONS[next],
      },
      {},
    );
    settleRailVisibility(conversationId, outcome);
    setChanging(false);
  }

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button
              variant="quiet"
              size="icon"
              busy={changing}
              aria-label={VISIBILITY_LABEL + current}
              title={audienceDetail(conversation, viewer)}
            >
              {current === PRIVATE ? <IconLock aria-hidden /> : <IconUsers aria-hidden />}
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">{audienceLabel(conversation, viewer)}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup
          value={current}
          onValueChange={(next) => void change(next as Visibility)}
        >
          <DropdownMenuRadioItem value={WORKSPACE}>{WORKSPACE}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value={PRIVATE}>{PRIVATE}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

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
  conversation,
  focusComposer,
  readOnly,
  stops,
  focusRun,
  onCreated,
  onActivity,
  conversationOnly = false,
  slot,
  onSelectSlot,
  crumb,
}: ChatPaneProps) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const gate = conversation && !conversation.readable && !disclosed ? conversation : null;
  const { acts, selected, settled, shell } = useThreadActs({
    agent,
    conversationId,
    enabled: gate === null && !conversationOnly,
    slot,
    onSelectSlot,
  });
  return (
    <Pane>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {conversation ? (
          <Header
            crumb={crumb}
            title={subject(conversation, viewer)}
            note={
              visibilityOwned(conversation, member) ? (
                <VisibilityControl agentId={agent.id} conversation={conversation} member={member} />
              ) : (
                <AudienceMark entry={conversation} />
              )
            }
            acts={
              <>
                <SurfaceMark conversation={conversation} />
                {acts}
              </>
            }
            pinned
          />
        ) : null}
        {gate === null ? (
          <Chat
            agent={agent}
            member={member}
            conversationId={conversationId}
            focusComposer={focusComposer}
            readOnly={readOnly || (conversation !== undefined && !conversation.speakable)}
            stops={stops}
            focusRun={focusRun}
            onCreated={onCreated}
            onActivity={onActivity}
            onSettled={settled}
          />
        ) : (
          <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
            <Disclose agent={agent} conversation={gate} onOpened={() => setDisclosed(true)} />
          </div>
        )}
      </div>
      {shell}
      {gate === null && conversationId && slot ? (
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
