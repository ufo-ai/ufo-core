import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { Avatar } from "@/blocks/avatar";
import { Checkbox } from "@/blocks/checkbox";
import {
  Count,
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemHeader,
  ItemMedia,
  ItemMeta,
  ItemSection,
  ItemTitle,
  StatusIcon,
  Tag,
} from "@/blocks/item";
import { ItemPage } from "@/blocks/docs/ItemPage";
import { ItemCheckboxRow } from "@/blocks/docs/examples/item-checkbox-row";
import { ItemCollapsible } from "@/blocks/docs/examples/item-collapsible";
import { ItemSectionEmpty } from "@/blocks/docs/examples/item-section-empty";
import { ItemNarrow } from "@/blocks/docs/examples/item-narrow";
import { ItemStatesInteractive } from "@/blocks/docs/examples/item-states-interactive";

const STYLESHEET = readFileSync(join(import.meta.dirname, "..", "..", "src", "blocks", "item.css"), "utf8");

const EXAMPLES = [
  "Default",
  "Variants",
  "Size sm",
  "Avatar",
  "Image",
  "Group",
  "Header",
  "Link",
  "Dropdown",
  "Extra small",
  "Narrow",
  "Meeting",
  "Collapsible header",
  "Interactive",
  "Disabled",
  "Group with separators",
  "Mono lines",
  "States",
  "Every state",
  "Section",
  "Empty section",
  "Interactive states",
  "Checkbox in a clickable row",
  "Tag as a filter",
  "Media in mono",
  "API",
];

test("a bare item states every variant it is in, so the stylesheet answers one row shape", () => {
  render(
    <Item>
      <ItemContent>
        <ItemTitle>Design sync</ItemTitle>
      </ItemContent>
    </Item>,
  );
  const row = screen.getByText("Design sync").closest(".blk-item");
  expect(row?.getAttribute("data-variant")).toBe("default");
  expect(row?.getAttribute("data-size")).toBe("default");
  expect(row?.getAttribute("data-accent")).toBe("none");
  expect(row?.getAttribute("data-state")).toBe("default");
});

test("every size is one attribute, so a row's height is the stylesheet's answer and never a caller's", () => {
  for (const size of ["default", "sm", "xs"] as const) {
    const { container, unmount } = render(
      <Item size={size}>
        <ItemTitle>Design sync</ItemTitle>
      </Item>,
    );
    expect(container.querySelector(".blk-item")!.getAttribute("data-size")).toBe(size);
    unmount();
  }
});

test("an accent draws the bar and none leaves it out, so a row without an accent has nothing to align to", () => {
  const { rerender, container } = render(
    <Item accent="primary">
      <ItemTitle>Design sync</ItemTitle>
    </Item>,
  );
  expect(container.querySelectorAll(".blk-item-bar")).toHaveLength(1);
  rerender(
    <Item>
      <ItemTitle>Design sync</ItemTitle>
    </Item>,
  );
  expect(container.querySelectorAll(".blk-item-bar")).toHaveLength(0);
});

