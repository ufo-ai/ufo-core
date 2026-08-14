import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

/** The column a record stands in, offered to whatever is drawn inside the pane rather than only to
 *  the shell that drew it. A create form belongs beside the list it will join, but the list is
 *  several components deep inside the page's own scroller — and the column has to be a sibling of
 *  that scroller, not a child of it, or it scrolls away with the rows. So the pane holds the column
 *  and a view fills it from wherever it happens to be. */
type Slot = {
  /** Whether a host is above at all. A view drawn outside any pane has no column to reach. */
  hosted: boolean;
  host: HTMLElement | null;
  open: (id: string) => void;
  close: (id: string) => void;
  /** Every record the column is holding, last on top. A record needs to know not only whether it is
   *  the one shown but whether it is held at all, so it can tell being displaced from not having
   *  reached the column yet. */
  stack: string[];
};

const BesideContext = createContext<Slot>({
  hosted: false,
  host: null,
  open: () => {},
  close: () => {},
  stack: [],
});

const GRID = "relative grid min-h-0 flex-1 grid-cols-(--grid-slot) max-narrow:grid-cols-1";
const OVER = "absolute inset-0 z-10 flex min-h-0 flex-col bg-surface";

/** The pane's second column, drawn only while something is in it. The column holds one record —
 *  the last opened — because it is one column: a screen that opened a create form while a record
 *  stood there would otherwise put two panels in a slot sized for one, and the second lands under
 *  the table instead of beside it.
 *
 *  The wrapper is always the same element, `display: contents` while the column is shut, because a
 *  wrapper that appeared only when something opened would move every list one level down the tree
 *  the moment it did — and React reads that as a different component, unmounts the list, and takes
 *  with it the very state that asked for the column.
 *
 *  `over` is the host a record panel keeps for its own contents. An act raised from inside that
 *  panel cannot be drawn in the column the panel is already standing in: filling the column means
 *  emptying it of the panel, which unmounts the very tab that raised the act and takes the act with
 *  it. So the panel's own host lies over it instead — the opener stays mounted underneath, and the
 *  form still opens in the sidebar rather than over the middle of the screen. What it covers is
 *  `inert` while it stands — the whole panel, its heading included, since the cover reaches the
 *  panel's own edges and a Close button left tabbable behind it shuts the record the form is
 *  standing on. */
export function BesideHost({ over = false, children }: { over?: boolean; children: ReactNode }) {
  const [stack, setStack] = useState<string[]>([]);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const open = useCallback(
    (id: string) => setStack((held) => [...held.filter((entry) => entry !== id), id]),
    [],
  );
  const close = useCallback(
    (id: string) => setStack((held) => held.filter((entry) => entry !== id)),
    [],
  );
  const shown = stack.length > 0;
  const slot = useMemo(
    () => ({ hosted: true, host, open, close, stack }),
    [host, open, close, stack],
  );
  const held = (
    <div className="contents" inert={over && shown}>
      {children}
    </div>
  );
  return (
    <BesideContext.Provider value={slot}>
      {over ? (
        <>
          {held}
          <div ref={setHost} className={shown ? OVER : "contents"} />
        </>
      ) : (
        <div className={shown ? GRID : "contents"}>
          {held}
          <div ref={setHost} className="contents" />
        </div>
      )}
    </BesideContext.Provider>
  );
}

/** Puts a record in the pane's second column and states that the column is wanted. Handed null it
 *  wants nothing, so the pane goes back to one column and the list takes the whole width again.
 *
 *  A view drawn outside any pane — a panel mounted on its own — has no column to reach, and there
 *  the record stands where it was returned. Under a host it waits for that host's own element
 *  instead of standing inline for a frame first: an element that moves into a portal is a different
 *  child in that position, so React would tear the record down and build it again, firing every
 *  read inside it twice. A record the column stops showing is hidden rather than torn down: it
 *  holds what it had read and comes back without a second fetch, and the record that displaced it
 *  can hand focus back to the act that opened it instead of to a panel rebuilding itself.
 *
 *  `onDisplaced` is how a record that can be shut learns something else has taken the column. A
 *  create form must take it: the act that raised the form is still on the list beside the record
 *  that displaced it, and a form left holding state nothing shows makes that act dead — pressing it
 *  again sets a flag that is already set, so nothing rises and no form appears. A record the route
 *  put there passes none and simply waits, because it has nothing to shut and comes back when
 *  whatever covered it goes. */
export function useBeside(record: ReactNode | null, onDisplaced?: () => void): ReactNode {
  const { hosted, host, open, close, stack } = useContext(BesideContext);
  const id = useId();
  const on = record !== null;
  useEffect(() => {
    if (!on) return;
    open(id);
    return () => close(id);
  }, [on, id, open, close]);
  const displaced = on && stack.includes(id) && stack[stack.length - 1] !== id;
  const leave = useRef(onDisplaced);
  leave.current = onDisplaced;
  useEffect(() => {
    if (displaced) leave.current?.();
  }, [displaced]);
  if (!on) return null;
  if (!hosted) return record;
  if (!host) return null;
  return createPortal(
    <div className={displaced ? undefined : "contents"} hidden={displaced}>
      {record}
    </div>,
    host,
  );
}
