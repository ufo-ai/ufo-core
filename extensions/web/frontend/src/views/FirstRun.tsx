import {
  IconCheck,
  IconChevronLeft,
  IconLoader2,
  IconMail,
  IconRepeat,
  IconSparkles,
  IconSquareRoundedCheckFilled,
  IconTarget,
  IconTerminal2,
  IconTrendingUp,
  IconX,
} from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Notice, Panel, PanelSkeleton, usePanelRead } from "@/kernel/panel";
import mark from "@brand/ufo-mark.svg";
import { AgentIcon } from "@/lib/agentIcon";
import { getJson, postAction, postObjectAction, type ObjectAction } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { chatSurface } from "@/lib/mainAgent";
import { openConversation } from "@/lib/turnStream";
import type { ActionView, Agent, Member } from "@/lib/types";

type ProviderTile = { name: string; label: string; summary: string; group: string };

type Connector = ProviderTile & { installed: boolean };

/** The read behind the run: the catalog, the installs this deploy can make, whether iMessage is
 *  offered, and the acts the member, memory and profile collections project for this member. The
 *  run draws the Slack install and the website step from it; the Connect page reads the same
 *  catalog for the tiles it offers. */
export type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
  actions: { member: ActionView[]; memory: ActionView[]; enrichment_profile: ActionView[] };
  model_key_held: boolean;
};

/** One member's row of the profile kind, as the object index projects it: what the enrichment
 *  learned about the member and the company behind their confirmed website. Every field is a
 *  scalar, which is the shape an index row takes. */
type Profile = {
  name: string;
  summary: string;
  status: "matched" | "no_match";
  full_name: string | null;
  job_title: string | null;
  job_title_role: string | null;
  job_title_levels: string | null;
  company_name: string | null;
  company_industry: string | null;
  company_size: string | null;
  company_founded: number | null;
  company_summary: string | null;
  company_location: string | null;
};

const PROFILE_READ = "/objects/enrichment_profile";
const CONFIRM_WEBSITE_ACTION = "confirm_website";

const BUSINESS_STEP = "business";
const WEBSITE_STEP = "website";
const POSITION_STEP = "position";
const DETAILS_STEP = "details";
const SLACK_STEP = "slack";

const ROLES = [
  "Founder",
  "Designer",
  "Marketing",
  "Operations",
  "Engineer",
  "Growth",
  "Human Resources",
  "Copywriting",
  "Researcher",
  "Executive Assistant",
  "Sales",
  "Other",
] as const;

type Role = (typeof ROLES)[number];

const OTHER_ROLE: Role = "Other";

/** The role the profile's title suggests, by the levels and the role class the enrichment reports:
 *  an owner or a chief is the founder whatever their title says, and every other class maps to the
 *  one option that names it. A class outside the map suggests the founder, the option the run
 *  opens on, and the member picks another. */
const ROLE_BY_CLASS: Record<string, Role> = {
  design: "Designer",
  marketing: "Marketing",
  operations: "Operations",
  engineering: "Engineer",
  sales: "Sales",
  human_resources: "Human Resources",
  research: "Researcher",
};

const FOUNDER_LEVELS = new Set(["owner", "cxo"]);

const DEFAULT_ROLE: Role = "Founder";

/** The domains a member signs up from that are their mail provider and never their company, the
 *  same list the enrichment holds. A box prefilled with one of them is confirmed in a press, and
 *  the mail provider stands as the workspace's company, so the box opens empty instead. */
const FREE_MAIL_DOMAINS = new Set([
  "aol.com",
  "fastmail.com",
  "gmail.com",
  "googlemail.com",
  "gmx.com",
  "gmx.net",
  "hey.com",
  "hotmail.com",
  "icloud.com",
  "live.com",
  "mac.com",
  "mail.com",
  "me.com",
  "msn.com",
  "outlook.com",
  "pm.me",
  "proton.me",
  "protonmail.com",
  "yahoo.com",
  "yandex.com",
  "ymail.com",
  "zoho.com",
]);

function signupWebsite(email: string): string {
  const domain = (email.split("@")[1] ?? "").toLowerCase();
  return FREE_MAIL_DOMAINS.has(domain) ? "" : domain;
}

function suggestedRole(profile: Profile): Role {
  const levels = (profile.job_title_levels ?? "").split(",").map((level) => level.trim());
  if (levels.some((level) => FOUNDER_LEVELS.has(level))) return DEFAULT_ROLE;
  return ROLE_BY_CLASS[profile.job_title_role ?? ""] ?? DEFAULT_ROLE;
}

/** What the enrichment made of the company, as the business box opens on it: the summary it holds,
 *  then the one line of facts under it. A row that learned nothing describes nothing, and the
 *  member writes the box themselves. */
function describes(profile: Profile): string {
  const industry = profile.company_industry;
  const facts = [
    industry ? industry[0].toUpperCase() + industry.slice(1) : null,
    profile.company_size ? profile.company_size + " people" : null,
    profile.company_founded ? "founded " + profile.company_founded : null,
    profile.company_location,
  ].filter((fact): fact is string => Boolean(fact));
  return [profile.company_summary, facts.length ? facts.join(", ") + "." : null]
    .filter((line): line is string => Boolean(line))
    .join("\n");
}

