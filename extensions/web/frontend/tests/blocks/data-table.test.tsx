import { act, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import {
  DataTable,
  DataTableColumnHeader,
  DataTablePagination,
  DataTableSelectCell,
  DataTableSelectHeader,
  DataTableToolbar,
  useDataTable,
  type Column,
} from "@/blocks/data-table";

type Row = { id: string; name: string; size: number };

const ROWS: Row[] = Array.from({ length: 24 }, (_, index) => ({
  id: `row-${index + 1}`,
  name: `Row ${String(index + 1).padStart(2, "0")}`,
  size: ((index * 7) % 24) + 1,
}));

const COLUMNS: Column<Row>[] = [
  {
    id: "select",
    header: (context) => <DataTableSelectHeader table={context.table} />,
    cell: (row, table) => <DataTableSelectCell table={table} row={row} />,
    hideable: false,
    width: 32,
  },
  { id: "name", header: "Name", accessor: (row) => row.name, sortable: true, filterable: true },
  {
    id: "size",
    header: (context) => <DataTableColumnHeader {...context}>Size</DataTableColumnHeader>,
    accessor: (row) => row.size,
    sortable: true,
    align: "right",
    width: 96,
  },
];

function table() {
  return renderHook(() => useDataTable({ data: ROWS, columns: COLUMNS, getRowId: (row) => row.id }));
}

function Harness() {
  const api = useDataTable({ data: ROWS, columns: COLUMNS, getRowId: (row) => row.id });
  return (
    <div className="blk-data-frame">
      <DataTableToolbar table={api} filterPlaceholder="Filter names" />
      <DataTable table={api} emptyText="No results." />
      <DataTablePagination table={api} />
    </div>
  );
}

test("toggling a column's sort cycles ascending, descending and unsorted", () => {
  const { result } = table();
  expect(result.current.sort).toBeNull();
  expect(result.current.rows[0].name).toBe("Row 01");

  act(() => result.current.toggleSort("size"));
  expect(result.current.sort).toEqual({ id: "size", dir: "asc" });
  expect(result.current.rows[0].size).toBe(1);

  act(() => result.current.toggleSort("size"));
  expect(result.current.sort).toEqual({ id: "size", dir: "desc" });
  expect(result.current.rows[0].size).toBe(24);

  act(() => result.current.toggleSort("size"));
  expect(result.current.sort).toBeNull();
  expect(result.current.rows[0].name).toBe("Row 01");

  act(() => result.current.toggleSort("name"));
  act(() => result.current.toggleSort("size"));
  expect(result.current.sort).toEqual({ id: "size", dir: "asc" });
});

test("a filter narrows the rows to the filterable column and returns to the first page", () => {
  const { result } = table();
  expect(result.current.allRows).toBe(24);

  act(() => result.current.setPage(3));
  expect(result.current.page).toBe(3);

  act(() => result.current.setFilter("row 1"));
  expect(result.current.page).toBe(1);
  expect(result.current.allRows).toBe(10);
  expect(result.current.rows.map((row) => row.name)).toContain("Row 10");
  expect(result.current.rows.map((row) => row.name)).not.toContain("Row 02");

  act(() => result.current.setFilter("nothing here"));
  expect(result.current.allRows).toBe(0);
  expect(result.current.rows).toEqual([]);
  expect(result.current.pageCount).toBe(1);
});

test("paging clamps to its bounds and a new page size returns to the first page", () => {
  const { result } = table();
  expect(result.current.pageCount).toBe(3);
  expect(result.current.rows).toHaveLength(10);

  act(() => result.current.setPage(0));
  expect(result.current.page).toBe(1);

  act(() => result.current.setPage(99));
  expect(result.current.page).toBe(3);
  expect(result.current.rows).toHaveLength(4);

  act(() => result.current.setPageSize(20));
  expect(result.current.page).toBe(1);
  expect(result.current.pageCount).toBe(2);
  expect(result.current.rows).toHaveLength(20);
});

test("selection covers the current page and reports what is partly selected", () => {
  const { result } = table();
  expect(result.current.allSelected).toBe(false);
  expect(result.current.someSelected).toBe(false);

  act(() => result.current.toggleAll());
  expect(result.current.allSelected).toBe(true);
  expect(result.current.someSelected).toBe(false);
  expect(result.current.selectedCount).toBe(10);

  act(() => result.current.toggleRow("row-1"));
  expect(result.current.allSelected).toBe(false);
  expect(result.current.someSelected).toBe(true);
  expect(result.current.selectedCount).toBe(9);

  act(() => result.current.toggleAll());
  expect(result.current.allSelected).toBe(true);
  expect(result.current.selectedCount).toBe(10);

  act(() => result.current.toggleAll());
  expect(result.current.allSelected).toBe(false);
  expect(result.current.someSelected).toBe(false);
  expect(result.current.selected.size).toBe(0);
});

test("hiding a column drops it from the visible set and says so", () => {
  const { result } = table();
  expect(result.current.visibleColumns.map((column) => column.id)).toEqual([
    "select",
    "name",
    "size",
  ]);
  expect(result.current.columnVisibility).toEqual({ select: true, name: true, size: true });

  act(() => result.current.setColumnVisible("size", false));
  expect(result.current.visibleColumns.map((column) => column.id)).toEqual(["select", "name"]);
  expect(result.current.columnVisibility.size).toBe(false);

  act(() => result.current.setColumnVisible("size", true));
  expect(result.current.visibleColumns).toHaveLength(3);
});

test("a sortable heading cycles the sort it publishes", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const heading = () => screen.getByRole("columnheader", { name: "Name" });
  expect(heading().getAttribute("aria-sort")).toBe("none");

  await user.click(screen.getByRole("button", { name: "Name" }));
  expect(heading().getAttribute("aria-sort")).toBe("ascending");

  await user.click(screen.getByRole("button", { name: "Name" }));
  expect(heading().getAttribute("aria-sort")).toBe("descending");

  await user.click(screen.getByRole("button", { name: "Name" }));
  expect(heading().getAttribute("aria-sort")).toBe("none");
});

