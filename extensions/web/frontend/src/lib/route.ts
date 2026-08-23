import { holdableTrack, unholdable } from "@/lib/tracks";

export const WORKSPACE_TABS = [
  "team",
  "skills",
  "memory",
  "sources",
  "credentials",
  "usage",
  "billing",
] as const;

/** Every top-level screen name the address codec reads and writes. The portal itself hosts only
 *  `connectors`; the rest are screens shipped as apps, and a section address naming one lands on
 *  that app with its place carried — the name outlives who renders it, so links keep working. */
export const SECTIONS = ["wiki", "artifacts", "radar", "tasks", "connectors"] as const;

export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];
export type Section = (typeof SECTIONS)[number];

export type PlaceStep = "push" | "replace" | "back";

export type WorkspacePlace = {
  kind?: string;
  after?: string;
  q?: string;
  chip?: string;
  face?: string;
  scope?: string;
  /** The slots standing on the screen, in track order, carried as one `~`-joined `open` key: a
   *  link to a screen carries every slot on it, and a link naming one thing is a track of one. A
   *  slot id names the app its record is read in as well as the record, so one key carries a track
   *  spanning several apps. The track store says which rows are tracks, so the address carries
   *  exactly what a screen can be left holding and reads no other as a place. */
  opens?: string[];
  range?: string;
};

export type Route =
  | { kind: "home" }
  | { kind: "chat"; conversationId: string; slot?: string }
  | {
      kind: "conversation-slot";
      agentId: string;
      conversationId: string;
      slot: string;
      rootConversationId?: string;
    }
  | { kind: "new-chat"; agentId: string }
  | { kind: "agents"; build?: boolean }
  | { kind: "agent"; agentId: string; place: WorkspacePlace }
  | { kind: "workspace"; view: WorkspaceTab; place: WorkspacePlace }
  | { kind: "section"; section: Section; place: WorkspacePlace }
  | { kind: "first-run" }
  | { kind: "admin" }
  | { kind: "bad-link" };

export type RouteKind = Route["kind"];

/** The one route a kind names, so a table row, a dispatch arm and a title segment all speak about
 *  the same member of the union rather than about `Route` at large. */
export type RouteOf<Kind extends RouteKind> = Extract<Route, { kind: Kind }>;

const PLACE_KEYS = ["kind", "after", "q", "chip", "face", "scope", "range"] as const;

const TRACK_KEY = "open";
const TRACK_SEPARATOR = "~";

function parsePlace(raw: string | undefined): WorkspacePlace | null {
  if (!raw) return {};
  const params = new URLSearchParams(raw);
  const place: WorkspacePlace = {};
  for (const key of PLACE_KEYS) {
    const value = params.get(key);
    if (value) place[key] = value;
  }
  const track = params.get(TRACK_KEY);
  if (!track) return place;
  const opens = track.split(TRACK_SEPARATOR);
  if (!holdableTrack(opens)) return null;
  place.opens = opens;
  return place;
}

/** The address for a place. The track it carries is exactly the one `parsePlace` admits — the same
 *  rule, read off the same module — plus the one rule this separator adds, so a link this writes is
 *  a link the next read stands a screen on rather than turning away as invalid. A track no screen
 *  could be left holding raises here, at the call that made it up. */
function serializePlace(place: WorkspacePlace): string {
  const params = new URLSearchParams();
  for (const key of PLACE_KEYS) {
    const value = place[key];
    if (value) params.set(key, value);
  }
  const opens = place.opens ?? [];
  const carried = opens.find((id) => id.includes(TRACK_SEPARATOR));
  if (carried !== undefined) {
    throw new Error("A slot id cannot hold " + TRACK_SEPARATOR + ": " + JSON.stringify(carried));
  }
  const fault = unholdable(opens);
  if (fault) throw new Error(fault);
  if (opens.length) params.set(TRACK_KEY, opens.join(TRACK_SEPARATOR));
  const raw = params.toString();
  return raw ? "?" + raw : "";
}

function isWorkspaceTab(name: string): name is WorkspaceTab {
  return WORKSPACE_TABS.includes(name as WorkspaceTab);
}

function isSection(name: string): name is Section {
  return SECTIONS.includes(name as Section);
}

export const HOME_HASH = "#/";

export const AGENTS_HASH = "#/agents";

/** The app-building wizard's own address, so any screen can raise it by navigation. */
export const BUILDER_HASH = AGENTS_HASH + "/builder";

/** Administration's own address. One spelling, so the screen that opens it and the pattern that
 *  reads it cannot drift apart. */
export const ADMIN_HASH = "#/admin";

/** The first run's own address. It is a place, not a boot flag: a member can return to it, and
 *  send a teammate to it, exactly as they can to any other screen. */
export const FIRST_RUN_HASH = "#/first-run";

