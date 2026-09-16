import { useEffect, useState, type ReactNode } from "react";
import {
  IconAlertTriangle,
  IconCheck,
  IconDots,
  IconHistory,
  IconLoader,
  IconLock,
  IconPlayerPause,
  IconPlus,
  IconX,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, Input, Textarea } from "@/components/ui/field";
import { MarkTile } from "@/components/ui/item";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { TdFill, TdStamp } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { rowControl } from "@/kernel/row";
import type { ObjectRow, ObjectValue } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { PageToolbar } from "@/kernel/pane";
import {
  OutcomeNotice,
  Panel,
  PanelBlank,
  PanelEmpty,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import type { SpecValue } from "@/kernel/form";
import { closed, opened } from "@/kernel/slots";
import { DataTable } from "@/kernel/table";
import { Pill, SelfSaving } from "@/kernel/task";
import { getJson, postIntent } from "@/lib/api";
import { AgentIcon } from "@/lib/agentIcon";
import { automationId, automationLane, type AutomationLane } from "@/lib/automationLane";
import { BrandMark } from "@/lib/brandMark";
import { ConversationLink } from "@/lib/conversationLink";
import {
  HOURS_OF_DAY,
  asMode,
  cadenceOf,
  cadenceWord,
  cronOf,
  dayFromDateValue,
  labelOf,
  startDateValue,
  timeLabel,
  weekdayName,
  type Cadence,
  type CadenceMode,
} from "@/lib/cadence";
import { cn } from "@/lib/cn";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { OwnerMark } from "@/lib/ownerMark";
import { setPendingAsk } from "@/lib/pendingAsk";
import { chatHash, newChatHash } from "@/lib/route";
import { navigate } from "@/lib/router";
import type { SpecSchema } from "@/lib/types";
import { DiscloseBand } from "@/views/Conversations";

const TURN_KIND = "turn";
const TASK_KIND = "scheduled_task";
const TRIGGER_KIND = "source_trigger";
const AUTOMATIONS_READ = "/automations";
const HEROES_READ = "/workspace/automations";
const PART = "/";
const DETAILS = "Details";
const NAME = "Name";
const INSTRUCTIONS = "Instructions";
const INSTRUCTION_LINES = 4;
const INSTRUCTION_LINES_GROWN = 16;
const WHEN_TO_RUN = "When to run";
const AT_HOUR = "Hour";
const ON_DAY = "Day";
const STARTING = "Starting";
const MONTH_DAY_NOTE =
  "A monthly automation runs on this date's day of every month, and skips a month shorter than that day.";
const WEEK_DAYS = [0, 1, 2, 3, 4, 5, 6];
const RUN_HISTORY = "Run history";
const SCHEDULE = "Schedule";
const PAUSED = "Paused";
const NEW_AUTOMATION = "New automation";
const CREATE = "Create";
const PAUSE = "Pause";
const RESUME = "Resume";
const DELETE = "Delete";
const CONFIRM_DELETE = "Confirm delete";
const MORE = "More";
const SAID_APART = " · ";
const NO_RUNS = "No run yet.";
/** The run history states what it holds in the label above it, so its columns are drawn unnamed. */
const RUN_COLUMNS = [
  { label: "", fill: true },
  { label: "", stamp: true },
];
const NAME_FIELD = "automation-name";
const INSTRUCTIONS_FIELD = "automation-instructions";
const DEFAULT_SCHEDULE = "0 9 * * *";
const AUTOMATION_FALLBACK_NAME = "automation";
/** `core/src/ufo/runtime/object_name.py` refuses a name longer than this. */
const NAME_LIMIT = 64;
const NAME_TRIES = 50;
const PART_COUNT = "-";
const NOT_FOUND = 404;
const NAME_UNREAD = "The automations could not be read. Try again.";
const WATCHING = "Watching ";
const PRIVATE_AUTOMATION = "Private automation";
const ANOTHER_MEMBER = "another member";
const EVERY_STREAM = "Every stream synced from ";
const WHOLE_FEED = "the whole feed of ";
const ON = " on ";
/** The surface a connection card draws, which these cards share: one radius, one hairline, one
 *  ground, so the two grids read as one product. */
const HERO_CARD = "flex flex-col gap-2xl rounded-card border border-edge bg-raised p-2xl";
const HERO_LINE = "m-0 line-clamp-2 text-small text-ink-soft";
const HERO_PLACES = [0, 1, 2];
const HERO_LINES = ["w-full", "w-4/5"];
/** A blank the type sets a line box on, which the skeleton over it is then the height of. */
const BLANK = "\u00a0";
const NO_RUNS_FOR_ONE = "No run yet. It will report into ";
const NO_AUTOMATIONS = "No automation yet.";
const NOT_ON_PAGE = "That item is not on this page.";
/** The cadences this screen offers. A cron outside them keeps its own words on the control and is
 *  rewritten in the automation's settings. */
const EVERY_OPTIONS: { mode: CadenceMode; label: string }[] = [
  { mode: "interval", label: "Every hour" },
  { mode: "daily", label: "Every day" },
  { mode: "weekdays", label: "Every weekday" },
  { mode: "weekly", label: "Every week" },
  { mode: "monthly", label: "Every month" },
];
const COLUMNS = [
  { label: "Name", fill: true },
  { label: "Creator", stamp: true },
  { label: "Events", stamp: true },
  { label: "Next run", stamp: true },
  { label: "Last run", stamp: true },
];
/** Sized to the circle a member is drawn in beside it, so the two marks down the row are one size. */
const CHIP =
  "flex size-(--size-avatar) shrink-0 items-center justify-center rounded-control border border-edge bg-fill";
/** Every cell but the two run times states a fact the member came to read, so the table is drawn in
 *  the page's own ink and the stamps alone recede. */
const FACT = "text-ink";
/** A paused row recedes but keeps every word it holds. The dimming rides the cells rather than the
 *  row, so the fill under the pointer and the band on the standing row stay at full strength. */
const RESTING = "opacity-(--opacity-muted-strong)";
const MARK = "size-3.5 shrink-0 text-ink-soft";
const LIVE = new Set(["queued", "running", "parked"]);
const STATUS_WORDS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  parked: "Waiting",
  done: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};
