import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconX } from "@tabler/icons-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { useNarrow } from "@/lib/narrow";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";

export type SheetProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  describedBy?: string;
};

type Host = { target: HTMLElement | null; hold: (id: string, open: boolean) => void };

const HostContext = createContext<Host | null>(null);

const SHEET_SHARE = "50";
const SHEET_FLOOR = "25";
const BODY_FLOOR = "30";

/** The pane's right column: while any sheet under it is open, the pane splits half and half with a
 *  handle the member drags, and the sheets render into the right half. Under the narrow breakpoint
 *  there is no room to split, so the sheet covers the pane instead. */
export function SheetHost({ children }: { children: ReactNode }) {
  const [held, setHeld] = useState<ReadonlySet<string>>(new Set());
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const hold = useCallback(
    (id: string, open: boolean) =>
      setHeld((current) => {
        if (current.has(id) === open) return current;
        const next = new Set(current);
        if (open) next.add(id);
        else next.delete(id);
        return next;
      }),
    [],
  );
  const narrow = useNarrow();
  const shown = held.size > 0;
  const beside = shown && !narrow;
  const host = useMemo(() => ({ target: shown ? target : null, hold }), [shown, target, hold]);
  return (
    <HostContext.Provider value={host}>
      <div className="relative flex min-h-0 flex-1 flex-col">
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel
            minSize={BODY_FLOOR}
            // A pane's own lane track is `absolute inset-0 z-10`: it resolves against the wrapper
            // around both columns and paints over the sheet unless the body column bounds it.
            className="relative isolate flex min-h-0 min-w-0 flex-col"
          >
            {children}
          </ResizablePanel>
          {beside ? (
            <>
              <ResizableHandle withHandle />
              <ResizablePanel
                defaultSize={SHEET_SHARE}
                minSize={SHEET_FLOOR}
                className="flex min-h-0 min-w-0 flex-col"
              >
                <div ref={setTarget} className="relative min-h-0 flex-1" />
              </ResizablePanel>
            </>
          ) : null}
        </ResizablePanelGroup>
        {shown && !beside ? <div ref={setTarget} className="absolute inset-0" /> : null}
      </div>
    </HostContext.Provider>
  );
}

/** A column on the right edge of the pane: a title, a close control, optional acts, and a scrolling
 * body. The pane beside it stays live and gives up half its width while the sheet is open. A sheet
 * opened from another covers it, and reveals it again on close. */
export function Sheet({ open, onClose, title, children, actions, describedBy }: SheetProps) {
  const host = useContext(HostContext);
  if (host === null) throw new Error("Sheet renders under a SheetHost");
  const id = useId();
  const { hold } = host;
  useEffect(() => {
    if (!open) return;
    hold(id, true);
    return () => hold(id, false);
  }, [hold, id, open]);
  if (!open || host.target === null) return null;
  return (
    <DialogPrimitive.Root modal={false} open onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal container={host.target}>
        <DialogPrimitive.Content
          data-slot="sheet-content"
          aria-describedby={describedBy}
          onInteractOutside={(event) => event.preventDefault()}
          className="absolute inset-0 flex min-h-0 flex-col bg-surface"
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
