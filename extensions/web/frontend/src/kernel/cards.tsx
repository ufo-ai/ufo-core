import { useEffect, useRef, useState, type ReactNode } from "react";

import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

/** The mark a card leads with: a square placeholder for a per-row logo, or a full-width band that
 *  carries the row's own image once it has one — or, with no image, whatever `body` renders for
 *  the row. Either way the placeholder holds the space. A record that will never earn a picture
 *  takes none, and its status rides the name's line. */
export type CardMark<Row> =
  | { shape: "square" }
  | { shape: "band"; image?: (row: Row) => string | null; body?: (row: Row) => ReactNode };

/** A grid of cards holds its rhythm only while every card is the same height, so the text a row
 *  supplies is cut to a fixed number of lines: one for the name, two for the description. A card
 *  states enough to choose by; the whole of it is on the row's own screen. A phone's grid is one
 *  column, where there is no second card on the line to keep level with — so the description runs
 *  as long as it is, and a record with no screen of its own is still read in full.
 *
 *  A record that has no screen of its own at any width takes `whole`: the card is all there is to
 *  read it on, so its description runs to the end of the sentence rather than to the second line.
 *
 *  `open` hands the card to `rowControl`, so the whole card is the control that opens the record
 *  and a press on the card's own act never also opens it. */
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
    <ul className="m-0 grid list-none grid-cols-2 gap-lg p-0 max-narrow:grid-cols-1">
      {rows.map((row) => {
        const said = body?.(row);
        const from = meta?.(row);
        const act = action?.(row);
        const press = open?.(row) ?? null;
        const control = press ? rowControl(press) : null;
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
              <Band src={mark.image?.(row) ?? null} body={mark.body?.(row)} />
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
                <div data-part="primary" className="min-w-0 truncate font-display text-body">
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

/** The band mounts its `body` fallback only once it nears the viewport — the text counterpart of
 *  the image's `loading="lazy"`, so a long listing reads only the excerpts the member scrolls to. */
function Band({ src, body }: { src: string | null; body?: ReactNode }) {
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
      className="h-(--size-band) w-full overflow-hidden border-b border-edge bg-fill"
    >
      {src && !failed ? (
        <img
          loading="lazy"
          alt=""
          src={src}
          onError={() => setFailed(true)}
          className="size-full object-cover"
        />
      ) : neared ? (
        (body ?? null)
      ) : null}
    </div>
  );
}
