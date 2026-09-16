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
      className={cn("flex h-max min-h-full flex-col gap-2xl", className)}
      {...props}
    />
  );
}

const itemVariants = cva("min-w-0 shrink-0", {
  variants: {
    mark: {
      held: cn(
        "rounded-panel bg-attention outline-2 outline-attention-ink",
        "transition-[background-color,outline-color] duration-500",
      ),
      "letting-go": cn(
        "rounded-panel bg-transparent outline-2 outline-transparent",
        "transition-[background-color,outline-color] duration-500",
      ),
    },
    throb: { true: "animate-marked", false: "" },
  },
  defaultVariants: { throb: false },
});

/** `mark` draws the row a jump landed on in the second accent, because the first is the fill a
 *  member's own words carry: a mark in that fill reads as a message the member sent. `throb` is the
 *  caller's rather than a `motion-reduce:` rule, because the member's preference decides whether the
 *  row pulses at all rather than how it pulses. */
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
    <MessageScrollerPrimitive.Button
      data-slot="message-scroller-button"
      direction="end"
      behavior={travelled}
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
