import { useMemo, useState, type ReactNode } from "react";
import {
  IconAdjustmentsHorizontal,
  IconArrowDown,
  IconArrowUp,
  IconArrowsSort,
  IconChevronLeft,
  IconChevronRight,
  IconChevronsLeft,
  IconChevronsRight,
} from "@tabler/icons-react";

import { IconButton, SearchField } from "@/blocks/action-bar";
import { Checkbox } from "@/blocks/checkbox";
import {
  Menu,
  MenuCheckboxItem,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuTrigger,
} from "@/blocks/menu";
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/blocks/table";

import "@/blocks/data-table.css";

const FIRST_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;
const PAGE_SIZES = [10, 20, 50];
const SORT_ICON_SIZE = 12;
const ICON_SIZE = 16;
const ICON_STROKE = 1.5;

export type SortDirection = "asc" | "desc";
export type Sort = { id: string; dir: SortDirection };
export type HeaderContext<Row> = { column: Column<Row>; table: DataTableApi<Row> };

export type Column<Row> = {
  id: string;
  header: ReactNode | ((context: HeaderContext<Row>) => ReactNode);
  accessor?: (row: Row) => string | number;
  cell?: (row: Row, table: DataTableApi<Row>) => ReactNode;
  sortable?: boolean;
  filterable?: boolean;
  align?: "left" | "right";
  width?: number | string;
  hideable?: boolean;
  enableHiding?: boolean;
};

export type DataTableApi<Row> = {
  columns: Column<Row>[];
  rows: Row[];
  allRows: number;
  getRowId: (row: Row) => string;
  page: number;
  pageCount: number;
  setPage: (page: number) => void;
  pageSize: number;
  setPageSize: (size: number) => void;
  sort: Sort | null;
  setSort: (sort: Sort | null) => void;
  toggleSort: (id: string) => void;
  filter: string;
  setFilter: (filter: string) => void;
  filterColumn: string;
  setFilterColumn: (id: string) => void;
  visibleColumns: Column<Row>[];
  columnVisibility: Record<string, boolean>;
  setColumnVisible: (id: string, visible: boolean) => void;
  selected: Set<string>;
  toggleRow: (id: string) => void;
  toggleAll: () => void;
  allSelected: boolean;
  someSelected: boolean;
  selectedCount: number;
};

function hideable<Row>(column: Column<Row>): boolean {
  return (column.hideable ?? true) && (column.enableHiding ?? true);
}

