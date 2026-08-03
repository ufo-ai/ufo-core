const DRAFT_PREFIX = "ufo.chat-draft.";
const DRAFT_DEBOUNCE_MS = 400;
const DRAFT_MAX_CHARS = 20_000;

function store(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

const pending = new Map<string, string>();
let timer: ReturnType<typeof setTimeout> | null = null;

export function readDraft(chatId: string): string {
  return store()?.getItem(DRAFT_PREFIX + chatId) ?? "";
}

export function writeDraft(chatId: string, text: string): void {
  pending.set(chatId, text);
  if (timer) clearTimeout(timer);
  timer = setTimeout(flushDrafts, DRAFT_DEBOUNCE_MS);
}

export function clearDraft(chatId: string): void {
  pending.delete(chatId);
  store()?.removeItem(DRAFT_PREFIX + chatId);
}

export function flushDrafts(): void {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  const held = store();
  if (held !== null) {
    try {
      for (const [chatId, text] of pending) {
        if (text) held.setItem(DRAFT_PREFIX + chatId, text.slice(0, DRAFT_MAX_CHARS));
        else held.removeItem(DRAFT_PREFIX + chatId);
      }
    } catch {
      pending.clear();
      return;
    }
  }
  pending.clear();
}

export function installDraftFlush(): () => void {
  const onHidden = () => {
    if (document.visibilityState === "hidden") flushDrafts();
  };
  window.addEventListener("pagehide", flushDrafts);
  document.addEventListener("visibilitychange", onHidden);
  return () => {
    flushDrafts();
    window.removeEventListener("pagehide", flushDrafts);
    document.removeEventListener("visibilitychange", onHidden);
  };
}
