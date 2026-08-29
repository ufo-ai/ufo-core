import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { expect, test, vi } from "vitest";

import {
  LANE_NEXT,
  LANE_PRIOR,
  SlotTrack,
  appended,
  beside,
  closed,
  opened,
  useSlot,
  type Seek,
  type SlotKind,
} from "@/kernel/slots";
import { Banded } from "@/kernel/pane";
import { soundMoved, soundOpened } from "@/lib/sound";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

import { atPhoneWidth } from "./harness";

vi.mock("@/lib/sound", () => ({ soundOpened: vi.fn(), soundMoved: vi.fn() }));

/** Motion's frame loop needs a browser to run in, so the spring lands at once here and every arm
 *  of it is recorded: what the row does is where each lane comes to rest, and which lanes were
 *  moved at all. */
const glided = vi.hoisted(() => ({ moves: [] as number[], still: false }));

vi.mock("motion/react", async (whole) => {
  const real = await whole<typeof import("motion/react")>();
  return {
    ...real,
    useReducedMotion: () => glided.still,
    animate: (value: { jump: (to: number) => void }, to: number) => {
      glided.moves.push(to);
      value.jump(to);
      return { stop: () => {} };
    },
  };
});

const heard = () => {
  vi.mocked(soundOpened).mockClear();
  vi.mocked(soundMoved).mockClear();
};

function Opener({ name, kind = "reading" }: { name: string; kind?: SlotKind }) {
  const [open, setOpen] = useState(false);
  const slot = useSlot(open ? <p>{name} body</p> : null, {
    id: name,
    kind,
    title: name,
    onClose: () => setOpen(false),
  });
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open {name}
      </button>
      {slot}
    </>
  );
}

function Body({ name, mounts }: { name: string; mounts: string[] }) {
  useEffect(() => {
    mounts.push("mount:" + name);
    return () => void mounts.push("unmount:" + name);
  }, [name, mounts]);
  return <p>{name} body</p>;
}

function Held({
  name,
  mounts,
  kind = "reading",
}: {
  name: string;
  mounts: string[];
  kind?: SlotKind;
}) {
  return useSlot(<Body name={name} mounts={mounts} />, { id: name, kind, title: name });
}

function Standing({
  name,
  kind,
  crumb,
  onClose,
  children,
}: {
  name: string;
  kind: SlotKind;
  crumb?: Crumb;
  onClose?: () => void;
  children: ReactNode;
}) {
  return useSlot(children, { id: name, kind, title: name, crumb, onClose });
}

/** A screen with a place: the address holds the track, every act writes it, and the lanes are drawn
 *  from it. A lane is opened beside the ones already standing, and each lane holds a link to every
 *  other, which is the press that walks the path. `opening` is the row the screen stands whole on
 *  its first render, which is a member arriving on an address that already names lanes. */
function Walk({ names, opening = [] }: { names: string[]; opening?: string[] }) {
  const [opens, setOpens] = useState<string[]>(opening);
  return (
    <SlotTrack opens={opens} onMove={setOpens}>
      <p data-testid="address">{opens.join(" ")}</p>
      {names.map((name) => (
        <button key={name} type="button" onClick={() => setOpens(appended(opens, name))}>
          Open {name}
        </button>
      ))}
      {names.map((name) =>
        opens.includes(name) ? (
          <Walked key={name} name={name} names={names} opens={opens} onPlace={setOpens} />
        ) : null,
      )}
    </SlotTrack>
  );
}

function Walked({
  name,
  names,
  opens,
  onPlace,
}: {
  name: string;
  names: string[];
  opens: string[];
  onPlace: (opens: string[]) => void;
}) {
  return useSlot(
    <>
      {names
        .filter((other) => other !== name)
        .map((other) => (
          <button
            key={other}
            type="button"
            onClick={() => onPlace(opened(opens, other, name))}
          >
            {name} to {other}
          </button>
        ))}
    </>,
    { id: name, kind: "panel", title: name, onClose: () => onPlace(closed(opens, name)) },
  );
}

function Describing() {
  const [open, setOpen] = useState(false);
  const slot = useSlot(
    open ? <p id="what-adding-does">Each address is added to this workspace.</p> : null,
    {
      id: "Add members",
      kind: "panel",
      title: "Add members",
      describes: "what-adding-does",
      onClose: () => setOpen(false),
    },
  );
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open Add members
      </button>
      {slot}
    </>
  );
}

const open = (name: string) =>
  userEvent.click(screen.getByRole("button", { name: "Open " + name }));

const address = () =>
  (screen.getByTestId("address").textContent ?? "").split(" ").filter(Boolean);

const standing = () => screen.queryAllByRole("region").map((slot) => slot.getAttribute("aria-label"));

const slotFor = (name: string) => screen.getByRole("region", { name });

const rule = (name: string) => slotFor(name).className.split(" ");

const track = () => document.querySelector("[data-slot=slot-track]") as HTMLElement;

const TOKENS = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

function token(name: string): number {
  const declared = new RegExp("^\\s*" + name + ":\\s*(\\d+)px;", "m").exec(TOKENS);
  if (!declared) throw new Error("theme.css declares no " + name);
  return Number(declared[1]);
}

/** The track at the width the comps are drawn at: 1440 less the page's padding, the rail, and the
 *  hairlines either side of it. */
const TRACK_WIDTH = 1375;

test("a press in the pane's own list leaves the target standing alone", () => {
  expect(opened(["one", "two", "three"], "four")).toEqual(["four"]);
  expect(opened([], "one")).toEqual(["one"]);
});

test("a press inside a lane shuts the lanes after it and stands the target at its right", () => {
  expect(opened(["one", "two", "three"], "four", "one")).toEqual(["one", "four"]);
  expect(opened(["one", "two", "three"], "four", "two")).toEqual(["one", "two", "four"]);
  expect(opened(["one", "two", "three"], "four", "three")).toEqual([
    "one",
    "two",
    "three",
    "four",
  ]);
});

/** The views place what comes back, so a fresh array of the same ids is a press that changed
 *  nothing pushing a history entry and remounting the record being read. Identity is the answer,
 *  not equal contents. */
test("re-pressing the lane that already stands hands back the very track it was given", () => {
  const track = ["one", "two", "three"];
  expect(opened(track, "two", "one")).toBe(track);
  expect(opened(track, "one")).toBe(track);
  expect(appended(track, "two")).toBe(track);
  expect(closed(track, "four")).toBe(track);
});

/** The track is keyed by id, so the same id twice is two hosts fighting over one record. */
test("a lane reached again from further down the path moves rather than doubling", () => {
  expect(opened(["one", "two", "three"], "one", "three")).toEqual(["two", "three", "one"]);
});

/** A `from` naming a lane that has already been shut raised nothing, so it is the root. */
test("a press from a lane that no longer stands is a press in the root list", () => {
  expect(opened(["one", "two"], "three", "gone")).toEqual(["three"]);
});

test("a deliberate open-beside appends and shuts nothing", () => {
  expect(appended(["one", "two"], "three")).toEqual(["one", "two", "three"]);
  expect(appended([], "one")).toEqual(["one"]);
});

test("closing a lane closes every lane opened from it", () => {
  expect(closed(["one", "two", "three"], "two")).toEqual(["one"]);
  expect(closed(["one", "two", "three"], "one")).toEqual([]);
  expect(closed(["one", "two", "three"], "three")).toEqual(["one", "two"]);
});

/** The gesture the browser already gave the member for a second tab, and nothing else: a plain
 *  press walks the path. */
test("command, control and the middle button ask for a lane beside", () => {
  expect(beside({ metaKey: true, ctrlKey: false, button: 0 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: true, button: 0 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: false, button: 1 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: false, button: 0 })).toBe(false);
  expect(beside({ metaKey: false, ctrlKey: false })).toBe(false);
});

