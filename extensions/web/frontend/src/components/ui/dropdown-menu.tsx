import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export const DropdownMenu = DropdownMenuPrimitive.Root;

export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

export const DropdownMenuSub = DropdownMenuPrimitive.Sub;

export const DropdownMenuRadioGroup = DropdownMenuPrimitive.RadioGroup;

const POPUP =
  "z-10 flex min-w-(--container-menu) origin-(--radix-dropdown-menu-content-transform-origin) flex-col gap-px rounded-menu border border-edge bg-popover text-popover-foreground p-sm [box-shadow:var(--shadow-raised)] animate-raise";

const ITEM =
  "flex h-(--size-row) cursor-default select-none items-center justify-between gap-sm rounded-control p-sm text-ui outline-none data-[highlighted]:bg-fill";

function SubChevron() {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden
      className="size-(--spacing-lg) shrink-0 text-ink-soft"
    >
      <path
        d="M4.5 3 7.5 6 4.5 9"
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

export function DropdownMenuContent({
  className,
  sideOffset = 4,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        className={cn(POPUP, className)}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Item>) {
  return (
    <DropdownMenuPrimitive.Item
      data-slot="dropdown-menu-item"
      className={cn(ITEM, className)}
      {...props}
    />
  );
}

export function DropdownMenuSubTrigger({
  className,
  children,
  value,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.SubTrigger> & { value?: string }) {
  return (
    <DropdownMenuPrimitive.SubTrigger
      data-slot="dropdown-menu-sub-trigger"
      className={cn(ITEM, "data-[state=open]:bg-fill", className)}
      {...props}
    >
      {children}
      <span className="flex items-center gap-xs">
        {value ? <span className="text-ink-soft">{value}</span> : null}
        <SubChevron />
      </span>
    </DropdownMenuPrimitive.SubTrigger>
  );
}

export function DropdownMenuSubContent({
  className,
  sideOffset = 4,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.SubContent>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.SubContent
        data-slot="dropdown-menu-sub-content"
        sideOffset={sideOffset}
        className={cn(POPUP, className)}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.RadioItem>) {
  return (
    <DropdownMenuPrimitive.RadioItem
      data-slot="dropdown-menu-radio-item"
      className={cn(ITEM, className)}
      {...props}
    >
      {children}
      <DropdownMenuPrimitive.ItemIndicator asChild>
        <Tick />
      </DropdownMenuPrimitive.ItemIndicator>
    </DropdownMenuPrimitive.RadioItem>
  );
}
