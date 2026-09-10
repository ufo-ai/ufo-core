import { Fragment, useRef, useState } from "react";
import { IconArrowUp, IconInbox, IconSearch, IconSettings } from "@tabler/icons-react";

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
import {
  Card,
  CardAction,
  CardButton,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/blocks/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemFooter,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemSection,
  ItemTitle,
  Tag,
} from "@/blocks/item";
import { Menu, MenuCheckboxItem, MenuContent, MenuLabel, MenuTrigger } from "@/blocks/menu";
import { Prose } from "@/blocks/typography";

type Fact = { id: string; text: string };
type Note = { id: string; band: string; label: string; body: string };
type Chip = { id: string; band: string; label: string };
type Entry = {
  id: string;
  number?: string;
  title: string;
  date?: string;
  approved: boolean;
  running: boolean;
};

type Data = {
  cadence?: string;
  armed: Record<string, boolean>;
  facts?: Fact[];
  notes: Note[];
  prompts?: Chip[];
  queue: Entry[];
};

const NAME = "Issues";
const ICON = 16;
const STROKE = 1.5;
const JOIN = " · ";
const SORTS = ["Identifier", "Title"];
const CADENCES = ["Hourly", "Daily", "Weekly"];
const GROUPS = [
  { label: "Waiting", approved: false, empty: "Records waiting for approval appear here." },
  { label: "Approved", approved: true, empty: "A record appears here once you approve it." },
];
const NO_MATCH = "No record matches the search.";

