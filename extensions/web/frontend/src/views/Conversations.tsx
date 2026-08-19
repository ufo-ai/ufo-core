import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Moment } from "@/lib/moments";
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
import { RowLines } from "@/kernel/rows";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import {
  audienceLabel,
  isMemberAudience,
  origin as surfaceOrigin,
  ownerLabel,
  slackLink,
  speakerName,
  useViewer,
} from "@/lib/audience";
import { useEarlierMessages, type EarlierMessages } from "@/lib/earlier";
import type { WorkspacePlace } from "@/lib/route";
import type { Agent, Conversation, Message, Transcript } from "@/lib/types";

/** The one way back out of a conversation, and the only thing above the section that names it. */
export function Back({ onBack }: { onBack: () => void }) {
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
export function who(
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

/** The meta line: whose the row is, where it came in, who spoke, how busy it is, and who may read
 *  it. The viewer's own private row carries no audience label — the exception is labelled, never
 *  the default — and a label the origin or the subject already states does not repeat. Another
 *  member's private row states owner and audience as the one part `Private to <email>`. */
export function metaParts(entry: Conversation, viewer: string | null): ReactNode[] {
  const mine = entry.member_email !== null && entry.member_email === viewer;
  const theirs = isMemberAudience(entry.audience) && !mine;
  const shown = subject(entry, viewer);
  const reach = mine ? "" : audienceLabel(entry, viewer);
  const named = entry.member_email === null || theirs ? "" : ownerLabel(entry.member_email, viewer);
  const parts = [
    named,
    ...originParts(entry),
    entry.speakers.map(speakerName).join(", "),
    turns(entry.turn_count),
    reach === origin(entry) ? "" : reach,
  ];
  return parts.map((part) => (part === shown ? "" : part));
}

function turns(count: number): string {
  return count === 1 ? "1 turn" : count + " turns";
}

/** The moment a row carries, named by what it is: the last turn where the conversation holds one,
 *  else the day it was opened. The list stands in last-activity order, so a bare date beside a row
 *  the member is scanning for what moved most recently has to say which of the two it shows — a
 *  conversation nobody has spoken in yet is the one row showing a creation date, and it reads as
 *  one rather than as activity that stopped there. */
export function moment(entry: Conversation) {
  return entry.last_turn_at ? (
    <>
      Last turn <Moment at={entry.last_turn_at} />
    </>
  ) : (
    <>
      Created <Moment at={entry.created_at} />
    </>
  );
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

/** Every screen that lists conversations draws this one section, so a row reads the same wherever
 *  the member met it: the search on the bar, one
 *  row line per conversation, and the row itself as the control that opens it. The
 *  rows decide the rest: one carrying an agent states that agent where one read in a single
 *  agent's namespace states the surface, and a row nobody may open says which of the two it is. The
 *  channel a row's meta line already names is the way back to Slack, so the member chooses between
 *  reading the conversation here and answering it where it is happening without the row's title
 *  giving up any width for a second control. */
export function ConversationList({
  rows,
  blank,
  typed,
  searched,
  onType,
  onSearch,
  onOpen,
  onDisclose,
}: {
  rows: Conversation[];
  blank: string;
  typed: string;
  searched: string;
  onType: (typed: string) => void;
  onSearch: () => void;
  onOpen: (conversation: Conversation) => void;
  onDisclose?: (conversation: Conversation) => void;
}) {
  const viewer = useViewer();
  return (
    <Section
      title="Conversations"
      bar={
        <Search
          label="Search"
          placeholder="Search"
          className="w-(--container-control-row)"
          value={typed}
          onChange={(event) => onType(event.target.value)}
          onSubmit={onSearch}
        />
      }
    >
      {rows.length ? (
        <RowLines
          rows={rows}
          rowKey={(entry) => entry.id}
          primary={(entry) => subject(entry, viewer)}
          meta={(entry) => metaParts(entry, viewer)}
          when={moment}
          open={(entry) =>
            entry.readable
              ? () => onOpen(entry)
              : entry.disclosable && onDisclose
                ? () => onDisclose(entry)
                : null
          }
        />
      ) : (
        <PanelBlank body={searched ? "No conversation matches this search." : blank} />
      )}
    </Section>
  );
}

export function Conversations({
  agent,
  place,
  onPlace,
}: {
  agent: Agent;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace) => void;
}) {
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [searched, setSearched] = useState("");
  const state = usePanelRead<{ conversations: Conversation[] }>(
    "/agents/" + agent.id + "/conversations" + (searched ? "?q=" + encodeURIComponent(searched) : ""),
  );

  const opened =
    state.phase === "ready" && place.open
      ? state.payload.conversations.find((entry) => entry.id === place.open)
      : undefined;
  if (opened?.readable || opened && disclosed === opened.id) {
    return (
      <ConversationDetail
        agent={agent}
        conversation={opened}
        onBack={() => onPlace({ open: undefined })}
      />
    );
  }
  if (opened) {
    return (
      <Disclose
        key={opened.id}
        agent={agent}
        conversation={opened}
        onBack={() => onPlace({ open: undefined })}
        onOpened={() => setDisclosed(opened.id)}
      />
    );
  }
  return (
    <Panel state={state}>
      {(payload) => (
        <ConversationList
          rows={payload.conversations}
          blank={"No conversation with " + agentName(agent.name) + " yet."}
          typed={typed}
          searched={searched}
          onType={setTyped}
          onSearch={() => setSearched(typed)}
          onOpen={(conversation) => onPlace({ open: conversation.id })}
          onDisclose={(conversation) => onPlace({ open: conversation.id })}
        />
      )}
    </Panel>
  );
}

/** An app's conversations, read the way chat is: the threads in a column on the left, the one
 *  picked open beside them. The column is the index, so a row press replaces what stands to its
 *  right rather than covering the list. */
export function ConversationsPane({
  agent,
  place,
  onPlace,
}: {
  agent: Agent;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace) => void;
}) {
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [searched, setSearched] = useState("");
  const viewer = useViewer();
  const state = usePanelRead<{ conversations: Conversation[] }>(
    "/agents/" + agent.id + "/conversations" + (searched ? "?q=" + encodeURIComponent(searched) : ""),
  );
  const rows = state.phase === "ready" ? state.payload.conversations : [];
  const opened = place.open ? rows.find((entry) => entry.id === place.open) : undefined;
  const readable = opened && (opened.readable || disclosed === opened.id);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[var(--container-threads)_1fr] max-narrow:grid-cols-1">
      <nav
        aria-label="Conversations"
        className="flex min-h-0 flex-col gap-md overflow-y-auto border-r border-edge p-2xl max-narrow:border-r-0"
      >
        <Search
          label="Search"
          placeholder="Search"
          className="w-full shrink-0"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          onSubmit={() => setSearched(typed.trim())}
        />
        {/* A read that failed is stated where the rows would stand. The column carries the whole
            answer to what conversations there are, so an empty one is read as "none" — and a
            projection that refused must never be read as an app nobody has spoken to. */}
        {state.phase === "failed" ? (
          <p className="m-0 text-label text-ink-soft">{state.message}</p>
        ) : (
          <>
            <RowLines
              rows={rows}
              rowKey={(entry) => entry.id}
              primary={(entry) => subject(entry, viewer)}
              meta={(entry) => metaParts(entry, viewer)}
              when={moment}
              open={(entry) =>
                entry.readable || entry.disclosable ? () => onPlace({ open: entry.id }) : null
              }
            />
            {state.phase === "ready" && !rows.length ? (
              <p className="m-0 text-label text-ink-soft">
                {"No conversation with " + agentName(agent.name) + " yet."}
              </p>
            ) : null}
          </>
        )}
      </nav>
      <div className="flex min-h-0 min-w-0 flex-col overflow-y-auto p-2xl">
        {!opened ? (
          <p className="m-auto max-w-empty text-center text-ink-soft">
            Pick a conversation to read it.
          </p>
        ) : readable ? (
          <ConversationDetail agent={agent} conversation={opened} />
        ) : (
          <Disclose
            key={opened.id}
            agent={agent}
            conversation={opened}
            onOpened={() => setDisclosed(opened.id)}
          />
        )}
      </div>
    </div>
  );
}