/** Every part an address is spelled from. A pattern and the builder that answers it are built out
 *  of the same constant, so a prefix cannot be spelled one way in the regex and another way in the
 *  hash a screen hands the browser. */
const CHAT_PREFIX = "#/c/";
const NEW_CHAT_PREFIX = "#/new/";
const AGENT_PREFIX = AGENTS_HASH + "/";
const WORKSPACE_PREFIX = "#/workspace/";
const CONVERSATIONS_PART = "/conversations/";
const SLOTS_PART = "/slots/";
const SLOT_PARAM = "slot";
const ROOT_PARAM = "root";
const CHAT_TARGET_PARAM = "c";
const FIRST_RUN_PARAM = "first";
const ARTIFACT_TARGET_PARAM = "a";
const ARTIFACT_PATH_PREFIX = "/artifacts/";

const UUID = "[0-9a-f-]{36}";
const SLOT_NAME = "[a-z][a-z0-9_-]{0,63}";
const SECTION_NAME = "[a-z][a-z-]*";
const TAB_NAME = "[\\w-]+";
/** What a screen's own address may end in: the place it stands at, captured whole for the place
 *  codec to read. */
const PLACE_TAIL = "(?:\\?(.*))?$";
const SLOT_ONLY = new RegExp("^" + SLOT_NAME + "$");

/** A route with no parts to carry reads its own path, and reads it whatever query the address
 *  arrived holding: a member sent `#/admin?ref=mail` asked for administration, not for whatever a
 *  reader that turned the whole string down would land them on. */
function bare(path: string): RegExp {
  return new RegExp(`^${path}(?:\\?.*)?$`);
}

type RouteRow<Kind extends RouteKind, Args extends unknown[]> = {
  kind: Kind;
  pattern: RegExp;
  read: (match: RegExpMatchArray) => RouteOf<Kind> | null;
  write: (...args: Args) => string;
};

/** One row of the route table: the kind an address reads to, the pattern that reads it, the read
 *  that fills the route in, and the builder that writes the same address back. The four are
 *  declared together because that is what stops them drifting — a row's read cannot answer
 *  another row's kind, and its builder is the only spelling of the address its own pattern
 *  matches. A read answering `null` means the address named this route and named it wrongly,
 *  which is a bad link. */
function row<Kind extends RouteKind, Args extends unknown[]>(
  kind: Kind,
  pattern: RegExp,
  read: (match: RegExpMatchArray) => RouteOf<Kind> | null,
  write: (...args: Args) => string,
): RouteRow<Kind, Args> {
  return { kind, pattern, read, write };
}

