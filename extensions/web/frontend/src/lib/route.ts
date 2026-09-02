import { holdableTrack, unholdable } from "@/lib/tracks";

export const WORKSPACE_TABS = [
  "team",
  "apps",
  "tasks",
  "skills",
  "memory",
  "connectors",
  "credentials",
  "usage",
  "billing",
] as const;

/** Every top-level screen name the address codec reads and writes. The portal itself hosts only
 *  `connectors`; the rest are screens shipped as apps, and a section address naming one lands on
 *  that app with its place carried — the name outlives who renders it, so links keep working. */
export const SECTIONS = ["wiki", "artifacts", "radar", "connectors"] as const;

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
  | { kind: "home"; place: WorkspacePlace }
  | { kind: "chat"; conversationId: string; slot?: string }
  | {
      kind: "conversation-slot";
      agentId: string;
      conversationId: string;
      slot: string;
      rootConversationId?: string;
    }
  | { kind: "new-chat"; agentId: string }
  | { kind: "builder" }
  | { kind: "agent"; agentId: string; place: WorkspacePlace }
  | { kind: "agent-setup"; agentId: string }
  | { kind: "workspace"; view: WorkspaceTab; place: WorkspacePlace }
  | { kind: "section"; section: Section; place: WorkspacePlace }
  | { kind: "first-run" }
  | { kind: "bad-link" };

export type RouteKind = Route["kind"];

/** The one route a kind names, so a table row, a dispatch arm and a title segment all speak about
 *  the same member of the union rather than about `Route` at large. */
export type RouteOf<Kind extends RouteKind> = Extract<Route, { kind: Kind }>;

/** The chat surface's start screen, as the address spells it: a track whose head is this stands the
 *  composer and its starters rather than a conversation. It is an address token — it rides the
 *  track key — so it is declared with the table that reads and writes it. */
export const COMPOSE = "compose";

/** The lane a member opens to pick an app, as the address spells it. It names no app because none
 *  has been picked yet, which is what tells it from every other home lane. */
export const HOME_NEW_LANE = "new";

/** The lane the connectors screen stands in, as the address spells it. The portal draws that screen
 *  itself rather than an app holding it, so the lane names the screen; an app id is a uuid and
 *  carries no such word, and the screen is one record, so it stands in one lane. */
export const HOME_CONNECTORS_LANE = "connectors";

/** What stands between an app and the instance of it a lane holds: an app opened twice is two lanes
 *  over one app, so the second lane names the app and the instance both. An app id is a uuid and
 *  carries none of this character, so a lane splits back into the parts it was minted from. */
const HOME_LANE_INSTANCE = ".";

/** The lane an app takes on home, given the lanes already standing: the first instance is the app
 *  itself, and every one after it carries the lowest instance number no lane holds. */
export function mintHomeLane(agentId: string, taken: readonly string[]): string {
  if (!taken.includes(agentId)) return agentId;
  let instance = 2;
  while (taken.includes(agentId + HOME_LANE_INSTANCE + instance)) instance += 1;
  return agentId + HOME_LANE_INSTANCE + instance;
}

/** What marks a lane as a conversation's rather than an app's. An app id is a uuid and a uuid holds
 *  no colon, so the mark cannot be read off an app's lane, off an instance of one, or off the
 *  picker's one word. */
const HOME_LANE_CONVERSATION = "c:";

/** The lane a conversation takes on home. It carries no instance: a conversation is one record and
 *  two lanes over it would be two hosts over one transcript, so the conversation is the lane. */
export function homeConversationLane(conversationId: string): string {
  return HOME_LANE_CONVERSATION + conversationId;
}

/** The conversation a home lane holds, or nothing where the lane holds an app or is the picker. */
export function homeLaneConversation(lane: string): string | null {
  return lane.startsWith(HOME_LANE_CONVERSATION)
    ? lane.slice(HOME_LANE_CONVERSATION.length)
    : null;
}

/** The app a home lane stands, or nothing where the lane holds a conversation, holds a screen the
 *  portal draws itself, or is the picker and stands none. */
export function homeLaneAgent(lane: string): string | null {
  if (lane === HOME_NEW_LANE || lane === HOME_CONNECTORS_LANE) return null;
  if (lane.startsWith(HOME_LANE_CONVERSATION)) return null;
  const [agentId] = lane.split(HOME_LANE_INSTANCE);
  return agentId;
}

const TRACK_KEY = "open";
const TRACK_SEPARATOR = "~";

type PlaceKey = keyof WorkspacePlace;

/** How one place key crosses an address: the read that takes it off the query, and the write that
 *  puts it back. A read answering `null` means the key was there and held what no screen can stand
 *  on, which makes the whole address a bad link. */
type PlaceField<Key extends PlaceKey> = {
  read: (params: URLSearchParams) => WorkspacePlace[Key] | null;
  write: (params: URLSearchParams, value: WorkspacePlace[Key]) => void;
};

