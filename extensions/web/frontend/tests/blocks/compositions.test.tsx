import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { CompositionsPage } from "@/blocks/docs/CompositionsPage";
import TodosLane from "@/blocks/docs/examples/compose-todos";
import MeetingsLane from "@/blocks/docs/examples/compose-meetings";
import LeadsLane from "@/blocks/docs/examples/compose-leads";
import CodingLane from "@/blocks/docs/examples/compose-coding";
import AssistantLane from "@/blocks/docs/examples/compose-assistant";
import Shell from "@/blocks/docs/examples/compose-shell";
import CardWithTable from "@/blocks/docs/examples/compose-card-table";
import CardWithItems from "@/blocks/docs/examples/compose-card-items";
import StatDashboard from "@/blocks/docs/examples/compose-stat-dashboard";

const EXAMPLES = [
  "Todos lane",
  "Meetings lane",
  "Leads lane",
  "Coding lane",
  "Assistant lane",
  "Shell",
  "Card with table",
  "Card with items",
  "Stat dashboard",
];

const rows = (root: HTMLElement) => root.querySelectorAll("tbody .blk-table-row").length;

const SUMMARY =
  "The goal of the meeting is to align on the designs and discuss next steps for the week";

test("the todos lane heads each of its four groups with a toggle that folds the rows", () => {
  const { container } = render(<TodosLane />);
  const toggles = screen.getAllByRole("button", { expanded: true });
  expect(toggles.map((toggle) => toggle.textContent)).toEqual([
    "Ideas6",
    "Up next5",
    "In progress5",
    "Shipped3",
  ]);
  expect(rows(container)).toBe(19);

  fireEvent.click(toggles[0]);

  expect(toggles[0].getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Rework the onboarding copy")).toBeNull();
  expect(rows(container)).toBe(13);
});

test("the leads lane search field takes what is typed and narrows the table", () => {
  const { container } = render(<LeadsLane />);
  expect(rows(container)).toBe(12);

  const field = screen.getByRole("searchbox", { name: "Search" }) as HTMLInputElement;
  fireEvent.change(field, { target: { value: "ram" } });

  expect(field.value).toBe("ram");
  expect(rows(container)).toBe(1);
  expect(screen.getByText("Ramp")).toBeTruthy();
});

test("the meetings lane stacks seven meetings under two day headers, each behind its accent bar", () => {
  const { container } = render(<MeetingsLane />);
  const items = [...container.querySelectorAll(".blk-item")];
  expect(items.length).toBe(7);
  for (const item of items) expect(item.querySelector(":scope > .blk-item-bar")).toBeTruthy();
  expect(container.querySelectorAll(".blk-item-header").length).toBe(2);
  expect(container.querySelectorAll('.blk-item[data-accent="primary"]').length).toBe(3);
  expect(container.querySelectorAll('.blk-item[data-state="past"]').length).toBe(4);
  expect(screen.getAllByText(SUMMARY).length).toBe(7);
});

test("the coding lane sets its listing on the lane ground and folds the long lines", () => {
  const { container } = render(<CodingLane />);
  const code = container.querySelector(".blk-lane-body .blk-code")!;
  expect(code.textContent).toContain("TerminalLine");
  expect(code.getAttribute("data-variant")).toBe("plain");
  expect(code.getAttribute("data-wrap")).toBe("true");
  expect(container.querySelectorAll(".blk-lane-foot .blk-prompt").length).toBe(3);
});

test("the assistant lane ends in the two-row composer", () => {
  const { container } = render(<AssistantLane />);
  expect(container.querySelector(".blk-lane-foot .blk-composer")?.getAttribute("data-rows")).toBe("2");
  expect(screen.getByRole("textbox", { name: "Ask Assistant anything..." }).tagName).toBe("TEXTAREA");
});

test("the assistant lane pills the card it made and holds the member's turn at the trailing edge", () => {
  const { container } = render(<AssistantLane />);
  const pill = container.querySelector('.blk-card[data-variant="pill"]')!;
  expect(pill.textContent).toContain("Competitive Analysis");
  expect(pill.querySelector(".blk-avatar")).toBeTruthy();
  const bubble = container.querySelector('.blk-card[data-variant="muted"]')!;
  expect(bubble.getAttribute("data-align")).toBe("end");
  expect(bubble.getAttribute("data-inline")).toBe("true");
});

test("a card carries a table on its own ground", () => {
  const { container } = render(<CardWithTable />);
  expect(container.querySelector(".blk-card .blk-table")).toBeTruthy();
  expect(rows(container)).toBe(5);
});

test("a card carries a group of items and drops the rule under the last one", () => {
  const { container } = render(<CardWithItems />);
  expect(container.querySelectorAll(".blk-card .blk-item-group .blk-item").length).toBe(2);
  expect(container.querySelector(".blk-card .blk-item-group")?.getAttribute("data-flush")).toBe("true");
});

test("the dashboard card grids three tiles over its chart", () => {
  const { container } = render(<StatDashboard />);
  expect(container.querySelectorAll(".blk-card .blk-stat-tile").length).toBe(3);
  expect(container.querySelector(".blk-card .blk-chart")).toBeTruthy();
});

test("the shell stands the four lanes side by side", () => {
  const { container } = render(<Shell />);
  expect(container.querySelectorAll(".blk-lanes > .blk-lane").length).toBe(4);
});

test("the page renders every composition", () => {
  render(<CompositionsPage />);
  for (const title of EXAMPLES) expect(screen.getByRole("heading", { name: title })).toBeTruthy();
});
