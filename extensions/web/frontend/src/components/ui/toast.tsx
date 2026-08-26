import * as ToastPrimitive from "@radix-ui/react-toast";

import { cn } from "@/lib/cn";

const TOAST_DWELL_MS = 6000;

export type ToastState = { title: string; description?: string };

export const SILENT: ToastState = { title: "" };

/** Where the toast stands. `screen` is the page's own corner, which is where an outcome belonging
 *  to the whole screen is reported. `surface` is the foot of the thing that raised it, for a panel
 *  laid over the page: a message about what the member just did in that panel, drawn in the far
 *  corner of a screen dimmed behind it, belongs to nothing they are looking at. */
const POSITIONS = {
  screen: "fixed bottom-2xl left-2xl",
  surface: "absolute right-2xl bottom-2xl",
} as const;

export type ToastPosition = keyof typeof POSITIONS;

/** A toast reports an outcome whose surface has already gone, or a read the member cannot correct
 *  from any control on the screen. The title names what happened; the description names why. A
 *  refusal a field can answer stays beside that field instead, because a message that dismisses
 *  itself cannot be read back.
 *
 *  The viewport is what stands the toast, and the toast itself is only a card — so where it is read
 *  is a property of the screen rather than of the message, and moving it is one word at the call
 *  site. Dismissal is the primitive's: it holds the dwell, pauses it while the pointer is over the
 *  card or the window is in the background, and gives the member a swipe to send it away early. */
export function Toast({
  state,
  onDone,
  position = "screen",
}: {
  state: ToastState;
  onDone: () => void;
  position?: ToastPosition;
}) {
  const { title, description } = state;
  return (
    // The stand is ours rather than the viewport's: the primitive puts its own wrapper around that
    // list in normal flow, and a toast rendered beside a screen's own panes would take a track in
    // the grid they are laid out by — halving the screen behind it. Standing the whole thing out of
    // flow here keeps the toast off every layout it is drawn over.
    <div data-slot="toast-stand" className={cn("z-10", POSITIONS[position])}>
      <ToastPrimitive.Provider duration={TOAST_DWELL_MS} swipeDirection="left">
      <ToastPrimitive.Root
        data-slot="toast"
        open={Boolean(title)}
        onOpenChange={(open) => {
          if (!open) onDone();
        }}
        className={cn(
          "rounded-panel border border-edge bg-popover text-popover-foreground px-lg py-md",
          "[box-shadow:var(--shadow-raised)] animate-raise",
          "data-[swipe=move]:translate-x-(--radix-toast-swipe-move-x)",
          "data-[swipe=cancel]:translate-x-0 data-[swipe=end]:opacity-0",
        )}
      >
        <ToastPrimitive.Title className="text-ui font-strong">{title}</ToastPrimitive.Title>
        {description ? (
          <ToastPrimitive.Description className="text-small text-ink-soft mt-hair">
            {description}
          </ToastPrimitive.Description>
        ) : null}
      </ToastPrimitive.Root>
      <ToastPrimitive.Viewport
        data-slot="toast-viewport"
        className="m-0 flex max-w-empty list-none flex-col gap-sm p-0 outline-none"
      />
      </ToastPrimitive.Provider>
    </div>
  );
}
