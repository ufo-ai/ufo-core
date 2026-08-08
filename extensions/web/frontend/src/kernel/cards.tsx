import { useState, type ReactNode } from "react";

/** The mark a card leads with: a square placeholder for a per-row logo, or a full-width band that
 *  carries the row's own image once it has one. Either way the placeholder holds the space. */
export type CardMark<Row> =
  | { shape: "square" }
  | { shape: "band"; image?: (row: Row) => string | null };

/** A grid of cards holds its rhythm only while every card is the same height, so the text a row
 *  supplies is cut to a fixed number of lines: one for the name, two for the description. A card
 *  states enough to choose by; the whole of it is on the row's own screen. */
export function CardGrid<Row>({
  rows,
  rowKey,
  mark,
  primary,
  status,
  body,
  action,
}: {
  rows: Row[];
  rowKey: (row: Row) => string;
  mark: CardMark<Row>;
  primary: (row: Row) => ReactNode;
  status?: (row: Row) => ReactNode;
  body?: (row: Row) => ReactNode;
  action?: (row: Row) => ReactNode;
}) {
  return (
    <ul className="m-0 grid list-none grid-cols-2 gap-lg p-0 max-narrow:grid-cols-1">
      {rows.map((row) => {
        const said = body?.(row);
        const act = action?.(row);
        const state = status ? (
          <div data-part="status" className="whitespace-nowrap text-small opacity-(--muted)">
            {status(row)}
          </div>
        ) : null;
        return (
          <li
            key={rowKey(row)}
            className="flex flex-col overflow-hidden rounded-panel border border-edge bg-surface"
          >
            {mark.shape === "band" ? <Band src={mark.image?.(row) ?? null} /> : null}
            <div className="flex flex-1 flex-col p-xl">
              {mark.shape === "square" ? (
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
                {mark.shape === "band" ? state : null}
              </div>
              {said ? (
                <p
                  data-part="body"
                  className="m-0 mt-sm line-clamp-2 text-small opacity-(--muted-soft)"
                >
                  {said}
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
