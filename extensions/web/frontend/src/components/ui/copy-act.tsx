import { useEffect, useRef, useState } from "react";

import { IconCheck, IconCopy, IconX } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** The glyph draws at the meta line's own type — 12 pixels. The box opens at its leading edge
 *  rather than around it, because the model's mark stands 10 pixels before it. */
const TAP_TARGET = cn(
  "relative",
  "before:absolute before:start-0 before:top-1/2 before:-translate-y-1/2",
  "before:size-(--size-touch)",
);

const COPY_LABELS = {
  idle: "Copy message",
  copied: "Copied",
  failed: "Copy failed",
} as const;

const COPIED_MS = 2_000;

/** A browser refuses `writeText` when the document is not focused or the permission is withheld,
 *  and a rejection left unhandled would draw nothing at all. */
export function CopyAct({ text }: { text: string }) {
  const [state, setState] = useState<keyof typeof COPY_LABELS>("idle");
  const fades = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (fades.current) window.clearTimeout(fades.current);
    },
    [],
  );
  const copy = async () => {
    let next: keyof typeof COPY_LABELS = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setState(next);
    if (fades.current) window.clearTimeout(fades.current);
    fades.current = window.setTimeout(() => setState("idle"), COPIED_MS);
  };
  return (
    <Button
      variant="mark"
      size="glyph"
      aria-label={COPY_LABELS[state]}
      tone={state === "failed" ? "attention" : undefined}
      className={cn("size-(--size-icon) [&_svg]:size-(--size-icon)", TAP_TARGET)}
      onClick={copy}
    >
      {state === "copied" ? (
        <IconCheck stroke={1.5} aria-hidden />
      ) : state === "failed" ? (
        <IconX stroke={1.5} aria-hidden />
      ) : (
        <IconCopy stroke={1.5} aria-hidden />
      )}
    </Button>
  );
}
