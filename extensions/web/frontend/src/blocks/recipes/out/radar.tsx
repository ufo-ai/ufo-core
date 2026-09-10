import { useState } from "react";
import { IconChecks, IconDots, IconRadar, IconSearch } from "@tabler/icons-react";

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
  Item,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemMedia,
  ItemSection,
  ItemTitle,
  Tag,
} from "@/blocks/item";
import { Menu, MenuCheckboxItem, MenuContent, MenuLabel, MenuTrigger } from "@/blocks/menu";
import { Prose } from "@/blocks/typography";

type Data = {
  tags: { id: string; label: string }[];
  releases: { id: string; version: string; day?: string; summary?: string }[];
  entries: {
    id: string;
    version: string;
    title: string;
    body: string;
    tags?: string[];
    read: boolean;
  }[];
};

const NAME = "Radar";
const ICON = 16;
const STROKE = 1.5;
const BODY_LINES = 3;

export default function App({ data }: { data: Data }) {
  const [read, setRead] = useState(data.entries.filter((entry) => entry.read).map((entry) => entry.id));
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [showRead, setShowRead] = useState(true);
  const [folded, setFolded] = useState<string[]>([]);
  const [dropped, setDropped] = useState<string[]>([]);

  const needle = query.trim().toLowerCase();
  const matches = data.entries.filter(
    (entry) =>
      (showRead || !read.includes(entry.id)) &&
      (tag === "" || (entry.tags ?? []).includes(tag)) &&
      (needle === "" || `${entry.title} ${entry.body}`.toLowerCase().includes(needle)),
  );
  const versions = [
    ...new Set([
      ...data.releases.map((release) => release.version),
      ...data.entries.map((entry) => entry.version),
    ]),
  ];
  const groups = versions
    .filter((version) => !dropped.includes(version))
    .map((version) => ({
      version,
      release: data.releases.find((release) => release.version === version),
      rows: matches.filter((entry) => entry.version === version),
    }))
    .filter((group) => group.rows.length > 0);
  const drawn = groups.flatMap((group) => group.rows.map((entry) => entry.id));
  const labels = data.tags.map((filter) => filter.label);
  const chips = tag === "" || labels.includes(tag) ? labels : [...labels, tag];

  return (
    <>
      <ActionBar variant="header">
        <ActionBarTitle icon={<IconRadar size={ICON} stroke={STROKE} />} menu>
          <Menu>
            <MenuTrigger>{NAME}</MenuTrigger>
            <MenuContent align="start">
              <MenuCheckboxItem checked={showRead} onCheckedChange={setShowRead}>
                Read entries
              </MenuCheckboxItem>
            </MenuContent>
          </Menu>
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Mark shown entries read" onClick={() => setRead([...new Set([...read, ...drawn])])}>
            <IconChecks size={ICON} stroke={STROKE} />
          </IconButton>
          <IconButton
            label="Search entries"
            active={searching}
            onClick={() => {
              setSearching(!searching);
              setQuery("");
            }}
          >
            <IconSearch size={ICON} stroke={STROKE} />
          </IconButton>
          <Menu>
            <MenuTrigger asChild>
              <IconButton label="Filter releases">
                <IconDots size={ICON} stroke={STROKE} />
              </IconButton>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>Releases</MenuLabel>
              {versions.map((version) => (
                <MenuCheckboxItem
                  key={version}
                  checked={!dropped.includes(version)}
                  onCheckedChange={(on) =>
                    setDropped(on ? dropped.filter((held) => held !== version) : [...dropped, version])
                  }
                >
                  {version}
                </MenuCheckboxItem>
              ))}
            </MenuContent>
          </Menu>
        </ActionBarActions>
      </ActionBar>
      {chips.length === 0 ? null : (
        <Prompts wrap>
          <Prompt active={tag === ""} onClick={() => setTag("")}>
            All
          </Prompt>
          {chips.map((label) => (
            <Prompt key={label} active={tag === label} onClick={() => setTag(label)}>
              {label}
            </Prompt>
          ))}
        </Prompts>
      )}
      {searching ? (
        <SearchField value={query} onChange={setQuery} onClear={() => setQuery("")} />
      ) : null}
      {groups.length === 0 ? (
        <Prose>
          <p>
            {data.entries.length === 0
              ? "Entries appear here when a release adds them."
              : "No entry matches the current filters."}
          </p>
        </Prose>
      ) : (
        <ItemGroup>
          {groups.map((group) => (
            <ItemSection
              key={group.version}
              label={group.version}
              badge={group.release?.day}
              count={group.rows.filter((entry) => !read.includes(entry.id)).length}
              open={!folded.includes(group.version)}
              onOpenChange={(open) =>
                setFolded(open ? folded.filter((held) => held !== group.version) : [...folded, group.version])
              }
            >
              {group.release?.summary === undefined ? null : (
                <Prose>
                  <p>{group.release.summary}</p>
                </Prose>
              )}
              {group.rows.map((entry) => (
                <Item key={entry.id} variant="outline" state={read.includes(entry.id) ? "past" : "default"}>
                  <ItemMedia variant="checkbox">
                    <Checkbox
                      label={`Mark ${entry.title} read`}
                      checked={read.includes(entry.id)}
                      onCheckedChange={(on) =>
                        setRead(on ? [...read, entry.id] : read.filter((held) => held !== entry.id))
                      }
                    />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle>{entry.title}</ItemTitle>
                    <Prose>
                      <ItemDescription lines={BODY_LINES}>{entry.body}</ItemDescription>
                    </Prose>
                  </ItemContent>
                  {entry.tags === undefined ? null : (
                    <ItemFooter>
                      {entry.tags.map((label) => (
                        <button key={label} type="button" aria-pressed={tag === label} onClick={() => setTag(label)}>
                          <Tag>{label}</Tag>
                        </button>
                      ))}
                    </ItemFooter>
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
