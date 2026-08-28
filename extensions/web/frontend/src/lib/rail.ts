import type { Agent, OwnedConversation } from "@/lib/types";

export type ChatRow = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
};

/** The permalink resolve, and — for a conversation another surface holds, which has no chat row
 *  to route by — that conversation, naming the agent it ran under. */
export type ChatsPayload = { chats: ChatRow[]; conversation?: OwnedConversation };

/** One row of the conversation kind's member listing — the rail's read. The row's `name` is the
 *  conversation id, and the index adds the agent beside the kind's own fields. */
export type ConversationRow = {
  name: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
};

export type ConversationsPayload = { objects: ConversationRow[]; next_cursor?: string | null };

export function chatRows(payload: ConversationsPayload): ChatRow[] {
  return payload.objects.map((row) => ({
    conversation_id: row.name,
    agent_id: row.agent_id,
    agent_name: row.agent_name,
    title: row.title,
    last_at: row.last_at,
    surface: row.surface,
    surface_label: row.surface_label,
    mine: row.mine,
    speaker: row.speaker,
  }));
}

const HELD_SIDEBAR = "sidebar";

/** Whether the sidebar stands folded to its glyph rail. The shell opens on the rail: the sidebar is
 *  a place a member goes to reach another screen, not the screen they came for, so the width it
 *  takes belongs to the screen until they ask for it — and once they have asked, that choice is
 *  theirs on every load after. */
export function heldSidebar(): boolean {
  return localStorage.getItem(HELD_SIDEBAR) !== "expanded";
}

export function holdSidebar(collapsed: boolean): void {
  localStorage.setItem(HELD_SIDEBAR, collapsed ? "collapsed" : "expanded");
}

const HELD_PINNED = "pinned-rows";

/** What the member pinned into the sidebar — apps by id, in the order they pinned them. A browser
 *  holding nothing (`null`) has never had a pin touched, which is not the same as one holding an
 *  empty set: until then the workspace's shipped apps stand pinned, so the sidebar arrives holding
 *  its own destinations. A stored id no live agent answers — an app since removed — resolves to
 *  nothing rather than a row. */
export function heldPinned(): string[] | null {
  const held = localStorage.getItem(HELD_PINNED);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdPinned(pinned: string[]): void {
  localStorage.setItem(HELD_PINNED, pinned.join("\n"));
}

/** The apps in the order the sidebar draws them: the pinned ones in the order the member pinned
 *  them, then the rest by the app that worked most recently, and last the apps that have never
 *  worked, by name. A pin is a place the member put a row, so it holds that place whatever the app
 *  has been doing since; below the pins the column answers to the workspace instead, where an app
 *  that ran this morning is nearer the member's day than one that ran in March, and an app that
 *  has done nothing at all has only its name to stand by. Two apps sharing a moment keep the order
 *  they arrived in, so a read answering for neither settles the column rather than shuffling it.
 *  Whether work in flight is a moment now is the caller's read, which is why the moment arrives as
 *  a lookup. */
export function appOrder(
  apps: Agent[],
  pinned: string[],
  lastActiveAt: (agentId: string) => number | null,
): Agent[] {
  const byId = new Map(apps.map((app) => [app.id, app]));
  const stood = pinned.map((id) => byId.get(id)).filter((app) => app !== undefined);
  const drawn = new Set(stood);
  const active: { app: Agent; at: number }[] = [];
  const never: Agent[] = [];
  for (const app of apps) {
    if (drawn.has(app)) continue;
    const at = lastActiveAt(app.id);
    if (at === null) never.push(app);
    else active.push({ app, at });
  }
  active.sort((left, right) => right.at - left.at);
  never.sort((left, right) => left.name.localeCompare(right.name));
  return stood.concat(
    active.map((row) => row.app),
    never,
  );
}

/** The apps in the order the sidebar draws them, with work in flight at the top: an app that is
 *  working rises, and nothing else moves — the others keep the order `appOrder` gave them, and one
 *  arriving from below pushes them down by a row rather than reshuffling them. Work is the one fact
 *  worth the top of the column, and it is worth it for an app the member never pinned. Working is
 *  the caller's read, so an app's own status governs this and nothing here has to ask for it.
 *
 *  Every app the workspace has is drawn. The list scrolls inside the height `--size-apps-open`
 *  allows, so a long one costs the column nothing — and an app the list refused to draw is one the
 *  member has no way to reach, which is what a pin they cannot see cannot be undone from. */
export function appLadder(apps: Agent[], working: (agentId: string) => boolean): Agent[] {
  const busy = apps.filter((app) => working(app.id));
  const risen = new Set(busy.map((app) => app.id));
  return busy.concat(apps.filter((app) => !risen.has(app.id)));
}

const HELD_SECTIONS_SHUT = "sections-shut";

/** The sidebar sections the member has folded shut, by name, held across sessions. A section is a
 *  place they keep or put away; one they put away stays away on the next load, because a column
 *  narrowed to the part someone works from would widen again on every reload otherwise. A browser
 *  holding nothing has folded none, which is the sidebar whole. */
export function heldSectionsShut(): string[] {
  return (localStorage.getItem(HELD_SECTIONS_SHUT) ?? "").split("\n").filter(Boolean);
}

export function holdSectionsShut(shut: string[]): void {
  localStorage.setItem(HELD_SECTIONS_SHUT, shut.join("\n"));
}

export function stampIso(at: Date): string {
  return at.toISOString();
}

function moment(row: ChatRow): number {
  const at = new Date(row.last_at).getTime();
  return Number.isNaN(at) ? 0 : at;
}

export function bumpChat(rows: ChatRow[], conversationId: string, at: Date): ChatRow[] {
  const bumped = rows.map((row) =>
    row.conversation_id === conversationId ? { ...row, last_at: stampIso(at) } : row,
  );
  bumped.sort((a, b) => moment(b) - moment(a));
  return bumped;
}

export function mergeChats(fetched: ChatRow[], held: ChatRow[]): ChatRow[] {
  const known = new Set(fetched.map((row) => row.conversation_id));
  const merged = fetched.concat(held.filter((row) => !known.has(row.conversation_id)));
  merged.sort((a, b) => moment(b) - moment(a));
  return merged;
}
