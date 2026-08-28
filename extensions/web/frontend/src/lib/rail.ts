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
 *  them, then every other app by name. A pin is a place the member put a row, so it holds that place
 *  whatever the app has been doing since; below the pins the column is alphabetical, which is the
 *  one order a member can predict — they know the app's name before they know when it last ran, and
 *  a column that reordered itself as apps worked moved the row they were reaching for.
 *
 *  A stored pin no live app answers — an app since removed — resolves to nothing rather than a row.
 *
 *  Every app the workspace has is drawn. The list scrolls inside the height `--size-apps-open`
 *  allows, so a long one costs the column nothing — and an app the list refused to draw is one the
 *  member has no way to reach, which is what a pin they cannot see cannot be undone from. */
export function appOrder(apps: Agent[], pinned: string[]): Agent[] {
  const byId = new Map(apps.map((app) => [app.id, app]));
  const stood = pinned.map((id) => byId.get(id)).filter((app) => app !== undefined);
  const drawn = new Set(stood);
  const rest = apps.filter((app) => !drawn.has(app));
  rest.sort((left, right) => left.name.localeCompare(right.name));
  return stood.concat(rest);
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
