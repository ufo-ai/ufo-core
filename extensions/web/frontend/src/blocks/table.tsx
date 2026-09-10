import {
  Children,
  cloneElement,
  isValidElement,
  useState,
  type CSSProperties,
  type DragEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  IconArrowDown,
  IconArrowUp,
  IconChevronDown,
  IconCircle,
  IconCircleDotted,
  IconCircleFilled,
  IconCircleHalf,
} from "@tabler/icons-react";

import "@/blocks/table.css";

const SCORE_HIGH = 70;
const SCORE_MID = 30;
const SCORE_SPAN = 100;
const SECTION_ICON = {
  ready: IconCircleDotted,
  started: IconCircle,
  working: IconCircleHalf,
  done: IconCircleFilled,
};

type Align = "left" | "right";
type SectionStatus = keyof typeof SECTION_ICON;

export type TableHideBelow = 640 | 880;
export type TableRowDrag = {
  draggable?: boolean;
  onDragStart?: (event: DragEvent<HTMLTableRowElement>) => void;
  onDragOver?: (event: DragEvent<HTMLTableRowElement>) => void;
  onDrop?: (event: DragEvent<HTMLTableRowElement>) => void;
  onDragEnd?: (event: DragEvent<HTMLTableRowElement>) => void;
};

/** Rows and columns on real table semantics, scrolling sideways when the columns outgrow their box. */
export function Table({
  density = "default",
  children,
}: {
  density?: "default" | "dense";
  children: ReactNode;
}) {
  return (
    <div className="blk-table-wrap">
      <table className="blk-table" data-density={density}>
        {children}
      </table>
    </div>
  );
}

/** The column headings of a table. */
export function TableHeader({ children }: { children: ReactNode }) {
  return <thead className="blk-table-header">{children}</thead>;
}

function dropSeat(event: DragEvent<HTMLTableRowElement>, over: number, from: number): number {
  const box = event.currentTarget.getBoundingClientRect();
  const to = event.clientY > box.top + box.height / 2 ? over + 1 : over;
  return from < to ? to - 1 : to;
}

/** The data rows of a table. `onReorder` makes them draggable and reports where one was dropped. */
export function TableBody({
  onReorder,
  children,
}: {
  onReorder?: (from: number, to: number) => void;
  children: ReactNode;
}) {
  const [held, setHeld] = useState<number | null>(null);
  if (!onReorder) return <tbody className="blk-table-body">{children}</tbody>;
  let seat = -1;
  return (
    <tbody className="blk-table-body">
      {Children.map(children, (child) => {
        if (!isValidElement(child) || child.type !== TableRow) return child;
        seat += 1;
        const at = seat;
        return cloneElement(child as ReactElement<TableRowDrag & { dragging?: boolean }>, {
          draggable: true,
          dragging: held === at,
          onDragStart: () => setHeld(at),
          onDragOver: (event: DragEvent<HTMLTableRowElement>) => event.preventDefault(),
          onDrop: (event: DragEvent<HTMLTableRowElement>) => {
            event.preventDefault();
            setHeld(null);
            if (held === null) return;
            const to = dropSeat(event, at, held);
            if (to !== held) onReorder(held, to);
          },
          onDragEnd: () => setHeld(null),
        });
      })}
    </tbody>
  );
}

/** The summary rows below the data. */
export function TableFooter({ children }: { children: ReactNode }) {
  return <tfoot className="blk-table-footer">{children}</tfoot>;
}

/** One row, optionally selectable, clickable, dragged, edited, or marked done or disabled. */
export function TableRow({
  selected,
  dragging,
  editing,
  onClick,
  state = "default",
  children,
  ...drag
}: TableRowDrag & {
  selected?: boolean;
  dragging?: boolean;
  editing?: boolean;
  onClick?: () => void;
  state?: "default" | "done" | "disabled";
  children: ReactNode;
}) {
  return (
    <tr
      className="blk-table-row"
      data-state={state}
      data-selected={selected ? "true" : undefined}
      data-dragging={dragging ? "true" : undefined}
      data-editing={editing ? "true" : undefined}
      data-clickable={onClick ? "true" : undefined}
      {...drag}
      aria-selected={selected}
      aria-disabled={state === "disabled" ? true : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              onClick();
            }
          : undefined
      }
    >
      {children}
    </tr>
  );
}

function wrapText(children: ReactNode): ReactNode {
  return Children.map(children, (child) =>
    typeof child === "string" || typeof child === "number" ? (
      <span className="blk-table-text">{child}</span>
    ) : (
      child
    ),
  );
}

