import {
  IconChevronDown,
  IconFilter,
  IconLayoutGrid,
  IconList,
  IconX,
} from "@tabler/icons-react";
import {
  Fragment,
  createContext,
  useContext,
  useEffect,
  useRef,
  type ComponentProps,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button, buttonVariants } from "@/components/ui/button";
import { BesideHost } from "@/kernel/beside";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { FilterOption } from "@/components/ui/filter";
import { ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
import { cn } from "@/lib/cn";

/** The one measure the portal is read at, centred in whatever width the shell leaves — a
 *  transcript, and a screen of records alike. A conversation needs it because prose has a line
 *  length the eye can return along; a screen of records takes the same one so that moving between
 *  a conversation and the records behind it does not move the column under the member. It bounds
 *  the content, never the pane: the scroll and the gutter stay at the pane's own edges, so a
 *  scrollbar sits where the member reaches for it rather than beside the text. */
export const COLUMN = "mx-auto w-full max-w-page";

/** The pane a destination draws in. It runs the full width the shell leaves, because the top bar's
 *  rule separates the page's header from its body and a rule that stops two thirds of the way
 *  across states a boundary the surface does not have. */
export function Pane({ className, children, ...props }: ComponentProps<"main">) {
  return (
    <main {...props} className={cn("flex min-h-0 min-w-0 flex-col", className)}>
      <BesideHost>{children}</BesideHost>
    </main>
  );
}

/** Every screen is this: one scrolling column, a gutter either side, and a stack of bands one gap
 *  apart. The page owns that rhythm rather than each band carrying its own margins — a band that
 *  spaced itself would add to the gap instead of sitting in it, so the distance between a title and
 *  its records would be a sum of whatever the two happened to declare. The measure is what the
 *  shell leaves rather than a fixed column, because the record opened beside a list is what decides
 *  how much width a list has, and a list that stayed narrow while its pane grew would leave the act
 *  on each row stranded in the middle of the screen. */
export function Page({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      {...props}
      className={cn(
        BANDS,
        "min-h-0 min-w-0 flex-1 overflow-y-auto scrollbar-gutter-stable",
        "px-(--size-page-gutter) py-(--size-page-top) max-narrow:px-2xl",
        className,
      )}
    />
  );
}

/** The one gap a stack of bands is set at, and the only place it is written. A band carries no
 *  margin of its own, so whatever stacks bands has to state this — `Page` for a screen, the tab
 *  panel for the views a strip switches between, the record drawer for a record's groups. A
 *  container that stacks bands and forgets it renders them flush, which is a fault the bands
 *  themselves cannot see. */
export const BANDS = "flex flex-col gap-6xl";

/** The band a page is headed by: what the page is on the left, and on the right the acts that reach
 *  the whole of it — the search over every record and the one that makes another. What narrows the
 *  records to a family stays with the records, so the head of the page and the head of the table
 *  each carry the controls that answer to them.
 *
 *  The name is the page's own, at the size the words around it are set — not a masthead. A page
 *  reached by pressing its name in the sidebar has been announced already, and a title set three
 *  steps larger than everything under it states a beginning where the member is only continuing.
 *  Where the page stands among siblings, the name is also the control that moves between them, so
 *  the one word the member is reading is the one they press to leave — and the destinations do not
 *  need a strip of their own under the band.
 *
 *  A phone has no room for all three on one line, and the title is the only part that can give —
 *  which is the page's own name, so it must not. The band stacks below the narrow breakpoint: the
 *  title keeps its line and the controls take the one beneath it. They wrap among themselves rather
 *  than shrink: the search states a width it is readable at, and one squeezed below it is a box the
 *  member cannot type into. */
export function PageHeader({
  title,
  siblings,
  onPick,
  search,
  action,
}: {
  /** Absent where the shell above already named the page — the band then carries the acts alone,
   *  rather than a second heading saying the word the member just pressed. */
  title?: string;
  /** The destinations this one stands among, current included. Two or more make the name the
   *  control that moves between them; one draws a plain name, because a chevron that opens a
   *  list of one is a control with nothing to do. */
  siblings?: FilterOption[];
  onPick?: (value: string) => void;
  search?: ReactNode;
  action?: ReactNode;
}) {
  const switches = siblings && siblings.length > 1 && onPick;
  return (
    <div
      className={cn(
        "flex h-(--size-control) shrink-0 items-center gap-sm",
        "max-narrow:h-auto max-narrow:flex-col max-narrow:items-stretch",
      )}
    >
      {title ? (
        <h1 className="m-0 min-w-0 flex-1 truncate text-body font-medium">
          {switches ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className={cn(
                    "flex min-w-0 items-center gap-2xs rounded-control border-0 bg-transparent",
                    "-mx-xs px-xs py-2xs font-sans text-inherit",
                    "transition-[background-color] duration-100 ease-control",
                    "hover:bg-fill data-[state=open]:bg-fill",
                  )}
                >
                  <span className="min-w-0 truncate">{title}</span>
                  <IconChevronDown className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuRadioGroup
                  value={siblings.find((entry) => entry.label === title)?.value ?? ""}
                  onValueChange={onPick}
                >
                  {siblings.map((entry) => (
                    <DropdownMenuRadioItem key={entry.value} value={entry.value}>
                      {entry.label}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            title
          )}
        </h1>
      ) : (
        <span className="flex-1 max-narrow:hidden" />
      )}
      {search || action ? (
        <div className="flex min-w-0 flex-wrap items-center gap-sm max-narrow:w-full">
          {search}
          {action}
        </div>
      ) : null}
    </div>
  );
}

const PageActContext = createContext<HTMLElement | null>(null);

/** The header's act slot, offered to whatever view the shell draws under it. A shell that switches
 *  between tabs holds one header over all of them, so the act that makes a record cannot be declared
 *  beside the tab: whether the member may take it at all arrives with the view's own read, long
 *  after the header stood. The shell holds the slot and the view fills it from where it is. */
export function PageActs({ host, children }: { host: HTMLElement | null; children: ReactNode }) {
  return <PageActContext.Provider value={host}>{children}</PageActContext.Provider>;
}

/** Puts a view's one act on the header above it. A view drawn under no shell keeps the act where it
 *  was returned, so a panel mounted on its own still states it. */
export function usePageAct(act: ReactNode | null): ReactNode {
  const host = useContext(PageActContext);
  if (act === null) return null;
  return host ? createPortal(act, host) : act;
}

/** The band that narrows the records: which family of them to show, on the page's own left edge and
 *  a band clear of both the title above and the records below. The order they are in is not here —
 *  it sits on the head of the column it orders, where the member is already pointing.
 *
 *  A phone fits one such control on a line, so below the narrow breakpoint they wrap rather than
 *  share: two rows of choices on one line leave each of them a strip too narrow to read a label
 *  in. */
export function PageToolbar({ children }: { children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex h-(--size-control) shrink-0 items-center gap-sm",
        "max-narrow:h-auto max-narrow:flex-wrap",
      )}
    >
      {children}
    </div>
  );
}

/** The rule between the controls that narrow a listing and the controls that redraw it. They
 *  answer different questions — which records, and in what shape — and a bar that ran them
 *  together reads as one row of glyphs with no seam in it.
 *
 *  It stretches rather than centring: the bar centres what it holds, and a rule is a line with no
 *  content to take a height from, so a centred one is drawn at no height at all. A phone wraps the
 *  bar onto more than one line, where a vertical rule divides whichever two controls happen to
 *  land beside each other — so there it is not drawn. */
export function ToolbarRule() {
  return <span aria-hidden className="my-xs w-px shrink-0 self-stretch bg-edge max-narrow:hidden" />;
}

/** The shape a member reads a listing in. `tiles` leads with each record's own picture and is
 *  what a member scanning for something they would recognise by sight wants; `table` states the
 *  same records as facts in columns, which is what comparing them down a page needs. */
export type Face = "tiles" | "table";

const FACES: { face: Face; label: string; glyph: typeof IconLayoutGrid }[] = [
  { face: "tiles", label: "Tiles", glyph: IconLayoutGrid },
  { face: "table", label: "Table", glyph: IconList },
];

/** Which shape the records are drawn in, held at the right of the bar where the acts on the whole
 *  listing stand. It is a switch and not a filter: pressing it changes nothing about which records
 *  are on the screen, so it sits past the rule rather than among the narrowings.
 *
 *  A press on the shape already picked is swallowed. The control states one of two shapes at all
 *  times, and a group holding one answer clears that answer on a second press — which would leave
 *  the listing with no shape at all and nothing on the screen saying so. The picked shape draws as
 *  the checked radio it is, which is what a group of one answer reads out as. */
export function ViewSwitch({ face, onPick }: { face: Face; onPick: (face: Face) => void }) {
  return (
    <ToggleGroupOne
      value={face}
      onValueChange={(next) => next && onPick(next as Face)}
      aria-label="Shape"
      className="flex shrink-0 items-center gap-hair"
    >
      {FACES.map(({ face: name, label, glyph: Glyph }) => (
        <ToggleGroupItem
          key={name}
          value={name}
          aria-label={label}
          className={cn(
            buttonVariants({ variant: "row", size: "icon" }),
            "border-transparent text-ink-soft hover:bg-fill",
            "aria-checked:bg-fill aria-checked:text-ink",
          )}
        >
          <Glyph className="size-(--size-glyph)" aria-hidden />
        </ToggleGroupItem>
      ))}
    </ToggleGroupOne>
  );
}

/** One named run of narrowings inside the filter menu. The groups name the axes a member thinks
 *  in — what kind of record, what kind of file — and the options inside a group are what that axis
 *  offers. */
export type FacetGroup = { label: string; options: FilterOption[] };

/** Every narrowing a listing offers, behind one glyph. A listing narrowed on more than one axis
 *  cannot draw them all as pill rows: two rows of pills fill the bar the search and the shape
 *  switch also stand in, and the member reads six choices to make one. The menu states the axes by
 *  name instead, so the same bar holds a listing with one narrowing and a listing with four.
 *
 *  The narrowings are one radio group across every named run, not one per run: a listing holds a
 *  single narrowing at a time, and a menu drawing a tick beside a choice in each group would claim
 *  the member had picked several. The empty value is every record and leads the menu, so the way
 *  back to the whole listing is the first thing under the glyph rather than a second press on
 *  whatever is picked. */
export function FacetMenu({
  groups,
  value,
  onPick,
}: {
  groups: FacetGroup[];
  value: string;
  onPick: (value: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="row"
          size="icon"
          aria-label="Filter"
          className={cn(
            "border-transparent text-ink-soft hover:bg-fill",
            value && "bg-fill text-ink",
          )}
        >
          <IconFilter className="size-glyph" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={value} onValueChange={onPick}>
          <DropdownMenuRadioItem value="">All</DropdownMenuRadioItem>
          {groups.map((group) => (
            <Fragment key={group.label}>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>{group.label}</DropdownMenuLabel>
              {group.options.map((option) => (
                <DropdownMenuRadioItem key={option.value} value={option.value}>
                  {option.label}
                </DropdownMenuRadioItem>
              ))}
            </Fragment>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A record opened beside the list it came from: a column of the pane rather than a sheet laid
 *  over it, so the list stays readable and closing the record is a press rather than a way back.
 *  Under `--breakpoint-narrow` there is no room for two columns, so it covers the pane instead.
 *
 *  It takes focus when it opens and gives it back when it closes — but only if it still holds it.
 *  A pane may host two of these, and the one displaced unmounts a commit after its replacement has
 *  already focused itself; a panel that handed focus back unconditionally would take it out of the
 *  panel the member just opened and drop them behind it. Escape leaves it. A record
 *  that opened where the member was not looking, and that only an unlabelled corner glyph could
 *  shut, is one a keyboard never reaches — and at narrow widths the panel covers the pane, so that
 *  member would have nothing to go back to. Escape is taken only where nothing else has claimed it:
 *  a select open inside the form answers that key first, and a panel that shut on it would take the
 *  whole form away when the member meant to close a menu. */
export function RecordPanel({
  title,
  describedBy,
  onClose,
  children,
}: {
  title: string;
  /** The id of the one line stating what this record's form does, where it has one — a heading
   *  names the panel, and a member arriving on it by keyboard hears only that name otherwise. */
  describedBy?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const held = useRef<HTMLElement>(null);
  useEffect(() => {
    const before = document.activeElement;
    const panel = held.current;
    panel?.focus();
    return () => {
      if (!(before instanceof HTMLElement) || !document.body.contains(before)) return;
      const active = document.activeElement;
      const claimed =
        active instanceof HTMLElement && active !== document.body && !panel?.contains(active);
      if (claimed) return;
      before.focus();
    };
  }, []);
  return (
    <aside
      ref={held}
      aria-label={title}
      aria-describedby={describedBy}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.stopPropagation();
        onClose();
      }}
      className={cn(
        "relative flex min-h-0 min-w-0 flex-1 flex-col gap-(--size-record-gutter)",
        "border-l border-edge py-2xl",
        "max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:border-l-0",
        "max-narrow:bg-surface",
      )}
    >
      <BesideHost over>
        <header className="flex h-(--size-control) shrink-0 items-center gap-md px-(--size-record-gutter)">
          <h2 className="m-0 flex-1 truncate text-subtitle font-medium">{title}</h2>
          <Button size="icon" aria-label="Close" onClick={onClose}>
            <IconX className="size-icon" aria-hidden />
          </Button>
        </header>
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col gap-(--size-record-gutter)",
            "overflow-y-auto scrollbar-gutter-stable px-(--size-record-gutter)",
          )}
        >
          {children}
        </div>
      </BesideHost>
    </aside>
  );
}

/** Where the member is, and what they can do about it — the one header shape every screen that
 *  sits *inside* something else wears. A root has no crumb, because there is nothing above it to
 *  name; a record has one, because the list it came from is the way back and a title alone makes
 *  the member find that list again in the sidebar.
 *
 *  Every parent in this portal is reached by a callback rather than an address — an agent's own
 *  conversation lives in a place on its agent's hash, not at one of its own — so `parent` takes
 *  the verb that gets there rather than a URL.
 *
 *  `note` is what the record says about itself in passing: a model, a slot, the kind of thing it
 *  is. `actions` are the acts on the record, and they stand at the far end as pills so a row of
 *  them reads as one band of controls rather than as chrome tucked under a heading. Alignment is
 *  on the box, not the baseline: a pill and a word share a centre, never a baseline.
 *
 *  A phone holds the name and the acts on one line only by hiding some of the acts past an edge
 *  that marks nothing, so the band stacks below the narrow breakpoint the way `PageHeader` does:
 *  the name keeps its line and the acts wrap among themselves on the one beneath it. */
export function PaneHeader({
  parent,
  current,
  note,
  actions,
}: {
  parent?: { label: string; onGo?: () => void };
  current: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "box-content flex min-h-(--size-control) items-center gap-md border-b border-edge px-2xl py-lg",
        "max-narrow:flex-col max-narrow:items-stretch",
      )}
    >
      <Breadcrumb className="min-w-0">
        <BreadcrumbList className="flex-nowrap">
          {parent ? (
            <>
              <BreadcrumbItem>
                {parent.onGo ? (
                  <BreadcrumbLink aria-label={"Back to " + parent.label} onClick={parent.onGo}>
                    {parent.label}
                  </BreadcrumbLink>
                ) : (
                  parent.label
                )}
              </BreadcrumbItem>
              <BreadcrumbSeparator />
            </>
          ) : null}
          <BreadcrumbItem className="min-w-0">
            <BreadcrumbPage>{current}</BreadcrumbPage>
            {note}
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      {actions ? (
        <div className="ml-auto flex items-center gap-sm max-narrow:ml-0 max-narrow:flex-wrap">
          {actions}
        </div>
      ) : null}
    </div>
  );
}
