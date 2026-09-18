import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
} from "@shadcn/react/message-scroller";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export { useMessageScroller };

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

/** The scroller moves itself with `behavior: "auto"`, so the CSS here decides whether those moves
 *  animate. Only a scroll the member caused should animate: opening a transcript, switching to
 *  another, and restoring the place when older messages load in above all have to land without
 *  moving, or they read as the page scrolling itself. `animate` is therefore off by default and the
 *  caller turns it on around the one scroll it asked for. */
export function MessageScrollerViewport({
  className,
  animate = false,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Viewport> & { animate?: boolean }) {
  return (
    <MessageScrollerPrimitive.Viewport
      data-slot="message-scroller-viewport"
      className={cn(
        "size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain contain-content",
        animate && "scroll-smooth motion-reduce:scroll-auto",
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
      className={cn("flex flex-col gap-2xl", className)}
      {...props}
    />
  );
}

const MARKED = cn(
  "[&_[data-slot=bubble-content]]:outline-2",
  "[&_[data-slot=bubble-content]]:transition-[outline-color]",
  "[&_[data-slot=bubble-content]]:duration-500",
  "[&_[data-slot=bubble]~[data-slot=bubble]_[data-slot=bubble-content]]:outline-none",
  "not-has-[[data-slot=bubble-content]]:outline-2",
  "not-has-[[data-slot=bubble-content]]:transition-[outline-color]",
  "not-has-[[data-slot=bubble-content]]:duration-500",
);

const itemVariants = cva("min-w-0 shrink-0", {
  variants: {
    mark: {
      held: cn(
        MARKED,
        "[&_[data-slot=bubble-content]]:outline-attention-ink",
        "not-has-[[data-slot=bubble-content]]:outline-attention-ink",
      ),
      "letting-go": cn(
        MARKED,
        "[&_[data-slot=bubble-content]]:outline-transparent",
        "not-has-[[data-slot=bubble-content]]:outline-transparent",
      ),
    },
    throb: {
      true: cn(
        "[&_[data-slot=bubble-content]]:animate-marked",
        "not-has-[[data-slot=bubble-content]]:animate-marked",
      ),
      false: "",
    },
  },
  defaultVariants: { throb: false },
});

/** `mark` draws the second accent around the words a jump landed on — the row's first bubble, so
 *  one landing leaves one mark however many the turn drew, and the row itself where the turn drew
 *  no bubble at all. The row's own box spans the column, which is why the mark is not drawn on it.
 *  The first accent is the fill a member's own words carry, and a mark in that fill reads as a
 *  message the member sent. `throb` is the caller's rather than a `motion-reduce:` rule, because
 *  the member's preference decides whether the mark pulses at all rather than how it pulses. */
export function MessageScrollerItem({
  className,
  mark,
  throb,
  scrollAnchor = false,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Item> & VariantProps<typeof itemVariants>) {
  return (
    <MessageScrollerPrimitive.Item
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      className={cn(itemVariants({ mark, throb }), className)}
      {...props}
    />
  );
}

/** Floated over the column, the control covered whatever row reached the foot and took the press a
 *  question card's choices were meant to get. The lane opens the height it needs, as `unfolds` does. */
const LANE = cn(
  "flex box-content shrink-0 items-center justify-center overflow-hidden",
  /* An `fr` track keeps an automatic `auto` minimum, so `0fr` cannot close over a control that
     states its own height; the height this opens to is known, so it is the thing animated. */
  "h-0 has-[[data-active=true]]:h-(--size-touch) has-[[data-active=true]]:py-sm",
  "transition-[height] duration-200 ease-(--ease-leave)",
  "has-[[data-active=true]]:ease-(--ease-enter) motion-reduce:transition-none",
);

/** The browser does not read the reduced-motion preference for a scroll it was told to animate, so it
 *  is read here. */
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
    <div className={LANE}>
      <MessageScrollerPrimitive.Button
        data-slot="message-scroller-button"
        direction="end"
        behavior={travelled}
        aria-label={children === undefined ? "Jump to bottom" : undefined}
        className={cn(
          "size-(--size-touch)",
          "border-edge bg-surface text-ink hover:bg-fill",
          "data-[active=false]:pointer-events-none",
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
    </div>
  );
}