/** Two records reached from one list are two open things, not a column one of them has to win. */
test("two records opened from one list stand side by side, and neither is hidden", async () => {
  render(
    <SlotTrack>
      <Opener name="First" />
      <Opener name="Second" />
    </SlotTrack>,
  );

  await open("First");
  await open("Second");

  expect(standing()).toEqual(["First", "Second"]);
  for (const slot of screen.getAllByRole("region")) {
    expect(slot.hasAttribute("hidden")).toBe(false);
    expect(slot.className).not.toContain("hidden");
  }
  expect(screen.getByText("First body")).toBeTruthy();
  expect(screen.getByText("Second body")).toBeTruthy();
});

test("one width rule to a kind, and the reading slot is the one that gives", async () => {
  render(
    <SlotTrack>
      <Opener name="Threads" kind="index" />
      <Opener name="Run" kind="reading" />
      <Opener name="Detail" kind="panel" />
    </SlotTrack>,
  );

  for (const name of ["Threads", "Run", "Detail"]) await open(name);

  // A list is read down, not across: it holds the measure the sheet names it at and neither takes
  // the width the reading lane gives up nor gives up its own.
  expect(rule("Threads")).toEqual(
    expect.arrayContaining(["grow-0", "shrink-0", "basis-(--container-index)"]),
  );
  expect(token("--container-index")).toBeLessThan(token("--size-slot-min"));
  expect(rule("Run")).toEqual(
    expect.arrayContaining(["grow", "shrink", "basis-0", "min-w-(--size-slot-min)"]),
  );
  expect(rule("Detail")).toEqual(
    expect.arrayContaining([
      "grow-0",
      "shrink",
      "basis-(--container-record)",
      "min-w-(--size-slot-min)",
    ]),
  );
});

/** `display: contents` promotes whatever the view rendered to be the flex item, so the pane's own
 *  body is in the track on the same terms as the slots and needs the same rule — from one place,
 *  not a class each screen remembers to add. */
test("the body the slots stand beside takes the reading slot's own width rule", async () => {
  render(
    <SlotTrack>
      <div data-testid="list">
        <p>a row</p>
        <Opener name="Run" kind="reading" />
        <Opener name="Detail" kind="panel" />
      </div>
    </SlotTrack>,
  );

  const body = () => screen.getByTestId("list").parentElement as HTMLElement;
  expect(body().className).toBe("contents");

  await open("Run");
  await open("Detail");

  const flexes = (node: HTMLElement) =>
    node.className.split(" ").filter((name) => /^(grow|shrink|basis-|min-w-)/.test(name));
  expect(flexes(body())).toEqual(flexes(slotFor("Run")));
  expect(flexes(body())).toEqual(["grow", "shrink", "basis-0", "min-w-(--size-slot-min)"]);
  expect(body().className).toContain("max-narrow:snap-start");
});

test("closing a slot gives its width to the slots that remain", async () => {
  render(
    <SlotTrack>
      <Opener name="Run" kind="reading" />
      <Opener name="Detail" kind="panel" />
    </SlotTrack>,
  );

  await open("Run");
  await open("Detail");
  expect(standing()).toEqual(["Run", "Detail"]);

  await userEvent.click(within(slotFor("Detail")).getByRole("button", { name: "Close Detail" }));

  expect(standing()).toEqual(["Run"]);
  expect(rule("Run")).toEqual(expect.arrayContaining(["grow", "basis-0"]));
  expect(rule("Run").some((name) => name.startsWith("basis-(--"))).toBe(false);
});

/** A record that reached its slot through a portal must not have stood inline first: an element
 *  that moves position is torn down and built again, firing every read inside it twice. And nothing
 *  in the track is displaced, so a slot opened beside a record leaves that record alone. */
test("a record mounts once on its way into the track, and a slot beside it never rebuilds it", async () => {
  const mounts: string[] = [];
  render(
    <SlotTrack>
      <Held name="assistant" mounts={mounts} />
      <Opener name="New task" kind="panel" />
    </SlotTrack>,
  );

  expect(mounts).toEqual(["mount:assistant"]);

  await open("New task");

  expect(standing()).toEqual(["assistant", "New task"]);
  expect(mounts).toEqual(["mount:assistant"]);
});

/** The lane takes focus as it lands, so what a member who cannot see it is told is the whole of
 *  what the lane says about itself. A panel whose one line states what it will do with everything
 *  typed into it says that line to the member standing in it, or says it to nobody. A lane that
 *  states nothing points at nothing: an empty description is a promise the reader steps over. */
test("a lane says what it does to the member focus lands on", async () => {
  render(
    <SlotTrack>
      <Describing />
      <Opener name="Detail" kind="panel" />
    </SlotTrack>,
  );

  await open("Add members");

  const panel = slotFor("Add members");
  expect(panel.contains(document.activeElement)).toBe(true);
  expect(panel.getAttribute("aria-describedby")).toBe("what-adding-does");
  expect(document.getElementById("what-adding-does")?.textContent).toBe(
    "Each address is added to this workspace.",
  );

  await open("Detail");

  expect(slotFor("Detail").hasAttribute("aria-describedby")).toBe(false);
});

/** A lane is headed by what it is called. The kinds that stand for a body of something — an index,
 *  a reading — wear a glyph that says which, because a track holding several says what each one is
 *  before it says what is in it. A panel stands for nothing but itself, so its title is the whole
 *  of its head and a glyph beside it would only restate the word. */
test("a panel lane is headed by its title alone, where the other kinds wear a glyph", async () => {
  render(
    <SlotTrack>
      <Opener name="Detail" kind="panel" />
      <Opener name="Index" kind="index" />
      <Opener name="Reading" kind="reading" />
    </SlotTrack>,
  );

  await open("Detail");
  await open("Index");
  await open("Reading");

  const glyphs = (name: string) =>
    within(slotFor(name)).getByRole("heading").parentElement!.querySelectorAll("svg").length;

  expect(glyphs("Index")).toBeGreaterThan(0);
  expect(glyphs("Reading")).toBeGreaterThan(0);
  expect(glyphs("Detail")).toBe(0);
});

/** The lane lands on itself so a member who cannot see it is told what it is. Content that opens on
 *  a field the member is meant to type in has already answered that question, and taking the lane's
 *  own focus after it would drop the cursor out of the words they came to write. */
function Composer() {
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => box.current?.focus(), []);
  return <textarea ref={box} aria-label="Message" />;
}

function ComposingPane() {
  const [standing, setStanding] = useState(true);
  return useSlot(standing ? <Composer /> : null, {
    id: "Compose",
    kind: "panel",
    title: "Compose",
    onClose: () => setStanding(false),
  });
}

test("a lane leaves focus where its content has already put it", async () => {
  render(
    <SlotTrack>
      <ComposingPane />
    </SlotTrack>,
  );

  expect(document.activeElement).toBe(screen.getByLabelText("Message"));
});

/** Escape in a field the lane holds is the way out of the field, and that is the whole of what it
 *  does in a lane. A row is several open things at once, and a key that shut one of them would spend
 *  a member's whole row on a mistyped press — the way out of a lane is the close control on its
 *  band. Out of the field, the bracket keys walk the row again. */