export default function App({ data }: { data: Data }) {
  const [flips, setFlips] = useState<Record<string, boolean>>({});
  const [started, setStarted] = useState<string[]>([]);
  const [approved, setApproved] = useState<string[]>([]);
  const [cadence, setCadence] = useState(data.cadence ?? CADENCES[0]);
  const [sort, setSort] = useState(SORTS[0]);
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [folded, setFolded] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const foot = useRef<HTMLDivElement>(null);

  const facts = data.facts ?? [];
  const chips = data.prompts ?? [];
  const bands = [...new Set(data.notes.map((note) => note.band))];
  const armed = (band: string) => flips[band] ?? data.armed[band] === true;
  const query = search.trim().toLowerCase();
  const ordered = data.queue
    .filter((entry) => `${entry.number ?? ""} ${entry.title}`.toLowerCase().includes(query))
    .sort((one, other) =>
      sort === "Title"
        ? one.title.localeCompare(other.title)
        : (other.number ?? "").localeCompare(one.number ?? "", undefined, { numeric: true }),
    );

  const fill = (text: string) => {
    setDraft(text);
    foot.current?.querySelector("input")?.focus();
  };

  return (
    <>
      <ActionBar variant="header">
        <ActionBarTitle icon={<IconInbox size={ICON} stroke={STROKE} />} menu>
          <Menu>
            <MenuTrigger>{NAME}</MenuTrigger>
            <MenuContent align="start">
              <MenuLabel>Sort</MenuLabel>
              {SORTS.map((key) => (
                <MenuCheckboxItem key={key} checked={sort === key} onCheckedChange={() => setSort(key)}>
                  {key}
                </MenuCheckboxItem>
              ))}
            </MenuContent>
          </Menu>
        </ActionBarTitle>
        <ActionBarActions>
          {searching ? <SearchField value={search} onChange={setSearch} onClear={() => setSearch("")} /> : null}
          <Menu>
            <MenuTrigger asChild>
              <IconButton label="Setting">
                <IconSettings size={ICON} stroke={STROKE} />
              </IconButton>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>Sweep</MenuLabel>
              {CADENCES.map((value) => (
                <MenuCheckboxItem
                  key={value}
                  checked={cadence === value}
                  onCheckedChange={() => setCadence(value)}
                >
                  {value}
                </MenuCheckboxItem>
              ))}
            </MenuContent>
          </Menu>
          <IconButton
            label="Search"
            active={searching}
            onClick={() => {
              setSearching(!searching);
              setSearch("");
            }}
          >
            <IconSearch size={ICON} stroke={STROKE} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>

      <Prose>
        {facts.length ? (
          <small>{facts.map((fact) => fact.text).join(JOIN)}</small>
        ) : (
          <p>Facts appear here once a band has run.</p>
        )}
      </Prose>

      {bands.map((band) => {
        const bandChips = chips.filter((chip) => chip.band === band);
        return (
          <Card key={band} variant="plain">
            <CardHeader>
              <CardTitle>{band}</CardTitle>
              {armed(band) ? <CardDescription>{cadence}</CardDescription> : null}
              <CardAction>
                <ItemActions>
                  <Tag>{armed(band) ? "Armed" : "Off"}</Tag>
                  <CardButton
                    variant="secondary"
                    onClick={() => setFlips((held) => ({ ...held, [band]: !armed(band) }))}
                  >
                    {armed(band) ? "Disarm" : "Arm"}
                  </CardButton>
                </ItemActions>
              </CardAction>
            </CardHeader>
            <CardContent>
              <Prose>
                {data.notes
                  .filter((note) => note.band === band)
                  .map((note) => (
                    <Fragment key={note.id}>
                      <h4>{note.label}</h4>
                      <p>{note.body}</p>
                    </Fragment>
                  ))}
              </Prose>
            </CardContent>
            <CardFooter>
              {bandChips.length ? (
                <Prompts wrap>
                  {bandChips.map((chip) => (
                    <Prompt key={chip.id} disabled={!armed(band)} onClick={() => fill(chip.label)}>
                      {chip.label}
                    </Prompt>
                  ))}
                </Prompts>
              ) : (
                <Prose>
                  <p>Chips appear here once the agent offers one.</p>
                </Prose>
              )}
            </CardFooter>
          </Card>
        );
      })}

      {bands.length ? null : (
        <Prose>
          <p>A band appears here once you set one up in chat.</p>
        </Prose>
      )}

      <ItemGroup>
        {GROUPS.map((group) => {
          const rows = ordered.filter(
            (entry) => (entry.approved || approved.includes(entry.id)) === group.approved,
          );
          return (
            <ItemSection
              key={group.label}
              label={group.label}
              count={rows.length}
              open={!folded.includes(group.label)}
              onOpenChange={(next) =>
                setFolded((held) =>
                  next ? held.filter((label) => label !== group.label) : [...held, group.label],
                )
              }
            >
              {rows.length ? (
                rows.map((entry) => {
                  const running = entry.running || started.includes(entry.id);
                  return (
                    <Item key={entry.id} state={group.approved ? "past" : "default"}>
                      {entry.number ? <ItemMedia variant="default">{entry.number}</ItemMedia> : null}
                      <ItemContent>
                        <ItemTitle>{entry.title}</ItemTitle>
                      </ItemContent>
                      <ItemActions>
                        {entry.date ? <ItemMeta>{entry.date}</ItemMeta> : null}
                        {running ? <Tag>Running</Tag> : null}
                      </ItemActions>
                      <ItemFooter>
                        <Prompt
                          disabled={running}
                          onClick={() => setStarted((held) => [...held, entry.id])}
                        >
                          Start
                        </Prompt>
                        {group.approved ? null : (
                          <CardButton
                            variant="secondary"
                            onClick={() => setApproved((held) => [...held, entry.id])}
                          >
                            Approve
                          </CardButton>
                        )}
                      </ItemFooter>
                    </Item>
                  );
                })
              ) : (
                <Prose>
                  <p>{query ? NO_MATCH : group.empty}</p>
                </Prose>
              )}
            </ItemSection>
          );
        })}
      </ItemGroup>

      <div ref={foot}>
        <Composer
          placeholder="Ask the agent"
          value={draft}
          onChange={setDraft}
          onSubmit={() => setDraft("")}
          trailing={
            <IconButton label="Send" type="submit">
              <IconArrowUp size={ICON} stroke={STROKE} />
            </IconButton>
          }
        />
      </div>
    </>
  );
}
