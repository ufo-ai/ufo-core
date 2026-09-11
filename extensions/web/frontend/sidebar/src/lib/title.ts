import { agentName } from "@/lib/agentName";
import { isPortalChat } from "@/lib/audience";
import type { ChatRow } from "@/lib/rail";
import { agentHash, newChatHash, type Route } from "@/lib/route";
import type { Agent, OwnedConversation } from "@/lib/types";
import { SECTION_VIEWS, TASK_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";

export const APP_STORE_TITLE = "App Store";

const PRODUCT = "ufo";
const TRAIL = " · ";
const NEW_CONVERSATION = "New conversation";
const FIRST_RUN = "Set up this workspace";
const WORKSPACE = "Workspace";
const TASKS = "Tasks";
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

/** A tab truncates from the right, so the word that tells two of them apart stands first. */
export function titled(...parts: string[]): string {
  return [...parts, PRODUCT].join(TRAIL);
}

export function pageTitle(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): string {
  return titled(...trail(route, agents, rows, linked, main).map((step) => step.label));
}

export function pageCrumb(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): Crumb | undefined {
  return trail(route, agents, rows, linked, main)[1];
}

function trail(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): Crumb[] {
  return where(route, agents, rows, linked, main).filter((step): step is Crumb =>
    Boolean(step?.label),
  );
}

/** The `never` at the end is what makes a route the table gains a compile error here, instead of a tab
 *  that says `undefined`. */
function where(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): (Crumb | undefined)[] {
  const named = (agentId: string) => {
    const found = agents.find((agent) => agent.id === agentId);
    return found ? agentCrumb(found) : undefined;
  };
  switch (route.kind) {
    case "home":
      return [{ label: NEW_CONVERSATION }, main ? agentCrumb(main) : undefined];
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
    case "agents":
      return [{ label: APPS }];
    case "store":
      return [{ label: APP_STORE_TITLE }];
    case "agent":
      return [named(route.agentId)];
    case "agent-setup":
      return [{ label: SETUP }, named(route.agentId)];
    case "tasks":
      return [{ label: TASK_VIEWS[route.view].label }, { label: TASKS }];
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
