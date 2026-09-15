import { holdableTrack, unholdable } from "@/lib/tracks";

export const WORKSPACE_TABS = [
  "team",
  "apps",
  "chat",
  "skills",
  "memory",
  "credentials",
  "usage",
  "billing",
] as const;

export const SECTIONS = ["wiki", "radar", "artifacts", "connectors"] as const;

export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];
export type Section = (typeof SECTIONS)[number];

export const CONNECTION_TABS = ["connectors"] as const satisfies readonly Section[];

export type ConnectionTab = (typeof CONNECTION_TABS)[number];

export function connectionTab(section: Section): section is ConnectionTab {
  return CONNECTION_TABS.includes(section as ConnectionTab);
}

export type PlaceStep = "push" | "replace" | "back";

export type WorkspacePlace = {
  kind?: string;
  after?: string;
  q?: string;
  chip?: string;
  face?: string;
  scope?: string;
  opens?: string[];
  range?: string;
  runs?: string;
};

export type Route =
  | { kind: "home"; place: WorkspacePlace }
  | { kind: "chat"; conversationId: string; slot?: string; report?: string; run?: string }
  | {
      kind: "conversation-slot";
      agentId: string;
      conversationId: string;
      slot: string;
      rootConversationId?: string;
    }
  | { kind: "new-chat"; agentId: string }
  | { kind: "chats"; place: WorkspacePlace }
  | { kind: "agents"; build?: boolean; place: WorkspacePlace }
  | { kind: "store" }
  | { kind: "automations"; place: WorkspacePlace }
  | { kind: "agent"; agentId: string; place: WorkspacePlace }
  | { kind: "agent-setup"; agentId: string }
  | { kind: "workspace"; view: WorkspaceTab; place: WorkspacePlace }
  | { kind: "section"; section: Section; place: WorkspacePlace }
  | { kind: "first-run"; step?: string }
  | { kind: "bad-link" };

export type RouteKind = Route["kind"];

export type RouteOf<Kind extends RouteKind> = Extract<Route, { kind: Kind }>;

export const COMPOSE = "compose";

const TRACK_KEY = "open";
const TRACK_SEPARATOR = "~";

type PlaceKey = keyof WorkspacePlace;

type PlaceField<Key extends PlaceKey> = {
  read: (params: URLSearchParams) => WorkspacePlace[Key] | null;
  write: (params: URLSearchParams, value: WorkspacePlace[Key]) => void;
};

function text(key: string): {
  read: (params: URLSearchParams) => string | undefined;
  write: (params: URLSearchParams, value: string | undefined) => void;
} {
  return {
    read: (params) => params.get(key) || undefined,
    write: (params, value) => {
      if (value) params.set(key, value);
    },
  };
}

const TRACK: PlaceField<"opens"> = {
  read: (params) => {
    const track = params.get(TRACK_KEY);
    if (!track) return undefined;
    const opens = track.split(TRACK_SEPARATOR);
    return holdableTrack(opens) ? opens : null;
  },
  write: (params, opens = []) => {
    const carried = opens.find((id) => id.includes(TRACK_SEPARATOR));
    if (carried !== undefined) {
      throw new Error("A slot id cannot hold " + TRACK_SEPARATOR + ": " + JSON.stringify(carried));
    }
    const fault = unholdable(opens);
    if (fault) throw new Error(fault);
    if (opens.length) params.set(TRACK_KEY, opens.join(TRACK_SEPARATOR));
  },
};

/** Typed over `WorkspacePlace` itself, so a key the type gains and this record does not is a compile
 *  error rather than a key that quietly leaves the address on the next place change. */
const PLACE_CODEC: { [Key in PlaceKey]: PlaceField<Key> } = {
  kind: text("kind"),
  after: text("after"),
  q: text("q"),
  chip: text("chip"),
  face: text("face"),
  scope: text("scope"),
  range: text("range"),
  runs: text("runs"),
  opens: TRACK,
};

const PLACE_KEYS = Object.keys(PLACE_CODEC) as PlaceKey[];

function readKey<Key extends PlaceKey>(
  place: WorkspacePlace,
  key: Key,
  params: URLSearchParams,
): boolean {
  const value = PLACE_CODEC[key].read(params);
  if (value === null) return false;
  place[key] = value;
  return true;
}

