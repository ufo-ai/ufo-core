import {
  IconApps,
  IconClockPlay,
  IconFile,
  IconMessage,
  IconPlug,
  type TablerIcon,
} from "@tabler/icons-react";

import { slotOf, titled } from "@/kernel/objects";
import { agentName } from "@/lib/agentName";
import { getJson } from "@/lib/api";
import { subject } from "@/lib/audience";
import { chatHash, agentHash, sectionHash, tasksHash } from "@/lib/route";
import type { Agent, Conversation } from "@/lib/types";

export type Hit = {
  key: string;
  hash: string;
  primary: string;
  fact: string;
  mine?: boolean;
};

export type Group = {
  label: string;
  icon: TablerIcon;
  hits: Hit[];
  failed: string | null;
};

const SCHEDULED_TASK_KIND = "scheduled_task";

const TASK_KINDS = [
  { kind: SCHEDULED_TASK_KIND, label: "scheduled task" },
  { kind: "source_trigger", label: "source trigger" },
] as const;

const SITE_KIND = "site";

const NEW_CONVERSATION = "New conversation";

/** A thread hit's main line: what the transcript list calls the same conversation, and the word for
 *  an untitled one — never its id. */
function threadLine(entry: Conversation, viewer: string | null): string {
  return subject(entry, viewer) || NEW_CONVERSATION;
}

type FoundObject = { name: string; agent_id: string; summary: string };

type FoundArtifact = { name: string; filename: string; media_type: string };

type Found = {
  conversations: { conversations: Conversation[] };
  artifacts: { objects: FoundArtifact[] };
  objects: { objects: FoundObject[] };
};

function query(term: string, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ q: term, ...extra });
  return "?" + params.toString();
}

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

type FoundProvider = { name: string; label: string };

type FoundConnection = {
  provider: string;
  account_id: string | null;
  account_label: string | null;
  owner_email: string | null;
  shared: boolean;
  grant: string;
};

const CONNECTION_SLOT = "connection/";

export async function searchConnectors(term: string, signal: AbortSignal): Promise<Group> {
  const wanted = term.trim();
  const [catalog, pool] = await Promise.all([
    getJson<{ providers: FoundProvider[] }>("/connector-catalog" + query(wanted), signal),
    getJson<{ connections: FoundConnection[] }>("/connections", signal),
  ]);
  const providers = catalog.ok ? catalog.payload.providers : [];
  const labels = new Map(providers.map((row) => [row.name, row.label]));
  const sought = wanted.toLowerCase();
  const connections = (pool.ok ? pool.payload.connections : []).filter((entry) =>
    [entry.provider, entry.account_label, entry.account_id, entry.owner_email].some((held) =>
      held?.toLowerCase().includes(sought),
    ),
  );
  const connected = new Set(connections.map((entry) => entry.provider));
  return {
    label: "Connectors",
    icon: IconPlug,
    hits: [
      ...connections.map((entry) => ({
        key: entry.grant,
        hash: sectionHash("connectors", { opens: [CONNECTION_SLOT + entry.grant] }),
        primary: labels.get(entry.provider) ?? entry.provider,
        fact: entry.account_label ?? (entry.shared ? "Workspace" : "Only you"),
      })),
      ...providers
        .filter((row) => !connected.has(row.name))
        .map((row) => ({
          key: "offer:" + row.name,
          hash: sectionHash("connectors", { q: wanted }),
          primary: row.label,
          fact: "Not connected",
        })),
    ],
    failed: null,
  };
}

type Slot = "apps" | "conversations" | "files" | "sites" | "tasks" | "connectors";

/** A conversation search is one read per agent the member reaches, because no projection searches
 *  conversations across agents. */
