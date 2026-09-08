import { Command as CommandPrimitive } from "cmdk";
import { IconChevronLeft } from "@tabler/icons-react";
import {
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
  type RefObject,
} from "react";

import { cn } from "@/lib/cn";

/** cmdk's vim bindings — which spend `ctrl+k` on moving the cursor up — are off: `ctrl+k` is kill-line
 *  in a readline-shaped field, and the box is one of those. */
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
          "w-full min-w-0 border-0 bg-transparent p-0 text-body text-inherit outline-none placeholder:text-ink-soft",
          className,
        )}
        {...props}
      />
      {trailing ? <span className="shrink-0">{trailing}</span> : null}
    </div>
  );
}

export function CommandList({
  className,
  children,
  ...props
}: ComponentProps<typeof CommandPrimitive.List>) {
  const rows = useRef<HTMLDivElement>(null);
  return (
    <div className="relative flex min-h-0 flex-col">
      <CommandPrimitive.List
        ref={rows}
        data-slot="command-list"
        className={cn("h-(--size-palette) min-h-0 scroll-py-2xs overflow-y-auto p-2xs", className)}
        {...props}
      >
        {children}
      </CommandPrimitive.List>
      <CommandStack rows={rows} />
    </div>
  );
}

type Stacked = { heading: string; top: number };

const GROUP = "[cmdk-group]";

const GROUP_HEADING = "[cmdk-group-heading]";

function same(held: Stacked[], next: Stacked[]): boolean {
  return (
    held.length === next.length &&
    held.every((run, at) => run.heading === next[at].heading && run.top === next[at].top)
  );
}

function below(rows: HTMLDivElement): Stacked[] {
  const fold = rows.clientHeight;
  if (!fold) return [];
  const top = rows.getBoundingClientRect().top;
  const groups = [...rows.querySelectorAll<HTMLElement>(GROUP)];
  const stack: Stacked[] = [];
  let taken = 0;
  for (let at = groups.length - 1; at >= 0; at--) {
    const heading = groups[at].querySelector<HTMLElement>(GROUP_HEADING);
    if (!heading) continue;
    const height = heading.getBoundingClientRect().height;
    const stands = groups[at].getBoundingClientRect().top - top + rows.scrollTop;
    if (stands <= rows.scrollTop + fold - taken - height) break;
    stack.unshift({ heading: heading.textContent ?? "", top: stands });
    taken += height;
  }
  return stack;
}

/** The stack is out of the reading and tab order: cmdk already names every run to a screen reader. */
function CommandStack({ rows }: { rows: RefObject<HTMLDivElement | null> }) {
  const [stack, setStack] = useState<Stacked[]>([]);
  useEffect(() => {
    const list = rows.current;
    if (!list) return;
    const measure = () => {
      const next = below(list);
      setStack((held) => (same(held, next) ? held : next));
    };
    measure();
    list.addEventListener("scroll", measure);
    const resized = new ResizeObserver(measure);
    resized.observe(list);
    const changed = new MutationObserver(measure);
    changed.observe(list, { childList: true, subtree: true, characterData: true });
    return () => {
      list.removeEventListener("scroll", measure);
      resized.disconnect();
      changed.disconnect();
    };
  }, [rows]);
  if (!stack.length) return null;
  return (
    <div
      data-slot="command-stack"
      aria-hidden
      className="pointer-events-none absolute inset-x-2xs bottom-0 flex flex-col"
    >
      {stack.map((run) => (
        <button
          key={run.heading}
          type="button"
          tabIndex={-1}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => rows.current?.scrollTo({ top: run.top, behavior: "smooth" })}
          className="pointer-events-auto flex h-(--size-row) shrink-0 items-center border-0 bg-popover px-lg text-left text-label text-ink-soft hover:text-ink"
        >
          {run.heading}
        </button>
      ))}
    </div>
  );
}

/** cmdk hides the heading element itself and points the group's label at it, so the heading is the
 *  words a member would use for the kind. */
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

const FACT = "ml-auto shrink-0 truncate text-label text-ink-soft";

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
  if (Glyph) {
    return (
      <CommandPrimitive.Item
        data-slot="command-item"
        className={cn(ROW, "h-(--size-palette-app) gap-lg", className)}
        {...props}
      >
        <span className="flex size-(--size-palette-mark) shrink-0 items-center justify-center">
          <Glyph className="size-(--size-glyph) text-ink-soft" aria-hidden />
        </span>
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-ui text-ink">{primary}</span>
          {secondary ? (
            <span className="truncate text-label text-ink-soft">{secondary}</span>
          ) : null}
        </span>
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
      <span className="min-w-0 truncate text-ui text-ink">{primary}</span>
      {fact ? <span className={FACT}>{fact}</span> : null}
    </CommandPrimitive.Item>
  );
}

export function CommandNote({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="command-note"
      className={cn("m-0 px-lg py-sm text-ui text-ink-soft", className)}
      {...props}
    />
  );
}

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
