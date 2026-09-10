import { Fragment, useState } from "react";
import { IconAlertTriangle, IconChartLine, IconDots, IconSearch } from "@tabler/icons-react";
import { Line, LineChart, XAxis, YAxis } from "recharts";

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
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/blocks/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/blocks/chart";
import { Menu, MenuCheckboxItem, MenuContent, MenuItem, MenuTrigger } from "@/blocks/menu";
import { StatGrid, StatTile } from "@/blocks/stat";
import {
  Mark,
  ScoreDot,
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/blocks/table";
import { Prose } from "@/blocks/typography";

type Point = {
  id: string;
  night: string;
  rate: number;
  label: string;
  counts: string;
  suites: number;
  shards: number;
  delta?: string;
  direction?: "" | "up" | "down" | "flat";
  excluded?: number;
  shardRows?: number;
  previous?: string;
};

type Row = {
  id: string;
  suite: string;
  rate: number;
  detail: string;
  warn?: boolean;
  shard?: string;
  model?: string;
  previous?: string;
  passed?: string;
  movement?: string;
};

type Data = { nights: Point[]; suites: Row[] };

type SortKey = "suite" | "passed" | "rate";

type Sort = { key: SortKey; dir: "asc" | "desc" };

const ICON = 16;
const STROKE = 1.5;
const MARK_ICON = 12;
const TILES = 4;
const COLUMNS = 6;
const RECENT = 7;
const DOT = 3;
const ACTIVE_DOT = 6;
const DOMAIN_PAD = 2;
const TICK_GAP = 24;
const LESSER = 640;
const SUITE_WIDTH = 268;
const SHARD_WIDTH = 154;
const MODEL_WIDTH = 144;
const PASSED_WIDTH = 80;
const RATE_WIDTH = 62;
const PREVIOUS_WIDTH = 104;
const CHART_CONFIG = {
  rate: { label: "Pass rate", color: "var(--blk-primary)" },
} satisfies ChartConfig;

export default function App({ data }: { data: Data }) {
  const [pointId, setPointId] = useState(data.nights.at(-1)?.id ?? "");
  const [sort, setSort] = useState<Sort>({ key: "rate", dir: "asc" });
  const [openId, setOpenId] = useState("");
  const [movement, setMovement] = useState("");
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [warnedOnly, setWarnedOnly] = useState(false);

  const point = data.nights.find((night) => night.id === pointId) ?? data.nights.at(-1);
  const rates = data.nights.map((night) => night.rate);
  const domain: [number, number] = [
    Math.min(...rates) - DOMAIN_PAD,
    Math.max(...rates) + DOMAIN_PAD,
  ];
  const movements = [...new Set(data.suites.flatMap((row) => (row.movement ? [row.movement] : [])))];
  const needle = query.trim().toLowerCase();
  const rows = data.suites
    .filter((row) => (warnedOnly ? row.warn === true : true))
    .filter((row) => (movement ? row.movement === movement : true))
    .filter((row) => row.suite.toLowerCase().includes(needle))
    .sort((one, other) => {
      const order =
        sort.key === "suite"
          ? one.suite.localeCompare(other.suite)
          : sort.key === "passed"
            ? Number.parseInt(one.passed ?? "0", 10) - Number.parseInt(other.passed ?? "0", 10)
            : one.rate - other.rate;
      return sort.dir === "asc" ? order : -order;
    });
  const sortedAs = (key: SortKey) => (sort.key === key ? sort.dir : false);
  const toggleSort = (key: SortKey) =>
    setSort(
      sort.key === key ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" },
    );

  return (
    <>
      <ActionBar variant="header">
        {data.nights.length ? (
          <Menu>
            <MenuTrigger>
              <ActionBarTitle icon={<IconChartLine size={ICON} stroke={STROKE} />} menu>
                Nightly evals
              </ActionBarTitle>
            </MenuTrigger>
            <MenuContent align="start">
              {data.nights
                .slice(-RECENT)
                .reverse()
                .map((night) => (
                  <MenuItem key={night.id} onSelect={() => setPointId(night.id)}>
                    {night.night}
                  </MenuItem>
                ))}
            </MenuContent>
          </Menu>
        ) : (
          <ActionBarTitle icon={<IconChartLine size={ICON} stroke={STROKE} />}>
            Nightly evals
          </ActionBarTitle>
        )}
        {data.suites.length ? (
          <ActionBarCenter>
            {rows.length} of {data.suites.length} suites
          </ActionBarCenter>
        ) : null}
        <ActionBarActions>
          <IconButton
            label="Filter by name"
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
              <IconButton label="Suite list options">
                <IconDots size={ICON} stroke={STROKE} />
              </IconButton>
            </MenuTrigger>
            <MenuContent>
              <MenuCheckboxItem checked={warnedOnly} onCheckedChange={setWarnedOnly}>
                Warned suites only
              </MenuCheckboxItem>
            </MenuContent>
          </Menu>
        </ActionBarActions>
      </ActionBar>
      {movements.length ? (
        <Prompts wrap>
          <Prompt active={movement === ""} onClick={() => setMovement("")}>
            All
          </Prompt>
          {movements.map((value) => (
            <Prompt key={value} active={movement === value} onClick={() => setMovement(value)}>
              {value}
            </Prompt>
          ))}
        </Prompts>
      ) : null}
      {point ? (
        <StatGrid columns={TILES}>
          <StatTile
            label="Pass rate"
            value={point.label}
            delta={
              point.delta && point.direction
                ? { value: point.delta, direction: point.direction }
                : undefined
            }
            sub={point.previous ? `Previous run ${point.previous}` : undefined}
          />
          <StatTile
            label="Scored"
            value={point.counts}
            sub={
              point.excluded === undefined
                ? undefined
                : `${point.excluded} cases excluded from grading`
            }
          />
          <StatTile
            label="Suites"
            value={String(point.suites)}
            sub={point.shardRows === undefined ? undefined : `${point.shardRows} suite by shard rows`}
          />
          <StatTile label="Shards" value={String(point.shards)} />
        </StatGrid>
      ) : (
        <Prose>
          <p>The measures of a run appear here once one is recorded.</p>
        </Prose>
      )}
      <Card variant="plain">
        <CardContent>
          {data.nights.length ? (
            <ChartContainer config={CHART_CONFIG} ratio="half">
              <LineChart
                accessibilityLayer
                data={data.nights}
                margin={{ left: 16, right: 16, top: 16 }}
                onClick={(state) => {
                  const picked = data.nights.find((night) => night.night === state.activeLabel);
                  if (picked) setPointId(picked.id);
                }}
              >
                <YAxis dataKey="rate" domain={domain} hide />
                <XAxis
                  dataKey="night"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                  interval="preserveStartEnd"
                  minTickGap={TICK_GAP}
                />
                <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
                <Line
                  dataKey="rate"
                  type="linear"
                  strokeWidth={2}
                  stroke="var(--blk-color-rate)"
                  activeDot={{ r: ACTIVE_DOT }}
                  dot={({ cx, cy, payload, index }) => (
                    <circle
                      key={index}
                      cx={cx}
                      cy={cy}
                      r={payload.id === point?.id ? ACTIVE_DOT : DOT}
                      fill="var(--blk-color-rate)"
                    />
                  )}
                />
              </LineChart>
            </ChartContainer>
          ) : (
            <Prose>
              <p>A point joins this series each time a run finishes.</p>
            </Prose>
          )}
        </CardContent>
      </Card>
      <Card variant="plain">
        <CardHeader>
          <CardTitle>Latest run</CardTitle>
          {searching ? (
            <CardAction>
              <SearchField
                placeholder="Filter by name"
                value={query}
                onChange={setQuery}
                onClear={() => setQuery("")}
              />
            </CardAction>
          ) : null}
        </CardHeader>
        <CardContent>
          <Table density="dense">
            <TableHeader>
              <TableRow>
                <TableHead
                  width={SUITE_WIDTH}
                  sortable
                  sorted={sortedAs("suite")}
                  onSort={() => toggleSort("suite")}
                >
                  Suite
                </TableHead>
                <TableHead width={SHARD_WIDTH} hideBelow={LESSER}>
                  Shard
                </TableHead>
                <TableHead width={MODEL_WIDTH} hideBelow={LESSER}>
                  Model
                </TableHead>
                <TableHead
                  width={PASSED_WIDTH}
                  sortable
                  sorted={sortedAs("passed")}
                  onSort={() => toggleSort("passed")}
                >
                  Passed
                </TableHead>
                <TableHead
                  width={RATE_WIDTH}
                  align="right"
                  sortable
                  sorted={sortedAs("rate")}
                  onSort={() => toggleSort("rate")}
                >
                  Rate
                </TableHead>
                <TableHead width={PREVIOUS_WIDTH} hideBelow={LESSER}>
                  Vs prev run
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length ? (
                rows.map((row) => (
                  <Fragment key={row.id}>
                    <TableRow
                      selected={openId === row.id}
                      onClick={() => setOpenId(openId === row.id ? "" : row.id)}
                    >
                      <TableCell>
                        {row.warn ? (
                          <Mark>
                            <IconAlertTriangle size={MARK_ICON} stroke={STROKE} />
                          </Mark>
                        ) : null}
                        {row.suite}
                      </TableCell>
                      <TableCell hideBelow={LESSER} muted>
                        {row.shard}
                      </TableCell>
                      <TableCell hideBelow={LESSER} muted>
                        {row.model}
                      </TableCell>
                      <TableCell>{row.passed}</TableCell>
                      <TableCell align="right">
                        <ScoreDot value={row.rate} />
                      </TableCell>
                      <TableCell hideBelow={LESSER} muted>
                        {row.previous}
                      </TableCell>
                    </TableRow>
                    {openId === row.id ? (
                      <TableRow selected>
                        <TableCell colSpan={COLUMNS}>
                          <Prose>
                            <p>{row.detail}</p>
                          </Prose>
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                ))
              ) : (
                <TableEmpty columns={COLUMNS}>
                  {data.suites.length
                    ? "No suite matches the filters in force."
                    : "A suite lands here for every shard a run scores."}
                </TableEmpty>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
