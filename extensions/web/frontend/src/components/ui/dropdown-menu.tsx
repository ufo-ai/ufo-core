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

/** `container` is where the menu is drawn: the document's own end by default, and the element a
 *  caller names when the menu belongs inside a layer already standing — a drawer is a modal, and a
 *  menu drawn past it is out of the member's reach. */
export function DropdownMenuContent({
  className,
  sideOffset = 4,
  container,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content> & { container?: HTMLElement | null }) {
  return (
    <DropdownMenuPrimitive.Portal container={container}>
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

/** A choice a member turns on and off rather than picks between. Ticking one leaves the menu open:
 *  the options are read as a set and are usually changed together. */
export function DropdownMenuCheckboxItem({
  className,
  children,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.CheckboxItem>) {
  return (
    <DropdownMenuPrimitive.CheckboxItem
      data-slot="dropdown-menu-checkbox-item"
      className={cn(ITEM, className)}
      onSelect={(event) => event.preventDefault()}
      {...props}
    >
      {children}
      <DropdownMenuPrimitive.ItemIndicator asChild>
        <Tick />
      </DropdownMenuPrimitive.ItemIndicator>
    </DropdownMenuPrimitive.CheckboxItem>
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

/** The name over the items it heads. It is a caption rather than an item: no row height, no fill
 *  under the pointer, no press — a line that answers the pointer is a line the member reads as
 *  pickable, and this one only says what the options below it are. It takes the register the table
 *  heads take, so a name over a set reads the same wherever the portal states one. */
export function DropdownMenuLabel({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return (
    <DropdownMenuPrimitive.Label
      data-slot="dropdown-menu-label"
      className={cn("px-sm pt-sm pb-2xs text-label text-ink-soft", className)}
      {...props}
    />
  );
}

/** The rule between two groups of items. It reaches the popup's own edges rather than stopping at
 *  the padding the items stand in, so the menu is parted in two instead of carrying a short line
 *  inside one column of rows. */
export function DropdownMenuSeparator({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn("-mx-sm my-sm h-px bg-edge", className)}
      {...props}
    />
  );
}