/** What a content read that refused says, wherever one is drawn: a conversation this member may
 *  not read is not a status code. */
export function unreadable(message: string): ReactNode {
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
  onOpenArtifacts,
}: {
  title?: ReactNode;
  messages: Message[];
  earlier?: EarlierMessages;
  onOpenArtifacts?: () => void;
}) {
  return (
    <Section title={title}>
      {messages.length ? (
        <TranscriptScroll>
          <OpenedAtTheFoot>
            <MessageLog messages={messages} earlier={earlier} onOpenArtifacts={onOpenArtifacts} />
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
  onOpenArtifacts,
}: {
  agent: Agent;
  conversation: Conversation;
  /** Absent where the pane above already leads back out — a header naming the agent is one, and a
   *  way out under it would be a second. */
  onBack?: () => void;
  /** True where the pane's own header already states what the conversation is called, so the
   *  transcript draws no heading of its own — the name is stated once. */
  headed?: boolean;
  onOpenArtifacts?: () => void;
}) {
  const path = "/agents/" + agent.id + "/conversations/" + conversation.id;
  const state = usePanelRead<Transcript>(path + "/transcript");
  const earlier = useEarlierMessages(
    path + "/transcript",
    state.phase === "ready" ? (state.payload.earlier ?? 0) : 0,
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
            onOpenArtifacts={onOpenArtifacts}
          />
        )}
      </Panel>
    </>
  );
}
