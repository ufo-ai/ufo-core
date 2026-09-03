import {
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconSquareRoundedCheckFilled,
  IconSquareRoundedFilled,
} from "@tabler/icons-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Notice, Panel, QUIET, usePanelRead, type NoticeState } from "@/kernel/panel";
import { BASE, postIntent } from "@/lib/api";
import { openConsentWindow } from "@/lib/consent";
import { newChatHash } from "@/lib/route";
import { agentName } from "@/lib/agentName";
import { setPendingAsk } from "@/lib/pendingAsk";
import { ProviderGlyph } from "@/lib/providerGlyph";
import { cn } from "@/lib/cn";
import { navigate } from "@/lib/router";
import type { Agent } from "@/lib/types";

/** The recurrence an app offers to arm its schedule with. `hour` null is hourly, which names no
 *  wall-clock time; `weekdays` empty is every day. */
export type SetupCadence = { hour: number | null; minute: number; weekdays: number[] };

export type SetupSchedule = { name: string; prompt: string; cadences: SetupCadence[] };

/** The setup read, as it arrives. Every field is optional here because nothing crosses the wire
 *  checked: a screen that indexed a list the answer did not carry would blank itself over a payload
 *  that merely declared nothing. `own_page` is whether this workspace has built the app its page. */
export type SetupState = {
  connectors?: {
    provider: string;
    label: string;
    summary: string;
    granted: boolean;
    required: boolean;
  }[];
  credentials?: { label: string; filled: boolean; required: boolean }[];
  standing?: { kind: string; armed: boolean; required: boolean; schedule: string | null }[];
  schedule?: SetupSchedule | null;
  instructions?: string;
  own_page?: boolean;
};

const SCHEDULE_KIND = "scheduled_task";

const CONNECT = "Connect";
const CONNECTING = "Connecting";
const CONNECTED = "Connected";

/* A step is titled by what it asks of the member and says nothing about which account answers it,
   because the same question is put whatever the app happens to be connected to. The line under it
   is where the provider is named, and the act carries its verb. */
const ACCOUNT_STEP = "Choose your account";
const CREDENTIAL_STEP = "Add the workspace credential";
const SCHEDULE_STEP = "Choose when it runs";
const TRIGGER_STEP = "Choose what wakes it";

const SCHEDULE_NOTE = "Pick how often it runs without being asked.";
const TRIGGER_NOTE = "A change in a source starts a run.";
const OWN_CADENCE = "Say when, in your own words";
const CADENCE_ASK = "Run on this cadence:";
const NOT_INSTALLED = "Not installed";
const FILLED = "Filled";
const NOT_FILLED = "Not filled";
const ARMED = "Set";
const CHOOSE_WHEN = "Choose when it runs";
const SET_UP = "Set up";
/** The gutter the status mark stands in, so the name, the line under it and the answers all share
 *  one left edge. It is the mark and the gap beside it, named where both are. */
const STEP_INSET = "pl-[calc(var(--size-glyph)+var(--spacing-sm))]";
const ANOTHER_CADENCE = "Something else";

const BUILD_APP = "Build app";
const SET_UP_APP = "Set up your";
/** What the press types on the member's behalf, into the composer they send it from. It names the
 *  skill and stops: the steps live in the skill, and an ask repeating them would be a second copy
 *  of the procedure that drifts the first time either changes. */
const BUILD_ASK = "Build this workspace its own version of your page. Load your homepage skill and "
  + "follow it.";

const BLOCKED_POPUP = "Your browser blocked the window. Allow pop-ups and press Connect again.";
const CONNECT_REFUSED = "The connect did not open a consent page. Try again.";
const CONSENT_ELSEWHERE = "The consent window closed before the link arrived. Press Connect again.";

const EVERY_HOUR = "every hour";
const EVERY_DAY = "every day";
const EVERY_WEEKDAY = "every weekday";
const WORKING_WEEK = [1, 2, 3, 4, 5];
const MINUTES_IN_DAY = 24 * 60;
const WEEK = 7;

/** The cron this cadence fires on, in UTC.
 *
 *  Cron stores UTC and only the browser knows the member's offset, so this is the one place the two
 *  meet. An hourly cadence names no wall-clock time, so there is nothing to convert. An hour that
 *  crosses midnight takes its weekdays with it — a Monday 9pm local that is Tuesday 03:00 UTC fires
 *  on Tuesday, and a set that did not move would fire a day early every week. */
