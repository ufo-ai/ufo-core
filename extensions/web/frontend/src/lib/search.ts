import {
  IconApps,
  IconClockPlay,
  IconFile,
  IconMessage,
  IconNote,
  type TablerIcon,
} from "@tabler/icons-react";

import { slotOf } from "@/kernel/objects";
import { agentName } from "@/lib/agentName";
import { getJson } from "@/lib/api";
import { chatHash, workspaceHash, agentHash } from "@/lib/route";
import type { Agent, Conversation } from "@/lib/types";

/** What one term finds, kind by kind. The bar's search reaches the whole workspace, and the
 *  workspace answers through the reads each screen already takes — there is no second projection
 *  ranking these, so a group is exactly what that screen would have listed for the same term. */
export type Hit = {
  /** What the hit is, which is what makes two of them the same one. Never the place it opens: a
   *  kind whose records share a screen — every memory match opens the memory screen — would
   *  collapse to a single row if where it lands were its identity. */
  key: string;
  /** Where picking the hit lands. Every kind is addressable: a record by its own place, a file by
   *  the artifacts screen carrying the term and the file to open. */
  hash: string;
  primary: string;
  /** The one fact that tells two hits of a kind apart — whose agent, what class, how large. */
  fact: string;
};

export type Group = {
  label: string;
  /** The glyph the kind is drawn with wherever it stands — the same one the place it opens takes,
   *  so a hit and the screen it lands on are the one thing to the eye. */
  icon: TablerIcon;
  hits: Hit[];
  /** What the read said when it refused. A group that failed states it rather than reading as a
   *  kind holding nothing. */
  failed: string | null;
};

/** The object kinds the tasks screen lists: the clock- and source-fired standing orders an agent
 *  owns. Each is read across every agent the member reaches, and two agents may hold one name, so a
 *  hit is identified by the agent as well as the name. */
const TASK_KINDS = [
  { kind: "scheduled_task", label: "scheduled task" },
  { kind: "source_trigger", label: "source trigger" },
] as const;

/** A hosted site is the workspace's, not an agent's, and it is listed and opened on the artifacts
 *  screen — the tasks filter holds no family for it. Its read names one agent because a read
 *  naming none fans out per agent and answers the same site once for each. */
const SITE_KIND = "site";

/** What a hit needs of an object row: what it is called, and the agent whose namespace holds it —
 *  the record's own place is under that agent, not under the member who searched. */
type FoundObject = { name: string; agent_id: string };

/** What a hit needs of a file: its own id, which the card it opens is keyed by, what it is called,
 *  and what it is. */
type FoundFile = { id: string; filename: string; media_type: string };

type Found = {
  conversations: { conversations: Conversation[] };
  artifacts: { artifacts: FoundFile[] };
  memory: { matches: { text: string; kind: string; ref: string | null }[] };
  objects: { objects: FoundObject[] };
};

/** The term as a query, bounded where the surface bounds it. */
function query(term: string, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ q: term, ...extra });
  return "?" + params.toString();
}

/** One kind's answer, from however many reads it takes. A kind read once per agent can name the
 *  same record twice — a conversation every agent's read can see — so hits are deduped by what
 *  they are, the way the memory read unions its own per-agent searches. */
async function group<K extends keyof Found>(
  label: string,
  icon: TablerIcon,
  paths: string[],
  hits: (payload: Found[K], path: string) => Hit[],
  signal: AbortSignal,
): Promise<Group> {
  const answers = await Promise.all(paths.map((path) => getJson<Found[K]>(path, signal)));
  const failed = answers.find((answer) => !answer.ok);
  const found = answers.flatMap((answer, at) => (answer.ok ? hits(answer.payload, paths[at]) : []));
  const held = new Map(found.map((hit) => [hit.key, hit]));
  return {
    label,
    icon,
    hits: [...held.values()],
    failed: failed && !failed.ok ? failed.message : null,
  };
}

/** Every kind one term reaches, in the order a member scans them: what they are talking to, what
 *  they said, what came out of it, what is remembered, and what runs on its own. The agents are
 *  matched here rather than read, because the shell already holds the whole list.
 *
 *  A conversation search is one read per agent the member reaches — the same fan-out the memory
 *  read runs server-side — because no projection searches conversations across agents. */
