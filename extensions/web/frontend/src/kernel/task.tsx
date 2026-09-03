import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { IconChevronDown } from "@tabler/icons-react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Facts, type Fact } from "@/components/ui/facts";
import { Input, Label, Textarea } from "@/components/ui/field";
import { localMoment, wireMoment, type SpecValue } from "@/kernel/form";
import { QUIET, Section, type NoticeState } from "@/kernel/panel";
import { beside } from "@/kernel/slots";
import type { ObjectAddress, ObjectLink, ObjectValue } from "@/kernel/objects";
import {
  CADENCE_MODES,
  INTERVAL_HOURS,
  asMode,
  cadenceOf,
  clockFromValue,
  clockOf,
  cronOf,
  labelOf,
  timeValue,
  weekdayName,
  type Cadence,
  type CadenceMode,
} from "@/lib/cadence";
import { ownerLabel, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { ConversationLink } from "@/lib/conversationLink";
import { Moment } from "@/lib/moments";

/** How long after the last keystroke the pane commits what was typed. The pane carries no submit,
 *  so this is what saving is: long enough that a word being typed is one apply rather than six,
 *  short enough that a member who looks away has already saved. */
const SETTLE_MS = 800;

const SAVING = "Saving…";
const SAVED = "Saved";

/** What a member who did not write the task reads instead of an editable prompt. An admin may
 *  change when a task fires, never what it says, so the box states the prompt and offers no edit
 *  the kind would refuse. */
const CONTENT_IS_THE_CREATORS = "Only the member who wrote this task can change what it says.";
const PRIVATE_CONTENT = "This task's prompt is not visible to you.";
const NO_EXPIRY = "Leave this empty to let the task run until it is deleted.";
const REPORTS_TO = "Reports to";
const ITS_CHAT = "its chat";

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];

type Save = "rest" | "saving" | "saved";

/** One mutation of the record this pane stands on, as the record's own apply answers it. */
export type ApplySpec = (spec: Record<string, SpecValue>) => Promise<NoticeState>;

/** What saving is in a pane with no submit: the box holds what the member types, commits it a
 *  moment after the keystrokes stop, and commits again on the way out of the box — a save the
 *  member has to ask for is a save this pane has no control for. While the box is being typed in it
 *  holds its own text: the record is read again after every apply, and that read landing
 *  mid-keystroke would type over the member. The settle is also what holds a box that answers one
 *  change per typed digit to a single apply, rather than storing every value the member passed
 *  through on the way to the one they meant. */
function useSettled(value: string, onSave: (value: string) => Promise<NoticeState>) {
  const [held, setHeld] = useState(value);
  const [state, setState] = useState<Save>("rest");
  const stored = useRef(value);
  const typing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    stored.current = value;
    if (!typing.current) setHeld(value);
  }, [value]);

  useEffect(() => () => clearTimeout(timer.current ?? undefined), []);

  async function commit(next: string) {
    clearTimeout(timer.current ?? undefined);
    timer.current = null;
    typing.current = false;
    if (next === stored.current) return;
    setState("saving");
    const outcome = await onSave(next);
    setState(outcome.refused ? "rest" : "saved");
  }

  function typed(next: string) {
    setHeld(next);
    typing.current = true;
    setState("rest");
    clearTimeout(timer.current ?? undefined);
    timer.current = setTimeout(() => void commit(next), SETTLE_MS);
  }

  return { held, state, typed, commit };
}

/** A field that saves itself, on the settle every box in this pane commits by. `type` names the box
 *  the browser draws its own editor for — a moment on the member's own clock — which is as wide as
 *  the value it holds rather than as wide as the column. */
function SelfSaving({
  label,
  value,
  lines,
  type,
  readOnly,
  note,
  onSave,
}: {
  label: string;
  value: string;
  lines?: number;
  type?: "datetime-local";
  readOnly?: boolean;
  note?: string;
  onSave: (value: string) => Promise<NoticeState>;
}) {
  const id = useId();
  const { held, state, typed, commit } = useSettled(value, onSave);

  const box = lines ? (
    <Textarea
      id={id}
      rows={lines}
      value={held}
      readOnly={readOnly}
      className="max-w-full"
      onChange={(event) => typed(event.target.value)}
      onBlur={() => void commit(held)}
    />
  ) : (
    <Input
      id={id}
      type={type}
      value={held}
      readOnly={readOnly}
      className={type ? "max-w-fit" : "max-w-full"}
      onChange={(event) => typed(event.target.value)}
      onBlur={() => void commit(held)}
    />
  );

  return (
    <div className="flex flex-col gap-2xs">
      <div className="flex items-center gap-sm">
        <Label htmlFor={id} className="min-w-0 flex-1 truncate font-normal text-ink-soft">
          {label}
        </Label>
        <span aria-live="polite" className="shrink-0 text-small text-ink-soft">
          {state === "saving" ? SAVING : state === "saved" ? SAVED : ""}
        </span>
      </div>
      {box}
      {note ? <p className="m-0 text-small text-ink-soft">{note}</p> : null}
    </div>
  );
}

