import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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
import { soundEnded, soundMoved } from "@/lib/sound";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

import { atPhoneWidth } from "./harness";

vi.mock("@/lib/sound", () => ({ soundMoved: vi.fn(), soundEnded: vi.fn() }));

/** Motion's frame loop needs a browser to run in, so the spring lands at once here and every arm of it
 *  is recorded. */
const glided = vi.hoisted(() => ({ starts: [] as number[], moves: [] as number[], still: false }));

vi.mock("motion/react", async (whole) => {
  const real = await whole<typeof import("motion/react")>();
  return {
    ...real,
    useReducedMotion: () => glided.still,
    animate: (value: { get: () => number; jump: (to: number) => void }, to: number) => {
      glided.starts.push(value.get());
      glided.moves.push(to);
      value.jump(to);
      return { stop: () => {} };
    },
  };
});

const heard = () => {
  vi.mocked(soundMoved).mockClear();
  vi.mocked(soundEnded).mockClear();
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

/** Identity is the answer, not equal contents: a fresh array of the same ids pushes a history entry and
 *  remounts the record being read. */
test("re-pressing the lane that already stands hands back the very track it was given", () => {
  const track = ["one", "two", "three"];
  expect(opened(track, "two", "one")).toBe(track);
  expect(opened(track, "one")).toBe(track);
  expect(appended(track, "two")).toBe(track);
  expect(closed(track, "four")).toBe(track);
});

test("a lane reached again from further down the path moves rather than doubling", () => {
  expect(opened(["one", "two", "three"], "one", "three")).toEqual(["two", "three", "one"]);
});

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

test("command, control and the middle button ask for a lane beside", () => {
  expect(beside({ metaKey: true, ctrlKey: false, button: 0 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: true, button: 0 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: false, button: 1 })).toBe(true);
  expect(beside({ metaKey: false, ctrlKey: false, button: 0 })).toBe(false);
  expect(beside({ metaKey: false, ctrlKey: false })).toBe(false);
});

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

/** `display: contents` promotes whatever the view rendered to be the flex item, so the pane's own body
 *  is in the track on the same terms as the slots. */
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

/** An element that moves position is torn down and built again, firing every read inside it twice. */
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

test("a lane arriving under an open layer leaves the focus the layer holds", async () => {
  render(
    <>
      <div role="dialog">
        <input aria-label="Search" />
      </div>
      <SlotTrack>
        <Opener name="New thing" />
      </SlotTrack>
    </>,
  );

  const box = screen.getByLabelText("Search");
  box.focus();
  fireEvent.click(screen.getByRole("button", { name: "Open New thing" }));

  expect(screen.getByText("New thing body")).toBeTruthy();
  expect(document.activeElement).toBe(box);
});

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

/** The track reads nothing off the report — it measures the row itself — so a wake-up carrying no
 *  records is the whole of what it needs. */
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

/** The width the rail leaves on the 1512px laptop `--size-slot-min` is set against. */
const PANE_WIDTH = 1472;

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

test("neither verb stands a lane past the cap", () => {
  const full = Array.from({ length: TRACK_MAX_SLOTS }, (_, at) => "object/report/" + at);
  const room = full.slice(0, -1);

  expect(appended(full, "object/report/late")).toBe(full);
  expect(opened(full, "object/report/late", full.at(-1))).toBe(full);

  expect(appended(room, "object/report/late")).toEqual([...room, "object/report/late"]);
  expect(opened(full, "object/report/late", full[0])).toEqual([full[0], "object/report/late"]);
  expect(opened(full, "object/report/late")).toEqual(["object/report/late"]);
});

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

/** A row jsdom never laid out has no width, so every lane in it is near — which is what a track
 *  measured before its first layout answers too. */
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

/** An observer's first batch arrives a task after the paint, so the track measures the row and its
 *  lanes itself as they stand. */
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

function Taking({
  opening,
  coming,
  drawn,
}: {
  opening: string[];
  coming: string;
  drawn: string[];
}) {
  const [opens, setOpens] = useState(opening);
  return (
    <SlotTrack over opens={opens} onMove={setOpens}>
      {opens.map((name) => (
        <Taken
          key={name}
          name={name}
          opens={opens}
          coming={coming}
          drawn={drawn}
          onOpens={setOpens}
        />
      ))}
    </SlotTrack>
  );
}

function Taken({
  name,
  opens,
  coming,
  drawn,
  onOpens,
}: {
  name: string;
  opens: string[];
  coming: string;
  drawn: string[];
  onOpens: (opens: string[]) => void;
}) {
  const lane = useSlot(
    <>
      <p>{name} body</p>
      <button
        type="button"
        onClick={() => onOpens(opens.map((held) => (held === name ? coming : held)))}
      >
        Pick in {name}
      </button>
    </>,
    { id: name, kind: "reading", title: name },
  );
  useLayoutEffect(() => {
    const stood = document.querySelector<HTMLElement>('section[aria-label="' + name + '"]');
    if (stood === null) return;
    const holds = (stood.textContent ?? "").includes(name + " body");
    drawn.push(name + ":" + (stood.hidden ? "hidden" : holds ? "body" : "empty"));
  });
  return lane;
}

test("a pick that takes the expanded lane over leaves the lanes beside it drawn", async () => {
  const drawn: string[] = [];
  render(<Taking opening={["One", "Two", "Three"]} coming="Four" drawn={drawn} />);

  await userEvent.click(screen.getByRole("button", { name: "Expand One" }));

  expect(standing()).toEqual(["One"]);
  expect(drawn).toContain("One:body");
  expect(drawn).toContain("Two:hidden");
  drawn.length = 0;

  await userEvent.click(screen.getByRole("button", { name: "Pick in One" }));

  expect(drawn.filter((said) => said === "Two:empty" || said === "Three:empty")).toEqual([]);
  expect(drawn).toContain("Two:body");
  expect(standing()).toEqual(["Four", "Two", "Three"]);
  expect(screen.getByText("Four body")).toBeTruthy();
  expect(screen.getByText("Two body")).toBeTruthy();
});

/** A browser puts no focus on an element wearing `hidden`, so a press reaching for one would sound the
 *  move and leave the member where they stood. */
test("the walk under an expansion carries the expansion and reaches for no hidden lane", async () => {
  const drawn: string[] = [];
  render(<Taking opening={["One", "Two", "Three"]} coming="Four" drawn={drawn} />);
  await userEvent.click(screen.getByRole("button", { name: "Expand One" }));
  expect(standing()).toEqual(["One"]);

  const reached: string[] = [];
  const focuses = vi
    .spyOn(HTMLElement.prototype, "focus")
    .mockImplementation(function (this: HTMLElement) {
      if (this.tagName === "SECTION") reached.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    heard();

    press(LANE_NEXT);

    expect(standing()).toEqual(["Two"]);
    expect(reached).toEqual([]);
    expect(soundMoved).toHaveBeenCalledTimes(1);

    press(LANE_NEXT);

    expect(standing()).toEqual(["Three"]);

    press(LANE_NEXT);

    expect(standing()).toEqual(["Three"]);
    expect(soundEnded).toHaveBeenCalledTimes(1);

    press(LANE_PRIOR);
    press(LANE_PRIOR);

    expect(standing()).toEqual(["One"]);
    expect(reached).toEqual([]);
  } finally {
    focuses.mockRestore();
  }
});

function Seeking({
  names,
  opening,
  seeking,
  onActive,
}: {
  names: string[];
  opening: string[];
  seeking?: string;
  onActive?: (id: string | undefined) => void;
}) {
  const [opens, setOpens] = useState(opening);
  const [seek, setSeek] = useState<Seek | undefined>(
    seeking === undefined ? undefined : { id: seeking, expansion: "switch" },
  );
  return (
    <SlotTrack opens={opens} onMove={setOpens} seek={seek} onActive={onActive}>
      {names.map((name) => (
        <button
          key={name}
          type="button"
          onClick={() => setSeek({ id: name, expansion: "switch" })}
        >
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

test("a lane sought before it stands is the lane the track names as it lands", async () => {
  const seen: (string | undefined)[] = [];
  const note = (id: string | undefined) => void seen.push(id);
  render(<Seeking names={["A", "B"]} opening={["A", "B"]} seeking="B" onActive={note} />);

  expect(standing()).toEqual(["A", "B"]);
  expect(seen.at(-1)).toBe("B");

  await userEvent.click(screen.getByRole("button", { name: "Close B" }));

  expect(standing()).toEqual(["A"]);
  expect(seen.at(-1)).toBeUndefined();
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

const lit = () =>
  screen
    .queryAllByRole("region")
    .filter((slot) => slot.hasAttribute("data-lit"))
    .map((slot) => slot.getAttribute("aria-label"));

const lands = (on: HTMLElement) => act(() => on.focus());

test("the lane being typed in draws the focus stripe, and clears when the typing ends", async () => {
  render(<Fields names={["A", "B", "C"]} />);

  expect(lit()).toEqual([]);

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(lit()).toEqual(["A"]);
  const stripe = (name: string) => {
    const found = slotFor(name).querySelector('[data-slot="focus"]');
    if (!(found instanceof HTMLElement)) throw new Error(name + " has no focus stripe");
    return found;
  };
  const worn = (name: string) => stripe(name).className.split(" ");
  expect(stripe("A").hasAttribute("data-on")).toBe(true);
  expect(worn("A")).toEqual(
    expect.arrayContaining(["scale-x-100", "h-1", "shrink-0", "bg-ring", "transition-transform"]),
  );
  expect(worn("A")).not.toContain("absolute");
  expect(stripe("B").hasAttribute("data-on")).toBe(false);
  expect(worn("B")).toContain("scale-x-0");
  expect(worn("B")).toContain("h-1");
  for (const name of ["A", "B", "C"]) expect(slotFor(name).firstElementChild).toBe(stripe(name));
  expect(slotFor("B").className).not.toContain("opacity-");
  expect(slotFor("A").className.split(" ")).not.toContain("outline-ring");
  expect(slotFor("B").className.split(" ")).toContain("outline-none");
  expect(worn("B")).toContain("group-focus-visible/lane:scale-x-100");

  lands(screen.getByRole("textbox", { name: "B composer" }));
  expect(lit()).toEqual(["B"]);

  await userEvent.keyboard("{Escape}");
  expect(document.activeElement).toBe(slotFor("B"));
  expect(lit()).toEqual([]);

  lands(screen.getByRole("button", { name: "A act" }));
  expect(lit()).toEqual([]);
});

test("a lane standing alone keeps the stripe's room and never draws it", () => {
  render(<Fields names={["A"]} />);
  const stripe = slotFor("A").querySelector('[data-slot="focus"]');
  if (!(stripe instanceof HTMLElement)) throw new Error("A has no focus stripe");

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(lit()).toEqual([]);
  expect(stripe.className.split(" ")).toContain("h-1");
  expect(stripe.className.split(" ")).toContain("scale-x-0");
  expect(stripe.className).not.toContain("group-focus-visible");
});

/** An unnamed `group` on the lane would fire every `group-hover:` and `group-focus-visible:` utility
 *  standing inside it on the lane's own hover and focus. */
test("the lane names its group, so the mark reaches the stripe alone", () => {
  render(<Fields names={["A", "B"]} />);
  const worn = slotFor("A").className.split(" ");

  expect(worn).toContain("group/lane");
  expect(worn).not.toContain("group");
});

test("the stripe sweeps toward the lane the cursor moved to", async () => {
  render(<Fields names={["A", "B", "C"]} />);
  const stripe = (name: string) => {
    const found = slotFor(name).querySelector('[data-slot="focus"]');
    if (!(found instanceof HTMLElement)) throw new Error(name + " has no focus stripe");
    return found.className.split(" ");
  };
  const origins = (name: string) => stripe(name).filter((worn) => worn.startsWith("origin-"));

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(origins("A")).toEqual([]);
  expect(stripe("A")).toEqual(expect.arrayContaining(["duration-200", "ease-enter"]));
  expect(stripe("A")).not.toContain("delay-100");

  lands(screen.getByRole("textbox", { name: "C composer" }));
  expect(origins("A")).toEqual(["origin-right"]);
  expect(stripe("A")).toEqual(expect.arrayContaining(["scale-x-0", "duration-150", "ease-in"]));
  expect(stripe("A")).not.toContain("delay-100");
  expect(origins("C")).toEqual(["origin-left"]);
  expect(stripe("C")).toEqual(
    expect.arrayContaining(["scale-x-100", "delay-100", "duration-200", "ease-out"]),
  );
  expect(origins("B")).toEqual([]);
  expect(stripe("B")).toEqual(expect.arrayContaining(["scale-x-0", "ease-leave"]));

  lands(screen.getByRole("textbox", { name: "B composer" }));
  expect(origins("C")).toEqual(["origin-left"]);
  expect(origins("B")).toEqual(["origin-right"]);
  expect(origins("A")).toEqual([]);

  await userEvent.keyboard("{Escape}");
  expect(lit()).toEqual([]);
  expect(origins("B")).toEqual([]);

  lands(screen.getByRole("button", { name: "A act" }));
  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(lit()).toEqual(["A"]);
  expect(origins("A")).toEqual([]);
});

test("a lane beside the marked one is pressed and typed into like any other", async () => {
  render(<Fields names={["A", "B"]} />);

  lands(screen.getByRole("textbox", { name: "A composer" }));
  expect(lit()).toEqual(["A"]);

  await userEvent.click(screen.getByRole("textbox", { name: "B composer" }));
  await userEvent.keyboard("hello");

  expect((screen.getByRole("textbox", { name: "B composer" }) as HTMLTextAreaElement).value).toBe(
    "hello",
  );
  expect(lit()).toEqual(["B"]);
});

/** A browser scrolling a second time to the element it has just focused fights the row's snap and
 *  leaves it resting between two lanes. */
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

test("a lane opened by a press is silent, and so is a row stood whole", async () => {
  heard();
  render(<Walk names={["A", "B", "C", "D"]} opening={["A", "B", "C"]} />);

  expect(soundMoved).not.toHaveBeenCalled();

  await open("D");

  expect(soundMoved).not.toHaveBeenCalled();
  expect(soundEnded).not.toHaveBeenCalled();
});

test("the row moving under a bracket press is heard, once per press", async () => {
  heard();
  render(<Row names={["A", "B", "C"]} />);

  press(LANE_NEXT);
  expect(soundMoved).toHaveBeenCalledTimes(1);

  press(LANE_PRIOR);
  expect(soundMoved).toHaveBeenCalledTimes(2);
});

test("a seek is silent, whether the lane stands or lands", async () => {
  heard();
  const { unmount } = render(<Seeking names={["A", "B", "C"]} opening={["A", "B"]} />);

  await userEvent.click(screen.getByRole("button", { name: "Seek B" }));
  expect(soundMoved).not.toHaveBeenCalled();

  unmount();
  heard();
  render(<Seeking names={["A", "B"]} opening={["A", "B"]} seeking="B" />);

  expect(soundMoved).not.toHaveBeenCalled();
  expect(soundEnded).not.toHaveBeenCalled();
});


test("the track names the lane the member stands in, and keeps naming it after focus leaves", () => {
  const seen: (string | undefined)[] = [];
  const note = (id: string | undefined) => void seen.push(id);
  render(
    <SlotTrack onActive={note}>
      <Standing name="A" kind="panel">
        <p>A body</p>
      </Standing>
      <Standing name="B" kind="panel">
        <p>B body</p>
      </Standing>
    </SlotTrack>,
  );

  expect(seen).toEqual([undefined]);

  press(LANE_NEXT);

  expect(seen.at(-1)).toBe("B");

  act(() => slotFor("B").blur());

  expect(seen.at(-1)).toBe("B");
});

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

test("the bracket keys walk the row, and it stops at both ends", () => {
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    render(<Row names={["A", "B", "C"]} />);
    heard();
    expect(standing()).toEqual(["A", "B", "C"]);
    expect(document.activeElement).toBe(document.body);

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("B"));
    expect(scrolled).toEqual(["B"]);

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("C"));

    press(LANE_NEXT);
    expect(document.activeElement).toBe(slotFor("C"));
    expect(soundEnded).toHaveBeenCalledTimes(1);

    press(LANE_PRIOR);
    expect(document.activeElement).toBe(slotFor("B"));

    press(LANE_PRIOR);
    expect(document.activeElement).toBe(slotFor("A"));

    press(LANE_PRIOR);
    expect(document.activeElement).toBe(slotFor("A"));
    expect(scrolled).toEqual(["B", "C", "B", "A"]);
    expect(soundMoved).toHaveBeenCalledTimes(4);
    expect(soundEnded).toHaveBeenCalledTimes(2);
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
      <button type="button" onClick={() => setOpens([...opens, LATE])}>
        Open a lane at the end
      </button>
      <button type="button" onClick={() => setOpens(["A", "B", "C"])}>
        Stand three lanes
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

const LATE = "J";

/** The pane the laptop leaves, four lanes across at `--size-slot-min`, and the step from one lane's left
 *  edge to the next: its share of the row, and the hairline after it. */
const ROW_WIDTH = 1472;
const STEP = (ROW_WIDTH - 3) / 4 + 1;

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
  glided.starts.length = 0;
  glided.moves.length = 0;
  return () => boxes.mockRestore();
}

test("a row wider than the pane queues its far lanes past the right edge", () => {
  const done = rolling(NINE);
  try {
    expect(spotted("A")).toBe(0);
    expect(spotted("E")).toBe(4);
    expect(spotted("F")).toBe(5);
    expect(spotted("I")).toBe(8);
  } finally {
    done();
  }
});

test("a bracket press stands the member in the next lane, and holds the row still", () => {
  const done = rolling(NINE);
  try {
    heard();

    press(LANE_NEXT);

    expect(document.activeElement).toBe(slotFor("B"));
    expect(spotted("A")).toBe(0);
    expect(spotted("E")).toBe(4);
    expect(soundMoved).toHaveBeenCalledTimes(1);

    press(LANE_PRIOR);

    expect(document.activeElement).toBe(slotFor("A"));
    expect(spotted("A")).toBe(0);
    expect(soundEnded).not.toHaveBeenCalled();
  } finally {
    done();
  }
});

test("the walk carries the row only where the lane it lands on is off the screen", () => {
  const done = rolling(NINE);
  try {
    for (let walked = 0; walked < 4; walked += 1) press(LANE_NEXT);

    expect(document.activeElement).toBe(slotFor("E"));
    expect(spotted("E")).toBe(3);
    expect(spotted("A")).toBe(-1);

    press(LANE_NEXT);

    expect(document.activeElement).toBe(slotFor("F"));
    expect(spotted("F")).toBe(3);
    expect(spotted("B")).toBe(-1);
  } finally {
    done();
  }
});

test("the walk stops at both ends, and the press past one is heard as the end", () => {
  const done = rolling(NINE);
  try {
    heard();

    press(LANE_PRIOR);

    expect(document.activeElement).toBe(document.body);
    expect(spotted("A")).toBe(0);
    expect(soundMoved).not.toHaveBeenCalled();
    expect(soundEnded).toHaveBeenCalledTimes(1);

    for (let walked = 0; walked < 8; walked += 1) press(LANE_NEXT);

    expect(document.activeElement).toBe(slotFor("I"));
    expect(spotted("F")).toBe(0);
    expect(spotted("I")).toBe(3);
    expect(soundMoved).toHaveBeenCalledTimes(8);

    press(LANE_NEXT);

    expect(document.activeElement).toBe(slotFor("I"));
    expect(spotted("I")).toBe(3);
    expect(soundEnded).toHaveBeenCalledTimes(2);
  } finally {
    done();
  }
});

test("a row every lane fits rolls nowhere", () => {
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

test("the wheel stops at both ends of the row", () => {
  vi.useFakeTimers();
  const done = rolling(NINE);
  try {
    const roll = (deltaX: number) =>
      act(() =>
        void track().dispatchEvent(
          new WheelEvent("wheel", { deltaX, deltaY: 0, cancelable: true, bubbles: true }),
        ),
      );

    roll(-400);

    expect(spotted("A")).toBe(0);

    roll(4000);

    expect(spotted("A")).toBe(-5);
    expect(spotted("I")).toBe(3);

    roll(2000);

    expect(spotted("A")).toBe(-5);

    act(() => vi.advanceTimersByTime(200));

    expect(spotted("A")).toBe(-5);
  } finally {
    done();
    vi.useRealTimers();
  }
});

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

test("a lane more than a lane past either edge of the rolling row holds no body", () => {
  const done = rolling(NINE);
  try {
    for (const name of ["A", "B", "C", "D", "E", "F"]) {
      expect(screen.getByText(name + " body")).toBeTruthy();
    }
    expect(screen.queryByText("G body")).toBeNull();
    expect(screen.queryByText("H body")).toBeNull();
    expect(screen.queryByText("I body")).toBeNull();
  } finally {
    done();
  }
});

test("a lane the address moved slides to its new place, and a lane that stayed does not", () => {
  const done = rolling(NINE);
  try {
    fireEvent.click(screen.getByRole("button", { name: "Carry the last to the head" }));

    expect(spotted("I")).toBe(0);
    expect(spotted("A")).toBe(1);
    expect(spotted("H")).toBe(8);

    glided.moves.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Swap the first two" }));

    expect(glided.moves).toHaveLength(2);
    expect(spotted("A")).toBe(0);
    expect(spotted("I")).toBe(1);
  } finally {
    done();
  }
});

test("a lane joining a fitting row glides in from one step left, and the row glides with it", () => {
  const done = rolling(["A", "B", "C"]);
  try {
    const wide = (ROW_WIDTH - 2) / 3 + 1;

    fireEvent.click(screen.getByRole("button", { name: "Open a lane at the end" }));

    expect(standing()).toEqual(["A", "B", "C", LATE]);
    expect(glided.starts).toEqual([wide, 2 * wide, 3 * STEP - STEP]);
    expect(glided.moves).toEqual([STEP, 2 * STEP, 3 * STEP]);
    expect(spotted("A")).toBe(0);
    expect(spotted(LATE)).toBe(3);
  } finally {
    done();
  }
});

test("a lane joining a rolling row glides in from one step left of the row's end", () => {
  const done = rolling(NINE);
  try {
    fireEvent.click(screen.getByRole("button", { name: "Open a lane at the end" }));

    expect(glided.starts).toEqual([9 * STEP - STEP, 0]);
    expect(glided.moves).toEqual([9 * STEP, 6 * STEP]);
    expect(spotted(LATE)).toBe(3);
    expect(spotted("A")).toBe(-6);
  } finally {
    done();
  }
});

test("a row that stands several lanes at once slides none of them", () => {
  const done = rolling([]);
  try {
    fireEvent.click(screen.getByRole("button", { name: "Stand three lanes" }));

    expect(standing()).toEqual(["A", "B", "C"]);
    expect(glided.moves).toEqual([]);
    for (const name of ["A", "B", "C"]) {
      expect(slotFor(name).style.transform).toBe("translateX(0px)");
    }
  } finally {
    done();
  }
});

test("a reduced-motion arrival seats the lane in its place without a glide", () => {
  glided.still = true;
  const done = rolling(["A", "B", "C"]);
  try {
    fireEvent.click(screen.getByRole("button", { name: "Open a lane at the end" }));

    expect(glided.moves).toEqual([]);
    for (const name of ["A", "B", "C", LATE]) {
      expect(slotFor(name).style.transform).toBe("translateX(0px)");
    }
    expect(spotted(LATE)).toBe(3);
  } finally {
    glided.still = false;
    done();
  }
});

test("a reduced-motion walk lands the row without a glide", () => {
  glided.still = true;
  const done = rolling(NINE);
  try {
    for (let walked = 0; walked < 4; walked += 1) press(LANE_NEXT);

    expect(spotted("A")).toBe(-1);
    expect(spotted("E")).toBe(3);
    expect(glided.moves).toEqual([]);
  } finally {
    glided.still = false;
    done();
  }
});

test("a resize re-seats the row at its new step without a glide", () => {
  const done = rolling(NINE);
  try {
    for (let walked = 0; walked < 4; walked += 1) press(LANE_NEXT);
    expect(spotted("A")).toBe(-1);
    glided.moves.length = 0;

    const narrower = laidOut([0, 1000], {});
    Sizing.made[0].report();

    const step = (1000 - 1) / 2 + 1;
    const lane = screen.getByRole("region", { name: "A" });
    expect(lane.style.transform).toBe("translateX(" + Math.round(-step * 100) / 100 + "px)");
    expect(glided.moves).toEqual([]);
    narrower.mockRestore();
  } finally {
    done();
  }
});

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
