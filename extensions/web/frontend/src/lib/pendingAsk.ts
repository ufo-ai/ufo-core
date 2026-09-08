export type PendingAsk = { text: string; send: boolean; meant: string | null };

export const BUILD_ASK =
  "Build this workspace its own version of your page. Load your homepage skill and follow it.";

const PENDING = new Map<string, PendingAsk>();

const WAITING = new Set<() => void>();

export function setPendingAsk(
  agentId: string,
  text: string,
  send: boolean,
  meant: string | null = null,
): void {
  PENDING.set(agentId, { text, send, meant });
  for (const wake of [...WAITING]) wake();
}

export function takePendingAsk(agentId: string, chatKey: string): PendingAsk | null {
  const held = PENDING.get(agentId) ?? null;
  if (held === null) return null;
  if ((held.meant ?? "new:" + agentId) !== chatKey) return null;
  PENDING.delete(agentId);
  return held;
}

export function watchPendingAsk(wake: () => void): () => void {
  WAITING.add(wake);
  return () => {
    WAITING.delete(wake);
  };
}