/** A compact dropdown of one closed set, with a tick against what is picked. It is the pane's one
 *  shape for a choice: what is chosen is the control's own words, so a member reads the schedule
 *  off the pill rather than out of a form. */
function Pill({
  label,
  said,
  value,
  options,
  onPick,
}: {
  label: string;
  said: string;
  value: string;
  options: { value: string; label: string }[];
  onPick: (value: string) => void;
}) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="row" size="bar" aria-label={label}>
          {said}
          <IconChevronDown aria-hidden className="size-(--size-glyph)" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={value} onValueChange={onPick}>
          {options.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The chip that says where the task reports, and presses through to it. Where a task fires is
 *  fixed when it is created — the kind binds the applying turn's conversation and every update
 *  preserves it — so this states that binding rather than offering a choice of it. */
function ReportsTo({ said, onOpen }: { said: string; onOpen: ((aside: boolean) => void) | null }) {
  const worn = `${REPORTS_TO} ${said}`;
  if (onOpen === null) {
    return (
      <span className={cn(buttonVariants({ variant: "row", size: "bar" }), "text-ink-soft")}>
        {worn}
      </span>
    );
  }
  return (
    <Button
      variant="row"
      size="bar"
      onClick={(event) => onOpen(beside(event))}
      onAuxClick={(event) => {
        if (!beside(event)) return;
        onOpen(true);
      }}
    >
      {worn}
    </Button>
  );
}

/** The cadence pills: which shape the schedule takes, and the one detail that shape asks for. A
 *  pick off a menu is one deliberate choice, so it applies at once; the time is typed, and a `time`
 *  box answers one change per completed segment, so it commits on the settle every typed box in
 *  this pane commits by — 09:00 retyped as 14:00 through 01:00 stores 14:00 alone. Cron is written
 *  in UTC off the clock in front of the member. */
function CadencePills({
  cadence,
  cron,
  offset,
  onPick,
}: {
  cadence: Cadence;
  cron: string;
  offset: number;
  onPick: (next: Cadence) => Promise<NoticeState>;
}) {
  const clock = clockOf(cadence);
  const time = useSettled(clock === null ? "" : timeValue(clock.hour, clock.minute), (value) => {
    const picked = clockFromValue(value);
    return picked === null ? Promise.resolve(QUIET) : onPick({ ...cadence, ...picked });
  });
  return (
    <>
      <Pill
        label="Schedule"
        said={labelOf(cadence)}
        value={cadence.mode}
        options={CADENCE_MODES.map((offer) => ({ value: offer.mode, label: offer.label }))}
        onPick={(mode) => void onPick(asMode(cadence, mode as CadenceMode, cron, offset))}
      />
      {cadence.mode === "interval" ? (
        <Pill
          label="Interval"
          said={intervalLabel(cadence.hours)}
          value={String(cadence.hours)}
          options={INTERVAL_HOURS.map((hours) => ({
            value: String(hours),
            label: intervalLabel(hours),
          }))}
          onPick={(hours) => void onPick({ mode: "interval", hours: Number(hours) })}
        />
      ) : null}
      {cadence.mode === "weekly" ? (
        <Pill
          label="Day"
          said={weekdayName(cadence.weekday)}
          value={String(cadence.weekday)}
          options={WEEKDAYS.map((day) => ({ value: String(day), label: weekdayName(day) }))}
          onPick={(day) => void onPick({ ...cadence, weekday: Number(day) })}
        />
      ) : null}
      {clock === null ? null : (
        <Input
          type="time"
          aria-label="Time"
          value={time.held}
          className="h-(--size-control) w-fit rounded-full py-0 text-label"
          onChange={(event) => time.typed(event.target.value)}
          onBlur={() => void time.commit(time.held)}
        />
      )}
    </>
  );
}

function intervalLabel(hours: number): string {
  return hours === 1 ? "Every hour" : `Every ${hours} hours`;
}

/** The scheduled task's own pane: what the task says, when it fires, and where it reports — each
 *  one the control that changes it rather than a fact beside a form. Nothing here has a submit: a
 *  prompt commits itself once typing settles, and a pill applies on the pick, so the pane a member
 *  reads and the pane they change are one screen.
 *
 *  Two choices this shape offers elsewhere are not the kind's to make. Where a task reports is
 *  bound to the conversation that created it and no update moves it, so the chat is stated and not
 *  picked; and the kind holds no notification preference at all, so nothing here claims one. */
export function ScheduledTaskPane({
  agentId,
  spec,
  status,
  summary,
  links,
  onApply,
  onOpen,
}: {
  agentId: string;
  spec: Record<string, ObjectValue> | null;
  status: Record<string, ObjectValue>;
  summary: string;
  links: ObjectLink[];
  onApply: ApplySpec;
  onOpen: (at: ObjectAddress, aside: boolean) => void;
}) {
  const viewer = useViewer();
  const [firing, setFiring] = useState(false);
  const mine = status.mine === true;
  const paused = status.paused === true || spec?.paused === true;
  const origin = typeof status.origin === "string" && status.origin ? status.origin : ITS_CHAT;
  const reports = links.find((link) => link.relation === "reports_to") ?? null;
  const schedule = typeof spec?.schedule === "string" ? spec.schedule : "";
  const offset = new Date().getTimezoneOffset();
  /** Whether the schedule is being read as the cron itself. Every other shape is derived from the
   *  stored cron, but `custom` is no cron of its own: `cronOf` writes back exactly what `cadenceOf`
   *  read, so applying the pick would store the schedule the task already has and the pill would
   *  state its old cadence again. So the pick is held here, it opens the cron box, and the box is
   *  what changes the schedule. */
  const [asCron, setAsCron] = useState(false);
  const cadence: Cadence = asCron
    ? { mode: "custom", cron: schedule }
    : cadenceOf(schedule, offset);
  const facts: Fact[] = [
    { label: "Next run", value: moment(status.next_run_at) },
    { label: "Last run", value: lastRun(status) },
    { label: "Created by", value: ownerLabel(email(status.owner_email), viewer) },
  ];
  if (typeof status.conversation === "string" && status.conversation) {
    facts.push({ label: "Conversation", value: <ConversationLink id={status.conversation} /> });
  }
  const save = (cron: string) => onApply({ schedule: cron });
  return (
    <>
      <Section title="Task">
        <div className="flex flex-col gap-2xl">
          {spec === null ? (
            <p className="m-0 text-ink-soft">
              {summary ? `${summary} · ${PRIVATE_CONTENT}` : PRIVATE_CONTENT}
            </p>
          ) : (
            <>
              <SelfSaving
                label="Prompt"
                value={typeof spec.prompt === "string" ? spec.prompt : ""}
                lines={8}
                readOnly={!mine}
                note={mine ? undefined : CONTENT_IS_THE_CREATORS}
                onSave={(prompt) => onApply({ prompt })}
              />
              <SelfSaving
                label="Description"
                value={typeof spec.description === "string" ? spec.description : ""}
                readOnly={!mine}
                onSave={(description) => onApply({ description })}
              />
              <div className="flex flex-wrap items-center gap-sm">
                <CadencePills
                  cadence={cadence}
                  cron={schedule}
                  offset={offset}
                  onPick={(next) => {
                    setAsCron(next.mode === "custom");
                    return next.mode === "custom"
                      ? Promise.resolve(QUIET)
                      : save(cronOf(next, offset));
                  }}
                />
                <ReportsTo
                  said={origin}
                  onOpen={
                    reports === null || !reports.opens
                      ? null
                      : (aside) =>
                          onOpen({ agent: agentId, kind: reports.kind, name: reports.name }, aside)
                  }
                />
              </div>
              {cadence.mode === "custom" ? (
                <SelfSaving label="Cron (UTC)" value={schedule} onSave={save} />
              ) : null}
              <SelfSaving
                label="Expires"
                type="datetime-local"
                value={typeof spec.expires_at === "string" ? localMoment(spec.expires_at) : ""}
                note={NO_EXPIRY}
                onSave={(local) => {
                  // The moment the box holds has to be one the runner could act on, or the box
                  // holds nothing the kind should store: an empty box is the clear every other
                  // value is typed on the way to, and a past instant is swept with the task
                  // itself. Either answers the quiet that leaves the stored value standing, as
                  // the sibling time box does for a half-typed clock.
                  if (local !== "" && new Date(local).getTime() <= Date.now()) {
                    return Promise.resolve(QUIET);
                  }
                  return onApply({ expires_at: local ? wireMoment(local) : null });
                }}
              />
            </>
          )}
        </div>
      </Section>
      <Section title="Runs">
        <Facts rows={facts} />
        <div className="mt-2xl flex flex-wrap gap-sm">
          <Button
            variant="row"
            busy={firing}
            onClick={async () => {
              setFiring(true);
              // A paused row is never claimed, so the fire has to carry the resume with it or it is
              // stated as saved and never happens. The button says both acts for that reason.
              await onApply(paused ? { run_now: true, paused: false } : { run_now: true });
              setFiring(false);
            }}
          >
            {paused ? "Resume and run now" : "Run now"}
          </Button>
          <Button variant="row" onClick={() => void onApply({ paused: !paused })}>
            {paused ? "Resume" : "Pause"}
          </Button>
        </div>
      </Section>
    </>
  );
}

function email(value: ObjectValue | undefined): string | null {
  return typeof value === "string" && value ? value : null;
}

function moment(value: ObjectValue | undefined): ReactNode {
  return typeof value === "string" && value ? <Moment at={value} /> : "—";
}

/** The run a member reads together with how it went. A task that has never fired states no ending,
 *  and one whose turn the workspace no longer holds states the moment alone. */
function lastRun(status: Record<string, ObjectValue>): ReactNode {
  const at = status.last_run_at;
  if (typeof at !== "string" || !at) return "—";
  const said = status.last_run_status;
  return (
    <>
      <Moment at={at} />
      {typeof said === "string" && said ? ` · ${said}` : null}
    </>
  );
}