test("Escape steps out of a lane's field, and shuts nothing", async () => {
  render(
    <SlotTrack>
      <ComposingPane />
      <Standing name="Beside" kind="panel">
        <p>beside body</p>
      </Standing>
    </SlotTrack>,
  );

  const box = screen.getByLabelText("Message");
  expect(document.activeElement).toBe(box);

  await userEvent.keyboard("{Escape}");

  expect(document.activeElement).toBe(slotFor("Compose"));
  expect(screen.getByLabelText("Message")).toBeTruthy();

  press(LANE_NEXT, {}, slotFor("Compose"));

  expect(document.activeElement).toBe(slotFor("Beside"));

  slotFor("Compose").focus();
  await userEvent.keyboard("{Escape}");

  expect(screen.getByLabelText("Message")).toBeTruthy();
  expect(standing()).toEqual(["Compose", "Beside"]);
});

/** The way out of a lane is the close control on its band, and focus goes back to the act that
 *  raised the lane rather than being left on a control that no longer stands. */
test("the band's close control leaves a slot, and focus goes back where it came from", async () => {
  render(
    <SlotTrack>
      <Opener name="New thing" />
    </SlotTrack>,
  );

  const act = screen.getByRole("button", { name: "Open New thing" });
  await userEvent.click(act);
  expect(slotFor("New thing").contains(document.activeElement)).toBe(true);

  await userEvent.click(
    within(slotFor("New thing")).getByRole("button", { name: "Close New thing" }),
  );

  expect(screen.queryByRole("region")).toBeNull();
  expect(document.activeElement).toBe(act);
});

/** A screen whose every column is a lane hands the track its whole box. The row lies over that box —
 *  edge to edge, above what is under it, with its own hairline either side — rather than standing
 *  beside a body: a reading lane in all but name would take an equal share of the width, so one
 *  open lane would hold half the screen instead of the whole of it. What the track was handed still
 *  stands underneath, because that is what registers the lanes; it is not one of them. */
test("a track handed the whole box lies over it, and holds only its own lanes", async () => {
  render(
    <SlotTrack>
      <p>the list</p>
      <Standing name="assistant" kind="panel">
        <SlotTrack over>
          <Opener name="New task" kind="panel" />
        </SlotTrack>
      </Standing>
    </SlotTrack>,
  );

  await open("New task");

  expect(standing()).toEqual(["assistant", "New task"]);
  expect(screen.getByText("New task body")).toBeTruthy();
  expect(screen.getByText("the list")).toBeTruthy();

  const cover = slotFor("assistant").querySelector("[data-slot=slot-track]");
  if (!cover) throw new Error("the lane holds no track for what stands in it");
  expect(cover.className).toContain("absolute");
  expect(cover.className).toContain("inset-0");
  expect(cover.className).toContain("z-10");
  expect(cover.className).toContain("border-x");
  expect(cover.className).toContain("border-edge");

  const band = slotFor("New task").querySelector("[data-slot=header]");
  if (!band) throw new Error("the lane draws no band");
  expect(band.getAttribute("draggable")).toBeNull();

  const opener = screen.getByRole("button", { name: "Open New task" });
  expect(cover.contains(opener)).toBe(false);
  expect(slotFor("New task").closest("[data-slot=slot-track]")).toBe(cover);
});

/** A resize observer the test drives by hand. The track reads nothing off the report — it measures
 *  the row itself — so a wake-up carrying no records is the whole of what it needs. */
class Sizing {
  static made: Sizing[] = [];
  seen: Element[] = [];
  constructor(readonly callback: ResizeObserverCallback) {
    Sizing.made.push(this);
  }
  observe(target: Element) {
    this.seen.push(target);
  }
  unobserve(target: Element) {
    this.seen = this.seen.filter((held) => held !== target);
  }
  disconnect() {
    this.seen = [];
  }
  report() {
    act(() => this.callback([], this as unknown as ResizeObserver));
  }
}

function Spread({ names }: { names: string[] }) {
  return (
    <SlotTrack over>
      {names.map((name) => (
        <Standing key={name} name={name} kind="panel">
          <p>{name} body</p>
        </Standing>
      ))}
    </SlotTrack>
  );
}

/** The width the rail leaves on the laptop `--size-slot-min` is set against. */
const PANE_WIDTH = 1240;

/** A track handed the whole pane fills it. The lanes on screen divide the row exactly — as many as
 *  the floor leaves room for — and the rest stand off the right edge at that same width, so a row of
 *  nine on this pane is four across and five waiting, none of them a lane cut in half at the edge.
 *  The row snaps to those boundaries at every width, not only where a lane is the whole screen. */
test("a track handed the whole pane divides its width between the lanes that fit", () => {
  Sizing.made = [];
  vi.stubGlobal("ResizeObserver", Sizing);
  const boxes = laidOut([0, PANE_WIDTH], {});
  try {
    render(<Spread names={["A", "B", "C", "D", "E", "F", "G", "H", "I"]} />);
    const row = track();
    row.style.setProperty("--size-slot-min", token("--size-slot-min") + "px");
    Sizing.made[0].report();

    expect(Sizing.made[0].seen).toEqual([row]);
    expect(Math.floor(PANE_WIDTH / token("--size-slot-min"))).toBe(4);
    expect(row.style.getPropertyValue("--lane-share")).toBe("4");
    expect(row.className.split(" ")).toContain("overflow-hidden");
    expect(row.className.split(" ")).not.toContain("snap-x");
    expect(row.className.split(" ")).not.toContain("overflow-x-auto");
    for (const slot of screen.getAllByRole("region")) {
      expect(slot.className.split(" ")).toContain("lane-share");
    }
  } finally {
    boxes.mockRestore();
  }
});

/** Fewer lanes than fit still divide the whole row: three lanes take a third of the pane each,
 *  rather than a lane's worth apiece with the rest of the pane left empty beside them. */
test("a row with room to spare divides its width between the lanes it holds", () => {
  Sizing.made = [];
  vi.stubGlobal("ResizeObserver", Sizing);
  const boxes = laidOut([0, PANE_WIDTH], {});
  try {
    render(<Spread names={["A", "B", "C"]} />);
    const row = track();
    row.style.setProperty("--size-slot-min", token("--size-slot-min") + "px");
    Sizing.made[0].report();

    expect(row.style.getPropertyValue("--lane-share")).toBe("3");
  } finally {
    boxes.mockRestore();
  }
});

test("a row nothing can measure gives every lane an equal share", () => {
  vi.stubGlobal("ResizeObserver", undefined);
  render(<Spread names={["A", "B", "C", "D", "E"]} />);

  expect(standing()).toEqual(["A", "B", "C", "D", "E"]);
  expect(track().style.getPropertyValue("--lane-share")).toBe("5");
});

/** A track standing beside a body is not the pane, so its lanes are sized by what they hold and page
 *  one to a screen at the narrow width, the way they always have. */
test("a track standing beside a body sizes its lanes by kind and shares nothing", async () => {
  render(
    <SlotTrack>
      <Opener name="Threads" kind="index" />
      <Opener name="Run" kind="reading" />
    </SlotTrack>,
  );

  await open("Threads");
  await open("Run");

  expect(rule("Threads")).toEqual(expect.arrayContaining(["basis-(--container-index)"]));
  expect(rule("Run")).toEqual(expect.arrayContaining(["grow", "shrink", "basis-0"]));
  for (const slot of screen.getAllByRole("region")) {
    expect(slot.className.split(" ")).not.toContain("lane-share");
    expect(slot.className.split(" ")).toContain("max-narrow:basis-full");
  }
  expect(track().className.split(" ")).not.toContain("snap-x");
  expect(track().className.split(" ")).toContain("max-narrow:snap-x");
  expect(track().style.getPropertyValue("--lane-share")).toBe("");
});

/** A lane that arrived by a press is the one the member is looking for: it scrolls into view and
 *  lands on itself. A screen mounting a row whole is restating lanes the member already arranged,
 *  and a track that scrolled and focused each of those in turn would open paged to the lane at the
 *  far end rather than the first. */
