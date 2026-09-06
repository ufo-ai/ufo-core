import { agentName } from "@/lib/agentName";
import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE } from "@/lib/audience";
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

/** A run of rows the rail draws together. A group the member can fold states its heading; the
 *  recency ladder is the rail's own order rather than a set of places, so it is drawn as one
 *  unheaded run of rows and carries no label. */
export type RailGroup = { label: string | null; rows: ChatRow[] };

export type RailSort = "recency" | "agent";

const HELD_SORT = "rail-sort";

/** Which ladder the rail draws its rows in, held across sessions. */
export function heldRailSort(): RailSort {
  return localStorage.getItem(HELD_SORT) === "agent" ? "agent" : "recency";
}

export function holdRailSort(sort: RailSort): void {
  localStorage.setItem(HELD_SORT, sort);
}

/** The agent sort's groups. Rows bucket under the name as stored — that name is the agent's
 *  identity, and two agents whose names differ only in case are two agents — and the heading is
 *  that name drawn the way every other surface draws it. */
function groupChatsByAgent(rows: ChatRow[]): RailGroup[] {
  const buckets = new Map<string, ChatRow[]>();
  for (const row of rows) {
    buckets.set(row.agent_name, (buckets.get(row.agent_name) ?? []).concat(row));
  }
  return [...buckets.entries()].map(([name, grouped]) => ({
    label: agentName(name),
    rows: grouped,
  }));
}

export type RailShown = { terminal: boolean; slack: boolean; imessage: boolean };

export const RAIL_SHOWN_OPTIONS: { surface: keyof RailShown; label: string }[] = [
  { surface: "terminal", label: "Terminal" },
  { surface: "slack", label: "Slack" },
  { surface: "imessage", label: "iMessage" },
];

const HELD_SHOWN = "rail-shown";

/** Which surfaces beside the portal's own the rail admits, held across sessions. A Slack thread, a
 *  terminal session and an iMessage exchange are conversations the member had somewhere else, so
 *  each is admitted only once they name it, and a browser holding nothing admits none. */
export function heldRailShown(): RailShown {
  const held = (localStorage.getItem(HELD_SHOWN) ?? "").split(",");
  return {
    terminal: held.includes("terminal"),
    slack: held.includes("slack"),
    imessage: held.includes("imessage"),
  };
}

export function holdRailShown(shown: RailShown): void {
  const named = RAIL_SHOWN_OPTIONS.filter((option) => shown[option.surface]);
  localStorage.setItem(HELD_SHOWN, named.map((option) => option.surface).join(","));
}

const HELD_SHUT = "rail-shut";

/** The groups the member has shut, by label, held across sessions — a rail narrowed to the ladders
 *  someone works from would widen again on every reload otherwise. A label is the key under either
 *  sort, since an agent's name and a date bucket's name are both labels. A browser holding nothing
 *  has never had a heading clicked, which is not the same as one holding an empty set. */