/** A key carried as its own text under its own name, which is every place key but the track. */
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

/** The track, carried as one `~`-joined key. The read admits exactly the rows a screen can be left
 *  holding and turns the address away otherwise; the write refuses a row that read would not admit,
 *  raising at the call that made it up rather than at the link a member is handed. */
const TRACK: PlaceField<"opens"> = {
  read: (params) => {
    const track = params.get(TRACK_KEY);
    /* An absent key states nothing, and the screen's held track answers; a key present and empty is
       the member having shut the last slot — a statement, and the store obeys it. */
    if (track === null) return undefined;
    if (track === "") return [];
    const opens = track.split(TRACK_SEPARATOR);
    return holdableTrack(opens) ? opens : null;
  },
  write: (params, opens) => {
    if (opens === undefined) return;
    const carried = opens.find((id) => id.includes(TRACK_SEPARATOR));
    if (carried !== undefined) {
      throw new Error("A slot id cannot hold " + TRACK_SEPARATOR + ": " + JSON.stringify(carried));
    }
    const fault = unholdable(opens);
    if (fault) throw new Error(fault);
    params.set(TRACK_KEY, opens.join(TRACK_SEPARATOR));
  },
};

/** Every key a place holds, each with the one way it is read, written and carried forward. The
 *  record is typed over `WorkspacePlace` itself, so a key the type gains and this record does not is
 *  a compile error rather than a key that quietly leaves the address on the next place change.
 *  Declaration order is the order an address spells the keys in. */
