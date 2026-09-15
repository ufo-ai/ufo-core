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
  MONTH_DAY_NUMBERS,
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

const SETTLE_MS = 800;

const SAVING = "Saving…";
const SAVED = "Saved";
const EXPAND = "Expand";
const COLLAPSE = "Collapse";

const CONTENT_IS_THE_CREATORS = "Only the member who wrote this task can change what it says.";
const PRIVATE_CONTENT = "This task's prompt is not visible to you.";
const NO_EXPIRY = "Leave this empty to let the task run until it is deleted.";
const REPORTS_TO = "Reports to";
const ITS_CHAT = "its chat";

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];

type Save = "rest" | "saving" | "saved";

export type ApplySpec = (spec: Record<string, SpecValue>) => Promise<NoticeState>;

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

/** One field that saves what it is left holding. `moreLines` gives a multiline field a press that
 *  grows it for a longer edit and shrinks it back. */
export function SelfSaving({
  label,
  value,
  lines,
  moreLines,
  type,
  readOnly,
  note,
  onSave,
}: {
  label: string;
  value: string;
  lines?: number;
  moreLines?: number;
  type?: "datetime-local";
  readOnly?: boolean;
  note?: string;
  onSave: (value: string) => Promise<NoticeState>;
}) {
  const id = useId();
  const { held, state, typed, commit } = useSettled(value, onSave);
  const [grown, setGrown] = useState(false);

  const box = lines ? (
    <Textarea
      id={id}
      rows={grown && moreLines ? moreLines : lines}
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
        <Label htmlFor={id} className="min-w-0 flex-1 truncate font-normal text-small text-ink-quiet">
          {label}
        </Label>
        <span aria-live="polite" className="shrink-0 text-small text-ink-soft">
          {state === "saving" ? SAVING : state === "saved" ? SAVED : ""}
        </span>
        {lines && moreLines ? (
          <Button variant="quiet" size="bar" onClick={() => setGrown(!grown)}>
            {grown ? COLLAPSE : EXPAND}
          </Button>
        ) : null}
      </div>
      {box}
      {note ? <p className="m-0 text-small text-ink-soft">{note}</p> : null}
    </div>
  );
}

export function Pill({
  label,
  said,
  value,
  options,
  disabled,
  onPick,
}: {
  label: string;
  said: string;
  value: string;
  options: { value: string; label: string }[];
  disabled?: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="row" size="bar" aria-label={label} disabled={disabled}>
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

function CadencePills({
  cadence,
  cron,
  offset,
  disabled,
  onPick,
}: {
  cadence: Cadence;
  cron: string;
  offset: number;
  disabled?: boolean;
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
        disabled={disabled}
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
          disabled={disabled}
          onPick={(hours) => void onPick({ mode: "interval", hours: Number(hours) })}
        />
      ) : null}
      {cadence.mode === "monthly" ? (
        <Pill
          label="Day of month"
          said={dayOfMonthLabel(cadence.day)}
          value={String(cadence.day)}
          options={MONTH_DAY_NUMBERS.map((day) => ({
            value: String(day),
            label: dayOfMonthLabel(day),
          }))}
          disabled={disabled}
          onPick={(day) => void onPick({ ...cadence, day: Number(day) })}
        />
      ) : null}
      {cadence.mode === "weekly" ? (
        <Pill
          label="Day"
          said={weekdayName(cadence.weekday)}
          value={String(cadence.weekday)}
          options={WEEKDAYS.map((day) => ({ value: String(day), label: weekdayName(day) }))}
          disabled={disabled}
          onPick={(day) => void onPick({ ...cadence, weekday: Number(day) })}
        />
      ) : null}
      {clock === null ? null : (
        <Input
          type="time"
          aria-label="Time"
          value={time.held}
          disabled={disabled}
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

function dayOfMonthLabel(day: number): string {
  return `Day ${day}`;
}

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
  const contentEditable = status.content_editable === true;
  const scheduleEditable = status.schedule_editable === true;
  const pausable = status.pausable === true;
  const resumable = status.resumable === true;
  const runnable = status.runnable === true;
  const paused = status.paused === true || spec?.paused === true;
  const origin = typeof status.origin === "string" && status.origin ? status.origin : ITS_CHAT;
  const reports = links.find((link) => link.relation === "reports_to") ?? null;
  const schedule = typeof spec?.schedule === "string" ? spec.schedule : "";
  const offset = new Date().getTimezoneOffset();
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
                readOnly={!contentEditable}
                note={contentEditable ? undefined : CONTENT_IS_THE_CREATORS}
                onSave={(prompt) => onApply({ prompt })}
              />
              <SelfSaving
                label="Description"
                value={typeof spec.description === "string" ? spec.description : ""}
                readOnly={!contentEditable}
                onSave={(description) => onApply({ description })}
              />
              <div className="flex flex-wrap items-center gap-sm">
                <CadencePills
                  cadence={cadence}
                  cron={schedule}
                  offset={offset}
                  disabled={!scheduleEditable}
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
                <SelfSaving
                  label="Cron (UTC)"
                  value={schedule}
                  readOnly={!scheduleEditable}
                  onSave={save}
                />
              ) : null}
              <SelfSaving
                label="Expires"
                type="datetime-local"
                value={typeof spec.expires_at === "string" ? localMoment(spec.expires_at) : ""}
                readOnly={!scheduleEditable}
                note={NO_EXPIRY}
                onSave={(local) => {
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
            disabled={!runnable}
            onClick={async () => {
              setFiring(true);
              await onApply(paused ? { run_now: true, paused: false } : { run_now: true });
              setFiring(false);
            }}
          >
            {paused ? "Resume and run now" : "Run now"}
          </Button>
          <Button
            variant="row"
            disabled={paused ? !resumable : !pausable}
            onClick={() => void onApply({ paused: !paused })}
          >
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
