import { useState } from "react";
import { IconArrowUp, IconChartBar, IconDatabase, IconDots, IconMessagePlus } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  Composer,
  IconButton,
  Prompt,
  Prompts,
} from "@/blocks/action-bar";
import {
  Card,
  CardAction,
  CardButton,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/blocks/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle, Tag } from "@/blocks/item";
import {
  Menu,
  MenuCheckboxItem,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuTrigger,
} from "@/blocks/menu";
import { Stat, StatGrid, type StatDelta } from "@/blocks/stat";
import { Prose } from "@/blocks/typography";

const NAME = "Metrics";
const ICON = 16;
const STROKE = 1.5;
const FIGURE = /\d/;
const BODY_LINES = 3;

type Band = { id: string; name: string; scope?: string; on: boolean; columns: number };

type Measure = {
  id: string;
  section: string;
  source?: string;
  label: string;
  value: string;
  delta?: string;
  direction?: string;
  counted?: string;
  priorValue?: string;
  priorDelta?: string;
  priorDirection?: string;
};

type Ask = { id: string; title: string; body?: string; asked: boolean };

type Data = {
  windows: Record<string, string>;
  sections: Band[];
  stats: Measure[];
  asks: Ask[];
};

type Reading = { value: string; delta?: string; direction?: string };

function columnsOf(columns: number): 2 | 3 | 4 {
  if (columns >= 4) return 4;
  if (columns === 3) return 3;
  return 2;
}

function deltaOf(reading: Reading): StatDelta | undefined {
  const { delta, direction } = reading;
  if (!delta) return undefined;
  if (direction !== "up" && direction !== "down" && direction !== "flat") return undefined;
  return { value: delta, direction };
}