export async function searchEverywhere(
  term: string,
  agents: Agent[],
  signal: AbortSignal,
): Promise<Group[]> {
  const wanted = term.trim();
  if (!wanted) return [];
  const matched = agents.filter((agent) => agent.name.toLowerCase().includes(wanted.toLowerCase()));
  /** The agent a workspace-owned kind is read under — the main one, as the artifacts app does. */
  const named = agents.find((agent) => agent.main) ?? agents[0];
  /** Where a record's hit lands: the shipped app whose pane reads that kind. A workspace without
   *  the app has no screen for the kind, so its group is dropped rather than pointed nowhere. */
  const app = (slug: string) => agents.find((agent) => agent.app === slug);
  const artifactsApp = app("artifacts");
  const tasksApp = app("tasks");
  const [conversations, files, sites, memory, tasks] = await Promise.all([
    group<"conversations">(
      "Conversations",
      IconMessage,
      agents.map((agent) => "/agents/" + agent.id + "/conversations" + query(wanted)),
      (payload) =>
        payload.conversations.map((entry) => ({
          key: entry.id,
          hash: chatHash(entry.id),
          primary: entry.description || entry.id,
          fact: entry.agent ? agentName(entry.agent.name) : "",
        })),
      signal,
    ),
    group<"artifacts">(
      "Artifacts",
      IconFile,
      ["/workspace/artifacts" + query(wanted)],
      (payload) =>
        payload.artifacts.map((entry) => ({
          key: entry.id,
          hash: artifactsApp ? agentHash(artifactsApp.id, { opens: [entry.id] }) : "",
          primary: entry.filename,
          fact: entry.media_type,
        })),
      signal,
    ),
    group<"objects">(
      "Artifacts",
      IconFile,
      ["/objects/" + SITE_KIND + query(wanted, { agent: named?.id ?? "" })],
      (payload) =>
        payload.objects.map((row) => ({
          key: SITE_KIND + "/" + row.name,
          hash: artifactsApp
            ? agentHash(artifactsApp.id, {
                opens: [slotOf({ agent: row.agent_id, kind: SITE_KIND, name: row.name })],
              })
            : "",
          primary: row.name,
          fact: SITE_KIND,
        })),
      signal,
    ),
    group<"memory">(
      "Memory",
      IconNote,
      ["/workspace/memory" + query(wanted)],
      (payload) =>
        payload.matches.map((match) => ({
          key: match.ref ?? match.text,
          hash: workspaceHash("memory", { q: wanted }),
          primary: match.text,
          fact: match.kind,
        })),
      signal,
    ),
    group<"objects">(
      "Tasks",
      IconClockPlay,
      TASK_KINDS.map((entry) => "/objects/" + entry.kind + query(wanted)),
      (payload, path) => {
        const kind = path.slice("/objects/".length).split("?")[0];
        return payload.objects.map((row) => ({
          key: row.agent_id + "/" + kind + "/" + row.name,
          hash: tasksApp
            ? agentHash(tasksApp.id, {
                opens: [slotOf({ agent: row.agent_id, kind, name: row.name })],
              })
            : "",
          primary: row.name,
          fact: TASK_KINDS.find((entry) => entry.kind === kind)?.label ?? kind,
        }));
      },
      signal,
    ),
  ]);
  /** Files and sites are two reads of one app, so they stand as one group — the artifacts app is
   *  what a member opens either from. */
  const artifacts: Group = {
    label: "Artifacts",
    icon: IconFile,
    hits: [...files.hits, ...sites.hits],
    failed: files.failed ?? sites.failed,
  };
  return [
    {
      label: "Apps",
      icon: IconApps,
      hits: matched.map((agent) => ({
        key: agent.id,
        hash: agentHash(agent.id),
        primary: agentName(agent.name),
        fact: agent.model,
      })),
      failed: null,
    },
    conversations,
    ...(artifactsApp ? [artifacts] : []),
    memory,
    ...(tasksApp ? [tasks] : []),
  ].filter((entry) => entry.hits.length > 0 || entry.failed !== null);
}
