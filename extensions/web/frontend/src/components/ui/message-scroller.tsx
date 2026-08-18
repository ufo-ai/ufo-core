import type { ComponentProps } from "react";
import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
} from "@shadcn/react/message-scroller";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export { useMessageScroller };

/** The transcript's own scrolling. What a chat pane has to get right is not scrolling but the
 *  three places it must not move: a reply streaming in follows the foot only while the member is
 *  already there, older messages loaded in above hold the line being read exactly where it was,
 *  and a conversation opened cold lands at the end rather than scrolling there in front of the
 *  member. Those are decided here, once, rather than by a listener in every pane that draws a
 *  conversation. */
export function MessageScrollerProvider(
  props: ComponentProps<typeof MessageScrollerPrimitive.Provider>,
) {
  return <MessageScrollerPrimitive.Provider {...props} />;
}

export function MessageScroller({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Root>) {
  return (
    <MessageScrollerPrimitive.Root
      data-slot="message-scroller"
      className={cn(
        "group/message-scroller relative flex size-full min-h-0 flex-col overflow-hidden",
        "scroll-fade-b",
        className,
      )}
      {...props}
    />
  );
}

/** The pane that actually scrolls. The gutter is held open so a growing reply never shifts the
 *  line being read, the last lines fade into the surface rather than meeting a hard edge, and
 *  while the pane is scrolling itself the bar takes itself out of the way — a thumb racing down
 *  the page states the scroll a second time and the member did not ask for it. */
export function MessageScrollerViewport({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Viewport>) {
  return (
    <MessageScrollerPrimitive.Viewport
      data-slot="message-scroller-viewport"
      className={cn(
        "size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain contain-content",
        "scrollbar-thin scrollbar-gutter-stable",
        "data-autoscrolling:scrollbar-quiet",
        className,
      )}
      {...props}
    />
  );
}

export function MessageScrollerContent({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Content>) {
  return (
    <MessageScrollerPrimitive.Content
      data-slot="message-scroller-content"
      className={cn("flex h-max min-h-full flex-col gap-6xl", className)}
      {...props}
    />
  );
}

/** One row of the transcript, laid out for real whatever the pane can see.
 *
 *  shadcn skips layout for rows scrolled out of view and gives the scrollbar an estimated height
 *  for them instead. That trade needs every row to be about the height of the guess, and a message
 *  is not: one is a word, the next is a table and a code block. A conversation opened at its end
 *  then lands against a total height built from guesses, measures the rows it actually drew,
 *  and corrects — which is the stutter, once per row that was wrong.
 *
 *  Laying every row out costs a transcript's worth of work at open, which is what the member is
 *  waiting for anyway. Skipping it again needs a per-row height worth trusting, not one constant. */
export function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Item>) {
  return (
    <MessageScrollerPrimitive.Item
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      className={cn("min-w-0 shrink-0", className)}
      {...props}
    />
  );
}

/** The way back to the live edge, drawn only while there is one to go back to. It is not hidden
 *  when idle but pushed off its edge and faded, so the member who scrolls up sees it arrive
 *  rather than find it already there. */
/** The distance is travelled rather than skipped: a member who presses this has lost their place,
 *  and watching the pane run to the foot is what tells them where the foot was. A member who asked
 *  for less motion is taken there at once instead — the browser does not read that preference for
 *  a scroll it was told to animate, so it is read here. */
export function MessageScrollerButton({
  behavior,
  className,
  children,
  render,
  ...props
}: Omit<ComponentProps<typeof MessageScrollerPrimitive.Button>, "direction">) {
  const travelled =
    behavior ??
    (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth");
  return (
    <MessageScrollerPrimitive.Button
      data-slot="message-scroller-button"
      direction="end"
      behavior={travelled}
      /* The name rides `aria-label`, as every other icon-only act in the portal states its own: a
         label held in the box as text has to be clipped to a pixel to keep it off the screen, and
         a caller that hands its own words needs no label from here. */
      aria-label={children === undefined ? "Jump to bottom" : undefined}
      className={cn(
        "absolute start-1/2 bottom-2xl z-10 -translate-x-1/2 rtl:translate-x-1/2",
        "border-edge bg-surface text-ink hover:bg-fill",
        "transition-[translate,scale,opacity] duration-200",
        "data-[active=false]:pointer-events-none data-[active=false]:translate-y-full",
        "data-[active=false]:scale-95 data-[active=false]:opacity-0",
        "data-[active=false]:ease-(--ease-leave)",
        "data-[active=true]:translate-y-0 data-[active=true]:scale-100",
        "data-[active=true]:opacity-100 data-[active=true]:ease-(--ease-enter)",
        className,
      )}
      render={render ?? <Button variant="outline" size="icon" />}
      {...props}
    >
      {children ?? (
        <svg viewBox="0 0 12 12" aria-hidden className="size-(--size-icon)">
          <path
            d="M3 4.5 6 7.5 9 4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </MessageScrollerPrimitive.Button>
  );
}