function writeKey<Key extends PlaceKey>(
  params: URLSearchParams,
  place: WorkspacePlace,
  key: Key,
): void {
  PLACE_CODEC[key].write(params, place[key]);
}

function carryKey<Key extends PlaceKey>(next: WorkspacePlace, from: WorkspacePlace, key: Key): void {
  next[key] = from[key];
}

function parsePlace(raw: string | undefined): WorkspacePlace | null {
  if (!raw) return {};
  const params = new URLSearchParams(raw);
  const place: WorkspacePlace = {};
  for (const key of PLACE_KEYS) {
    if (!readKey(place, key, params)) return null;
  }
  return place;
}

export function serializePlace(place: WorkspacePlace): string {
  const params = new URLSearchParams();
  for (const key of PLACE_KEYS) writeKey(params, place, key);
  const raw = params.toString();
  return raw ? "?" + raw : "";
}

/** Total over the codec's own keys, so no place change can drop one: a merge that listed the keys by
 *  hand omitted `range`, and every act on the usage tab erased the range from the address. */
export function mergePlace(held: WorkspacePlace, patch: WorkspacePlace): WorkspacePlace {
  const next: WorkspacePlace = {};
  for (const key of PLACE_KEYS) carryKey(next, key in patch ? patch : held, key);
  return next;
}

function isSection(name: string): name is Section {
  return SECTIONS.includes(name as Section);
}

export const HOME_HASH = "#/";

export const AGENTS_HASH = "#/agents";

export const BUILDER_HASH = AGENTS_HASH + "/builder";

export const STORE_HASH = AGENTS_HASH + "/store";

const CHATS_HASH = "#/chats";

const AUTOMATIONS_HASH = "#/automations";

export const FIRST_RUN_HASH = "#/first-run";

/** A pattern and the builder that answers it are built from the same constant, so a prefix cannot be
 *  spelled one way in the regex and another in the hash a screen hands the browser. */
const CHAT_PREFIX = "#/c/";
const NEW_CHAT_PREFIX = "#/new/";
const AGENT_PREFIX = AGENTS_HASH + "/";
const WORKSPACE_PREFIX = "#/workspace/";
const CONVERSATIONS_PART = "/conversations/";
const SLOTS_PART = "/slots/";
const SLOT_PARAM = "slot";
const REPORT_PARAM = "report";
const RUN_PARAM = "run";
const ROOT_PARAM = "root";
const CHAT_TARGET_PARAM = "c";
const FIRST_RUN_PARAM = "first";
const ARTIFACT_TARGET_PARAM = "a";
const ARTIFACT_PATH_PREFIX = "/artifacts/";

const UUID = "[0-9a-f-]{36}";
const SLOT_NAME = "[a-z][a-z0-9_-]{0,63}";
const SECTION_NAME = "[a-z][a-z-]*";
const STEP_NAME = "[a-z][a-z-]*";
const TAB_NAME = "[\\w-]+";
const PLACE_TAIL = "(?:\\?(.*))?$";
const SETUP_SEGMENT = "/setup";
const SLOT_ONLY = new RegExp("^" + SLOT_NAME + "$");
const UUID_ONLY = new RegExp("^" + UUID + "$");

/** It reads its own path whatever query the address arrived holding: a member sent `#/first-run?ref=mail`
 *  asked for the first run. */
function bare(path: string): RegExp {
  return new RegExp(`^${path}(?:\\?.*)?$`);
}

type RouteRow<Kind extends RouteKind, Args extends unknown[]> = {
  kind: Kind;
  pattern: RegExp;
  read: (match: RegExpMatchArray) => RouteOf<Kind> | null;
  write: (...args: Args) => string;
};

/** A read answering `null` means the address named this route and named it wrongly, which is a bad link. */
function row<Kind extends RouteKind, Args extends unknown[]>(
  kind: Kind,
  pattern: RegExp,
  read: (match: RegExpMatchArray) => RouteOf<Kind> | null,
  write: (...args: Args) => string,
): RouteRow<Kind, Args> {
  return { kind, pattern, read, write };
}

const HOME = row<"home", [WorkspacePlace?]>(
  "home",
  new RegExp("^(?:#/?)?" + PLACE_TAIL),
  (match) => {
    const place = parsePlace(match[1]);
    return place && { kind: "home", place };
  },
  (place: WorkspacePlace = {}) => HOME_HASH + serializePlace(place),
);

