export const AGENT_TABS = [
  "overview",
  "conversations",
  "tasks",
  "connections",
  "skills",
  "usage",
] as const;

export const WORKSPACE_TABS = [
  "team",
  "sources",
  "credentials",
  "memory",
  "artifacts",
  "sites",
  "usage",
] as const;

export const SUBAGENT_TABS = ["overview", "conversations", "skills"] as const;

export type AgentTab = (typeof AGENT_TABS)[number];
export type SubagentTab = (typeof SUBAGENT_TABS)[number];
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

export type PlaceStep = "push" | "replace" | "back";

export type WorkspacePlace = {
  kind?: string;
  after?: string;
  q?: string;
  chip?: string;
  open?: string;
};

export type Route =
  | { kind: "home" }
  | { kind: "chat"; conversationId: string }
  | { kind: "new-chat"; agentId: string }
  | { kind: "agents" }
  | { kind: "agent"; agentId: string; tab: AgentTab }
  | { kind: "subagent"; name: string; tab: SubagentTab }
  | { kind: "workspace"; view: WorkspaceTab; place: WorkspacePlace }
  | { kind: "admin" };

const CHAT_HASH = /^#\/c\/([0-9a-f-]{36})$/;
const NEW_CHAT_HASH = /^#\/new\/([0-9a-f-]{36})$/;
const AGENT_HASH = /^#\/agents\/([0-9a-f-]{36})(?:\/(\w+))?$/;
const SUBAGENT_HASH = /^#\/subagents\/(\w+)(?:\/(\w+))?$/;
const WORKSPACE_HASH = /^#\/workspace\/([\w-]+)(?:\?(.*))?$/;

const PLACE_KEYS = ["kind", "after", "q", "chip", "open"] as const;

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

export function parseHash(hash: string): Route {
  if (hash === "#/admin") return { kind: "admin" };
  if (hash === "#/agents") return { kind: "agents" };
  const chat = hash.match(CHAT_HASH);
  if (chat) return { kind: "chat", conversationId: chat[1] };
  const fresh = hash.match(NEW_CHAT_HASH);
  if (fresh) return { kind: "new-chat", agentId: fresh[1] };
  const workspace = hash.match(WORKSPACE_HASH);
  if (workspace && isWorkspaceTab(workspace[1])) {
    return { kind: "workspace", view: workspace[1], place: parsePlace(workspace[2]) };
  }
  const agent = hash.match(AGENT_HASH);
  if (agent) {
    return { kind: "agent", agentId: agent[1], tab: isAgentTab(agent[2]) ? agent[2] : "overview" };
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

export function chatHash(conversationId: string): string {
  return "#/c/" + conversationId;
}

export function newChatHash(agentId: string): string {
  return "#/new/" + agentId;
}

export function agentHash(agentId: string, tab: AgentTab): string {
  return "#/agents/" + agentId + (tab === "overview" ? "" : "/" + tab);
}

export function subagentHash(name: string, tab: SubagentTab): string {
  return "#/subagents/" + name + (tab === "overview" ? "" : "/" + tab);
}

export function workspaceHash(view: WorkspaceTab, place: WorkspacePlace = {}): string {
  return "#/workspace/" + view + serializePlace(place);
}