/** Sorting, filtering, paging, column visibility and row selection over an array, with no rendering of its own. */
export function useDataTable<Row>({
  data,
  columns,
  pageSize: initialPageSize = DEFAULT_PAGE_SIZE,
  getRowId,
}: {
  data: Row[];
  columns: Column<Row>[];
  pageSize?: number;
  getRowId: (row: Row) => string;
}): DataTableApi<Row> {
  const [page, setPageState] = useState(FIRST_PAGE);
  const [pageSize, setPageSizeState] = useState(initialPageSize);
  const [sort, setSort] = useState<Sort | null>(null);
  const [filter, setFilterState] = useState("");
  const [filterColumn, setFilterColumnState] = useState(
    () => columns.find((column) => column.filterable)?.id ?? "",
  );
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  const matched = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return data;
    const searched = columns.filter(
      (column) => column.accessor && (filterColumn ? column.id === filterColumn : true),
    );
    return data.filter((row) =>
      searched.some((column) => String(column.accessor?.(row)).toLowerCase().includes(needle)),
    );
  }, [data, columns, filter, filterColumn]);

  const ordered = useMemo(() => {
    const read = sort && columns.find((column) => column.id === sort.id)?.accessor;
    if (!sort || !read) return matched;
    const way = sort.dir === "asc" ? 1 : -1;
    return [...matched].sort((left, right) => {
      const a = read(left);
      const b = read(right);
      if (typeof a === "number" && typeof b === "number") return (a - b) * way;
      return String(a).localeCompare(String(b)) * way;
    });
  }, [matched, columns, sort]);

  const pageCount = Math.max(1, Math.ceil(ordered.length / pageSize));
  const current = Math.min(page, pageCount);
  const rows = useMemo(
    () => ordered.slice((current - 1) * pageSize, current * pageSize),
    [ordered, current, pageSize],
  );

  const pageIds = rows.map(getRowId);
  const allSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const someSelected = !allSelected && pageIds.some((id) => selected.has(id));

  return {
    columns,
    rows,
    allRows: ordered.length,
    getRowId,
    page: current,
    pageCount,
    setPage: (next) => setPageState(Math.min(Math.max(next, FIRST_PAGE), pageCount)),
    pageSize,
    setPageSize: (size) => {
      setPageSizeState(size);
      setPageState(FIRST_PAGE);
    },
    sort,
    setSort,
    toggleSort: (id) =>
      setSort((held) => {
        if (!held || held.id !== id) return { id, dir: "asc" };
        return held.dir === "asc" ? { id, dir: "desc" } : null;
      }),
    filter,
    setFilter: (next) => {
      setFilterState(next);
      setPageState(FIRST_PAGE);
    },
    filterColumn,
    setFilterColumn: (id) => {
      setFilterColumnState(id);
      setPageState(FIRST_PAGE);
    },
    visibleColumns: columns.filter((column) => hidden[column.id] !== true),
    columnVisibility: Object.fromEntries(
      columns.map((column) => [column.id, hidden[column.id] !== true]),
    ),
    setColumnVisible: (id, visible) => setHidden((held) => ({ ...held, [id]: !visible })),
    selected,
    toggleRow: (id) =>
      setSelected((held) => {
        const next = new Set(held);
        if (!next.delete(id)) next.add(id);
        return next;
      }),
    toggleAll: () =>
      setSelected((held) => {
        const next = new Set(held);
        if (allSelected) pageIds.forEach((id) => next.delete(id));
        else pageIds.forEach((id) => next.add(id));
        return next;
      }),
    allSelected,
    someSelected,
    selectedCount: ordered.filter((row) => selected.has(getRowId(row))).length,
  };
}

