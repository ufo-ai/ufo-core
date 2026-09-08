import * as DialogPrimitive from "@radix-ui/react-dialog";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** A modal dialog's root: holds whether it is open, for a confirmation or a form that takes the
 * screen until it is answered. */
export const Dialog = DialogPrimitive.Root;

/** The control that opens the Dialog it stands in; `asChild` makes the child the trigger. */
export const DialogTrigger = DialogPrimitive.Trigger;

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
