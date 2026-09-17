import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE } from "@/lib/audience";
import type { Agent, Conversation, ConversationTurn } from "@/lib/types";

/** A type predicate, so the ink and rank tables over the rest stay total. */
export function live(turn: ConversationTurn): turn is "running" | "queued" {
  return turn === "running" || turn === "queued";
}

export type ChatState = "working" | "waiting" | "unread" | "idle";

/** What a chat is doing, in the order a member wants it: the agent is working, it is held for an
 *  answer only they can give, it moved while they were away, or it is resting. One reading feeds
 *  every mark and every order the portal draws a conversation in, so the rail, the table and the
 *  eye cannot disagree. */
export function chatState(row: Conversation): ChatState {
  if (live(row.turn)) return "working";
  if (row.turn === "parked") return "waiting";
  return row.unread ? "unread" : "idle";
}

export const CHAT_STATE_RANK: Record<ChatState, number> = {
  working: 0,
  waiting: 1,
  unread: 2,
  idle: 3,
};

/** A listed conversation on the wire: the kind's row, its fields flat under `name`, and the agent
 *  the read ran under. */
export type ConversationRow = Omit<Conversation, "conversation_id"> & { name: string };

export type ConversationsPayload = {
  objects: ConversationRow[];
  next_cursor?: string | null;
  /** The workspace holds more than the read was allowed to build, so the rows are not all of them. */
  cut?: boolean;
};

/** A resolved conversation on the wire — `objects/conversation/<id>` — the same fields under
 *  `status`, the object detail's envelope. */
export type ConversationDetailPayload = {
  name: string;
  agent_id: string;
  agent_name: string;
  status: Omit<Conversation, "conversation_id" | "agent_id" | "agent_name">;
};

function conversation(name: string, row: Omit<Conversation, "conversation_id">): Conversation {
  return {
    conversation_id: name,
    agent_id: row.agent_id,
    agent_name: row.agent_name,
    title: row.title,
    opening: row.opening,
    last_at: row.last_at,
    surface: row.surface,
    surface_label: row.surface_label,
    audience: row.audience,
    member_email: row.member_email,
    owner_email: row.owner_email,
    owner_name: row.owner_name,
    mine: row.mine,
    speaker: row.speaker,
    source: row.source,
    turn: row.turn,
    automation_kind: row.automation_kind,
    automation_name: row.automation_name,
    automation_title: row.automation_title,
    unread: row.unread,
    artifacts: row.artifacts,
    speakers: row.speakers,
    turn_count: row.turn_count,
    created_at: row.created_at,
    last_turn_at: row.last_turn_at,
    archived: row.archived,
    deleted: row.deleted,
    pinned: row.pinned,
    readable: row.readable,
    disclosable: row.disclosable,
    speakable: row.speakable,
  };
}

export function chatRows(payload: ConversationsPayload): Conversation[] {
  return payload.objects.map((row) => conversation(row.name, row));
}

export function resolvedConversation(payload: ConversationDetailPayload): Conversation {
  return conversation(payload.name, {
    ...payload.status,
    agent_id: payload.agent_id,
    agent_name: payload.agent_name,
  });
}

export type RailShown = {
  terminal: boolean;
  slack: boolean;
  imessage: boolean;
  automations: boolean;
};

export type RailSort = "recency" | "priority";

export const RAIL_SORT_OPTIONS: { sort: RailSort; label: string }[] = [
  { sort: "recency", label: "Recency" },
  { sort: "priority", label: "Priority" },
];

const HELD_SORT = "rail-sort";

/** An app page frame reaches this module, and a browser blocking third-party storage raises on the
 *  property itself rather than on the read. */
export function heldStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function heldRailSort(): RailSort {
  return heldStorage()?.getItem(HELD_SORT) === "priority" ? "priority" : "recency";
}

export function holdRailSort(sort: RailSort): void {
  heldStorage()?.setItem(HELD_SORT, sort);
}

export const RAIL_SHOWN_OPTIONS: { surface: keyof RailShown; label: string }[] = [
  { surface: "terminal", label: "Terminal" },
  { surface: "slack", label: "Slack" },
  { surface: "imessage", label: "iMessage" },
  { surface: "automations", label: "Automations" },
];

/** The key names the option set it spells: a comma list written for a shorter set cannot say
 *  what a member chose about an option that set did not hold, so it is not read. */
