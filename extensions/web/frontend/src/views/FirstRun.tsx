import {
  IconCheck,
  IconChevronLeft,
  IconLoader2,
  IconMail,
  IconSparkles,
  IconSquareRoundedCheckFilled,
  IconX,
} from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ToggleGroup, ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Notice, Panel, PanelSkeleton, usePanelRead } from "@/kernel/panel";
import { Frame, Head } from "@/views/Frame";
import { AgentIcon } from "@/lib/agentIcon";
import { BASE, getJson, postAction, postIntent, postObjectAction } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { chatSurface } from "@/lib/mainAgent";
import { openConversation } from "@/lib/turnStream";
import type { ActionView, Agent, Member } from "@/lib/types";
import { CONNECT_INSTALLS, Connect, ConnectSurfaces } from "@/views/Surfaces";

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
/** The step the welcome leads to. It is the first the run can stand, and a deploy that cannot act
 *  on the answer lands the address on the first step it does stand instead. */
const WEBSITE_STEP = "website";
const POSITION_STEP = "position";
const TOOLS_STEP = "tools";
const GOALS_STEP = "goals";
const SLACK_STEP = "slack";
const SURFACES_STEP = "surfaces";

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

/** What the member wrote about their business, as the sentence the chat opens on: the agent hears
 *  who it works for before its first turn rather than asking. The business is the whole of it, so
 *  the thread is founded the moment that box is answered and the turn runs while the member walks
 *  the rest of the run. */
function opening(business: string): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  return "I just set up this workspace. My business: " + said(business) + ". " + FIRST_TASK;
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

/** The connections this member already holds, as the pool projects them. The tools step reads it
 *  while it waits: an account is granted on the provider's own pages, so the grant landing here is
 *  the only account of it this screen gets. */
type PoolPayload = { connections: { provider: string }[] };

const POOL_READ = "/connections";

/** The tools a role suggests, in the order the step offers them: what somebody in that seat works
 *  in every day, named by the catalog's own provider names. The step draws only the ones this
 *  deploy's catalog carries, so a suggestion this deploy cannot grant is never offered.
 *
 *  Slack is on none of these lists. It installs for the whole workspace rather than for one member,
 *  and the run gives it a step of its own — offering it twice would ask one member to install it
 *  twice. */
const TOOLS_BY_ROLE: Record<Role, string[]> = {
  Founder: ["gmail", "googlecalendar", "notion", "stripe", "hubspot"],
  Designer: ["figma", "notion", "googledrive", "linear"],
  Marketing: ["google_search_console", "hubspot", "notion", "googledrive"],
  Operations: ["googlecalendar", "gmail", "notion", "quickbooks"],
  Engineer: ["github", "linear", "jira", "notion"],
  Growth: ["google_search_console", "hubspot", "stripe", "googlesheets"],
  "Human Resources": ["googlecalendar", "gmail", "asana", "notion"],
  Copywriting: ["googledrive", "notion", "figma", "gmail"],
  Researcher: ["googledrive", "googlesheets", "notion", "gmail"],
  "Executive Assistant": ["googlecalendar", "gmail", "googledrive", "zoom"],
  Sales: ["hubspot", "salesforce", "attio", "gmail"],
  Other: ["gmail", "googlecalendar", "googledrive", "notion"],
};

/** What the tools step offers this member: the role's own tools, drawn from the catalog so each
 *  one carries the label and the sentence the connectors screen gives it. A deploy whose catalog
 *  carries none of them offers nothing, and the step stands down. */
function suggested(role: Role, providers: ProviderTile[]): ProviderTile[] {
  return TOOLS_BY_ROLE[role]
    .map((name) => providers.find((tile) => tile.name === name))
    .filter((tile): tile is ProviderTile => tile !== undefined);
}

/** What is top of mind for a member as they start, picked from a grid rather than typed: each pick
 *  is written to memory and opens a thread of its own, so the run hands back one conversation per
 *  pick beside the first task. A member whose concern is not on the grid names it under `Other`.
 *
 *  `said` is the pick in the member's own words, which is what memory records. `asks` is the rest
 *  of the sentence that thread opens on — the first step to take, and the trap to avoid taking it
 *  into. It is stated here rather than held in a skill because the run already knows which pick
 *  was made: routing a description the wizard could name outright buys a round trip and a class
 *  of failure, and buys nothing else. Each one is written as the member's own ask, because the
 *  member is the speaker on that thread and reads it as theirs. */