test("a row stood whole scrolls to none of its lanes, where a lane joining it lands", async () => {
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    render(<Walk names={["A", "B", "C", "D"]} opening={["A", "B", "C"]} />);

    expect(standing()).toEqual(["A", "B", "C"]);
    expect(scrolled).toEqual([]);
    expect(document.activeElement).toBe(document.body);

    await open("D");

    expect(standing()).toEqual(["A", "B", "C", "D"]);
    expect(scrolled).toEqual(["D"]);
    expect(slotFor("D").contains(document.activeElement)).toBe(true);
  } finally {
    scrolls.mockRestore();
  }
});

/** The track is a row of open things, not a path: a link followed from a slot opens beside that
 *  slot rather than at the far end, where the member would have to scroll to find it. */
test("a link followed inside a slot opens the next one immediately to its right", async () => {
  render(
    <SlotTrack>
      <Standing name="Records" kind="panel">
        <Opener name="Run" />
      </Standing>
      <Opener name="Detail" kind="panel" />
    </SlotTrack>,
  );

  await open("Detail");
  expect(standing()).toEqual(["Records", "Detail"]);

  await open("Run");

  expect(standing()).toEqual(["Records", "Run", "Detail"]);
});

/** One thing owns the order. The address is the one: it is what a link states, what the store holds,
 *  what a crumb reads as the lane to its left, and what shutting a lane cuts from. So a lane the
 *  address moved moves on the screen without being placed again — the lane's own props did not
 *  change, and a track that waited for them would draw a row the address no longer holds. */
test("a lane the address moved stands where the address puts it", async () => {
  render(<Walk names={["A", "B", "C", "D"]} />);

  await open("A");
  await open("B");
  await open("C");
  expect(standing()).toEqual(["A", "B", "C"]);
  expect(address()).toEqual(standing());

  await userEvent.click(screen.getByRole("button", { name: "C to A" }));

  expect(address()).toEqual(["B", "C", "A"]);
  expect(standing()).toEqual(address());

  await userEvent.click(screen.getByRole("button", { name: "B to D" }));

  expect(address()).toEqual(["B", "D"]);
  expect(standing()).toEqual(address());
});

/** A lane carried by hand is a lane the member put there, so it is put there in the one place a
 *  lane's place is kept. A move the address never hears is a row that reads one way and closes,
 *  crumbs and reloads another. What the member takes hold of is the band already reading as this
 *  lane's name, rather than a grip drawn beside it that names nothing. */
test("a lane dragged by its band is moved in the address", async () => {
  render(<Walk names={["A", "B"]} />);

  await open("A");
  await open("B");
  expect(standing()).toEqual(["A", "B"]);

  const carried = new Map<string, string>();
  const dataTransfer = {
    setData: (kind: string, value: string) => void carried.set(kind, value),
    getData: (kind: string) => carried.get(kind) ?? "",
  };
  const band = slotFor("B").querySelector("[data-slot=header]");
  if (!band) throw new Error("the lane draws no band to drag it by");
  expect(band.getAttribute("draggable")).toBe("true");

  fireEvent.dragStart(band, { dataTransfer });
  fireEvent.dragOver(slotFor("A"), { dataTransfer });
  fireEvent.drop(slotFor("A"), { dataTransfer });

  expect(address()).toEqual(["B", "A"]);
  expect(standing()).toEqual(address());
});

/** Nothing is evicted: past the floor the track runs off the pane's edge and scrolls, rather than
 *  dividing the width into columns none of the open things can be read in. */
test("past its floor the track scrolls rather than crushing a slot under it", async () => {
  const names = ["One", "Two", "Three", "Four", "Five"];
  render(
    <SlotTrack>
      {names.map((name) => (
        <Opener key={name} name={name} />
      ))}
    </SlotTrack>,
  );

  for (const name of names) await open(name);

  expect(standing()).toEqual(names);
  for (const slot of screen.getAllByRole("region")) {
    expect(slot.className).toContain("min-w-(--size-slot-min)");
  }
  expect(track().className).toContain("overflow-x-auto");
  expect(names.length * token("--size-slot-min")).toBeGreaterThan(TRACK_WIDTH);
});

test("the hairline between two slots is the track's own background through the gap", async () => {
  render(
    <SlotTrack>
      <Opener name="First" />
      <Opener name="Second" />
    </SlotTrack>,
  );

  await open("First");
  await open("Second");

  expect(track().className).toContain("gap-px");
  expect(track().className).toContain("bg-edge");
  for (const slot of screen.getAllByRole("region")) {
    expect(slot.className).toContain("bg-surface");
    expect(slot.className).not.toContain("border");
  }
});

test("a lane's band is ruled off the body that scrolls under it, inside the band's own height", async () => {
  render(
    <SlotTrack>
      <Opener name="First" />
    </SlotTrack>,
  );

  await open("First");

  const slot = screen.getByRole("region", { name: "First" });
  expect(slot.querySelector("[data-slot=header]")?.className).toContain("shadow-rule");
  expect(slot.className).not.toContain("border");
});

test("a narrow viewport pages one slot to a screen", async () => {
  render(
    <SlotTrack>
      <Opener name="First" />
      <Opener name="Second" />
    </SlotTrack>,
  );

  await open("First");
  await open("Second");

  expect(track().className).toContain("max-narrow:snap-x");
  expect(track().className).toContain("max-narrow:snap-mandatory");
  for (const slot of screen.getAllByRole("region")) {
    expect(rule(slot.getAttribute("aria-label") ?? "")).toEqual(
      expect.arrayContaining(["max-narrow:basis-full", "max-narrow:snap-start"]),
    );
  }
});

/** A lane wears the portal's one header, so its name, the kind of thing it holds and the way out of
 *  it stand where a page's and a record's do. The band draws no rule of its own — the track draws
 *  the hairline between one lane and the next, and a line under every band would cross it. */
test("a slot states its name, the kind of thing it holds, and how to shut it", async () => {
  render(
    <SlotTrack>
      <Standing name="Records" kind="panel">
        <p>the rows</p>
      </Standing>
      <Opener name="Detail" kind="panel" />
    </SlotTrack>,
  );

  await open("Detail");

  const header = slotFor("Detail").querySelector<HTMLElement>("[data-slot=header]");
  if (!header) throw new Error("the slot draws no header");
  expect(header.firstElementChild!.className).toContain("h-(--size-control)");
  expect(header.className).not.toContain("border-b");
  expect(within(header).getByRole("heading", { level: 2 }).textContent).toBe("Detail");
  expect(within(header).getByRole("button", { name: "Close Detail" })).toBeTruthy();
  expect(within(slotFor("Records")).queryByRole("button", { name: /^Close/ })).toBeNull();
});

/** A page standing under a band of its own draws its headers as that band's acts. A lane it opens is
 *  not one of them: the lane is a surface the band outside never named, so it wears the portal's
 *  header whole — the name, the kind, and the way out — and stands no row of acts in its place. */
test("a lane under a band draws that band whole", async () => {
  render(
    <Banded value>
      <SlotTrack>
        <Standing name="Records" kind="panel">
          <p>the rows</p>
        </Standing>
        <Opener name="Detail" kind="panel" />
      </SlotTrack>
    </Banded>,
  );

  await open("Detail");

  const lane = slotFor("Detail");
  const header = lane.querySelector<HTMLElement>("[data-slot=header]");
  if (!header) throw new Error("the lane draws no header");
  expect(within(header).getByRole("heading", { level: 2 }).textContent).toBe("Detail");
  expect(within(header).getByRole("button", { name: "Close Detail" })).toBeTruthy();
  expect(lane.querySelector("[data-slot=page-acts]")).toBeNull();
});

