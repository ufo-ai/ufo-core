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
  /** The acts on the record, in the header between its name and the way out — the record panel's
   *  own header shape, so an act is reachable however far the content below runs. */
  actions?: ReactNode;
};

export function Sheet({ open, onClose, title, children, actions }: SheetProps) {
  return (
    <DialogPrimitive.Root modal={false} open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          data-slot="sheet-content"
          aria-describedby={undefined}
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 left-auto z-10 w-drawer overflow-y-auto",
            "bg-surface border-l border-edge p-2xl",
            "flex flex-col gap-2xl",
          )}
        >
          <header data-slot="sheet-header" className="flex h-(--size-control) shrink-0 items-center gap-md">
            <DialogPrimitive.Title className="m-0 flex-1 truncate text-subtitle font-medium">
              {title}
            </DialogPrimitive.Title>
            {actions}
            <DialogPrimitive.Close asChild>
              <Button size="icon" aria-label="Close">
                <IconX className="size-icon" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
