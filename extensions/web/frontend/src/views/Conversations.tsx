import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Table, Td, Th } from "@/components/ui/table";
import { Heading } from "@/views/Usage";
import { day } from "@/views/Tasks";
import { formatSize } from "@/views/Chat";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { BASE, postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Agent } from "@/lib/types";

const MAX_PREVIEW_BYTES = 256 * 1024;

type Conversation = {
  id: string;
  surface: string;
  queue_key: string;
  member_email: string | null;
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  readable: boolean;
  disclosable?: boolean;
};

type Turn = {
  id: string;
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

export function turnTree(turns: Turn[], spawned: Turn[]): { turn: Turn; depth: number }[] {
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

function Disclose({
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
  const [outcome, setOutcome] = useState("");
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
      setOutcome(submitted.message);
      return;
    }
    onOpened();
  }

  return (
    <div className="flex flex-col gap-md">
      <Button variant="row" onClick={onBack}>
        All conversations
      </Button>
      <h2 className="text-label m-0 opacity-(--muted-soft)">
        {conversation.surface} · {conversation.member_email || conversation.queue_key}
      </h2>
      <p className="max-w-hint">
        This conversation is private to {owner} and may contain private information. Opening it
        records your email, theirs, and the time.
      </p>
      <div>
        <Button variant="row" disabled={busy} onClick={acknowledge}>
          Open transcript
        </Button>
      </div>
      <Notice>{outcome}</Notice>
    </div>
  );
}

export function Conversations({ agent }: { agent: Agent }) {
  const [opened, setOpened] = useState<Conversation | null>(null);
  const [disclosing, setDisclosing] = useState<Conversation | null>(null);
  const state = usePanelRead<{ conversations: Conversation[] }>(
    "/agents/" + agent.id + "/conversations",
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
  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const entries = state.payload.conversations;
  if (!entries.length) return <PanelEmpty>No conversations with {agent.name} yet.</PanelEmpty>;

  return (
    <Table>
      <thead>
        <tr>
          {["conversation", "surface", "turns", "last activity", ""].map((column, index) => (
            <Th key={index}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.id}>
            <Td>{entry.member_email || entry.queue_key}</Td>
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
                <span className="font-mono text-mono">not shared with you</span>
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function ConversationDetail({
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
      <Button variant="row" onClick={onBack}>
        All conversations
      </Button>
      <Heading>
        {conversation.surface} · {conversation.member_email || conversation.queue_key}
      </Heading>
      {state.phase === "failed" ? (
        <PanelEmpty>
          {state.message.startsWith("Error 404")
            ? "This conversation is not shared with you."
            : state.message}
        </PanelEmpty>
      ) : null}
      {state.phase === "ready" ? (
        !state.payload.turns.length && !state.payload.subagent_turns.length ? (
          <PanelEmpty>No turns in this conversation yet.</PanelEmpty>
        ) : (
          <div className="my-lg flex flex-col gap-lg">
            {turnTree(state.payload.turns, state.payload.subagent_turns).map((entry) => (
              <TurnLine key={entry.turn.id} turn={entry.turn} depth={entry.depth} />
            ))}
          </div>
        )
      ) : null}
      <ConversationFiles base={path} />
    </>
  );
}

function TurnLine({ turn, depth }: { turn: Turn; depth: number }) {
  const answer = turn.outcome || turn.error_class;
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-2xs",
        depth && "border-l-(length:--marker-width) border-edge-strong pl-md",
      )}
      style={{ marginLeft: depth * 16 + "px" }}
    >
      <div className="font-mono text-mono">
        {(turn.subagent_profile ? "subagent " + turn.subagent_profile : "turn " + turn.seq) +
          " · " +
          turn.status +
          " · " +
          (day(turn.created_at) || "")}
      </div>
      <div className="max-w-bubble self-end whitespace-pre-wrap rounded-bubble bg-fill px-lg py-sm">
        {turn.inbound}
      </div>
      {answer ? (
        <div className="max-w-bubble self-start whitespace-pre-wrap rounded-bubble bg-fill-subtle px-lg py-sm">
          {answer}
        </div>
      ) : null}
    </div>
  );
}

function ConversationFiles({ base }: { base: string }) {
  const state = usePanelRead<{ files: WorkspaceFile[] }>(base + "/files");
  const [preview, setPreview] = useState<string | null>(null);

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const files = state.payload.files;

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
    <>
      <Heading>Workspace files</Heading>
      {!files.length ? (
        <PanelEmpty>No files in this conversation's workspace.</PanelEmpty>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                {["file", "size", "modified", ""].map((column, index) => (
                  <Th key={index}>{column}</Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {files.map((entry) => (
                <tr key={entry.path}>
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
                </tr>
              ))}
            </tbody>
          </Table>
          {preview === null ? null : (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill-subtle p-lg font-mono text-mono">
              {preview}
            </pre>
          )}
        </>
      )}
    </>
  );
}
