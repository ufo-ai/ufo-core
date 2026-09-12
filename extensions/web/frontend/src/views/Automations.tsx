import { useEffect, useState, type ReactNode } from "react";
import { IconClockPlay, IconPlayerPause, IconPlus } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Sheet } from "@/components/ui/sheet";
import { Td, TdFact } from "@/components/ui/table";
import { CardGrid } from "@/kernel/cards";
import type { ObjectRow, ObjectValue } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { Pager } from "@/kernel/pager";
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
import { RowLines } from "@/kernel/rows";
import { closed, opened } from "@/kernel/slots";
import { DataTable } from "@/kernel/table";
import { Pill, SelfSaving } from "@/kernel/task";
import { getJson, postIntent } from "@/lib/api";
import { AgentIcon } from "@/lib/agentIcon";
import { automationId, automationLane, type AutomationLane } from "@/lib/automationLane";
import { useMe } from "@/lib/audience";
import { BrandMark } from "@/lib/brandMark";
import { ConversationLink } from "@/lib/conversationLink";
import {
  HOURS_OF_DAY,
  asMode,
  cadenceOf,
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
import { setPendingAsk } from "@/lib/pendingAsk";
import { newChatHash } from "@/lib/route";
import { navigate } from "@/lib/router";
import type { SpecSchema } from "@/lib/types";
import { ChatPane } from "@/views/ChatPane";

const TURN_KIND = "turn";
const TASK_KIND = "scheduled_task";
const TRIGGER_KIND = "source_trigger";
const AUTOMATIONS_READ = "/automations";
const HEROES_READ = "/workspace/automations";
const RUN_PREFIX = "run/";
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
const NEVER_RAN = "No run yet";
const NO_RUNS_FOR_ONE = "No run yet. It will report into ";
const NO_AUTOMATIONS = "No automation yet.";
const NOT_ON_PAGE = "That item is not on this page.";
const NO_AGENT = "The app that ran this is not listed for you.";
const RUN = "Run";
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
  "Name",
  { label: "Events", fact: true },
  { label: "Next run", fact: true },
  { label: "Last run", fact: true },
];
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
  prompt: string;
  schedule: string;
  watching: string;
  provider: string;
  nextRunAt: string;
  lastRunAt: string;
  paused: boolean;
  mine: boolean;
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
type RunPayload = { name: string; status: Record<string, ObjectValue> };

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

/** A task reads as its own words, never as the name it is filed under. */
function taskEntry(row: IndexRow): Entry {
  const paused = row.paused === true;
  return {
    agent: row.agent_id,
    kind: TASK_KIND,
    name: row.name,
    label: said(row.description) || firstLine(said(row.prompt)) || row.name,
    conversation: said(row.conversation),
    prompt: said(row.prompt),
    schedule: said(row.schedule),
    watching: "",
    provider: "",
    nextRunAt: paused ? "" : said(row.next_run_at),
    lastRunAt: said(row.last_run_at),
    paused,
    mine: row.mine === true,
  };
}

/** A trigger reads as the thing it watches — the resource where it names one, else the feed and
 *  the streams of it that wake a conversation, never the account behind the feed. */
function triggerEntry(row: IndexRow): Entry {
  const resource = said(row.resource);
  const watched = [said(row.provider_label), ...said(row.streams).split(",")]
    .filter(Boolean)
    .join(" ");
  return {
    agent: row.agent_id,
    kind: TRIGGER_KIND,
    name: row.name,
    label: WATCHING + (resource || watched),
    conversation: said(row.conversation),
    prompt: "",
    schedule: "",
    watching: watched,
    provider: said(row.provider),
    nextRunAt: "",
    lastRunAt: said(row.last_run_at),
    paused: false,
    mine: row.mine === true,
  };
}