test("an onClick row takes the press over its whole width, so a list row needs no inner button", async () => {
  const pick = vi.fn();
  render(
    <Item onClick={pick}>
      <ItemTitle>Inbox</ItemTitle>
    </Item>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Inbox" }));
  expect(pick).toHaveBeenCalledTimes(1);
});

test("render draws the row as the element it is handed, so a whole row is one link and still one row", () => {
  const { container } = render(
    <Item variant="outline" accent="primary" render={<a href="#/card" />}>
      <ItemContent>
        <ItemTitle>Card</ItemTitle>
      </ItemContent>
    </Item>,
  );
  const row = screen.getByRole("link", { name: "Card" });
  expect(row.className).toBe("blk-item");
  expect(row.getAttribute("href")).toBe("#/card");
  expect(row.getAttribute("data-variant")).toBe("outline");
  expect(row.getAttribute("data-state")).toBe("default");
  expect(container.querySelectorAll(".blk-item-bar")).toHaveLength(1);
});

test("a disabled item refuses the press, so an unavailable row cannot be reached by keyboard either", async () => {
  const pick = vi.fn();
  render(
    <Item state="disabled" onClick={pick}>
      <ItemTitle>Billing</ItemTitle>
    </Item>,
  );
  const row = screen.getByRole("button", { name: "Billing" });
  expect(row.getAttribute("aria-disabled")).toBe("true");
  expect(row.getAttribute("tabindex")).toBe("-1");
  await userEvent.setup({ pointerEventsCheck: 0 }).click(row);
  fireEvent.keyDown(row, { key: "Enter" });
  expect(pick).not.toHaveBeenCalled();
});

test("a group states whether it is flush, so the rule under the last row is the stylesheet's to drop", () => {
  const { container, rerender } = render(
    <ItemGroup flush>
      <Item>
        <ItemTitle>Design</ItemTitle>
      </Item>
    </ItemGroup>,
  );
  expect(container.querySelector(".blk-item-group")!.getAttribute("data-flush")).toBe("true");
  rerender(
    <ItemGroup>
      <Item>
        <ItemTitle>Design</ItemTitle>
      </Item>
    </ItemGroup>,
  );
  expect(container.querySelector(".blk-item-group")!.getAttribute("data-flush")).toBeNull();
});

test("the media slot names what it holds, so one span answers a mark, a face and a picture", () => {
  const { container } = render(
    <>
      <ItemMedia variant="default">
        <span>K</span>
      </ItemMedia>
      <ItemMedia variant="avatar">
        <Avatar alt="Ana Ruiz" fallback="AR" />
      </ItemMedia>
      <ItemMedia variant="image">
        <img alt="" src="cover.png" />
      </ItemMedia>
    </>,
  );
  const slots = [...container.querySelectorAll(".blk-item-media")].map((slot) => slot.getAttribute("data-variant"));
  expect(slots).toEqual(["default", "avatar", "image"]);
});

test("a collapsible header holds its own open state, so a header used alone still reports what it did", async () => {
  render(<ItemHeader collapsible>Today</ItemHeader>);
  const chevron = screen.getByRole("button");
  expect(chevron.getAttribute("aria-expanded")).toBe("true");
  await userEvent.click(chevron);
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
});

test("the chevron folds the rows the header stands over, so aria-expanded matches what is on screen", async () => {
  render(<ItemCollapsible />);
  expect(screen.getByText("Draft the migration plan")).toBeTruthy();
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Draft the migration plan")).toBeNull();
});

test("every status is one labelled circle, so the progress of a row is read and not inferred from a colour", () => {
  for (const status of ["ready", "started", "working", "done"] as const) {
    const { unmount } = render(<StatusIcon status={status} />);
    const mark = screen.getByLabelText(status);
    expect(mark.tagName).toBe("svg");
    expect(mark.getAttribute("data-status")).toBe(status);
    unmount();
  }
});

test("a description states the line count it is cut at, so one line is the shape a row keeps by default", () => {
  const { container, rerender } = render(<ItemDescription>Agree the navigation pattern.</ItemDescription>);
  expect(container.querySelector(".blk-item-description")!.getAttribute("data-lines")).toBe("1");
  rerender(<ItemDescription lines={2}>Agree the navigation pattern.</ItemDescription>);
  expect(container.querySelector(".blk-item-description")!.getAttribute("data-lines")).toBe("2");
});

test("a tag and a count render their text, so a row's trailing facts are read and not decorative", () => {
  render(
    <>
      <Tag>Project</Tag>
      <Count icon={<StatusIcon status="ready" />}>8</Count>
    </>,
  );
  expect(screen.getByText("Project").className).toContain("blk-tag");
  expect(screen.getByText("8").className).toContain("blk-count");
});

test("the narrow lane leaves the truncation to the stylesheet, so no row sets a width of its own", () => {
  const { container } = render(<ItemNarrow />);
  const rows = [...container.querySelectorAll(".blk-item")];
  expect(rows).toHaveLength(4);
  expect(rows[0].getAttribute("data-accent")).toBe("primary");
  expect(rows[0].getAttribute("data-state")).toBe("default");
  expect(rows[3].getAttribute("data-accent")).toBe("muted");
  expect(rows[3].getAttribute("data-state")).toBe("past");
  for (const content of container.querySelectorAll(".blk-item-content")) {
    expect(content.className).toBe("blk-item-content");
    expect(content.getAttribute("style")).toBeNull();
  }
});

test("each line states its face, so a channel name and an issue number set in mono are one attribute", () => {
  const { container } = render(
    <>
      <ItemTitle font="mono">#eng</ItemTitle>
      <ItemDescription font="mono">Closes #3112</ItemDescription>
      <ItemMeta font="mono">25 Aug</ItemMeta>
    </>,
  );
  const faces = [
    container.querySelector(".blk-item-title")!.getAttribute("data-font"),
    container.querySelector(".blk-item-description")!.getAttribute("data-font"),
    container.querySelector(".blk-item-meta")!.getAttribute("data-font"),
  ];
  expect(faces).toEqual(["mono", "mono", "mono"]);
});

test("a line left alone stays in the sans face, so mono is asked for and never inherited", () => {
  const { container } = render(
    <>
      <ItemTitle>Design sync</ItemTitle>
      <ItemDescription>Agree the navigation pattern.</ItemDescription>
      <ItemMeta>25 Aug</ItemMeta>
    </>,
  );
  const faces = [
    container.querySelector(".blk-item-title")!.getAttribute("data-font"),
    container.querySelector(".blk-item-description")!.getAttribute("data-font"),
    container.querySelector(".blk-item-meta")!.getAttribute("data-font"),
  ];
  expect(faces).toEqual(["sans", "sans", "sans"]);
});

test("the page carries every example, so the reference shows each shape the block ships", () => {
  render(<ItemPage />);
  for (const example of EXAMPLES) {
    expect(screen.getByRole("heading", { name: example })).toBeTruthy();
  }
});

function dropAt(row: Element, clientY: number) {
  const drop = createEvent.drop(row);
  Object.defineProperty(drop, "clientY", { value: clientY });
  fireEvent(row, drop);
}

test("every visual state is one attribute, so a row's fill and lift are the stylesheet's answer", () => {
  const { container } = render(
    <>
      <Item selected>
        <ItemTitle>Selected</ItemTitle>
      </Item>
      <Item dragging>
        <ItemTitle>Dragging</ItemTitle>
      </Item>
      <Item editing>
        <ItemTitle>Editing</ItemTitle>
      </Item>
    </>,
  );
  const rows = [...container.querySelectorAll(".blk-item")];
  expect(rows.map((row) => row.getAttribute("data-selected"))).toEqual(["true", null, null]);
  expect(rows.map((row) => row.getAttribute("data-dragging"))).toEqual([null, "true", null]);
  expect(rows.map((row) => row.getAttribute("data-editing"))).toEqual([null, null, "true"]);
});

test("a drag handle draws the grip, so a row a member can move says so before it is grabbed", () => {
  const { container, rerender } = render(
    <Item dragHandle>
      <ItemTitle>Draft the migration plan</ItemTitle>
    </Item>,
  );
  expect(container.querySelectorAll(".blk-item-grip")).toHaveLength(1);
  rerender(
    <Item>
      <ItemTitle>Draft the migration plan</ItemTitle>
    </Item>,
  );
  expect(container.querySelectorAll(".blk-item-grip")).toHaveLength(0);
});

test("the media slot sizes a checkbox, so a row's box is 16px without the caller saying so", () => {
  const { container } = render(
    <ItemMedia variant="checkbox">
      <Checkbox label="Draft the migration plan" checked={false} onCheckedChange={() => undefined} />
    </ItemMedia>,
  );
  expect(container.querySelector(".blk-item-media")!.getAttribute("data-variant")).toBe("checkbox");
});

test("a status icon with a handler is a button that reports done pressed, so the circle moves the row on", async () => {
  const moved = vi.fn();
  const { rerender } = render(<StatusIcon status="working" label="Move the plan on" onClick={moved} />);
  const circle = screen.getByRole("button", { name: "Move the plan on" });
  expect(circle.getAttribute("aria-pressed")).toBe("false");
  await userEvent.click(circle);
  expect(moved).toHaveBeenCalledTimes(1);
  rerender(<StatusIcon status="done" label="Move the plan on" onClick={moved} />);
  expect(screen.getByRole("button", { name: "Move the plan on" }).getAttribute("aria-pressed")).toBe("true");
});

test("an editable title commits on Enter and drops the draft on Escape, so a rename needs no save button", async () => {
  const user = userEvent.setup();
  const committed = vi.fn();
  const cancelled = vi.fn();
  const changed = vi.fn();
  render(
    <ItemTitle
      editable={{ value: "Draft", onChange: changed, onCommit: committed, onCancel: cancelled }}
    />,
  );
  const field = screen.getByRole("textbox", { name: "Title" });
  await user.type(field, "s");
  expect(changed).toHaveBeenCalledWith("Drafts");
  await user.keyboard("{Enter}");
  expect(committed).toHaveBeenCalledTimes(1);
  await user.keyboard("{Escape}");
  expect(cancelled).toHaveBeenCalledTimes(1);
});

test("a group with onReorder drags its rows and reports the seat one was dropped into", () => {
  const moved = vi.fn();
  const { container } = render(
    <ItemGroup onReorder={moved}>
      <Item>
        <ItemTitle>One</ItemTitle>
      </Item>
      <Item>
        <ItemTitle>Two</ItemTitle>
      </Item>
      <Item>
        <ItemTitle>Three</ItemTitle>
      </Item>
    </ItemGroup>,
  );
  const rows = [...container.querySelectorAll(".blk-item")];
  expect(rows.every((row) => row.getAttribute("draggable") === "true")).toBe(true);

  fireEvent.dragStart(rows[0]);
  expect(container.querySelectorAll(".blk-item")[0].getAttribute("data-dragging")).toBe("true");
  fireEvent.dragOver(rows[2]);
  dropAt(rows[2], 100);
  expect(moved).toHaveBeenCalledWith(0, 2);
  expect(container.querySelectorAll(".blk-item")[0].getAttribute("data-dragging")).toBeNull();

  fireEvent.dragStart(rows[2]);
  dropAt(rows[0], 0);
  expect(moved).toHaveBeenLastCalledWith(2, 0);
});

test("a group without onReorder leaves its rows undraggable, so a fixed list cannot be reordered by accident", () => {
  const { container } = render(
    <ItemGroup>
      <Item>
        <ItemTitle>One</ItemTitle>
      </Item>
    </ItemGroup>,
  );
  expect(container.querySelector(".blk-item")!.getAttribute("draggable")).toBeNull();
});

test("a section draws its own heading and folds its rows, so an app hands over a label and nothing else", async () => {
  const opened = vi.fn();
  const { container } = render(
    <ItemSection label="Today" badge="26" count={2} accent="primary" onOpenChange={opened}>
      <Item>
        <ItemTitle>Draft the migration plan</ItemTitle>
      </Item>
    </ItemSection>,
  );
  const header = container.querySelector(".blk-item-header")!;
  expect(header.getAttribute("data-accent")).toBe("primary");
  expect(header.querySelector(".blk-item-header-badge")!.textContent).toBe("26");
  expect(header.querySelector(".blk-tag")!.textContent).toBe("2");
  expect(screen.getByText("Draft the migration plan")).toBeTruthy();

  const chevron = screen.getByRole("button");
  expect(chevron.getAttribute("aria-expanded")).toBe("true");
  await userEvent.click(chevron);
  expect(opened).toHaveBeenCalledWith(false);
  expect(screen.queryByText("Draft the migration plan")).toBeNull();
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
});

test("a controlled section holds the state its caller gives it, and never folds itself", async () => {
  const opened = vi.fn();
  const { rerender } = render(
    <ItemSection label="Today" open={false} onOpenChange={opened}>
      <Item>
        <ItemTitle>Draft the migration plan</ItemTitle>
      </Item>
    </ItemSection>,
  );
  expect(screen.queryByText("Draft the migration plan")).toBeNull();
  await userEvent.click(screen.getByRole("button"));
  expect(opened).toHaveBeenCalledWith(true);
  expect(screen.queryByText("Draft the migration plan")).toBeNull();
  rerender(
    <ItemSection label="Today" open onOpenChange={opened}>
      <Item>
        <ItemTitle>Draft the migration plan</ItemTitle>
      </Item>
    </ItemSection>,
  );
  expect(screen.getByText("Draft the migration plan")).toBeTruthy();
});

test("defaultOpen sets where an uncontrolled section starts, and collapsible false drops the chevron", async () => {
  const shut = render(
    <ItemSection label="Tomorrow" defaultOpen={false}>
      <Item>
        <ItemTitle>Rewrite the onboarding copy</ItemTitle>
      </Item>
    </ItemSection>,
  );
  expect(screen.queryByText("Rewrite the onboarding copy")).toBeNull();
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByText("Rewrite the onboarding copy")).toBeTruthy();
  shut.unmount();

  render(
    <ItemSection label="Tomorrow" collapsible={false} defaultOpen={false}>
      <Item>
        <ItemTitle>Rewrite the onboarding copy</ItemTitle>
      </Item>
    </ItemSection>,
  );
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("Rewrite the onboarding copy")).toBeTruthy();
});

