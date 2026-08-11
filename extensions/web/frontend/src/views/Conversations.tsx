import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Td } from "@/components/ui/table";
import { day } from "@/lib/moments";
import { formatSize } from "@/views/Chat";
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
import { DataTable } from "@/kernel/table";
import { BASE, postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Agent, Conversation } from "@/lib/types";

const MAX_PREVIEW_BYTES = 256 * 1024;

export type Turn = {
  id: string;
  agent_id: string;
  conversation_id: string;
  seq: number;
  status: string;
  created_at: string;
  inbound: string;
  outcome: string | null;
  error_class: string | null;
  subagent_profile: string | null;
  parent_turn_id: string | null;
};

type WorkspaceFile = { path: string; size_bytes: number; modified_at: string };

export function turnTree(
  turns: Turn[],
  spawned: Turn[],
): { turn: Turn; depth: number }[] {
  const children = new Map<string | null, Turn[]>();
  for (const turn of spawned) {
    const siblings = children.get(turn.parent_turn_id) ?? [];
    siblings.push(turn);
    children.set(turn.parent_turn_id, siblings);
  }
  const rows: { turn: Turn; depth: number }[] = [];
  const placed = new Set<string>();
  const walk = (turn: Turn, depth: number) => {
    rows.push({ turn, depth });
    placed.add(turn.id);
    for (const child of children.get(turn.id) ?? []) walk(child, depth + 1);
  };
  for (const turn of turns) walk(turn, 0);
  for (const turn of spawned) if (!placed.has(turn.id)) walk(turn, 0);
  return rows;
}

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
      <Section title={title(conversation)}>
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

/** Who a conversation belongs to, as a member reads it: the member's own address, or — for the
 *  two audiences that name no member — what it is, since a room's content nobody reads here and
 *  a workspace-shared conversation everybody does. The short id distinguishes two of a kind; the
 *  queue key is never a member-facing name. */
export function who(entry: { member_email: string | null; readable: boolean; id: string }): string {
  if (entry.member_email) return entry.member_email;
  const kind = entry.readable ? "Shared" : "Channel or room";
  return kind + " · " + entry.id.slice(0, 8);
}

/** What a conversation is called: the words it opened with — the same cut the rail labels a chat
 *  with, so an index row and a rail row never name one conversation two ways — else whose it is,
 *  which is all a row the member may not read has to state. */
function subject(conversation: Conversation): string {
  return conversation.description || who(conversation);
}

/** One conversation names itself the same way on every screen that opens it — the surface it came
 *  in on, then what it is about. A row states its surface in its own meta line; a heading standing
 *  alone above a transcript has nowhere else to put it. */
function title(conversation: Conversation): string {
  return conversation.surface + " · " + subject(conversation);
}

/** What a member may do with a row they cannot simply open. `Private` reads on another member's
 *  conversation an admin may disclose to themselves: pressing it reaches the acknowledgement,
 *  never the transcript, so the record of who read whose is still written by an act. */
function standing(entry: Conversation): string {
  if (entry.readable) return "";
  return entry.disclosable ? "Private" : "Not shared with you";
}

function turns(count: number): string {
  return count === 1 ? "1 turn" : count + " turns";
}

function matches(entry: Conversation, query: string): boolean {
  const stated = [subject(entry), who(entry), entry.surface, ...entry.speakers].join(" ");
  return stated.toLowerCase().includes(query.toLowerCase());
}

