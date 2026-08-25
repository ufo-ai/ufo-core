import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Facts, Group } from "@/components/ui/facts";
import { Notice, Panel, QUIET, usePanelRead, type NoticeState } from "@/kernel/panel";
import { BASE, postIntent } from "@/lib/api";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { newChatHash } from "@/lib/route";
import { setPendingAsk } from "@/lib/pendingAsk";
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
  connectors?: { provider: string; granted: boolean }[];
  credentials?: { label: string; filled: boolean; provider: string | null }[];
  standing?: { kind: string; armed: boolean }[];
  schedule?: SetupSchedule | null;
  instructions?: string;
  own_page?: boolean;
};

const SCHEDULE_KIND = "scheduled_task";

const ACCOUNTS = "Accounts";
const WORKSPACE_INSTALLS = "Workspace";
const RUNS = "Runs";

const CONNECT = "Connect";
const CONNECTING = "Connecting";
const CONNECTED = "Connected";
const OUTSTANDING = "Not connected";
const INSTALL = "Install";
const INSTALLED = "Installed";
const NOT_INSTALLED = "Not installed";
const ADMIN_INSTALLS = "An admin installs this.";
const ARMED = "Set";
const NOT_SET = "Not set";
const PICK = "Choose a cadence";
const ARMING = "Setting";
const SET_UP = "Set up";

/** What the row types on the member's behalf. It names the skill rather than the steps, so the app
 *  reads its own outstanding setup at that moment instead of following a stale sentence. */
const SETUP_ASK = "Load the agent-setup skill and follow its instructions.";

const BUILD_APP = "Build app";
const BUILD_NOTE =
  "The app reads what it is connected to and builds its own page. It runs in a conversation you "
  + "can watch, and you can ask for changes there.";

/** What the press types on the member's behalf, into the composer they send it from. It names the
 *  skill and stops: the steps live in the skill, and an ask repeating them would be a second copy
 *  of the procedure that drifts the first time either changes. */
const BUILD_ASK = "Build this workspace its own version of your page. Load your homepage skill and "
  + "follow it.";

const BLOCKED_POPUP = "Your browser blocked the window. Allow pop-ups and press Connect again.";
const CONNECT_REFUSED = "The connect did not open a consent page. Try again.";
const CONSENT_ELSEWHERE = "The consent window closed before the link arrived. Press Connect again.";

/** The workspace-wide install a credential's provider takes, keyed by that provider. The named tool
 *  mints the link inside its turn and the outcome carries it back, so installing takes no message
 *  the member has to send — the same two verbs the first run offers. */
const INSTALL_VERB: Record<string, string> = {
  slack: "connect_slack",
  github: "connect_github",
};

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

/** A kind as a member says it. An object kind is a slug, and a row wearing one reads as the one
 *  line on the screen written for the machine. */
function noun(kind: string): string {
  return kind.replace(/_/g, " ");
}

/** Wiring one app: the accounts it works from, the workspace installs it needs, and the standing
 *  order that gives it an occasion to run.
 *
 *  It is a screen and not a band on the app's own page, and that is the whole of why the acts here
 *  work. A page is framed cross-origin and speaks with the viewer's whole session, so the bridge
 *  fences it by name — and the two acts an unwired app most needs are exactly the ones that cannot
 *  cross: a workspace install is admin-gated, and forking the page is model work. Here they are
 *  ordinary portal acts.
 *
 *  An app arrives at this screen instead of its page, which is what the page is spared: with no
 *  account there is nothing real to draw, and rows of sample data in their place would be showing a
 *  member someone else's app and calling it theirs. The page draws what the app is and what it has
 *  done; this screen exists so it never has to draw what it would be. */
