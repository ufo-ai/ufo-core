export const AGENT_TABS = [
  "chat",
  "conversations",
  "overview",
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

export const WORKSPACE_LABELS: Record<WorkspaceTab, string> = {
  team: "Team",
  sources: "Sources",
  credentials: "Credentials",
  memory: "Memory",
  artifacts: "Artifacts",
  sites: "Sites",
  usage: "Usage",
};

export type AgentTab = (typeof AGENT_TABS)[number];
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

export type Route =
  | { kind: "agent"; agentId: string | null; tab: AgentTab }
  | { kind: "workspace"; view: WorkspaceTab }
  | { kind: "admin" };

const AGENT_HASH = /^#\/agents\/([0-9a-f-]{36})(?:\/(\w+))?$/;
const WORKSPACE_HASH = /^#\/workspace\/([\w-]+)$/;

function isAgentTab(name: string | undefined): name is AgentTab {
  return AGENT_TABS.includes((name ?? "") as AgentTab);
}

function isWorkspaceTab(name: string): name is WorkspaceTab {
  return WORKSPACE_TABS.includes(name as WorkspaceTab);
}

export function parseHash(hash: string): Route {
  if (hash === "#/admin") return { kind: "admin" };
  const workspace = hash.match(WORKSPACE_HASH);
  if (workspace && isWorkspaceTab(workspace[1])) {
    return { kind: "workspace", view: workspace[1] };
  }
  const agent = hash.match(AGENT_HASH);
  if (!agent) return { kind: "agent", agentId: null, tab: "chat" };
  return {
    kind: "agent",
    agentId: agent[1],
    tab: isAgentTab(agent[2]) ? agent[2] : "chat",
  };
}

export function agentHash(agentId: string, tab: AgentTab): string {
  return "#/agents/" + agentId + (tab === "chat" ? "" : "/" + tab);
}

export function workspaceHash(view: WorkspaceTab): string {
  return "#/workspace/" + view;
}
