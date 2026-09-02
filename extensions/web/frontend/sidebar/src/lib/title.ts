import { agentName } from "@/lib/agentName";
import { isPortalChat } from "@/lib/audience";
import type { ChatRow } from "@/lib/rail";
import { agentHash, type Route } from "@/lib/route";
import type { Agent, OwnedConversation } from "@/lib/types";
import { SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";

const PRODUCT = "ufo";
const TRAIL = " · ";
const NEW_CONVERSATION = "New conversation";
const FIRST_RUN = "Set up this workspace";
const WORKSPACE = "Workspace";
const APPS = "Apps";
export const SETUP = "Set up";
const INVALID_LINK = "Invalid link";

/** One step of where the member is: what a surface is called, and the address it stands at where
 *  the step is a place of its own. A band draws the step above its own name as its crumb, which is
 *  why the address rides the step rather than a verb — a crumb to an address is navigation, and a
 *  step no address reaches is the landmark's name and nothing to press. */
export type Crumb = { label: string; at?: string };

/** The step an app is: its own name, at its own address. The trail names an app here, and a framed
 *  page's own crumb is this same step, so no two bands name one app two ways. */
export function agentCrumb(agent: { id: string; name: string }): Crumb {
  return { label: agentName(agent.name), at: agentHash(agent.id) };
}

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
  return titled(...trail(route, agents, rows, linked, main).map((step) => step.label));
}

/** The crumb a band draws before its own name, off the very trail the tab title is joined from: a
 *  band names the trail's innermost step, so its crumb is the step above that one — the same words
 *  the tab puts after the name. Where am I is derived once here, so the band and the tab cannot
 *  disagree about it. */
export function pageCrumb(
  route: Route,
  agents: Agent[],
  rows: ChatRow[],
  linked: Record<string, OwnedConversation>,
  main: Agent | null,
): Crumb | undefined {
  return trail(route, agents, rows, linked, main)[1];
}

/** The trail as every reader of it takes it: innermost first, and only the steps that came back
 *  with a name — an agent the roster does not hold names nothing, and a step saying nothing is no
 *  step in the path. */
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

/** Where the member is, one arm per kind the route table declares. The `never` at the end is what
 *  makes a route the table gains a compile error here, instead of a tab that says `undefined`. */
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
      /* A conversation the roster holds no app for — one carried on an extension's own surface — is
         named by the app all the same and stands at no address of its own, so the crumb to it is
         the name and nothing to press. */
      if (row) {
        return [
          { label: row.title },
          named(row.agent_id) ?? { label: agentName(row.agent_name) },
        ];
      }
      const held = linked[route.conversationId];
      return held ? [{ label: held.description }, agentCrumb(held.agent)] : [];
    }
    case "conversation-slot":
      return [{ label: route.slot }, named(route.agentId)];
    case "agents":
      return [{ label: APPS }];
    case "agent":
      return [named(route.agentId)];
    case "agent-setup":
      return [{ label: SETUP }, named(route.agentId)];
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