const FIRST_RUN = row(
  "first-run",
  new RegExp(`^${FIRST_RUN_HASH}(?:/(${STEP_NAME}))?(?:\\?.*)?$`),
  (match) => ({ kind: "first-run", ...(match[1] ? { step: match[1] } : {}) }),
  (step?: string) => FIRST_RUN_HASH + (step ? "/" + step : ""),
);

const AGENTS = row<"agents", [WorkspacePlace?]>(
  "agents",
  new RegExp(`^${AGENTS_HASH}${PLACE_TAIL}`),
  (match) => {
    const place = parsePlace(match[1]);
    return place && { kind: "agents", place };
  },
  (place: WorkspacePlace = {}) => AGENTS_HASH + serializePlace(place),
);

const BUILDER = row(
  "agents",
  bare(BUILDER_HASH),
  () => ({ kind: "agents", build: true, place: {} }),
  () => BUILDER_HASH,
);

const STORE = row(
  "store",
  bare(STORE_HASH),
  () => ({ kind: "store" }),
  () => STORE_HASH,
);

const CHATS = row<"chats", [WorkspacePlace?]>(
  "chats",
  new RegExp(`^${CHATS_HASH}${PLACE_TAIL}`),
  (match) => {
    const place = parsePlace(match[1]);
    return place && { kind: "chats", place };
  },
  (place: WorkspacePlace = {}) => CHATS_HASH + serializePlace(place),
);

const AUTOMATIONS = row<"automations", [WorkspacePlace?]>(
  "automations",
  new RegExp(`^${AUTOMATIONS_HASH}${PLACE_TAIL}`),
  (match) => {
    const place = parsePlace(match[1]);
    return place && { kind: "automations", place };
  },
  (place: WorkspacePlace = {}) => AUTOMATIONS_HASH + serializePlace(place),
);