export function AgentSetup({ agent, admin }: { agent: Agent; admin: boolean }) {
  const [reloads, setReloads] = useState(0);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [arming, setArming] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [watching, setWatching] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const consent = useRef<Window | null>(null);
  const state = usePanelRead<SetupState>("/agents/" + agent.id + "/setup", reloads);
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

  /** The workspace-wide install, which is one act and not a brokered grant: the tool mints the link
   *  inside its own turn and the outcome carries it straight back. */
  async function install(provider: string) {
    setNotice(QUIET);
    const opened = openConsentWindow();
    setInstalling(provider);
    const outcome = await postIntent(agent.id, { verb: INSTALL_VERB[provider] });
    setInstalling(null);
    if (opened && outcome.url) opened.location.href = outcome.url;
    if (opened && !outcome.url) opened.close();
    /* The link is kept only for a member whose browser refused the window, so one press is the
       whole act for everybody else. */
    setLink(opened ? null : (outcome.url ?? null));
    setNotice(outcome.url ? QUIET : { text: outcome.message, refused: !outcome.applied });
    setReloads((count) => count + 1);
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
  function ask() {
    setPendingAsk(agent.id, SETUP_ASK, false);
    navigate(newChatHash(agent.id));
  }

  async function arm(schedule: SetupSchedule, cadence: SetupCadence) {
    setNotice(QUIET);
    setArming(schedule.name);
    const outcome = await postIntent(agent.id, {
      verb: "apply",
      kind: SCHEDULE_KIND,
      name: schedule.name,
      spec: {
        schedule: cronFor(cadence, new Date().getTimezoneOffset()),
        prompt: schedule.prompt,
      },
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
        return (
          <div className="flex flex-col gap-2xl">
          {connectors.length ? (
            <Group title={ACCOUNTS}>
              <Facts
                rows={connectors.map((connector) => ({
                  label: connector.provider,
                  value: connector.granted ? (
                    CONNECTED
                  ) : (
                    <span className="flex items-center justify-end gap-lg">
                      <span>{OUTSTANDING}</span>
                      <Button
                        variant="outline"
                        size="bar"
                        busy={connecting === connector.provider}
                        disabled={connecting !== null && connecting !== connector.provider}
                        onClick={() => connect(connector.provider)}
                      >
                        {connecting === connector.provider ? CONNECTING : CONNECT}
                      </Button>
                    </span>
                  ),
                }))}
              />
            </Group>
          ) : null}
          {credentials.length ? (
            <Group title={WORKSPACE_INSTALLS}>
              <Facts
                rows={credentials.map((credential) => ({
                  label: credential.label,
                  value: installValue(credential),
                }))}
              />
            </Group>
          ) : null}
          {standing.length ? (
            <Group title={RUNS}>
              <Facts
                rows={standing.map((order) => ({
                  label: noun(order.kind),
                  value: standingValue(order, payload.schedule ?? null),
                }))}
              />
            </Group>
          ) : null}
          <Notice tone={notice.refused ? "attention" : "quiet"}>{notice.text}</Notice>
          {link ? (
            <Notice>
              <ConsentLink url={link}>Open the install page</ConsentLink>
            </Notice>
          ) : null}
          {payload.instructions ? (
            <p className="m-0 max-w-hint text-label text-ink-soft">{payload.instructions}</p>
          ) : null}
            <div className="flex flex-col gap-lg">
              <Button variant="send" className="self-start" onClick={buildApp}>
                {BUILD_APP}
              </Button>
              <p className="m-0 max-w-hint text-label text-ink-soft">{BUILD_NOTE}</p>
            </div>
          </div>
        );
      }}
    </Panel>
  );

  /** A credential fills once for the whole workspace. Where its provider takes an install, this is
   *  the press that hands an admin the link; where it does not, or where the member is not an
   *  admin, the row states what is true and offers nothing — which is honest, because there is
   *  nothing they can press. */
  function installValue(credential: NonNullable<SetupState["credentials"]>[number]) {
    if (credential.filled) return INSTALLED;
    const verb = credential.provider === null ? undefined : INSTALL_VERB[credential.provider];
    if (verb === undefined) return NOT_INSTALLED;
    if (!admin) {
      return (
        <span className="flex items-center justify-end gap-lg">
          <span>{NOT_INSTALLED}</span>
          <span className="text-ink-soft">{ADMIN_INSTALLS}</span>
        </span>
      );
    }
    const provider = credential.provider as string;
    return (
      <span className="flex items-center justify-end gap-lg">
        <span>{NOT_INSTALLED}</span>
        <Button
          variant="outline"
          size="bar"
          busy={installing === provider}
          disabled={installing !== null && installing !== provider}
          onClick={() => install(provider)}
        >
          {INSTALL}
        </Button>
      </span>
    );
  }

  function standingValue(
    order: NonNullable<SetupState["standing"]>[number],
    schedule: SetupSchedule | null,
  ) {
    if (order.armed) return ARMED;
    /* A kind the app offers no cadence for is settled in chat, because settling it is more than one
       answer: a feed trigger takes a registered source, shared, and a trigger naming it. So the row
       carries the ask rather than a control it cannot complete — a row that stated the need and
       offered nothing left the member reading a chore with no way to do it. */
    if (order.kind !== SCHEDULE_KIND || schedule === null) {
      return (
        <span className="flex items-center justify-end gap-lg">
          <span>{NOT_SET}</span>
          <Button variant="outline" size="bar" onClick={ask}>
            {SET_UP}
          </Button>
        </span>
      );
    }
    /* The cadence is the whole of what the member is asked. The app authored the prompt, because
       the prompt IS the app's job — asking a member to write it is asking them to write the app
       they were given. */
    return (
      <span className="flex items-center justify-end gap-lg">
        <span>{NOT_SET}</span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="bar" busy={arming === schedule.name}>
              {arming === schedule.name ? ARMING : PICK}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {schedule.cadences.map((cadence) => (
              <DropdownMenuItem
                key={cronFor(cadence, 0)}
                onSelect={() => void arm(schedule, cadence)}
              >
                {labelOf(cadence)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
    );
  }
}
