import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import { TableDefault } from "@/blocks/docs/examples/table-default";
import defaultSource from "@/blocks/docs/examples/table-default.tsx?raw";
import { TableLeads } from "@/blocks/docs/examples/table-leads";
import leadsSource from "@/blocks/docs/examples/table-leads.tsx?raw";
import { TableSections } from "@/blocks/docs/examples/table-sections";
import sectionsSource from "@/blocks/docs/examples/table-sections.tsx?raw";
import { TableSortable } from "@/blocks/docs/examples/table-sortable";
import sortableSource from "@/blocks/docs/examples/table-sortable.tsx?raw";
import { TableSortedElsewhere } from "@/blocks/docs/examples/table-sorted-elsewhere";
import sortedElsewhereSource from "@/blocks/docs/examples/table-sorted-elsewhere.tsx?raw";
import { TableSelected } from "@/blocks/docs/examples/table-selected";
import selectedSource from "@/blocks/docs/examples/table-selected.tsx?raw";
import { TableDense } from "@/blocks/docs/examples/table-dense";
import denseSource from "@/blocks/docs/examples/table-dense.tsx?raw";
import { TableEmptyState } from "@/blocks/docs/examples/table-empty";
import emptySource from "@/blocks/docs/examples/table-empty.tsx?raw";
import { TableLoading } from "@/blocks/docs/examples/table-loading";
import loadingSource from "@/blocks/docs/examples/table-loading.tsx?raw";
import { TableFooterCaption } from "@/blocks/docs/examples/table-footer";
import footerSource from "@/blocks/docs/examples/table-footer.tsx?raw";
import { TableActions } from "@/blocks/docs/examples/table-actions";
import actionsSource from "@/blocks/docs/examples/table-actions.tsx?raw";
import { TableRtl } from "@/blocks/docs/examples/table-rtl";
import rtlSource from "@/blocks/docs/examples/table-rtl.tsx?raw";
import { TableData } from "@/blocks/docs/examples/table-data";
import dataSource from "@/blocks/docs/examples/table-data.tsx?raw";
import { TableDataSorting } from "@/blocks/docs/examples/table-data-sorting";
import dataSortingSource from "@/blocks/docs/examples/table-data-sorting.tsx?raw";
import { TableDataFiltering } from "@/blocks/docs/examples/table-data-filtering";
import dataFilteringSource from "@/blocks/docs/examples/table-data-filtering.tsx?raw";
import { TableDataVisibility } from "@/blocks/docs/examples/table-data-visibility";
import dataVisibilitySource from "@/blocks/docs/examples/table-data-visibility.tsx?raw";
import { TableDataSelection } from "@/blocks/docs/examples/table-data-selection";
import dataSelectionSource from "@/blocks/docs/examples/table-data-selection.tsx?raw";
import { TableDataPagination } from "@/blocks/docs/examples/table-data-pagination";
import dataPaginationSource from "@/blocks/docs/examples/table-data-pagination.tsx?raw";
import { TableDataFull } from "@/blocks/docs/examples/table-data-full";
import dataFullSource from "@/blocks/docs/examples/table-data-full.tsx?raw";
import { TableStates } from "@/blocks/docs/examples/table-states";
import statesSource from "@/blocks/docs/examples/table-states.tsx?raw";
import { TableStatesInteractive } from "@/blocks/docs/examples/table-states-interactive";
import statesInteractiveSource from "@/blocks/docs/examples/table-states-interactive.tsx?raw";