const HELD_SHOWN = "rail-shown-with-automations";

/** A browser holding nothing has never opened the filter, and the rail it draws is every
 *  conversation the member has; an empty set is a member who switched them all off. */
export function heldRailShown(): RailShown {
  const held = heldStorage()?.getItem(HELD_SHOWN) ?? null;
  if (held === null) return { terminal: true, slack: true, imessage: true, automations: true };
  const named = held.split(",");
  return {
    terminal: named.includes("terminal"),
    slack: named.includes("slack"),
    imessage: named.includes("imessage"),
    automations: named.includes("automations"),
  };
}

export function holdRailShown(shown: RailShown): void {
  const named = RAIL_SHOWN_OPTIONS.filter((option) => shown[option.surface]);
  heldStorage()?.setItem(HELD_SHOWN, named.map((option) => option.surface).join(","));
}

const HELD_SIDEBAR = "sidebar";

export function heldSidebar(): boolean {
  return heldStorage()?.getItem(HELD_SIDEBAR) === "collapsed";
}

export function holdSidebar(collapsed: boolean): void {
  heldStorage()?.setItem(HELD_SIDEBAR, collapsed ? "collapsed" : "expanded");
}

const HELD_PINNED = "pinned-rows";

/** A browser holding `null` has never had a pin touched, which is not the same as one holding an empty
 *  set. A stored id no live agent answers resolves to nothing rather than a row. */