test("the interactive example checks a row off, picks it, renames it and moves it", async () => {
  const user = userEvent.setup();
  const { container } = render(<ItemStatesInteractive />);

  await user.click(screen.getByRole("button", { name: "Move Draft the migration plan on" }));
  expect(screen.getByRole("button", { name: "Move Draft the migration plan on" }).getAttribute("aria-pressed")).toBe(
    "false",
  );
  await user.click(screen.getByRole("button", { name: "Move Draft the migration plan on" }));
  await user.click(screen.getByRole("button", { name: "Move Draft the migration plan on" }));
  expect(screen.getByRole("button", { name: "Move Draft the migration plan on" }).getAttribute("aria-pressed")).toBe(
    "true",
  );

  await user.click(screen.getByLabelText("Select Draft the migration plan"));
  expect(container.querySelectorAll('.blk-item[data-selected="true"]')).toHaveLength(1);

  await user.click(screen.getByRole("button", { name: "Rename Review the connector audit" }));
  const field = screen.getByRole("textbox", { name: "Title" });
  await user.clear(field);
  await user.type(field, "Review the audit{Enter}");
  expect(screen.getByText("Review the audit")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();

  const rows = [...container.querySelectorAll(".blk-item")];
  fireEvent.dragStart(rows[0]);
  dropAt(rows[2], 100);
  const titles = [...container.querySelectorAll(".blk-item-title")].map((row) => row.textContent);
  expect(titles).toEqual(["Review the audit", "Rewrite the onboarding copy", "Draft the migration plan"]);
});

// A footer whose min-width stays auto is sized by its content, so a chip row painted past the lane.
test("the footer may shrink under its content, so an overflowing chip row scrolls inside it", () => {
  const rule = STYLESHEET.split(".blk-item-footer {")[1].split("}")[0];
  expect(rule).toContain("min-width: 0");
});

test("an onClick row is a div at role button, so a checkbox or a menu inside it is still valid markup", async () => {
  const pick = vi.fn();
  const { container } = render(
    <Item onClick={pick}>
      <ItemContent>
        <ItemTitle>Inbox</ItemTitle>
      </ItemContent>
    </Item>,
  );
  const row = screen.getByRole("button", { name: "Inbox" });
  expect(row.tagName).toBe("DIV");
  expect(row.getAttribute("tabindex")).toBe("0");
  expect(container.querySelector("button")).toBeNull();
  fireEvent.keyDown(row, { key: "Enter" });
  fireEvent.keyDown(row, { key: " " });
  fireEvent.keyDown(row, { key: "a" });
  expect(pick).toHaveBeenCalledTimes(2);
});

test("the box in a clickable row swallows its own click, so ticking a row does not also open it", async () => {
  const user = userEvent.setup();
  const pick = vi.fn();
  const ticked = vi.fn();
  render(
    <Item onClick={pick}>
      <ItemMedia variant="checkbox">
        <Checkbox label="Finish Inbox" checked={false} onCheckedChange={ticked} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Inbox</ItemTitle>
      </ItemContent>
    </Item>,
  );
  await user.click(screen.getByLabelText("Finish Inbox"));
  expect(ticked).toHaveBeenCalledWith(true);
  expect(pick).not.toHaveBeenCalled();
  await user.click(screen.getByText("Inbox"));
  expect(pick).toHaveBeenCalledTimes(1);
});

test("the example row opens on a click and ticks on the box, each act on its own", async () => {
  const user = userEvent.setup();
  const { container } = render(<ItemCheckboxRow />);
  await user.click(screen.getByLabelText("Finish Draft the migration plan"));
  expect((screen.getByLabelText("Finish Draft the migration plan") as HTMLInputElement).checked).toBe(true);
  expect(container.querySelectorAll('.blk-item[data-state="active"]')).toHaveLength(0);

  await user.click(screen.getByText("Draft the migration plan"));
  expect(container.querySelectorAll('.blk-item[data-state="active"]')).toHaveLength(1);
});

test("a tag with onClick is a button reporting whether it is held, so a collection is also a filter", async () => {
  const pressed = vi.fn();
  const { rerender } = render(<Tag onClick={pressed}>Platform</Tag>);
  const tag = screen.getByRole("button", { name: "Platform" });
  expect(tag.getAttribute("aria-pressed")).toBe("false");
  expect(tag.getAttribute("data-pressed")).toBeNull();
  await userEvent.click(tag);
  expect(pressed).toHaveBeenCalledOnce();
  rerender(
    <Tag pressed onClick={pressed}>
      Platform
    </Tag>,
  );
  expect(screen.getByRole("button", { name: "Platform" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Platform" }).getAttribute("data-pressed")).toBe("true");
});

test("a tag without onClick stays a plain pill, so a label is never announced as a control", () => {
  render(<Tag>Platform</Tag>);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("Platform").className).toBe("blk-tag");
});

test("a section with no rows draws its empty line, and drops it as soon as a row arrives", () => {
  const { container, rerender } = render(
    <ItemSection label="Waiting" empty="Every record is approved.">
      {[]}
    </ItemSection>,
  );
  expect(screen.getByText("Every record is approved.").className).toBe("blk-item-section-empty");
  rerender(
    <ItemSection label="Waiting" empty="Every record is approved.">
      <Item>
        <ItemContent>
          <ItemTitle>Draft the migration plan</ItemTitle>
        </ItemContent>
      </Item>
    </ItemSection>,
  );
  expect(screen.queryByText("Every record is approved.")).toBeNull();
  expect(container.querySelectorAll(".blk-item-section > .blk-item")).toHaveLength(1);
});

test("the empty line goes as the example moves a record between its two sections", async () => {
  const user = userEvent.setup();
  render(<ItemSectionEmpty />);
  expect(screen.getByText("A record appears here once you approve it.")).toBeTruthy();
  await user.click(screen.getByText("Draft the migration plan"));
  expect(screen.queryByText("A record appears here once you approve it.")).toBeNull();
  await user.click(screen.getByText("Review the connector audit"));
  expect(screen.getByText("Every record is approved.")).toBeTruthy();
});

test("the media slot takes the mono face like the other parts of a row", () => {
  const { container } = render(
    <Item>
      <ItemMedia variant="default" font="mono">
        349
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Connector audit fails on refresh</ItemTitle>
      </ItemContent>
    </Item>,
  );
  expect(container.querySelector(".blk-item-media")?.getAttribute("data-font")).toBe("mono");
  expect(STYLESHEET).toContain('.blk-item-media[data-font="mono"]');
});

// A trailing icon button paints a hover surface 4px past its own box, which bled over the row's edge.
test("the trailing slot reserves the icon button's inset, so its hover surface stays inside the row", () => {
  const rule = STYLESHEET.split("\n.blk-item-actions {")[1].split("}")[0];
  expect(rule).toContain("padding-inline-end: 4px");
});

test("a section holds 8px between its heading and its first row", () => {
  const rule = STYLESHEET.split('.blk-item-section[data-open="true"] > .blk-item-header {')[1].split("}")[0];
  expect(rule).toContain("margin-bottom: 8px");
});