/** A run moves in seconds while it works, so a pane holding one re-reads at that rate; a pane of
 *  settled runs says the same thing every time it is asked. */
const WORKING_MS = 4_000;
const RESTING_MS = 30_000;
const HEROES_MS = 300_000;

const EXPLORE = "Explore automations";
const EXPLORE_NOTE = "Put your recurring work on autopilot.";

type Hero = { mark: string; title: string; line: string; ask: string };
type HeroesPayload = { heroes: Hero[] };

/** A workspace whose memory is still empty ranks nothing, and these cards are the empty state. */
const FALLBACK_HEROES: Hero[] = [
  {
    mark: "nephele",
    title: "Meeting brief",
    line: "Daily meeting brief to get you ready for your day.",
    ask: "Set up a daily automation that briefs me each morning on the meetings I have that day.",
  },
  {
    mark: "wedjat",
    title: "Morning update",
    line: "Morning update from across my team at 9am.",
    ask: "Set up an automation that sends me a morning update from across my team every weekday at 9am.",
  },
  {
    mark: "kalyx",
    title: "End of day summary",
    line: "End of day summary and actions for my team.",
    ask: "Set up an automation that sends my team an end of day summary and the actions it left open.",
  },
];

type Run = {
  name: string;
  conversation: string;
  status: string;
  createdAt: string;
  text: string;
};

/** One scheduled task or one source trigger as the combined list draws it. `lastRunAt` is the one
 *  column both kinds sort on: the task's own last fire, the trigger's newest woken turn. */
type Entry = {
  agent: string;
  kind: string;
  name: string;
  label: string;
  conversation: string;
  owner: string;
  readable: boolean;
  prompt: string;
  schedule: string;
  watching: string;
  feed: string;
  provider: string;
  connection: string;
  resource: string;
  streams: string[];
  nextRunAt: string;
  lastRunAt: string;
  paused: boolean;
  contentEditable: boolean;
  scheduleEditable: boolean;
  pausable: boolean;
  resumable: boolean;
  deletable: boolean;
};

type IndexRow = ObjectRow & { agent_id: string };
type KindPayload = {
  kind: string;
  spec_schema: SpecSchema | null;
  applies: boolean;
  deletes: boolean;
};
type AutomationsPayload = {
  kinds: KindPayload[];
  objects: (IndexRow & { kind: string })[];
  next_cursor: string | null;
};
type RunsPayload = { objects: IndexRow[]; next_cursor: string | null };

function said(value: ObjectValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim()) ?? "";
}

function toRun(row: IndexRow): Run {
  return {
    name: row.name,
    conversation: said(row.conversation),
    status: said(row.status),
    createdAt: said(row.created_at),
    text: said(row.text),
  };
}

function taskEntry(row: IndexRow): Entry {
  const paused = row.paused === true;
  const readable = row.readable !== false;
  return {
    agent: row.agent_id,
    kind: TASK_KIND,
    name: row.name,
    label: readable ? said(row.description) || firstLine(said(row.prompt)) || row.name : row.name,
    conversation: said(row.conversation),
    owner: said(row.owner_email),
    readable,
    prompt: said(row.prompt),
    schedule: said(row.schedule),
    watching: "",
    feed: "",
    provider: "",
    connection: "",
    resource: "",
    streams: [],
    nextRunAt: paused ? "" : said(row.next_run_at),
    lastRunAt: said(row.last_run_at),
    paused,
    contentEditable: row.content_editable === true,
    scheduleEditable: row.schedule_editable === true,
    pausable: row.pausable === true,
    resumable: row.resumable === true,
    deletable: row.deletable === true,
  };
}

