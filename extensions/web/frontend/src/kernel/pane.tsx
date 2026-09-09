import {
  IconChevronLeft,
  IconFilter,
  IconLayoutGrid,
  IconList,
  IconX,
} from "@tabler/icons-react";
import {
  Fragment,
  createContext,
  useContext,
  type ComponentProps,
  type DragEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button, buttonVariants } from "@/components/ui/button";
import type { Crumb } from "@/lib/title";
import { Empty } from "@/kernel/panel";
import { SlotTrack } from "@/kernel/slots";
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
import { GLYPH_STROKE } from "@/lib/glyph";

/** The one measure the portal is read at, centred in whatever width the shell leaves — a
 *  transcript, and a screen of records alike. A conversation needs it because prose has a line
 *  length the eye can return along; a screen of records takes the same one so that moving between
 *  a conversation and the records behind it does not move the column under the member. It bounds
 *  the content, never the pane: the scroll and the gutter stay at the pane's own edges, so a
 *  scrollbar sits where the member reaches for it rather than beside the text. */
export const COLUMN = "mx-auto w-full max-w-page";

/** The pane a destination draws in. It runs the full width the shell leaves, because the top bar's
 *  rule separates the page's header from its body and a rule that stops two thirds of the way
 *  across states a boundary the surface does not have.
 *
 *  `opens` is the order the lanes stand in and `onMove` is where a lane carried by hand is written,
 *  handed straight to the track. They arrive together or not at all: an order that could be read
 *  without being written would take a drag nothing records, and the address would then name a row
 *  the member is not looking at. A pane whose lanes no address holds passes neither. */
export function Pane({
  className,
  children,
  opens,
  onMove,
  ...props
}: ComponentProps<"main"> &
  (
    | { opens: string[]; onMove: (opens: string[]) => void }
    | { opens?: undefined; onMove?: undefined }
  )) {
  return (
    <main {...props} className={cn("flex min-h-0 min-w-0 flex-col", className)}>
      {opens === undefined ? (
        <SlotTrack>{children}</SlotTrack>
      ) : (
        <SlotTrack opens={opens} onMove={onMove}>
          {children}
        </SlotTrack>
      )}
    </main>
  );
}

/** A sentence standing where a screen could not draw: the pane holds it centered, so the note
 *  reads as the screen's whole answer rather than a row that happens to be alone. */
export function PaneNote({ children }: { children: ReactNode }) {
  return (
    <Pane className={COLUMN}>
      <Empty>{children}</Empty>
    </Pane>
  );
}

const BandedContext = createContext(false);

export function Banded({ value, children }: { value: boolean; children: ReactNode }) {
  return <BandedContext.Provider value={value}>{children}</BandedContext.Provider>;
}

/** Every screen is this: one scrolling column, a gutter either side, and a stack of bands one gap
 *  apart. The page owns that rhythm rather than each band carrying its own margins — a band that
 *  spaced itself would add to the gap instead of sitting in it, so the distance between a title and
 *  its records would be a sum of whatever the two happened to declare. The measure is what the
 *  shell leaves rather than a fixed column, because the record opened beside a list is what decides
 *  how much width a list has, and a list that stayed narrow while its pane grew would leave the act
 *  on each row stranded in the middle of the screen.
 *
 *  Under a band the gutter is the lane's, not the screen's: one padding all round, the same the
 *  lane's own body and a transcript take, so the page's first line starts where the band's title
 *  above it does. The screen's gutter is a measure for the width a screen has — a third of a
 *  320-pixel lane on each side, and a page top deeper than the band heading it. The narrow
 *  breakpoint cannot answer it either: it reads the viewport, and a lane is narrow inside a window
 *  that is not.
 *
 *  The padding is stated all round rather than dropped at the top where the acts row stands, because
 *  a page offering no act draws no row at all — the row hides itself — and only the page's own
 *  padding holds every banded page clear of the band above it. */
