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
  footer?: ReactNode;
};

export function Sheet({ open, onClose, title, children, footer }: SheetProps) {
  return (
    <DialogPrimitive.Root modal={false} open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          data-slot="sheet-content"
          aria-describedby={undefined}
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 left-auto z-10 w-drawer overflow-y-auto",
            "bg-popover text-popover-foreground border-l border-edge p-2xl [box-shadow:var(--shadow-raised)]",
            "flex flex-col gap-2xl",
          )}
        >
          <header data-slot="sheet-header" className="flex h-(--size-control) items-center justify-between gap-md">
            <DialogPrimitive.Title className="m-0 text-title font-strong [overflow-wrap:anywhere]">
              {title}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button size="icon" aria-label="Close">
                <IconX className="size-icon" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          {children}
          {footer ? <footer data-slot="sheet-footer" className="mt-auto">{footer}</footer> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