/** A trigger reads as the thing it watches — the resource where it names one, else the feed and
 *  the streams of it that wake a conversation, never the account behind the feed. */
function triggerEntry(row: IndexRow): Entry {
  const resource = said(row.resource);
  const streams = said(row.streams).split(",").filter(Boolean);
  const feed = said(row.provider_label) || said(row.provider);
  const watched = [feed, ...streams].filter(Boolean).join(" ");
  return {
    agent: row.agent_id,
    kind: TRIGGER_KIND,
    name: row.name,
    label: WATCHING + (resource || watched),
    conversation: said(row.conversation),
    owner: said(row.owner_email),
    readable: true,
    prompt: "",
    schedule: "",
    watching: watched,
    feed,
    provider: said(row.provider),
    connection: said(row.connection),
    resource,
    streams,
    nextRunAt: "",
    lastRunAt: said(row.last_run_at),
    paused: row.paused === true,
    contentEditable: false,
    scheduleEditable: false,
    pausable: row.pausable === true,
    resumable: row.resumable === true,
    deletable: row.deletable === true,
  };
}

/** The read answers in one order, so the rows are drawn in the order they arrive. */
function entriesOf(payload: AutomationsPayload): Entry[] {
  return payload.objects.map((row) => (row.kind === TASK_KIND ? taskEntry(row) : triggerEntry(row)));
}

/** A paused automation runs next at no time at all, so it sits under the ones that do. A sort holds
 *  equal keys in the order it was given them, so each run keeps the order the read answered in. */
function resting(entries: Entry[]): Entry[] {
  return [...entries].sort((one, two) => Number(one.paused) - Number(two.paused));
}

function listPath(query: string | undefined, after: string | undefined): string {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (after) params.set("cursor", after);
  const asked = params.toString();
  return AUTOMATIONS_READ + (asked ? "?" + asked : "");
}

function runsPath(lane: AutomationLane, after: string | undefined): string {
  const params = new URLSearchParams({
    agent: lane.agent,
    fired: "true",
    order_by: "created_at",
    order: "desc",
    source: lane.kind,
    source_name: lane.name,
  });
  if (after) params.set("cursor", after);
  return "/objects/" + TURN_KIND + "?" + params.toString();
}

/** An automation is filed under the words the member described it with: lowercased, with every run
 *  of anything but a letter or a digit drawn as one hyphen, cut to the length a name takes. */
function slugged(said: string, room = NAME_LIMIT): string {
  return said
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, room)
    .replace(/^-+|-+$/g, "");
}

function namePath(agent: string, name: string): string {
  return "/objects/" + TASK_KIND + "/" + encodeURIComponent(name) + "?agent=" + agent;
}

/** Applying a name the member already holds updates that automation rather than refusing it, so
 *  each candidate is asked of the kind, which answers not-found for a name it does not hold. */
async function freeName(said: string, agent: string): Promise<string | null> {
  const slug = slugged(said) || AUTOMATION_FALLBACK_NAME;
  for (let count = 1; count <= NAME_TRIES; count += 1) {
    const suffix = count === 1 ? "" : PART_COUNT + count;
    const candidate = slugged(slug, NAME_LIMIT - suffix.length) + suffix;
    const read = await getJson(namePath(agent, candidate));
    if (read.ok) continue;
    return read.status === NOT_FOUND ? candidate : null;
  }
  return null;
}

/** The streams that wake a trigger and the resource they are watched on — every stream the
 *  connection syncs, or its whole feed, where the trigger narrows to neither. */
function watchedLine(entry: Entry): string {
  const streams = entry.streams.length
    ? entry.streams.map(streamWords).join(", ")
    : EVERY_STREAM + entry.feed;
  const scope = entry.resource || WHOLE_FEED + entry.feed;
  return `${streams}${ON}${scope}`;
}

function streamWords(stream: string): string {
  return stream.replaceAll("_", " ");
}

function RunMark({ status }: { status: string }) {
  const says = STATUS_WORDS[status] ?? status;
  const glyph = "size-(--size-glyph) shrink-0";
  if (LIVE.has(status)) {
    return (
      <IconLoader
        role="img"
        aria-label={says}
        className={cn(glyph, "animate-spin text-ink-soft motion-reduce:animate-none")}
      />
    );
  }
  if (status === "failed") {
    return <IconAlertTriangle role="img" aria-label={says} className={cn(glyph, "text-blocked")} />;
  }
  if (status === "cancelled") {
    return <IconX role="img" aria-label={says} className={cn(glyph, "text-ink-quiet")} />;
  }
  return <IconCheck role="img" aria-label={says} className={cn(glyph, "text-ink-soft")} />;
}