/** The read answers in one order, so the rows are drawn in the order they arrive. */
function entriesOf(payload: AutomationsPayload): Entry[] {
  return payload.objects.map((row) => (row.kind === TASK_KIND ? taskEntry(row) : triggerEntry(row)));
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

function runId(agent: string, run: string): string {
  return RUN_PREFIX + [agent, run].join(PART);
}

function runAt(id: string): { agent: string; name: string } {
  const [agent = "", name = ""] = id.slice(RUN_PREFIX.length).split(PART);
  return { agent, name };
}

/** When the automation runs next, as the Details pane states it: a task says its next fire, a
 *  trigger says the feed it wakes on, and a stopped task says it is stopped. */
function whenLine(entry: Entry): ReactNode {
  if (entry.paused) return PAUSED;
  if (entry.kind === TRIGGER_KIND) return entry.watching;
  return entry.nextRunAt ? <Moment at={entry.nextRunAt} /> : NEVER_RAN;
}

function RunMark({ status }: { status: string }) {
  const live = LIVE.has(status);
  return (
    <span
      role="img"
      aria-label={STATUS_WORDS[status] ?? status}
      className={cn(
        "mt-xs size-sm shrink-0 rounded-full",
        live
          ? "bg-live animate-pulse motion-reduce:animate-none"
          : status === "failed"
            ? "bg-blocked"
            : status === "cancelled"
              ? "bg-ink-faint"
              : "bg-edge",
      )}
    />
  );
}

/** What wakes the automation, as one mark in the list's own column: the clock a schedule fires on,
 *  or the feed a trigger watches. */
function EventMark({ entry }: { entry: Entry }) {
  if (entry.kind === TASK_KIND)
    return (
      <IconClockPlay
        role="img"
        aria-label={SCHEDULE}
        className="size-(--size-glyph) shrink-0 text-ink-soft"
      />
    );
  return (
    <span role="img" aria-label={entry.watching} className="flex">
      <BrandMark provider={entry.provider} className="size-icon" />
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
  deletes,
  onApply,
  onDelete,
}: {
  entry: Entry;
  deletes: boolean;
  onApply: (spec: Record<string, SpecValue>) => Promise<NoticeState>;
  onDelete: () => Promise<NoticeState>;
}) {
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  async function save(spec: Record<string, SpecValue>): Promise<NoticeState> {
    const outcome = await onApply(spec);
    setNotice(outcome.refused ? outcome : QUIET);
    return outcome;
  }
  async function remove(): Promise<void> {
    const outcome = await onDelete();
    setNotice(outcome.refused ? outcome : QUIET);
  }
  return (
    <div className="flex shrink-0 flex-col gap-2xl">
      <OutcomeNotice state={notice} />
      <SelfSaving
        label={NAME}
        value={entry.label}
        readOnly={!entry.mine}
        onSave={(description) => save({ description })}
      />
      <SelfSaving
        label={INSTRUCTIONS}
        value={entry.prompt}
        lines={INSTRUCTION_LINES}
        moreLines={INSTRUCTION_LINES_GROWN}
        readOnly={!entry.mine}
        onSave={(prompt) => save({ prompt })}
      />
      <WhenToRun
        schedule={entry.schedule}
        readOnly={!entry.mine}
        onSave={(schedule) => save({ schedule })}
      />
      <div className="flex flex-wrap gap-sm">
        <Button
          variant="row"
          disabled={!entry.mine}
          onClick={() => void save({ paused: !entry.paused })}
        >
          {entry.paused ? RESUME : PAUSE}
        </Button>
        {deletes ? (
          <ConfirmButton
            verb={DELETE}
            variant="row"
            disabled={!entry.mine}
            onClick={() => void remove()}
          />
        ) : null}
      </div>
    </div>
  );
}

/** A trigger fires on the feed it watches rather than on a clock, so its Details state what it is
 *  called and what wakes it. */
function TriggerInfo({ entry }: { entry: Entry }) {
  return (
    <div className="flex shrink-0 flex-col gap-2xl">
      <h3 className="m-0 text-subtitle font-medium">{entry.label}</h3>
      <Stated label={WHEN_TO_RUN}>{whenLine(entry)}</Stated>
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
  const id = automationId(lane);
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
              <RowLines
                rows={runs}
                rowKey={(run) => run.name}
                mark={(run) => <RunMark status={run.status} />}
                primary={(run) => <Moment at={run.createdAt} />}
                meta={(run) => [STATUS_WORDS[run.status] ?? run.status, firstLine(run.text)]}
                open={(run) => () =>
                  onPlace({ opens: opened(opens, runId(lane.agent, run.name), id) })
                }
              />
              <Pager
                payload={{ older: held.next_cursor }}
                onPlace={(stepped) => onPlace({ runs: stepped.after, opens })}
              />
            </>
          );
        }}
      </Panel>
    </div>
  );
}

/** `stops` names this run, so a sheet standing on an older run cannot end the conversation's newest
 *  turn. */
