import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { day } from "@/lib/moments";
import { MessageLog } from "@/kernel/messages";
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
import { postIntent } from "@/lib/api";
import {
  audienceLabel,
  isMemberAudience,
  origin as surfaceOrigin,
  ownerLabel,
  useViewer,
} from "@/lib/audience";
import type { Agent, Conversation, Message } from "@/lib/types";

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
  onBack: () => void;
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
      <Back onBack={onBack} />
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
 *  member's address, or — where no member's name is on it — who may read it, which is what a row
 *  with no owner is. */
export function who(
  entry: { member_email: string | null; audience: string; surface_label?: string | null },
  viewer: string | null,
): string {
  if (entry.member_email === null) return audienceLabel(entry, viewer);
  return ownerLabel(entry.member_email, viewer);
}

/** What a conversation is called: the words it opened with — the same cut the rail labels a chat
 *  with, so an index row and a rail row never name one conversation two ways — else whose it is,
 *  which is all a row the member may not read has to state. */
function subject(conversation: Conversation, viewer: string | null): string {
  return conversation.description || who(conversation, viewer);
}

/** Where a conversation came from, in the one slot a row and a heading each keep for it: the agent
 *  that ran it where the read spans every agent, else the surface it came in on. One fact, and it
 *  is the one the pane the member is standing in does not already state — a subagent's page names
 *  the profile and needs the agent, an agent's page names the agent and needs the surface. */
function origin(conversation: Conversation): string {
  return conversation.agent ? conversation.agent.name : surfaceOrigin(conversation);
}

/** One conversation names itself the same way on every screen that opens it — where it came from,
 *  then what it is about. A row states its origin in its own meta line; a heading standing alone
 *  above a transcript has nowhere else to put it. */
export function conversationTitle(conversation: Conversation, viewer: string | null): string {
  return origin(conversation) + " · " + subject(conversation, viewer);
}

/** The meta line: whose the row is, where it came in, who spoke, how busy it is, and who may read
 *  it. The viewer's own private row carries no audience label — the exception is labelled, never
 *  the default — and a label the origin or the subject already states does not repeat. Another
 *  member's private row states owner and audience as the one part `Private to <email>`. */
function metaParts(entry: Conversation, viewer: string | null): string[] {
  const mine = entry.member_email !== null && entry.member_email === viewer;
  const theirs = isMemberAudience(entry.audience) && !mine;
  const shown = subject(entry, viewer);
  const reach = mine ? "" : audienceLabel(entry, viewer);
  const named = entry.member_email === null || theirs ? "" : ownerLabel(entry.member_email, viewer);
  const parts = [
    named,
    origin(entry),
    entry.speakers.join(", "),
    turns(entry.turn_count),
    reach === origin(entry) ? "" : reach,
  ];
  return parts.map((part) => (part === shown ? "" : part));
}

function turns(count: number): string {
  return count === 1 ? "1 turn" : count + " turns";
}

function matches(entry: Conversation, query: string, viewer: string | null): boolean {
  const stated = [subject(entry, viewer), who(entry, viewer), origin(entry), ...entry.speakers];
  return stated.join(" ").toLowerCase().includes(query.toLowerCase());
}

/** Every screen that lists conversations draws this one section — an agent's own and a subagent's
 *  runs alike — so a row reads the same wherever the member met it: the search and `Refresh` on
 *  the bar, one row line per conversation, and the row itself as the control that opens it. The
 *  rows decide the rest: one carrying an agent states that agent where one read in a single
 *  agent's namespace states the surface, and a row nobody may open says which of the two it is. */
export function ConversationList({
  rows,
  blank,
  onRefresh,
  onOpen,
  onDisclose,
}: {
  rows: Conversation[];
  blank: string;
  onRefresh: () => void;
  onOpen: (conversation: Conversation) => void;
  onDisclose?: (conversation: Conversation) => void;
}) {
  const [query, setQuery] = useState("");
  const viewer = useViewer();
  const shown = rows.filter((entry) => matches(entry, query, viewer));
  return (
    <Section
      title="Conversations"
      bar={
        <>
          <Input
            type="search"
            aria-label="Search"
            placeholder="Search"
            className="max-w-control-row"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button onClick={onRefresh}>Refresh</Button>
        </>
      }
    >
      {shown.length ? (
        <RowLines
          rows={shown}
          rowKey={(entry) => entry.id}
          primary={(entry) => subject(entry, viewer)}
          meta={(entry) => metaParts(entry, viewer)}
          when={(entry) => day(entry.last_turn_at) || day(entry.created_at)}
          open={(entry) =>
            entry.readable
              ? () => onOpen(entry)
              : entry.disclosable && onDisclose
                ? () => onDisclose(entry)
                : null
          }
        />
      ) : (
        <PanelBlank body={query ? "No conversation matches this search." : blank} />
      )}
    </Section>
  );
}

export function Conversations({ agent }: { agent: Agent }) {
  const [opened, setOpened] = useState<Conversation | null>(null);
  const [disclosing, setDisclosing] = useState<Conversation | null>(null);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<{ conversations: Conversation[] }>(
    "/agents/" + agent.id + "/conversations",
    reloads,
  );

  if (opened) {
    return <ConversationDetail agent={agent} conversation={opened} onBack={() => setOpened(null)} />;
  }
  if (disclosing) {
    return (
      <Disclose
        key={disclosing.id}
        agent={agent}
        conversation={disclosing}
        onBack={() => setDisclosing(null)}
        onOpened={() => {
          setOpened(disclosing);
          setDisclosing(null);
        }}
      />
    );
  }
  return (
    <Panel state={state}>
      {(payload) => (
        <ConversationList
          rows={payload.conversations}
          blank={"No conversation with " + agent.name + " yet."}
          onRefresh={() => setReloads((count) => count + 1)}
          onOpen={setOpened}
          onDisclose={setDisclosing}
        />
      )}
    </Panel>
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
 *  way the row that opened it named it, and no composer under it. The section carries no act — a
 *  transcript has no diff state to read, so a Changes link here would be drawn over conversations
 *  that changed no file. */
export function ConversationTranscript({
  conversationId,
  title,
  messages,
}: {
  conversationId: string;
  title: string;
  messages: Message[];
}) {
  return (
    <Section title={title}>
      {messages.length ? (
        <div className="flex flex-col gap-md">
          <MessageLog messages={messages} conversationId={conversationId} />
        </div>
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
}: {
  agent: Agent;
  conversation: Conversation;
  onBack: () => void;
}) {
  const path = "/agents/" + agent.id + "/conversations/" + conversation.id;
  const state = usePanelRead<{ messages: Message[] }>(path + "/transcript");
  const viewer = useViewer();

  return (
    <>
      <Back onBack={onBack} />
      <Panel state={state} failed={unreadable}>
        {(payload) => (
          <ConversationTranscript
            conversationId={conversation.id}
            title={conversationTitle(conversation, viewer)}
            messages={payload.messages}
          />
        )}
      </Panel>
    </>
  );
}
