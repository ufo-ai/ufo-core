import { useEffect, useRef, useState, type ReactNode } from "react";

import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

export type CardMark<Row> =
  | { shape: "square" }
  | { shape: "band"; image?: (row: Row) => string | null; body?: (row: Row) => ReactNode }
  | { shape: "tile"; image?: (row: Row) => string | null; body?: (row: Row) => ReactNode };

/** An act is laid beside the tile's own control rather than inside it, since a control inside a control
 *  is not markup a browser keeps. */
export function CardGrid<Row>({
  rows,
  rowKey,
  mark,
  primary,
  status,
  body,
  meta,
  action,
  open,
  current,
  whole,
}: {
  rows: Row[];
  rowKey: (row: Row) => string;
  mark?: CardMark<Row>;
  primary: (row: Row) => ReactNode;
  status?: (row: Row) => ReactNode;
  body?: (row: Row) => ReactNode;
  meta?: (row: Row) => ReactNode;
  action?: (row: Row) => ReactNode;
  open?: (row: Row) => (() => void) | null;
  current?: (row: Row) => boolean;
  whole?: boolean;
}) {
  return (
    <ul
      className={cn(
        "m-0 grid list-none gap-lg p-0",
        mark?.shape === "tile"
          ? "grid-cols-[repeat(auto-fill,minmax(var(--size-tile),1fr))]"
          : "grid-cols-2 max-narrow:grid-cols-1",
      )}
    >
      {rows.map((row) => {
        const press = open?.(row) ?? null;
        const control = press ? rowControl(press) : null;
        const standing = current?.(row) ?? false;
        if (mark?.shape === "tile") {
          const out = action?.(row);
          return (
            <li
              key={rowKey(row)}
              aria-current={standing || undefined}
              className={cn("relative flex flex-col gap-sm", standing && "rounded-panel bg-fill")}
            >
              <Tile press={press}>
                <Picture
                  src={mark.image?.(row) ?? null}
                  body={mark.body?.(row)}
                  className={cn(
                    "aspect-square w-full rounded-panel border border-edge",
                    "transition-[border-color] duration-100 ease-control",
                    "group-hover:border-edge-strong",
                  )}
                />
                <div data-part="primary" className="w-full truncate text-body font-medium">
                  {primary(row)}
                </div>
                {status ? (
                  <div data-part="status" className="w-full truncate text-small text-ink-soft">
                    {status(row)}
                  </div>
                ) : null}
              </Tile>
              {out ? <div className="absolute top-sm right-sm flex gap-xs">{out}</div> : null}
            </li>
          );
        }
        const said = body?.(row);
        const from = meta?.(row);
        const act = action?.(row);
        const state = status ? (
          <div data-part="status" className="whitespace-nowrap text-small text-ink-soft">
            {status(row)}
          </div>
        ) : null;
        return (
          <li
            key={rowKey(row)}
            {...control}
            aria-current={standing || undefined}
            className={cn(
              "flex flex-col overflow-hidden rounded-panel border border-edge bg-card text-card-foreground",
              control?.className,
              press && "hover:bg-fill",
              standing && "bg-fill",
            )}
          >
            {mark?.shape === "band" ? (
              <Picture
                src={mark.image?.(row) ?? null}
                body={mark.body?.(row)}
                className="h-(--size-band) w-full border-b border-edge"
              />
            ) : null}
            <div className="flex flex-1 flex-col p-xl">
              {mark?.shape === "square" ? (
                <div className="mb-lg flex items-start justify-between gap-md">
                  <div
                    data-part="mark"
                    aria-hidden
                    className="size-7xl rounded-panel bg-fill"
                  />
                  {state}
                </div>
              ) : null}
              <div className="flex items-baseline justify-between gap-md">
                <div data-part="primary" className="min-w-0 truncate text-body font-medium">
                  {primary(row)}
                </div>
                {mark?.shape !== "square" ? state : null}
              </div>
              {said ? (
                <p
                  data-part="body"
                  className={cn(
                    "m-0 mt-sm text-small text-ink-soft",
                    !whole && "line-clamp-2 max-narrow:line-clamp-none",
                  )}
                >
                  {said}
                </p>
              ) : null}
              {from ? (
                <p
                  data-part="meta"
                  className="m-0 mt-sm truncate font-mono text-mono text-ink-soft"
                >
                  {from}
                </p>
              ) : null}
              {act ? <div className="mt-auto pt-lg">{act}</div> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function codeSpans(text: string): ReactNode {
  const segments = text.split("`");
  return segments.map((segment, index) =>
    index % 2 === 1 && index < segments.length - 1 ? (
      <code key={index} className="font-mono text-mono">
        {segment}
      </code>
    ) : (
      segment
    ),
  );
}

const TILE = "group flex w-full flex-col items-start gap-sm text-left";

function Tile({ press, children }: { press: (() => void) | null; children: ReactNode }) {
  if (!press) return <div className={TILE}>{children}</div>;
  return (
    <button
      type="button"
      onClick={press}
      className={cn(TILE, "cursor-pointer border-0 bg-transparent p-0 font-sans text-inherit")}
    >
      {children}
    </button>
  );
}

function Picture({
  src,
  body,
  className,
}: {
  src: string | null;
  body?: ReactNode;
  className: string;
}) {
  const [failed, setFailed] = useState(false);
  const [neared, setNeared] = useState(false);
  const mark = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = mark.current;
    if (!node) return;
    const watcher = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setNeared(true);
        watcher.disconnect();
      }
    });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);
  return (
    <div
      ref={mark}
      data-part="mark"
      aria-hidden
      className={cn("overflow-hidden bg-fill", className)}
    >
      {src && !failed ? (
        <img
          loading="lazy"
          alt=""
          src={src}
          onError={() => setFailed(true)}
          className="size-full object-cover object-top"
        />
      ) : neared ? (
        (body ?? null)
      ) : null}
    </div>
  );
}
