import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { TablePage } from "@/blocks/docs/TablePage";
import { TableSortedElsewhere } from "@/blocks/docs/examples/table-sorted-elsewhere";
import { TableActions } from "@/blocks/docs/examples/table-actions";
import { TableRtl } from "@/blocks/docs/examples/table-rtl";
import { TableStatesInteractive } from "@/blocks/docs/examples/table-states-interactive";
import {
  ScoreDot,
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
  TableSection,
} from "@/blocks/table";

const STYLESHEET = readFileSync(join(import.meta.dirname, "..", "..", "src", "blocks", "table.css"), "utf8");

const EXAMPLES = [
  "Default",
  "Leads",
  "Grouped sections",
  "Sortable header",
  "Sort driven elsewhere",
  "Selected row",
  "Dense",
  "Empty",
  "Loading",
  "With footer and caption",
  "Actions",
  "RTL",
  "Data table",
  "Sorting",
  "Filtering",
  "Column visibility",
  "Row selection",
  "Pagination",
  "Full",
  "Every state",
  "Interactive states",
];

test("a section folds its rows away and says so on its heading", () => {
  render(
    <Table>
      <TableSection title="Ideas" count={2} status="ready" columns={2}>
        <TableRow>
          <TableCell>Rework the onboarding copy</TableCell>
        </TableRow>
        <TableRow>
          <TableCell>Audit the empty states</TableCell>
        </TableRow>
      </TableSection>
    </Table>,
  );
  const heading = screen.getByRole("button", { name: /Ideas/ });
  expect(heading.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Rework the onboarding copy")).toBeTruthy();

  fireEvent.click(heading);
  expect(heading.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Rework the onboarding copy")).toBeNull();

  fireEvent.click(heading);
  expect(heading.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Audit the empty states")).toBeTruthy();
});

test("a section reports the state its heading was pressed towards", () => {
  const opened = vi.fn();
  render(
    <Table>
      <TableSection title="Shipped" open={false} onOpenChange={opened} status="done">
        <TableRow>
          <TableCell>Dark theme tokens</TableCell>
        </TableRow>
      </TableSection>
    </Table>,
  );
  expect(screen.queryByText("Dark theme tokens")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Shipped/ }));
  expect(opened).toHaveBeenCalledWith(true);
});

test("a selected row publishes its selection and a clickable row answers a click", () => {
  const picked = vi.fn();
  render(
    <Table>
      <TableBody>
        <TableRow selected onClick={picked}>
          <TableCell>Airbnb</TableCell>
        </TableRow>
        <TableRow selected={false}>
          <TableCell>Stripe</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
  const [chosen, other] = screen.getAllByRole("row");
  expect(chosen.getAttribute("aria-selected")).toBe("true");
  expect(other.getAttribute("aria-selected")).toBe("false");

  fireEvent.click(screen.getByText("Airbnb"));
  expect(picked).toHaveBeenCalledOnce();
});

test("a sortable heading publishes its direction and calls back when pressed", () => {
  const sort = vi.fn();
  const { rerender } = render(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead sortable sorted="asc" onSort={sort}>
            Headcount
          </TableHead>
          <TableHead>Company</TableHead>
        </TableRow>
      </TableHeader>
    </Table>,
  );
  const [sorting, plain] = screen.getAllByRole("columnheader");
  expect(sorting.getAttribute("aria-sort")).toBe("ascending");
  expect(plain.getAttribute("aria-sort")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Headcount" }));
  expect(sort).toHaveBeenCalledOnce();

  rerender(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead sortable sorted="desc" onSort={sort}>
            Headcount
          </TableHead>
          <TableHead sortable sorted={false} onSort={sort}>
            Company
          </TableHead>
        </TableRow>
      </TableHeader>
    </Table>,
  );
  const [down, unsorted] = screen.getAllByRole("columnheader");
  expect(down.getAttribute("aria-sort")).toBe("descending");
  expect(unsorted.getAttribute("aria-sort")).toBe("none");
});

test("a score dot takes its tier and its fade from the value", () => {
  render(
    <div>
      <ScoreDot value={98} />
      <ScoreDot value={70} />
      <ScoreDot value={69} />
      <ScoreDot value={30} />
      <ScoreDot value={29} />
      <ScoreDot value={0} />
    </div>,
  );
  const dots = Array.from(document.querySelectorAll<HTMLElement>(".blk-score"));
  expect(dots.map((dot) => dot.getAttribute("data-tier"))).toEqual([
    "high",
    "high",
    "mid",
    "mid",
    "low",
    "low",
  ]);
  expect(dots.map((dot) => dot.style.getPropertyValue("--blk-score"))).toEqual([
    "0.98",
    "0.7",
    "0.69",
    "0.3",
    "0.29",
    "0",
  ]);
});

test("a dimmed cell says so, so the leads index reads at half the secondary ink", () => {
  render(
    <Table>
      <TableBody>
        <TableRow>
          <TableCell muted="dim">1</TableCell>
          <TableCell muted>Payments</TableCell>
          <TableCell>Stripe</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
  const [index, category, company] = screen.getAllByRole("cell");
  expect(index.getAttribute("data-muted")).toBe("dim");
  expect(category.getAttribute("data-muted")).toBe("true");
  expect(company.getAttribute("data-muted")).toBeNull();
});

test("an empty table draws one cell across every column", () => {
  render(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Company</TableHead>
          <TableHead>Category</TableHead>
          <TableHead align="right">Headcount</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableEmpty columns={3}>No leads match this filter.</TableEmpty>
      </TableBody>
    </Table>,
  );
  const cell = screen.getByText("No leads match this filter.");
  expect(cell.tagName).toBe("TD");
  expect(cell.getAttribute("colspan")).toBe("3");
});

test("a sortable heading publishes its direction without a button until it is given a handler", () => {
  render(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead sortable sorted="desc">
            Amount
          </TableHead>
        </TableRow>
      </TableHeader>
    </Table>,
  );
  expect(screen.getByRole("columnheader").getAttribute("aria-sort")).toBe("descending");
  expect(screen.queryByRole("button")).toBeNull();
});

test("a heading sorted from elsewhere still draws the arrow and publishes the direction", () => {
  const { container } = render(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead sorted="asc">Company</TableHead>
          <TableHead>Headcount</TableHead>
        </TableRow>
      </TableHeader>
    </Table>,
  );
  const [sorted, plain] = screen.getAllByRole("columnheader");
  expect(sorted.getAttribute("aria-sort")).toBe("ascending");
  expect(sorted.getAttribute("data-sorted")).toBe("asc");
  expect(sorted.querySelector(".blk-table-cell-inner > svg")).toBeTruthy();
  expect(plain.getAttribute("aria-sort")).toBeNull();
  expect(plain.querySelector("svg")).toBeNull();
  expect(container.querySelectorAll("button")).toHaveLength(0);
});

test("the menu moves the arrow between the columns it sorts, leaving neither heading a button", async () => {
  const user = userEvent.setup();
  const { container } = render(<TableSortedElsewhere />);
  const column = (name: string) => screen.getByRole("columnheader", { name });
  expect(column("Headcount").getAttribute("aria-sort")).toBe("descending");
  expect(column("Company").getAttribute("aria-sort")).toBeNull();

  await user.click(screen.getByRole("button", { name: "Sort by headcount" }));
  await user.click(await screen.findByRole("menuitemcheckbox", { name: "Company" }));
  expect(column("Company").getAttribute("aria-sort")).toBe("ascending");
  expect(column("Headcount").getAttribute("aria-sort")).toBeNull();
  expect(container.querySelectorAll(".blk-table-sort")).toHaveLength(0);
});

// A trailing icon button paints a hover surface 4px past its own box, which bled over the table's edge.
test("the last cell reserves the icon button's inset, so its hover surface stays inside the table", () => {
  const rule = STYLESHEET.split(".blk-table-row > :last-child {")[1].split("}")[0];
  expect(rule).toContain("padding-inline-end: 4px");
});

test("the actions example opens a menu for one row", async () => {
  const user = userEvent.setup();
  render(<TableActions />);
  await user.click(screen.getByRole("button", { name: "Actions for Desk lamp" }));
  expect(screen.getByRole("menuitem", { name: "Copy ID" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "View details" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Delete" }).getAttribute("data-destructive")).toBe(
    "true",
  );
});

test("the rtl example turns the table around", () => {
  render(<TableRtl />);
  const wrap = document.querySelector(".blk-table-wrap");
  expect(wrap?.parentElement?.getAttribute("dir")).toBe("rtl");
});

test("the docs page renders every example", () => {
  render(<TablePage />);
  for (const title of EXAMPLES) {
    expect(screen.getByRole("heading", { name: title, level: 3 })).toBeTruthy();
  }
});

function dropAt(row: Element, clientY: number) {
  const drop = createEvent.drop(row);
  Object.defineProperty(drop, "clientY", { value: clientY });
  fireEvent(row, drop);
}

test("a row states the state it is in, so a held or edited row is the stylesheet's to draw", () => {
  const { container } = render(
    <Table>
      <TableBody>
        <TableRow selected>
          <TableCell>Selected</TableCell>
        </TableRow>
        <TableRow dragging>
          <TableCell>Dragging</TableCell>
        </TableRow>
        <TableRow editing>
          <TableCell>Editing</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
  const rows = [...container.querySelectorAll(".blk-table-row")];
  expect(rows.map((row) => row.getAttribute("data-selected"))).toEqual(["true", null, null]);
  expect(rows.map((row) => row.getAttribute("data-dragging"))).toEqual([null, "true", null]);
  expect(rows.map((row) => row.getAttribute("data-editing"))).toEqual([null, null, "true"]);
});

test("a body with onReorder drags its rows and reports the seat one was dropped into", () => {
  const moved = vi.fn();
  const { container } = render(
    <Table>
      <TableBody onReorder={moved}>
        <TableRow>
          <TableCell>One</TableCell>
        </TableRow>
        <TableRow>
          <TableCell>Two</TableCell>
        </TableRow>
        <TableRow>
          <TableCell>Three</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
  const rows = [...container.querySelectorAll(".blk-table-row")];
  expect(rows.every((row) => row.getAttribute("draggable") === "true")).toBe(true);

  fireEvent.dragStart(rows[0]);
  expect(container.querySelectorAll(".blk-table-row")[0].getAttribute("data-dragging")).toBe("true");
  fireEvent.dragOver(rows[2]);
  dropAt(rows[2], 100);
  expect(moved).toHaveBeenCalledWith(0, 2);
  expect(container.querySelectorAll(".blk-table-row")[0].getAttribute("data-dragging")).toBeNull();

  fireEvent.dragStart(rows[2]);
  dropAt(rows[0], 0);
  expect(moved).toHaveBeenLastCalledWith(2, 0);
});

test("a column names the width it drops out below, and its cells carry the same value", () => {
  const { container } = render(
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Task</TableHead>
          <TableHead hideBelow={640}>Project</TableHead>
          <TableHead hideBelow={880}>Due</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell>Draft the migration plan</TableCell>
          <TableCell hideBelow={640}>Migration</TableCell>
          <TableCell hideBelow={880}>Sept 23</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
  const heads = [...container.querySelectorAll(".blk-table-head")].map((head) => head.getAttribute("data-hide-below"));
  const cells = [...container.querySelectorAll(".blk-table-cell")].map((cell) => cell.getAttribute("data-hide-below"));
  expect(heads).toEqual([null, "640", "880"]);
  expect(cells).toEqual([null, "640", "880"]);
  expect(container.querySelector(".blk-table-wrap")).not.toBeNull();
});

test("the interactive example selects every row, moves a row on, and drags one into a new seat", async () => {
  const user = userEvent.setup();
  const { container } = render(<TableStatesInteractive />);

  await user.click(screen.getByLabelText("Select Draft the migration plan"));
  expect((screen.getByLabelText("Select every task") as HTMLInputElement).indeterminate).toBe(true);
  await user.click(screen.getByLabelText("Select every task"));
  expect(container.querySelectorAll('.blk-table-row[data-selected="true"]')).toHaveLength(3);

  const circle = screen.getByRole("button", { name: "Move Rewrite the onboarding copy on" });
  expect(circle.getAttribute("aria-pressed")).toBe("false");
  await user.click(circle);
  expect(
    screen.getByRole("button", { name: "Move Rewrite the onboarding copy on" }).getAttribute("aria-pressed"),
  ).toBe("true");

  const rows = [...container.querySelectorAll(".blk-table-body .blk-table-row")];
  fireEvent.dragStart(rows[0]);
  dropAt(rows[2], 100);
  const titles = [...container.querySelectorAll(".blk-table-body .blk-table-row")].map(
    (row) => row.lastElementChild!.textContent,
  );
  expect(titles).toEqual([
    "Review the connector audit",
    "Rewrite the onboarding copy",
    "Draft the migration plan",
  ]);
});

test("the page carries the states section, so a reader finds the row states beside the shapes", () => {
  render(<TablePage />);
  expect(screen.getByRole("heading", { name: "States", level: 2 })).toBeTruthy();
});
