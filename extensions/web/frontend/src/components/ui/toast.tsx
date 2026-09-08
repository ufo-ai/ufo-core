import * as ToastPrimitive from "@radix-ui/react-toast";

import { cn } from "@/lib/cn";

const TOAST_DWELL_MS = 6000;

export type ToastState = { title: string; description?: string };

export const SILENT: ToastState = { title: "" };

const POSITIONS = {
  screen: "fixed bottom-2xl left-2xl",
  surface: "absolute right-2xl bottom-2xl",
} as const;

export type ToastPosition = keyof typeof POSITIONS;

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