/** A lane holding a record reached from a list is "Radar / morning-digest", and the crumb is what a
 *  lane paged one to a screen has instead of the lane that would otherwise stand to its left. It
 *  states where the record came from and shuts nothing: a lane on the screen is no address, and the
 *  way out of the lane is its own verb. At the floor the track shrinks a lane to there is room for
 *  exactly that band: the measure the name is guaranteed, and the way out beside it. */
test("a lane names the surface it was reached from, and that band fits the lane's floor", () => {
  render(
    <SlotTrack>
      <Standing name="morning-digest" kind="panel" crumb={{ label: "Radar" }}>
        <p>the story</p>
      </Standing>
    </SlotTrack>,
  );

  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  expect(within(path).getByText("morning-digest").getAttribute("aria-current")).toBe("page");
  expect(path.parentElement!.className).toContain("min-w-0");
  expect(path.parentElement!.className).not.toContain("min-w-(--container-title)");
  expect(path.textContent).toBe("Radar/morning-digest");
  expect(within(path).queryByRole("link")).toBeNull();

  expect(
    token("--container-title") +
      token("--spacing-md") +
      token("--size-control") +
      2 * token("--spacing-2xl"),
  ).toBeLessThanOrEqual(token("--size-slot-min"));
});

/** Both verbs that stand a lane grow the track — `appended` always, and `opened` whenever the lane
 *  it was pressed from is the one at the far end — so both stop at the same cap. What comes back is
 *  the very array they were handed, which is what every other press that changes nothing hands
 *  back, so the screen writes no address and records no step for it. */
test("neither verb stands a lane past the cap", () => {
  const full = Array.from({ length: TRACK_MAX_SLOTS }, (_, at) => "object/report/" + at);
  const room = full.slice(0, -1);

  expect(appended(full, "object/report/late")).toBe(full);
  expect(opened(full, "object/report/late", full.at(-1))).toBe(full);

  expect(appended(room, "object/report/late")).toEqual([...room, "object/report/late"]);
  expect(opened(full, "object/report/late", full[0])).toEqual([full[0], "object/report/late"]);
  expect(opened(full, "object/report/late")).toEqual(["object/report/late"]);
});

/** An observer the test drives by hand: what it has been asked to watch, and a report it delivers
 *  about which of those stand in view. */
class Watching {
  static made: Watching[] = [];
  seen: Element[] = [];
  constructor(
    readonly callback: IntersectionObserverCallback,
    readonly options: IntersectionObserverInit,
  ) {
    Watching.made.push(this);
  }
  observe(target: Element) {
    this.seen.push(target);
  }
  unobserve(target: Element) {
    this.seen = this.seen.filter((held) => held !== target);
  }
  disconnect() {
    this.seen = [];
  }
  takeRecords() {
    return [];
  }
  report(near: Element[]) {
    act(() =>
      this.callback(
        this.seen.map(
          (target) => ({ target, isIntersecting: near.includes(target) }) as IntersectionObserverEntry,
        ),
        this as unknown as IntersectionObserver,
      ),
    );
  }
}

/** A lane holds its body only near the scrollport. Its width and its band stand whatever the
 *  observer says, so the row's scroll width, its handles and the address hold still; the body is
 *  drawn once the lane comes within a scrollport of view and torn down again when it leaves. One
 *  observer per track, rooted on the scrolling row with a scrollport of margin either side. */
test("a lane past a scrollport of the view keeps its width and band, and holds no body", () => {
  Watching.made = [];
  vi.stubGlobal("IntersectionObserver", Watching);
  const mounts: string[] = [];
  render(
    <SlotTrack>
      <Held name="One" mounts={mounts} />
      <Held name="Two" mounts={mounts} />
      <Held name="Three" mounts={mounts} />
    </SlotTrack>,
  );

  expect(standing()).toEqual(["One", "Two", "Three"]);
  expect(Watching.made).toHaveLength(1);
  const [watching] = Watching.made;
  expect(watching.options.root).toBe(track());
  expect(watching.options.rootMargin).toBe("0px 100%");
  expect(watching.seen).toEqual([slotFor("One"), slotFor("Two"), slotFor("Three")]);
  expect(mounts).toEqual(["mount:One", "mount:Two", "mount:Three"]);

  watching.report([slotFor("One"), slotFor("Two")]);

  expect(mounts.at(-1)).toBe("unmount:Three");
  expect(screen.queryByText("Three body")).toBeNull();
  const three = slotFor("Three");
  expect(within(three).getByRole("heading", { level: 2 }).textContent).toBe("Three");
  expect(rule("Three")).toEqual(
    expect.arrayContaining(["grow", "shrink", "basis-0", "min-w-(--size-slot-min)"]),
  );
  expect(three.lastElementChild!.className).toContain("bg-surface");

  watching.report([slotFor("Two"), slotFor("Three")]);

  expect(screen.getByText("Three body")).toBeTruthy();
  expect(screen.queryByText("One body")).toBeNull();
  expect(mounts.slice(-2)).toEqual(["unmount:One", "mount:Three"]);
});

/** A row jsdom never laid out has no width, so every lane in it is near and every body is drawn —
 *  which is what a track measured before its first layout answers too. */
test("a row no layout has given a width holds every body", () => {
  Watching.made = [];
  vi.stubGlobal("IntersectionObserver", Watching);
  const mounts: string[] = [];
  render(
    <SlotTrack>
      <Held name="One" mounts={mounts} />
      <Held name="Two" mounts={mounts} />
    </SlotTrack>,
  );

  expect(track().getBoundingClientRect().width).toBe(0);
  expect(mounts).toEqual(["mount:One", "mount:Two"]);
});

/** The boxes a laid-out row and its lanes stand in: the row is the scrollport, a lane stands where
 *  the record puts it, and everything else measures the zeros jsdom hands back. */
function laidOut(port: [number, number], lanes: Record<string, [number, number]>) {
  return vi
    .spyOn(Element.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: Element) {
      const label = this.getAttribute("aria-label");
      const box =
        this.getAttribute("data-slot") === "slot-track" ? port : label ? lanes[label] : undefined;
      const [left, right] = box ?? [0, 0];
      return { left, right, width: right - left } as unknown as DOMRect;
    });
}

/** An observer's first batch arrives a task after the paint, so a track that drew nothing until it
 *  spoke would show a member every lane on their screen empty on the way in. The track measures the
 *  row and its lanes itself as they stand: a lane within a scrollport of the row holds its body in
 *  the frame it stands in, with nothing reported about it. */
test("a lane the track measures beside the row holds its body before the observer speaks", () => {
  Watching.made = [];
  vi.stubGlobal("IntersectionObserver", Watching);
  const boxes = laidOut([0, 1000], { One: [0, 500], Two: [500, 1000] });
  const mounts: string[] = [];
  try {
    render(
      <SlotTrack>
        <Held name="One" mounts={mounts} />
        <Held name="Two" mounts={mounts} />
      </SlotTrack>,
    );

    expect(Watching.made[0].seen).toHaveLength(2);
    expect(mounts).toEqual(["mount:One", "mount:Two"]);
    expect(screen.getByText("Two body")).toBeTruthy();
  } finally {
    boxes.mockRestore();
  }
});

/** The measurement reaches exactly as far as the observer's own margin: one scrollport past either
 *  edge. A lane standing further out is the row of twenty app pages the rule is for, and it waits
 *  for the observer like any other. */
