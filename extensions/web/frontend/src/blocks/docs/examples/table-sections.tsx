import {
  IconChartBar,
  IconCircle,
  IconCircleDotted,
  IconCircleFilled,
  IconCircleHalf,
  IconUsers,
} from "@tabler/icons-react";

import {
  AvatarPair,
  Table,
  TableCell,
  TableMeta,
  TableRow,
  TableSection,
  TableTag,
} from "@/blocks/table";

const STATUS = {
  ready: { Icon: IconCircleDotted, color: "var(--blk-text-2)" },
  started: { Icon: IconCircle, color: "var(--blk-text-2)" },
  working: { Icon: IconCircleHalf, color: "var(--blk-secondary)" },
  done: { Icon: IconCircleFilled, color: "var(--blk-primary)" },
} as const;

const PAIRS: [string, string][] = [
  ["linear-gradient(135deg, #ff9a3c, #ff3d8b)", "linear-gradient(135deg, #b14bff, #ff6700)"],
  ["linear-gradient(135deg, #ff6700, #ff2d78)", "linear-gradient(135deg, #8a5bff, #ff9a3c)"],
];

const GROUPS = [
  {
    title: "Ideas",
    status: "ready",
    rows: ["Rework the onboarding copy", "Audit the empty states", "Sketch the weekly digest"],
  },
  {
    title: "Up next",
    status: "started",
    rows: ["Ship the import flow", "Split the settings page", "Add keyboard navigation"],
  },
  {
    title: "In progress",
    status: "working",
    rows: ["Rebuild the table block", "Wire the search index", "Tune the composer"],
  },
  {
    title: "Shipped",
    status: "done",
    rows: ["Dark theme tokens", "Row selection", "Section headers"],
  },
] as const;

export function TableSections() {
  return (
    <Table>
      <colgroup>
        <col style={{ width: 32 }} />
        <col style={{ width: 32 }} />
        <col />
        <col style={{ width: 64 }} />
        <col style={{ width: 40 }} />
        <col style={{ width: 48 }} />
      </colgroup>
      {GROUPS.map((group) => {
        const { Icon, color } = STATUS[group.status];
        return (
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
                  <Icon size={16} stroke={1.5} style={{ color }} />
                </TableCell>
                <TableCell>{row}</TableCell>
                <TableCell>
                  <TableTag>Project</TableTag>
                </TableCell>
                <TableCell>
                  {index === 2 ? (
                    <TableMeta>
                      <IconUsers size={14} stroke={1.5} />2
                    </TableMeta>
                  ) : (
                    <AvatarPair colors={PAIRS[index]} />
                  )}
                </TableCell>
                <TableCell align="right">
                  <TableMeta>Sept 23</TableMeta>
                </TableCell>
              </TableRow>
            ))}
          </TableSection>
        );
      })}
    </Table>
  );
}
