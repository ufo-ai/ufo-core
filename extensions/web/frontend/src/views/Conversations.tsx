import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { MessageLog, OpenedAtTheFoot, TranscriptScroll } from "@/kernel/messages";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelBlank,
  PanelEmpty,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import {
  audienceLabel,
  origin as surfaceOrigin,
  ownerLabel,
  slackLink,
  speakerName,
  useViewer,
} from "@/lib/audience";
import { useEarlierMessages, type EarlierMessages } from "@/lib/earlier";
import type { Agent, Conversation, Message, Transcript } from "@/lib/types";

/** The one way back out of a conversation, and the only thing above the section that names it. */
function Back({ onBack }: { onBack: () => void }) {
  return (
    <div className="mb-lg">
      <Button variant="row" onClick={onBack}>
        All conversations
      </Button>
    </div>
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
  /** Absent where the pane above already leads back out — a header naming the agent is one, and a
   *  way out under it would be a second. */
  onBack?: () => void;
  onOpened: () => void;
}) {
  const [outcome, setOutcome] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const viewer = useViewer();
  const owner = conversation.member_email || "another member";

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  async function acknowledge() {
    setBusy(true);
    const submitted = await postIntent(agent.id, {
      verb: "read",
      kind: "transcript",
      conversation_id: conversation.id,
    });
    if (!live.current) return;
    setBusy(false);
    if (!submitted.applied) {
      setOutcome(outcomeNotice(submitted));
      return;
    }
    onOpened();
  }

  return (
    <>
      {onBack ? <Back onBack={onBack} /> : null}
      <Section title={conversationTitle(conversation, viewer)}>
        <p className="m-0 max-w-hint">
          This conversation is private to {owner} and may contain private information. Opening it
          records your email, theirs, and the time.
        </p>
        <div>
          <Button variant="send" busy={busy} onClick={acknowledge}>
            Open transcript
          </Button>
        </div>
        <OutcomeNotice state={outcome} />
      </Section>
    </>
  );
}

/** Who a conversation belongs to, as a member reads it: `You` for the viewer's own, another
 *  member's name before their address, or — where no member's name is on it — who may read it,
 *  which is what a row with no owner is. */
function who(
  entry: {
    member_email: string | null;
    audience: string;
    surface_label?: string | null;
    speakers?: string[];
  },
  viewer: string | null,
): string {
  if (entry.member_email === null) return audienceLabel(entry, viewer);
  const owned = ownerLabel(entry.member_email, viewer);
  if (owned === "You") return owned;
  const sender = entry.speakers?.find(Boolean);
  return sender ? speakerName(sender) : owned;
}

/** What a conversation is called: the name the titling job wrote for it — the same string the rail
 *  labels a chat with, so an index row and a rail row never name one conversation two ways — else
 *  whose it is, which is all a row the member may not read has to state. */
export function subject(conversation: Conversation, viewer: string | null): string {
  return conversation.description || who(conversation, viewer);
}

/** Where a conversation came from, in the one slot a row and a heading each keep for it: the agent
 *  that ran it where the read spans every agent, else the surface it came in on. One fact, and it
 *  is the one the pane the member is standing in does not already state. */
function origin(conversation: Conversation): string {
  return conversation.agent ? agentName(conversation.agent.name) : surfaceOrigin(conversation);
}

/** One conversation names itself the same way on every screen that opens it — where it came from,
 *  then what it is about — and the words naming where it came from are the way back out where one
 *  leads there (`originParts`). */
export function conversationTitle(conversation: Conversation, viewer: string | null): ReactNode {
  const parts: ReactNode[] = [...originParts(conversation), subject(conversation, viewer)];
  return parts.flatMap((part, index) => (index ? [" · ", part] : part));
}

/** Where a conversation leads back out to, and what that way out says — null where it draws none, so
 *  the words that name where it came in know whether they lead anywhere. */