/** A column heading that carries the sort direction and opens a menu to sort or hide the column. */
export function DataTableColumnHeader<Row>({
  column,
  table,
  children,
}: {
  column: Column<Row>;
  table: DataTableApi<Row>;
  children: ReactNode;
}) {
  const dir = table.sort?.id === column.id ? table.sort.dir : null;
  const Arrow = dir === "asc" ? IconArrowUp : dir === "desc" ? IconArrowDown : IconArrowsSort;
  return (
    <Menu>
      <MenuTrigger asChild>
        <button type="button" className="blk-data-header" data-sorted={dir ?? undefined}>
          <span className="blk-data-header-label">{children}</span>
          <Arrow size={SORT_ICON_SIZE} stroke={ICON_STROKE} />
        </button>
      </MenuTrigger>
      <MenuContent align="start">
        <MenuItem onSelect={() => table.setSort({ id: column.id, dir: "asc" })}>Asc</MenuItem>
        <MenuItem onSelect={() => table.setSort({ id: column.id, dir: "desc" })}>Desc</MenuItem>
        <MenuItem
          disabled={!hideable(column)}
          onSelect={() => table.setColumnVisible(column.id, false)}
        >
          Hide
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}

/** The menu that shows and hides columns. */
export function DataTableViewOptions<Row>({ table }: { table: DataTableApi<Row> }) {
  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton label="Columns">
          <IconAdjustmentsHorizontal size={ICON_SIZE} stroke={ICON_STROKE} />
        </IconButton>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>Columns</MenuLabel>
        {table.columns.filter(hideable).map((column) => (
          <MenuCheckboxItem
            key={column.id}
            checked={table.columnVisibility[column.id]}
            onCheckedChange={(next) => table.setColumnVisible(column.id, next)}
          >
            {column.id}
          </MenuCheckboxItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

/** The row above a data table: the filter field on the left, actions and the column menu on the right. */
export function DataTableToolbar<Row>({
  table,
  filterPlaceholder = "Filter rows",
  children,
}: {
  table: DataTableApi<Row>;
  filterPlaceholder?: string;
  children?: ReactNode;
}) {
  return (
    <div className="blk-data-toolbar">
      <SearchField placeholder={filterPlaceholder} value={table.filter} onChange={table.setFilter} />
      <div className="blk-data-toolbar-actions">
        {children}
        <DataTableViewOptions table={table} />
      </div>
    </div>
  );
}

/** The row below a data table: the selection count on the left, page size and page steps on the right. */
export function DataTablePagination<Row>({
  table,
  pageSizes = PAGE_SIZES,
}: {
  table: DataTableApi<Row>;
  pageSizes?: number[];
}) {
  const first = table.page === FIRST_PAGE;
  const last = table.page === table.pageCount;
  return (
    <div className="blk-data-pagination">
      <span className="blk-data-pagination-count">
        {table.selectedCount} of {table.allRows} row(s) selected.
      </span>
      <div className="blk-data-pagination-controls">
        <label className="blk-data-page-size">
          Rows per page
          <select
            className="blk-select"
            value={table.pageSize}
            onChange={(event) => table.setPageSize(Number(event.target.value))}
          >
            {pageSizes.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
        <span className="blk-data-page">
          Page {table.page} of {table.pageCount}
        </span>
        <div className="blk-data-pagination-steps">
          <IconButton label="First page" disabled={first} onClick={() => table.setPage(FIRST_PAGE)}>
            <IconChevronsLeft size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
          <IconButton
            label="Previous page"
            disabled={first}
            onClick={() => table.setPage(table.page - 1)}
          >
            <IconChevronLeft size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
          <IconButton label="Next page" disabled={last} onClick={() => table.setPage(table.page + 1)}>
            <IconChevronRight size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
          <IconButton
            label="Last page"
            disabled={last}
            onClick={() => table.setPage(table.pageCount)}
          >
            <IconChevronsRight size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
        </div>
      </div>
    </div>
  );
}

/** The select-all checkbox for a column of row checkboxes. */
export function DataTableSelectHeader<Row>({ table }: { table: DataTableApi<Row> }) {
  return (
    <Checkbox
      label="Select all rows"
      checked={table.allSelected}
      indeterminate={table.someSelected}
      onCheckedChange={table.toggleAll}
    />
  );
}

/** The checkbox that selects one row. */
export function DataTableSelectCell<Row>({ table, row }: { table: DataTableApi<Row>; row: Row }) {
  const id = table.getRowId(row);
  return (
    <Checkbox
      label="Select row"
      checked={table.selected.has(id)}
      onCheckedChange={() => table.toggleRow(id)}
    />
  );
}

/** The table a useDataTable describes: its visible columns, its page of rows, and a line where it has none. */
export function DataTable<Row>({
  table,
  emptyText,
}: {
  table: DataTableApi<Row>;
  emptyText: string;
}) {
  const columns = table.visibleColumns;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column) => {
            const rendered =
              typeof column.header === "function" ? column.header({ column, table }) : column.header;
            return (
              <TableHead
                key={column.id}
                align={column.align}
                width={column.width}
                sortable={column.sortable}
                sorted={table.sort?.id === column.id ? table.sort.dir : false}
                onSort={
                  column.sortable && typeof column.header !== "function"
                    ? () => table.toggleSort(column.id)
                    : undefined
                }
              >
                {rendered}
              </TableHead>
            );
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {table.rows.length === 0 ? (
          <TableEmpty columns={columns.length}>{emptyText}</TableEmpty>
        ) : (
          table.rows.map((row) => {
            const id = table.getRowId(row);
            return (
              <TableRow key={id} selected={table.selected.has(id) || undefined}>
                {columns.map((column) => (
                  <TableCell key={column.id} align={column.align}>
                    {column.cell ? column.cell(row, table) : column.accessor?.(row)}
                  </TableCell>
                ))}
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
