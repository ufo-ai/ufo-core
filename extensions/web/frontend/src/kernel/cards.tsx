import { useState, type ReactNode } from "react";

import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

/** The mark a card leads with: a square placeholder for a per-row logo, or a full-width band that
 *  carries the row's own image once it has one. Either way the placeholder holds the space. A
 *  record that will never earn a picture takes none, and its status rides the name's line. */
export type CardMark<Row> =
  | { shape: "square" }
  | { shape: "band"; image?: (row: Row) => string | null };

/** A grid of cards holds its rhythm only while every card is the same height, so the text a row
 *  supplies is cut to a fixed number of lines: one for the name, two for the description. A card
 *  states enough to choose by; the whole of it is on the row's own screen.
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
          <div data-part="status" className="whitespace-nowrap text-small opacity-(--muted)">
            {status(row)}
          </div>
        ) : null;
        return (
          <li
            key={rowKey(row)}
            {...control}
            className={cn(
              "flex flex-col overflow-hidden rounded-panel border border-edge bg-surface",
              control?.className,
              press && "hover:bg-fill-hover",
            )}
          >
            {mark?.shape === "band" ? <Band src={mark.image?.(row) ?? null} /> : null}
            <div className="flex flex-1 flex-col p-xl">
              {mark?.shape === "square" ? (
                <div className="mb-lg flex items-start justify-between gap-md">
                  <div
                    data-part="mark"
                    aria-hidden
                    className="size-6xl rounded-panel bg-fill-subtle"
                  />
                  {state}
                </div>
              ) : null}
              <div className="flex items-baseline justify-between gap-md">
                <div data-part="primary" className="min-w-0 truncate text-body">
                  {primary(row)}
                </div>
                {mark?.shape !== "square" ? state : null}
              </div>
              {said ? (
                <p
                  data-part="body"
                  className="m-0 mt-sm line-clamp-2 text-small opacity-(--muted-soft)"
                >
                  {said}
                </p>
              ) : null}
              {from ? (
                <p
                  data-part="meta"
                  className="m-0 mt-sm truncate font-mono text-mono opacity-(--muted-soft)"
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

function Band({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <div data-part="mark" aria-hidden className="h-(--size-band) w-full border-b border-edge bg-fill-subtle">
      {src && !failed ? (
        <img
          loading="lazy"
          alt=""
          src={src}
          onError={() => setFailed(true)}
          className="size-full object-cover"
        />
      ) : null}
    </div>
  );
}
