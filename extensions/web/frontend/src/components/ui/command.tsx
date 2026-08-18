import { Command as CommandPrimitive } from "cmdk";
import { IconSearch, type TablerIcon } from "@tabler/icons-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** One box over a list of rows, moved through by arrow keys and taken by Enter. The rows are
 *  options under the box's combobox rather than buttons, so the member reaches every one of them
 *  without leaving the field they are typing in.
 *
 *  `ctrl+k` opens this, so cmdk's vim bindings — which spend `ctrl+k` on moving the cursor up —
 *  are off: the one chord cannot mean two things depending on whether the list is already open. */
export function Command({ className, ...props }: ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn("flex min-h-0 w-full flex-col", className)}
      {...props}
      vimBindings={false}
    />
  );
}

/** The field, glyph and all, ruled off from the rows beneath it. Nothing here is filled: fill is
 *  what marks the row under the cursor, and a second filled shape in the panel reads as a second
 *  thing to pick rather than the box being typed into. */
export function CommandInput({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div
      data-slot="command-input-wrapper"
      className="flex shrink-0 items-center gap-sm border-b border-edge px-lg py-md"
    >
      <IconSearch className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          "w-full border-0 bg-transparent p-0 text-ui text-inherit outline-none placeholder:text-ink-soft",
          className,
        )}
        {...props}
      />
    </div>
  );
}

export function CommandList({ className, ...props }: ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn("h-(--size-palette) min-h-0 scroll-py-2xs overflow-y-auto p-2xs", className)}
      {...props}
    />
  );
}

/** A named run of rows. The heading names the group to a screen reader as well as to the eye —
 *  cmdk hides the heading element itself and points the group's label at it — so the heading is
 *  the words a member would use for the kind, never a decoration over the rows. */
export function CommandGroup({
  heading,
  className,
  ...props
}: Omit<ComponentProps<typeof CommandPrimitive.Group>, "heading"> & { heading: string }) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      heading={
        <span className="block px-sm py-2xs text-small font-medium text-ink-soft">{heading}</span>
      }
      className={cn("overflow-hidden py-2xs", className)}
      {...props}
    />
  );
}

/** A row: the glyph for where it lands, what it is, and on the right the one fact telling it from
 *  its neighbours. The glyph stands under the box's own, so a row's words start where the typed
 *  words do. The row under the cursor is marked by fill, the way a menu marks the item the
 *  keyboard is on. */
export function CommandItem({
  icon: Glyph,
  primary,
  fact,
  className,
  ...props
}: Omit<ComponentProps<typeof CommandPrimitive.Item>, "children"> & {
  icon: TablerIcon;
  primary: string;
  fact?: string;
}) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "flex cursor-default select-none items-center gap-sm rounded-control px-sm py-sm text-ui",
        "outline-none data-[selected=true]:bg-fill",
        className,
      )}
      {...props}
    >
      <Glyph className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
      <span className="min-w-0 truncate">{primary}</span>
      {fact ? (
        <span className="ml-auto shrink-0 truncate font-mono text-small text-ink-soft">{fact}</span>
      ) : null}
    </CommandPrimitive.Item>
  );
}

/** What the list says when it is not answering with rows: that a read is running, that nothing
 *  matched, that a kind refused. It takes the padding of a row so it stands in the column the rows
 *  stand in rather than against the panel's edge. */
export function CommandNote({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="command-note"
      className={cn("m-0 px-sm py-sm text-ui text-ink-soft", className)}
      {...props}
    />
  );
}
