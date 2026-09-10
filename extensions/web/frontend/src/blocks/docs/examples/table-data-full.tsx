import { IconDots } from "@tabler/icons-react";

import { IconButton } from "@/blocks/action-bar";
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
import {
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from "@/blocks/menu";
import { Mark, ScoreDot } from "@/blocks/table";

type Lead = {
  score: number;
  company: string;
  category: string;
  funding: string;
  headcount: number;
};

const LEADS: Lead[] = [
  { score: 98, company: "Stripe", category: "Payments", funding: "$35B Series G", headcount: 8000 },
  { score: 96, company: "Airbnb", category: "Travel", funding: "$5.8B Series F", headcount: 6000 },
  { score: 92, company: "Figma", category: "Design", funding: "$20B Series E", headcount: 1300 },
  { score: 90, company: "Ramp", category: "Fintech", funding: "$8.1B Series D", headcount: 900 },
  { score: 88, company: "Linear", category: "Productivity", funding: "$400M Series B", headcount: 60 },
  { score: 84, company: "Discord", category: "Social", funding: "$15B Series H", headcount: 2500 },
  { score: 82, company: "Vercel", category: "Infrastructure", funding: "$3.3B Series E", headcount: 600 },
  { score: 78, company: "Notion", category: "Productivity", funding: "$10B Series C", headcount: 1000 },
  { score: 74, company: "Retool", category: "Developer", funding: "$3.2B Series C", headcount: 500 },
  { score: 71, company: "Deel", category: "Payroll", funding: "$12B Series D", headcount: 4000 },
  { score: 68, company: "Rippling", category: "Payroll", funding: "$13B Series F", headcount: 3000 },
  { score: 62, company: "Robinhood", category: "Finance", funding: "$11.7B Series G", headcount: 3500 },
  { score: 58, company: "Brex", category: "Fintech", funding: "$12B Series D", headcount: 1100 },
  { score: 54, company: "Airtable", category: "Productivity", funding: "$11B Series F", headcount: 800 },
  { score: 48, company: "Plaid", category: "Fintech", funding: "$5.3B Series D", headcount: 1200 },
  { score: 42, company: "Calendly", category: "Scheduling", funding: "$3B Series B", headcount: 400 },
  { score: 36, company: "Loom", category: "Video", funding: "$1.5B Series C", headcount: 250 },
  { score: 28, company: "Clubhouse", category: "Social", funding: "$4B Series C", headcount: 100 },
  { score: 22, company: "Bolt", category: "Payments", funding: "$11B Series E", headcount: 700 },
  { score: 12, company: "Zoom", category: "Video", funding: "$35B IPO", headcount: 6000 },
];

const HEADCOUNT = new Intl.NumberFormat("en-US");

const COLUMNS: Column<Lead>[] = [
  {
    id: "select",
    header: (context) => <DataTableSelectHeader table={context.table} />,
    cell: (row, table) => <DataTableSelectCell table={table} row={row} />,
    hideable: false,
    width: 32,
  },
  {
    id: "score",
    header: (context) => <DataTableColumnHeader {...context}>Fit Score</DataTableColumnHeader>,
    accessor: (row) => row.score,
    cell: (row) => <ScoreDot value={row.score} />,
    sortable: true,
    width: 88,
  },
  {
    id: "company",
    header: (context) => <DataTableColumnHeader {...context}>Company</DataTableColumnHeader>,
    accessor: (row) => row.company,
    cell: (row) => (
      <>
        <Mark />
        {row.company}
      </>
    ),
    sortable: true,
    filterable: true,
  },
  { id: "category", header: "Category", accessor: (row) => row.category },
  { id: "funding", header: "Funding", accessor: (row) => row.funding },
  {
    id: "headcount",
    header: (context) => <DataTableColumnHeader {...context}>Headcount</DataTableColumnHeader>,
    accessor: (row) => row.headcount,
    cell: (row) => HEADCOUNT.format(row.headcount),
    sortable: true,
    align: "right",
    width: 104,
  },
  {
    id: "actions",
    header: "",
    cell: (row) => (
      <Menu>
        <MenuTrigger asChild>
          <IconButton label={`Actions for ${row.company}`}>
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </MenuTrigger>
        <MenuContent>
          <MenuLabel>Actions</MenuLabel>
          <MenuItem>Copy ID</MenuItem>
          <MenuSeparator />
          <MenuItem>View details</MenuItem>
          <MenuItem destructive>Delete</MenuItem>
        </MenuContent>
      </Menu>
    ),
    hideable: false,
    width: 16,
  },
];

export function TableDataFull() {
  const table = useDataTable({
    data: LEADS,
    columns: COLUMNS,
    getRowId: (row) => row.company,
  });
  return (
    <div className="blk-data-frame">
      <DataTableToolbar table={table} filterPlaceholder="Filter companies" />
      <DataTable table={table} emptyText="No leads match this filter." />
      <DataTablePagination table={table} />
    </div>
  );
}
