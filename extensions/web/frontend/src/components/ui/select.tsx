import * as SelectPrimitive from "@radix-ui/react-select";
import type { ComponentProps } from "react";

import { CONTROL } from "@/components/ui/field";
import { cn } from "@/lib/cn";

export const Select = SelectPrimitive.Root;

function Chevron({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden
      className={cn("size-(--spacing-lg) shrink-0 text-ink-soft", className)}
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
  return <SelectPrimitive.Value data-slot="select-value" className={cn("min-w-0 truncate", className)} {...props} />;
}

export const BAR_CONTROL = "h-(--size-control) rounded-full py-0";

export const PLAIN_CONTROL = cn(
  "size-auto justify-end gap-xs px-0 py-0",
  "border-0 bg-transparent text-ui hover:border-transparent",
);

export function SelectTrigger({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
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
    <SelectPrimitive.ScrollUpButton data-slot="select-scroll-up-button" className="flex cursor-default items-center justify-center py-2xs">
      <Chevron className="rotate-180" />
    </SelectPrimitive.ScrollUpButton>
  );
}

function ScrollDown() {
  return (
    <SelectPrimitive.ScrollDownButton data-slot="select-scroll-down-button" className="flex cursor-default items-center justify-center py-2xs">
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
        data-slot="select-content"
        position="popper"
        sideOffset={4}
        className={cn(
          "z-10 max-h-(--radix-select-content-available-height) overflow-y-auto",
          "min-w-(--radix-select-trigger-width) origin-(--radix-select-content-transform-origin)",
          "rounded-panel border border-edge bg-popover text-popover-foreground p-2xs",
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
      data-slot="select-item"
      className={cn(
        "flex cursor-default select-none items-center justify-between gap-lg rounded-sm px-lg py-sm",
        "text-ui outline-none data-[highlighted]:bg-fill",
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