export function Page({ className, ...props }: ComponentProps<"div">) {
  const banded = useContext(BandedContext);
  return (
    <div
      data-slot="page"
      {...props}
      className={cn(
        BANDS,
        "min-h-0 min-w-0 flex-1 overflow-y-auto scrollbar-gutter-stable",
        banded ? "p-2xl" : "px-(--size-page-gutter) py-(--size-page-top) max-narrow:px-2xl",
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

const NAME = "flex min-w-(--container-title) flex-1 items-center max-narrow:min-w-0";


const LANE_BAND = {
  pad: "px-2xl py-sm",
  name: "gap-2xs",
  title: "tracking-(--tracking-ui)",
  acts: "gap-md",
};
const PAGE_BAND = { pad: "px-2xl py-lg", name: "gap-sm", title: "", acts: "gap-sm" };

const BAND_ACTS =
  "flex shrink-0 flex-wrap items-center justify-end gap-sm px-2xl py-lg " +
  "not-has-[:not(.contents)]:hidden";

/** The band every surface is headed by: where the member is on the left, and on the right what
 *  they can do about the whole of it. A destination, a record opened beside it, a half of the
 *  agent pane, and a sheet all wear this one band, so a member moving between them finds the name,
 *  the acts and the way out in the same places rather than learning each screen's own chrome.
 *
 *  The order is fixed and not the caller's: the way back, the name, what the name says about
 *  itself in passing, the search over the whole surface, the acts, and the way out last. A band
 *  that let each screen order its own contents would state a different reading order on every one
 *  of them, and the way out would move.
 *
 *  The name is set at the size the pills beside it are, so the band reads as one row of chrome and
 *  the first heading on the screen is the content under it. It keeps its measure at every width
 *  above the narrow breakpoint — the acts give, never the title. Below that breakpoint there is no
 *  room for both on one line and the name is the part that cannot go, so the band stacks: the name
 *  keeps its line and the acts wrap on the one beneath.
 *
 *  `heading` is the level the name is a heading at: the destination the member navigated to is the
 *  page's `1`, a surface standing inside that page is `2`. A band drawing a crumb takes it too —
 *  the crumb's last step is the name, so that step is the heading rather than a second one drawn
 *  beside it. Omitted, the name is plain text: a surface the landmark it stands in already names
 *  takes no second name.
 *
 *  `onBack` leaves a view standing inside the surface without shutting the surface, so it is a
 *  chevron beside the name it leaves rather than a cross among the acts: the cross states that
 *  what the band names is going, and here what the band names is where the press lands.
 *
 *  `crumb` is where the member came from and nothing else. It carries an address, not a verb: a
 *  crumb to a place is navigation, so it is a link that can be opened in a second tab, and shutting
 *  the surface is `onClose` — a verb of the lane, which is a different act with a different word for
 *  it. A crumb standing at no address is the landmark's name, drawn as text.
 *
 *  A screen states one heading at level 1, and this band is where it stands. A view whose name is
 *  prose the band's one line cannot hold states it whole in its own body as well — the crumb is cut
 *  to the width it has, never to what a reader hears — and the body's copy is the one that is not a
 *  heading.
 *
 *  `pinned` is where the band stands, and both the rule and the inset follow from it. A band that
 *  scrolls with its page is lined up by that page's own gutter and has no boundary under it to
 *  draw; a band held above a scroller has neither, so it states its own inset and the rule the
 *  content passes beneath.
 *
 *  Under a band, a header is its acts: a lane already names the page and holds the way out, so the
 *  name, the crumb, the mark, the note, the lede, the bar and the close would be a second header
 *  saying what the first one said. What the band cannot carry is the act the page itself offers, so
 *  that is what is left standing — a row at the top of the body, where the acts were. */
export function Header({
  heading,
  crumb,
  glyph,
  title,
  note,
  acts,
  lede,
  bar,
  onClose,
  closes,
  onBack,
  onLift,
  onDoubleClick,
  pinned = false,
  ruled = false,
}: {
  heading?: 1 | 2;
  crumb?: Crumb;
  glyph?: ReactNode;
  title?: ReactNode;
  note?: ReactNode;
  acts?: ReactNode;
  lede?: string;
  bar?: ReactNode;
  onClose?: () => void;
  closes?: string;
  onBack?: () => void;
  onLift?: (event: DragEvent<HTMLDivElement>) => void;
  onDoubleClick?: ComponentProps<"div">["onDoubleClick"];
  pinned?: boolean;
  ruled?: boolean;
}) {
  const banded = useContext(BandedContext);
  if (banded && crumb === undefined && onClose === undefined && onBack === undefined)
    return (
      <div data-slot="page-acts" className={BAND_ACTS}>
        {acts}
      </div>
    );
  const band = ruled ? LANE_BAND : PAGE_BAND;
  const Name = heading === 1 ? "h1" : heading === 2 ? "h2" : "span";
  return (
    <div
      data-slot="header"
      draggable={onLift ? true : undefined}
      onDragStart={onLift}
      onDoubleClick={onDoubleClick}
      className={cn(
        "flex shrink-0 flex-col gap-sm",
        pinned && band.pad,
        ruled && "shadow-rule",
        onLift && "cursor-ew-resize",
      )}
      style={
        pinned
          ? { paddingRight: "calc(var(--spacing-2xl) + var(--pane-acts-inset, 0px))" }
          : undefined
      }
    >
      <div
        className={cn(
          "flex h-(--size-control) items-center gap-md",
          "max-narrow:h-auto max-narrow:flex-col max-narrow:items-stretch",
        )}
      >
        {crumb === undefined && title === undefined && onBack === undefined ? (
          <span className="flex-1 max-narrow:hidden" />
        ) : (
          <div className={cn(NAME, band.name, ruled && "min-w-0")}>
            {onBack ? (
              <Button
                variant={ruled ? "mark" : "quiet"}
                size={ruled ? "glyph" : "icon"}
                aria-label="Back"
                className="-ms-2xs shrink-0"
                onClick={onBack}
              >
                <IconChevronLeft aria-hidden stroke={ruled ? GLYPH_STROKE : undefined} />
              </Button>
            ) : null}
            {glyph ? (
              <span
                aria-hidden
                className={cn(
                  "flex shrink-0 [&_svg]:size-(--size-glyph)",
                  ruled ? "text-ink" : "text-ink-soft",
                )}
              >
                {glyph}
              </span>
            ) : null}
            {crumb ? (
              <Breadcrumb className="min-w-0 flex-1">
                <BreadcrumbList className="min-w-0 flex-nowrap">
                  <BreadcrumbItem>
                    {crumb.at ? (
                      <BreadcrumbLink href={crumb.at} aria-label={"Back to " + crumb.label}>
                        {crumb.label}
                      </BreadcrumbLink>
                    ) : (
                      crumb.label
                    )}
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem className="min-w-0">
                    <Name className={cn("m-0 flex min-w-0 text-label", band.title)}>
                      <BreadcrumbPage>{title}</BreadcrumbPage>
                    </Name>
                    {note}
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            ) : (
              <>
                <Name
                  className={cn("m-0 min-w-0 flex-1 truncate text-label font-medium", band.title)}
                >
                  {title}
                </Name>
                {note}
              </>
            )}
          </div>
        )}
        {acts || onClose ? (
          <div
            className={cn(
              "flex shrink-0 items-center",
              band.acts,
              "max-narrow:w-full max-narrow:flex-wrap",
            )}
          >
            {acts}
            {onClose ? (
              <Button
                variant={ruled ? "mark" : "quiet"}
                size={ruled ? "glyph" : "icon"}
                aria-label={closes ? "Close " + closes : "Close"}
                onClick={onClose}
              >
                <IconX aria-hidden stroke={ruled ? GLYPH_STROKE : undefined} />
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {lede ? <p className="m-0 max-w-hint text-label text-ink-soft">{lede}</p> : null}
      {bar}
    </div>
  );
}

const PageActContext = createContext<HTMLElement | null>(null);

export function PageActs({ host, children }: { host: HTMLElement | null; children: ReactNode }) {
  return <PageActContext.Provider value={host}>{children}</PageActContext.Provider>;
}

export function usePageAct(act: ReactNode | null): ReactNode {
  const host = useContext(PageActContext);
  if (act === null) return null;
  return host ? createPortal(act, host) : act;
}

const PageHeadContext = createContext<HTMLElement | null>(null);

export function PageHead({ host, children }: { host: HTMLElement | null; children: ReactNode }) {
  return <PageHeadContext.Provider value={host}>{children}</PageHeadContext.Provider>;
}

/** Puts a view's own band above the shell it is drawn in. A view drawn under no shell keeps the
 *  band where it was returned, so a screen mounted on its own still states its name. */
export function usePageHead(band: ReactNode): ReactNode {
  const host = useContext(PageHeadContext);
  return host ? createPortal(band, host) : band;
}

const PageSearchContext = createContext<ReactNode>(null);

export function PageSearch({ node, children }: { node: ReactNode; children: ReactNode }) {
  return <PageSearchContext.Provider value={node}>{children}</PageSearchContext.Provider>;
}

export function usePageSearch(): ReactNode {
  return useContext(PageSearchContext);
}

/** The band that narrows the records: which family of them to show, on the page's own left edge and
 *  a band clear of both the title above and the records below. The order they are in is not here —
 *  it sits on the head of the column it orders, where the member is already pointing.
 *
 *  A phone fits one such control on a line, so below the narrow breakpoint they wrap rather than
 *  share: two rows of choices on one line leave each of them a strip too narrow to read a label
 *  in. */
export function PageToolbar({ className, children, ...props }: ComponentProps<"div">) {
  const search = useContext(PageSearchContext);
  return (
    <div
      {...props}
      className={cn(
        "flex h-(--size-control) shrink-0 items-center gap-sm",
        "max-narrow:h-auto max-narrow:flex-wrap",
        className,
      )}
    >
      {search}
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
          <Glyph aria-hidden />
        </ToggleGroupItem>
      ))}
    </ToggleGroupOne>
  );
}

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
          <IconFilter aria-hidden />
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