function RunSheet({
  id,
  opens,
  onPlace,
}: {
  id: string;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const { agent: agentId, name: run } = runAt(id);
  const agents = useAgents();
  const member = useMe();
  const shut = () => onPlace({ opens: closed(opens, id) });
  const [working, setWorking] = useState(true);
  const state = usePanelRead<RunPayload>(
    "/objects/" + TURN_KIND + "/" + run + "?agent=" + agentId,
    0,
    working ? WORKING_MS : RESTING_MS,
  );
  const status = state.phase === "ready" ? said(state.payload.status.status) : null;
  useEffect(() => {
    if (status !== null) setWorking(LIVE.has(status));
  }, [status]);
  const agent = agents.find((entry) => entry.id === agentId);
  return (
    <Sheet open title={RUN} onClose={shut}>
      {agent === undefined || member === null ? (
        <PanelEmpty>{NO_AGENT}</PanelEmpty>
      ) : (
        <Panel state={state}>
          {(held) => (
            <ChatPane
              agent={agent}
              member={member}
              conversationId={said(held.status.conversation)}
              readOnly
              stops={run}
              conversationOnly
            />
          )}
        </Panel>
      )}
    </Sheet>
  );
}

/** One automation's Details: the fields it runs under at the top, its runs listed under them. */
function DetailsSheet({
  lane,
  entry,
  deletes,
  after,
  opens,
  onPlace,
  onApply,
  onDelete,
}: {
  lane: AutomationLane;
  entry: Entry | null;
  deletes: boolean;
  after: string | undefined;
  opens: string[];
  onPlace: (place: Placement) => void;
  onApply: (entry: Entry, spec: Record<string, SpecValue>) => Promise<NoticeState>;
  onDelete: (entry: Entry) => Promise<NoticeState>;
}) {
  const shut = () => onPlace({ opens: closed(opens, automationId(lane)), runs: undefined });
  return (
    <Sheet open title={DETAILS} onClose={shut}>
      {entry === null ? (
        <PanelEmpty>{NOT_ON_PAGE}</PanelEmpty>
      ) : entry.kind === TASK_KIND ? (
        <TaskFields
          entry={entry}
          deletes={deletes}
          onApply={(spec) => onApply(entry, spec)}
          onDelete={async () => {
            const outcome = await onDelete(entry);
            if (!outcome.refused) shut();
            return outcome;
          }}
        />
      ) : (
        <TriggerInfo entry={entry} />
      )}
      <RunHistory
        lane={lane}
        conversation={entry?.conversation ?? ""}
        after={after}
        opens={opens}
        onPlace={onPlace}
      />
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
      spec: { description: description.trim(), prompt: prompt.trim(), schedule },
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

/** Ranked where the start screen's starters are, under the automations instructions. A card is
 *  drawn as a connection is: mark in its tile, name, line, and the act that starts it. */
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
      <CardGrid
        columns={3}
        rows={heroes}
        rowKey={(hero) => hero.ask}
        mark={{
          shape: "square",
          body: (hero) => <AgentIcon name={hero.mark} className="size-(--size-brand-mark)" />,
        }}
        primary={(hero) => hero.title}
        body={(hero) => hero.line}
        action={(hero) => (
          <Button variant="send" size="bar" onClick={start(hero)}>
            {CREATE}
          </Button>
        )}
        open={start}
      />
    </Section>
  );
}

/** The workspace's scheduled tasks and source triggers as one table, most recently run first,
 *  headed by three automations the member can start. One row is one automation: what it is called,
 *  what wakes it, when it runs next and when it last ran. A row opens the Details column beside the
 *  list — that automation's own fields, then its runs; pressing a run opens that run's transcript,
 *  read-only. The table pages on `after` and the open automation's runs page on `runs`, so one
 *  listing's step never moves the other's. */
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
              <>
                <DataTable
                  columns={COLUMNS}
                  rows={entries}
                  rowKey={(entry) => entry.agent + PART + entry.kind + PART + entry.name}
                  empty={NO_AUTOMATIONS}
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
                  {(entry) => (
                    <>
                      <Td>
                        <span className="flex items-center gap-sm">
                          {entry.paused ? (
                            <IconPlayerPause
                              role="img"
                              aria-label={PAUSED}
                              className="size-(--size-glyph) shrink-0 text-ink-soft"
                            />
                          ) : null}
                          <span className="truncate">{entry.label}</span>
                        </span>
                      </Td>
                      <Td>
                        <EventMark entry={entry} />
                      </Td>
                      <TdFact>{entry.nextRunAt ? <Moment at={entry.nextRunAt} /> : ""}</TdFact>
                      <TdFact>{entry.lastRunAt ? <Moment at={entry.lastRunAt} /> : NEVER_RAN}</TdFact>
                    </>
                  )}
                </DataTable>
                <Pager
                  payload={{ older: payload?.next_cursor }}
                  onPlace={(stepped) =>
                    onPlace({ after: stepped.after, opens: [], runs: undefined })
                  }
                />
              </>
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
          onDelete={(entry) =>
            submit(entry.agent, { verb: "delete", kind: entry.kind, name: entry.name })
          }
        />
      ) : top.startsWith(RUN_PREFIX) ? (
        <RunSheet key={top} id={top} opens={opens} onPlace={onPlace} />
      ) : null}
    </>
  );
}