export async function searchEverywhere(
  term: string,
  agents: Agent[],
  viewer: string | null,
  signal: AbortSignal,
  answering: (groups: Group[]) => void = () => {},
): Promise<Group[]> {
  const wanted = term.trim();
  if (!wanted) return [];
  const matched = agents.filter((agent) => agent.name.toLowerCase().includes(wanted.toLowerCase()));
  const named = agents.find((agent) => agent.main) ?? agents[0];
  const held = new Map<Slot, Group>();
  const standing = (): Group[] => {
    const files = held.get("files");
    const sites = held.get("sites");
    const artifacts: Group | null =
      files || sites
        ? {
            label: "Artifacts",
            icon: IconFile,
            hits: [...(files?.hits ?? []), ...(sites?.hits ?? [])],
            failed: files?.failed ?? sites?.failed ?? null,
          }
        : null;
    return [
      held.get("apps"),
      held.get("conversations"),
      artifacts,
      held.get("tasks"),
      held.get("connectors"),
    ]
      .filter((entry): entry is Group => entry !== undefined && entry !== null)
      .filter((entry) => entry.hits.length > 0 || entry.failed !== null);
  };
  const lands = async (slot: Slot, reading: Promise<Group>) => {
    const answer = await reading;
    held.set(slot, answer);
    if (!signal.aborted) answering(standing());
  };
  held.set("apps", {
    label: "Apps",
    icon: IconApps,
    hits: matched.map((agent) => ({
      key: agent.id,
      hash: agentHash(agent.id),
      primary: agentName(agent.name),
      fact: agent.model,
    })),
    failed: null,
  });
  answering(standing());
  await Promise.all([
    lands(
      "conversations",
      group<"conversations">(
        "Threads",
        IconMessage,
        agents.map((agent) => "/agents/" + agent.id + "/conversations" + query(wanted)),
        (payload) =>
          payload.conversations.map((entry) => ({
            key: entry.id,
            hash: chatHash(entry.id),
            primary: threadLine(entry, viewer),
            fact: entry.agent ? agentName(entry.agent.name) : "",
          })),
        signal,
      ),
    ),
    lands(
      "files",
      group<"artifacts">(
        "Artifacts",
        IconFile,
        ["/objects/artifact" + query(wanted, { order_by: "shared_at", order: "desc" })],
        (payload) =>
          payload.objects.map((entry) => ({
            key: entry.name,
            hash: sectionHash("artifacts", { opens: [entry.name] }),
            primary: entry.filename,
            fact: entry.media_type,
          })),
        signal,
      ),
    ),
    lands(
      "sites",
      group<"objects">(
        "Artifacts",
        IconFile,
        ["/objects/" + SITE_KIND + query(wanted, { agent: named?.id ?? "" })],
        (payload) =>
          payload.objects.map((row) => ({
            key: SITE_KIND + "/" + row.name,
            hash: sectionHash("artifacts", {
              opens: [slotOf({ agent: row.agent_id, kind: SITE_KIND, name: row.name })],
            }),
            primary: row.name,
            fact: SITE_KIND,
          })),
        signal,
      ),
    ),
    lands(
      "tasks",
      group<"objects">(
        "Tasks",
        IconClockPlay,
        TASK_KINDS.map((entry) => "/objects/" + entry.kind + query(wanted)),
        (payload, path) => {
          const kind = path.slice("/objects/".length).split("?")[0];
          return payload.objects.map((row) => ({
            key: row.agent_id + "/" + kind + "/" + row.name,
            hash: tasksHash(kind === SCHEDULED_TASK_KIND ? "scheduled" : "triggers", {
              opens: [slotOf({ agent: row.agent_id, kind, name: row.name })],
            }),
            primary: titled(kind, row.name, row),
            fact: TASK_KINDS.find((entry) => entry.kind === kind)?.label ?? kind,
          }));
        },
        signal,
      ),
    ),
    lands("connectors", searchConnectors(wanted, signal)),
  ]);
  return standing();
}

export type Scope =
  | { kind: "app"; agent: Agent }
  | { kind: "surface"; surface: string; label: string };

/** A read spanning agents can name one conversation twice, so hits are deduped by the conversation they name. */
export async function searchThreads(
  scope: Scope,
  term: string,
  agents: Agent[],
  viewer: string | null,
  signal: AbortSignal,
): Promise<Group> {
  const wanted = term.trim();
  const read = scope.kind === "app" ? [scope.agent] : agents;
  const answers = await Promise.all(
    read.map((agent) =>
      getJson<{ conversations: Conversation[] }>(
        "/agents/" + agent.id + "/conversations" + query(wanted),
        signal,
      ),
    ),
  );
  const failed = answers.find((answer) => !answer.ok);
  const found = answers.flatMap((answer) =>
    answer.ok
      ? answer.payload.conversations.filter(
          (entry) => scope.kind === "app" || entry.surface === scope.surface,
        )
      : [],
  );
  const held = new Map(
    found.map((entry) => [
      entry.id,
      {
        key: entry.id,
        hash: chatHash(entry.id),
        primary: threadLine(entry, viewer),
        fact: "",
        mine: entry.member_email !== null && entry.member_email === viewer,
      },
    ]),
  );
  return {
    label: "Result threads",
    icon: IconMessage,
    hits: [...held.values()],
    failed: failed && !failed.ok ? failed.message : null,
  };
}
