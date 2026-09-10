import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconChevronLeft, IconChevronRight, IconMinus, IconPlus, IconX } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { PICTURE_DID_NOT_LOAD, type SharedFile } from "@/kernel/artifact";
import { cn } from "@/lib/cn";

const ZOOM_STEPS: readonly (number | null)[] = [null, 1, 2, 4];

function zoomLabel(step: number | null): string {
  return step === null ? "Fit" : `${String(step * 100)}%`;
}

const zoomedIn = (step: number) => Math.min(step + 1, ZOOM_STEPS.length - 1);
const zoomedOut = (step: number) => Math.max(step - 1, 0);

export function Lightbox({
  files,
  at,
  onMove,
  onClose,
}: {
  files: SharedFile[];
  at: number;
  onMove: (at: number) => void;
  onClose: () => void;
}) {
  const file = files[at];
  const [step, setStep] = useState(0);
  const [failed, setFailed] = useState(false);
  const [natural, setNatural] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const zoom = ZOOM_STEPS[step];
  const pictured =
    file.media_type.startsWith("image/") && file.url !== null
      ? file.url
      : (file.preview_url ?? file.url);

  // The width is left standing: the element is keyed per picture, so it measures itself as it attaches,
  // and zeroing it here would land after that measurement and undo it.
  useEffect(() => {
    setStep(0);
    setFailed(false);
  }, [at]);

  useEffect(() => {
    scroller.current?.scrollTo({ left: 0, top: 0 });
  }, [zoom]);

  // A picture the browser already holds decodes before this element carries a load handler, so no load
  // event arrives and only `complete` carries the width. The element attaches in a later commit.
  const measure = useCallback((held: HTMLImageElement | null) => {
    if (held !== null && held.complete && held.naturalWidth > 0) setNatural(held.naturalWidth);
  }, []);

  const move = (by: number) => {
    const next = at + by;
    if (next < 0 || next >= files.length) return;
    onMove(next);
  };

  return (
    <DialogPrimitive.Root open onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          data-slot="lightbox"
          aria-describedby={undefined}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") move(-1);
            if (event.key === "ArrowRight") move(1);
            if (event.key === "+" || event.key === "=") setStep(zoomedIn);
            if (event.key === "-") setStep(zoomedOut);
          }}
          className="fixed inset-0 z-20 flex flex-col outline-none animate-appear"
        >
          <div
            data-slot="lightbox-bar"
            className={cn(
              "flex min-h-(--size-control) shrink-0 flex-wrap items-center gap-md",
              "border-b border-edge bg-surface px-2xl py-lg",
            )}
          >
            <Button variant="quiet" size="icon" aria-label="Close" onClick={onClose}>
              <IconX aria-hidden />
            </Button>
            <DialogPrimitive.Title className="m-0 min-w-0 flex-1 truncate text-label font-medium">
              {file.subject || file.filename}
            </DialogPrimitive.Title>
            {files.length > 1 ? (
              <div className="flex shrink-0 items-center gap-sm">
                <Button
                  variant="quiet"
                  size="icon"
                  aria-label="Previous picture"
                  disabled={at === 0}
                  onClick={() => move(-1)}
                >
                  <IconChevronLeft aria-hidden />
                </Button>
                <span className="font-mono text-small tabular-nums text-ink-soft">
                  {at + 1} / {files.length}
                </span>
                <Button
                  variant="quiet"
                  size="icon"
                  aria-label="Next picture"
                  disabled={at === files.length - 1}
                  onClick={() => move(1)}
                >
                  <IconChevronRight aria-hidden />
                </Button>
              </div>
            ) : null}
            <div className="flex shrink-0 items-center gap-sm">
              <Button
                variant="quiet"
                size="icon"
                aria-label="Zoom out"
                disabled={step === 0}
                onClick={() => setStep(zoomedOut)}
              >
                <IconMinus aria-hidden />
              </Button>
              <span
                aria-live="polite"
                className="min-w-12 text-center font-mono text-small tabular-nums text-ink-soft"
              >
                {zoomLabel(zoom)}
              </span>
              <Button
                variant="quiet"
                size="icon"
                aria-label="Zoom in"
                disabled={step === ZOOM_STEPS.length - 1}
                onClick={() => setStep(zoomedIn)}
              >
                <IconPlus aria-hidden />
              </Button>
            </div>
            {file.url ? (
              <a
                href={file.url}
                download={file.filename}
                className={cn(buttonVariants({ variant: "outline" }), "shrink-0 no-underline")}
              >
                Download
              </a>
            ) : null}
          </div>
          <div
            ref={scroller}
            data-slot="lightbox-stage"
            className={cn(
              "flex min-h-0 flex-1 bg-surface p-lg",
              zoom === null ? "overflow-hidden" : "overflow-auto",
            )}
          >
            {failed || pictured === null ? (
              <div className="m-auto font-mono text-small text-ink-soft">
                {PICTURE_DID_NOT_LOAD}
              </div>
            ) : (
              <button
                type="button"
                aria-label={zoom === null ? "Show at full size" : "Fit to window"}
                onClick={() => setStep((held) => (held === 0 ? 1 : 0))}
                className={cn(
                  "m-auto cursor-pointer border-0 bg-transparent p-0",
                  // Fitted, the button is the stage's own height: a percentage of a content-sized box is no bound at
                  // all, so a picture taller than the stage would be cut off by a step that does not scroll.
                  zoom === null
                    ? "flex h-full w-fit max-w-full items-center justify-center"
                    : "block shrink-0",
                )}
              >
                <img
                  key={pictured}
                  ref={measure}
                  alt={file.subject || file.filename}
                  src={pictured}
                  onError={() => setFailed(true)}
                  onLoad={(event) => setNatural(event.currentTarget.naturalWidth)}
                  className={cn(
                    "block",
                    zoom === null ? "max-h-full max-w-full object-contain" : "h-auto max-w-none",
                  )}
                  style={zoom === null || natural === 0 ? undefined : { width: natural * zoom }}
                />
              </button>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
