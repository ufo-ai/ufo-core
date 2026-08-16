import type { ComponentProps } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

/** A file the conversation is carrying — one the member attached to a message, or one a reply
 *  produced. It is drawn as a card on the pane rather than as a line of text, so a message with
 *  files reads as words and then things, and it keeps a minimum width so a four-character name
 *  does not shrink the card to a pill the pointer has to hunt for. */
const attachmentVariants = cva(
  cn(
    "flex w-fit max-w-full min-w-(--container-attachment) shrink-0 flex-wrap items-center",
    "rounded-panel border border-edge bg-card text-card-foreground",
  ),
  {
    variants: {
      size: {
        sm: cn(
          "gap-md text-small",
          "has-data-[slot=attachment-content]:px-sm has-data-[slot=attachment-content]:py-xs",
        ),
      },
    },
    defaultVariants: { size: "sm" },
  },
);

export function Attachment({
  className,
  size = "sm",
  ...props
}: ComponentProps<"div"> & VariantProps<typeof attachmentVariants>) {
  return (
    <div
      data-slot="attachment"
      className={cn(attachmentVariants({ size }), className)}
      {...props}
    />
  );
}

export function AttachmentContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-content"
      className={cn("max-w-full min-w-0 flex-1 leading-chrome", className)}
      {...props}
    />
  );
}

/** The file's name, truncated rather than wrapped: the card holds one line, so a row of files
 *  stays one row tall whatever the names are. */
export function AttachmentTitle({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="attachment-title"
      className={cn("block max-w-full min-w-0 truncate font-medium", className)}
      {...props}
    />
  );
}

export function AttachmentDescription({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="attachment-description"
      className={cn(
        "mt-hair block max-w-full min-w-0 truncate text-small text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}

/** A row of files that scrolls sideways rather than wrapping, so a message carrying six of them
 *  stays one row tall and the reply beneath it does not move down the page. The fade that says
 *  "more that way" ramps over the row's edges, so the ramp lives in gutters the padding opens and
 *  the negative margin gives back — over the first card, it would eat the card's left border. */
export function AttachmentGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-group"
      className={cn(
        "-mx-sm flex min-w-0 gap-lg overflow-x-auto overscroll-x-contain px-sm py-2xs",
        "scroll-fade-x scrollbar-none snap-x snap-mandatory scroll-px-sm",
        "*:data-[slot=attachment]:flex-none *:data-[slot=attachment]:snap-start",
        className,
      )}
      {...props}
    />
  );
}
