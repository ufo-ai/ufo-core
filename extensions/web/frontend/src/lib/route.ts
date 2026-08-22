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

const CHAT_PREFIX = "#/c/";
const CHAT_HASH = /^#\/c\/([0-9a-f-]{36})(?:\?(.*))?$/;
const CHAT_TARGET_PARAM = "c";
const FIRST_RUN_PARAM = "first";
const ARTIFACT_TARGET_PARAM = "a";
const ARTIFACT_PATH_PREFIX = "/artifacts/";
const CONVERSATION_SLOT_HASH =
  /^#\/agents\/([0-9a-f-]{36})\/conversations\/([0-9a-f-]{36})\/slots\/([a-z][a-z0-9_-]{0,63})(?:\?root=([0-9a-f-]{36}))?$/;
const NEW_CHAT_HASH = /^#\/new\/([0-9a-f-]{36})$/;
/** The first run's own address. It is a place, not a boot flag: a member can return to it, and
 *  send a teammate to it, exactly as they can to any other screen. */
export const FIRST_RUN_HASH = "#/first-run";
const AGENT_HASH = /^#\/agents\/([0-9a-f-]{36})(?:\?(.*))?$/;
const WORKSPACE_HASH = /^#\/workspace\/([\w-]+)(?:\?(.*))?$/;
const SECTION_HASH = /^#\/([a-z][a-z-]*)(?:\?(.*))?$/;

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
export const BUILDER_HASH = "#/agents/builder";

export function parseHash(hash: string): Route {
  if (hash === FIRST_RUN_HASH) return { kind: "first-run" };
  if (hash === "#/admin") return { kind: "admin" };
  if (hash === AGENTS_HASH) return { kind: "agents" };
  if (hash === BUILDER_HASH) return { kind: "agents", build: true };
  const chat = hash.match(CHAT_HASH);
  if (chat) {
    const slot = new URLSearchParams(chat[2]).get("slot");
    return {
      kind: "chat",
      conversationId: chat[1],
      ...(slot && /^[a-z][a-z0-9_-]{0,63}$/.test(slot) ? { slot } : {}),
    };
  }
  if (hash.startsWith(CHAT_PREFIX)) return { kind: "bad-link" };
  const conversationSlot = hash.match(CONVERSATION_SLOT_HASH);
  if (conversationSlot)
    return {
      kind: "conversation-slot",
      agentId: conversationSlot[1],
      conversationId: conversationSlot[2],
      slot: conversationSlot[3],
      ...(conversationSlot[4] ? { rootConversationId: conversationSlot[4] } : {}),
    };
  const fresh = hash.match(NEW_CHAT_HASH);
  if (fresh) return { kind: "new-chat", agentId: fresh[1] };
  const workspace = hash.match(WORKSPACE_HASH);
  /* Connectors stood on a workspace tab once, so links to that address exist outside this code;
     the address keeps answering with the section that holds the same screen. */
  if (workspace && workspace[1] === "connectors") {
    const place = parsePlace(workspace[2]);
    if (!place) return { kind: "bad-link" };
    return { kind: "section", section: "connectors", place };
  }
  if (workspace && isWorkspaceTab(workspace[1])) {
    const place = parsePlace(workspace[2]);
    if (!place) return { kind: "bad-link" };
    return { kind: "workspace", view: workspace[1], place };
  }
  const section = hash.match(SECTION_HASH);
  if (section && isSection(section[1])) {
    const place = parsePlace(section[2]);
    if (!place) return { kind: "bad-link" };
    return { kind: "section", section: section[1], place };
  }
  const agent = hash.match(AGENT_HASH);
  if (agent) {
    const place = parsePlace(agent[2]);
    if (!place) return { kind: "bad-link" };
    return { kind: "agent", agentId: agent[1], place };
  }
  return { kind: "home" };
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

export function chatHash(conversationId: string, slot?: string): string {
  return CHAT_PREFIX + conversationId + (slot ? "?slot=" + encodeURIComponent(slot) : "");
}

export function newChatHash(agentId: string): string {
  return "#/new/" + agentId;
}

export function agentHash(agentId: string, place: WorkspacePlace = {}): string {
  return "#/agents/" + agentId + serializePlace(place);
}

export function workspaceHash(view: WorkspaceTab, place: WorkspacePlace = {}): string {
  return "#/workspace/" + view + serializePlace(place);
}

export function sectionHash(section: Section, place: WorkspacePlace = {}): string {
  return "#/" + section + serializePlace(place);
}
