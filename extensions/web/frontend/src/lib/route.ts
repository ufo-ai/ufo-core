export const AGENT_TABS = [
  "overview",
  "conversations",
  "scheduled",
  "connectors",
  "skills",
  "usage",
] as const;

export const WORKSPACE_TABS = ["team", "sources", "connectors", "credentials", "usage"] as const;

export const CUSTOMIZE_TABS = ["memory"] as const;

export const SUBAGENT_TABS = ["overview", "conversations", "skills"] as const;

export const SECTIONS = ["scheduled", "artifacts", "sites"] as const;

export type AgentTab = (typeof AGENT_TABS)[number];
export type SubagentTab = (typeof SUBAGENT_TABS)[number];
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];
export type Section = (typeof SECTIONS)[number];
export type CustomizeTab = (typeof CUSTOMIZE_TABS)[number];

export type PlaceStep = "push" | "replace" | "back";

export type WorkspacePlace = {
  kind?: string;
  after?: string;
  q?: string;
  chip?: string;
  open?: string;
  agent?: string;
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
  | { kind: "agents" }
  | { kind: "agent"; agentId: string; tab: AgentTab; place: WorkspacePlace }
  | {
      kind: "subagent";
      name: string;
      tab: SubagentTab;
      conversationId?: string;
      rootConversationId?: string;
    }
  | { kind: "workspace"; view: WorkspaceTab; place: WorkspacePlace }
  | { kind: "section"; section: Section; place: WorkspacePlace }
  | { kind: "customize"; view: CustomizeTab; place: WorkspacePlace }
  | { kind: "admin" }
  | { kind: "bad-link" };

const CHAT_PREFIX = "#/c/";
const CHAT_HASH = /^#\/c\/([0-9a-f-]{36})(?:\?(.*))?$/;
const CHAT_TARGET_PARAM = "c";
const ARTIFACT_TARGET_PARAM = "a";
const ARTIFACT_PATH_PREFIX = "/artifacts/";
const CONVERSATION_SLOT_HASH =
  /^#\/agents\/([0-9a-f-]{36})\/conversations\/([0-9a-f-]{36})\/slots\/([a-z][a-z0-9_-]{0,63})(?:\?root=([0-9a-f-]{36}))?$/;
const NEW_CHAT_HASH = /^#\/new\/([0-9a-f-]{36})$/;
const AGENT_HASH = /^#\/agents\/([0-9a-f-]{36})(?:\/(\w+))?(?:\?(.*))?$/;
const SUBAGENT_HASH = /^#\/subagents\/(\w+)(?:\/(\w+))?$/;
const SUBAGENT_CONVERSATION_HASH =
  /^#\/subagents\/(\w+)\/conversations\/([0-9a-f-]{36})(?:\?root=([0-9a-f-]{36}))?$/;
const WORKSPACE_HASH = /^#\/workspace\/([\w-]+)(?:\?(.*))?$/;
const SECTION_HASH = /^#\/([a-z][a-z-]*)(?:\?(.*))?$/;
const CUSTOMIZE_HASH = /^#\/customize\/([\w-]+)(?:\?(.*))?$/;

const PLACE_KEYS = ["kind", "after", "q", "chip", "open", "agent"] as const;

function parsePlace(raw: string | undefined): WorkspacePlace {
  if (!raw) return {};
  const params = new URLSearchParams(raw);
  const place: WorkspacePlace = {};
  for (const key of PLACE_KEYS) {
    const value = params.get(key);
    if (value) place[key] = value;
  }
  return place;
}

function serializePlace(place: WorkspacePlace): string {
  const params = new URLSearchParams();
  for (const key of PLACE_KEYS) {
    const value = place[key];
    if (value) params.set(key, value);
  }
  const raw = params.toString();
  return raw ? "?" + raw : "";
}

function isAgentTab(name: string | undefined): name is AgentTab {
  return AGENT_TABS.includes((name ?? "") as AgentTab);
}

function isSubagentTab(name: string | undefined): name is SubagentTab {
  return SUBAGENT_TABS.includes((name ?? "") as SubagentTab);
}

function isWorkspaceTab(name: string): name is WorkspaceTab {
  return WORKSPACE_TABS.includes(name as WorkspaceTab);
}

function isSection(name: string): name is Section {
  return SECTIONS.includes(name as Section);
}

function isCustomizeTab(name: string): name is CustomizeTab {
  return CUSTOMIZE_TABS.includes(name as CustomizeTab);
}

export function parseHash(hash: string): Route {
  if (hash === "#/admin") return { kind: "admin" };
  if (hash === "#/agents") return { kind: "agents" };
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
  if (workspace && isWorkspaceTab(workspace[1])) {
    return { kind: "workspace", view: workspace[1], place: parsePlace(workspace[2]) };
  }
  const customize = hash.match(CUSTOMIZE_HASH);
  if (customize && isCustomizeTab(customize[1])) {
    return { kind: "customize", view: customize[1], place: parsePlace(customize[2]) };
  }
  const section = hash.match(SECTION_HASH);
  if (section && isSection(section[1])) {
    return { kind: "section", section: section[1], place: parsePlace(section[2]) };
  }
  const agent = hash.match(AGENT_HASH);
  if (agent) {
    return {
      kind: "agent",
      agentId: agent[1],
      tab: isAgentTab(agent[2]) ? agent[2] : "overview",
      place: parsePlace(agent[3]),
    };
  }
  const run = hash.match(SUBAGENT_CONVERSATION_HASH);
  if (run) {
    return {
      kind: "subagent",
      name: run[1],
      tab: "conversations",
      conversationId: run[2],
      ...(run[3] ? { rootConversationId: run[3] } : {}),
    };
  }
  const subagent = hash.match(SUBAGENT_HASH);
  if (subagent) {
    return {
      kind: "subagent",
      name: subagent[1],
      tab: isSubagentTab(subagent[2]) ? subagent[2] : "overview",
    };
  }
  return { kind: "home" };
}

export function artifactTarget(search: string): string | null {
  const target = new URLSearchParams(search).get(ARTIFACT_TARGET_PARAM);
  return target && target.startsWith(ARTIFACT_PATH_PREFIX) ? target : null;
}

export function bootRoute(hash: string, search: string): Route {
  const route = parseHash(hash);
  if (route.kind !== "home") return route;
  const target = new URLSearchParams(search).get(CHAT_TARGET_PARAM);
  return target ? parseHash(chatHash(target)) : route;
}

export function chatHash(conversationId: string, slot?: string): string {
  return CHAT_PREFIX + conversationId + (slot ? "?slot=" + encodeURIComponent(slot) : "");
}

export function newChatHash(agentId: string): string {
  return "#/new/" + agentId;
}

export function agentHash(agentId: string, tab: AgentTab, place: WorkspacePlace = {}): string {
  return "#/agents/" + agentId + (tab === "overview" ? "" : "/" + tab) + serializePlace(place);
}

export function subagentHash(name: string, tab: SubagentTab): string {
  return "#/subagents/" + name + (tab === "overview" ? "" : "/" + tab);
}

/** One subagent run, and — where the member reached it from the conversation that spawned it —
 *  that conversation, which is the read the run is authorized through. */
export function subagentConversationHash(
  name: string,
  conversationId: string,
  rootConversationId?: string,
): string {
  const root = rootConversationId ? "?root=" + rootConversationId : "";
  return "#/subagents/" + name + "/conversations/" + conversationId + root;
}

export function workspaceHash(view: WorkspaceTab, place: WorkspacePlace = {}): string {
  return "#/workspace/" + view + serializePlace(place);
}

export function sectionHash(section: Section, place: WorkspacePlace = {}): string {
  return "#/" + section + serializePlace(place);
}

export function customizeHash(view: CustomizeTab, place: WorkspacePlace = {}): string {
  return "#/customize/" + view + serializePlace(place);
}
