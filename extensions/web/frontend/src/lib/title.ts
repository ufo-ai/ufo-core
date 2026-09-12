import { agentName } from "@/lib/agentName";
import { isPortalChat } from "@/lib/audience";
import type { ChatRow } from "@/lib/rail";
import { agentHash, newChatHash, workspaceHash, type Route } from "@/lib/route";
import type { Agent, OwnedConversation } from "@/lib/types";
import { APP_CREATOR_TITLE } from "@/lib/wizard";
import { SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";

const PRODUCT = "ufo";
const TRAIL = " · ";
const HOME = "Home";
const NEW_CONVERSATION = "New conversation";
const FIRST_RUN = "Set up this workspace";
const WORKSPACE = "Workspace";
const AUTOMATIONS = "Automations";
const APPS = "Apps";
export const SETUP = "Set up";
const INVALID_LINK = "Invalid link";

export type Crumb = { label: string; at?: string };

export function agentCrumb(agent: { id: string; name: string }): Crumb {
  return { label: agentName(agent.name), at: agentHash(agent.id) };
}

function chatCrumb(agent: { id: string; name: string }): Crumb {
  return { label: agentName(agent.name), at: newChatHash(agent.id) };
}

export function titled(...parts: string[]): string {
  return [...parts, PRODUCT].join(TRAIL);
}

export function pageTitle(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
): string {
  return titled(...trail(route, agents, rows, linked).map((step) => step.label));
}

export function pageCrumb(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
): Crumb | undefined {
  return trail(route, agents, rows, linked)[1];
}

function trail(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
): Crumb[] {
  return where(route, agents, rows, linked).filter((step): step is Crumb =>
    Boolean(step?.label),
  );
}

function where(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
): (Crumb | undefined)[] {
  const named = (agentId: string) => {
    const found = agents.find((agent) => agent.id === agentId);
    return found ? agentCrumb(found) : undefined;
  };
  switch (route.kind) {
    case "home":
      return [{ label: HOME }];
    case "first-run":
      return [{ label: FIRST_RUN }];
    case "new-chat":
      return [{ label: NEW_CONVERSATION }, named(route.agentId)];
    case "chat": {
      const row = rows.find(
        (entry) => entry.conversation_id === route.conversationId && isPortalChat(entry.surface),
      );
      if (row) {
        const found = agents.find((agent) => agent.id === row.agent_id);
        return [
          { label: row.title },
          found ? chatCrumb(found) : { label: agentName(row.agent_name) },
        ];
      }
      const held = linked[route.conversationId];
      return held ? [{ label: held.description }, chatCrumb(held.agent)] : [];
    }
    case "conversation-slot":
      return [{ label: route.slot }, named(route.agentId)];
    case "builder":
      return [
        { label: APP_CREATOR_TITLE },
        { label: APPS, at: workspaceHash("apps") },
        { label: WORKSPACE },
      ];
    case "agent":
      return [named(route.agentId)];
    case "agent-setup":
      return [{ label: SETUP }, named(route.agentId)];
    case "automations":
      return [{ label: AUTOMATIONS }];
    case "workspace":
      return [{ label: WORKSPACE_VIEWS[route.view].label }, { label: WORKSPACE }];
    case "section":
      return [{ label: SECTION_VIEWS[route.section]?.label ?? agentName(route.section) }];
    case "bad-link":
      return [{ label: INVALID_LINK }];
  }
  const missed: never = route;
  return missed;
}
