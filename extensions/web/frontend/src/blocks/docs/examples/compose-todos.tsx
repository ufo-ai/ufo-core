import {
  IconChartBar,
  IconDots,
  IconLayoutKanban,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconUsers,
} from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
} from "@/blocks/action-bar";
import { Count, StatusIcon, Tag, type ItemStatus } from "@/blocks/item";
import { AvatarPair, Table, TableCell, TableMeta, TableRow, TableSection } from "@/blocks/table";
import "@/blocks/docs/compositions.css";

const PAIRS: [string, string][] = [
  ["linear-gradient(135deg, #ff9a3c, #ff3d8b)", "linear-gradient(135deg, #b14bff, #ff6700)"],
  ["linear-gradient(135deg, #ff6700, #ff2d78)", "linear-gradient(135deg, #8a5bff, #ff9a3c)"],
];

const GROUPS: { title: string; status: ItemStatus; rows: string[] }[] = [
  {
    title: "Ideas",
    status: "ready",
    rows: [
      "Rework the onboarding copy",
      "Audit the empty states",
      "Sketch the weekly digest",
      "Name the export formats",
      "Draft the pricing page",
      "Collect the support themes",
    ],
  },
  {
    title: "Up next",
    status: "started",
    rows: [
      "Ship the import flow",
      "Split the settings page",
      "Add keyboard navigation",
      "Cache the search index",
      "Write the migration notes",
    ],
  },
  {
    title: "In progress",
    status: "working",
    rows: [
      "Rebuild the table block",
      "Wire the search index",
      "Tune the composer",
      "Trim the bundle",
      "Land the audit trail",
    ],
  },
  {
    title: "Shipped",
    status: "done",
    rows: ["Dark theme tokens", "Row selection", "Section headers"],
  },
];

export default function TodosLane() {
  return (
    <div className="blk-lane">
      <ActionBar>
        <ActionBarTitle icon={<IconLayoutKanban size={16} stroke={1.5} />} menu>
          Todos
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="New todo">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Search todos">
            <IconSearch size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <Prompts>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize todos</Prompt>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Brainstorm ideas</Prompt>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Delegate to Assistant</Prompt>
      </Prompts>
      <div className="blk-lane-body">
        <Table>
          <colgroup>
            <col style={{ width: 32 }} />
            <col style={{ width: 32 }} />
            <col />
            <col style={{ width: 68 }} />
            <col style={{ width: 44 }} />
            <col style={{ width: 42 }} />
          </colgroup>
          {GROUPS.map((group) => (
            <TableSection
              key={group.title}
              title={group.title}
              count={group.rows.length}
              status={group.status}
              columns={6}
            >
              {group.rows.map((row, index) => (
                <TableRow key={row} state={group.status === "done" ? "done" : "default"}>
                  <TableCell muted>
                    <IconChartBar size={16} stroke={1.5} />
                  </TableCell>
                  <TableCell>
                    <StatusIcon status={group.status} />
                  </TableCell>
                  <TableCell>{row}</TableCell>
                  <TableCell>
                    <Tag>Project</Tag>
                  </TableCell>
                  <TableCell>
                    {index % 3 === 2 ? (
                      <Count icon={<IconUsers size={14} stroke={1.5} />}>2</Count>
                    ) : (
                      <AvatarPair colors={PAIRS[index % 2]} />
                    )}
                  </TableCell>
                  <TableCell align="right">
                    <TableMeta>Sept 23</TableMeta>
                  </TableCell>
                </TableRow>
              ))}
            </TableSection>
          ))}
        </Table>
      </div>
    </div>
  );
}
