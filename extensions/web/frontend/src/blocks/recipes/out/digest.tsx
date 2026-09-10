import { useState } from "react";
import { IconClock, IconDots, IconNotes, IconSearch } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarCenter,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import { Card, CardAction, CardButton, CardContent, CardHeader, CardTitle } from "@/blocks/card";
import { Checkbox } from "@/blocks/checkbox";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  Tag,
} from "@/blocks/item";
import { Menu, MenuCheckboxItem, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/blocks/menu";
import { Stat, StatGrid } from "@/blocks/stat";
import { Prose } from "@/blocks/typography";

type Period = { id: string; date: string; quiet: number; words: number };
type Entry = {
  id: string;
  date: string;
  channel: string;
  lead: string;
  messages: number;
  summary: string;
  detail: string;
};
type Question = { id: string; channel: string; question: string; waiting: string; resolved: boolean };
type Filter = { id: string; label: string; active: boolean };

type Data = {
  hour: string;
  filters: Filter[];
  days: Period[];
  channels: Entry[];
  blockers: Question[];
};

const PERIOD_MEASURES = ["quiet", "words"] as const;
const RECORD_MEASURE = "messages";
const SETTING_OFF = "Off";
const APP_NAME = "Digest";
const PERIOD_LIST = "Periods";
const RECORD_LIST = "Records";
const QUESTION_LIST = "Questions";
const SEPARATOR = " · ";

export default function App({ data }: { data: Data }) {
  const [periodId, setPeriodId] = useState(data.days[0]?.id ?? "");
  const [filterId, setFilterId] = useState(data.filters.find((one) => one.active)?.id ?? "");
  const [opened, setOpened] = useState<string[]>([]);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [resolvedShown, setResolvedShown] = useState(true);
  const [settingOn, setSettingOn] = useState(true);

  const period = data.days.find((one) => one.id === periodId);
  const whole = data.filters.find((one) => one.active)?.id ?? data.filters[0]?.id;
  const narrowed = filterId === whole ? "" : (data.filters.find((one) => one.id === filterId)?.label ?? "");
  const asked = query.trim().toLowerCase();
  const entries = data.channels.filter(
    (entry) =>
      entry.date === period?.date &&
      (narrowed === "" || entry.channel === narrowed) &&
      (asked === "" || `${entry.channel} ${entry.summary}`.toLowerCase().includes(asked)),
  );
  const summed = entries.reduce((total, entry) => total + entry[RECORD_MEASURE], 0);
  const questions = data.blockers.map((one) => ({ ...one, resolved: ticked[one.id] ?? one.resolved }));
  const unresolved = questions.filter((one) => !one.resolved);
  const shownQuestions = resolvedShown ? questions : unresolved;

  return (
    <>
      <ActionBar variant="header">
        <Menu>
          <MenuTrigger>
            <ActionBarTitle icon={<IconNotes size={16} stroke={1.5} />} menu>
              {APP_NAME}
            </ActionBarTitle>
          </MenuTrigger>
          <MenuContent align="start">
            <MenuLabel>{PERIOD_LIST}</MenuLabel>
            {data.days.map((one) => (
              <MenuItem key={one.id} onSelect={() => setPeriodId(one.id)}>
                {one.date}
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        {data.hour ? <ActionBarCenter>{settingOn ? data.hour : SETTING_OFF}</ActionBarCenter> : null}
        <ActionBarActions>
          <IconButton label="Digest hour" active={settingOn} onClick={() => setSettingOn(!settingOn)}>
            <IconClock size={16} stroke={1.5} />
          </IconButton>
          <Menu>
            <MenuTrigger asChild>
              <IconButton label="List options">
                <IconDots size={16} stroke={1.5} />
              </IconButton>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>Show</MenuLabel>
              <MenuCheckboxItem checked={resolvedShown} onCheckedChange={setResolvedShown}>
                Resolved questions
              </MenuCheckboxItem>
            </MenuContent>
          </Menu>
          <IconButton
            label="Search records"
            active={searching}
            onClick={() => {
              setSearching(!searching);
              setQuery("");
            }}
          >
            <IconSearch size={16} stroke={1.5} />
          </IconButton>
          {searching ? <SearchField value={query} onChange={setQuery} onClear={() => setQuery("")} /> : null}
        </ActionBarActions>
      </ActionBar>

      <StatGrid columns={4}>
        <Stat label={RECORD_LIST} value={String(entries.length)} />
        <Stat label={RECORD_MEASURE} value={String(summed)} />
        {period
          ? PERIOD_MEASURES.map((key) => <Stat key={key} label={key} value={String(period[key])} />)
          : null}
      </StatGrid>

      <Card variant="outline">
        <CardHeader>
          <CardTitle>{period ? period.date : RECORD_LIST}</CardTitle>
          <CardAction>
            <Tag>{entries.length}</Tag>
          </CardAction>
        </CardHeader>
        <CardContent>
          {data.filters.length > 0 ? (
            <Prompts wrap>
              {data.filters.map((one) => (
                <Prompt key={one.id} active={one.id === filterId} onClick={() => setFilterId(one.id)}>
                  {one.label}
                </Prompt>
              ))}
            </Prompts>
          ) : null}
          {entries.length > 0 ? (
            <ItemGroup flush>
              {entries.map((entry) => (
                <Item
                  key={entry.id}
                  variant="outline"
                  selected={opened.includes(entry.id)}
                  onClick={() =>
                    setOpened((held) =>
                      held.includes(entry.id) ? held.filter((id) => id !== entry.id) : [...held, entry.id],
                    )
                  }
                >
                  <ItemContent>
                    <ItemTitle>{entry.channel}</ItemTitle>
                    <ItemDescription>{entry.summary}</ItemDescription>
                  </ItemContent>
                  <ItemActions>
                    <ItemMeta>
                      {[entry.lead, `${entry[RECORD_MEASURE]}`].filter(Boolean).join(SEPARATOR)}
                    </ItemMeta>
                  </ItemActions>
                  {opened.includes(entry.id) ? <ItemFooter>{entry.detail}</ItemFooter> : null}
                </Item>
              ))}
            </ItemGroup>
          ) : (
            <Prose>
              <p>Records appear here once the agent summarizes the period.</p>
            </Prose>
          )}
        </CardContent>
      </Card>

      <Card variant="plain">
        <CardTitle>{PERIOD_LIST}</CardTitle>
        {data.days.length > 0 ? (
          <ItemGroup flush>
            {data.days.map((one) => (
              <Item
                key={one.id}
                size="sm"
                selected={one.id === periodId}
                onClick={() => setPeriodId(one.id)}
              >
                <ItemContent>
                  <ItemTitle>{one.date}</ItemTitle>
                </ItemContent>
                <ItemActions>
                  <ItemMeta>
                    {PERIOD_MEASURES.map((key) => `${one[key]} ${key}`).join(SEPARATOR)}
                  </ItemMeta>
                </ItemActions>
              </Item>
            ))}
          </ItemGroup>
        ) : (
          <Prose>
            <p>Periods appear here once the agent summarizes one.</p>
          </Prose>
        )}
      </Card>

      <Card variant="plain">
        <CardHeader>
          <CardTitle>
            {QUESTION_LIST} <Tag>{unresolved.length}</Tag>
          </CardTitle>
          {unresolved.length > 0 ? (
            <CardAction>
              <CardButton
                variant="secondary"
                onClick={() => setTicked(Object.fromEntries(questions.map((one) => [one.id, true])))}
              >
                Clear all
              </CardButton>
            </CardAction>
          ) : null}
        </CardHeader>
        <CardContent>
          {unresolved.length > 0 && shownQuestions.length > 0 ? (
            <ItemGroup flush>
              {shownQuestions.map((one) => (
                <Item key={one.id} variant="outline" state={one.resolved ? "past" : "default"}>
                  <ItemMedia variant="checkbox">
                    <Checkbox
                      label={one.question}
                      checked={one.resolved}
                      onCheckedChange={(on) => setTicked((held) => ({ ...held, [one.id]: on }))}
                    />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle>{one.question}</ItemTitle>
                    <ItemMeta>{[one.channel, one.waiting].filter(Boolean).join(SEPARATOR)}</ItemMeta>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          ) : (
            <Prose>
              <p>Open questions appear here when a record needs an answer.</p>
            </Prose>
          )}
        </CardContent>
      </Card>
    </>
  );
}