export function heldRailShut(): string[] | null {
  const held = localStorage.getItem(HELD_SHUT);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdRailShut(shut: string[]): void {
  localStorage.setItem(HELD_SHUT, shut.join("\n"));
}

/** Which groups stand shut. Until the member has touched a heading the rail opens its first group
 *  and shuts the rest: the conversations someone is working from are the recent ones, and the rest
 *  of the history is a list they ask for. The unheaded run of rows counts as a group here and can
 *  never be shut, so a rail led by it opens nothing else. The first click writes that whole set
 *  down, so from then on the member's own set governs — including the set that holds nothing shut.
 *  A label the rail has stopped drawing stays in the set and governs nothing until it is drawn
 *  again. */
export function railShut(held: string[] | null, labels: (string | null)[]): string[] {
  return held ?? labels.slice(1).filter((label) => label !== null);
}

const HELD_SIDEBAR = "sidebar";

/** Whether the sidebar stands folded to its glyph rail. The shell opens with the sidebar open: a
 *  member arriving for the first time sees the conversations and apps by name rather than a column
 *  of glyphs. A browser holding a choice keeps it: once the member has folded the sidebar, that
 *  choice is theirs on every load after. */
export function heldSidebar(): boolean {
  return localStorage.getItem(HELD_SIDEBAR) === "collapsed";
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

const HELD_CHATS_PINNED = "pinned-chats";
const HELD_CHATS_ARCHIVED = "archived-chats";

/** The conversations the member pinned to the head of the rail, by id, newest pin first. A pin is a
 *  place the member put a thread, so it holds that place however long the thread has been quiet. */
export function heldPinnedChats(): string[] {
  return (localStorage.getItem(HELD_CHATS_PINNED) ?? "").split("\n").filter(Boolean);
}

export function holdPinnedChats(pinned: string[]): void {
  localStorage.setItem(HELD_CHATS_PINNED, pinned.join("\n"));
}

/** The conversations the member put away, by id. An archived thread leaves the rail and is read in
 *  the archive, which is the same rail under one heading — it is put away, never deleted. */
export function heldArchivedChats(): string[] {
  return (localStorage.getItem(HELD_CHATS_ARCHIVED) ?? "").split("\n").filter(Boolean);
}

export function holdArchivedChats(archived: string[]): void {
  localStorage.setItem(HELD_CHATS_ARCHIVED, archived.join("\n"));
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

/** The apps drawn while the list stands collapsed, and the tail the `More` row reveals. */
export type AppRun = { shown: Agent[]; more: Agent[] };

/** How many apps the sidebar states before the rest wait behind `More`. The column is shared with
 *  the member's conversations, so the list takes a run of it and no more. Opened, it draws every
 *  app the workspace has and scrolls inside the height `--size-apps-open` allows — the tail is
 *  never cut, because an app the list refused to draw is one the member has no way to reach. */
const APPS_SHOWN = 8;

/** Where the collapsed list ends, and what work in flight does to it.
 *
 *  The cut is a count, not a judgement about any one app: the same number of rows stands whatever
 *  the workspace is doing, so the column holds still. A run that admitted apps for being busy — or
 *  that stood for the pinned ones alone — would resize itself as work started and as the member
 *  pinned, and the list would collapse under them at the moment they acted on it. Pinning moves an
 *  app up the order; it does not decide what the sidebar draws.
 *
 *  An app that is working rises to the top, and nothing else moves: the others keep their order
 *  under it, and one arriving from the tail pushes them down by a row rather than reshuffling them.
 *  Work is the one fact worth the top of the column, and it is worth reaching for an app the member
 *  never pinned. Working is the caller's read, so an app's own status governs this and nothing here
 *  has to ask for it. */
export function appRun(apps: Agent[], working: (agentId: string) => boolean): AppRun {
  const busy = apps.filter((app) => working(app.id));
  const risen = new Set(busy.map((app) => app.id));
  const ladder = busy.concat(apps.filter((app) => !risen.has(app.id)));
  return { shown: ladder.slice(0, APPS_SHOWN), more: ladder.slice(APPS_SHOWN) };
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

const HELD_APPS_EXPANDED = "apps-expanded";

/** Whether the member has opened the apps list past the run the sidebar draws on its own. The
 *  column carries their conversations as well, so the apps take a run of it and the rest wait
 *  behind a row; a member who asked for the whole set asked about their own workspace, and that
 *  answer holds on every load after. */
export function heldAppsExpanded(): boolean {
  return localStorage.getItem(HELD_APPS_EXPANDED) === "expanded";
}

export function holdAppsExpanded(expanded: boolean): void {
  localStorage.setItem(HELD_APPS_EXPANDED, expanded ? "expanded" : "collapsed");
}

function admits(row: ChatRow, shown: RailShown): boolean {
  if (row.surface === UFO_SURFACE) return shown.terminal;
  if (row.surface === SLACK_SURFACE) return shown.slack;
  if (row.surface === IMESSAGE_SURFACE) return shown.imessage;
  return true;
}

const OTHER_MEMBERS = "Other members";

/** What the member did to the rail's own rows: the threads they pinned, and the ones they put
 *  away. Both are ids rather than rows, because a rail read answers the same conversation under a
 *  new title and a new moment while the member's mark on it stands. */
export type RailMarks = { pinned: string[]; archived: string[] };

export const NO_MARKS: RailMarks = { pinned: [], archived: [] };

/** The rail's groups, in the order it draws them. The member's own conversations take the ladder
 *  the sort names — an app's name over each run, or, by recency, one unheaded run in the order the
 *  rows already stand in. A date the rail can state in the row's own words buys nothing as a
 *  heading, and five of them turn a column of a dozen conversations into a column of headings.
 *  The readable ones their colleagues are in follow as one group at the foot — never subdivided,
 *  and by recency under either sort: a sidebar column holds two heading weights, not three, and a
 *  colleague's thread is read for what happened lately in it. A group is drawn only when it holds
 *  a row.
 *
 *  The filter narrows the groups and never the rail itself: a permalink to a Slack thread opens it
 *  whether or not the rail is admitting Slack.
 *
 *  What the member marked governs before any sort does. The threads they pinned lead the column
 *  they already stand in rather than a column of their own: a pin says `keep this where I can
 *  reach it`, and the answer to that is the top of the list the member is reading, not a second
 *  heading to read past. The ones they put away leave the column altogether and are read in the
 *  chat app's own archive, so a member reaches them without the column carrying them. */
export function railGroups(
  rows: ChatRow[],
  sort: RailSort,
  shown: RailShown,
  marks: RailMarks = NO_MARKS,
): RailGroup[] {
  const admitted = rows.filter((row) => admits(row, shown));
  const archived = new Set(marks.archived);
  const live = admitted.filter((row) => !archived.has(row.conversation_id));
  const pins = new Set(marks.pinned);
  const led = live
    .filter((row) => pins.has(row.conversation_id))
    .concat(live.filter((row) => !pins.has(row.conversation_id)));
  const own = led.filter((row) => row.mine);
  const theirs = led.filter((row) => !row.mine);
  const grouped =
    sort === "agent" ? groupChatsByAgent(own) : own.length ? [{ label: null, rows: own }] : [];
  return theirs.length ? grouped.concat({ label: OTHER_MEMBERS, rows: theirs }) : grouped;
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