export default function App({ data }: { data: Data }) {
  const keys = Object.keys(data.windows);
  const [chosen, setChosen] = useState(keys[0] ?? "");
  const [turned, setTurned] = useState<Record<string, boolean>>({});
  const [dropped, setDropped] = useState<string[]>([]);
  const [answered, setAnswered] = useState<Record<string, boolean>>({});
  const [empties, setEmpties] = useState(true);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState("");
  const [caret, setCaret] = useState(0);

  const held = keys.includes(chosen) ? chosen : (keys[0] ?? "");
  const label = data.windows[held] ?? "";
  const prior = keys.indexOf(held) > 0;
  const sources = [...new Set(data.stats.flatMap((measure) => (measure.source ? [measure.source] : [])))];

  const reading = (measure: Measure): Reading =>
    prior
      ? { value: measure.priorValue ?? "", delta: measure.priorDelta, direction: measure.priorDirection }
      : { value: measure.value, delta: measure.delta, direction: measure.direction };

  const measures = (band: Band) =>
    data.stats.filter(
      (measure) =>
        measure.section === band.id &&
        (measure.source === undefined || !dropped.includes(measure.source)) &&
        (empties || FIGURE.test(reading(measure).value)),
    );

  const turn = (id: string, on: boolean) => setTurned({ ...turned, [id]: on });

  const pick = (source: string, on: boolean) =>
    setDropped(on ? dropped.filter((name) => name !== source) : [...dropped, source]);

  const fill = (ask: Ask) => {
    setDraft(ask.title);
    setPending(ask.id);
    setCaret(caret + 1);
  };

  const start = () => {
    setDraft("");
    setPending("");
    setCaret(caret + 1);
  };

  const send = () => {
    if (pending) setAnswered({ ...answered, [pending]: true });
    setPending("");
    setDraft("");
  };

  return (
    <>
      <ActionBar>
        <ActionBarTitle
          icon={<IconChartBar size={ICON} stroke={STROKE} />}
          menu={
            data.sections.length > 0 ? (
              <MenuContent align="start">
                <MenuLabel>Sections</MenuLabel>
                {data.sections.map((band) => (
                  <MenuCheckboxItem
                    key={band.id}
                    checked={turned[band.id] ?? band.on}
                    onCheckedChange={(next) => turn(band.id, next)}
                  >
                    {band.name}
                  </MenuCheckboxItem>
                ))}
              </MenuContent>
            ) : undefined
          }
        >
          {NAME}
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Ask" onClick={start}>
            <IconMessagePlus size={ICON} stroke={STROKE} />
          </IconButton>
          {sources.length > 0 ? (
            <Menu>
              <MenuTrigger asChild>
                <IconButton label="Sources">
                  <IconDatabase size={ICON} stroke={STROKE} />
                </IconButton>
              </MenuTrigger>
              <MenuContent>
                <MenuLabel>Sources</MenuLabel>
                {sources.map((source) => (
                  <MenuCheckboxItem
                    key={source}
                    checked={!dropped.includes(source)}
                    onCheckedChange={(next) => pick(source, next)}
                  >
                    {source}
                  </MenuCheckboxItem>
                ))}
              </MenuContent>
            </Menu>
          ) : null}
          <Menu>
            <MenuTrigger asChild>
              <IconButton label="More">
                <IconDots size={ICON} stroke={STROKE} />
              </IconButton>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>Show</MenuLabel>
              <MenuCheckboxItem checked={empties} onCheckedChange={setEmpties}>
                Empty measures
              </MenuCheckboxItem>
            </MenuContent>
          </Menu>
        </ActionBarActions>
      </ActionBar>
      {keys.length > 0 ? (
        <Prompts wrap>
          {keys.map((key) => (
            <Prompt key={key} active={key === held} onClick={() => setChosen(key)}>
              {data.windows[key]}
            </Prompt>
          ))}
        </Prompts>
      ) : null}
      {data.sections.length > 0 ? (
        data.sections.map((band) => {
          const on = turned[band.id] ?? band.on;
          const rows = measures(band);
          return (
            <Card key={band.id} variant="plain">
              <CardHeader>
                <CardTitle>{band.name}</CardTitle>
                <CardDescription>{[band.scope, label].filter(Boolean).join(" ")}</CardDescription>
                <CardAction>
                  {on ? (
                    <Menu>
                      <MenuTrigger asChild>
                        <IconButton label={`${band.name} actions`}>
                          <IconDots size={ICON} stroke={STROKE} />
                        </IconButton>
                      </MenuTrigger>
                      <MenuContent>
                        <MenuItem onSelect={() => turn(band.id, false)}>Turn off</MenuItem>
                      </MenuContent>
                    </Menu>
                  ) : (
                    <CardButton variant="secondary" size="sm" onClick={() => turn(band.id, true)}>
                      Turn on
                    </CardButton>
                  )}
                </CardAction>
              </CardHeader>
              {on ? (
                <CardContent>
                  {rows.length > 0 ? (
                    <StatGrid columns={columnsOf(band.columns)}>
                      {rows.map((measure) => (
                        <Stat
                          key={measure.id}
                          label={
                            measure.source ? (
                              <>
                                <Tag>{measure.source}</Tag>
                                {measure.label}
                              </>
                            ) : (
                              measure.label
                            )
                          }
                          value={reading(measure).value}
                          delta={deltaOf(reading(measure))}
                          sub={measure.counted}
                        />
                      ))}
                    </StatGrid>
                  ) : (
                    <Prose>
                      <p>No measures. Each measure from a picked source draws here.</p>
                    </Prose>
                  )}
                </CardContent>
              ) : null}
            </Card>
          );
        })
      ) : (
        <Prose>
          <p>No sections. Each named set of measures draws here as a band.</p>
        </Prose>
      )}
      <Card variant="plain">
        <CardHeader>
          <CardTitle>Asks</CardTitle>
        </CardHeader>
        <CardContent>
          {data.asks.length > 0 ? (
            <ItemGroup flush>
              {data.asks.map((ask) => {
                const asked = answered[ask.id] ?? ask.asked;
                return (
                  <Item key={ask.id} state={asked ? "past" : "default"}>
                    <ItemContent>
                      <ItemTitle>{ask.title}</ItemTitle>
                      {ask.body ? <ItemDescription lines={BODY_LINES}>{ask.body}</ItemDescription> : null}
                    </ItemContent>
                    <ItemActions>
                      <CardButton variant="secondary" size="sm" disabled={asked} onClick={() => fill(ask)}>
                        Ask
                      </CardButton>
                    </ItemActions>
                  </Item>
                );
              })}
            </ItemGroup>
          ) : (
            <Prose>
              <p>No asks. Each request that would complete a measure draws here.</p>
            </Prose>
          )}
        </CardContent>
      </Card>
      <Composer
        placeholder="Ask about a measure"
        value={draft}
        focusKey={caret}
        onChange={setDraft}
        onSubmit={send}
        trailing={
          <IconButton label="Send" type="submit">
            <IconArrowUp size={ICON} stroke={STROKE} />
          </IconButton>
        }
      />
    </>
  );
}
