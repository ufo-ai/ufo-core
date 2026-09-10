import { useRef, useState } from "react";
import { IconChecklist, IconDots, IconPlus, IconSearch, IconUsers } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  Composer,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import { Count, ItemTitle, StatusIcon, Tag, type ItemStatus } from "@/blocks/item";
import {
  Menu,
  MenuButton,
  MenuCheckboxItem,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from "@/blocks/menu";
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableMeta,
  TableRow,
  TableSection,
} from "@/blocks/table";

type Task = {
  id: string;
  title: string;
  status: string;
  project?: string;
  assignees?: number;
  due?: string;
};

type Data = { tasks: Task[] };

type SortKey = "title" | "project" | "assignees" | "due";

const APP_NAME = "Tasks";
const COLUMNS = 5;
const TAG_CHIPS = 3;
const RING: ItemStatus[] = ["ready", "started", "working", "done"];
const SORTS: { key: SortKey; label: string }[] = [
  { key: "title", label: "Task" },
  { key: "project", label: "Project" },
  { key: "assignees", label: "People" },
  { key: "due", label: "Due" },
];

const label = (value: string) => value.slice(0, 1).toUpperCase() + value.slice(1);

const ring = (at: number, of: number): ItemStatus =>
  at === of - 1 ? "done" : RING[Math.min(at, RING.length - 2)];

function compare(one: Task, other: Task, key: SortKey): number {
  const left = one[key];
  const right = other[key];
  if (left === undefined) return right === undefined ? 0 : 1;
  if (right === undefined) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right));
}