/** What the opening line asks for once it has said who the member is: the workspace's first task,
 *  set up by the agent in the lane the dashboard opens on. The `competitive-intel` skill routes on
 *  it and finishes the setup in chat, so the member never meets an empty dashboard. */
const FIRST_TASK = "Set up my first task: a daily competitive analysis.";

/** What the member wrote about their business, the role they picked, and whatever more they
 *  shared, as the sentence the chat opens on: the agent hears who it works for before its first
 *  turn rather than asking. */
function opening(business: string, role: string, details: string): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  const about = "I just set up this workspace. My business: " + said(business) + ". My role: " + role + ".";
  const told = details.trim() ? about + " " + said(details) + "." : about;
  return told + " " + FIRST_TASK;
}

/** The connector catalog and the workspace's installs — the read behind both selectors. The first
 *  run reads it for the Slack step, that step waiting on an install reads it again for the single
 *  fact it is waiting on, and the Connect page reads it for the tiles it offers. */
export const FIRST_RUN_READ = "/workspace/first-run";

/** The memory collection's own act, and the one write the run makes. */
const RECORD_FIRST_RUN_ACTION = "record_first_run";

/** How often a waiting selector re-reads. The install happens on the provider's own pages, so
 *  nothing here can say when it lands — the wait is the member's, and it is measured in the
 *  seconds they spend over there rather than in the half-minute a pane showing records can hold a
 *  stale answer for. */
export const WATCH_MS = 3_000;

/** The object a workspace install acts on and the action that installs it, keyed by the connector
 *  that takes one: Slack's is the surface object's connect, GitHub's the installation credential's.
 *  The object's detail is read for the act it projects and the act mints the install link inside
 *  the turn, so installing takes no message the member has to send. Every provider outside this map
 *  connects a member's own account through the broker verb instead. */
export const CONNECT_INSTALLS: Record<string, ObjectAction> = {
  slack: { kind: "surface", name: "slack", action: "slack_connect" },
  github: { kind: "credential", name: "github-app-installation", action: "connect_github" },
};

/** The goals a member most often starts with, picked rather than typed: each pick is written to
 *  memory and opens a thread of its own, so the run hands back one conversation per goal beside the
 *  first task.
 *
 *  `said` is the goal in the member's own words, which is what memory records. `asks` is the rest
 *  of the sentence that thread opens on — the first step to take, and the trap to avoid taking it
 *  into. It is stated here rather than held in a skill because the run already knows which goal
 *  was picked: routing a description the wizard could name outright buys a round trip and a class
 *  of failure, and buys nothing else. Each one is written as the member's own ask, because the
 *  member is the speaker on that thread and reads it as theirs. */
const GOALS = [
  {
    label: "More revenue",
    said: "more revenue",
    icon: IconTrendingUp,
    asks:
      "Start by working out my funnel as it stands from whatever CRM, billing, analytics or " +
      "spreadsheet access I have granted: volume at each stage, conversion between them, and " +
      "average deal size. Name the single stage losing the most, and one experiment on it with " +
      "a metric. Do not guess a number I have not given you — say what you could not read. Ask " +
      "me at most one thing.",
  },
  {
    label: "Faster product dev",
    said: "faster product development",
    icon: IconTerminal2,
    asks:
      "Start by measuring how long a change takes from opened to shipped, from whatever " +
      "repository and issue tracker I have granted, and name the stage that holds it longest. " +
      "Use what I already have rather than proposing a replacement for it. Do not invent a " +
      "cycle time you could not read — say what you could not read. Ask me at most one thing.",
  },
  {
    label: "Automate ops",
    said: "automating operations",
    icon: IconRepeat,
    asks:
      "Start by naming the recurring workflow that costs us the most time and mapping it as it " +
      "runs today: trigger, frequency, inputs, decisions, outputs, and owner. Split it into the " +
      "steps an agent can complete and the steps a person must approve, and keep the approval " +
      "wherever money, contracts, access or an outside commitment changes. Ask me at most one " +
      "thing.",
  },
  {
    label: "Find PMF",
    said: "finding product-market fit",
    icon: IconTarget,
    asks:
      "Start by stating the hypothesis my business implies — which user, which problem — and " +
      "count the candidates I can already reach in whatever CRM, support or calendar access I " +
      "have granted. Tell me the gap between what we claim and what has been tested. Send " +
      "nothing and book nobody. Ask me at most one thing.",
  },
];

/** The sentence one picked goal's thread opens on: who the member is, the goal in their words, and
 *  what taking the first step on it means. Nobody is reading this thread yet — the member is on the
 *  first task — so it asks for work done rather than for a plan. */
function goalOpening(business: string, role: string, goal: (typeof GOALS)[number]): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  return (
    "I just set up this workspace. My business: " +
    said(business) +
    ". My role: " +
    role +
    ". My goal: " +
    goal.said +
    ". " +
    goal.asks
  );
}

/** The one memory the run writes, before any thread opens: who the workspace is for, and what they
 *  came for. Every thread recalls it, so none of them asks what the business does. The action bounds
 *  a body, so the goals fall away before the business does — a member with no goals still has a
 *  workspace that knows them. */
