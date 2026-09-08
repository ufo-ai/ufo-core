import {
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

export const COLUMN = "mx-auto w-full max-w-page";

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
  onLift?: (event: DragEvent<HTMLDivElement>) => void;
  onDoubleClick?: ComponentProps<"div">["onDoubleClick"];
  pinned?: boolean;
  ruled?: boolean;
}) {
  const banded = useContext(BandedContext);
  if (banded && crumb === undefined && onClose === undefined)
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
        {crumb === undefined && title === undefined ? (
          <span className="flex-1 max-narrow:hidden" />
        ) : (
          <div className={cn(NAME, band.name, ruled && "min-w-0")}>
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

export function ToolbarRule() {
  return <span aria-hidden className="my-xs w-px shrink-0 self-stretch bg-edge max-narrow:hidden" />;
}

export type Face = "tiles" | "table";

const FACES: { face: Face; label: string; glyph: typeof IconLayoutGrid }[] = [
  { face: "tiles", label: "Tiles", glyph: IconLayoutGrid },
  { face: "table", label: "Table", glyph: IconList },
];

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
