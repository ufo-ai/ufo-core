import { useRef, useState } from "react";

import { getJson } from "@/lib/api";
import type { Message } from "@/lib/types";

export type EarlierPage = { index: number; messages: Message[] };

/** Older pages of a compacted conversation, held above the tail the transcript already states.
 *  `pages` is what has landed, oldest first; `more` says another page stands above them; `load`
 *  brings it in. A failed load stops asking on its own and waits for `load` to be called again —
 *  the row that watches the scroll would otherwise retry forever against the same answer. */
export type EarlierMessages = {
  pages: EarlierPage[];
  more: boolean;
  loading: boolean;
  failed: boolean;
  load: () => void;
};

/** Pages a conversation's compacted-away messages in from `<path>/<index>`, newest page first.
 *  `above` is the transcript's own `earlier` — the page standing over the tail — and each page's
 *  response names the one over it, so the chain is the server's to state and a record the
 *  transcript never reflected is never asked for. A transcript that restates a different `above`
 *  has compacted again since the pages loaded, so they no longer abut the tail and are dropped
 *  rather than drawn around a gap. */
export function useEarlierMessages(path: string | null, above: number): EarlierMessages {
  const [held, setHeld] = useState<{ above: number; next: number; pages: EarlierPage[] }>({
    above,
    next: above,
    pages: [],
  });
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const inflight = useRef(false);
  if (held.above !== above) setHeld({ above, next: above, pages: [] });
  const next = held.next;
  const load = async () => {
    if (inflight.current || path === null || next < 1) return;
    inflight.current = true;
    setFailed(false);
    setLoading(true);
    const result = await getJson<{ messages: Message[]; earlier?: number }>(path + "/" + next);
    inflight.current = false;
    setLoading(false);
    if (!result.ok) {
      setFailed(true);
      return;
    }
    setHeld((current) =>
      current.above === above && current.next === next
        ? {
            ...current,
            next: result.payload.earlier ?? 0,
            pages: [{ index: next, messages: result.payload.messages }, ...current.pages],
          }
        : current,
    );
  };
  return {
    pages: held.pages,
    more: path !== null && next >= 1,
    loading,
    failed,
    load: () => void load(),
  };
}
