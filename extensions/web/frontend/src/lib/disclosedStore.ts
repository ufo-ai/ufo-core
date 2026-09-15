import { useSyncExternalStore } from "react";

/** The disclosures acknowledged in this session, held outside every pane that draws one: a listing
 *  answers `readable` from audience membership alone, so a remounted pane would draw the
 *  acknowledgement over a transcript already opened and record a second disclosure for one act. */
const DISCLOSED = new Set<string>();

const listeners = new Set<() => void>();

export function markDisclosed(conversationId: string): void {
  if (DISCLOSED.has(conversationId)) return;
  DISCLOSED.add(conversationId);
  for (const listener of [...listeners]) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useDisclosed(conversationId: string | null | undefined): boolean {
  return useSyncExternalStore(
    subscribe,
    () => conversationId != null && DISCLOSED.has(conversationId),
  );
}

export function resetDisclosedStore(): void {
  DISCLOSED.clear();
  listeners.clear();
}
