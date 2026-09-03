import { Command as CommandPrimitive } from "cmdk";
import { IconChevronLeft } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** One box over a list of rows, moved through by arrow keys and taken by Enter. The rows are
 *  options under the box's combobox rather than buttons, so the member reaches every one of them
 *  without leaving the field they are typing in.
 *
 *  cmdk's vim bindings — which spend `ctrl+k` on moving the cursor up — are off: `ctrl+k` is
 *  kill-line in a readline-shaped field, and the box is one of those fields. */
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

/** The field, ruled off from the rows beneath it. It draws no mark of its own: the cursor is
 *  already in it when the panel opens, so a glyph saying "this searches" states what the member is
 *  doing rather than what they can do. Nothing here is filled either — fill is what marks the row
 *  under the cursor, and a second filled shape in the panel reads as a second thing to pick.
 *
 *  `onBack` puts a chevron before the field, which is the one mark that earns the column: it says
 *  the panel is inside something and names the way out. `trailing` holds the key the box itself
 *  answers to, at the end of the field the key acts on. */
export function CommandInput({
  className,
  onBack,
  trailing,
  ...props
}: ComponentProps<typeof CommandPrimitive.Input> & {
  onBack?: () => void;
  trailing?: ReactNode;
}) {
  return (
    <div
      data-slot="command-input-wrapper"
      className="flex h-8xl shrink-0 items-center gap-sm border-b border-edge px-2xl"
    >
      {onBack ? (
        <button
          type="button"
          aria-label="Back"
          onClick={onBack}
          className="-ms-2xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
        >
          <IconChevronLeft className="size-(--size-glyph)" aria-hidden />
        </button>
      ) : null}
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          "w-full min-w-0 border-0 bg-transparent p-0 text-label text-inherit outline-none placeholder:text-ink-soft",
          className,
        )}
        {...props}
      />
      {trailing ? <span className="shrink-0">{trailing}</span> : null}
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

/** A named run of rows, its rows a hair apart so a run reads as one block and the fill under the
 *  cursor still reads as one row. The heading names the group to a screen reader as well as to the
 *  eye — cmdk hides the heading element itself and points the group's label at it — so the heading
 *  is the words a member would use for the kind, never a decoration over the rows. `action` narrows
 *  the run to part of itself and stands at the right of the heading's row; it is drawn beside the
 *  hidden heading rather than inside it, so the keyboard still reaches it. */
export function CommandGroup({
  heading,
  action,
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof CommandPrimitive.Group>, "heading"> & {
  heading: string;
  action?: ReactNode;
}) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      heading={
        <span className="flex h-(--size-row) items-center px-lg text-label text-ink-soft">
          {heading}
        </span>
      }
      className={cn(
        "relative overflow-hidden py-2xs",
        "[&_[cmdk-group-items]]:flex [&_[cmdk-group-items]]:flex-col [&_[cmdk-group-items]]:gap-2xs",
        className,
      )}
      {...props}
    >
      {action ? (
        <div className="absolute top-2xs right-lg flex h-(--size-row) items-center">{action}</div>
      ) : null}
      {children}
    </CommandPrimitive.Group>
  );
}

const ROW =
  "flex cursor-default items-center rounded-control px-lg outline-none select-none data-[selected=true]:bg-fill";

const FACT = "ml-auto shrink-0 truncate text-small tabular-nums text-ink-soft";

/** A row, in the one of three shapes its own content asks for. An application carries a second line
 *  under its name, so it takes the tallest pitch and stands its mark in a box that fixes where every
 *  name in the run starts, whatever glyph is drawn inside. A place or a scope is a glyph and a name,
 *  and takes the row pitch the rest of the portal keeps. A thread is a title and the time it last
 *  moved, and takes the tightest pitch of the three: a run of them is read straight down, and a
 *  glyph repeated on every line would be a column of the same mark. `fact` is the one figure telling
 *  a row from its neighbours and sits flush right in figures of one width, so the times under it
 *  line up. The row under the cursor is marked by fill, the way a menu marks the item the keyboard
 *  is on. */
export function CommandItem({
  icon: Glyph,
  primary,
  secondary,
  fact,
  className,
  ...props
}: Omit<ComponentProps<typeof CommandPrimitive.Item>, "children"> & {
  icon?: (props: { className?: string; "aria-hidden"?: boolean }) => ReactNode;
  primary: string;
  secondary?: string;
  fact?: string;
}) {
  if (secondary) {
    return (
      <CommandPrimitive.Item
        data-slot="command-item"
        className={cn(ROW, "h-(--size-palette-app) gap-2xl", className)}
        {...props}
      >
        <span className="flex size-(--size-palette-mark) shrink-0 items-center justify-center">
          {Glyph ? <Glyph className="size-(--size-glyph) text-ink-soft" aria-hidden /> : null}
        </span>
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-label text-ink">{primary}</span>
          <span className="truncate text-small text-ink-soft">{secondary}</span>
        </span>
        {fact ? <span className={FACT}>{fact}</span> : null}
      </CommandPrimitive.Item>
    );
  }

  if (Glyph) {
    return (
      <CommandPrimitive.Item
        data-slot="command-item"
        className={cn(ROW, "h-(--size-row) gap-sm", className)}
        {...props}
      >
        <Glyph className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
        <span className="min-w-0 truncate text-label text-ink">{primary}</span>
        {fact ? <span className={FACT}>{fact}</span> : null}
      </CommandPrimitive.Item>
    );
  }

  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(ROW, "h-(--size-palette-thread) gap-sm", className)}
      {...props}
    >
      <span className="min-w-0 truncate text-label text-ink">{primary}</span>
      {fact ? <span className={FACT}>{fact}</span> : null}
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
      className={cn("m-0 px-lg py-sm text-label text-ink-soft", className)}
      {...props}
    />
  );
}

/** The bar under the rows: on the left what the panel is showing, on the right the keys that act
 *  on it as it stands. A hint names the act and then the key, so the bar is read as a sentence the
 *  member can carry out, and a key that does nothing here is left out rather than dimmed. */
export function CommandFoot({
  lead,
  hints,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  lead: ReactNode;
  hints: { label: string; keys: string[] }[];
}) {
  return (
    <div
      data-slot="command-foot"
      className={cn(
        "flex shrink-0 items-center gap-2xl border-t border-edge px-2xl py-lg text-label text-ink-soft",
        className,
      )}
      {...props}
    >
      <span className="flex min-w-0 items-center gap-xs truncate font-medium text-ink">{lead}</span>
      <span className="ml-auto flex shrink-0 items-center gap-2xl">
        {hints.map((hint) => (
          <span key={hint.label} className="flex items-center gap-sm">
            {hint.label}
            {hint.keys.map((key) => (
              <CommandKbd key={key}>{key}</CommandKbd>
            ))}
          </span>
        ))}
      </span>
    </div>
  );
}

/** One key, drawn as the cap it is printed on: a ground and no edge, the characters opened out so
 *  two of them read as one key rather than as a word. It is the same cap the shortcuts sheet lists,
 *  so a key a member learns in the palette is the key they recognise everywhere else. */
export function CommandKbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="command-kbd"
      className={cn(
        "inline-flex h-4xl min-w-4xl items-center justify-center rounded-key bg-fill px-xs font-sans text-small tracking-key text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}
