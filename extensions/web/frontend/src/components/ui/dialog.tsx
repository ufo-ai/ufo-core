import * as DialogPrimitive from "@radix-ui/react-dialog";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export const Dialog = DialogPrimitive.Root;

export function DialogContent({
  className,
  children,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay data-slot="dialog-overlay" className="fixed inset-0 z-10 bg-scrim animate-appear" />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(
          "fixed top-1/2 left-1/2 z-10 -translate-x-1/2 -translate-y-1/2",
          "w-dialog max-h-[var(--media-tall)] overflow-y-auto",
          "flex flex-col gap-4xl rounded-card border border-edge bg-popover text-popover-foreground p-4xl",
          "[box-shadow:var(--shadow-raised)] animate-raise",
          className,
        )}
        {...props}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn("flex flex-col gap-2xs", className)} {...props} />;
}

export function DialogTitle({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("m-0 text-subtitle font-strong [overflow-wrap:anywhere]", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn("m-0 text-label text-ink-soft", className)}
      {...props}
    />
  );
}

/** The close sits in the footer beside the act it cancels, never as a corner glyph: the portal
 *  ships no icon set, and a labelled control states what leaving does. `Cancel` takes the padding
 *  of `send` so the pair reads as one choice of two, not an act with a smaller way out. A dialog
 *  with nothing to commit names the leave `Close` — there is no act to abandon.
 *
 *  `lead` is the far end of the footer: an act on the record the dialog is showing rather than one
 *  of the two ways out of the dialog. It stands apart from the pair so a destructive act is never
 *  the control beside the one the member is reaching for. */
export function DialogFooter({
  className,
  children,
  lead,
  leave = "Cancel",
  ...props
}: ComponentProps<"div"> & { lead?: ReactNode; leave?: string }) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "flex flex-wrap items-center justify-end gap-sm border-t border-edge pt-lg",
        className,
      )}
      {...props}
    >
      {lead ? <div className="mr-auto">{lead}</div> : null}
      <DialogPrimitive.Close asChild>
        <Button className="px-3xl py-md">{leave}</Button>
      </DialogPrimitive.Close>
      {children}
    </div>
  );
}