/** What a run says it did, as one line: how it ended, then the first line it wrote. A run that filed
 *  nothing still says so, which is the answer a member opened the history for. */
function RunSaid({ run }: { run: Run }) {
  const word = STATUS_WORDS[run.status] ?? run.status;
  const said = firstLine(run.text);
  return (
    <span className="flex min-w-0 items-center gap-sm">
      <RunMark status={run.status} />
      <span className="truncate">{said ? word + SAID_APART + said : word}</span>
    </span>
  );
}

/** One tile for whatever wakes the automation, so a clock and a brand read as one set and a
 *  provider the portal holds no mark for still lands on a tile the size of every other. */
function EventTile({ says, children }: { says: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span role="img" aria-label={says} className={CHIP}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">{says}</TooltipContent>
    </Tooltip>
  );
}

/** What wakes the automation, as the mark of the thing that fires it and nothing else: the column is
 *  64px, and how often a schedule fires is the tooltip rather than a word the track cannot hold. */
function EventMark({ entry }: { entry: Entry }) {
  if (entry.kind !== TASK_KIND) {
    return (
      <EventTile says={entry.watching}>
        <BrandMark provider={entry.provider} className="size-(--size-glyph)" />
      </EventTile>
    );
  }
  const word = cadenceWord(cadenceOf(entry.schedule, new Date().getTimezoneOffset())) || SCHEDULE;
  return (
    <EventTile says={word}>
      <IconHistory className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
    </EventTile>
  );
}

/** The marks stand at the row's end rather than before the name, which slid every name in the
 *  column left by whichever marks the row above it happened to hold. */
function AutomationMarks({ entry }: { entry: Entry }) {
  return (
    <span className="ml-auto flex shrink-0 items-center gap-2xs">
      {entry.readable ? null : (
        <Tooltip>
          <TooltipTrigger asChild>
            <IconLock className={MARK} role="img" aria-label={PRIVATE_AUTOMATION} />
          </TooltipTrigger>
          <TooltipContent side="top">{PRIVATE_AUTOMATION}</TooltipContent>
        </Tooltip>
      )}
      {entry.paused ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <IconPlayerPause className={MARK} role="img" aria-label={PAUSED} />
          </TooltipTrigger>
          <TooltipContent side="top">{PAUSED}</TooltipContent>
        </Tooltip>
      ) : null}
    </span>
  );
}

/** One labelled block of the automation's own words, drawn as a field the member reads into. */
function Stated({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex shrink-0 flex-col gap-sm">
      <p className="m-0 text-small text-ink-quiet">{label}</p>
      <div className="whitespace-pre-wrap rounded-panel bg-fill p-lg text-small text-ink-soft">
        {children}
      </div>
    </div>
  );
}

/** The frequency the task fires on, as the member sets it: how often, then the hour, the day of the
 *  week or the date that frequency needs. Every pick is written as the task's own cron. */
function WhenToRun({
  schedule,
  readOnly,
  onSave,
}: {
  schedule: string;
  readOnly?: boolean;
  onSave: (schedule: string) => Promise<NoticeState>;
}) {
  const offset = new Date().getTimezoneOffset();
  const cadence = cadenceOf(schedule, offset);
  const save = (next: Cadence) => void onSave(cronOf(next, offset));
  return (
    <div className="flex shrink-0 flex-col gap-sm">
      <p className="m-0 text-small text-ink-quiet">{WHEN_TO_RUN}</p>
      <div className="flex flex-wrap items-center gap-sm">
        <Pill
          label={WHEN_TO_RUN}
          said={everyLabel(cadence)}
          value={cadence.mode}
          options={EVERY_OPTIONS.map((offer) => ({ value: offer.mode, label: offer.label }))}
          disabled={readOnly}
          onPick={(mode) => save(asMode(cadence, mode as CadenceMode, schedule, offset))}
        />
        {cadence.mode === "interval" || cadence.mode === "custom" ? null : (
          <Pill
            label={AT_HOUR}
            said={timeLabel(cadence.hour, cadence.minute)}
            value={String(cadence.hour)}
            options={HOURS_OF_DAY.map((hour) => ({
              value: String(hour),
              label: timeLabel(hour, 0),
            }))}
            disabled={readOnly}
            onPick={(hour) => save({ ...cadence, hour: Number(hour) })}
          />
        )}
        {cadence.mode === "weekly" ? (
          <Pill
            label={ON_DAY}
            said={weekdayName(cadence.weekday)}
            value={String(cadence.weekday)}
            options={WEEK_DAYS.map((day) => ({ value: String(day), label: weekdayName(day) }))}
            disabled={readOnly}
            onPick={(day) => save({ ...cadence, weekday: Number(day) })}
          />
        ) : null}
        {cadence.mode === "monthly" ? (
          <Input
            type="date"
            aria-label={STARTING}
            value={startDateValue(cadence.day, new Date())}
            disabled={readOnly}
            className="h-(--size-control) w-fit rounded-full py-0 text-label"
            onChange={(event) => {
              const day = dayFromDateValue(event.target.value);
              if (day !== null) save({ ...cadence, day });
            }}
          />
        ) : null}
      </div>
      {cadence.mode === "monthly" ? (
        <p className="m-0 text-small text-ink-quiet">{MONTH_DAY_NOTE}</p>
      ) : null}
    </div>
  );
}

