import { fireEvent, render } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { Command, CommandGroup, CommandItem, CommandList } from "@/components/ui/command";

/** The runs the launcher lists with an empty box, in the order it stands them. */
const RUNS = [
  { heading: "Applications", rows: ["Chat", "Radar", "Artifacts"] },
  { heading: "Threads", rows: ["Rename the deploy job"] },
  { heading: "Places", rows: ["Home", "Apps"] },
];

/** How tall the list is, and where each run starts inside it: a list holding one screen of rows and
 *  two runs standing under the fold. */
const FOLD = 100;
const HEADING = 20;
const TOPS = [0, 200, 400];

function Palette() {
  return (
    <Command label="Search" shouldFilter={false}>
      <CommandList label="Results">
        {RUNS.map((run) => (
          <CommandGroup key={run.heading} heading={run.heading}>
            {run.rows.map((row) => (
              <CommandItem key={row} value={row} primary={row} />
            ))}
          </CommandGroup>
        ))}
      </CommandList>
    </Command>
  );
}

/** jsdom lays nothing out, so the list's fold and the place each run starts at are stated here. A
 *  run's own top moves with the scroll, as it does in a browser. */
function laid() {
  const list = document.querySelector<HTMLElement>("[cmdk-list]");
  if (!list) throw new Error("no list");
  vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(FOLD);
  list.getBoundingClientRect = () => ({ top: 0, height: FOLD }) as DOMRect;
  [...list.querySelectorAll<HTMLElement>("[cmdk-group]")].forEach((group, at) => {
    group.getBoundingClientRect = () => ({ top: TOPS[at] - list.scrollTop }) as DOMRect;
    const heading = group.querySelector<HTMLElement>("[cmdk-group-heading]");
    if (heading) heading.getBoundingClientRect = () => ({ height: HEADING }) as DOMRect;
  });
  fireEvent.scroll(list);
  return list;
}

/** The headings drawn at the foot of the list. The stack is out of the reading order — cmdk names
 *  every run to a screen reader already — so it is read off the DOM rather than by role. */
function stack(): HTMLButtonElement[] {
  const foot = document.querySelector("[data-slot='command-stack']");
  return [...(foot?.querySelectorAll("button") ?? [])];
}

function stacked(): string[] {
  return stack().map((row) => row.textContent ?? "");
}

test("the runs under the fold are named at the foot of the list, in the order they stand in it", () => {
  render(<Palette />);
  laid();
  expect(stacked()).toEqual(["Threads", "Places"]);
});

test("a stacked heading carries the list to its run, and leaves the stack once its run is in view", () => {
  render(<Palette />);
  const list = laid();
  fireEvent.click(stack()[0]);
  expect(list.scrollTop).toBe(TOPS[1]);
  fireEvent.scroll(list);
  expect(stacked()).toEqual(["Places"]);
});

test("a list holding every run under the fold stacks nothing", () => {
  render(<Palette />);
  const list = laid();
  vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(1000);
  fireEvent.scroll(list);
  expect(stacked()).toEqual([]);
});