test("a lane a scrollport past the row's edge holds none until the observer says otherwise", () => {
  Watching.made = [];
  vi.stubGlobal("IntersectionObserver", Watching);
  const boxes = laidOut([0, 1000], { Near: [0, 500], Far: [2500, 3000] });
  const mounts: string[] = [];
  try {
    render(
      <SlotTrack>
        <Held name="Near" mounts={mounts} />
        <Held name="Far" mounts={mounts} />
      </SlotTrack>,
    );

    expect(standing()).toEqual(["Near", "Far"]);
    expect(mounts).toEqual(["mount:Near"]);
    expect(screen.queryByText("Far body")).toBeNull();

    Watching.made[0].report([slotFor("Near"), slotFor("Far")]);

    expect(mounts).toEqual(["mount:Near", "mount:Far"]);
    expect(screen.getByText("Far body")).toBeTruthy();
  } finally {
    boxes.mockRestore();
  }
});

test("a track nothing can observe draws every body", () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const mounts: string[] = [];
  render(
    <SlotTrack>
      <Held name="One" mounts={mounts} />
      <Held name="Two" mounts={mounts} />
    </SlotTrack>,
  );

  expect(mounts).toEqual(["mount:One", "mount:Two"]);
  expect(screen.getByText("Two body")).toBeTruthy();
});

/** A screen whose track is sought from outside it — the rail's tile naming a lane. */
function Seeking({
  names,
  opening,
  seeking,
}: {
  names: string[];
  opening: string[];
  seeking?: string;
}) {
  const [opens, setOpens] = useState(opening);
  const [seek, setSeek] = useState<Seek | undefined>(
    seeking === undefined ? undefined : { id: seeking },
  );
  return (
    <SlotTrack opens={opens} onMove={setOpens} seek={seek}>
      {names.map((name) => (
        <button key={name} type="button" onClick={() => setSeek({ id: name })}>
          Seek {name}
        </button>
      ))}
      {opens.map((name) => (
        <Standing
          key={name}
          name={name}
          kind="panel"
          onClose={() => setOpens(closed(opens, name))}
        >
          <p>{name} body</p>
        </Standing>
      ))}
    </SlotTrack>
  );
}

/** A seek scrolls the lane it names into view, and every press is its own ask: the member scrolled
 *  away between two presses on one tile. A lane not standing is sought in vain and scrolls nothing. */
test("a seek scrolls the standing lane it names into view, once per press", async () => {
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    render(<Seeking names={["A", "B", "C"]} opening={["A", "B"]} />);
    expect(standing()).toEqual(["A", "B"]);
    expect(scrolled).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Seek B" }));
    expect(scrolled).toEqual(["B"]);

    await userEvent.click(screen.getByRole("button", { name: "Seek B" }));
    expect(scrolled).toEqual(["B", "B"]);

    await userEvent.click(screen.getByRole("button", { name: "Seek C" }));
    expect(scrolled).toEqual(["B", "B"]);
  } finally {
    scrolls.mockRestore();
  }
});

/** A row stood whole scrolls to none of its lanes — unless one of them was sought as the screen
 *  mounted, which is the rail's tile pressed from another screen: that lane scrolls in as it lands.
 *  A seek is a scroll and nothing else, so the lane it lands takes no focus: the member asked to see
 *  it, and a lane that stood itself in the member's hands would take the keys off whatever they were
 *  doing. A lane arriving by a press does land on itself, because the press was that ask. */
test("a lane sought before it stands scrolls into view as it lands, and takes no focus", () => {
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    render(<Seeking names={["A", "B"]} opening={["A", "B"]} seeking="B" />);

    expect(standing()).toEqual(["A", "B"]);
    expect(scrolled).toEqual(["B"]);
    expect(document.activeElement).toBe(document.body);
    expect(slotFor("B").contains(document.activeElement)).toBe(false);
  } finally {
    scrolls.mockRestore();
  }
});

function Fields({ names }: { names: string[] }) {
  return (
    <SlotTrack>
      {names.map((name) => (
        <Standing key={name} name={name} kind="panel">
          <textarea aria-label={name + " composer"} />
          <button type="button">{name} act</button>
        </Standing>
      ))}
    </SlotTrack>
  );
}

const dimmed = () =>
  screen
    .queryAllByRole("region")
    .filter((slot) => slot.hasAttribute("data-dimmed"))
    .map((slot) => slot.getAttribute("aria-label"));

const lands = (on: HTMLElement) => act(() => on.focus());

/** A row is several open things at once, and the moment a member starts putting words into one of
 *  them the rest are what they are reading past. The lane with the cursor in it stays lit and every
 *  other fades under it; a press into a second lane's field moves the fade rather than adding one.
 *  It is the cursor that is read and not the press, so a lane focused at a button dims nothing, and
 *  Escape out of the field ends it along with the typing. */
test("the lanes beside the one being typed in dim, and clear when the typing ends", async () => {
  render(<Fields names={["A", "B", "C"]} />);

  expect(dimmed()).toEqual([]);

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(dimmed()).toEqual(["B", "C"]);
  expect(slotFor("A").className).not.toContain("opacity-");
  expect(slotFor("B").className).toContain("opacity-(--opacity-dimmed)");

  lands(screen.getByRole("textbox", { name: "B composer" }));
  expect(dimmed()).toEqual(["A", "C"]);

  await userEvent.keyboard("{Escape}");
  expect(document.activeElement).toBe(slotFor("B"));
  expect(dimmed()).toEqual([]);

  lands(screen.getByRole("button", { name: "A act" }));
  expect(dimmed()).toEqual([]);
});

/** A dimmed lane is faded, never taken away: it answers a press like any other, and the press that
 *  lands in its own field is what moves the fade off it. */
test("a dimmed lane is pressed and typed into like any other", async () => {
  render(<Fields names={["A", "B"]} />);

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(dimmed()).toEqual(["B"]);

  await userEvent.click(screen.getByRole("textbox", { name: "B composer" }));
  await userEvent.keyboard("hello");

  expect((screen.getByRole("textbox", { name: "B composer" }) as HTMLTextAreaElement).value).toBe(
    "hello",
  );
  expect(dimmed()).toEqual(["A"]);
});

/** Every place the track takes focus has already scrolled the lane to where it wants it. A browser
 *  scrolling a second time to the element it has just focused fights the row's snap and leaves it
 *  resting between two lanes, which is a screen that opens with a lane cut off at its left edge. */