function everyLabel(cadence: Cadence): string {
  if (cadence.mode === "interval" && cadence.hours !== 1) return labelOf(cadence);
  const offer = EVERY_OPTIONS.find((one) => one.mode === cadence.mode);
  return offer === undefined ? labelOf(cadence) : offer.label;
}

/** The task at the head of the Details pane, as the fields a member edits. Each field saves itself,
 *  so the pane is the one place the automation's own words are written. */
function TaskFields({
  entry,
  onApply,
}: {
  entry: Entry;
  onApply: (spec: Record<string, SpecValue>) => Promise<NoticeState>;
}) {
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  async function save(spec: Record<string, SpecValue>): Promise<NoticeState> {
    const outcome = await onApply(spec);
    setNotice(outcome.refused ? outcome : QUIET);
    return outcome;
  }
  return (
    <div className="flex shrink-0 flex-col gap-2xl">
      <OutcomeNotice state={notice} />
      <SelfSaving
        label={NAME}
        value={entry.label}
        readOnly={!entry.contentEditable}
        onSave={(description) => save({ description })}
      />
      <SelfSaving
        label={INSTRUCTIONS}
        value={entry.prompt}
        lines={INSTRUCTION_LINES}
        moreLines={INSTRUCTION_LINES_GROWN}
        readOnly={!entry.contentEditable}
        onSave={(prompt) => save({ prompt })}
      />
      <WhenToRun
        schedule={entry.schedule}
        readOnly={!entry.scheduleEditable}
        onSave={(schedule) => save({ schedule })}
      />
    </div>
  );
}

/** What the trigger watches, which is its identity, so nothing here edits it. Its acts stand at the
 *  head of the sheet beside a task's. */
function TriggerInfo({ entry }: { entry: Entry }) {
  return (
    <div className="flex shrink-0 flex-col gap-2xl">
      <h3 className="m-0 text-subtitle font-medium">{entry.label}</h3>
      <Stated label={WHEN_TO_RUN}>{watchedLine(entry)}</Stated>
    </div>
  );
}

/** The automation's runs, newest first, listed directly under its own words and paged like every
 *  other listing. Pressing one opens that run's transcript beside the automation. */