export default function App({ data }: { data: Data }) {
  const [moved, setMoved] = useState<Record<string, string>>({});
  const [named, setNamed] = useState<Record<string, string>>({});
  const [gone, setGone] = useState<string[]>([]);
  const [extra, setExtra] = useState<Task[]>([]);
  const [seats, setSeats] = useState<string[]>([]);
  const [sort, setSort] = useState<SortKey | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [query, setQuery] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [folded, setFolded] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const made = useRef(0);
  const asked = useRef<Task | null>(null);

  const records = data.tasks;
  const statuses = [...new Set(records.map((task) => task.status))];
  const tags = [
    ...new Set(records.flatMap((task) => (task.project === undefined ? [] : [task.project]))),
  ];

  const held = [...extra, ...records]
    .filter((task) => !gone.includes(task.id))
    .map((task) => ({
      ...task,
      status: moved[task.id] ?? task.status,
      title: named[task.id] ?? task.title,
    }));
  const rank = new Map(seats.map((id, at) => [id, at]));
  const arranged =
    seats.length === 0
      ? held
      : [...held].sort((one, other) => (rank.get(one.id) ?? -1) - (rank.get(other.id) ?? -1));
  const text = (query ?? "").trim().toLowerCase();
  const shown = arranged.filter(
    (task) =>
      (tag === null || task.project === tag) &&
      (text === "" || task.title.toLowerCase().includes(text)),
  );
  const groups = statuses
    .filter((status) => !hidden.includes(status))
    .map((status) => {
      const rows = shown.filter((task) => task.status === status);
      return {
        status,
        rows: sort === null ? rows : [...rows].sort((one, other) => compare(one, other, sort)),
      };
    });
  const drawn = groups.reduce((sum, group) => sum + group.rows.length, 0);

  const advance = (task: Task) => {
    const at = statuses.indexOf(task.status);
    setMoved({ ...moved, [task.id]: statuses[(at + 1) % statuses.length] });
  };

  const swap = (task: Task, step: number) => {
    const group = groups.find((one) => one.status === task.status);
    if (!group) return;
    const mate = group.rows[group.rows.findIndex((row) => row.id === task.id) + step];
    if (!mate) return;
    const ids = arranged.map((row) => row.id);
    const from = ids.indexOf(task.id);
    const to = ids.indexOf(mate.id);
    ids[from] = mate.id;
    ids[to] = task.id;
    setSeats(ids);
  };

  const rename = () => {
    if (editing === null) return;
    const next = editing.text.trim();
    if (next !== "") setNamed({ ...named, [editing.id]: next });
    setEditing(null);
  };

  const append = (value: string) => {
    const next = value.trim();
    if (next === "" || statuses.length === 0) return;
    made.current += 1;
    setExtra([{ id: `added-${made.current}`, title: next, status: statuses[0] }, ...extra]);
    setDraft("");
  };

  return (
    <>
      <ActionBar variant="header">
        <ActionBarTitle
          icon={<IconChecklist size={16} stroke={1.5} />}
          menu={
            <MenuContent align="start">
              <MenuLabel>Sort by</MenuLabel>
              {SORTS.map((option) => (
                <MenuCheckboxItem
                  key={option.key}
                  checked={sort === option.key}
                  onCheckedChange={(on) => setSort(on ? option.key : null)}
                >
                  {option.label}
                </MenuCheckboxItem>
              ))}
            </MenuContent>
          }
        >
          {APP_NAME}
        </ActionBarTitle>
        <ActionBarActions>
          {query === null ? null : (
            <SearchField
              placeholder="Search tasks"
              value={query}
              onChange={setQuery}
              onClear={() => setQuery("")}
            />
          )}
          <IconButton
            label="Add a task"
            disabled={statuses.length === 0}
            pressed={draft !== null}
            onClick={() => setDraft(draft === null ? "" : null)}
          >
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
          <IconButton
            label="Search tasks"
            pressed={query !== null}
            onClick={() => setQuery(query === null ? "" : null)}
          >
            <IconSearch size={16} stroke={1.5} />
          </IconButton>
          {statuses.length === 0 ? null : (
            <Menu>
              <MenuTrigger asChild>
                <IconButton label="Groups shown">
                  <IconDots size={16} stroke={1.5} />
                </IconButton>
              </MenuTrigger>
              <MenuContent>
                <MenuLabel>Groups shown</MenuLabel>
                {statuses.map((status) => (
                  <MenuCheckboxItem
                    key={status}
                    checked={!hidden.includes(status)}
                    onCheckedChange={(on) =>
                      setHidden(
                        on ? hidden.filter((one) => one !== status) : [...hidden, status],
                      )
                    }
                  >
                    {label(status)}
                  </MenuCheckboxItem>
                ))}
              </MenuContent>
            </Menu>
          )}
        </ActionBarActions>
      </ActionBar>

      {tags.length === 0 ? null : (
        <Prompts wrap>
          {tags.slice(0, TAG_CHIPS).map((value) => (
            <Prompt
              key={value}
              active={tag === value}
              onClick={() => setTag(tag === value ? null : value)}
            >
              {value}
            </Prompt>
          ))}
          <Prompt active={tag === null} onClick={() => setTag(null)}>
            All
          </Prompt>
        </Prompts>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead width={24} />
            <TableHead width="100%" sorted={sort === "title" ? "asc" : false}>
              Task
            </TableHead>
            <TableHead hideBelow={640} width={104} sorted={sort === "project" ? "asc" : false}>
              Project
            </TableHead>
            <TableHead hideBelow={640} width={80} sorted={sort === "assignees" ? "asc" : false}>
              People
            </TableHead>
            <TableHead align="right" width={112} sorted={sort === "due" ? "asc" : false}>
              Due
            </TableHead>
          </TableRow>
        </TableHeader>
        {groups.map((group) => (
          <TableSection
            key={group.status}
            title={label(group.status)}
            count={group.rows.length}
            status={ring(statuses.indexOf(group.status), statuses.length)}
            columns={COLUMNS}
            open={!folded.includes(group.status)}
            onOpenChange={(open) =>
              setFolded(
                open ? folded.filter((one) => one !== group.status) : [...folded, group.status],
              )
            }
          >
            {group.rows.map((task, at) => (
              <TableRow
                key={task.id}
                state={
                  statuses.indexOf(task.status) === statuses.length - 1 ? "done" : "default"
                }
                editing={editing !== null && editing.id === task.id}
              >
                <TableCell>
                  <StatusIcon
                    status={ring(statuses.indexOf(task.status), statuses.length)}
                    label={`Move ${task.title} on`}
                    onClick={() => advance(task)}
                  />
                </TableCell>
                <TableCell>
                  {editing !== null && editing.id === task.id ? (
                    <ItemTitle
                      editable={{
                        value: editing.text,
                        onChange: (value) => setEditing({ id: task.id, text: value }),
                        onCommit: rename,
                        onCancel: () => setEditing(null),
                      }}
                    />
                  ) : (
                    task.title
                  )}
                </TableCell>
                <TableCell hideBelow={640}>
                  {task.project === undefined ? null : <Tag>{task.project}</Tag>}
                </TableCell>
                <TableCell hideBelow={640}>
                  {task.assignees === undefined ? null : (
                    <Count icon={<IconUsers size={14} stroke={1.5} />}>{task.assignees}</Count>
                  )}
                </TableCell>
                <TableCell align="right">
                  {task.due === undefined ? null : <TableMeta>{task.due}</TableMeta>}
                  <Menu>
                    <MenuButton icon label={`Options for ${task.title}`}>
                      <IconDots size={16} stroke={1.5} />
                    </MenuButton>
                    <MenuContent
                      onCloseAutoFocus={(event) => {
                        const target = asked.current;
                        if (target === null) return;
                        asked.current = null;
                        event.preventDefault();
                        setEditing({ id: target.id, text: target.title });
                      }}
                    >
                      <MenuItem disabled={sort !== null || at === 0} onSelect={() => swap(task, -1)}>
                        Move up
                      </MenuItem>
                      <MenuItem
                        disabled={sort !== null || at === group.rows.length - 1}
                        onSelect={() => swap(task, 1)}
                      >
                        Move down
                      </MenuItem>
                      <MenuSeparator />
                      <MenuLabel>Move to</MenuLabel>
                      {statuses
                        .filter((status) => status !== task.status)
                        .map((status) => (
                          <MenuItem
                            key={status}
                            onSelect={() =>
                              setMoved({ ...moved, [task.id]: status })
                            }
                          >
                            {label(status)}
                          </MenuItem>
                        ))}
                      <MenuSeparator />
                      <MenuItem
                        onSelect={() => {
                          asked.current = task;
                        }}
                      >
                        Rename
                      </MenuItem>
                      <MenuItem destructive onSelect={() => setGone([...gone, task.id])}>
                        Delete
                      </MenuItem>
                    </MenuContent>
                  </Menu>
                </TableCell>
              </TableRow>
            ))}
          </TableSection>
        ))}
        {drawn === 0 ? (
          <TableBody>
            <TableEmpty columns={COLUMNS}>
              {records.length === 0
                ? "Tasks appear here, grouped by status."
                : "No task matches the filter."}
            </TableEmpty>
          </TableBody>
        ) : null}
      </Table>

      {draft === null ? null : (
        <Composer
          placeholder="Add a task"
          value={draft}
          onChange={setDraft}
          onSubmit={append}
          autoFocus
        />
      )}
    </>
  );
}