export function Conversations({ agent }: { agent: Agent }) {
  const [opened, setOpened] = useState<Conversation | null>(null);
  const [disclosing, setDisclosing] = useState<Conversation | null>(null);
  const [query, setQuery] = useState("");
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
      {(payload) => {
        const shown = payload.conversations.filter((entry) => matches(entry, query));
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
                <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
              </>
            }
          >
            {shown.length ? (
              <RowLines
                rows={shown}
                rowKey={(entry) => entry.id}
                primary={subject}
                meta={(entry) => [
                  entry.speakers.join(", "),
                  entry.surface,
                  turns(entry.turn_count),
                  standing(entry),
                ]}
                when={(entry) => day(entry.last_turn_at) || day(entry.created_at)}
                open={(entry) =>
                  entry.readable
                    ? () => setOpened(entry)
                    : entry.disclosable
                      ? () => setDisclosing(entry)
                      : null
                }
              />
            ) : (
              <PanelBlank
                body={
                  query
                    ? "No conversation matches this search."
                    : "No conversations with " + agent.name + " yet."
                }
              />
            )}
          </Section>
        );
      }}
    </Panel>
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
  const state = usePanelRead<{ turns: Turn[]; subagent_turns: Turn[] }>(path + "/turns");

  return (
    <>
      <Back onBack={onBack} />
      <Section title={title(conversation)}>
        <Panel
          state={state}
          failed={(message) => (
            <PanelEmpty>
              {message.startsWith("Error 404")
                ? "This conversation is not shared with you."
                : message}
            </PanelEmpty>
          )}
        >
          {(payload) =>
            payload.turns.length || payload.subagent_turns.length ? (
              <div className="flex flex-col gap-lg">
                {turnTree(payload.turns, payload.subagent_turns).map((entry) => (
                  <TurnLine key={entry.turn.id} turn={entry.turn} depth={entry.depth} />
                ))}
              </div>
            ) : (
              <PanelBlank body="No turns in this conversation yet." />
            )
          }
        </Panel>
      </Section>
      <ConversationFiles base={path} />
    </>
  );
}

export function TurnLine({ turn, depth }: { turn: Turn; depth: number }) {
  const answer = turn.outcome || turn.error_class;
  const name = turn.subagent_profile ? "subagent " + turn.subagent_profile : "turn " + turn.seq;
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-2xs",
        depth && "border-l-(length:--marker-width) border-edge-strong pl-md",
      )}
      style={{ marginLeft: "calc(var(--spacing-2xl) * " + depth + ")" }}
    >
      <div className="flex gap-md font-mono text-mono">
        <span>{name + " · " + turn.status + " · " + (day(turn.created_at) || "")}</span>
      </div>
      <div className="max-w-bubble self-end whitespace-pre-wrap wrap-anywhere rounded-bubble bg-fill px-lg py-sm">
        {turn.inbound}
      </div>
      {answer ? (
        <div className="max-w-bubble self-start whitespace-pre-wrap wrap-anywhere rounded-bubble bg-fill-subtle px-lg py-sm">
          {answer}
        </div>
      ) : null}
    </div>
  );
}

function ConversationFiles({ base }: { base: string }) {
  const state = usePanelRead<{ files: WorkspaceFile[] }>(base + "/files");
  const [preview, setPreview] = useState<string | null>(null);

  async function view(entry: WorkspaceFile) {
    setPreview("Reading " + entry.path + "…");
    try {
      const res = await fetch(BASE + base + "/files/" + entry.path, {
        credentials: "same-origin",
      });
      setPreview(res.ok ? await res.text() : "Error " + res.status + " — reload to retry.");
    } catch {
      setPreview("Network error — try again.");
    }
  }

  return (
    <Panel state={state}>
      {(payload) => (
        <Section title="Workspace files">
          <DataTable
            columns={["File", "Size", "Modified", ""]}
            rows={payload.files}
            rowKey={(entry) => entry.path}
            empty="No files in this conversation's workspace."
          >
            {(entry) => (
              <>
                <Td>{entry.path}</Td>
                <Td>{formatSize(entry.size_bytes)}</Td>
                <Td>{day(entry.modified_at)}</Td>
                <Td>
                  <div className="flex flex-wrap items-baseline gap-xs">
                    {entry.size_bytes <= MAX_PREVIEW_BYTES ? (
                      <Button variant="row" onClick={() => view(entry)}>
                        View
                      </Button>
                    ) : null}
                    <a href={BASE + base + "/files/" + entry.path}>Download</a>
                  </div>
                </Td>
              </>
            )}
          </DataTable>
          {preview === null ? null : (
            <pre className="overflow-x-auto whitespace-pre-wrap wrap-anywhere rounded-panel bg-fill-subtle p-lg font-mono text-mono">
              {preview}
            </pre>
          )}
        </Section>
      )}
    </Panel>
  );
}
