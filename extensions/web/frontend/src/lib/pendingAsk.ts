/** One message a panel hands to the composer instead of sending it.
 *
 *  A panel that founds its own conversation owns an id nothing else holds: the rail never gains the
 *  row, the agent's conversation tab renders read-only, and a connect link the turn produces lives
 *  only on the live tail — so a reload loses it and the member has no place to answer. Handing the
 *  text to the composer instead keeps one path: the member reads what will be sent, presses send,
 *  and the conversation is theirs like any other.
 *
 *  It is held in memory and taken once. A draft would outlive the press and reappear later under a
 *  key the member never typed into. */
const PENDING = new Map<string, string>();

export function setPendingAsk(agentId: string, text: string): void {
  PENDING.set(agentId, text);
}

/** The pending ask for this agent, removed as it is read, or "" when there is none. */
export function takePendingAsk(agentId: string): string {
  const held = PENDING.get(agentId) ?? "";
  PENDING.delete(agentId);
  return held;
}