type Goal = { label: string; said: string; asks: string };

const GOALS: Goal[] = [
  {
    label: "Growing revenue",
    said: "growing revenue",
    asks:
      "Start by working out my funnel as it stands from whatever CRM, billing, analytics or " +
      "spreadsheet access I have granted: volume at each stage, conversion between them, and " +
      "average deal size. Name the single stage losing the most, and one experiment on it with " +
      "a metric. Do not guess a number I have not given you — say what you could not read. Ask " +
      "me at most one thing.",
  },
  {
    label: "Shipping product",
    said: "shipping product",
    asks:
      "Start by measuring how long a change takes from opened to shipped, from whatever " +
      "repository and issue tracker I have granted, and name the stage that holds it longest. " +
      "Use what I already have rather than proposing a replacement for it. Do not invent a " +
      "cycle time you could not read — say what you could not read. Ask me at most one thing.",
  },
  {
    label: "Understanding competitors",
    said: "understanding competitors",
    asks:
      "Start by naming the competitors my business implies, from my website and whatever CRM, " +
      "support or analytics access I have granted: what each one sells, to whom, and at what " +
      "price. Say where we win and where we lose, in one line each. Do not invent a competitor " +
      "or a price you could not read — say what you could not read. Ask me at most one thing.",
  },
  {
    label: "Finding customers",
    said: "finding customers",
    asks:
      "Start by describing the customers my business already has, from whatever CRM, billing or " +
      "support access I have granted: who they are, how they found us, and what they bought. " +
      "Name the one segment worth more of and where the next ones like them are reached. Do not " +
      "invent a customer you could not read — say what you could not read. Ask me at most one " +
      "thing.",
  },
  {
    label: "Hiring",
    said: "hiring",
    asks:
      "Start by listing the roles my business is hiring for, from whatever applicant tracking, " +
      "calendar or email access I have granted, and where each one stands: open, interviewing, " +
      "or offered. Name the one role holding the rest back. Contact no candidate and send " +
      "nothing. Ask me at most one thing.",
  },
  {
    label: "Improving operations",
    said: "improving operations",
    asks:
      "Start by naming the recurring workflow that costs us the most time and mapping it as it " +
      "runs today: trigger, frequency, inputs, decisions, outputs, and owner. Split it into the " +
      "steps an agent can complete and the steps a person must approve, and keep the approval " +
      "wherever money, contracts, access or an outside commitment changes. Ask me at most one " +
      "thing.",
  },
  {
    label: "Fundraising",
    said: "fundraising",
    asks:
      "Start by working out my runway from whatever banking, billing or spreadsheet access I " +
      "have granted: cash, monthly burn, and months left. List the investors already in my CRM " +
      "or inbox and where each conversation stands. Do not guess a number I have not given you " +
      "— say what you could not read. Send nothing. Ask me at most one thing.",
  },
  {
    label: "User acquisition",
    said: "user acquisition",
    asks:
      "Start by working out where my users come from, from whatever analytics, advertising or " +
      "CRM access I have granted: volume, cost and conversion by channel. Name the one channel " +
      "worth more spend and the one worth stopping, each with a metric. Do not guess a number I " +
      "have not given you — say what you could not read. Ask me at most one thing.",
  },
];

const OTHER_GOAL = "Other";

/** The rest of the sentence an `Other` thread opens on: the run knows only the member's words for
 *  it, so the first step is to read what is already in reach and say what is not. */
const OTHER_ASKS =
  "Start by saying what you can already read about it from whatever access I have granted, and " +
  "the first step you would take. Do not guess what you could not read — say so. Ask me at most " +
  "one thing.";

/** The sentence one picked goal's thread opens on: who the member is, the goal in their words, and
 *  what taking the first step on it means. Nobody is reading this thread yet — the member is on the
 *  first task — so it asks for work done rather than for a plan. */