const PLACE_CODEC: { [Key in PlaceKey]: PlaceField<Key> } = {
  kind: text("kind"),
  after: text("after"),
  q: text("q"),
  chip: text("chip"),
  face: text("face"),
  scope: text("scope"),
  range: text("range"),
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

/** The address for a place, over every key the codec knows — the same keys the read admits, so a
 *  link this writes is a link the next read stands a screen on. */
export function serializePlace(place: WorkspacePlace): string {
  const params = new URLSearchParams();
  for (const key of PLACE_KEYS) writeKey(params, place, key);
  const raw = params.toString();
  return raw ? "?" + raw : "";
}

/** The place a patch leaves: a key the patch names it takes, and a key the patch does not name
 *  stays as it stood. Total over the codec's own keys, so no place change can drop one — a merge
 *  that listed the keys by hand omitted `range`, and every search, filter, page step and lane open
 *  on the usage tab erased the range from the address. */
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

/** The app-building wizard's own address, so any screen can raise it by navigation. */
export const BUILDER_HASH = AGENTS_HASH + "/builder";

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
const SETUP_SEGMENT = "/setup";
const SLOT_ONLY = new RegExp("^" + SLOT_NAME + "$");

/** A route with no parts to carry reads its own path, and reads it whatever query the address
 *  arrived holding: a member sent `#/first-run?ref=mail` asked for the first run, not for whatever
 *  a reader that turned the whole string down would land them on. */
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

/** Home stands a track like any other screen — its lanes are the apps the member opened, in the
 *  order the address states them — so it reads its place off the same tail every screen that carries
 *  one does. The bare path is home holding nothing, which is the address a member lands on before
 *  they have opened a lane. */
const HOME = row(
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
  bare(FIRST_RUN_HASH),
  () => ({ kind: "first-run" }),
  () => FIRST_RUN_HASH,
);

const APPS = row<"workspace", [WorkspacePlace?]>(
  "workspace",
  new RegExp(`^${AGENTS_HASH}${PLACE_TAIL}`),
  (match) => {
    const place = parsePlace(match[1]);
    return place && { kind: "workspace", view: "apps", place };
  },
  (place: WorkspacePlace = {}) => AGENTS_HASH + serializePlace(place),
);

const BUILDER = row(
  "builder",
  bare(BUILDER_HASH),
  () => ({ kind: "builder" }),
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

/** An app the workspace has not finished wiring stands here instead of on its own page, so setup
 *  is an address a member can be sent to, link, and come back to — never a band the page carries.
 *  A page draws what the app is and what it has done; neither has an answer yet for an app with no
 *  account, and standing sample rows in their place would be showing the member someone else's app
 *  and calling it theirs. */
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

/* The connectors screen is a workspace tab and a section both: the tab is where a member finds it,
   and the section name is the address held links carry. So the workspace prefix reads that one name
   as the tab, and every other section name at this prefix stays the section's. */
const WORKSPACE = row<"workspace" | "section", [WorkspaceTab, WorkspacePlace?]>(
  "workspace",
  new RegExp(`^${WORKSPACE_PREFIX}(${TAB_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    const place = parsePlace(match[2]);
    if (!place) return null;
    if (isSection(name) && name !== "connectors") {
      return { kind: "section", section: name, place };
    }
    const tab = WORKSPACE_TABS.find((candidate) => candidate === name);
    return tab === undefined ? null : { kind: "workspace", view: tab, place };
  },
  (view: WorkspaceTab, place: WorkspacePlace = {}) =>
    WORKSPACE_PREFIX + view + serializePlace(place),
);

/* The door swings the other way too: tasks stood as its own section, shipped as an app, and links
   to that address exist outside this code. Only a name that made that move answers here — every
   other tab keeps `#/workspace/` as its one address — which is why this row, like the workspace
   row, reads to either kind. */
const TAB_SECTIONS: readonly WorkspaceTab[] = ["tasks"];

const SECTION = row<"section" | "workspace", [Section, WorkspacePlace?]>(
  "section",
  new RegExp(`^#/(${SECTION_NAME})${PLACE_TAIL}`),
  (match) => {
    const name = match[1];
    const place = parsePlace(match[2]);
    if (!place) return null;
    if (isSection(name)) return { kind: "section", section: name, place };
    const moved = TAB_SECTIONS.find((tab) => tab === name);
    return moved === undefined ? null : { kind: "workspace", view: moved, place };
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
  BUILDER,
  APPS,
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

/** Whether the shell sends the member where a framed app page asks: a conversation permalink, a new
 *  conversation, an app, an app's setup screen, or a built-in section. Everything else — home,
 *  administration, a workspace tab, an external URL that parses to `home` — is refused, so a frame
 *  cannot bounce the member into an arbitrary place (RFC 0039's navigation fence, and the whole of
 *  it: the shell acts on a frame's request under the viewer's own session).
 *
 *  A page reaches its own setup screen because that screen is the only place an account is
 *  reconnected or a schedule re-armed, and a built page whose account was revoked is exactly the
 *  page that has to say so. The screen is forced on nobody: an app stands there of its own accord
 *  only until the workspace has built it a page once.
 *
 *  It is a column of the table rather than a second list of kinds held beside it. The record is typed
 *  over every kind the table declares, so a route the table gains states here whether a frame may
 *  reach it, and one that states nothing is a compile error rather than a fence that quietly admits
 *  it. */
const FRAMED: { [Kind in RouteKind]: boolean } = {
  home: false,
  "first-run": false,
  builder: false,
  chat: true,
  "conversation-slot": false,
  "new-chat": true,
  agent: true,
  "agent-setup": true,
  workspace: false,
  section: true,
  "bad-link": false,
};

/** Whether the shell will send the member to `to` on a framed page's request. */
export function framedNavigation(to: string): boolean {
  return FRAMED[parseHash(to).kind];
}

/** A place a control can name to ask whether the member is already standing there. Three id spaces
 *  reach this — an app's own id, a slot standing on a screen, a screen with one name — and a name
 *  means nothing across them, so the space leads the name, exactly as the track store's keys do. */
export type Stand = `agent:${string}` | `open:${string}` | "workspace" | `section:${Section}`;

/** The composer, as a place. The chat app opened at its start screen carries it as the head of its
 *  own track; the home screen and a new conversation are the same place with no app around them. One
 *  stand covers all three, so the sidebar's New conversation row asks one question however the
 *  workspace is shipped. */
export const COMPOSING: Stand = `open:${COMPOSE}`;

/** Where a route leaves the member standing. A route stands in more than one place at once: an app
 *  holding a conversation stands both on that app and on the conversation, and the row for each
 *  marks itself. Exhaustive over the kinds the table declares, so a route it gains says where it
 *  stands rather than marking nothing. */
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
      return route.view === "apps" ? [] : ["workspace"];
    case "section":
      return [`section:${route.section}`];
    case "builder":
    case "first-run":
    case "bad-link":
      return [];
  }
  const missed: never = route;
  return missed;
}

/** The "am I here" test every sidebar row and every rail row wears, read off the table rather than
 *  inlined as a comparison of route fields at each row. */
export function standing(route: Route, stand: Stand): boolean {
  return stands(route).includes(stand);
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

/* The builders, each one its row's own `write`: the address a screen hands the browser is written
   by the row whose pattern reads it back. */
/** The address of the workspace home at a place. */
export const homeHash = HOME.write;
/** The address of one conversation, optionally at a slot. */
export const chatHash = CHAT.write;
/** The address of one slot in a conversation. */
export const conversationSlotHash = CONVERSATION_SLOT.write;
/** The address of a fresh chat with an agent. */
export const newChatHash = NEW_CHAT.write;
/** The address of an agent's screen at a place. */
export const agentHash = AGENT.write;
/** The address of an agent's setup screen. */
export const agentSetupHash = AGENT_SETUP.write;
/** The address of a workspace tab, optionally at a place. */
export function workspaceHash(view: WorkspaceTab, place: WorkspacePlace = {}): string {
  return view === "apps" ? APPS.write(place) : WORKSPACE.write(view, place);
}
/** The address of a section, optionally at a place. */
export const sectionHash = SECTION.write;