export function firstRunRecorded(
  business: string,
  role: string,
  goals: string[],
  budget: number,
): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  const who = said(business) + ". Their role: " + role + ".";
  for (let named = goals.length; named > 0; named -= 1) {
    const body = who + " Their goals: " + goals.slice(0, named).join(", ") + ".";
    if (body.length <= budget) return body;
  }
  return who.slice(0, budget);
}

/** How long each row of the building screen waits for the one before it. The rows land one at a
 *  time so the member reads what the workspace holds as it fills, rather than a list all at once. */
export const BUILD_STEP_MS = 700;

/** A local slot has no Slack app to install, so a dev host offers a way onto the success screen. */
const LOCAL_DEV = location.hostname === "localhost" || location.hostname.endsWith(".localhost");

const SLACK_POINTS = [
  "Mention @ufo or send it a DM.",
  "It replies, remembers, and works with your team.",
  "One install for the whole workspace.",
];

/** The head of every screen on the run: the workspace mark, the progress wherever a step is being
 *  counted, and the screen's own acts. */
function Head({ actions, at, steps }: { actions?: ReactNode; at?: number; steps?: number }) {
  return (
    <div className="grid h-8xl grid-cols-3 items-center px-7xl max-narrow:px-2xl">
      <span
        role="img"
        aria-label="ufo"
        className="block size-(--size-glyph) shrink-0 bg-current"
        style={{ mask: `url(${mark}) center / contain no-repeat` }}
      />
      {at !== undefined && steps !== undefined ? (
        <div
          role="progressbar"
          aria-label="Step"
          aria-valuemin={1}
          aria-valuemax={steps}
          aria-valuenow={at + 1}
          className="flex items-center gap-2xs justify-self-center"
        >
          {Array.from({ length: steps }, (_, index) => (
            <span
              key={index}
              aria-hidden
              className={cn(
                "h-2xs rounded-full",
                index === at ? "w-(--size-step-on) bg-ink" : "w-(--size-step-off) bg-edge",
              )}
            />
          ))}
        </div>
      ) : null}
      <div className="col-start-3 flex items-center justify-self-end gap-sm">{actions}</div>
    </div>
  );
}

/** The page a step is read on, with the workspace mark, progress, and the step's acts in its head.
 *  It has no navigation because an unset workspace has nowhere else to go, and offering
 *  destinations would pull the member away from finishing setup. The rest of the viewport centres
 *  the step's question and answer together, under whatever mark leads it. */
function Frame({
  title,
  note,
  lead,
  actions,
  at,
  steps,
  children,
}: {
  title?: ReactNode;
  note?: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  at?: number;
  steps?: number;
  children: ReactNode;
}) {
  return (
    <main className="grid h-dvh grid-rows-[auto_1fr] overflow-y-auto">
      <Head actions={actions} at={at} steps={steps} />
      <div className="mx-auto flex min-h-0 w-full max-w-section flex-col items-center justify-center gap-6xl px-2xl py-6xl">
        {title ? (
          <div className="flex max-w-form flex-col items-center gap-2xl text-center">
            {lead}
            <div className="flex flex-col gap-sm">
              <h1 className="m-0 text-subtitle font-medium text-ink">{title}</h1>
              {note ? <p className="m-0 text-label text-ink-soft">{note}</p> : null}
            </div>
          </div>
        ) : null}
        {children}
      </div>
    </main>
  );
}

/** The way out of the run: the chat, with nothing asked. */
function Close({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="mark"
      size="glyph"
      aria-label="Close"
      className="text-ink-quiet"
      onClick={onClick}
    >
      <IconX stroke={1.25} aria-hidden />
    </Button>
  );
}

/** The acts under a step: the way back, and the way on. */
function Foot({
  onBack,
  onNext,
  nextDisabled,
  busy,
}: {
  onBack: () => void;
  onNext: () => void;
  nextDisabled: boolean;
  busy?: boolean;
}) {
  return (
    <div className="flex items-center gap-2xl">
      <Button
        variant="quiet"
        size="icon"
        aria-label="Back"
        className="size-10 bg-fill hover:bg-fill-strong"
        onClick={onBack}
      >
        <IconChevronLeft stroke={1.5} aria-hidden />
      </Button>
      <Button
        variant="send"
        size="bar"
        className="h-10 w-32"
        disabled={nextDisabled}
        busy={busy}
        onClick={onNext}
      >
        Next
      </Button>
    </div>
  );
}

/** A read that failed, stated the one way every fault on the run is: as a toast. The screen keeps
 *  its frame and its way out, and the sentence is raised once for the message, not once per
 *  render. */
function Failed({ message, onToast }: { message: string; onToast: (title: string) => void }) {
  useEffect(() => onToast(message), [message, onToast]);
  return null;
}

/** The column a question and its answer stand in, read down from a left edge. */
const STEP_COLUMN = "flex w-(--container-dialog) max-w-full flex-col items-start gap-6xl";

/** The filled card a written answer is typed into, holding the box and whatever stands under it. */
const ANSWER_CARD = cn(
  "flex w-full flex-col gap-2xl rounded-(--radius-answer) bg-fill p-lg",
  "focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink",
);

/** The box inside the card: it draws no surface of its own, because a second surface inside the
 *  first states a box within a box. */
