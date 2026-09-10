import {
  Children,
  cloneElement,
  isValidElement,
  useState,
  type DragEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  IconChevronDown,
  IconCircle,
  IconCircleDotted,
  IconCircleFilled,
  IconCircleHalf,
  IconGripVertical,
} from "@tabler/icons-react";

import "@/blocks/item.css";

const STATUS_ICONS = {
  ready: IconCircleDotted,
  started: IconCircle,
  working: IconCircleHalf,
  done: IconCircleFilled,
} as const;
const ICON_SIZE = 16;
const ICON_STROKE = 1.5;

export type ItemStatus = keyof typeof STATUS_ICONS;
export type ItemVariant = "default" | "outline" | "muted";
export type ItemSize = "default" | "sm" | "xs";
export type ItemAccent = "primary" | "muted" | "none";
export type ItemState = "default" | "active" | "disabled" | "past";
export type ItemMediaVariant = "default" | "icon" | "image" | "avatar" | "status" | "checkbox";
export type ItemFont = "sans" | "mono";

export type ItemDrag = {
  draggable?: boolean;
  onDragStart?: (event: DragEvent<HTMLElement>) => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDrop?: (event: DragEvent<HTMLElement>) => void;
  onDragEnd?: (event: DragEvent<HTMLElement>) => void;
};

/** A list row holding media, content, meta and actions, with an optional accent bar. */
export function Item({
  variant = "default",
  size = "default",
  accent = "none",
  state = "default",
  selected,
  dragging,
  editing,
  dragHandle,
  render,
  onClick,
  children,
  ...drag
}: ItemDrag & {
  variant?: ItemVariant;
  size?: ItemSize;
  accent?: ItemAccent;
  state?: ItemState;
  selected?: boolean;
  dragging?: boolean;
  editing?: boolean;
  dragHandle?: boolean;
  render?: ReactElement<{ className?: string }>;
  onClick?: () => void;
  children: ReactNode;
}) {
  const attrs = {
    className: "blk-item",
    "data-variant": variant,
    "data-size": size,
    "data-accent": accent,
    "data-state": state,
    "data-selected": selected ? "true" : undefined,
    "data-dragging": dragging ? "true" : undefined,
    "data-editing": editing ? "true" : undefined,
    ...drag,
  };
  const lead = (
    <>
      {accent === "none" ? null : <span className="blk-item-bar" aria-hidden="true" />}
      {dragHandle ? (
        <span className="blk-item-grip" aria-hidden="true">
          <IconGripVertical size={ICON_SIZE} stroke={ICON_STROKE} />
        </span>
      ) : null}
    </>
  );
  if (render !== undefined && isValidElement(render)) {
    return cloneElement(render, attrs as Partial<typeof render.props>, lead, children);
  }
  if (onClick) {
    const shut = state === "disabled";
    return (
      <div
        {...attrs}
        role="button"
        tabIndex={shut ? -1 : 0}
        data-interactive="true"
        aria-disabled={shut ? true : undefined}
        onClick={shut ? undefined : onClick}
        onKeyDown={(event) => {
          if (shut || (event.key !== "Enter" && event.key !== " ")) return;
          event.preventDefault();
          onClick();
        }}
      >
        {lead}
        {children}
      </div>
    );
  }
  return (
    <div {...attrs}>
      {lead}
      {children}
    </div>
  );
}

function dropSeat(event: DragEvent<HTMLElement>, over: number, from: number): number {
  const box = event.currentTarget.getBoundingClientRect();
  const to = event.clientY > box.top + box.height / 2 ? over + 1 : over;
  return from < to ? to - 1 : to;
}

/** A vertical list of items and their headers. `flush` drops the rule under the last row. */
export function ItemGroup({
  flush = false,
  onReorder,
  children,
}: {
  flush?: boolean;
  onReorder?: (from: number, to: number) => void;
  children: ReactNode;
}) {
  const [held, setHeld] = useState<number | null>(null);
  const attrs = { className: "blk-item-group", "data-flush": flush ? "true" : undefined };
  if (!onReorder) return <div {...attrs}>{children}</div>;
  let seat = -1;
  return (
    <div {...attrs}>
      {Children.map(children, (child) => {
        if (!isValidElement(child) || child.type !== Item) return child;
        seat += 1;
        const at = seat;
        return cloneElement(child as ReactElement<ItemDrag & { dragging?: boolean }>, {
          draggable: true,
          dragging: held === at,
          onDragStart: () => setHeld(at),
          onDragOver: (event: DragEvent<HTMLElement>) => event.preventDefault(),
          onDrop: (event: DragEvent<HTMLElement>) => {
            event.preventDefault();
            setHeld(null);
            if (held === null) return;
            const to = dropSeat(event, at, held);
            if (to !== held) onReorder(held, to);
          },
          onDragEnd: () => setHeld(null),
        });
      })}
    </div>
  );
}

/** A rule that breaks a group into sections. */
export function ItemSeparator() {
  return <div className="blk-item-separator" role="separator" />;
}

/** The leading slot of a row: a plain slot, an icon, a status mark, a checkbox, an avatar, or a picture. */
export function ItemMedia({
  variant = "icon",
  font = "sans",
  children,
}: {
  variant?: ItemMediaVariant;
  font?: ItemFont;
  children: ReactNode;
}) {
  return (
    <span
      className="blk-item-media"
      data-variant={variant}
      data-font={font}
      onClick={variant === "checkbox" ? (event) => event.stopPropagation() : undefined}
    >
      {children}
    </span>
  );
}

/** The text column of a row, which takes the free width and cuts its lines to fit. */
export function ItemContent({ children }: { children: ReactNode }) {
  return <span className="blk-item-content">{children}</span>;
}

