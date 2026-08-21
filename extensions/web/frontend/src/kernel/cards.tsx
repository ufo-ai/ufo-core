import { useEffect, useRef, useState, type ReactNode } from "react";

import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

/** The mark a card leads with: a square placeholder for a per-row logo, or a full-width band that
 *  carries the row's own image once it has one — or, with no image, whatever `body` renders for
 *  the row. Either way the placeholder holds the space. A record that will never earn a picture
 *  takes none, and its status rides the name's line.
 *
 *  A tile reads the row's picture the same two ways a band does, and is the whole of what the grid
 *  draws for that row, so the mark settles the shape of the grid rather than the head of a card. */
export type CardMark<Row> =
  | { shape: "square" }
  | { shape: "band"; image?: (row: Row) => string | null; body?: (row: Row) => ReactNode }
  | { shape: "tile"; image?: (row: Row) => string | null; body?: (row: Row) => ReactNode };

/** A grid of cards holds its rhythm only while every card is the same height, so the text a row
 *  supplies is cut to a fixed number of lines: one for the name, two for the description. A card
 *  states enough to choose by; the whole of it is on the row's own screen. A phone's grid is one
 *  column, where there is no second card on the line to keep level with — so the description runs
 *  as long as it is, and a record with no screen of its own is still read in full.
 *
 *  A record that has no screen of its own at any width takes `whole`: the card is all there is to
 *  read it on, so its description runs to the end of the sentence rather than to the second line.
 *
 *  A `tile` mark makes the grid dense and picture-first, the way a library of pictures is read:
 *  the track fills with as many `--size-tile` columns as the width takes, at every width. The
 *  picture is the tile, so it carries none of a card's chrome — a fill and a padding box drawn
 *  around a picture only pad what already fills its own bounds. It keeps one hairline, which is
 *  what gives a tile whose picture is missing or still loading a box to stand in, and which is
 *  what strengthens under the pointer. The
 *  name and the status sit under it as plain text; the description and the meta line are not
 *  drawn, since a tile states its picture, its name and where it stands. An act the record carries
 *  is laid over the corner of its picture — beside the tile's own control rather than inside it,
 *  since a control inside a control is not markup a browser keeps, and above the tile rather than
 *  under it, since only some records carry an act and a grid that gave those rows an extra line
 *  would step down the page wherever one of them landed.
 *
 *  `open` makes the whole record the control that reaches it, drawn the way each shape can afford.
 *  A card holds acts of its own, so it takes `rowControl` and a press on one of those acts never
 *  also opens the record. A tile holds none, so it is a button outright and the `li` around it
 *  keeps the role that makes the grid a list to a reader. */
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
        if (mark?.shape === "tile") {
          const out = action?.(row);
          return (
            <li key={rowKey(row)} className="relative flex flex-col gap-sm">
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
            className={cn(
              "flex flex-col overflow-hidden rounded-panel border border-edge bg-card text-card-foreground",
              control?.className,
              press && "hover:bg-fill",
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

/** A card body's sentence renders its backtick-quoted literals as code spans, never as raw
 *  backticks. */
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

/** What a tile is pressed by. A card holds acts of its own, so it takes `rowControl` and cannot be
 *  a button — a button inside a button is not markup a browser keeps. A tile holds none: its
 *  picture, its name and its moment are all it draws, so the whole of it is one real button, and
 *  the `li` around it keeps the role that makes the grid a list to a reader.
 *
 *  A tile that opens nothing is a plain box. It takes no tab stop and no pointer, because a record
 *  the member cannot open is not a control. Either way it is the `group` its picture answers the
 *  pointer as, so hovering anywhere on the tile — the name included — is what draws the edge. */
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

/** The picture mounts its `body` fallback only once it nears the viewport — the text counterpart of
 *  the image's `loading="lazy"`, so a long listing reads only the excerpts the member scrolls to.
 *  A band and a tile differ in the box the picture is held in and in nothing else, so one watcher
 *  serves both. */
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