const ANSWER_BOX = cn(
  "w-full resize-none border-0 bg-transparent p-0 font-sans text-label text-field-ink",
  "placeholder:text-ink-faint focus-visible:outline-none",
);

/** Every line on the welcome is trimmed to its caps, so a gap between two lines measures from the
 *  letters rather than from the half-leading around them — which is how the comp measures it. */
const CAP_TRIM = "[text-box:trim-both_cap_alphabetic]";

const POINTS = [
  {
    title: "Understand your business",
    note: "UFO learns your company, goals, tools, and workflows.",
  },
  {
    title: "Build what your team needs",
    note: "Turn that context into apps, agents, and workflows built around how you work.",
  },
  {
    title: "Keep work moving",
    note: "UFO works alongside you and your team, including inside tools like Slack.",
  },
];

/** The screen the run opens on: what the product is, in a title and three points, and the one act
 *  that begins the run. Closing it opens the chat instead. The points stand on the one plane the
 *  run lays in ink, so the type on them takes the pane's tone; the plane leaves a narrow screen,
 *  which has no room beside the title for it. */
function Welcome({ onStart, onClose }: { onStart: () => void; onClose: () => void }) {
  return (
    <main className="grid h-dvh grid-rows-[auto_1fr] overflow-y-auto border-t border-edge">
      <Head actions={<Close onClick={onClose} />} />
      <div className="flex min-h-0 items-center justify-center gap-6xl py-5xl pr-5xl max-narrow:pr-0">
        <div className="flex min-w-0 flex-1 flex-col items-center gap-6xl text-center">
          <div className="flex flex-col items-center gap-2xl">
            <Badge tone="affirm" className="h-(--size-control) px-lg text-label font-medium">
              <span className={CAP_TRIM}>Welcome to UFO</span>
            </Badge>
            <h1
              className={cn(
                "m-0 text-figure leading-tight font-medium tracking-(--tracking-ui) text-ink",
                CAP_TRIM,
              )}
            >
              An AI operating system{" "}
              <br />
              for your business
            </h1>
            <p className={cn("m-0 text-ui tracking-(--tracking-ui) text-ink-quiet", CAP_TRIM)}>
              UFO understands how your business works, then creates{" "}
              <br />
              apps and an agent to help your team run it.
            </p>
          </div>
          <Button variant="send" size="bar" className="h-10 w-32" onClick={onStart}>
            Get started
          </Button>
        </div>
        <div className="flex h-full min-w-0 flex-1 items-center justify-center rounded-menu bg-ink max-narrow:hidden">
          <ol className="m-0 flex w-(--container-card) list-none flex-col gap-6xl p-0">
            {POINTS.map((point, index) => (
              <li key={point.title} className="flex gap-2xl">
                <span className="flex size-(--size-glyph) shrink-0 items-center justify-center rounded-sm bg-ink-soft">
                  <span className={cn("text-fine font-medium text-surface", CAP_TRIM)} aria-hidden>
                    {index + 1}
                  </span>
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-2xl">
                  <h2 className={cn("m-0 text-subtitle font-medium text-surface", CAP_TRIM)}>
                    {point.title}
                  </h2>
                  <p className={cn("m-0 text-ui tracking-(--tracking-ui) text-ink-quiet", CAP_TRIM)}>
                    {point.note}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </main>
  );
}

/** One fact the run has learned about the member, set apart from the sentence around it. */
function Fact({ children }: { children: ReactNode }) {
  return <span className="text-link underline underline-offset-2">{children}</span>;
}

/** The screen the run ends on: the workspace filling with its shipped apps, one row at a time,
 *  then the assistant, then the way in. The apps are the roster's, drawn with their own marks; the
 *  timing is the screen's own, since the workspace already holds them.
 *
 *  An app this deploy withholds is not drawn at all. `hidden` says the deploy withholds the app
 *  from every list the portal draws, never that the app is unbuilt — the workspace holds it and a
 *  member arriving on its address opens it. So naming it here and marking it `Coming soon` stated
 *  something untrue about a shipped app, and this screen lists what the workspace offers. */
function Building({
  company,
  apps,
  assistant,
  threads,
  onClose,
  onDone,
}: {
  company: string | null;
  apps: Agent[];
  assistant: Agent;
  threads: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const built = apps.filter((app) => !app.hidden);
  const stages = built.length + 2 + (threads.length ? 1 : 0);
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= stages) return;
    const timer = setTimeout(() => setShown(shown + 1), BUILD_STEP_MS);
    return () => clearTimeout(timer);
  }, [shown, stages]);
  const appsDone = shown >= built.length;
  const trained = shown >= built.length + 1;
  const started = shown >= built.length + 2;
  const done = shown >= stages;
  const row = (agent: Agent) => (
    <li
      key={agent.id}
      className="flex h-10 w-full items-center gap-sm rounded-(--radius-answer) bg-fill px-2xl text-label text-ink animate-appear motion-reduce:animate-none"
    >
      <AgentIcon name={agent.icon} className="size-(--size-glyph) shrink-0" />
      {agent.name}
    </li>
  );
  const pending = (
    <li className="flex h-10 w-full items-center gap-sm rounded-(--radius-answer) bg-fill px-2xl text-label text-ink-quiet">
      <IconLoader2 className="size-(--size-glyph) shrink-0 animate-spin" stroke={1.5} aria-hidden />
      Creating…
    </li>
  );
  /* One row per goal the run opened a thread on. The turns are already running, so the row reports
     a thread that exists rather than one this screen is about to make. */
  const thread = (goal: string) => (
    <li
      key={goal}
      className="flex h-10 w-full items-center gap-sm rounded-(--radius-answer) bg-fill px-2xl text-label text-ink animate-appear motion-reduce:animate-none"
    >
      <IconSparkles className="size-(--size-glyph) shrink-0 text-link" stroke={1.5} aria-hidden />
      {goal}
    </li>
  );
  const label = (text: string, finished: boolean) => (
    <p className="m-0 flex items-center gap-sm text-label font-medium tracking-(--tracking-ui) text-ink-quiet">
      {finished ? (
        <IconCheck className="size-(--size-glyph) shrink-0 text-link" stroke={1.5} aria-hidden />
      ) : (
        <IconSparkles className="size-(--size-glyph) shrink-0 text-link" stroke={1.5} aria-hidden />
      )}
      {text}
    </p>
  );
  return (
    <Frame actions={<Close onClick={onClose} />}>
      <div className="flex w-full max-w-(--container-connect) flex-col gap-6xl">
        <div className="flex flex-col gap-2xl">
          {company ? <p className="m-0 text-subtitle font-medium text-ink-quiet">{company}</p> : null}
          <h1 className="m-0 text-subtitle font-medium text-ink">Creating your business’s workspace</h1>
        </div>
        <div className="flex flex-col gap-2xl">
          {label(appsDone ? "Building out foundational apps" : "Building out foundational apps…", appsDone)}
          <ul className="m-0 flex list-none flex-col gap-2xs p-0">
            {built.slice(0, shown).map(row)}
            {appsDone ? null : pending}
          </ul>
          {appsDone ? label(trained ? "Trained your assistant" : "Training your assistant…", trained) : null}
          {appsDone ? (
            <ul className="m-0 flex list-none flex-col gap-2xs p-0">
              {trained ? row(assistant) : pending}
            </ul>
          ) : null}
          {trained && threads.length
            ? label(started ? "Started work on your goals" : "Starting work on your goals…", started)
            : null}
          {trained && threads.length ? (
            <ul className="m-0 flex list-none flex-col gap-2xs p-0">
              {started ? threads.map(thread) : pending}
            </ul>
          ) : null}
        </div>
        {done ? (
          <Button
            variant="send"
            size="bar"
            className="h-10 w-full animate-appear motion-reduce:animate-none"
            onClick={onDone}
          >
            Open your workspace
          </Button>
        ) : null}
      </div>
    </Frame>
  );
}

/** The run, drawn the same on both shells. What a shell owns is where its chat lives, so the two
 *  acts that touch a conversation are the shell's: `onHandoff` commits the first task's words to
 *  whichever composer that shell is about to stand, and `onDone` carries the member to it. Every
 *  other act here — the enrichment read, the memory write, the thread per goal, the build screen —
 *  is the same wherever the run is drawn, so it lives here once. */
export function FirstRun({
  agent,
  agents,
  member,
  onClose,
  onHandoff,
  onDone,
}: {
  agent: Agent;
  agents: Agent[];
  member: Member;
  onClose: () => void;
  onHandoff: (text: string) => void;
  onDone: () => void;
}) {
  const state = usePanelRead<FirstRunPayload>(FIRST_RUN_READ, 0);
  const [business, setBusiness] = useState("");
  const [website, setWebsite] = useState(() => signupWebsite(member.email));
  const [profile, setProfile] = useState<Profile | null>(null);
  const [role, setRole] = useState<Role>(DEFAULT_ROLE);
  const [rolePicked, setRolePicked] = useState(false);
  const [otherRole, setOtherRole] = useState("");
  const [details, setDetails] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [at, setAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [building, setBuilding] = useState(false);
  /* The goals whose threads the run actually founded, which is what the build screen reports: a
     thread the POST never opened is not named as opened. */
  const [threads, setThreads] = useState<string[]>([]);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [welcomed, setWelcomed] = useState(false);
  /* Held across renders, because the failed panel raises its sentence from an effect keyed on this
     function: a new one every render would raise the sentence again on every render it caused. */
  const refuse = useCallback((title: string) => setToast({ title }), []);

  if (!welcomed) {
    return <Welcome onStart={() => setWelcomed(true)} onClose={onClose} />;
  }
  if (building) {
    return (
      <Building
        company={profile?.status === "matched" ? profile.company_name : null}
        apps={agents.filter((row) => row.app && !row.main)}
        assistant={agent}
        threads={threads}
        onClose={onDone}
        onDone={onDone}
      />
    );
  }

  return (
    <>
      <Panel
        state={state}
        loading={() => (
          <Frame actions={<Close onClick={onClose} />}>
            <PanelSkeleton shape="form" />
          </Frame>
        )}
        failed={(message) => (
          <Frame actions={<Close onClick={onClose} />}>
            <Failed message={message} onToast={refuse} />
          </Frame>
        )}
      >
        {(payload) => {
          const slack = payload.connectors.find((row) => row.name === SLACK_STEP);
          const confirm = payload.actions.enrichment_profile.find(
            (view) => view.name === CONFIRM_WEBSITE_ACTION,
          );
          /** The run the member is on: the website it reads the business from, then the questions
           *  that read comes back to answer, then the Slack install wherever this deploy offers one.
           *  The website step stands only where the deploy can act on the answer. Every step is
           *  certain from the start, so the head counts them all from the first screen. */
          const revealed = [
            ...(confirm ? [WEBSITE_STEP] : []),
            BUSINESS_STEP,
            POSITION_STEP,
            DETAILS_STEP,
            ...(slack ? [SLACK_STEP] : []),
          ];
          const step = revealed[at];
          const held = slack ? slack.installed : false;
          const installed = step === SLACK_STEP && (held || connected);
          const said = role === OTHER_ROLE ? otherRole.trim() : role;
          const write = payload.actions.memory.find(
            (view) => view.name === RECORD_FIRST_RUN_ACTION,
          );
          /** What the run leaves behind, in the order the rest depends on: the memory every thread
           *  recalls, then a thread per picked goal, then the first task on the lane the member
           *  lands on. The memory is written first because a goal thread that starts before it
           *  would ask what the business does. A refused write holds the step — the run has nothing
           *  to hand over without it, and the member reads why. */
          const finish = async () => {
            if (busy) return;
            if (!write) {
              refuse("This deploy runs without memory.");
              return;
            }
            setBusy(true);
            const budget =
              write.input_schema.properties?.body?.maxLength ?? Number.POSITIVE_INFINITY;
            const picked = GOALS.filter((goal) => goals.includes(goal.label));
            const outcome = await postAction(agent.id, write.call, {
              body: firstRunRecorded(business, said, picked.map((goal) => goal.said), budget),
            });
            if (!outcome.applied) {
              setBusy(false);
              refuse(outcome.message);
              return;
            }
            const speaks = chatSurface(agents) ?? agent;
            const opened: string[] = [];
            for (const goal of picked) {
              const founded = await openConversation(speaks.id, goalOpening(business, said, goal));
              if (founded) opened.push(goal.label);
            }
            onHandoff(opening(business, said, details));
            setThreads(opened);
            setBusy(false);
            setBuilding(true);
          };
          const advance = () => (at + 1 < revealed.length ? setAt(at + 1) : void finish());
          const back = () => (at ? setAt(at - 1) : setWelcomed(false));
          /** Confirms the website, reads back what the enrichment made of it, and suggests the role
           *  it found where the member has not picked one. A refusal is stated and holds the step. */
          const confirmWebsite = async () => {
            if (!confirm || busy) return;
            setBusy(true);
            const outcome = await postAction(agent.id, confirm.call, { website: website.trim() });
            if (!outcome.applied) {
              setBusy(false);
              refuse(outcome.message);
              return;
            }
            const read = await getJson<{ objects: Profile[] }>(
              `${PROFILE_READ}?email=${encodeURIComponent(member.email)}`,
            );
            setBusy(false);
            if (!read.ok) {
              refuse(read.message);
              return;
            }
            const own = read.payload.objects.find((row) => row.name === member.email) ?? null;
            setProfile(own);
            if (own && !rolePicked) setRole(suggestedRole(own));
            const learned = own ? describes(own) : "";
            if (learned) setBusiness((held) => (held.trim() ? held : learned));
            advance();
          };
          const article = (word: string) => (/^[aeiou]/i.test(word) ? "an" : "a");
          const company = profile?.status === "matched" ? profile.company_name : null;
          const industry = profile?.status === "matched" ? profile.company_industry : null;
          return (
            <Frame
              title={
                installed
                  ? "We were able to connect to Slack"
                  : step === SLACK_STEP
                    ? "Connect your messaging app"
                    : undefined
              }
              note={
                installed
                  ? "This installed UFO in Slack for your team"
                  : step === SLACK_STEP
                    ? "This will install UFO in Slack for your team"
                    : undefined
              }
              lead={
                installed ? (
                  <Badge tone="affirm" className="gap-2xs">
                    <IconCheck className="size-(--size-glyph)" stroke={1.5} aria-hidden />
                    Success
                  </Badge>
                ) : step === SLACK_STEP ? (
                  <Badge tone="affirm">Get the power of UFO everywhere</Badge>
                ) : undefined
              }
              at={at}
              steps={revealed.length}
              actions={<Close onClick={onClose} />}
            >
              {step === BUSINESS_STEP ? (
                <div className={STEP_COLUMN}>
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    Tell us a bit about your business
                  </h1>
                  <div className={ANSWER_CARD}>
                    <textarea
                      rows={3}
                      aria-label="About your business"
                      autoFocus
                      placeholder="Software startup, Marketing agency, Design studio, AI consulting…"
                      value={business}
                      onChange={(event) => setBusiness(event.target.value)}
                      className={ANSWER_BOX}
                    />
                    <span
                      className={cn(
                        buttonVariants({ variant: "outline", size: "bar" }),
                        "self-start px-lg text-ink-quiet",
                      )}
                    >
                      <IconMail className="size-(--size-glyph)" stroke={1.5} aria-hidden />
                      {member.email}
                    </span>
                  </div>
                  <Foot onBack={back} onNext={advance} nextDisabled={!business.trim()} />
                </div>
              ) : null}
              {step === WEBSITE_STEP ? (
                <div className={STEP_COLUMN}>
                  <div className="flex flex-col gap-2xl">
                    <h1 className="m-0 text-subtitle font-medium text-ink">Confirm your website</h1>
                    <p className="m-0 text-label text-ink-soft">
                      UFO looks up your website and your email address with People Data Labs to
                      learn about your business. Clear it to skip: nothing is sent.
                    </p>
                  </div>
                  <div className="relative w-full">
                    <Input
                      surface="answer"
                      aria-label="Website"
                      autoFocus
                      inputMode="url"
                      autoComplete="url"
                      placeholder="company.com"
                      value={website}
                      onChange={(event) => setWebsite(event.target.value)}
                    />
                    {website ? (
                      <Button
                        variant="mark"
                        size="glyph"
                        aria-label="Clear"
                        className="absolute top-1/2 right-lg -translate-y-1/2"
                        onClick={() => setWebsite("")}
                      >
                        <IconX stroke={1.5} aria-hidden />
                      </Button>
                    ) : null}
                  </div>
                  <Foot onBack={back} onNext={confirmWebsite} nextDisabled={false} busy={busy} />
                </div>
              ) : null}
              {step === POSITION_STEP ? (
                <div className={STEP_COLUMN}>
                  <div className="flex flex-col gap-sm">
                    {company ? (
                      <p className="m-0 text-subtitle font-medium text-ink-quiet">{company}</p>
                    ) : null}
                    <h1 className="m-0 text-subtitle font-medium text-ink">
                      What is your role at the business?
                    </h1>
                  </div>
                  <ToggleGroupOne
                    aria-label="Role"
                    className="grid w-full grid-cols-3 gap-2xs max-narrow:grid-cols-2"
                    value={role}
                    onValueChange={(value) => {
                      if (!value) return;
                      setRole(value as Role);
                      setRolePicked(true);
                    }}
                  >
                    {ROLES.map((option) => (
                      <ToggleGroupItem
                        key={option}
                        value={option}
                        autoFocus={option === role}
                        className={cn(
                          "group flex h-10 items-center justify-between gap-sm rounded-(--radius-answer)",
                          "border border-transparent bg-fill px-2xl text-start text-label text-ink",
                          "hover:bg-fill-strong",
                          "data-[state=on]:border-link data-[state=on]:bg-transparent data-[state=on]:text-link",
                          "data-[state=on]:hover:bg-transparent",
                          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink",
                          option === OTHER_ROLE && "text-ink-quiet",
                        )}
                      >
                        {option}
                        <IconCheck
                          className="size-(--size-glyph) shrink-0 opacity-0 group-data-[state=on]:opacity-100"
                          stroke={1.5}
                          aria-hidden
                        />
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroupOne>
                  {role === OTHER_ROLE ? (
                    <Input
                      surface="answer"
                      aria-label="Your role"
                      autoFocus
                      placeholder="Your role"
                      value={otherRole}
                      onChange={(event) => setOtherRole(event.target.value)}
                    />
                  ) : null}
                  <Foot onBack={back} onNext={advance} nextDisabled={!said} />
                </div>
              ) : null}
              {step === DETAILS_STEP ? (
                <div className={STEP_COLUMN}>
                  <div className="flex flex-col gap-2xl">
                    <h1 className="m-0 text-subtitle font-medium text-ink">
                      Share more details about your business
                    </h1>
                    {said ? (
                      <p className="m-0 text-label font-medium tracking-(--tracking-ui) text-ink-quiet">
                        You’re {article(said)} <Fact>{said}</Fact>
                        {company ? (
                          <>
                            {" "}
                            building <Fact>{company}</Fact>
                            {industry ? (
                              <>
                                , {article(industry)} <Fact>{industry}</Fact> business
                              </>
                            ) : null}
                          </>
                        ) : (
                          " building your business"
                        )}
                        .
                      </p>
                    ) : null}
                  </div>
                  <div className={ANSWER_CARD}>
                    <textarea
                      rows={3}
                      aria-label="What you want help with"
                      autoFocus
                      placeholder="What do you want help with? Finding customers, shipping faster, hiring, keeping the books…"
                      value={details}
                      onChange={(event) => setDetails(event.target.value)}
                      className={ANSWER_BOX}
                    />
                    <p className="m-0 text-label font-medium tracking-(--tracking-ui) text-ink-quiet">
                      Pick the goals you want work started on. Each one opens its own thread.
                    </p>
                    <div className="flex flex-wrap gap-2xs">
                      {GOALS.map((goal) => {
                        const picked = goals.includes(goal.label);
                        return (
                          <Button
                            key={goal.label}
                            variant={picked ? "send" : "outline"}
                            size="bar"
                            aria-pressed={picked}
                            className="px-lg font-normal"
                            onClick={() =>
                              setGoals((held) =>
                                held.includes(goal.label)
                                  ? held.filter((name) => name !== goal.label)
                                  : [...held, goal.label],
                              )
                            }
                          >
                            <goal.icon className="size-(--size-glyph)" stroke={1.5} aria-hidden />
                            {goal.label}
                          </Button>
                        );
                      })}
                    </div>
                  </div>
                  <Foot onBack={back} onNext={advance} nextDisabled={false} />
                </div>
              ) : null}
              {installed ? (
                <Button variant="send" size="bar" className="h-10 w-38" onClick={finish}>
                  Continue
                </Button>
              ) : null}
              {step === SLACK_STEP && slack && !installed ? (
                <div className="flex w-full flex-col items-center gap-6xl">
                  <ul className="m-0 flex list-none flex-col gap-2xl p-0">
                    {SLACK_POINTS.map((point) => (
                      <li
                        key={point}
                        className="flex items-center gap-sm text-label font-medium tracking-(--tracking-ui) text-ink"
                      >
                        <IconSquareRoundedCheckFilled
                          className="size-(--size-glyph) shrink-0 text-link"
                          aria-hidden
                        />
                        {point}
                      </li>
                    ))}
                  </ul>
                  <div className="flex w-full max-w-(--container-connect) flex-col items-center gap-sm">
                    <Connect
                      agent={agent}
                      admin={member.admin}
                      row={slack}
                      held={held}
                      onConnected={() => setConnected(true)}
                      onRefused={refuse}
                    />
                    <div className="w-full px-2xl">
                      <Button
                        variant="quiet"
                        size="bar"
                        className="h-10 w-full bg-fill text-ink hover:bg-fill-strong"
                        onClick={finish}
                      >
                        I use something different
                      </Button>
                      {LOCAL_DEV ? (
                        <Button
                          variant="quiet"
                          size="bar"
                          className="h-10 w-full"
                          onClick={() => setConnected(true)}
                        >
                          Continue
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </div>
              ) : null}
            </Frame>
          );
        }}
      </Panel>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

/** Waits for the install this step asked for, and reports it once. The install is granted on the
 *  provider's pages, which tell this page nothing, so the only account of it is the projection the
 *  page already reads — asked for often while a step waits on it, and once more the moment the tab
 *  carrying that step is looked at again, which is what a member coming back from the install is
 *  doing. The page's own read of that projection is `held`: whichever of the two reads sees the
 *  install first is the one that reports it, so the order the network answers in cannot strand
 *  the step on a connected install.
 *
 *  A step that opened on a connector the workspace already held never arms: it reads nothing, and
 *  reports nothing to advance past. So the report is only ever the install arriving under a member
 *  who was waiting for it, which is the one thing that should move them on. */
function useConnected(name: string, held: boolean, onConnected: () => void) {
  const armed = useRef(!held);
  const state = usePanelRead<FirstRunPayload>(
    armed.current && !held ? FIRST_RUN_READ : null,
    0,
    WATCH_MS,
  );
  const landed =
    held ||
    (state.phase === "ready" &&
      state.payload.connectors.some((row) => row.name === name && row.installed));
  const reported = useRef(false);
  useEffect(() => {
    if (!armed.current || !landed || reported.current) return;
    reported.current = true;
    onConnected();
  }, [landed, onConnected]);
}

/** One connector's own step. The act dispatches that connector's admin-gated tool, which seals the
 *  install link for this workspace and answers with it — a non-admin is told who installs it rather
 *  than pressing an act the workspace refuses. The link expires, so the act stays on the step and
 *  mints another.
 *
 *  Pressing it is the whole thing: the consent window opens on the press and the minted link lands
 *  in it, rather than appearing under the button as a second thing to find. A browser that refuses
 *  the window is the only case that still renders the link, because then there is nothing else to
 *  carry the member over. A refusal is the run's to state, so it is handed up rather than drawn
 *  here. The act carries the mark itself, so the product is named once rather than drawn twice on
 *  one page. */
function Connect({
  agent,
  admin,
  row,
  held,
  onConnected,
  onRefused,
}: {
  agent: Agent;
  admin: boolean;
  row: Connector;
  held: boolean;
  onConnected: () => void;
  onRefused: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  useConnected(row.name, held, onConnected);

  async function connect() {
    if (busy) return;
    setBusy(true);
    // Opened on the press, before the round trip that mints the link: a window opened afterwards
    // has lost the gesture the browser opens one for. It waits on the provider's own page.
    const consent = openConsentWindow();
    const outcome = await postObjectAction(agent.id, CONNECT_INSTALLS[row.name], {});
    setBusy(false);
    if (consent && outcome.url) consent.location.href = outcome.url;
    if (consent && !outcome.url) consent.close();
    // The step keeps the link only for a member whose browser refused the window, so pressing once
    // is the whole act for everybody else.
    setLink(consent ? null : (outcome.url ?? null));
    if (!outcome.url) onRefused(outcome.message);
  }

  return (
    <div className="flex w-full max-w-(--container-connect) flex-col items-center gap-sm px-2xl">
      {admin ? (
        <Button variant="send" size="bar" className="h-10 w-full" busy={busy} onClick={connect}>
          <BrandMark provider={row.name} onInk className="size-(--size-glyph)" />
          {"Connect " + row.label}
        </Button>
      ) : (
        <span className="flex h-10 w-full items-center justify-center text-center text-label text-ink-soft">
          {"A workspace admin connects " + row.label + "."}
        </span>
      )}
      {link ? (
        <Notice>
          <ConsentLink url={link}>{"Open the " + row.label + " install page"}</ConsentLink>
        </Notice>
      ) : null}
    </div>
  );
}