export type ItemEdit = {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
};

/** The first line of a row. `editable` swaps it for a field that saves on Enter and drops on Escape. */
export function ItemTitle({
  font = "sans",
  editable,
  children,
}: {
  font?: ItemFont;
  editable?: ItemEdit;
  children?: ReactNode;
}) {
  if (editable) {
    return (
      <input
        className="blk-item-title"
        data-font={font}
        aria-label="Title"
        autoFocus
        value={editable.value}
        onChange={(event) => editable.onChange(event.target.value)}
        onBlur={() => editable.onCommit()}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            editable.onCommit();
          }
          if (event.key === "Escape") {
            event.preventDefault();
            editable.onCancel();
          }
        }}
      />
    );
  }
  return (
    <span className="blk-item-title" data-font={font}>
      {children}
    </span>
  );
}

/** The second line of a row, cut with an ellipsis at the line count it is given. */
export function ItemDescription({
  lines = 1,
  font = "sans",
  children,
}: {
  lines?: 1 | 2 | 3;
  font?: ItemFont;
  children: ReactNode;
}) {
  return (
    <span className="blk-item-description" data-lines={lines} data-font={font}>
      {children}
    </span>
  );
}

/** A secondary line such as a time, a place, or a date. */
export function ItemMeta({ font = "sans", children }: { font?: ItemFont; children: ReactNode }) {
  return (
    <span className="blk-item-meta" data-font={font}>
      {children}
    </span>
  );
}

/** The trailing slot of a row. */
export function ItemActions({ children }: { children: ReactNode }) {
  return <span className="blk-item-actions">{children}</span>;
}

/** A full-width row under the content of an item. */
export function ItemFooter({ children }: { children: ReactNode }) {
  return <span className="blk-item-footer">{children}</span>;
}

export type ItemHeaderProps = {
  badge?: ReactNode;
  accent?: "primary" | "muted";
  collapsible?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
};

/** A heading carrying a badge and, when collapsible, the chevron that folds the rows under it. */
export function ItemHeader({
  badge,
  accent = "muted",
  collapsible = false,
  open,
  onOpenChange,
  children,
}: ItemHeaderProps) {
  const [held, setHeld] = useState(open ?? true);
  const isOpen = open ?? held;
  return (
    <div className="blk-item-header" data-accent={accent} data-open={isOpen}>
      {collapsible ? (
        <button
          type="button"
          className="blk-item-header-chevron"
          aria-expanded={isOpen}
          onClick={() => {
            setHeld(!isOpen);
            onOpenChange?.(!isOpen);
          }}
        >
          <IconChevronDown size={ICON_SIZE} stroke={ICON_STROKE} />
        </button>
      ) : null}
      {badge === undefined ? null : <span className="blk-item-header-badge">{badge}</span>}
      <span className="blk-item-header-label">{children}</span>
    </div>
  );
}

/** A group of rows under a heading of its own, which the chevron folds. `empty` stands in for no rows. */
export function ItemSection({
  label,
  badge,
  count,
  accent = "muted",
  collapsible = true,
  open,
  defaultOpen = true,
  onOpenChange,
  empty,
  children,
}: {
  label: ReactNode;
  badge?: ReactNode;
  count?: number;
  accent?: "primary" | "muted";
  collapsible?: boolean;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  empty?: ReactNode;
  children?: ReactNode;
}) {
  const [held, setHeld] = useState(defaultOpen);
  const expanded = collapsible ? (open ?? held) : true;
  const bare = Children.toArray(children).length === 0;
  return (
    <div className="blk-item-section" data-open={expanded}>
      <ItemHeader
        badge={badge}
        accent={accent}
        collapsible={collapsible}
        open={expanded}
        onOpenChange={(next) => {
          if (open === undefined) setHeld(next);
          onOpenChange?.(next);
        }}
      >
        {label}
        {count === undefined ? null : <Tag>{count}</Tag>}
      </ItemHeader>
      {expanded && bare && empty !== undefined ? (
        <span className="blk-item-section-empty">{empty}</span>
      ) : null}
      {expanded && !bare ? children : null}
    </div>
  );
}

/** A pill naming the collection a row belongs to. `onClick` makes it the filter the row toggles. */
export function Tag({
  onClick,
  pressed,
  children,
}: {
  onClick?: () => void;
  pressed?: boolean;
  children: ReactNode;
}) {
  if (onClick) {
    return (
      <button
        type="button"
        className="blk-tag"
        data-pressed={pressed ? "true" : undefined}
        aria-pressed={pressed === true}
        onClick={onClick}
      >
        {children}
      </button>
    );
  }
  return <span className="blk-tag">{children}</span>;
}

/** A small icon and number pair, such as the attendees of a meeting. */
export function Count({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="blk-count">
      {icon}
      {children}
    </span>
  );
}

/** The circle that reads a row's progress. `onClick` makes it the button that moves the row on. */
export function StatusIcon({
  status,
  onClick,
  label,
}: {
  status: ItemStatus;
  onClick?: () => void;
  label?: string;
}) {
  const Icon = STATUS_ICONS[status];
  if (onClick) {
    return (
      <button
        type="button"
        className="blk-status-button"
        aria-label={label ?? status}
        aria-pressed={status === "done"}
        onClick={onClick}
      >
        <Icon size={ICON_SIZE} stroke={ICON_STROKE} className="blk-status" data-status={status} aria-hidden="true" />
      </button>
    );
  }
  return (
    <Icon
      size={ICON_SIZE}
      stroke={ICON_STROKE}
      className="blk-status"
      data-status={status}
      role="img"
      aria-label={status}
    />
  );
}