test("the track takes focus without scrolling, having scrolled the lane itself", () => {
  const landed: (FocusOptions | undefined)[] = [];
  const focuses = vi
    .spyOn(HTMLElement.prototype, "focus")
    .mockImplementation(function (this: HTMLElement, held?: FocusOptions) {
      if (this.tagName === "SECTION") landed.push(held);
    });
  try {
    const { unmount } = render(<Walk names={["A", "B", "C", "D"]} opening={["A", "B", "C"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open D" }));

    expect(landed).toEqual([{ preventScroll: true }]);

    unmount();
    landed.length = 0;
    render(<Row names={["A", "B"]} />);
    press(LANE_NEXT);

    expect(landed).toEqual([{ preventScroll: true }]);
  } finally {
    focuses.mockRestore();
  }
});

/** Two acts on the track are said out loud, because both move the screen under a member whose eyes
 *  are on the pane rather than on their hand: a lane opening, and the row moving under focus. A row
 *  the screen stood whole opened nothing — the member is arriving at a row they already arranged —
 *  so it says nothing at all. */
test("a lane opened by a press is heard, and a row stood whole is not", async () => {
  heard();
  render(<Walk names={["A", "B", "C", "D"]} opening={["A", "B", "C"]} />);

  expect(soundOpened).not.toHaveBeenCalled();
  expect(soundMoved).not.toHaveBeenCalled();

  await open("D");

  expect(soundOpened).toHaveBeenCalledTimes(1);
  expect(soundMoved).not.toHaveBeenCalled();
});

/** A seek and a bracket press both move the row rather than open anything, so both are heard as
 *  that — the lane already standing and the lane that scrolls in as it lands alike. */
test("the row moving is heard, once per press and once per seek", async () => {
  heard();
  render(<Row names={["A", "B", "C"]} />);

  press(LANE_NEXT);
  expect(soundMoved).toHaveBeenCalledTimes(1);

  press(LANE_PRIOR);
  expect(soundMoved).toHaveBeenCalledTimes(2);
  expect(soundOpened).not.toHaveBeenCalled();
});

test("a seek is heard as the row moving, whether the lane stands or lands", async () => {
  heard();
  const { unmount } = render(<Seeking names={["A", "B", "C"]} opening={["A", "B"]} />);

  await userEvent.click(screen.getByRole("button", { name: "Seek B" }));
  expect(soundMoved).toHaveBeenCalledTimes(1);

  unmount();
  heard();
  render(<Seeking names={["A", "B"]} opening={["A", "B"]} seeking="B" />);

  expect(soundMoved).toHaveBeenCalledTimes(1);
  expect(soundOpened).not.toHaveBeenCalled();
});


/** A row stood whole, one lane of it holding a field the member types in. */
function Row({ names, typing }: { names: string[]; typing?: string }) {
  return (
    <SlotTrack>
      {names.map((name) => (
        <Standing key={name} name={name} kind="panel">
          {name === typing ? <textarea aria-label={name + " composer"} /> : <p>{name} body</p>}
        </Standing>
      ))}
    </SlotTrack>
  );
}

const press = (key: string, held: KeyboardEventInit = {}, on: Node = document) =>
  fireEvent.keyDown(on, { key, ...held });

/** `[` and `]` walk the row. From outside it the first press moves the way it was pressed; after
 *  that the lane holding focus is where the walk stands, and both ends wrap. The lane it reaches
 *  takes focus and scrolls to itself, which is the whole of the act. */
test("the bracket keys walk the row, and it wraps at both ends", () => {
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    render(<Row names={["A", "B", "C"]} />);
    expect(standing()).toEqual(["A", "B", "C"]);
    expect(document.activeElement).toBe(document.body);

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("B"));
    expect(scrolled).toEqual(["B"]);

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("C"));

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("A"));

    press(LANE_PRIOR);
    expect(document.activeElement).toBe(slotFor("C"));

    press(LANE_PRIOR);
    expect(document.activeElement).toBe(slotFor("B"));
    expect(scrolled).toEqual(["B", "C", "A", "C", "B"]);
  } finally {
    scrolls.mockRestore();
  }
});

test("a bracket held with a modifier, or any other key, moves no lane", () => {
  render(<Row names={["A", "B"]} />);

  press(LANE_NEXT, { metaKey: true });
  press(LANE_NEXT, { ctrlKey: true });
  press(LANE_NEXT, { altKey: true });
  press(LANE_PRIOR, { metaKey: true });
  press("ArrowDown");
  press("p");

  expect(document.activeElement).toBe(document.body);
});

test("a row of one lane walks nowhere", () => {
  render(<Row names={["A"]} />);

  press(LANE_NEXT);

  expect(document.activeElement).toBe(document.body);
});

test("a press another handler has already taken is left alone", () => {
  render(<Row names={["A", "B"]} />);
  const taken = new KeyboardEvent("keydown", {
    key: LANE_NEXT,
    bubbles: true,
    cancelable: true,
  });
  taken.preventDefault();

  act(() => void document.dispatchEvent(taken));

  expect(document.activeElement).toBe(document.body);
});

/** A bare bracket is a character first. Typed into a composer it is the member's text and the row
 *  neither moves nor takes the press; from the lane itself it is the shortcut, and the press is
 *  taken so nothing else answers it too. */
test("a bracket typed into a field is the member's text, and from the lane it walks the row", () => {
  render(<Row names={["A", "B"]} typing="A" />);
  const composer = screen.getByRole("textbox", { name: "A composer" });
  composer.focus();

  expect(press(LANE_NEXT, {}, composer)).toBe(true);
  expect(document.activeElement).toBe(composer);

  slotFor("A").focus();

  expect(press(LANE_NEXT, {}, slotFor("A"))).toBe(false);
  expect(document.activeElement).toBe(slotFor("B"));
});

test("a press inside an open select is left to the select", () => {
  render(
    <SlotTrack>
      <Standing name="A" kind="panel">
        <div role="listbox">
          <div role="option" tabIndex={-1}>
            Weekly
          </div>
        </div>
      </Standing>
      <Standing name="B" kind="panel">
        <p>B body</p>
      </Standing>
    </SlotTrack>,
  );
  const option = screen.getByRole("option", { name: "Weekly" });
  option.focus();

  press(LANE_NEXT, {}, option);

  expect(document.activeElement).toBe(option);
});

/** Two tracks, one standing inside a lane of the other. The row nearest the focused element walks
 *  and the other holds still, so one press never moves two rows; outside both, the row no other row
 *  encloses is the one that answers. */
test("the row nearest the focused element is the one that walks", () => {
  render(
    <SlotTrack>
      <Standing name="Outer one" kind="panel">
        <p>outer one body</p>
      </Standing>
      <Standing name="Outer two" kind="panel">
        <SlotTrack>
          <Standing name="Inner one" kind="panel">
            <p>inner one body</p>
          </Standing>
          <Standing name="Inner two" kind="panel">
            <p>inner two body</p>
          </Standing>
        </SlotTrack>
      </Standing>
    </SlotTrack>,
  );

  slotFor("Inner one").focus();
  press(LANE_NEXT);
  expect(document.activeElement).toBe(slotFor("Inner two"));

  slotFor("Inner two").blur();
  press(LANE_NEXT);
  expect(document.activeElement).toBe(slotFor("Outer two"));
});

/** A row of nine on a pane four lanes wide, the order it stands in written in the address. */
function Rolled({ opening }: { opening: string[] }) {
  const [opens, setOpens] = useState(opening);
  return (
    <SlotTrack over opens={opens} onMove={setOpens}>
      <button
        type="button"
        onClick={() => setOpens([opens[opens.length - 1], ...opens.slice(0, -1)])}
      >
        Carry the last to the head
      </button>
      <button type="button" onClick={() => setOpens([opens[1], opens[0], ...opens.slice(2)])}>
        Swap the first two
      </button>
      {opens.map((name) => (
        <Standing key={name} name={name} kind="panel">
          <p>{name} body</p>
        </Standing>
      ))}
    </SlotTrack>
  );
}

const NINE = ["A", "B", "C", "D", "E", "F", "G", "H", "I"];

/** The pane the comps are drawn at, four lanes across at `--size-slot-min`, and the step from one
 *  lane's left edge to the next: the share of the row it takes, and the hairline after it. */
const ROW_WIDTH = 1400;
const STEP = (ROW_WIDTH - 3) / 4 + 1;

/** Where a lane stands along the row, in lanes. The row itself never scrolls, so a lane's place is
 *  where the flex row laid it plus the transform the track wrote over it. */
const spotted = (name: string): number => {
  const lanes = screen.getAllByRole("region");
  const at = lanes.findIndex((lane) => lane.getAttribute("aria-label") === name);
  const shift = /translateX\((-?[\d.]+)px\)/.exec(lanes[at].style.transform);
  return Math.round(((at * STEP + (shift ? Number(shift[1]) : 0)) / STEP) * 1000) / 1000;
};

function rolling(names: string[]): () => void {
  Sizing.made = [];
  vi.stubGlobal("ResizeObserver", Sizing);
  const boxes = laidOut([0, ROW_WIDTH], {});
  render(<Rolled opening={names} />);
  const row = track();
  row.style.setProperty("--size-slot-min", token("--size-slot-min") + "px");
  Sizing.made[0].report();
  glided.moves.length = 0;
  return () => boxes.mockRestore();
}

