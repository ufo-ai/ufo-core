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
import { DataTable } from "@/kernel/table";
import { BASE, postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { conversationSlotHash } from "@/lib/route";
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
): { turn: Turn; depth: number; first: boolean }[] {
  const children = new Map<string | null, Turn[]>();
  for (const turn of spawned) {
    const siblings = children.get(turn.parent_turn_id) ?? [];
    siblings.push(turn);
    children.set(turn.parent_turn_id, siblings);
  }
  const rows: { turn: Turn; depth: number; first: boolean }[] = [];
  const placed = new Set<string>();
  const conversations = new Set<string>();
  const walk = (turn: Turn, depth: number) => {
    const first = !conversations.has(turn.conversation_id);
    rows.push({ turn, depth, first });
    conversations.add(turn.conversation_id);
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

/** One conversation names itself the same way on every screen that opens it — the surface it came
 *  in on, then whose it is. */
function title(conversation: Conversation): string {
  return conversation.surface + " · " + who(conversation);
}

function matches(entry: Conversation, query: string): boolean {
  return (who(entry) + " " + entry.surface).toLowerCase().includes(query.toLowerCase());
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
      {(payload) => (
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
          <DataTable
            columns={["Member", "Surface", "Turns", "Last Activity", ""]}
            rows={payload.conversations.filter((entry) => matches(entry, query))}
            rowKey={(entry) => entry.id}
            empty={"No conversations with " + agent.name + " yet."}
            note={query ? "No conversation matches this search." : undefined}
          >
            {(entry) => (
              <>
                <Td>{who(entry)}</Td>
                <Td>{entry.surface}</Td>
                <Td>{String(entry.turn_count)}</Td>
                <Td>{day(entry.last_turn_at) || day(entry.created_at)}</Td>
                <Td>
                  {entry.readable ? (
                    <Button variant="row" onClick={() => setOpened(entry)}>
                      Open
                    </Button>
                  ) : entry.disclosable ? (
                    <Button variant="row" onClick={() => setDisclosing(entry)}>
                      Open as admin
                    </Button>
                  ) : (
                    <span className="opacity-(--muted-soft)">not shared with you</span>
                  )}
                </Td>
              </>
            )}
          </DataTable>
        </Section>
      )}
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
                  <TurnLine
                    key={entry.turn.id}
                    turn={entry.turn}
                    depth={entry.depth}
                    showChanges={entry.first}
                    rootConversationId={
                      entry.turn.conversation_id === conversation.id ? undefined : conversation.id
                    }
                  />
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

/** What a turn is called on the line that states it and on the link that leaves it. A detail can
 *  merge several subagent conversations, so "Changes" alone names none of them. */
function named(turn: Turn): string {
  return turn.subagent_profile ? "subagent " + turn.subagent_profile : "turn " + turn.seq;
}

export function TurnLine({
  turn,
  depth,
  showChanges,
  rootConversationId,
}: {
  turn: Turn;
  depth: number;
  showChanges: boolean;
  rootConversationId?: string;
}) {
  const answer = turn.outcome || turn.error_class;
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-2xs",
        depth && "border-l-(length:--marker-width) border-edge-strong pl-md",
      )}
      style={{ marginLeft: "calc(var(--spacing-2xl) * " + depth + ")" }}
    >
      <div className="flex gap-md font-mono text-mono">
        <span>{named(turn) + " · " + turn.status + " · " + (day(turn.created_at) || "")}</span>
        {showChanges ? (
          <a
            href={conversationSlotHash(
              turn.agent_id,
              turn.conversation_id,
              "changes",
              rootConversationId,
            )}
            aria-label={"Changes from " + named(turn)}
          >
            Changes
          </a>
        ) : null}
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