function RunHistory({
  lane,
  conversation,
  after,
  opens,
  onPlace,
}: {
  lane: AutomationLane;
  conversation: string;
  after: string | undefined;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const [working, setWorking] = useState(false);
  const state = usePanelRead<RunsPayload>(
    runsPath(lane, after),
    0,
    working ? WORKING_MS : RESTING_MS,
  );
  const payload = state.phase === "ready" ? state.payload : null;
  useEffect(() => {
    if (payload === null) return;
    setWorking(payload.objects.some((row) => LIVE.has(said(row.status))));
  }, [payload]);
  return (
    <div className="flex shrink-0 flex-col gap-sm">
      <p className="m-0 text-small text-ink-quiet">{RUN_HISTORY}</p>
      <Panel state={state}>
        {(held) => {
          const runs = held.objects.map(toRun);
          if (!runs.length)
            return (
              <PanelBlank
                body={
                  conversation ? (
                    <>
                      {NO_RUNS_FOR_ONE}
                      <ConversationLink id={conversation} />.
                    </>
                  ) : (
                    NO_RUNS_FOR_ONE
                  )
                }
              />
            );
          return (
            <>
              <DataTable
                columns={RUN_COLUMNS}
                bare
                stacks={false}
                rows={runs}
                rowKey={(run) => run.name}
                empty={NO_RUNS}
                open={(run) =>
                  run.conversation
                    ? () => navigate(chatHash(run.conversation, undefined, undefined, run.name))
                    : null
                }
                pager={{
                  payload: { older: held.next_cursor },
                  after,
                  onPlace: (stepped) => onPlace({ runs: stepped.after, opens }),
                }}
              >
                {(run) => (
                  <>
                    <TdFill className={FACT}>
                      <RunSaid run={run} />
                    </TdFill>
                    <TdStamp className="text-right">
                      <Moment at={run.createdAt} />
                    </TdStamp>
                  </>
                )}
              </DataTable>
            </>
          );
        }}
      </Panel>
    </div>
  );
}

/** `ConfirmButton` cannot serve here: the press that arms it is the press that shuts the menu, so
 *  the Delete item arms itself and holds the menu open instead. */
function ActsMenu({
  entry,
  pauses,
  deletes,
  onPause,
  onDelete,
}: {
  entry: Entry;
  pauses: boolean;
  deletes: boolean;
  onPause: () => void;
  onDelete: () => void;
}) {
  const [armed, setArmed] = useState(false);
  if (!pauses && !deletes) return null;
  return (
    <DropdownMenu onOpenChange={(open) => !open && setArmed(false)}>
      <DropdownMenuTrigger asChild>
        <Button variant="quiet" size="icon" aria-label={MORE}>
          <IconDots aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {pauses ? (
          <DropdownMenuItem
            disabled={entry.paused ? !entry.resumable : !entry.pausable}
            onSelect={onPause}
          >
            {entry.paused ? RESUME : PAUSE}
          </DropdownMenuItem>
        ) : null}
        {deletes ? (
          <DropdownMenuItem
            disabled={!entry.deletable}
            onSelect={(event) => {
              if (armed) {
                onDelete();
                return;
              }
              event.preventDefault();
              setArmed(true);
            }}
          >
            {armed ? CONFIRM_DELETE : DELETE}
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** One automation's Details: the fields it runs under at the top, its runs listed under them. */
function DetailsSheet({
  lane,
  entry,
  deletes,
  triggerApplies,
  triggerDeletes,
  after,
  opens,
  onPlace,
  onApply,
  onPause,
  onDelete,
  onDisclosed,
}: {
  lane: AutomationLane;
  entry: Entry | null;
  deletes: boolean;
  triggerApplies: boolean;
  triggerDeletes: boolean;
  after: string | undefined;
  opens: string[];
  onPlace: (place: Placement) => void;
  onApply: (entry: Entry, spec: Record<string, SpecValue>) => Promise<NoticeState>;
  onPause: (entry: Entry, paused: boolean) => Promise<NoticeState>;
  onDelete: (entry: Entry) => Promise<NoticeState>;
  onDisclosed: () => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, automationId(lane)), runs: undefined });
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const acted = async (run: Promise<NoticeState>) => {
    const outcome = await run;
    setNotice(outcome.refused ? outcome : QUIET);
  };
  const ended = async (held: Entry) => {
    const outcome = await onDelete(held);
    setNotice(outcome.refused ? outcome : QUIET);
    if (!outcome.refused) shut();
  };
  const standing = entry !== null && entry.readable ? entry : null;
  return (
    <Sheet
      open
      title={DETAILS}
      onClose={shut}
      actions={
        standing === null ? null : (
          <ActsMenu
            entry={standing}
            pauses={standing.kind === TASK_KIND || triggerApplies}
            deletes={standing.kind === TASK_KIND ? deletes : triggerDeletes}
            onPause={() =>
              void acted(
                standing.kind === TASK_KIND
                  ? onApply(standing, { paused: !standing.paused })
                  : onPause(standing, !standing.paused),
              )
            }
            onDelete={() => void ended(standing)}
          />
        )
      }
    >
      <OutcomeNotice state={notice} />
      {entry === null ? (
        <PanelEmpty>{NOT_ON_PAGE}</PanelEmpty>
      ) : !entry.readable ? (
        <DiscloseBand
          agentId={entry.agent}
          conversationId={entry.conversation}
          owner={entry.owner || ANOTHER_MEMBER}
          title={PRIVATE_AUTOMATION}
          onOpened={onDisclosed}
        />
      ) : entry.kind === TASK_KIND ? (
        <TaskFields entry={entry} onApply={(spec) => onApply(entry, spec)} />
      ) : (
        <TriggerInfo entry={entry} />
      )}
      {entry !== null && !entry.readable ? null : (
        <RunHistory
          lane={lane}
          conversation={entry?.conversation ?? ""}
          after={after}
          opens={opens}
          onPlace={onPlace}
        />
      )}
    </Sheet>
  );
}

/** The pane the New automation act opens: the fields Details edits, before the automation exists.
 *  The name is its description, slugged; the automation is the main agent's, so no app is asked. */
function NewAutomation({
  lane,
  onDone,
  onClose,
}: {
  lane: string;
  onDone: (lane: string, envelope: unknown) => Promise<NoticeState>;
  onClose: () => void;
}) {
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [schedule, setSchedule] = useState(DEFAULT_SCHEDULE);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const ready = description.trim() !== "" && prompt.trim() !== "";

  async function create() {
    setBusy(true);
    const name = await freeName(description, lane);
    if (name === null) {
      setBusy(false);
      setNotice({ text: NAME_UNREAD, refused: true });
      return;
    }
    const outcome = await onDone(lane, {
      verb: "apply",
      kind: TASK_KIND,
      name,
      spec: {
        description: description.trim(),
        prompt: prompt.trim(),
        schedule,
      },
    });
    setBusy(false);
    if (outcome.refused) {
      setNotice(outcome);
      return;
    }
    onClose();
  }

  return (
    <div className="flex shrink-0 flex-col gap-2xl">
      <OutcomeNotice state={notice} />
      <Field label={NAME} htmlFor={NAME_FIELD}>
        <Input
          id={NAME_FIELD}
          value={description}
          className="max-w-full"
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>
      <Field label={INSTRUCTIONS} htmlFor={INSTRUCTIONS_FIELD}>
        <Textarea
          id={INSTRUCTIONS_FIELD}
          rows={INSTRUCTION_LINES}
          value={prompt}
          className="max-w-full"
          onChange={(event) => setPrompt(event.target.value)}
        />
      </Field>
      <WhenToRun
        schedule={schedule}
        onSave={(next) => {
          setSchedule(next);
          return Promise.resolve(QUIET);
        }}
      />
      <div className="flex justify-end">
        <Button
          variant="send"
          size="bar"
          busy={busy}
          disabled={!ready}
          onClick={() => void create()}
        >
          {CREATE}
        </Button>
      </div>
    </div>
  );
}

/** Ranked starters, drawn on the surface a connection card draws. While the ranking is read the
 *  grid holds three cards of that size, so the answer lands where its placeholder stood. */
function Heroes({ agentId }: { agentId: string }) {
  const read = usePanelRead<HeroesPayload>(HEROES_READ, 0, HEROES_MS);
  const answered = read.phase === "ready" ? read.payload : null;
  const heroes = answered?.heroes?.length ? answered.heroes : FALLBACK_HEROES;
  const start = (hero: Hero) => () => {
    setPendingAsk(agentId, hero.ask, true);
    navigate(newChatHash(agentId));
  };
  return (
    <Section title={EXPLORE} note={EXPLORE_NOTE}>
      <ul className="m-0 grid list-none grid-cols-3 gap-2xl p-0 max-narrow:grid-cols-1">
        {read.phase === "loading"
          ? HERO_PLACES.map((place) => <HeroWaiting key={place} />)
          : heroes.map((hero) => (
              <HeroCard key={hero.ask} hero={hero} onStart={start(hero)} />
            ))}
      </ul>
    </Section>
  );
}

function HeroCard({ hero, onStart }: { hero: Hero; onStart: () => void }) {
  const control = rowControl(onStart);
  return (
    <li {...control} className={cn(HERO_CARD, control.className, "hover:bg-fill")}>
      <MarkTile>
        <AgentIcon name={hero.mark} className="size-(--size-brand-mark)" />
      </MarkTile>
      <div className="flex min-w-0 flex-col">
        <span
          data-part="primary"
          className="truncate text-body leading-(--leading-chrome) font-medium text-ink"
        >
          {hero.title}
        </span>
        <p data-part="body" className={HERO_LINE}>
          {hero.line}
        </p>
      </div>
      <div className="mt-auto flex items-center">
        <Button variant="send" size="bar" onClick={onStart}>
          {CREATE}
        </Button>
      </div>
    </li>
  );
}

/** Every line is drawn over a blank the card's own type sets the height of, so the settled card
 *  takes the same room the placeholder held. */
function HeroWaiting() {
  return (
    <li className={HERO_CARD}>
      <Skeleton className="size-(--size-touch) shrink-0 rounded-control" />
      <div className="flex min-w-0 flex-col">
        <span className="relative text-body leading-(--leading-chrome)">
          {BLANK}
          <Skeleton className="absolute inset-y-0 left-0 w-3/5" />
        </span>
        <p className={cn(HERO_LINE, "m-0")}>
          {HERO_LINES.map((width, index) => (
            <span key={index} className="relative block">
              {BLANK}
              <Skeleton className={cn("absolute inset-y-0 left-0", width)} />
            </span>
          ))}
        </p>
      </div>
      <div className="mt-auto flex items-center">
        <Skeleton className="h-(--size-control) w-(--size-act) rounded-full" />
      </div>
    </li>
  );
}

/** The workspace's scheduled tasks and source triggers as one table. It pages on `after` and the
 *  open automation's runs page on `runs`, so one listing's step never moves the other's. */
export function Automations({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const opens = place.opens ?? [];
  const agents = useAgents();
  const mainAgent = useMainAgent();
  const [reloads, setReloads] = useState(0);
  const [creating, setCreating] = useState(false);
  const state = usePanelRead<AutomationsPayload>(listPath(place.q, place.after), reloads);
  const payload = state.phase === "ready" ? state.payload : null;
  const entries = payload === null ? [] : entriesOf(payload);
  const owner = mainAgent?.id ?? agents[0]?.id ?? null;
  const held = payload?.kinds.find((one) => one.kind === TASK_KIND) ?? null;
  const watching = payload?.kinds.find((one) => one.kind === TRIGGER_KIND) ?? null;
  const applies = held?.applies === true;
  const deletes = held?.deletes === true;
  const top = opens[opens.length - 1];
  const standing = top === undefined ? null : automationLane(top);

  async function submit(lane: string, envelope: unknown) {
    const outcome = await postIntent(lane, envelope);
    setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  return (
    <>
      <PageToolbar>
        {applies && owner ? (
          <Button variant="send" size="bar" className="ml-auto" onClick={() => setCreating(true)}>
            <IconPlus aria-hidden />
            {NEW_AUTOMATION}
          </Button>
        ) : null}
      </PageToolbar>
      {owner ? <Heroes agentId={owner} /> : null}
      <Section>
        <Panel state={state}>
          {() =>
            entries.length ? (
              <DataTable
                columns={COLUMNS}
                rows={resting(entries)}
                rowKey={(entry) => entry.agent + PART + entry.kind + PART + entry.name}
                empty={NO_AUTOMATIONS}
                lede
                pager={{
                  payload: { older: payload?.next_cursor },
                  after: place.after,
                  onPlace: (stepped) =>
                    onPlace({ after: stepped.after, opens: [], runs: undefined }),
                }}
                open={(entry) => () =>
                  onPlace({ opens: opened(opens, automationId(entry)), runs: undefined })
                }
                current={(entry) =>
                  standing !== null &&
                  standing.agent === entry.agent &&
                  standing.kind === entry.kind &&
                  standing.name === entry.name
                }
              >
                {(entry) => {
                  const rested = entry.paused ? RESTING : undefined;
                  return (
                    <>
                      <TdFill className={cn(FACT, rested)}>
                        <span className="flex min-w-0 items-center gap-sm">
                          <span className="truncate">{entry.label}</span>
                          <AutomationMarks entry={entry} />
                        </span>
                      </TdFill>
                      <TdStamp className={cn(FACT, rested)}>
                        <OwnerMark email={entry.owner} />
                      </TdStamp>
                      <TdStamp className={cn(FACT, rested)}>
                        <EventMark entry={entry} />
                      </TdStamp>
                      <TdStamp className={rested}>
                        {entry.nextRunAt ? <Moment at={entry.nextRunAt} /> : ""}
                      </TdStamp>
                      <TdStamp className={rested}>
                        {entry.lastRunAt ? <Moment at={entry.lastRunAt} /> : ""}
                      </TdStamp>
                    </>
                  );
                }}
              </DataTable>
            ) : null
          }
        </Panel>
      </Section>
      {creating && owner ? (
        <Sheet open title={NEW_AUTOMATION} onClose={() => setCreating(false)}>
          <NewAutomation lane={owner} onDone={submit} onClose={() => setCreating(false)} />
        </Sheet>
      ) : null}
      {top === undefined ? null : standing !== null ? (
        <DetailsSheet
          key={top}
          lane={standing}
          deletes={deletes}
          triggerApplies={watching?.applies === true}
          triggerDeletes={watching?.deletes === true}
          after={place.runs}
          entry={
            entries.find(
              (entry) =>
                entry.agent === standing.agent &&
                entry.kind === standing.kind &&
                entry.name === standing.name,
            ) ?? null
          }
          opens={opens}
          onPlace={onPlace}
          onApply={(entry, spec) =>
            submit(entry.agent, { verb: "apply", kind: entry.kind, name: entry.name, spec })
          }
          onPause={(entry, paused) =>
            submit(entry.agent, {
              verb: "apply",
              kind: TRIGGER_KIND,
              name: entry.name,
              spec: {
                connection: entry.connection,
                resource: entry.resource,
                streams: entry.streams,
                paused,
              },
            })
          }
          onDelete={(entry) =>
            submit(entry.agent, { verb: "delete", kind: entry.kind, name: entry.name })
          }
          onDisclosed={() => setReloads((count) => count + 1)}
        />
      ) : null}
    </>
  );
}
