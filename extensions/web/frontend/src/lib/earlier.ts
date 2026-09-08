import { useRef, useState } from "react";

import { getJson } from "@/lib/api";
import type { Message } from "@/lib/types";

export type EarlierPage = { cursor: string; messages: Message[] };

export type EarlierMessages = {
  pages: EarlierPage[];
  more: boolean;
  loading: boolean;
  failed: boolean;
  load: () => void;
};

/** A transcript that restates a different root has compacted again since the pages loaded, so they no
 *  longer abut the tail and are dropped rather than drawn around a gap. */
export function useEarlierMessages(path: string | null, root: string | null): EarlierMessages {
  const [held, setHeld] = useState<{
    root: string | null;
    next: string | null;
    pages: EarlierPage[];
  }>({
    root,
    next: root,
    pages: [],
  });
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const inflight = useRef(false);
  if (held.root !== root) setHeld({ root, next: root, pages: [] });
  const next = held.next;
  const load = async () => {
    if (inflight.current || path === null || next === null) return;
    inflight.current = true;
    setFailed(false);
    setLoading(true);
    const result = await getJson<{ messages: Message[]; earlier_cursor?: string }>(
      path + "?cursor=" + encodeURIComponent(next),
    );
    inflight.current = false;
    setLoading(false);
    if (!result.ok) {
      setFailed(true);
      return;
    }
    setHeld((current) =>
      current.root === root && current.next === next
        ? {
            ...current,
            next: result.payload.earlier_cursor ?? null,
            pages: [{ cursor: next, messages: result.payload.messages }, ...current.pages],
          }
        : current,
    );
  };
  return {
    pages: held.pages,
    more: path !== null && next !== null,
    loading,
    failed,
    load: () => void load(),
  };
}
