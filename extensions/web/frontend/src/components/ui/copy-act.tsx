import { useEffect, useRef, useState } from "react";

import { IconCheck, IconCopy, IconX } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

const TAP_FLOOR = "max-narrow:inline-flex max-narrow:min-h-(--size-control) max-narrow:items-center";

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
      className={cn(
        "size-(--size-icon) [&_svg]:size-(--size-icon)",
        TAP_FLOOR,
        "max-narrow:min-w-(--size-control) max-narrow:justify-center",
      )}
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