const HOME = row(
  "home",
  /^(?:#\/?)?(?:\?.*)?$/,
  () => ({ kind: "home" }),
  () => HOME_HASH,
);

const FIRST_RUN = row(
  "first-run",
  bare(FIRST_RUN_HASH),
  () => ({ kind: "first-run" }),
  () => FIRST_RUN_HASH,
);

const ADMIN = row(
  "admin",
  bare(ADMIN_HASH),
  () => ({ kind: "admin" }),
  () => ADMIN_HASH,
);

const AGENTS = row(
  "agents",
  bare(AGENTS_HASH),
  () => ({ kind: "agents" }),
  () => AGENTS_HASH,
);

const BUILDER = row(
  "agents",
  bare(BUILDER_HASH),
  () => ({ kind: "agents", build: true }),
  () => BUILDER_HASH,
);

const CHAT = row(
  "chat",
  new RegExp(`^${CHAT_PREFIX}(${UUID})${PLACE_TAIL}`),
  (match) => {
    const slot = new URLSearchParams(match[2]).get(SLOT_PARAM);
    return {
      kind: "chat",
      conversationId: match[1],
      ...(slot && SLOT_ONLY.test(slot) ? { slot } : {}),
    };
  },
  (conversationId: string, slot?: string) =>
    CHAT_PREFIX +
    conversationId +
    (slot ? "?" + SLOT_PARAM + "=" + encodeURIComponent(slot) : ""),
);

const CONVERSATION_SLOT = row(
  "conversation-slot",
  new RegExp(
    `^${AGENT_PREFIX}(${UUID})${CONVERSATIONS_PART}(${UUID})${SLOTS_PART}` +
      `(${SLOT_NAME})(?:\\?${ROOT_PARAM}=(${UUID}))?$`,
  ),
  (match) => ({
    kind: "conversation-slot",
    agentId: match[1],
    conversationId: match[2],
    slot: match[3],
    ...(match[4] ? { rootConversationId: match[4] } : {}),
  }),
  (agentId: string, conversationId: string, slot: string, rootConversationId?: string) =>
    AGENT_PREFIX +
    agentId +
    CONVERSATIONS_PART +
    conversationId +
    SLOTS_PART +
    slot +
    (rootConversationId ? "?" + ROOT_PARAM + "=" + rootConversationId : ""),
);

const NEW_CHAT = row(
  "new-chat",
  new RegExp(`^${NEW_CHAT_PREFIX}(${UUID})$`),
  (match) => ({ kind: "new-chat", agentId: match[1] }),
  (agentId: string) => NEW_CHAT_PREFIX + agentId,
);

const AGENT = row(
  "agent",
  new RegExp(`^${AGENT_PREFIX}(${UUID})${PLACE_TAIL}`),
  (match) => {
    const place = parsePlace(match[2]);
    return place && { kind: "agent", agentId: match[1], place };
  },
  (agentId: string, place: WorkspacePlace = {}) =>
    AGENT_PREFIX + agentId + serializePlace(place),
);

/* Connectors stood on a workspace tab once, and so did every screen that ships as an app today, so
   links to those addresses exist outside this code. The address keeps answering with the section
   that holds the same screen, which is why this one row reads to either kind. */
const WORKSPACE = row<"workspace" | "section", [WorkspaceTab, WorkspacePlace?]>(
  "workspace",
  new RegExp(`^${WORKSPACE_PREFIX}(${TAB_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    const place = parsePlace(match[2]);
    if (!place) return null;
    if (isWorkspaceTab(name)) return { kind: "workspace", view: name, place };
    if (isSection(name)) return { kind: "section", section: name, place };
    return null;
  },
  (view: WorkspaceTab, place: WorkspacePlace = {}) =>
    WORKSPACE_PREFIX + view + serializePlace(place),
);

const SECTION = row(
  "section",
  new RegExp(`^#/(${SECTION_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    if (!isSection(name)) return null;
    const place = parsePlace(match[2]);
    return place && { kind: "section", section: name, place };
  },
  (section: Section, place: WorkspacePlace = {}) => "#/" + section + serializePlace(place),
);

/** A row as the read walks it, whichever kind it answers with. */
type RouteReader = { pattern: RegExp; read: (match: RegExpMatchArray) => Route | null };

/** The route table, in the order an address is tried against it: a longer address stands before the
 *  shorter one it starts with, and a route that carries a place stands after the singletons whose
 *  own names its pattern would otherwise swallow. */
const ROUTES: readonly RouteReader[] = [
  HOME,
  FIRST_RUN,
  ADMIN,
  BUILDER,
  AGENTS,
  CHAT,
  CONVERSATION_SLOT,
  NEW_CHAT,
  AGENT,
  WORKSPACE,
  SECTION,
];

/** What the address says, read off the table. One answer covers every address the portal cannot
 *  read — a name no screen carries, a mis-cased permalink, a mangled track — so a member holding
 *  a broken link is told it is broken rather than stood in front of the home composer. */
export function parseHash(hash: string): Route {
  for (const route of ROUTES) {
    const match = hash.match(route.pattern);
    if (match) return route.read(match) ?? { kind: "bad-link" };
  }
  return { kind: "bad-link" };
}

/** Whether a route is of a kind, and the narrowing that goes with it. An app page is compiled in
 *  the browser against the kit, so this is the one test it asks with rather than re-deriving the
 *  kinds the table declares. */
export function routeIs<Kind extends RouteKind>(
  route: Route,
  kind: Kind,
): route is RouteOf<Kind> {
  return route.kind === kind;
}

export function artifactTarget(search: string): string | null {
  const target = new URLSearchParams(search).get(ARTIFACT_TARGET_PARAM);
  return target && target.startsWith(ARTIFACT_PATH_PREFIX) ? target : null;
}

/** Where a fresh page load lands. The sign-in form carries the conversation or artifact it was
 *  opened for, and the sign-in that founded the workspace carries `first`, which is the query form
 *  of the first run's own address — a form sign-in can post to, since a fragment never
 *  reaches the server. The shell puts the address itself in the bar on arrival. A member returning
 *  later carries neither and lands where they always do.
 *
 *  `first-run` names no agent because this read has none: the shell resolves it against the roster
 *  it already holds, which is the only place the main agent's id is known. */
export function bootRoute(hash: string, search: string): Route {
  const route = parseHash(hash);
  if (route.kind !== "home") return route;
  const params = new URLSearchParams(search);
  const target = params.get(CHAT_TARGET_PARAM);
  if (target) return parseHash(chatHash(target));
  if (params.get(FIRST_RUN_PARAM)) return { kind: "first-run" };
  return route;
}

/** The builders, each one its row's own `write`: the address a screen hands the browser is written
 *  by the row whose pattern reads it back. */
export const chatHash = CHAT.write;
export const conversationSlotHash = CONVERSATION_SLOT.write;
export const newChatHash = NEW_CHAT.write;
export const agentHash = AGENT.write;
export const workspaceHash = WORKSPACE.write;
export const sectionHash = SECTION.write;