/** One column heading, sized to a width, able to sort, and able to drop out of a narrow table. */
export function TableHead({
  align = "left",
  width,
  sortable,
  sorted = false,
  hideBelow,
  onSort,
  children,
}: {
  align?: Align;
  width?: number | string;
  sortable?: boolean;
  sorted?: "asc" | "desc" | false;
  hideBelow?: TableHideBelow;
  onSort?: () => void;
  children?: ReactNode;
}) {
  const style: CSSProperties | undefined = width === undefined ? undefined : { width };
  const order = sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none";
  const arrow =
    sorted === "asc" ? (
      <IconArrowUp size={12} stroke={1.5} />
    ) : sorted === "desc" ? (
      <IconArrowDown size={12} stroke={1.5} />
    ) : null;
  return (
    <th
      className="blk-table-head"
      scope="col"
      data-align={align}
      data-hide-below={hideBelow}
      data-sorted={sorted === false ? undefined : sorted}
      style={style}
      aria-sort={sortable || sorted !== false ? order : undefined}
    >
      {sortable && onSort ? (
        <button type="button" className="blk-table-sort" onClick={onSort}>
          {wrapText(children)}
          {arrow}
        </button>
      ) : (
        <span className="blk-table-cell-inner">
          {wrapText(children)}
          {arrow}
        </span>
      )}
    </th>
  );
}

/** One cell, aligned left or right and dimmed to secondary text, or half of it, when muted. */
export function TableCell({
  align = "left",
  muted,
  colSpan,
  hideBelow,
  children,
}: {
  align?: Align;
  muted?: boolean | "dim";
  colSpan?: number;
  hideBelow?: TableHideBelow;
  children?: ReactNode;
}) {
  const ink = muted === true ? "true" : muted === "dim" ? "dim" : undefined;
  return (
    <td
      className="blk-table-cell"
      data-align={align}
      data-muted={ink}
      data-hide-below={hideBelow}
      colSpan={colSpan}
    >
      <span className="blk-table-cell-inner">{wrapText(children)}</span>
    </td>
  );
}

/** A line of secondary text below the table saying what it holds. */
export function TableCaption({ children }: { children: ReactNode }) {
  return <caption className="blk-table-caption">{children}</caption>;
}

/** A group of rows under a collapsible heading that spans every column. */
export function TableSection({
  title,
  count,
  status,
  columns = 1,
  open,
  defaultOpen = true,
  onOpenChange,
  children,
}: {
  title: string;
  count?: number;
  status?: SectionStatus;
  columns?: number;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [held, setHeld] = useState(defaultOpen);
  const expanded = open ?? held;
  const Status = status ? SECTION_ICON[status] : null;
  return (
    <tbody className="blk-table-section">
      <tr className="blk-table-section-row">
        <td className="blk-table-section-cell" colSpan={columns}>
          <button
            type="button"
            className="blk-table-section-toggle"
            data-status={status}
            aria-expanded={expanded}
            onClick={() => {
              if (open === undefined) setHeld(!expanded);
              onOpenChange?.(!expanded);
            }}
          >
            <IconChevronDown size={16} stroke={1.5} className="blk-table-section-chevron" />
            {Status ? <Status size={16} stroke={1.5} className="blk-table-section-status" /> : null}
            <span className="blk-table-section-title">{title}</span>
            {count === undefined ? null : <TableTag>{count}</TableTag>}
          </button>
        </td>
      </tr>
      {expanded ? children : null}
    </tbody>
  );
}

/** The row a table shows in place of data it does not have. */
export function TableEmpty({ columns = 1, children }: { columns?: number; children: ReactNode }) {
  return (
    <tr className="blk-table-row">
      <td className="blk-table-empty-cell" colSpan={columns}>
        {children}
      </td>
    </tr>
  );
}

/** Placeholder rows that shimmer while the data loads. */
export function TableSkeleton({ rows, columns }: { rows: number; columns: number }) {
  return (
    <tbody className="blk-table-body">
      {Array.from({ length: rows }, (_, row) => (
        <tr className="blk-table-row" key={row}>
          {Array.from({ length: columns }, (_, column) => (
            <td className="blk-table-cell" key={column}>
              <span className="blk-table-shimmer" />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/** A small filled label for a count or a category. */
export function TableTag({ children }: { children: ReactNode }) {
  return <span className="blk-table-tag">{children}</span>;
}

/** Small dimmed text for a row's date or count. */
export function TableMeta({ children }: { children: ReactNode }) {
  return <span className="blk-table-meta">{children}</span>;
}

/** A score with a dot in the primary ink, faded towards the ground as the score falls. */
export function ScoreDot({ value }: { value: number }) {
  const tier = value >= SCORE_HIGH ? "high" : value >= SCORE_MID ? "mid" : "low";
  return (
    <span
      className="blk-score"
      data-tier={tier}
      style={{ "--blk-score": value / SCORE_SPAN } as CSSProperties}
    >
      <span className="blk-score-dot" />
      <span className="blk-score-value">{value}</span>
    </span>
  );
}

/** A 16px square standing for the thing a row is about. */
export function Mark({ children }: { children?: ReactNode }) {
  return <span className="blk-mark">{children}</span>;
}

/** Two overlapping 16px circles standing for the people on a row. */
export function AvatarPair({ colors }: { colors: [string, string] }) {
  return (
    <span className="blk-avatar-pair">
      <span className="blk-avatar" style={{ background: colors[0] }} />
      <span className="blk-avatar" style={{ background: colors[1] }} />
    </span>
  );
}