const CHAT = row(
  "chat",
  new RegExp(`^${CHAT_PREFIX}(${UUID})${PLACE_TAIL}`),
  (match) => {
    const place = new URLSearchParams(match[2]);
    const slot = place.get(SLOT_PARAM);
    const report = place.get(REPORT_PARAM);
    const run = place.get(RUN_PARAM);
    return {
      kind: "chat",
      conversationId: match[1],
      ...(slot && SLOT_ONLY.test(slot) ? { slot } : {}),
      ...(report && UUID_ONLY.test(report) ? { report } : {}),
      ...(run && UUID_ONLY.test(run) ? { run } : {}),
    };
  },
  (conversationId: string, slot?: string, report?: string, run?: string) => {
    const place = new URLSearchParams();
    if (slot) place.set(SLOT_PARAM, slot);
    if (report) place.set(REPORT_PARAM, report);
    if (run) place.set(RUN_PARAM, run);
    const tail = place.toString();
    return CHAT_PREFIX + conversationId + (tail ? "?" + tail : "");
  },
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

const AGENT_SETUP = row(
  "agent-setup",
  new RegExp(`^${AGENT_PREFIX}(${UUID})${SETUP_SEGMENT}(?:\\?.*)?$`),
  (match) => ({ kind: "agent-setup", agentId: match[1] }),
  (agentId: string) => AGENT_PREFIX + agentId + SETUP_SEGMENT,
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

const WORKSPACE = row<"workspace" | "section", [WorkspaceTab, WorkspacePlace?]>(
  "workspace",
  new RegExp(`^${WORKSPACE_PREFIX}(${TAB_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    const place = parsePlace(match[2]);
    if (!place) return null;
    if (isSection(name)) {
      return connectionTab(name) ? null : { kind: "section", section: name, place };
    }
    const tab = WORKSPACE_TABS.find((candidate) => candidate === name);
    return tab === undefined ? null : { kind: "workspace", view: tab, place };
  },
  (view: WorkspaceTab, place: WorkspacePlace = {}) =>
    WORKSPACE_PREFIX + view + serializePlace(place),
);

const SECTION = row<"section", [Section, WorkspacePlace?]>(
  "section",
  new RegExp(`^#/(${SECTION_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    const place = parsePlace(match[2]);
    if (!place) return null;
    return isSection(name) ? { kind: "section", section: name, place } : null;
  },
  (section: Section, place: WorkspacePlace = {}) => "#/" + section + serializePlace(place),
);

type RouteReader = { pattern: RegExp; read: (match: RegExpMatchArray) => Route | null };

/** A longer address stands before the shorter one it starts with, and a route carrying a place stands
 *  after the singletons whose own names its pattern would otherwise swallow. */
const ROUTES: readonly RouteReader[] = [
  HOME,
  FIRST_RUN,
  BUILDER,
  STORE,
  AGENTS,
  CHATS,
  AUTOMATIONS,
  CHAT,
  CONVERSATION_SLOT,
  NEW_CHAT,
  AGENT_SETUP,
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

/** Typed over every kind the table declares, so a route the table gains states here whether a frame may
 *  reach it, and one that states nothing is a compile error rather than a fence that admits it. */
const FRAMED: { [Kind in RouteKind]: boolean } = {
  home: false,
  "first-run": false,
  agents: false,
  store: false,
  chats: false,
  automations: false,
  chat: true,
  "conversation-slot": false,
  "new-chat": true,
  agent: true,
  "agent-setup": true,
  workspace: false,
  section: true,
  "bad-link": false,
};

export function framedNavigation(to: string): boolean {
  return FRAMED[parseHash(to).kind];
}

export type Stand =
  | `agent:${string}`
  | `open:${string}`
  | "workspace"
  | "automations"
  | "chats"
  | "store"
  | `section:${Section}`;

export const COMPOSING: Stand = `open:${COMPOSE}`;

/** Exhaustive over the kinds the table declares, so a route it gains says where it stands rather than
 *  marking nothing. */
function stands(route: Route): Stand[] {
  switch (route.kind) {
    case "home":
    case "new-chat":
      return [COMPOSING];
    case "chat":
    case "conversation-slot":
      return [`open:${route.conversationId}`];
    case "agent": {
      const head = route.place.opens?.[0];
      const app: Stand = `agent:${route.agentId}`;
      return head === undefined ? [app] : [app, `open:${head}`];
    }
    case "agent-setup":
      return [`agent:${route.agentId}`];
    case "workspace":
      return ["workspace"];
    case "automations":
      return ["automations"];
    case "store":
      return ["store"];
    case "chats":
      return ["chats"];
    case "section":
      return [`section:${route.section}`];
    case "agents":
    case "first-run":
    case "bad-link":
      return [];
  }
  const missed: never = route;
  return missed;
}

export function standing(route: Route, stand: Stand): boolean {
  return stands(route).includes(stand);
}

export function artifactTarget(search: string): string | null {
  const target = new URLSearchParams(search).get(ARTIFACT_TARGET_PARAM);
  return target && target.startsWith(ARTIFACT_PATH_PREFIX) ? target : null;
}

export function bootRoute(hash: string, search: string): Route {
  const route = parseHash(hash);
  if (route.kind !== "home") return route;
  const params = new URLSearchParams(search);
  const target = params.get(CHAT_TARGET_PARAM);
  if (target) return parseHash(chatHash(target));
  if (params.get(FIRST_RUN_PARAM)) return { kind: "first-run" };
  return route;
}

/** The address of one conversation, optionally at a slot, at a report, or at the run whose own
 *  words the transcript stands on. */
export const chatHash = CHAT.write;
/** The address of the workspace home at a place. */
export const homeHash = HOME.write;
/** The address of one slot in a conversation. */
export const conversationSlotHash = CONVERSATION_SLOT.write;
/** The address of a fresh chat with an agent. */
export const newChatHash = NEW_CHAT.write;
/** The address of an agent's screen at a place. */
export const agentHash = AGENT.write;
/** The address of the apps screen, optionally at a place. */
export const agentsHash = AGENTS.write;
/** The address of an agent's setup screen. */
export const agentSetupHash = AGENT_SETUP.write;
/** The address of a workspace tab, optionally at a place. */
export const workspaceHash = WORKSPACE.write;
/** The address of a section, optionally at a place. */
export const sectionHash = SECTION.write;
/** The address of the chats screen, optionally at a place. */
export const chatsHash = CHATS.write;
/** The address of the automations screen, optionally at a place. */
export const automationsHash = AUTOMATIONS.write;
/** The address of the first run, at a step or at its welcome. */
export const firstRunHash = FIRST_RUN.write;