export function TablePage() {
  return (
    <DocPage
      title="Table"
      description="Rows and columns for dense data, with grouped sections, sorting affordances and empty and loading states."
    >
      <Example title="Default" code={defaultSource}>
        <TableDefault />
      </Example>
      <Example
        title="Leads"
        description="A fixed score column beside four columns that share the remaining width."
        code={leadsSource}
      >
        <TableLeads />
      </Example>
      <Example
        title="Grouped sections"
        description="Each section heading spans every column and folds its rows away."
        code={sectionsSource}
      >
        <TableSections />
      </Example>
      <Example title="Sortable header" code={sortableSource}>
        <TableSortable />
      </Example>
      <Example
        title="Sort driven elsewhere"
        description="A menu holds the sort key, and sorted alone marks the column: the arrow needs no onSort."
        code={sortedElsewhereSource}
      >
        <TableSortedElsewhere />
      </Example>
      <Example title="Selected row" code={selectedSource}>
        <TableSelected />
      </Example>
      <Example title="Dense" code={denseSource}>
        <TableDense />
      </Example>
      <Example title="Empty" code={emptySource}>
        <TableEmptyState />
      </Example>
      <Example title="Loading" code={loadingSource}>
        <TableLoading />
      </Example>
      <Example title="With footer and caption" code={footerSource}>
        <TableFooterCaption />
      </Example>
      <Example
        title="Actions"
        description="The last column holds a menu for the acts that belong to one row."
        code={actionsSource}
      >
        <TableActions />
      </Example>
      <Example
        title="RTL"
        description="Padding and alignment follow dir, so the table reads right to left inside a dir=rtl wrapper."
        code={rtlSource}
      >
        <TableRtl />
      </Example>

      <DocSection
        title="Data table"
        description="useDataTable sorts, filters, pages, hides columns and selects rows over an array; the components below draw what it returns."
      >
        <Example title="Data table" code={dataSource}>
          <TableData />
        </Example>
        <Example
          title="Sorting"
          description="A sortable column with a plain heading cycles ascending, descending and unsorted; DataTableColumnHeader gives the same column a menu."
          code={dataSortingSource}
        >
          <TableDataSorting />
        </Example>
        <Example
          title="Filtering"
          description="The toolbar's field narrows the rows against the first filterable column."
          code={dataFilteringSource}
        >
          <TableDataFiltering />
        </Example>
        <Example title="Column visibility" code={dataVisibilitySource}>
          <TableDataVisibility />
        </Example>
        <Example title="Row selection" code={dataSelectionSource}>
          <TableDataSelection />
        </Example>
        <Example title="Pagination" code={dataPaginationSource}>
          <TableDataPagination />
        </Example>
        <Example
          title="Full"
          description="Toolbar, column menus, selection, row actions and pagination over twenty leads."
          code={dataFullSource}
        >
          <TableDataFull />
        </Example>
      </DocSection>

      <DocSection
        title="States"
        description="The row carries the state; the app decides when it applies. A column names the width it drops out below."
      >
        <Example
          title="Every state"
          description="Selected, dragging, editing, done and disabled, beside the box and the circle each row reads."
          code={statesSource}
        >
          <TableStates />
        </Example>
        <Example
          title="Interactive states"
          description="The header box selects every row, the circle moves a row on, and a row is dragged into a new seat."
          code={statesInteractiveSource}
        >
          <TableStatesInteractive />
        </Example>
      </DocSection>

      <DocSection title="API">
        <h3>Table</h3>
        <PropsTable
          rows={[
            { name: "density", type: '"default" | "dense"', default: '"default"', description: "Row height: 34px, or 28px when dense. A header row is 32px." },
            { name: "children", type: "ReactNode", description: "Caption, colgroup, header, body, sections and footer." },
          ]}
        />
        <h3>TableRow</h3>
        <PropsTable
          rows={[
            { name: "selected", type: "boolean", description: "Fills the row and draws a 1.5px accent bar down its leading edge." },
            { name: "dragging", type: "boolean", description: "Fades the row to .6, fills it and lifts it on the float shadow while it is held." },
            { name: "editing", type: "boolean", description: "Fills the row and lets its text run past one line." },
            { name: "onClick", type: "() => void", description: "Makes the row clickable and reachable by keyboard." },
            { name: "state", type: '"default" | "done" | "disabled"', default: '"default"', description: "Done drops the row to secondary text; disabled dims it and stops input." },
          ]}
        />
        <h3>TableHead</h3>
        <PropsTable
          rows={[
            { name: "align", type: '"left" | "right"', default: '"left"', description: "Which edge the heading sits against; right is the trailing edge, so it follows dir." },
            { name: "width", type: "number | string", description: "The whole column box, 16px gap included; columns without one share the rest." },
            { name: "sortable", type: "boolean", description: "Publishes aria-sort, and renders the heading as a button when onSort is given." },
            {
              name: "sorted",
              type: '"asc" | "desc" | false',
              default: "false",
              description:
                "The current sort direction, drawn as an arrow and published as aria-sort. It marks the column whether or not the heading itself sorts.",
            },
            { name: "hideBelow", type: "640 | 880", description: "Drops the heading out below that table width. Its cells need the same value." },
            { name: "onSort", type: "() => void", description: "Called when the heading is pressed." },
          ]}
        />
        <h3>TableCell</h3>
        <PropsTable
          rows={[
            { name: "align", type: '"left" | "right"', default: '"left"', description: "Which edge the content sits against; right is the trailing edge, so it follows dir." },
            {
              name: "muted",
              type: 'boolean | "dim"',
              description: "Draws the cell in secondary text, or in half of it.",
            },
            { name: "colSpan", type: "number", description: "How many columns the cell covers." },
            { name: "hideBelow", type: "640 | 880", description: "Drops the cell out below that table width, matching its heading." },
          ]}
        />
        <h3>TableBody</h3>
        <PropsTable
          rows={[
            { name: "onReorder", type: "(from: number, to: number) => void", description: "Makes the rows draggable and reports the seat one was dropped into." },
            { name: "children", type: "ReactNode", description: "The data rows." },
          ]}
        />
        <h3>TableSection</h3>
        <PropsTable
          rows={[
            { name: "title", type: "string", description: "The heading of the group." },
            { name: "count", type: "number", description: "Shown as a tag beside the title." },
            { name: "status", type: '"ready" | "started" | "working" | "done"', description: "Picks and tints the circle icon before the title." },
            {
              name: "columns",
              type: "number",
              default: "1",
              description: "How many columns the heading covers. Count the ones hideBelow has dropped out of it.",
            },
            { name: "open", type: "boolean", description: "Controls the group; leave it out to let the section hold its own state." },
            { name: "defaultOpen", type: "boolean", default: "true", description: "Whether an uncontrolled group starts open." },
            { name: "onOpenChange", type: "(open: boolean) => void", description: "Called with the state the heading was pressed towards." },
          ]}
        />
        <h3>TableEmpty</h3>
        <PropsTable
          rows={[
            {
              name: "columns",
              type: "number",
              default: "1",
              description: "How many columns the row covers. Count the ones hideBelow has dropped out of it.",
            },
            { name: "children", type: "ReactNode", description: "The line shown in the 96px row." },
          ]}
        />
        <h3>TableSkeleton</h3>
        <PropsTable
          rows={[
            { name: "rows", type: "number", description: "How many placeholder rows to draw." },
            { name: "columns", type: "number", description: "How many placeholder cells each row holds." },
          ]}
        />
        <h3>ScoreDot</h3>
        <PropsTable
          rows={[
            {
              name: "value",
              type: "number",
              description: "The score out of 100; the dot holds the primary ink and fades with it, from 1 at 100 to 0.2 at 0.",
            },
          ]}
        />
        <h3>Mark, AvatarPair, TableTag, TableMeta</h3>
        <PropsTable
          rows={[
            { name: "Mark children", type: "ReactNode", description: "Optional glyph inside the 16px square." },
            { name: "AvatarPair colors", type: "[string, string]", description: "Backgrounds for the two overlapping 16px circles." },
            { name: "TableTag children", type: "ReactNode", description: "The label inside the 20px filled tag." },
            { name: "TableMeta children", type: "ReactNode", description: "Small dimmed text such as a date or a head count." },
          ]}
        />
      </DocSection>

      <DocSection title="Data table API">
        <h3>useDataTable options</h3>
        <PropsTable
          rows={[
            { name: "data", type: "Row[]", description: "Every row, before filtering, sorting and paging." },
            { name: "columns", type: "Column<Row>[]", description: "The columns, in the order they are drawn." },
            { name: "pageSize", type: "number", default: "10", description: "Rows on a page until setPageSize moves it." },
            { name: "getRowId", type: "(row: Row) => string", description: "The stable id a row is keyed and selected by." },
          ]}
        />
        <h3>Column</h3>
        <PropsTable
          rows={[
            { name: "id", type: "string", description: "The column's key, and the label its checkbox takes in the column menu." },
            { name: "header", type: "ReactNode | ((context) => ReactNode)", description: "The heading. A function is called with { column, table } and owns its own control." },
            { name: "accessor", type: "(row: Row) => string | number", description: "The value the column sorts and filters on." },
            { name: "cell", type: "(row: Row, table) => ReactNode", description: "What the cell draws; without it the cell draws the accessor's value." },
            { name: "sortable", type: "boolean", description: "Lets the column be sorted, and publishes aria-sort on its heading." },
            { name: "filterable", type: "boolean", description: "Marks the column the toolbar's field filters on; the first one is the default." },
            { name: "align", type: '"left" | "right"', default: '"left"', description: "Which edge the heading and cells sit against." },
            { name: "width", type: "number | string", description: "The whole column box, 16px gap included." },
            { name: "hideable", type: "boolean", default: "true", description: "Whether the column menu can hide the column." },
            { name: "enableHiding", type: "boolean", default: "true", description: "Read with hideable: either one false keeps the column out of the column menu." },
          ]}
        />
        <h3>useDataTable return</h3>
        <PropsTable
          rows={[
            { name: "columns, visibleColumns", type: "Column<Row>[]", description: "Every column, and the ones that are not hidden." },
            { name: "rows", type: "Row[]", description: "The current page, filtered and sorted." },
            { name: "allRows", type: "number", description: "How many rows survive the filter." },
            { name: "getRowId", type: "(row: Row) => string", description: "The id function the hook was given." },
            { name: "page, pageCount, setPage", type: "number, number, (page: number) => void", description: "The page, counted from 1; setPage clamps to the bounds." },
            { name: "pageSize, setPageSize", type: "number, (size: number) => void", description: "Rows per page; a change returns to page 1." },
            { name: "sort, setSort", type: "{ id, dir } | null, (sort) => void", description: "The sorted column and its direction." },
            { name: "toggleSort", type: "(id: string) => void", description: "Cycles that column ascending, descending, unsorted." },
            { name: "filter, setFilter", type: "string, (filter: string) => void", description: "The filter text; a change returns to page 1." },
            { name: "filterColumn, setFilterColumn", type: "string, (id: string) => void", description: "Which column the filter reads; empty means every column with an accessor." },
            { name: "columnVisibility, setColumnVisible", type: "Record<string, boolean>, (id, visible) => void", description: "Whether each column is drawn." },
            { name: "selected, toggleRow, toggleAll", type: "Set<string>, (id) => void, () => void", description: "The selected ids; toggleAll covers the current page." },
            { name: "allSelected, someSelected", type: "boolean", description: "Whether the current page is wholly or partly selected." },
            { name: "selectedCount", type: "number", description: "How many filtered rows are selected." },
          ]}
        />
        <h3>DataTable</h3>
        <PropsTable
          rows={[
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned." },
            { name: "emptyText", type: "string", description: "The line drawn across every column when the page holds no rows." },
          ]}
        />
        <h3>DataTableColumnHeader</h3>
        <PropsTable
          rows={[
            { name: "column", type: "Column<Row>", description: "The column the heading belongs to." },
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned." },
            { name: "children", type: "ReactNode", description: "The label beside the sort arrow." },
          ]}
        />
        <h3>DataTableToolbar</h3>
        <PropsTable
          rows={[
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned." },
            { name: "filterPlaceholder", type: "string", default: '"Filter rows"', description: "The placeholder in the filter field." },
            { name: "children", type: "ReactNode", description: "Controls placed before the column menu on the right." },
          ]}
        />
        <h3>DataTableViewOptions</h3>
        <PropsTable
          rows={[
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned; every hideable column gets a row in the menu." },
          ]}
        />
        <h3>DataTablePagination</h3>
        <PropsTable
          rows={[
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned." },
            { name: "pageSizes", type: "number[]", default: "[10, 20, 50]", description: "The choices in the rows-per-page select." },
          ]}
        />
        <h3>DataTableSelectHeader, DataTableSelectCell</h3>
        <PropsTable
          rows={[
            { name: "table", type: "DataTableApi<Row>", description: "What useDataTable returned." },
            { name: "row", type: "Row", description: "DataTableSelectCell only: the row the checkbox selects." },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
