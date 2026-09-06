/** One message a panel hands to an agent's new chat: the words, and whether the composer founding
 *  that chat sends them or leaves them standing for the member to send. It is the *new* chat that
 *  takes them — a conversation the member is already reading belongs to the same agent, and would
 *  otherwise say them into the thread they were leaving.
 *
 *  A panel never founds the conversation itself, either way. It owns an id nothing else holds: the
 *  rail never gains the row, the agent's conversation tab renders read-only, and a connect link the
 *  turn produces lives only on the live tail — so a reload loses it and the member has no place to
 *  answer. Handing the words to the chat screen keeps one path: the conversation is founded by the
 *  composer that is on the screen the member is looking at, like any other.
 *
 *  `send` turns on what the words commit, not on who composed them. The palette's row, the start
 *  screen's starters and the first run's opening line each only ask for something the conversation
 *  goes on to decide, so all three are said at once. An ask that commits an act the moment it lands
 *  is handed over unsent: a setup ask binds its grants to the speaker in that conversation, so the
 *  member reads what they are about to say before it becomes theirs.
 *
 *  It is held in memory and taken once. A draft would outlive the press and reappear later under a
 *  key the member never typed into. */
export type PendingAsk = { text: string; send: boolean; meant: string | null };

/** What the build press types on the member's behalf, into the composer they send it from. It names
 *  the skill and stops: the steps live in the skill, and an ask repeating them would be a second
 *  copy of the procedure that drifts the first time either changes.
 *
 *  Two presses say it — a shipped app's setup screen, and the header of an app that has no page
 *  yet — and both must say the same words, because the routing eval measures this exact string. */
export const BUILD_ASK =
  "Build this workspace its own version of your page. Load your homepage skill and follow it.";

const PENDING = new Map<string, PendingAsk>();

const WAITING = new Set<() => void>();

/** `meant` names the one composer the words are for, by its chat key — home can stand founding
 *  composers for the same agent, and an ask any of them could take would be taken by a lane while
 *  the navigation tears it down, the words sent into a screen the member just left. A setter
 *  routing somewhere other than the agent's new chat screen names the key it is routing to; null
 *  names that screen's own key, `new:<agentId>`, which is where every setter that passes it
 *  goes. */
export function setPendingAsk(
  agentId: string,
  text: string,
  send: boolean,
  meant: string | null = null,
): void {
  PENDING.set(agentId, { text, send, meant });
  for (const wake of [...WAITING]) wake();
}

/** The pending ask for this agent, removed as this composer reads it, or null when there is none —
 *  or when the ask is meant for a different composer than the one asking. */
export function takePendingAsk(agentId: string, chatKey: string): PendingAsk | null {
  const held = PENDING.get(agentId) ?? null;
  if (held === null) return null;
  if ((held.meant ?? "new:" + agentId) !== chatKey) return null;
  PENDING.delete(agentId);
  return held;
}

/** Called when an ask lands. A composer that is already mounted when the ask is set — the palette
 *  hands one from the chat screen itself — never mounts again to read it, so the text would sit in
 *  the map unread. */
export function watchPendingAsk(wake: () => void): () => void {
  WAITING.add(wake);
  return () => {
    WAITING.delete(wake);
  };
}
