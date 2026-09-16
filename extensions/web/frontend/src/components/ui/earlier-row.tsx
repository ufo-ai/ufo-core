import { useEffect, useLayoutEffect, useRef } from "react";

import { Marker, MarkerContent } from "@/components/ui/marker";
import { MessageScrollerItem } from "@/components/ui/message-scroller";
import type { EarlierMessages } from "@/lib/earlier";

export function scrollerOf(node: Element): Element {
  for (let held = node.parentElement; held !== null; held = held.parentElement) {
    const overflow = getComputedStyle(held).overflowY;
    if (overflow === "auto" || overflow === "scroll") return held;
  }
  return document.scrollingElement ?? document.documentElement;
}

function placeOf(node: Element, scroller: Element): number {
  const top = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  return scroller === document.scrollingElement ? top : top + scroller.scrollTop;
}

export function EarlierRow({ earlier }: { earlier: EarlierMessages }) {
  const row = useRef<HTMLDivElement>(null);
  const held = useRef<{ scroller: Element; anchor: Element; place: number } | null>(null);
  const load = () => {
    const node = row.current;
    if (node === null) return;
    const anchor = node.nextElementSibling;
    if (anchor !== null) {
      const scroller = scrollerOf(node);
      held.current = { scroller, anchor, place: placeOf(anchor, scroller) };
    }
    earlier.load();
  };
  const latest = useRef(load);
  latest.current = load;
  const watching = earlier.more && !earlier.loading && !earlier.failed;
  useEffect(() => {
    const node = row.current;
    if (!watching || node === null) return;
    const watcher = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) latest.current();
    });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [watching, earlier.pages.length]);
  useLayoutEffect(() => {
    const kept = held.current;
    if (kept === null) return;
    held.current = null;
    kept.scroller.scrollTop += placeOf(kept.anchor, kept.scroller) - kept.place;
  }, [earlier.pages.length]);
  if (earlier.failed) {
    return (
      <MessageScrollerItem ref={row}>
        <Marker render={<button type="button" onClick={load} />}>
          <MarkerContent>Couldn't load earlier messages — retry</MarkerContent>
        </Marker>
      </MessageScrollerItem>
    );
  }
  if (earlier.loading || earlier.more) {
    return (
      <MessageScrollerItem ref={row}>
        <Marker>
          <MarkerContent>{earlier.loading ? "Loading earlier messages…" : null}</MarkerContent>
        </Marker>
      </MessageScrollerItem>
    );
  }
  return <MessageScrollerItem ref={row} />;
}