/** A row holding more lanes than fit has no end: the lanes past the far edge are read one row's
 *  length to the left instead, so they stand waiting on the near side rather than queued behind
 *  every other lane. */
test("a row wider than the pane carries its far lanes round to the near side", () => {
  const done = rolling(NINE);
  try {
    expect(spotted("A")).toBe(0);
    expect(spotted("E")).toBe(4);
    expect(spotted("F")).toBe(5);
    expect(spotted("I")).toBe(-1);
    expect(spotted("G")).toBe(-3);
  } finally {
    done();
  }
});

/** `]` and `[` move the row itself, one lane a press, and focus stays where the member left it. */
test("a bracket press rotates the endless row by one lane, and moves no focus", () => {
  const done = rolling(NINE);
  try {
    heard();

    press(LANE_NEXT);

    expect(spotted("B")).toBe(0);
    expect(spotted("A")).toBe(-1);
    expect(spotted("F")).toBe(4);
    expect(document.activeElement).toBe(document.body);
    expect(soundMoved).toHaveBeenCalledTimes(1);

    press(LANE_PRIOR);

    expect(spotted("A")).toBe(0);
    expect(spotted("I")).toBe(-1);
    expect(soundMoved).toHaveBeenCalledTimes(2);
  } finally {
    done();
  }
});

/** A row every lane already fits has nothing to bring round, so the keys move it nowhere. */
test("a row every lane fits rotates nowhere", () => {
  const done = rolling(["A", "B", "C"]);
  try {
    press(LANE_NEXT);

    expect(spotted("A")).toBe(0);
    expect(spotted("B")).toBe(1);
    expect(spotted("C")).toBe(2);
    expect(glided.moves).toEqual([]);
  } finally {
    done();
  }
});

/** The horizontal wheel carries the row under the hand, and the row settles on the nearest lane
 *  boundary once the hand stops — a row left resting between two lanes shows both of them cut. */
test("the horizontal wheel carries the row, and it settles on a lane boundary", () => {
  vi.useFakeTimers();
  const done = rolling(NINE);
  try {
    const rolled = new WheelEvent("wheel", { deltaX: 50, deltaY: 2, cancelable: true, bubbles: true });
    act(() => void track().dispatchEvent(rolled));

    expect(rolled.defaultPrevented).toBe(true);
    expect(spotted("A")).toBeCloseTo(-50 / STEP, 3);

    act(() => vi.advanceTimersByTime(200));

    expect(spotted("A")).toBe(0);
  } finally {
    done();
    vi.useRealTimers();
  }
});

/** A wheel down the page is the page's, whatever it also carries sideways. */
test("a wheel that is mostly vertical is left to the page", () => {
  const done = rolling(NINE);
  try {
    const rolled = new WheelEvent("wheel", { deltaX: 5, deltaY: 80, cancelable: true, bubbles: true });
    act(() => void track().dispatchEvent(rolled));

    expect(rolled.defaultPrevented).toBe(false);
    expect(spotted("A")).toBe(0);
  } finally {
    done();
  }
});

/** A lane holds its body only near the row: one lane of reach past either edge, read off the place
 *  the lane stands in rather than off anything the browser has to observe. */
test("a lane more than a lane past either edge of the endless row holds no body", () => {
  const done = rolling(NINE);
  try {
    for (const name of ["A", "B", "C", "D", "E", "F", "I"]) {
      expect(screen.getByText(name + " body")).toBeTruthy();
    }
    expect(screen.queryByText("G body")).toBeNull();
    expect(screen.queryByText("H body")).toBeNull();
  } finally {
    done();
  }
});

/** The order the address states is what the row stands in, and a lane the address moved slides from
 *  where it stood to where it now stands rather than being redrawn there. A lane that did not move
 *  is not animated at all. */
test("a lane the address moved slides to its new place, and a lane that stayed does not", () => {
  const done = rolling(NINE);
  try {
    fireEvent.click(screen.getByRole("button", { name: "Carry the last to the head" }));

    expect(spotted("I")).toBe(0);
    expect(spotted("A")).toBe(1);
    expect(spotted("H")).toBe(-1);

    glided.moves.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Swap the first two" }));

    expect(glided.moves).toHaveLength(2);
    expect(spotted("A")).toBe(0);
    expect(spotted("I")).toBe(1);
  } finally {
    done();
  }
});

/** A member who asked for stillness gets the row moved, not animated: the same press lands the same
 *  lane, and no spring is ever started. */
test("a reduced-motion press lands the row without a glide", () => {
  glided.still = true;
  const done = rolling(NINE);
  try {
    press(LANE_NEXT);

    expect(spotted("A")).toBe(-1);
    expect(spotted("B")).toBe(0);
    expect(glided.moves).toEqual([]);
  } finally {
    glided.still = false;
    done();
  }
});

/** A pane that changes width keeps the row on the lane it was showing: every lane re-seats at the
 *  new step in one jump, and nothing springs across the change. */
test("a resize re-seats the row at its new step without a glide", () => {
  const done = rolling(NINE);
  try {
    press(LANE_NEXT);
    expect(spotted("B")).toBe(0);
    glided.moves.length = 0;

    const narrower = laidOut([0, 1000], {});
    Sizing.made[0].report();

    const step = (1000 - 2) / 3 + 1;
    const lane = screen.getByRole("region", { name: "A" });
    expect(lane.style.transform).toBe("translateX(" + Math.round(-step * 100) / 100 + "px)");
    expect(glided.moves).toEqual([]);
    narrower.mockRestore();
  } finally {
    done();
  }
});

/** A phone has no bracket keys, no rail and no wheel, so the row it holds is the base row: scrolled
 *  and snapped one lane to a screen by the finger, with no lane carried off by a transform. */
test("a phone pages the row by scrolling, and the ticker stays off", () => {
  atPhoneWidth();
  const done = rolling(NINE);
  try {
    expect(track().className).toContain("max-narrow:overflow-x-auto");
    expect(track().className).toContain("max-narrow:snap-mandatory");
    for (const name of NINE) expect(spotted(name)).toBe(NINE.indexOf(name));
    const lane = screen.getByRole("region", { name: "A" });
    expect(lane.className).toContain("max-narrow:snap-start");

    press(LANE_NEXT);

    expect(spotted("A")).toBe(0);
    expect(glided.moves).toEqual([]);
  } finally {
    done();
    vi.unstubAllGlobals();
  }
});

/** A phone's row scrolls under the finger, so what stands in view is the observer's to say, not the
 *  offset's: a lane swiped to from the far end of the row mounts its body as it comes into view. */
test("a phone's row mounts the lane the finger reaches", () => {
  atPhoneWidth();
  Watching.made = [];
  vi.stubGlobal("IntersectionObserver", Watching);
  Sizing.made = [];
  vi.stubGlobal("ResizeObserver", Sizing);
  const boxes = laidOut([0, 390], {
    A: [0, 390],
    B: [390, 780],
    C: [780, 1170],
    D: [1170, 1560],
    E: [1560, 1950],
  });
  try {
    render(<Spread names={["A", "B", "C", "D", "E"]} />);
    Sizing.made[0].report();

    expect(screen.getByText("A body")).toBeTruthy();
    expect(screen.queryByText("E body")).toBeNull();
    expect(Watching.made).toHaveLength(1);

    Watching.made[0].report([slotFor("E")]);

    expect(screen.getByText("E body")).toBeTruthy();
  } finally {
    boxes.mockRestore();
    vi.unstubAllGlobals();
  }
});

