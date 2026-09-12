import { useEffect, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ActionControls } from "@/kernel/action";
import { MessageLog, OpenedAtTheFoot, TranscriptScroll } from "@/kernel/messages";
import { Notice, Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { postAction } from "@/lib/api";
import {
  origin as surfaceOrigin,
  slackLink,
  subject,
  useViewer,
} from "@/lib/audience";
import { useEarlierMessages, type EarlierMessages } from "@/lib/earlier";
import { useRoute } from "@/lib/router";
import type { ActionView, Agent, Conversation, Message, Transcript } from "@/lib/types";

function Back({ onBack }: { onBack: () => void }) {
  return (
    <div className="mb-lg">
      <Button variant="row" onClick={onBack}>
        All conversations
      </Button>
    </div>
  );
}

/** The acknowledgement that opens one member's private conversation to an admin, wherever that
 *  content is read: the transcript itself, and the automations reporting into it, which the same
 *  disclosure opens. It names whose words they are and what opening records, and reads nothing
 *  until the act is taken. */
export function DiscloseBand({
  agentId,
  conversationId,
  owner,
  title,
  onOpened,
}: {
  agentId: string;
  conversationId: string;
  owner: string;
  title: ReactNode;
  onOpened: () => void;
}) {
  const live = useRef(true);
  const acts = usePanelRead<{ actions: ActionView[] }>("/actions/conversation/" + conversationId);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  return (
    <Section title={title}>
      <p className="m-0 max-w-hint">
        This conversation is private to {owner} and may contain private information. Opening it
        records your email, theirs, and the time.
      </p>
      <Panel state={acts} loading={() => null} failed={(message) => <Notice>{message}</Notice>}>
        {({ actions }) => (
          <ActionControls
            views={actions}
            post={(view, input) => postAction(agentId, view.call, input)}
            onApplied={() => {
              if (live.current) onOpened();
            }}
          />
        )}
      </Panel>
    </Section>
  );
}

export function Disclose({
  agent,
  conversation,
  onBack,
  onOpened,
}: {
  agent: Agent;
  conversation: Conversation;
  onBack?: () => void;
  onOpened: () => void;
}) {
  const viewer = useViewer();
  return (
    <>
      {onBack ? <Back onBack={onBack} /> : null}
      <DiscloseBand
        agentId={agent.id}
        conversationId={conversation.id}
        owner={conversation.member_email || "another member"}
        title={conversationTitle(conversation, viewer)}
        onOpened={onOpened}
      />
    </>
  );
}

function origin(conversation: Conversation): string {
  return conversation.agent ? agentName(conversation.agent.name) : surfaceOrigin(conversation);
}

export function conversationTitle(conversation: Conversation, viewer: string | null): ReactNode {
  const parts: ReactNode[] = [...originParts(conversation), subject(conversation, viewer)];
  return parts.flatMap((part, index) => (index ? [" · ", part] : part));
}

function wayOut(conversation: Conversation): { href: string; channel: string } | null {
  const href = slackLink(conversation.surface, conversation.source);
  return href === null ? null : { href, channel: surfaceOrigin(conversation) };
}

function originParts(conversation: Conversation): ReactNode[] {
  const out = wayOut(conversation);
  const stated = origin(conversation);
  if (out === null) return [stated];
  const away = <WayOut key="way-out" out={out} />;
  return out.channel === stated ? [away] : [stated, away];
}

/** On a listing row the link is the row's second target: `rowControl` hands a press that lands on it to
 *  the link, so leaving for Slack never rides along with opening the transcript. */
function WayOut({ out }: { out: { href: string; channel: string } }) {
  return (
    <a
      href={out.href}
      target="_blank"
      rel="noopener noreferrer"
      className="whitespace-nowrap text-inherit no-underline hover:underline focus-visible:underline"
    >
      {out.channel} <span className="text-ink-soft">↗</span>
    </a>
  );
}

function unreadable(message: string): ReactNode {
  return (
    <PanelEmpty>
      {message.startsWith("Error 404") ? "This conversation is not shared with you." : message}
    </PanelEmpty>
  );
}

export function ConversationTranscript({
  title,
  messages,
  earlier,
}: {
  title?: ReactNode;
  messages: Message[];
  earlier?: EarlierMessages;
}) {
  const route = useRoute();
  return (
    <Section title={title}>
      {messages.length ? (
        <TranscriptScroll>
          <OpenedAtTheFoot>
            <MessageLog
              messages={messages}
              earlier={earlier}
              report={route.kind === "chat" ? (route.report ?? null) : null}
            />
          </OpenedAtTheFoot>
        </TranscriptScroll>
      ) : (
        <PanelBlank body="No messages in this conversation yet." />
      )}
    </Section>
  );
}

/** One conversation's transcript, read from the agent's transcript route, with a way back where
 * the pane above has none. */
export function ConversationDetail({
  agent,
  conversation,
  onBack,
  headed = false,
}: {
  agent: Agent;
  conversation: Conversation;
  onBack?: () => void;
  headed?: boolean;
}) {
  const path = "/agents/" + agent.id + "/conversations/" + conversation.id;
  const state = usePanelRead<Transcript>(path + "/transcript");
  const earlier = useEarlierMessages(
    path + "/transcript",
    state.phase === "ready" ? (state.payload.earlier_cursor ?? null) : null,
  );
  const viewer = useViewer();

  return (
    <>
      {onBack ? <Back onBack={onBack} /> : null}
      <Panel state={state} failed={unreadable}>
        {(payload) => (
          <ConversationTranscript
            title={headed ? undefined : conversationTitle(conversation, viewer)}
            messages={payload.messages}
            earlier={earlier}
          />
        )}
      </Panel>
    </>
  );
}