export function heldPinned(): string[] | null {
  const held = heldStorage()?.getItem(HELD_PINNED) ?? null;
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdPinned(pinned: string[]): void {
  heldStorage()?.setItem(HELD_PINNED, pinned.join("\n"));
}

/** Two apps sharing a moment keep the order they arrived in, so a read answering for neither settles
 *  the column rather than shuffling it. */
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

export type AppRun = { shown: Agent[]; more: Agent[] };

/** Opened, the list scrolls inside `--size-apps-open` rather than cutting its tail: an app the list
 *  refused to draw is one the member has no way to reach. */
const APPS_SHOWN = 8;

/** The cut is a count, not a judgement about any one app: a run that admitted apps for being busy
 *  would resize itself as work started, and the list would collapse under the member as they acted. */
export function appRun(apps: Agent[], working: (agentId: string) => boolean): AppRun {
  const busy = apps.filter((app) => working(app.id));
  const risen = new Set(busy.map((app) => app.id));
  const ladder = busy.concat(apps.filter((app) => !risen.has(app.id)));
  return { shown: ladder.slice(0, APPS_SHOWN), more: ladder.slice(APPS_SHOWN) };
}

const HELD_SECTIONS_SHUT = "sections-shut";

export function heldSectionsShut(): string[] {
  return (heldStorage()?.getItem(HELD_SECTIONS_SHUT) ?? "").split("\n").filter(Boolean);
}

export function holdSectionsShut(shut: string[]): void {
  heldStorage()?.setItem(HELD_SECTIONS_SHUT, shut.join("\n"));
}

const HELD_APPS_EXPANDED = "apps-expanded";

export function heldAppsExpanded(): boolean {
  return heldStorage()?.getItem(HELD_APPS_EXPANDED) === "expanded";
}

export function holdAppsExpanded(expanded: boolean): void {
  heldStorage()?.setItem(HELD_APPS_EXPANDED, expanded ? "expanded" : "collapsed");
}

/** An automation's conversation is admitted on that alone: what drove it is the thing a member
 *  filters it out for, and the surface it reported on is beside the point. */
function admits(row: Conversation, shown: RailShown): boolean {
  if (row.automation_name !== null) return shown.automations;
  if (row.surface === UFO_SURFACE) return shown.terminal;
  if (row.surface === SLACK_SURFACE) return shown.slack;
  if (row.surface === IMESSAGE_SURFACE) return shown.imessage;
  return true;
}

/** The conversations the member is in — `mine` is the listing's one participation fact — so one
 *  shared with them that they never spoke in stands on the Home table and not here. Recency is the
 *  order they arrive in; priority is the Home table's own, off the one state reading, so the two
 *  screens do not rank one workspace two ways. */
export function railRows(rows: Conversation[], shown: RailShown, sort: RailSort): Conversation[] {
  const kept = rows.filter((row) => row.mine && admits(row, shown));
  if (sort === "recency") return kept;
  return [...kept].sort(
    (one, two) =>
      CHAT_STATE_RANK[chatState(one)] - CHAT_STATE_RANK[chatState(two)] || moment(two) - moment(one),
  );
}

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const WEEK_MS = 604_800_000;
const YEAR_MS = 31_536_000_000;

/** The rail's own stamp, narrower than the `Moment` the rest of the portal draws: the sidebar's
 *  width belongs to the titles, so a row spends one number and one letter on the age of its chat.
 *  The whole moment is still the row's to state, and the `time` it sits in carries it. A stamp
 *  ahead of now — a clock that disagrees with the server's — reads as `now` rather than as a
 *  negative age. */
export function railStamp(raw: string, now: Date): string {
  const at = new Date(raw).getTime();
  if (Number.isNaN(at)) return "";
  const span = Math.max(0, now.getTime() - at);
  if (span < MINUTE_MS) return "now";
  if (span < HOUR_MS) return Math.floor(span / MINUTE_MS) + "m";
  if (span < DAY_MS) return Math.floor(span / HOUR_MS) + "h";
  if (span < WEEK_MS) return Math.floor(span / DAY_MS) + "d";
  if (span < YEAR_MS) return Math.floor(span / WEEK_MS) + "w";
  return Math.floor(span / YEAR_MS) + "y";
}

export function stampIso(at: Date): string {
  return at.toISOString();
}

function moment(row: Conversation): number {
  const at = new Date(row.last_at).getTime();
  return Number.isNaN(at) ? 0 : at;
}

/** A conversation this tab has just admitted a turn into: its row moves to the top and its dot
 *  reads live, until the next listing read states the turn the engine holds. */
export function bumpChat(
  rows: Conversation[],
  conversationId: string,
  at: Date,
  turn: ConversationTurn,
): Conversation[] {
  const bumped = rows.map((row) =>
    row.conversation_id === conversationId ? { ...row, last_at: stampIso(at), turn } : row,
  );
  bumped.sort((a, b) => moment(b) - moment(a));
  return bumped;
}

/** A chat this tab has just opened: its mark stops reading unread as the member reaches it, ahead
 *  of the transcript read that moves the cursor the next listing answers from. */
export function readChat(rows: Conversation[], conversationId: string): Conversation[] {
  return rows.map((row) =>
    row.conversation_id === conversationId ? { ...row, unread: false } : row,
  );
}

export const CONVERSATION_KIND = "conversation";
export const ARCHIVE_ACTION = "archive_conversation";
export const UNARCHIVE_ACTION = "unarchive_conversation";
export const PIN_ACTION = "pin_conversation";
export const UNPIN_ACTION = "unpin_conversation";
export const DELETE_ACTION = "delete_conversation";

/** What each filing act leaves on the row it acted on. A screen draws the act from this table
 *  rather than from a read of the workspace: the verb is one round trip and every listing behind a
 *  chat screen is another, so a filing that waited for them took seconds to appear. */
export const FILING_MARKS: Record<string, Partial<Conversation>> = {
  [ARCHIVE_ACTION]: { archived: true },
  [UNARCHIVE_ACTION]: { archived: false },
  [PIN_ACTION]: { pinned: true },
  [UNPIN_ACTION]: { pinned: false },
  [DELETE_ACTION]: { deleted: true },
};

/** A chat filed out of the listing — archived or deleted: its row leaves, because the listing read
 *  behind the rail answers without it and `mergeChats` holds every row a read does not name. */
export function filedChat(rows: Conversation[], conversationId: string): Conversation[] {
  return rows.filter((row) => row.conversation_id !== conversationId);
}

/** A turn that ends restates its row's mark alone: its moment is the turn it started, so a landing
 *  turn must not reorder the rail under the member. */
export function turnedChat(rows: Conversation[], conversationId: string, turn: ConversationTurn): Conversation[] {
  return rows.map((row) => (row.conversation_id === conversationId ? { ...row, turn } : row));
}

export function mergeChats(fetched: Conversation[], held: Conversation[]): Conversation[] {
  const known = new Set(fetched.map((row) => row.conversation_id));
  const merged = fetched.concat(held.filter((row) => !known.has(row.conversation_id)));
  merged.sort((a, b) => moment(b) - moment(a));
  return merged;
}
