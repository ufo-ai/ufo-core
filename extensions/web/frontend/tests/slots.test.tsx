import { readFileSync } from "node:fs";
import { join } from "node:path";

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { expect, test } from "vitest";

import { SlotTrack, appended, beside, closed, opened, useSlot, type SlotKind } from "@/kernel/slots";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

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
  children,
}: {
  name: string;
  kind: SlotKind;
  crumb?: Crumb;
  children: ReactNode;
}) {
  return useSlot(children, { id: name, kind, title: name, crumb });
}

/** A screen with a place: the address holds the track, every act writes it, and the lanes are drawn
 *  from it. A lane is opened beside the ones already standing, and each lane holds a link to every
 *  other, which is the press that walks the path. */
function Walk({ names }: { names: string[] }) {
  const [opens, setOpens] = useState<string[]>([]);
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
 *  own focus after it would drop the cursor out of the words they came to write. Escape still
 *  reaches the lane from inside the field. */
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

  await userEvent.keyboard("{Escape}");

  expect(screen.queryByLabelText("Message")).toBeNull();
});

test("Escape leaves a slot, and focus goes back where it came from", async () => {
  render(
    <SlotTrack>
      <Opener name="New thing" />
    </SlotTrack>,
  );

  const act = screen.getByRole("button", { name: "Open New thing" });
  await userEvent.click(act);
  expect(slotFor("New thing").contains(document.activeElement)).toBe(true);

  await userEvent.keyboard("{Escape}");

  expect(screen.queryByRole("region")).toBeNull();
  expect(document.activeElement).toBe(act);
});

/** An act raised from inside a lane cannot be drawn in the lane it is already standing in: filling
 *  that lane empties it of the panel, which unmounts the tab that raised the act and takes the act
 *  with it. It lies over instead — and reaches only as far as its own lanes, so the openers it was
 *  raised from stay uncovered. A cover across the whole host takes the next act with it: an app's
 *  connections and the act that adds one sit side by side under it, and opening one lane locks out
 *  the opener of the next. jsdom honours neither `inert` nor geometry, so this is asserted as the
 *  DOM relationship a browser enforces rather than as a press that fails to land. */
test("the cover an act opens over its lane stops at the standing lanes' edge", async () => {
  render(
    <SlotTrack>
      <p>the list</p>
      <Standing name="assistant" kind="panel">
        <SlotTrack over>
          <Opener name="New task" kind="panel" />
          <Opener name="Second task" kind="panel" />
        </SlotTrack>
      </Standing>
    </SlotTrack>,
  );

  await open("New task");

  expect(standing()).toEqual(["assistant", "New task"]);
  expect(screen.getByText("New task body")).toBeTruthy();
  expect(screen.getByText("the list")).toBeTruthy();

  const cover = slotFor("assistant").querySelector("[data-slot=slot-track]");
  if (!cover) throw new Error("the lane holds no track for the acts raised inside it");
  expect(cover.className).toContain("absolute");
  expect(cover.className).toContain("z-10");
  expect(cover.className).toContain("end-0");
  expect(cover.className).not.toContain("inset-0");

  const band = slotFor("New task").querySelector("[data-slot=header]");
  if (!band) throw new Error("the lane draws no band");
  expect(band.getAttribute("draggable")).toBeNull();

  const next = screen.getByRole("button", { name: "Open Second task" });
  expect(cover.contains(next)).toBe(false);
  expect(next.closest("[inert]")).toBeNull();
  expect(document.querySelector("[inert]")).toBeNull();

  await open("Second task");

  expect(standing()).toEqual(["assistant", "New task", "Second task"]);
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
  expect(path.parentElement!.className).toContain("min-w-(--container-title)");
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