function wayOut(conversation: Conversation): { href: string; channel: string } | null {
  const href = slackLink(conversation.surface, conversation.source);
  return href === null ? null : { href, channel: surfaceOrigin(conversation) };
}

/** Where a conversation came from, as the parts a heading and a row's meta line each state it in:
 *  the words `origin` names, and the way out where one is drawn. Where those words name the channel
 *  the way out lands in they are that way out, so the origin is stated once and nothing is drawn
 *  beside them; where they name the agent that ran the conversation the way out follows them as its
 *  own part, because the agent and the channel it came in on are two facts. Where no way out is
 *  drawn the words stand alone. */
function originParts(conversation: Conversation): ReactNode[] {
  const out = wayOut(conversation);
  const stated = origin(conversation);
  if (out === null) return [stated];
  const away = <WayOut key="way-out" out={out} />;
  return out.channel === stated ? [away] : [stated, away];
}

/** The way out of a conversation Slack holds, drawn the same wherever it appears: the channel it is
 *  in, named the way Slack names it, on the permalink of the message it opened with. The channel
 *  name is the whole label — a member reading `#ops-warehouse` knows both that the link leaves for
 *  Slack and which room it lands in, where `Open in Slack` says only the half they already knew —
 *  and the arrow is the portal's one glyph for an act that leaves it. A conversation the surface
 *  named no channel for reads as the surface itself, because a way out still has to say where it
 *  goes.
 *
 *  It is the words that name where the conversation came in rather than a control beside them, so a
 *  heading and a row state where a conversation is happening once and the row's title gives up no
 *  width for it. Those words are drawn the way the portal draws every other act that leaves it: they
 *  keep the colour and weight of the line they sit in and carry no resting underline, so the way out
 *  never outshouts the title beside it nor breaks a muted meta line. The underline arrives on hover
 *  and focus where the member is already asking what the words do, and it is what a way out inside a
 *  line answers with: `Source ↗` brightens from muted, which a word on a line the meta already mutes
 *  cannot do without first drawing itself fainter than the words beside it. The arrow is muted against
 *  them, because it marks the act and does not name it. On a listing row the link is the row's second
 *  target — `rowControl` hands a press that lands on it to the link, so leaving for Slack never rides
 *  along with opening the transcript. */
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

/** What a content read that refused says, wherever one is drawn: a conversation this member may
 *  not read is not a status code. */
function unreadable(message: string): ReactNode {
  return (
    <PanelEmpty>
      {message.startsWith("Error 404") ? "This conversation is not shared with you." : message}
    </PanelEmpty>
  );
}

/** One conversation read rather than continued: the same message log the chat draws, headed the
 *  way the row that opened it named it, and no composer under it. It takes no pane of its own — the
 *  page this section stands in is what scrolls, and a transcript that scrolled itself inside it
 *  would put a second bar beside the same words. The section carries no act — a transcript has no
 *  diff state to read, so a Changes link here would be drawn over conversations that changed no
 *  file, and the one way out of the conversation is the channel its heading names rather than a
 *  control beside that heading. */
export function ConversationTranscript({
  title,
  messages,
  earlier,
}: {
  title?: ReactNode;
  messages: Message[];
  earlier?: EarlierMessages;
}) {
  return (
    <Section title={title}>
      {messages.length ? (
        <TranscriptScroll>
          <OpenedAtTheFoot>
            <MessageLog messages={messages} earlier={earlier} />
          </OpenedAtTheFoot>
        </TranscriptScroll>
      ) : (
        <PanelBlank body="No messages in this conversation yet." />
      )}
    </Section>
  );
}

export function ConversationDetail({
  agent,
  conversation,
  onBack,
  headed = false,
}: {
  agent: Agent;
  conversation: Conversation;
  /** Absent where the pane above already leads back out — a header naming the agent is one, and a
   *  way out under it would be a second. */
  onBack?: () => void;
  /** True where the pane's own header already states what the conversation is called, so the
   *  transcript draws no heading of its own — the name is stated once. */
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