test("a column header menu sorts and hides its column", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole("button", { name: "Size" }));
  await user.click(screen.getByRole("menuitem", { name: "Desc" }));
  expect(screen.getByRole("columnheader", { name: "Size" }).getAttribute("aria-sort")).toBe(
    "descending",
  );

  await user.click(screen.getByRole("button", { name: "Size" }));
  await user.click(screen.getByRole("menuitem", { name: "Hide" }));
  expect(screen.queryByRole("columnheader", { name: "Size" })).toBeNull();
});

test("the toolbar's field narrows the rows and the empty line stands in when none match", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  expect(screen.getAllByRole("row")).toHaveLength(11);

  await user.type(screen.getByLabelText("Filter names"), "Row 03");
  expect(screen.getByText("Row 03")).toBeTruthy();
  expect(screen.queryByText("Row 01")).toBeNull();

  await user.clear(screen.getByLabelText("Filter names"));
  await user.type(screen.getByLabelText("Filter names"), "nothing here");
  expect(screen.getByText("No results.")).toBeTruthy();
});

test("the view options menu hides a column's heading", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole("button", { name: "Columns" }));
  expect(screen.queryByRole("menuitemcheckbox", { name: "select" })).toBeNull();

  await user.click(screen.getByRole("menuitemcheckbox", { name: "name" }));
  expect(screen.queryByRole("columnheader", { name: "Name" })).toBeNull();
  expect(screen.getByRole("columnheader", { name: "Size" })).toBeTruthy();
});

test("the select-all header checks the page and goes indeterminate when one row is unchecked", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const all = screen.getByLabelText<HTMLInputElement>("Select all rows");
  expect(all.checked).toBe(false);
  expect(screen.getByText("0 of 24 row(s) selected.")).toBeTruthy();

  await user.click(all);
  expect(all.checked).toBe(true);
  expect(all.indeterminate).toBe(false);
  expect(screen.getByText("10 of 24 row(s) selected.")).toBeTruthy();

  await user.click(screen.getAllByLabelText("Select row")[0]);
  expect(all.checked).toBe(false);
  expect(all.indeterminate).toBe(true);
  expect(screen.getByText("9 of 24 row(s) selected.")).toBeTruthy();
});

test("the page steps disable at the bounds and a new page size returns to the first page", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const step = (name: string) => screen.getByRole("button", { name });
  expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  expect(step("First page").hasAttribute("disabled")).toBe(true);
  expect(step("Previous page").hasAttribute("disabled")).toBe(true);
  expect(step("Next page").hasAttribute("disabled")).toBe(false);

  await user.click(step("Last page"));
  expect(screen.getByText("Page 3 of 3")).toBeTruthy();
  expect(step("Next page").hasAttribute("disabled")).toBe(true);
  expect(step("Last page").hasAttribute("disabled")).toBe(true);

  await user.click(step("Previous page"));
  expect(screen.getByText("Page 2 of 3")).toBeTruthy();

  await user.selectOptions(screen.getByLabelText("Rows per page"), "20");
  expect(screen.getByText("Page 1 of 2")).toBeTruthy();
  expect(step("Previous page").hasAttribute("disabled")).toBe(true);
});
