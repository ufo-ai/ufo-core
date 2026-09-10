import { useState } from "react";
import { IconCalendar, IconChecks, IconPlus, IconSearch, IconUsers } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import { Checkbox } from "@/blocks/checkbox";
import {
  Count,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemSection,
  ItemTitle,
} from "@/blocks/item";
import { Menu, MenuCheckboxItem, MenuContent, MenuTrigger } from "@/blocks/menu";
import { Prose } from "@/blocks/typography";

type Meeting = {
  id: string;
  day: string;
  title: string;
  notes?: string;
  time?: string;
  place?: string;
  attendees?: number;
  read: boolean;
};

type Data = {
  summary?: string;
  meetings: Meeting[];
};

type Filter = "Unread" | "Read";

const NAME = "Meetings";
const LINE = "Summary";
const FILTERS: Filter[] = ["Unread", "Read"];
const NOTHING = "No meetings drawn. Add one, or clear the search and the filter.";
const META = " • ";
const ICON = 16;
const STROKE = 1.5;
const COUNT_ICON = 14;

export default function App({ data }: { data: Data }) {
  const [seed, setSeed] = useState(data.meetings);
  const [rows, setRows] = useState(data.meetings);
  const [opened, setOpened] = useState<string | null>(null);
  const [folded, setFolded] = useState<string[]>([]);
  const [dropped, setDropped] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter | null>(null);
  const [line, setLine] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [seq, setSeq] = useState(0);

  if (seed !== data.meetings) {
    setSeed(data.meetings);
    setRows(data.meetings);
  }

  const days = [...new Set(rows.map((row) => row.day))];
  const current = days.at(0);
  const needle = (query ?? "").trim().toLowerCase();
  const drawn = rows.filter(
    (row) =>
      !dropped.includes(row.day) &&
      row.title.toLowerCase().includes(needle) &&
      (filter === null || row.read === (filter === "Read")),
  );
  const sections = days.filter(
    (day) =>
      !dropped.includes(day) &&
      (drawn.some((row) => row.day === day) || (day === current && draft !== null)),
  );

  const add = () => {
    if (current === undefined) return;
    setFolded(folded.filter((day) => day !== current));
    setDropped(dropped.filter((day) => day !== current));
    setDraft("");
  };

  const commit = () => {
    const title = (draft ?? "").trim();
    setDraft(null);
    if (title === "" || current === undefined) return;
    setRows([{ id: `row-${seq}`, day: current, title, read: false }, ...rows]);
    setSeq(seq + 1);
  };

  const markRead = () => {
    const ids = drawn.map((row) => row.id);
    setRows(rows.map((row) => (ids.includes(row.id) ? { ...row, read: true } : row)));
  };

  return (
    <>
      <ActionBar>
        <ActionBarTitle icon={<IconCalendar size={ICON} stroke={STROKE} />} menu={days.length > 0}>
          {days.length > 0 ? (
            <Menu>
              <MenuTrigger>{NAME}</MenuTrigger>
              <MenuContent align="start">
                {days.map((day) => (
                  <MenuCheckboxItem
                    key={day}
                    checked={!dropped.includes(day)}
                    onCheckedChange={(on) =>
                      setDropped(on ? dropped.filter((name) => name !== day) : [...dropped, day])
                    }
                  >
                    {day}
                  </MenuCheckboxItem>
                ))}
              </MenuContent>
            </Menu>
          ) : (
            NAME
          )}
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Add meeting" disabled={current === undefined} onClick={add}>
            <IconPlus size={ICON} stroke={STROKE} />
          </IconButton>
          <IconButton
            label="Search meetings"
            active={query !== null}
            onClick={() => setQuery(query === null ? "" : null)}
          >
            <IconSearch size={ICON} stroke={STROKE} />
          </IconButton>
          <IconButton
            label="Mark drawn meetings read"
            disabled={drawn.every((row) => row.read)}
            onClick={markRead}
          >
            <IconChecks size={ICON} stroke={STROKE} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <Prompts wrap>
        {data.summary ? (
          <Prompt active={line} onClick={() => setLine(!line)}>
            {LINE}
          </Prompt>
        ) : null}
        {FILTERS.map((name) => (
          <Prompt
            key={name}
            active={filter === name}
            onClick={() => setFilter(filter === name ? null : name)}
          >
            {name}
          </Prompt>
        ))}
      </Prompts>
      {query === null ? null : (
        <ActionBar variant="toolbar">
          <SearchField value={query} onChange={setQuery} onClear={() => setQuery("")} />
        </ActionBar>
      )}
      {line && data.summary ? (
        <Prose>
          <p>{data.summary}</p>
        </Prose>
      ) : null}
      {sections.length === 0 ? (
        <Prose>
          <p>{NOTHING}</p>
        </Prose>
      ) : (
        <ItemGroup>
          {sections.map((day) => (
            <ItemSection
              key={day}
              label={day}
              badge={drawn.filter((row) => row.day === day && !row.read).length}
              accent={day === current ? "primary" : "muted"}
              open={!folded.includes(day)}
              onOpenChange={(on) =>
                setFolded(on ? folded.filter((name) => name !== day) : [...folded, day])
              }
            >
              {day === current && draft !== null ? (
                <Item editing accent="primary">
                  <ItemContent>
                    <ItemTitle
                      editable={{
                        value: draft,
                        onChange: setDraft,
                        onCommit: commit,
                        onCancel: () => setDraft(null),
                      }}
                    />
                  </ItemContent>
                </Item>
              ) : null}
              {drawn
                .filter((row) => row.day === day)
                .map((row) => (
                  <Item
                    key={row.id}
                    accent={day === current ? "primary" : "muted"}
                    state={row.read || day !== current ? "past" : "default"}
                    selected={opened === row.id}
                    onClick={() => setOpened(opened === row.id ? null : row.id)}
                  >
                    <ItemMedia variant="checkbox">
                      <span onClick={(event) => event.stopPropagation()}>
                        <Checkbox
                          label={`Mark ${row.title} read`}
                          checked={row.read}
                          onCheckedChange={(on) =>
                            setRows(rows.map((held) => (held.id === row.id ? { ...held, read: on } : held)))
                          }
                        />
                      </span>
                    </ItemMedia>
                    <ItemContent>
                      <ItemTitle>{row.title}</ItemTitle>
                      {row.notes ? (
                        <ItemDescription lines={opened === row.id ? 3 : 1}>{row.notes}</ItemDescription>
                      ) : null}
                      {row.time || row.place ? (
                        <ItemMeta>{[row.time, row.place].filter(Boolean).join(META)}</ItemMeta>
                      ) : null}
                    </ItemContent>
                    {row.attendees === undefined ? null : (
                      <ItemActions>
                        <Count icon={<IconUsers size={COUNT_ICON} stroke={STROKE} />}>
                          {row.attendees}
                        </Count>
                      </ItemActions>
                    )}
                  </Item>
                ))}
            </ItemSection>
          ))}
        </ItemGroup>
      )}
    </>
  );
}
