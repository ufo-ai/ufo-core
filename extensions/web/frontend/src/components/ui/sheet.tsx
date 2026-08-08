import * as DialogPrimitive from "@radix-ui/react-dialog";
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
          aria-describedby={undefined}
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 left-auto z-10 w-drawer overflow-y-auto",
            "bg-surface border-l border-edge-strong p-2xl [box-shadow:var(--shadow-raised)]",
            "flex flex-col gap-md",
          )}
        >
          <header className="flex items-baseline justify-between gap-md">
            <DialogPrimitive.Title className="m-0 text-body [overflow-wrap:anywhere]">
              {title}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button>Close</Button>
            </DialogPrimitive.Close>
          </header>
          {children}
          {footer ? <footer className="mt-auto">{footer}</footer> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
