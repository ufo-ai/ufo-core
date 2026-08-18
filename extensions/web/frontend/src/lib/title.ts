import { isPortalChat } from "@/lib/audience";
import type { ChatRow } from "@/lib/rail";
import type { Route } from "@/lib/route";
import type { Agent, OwnedConversation } from "@/lib/types";
import { AGENT_TAB_LABELS, SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";

const PRODUCT = "ufo";
const TRAIL = " · ";
const NEW_CONVERSATION = "New conversation";
const WORKSPACE = "Workspace";
const APPS = "Apps";
const ADMINISTRATION = "Administration";
const INVALID_LINK = "Invalid link";

/** What the browser tab says: where the member is, innermost first, then the page that holds it,
 *  ending in the product. A tab truncates from the right, so the word that tells two of them apart
 *  stands first, and every part is the name the page itself carries. */
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
  const trail = where(route, agents, rows, linked, main);
  return titled(...trail.filter((part): part is string => Boolean(part)));
}

function where(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): (string | undefined)[] {
  const named = (agentId: string) => agents.find((agent) => agent.id === agentId)?.name;
  switch (route.kind) {
    case "home":
      return [NEW_CONVERSATION, main?.name];
    case "new-chat":
      return [NEW_CONVERSATION, named(route.agentId)];
    case "chat": {
      const row = rows.find(
        (entry) => entry.conversation_id === route.conversationId && isPortalChat(entry.surface),
      );
      if (row) return [row.title, row.agent_name];
      const held = linked[route.conversationId];
      return held ? [held.description, held.agent.name] : [];
    }
    case "conversation-slot":
      return [route.slot, named(route.agentId)];
    case "agents":
      return [APPS];
    case "agent":
      return [AGENT_TAB_LABELS[route.tab], named(route.agentId)];
    case "workspace":
      return [WORKSPACE_VIEWS[route.view].label, WORKSPACE];
    case "section":
      return [SECTION_VIEWS[route.section].label];
    case "admin":
      return [ADMINISTRATION];
    case "bad-link":
      return [INVALID_LINK];
  }
}
