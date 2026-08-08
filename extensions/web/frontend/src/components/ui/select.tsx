import * as SelectPrimitive from "@radix-ui/react-select";
import type { ComponentProps } from "react";

import { CONTROL } from "@/components/ui/field";
import { cn } from "@/lib/cn";

export const Select = SelectPrimitive.Root;

/** The two glyphs are drawn here rather than imported. The portal carries no icon set, and an icon
 *  library earns its weight at a vocabulary of icons, not at a chevron and a tick — both of which
 *  take their colour from the ink they sit in and need no token of their own. */
function Chevron({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden
      className={cn("size-(--spacing-lg) shrink-0 opacity-(--muted)", className)}
    >
      <path
        d="M3 4.5 6 7.5 9 4.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Tick() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className="size-(--spacing-lg) shrink-0">
      <path
        d="M2.5 6.25 4.75 8.5 9.5 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SelectValue({ className, ...props }: ComponentProps<typeof SelectPrimitive.Value>) {
  return <SelectPrimitive.Value className={cn("min-w-0 truncate", className)} {...props} />;
}

export function SelectTrigger({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        CONTROL,
        "flex w-full max-w-control items-center justify-between gap-sm whitespace-nowrap",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <Chevron />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function ScrollUp() {
  return (
    <SelectPrimitive.ScrollUpButton className="flex cursor-default items-center justify-center py-2xs">
      <Chevron className="rotate-180" />
    </SelectPrimitive.ScrollUpButton>
  );
}

function ScrollDown() {
  return (
    <SelectPrimitive.ScrollDownButton className="flex cursor-default items-center justify-center py-2xs">
      <Chevron />
    </SelectPrimitive.ScrollDownButton>
  );
}

export function SelectContent({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position="popper"
        sideOffset={4}
        className={cn(
          "z-10 max-h-(--radix-select-content-available-height) overflow-y-auto",
          "min-w-(--radix-select-trigger-width) origin-(--radix-select-content-transform-origin)",
          "rounded-panel border border-edge-strong bg-surface p-2xs",
          "[box-shadow:var(--shadow-raised)] animate-raise",
          className,
        )}
        {...props}
      >
        <ScrollUp />
        <SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport>
        <ScrollDown />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "flex cursor-default select-none items-center justify-between gap-lg rounded-sm px-lg py-sm",
        "text-ui outline-none data-[highlighted]:bg-fill-hover",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-(--disabled)",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator asChild>
        <Tick />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