function goalOpening(business: string, role: string, goal: Goal): string {
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

/** What the member has answered so far, held in this tab across a reload so the step the address
 *  names is drawn with its content intact. One record under one key per workspace, written as it
 *  changes and dropped when the run ends or is closed. */
type Answers = {
  business: string;
  website: string;
  profile: Profile | null;
  role: Role;
  rolePicked: boolean;
  otherRole: string;
  goals: string[];
  otherGoal: string;
  /* The tools the member picked off their role's suggestions, by provider name. */
  tools: string[];
  declined: boolean;
};

const ANSWERS_PREFIX = "ufo.first-run.";

function answersKey(member: Member): string {
  return ANSWERS_PREFIX + (member.workspace_id ?? member.email);
}

function freshAnswers(member: Member): Answers {
  return {
    business: "",
    website: signupWebsite(member.email),
    profile: null,
    role: DEFAULT_ROLE,
    rolePicked: false,
    otherRole: "",
    goals: [],
    otherGoal: "",
    tools: [],
    declined: false,
  };
}

/** The answers this tab holds for the member, or a fresh record where it holds none or the browser
 *  hands back no store. */
function readAnswers(member: Member): Answers {
  const fresh = freshAnswers(member);
  try {
    const held = globalThis.sessionStorage?.getItem(answersKey(member));
    return held ? { ...fresh, ...(JSON.parse(held) as Partial<Answers>) } : fresh;
  } catch {
    return fresh;
  }
}

/** Hold the answers, or drop them where `answers` is null. A browser that refuses the write costs
 *  the member their answers on a reload, never the run. */
function holdAnswers(member: Member, answers: Answers | null): void {
  try {
    const held = globalThis.sessionStorage;
    if (answers === null) held?.removeItem(answersKey(member));
    else held?.setItem(answersKey(member), JSON.stringify(answers));
  } catch {
    return;
  }
}

/** The steps the run stands, in order: the website it reads the business from wherever the deploy
 *  can act on the answer, the questions that read comes back to answer, the tools the role
 *  suggests, the Slack install wherever this deploy offers one, and the other surfaces once Slack
 *  is declined. */
function revealedSteps(payload: FirstRunPayload, declined: boolean, tools: boolean): string[] {
  const slack = payload.connectors.some((row) => row.name === SLACK_STEP);
  const confirm = payload.actions.enrichment_profile.some(
    (view) => view.name === CONFIRM_WEBSITE_ACTION,
  );
  return [
    ...(confirm ? [WEBSITE_STEP] : []),
    BUSINESS_STEP,
    POSITION_STEP,
    ...(tools ? [TOOLS_STEP] : []),
    GOALS_STEP,
    ...(slack ? [SLACK_STEP] : []),
    ...(slack && declined ? [SURFACES_STEP] : []),
  ];
}

/** An address naming a step the run does not stand — a bad link, the Slack step on a deploy without
 *  one — is written over with the step the screen draws in its place. */
function Land({ step, onStep }: { step: string; onStep: (step: string) => void }) {
  useEffect(() => onStep(step), [step, onStep]);
  return null;
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

/** The column a question and its answer stand in, read down from a left edge. */
const STEP_COLUMN = "flex w-(--container-answer) max-w-full flex-col items-start gap-6xl";

/** A question and its answer, read down one column, with the way back and the way on beneath. The
 *  keys say Next too: Cmd+Enter or Ctrl+Enter anywhere on the screen moves on, so a member typing
 *  an answer never reaches for the pointer, and a Next that is disabled or busy holds against the
 *  keys the way it holds against a press. */
function Step({
  children,
  onBack,
  onNext,
  nextDisabled,
  busy,
}: {
  children: ReactNode;
  onBack: () => void;
  onNext: () => void;
  nextDisabled: boolean;
  busy?: boolean;
}) {
  useEffect(() => {
    const submit = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
      if (nextDisabled || busy) return;
      event.preventDefault();
      onNext();
    };
    document.addEventListener("keydown", submit);
    return () => document.removeEventListener("keydown", submit);
  }, [onNext, nextDisabled, busy]);
  return (
    <div className={STEP_COLUMN}>
      {children}
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

/** The grid the picks stand on: as many across as the column holds at one pick's width, so the
 *  labels never wrap and a narrow window drops a column instead. */
const CHOICES = "grid w-full grid-cols-[repeat(auto-fit,minmax(var(--container-choice),1fr))] gap-2xs";

/** One option on a grid of picks: filled until it is on, then outlined in the accent with its
 *  check drawn. The role step and the goals step share it, so a pick reads the same on both. */
const CHOICE = cn(
  "group flex h-10 items-center justify-between gap-sm rounded-(--radius-answer)",
  "border border-transparent bg-fill px-2xl text-start text-label whitespace-nowrap text-ink",
  "hover:bg-fill-strong",
  "data-[state=on]:border-link data-[state=on]:bg-transparent data-[state=on]:text-link",
  "data-[state=on]:hover:bg-transparent",
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink",
);

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

/** The run, drawn the same on both shells. What a shell owns is where its chat lives, so the one
 *  act that touches a conversation is the shell's: `onDone` carries the member to the thread the
 *  first task runs on, named by the conversation the run founded. Every other act here — the
 *  enrichment read, the memory write, the thread per goal, the build screen — is the same wherever
 *  the run is drawn, so it lives here once.
 *
 *  The step the run is on is the address's: `step` names it and `onStep` moves it, so a reload
 *  lands where the member was. No step is the welcome; the build screen follows the finish and is
 *  named by no address. */
export function FirstRun({
  agent,
  agents,
  member,
  step: asked,
  onStep,
  onClose,
  onDone,
}: {
  agent: Agent;
  agents: Agent[];
  member: Member;
  step: string | undefined;
  onStep: (step: string | undefined) => void;
  onClose: () => void;
  onDone: (conversationId: string | null) => void;
}) {
  const state = usePanelRead<FirstRunPayload>(FIRST_RUN_READ, 0);
  const [answers, setAnswers] = useState<Answers>(() => readAnswers(member));
  /* Whether the tools step is standing its picks to connect rather than offering them. */
  const [connecting, setConnecting] = useState(false);
  /* The accounts this member holds, read only while the step stands the picks to connect: a grant
     lands on the provider's pages, so the pool is the one place this screen learns of it. */
  const pool = usePanelRead<PoolPayload>(connecting ? POOL_READ : null, 0, WATCH_MS);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [building, setBuilding] = useState(false);
  /* The goals whose threads the run actually founded, which is what the build screen reports: a
     thread the POST never opened is not named as opened. */
  const [threads, setThreads] = useState<string[]>([]);
  /* The first task's own thread, founded as soon as the business is known and running behind every
     step after it. The promise is held rather than the id, so the end of the run waits on the
     founding it started rather than founding a second one. It is founded once: a member walking
     back to edit the business is editing a box whose thread is already answering. */
  const founding = useRef<Promise<string | null> | null>(null);
  const [thread, setThread] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(SILENT);
  /* Held across renders, because the failed panel raises its sentence from an effect keyed on this
     function: a new one every render would raise the sentence again on every render it caused. */
  const refuse = useCallback((title: string) => setToast({ title }), []);
  useEffect(() => holdAnswers(member, answers), [member, answers]);
  const answer = (patch: Partial<Answers>) => setAnswers((held) => ({ ...held, ...patch }));
  const close = () => {
    holdAnswers(member, null);
    onClose();
  };
  const { business, website, profile, role, otherRole, goals, otherGoal, tools } = answers;
  const declined = asked === SURFACES_STEP || answers.declined;

  if (asked === undefined) {
    return <Welcome onStart={() => onStep(WEBSITE_STEP)} onClose={close} />;
  }
  if (building) {
    return (
      <Building
        company={profile?.status === "matched" ? profile.company_name : null}
        apps={agents.filter((row) => row.app && !row.main)}
        assistant={agent}
        threads={threads}
        onClose={() => onDone(thread)}
        onDone={() => onDone(thread)}
      />
    );
  }

  return (
    <>
      <Panel
        state={state}
        loading={() => (
          <Frame actions={<Close onClick={close} />}>
            <PanelSkeleton shape="form" />
          </Frame>
        )}
        failed={(message) => (
          <Frame actions={<Close onClick={close} />}>
            <Failed message={message} onToast={refuse} />
          </Frame>
        )}
      >
        {(payload) => {
          const slack = payload.connectors.find((row) => row.name === SLACK_STEP);
          const confirm = payload.actions.enrichment_profile.find(
            (view) => view.name === CONFIRM_WEBSITE_ACTION,
          );
          /** What this role works in, which is what the tools step suggests. The step stands only
           *  where the catalog carries at least one of them, so a deploy with no connectors offers
           *  no empty grid. */
          const suggests = suggested(role, payload.providers);
          const revealed = revealedSteps(payload, declined, suggests.length > 0);
          const at = Math.max(0, revealed.indexOf(asked));
          const step = revealed[at];
          const held = slack ? slack.installed : false;
          const installed = step === SLACK_STEP && (held || connected);
          const said = role === OTHER_ROLE ? otherRole.trim() : role;
          const write = payload.actions.memory.find(
            (view) => view.name === RECORD_FIRST_RUN_ACTION,
          );
          const speaks = chatSurface(agents) ?? agent;
          /** The first task, said as soon as the business box is answered rather than at the end of
           *  the run: the brief is the slowest thing the workspace does, and every step after this
           *  one is time it can spend working instead of waiting. Nothing here is read — the member
           *  is still on the run — and the thread is handed to them when they land. */
          const kickOff = () => {
            founding.current ??= openConversation(speaks.id, opening(business)).then((founded) => {
              setThread(founded);
              return founded;
            });
          };
          /** What the run leaves behind, in the order the rest depends on: the memory every thread
           *  recalls, then a thread per picked goal, then the first task's own thread, founded back
           *  on the business step and waited on here for the lane the member lands in. The memory is
           *  written first because a goal thread that starts before it would ask what the business
           *  does. A refused write holds the step — the run has nothing to hand over without it, and
           *  the member reads why. */
          const finish = async () => {
            if (busy) return;
            if (!write) {
              refuse("This deploy runs without memory.");
              return;
            }
            setBusy(true);
            const budget =
              write.input_schema.properties?.body?.maxLength ?? Number.POSITIVE_INFINITY;
            const wrote = otherGoal.trim();
            const picked: Goal[] = [
              ...GOALS.filter((goal) => goals.includes(goal.label)),
              ...(goals.includes(OTHER_GOAL)
                ? [{ label: wrote, said: wrote.replace(/[.!?]+$/, ""), asks: OTHER_ASKS }]
                : []),
            ];
            const outcome = await postAction(agent.id, write.call, {
              body: firstRunRecorded(business, said, picked.map((goal) => goal.said), budget),
            });
            if (!outcome.applied) {
              setBusy(false);
              refuse(outcome.message);
              return;
            }
            const opened: string[] = [];
            for (const goal of picked) {
              const founded = await openConversation(speaks.id, goalOpening(business, said, goal));
              if (founded) opened.push(goal.label);
            }
            kickOff();
            await founding.current;
            setThreads(opened);
            setBusy(false);
            setBuilding(true);
            holdAnswers(member, null);
          };
          const advance = () => {
            if (step === BUSINESS_STEP) kickOff();
            /* The tools step answers twice: the picks, then connecting them. A step that picked
               nothing has nothing to connect and moves straight on. */
            if (step === TOOLS_STEP && tools.length && !connecting) return setConnecting(true);
            return at + 1 < revealed.length ? onStep(revealed[at + 1]) : void finish();
          };
          const back = () => {
            if (step === TOOLS_STEP && connecting) return setConnecting(false);
            return onStep(at ? revealed[at - 1] : undefined);
          };
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
            const learned = own ? describes(own) : "";
            setAnswers((held) => ({
              ...held,
              profile: own,
              role: own && !held.rolePicked ? suggestedRole(own) : held.role,
              business: held.business.trim() || !learned ? held.business : learned,
            }));
            advance();
          };
          const company = profile?.status === "matched" ? profile.company_name : null;
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
              actions={<Close onClick={close} />}
            >
              {revealed.includes(asked) ? null : <Land step={step} onStep={onStep} />}
              {step === BUSINESS_STEP ? (
                <Step onBack={back} onNext={advance} nextDisabled={!business.trim()}>
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
                      onChange={(event) => answer({ business: event.target.value })}
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
                </Step>
              ) : null}
              {step === WEBSITE_STEP ? (
                <Step onBack={back} onNext={confirmWebsite} nextDisabled={false} busy={busy}>
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
                      onChange={(event) => answer({ website: event.target.value })}
                    />
                    {website ? (
                      <Button
                        variant="mark"
                        size="glyph"
                        aria-label="Clear"
                        className="absolute top-1/2 right-lg -translate-y-1/2"
                        onClick={() => answer({ website: "" })}
                      >
                        <IconX stroke={1.5} aria-hidden />
                      </Button>
                    ) : null}
                  </div>
                </Step>
              ) : null}
              {step === POSITION_STEP ? (
                <Step onBack={back} onNext={advance} nextDisabled={!said}>
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
                    className={CHOICES}
                    value={role}
                    onValueChange={(value) => {
                      if (!value) return;
                      answer({ role: value as Role, rolePicked: true });
                    }}
                  >
                    {ROLES.map((option) => (
                      <ToggleGroupItem
                        key={option}
                        value={option}
                        autoFocus={option === role}
                        className={cn(CHOICE, option === OTHER_ROLE && "text-ink-quiet")}
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
                      onChange={(event) => answer({ otherRole: event.target.value })}
                    />
                  ) : null}
                </Step>
              ) : null}
              {step === TOOLS_STEP && !connecting ? (
                <Step onBack={back} onNext={advance} nextDisabled={false}>
                  <div className="flex flex-col gap-sm">
                    {said ? (
                      <p className="m-0 text-subtitle font-medium text-ink-quiet">{said}</p>
                    ) : null}
                    <h1 className="m-0 text-subtitle font-medium text-ink">
                      Which tools do you work in?
                    </h1>
                    <p className="m-0 text-label text-ink-soft">
                      These are the ones your role usually needs. Pick what UFO should work in, or
                      skip and connect them later.
                    </p>
                  </div>
                  <ToggleGroup
                    aria-label="Tools"
                    className={CHOICES}
                    value={tools}
                    onValueChange={(picked) => answer({ tools: picked })}
                  >
                    {suggests.map((tile) => (
                      <ToggleGroupItem key={tile.name} value={tile.name} className={CHOICE}>
                        <span className="flex min-w-0 items-center gap-sm">
                          <BrandMark provider={tile.name} className="size-(--size-glyph) shrink-0" />
                          {tile.label}
                        </span>
                        <IconCheck
                          className="size-(--size-glyph) shrink-0 opacity-0 group-data-[state=on]:opacity-100"
                          stroke={1.5}
                          aria-hidden
                        />
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </Step>
              ) : null}
              {step === TOOLS_STEP && connecting ? (
                <Step onBack={back} onNext={advance} nextDisabled={false}>
                  <div className="flex flex-col gap-sm">
                    <h1 className="m-0 text-subtitle font-medium text-ink">
                      Connect the tools you picked
                    </h1>
                    <p className="m-0 text-label text-ink-soft">
                      Each one opens its own consent page. Next carries on with whatever is left.
                    </p>
                  </div>
                  <ul className="m-0 flex w-full list-none flex-col gap-2xs p-0">
                    {suggests
                      .filter((tile) => tools.includes(tile.name))
                      .map((tile) => (
                        <ConnectTool
                          key={tile.name}
                          agent={agent}
                          tile={tile}
                          connected={
                            pool.phase === "ready" &&
                            pool.payload.connections.some((entry) => entry.provider === tile.name)
                          }
                          onRefused={refuse}
                        />
                      ))}
                  </ul>
                </Step>
              ) : null}
              {step === GOALS_STEP ? (
                <Step
                  onBack={back}
                  onNext={advance}
                  nextDisabled={goals.includes(OTHER_GOAL) && !otherGoal.trim()}
                >
                  <div className="flex flex-col gap-sm">
                    {company ? (
                      <p className="m-0 text-subtitle font-medium text-ink-quiet">{company}</p>
                    ) : null}
                    <h1 className="m-0 text-subtitle font-medium text-ink">
                      What is top of mind right now?
                    </h1>
                  </div>
                  <ToggleGroup
                    aria-label="Top of mind"
                    className={CHOICES}
                    value={goals}
                    onValueChange={(picked) => answer({ goals: picked })}
                  >
                    {[...GOALS.map((goal) => goal.label), OTHER_GOAL].map((option) => (
                      <ToggleGroupItem
                        key={option}
                        value={option}
                        className={cn(CHOICE, option === OTHER_GOAL && "text-ink-quiet")}
                      >
                        {option}
                        <IconCheck
                          className="size-(--size-glyph) shrink-0 opacity-0 group-data-[state=on]:opacity-100"
                          stroke={1.5}
                          aria-hidden
                        />
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                  {goals.includes(OTHER_GOAL) ? (
                    <Input
                      surface="answer"
                      aria-label="What is top of mind"
                      autoFocus
                      placeholder="What is top of mind"
                      value={otherGoal}
                      onChange={(event) => answer({ otherGoal: event.target.value })}
                    />
                  ) : null}
                </Step>
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
                        onClick={() => {
                          answer({ declined: true });
                          onStep(SURFACES_STEP);
                        }}
                      >
                        I don't use Slack
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
              {step === SURFACES_STEP ? (
                <Step onBack={back} onNext={finish} nextDisabled={false} busy={busy}>
                  <div className="flex flex-col gap-sm">
                    {company ? (
                      <p className="m-0 text-body leading-(--leading-chrome) font-medium text-ink-quiet">{company}</p>
                    ) : null}
                    <h1 className="m-0 text-body leading-(--leading-chrome) font-medium text-ink">
                      Get UFO everywhere you work
                    </h1>
                  </div>
                  <ConnectSurfaces
                    agent={agent}
                    member={member}
                    hidden={[SLACK_STEP]}
                    onRefused={refuse}
                  />
                </Step>
              ) : null}
            </Frame>
          );
        }}
      </Panel>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

/** The link a connect request mints. The broker verb ends its turn on the handoff rather than in
 *  its answer, so the link is the turn's own `connect` frame and this waits for it; a turn that
 *  ends without one granted nothing, and the row says so. */
function mintedLink(turnId: string): Promise<string | null> {
  return new Promise((resolve) => {
    const stream = new EventSource(BASE + "/turns/" + turnId + "/stream");
    const settle = (url: string | null) => {
      stream.close();
      resolve(url);
    };
    stream.addEventListener("connect", () => settle(BASE + "/turns/" + turnId + "/connect"));
    stream.addEventListener("terminal", () => settle(null));
    stream.onerror = () => settle(null);
  });
}

const CONNECT_REFUSED = "No connection request was opened. Ask in chat to connect the account.";

/** One picked tool, and the press that connects it. The act is the connectors screen's own: a
 *  workspace install where the provider takes one, and the broker's connect verb for every account
 *  that is the member's. The consent window opens on the press, before the round trip that mints
 *  the link, because a window opened after it has lost the gesture the browser opens one for.
 *
 *  An account already in the pool is drawn connected and offers no press: the run asks for what is
 *  missing and never for what the member already granted. */
function ConnectTool({
  agent,
  tile,
  connected,
  onRefused,
}: {
  agent: Agent;
  tile: ProviderTile;
  connected: boolean;
  onRefused: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);

  async function connect() {
    if (busy) return;
    setBusy(true);
    setLink(null);
    const consent = openConsentWindow();
    const install = CONNECT_INSTALLS[tile.name];
    const outcome = install
      ? await postObjectAction(agent.id, install, {})
      : await postIntent(agent.id, {
          verb: "connect",
          kind: "connection",
          name: tile.name,
          spec: { shared: false },
        });
    if (!outcome.applied) {
      setBusy(false);
      consent?.close();
      onRefused(outcome.message);
      return;
    }
    const url = install ? (outcome.url ?? null) : await mintedLink(outcome.turn_id ?? "");
    setBusy(false);
    if (!url) {
      consent?.close();
      onRefused(CONNECT_REFUSED);
      return;
    }
    if (consent) consent.location.href = url;
    // The row keeps the link only for a member whose browser refused the window, so pressing once
    // is the whole act for everybody else.
    setLink(consent ? null : url);
  }

  return (
    <li className="flex w-full flex-col gap-sm">
      <div className="flex h-10 w-full items-center gap-sm rounded-(--radius-answer) bg-fill px-2xl text-label text-ink">
        <BrandMark provider={tile.name} className="size-(--size-glyph) shrink-0" />
        <span className="min-w-0 flex-1 truncate">{tile.label}</span>
        {connected ? (
          <Badge tone="affirm" className="gap-2xs">
            <IconCheck className="size-(--size-glyph)" stroke={1.5} aria-hidden />
            Connected
          </Badge>
        ) : (
          <Button
            variant="send"
            size="bar"
            className="h-8"
            busy={busy}
            aria-label={"Connect " + tile.label}
            onClick={connect}
          >
            Connect
          </Button>
        )}
      </div>
      {link ? (
        <Notice>
          <ConsentLink url={link}>{"Open the " + tile.label + " consent page"}</ConsentLink>
        </Notice>
      ) : null}
    </li>
  );
}