export function cronFor(cadence: SetupCadence, offsetMinutes: number): string {
  if (cadence.hour === null) return `${cadence.minute} * * * *`;
  const local = cadence.hour * 60 + cadence.minute;
  const utc = local + offsetMinutes;
  const shift = Math.floor(utc / MINUTES_IN_DAY);
  const settled = ((utc % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
  const days = cadence.weekdays.length
    ? cadence.weekdays.map((day) => (((day + shift) % WEEK) + WEEK) % WEEK).sort()
    : [];
  const field = days.length ? days.join(",") : "*";
  return `${settled % 60} ${Math.floor(settled / 60)} * * ${field}`;
}

/** What a cadence is called, in the member's own clock.
 *
 *  It names the recurrence and nothing else. A schedule's first fire is its next cron occurrence —
 *  `next_fire` advances strictly past now and the kind runs nothing one-shot — so a label reading
 *  "Now, and every weekday at 9:00" promised a run that never came. */
export function labelOf(cadence: SetupCadence): string {
  if (cadence.hour === null) return EVERY_HOUR;
  const time = new Date(2026, 0, 5, cadence.hour, cadence.minute).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  const days =
    cadence.weekdays.length === 0
      ? EVERY_DAY
      : [...cadence.weekdays].sort().join(",") === WORKING_WEEK.join(",")
        ? EVERY_WEEKDAY
        : cadence.weekdays
            .map((day) =>
              new Date(2026, 0, 4 + day).toLocaleDateString(undefined, { weekday: "long" }),
            )
            .join(", ");
  return `${days} at ${time}`;
}

/** The cadence a cron stands for, in the member's own clock — the read `cronFor` writes back, so a
 *  schedule the member armed is recognised as the offer they took. Null where the cron is not one
 *  of the shapes an offer converts to: an app's own composition can name anything, and a guess at
 *  what it meant would put words on the screen the schedule does not hold. */
export function cadenceOf(cron: string, offsetMinutes: number): SetupCadence | null {
  const field = cron.trim().split(/\s+/);
  if (field.length !== 5) return null;
  const [minute, hour, day, month, weekday] = field;
  if (day !== "*" || month !== "*") return null;
  const past = Number(minute);
  if (!Number.isInteger(past)) return null;
  if (hour === "*") return weekday === "*" ? { hour: null, minute: past, weekdays: [] } : null;
  const struck = Number(hour);
  if (!Number.isInteger(struck)) return null;
  const local = struck * 60 + past - offsetMinutes;
  const shift = Math.floor(local / MINUTES_IN_DAY);
  const settled = ((local % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
  const days =
    weekday === "*"
      ? []
      : weekday
          .split(",")
          .flatMap((one) => (one.includes("-") ? span(one) : [Number(one)]))
          .map((day) => (((day + shift) % WEEK) + WEEK) % WEEK)
          .sort();
  if (days.some((day) => !Number.isInteger(day))) return null;
  return { hour: Math.floor(settled / 60), minute: settled % 60, weekdays: days };
}

/** Cron's own range, which a member's app may write where the offers write a list. */
function span(range: string): number[] {
  const [from, to] = range.split("-").map(Number);
  if (!Number.isInteger(from) || !Number.isInteger(to) || to < from) return [NaN];
  return Array.from({ length: to - from + 1 }, (_, step) => from + step);
}

/** Why an account is being asked for, in the words the connect tiles already use for it. The
 *  catalog states what an agent does with the provider, so the line reads as the reason to connect
 *  it; a provider the catalog does not curate is named without one. */
function connectNote(label: string, summary: string): string {
  if (!summary) return `Connect ${label} so this app can work from your account.`;
  return `Connect ${label} to ${summary[0].toLowerCase()}${summary.slice(1)}`;
}

type SetupStep = {
  key: string;
  title: string;
  note: string;
  required: boolean;
  done: boolean;
  body: ReactNode;
};

function SetupStepper({ steps }: { steps: SetupStep[] }) {
  const baseId = useId();
  const [opened, setOpened] = useState<string | null>(null);
  const previous = useRef(new Map<string, boolean>());
  const active =
    opened && steps.some((step) => step.key === opened)
      ? opened
      : (steps.find((step) => !step.done)?.key ?? steps[0]?.key ?? null);

  useEffect(() => {
    if (opened !== null) {
      const index = steps.findIndex((candidate) => candidate.key === opened);
      const step = steps[index];
      if (previous.current.get(opened) === false && step?.done) {
        setOpened(steps[index + 1]?.key ?? null);
      }
    }
    previous.current = new Map(steps.map((step) => [step.key, step.done]));
  }, [opened, steps]);

  return (
    <div className="flex flex-col gap-2xl">
      {steps.map((step, index) => {
        const open = step.key === active;
        const contentId = `${baseId}-step-${index}`;
        const Status = step.done ? IconSquareRoundedCheckFilled : IconSquareRoundedFilled;
        const Chevron = open ? IconChevronDown : IconChevronRight;
        return (
          <Collapsible
            key={step.key}
            className="rounded-(--radius-answer) bg-fill px-2xl py-sm"
            open={open}
            onOpenChange={(next) => {
              if (next) setOpened(step.key);
            }}
          >
            <CollapsibleTrigger
              type="button"
              aria-expanded={open}
              aria-controls={contentId}
              className="flex h-(--size-record) w-full items-center gap-sm text-left"
            >
              <Status
                aria-hidden="true"
                className={
                  "size-(--size-glyph) shrink-0 " + (step.done ? "text-ink" : "text-ink-faint")
                }
                stroke={1.25}
              />
              <span className="min-w-0 flex-1 text-label font-medium tracking-ui">
                {step.title}
                {step.required ? <span className="text-ink-soft">*</span> : null}
              </span>
              <Chevron
                aria-hidden="true"
                className="size-(--size-glyph) shrink-0 text-ink-soft"
                stroke={1.25}
              />
            </CollapsibleTrigger>
            <CollapsibleContent id={contentId}>
              {/* Everything under the name stands where the name stands, past the mark's own
                  gutter, so the step reads as one column rather than as a title with the rest
                  hanging off its left. */}
              <div className={cn(STEP_INSET, "flex flex-col items-start gap-2xl pb-sm")}>
                {step.note ? (
                  <span className="text-fine leading-chrome text-ink-soft">{step.note}</span>
                ) : null}
                {step.body}
              </div>
            </CollapsibleContent>
          </Collapsible>
        );
      })}
    </div>
  );
}

/** Wiring one app: the accounts it works from, the workspace installs it needs, and the standing
 *  order that gives it an occasion to run.
 *
 *  It is a screen and not a band on the app's own page, and that is the whole of why the acts here
 *  work. A page is framed cross-origin and speaks with the viewer's whole session, so the bridge
 *  fences it by name — and the two acts an unwired app most needs are exactly the ones that cannot
 *  cross: connecting an account is a consent handoff, and forking the page is model work. Here they
 *  are ordinary portal acts.
 *
 *  An app arrives at this screen instead of its page, which is what the page is spared: with no
 *  account there is nothing real to draw, and rows of sample data in their place would be showing a
 *  member someone else's app and calling it theirs. The page draws what the app is and what it has
 *  done; this screen exists so it never has to draw what it would be. */
export function AgentSetup({
  agent,
  onBuilt,
}: {
  agent: Agent;
  /** Told when this read says the workspace has built the app its page. The boot roster carries that
   *  fact for the shell, and a build lands long after boot — so the roster is read again, and the
   *  app stops being one the workspace is still building. */
  onBuilt: () => void;
}) {
  const [reloads, setReloads] = useState(0);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [arming, setArming] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [watching, setWatching] = useState<string | null>(null);
  const consent = useRef<Window | null>(null);
  const state = usePanelRead<SetupState>("/agents/" + agent.id + "/setup", reloads);
  const built = state.phase === "ready" && state.payload.own_page === true;
  useEffect(() => {
    if (built) onBuilt();
  }, [built, onBuilt]);
  /** The turn the connect admitted, watched for the link it mints.
   *
   *  A consent link is minted per speaking member at stream time — never in a transcript and never
   *  in the intent's own answer — so the turn's stream is where it arrives. A turn that ends
   *  without one is a refusal the member has to read.
   *
   *  Every path out of this effect releases the controls. The stream closes on the first of the two
   *  frames, so a control left waiting on the other would wait for the life of the screen. */
  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    const release = () => {
      stream.close();
      setConnecting(null);
      setWatching(null);
      setReloads((count) => count + 1);
    };
    stream.addEventListener("connect", () => {
      if (consent.current) consent.current.location.href = BASE + "/turns/" + watching + "/connect";
      else setNotice({ text: CONSENT_ELSEWHERE, refused: true });
      consent.current = null;
      release();
    });
    stream.addEventListener("terminal", () => {
      consent.current?.close();
      consent.current = null;
      setNotice({ text: CONNECT_REFUSED, refused: true });
      release();
    });
    stream.onerror = release;
    return () => stream.close();
  }, [watching]);

  async function connect(provider: string) {
    setNotice(QUIET);
    /* Opened on the press, before the round trip that mints the link: a window opened afterwards
       has lost the gesture the browser opens one for. */
    const opened = openConsentWindow();
    if (!opened) {
      setNotice({ text: BLOCKED_POPUP, refused: true });
      return;
    }
    consent.current = opened;
    setConnecting(provider);
    /* The grant is this app's own: the intent names this agent, so the account the member picks
       binds here rather than to whichever app they last opened. */
    const outcome = await postIntent(agent.id, {
      verb: "connect",
      kind: "connection",
      name: provider,
      spec: { shared: false },
    });
    if (!outcome.applied || !outcome.turn_id) {
      opened.close();
      consent.current = null;
      setConnecting(null);
      setNotice({ text: outcome.message, refused: true });
      return;
    }
    setWatching(outcome.turn_id);
  }

  /** Build the app's page from its own sources, and watch it happen.
   *
   *  It stands below the setup list because it is what the list is for: the todos are what the app
   *  needs before it can read anything, and this is the act that turns a wired app into its screen.
   *  It is offered whether or not they are settled — an app builds a thinner page from fewer
   *  sources, and a member who wants to see it now is not told to come back later.
   *
   *  The press opens the work rather than spending it: the ask rides to the app's own new chat and
   *  stands in the composer, so the member reads what they are about to ask for and sends it. The
   *  build then runs in a conversation they can watch and correct. */
  function buildApp() {
    setPendingAsk(agent.id, BUILD_ASK, false);
    navigate(newChatHash(agent.id));
  }

  /** Hand the app's own instructions to the composer, where the member sends them and the app
   *  drives the rest. The words are the app's declaration, so what is asked for is what the app
   *  said it needs, not a second description of it written here. */
  function ask(instructions: string) {
    setPendingAsk(agent.id, instructions, false);
    navigate(newChatHash(agent.id));
  }

  /** A cadence the offers do not cover, in the member's own words. Only they know what they meant
   *  by it, so the words go to the app rather than to a parser here: it composes the schedule and
   *  says back what it armed, in the conversation the member sends them from. */
  function sayCadence(said: string) {
    setPendingAsk(agent.id, `${CADENCE_ASK} ${said}`, false);
    navigate(newChatHash(agent.id));
  }

  /** Arm the app's declared schedule on this cadence, or move the cadence of the order it already
   *  holds.
   *
   *  The prompt rides on the create alone. The row that exists holds it already, and the kind holds
   *  content to the task's creator: a spec that set `prompt` again is refused for every admin who
   *  did not arm it, on a step that only ever asked them how often. Omitting it preserves what the
   *  order runs. */
  async function arm(schedule: SetupSchedule, cadence: SetupCadence, armed: boolean) {
    setNotice(QUIET);
    setArming(schedule.name);
    const cron = cronFor(cadence, new Date().getTimezoneOffset());
    const outcome = await postIntent(agent.id, {
      verb: "apply",
      kind: SCHEDULE_KIND,
      name: schedule.name,
      spec: armed ? { schedule: cron } : { schedule: cron, prompt: schedule.prompt },
    });
    setArming(null);
    if (!outcome.applied) {
      setNotice({ text: outcome.message, refused: true });
      return;
    }
    setReloads((count) => count + 1);
  }

  return (
    <Panel state={state} shape="form">
      {(payload) => {
        const connectors = payload.connectors ?? [];
        const credentials = payload.credentials ?? [];
        const standing = payload.standing ?? [];
        const schedule = payload.schedule ?? null;
        const steps: SetupStep[] = [
          ...connectors.map((connector, index) => {
            const title = `${CONNECT} ${connector.label}`;
            return {
              key: `connector:${connector.provider}:${index}`,
              title: ACCOUNT_STEP,
              note: connectNote(connector.label, connector.summary),
              required: connector.required,
              done: connector.granted,
              body: connector.granted ? (
                CONNECTED
              ) : (
                <Button
                    variant="outline"
                    size="bar"
                    className="my-sm bg-surface text-ink"
                    busy={connecting === connector.provider}
                    disabled={connecting !== null && connecting !== connector.provider}
                    onClick={() => connect(connector.provider)}
                  >
                    <ProviderGlyph
                      provider={connector.provider}
                      className="size-(--size-glyph) text-ink"
                    />
                    {connecting === connector.provider ? CONNECTING : title}
                  </Button>
              ),
            };
          }),
          ...credentials.map((credential, index) => ({
            key: `credential:${credential.label}:${index}`,
            title: CREDENTIAL_STEP,
            note: `${credential.label} is filled once for the whole workspace.`,
            required: credential.required,
            done: credential.filled,
            body: credential.filled ? FILLED : NOT_FILLED,
          })),
          ...standing.map((order, index) => {
            const scheduled = order.kind === SCHEDULE_KIND && schedule !== null;
            const title = scheduled ? CHOOSE_WHEN : SET_UP;
            return {
              key: `standing:${order.kind}:${index}`,
              title: scheduled ? SCHEDULE_STEP : TRIGGER_STEP,
              note: scheduled ? SCHEDULE_NOTE : TRIGGER_NOTE,
              required: order.required,
              done: order.armed,
              body: standingValue(order, schedule, title, payload.instructions ?? ""),
            };
          }),
        ];
        return (
          <div className="flex flex-col gap-2xl">
            {/* What the app is stands over what it still needs, in the column that holds them
                both: a member meets the app before the list of what it is waiting on. */}
            <div className="flex flex-col gap-2xs">
              <h1 className="m-0 text-subtitle leading-chrome font-medium">
                {`${SET_UP_APP} ${agentName(agent.name)} app`}
              </h1>
              {agent.purpose ? (
                <p className="m-0 max-w-hint text-label leading-chrome text-ink-soft">
                  {agent.purpose}
                </p>
              ) : null}
            </div>
            <SetupStepper steps={steps} />
            {notice.text ? (
              <Notice tone={notice.refused ? "attention" : "quiet"}>{notice.text}</Notice>
            ) : null}
            <Button variant="send" size="bar" className="self-start" onClick={buildApp}>
              {BUILD_APP}
            </Button>
          </div>
        );
      }}
    </Panel>
  );

  function standingValue(
    order: NonNullable<SetupState["standing"]>[number],
    schedule: SetupSchedule | null,
    title: string,
    instructions: string,
  ) {
    if (order.armed && (order.kind !== SCHEDULE_KIND || schedule === null)) return ARMED;
    /* A kind the app offers no cadence for is settled in chat, because settling it is more than one
       answer: a feed trigger takes a registered source, shared, and a trigger naming it. So the row
       carries the ask rather than a control it cannot complete — a row that stated the need and
       offered nothing left the member reading a chore with no way to do it. */
    if (order.kind !== SCHEDULE_KIND || schedule === null) {
      if (!instructions) return NOT_INSTALLED;
      return (
        <Button
          variant="outline"
          size="bar"
          className="my-sm bg-surface text-ink"
          onClick={() => ask(instructions)}
        >
          {title}
        </Button>
      );
    }
    /* The cadence is the whole of what the member is asked. The app authored the prompt, because
       the prompt IS the app's job — asking a member to write it is asking them to write the app
       they were given. */
    /* What the app is armed with, said the way the offers say it. An answer the member composed
       themselves matches no offer, so it stands in the field they wrote it in rather than being
       dropped: the step shows what it is set to either way. */
    const held = order.schedule ? cadenceOf(order.schedule, new Date().getTimezoneOffset()) : null;
    const taken = held ? labelOf(held) : "";
    const offered = schedule.cadences.some((offer) => labelOf(offer) === taken);
    return (
      <div className="flex w-full flex-col gap-2xl">
        <ToggleGroupOne
          className="flex flex-col gap-2xs"
          value={taken && offered ? taken : ""}
          onValueChange={(picked) => {
            const cadence = schedule.cadences.find((offer) => labelOf(offer) === picked);
            if (cadence) void arm(schedule, cadence, order.armed);
          }}
        >
          {schedule.cadences.map((cadence) => (
            <ToggleGroupItem
              key={cronFor(cadence, 0)}
              value={labelOf(cadence)}
              disabled={arming !== null}
              className={cn(
                "group flex h-10 w-full items-center justify-between rounded-(--radius-answer)",
                "border-0 bg-surface px-2xl text-start text-label text-ink hover:bg-fill-strong",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink",
              )}
            >
              {labelOf(cadence)}
              <IconCheck
                className={cn(
                  "size-(--size-glyph) shrink-0 text-ink-soft opacity-0",
                  "group-data-[state=on]:opacity-100",
                )}
                aria-hidden
              />
            </ToggleGroupItem>
          ))}
        </ToggleGroupOne>
        <Input
          surface="answer"
          className="bg-surface placeholder:text-ink-soft"
          aria-label={ANOTHER_CADENCE}
          placeholder={OWN_CADENCE}
          defaultValue={taken && !offered ? taken : undefined}
          disabled={arming !== null}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            const said = event.currentTarget.value.trim();
            if (said) sayCadence(said);
          }}
        />
      </div>
    );
  }
}
