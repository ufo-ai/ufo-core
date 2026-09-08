import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconX } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type SheetProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  describedBy?: string;
};

/** A drawer on the right edge that opens over the pane without taking the screen: a title, a close
 * control, optional acts, and a scrolling body. The pane behind stays live. */
export function Sheet({ open, onClose, title, children, actions, describedBy }: SheetProps) {
  return (
    <DialogPrimitive.Root modal={false} open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          data-slot="sheet-content"
          aria-describedby={describedBy}
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 left-auto z-10 w-drawer",
            "flex min-h-0 flex-col border-l border-edge bg-surface",
            "[box-shadow:var(--shadow-raised)]",
          )}
        >
          <div
            data-slot="sheet-header"
            className="flex min-h-(--size-control) shrink-0 items-center gap-md border-b border-edge px-2xl py-lg"
          >
            <Button variant="quiet" size="icon" aria-label="Close" onClick={onClose}>
              <IconX aria-hidden />
            </Button>
            <DialogPrimitive.Title className="m-0 min-w-0 flex-1 truncate text-label font-medium">
              {title}
            </DialogPrimitive.Title>
            {actions ? <div className="flex shrink-0 items-center gap-sm">{actions}</div> : null}
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-2xl overflow-y-auto p-2xl">
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
