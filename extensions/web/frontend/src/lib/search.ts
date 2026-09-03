import {
  IconApps,
  IconClockPlay,
  IconFile,
  IconMessage,
  IconPlug,
  type TablerIcon,
} from "@tabler/icons-react";

import { slotOf } from "@/kernel/objects";
import { agentName } from "@/lib/agentName";
import { getJson } from "@/lib/api";
import { chatHash, workspaceHash, agentHash, sectionHash } from "@/lib/route";
import type { Agent, Conversation } from "@/lib/types";

/** What one term finds, kind by kind. The bar's search reaches the whole workspace, and the
 *  workspace answers through the reads each screen already takes — there is no second projection
 *  ranking these, so a group is exactly what that screen would have listed for the same term. */
export type Hit = {
  /** What the hit is, which is what makes two of them the same one. Never the place it opens: a
   *  kind whose records share a screen — every task match opens the tasks screen — would collapse
   *  to a single row if where it lands were its identity. */
  key: string;
  /** Where picking the hit lands. Every kind is addressable: a record by its own place, a file by
   *  the artifacts screen carrying the term and the file to open. */
  hash: string;
  primary: string;
  /** The one fact that tells two hits of a kind apart — whose agent, what class, how large. */
  fact: string;
  /** Whether the record is the member's own, where the read answers whose it is. A kind whose read
   *  names no owner states nothing. */
  mine?: boolean;
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

/** What a hit needs of a file's object row: its name, which the card it opens is keyed by, what
 *  it is called, and what it is. */
type FoundArtifact = { name: string; filename: string; media_type: string };

type Found = {
  conversations: { conversations: Conversation[] };
  artifacts: { objects: FoundArtifact[] };
  objects: { objects: FoundObject[] };
};

/** The term as a query, bounded where the surface bounds it. */
function query(term: string, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ q: term, ...extra });
  return "?" + params.toString();
}

/** One kind's answer, from however many reads it takes. A kind read once per agent can name the
 *  same record twice — a conversation every agent's read can see — so hits are deduped by what
 *  they are. */
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

/** What a hit needs of a catalog tile: the provider it connects, and the name the workspace draws
 *  it under. The catalog read is the term's own filter — the surface matches `q` against the
 *  provider and the label together — so nothing is narrowed again here. */
type FoundProvider = { name: string; label: string };

/** What a hit needs of a pooled connection: the grant the panel opens it by, the provider whose
 *  label names it, the strings a member would search an account by, and who reaches it — the fact
 *  an account carrying no label of its own falls back to. */
type FoundConnection = {
  provider: string;
  account_id: string | null;
  account_label: string | null;
  owner_email: string | null;
  shared: boolean;
  grant: string;
};

/** The slot a connection stands in on the connectors screen, which is what a hit opens it at. */
const CONNECTION_SLOT = "connection/";

/** What one term reaches of the connectors screen, from the two reads that screen takes: the
 *  accounts the workspace already holds, then the providers it could still connect. The pool
 *  carries no query, so an account is matched here on the strings the screen draws it by. A
 *  provider whose account is already in the pool is not offered a second time — the tile would
 *  name the thing the row above it names.
 *
 *  Neither read's refusal surfaces: the group stands on whatever the other read answered and reads
 *  as a kind holding nothing. A connector read that refuses — the broker the deploy never keyed,
 *  the catalog it cannot reach — is not a thing the member can act on from the palette, and a
 *  workspace whose connectors are simply unreachable would otherwise put that line under every
 *  term they type. */
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

/** A kind's place in the list, which is where its group stands whenever the kind lands. The reads
 *  run side by side and land in whatever order each one is answered, so the order a member scans
 *  them in is held here rather than taken from the order they finish in. */
type Slot = "apps" | "conversations" | "files" | "sites" | "tasks" | "connectors";

/** Every kind one term reaches, in the order a member scans them: what they are talking to, what
 *  they said, what came out of it, and what runs on its own. The agents are matched here rather
 *  than read, because the shell already holds the whole list.
 *
 *  A conversation search is one read per agent the member reaches, because no projection searches
 *  conversations across agents.
 *
 *  `answering` is handed every kind that has landed so far, each time one lands: the slow kinds do
 *  not hold back the fast ones, and a group arrives under the heading it always stands under. The
 *  same list is returned when the last kind lands, so a caller that wants only the whole answer
 *  passes no callback. */
export async function searchEverywhere(
  term: string,
  agents: Agent[],
  signal: AbortSignal,
  answering: (groups: Group[]) => void = () => {},
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
  /** What each kind has answered. A kind that has not landed holds no slot, and its place in the
   *  list is simply not drawn — the groups around it stand without it. */
  const held = new Map<Slot, Group>();
  /** Everything that has landed, as it stands. */
  const standing = (): Group[] => {
    const files = held.get("files");
    const sites = held.get("sites");
    /** Files and sites are two reads of one app, so they stand as one group — the artifacts app is
     *  what a member opens either from — and the group stands on whichever read has landed. */
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
      ...(artifactsApp ? [artifacts] : []),
      held.get("tasks"),
      held.get("connectors"),
    ]
      .filter((entry): entry is Group => entry !== undefined && entry !== null)
      .filter((entry) => entry.hits.length > 0 || entry.failed !== null);
  };
  /** One kind landing: it takes its slot, and the caller is handed the list as it now stands. A
   *  search the caller has dropped says nothing more. */
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
            hash: artifactsApp ? agentHash(artifactsApp.id, { opens: [entry.name] }) : "",
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
            hash: workspaceHash("tasks", {
              opens: [slotOf({ agent: row.agent_id, kind, name: row.name })],
            }),
            primary: row.name,
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

/** Which threads a page stands in. An app holds its own, so its scope names the agent and is read
 *  under it alone; a connection holds threads across every app it reached, and no read is keyed by
 *  a surface, so that scope names the surface and the rows are kept by it. */
export type Scope =
  | { kind: "app"; agent: Agent }
  | { kind: "surface"; surface: string; label: string };

/** The threads one term finds inside a scope, from the conversation listing each agent already
 *  answers — a thread is found by the same search its app's screen runs. A read spanning agents can
 *  name one conversation twice, so hits are deduped by the conversation they name. */
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
        primary: entry.description || entry.id,
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
